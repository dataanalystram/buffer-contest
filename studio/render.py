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
import subprocess
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
_SUBS = str.maketrans({"\u2192": "->", "\u2190": "<-", "\u2022": "-", "\u2026": "...", "\u00a0": " "})
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
        f = font(FONTS.get(p.get("font"), "Anton.ttf"), max(18, int(target_w * 0.28)))
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
              ratio: str = "4:5", label: str = "", credit: str = "", n: tuple[int, int] | None = None) -> tuple[bytes, dict]:
    """Cover slide: photo with a brand-colored fade, the headline, the source credit, the logo bug."""
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = (hex_to_rgb(p["colors"][k]) for k in ("primary", "accent", "text"))
    if image:
        try:
            canvas = cover(Image.open(io.BytesIO(image)), W, H)
        except Exception:
            canvas = Image.new("RGB", (W, H), prim)
            image = None
    else:
        canvas = Image.new("RGB", (W, H), prim)
    if image:
        _gradient(canvas, prim, 0.30, max(0.35, float(p.get("overlay", 0.55)) + 0.4))
    d = ImageDraw.Draw(canvas)
    m = int(p.get("safe_margin", 64))
    head = shape_headline(headline, p)
    hf_name = FONTS.get(p.get("font"), "Anton.ttf")
    size = int(W * (0.1 if p.get("font") == "anton" else 0.078))
    while True:
        f = font(hf_name, size)
        lines = wrap(d, head, f, W - 2 * m)
        if len(lines) <= 5 or size < 40:
            break
        size = int(size * 0.92)
    lh = int(size * (1.04 if p.get("font") == "anton" else 1.14))
    bottom = H - m - (int(W * 0.075) if (credit and p.get("source_credit", True)) else 0) - int(H * 0.04)
    y = bottom - lh * len(lines)
    if label:
        lf = font(BODY_FONTS.get(p.get("font"), "Inter-SemiBold.ttf"), int(W * 0.03))
        lt = label.upper()
        tw = _tlen(d, lt, font=lf)
        d.rounded_rectangle((m, y - int(W * 0.08), m + tw + 36, y - int(W * 0.08) + lf.size + 22), 8, fill=acc)
        _draw(d, (m + 18, y - int(W * 0.08) + 9), lt, font=lf, fill=prim)
    for i, line in enumerate(lines):
        col = acc if i == 0 and len(lines) > 1 else txt
        _draw(d, (m, y + i * lh), line, font=f, fill=col)
    meta_credit = ""
    if credit and p.get("source_credit", True):
        cf = font(BODY_FONTS.get(p.get("font"), "Inter-Regular.ttf"), int(W * 0.026))
        meta_credit = f"Source: {credit}"
        _draw(d, (m, H - m - cf.size), meta_credit, font=cf, fill=txt + (200,))
    handle = (kit.get("handle") or "").lstrip("@")
    if handle:
        hf = font(BODY_FONTS.get(p.get("font"), "Inter-SemiBold.ttf"), int(W * 0.026))
        t = "@" + handle
        _draw(d, (W - m - _tlen(d, t, font=hf), H - m - hf.size), t, font=hf, fill=acc)
    if n:
        nf = font(BODY_FONTS.get(p.get("font"), "Inter-SemiBold.ttf"), int(W * 0.024))
        t = f"{n[0]}/{n[1]}"
        x = m if p.get("logo_position") in ("tr", "br") else W - m - _tlen(d, t, font=nf)
        _draw(d, (x, m), t, font=nf, fill=txt)
    has_logo = place_logo(canvas, load_logo(logo), p, kit)
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), "has_logo": has_logo,
                           "headline": head, "credit": meta_credit}


