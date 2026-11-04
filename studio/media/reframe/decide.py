"""The layout of every shot of a clip, and the stability rules around it.

Rules, per shot, first match wins (a pinned layout from the person or their agent always wins first):

  R1  screen content + a webcam face in a corner   -> screen (PiP: screen card, face circle; stacked when the webcam
                                                      is >= 240 px tall)
  R2  screen content, no face                      -> fit (blurred or brand-colour background)
  R3  one face, height >= 10% of the frame         -> track (9:16 crop following the face)
  R4  one face, smaller (a wide stage)              -> fit
  R5  two faces that fit inside one 9:16 crop       -> track, centred on the pair
  R6  two faces apart, both talking >= 20%          -> split (only with HQ_LAYOUT_SPLIT=1; high motion threshold)
  R7  two faces apart, one talking >= 80%           -> track on the one talking
  R8  three or more faces, or low confidence        -> fit

Screen content means at least 2 of the 3 screen signals AND edge-dense content spanning at least half the frame
WIDTH, with no large face in the shot. "Talking" is mouth motion measured only inside the word timestamps.

Stability: a 1-second majority vote inside each shot (one bad sample never flips it), layouts change only at cuts,
every layout holds at least 2.0 s (shorter shots take a neighbour's layout), and a clip of 60 s or less changes layout
at most 3 times (more, and the whole clip takes its majority layout). Upscale guard: a tracked crop that would be
scaled up more than 2.5x renders as fit instead (720p is allowed when the face is >= 15% tall).
"""
from __future__ import annotations

import math
from collections import Counter

from . import LAYOUTS, split_enabled

OUT_W, OUT_H = 1080, 1920
MIN_HOLD = 2.0
MAX_CHANGES_PER_MIN = 3
PRESENT = 0.6          # a track counts in a shot when it's in >= 60% of the shot's samples
FACE_MIN_H = 0.10      # R3 / R4
WEBCAM_MAX_H = 0.18
BIG_FACE_H = 0.25      # a face this large means a person on camera, not a screen
CONTENT_MIN = 0.5      # screen content spans at least half the width
EDGE_SCREEN = 0.04     # and is text/UI dense (edge density outside faces)
UPSCALE_MAX = 2.5
TALK_MIN = 0.20        # R6
TALK_SOLO = 0.80       # R7
MOTION_MIN = 4.0       # mouth motion (mean grey-level change) that counts as talking; deliberately high
SPLIT_MIN_SHOT = 2.5


# ---------------------------------------------------------------------- per-shot signals (pure)
def summarize(tracks: list[dict], a: float, b: float, n_samples: int) -> list[dict]:
    """Tracks present in the shot [a, b] (clip s): median box, presence share, mouth motion over the shot."""
    from statistics import median
    out = []
    for tr in tracks:
        pts = [(t, f) for t, f in tr["points"] if a <= t < b]
        if not pts:
            continue
        cx = median(f["x"] + f["w"] / 2 for _, f in pts)
        cy = median(f["y"] + f["h"] / 2 for _, f in pts)
        out.append({"id": tr["id"], "cx": round(cx, 4), "cy": round(cy, 4), "w": round(median(f["w"] for _, f in pts), 4),
                    "h": round(median(f["h"] for _, f in pts), 4), "presence": round(len(pts) / max(1, n_samples), 3),
                    "score": round(median(f["score"] for _, f in pts), 3), "points": pts})
    return out


def is_webcam(t: dict) -> bool:
    return t["h"] < WEBCAM_MAX_H and (t["cx"] < 0.35 or t["cx"] > 0.65) and (t["cy"] < 0.4 or t["cy"] > 0.6)


def crop_scale(src_h: int, zoom: float = 1.0) -> float:
    """How much a full-height 9:16 crop is scaled up to 1920 tall."""
    return OUT_H / max(1.0, src_h / zoom)


