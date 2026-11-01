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

import json
import math
import os
import shutil
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import captions, ff, music, textfit

SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
FPS = 30
CARD_SECS = 3.0
DEFAULT_BRAND = {"name": "HuhByte AI", "handle": "@huhbyte.ai", "accent": "#C9ACFA", "bg": "#0E0E10", "text": "#FFFFFF",
                 "muted": "#A0A0A8", "logo": None, "head_font": "Anton.ttf", "body_font": "Poppins-Medium.ttf",
                 "bold_font": "Poppins-Bold.ttf", "end_line": "AI news made simple, every day."}


def safe_area(W: int, H: int) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) inner rectangle clear of platform UI. 9:16 numbers from code/hb.py (Reels/TikTok:
    header ~230 px, caption/buttons ~420 px, right rail ~150 px); feed posts only need a margin."""
    if H / W > 1.6:
        return 60, 230, W - 150, H - 420
    return 56, 56, W - 56, H - 56


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
                bright = band > 225
                edge = np.abs(np.diff(band, axis=1)) > 90
                return float((bright[:, 1:] & edge).mean())
            bottom, top = score(0.82, 0.97), score(0.03, 0.18)
            if bottom > 0.004 and bottom > 3 * top:
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
        s, e = float(m.get("start", 0)), float(m.get("end", hi))
        if e - s > hi:
            e = s + hi
        if e - s < lo:
            e = min(duration, s + lo)
        return max(0.0, s - 0.15), min(duration, e + 0.25), m.get("hook") or m.get("title") or "best moment"
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
    boxes["label"] = _pill(d, label, (l + r) // 2, bb[3] + int(H * 0.025), brand, size=30)
    im.save(path)
    return boxes


# ------------------------------------------------------------------------------------------------ overlays per style
def _overlay(style: str, W: int, H: int, brand: dict, hook: str, credit: str, panel: tuple | None, accent_words,
             cap_band: tuple | None) -> tuple[Image.Image, dict]:
    o = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(o)
    l, t, r, b = safe_area(W, H)
    boxes: dict = {}
    if style == "huhbyte_news":
        o.paste(Image.new("RGBA", (W, H), _rgb(brand["bg"]) + (255,)))
        d = ImageDraw.Draw(o)
        y = t
        lg = _logo(brand, int(H * 0.034))
        if lg:
            o.paste(lg, ((W - lg.width) // 2, y), lg)
            boxes["logo"] = ((W - lg.width) // 2, y, (W + lg.width) // 2, y + lg.height)
            y += lg.height + int(H * 0.018)
        bb, _ = _text_block(d, hook, brand["head_font"], (l, y, r, panel[1] - int(H * 0.02)), max_size=96, min_size=34,
                            max_lines=3, upper=True, accent_words=accent_words, accent=brand["accent"], valign="bottom")
        boxes["hook"] = bb
        d.rectangle(panel, fill=(0, 0, 0, 0))
        boxes["panel"] = panel
        if credit:
            cb, _ = _text_block(d, credit, "Inter-Regular.ttf", (l, cap_band[1] + 6, r, cap_band[1] + 6 + int(H * 0.022)),
                                max_size=26, min_size=16, max_lines=1, fill=brand["muted"])
            boxes["credit"] = cb
        if H / W > 1.6:
            boxes["pill"] = _pill(d, "Follow " + brand["handle"], (l + r) // 2, b - int(H * 0.045), brand, size=32,
                                  maxw=r - l)
    elif style in ("opus", "hype"):
        # translucent top band for the hook (Opus puts a title card at the top), credit chip
        hb = (l, t, r, t + int(H * (0.13 if style == "opus" else 0.16)))
        if style == "opus":
            d.rounded_rectangle((hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10), 28, fill=(255, 255, 255, 236))
            bb, _ = _text_block(d, hook, brand["bold_font"], (hb[0] + 18, hb[1] + 4, hb[2] - 18, hb[3] - 4), max_size=64,
                                min_size=26, max_lines=3, fill="#111114")
        else:
            bb = hb
        boxes["hook"] = bb
        if credit:
            f = textfit.fit(credit, "Inter-SemiBold.ttf", r - l - 40, 40, max_size=26, min_size=16, max_lines=1)
            fo = textfit.font("Inter-SemiBold.ttf", f.size)
            tw = int(fo.getlength(f.lines[0]))
            y0 = (hb[3] + 22) if style == "opus" else t
            d.rounded_rectangle((l, y0, l + tw + 28, y0 + f.size + 20), 14, fill=(0, 0, 0, 150))
            d.text((l + 14, y0 + 8), f.lines[0], font=fo, fill=(235, 235, 240))
            boxes["credit"] = (l, y0, l + tw + 28, y0 + f.size + 20)
    elif style == "minimal":
        y = t
        bb, _ = _text_block(d, hook, "Inter-SemiBold.ttf", (l, y, r, panel[1] - int(H * 0.025)), max_size=64, min_size=28,
                            max_lines=3, align="left", valign="bottom", stroke=0)
        boxes["hook"] = bb
        d.rounded_rectangle((panel[0] - 3, panel[1] - 3, panel[2] + 3, panel[3] + 3), 18, outline=(255, 255, 255, 90), width=3)
        boxes["panel"] = panel
        if credit:
            cb, _ = _text_block(d, credit, "Inter-Regular.ttf", (l, panel[3] + 14, r, panel[3] + 14 + int(H * 0.022)),
                                max_size=26, min_size=16, max_lines=1, fill="#C8C8CE", align="left")
            boxes["credit"] = cb
    return o, boxes


def _panel_for(style: str, W: int, H: int, sw: int, sh: int) -> tuple[int, int, int, int]:
    l, t, r, b = safe_area(W, H)
    if style == "huhbyte_news":
        pw = W
        ph = int(round(pw * sh / sw / 2) * 2)
        maxh = int(H * (0.42 if H / W > 1.6 else 0.5))
        if ph > maxh:
            ph = maxh - maxh % 2
            pw = int(round(ph * sw / sh / 2) * 2)
        y = int(H * 0.36) if H / W > 1.6 else int(H * 0.26)
        x = (W - pw) // 2
        return (x, y, x + pw, y + ph)
    if style == "minimal":
        pw = r - l
        ph = int(round(pw * sh / sw / 2) * 2)
        maxh = int((b - t) * 0.62)
        if ph > maxh:
            ph = maxh - maxh % 2
            pw = int(round(ph * sw / sh / 2) * 2)
        x = (W - pw) // 2
        y = t + int((b - t) * 0.22)
        return (x, y, x + pw, y + ph)
    return (0, 0, W, H)


# ------------------------------------------------------------------------------------------------ main entry
def render_style(source: str | None, style: str, *, out: str, ratio: str = "9:16", hook: str = "", facts: list[str] | None = None,
                 brand: dict | None = None, words: list[dict] | None = None, credit: str = "", clip: tuple | None = None,
                 images: list[str] | None = None, music_mood: str = "auto", names: list[str] | None = None,
                 burned_subs: bool | None = None, accent_words: list[str] | None = None, crf: int = 21,
                 preset: str = "veryfast", workdir: str | None = None, max_main: float = 180.0) -> dict:
    """Render one styled video. ``source`` None + ``images`` -> a Ken Burns slideshow (no-media fallback).
    Returns {path, style, ratio, width, height, duration, clip, boxes, captions, burned_subs, music}."""
    from .styles import STYLE_PRESETS
    if style not in STYLE_PRESETS:
        raise ff.MediaError(f"Unknown style {style!r}. Pick one of: {', '.join(STYLE_PRESETS)}.")
    preset_cfg = STYLE_PRESETS[style]
    brand = {**DEFAULT_BRAND, **(brand or {})}
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
            sw, sh, dur, has_a = 1920, 1080, max(6.0, 3.5 * len(images or [])), False
        words = captions.fix_names(list(words or []), names)
        # clip window
        if clip:
            c0, c1, why = float(clip[0]), float(clip[1]), "given"
        elif preset_cfg["autoclip"] and source:
            c0, c1, why = pick_clip(words, dur, *preset_cfg["clip_range"])
        else:
            c0, c1, why = 0.0, min(dur, max_main), "full video"
        D = max(0.5, c1 - c0)
        cw = [{"w": w["w"], "s": w["s"] - c0, "e": w["e"] - c0} for w in words if c0 <= w["s"] < c1]
        silent = (not has_a) or (source is not None and ff.is_silent(source))
        if burned_subs is None:
            burned_subs = bool(source) and preset_cfg["fill"] == "fill" and detect_burned_subs(source, dur)
        cover_s = preset_cfg["cover_secs"] if source else 0.0
        facts = [f for f in (facts or []) if f.strip()]
        facts_s = preset_cfg["facts_secs"] if facts else 0.0
        total = cover_s + D + facts_s + CARD_SECS

        # stills for cover / facts backgrounds
        still = None
        if source:
            still = Image.open(frame_at(source, c0 + min(D * 0.3, 4.0), str(wd / "still.png")))
        elif images:
            still = Image.open(images[0])

        panel = _panel_for(preset_cfg["layout"], W, H, sw, sh)
        l, t, r, b = safe_area(W, H)
        if preset_cfg["layout"] == "huhbyte_news":
            cap_band = (panel[3] + int(H * 0.012), min(b - int(H * 0.06), panel[3] + int(H * 0.012) + int(H * 0.09)))
        elif preset_cfg["layout"] == "minimal":
            cap_band = (panel[3] + int(H * 0.04), panel[3] + int(H * 0.04) + int(H * 0.08))
        else:
            cap_band = None
        ov, boxes = _overlay(preset_cfg["layout"], W, H, brand, hook, credit, panel, acc, cap_band)
        ov_path = wd / "overlay.png"
        ov.save(ov_path)
        cards = {"end": str(wd / "end.png")}
        boxes["end_card"] = end_card(cards["end"], W, H, brand)
        if cover_s:
            cards["cover"] = str(wd / "cover.png")
            boxes["cover_card"] = cover_card(cards["cover"], W, H, brand, hook, still, label=preset_cfg["cover_label"],
                                             accent_words=acc)
        if facts_s:
            cards["facts"] = str(wd / "facts.png")
            boxes["facts_card"] = facts_card(cards["facts"], W, H, brand, preset_cfg["facts_title"], facts, still)

        # captions
        ass_path = None
        cap_problems: list[str] = []
        if cw and preset_cfg["captions"]:
            cy = None
            if preset_cfg["layout"] in ("opus", "hype"):
                cy = 0.60 if H / W > 1.6 else 0.70
            ass = captions.build_ass(cw, preset_cfg["captions"], W=W, H=H, accent=brand["accent"], text=brand["text"],
                                     outline=brand["bg"], offset=0.0, band=cap_band, center_y=cy, end=D)
            cap_problems = captions.check_ass_fits(ass, W, H)
            ass_path = wd / "caps.ass"
            ass_path.write_text(ass)

        # music
        mood = preset_cfg["music"] if music_mood == "auto" else music_mood
        wav = None
        if mood != "none":
            wav = music.make(str(wd / "music.wav"), total + 0.5, mood)

        args, fc = _graph(source, images, preset_cfg, W, H, panel, c0, D, cover_s, facts_s, total, ov_path, cards,
                          ass_path, burned_subs, has_a and not silent, wav, sw, sh, brand)
        outp = Path(out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        cmd = ["-y", "-v", "error", *args, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
               "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-movflags", "+faststart", str(outp)]
        (wd / "ffmpeg_args.json").write_text(json.dumps(cmd, indent=0))
        ff.run(cmd, timeout=1800)
        got = ff.probe(str(outp))
        return {"path": str(outp), "style": style, "ratio": ratio, "width": got["width"], "height": got["height"],
                "duration": round(got["duration"], 2), "clip": [round(c0, 2), round(c1, 2), why], "boxes": boxes,
                "safe_area": safe_area(W, H), "caption_problems": cap_problems, "burned_subs": burned_subs,
                "music": mood if wav else "none", "source_silent": silent, "cover_secs": cover_s, "facts_secs": facts_s,
                "credit": credit}
    finally:
        if own:
            shutil.rmtree(wd, ignore_errors=True)


def _graph(source, images, cfg, W, H, panel, c0, D, cover_s, facts_s, total, ov_path, cards, ass_path, burned_subs,
           speech, wav, sw, sh, brand):
    """FFmpeg inputs + one filter graph: [cover] + [main] + [facts] + [end] video, and the audio mix."""
    args: list[str] = []
    n = 0
    vin = None
    if source:
        args += ["-ss", f"{c0:.3f}", "-t", f"{D:.3f}", "-i", source]
        vin, n = n, n + 1
    else:
        for p in images or []:
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D / max(1, len(images)):.3f}", "-i", p]
        n += len(images or [])
    ov_i = n
    args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", str(ov_path)]
    n += 1
    idx = {}
    for k in ("cover", "facts", "end"):
        if k in cards:
            secs = {"cover": cover_s, "facts": facts_s, "end": CARD_SECS}[k]
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{secs:.3f}", "-i", cards[k]]
            idx[k], n = n, n + 1
    wav_i = None
    if wav:
        args += ["-i", wav]
        wav_i, n = n, n + 1
    args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=44100:cl=stereo"]
    sil_i, n = n, n + 1

    bg = brand["bg"].lstrip("#")
    parts = []
    px0, py0, px1, py1 = panel
    pw, ph = px1 - px0, py1 - py0
    if source:
        src = f"[{vin}:v]setpts=PTS-STARTPTS,fps={FPS},setsar=1"
        if burned_subs:  # crop the burned-in subtitle band away so captions never double up
            src += ",crop=iw:ih*0.84:0:0"
        if cfg["fill"] == "fill":
            parts.append(f"{src},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base]")
        elif cfg["fill"] == "blurpanel":
            parts.append(f"{src},split[s1][s2];[s1]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                         f"crop={W // 4}:{H // 4},gblur=sigma=12,scale={W}:{H},eq=brightness=-0.18:saturation=0.8[bgv];"
                         f"[s2]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}[pv];"
                         f"[bgv][pv]overlay={px0}:{py0}[base]")
        else:  # panel on the brand ground
            parts.append(f"color=c=0x{bg}:s={W}x{H}:r={FPS}:d={D:.3f}[bgc];{src},scale={pw}:{ph}:"
                         f"force_original_aspect_ratio=increase,crop={pw}:{ph}[pv];[bgc][pv]overlay={px0}:{py0}:shortest=1[base]")
    else:  # slideshow with a slow push-in per image
        k = len(images or [])
        per = D / max(1, k)
        segs = []
        for i in range(k):
            fr = int(per * FPS)
            parts.append(f"[{i}:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                         f"zoompan=z='min(1.0+0.0009*on,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={fr}:s={W}x{H}:fps={FPS},"
                         f"setsar=1[k{i}]")
            segs.append(f"[k{i}]")
        parts.append(f"{''.join(segs)}concat=n={k}:v=1:a=0[base]")
    chain = f"[base][{ov_i}:v]overlay=0:0:shortest=1"
    if cfg.get("flash") and source:
        chain += ",fade=t=in:st=0:d=0.18:color=white"
    if ass_path:
        fontsdir = str(Path(__file__).resolve().parent.parent / "fonts")
        chain += f",ass='{_esc(str(ass_path))}':fontsdir='{_esc(fontsdir)}'"
    chain += f",format=yuv420p,setsar=1[main]"
    parts.append(chain)
    order = []
    for k in ("cover",):
        if k in idx:
            parts.append(f"[{idx[k]}:v]fps={FPS},format=yuv420p,setsar=1[c_{k}]")
            order.append(f"[c_{k}]")
    order.append("[main]")
    for k in ("facts", "end"):
        if k in idx:
            parts.append(f"[{idx[k]}:v]fps={FPS},fade=t=in:st=0:d=0.25,format=yuv420p,setsar=1[c_{k}]")
            order.append(f"[c_{k}]")
    parts.append(f"{''.join(order)}concat=n={len(order)}:v=1:a=0[v]")

    # audio
    if source and speech:
        d_ms = int(cover_s * 1000)
        parts.append(f"[{vin}:a]asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=stereo,"
                     f"afade=t=out:st={max(0.0, D - 0.5):.3f}:d=0.5,adelay={d_ms}|{d_ms},apad,atrim=0:{total:.3f}[sp]")
        if wav_i is not None:
            parts.append(f"[{wav_i}:a]atrim=0:{total:.3f},volume=0.55[mu];[sp]asplit[sp1][sp2];"
                         f"[mu][sp1]sidechaincompress=threshold=0.02:ratio=12:attack=15:release=350[duck];"
                         f"[sp2][duck]amix=inputs=2:weights='1 0.35':normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[a]")
        else:
            parts.append("[sp]loudnorm=I=-14:TP=-1.5:LRA=11[a]")
    elif wav_i is not None:
        parts.append(f"[{wav_i}:a]atrim=0:{total:.3f},loudnorm=I=-16:TP=-1.5:LRA=11[a]")
    else:
        parts.append(f"[{sil_i}:a]anull[a]")
    return args, ";".join(parts)


def _esc(p: str) -> str:
    return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
