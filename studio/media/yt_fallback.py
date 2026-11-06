"""YouTube link -> MP4 when yt-dlp is bot-blocked (datacenter IPs and GitHub runners get "Sign in to confirm you're not
a bot"). Off unless the operator turns it on: ``HQ_YT_FALLBACK=invidious`` with ``HQ_INVIDIOUS_INSTANCES`` (comma-separated
https base URLs of Invidious instances that allow proxied downloads, ``local=true``). The flag lives on the app and
travels to the GitHub worker inside each job spec (``config()`` / ``apply()``); nothing in it is secret.

What was tried for the Buffer Commit kickoff video (1 Nov 2026, docs/MCP_TOOLS.md "How the demo video was fetched"):
yt-dlp web/tv/ios/mweb with the bgutil PO-token provider (still bot-checked: the block is the IP, not the token), cobalt
public instances (all need a Turnstile JWT now), the ytultra.com backend (its free answer is googlevideo URLs locked to its
own server IP; the merged 720p is a paid feature), Piped (blocked like yt-dlp) and Invidious (one instance still proxies
itag 136 + 140 with ``local=true``). Only the last route works, so it is the only one implemented.

Every request goes through netguard (public hosts only, each redirect hop re-checked); the two streams are size-capped
while they download, muxed with FFmpeg (stream copy, no re-encode) and vetted by mediasafe like any other source.
Rights still apply: the caller keeps the credit line and the person's "I have the right to use this".
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .. import netguard

FAIL = "Couldn't fetch this video from here (YouTube blocks servers). Upload the file instead."
CHUNK = 4 * 1024 * 1024
VIDEO_ITAGS = {1080: ["137"], 720: ["136"], 480: ["135"], 360: ["134"]}  # H.264 MP4 video-only, best first
AUDIO_ITAGS = ["140", "139"]  # AAC
UA = {"User-Agent": "Mozilla/5.0 HuhQueueMediaBot/1.0"}


class FallbackError(RuntimeError):
    pass


class TooLong(FallbackError):
    """The video is over the caller's duration cap (read from the instance's metadata before any stream downloads)."""


PROBE_BYTES = 64 * 1024  # same as download.PROBE_BYTES


def instances() -> list[str]:
    raw = os.environ.get("HQ_INVIDIOUS_INSTANCES", "")
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip().startswith("https://")]


def enabled() -> bool:
    return os.environ.get("HQ_YT_FALLBACK", "").strip().lower() == "invidious" and bool(instances())


def config() -> dict | None:
    """What the app hands the worker inside a job spec."""
    return {"mode": "invidious", "instances": instances()} if enabled() else None


def apply(cfg: dict | None) -> None:
    """The worker side: turn the route on for this job only when the app said so (https instances only)."""
    if not isinstance(cfg, dict) or cfg.get("mode") != "invidious":
        return
    inst = [str(u).strip().rstrip("/") for u in cfg.get("instances") or [] if str(u).strip().startswith("https://")][:5]
    if inst:
        os.environ["HQ_YT_FALLBACK"] = "invidious"
        os.environ["HQ_INVIDIOUS_INSTANCES"] = ",".join(inst)


def pick_formats(info: dict, max_height: int = 720) -> tuple[dict | None, dict | None]:
    """The best H.264 video-only stream at or under max_height and the best AAC audio from /api/v1/videos (pure)."""
    by_itag: dict[str, dict] = {}
    fmts = [f for f in info.get("adaptiveFormats") or [] if f.get("url")]
    # a video with auto-dubbed audio lists one itag-140 per language; xtags=acont=original marks the speaker's own voice
    fmts.sort(key=lambda f: 0 if "acont%3Doriginal" in f["url"] or "acont=original" in f["url"] else
              (2 if "acont%3Ddubbed" in f["url"] or "acont=dubbed" in f["url"] else 1))
    for f in fmts:
        by_itag.setdefault(str(f.get("itag")), f)
    video = None
    for h in sorted((h for h in VIDEO_ITAGS if h <= max_height), reverse=True):
        video = next((by_itag[i] for i in VIDEO_ITAGS[h] if i in by_itag), None)
        if video:
            break
    audio = next((by_itag[i] for i in AUDIO_ITAGS if i in by_itag), None)
    return video, audio


def _get_json(url: str, http=None) -> dict:
    r = netguard.safe_get(url, http=http, https_only=True, timeout=20, limit=4_000_000, headers=UA)
    if r.status_code != 200:
        raise FallbackError(f"{url.split('/api/')[0]} answered {r.status_code}")
    return r.json()


def probe(url: str, http=None) -> dict:
    """The fallback route's answer for a probe: the instance lists streams and the first 64 KB of the video stream read."""
    from .find import youtube_id
    vid = youtube_id(url) or ""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
        return {"ok": False, "via": "youtube_fallback", "reason": "not a YouTube video id"}
    errors = []
    for base in instances():
        try:
            info = _get_json(f"{base}/api/v1/videos/{vid}?local=true", http)
            v, a = pick_formats(info, 720)
            if not v or not a:
                raise FallbackError("no H.264 + AAC streams")
            vurl = base + v["url"] if v["url"].startswith("/") else v["url"]
            with netguard.safe_stream(vurl, http=http, https_only=True, timeout=20, headers={**UA, "Range": "bytes=0-65535"}) as r:
                if r.status_code not in (200, 206):
                    raise FallbackError(f"stream answered {r.status_code}")
                n = 0
                for c in r.iter_bytes(16 * 1024):  # a server may ignore Range and answer 200 with the whole video
                    n += len(c)
                    if n >= PROBE_BYTES:
                        break
                n = min(n, PROBE_BYTES)
            return {"ok": True, "via": "youtube_fallback", "bytes_read": n, "title": str(info.get("title") or "")[:200],
                    "duration": info.get("lengthSeconds")}
        except Exception as e:  # noqa: BLE001
            errors.append(str(e)[:80])
    return {"ok": False, "via": "youtube_fallback", "reason": "YouTube blocked the worker and the fallback route failed ("
            + "; ".join(errors)[:160] + ")", "code": "bot"}


