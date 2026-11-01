"""Auto-fit text into a box so it never clips, never overflows, never overlaps.

``fit(text, font, box_w, box_h)`` finds the LARGEST font size (binary search between min and max) at which the text,
word-wrapped greedily, fits the box measured with the real glyph metrics (ascent + descent, widest line), within
``max_lines``. A single word wider than the box is broken by characters, so even a long product name never runs off
the frame; if even the minimum size can't hold the text, the last line ends with an ellipsis INSIDE the box.
``draw_fit`` draws the result and returns the pixel rectangle actually inked, which callers and tests compare against
the frame's safe area and other elements to prove nothing is clipped or overlapping.

Approach adapted from the owner's HuhByte toolkit (dataanalystram/Huhbyte.ai, code/hb.py ``wrap``/``headline``),
made exact with Pillow's font metrics. Pure Pillow, no FFmpeg.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


@lru_cache(maxsize=256)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    p = Path(name)
    if not p.is_absolute():
        p = FONT_DIR / name
    return ImageFont.truetype(str(p), size)


def text_w(f: ImageFont.FreeTypeFont, s: str) -> float:
    return f.getlength(s)


def line_h(f: ImageFont.FreeTypeFont) -> int:
    asc, desc = f.getmetrics()
    return asc + desc


def _break_word(word: str, f, maxw: float) -> list[str]:
    out, cur = [], ""
    for ch in word:
        if cur and text_w(f, cur + ch) > maxw:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def wrap(text: str, f, maxw: float) -> list[str]:
    """Greedy word wrap; words wider than maxw are split by characters."""
    lines: list[str] = []
    for para in str(text).split("\n"):
        cur = ""
        for w in para.split():
            parts = [w] if text_w(f, w) <= maxw else _break_word(w, f, maxw)
            for part in parts:
                if not cur:
                    cur = part
                elif text_w(f, cur + " " + part) <= maxw:
                    cur = cur + " " + part
                else:
                    lines.append(cur)
                    cur = part
        lines.append(cur)
    return [ln for ln in lines if ln != ""] or [""]


@dataclass
class Fit:
    size: int
    lines: list[str]
    line_height: int
    width: int
    height: int
    ok: bool
    font_name: str = ""
    spacing: float = 1.12
    meta: dict = field(default_factory=dict)


def measure(lines: list[str], f, spacing: float) -> tuple[int, int, int]:
    lh = int(round(line_h(f) * spacing))
    w = int(max((text_w(f, ln) for ln in lines), default=0) + 0.999)
    h = lh * (len(lines) - 1) + line_h(f) if lines else 0
    return w, h, lh


def fit(text: str, font_name: str, box_w: int, box_h: int, *, max_size: int = 140, min_size: int = 18,
        max_lines: int = 4, spacing: float = 1.12, upper: bool = False) -> Fit:
    """Largest size whose wrapped text fits box_w x box_h in <= max_lines. ok=False only if even min_size can't
    (then the lines are cut with an ellipsis so they still fit)."""
    text = (text or "").strip()
    if upper:
        text = text.upper()
    lo, hi, best = min_size, max(min_size, max_size), None
    while lo <= hi:
        mid = (lo + hi) // 2
        f = font(font_name, mid)
        lines = wrap(text, f, box_w)
        w, h, lh = measure(lines, f, spacing)
        if len(lines) <= max_lines and w <= box_w and h <= box_h:
            best = Fit(mid, lines, lh, w, h, True, font_name, spacing)
            lo = mid + 1
        else:
            hi = mid - 1
    if best:  # wrapping isn't perfectly monotonic in size: probe a few sizes above the binary-search answer
        for sz in range(best.size + 1, min(max_size, best.size + 8) + 1):
            f = font(font_name, sz)
            lines = wrap(text, f, box_w)
            w, h, lh = measure(lines, f, spacing)
            if len(lines) <= max_lines and w <= box_w and h <= box_h:
                best = Fit(sz, lines, lh, w, h, True, font_name, spacing)
        return best
    f = font(font_name, min_size)  # last resort: min size, cut lines with an ellipsis so nothing leaves the box
    lines = wrap(text, f, box_w)
    w, h, lh = measure(lines, f, spacing)
    keep = max(1, min(max_lines, (box_h - line_h(f)) // max(1, lh) + 1))
    if len(lines) > keep:
        lines = lines[:keep]
        last = lines[-1]
        while last and text_w(f, last + "…") > box_w:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    w, h, lh = measure(lines, f, spacing)
    # ok=False flags the degraded result (callers may shorten the copy); the text still never leaves the box
    return Fit(min_size, lines, lh, w, h, False, font_name, spacing, {"truncated": True, "inside": w <= box_w and h <= box_h})


def draw_fit(d: ImageDraw.ImageDraw, fitres: Fit, box: tuple[int, int, int, int], *, fill="white", align: str = "center",
             valign: str = "middle", accent_words: set[str] | None = None, accent="#C9ACFA",
             stroke: int = 0, stroke_fill="black") -> tuple[int, int, int, int]:
    """Draw a Fit inside box (x0, y0, x1, y1). Returns the rectangle actually inked (union of glyph bboxes)."""
    x0, y0, x1, y1 = box
    f = font(fitres.font_name, fitres.size)
    bw, bh = x1 - x0, y1 - y0
    top = y0 if valign == "top" else (y1 - fitres.height if valign == "bottom" else y0 + (bh - fitres.height) // 2)
    acc = {w.upper().strip(",.:!?") for w in (accent_words or set())}
    ink = [10**9, 10**9, -1, -1]
    y = top
    for ln in fitres.lines:
        lw = text_w(f, ln)
        x = x0 if align == "left" else (x1 - lw if align == "right" else x0 + (bw - lw) / 2)
        if acc:
            cx = x
            for wd in ln.split(" "):
                col = accent if wd.upper().strip(",.:!?") in acc else fill
                d.text((cx, y), wd, font=f, fill=col, stroke_width=stroke, stroke_fill=stroke_fill)
                cx += text_w(f, wd + " ")
        else:
            d.text((x, y), ln, font=f, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)
        bb = d.textbbox((x, y), ln, font=f, stroke_width=stroke)
        ink = [min(ink[0], bb[0]), min(ink[1], bb[1]), max(ink[2], bb[2]), max(ink[3], bb[3])]
        y += fitres.line_height
    return tuple(int(v) for v in ink)  # type: ignore[return-value]


def inside(inner, outer) -> bool:
    return inner[0] >= outer[0] and inner[1] >= outer[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def overlaps(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
