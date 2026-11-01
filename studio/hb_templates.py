"""The HuhByte AI post formats as brand-kit templates (any brand; HuhByte AI is the sample).

Ported from the owner's HuhByte pipeline (dataanalystram/Huhbyte.ai, same owner: code/hb.py cover_slide / text_slide /
facts_slide / follow_slide, automation/weekly_recap.py render_cover / story_layout / render_cta, docs/DESIGN_SYSTEM.md,
docs/GITHUB_PILLAR.md), redrawn on studio.templates.Brand so every colour, font, logo and @handle comes from the
person's brand kit, at 4:5, 9:16, 1:1 and 16:9, with every text block auto-fitted.

  editorial_cover    hero photo + circular product inset + condensed headline with accent keywords + divider + logo
  text_slides        the editorial cover, then plain-words text slides with a rounded image panel, end card last
  launch_reel        launch cover + "what it does" facts slide (+ the real launch video as a Reel with the Follow end card)
  weekly_recap       "DO YOU REALIZE WHAT JUST HAPPENED IN AI IN THE LAST 7 DAYS?" cover, one slide per story, save card
  midweek_recap      the same, Monday to Wednesday, three stories
  github_repo        the GitHub pillar: hook cover with a repo card (stars, licence, language), 3 plain facts, the link
  yc_launch          one YC startup: batch tag, what it does, founders and funding
  viral_trend        a viral clip with the creator credited on every slide (+ the clip as a Reel with on-screen credit)
  editorial_story    one company / launch: hero cover, one idea per slide, founders, Follow (studio/editorial.py)

Rules kept from the HuhByte docs: the accent colour only on the key words of a headline and on the @handle; every carousel
ends with the Follow slide (recaps: Follow + SAVE THIS, their only CTA); a third-party clip always carries its credit.
"""
from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw, ImageFilter

from . import templates as T

RECAP_HEADLINE = "Do you realize what just happened in AI in the last 7 days?"
RECAP_ACCENT = {"AI", "7", "DAYS"}
MIDWEEK_HEADLINE = "Mid-week: 3 things that just happened in AI"


# ---------------------------------------------------------------------------------------------------- helpers
def _img(v) -> Image.Image | None:
    if isinstance(v, Image.Image):
        return v.convert("RGB")
    if isinstance(v, (bytes, bytearray)):
        return T._open(bytes(v))
    return None


def _norm(w: str) -> str:
    return re.sub(r"[^\w$%]", "", w).upper()


def _auto_accent(text: str) -> set[str]:
    """No accent words given: the figures and the longest proper noun, like hb.py's highlight picks."""
    ws = text.split()
    out = {_norm(w) for w in ws if re.search(r"\d", w)}
    caps = [w for w in ws[1:] if w[:1].isupper() and len(_norm(w)) > 2]
    if caps and len(out) < 2:
        out.add(_norm(max(caps, key=len)))
    return {w for w in out if w}