def _download(url: str, size: int, dest: Path, max_bytes: int, http=None) -> None:
    """Ranged GETs (4 MB each) so a slow proxy can't hold one long stream; the byte cap is checked before any write."""
    if size <= 0 or size > max_bytes:
        raise FallbackError(f"stream is {size} bytes (cap {max_bytes})")
    with open(dest, "wb") as fh:
        for a in range(0, size, CHUNK):
            b = min(size, a + CHUNK) - 1
            with netguard.safe_stream(url, http=http, https_only=True, timeout=60, headers={**UA, "Range": f"bytes={a}-{b}"}) as r:
                if r.status_code not in (200, 206):
                    raise FallbackError(f"stream answered {r.status_code}")
                n = 0
                for chunk in r.iter_bytes(1 << 20):
                    n += len(chunk)
                    if n > b - a + 1:
                        raise FallbackError("the proxy sent more than it was asked for")
                    fh.write(chunk)


def fetch(video_id: str, dest: str | Path, *, max_bytes: int = 300 * 1024 * 1024, max_height: int = 720, http=None,
          max_secs: int = 0) -> dict:
    """Download a YouTube video through the configured Invidious instances. Returns {path, instance, itags}."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id or ""):
        raise FallbackError("not a YouTube video id")
    if not enabled():
        raise FallbackError("the YouTube fallback is off (HQ_YT_FALLBACK)")
    dest = Path(dest)
    errors = []
    for base in instances():
        try:
            info = _get_json(f"{base}/api/v1/videos/{video_id}?local=true", http)
            secs = info.get("lengthSeconds")
            if max_secs and isinstance(secs, (int, float)) and secs > max_secs:
                raise TooLong(f"This video is {secs / 3600:.1f} h; links are taken up to {max_secs / 3600:g} h. Trim it and upload the file.")
            v, a = pick_formats(info, max_height)
            if not v or not a:
                raise FallbackError("no H.264 + AAC streams")
            vurl, aurl = (base + u if u.startswith("/") else u for u in (v["url"], a["url"]))
            with tempfile.TemporaryDirectory() as td:
                vp, ap = Path(td) / "v.mp4", Path(td) / "a.m4a"
                _download(vurl, int(v.get("clen") or 0), vp, max_bytes, http)
                _download(aurl, int(a.get("clen") or 0), ap, max_bytes, http)
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(vp), "-i", str(ap), "-c", "copy", "-movflags", "+faststart",
                                str(dest)], check=True, timeout=600)
            return {"path": str(dest), "instance": base, "itags": [str(v.get("itag")), str(a.get("itag"))]}
        except TooLong:
            raise
        except (FallbackError, netguard.FetchError, subprocess.SubprocessError, OSError, ValueError) as e:
            errors.append(f"{base}: {str(e)[:120]}")
        except Exception as e:  # noqa: BLE001 - httpx errors and the like: try the next instance
            errors.append(f"{base}: {e.__class__.__name__}")
    raise FallbackError("; ".join(errors) or "no instance configured")
