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


# Why a platform refused, in words the person can act on. Checked in order. "Sign in to confirm your age" is NOT a bot
# block (job 65, 4 Nov 2026: an age-restricted video was reported as "YouTube blocks servers").
REFUSALS = (
    (("confirm your age", "age-restricted", "inappropriate for some users"), "age",
     "This video is age-restricted on {site}, which only plays it to a signed-in account, and HuhQueue never uses accounts "
     "or cookies. Download it yourself and upload the file."),
    (("private video", "this video is private"), "private",
     "This video is private on {site}. Download it yourself (from your account) and upload the file."),
    (("members-only", "members only", "join this channel"), "members",
     "This video is for channel members only on {site}. Download it yourself and upload the file."),
    (("not available in your country", "geo restrict", "geo-restrict", "blocked it in your country"), "geo",
     "{site} doesn't offer this video where our server is. Download it yourself and upload the file."),
    (("video unavailable", "has been removed", "no longer available", "does not exist"), "gone",
     "{site} says this video is unavailable (removed, or the link is wrong). Check the link, or upload the file."),
    (("not a bot", "sign in to confirm", "too many requests", "http error 429"), "bot",
     "{site} blocked our server (a bot check), so it couldn't fetch this video. Download it yourself and upload the file."),
)
SITE = {"youtube": "YouTube", "x": "X", "vimeo": "Vimeo", "loom": "Loom", "tiktok": "TikTok", "instagram": "Instagram",
        "linkedin": "LinkedIn", "wistia": "Wistia"}


def refusal(msg: str, plat: str) -> tuple[str, str] | None:
    """(code, message) for a platform refusal yt-dlp reported, or None for any other error."""
    low = (msg or "").lower()
    for needles, code, text in REFUSALS:
        if any(n in low for n in needles):
            return code, text.format(site=SITE.get(plat, "The platform"))
    return None


class _Caps:
    """yt-dlp's match_filter: refuse a video past the duration or size caps before a byte is downloaded."""

    def __init__(self, max_secs: int, max_bytes: int):
        self.max_secs, self.max_bytes, self.why = max_secs, max_bytes, ""

    def __call__(self, info: dict, *, incomplete: bool = False):
        info = info or {}
        d = info.get("duration")
        if self.max_secs and isinstance(d, (int, float)) and d > self.max_secs:
            self.why = (f"This video is {d / 3600:.1f} h; links are taken up to {self.max_secs / 3600:g} h. "
                        "Trim it and upload the file.")
            return self.why
        size = info.get("filesize") or info.get("filesize_approx")
        if isinstance(size, (int, float)) and size > self.max_bytes:
            self.why = f"This video is over {self.max_bytes // (1024 * 1024)} MB. Export a shorter cut and upload it."
            return self.why
        return None


def download_platform(url: str, dest: Path, max_bytes: int = MAX_BYTES, max_height: int = MAX_HEIGHT,
                      max_secs: int = 0, meta: dict | None = None) -> Path:
    """yt-dlp with ONLY the platform's extractor, mp4 at or under max_height, no cookies, no accounts. Fallback chain on a
    bot block: the platform's own public media (YouTube: the operator's Invidious route; X: the post's MP4 on
    video.twimg.com), then a DownloadError that says exactly why so the person can upload the file instead.
    ``meta`` (optional) gets the title, duration and the licence the platform states."""
    plat = platform_of(url)
    if not plat:
        raise DownloadError("That link isn't on a supported video platform.")
    try:
        import yt_dlp
    except Exception as e:  # noqa: BLE001
        raise DownloadError("yt-dlp isn't installed on this worker (pip install yt-dlp).") from e
    netguard.check_url(url, https_only=True)
    from .find import _QuietLog
    caps = _Caps(max_secs, max_bytes)
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True, "logger": _QuietLog(), "cachedir": False,
        "allowed_extractors": PLATFORMS[plat]["ie"],
        "format": f"b[ext=mp4][height<={max_height}]/bv*[ext=mp4][height<={max_height}]+ba[ext=m4a]/b[height<={max_height}]/b",
        "merge_output_format": "mp4", "max_filesize": max_bytes, "outtmpl": str(dest.with_suffix(".%(ext)s")),
        "overwrites": True, "socket_timeout": 60, "retries": 2, "postprocessors": [], "match_filter": caps,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(watch_url(url), download=True)
    except Exception as e:  # noqa: BLE001
        msg = str(e).split("\n")[0]
        why = refusal(msg, plat)
        if why and why[0] == "bot":
            return _public_fallback(url, plat, dest, max_bytes, max_height, e, why[1])
        if why:
            raise DownloadError(why[1]) from e
        raise DownloadError(f"Couldn't download the video: {msg[:160]}") from e
    if caps.why:
        raise DownloadError(caps.why)
    if meta is not None and info:
        meta.update({"title": str(info.get("title") or "")[:200], "duration": info.get("duration"),
                     "licence": " ".join(str(info.get("license") or "").split())[:120]})
    got = Path(info.get("requested_downloads", [{}])[0].get("filepath") or "") if info else Path()
    if not got.is_file():
        cands = sorted(dest.parent.glob(dest.stem + ".*"), key=lambda p: p.stat().st_size, reverse=True)
        got = cands[0] if cands else got
    if not got.is_file():
        raise DownloadError(f"The download finished without a file (over {max_bytes // (1024 * 1024)} MB?). "
                            "Export a shorter cut and upload it.")
    if got != dest:
        got.replace(dest)
    return dest


