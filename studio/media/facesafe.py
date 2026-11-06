"""Face-safe overlays: no hook box, caption, credit chip or pill ever sits on the speaker's face (owner bug, 6 Nov 2026).

The bug: One take's Opus-style render (studio/media/longform.render_moment -> compose.render_style "opus") put its
overlays at fixed fractions of the frame (hook box under the top safe line, credit under it, captions at 60 % height).
The face track only steered the crop's x. On a talking head cropped to 9:16 the face fills the upper half, so the
white hook box sat on the forehead and eyes and the captions on the chin.

What this module does (pure geometry, no OpenCV, except ``detect_out`` / ``qa_measure`` which import cv2 lazily):

* ``head_box``      a detector's face box grown to the head (forehead / hair above, chin below), fractions of the source.
* ``view_at``       where a source pixel lands in the output at clip time t: the tracked crop (``take.crop_plan`` keys),
                    the centre crop, the blurred pad, the burned-subtitle crop and a punch-in zoom (reframe/punch.py,
                    ``zoompan`` with x = (iw - iw/z) * 0.5, y = (ih - ih/z) * cy).
* ``out_boxes``     face samples (clip time) -> output pixel boxes, after reframe and zoom.
* ``zone``          the keep-out rectangle over a set of boxes: rows / columns covered in at least ``occ`` of the samples
                    (a single stray detection doesn't empty the frame), plus a margin.
* ``segment_zones`` one keep-out per segment (scene cuts inside the clip, at most SEG_S long) for the captions, and one
                    for the whole clip for overlays that stay on screen the whole time (hook box, credit chip).
* ``place_opus``    the Opus-style layout: captions in the free band under the chin (or above the head), the hook box in
                    the free top band, else stacked in the bottom band, shrunk, shortened, and dropped when nothing fits;
                    the credit chip in a free spot, else the lowest safe spot. Notes say what moved and why.
* ``overlap``       the fraction of a face box covered by a rectangle / a mask: the QA number.

QA after the render (``qa_measure``): frames sampled from the finished clip are compared with the same frames of the
reframed source WITHOUT overlays; the pixels that differ are what we drew. YuNet finds the faces on the clean frame (in
output coordinates already, so no mapping can hide a mistake), and the overlap is the share of each face box (plus a
small margin) that our pixels cover. Over QA_MAX the caller re-places with those boxes and renders once more; still over,
the clip is marked needs_review with the reason. Never silent: every fallback is named in the returned record.
"""
from __future__ import annotations

import copy
import os
import threading
from statistics import median

HEAD_UP = 0.35      # forehead + hair above a detector's face box (fraction of the box height)
HEAD_DOWN = 0.15    # chin below it
HEAD_SIDE = 0.10    # ears, each side (fraction of the box width)
MARGIN = 0.012      # clear space between a face and any overlay, as a fraction of the output height (23 px at 1920)
OCC_STATIC = 0.05   # a static overlay keeps clear of rows a face covers in >= 5 % of the clip's samples
OCC_SEGMENT = 0.10  # a segment's captions keep clear of rows a face covers in >= 10 % of the segment's samples
SEG_S = 4.0         # captions re-place at most every 4 s (and at every scene cut)
QA_MAX = 0.02       # more than 2 % of a face (box + QA_PAD) under our pixels fails the render QA
QA_PAD = 0.06       # the QA face box grows 6 % each side (eyes, nose and mouth are what matter)
QA_FRAMES = 6
MIN_HOOK_H = 0.045  # a hook box shorter than 4.5 % of the frame height (86 px at 1920) isn't readable: shorten / drop
LEVELS = ("full", "compact")  # caption levels: the style's own size, then one line at COMPACT_SIZE of it under the chin
COMPACT_SIZE = 0.62           # opus at 4:5: 1350 x 0.062 x 0.62 x 1.25 x 1.08 = ~70 px tall, one line
SAMPLE_CACHE_MAX = 32         # YuNet samples kept per (source, clip span): the 4:5 render reuses the 9:16 samples


# ---------------------------------------------------------------------- boxes
def head_box(f: dict) -> tuple[float, float, float, float]:
    """A face sample {x, y, w, h} (top-left + size, fractions of the source) -> (x0, y0, x1, y1) of the head."""
    x, y, w, h = float(f["x"]), float(f["y"]), float(f["w"]), float(f["h"])
    return (x - w * HEAD_SIDE, y - h * HEAD_UP, x + w * (1 + HEAD_SIDE), y + h * (1 + HEAD_DOWN))


def from_center(f: dict, src_w: int, src_h: int) -> dict:
    """An analysis / Jev sample {t, cx, cy, w[, h]} -> {x, y, w, h}. Without h the height is estimated from the width
    (faces are ~1.2x taller than wide in pixels; reframe/faces.from_analysis uses the same rule)."""
    w = float(f["w"])
    h = float(f["h"]) if f.get("h") else min(1.0, w * src_w / max(1, src_h) * 1.2)
    return {"x": float(f["cx"]) - w / 2, "y": float(f.get("cy", 0.4)) - h / 2, "w": w, "h": h}


