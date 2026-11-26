"""Is a shot a screen (slides, a screen recording, a UI) and how WIDE is its content?

Three grey thumbnails per shot; the shot is a screen when at least 2 of these hold:
  (a) Canny edge density, outside face boxes, >= 0.08 (text and UI are edge-dense)
  (b) mean absolute frame difference < 2.0 (static)
  (c) the 16-bin luma histogram has >= 40% of its mass in 2 bins (flat UI backgrounds)

The content width is the fraction of the frame WIDTH the edge-dense columns span (not how long it is on screen): a
corner logo spans ~15% and survives any crop, a spreadsheet spans ~100% and can't be cropped to 9:16.
"""
from __future__ import annotations

import math

EDGE_MIN = 0.08
STATIC_MAX = 2.0
FLAT_MIN = 0.40
COL_MIN = 0.05  # a column counts as content when this share of its pixels are edges
FOCUS_OUT_W = 1064     # the focus card's width on the 1080-wide clip (the fit layout's gutters)
FOCUS_SCALE = 2.0      # aim: UI text shown at 2x its source size (fit shows a 1376 px screen at 0.77x; 1.6x still read small, 26 Nov)
FOCUS_MIN_W = 0.30     # never crop narrower than 30% of the screen ...
FIT_READS = 1.4        # a screen the plain fit already shows at this scale reads fine as it is
FOCUS_MAX_W = 0.85     # ... and a crop wider than this isn't worth it (the plain fit is kept)
FOCUS_ASPECT = 1.0     # crop height / width (a square card leaves the caption band below it free)
CUT_SHARE = 0.5        # text just past a crop edge at half the density inside means the crop cuts through it ...
MOVE_SHARE = 0.6       # ... which is fine only when this share of what moves is inside the crop


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


def _best_window(profile, n: int) -> int:
    """Start of the length-n window with the most activity in a 1-D profile."""
    import numpy as np
    c = np.concatenate([[0.0], np.cumsum(profile, dtype="float64")])
    sums = c[n:] - c[:-n]
    return int(np.argmax(sums)) if len(sums) else 0


