"""Video-first research: when the person asks for a video, or the product has one, find THE launch video before anything
else, in this order (owner, 2 Nov 2026: a Product Hunt link + "fetch the image as well as the video" made only a carousel):

  a. Product Hunt's API (the app's own key, studio.appkeys; a person's developer token as an override): the launch's
     media (type ``video`` with ``videoUrl``, usually YouTube), its images and the product's own website.
  b. The product's own site: og:video, <video>/<source>, YouTube/Vimeo/Loom/Wistia embeds, JSON-LD VideoObject, and one
     hop to a press / media kit page (studio.media.find.scan_page).
  c. The official channel: a web search (Firecrawl search when FIRECRAWL_API_KEY is set, else the YouTube Data API when
     a key is set) for the product's launch video on YouTube or X, kept only when the uploader is the product itself.

Downloads: only the maker's own launch video, for news coverage, credited on screen and in the caption ("Video: <maker>").
YouTube blocks downloads from Render and GitHub runners, so direct files, Vimeo, Loom and X win; a YouTube-only video is
never handed to the worker unless the operator turned on the YouTube fallback (studio.media.yt_fallback). Then the result
says so plainly and carries the question to ask: upload the file (with the app's real upload limit). No video anywhere ->
"No video found" and the same upload offer.

Pure functions over an httpx client: every fetch goes through netguard (find.py's ``_get``); keys never leave this module.
"""
from __future__ import annotations

import os
import re
from typing import Callable
from urllib.parse import urljoin, urlparse, urlunparse

from .. import netguard
from . import find, youtube_info

PH_GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
PH_FIELDS = ("id name tagline description slug url website createdAt featuredAt votesCount "
             "thumbnail { type url videoUrl } media { type url videoUrl }")
WANT_VIDEO = re.compile(r"\b(?:videos?|reels?|clips?|shorts?|trailer|footage|demo video|launch video|mp4|tiktok|youtube)\b", re.I)
DIRECT_FIRST = {"direct": 0, "vimeo": 1, "loom": 2, "wistia": 3, "x": 4, "tiktok": 5, "instagram": 6, "youtube": 9}
PRESS_RX = re.compile(r"/(?:press|press-kit|presskit|media-kit|mediakit|brand|newsroom)(?:/|$)", re.I)
ACCEPT = "MP4/MOV"


def wants_video(prompt: str) -> bool:
    """The person asked for a video ('fetch the image as well as the video', 'make a reel', 'short')."""
    return bool(WANT_VIDEO.search(prompt or ""))


def ph_slug(url: str) -> tuple[str, str] | None:
    """('product' | 'post', slug) for a Product Hunt link, else None."""
    if find.host_of(url or "") != "producthunt.com":
        return None
    m = re.match(r"^/(products|posts)/([a-z0-9][a-z0-9-]{0,80})", urlparse(url).path, re.I)
    if not m:
        return None
    return ("product" if m.group(1).lower() == "products" else "post", m.group(2).lower())


def is_product_link(url: str) -> bool:
    return ph_slug(url) is not None


def slug_name(url: str) -> str:
    """'screen-studio' -> 'Screen Studio' (the product's name when Product Hunt's API can't be read)."""
    sl = ph_slug(url)
    return " ".join(w.capitalize() for w in re.sub(r"-\d+$", "", sl[1]).split("-")) if sl else ""


def clean_site(url: str) -> str:
    """The maker's website without Product Hunt's tracking (?ref=producthunt, utm_*)."""
    if not url:
        return ""
    p = urlparse(url if "://" in url else "https://" + url)
    q = "&".join(x for x in (p.query or "").split("&") if x and not x.lower().startswith(("ref=", "utm_")))
    return urlunparse((p.scheme or "https", p.netloc, p.path or "/", "", q, ""))


def ph_query(slug: str, kind: str = "product") -> str:
    """A launch's slug is the product's slug and later launches add -2, -3...: ask for all of them in one call."""
    slugs = [slug] + ([f"{slug}-{i}" for i in range(2, 7)] if kind == "product" else [])
    parts = [f'p{i}: post(slug: "{s}") {{ {PH_FIELDS} }}' for i, s in enumerate(slugs)]
    return "query { " + " ".join(parts) + " }"


