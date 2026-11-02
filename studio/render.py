"""Renderer: brand kit + ledger params -> finished photos, carousels and videos.

Photos and carousels render in-process with Pillow (fast, a few hundred ms). Videos render with FFmpeg, either here
(self-host, tests) or on the free GitHub Actions worker (`python -m studio.render_worker`) for the hosted app, because
the free web instance is too small for video. Every render returns (bytes, meta); meta is what brand_check reads.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import tempfile
import unicodedata
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from .brand import BODY_FONTS, FONTS, hex_to_rgb

FONT_DIR = Path(__file__).parent / "fonts"
SIZES = {"4:5": (1080, 1350), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / name), size)


def cover(img: Image.Image, w: int, h: int, cx: float = 0.5, cy: float = 0.4) -> Image.Image:
    img = ImageOps.exif_transpose(img).convert("RGB")
    s = max(w / img.width, h / img.height)
    nw, nh = max(w, int(img.width * s + 0.5)), max(h, int(img.height * s + 0.5))
    img = img.resize((nw, nh), Image.LANCZOS)
    x = int((nw - w) * cx)
    y = int((nh - h) * cy)
    return img.crop((x, y, x + w, y + h))


# ---- text that the bundled fonts can actually draw (no tofu boxes for emoji or symbols they lack)
# Unicode hyphens/dashes/quotes a model likes to emit (U+2011 non-breaking hyphen made "noise-free" render as
# "noisefree" because the fonts lack it) are mapped to the plain glyphs every bundled font has.
_SUBS = str.maketrans({"\u2192": "->", "\u2190": "<-", "\u2022": "-", "\u2026": "...", "\u00a0": " ", "\u2010": "-",
                       "\u2011": "-", "\u2012": "-", "\u2013": "\u2013", "\u2212": "-", "\u202f": " ", "\u2009": " "})
_CMAPS: dict[str, set[int] | None] = {}


def _cmap(f) -> set[int] | None:
    path = getattr(f, "path", "")
    if path not in _CMAPS:
        try:
            from fontTools.ttLib import TTFont
            _CMAPS[path] = set(TTFont(path, lazy=True).getBestCmap() or {})
        except Exception:  # fontTools missing or unreadable font: fall back to Unicode categories
            _CMAPS[path] = None
    return _CMAPS[path]


def drawable(text: str, f=None) -> str:
    """Keep only characters the font has a glyph for; emoji, pictographs and stray symbols are dropped, never boxed."""
    s = unicodedata.normalize("NFKC", str(text or "")).translate(_SUBS)
    cm = _cmap(f) if f is not None else None
    keep = []
    for ch in s:
        if ch in " \n":
            keep.append(ch)
        elif cm is not None:
            if ord(ch) in cm:
                keep.append(ch)
        elif unicodedata.category(ch) not in ("So", "Sk", "Cs", "Co", "Cn", "Cf", "Mn") and ord(ch) < 0x2E80:
            keep.append(ch)
    return re.sub(r"[ \t]{2,}", " ", "".join(keep))


def _draw(d, xy, text, **kw):
    d.text(xy, drawable(text, kw.get("font")), **kw)


def _tlen(d, text, **kw):
    return d.textlength(drawable(text, kw.get("font")), **kw)


def _tbox(d, xy, text, **kw):
    return d.textbbox(xy, drawable(text, kw.get("font")), **kw)


def wrap(d: ImageDraw.ImageDraw, text: str, f, maxw: int) -> list[str]:
    lines, cur = [], ""
    for w in drawable(text, f).split():
        t = (cur + " " + w).strip()
        if _tlen(d, t, font=f) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def shape_headline(text: str, p: dict) -> str:
    words = re.sub(r"\s+", " ", text or "").strip().split(" ")
    n = int(p.get("headline_max_words", 14))
    if len(words) > n:
        words = words[:n]
        words[-1] = words[-1].rstrip(",;:") + "…"
    h = " ".join(words)
    if p.get("headline_case") == "upper":
        return h.upper()
    if h.isupper() and len(h) > 6:  # an all-caps source headline still follows a sentence-case rule
        h = h.capitalize()
    return h[:1].upper() + h[1:] if h else h


def load_logo(logo_bytes: bytes | None) -> Image.Image | None:
    if not logo_bytes:
        return None
    try:
        im = Image.open(io.BytesIO(logo_bytes)).convert("RGBA")
        bbox = im.getchannel("A").getbbox()
        return im.crop(bbox) if bbox else im
    except Exception:
        return None


def place_logo(canvas: Image.Image, logo: Image.Image | None, p: dict, kit: dict) -> bool:
    """Logo bug at the ledger's position and scale. Without a logo file, a wordmark pill in the accent color."""
    W, H = canvas.size
    m = int(p.get("safe_margin", 64))
    target_w = int(W * float(p.get("logo_scale", 0.11)))
    if logo is not None:
        lg = logo.copy()
        lg.thumbnail((target_w, target_w), Image.LANCZOS)
    else:
        f = font(FONTS.get(p.get("font"), "Inter-Bold.ttf"), max(18, int(target_w * 0.28)))
        name = drawable((kit.get("name") or "").strip(), f).strip()[:18]
        if not name:  # no logo and no brand name yet: no placeholder wordmark
            p["_logo_box"] = None
            return False
        d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        tw = int(_tlen(d0, name, font=f))
        ph = int(f.size * 1.7)
        lg = Image.new("RGBA", (tw + ph, ph), (0, 0, 0, 0))
        dd = ImageDraw.Draw(lg)
        dd.rounded_rectangle((0, 0, lg.width - 1, ph - 1), ph // 2, fill=hex_to_rgb(p["colors"]["accent"]) + (255,))
        bb = _tbox(dd, (0, 0), name, font=f)
        _draw(dd, ((lg.width - tw) / 2, (ph - (bb[3] - bb[1])) / 2 - bb[1]), name, font=f, fill=hex_to_rgb(p["colors"]["primary"]))
    pos = p.get("logo_position", "tr")
    x = m if pos in ("tl", "bl") else W - m - lg.width
    y = m if pos in ("tl", "tr") else H - m - lg.height
    canvas.paste(lg, (x, y), lg)
    p["_logo_box"] = (x, y, lg.width, lg.height)
    return True


def _gradient(canvas: Image.Image, color, start: float, strength: float) -> None:
    W, H = canvas.size
    g = Image.new("L", (1, H))
    for yy in range(H):
        t = max(0.0, (yy / H - start) / (1 - start))
        g.putpixel((0, yy), int(255 * min(1.0, strength * (0.25 + 0.75 * t ** 0.9) if yy / H > start else strength * 0.25 * (yy / H / max(start, .01)) ** 2)))
    g = g.resize((W, H))
    over = Image.new("RGB", (W, H), color)
    canvas.paste(over, (0, 0), g)


def news_card(headline: str, kit: dict, p: dict, *, image: bytes | None = None, logo: bytes | None = None,
              ratio: str = "4:5", label: str = "", credit: str = "", n: tuple[int, int] | None = None,
              inset: bytes | None = None, hi=()) -> tuple[bytes, dict]:
    """Cover slide in the editorial look (hbstyle.cover): hero band, divider, auto-fit headline that never clips."""
    from . import hbstyle
    canvas, m = hbstyle.cover(headline, kit, p, image=image, inset=inset, logo=logo, ratio=ratio, label=label, credit=credit, n=n, hi=hi)
    W, H = canvas.size
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), **m}


