"""Can Buffer read this media link? Checked right before createPost, the way Buffer's fetcher reads it.

Buffer has no upload endpoint: it downloads every asset URL itself (developers.buffer.com/guides/hosting-media). Its fetcher
asks with HEAD first (Render logs, 2 Nov 2026: HEAD /m/<token>.mp4 from AWS 3.x addresses), then reads the bytes, and a
link that fails any step comes back as "Invalid post: Video (or Image) could not be read from its URL." with no detail.

So before a post goes to Buffer, each media URL is read here the same way, from the outside:

1. HEAD, no redirects followed: must answer 200 (a 405, a 404, a redirect to a sign-in page all fail Buffer the same way),
   with the file's own Content-Type (image/* for an image asset, video/* for a video; text/html means a web page, usually
   a sign-in or preview page) and a Content-Length above zero.
2. GET bytes=0-1023: must answer 206 with Content-Range for a video (Buffer's video reader seeks), 200 or 206 for an
   image, and the first bytes must look like the file type.

The answer is one plain sentence per broken link that says what came back and what to do. A link that passed is
remembered for 10 minutes, so a post to four channels with the same six slides checks each slide once.

Security (SECURITY_RULES 1; CodeRabbit, 4 Nov 2026): the link is user-supplied and the hosted app makes both requests, so
both go through ``netguard.safe_once``: https only, no user:pass, every address the host resolves to must be public
(private, loopback, link-local/metadata, CGNAT, IPv4-mapped IPv6, ...), and the connection is pinned to the vetted IP. The
HEAD's vetted IP is reused for the GET, so a DNS answer that flips between the two can't steer the GET inside. A redirect
is reported (Buffer won't follow it either), never followed, and the range read is capped at RANGE_MAX bytes.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import httpx

from . import netguard

TIMEOUT = 20.0
OK_TTL = 600
_OK: dict[str, float] = {}

UA = "HuhQueue-media-check/1.0 (checks a media link the way Buffer reads it)"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
RANGE_MAX = 64 * 1024   # bytes read from the range GET even if the server ignores Range and sends the whole file


def kind_of(url: str) -> str:
    return "video" if urlparse(url).path.lower().endswith(VIDEO_EXT) else "image"


def _magic_ok(kind: str, head: bytes) -> bool:
    if not head:
        return False
    if kind == "video":
        return head[4:8] == b"ftyp" or head[:4] == b"\x1a\x45\xdf\xa3"
    return (head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:6] in (b"GIF87a", b"GIF89a")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"))


def _short(url: str) -> str:
    p = urlparse(url)
    path = p.path if len(p.path) <= 48 else p.path[:22] + "…" + p.path[-22:]
    return f"{p.netloc}{path}"


def _public_url(url: str) -> bool:
    try:
        netguard.check_url(url, https_only=True)
        return True
    except netguard.FetchError:
        return False


def check_one(url: str, http: httpx.Client, kind: str | None = None) -> str | None:
    """None when Buffer can read the link; otherwise what came back and what to do, in one sentence."""
    kind = kind or kind_of(url)
    word = "video" if kind == "video" else "image"
    where = _short(url)
    if not url.startswith("https://"):
        return f"The {word} link {where} is not https://. Buffer only downloads public https links."
    hit = _OK.get(url)
    if hit and hit > time.time():
        return None
    h = {"User-Agent": UA}
    try:
        r, ip = netguard.safe_once(url, method="HEAD", http=http, headers=h, timeout=TIMEOUT, https_only=True)
    except netguard.FetchError as e:
        return f"The {word} link {where} can't be used: {e} Buffer needs a public https link it can download."
    except httpx.TimeoutException:
        return (f"The {word} link {where} did not answer within {int(TIMEOUT)} s, so Buffer can't read it either. "
                "If it is on a free host that sleeps, open it once and try again, or upload the file to the Library.")
    except httpx.HTTPError as e:
        return f"The {word} link {where} could not be reached ({type(e).__name__}). Buffer needs a public link it can download."
    if r.status_code in (301, 302, 303, 307, 308):
        to = r.headers.get("location", "")
        shown = to.startswith("https://") and _public_url(to)   # never echo an internal redirect target
        return (f"The {word} link {where} redirects to {_short(to) if shown else 'another page'} instead of "
                "serving the file. Buffer needs a direct link to the file (not a share, preview or sign-in page). "
                "Upload the file to the Library (create_upload) and use its link.")
    if r.status_code in (405, 501):
        return (f"The {word} link {where} refuses HEAD requests ({r.status_code}). Buffer checks every file with HEAD first, "
                "so it reports \"could not be read\". Host the file somewhere that answers HEAD, or upload it to the Library.")
    if r.status_code in (401, 403):
        return (f"The {word} link {where} needs a sign-in ({r.status_code}). Buffer can't sign in: use a public link, or upload "
                "the file to the Library.")
    if r.status_code in (404, 410):
        return f"The {word} link {where} is gone ({r.status_code}). Render or upload the {word} again."
    if r.status_code >= 400:
        return f"The {word} link {where} answered {r.status_code}, so Buffer can't read it. Try again in a minute, or upload the file."
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype in ("text/html", "application/xhtml+xml"):
        return (f"The {word} link {where} returns a web page (probably a sign-in or preview page), not a {word}. "
                "Buffer needs the file itself: upload it to the Library (create_upload) and use that link.")
    if not ctype.startswith(kind + "/"):
        return (f"The {word} link {where} is served as \"{ctype or 'no Content-Type'}\", not {kind}/..., so Buffer won't "
                f"read it as a {word}.")
    try:
        size = int(r.headers.get("content-length") or "0")
    except ValueError:
        size = 0
    if size <= 0:
        return (f"The {word} link {where} doesn't say how big the file is (no Content-Length), which Buffer's reader "
                "needs. Upload the file to the Library and use that link.")
    try:
        g, _ = netguard.safe_once(url, method="GET", http=http, headers={**h, "Range": "bytes=0-1023"}, timeout=TIMEOUT,
                                  https_only=True, ip=ip, limit=RANGE_MAX, truncate=True)
    except netguard.FetchError as e:
        return f"The {word} link {where} can't be read: {e}"
    except httpx.HTTPError as e:
        return f"The {word} link {where} answered HEAD but the download failed ({type(e).__name__})."
    if kind == "video" and g.status_code != 206:
        return (f"The {word} link {where} doesn't support byte ranges (answered {g.status_code} to a Range request), "
                "which Buffer's video reader needs. Upload the file to the Library and use that link.")
    if g.status_code not in (200, 206):
        return f"The {word} link {where} answered {g.status_code} when read, so Buffer can't read it."
    if not _magic_ok(kind, g.content[:32]):
        return f"The {word} link {where} doesn't contain a {word} file (its first bytes aren't a known {word} format)."
    _OK[url] = time.time() + OK_TTL
    return None


def check(urls: list[str], http: httpx.Client) -> list[str]:
    """Every problem across the links (in order, de-duplicated), checking up to 6 at a time."""
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(urls))) as ex:
        res = list(ex.map(lambda u: check_one(u, http), urls))
    return [r for r in res if r]


def message(problems: list[str]) -> str:
    n = len(problems)
    head = "Buffer would refuse this post: " if n == 1 else f"Buffer would refuse this post ({n} media links it can't read): "
    return head + " ".join(problems[:3]) + (f" (+{n - 3} more like this)" if n > 3 else "")


def reset() -> None:
    _OK.clear()
