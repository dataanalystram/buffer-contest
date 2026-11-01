"""Make a post › From a link: a preview of what the agent will read from a pasted link (owner, 1 Nov 2026).

The same readers the agent's read_url tool uses (studio/reader.py: the free X cascade for posts, a direct read with the
Firecrawl fallback for pages), so the preview is what the agent gets. Every fetch goes through netguard (SECURITY_RULES 1);
per-account rate limit shared with thumbnails; the free sample answers from the URL alone and never fetches (rule 3).
REST: POST /api/link/preview {url}; MCP / agent: preview_link (docs/AGENT_PARITY.md).
"""
from __future__ import annotations

from urllib.parse import urlparse

from . import intake, netguard, reader, thumbs
from .actions import ActionError

CACHE: dict[str, tuple[float, dict]] = {}
TTL = 900


def _host(u: str) -> str:
    return (urlparse(u).hostname or "").removeprefix("www.")


def preview(a, url: str, http=None, demo: bool = False) -> dict:
    import time
    url = (url or "").strip()
    if not url or len(url) > 2000:
        raise ActionError("Paste a link (https://…).")
    try:
        url = netguard.check_url(intake.clean_url(url))
    except netguard.FetchError as e:
        raise ActionError(f"That link isn't allowed: {e}")
    kind = "x" if reader.x_status(url) else "page"
    if demo:  # the free sample never makes this server fetch a page
        return {"url": url, "site": "X" if kind == "x" else _host(url), "title": _host(url), "description": "", "image": "",
                "kind": kind, "media": [], "sample": True}
    hit = CACHE.get(url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    if not thumbs.allow(getattr(a, "uid", "")):
        raise ActionError("Too many previews at once. Try again in a minute.", 429)
    try:
        art = reader.read_url(url, http)
    except reader.ReadError as e:
        raise ActionError(str(e)[:200], 422)
    post = art.get("post") or {}
    out = {"url": art.get("url") or url, "site": (art.get("site") or _host(url))[:80], "title": (art.get("title") or "")[:200],
           "description": (art.get("description") or "")[:400], "image": art.get("image") or "", "kind": kind,
           "author": (f"{post.get('author_name')} (@{post.get('handle')})" if post else "")[:120],
           "media": [m.get("type") for m in art.get("media") or [] if m.get("type")][:4], "via": art.get("via", "")}
    if len(CACHE) > 300:
        CACHE.clear()
    CACHE[url] = (time.time(), out)
    return out
