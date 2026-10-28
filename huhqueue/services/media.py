"""Media library: store uploads, keep a JPEG copy for Instagram, build public URLs."""
from __future__ import annotations

import mimetypes
import re
import shutil
from pathlib import Path

import httpx
from PIL import Image, ImageOps

from ..connectors import MediaItem
from ..models import Media, new_id

IMAGE_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
             ".gif": "image/gif", ".heic": "image/heic"}
VIDEO_EXT = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
DOC_EXT = {".pdf": "application/pdf"}   # LinkedIn document (PDF carousel) posts
MAX_BYTES = 1024 * 1024 * 1024  # 1 GB


class MediaError(ValueError):
    pass


def _safe_name(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.") or "file"
    return base[:80]


def classify(filename: str, content_type: str | None = None) -> tuple[str, str]:
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXT:
        return "image", IMAGE_EXT[ext]
    if ext in VIDEO_EXT:
        return "video", VIDEO_EXT[ext]
    if ext in DOC_EXT:
        return "document", DOC_EXT[ext]
    ct = (content_type or mimetypes.guess_type(filename)[0] or "").lower()
    if ct.startswith("image/"):
        return "image", ct
    if ct.startswith("video/"):
        return "video", ct
    if ct == "application/pdf":
        return "document", ct
    raise MediaError(f"Unsupported file type: {filename} (use JPG/PNG/WebP images, MP4/MOV/WebM video or a PDF document)")


def save_file(core, src_path: Path, filename: str, content_type: str | None = None, alt_text: str = "") -> Media:
    kind, mime = classify(filename, content_type)
    size = src_path.stat().st_size
    if size == 0:
        raise MediaError("Empty file")
    if size > MAX_BYTES:
        raise MediaError("File is larger than 1 GB")
    mid = new_id("md")
    safe = _safe_name(filename)
    stored = f"{mid}-{safe}"
    dest = core.settings.media_dir / stored
    shutil.move(str(src_path), dest)
    m = Media(id=mid, filename=safe, stored_name=stored, mime=mime, kind=kind, size=size, alt_text=alt_text,
              sha256=file_sha256(dest))
    if kind == "video":
        _probe_video(dest, m)
    if kind == "image":
        try:
            with Image.open(dest) as im:
                im = ImageOps.exif_transpose(im)
                m.width, m.height = im.size
                if mime != "image/jpeg":
                    jpeg = f"{mid}-{Path(safe).stem}.jpg"
                    rgb = im.convert("RGB")
                    rgb.save(core.settings.media_dir / jpeg, "JPEG", quality=92, optimize=True)
                    m.jpeg_name = jpeg
        except Exception as e:  # Pillow could not read it
            dest.unlink(missing_ok=True)
            raise MediaError(f"Could not read image {filename}: {e}") from e
    with core.db.session() as s:
        s.add(m)
    return m


def square_crop(path: Path, size: int = 400) -> None:
    """Centre-crop an uploaded image to a size x size square in place (avatars, link thumbnails). Non-images are left alone."""
    try:
        with Image.open(path) as im:
            fmt = im.format or "PNG"
            out = ImageOps.fit(ImageOps.exif_transpose(im), (size, size), Image.LANCZOS)
        if fmt == "JPEG" and out.mode not in ("RGB", "L"):
            out = out.convert("RGB")
        out.save(path, fmt)
    except (OSError, ValueError):
        return


def file_sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_video(path: Path, m: Media) -> None:
    """Read width/height/duration with ffprobe when available (never modifies or trims the file)."""
    from .endcard import ffmpeg_available, probe
    if not ffmpeg_available():
        return
    try:
        info = probe(path)
        m.width, m.height, m.duration = info["width"], info["height"], round(info["duration"], 3)
    except Exception:
        pass


def download(core, url: str, filename: str | None = None) -> Media:
    """Fetch media from a URL (handy for pipelines that already host their renders)."""
    if not re.match(r"^https?://", url or ""):
        raise MediaError("URL must start with http(s)://")
    name = filename or Path(url.split("?")[0]).name or "download"
    tmp = core.settings.media_dir / f".dl-{new_id('tmp')}"
    try:
        with core.http.stream("GET", url) as r:
            if r.status_code >= 400:
                raise MediaError(f"Download failed: HTTP {r.status_code}")
            ct = r.headers.get("content-type", "").split(";")[0]
            if not Path(name).suffix and ct:
                name += mimetypes.guess_extension(ct) or ""
            total = 0
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise MediaError("File is larger than 1 GB")
                    fh.write(chunk)
        return save_file(core, tmp, name, ct)
    except httpx.HTTPError as e:
        raise MediaError(f"Download failed: {e}") from e
    finally:
        tmp.unlink(missing_ok=True)


def delete(core, media: Media) -> None:
    for name in (media.stored_name, media.jpeg_name):
        if name:
            (core.settings.media_dir / name).unlink(missing_ok=True)


def public_url(core, media: Media, jpeg: bool = False) -> str | None:
    base = core.public_base_url()
    if not base:
        return None
    name = media.jpeg_name if jpeg and media.jpeg_name else media.stored_name
    return f"{base}/m/{media.public_token}/{name}"


def ensure_jpeg(core, media: Media) -> bool:
    """Make sure a non-JPEG image (PNG/WebP/GIF...) has a JPEG copy on disk - Instagram only accepts JPEG.

    Normally made at upload time; this also covers older uploads, deleted copies and files whose
    extension lies about their format. Returns True when a new copy was written."""
    if media.kind != "image":
        return False
    d = core.settings.media_dir
    if media.jpeg_name and (d / media.jpeg_name).exists():
        return False
    src = d / media.stored_name
    try:
        with Image.open(src) as im:
            if im.format == "JPEG" and media.mime == "image/jpeg":
                return False
            im = ImageOps.exif_transpose(im)
            name = f"{media.id}-{Path(media.filename).stem}.jpg"
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                bg = Image.new("RGB", im.size, (255, 255, 255))
                bg.paste(im, mask=im.split()[-1])
                im = bg
            im.convert("RGB").save(d / name, "JPEG", quality=92, optimize=True)
    except (OSError, ValueError) as e:
        raise MediaError(f"Could not convert {media.filename} to JPEG: {e}") from e
    media.jpeg_name = name
    return True


def to_item(core, media: Media) -> MediaItem:
    d = core.settings.media_dir
    return MediaItem(
        id=media.id, kind=media.kind, mime=media.mime, path=d / media.stored_name, filename=media.filename,
        size=media.size, width=media.width, height=media.height, alt_text=media.alt_text or "",
        duration=media.duration, sha256=media.sha256 or "",
        public_url=public_url(core, media),
        jpeg_public_url=public_url(core, media, jpeg=True) if media.kind == "image" else None,
        jpeg_path=(d / media.jpeg_name) if media.jpeg_name else None,
    )


def media_dict(core, m: Media) -> dict:
    return {"id": m.id, "filename": m.filename, "kind": m.kind, "mime": m.mime, "size": m.size,
            "width": m.width, "height": m.height, "duration": m.duration, "sha256": m.sha256,
            "alt_text": m.alt_text, "url": f"/files/{m.id}", "public_path": f"/m/{m.public_token}/{m.stored_name}",
            "public_url": public_url(core, m), "jpeg_public_url": public_url(core, m, True) if m.jpeg_name else None,
            "created_at": m.created_at.isoformat() + "Z"}
