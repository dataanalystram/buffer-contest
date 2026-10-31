"""Run FFmpeg / FFprobe on media we did not make, safely.

A video file is a program for its demuxer. A crafted "mp4" can really be an HLS playlist or an ffconcat list whose
entries are ``http://169.254.169.254/...`` or ``file:///etc/passwd``, and FFmpeg would happily open them and bake the
result into the output. Every FFmpeg/FFprobe call in this app goes through ``run`` / ``popen`` here, which:

* puts ``-protocol_whitelist file,pipe`` before every input, so no input (or anything an input refers to) can open a
  network URL (http, https, tcp, udp, rtmp, ftp, data, crypto, subfile...);
* puts ``-format_whitelist`` before every input, so only plain media demuxers run (mp4/mov, Matroska/WebM, a few audio
  and image formats). Playlist and list demuxers (hls, dash, concat/ffconcat, tee...) are refused, which is what stops a
  playlist from pulling in other local files. Inputs we build ourselves with an explicit ``-f lavfi`` or ``-f concat``
  (our own temp-file list) get exactly that demuxer added;
* runs with ``-nostdin`` (FFmpeg) and a wall-clock timeout, and refuses a URL input or an input file over ``MAX_INPUT_BYTES``;
* ``check_source`` vets a downloaded upload before anything else touches it (MediaPipe and PySceneDetect read it with their
  own decoders): size cap, a hardened probe, and a container that must be on the plain-video list.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

PROTOCOLS = "file,pipe"
MEDIA_FORMATS = ("mov", "mp4", "m4a", "3gp", "matroska", "webm", "avi", "flv", "mpegts", "mp3", "wav", "aac", "ogg", "flac",
                 "image2", "png_pipe", "jpeg_pipe", "webp_pipe", "gif", "bmp_pipe")
OWN_FORMATS = ("lavfi", "concat")  # only when WE passed -f for an input we built (filter sources, our own clip list)
SOURCE_CONTAINERS = ("mov", "mp4", "m4a", "3gp", "matroska", "webm")  # what an uploaded video may be
URL_SCHEMES = ("http", "https", "tcp", "udp", "rtp", "rtmp", "rtsp", "ftp", "sftp", "data", "subfile", "crypto", "concat",
               "async", "cache", "tee", "gopher", "smb", "srt", "tls", "unix", "file")
MAX_INPUT_BYTES = int(os.environ.get("HQ_MAX_MEDIA_INPUT_BYTES", str(2 * 1024 * 1024 * 1024)))
DEFAULT_TIMEOUT = int(os.environ.get("HQ_FFMPEG_TIMEOUT", "1800"))

# OpenCV (PySceneDetect's default backend) opens files with its own FFmpeg: keep it to local files too.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "protocol_whitelist;file")


class MediaRefused(RuntimeError):
    pass


def _whitelist(forced: str | None) -> str:
    fmts = list(MEDIA_FORMATS)
    if forced in OWN_FORMATS:
        fmts.append(forced)
    return ",".join(fmts)


def _check_input(path: str, forced: str | None) -> None:
    if forced == "lavfi" or path in ("-", "pipe:", "pipe:0"):
        return
    if "://" in path or (":" in path and path.split(":", 1)[0].split(",", 1)[0].lower() in URL_SCHEMES):
        raise MediaRefused("Only local media files can be processed.")
    try:
        size = Path(path).stat().st_size
    except OSError:
        return  # FFmpeg reports the missing file itself
    if size > MAX_INPUT_BYTES:
        raise MediaRefused(f"The media file is over {MAX_INPUT_BYTES // (1024 * 1024)} MB.")


def harden(cmd: list[str]) -> list[str]:
    """The same command with the protocol and demuxer whitelists before every input (and -nostdin for ffmpeg)."""
    if not cmd:
        return list(cmd)
    tool = Path(cmd[0]).name
    if tool not in ("ffmpeg", "ffprobe"):
        return list(cmd)
    if tool == "ffprobe":  # one positional input; the options apply to it
        path = next((a for a in reversed(cmd[1:]) if not a.startswith("-")), "")
        if path:
            _check_input(path, None)
        return [cmd[0], "-protocol_whitelist", PROTOCOLS, "-format_whitelist", _whitelist(None), *cmd[1:]]
    out, forced, i = [cmd[0], "-nostdin"], None, 1
    while i < len(cmd):
        a = cmd[i]
        if a == "-f" and i + 1 < len(cmd):
            forced = cmd[i + 1]
        if a == "-i" and i + 1 < len(cmd):
            _check_input(cmd[i + 1], forced)
            out += ["-protocol_whitelist", PROTOCOLS, "-format_whitelist", _whitelist(forced), "-i", cmd[i + 1]]
            forced, i = None, i + 2
            continue
        if a != "-nostdin":
            out.append(a)
        i += 1
    return out


def run(cmd: list[str], timeout: float | None = None, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.run(harden(cmd), timeout=timeout or DEFAULT_TIMEOUT, **kw)


def popen(cmd: list[str], **kw) -> subprocess.Popen:
    kw.setdefault("stdin", subprocess.DEVNULL)
    return subprocess.Popen(harden(cmd), **kw)


def check_source(path: str, max_bytes: int | None = None) -> dict:
    """Vet an uploaded video before any decoder sees it. Returns the probe JSON; MediaRefused when it isn't plain video."""
    p = Path(path)
    if not p.is_file():
        raise MediaRefused("The video file is missing.")
    cap = max_bytes or MAX_INPUT_BYTES
    if p.stat().st_size > cap:
        raise MediaRefused(f"The video is over {cap // (1024 * 1024)} MB.")
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)], timeout=120, text=True)
    try:
        j = json.loads(r.stdout or "{}")
    except ValueError:
        j = {}
    names = set(((j.get("format") or {}).get("format_name") or "").split(","))
    if r.returncode != 0 or not j.get("streams") or not names & set(SOURCE_CONTAINERS):
        raise MediaRefused("That file isn't a plain video (MP4, MOV, MKV or WebM), so it can't be processed.")
    return j
