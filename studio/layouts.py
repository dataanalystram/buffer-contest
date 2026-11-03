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
    if b.layout == "editorial" and b.variant == "masthead":
        return _front_masthead(b, W, H, m, wide, kicker, title, sub, n)
    if b.layout == "minimal" and b.variant == "bar":
        return _front_bar(b, W, H, m, wide, kicker, title, sub, n)
    return {"bold_news": _front_bold, "editorial": _front_editorial, "playful": _front_playful}.get(b.layout, _front_minimal)(
        b, W, H, m, wide, kicker, title, sub, n)


def _centre(d, y, text, f, fill, W) -> None:
    d.text(((W - d.textlength(text, font=f)) / 2, y), text, font=f, fill=fill)


def _tracked_w(d, text, f, sp) -> int:
    return int(sum(d.textlength(ch, font=f) + sp for ch in text) - sp) if text else 0


def _surface_ink(b: T.Brand):
    """Ink and muted ink on the theme's surface block."""
    sf = b.surface or b.bg
    ink = b.text if T.contrast(b.text, sf) >= 4.5 else ((17, 17, 20) if T._lum(sf) > 0.4 else (255, 255, 255))
    return ink, T.mix(ink, sf, 0.4)


def _plain(b: T.Brand, size: int):
    """A plain sans for small UI text: the body face, or the headline face when the body is a serif."""
    return b.head(size) if "Serif" in b.body_file or "Garamond" in b.body_file else b.body(size, strong=True)


def _bar_h(S: int) -> int:
    return int(S * 0.085)


