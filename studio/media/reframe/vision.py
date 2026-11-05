"""Optional free vision layout picker: one categorical question per clip, from a few keyframes.

    HQ_LAYOUT_VISION = off (default) | shadow | on

* off     nothing is sent anywhere.
* shadow  the question is asked and the answer logged next to what the rules decided; the rules still decide.
* on      the answer picks the layout of every shot it can (a shot without the faces a layout needs keeps the rules'
          choice). A pinned layout (the person's or their agent's explicit choice) always wins.

Frames are sent ONLY when the take's owner turned on "Smart layout (beta)" (studio/take_layout.py). At most 12
keyframes, at most 1024 px, re-encoded as plain JPEG (no EXIF or other metadata), no audio.

Free providers, in order, then the rules alone:
  1. NVIDIA NIM (``NVIDIA_API_KEY``), nvidia/nemotron-3.5-super-vl-120b-a12b. NVIDIA's free API access is for
     prototyping and may log prompts.
  2. OpenRouter (``OPENROUTER_API_KEY``), only models whose id ends in ``:free`` (anything else is refused before a
     request); free models may log prompts and are rate limited (about 50 requests a day on a free key).
Limits enforced here, before each request: a per-provider day counter and a 20-requests-a-minute guard, on this
machine and on the app's shared ledger (studio/free_quota.py: a worker run is a fresh machine). Every request
times out after 20 s; any error, refusal or unreadable answer falls back to the next provider and then to the rules,
logged at INFO, never raised.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
from pathlib import Path

log = logging.getLogger("huhqueue.layout_vision")
CATEGORIES = ("talk", "screencast", "two-speaker", "wide")
MAX_FRAMES, MAX_PX = 12, 1024
TIMEOUT_S = 20.0
RPM = 20
PROVIDERS = [
    {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
     "model": "nvidia/nemotron-3.5-super-vl-120b-a12b", "key_env": "NVIDIA_API_KEY", "rpd_env": "HQ_VISION_NVIDIA_RPD", "rpd": 200},
    {"name": "openrouter", "url": "https://openrouter.ai/api/v1/chat/completions",
     "model": "nvidia/nemotron-3.5-super-vl:free", "key_env": "OPENROUTER_API_KEY", "rpd_env": "HQ_VISION_OPENROUTER_RPD", "rpd": 45},
]
QUESTION = ("These are keyframes from one short video clip. Which ONE layout fits it best for a vertical 9:16 short?\n"
            "talk = one person on camera; screencast = a screen, slides or app recording (maybe with a small webcam); "
            "two-speaker = two people talking in the same shot; wide = a wide shot, a group, a stage or anything else.\n"
            'Answer with JSON only: {"layout": "talk|screencast|two-speaker|wide"}')
_lock = threading.Lock()


class PaidModelRefused(RuntimeError):
    pass


def mode() -> str:
    m = os.environ.get("HQ_LAYOUT_VISION", "off").strip().lower()
    return m if m in ("off", "shadow", "on") else "off"


def assert_free(provider: dict) -> None:
    """Never a paid model: on OpenRouter only ':free' ids; NVIDIA only through its free API catalog endpoint."""
    if provider["name"] == "openrouter" and not str(provider["model"]).endswith(":free"):
        raise PaidModelRefused(f"{provider['model']} isn't a :free OpenRouter model")
    if provider["name"] == "nvidia" and not provider["url"].startswith("https://integrate.api.nvidia.com/"):
        raise PaidModelRefused("NVIDIA calls go to the free API catalog endpoint only")


# ---------------------------------------------------------------------- counters
class Limits:
    """Per-provider requests: a rolling day count and a 20-a-minute guard (JSON file, shared by jobs on one machine)."""

    def __init__(self, path: str | None = None, now=time.time):
        self.path = Path(path or os.environ.get("HQ_VISION_USAGE_FILE") or (Path(tempfile.gettempdir()) / "hq_vision_usage.json"))
        self.now = now

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def stamps(self, name: str) -> list[float]:
        t = self.now()
        return [float(x) for x in self._load().get(name, []) if t - float(x) < 86400]

    def allow(self, provider: dict) -> tuple[bool, str]:
        st, t = self.stamps(provider["name"]), self.now()
        rpd = int(os.environ.get(provider["rpd_env"]) or provider["rpd"])
        if len(st) >= rpd:
            return False, f"{provider['name']}: day limit ({rpd}) reached"
        if sum(1 for x in st if t - x < 60) >= RPM:
            return False, f"{provider['name']}: {RPM} requests/minute guard"
        return True, ""

    def record(self, name: str) -> None:
        with _lock:
            d = self._load()
            d[name] = self.stamps(name) + [self.now()]
            try:
                tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
                tmp.write_text(json.dumps(d))
                os.replace(tmp, self.path)
            except OSError:
                pass


# ---------------------------------------------------------------------- frames
def keyframes(path: str, start: float, end: float, src_w: int, src_h: int, n: int = 8) -> list[bytes]:
    """Up to n (<= 12) JPEG keyframes, <= 1024 px on the long side, re-encoded from raw pixels (no metadata)."""
    import subprocess

    from PIL import Image

    from ... import mediasafe
    n = max(1, min(MAX_FRAMES, n))
    s = MAX_PX / max(src_w, src_h, 1)
    w, h = (max(2, int(src_w * s) // 2 * 2), max(2, int(src_h * s) // 2 * 2)) if s < 1 else (src_w // 2 * 2, src_h // 2 * 2)
    out = []
    for k in range(n):
        t = start + (end - start) * (k + 0.5) / n
        r = mediasafe.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-an", "-vf", f"scale={w}:{h}",
                           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], timeout=60)
        if r.returncode != 0 or len(r.stdout) < w * h * 3:
            continue
        im = Image.frombytes("RGB", (w, h), r.stdout[: w * h * 3])
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=72)  # a fresh JPEG from pixels: no EXIF, GPS or other metadata
        out.append(buf.getvalue())
    _ = subprocess  # (mediasafe wraps subprocess)
    return out


# ---------------------------------------------------------------------- the question
def parse(content: str) -> str | None:
    """The category from a model answer (reasoning models may think first: the LAST valid answer counts)."""
    if not isinstance(content, str):
        return None
    text = re.sub(r"<think>.*?</think>", " ", content, flags=re.S | re.I)
    hits = re.findall(r'"layout"\s*:\s*"([a-z\- ]+)"', text.lower()) or re.findall(r"\b(talk|screencast|two-speaker|wide)\b", text.lower())
    for h in reversed(hits):
        h = h.strip().replace(" ", "-")
        if h in CATEGORIES:
            return h
    return None


def ask(provider: dict, frames: list[bytes], key: str, http=None) -> str | None:
    assert_free(provider)
    import httpx
    content = [{"type": "text", "text": QUESTION}] + [
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(f).decode()}} for f in frames]
    body = {"model": provider["model"], "messages": [{"role": "user", "content": content}], "temperature": 0,
            "max_tokens": 4096, "stream": False}
    client = http or httpx.Client(timeout=TIMEOUT_S, trust_env=False, follow_redirects=False)
    try:
        r = client.post(provider["url"], headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                        json=body, timeout=TIMEOUT_S)
    finally:
        if http is None:
            client.close()
    if r.status_code != 200 or len(r.content) > 2_000_000:
        raise RuntimeError(f"{provider['name']} answered {r.status_code}")
    msg = ((r.json().get("choices") or [{}])[0].get("message") or {})
    return parse(msg.get("content") or "") or parse(msg.get("reasoning_content") or "")


def pick(frames: list[bytes], limits: Limits | None = None, http=None, env=None) -> dict | None:
    """{category, provider} from the first free provider that answers, or None (rules only)."""
    env = os.environ if env is None else env
    limits = limits or Limits()
    for prov in PROVIDERS:
        key = env.get(prov["key_env"], "")
        if not key:
            continue
        try:
            assert_free(prov)
        except PaidModelRefused as e:
            log.info("layout vision: %s", e)
            continue
        ok, why = limits.allow(prov)
        if ok:
            from ... import free_quota  # the app's shared day/minute ledger (each worker run is a fresh machine)
            ok, why = free_quota.remote_ok(prov["name"], 1)
        if not ok:
            log.info("layout vision: %s; next", why)
            continue
        limits.record(prov["name"])
        try:
            cat = ask(prov, frames, key, http)
        except Exception as e:  # noqa: BLE001 - any failure: the next provider, then the rules
            log.info("layout vision: %s failed (%s); next", prov["name"], e.__class__.__name__)
            continue
        if cat:
            return {"category": cat, "provider": prov["name"], "model": prov["model"]}
        log.info("layout vision: %s gave no usable answer; next", prov["name"])
    return None


def pick_for_clip(path: str, start: float, end: float, src_w: int, src_h: int) -> dict | None:
    """Ask about one clip (caller checked the mode and the owner's opt-in). Never raises."""
    try:
        frames = keyframes(path, start, end, src_w, src_h)
        if not frames:
            return None
        got = pick(frames)
        if got:
            got["mode"] = mode()
            log.info("layout vision (%s): %s says %s", got["mode"], got["provider"], got["category"])
        return got
    except Exception as e:  # noqa: BLE001
        log.info("layout vision: skipped (%s)", e.__class__.__name__)
        return None


def apply(shots: list[dict], sigs: list[dict], category: str) -> tuple[list[dict], int]:
    """Mode 'on': the clip's category picks each unpinned shot's layout where its faces allow it."""
    from . import split_enabled
    from .decide import FACE_MIN_H, is_webcam
    if category == "two-speaker" and not split_enabled():
        return shots, 0  # split stays opt-in (HQ_LAYOUT_SPLIT=1): the rules keep their choice
    want = {"talk": "track", "screencast": "screen", "two-speaker": "split", "wide": "fit"}.get(category)
    n, out = 0, []
    for s, sig in zip(shots, sigs):
        s = dict(s)
        faces = [t for t in sig["faces"] if t["presence"] >= 0.6]
        ok = (want == "fit" or (want == "track" and any(t["h"] >= FACE_MIN_H for t in faces))
              or (want == "split" and len(faces) == 2) or (want == "screen" and any(is_webcam(t) for t in faces)))
        if want and ok and not s.get("pinned") and s["layout"] != want:
            s.update(layout=want, rule=f"vision:{category}")
            if want == "track":
                s["faces"] = [max(faces, key=lambda t: t["h"])["id"]]
            if want == "fit":
                s.setdefault("bg", "blur")
            n += 1
        out.append(s)
    return out, n
