"""Jev (TypeSafe System One, ``jev-1.14.0``) as a SECOND OPINION on faces in a take (owner ask, 4 Nov 2026).

**Model.** ``jev-1.14.0``, pinned: the newest release (14 Oct 2026, docs.typesafe.ai/models). ``jev-latest`` and
``jev-preview`` both point to it today (no preview build), and Box questions need Jev 1.14+. Pinned, not the alias, because
the YES / NO thresholds below were set against it (the docs' advice for tuned thresholds). HQ_JEV_MODEL may name an alias
or another ``jev-1.14``+ version; anything older falls back to the pin.

**Where it runs (production).** TYPESAFE_API_KEY lives on the HuhQueue server (Render), not on the video workers. A
worker without the key picks the keyframes, re-encodes them (JPEG, no metadata) and returns them with the analysis
(``analysis["jev_frames"]``); the server asks Jev for those frames (``studio_opinion``, studio/takes.py ``_studio_jev``),
merges the boxes into the face track and drops the frames before anything is saved. The key never leaves the server and
is never logged. A worker that does have the key (a repo secret on GitHub Actions) asks Jev itself, as below.

**Free, capped.** At most ``cap`` keyframes per take (HQ_JEV_FACE_CAP, default 12) and HQ_JEV_DAILY_CAP keyframes per
account per day on the server (default 60); past either, the take uses the local faces only.

Local MediaPipe / OpenCV stays the primary detector on every sampled frame (studio/take.py face_track). Jev only looks
at a few keyframes, at most ``cap`` per take (HQ_JEV_FACE_CAP, default 12):

* the first frame after each scene cut that the local detector didn't cover confidently (``scene_start``), and
* stretches where the local detector found no face (``no_local_face``) or only a low-confidence one (``low_confidence``),
  spread evenly over the take.

Each keyframe is one request with two questions over the same image (speculative fan-out, docs.typesafe.ai):

* Noul ``face_visible`` "Is a person's face visible in `frame`?"
* Box ``speaker_face`` "the main speaker's face in `frame`" (a Box always returns a box, so it only counts when the Noul
  is a clear yes).

The model judges, the code decides (the truth table, all in ``decide``):

    face_visible >= YES (0.7) and a sane box    -> a face sample at that time (and held for HOLD_S where local found
                                                 nothing), merged into the reframe track; crop_plan smooths it as usual
    face_visible <= NO (0.3)                    -> recorded as "no face"; nothing added
    in between, or a box that isn't sane        -> recorded as unsettled; nothing added
    any error / timeout / bad answer            -> recorded with the error; nothing added (local + centred crop), logged

It never fails the job, never runs without TYPESAFE_API_KEY on the worker, and only runs when the job spec says so
(``spec.jev``: a person started the take in the app, or an MCP app's request was approved through studio/consent.py).
Images: JPEG, 280-1568 px per side, re-encoded from raw pixels (no EXIF, upright), with the
``TypeSafe-Danger-Zone-Images: true`` header, sent as multipart. The key is never printed or stored.
"""
from __future__ import annotations

import io
import base64
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

API_URL = "https://api.typesafe.ai/v1/systemone"
PIN = "jev-1.14.0"            # Box needs Jev 1.14+; the thresholds were tuned on it


def _model() -> str:
    m = os.environ.get("HQ_JEV_MODEL", "").strip()
    if m in ("jev-latest", "jev-preview"):
        return m
    v = re.fullmatch(r"jev-(\d+)\.(\d+)(?:\.\d+)?", m)
    return m if v and (int(v[1]), int(v[2])) >= (1, 14) else PIN


MODEL = _model()
FRAME_MAX_BYTES = 400_000    # one keyframe a worker hands to the server (a 768 px JPEG is ~60 KB)
WHYS = ("scene_start", "no_local_face", "low_confidence")
DEFAULT_CAP = 12
YES, NO = 0.7, 0.3          # Noul thresholds (docs: 0.7 / 0.3 starting points)
HOLD_S = 1.0                # a Jev face also stands for local-empty samples this close to its keyframe
TIMEOUT_S = 20.0
CONCURRENCY = 4
MIN_SIDE, MAX_SIDE = 280, 1568
SCENE_OFFSET = 0.4          # look just after the cut, not on the blended transition frame
MIN_GAP = 2.0               # keyframes closer than this are the same moment
LOW_CONF = 0.7              # crop_plan ignores local samples under this

