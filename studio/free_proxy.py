"""The app makes the video worker's free-tier calls with ITS keys, so no provider key ever reaches a worker (5 Nov 2026).

Production runs jobs on an InstaBox box (studio/instabox_worker.py) whose environment carries only the job's id, the
app URL and the one-job token, and the repo has no Actions secrets. So the worker (free_quota.WorkerProxy) sends:

  * one audio chunk (16 kHz mono FLAC, <= 25 MB, <= ~10 min) to ``POST /api/worker/jobs/{id}/asr``: the app checks the
    job transcribes, reserves the chunk's audio-seconds on the shared ledger (free_quota.Ledger) and asks Groq
    ``whisper-large-v3-turbo`` (free plan) once. A 429 pauses Groq for every job on this app until its retry-after.
  * a clip's keyframes (<= 12 JPEGs, each <= 400 KB and <= 1024 px) to ``POST /api/worker/jobs/{id}/vision``: the app
    re-checks that the take's owner turned on Smart layout (beta), decodes and re-encodes every frame (no metadata
    survives), then runs the vision picker (NVIDIA NIM free catalog, then OpenRouter ``:free`` only) reserving each
    request on the ledger.

Both answer {"ok": false, "why": ...} for a refusal (limit near, opt-in off, no key on the app, provider error); the
worker then transcribes locally / keeps the rules. Nothing is retried, nothing reaches a paid tier, keys and frames are
never logged. The same pattern as #108's Jev second opinion (the server asks, with its own key).

No FastAPI here (the routes live in studio/app.py).
"""
from __future__ import annotations

import base64
import binascii
import io
import os
import threading
import time

from . import free_quota

VISION_BODY_MAX = 7 * 1024 * 1024        # 12 frames x 400 KB as base64 (~6.4 MB) plus JSON
HTTP = None                              # tests plug a fake client in; None: a fresh httpx client per call
_block = {"groq": 0.0}
_block_lock = threading.Lock()


def flac_seconds(data: bytes) -> float | None:
    """Duration from the FLAC STREAMINFO block (total samples / sample rate), or None when the header doesn't say."""
    if len(data) < 42 or data[:4] != b"fLaC" or (data[4] & 0x7F) != 0:
        return None
    bits = int.from_bytes(data[18:26], "big")
    rate = bits >> 44
    total = bits & ((1 << 36) - 1)
    if not rate or not total:
        return None
    return total / rate


def clean_audio(data: bytes, claimed) -> float:
    """The audio-seconds to reserve for one chunk: the FLAC header's duration when it has one (a worker can't
    under-report), else the x-audio-seconds header. ValueError (400) on anything that isn't one sane FLAC chunk."""
    if not data or data[:4] != b"fLaC":
        raise ValueError("Send one FLAC audio chunk.")
    try:
        c = float(claimed)
    except (TypeError, ValueError):
        raise ValueError("x-audio-seconds must be a number.")
    if not 0 < c <= free_quota.ASR_MAX_SECONDS:
        raise ValueError(f"x-audio-seconds must be above 0 and at most {int(free_quota.ASR_MAX_SECONDS)}.")
    real = flac_seconds(data)
    if real is not None and real > free_quota.ASR_MAX_SECONDS:
        raise ValueError(f"One chunk is at most {int(free_quota.ASR_MAX_SECONDS)} seconds of audio.")
    return max(c, real or 0.0)


def clean_frames(frames) -> list[bytes]:
    """Validated keyframes, each decoded and re-encoded as a plain JPEG (no EXIF/GPS or other metadata survives).
    ValueError (400) on a bad type, too many frames, an oversized frame, or anything that isn't a small JPEG."""
    if not isinstance(frames, list) or not frames:
        raise ValueError("frames must be a non-empty list of base64 JPEGs.")
    if len(frames) > free_quota.VISION_MAX_FRAMES:
        raise ValueError(f"At most {free_quota.VISION_MAX_FRAMES} frames.")
    from PIL import Image
    out = []
    for f in frames:
        if not isinstance(f, str) or len(f) > free_quota.VISION_FRAME_MAX_BYTES * 4 // 3 + 8:
            raise ValueError("Each frame is a base64 JPEG of at most 400 KB.")
        try:
            raw = base64.b64decode(f, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("A frame isn't valid base64.")
        if len(raw) > free_quota.VISION_FRAME_MAX_BYTES or raw[:3] != b"\xff\xd8\xff":
            raise ValueError("Each frame is a JPEG of at most 400 KB.")
        try:
            with Image.open(io.BytesIO(raw)) as im:
                if im.format != "JPEG" or max(im.size) > free_quota.VISION_MAX_PX or min(im.size) < 1:
                    raise ValueError("Each frame is a JPEG of at most 1024 px.")
                rgb = im.convert("RGB")
        except ValueError:
            raise
        except Exception:  # noqa: BLE001 - truncated or hostile image data
            raise ValueError("A frame couldn't be read as a JPEG.")
        buf = io.BytesIO()
        rgb.save(buf, "JPEG", quality=72)  # fresh pixels only: no metadata leaves the app
        out.append(buf.getvalue())
    return out


def _blocked(provider: str) -> bool:
    with _block_lock:
        return _block.get(provider, 0.0) > time.time()


def _pause(provider: str, seconds: float) -> None:
    with _block_lock:
        _block[provider] = time.time() + min(3600.0, max(60.0, float(seconds)))


def asr(house, jid: int, data: bytes, secs: float) -> dict:
    """One chunk through Groq with the app's key. Never raises."""
    from .media import asr as asr_mod
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return {"ok": False, "why": "groq: no key on the app"}
    if _blocked("groq"):
        return {"ok": False, "why": "groq: Groq asked us to wait (429)"}
    try:
        r = house.free_quota_reserve(jid, "groq", secs)
        if not r.get("ok"):
            return {"ok": False, "why": str(r.get("why") or "groq: refused")}
        words, lang = asr_mod.groq_post(data, "audio.flac", key, HTTP)
    except asr_mod.GroqRateLimited as e:
        _pause("groq", e.wait)
        return {"ok": False, "why": str(e)}
    except asr_mod.GroqUnavailable as e:
        return {"ok": False, "why": str(e)}
    except Exception as e:  # noqa: BLE001 - a ledger/database error: the worker transcribes locally
        return {"ok": False, "why": f"groq: the app couldn't ask Groq ({e.__class__.__name__})"}
    return {"ok": True, "words": words, "language": lang, "model": asr_mod.GROQ_MODEL}


def vision(house, jid: int, frames: list[bytes]) -> dict:
    """The vision picker with the app's keys; every request reserved on the ledger (opt-in re-checked there). Never
    raises."""
    from .media.reframe import vision as vis

    def reserve(name: str) -> tuple[bool, str]:
        r = house.free_quota_reserve(jid, name, 1)
        return bool(r.get("ok")), str(r.get("why") or "")
    try:
        got = vis.pick(frames, http=HTTP, env=os.environ, reserve=reserve)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "why": f"vision: {e.__class__.__name__}"}
    if not got:
        return {"ok": False, "why": "vision: no free provider answered (or none is set up on the app)"}
    return {"ok": True, "category": got["category"], "provider": got["provider"], "model": got["model"]}