def view_at(plan: dict | None, t: float, src_w: int, src_h: int, W: int, H: int, keep: float = 1.0,
            zoom: tuple[float, float] | None = None):
    """A function (x_px, y_px in the source) -> (x, y) in the output at clip time t, matching compose._main_graph:
    ``track`` crops crop_w x (src_h * keep) at take.crop_x_at(plan, t) and scales to W x H; ``pad`` fits the whole frame
    inside; anything else is the centre crop (scale to cover). ``keep`` < 1 is the burned-subtitle crop (the top part is
    kept). ``zoom`` (z, cy) is a punch-in around the horizontal centre and cy of the height."""
    sh = src_h * keep
    mode = (plan or {}).get("mode")
    if mode == "track":
        from ..take import crop_x_at
        cx, cw = float(crop_x_at(plan, t)), float(plan["crop_w"])
        sx, sy, ox, oy = W / cw, H / sh, -cx * W / cw, 0.0
    elif mode == "pad":
        k = min(W / src_w, H / sh)
        sx = sy = k
        ox, oy = (W - src_w * k) / 2, (H - sh * k) / 2
    else:
        k = max(W / src_w, H / sh)
        sx = sy = k
        ox, oy = (W - src_w * k) / 2, (H - sh * k) / 2

    def f(x: float, y: float) -> tuple[float, float]:
        X, Y = x * sx + ox, y * sy + oy
        if zoom and zoom[0] > 1.0:
            z, zy = zoom
            X = (X - (W - W / z) * 0.5) * z
            Y = (Y - (H - H / z) * zy) * z
        return X, Y
    return f


def out_boxes(samples: list[dict], plan: dict | None, src_w: int, src_h: int, W: int, H: int, keep: float = 1.0,
              zoom_at=None, head: bool = True) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Face samples [{t (clip s), faces: [{x, y, w, h}]}] -> [(t, (x0, y0, x1, y1))] output pixels, clipped to the frame.
    zoom_at(t) -> (z, cy) or None: the punch-in at that time."""
    out = []
    for s in samples or []:
        t = float(s["t"])
        fn = view_at(plan, t, src_w, src_h, W, H, keep, zoom_at(t) if zoom_at else None)
        for face in s.get("faces") or []:
            x0, y0, x1, y1 = head_box(face) if head else (face["x"], face["y"], face["x"] + face["w"], face["y"] + face["h"])
            a, b = fn(x0 * src_w, y0 * src_h)
            c, d = fn(x1 * src_w, y1 * src_h)
            bx = (max(0, int(a)), max(0, int(b)), min(W, int(c + 0.999)), min(H, int(d + 0.999)))
            if bx[2] - bx[0] >= 4 and bx[3] - bx[1] >= 4:
                out.append((t, bx))
    return out


def zone(boxes: list[tuple[int, int, int, int]], W: int, H: int, occ: float = OCC_SEGMENT, n: int | None = None,
         margin: float = MARGIN) -> tuple[int, int, int, int] | None:
    """The keep-out rectangle (x0, y0, x1, y1): the rows and columns covered by a face in at least ``occ`` of ``n``
    samples (n defaults to the number of boxes), grown by ``margin`` of H. None when no row reaches it."""
    if not boxes:
        return None
    n = max(1, n or len(boxes))
    need = max(1, int(round(occ * n + 0.4999)))

    def span(lo_hi: list[tuple[int, int]], size: int) -> tuple[int, int] | None:
        step = max(1, size // 240)
        cuts = sorted({v for a, b in lo_hi for v in (a, b)} | set(range(0, size + 1, step)))
        first = last = None
        for p, q in zip(cuts, cuts[1:]):
            mid = (p + q) / 2
            if sum(1 for a, b in lo_hi if a <= mid < b) >= need:
                first = p if first is None else first
                last = q
        return (first, last) if first is not None else None
    ys = span([(b[1], b[3]) for b in boxes], H)
    xs = span([(b[0], b[2]) for b in boxes], W)
    if not ys or not xs:
        return None
    m = int(round(margin * H))
    return (max(0, xs[0] - m), max(0, ys[0] - m), min(W, xs[1] + m), min(H, ys[1] + m))


def segment_zones(boxes_t: list[tuple[float, tuple]], D: float, W: int, H: int, cuts: list[float] | None = None,
                  n_per_s: float = 2.0) -> list[dict]:
    """[{start, end, keepout}] over the clip [0, D]: a segment at every scene cut, none longer than SEG_S. A segment's
    expected sample count is its length x the sampling rate, so a segment where the face shows briefly still counts it."""
    edges = sorted({0.0, float(D)} | {float(c) for c in cuts or [] if 0.2 < c < D - 0.2})
    segs = []
    for a, b in zip(edges, edges[1:]):
        k = max(1, int((b - a) // SEG_S + (1 if (b - a) % SEG_S > 1e-6 else 0)))
        step = (b - a) / k
        for i in range(k):
            segs.append((a + i * step, a + (i + 1) * step))
    out = []
    for a, b in segs:
        bx = [x for t, x in boxes_t if a - 0.25 <= t < b + 0.25]
        exp = max(1, int(round((b - a + 0.5) * n_per_s)))
        out.append({"start": round(a, 3), "end": round(b, 3), "keepout": zone(bx, W, H, OCC_SEGMENT, max(len(bx), exp) if bx else None)})
    return out


def overlap(face: tuple, rect: tuple) -> float:
    """Share of the face box's area that the rectangle covers (0-1)."""
    fx0, fy0, fx1, fy1 = face
    rx0, ry0, rx1, ry1 = rect
    iw, ih = max(0, min(fx1, rx1) - max(fx0, rx0)), max(0, min(fy1, ry1) - max(fy0, ry0))
    area = max(1, (fx1 - fx0) * (fy1 - fy0))
    return iw * ih / area


def hits(rect: tuple, keepout: tuple | None) -> bool:
    if not keepout:
        return False
    return not (rect[2] <= keepout[0] or rect[0] >= keepout[2] or rect[3] <= keepout[1] or rect[1] >= keepout[3])


