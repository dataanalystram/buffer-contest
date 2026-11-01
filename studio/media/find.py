"""find_launch_video: given a link (or just a story), find the REAL launch / demo video on any platform.

Cascade (docs/MEDIA_PIPELINE.md has the sources table with licence, rate limits and ToS for each step):

  1. The link IS a video post (X, YouTube, Vimeo, Loom, TikTok, Instagram, Wistia) -> that post, via the platform's
     public oEmbed for title/author (documented, no key) and yt-dlp metadata (no download) for duration/size.
  2. A Launch YC page -> the launch's JSON (same URL, Accept: application/json): video links in the body, the share
     image, then the company's own site (step 4) with the company name as the expected uploader.
  3. A Hacker News item -> the HN Algolia API item -> its URL (one hop, then steps 1/2/4).
     A GitHub repo -> README (REST API) video links + latest release video assets.
  4. Any page (company blog, press kit, Product Hunt page, LinkedIn post) -> og:video / og:video:secure_url,
     twitter:player(:stream), JSON-LD VideoObject, <video>/<source>, embedded YouTube/Vimeo/Loom/Wistia iframes,
     links to single YouTube/Vimeo/Loom/X posts.
  5. No video yet -> optional search: YouTube Data API search.list (the operator's key; its own bucket of 100
     calls/day) or, only when the operator opts in (HQ_MEDIA_YTSEARCH=1), yt-dlp "ytsearch" (scrapes YouTube: ToS risk).
  6. Nothing playable -> images (og:image, YC share image) so the caller can make a slideshow or generate one.

Every page fetch goes through ``netguard.safe_get`` (no auto redirects, every hop re-checked, byte cap; SECURITY_RULES 1).
yt-dlp is only pointed at an allow-listed platform host with ONLY that platform's extractor enabled (never the generic
extractor), so a user URL can't make it fetch an arbitrary page. Returns plain JSON dicts (agent-core stores them).
"""
from __future__ import annotations

import html
import json
import os
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urljoin, urlparse

import httpx

from .. import netguard

UA = "Mozilla/5.0 (compatible; HuhQueueMediaBot/1.0; +https://huhqueue-studio.onrender.com)"
PAGE_LIMIT = 3_000_000

# platform -> hosts and the ONLY yt-dlp extractor allowed for it (yt_dlp/extractor IE keys)
PLATFORMS: dict[str, dict] = {
    "x": {"hosts": ("x.com", "twitter.com", "mobile.twitter.com"), "ie": ["Twitter"]},
    "youtube": {"hosts": ("youtube.com", "youtu.be", "m.youtube.com", "youtube-nocookie.com"), "ie": ["Youtube"]},
    "vimeo": {"hosts": ("vimeo.com", "player.vimeo.com"), "ie": ["Vimeo"]},
    "loom": {"hosts": ("loom.com",), "ie": ["Loom"]},
    "tiktok": {"hosts": ("tiktok.com",), "ie": ["TikTok"]},
    "instagram": {"hosts": ("instagram.com",), "ie": ["Instagram"]},
    "linkedin": {"hosts": ("linkedin.com",), "ie": ["LinkedIn"]},
    "wistia": {"hosts": ("wistia.com", "wistia.net", "wi.st"), "ie": ["Wistia"]},
}
OEMBED = {
    "x": "https://publish.x.com/oembed?omit_script=1&dnt=true&url={u}",
    "youtube": "https://www.youtube.com/oembed?format=json&url={u}",
    "vimeo": "https://vimeo.com/api/oembed.json?url={u}",
    "loom": "https://www.loom.com/v1/oembed?url={u}",
    "tiktok": "https://www.tiktok.com/oembed?url={u}",
}
# how much we trust each way of finding a video (higher = more likely THE launch video); used for ranking
CONF = {"direct_post": 0.97, "yc_launch_body": 0.93, "og_video": 0.9, "jsonld_video": 0.88, "twitter_player": 0.85,
        "github_readme": 0.85, "github_release": 0.8, "video_tag": 0.8, "iframe": 0.82, "page_link": 0.6,
        "youtube_api_search": 0.5, "ytsearch": 0.45, "og_image": 0.3, "yc_share_image": 0.35}
VIDEO_EXT = (".mp4", ".mov", ".webm", ".m4v")
URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.I)


