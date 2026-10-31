"""Brand templates: reusable post designs a brand applies per channel (UI = REST = MCP).

A template turns a little structured data (a week of stories, a launch, a quote, a tutorial) into finished slides in the
brand: the brand's FRONT card first, the brand's END card last, the brand's logo, colours, headline and body fonts in
between. Every slide is drawn here with Pillow (a few hundred ms per carousel), so the brand screen can show a live
preview while someone edits their kit. Reels and quote clips are the same slides put through FFmpeg.

Templates
  weekly_recap    "What happened in AI in the last 7 days": a carousel or Reel built from the week's sent and scheduled posts
  midweek_recap   the same, Monday to Wednesday, three stories
  breaking        a breaking-news cover
  launch          a launch/product carousel
  quote           a quote/CEO card, and a captioned 9:16 clip
  tutorial        "Free way to do X": steps

Per channel: the frame (4:5, 9:16, 16:9, 1:1) and how many slides the network takes come from CHANNELS. Nothing here talks
to the network: the caller stores the bytes as HuhQueue media and hands Buffer the public URL.
"""
from __future__ import annotations

import io
import math
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT_DIR = Path(__file__).parent / "fonts"
SIZES = {"4:5": (1080, 1350), "9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1600, 900)}
HEAD_FONTS = {"anton": "Anton.ttf", "poppins": "Poppins-Bold.ttf", "inter": "Inter-Bold.ttf", "figtree": "Figtree-ExtraBold.ttf"}
BODY_FONTS = {"poppins": "Poppins-Medium.ttf", "inter": "Inter-Regular.ttf", "inter-semibold": "Inter-SemiBold.ttf",
              "figtree": "Figtree-Medium.ttf", "anton": "Inter-SemiBold.ttf"}
STYLES = {  # what a brand can pick for its front and end card instead of uploading one
    "bold": "Solid ground, a thick accent bar and the headline set big",
    "glow": "Dark ground with a soft accent glow behind the headline",
    "grid": "A fine grid on the ground, like an editor's layout sheet",
    "minimal": "Light ground, dark text, the accent only on the kicker",
}

# Per channel: the frame Buffer and the network take best, the most images in one post, and whether it wants video.
CHANNELS = {
    "instagram": {"label": "Instagram", "ratio": "4:5", "max_slides": 10, "video_ratio": "9:16"},
    "youtube": {"label": "YouTube Shorts", "ratio": "9:16", "max_slides": 0, "video_ratio": "9:16", "video_only": True},
    "tiktok": {"label": "TikTok", "ratio": "9:16", "max_slides": 0, "video_ratio": "9:16", "video_only": True},
    "twitter": {"label": "X", "ratio": "16:9", "max_slides": 4, "video_ratio": "16:9"},
    "linkedin": {"label": "LinkedIn", "ratio": "4:5", "max_slides": 9, "video_ratio": "4:5"},
    "threads": {"label": "Threads", "ratio": "4:5", "max_slides": 10, "video_ratio": "9:16"},
    "bluesky": {"label": "Bluesky", "ratio": "16:9", "max_slides": 4, "video_ratio": "16:9"},
    "facebook": {"label": "Facebook", "ratio": "4:5", "max_slides": 10, "video_ratio": "9:16"},
    "mastodon": {"label": "Mastodon", "ratio": "16:9", "max_slides": 4, "video_ratio": "16:9"},
}

TEMPLATES = {
    "weekly_recap": {"name": "What happened in AI in the last 7 days", "kind": "carousel", "video": True,
                     "about": "Weekly recap carousel or Reel, built from the week's sent and scheduled posts.",
                     "slot": {"weekday": "mon", "time": "20:00"}},
    "midweek_recap": {"name": "Mid-week recap", "kind": "carousel", "video": True,
                      "about": "Monday to Wednesday in three stories.", "slot": {"weekday": "wed", "time": "18:00"}},
    "breaking": {"name": "Breaking news cover", "kind": "single", "video": False,
                 "about": "One headline, the source and the time, in the brand."},
    "launch": {"name": "Launch / product carousel", "kind": "carousel", "video": True,
               "about": "What launched, what it does, what it costs, why it matters."},
    "quote": {"name": "Quote / CEO clip with captions", "kind": "quote", "video": True,
              "about": "A quote card, and a 9:16 clip with word-by-word captions."},
    "tutorial": {"name": "Free way to do X", "kind": "carousel", "video": True,
                 "about": "A free tool and the steps to use it."},
}

SAMPLE = {  # what a template shows before it has real data (the live preview, the docs)
    "weekly_recap": {"stories": [
        {"title": "A new open model tops the coding leaderboard", "body": "Free weights, and it runs on one GPU.", "tag": "Models"},
        {"title": "Agents get a kill switch by default", "body": "Teams can pause every agent action with one click.", "tag": "Agents"},
        {"title": "Video models learn to keep a character on screen", "body": "Same face, same clothes, shot to shot.", "tag": "Video"},
        {"title": "A browser that summarises every tab", "body": "Built in, and it works offline.", "tag": "Tools"},
        {"title": "Chips: a cheaper inference card ships", "body": "Half the power for the same tokens per second.", "tag": "Hardware"},
    ]},
    "midweek_recap": {"stories": [
        {"title": "Open model tops the coding leaderboard", "body": "Free weights, one GPU.", "tag": "Models"},
        {"title": "Agents get a kill switch", "body": "Pause everything with one click.", "tag": "Agents"},
        {"title": "A browser that summarises tabs", "body": "Built in and offline.", "tag": "Tools"},
    ]},
    "breaking": {"title": "A new open model just beat the leaderboard leader", "source": "Sample News", "when": "Just now"},
    "launch": {"product": "Sample Studio", "company": "Sample Co", "tagline": "Turn one long video into a week of shorts",
               "features": ["Finds the best moments in your own words", "Reframes to 9:16 and follows the speaker",
                            "Burns in captions in your brand font"], "price": "Free to start", "why": "One recording, seven posts."},
    "quote": {"quote": "Agents should draft. People should decide what goes out.", "speaker": "A founder", "role": "Sample quote"},
    "tutorial": {"task": "turn a podcast into captions", "tool": "an open-source speech model",
                 "steps": [{"title": "Install it", "body": "One command, runs on a laptop CPU."},
                           {"title": "Drop in your audio", "body": "MP3, WAV or the video file itself."},
                           {"title": "Export captions", "body": "SRT for YouTube, burned in for Reels."}]},
}

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


# ---------------------------------------------------------------------------------------------------- brand
def hex_rgb(h: str, default=(17, 17, 20)) -> tuple[int, int, int]:
    h = (h or "").strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        return default
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(c) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Brand:
    """Everything a render needs from a brand, resolved once: colours as RGB, fonts as files, the logo as an image."""

    def __init__(self, kit: dict, logo: bytes | None = None, front: bytes | None = None, end: bytes | None = None):
        c = kit.get("colors") or {}
        self.kit = kit
        self.name = (kit.get("name") or "").strip()
        self.handle = (kit.get("handle") or "").strip().lstrip("@")
        self.bg = hex_rgb(c.get("primary") or c.get("ground"), (17, 17, 20))
        self.accent = hex_rgb(c.get("accent"), (201, 172, 250))
        self.text = hex_rgb(c.get("text"), (255, 255, 255))
        self.second = hex_rgb(c.get("secondary") or "", mix(self.accent, self.bg, 0.55))
        if contrast(self.text, self.bg) < 3:  # never ship unreadable text: fall back to black or white
            self.text = (255, 255, 255) if _lum(self.bg) < 0.4 else (17, 17, 20)
        self.on_accent = (17, 17, 20) if _lum(self.accent) > 0.35 else (255, 255, 255)
        self.head_file = HEAD_FONTS.get(kit.get("font") or "anton", "Anton.ttf")
        self.body_file = BODY_FONTS.get(kit.get("body_font") or ("poppins" if kit.get("font") == "anton" else kit.get("font") or "inter"),
                                        "Inter-Regular.ttf")
        self.upper = (kit.get("headline_case") or ("upper" if kit.get("font") == "anton" else "title")) == "upper"
        self.front_style = (kit.get("front_card") or {}).get("style") or "glow"
        self.end_style = (kit.get("end_card") or {}).get("style") or self.front_style
        self.kicker = (kit.get("front_card") or {}).get("kicker") or ""
        ec = kit.get("end_card") or {}
        self.cta = (ec.get("cta") or "Follow for more").strip()[:28]
        self.end_line = (ec.get("line") or "").strip()[:70]
        self.label = (kit.get("label") or "").strip()[:60]  # e.g. "Concept example, not affiliated"
        self.logo = _open_logo(logo)
        self.front_img = _open(front)
        self.end_img = _open(end)

    def head(self, size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(FONT_DIR / self.head_file), max(8, int(size)))

    def body(self, size: int, strong: bool = False) -> ImageFont.FreeTypeFont:
        f = self.body_file
        if strong:
            f = {"Inter-Regular.ttf": "Inter-SemiBold.ttf", "Poppins-Medium.ttf": "Poppins-Bold.ttf",
                 "Figtree-Medium.ttf": "Figtree-ExtraBold.ttf"}.get(f, f)
        return ImageFont.truetype(str(FONT_DIR / f), max(8, int(size)))

    def headline(self, s: str) -> str:
        s = " ".join((s or "").split())
        return s.upper() if self.upper else (s[:1].upper() + s[1:])


def _open(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _open_logo(data: bytes | None) -> Image.Image | None:
    if not data:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    # logos exported on black with a "transparent" alpha of near-zero noise: cut to what is actually visible
    a = im.getchannel("A").point(lambda v: 255 if v > 24 else 0)
    box = a.getbbox()
    return im.crop(box) if box else im


# ---------------------------------------------------------------------------------------------------- drawing
_SUBS = str.maketrans({"\u2192": "->", "\u2190": "<-", "\u2026": "...", "\u00a0": " ", "\u2011": "-"})


def _clean(text: str, f) -> str:
    """Characters the font can't draw become plain ones (no tofu boxes on a published slide)."""
    out = []
    for ch in (text or "").translate(_SUBS):
        try:
            ok = f.getmask(ch).getbbox() is not None or ch.isspace()
        except Exception:
            ok = False
        out.append(ch if ok else ("'" if ch in "‘’" else '"' if ch in "“”" else "-" if ch in "–—" else ""))
    return "".join(out)


def wrap(d: ImageDraw.ImageDraw, text: str, f, maxw: int) -> list[str]:
    words, lines, cur = _clean(text, f).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fit_text(d, text: str, make_font, start: int, maxw: int, max_lines: int, min_size: int = 28):
    size = start
    while True:
        f = make_font(size)
        lines = wrap(d, text, f, maxw)
        if len(lines) <= max_lines or size <= min_size:
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                lines[-1] = lines[-1].rstrip(" ,.;:") + "..."
            return f, lines
        size = int(size * 0.92)


def _ground(W: int, H: int, b: Brand, style: str) -> Image.Image:
    if style == "minimal":
        im = Image.new("RGB", (W, H), _ground_rgb(b, style))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, W, int(min(W, H) * 0.018)), fill=b.accent)
        return im
    im = Image.new("RGB", (W, H), b.bg)
    if style == "glow":
        g = Image.new("L", (W, H), 0)
        gd = ImageDraw.Draw(g)
        r = int(max(W, H) * 0.42)
        cx, cy = int(W * 0.82), int(H * 0.18)
        gd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=150)
        g = g.filter(ImageFilter.GaussianBlur(r * 0.55))
        im.paste(Image.new("RGB", (W, H), b.accent), (0, 0), g)
        g2 = Image.new("L", (W, H), 0)
        ImageDraw.Draw(g2).ellipse((-r // 2, H - r, r, H + r // 2), fill=70)
        im.paste(Image.new("RGB", (W, H), b.second), (0, 0), g2.filter(ImageFilter.GaussianBlur(r * 0.5)))
    elif style == "grid":
        d = ImageDraw.Draw(im)
        step = W // 12
        line = mix(b.bg, b.text, 0.08)
        for x in range(0, W, step):
            d.line((x, 0, x, H), fill=line, width=1)
        for y in range(0, H, step):
            d.line((0, y, W, y), fill=line, width=1)
    elif style == "bold":
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, int(W * 0.035), H), fill=b.accent)
    return im