# ---------------------------------------------------------------------- placement (Opus-style layout)
def bands(keepout: tuple | None, safe: tuple[int, int, int, int]) -> dict:
    """Free horizontal bands inside the safe area: {"top": (y0, y1), "bottom": (y0, y1)} (a band may be empty)."""
    l, t, r, b = safe
    if not keepout:
        return {"top": (t, b), "bottom": (t, b), "clear": True}
    return {"top": (t, max(t, min(b, keepout[1]))), "bottom": (min(b, max(t, keepout[3])), b), "clear": False}


def caption_center(keepout: tuple | None, safe: tuple, cap_h: int, default_cy: int) -> tuple[int, str]:
    """(centre y, where) for a caption block of cap_h px: under the chin when it fits there, else above the head, else
    the safest band (the larger free band, centred in it; the block can't avoid the face there, the caller flags it)."""
    l, t, r, b = safe
    half = cap_h // 2
    if not keepout:
        return default_cy, "default"
    bd = bands(keepout, safe)
    (ty0, ty1), (by0, by1) = bd["top"], bd["bottom"]
    if default_cy - half >= keepout[3] and default_cy + half <= b:
        return default_cy, "default"           # the usual line is already under the chin
    if by1 - by0 >= cap_h:
        return by0 + half, "below_face"         # right under the chin, the lower third
    if ty1 - ty0 >= cap_h:
        return ty1 - half, "above_face"
    # nothing fits: the safest band, the bigger one, pushed to its far edge (less of the block over the face)
    if by1 - by0 >= ty1 - ty0:
        return b - half, "safest_bottom"
    return t + half, "safest_top"


