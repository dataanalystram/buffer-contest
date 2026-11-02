"""Branded covers / thumbnails (owner, 2 Nov 2026: "the cover is too generic").

``make_covers`` builds three variants from the video's best frames (or the product's hero image) and picks the best:

  1. Frames: ~14 samples across the clip (never the first/last 4 %), each scored for sharpness (edge energy), exposure
     (a black, white or flat transition frame is rejected), contrast, colour and a face bonus. The top frames that differ
     from each other are the candidates.
  2. Subject: faces (OpenCV's Haar cascade when OpenCV is installed; the video worker has it) else the frame's
     edge-density centroid (a saliency stand-in). The crop keeps the subject in frame at each size.
  3. Headline: a bold 2-5 word hook (``hook_words``) in the brand's headline font, its key word (a number, a money figure,
     the product name or the longest word) in the brand accent, on a subtle gradient + vignette for legibility, logo on.
  4. Safe margins: on 9:16 the text sits inside the centre 4:5 band (what the profile grid shows) with side margins; on
     4:5 inside the centre 3:4 crop; on 16:9 (YouTube 1280x720) clear of the bottom-right timestamp corner. Text never
     covers a face: a layout that would is moved to the other side or loses points.
  5. Score: WCAG contrast of the text against what is behind it (after the gradient), the frame's own score, the face
     penalty and the safe-area check. The best variant is first; the others stay available so the person can swap.

Sizes: 9:16 1080x1920 (the Reel cover), 4:5 1080x1350 (feed / grid), 16:9 1280x720 (YouTube thumbnail).
Pure Pillow (+ OpenCV when present); frames come from FFmpeg. Nothing here touches the network.
"""
from __future__ import annotations

import io
import math
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps, ImageStat

from . import textfit

SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "16:9": (1280, 720)}
LAYOUTS = ("bottom", "top", "inset")
STOP = {"a", "an", "the", "of", "to", "for", "and", "or", "in", "on", "with", "is", "are", "was", "its", "it", "this", "that",
        "just", "now", "your", "you", "how", "why", "what", "at", "by", "from", "as", "be", "has", "have", "into", "about"}
HEAD_FONTS = {"anton": "Anton.ttf", "poppins": "Poppins-Bold.ttf", "inter": "Inter-Bold.ttf", "figtree": "Figtree-ExtraBold.ttf"}