QUESTIONS = {
    "face_visible": {"type": "noul", "instructions": "Is a person's face visible in `frame`?",
                     "criteria": {"true": "A human face (front or side view) can be seen in the frame, even if small.",
                                  "false": "The frame shows no human face: slides, a screen recording, an empty room, "
                                           "or a person seen only from behind."}},
    "speaker_face": {"type": "box", "instructions": "the main speaker's face in `frame`"},
}


def cap_from_env() -> int:
    try:
        return max(0, min(40, int(os.environ.get("HQ_JEV_FACE_CAP", str(DEFAULT_CAP)))))
    except ValueError:
        return DEFAULT_CAP


def daily_cap() -> int:
    """Keyframes per account per day on the server's key (HQ_JEV_DAILY_CAP, default 60; 0 turns server-side Jev off)."""
    try:
        return max(0, min(1000, int(os.environ.get("HQ_JEV_DAILY_CAP", "60"))))
    except ValueError:
        return 60


def enabled(spec_flag) -> tuple[bool, str]:
    """(on, why). Off unless the job asked for it AND the worker has a key."""
    if spec_flag is not True:
        return False, "off for this take (not started by a person in the app, or not approved)"
    if not os.environ.get("TYPESAFE_API_KEY"):
        return False, "off: TYPESAFE_API_KEY isn't set on this worker"
    return True, "on"


# ---------------------------------------------------------------------- which frames
def pick_keyframes(faces: list[dict], scenes: list[float], duration: float, fps: float = 2.0, cap: int = DEFAULT_CAP) -> list[dict]:
    """[{t, why}] sorted by t, at most ``cap``. Scene starts first, then frames where the local detector found nothing
    or only a weak face, spread evenly over the take."""
    if cap <= 0 or duration <= 0:
        return []
    by_t = {round(f["t"] * fps): f for f in faces or []}
    n = int(duration * fps)
    weak, empty = [], []
    for k in range(n + 1):
        f = by_t.get(k)
        if f is None:
            empty.append(k / fps)
        elif f.get("score", 1) < LOW_CONF:
            weak.append(k / fps)
    picked: list[dict] = []

    def add(t: float, why: str) -> bool:
        t = round(min(max(0.0, t), max(0.0, duration - 0.05)), 2)
        if any(abs(p["t"] - t) < MIN_GAP for p in picked):
            return False
        picked.append({"t": t, "why": why})
        return True
    for s in [0.0] + sorted(x for x in scenes or [] if 0 < x < duration):
        if len(picked) >= cap:
            break
        k0 = round((s + SCENE_OFFSET) * fps)  # a scene the local detector already covers confidently needs no second opinion
        f = by_t.get(k0) or by_t.get(k0 + 1)
        if f is not None and f.get("score", 1) >= LOW_CONF:
            continue
        add(s + SCENE_OFFSET, "scene_start")
    for pool, why in ((empty, "no_local_face"), (weak, "low_confidence")):
        if len(picked) >= cap or not pool:
            continue
        step = max(1, len(pool) // (cap - len(picked)))
        for t in pool[::step]:
            if len(picked) >= cap:
                break
            add(t, why)
    return sorted(picked, key=lambda p: p["t"])[:cap]


# ---------------------------------------------------------------------- images
def prepare(img) -> tuple[bytes, tuple[int, int]]:
    """A PIL image -> baseline JPEG bytes within 280-1568 px per side, rebuilt from raw pixels (no EXIF/ICC/comments)."""
    from PIL import Image, ImageOps
    im = ImageOps.exif_transpose(img).convert("RGB")
    w, h = im.size
    s = min(1.0, MAX_SIDE / max(w, h))
    if min(w, h) * s < MIN_SIDE:
        s = MIN_SIDE / min(w, h)
    if s != 1.0:
        im = im.resize((max(MIN_SIDE, min(MAX_SIDE, round(w * s))), max(MIN_SIDE, min(MAX_SIDE, round(h * s)))), Image.LANCZOS)
    clean = Image.frombytes("RGB", im.size, im.tobytes())  # pixels only: nothing of the source's metadata survives
    buf = io.BytesIO()
    clean.save(buf, "JPEG", quality=88, optimize=False, progressive=False)
    return buf.getvalue(), clean.size


def frame_image(path: str, t: float, width: int = 768):
    """One frame of the video at t seconds, as a PIL image (FFmpeg, through mediasafe)."""
    from PIL import Image

    from .. import mediasafe
    p = mediasafe.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", path, "-frames:v", "1",
                       "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True, timeout=60)
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(f"no frame at {t:.2f}s")
    return Image.open(io.BytesIO(p.stdout))


# ---------------------------------------------------------------------- one call
def ask(jpeg: bytes, key: str, http=None, timeout: float = TIMEOUT_S) -> dict:
    """One System One request (multipart). Returns the parsed JSON; raises on any HTTP or shape problem."""
    import httpx
    ref = f"frame-{uuid.uuid4()}"
    body = {"model": MODEL, "state": {"frame": ref}, "assets": {ref: {"type": "image", "part": "frame"}}, "questions": QUESTIONS}
    files = {"request": (None, json.dumps(body), "application/json"), "frame": ("frame.jpg", jpeg, "image/jpeg")}
    headers = {"Authorization": f"Bearer {key}", "TypeSafe-Danger-Zone-Images": "true"}
    own = http is None
    c = http or httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False)
    try:
        r = c.post(API_URL, headers=headers, files=files, timeout=timeout)
    finally:
        if own:
            c.close()
    if r.status_code != 200:
        detail = ""
        try:
            detail = str((r.json() or {}).get("detail"))[:160]
        except ValueError:
            pass
        raise RuntimeError(f"HTTP {r.status_code} {detail}".strip())
    data = r.json()
    ans = (data or {}).get("answers") or {}
    nv, bx = ans.get("face_visible") or {}, ans.get("speaker_face") or {}
    if not isinstance(nv.get("noul"), (int, float)) or not isinstance(bx.get("box"), dict):
        raise RuntimeError("answer without face_visible / speaker_face")
    return data