def place_opus(L: dict, W: int, H: int, *, static: tuple | None, segments: list[dict], cap_h: int, hook: str,
               hook_fit, credit_h: int, captions_on: bool, opener: tuple | None = None,
               opener_s: float | None = None) -> tuple[dict, dict]:
    """Re-place the Opus-style overlays in L (compose.layout) clear of the faces.

    static: the clip-wide keep-out (hook box and credit chip stay on screen the whole clip); segments: [{start, end,
    keepout}] for the captions. hook_fit(text, w, h) -> (fits: bool, height_px) measures the hook box for a text and a
    box. Returns (L, record): L["hook"] may be None (dropped), L["hook_text"] the (maybe shortened) text,
    L["credit_xy"] the chip's top-left, L["caption_cy"] a list of (start, end, centre y) per segment.
    opener_s: the hook box and credit chip show only for the first opener_s seconds (compose.HOOK_OPENER_SECS): they keep
    clear of ``opener`` (the keep-out of those seconds, zones_for(opener_s=...)) when given, else of ``static``, and of
    the captions of those seconds only."""
    l, t, r, b = L["safe"]
    safe = (l, t, r, b)
    rec: dict = {"keepout": list(static) if static else None, "hook": "kept", "notes": []}
    caps_static = static  # the captions' own fallback keep-out stays the clip-wide one
    if opener_s:
        rec["opener_s"] = opener_s
        if opener is not None:
            static = opener
            rec["opener_keepout"] = list(opener)
    default_cy = int(L.get("captions_center") or H * 0.6)
    pad = 10                                     # the white box is drawn 10 px outside L["hook"]
    gap = int(H * 0.012)
    # 1) captions, segment by segment
    caps, cap_where = [], []
    for s in segments or [{"start": 0.0, "end": 1e9, "keepout": caps_static}]:
        cy, where = caption_center(s.get("keepout"), safe, cap_h, default_cy)
        caps.append((s["start"], s["end"], int(cy)))
        cap_where.append(where)
    if captions_on:
        L["caption_cy"] = caps
        L["captions_center"] = int(median([c[2] for c in caps])) if caps else default_cy
        rec["captions"] = sorted(set(cap_where))
        if any(w.startswith("safest") for w in cap_where):
            rec["notes"].append("no band clear of the face for the captions in "
                                f"{sum(w.startswith('safest') for w in cap_where)} of {len(cap_where)} segments: safest band used")
    # where the captions sit over the whole clip (the static overlays keep clear of every position they take)
    cap_rects = [(l, cy - cap_h // 2, r, cy + cap_h // 2) for a, _, cy in caps
                 if not opener_s or a < opener_s] if captions_on else []

    def free(rect) -> bool:
        return not hits(rect, static) and not any(hits(rect, c) for c in cap_rects)
    # 2) the hook box: the default spot, else the free top band, else under / over the captions, shrinking, then shorter
    default_hook = L.get("hook")
    text = (hook or "").strip()
    placed = None
    if text and default_hook:
        hx0, hx1 = default_hook[0], default_hook[2]
        want_h = default_hook[3] - default_hook[1]
        cand = []
        if free((hx0 - pad, default_hook[1] - pad, hx1 + pad, default_hook[3] + pad)):
            cand.append(("default", default_hook[1], default_hook[1] + want_h))
        bd = bands(static, safe)
        ty0, ty1 = bd["top"]
        by0, by1 = bd["bottom"]
        cap_top = min((c[1] for c in cap_rects), default=b)
        cap_bot = max((c[3] for c in cap_rects), default=t)
        # free stretches (y0, y1) the box may use, best first
        spans = [("top", ty0 + pad, ty1 - pad)]
        if captions_on:
            spans += [("above_captions", max(by0, t) + pad, cap_top - gap - pad), ("below_captions", cap_bot + gap + pad, by1 - pad)]
        else:
            spans += [("bottom", by0 + pad, by1 - pad)]
        words = text.split()
        tries = [text]
        for n in (10, 8, 6, 5, 4):
            if len(words) > n:
                tries.append(" ".join(words[:n]).rstrip(",;:.-") + "…")
        for where, y0, y1 in spans:
            if cand:
                break
            room = y1 - y0
            if room < MIN_HOOK_H * H:
                continue
            h = min(want_h, room)
            for k, tx in enumerate(tries):
                ok, need = hook_fit(tx, hx1 - hx0, h)
                if not ok:
                    continue
                hh = min(h, max(need + 16, int(MIN_HOOK_H * H)))
                y = y1 - hh if where == "above_captions" else y0
                if free((hx0 - pad, y - pad, hx1 + pad, y + hh + pad)):
                    cand.append((where if k == 0 else f"{where}, shortened", y, y + hh))
                    text = tx
                    break
        if cand:
            where, y0, y1 = cand[0]
            placed = (hx0, int(y0), hx1, int(y1))
            rec["hook"] = where
        else:
            rec["hook"] = "dropped"
            rec["notes"].append("hook box dropped: no space clear of the face, even shortened")
    L["hook"] = placed
    L["hook_text"] = text if placed else ""
    # 3) the credit chip: under the hook, else the free top band, else the bottom of the safe area, else the lowest spot
    if credit_h:
        cw = int((r - l) * 0.75)
        spots = []
        if placed:
            spots.append(("under_hook", placed[3] + pad + 14))
            spots.append(("over_hook", placed[1] - pad - 14 - credit_h))
        spots += [("top", t), ("bottom", b - credit_h), ("below_captions", (max((c[3] for c in cap_rects), default=t)) + gap)]
        hook_rect = (placed[0] - pad, placed[1] - pad, placed[2] + pad, placed[3] + pad) if placed else None
        got = None
        for where, y in spots:
            rect = (l, y, l + cw, y + credit_h)
            if y < t or y + credit_h > b or (hook_rect and hits(rect, hook_rect)):
                continue
            if free(rect):
                got = (where, y)
                break
        if not got and opener_s:  # an opener's credit is on the end card too: with no free spot it shows only there
            L["credit_xy"] = None
            L["credit_end_only"] = True
            rec["credit"] = "end_card_only"
            rec["notes"].append("credit chip shown on the end card only: no free spot in the opening seconds")
            return L, rec
        if not got:  # the credit is a rights line: it always shows, at the lowest safe spot clear of the hook box (the
            # face may be there: said in the notes); before 6 Nov 2026 it could land on the hook's second line
            low = [("lowest_safe", b - credit_h)]
            if hook_rect:
                low += [("lowest_safe, over_hook", hook_rect[1] - 14 - credit_h), ("lowest_safe, under_hook", hook_rect[3] + 14)]
            low = [(w, y) for w, y in low if t <= y and y + credit_h <= b]
            got = next(((w, y) for w, y in low if not (hook_rect and hits((l, y, l + cw, y + credit_h), hook_rect))), None)
            if got is None:
                got = ("lowest_safe", b - credit_h)
                rec["notes"].append("credit chip overlaps the hook box: no spot clear of it")
            if hits((l, got[1], l + cw, got[1] + credit_h), static):
                rec["notes"].append("credit chip overlaps the face zone: no free spot")
        L["credit_xy"] = (l, int(got[1]))
        rec["credit"] = got[0]
    return L, rec


# ---------------------------------------------------------------------- caption levels + predicted cover (before rendering)
def compact_style(st):
    """The compact caption level of a captions.CapStyle: one line at COMPACT_SIZE of the size, sitting under the chin
    where the full block has no room (a face filling a 4:5 frame)."""
    from dataclasses import replace
    return replace(st, size_frac=st.size_frac * COMPACT_SIZE, min_frac=st.min_frac * COMPACT_SIZE, max_lines=1)


def caption_height(st, H: int) -> int:
    """The caption block's height in px for a CapStyle (the pop animation's growth included)."""
    return int(H * st.size_frac * 1.25 * st.max_lines * (1.08 if st.pop else 1.0))


def _union_cover(face: tuple, rects: list[tuple]) -> float:
    """Share of the face box covered by the union of rects (exact, by coordinate compression)."""
    fx0, fy0, fx1, fy1 = face
    area = max(1, (fx1 - fx0) * (fy1 - fy0))
    cl = [(max(fx0, r[0]), max(fy0, r[1]), min(fx1, r[2]), min(fy1, r[3])) for r in rects]
    cl = [r for r in cl if r[2] > r[0] and r[3] > r[1]]
    if not cl:
        return 0.0
    xs = sorted({v for r in cl for v in (r[0], r[2])})
    tot = 0
    for a, b in zip(xs, xs[1:]):
        ys = sorted((r[1], r[3]) for r in cl if r[0] <= a and r[2] >= b)
        cov, c0, c1 = 0, None, None
        for y0, y1 in ys:
            if c1 is None or y0 > c1:
                if c1 is not None:
                    cov += c1 - c0
                c0, c1 = y0, y1
            else:
                c1 = max(c1, y1)
        if c1 is not None:
            cov += c1 - c0
        tot += cov * (b - a)
    return tot / area


def overlay_rects(L: dict, cap_h: int, captions_on: bool, credit_h: int = 0) -> tuple[list[tuple], list[tuple]]:
    """(static rects, [(start, end, caption rect)]) of a placed layout: the hook box (with its 10 px white pad), the credit
    chip (its widest), and the caption block per segment across the safe width (an upper bound of the drawn text)."""
    l, t, r, b = L["safe"]
    static = []
    if L.get("hook"):
        hx0, hy0, hx1, hy1 = L["hook"]
        static.append((hx0 - 10, hy0 - 10, hx1 + 10, hy1 + 10))
    if credit_h and L.get("credit_xy"):
        x, y = L["credit_xy"]
        static.append((x, y, x + int((r - l) * 0.75), y + credit_h))
    caps = []
    if captions_on:
        caps = [(float(a), float(e), (l, int(cy) - cap_h // 2, r, int(cy) + cap_h // 2)) for a, e, cy in (L.get("caption_cy") or [])]
        if not caps and L.get("captions_center"):
            cc = int(L["captions_center"])
            caps = [(0.0, 1e9, (l, cc - cap_h // 2, r, cc + cap_h // 2))]
    return static, caps


def predict_cover(L: dict, pred: list[tuple[float, tuple]], W: int, H: int, *, cap_h: int, captions_on: bool,
                  credit_h: int = 0, pad: float = QA_PAD, static_until: float | None = None) -> float:
    """The worst share of a face (box + pad, like the QA) that the placed overlays would cover, from the face track
    [(clip t, (x0, y0, x1, y1) output px)]: what the render QA would measure, known before anything is encoded.
    static_until: the hook box and credit chip show only before this clip time (the opener)."""
    static, caps = overlay_rects(L, cap_h, captions_on, credit_h)
    worst = 0.0
    for t, f in pred or []:
        x0, y0, x1, y1 = f
        dx, dy = int((x1 - x0) * pad), int((y1 - y0) * pad)
        face = (max(0, x0 - dx), max(0, y0 - dy), min(W, x1 + dx), min(H, y1 + dy))
        rects = list(static) if (static_until is None or t < static_until) else []
        if caps:
            now = [rc for a, e, rc in caps if a - 1e-6 <= t < e]
            rects += now or [caps[-1][2] if t >= caps[-1][1] else caps[0][2]]
        worst = max(worst, _union_cover(face, rects))
    return round(worst, 4)


def placement_sig(L: dict, level: str) -> str:
    """A short signature of where every overlay lands: a re-place with the same signature would encode the same video."""
    import hashlib
    import json
    key = {"level": level, "hook": list(L["hook"]) if L.get("hook") else None, "hook_text": L.get("hook_text") or "",
           "credit": list(L["credit_xy"]) if L.get("credit_xy") else None,
           "caps": [list(c) for c in L.get("caption_cy") or []], "center": L.get("captions_center")}
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode(), usedforsecurity=False).hexdigest()[:16]


def qa_boxes(qa: dict) -> list[tuple[float, tuple]]:
    """The faces the QA found, as predicted-cover samples [(t, box)] (output px)."""
    return [(float(f["t"]), tuple(int(v) for v in b)) for f in qa.get("frames") or [] for b in f.get("faces") or []]


# ---------------------------------------------------------------------- render QA (cv2 + numpy, worker only)
def detect_out(img_bgr, score: float = 0.6) -> list[tuple[int, int, int, int]]:
    """YuNet faces on an output-sized frame -> pixel boxes (x0, y0, x1, y1). Raises reframe.faces.FaceModelError."""
    from .reframe import faces as fm
    h, w = img_bgr.shape[:2]
    det = fm.detector(w, h, score=score)
    det.setInputSize((w, h))
    _, rows = det.detect(img_bgr)
    out = []
    for r in rows if rows is not None else []:
        x, y, bw, bh = (float(v) for v in r[:4])
        if bw >= 12 and bh >= 12:
            out.append((int(x), int(y), int(x + bw), int(y + bh)))
    return out


def drawn_mask(rendered, clean, thresh: int = 56):
    """Pixels our overlays changed: |rendered - clean| over ``thresh`` on any channel, opened 3x3 (encode noise and a
    frame of motion at the face's edge drop out; text, boxes and outlines stay)."""
    import cv2
    import numpy as np
    a = cv2.GaussianBlur(rendered, (3, 3), 0).astype(np.int16)
    b = cv2.GaussianBlur(clean, (3, 3), 0).astype(np.int16)
    m = (np.abs(a - b).max(axis=2) > thresh).astype(np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))


def face_cover(mask, face: tuple, pad: float = QA_PAD) -> float:
    """Share of the face box (grown by pad each side) whose pixels our overlays changed."""
    h, w = mask.shape[:2]
    x0, y0, x1, y1 = face
    dx, dy = int((x1 - x0) * pad), int((y1 - y0) * pad)
    x0, y0, x1, y1 = max(0, x0 - dx), max(0, y0 - dy), min(w, x1 + dx), min(h, y1 + dy)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return float(mask[y0:y1, x0:x1].mean())


# ---------------------------------------------------------------------- the worker side: faces for a clip, zones, QA
_SAMPLES: dict = {}
_SAMPLES_LOCK = threading.Lock()


def _sample_key(source: str, start: float, end: float, src_w: int, src_h: int, fps: float) -> tuple:
    try:
        st = os.stat(source)
        sig = (st.st_size, st.st_mtime_ns)
    except OSError:
        sig = None
    return (str(source), sig, round(float(start), 3), round(float(end), 3), int(src_w), int(src_h), float(fps))


def cached_sample(source: str, start: float, end: float, src_w: int, src_h: int, fps: float = 2.0) -> tuple[dict, bool]:
    """reframe.faces.sample for a clip span, cached per (file, span, size, fps): the clip's other ratios reuse the samples
    instead of decoding and running YuNet again (431 s per ratio under CPU steal, take #91). Deep copies both ways, so a
    caller adding Jev boxes never changes the cached samples. Returns (samples, cache_hit)."""
    from .reframe import faces as fm
    key = _sample_key(source, start, end, src_w, src_h, fps)
    with _SAMPLES_LOCK:
        hit = _SAMPLES.get(key)
    if hit is not None:
        return copy.deepcopy(hit), True
    got = fm.sample(source, start, end, src_w, src_h, fps=fps)
    with _SAMPLES_LOCK:
        while len(_SAMPLES) >= SAMPLE_CACHE_MAX:
            _SAMPLES.pop(next(iter(_SAMPLES)))
        _SAMPLES[key] = copy.deepcopy(got)
    return got, False


def clip_samples(source: str, start: float, end: float, src_w: int, src_h: int, faces: list[dict] | None,
                 fps: float = 2.0) -> tuple[list[dict], dict]:
    """Face samples over the clip [start, end] in clip seconds, and a record of what found them. YuNet (OpenCV, free)
    on every sampled frame; the analysis' Jev-verified keyframe boxes added on top; when YuNet can't run here, the
    analysis' samples (MediaPipe / Haar + Jev) and the record says so (never silent)."""
    rec: dict = {"detector": "", "samples": 0, "with_face": 0, "jev_boxes": 0, "fallback": None, "cache": None}
    samples: list[dict] | None = None
    try:
        got, hit = cached_sample(source, start, end, src_w, src_h, fps)
        rec["cache"] = "hit" if hit else "miss"
        samples = [{"t": s["t"], "faces": [{k: f[k] for k in ("x", "y", "w", "h", "score")} for f in s["faces"]]}
                   for s in got["samples"]]
        rec["detector"] = got["detector"]
    except Exception as e:  # noqa: BLE001 - no OpenCV / bad model on this worker: the analysis' faces, said so
        rec["fallback"] = f"YuNet unavailable here ({e.__class__.__name__}): the analysis' face samples were used"
        rec["detector"] = "analysis faces (MediaPipe / Haar + Jev)"
    by_t: dict[float, dict] = {}
    if samples is None:
        n = max(1, int((end - start) * fps))
        samples = [{"t": round(k / fps, 3), "faces": []} for k in range(n + 1)]
    for s in samples:
        by_t[round(s["t"], 2)] = s
    for f in faces or []:
        try:
            t = float(f["t"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (start <= t <= end):
            continue
        jev = f.get("src") == "jev"
        if rec["fallback"] is None and not jev:
            continue  # YuNet ran: the analysis' local samples add nothing, its Jev boxes do
        if float(f.get("score", 1)) < 0.5:
            continue
        box = from_center(f, src_w, src_h)
        near = min(by_t, key=lambda x: abs(x - (t - start))) if by_t else None
        if near is not None and abs(near - (t - start)) <= 0.5 / fps + 1e-6:
            by_t[near]["faces"].append({**box, "score": float(f.get("score", 1)), "src": "jev" if jev else "analysis"})
            rec["jev_boxes"] += int(jev)
    rec["samples"] = len(samples)
    rec["with_face"] = sum(1 for s in samples if s["faces"])
    return samples, rec


def zones_for(samples: list[dict], plan: dict | None, src_w: int, src_h: int, W: int, H: int, D: float, keep: float = 1.0,
              cuts: list[float] | None = None, extra: list[tuple[float, tuple]] | None = None, fps: float = 2.0,
              opener_s: float | None = None) -> dict:
    """{"static", "segments", "boxes"[, "opener"]}: the keep-outs a render needs (output pixels; clip seconds). opener_s:
    also the keep-out of the first opener_s seconds (the opus hook + credit opener, compose.HOOK_OPENER_SECS); with no
    face sample there it is the clip-wide one (never less careful than before)."""
    bt = out_boxes(samples, plan, src_w, src_h, W, H, keep) + list(extra or [])
    n = max(len(samples or []), int(D * fps))
    static = zone([b for _, b in bt], W, H, OCC_STATIC, n)
    out = {"static": static, "segments": segment_zones(bt, D, W, H, cuts, fps), "boxes": len(bt)}
    if opener_s:
        early = [b for tt, b in bt if tt <= opener_s + 0.5]
        out["opener"] = zone(early, W, H, OCC_STATIC, max(len(early), int(min(D, opener_s) * fps))) if early else static
    return out


def _frame(args: list[str], out: str) -> None:
    from . import ff
    ff.run(["-y", "-v", "error", *args, "-frames:v", "1", "-update", "1", out], timeout=120)


def clean_filter(plan: dict | None, t: float, W: int, H: int, keep: float = 1.0) -> str:
    """The reframe of compose._main_graph at clip time t, without any overlay (the QA's reference frame)."""
    from ..take import crop_x_at
    pre = f"crop=iw:trunc(ih*{keep}/2)*2:0:0," if keep < 1.0 else ""
    mode = (plan or {}).get("mode")
    if mode == "track":
        return f"{pre}crop={int(plan['crop_w'])}:ih:{int(crop_x_at(plan, t))}:0,scale={W}:{H},setsar=1"
    if mode == "pad":
        return f"{pre}scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    return f"{pre}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"


def qa_measure(rendered: str, source: str, c0: float, D: float, plan: dict | None, W: int, H: int, keep: float = 1.0,
               t_offset: float = 0.0, n: int = QA_FRAMES, workdir: str | None = None, predicted=None,
               keep_frames: str | None = None) -> dict:
    """Sample n frames of the finished clip, compare each with the same frame of the reframed source without overlays,
    find the faces (YuNet) on the clean frame and measure how much of each face our pixels cover. Returns
    {ok, max_cover, frames: [{t, faces, cover}], faces_by}. predicted(t) -> [boxes]: used where YuNet finds no face
    (said so in faces_by). keep_frames: a path prefix to save each rendered frame as PNG (for review)."""
    import tempfile
    from pathlib import Path

    import cv2
    td = Path(workdir or tempfile.mkdtemp(prefix="hq_fsqa_"))
    td.mkdir(parents=True, exist_ok=True)
    ts = [round(0.3 + i * max(0.0, D - 0.6) / max(1, n - 1), 3) for i in range(n)]
    frames, worst, by = [], 0.0, set()
    for i, t in enumerate(ts):
        rp, cp = str(td / f"qa_r{i}.png"), str(td / f"qa_c{i}.png")
        _frame(["-ss", f"{t_offset + t:.3f}", "-i", rendered], rp)
        _frame(["-ss", f"{c0 + t:.3f}", "-i", source, "-vf", clean_filter(plan, t, W, H, keep)], cp)
        r, c = cv2.imread(rp), cv2.imread(cp)
        if r is None or c is None or r.shape != c.shape:
            frames.append({"t": t, "error": "frame size mismatch"})
            continue
        try:
            fb = detect_out(c)
            how = "YuNet"
        except Exception as e:  # noqa: BLE001
            fb, how = [], f"none ({e.__class__.__name__})"
        if not fb and predicted:
            fb, how = list(predicted(t) or []), "predicted (YuNet found none)"
        by.add(how)
        m = drawn_mask(r, c)
        covers = [round(face_cover(m, f), 4) for f in fb]
        cov = max(covers) if covers else 0.0
        worst = max(worst, cov)
        frames.append({"t": t, "faces": [list(f) for f in fb], "cover": cov})
        if keep_frames:
            import shutil
            shutil.copy(rp, f"{keep_frames}{i}.png")
    return {"ok": worst <= QA_MAX, "max_cover": round(worst, 4), "threshold": QA_MAX, "frames": frames,
            "faces_by": sorted(by), "frames_dir": str(td)}


def qa_jpegs(qa: dict, k: int = 2, width: int = 540) -> list[dict]:
    """The k rendered QA frames with a face (worst cover first) as small JPEGs [{t, jpeg (base64)}], for Jev / SubQ's
    final look on the server (the keys stay there). EXIF-free: re-encoded from raw pixels."""
    import base64
    from pathlib import Path

    from PIL import Image

    from .jev_faces import FRAME_MAX_BYTES, prepare
    out = []
    order = sorted((f for f in qa.get("frames") or [] if f.get("faces")), key=lambda f: -f.get("cover", 0))
    for f in order[:k]:
        i = qa["frames"].index(f)
        p = Path(qa["frames_dir"]) / f"qa_r{i}.png"
        if not p.is_file():
            continue
        im = Image.open(p)
        im = im.resize((width, int(im.height * width / im.width)))
        jpeg, _ = prepare(im)
        if len(jpeg) <= FRAME_MAX_BYTES:
            out.append({"t": f["t"], "jpeg": base64.b64encode(jpeg).decode()})
    return out


# ---------------------------------------------------------------------- the main One take path (studio/media/reframe)
def track_face_spans(seg: dict, shots: list[dict] | None, src_w: int, src_h: int, H: int = 1920,
                     margin: float = MARGIN) -> list[tuple[int, int]]:
    """The head's vertical extent (y0, y1) on the H-tall clip in every single-face tracked shot that overlaps the
    sidecar segment ``seg`` ({start, end} clip seconds): the shot's median face box (``face_med``, fractions of the
    source) mapped through the tracked crop (``path``: crop_h; the crop keeps the face at path.FACE_Y when it can move
    vertically) and through the punch-in (``punch``: zoompan around (0.5, cy) up to its peak, reframe/punch.py), at
    zoom 1 and at the peak (the larger extent counts), grown to the head and by ``margin`` of H."""
    from .reframe.path import FACE_Y
    out = []
    for s in shots or []:
        try:
            if s.get("layout") != "track" or not (float(s["start"]) < float(seg["end"]) and float(seg["start"]) < float(s["end"])):
                continue
            p, f = s.get("path"), s.get("face_med")
            if not p or not f:
                continue
            ch = float(p["crop_h"])
            if ch <= 0:
                continue
            fh = float(f["h"]) * src_h / ch                     # the face's height as a fraction of the output
            cy = FACE_Y if ch < src_h - 1 else float(f["cy"])   # a full-height crop can't move: the face stays where it is
            y0, y1 = cy - fh / 2 - fh * HEAD_UP, cy + fh / 2 + fh * HEAD_DOWN
            ys = [(y0, y1)]
            pp = s.get("punch")
            if isinstance(pp, dict) and float(pp.get("peak") or 1.0) > 1.0:
                z, zc = float(pp["peak"]), float(pp.get("cy", cy))
                ys.append(((y0 - (1 - 1 / z) * zc) * z, (y1 - (1 - 1 / z) * zc) * z))
            a, b = min(v[0] for v in ys), max(v[1] for v in ys)
            m = margin
            out.append((int(max(0.0, a - m) * H), int(min(1.0, b + m) * H + 0.999)))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return out


def place_track_captions(sidecar: list[dict], shots: list[dict] | None, src_w: int, src_h: int, frames=None,
                         crop_at=None, crop_x=None, line_h: int = 170) -> dict:
    """Each tracked segment's caption line clear of the face (and of on-screen text, studio/media/ocr), in place:
    seg["caption_y"] (1920-tall coordinates). {moved_for_text, moved_for_face, blocked: [{start, end, face}]}:
    blocked segments keep the nearest safe line, which a face covers (the clip is flagged for review)."""
    from . import captions_ass, ocr
    rec = {"moved_for_text": 0, "moved_for_face": 0, "blocked": []}
    for seg in sidecar or []:
        try:
            face = track_face_spans(seg, shots, src_w, src_h, captions_ass.REF_H) if seg.get("layout", "track") == "track" else []
            default = captions_ass.band_y(seg, captions_ass.REF_H)
            y = ocr.free_caption_y(frames if isinstance(frames, list) else [], seg, default, src_w, src_h, crop_at=crop_at,
                                   crop_x=crop_x, avoid=face, line_h=line_h)
        except Exception:  # noqa: BLE001 - placement never fails a render
            continue
        cy = y if y is not None else default
        if y is not None:
            seg["caption_y"] = int(y)
            clear_text = ocr.free_caption_y(frames if isinstance(frames, list) else [], seg, default, src_w, src_h,
                                            crop_at=crop_at, crop_x=crop_x, line_h=line_h)
            rec["moved_for_text" if clear_text is not None else "moved_for_face"] += 1
        if any(not (cy + line_h / 2 <= a or cy - line_h / 2 >= b) for a, b in face):
            rec["blocked"].append({"start": seg.get("start"), "end": seg.get("end"), "face": [list(f) for f in face]})
    return rec


def video_jpegs(video: str, ts: list[float], width: int = 540) -> list[dict]:
    """Frames of a finished clip at ts (seconds) as small EXIF-free JPEGs [{t, jpeg (base64)}] for the final look."""
    import base64

    from .jev_faces import FRAME_MAX_BYTES, frame_image, prepare
    out = []
    for t in ts:
        try:
            jpeg, _ = prepare(frame_image(video, t, width))
        except Exception:  # noqa: BLE001 - a frame that can't be read is skipped
            continue
        if len(jpeg) <= FRAME_MAX_BYTES:
            out.append({"t": round(float(t), 2), "jpeg": base64.b64encode(jpeg).decode()})
    return out


MAIN_QA_FRAMES = 12  # the main path's QA looks at most this many tracked segments, spread over the whole clip


def main_qa(video: str, sidecar: list[dict], placement: dict, offset: float = 0.0, W: int = 1080, H: int = 1920,
            line_h: int = 170, k: int = 2) -> dict:
    """The main path's QA (worker): YuNet on the finished clip at each tracked segment's middle, and the share of each
    face (box + QA_PAD) under the caption line there. {status, reason?, max_cover, frames, faces_by, qa_jpegs}.
    Never raises: a QA that can't run says so and the clip waits for a person only when placement was blocked."""
    from . import captions_ass
    rec: dict = {"placed": True, "frames": [], "faces_by": [], "status": "ok", "max_cover": None}
    try:
        import cv2
        import numpy as np

        from .jev_faces import frame_image
        worst = []
        tracked = [s for s in sidecar or [] if s.get("layout", "track") == "track"]
        if len(tracked) > MAIN_QA_FRAMES:  # evenly over the clip, the last segment included (not just the first few)
            step = (len(tracked) - 1) / (MAIN_QA_FRAMES - 1)
            tracked = [tracked[round(i * step)] for i in range(MAIN_QA_FRAMES)]
        for seg in tracked:
            t = (float(seg["start"]) + float(seg["end"])) / 2
            im = frame_image(video, offset + t, W)
            bgr = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
            fb = detect_out(bgr)
            y = captions_ass.band_y(seg, bgr.shape[0])
            s = bgr.shape[0] / captions_ass.REF_H
            band = (int(W * 0.08), int(y - line_h * s / 2), int(W * 0.92), int(y + line_h * s / 2))
            covers = []
            for f in fb:
                dx, dy = int((f[2] - f[0]) * QA_PAD), int((f[3] - f[1]) * QA_PAD)
                covers.append(overlap((f[0] - dx, f[1] - dy, f[2] + dx, f[3] + dy), band))
            cov = round(max(covers), 4) if covers else 0.0
            rec["frames"].append({"t": round(offset + t, 2), "faces": [list(f) for f in fb], "cover": cov})
            worst.append((cov, offset + t, bool(fb)))
            rec["max_cover"] = max(cov, rec["max_cover"] or 0.0)  # kept as measured, even if a later frame fails
        rec["faces_by"] = ["YuNet"]
        if tracked and rec["max_cover"] is None:
            rec["max_cover"] = 0.0
        pick = [t for c, t, has in sorted(worst, key=lambda x: -x[0]) if has][:k]
        try:
            rec["qa_jpegs"] = video_jpegs(video, pick)
        except Exception:  # noqa: BLE001 - the measurement stands without the final look's frames
            rec["qa_jpegs"] = []
    except Exception as e:  # noqa: BLE001 - never reported as clear: max_cover stays None (or what was measured)
        rec["qa_error"] = f"{e.__class__.__name__}"
        rec["faces_by"] = [f"none ({e.__class__.__name__})"]
    if placement.get("blocked"):
        rec.update(status="needs_review", reason=f"no caption line clear of the speaker's face in {len(placement['blocked'])} "
                   "segment(s): the nearest safe line was used")
    elif rec["max_cover"] is not None and rec["max_cover"] > QA_MAX:
        rec.update(status="needs_review", reason=f"the captions cover {rec['max_cover'] * 100:.1f}% of the speaker's face "
                   f"(limit {QA_MAX * 100:.0f}%)")
    elif rec.get("qa_error") and not rec["frames"]:
        rec.update(status="needs_review", reason="the render QA couldn't check the frames for text on the face")
    return rec


def grow_qa_boxes(qa: dict) -> list[tuple[float, tuple]]:
    """The faces the QA found, grown to heads like head_box, as extra keep-out samples for the re-place."""
    out = []
    for f in qa.get("frames") or []:
        for b in f.get("faces") or []:
            x0, y0, x1, y1 = b
            w, h = x1 - x0, y1 - y0
            out.append((f["t"], (int(x0 - w * HEAD_SIDE), int(y0 - h * HEAD_UP), int(x1 + w * HEAD_SIDE), int(y1 + h * HEAD_DOWN))))
    # each QA face holds for its neighbourhood: repeat it so the static zone's occupancy counts it
    return out
