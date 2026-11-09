"""Onboarding personalisation: what the person wants to post about, where, and their brand, then a tailored feed with
"recommended for you" posts already rendered in their brand.

- GET/PUT /api/onboarding: content types (AI news, product launches, YC startups, tutorials, dev tools, memes, India tech,
  research, funding, open source), channels, website (-> brand kit via the context.dev Brand API, with the built-in
  website reader as the fallback), tone, and an X watchlist (accounts read with the person's own X API key; single post
  links read through X's public oEmbed).
- GET /api/recommendations: the Sources agent's scan filtered and ranked by those choices, plus up to three ready-to-
  approve posts (cover in the brand + one caption per chosen channel). Cached per account for an hour, so opening the
  screen costs no source requests; ?refresh=1 rescans (inside the Sources agent's daily cap), at most once an hour
  (HQ_RECS_REFRESH_SECONDS), and each rebuild counts HQ_RECS_COST_USD against the person's included budget.
  The sample workspace never scans, renders or calls a model here: it serves the pre-made sample_recs/recs.json
  (scripts/build_sample_recs.py), filtered by the visitor's choices.
Stored in the existing database (user_profiles); the sample workspace uses recorded fixtures only.
"""
from __future__ import annotations

import json
import os
import re
import time

from pathlib import Path

import httpx

from .actions import ActionError
from . import brand, budget, demo_guard, netguard, reader, render

SCHEMA = "CREATE TABLE IF NOT EXISTS user_profiles (user_id TEXT PRIMARY KEY, data TEXT, recs TEXT, recs_at REAL, updated REAL)"
CONTEXT_DEV = "https://api.context.dev/v1/brand/retrieve"
RECS_TTL = 3600
RECS_REFRESH = int(os.environ.get("HQ_RECS_REFRESH_SECONDS", "3600"))  # one rebuild per account per hour, refresh or not
RECS_COST = float(os.environ.get("HQ_RECS_COST_USD", "0.02"))          # a scan + 3 rendered covers, charged to the person's $1
SAMPLE_RECS = Path(__file__).parent / "sample_recs" / "recs.json"
_sample_cache: dict = {}

CONTENT_TYPES = {
    "ai_news": {"label": "AI news", "cats": {"model_release", "research", "safety_society", "policy_law"}, "words": ["ai", "model", "openai", "anthropic", "gemini", "llm"], "sources": ["hn", "rss"]},
    "product_launches": {"label": "Product launches", "cats": {"product_feature"}, "words": ["launch", "launches", "introducing", "now available", "ships"], "sources": ["ph", "hn", "rss"]},
    "yc_startups": {"label": "YC startups", "cats": {"product_feature", "business_funding"}, "words": ["yc", "y combinator", "startup"], "sources": ["yc", "hn"]},
    "tutorials": {"label": "Tutorials and how-tos", "cats": {"howto_tools"}, "words": ["how to", "guide", "tutorial", "explained"], "sources": ["hn", "rss"]},
    "dev_tools": {"label": "Developer tools", "cats": {"howto_tools", "product_feature"}, "words": ["api", "sdk", "open source", "github", "developer", "cli"], "sources": ["hn", "ph"]},
    "memes": {"label": "Memes and culture", "cats": {"safety_society"}, "words": ["meme", "viral", "funny"], "sources": ["hn", "x"]},
    "india_tech": {"label": "India tech", "cats": {"business_funding", "product_feature", "policy_law"}, "words": ["india", "indian", "bengaluru", "bangalore", "rupee", "₹"], "sources": ["hn", "rss"]},
    "research": {"label": "Research papers", "cats": {"research"}, "words": ["paper", "arxiv", "study", "researchers"], "sources": ["hn", "rss"]},
    "funding": {"label": "Funding and deals", "cats": {"business_funding"}, "words": ["raises", "raised", "funding", "series", "acquires"], "sources": ["yc", "hn"]},
    "open_source": {"label": "Open source", "cats": {"howto_tools", "model_release"}, "words": ["open source", "open-source", "github", "weights"], "sources": ["hn"]},
}
TONES = ["plain", "punchy", "formal", "casual", "explainer"]


def ensure(store) -> None:
    store.x(SCHEMA.replace(" REAL", " DOUBLE PRECISION") if store.pg else SCHEMA)
    if store.pg:
        store.x("ALTER TABLE user_profiles ENABLE ROW LEVEL SECURITY")


class OnboardingError(ValueError):
    pass