def decide(data: dict) -> dict:
    """The truth table (see the module doc). Returns {verdict, face_visible, box?, confidence?}."""
    ans = data["answers"]
    p = float(ans["face_visible"]["noul"])
    box = ans["speaker_face"]["box"]
    conf = ans["speaker_face"].get("confidence")
    out = {"face_visible": round(p, 3)}
    try:
        l, r, t, b = (float(box[k]) for k in ("left", "right", "top", "bottom"))
    except (KeyError, TypeError, ValueError):
        return {**out, "verdict": "unsettled", "note": "box missing an edge"}
    ok_box = 0 <= l < r <= 1 and 0 <= t < b <= 1 and 0.01 <= (r - l) <= 0.9 and 0.01 <= (b - t) <= 0.95
    if p >= YES and ok_box:
        return {**out, "verdict": "face", "box": {"left": round(l, 4), "right": round(r, 4), "top": round(t, 4), "bottom": round(b, 4)},
                "confidence": conf}
    if p <= NO:
        return {**out, "verdict": "no_face"}
    return {**out, "verdict": "unsettled", **({"note": "box not usable"} if p >= YES else {})}


# ---------------------------------------------------------------------- the take
def second_opinion(path: str, faces: list[dict], scenes: list[float], duration: float, spec_flag=None, cap: int | None = None,
                   fps: float = 2.0, http=None, grab=None, log=print, timeout: float = TIMEOUT_S) -> tuple[list[dict], dict]:
    """(faces merged with Jev's boxes, record for the take result). Never raises."""
    t0 = time.time()
    cap = cap_from_env() if cap is None else max(0, int(cap))
    on, why = enabled(spec_flag)
    rec: dict = {"enabled": on, "why": why, "model": MODEL, "cap": cap, "frames": [], "calls": 0, "errors": 0,
                 "used": 0, "input_tokens": 0, "fallback": None}
    if not on or cap == 0:
        return faces, rec
    try:
        keys = pick_keyframes(faces, scenes, duration, fps, cap)
    except Exception as e:  # noqa: BLE001 - never fail the take because of Jev
        rec["fallback"] = f"keyframes: {e.__class__.__name__}"
        log(f"jev: {rec['fallback']}; local faces + centred crop")
        return faces, rec
    if not keys:
        rec["why"] = "on: the local detector was confident on every keyframe"
        return faces, rec
    key = os.environ.get("TYPESAFE_API_KEY", "")
    grab = grab or frame_image

    def one(k: dict) -> dict:
        t1 = time.perf_counter()
        out = {"t": k["t"], "why": k["why"]}
        try:
            jpeg, _ = prepare(grab(path, k["t"]))
            data = ask(jpeg, key, http=http, timeout=timeout)
            out.update(decide(data))
            out["input_tokens"] = int(((data.get("usage") or {}).get("input_tokens")) or 0)
        except Exception as e:  # noqa: BLE001 - timeout, HTTP error, bad answer, no frame: local + centred crop
            msg = str(e).replace(key, "***") if key else str(e)
            out.update({"verdict": "error", "error": f"{e.__class__.__name__}: {msg[:120]}"})
        out["ms"] = round((time.perf_counter() - t1) * 1000)
        return out
    try:
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(keys))) as ex:
            results = list(ex.map(one, keys))
    except Exception as e:  # noqa: BLE001
        rec["fallback"] = f"{e.__class__.__name__}"
        log(f"jev: failed ({rec['fallback']}); local faces + centred crop")
        return faces, rec
    rec["calls"] = len(results)
    rec["errors"] = sum(r["verdict"] == "error" for r in results)
    rec["input_tokens"] = sum(r.get("input_tokens", 0) for r in results)
    merged = merge(faces, results, fps)
    rec["used"] = sum(r.get("used", False) for r in results)
    rec["frames"] = results
    if rec["errors"]:
        rec["fallback"] = f"{rec['errors']} of {len(results)} Jev calls failed: local faces + centred crop there"
        log(f"jev: {rec['fallback']} (first: {next(r['error'] for r in results if r['verdict'] == 'error')})")
    rec["secs"] = round(time.time() - t0, 2)
    log(f"jev: {len(results)} keyframes, {rec['used']} faces added, {rec['errors']} errors, {rec['input_tokens']} input tokens, {rec['secs']} s")
    return merged, rec


