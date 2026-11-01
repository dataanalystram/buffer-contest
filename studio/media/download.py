"""Download a found video to a local file, safely, then vet it before any decoder touches it.

* A direct file (og:video, <video src>, GitHub attachment, release asset) streams through ``netguard.safe_stream``
  (every redirect hop re-checked, public IPs only) with a byte cap.
* A platform post (X, YouTube, Vimeo, Loom, TikTok, Instagram, Wistia) goes through yt-dlp with ONLY that platform's
  extractor allowed, a size cap, a height cap (1080p is plenty for a 1080-wide Reel), no playlists, no post-processors
  that run commands, MP4 preferred so no remux is needed.
* Afterwards ``mediasafe.check_source`` refuses anything that is not a plain MP4/MOV/MKV/WebM.

Downloading platform media is a rights and ToS question, not just a technical one: callers must keep the credit
line and the user's "I have the right to use this" confirmation with the render (docs/MEDIA_PIPELINE.md, Risks).
"""
from __future__ import annotations

import os
from pathlib import Path

from .. import mediasafe, netguard
from .find import PLATFORMS, platform_of, watch_url

MAX_BYTES = int(os.environ.get("HQ_MEDIA_MAX_BYTES", str(300 * 1024 * 1024)))
MAX_HEIGHT = int(os.environ.get("HQ_MEDIA_MAX_HEIGHT", "1080"))


class DownloadError(RuntimeError):
    pass


def download_direct(url: str, dest: Path, max_bytes: int = MAX_BYTES, http=None) -> Path:
    with netguard.safe_stream(url, http=http, https_only=url.startswith("https://"), timeout=120,
                              headers={"User-Agent": "Mozilla/5.0 HuhQueueMediaBot/1.0"}) as r:
        if r.status_code != 200:
            raise DownloadError(f"The file answered {r.status_code}.")
        n = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise DownloadError(f"The video is over {max_bytes // (1024 * 1024)} MB.")
                fh.write(chunk)
    return dest


def download_platform(url: str, dest: Path, max_bytes: int = MAX_BYTES, max_height: int = MAX_HEIGHT) -> Path:
    plat = platform_of(url)
    if not plat:
        raise DownloadError("That link isn't on a supported video platform.")
    try:
        import yt_dlp
    except Exception as e:  # noqa: BLE001
        raise DownloadError("yt-dlp isn't installed on this worker (pip install yt-dlp).") from e
    netguard.check_url(url, https_only=True)
    from .find import _QuietLog
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "logger": _QuietLog(), "cachedir": False,
        "allowed_extractors": PLATFORMS[plat]["ie"],
        "format": f"b[ext=mp4][height<={max_height}]/bv*[ext=mp4][height<={max_height}]+ba[ext=m4a]/b[height<={max_height}]/b",
        "merge_output_format": "mp4", "max_filesize": max_bytes, "outtmpl": str(dest.with_suffix(".%(ext)s")),
        "overwrites": True, "socket_timeout": 60, "retries": 2, "postprocessors": [],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(watch_url(url), download=True)
    except Exception as e:  # noqa: BLE001
        msg = str(e).split("\n")[0]
        if "not a bot" in msg or "Sign in" in msg:
            if plat == "youtube":  # the operator's opt-in route (HQ_YT_FALLBACK=invidious); else: upload the file
                return _youtube_fallback(url, dest, max_bytes, max_height, e)
            raise DownloadError("The platform asked this server to sign in (bot check), so the video can't be fetched "
                                "from here. Use the company's own file, or upload the video.") from e
        raise DownloadError(f"Couldn't download the video: {msg[:160]}") from e
    got = Path(info.get("requested_downloads", [{}])[0].get("filepath") or "") if info else Path()
    if not got.is_file():
        cands = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_size, reverse=True)
        got = cands[0] if cands else got
    if not got.is_file():
        raise DownloadError("The download finished without a file.")
    if got != dest:
        got.replace(dest)
    return dest


def _youtube_fallback(url: str, dest: Path, max_bytes: int, max_height: int, cause: Exception) -> Path:
    """yt-dlp was bot-checked: the Invidious route when the operator turned it on (studio.media.yt_fallback), else the
    plain message to upload the file."""
    from . import yt_fallback
    from .find import youtube_id
    if not yt_fallback.enabled():
        raise DownloadError(yt_fallback.FAIL) from cause
    try:
        yt_fallback.fetch(youtube_id(url) or "", dest, max_bytes=max_bytes, max_height=min(max_height, 720))
    except yt_fallback.FallbackError as e:
        print(f"youtube fallback failed: {str(e)[:300]}")
        raise DownloadError(yt_fallback.FAIL) from e
    return dest


def download_video(candidate: dict | str, dest: str | Path, *, max_bytes: int = MAX_BYTES, http=None) -> dict:
    """candidate: a find_launch_video result (or a URL). Returns {path, bytes, probe, credit, source_url}."""
    c = {"url": candidate} if isinstance(candidate, str) else dict(candidate)
    url = c["url"]
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if platform_of(url):
        download_platform(url, dest, max_bytes=max_bytes)
    else:
        download_direct(url, dest, max_bytes=max_bytes, http=http)
    probe = mediasafe.check_source(str(dest), max_bytes=max_bytes)
    return {"path": str(dest), "bytes": dest.stat().st_size, "probe_format": (probe.get("format") or {}).get("format_name"),
            "credit": c.get("credit") or "", "source_url": url}