def focus_box(thumbs: list, src_w: int, src_h: int, min_share: float = 0.0) -> tuple[int, int, int, int] | None:
    """A readable crop (x, y, w, h in source pixels) of a screen recording's active region: where its text/UI edges
    and its changes between frames (typing, the cursor, scrolling) are densest, sized so the UI is shown at about
    FOCUS_SCALE x on the 9:16 clip. None when there is nothing to go on or the crop would be nearly the whole screen."""
    import cv2
    import numpy as np
    if not thumbs or src_w <= 0 or src_h <= 0:
        return None
    th, tw = thumbs[0].shape[:2]
    cw = min(src_w, max(int(FOCUS_MIN_W * src_w), int(FOCUS_OUT_W / FOCUS_SCALE)))
    ch = min(src_h, int(cw * FOCUS_ASPECT))
    if cw >= FOCUS_MAX_W * src_w or FOCUS_OUT_W / src_w >= FIT_READS:
        return None   # nearly the whole screen, or the plain fit already shows a small screen large enough
    edges = np.mean([cv2.Canny(g, 80, 160) / 255.0 for g in thumbs], axis=0)
    chg = (np.mean([np.abs(a.astype("int16") - b.astype("int16")) > 12 for a, b in zip(thumbs, thumbs[1:])], axis=0)
           if len(thumbs) > 1 else np.zeros_like(edges))
    act = edges + 2.0 * chg  # what changes counts double: that is where the viewer looks
    if float(act.mean()) <= 1e-4:
        return None
    nw, nh = max(1, round(cw / src_w * tw)), max(1, round(ch / src_h * th))
    # across: centred on the activity's centre of mass, smoothed over ~5% of the width first (a thin scrollbar or
    # divider line isn't content) and squared (dense text and changes outweigh a full-width toolbar or address bar);
    # down: the busiest window
    k = max(3, tw // 20)
    cols = np.convolve(act.sum(axis=0).astype("float64"), np.ones(k) / k, mode="same") ** 2
    cx = float((cols * np.arange(tw)).sum() / cols.sum()) if cols.sum() > 0 else tw / 2
    x0 = int(min(tw - nw, max(0, round(cx - nw / 2))))
    y0 = _best_window(act[:, x0:x0 + nw].sum(axis=1), nh)
    # when the busy block (columns with real activity in these rows) fits in the crop, slide the crop to hold all of it
    # rather than shave its edge off
    prof = np.convolve(act[y0:y0 + nh].sum(axis=0).astype("float64"), np.ones(3) / 3, mode="same")
    on = np.where(prof >= 0.25 * float(prof.max()))[0] if prof.max() > 0 else np.array([], int)
    runs = np.split(on, np.where(np.diff(on) > 1)[0] + 1) if len(on) else []
    busy = np.concatenate([r for r in runs if len(r) >= max(3, tw // 30)] or [np.array([], int)])  # not a scrollbar
    hold = None
    if len(busy) and busy[-1] - busy[0] + 1 <= nw:
        if not (x0 <= busy[0] and busy[-1] < x0 + nw):
            x0 = int(min(tw - nw, max(0, busy[0] if busy[0] < x0 else busy[-1] + 1 - nw)))
        hold = (busy[0] / tw * src_w, (busy[-1] + 1) / tw * src_w)   # in source px, so rounding can't shave it
    if min_share and float(act[y0:y0 + nh, x0:x0 + nw].sum()) < min_share * float(act.sum()):
        return None   # the detail is spread over the frame (a landscape, a room): no single thing to zoom in on
    # a no-face shot on a flat backdrop is a title card ("ONE TAKE" came out "NE TAKE"): never cut its text; in a room
    # (a phone held up) the crop cuts the room on purpose (26 Nov Mac run: the cut check had sent the phone back to full)
    hist = np.histogram(thumbs[0], bins=16, range=(0, 256))[0].astype("float64")
    card = float(np.sort(hist / max(1.0, hist.sum()))[-2:].sum()) >= FLAT_MIN
    if (not min_share or card) and _cuts_content(edges, chg, x0, y0, nw, nh, CUT_SHARE):
        return None   # a title card or a line of big text runs past the crop ("AKE A PO"): the plain fit shows it whole
    if min_share and card and not _holds_text(edges, x0, y0, nw, nh):
        return None   # an animated title: its own motion is inside the crop, but its letters still run past it ("NE TAK")
    x = int(min(src_w - cw, max(0, round(x0 / tw * src_w))))
    if hold and x + cw < hold[1]:
        x = int(min(src_w - cw, max(0, math.ceil(hold[1]) - cw + 2)))
        x += x % 2   # the even rounding below must not undo it
    elif hold and x > hold[0]:
        x = int(max(0, math.floor(hold[0]) - 2))
    y = int(min(src_h - ch, max(0, round(y0 / th * src_h))))
    return x - x % 2, y - y % 2, cw - cw % 2, ch - ch % 2


def _cuts_content(edges, chg, x0: int, y0: int, nw: int, nh: int, share: float = CUT_SHARE) -> bool:
    """Would the crop [x0, x0+nw) x [y0, y0+nh) cut through what the shot is showing? True when text/UI carries on just
    past a side edge about as densely as inside the crop, unless most of what moves (typing, the cursor) is inside it:
    zooming in on where someone types is fine even when the code lines run on, cutting a still title card is not
    (26 Nov Mac run: the demo's "MAKE A POST" card came out as "AKE A PO")."""
    th, tw = edges.shape
    strip = max(2, tw // 32)
    rows = edges[y0:y0 + nh]
    inside = float(rows[:, x0:x0 + nw].mean()) if nw > 0 else 0.0
    if inside <= 1e-4:
        return False
    sides = [rows[:, max(0, x0 - strip):x0], rows[:, x0 + nw:min(tw, x0 + nw + strip)]]
    cut = any(sd.size and float(sd.mean()) >= share * inside for sd in sides)
    if not cut:
        return False
    total = float(chg.sum())
    return total <= 1e-4 or float(chg[y0:y0 + nh, x0:x0 + nw].sum()) < MOVE_SHARE * total


def _holds_text(edges, x0: int, y0: int, nw: int, nh: int) -> bool:
    """On a flat card: does the crop hold every column of text in its rows (with a small margin)? Unlike
    _cuts_content, motion doesn't excuse a cut here: on a card the moving thing is the title itself."""
    import numpy as np
    th, tw = edges.shape
    cols = edges[y0:y0 + nh].mean(axis=0)
    if not len(cols) or float(cols.max()) <= 1e-4:
        return True
    on = np.where(cols >= 0.1 * float(cols.max()))[0]
    m = max(1, tw // 80)
    return bool(on[0] >= max(0, x0 - m) and on[-1] < min(tw, x0 + nw + m)) if len(on) else True


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
