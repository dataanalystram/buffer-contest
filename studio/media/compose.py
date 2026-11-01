"""Edit a launch video into a branded short, in one of four styles, for any of 9:16 / 4:5 / 1:1.

    render_style(source, "huhbyte_news", ratio="9:16", hook="...", brand={...}, words=[...], out="reel.mp4")

Styles (studio/media/styles.py describes them for agents and the UI):

  opus           Opus-Clip-style auto-clip: the best 20-45 s moment of the transcript, full-bleed reframed video,
                 big bouncing word captions, hook title on top from frame 0, Follow end card.
  huhbyte_news   The owner's HuhByte news layout (code/hb.py build_reel/add_cta): branded cover card with the hook
                 (first second), black frame with logo + hook headline, the FULL video in a panel (never trimmed),
                 word-highlight captions under it, credit line, Follow pill, facts card, 3 s Follow end card.
  minimal        Calm explainer: blurred-video backdrop, rounded video panel, small title, clean boxed captions.
  hype           Fast and loud: full-bleed, white flash + giant hook in the first second, one-word captions that
                 punch in, pulse music, end card.

Audio rule (owner, 23 Oct 2026: never ship a silent Reel): real audio is always kept; when the source is silent the
original score is the soundtrack, otherwise the music is ducked ~20 dB under the speech (sidechain), then the mix is
normalised to -14 LUFS. Every layout box is computed, every text block is auto-fitted (textfit) inside the platform
safe area, and ``render_style`` returns the boxes so tests can prove nothing clips or overlaps.
Credit: layouts adapted from dataanalystram/Huhbyte.ai (same owner), see NOTICE.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import captions, ff, music, textfit

SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
FPS = 30
CARD_SECS = 3.0
HYPE_HOOK_SECS = 1.4  # hype: the giant hook holds for the first beat, then the one-word captions take over
DEFAULT_BRAND = {"name": "HuhByte AI", "handle": "@huhbyte.ai", "accent": "#C9ACFA", "bg": "#0E0E10", "text": "#FFFFFF",
                 "muted": "#A0A0A8", "logo": None, "head_font": "Anton.ttf", "body_font": "Poppins-Medium.ttf",
                 "bold_font": "Poppins-Bold.ttf", "end_line": "AI news made simple, every day."}


def safe_area(W: int, H: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) inner rectangle clear of platform UI. 9:16 numbers from code/hb.py (Reels/TikTok:
    header ~230 px, caption/buttons ~420 px, right rail ~150 px); feed posts only need a margin."""
    k = W / 1080  # the platform UI scales with the frame, so a 720x1280 render keeps the same proportions
    if H / W > 1.6:
        return round(60 * k), round(230 * k), W - round(150 * k), H - round(420 * k)
    return round(56 * k), round(56 * k), W - round(56 * k), H - round(56 * k)


# ------------------------------------------------------------------------------------------------ small helpers
def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _logo(brand: dict, h: int) -> Image.Image | None:
    p = brand.get("logo")
    if not p or not Path(p).is_file():
        return None
    im = Image.open(p).convert("RGBA")
    return im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)


def _pill(d: ImageDraw.ImageDraw, text: str, cx: int, y: int, brand: dict, size: int = 34, maxw: int = 900) -> tuple:
    r = textfit.fit(text, brand["bold_font"], maxw - size * 2, size * 2, max_size=size, min_size=18, max_lines=1)
    f = textfit.font(brand["bold_font"], r.size)
    tw = f.getlength(r.lines[0])
    ph, pw = int(r.size * 2.1), int(tw + r.size * 1.9)
    box = (cx - pw // 2, y, cx + pw // 2, y + ph)
    d.rounded_rectangle(box, ph // 2, fill=_rgb(brand["accent"]) + (255,))
    a, de = f.getmetrics()
    d.text((cx - tw / 2, y + (ph - (a + de)) / 2), r.lines[0], font=f, fill=_rgb(brand["bg"]))
    return box


def _text_block(d, text, font_name, box, *, max_size, min_size=20, max_lines=4, fill="#FFFFFF", align="center",
                valign="middle", upper=False, accent_words=None, accent="#C9ACFA", spacing=1.08, stroke=0):
    x0, y0, x1, y1 = box
    r = textfit.fit(text, font_name, x1 - x0 - 2 * stroke, y1 - y0 - 2 * stroke, max_size=max_size, min_size=min_size,
                    max_lines=max_lines, spacing=spacing, upper=upper)
    ink = textfit.draw_fit(d, r, (x0 + stroke, y0 + stroke, x1 - stroke, y1 - stroke), fill=fill, align=align,
                           valign=valign, accent_words=accent_words, accent=accent, stroke=stroke)
    return ink, r


def cover_fit(img: Image.Image, w: int, h: int, cy: float = 0.45) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, int(img.width * s + 0.5)), max(h, int(img.height * s + 0.5))), Image.LANCZOS)
    x, y = (img.width - w) // 2, int((img.height - h) * cy)
    return img.crop((x, y, x + w, y + h))


