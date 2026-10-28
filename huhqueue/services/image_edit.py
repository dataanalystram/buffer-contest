"""In-app image editor (Buffer's 15 Sep 2026 editor, free): rotate, flip, crop (free or to a network aspect ratio),
resize, brightness / contrast / saturation and text overlays, applied with Pillow on the server.

The editor never touches the original: every save writes a new media item ("<name>-edited.<ext>") that keeps the
original's alt text, title, tags and folder, so posts that already use the original are unchanged. The same call with
`preview=true` returns a small JPEG data URL instead of saving, which is what the UI shows while you edit.

Operation order matches the browser cropper (react-easy-crop): flip, then rotate clockwise around the centre with the
canvas grown to fit, then crop in pixels of that rotated image, then resize, then adjust colours, then draw text.
"""
from __future__ import annotations

import base64
import io
import re
import tempfile
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFont, ImageOps

from ..models import Media
from .media import MediaError, save_file

# Aspect presets the composer offers; values are width / height.
ASPECTS = {"1:1": 1.0, "4:5": 4 / 5, "9:16": 9 / 16, "16:9": 16 / 9, "1.91:1": 1.91, "3:4": 3 / 4, "2:3": 2 / 3}
MAX_SIDE = 8192
PREVIEW_SIDE = 1080
FONTS = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")
OUT_FORMAT = {"image/jpeg": ("JPEG", ".jpg"), "image/png": ("PNG", ".png"), "image/webp": ("WEBP", ".webp")}


def _font(px: int) -> ImageFont.ImageFont:
    for f in FONTS:
        if Path(f).exists():
            try:
                return ImageFont.truetype(f, px)
            except OSError:
                continue
    return ImageFont.load_default(size=px)


def _color(v, default: str) -> tuple:
    try:
        c = ImageColor.getcolor(str(v or default), "RGBA")
    except ValueError as e:
        raise MediaError(f"Not a colour: {v}") from e
    return c


def _num(ops: dict, key: str, lo: float, hi: float, default: float) -> float:
    v = ops.get(key, default)
    try:
        v = float(default if v is None or v == "" else v)
    except (TypeError, ValueError) as e:
        raise MediaError(f"{key} must be a number") from e
    if not lo <= v <= hi:
        raise MediaError(f"{key} must be between {lo:g} and {hi:g}")
    return v


def center_crop(w: int, h: int, ratio: float) -> dict:
    """Largest centred box of `ratio` (w/h) inside w x h."""
    if w / h > ratio:
        cw, ch = round(h * ratio), h
    else:
        cw, ch = w, round(w / ratio)
    return {"x": (w - cw) // 2, "y": (h - ch) // 2, "width": cw, "height": ch}


def parse_aspect(v) -> float:
    if v in ASPECTS:
        return ASPECTS[v]
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[:/x]\s*(\d+(?:\.\d+)?)\s*", str(v or ""))
    if not m or not float(m.group(2)):
        raise MediaError(f"aspect must look like 4:5 (presets: {', '.join(ASPECTS)})")
    return float(m.group(1)) / float(m.group(2))


def apply(im: Image.Image, ops: dict) -> Image.Image:
    """Run the edit operations on an image (pure: no files, no database)."""
    im = ImageOps.exif_transpose(im)
    im = im.convert("RGBA")
    if ops.get("flip_h"):
        im = ImageOps.mirror(im)
    if ops.get("flip_v"):
        im = ImageOps.flip(im)
    angle = _num(ops, "rotate", -360, 360, 0) % 360
    if angle:
        im = im.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True)  # Pillow turns counter-clockwise
    crop = ops.get("crop")
    if not crop and ops.get("aspect"):
        crop = center_crop(*im.size, parse_aspect(ops["aspect"]))
    if crop:
        try:
            x, y, w, h = (int(round(float(crop[k]))) for k in ("x", "y", "width", "height"))
        except (KeyError, TypeError, ValueError) as e:
            raise MediaError("crop needs x, y, width and height in pixels") from e
        x, y = max(0, x), max(0, y)
        w, h = min(w, im.width - x), min(h, im.height - y)
        if w < 1 or h < 1:
            raise MediaError("crop is outside the image")
        im = im.crop((x, y, x + w, y + h))
    rs = ops.get("resize") or {}
    if rs.get("width") or rs.get("height"):
        try:
            rw, rh = int(rs.get("width") or 0), int(rs.get("height") or 0)
        except (TypeError, ValueError) as e:
            raise MediaError("resize width/height must be whole numbers") from e
        rw = rw or round(im.width * rh / im.height)
        rh = rh or round(im.height * rw / im.width)
        if not (1 <= rw <= MAX_SIDE and 1 <= rh <= MAX_SIDE):
            raise MediaError(f"resize must be between 1 and {MAX_SIDE} pixels a side")
        im = im.resize((rw, rh), Image.Resampling.LANCZOS)
    for key, enh in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast), ("saturation", ImageEnhance.Color)):
        f = _num(ops, key, 0, 3, 1)
        if f != 1:
            alpha = im.getchannel("A")
            im = enh(im.convert("RGB")).enhance(f).convert("RGBA")
            im.putalpha(alpha)
    texts = ops.get("texts") or []
    if not isinstance(texts, list) or len(texts) > 10:
        raise MediaError("texts must be a list of up to 10 overlays")
    for t in texts:
        _draw_text(im, t if isinstance(t, dict) else {"text": str(t)})
    return im