def rule(sig: dict, src: tuple[int, int], prefs: dict | None = None) -> dict:
    """{layout, rule, ...options} for one shot (or one 1-s window) from its signals."""
    prefs = prefs or {}
    sw, sh = src
    faces = [t for t in sig["faces"] if t["presence"] >= PRESENT]
    bg = prefs.get("bg") if prefs.get("bg") in ("blur", "brand") else "blur"
    big_face = any(t["h"] >= BIG_FACE_H for t in faces)
    screen = (bool(sig.get("screen")) and float(sig.get("edge") or 0) >= EDGE_SCREEN
              and float(sig.get("content_width") or 0) >= CONTENT_MIN and not big_face)
    webcams = [t for t in faces if is_webcam(t)] if screen else []   # a corner face is a webcam only over a screen
    people = [t for t in faces if t not in webcams]
    if screen and webcams and not people:
        cam = max(webcams, key=lambda t: t["h"])
        tall_px = cam["h"] * sh * 1.6  # the webcam box is ~1.6x the face height
        return {"layout": "screen", "rule": "R1", "style": "stack" if tall_px >= 240 else "pip", "face": cam["id"]}
    if screen and not faces:
        return {"layout": "fit", "rule": "R2", "bg": bg}
    if sig.get("low_conf"):
        return {"layout": "fit", "rule": "R8", "bg": bg}
    if len(people) == 1:
        p = people[0]
        if p["h"] < FACE_MIN_H:
            return {"layout": "fit", "rule": "R4", "bg": bg}
        if crop_scale(sh) > UPSCALE_MAX and p["h"] < 0.15:
            return {"layout": "fit", "rule": "R3-upscale", "bg": bg}
        return {"layout": "track", "rule": "R3", "faces": [p["id"]]}
    if len(people) == 2:
        l, r = sorted(people, key=lambda t: t["cx"])
        crop_frac = (sh * 9 / 16) / sw
        spread = (r["cx"] + r["w"] / 2) - (l["cx"] - l["w"] / 2)
        if spread <= 0.9 * crop_frac:
            if crop_scale(sh) > UPSCALE_MAX and min(l["h"], r["h"]) < 0.15:
                return {"layout": "fit", "rule": "R5-upscale", "bg": bg}
            return {"layout": "track", "rule": "R5", "faces": [l["id"], r["id"]]}
        ml, mr = l.get("motion"), r.get("motion")
        if ml is not None and mr is not None and (ml + mr) > 0:
            share_l = ml / (ml + mr)
            both_talk = min(ml, mr) >= MOTION_MIN and min(share_l, 1 - share_l) >= TALK_MIN
            if both_talk and split_enabled() and sig.get("dur", 0) >= SPLIT_MIN_SHOT:
                return {"layout": "split", "rule": "R6", "faces": [l["id"], r["id"]]}
            solo = l if share_l >= TALK_SOLO else (r if share_l <= 1 - TALK_SOLO else None)
            if solo is not None and max(ml, mr) >= MOTION_MIN and solo["h"] >= FACE_MIN_H:
                return {"layout": "track", "rule": "R7", "faces": [solo["id"]]}
        return {"layout": "fit", "rule": "R8", "bg": bg}
    if not faces and not screen:
        return {"layout": "fit", "rule": "R8-noface", "bg": bg}
    return {"layout": "fit", "rule": "R8", "bg": bg}


def window_votes(sig: dict, src: tuple[int, int], prefs: dict | None, n_per_s: float) -> dict:
    """The shot's decision by a 1-second majority vote: each second is decided on the faces seen in it; the shot takes
    the layout most seconds chose (the whole-shot decision breaks ties and keeps its options)."""
    whole = rule(sig, src, prefs)
    a, b = sig["start"], sig["end"]
    secs = max(1, int(math.floor(b - a)))
    votes = []
    for k in range(secs):
        w0, w1 = a + k, min(b, a + k + 1)
        faces = []
        for t in sig["faces"]:
            pts = [p for p in t["points"] if w0 <= p[0] < w1]
            if pts:
                faces.append({**t, "presence": len(pts) / max(1.0, (w1 - w0) * n_per_s), "points": pts})
        votes.append(rule({**sig, "faces": faces}, src, prefs)["layout"])
    top = Counter(votes).most_common()
    if top and top[0][0] != whole["layout"] and (len(top) == 1 or top[0][1] > top[1][1]) and top[0][1] > len(votes) / 2:
        # most seconds disagree with the shot-level decision: re-decide with the majority's faces only
        maj = top[0][0]
        if maj == "fit":
            return {"layout": "fit", "rule": whole["rule"] + "+vote", "bg": (prefs or {}).get("bg") or "blur"}
        return {**whole, "rule": whole["rule"] + "+vote"} if whole["layout"] == maj else whole
    return whole