class FindError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- url helpers
def host_of(url: str) -> str:
    h = (urlparse(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def platform_of(url: str) -> str | None:
    h = host_of(url)
    for name, p in PLATFORMS.items():
        if any(h == s or h.endswith("." + s) for s in p["hosts"]):
            return name
    return None


def youtube_id(url: str) -> str | None:
    p = urlparse(url)
    h = host_of(url)
    if h == "youtu.be":
        return p.path.strip("/").split("/")[0] or None
    if "youtube" in h:
        if p.path == "/watch":
            return (parse_qs(p.query).get("v") or [None])[0]
        m = re.match(r"/(?:embed|shorts|live|v)/([A-Za-z0-9_-]{6,})", p.path)
        if m:
            return m.group(1)
    return None


def canonical(url: str) -> str:
    """One key per video, so the same video found twice (youtu.be vs embed vs watch) is listed once."""
    yid = youtube_id(url)
    if yid:
        return f"youtube:{yid}"
    m = re.search(r"(?:x|twitter)\.com/[^/]+/status/(\d+)", url)
    if m:
        return f"x:{m.group(1)}"
    m = re.search(r"vimeo\.com/(?:video/)?(\d+)", url)
    if m:
        return f"vimeo:{m.group(1)}"
    m = re.search(r"loom\.com/(?:share|embed)/([a-f0-9]{16,})", url)
    if m:
        return f"loom:{m.group(1)}"
    return url.split("#")[0].rstrip("/")


def is_video_post(url: str) -> bool:
    """True for a URL that points at ONE video post on a known platform (not a channel or a profile)."""
    plat = platform_of(url)
    path = urlparse(url).path
    if plat == "youtube":
        return youtube_id(url) is not None
    if plat == "x":
        return bool(re.search(r"/status/\d+", path))
    if plat == "vimeo":
        return bool(re.search(r"^/(?:video/)?\d+", path))
    if plat == "loom":
        return bool(re.search(r"/(?:share|embed)/[a-f0-9]{16,}", path))
    if plat == "tiktok":
        return "/video/" in path
    if plat == "instagram":
        return bool(re.match(r"/(?:p|reel|reels|tv)/", path))
    if plat == "linkedin":
        return "/posts/" in path or "/feed/update/" in path
    if plat == "wistia":
        return "/medias/" in path
    return False


def watch_url(url: str) -> str:
    yid = youtube_id(url)
    if yid:
        return f"https://www.youtube.com/watch?v={yid}"
    m = re.search(r"player\.vimeo\.com/video/(\d+)", url)
    if m:
        return f"https://vimeo.com/{m.group(1)}"
    m = re.search(r"loom\.com/embed/([a-f0-9]+)", url)
    if m:
        return f"https://www.loom.com/share/{m.group(1)}"
    return url


def urls_in(text: str) -> list[str]:
    out = []
    for u in URL_RE.findall(text or ""):
        u = html.unescape(u).rstrip(".,;:!?>*_")
        if u not in out:
            out.append(u)
    return out


# ---------------------------------------------------------------------------------------------- page scanning
class _Scan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, list[str]] = {}
        self.videos: list[str] = []
        self.iframes: list[str] = []
        self.links: list[str] = []
        self.jsonld: list[str] = []
        self._ld = False
        self._buf: list[str] = []
        self.title = ""
        self._t = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, []).append(a["content"].strip())
        elif tag in ("video", "source") and (a.get("src") or a.get("data-src")):
            self.videos.append(a.get("src") or a.get("data-src"))
        elif tag == "iframe" and (a.get("src") or a.get("data-src")):
            self.iframes.append(a.get("src") or a.get("data-src"))
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "script" and "ld+json" in a.get("type", ""):
            self._ld, self._buf = True, []
        elif tag == "title":
            self._t = True

    def handle_endtag(self, tag):
        if tag == "script" and self._ld:
            self.jsonld.append("".join(self._buf))
            self._ld = False
        elif tag == "title":
            self._t = False

    def handle_data(self, data):
        if self._ld:
            self._buf.append(data)
        elif self._t:
            self.title += data


def _ld_videos(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    out, stack = [], [data]
    while stack:
        x = stack.pop()
        if isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, dict):
            t = x.get("@type")
            if t == "VideoObject" or (isinstance(t, list) and "VideoObject" in t):
                out.append(x)
            stack.extend(v for v in x.values() if isinstance(v, (dict, list)))
    return out


