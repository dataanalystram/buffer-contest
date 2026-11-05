"""Punch-in: a gentle zoom in and back out on face-tracked shots (owner ask, 5 Nov 2026; the OpusClip-style "auto zoom").

What it does, all in ffmpeg (``zoompan``, no extra model, no paid service):

* Events: the start of every face-tracked shot (a scene start) and moments of speech emphasis: the first word after a
  pause of ``PAUSE`` s or more, or a word that is a number or ends in "!". Events closer than ``GAP`` s to the previous
  one are dropped, so the camera never pumps.
* Each event eases from 1.0 to at most ``ZMAX`` (1.12) over ``RAMP`` (0.4 s, cosine ease), holds, and eases back out
  over ``RAMP`` before the next event or the shot end. Nothing jumps: the zoom is continuous and starts at 1.0.
* Faces are never cropped out: the zoom is centred on the point the tracked crop keeps the face at (horizontal centre,
  ``path.FACE_Y`` down), and the peak is lowered until the face box, wherever the dead zone lets it drift, still fits
  inside the frame with a margin. A shot whose face can't fit gets no punch at all. The upscale guard
  (``decide.UPSCALE_MAX``) holds too.
* Reduced motion: the effect is small and slow by design (12 % at most, 0.4 s eases, one move per ``GAP``), it is a
  per-take switch (``punch_in``, on by default), and the app defaults that switch to off for a person whose device asks
  for reduced motion (take.js). Turning it off renders the shot exactly as before.

Only the ``track`` layout gets it: fit, split and screen layouts already frame a whole scene or a composite.
"""
from __future__ import annotations

from statistics import median

from .decide import OUT_H, UPSCALE_MAX
from .path import DEAD, FACE_Y

ZMAX = 1.12        # the peak zoom
RAMP = 0.4         # seconds to ease in, and to ease out (within the 300-500 ms the owner asked for)
HOLD = 1.2         # the longest a punch holds at its peak
GAP = 3.0          # at least this many seconds between two punches
PAUSE = 0.45       # a pause before a word this long marks emphasis
MARGIN = 0.03      # the face box keeps this much of the frame clear at each edge, at the peak
MIN_SHOT = 1.5     # a shot shorter than this gets no punch


def emphasis(words: list[dict] | None, start: float) -> list[float]:
    """Clip-time starts of emphasised words: after a pause of PAUSE s or more, a number, or a word ending in "!"."""
    ws = [w for w in words or [] if str(w.get("w", "")).strip()]
    out = []
    for i, w in enumerate(ws):
        t = str(w["w"]).strip()
        s = float(w["s"]) - start
        paused = i > 0 and float(w["s"]) - float(ws[i - 1]["e"]) >= PAUSE
        if paused or t.rstrip("\"')]").endswith("!") or any(ch.isdigit() for ch in t):
            out.append(round(s, 3))
    return out


def safe_peak(face_w: float, face_h: float, cy: float) -> float:
    """The largest zoom (<= ZMAX) that keeps a face box (fractions of the output frame) inside the frame with MARGIN,
    with its centre anywhere the tracker's dead zone allows (DEAD either side of the zoom centre)."""
    z = ZMAX
    # a point at offset d from the zoom centre lands at d * z: the far edge of the face is DEAD + half its size away
    half_x, half_y = DEAD + face_w / 2, DEAD + face_h / 2
    for room, half in ((0.5 - MARGIN, half_x), (min(cy, 1 - cy) - MARGIN, half_y)):
        if half <= 0:
            continue
        z = min(z, room / half)
    return round(z, 3) if z >= 1.02 else 1.0


def events(shot_start: float, shot_end: float, emph: list[float]) -> list[tuple[float, float]]:
    """[(t0, t1)] punch windows in shot time: in at t0, back to 1.0 by t1."""
    dur = shot_end - shot_start
    if dur < MIN_SHOT:
        return []
    starts = [0.0] + [round(t - shot_start, 3) for t in emph if shot_start + 0.2 <= t < shot_end - 2 * RAMP]
    picked: list[float] = []
    for t in sorted(starts):
        if not picked or t - picked[-1] >= GAP:
            picked.append(t)
    out = []
    for i, t0 in enumerate(picked):
        nxt = picked[i + 1] if i + 1 < len(picked) else dur
        t1 = min(t0 + 2 * RAMP + HOLD, nxt, dur)
        if t1 - t0 >= 2 * RAMP:
            out.append((round(t0, 3), round(t1, 3)))
    return out


def z_expr(windows: list[tuple[float, float]], peak: float) -> str:
    """zoompan's z: 1 + (peak - 1) * an eased bump per window, on the input time ``it`` (shot time)."""
    if not windows or peak <= 1.0:
        return "1"
    bumps = []
    for t0, t1 in windows:
        u = f"min(clip((it-{t0:.3f})/{RAMP},0,1),clip(({t1:.3f}-it)/{RAMP},0,1))"
        bumps.append(f"(1-cos(PI*{u}))/2")  # cosine ease: 0 -> 1 -> 0 with zero speed at both ends
    return f"1+{peak - 1:.4f}*({'+'.join(bumps)})"


def plan_shot(s: dict, emph: list[float], face: dict | None, src: tuple[int, int]) -> dict | None:
    """{windows, peak, cy} for one track shot, or None (not a track shot, too short, no room, nothing to do)."""
    p = s.get("path")
    if s.get("layout") != "track" or not p:
        return None
    sw, sh = src
    cw, ch = int(p["crop_w"]), int(p["crop_h"])
    # where the face sits in the output: centred across; FACE_Y down when the crop can move vertically, else where it is
    cy = FACE_Y if ch < sh else float((face or {}).get("cy", FACE_Y))
    fw = float((face or {}).get("w", 0.0)) * sw / cw
    fh = float((face or {}).get("h", 0.0)) * sh / ch
    peak = safe_peak(fw, fh, cy)
    peak = min(peak, UPSCALE_MAX * ch / OUT_H)  # never past the upscale guard (OUT_H / (ch / peak) <= UPSCALE_MAX)
    if peak < 1.02:
        return None
    wins = events(float(s["start"]), float(s["end"]), emph)
    if not wins:
        return None
    return {"windows": wins, "peak": round(peak, 3), "cy": round(min(max(cy, 0.0), 1.0), 3)}


def face_size(points: list[tuple[float, dict]]) -> dict | None:
    """Median face box (fractions of the source) over a shot's samples: {w, h, cy}."""
    if not points:
        return None
    return {"w": median(f["w"] for _, f in points), "h": median(f["h"] for _, f in points),
            "cy": median(f["y"] + f["h"] / 2 for _, f in points)}


def filter_for(pp: dict | None, w: int, h: int) -> str:
    """The zoompan step appended after the shot's scale to w x h ('' when there is no punch). One output frame per input
    frame (d=1) at 30 fps; the zoom centre stays put on screen: x = c * (iw - iw / zoom)."""
    if not pp:
        return ""
    z = z_expr(pp["windows"], pp["peak"])
    return (f",zoompan=z='{z}':x='(iw-iw/zoom)*0.5':y='(ih-ih/zoom)*{pp['cy']}':d=1:s={w}x{h}:fps=30")
