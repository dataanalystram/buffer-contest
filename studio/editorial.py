"""editorial_story: the HuhByte "Editorial" carousel as a brand-kit template (owner, 1 Nov 2026: the ernd carousel).

Ported from the owner's HuhByte pipeline (dataanalystram/Huhbyte.ai code/hb.py editorial_cover / editorial_slide /
editorial_follow, docs/DESIGN_SYSTEM.md "Editorial"), drawn on studio.templates.Brand so colours, logo and @handle come
from the person's kit. Condensed headline face: Oswald for a condensed kit (Anton / Oswald), else the kit's own.

  cover     the hero (hero_edit: brand art + the product, tilted, + founder avatars), eyebrow ("YC F26 · AI + FINTECH"),
            a big Title Case headline with ONE accent word, a one-line dek
  idea      eyebrow + 1-2 blocks (big headline, plain body with **bold** key facts, hairline between) + a small grey
            source/terms footnote; optional image band across the top that fades into the ground
  founders  circular photos with a white ring + names, then the idea block
  follow    logo, "Follow", @handle in the accent, the brand line

Every slide: logo top-left, n/total top-right, the spaced brand wordmark + one accent hairline at the bottom. Text is
auto-fitted; ``fit`` reports any block that still overflows (the agent rewrites it shorter, never ships it clipped).
"""
from __future__ import annotations

import contextvars
import re

from PIL import Image, ImageDraw

from . import hero_edit
from . import templates as T

SIZES = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920)}
M = 72
GREY, RULE, BODY = (150, 150, 156), (58, 58, 64), (222, 222, 226)  # the dark kit's tones (what _tones gives for white on black)


def _tones(b: T.Brand) -> dict:
    """Body copy, footnote grey, hairline and wordmark tones from THIS brand's text and ground, so a light kit (dark text
    on white) reads as well as the dark one. White on black gives exactly the old fixed greys."""
    t, g = b.text, b.bg
    return {"body": T.mix(t, g, 0.13), "grey": T.mix(t, g, 0.42), "rule": T.mix(t, g, 0.77), "mark": T.mix(t, g, 0.08)}
MIN_CONTENT = 4  # content slides (not cover / follow) a carousel needs when the sources support it (owner rule)
MAX_UPSCALE = 1.5  # a cover picture is never blown up past this (owner bug, 2 Nov 2026: the ernd cover was a 4x logo);
#                    a smaller picture sits as an inset on the brand ground instead
# Brands whose casing Title Case must not touch (owner bug, 2 Nov 2026: "Ernd"). Words with an inner capital (iPhone,
# xAI, YC) or a digit are already kept as written; these fix the same names when they come in all lowercase.
KNOWN_CASE = {w.lower(): w for w in ("iPhone", "iPad", "iOS", "macOS", "xAI", "OpenAI", "ChatGPT", "GitHub", "YouTube",
                                     "TikTok", "LinkedIn", "eBay", "ernd")}
_COMMON = set("a an the and or but of in on at to for from with by as is are was were be it its this that these those "
               "we you they he she i our your their my new now how why what when who just more most all no not "
               "here there so if then than".split())
_KEEP: contextvars.ContextVar[dict] = contextvars.ContextVar("editorial_keep_case", default=None)


class Fit:
    def __init__(self):
        self.problems: list[str] = []


def _head_file(b: T.Brand) -> str:
    k = (b.kit.get("font") or "anton").lower()
    return "Oswald-SemiBold.ttf" if k in ("anton", "oswald") else b.head_file


def _ed_body(b: T.Brand, size: int, strong: bool = False, semi: bool = False):
    """The editorial body face: Inter for the default kits (Inter / Poppins), else the kit's own body font."""
    if "Inter" in b.body_file or b.body_file.startswith("Poppins"):
        return _font("Inter-SemiBold.ttf" if semi else "Inter-Bold.ttf" if strong else "Inter-Regular.ttf", size)
    return b.body(size, strong=strong or semi)