def _rgb(h: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


def _lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb[:3]
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ------------------------------------------------------------------------------------------------ frames
def sample_frames(video: str, n: int = 14, start: float = 0.0, end: float | None = None, width: int = 720) -> list[tuple[float, Image.Image]]:
    """n frames across [start, end] (skipping the first/last 4 %), as RGB images ``width`` px wide."""
    from . import ff
    dur = end if end is not None else (ff.probe(video).get("duration") or 0.0)
    span = max(0.5, dur - start)
    out = []
    with tempfile.TemporaryDirectory(prefix="hq_cov_") as d:
        for i in range(n):
            t = start + span * (0.04 + 0.92 * i / max(1, n - 1))
            p = Path(d) / f"f{i}.jpg"
            try:
                ff.run(["-y", "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(p)],
                       timeout=60)
                out.append((round(t, 2), Image.open(p).convert("RGB")))
            except Exception:  # noqa: BLE001 - a frame that won't decode is skipped
                continue
    return out


def faces(im: Image.Image) -> list[tuple[float, float, float, float]]:
    """Face boxes as fractions (x0, y0, x1, y1). OpenCV Haar cascade when installed, else none."""
    try:
        import cv2  # opencv-python-headless (Apache-2.0), on the video worker
        import numpy as np
    except Exception:  # noqa: BLE001
        return []
    try:
        g = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2GRAY)
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        h, w = g.shape
        found = casc.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(max(24, w // 18), max(24, w // 18)))
    except Exception:  # noqa: BLE001
        return []
    return [(x / w, y / h, (x + fw) / w, (y + fh) / h) for (x, y, fw, fh) in found][:4]


def frame_quality(im: Image.Image) -> dict:
    """Sharpness, exposure, contrast and colour of one frame; ``reject`` for black / white / flat (transition) frames."""
    g = im.convert("L").resize((320, max(1, int(320 * im.height / im.width))))
    st = ImageStat.Stat(g)
    mean, sd = st.mean[0], st.stddev[0]
    edges = ImageStat.Stat(g.filter(ImageFilter.FIND_EDGES)).mean[0]
    sat = ImageStat.Stat(im.convert("HSV").resize((160, 90))).mean[1]
    reject = mean < 28 or mean > 232 or sd < 18
    expo = 1.0 - min(1.0, abs(mean - 120) / 110)
    score = (min(edges, 40) / 40) * 0.45 + min(sd, 70) / 70 * 0.25 + expo * 0.2 + min(sat, 120) / 120 * 0.1
    return {"sharp": round(edges, 2), "bright": round(mean, 1), "contrast": round(sd, 1), "colour": round(sat, 1),
            "score": round(0.0 if reject else score, 4), "reject": reject}


def _hash(im: Image.Image) -> list[int]:
    px = list(im.convert("L").resize((9, 8)).getdata())
    return [1 if px[r * 9 + c] > px[r * 9 + c + 1] else 0 for r in range(8) for c in range(8)]


def best_frames(frames: list[tuple[float, Image.Image]], k: int = 3) -> list[dict]:
    """Top k distinct frames: {t, image, quality, faces, score}."""
    scored = []
    for t, im in frames:
        q = frame_quality(im)
        if q["reject"]:
            continue
        fs = faces(im)
        scored.append({"t": t, "image": im, "quality": q, "faces": fs, "score": round(q["score"] + (0.12 if fs else 0.0), 4),
                       "hash": _hash(im)})
    scored.sort(key=lambda x: -x["score"])
    out: list[dict] = []
    for s in scored:
        if all(sum(a != b for a, b in zip(s["hash"], o["hash"])) > 10 for o in out):
            out.append(s)
        if len(out) >= k:
            break
    return out


def subject_center(im: Image.Image, fs: list) -> tuple[float, float]:
    """Where the subject is (fractions): the faces' centre, else the edge-density centroid of the frame."""
    if fs:
        return (sum((f[0] + f[2]) / 2 for f in fs) / len(fs), sum((f[1] + f[3]) / 2 for f in fs) / len(fs))
    g = im.convert("L").resize((48, 27 if im.width >= im.height else 64)).filter(ImageFilter.FIND_EDGES)
    w, h = g.size
    px = list(g.getdata())
    tot = sum(px) or 1
    cx = sum((i % w) * v for i, v in enumerate(px)) / tot / max(1, w - 1)
    cy = sum((i // w) * v for i, v in enumerate(px)) / tot / max(1, h - 1)
    return 0.5 + (cx - 0.5) * 0.7, 0.5 + (cy - 0.5) * 0.7  # pulled toward the centre: saliency is a hint, not a truth


def crop_to(im: Image.Image, size: tuple[int, int], center: tuple[float, float]) -> tuple[Image.Image, tuple]:
    """Scale to cover ``size`` and crop around ``center``; returns (image, (x, y, scale, nw, nh)) to map boxes."""
    W, H = size
    s = max(W / im.width, H / im.height)
    nw, nh = int(math.ceil(im.width * s)), int(math.ceil(im.height * s))
    big = im.resize((nw, nh), Image.LANCZOS)
    x = int(min(max(0, center[0] * nw - W / 2), nw - W))
    y = int(min(max(0, center[1] * nh - H / 2), nh - H))
    return big.crop((x, y, x + W, y + H)), (x, y, s, nw, nh)


def _map_faces(fs, m, size) -> list[tuple[int, int, int, int]]:
    x, y, _s, nw, nh = m
    W, H = size
    out = []
    for f in fs:
        b = (int(f[0] * nw - x), int(f[1] * nh - y), int(f[2] * nw - x), int(f[3] * nh - y))
        if b[2] > 0 and b[3] > 0 and b[0] < W and b[1] < H:
            out.append(b)
    return out


# ------------------------------------------------------------------------------------------------ headline
def hook_words(text: str, name: str = "", max_words: int = 5) -> tuple[list[str], int]:
    """A 2-5 word cover line from the hook, and the index of its key word (drawn in the accent).

    A short hook is kept as written. A long one becomes "<Name>: <payoff>" (the product name, then the last content words,
    where a headline usually lands: "Screen Studio turns screen recordings into polished demos" -> "Screen Studio: polished
    demos"); with no name, its first content words. The key word is a number / money figure when there is one, else the
    payoff's last word."""
    t = re.sub(r"https?://\S+", "", text or "").strip()
    t = re.sub(r"[“”\"`]", "", t)
    words = [w.strip(".,;:!?()[]") for w in t.split() if w.strip(".,;:!?()[]")]
    if not words:
        words = (name or "Watch this").split()
    nm = [w.lower() for w in (name or "").split()]

    def num(w):
        return bool(re.search(r"[$€£₹]|\d", w))
    if len(words) <= max_words:
        keep = list(words)
    elif nm and len(nm) <= 3 and [w.lower() for w in words[:len(nm)]] == nm:
        rest = [w for w in words[len(nm):] if w.lower() not in STOP and w.lower() not in nm]
        room = max(1, min(2, max_words - len(nm)))
        nums = [w for w in rest if num(w)]
        tail = (nums[:1] + [w for w in rest[-room:] if w not in nums])[:room] if nums else rest[-room:]
        keep = words[:len(nm)] + tail
        keep[len(nm) - 1] = keep[len(nm) - 1] + ":"
    else:
        keep = [w for w in words if w.lower() not in STOP][:max_words]
    if len(keep) < 2:
        keep = words[:max_words]
    key = next((i for i, w in enumerate(keep) if num(w)), len(keep) - 1)
    return keep, key


def _head_font(brand: dict) -> str:
    f = str(brand.get("head_font") or "")
    if f.endswith(".ttf"):
        return f
    return HEAD_FONTS.get(str(brand.get("font") or "").lower(), "Anton.ttf")


def _logo(brand: dict, h: int) -> Image.Image | None:
    p = brand.get("logo")
    if not p:
        return None
    try:
        im = Image.open(p).convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
    s = h / max(1, im.height)
    return im.resize((max(1, int(im.width * s)), h), Image.LANCZOS)


def _gradient(size, box, side: str, color, strength: float = 0.82) -> Image.Image:
    """A soft dark gradient toward the text side + a light vignette (RGBA overlay)."""
    W, H = size
    ov = Image.new("RGBA", size, (0, 0, 0, 0))
    if side in ("bottom", "top"):
        h = int(min(H, (box[3] - box[1]) * 2.2 + H * 0.12))
        m = Image.new("L", (1, 256))
        for i in range(256):
            m.putpixel((0, i), int(255 * strength * (i / 255) ** 1.6))
        m = m.resize((W, h))
        if side == "top":
            m = ImageOps.flip(m)
        layer = Image.new("RGBA", (W, h), color + (255,))
        layer.putalpha(m)
        ov.alpha_composite(layer, (0, H - h if side == "bottom" else 0))
    else:
        w = int(min(W, (box[2] - box[0]) * 1.5 + W * 0.1))
        m = Image.new("L", (256, 1))
        for i in range(256):
            m.putpixel((i, 0), int(255 * strength * ((255 - i) / 255) ** 1.4))
        m = m.resize((w, H))
        layer = Image.new("RGBA", (w, H), color + (255,))
        layer.putalpha(m)
        ov.alpha_composite(layer, (0, 0))
    vig = Image.new("L", size, 0)
    ImageDraw.Draw(vig).ellipse((-W * 0.25, -H * 0.2, W * 1.25, H * 1.2), fill=255)
    vig = ImageOps.invert(vig.filter(ImageFilter.GaussianBlur(max(W, H) // 10))).point(lambda v: int(v * 0.45))
    vl = Image.new("RGBA", size, (0, 0, 0, 255))
    vl.putalpha(vig)
    ov.alpha_composite(vl)
    return ov


def safe_box(ratio: str, W: int, H: int) -> tuple[int, int, int, int]:
    """Where text may go. 9:16: inside the centre 4:5 band (the grid crop) with side margins; 4:5: inside the centre 3:4
    crop; 16:9: margins, clear of the bottom-right timestamp."""
    if ratio == "9:16":
        band = int(W * 5 / 4)
        y0 = (H - band) // 2
        return (int(W * 0.08), y0 + int(band * 0.05), int(W * 0.92), y0 + band - int(band * 0.05))
    if ratio == "4:5":
        cw = int(H * 3 / 4)
        x0 = (W - cw) // 2
        return (x0 + int(cw * 0.06), int(H * 0.06), x0 + cw - int(cw * 0.06), int(H * 0.94))
    return (int(W * 0.05), int(H * 0.08), int(W * 0.95), int(H * 0.86))


def _text_area(layout: str, ratio: str, W: int, H: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = safe_box(ratio, W, H)
    if ratio == "16:9":
        if layout in ("left", "inset"):
            return (x0, y0, x0 + int((x1 - x0) * 0.56), y1)
        if layout == "top":
            return (x0, y0, x1 - int(W * 0.12), y0 + int((y1 - y0) * 0.42))
        return (x0, y1 - int((y1 - y0) * 0.42), x1 - int(W * 0.16), y1)
    h = int((y1 - y0) * (0.34 if ratio == "9:16" else 0.36))
    if layout == "inset":
        return (x0, y0, x1, y0 + int((y1 - y0) * (0.30 if ratio == "9:16" else 0.33)))
    if layout == "top":
        top = y0 + int((y1 - y0) * 0.08)
        return (x0, top, x1, top + h)
    if layout == "left":
        top = y0 + (y1 - y0 - h) // 2
        return (x0, top, x0 + int((x1 - x0) * 0.8), top + h)
    return (x0, y1 - h, x1, y1)


def _overlap(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy / max(1, (b[2] - b[0]) * (b[3] - b[1]))


def compose_cover(base: Image.Image, words: list[str], key: int, brand: dict, ratio: str, layout: str,
                  fs: list | None = None) -> tuple[Image.Image, dict]:
    """One cover at ``ratio``: crop around the subject, gradient + vignette, the hook (key word in the accent), the logo."""
    W, H = SIZES[ratio]
    fs = fs or []
    dark = _rgb(brand.get("bg") or "#0B0B0F", (11, 11, 15))
    accent = _rgb(brand.get("accent") or "#C9ACFA", (201, 172, 250))
    area = _text_area(layout, ratio, W, H)
    font_name = _head_font(brand)
    text = " ".join(words).upper() if font_name == "Anton.ttf" else " ".join(words)
    fit = textfit.fit(text, font_name, area[2] - area[0], area[3] - area[1], max_size=int(H * (0.12 if ratio == "16:9" else 0.075 if ratio == "9:16" else 0.09)),
                      min_size=int(H * 0.03), max_lines=3)
    f = textfit.font(font_name, fit.size)
    lh = fit.line_height
    tx0 = area[0]
    ty0 = area[3] - fit.height if layout == "bottom" else (area[1] + (area[3] - area[1] - fit.height) // 2 if layout == "left" else area[1])
    if layout == "inset":
        ty0 = area[3] - fit.height if ratio != "16:9" else area[1] + (area[3] - area[1] - fit.height) // 2
    tbox = (tx0, ty0, tx0 + fit.width, ty0 + fit.height)
    if layout == "inset":
        # brand ground + the picture framed (rounded, shadow): the tasteful crop for screen recordings and UI shots
        canvas = Image.new("RGBA", (W, H), dark + (255,))
        glow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(glow).ellipse((W * 0.1, H * 0.35, W * 1.2, H * 1.15), fill=90)
        glow = glow.filter(ImageFilter.GaussianBlur(max(W, H) // 6))
        gl = Image.new("RGBA", (W, H), accent + (255,))
        gl.putalpha(glow)
        canvas.alpha_composite(gl)
        sb = safe_box(ratio, W, H)
        if ratio == "16:9":
            fx0, fy0, fx1, fy1 = int(W * 0.58), int(H * 0.14), int(W * 0.97), int(H * 0.86)
        else:
            fx0, fx1 = int(W * 0.06), int(W * 0.94)
            fy0 = tbox[3] + int(H * 0.035)
            fy1 = min(sb[3] + int(H * 0.08), H - int(H * 0.06))
        fw, fh = fx1 - fx0, max(40, fy1 - fy0)
        pic, m = crop_to(base, (fw, fh), subject_center(base, fs))
        rad = max(12, W // 40)
        mask = Image.new("L", (fw, fh), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, fw - 1, fh - 1), radius=rad, fill=255)
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((fx0 + 10, fy0 + 18, fx1 + 10, fy1 + 18), radius=rad, fill=(0, 0, 0, 150))
        canvas.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
        pr = pic.convert("RGBA")
        pr.putalpha(mask)
        canvas.alpha_composite(pr, (fx0, fy0))
        ImageDraw.Draw(canvas).rounded_rectangle((fx0, fy0, fx1, fy1), radius=rad, outline=accent + (200,), width=max(2, W // 360))
        face_px = [(b[0] + fx0, b[1] + fy0, b[2] + fx0, b[3] + fy0) for b in _map_faces(fs, m, (fw, fh))]
    else:
        img, m = crop_to(base, (W, H), subject_center(base, fs))
        face_px = _map_faces(fs, m, (W, H))
        canvas = img.convert("RGBA")
        pre = canvas.crop(tbox).convert("RGB") if tbox[2] > tbox[0] and tbox[3] > tbox[1] else None
        bright = _lum(tuple(int(v) for v in ImageStat.Stat(pre).mean[:3])) if pre else 0.0
        canvas.alpha_composite(_gradient((W, H), tbox, layout, dark, 0.97 if bright > 0.3 else 0.82))
        post = canvas.crop(tbox).convert("RGB") if pre else None
        if post and _lum(tuple(int(v) for v in ImageStat.Stat(post).mean[:3])) > 0.18:
            # still too bright behind the words (a white UI): a soft brand-coloured plate under them
            pad = int(H * 0.018)
            plate = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ImageDraw.Draw(plate).rounded_rectangle((tbox[0] - pad, tbox[1] - pad, tbox[2] + pad, tbox[3] + pad),
                                                    radius=pad, fill=dark + (205,))
            canvas.alpha_composite(plate)
    behind = canvas.crop(tbox).convert("RGB") if tbox[2] > tbox[0] and tbox[3] > tbox[1] else None
    bg_mean = tuple(int(v) for v in ImageStat.Stat(behind).mean[:3]) if behind else dark
    text_col = _rgb(brand.get("text") or "#FFFFFF")
    if contrast(text_col, bg_mean) < 3:  # a light brand text colour on a light patch: switch to the readable one
        text_col = (255, 255, 255) if _lum(bg_mean) < 0.4 else (12, 12, 14)
    if contrast(accent, bg_mean) < 2.2:
        accent = text_col
    d = ImageDraw.Draw(canvas)
    flat = text.split()
    k_word = flat[key] if 0 <= key < len(flat) else ""
    y, idx = ty0, 0
    for ln in fit.lines:  # word by word so the key word takes the accent; the wrap is textfit's
        x = tx0
        for w in ln.split():
            col = accent if (idx == key and w == k_word) else text_col
            d.text((x + 3, y + 4), w, font=f, fill=(0, 0, 0, 110))
            d.text((x, y), w, font=f, fill=col + (255,))
            x += f.getlength(w + " ")
            idx += 1
        y += lh
    sb = safe_box(ratio, W, H)
    bar_y = tbox[3] + int(H * 0.018)
    if ratio != "16:9" and layout in ("bottom", "left") and bar_y + 8 < sb[3]:
        d.rectangle((tx0, bar_y, tx0 + int(W * 0.12), bar_y + max(6, H // 240)), fill=accent + (255,))
    lg = _logo(brand, int(H * (0.045 if ratio != "16:9" else 0.085)))
    lbox = None
    if lg:
        lx, ly = (sb[0], sb[1]) if layout != "top" else (sb[0], sb[3] - lg.height)
        if layout == "inset" and ratio != "16:9":
            lx, ly = sb[0], max(int(H * 0.06), tbox[1] - lg.height - int(H * 0.025))
        if ratio == "16:9":
            lx, ly = (W - lg.width - int(W * 0.04), int(H * 0.06))
        canvas.alpha_composite(lg, (lx, ly))
        lbox = (lx, ly, lx + lg.width, ly + lg.height)
    face_hit = max((_overlap(tbox, fb) for fb in face_px), default=0.0)
    inside = tbox[0] >= sb[0] - 1 and tbox[1] >= sb[1] - 1 and tbox[2] <= sb[2] + 1 and tbox[3] <= sb[3] + 1
    return canvas.convert("RGB"), {"text_box": tbox, "logo_box": lbox, "faces": face_px, "face_overlap": round(face_hit, 3),
                                   "contrast": round(contrast(text_col, bg_mean), 2), "inside_safe": inside, "fit_ok": fit.ok,
                                   "font": font_name, "text": text, "key_word": k_word}


def score_variant(info: dict, frame_score: float) -> float:
    legible = min(info["contrast"], 10.0) / 10.0
    return round(0.5 * legible + 0.3 * min(1.0, frame_score / 0.8) + 0.2 * (1 - min(1.0, info["face_overlap"] * 3))
                 - (0.3 if not info["inside_safe"] else 0) - (0.15 if not info["fit_ok"] else 0), 4)


def make_covers(video: str | None = None, image: Image.Image | bytes | None = None, hook: str = "", brand: dict | None = None,
                name: str = "", clip: tuple[float, float] | None = None, n_variants: int = 3, frames=None) -> dict:
    """Three cover variants, best first: {"variants": [{layout, frame_t, source, score, info, images: {ratio: Image}}],
    "words", "key"}. ``frames`` (tests) replaces FFmpeg sampling with [(t, Image)]."""
    brand = dict(brand or {})
    cands: list[dict] = []
    if video or frames is not None:
        fr = frames if frames is not None else sample_frames(video, start=(clip or (0, None))[0], end=(clip or (0, None))[1])
        cands = best_frames(fr, k=n_variants)
    if image is not None:
        hero = Image.open(io.BytesIO(image)).convert("RGB") if isinstance(image, (bytes, bytearray)) else image.convert("RGB")
        q = frame_quality(hero)
        hq = {"t": None, "image": hero, "quality": q, "faces": faces(hero), "score": max(q["score"], 0.35), "source": "hero"}
        cands = ([hq] + cands) if (not cands or cands[0]["score"] < 0.3) else (cands + [hq])
    if not cands:
        return {"variants": [], "words": [], "key": 0, "note": "No usable frame or image for a cover."}
    words, key = hook_words(hook, name)
    plans = [(cands[min(i, len(cands) - 1)] if i < 2 else cands[0], layout) for i, layout in enumerate(LAYOUTS[:n_variants])]
    variants = []
    for c, layout in plans:
        imgs, infos = {}, {}
        for r in SIZES:
            im, info = compose_cover(c["image"], words, key, brand, r, layout, c.get("faces"))
            if info["face_overlap"] > 0.15:  # never text over a face: try the other side
                alt = "top" if layout != "top" else "bottom"
                im2, info2 = compose_cover(c["image"], words, key, brand, r, alt, c.get("faces"))
                if info2["face_overlap"] < info["face_overlap"]:
                    im, info = im2, info2
            imgs[r], infos[r] = im, info
        sc = round(sum(score_variant(infos[r], c["score"]) for r in SIZES) / len(SIZES), 4)
        variants.append({"layout": layout, "frame_t": c.get("t"), "source": c.get("source", "frame"), "score": sc,
                         "info": {r: {k: infos[r][k] for k in ("contrast", "face_overlap", "inside_safe", "fit_ok", "text_box")} for r in SIZES},
                         "images": imgs})
    variants.sort(key=lambda v: -v["score"])
    return {"variants": variants, "words": words, "key": key}


def jpeg(im: Image.Image, q: int = 88) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
    return buf.getvalue()
