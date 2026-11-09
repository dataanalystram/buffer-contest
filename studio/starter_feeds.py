"""Starter feeds: a new account's News is never empty (owner, 9 Nov 2026).

- Skipped setup (or no interests picked): a short list of reliable tech-news feeds is followed for the person the first
  time their For you feed opens, so current stories show at once.
- Picked interests in setup: the feeds that match those content types are followed (one per interest first, so every
  interest gets a source, up to radar.MAX_FEEDS), and the starter feeds that no longer match are unfollowed.

Only feeds HuhQueue added are ever removed: a feed the person followed by hand stays, and a starter feed the person
unfollowed is never added back. The bookkeeping lives in user_profiles.data (onboarding's row): ``starter_feeds`` (the
URLs we followed for them) and ``starter_dropped`` (the ones they unfollowed). Every URL is a public RSS feed from the
Sources library (studio/sources.py LIBRARY) or a Google News topic search; nothing needs a key. The sample workspace
keeps its own seeded feeds (demo.SEED_FEEDS). HQ_STARTER_FEEDS=0 turns it off without a deploy.
"""
from __future__ import annotations

import json
import os
import time
from urllib.parse import quote_plus

from . import demo_guard, radar


def _gnews(q: str) -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(q)}+when:2d&hl=en-US&gl=US&ceid=US:en"


# url -> label shown in setup ("Following for you")
LABELS = {
    "https://hnrss.org/frontpage": "Hacker News",
    "https://www.theverge.com/rss/index.xml": "The Verge",
    "https://techcrunch.com/feed/": "TechCrunch",
    "https://techcrunch.com/category/artificial-intelligence/feed/": "TechCrunch AI",
    "https://www.technologyreview.com/topic/artificial-intelligence/feed/": "MIT Technology Review AI",
    "https://openai.com/news/rss.xml": "OpenAI News",
    "https://www.producthunt.com/feed": "Product Hunt",
    "https://hnrss.org/launches": "Launch HN",
    "https://hnrss.org/show": "Show HN",
    "https://github.blog/feed/": "GitHub Blog",
    "https://huggingface.co/blog/feed.xml": "Hugging Face Blog",
    "https://rss.arxiv.org/rss/cs.AI": "arXiv cs.AI",
    "https://feeds.arstechnica.com/arstechnica/index": "Ars Technica",
    "https://www.engadget.com/rss.xml": "Engadget",
    _gnews('"Y Combinator" startup'): "Google News: YC startups",
    _gnews("startup funding round"): "Google News: funding",
    _gnews("India tech startup"): "Google News: India tech",
    _gnews("tutorial OR \"how to\" developers"): "Google News: tutorials",
    _gnews("viral internet culture"): "Google News: internet culture",
}
_U = {v: k for k, v in LABELS.items()}

# skipped setup, or no interests: current, general tech news
DEFAULT = [_U["Hacker News"], _U["The Verge"], _U["TechCrunch"], _U["MIT Technology Review AI"], _U["Ars Technica"]]

# onboarding.CONTENT_TYPES id -> feeds, best first
BY_TYPE = {
    "ai_news": [_U["TechCrunch AI"], _U["MIT Technology Review AI"], _U["OpenAI News"]],
    "product_launches": [_U["Product Hunt"], _U["Show HN"]],
    "yc_startups": [_U["Launch HN"], _U["Google News: YC startups"]],
    "tutorials": [_U["Google News: tutorials"], _U["GitHub Blog"]],
    "dev_tools": [_U["GitHub Blog"], _U["Show HN"]],
    "memes": [_U["Google News: internet culture"], _U["The Verge"]],
    "india_tech": [_U["Google News: India tech"]],
    "research": [_U["arXiv cs.AI"], _U["Hugging Face Blog"]],
    "funding": [_U["Google News: funding"], _U["TechCrunch"]],
    "open_source": [_U["Hugging Face Blog"], _U["GitHub Blog"]],
}

TYPES_OF: dict[str, tuple] = {}  # feed url -> the interests it was picked for (onboarding.rank_stories)
for _t, _urls in BY_TYPE.items():
    for _u in _urls:
        TYPES_OF[_u] = TYPES_OF.get(_u, ()) + (_t,)


def enabled() -> bool:
    return os.environ.get("HQ_STARTER_FEEDS", "1") != "0"