def merge(faces: list[dict], results: list[dict], fps: float = 2.0) -> list[dict]:
    """Jev's faces into the local samples: a Jev face replaces a missing or weak local sample at its keyframe and holds for
    HOLD_S over local-empty samples (never over a confident local one). Marks each result ``used``."""
    by_k = {round(f["t"] * fps): dict(f) for f in faces or []}
    for r in results:
        r["used"] = False
        if r.get("verdict") != "face":
            continue
        b = r["box"]
        s = {"cx": round((b["left"] + b["right"]) / 2, 4), "cy": round((b["top"] + b["bottom"]) / 2, 4),
             "w": round(b["right"] - b["left"], 4),
             "h": round(b["bottom"] - b["top"], 4), "score": round(max(LOW_CONF, min(0.95, r["face_visible"])), 2), "src": "jev"}
        k0 = round(r["t"] * fps)
        span = int(HOLD_S * fps)
        for k in range(k0 - span, k0 + span + 1):
            if k < 0:
                continue
            cur = by_k.get(k)
            if cur is None or (cur.get("score", 1) < LOW_CONF and cur.get("src") != "jev"):
                by_k[k] = {"t": round(k / fps, 2), **s}
                r["used"] = True
    return [by_k[k] for k in sorted(by_k)]


# ---------------------------------------------------------------------- worker without the key -> the server asks
def keyframe_jpegs(path: str, faces: list[dict], scenes: list[float], duration: float, cap: int | None = None, fps: float = 2.0,
                   grab=None, log=print) -> list[dict]:
    """On a worker WITHOUT the key: the keyframes Jev should see, as [{t, why, jpeg (base64)}], at most ``cap``."""
    cap = cap_from_env() if cap is None else max(0, int(cap))
    grab, out = grab or frame_image, []
    for k in pick_keyframes(faces, scenes, duration, fps, cap):
        try:
            jpeg, _ = prepare(grab(path, k["t"]))
        except Exception as e:  # noqa: BLE001 - one missing frame is one fewer keyframe
            log(f"jev: no keyframe at {k['t']}s ({e.__class__.__name__})")
            continue
        if len(jpeg) <= FRAME_MAX_BYTES:
            out.append({"t": k["t"], "why": k["why"], "jpeg": base64.b64encode(jpeg).decode()})
    return out