def fact_slide(title: str, bullets: list[str], kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5",
               n: tuple[int, int] | None = None, image: bytes | None = None, headline: str = "") -> tuple[bytes, dict]:
    from . import hbstyle
    canvas, m = hbstyle.facts(title, bullets, kit, p, image=image, logo=logo, ratio=ratio, n=n, headline=headline)
    W, H = canvas.size
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), **m}


def post_slide(post: dict, kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5", headline: str = "",
               n: tuple[int, int] | None = None) -> tuple[bytes, dict]:
    """A social post (X via oEmbed) as a brand-styled quote card."""
    from . import hbstyle
    canvas, m = hbstyle.post_card(post, kit, p, logo=logo, ratio=ratio, headline=headline, n=n)
    W, H = canvas.size
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), **m}


def end_card(kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5",
             n: tuple[int, int] | None = None) -> tuple[bytes, dict]:
    from . import hbstyle
    canvas, m = hbstyle.end_card(kit, p, logo=logo, ratio=ratio, n=n)
    W, H = canvas.size
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), **m}


def brand_photo(photo: bytes, kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5", caption: str = "",
                focus: tuple[float, float] = (0.5, 0.4)) -> tuple[bytes, dict]:
    """A person's own photo, cropped to the network frame, graded, with the logo bug and an optional caption band."""
    W, H = SIZES.get(ratio, SIZES["4:5"])
    canvas = cover(Image.open(io.BytesIO(photo)), W, H, *focus)
    canvas = ImageOps.autocontrast(canvas, cutoff=1)
    head = ""
    if caption:
        prim, acc, txt = (hex_to_rgb(p["colors"][k]) for k in ("primary", "accent", "text"))
        _gradient(canvas, prim, 0.55, float(p.get("overlay", 0.55)) + 0.3)
        d = ImageDraw.Draw(canvas)
        m = int(p.get("safe_margin", 64))
        head = shape_headline(caption, p)
        f = font(FONTS.get(p.get("font"), "Inter-Bold.ttf"), int(W * 0.075))
        lines = wrap(d, head, f, W - 2 * m)[:4]
        y = H - m - int(f.size * 1.08) * len(lines) - int(H * 0.03)
        for i, line in enumerate(lines):
            _draw(d, (m, y + i * int(f.size * 1.08)), line, font=f, fill=acc if i == 0 and len(lines) > 1 else txt)
    has_logo = place_logo(canvas, load_logo(logo), p, kit)
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), "has_logo": has_logo,
                           "headline": head, "credit": "own photo"}


