"""A small, dependency-free SVG rasteriser for brand logos (header wordmarks, SVG favicons), on Pillow.

Pillow can't read SVG and the hosted app has no cairo, so brand onboarding draws the logo itself. Logos are almost always
plain filled shapes, so this covers exactly that: <path> (M L H V C S Q T A Z, absolute and relative), <rect>, <circle>,
<ellipse>, <polygon>, <polyline>, nested <g>, and transform (matrix, translate, scale, rotate) on any of them. Curves are
flattened to polygons and every closed contour is XOR-ed into the mask (the even-odd rule), which also cuts the holes of
letters drawn with the non-zero rule the usual way (outer and inner contours wound opposite). Masks, clip paths, <defs>,
gradients, text and filters are skipped. Drawn at 4x and downsampled, so edges are smooth.

Returns an RGBA image tight to what was drawn, or None when nothing drawable was found. Never fetches anything (an SVG's
<image href> and external references are ignored), and caps the work so a hostile file can't burn the CPU.
"""
from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from PIL import Image, ImageChops, ImageDraw

MAX_BYTES = 400_000
MAX_SHAPES = 600
MAX_POINTS = 120_000
SKIP = {"defs", "mask", "clippath", "lineargradient", "radialgradient", "pattern", "filter", "symbol", "style", "script",
        "title", "desc", "metadata", "text", "image", "foreignobject", "marker"}
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)")


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1].lower() if isinstance(el.tag, str) else ""


def _f(v, default=0.0) -> float:
    try:
        m = _NUM.match(str(v).strip())
        return float(m.group(0)) if m else default
    except (TypeError, ValueError):
        return default


# ------------------------------------------------------------------------------------------------ transforms
def _mul(a, b):
    """3x2 affine matrices as (a, b, c, d, e, f): a*b means apply b first, then a."""
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1], a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


IDENT = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(s: str | None):
    m = IDENT
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in _NUM.findall(args)]
        if name == "matrix" and len(v) == 6:
            t = tuple(v)
        elif name == "translate" and v:
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            r = math.radians(v[0])
            t = (math.cos(r), math.sin(r), -math.sin(r), math.cos(r), 0, 0)
            if len(v) == 3:
                t = _mul(_mul((1, 0, 0, 1, v[1], v[2]), t), (1, 0, 0, 1, -v[1], -v[2]))
        elif name == "skewX" and v:
            t = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            t = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mul(m, t)
    return m


def _apply(m, pts):
    return [(m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]) for x, y in pts]