def scan_page(page_html: str, base: str) -> dict:
    """Pure: every video/image reference a page makes, in priority order. Tests feed it saved HTML."""
    s = _Scan()
    try:
        s.feed(page_html or "")
    except Exception:  # noqa: BLE001 - broken HTML still yields what was parsed
        pass
    m = s.meta
    found: list[dict] = []

    def absu(u: str) -> str:
        return urljoin(base, html.unescape(u.strip()))

    def add(u: str, how: str, **kw):
        if not u or u.strip().startswith(("data:", "blob:", "javascript:")):
            return
        u = absu(u)
        if urlparse(u).scheme in ("http", "https"):
            found.append({"url": u, "how": how, **kw})

    for k in ("og:video:secure_url", "og:video:url", "og:video"):
        for u in m.get(k, []):
            add(u, "og_video", mime=(m.get("og:video:type") or [""])[0])
    for u in m.get("twitter:player:stream", []):
        add(u, "og_video")
    for u in m.get("twitter:player", []):
        add(u, "twitter_player")
    for raw in s.jsonld:
        for v in _ld_videos(raw):
            for key in ("contentUrl", "embedUrl"):
                if isinstance(v.get(key), str):
                    add(v[key], "jsonld_video", title=v.get("name") or "")
    for u in s.videos:
        add(u, "video_tag")
    for u in s.iframes:
        if platform_of(absu(u)):
            add(u, "iframe")
    for u in s.links:
        au = absu(u)
        if is_video_post(au) or urlparse(au).path.lower().endswith(VIDEO_EXT):
            add(au, "page_link")
    images: list[str] = []
    for k in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
        for u in m.get(k, []):
            u2 = absu(u)
            if u2 not in images:
                images.append(u2)
    title = (m.get("og:title") or [s.title.strip()])[0]
    site = (m.get("og:site_name") or [host_of(base)])[0]
    return {"videos": found, "images": images, "title": html.unescape(title or ""), "site": site,
            "description": html.unescape((m.get("og:description") or m.get("description") or [""])[0])}


# ---------------------------------------------------------------------------------------------- fetchers
def _get(url: str, http: httpx.Client | None, *, accept: str = "text/html,*/*;q=0.8", limit: int = PAGE_LIMIT,
         headers: dict | None = None) -> httpx.Response:
    h = {"User-Agent": UA, "Accept": accept, **(headers or {})}
    return netguard.safe_get(url, http=http, headers=h, limit=limit, timeout=15)


def _json(url: str, http, **kw) -> Any:
    r = _get(url, http, accept="application/json", **kw)
    if r.status_code != 200:
        raise FindError(f"{host_of(url)} answered {r.status_code}")
    return r.json()


def oembed(url: str, http=None) -> dict | None:
    plat = platform_of(url)
    tpl = OEMBED.get(plat or "")
    if not tpl:
        return None
    try:
        j = _json(tpl.format(u=quote(watch_url(url), safe="")), http, limit=400_000)
    except Exception:  # noqa: BLE001
        return None
    raw = j.get("html") or ""
    text = ""
    if plat == "x" and raw:
        text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))).strip()
    return {"title": j.get("title") or text[:200], "author": j.get("author_name") or "", "author_url": j.get("author_url") or "",
            "thumbnail": j.get("thumbnail_url") or "", "provider": j.get("provider_name") or plat, "text": text,
            "has_media_link": "pic.x.com" in raw or "pic.twitter.com" in raw}


class _QuietLog:
    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