def _front_masthead(b, W, H, m, wide, kicker, title, sub, n):
    """Editorial B: a centred masthead (logo, double rule, tracked kicker) and the headline on the brand's tinted surface
    block, centred, with the accent as a short rule (Wispr Flow: cream ground, lavender block, dark-green accent)."""
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    S = min(W, H)
    m = int(m * 1.1)
    lw = int(W * (0.2 if not wide else 0.13))
    lg = T.logo_for(b, "", *T.logo_box(b.logo.width, b.logo.height, lw), grow=True) if b.logo is not None else None
    y = m
    if lg is not None:
        im.paste(lg, ((W - lg.width) // 2, y), lg)
        y += lg.height
    else:
        f = b.head(int(S * 0.05))
        t = T._clean(b.name or "", f)
        _centre(d, y, t, f, ink, W)
        y += int(f.size * 1.1)
    y += int(S * 0.03)
    _rule(d, m, W - m, y, b)
    _rule(d, m, W - m, y + max(5, S // 160), b)
    y += max(5, S // 160) + int(S * 0.028)
    if kicker:
        kf = b.body(int(S * 0.022), strong=True)
        t = T._clean(kicker.upper(), kf)
        sp = int(kf.size * 0.24)
        _tracked(d, (int((W - _tracked_w(d, t, kf, sp)) / 2), y), t, kf, emph, sp)
        y += int(kf.size * 1.6)
    # the surface block
    pad = int(S * 0.065)
    bx0, bx1 = m, W - m
    maxw = bx1 - bx0 - 2 * pad
    hf, lines = T.fit_text(d, title if not b.upper else b.headline(title), b.head, int(S * 0.1 * b.scale), maxw, 4 if not wide else 3)
    lh = int(hf.size * 1.12)
    sf = b.body(int(S * 0.032))
    sub_lines = T.wrap(d, sub, sf, int(maxw * 0.9))[:3] if sub else []
    inner = lh * len(lines) + (int(S * 0.07) + int(sf.size * 1.5) * len(sub_lines) if sub_lines else 0)
    foot = int(S * 0.1)
    by1 = H - m - foot
    by0 = max(y + int(S * 0.04), by1 - inner - 2 * pad - int(S * 0.04))
    rad = max(int(S * 0.02), min(int(S * 0.06), _px(b, W)))
    d.rounded_rectangle((bx0, by0, bx1, by1), radius=rad, fill=b.surface)
    sink, smuted = _surface_ink(b)
    yy = by0 + ((by1 - by0) - inner) // 2
    for line in lines:
        _centre(d, yy, line, hf, sink, W)
        yy += lh
    if sub_lines:
        yy += int(S * 0.025)
        ar = b.accent if T.contrast(b.accent, b.surface) >= 2 else sink
        d.rectangle((W // 2 - int(W * 0.04), yy, W // 2 + int(W * 0.04), yy + max(3, int(S * 0.005))), fill=ar)
        yy += int(S * 0.045)
        for s_ in sub_lines:
            _centre(d, yy, s_, sf, smuted, W)
            yy += int(sf.size * 1.5)
    _folio_centre(im, b, n, m)
    return im


def _folio_centre(im, b, n, m):
    W, H = im.size
    d = ImageDraw.Draw(im)
    _, muted, _ = _inks(b)
    S = min(W, H)
    f = b.body(int(S * 0.024))
    y = H - m - f.size
    parts = (["@" + b.handle] if b.handle else []) + ([f"{n[0]} / {n[1]}"] if n and n[1] > 1 else [])
    if parts:
        _centre(d, y, "  \u00b7  ".join(parts) if T._has(f, "\u00b7") else "   ".join(parts), f, muted, W)
    if b.label:
        lf = b.body(int(S * 0.018))
        t = T._clean(b.label, lf)
        _centre(d, H - int(m * 0.45) - lf.size, t, lf, muted, W)


def _top_bar(im, b, W, S, left: str = "", right: str = "") -> int:
    """Minimal B: a solid accent bar across the top (the Hacker News header), small bold text in it. Returns its height."""
    d = ImageDraw.Draw(im)
    h = _bar_h(S)
    d.rectangle((0, 0, W, h), fill=b.accent)
    f = _plain(b, int(S * 0.026))
    m = int(S * 0.05)
    if left:
        t = T._clean(left, f)
        d.text((m, (h - f.size) // 2 - int(f.size * 0.08)), t, font=f, fill=b.on_accent)
    if right:
        t = T._clean(right, f)
        d.text((W - m - d.textlength(t, font=f), (h - f.size) // 2 - int(f.size * 0.08)), t, font=f, fill=b.on_accent)
    return h


def _front_bar(b, W, H, m, wide, kicker, title, sub, n):
    """Minimal B: the accent as a solid bar across the top with the kicker in it, the logo under it, a plain sans
    headline set high and left, a thin rule and the handle at the foot (Y Combinator / Hacker News)."""
    im = Image.new("RGB", (W, H), b.bg)
    S = min(W, H)
    bar = _top_bar(im, b, W, S, kicker or "", f"{n[0]} / {n[1]}" if n and n[1] > 1 else "")
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    y = bar + int(S * 0.055)
    lh_logo = T._logo(im, b, int(W * (0.2 if not wide else 0.14)), (m, y), "")
    y += max(lh_logo, int(S * 0.03)) + int(S * 0.07)
    maxw = W - 2 * m - (int(W * 0.22) if wide else 0)
    hf, lines = T.fit_text(d, b.headline(title), b.head, int(S * 0.1 * b.scale), maxw, 5 if not wide else 3)
    lh = int(hf.size * 1.12)
    sfz = b.body(int(S * 0.034))
    blk = lh * len(lines) + (int(S * 0.035) + int(sfz.size * 1.45) * len(T.wrap(d, sub, sfz, maxw)[:3]) if sub else 0)
    y = max(y, y + (H - m - int(S * 0.08) - y - blk) * 2 // 5)  # set high, not stranded at the top
    for line in lines:
        d.text((m, y), line, font=hf, fill=ink)
        y += lh
    if sub:
        sf = b.body(int(S * 0.034))
        y += int(S * 0.035)
        for s_ in T.wrap(d, sub, sf, maxw)[:3]:
            d.text((m, y), s_, font=sf, fill=muted)
            y += int(sf.size * 1.45)
    f = _plain(b, int(S * 0.026))
    fy = H - m - f.size
    d.rectangle((m, fy - int(S * 0.03), W - m, fy - int(S * 0.03) + max(2, S // 400)), fill=T.mix(b.text, b.bg, 0.8))
    if b.handle:
        d.text((m, fy), "@" + b.handle, font=f, fill=muted)
    if b.label:
        lf = b.body(int(S * 0.018))
        t = T._clean(b.label, lf)
        d.text((W - m - d.textlength(t, font=lf), fy + (f.size - lf.size)), t, font=lf, fill=muted)
    return im


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
    if b.layout == "editorial" and b.variant == "masthead":
        d.rounded_rectangle((m, int(H * 0.16), W - m, H - int(H * 0.16)), radius=max(int(S * 0.03), min(int(S * 0.06), _px(b, W))),
                            fill=b.surface)
        ink, muted = _surface_ink(b)
    elif b.layout == "editorial":
        _rule(d, m, W - m, int(H * 0.14), b)
        _rule(d, m, W - m, H - int(H * 0.14), b)
    if b.layout == "minimal" and b.variant == "bar":
        _top_bar(im, b, W, S)
        d = ImageDraw.Draw(im)
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


# ================================================================================================== quote (square post)
def quote(b: T.Brand, ratio: str, text: str, speaker: str, role: str = "", n=None) -> Image.Image:
    """The pulled-quote card in the theme's layout and variant. Laid out from the bottom up: the footer, then the
    speaker block, and the quote is fitted to the room above them, so a long name or role never runs into the handle."""
    W, H, m, wide = T._frame(ratio)
    S = min(W, H)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    ink, muted, emph = _inks(b)
    lay, var = b.layout, b.variant
    centred = lay == "editorial" and var == "masthead"
    if lay == "playful":
        _blobs(im, b)
        d = ImageDraw.Draw(im)
    top = m
    if lay == "minimal" and var == "bar":
        top = _top_bar(im, b, W, S, role or "", "") + int(S * 0.05)
        role = ""  # the role sits in the bar
    elif lay == "editorial" and var != "masthead":
        m = int(m * 1.1)
    # logo
    lw = int(W * (0.18 if not wide else 0.12))
    if centred and b.logo is not None:
        lg = T.logo_for(b, "", *T.logo_box(b.logo.width, b.logo.height, lw), grow=True)
        im.paste(lg, ((W - lg.width) // 2, top), lg)
        lh_logo = lg.height
    else:
        lh_logo = T._logo(im, b, lw, (m, top), "")
    y0 = top + max(lh_logo, int(S * 0.03)) + int(S * 0.05)
    # footer + speaker block, bottom up
    ff = b.body(int(S * 0.026), strong=True)
    foot_y = H - m - ff.size
    spf = b.body(int(S * 0.038), strong=True) if lay != "editorial" else b.head(int(S * 0.04))
    rf = b.body(int(S * 0.028))
    sp_lines = T.wrap(d, T._clean(speaker or "", spf), spf, W - 2 * m)[:2]
    role_lines = T.wrap(d, T._clean(role or "", rf), rf, W - 2 * m)[:2] if role else []
    gap = int(S * 0.045)
    block = int(spf.size * 1.25) * len(sp_lines) + (int(rf.size * 0.4) + int(rf.size * 1.3) * len(role_lines) if role_lines else 0)
    sp_y = foot_y - gap - block
    rule_y = sp_y - int(S * 0.04)
    # the quote mark and the quote
    qmark = "\u201c"
    qf = (b.head if lay == "editorial" else (lambda s: b.body(s, strong=True)))(int(S * (0.2 if lay == "editorial" else 0.24)))
    if not T._has(qf, qmark):
        qmark = '"'
    q_top = y0
    qbb = d.textbbox((0, 0), qmark, font=qf)
    qh = qbb[3] - qbb[1]
    if lay != "playful":
        qx = (W - (qbb[2] - qbb[0])) // 2 - qbb[0] if centred else m - qbb[0]
        d.text((qx, q_top - qbb[1]), qmark, font=qf, fill=emph)
        q_top += qh + int(S * 0.02)
    mk = (lambda s: b.head(s)) if not b.upper else (lambda s: b.body(s, strong=True))
    pad = int(S * 0.06) if (centred or lay == "playful") else 0
    maxw = W - 2 * m - 2 * pad
    room = rule_y - int(S * 0.04) - q_top - 2 * pad
    max_l = 6 if not wide else 4
    tf, lines = T.fit_text(d, text, mk, int(S * (0.075 if lay != "bold_news" else 0.085)), maxw, max_l, min_size=24)
    while len(lines) * int(tf.size * 1.18) > room and tf.size > 24:
        tf, lines = T.fit_text(d, text, mk, int(tf.size * 0.92), maxw, max_l, min_size=24)
    qtext_h = len(lines) * int(tf.size * 1.18)
    body_ink = ink
    if centred or lay == "playful":  # the quote sits on the surface block
        bx0, bx1 = m, W - m
        q_top += max(0, (rule_y - int(S * 0.04) - q_top - qtext_h - 2 * pad) // 3)  # the block sits a little above centre
        by0, by1 = q_top - int(S * 0.01), q_top + qtext_h + 2 * pad
        rad = max(int(S * 0.03), min(int(S * 0.06), _px(b, W)))
        if lay == "playful":
            sh = Image.new("L", (W, H), 0)
            ImageDraw.Draw(sh).rounded_rectangle((bx0 + 8, by0 + 14, bx1 + 8, by1 + 14), radius=rad, fill=70)
            im.paste(Image.new("RGB", (W, H), T.mix(b.bg, (0, 0, 0), 0.5)), (0, 0), sh.filter(ImageFilter.GaussianBlur(18)))
            d = ImageDraw.Draw(im)
        d.rounded_rectangle((bx0, by0, bx1, by1), radius=rad, fill=b.surface)
        body_ink = _surface_ink(b)[0]
        q_top = by0 + pad
        rule_y = max(rule_y, by1 + int(S * 0.04))
    yy = q_top
    for line in lines:
        x = (W - d.textlength(line, font=tf)) / 2 if centred else m + pad
        d.text((x, yy), line, font=tf, fill=body_ink)
        yy += int(tf.size * 1.18)
    # accent rule, speaker, role
    rw = int(W * 0.08)
    rx = (W - rw) // 2 if centred else m
    rule_y = max(rule_y, yy + int(S * 0.03))
    d.rectangle((rx, rule_y, rx + rw, rule_y + max(4, S // 200)), fill=b.accent)
    sp_y = max(sp_y, rule_y + int(S * 0.035))
    for line in sp_lines:
        x = (W - d.textlength(line, font=spf)) / 2 if centred else m
        d.text((x, sp_y), line, font=spf, fill=ink)
        sp_y += int(spf.size * 1.25)
    if role_lines:
        sp_y += int(rf.size * 0.4)
        for line in role_lines:
            x = (W - d.textlength(line, font=rf)) / 2 if centred else m
            d.text((x, sp_y), line, font=rf, fill=muted)
            sp_y += int(rf.size * 1.3)
    # footer: always below the speaker block
    if centred:
        _folio_centre(im, b, n, m)
    elif lay == "editorial":
        _folio(im, b, n, m)
    else:
        T._footer(im, b, n, "plain", m)
    return im