# ------------------------------------------------------------------------------------------------ path flattening
def _cubic(p0, p1, p2, p3, n=12):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _quad(p0, p1, p2, n=10):
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0], u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _arc(p0, rx, ry, phi, large, sweep, p1):
    """SVG endpoint arc -> points (the spec's F.6.5 conversion to centre form)."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1 * y1 - ry * ry * x1 * x1
    den = rx * rx * y1 * y1 + ry * ry * x1 * x1
    k = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        k = -k
    cx1, cy1 = k * rx * y1 / ry, -k * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a
    t1 = ang(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    dt = ang((x1 - cx1) / rx, (y1 - cy1) / ry, (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = max(4, int(abs(dt) / (math.pi / 16)))
    out = []
    for i in range(1, n + 1):
        t = t1 + dt * i / n
        x, y = rx * math.cos(t), ry * math.sin(t)
        out.append((c * x - s * y + cx, s * x + c * y + cy))
    return out


def path_contours(d: str) -> list[list[tuple[float, float]]]:
    contours: list[list] = []
    cur: list = []
    x = y = sx = sy = 0.0
    last_c = last_q = None
    for cmd, argstr in _CMD.findall(d or ""):
        nums = [float(n) for n in _NUM.findall(argstr)]
        rel = cmd.islower()
        C = cmd.upper()
        if C == "Z":
            if cur:
                contours.append(cur)
            cur = []
            x, y = sx, sy
            last_c = last_q = None
            continue
        size = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}[C]
        if not nums:
            continue
        for i in range(0, len(nums) - size + 1, size):
            a = nums[i:i + size]
            if C == "M":
                if cur:
                    contours.append(cur)
                x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                sx, sy = x, y
                cur = [(x, y)]
                C = "L"  # further pairs after M are line-tos
                last_c = last_q = None
                continue
            if not cur:
                cur = [(x, y)]
            if C == "L":
                x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                cur.append((x, y))
                last_c = last_q = None
            elif C == "H":
                x = x + a[0] if rel else a[0]
                cur.append((x, y))
                last_c = last_q = None
            elif C == "V":
                y = y + a[0] if rel else a[0]
                cur.append((x, y))
                last_c = last_q = None
            elif C == "C":
                p1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                p2 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                p3 = (x + a[4], y + a[5]) if rel else (a[4], a[5])
                cur += _cubic((x, y), p1, p2, p3)
                last_c, last_q = p2, None
                x, y = p3
            elif C == "S":
                p1 = (2 * x - last_c[0], 2 * y - last_c[1]) if last_c else (x, y)
                p2 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                p3 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                cur += _cubic((x, y), p1, p2, p3)
                last_c, last_q = p2, None
                x, y = p3
            elif C == "Q":
                p1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                p2 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                cur += _quad((x, y), p1, p2)
                last_q, last_c = p1, None
                x, y = p2
            elif C == "T":
                p1 = (2 * x - last_q[0], 2 * y - last_q[1]) if last_q else (x, y)
                p2 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                cur += _quad((x, y), p1, p2)
                last_q, last_c = p1, None
                x, y = p2
            elif C == "A":
                p1 = (x + a[5], y + a[6]) if rel else (a[5], a[6])
                cur += _arc((x, y), a[0], a[1], a[2], int(a[3]), int(a[4]), p1)
                last_c = last_q = None
                x, y = p1
    if cur:
        contours.append(cur)
    return [c for c in contours if len(c) >= 3]


def _ellipse(cx, cy, rx, ry, n=48):
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _style(el) -> dict:
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip()
    for k in ("fill", "opacity", "fill-opacity", "display", "visibility", "stroke", "stroke-width"):
        if el.get(k) is not None and k not in out:
            out[k] = el.get(k)
    return out


def _visible_fill(st: dict, inherited: str) -> str | None:
    if st.get("display") == "none" or st.get("visibility") == "hidden":
        return None
    fill = (st.get("fill") or inherited or "black").strip()
    if fill.lower() in ("none", "transparent") or fill.startswith("url("):
        return None
    try:
        if float(st.get("opacity", 1)) < 0.05 or float(st.get("fill-opacity", 1)) < 0.05:
            return None
    except ValueError:
        pass
    return fill


def shapes(svg_text: str) -> tuple[list[tuple[list, str]], tuple[float, float, float, float]]:
    """[(contours in user space, fill), ...] and the viewBox (x, y, w, h)."""
    if not svg_text or len(svg_text) > MAX_BYTES or "<!ENTITY" in svg_text:
        return [], (0, 0, 0, 0)
    txt = re.sub(r"<!DOCTYPE[^>]*>", "", svg_text)
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return [], (0, 0, 0, 0)
    if _tag(root) != "svg":
        root = next((e for e in root.iter() if _tag(e) == "svg"), None)
        if root is None:
            return [], (0, 0, 0, 0)
    vb = [float(v) for v in _NUM.findall(root.get("viewBox") or root.get("viewbox") or "")]
    if len(vb) != 4 or vb[2] <= 0 or vb[3] <= 0:
        w, h = _f(root.get("width"), 0), _f(root.get("height"), 0)
        vb = [0, 0, w or 0, h or 0]
    out: list = []
    budget = {"shapes": 0, "points": 0}

    def walk(el, m, fill):
        for ch in list(el):
            t = _tag(ch)
            if t in SKIP or budget["shapes"] >= MAX_SHAPES:
                continue
            st = _style(ch)
            mm = _mul(m, parse_transform(ch.get("transform")))
            f = st.get("fill") or fill
            if t in ("g", "a", "svg", "switch"):
                if st.get("display") != "none":
                    walk(ch, mm, f)
                continue
            vis = _visible_fill(st, fill)
            if vis is None and not st.get("fill") and ch.get("class") and (fill or "").strip().lower() == "none":
                vis = "currentColor"  # fill set by the page's CSS class (fill="none" on the root): draw it in the ink
                budget["css"] = budget.get("css", 0) + 1
            if vis is None:
                continue
            cs: list = []
            if t == "path":
                cs = path_contours(ch.get("d") or "")
            elif t == "rect":
                x, y, w, h = (_f(ch.get(k)) for k in ("x", "y", "width", "height"))
                if w > 0 and h > 0:
                    rx = _f(ch.get("rx"), 0) or _f(ch.get("ry"), 0)
                    if rx:
                        rx = min(rx, w / 2, h / 2)
                        pts = []
                        for cx, cy, a0 in ((x + w - rx, y + rx, -90), (x + w - rx, y + h - rx, 0), (x + rx, y + h - rx, 90), (x + rx, y + rx, 180)):
                            pts += [(cx + rx * math.cos(math.radians(a0 + 90 * i / 8)), cy + rx * math.sin(math.radians(a0 + 90 * i / 8)))
                                    for i in range(9)]
                        cs = [pts]
                    else:
                        cs = [[(x, y), (x + w, y), (x + w, y + h), (x, y + h)]]
            elif t == "circle":
                r = _f(ch.get("r"))
                if r > 0:
                    cs = [_ellipse(_f(ch.get("cx")), _f(ch.get("cy")), r, r)]
            elif t == "ellipse":
                rx, ry = _f(ch.get("rx")), _f(ch.get("ry"))
                if rx > 0 and ry > 0:
                    cs = [_ellipse(_f(ch.get("cx")), _f(ch.get("cy")), rx, ry)]
            elif t in ("polygon", "polyline"):
                v = [float(n) for n in _NUM.findall(ch.get("points") or "")]
                pts = list(zip(v[0::2], v[1::2]))
                if len(pts) >= 3:
                    cs = [pts]
            if cs:
                budget["shapes"] += 1
                budget["points"] += sum(len(c) for c in cs)
                if budget["points"] > MAX_POINTS:
                    return
                out.append(([_apply(mm, c) for c in cs], vis))
    walk(root, IDENT, (root.get("fill") or _style(root).get("fill") or "black"))
    _LAST["css_fills"] = budget.get("css", 0)
    return out, tuple(vb)


_LAST: dict = {}


def css_filled(svg_text: str) -> bool:
    """True when most shapes take their colour from the page's CSS (a multi-colour mark we can only draw in one ink)."""
    sh, _ = shapes(svg_text)
    return bool(sh) and _LAST.get("css_fills", 0) * 2 > len(sh)