# ---------------------------------------------------------------------- stability (pure)
def stabilize(shots: list[dict], pins: dict[int, dict] | None = None) -> list[dict]:
    """Apply the hold, the change cap and the person's pins to [{start, end, layout, ...}] (clip seconds)."""
    pins = pins or {}
    out = [dict(s) for s in shots]
    if not out:
        return out
    # 1) minimum hold: a run of one layout shorter than MIN_HOLD takes the longer neighbouring run's layout
    for _ in range(len(out)):
        runs = _runs(out)
        short = [r for r in runs if r[2] - r[1] < MIN_HOLD and not any(i in pins for i in range(r[3], r[4] + 1))]
        if not short or len(runs) == 1:
            break
        _, a, b, i0, i1 = min(short, key=lambda r: r[2] - r[1])
        cand = [j for j in (i0 - 1, i1 + 1) if 0 <= j < len(out)]
        pick = out[max(cand, key=lambda j: (_run_len(out, j), -j))]  # the longer neighbouring run; the earlier on a tie
        for i in range(i0, i1 + 1):
            out[i] = {**_opts(pick), "start": out[i]["start"], "end": out[i]["end"], "rule": "hold"}
    # 2) change cap: at most 3 layout changes per started minute; beyond that, the clip's majority layout everywhere
    dur = out[-1]["end"] - out[0]["start"]
    cap = MAX_CHANGES_PER_MIN * max(1, math.ceil(dur / 60.0 - 1e-9))
    if len(_runs(out)) - 1 > cap:
        by = Counter()
        for s in out:
            by[s["layout"]] += s["end"] - s["start"]
        maj = by.most_common(1)[0][0]
        donor = max((s for s in out if s["layout"] == maj), key=lambda s: s["end"] - s["start"])
        out = [{**_opts(donor), "start": s["start"], "end": s["end"], "rule": "cap"} for s in out]
    # 3) pins win
    for i, pin in pins.items():
        if 0 <= i < len(out):
            out[i] = {**out[i], **pin, "rule": "pinned", "pinned": True}
    return out


def _opts(s: dict) -> dict:
    return {k: v for k, v in s.items() if k not in ("start", "end", "rule", "pinned", "signals")}


def _runs(seq: list[dict]) -> list[tuple[str, float, float, int, int]]:
    runs = []
    for i, s in enumerate(seq):
        if runs and runs[-1][0] == s["layout"]:
            lay, a, _, i0, _ = runs[-1]
            runs[-1] = (lay, a, s["end"], i0, i)
        else:
            runs.append((s["layout"], s["start"], s["end"], i, i))
    return runs


def _run_len(seq: list[dict], i: int) -> float:
    for lay, a, b, i0, i1 in _runs(seq):
        if i0 <= i <= i1:
            return b - a
    return 0.0


def sidecar(shots: list[dict]) -> list[dict]:
    """The layout sidecar: [{start, end, layout}] in clip seconds, consecutive shots of one layout merged."""
    out = []
    for lay, a, b, _, _ in _runs(shots):
        out.append({"start": round(a, 3), "end": round(b, 3), "layout": lay})
    return out


def clean_pin(v) -> dict | None:
    """A pinned layout from the API: 'track' | 'fit' | 'split' | 'screen' (or a dict with layout + bg / style)."""
    if isinstance(v, str):
        v = {"layout": v}
    if not isinstance(v, dict) or v.get("layout") not in LAYOUTS:
        return None
    pin = {"layout": v["layout"]}
    if v["layout"] == "fit" and v.get("bg") in ("blur", "brand"):
        pin["bg"] = v["bg"]
    if v["layout"] == "screen" and v.get("style") in ("pip", "stack"):
        pin["style"] = v["style"]
    return pin


# ---------------------------------------------------------------------- the whole clip
def decide(shot_sigs: list[dict], src: tuple[int, int], prefs: dict | None = None, pins: dict[int, dict] | None = None,
           n_per_s: float = 5.0) -> list[dict]:
    """[{start, end, layout, rule, ...}] for each shot, stabilised. shot_sigs: [{start, end, dur, faces, screen,
    content_width}] from ``signals_for``."""
    raw = []
    for s in shot_sigs:
        d = window_votes(s, src, prefs, n_per_s)
        raw.append({"start": s["start"], "end": s["end"], **d})
    return stabilize(raw, pins)


def signals_for(shots: list[tuple[float, float]], samples: list[dict], tracks: list[dict], screen_sig=None,
                speech: list[tuple[float, float]] | None = None, fps: float = 5.0) -> list[dict]:
    """Per-shot signals (clip seconds). screen_sig(a, b, faces) -> {screen, content_width}; None = no screen check."""
    from .faces import mouth_motion
    out = []
    for a, b in shots:
        n = sum(1 for s in samples if a <= s["t"] < b)
        faces = summarize(tracks, a, b, n)
        for t in faces:
            sp = [(max(a, s), min(b, e)) for s, e in speech or [] if e > a and s < b]
            t["motion"] = mouth_motion({"points": t["points"]}, sp if speech is not None else None)
        sig = {"start": round(a, 3), "end": round(b, 3), "dur": round(b - a, 3), "faces": faces, "screen": False, "content_width": 0.0,
               "low_conf": bool(faces) and min(t["score"] for t in faces) < 0.75 and len(faces) >= 2}
        if screen_sig is not None:
            boxes = [f for s in samples if a <= s["t"] < b for f in s["faces"]]
            sig.update(screen_sig(a, b, boxes))
        out.append(sig)
    return out


def public(shots: list[dict]) -> list[dict]:
    """The plan as the API shows it (no face points)."""
    keep = ("start", "end", "layout", "rule", "bg", "style", "pinned")
    return [{k: s[k] for k in keep if k in s} for s in shots]
