"""FFmpeg for the media agent: find the binaries, run them hardened, probe a file.

Every call goes through ``studio.mediasafe`` (protocol + demuxer whitelists before every input, ``-nostdin``, timeout).
mediasafe recognises the tool by its file NAME (``ffmpeg`` / ``ffprobe``). The imageio-ffmpeg wheel ships its binary
as ``ffmpeg-linux-x86_64-v7.0.2``, which mediasafe would NOT harden, so ``ffmpeg_bin()`` links such a binary to a file
literally called ``ffmpeg`` in a private directory first. The wheel ships no ffprobe: ``probe()`` falls back to
parsing ``ffmpeg -i`` when ffprobe is missing.

Binary order: ``HQ_FFMPEG`` env, ``ffmpeg`` on PATH (Debian/Ubuntu apt package: has ffprobe and libass), then the
imageio-ffmpeg static build (GPLv3 binary, libass included, no ffprobe, no drawtext).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

from .. import mediasafe


class MediaError(RuntimeError):
    """A media step failed; the message is safe to show (no paths with secrets, no tokens)."""


@lru_cache(maxsize=1)
def ffmpeg_bin() -> str:
    cand = os.environ.get("HQ_FFMPEG") or shutil.which("ffmpeg")
    if not cand:
        try:
            import imageio_ffmpeg  # optional wheel: pip install imageio-ffmpeg
            cand = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            cand = ""
    if not cand:
        raise MediaError("FFmpeg isn't installed on this server.")
    if Path(cand).name != "ffmpeg":  # mediasafe hardens by name: give it a name it recognises
        d = Path(tempfile.gettempdir()) / "hq-ffbin"
        d.mkdir(exist_ok=True)
        link = d / "ffmpeg"
        if not link.exists():
            try:
                link.symlink_to(cand)
            except OSError:
                shutil.copy2(cand, link)
        cand = str(link)
    return cand


@lru_cache(maxsize=1)
def ffprobe_bin() -> str | None:
    p = os.environ.get("HQ_FFPROBE") or shutil.which("ffprobe")
    if not p:
        sib = Path(ffmpeg_bin()).resolve().with_name("ffprobe")
        p = str(sib) if sib.exists() else None
    return p


def have_ffmpeg() -> bool:
    try:
        ffmpeg_bin()
        return True
    except MediaError:
        return False


def run(args: list[str], timeout: float = 900) -> str:
    """Run ``ffmpeg <args>`` hardened. Returns stderr text. Raises MediaError with the tail of stderr on failure."""
    r = mediasafe.run([ffmpeg_bin(), "-hide_banner", *args], timeout=timeout, text=True)
    if r.returncode != 0:
        raise MediaError("FFmpeg failed: " + (r.stderr or "")[-600:])
    return r.stderr or ""


def probe(path: str) -> dict:
    """{duration, width, height, fps, has_audio} for a local file."""
    fp = ffprobe_bin()
    if fp:
        r = mediasafe.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
                          timeout=120, text=True)
        j = json.loads(r.stdout or "{}")
        v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
        a = any(s.get("codec_type") == "audio" for s in j.get("streams", []))
        num, _, den = (v.get("r_frame_rate") or "30/1").partition("/")
        fps = float(num) / float(den or 1) if float(den or 1) else 30.0
        dur = float((j.get("format") or {}).get("duration") or v.get("duration") or 0)
        return {"duration": dur, "width": int(v.get("width") or 0), "height": int(v.get("height") or 0),
                "fps": round(fps, 3), "has_audio": a}
    r = mediasafe.run([ffmpeg_bin(), "-hide_banner", "-i", path], timeout=60, text=True)  # exits 1: no output file
    err = r.stderr or ""
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
    v = re.search(r"Video: .*?(\d{2,5})x(\d{2,5})", err)
    f = re.search(r"([\d.]+) fps", err)
    return {"duration": dur, "width": int(v.group(1)) if v else 0, "height": int(v.group(2)) if v else 0,
            "fps": float(f.group(1)) if f else 30.0, "has_audio": "Audio:" in err}


def mean_volume(path: str) -> float:
    """Mean loudness in dB (-91 for no audio stream / digital silence)."""
    try:
        err = run(["-i", path, "-map", "0:a:0?", "-af", "volumedetect", "-vn", "-f", "null", "-"], timeout=300)
    except MediaError:
        return -91.0
    m = re.search(r"mean_volume: (-?[\d.]+) dB", err)
    return float(m.group(1)) if m else -91.0


def is_silent(path: str, thresh_db: float = -50.0) -> bool:
    return mean_volume(path) < thresh_db
