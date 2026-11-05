"""gzip for text answers only (HTML, JS, CSS, JSON, SVG): the Render free plan's 5 GB of outbound bandwidth ran out on
4 Nov 2026 and the service was suspended on 5 Nov ("Monthly Quota Exceeded"). Text shrinks 3-8x; media never goes through
here (video, images and fonts are already compressed, and a Range answer must keep its exact bytes).

A pure ASGI middleware, so it works outermost, behind Starlette's BaseHTTPMiddleware (which re-chunks every body):
  - only when the client sent ``Accept-Encoding: gzip``;
  - only a 200 with a compressible Content-Type, a declared Content-Length between MIN_BYTES and MAX_BYTES, and no
    Content-Encoding or Content-Range already set; anything else (a stream with no length, a 206, a 304, a HEAD) passes
    through untouched, byte for byte;
  - the body is gathered (it is at most MAX_BYTES, and the length was declared up front) and compressed once;
  - Content-Length is rewritten, ``Vary: Accept-Encoding`` added, and a strong ETag weakened (``W/``) because the gzip
    bytes are a different representation (RFC 9110 8.8.3).
"""
from __future__ import annotations

import gzip

from starlette.datastructures import Headers, MutableHeaders

MIN_BYTES = 1024
MAX_BYTES = 4 * 1024 * 1024
LEVEL = 6
TEXT_TYPES = ("text/", "application/json", "application/javascript", "application/manifest+json", "application/xml",
              "image/svg+xml", "application/problem+json")


def compressible(content_type: str) -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return ct.startswith(TEXT_TYPES) and ct != "text/event-stream"


def _q(params: str) -> float:
    for p in params.split(";"):
        k, _, v = p.strip().partition("=")
        if k.strip().lower() == "q":
            try:
                return max(0.0, min(1.0, float(v.strip())))
            except ValueError:
                return 0.0
    return 1.0


def accepts_gzip(scope) -> bool:
    """RFC 9110 12.5.3: an explicit ``gzip`` entry decides (q=0 refuses it, even with ``*`` allowed); else ``*`` decides."""
    ae = Headers(scope=scope).get("accept-encoding", "")
    gz = star = None
    for part in ae.split(","):
        name, _, params = part.strip().partition(";")
        name = name.strip().lower()
        if name == "gzip":
            gz = _q(params)
        elif name == "*":
            star = _q(params)
    if gz is not None:
        return gz > 0
    return bool(star and star > 0)


class GzipText:
    def __init__(self, app, minimum: int = MIN_BYTES, maximum: int = MAX_BYTES, level: int = LEVEL):
        self.app, self.minimum, self.maximum, self.level = app, minimum, maximum, level

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") == "HEAD" or not accepts_gzip(scope):
            await self.app(scope, receive, send)
            return
        start: dict | None = None
        mode = ""          # "" undecided, "pass" untouched, "gzip" gathering
        parts: list[bytes] = []

        async def wrapped(message):
            nonlocal start, mode
            if message["type"] == "http.response.start":
                h = Headers(raw=message["headers"])
                try:
                    n = int(h.get("content-length", "-1"))
                except ValueError:
                    n = -1
                ok = (message["status"] == 200 and compressible(h.get("content-type", "")) and "content-encoding" not in h
                      and "content-range" not in h and self.minimum <= n <= self.maximum)
                if not ok:
                    mode = "pass"
                    if compressible(h.get("content-type", "")) and "content-encoding" not in h:
                        MutableHeaders(raw=message["headers"]).add_vary_header("Accept-Encoding")
                    await send(message)
                    return
                mode, start = "gzip", message
                return
            if message["type"] != "http.response.body" or mode == "pass":
                await send(message)
                return
            parts.append(message.get("body", b""))
            if message.get("more_body", False):
                return
            body = gzip.compress(b"".join(parts), compresslevel=self.level, mtime=0)
            h = MutableHeaders(raw=start["headers"])
            h["Content-Encoding"] = "gzip"
            h["Content-Length"] = str(len(body))
            h.add_vary_header("Accept-Encoding")
            tag = h.get("etag")
            if tag and not tag.startswith("W/"):
                h["ETag"] = "W/" + tag
            await send(start)
            await send({"type": "http.response.body", "body": body, "more_body": False})

        await self.app(scope, receive, wrapped)