def _public_fallback(url: str, plat: str, dest: Path, max_bytes: int, max_height: int, cause: Exception, why: str) -> Path:
    """yt-dlp was bot-checked. The platform's own public media next: YouTube through the operator's Invidious route
    (studio.media.yt_fallback, off unless configured), X through the post's MP4 (studio.xreader, public syndication;
    the MP4 itself through netguard)."""
    if plat == "youtube":
        return _youtube_fallback(url, dest, max_bytes, max_height, cause, why)
    if plat == "x":
        import re as _re
        m = _re.search(r"(?:x|twitter)\.com/([^/]+)/status/(\d+)", url)
        try:
            from .. import xreader
            art = xreader.read(m.group(1), m.group(2)) if m else {}
            vid = next((v.get("url") for v in (art.get("videos") or []) if str(v.get("url") or "").startswith("https://")), "")
        except Exception:  # noqa: BLE001 - the public read failed too: the clear message below
            vid = ""
        if vid:
            return download_direct(vid, dest, max_bytes=max_bytes)
    raise DownloadError(why) from cause


def _youtube_fallback(url: str, dest: Path, max_bytes: int, max_height: int, cause: Exception, why: str = "") -> Path:
    """yt-dlp was bot-checked: the Invidious route when the operator turned it on (studio.media.yt_fallback), else the
    plain message to upload the file."""
    from . import yt_fallback
    from .find import youtube_id
    if not yt_fallback.enabled():
        raise DownloadError(why or yt_fallback.FAIL) from cause
    try:
        yt_fallback.fetch(youtube_id(url) or "", dest, max_bytes=max_bytes, max_height=min(max_height, 720))
    except yt_fallback.FallbackError as e:
        print(f"youtube fallback failed: {str(e)[:300]}")
        raise DownloadError(why or yt_fallback.FAIL) from e
    return dest


def download_video(candidate: dict | str, dest: str | Path, *, max_bytes: int = MAX_BYTES, http=None, max_secs: int = 0) -> dict:
    """candidate: a find_launch_video result (or a URL). Returns {path, bytes, probe_format, credit, source_url, meta}.
    The ONE fetch path for Make a post (media_render), the weekly recap and One take / MCP video links."""
    c = {"url": candidate} if isinstance(candidate, str) else dict(candidate)
    url = c["url"]
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta: dict = {}
    if platform_of(url):
        download_platform(url, dest, max_bytes=max_bytes, max_secs=max_secs, meta=meta)
    else:
        download_direct(url, dest, max_bytes=max_bytes, http=http)
    probe = mediasafe.check_source(str(dest), max_bytes=max_bytes)
    return {"path": str(dest), "bytes": dest.stat().st_size, "probe_format": (probe.get("format") or {}).get("format_name"),
            "credit": c.get("credit") or "", "source_url": url, "meta": meta}