def _font(file: str, size: int):
    return T.ImageFont.truetype(str(T.FONT_DIR / file), max(8, int(size)))


def keep_words(*texts: str, extra=()) -> dict:
    """Words whose casing is the brand's own: the extra list (spec keep_case / company) plus any word the copy writes in
    lowercase at the START of a sentence ("ernd is the credit card for the AI stack"), which only a stylised name does."""
    out = {}
    for w in extra or ():
        for part in str(w or "").split():
            part = part.strip(".,:;!?\"'()")
            if part:
                out[part.lower()] = part
    for t in texts:
        for m in re.finditer(r"(?:^|[.!?]\s+)([a-z][a-z0-9]*[a-z0-9])\b", str(t or "")):
            if m.group(1) not in _COMMON:
                out.setdefault(m.group(1).lower(), m.group(1))
    return out


def _title(s: str, keep: dict | None = None) -> str:
    s = " ".join((s or "").split())
    small = {"a", "an", "the", "and", "or", "of", "vs"}
    keep = {**KNOWN_CASE, **(_KEEP.get() or {}), **(keep or {})}
    out = []
    for i, w in enumerate(s.split(" ")):
        m = re.match(r"^([^\w]*)(\w[\w'’-]*?)([^\w]*)$", w)
        core = m.group(2).lower() if m else ""
        if m and core in keep:
            out.append(m.group(1) + keep[core] + m.group(3))  # ernd, iPhone, xAI as the brand writes them
        elif any(ch.isupper() for ch in w[1:]) or any(ch.isdigit() for ch in w) or w.startswith(("$", "@", "#")):
            out.append(w)  # keep 4%, $10K, iPhone, YC as written
        elif i and w.lower() in small:
            out.append(w.lower())
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def _norm(w: str) -> str:
    return re.sub(r"[^\w$%.]", "", w).strip(".").lower()


def _track(d, xy, text, f, fill, sp):
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + sp
    return x - sp


def _logo(canvas: Image.Image, b: T.Brand, h: int, xy) -> None:
    if b.logo is None:
        d = ImageDraw.Draw(canvas)
        f = _font(_head_file(b), h)
        d.text(xy, b.name or "", font=f, fill=b.text)
        return
    lg = T.logo_for(b, "plain", int(h * 6), h)  # recoloured to the ink when it would vanish on the ground
    canvas.paste(lg, (int(xy[0]), int(xy[1])), lg if lg.mode == "RGBA" else None)


def _wordmark(canvas, b: T.Brand, y: int, W: int) -> None:
    d = ImageDraw.Draw(canvas)
    f = _ed_body(b, 22, semi=True)
    word = (b.name or b.handle or "").upper()
    x = _track(d, (M, y), word, f, _tones(b)["mark"], 7) + 26
    bb = d.textbbox((0, 0), "H", font=f)
    ry = y + (bb[1] + bb[3]) // 2
    d.line((x, ry, max(x + 40, W - M), ry), fill=b.accent, width=2)


def _counter(d, n, W, y, b: T.Brand | None = None):
    if not n:
        return
    t = f"{n[0]}/{n[1]}"
    f = _font("Inter-Regular.ttf", 28)
    d.text((W - M - d.textlength(t, font=f), y), t, font=f, fill=_tones(b)["grey"] if b is not None else GREY)


def _label(d, b, xy, text):
    return _track(d, xy, (text or "").upper(), _ed_body(b, 26, semi=True), b.text, 3)


def _headline(d, b, text, x, y, size, accent, maxw, draw=True, lh=1.06):
    f = _font(_head_file(b), size)
    acc = {_norm(a) for a in accent or []}
    lines = T.wrap(d, _title(text), f, maxw)
    for ln in lines:
        cx = x
        for wd in ln.split(" "):
            if draw:
                d.text((cx, y - int(size * 0.12)), wd, font=f, fill=b.accent if _norm(wd) in acc else b.text)
            cx += d.textlength(wd + " ", font=f)
        y += int(size * lh)
    return y, len(lines)