def _draw_text(im: Image.Image, t: dict) -> None:
    text = str(t.get("text") or "").strip()[:300]
    if not text:
        return
    size = max(8, round(_num(t, "size", 0.01, 0.5, 0.08) * im.height))
    font = _font(size)
    x, y = _num(t, "x", 0, 1, 0.5) * im.width, _num(t, "y", 0, 1, 0.5) * im.height
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    align = t.get("align") if t.get("align") in ("left", "center", "right") else "center"
    box = d.multiline_textbbox((x, y), text, font=font, anchor="mm", align=align)
    if t.get("background"):
        pad = round(size * 0.3)
        d.rounded_rectangle((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad), radius=pad,
                            fill=_color(t["background"], "#00000099"))
    stroke = round(size * 0.06) if t.get("outline", not t.get("background")) else 0
    d.multiline_text((x, y), text, font=font, anchor="mm", align=align, fill=_color(t.get("color"), "#ffffff"),
                     stroke_width=stroke, stroke_fill=(0, 0, 0, 200))
    im.alpha_composite(layer)


def _flatten(im: Image.Image, keep_alpha: bool) -> Image.Image:
    if keep_alpha:
        return im
    bg = Image.new("RGB", im.size, (255, 255, 255))
    bg.paste(im, mask=im.getchannel("A"))
    return bg


def preview(core, media: Media, ops: dict) -> dict:
    with Image.open(core.settings.media_dir / media.stored_name) as src:
        im = apply(src, ops)
    w, h = im.size
    small = _flatten(im, False)
    small.thumbnail((PREVIEW_SIDE, PREVIEW_SIDE))
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=85)
    return {"preview": "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), "width": w, "height": h}


def edit(core, media: Media, ops: dict) -> Media:
    """Apply `ops` and save the result as a new media item next to the original."""
    if media.kind != "image":
        raise MediaError("Only images can be edited (videos and PDFs are not supported)")
    with Image.open(core.settings.media_dir / media.stored_name) as src:
        im = apply(src, ops)
    fmt, ext = OUT_FORMAT.get(media.mime, ("JPEG", ".jpg"))  # GIF / HEIC come out as JPEG
    keep_alpha = fmt in ("PNG", "WEBP")
    out = _flatten(im, keep_alpha)
    with tempfile.NamedTemporaryFile(delete=False, dir=core.settings.media_dir, prefix=".edit-", suffix=ext) as tmp:
        out.save(tmp, fmt, **({"quality": 92, "optimize": True} if fmt == "JPEG" else {}))
    stem = re.sub(r"-edited(-\d+)?$", "", Path(media.filename).stem)
    alt = ops["alt_text"][:1000] if isinstance(ops.get("alt_text"), str) else (media.alt_text or "")
    new = save_file(core, Path(tmp.name), f"{stem}-edited{ext}", alt_text=alt)
    with core.db.session() as s:
        m = s.get(Media, new.id)
        m.title = getattr(media, "title", "") or ""
        m.tags = list(getattr(media, "tags", None) or [])
        m.folder = getattr(media, "folder", "") or ""
        s.flush()
        s.expunge(m)
    return m