def parse_ph(data: dict) -> list[dict]:
    """GraphQL answer -> the launches found, newest first: {name, tagline, website, url, created, videos, images}."""
    out = []
    for v in ((data or {}).get("data") or {}).values():
        if not isinstance(v, dict) or not v.get("name"):
            continue
        media = [m for m in (v.get("media") or []) if isinstance(m, dict)]
        vids = [m.get("videoUrl") for m in media if (m.get("type") or "").lower() == "video" and m.get("videoUrl")]
        th = v.get("thumbnail") or {}
        if (th.get("type") or "").lower() == "video" and th.get("videoUrl"):
            vids.append(th["videoUrl"])
        imgs = [m.get("url") for m in media if (m.get("type") or "").lower() == "image" and m.get("url")]
        out.append({"name": v["name"], "tagline": v.get("tagline") or "", "description": (v.get("description") or "")[:600],
                    "website": clean_site(v.get("website") or ""), "url": v.get("url") or "", "slug": v.get("slug") or "",
                    "created": v.get("featuredAt") or v.get("createdAt") or "", "votes": v.get("votesCount") or 0,
                    "videos": list(dict.fromkeys(vids)), "images": list(dict.fromkeys(imgs))[:8],
                    "thumbnail": th.get("url") or ""})
    out.sort(key=lambda x: x["created"], reverse=True)
    return out


def ph_lookup(url: str, token: str, http=None) -> dict:
    """Product Hunt's API for one product / post link: the newest launch with a video (else the newest), plus every
    launch's video. {} when there is no token or no launch; raises find.FindError when Product Hunt refused."""
    sl = ph_slug(url)
    if not sl or not token:
        return {}
    r = netguard.safe_post(PH_GRAPHQL, http=http, json={"query": ph_query(sl[1], sl[0])},
                           headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": find.UA},
                           timeout=15, allowed_hosts={"api.producthunt.com"}, limit=2_000_000)
    if r.status_code != 200:
        raise find.FindError(f"Product Hunt answered {r.status_code}")
    launches = parse_ph(r.json())
    if not launches:
        return {}
    best = next((x for x in launches if x["videos"]), launches[0])
    return {**best, "launches": len(launches), "all_videos": list(dict.fromkeys(v for x in launches for v in x["videos"]))}


def firecrawl_search(q: str, http=None, n: int = 6) -> list[dict]:
    """Firecrawl /v1/search (the operator's FIRECRAWL_API_KEY): [{url, title}]. [] without a key."""
    key = os.environ.get("FIRECRAWL_API_KEY", "")
    if not key:
        return []
    r = netguard.safe_post("https://api.firecrawl.dev/v1/search", http=http, json={"query": q, "limit": n},
                           headers={"Authorization": f"Bearer {key}", "Accept": "application/json"}, timeout=30,
                           allowed_hosts={"api.firecrawl.dev"}, limit=1_000_000)
    if r.status_code != 200:
        raise find.FindError(f"web search answered {r.status_code}")  # never echo the request: it carries the key
    items = (r.json() or {}).get("data") or []
    return [{"url": i.get("url") or "", "title": i.get("title") or ""} for i in items if isinstance(i, dict) and i.get("url")]


def search_available(youtube_api_key: str | None = None) -> bool:
    return bool(os.environ.get("FIRECRAWL_API_KEY") or youtube_api_key)


def _maker_match(c: dict, name: str, domain: str) -> bool:
    n = find._norm(name)
    who = find._norm(c.get("uploader", "")) + find._norm(c.get("author_url", ""))
    if n and len(n) >= 3 and n[:12] in who:
        return True
    d = find._norm(domain.split(".")[0]) if domain else ""
    return bool(d and len(d) >= 3 and d in who)


def yt_allowed() -> bool:
    """May the worker try a YouTube download? Only with the operator's fallback route on (YouTube bot-blocks servers)."""
    return bool(os.environ.get("HQ_YT_FALLBACK"))


LAUNCH_HOWS = {"product_hunt", "yc_launch", "official_channel", "linked"}  # the launch film the maker posted for the launch itself
HERO_RX = re.compile(r"(?:^|[/_.-])(?:hero|loop|loops|background|bg|banner|ambient|header|backdrop|splash)(?:[/_.-]|\d|$)", re.I)
YC_LAUNCH_RX = re.compile(r"^https?://(?:www\.)?ycombinator\.com/(?:launches|companies)/", re.I)


