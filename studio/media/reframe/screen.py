"""Is a shot a screen (slides, a screen recording, a UI) and how WIDE is its content?

Three grey thumbnails per shot; the shot is a screen when at least 2 of these hold:
  (a) Canny edge density, outside face boxes, >= 0.08 (text and UI are edge-dense)
  (b) mean absolute frame difference < 2.0 (static)
  (c) the 16-bin luma histogram has >= 40% of its mass in 2 bins (flat UI backgrounds)

The content width is the fraction of the frame WIDTH the edge-dense columns span (not how long it is on screen): a
corner logo spans ~15% and survives any crop, a spreadsheet spans ~100% and can't be cropped to 9:16.
"""
from __future__ import annotations

EDGE_MIN = 0.08
STATIC_MAX = 2.0
FLAT_MIN = 0.40
COL_MIN = 0.03  # a column counts as content when this share of its pixels are edges


def _mask_faces(edges, faces: list[dict]):
    h, w = edges.shape
    for f in faces or []:
        x0, y0 = int(max(0, (f["x"] - f["w"] * 0.3) * w)), int(max(0, (f["y"] - f["h"] * 0.3) * h))
        x1, y1 = int(min(w, (f["x"] + f["w"] * 1.3) * w)), int(min(h, (f["y"] + f["h"] * 1.6) * h))
        edges[y0:y1, x0:x1] = 0
    return edges


def signals(thumbs: list, faces: list[dict] | None = None) -> dict:
    """{edge, diff, flat, screen, content_width} from up to 3 grey thumbnails of one shot (numpy uint8 arrays)."""
    import cv2
    import numpy as np
    if not thumbs:
        return {"edge": 0.0, "diff": 99.0, "flat": 0.0, "screen": False, "content_width": 0.0}
    edges = [_mask_faces(cv2.Canny(g, 80, 160), faces) for g in thumbs]
    edge = float(np.mean([e.mean() / 255.0 for e in edges]))
    diffs = [float(np.mean(np.abs(a.astype("int16") - b.astype("int16")))) for a, b in zip(thumbs, thumbs[1:])]
    diff = float(np.mean(diffs)) if diffs else 0.0
    flats = []
    for g in thumbs:
        hist = np.histogram(g, bins=16, range=(0, 256))[0].astype("float64")
        hist /= max(1.0, hist.sum())
        flats.append(float(np.sort(hist)[-2:].sum()))
    flat = float(np.mean(flats))
    votes = (edge >= EDGE_MIN) + (diff < STATIC_MAX) + (flat >= FLAT_MIN)
    cols = np.mean([e.mean(axis=0) / 255.0 for e in edges], axis=0)
    active = np.where(cols >= COL_MIN)[0]
    width = float((active[-1] - active[0] + 1) / len(cols)) if len(active) >= max(2, len(cols) // 50) else 0.0
    return {"edge": round(edge, 4), "diff": round(diff, 3), "flat": round(flat, 3), "screen": bool(votes >= 2),
            "content_width": round(width, 3)}


def pick_thumbs(thumbs: dict, a: float, b: float) -> list:
    """Three thumbnails at 25 / 50 / 75% of the shot [a, b] (clip seconds)."""
    if not thumbs:
        return []
    ts = sorted(thumbs)
    out = []
    for frac in (0.25, 0.5, 0.75):
        want = a + (b - a) * frac
        t = min(ts, key=lambda x: abs(x - want))
        if a - 0.3 <= t <= b + 0.3 and (not out or out[-1] is not thumbs[t]):
            out.append(thumbs[t])
    return out
