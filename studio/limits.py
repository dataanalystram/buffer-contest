"""Request size caps and the per-IP request rate limit for anonymous and sample traffic. SECURITY_RULES 3 and 6.

* ``BodyCap`` (ASGI): every POST/PUT/PATCH body is capped by path before anything parses it. A Content-Length over the
  cap is refused at once with 413; a body without one (chunked) is counted as it streams and cut off at the cap.
* ``read_json``: the endpoints that take big bodies (uploads, the worker's results) authenticate first, then read the
  body through this with their own cap, so nothing is buffered or decoded for a caller who isn't allowed in.
* ``RateLimit``: a sliding one-minute window per client IP for requests that carry no real account (the free sample and
  anonymous callers). Real accounts are never slowed here. In memory, per process (the app runs one process).
"""
from __future__ import annotations

import json
import threading
import time

from .actions import ActionError

MB = 1024 * 1024
DEFAULT_MAX = 2 * MB                 # JSON bodies everywhere else
MEDIA_MAX = 56 * MB                  # /api/media: a 40 MB file as base64 plus the JSON around it
DEMO_MEDIA_MAX = 11 * MB             # the free sample: an 8 MB file as base64
WORKER_MAX = 240 * MB                # the video worker's clip/reel results (base64 mp4 + cover), token-checked first
FORM_MAX = 64 * 1024                 # OAuth forms


def cap_for(path: str) -> int:
    if path == "/api/media":
        return MEDIA_MAX
    if path.startswith("/api/worker/"):
        return WORKER_MAX
    if path.startswith("/oauth/"):
        return FORM_MAX
    return DEFAULT_MAX


class _TooLarge(Exception):
    pass


async def _send_413(send, cap: int) -> None:
    body = json.dumps({"error": f"Request too large (over {cap // MB or 1} MB)."}).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodyCap:
    def __init__(self, app, cap=cap_for):
        self.app, self.cap = app, cap

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        cap = self.cap(scope.get("path", ""))
        cl = dict(scope.get("headers") or []).get(b"content-length")
        try:
            if cl is not None and int(cl) > cap:
                return await _send_413(send, cap)
        except ValueError:
            return await _send_413(send, cap)
        seen, started = 0, False

        async def rcv():
            nonlocal seen
            m = await receive()
            if m.get("type") == "http.request":
                seen += len(m.get("body") or b"")
                if seen > cap:
                    raise _TooLarge()
            return m

        async def snd(m):
            nonlocal started
            if m.get("type") == "http.response.start":
                started = True
            await send(m)

        try:
            await self.app(scope, rcv, snd)
        except _TooLarge:
            if not started:
                await _send_413(send, cap)


async def read_body(request, cap: int) -> bytes:
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > cap:
                raise ActionError(f"Request too large (over {cap // MB or 1} MB).", 413)
        except ValueError:
            raise ActionError("Bad Content-Length.", 400)
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > cap:
            raise ActionError(f"Request too large (over {cap // MB or 1} MB).", 413)
    return bytes(buf)


async def read_json(request, cap: int) -> dict:
    raw = await read_body(request, cap)
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise ActionError("Send JSON.", 400)
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.", 400)
    return body


class RateLimit:
    """At most ``per_min`` requests a minute per key (and ``demo_per_min`` new sample workspaces)."""

    def __init__(self, per_min: int = 120, demo_per_min: int = 6):
        self.per_min, self.demo_per_min = per_min, demo_per_min
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._swept = time.time()

    def hit(self, key: str, limit: int) -> float:
        """Count one request; 0 when allowed, else the seconds until the window has room."""
        now = time.time()
        with self._lock:
            if now - self._swept > 120:
                self._hits = {k: [t for t in v if now - t < 60] for k, v in self._hits.items() if v and now - v[-1] < 60}
                self._swept = now
            q = [t for t in self._hits.get(key, []) if now - t < 60]
            if len(q) >= limit:
                self._hits[key] = q
                return max(1.0, 60 - (now - q[0]))
            q.append(now)
            self._hits[key] = q
            return 0.0