def is_hero_loop(c: dict) -> bool:
    """A website's decorative hero / background loop (aviern.com/renders/web-hero.av1.mp4): a real video of the product,
    but not its launch film. Kept as the fallback when nothing better can be fetched."""
    if c.get("how") in LAUNCH_HOWS or c.get("platform") not in ("direct", "web", None):
        return False
    path = urlparse(c.get("url") or "").path
    d = c.get("duration")
    return bool(HERO_RX.search(path)) or (isinstance(d, (int, float)) and 0 < d < 12)


def _rank_key(c: dict) -> tuple:
    """Official first; then the launch film (Product Hunt / YC launch / the maker's channel) before other videos, and a
    site's hero loop last; then direct files before platforms."""
    return (0 if c.get("official") else 1, 1 if is_hero_loop(c) else 0, 0 if c.get("how") in LAUNCH_HOWS else 1,
            DIRECT_FIRST.get(c["platform"], 8), -c.get("confidence", 0))


def _fetchable(c: dict) -> bool:
    return (c["kind"] == "video" and bool(c.get("official")) and c["platform"] in find.DOWNLOADABLE
            and (c["platform"] != "youtube" or yt_allowed()) and c.get("downloadable", True) is not False)


def _site_from_search(name: str, http) -> str:
    try:
        for h in firecrawl_search(f"{name} official website", http, n=5):
            u = h["url"]
            if u.startswith("https://") and not find.platform_of(u) and find.host_of(u) not in (
                    "producthunt.com", "wikipedia.org", "en.wikipedia.org", "crunchbase.com", "g2.com", "reddit.com"):
                return clean_site(f"{urlparse(u).scheme}://{urlparse(u).netloc}/")
    except Exception:  # noqa: BLE001
        pass
    return ""


BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")


def is_ph_redirect(url: str) -> bool:
    """Product Hunt hands out the maker's website as producthunt.com/r/<code>, which answers 403 to servers."""
    return bool(url) and find.host_of(url) == "producthunt.com" and urlparse(url if "://" in url else "https://" + url).path.startswith("/r/")


def resolve_site(url: str, name: str, http=None, log: Callable[[str], None] | None = None) -> tuple[str, str]:
    """The maker's real website for ``url``: a producthunt.com/r/... redirect is followed with a browser User-Agent (every
    hop checked by netguard), else found by name with a web search. Returns (site, note); ("", note) when neither worked."""
    if not is_ph_redirect(url):
        return url, ""
    log = log or (lambda m: None)
    try:
        with netguard.safe_stream(url, http=http, timeout=12, headers={"User-Agent": BROWSER_UA,
                                                                      "Accept": "text/html,*/*;q=0.8"}) as r:
            final = str(r.url)
            if r.status_code < 400 and find.host_of(final) and find.host_of(final) != "producthunt.com":
                return clean_site(final), ""
            log(f"product hunt redirect answered {r.status_code}")
    except Exception as e:  # noqa: BLE001 - fall through to the search
        log(f"product hunt redirect: {e.__class__.__name__}")
    site = _site_from_search(name, http) if name else ""
    if site:
        return site, "Product Hunt's link to the website was blocked, so the site was found by search."
    return "", "Product Hunt's link to the website was blocked and no web search is configured to find it."


