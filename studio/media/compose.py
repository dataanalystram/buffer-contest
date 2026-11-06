"""Edit a launch video into a branded short, in one of four styles, for any of 9:16 / 4:5 / 1:1.

    render_style(source, "huhbyte_news", ratio="9:16", hook="...", brand={...}, words=[...], out="reel.mp4")

Styles (studio/media/styles.py describes them for agents and the UI):

  opus           Opus-Clip-style auto-clip: the best 20-45 s moment of the transcript, full-bleed reframed video,
                 big bouncing word captions, hook title on top from frame 0, Follow end card.
  huhbyte_news   The Newsroom layout (id kept for compatibility; first built as the owner's HuhByte layout, code/hb.py build_reel/add_cta): branded cover card with the hook
                 (first second), black frame with logo + hook headline, the FULL video in a panel (never trimmed),
                 word-highlight captions under it, credit line, Follow pill, facts card, 3 s Follow end card.
  minimal        Calm explainer: blurred-video backdrop, rounded video panel, small title, clean boxed captions.
  hype           Fast and loud: full-bleed, white flash + giant hook in the first second, one-word captions that
                 punch in, pulse music, end card.

Audio rule (owner, 3 Nov 2026, replaces "never ship a silent Reel"): NO music is ever added by default. The video's own
sound is kept; a silent video stays silent unless the person picked music (music_mood "cinematic" / "pulse"; "auto" and
"none" both mean no music; Instagram music is added by Buffer as metadata, never baked in). With music picked, it is ducked
~20 dB under the speech (sidechain), then the mix is normalised to -14 LUFS. Every layout box is computed, every text block is auto-fitted (textfit) inside the platform
safe area, and ``render_style`` returns the boxes so tests can prove nothing clips or overlaps.
Credit: layouts adapted from dataanalystram/Huhbyte.ai (same owner), see NOTICE.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import captions, ff, textfit  # music (numpy) is imported where a video gets a bed: the web dyno has no numpy

def _hl(brand: dict) -> str:
    """The colour of the highlighted headline words (covers, hooks) and the theme progress bar: the brand kit's Accent
    (the builder's fourth colour, kit colors.secondary) when it reads on the ground, else the brand accent. Burned-in
    captions keep the brand accent: they sit on moving footage, where only the strongest colour reads."""
    return brand.get("highlight") or brand["accent"]


SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
FPS = 30
CARD_SECS = 3.0
# The chosen cover held as the Reel's very first frames, so Buffer's thumbnailOffset (the only cover control its API has:
# VideoAssetInput.thumbnailUrl is rejected for video) can point Instagram / TikTok at it. Six frames: long enough that the
# offset lands on it after Instagram's re-encode, short enough that a viewer never sees a held still (owner, 3 Nov 2026:
# the 0.6 s cover + the theme's title card played as two stills before the video, and again on every loop).
COVER_FRAME_SECS = 0.2


def cover_thumb_ms(secs: float) -> int:
    """Buffer thumbnailOffset (ms) for a cover held ``secs`` at the start: the middle of it."""
    return int(round(float(secs) * 500))


def cover_frame_secs(secs: float | None) -> float:
    """The cover-frame length actually used: COVER_FRAME_SECS unless a shorter/longer one in 0.1-0.25 s is asked for."""
    try:
        v = float(secs) if secs is not None else COVER_FRAME_SECS
    except (TypeError, ValueError):
        v = COVER_FRAME_SECS
    return round(min(0.25, max(0.1, v)), 3)
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


def _outline_pill(d: ImageDraw.ImageDraw, text: str, cx: int, y: int, brand: dict, height: int = 60, maxw: int = 900) -> tuple:
    """A quiet outlined pill (source / 'reported by' lines): accent outline, text in the brand's text colour."""
    size = max(16, int(height * 0.42))
    r = textfit.fit(text, "Inter-SemiBold.ttf", maxw - size * 2, height, max_size=size, min_size=14, max_lines=1)
    f = textfit.font("Inter-SemiBold.ttf", r.size)
    tw = f.getlength(r.lines[0])
    pw = int(tw + r.size * 2.2)
    box = (cx - pw // 2, y, cx + pw // 2, y + height)
    d.rounded_rectangle(box, height // 2, fill=(0, 0, 0, 110), outline=_rgb(brand["accent"]) + (255,), width=3)
    a, de = f.getmetrics()
    d.text((cx - tw / 2, y + (height - (a + de)) / 2), r.lines[0], font=f, fill=_rgb(brand.get("text", "#FFFFFF")))
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


def _caption_band(a, lo: float = 0.55, hi: float = 0.95, bh: float = 0.1):
    """The (top, bottom, mask) of the densest band of centred, bright, sharp-edged text in rows [lo, hi] of a greyscale
    frame, or None. Burned captions are centred (word-by-word or a line), so the text mass sits around the middle and the
    outer margins are empty; slide text and name straps are left-aligned and fail the balance test."""
    import numpy as np
    h, w = a.shape
    txt = (a[:, 1:] > 150) & (np.abs(np.diff(a, axis=1)) > 60)  # 150: accent-coloured caption words count too
    best = None
    step, size = max(1, int(h * 0.02)), max(4, int(h * bh))
    for r0 in range(int(h * lo), int(h * hi) - size + 1, step):
        band = txt[r0:r0 + size]
        mid = band[:, int(w * 0.1):int(w * 0.9)]
        sc = float(mid.mean())
        if sc < 0.012:
            continue
        cols = band.sum(axis=0).astype(float)
        left, right = cols[int(w * 0.1):w // 2].sum(), cols[w // 2:int(w * 0.9)].sum()
        outer = cols[:int(w * 0.06)].sum() + cols[int(w * 0.94):].sum()
        if min(left, right) <= 0 or max(left, right) / min(left, right) > 2.2 or outer > 0.08 * cols.sum():
            continue
        if best is None or sc > best[0]:
            best = (sc, r0, band)
    if best is None:
        return None
    return best[1] / h, (best[1] + size) / h, best[2]


def detect_burned_captions(src: str, start: float = 0.0, end: float | None = None, samples: int = 10) -> dict | None:
    """Captions already burned into the picture anywhere in the lower half (an Opus-style word caption at ~75%, a subtitle
    line at the bottom): centred bright text at about the same height in most sampled frames, whose words CHANGE from
    frame to frame (a static name strap or logo doesn't). Returns {"top", "bottom", "frames"} as fractions of the source
    height, or None. Owner bug 2 Nov 2026: edit_video captioned an already-captioned clip and the captions doubled."""
    import numpy as np
    if end is None:
        end = ff.probe(src)["duration"]
    span = max(0.1, end - start)
    found = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(samples):
            t = start + span * (i + 0.5) / samples
            p = Path(td) / f"c{i}.png"
            try:
                ff.run(["-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=360:-2",
                        "-update", "1", str(p)], timeout=60)
            except ff.MediaError:
                continue
            b = _caption_band(np.asarray(Image.open(p).convert("L"), dtype=np.int16))
            if b:
                found.append(b)
    need = max(3, int(samples * 0.35 + 0.5))  # captions pause between sentences: about a third of the frames is enough
    if len(found) < need:
        return None
    tops = sorted(b[0] for b in found)
    med = tops[len(tops) // 2]
    same = [b for b in found if abs(b[0] - med) <= 0.08]
    if len(same) < need:
        return None
    changed = 0
    for x, y in zip(same, same[1:]):
        m1, m2 = x[2], y[2]
        hh = min(m1.shape[0], m2.shape[0])
        diff = float((m1[:hh] ^ m2[:hh]).mean())
        changed += diff > 0.35 * max(float(m1.mean()), float(m2.mean()), 1e-6)
    if changed < max(2, (len(same) - 1) // 2):  # noqa: SIM102
        return None  # the same text every time: a strap or a logo, not captions
    return {"top": round(min(b[0] for b in same), 3), "bottom": round(max(b[1] for b in same), 3), "frames": len(same)}


def _band_rows(a, lo: float = 0.55, hi: float = 0.95, thr: float = 0.06) -> tuple[float, float] | None:
    """Rows in [lo, hi] of a greyscale frame dense with sharp vertical edges (text / a graphic bar), as (top, bottom)
    fractions of the frame height, or None. The densest contiguous run of at least 3% of the height wins."""
    import numpy as np
    h, w = a.shape
    dens = (np.abs(np.diff(a, axis=1)) > 50).mean(axis=1)
    top_ref = float(np.median(dens[int(h * 0.05):int(h * 0.45)]))  # a busy picture has edges everywhere: not a band
    best, run = None, None
    for r in range(int(h * lo), int(h * hi)):
        on = dens[r] > max(thr, 2.5 * top_ref + 0.01)
        if on and run is None:
            run = r
        if (not on or r == int(h * hi) - 1) and run is not None:
            end = r if not on else r + 1
            if end - run >= max(3, int(h * 0.03)) and (best is None or end - run > best[1] - best[0]):
                best = (run, end)
            run = None
    return (best[0] / h, best[1] / h) if best else None


def detect_lower_third(src: str, start: float = 0.0, end: float | None = None, samples: int = 6) -> dict | None:
    """A lower-third graphic band in the source (a name strap, a news ticker, a product caption bar): rows in the lower
    part of most sampled frames dense with text edges, at the same height each time, far denser than the top of the
    picture. Returns {"top", "bottom"} as fractions of the source height (min top / max bottom seen), or None.
    Our captions are then placed above it (render_style), in every ratio."""
    import numpy as np
    try:
        dur = ff.probe(src)["duration"]
    except Exception:  # noqa: BLE001  no probe: sample the range we were given
        dur = 0.0
    if end is None and not dur:
        return None
    end = dur if end is None or (dur and end > dur) else end  # cut_first_s sends [cut, cut + 600]: never sample past the end
    span = max(0.1, end - start)
    bands = []
    with tempfile.TemporaryDirectory() as td:
        for i in range(samples):
            t = start + span * (i + 1) / (samples + 1)
            p = Path(td) / f"l{i}.png"
            try:
                ff.run(["-y", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1", "-vf", "scale=480:-2",
                        "-update", "1", str(p)], timeout=60)
            except ff.MediaError:
                continue
            if not p.exists():  # ffmpeg writes nothing for a seek past the end and still exits 0
                continue
            b = _band_rows(np.asarray(Image.open(p).convert("L"), dtype=np.int16))
            if b:
                bands.append(b)
    if len(bands) < max(2, samples // 3):
        return None
    tops = sorted(b[0] for b in bands)
    med = tops[len(tops) // 2]
    same = [b for b in bands if abs(b[0] - med) <= 0.05]  # a strap sits at one height; moving texture doesn't
    if len(same) < max(2, samples // 3):
        return None
    return {"top": round(min(b[0] for b in same), 3), "bottom": round(max(b[1] for b in same), 3), "frames": len(same)}


def _keep(burned_subs) -> float:
    """How much of the source height survives the burned-caption crop: 0.84 (a bottom subtitle line) by default, or the
    detected caption band's top when edit_video replaces the source's own captions (captions="replace")."""
    if isinstance(burned_subs, float) and 0.3 <= burned_subs < 1.0:
        return burned_subs
    return 0.84


def band_in_output(band: dict, sw: int, sh: int, W: int, H: int, reframe: dict | None = None, burned_subs: bool = False) -> int | None:
    """Where a source band's top lands in the output frame (pixels) for the full-bleed styles, or None when it is cropped
    away. Mirrors _main_graph: the burned-subtitle crop, then track (full height), pad (fit + centre) or cover crop."""
    top = band["top"]
    if burned_subs:
        keep = _keep(burned_subs)
        if top >= keep:
            return None
        top = top / keep
        sh = int(sh * keep)
    mode = (reframe or {}).get("mode")
    if mode == "track":
        y = top * H
    elif mode == "pad":
        k = min(W / sw, H / sh)
        y = (H - sh * k) / 2 + top * sh * k
    else:
        k = max(W / sw, H / sh)
        y = top * sh * k - (sh * k - H) / 2
    return int(y) if 0 < y < H else None


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
    bb, _ = _text_block(d, "Follow", brand["head_font"], (l, y, r, y + int(H * 0.13)), max_size=int(W * 0.2), max_lines=1,
                        fill=brand["text"])
    boxes["follow"] = bb
    y = bb[3] + int(H * 0.015)
    bb, _ = _text_block(d, brand["handle"], brand["head_font"], (l, y, r, y + int(H * 0.09)), max_size=int(W * 0.15),
                        max_lines=1, fill=brand["accent"])
    boxes["handle"] = bb
    y = bb[3] + int(H * 0.03)
    d.line((W // 2 - 60, y, W // 2 + 60, y), fill=(70, 70, 76), width=2)
    y += int(H * 0.03)
    bb, _ = _text_block(d, brand["end_line"], "Inter-Regular.ttf", (l, y, r, y + int(H * 0.08)), max_size=40, max_lines=2,
                        fill=brand.get("muted") if brand["text"].upper() != "#FFFFFF" else "#DADADE")
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
                        accent_words=accent_words, accent=_hl(brand), valign="bottom")
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
                            accent_words=accent_words, accent=_hl(brand), valign="bottom")
        boxes["hook"] = bb
        d.rectangle(L["panel"], fill=(0, 0, 0, 0))
        boxes["panel"] = L["panel"]
        if "pill_y" in L:
            boxes["pill"] = _pill(d, "Follow " + brand["handle"], W // 2, L["pill_y"], brand, size=32, maxw=r - l)
    elif style in ("opus", "hype"):
        hb = L["hook"]
        if style == "opus" and hb and hook.strip():
            d.rounded_rectangle((hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10), 28, fill=(255, 255, 255, 236))
            bb, _ = _text_block(d, hook, brand["bold_font"], (hb[0] + 18, hb[1] + 4, hb[2] - 18, hb[3] - 4), max_size=64,
                                min_size=24, max_lines=3, fill="#111114")
            boxes["hook"] = (hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10)
            boxes["hook_text"] = bb
        if credit:
            if L.get("credit_xy"):  # face-safe spot (studio/media/facesafe.place_opus)
                cx, y0 = L["credit_xy"]
            else:
                cx, y0 = l, ((hb[3] + 24) if (style == "opus" and hb) else t)
            boxes["credit"] = _credit_chip(d, credit, cx, y0, r - l)
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
                        accent_words=accent_words, accent=_hl(brand), stroke=stroke)
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
MUSIC_MOODS = ("cinematic", "pulse")


def picked_music(music_mood: str | None) -> str:
    """The score a render bakes in: only one the person PICKED ("cinematic" / "pulse", or the old aliases "hype" /
    "energetic"). "auto", "none", "" and anything unknown are no music: a silent video stays silent (owner, 3 Nov 2026)."""
    m = str(music_mood or "").strip().lower()
    if m in ("hype", "energetic"):
        return "pulse"
    return m if m in MUSIC_MOODS else "none"


CREDIT_CHIP_H = 46  # _credit_chip at its 24 px maximum: 24 px Inter ascent + descent + 16 px padding


def _cy_at(caption_cy):
    """[(start, end, centre y)] -> t -> centre y (None outside every segment)."""
    if not caption_cy:
        return None
    segs = sorted(caption_cy)

    def at(t: float):
        for a, b, cy in segs:
            if a - 1e-6 <= t < b:
                return cy
        return segs[-1][2] if t >= segs[-1][1] else segs[0][2]
    return at


def _face_safe_layout(L: dict, W: int, H: int, brand: dict, hook: str, credit: str, caps_on: bool, cfg: dict,
                      face_safe: dict) -> tuple[dict, dict]:
    """The Opus-style layout placed clear of the faces (studio/media/facesafe.place_opus), at the caption level picked
    BEFORE rendering from the face track's predicted cover: full when it covers <= QA_MAX of a face, else the level with
    the lowest prediction (compact: one smaller line under the chin). face_safe["min_level"] starts higher (a re-place)."""
    from . import facesafe
    st0 = captions.STYLES[cfg["captions"]]

    def hook_fit(text: str, w: int, h: int):
        r = textfit.fit(text, brand["bold_font"], w - 36, max(8, h - 8), max_size=64, min_size=30, max_lines=3)
        return bool(r.ok), int(r.height)
    pred = face_safe.get("pred") or []
    lo = face_safe.get("min_level") or 0
    lo = max(0, min(int(lo) if isinstance(lo, int) else 0, len(facesafe.LEVELS) - 1))
    credit_h = CREDIT_CHIP_H if credit else 0
    best, tried = None, {}
    for lv in range(lo, len(facesafe.LEVELS)):
        st = st0 if lv == 0 else facesafe.compact_style(st0)
        cap_h = facesafe.caption_height(st, H)
        L2, rec = facesafe.place_opus(dict(L), W, H, static=face_safe.get("static"), segments=face_safe.get("segments") or [],
                                      cap_h=cap_h, hook=hook, hook_fit=hook_fit, credit_h=credit_h, captions_on=caps_on)
        pc = facesafe.predict_cover(L2, pred, W, H, cap_h=cap_h, captions_on=caps_on, credit_h=credit_h)
        tried[facesafe.LEVELS[lv]] = pc
        if best is None or pc < best[0] or pc <= facesafe.QA_MAX:
            best = (pc, lv, L2, rec, cap_h, st)
        if pc <= facesafe.QA_MAX:
            break
    pc, lv, L2, rec, cap_h, st = best
    rec.update(cap_h=cap_h, level=facesafe.LEVELS[lv], predicted_cover=pc, levels=tried, sig=placement_sig_of(L2, lv))
    if lv:
        L2["_cap_style"] = st
    return L2, rec


def placement_sig_of(L: dict, lv: int) -> str:
    from . import facesafe
    return facesafe.placement_sig(L, facesafe.LEVELS[lv])


def render_style(source: str | None, style: str, *, out: str, ratio: str = "9:16", hook: str = "", facts: list[str] | None = None,
                 brand: dict | None = None, words: list[dict] | None = None, credit: str = "", clip: tuple | None = None,
                 images: list[str] | None = None, music_mood: str = "auto", names: list[str] | None = None,
                 burned_subs: bool | None = None, captions_mode: str = "auto", accent_words: list[str] | None = None,
                 crf: int = 21, preset: str = "veryfast", workdir: str | None = None, max_main: float = 180.0,
                 brand_colors: bool = False, reframe: dict | None = None, lower_third_mode="auto",
                 detect_own_captions: bool = False, brand_pass: bool | dict = False, cover_png: str | None = None,
                 cover_secs: float = COVER_FRAME_SECS, logo: bool = True, face_safe: dict | None = None,
                 captions_style: str | None = None, skip_sig: str | None = None) -> dict:
    """Render one styled video. ``source`` None + ``images`` -> a slow push-in slideshow (no-media fallback).
    captions_mode: auto (off in panel styles when the source already has burned-in subtitles) | on | off.
    ``style`` is a base style or a theme (styles.THEMES: its grade, effects, captions, cards and chrome on a base
    layout); brand_colors=True keeps the brand's accent inside a theme.
    reframe: a crop plan for the full-bleed styles (``longform.reframe_plan`` / ``take.crop_plan``): ``track`` follows
    the face, ``pad`` shows the whole frame over a blurred copy, anything else (or None) is the centre crop.
    lower_third_mode: auto (detect a lower-third band and lift the captions above it) | off | a band dict.
    detect_own_captions: with captions_mode auto, look for captions already burned into the picture anywhere in the
    lower half (detect_burned_captions) and add none when they are there (edit_video of a finished clip). captions_mode
    "replace" crops those captions off (full-bleed styles) and captions the video again.
    brand_pass: brand the whole video (studio/media/brandpass.py): a hook title over the opening seconds, a logo bug, a
    lower-third with the credit, captions in the brand colours; a dict turns single elements off ({"lower": False}).
    cover_png: the chosen cover (studio/media/covers.py) held for ``cover_secs`` as the very first frames, so Instagram /
    TikTok can use it as the Reel cover (Buffer only takes a frame offset, thumbnailOffset, never a separate image).
    style "none": the video as it is (``render_plain``): trimmed / fitted, ``logo`` bug when True, the end card.
    face_safe: {"static": keep-out (x0, y0, x1, y1), "segments": [{start, end, keepout}]} in output pixels / clip
    seconds (studio/media/facesafe): the Opus-style hook box, credit chip and captions are placed clear of the faces
    there (the hook shrinks, shortens or drops when nothing fits). The result's ``face_safe`` says what moved.
    captions_style: a captions.STYLES id instead of the style's own (Layout & captions > Caption look on a shorts clip).
    skip_sig: a placement signature (face_safe["sig"] of an earlier render): when this placement lands on exactly the same
    spots, nothing is encoded and the result is {"skipped": True, "face_safe": ...} (a re-place that changes nothing).
    Returns {path, style, ratio, width, height, duration, clip, boxes, safe_area, caption_problems, ...}."""
    from . import theme_cards
    from .styles import ALL_IDS, NONE, apply_palette, config, resolve
    style = resolve(style)
    if style == NONE:
        if not source:
            raise ff.MediaError("No style needs a video to post as it is.")
        return render_plain(source, out=out, ratio=ratio, brand=brand, clip=clip, logo=logo, cover_png=cover_png,
                            cover_secs=cover_secs, crf=crf, preset=preset, workdir=workdir, max_main=max_main)
    if brand_pass:
        brand_colors = True  # branded captions: the brand's accent stays inside any theme
    try:
        cfg = config(style)
    except KeyError:
        raise ff.MediaError(f"Unknown style {style!r}. Pick one of: {', '.join(ALL_IDS)}.") from None
    if captions_style and cfg.get("captions"):
        if captions_style not in captions.STYLES:
            raise ff.MediaError(f"Unknown caption style {captions_style!r}.")
        cfg = {**cfg, "captions": captions_style}
    tm: dict = {}
    brand = {**DEFAULT_BRAND, **(brand or {})}
    if cfg.get("theme"):
        brand = apply_palette(brand, cfg, keep_brand=brand_colors)
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
            c0, c1 = clamp_clip(clip, dur if source else 0.0)
            why = "given"
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
            captions_mode in ("on", "replace") or (captions_mode == "auto" and not (burned_subs and cfg["fill"] != "fill")))
        captions_note, own_caps = "", None
        if caps_on and source and (captions_mode == "replace" or (captions_mode == "auto" and detect_own_captions)):
            # captions already burned into the picture (an Opus-style clip): ours would double them (owner bug 2 Nov 2026)
            own_caps = detect_burned_captions(source, c0, c1)
            if own_caps and captions_mode == "replace" and cfg["fill"] == "fill" and own_caps["top"] >= 0.45:
                burned_subs = round(max(0.3, own_caps["top"] - 0.01), 3)  # crop their captions off, then caption it ourselves
                captions_note = ("This video already had captions burned in, so the bottom of the frame (from "
                                 f"{int(own_caps['top'] * 100)}% down) was cropped off and new captions added.")
            elif own_caps:
                caps_on = False
                captions_note = ("This video already has captions burned in, so no new captions were added (they would "
                                 "double up). Ask for captions=\"replace\" to crop the old ones off and caption it again.")
        has_cover_frame = bool(cover_png and source and Path(cover_png).is_file())
        # One opening still at most: with the chosen cover as the first frames, the theme's title card is not added after
        # it (it repeated the same title as a second still; the hook stays on screen over the video from frame 0).
        cover_s = cfg["cover_secs"] if (source and not has_cover_frame) else 0.0
        facts = [f for f in (facts or []) if f.strip()]
        facts_s = cfg["facts_secs"] if facts else 0.0
        total = cover_s + D + facts_s + CARD_SECS

        lsw, lsh = (int(1000 * cfg["gate"][0] / cfg["gate"][1]), 1000) if cfg.get("gate") else (sw, sh)
        if cfg.get("tall_aspect") and H / W > 1.6:  # vertical is its own layout: a square crop fills the stage
            lsw, lsh = cfg["tall_aspect"]
        L = layout(cfg["layout"], W, H, lsw, lsh, brand, hook, caps_on)
        lower_third = None
        pad_band = None
        if caps_on and source and cfg["fill"] == "fill" and "captions_center" in L and (reframe or {}).get("mode") == "pad":
            # screen recordings / slides (pad): the whole frame sits centred, so the captions go in the empty band under
            # it instead of over the slide (and a name line on the slide is no longer in their way)
            k = min(W / sw, (H / (sh * _keep(burned_subs) if burned_subs else sh)))
            fit_bottom = (H + (sh * _keep(burned_subs) if burned_subs else sh) * k) / 2
            if H - fit_bottom >= H * 0.14:
                pad_band = int(fit_bottom + (H - fit_bottom) * 0.30)
                L["captions_center"] = min(pad_band, int(H * 0.80))
        if pad_band is None and caps_on and source and cfg["fill"] == "fill" and "captions_center" in L and lower_third_mode != "off":
            # a lower-third graphic in the source (name strap, ticker): our captions go above it, in every ratio
            lower_third = lower_third_mode if isinstance(lower_third_mode, dict) else detect_lower_third(source, c0, c1)
            y_band = band_in_output(lower_third, sw, sh, W, H, reframe, burned_subs) if lower_third else None
            if y_band is not None:
                cst = captions.STYLES[cfg["captions"]]
                half = int(H * cst.size_frac * 1.25 * cst.max_lines / 2) + int(H * 0.02)
                cy = min(L["captions_center"], y_band - half)
                L["captions_center"] = max(int(H * 0.30), cy)
                if cfg["layout"] == "hype" and "hook" in L:  # the giant first-beat hook stays clear of the lifted line
                    hx0, hy0, hx1, hy1 = L["hook"]
                    L["hook"] = (hx0, hy0, hx1, max(hy0 + int(H * 0.08), min(hy1, L["captions_center"] - int(H * 0.09))))
                lower_third = {**lower_third, "out_top": y_band}
        # "own video" is not a source to credit on screen: no chip; the brand pass lower-third shows the handle instead
        chip_credit = "" if (credit or "").strip().lower() in ("own video", "your video") else credit
        fs_rec, cap_style = None, None
        if face_safe is not None and cfg["layout"] == "opus":  # never on the face (owner bug 6 Nov 2026)
            safe0 = L["safe"]
            if lower_third and lower_third.get("out_top"):  # and never on the source's name strap: place above it
                l_, t_, r_, b_ = safe0
                L = {**L, "safe": (l_, t_, r_, max(t_ + 1, min(b_, int(lower_third["out_top"]) - int(H * 0.02))))}
            L, fs_rec = _face_safe_layout(L, W, H, brand, hook, chip_credit, caps_on, cfg, face_safe)
            L["safe"] = safe0
            hook = L.get("hook_text", hook) if L.get("hook") else ""
            cap_style = L.pop("_cap_style", None)  # the compact caption level, when the full one would cover the face
            if skip_sig and fs_rec.get("sig") == skip_sig:  # a re-place landing where the first render already was
                return {"skipped": True, "path": None, "face_safe": fs_rec, "style": style, "ratio": ratio}
        still = Image.open(frame_at(source, c0 + min(D * 0.3, 4.0), str(wd / "still.png"))) if source else Image.open(images[0])
        if cfg.get("chrome"):
            ov, boxes = theme_cards.overlay(cfg, W, H, brand, hook, chip_credit, L, acc)
        else:
            ov, boxes = _overlay(cfg["layout"], W, H, brand, hook, chip_credit, L, acc)
        stage_path = None
        if cfg.get("fill") == "stage":
            stage_path = wd / "stage.png"
            theme_cards.stage(cfg, W, H, brand, L["panel"]).save(stage_path)
        if "cap_band" in L:
            boxes["cap_band"] = L["cap_band"]
        ov_path = wd / "overlay.png"
        ov.save(ov_path)
        extras: list = []
        if brand_pass and source:
            from . import brandpass
            extras, boxes["brand_pass"] = brandpass.layers(
                str(wd), W, H, brand, hook=hook, credit=credit, D=D, boxes=boxes, cfg=cfg, has_cover=bool(cover_s),
                captions_center=L.get("captions_center") if caps_on else None, accent_words=list(acc) or None,
                want=brand_pass if isinstance(brand_pass, dict) else None)
        hook_path = None
        if cfg["layout"] == "hype" and hook.strip():
            hk, boxes["hook"] = _hype_hook(W, H, brand, hook, L, acc)
            hook_path = wd / "hook.png"
            hk.save(hook_path)
        segs = []
        pre_s = 0.0
        if has_cover_frame:  # the chosen cover as the very first frames (Reel cover via thumbnailOffset)
            pre_s = cover_frame_secs(cover_secs)
            cp = wd / "cover_frame.png"
            Image.open(cover_png).convert("RGB").resize((W, H), Image.LANCZOS).save(cp)
            segs.append(_card_segment(str(cp), pre_s, str(wd / "s00_cover.mp4"), crf, preset, fade=False))
            boxes["cover_frame"] = {"secs": pre_s, "thumbnail_offset_ms": cover_thumb_ms(pre_s)}
        if cover_s:
            if cfg.get("card"):
                boxes["cover_boxes"] = theme_cards.cover(cfg, str(wd / "cover.png"), W, H, brand, hook, still,
                                                         label=cfg["cover_label"], accent_words=acc)
            else:
                cover_card(str(wd / "cover.png"), W, H, brand, hook, still, label=cfg["cover_label"], accent_words=acc)
            boxes["cover_card"] = "cover.png"
            segs.append(_card_segment(str(wd / "cover.png"), cover_s, str(wd / "s0_cover.mp4"), crf, preset, fade=False))

        _t = time.time()
        ass_path = None
        cap_problems: list[str] = []
        if caps_on:
            ass = captions.build_ass(cw, cfg["captions"], W=W, H=H, accent=brand["accent"], text=brand["text"],
                                     outline=brand["bg"], band=(L["cap_band"][1], L["cap_band"][3]) if "cap_band" in L else None,
                                     center_y=(L["captions_center"] / H) if "captions_center" in L else None, end=D,
                                     palette={k: brand[k] for k in ("text", "accent", "bg", "muted", "on_accent") if brand.get(k)},
                                     left=L.get("cap_left", L["safe"][0]), center_y_at=_cy_at(L.get("caption_cy")),
                                     cap_style=cap_style)
            cap_problems = captions.check_ass_fits(ass, W, H)
            ass_path = wd / "caps.ass"
            ass_path.write_text(ass)

        tm["captions"] = round(time.time() - _t, 2)
        _t = time.time()
        main = str(wd / "s1_main.mp4")
        args, fc = _main_graph(source, images, cfg, W, H, L["panel"], c0, D, ov_path, ass_path, burned_subs, brand,
                               hook_path=hook_path, stage_path=stage_path, L=L, wd=wd, reframe=reframe, extras=extras)
        ff.run(["-y", "-v", "error", *args, "-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{D:.3f}",
                *_venc(crf, preset), main], timeout=1800)
        segs.append(main)
        tm["encode"] = round(time.time() - _t, 2)
        _t = time.time()
        if facts_s:
            if cfg.get("card"):
                theme_cards.facts(cfg, str(wd / "facts.png"), W, H, brand, cfg["facts_title"] or "What it does", facts, still)
            else:
                facts_card(str(wd / "facts.png"), W, H, brand, cfg["facts_title"], facts, still)
            segs.append(_card_segment(str(wd / "facts.png"), facts_s, str(wd / "s2_facts.mp4"), crf, preset))
        boxes["end_card"] = end_card(str(wd / "end.png"), W, H, brand)
        segs.append(_card_segment(str(wd / "end.png"), CARD_SECS, str(wd / "s3_end.mp4"), crf, preset))
        lst = wd / "segments.txt"
        lst.write_text("".join(f"file '{Path(s).resolve()}'\n" for s in segs))
        video = str(wd / "video.mp4")
        ff.run(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", video], timeout=300)

        tm["cards"] = round(time.time() - _t, 2)  # facts + end card segments and the concat
        _t = time.time()
        mood = picked_music(music_mood)  # never the theme's score by default (owner, 3 Nov 2026: a piano bed on his upload)
        wav = None
        if mood != "none":
            from . import music
            wav = music.make(str(wd / "music.wav"), total + 0.5, mood)
        outp = Path(out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        total += pre_s
        _mux_audio(video, source if (source and not silent) else None, c0, D, cover_s + pre_s, total, wav, str(outp))
        tm["audio_mux"] = round(time.time() - _t, 2)
        got = ff.probe(str(outp))
        return {"timings": tm, "path": str(outp), "style": style, "theme": cfg.get("theme"), "ratio": ratio, "width": got["width"], "height": got["height"],
                "duration": round(got["duration"], 2), "clip": [round(c0, 2), round(c1, 2), why], "boxes": boxes,
                "layout": {k: v for k, v in L.items()}, "safe_area": safe_area(W, H), "caption_problems": cap_problems,
                "captions": caps_on, "burned_subs": bool(burned_subs), "lower_third": lower_third,
                "captions_note": captions_note, "source_captions": {k: own_caps[k] for k in ("top", "bottom")} if own_caps else None,
                "captions_center": L.get("captions_center"), "music": mood if wav else "none", "source_silent": silent,
                "cover_secs": cover_s, "facts_secs": facts_s, "credit": credit, "cover_frame_secs": pre_s,
                "thumbnail_offset_ms": cover_thumb_ms(pre_s) if pre_s else None,
                "brand_pass": sorted((boxes.get("brand_pass") or {}).keys()) if brand_pass else [],
                "face_safe": fs_rec, "hook_shown": hook}
    finally:
        if own:
            shutil.rmtree(wd, ignore_errors=True)



def render_plain(source: str, *, out: str, ratio: str = "9:16", brand: dict | None = None, clip: tuple | None = None,
                 logo: bool = True, cover_png: str | None = None, cover_secs: float = COVER_FRAME_SECS, crf: int = 21,
                 preset: str = "veryfast", workdir: str | None = None, max_main: float = 180.0, end: bool = True) -> dict:
    """"No style: post my video as it is": the video only lightly finished. Trimmed (``clip``) and fitted to the frame
    (a full crop when the shape is close, else the whole frame over a blurred copy of itself), the logo bug when
    ``logo`` is on, the brand's Follow end card last, its own sound (no music, no captions, no theme)."""
    from . import brandpass
    brand = {**DEFAULT_BRAND, **(brand or {})}
    if ratio not in SIZES:
        raise ff.MediaError(f"Unknown ratio {ratio!r}. Use 9:16, 4:5 or 1:1.")
    W, H = SIZES[ratio]
    own = workdir is None
    wd = Path(workdir or tempfile.mkdtemp(prefix="hqp-"))
    wd.mkdir(parents=True, exist_ok=True)
    try:
        pr = ff.probe(source)
        sw, sh, dur, has_a = pr["width"] or 1920, pr["height"] or 1080, pr["duration"], pr["has_audio"]
        c0, c1 = clamp_clip(clip, dur) if clip else (0.0, min(dur, max_main))
        D = max(0.5, c1 - c0)
        close = abs((sw / sh) - (W / H)) / (W / H) < 0.12
        src = f"[0:v]setpts=PTS-STARTPTS,fps={FPS},setsar=1"
        if close:
            fc = f"{src},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[base]"
        else:
            fc = (f"{src},split[s1][s2];[s1]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,crop={W // 8}:{H // 8},"
                  f"gblur=sigma=6,scale={W}:{H},eq=brightness=-0.2:saturation=0.85[bgv];"
                  f"[s2]scale={W}:{H}:force_original_aspect_ratio=decrease[fgv];[bgv][fgv]overlay=(W-w)/2:(H-h)/2[base]")
        args = ["-ss", f"{c0:.3f}", "-t", f"{D:.3f}", "-i", source]
        boxes = {}
        chain = "[base]"
        if logo:
            im, bx = brandpass.bug(W, H, brand)
            if bx[2] > bx[0]:
                im.save(wd / "bug.png")
                args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", str(wd / "bug.png")]
                chain += "[1:v]overlay=0:0:shortest=1"
                boxes["bug"] = bx
            else:
                chain += "null"
        else:
            chain += "null"
        fc += ";" + chain + ",format=yuv420p,setsar=1[v]"
        segs = []
        pre_s = 0.0
        if cover_png and Path(cover_png).is_file():
            pre_s = cover_frame_secs(cover_secs)
            cp = wd / "cover_frame.png"
            Image.open(cover_png).convert("RGB").resize((W, H), Image.LANCZOS).save(cp)
            segs.append(_card_segment(str(cp), pre_s, str(wd / "s00_cover.mp4"), crf, preset, fade=False))
        main = str(wd / "s1_main.mp4")
        ff.run(["-y", "-v", "error", *args, "-filter_complex", fc, "-map", "[v]", "-an", "-t", f"{D:.3f}", *_venc(crf, preset), main],
               timeout=1800)
        segs.append(main)
        card = 0.0
        if end:
            boxes["end_card"] = end_card(str(wd / "end.png"), W, H, brand)
            segs.append(_card_segment(str(wd / "end.png"), CARD_SECS, str(wd / "s3_end.mp4"), crf, preset))
            card = CARD_SECS
        lst = wd / "segments.txt"
        lst.write_text("".join(f"file '{Path(x).resolve()}'\n" for x in segs))
        video = str(wd / "video.mp4")
        ff.run(["-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", video], timeout=300)
        total = pre_s + D + card
        silent = (not has_a) or ff.is_silent(source)
        outp = Path(out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        _mux_audio(video, None if silent else source, c0, D, pre_s, total, None, str(outp))
        got = ff.probe(str(outp))
        return {"path": str(outp), "style": "none", "theme": None, "ratio": ratio, "width": got["width"], "height": got["height"],
                "duration": round(got["duration"], 2), "clip": [round(c0, 2), round(c1, 2), "given" if clip else "full video"],
                "boxes": boxes, "safe_area": safe_area(W, H), "caption_problems": [], "captions": False, "burned_subs": False,
                "captions_note": "", "source_captions": None, "music": "none", "source_silent": silent, "fit": "crop" if close else "blurred sides",
                "logo": bool(boxes.get("bug")), "cover_frame_secs": pre_s, "thumbnail_offset_ms": cover_thumb_ms(pre_s) if pre_s else None,
                "credit": "", "brand_pass": []}
    finally:
        if own:
            shutil.rmtree(wd, ignore_errors=True)


def _main_graph(source, images, cfg, W, H, panel, c0, D, ov_path, ass_path, burned_subs, brand, hook_path=None,
                stage_path=None, L=None, wd=None, reframe=None, extras=None):
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
    from . import styles as _st
    from . import theme_cards as _tc
    nxt = ov_i + 1 + (1 if hook_path else 0)
    st_i = sw_i = None
    if stage_path:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", str(stage_path)]
        st_i, nxt = nxt, nxt + 1
    if _st.has_fx(cfg, "sweep") and wd is not None:
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", _tc.sweep_png(str(Path(wd) / "sweep.png"), W, H)]
        sw_i, nxt = nxt, nxt + 1
    ex_i = []
    for (png, _t0, _t1) in extras or []:  # brand pass layers (hook title, logo bug, lower-third), each with its window
        args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{D:.3f}", "-i", str(png)]
        ex_i.append(nxt)
        nxt += 1
    look = _st.footage_chain(cfg, W)
    look = ("," + look) if look else ""
    weave = _st.has_fx(cfg, "weave")
    bg = brand["bg"].lstrip("#")
    px0, py0, px1, py1 = panel
    pw, ph = px1 - px0, py1 - py0
    parts = []
    if source:
        src = f"[0:v]setpts=PTS-STARTPTS,fps={FPS},setsar=1"
        if burned_subs and cfg["fill"] == "fill":  # crop the burned-in subtitle band so captions never double up
            src += f",crop=iw:trunc(ih*{_keep(burned_subs)}/2)*2:0:0"
        mode = (reframe or {}).get("mode")
        if cfg["fill"] == "fill" and mode == "track":  # subject-aware: the crop window follows the face (take.crop_plan)
            from ..take import crop_expr
            parts.append(f"{src},crop={int(reframe['crop_w'])}:ih:x='{crop_expr(reframe)}':y=0,scale={W}:{H},setsar=1{look}[base]")
        elif cfg["fill"] == "fill" and mode == "pad":  # no one on camera: the whole frame over a blurred copy of itself
            parts.append(f"{src},split[s1][s2];[s1]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
                         f"crop={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.2:saturation=0.85[bgv];"
                         f"[s2]scale={W}:{H}:force_original_aspect_ratio=decrease[fgv];[bgv][fgv]overlay=(W-w)/2:(H-h)/2{look}[base]")
        elif cfg["fill"] == "fill":
            parts.append(f"{src},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}{look}[base]")
        elif cfg["fill"] == "blurpanel":
            parts.append(f"{src},split[s1][s2];[s1]scale={W // 8}:{H // 8}:force_original_aspect_ratio=increase,"
                         f"crop={W // 8}:{H // 8},gblur=sigma=6,scale={W}:{H},eq=brightness=-0.16:saturation=0.85[bgv];"
                         f"[s2]scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}{look}[pv];"
                         f"[bgv][pv]overlay={px0}:{py0}[base]")
        else:
            ground = (f"[{st_i}:v]fps={FPS},format=yuv420p,setsar=1[bgc]" if st_i is not None
                      else f"color=c=0x{bg}:s={W}x{H}:r={FPS}:d={D:.3f}[bgc]")
            if weave:  # projector gate weave: scale a little over, crop with a drifting offset
                fit = (f"scale={pw + 12}:{ph + 12}:force_original_aspect_ratio=increase,crop={pw + 12}:{ph + 12},"
                       f"crop={pw}:{ph}:'6+3*sin(t*9.1)+2*sin(t*23)':'6+3*cos(t*7.3)'")
            else:
                fit = f"scale={pw}:{ph}:force_original_aspect_ratio=increase,crop={pw}:{ph}"
            parts.append(f"{ground};{src},{fit}{look}[pv];[bgc][pv]overlay={px0}:{py0}:shortest=1[base]")
        if _st.has_fx(cfg, "tiltshift"):  # miniature: blurred bands top and bottom, a softer band inside each
            a1, a2 = int(H * 0.26) // 2 * 2, int(H * 0.36) // 2 * 2
            parts.append(f"[base]split=5[t0][t1][t2][t3][t4];[t1]crop={W}:{a1}:0:0,gblur=sigma=10[u1];"
                         f"[t2]crop={W}:{a2 - a1}:0:{a1},gblur=sigma=3.5[u2];[t3]crop={W}:{a1}:0:{H - a1},gblur=sigma=10[d1];"
                         f"[t4]crop={W}:{a2 - a1}:0:{H - a2},gblur=sigma=3.5[d2];[t0][u1]overlay=0:0[q1];[q1][u2]overlay=0:{a1}[q2];"
                         f"[q2][d1]overlay=0:{H - a1}[q3];[q3][d2]overlay=0:{H - a2}[base]")
    else:
        segs = []
        for i in range(len(images)):
            fr = max(1, int(D / len(images) * FPS))
            parts.append(f"[{i}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
                         f"scale={int(W * 1.12) // 2 * 2}:-2,zoompan=z='min(1+0.0012*on,1.1)':x='iw/2-(iw/zoom/2)':"
                         f"y='ih/2-(ih/zoom/2)':d={fr}:s={W}x{H}:fps={FPS}{look}[k{i}]")
            segs.append(f"[k{i}]")
        parts.append(f"{''.join(segs)}concat=n={len(images)}:v=1:a=0[base]")
    chain = "[base]"
    if sw_i is not None:  # glass studio: a strip of light travels across the frame every 3.2 s
        chain += f"[{sw_i}:v]overlay=x='-w+(W+w)*mod(t,3.2)/3.2':y=0:shortest=1[sw];[sw]"
    chain += f"[{ov_i}:v]overlay=0:0:shortest=1"
    if hook_path:
        chain += f"[b1];[b1][{ov_i + 1}:v]overlay=0:0:eof_action=pass:enable='lt(t,{HYPE_HOOK_SECS})'"
    for k, (png, t0, t1) in zip(ex_i, extras or []):
        fade = []
        if t0 > 0:
            fade.append(f"fade=t=in:st={t0:.2f}:d=0.25:alpha=1")
        if t1 < D - 0.05:
            fade.append(f"fade=t=out:st={max(t0, t1 - 0.3):.2f}:d=0.3:alpha=1")
        parts.append(f"[{k}:v]format=rgba" + ("," + ",".join(fade) if fade else "") + f"[bp{k}]")
        chain += f"[bq{k}];[bq{k}][bp{k}]overlay=0:0:eof_action=pass:enable='between(t,{t0:.2f},{t1:.2f})'"
    chain += _theme_text(cfg, L or {}, D, brand.get("highlight"))
    tr = _st.transition_chain(cfg) if source else ""
    if tr:
        chain += "," + tr
    if ass_path:
        fontsdir = str(Path(__file__).resolve().parent.parent / "fonts")
        chain += f",ass='{_esc(str(ass_path))}':fontsdir='{_esc(fontsdir)}'"
    parts.append(chain + ",format=yuv420p,setsar=1[v]")
    return args, ";".join(parts)


def _theme_text(cfg: dict, L: dict, D: float, highlight: str | None = None) -> str:
    """Live text the theme draws with FFmpeg: the countdown's T-minus clock + progress bar (in the highlight colour: the
    brand kit's Accent when the brand accent stays, else the theme's), the investigation timecode."""
    out = ""
    mono = _esc(str(Path(__file__).resolve().parent.parent / "fonts" / "IBMPlexMono-SemiBold.ttf"))
    if "clock_text" in L:
        x, y, sz = L["clock_text"]
        col = (cfg.get("palette") or {}).get("text", "#FFFFFF").lstrip("#")
        txt = (f"%{{eif\\:trunc(({D:.2f}-t)/60)\\:d\\:2}}\\:%{{eif\\:mod(trunc({D:.2f}-t)\\,60)\\:d\\:2}}"
               f".%{{eif\\:mod(trunc(({D:.2f}-t)*10)\\,10)\\:d\\:1}}")
        out += f",drawtext=fontfile='{mono}':text='{txt}':x={x}:y={y}:fontsize={sz}:fontcolor=0x{col}"
    if "progress" in L:
        x, y, w, h = L["progress"]
        acc = (highlight or (cfg.get("palette") or {}).get("accent", "#FF5B1F")).lstrip("#")
        n = 24
        for i in range(n):
            out += (f",drawbox=x={x + w * i // n}:y={y}:w={w // n + 1}:h={h}:color=0x{acc}@1:t=fill"
                    f":enable='gte(t,{D * (i + 1) / n:.3f})'")
    if "timecode" in L:
        x, y, sz = L["timecode"]
        out += (f",drawtext=fontfile='{mono}':text='T+ %{{pts\\:hms}}':x={x}:y={y}:fontsize={sz}:fontcolor=0xECEAE4"
                ":box=1:boxcolor=0x0B0C0E@0.75:boxborderw=8")
    return out


def clamp_clip(clip, dur: float) -> tuple[float, float]:
    """A requested cut, kept inside the source: asking for 30 s of a 9 s clip gives the 9 s it has. Without this the
    picture ended with the source while the audio ran on to the asked length (owner bug 2 Nov 2026: a screencast Reel
    was 14.4 s of video under 38 s of audio, so it looked frozen)."""
    c0, c1 = float(clip[0] or 0), float(clip[1] or 0)
    if dur and dur > 0:
        c0 = min(max(0.0, c0), max(0.0, dur - 0.5))
        c1 = min(c1, dur) if c1 > 0 else dur
    if c1 <= c0:
        c1 = c0 + 0.5
    return c0, c1


def _mux_audio(video: str, speech_src: str | None, c0: float, D: float, cover_s: float, total: float, wav: str | None,
               out: str) -> None:
    """Final pass: copy the joined video, build the audio (real sound first, music ducked under it), -14 LUFS.
    The audio never outlasts the picture: ``total`` is cut to the joined video's real length."""
    try:
        vlen = float(ff.probe(video).get("duration") or 0)
    except Exception:  # noqa: BLE001  a probe failure keeps the planned length
        vlen = 0.0
    if vlen > 0.3:
        total = min(total, vlen)
        D = min(D, max(0.3, total - cover_s))
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
    # ff.AAC_KBPS, under Instagram's 128 kbps Reels spec (160k measured 142-153 kbps and every Instagram chip warned, take #91)
    ff.run([*args, "-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac",
            "-b:a", f"{ff.AAC_KBPS}k", "-ar", "44100", "-t", f"{total:.3f}", "-movflags", "+faststart", out], timeout=900)