def wanted(types: list[str]) -> list[str]:
    """The starter feeds for these interests: round-robin (each interest's best feed first), then Hacker News so the
    feed always has fresh general stories, capped at radar.MAX_FEEDS. No interests -> DEFAULT."""
    lists = [BY_TYPE[t] for t in types if t in BY_TYPE]
    if not lists:
        return list(DEFAULT)
    out: list[str] = []
    for i in range(max(len(x) for x in lists)):
        for x in lists:
            if i < len(x) and x[i] not in out:
                out.append(x[i])
    if _U["Hacker News"] not in out:
        out.append(_U["Hacker News"])
    return out[: radar.MAX_FEEDS]


def _profile(store, uid: str) -> dict:
    rows = store.q("SELECT data FROM user_profiles WHERE user_id=?", (uid,))
    return json.loads(rows[0]["data"] or "{}") if rows else {}


def _save(store, uid: str, data: dict) -> None:
    store.x("INSERT INTO user_profiles(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
            "updated=excluded.updated", (uid, json.dumps(data), time.time()))


def sync(store, uid: str, types: list[str] | None = None, first_run_only: bool = False) -> dict:
    """Follow the starter feeds for these interests (None -> the interests saved in setup). first_run_only: only when
    the account has never had starter feeds and follows nothing (the For you feed's first open). Returns
    {"added": [...], "removed": [...]}; never raises (the feed must still open)."""
    out = {"added": [], "removed": []}
    if not enabled() or demo_guard.is_demo(uid):
        return out
    try:
        from . import onboarding
        onboarding.ensure(store)
        prof = _profile(store, uid)
        feeds = [dict(r) for r in store.q("SELECT id,url FROM feeds WHERE user_id=? ORDER BY id", (uid,))]
        if first_run_only and ("starter_feeds" in prof or feeds):
            return out
        if types is None:
            types = [t for t in prof.get("content_types") or [] if t in onboarding.CONTENT_TYPES]
        mine = set(prof.get("starter_feeds") or [])
        dropped = set(prof.get("starter_dropped") or [])
        want = [u for u in wanted(types) if u not in dropped]
        for f in feeds:  # starter feeds that no longer match the interests go; hand-followed feeds always stay
            if f["url"] in mine and f["url"] not in want:
                store.x("DELETE FROM feeds WHERE id=? AND user_id=?", (f["id"], uid))
                mine.discard(f["url"])
                out["removed"].append(f["url"])
        have = {f["url"] for f in feeds} - set(out["removed"])
        room = radar.MAX_FEEDS - len(have)
        now = time.time()
        for u in want:
            if room <= 0:
                break
            if u in have:
                continue
            store.x("INSERT INTO feeds(user_id,url,created) VALUES(?,?,?)", (uid, u, now))
            mine.add(u)
            have.add(u)
            room -= 1
            out["added"].append(u)
        prof = _profile(store, uid)  # re-read: keep anything onboarding wrote meanwhile
        prof["starter_feeds"] = sorted(mine)
        _save(store, uid, prof)
        if out["added"] or out["removed"]:
            store.audit(uid, "HuhQueue", "starter_feeds", "read", {"added": len(out["added"]), "removed": len(out["removed"]), "interests": types})
    except Exception:  # noqa: BLE001 - starter feeds are a convenience; News must open regardless
        return out
    return out


def forget(store, uid: str, url: str) -> None:
    """The person unfollowed a feed: if HuhQueue had added it, never add it back."""
    try:
        prof = _profile(store, uid)
        if url in (prof.get("starter_feeds") or []):
            prof["starter_feeds"] = [u for u in prof["starter_feeds"] if u != url]
            prof["starter_dropped"] = sorted(set(prof.get("starter_dropped") or []) | {url})
            _save(store, uid, prof)
    except Exception:  # noqa: BLE001
        pass


def mine(store, uid: str) -> list[str]:
    try:
        return list(_profile(store, uid).get("starter_feeds") or [])
    except Exception:  # noqa: BLE001
        return []


def following(store, uid: str) -> list[dict]:
    """The feeds the account follows, labelled (setup's "Following for you" row)."""
    rows = store.q("SELECT url FROM feeds WHERE user_id=? ORDER BY id", (uid,))
    return [{"url": r["url"], "label": LABELS.get(r["url"]) or radar_title(r["url"])} for r in rows]


def radar_title(url: str) -> str:
    hit = radar._cache.get(url)
    if hit:
        return hit[1].get("title") or url
    from urllib.parse import urlparse
    return (urlparse(url).hostname or url).removeprefix("www.")