def frame_at(src: str, t: float, out: str) -> str:
    ff.run(["-y", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", src, "-frames:v", "1", "-update", "1", out], timeout=120)
    return out


# ------------------------------------------------------------------------------------------------ analysis
def detect_burned_subs(src: str, duration: float, samples: int = 6) -> bool:
    """Cheap check for subtitles already burned into the bottom of the source (the Aarush launch video has them):
    bright, high-contrast thin text in the bottom-centre band of most sampled frames, much more than at the top."""
    import numpy as np
    hits = 0
    with tempfile.TemporaryDirectory() as td:
        for i in range(samples):
            t = duration * (i + 1) / (samples + 1)
            p = Path(td) / f"f{i}.png"
            try:
                ff.run(["-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=480:-2",
                        "-update", "1", str(p)], timeout=60)
            except ff.MediaError:
                continue
            a = np.asarray(Image.open(p).convert("L"), dtype=np.int16)
            h, w = a.shape

            def score(r0, r1):
                band = a[int(h * r0):int(h * r1), int(w * 0.2):int(w * 0.8)]
                bright = band > 200
                edge = np.abs(np.diff(band, axis=1)) > 60
                return float((bright[:, 1:] & edge).mean())
            bottom, top = score(0.82, 0.97), score(0.03, 0.18)
            if bottom > 0.005 and bottom > 2 * top + 0.002:  # tuned on the Aarush launch video (subs) vs Axonnic demos (none)
                hits += 1
    return hits >= max(2, samples // 2)


def pick_clip(words: list[dict], duration: float, lo: float = 20.0, hi: float = 45.0) -> tuple[float, float, str]:
    """Opus-style: the best self-contained moment (sentence boundaries) of the transcript. Reuses the One take
    engine's deterministic scorer (studio/take.py) so both features pick moments the same way."""
    if duration <= hi or not words:
        return 0.0, duration, "whole video"
    from .. import take
    sents = take.sentences(words)
    try:
        ms = take.heuristic_moments(sents, 1)
    except Exception:  # noqa: BLE001
        ms = []
    if ms:
        m = ms[0]
        s, e = float(m["s"]), float(m["e"])  # take.heuristic_moments: {"a","b","s","e","score","hook","why"}
        if e - s > hi:  # end on the last sentence that finishes inside the limit, never mid-sentence
            ends = [x["e"] for x in sents if s < x["e"] <= s + hi]
            e = max(ends) if ends and max(ends) - s >= lo else s + hi
        if e - s < lo:
            e = min(duration, s + lo)
        return max(0.0, s - 0.15), min(duration, e + 0.25), m.get("hook") or "best moment"
    return 0.0, min(duration, hi), "opening"


# ------------------------------------------------------------------------------------------------ cards
def end_card(path: str, W: int, H: int, brand: dict) -> dict:
    """Follow end card (code/hb.py end_card_png, editorial version): logo, 'Follow', @handle in the accent, a hairline
    and the brand's one-line promise; every line auto-fitted inside the safe area."""
    im = Image.new("RGB", (W, H), _rgb(brand["bg"]))
    d = ImageDraw.Draw(im)
    l, t, r, b = safe_area(W, H)
    boxes = {}
    y = t + int((b - t) * 0.12)
    lg = _logo(brand, int(W * 0.11))
    if lg:
        im.paste(lg, ((W - lg.width) // 2, y), lg)
        boxes["logo"] = ((W - lg.width) // 2, y, (W + lg.width) // 2, y + lg.height)
        y += lg.height + int(H * 0.04)
    else:
        bb, _ = _text_block(d, brand["name"].upper(), "Inter-SemiBold.ttf", (l, y, r, y + int(H * 0.04)), max_size=44,
                            fill=brand["muted"])
        boxes["name"] = bb
        y = bb[3] + int(H * 0.04)
    bb, _ = _text_block(d, "Follow", brand["head_font"], (l, y, r, y + int(H * 0.13)), max_size=int(W * 0.2), max_lines=1)
    boxes["follow"] = bb
    y = bb[3] + int(H * 0.015)
    bb, _ = _text_block(d, brand["handle"], brand["head_font"], (l, y, r, y + int(H * 0.09)), max_size=int(W * 0.15),
                        max_lines=1, fill=brand["accent"])
    boxes["handle"] = bb
    y = bb[3] + int(H * 0.03)
    d.line((W // 2 - 60, y, W // 2 + 60, y), fill=(70, 70, 76), width=2)
    y += int(H * 0.03)
    bb, _ = _text_block(d, brand["end_line"], "Inter-Regular.ttf", (l, y, r, y + int(H * 0.08)), max_size=40, max_lines=2,
                        fill="#DADADE")
    boxes["line"] = bb
    im.save(path)
    return boxes


def facts_card(path: str, W: int, H: int, brand: dict, title: str, facts: list[str], bg: Image.Image | None = None) -> dict:
    im = (cover_fit(bg, W, H).filter(ImageFilter.GaussianBlur(28)) if bg else Image.new("RGB", (W, H), _rgb(brand["bg"])))
    im = Image.blend(im, Image.new("RGB", (W, H), _rgb(brand["bg"])), 0.72)
    d = ImageDraw.Draw(im)
    l, t, r, b = safe_area(W, H)
    boxes = {}
    bb, _ = _text_block(d, title, brand["head_font"], (l, t + 20, r, t + 20 + int(H * 0.12)), max_size=110, max_lines=2,
                        upper=True)
    boxes["title"] = bb
    y = bb[3] + int(H * 0.04)
    n = max(1, len(facts[:4]))
    slot = (b - y - 20) // n
    for i, fct in enumerate(facts[:4]):
        dot_y = y + 14
        d.ellipse((l, dot_y, l + 18, dot_y + 18), fill=_rgb(brand["accent"]))
        bb, _ = _text_block(d, fct, brand["body_font"], (l + 40, y, r, y + slot - 18), max_size=54, min_size=22,
                            max_lines=4, align="left", valign="top")
        boxes[f"fact{i}"] = bb
        y += slot
    im.save(path)
    return boxes


def cover_card(path: str, W: int, H: int, brand: dict, hook: str, still: Image.Image | None, label: str = "WATCH",
               accent_words=None) -> dict:
    """Brand cover (code/hb.py cover_slide): the hero frame full-bleed with a dark fade, label pill, big hook."""
    im = cover_fit(still, W, H, 0.35) if still else Image.new("RGB", (W, H), _rgb(brand["bg"]))
    grad = Image.new("L", (W, H))
    gd = ImageDraw.Draw(grad)
    for y in range(H):
        gd.line((0, y, W, y), fill=int(255 * min(1, max(0, (y - H * 0.35) / (H * 0.4)))))
    im.paste(Image.new("RGB", (W, H), _rgb(brand["bg"])), (0, 0), grad)
    d = ImageDraw.Draw(im, "RGBA")
    l, t, r, b = safe_area(W, H)
    boxes = {}
    lg = _logo(brand, int(H * 0.035))
    if lg:
        im.paste(lg, (l, t), lg)
        boxes["logo"] = (l, t, l + lg.width, t + lg.height)
    hook_box = (l, int(H * 0.52), r, b - int(H * 0.09))
    bb, _ = _text_block(d, hook, brand["head_font"], hook_box, max_size=150, min_size=40, max_lines=4, upper=True,
                        accent_words=accent_words, accent=brand["accent"], valign="bottom")
    boxes["hook"] = bb
    boxes["label"] = _pill(d, label, (l + r) // 2, bb[3] + int(H * 0.03), brand, size=30)  # clear gap under the last line
    im.save(path)
    return boxes


# ------------------------------------------------------------------------------------------------ layout
def layout(style: str, W: int, H: int, sw: int, sh: int, brand: dict, hook: str, captions_on: bool) -> dict:
    """Every box of the main section, stacked top to bottom inside the safe area with no overlap:
    logo -> hook -> video panel -> caption band -> Follow pill (9:16 only). Returns pixel boxes (x0, y0, x1, y1)."""
    l, t, r, b = safe_area(W, H)
    tall = H / W > 1.6
    L: dict = {"safe": (l, t, r, b)}
    if style in ("opus", "hype"):
        hb = (l, t, r, t + int(H * (0.12 if tall else 0.15)))
        L.update(panel=(0, 0, W, H), hook=hb, captions_center=int(H * (0.60 if tall else 0.70)))
        if style == "hype":  # giant hook under the credit chip for the first beat, clear of the caption line
            y0 = t + int(H * (0.045 if tall else 0.07))
            L["hook"] = (l, y0, r, min(y0 + int(H * (0.24 if tall else 0.26)), L["captions_center"] - int(H * 0.09)))
        return L
    logo_h = int(H * 0.032) if (style == "huhbyte_news" and brand.get("logo")) else 0
    hook_font = brand["head_font"] if style == "huhbyte_news" else "Inter-SemiBold.ttf"
    hf = textfit.fit(hook, hook_font, r - l, int(H * (0.17 if tall else 0.16)), max_size=96 if style == "huhbyte_news" else 64,
                     min_size=30, max_lines=3, upper=style == "huhbyte_news")
    hook_h = hf.height + 8
    pill_h = int(32 * 2.1) if (style == "huhbyte_news" and tall) else 0
    cap_h = int(H * (0.036 if style == "huhbyte_news" else 0.034) * 2.3) if captions_on else 0
    if style == "huhbyte_news":
        pw = W
    else:
        pw = r - l
    ph = int(round(pw * sh / sw / 2) * 2)
    gaps = [int(H * 0.018)] * 4
    fixed = logo_h + hook_h + cap_h + pill_h + sum(gaps)
    max_ph = (b - t) - fixed
    if ph > max_ph:
        ph = max(2, max_ph - max_ph % 2)
        pw = min(pw, int(round(ph * sw / sh / 2) * 2))
    slack = (b - t) - fixed - ph
    y = t + max(0, slack // 3)  # optical centre: a third of the slack above, the rest below
    if logo_h:
        L["logo"] = ((W - 3 * logo_h) // 2, y, (W + 3 * logo_h) // 2, y + logo_h)
        y += logo_h + gaps[0]
    L["hook"] = (l, y, r, y + hook_h)
    y += hook_h + gaps[1]
    x0 = (W - pw) // 2
    L["panel"] = (x0, y, x0 + pw, y + ph)
    y += ph + gaps[2]
    if cap_h:
        L["cap_band"] = (l, y, r, y + cap_h)
        y += cap_h + gaps[3]
    if pill_h:
        L["pill_y"] = y
    return L


def _credit_chip(d, credit: str, x: int, y: int, maxw: int) -> tuple:
    f = textfit.fit(credit, "Inter-SemiBold.ttf", maxw - 28, 40, max_size=24, min_size=14, max_lines=1)
    fo = textfit.font("Inter-SemiBold.ttf", f.size)
    tw = int(fo.getlength(f.lines[0]))
    a, de = fo.getmetrics()
    box = (x, y, x + tw + 28, y + a + de + 16)
    d.rounded_rectangle(box, 12, fill=(0, 0, 0, 160))
    d.text((x + 14, y + 8), f.lines[0], font=fo, fill=(236, 236, 240))
    return box


# ------------------------------------------------------------------------------------------------ overlays per style
def _overlay(style: str, W: int, H: int, brand: dict, hook: str, credit: str, L: dict, accent_words) -> tuple[Image.Image, dict]:
    o = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    l, t, r, b = L["safe"]
    boxes: dict = {}
    if style == "huhbyte_news":
        o.paste(Image.new("RGBA", (W, H), _rgb(brand["bg"]) + (255,)))
        d = ImageDraw.Draw(o)
        if "logo" in L:
            lg = _logo(brand, L["logo"][3] - L["logo"][1])
            if lg:
                x = (W - lg.width) // 2
                o.paste(lg, (x, L["logo"][1]), lg)
                boxes["logo"] = (x, L["logo"][1], x + lg.width, L["logo"][1] + lg.height)
        bb, _ = _text_block(d, hook, brand["head_font"], L["hook"], max_size=96, min_size=30, max_lines=3, upper=True,
                            accent_words=accent_words, accent=brand["accent"], valign="bottom")
        boxes["hook"] = bb
        d.rectangle(L["panel"], fill=(0, 0, 0, 0))
        boxes["panel"] = L["panel"]
        if "pill_y" in L:
            boxes["pill"] = _pill(d, "Follow " + brand["handle"], W // 2, L["pill_y"], brand, size=32, maxw=r - l)
    elif style in ("opus", "hype"):
        hb = L["hook"]
        if style == "opus":
            d.rounded_rectangle((hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10), 28, fill=(255, 255, 255, 236))
            bb, _ = _text_block(d, hook, brand["bold_font"], (hb[0] + 18, hb[1] + 4, hb[2] - 18, hb[3] - 4), max_size=64,
                                min_size=24, max_lines=3, fill="#111114")
            boxes["hook"] = (hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10)
            boxes["hook_text"] = bb
        if credit:
            y0 = (hb[3] + 24) if style == "opus" else t
            boxes["credit"] = _credit_chip(d, credit, l, y0, r - l)
    elif style == "minimal":
        bb, _ = _text_block(d, hook, "Inter-SemiBold.ttf", L["hook"], max_size=64, min_size=28, max_lines=3, align="left",
                            valign="bottom")
        boxes["hook"] = bb
        p = L["panel"]
        d.rounded_rectangle((p[0] - 4, p[1] - 4, p[2] + 4, p[3] + 4), 18, outline=(255, 255, 255, 110), width=4)
        boxes["panel"] = p
    if credit and style in ("huhbyte_news", "minimal"):
        p = L["panel"]
        boxes["credit"] = _credit_chip(d, credit, p[0] + 16, p[1] + 16, (p[2] - p[0]) - 32)
    return o, boxes


def _hype_hook(W: int, H: int, brand: dict, hook: str, L: dict, accent_words) -> tuple[Image.Image, tuple]:
    """Hype's first-beat title: giant uppercase head font with a heavy outline, auto-fitted in L["hook"]."""
    o = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    stroke = max(4, W // 150)
    bb, _ = _text_block(d, hook, brand["head_font"], L["hook"], max_size=150, min_size=40, max_lines=3, upper=True,
                        accent_words=accent_words, accent=brand["accent"], stroke=stroke)
    return o, bb


# ------------------------------------------------------------------------------------------------ encode helpers
def _venc(crf: int, preset: str) -> list[str]:
    # identical settings for every segment (no per-segment -tune: the joined MP4 keeps only the FIRST segment's SPS/PPS,
    # so a stillimage-tuned card made the following segments decode black in testing)
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-profile:v", "high", "-threads", os.environ.get("HQ_FFMPEG_THREADS", "2"), "-video_track_timescale", "15360"]


def _card_segment(png: str, secs: float, out: str, crf: int, preset: str, fade: bool = True) -> str:
    vf = f"fps={FPS},format=yuv420p,setsar=1" + (",fade=t=in:st=0:d=0.25" if fade else "")
    ff.run(["-y", "-v", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{secs:.3f}", "-i", png, "-vf", vf, "-an",
            *_venc(crf, preset), out], timeout=600)
    return out


def _esc(p: str) -> str:
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


# ------------------------------------------------------------------------------------------------ main entry
def render_style(source: str | None, style: str, *, out: str, ratio: str = "9:16", hook: str = "", facts: list[str] | None = None,
                 brand: dict | None = None, words: list[dict] | None = None, credit: str = "", clip: tuple | None = None,
                 images: list[str] | None = None, music_mood: str = "auto", names: list[str] | None = None,
                 burned_subs: bool | None = None, captions_mode: str = "auto", accent_words: list[str] | None = None,
                 crf: int = 21, preset: str = "veryfast", workdir: str | None = None, max_main: float = 180.0,
                 reframe: dict | None = None) -> dict:
    """Render one styled video. ``source`` None + ``images`` -> a slow push-in slideshow (no-media fallback).
    captions_mode: auto (off in panel styles when the source already has burned-in subtitles) | on | off.
    reframe: a crop plan for the full-bleed styles (``longform.reframe_plan`` / ``take.crop_plan``): ``track`` follows
    the face, ``pad`` shows the whole frame over a blurred copy, anything else (or None) is the centre crop.
    Returns {path, style, ratio, width, height, duration, clip, boxes, safe_area, caption_problems, ...}."""
    from .styles import STYLE_PRESETS, resolve
    style = resolve(style)
    if style not in STYLE_PRESETS:
        raise ff.MediaError(f"Unknown style {style!r}. Pick one of: {', '.join(STYLE_PRESETS)}.")
    cfg = STYLE_PRESETS[style]
    brand = {**DEFAULT_BRAND, **(brand or {})}
    if ratio not in SIZES:
        raise ff.MediaError(f"Unknown ratio {ratio!r}. Use 9:16, 4:5 or 1:1.")
    W, H = SIZES[ratio]
    own = workdir is None
    wd = Path(workdir or tempfile.mkdtemp(prefix="hqm-"))
    wd.mkdir(parents=True, exist_ok=True)
    acc = set(accent_words or [])
    try:
        if source:
            pr = ff.probe(source)
            sw, sh, dur, has_a = pr["width"] or 1920, pr["height"] or 1080, pr["duration"], pr["has_audio"]
        else:
            if not images:
                raise ff.MediaError("Nothing to render: no video and no images.")
            sw, sh, dur, has_a = 1920, 1080, max(6.0, 3.5 * len(images)), False
        words = captions.fix_names(list(words or []), names)
        if clip:
            c0, c1, why = float(clip[0]), float(clip[1]), "given"
        elif cfg["autoclip"] and source:
            c0, c1, why = pick_clip(words, dur, *cfg["clip_range"])
        else:
            c0, c1, why = 0.0, min(dur, max_main), "full video"
        D = max(0.5, c1 - c0)
        cw = [{"w": w["w"], "s": w["s"] - c0, "e": w["e"] - c0} for w in words if c0 <= w["s"] < c1]
        silent = (not has_a) or (source is not None and ff.is_silent(source))
        if burned_subs is None:
            burned_subs = bool(source) and detect_burned_subs(source, dur)
        caps_on = bool(cw) and bool(cfg["captions"]) and (
            captions_mode == "on" or (captions_mode == "auto" and not (burned_subs and cfg["fill"] != "fill")))
        cover_s = cfg["cover_secs"] if source else 0.0
        facts = [f for f in (facts or []) if f.strip()]
        facts_s = cfg["facts_secs"] if facts else 0.0
        total = cover_s + D + facts_s + CARD_SECS

        still = Image.open(frame_at(source, c0 + min(D * 0.3, 4.0), str(wd / "still.png"))) if source else Image.open(images[0])
        L = layout(cfg["layout"], W, H, sw, sh, brand, hook, caps_on)
        ov, boxes = _overlay(cfg["layout"], W, H, brand, hook, credit, L, acc)
        if "cap_band" in L:
            boxes["cap_band"] = L["cap_band"]
        ov_path = wd / "overlay.png"
        ov.save(ov_path)
        hook_path = None
        if cfg["layout"] == "hype" and hook.strip():
            hk, boxes["hook"] = _hype_hook(W, H, brand, hook, L, acc)
            hook_path = wd / "hook.png"
            hk.save(hook_path)
        segs = []
        if cover_s:
            cover_card(str(wd / "cover.png"), W, H, brand, hook, still, label=cfg["cover_label"], accent_words=acc)
            boxes["cover_card"] = "cover.png"
            segs.append(_card_segment(str(wd / "cover.png"), cover_s, str(wd / "s0_cover.mp4"), crf, preset, fade=False))

        ass_path = None
        cap_problems: list[str] = []
        if caps_on:
            ass = captions.build_ass(cw, cfg["captions"], W=W, H=H, accent=brand["accent"], text=brand["text"],
                                     outline=brand["bg"], band=(L["cap_band"][1], L["cap_band"][3]) if "cap_band" in L else None,
                                     center_y=(L["captions_center"] / H) if "captions_center" in L else None, end=D)
            cap_problems = captions.check_ass_fits(ass, W, H)
            ass_path = wd / "caps.ass"
            ass_path.write_text(ass)

        main = str(wd / "s1_main.mp4")
        args, fc = _main_graph(source, images, cfg, W, H, L["panel"], c0, D, ov_path, ass_path, burned_subs, brand,
                               hook_path=hook_path, reframe=reframe)
        ff.run(["-y", "-v", "error", *args, "-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{D:.3f}",
                *_venc(crf, preset), main], timeout=1800)
        segs.append(main)
        if facts_s:
            facts_card(str(wd / "facts.png"), W, H, brand, cfg["facts_title"], facts, still)
            segs.append(_card_segment(str(wd / "facts.png"), facts_s, str(wd / "s2_facts.mp4"), crf, preset))
        boxes["end_card"] = end_card(str(wd / "end.png"), W, H, brand)
        segs.append(_card_segment(str(wd / "end.png"), CARD_SECS, str(wd / "s3_end.mp4"), crf, preset))
        lst = wd / "segments.txt"
        lst.write_text("".join(f"file '{Path(s).resolve()}'\n" for s in segs))
        video = str(wd / "video.mp4")
        ff.run(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", video], timeout=300)

        mood = cfg["music"] if music_mood == "auto" else music_mood
        wav = music.make(str(wd / "music.wav"), total + 0.5, mood) if mood != "none" else None
        outp = Path(out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        _mux_audio(video, source if (source and not silent) else None, c0, D, cover_s, total, wav, str(outp))
        got = ff.probe(str(outp))
        return {"path": str(outp), "style": style, "ratio": ratio, "width": got["width"], "height": got["height"],
                "duration": round(got["duration"], 2), "clip": [round(c0, 2), round(c1, 2), why], "boxes": boxes,
                "layout": {k: v for k, v in L.items()}, "safe_area": safe_area(W, H), "caption_problems": cap_problems,
                "captions": caps_on, "burned_subs": burned_subs, "music": mood if wav else "none", "source_silent": silent,
                "cover_secs": cover_s, "facts_secs": facts_s, "credit": credit}
    finally:
        if own:
            shutil.rmtree(wd, ignore_errors=True)


def _main_graph(source, images, cfg, W, H, panel, c0, D, ov_path, ass_path, burned_subs, brand, hook_path=None, reframe=None):
    args: list[str] = []
    n = 0
    if source:
        args += ["-ss", f"{c0:.3f}", "-t", f"{D:.3f}", "-i", source]
        n = 1
    else:
        for p in images:
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D / len(images):.3f}", "-i", p]
        n = len(images)
    ov_i = n
    args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", str(ov_path)]
    if hook_path:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{min(D, HYPE_HOOK_SECS):.3f}", "-i", str(hook_path)]
    bg = brand["bg"].lstrip("#")
    px0, py0, px1, py1 = panel
    pw, ph = px1 - px0, py1 - py0
    parts = []
    if source:
        src = f"[0:v]setpts=PTS-STARTPTS,fps={FPS},setsar=1"
        if burned_subs and cfg["fill"] == "fill":  # crop the burned-in subtitle band so captions never double up
            src += ",crop=iw:trunc(ih*0.84/2)*2:0:0"
        mode = (reframe or {}).get("mode")
        if cfg["fill"] == "fill" and mode == "track":  # subject-aware: the crop window follows the face (take.crop_plan)
            from ..take import crop_expr
            parts.append(f"{src},crop={int(reframe['crop_w'])}:ih:x='{crop_expr(reframe)}':y=0,scale={W}:{H},setsar=1[base]")
        elif cfg["fill"] == "fill" and mode == "pad":  # no one on camera: the whole frame over a blurred copy of itself
            parts.append(f"{src},split[s1][s2];[s1]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
                         f"crop={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.2:saturation=0.85[bgv];"
                         f"[s2]scale={W}:{H}:force_original_aspect_ratio=decrease[fgv];[bgv][fgv]overlay=(W-w)/2:(H-h)/2[base]")
        elif cfg["fill"] == "fill":
            parts.append(f"{src},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base]")
        elif cfg["fill"] == "blurpanel":
            parts.append(f"{src},split[s1][s2];[s1]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
                         f"crop={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.16:saturation=0.85[bgv];"
                         f"[s2]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}[pv];"
                         f"[bgv][pv]overlay={px0}:{py0}[base]")
        else:
            parts.append(f"color=c=0x{bg}:s={W}x{H}:r={FPS}:d={D:.3f}[bgc];{src},scale={pw}:{ph}:"
                         f"force_original_aspect_ratio=increase,crop={pw}:{ph}[pv];[bgc][pv]overlay={px0}:{py0}:shortest=1[base]")
    else:
        segs = []
        for i in range(len(images)):
            fr = max(1, int(D / len(images) * FPS))
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                         f"scale={int(W * 1.12) // 2 * 2}:-2,zoompan=z='min(1+0.0012*on,1.1)':x='iw/2-(iw/zoom/2)':"
                         f"y='ih/2-(ih/zoom/2)':d={fr}:s={W}x{H}:fps={FPS}[k{i}]")
            segs.append(f"[k{i}]")
        parts.append(f"{''.join(segs)}concat=n={len(images)}:v=1:a=0[base]")
    chain = f"[base][{ov_i}:v]overlay=0:0:shortest=1"
    if hook_path:
        chain += f"[b1];[b1][{ov_i + 1}:v]overlay=0:0:eof_action=pass:enable='lt(t,{HYPE_HOOK_SECS})'"
    if cfg.get("flash") and source:
        chain += ",fade=t=in:st=0:d=0.2:color=white"
    if ass_path:
        fontsdir = str(Path(__file__).resolve().parent.parent / "fonts")
        chain += f",ass='{_esc(str(ass_path))}':fontsdir='{_esc(fontsdir)}'"
    parts.append(chain + ",format=yuv420p,setsar=1[v]")
    return args, ";".join(parts)


def _mux_audio(video: str, speech_src: str | None, c0: float, D: float, cover_s: float, total: float, wav: str | None,
               out: str) -> None:
    """Final pass: copy the joined video, build the audio (real sound first, music ducked under it), -14 LUFS."""
    args = ["-y", "-v", "error", "-i", video]
    parts = []
    n = 1
    if speech_src:
        args += ["-ss", f"{c0:.3f}", "-t", f"{D:.3f}", "-i", speech_src]
        sp_i, n = n, n + 1
        d_ms = int(cover_s * 1000)
        parts.append(f"[{sp_i}:a]asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=stereo,"
                     f"afade=t=out:st={max(0.0, D - 0.4):.3f}:d=0.4,adelay={d_ms}|{d_ms},apad,atrim=0:{total:.3f}[sp]")
    if wav:
        args += ["-i", wav]
        mu_i, n = n, n + 1
    if speech_src and wav:
        parts.append(f"[{mu_i}:a]atrim=0:{total:.3f},volume=0.5[mu];[sp]asplit[sp1][sp2];"
                     f"[mu][sp1]sidechaincompress=threshold=0.015:ratio=14:attack=10:release=400[duck];"
                     f"[sp2][duck]amix=inputs=2:weights='1 0.3':normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    elif speech_src:
        parts.append("[sp]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    elif wav:
        parts.append(f"[{mu_i}:a]atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
        parts.append(f"[{n}:a]anull[a]")
    ff.run([*args, "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-b:a", "160k", "-ar", "44100", "-t", f"{total:.3f}", "-movflags", "+faststart", out], timeout=900)
