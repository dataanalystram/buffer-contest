"""'Follow @huhbyte' call-to-action for video posts.

follow_cta option (every video post, every network):
  off      - nothing
  card     - append a 2.5 s branded end card to the video (needs ffmpeg on PATH)
  comment  - post the CTA as the first comment (Instagram, YouTube); TikTok has no
             comment API, so the CTA line is appended to the caption instead
  both     - card + comment
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC = Path(__file__).resolve().parent.parent / "static"
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
CTA_CHOICES = [["off", "Off"], ["card", "End card on the video"], ["comment", "First comment"],
               ["both", "End card + first comment"]]


class EndCardError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _font(size: int):
    for p in FONT_CANDIDATES:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def render_card(width: int, height: int, text: str, out: Path) -> Path:
    """Draw the end-card frame: soft lavender background, HuhByte mark, CTA text."""
    img = Image.new("RGB", (width, height), "#F3ECFF")
    d = ImageDraw.Draw(img)
    # vertical lavender gradient
    for y in range(height):
        t = y / max(1, height - 1)
        c = tuple(int(a + (b - a) * t) for a, b in zip((0xF6, 0xF1, 0xFF), (0xC9, 0xAC, 0xFA)))
        d.line([(0, y), (width, y)], fill=c)
    mark = Image.open(STATIC / "mark.png").convert("RGBA")
    side = int(min(width, height) * 0.32)
    mark = mark.resize((side, int(side * mark.height / mark.width)), Image.LANCZOS)
    mx, my = (width - mark.width) // 2, int(height * 0.38) - mark.height // 2
    img.paste(mark, (mx, my), mark)
    size = max(18, int(min(width, height) * 0.075))
    f = _font(size)
    lines = text.split("\n")
    y = my + mark.height + int(size * 0.8)
    for i, line in enumerate(lines):
        fnt = f if i == 0 else _font(int(size * 0.6))
        w = d.textlength(line, font=fnt)
        d.text(((width - w) / 2, y), line, font=fnt, fill="#2B1B4A" if i == 0 else "#5B3FA0")
        y += int(fnt.size * 1.4) if hasattr(fnt, "size") else size
    img.save(out, "PNG")
    return out


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise EndCardError(f"ffprobe failed: {r.stderr[-300:]}")
    data = json.loads(r.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise EndCardError("No video stream")
    num, _, den = (v.get("avg_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {"width": int(v["width"]), "height": int(v["height"]), "fps": round(fps or 30.0, 3),
            "duration": float(data.get("format", {}).get("duration") or v.get("duration") or 0),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams)}


def append_end_card(src: Path, dest: Path, text: str, seconds: float = 2.5) -> Path:
    if not ffmpeg_available():
        raise EndCardError("ffmpeg is not installed - install it (brew install ffmpeg / apt install ffmpeg) "
                           "or choose 'First comment' instead of an end card.")
    info = probe(src)
    w, h, fps = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2, info["fps"]
    card = dest.with_suffix(".card.png")
    render_card(w, h, text, card)
    inputs = ["-i", str(src), "-loop", "1", "-t", str(seconds), "-i", str(card),
              "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    a0 = ("[0:a]aresample=44100,aformat=channel_layouts=stereo[a0];" if info["has_audio"]
          else f"[2:a]atrim=0:{info['duration'] or 0.1},asetpts=N/SR/TB[a0];")
    filt = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,"
            f"setsar=1,fps={fps},format=yuv420p[v0];"
            f"[1:v]scale={w}:{h},setsar=1,fps={fps},format=yuv420p[v1];"
            f"{a0}"
            f"[2:a]atrim=0:{seconds},asetpts=N/SR/TB[a1];"
            f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]")
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    card.unlink(missing_ok=True)
    if r.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        raise EndCardError(f"ffmpeg failed: {r.stderr[-400:]}")
    return dest


def ensure_end_card(core, media, text: str) -> str:
    """Create (once) the end-carded copy of a video Media row; returns its stored file name."""
    key = hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:8]
    name = f"{media.id}-endcard-{key}.mp4"
    path = core.settings.media_dir / name
    if not path.exists():
        append_end_card(core.settings.media_dir / media.stored_name, path, text)
    return name