def headline(d, b: T.Brand, text: str, box, accent=None, start=None, max_lines=4, align="center", min_size=26) -> int:
    """Condensed headline with accent keywords (hb.headline / weekly_recap draw_headline). Returns the bottom y."""
    x0, y0, x1, y1 = box
    acc = {_norm(a) for a in (accent if accent is not None else _auto_accent(text))}
    t = b.headline(text)
    size = start or int((x1 - x0) * 0.13)
    while True:
        f = b.head(size)
        lines = T.wrap(d, t, f, x1 - x0)
        lh = int(size * (1.04 if b.upper else 1.12))
        if (len(lines) <= max_lines and lh * len(lines) <= y1 - y0) or size <= min_size:
            break
        size = int(size * 0.93)
    lines = lines[:max_lines]
    y = y0 + max(0, (y1 - y0 - lh * len(lines)) // 2)
    for ln in lines:
        lw = d.textlength(ln, font=f)
        x = x0 if align == "left" else x0 + (x1 - x0 - lw) / 2
        words = ln.split(" ")
        for i, wd in enumerate(words):
            k = _norm(wd)
            pair = (k + " " + _norm(words[i + 1])) if i + 1 < len(words) else ""
            on = k in acc or pair in acc or (i and (_norm(words[i - 1]) + " " + k) in acc)
            d.text((x, y), wd, font=f, fill=b.accent if on else b.text)
            x += d.textlength(wd + " ", font=f)
        y += lh
    return y


def _fit_line(d, text, make_font, start: int, maxw: int, min_size: int = 10):
    """One line that always fits: shrink until it does (URLs and repo names don't wrap)."""
    size = start
    while size > min_size and d.textlength(text, font=make_font(size)) > maxw:
        size = int(size * 0.93)
    return make_font(size)


def _badge(d, b: T.Brand, n, W, m):
    if not n or n[1] <= 1:
        return
    f = b.body(int(min(W, 1350) * 0.03))
    t = f"{n[0]}/{n[1]}"
    tw = d.textlength(t, font=f)
    h = int(f.size * 1.7)
    d.rounded_rectangle((W - m - tw - f.size * 1.1, m * 0.55, W - m, m * 0.55 + h), h // 2, fill=T.mix(b.bg, b.text, 0.16))
    d.text((W - m - tw - f.size * 0.55, m * 0.55 + (h - f.size * 1.2) / 2), t, font=f, fill=b.text)


def _divider(canvas, d, b: T.Brand, y, W):
    """hb.divider: two white rules with the logo between them."""
    lh = max(28, int(W * 0.055))
    lg = T.logo_for(b, "glow", int(lh * 3.2), lh)
    lw = lg.width if lg else int(d.textlength(b.name or "", font=b.head(lh)))
    mid = W // 2
    d.line((0, y, mid - lw // 2 - 24, y), fill=b.text, width=3)
    d.line((mid + lw // 2 + 24, y, W, y), fill=b.text, width=3)
    if lg:
        canvas.paste(lg, (mid - lg.width // 2, y - lg.height // 2), lg)
    elif b.name:
        f = b.head(int(lh * 0.8))
        d.text((mid - lw // 2, y - f.size // 2), b.headline(b.name), font=f, fill=b.text)


def _circle(canvas, img: Image.Image, D: int, xy):
    m = Image.new("L", (D, D), 0)
    ImageDraw.Draw(m).ellipse((0, 0, D, D), fill=255)
    ring = Image.new("RGBA", (D + 16, D + 16), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, D + 15, D + 15), fill="white")
    canvas.paste(ring, (xy[0] - 8, xy[1] - 8), ring)
    canvas.paste(T._cover(img, D, D), xy, m)


def _hero_ground(b: T.Brand, w: int, h: int, hero) -> Image.Image:
    im = _img(hero) or b.front_img
    if im is not None:
        return T._cover(im, w, h)
    return T._ground(w, h, b, "glow")


def _fade(canvas, x0, y0, w, h, color, start=0.62):
    g = Image.new("L", (w, h))
    gd = ImageDraw.Draw(g)
    for y in range(h):
        gd.line((0, y, w, y), fill=int(255 * max(0.0, (y - h * start) / (h * (1 - start))) ** 1.2))
    canvas.paste(Image.new("RGB", (w, h), color), (x0, y0), g)


def _swipe(d, b: T.Brand, W, H, m):
    f = b.body(int(min(W, H) * 0.026), strong=True)
    t = "SWIPE"
    tw = d.textlength(t, font=f)
    ax = W - m - int(f.size * 1.9)
    ay = H - m * 0.7
    d.text((ax - tw - f.size * 0.6, ay - f.size * 0.7), t, font=f, fill=b.accent)
    d.line((ax, ay, ax + f.size * 1.3, ay), fill=b.accent, width=4)
    d.polygon([(ax + f.size * 1.7, ay), (ax + f.size * 1.2, ay - f.size * 0.35), (ax + f.size * 1.2, ay + f.size * 0.35)], fill=b.accent)


def _handle_footer(canvas, d, b: T.Brand, W, H, m, credit=""):
    f = b.body(int(min(W, H) * 0.024), strong=True)
    y = H - m * 0.7 - f.size * 0.7
    hx = W - m
    if b.handle:
        t = "@" + b.handle
        hx = W - m - d.textlength(t, font=f)
        d.text((hx, y), t, font=f, fill=b.accent)
    lg = T.logo_for(b, "glow", int(W * 0.14), int(f.size * 1.6))
    x = m
    if lg and not credit:
        canvas.paste(lg, (m, int(y + f.size * 0.6 - lg.height / 2)), lg)
    if credit:
        cf = b.body(int(min(W, H) * 0.022))
        t = T._clean(credit, cf)
        while d.textlength(t, font=cf) > hx - x - 30 and len(t) > 8:
            t = t[:-2]
        d.text((x, y), t, font=cf, fill=T.mix(b.text, b.bg, 0.4))


def _panel(canvas, img, box, r=28, b: T.Brand | None = None, word=""):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w < 20 or h < 20:
        return
    src = _img(img)
    if src is None and b is not None:  # no image: a brand card (glow + logo or the tag), never an empty hole
        pic = T._ground(w, h, b, "glow")
        dd = ImageDraw.Draw(pic)
        lg = T.logo_for(b, "glow", int(w * 0.42), int(h * 0.3))
        if word and len(word) <= 14:
            f = _fit_line(dd, b.headline(word), b.head, int(h * 0.36), int(w * 0.84))
            t = b.headline(word)
            bb = dd.textbbox((0, 0), t, font=f)
            dd.text(((w - (bb[2] - bb[0])) / 2 - bb[0], (h - (bb[3] - bb[1])) / 2 - bb[1]), t, font=f,
                    fill=T.mix(b.accent, b.bg, 0.15))
        elif lg is not None:
            pic.paste(lg, ((w - lg.width) // 2, (h - lg.height) // 2), lg)
    else:
        pic = T._cover(src, w, h) if src is not None else Image.new("RGB", (w, h), (30, 30, 34))
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), r, fill=255)
    canvas.paste(pic, (x0, y0), m)


def _bullets(d, b: T.Brand, items, box, size) -> int:
    x0, y0, x1, y1 = box
    for _ in range(8):
        f = b.body(size)
        lh = int(size * 1.36)
        y = y0
        rows = []
        for it in items:
            ls = T.wrap(d, it, f, x1 - x0 - int(size * 1.2))
            rows.append(ls)
            y += lh * len(ls) + int(size * 0.45)
        if y <= y1 or size <= 22:
            break
        size = int(size * 0.92)
    y = y0
    for ls in rows:
        dot = int(size * 0.38)
        d.ellipse((x0, y + size * 0.42, x0 + dot, y + size * 0.42 + dot), fill=b.accent)
        for ln in ls:
            d.text((x0 + int(size * 1.2), y), ln, font=f, fill=b.text)
            y += lh
        y += int(size * 0.45)
    return y


# ---------------------------------------------------------------------------------------------------- slides
def editorial_cover(b: T.Brand, ratio: str, title: str, hero=None, inset=None, accent=None, sub: str = "", n=None,
                    label: str = "", swipe: bool = True) -> Image.Image:
    """hb.cover_slide in the brand: hero band, circular inset with a white ring, n/total badge, logo divider, headline."""
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    if wide:  # 16:9: the hero fills the left half, the words sit on the right
        hw = W // 2
        im.paste(_hero_ground(b, hw, H, hero), (0, 0))
        g = Image.new("L", (hw, H))
        gd = ImageDraw.Draw(g)
        for x in range(hw):
            gd.line((x, 0, x, H), fill=int(255 * max(0.0, (x - hw * 0.6) / (hw * 0.4)) ** 1.2))
        im.paste(Image.new("RGB", (hw, H), b.bg), (0, 0), g)
        d = ImageDraw.Draw(im)
        if _img(inset) is not None:
            D = int(H * 0.3)
            _circle(im, _img(inset), D, (m, m))
        box = (hw + m // 2, int(H * 0.2), W - m, H - int(m * 1.6))
        if label:
            kf = b.body(int(H * 0.034), strong=True)
            T._pill(d, (box[0], int(H * 0.08)), label.upper(), kf, b.accent, b.on_accent)
        headline(d, b, title, box, accent, start=int(H * 0.13), max_lines=4, align="left")
    else:
        ph = int(H * (0.52 if H / W > 1.5 else (0.58 if H > W else 0.5)))
        im.paste(_hero_ground(b, W, ph, hero), (0, 0))
        _fade(im, 0, 0, W, ph, b.bg)
        d = ImageDraw.Draw(im)
        if _img(inset) is not None:
            D = int(W * 0.3)
            _circle(im, _img(inset), D, (int(m * 0.6), int(H * 0.09)))
        _divider(im, d, b, ph - 10, W)
        top = ph + int(W * 0.07)
        if label:
            kf = b.body(int(W * 0.03), strong=True)
            lw = d.textlength(label.upper(), font=kf) + 44
            T._pill(d, (int((W - lw) / 2), top), label.upper(), kf, b.accent, b.on_accent)
            top += int(kf.size * 2.4)
        sub_h = int(W * 0.06) if sub else 0
        bot = H - int(m * 1.5) - sub_h
        y = headline(d, b, title, (m, top, W - m, bot), accent, start=int(W * 0.105), max_lines=4)
        if sub:
            sf = b.body(int(W * 0.03))
            for ln in T.wrap(d, sub, sf, W - 2 * m)[:1]:
                d.text(((W - d.textlength(ln, font=sf)) / 2, min(y + int(W * 0.025), H - int(m * 1.5) - sf.size)), ln, font=sf,
                       fill=T.mix(b.text, b.bg, 0.25))
    _badge(d, b, n, W, m)
    if swipe and n and n[1] > 1:
        _swipe(d, b, W, H, m)
    return im


def text_slide(b: T.Brand, ratio: str, text: str, image=None, n=None, title: str = "") -> Image.Image:
    """hb.text_slide: plain words at the top, a rounded image panel under them, logo + handle footer."""
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    _badge(d, b, n, W, m)
    tw = (W // 2 - m) if wide else W - 2 * m
    y = int(m * 1.6)
    if title:
        y = headline(d, b, title, (m, y, m + tw, y + int(H * 0.16)), None, start=int(min(W, H) * 0.08), max_lines=2,
                     align="left") + int(m * 0.3)
    size = int(min(W, H) * 0.046)
    floor = H - int(m * 1.6) if wide else int(H * 0.5)
    while True:
        f = b.body(size)
        lines = T.wrap(d, text, f, tw)
        if y + int(size * 1.4) * len(lines) <= floor or size < 24:
            break
        size = int(size * 0.92)
    for ln in lines:
        d.text((m, y), ln, font=f, fill=b.text)
        y += int(size * 1.4)
    if wide:
        _panel(im, image, (W // 2 + m // 2, int(m * 1.6), W - m, H - int(m * 1.6)), b=b)
    else:
        _panel(im, image, (int(m * 0.3), y + int(m * 0.5), W - int(m * 0.3), H - int(m * 1.5)), b=b)
    _handle_footer(im, d, b, W, H, m)
    return im


def facts_slide(b: T.Brand, ratio: str, title: str, bullets: list[str], image=None, n=None, accent=None) -> Image.Image:
    """hb.facts_slide: condensed title with accent words, accent-dot bullets in plain words, an image panel below."""
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    _badge(d, b, n, W, m)
    tw = (W // 2) if wide else W - 2 * m
    has_img = _img(image) is not None
    top = int(m * 1.5)
    if not has_img:  # calm and centred: measure the block, then place it in the middle of the free space
        probe = ImageDraw.Draw(Image.new("RGB", (W, H)))
        hy = headline(probe, b, title, (m, 0, m + tw, int(H * 0.15)), accent, start=int(min(W, H) * 0.1), max_lines=2, align="left")
        by = _bullets(probe, b, bullets[:4], (m, hy + int(m * 0.35), m + tw, H - int(m * 1.6) - top), int(min(W, H) * 0.052))
        top = max(top, (H - by) // 2 - int(m * 0.3))
    y = headline(d, b, title, (m, top, m + tw, top + int(H * 0.15)), accent, start=int(min(W, H) * 0.1),
                 max_lines=2, align="left") + int(m * 0.35)
    floor = H - int(m * 1.6) if (wide or not has_img) else int(H * 0.62)
    y = _bullets(d, b, bullets[:4], (m, y, m + tw, floor), int(min(W, H) * 0.052))
    if wide:
        if _img(image) is not None:
            _panel(im, image, (m + tw + m // 2, int(m * 1.5), W - m, H - int(m * 1.6)))
    elif _img(image) is not None and H - y - int(m * 1.6) > H * 0.18:
        _panel(im, image, (int(m * 0.3), y + int(m * 0.3), W - int(m * 0.3), H - int(m * 1.5)))
    _handle_footer(im, d, b, W, H, m)
    return im


def story_slide(b: T.Brand, ratio: str, s: dict, n=None) -> Image.Image:
    """weekly_recap.story_layout: logo + n/total, headline with accent words, body, the media (16:9), credit + @handle."""
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    lg = T.logo_for(b, "glow", int(W * 0.16), int(min(W, H) * 0.045))
    if lg:
        im.paste(lg, (m, int(m * 0.55)), lg)
    _badge(d, b, n, W, m)
    title, body = s.get("title", ""), s.get("body", "")
    if wide:
        tb = (m, int(m * 1.7), W // 2, H - int(m * 1.6))
        y = headline(d, b, title, (tb[0], tb[1], tb[2], tb[1] + int(H * 0.42)), s.get("accent"), start=int(H * 0.09),
                     max_lines=3, align="left")
        bf = b.body(int(H * 0.04))
        for ln in T.wrap(d, body, bf, tb[2] - tb[0])[:4]:
            d.text((m, y + 16), ln, font=bf, fill=T.mix(b.text, b.bg, 0.2))
            y += int(bf.size * 1.38)
        _panel(im, s.get("image"), (W // 2 + m // 2, int(m * 1.7), W - m, H - int(m * 1.6)), r=18, b=b, word=s.get("tag", ""))
    else:
        media_h = int(W * 9 / 16) if H / W > 1.15 else int(H * 0.36)
        top = int(m * 1.9)
        avail = H - int(m * 1.6) - media_h - int(m * 0.6) - top
        y = headline(d, b, title, (m, top, W - m, top + int(avail * 0.62)), s.get("accent"), start=int(W * 0.07), max_lines=3,
                     align="left")
        bf = b.body(int(W * 0.035))
        for ln in T.wrap(d, body, bf, W - 2 * m)[:3]:
            if y + bf.size * 1.4 > top + avail:
                break
            d.text((m, y + 10), ln, font=bf, fill=T.mix(b.text, b.bg, 0.2))
            y += int(bf.size * 1.38)
        my = min(max(y + int(m * 0.6), top + int(avail * 0.5)), H - int(m * 1.6) - media_h)
        _panel(im, s.get("image"), (0, my, W, my + media_h), r=0, b=b, word=s.get("tag", ""))
    _handle_footer(im, d, b, W, H, m, credit=s.get("credit") or (("Source: " + s["source"]) if s.get("source") else ""))
    return im


def save_card(b: T.Brand, ratio: str, n=None) -> Image.Image:
    """weekly_recap.render_cta: the recap's only CTA. Logo, FOLLOW, @handle in the accent, the line, SAVE THIS."""
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    d = ImageDraw.Draw(im)
    _badge(d, b, n, W, m)
    k = min(W, H)
    lg = T.logo_for(b, "glow", int(k * 0.3), int(k * 0.1))
    y = int(H * (0.12 if not wide else 0.08))
    if lg:
        im.paste(lg, ((W - lg.width) // 2, y), lg)
        y += lg.height + int(k * 0.05)
    f, lines = T.fit_text(d, "FOLLOW" if b.upper else "Follow", b.head, int(k * 0.16), W - 2 * m, 1)
    d.text(((W - d.textlength(lines[0], font=f)) / 2, y), lines[0], font=f, fill=b.text)
    y += int(f.size * 1.08)
    if b.handle:
        hf, hl = T.fit_text(d, "@" + (b.handle.upper() if b.upper else b.handle), b.head, int(k * 0.12), W - 2 * m, 1)
        d.text(((W - d.textlength(hl[0], font=hf)) / 2, y), hl[0], font=hf, fill=b.accent)
        y += int(hf.size * 1.25)
    line = b.end_line or "for daily AI news made simple"
    lf = b.body(int(k * 0.036))
    for ln in T.wrap(d, line, lf, W - 2 * m)[:2]:
        d.text(((W - d.textlength(ln, font=lf)) / 2, y), ln, font=lf, fill=T.mix(b.text, b.bg, 0.15))
        y += int(lf.size * 1.4)
    y += int(k * 0.04)
    pf = b.head(int(k * 0.055))
    t = "SAVE THIS" if b.upper else "Save this"
    tw = d.textlength(t, font=pf)
    ph = int(pf.size * 1.65)
    pw = int(tw + ph * 1.6)
    px = (W - pw) // 2
    d.rounded_rectangle((px, y, px + pw, y + ph), ph // 2, fill=b.accent)
    bx, by = px + int(ph * 0.42), y + int(ph * 0.25)
    bw, bh = int(ph * 0.33), int(ph * 0.5)
    d.polygon([(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx + bw // 2, by + int(bh * 0.74)), (bx, by + bh)], fill=b.on_accent)
    bb = d.textbbox((0, 0), t, font=pf)
    d.text((bx + bw + int(ph * 0.25), y + (ph - (bb[3] - bb[1])) / 2 - bb[1]), t, font=pf, fill=b.on_accent)
    y += ph + int(k * 0.03)
    nf = b.body(int(k * 0.03))
    t = "so you can catch up on the week later"
    if y + nf.size < H - m * 0.6:
        d.text(((W - d.textlength(t, font=nf)) / 2, y), t, font=nf, fill=T.mix(b.text, b.bg, 0.4))
    return im


def repo_card(d, b: T.Brand, repo: dict, box):
    """The GitHub pillar's repo card: owner/name, stars, licence and language chips."""
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, int((y1 - y0) * 0.12), fill=T.mix(b.bg, b.text, 0.07), outline=T.mix(b.bg, b.text, 0.22), width=2)
    pad = int((x1 - x0) * 0.05)
    name = repo.get("repo") or "owner/repo"
    f = _fit_line(d, name, lambda s: b.body(s, strong=True), int((y1 - y0) * 0.24), x1 - x0 - 2 * pad)
    d.text((x0 + pad, y0 + pad), name, font=f, fill=b.text)
    cf = b.body(int((y1 - y0) * 0.14), strong=True)
    x, y = x0 + pad, y0 + pad + int(f.size * 1.5)
    for chip in [c for c in (str(repo["stars"]) + " stars" if repo.get("stars") else "", repo.get("license", ""),
                             repo.get("language", "")) if c]:
        t = T._clean(chip, cf)
        w = d.textlength(t, font=cf) + cf.size * 1.4
        if x + w > x1 - pad:
            break
        T._pill(d, (x, y), t, cf, T.mix(b.bg, b.accent, 0.25), b.text, pad=(int(cf.size * 0.7), int(cf.size * 0.35)))
        x += int(w + cf.size * 0.5)


# ---------------------------------------------------------------------------------------------------- templates
TEMPLATES = {
    "editorial_cover": {"name": "Editorial cover (photo + product inset)", "kind": "single", "video": False, "front": "editorial",
                        "about": "The HuhByte cover: hero photo, circular product inset, condensed headline with the key words in "
                                 "your accent, logo divider. Data: title, hero, inset, accent_words."},
    "text_slides": {"name": "Text slides", "kind": "carousel", "video": True, "front": "editorial",
                    "about": "The editorial cover, then plain-words slides with a rounded image panel, end card last."},
    "launch_reel": {"name": "Launch video Reel", "kind": "reel", "video": True, "front": "editorial",
                    "about": "A product launch: cover, what it does, then the real launch video as a Reel (never trimmed) with "
                             "the Follow end card. Without a video: the slides as a Reel."},
    "weekly_recap": {"name": "Do you realize what just happened in AI in the last 7 days?", "kind": "carousel", "video": True,
                     "front": "editorial", "end": "save",
                     "about": "The weekly recap carousel or Reel: the hook cover, one slide per story with its media and credit, "
                              "and the Follow + SAVE THIS card (its only CTA). Built from the week's sent and scheduled posts.",
                     "slot": {"weekday": "mon", "time": "20:00"}},
    "midweek_recap": {"name": "Mid-week recap", "kind": "carousel", "video": True, "front": "editorial", "end": "save",
                      "about": "Monday to Wednesday in three stories, in the recap layout.", "slot": {"weekday": "wed", "time": "18:00"}},
    "github_repo": {"name": "GitHub repo pillar", "kind": "carousel", "video": True, "front": "editorial",
                    "about": "\"This free GitHub repo...\": a hook cover with the repo card (stars, licence, language), three plain "
                             "facts, the link slide, end card. Video first when the repo has a demo."},
    "yc_launch": {"name": "YC launch: one startup", "kind": "carousel", "video": True, "front": "editorial",
                  "about": "One YC startup: the batch tag, what it does in plain words, founders and funding, end card."},
    "viral_trend": {"name": "Viral trend with creator credit", "kind": "reel", "video": True, "front": "editorial",
                    "about": "A viral clip or trend with the creator credited on every slide and on screen in the Reel."},
    "editorial_story": {"name": "Editorial story: one company, 5-7 slides", "kind": "carousel", "video": False, "front": "editorial",
                        "about": "The HuhByte editorial carousel about one company or launch: the hero (product on the brand art + "
                                 "founder avatars), one idea per slide with **bold** key facts and a source footnote, the founders, "
                                 "Follow last. The agent fills it with research_company -> hero_edit -> write_editorial. Data: "
                                 "eyebrow, headline, accent, dek, slides[{eyebrow, blocks[{head, body}], note, image}], founders, "
                                 "hero, band, founder_photos."},
}

SAMPLE = {
    "editorial_cover": {"title": "This free app turns one video into a week of posts", "accent_words": ["free", "week"]},
    "text_slides": {"title": "What a 1M-token context window really means", "slides": [
        {"text": "A context window is how much an AI model can read at once. One million tokens is about 750,000 words."},
        {"text": "That is every Harry Potter book, twice, in a single prompt, with room left for your question."},
        {"text": "It means whole codebases and long contracts fit without cutting them up first."}]},
    "launch_reel": {"product": "Sample Studio", "company": "Sample Co", "title": "Sample Studio turns long videos into shorts",
                    "features": ["Finds the best moments from what you say", "Reframes to 9:16 and follows the speaker",
                                 "Burns in captions in your brand font"], "price": "Free to start"},
    "weekly_recap": {"stories": [
        {"title": "A new open model tops the coding leaderboard", "body": "Free weights, and it runs on one GPU.", "tag": "Models",
         "accent": ["OPEN", "MODEL"]},
        {"title": "Agents get a kill switch by default", "body": "Teams can pause every agent action with one click.", "tag": "Agents"},
        {"title": "Video models learn to keep a character on screen", "body": "Same face, same clothes, shot to shot.", "tag": "Video"},
        {"title": "A browser that summarises every tab", "body": "Built in, and it works offline.", "tag": "Tools"},
        {"title": "A cheaper inference chip ships", "body": "Half the power for the same tokens per second.", "tag": "Hardware"}]},
    "midweek_recap": {"stories": [
        {"title": "Open model tops the coding leaderboard", "body": "Free weights, one GPU.", "tag": "Models"},
        {"title": "Agents get a kill switch", "body": "Pause everything with one click.", "tag": "Agents"},
        {"title": "A browser that summarises tabs", "body": "Built in and offline.", "tag": "Tools"}]},
    "github_repo": {"repo": "sample-org/clip-maker", "hook": "This free GitHub repo turns any video into shorts",
                    "stars": "12.4k", "license": "MIT", "language": "Python",
                    "facts": ["Cuts a long video into captioned 9:16 clips", "12.4k stars on GitHub", "Free and open source (MIT)"]},
    "yc_launch": {"company": "Sample Robotics", "batch": "F25", "title": "Sample Robotics builds robot hands that learn from video",
                  "what": ["Robot hands that copy a task after watching one video", "No code: show it, it repeats it",
                           "Built for small factories"], "founders": "Two ex-research engineers", "funding": "Raised a seed round"},
    "viral_trend": {"creator": "@samplecreator", "platform": "X", "title": "This AI clip is everywhere this week",
                    "points": ["One prompt made a 30-second short film", "The creator shared the full workflow", "Free tools only"]},
    "editorial_story": {"eyebrow": "YC F25 · AI + TOOLS", "headline": "Sample Co Turns One Video Into 7 Posts", "accent": ["7"],
                        "dek": "Sample Co (YC F25) is a sample company for this preview.",
                        "slides": [
                            {"eyebrow": "How it works", "blocks": [{"head": "One Upload, 7 Posts", "body": "Drop in **one video** and it cuts "
                             "**7 short posts**, each with captions in your brand."}], "accent": ["7"]},
                            {"eyebrow": "Why it matters", "blocks": [{"head": "Hours Back Every Week", "body": "Editing shorts by hand "
                             "takes **hours**. This takes **minutes**."}], "accent": ["Hours"], "note": "Sample figures for the preview."},
                            {"eyebrow": "The math", "blocks": [{"head": "$0 to Start", "body": "Free for your **first 10 videos**, then a "
                             "flat monthly price."}], "accent": ["$0"], "image": True},
                            {"eyebrow": "What's next", "blocks": [{"head": "Live Clips Soon", "body": "Clips from **live streams** are "
                             "next on the list."}], "accent": ["Live"]}],
                        "founders": {"eyebrow": "The founders", "blocks": [{"head": "Two Video Nerds", "body": "Ex **video editors** "
                                     "who got tired of cutting shorts by hand."}], "accent": ["Nerds"], "names": ["Sam Sample", "Alex Example"]}},
}

DEFAULT_CREDIT = "Video: the creator"


def _range(d: dict, now, days: int) -> str:
    from datetime import timedelta
    if d.get("range"):
        return d["range"]
    if days == 7:
        return T._range_label(now - timedelta(days=6), now)
    monday = now - timedelta(days=now.weekday())
    return T._range_label(monday, monday + timedelta(days=2))


def slides(template: str, data: dict | None, b: T.Brand, ratio: str, now=None, max_slides: int | None = None) -> list[Image.Image]:
    from datetime import datetime, timezone
    d = {**SAMPLE[template], **(data or {})}
    now = now or datetime.now(timezone.utc)
    cap = max_slides or 10
    body: list = []
    end = "save" if TEMPLATES[template].get("end") == "save" else "brand"
    hero, inset = d.get("hero"), d.get("inset")
    if template == "editorial_story":  # studio/editorial.py: cover, one idea per slide, founders, Follow
        from . import editorial
        spec = {k: v for k, v in d.items() if k not in ("hero", "band", "image", "inset", "founder_photos")}
        photos = [p for p in (_img(x) for x in d.get("founder_photos") or []) if p is not None]
        ims, _fit = editorial.render(b, spec, hero=_img(hero), band=_img(d.get("band") or d.get("image")), founders=photos,
                                     ratio=ratio if ratio in editorial.SIZES else "4:5")
        return ims if len(ims) <= cap else ims[:cap - 1] + ims[-1:]
    if template == "editorial_cover":
        return [editorial_cover(b, ratio, d["title"], hero, inset, d.get("accent_words"), d.get("sub", ""), label=d.get("label", ""),
                                swipe=False)]
    if template in ("weekly_recap", "midweek_recap"):
        days = 7 if template == "weekly_recap" else 3
        stories = [s for s in d.get("stories") or [] if (s.get("title") or "").strip()][: (7 if days == 7 else 3)]
        front = (d.get("title") or (RECAP_HEADLINE if days == 7 else MIDWEEK_HEADLINE), RECAP_ACCENT if days == 7 else {"AI", "3"},
                 _range(d, now, days), "")
        body = [("story", s) for s in stories]
    elif template == "text_slides":
        front = (d["title"], d.get("accent_words"), d.get("sub", ""), "")
        body = [("text", s) for s in d.get("slides") or []]
    elif template == "launch_reel":
        front = (d.get("title") or f"{d['product']} is here", d.get("accent_words"), d.get("company", ""), "LAUNCH")
        feats = list(d.get("features") or []) + ([f"Price: {d['price']}"] if d.get("price") else [])
        body = [("facts", "What it does", feats[:4], d.get("image"))]
    elif template == "github_repo":
        front = (d.get("hook") or f"This free GitHub repo: {d['repo']}", d.get("accent_words") or {"FREE", "GITHUB"}, "", "OPEN SOURCE")
        body = [("repo", d), ("facts", "Why it matters", list(d.get("facts") or [])[:3], d.get("image")), ("link", d)]
    elif template == "yc_launch":
        front = (d.get("title") or d["company"], d.get("accent_words"), d.get("company", ""), f"Y COMBINATOR {d.get('batch', '')}".strip())
        extra = [x for x in (d.get("founders") and f"Founders: {d['founders']}", d.get("funding")) if x]
        body = [("facts", "What they do", list(d.get("what") or [])[:3], d.get("image"))]
        if extra:
            body.append(("facts", "The team", extra, None))
    elif template == "viral_trend":
        credit = f"Video: {d.get('creator', '')} on {d.get('platform', '')}".strip()
        front = (d["title"], d.get("accent_words"), credit, "TRENDING")
        body = [("facts", "What's going on", list(d.get("points") or [])[:3], d.get("image")), ("credit", credit)]
    else:
        raise ValueError(template)
    keep = max(0, cap - 2)
    body = body[:keep]
    total = len(body) + 2
    out = [editorial_cover(b, ratio, front[0], hero, inset, front[1], front[2], n=(1, total), label=front[3])]
    for k, part in enumerate(body, start=2):
        n = (k, total)
        kind = part[0]
        if kind == "story":
            out.append(story_slide(b, ratio, part[1], n=n))
        elif kind == "text":
            out.append(text_slide(b, ratio, part[1].get("text", ""), part[1].get("image"), n=n, title=part[1].get("title", "")))
        elif kind == "facts":
            out.append(facts_slide(b, ratio, part[1], part[2], part[3], n=n))
        elif kind == "repo":
            out.append(_repo_slide(b, ratio, part[1], n))
        elif kind == "link":
            out.append(_link_slide(b, ratio, part[1], n))
        elif kind == "credit":
            out.append(_credit_slide(b, ratio, part[1], n))
    out.append(save_card(b, ratio, n=(total, total)) if end == "save" else T.end_card(b, ratio, n=(total, total)))
    return out


def _repo_slide(b, ratio, d, n):
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    dd = ImageDraw.Draw(im)
    _badge(dd, b, n, W, m)
    y = headline(dd, b, "The repo", (m, int(m * 1.5), W - m, int(m * 1.5) + int(H * 0.12)), set(), start=int(min(W, H) * 0.085),
                 max_lines=1, align="left")
    ch = int(min(W, H) * 0.3)
    repo_card(dd, b, d, (m, y + int(m * 0.4), W - m, y + int(m * 0.4) + ch))
    if _img(d.get("image")) is not None:
        _panel(im, d.get("image"), (m, y + int(m * 0.8) + ch, W - m, H - int(m * 1.6)))
    _handle_footer(im, dd, b, W, H, m)
    return im


def _link_slide(b, ratio, d, n):
    W, H, m, wide = T._frame(ratio)
    im = T._ground(W, H, b, "glow")
    dd = ImageDraw.Draw(im)
    _badge(dd, b, n, W, m)
    url = "github.com/" + str(d.get("repo", "")).strip("/")
    k = min(W, H)
    y = headline(dd, b, "Link", (m, int(H * 0.25), W - m, int(H * 0.25) + int(k * 0.16)), set(), start=int(k * 0.13), max_lines=1)
    f = _fit_line(dd, url, lambda s: b.body(s, strong=True), int(k * 0.06), W - 2 * m)
    for ln in [url]:
        dd.text(((W - dd.textlength(ln, font=f)) / 2, y + int(k * 0.04)), ln, font=f, fill=b.accent)
        y += int(f.size * 1.3)
    tip = d.get("cta") or "Link in the caption and the first comment"
    tf = b.body(int(k * 0.034))
    for ln in T.wrap(dd, tip, tf, W - 2 * m)[:2]:
        dd.text(((W - dd.textlength(ln, font=tf)) / 2, y + int(k * 0.07)), ln, font=tf, fill=T.mix(b.text, b.bg, 0.25))
        y += int(tf.size * 1.4)
    _handle_footer(im, dd, b, W, H, m)
    return im


def _credit_slide(b, ratio, credit, n):
    W, H, m, wide = T._frame(ratio)
    im = Image.new("RGB", (W, H), b.bg)
    dd = ImageDraw.Draw(im)
    _badge(dd, b, n, W, m)
    k = min(W, H)
    y = headline(dd, b, "Credit", (m, int(H * 0.3), W - m, int(H * 0.3) + int(k * 0.15)), set(), start=int(k * 0.12), max_lines=1)
    f = _fit_line(dd, credit, lambda s: b.body(s, strong=True), int(k * 0.06), W - 2 * m)
    for ln in [credit]:
        dd.text(((W - dd.textlength(ln, font=f)) / 2, y + int(k * 0.04)), ln, font=f, fill=b.accent)
        y += int(f.size * 1.3)
    tf = b.body(int(k * 0.032))
    t = "All rights to the creator. Go follow them."
    dd.text(((W - dd.textlength(t, font=tf)) / 2, y + int(k * 0.08)), t, font=tf, fill=T.mix(b.text, b.bg, 0.3))
    _handle_footer(im, dd, b, W, H, m)
    return im


def video(template: str, data: dict | None, b: T.Brand, ratio: str = "9:16", now=None, source: str | None = None,
          theme: str | None = None) -> bytes:
    """The template as a Reel. launch_reel / viral_trend with a real clip (``source`` path) go through the media pipeline
    (studio.media.compose: the full video in the brand, credit on screen, Follow end card, music under silence) in
    ``theme`` (default huhbyte_news / caption_news); everything else is the slides as a Reel."""
    d = {**SAMPLE[template], **(data or {})}
    if source and template in ("launch_reel", "viral_trend", "github_repo", "yc_launch"):
        import tempfile
        from pathlib import Path

        from .media import compose
        from .media.worker import brand_from_kit
        with tempfile.TemporaryDirectory() as td:
            logo = None
            if b.logo is not None:
                logo = str(Path(td) / "logo.png")
                b.logo.save(logo)
            credit = (f"Video: {d.get('creator')} on {d.get('platform', 'X')}" if template == "viral_trend"
                      else d.get("credit") or (f"Video: {d.get('company')}" if d.get("company") else DEFAULT_CREDIT))
            facts = list(d.get("features") or d.get("facts") or d.get("what") or d.get("points") or [])[:4]
            m = compose.render_style(source, theme or ("caption_news" if template == "viral_trend" else "huhbyte_news"),
                                     out=str(Path(td) / "t.mp4"), ratio=ratio, hook=d.get("title") or d.get("hook") or "",
                                     facts=facts, brand=brand_from_kit(b.kit, logo), credit=credit)
            return Path(m["path"]).read_bytes()
    return T.reel(slides(template, data, b, ratio, now=now))


def jpeg_bytes(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=90)
    return buf.getvalue()


_ = ImageFilter  # Pillow filters are used by the templates module this one builds on