def _ink(b: Brand, style: str) -> tuple:
    """(text colour, muted colour) for a ground style."""
    if style == "minimal" and _lum(b.bg) < 0.4:
        dark = b.bg
        return dark, mix(dark, (255, 255, 255), 0.45)
    return b.text, mix(b.text, b.bg, 0.38)


def _ground_rgb(b: Brand, style: str):
    if style == "minimal":
        return mix(b.text if _lum(b.text) > 0.5 else (250, 249, 246), (255, 255, 255), 0.4) if _lum(b.bg) < 0.4 else b.bg
    return b.bg


def _emph(b: Brand, style: str):
    """The accent when it reads on this ground, else the ink (a pale accent on a light card would vanish)."""
    g = _ground_rgb(b, style)
    return b.accent if contrast(b.accent, g) >= 2.2 else _ink(b, style)[0]


def logo_for(b: Brand, style: str, w: int, h: int | None = None) -> Image.Image | None:
    """The logo sized for the slot, recoloured to the ink when it would vanish on the ground (a dark mark on a dark card)."""
    if b.logo is None:
        return None
    lg = b.logo.copy()
    lg.thumbnail((w, h or int(w * 0.6)), Image.LANCZOS)
    g = _ground_rgb(b, style)
    px = [p for p in lg.getdata() if p[3] > 128]
    if px:
        avg = tuple(sum(p[i] for p in px) // len(px) for i in range(3))
        if contrast(avg, g) < 1.6:
            ink = _ink(b, style)[0]
            solid = Image.new("RGBA", lg.size, ink + (255,))
            solid.putalpha(lg.getchannel("A"))
            lg = solid
    return lg


def _logo(canvas: Image.Image, b: Brand, box_w: int, xy: tuple[int, int], style: str = "") -> int:
    """Paste the logo at xy (top-left) no wider than box_w; returns its height. No logo: the brand name as a wordmark."""
    d = ImageDraw.Draw(canvas)
    lg = logo_for(b, style, box_w)
    if lg is not None:
        canvas.paste(lg, xy, lg)
        return lg.height
    if not b.name:
        return 0
    f = b.head(int(box_w * 0.22))
    t = b.headline(b.name)
    d.text(xy, _clean(t, f), font=f, fill=_ink(b, style)[0])
    return int(f.size * 1.1)


def _pill(d, xy, text: str, f, fill, ink, pad=(22, 12)) -> tuple[int, int]:
    t = _clean(text, f)
    w = int(d.textlength(t, font=f))
    bb = d.textbbox((0, 0), t, font=f)
    h = bb[3] - bb[1]
    x, y = xy
    d.rounded_rectangle((x, y, x + w + pad[0] * 2, y + h + pad[1] * 2), radius=(h + pad[1] * 2) // 2, fill=fill)
    d.text((x + pad[0], y + pad[1] - bb[1]), t, font=f, fill=ink)
    return w + pad[0] * 2, h + pad[1] * 2


def _footer(canvas, b: Brand, n: tuple[int, int] | None, style: str, m: int) -> None:
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    ink, muted = _ink(b, style)
    f = b.body(int(min(W, H) * 0.026), strong=True)
    y = H - m - f.size
    if b.handle:
        d.text((m, y), "@" + b.handle, font=f, fill=muted)
    if n and n[1] > 1:  # progress dots: where you are in the carousel
        r, gap = int(min(W, H) * 0.007), int(min(W, H) * 0.024)
        total = n[1] * gap
        x0 = W - m - total
        for i in range(n[1]):
            cx = x0 + i * gap + gap // 2
            col = b.accent if i == n[0] - 1 else mix(muted, b.bg, 0.4)
            d.ellipse((cx - r, y + f.size // 2 - r, cx + r, y + f.size // 2 + r), fill=col)
    if b.label:
        lf = b.body(int(min(W, H) * 0.02))
        t = _clean(b.label, lf)
        d.text(((W - d.textlength(t, font=lf)) / 2, H - int(m * 0.55) - lf.size), t, font=lf, fill=muted)


def _frame(ratio: str) -> tuple[int, int, int, bool]:
    W, H = SIZES.get(ratio, SIZES["4:5"])
    wide = W > H
    m = int(min(W, H) * (0.075 if wide else 0.074))
    return W, H, m, wide


# ---------------------------------------------------------------------------------------------------- slides
def front_card(b: Brand, ratio: str, kicker: str, title: str, sub: str = "", n=None) -> Image.Image:
    """The brand's front card: an uploaded card image, or the chosen style with the title set big."""
    W, H, m, wide = _frame(ratio)
    style = b.front_style
    if b.front_img is not None:
        im = _cover(b.front_img, W, H)
        shade = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(shade)
        for yy in range(H):
            sd.line((0, yy, W, yy), fill=int(200 * max(0.0, (yy / H - 0.35) / 0.65) ** 1.2))
        im.paste(Image.new("RGB", (W, H), b.bg), (0, 0), shade)
        style = "photo"
    else:
        im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, style if style != "photo" else "glow")
    _logo(im, b, int(W * (0.24 if not wide else 0.17)), (m, m), style)
    maxw = W - 2 * m - (int(W * 0.25) if wide else 0)
    hf, lines = fit_text(d, b.headline(title), b.head, int(min(W, H) * (0.15 if b.upper else 0.115)), maxw, 4 if not wide else 3)
    lh = int(hf.size * (1.0 if b.upper else 1.1))
    sf = b.body(int(min(W, H) * 0.036))
    sub_lines = wrap(d, sub, sf, maxw)[:3] if sub else []
    block = lh * len(lines) + (int(sf.size * 1.45) * len(sub_lines) + int(min(W, H) * 0.03) + int(hf.size * 0.18) if sub_lines else 0)
    y = H - m - int(min(W, H) * 0.09) - block
    if kicker:
        kf = b.body(int(min(W, H) * 0.03), strong=True)
        _, kh = _pill(d, (m, y - int(kf.size * 2.6)), kicker.upper(), kf, b.accent, b.on_accent)
    for i, line in enumerate(lines):
        emph = i == len(lines) - 1 and len(lines) > 1
        if emph and style == "minimal":  # a marker-pen band behind the last line, the accent as a highlighter
            bb = d.textbbox((m, y + i * lh), line, font=hf)
            d.rectangle((bb[0] - 10, bb[1] + (bb[3] - bb[1]) * 0.45, bb[2] + 10, bb[3] + 8), fill=b.accent)
        d.text((m, y + i * lh), line, font=hf, fill=_emph(b, style) if (emph and style != "minimal") else ink)
    y += lh * len(lines) + int(min(W, H) * 0.03) + (int(hf.size * 0.18) if b.upper else 0)
    for s in sub_lines:
        d.text((m, y), s, font=sf, fill=muted)
        y += int(sf.size * 1.45)
    _footer(im, b, n, style, m)
    return im


def end_card(b: Brand, ratio: str, n=None) -> Image.Image:
    """The brand's end card: an uploaded end card, or the style with the logo, the call to action and the handle."""
    W, H, m, wide = _frame(ratio)
    if b.end_img is not None:
        return _cover(b.end_img, W, H)
    style = b.end_style
    im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, style)
    lw = int(W * (0.5 if not wide else 0.32))
    lh = 0
    lg = logo_for(b, style, lw, int(lw * 0.7))
    if lg is not None:
        im.paste(lg, ((W - lg.width) // 2, int(H * (0.26 if not wide else 0.16))), lg)
        lh = lg.height
    y = int(H * (0.26 if not wide else 0.16)) + lh + int(min(W, H) * 0.08)
    cf, lines = fit_text(d, b.headline(b.cta), b.head, int(min(W, H) * 0.12), W - 2 * m, 2)
    if len(lines) == 2 and len(lines[1].split()) == 1 and len(lines[0].split()) > 2:  # no one-word orphan on the CTA
        ws = lines[0].split()
        lines = [" ".join(ws[:-1]), ws[-1] + " " + lines[1]]
    for line in lines:
        d.text(((W - d.textlength(line, font=cf)) / 2, y), line, font=cf, fill=ink)
        y += int(cf.size * (1.02 if b.upper else 1.12))
    y += int(cf.size * 0.25)
    if b.handle:
        hf = b.body(int(min(W, H) * 0.05), strong=True)
        t = "@" + b.handle
        y += int(min(W, H) * 0.03)
        pw = d.textlength(t, font=hf) + 56
        _pill(d, (int((W - pw) / 2), y), t, hf, b.accent, b.on_accent, pad=(28, 16))
        y += int(hf.size * 2.3)
    if b.end_line:
        lf = b.body(int(min(W, H) * 0.032))
        for line in wrap(d, b.end_line, lf, W - 2 * m)[:2]:
            d.text(((W - d.textlength(line, font=lf)) / 2, y), line, font=lf, fill=muted)
            y += int(lf.size * 1.4)
    if b.label:
        lf = b.body(int(min(W, H) * 0.02))
        t = _clean(b.label, lf)
        d.text(((W - d.textlength(t, font=lf)) / 2, H - int(m * 0.8) - lf.size), t, font=lf, fill=muted)
    return im


def story_slide(b: Brand, ratio: str, num: int, title: str, body: str, tag: str = "", source: str = "", n=None) -> Image.Image:
    W, H, m, wide = _frame(ratio)
    style = "grid" if b.front_style == "grid" else ("minimal" if b.front_style == "minimal" else "plain")
    im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, style)
    _logo(im, b, int(W * (0.16 if not wide else 0.11)), (m, m), style)
    nf = b.head(int(min(W, H) * (0.3 if not wide else 0.36)))
    num_t = f"{num:02d}"
    nb = d.textbbox((0, 0), num_t, font=nf)
    nw, nh = nb[2] - nb[0], nb[3] - nb[1]
    gap = int(min(W, H) * 0.035)
    x0, maxw = (m + nw + int(W * 0.05), W - 2 * m - nw - int(W * 0.05)) if wide else (m, W - 2 * m)
    hf, lines = fit_text(d, b.headline(title), b.head, int(min(W, H) * (0.095 if b.upper else 0.075)), maxw, 4)
    hlh = int(hf.size * (1.02 if b.upper else 1.12))
    bf = b.body(int(min(W, H) * 0.04))
    blines = wrap(d, body, bf, maxw)[:4] if body else []
    tf = b.body(int(min(W, H) * 0.028), strong=True)
    tag_h = int(tf.size * 1.25) + 24 + gap if tag else 0
    text_h = tag_h + hlh * len(lines) + int(hf.size * 0.2) + (gap + int(bf.size * 1.42) * len(blines) if blines else 0)
    top_min = m + int(min(W, H) * 0.14)
    if wide:
        y = max(top_min, (H - text_h) // 2)
        d.text((m - nb[0], (H - nh) // 2 - nb[1]), num_t, font=nf, fill=_emph(b, style))
    else:
        total = nh + gap + text_h
        y0 = max(top_min, (H - total) // 2 - int(H * 0.04)) if H / W > 1.5 else int(H * 0.17)
        d.text((m - nb[0], y0 - nb[1]), num_t, font=nf, fill=_emph(b, style))
        y = y0 + nh + gap
    if tag:
        _, th = _pill(d, (x0, y), tag.upper(), tf, mix(b.bg, b.accent, 0.22) if style != "minimal" else b.accent,
                      b.accent if style != "minimal" else b.on_accent)
        y += th + gap
    for line in lines:
        d.text((x0, y), line, font=hf, fill=ink)
        y += hlh
    y += int(hf.size * 0.2)
    if blines:
        y += gap
        for line in blines:
            d.text((x0, y), line, font=bf, fill=muted)
            y += int(bf.size * 1.42)
    if source:
        sf = b.body(int(min(W, H) * 0.024))
        d.text((x0, y + int(min(W, H) * 0.03)), _clean("Source: " + source, sf), font=sf, fill=mix(muted, b.bg, 0.3))
    _footer(im, b, n, style, m)
    return im


def bullets_slide(b: Brand, ratio: str, title: str, items: list[str], n=None) -> Image.Image:
    W, H, m, wide = _frame(ratio)
    style = "minimal" if b.front_style == "minimal" else "plain"
    im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, style)
    _logo(im, b, int(W * (0.16 if not wide else 0.11)), (m, m), style)
    y = int(H * (0.2 if not wide else 0.24))
    hf, lines = fit_text(d, b.headline(title), b.head, int(min(W, H) * (0.1 if b.upper else 0.08)), W - 2 * m, 2)
    for line in lines:
        d.text((m, y), line, font=hf, fill=_emph(b, style))
        y += int(hf.size * 1.05)
    y += int(min(W, H) * 0.05)
    bf = b.body(int(min(W, H) * (0.042 if not wide else 0.046)))
    for it in items[:5]:
        r = int(bf.size * 0.22)
        d.rounded_rectangle((m, y + int(bf.size * 0.3), m + r * 2 + 6, y + int(bf.size * 0.3) + r * 2 + 6), radius=4, fill=b.accent)
        for line in wrap(d, it, bf, W - 2 * m - int(bf.size * 1.3))[:3]:
            d.text((m + int(bf.size * 1.3), y), line, font=bf, fill=ink)
            y += int(bf.size * 1.38)
        y += int(bf.size * 0.7)
    _footer(im, b, n, style, m)
    return im


def quote_card(b: Brand, ratio: str, quote: str, speaker: str, role: str = "", n=None) -> Image.Image:
    W, H, m, wide = _frame(ratio)
    style = b.front_style if b.front_style in ("glow", "grid", "minimal") else "glow"
    im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, style)
    _logo(im, b, int(W * (0.18 if not wide else 0.12)), (m, m), style)
    qf = b.body(int(min(W, H) * 0.4), strong=True)
    d.text((m - int(min(W, H) * 0.01), int(H * (0.12 if not wide else 0.08))), "\u201c" if _has(qf, "\u201c") else '"', font=qf, fill=_emph(b, style))
    y = int(H * (0.36 if not wide else 0.32))
    tf, lines = fit_text(d, quote, lambda s: b.head(s) if not b.upper else b.body(s, strong=True),
                         int(min(W, H) * 0.08), W - 2 * m, 6 if not wide else 4)
    for line in lines:
        d.text((m, y), line, font=tf, fill=ink)
        y += int(tf.size * 1.18)
    y += int(min(W, H) * 0.05)
    d.rectangle((m, y, m + int(W * 0.08), y + 6), fill=b.accent)
    y += int(min(W, H) * 0.035)
    sf = b.body(int(min(W, H) * 0.04), strong=True)
    d.text((m, y), _clean(speaker, sf), font=sf, fill=ink)
    if role:
        rf = b.body(int(min(W, H) * 0.03))
        d.text((m, y + int(sf.size * 1.4)), _clean(role, rf), font=rf, fill=muted)
    _footer(im, b, n, style, m)
    return im


def breaking_card(b: Brand, ratio: str, title: str, source: str, when: str, image: bytes | None = None, n=None) -> Image.Image:
    W, H, m, wide = _frame(ratio)
    photo = _open(image)
    if photo is not None:
        im = _cover(photo, W, H)
        shade = Image.new("L", (W, H), 0)
        sd = ImageDraw.Draw(shade)
        for yy in range(H):
            sd.line((0, yy, W, yy), fill=int(235 * max(0.0, (yy / H - 0.25) / 0.75) ** 0.9))
        im.paste(Image.new("RGB", (W, H), b.bg), (0, 0), shade)
        style = "photo"
    else:
        style = b.front_style if b.front_style != "minimal" else "bold"
        im = _ground(W, H, b, style)
    d = ImageDraw.Draw(im)
    ink, muted = _ink(b, "glow")
    _logo(im, b, int(W * (0.22 if not wide else 0.15)), (m, m), style)
    kf = b.body(int(min(W, H) * 0.036), strong=True)
    hf, lines = fit_text(d, b.headline(title), b.head, int(min(W, H) * (0.14 if b.upper else 0.1)), W - 2 * m, 4 if not wide else 3)
    lh = int(hf.size * (1.0 if b.upper else 1.1))
    y = H - m - int(min(W, H) * 0.12) - lh * len(lines)
    pw, ph = _pill(d, (m, y - int(kf.size * 3)), "BREAKING", kf, b.accent, b.on_accent)
    r = int(kf.size * 0.32)
    cx, cy = m + pw + int(kf.size * 0.9), y - int(kf.size * 3) + ph // 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=b.accent)
    d.text((cx + r * 3, cy - kf.size * 0.6), _clean(when, kf), font=kf, fill=ink)
    for i, line in enumerate(lines):
        d.text((m, y + i * lh), line, font=hf, fill=ink)
    if source:
        sf = b.body(int(min(W, H) * 0.028))
        d.text((m, y + lh * len(lines) + int(min(W, H) * 0.025)), _clean("Source: " + source, sf), font=sf, fill=muted)
    _footer(im, b, n, style, m)
    return im


def _has(f, ch: str) -> bool:
    try:
        return f.getmask(ch).getbbox() is not None
    except Exception:
        return False


def _cover(img: Image.Image, W: int, H: int) -> Image.Image:
    s = max(W / img.width, H / img.height)
    im = img.resize((math.ceil(img.width * s), math.ceil(img.height * s)), Image.LANCZOS)
    x, y = (im.width - W) // 2, int((im.height - H) * 0.4)
    return im.crop((x, y, x + W, y + H)).convert("RGB")


# ---------------------------------------------------------------------------------------------------- templates
def _range_label(start: datetime, end: datetime) -> str:
    if start.month == end.month:
        return f"{start.day}\u2013{end.day} {end.strftime('%b')}"
    return f"{start.day} {start.strftime('%b')} \u2013 {end.day} {end.strftime('%b')}"


def slides(template: str, data: dict | None, b: Brand, ratio: str = "4:5", now: datetime | None = None,
           max_slides: int | None = None) -> list[Image.Image]:
    """The template's slides in order: front card first, end card last."""
    if template not in TEMPLATES:
        raise ValueError(f"Unknown template {template!r}. Pick one of {', '.join(TEMPLATES)}.")
    d = {**SAMPLE[template], **(data or {})}
    now = now or datetime.now(timezone.utc)
    body: list = []
    if template in ("weekly_recap", "midweek_recap"):
        stories = [s for s in d.get("stories") or [] if (s.get("title") or "").strip()]
        if template == "weekly_recap":
            label = d.get("range") or _range_label(now - timedelta(days=6), now)
            front = ("Weekly recap", "What happened in AI in the last 7 days", label)
            stories = stories[:7]
        else:
            monday = now - timedelta(days=now.weekday())
            label = d.get("range") or _range_label(monday, monday + timedelta(days=2))
            front = ("Mid-week", "Mid-week recap: 3 things you missed", label)
            stories = stories[:3]
        body = [("story", i + 1, s) for i, s in enumerate(stories)]
    elif template == "breaking":
        return [breaking_card(b, ratio, d["title"], d.get("source", ""), d.get("when", "Just now"), d.get("image"))]
    elif template == "launch":
        front = ("Launch", f"{d['product']}: {d['tagline']}", d.get("company", ""))
        body = [("bullets", "What it does", d.get("features") or [])]
        extra = [x for x in (d.get("price") and f"Price: {d['price']}", d.get("availability") and f"Where: {d['availability']}",
                             d.get("why") and f"Why it matters: {d['why']}") if x]
        if extra:
            body.append(("bullets", "The details", extra))
    elif template == "quote":
        front = None
        body = [("quote", d)]
    elif template == "tutorial":
        front = ("Free", f"Free way to {d['task']}", f"Using {d['tool']}" if d.get("tool") else "")
        body = [("step", i + 1, s) for i, s in enumerate(d.get("steps") or [])]
    cap = max_slides or 10
    if front is not None:
        keep = max(0, cap - 2)
        body = body[:keep]
    total = len(body) + (2 if front is not None else 1)
    out, k = [], 1
    if front is not None:
        out.append(front_card(b, ratio, front[0], front[1], front[2], n=(k, total)))
        k += 1
    for part in body:
        if part[0] == "story":
            s = part[2]
            out.append(story_slide(b, ratio, part[1], s["title"], s.get("body", ""), s.get("tag", ""), s.get("source", ""), n=(k, total)))
        elif part[0] == "bullets":
            out.append(bullets_slide(b, ratio, part[1], part[2], n=(k, total)))
        elif part[0] == "step":
            s = part[2]
            out.append(story_slide(b, ratio, part[1], s.get("title", ""), s.get("body", ""), "Step " + str(part[1]), n=(k, total)))
        elif part[0] == "quote":
            q = part[1]
            out.append(quote_card(b, ratio, q["quote"], q.get("speaker", ""), q.get("role", ""), n=(k, total)))
        k += 1
    out.append(end_card(b, ratio, n=(k, total)))
    return out


def png(im: Image.Image) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def jpeg(im: Image.Image, q: int = 90) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True, progressive=True)
    return buf.getvalue()


def for_channel(template: str, data: dict | None, b: Brand, service: str, now: datetime | None = None) -> list[Image.Image]:
    spec = CHANNELS.get(service, CHANNELS["instagram"])
    return slides(template, data, b, spec["ratio"], now=now, max_slides=spec["max_slides"] or 10)


# ---------------------------------------------------------------------------------------------------- video
def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def _ff(args: list[str], timeout: int = 240) -> None:
    r = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {r.returncode}): " + r.stderr[-800:])


def reel(frames: list[Image.Image], secs: float = 2.6, end_secs: float = 2.2, fade: float = 0.3) -> bytes:
    """Slides -> a silent 30 fps H.264 MP4 (front card first, end card last as given), a slow push-in on each slide and
    short crossfades. Silent on purpose: people add licensed music inside the network's app."""
    if not frames:
        raise ValueError("no slides")
    W, H = frames[0].size
    W, H = W - W % 2, H - H % 2
    tmp = Path(tempfile.mkdtemp(prefix="hq_tpl_"))
    try:
        parts = []
        for i, im in enumerate(frames):
            src = tmp / f"s{i}.png"
            im.convert("RGB").resize((W, H)).save(src)
            dur = end_secs if i == len(frames) - 1 else secs
            nfr = int(dur * 30)
            out = tmp / f"p{i}.mp4"
            vf = (f"scale={int(W * 1.05) // 2 * 2}:{int(H * 1.05) // 2 * 2},"
                  f"crop=w='{W * 1.05:.0f}-{W * 0.05:.0f}*n/{nfr}':h='{H * 1.05:.0f}-{H * 0.05:.0f}*n/{nfr}':x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
                  f"scale={W}:{H},setsar=1,fps=30,format=yuv420p")
            _ff(["-loop", "1", "-t", f"{dur}", "-i", str(src), "-vf", vf, "-frames:v", str(nfr), "-c:v", "libx264", "-preset", "veryfast", "-threads", "2",
                 "-crf", "23", "-an", str(out)])
            parts.append((out, dur))
        return _join(parts, tmp, fade)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _join(parts: list[tuple[Path, float]], tmp: Path, fade: float) -> bytes:
    inputs, fc, prev, off = [], [], "0:v", 0.0
    for p, _ in parts:
        inputs += ["-i", str(p)]
    for i in range(1, len(parts)):
        off += parts[i - 1][1] - fade
        fc.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={fade}:offset={off:.3f}[x{i}]")
        prev = f"x{i}"
    final = tmp / "out.mp4"
    total = sum(d for _, d in parts) - fade * (len(parts) - 1)
    cmd = [*inputs, "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo"]
    cmd += (["-filter_complex", ";".join(fc), "-map", f"[{prev}]"] if fc else ["-map", "0:v"])
    cmd += ["-map", f"{len(parts)}:a", "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(final)]
    _ff(cmd)
    return final.read_bytes()


def caption_chunks(text: str, words_per: int = 3) -> list[str]:
    words = (text or "").split()
    return [" ".join(words[i:i + words_per]) for i in range(0, len(words), words_per)]


def quote_clip(b: Brand, quote: str, speaker: str, role: str = "", ratio: str = "9:16", wps: float = 2.6,
               background: Image.Image | None = None) -> bytes:
    """A captioned 9:16 clip: the brand's front card, the quote with captions popping in a few words at a time (the
    way short-form captions read), and the brand's end card. Captions are drawn by Pillow (the brand's own fonts) and
    timed at about ``wps`` words a second; a recorded clip's word timestamps can replace the timing."""
    W, H, m, wide = _frame(ratio)
    base = background or quote_card(b, ratio, "", speaker, role)
    if background is None:  # the quote card without its quote: the captions carry the words
        base = _ground(W, H, b, b.front_style if b.front_style != "minimal" else "glow")
        d0 = ImageDraw.Draw(base)
        _logo(base, b, int(W * 0.22), (m, m), b.front_style)
        sf = b.body(int(min(W, H) * 0.045), strong=True)
        d0.text((m, int(H * 0.18)), _clean(speaker, sf), font=sf, fill=b.text)
        if role:
            rf = b.body(int(min(W, H) * 0.034))
            d0.text((m, int(H * 0.18) + int(sf.size * 1.5)), _clean(role, rf), font=rf, fill=mix(b.text, b.bg, 0.4))
        d0.rectangle((m, int(H * 0.18) - 28, m + int(W * 0.1), int(H * 0.18) - 20), fill=b.accent)
    chunks = caption_chunks(quote)
    tmp = Path(tempfile.mkdtemp(prefix="hq_quote_"))
    try:
        parts = []
        front = front_card(b, ratio, "Quote", f"\u201c{' '.join(quote.split()[:9])}{'...' if len(quote.split()) > 9 else ''}\u201d", speaker)
        fp = tmp / "front.png"
        front.save(fp)
        listing = [f"file '{fp}'", "duration 1.6"]
        shown = ""
        for i, ch in enumerate(chunks):
            im = base.copy()
            d = ImageDraw.Draw(im)
            shown = (shown + " " + ch).strip()
            # the full line so far, dim, with the newest words highlighted on an accent band
            pf = b.head(int(min(W, H) * (0.085 if not b.upper else 0.1)))
            lines = wrap(d, b.headline(shown) if b.upper else shown, pf, W - 2 * m)[-4:]
            y = int(H * 0.42)
            for j, line in enumerate(lines):
                last = j == len(lines) - 1
                if last:
                    bb = d.textbbox((m, y), line, font=pf)
                    d.rounded_rectangle((bb[0] - 18, bb[1] - 14, bb[2] + 18, bb[3] + 14), radius=14, fill=b.accent)
                d.text((m, y), line, font=pf, fill=b.on_accent if last else mix(b.text, b.bg, 0.25))
                y += int(pf.size * 1.25)
            p = tmp / f"c{i}.png"
            im.save(p)
            listing += [f"file '{p}'", f"duration {max(0.6, len(ch.split()) / wps):.2f}"]
        end = end_card(b, ratio)
        ep = tmp / "end.png"
        end.save(ep)
        listing += [f"file '{ep}'", "duration 2.0", f"file '{ep}'"]  # the concat demuxer needs the last file twice
        (tmp / "list.txt").write_text("\n".join(listing))
        out = tmp / "q.mp4"
        total = 1.6 + sum(max(0.6, len(c.split()) / wps) for c in chunks) + 2.0
        _ff(["-f", "concat", "-safe", "0", "-i", str(tmp / "list.txt"), "-f", "lavfi", "-t", f"{total:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
             "-vf", f"scale={W}:{H},fps=30,format=yuv420p", "-map", "0:v", "-map", "1:a", "-shortest", "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "23", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(out)])
        return out.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def video(template: str, data: dict | None, b: Brand, ratio: str = "9:16", now: datetime | None = None) -> bytes:
    if template == "quote":
        d = {**SAMPLE["quote"], **(data or {})}
        return quote_clip(b, d["quote"], d.get("speaker", ""), d.get("role", ""), ratio)
    return reel(slides(template, data, b, ratio, now=now))


# ---------------------------------------------------------------------------------------------------- recap from posts
URL_RE = re.compile(r"https?://\S+")
TAG_RE = re.compile(r"(?<!\w)[#@]\w+")


def _sentences(text: str) -> list[str]:
    t = TAG_RE.sub("", URL_RE.sub("", text or ""))
    t = re.sub(r"\s+", " ", t).strip()
    return [s.strip(" -:") for s in re.split(r"(?<=[.!?])\s+|\n", t) if len(s.strip()) > 8]


def _key(s: str) -> set[str]:
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "is", "are", "it", "this", "that", "your", "our", "we", "you"}
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in stop and len(w) > 2}


def stories_from_posts(posts: list[dict], start: datetime, end: datetime, limit: int = 7) -> list[dict]:
    """The week's posts -> recap stories: one per story even when it went to four channels, newest last, first sentence as
    the title and the next as the line under it. Sample-only and empty posts are skipped."""
    rows = []
    for p in posts:
        when = _parse(p.get("sentAt") or p.get("dueAt") or p.get("createdAt"))
        if not when or not (start <= when <= end):
            continue
        ss = _sentences(p.get("text") or "")
        if not ss:
            continue
        ss[0] = re.sub(r"^(Sample|Draft):\s*", "", ss[0])
        rows.append((when, ss, p))
    rows.sort(key=lambda r: r[0])
    out: list[dict] = []
    for when, ss, p in rows:
        k = _key(ss[0])
        dup = next((o for o in out if len(k & o["_k"]) >= max(2, int(0.5 * min(len(k), len(o["_k"]))))), None)
        if dup:
            dup["channels"].add(p.get("service") or p.get("channelId") or "")
            continue
        title = ss[0][:110]
        body = ss[1][:140] if len(ss) > 1 else ""
        out.append({"title": title, "body": body, "tag": when.strftime("%a"), "when": when.isoformat(), "_k": k,
                    "channels": {p.get("service") or p.get("channelId") or ""}, "status": p.get("status", "")})
    for o in out:
        o.pop("_k")
        o["channels"] = sorted(c for c in o["channels"] if c)
    return out[-limit:]


def _parse(s) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------------------- recurring slots
def next_fire(weekday: str, hhmm: str, tz: str, after: datetime) -> datetime:
    """The next time a weekly slot fires (weekday + HH:MM in the slot's timezone), as UTC."""
    from zoneinfo import ZoneInfo
    try:
        zone = ZoneInfo(tz or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    h, mi = (int(x) for x in hhmm.split(":"))
    local = after.astimezone(zone)
    wd = DAYS.index(weekday)
    cand = local.replace(hour=h, minute=mi, second=0, microsecond=0) + timedelta(days=(wd - local.weekday()) % 7)
    if cand <= local:
        cand += timedelta(days=7)
    return cand.astimezone(timezone.utc)