def clean_frames(frames, cap: int) -> list[dict]:
    """The server's check of what a worker sent: a list of at most ``cap`` {t, why, jpeg}; every image decoded and
    re-encoded here (pixels only, 280-1568 px), anything odd dropped. Returns [{t, why, jpeg (bytes)}]."""
    from PIL import Image
    out: list[dict] = []
    if not isinstance(frames, list):
        return out
    for f in frames[:max(0, cap)]:
        try:
            t, why, b = f["t"], f["why"], f["jpeg"]
            if isinstance(t, bool) or not isinstance(t, (int, float)) or why not in WHYS or not isinstance(b, str):
                continue
            if len(b) > FRAME_MAX_BYTES * 4 // 3 + 4:
                continue
            raw = base64.b64decode(b, validate=True)
            if not raw.startswith(b"\xff\xd8"):
                continue
            im = Image.open(io.BytesIO(raw))
            if im.format != "JPEG" or max(im.size) > MAX_SIDE or min(im.size) < 16:
                continue
            jpeg, _ = prepare(im)
            out.append({"t": round(float(t), 2), "why": why, "jpeg": jpeg})
        except Exception:  # noqa: BLE001 - a bad frame is skipped, never an error on the take
            continue
    return out


def studio_opinion(faces: list[dict], frames: list[dict], key: str, cap: int, fps: float = 2.0, http=None, log=print,
                   timeout: float = TIMEOUT_S) -> tuple[list[dict], dict]:
    """On the SERVER: Jev on the worker's keyframes (already ``clean_frames``). Same record as second_opinion. Never raises."""
    t0 = time.time()
    rec: dict = {"enabled": True, "why": "on (asked from the server; the key stays there)", "model": MODEL, "cap": cap,
                 "frames": [], "calls": 0, "errors": 0, "used": 0, "input_tokens": 0, "fallback": None, "where": "server"}
    frames = frames[:max(0, cap)]
    if not frames:
        rec["why"] = "on: no keyframes needed a second opinion"
        return faces, rec

    def one(k: dict) -> dict:
        t1 = time.perf_counter()
        out = {"t": k["t"], "why": k["why"]}
        try:
            data = ask(k["jpeg"], key, http=http, timeout=timeout)
            out.update(decide(data))
            out["input_tokens"] = int(((data.get("usage") or {}).get("input_tokens")) or 0)
        except Exception as e:  # noqa: BLE001 - timeout, HTTP error, bad answer: local + centred crop there
            msg = str(e).replace(key, "***") if key else str(e)
            out.update({"verdict": "error", "error": f"{e.__class__.__name__}: {msg[:120]}"})
        out["ms"] = round((time.perf_counter() - t1) * 1000)
        return out
    try:
        with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(frames))) as ex:
            results = list(ex.map(one, frames))
    except Exception as e:  # noqa: BLE001
        rec["fallback"] = e.__class__.__name__
        return faces, rec
    rec["calls"] = len(results)
    rec["errors"] = sum(r["verdict"] == "error" for r in results)
    rec["input_tokens"] = sum(r.get("input_tokens", 0) for r in results)
    merged = merge(faces, results, fps)
    rec["used"] = sum(r.get("used", False) for r in results)
    rec["frames"] = results
    if rec["errors"]:
        rec["fallback"] = f"{rec['errors']} of {len(results)} Jev calls failed: local faces + centred crop there"
    rec["secs"] = round(time.time() - t0, 2)
    log(f"jev (server): {len(results)} keyframes, {rec['used']} faces added, {rec['errors']} errors, {rec['input_tokens']} input tokens")
    return merged, rec
