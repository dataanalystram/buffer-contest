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


def search(query: str, feeds: list[dict], http: httpx.Client | None = None, limit: int = 12, store=None, uid: str = "") -> list[dict]:
    """Leads from every free source, deduplicated by title, newest first. Followed feeds are filtered by the query. With a
    store, the cached source library joins in (the workspace's own on/off choices hold; no AI, no wait for a fetch)."""
    words = [w for w in query.lower().split() if len(w) > 1]
    leads = bing_news(query, http) + hacker_news(query, http)
    if store is not None:  # the cached source library (RSS/HTTP, no AI); a stale cache is re-read in the background
        try:
            leads += library_leads(store, query, library_off(store, uid))
            warm(store, http)
        except Exception as e:  # noqa: BLE001 - the library is a bonus here; the other sources still answer
            print(f"sources: library leads skipped ({e.__class__.__name__})", flush=True)
    if feeds:
        leads += [s for s in followed(feeds, http, 30) if not words or any(w in s["title"].lower() for w in words)]
    now = time.time()

    def score(x):
        t = x["title"].lower()
        rel = sum(3 for w in words if w in t)
        fresh = max(0.0, 3 - (now - (x["published"] or now - 3 * 86400)) / 86400)  # up to 3 points for the last 3 days
        src = {"bing_news": 2, "your_feeds": 2, "source_library": 2, "hacker_news": 0.5}.get(x["via"], 0) \
            + min(1.5, len(x.get("also") or []) * 0.5) - (2 if x.get("type") == "paper" else 0) + min(2, (x.get("points") or 0) / 150)
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
    from . import netguard
    try:  # hop by hop, each host checked (SECURITY_RULES 1); only the final address is kept, nothing is read
        with netguard.safe_stream(url, http=http, headers=UA, timeout=10) as r:
            final = str(r.url)
        return final if "news.google.com" not in final else url
    except (httpx.HTTPError, netguard.FetchError):
        return url


# ================================================================================================ the source library
# Free, key-less sources grouped by category, read over plain RSS/HTTP (Firecrawl only as a cached fallback for a page that
# needs JavaScript). One shared copy per source lives in the database with a TTL, so many people (and the research step)
# cause one fetch, a failing source never breaks the others, and the signed-out sample reads the same copy without fetching.
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

CATEGORIES = ("Launches", "AI labs", "Tech news", "Research", "Videos", "Open source")
TYPE_LABEL = {"article": "Article", "launch": "Launch", "paper": "Research paper — not a product", "video": "Video",
              "repo": "Repo", "discussion": "Discussion"}
LIB_TTL = 900               # a source is re-read at most every 15 minutes by scans and the research step
REFRESH_FLOOR = 120         # a Refresh re-reads only sources whose copy is older than 2 minutes (many people, one fetch)
REFRESH_EVERY = 60          # one Refresh per workspace per minute
FIRECRAWL_TTL = 6 * 3600    # a page read through Firecrawl is kept 6 hours (credits are not spent per click)
FRESH_HOURS = 48
SLOW_MS = 4000
PER_SOURCE = 20
FIRECRAWL = "https://api.firecrawl.dev/v1/scrape"
LIB_UA = {"User-Agent": "Mozilla/5.0 (HuhQueue news radar; +https://huhqueue-studio.onrender.com)"}
YT = "https://www.youtube.com/feeds/videos.xml?channel_id="
VIDEO_REUSE = "permission unknown"
VIDEO_NOTE = "Someone else's video: embed it or link to it. HuhQueue never downloads it."


def _src(sid, label, cat, kind, url, typ="article", **kw) -> dict:
    return {"id": sid, "label": label, "category": cat, "kind": kind, "url": url, "type": typ, **kw}