def research_video(query: str, *, http=None, ph_token: str = "", youtube_api_key: str | None = None, upload_limit_mb: int = 40,
                   name_hint: str = "", website_hint: str = "", deep: bool = True,
                   log: Callable[[str], None] | None = None) -> dict:
    """The video-first cascade for a link (or a product name). Returns::

        {status: found | youtube_only | blocked | none, video: the candidate the worker fetches | None, launch_video: the
         best official video anywhere (e.g. the YouTube original) | None, best_downloadable / best_video (find_media's
         shape), credit: "Video: <maker>", maker, website, product: {name, tagline, description, url, images, thumbnail}
         | None, images, steps: [{step, ok, found, note}], plan_line, ask ("" when found), upload: {accept, max_mb, label}}
    """
    log = log or (lambda m: None)
    links = find.urls_in(query)
    steps: list[dict] = []
    cands: list[dict] = []
    product: dict | None = None
    name = (name_hint or "").strip()
    website = clean_site(website_hint)
    images: list[str] = []

    # a. Product Hunt
    ph_link = next((u for u in links if is_product_link(u)), "")
    if ph_link:
        note, ph = "", {}
        if not ph_token:
            note = "Product Hunt isn't connected on this server, so its launch media wasn't read."
        else:
            try:
                ph = ph_lookup(ph_link, ph_token, http)
                if not ph:
                    note = "Product Hunt has no launch under that name."
            except Exception as e:  # noqa: BLE001 - one source failing never stops the cascade
                note = f"Product Hunt didn't answer ({str(e)[:80]})."
        if ph:
            product = {k: ph.get(k) for k in ("name", "tagline", "description", "url", "images", "thumbnail", "website", "created")}
            name = name or ph["name"]
            website = website or ph["website"]
            images += ph["images"] + ([ph["thumbnail"]] if ph.get("thumbnail") else [])
            for v in ph["all_videos"][:3]:
                c = find._cand(v, "direct_post", source_page=ph_link, kind="video")
                c.update(how="product_hunt", confidence=0.95, official=True)
                cands.append(c)
        else:
            name = name or slug_name(ph_link)
            website = website or _site_from_search(name, http)
        steps.append({"step": "product_hunt", "ok": bool(ph), "found": len(ph.get("all_videos") or []) if ph else 0, "note": note})
    elif not website:
        website = next((u for u in links if not find.platform_of(u) and not YC_LAUNCH_RX.match(u)), "")

    # a0. a YouTube video link (owner bug, 3 Nov 2026): the real video's details (Data API / oEmbed), then the maker's own
    #     website, so the official copy there is the fallback when YouTube itself can't be downloaded here.
    linked, linked_info, linked_site_ok = None, None, False
    yt_link = "" if ph_link else next((u for u in links if youtube_info.is_youtube_video(u)), "")
    if yt_link:
        linked_info = youtube_info.video_info(yt_link, http, youtube_api_key or "")
        if linked_info:
            name = name or linked_info["channel"]
            linked = find._cand(linked_info["url"], "linked", kind="video", title=linked_info["title"], uploader=linked_info["channel"],
                                thumbnail=linked_info["thumbnail"], author_url=linked_info["channel_url"], duration=linked_info["duration"],
                                source_page=yt_link)
            linked["confidence"] = 0.97
            cands.append(linked)
            site_how = ""
            if not website:
                website, site_how = youtube_info.find_site(name, linked_info["links"], http)
            linked_site_ok = bool(website)
            via = "the YouTube Data API" if linked_info["via"] == "youtube_api" else "YouTube oEmbed"
            steps.append({"step": "linked_video", "ok": True, "found": 1, "url": yt_link,
                          "note": f"Read “{linked_info['title']}” by {linked_info['channel']} ({via})"
                                  + (f"; its website is {find.host_of(website)} ({site_how})" if website else f"; {site_how}")})
        else:
            steps.append({"step": "linked_video", "ok": False, "found": 0, "url": yt_link,
                          "note": "YouTube didn't give this video's details (private, removed or not answering)."})

    # a1. a YC launch page (ycombinator.com/launches/...): the founders' launch video is embedded there (usually YouTube)
    for yl in [u for u in links if YC_LAUNCH_RX.match(u)][:1]:
        found, note = 0, ""
        try:
            got, ctx = find.from_page(yl, http)
            if not name:  # "Launch YC: Aviern: Unmanned cargo airplanes ..." -> Aviern
                t = re.sub(r"^\s*launch\s+yc\s*:\s*", "", ctx.get("title") or "", flags=re.I)
                name = re.split(r"\s*[:|–—-]\s", t)[0].strip()[:60] if t and t != (ctx.get("title") or "") else name
            seen = set()
            for c in got:
                if c["kind"] == "video" and c["url"] not in seen:
                    seen.add(c["url"])
                    c.update(how="yc_launch", official=True, confidence=max(c.get("confidence", 0.5), 0.95), source_page=yl)
                    cands.append(c)
                    found += 1
        except Exception as e:  # noqa: BLE001 - one source failing never stops the cascade
            note = f"The YC launch page didn't open ({e.__class__.__name__})."
        steps.append({"step": "yc_launch", "ok": not note, "found": found, "note": note, "url": yl})
    if not website:
        website = next((u for u in links if not find.platform_of(u) and not YC_LAUNCH_RX.match(u)), "")

    # a2. Product Hunt's website field is often a producthunt.com/r/... redirect that 403s: find the real site first,
    #     so the maker's own video (e.g. screen.studio's hero-demo.mp4) is checked before YouTube.
    site_note = ""
    if is_ph_redirect(website):
        website, site_note = resolve_site(website, name, http, log)

    # b. the product's own site (and one hop to its press kit)
    if not website and site_note:
        steps.append({"step": "website", "ok": False, "found": 0, "note": site_note, "url": ""})
    if website:
        found, note = 0, ""
        try:
            got, ctx = find.from_page(website, http)
            if ctx.get("note"):
                note = f"Its website answered: {ctx['note']}."
            name = name or re.split(r"\s[|\-–—:]\s", ctx.get("site_name") or ctx.get("title") or "")[0].strip()
            for c in got:
                if c["kind"] == "video":
                    cands.append(c)
                    found += 1
                elif c["kind"] == "image":
                    images.append(c["url"])
            if not found and not note:  # one hop: a press / media kit page often holds the launch film
                r = find._get(website, http)
                sc = find._Scan()
                try:
                    sc.feed(r.text or "")
                except Exception:  # noqa: BLE001
                    pass
                press = next((urljoin(website, h) for h in sc.links if PRESS_RX.search(urlparse(urljoin(website, h)).path)
                              and find.host_of(urljoin(website, h)) == find.host_of(website)), "")
                if press:
                    got2, _ = find.from_page(press, http)
                    vids = [c for c in got2 if c["kind"] == "video"]
                    cands += vids
                    found += len(vids)
                    note = f"Checked its press page ({urlparse(press).path})."
        except Exception as e:  # noqa: BLE001
            note = f"Its website didn't open ({e.__class__.__name__})."
        if site_note:
            note = (site_note + " " + note).strip()
        steps.append({"step": "website", "ok": "didn't open" not in note and "answered" not in note, "found": found,
                      "note": note, "url": website})

    dom = find.host_of(website) if website else ""
    for c in cands:
        find._mark_official(c, name, dom)
    for c in [c for c in cands if c["platform"] not in ("direct", "web")][:4]:  # oEmbed author, size; never downloads
        try:
            find.enrich(c, http, deep=deep)
        except Exception as e:  # noqa: BLE001
            log(f"enrich: {e}")
        if not c.get("official") and _maker_match(c, name, dom):
            c["official"] = True

    if linked is not None:
        # the linked video counts as the maker's own when its channel has a website of the same name; else it is someone
        # else's video and the rights question applies. With the operator's YouTube fallback on, a server bot check on
        # the metadata read doesn't stop the worker (it fetches through the fallback route).
        linked["official"] = linked_site_ok
        if linked_site_ok:
            linked["confidence"] = 0.98
        if yt_allowed() and linked.get("blocked") == "platform_bot_check":
            linked["downloadable"], linked["blocked"] = True, ""

    # c. the official channel, by web search, when nothing fetchable came from the maker yet
    if name and not any(_fetchable(c) and not is_hero_loop(c) for c in cands):  # a hero loop alone: still look for the launch film
        found, note, extra = 0, "", []
        if not search_available(youtube_api_key):
            note = "No web search is configured on this server for the official channel."
        else:
            try:
                hits = firecrawl_search(f"{name} launch video", http)
                for h in hits:
                    u = h["url"]
                    if find.is_video_post(u) and find.platform_of(u) in ("youtube", "x", "vimeo", "loom"):
                        extra.append(find._cand(u, "page_link", title=h.get("title", "")))
                if not extra and youtube_api_key:
                    extra = find.youtube_api_search(f"{name} launch", youtube_api_key, http, n=5)
            except Exception as e:  # noqa: BLE001
                note = f"The channel search didn't answer ({str(e)[:60]})."
        for c in extra[:4]:
            try:
                find.enrich(c, http, deep=deep)
            except Exception:  # noqa: BLE001
                pass
            if _maker_match(c, name, dom):  # only the maker's own channel counts
                c.update(official=True, how="official_channel", confidence=max(c.get("confidence", 0.5), 0.8))
                cands.append(c)
                found += 1
        steps.append({"step": "official_channel", "ok": not note, "found": found, "note": note})

    vids: dict[str, dict] = {}
    for c in cands:
        if c["kind"] == "video" and (c["key"] not in vids or c.get("confidence", 0) > vids[c["key"]].get("confidence", 0)):
            vids[c["key"]] = c
    for c in vids.values():
        if c["platform"] == "youtube" and not yt_allowed():
            c["downloadable"] = False
            c["blocked"] = c.get("blocked") or "youtube_blocks_servers"
    ranked = sorted(vids.values(), key=_rank_key)
    for c in ranked:
        if is_hero_loop(c):
            c["note"] = c.get("note") or "the website's hero loop (used only when the launch video can't be fetched)"
    official = [c for c in ranked if c.get("official")]
    pick = next((c for c in official if _fetchable(c)), None)
    launch = pick or (official or [None])[0]
    if linked is not None and not pick:
        launch = linked if not launch or launch.get("platform") == "youtube" else launch  # the video we use; else the best official one anywhere (the YouTube original)
    maker = name or (launch or {}).get("uploader") or dom or "the maker"
    credit = f"Video: {maker}"
    for c in ranked:
        c["credit"] = credit if c.get("official") else find.credit_line(c)
        if name and not c.get("company"):
            c["company"] = name
    limit = int(upload_limit_mb)
    if pick and linked is not None:
        status, ask = "found", ""
        if pick is linked:
            plan_line = f"Video found: the video in your link ({maker} on YouTube), fetched through the YouTube fallback, branded as a Reel with credit"
        else:
            plan_line = (f"Video found: {maker}'s own copy on {find.host_of(pick['url']) or 'its website'} (YouTube blocks downloads "
                         "here), branded as a Reel with credit")
    elif pick:
        status, ask = "found", ""
        where = {"direct": "its website", "vimeo": "Vimeo", "loom": "Loom", "x": "X", "youtube": "YouTube",
                 "wistia": "Wistia"}.get(pick["platform"], pick["platform"])
        plan_line = f"Video found: {maker}'s launch video ({where}), branded as a Reel with credit"
    elif linked is not None:
        status = "youtube_only"
        how = "with your YouTube Data API key" if linked_info["via"] == "youtube_api" else "through YouTube's oEmbed"
        ask = (f"I read the video's details {how}: “{linked_info['title']}” by {linked_info['channel']}. The YouTube Data API gives "
               f"details, not the video file, and YouTube blocks downloads from our servers"
               + (f"; {find.host_of(website)} has no copy I can use" if website else "") +
               f". Upload the file ({ACCEPT}, up to {limit} MB) and I'll brand it with the credit “Video: {linked_info['channel']}”.")
        plan_line = "The video is on YouTube only and can't be downloaded here: asking you to upload it"
    elif launch:
        status = "youtube_only" if launch["platform"] == "youtube" else "blocked"
        site = find.NAMES.get(launch["platform"], launch["platform"])
        ask = f"I found the launch video on {site} but can't download it here. Upload the file ({ACCEPT}, up to {limit} MB) and I'll brand it."
        plan_line = f"Launch video is on {site} only and can't be downloaded here: asking you to upload it"
    else:
        status = "none"
        ask = f"No video found. Upload one ({ACCEPT}, up to {limit} MB) and I'll brand it."
        plan_line = "No video found, carousel only (upload a video to get a Reel)"
    return {"status": status, "video": _slim(pick) if pick else None, "launch_video": _slim(launch) if launch else None,
            "best_downloadable": _slim(pick) if pick else None, "best_video": _slim(launch) if launch else None,
            "candidates": [_slim(c) for c in ranked[:6]], "credit": credit, "maker": maker, "website": website, "product": product,
            "images": list(dict.fromkeys(i for i in ([linked_info["thumbnail"]] if linked_info and linked_info.get("thumbnail") else []) + images if i))[:8],
            "steps": steps, "plan_line": plan_line, "ask": ask,
            "youtube": ({k: linked_info.get(k) for k in ("title", "channel", "channel_url", "via", "published", "duration")} if linked_info else None),
            "upload": {"accept": "video/mp4,video/quicktime", "max_mb": limit, "label": ACCEPT}}


def _slim(c: dict) -> dict:
    keep = ("url", "platform", "kind", "how", "confidence", "title", "uploader", "duration", "official", "downloadable", "credit",
            "source_page", "thumbnail", "note", "blocked", "company", "width", "height")
    return {k: c[k] for k in keep if c.get(k) not in (None, "")}
