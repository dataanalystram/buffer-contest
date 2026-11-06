"""The final look at a rendered One take clip (server side): is any text covering the speaker's face?

The worker's render QA (studio/media/facesafe.qa_measure) measures the overlap with YuNet and a pixel diff. It sends the
two frames where a face was closest to our text (540 px JPEGs, rebuilt from raw pixels) and the server, which holds the
keys, asks a model to look at them:

* Jev (TypeSafe System One, ``jev-latest``; HQ_FACE_QA_MODEL may pin a ``jev-1.14``+ version) first, one Noul question:
  "Does any text, caption or graphic box cover part of a person's face (eyes, nose or mouth) in `frame`?"
* SubQ (``subq-snap-1.1``) only where Jev is unsettled (between NO and YES) or errored: the tie-break judgment.
* The code decides: a clear yes (>= YES) from either -> the clip needs review; a clear no -> fine; otherwise the
  worker's measurement stands. When no model can answer (no key, allowance used up, every call failed) the
  deterministic fallback is the worker's YuNet measurement, and the record says so (``fallback``) - never silent.

Free, capped: at most MAX_FRAMES frames a clip, counted against the account's daily Jev allowance (HQ_JEV_DAILY_CAP,
the same counter as the keyframe second opinion), and answers are cached by the frame's sha256 so a re-check of the
same frame costs nothing. The frames are never stored; the keys never leave the server and are never logged.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import re
import threading
import uuid

JEV = ("jev", "https://api.typesafe.ai/v1/systemone", "TYPESAFE_API_KEY")
SUBQ = ("subq", "https://api.subq.ai/v1/systemone", "SUBQ_API_KEY")
SUBQ_MODEL = "subq-snap-1.1"
YES, NO = 0.7, 0.3
MAX_FRAMES = 2
FRAME_MAX_B64 = 400_000 * 4 // 3 + 4
TIMEOUT_S = 20.0
QUESTION = {"covered": {"type": "noul",
                        "instructions": "Does any text, caption or graphic box cover part of a person's face (eyes, nose "
                                        "or mouth) in `frame`?",
                        "criteria": {"true": "Overlaid text, a caption or a box sits on top of a face: the eyes, nose or "
                                             "mouth are partly hidden by it.",
                                     "false": "All overlaid text and boxes are clear of every face (they may sit on the "
                                              "hair, neck, chest, clothes or background), or there is no face."}}}
_CACHE: dict[str, dict] = {}
_CACHE_MAX = 256
_LOCK = threading.Lock()


def jev_model() -> str:
    m = os.environ.get("HQ_FACE_QA_MODEL", "").strip()
    v = re.fullmatch(r"jev-(\d+)\.(\d+)(?:\.\d+)?", m)
    return m if (m in ("jev-latest", "jev-preview") or (v and (int(v[1]), int(v[2])) >= (1, 14))) else "jev-latest"


def clean(frames, cap: int = MAX_FRAMES) -> list[dict]:
    """What the worker sent, checked: [{t, jpeg (base64)}] -> [{t, jpeg (bytes)}], each image decoded and re-encoded
    here (pixels only), anything odd dropped. Never raises."""
    from PIL import Image

    from .media.jev_faces import prepare
    out: list[dict] = []
    if not isinstance(frames, list):
        return out
    for f in frames[:max(0, min(cap, MAX_FRAMES))]:
        try:
            if not isinstance(f, dict):
                continue
            t, b = f.get("t"), f.get("jpeg")
            if isinstance(t, bool) or not isinstance(t, (int, float)) or not isinstance(b, str) or len(b) > FRAME_MAX_B64:
                continue
            if not (0.0 <= float(t) <= 3600.0):
                continue
            raw = base64.b64decode(b, validate=True)
            if not raw.startswith(b"\xff\xd8"):
                continue
            im = Image.open(io.BytesIO(raw))
            if im.format != "JPEG" or max(im.size) > 1568 or min(im.size) < 16:
                continue
            jpeg, _ = prepare(im)
            out.append({"t": round(float(t), 2), "jpeg": jpeg})
        except Exception:  # noqa: BLE001 - a bad frame is skipped
            continue
    return out


def _ask(provider: tuple, model: str, jpeg: bytes, http=None) -> float:
    """One System One request; the Noul probability that text covers a face. Raises RuntimeError on any problem (the
    message never carries the key or the body)."""
    from . import netguard
    _name, url, env = provider
    key = os.environ.get(env, "")
    if not key:
        raise RuntimeError(f"{env} isn't set")
    ref = f"frame-{uuid.uuid4()}"
    body = {"model": model, "state": {"frame": ref},
            "assets": {ref: {"type": "image", "format": "jpg", "data_base64": base64.b64encode(jpeg).decode()}},
            "questions": QUESTION}
    try:
        r = netguard.safe_post(url, http=http, json=body, timeout=TIMEOUT_S, limit=200_000,
                               headers={"Authorization": f"Bearer {key}", "TypeSafe-Danger-Zone-Images": "true"})
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        p = float(((r.json() or {}).get("answers") or {})["covered"]["noul"])
    except RuntimeError as e:
        if str(e).startswith("HTTP "):
            raise
        raise RuntimeError(e.__class__.__name__) from None
    except netguard.FetchError:  # a ValueError: said as what it is, never as a bad answer (the message stays out)
        raise RuntimeError("FetchError") from None
    except (KeyError, TypeError, ValueError, AttributeError):
        raise RuntimeError("answer without covered") from None
    except Exception as e:  # noqa: BLE001 - FetchError, httpx errors
        raise RuntimeError(e.__class__.__name__) from None
    if not 0.0 <= p <= 1.0:
        raise RuntimeError("noul out of range")
    return p


def fallback(worker: dict | None) -> dict:
    """The deterministic answer when no model could look: the worker's YuNet + pixel-diff measurement."""
    from .media.facesafe import QA_MAX
    w = worker if isinstance(worker, dict) else {}
    mc = w.get("max_cover")
    if isinstance(mc, bool) or not isinstance(mc, (int, float)):
        return {"by": "none", "verdict": "unsettled", "max_cover": None}
    return {"by": "YuNet (worker)", "verdict": "covered" if float(mc) > QA_MAX else "clear", "max_cover": round(float(mc), 4)}


