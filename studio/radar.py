"""News radar: follow RSS/Atom feeds and see the newest stories, ready to turn into posts.

Feeds are fetched with the same public-host guard as article links, capped in size, and cached per feed for 10 minutes so
opening the home screen never hammers a site. Stories carry no Buffer calls; writing posts from one goes through
draft_from_link like any other link.
"""
from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import httpx

from .drafter import FetchError, _public

TTL = 600
MAX_FEEDS = 12
MAX_BYTES = 2_000_000
TAG = re.compile(r"<[^>]+>")
_cache: dict[str, tuple[float, dict]] = {}  # ponytail: in-process cache, per worker; fine for one Render instance


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(el, *names) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _when(raw: str) -> float:
    if not raw:
        return 0.0
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def parse(body: bytes, feed_url: str) -> dict:
    """RSS 2.0, RSS 1.0 and Atom → {title, stories:[{title, url, summary, published, image}]}."""
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FetchError("That address isn't an RSS or Atom feed.")
    channel = next((c for c in root if _local(c.tag) == "channel"), root)
    title = _text(channel, "title") or urlparse(feed_url).hostname or feed_url
    stories = []
    for item in root.iter():
        if _local(item.tag) not in ("item", "entry"):
            continue
        url = _text(item, "link")
        image = ""
        for child in item:
            name = _local(child.tag)
            if name == "link" and child.get("href") and child.get("rel", "alternate") == "alternate":
                url = url or child.get("href")
            if name in ("content", "thumbnail", "enclosure") and (child.get("url") or "").startswith("https://") \
                    and "image" in (child.get("type") or child.get("medium") or "image"):
                image = image or child.get("url")
        if not url.startswith(("http://", "https://")):
            continue
        summary = html.unescape(TAG.sub(" ", _text(item, "description", "summary", "content")))
        stories.append({"title": html.unescape(_text(item, "title") or url)[:300], "url": url,
                        "summary": " ".join(summary.split())[:280],
                        "published": _when(_text(item, "pubDate", "published", "updated", "date")), "image": image})
    return {"title": html.unescape(title)[:120], "stories": stories[:20]}


def fetch(feed_url: str, http: httpx.Client | None = None, check_host: bool = True) -> dict:
    hit = _cache.get(feed_url)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    p = urlparse(feed_url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Paste a feed address that starts with http:// or https://")
    if check_host and not _public(p.hostname):
        raise FetchError("That feed points to a private address, so it can't be read.")
    client = http or httpx.Client(timeout=10, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0 (HuhQueue news radar)"})
    try:
        r = client.get(feed_url)
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't open that feed ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise FetchError(f"The feed answered HTTP {r.status_code}.")
    feed = parse(r.content[:MAX_BYTES], feed_url)
    _cache[feed_url] = (time.time(), feed)
    return feed


def stories(feeds: list[dict], http: httpx.Client | None = None, limit: int = 15) -> dict:
    """Newest stories across the user's feeds, plus which feeds failed and why."""
    out, errors = [], []
    for f in feeds:
        try:
            feed = fetch(f["url"], http)
        except FetchError as e:
            errors.append({"feed": f["url"], "error": str(e)})
            continue
        out += [dict(s, source=feed["title"], feed_id=f["id"]) for s in feed["stories"]]
    out.sort(key=lambda s: s["published"], reverse=True)
    return {"stories": out[:limit], "errors": errors}
