"""read_url: one way to read any link the person pastes, without scraping logged-in pages.

- X / Twitter post links: no key needed. studio/xreader.py reads the ONE public post linked through oEmbed (the words),
  the public syndication endpoint react-tweet uses, and FxTwitter (photos, video); an X API bearer (the person's own,
  or X_BEARER_TOKEN) is an optional override. HuhQueue never crawls x.com or signs in to it.
- Article links: netguard.safe_get + the readability-style parser in drafter.fetch_article; when the page blocks bots or
  has no readable text, Firecrawl (FIRECRAWL_API_KEY on the server) reads it instead.
Every request goes through netguard (each redirect hop re-checked, private/metadata addresses refused, bodies capped).
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import urlencode, urlparse

import httpx

from . import drafter, netguard

X_STATUS = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status(?:es)?/(\d{5,25})", re.I)
OEMBED = "https://publish.x.com/oembed"
FIRECRAWL = "https://api.firecrawl.dev/v1/scrape"
UA = {"User-Agent": "Mozilla/5.0 (HuhQueue link reader; +https://huhqueue-studio.onrender.com)"}
MAX_MEDIA = 60 * 1024 * 1024


class ReadError(ValueError):
    pass


def x_status(url: str) -> tuple[str, str] | None:
    m = X_STATUS.match((url or "").strip())
    return (m.group(1), m.group(2)) if m else None


def _strip_tags(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<a [^>]*>(pic\.twitter\.com/\w+|https?://t\.co/\w+)</a>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return html.unescape(s).strip()


def parse_oembed(j: dict) -> dict:
    """oEmbed JSON -> {author_name, handle, text, date, url, has_media}."""
    h = j.get("html") or ""
    m = re.search(r"<p[^>]*>(.*?)</p>", h, re.S)
    text = _strip_tags(m.group(1)) if m else ""
    date = ""
    dm = re.findall(r"<a href=\"[^\"]*status/\d+[^\"]*\">([^<]+)</a>", h)
    if dm:
        date = html.unescape(dm[-1]).strip()
    handle = (j.get("author_url") or "").rstrip("/").rsplit("/", 1)[-1]
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"author_name": j.get("author_name") or handle, "handle": handle, "text": text, "date": date,
            "url": j.get("url") or "", "has_media": "pic.twitter.com" in h or "t.co/" in h,
            "truncated": text.endswith("…")}


def x_oembed(url: str, http: httpx.Client | None = None) -> dict:
    if not x_status(url):
        raise ReadError("That isn't a link to an X post.")
    q = OEMBED + "?" + urlencode({"url": url.split("?")[0], "omit_script": "1", "dnt": "true"})
    try:
        r = netguard.safe_get(q, http=http, headers=UA, limit=400_000, timeout=12)
    except (netguard.FetchError, httpx.HTTPError) as e:
        raise ReadError(f"X's public embed service didn't answer ({e.__class__.__name__}).")
    if r.status_code == 404:
        raise ReadError("X says that post doesn't exist or isn't public (deleted, protected or age-restricted).")
    if r.status_code >= 400:
        raise ReadError(f"X's public embed service answered HTTP {r.status_code}.")
    try:
        return parse_oembed(r.json())
    except ValueError:
        raise ReadError("X's embed service didn't return JSON.")


def x_media(status_id: str, bearer: str, http: httpx.Client | None = None) -> list[dict]:
    """The post's photos/videos through the official X API v2 with the person's own bearer token."""
    q = (f"https://api.x.com/2/tweets/{status_id}?" + urlencode({"expansions": "attachments.media_keys,author_id",
         "media.fields": "type,url,preview_image_url,variants,width,height,duration_ms", "tweet.fields": "text,created_at",
         "user.fields": "username,name,profile_image_url"}))
    try:
        r = netguard.safe_get(q, http=http, headers={**UA, "Authorization": f"Bearer {bearer}"}, limit=1_000_000, timeout=12)
    except (netguard.FetchError, httpx.HTTPError) as e:
        raise ReadError(f"The X API didn't answer ({e.__class__.__name__}).")
    if r.status_code in (401, 403):
        raise ReadError("The X API refused your key (HTTP %d). Check it in Sources settings." % r.status_code)
    if r.status_code == 429:
        raise ReadError("Your X API key is rate-limited right now.")
    if r.status_code >= 400:
        raise ReadError(f"The X API answered HTTP {r.status_code}.")
    j = r.json()
    out = []
    for m in (j.get("includes") or {}).get("media") or []:
        if m.get("type") in ("video", "animated_gif"):
            mp4 = [v for v in m.get("variants") or [] if v.get("content_type") == "video/mp4" and v.get("url")]
            mp4.sort(key=lambda v: -(v.get("bit_rate") or 0))
            # the highest bit rate at or under ~2.2 Mbps keeps a minute-long clip well under the download cap
            pick = next((v for v in mp4 if (v.get("bit_rate") or 0) <= 2_200_000), mp4[-1] if mp4 else None)
            if pick:
                out.append({"type": "video", "url": pick["url"], "poster": m.get("preview_image_url", ""),
                            "duration": (m.get("duration_ms") or 0) / 1000})
        elif m.get("type") == "photo" and m.get("url"):
            out.append({"type": "photo", "url": m["url"]})
    users = (j.get("includes") or {}).get("users") or []
    full = (j.get("data") or {}).get("text") or ""
    return out if not full else out + [{"type": "text", "text": full, "avatar": (users[0] if users else {}).get("profile_image_url", "")}]


def firecrawl(url: str, http: httpx.Client | None = None) -> dict:
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        raise ReadError("no Firecrawl key")
    netguard.check_url(url)  # Firecrawl fetches on its side, but we still never hand it a private address
    try:
        r = netguard.safe_post(FIRECRAWL, http=http, json={"url": url, "formats": ["markdown"], "onlyMainContent": True,
                                                            "timeout": 25000}, headers={"Authorization": f"Bearer {key}"},
                               timeout=40, allowed_hosts={"api.firecrawl.dev"}, limit=3_000_000)
    except (netguard.FetchError, httpx.HTTPError) as e:
        raise ReadError(f"Firecrawl didn't answer ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise ReadError(f"Firecrawl answered HTTP {r.status_code}.")
    d = (r.json() or {}).get("data") or {}
    md = d.get("markdown") or ""
    meta = d.get("metadata") or {}
    paras = [re.sub(r"[*_`>#\[\]]|\(http[^)]*\)", "", p).strip() for p in re.split(r"\n\s*\n", md)]
    paras = [p for p in paras if len(p) > 60][:12]
    host = (urlparse(url).hostname or "").removeprefix("www.")
    return {"url": meta.get("sourceURL") or url, "title": (meta.get("title") or meta.get("ogTitle") or host)[:300],
            "description": (meta.get("description") or meta.get("ogDescription") or "")[:600],
            "image": meta.get("ogImage") or "", "site": (meta.get("ogSiteName") or host)[:80], "paragraphs": paras, "via": "firecrawl"}


def read_article(url: str, http: httpx.Client | None = None) -> dict:
    """An article as {url, title, description, image, site, paragraphs, via}. Direct read first; Firecrawl when the page
    blocks bots or has no readable text."""
    err = ""
    try:
        art = drafter.fetch_article(url, http)
        if len(" ".join(art.get("paragraphs") or [])) >= 200 or (art.get("description") and len(art["description"]) > 80):
            return {**art, "via": "direct"}
        err = "page has little readable text"
    except drafter.FetchError as e:
        err = str(e)
        art = None
    try:
        return firecrawl(url, http)
    except ReadError as e2:
        if art:
            return {**art, "via": "direct"}
        raise ReadError(f"Couldn't read that link ({err}; Firecrawl: {e2}).")


def read_post(url: str, http: httpx.Client | None = None, x_bearer: str = "") -> dict:
    """An X post as an article-shaped dict plus 'post' (author, handle, text, date) and 'media' (photos/videos).

    No key needed: the free cascade in studio/xreader.py (oEmbed -> syndication -> FxTwitter), cached and rate-limited
    globally. A person's own X API bearer, or X_BEARER_TOKEN on the server, is an optional override for the media."""
    from . import xreader
    handle, sid = x_status(url)
    post_url = f"https://x.com/{handle}/status/{sid}"
    try:
        got = xreader.read(handle, sid, http)
    except xreader.XReadError as e:
        raise ReadError(str(e))
    post = {"author_name": got["author_name"] or handle, "handle": got["handle"] or handle, "text": got["text"], "date": got["date"],
            "url": post_url, "has_media": got["has_media"], "truncated": got["text"].endswith("…"), "via": got["layers"]}
    if got.get("quoted"):
        post["quoted"] = got["quoted"]
    media = [{"type": "video", "url": v["url"], "poster": v.get("poster", ""), "duration": v.get("duration", 0), "source_page": post_url}
             for v in got["videos"]] + [{"type": "photo", "url": u} for u in got["photos"]]
    note = ""
    bearer = x_bearer or (_app_x() and "app")
    if bearer and (x_bearer or (got["has_media"] and not media)):  # the optional official-API override
        try:
            from . import appkeys
            api = x_media(sid, x_bearer, http) if x_bearer else appkeys.x_media_shared(sid, http)
            full = next((g for g in api if g["type"] == "text"), None)
            if full and len(full["text"]) > len(post["text"]):
                post["text"] = re.sub(r"\s*https://t\.co/\w+$", "", full["text"]).strip()
                post["truncated"] = False
            api_media = [{**g, "source_page": post_url} for g in api if g["type"] != "text"]
            media = api_media or media
        except ReadError as e:
            note = "" if media else str(e)
    if got["has_media"] and not media and not note:
        note = ("This post has a photo or video that X's public embeds didn't share. Upload the clip here and I'll edit it "
                "into the Reel, or the render worker can fetch it from the post link.")
    text = post["text"]
    first = re.split(r"(?<=[.!?])\s+|\n", text, maxsplit=1)[0]
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    return {"url": post_url, "title": first[:200] or f"Post by @{handle}", "description": text[:600],
            "image": next((m["url"] for m in media if m["type"] == "photo"), next((m.get("poster", "") for m in media if m["type"] == "video"), "")),
            "site": "X", "paragraphs": paras, "via": "x_oembed" if got["layers"].get("oembed") == "ok" else "x_public",
            "post": {**post, "credit": f"X @{post['handle']}"}, "media": media, "media_note": note}


def _app_x() -> bool:
    from . import appkeys
    return bool(appkeys.x_app_bearer())


def read_url(url: str, http: httpx.Client | None = None, x_bearer: str = "") -> dict:
    url = (url or "").strip()
    if x_status(url):
        return read_post(url, http, x_bearer)
    return read_article(url, http)