def judge(frames: list[dict], allowance: int, http=None, worker: dict | None = None, log=print, off: str = "") -> dict:
    """{ran, why, by, verdict: covered|clear|unsettled, frames: [{t, jev, subq, verdict, cached}], calls, fallback}.
    frames: clean()'s output. allowance: model calls this account may still make today (the caller reserves them and
    refunds ``allowance - calls``). worker: the worker's QA record ({max_cover}), the deterministic fallback. Never
    raises; the fallback is logged. off: why the models don't run for this clip (e.g. no consent), said in ``why``."""
    rec: dict = {"ran": False, "why": "", "by": [], "verdict": "unsettled", "frames": [], "calls": 0, "model": jev_model(),
                 "fallback": None}
    try:
        if off:
            rec["why"] = str(off)[:200]
        else:
            _judge(frames, allowance, http, rec)
    except Exception as e:  # noqa: BLE001 - the final look never breaks the clip landing
        rec.update(ran=False, why=f"error ({e.__class__.__name__})")
    if not rec["ran"] or rec["verdict"] == "unsettled":
        fb = fallback(worker)
        rec["fallback"] = fb
        if not rec["ran"]:
            rec["verdict"] = fb["verdict"]
        log(f"face QA: models {'unsettled' if rec['ran'] else 'did not run'} ({rec['why']}); "
            f"fallback {fb['by']}: {fb['verdict']}")
    return rec


def _judge(frames, allowance, http, rec) -> None:
    if not isinstance(frames, list) or not frames:
        rec["why"] = "no frames with a face to check"
        return
    have_jev, have_subq = bool(os.environ.get(JEV[2])), bool(os.environ.get(SUBQ[2]))
    if not (have_jev or have_subq):
        rec["why"] = "off: neither TYPESAFE_API_KEY nor SUBQ_API_KEY is set on the server"
        return
    if isinstance(allowance, bool) or not isinstance(allowance, int) or allowance <= 0:
        rec["why"] = "off: today's free model allowance for this account is used up"
        return
    budget = [allowance]

    def spend() -> bool:
        with _LOCK:
            if budget[0] <= 0:
                return False
            budget[0] -= 1
            rec["calls"] += 1
            return True

    def one(f: dict) -> dict:
        h = hashlib.sha256(f["jpeg"]).hexdigest()
        with _LOCK:
            hit = _CACHE.get(h)
        if hit is not None:
            return {**hit, "t": f["t"], "cached": True}
        out: dict = {"t": f["t"], "cached": False}
        p = None
        if have_jev and spend():
            try:
                p = _ask(JEV, rec["model"], f["jpeg"], http)
                out["jev"] = round(p, 3)
            except Exception as e:  # noqa: BLE001
                out["jev_error"] = str(e)[:80]
        if (p is None or NO < p < YES) and have_subq and spend():
            try:
                q = _ask(SUBQ, SUBQ_MODEL, f["jpeg"], http)
                out["subq"] = round(q, 3)
                p = q if p is None or q >= YES or q <= NO else p
            except Exception as e:  # noqa: BLE001
                out["subq_error"] = str(e)[:80]
        out["verdict"] = "covered" if p is not None and p >= YES else ("clear" if p is not None and p <= NO else "unsettled")
        if out["verdict"] != "unsettled":
            with _LOCK:
                if len(_CACHE) >= _CACHE_MAX:
                    _CACHE.clear()
                _CACHE[h] = {k: v for k, v in out.items() if k not in ("t", "cached")}
        return out
    rec["frames"] = [one(f) for f in frames[:MAX_FRAMES]]
    vs = [f["verdict"] for f in rec["frames"]]
    rec["by"] = sorted({k for f in rec["frames"] for k in ("jev", "subq") if k in f})
    rec["ran"] = bool(rec["by"]) or any(f.get("cached") for f in rec["frames"])
    rec["verdict"] = "covered" if "covered" in vs else ("clear" if vs and all(v == "clear" for v in vs) else "unsettled")
    rec["why"] = "on" if rec["ran"] else "every model call failed"


def needs_review(fs: dict | None, verdict: dict | None) -> str | None:
    """The reason a landed clip must wait for a person, or None: the worker's own status first, then the final look."""
    fs = fs if isinstance(fs, dict) else {}
    if fs.get("status") == "needs_review":
        return str(fs.get("reason") or "the overlays may cover the speaker's face")[:200]
    v = verdict if isinstance(verdict, dict) else {}
    if v.get("verdict") == "covered":
        who = "Jev/SubQ" if v.get("ran") else "the render QA"
        return f"{who} saw text covering the speaker's face"
    return None