def fact_slide(title: str, bullets: list[str], kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5",
               n: tuple[int, int] | None = None) -> tuple[bytes, dict]:
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = (hex_to_rgb(p["colors"][k]) for k in ("primary", "accent", "text"))
    canvas = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(canvas)
    m = int(p.get("safe_margin", 64))
    tf = font(FONTS.get(p.get("font"), "Anton.ttf"), int(W * 0.07))
    bf = font(BODY_FONTS.get(p.get("font"), "Inter-Regular.ttf"), int(W * 0.04))
    y = int(H * 0.2)
    t = shape_headline(title, {**p, "headline_max_words": 8})
    for line in wrap(d, t, tf, W - 2 * m):
        _draw(d, (m, y), line, font=tf, fill=acc)
        y += int(tf.size * 1.1)
    y += int(H * 0.04)
    for b in bullets[:4]:
        d.ellipse((m, y + bf.size * 0.35, m + bf.size * 0.4, y + bf.size * 0.75), fill=acc)
        for line in wrap(d, b, bf, W - 2 * m - 40):
            _draw(d, (m + 40, y), line, font=bf, fill=txt)
            y += int(bf.size * 1.35)
        y += int(bf.size * 0.6)
    if n:
        nf = font(BODY_FONTS.get(p.get("font"), "Inter-SemiBold.ttf"), int(W * 0.024))
        tt = f"{n[0]}/{n[1]}"
        x = m if p.get("logo_position") in ("tr", "br") else W - m - _tlen(d, tt, font=nf)
        _draw(d, (x, m), tt, font=nf, fill=txt)
    has_logo = place_logo(canvas, load_logo(logo), p, kit)
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), "has_logo": has_logo,
                           "headline": t, "credit": "x"}