def ytdlp_meta(url: str, timeout: float = 40) -> dict | None:
    """Metadata only (download=False) with ONLY the platform's extractor. None when yt-dlp isn't installed."""
    plat = platform_of(url)
    if not plat:
        return None
    try:
        import yt_dlp  # Unlicense; optional dependency (requirements-media.txt)
    except Exception:  # noqa: BLE001
        return None
    netguard.check_url(url, https_only=True)
    opts = {"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True, "socket_timeout": timeout,
            "allowed_extractors": PLATFORMS[plat]["ie"], "cachedir": False, "logger": _QuietLog()}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(watch_url(url), download=False)
    except Exception as e:  # noqa: BLE001 - any extractor error just means "no metadata"
        return {"error": str(e).split("\n")[0][:200]}
    if not info:
        return None
    if info.get("_type") == "playlist" and info.get("entries"):
        info = next((e for e in info["entries"] if e), info)
    fmts = [f for f in info.get("formats") or [] if f.get("vcodec") not in (None, "none")]
    best = max(fmts, key=lambda f: (f.get("height") or 0), default={})
    return {"title": info.get("title") or "", "uploader": info.get("uploader") or info.get("channel") or "",
            "duration": info.get("duration"), "width": best.get("width") or info.get("width"),
            "height": best.get("height") or info.get("height"), "thumbnail": info.get("thumbnail") or "",
            "description": (info.get("description") or "")[:600], "license": info.get("license") or "",
            "webpage_url": info.get("webpage_url") or url, "timestamp": info.get("timestamp"), "has_video": bool(fmts)}


# ---------------------------------------------------------------------------------------------- candidates
DOWNLOADABLE = ("x", "youtube", "vimeo", "loom", "tiktok", "instagram", "direct", "wistia")


def _cand(url: str, how: str, *, source_page: str = "", kind: str | None = None, **kw) -> dict:
    plat = platform_of(url) or ("direct" if urlparse(url).path.lower().endswith(VIDEO_EXT) else "web")
    if kind is None:
        kind = "video" if (plat != "web" or how in ("og_video", "jsonld_video", "video_tag")) else "image"
    if plat == "web" and kind == "video":
        plat = "direct"
    c = {"url": watch_url(url) if plat not in ("direct", "web") else url, "platform": plat, "kind": kind, "how": how,
         "confidence": CONF.get(how, 0.5), "source_page": source_page, "title": "", "uploader": "", "duration": None,
         "width": None, "height": None, "thumbnail": "", "downloadable": kind == "video" and plat in DOWNLOADABLE,
         "credit": "", "official": False, "key": canonical(url)}
    c.update({k: v for k, v in kw.items() if v not in (None, "")})
    return c


NAMES = {"x": "X", "youtube": "YouTube", "vimeo": "Vimeo", "loom": "Loom", "tiktok": "TikTok", "instagram": "Instagram",
         "linkedin": "LinkedIn", "wistia": "Wistia"}


def credit_line(c: dict) -> str:
    who = c.get("uploader") or ""
    where = NAMES.get(c["platform"]) or host_of(c.get("source_page") or c["url"])
    return f"{'Video' if c['kind'] == 'video' else 'Image'}: {who + ' on ' if who else ''}{where}"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _mark_official(c: dict, company: str, domain: str) -> None:
    co = _norm(company)
    up = _norm(c.get("uploader", "")) + _norm(c.get("author_url", ""))
    if co and len(co) >= 3 and (co[:12] in up or co.removeprefix("the")[:12] in up):
        c["official"] = True
    if domain and host_of(c.get("source_page") or "").endswith(domain):
        c["official"] = True
    if c["official"]:
        c["confidence"] = round(min(0.99, c["confidence"] + 0.04), 3)


def enrich(c: dict, http=None, deep: bool = True) -> dict:
    """oEmbed (cheap, documented) then yt-dlp metadata (duration, size). Never downloads."""
    if c["kind"] != "video" or c["platform"] in ("direct", "web"):
        return c
    o = oembed(c["url"], http)
    if o:
        c.update({"title": c.get("title") or o["title"], "uploader": c.get("uploader") or o["author"],
                  "thumbnail": c.get("thumbnail") or o["thumbnail"], "author_url": o["author_url"]})
        if c["platform"] == "x":
            c["text"] = o["text"]
    if deep and os.environ.get("HQ_MEDIA_YTDLP", "1") != "0":
        m = ytdlp_meta(c["url"])
        if m and not m.get("error"):
            c.update({"title": c.get("title") or m["title"], "uploader": c.get("uploader") or m["uploader"],
                      "duration": m["duration"], "width": m["width"], "height": m["height"],
                      "thumbnail": c.get("thumbnail") or m["thumbnail"]})
            if m.get("license"):
                c["license"] = m["license"]
            if not m.get("has_video"):
                c["kind"], c["downloadable"], c["confidence"] = "image", False, 0.2
        elif m and m.get("error"):
            c["note"] = m["error"]
            if "No video" in m["error"]:  # e.g. a text-only X post: oEmbed works, there is just no video
                c["kind"], c["downloadable"], c["confidence"] = "post", False, 0.2
    return c