def rasterize(svg_text: str, height: int = 240, color: tuple[int, int, int] | None = None,
              current: tuple[int, int, int] = (17, 17, 20)) -> Image.Image | None:
    """The SVG as an RGBA image `height` px tall (width follows the drawing). color: draw every shape in one colour (a
    one-colour mark recoloured to the brand's ink); else each shape keeps its own fill (currentColor -> `current`)."""
    sh, vb = shapes(svg_text)
    if not sh:
        return None
    xs = [p[0] for cs, _ in sh for c in cs for p in c]
    ys = [p[1] for cs, _ in sh for c in cs for p in c]
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    if vb[2] > 0 and vb[3] > 0:  # clamp to the viewBox (anything outside it is not shown by a browser either)
        x0, y0 = max(x0, vb[0]), max(y0, vb[1])
        x1, y1 = min(x1, vb[0] + vb[2]), min(y1, vb[1] + vb[3])
    if x1 - x0 <= 0 or y1 - y0 <= 0:
        return None
    ss = 4
    k = height * ss / (y1 - y0)
    W = max(1, int(math.ceil((x1 - x0) * k)))
    H = height * ss
    if W > 8000 * ss // 4:
        k = (8000 * ss // 4) / (x1 - x0)
        W, H = int((x1 - x0) * k), max(1, int((y1 - y0) * k))
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for cs, fill in sh:
        mask = Image.new("1", (W, H), 0)
        for c in cs:
            pts = [((px - x0) * k, (py - y0) * k) for px, py in c]
            one = Image.new("1", (W, H), 0)
            ImageDraw.Draw(one).polygon(pts, fill=1)
            mask = ImageChops.logical_xor(mask, one)
        rgb = color or parse_color(fill, current)
        if rgb is None:
            continue
        layer = Image.new("RGBA", (W, H), rgb + (255,))
        canvas.paste(layer, (0, 0), mask.convert("L"))
    im = canvas.resize((max(1, W // ss), max(1, H // ss)), Image.LANCZOS)
    box = im.getchannel("A").point(lambda v: 255 if v > 16 else 0).getbbox()
    return im.crop(box) if box else None


_NAMED = {"black": (0, 0, 0), "white": (255, 255, 255), "red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 128, 0)}


def parse_color(v: str, current=(17, 17, 20)) -> tuple[int, int, int] | None:
    v = (v or "").strip().lower()
    if v in ("currentcolor", "inherit", ""):
        return current
    if v in _NAMED:
        return _NAMED[v]
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h[:3])
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    m = re.fullmatch(r"rgba?\(([^)]*)\)", v)
    if m:
        n = [float(x) for x in _NUM.findall(m.group(1))][:3]
        if len(n) == 3:
            return tuple(int(max(0, min(255, x))) for x in n)  # type: ignore[return-value]
    return current