def _jpeg(im: Image.Image) -> bytes:
    b = io.BytesIO()
    im.convert("RGB").save(b, "JPEG", quality=90, optimize=True, progressive=True)
    return b.getvalue()


def _pub(p: dict) -> dict:
    return {k: v for k, v in p.items() if not k.startswith("_")}


# ------------------------------------------------------------------ video (FFmpeg)
def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def _run(cmd: list[str], timeout: float = 900) -> None:
    from . import mediasafe  # protocol + demuxer whitelists before every input, and a time limit
    r = mediasafe.run(cmd, timeout=timeout, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])


def probe(path: str) -> dict:
    from . import mediasafe
    out = mediasafe.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                         "-of", "json", path], timeout=120, text=True, check=True).stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    return {"duration": float(j["format"].get("duration", 0)), "width": v.get("width"), "height": v.get("height"),
            "audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def slideshow_reel(slides: list[bytes], kit: dict, p: dict, *, logo: bytes | None = None, secs: float = 3.2,
                   ratio: str = "9:16", total: float | None = None) -> tuple[bytes, dict]:
    """A Reel from rendered slides: slow push-in on each slide, quick crossfades, the end card last (if the ledger
    wants one). Silent by design: the person adds licensed music in the network's app."""
    W, H = SIZES[ratio]
    tmp = Path(tempfile.mkdtemp(prefix="hs_reel_"))
    try:
        frames = list(slides)
        has_end = bool(p.get("end_card", True))
        if has_end:
            frames.append(end_card(kit, p, logo=logo, ratio=ratio)[0])
        if total and len(slides):  # a target length: the story slides share it, the end card keeps its 2.6 s
            secs = max(1.5, min(20.0, (float(total) - (2.6 if has_end else 0) + 0.35 * (len(frames) - 1)) / len(slides)))
        parts = []
        for i, b in enumerate(frames):
            src = tmp / f"s{i}.jpg"
            Image.open(io.BytesIO(b)).convert("RGB").resize((W, H), Image.LANCZOS).save(src, quality=92)
            out = tmp / f"p{i}.mp4"
            dur = 2.6 if (has_end and i == len(frames) - 1) else secs
            fr = int(dur * 30)
            # push-in from 100% to 106%: scale up once, then crop a shrinking window (cheap, no zoompan jitter)
            vf = (f"scale={int(W * 1.06)}:{int(H * 1.06)},"
                  f"crop=w='{W * 1.06}-{W * 0.06}*n/{fr}':h='{H * 1.06}-{H * 0.06}*n/{fr}':x='(in_w-out_w)/2':y='(in_h-out_h)/2',"
                  f"scale={W}:{H},setsar=1,fps=30,format=yuv420p")
            _run(["ffmpeg", "-y", "-loop", "1", "-t", f"{dur}", "-i", str(src), "-vf", vf, "-frames:v", str(fr),
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-threads", "2", "-an", str(out)])
            parts.append((out, dur))
        # crossfade chain
        fade = 0.35
        inputs, fc, prev, off = [], [], "0:v", 0.0
        for i, (path, dur) in enumerate(parts):
            inputs += ["-i", str(path)]
        for i in range(1, len(parts)):
            off += parts[i - 1][1] - fade
            lab = f"x{i}"
            fc.append(f"[{prev}][{i}:v]xfade=transition=fade:duration={fade}:offset={off:.3f}[{lab}]")
            prev = lab
        final = tmp / "reel.mp4"
        silent = ["-f", "lavfi", "-t", "180", "-i", "anullsrc=r=44100:cl=stereo"]
        cmd = ["ffmpeg", "-y", *inputs, *silent]
        ai = len(parts)
        if fc:
            cmd += ["-filter_complex", ";".join(fc), "-map", f"[{prev}]"]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-map", f"{ai}:a", "-shortest", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(final)]
        _run(cmd)
        info = probe(str(final))
        return final.read_bytes(), {"type": "video", "width": W, "height": H, "duration": round(info["duration"], 2),
                                    "params": _pub(p), "has_logo": True, "has_end_card": has_end, "headline": "", "credit": "x"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def brand_video(video: bytes, kit: dict, p: dict, *, logo: bytes | None = None, hook: str = "", ratio: str = "9:16",
                max_secs: float = 58.0, captions: list[dict] | None = None) -> tuple[bytes, dict]:
    """A clip (the person's own, or one read from a post) in the editorial Reel layout (hb.py build_reel): the hook
    headline and logo stay on screen from the first frame to the last, the whole clip plays in a band under it (never
    cropped to death), and the brand end card is appended last. Without a hook: full-frame crop + logo bug."""
    from . import hbstyle
    from .headline import render_hook
    hook = render_hook(hook)  # last line of defence: a URL or a request is never burned into a video
    W, H = SIZES[ratio]
    tmp = Path(tempfile.mkdtemp(prefix="hs_vid_"))
    try:
        src = tmp / "in.mp4"
        src.write_bytes(video)
        info = probe(str(src))
        has_end = bool(p.get("end_card", True))
        dur = min(info["duration"] or max_secs, max_secs - (2.6 if has_end else 0))
        head, boxes = "", []
        if hook:
            sw, sh = info.get("width") or 16, info.get("height") or 9
            vw = W
            vh = int(round(W * sh / sw / 2)) * 2
            if vh > 1000:  # tall source: fit a box and keep the headline visible
                vh = 1000
                vw = int(round(1000 * sw / sh / 2)) * 2
            ov, (vx, vy), boxes = hbstyle.reel_overlay(hook, kit, p, band=(vw, vh), logo=logo, ratio=ratio)
            ov.save(tmp / "ov.png")
            head = shape_headline(hook, p)
            vf = (f"[0:v]scale={vw}:{vh}:force_original_aspect_ratio=increase,crop={vw}:{vh},setsar=1,fps=30[sv];"
                  f"color=c=black:s={W}x{H}:r=30[bg];[bg][sv]overlay={vx}:{vy}:shortest=1[b];[b][1:v]overlay=0:0,format=yuv420p[v]")
        else:
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            place_logo(ov, load_logo(logo), p, kit)
            ov.save(tmp / "ov.png")
            vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30[b];"
                  f"[b][1:v]overlay=0:0,format=yuv420p[v]")
        body = tmp / "body.mp4"
        amap = ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"] if info["audio"] else []
        _run(["ffmpeg", "-y", "-t", f"{dur:.2f}", "-i", str(src), "-loop", "1", "-t", f"{dur:.2f}", "-i", str(tmp / "ov.png"),
              "-filter_complex", vf, "-map", "[v]", *amap, "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-threads", "2",
              "-movflags", "+faststart", str(body)])
        final = body
        if has_end:
            ec = tmp / "end.jpg"
            ec.write_bytes(end_card(kit, p, logo=logo, ratio=ratio)[0])
            endv = tmp / "end.mp4"
            _run(["ffmpeg", "-y", "-loop", "1", "-t", "2.6", "-i", str(ec), "-f", "lavfi", "-t", "2.6", "-i", "anullsrc=r=44100:cl=stereo",
                  "-vf", f"scale={W}:{H},setsar=1,fps=30,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                  "-c:a", "aac", "-b:a", "128k", "-shortest", str(endv)])
            if not info["audio"]:
                b2 = tmp / "body_a.mp4"
                _run(["ffmpeg", "-y", "-i", str(body), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-map", "0:v", "-map", "1:a",
                      "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-shortest", str(b2)])
                body = b2
            final = tmp / "final.mp4"
            _run(["ffmpeg", "-y", "-i", str(body), "-i", str(endv), "-filter_complex",
                  "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][a]", "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                  "-preset", "veryfast", "-crf", "22", "-threads", "2", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                  str(final)])
        out = probe(str(final))
        return final.read_bytes(), {"type": "video", "width": W, "height": H, "duration": round(out["duration"], 2),
                                    "params": _pub(p), "has_logo": True, "has_end_card": has_end, "headline": head,
                                    "credit": "own video", "text_boxes": boxes}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def thumbnail(data: bytes, w: int = 360) -> bytes:
    im = Image.open(io.BytesIO(data)).convert("RGB")
    im.thumbnail((w, w * 2))
    return _jpeg(im)


def video_poster(video: bytes) -> bytes | None:
    if not have_ffmpeg():
        return None
    tmp = Path(tempfile.mkdtemp(prefix="hs_pst_"))
    try:
        (tmp / "v.mp4").write_bytes(video)
        _run(["ffmpeg", "-y", "-ss", "0.8", "-i", str(tmp / "v.mp4"), "-frames:v", "1", "-update", "1", "-vf", "scale=360:-2",
              str(tmp / "p.jpg")])
        return (tmp / "p.jpg").read_bytes()
    except Exception:
        return None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


os.environ.setdefault("PYTHONIOENCODING", "utf-8")