class Onboarding:
    def __init__(self, house):
        self.h, self.store, self.http = house, house.store, house.http
        ensure(self.store)

    def _row(self, uid: str) -> dict:
        rows = self.store.q("SELECT data, recs, recs_at FROM user_profiles WHERE user_id=?", (uid,))
        return {"data": json.loads(rows[0]["data"] or "{}"), "recs": json.loads(rows[0]["recs"] or "null"), "recs_at": rows[0]["recs_at"] or 0} if rows else \
            {"data": {}, "recs": None, "recs_at": 0}

    def get(self, a) -> dict:
        d = self._row(a.uid)["data"]
        chans = [c for c in self.h.studio.channels(a) if not c.get("isDisconnected")]
        return {"profile": {"content_types": d.get("content_types") or [], "channel_ids": d.get("channel_ids") or [c["id"] for c in chans],
                            "website": d.get("website", ""), "tone": d.get("tone", "plain"), "x_watchlist": d.get("x_watchlist") or [],
                            "done": bool(d.get("done"))},
                "options": {"content_types": [{"id": k, "label": v["label"]} for k, v in CONTENT_TYPES.items()], "tones": TONES,
                            "channels": [{"id": c["id"], "service": c["service"], "name": c.get("displayName") or c["name"]} for c in chans]},
                "brand": {k: v for k, v in self.h.kit(a).items() if k in ("name", "handle", "colors", "font", "logo_url", "is_set")},
                "x_key": bool(self._x_bearer(a)), "following": self._following(a)}

    def _following(self, a) -> list[dict]:
        from . import starter_feeds
        try:
            return starter_feeds.following(self.store, a.uid)
        except Exception:  # noqa: BLE001
            return []

    def _x_bearer(self, a) -> str:
        try:
            rows = self.store.q("SELECT secret FROM source_settings WHERE user_id=?", (a.uid,))
        except Exception:
            return ""
        sec = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        return sec.get("x_bearer", "")

    def put(self, a, body: dict) -> dict:
        self.h.studio._guard_write(a, "onboarding")
        cur = self._row(a.uid)["data"]
        if "content_types" in body:
            types = [t for t in body.get("content_types") or [] if t in CONTENT_TYPES]
            cur["content_types"] = types[:10]
        if "channel_ids" in body:
            ok = {c["id"] for c in self.h.studio.channels(a)}
            cur["channel_ids"] = [c for c in body.get("channel_ids") or [] if c in ok]
        if "tone" in body:
            cur["tone"] = body["tone"] if body["tone"] in TONES else "plain"
        if "x_watchlist" in body:
            items = []
            for raw in (body.get("x_watchlist") or [])[:25]:
                s = str(raw).strip()
                if reader.x_status(s):
                    items.append({"type": "post", "url": s.split("?")[0]})
                elif re.fullmatch(r"@?[A-Za-z0-9_]{1,15}", s):
                    items.append({"type": "account", "handle": s.lstrip("@")})
            cur["x_watchlist"] = items
            handles = [i["handle"] for i in items if i["type"] == "account"]
            if handles and self._x_bearer(a):  # the Sources agent reads them with the person's own key
                self.h.sources.save_settings(a, {"x_query": "(" + " OR ".join(f"from:{h}" for h in handles[:20]) + ") -is:retweet"})
        out = {}
        if body.get("website"):
            cur["website"] = str(body["website"]).strip()[:300]
            if body.get("build_brand", True):
                out["brand"] = self.brand_from_domain(a, cur["website"])
        if body.get("done") is not None:
            cur["done"] = bool(body["done"])
        self.store.x("INSERT INTO user_profiles(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
                     "updated=excluded.updated, recs_at=0", (a.uid, json.dumps(cur), time.time()))
        self.store.audit(a.uid, a.label, "onboarding", "read", {"fields": sorted(k for k in body if k != "website"), "website": bool(body.get("website"))})
        if "content_types" in body or body.get("done"):  # News follows the interests: matching starter feeds (studio/starter_feeds.py)
            from . import starter_feeds
            out["feeds"] = starter_feeds.sync(self.store, a.uid, cur.get("content_types") or [])
        return {**self.get(a), **out}

    # ---------------------------------------------------------------- appearance (light / dark / system), owner 1 Nov 2026
    MODES = ("system", "light", "dark")

    def appearance(self, a) -> dict:
        return {"mode": self._row(a.uid)["data"].get("appearance") or "system", "modes": list(self.MODES)}

    def set_appearance(self, a, mode: str) -> dict:
        mode = str(mode or "").lower()
        if mode not in self.MODES:
            raise ActionError("Appearance is system, light or dark.")
        if demo_guard.is_demo(a.uid):  # the shared sample keeps it in the browser only
            return {"mode": mode, "modes": list(self.MODES), "saved": "browser"}
        rows = self.store.q("SELECT data FROM user_profiles WHERE user_id=?", (a.uid,))
        cur = json.loads(rows[0]["data"] or "{}") if rows else {}
        cur["appearance"] = mode
        self.store.x("INSERT INTO user_profiles(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data, "
                     "updated=excluded.updated", (a.uid, json.dumps(cur), time.time()))
        return {"mode": mode, "modes": list(self.MODES), "saved": "account"}

    # ---------------------------------------------------------------- brand from a domain (context.dev)
    def brand_from_domain(self, a, website: str) -> dict:
        if demo_guard.is_demo(a.uid):
            return {"source": "sample", "kit": self.h.kit(a), "note": "The sample keeps its example brand."}
        host = re.sub(r"^https?://", "", website.strip()).split("/")[0].lower().removeprefix("www.")
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host or ""):
            raise OnboardingError("Give your brand's website, e.g. huhbyte.ai")
        key = os.environ.get("CONTEXT_DEV_API_KEY", "")
        if key:
            try:
                prof = self._context_dev(host, key)
                if prof:
                    return {"source": "context.dev", **self._apply_profile(a, prof, host)}
            except OnboardingError:
                pass
        try:  # fallback: read the site ourselves (logo, theme colour, og tags)
            got = self.h.kit_from_website(a, "https://" + host)
            return {"source": "website", "kit": got["kit"], "found": got.get("found")}
        except Exception as e:
            raise OnboardingError(f"Couldn't read a brand from {host}: {str(e)[:120]}")

    def _context_dev(self, host: str, key: str) -> dict | None:
        try:
            r = netguard.safe_post(CONTEXT_DEV, http=self.http, json={"type": "by_domain", "domain": host,
                                                                       "timeoutOpts": {"milliseconds": 45000, "behavior": "return-partial"}},
                                   headers={"Authorization": f"Bearer {key}"}, timeout=60, allowed_hosts={"api.context.dev"}, limit=2_000_000)
        except (netguard.FetchError, httpx.HTTPError) as e:
            raise OnboardingError(f"context.dev didn't answer ({e.__class__.__name__})")
        if r.status_code >= 400:
            raise OnboardingError(f"context.dev answered HTTP {r.status_code}")
        return (r.json() or {}).get("brand")

    def _apply_profile(self, a, b: dict, host: str) -> dict:
        cols = [c.get("hex") for c in b.get("colors") or [] if brand.HEX_RE.fullmatch((c.get("hex") or "").strip())]
        colors = brand.pick_colors(cols[0] if cols else "", cols[1:6])
        patch = {"name": (b.get("title") or host)[:60], "website": "https://" + (b.get("domain") or host), "colors": colors}
        if b.get("description") and not self.h.kit(a).get("voice"):
            patch["voice"] = (b.get("slogan") or b.get("description"))[:200]
        x = next((s.get("url") for s in b.get("socials") or [] if s.get("type") in ("x", "twitter", "instagram") and s.get("url")), "")
        if x:
            patch["handle"] = x.rstrip("/").rsplit("/", 1)[-1].lstrip("@")[:40]
        logos = sorted(b.get("logos") or [], key=lambda l: (l.get("type") != "logo", l.get("mode") != "dark",
                                                           str(l.get("url", "")).lower().endswith(".svg")))
        for lg in logos:
            u = lg.get("url") or ""
            if not u or u.lower().endswith(".svg"):
                continue  # Pillow can't draw SVG; take the raster variant
            data = self.h._fetch_bytes(u, 2_000_000)
            if data:
                try:
                    from PIL import Image
                    import io
                    Image.open(io.BytesIO(data)).verify()
                    patch["logo"] = self.h.put_media(a, data, "logo.png", kind="logo")
                    break
                except Exception:
                    continue
        kit = self.h.save_kit(a, patch)
        return {"kit": kit, "found": {"logo": bool(patch.get("logo")), "palette": cols[:6], "handle": patch.get("handle", "")}}

    # ---------------------------------------------------------------- recommendations
    def recommendations(self, a, refresh: bool = False) -> dict:
        row = self._row(a.uid)
        if demo_guard.is_demo(a.uid):
            return self._sample_recs(a, row["data"])  # no scan, no render, no model: the pre-made set
        now = time.time()
        if row["recs"] and not refresh and row["recs_at"] > now - RECS_TTL:
            return {**row["recs"], "cached": True}
        last = self.store.q("SELECT MAX(ts) AS t FROM audit WHERE user_id=? AND action='recs_build'", (a.uid,))[0]["t"] or 0
        wait = int(last + RECS_REFRESH - now)
        if wait > 0:  # rate limit: one rebuild an hour, whatever asked for it (refresh, new choices)
            if row["recs"]:
                return {**row["recs"], "cached": True, "stale": row["recs_at"] <= now - RECS_TTL, "next_refresh_in": wait}
            raise OnboardingError(f"Recommendations rebuild once an hour; try again in {max(1, wait // 60)} min.")
        if budget.remaining(self.store, a.uid)["left_usd"] < RECS_COST:
            if row["recs"]:
                return {**row["recs"], "cached": True, "stale": True, "note": "Your included budget is used up, so these aren't refreshed."}
            raise OnboardingError("Your included budget is used up, so I can't build recommendations. Add your own key in AI engine.")
        out = self._build_recs(a, row["data"])
        budget.charge_user(self.store, a.uid, RECS_COST)
        self.store.audit(a.uid, a.label, "recs_build", "read", {"cost_usd": RECS_COST, "refresh": bool(refresh), "recommended": len(out["recommended"])})
        return {**out, "cached": False}

    def _build_recs(self, a, prof: dict) -> dict:
        types = prof.get("content_types") or ["ai_news", "product_launches"]
        want_src = sorted({s for t in types for s in CONTENT_TYPES[t]["sources"]})
        settings = self.h.sources.settings(a)
        on = [s for s in want_src if settings["on"].get(s)]
        scan = self.h.sources.scan(a, {"sources": on or ["hn", "yc", "rss"]})
        feed = _filter(scan["clusters"], types)
        watch = []
        for w in (prof.get("x_watchlist") or [])[:5]:
            if w["type"] == "post":
                try:
                    p = reader.x_oembed(w["url"], self.http)
                    watch.append({"url": w["url"], "author": p["author_name"], "handle": p["handle"], "text": p["text"][:280]})
                except Exception:  # a post that won't read (deleted, odd answer) never breaks the screen
                    continue
        chans = self._chans(a, prof)
        recommended = [self._ready_post(a, c, chans, prof.get("tone", "plain")) for c in feed[:3]]
        out = {"run_id": scan["run_id"], "content_types": types, "feed": [{k: c[k] for k in ("id", "topic", "category", "lane", "score", "story",
                                                                                              "items", "why")} for c in feed[:20]],
               "recommended": recommended, "watchlist": watch, "sample": scan.get("sample", False), "at": time.time()}
        self.store.x("INSERT INTO user_profiles(user_id,data,recs,recs_at,updated) VALUES(?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                     "recs=excluded.recs, recs_at=excluded.recs_at", (a.uid, json.dumps(prof), json.dumps(out), time.time(), time.time()))
        return out

    def _chans(self, a, prof: dict) -> list[dict]:
        allc = self.h.studio.channels(a)
        return [c for c in allc if c["id"] in (prof.get("channel_ids") or [])] or [c for c in allc if not c.get("isDisconnected")]

    def _sample_recs(self, a, prof: dict) -> dict:
        """The sample workspace's recommendations: pre-made clusters and covers, filtered by the visitor's choices; the
        captions come from the rule writer. Nothing is fetched, rendered, stored or charged."""
        from .sources_agent import caption
        if "feed" not in _sample_cache:
            _sample_cache["feed"] = json.loads(SAMPLE_RECS.read_text())["feed"]
        types = prof.get("content_types") or ["ai_news", "product_launches"]
        feed = _filter(_sample_cache["feed"], types)
        chans = self._chans(a, prof)
        kit, rules = self.h.kit(a), self.h.rules(a)
        recommended = []
        for c in [c for c in feed if c.get("cover")][:3]:
            st, url = c["story"], (c["items"][0] or {}).get("url", "")
            recommended.append({"cluster_id": c["id"], "headline": st.get("headline"), "source_url": url, "cover": c["cover"],
                                "captions": [{"channel_id": ch["id"], "service": ch["service"],
                                              "text": brand.apply_text_rules(caption(ch["service"], st, kit), rules)} for ch in chans],
                                "make_post": {"prompt": f"Post about this in all my channels {url}".strip(), "channel_ids": [ch["id"] for ch in chans]},
                                "tone": prof.get("tone", "plain")})
        return {"run_id": None, "content_types": types, "feed": [{k: c[k] for k in ("id", "topic", "category", "lane", "score", "story", "items", "why")}
                                                                  for c in feed[:20]],
                "recommended": recommended, "watchlist": [], "sample": True, "static": True, "at": time.time(), "cached": True}

    def _ready_post(self, a, cluster: dict, chans: list[dict], tone: str) -> dict:
        from .sources_agent import caption
        kit, rules = self.h.kit(a), self.h.rules(a)
        p = brand.visual_params(kit, rules)
        st = cluster["story"]
        b, m = render.news_card(st.get("headline") or cluster["items"][0]["title"], kit, p, logo=self.h._logo_bytes(kit), ratio="4:5",
                                label=cluster["topic"], credit=st.get("source") or cluster["items"][0]["source"])
        tok = self.h.put_media(a, b, "rec.jpg", kind="render", meta=m)
        url = (cluster["items"][0] or {}).get("url", "")
        return {"cluster_id": cluster["id"], "headline": st.get("headline"), "source_url": url, "cover": self.h.media_url(tok),
                "captions": [{"channel_id": c["id"], "service": c["service"], "text": brand.apply_text_rules(caption(c["service"], st, kit), rules)}
                             for c in chans],
                "make_post": {"prompt": f"Post about this in all my channels {url}".strip(), "channel_ids": [c["id"] for c in chans]},
                "tone": tone}


def _filter(clusters: list[dict], types: list[str]) -> list[dict]:
    """The clusters that match the chosen content types, best lane first, then the closest match, then the score."""
    cats = set().union(*[CONTENT_TYPES[t]["cats"] for t in types])
    words = [w for t in types for w in CONTENT_TYPES[t]["words"]]
    feed = []
    for c in clusters:
        if c["lane"] == "hidden":
            continue
        txt = (c["story"].get("headline", "") + " " + " ".join(i["title"] for i in c["items"])).lower()
        match = (c["category"] in cats) + sum(1 for w in words if re.search(rf"\b{re.escape(w)}\b", txt))
        if match:
            feed.append({**c, "match": match, "why": _why(c, types, txt)})
    feed.sort(key=lambda c: ({"postable": 0, "idea_bank": 1, "low": 2}.get(c["lane"], 3), -c["match"], -c["score"]))
    return feed


def _why(c: dict, types: list[str], txt: str) -> str:
    hits = [CONTENT_TYPES[t]["label"] for t in types if c["category"] in CONTENT_TYPES[t]["cats"] or any(w in txt for w in CONTENT_TYPES[t]["words"])]
    return "Matches " + ", ".join(hits[:2]) if hits else ""


def interests(store, uid: str) -> list[str]:
    """The content types the person accepted in setup (user_profiles, per account). [] when they haven't picked any."""
    try:
        ensure(store)
        rows = store.q("SELECT data FROM user_profiles WHERE user_id=?", (uid,))
    except Exception:  # noqa: BLE001 - the feed never breaks on the profile
        return []
    d = json.loads(rows[0]["data"] or "{}") if rows else {}
    return [t for t in d.get("content_types") or [] if t in CONTENT_TYPES]


def rank_stories(stories: list[dict], types: list[str], limit: int = 30, via: dict | None = None) -> dict:
    """The For you feed, following the accepted interests: stories that match a chosen content type (by its words),
    closest match first, then newest; the same input always gives the same order, so a refresh never reshuffles or
    drops the interests. With no interests the newest stories stand. Nothing matching -> the newest, with a note."""
    if not types:
        return {"stories": stories[:limit], "interests": [], "off_interest": 0}
    words = {t: CONTENT_TYPES[t]["words"] for t in types}
    out = []
    for st in stories:
        txt = f" {st.get('title', '')} {st.get('summary', '')} ".lower()
        hits = [t for t, ws in words.items() if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", txt) for w in ws)]
        # a story from a starter feed picked for an interest counts for it too (an arXiv title rarely says "paper")
        extra = [t for t in (via or {}).get(st.get("feed_id"), ()) if t in words and t not in hits]
        if hits or extra:
            out.append({**st, "match": len(hits) + 0.5 * len(extra),
                        "why_for_you": ("Matches " if hits else "Picked for ") + ", ".join(CONTENT_TYPES[t]["label"] for t in (hits + extra)[:2])})
    out.sort(key=lambda x: (-x["match"], -(x.get("published") or 0), x.get("url") or ""))
    lab = [{"id": t, "label": CONTENT_TYPES[t]["label"]} for t in types]
    if not out:
        return {"stories": stories[:limit], "interests": lab, "off_interest": len(stories),
                "note": "Nothing in the feeds you follow matches your interests right now, so these are the newest stories."}
    return {"stories": out[:limit], "interests": lab, "off_interest": len(stories) - len(out)}