def _rich(text: str):
    out, prev_space = [], True
    for i, part in enumerate((text or "").split("**")):
        ws = part.split()
        for j, w in enumerate(ws):
            out.append((w, i % 2 == 1, j == 0 and bool(out) and not prev_space and not part[:1].isspace()))
        if part:
            prev_space = part[-1:].isspace()
    return out


def _body(d, b, text, x, y, size, maxw, draw=True, lh=1.38):
    fr, fb = _ed_body(b, size), _ed_body(b, size, strong=True)
    sp = d.textlength(" ", font=fr)
    lines, w = [[]], 0
    for wd, bold, glue in _rich(text):
        ww = d.textlength(wd, font=fb if bold else fr)
        gap = 0 if (glue or not lines[-1]) else sp
        if lines[-1] and not glue and w + sp + ww > maxw:
            lines.append([])
            w, gap = 0, 0
        w += gap + ww
        lines[-1].append((wd, bold, ww, gap))
    for ln in lines:
        cx = x
        if draw:
            for wd, bold, ww, gap in ln:
                cx += gap
                d.text((cx, y), wd, font=fb if bold else fr, fill=b.text if bold else _tones(b)["body"])
                cx += ww
        y += int(size * lh)
    return y


def _grad(img, y0, y1, a0=0, a1=255, color=(0, 0, 0)):
    W, H = img.size
    g = Image.new("L", (1, H), 0)
    for y in range(H):
        t = 0 if y < y0 else (1 if y >= y1 else (y - y0) / max(1, y1 - y0))
        t = t * t * (3 - 2 * t)
        g.putpixel((0, y), int(a0 + (a1 - a0) * t) if y >= y0 else 0)
    img.paste(Image.new("RGB", (W, H), color), (0, 0), g.resize((W, H)))
    return img


def _top_vignette(img, h=220, a=150, color=(0, 0, 0)):
    W, H = img.size
    g = Image.new("L", (1, H), 0)
    for y in range(min(h, H)):
        g.putpixel((0, y), int(a * (1 - y / h) ** 2))
    img.paste(Image.new("RGB", (W, H), color), (0, 0), g.resize((W, H)))


# ---------------------------------------------------------------------------------------------------- slides
def upscale_needed(im: Image.Image, W: int, H: int) -> float:
    """How much a full-bleed crop to W x H would enlarge this picture."""
    return max(W / max(1, im.width), H / max(1, im.height))