def end_card(kit: dict, p: dict, *, logo: bytes | None = None, ratio: str = "4:5") -> tuple[bytes, dict]:
    W, H = SIZES.get(ratio, SIZES["4:5"])
    prim, acc, txt = (hex_to_rgb(p["colors"][k]) for k in ("primary", "accent", "text"))
    canvas = Image.new("RGB", (W, H), prim)
    d = ImageDraw.Draw(canvas)
    ec = kit.get("end_card") or {}
    big = font(FONTS.get(p.get("font"), "Anton.ttf"), int(W * 0.16))
    cta = (ec.get("cta") or "Follow").strip()[:20]
    handle = ("@" + kit["handle"].lstrip("@")) if kit.get("handle") else (kit.get("name") or "")
    hf = font(FONTS.get(p.get("font"), "Anton.ttf"), int(W * 0.09))
    while _tlen(d, handle, font=hf) > W * 0.86 and hf.size > 30:
        hf = font(FONTS.get(p.get("font"), "Anton.ttf"), int(hf.size * 0.92))
    lf = font(BODY_FONTS.get(p.get("font"), "Inter-Regular.ttf"), int(W * 0.036))
    lg = load_logo(logo)
    y = int(H * 0.3)
    if lg is not None:
        l2 = lg.copy()
        l2.thumbnail((int(W * 0.22), int(W * 0.22)), Image.LANCZOS)
        canvas.paste(l2, ((W - l2.width) // 2, y - l2.height - 40), l2)
    t = cta.upper() if p.get("headline_case") == "upper" else cta
    _draw(d, ((W - _tlen(d, t, font=big)) / 2, y), t, font=big, fill=txt)
    y += int(big.size * 1.1)
    _draw(d, ((W - _tlen(d, handle, font=hf)) / 2, y), handle, font=hf, fill=acc)
    y += int(hf.size * 1.4)
    line = (ec.get("line") or "").strip()[:60]
    if line:
        _draw(d, ((W - _tlen(d, line, font=lf)) / 2, y), line, font=lf, fill=txt)
    return _jpeg(canvas), {"type": "photo", "width": W, "height": H, "params": _pub(p), "has_logo": lg is not None,
                           "headline": t, "credit": "x", "end_card": True}


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
        f = font(FONTS.get(p.get("font"), "Anton.ttf"), int(W * 0.075))
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


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + r.stderr[-1500:])


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next((s for s in j["streams"] if s["codec_type"] == "video"), {})
    return {"duration": float(j["format"].get("duration", 0)), "width": v.get("width"), "height": v.get("height"),
            "audio": any(s["codec_type"] == "audio" for s in j["streams"])}


def slideshow_reel(slides: list[bytes], kit: dict, p: dict, *, logo: bytes | None = None, secs: float = 3.2,
                   ratio: str = "9:16") -> tuple[bytes, dict]:
    """A Reel from rendered slides: slow push-in on each slide, quick crossfades, the end card last (if the ledger
    wants one). Silent by design: the person adds licensed music in the network's app."""
    W, H = SIZES[ratio]
    tmp = Path(tempfile.mkdtemp(prefix="hs_reel_"))
    try:
        frames = list(slides)
        has_end = bool(p.get("end_card", True))
        if has_end:
            frames.append(end_card(kit, p, logo=logo, ratio=ratio)[0])
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
                  "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", str(out)])
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
        silent = ["-f", "lavfi", "-t", "60", "-i", "anullsrc=r=44100:cl=stereo"]
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
                max_secs: float = 58.0) -> tuple[bytes, dict]:
    """A person's own clip: crop to the frame, logo bug, a hook headline for the first 3 s, the end card at the end."""
    W, H = SIZES[ratio]
    tmp = Path(tempfile.mkdtemp(prefix="hs_vid_"))
    try:
        src = tmp / "in.mp4"
        src.write_bytes(video)
        info = probe(str(src))
        dur = min(info["duration"] or max_secs, max_secs - (2.6 if p.get("end_card", True) else 0))
        # overlay: transparent frame with logo bug (+ handle), and a separate hook overlay
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        place_logo(ov, load_logo(logo), p, kit)
        ov.save(tmp / "logo.png")
        head = ""
        if hook:
            hk = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            d = ImageDraw.Draw(hk)
            m = int(p.get("safe_margin", 64))
            head = shape_headline(hook, {**p, "headline_max_words": min(10, int(p.get("headline_max_words", 14)))})
            f = font(FONTS.get(p.get("font"), "Anton.ttf"), int(W * 0.085))
            lines = wrap(d, head, f, W - 2 * m - 40)[:3]
            lh = int(f.size * 1.12)
            y = int(H * 0.16)
            box_h = lh * len(lines) + 40
            d.rounded_rectangle((m, y - 20, W - m, y - 20 + box_h), 24, fill=hex_to_rgb(p["colors"]["primary"]) + (220,))
            for i, line in enumerate(lines):
                _draw(d, (m + 24, y + i * lh), line, font=f, fill=hex_to_rgb(p["colors"]["accent" if i == 0 else "text"]))
            hk.save(tmp / "hook.png")
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps=30[b];"
              f"[b][1:v]overlay=0:0[l]")
        inputs = ["-t", f"{dur:.2f}", "-i", str(src), "-i", str(tmp / "logo.png")]
        last = "l"
        if hook:
            inputs += ["-i", str(tmp / "hook.png")]
            vf += ";[l][2:v]overlay=0:0:enable='lt(t,3)'[h]"
            last = "h"
        vf += f";[{last}]format=yuv420p[v]"
        body = tmp / "body.mp4"
        amap = ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"] if info["audio"] else []
        _run(["ffmpeg", "-y", *inputs, "-filter_complex", vf, "-map", "[v]", *amap, "-c:v", "libx264", "-preset", "veryfast",
              "-crf", "22", "-movflags", "+faststart", str(body)])
        final = body
        has_end = bool(p.get("end_card", True))
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
                  "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(final)])
        out = probe(str(final))
        return final.read_bytes(), {"type": "video", "width": W, "height": H, "duration": round(out["duration"], 2),
                                    "params": _pub(p), "has_logo": True, "has_end_card": has_end, "headline": head,
                                    "credit": "own video"}
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
