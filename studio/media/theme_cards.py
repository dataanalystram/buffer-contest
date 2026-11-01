"""Title cards, facts cards, stages and on-screen chrome for the video THEMES (studio/media/styles.py).

Every function draws with Pillow and auto-fits its text with ``textfit`` (through compose's helpers), so nothing clips
at 9:16, 4:5 or 1:1, and returns the pixel boxes of what it drew so tests can check the safe area and overlaps.

  stage(cfg, W, H, brand, panel)          the ground behind a framed video (keynote stage, Swiss page, HUD grid...)
  overlay(cfg, W, H, brand, hook, ...)    the main section's chrome + hook + credit chip, drawn over the video
  cover(cfg, path, W, H, brand, hook...)  the theme's title card (the hook in the first second)
  facts(cfg, path, W, H, brand, ...)      the theme's facts card

Looks reimplemented from lemomo-ai/lemo-opuscar (MIT, Copyright (c) 2026 LemoLab; see NOTICE): Caption News' ledger
sting and channel bug, the news package's sting and paper strap, the newsreel / silent-film intertitle and projected
gate, the dark keynote stage, the Swiss grid page, the constructivist red band, the riso double hit, the HUD target
brackets, the screencast window, the investigation exhibit, the countdown clock block, the CRT terminal, the tilt-shift
road sign and the game-show candy set. Their canvas engines are not used; these are new Pillow layouts in their grammar.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw, ImageFilter

from . import textfit


def _c():
    from . import compose  # late: compose imports this module
    return compose


def _rgb(h: str) -> tuple[int, int, int]:
    h = (h or "#000000").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _mix(a, b, t: float):
    a, b = (_rgb(a) if isinstance(a, str) else a), (_rgb(b) if isinstance(b, str) else b)
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _k(W: int) -> float:
    return W / 1080


def _block(d, text, font, box, **kw):
    return _c()._text_block(d, text, font, box, **kw)


def _vgrad(W, H, y0, y1, a0, a1, color=(0, 0, 0)) -> Image.Image:
    """RGBA vertical gradient: alpha a0 at y0 to a1 at y1 (clamped outside)."""
    g = Image.new("L", (1, H))
    for y in range(H):
        t = 0.0 if y <= y0 else (1.0 if y >= y1 else (y - y0) / max(1, y1 - y0))
        t = t * t * (3 - 2 * t)
        g.putpixel((0, y), int(a0 + (a1 - a0) * t))
    lay = Image.new("RGBA", (W, H), color + (0,))
    lay.putalpha(g.resize((W, H)))
    return lay


def _radial(W, H, cx, cy, r, color, alpha) -> Image.Image:
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse((cx - r, cy - r, cx + r, cy + r), fill=alpha)
    m = m.filter(ImageFilter.GaussianBlur(r * 0.5))
    lay = Image.new("RGBA", (W, H), tuple(color) + (0,))
    lay.putalpha(m)
    return lay


def _vignette(im: Image.Image, strength: int = 150) -> Image.Image:
    W, H = im.size
    m = Image.new("L", (W, H), strength)
    ImageDraw.Draw(m).ellipse((-W * 0.15, -H * 0.1, W * 1.15, H * 1.1), fill=0)
    m = m.filter(ImageFilter.GaussianBlur(min(W, H) * 0.18))
    im = im.convert("RGBA")
    im.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (W, H), (0, 0, 0)).split(), m)))
    return im


def _grid(d, W, H, step, color, every=0, strong=None):
    """Hairline grid. Translucent colours are composited on a layer (ImageDraw would write the alpha straight in)."""
    im = d._image
    lay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    for i, x in enumerate(range(0, W + 1, step)):
        ld.line((x, 0, x, H), fill=strong if (every and i % every == 0 and strong) else color, width=1)
    for i, y in enumerate(range(0, H + 1, step)):
        ld.line((0, y, W, y), fill=strong if (every and i % every == 0 and strong) else color, width=1)
    if im.mode == "RGBA":
        im.alpha_composite(lay)
    else:
        im.paste(lay, (0, 0), lay)


def _dots(d, W, H, step, color, r=1.4):
    for y in range(step // 2, H, step):
        for x in range(step // 2, W, step):
            d.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _brackets(d, box, color, L=40, w=4):
    x0, y0, x1, y1 = box
    for (x, y, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        d.line((x, y, x + sx * L, y), fill=color, width=w)
        d.line((x, y, x, y + sy * L), fill=color, width=w)


def _label(d, text, xy, font, size, fill, spacing=3):
    """Small tracked uppercase label; returns its box."""
    f = textfit.font(font, size)
    x, y = xy
    x0 = x
    for ch in text.upper():
        d.text((x, y), ch, font=f, fill=fill)
        x += f.getlength(ch) + spacing
    a, de = f.getmetrics()
    return (int(x0), int(y), int(x - spacing), int(y + a + de))


def _still(still, W, H, cy=0.4):
    return _c().cover_fit(still, W, H, cy) if still is not None else None


def _grey(im: Image.Image, tint=None, contrast=1.2) -> Image.Image:
    from PIL import ImageEnhance, ImageOps
    g = ImageOps.grayscale(im)
    g = ImageEnhance.Contrast(g).enhance(contrast)
    if tint:
        return ImageOps.colorize(g, black=tint[0], white=tint[1])
    return g.convert("RGB")


def _paste_round(canvas, img, xy, r):
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, img.width - 1, img.height - 1), r, fill=255)
    canvas.paste(img, xy, m)


# ================================================================================================== stage
def stage(cfg: dict, W: int, H: int, brand: dict, panel: tuple) -> Image.Image:
    """The ground behind a framed video for fill='stage' themes (RGB, W x H)."""
    k = _k(W)
    bg = _rgb(brand["bg"])
    im = Image.new("RGBA", (W, H), bg + (255,))
    d = ImageDraw.Draw(im)
    ch = cfg.get("chrome")
    px0, py0, px1, py1 = panel
    if ch == "keynote":
        im.alpha_composite(_radial(W, H, W // 2, int(H * 0.12), int(W * 0.7), _mix(bg, "#9AA8C0", 0.35), 90))
        im.alpha_composite(_radial(W, H, int(W * 0.85), int(H * 0.8), int(W * 0.5), _rgb(brand["accent"]), 26))
        d = ImageDraw.Draw(im)
        _grid(d, W, H, max(24, int(48 * k)), (255, 255, 255, 8), every=4, strong=(255, 255, 255, 16))
        im = _vignette(im, 120)
    elif ch == "studio":
        im.alpha_composite(_radial(W, H, W // 2, int(py0 - H * 0.05), int(W * 0.55), (210, 225, 240), 60))
        floor = py1 + int(H * 0.01)
        im.alpha_composite(_vgrad(W, H, floor, H, 0, 70, (120, 140, 160)))
        d = ImageDraw.Draw(im)
        d.line((0, floor, W, floor), fill=(255, 255, 255, 40), width=2)
        im = _vignette(im, 140)
    elif ch == "swiss":
        step = max(20, int(W / 12))
        _grid(d, W, H, step, _mix(bg, "#000000", 0.07) + (255,))
    elif ch == "hud":
        im.alpha_composite(_radial(W, H, (px0 + px1) // 2, (py0 + py1) // 2, int(W * 0.6), _rgb(brand["accent"]), 34))
        d = ImageDraw.Draw(im)
        _dots(d, W, H, max(18, int(36 * k)), _mix(bg, brand["accent"], 0.22) + (255,), r=1.6 * k)
        im = _vignette(im, 170)
    elif ch == "window":
        top = _rgb(brand["bg"])
        bot = _mix(brand["bg"], brand["accent"], 0.35)
        g = Image.new("RGB", (1, H))
        for y in range(H):
            g.putpixel((0, y), _mix(top, bot, y / H))
        im = g.resize((W, H)).convert("RGBA")
        sh = Image.new("L", (W, H), 0)
        r = int(22 * k)
        ImageDraw.Draw(sh).rounded_rectangle((px0, py0 + int(14 * k), px1, py1 + int(18 * k)), r, fill=150)
        sh = sh.filter(ImageFilter.GaussianBlur(int(24 * k)))
        im.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (W, H), (0, 0, 0)).split(), sh)))
    elif ch == "evidence":
        d.rectangle((px0 - int(18 * k), py0 - int(18 * k), px1 + int(18 * k), py1 + int(18 * k)), fill=(20, 22, 25, 255),
                    outline=(46, 50, 56, 255), width=2)
        _grid(d, W, H, max(30, int(90 * k)), (255, 255, 255, 6))
    elif ch == "terminal":
        im.alpha_composite(_radial(W, H, (px0 + px1) // 2, (py0 + py1) // 2, int(W * 0.7), _rgb(brand["text"]), 22))
        im = _vignette(im, 190)
    return im.convert("RGB")


# ================================================================================================== overlay
def overlay(cfg: dict, W: int, H: int, brand: dict, hook: str, credit: str, L: dict, accent_words) -> tuple[Image.Image, dict]:
    """The main section's overlay for a theme: chrome, the hook (from frame 0) and the credit chip. Returns (RGBA, boxes)."""
    C = _c()
    k = _k(W)
    o = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    l, t, r, b = L["safe"]
    ch = cfg.get("chrome")
    lay = cfg["layout"]
    boxes: dict = {}
    acc = set(accent_words or [])
    head, bold = brand["head_font"], brand["bold_font"]
    text, accent, bgc = brand["text"], brand["accent"], brand["bg"]
    tall = H / W > 1.6

    if lay in ("opus", "hype"):  # full-bleed footage: the chrome floats on it
        hb = L["hook"]
        if ch in ("bug", "clock", "kino", "riso", "sign", "film"):
            if "botfade" in cfg.get("fx", []) or ch in ("bug", "clock"):
                o.alpha_composite(_vgrad(W, H, int(H * 0.42), H, 0, 215, _rgb(bgc)))
                o.alpha_composite(_vgrad(W, H, int(H * 0.16), 0, 0, 120, (0, 0, 0)))
                d = ImageDraw.Draw(o)
            y0 = hb[1]
            if ch == "film":
                bar = int(H * 0.085)
                d.rectangle((0, 0, W, bar), fill=(0, 0, 0, 255))
                d.rectangle((0, H - bar, W, H), fill=(0, 0, 0, 255))
                boxes["letterbox"] = (0, 0, W, bar)
                y0 = max(y0, bar + int(20 * k))
            if ch == "bug":  # yellow channel bug: the brand name in ink on the accent
                f_sz = max(16, int(30 * k))
                lab = (brand.get("name") or "NEWS").upper()
                fo = textfit.font("Inter-Bold.ttf", f_sz)
                tw = int(fo.getlength(lab)) + 3 * len(lab)
                bh = int(f_sz * 1.75)
                d.rectangle((l, y0, l + tw + int(f_sz * 1.2), y0 + bh), fill=_rgb(accent) + (255,))
                boxes["bug"] = _label(d, lab, (l + int(f_sz * 0.6), y0 + int(f_sz * 0.33)), "Inter-Bold.ttf", f_sz,
                                      _rgb(brand.get("on_accent", "#111111")))
                boxes["bug"] = (l, y0, l + tw + int(f_sz * 1.2), y0 + bh)
                y0 += bh + int(18 * k)
            hook_r = r
            if ch == "clock":  # the T-minus block lives top right; the running digits are drawn by FFmpeg (fx "clock")
                cw, chh = int(250 * k), int(92 * k)
                cb = (r - cw, y0, r, y0 + chh)
                d.rectangle(cb, fill=_rgb(bgc) + (230,), outline=_rgb(accent) + (255,), width=max(2, int(3 * k)))
                _label(d, "T-MINUS", (cb[0] + int(14 * k), cb[1] + int(8 * k)), "IBMPlexMono-SemiBold.ttf", max(12, int(18 * k)),
                       _rgb(accent))
                boxes["clock"] = cb
                L["clock_text"] = (cb[0] + int(14 * k), cb[1] + int(32 * k), max(18, int(46 * k)))
                hook_r = cb[0] - int(24 * k)
                bar_y = b - int(10 * k)
                d.rectangle((l, bar_y, r, bar_y + max(4, int(6 * k))), fill=(255, 255, 255, 50))
                L["progress"] = (l, bar_y, r - l, max(4, int(6 * k)))
            hh = max(int(H * (0.12 if tall else 0.15)), hb[3] - hb[1]) - (y0 - hb[1])
            box = (l, y0, hook_r, y0 + max(int(80 * k), hh))
            if ch == "kino":  # the red diagonal band carries the headline
                band = Image.new("RGBA", (int(W * 1.5), int((box[3] - box[1]) + 60 * k)), (0, 0, 0, 0))
                bd = ImageDraw.Draw(band)
                bd.rectangle((0, 0, band.width, band.height), fill=_rgb(accent) + (245,))
                band = band.rotate(4, expand=True, resample=Image.BICUBIC)
                o.alpha_composite(band, (-int(W * 0.25), box[1] - int(40 * k)))
                d = ImageDraw.Draw(o)
                bb, _ = _block(d, hook, head, box, max_size=int(110 * k), min_size=24, max_lines=3, upper=True,
                               fill=brand["text"], align="left", accent_words=acc, accent="#141210")
            elif ch == "riso":
                f = textfit.fit(hook, head, box[2] - box[0] - int(12 * k), box[3] - box[1], max_size=int(120 * k), min_size=24,
                                max_lines=3, upper=True)
                d.rectangle((box[0] - int(14 * k), box[1] - int(10 * k), box[0] + f.width + int(26 * k), box[1] + f.height + int(18 * k)),
                            fill=_rgb(bgc) + (235,))
                off = max(3, int(7 * k))
                textfit.draw_fit(d, f, (box[0] + off, box[1] + off, box[2], box[3]), fill=_rgb(text) + (230,), align="left", valign="top")
                bb = textfit.draw_fit(d, f, box, fill=_rgb(accent) + (235,), align="left", valign="top")
                bb = (bb[0], bb[1], bb[2] + off, bb[3] + off)
            elif ch == "sign":
                f = textfit.fit(hook, "Inter-Bold.ttf", box[2] - box[0] - int(60 * k), box[3] - box[1] - int(40 * k),
                                max_size=int(76 * k), min_size=22, max_lines=3)
                sb = (box[0], box[1], box[0] + f.width + int(60 * k), box[1] + f.height + int(44 * k))
                d.rounded_rectangle(sb, int(18 * k), fill=(31, 107, 69, 255))
                d.rounded_rectangle((sb[0] + int(7 * k), sb[1] + int(7 * k), sb[2] - int(7 * k), sb[3] - int(7 * k)), int(12 * k),
                                    outline=(255, 255, 255, 255), width=max(2, int(4 * k)))
                textfit.draw_fit(d, f, (sb[0] + int(30 * k), sb[1] + int(22 * k), sb[2] - int(30 * k), sb[3] - int(22 * k)),
                                 fill="#FFFFFF", align="left")
                bb = sb
            elif ch == "film":
                bb, _ = _block(d, hook, head, box, max_size=int(72 * k), min_size=22, max_lines=3, fill=text, accent_words=acc,
                               accent=accent, valign="top")
            else:  # bug / clock: big flush-left headline with a soft shadow
                sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                bb, _ = _block(ImageDraw.Draw(sh), hook, bold, box, max_size=int(84 * k), min_size=24, max_lines=3, fill=(0, 0, 0, 200),
                               align="left", valign="top")
                o.alpha_composite(sh.filter(ImageFilter.GaussianBlur(int(6 * k))))
                d = ImageDraw.Draw(o)
                bb, _ = _block(d, hook, bold, box, max_size=int(84 * k), min_size=24, max_lines=3, fill=text, align="left",
                               valign="top", accent_words=acc, accent=accent)
            boxes["hook"] = bb
            if credit:
                boxes["credit"] = C._credit_chip(d, credit, l, bb[3] + int(22 * k), r - l)
        elif ch == "candy":  # hype base: the giant hook is hype's first-beat layer; here the round label + credit
            f_sz = max(16, int(30 * k))
            lab = "ROUND 1 · " + (brand.get("name") or "").upper()
            fo = textfit.fit(lab, "Figtree-ExtraBold.ttf", r - l - int(60 * k), int(60 * k), max_size=f_sz, min_size=14, max_lines=1)
            tw = int(textfit.font("Figtree-ExtraBold.ttf", fo.size).getlength(fo.lines[0]))
            pb = (l, t, l + tw + int(48 * k), t + int(fo.size * 2.0))
            d.rounded_rectangle(pb, (pb[3] - pb[1]) // 2, fill=_rgb(accent) + (255,), outline=(27, 21, 48, 255), width=max(3, int(5 * k)))
            textfit.draw_fit(d, fo, (pb[0] + int(24 * k), pb[1], pb[2] - int(24 * k), pb[3]), fill="#1B1530")
            boxes["label"] = pb
            if credit:
                boxes["credit"] = C._credit_chip(d, credit, l, pb[3] + int(18 * k), r - l)
            L["hook"] = (l, pb[3] + int(90 * k), r, min(pb[3] + int(90 * k) + int(H * 0.24), L["captions_center"] - int(H * 0.09)))
        else:
            return C._overlay(lay, W, H, brand, hook, credit, L, acc)
        return o, boxes

    # ---------------------------------------------------------------- framed layouts (huhbyte_news / minimal / stage)
    p = L["panel"]
    if ch == "strap":  # news package: ink ground with the panel hole, lemon tag + headline, paper strap for the captions
        o.paste(Image.new("RGBA", (W, H), _rgb(bgc) + (255,)))
        d = ImageDraw.Draw(o)
        if "logo" in L:
            lg = C._logo(brand, L["logo"][3] - L["logo"][1])
            if lg:
                x = (W - lg.width) // 2
                o.paste(lg, (x, L["logo"][1]), lg)
                boxes["logo"] = (x, L["logo"][1], x + lg.width, L["logo"][1] + lg.height)
        hb = L["hook"]
        tag_w = max(8, int(14 * k))
        d.rectangle((hb[0], hb[1] + int(4 * k), hb[0] + tag_w, hb[3] - int(4 * k)), fill=_rgb(accent) + (255,))
        bb, _ = _block(d, hook, head, (hb[0] + tag_w + int(22 * k), hb[1], hb[2], hb[3]), max_size=int(92 * k), min_size=28,
                       max_lines=3, upper=True, fill=text, align="left", accent_words=acc, accent=accent)
        boxes["hook"] = (hb[0], min(bb[1], hb[1] + int(4 * k)), bb[2], max(bb[3], hb[3] - int(4 * k)))
        d.rectangle(p, fill=(0, 0, 0, 0))
        boxes["panel"] = p
        if "cap_band" in L:
            cb = L["cap_band"]
            end_w = int(140 * k)
            d.rectangle((cb[0], cb[1], cb[2], cb[3]), fill=_rgb("#F4F1E8") + (255,))
            d.rectangle((cb[0], cb[1], cb[0] + end_w, cb[3]), fill=_rgb(accent) + (255,))
            _block(d, (brand.get("name") or "NEWS").split()[0].upper(), "Inter-Bold.ttf",
                   (cb[0] + int(10 * k), cb[1] + int(8 * k), cb[0] + end_w - int(10 * k), cb[3] - int(8 * k)), max_size=int(30 * k),
                   min_size=9, max_lines=1, fill=brand.get("on_accent", "#121417"))
            L["cap_left"] = cb[0] + end_w + int(22 * k)
        if "pill_y" in L:
            boxes["pill"] = C._pill(d, "Follow " + brand["handle"], W // 2, L["pill_y"], brand, size=32, maxw=r - l)
    else:
        ground = cfg.get("fill") in ("panel", "stage")
        hb = L["hook"]
        if cfg.get("fill") == "panel":
            o.paste(Image.new("RGBA", (W, H), _rgb(bgc) + (255,)))
            d = ImageDraw.Draw(o)
            d.rectangle(p, fill=(0, 0, 0, 0))
        align, fnt, upper = "center", bold, False
        if ch in ("swiss", "evidence"):
            align = "left"
        if ch == "gate":
            fnt = head
        if ch == "terminal":
            fnt, upper = head, True
        hook_box = hb
        if ch == "swiss":  # the red signal square at the right of the headline row
            sq = int(min(hb[3] - hb[1], 70 * k))
            d.rectangle((hb[2] - sq, hb[1], hb[2], hb[1] + sq), fill=_rgb(accent) + (255,))
            boxes["signal"] = (hb[2] - sq, hb[1], hb[2], hb[1] + sq)
            hook_box = (hb[0], hb[1], hb[2] - sq - int(24 * k), hb[3])
        htxt = ("> " + hook) if ch == "terminal" else hook
        bb, _ = _block(d, htxt, fnt, hook_box, max_size=int((70 if ch != "terminal" else 92) * k), min_size=24, max_lines=3,
                       fill=text, align=align, valign="bottom", accent_words=acc, accent=accent, upper=upper)
        if ch == "terminal":  # the block cursor after the last line
            d.rectangle((min(bb[2] + int(10 * k), hook_box[2] - int(26 * k)), bb[3] - int(46 * k), min(bb[2] + int(36 * k), hook_box[2]),
                         bb[3] - int(4 * k)), fill=_rgb(text) + (255,))
        boxes["hook"] = bb
        boxes["panel"] = p
        rule = max(2, int(2 * k))
        if ch == "gate":  # a cream rule round the projected gate, with corner ornaments
            g = int(12 * k)
            d.rectangle((p[0] - g, p[1] - g, p[2] + g, p[3] + g), outline=_rgb(text) + (200,), width=rule)
            for (x, y) in ((p[0] - g, p[1] - g), (p[2] + g, p[1] - g), (p[0] - g, p[3] + g), (p[2] + g, p[3] + g)):
                d.ellipse((x - 6 * k, y - 6 * k, x + 6 * k, y + 6 * k), fill=_rgb(text) + (230,))
        elif ch in ("keynote", "studio"):
            d.rounded_rectangle((p[0] - 1, p[1] - 1, p[2] + 1, p[3] + 1), int(16 * k), outline=(255, 255, 255, 46), width=rule)
            _round_corners(d, p, int(16 * k), _rgb(bgc) + (255,))
        elif ch == "hud":
            g = int(14 * k)
            _brackets(d, (p[0] - g, p[1] - g, p[2] + g, p[3] + g), _rgb(accent) + (255,), L=int(48 * k), w=max(3, int(4 * k)))
            for i in range(0, p[2] - p[0], max(10, int(24 * k))):  # tick ruler under the picture
                hgt = int((14 if i % (max(10, int(24 * k)) * 5) == 0 else 7) * k)
                d.line((p[0] + i, p[3] + g + int(6 * k), p[0] + i, p[3] + g + int(6 * k) + hgt), fill=_rgb(accent) + (150,), width=1)
            lab = "SCAN 01 // " + (brand.get("name") or "").upper()
            boxes["hud_label"] = _label(d, lab[:34], (p[2] - int(16 * k) - int(len(lab[:34]) * 14 * k), p[1] + int(14 * k)),
                                        "IBMPlexMono-SemiBold.ttf", max(11, int(18 * k)), _rgb(accent) + (230,), spacing=2)
        elif ch == "window":  # app chrome over the top strip of the video, rounded corners
            tb = int(46 * k)
            d.rounded_rectangle((p[0], p[1], p[2], p[1] + tb + int(20 * k)), int(22 * k), fill=(246, 246, 248, 255))
            d.rectangle((p[0], p[1] + tb, p[2], p[1] + tb + int(20 * k)), fill=(0, 0, 0, 0))
            for i, col in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
                cx = p[0] + int((26 + i * 26) * k)
                d.ellipse((cx - 8 * k, p[1] + tb / 2 - 8 * k, cx + 8 * k, p[1] + tb / 2 + 8 * k), fill=col + (255,))
            ttl = (brand.get("name") or "demo") + " — demo"
            f = textfit.fit(ttl, "Inter-SemiBold.ttf", p[2] - p[0] - int(220 * k), tb - int(14 * k), max_size=int(22 * k), min_size=10, max_lines=1)
            textfit.draw_fit(d, f, (p[0] + int(110 * k), p[1], p[2] - int(110 * k), p[1] + tb), fill="#55555C")
            boxes["titlebar"] = (p[0], p[1], p[2], p[1] + tb)
            _round_corners(d, (p[0], p[1], p[2], p[3]), int(22 * k), _mix(bgc, accent, 0.3) + (255,), top=False)
        elif ch == "evidence":
            d.rectangle((p[0] - 1, p[1] - 1, p[2] + 1, p[3] + 1), outline=(46, 50, 56, 255), width=rule)
            lab = "EXHIBIT A"
            f_sz = max(12, int(20 * k))
            fo = textfit.font("IBMPlexMono-SemiBold.ttf", f_sz)
            tw = int(fo.getlength(lab)) + 2 * len(lab)
            eb = (p[2] - tw - int(30 * k), p[1] + int(14 * k), p[2] - int(12 * k), p[1] + int(14 * k) + int(f_sz * 1.7))
            d.rectangle(eb, fill=_rgb(accent) + (255,))
            _label(d, lab, (eb[0] + int(9 * k), eb[1] + int(f_sz * 0.3)), "IBMPlexMono-SemiBold.ttf", f_sz, _rgb(bgc), spacing=2)
            boxes["exhibit"] = eb
            L["timecode"] = (p[2] - int(200 * k), p[3] - int(50 * k), max(14, int(26 * k)))
        elif ch == "terminal":
            d.rounded_rectangle((p[0] - int(10 * k), p[1] - int(10 * k), p[2] + int(10 * k), p[3] + int(10 * k)), int(26 * k),
                                outline=_rgb(text) + (150,), width=rule)
        elif ch == "swiss":
            pass
        if not ground and "cap_band" not in L:
            pass
        if credit:
            boxes["credit"] = C._credit_chip(d, credit, p[0] + int(16 * k), p[1] + int(16 * k) + (int(46 * k) if ch == "window" else 0),
                                             (p[2] - p[0]) - int(32 * k) - (int(200 * k) if ch in ("hud", "evidence") else 0))
    return o, boxes


def _round_corners(d, p, r, color, top=True):
    """Round a video panel's square corners by painting the ground colour outside a quarter circle at each corner."""
    if color is None:
        return
    x0, y0, x1, y1 = p
    for (cx, cy, sx, sy) in ((x0, y0, 1, 1), (x1, y0, -1, 1), (x0, y1, 1, -1), (x1, y1, -1, -1)):
        if not top and sy == 1:
            continue
        sq = (min(cx, cx + r * sx), min(cy, cy + r * sy), max(cx, cx + r * sx), max(cy, cy + r * sy))
        d.rectangle(sq, fill=color)
        ex = (min(cx, cx + 2 * r * sx), min(cy, cy + 2 * r * sy), max(cx, cx + 2 * r * sx), max(cy, cy + 2 * r * sy))
        d.ellipse(ex, fill=(0, 0, 0, 0))


def sweep_png(path: str, W: int, H: int) -> str:
    """A soft diagonal strip of light (glass_studio's strip-light sweep), moved across the frame by FFmpeg."""
    w = max(60, int(W * 0.22))
    im = Image.new("RGBA", (w, H), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    for x in range(w):
        a = int(46 * math.sin(math.pi * x / w) ** 2)
        d.line((x, 0, x, H), fill=(235, 245, 255, a))
    im = im.rotate(0)
    im.save(path)
    return path


# ================================================================================================== covers
def cover(cfg: dict, path: str, W: int, H: int, brand: dict, hook: str, still: Image.Image | None, label: str = "WATCH",
          accent_words=None) -> dict:
    """The theme's title card. The hook is the largest thing on it, inside the safe area, from the first frame."""
    C = _c()
    k = _k(W)
    l, t, r, b = C.safe_area(W, H)
    v = cfg.get("card")
    bgc, text, accent = brand["bg"], brand["text"], brand["accent"]
    on_acc = brand.get("on_accent", "#111111")
    head, bold = brand["head_font"], brand["bold_font"]
    acc = set(accent_words or [])
    boxes: dict = {}
    im = Image.new("RGBA", (W, H), _rgb(bgc) + (255,))
    st = _still(still, W, H, 0.35)
    tall = H / W > 1.6
    lg = C._logo(brand, int(H * 0.032))

    def logo_tl(dd, color_bg=None):
        if lg:
            im.paste(lg, (l, t), lg)
            boxes["logo"] = (l, t, l + lg.width, t + lg.height)

    if v == "ledger":  # Caption News title sting: a yellow ruled page over the left of the footage, the title slammed on it
        if st:
            im.paste(st)
        im.alpha_composite(_vgrad(W, H, int(H * 0.3), H, 40, 200, _rgb(bgc)))
        pw = int(W * (0.78 if tall else 0.66))
        page = Image.new("RGBA", (pw, H), _rgb(accent) + (250,))
        pd = ImageDraw.Draw(page)
        for y in range(0, H, max(24, int(54 * k))):
            pd.line((0, y, pw, y), fill=_mix(accent, "#000000", 0.12) + (255,), width=1)
        pd.line((int(78 * k), 0, int(78 * k), H), fill=(255, 74, 61, 200), width=2)
        pd.line((int(86 * k), 0, int(86 * k), H), fill=(255, 74, 61, 200), width=2)
        im.alpha_composite(page)
        d = ImageDraw.Draw(im)
        x0 = int(110 * k)
        bb, _ = _block(d, hook, bold, (x0, int(H * 0.32), pw - int(40 * k), b - int(H * 0.12)), max_size=int(118 * k), min_size=30,
                       max_lines=5, fill=on_acc, align="left", valign="middle")
        boxes["hook"] = bb
        lb = _label(d, (brand.get("name") or "").upper() + " · " + (label or "NEWS"), (x0, bb[3] + int(30 * k)), "Inter-Bold.ttf",
                    max(14, int(24 * k)), _rgb(on_acc))
        boxes["label"] = lb
        logo_tl(d)
    elif v == "sting":  # news package: lemon plate on ink, the headline on paper
        d = ImageDraw.Draw(im)
        if st:
            sm = st.resize((W, H)).filter(ImageFilter.GaussianBlur(int(18 * k)))
            im.paste(Image.blend(sm, Image.new("RGB", (W, H), _rgb(bgc)), 0.7))
            d = ImageDraw.Draw(im)
        plate_h = int(H * 0.09)
        py = int(H * 0.36)
        d.rectangle((0, py, int(W * 0.7), py + plate_h), fill=_rgb(accent) + (255,))
        _block(d, (label or "NEWS").upper(), "Inter-Bold.ttf", (l, py + int(plate_h * 0.18), int(W * 0.7) - int(30 * k), py + plate_h - int(plate_h * 0.18)),
               max_size=int(64 * k), min_size=16, max_lines=1, fill=on_acc, align="left")
        y = py + plate_h + int(30 * k)
        bb, _ = _block(d, hook, head, (l, y, r, min(b, y + int(H * 0.32))), max_size=int(118 * k), min_size=30, max_lines=4,
                       upper=True, fill=text, align="left", valign="top", accent_words=acc, accent=accent)
        boxes["hook"] = bb
        d.line((l, bb[3] + int(26 * k), r, bb[3] + int(26 * k)), fill=_rgb(brand.get("muted", "#D4B064")) + (255,), width=max(2, int(3 * k)))
        logo_tl(d)
    elif v == "intertitle":  # newsreel / silent film: an ornate framed card, cream serif centred
        d = ImageDraw.Draw(im)
        fr = (l + int(10 * k), t + int(40 * k), r - int(10 * k), b - int(40 * k))
        d.rectangle(fr, outline=_rgb(text) + (220,), width=max(3, int(4 * k)))
        d.rectangle((fr[0] + int(14 * k), fr[1] + int(14 * k), fr[2] - int(14 * k), fr[3] - int(14 * k)), outline=_rgb(text) + (140,),
                    width=max(1, int(2 * k)))
        for (x, y) in ((fr[0], fr[1]), (fr[2], fr[1]), (fr[0], fr[3]), (fr[2], fr[3])):
            d.regular_polygon((x, y, int(16 * k)), 4, rotation=45, fill=_rgb(text) + (240,))
        inner = (fr[0] + int(60 * k), fr[1] + int(90 * k), fr[2] - int(60 * k), fr[3] - int(90 * k))
        bb, _ = _block(d, hook, head, inner, max_size=int(110 * k), min_size=28, max_lines=6, fill=text, spacing=1.18)
        boxes["hook"] = bb
        cy = bb[1] - int(40 * k)
        d.line((W // 2 - int(90 * k), cy, W // 2 + int(90 * k), cy), fill=_rgb(text) + (200,), width=2)
        cy = bb[3] + int(40 * k)
        d.line((W // 2 - int(90 * k), cy, W // 2 + int(90 * k), cy), fill=_rgb(text) + (200,), width=2)
        im = _vignette(im, 160)
    elif v in ("keynote", "studio"):  # one line per screen on a dark stage, the accent on one word
        stg = stage({**cfg, "chrome": "keynote" if v == "keynote" else "studio"}, W, H, brand,
                    (int(W * 0.2), int(H * 0.62), int(W * 0.8), int(H * 0.8)))
        im = stg.convert("RGBA")
        d = ImageDraw.Draw(im)
        if v == "studio" and st:
            sw = int(W * 0.62)
            sh = int(sw * 9 / 16)
            sm = C.cover_fit(still, sw, sh, 0.4)
            sy = int(H * 0.58)
            _paste_round(im, sm, ((W - sw) // 2, sy), int(14 * k))
            refl = sm.transpose(Image.FLIP_TOP_BOTTOM)
            m = _vgrad(sw, sh, 0, sh, 90, 0).getchannel("A")
            im.paste(refl, ((W - sw) // 2, sy + sh + int(6 * k)), m)
            d = ImageDraw.Draw(im)
            box = (l, t + int(H * 0.08), r, sy - int(40 * k))
        else:
            box = (l, int(H * 0.3), r, int(H * 0.66))
        lb = _label(d, (brand.get("name") or "").upper(), (l, box[1] - int(60 * k)), "Inter-SemiBold.ttf", max(12, int(22 * k)),
                    _rgb(brand.get("muted", "#8E9299")), spacing=6)
        boxes["label"] = lb
        bb, _ = _block(d, hook, bold, box, max_size=int(104 * k), min_size=28, max_lines=4, fill=text, accent_words=acc or None,
                       accent=accent)
        boxes["hook"] = bb
        d.line((W // 2 - int(40 * k), bb[3] + int(36 * k), W // 2 + int(40 * k), bb[3] + int(36 * k)), fill=(255, 255, 255, 90), width=2)
    elif v == "swiss":
        im = stage({**cfg, "chrome": "swiss"}, W, H, brand, (0, 0, 1, 1)).convert("RGBA")
        d = ImageDraw.Draw(im)
        sq = int(W * 0.14)
        d.rectangle((r - sq, t, r, t + sq), fill=_rgb(accent) + (255,))
        boxes["signal"] = (r - sq, t, r, t + sq)
        _label(d, "01", (l, t), "Inter-Bold.ttf", max(14, int(30 * k)), _rgb(text), spacing=1)
        if st:
            sw, sh = r - l, int((r - l) * 0.5)
            ph = C.cover_fit(still, sw, sh, 0.4)
            im.paste(ph, (l, b - sh))
            boxes["photo"] = (l, b - sh, r, b)
            hb = (l, t + sq + int(40 * k), r, b - sh - int(40 * k))
        else:
            hb = (l, t + sq + int(40 * k), r, b)
        bb, _ = _block(d, hook.lower() if len(hook) < 60 else hook, bold, hb, max_size=int(150 * k), min_size=30, max_lines=5,
                       fill=text, align="left", valign="bottom", accent_words=acc, accent=accent, spacing=1.0)
        boxes["hook"] = bb
    elif v == "kino":
        if st:
            im.paste(_grey(st, tint=((20, 18, 16), _rgb(text)), contrast=1.35))
        d = ImageDraw.Draw(im)
        band_h = int(H * (0.26 if tall else 0.34))
        band = Image.new("RGBA", (int(W * 1.6), band_h), _rgb(accent) + (250,))
        band = band.rotate(8, expand=True, resample=Image.BICUBIC)
        by = int(H * 0.40)
        im.alpha_composite(band, (-int(W * 0.3), by - int(band.height * 0.25)))
        d = ImageDraw.Draw(im)
        hb = (l, by + int(band_h * 0.05), r, by + band_h - int(band_h * 0.05))
        bb, _ = _block(d, hook, head, hb, max_size=int(130 * k), min_size=28, max_lines=3, upper=True, fill=text, align="left",
                       accent_words=acc, accent="#141210")
        boxes["hook"] = bb
        logo_tl(d)
    elif v == "riso":
        im = Image.new("RGBA", (W, H), _rgb(bgc) + (255,))
        d = ImageDraw.Draw(im)
        step = max(10, int(22 * k))
        for y in range(0, H, step):  # halftone ramp in the blue ink
            for x in range(0, W, step):
                rr = step * 0.42 * (y / H) ** 1.6
                if rr > 0.6:
                    d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=_rgb(text) + (120,))
        if st:
            sw, sh = r - l, int(H * 0.3)
            ph = _grey(C.cover_fit(still, sw, sh, 0.4), tint=(_rgb(text), _rgb(bgc)), contrast=1.3)
            im.paste(ph, (l, b - sh))
            boxes["photo"] = (l, b - sh, r, b)
            hb = (l, t + int(30 * k), r - int(10 * k), b - sh - int(40 * k))
        else:
            hb = (l, t + int(30 * k), r - int(10 * k), b)
        off = max(4, int(9 * k))
        f = textfit.fit(hook, head, hb[2] - hb[0] - off, hb[3] - hb[1] - off, max_size=int(170 * k), min_size=30, max_lines=5, upper=True)
        textfit.draw_fit(d, f, (hb[0] + off, hb[1] + off, hb[2], hb[3]), fill=_rgb(text) + (215,), align="left", valign="middle")
        bb = textfit.draw_fit(d, f, (hb[0], hb[1], hb[2] - off, hb[3] - off), fill=_rgb(accent) + (225,), align="left", valign="middle")
        boxes["hook"] = (bb[0], bb[1], bb[2] + off, bb[3] + off)
    elif v == "hud":
        im = stage({**cfg, "chrome": "hud"}, W, H, brand, (l, int(H * 0.5), r, b)).convert("RGBA")
        d = ImageDraw.Draw(im)
        if st:
            sw = r - l - int(40 * k)
            sh = int(sw * 9 / 16)
            ph = _grey(C.cover_fit(still, sw, sh, 0.4), tint=((0, 10, 14), _rgb(accent)), contrast=1.2)
            sy = b - sh - int(20 * k)
            im.paste(ph, (l + int(20 * k), sy))
            _brackets(d, (l + int(6 * k), sy - int(14 * k), r - int(6 * k), sy + sh + int(14 * k)), _rgb(accent) + (255,), L=int(50 * k))
            hb = (l, t + int(70 * k), r, sy - int(50 * k))
        else:
            hb = (l, t + int(70 * k), r, b)
        _label(d, "TARGET LOCKED // " + (brand.get("name") or "").upper()[:16], (l, t), "IBMPlexMono-SemiBold.ttf", max(11, int(20 * k)),
               _rgb(accent), spacing=2)
        bb, _ = _block(d, hook, head, hb, max_size=int(84 * k), min_size=24, max_lines=5, fill=text, align="left", valign="bottom",
                       accent_words=acc, accent=accent)
        boxes["hook"] = bb
    elif v == "window":
        stg = stage({**cfg, "chrome": "window"}, W, H, brand, (l, int(H * 0.52), r, b - int(20 * k)))
        im = stg.convert("RGBA")
        d = ImageDraw.Draw(im)
        wy, wb = int(H * 0.52), b - int(20 * k)
        if st:
            ph = C.cover_fit(still, r - l, wb - wy, 0.4)
            _paste_round(im, ph, (l, wy), int(22 * k))
        else:
            d.rounded_rectangle((l, wy, r, wb), int(22 * k), fill=(250, 250, 252, 255))
        d = ImageDraw.Draw(im)
        tb = int(46 * k)
        d.rounded_rectangle((l, wy, r, wy + tb + int(20 * k)), int(22 * k), fill=(246, 246, 248, 255))
        d.rectangle((l, wy + tb, r, wy + tb + int(20 * k)), fill=(246, 246, 248, 255))
        if st:
            ph2 = C.cover_fit(still, r - l, wb - wy - tb, 0.4)
            im.paste(ph2, (l, wy + tb))
        for i, col in enumerate(((255, 95, 87), (254, 188, 46), (40, 200, 64))):
            cx = l + int((26 + i * 26) * k)
            d.ellipse((cx - 8 * k, wy + tb / 2 - 8 * k, cx + 8 * k, wy + tb / 2 + 8 * k), fill=col + (255,))
        bb, _ = _block(d, hook, bold, (l, t + int(60 * k), r, wy - int(50 * k)), max_size=int(96 * k), min_size=28, max_lines=4,
                       fill=text, align="left", valign="bottom", accent_words=acc, accent=accent)
        boxes["hook"] = bb
        logo_tl(d)
    elif v == "dossier":
        im = Image.new("RGBA", (W, H), _rgb(bgc) + (255,))
        d = ImageDraw.Draw(im)
        if st:
            sw, sh = r - l, int((b - t) * 0.42)
            ph = _grey(C.cover_fit(still, sw, sh, 0.4), contrast=1.3)
            sy = b - sh
            im.paste(ph, (l, sy))
            d.rectangle((l - 1, sy - 1, r + 1, b + 1), outline=(46, 50, 56, 255), width=2)
            d.ellipse((l + sw * 0.55, sy + sh * 0.2, l + sw * 0.85, sy + sh * 0.75), outline=_rgb(accent) + (255,), width=max(3, int(5 * k)))
            hb = (l, t + int(80 * k), r, sy - int(40 * k))
        else:
            hb = (l, t + int(80 * k), r, b)
        f_sz = max(12, int(22 * k))
        lab = "CASE FILE · " + (brand.get("name") or "").upper()
        d.rectangle((l, t, l + int(len(lab) * f_sz * 0.72) + int(20 * k), t + int(f_sz * 1.8)), fill=_rgb(accent) + (255,))
        _label(d, lab, (l + int(10 * k), t + int(f_sz * 0.35)), "IBMPlexMono-SemiBold.ttf", f_sz, _rgb(bgc), spacing=1)
        bb, _ = _block(d, hook, bold, hb, max_size=int(96 * k), min_size=26, max_lines=5, fill=text, align="left", valign="bottom",
                       accent_words=acc, accent=accent)
        boxes["hook"] = bb
    elif v == "countdown":
        if st:
            im.paste(Image.blend(st, Image.new("RGB", (W, H), _rgb(bgc)), 0.55))
        d = ImageDraw.Draw(im)
        cb_h = int(H * (0.16 if tall else 0.2))
        cb = (l, t + int(40 * k), r, t + int(40 * k) + cb_h)
        d.rectangle(cb, fill=_rgb(accent) + (255,))
        _block(d, "T-0", "IBMPlexMono-SemiBold.ttf", (cb[0] + int(30 * k), cb[1] + int(10 * k), cb[2] - int(30 * k), cb[3] - int(10 * k)),
               max_size=int(200 * k), min_size=30, max_lines=1, fill=on_acc, align="left")
        boxes["clock"] = cb
        bb, _ = _block(d, hook, bold, (l, cb[3] + int(60 * k), r, b - int(20 * k)), max_size=int(100 * k), min_size=28, max_lines=4,
                       fill=text, align="left", valign="top", accent_words=acc, accent=accent)
        boxes["hook"] = bb
    elif v == "terminal":
        im = stage({**cfg, "chrome": "terminal"}, W, H, brand, (l, t, r, b)).convert("RGBA")
        d = ImageDraw.Draw(im)
        _label(d, (brand.get("name") or "huhbyte").lower().replace(" ", "") + ".sys  [ok]", (l, t), "VT323-Regular.ttf",
               max(16, int(34 * k)), _rgb(brand.get("muted", "#8A5E00")), spacing=1)
        bb, _ = _block(d, "> " + hook, head, (l, t + int(90 * k), r - int(30 * k), b - int(60 * k)), max_size=int(150 * k), min_size=30,
                       max_lines=7, fill=text, align="left", valign="middle", upper=True, accent_words=acc, accent=accent)
        d.rectangle((min(bb[2] + int(14 * k), r - int(30 * k)), bb[3] - int(60 * k), min(bb[2] + int(44 * k), r), bb[3] - int(4 * k)),
                    fill=_rgb(text) + (255,))
        boxes["hook"] = bb
        sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for y in range(0, H, max(3, int(4 * k))):
            sd.line((0, y, W, y), fill=(0, 0, 0, 70))
        im.alpha_composite(sl)
    elif v == "sign":
        if st:
            im.paste(st)
            im = _vignette(im, 120)
        d = ImageDraw.Draw(im)
        f = textfit.fit(hook, "Inter-Bold.ttf", r - l - int(80 * k), int(H * 0.3), max_size=int(96 * k), min_size=26, max_lines=4)
        sh = f.height + int(80 * k)
        sb = (l, int(H * 0.42) - sh // 2, r, int(H * 0.42) + sh // 2)
        d.rounded_rectangle(sb, int(24 * k), fill=(31, 107, 69, 255))
        d.rounded_rectangle((sb[0] + int(9 * k), sb[1] + int(9 * k), sb[2] - int(9 * k), sb[3] - int(9 * k)), int(16 * k),
                            outline=(255, 255, 255, 255), width=max(3, int(5 * k)))
        bb = textfit.draw_fit(d, f, (sb[0] + int(40 * k), sb[1] + int(40 * k), sb[2] - int(40 * k), sb[3] - int(40 * k)), fill="#FFFFFF")
        boxes["hook"] = sb
        logo_tl(d)
    elif v == "candy":
        d = ImageDraw.Draw(im)
        stripe = max(30, int(70 * k))
        col2 = _mix(bgc, "#FFFFFF", 0.08)
        for i, x in enumerate(range(-H, W + H, stripe)):
            if i % 2:
                d.polygon([(x, 0), (x + stripe, 0), (x + stripe + H * 0.5, H), (x + H * 0.5, H)], fill=col2 + (255,))
        cx, cy = W // 2, int(H * 0.45)
        for a in range(0, 360, 15):  # sunburst
            r0 = int(max(W, H))
            p1 = (cx + r0 * math.cos(math.radians(a)), cy + r0 * math.sin(math.radians(a)))
            p2 = (cx + r0 * math.cos(math.radians(a + 7)), cy + r0 * math.sin(math.radians(a + 7)))
            d.polygon([(cx, cy), p1, p2], fill=_rgb(brand.get("muted", "#FFB870")) + (60,))
        stroke = max(5, int(12 * k))
        bb, _ = _block(d, hook, head, (l, int(H * 0.26), r, int(H * 0.66)), max_size=int(150 * k), min_size=30, max_lines=4, upper=True,
                       fill=accent, stroke=stroke, accent_words=acc, accent="#FFFFFF")
        boxes["hook"] = bb
        lab = "ROUND 1"
        f = textfit.fit(lab, "Figtree-ExtraBold.ttf", int(W * 0.5), int(70 * k), max_size=int(44 * k), min_size=14, max_lines=1)
        tw = int(textfit.font("Figtree-ExtraBold.ttf", f.size).getlength(lab))
        pb = (W // 2 - tw // 2 - int(30 * k), bb[1] - int(110 * k), W // 2 + tw // 2 + int(30 * k), bb[1] - int(110 * k) + int(f.size * 1.9))
        d.rounded_rectangle(pb, (pb[3] - pb[1]) // 2, fill=(255, 255, 255, 255), outline=(27, 21, 48, 255), width=max(3, int(5 * k)))
        textfit.draw_fit(d, f, pb, fill="#1B1530")
        boxes["label"] = pb
    elif v == "film":
        if st:
            im.paste(Image.blend(st, Image.new("RGB", (W, H), (0, 0, 0)), 0.45))
            im = _vignette(im, 170)
        d = ImageDraw.Draw(im)
        bar = int(H * 0.085)
        d.rectangle((0, 0, W, bar), fill=(0, 0, 0, 255))
        d.rectangle((0, H - bar, W, H), fill=(0, 0, 0, 255))
        lb = _label(d, (brand.get("name") or "").upper(), (0, 0), "Inter-SemiBold.ttf", max(12, int(22 * k)), (0, 0, 0, 0), spacing=10)
        lw = lb[2] - lb[0]
        _label(d, (brand.get("name") or "").upper(), ((W - lw) // 2, int(H * 0.36)), "Inter-SemiBold.ttf", max(12, int(22 * k)),
               _rgb(brand.get("muted", "#9A948A")), spacing=10)
        bb, _ = _block(d, hook, head, (l, int(H * 0.4), r, int(H * 0.64)), max_size=int(110 * k), min_size=28, max_lines=4,
                       fill=text, accent_words=acc, accent=accent, spacing=1.12)
        boxes["hook"] = bb
    else:
        return C.cover_card(path, W, H, brand, hook, still, label=label, accent_words=acc)
    im.convert("RGB").save(path)
    return boxes


# ================================================================================================== facts card
def facts(cfg: dict, path: str, W: int, H: int, brand: dict, title: str, facts_: list[str], still: Image.Image | None) -> dict:
    """The theme's facts card: the same content as compose.facts_card, in the theme's ground, type and accent."""
    C = _c()
    k = _k(W)
    l, t, r, b = C.safe_area(W, H)
    v = cfg.get("card")
    bgc, text, accent = brand["bg"], brand["text"], brand["accent"]
    boxes: dict = {}
    if cfg.get("fill") == "stage" or v in ("swiss", "hud", "terminal", "keynote", "studio", "window", "dossier"):
        chrome = {"keynote": "keynote", "studio": "studio", "swiss": "swiss", "hud": "hud", "window": "window", "dossier": "evidence",
                  "terminal": "terminal"}.get(v, cfg.get("chrome"))
        im = stage({**cfg, "chrome": chrome}, W, H, brand, (0, H, 1, H)).convert("RGBA")
    else:
        im = Image.new("RGBA", (W, H), _rgb(bgc) + (255,))
        if still is not None and v not in ("intertitle", "riso", "candy"):
            bgim = C.cover_fit(still, W, H).filter(ImageFilter.GaussianBlur(int(28 * k)))
            im.paste(Image.blend(bgim, Image.new("RGB", (W, H), _rgb(bgc)), 0.78))
    d = ImageDraw.Draw(im)
    align = "left" if v in ("swiss", "hud", "terminal", "dossier", "ledger", "countdown", "window", "kino", "sign") else "center"
    upper = v in ("kino", "terminal", "sting", "candy", "riso")
    if v == "intertitle":
        fr = (l + int(10 * k), t + int(30 * k), r - int(10 * k), b - int(30 * k))
        d.rectangle(fr, outline=_rgb(text) + (220,), width=max(3, int(4 * k)))
        l, t, r, b = fr[0] + int(50 * k), fr[1] + int(60 * k), fr[2] - int(50 * k), fr[3] - int(60 * k)
    if v == "ledger":
        d.rectangle((l, t, l + int(16 * k), b), fill=_rgb(accent) + (255,))
        l += int(40 * k)
    if v == "sting":
        d.rectangle((0, t, int(W * 0.6), t + int(H * 0.012) + 4), fill=_rgb(accent) + (255,))
    bb, _ = _block(d, title, brand["head_font"], (l, t + int(20 * k), r, t + int(20 * k) + int(H * 0.12)), max_size=int(104 * k),
                   max_lines=2, upper=upper or brand["head_font"] == "Anton.ttf", fill=accent if v in ("swiss", "candy", "riso") else text,
                   align=align)
    boxes["title"] = bb
    y = bb[3] + int(H * 0.045)
    n = max(1, len(facts_[:4]))
    slot = (b - y - int(20 * k)) // n
    body = brand.get("body_font") or "Poppins-Medium.ttf"
    for i, fct in enumerate(facts_[:4]):
        if align == "left":
            mark = f"{i + 1:02d}" if v in ("swiss", "hud", "terminal", "dossier", "countdown") else ""
            if mark:
                _label(d, mark, (l, y + int(6 * k)), "Inter-Bold.ttf" if v == "swiss" else "IBMPlexMono-SemiBold.ttf", max(12, int(30 * k)),
                       _rgb(accent), spacing=1)
            else:
                d.ellipse((l, y + int(14 * k), l + int(18 * k), y + int(32 * k)), fill=_rgb(accent))
            fb, _ = _block(d, fct, body, (l + int(70 * k if mark else 40 * k), y, r, y + slot - int(18 * k)), max_size=int(54 * k),
                           min_size=20, max_lines=4, align="left", valign="top", fill=text)
        else:
            fb, _ = _block(d, fct, body, (l, y, r, y + slot - int(30 * k)), max_size=int(54 * k), min_size=20, max_lines=4,
                           align="center", valign="top", fill=text)
            if i < n - 1:
                ly = y + slot - int(16 * k)
                d.line((W // 2 - int(50 * k), ly, W // 2 + int(50 * k), ly), fill=_rgb(accent) + (160,), width=2)
        boxes[f"fact{i}"] = fb
        y += slot
    if v == "terminal":
        sl = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sl)
        for yy in range(0, H, max(3, int(4 * k))):
            sd.line((0, yy, W, yy), fill=(0, 0, 0, 70))
        im.alpha_composite(sl)
    if v in ("intertitle", "film"):
        im = _vignette(im, 150)
    im.convert("RGB").save(path)
    return boxes