def _inset(canvas: Image.Image, im: Image.Image, top: int, bottom: int) -> None:
    """A small picture centred in [top, bottom] at <= MAX_UPSCALE, rounded corners, on the ground (never pixelated)."""
    W = canvas.width
    bw, bh = W - 2 * M, max(40, bottom - top)
    k = min(MAX_UPSCALE, bw / max(1, im.width), bh / max(1, im.height))
    w, h = max(1, int(im.width * k)), max(1, int(im.height * k))
    pic = im.convert("RGBA").resize((w, h), Image.LANCZOS)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=max(4, min(w, h) // 10), fill=255)
    if pic.mode == "RGBA":
        from PIL import ImageChops
        mask = ImageChops.multiply(mask, pic.getchannel("A"))
    canvas.paste(pic.convert("RGB"), ((W - w) // 2, top + (bh - h) // 2), mask)


def cover(b: T.Brand, hero: Image.Image | None, eyebrow: str, headline: str, dek: str, accent=(), n=None, ratio="4:5",
          fit: Fit | None = None) -> Image.Image:
    W, H = SIZES.get(ratio, SIZES["4:5"])
    inset = hero is not None and upscale_needed(hero, W, H) > MAX_UPSCALE
    s = hero_edit.crop_to(hero, W, H) if hero is not None and not inset else Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(s)
    maxw = W - 2 * M
    size = 118
    while _headline(d, b, headline, M, 0, size, accent, maxw, draw=False)[1] > 3 and size > 60:
        size -= 4
    _, nl = _headline(d, b, headline, M, 0, size, accent, maxw, draw=False)
    if nl > 3 and fit:
        fit.problems.append(f"cover headline needs {nl} lines at the smallest size")
    sf = _ed_body(b, 34)
    sub = T.wrap(d, dek or "", sf, maxw)[:2]
    if dek and len(T.wrap(d, dek, sf, maxw)) > 2 and fit:
        fit.problems.append("cover dek is longer than two lines")
    wm_y = H - 100
    sub_y = wm_y - 56 - len(sub) * 46
    head_h = int(size * 1.06) * nl
    head_y = sub_y - 40 - head_h
    cat_y = head_y - 58
    if inset:
        _inset(s, hero, M + 90, max(M + 190, cat_y - 40))
        if fit:
            fit.problems.append(f"cover picture is small ({hero.width}x{hero.height}); shown as an inset, not upscaled "
                                f"(give a bigger image, e.g. the page's og:image, for a full-bleed cover)")
    _grad(s, max(0, cat_y - 380), cat_y + 120, 0, 252, b.bg)
    _top_vignette(s, color=b.bg)
    d = ImageDraw.Draw(s)
    _logo(s, b, 46, (M, M - 4))
    _counter(d, n, W, M, b)
    _label(d, b, (M, cat_y), eyebrow)
    _headline(d, b, headline, M, head_y, size, accent, maxw)
    y = sub_y
    for ln in sub:
        d.text((M, y), ln, font=sf, fill=_tones(b)["body"])
        y += 46
    _wordmark(s, b, wm_y, W)
    return s


def idea(b: T.Brand, eyebrow: str, blocks: list[dict], n=None, ratio="4:5", accent=(), note: str = "",
         image: Image.Image | None = None, image_h: int | None = None, fit: Fit | None = None) -> Image.Image:
    """blocks: [{head, body}] (1-2). image: a band across the top that fades into the ground."""
    W, H = SIZES.get(ratio, SIZES["4:5"])
    s = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(s)
    maxw = W - 2 * M
    wm_y = H - 100
    floor = wm_y - 70
    y0 = M + 90
    if image is not None:
        ih = image_h or int(H * 0.42)
        s.paste(hero_edit.crop_to(image, W, ih), (0, 0))
        _grad(s, int(ih * 0.55), ih, 0, 255, b.bg)
        _top_vignette(s, h=M + 120, a=170, color=b.bg)
        y0 = ih + 10
    nf = _font("Inter-Regular.ttf", 26)

    def layout(bs, bf, draw, y_start):
        y = y_start
        if draw:
            _label(d, b, (M, y), eyebrow)
        y += 26 + 52
        for i, bl in enumerate(blocks[:3]):
            if i:
                y += 34
                if draw:
                    d.line((M, y, W - M, y), fill=_tones(b)["rule"], width=2)
                y += 64
            y, _ = _headline(d, b, bl.get("head", ""), M, y, bs, accent, maxw, draw=draw)
            y += 40 - int(bs * 0.06)
            if bl.get("body"):
                y = _body(d, b, bl["body"], M, y, bf, maxw, draw=draw)
        if note:
            y += 16
            for ln in T.wrap(d, note, nf, maxw):
                if draw:
                    d.text((M, y), ln, font=nf, fill=_tones(b)["grey"])
                y += 36
        return y
    bs, bf = 124, 40
    while layout(bs, bf, False, y0) > floor and bs > 70:
        bs -= 6
        bf = max(32, bf - 1)
    end = layout(bs, bf, False, y0)
    if end > floor and fit:
        fit.problems.append(f"slide {n[0] if n else '?'} ({eyebrow}) overflows by {end - floor}px")
    y_start = y0 + max(0, (floor - end) // (2 if image is None else 3))
    layout(bs, bf, True, y_start)
    _logo(s, b, 46, (M, M - 4))
    _counter(d, n, W, M, b)
    _wordmark(s, b, wm_y, W)
    return s


def founders_band(photos: list[Image.Image], names: list[str], W: int, b: T.Brand, h: int = 620) -> Image.Image:
    s = Image.new("RGB", (W, h), b.bg)
    if not photos:
        return s
    k = len(photos[:4])
    dd = 200 if k <= 3 else 170
    gap = (W - 2 * M - k * (dd + 12)) // max(1, k - 1) if k > 1 else 0
    x = M if k > 1 else (W - dd) // 2
    d = ImageDraw.Draw(s)
    f = _font("Inter-Regular.ttf", 26)
    for i, p in enumerate(photos[:4]):
        av = hero_edit.avatar(p, dd)
        s.paste(av, (x, 140), av)
        nm = (names[i] if i < len(names) else "").split(" ")[0]
        if nm:
            tw = d.textlength(nm, font=f)
            d.text((x + av.width / 2 - tw / 2, 140 + av.height + 18), nm, font=f, fill=_tones(b)["grey"])
        x += dd + 12 + gap
    return s


def follow(b: T.Brand, n=None, ratio="4:5", line: str = "") -> Image.Image:
    W, H = SIZES.get(ratio, SIZES["4:5"])
    s = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(s)
    _counter(d, n, W, M, b)
    big = int(W * 0.2)
    hf = _font(_head_file(b), big)
    af = _font(_head_file(b), int(big * 0.62))
    lf = _font("Inter-Regular.ttf", 38)
    line = line or b.end_line or (f"More from {b.name}, every week." if b.name else "")
    lh = 110
    block = lh + 90 + int(big * 1.05) + int(big * 0.62 * 1.15) + 110
    y = (H - 100 - block) // 2
    if b.logo is not None:
        lg = T.logo_for(b, "plain", int(W * 0.5), lh)
        s.paste(lg, ((W - lg.width) // 2, y), lg if lg.mode == "RGBA" else None)
    y += lh + 90
    t = "Follow"
    d.text((W / 2 - d.textlength(t, font=hf) / 2, y - int(big * 0.12)), t, font=hf, fill=b.text)
    y += int(big * 1.05)
    t = "@" + (b.handle or "")
    while d.textlength(t, font=af) > W - 2 * M:
        af = _font(_head_file(b), int(af.size * 0.94))
    d.text((W / 2 - d.textlength(t, font=af) / 2, y - int(af.size * 0.12)), t, font=af, fill=b.accent)
    y += int(af.size * 1.15) + 60
    d.line((W / 2 - 60, y - 30, W / 2 + 60, y - 30), fill=_tones(b)["rule"], width=2)
    d.text((W / 2 - d.textlength(line, font=lf) / 2, y), line, font=lf, fill=_tones(b)["body"])
    _wordmark(s, b, H - 100, W)
    return s


# ---------------------------------------------------------------------------------------------------- the carousel
def content_slides(spec: dict) -> int:
    return len([x for x in spec.get("slides") or [] if x.get("blocks")]) + (1 if (spec.get("founders") or {}).get("blocks") else 0)


def check_spec(spec: dict, facts_available: int = 99) -> list[str]:
    """The minimum-content rule (owner, 1 Nov 2026): 5-7 slides of real content, one idea per slide, never hero/info/follow
    only. Returns what's wrong (the agent rewrites); [] when it passes."""
    out = []
    n = content_slides(spec)
    if n < MIN_CONTENT and facts_available >= MIN_CONTENT:
        out.append(f"only {n} content slides; the sources support at least {MIN_CONTENT}: add slides (how it works, why it "
                   "matters, the numbers, the founders)")
    if n + 2 > 7:
        out.append(f"{n + 2} slides; keep it to 7 at most (cover + {n} + follow)")
    for i, sl in enumerate(spec.get("slides") or [], 2):
        bl = sl.get("blocks") or []
        if not bl:
            out.append(f"slide {i} has no content")
        if len(bl) > 2:
            out.append(f"slide {i} carries {len(bl)} ideas; one idea per slide (two short blocks at most)")
        for x in bl:
            if len((x.get("head") or "").split()) > 7:
                out.append(f"slide {i}: headline '{x.get('head')}' is longer than 7 words")
            if len((x.get("body") or "").split()) > 40:
                out.append(f"slide {i}: body is longer than 40 words")
    if len((spec.get("headline") or "").split()) > 10:
        out.append("cover headline is longer than 10 words")
    return out


def render(b: T.Brand, spec: dict, *, hero: Image.Image | None = None, band: Image.Image | None = None,
           founders: list[Image.Image] | None = None, ratio: str = "4:5") -> tuple[list[Image.Image], Fit]:
    """spec: {eyebrow, headline, accent[], dek, slides:[{eyebrow, blocks:[{head, body}], accent[], note, image: bool}],
    founders:{eyebrow, blocks, accent, note, names[]}, follow_line}. hero: the hero_edit cover image; band: a picture for
    an idea slide that asks for one (image: true); founders: photos in the order of founders.names."""
    fit = Fit()
    copy = [spec.get("dek", "")] + [bl.get("body", "") for x in spec.get("slides") or [] for bl in x.get("blocks") or []]
    tok = _KEEP.set(keep_words(*copy, extra=list(spec.get("keep_case") or []) + [spec.get("company") or ""]))
    try:
        return _render(b, spec, hero, band, founders, ratio, fit)
    finally:
        _KEEP.reset(tok)


def _render(b, spec, hero, band, founders, ratio, fit):
    sl = [x for x in spec.get("slides") or [] if x.get("blocks")]
    fo = spec.get("founders") or {}
    total = 1 + len(sl) + (1 if fo.get("blocks") else 0) + 1
    W, _H = SIZES.get(ratio, SIZES["4:5"])
    out = [cover(b, hero, spec.get("eyebrow", ""), spec.get("headline", ""), spec.get("dek", ""), spec.get("accent") or [],
                 (1, total), ratio, fit)]
    k = 2
    for x in sl:
        img = band if (x.get("image") and band is not None) else None
        out.append(idea(b, x.get("eyebrow", ""), x["blocks"], (k, total), ratio, x.get("accent") or [], x.get("note", ""),
                        image=img, image_h=560 if img is not None else None, fit=fit))
        k += 1
    if fo.get("blocks"):
        fb = founders_band(founders or [], fo.get("names") or [], W, b) if founders else None
        out.append(idea(b, fo.get("eyebrow", "The founders"), fo["blocks"], (k, total), ratio, fo.get("accent") or [],
                        fo.get("note", ""), image=fb, image_h=620 if fb is not None else None, fit=fit))
        k += 1
    out.append(follow(b, (total, total), ratio, spec.get("follow_line", "")))
    return out, fit


def contact_sheet(slides: list[Image.Image], cols: int = 3, w: int = 540, title: str = "") -> Image.Image:
    h = int(w * slides[0].height / slides[0].width)
    rows = (len(slides) + cols - 1) // cols
    top = 50 if title else 0
    out = Image.new("RGB", (cols * w + (cols + 1) * 16, top + rows * h + (rows + 1) * 16), (30, 30, 32))
    d = ImageDraw.Draw(out)
    if title:
        d.text((16, 16), title, fill=(220, 220, 225), font=_font("Inter-Bold.ttf", 22))
    for i, s in enumerate(slides):
        r, c = divmod(i, cols)
        out.paste(s.resize((w, h), Image.LANCZOS), (16 + c * (w + 16), top + 16 + r * (h + 16)))
    return out
