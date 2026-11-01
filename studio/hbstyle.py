"""The HuhByte editorial look, brand-driven: hero photo band, circular inset, divider, auto-fit Anton headline,
accent-dot fact slides, a post (tweet) card, Reel frames with the headline on every frame, and an end card that
never clips.

Ported from the owner's own pipeline (dataanalystram/Huhbyte.ai, code/hb.py: cover_slide, text_slide, facts_slide,
reel_overlay_png, end_card_png; same owner, credited in NOTICE) and generalised: every colour, font, handle, logo and
end-card line comes from the brand kit and the Style Ledger, so any brand renders in its own style.

Rule of this module: text is never clipped. Every block of text goes through fit(), which shrinks the font until the
block fits its box (width AND height), hard-wraps a word that is wider than the box on its own, and only as a last
resort ends the block with an ellipsis. Every drawn text box is recorded in p["_boxes"], so tests (and brand_check) can
prove that nothing leaves the canvas.
"""
from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .brand import BODY_FONTS, FONTS, hex_to_rgb

SIZES = {"4:5": (1080, 1350), "9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
# Instagram/TikTok/YouTube UI zones on a 9:16 frame (top status/header, bottom caption/buttons, right action rail)
REEL_TOP_SAFE, REEL_BOTTOM_SAFE, REEL_RIGHT_SAFE = 150, 330, 140


def _r():
    from . import render  # late import: render imports this module
    return render


def _fonts(p: dict) -> tuple[str, str, str]:
    key = p.get("font") or "anton"
    head = FONTS.get(key, "Anton.ttf")
    body = BODY_FONTS.get(key, "Inter-SemiBold.ttf")
    strong = {"Inter-SemiBold.ttf": "Inter-Bold.ttf", "Poppins-Medium.ttf": "Poppins-Bold.ttf", "Inter-Regular.ttf": "Inter-SemiBold.ttf"}.get(body, body)
    return head, body, strong


def colors(p: dict):
    c = p.get("colors") or {}
    return (hex_to_rgb(c.get("primary") or "#111114"), hex_to_rgb(c.get("accent") or "#C9ACFA"),
            hex_to_rgb(c.get("text") or "#FFFFFF"))


def _mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


# ------------------------------------------------------------------ text that always fits
def _hard_wrap(d, word: str, f, maxw: int) -> list[str]:
    out, cur = [], ""
    for ch in word:
        if cur and d.textlength(cur + ch, font=f) > maxw:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def wrap(d, text: str, f, maxw: int) -> list[str]:
    R = _r()
    words = R.drawable(text, f).split()
    lines, cur = [], ""
    for w in words:
        if d.textlength(w, font=f) > maxw:  # one word wider than the box: break it rather than overflow
            if cur:
                lines.append(cur)
                cur = ""
            parts = _hard_wrap(d, w, f, maxw)
            lines += parts[:-1]
            cur = parts[-1]
            continue
        t = (cur + " " + w).strip()
        if not cur or d.textlength(t, font=f) <= maxw:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit(d, text: str, font_file: str, maxw: int, maxh: int, start: int, minimum: int = 22, lh: float = 1.1,
        max_lines: int | None = None):
    """(font, lines, line_height_px). Shrinks until the block fits maxw x maxh (and max_lines); never wider than maxw."""
    R = _r()
    size = max(minimum, int(start))
    while True:
        f = R.font(font_file, size)
        lines = wrap(d, text, f, maxw)
        step = int(size * lh)
        ok_lines = max_lines is None or len(lines) <= max_lines
        if (ok_lines and step * len(lines) <= maxh) or size <= minimum:
            break
        size = max(minimum, int(size * 0.93))
    # last resort at the minimum size: keep what fits and end with an ellipsis (still inside the box)
    cap = max(1, min(len(lines), maxh // max(1, step), max_lines or 999))
    if len(lines) > cap:
        lines = lines[:cap]
        last = lines[-1]
        while last and d.textlength(last + "…", font=f) > maxw:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return f, lines, step


def _text(d, xy, text, f, fill, p, anchor_w: int | None = None):
    """Draw one line and record its real pixel box in p['_boxes']."""
    R = _r()
    t = R.drawable(text, f)
    x, y = xy
    if anchor_w is not None:  # centre inside [x, x+anchor_w]
        x = x + (anchor_w - d.textlength(t, font=f)) / 2
    d.text((x, y), t, font=f, fill=fill)
    bb = d.textbbox((x, y), t, font=f)
    p.setdefault("_boxes", []).append([int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])])
    return bb


def _hi_words(text: str, extra=()) -> set[str]:
    """Words drawn in the accent colour: numbers, money, percentages, plus any the story marked."""
    out = {w.upper().strip(",.:;!?") for w in extra}
    for w in text.split():
        if re.search(r"[\d$€£%]", w):
            out.add(w.upper().strip(",.:;!?"))
    return out


def headline_block(d, text: str, box: tuple[int, int, int, int], p: dict, *, start: int, minimum: int = 40,
                   align: str = "left", hi=(), max_lines: int = 5, first_line_accent: bool = False, valign: str = "top") -> int:
    """Draw a headline inside box=(x, y, w, h), auto-fit; returns the y under the last line."""
    head_f, _, _ = _fonts(p)
    prim, acc, txt = colors(p)
    x, y, w, h = box
    lh = 1.04 if (p.get("font") or "anton") == "anton" else 1.12
    f, lines, step = fit(d, text, head_f, w, h, start, minimum, lh, max_lines)
    hiw = _hi_words(text, hi)
    total = step * len(lines)
    if valign == "bottom":
        y = y + h - total
    elif valign == "middle":
        y = y + (h - total) // 2
    R = _r()
    for i, ln in enumerate(lines):
        lx = x if align == "left" else x + (w - d.textlength(R.drawable(ln, f), font=f)) / 2
        words = ln.split(" ")
        if first_line_accent and not hiw:
            _text(d, (lx, y), ln, f, acc if (i == 0 and len(lines) > 1) else txt, p)
        else:
            cx = lx
            x0 = cx
            for j, wd in enumerate(words):
                col = acc if wd.upper().strip(",.:;!?") in hiw else txt
                piece = wd + (" " if j < len(words) - 1 else "")
                dt = R.drawable(piece, f)
                d.text((cx, y), dt, font=f, fill=col)
                cx += d.textlength(dt, font=f)
            bb = d.textbbox((x0, y), R.drawable(ln, f), font=f)
            p.setdefault("_boxes", []).append([int(bb[0]), int(bb[1]), int(bb[2]), int(bb[3])])
        y += step
    return y


# ------------------------------------------------------------------ pieces
def badge(d, txt: str, W: int, p: dict, side: str = "r", y: int = 40):
    _, _, strong = _fonts(p)
    f = _r().font(strong, max(22, int(W * 0.03)))
    tw = d.textlength(txt, font=f)
    hgt = int(f.size * 1.75)
    x1 = W - 56 if side == "r" else 56 + tw + 36
    d.rounded_rectangle((x1 - tw - 36, y, x1, y + hgt), hgt // 2, fill=(30, 30, 30, 200))
    _text(d, (x1 - tw - 18, y + (hgt - f.size) // 2 - 2), txt, f, (255, 255, 255), p)


def divider(canvas, d, y: int, label: str, p: dict, logo_img=None):
    """Two rules in the text colour with the logo (or a label pill) in the middle: hb.py's divider, brand-driven."""
    W = canvas.width
    prim, acc, txt = colors(p)
    _, _, strong = _fonts(p)
    if logo_img is not None:
        lg = logo_img.copy()
        lg.thumbnail((int(W * 0.24), 64), Image.LANCZOS)
        cx0 = (W - lg.width) // 2
        canvas.paste(lg, (cx0, y - lg.height // 2), lg)
        lw = lg.width
    elif label:
        f = _r().font(strong, max(20, int(W * 0.026)))
        lt = label.upper()
        tw = int(d.textlength(lt, font=f))
        ph = int(f.size * 1.9)
        cx0 = (W - tw - 40) // 2
        d.rounded_rectangle((cx0, y - ph // 2, cx0 + tw + 40, y + ph // 2), ph // 2, fill=acc)
        _text(d, (cx0 + 20, y - f.size // 2 - 3), lt, f, prim, p)
        lw = tw + 40
    else:
        lw = 0
    gap = 24 if lw else 0
    d.line((0, y, W / 2 - lw / 2 - gap, y), fill=txt, width=3)
    d.line((W / 2 + lw / 2 + gap, y, W, y), fill=txt, width=3)


def circle(canvas, img: Image.Image, D: int, xy, ring) -> None:
    m = Image.new("L", (D, D), 0)
    ImageDraw.Draw(m).ellipse((0, 0, D - 1, D - 1), fill=255)
    rg = Image.new("RGBA", (D + 16, D + 16), (0, 0, 0, 0))
    ImageDraw.Draw(rg).ellipse((0, 0, D + 15, D + 15), fill=ring + (255,))
    canvas.paste(rg, (xy[0] - 8, xy[1] - 8), rg)
    canvas.paste(_r().cover(img, D, D, 0.5, 0.35), xy, m)


def fade_into(canvas, band_h: int, color, start: float = 0.62) -> None:
    W = canvas.width
    g = Image.new("L", (1, band_h))
    for yy in range(band_h):
        t = max(0.0, (yy / band_h - start) / (1 - start))
        g.putpixel((0, yy), int(255 * min(1.0, t ** 1.2)))
    canvas.paste(Image.new("RGB", (W, band_h), color), (0, 0), g.resize((W, band_h)))


def _open(b: bytes | None) -> Image.Image | None:
    if not b:
        return None
    try:
        return ImageOps.exif_transpose(Image.open(io.BytesIO(b))).convert("RGB")
    except Exception:
        return None


def _placeholder_band(W: int, h: int, p: dict, word: str = "") -> Image.Image:
    """No photo: a brand-coloured band with a soft accent glow, so the cover still has a top half."""
    prim, acc, txt = colors(p)
    band = Image.new("RGB", (W, h), _mix(prim, acc, 0.08))
    glow = Image.new("L", (W, h), 0)
    ImageDraw.Draw(glow).ellipse((int(W * 0.15), int(h * 0.05), int(W * 0.85), int(h * 1.1)), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(int(W * 0.12)))
    band.paste(Image.new("RGB", (W, h), _mix(prim, acc, 0.45)), (0, 0), glow)
    return band


def footer(canvas, d, p: dict, kit: dict, credit: str = "", swipe: bool = False, bottom_safe: int = 0):
    W, H = canvas.size
    prim, acc, txt = colors(p)
    _, body, strong = _fonts(p)
    m = int(p.get("safe_margin", 64))
    f = _r().font(body, max(20, int(W * 0.026)))
    y = H - m - f.size - bottom_safe
    if credit and p.get("source_credit", True):
        maxw = int(W * 0.55)
        ct = f"Source: {credit}"
        while d.textlength(ct, font=f) > maxw and len(ct) > 12:
            ct = ct[:-2].rstrip() + "…" if not ct.endswith("…") else ct[:-2].rstrip() + "…"
        _text(d, (m, y), ct, f, txt, p)
        p["_credit"] = ct
    if swipe:
        sf = _r().font(strong, max(20, int(W * 0.026)))
        sx = W - m - 70 - d.textlength("SWIPE", font=sf)
        _text(d, (sx, y), "SWIPE", sf, acc, p)
        ax, ay = W - m - 52, y + sf.size // 2 + 2
        d.line((ax, ay, ax + 36, ay), fill=acc, width=4)
        d.polygon([(ax + 46, ay), (ax + 32, ay - 10), (ax + 32, ay + 10)], fill=acc)
    else:
        handle = (kit.get("handle") or "").lstrip("@")
        if handle:
            ht = "@" + handle
            _text(d, (W - m - d.textlength(ht, font=f), y), ht, f, acc, p)


# ------------------------------------------------------------------ slides
def cover(headline: str, kit: dict, p: dict, *, image: bytes | None = None, inset: bytes | None = None, logo: bytes | None = None,
          ratio: str = "4:5", label: str = "", credit: str = "", n: tuple[int, int] | None = None, hi=()):
    """hb.py cover_slide: hero photo band on top, fade into the ground, optional circular inset (a face, the product),
    divider with the label, the headline auto-fit underneath, footer with source + handle (or SWIPE on a carousel).
    The headline never sits on the photo, so text burned into a photo can't collide with it."""
    R = _r()
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = colors(p)
    p["_boxes"] = []
    canvas = Image.new("RGB", (W, H), prim)
    band_h = int(H * (0.52 if ratio == "9:16" else 0.56 if ratio == "4:5" else 0.5))
    top = REEL_TOP_SAFE if ratio == "9:16" else 0
    hero = _open(image)
    if hero is not None:
        canvas.paste(R.cover(hero, W, band_h, 0.5, 0.3), (0, top))
        fade = Image.new("RGB", (W, band_h), prim)
        g = Image.new("L", (1, band_h))
        for yy in range(band_h):
            t = max(0.0, (yy / band_h - 0.66) / 0.34)
            g.putpixel((0, yy), int(255 * min(1.0, t)))
        canvas.paste(fade, (0, top), g.resize((W, band_h)))
    else:
        canvas.paste(_placeholder_band(W, band_h, p), (0, top))
    if top:
        canvas.paste(Image.new("RGB", (W, top), prim), (0, 0))
    ins = _open(inset)
    if ins is not None:
        D = int(W * 0.3)
        circle(canvas, ins, D, (int(W * 0.045), top + int(H * 0.08)), txt)
    d = ImageDraw.Draw(canvas, "RGBA")
    if n:
        badge(d, f"{n[0]}/{n[1]}", W, p, side="l" if p.get("logo_position", "tr") in ("tr", "br") else "r",
              y=top + 40 if top else 40)
    div_y = top + band_h - 8
    divider(canvas, d, div_y, label or "", p)
    m = int(p.get("safe_margin", 64))
    bottom_safe = REEL_BOTTOM_SAFE - 120 if ratio == "9:16" else 0
    foot = int(W * 0.026) + m + 28 + bottom_safe
    head = R.shape_headline(headline, p)
    box = (m, div_y + 56, W - 2 * m, H - (div_y + 56) - foot)
    headline_block(d, head, box, p, start=int(W * (0.095 if p.get("font", "anton") == "anton" else 0.075)), minimum=34,
                   align="center", hi=hi, max_lines=5, first_line_accent=True, valign="middle")
    footer(canvas, d, p, kit, credit, swipe=bool(n and n[0] < n[1]), bottom_safe=bottom_safe)
    has_logo = R.place_logo(canvas, R.load_logo(logo), p, kit)
    return canvas, {"headline": head, "has_logo": has_logo, "credit": p.pop("_credit", ""), "text_boxes": p.pop("_boxes", [])}


def facts(title: str, bullets: list[str], kit: dict, p: dict, *, image: bytes | None = None, logo: bytes | None = None,
          ratio: str = "4:5", n: tuple[int, int] | None = None, headline: str = ""):
    """hb.py facts_slide: Anton title, accent-dot bullets in plain words, an image panel below when there is room.
    On a Reel frame (headline given) the story headline stays on top, so every frame says what the story is."""
    R = _r()
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = colors(p)
    head_f, body_f, _ = _fonts(p)
    p["_boxes"] = []
    canvas = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(canvas, "RGBA")
    m = int(p.get("safe_margin", 64))
    top = REEL_TOP_SAFE if ratio == "9:16" else int(H * 0.1)
    bottom_safe = REEL_BOTTOM_SAFE - 120 if ratio == "9:16" else 0
    if n:
        badge(d, f"{n[0]}/{n[1]}", W, p, side="l" if p.get("logo_position", "tr") in ("tr", "br") else "r")
    y = top
    if headline:
        y = headline_block(d, R.shape_headline(headline, p), (m, y, W - 2 * m, int(H * 0.2)), p, start=int(W * 0.07), minimum=30,
                           max_lines=3, first_line_accent=True) + 18
        d.line((m, y, m + 120, y), fill=acc, width=5)
        y += 36
    t = R.shape_headline(title, {**p, "headline_max_words": 8})
    tf, tlines, tstep = fit(d, t, head_f, W - 2 * m, int(H * 0.14), int(W * 0.065), 30, 1.08, 2)
    for ln in tlines:
        _text(d, (m, y), ln, tf, acc, p)
        y += tstep
    y += int(H * 0.025)
    foot = int(W * 0.026) + m + 40 + bottom_safe
    img = _open(image)
    room = H - foot - y
    text_h = int(room * (0.58 if img is not None else 1.0))
    items = [b for b in bullets if str(b).strip()][:4]
    size = int(W * 0.042)
    while True:  # one size for every bullet, small enough that all of them fit
        bf = R.font(body_f, size)
        step = int(size * 1.34)
        blocks = [wrap(d, b, bf, W - 2 * m - 44) for b in items]
        need = sum(step * len(bl) + int(size * 0.55) for bl in blocks)
        if need <= text_h or size <= 24:
            break
        size = int(size * 0.93)
    for bl in blocks:
        if y + step > H - foot:
            break
        d.ellipse((m, y + size * 0.38, m + size * 0.42, y + size * 0.8), fill=acc)
        for ln in bl:
            if y + step > H - foot:
                break
            _text(d, (m + 44, y), ln, bf, txt, p)
            y += step
        y += int(size * 0.55)
    if img is not None and H - foot - y > 200:
        ih = H - foot - y - 10
        pic = R.cover(img, W - 2 * m, ih, 0.5, 0.4)
        mk = Image.new("L", pic.size, 0)
        ImageDraw.Draw(mk).rounded_rectangle((0, 0, pic.width - 1, pic.height - 1), 28, fill=255)
        canvas.paste(pic, (m, y + 10), mk)
    footer(canvas, d, p, kit, "", swipe=False, bottom_safe=bottom_safe)
    has_logo = R.place_logo(canvas, R.load_logo(logo), p, kit)
    return canvas, {"headline": headline or t, "has_logo": has_logo, "credit": "x", "text_boxes": p.pop("_boxes", [])}


def post_card(post: dict, kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5", headline: str = "",
              n: tuple[int, int] | None = None):
    """A social post (an X post read through oEmbed) as a brand-styled quote card: author, @handle, the post's own
    words auto-fit, the date. With a headline (Reel frames) the story headline sits above the card."""
    R = _r()
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = colors(p)
    head_f, body_f, strong = _fonts(p)
    p["_boxes"] = []
    canvas = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(canvas, "RGBA")
    m = int(p.get("safe_margin", 64))
    top = REEL_TOP_SAFE if ratio == "9:16" else int(H * 0.09)
    bottom_safe = REEL_BOTTOM_SAFE - 120 if ratio == "9:16" else 0
    if n:
        badge(d, f"{n[0]}/{n[1]}", W, p, side="l" if p.get("logo_position", "tr") in ("tr", "br") else "r")
    y = top
    if headline:
        y = headline_block(d, R.shape_headline(headline, p), (m, y, W - 2 * m, int(H * 0.22)), p, start=int(W * 0.075), minimum=32,
                           max_lines=3, first_line_accent=True) + 34
    foot = int(W * 0.026) + m + 40 + bottom_safe
    avail = H - foot - y
    card = _mix(prim, txt, 0.07)
    cw = W - 2 * m - 60
    nf = R.font(strong, max(24, int(W * 0.04)))
    hf = R.font(body_f, max(20, int(W * 0.03)))
    av = int(nf.size * 2.2)
    date_h = hf.size + 30
    body = (post.get("text") or "").strip()
    bf, blines, bstep = fit(d, body, body_f, cw, avail - 34 - av - 30 - date_h - 30, int(W * 0.066), 24, 1.3)
    card_h = min(avail, 34 + av + 30 + bstep * len(blines) + 20 + date_h + 10)
    y = y + (avail - card_h) // 3  # sit a little above the middle of the space
    d.rounded_rectangle((m - 12, y, W - m + 12, y + card_h), 36, fill=card + (255,), outline=_mix(prim, acc, 0.55), width=3)
    cx, cy = m + 30, y + 34
    name = (post.get("author_name") or "").strip()[:48] or "Post"
    d.ellipse((cx, cy, cx + av, cy + av), fill=acc)
    init = R.drawable((name[:1] or "?").upper(), nf)
    _text(d, (cx + (av - d.textlength(init, font=nf)) / 2, cy + (av - nf.size) / 2 - 4), init, nf, prim, p)
    nx = cx + av + 22
    nm = name
    while d.textlength(nm, font=nf) > cw - av - 140 and len(nm) > 4:
        nm = nm[:-2] + "…"
    _text(d, (nx, cy + 2), nm, nf, txt, p)
    handle = ("@" + post["handle"]) if post.get("handle") else ""
    if handle:
        _text(d, (nx, cy + nf.size + 12), handle, hf, _mix(txt, prim, 0.35), p)
    xf = R.font(head_f, int(nf.size * 1.1))
    _text(d, (cx + cw - d.textlength("X", font=xf), cy), "X", xf, txt, p)
    ty = cy + av + 30
    for ln in blines:
        _text(d, (cx, ty), ln, bf, txt, p)
        ty += bstep
    if post.get("date"):
        _text(d, (cx, y + card_h - date_h), post["date"], hf, _mix(txt, prim, 0.4), p)
    footer(canvas, d, p, kit, post.get("credit") or "X", swipe=bool(n and n[0] < n[1]), bottom_safe=bottom_safe)
    has_logo = R.place_logo(canvas, R.load_logo(logo), p, kit)
    return canvas, {"headline": headline or body[:80], "has_logo": has_logo, "credit": "X", "text_boxes": p.pop("_boxes", [])}


def end_card(kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5", n: tuple[int, int] | None = None):
    """hb.py end_card_png, brand-driven: logo, the CTA, the @handle in the accent, a rule, the brand line. Each line is
    fitted to 86% of the width, so a long CTA ("Follow for daily AI") shrinks instead of running off the edges."""
    R = _r()
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = colors(p)
    head_f, body_f, _ = _fonts(p)
    p["_boxes"] = []
    canvas = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(canvas, "RGBA")
    ec = kit.get("end_card") or {}
    cta = (p.get("end_card_text") or ec.get("cta") or "Follow").strip()[:60]
    cta = cta.upper() if p.get("headline_case") == "upper" else cta
    handle = ("@" + kit["handle"].lstrip("@")) if kit.get("handle") else (kit.get("name") or "")
    line = (ec.get("line") or "").strip()[:90]
    maxw = int(W * 0.86)
    x0 = (W - maxw) // 2
    cf, clines, cstep = fit(d, cta, head_f, maxw, int(H * 0.26), int(W * 0.15), 40, 1.02, 2)
    hf, hlines, hstep = fit(d, handle, head_f, maxw, int(H * 0.09), int(W * 0.09), 28, 1.05, 1) if handle else (None, [], 0)
    lf, llines, lstep = fit(d, line, body_f, maxw, int(H * 0.1), int(W * 0.038), 22, 1.3, 2) if line else (None, [], 0)
    lg = R.load_logo(logo)
    lgh = 0
    if lg is not None:
        lg = lg.copy()
        lg.thumbnail((int(W * 0.5), int(W * 0.13)), Image.LANCZOS)
        lgh = lg.height + 70
    block = lgh + cstep * len(clines) + 14 + hstep * len(hlines) + (60 + lstep * len(llines) if llines else 0)
    y = max(REEL_TOP_SAFE if ratio == "9:16" else 60, (H - block) // 2 - (60 if ratio == "9:16" else 20))
    if lg is not None:
        canvas.paste(lg, ((W - lg.width) // 2, y), lg)
        y += lgh
    for ln in clines:
        _text(d, (x0, y), ln, cf, txt, p, anchor_w=maxw)
        y += cstep
    y += 14
    for ln in hlines:
        _text(d, (x0, y), ln, hf, acc, p, anchor_w=maxw)
        y += hstep
    if llines:
        y += 26
        d.line((W / 2 - 60, y, W / 2 + 60, y), fill=_mix(txt, prim, 0.5), width=2)
        y += 34
        for ln in llines:
            _text(d, (x0, y), ln, lf, _mix(txt, prim, 0.15), p, anchor_w=maxw)
            y += lstep
    if n:  # a carousel's last slide carries its N/N badge like the others
        badge(d, f"{n[0]}/{n[1]}", W, p, side="l" if p.get("logo_position", "tr") in ("tr", "br") else "r",
              y=REEL_TOP_SAFE if ratio == "9:16" else 40)
    return canvas, {"headline": cta, "has_logo": lg is not None, "credit": "x", "end_card": True, "text_boxes": p.pop("_boxes", [])}


def reel_overlay(hook: str, kit: dict, p: dict, *, band: tuple[int, int], logo: bytes | None = None, ratio: str = "9:16"):
    """hb.py reel_overlay_png: an opaque 9:16 frame with the logo and the hook headline above a transparent video band,
    so the headline is on screen from the first frame to the last (it doubles as the cover). Returns (RGBA, (vx, vy))."""
    R = _r()
    W, H = SIZES.get(ratio, SIZES["9:16"])
    prim, acc, txt = colors(p)
    p["_boxes"] = []
    o = Image.new("RGBA", (W, H), prim + (255,))
    d = ImageDraw.Draw(o)
    m = int(p.get("safe_margin", 64))
    y = REEL_TOP_SAFE
    lg = R.load_logo(logo)
    if lg is not None:
        l2 = lg.copy()
        l2.thumbnail((int(W * 0.4), 70), Image.LANCZOS)
        o.paste(l2, ((W - l2.width) // 2, y), l2)
        y += l2.height + 30
    vw, vh = band
    max_head = H - REEL_BOTTOM_SAFE - vh - y - 40
    hb = headline_block(d, R.shape_headline(hook, p), (m, y, W - 2 * m, max(160, max_head)), p, start=int(W * 0.08), minimum=34,
                        align="center", max_lines=4, first_line_accent=True)
    vy = max(hb + 30, (H - vh) // 2 - 40)
    vy = min(vy, H - REEL_BOTTOM_SAFE - vh)
    vx = (W - vw) // 2
    d.rectangle((vx, vy, vx + vw - 1, vy + vh - 1), fill=(0, 0, 0, 0))
    handle = (kit.get("handle") or "").lstrip("@")
    if handle:
        f = R.font(_fonts(p)[2], max(22, int(W * 0.03)))
        _text(d, (m, vy + vh + 26), "@" + handle, f, acc, p, anchor_w=W - 2 * m)
    return o, (vx, vy), p.pop("_boxes", [])


def overflow(meta: dict, size: tuple[int, int], margin: int = 0) -> list[list[int]]:
    """Text boxes that leave the canvas (or its margin). [] means nothing is clipped."""
    W, H = size
    return [b for b in meta.get("text_boxes") or [] if b[0] < margin or b[1] < margin or b[2] > W - margin or b[3] > H - margin]
