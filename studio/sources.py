"""Where the agent finds stories, all free and key-less: Google News search (RSS), Hacker News (Algolia API),
the user's own followed feeds, and any link they paste. Each result is a lead; the agent verifies it on the page itself."""
from __future__ import annotations

import html
import time
from urllib.parse import quote_plus, urlparse

import httpx

from . import radar
from .drafter import FetchError

UA = {"User-Agent": "Mozilla/5.0 (HouseStyle research agent; +https://huhqueue-studio.onrender.com)"}


def google_news(query: str, http: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}+when:7d&hl=en-US&gl=US&ceid=US:en"
    try:
        feed = radar.fetch(url, http)
    except FetchError:
        return []
    out = []
    for s in feed["stories"][:limit]:
        title = s["title"]
        src = ""
        if " - " in title:
            title, src = title.rsplit(" - ", 1)
        out.append({"title": html.unescape(title).strip(), "url": s["url"], "source": src or "Google News",
                    "published": s["published"], "via": "google_news", "summary": s.get("summary", "")})
    return out


def bing_news(query: str, http: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    """Bing News RSS: links carry the publisher URL in their url= parameter, so the agent can open the real page."""
    from urllib.parse import parse_qs
    url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss&qft=interval%3d%227%22"
    try:
        feed = radar.fetch(url, http)
    except FetchError:
        return []
    out = []
    for s in feed["stories"][:limit]:
        link = s["url"]
        if "apiclick.aspx" in link:
            link = (parse_qs(urlparse(link).query).get("url") or [link])[0]
        out.append({"title": s["title"], "url": link, "source": urlparse(link).hostname.removeprefix("www.") if urlparse(link).hostname else "",
                    "published": s["published"], "via": "bing_news", "summary": s.get("summary", ""), "image": s.get("image", "")})
    return out


def hacker_news(query: str, http: httpx.Client | None = None, limit: int = 8) -> list[dict]:
    client = http or httpx.Client(timeout=10, headers=UA)
    try:
        r = client.get("https://hn.algolia.com/api/v1/search_by_date",
                       params={"query": query, "tags": "story", "hitsPerPage": limit,
                               "numericFilters": f"created_at_i>{int(time.time() - 7 * 86400)}"})
        hits = r.json().get("hits", []) if r.status_code == 200 else []
    except (httpx.HTTPError, ValueError):
        return []
    return [{"title": h.get("title") or "", "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
             "source": urlparse(h.get("url") or "https://news.ycombinator.com").hostname or "news.ycombinator.com",
             "published": float(h.get("created_at_i") or 0), "via": "hacker_news", "points": h.get("points") or 0,
             "summary": ""} for h in hits if h.get("title")]


def followed(feeds: list[dict], http: httpx.Client | None = None, limit: int = 10) -> list[dict]:
    return [{"title": s["title"], "url": s["url"], "source": s.get("source", ""), "published": s["published"],
             "via": "your_feeds", "summary": s.get("summary", ""), "image": s.get("image", "")}
            for s in radar.stories(feeds, http, limit)["stories"]]


def search(query: str, feeds: list[dict], http: httpx.Client | None = None, limit: int = 12) -> list[dict]:
    """Leads from every free source, deduplicated by title, newest first. Followed feeds are filtered by the query."""
    words = [w for w in query.lower().split() if len(w) > 1]
    leads = bing_news(query, http) + hacker_news(query, http)
    if feeds:
        leads += [s for s in followed(feeds, http, 30) if not words or any(w in s["title"].lower() for w in words)]
    now = time.time()

    def score(x):
        t = x["title"].lower()
        rel = sum(3 for w in words if w in t)
        fresh = max(0.0, 3 - (now - (x["published"] or now - 3 * 86400)) / 86400)  # up to 3 points for the last 3 days
        src = {"bing_news": 2, "your_feeds": 2, "hacker_news": 0.5}.get(x["via"], 0) + min(2, (x.get("points") or 0) / 150)
        return rel + fresh + src
    seen, out = set(), []
    for s in sorted(leads, key=score, reverse=True):
        k = "".join(ch for ch in s["title"].lower() if ch.isalnum())[:60]
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out[:limit]


def resolve_google_link(url: str, http: httpx.Client | None = None) -> str:
    """Google News RSS links are redirects; follow them to the publisher's page when they resolve."""
    if "news.google.com" not in url:
        return url
    client = http or httpx.Client(timeout=10, follow_redirects=True, headers=UA)
    try:
        r = client.get(url)
        final = str(r.url)
        return final if "news.google.com" not in final else url
    except httpx.HTTPError:
        return url