LIBRARY = [
    _src("yc_launches", "YC Launches", "Launches", "yc", "https://www.ycombinator.com/launches", "launch"),
    _src("producthunt", "Product Hunt", "Launches", "rss", "https://www.producthunt.com/feed", "launch"),
    _src("launch_hn", "Launch HN", "Launches", "hnrss", "https://hnrss.org/launches", "launch"),
    _src("show_hn", "Show HN", "Launches", "hnrss", "https://hnrss.org/show", "launch"),
    _src("openai", "OpenAI News", "AI labs", "rss", "https://openai.com/news/rss.xml"),
    _src("anthropic", "Anthropic News", "AI labs", "anthropic", "https://www.anthropic.com/news"),
    _src("deepmind", "Google DeepMind", "AI labs", "rss", "https://deepmind.google/blog/rss.xml"),
    _src("google_ai", "Google AI blog", "AI labs", "rss", "https://blog.google/technology/ai/rss/"),
    _src("nvidia_blog", "NVIDIA Blog", "AI labs", "rss", "https://blogs.nvidia.com/feed/"),
    _src("microsoft_ai", "Microsoft AI Blog", "AI labs", "rss", "https://www.microsoft.com/en-us/ai/blog/feed/"),
    _src("meta", "Meta Newsroom", "AI labs", "rss", "https://about.fb.com/feed/"),
    _src("techcrunch", "TechCrunch", "Tech news", "rss", "https://techcrunch.com/feed/"),
    _src("techcrunch_ai", "TechCrunch AI", "Tech news", "rss", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    _src("verge", "The Verge", "Tech news", "rss", "https://www.theverge.com/rss/index.xml"),
    _src("ars", "Ars Technica", "Tech news", "rss", "https://feeds.arstechnica.com/arstechnica/index"),
    _src("mit_tr", "MIT Technology Review", "Tech news", "rss", "https://www.technologyreview.com/feed/"),
    _src("mit_tr_ai", "MIT Technology Review AI", "Tech news", "rss", "https://www.technologyreview.com/topic/artificial-intelligence/feed/"),
    _src("ieee_robotics", "IEEE Spectrum Robotics", "Tech news", "rss", "https://spectrum.ieee.org/feeds/topic/robotics.rss"),
    _src("ieee_ai", "IEEE Spectrum AI", "Tech news", "rss", "https://spectrum.ieee.org/feeds/topic/artificial-intelligence.rss"),
    _src("wired_ai", "WIRED AI", "Tech news", "rss", "https://www.wired.com/feed/tag/ai/latest/rss"),
    _src("engadget", "Engadget", "Tech news", "rss", "https://www.engadget.com/rss.xml"),
    _src("apple", "Apple Newsroom", "Tech news", "rss", "https://www.apple.com/newsroom/rss-feed.rss"),
    _src("hn_front", "Hacker News", "Tech news", "hnrss", "https://hnrss.org/frontpage", "discussion", aggregator=True),
    _src("google_news", "Google News", "Tech news", "gnews",
         "https://news.google.com/rss/search?q=%22artificial+intelligence%22+when:2d&hl=en-US&gl=US&ceid=US:en", aggregator=True),
    _src("arxiv_ai", "arXiv cs.AI", "Research", "rss", "https://rss.arxiv.org/rss/cs.AI", "paper"),
    _src("arxiv_cv", "arXiv cs.CV", "Research", "rss", "https://rss.arxiv.org/rss/cs.CV", "paper"),
    _src("yt_yc", "Y Combinator (YouTube)", "Videos", "youtube", YT + "UCcefcZRL2oaA_uBNeo5UOWg", "video"),
    _src("yt_openai", "OpenAI (YouTube)", "Videos", "youtube", YT + "UCXZCJLdBC09xxGZ6gcdrc6A", "video"),
    _src("yt_deepmind", "Google DeepMind (YouTube)", "Videos", "youtube", YT + "UCP7jMXSY2xbc3KCAE0MHQ-A", "video"),
    _src("yt_nvidia", "NVIDIA (YouTube)", "Videos", "youtube", YT + "UCHuiy8bXnmK5nisYHUd1J5g", "video"),
    _src("yt_anthropic", "Anthropic (YouTube)", "Videos", "youtube", YT + "UCrDwWp7EBBv4NwvScIpBDOA", "video"),
    _src("yt_figure", "Figure (YouTube)", "Videos", "youtube", YT + "UCYlq-KmwPjc1DtsGmthFqSQ", "video"),
    _src("yt_bostondynamics", "Boston Dynamics (YouTube)", "Videos", "youtube", YT + "UC7vVhkEfw4nOGp8TyDk7RcQ", "video"),
    _src("hackertube", "HackerTube", "Videos", "hackertube", "https://hackertube.org/api/list?area=ai-data&sort=recent&n=20", "video",
         evergreen=True, about="The YouTube videos HN keeps telling each other to watch"),
    _src("github_trending", "GitHub Trending", "Open source", "gh_trending", "https://github.com/trending", "repo"),
    _src("github_blog", "GitHub Blog", "Open source", "rss", "https://github.blog/feed/"),
    _src("hf_blog", "Hugging Face Blog", "Open source", "rss", "https://huggingface.co/blog/feed.xml"),
]
BY_ID = {s["id"]: s for s in LIBRARY}
_lock = threading.Lock()  # one refresh at a time per process; a second waits and then finds the copies fresh

LIB_SCHEMA = """
CREATE TABLE IF NOT EXISTS source_cache (source_id TEXT PRIMARY KEY, fetched REAL, ok INTEGER, ms INTEGER, error TEXT, via TEXT, items TEXT);
CREATE TABLE IF NOT EXISTS source_refresh (user_id TEXT PRIMARY KEY, at REAL)
"""


def ensure_library(store) -> None:
    for stmt in [x.strip() for x in LIB_SCHEMA.split(";") if x.strip()]:
        store.x(stmt.replace(" REAL", " DOUBLE PRECISION") if store.pg else stmt)
    if store.pg:
        for t in ("source_cache", "source_refresh"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


# ---------------------------------------------------------------- parsers (bytes in, items out: every one has a fixture test)
def _clean(text: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def lib_item(src: dict, title: str, url: str, published: float = 0.0, summary: str = "", image: str = "", **extra) -> dict:
    it = {"id": hashlib.sha256(url.encode()).hexdigest()[:12], "title": _clean(title)[:220], "url": url, "source_id": src["id"],
          "source": src["label"], "category": src["category"], "type": src["type"], "type_label": TYPE_LABEL[src["type"]],
          "published": float(published or 0), "summary": _clean(summary)[:240], "image": image or "", "engagement": 0}
    it.update(extra)
    return it


def video_fields(video_id: str) -> dict:
    """The official watch URL, the embed and YouTube's own thumbnail. Reuse stays unknown: link or embed only."""
    return {"image": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "video": {"url": f"https://www.youtube.com/watch?v={video_id}", "embed": f"https://www.youtube-nocookie.com/embed/{video_id}",
                      "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg", "reuse": VIDEO_REUSE, "note": VIDEO_NOTE}}


def _yt_id(url: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})", url or "")
    return m.group(1) if m else ""


def _parse_rss(src: dict, body: bytes) -> list[dict]:
    out = []
    for s in radar.parse(body, src["url"])["stories"][:PER_SOURCE]:
        title, summary, extra = s["title"], s.get("summary", ""), {}
        if src["kind"] == "hnrss":  # hnrss descriptions are "Article URL / Comments URL / Points"; keep the points only
            m = re.search(r"Points:\s*(\d+)", summary)
            extra["engagement"] = int(m.group(1)) if m else 0
            c = re.search(r"Comments URL:\s*(https://news\.ycombinator\.com/item\?id=\d+)", summary)
            if c:
                extra["discussion"] = c.group(1)
            summary = ""
        elif src["kind"] == "gnews" and " - " in title:
            title, pub = title.rsplit(" - ", 1)
            extra["publisher"] = pub.strip()[:60]
            summary = ""
        image = s.get("image", "")
        if src["kind"] == "youtube":
            vid = s.get("video_id") or _yt_id(s["url"])
            if not vid:
                continue
            extra.update(video_fields(vid))
            image = extra.pop("image")
            url = extra["video"]["url"]
        else:
            url = s["url"]
        out.append(lib_item(src, title, url, s.get("published") or 0.0, summary, image, **extra))
    return out


def _parse_yc(src: dict, body: bytes) -> list[dict]:
    from .sources_agent import parse_yc
    try:
        data = json.loads(body)
    except ValueError:
        raise FetchError("YC Launches didn't return JSON.")
    return [lib_item(src, it["title"], it["url"], it["published"], it["summary"], it.get("image", ""),
                     engagement=it.get("engagement", 0), company=it.get("company", "")) for it in parse_yc(data)[:PER_SOURCE]]


def _parse_gh_trending(src: dict, body: bytes, now: float) -> list[dict]:
    """github.com/trending, a light parse: owner/repo, the description, today's stars, the language. Trending is about
    today, so each repo is dated at the fetch."""
    h = body.decode("utf-8", "replace")
    out = []
    for row in re.findall(r'<article class="Box-row">(.*?)</article>', h, re.S)[:PER_SOURCE]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="/([\w.-]+/[\w.-]+)"', row)
        if not m:
            continue
        desc = re.search(r'<p class="col-9[^"]*"[^>]*>(.*?)</p>', row, re.S)
        stars = re.search(r"([\d,]+)\s+stars today", row)
        lang = re.search(r'itemprop="programmingLanguage">([^<]+)<', row)
        out.append(lib_item(src, m.group(1), f"https://github.com/{m.group(1)}", now, desc.group(1) if desc else "",
                            f"https://opengraph.githubassets.com/1/{m.group(1)}",
                            engagement=int(stars.group(1).replace(",", "")) if stars else 0, language=(lang.group(1).strip() if lang else "")))
    return out


def _parse_hackertube(src: dict, body: bytes) -> list[dict]:
    try:
        rows = json.loads(body)
    except ValueError:
        raise FetchError("HackerTube didn't return JSON.")
    out = []
    for d in rows if isinstance(rows, list) else []:
        vid = str(d.get("vid") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", vid) or not d.get("title"):
            continue
        f = video_fields(vid)
        out.append(lib_item(src, d["title"], f["video"]["url"], 0.0, d.get("why_hn") or d.get("summary") or "", f["image"],
                            video=f["video"], channel=str(d.get("channel") or "")[:80], evergreen=True,
                            engagement=int((d.get("hn") or {}).get("mentions") or 0)))
        if len(out) >= PER_SOURCE:
            break
    return out


def _date(text: str) -> float:
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return 0.0


def _parse_anthropic(src: dict, body: bytes) -> list[dict]:
    """anthropic.com/news has no feed; its server-rendered tiles are <time>date</time> then a link with a heading."""
    h = body.decode("utf-8", "replace")
    out, seen = [], set()
    for m in re.finditer(r'<a[^>]+href="(/(?:news|research)/[a-z0-9][a-z0-9-]*)"[^>]*>(.*?)</a>', h, re.S):
        path, inner = m.group(1), m.group(2)
        head = re.search(r"<h\d[^>]*>(.*?)</h\d>", inner, re.S)
        title = _clean(head.group(1) if head else inner)
        if path in seen or len(title) < 20:  # category links ("Announcements") are short and have no heading
            continue
        seen.add(path)
        body_p = re.search(r"<p[^>]*>(.*?)</p>", inner, re.S)
        when = re.findall(r"<time[^>]*>([^<]+)</time>", h[max(0, m.start() - 1500):m.start()] + inner)
        out.append(lib_item(src, title, "https://www.anthropic.com" + path, _date(when[-1]) if when else 0.0,
                            body_p.group(1) if body_p else ""))
        if len(out) >= PER_SOURCE:
            break
    return out


def _parse_anthropic_md(src: dict, md: str) -> list[dict]:
    """The same page as Firecrawl markdown: [### Title ... date ...](https://www.anthropic.com/news/slug)."""
    out, seen = [], set()
    for m in re.finditer(r"\[([^\]]{8,800})\]\((https://www\.anthropic\.com/(?:news|research)/[a-z0-9][a-z0-9-]*)\)", md):
        lines = [x.strip(" #*_") for x in m.group(1).splitlines() if x.strip(" #*_")]
        when = next((_date(x) for x in lines if _date(x)), 0.0)
        lines = [x for x in lines if not _date(x)]
        if not lines or len(lines[0]) < 20 or m.group(2) in seen:
            continue
        seen.add(m.group(2))
        out.append(lib_item(src, lines[0], m.group(2), when, " ".join(lines[1:2])))
    return out[:PER_SOURCE]


def parse_source(src: dict, body: bytes, now: float | None = None) -> list[dict]:
    kind = src["kind"]
    if kind in ("rss", "hnrss", "youtube", "gnews"):
        return _parse_rss(src, body)
    if kind == "yc":
        return _parse_yc(src, body)
    if kind == "gh_trending":
        return _parse_gh_trending(src, body, now or time.time())
    if kind == "hackertube":
        return _parse_hackertube(src, body)
    if kind == "anthropic":
        return _parse_anthropic(src, body)
    raise FetchError(f"Unknown source kind {kind}.")


# ---------------------------------------------------------------- fetching (every request through netguard, SECURITY_RULES 1)
def _http_get(url: str, http, accept: str = "") -> bytes:
    from . import netguard
    try:
        r = netguard.safe_get(url, http=http, headers={**LIB_UA, **({"Accept": accept} if accept else {})}, limit=radar.MAX_BYTES,
                              timeout=10, https_only=True)
    except netguard.FetchError as e:
        raise FetchError(str(e))
    except httpx.HTTPError as e:
        raise FetchError(f"didn't answer ({e.__class__.__name__})")
    if r.status_code >= 400:
        raise FetchError(f"answered HTTP {r.status_code}")
    return r.content


def _firecrawl_md(url: str, http) -> str:
    from . import netguard
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        raise FetchError("no Firecrawl key on the server")
    try:
        r = netguard.safe_post(FIRECRAWL, http=http, json={"url": url, "formats": ["markdown"], "onlyMainContent": False},
                               headers={"Authorization": f"Bearer {key}"}, timeout=40, allowed_hosts={"api.firecrawl.dev"}, limit=3_000_000)
        if r.status_code >= 400:
            raise FetchError(f"Firecrawl answered HTTP {r.status_code}")
        return str(((r.json() or {}).get("data") or {}).get("markdown") or "")
    except (netguard.FetchError, httpx.HTTPError, ValueError) as e:
        raise FetchError(f"Firecrawl failed ({e.__class__.__name__})")


def fetch_source(src: dict, http=None, now: float | None = None) -> tuple[list[dict], str]:
    """(items, via) for one source. Plain HTTP first; a JS-only page (no items in the HTML) falls back to Firecrawl."""
    accept = "application/json" if src["kind"] in ("yc", "hackertube") else ""
    try:
        items = parse_source(src, _http_get(src["url"], http, accept), now)
    except FetchError:
        if src["kind"] != "anthropic":
            raise
        items = []
    if items or src["kind"] != "anthropic":
        return items, "http"
    return _parse_anthropic_md(src, _firecrawl_md(src["url"], http)), "firecrawl"


# ---------------------------------------------------------------- the shared cache
def _rows(store) -> dict[str, dict]:
    return {r["source_id"]: dict(r) for r in store.q("SELECT source_id, fetched, ok, ms, error, via, items FROM source_cache")}


def _stale(row: dict | None, floor: float, now: float) -> bool:
    if not row:
        return True
    ttl = FIRECRAWL_TTL if row.get("via") == "firecrawl" and row.get("ok") else floor
    return now - float(row["fetched"] or 0) >= ttl


def refresh_library(store, ids: list[str], http=None, floor: float = LIB_TTL, now: float | None = None,
                    fetch=None) -> dict[str, dict]:
    """Re-read the stale sources among ids in parallel and store each result on its own. A source that fails keeps its
    last good items (marked failing), so one bad feed never empties the panel or stops the others."""
    fetch = fetch or fetch_source
    with _lock:
        now = now or time.time()
        rows = _rows(store)
        todo = [BY_ID[i] for i in ids if i in BY_ID and _stale(rows.get(i), floor, now)]

        def one(src):
            t0 = time.time()
            try:
                items, via = fetch(src, http, now)
                return src, items, via, "", int((time.time() - t0) * 1000)
            except FetchError as e:
                return src, None, "", str(e)[:200], int((time.time() - t0) * 1000)
            except Exception as e:  # noqa: BLE001 - an odd answer from one source is that source's failure only
                return src, None, "", f"sent something unreadable ({e.__class__.__name__})", int((time.time() - t0) * 1000)
        if todo:
            with ThreadPoolExecutor(min(8, len(todo))) as ex:
                results = list(ex.map(one, todo))
            for src, items, via, err, ms in results:
                prev = rows.get(src["id"]) or {}
                keep = items if items is not None else json.loads(prev.get("items") or "[]")
                store.x("INSERT INTO source_cache(source_id,fetched,ok,ms,error,via,items) VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET "
                        "fetched=excluded.fetched, ok=excluded.ok, ms=excluded.ms, error=excluded.error, via=excluded.via, items=excluded.items",
                        (src["id"], now, 0 if items is None else 1, ms, err, via or prev.get("via") or "", json.dumps(keep)))
                if items is None:
                    print(f"sources: {src['id']} failed", flush=True)  # the source id only; never a key or a response body
        return _rows(store)


def _similar(a: set, b: set) -> bool:
    shared = a & b
    return len(shared) >= 3 and len(shared) / max(1, min(len(a), len(b))) >= 0.6


def dedupe_library(items: list[dict]) -> list[dict]:
    """One card per story: the same canonical link, or a near-identical title from another source. The first source
    to have it leads (originals before aggregators); the others become `also` (the cross-check signal)."""
    from .sources_agent import canonical, tokens
    order = sorted(items, key=lambda it: (bool(BY_ID.get(it["source_id"], {}).get("aggregator")), -it["published"]))
    out, by_url = [], {}
    for it in order:
        key, tk = canonical(it["url"]), tokens(it["title"])
        hit = by_url.get(key) or next((o for o in out if o["source_id"] != it["source_id"] and _similar(tk, o["_tk"])), None)
        if hit:
            if it["source"] != hit["source"] and it["source"] not in hit["also"]:
                hit["also"].append(it["source"])
            hit["engagement"] = max(hit["engagement"], it.get("engagement") or 0)
            continue
        cur = dict(it, also=[], _tk=tk)
        by_url[key] = cur
        out.append(cur)
    for o in out:
        o.pop("_tk", None)
        o["also_n"] = len(o["also"])
    return out


def status_of(row: dict | None) -> str:
    if not row:
        return "unchecked"
    if not row["ok"]:
        return "failing"
    return "slow" if (row["ms"] or 0) > SLOW_MS else "ok"


def library_view(store, off: set | None = None, now: float | None = None, fresh_hours: float = FRESH_HOURS,
                 category: str = "") -> dict:
    """The panel: every source with its status dot and last check, and the fresh items from the sources that are on,
    deduplicated across sources, newest first."""
    off, now = set(off or ()), now or time.time()
    rows = _rows(store)
    cutoff = now - fresh_hours * 3600
    sources, items = [], []
    for s in LIBRARY:
        row = rows.get(s["id"])
        got = json.loads(row["items"]) if row and row.get("items") else []
        fresh = [it for it in got if it.get("evergreen") or it.get("published", 0) >= cutoff]
        sources.append({"id": s["id"], "label": s["label"], "category": s["category"], "type": s["type"],
                        "type_label": TYPE_LABEL[s["type"]], "url": s["url"], "enabled": s["id"] not in off,
                        "status": status_of(row), "last_checked": float(row["fetched"]) if row else None,
                        "ms": row["ms"] if row else None, "error": (row or {}).get("error") or "", "via": (row or {}).get("via") or "",
                        "items": len(fresh), **({"about": s["about"]} if s.get("about") else {})})
        if s["id"] not in off:
            items += fresh
    items = dedupe_library(items)
    items.sort(key=lambda it: (bool(it.get("evergreen")), -it["published"]))  # evergreen picks after the news
    counts = {c: sum(1 for it in items if it["category"] == c) for c in CATEGORIES}
    live = len({it["source_id"] for it in items})
    if category:
        items = [it for it in items if it["category"] == category]
    checked = [float(r["fetched"]) for r in rows.values() if r.get("fetched")]
    return {"categories": [{"name": c, "items": counts[c]} for c in CATEGORIES], "sources": sources, "items": items,
            "updated": max(checked) if checked else None, "fresh_hours": fresh_hours,
            "counts": {"sources": len(LIBRARY), "on": len(LIBRARY) - len(off & set(BY_ID)),
                       "with_items": live, "items": len(items)}}


def library_leads(store, query: str, off: set | None = None, limit: int = 10) -> list[dict]:
    """Research leads from the cached library (no fetch here): items whose title shares the query's words, as the same
    lead shape the other sources return. Research papers keep their label so they are never presented as products."""
    words = [w for w in (query or "").lower().split() if len(w) > 2]
    if not words:
        return []
    view = library_view(store, off)
    out = []
    for it in view["items"]:
        t = it["title"].lower()
        hits = sum(1 for w in words if w in t)
        if hits:
            out.append({"title": it["title"], "url": it["url"], "source": it["source"], "published": it["published"],
                        "via": "source_library", "summary": it["summary"], "image": it.get("image", ""),
                        "points": it.get("engagement", 0), "type": it["type"], "type_label": it["type_label"],
                        "also": it["also"], "_hits": hits})
    out.sort(key=lambda x: (-x["_hits"], -len(x["also"]), -x["published"]))
    for x in out:
        x.pop("_hits", None)
    return out[:limit]


def warm(store, http=None) -> None:
    """Re-read stale library sources on a background thread (the research step never waits for it)."""
    rows = _rows(store)
    if not rows or _lock.locked() or not any(_stale(rows.get(s["id"]), LIB_TTL, time.time()) for s in LIBRARY):
        return  # nobody has used the library yet (or a refresh is running): nothing to keep warm
    threading.Thread(target=lambda: _quiet(refresh_library, store, list(BY_ID), http), daemon=True).start()


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception as e:  # noqa: BLE001 - a background warm-up never raises into a request
        print(f"sources: warm-up failed ({e.__class__.__name__})", flush=True)


def library_off(store, uid: str) -> set:
    """The sources this workspace turned off (kept with its other source settings)."""
    if not uid:
        return set()
    rows = store.q("SELECT data FROM source_settings WHERE user_id=?", (uid,))
    try:
        return set((json.loads(rows[0]["data"]) if rows and rows[0]["data"] else {}).get("lib_off") or [])
    except ValueError:
        return set()