# ---------------------------------------------------------------------------------------------- source handlers
def from_yc_launch(url: str, http=None) -> tuple[list[dict], dict]:
    j = _json(url, http)
    company = j.get("company") or {}
    ctx = {"company": company.get("name") or "", "site": company.get("url") or "", "title": j.get("title") or "",
           "tagline": j.get("tagline") or "", "logo": company.get("logo") or ""}
    out = []
    for u in urls_in(j.get("body") or ""):
        if is_video_post(u) or urlparse(u).path.lower().endswith(VIDEO_EXT):
            out.append(_cand(u, "yc_launch_body", source_page=url))
    if j.get("share_image_url"):
        out.append(_cand(urljoin(url, j["share_image_url"]), "yc_share_image", source_page=url, kind="image"))
    return out, ctx


def from_hn(url: str, http=None) -> tuple[str | None, dict]:
    iid = (parse_qs(urlparse(url).query).get("id") or [""])[0]
    if not iid.isdigit():
        return None, {}
    j = _json(f"https://hn.algolia.com/api/v1/items/{iid}", http, limit=2_000_000)
    return j.get("url"), {"title": j.get("title") or ""}


def from_github(url: str, http=None) -> tuple[list[dict], dict]:
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2:
        return [], {}
    owner, repo = parts[0], parts[1].removesuffix(".git")
    out = []
    try:
        r = _get(f"https://api.github.com/repos/{owner}/{repo}/readme", http, accept="application/vnd.github.raw+json",
                 limit=1_000_000)
        md = r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        md = ""
    for u in urls_in(md) + re.findall(r'src="(https://[^"]+)"', md):
        if is_video_post(u) or urlparse(u).path.lower().endswith(VIDEO_EXT) or "github.com/user-attachments/assets/" in u:
            out.append(_cand(u, "github_readme", source_page=url, kind="video"))
    try:
        rel = _json(f"https://api.github.com/repos/{owner}/{repo}/releases/latest", http, limit=1_000_000)
        for a in rel.get("assets") or []:
            if str(a.get("name", "")).lower().endswith(VIDEO_EXT):
                out.append(_cand(a["browser_download_url"], "github_release", source_page=url, kind="video"))
    except Exception:  # noqa: BLE001
        pass
    return out, {"company": repo, "site": url}


def from_page(url: str, http=None) -> tuple[list[dict], dict]:
    r = _get(url, http)
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "text/html"):
        return [], {"note": f"{host_of(url)} answered {r.status_code}"}
    sc = scan_page(r.text, str(r.url) or url)
    out = [_cand(v["url"], v["how"], source_page=url, title=v.get("title") or "") for v in sc["videos"]]
    out += [_cand(u, "og_image", source_page=url, kind="image") for u in sc["images"][:2]]
    return out, {"title": sc["title"], "site_name": sc["site"], "description": sc["description"]}


def youtube_api_search(q: str, key: str, http=None, n: int = 5) -> list[dict]:
    """YouTube Data API v3 search.list (its own quota bucket: 100 calls/day by default)."""
    u = ("https://www.googleapis.com/youtube/v3/search?part=snippet&type=video&maxResults=%d&q=%s&key=%s"
         % (n, quote(q), quote(key)))
    r = netguard.safe_get(u, http=http, headers={"Accept": "application/json"}, limit=500_000, timeout=15)
    if r.status_code != 200:
        raise FindError(f"YouTube search answered {r.status_code}")  # never echo the URL: it carries the key
    out = []
    for it in r.json().get("items", []):
        vid = (it.get("id") or {}).get("videoId")
        sn = it.get("snippet") or {}
        if vid:
            out.append(_cand(f"https://www.youtube.com/watch?v={vid}", "youtube_api_search", title=sn.get("title", ""),
                             uploader=sn.get("channelTitle", ""),
                             thumbnail=((sn.get("thumbnails") or {}).get("high") or {}).get("url", "")))
    return out


