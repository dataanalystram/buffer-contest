"""Brand kit v2 base layouts: the front card (carousel cover, thumbnail, Reel cover) and the end card for a kit that
carries a generated theme (studio/design_system.py). Four layouts, each a different composition, not a recolour:

  bold_news   upper-case condensed headline set huge, the accent as a solid band behind the kicker and a thick bar
  editorial   serif headline between thin rules, a tracked small-caps kicker, "1 / 5" folio instead of dots
  minimal     plain ground, a short accent line over a sans headline, an outlined kicker
  playful     accent blobs and a big rounded surface card with a filled pill kicker

Everything else comes from the kit: colours, fonts, case, logo, handle, CTA, end line and the theme's surface colour,
corner radius and headline scale. Pure Pillow, like studio/templates.py (which calls in here when kit.theme is set).
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

from . import templates as T


def _px(b: T.Brand, W: int) -> int:
    """The theme's corner radius at this frame's scale (radius is in px of a 1080-wide card)."""
    return int((b.radius or 0) * W / 1080 * 1.6)


def _inks(b: T.Brand):
    ink = b.text
    muted = T.mix(b.text, b.bg, 0.4)
    emph = b.accent if T.contrast(b.accent, b.bg) >= 2.2 else ink
    return ink, muted, emph


def _rule(d, x0, x1, y, b: T.Brand, w: int = 2) -> None:
    d.rectangle((x0, y, x1, y + w - 1), fill=T.mix(b.text, b.bg, 0.72))


def _grid(im: Image.Image, b: T.Brand) -> None:
    W, H = im.size
    d = ImageDraw.Draw(im)
    step = W // 12
    line = T.mix(b.bg, b.text, 0.08)
    for x in range(0, W, step):
        d.line((x, 0, x, H), fill=line, width=1)
    for y in range(0, H, step):
        d.line((0, y, W, y), fill=line, width=1)


def _blobs(im: Image.Image, b: T.Brand) -> None:
    W, H = im.size
    r = int(min(W, H) * 0.3)
    for (cx, cy, col, a) in ((int(W * 0.94), int(H * 0.04), b.accent, 230), (int(W * 0.02), int(H * 0.46), b.second, 90)):
        g = Image.new("L", (W, H), 0)
        ImageDraw.Draw(g).ellipse((cx - r, cy - r, cx + r, cy + r), fill=a)
        im.paste(Image.new("RGB", (W, H), col), (0, 0), g.filter(ImageFilter.GaussianBlur(6)))


def _tracked(d, xy, text: str, f, fill, sp: int) -> int:
    x, y = xy
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + sp
    return int(x - sp)


def _folio(im: Image.Image, b: T.Brand, n, m: int) -> None:
    W, H = im.size
    d = ImageDraw.Draw(im)
    _, muted, _ = _inks(b)
    f = b.body(int(min(W, H) * 0.024))
    y = H - m - f.size
    _rule(d, m, W - m, y - int(f.size * 0.9), b)
    if b.handle:
        d.text((m, y), "@" + b.handle, font=f, fill=muted)
    if n and n[1] > 1:
        t = f"{n[0]} / {n[1]}"
        d.text((W - m - d.textlength(t, font=f), y), t, font=f, fill=muted)
    if b.label:
        lf = b.body(int(min(W, H) * 0.018))
        t = T._clean(b.label, lf)
        d.text(((W - d.textlength(t, font=lf)) / 2, H - int(m * 0.45) - lf.size), t, font=lf, fill=muted)


# ================================================================================================== front cards
def front(b: T.Brand, ratio: str, kicker: str, title: str, sub: str = "", n=None) -> Image.Image:
    W, H, m, wide = T._frame(ratio)
    return {"bold_news": _front_bold, "editorial": _front_editorial, "playful": _front_playful}.get(b.layout, _front_minimal)(
        b, W, H, m, wide, kicker, title, sub, n)


def _front_bold(b, W, H, m, wide, kicker, title, sub, n):
    im = Image.new("RGB", (W, H), b.bg)
    if b.front_style == "grid":
        _grid(im, b)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    T._logo(im, b, int(W * (0.22 if not wide else 0.15)), (m, m), "bold")
    maxw = W - 2 * m - (int(W * 0.22) if wide else 0)
    hf, lines = T.fit_text(d, b.headline(title).upper(), b.head, int(S * 0.165 * b.scale), maxw, 4 if not wide else 3)
    lh = int(hf.size * 0.98)
    sf = b.body(int(S * 0.034))
    sub_lines = T.wrap(d, sub, sf, maxw)[:2] if sub else []
    bar = int(S * 0.016)
    block = lh * len(lines) + bar + int(S * 0.05) + int(sf.size * 1.4) * len(sub_lines)
    y = H - m - int(S * 0.08) - block
    if kicker:  # the accent as a solid band behind the kicker
        kf = b.body(int(S * 0.032), strong=True)
        t = T._clean(kicker.upper(), kf)
        kh = int(kf.size * 2.1)
        ky = y - kh - int(S * 0.035)
        d.rounded_rectangle((m, ky, m + int(d.textlength(t, font=kf)) + int(kf.size * 1.6), ky + kh), radius=_px(b, W) // 2, fill=b.accent)
        d.text((m + int(kf.size * 0.8), ky + (kh - kf.size) // 2 - int(kf.size * 0.1)), t, font=kf, fill=b.on_accent)
    for i, line in enumerate(lines):
        d.text((m, y + i * lh), line, font=hf, fill=emph if (i == len(lines) - 1 and len(lines) > 1) else ink)
    y += lh * len(lines) + int(S * 0.045)
    d.rectangle((m, y, m + int(W * 0.28), y + bar), fill=b.accent)
    y += bar + int(S * 0.03)
    for s in sub_lines:
        d.text((m, y), s, font=sf, fill=muted)
        y += int(sf.size * 1.4)
    T._footer(im, b, n, "bold", m)
    return im


def _front_editorial(b, W, H, m, wide, kicker, title, sub, n):
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    m = int(m * 1.15)
    lh_logo = T._logo(im, b, int(W * (0.18 if not wide else 0.12)), (m, m), "")
    if kicker:
        kf = b.body(int(S * 0.024), strong=True)
        t = T._clean(kicker.upper(), kf)
        sp = int(kf.size * 0.22)
        tw = sum(d.textlength(ch, font=kf) + sp for ch in t) - sp
        _tracked(d, (int(W - m - tw), m + max(0, (lh_logo - kf.size) // 2)), t, kf, emph, sp)
    top = m + max(lh_logo, int(S * 0.04)) + int(S * 0.035)
    _rule(d, m, W - m, top, b)
    maxw = W - 2 * m - (int(W * 0.2) if wide else 0)
    hf, lines = T.fit_text(d, title if not b.upper else b.headline(title), b.head, int(S * 0.118 * b.scale), maxw, 5 if not wide else 3)
    lh = int(hf.size * 1.12)
    sf = b.body(int(S * 0.036))
    sub_lines = T.wrap(d, sub, sf, int(maxw * 0.86))[:3] if sub else []
    block = lh * len(lines) + (int(S * 0.06) + int(sf.size * 1.5) * len(sub_lines) if sub_lines else 0)
    y = max(top + int(S * 0.08), (H - block) // 2 + int(S * 0.04))
    for line in lines:
        d.text((m, y), line, font=hf, fill=ink)
        y += lh
    if sub_lines:
        y += int(S * 0.03)
        d.rectangle((m, y, m + int(W * 0.08), y + max(2, int(S * 0.004))), fill=b.accent)
        y += int(S * 0.03)
        for s in sub_lines:
            d.text((m, y), s, font=sf, fill=muted)
            y += int(sf.size * 1.5)
    _folio(im, b, n, m)
    return im


def _front_minimal(b, W, H, m, wide, kicker, title, sub, n):
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    T._logo(im, b, int(W * (0.2 if not wide else 0.14)), (m, m), "")
    maxw = W - 2 * m - (int(W * 0.25) if wide else 0)
    hf, lines = T.fit_text(d, b.headline(title), b.head, int(S * 0.105 * b.scale), maxw, 4 if not wide else 3)
    lh = int(hf.size * (1.0 if b.upper else 1.1))
    sf = b.body(int(S * 0.034))
    sub_lines = T.wrap(d, sub, sf, maxw)[:3] if sub else []
    block = lh * len(lines) + (int(sf.size * 1.45) * len(sub_lines) + int(S * 0.035) if sub_lines else 0)
    y = H - m - int(S * 0.09) - block
    ky = y - int(S * 0.06)
    if kicker:  # an outlined kicker with the theme's corners
        kf = b.body(int(S * 0.026), strong=True)
        t = T._clean(kicker, kf)
        bb = d.textbbox((0, 0), t, font=kf)
        pw, ph = int(bb[2] + kf.size * 1.4), int((bb[3] - bb[1]) + kf.size * 0.9)
        ky = y - ph - int(S * 0.035)
        d.rounded_rectangle((m, ky, m + pw, ky + ph), radius=min(ph // 2, _px(b, W)), outline=T.mix(b.text, b.bg, 0.6), width=max(2, S // 400))
        d.text((m + int(kf.size * 0.7), ky + int(kf.size * 0.45) - bb[1]), t, font=kf, fill=ink)
    d.rectangle((m, ky - int(S * 0.045), m + int(W * 0.1), ky - int(S * 0.045) + max(4, S // 180)), fill=b.accent)
    for line in lines:
        d.text((m, y), line, font=hf, fill=ink)
        y += lh
    y += int(S * 0.035)
    for s in sub_lines:
        d.text((m, y), s, font=sf, fill=muted)
        y += int(sf.size * 1.45)
    T._footer(im, b, n, "plain", m)
    return im


def _front_playful(b, W, H, m, wide, kicker, title, sub, n):
    im = Image.new("RGB", (W, H), b.bg)
    _blobs(im, b)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    T._logo(im, b, int(W * (0.22 if not wide else 0.15)), (m, m), "")
    rad = max(_px(b, W), int(S * 0.04))
    cx0, cx1 = m, W - m
    maxw = cx1 - cx0 - 2 * int(S * 0.06)
    hf, lines = T.fit_text(d, b.headline(title), b.head, int(S * 0.1 * b.scale), maxw, 4 if not wide else 2)
    lh = int(hf.size * 1.08)
    sf = b.body(int(S * 0.032))
    sub_lines = T.wrap(d, sub, sf, maxw)[:2] if sub else []
    kf = b.body(int(S * 0.028), strong=True)
    pad = int(S * 0.06)
    kh = int(kf.size * 2.2) if kicker else 0
    inner = kh + (int(S * 0.03) if kicker else 0) + lh * len(lines) + (int(S * 0.025) + int(sf.size * 1.45) * len(sub_lines) if sub_lines else 0)
    cy1 = H - m - int(S * 0.08)
    cy0 = cy1 - inner - 2 * pad
    shadow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((cx0 + 8, cy0 + 14, cx1 + 8, cy1 + 14), radius=rad, fill=70)
    im.paste(Image.new("RGB", (W, H), T.mix(b.bg, (0, 0, 0), 0.5)), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((cx0, cy0, cx1, cy1), radius=rad, fill=b.surface)
    y = cy0 + pad
    if kicker:
        T._pill(d, (cx0 + pad, y), kicker, kf, b.accent, b.on_accent, pad=(int(kf.size * 0.9), int(kf.size * 0.55)))
        y += kh + int(S * 0.03)
    for line in lines:
        d.text((cx0 + pad, y), line, font=hf, fill=ink)
        y += lh
    y += int(S * 0.025)
    for s in sub_lines:
        d.text((cx0 + pad, y), s, font=sf, fill=muted)
        y += int(sf.size * 1.45)
    T._footer(im, b, n, "plain", m)
    return im


# ================================================================================================== end cards
def end(b: T.Brand, ratio: str, n=None) -> Image.Image:
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    if b.layout == "bold_news" and b.end_style == "grid":
        _grid(im, b)
    if b.layout == "playful":
        _blobs(im, b)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    if b.layout == "playful":
        r = max(_px(b, W), int(S * 0.04))
        d.rounded_rectangle((m, int(H * 0.16), W - m, H - int(H * 0.16)), radius=r, fill=b.surface)
    if b.layout == "editorial":
        _rule(d, m, W - m, int(H * 0.14), b)
        _rule(d, m, W - m, H - int(H * 0.14), b)
    lw = int(W * (0.42 if not wide else 0.28))
    lg = T.logo_for(b, "", lw, int(lw * 0.6))
    y = int(H * (0.26 if not wide else 0.2))
    if lg is not None:
        im.paste(lg, ((W - lg.width) // 2, y), lg)
        y += lg.height + int(S * 0.07)
    cta = b.headline(b.cta).upper() if b.layout == "bold_news" else (b.cta if b.layout == "editorial" and not b.upper else b.headline(b.cta))
    cf, lines = T.fit_text(d, cta, b.head, int(S * (0.13 if b.layout == "bold_news" else 0.1) * b.scale), W - 2 * m - int(S * 0.08), 2)
    for line in lines:
        d.text(((W - d.textlength(line, font=cf)) / 2, y), line, font=cf, fill=ink)
        y += int(cf.size * (1.0 if b.layout == "bold_news" else 1.12))
    y += int(S * 0.04)
    if b.handle:
        t = "@" + b.handle
        if b.layout == "editorial":
            hf = b.body(int(S * 0.032), strong=True)
            sp = int(hf.size * 0.18)
            tw = sum(d.textlength(ch, font=hf) + sp for ch in t.upper()) - sp
            _tracked(d, (int((W - tw) / 2), y), t.upper(), hf, emph, sp)
            y += int(hf.size * 2)
        elif b.layout == "minimal":
            hf = b.body(int(S * 0.04), strong=True)
            bb = d.textbbox((0, 0), t, font=hf)
            pw, ph = int(bb[2] + hf.size * 1.6), int((bb[3] - bb[1]) + hf.size * 1.0)
            x = (W - pw) // 2
            d.rounded_rectangle((x, y, x + pw, y + ph), radius=min(ph // 2, _px(b, W)), outline=b.accent, width=max(3, S // 300))
            d.text((x + int(hf.size * 0.8), y + int(hf.size * 0.5) - bb[1]), t, font=hf, fill=ink)
            y += ph + int(S * 0.04)
        else:
            hf = b.body(int(S * 0.046), strong=True)
            pw = d.textlength(t, font=hf) + 64
            _, ph = T._pill(d, (int((W - pw) / 2), y), t, hf, b.accent, b.on_accent, pad=(32, 18),
                            radius=None if b.layout == "playful" else _px(b, W) // 2)
            y += ph + int(S * 0.04)
    if b.end_line:
        lf = b.body(int(S * 0.03))
        for line in T.wrap(d, b.end_line, lf, W - 2 * m - int(S * 0.1))[:2]:
            d.text(((W - d.textlength(line, font=lf)) / 2, y), line, font=lf, fill=muted)
            y += int(lf.size * 1.4)
    if b.label:
        lf = b.body(int(S * 0.02))
        t = T._clean(b.label, lf)
        d.text(((W - d.textlength(t, font=lf)) / 2, H - int(m * 0.8) - lf.size), t, font=lf, fill=muted)
    return im
