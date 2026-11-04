"""The tracked 9:16 crop path for one shot, as ffmpeg ``sendcmd`` lines for a named crop filter.

* Re-centred instantly on the face at the start of every shot (a cut never carries a pan).
* Dead zone: no move while the face centre stays inside the middle 30% of the crop; once it leaves, the crop follows
  with an exponential moving average (alpha 0.15 per 5 fps sample, rescaled to the 15 Hz path) until it's re-centred.
* Pan cap: 0.6 crop-widths per second.
* The face centre sits at 40% of the output height (headroom above, a caption band below) when the crop is shorter than
  the frame; a small face gets a mild zoom (at most 1.25x, never past the 2.5x upscale guard).
"""
from __future__ import annotations

from .decide import OUT_H, UPSCALE_MAX

ALPHA = 0.15          # per 5 fps sample
SAMPLE_HZ = 5.0
PATH_HZ = 15.0        # path resolution (every other frame at 30 fps)
DEAD = 0.15           # half of the middle 30%
SETTLE = 0.05         # a move ends once the face is within 5% of the crop centre
PAN_CAP = 0.6         # crop widths per second
FACE_Y = 0.40


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def zoom_for(face_h: float, src_h: int) -> float:
    z = 1.0
    if 0 < face_h < 0.15:
        z = min(1.25, 0.2 / face_h)
    while z > 1.0 and OUT_H / (src_h / z) > UPSCALE_MAX:
        z -= 0.05
    return max(1.0, round(z, 2))


def centre_series(points: list[tuple[float, dict]], ids_points: list[list[tuple[float, dict]]] | None = None):
    """[(t, cx, cy, h)] face centres (0-1) from one track's points, or the midpoint of two tracks' points."""
    if ids_points and len(ids_points) == 2:
        a, b = ids_points
        bt = {round(t, 2): f for t, f in b}
        out = []
        for t, f in a:
            g = bt.get(round(t, 2))
            if g is None:
                continue
            out.append((t, ((f["x"] + f["w"] / 2) + (g["x"] + g["w"] / 2)) / 2, ((f["y"] + f["h"] / 2) + (g["y"] + g["h"] / 2)) / 2,
                        max(f["h"], g["h"])))
        if out:
            return out
        points = a
    return [(t, f["x"] + f["w"] / 2, f["y"] + f["h"] / 2, f["h"]) for t, f in sorted(points, key=lambda p: p[0])]


def plan(series: list[tuple[float, float, float, float]], a: float, b: float, src_w: int, src_h: int) -> dict:
    """{crop_w, crop_h, zoom, keys: [(t_rel, x, y)]} for the shot [a, b] (clip seconds) from face centres."""
    from statistics import median
    hs = [h for _, _, _, h in series] or [0.2]
    z = zoom_for(median(hs), src_h)
    ch = min(src_h, _even(src_h / z))
    cw = min(src_w - src_w % 2, _even(ch * 9 / 16))
    max_x, max_y = src_w - cw, src_h - ch
    if not series:
        return {"crop_w": cw, "crop_h": ch, "zoom": z, "keys": [(0.0, max_x // 2, max_y // 2)]}
    step = 1.0 / PATH_HZ
    alpha = 1 - (1 - ALPHA) ** (SAMPLE_HZ / PATH_HZ)
    cap = PAN_CAP * cw * step

    def face_at(t: float):
        prev = series[0]
        for s in series:
            if s[0] > t:
                break
            prev = s
        return prev[1] * src_w, prev[2] * src_h

    def clamp(v, hi):
        return float(min(hi, max(0, v)))

    fx, fy = face_at(a)
    x, y = clamp(fx - cw / 2, max_x), clamp(fy - FACE_Y * ch, max_y)
    keys = [(0.0, int(round(x)), int(round(y)))]
    moving_x = moving_y = False
    t = a + step
    while t < b - 1e-6:
        fx, fy = face_at(t)
        off = (fx - (x + cw / 2)) / cw
        if abs(off) > DEAD:
            moving_x = True
        if moving_x:
            dx = alpha * (clamp(fx - cw / 2, max_x) - x)
            x = clamp(x + max(-cap, min(cap, dx)), max_x)
            if abs((fx - (x + cw / 2)) / cw) < SETTLE or x in (0.0, float(max_x)):
                moving_x = False
        if max_y > 0:
            offy = (fy - (y + FACE_Y * ch)) / ch
            if abs(offy) > DEAD:
                moving_y = True
            if moving_y:
                dy = alpha * (clamp(fy - FACE_Y * ch, max_y) - y)
                y = clamp(y + max(-cap, min(cap, dy)), max_y)
                if abs((fy - (y + FACE_Y * ch)) / ch) < SETTLE or y in (0.0, float(max_y)):
                    moving_y = False
        k = (round(t - a, 3), int(round(x)), int(round(y)))
        if k[1:] != keys[-1][1:]:
            keys.append(k)
        t += step
    return {"crop_w": cw, "crop_h": ch, "zoom": z, "keys": keys}


def at(p: dict, t_rel: float) -> tuple[int, int]:
    """Crop (x, y) at shot time t_rel (step function, as sendcmd applies it)."""
    cur = p["keys"][0]
    for k in p["keys"]:
        if k[0] > t_rel + 1e-9:
            break
        cur = k
    return cur[1], cur[2]


def sendcmd(p: dict, target: str) -> str:
    """sendcmd lines that move the crop filter named ``target`` (e.g. crop@s0) along the path."""
    return "".join(f"{t:.3f} {target} x {x}, {target} y {y};\n" for t, x, y in p["keys"][1:])


def coverage(p: dict, points: list[tuple[float, dict]], a: float, src_w: int, src_h: int) -> float:
    """Share of face samples whose centre is inside the crop (the reframe acceptance test: >= 95%)."""
    if not points:
        return 1.0
    inside = 0
    for t, f in points:
        x, y = at(p, t - a)
        cx, cy = (f["x"] + f["w"] / 2) * src_w, (f["y"] + f["h"] / 2) * src_h
        inside += x <= cx <= x + p["crop_w"] and y <= cy <= y + p["crop_h"]
    return inside / len(points)