def ytsearch(q: str, n: int = 5) -> list[dict]:
    try:
        import yt_dlp
    except Exception:  # noqa: BLE001
        return []
    with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "extract_flat": True, "logger": _QuietLog(),
                           "allowed_extractors": ["YoutubeSearch", "Youtube"]}) as y:
        info = y.extract_info(f"ytsearch{n}:{q}", download=False) or {}
    return [_cand(f"https://www.youtube.com/watch?v={e['id']}", "ytsearch", title=e.get("title") or "",
                  uploader=e.get("channel") or e.get("uploader") or "", duration=e.get("duration"))
            for e in info.get("entries") or [] if e and e.get("id")]


# ---------------------------------------------------------------------------------------------- the cascade
def find_launch_video(query: str, *, http: httpx.Client | None = None, max_results: int = 5, deep: bool = True,
                      youtube_api_key: str | None = None, allow_ytsearch: bool | None = None,
                      log: Callable[[str], None] | None = None) -> list[dict]:
    """Ranked candidates (best first) for a link or a story. Each is a JSON dict:
    {url, platform, kind: video|image|post, how, confidence 0-1, source_page, title, uploader, duration, width,
     height, thumbnail, downloadable, official, credit, key[, company, note, text]}.
    Videos come before images. Empty list when nothing was found (the caller generates images instead)."""
    log = log or (lambda m: None)
    youtube_api_key = youtube_api_key or os.environ.get("YOUTUBE_API_KEY") or None
    if allow_ytsearch is None:
        allow_ytsearch = os.environ.get("HQ_MEDIA_YTSEARCH") == "1"
    links = urls_in(query)
    cands: list[dict] = []
    ctx: dict = {}
    seen: set[str] = set()

    def visit(url: str, depth: int = 0):
        if depth > 2 or url in seen or len(seen) > 6:
            return
        seen.add(url)
        try:
            netguard.check_url(url)
        except Exception as e:  # noqa: BLE001
            log(f"skip {host_of(url)}: {e}")
            return
        h, path = host_of(url), urlparse(url).path
        try:
            if is_video_post(url):
                cands.append(_cand(url, "direct_post", source_page=url))
            elif h == "ycombinator.com" and path.startswith("/launches/"):
                got, c2 = from_yc_launch(url, http)
                cands.extend(got)
                ctx.update({k: v for k, v in c2.items() if v})
                if c2.get("site"):
                    visit(c2["site"], depth + 1)
            elif h == "news.ycombinator.com" and path == "/item":
                nxt, c2 = from_hn(url, http)
                ctx.update(c2)
                if nxt:
                    visit(nxt, depth + 1)
            elif h == "github.com":
                got, c2 = from_github(url, http)
                cands.extend(got)
                for k, v in c2.items():
                    ctx.setdefault(k, v)
            else:
                got, c2 = from_page(url, http)
                cands.extend(got)
                for k, v in c2.items():
                    ctx.setdefault(k, v)
        except Exception as e:  # noqa: BLE001 - one source failing never stops the cascade
            log(f"{h}: {e.__class__.__name__}: {str(e)[:120]}")

    for u in links[:2]:
        visit(u)

    def rank(cs):
        return sorted(cs, key=lambda c: ({"video": 0, "image": 1}.get(c["kind"], 2), -c["confidence"]))

    best: dict[str, dict] = {}
    for c in cands:  # dedupe: keep the most trusted way we found each video
        if c["key"] not in best or c["confidence"] > best[c["key"]]["confidence"]:
            best[c["key"]] = c
    ranked = rank(best.values())
    for c in [c for c in ranked if c["kind"] == "video"][:3]:
        enrich(c, http, deep=deep)

    if not any(c["kind"] == "video" for c in ranked):  # step 5: search, only with a key or explicit opt-in
        q = ctx.get("company") or re.sub(URL_RE, " ", query).strip() or ctx.get("title") or ""
        extra: list[dict] = []
        if q and youtube_api_key:
            try:
                extra = youtube_api_search(f"{q} launch demo", youtube_api_key, http)
            except Exception as e:  # noqa: BLE001
                log(f"youtube search: {e}")
        elif q and allow_ytsearch:
            try:
                extra = ytsearch(f"{q} launch demo")
            except Exception as e:  # noqa: BLE001
                log(f"ytsearch: {e}")
        ranked = rank(list(ranked) + extra)

    for c in ranked:
        _mark_official(c, ctx.get("company", ""), host_of(ctx["site"]) if ctx.get("site") else "")
        c["credit"] = credit_line(c)
        if ctx.get("company"):
            c["company"] = ctx["company"]
    return rank(ranked)[:max_results]
