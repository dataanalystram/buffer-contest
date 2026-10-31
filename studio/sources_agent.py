"""The Sources agent: finds news, rewrites it in the brand for each channel, and drafts it into Buffer (UI = REST = MCP).

    scan    pluggable sources (YC launches, Hacker News, RSS/Atom lab blogs, X with the user's own API key)
            -> dedupe (canonical URL + title) -> topic clusters (same story from several sources becomes one card)
            -> judgments per cluster (topic, kind, sensitive/unconfirmed) and decisions in code (lane, rank, what an
               agent may do), the News radar classifier design: Jev first, then SubQ, then rules (RADAR_CLASSIFIER.md)
    draft   one package per channel in the brand: the caption written to the channel's length, hashtag count and format,
            a breaking-news cover rendered in the brand kit at the channel's frame; dry run first, then Buffer DRAFTS
            through preview_post -> submit_post, so a person approves before anything is scheduled and an agent token's
            policy still holds. Nothing here ever publishes.

Security (work/plans/SECURITY_RULES.md): every live fetch goes through netguard.safe_get (no auto-redirects, every hop
re-checked, byte caps). The free sample workspace never fetches: it reads the recorded fixtures in sample_sources/raw/.
X is read only through the official API with the user's own key (sealed at rest); logged-in X is never scraped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from . import brand, demo_guard, drafter, netguard, radar, templates

FIXTURES = Path(__file__).parent / "sample_sources" / "raw"
UA = {"User-Agent": "Mozilla/5.0 (HuhQueue Sources agent; +https://huhqueue-studio.onrender.com)"}
MAX_ITEMS = 120
MAX_RSS = 8
SCANS_PER_DAY = 30          # live scans per real account per day
DEFAULT_RSS = ["https://openai.com/news/rss.xml", "https://deepmind.google/blog/rss.xml",
               "https://huggingface.co/blog/feed.xml", "https://blog.google/technology/ai/rss/"]
SAMPLE_RSS = {"openai.xml": "https://openai.com/news/rss.xml", "deepmind.xml": "https://deepmind.google/blog/rss.xml",
              "huggingface.xml": "https://huggingface.co/blog/feed.xml", "googleai.xml": "https://blog.google/technology/ai/rss/"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_settings (user_id TEXT PRIMARY KEY, data TEXT, secret TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS source_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, input TEXT, result TEXT, created REAL);
"""


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        store.x(stmt)
    if store.pg:
        for t in ("source_settings", "source_runs"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


class SourceError(ValueError):
    pass


# ================================================================================================ adapters
def _ts(raw) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(str(raw)).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _item(adapter: str, source: str, title: str, url: str, summary: str = "", published: float = 0.0, **extra) -> dict:
    return {"id": hashlib.sha256(url.encode()).hexdigest()[:12], "adapter": adapter, "source": source,
            "title": " ".join((title or "").split())[:300], "url": url, "summary": " ".join((summary or "").split())[:600],
            "published": published, "image": extra.pop("image", ""), "engagement": int(extra.pop("engagement", 0) or 0), **extra}


def _get(url: str, http, headers: dict | None = None, limit: int = 2_000_000) -> httpx.Response:
    """Every live source fetch: netguard.safe_get (hops re-checked, byte cap). SECURITY_RULES 1."""
    try:
        r = netguard.safe_get(url, http=http, headers={**UA, **(headers or {})}, limit=limit, timeout=12)
    except netguard.FetchError as e:
        raise SourceError(str(e))
    except httpx.HTTPError as e:
        raise SourceError(f"{urlparse(url).hostname} didn't answer ({e.__class__.__name__}).")
    if r.status_code == 401 or r.status_code == 403:
        raise SourceError(f"{urlparse(url).hostname} refused the request (HTTP {r.status_code}); check the key.")
    if r.status_code == 429:
        raise SourceError(f"{urlparse(url).hostname} is rate-limiting; try again later.")
    if r.status_code >= 400:
        raise SourceError(f"{urlparse(url).hostname} answered HTTP {r.status_code}.")
    return r


def parse_yc(data: dict) -> list[dict]:
    out = []
    for h in (data or {}).get("hits") or []:
        c = h.get("company") or {}
        url = h.get("search_path") or (f"https://www.ycombinator.com/launches/{h['slug']}" if h.get("slug") else "")
        if not url.startswith("https://") or not h.get("title"):
            continue
        out.append(_item("yc", "YC Launches", h["title"], url, h.get("tagline", ""), _ts(h.get("created_at")),
                         image=c.get("logo") or "", engagement=h.get("total_vote_count") or 0,
                         company=c.get("name", ""), company_url=c.get("url", ""), batch=c.get("batch", ""), tags=c.get("tags") or []))
    return out


def parse_hn(data: dict) -> list[dict]:
    out = []
    for h in (data or {}).get("hits") or []:
        if not h.get("title"):
            continue
        hn = f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        url = h.get("url") or hn
        out.append(_item("hn", "Hacker News", h["title"], url, "", float(h.get("created_at_i") or _ts(h.get("created_at"))),
                         engagement=h.get("points") or 0, comments=h.get("num_comments") or 0, discussion=hn))
    return out


def parse_x(data: dict) -> list[dict]:
    users = {u["id"]: u for u in ((data or {}).get("includes") or {}).get("users") or []}
    out = []
    for t in (data or {}).get("data") or []:
        u = users.get(t.get("author_id"), {})
        handle = u.get("username") or "x"
        text = t.get("text") or ""
        links = re.findall(r"https?://\S+", text)
        m = t.get("public_metrics") or {}
        out.append(_item("x", f"X @{handle}", re.sub(r"https?://\S+", "", text).strip()[:200],
                         links[-1] if links else f"https://x.com/{handle}/status/{t.get('id')}", "", _ts(t.get("created_at")),
                         engagement=(m.get("like_count") or 0) + 2 * (m.get("retweet_count") or 0), post=f"https://x.com/{handle}/status/{t.get('id')}"))
    return out


def parse_rss(body: bytes, feed_url: str) -> list[dict]:
    feed = radar.parse(body, feed_url)
    return [_item("rss", feed["title"], s["title"], s["url"], s.get("summary", ""), s.get("published") or 0.0, image=s.get("image", ""))
            for s in feed["stories"]]


class Adapter:
    key, label, about = "", "", ""
    needs_key = False

    def live(self, cfg: dict, secret: dict, http) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError

    def sample(self) -> list[dict]:  # pragma: no cover - interface
        raise NotImplementedError


class YC(Adapter):
    key, label, about = "yc", "YC Launches", "New launches from Y Combinator companies (ycombinator.com/launches)."

    def live(self, cfg, secret, http):
        r = _get("https://www.ycombinator.com/launches", http, {"Accept": "application/json"})
        try:
            return parse_yc(r.json())
        except ValueError:
            raise SourceError("YC Launches didn't return JSON.")

    def sample(self):
        return parse_yc(json.loads((FIXTURES / "yc.json").read_text()))


class HN(Adapter):
    key, label, about = "hn", "Hacker News", "The Hacker News front page (Algolia API), with points."

    def live(self, cfg, secret, http):
        q = (cfg.get("hn_query") or "").strip()[:80]
        url = "https://hn.algolia.com/api/v1/search?" + urlencode({"tags": "front_page", "hitsPerPage": 30, **({"query": q} if q else {})})
        try:
            return parse_hn(_get(url, http).json())
        except ValueError:
            raise SourceError("Hacker News didn't return JSON.")

    def sample(self):
        return parse_hn(json.loads((FIXTURES / "hn.json").read_text()))


class RSS(Adapter):
    key, label, about = "rss", "RSS / Atom", "Lab and company blogs, newsletters, any RSS or Atom feed."

    def live(self, cfg, secret, http):
        out, errors = [], []
        for url in (cfg.get("rss") or DEFAULT_RSS)[:MAX_RSS]:
            try:
                r = _get(url, http, limit=radar.MAX_BYTES)
                out += parse_rss(r.content, url)
            except (SourceError, drafter.FetchError) as e:
                errors.append(f"{urlparse(url).hostname}: {e}")
        if errors and not out:
            raise SourceError("; ".join(errors)[:300])
        return out

    def sample(self):
        out = []
        for name, url in SAMPLE_RSS.items():
            out += parse_rss((FIXTURES / name).read_bytes(), url)
        return out


class X(Adapter):
    key, label, about = "x", "X (your API key)", "Recent posts from accounts or searches you choose, through the official X API with your own key."
    needs_key = True

    def live(self, cfg, secret, http):
        key = (secret or {}).get("x_bearer", "")
        if not key:
            raise SourceError("Add your X API bearer token to read X. HuhQueue never scrapes logged-in X.")
        q = (cfg.get("x_query") or "(AI OR LLM) -is:retweet lang:en").strip()[:400]
        url = "https://api.x.com/2/tweets/search/recent?" + urlencode({
            "query": q, "max_results": 25, "tweet.fields": "created_at,public_metrics,author_id",
            "expansions": "author_id", "user.fields": "username,name"})
        try:
            return parse_x(_get(url, http, {"Authorization": f"Bearer {key}"}).json())
        except ValueError:
            raise SourceError("X didn't return JSON.")

    def sample(self):
        return parse_x(json.loads((FIXTURES / "x.json").read_text()))


ADAPTERS: dict[str, Adapter] = {a.key: a for a in (YC(), HN(), RSS(), X())}


# ================================================================================================ dedupe + clusters
STRIP_PARAMS = re.compile(r"^(utm_|ref$|ref_|fbclid$|gclid$|mc_|s$|src$)")
STOP = set("the a an and or of to in on for with by is are was at as from new its it this that how what why you your our "
           "we will be has have can just now into about after over more than not their they them his her us via vs".split())


def canonical(url: str) -> str:
    p = urlparse(url.strip())
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not STRIP_PARAMS.match(k.lower())])
    path = re.sub(r"/+$", "", p.path) or "/"
    return urlunparse(("https", host, path, "", q, ""))


def tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]", (text or "").lower()) if w not in STOP and len(w) > 2}


def dedupe(items: list[dict]) -> list[dict]:
    """One item per story link and per near-identical title; the copies are kept as `also` (which sources had it)."""
    by_url: dict[str, dict] = {}
    for it in items:
        k = canonical(it["url"])
        cur = by_url.get(k)
        if cur is None:
            by_url[k] = dict(it, also=[], canonical=k)
            continue
        cur["also"].append({"source": it["source"], "url": it["url"]})
        cur["engagement"] = max(cur["engagement"], it["engagement"])
        if len(it.get("summary", "")) > len(cur.get("summary", "")):
            cur["summary"] = it["summary"]
    out, seen = [], {}
    for it in by_url.values():
        tk = "".join(ch for ch in it["title"].lower() if ch.isalnum())[:70]
        if tk and tk in seen:
            seen[tk]["also"].append({"source": it["source"], "url": it["url"]})
            continue
        seen[tk] = it
        out.append(it)
    return out


def same_story(a: dict, b: dict) -> bool:
    """Code shortlists pairs (2+ salient title words shared, and half of the shorter title); the model would judge the
    borderline ones (same_event Noul, RADAR_CLASSIFIER.md). Without a model this rule decides."""
    ta, tb = tokens(a["title"]), tokens(b["title"])
    if not ta or not tb:
        return False
    shared = ta & tb
    return len(shared) >= 2 and len(shared) / min(len(ta), len(tb)) >= 0.5


def cluster(items: list[dict]) -> list[list[dict]]:
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i]["adapter"] == items[j]["adapter"] == "yc":
                continue  # two YC launches are two companies
            if same_story(items[i], items[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list[dict]] = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


# ================================================================================================ judgments + decisions
CATEGORY_KW = {  # the rules fallback of the radar classifier (radar_classify.py Rules)
    "model_release": r"\b(model|weights|gpt-?\d|gemini|llama|claude|mistral|qwen|nemotron|benchmark|frontier|open[- ]source model)\b",
    "product_feature": r"\b(app|feature|launch(es|ed)?|rolls? out|update|now available|assistant|introduc|platform|tool)\b",
    "research": r"\b(paper|research(ers)?|study|dataset|arxiv|technique|science|brain|quest)\b",
    "business_funding": r"\b(raises?|funding|valuation|acquir|series [a-d]|revenue|layoffs?|ipo|deal|investors?|stock|credit card)\b",
    "policy_law": r"\b(lawsuit|sue[sd]?|court|regulat|law|bill|senate|standard|copyright|opt out|judge|prefer|parliament|elect)\b",
    "safety_society": r"\b(safety|harm|misuse|deepfake|jobs|workers|weapons|ethic|privacy|schools?|education)\b",
    "hardware_infra": r"\b(chips?|gpu|nvidia|data ?cent(er|re)s?|compute|power|energy|tpu|server|inference)\b",
    "howto_tools": r"\b(how to|guide|tutorial|notes on|walkthrough|cookbook|tips|build|free way)\b",
}
TOPIC = {"model_release": "Models", "product_feature": "Launch", "research": "Research", "business_funding": "Business",
         "policy_law": "Policy", "safety_society": "Society", "hardware_infra": "Hardware", "howto_tools": "How-to"}
AI_RE = re.compile(r"\b(ai|a\.i\.|model|models|llm|gpt|gemini|claude|machine learning|agents?|neural|inference|genai)\b", re.I)


def judge(it: dict, brand_terms: set[str]) -> dict:
    """Rules judgments with the classifier's answer shape (Jev/SubQ answer the same questions when configured)."""
    txt = f"{it['title']} {it.get('summary', '')} {' '.join(it.get('tags') or [])}".lower()
    hits = {k: len(re.findall(p, txt)) for k, p in CATEGORY_KW.items()}
    cat = max(hits, key=lambda k: hits[k]) if any(hits.values()) else "product_feature"
    if it["adapter"] == "yc":
        cat = "product_feature"
    about_ai = 0.9 if AI_RE.search(txt) else 0.1
    evergreen = bool(re.search(r"\b(how to|guide|tutorial|explained|what is|lessons|notes on|free way)\b", txt))
    rel = min(3, len(tokens(txt) & brand_terms))
    return {"about_ai": about_ai, "category": cat, "kind": "evergreen" if evergreen else "news",
            "relevance": rel, "unconfirmed": 0.9 if re.search(r"\b(reportedly|rumou?r|leak|sources say|expected to|could)\b", txt) else 0.1,
            "sensitive": 0.9 if re.search(r"\b(death|died|suicide|killed|violence|weapons?|abuse|shooting|disaster)\b", txt) else 0.1,
            "allegation": 0.9 if re.search(r"\b(lawsuit|sued|accus|alleg|investigat)\b", txt) else 0.1,
            "format": "carousel" if evergreen or len(re.findall(r"\d", txt)) > 3 else "single_card", "provider": "rules"}


def decide(group: list[dict], j: dict, now: float) -> dict:
    """Judgments -> lane, rank score, what an agent may do (RADAR_CLASSIFIER.md §4: code decides, not the model)."""
    lead = group[0]
    hrs = (now - lead["published"]) / 3600 if lead["published"] else 999
    fresh = max(0.0, 1 - hrs / 168)
    pop = min(1.0, max(it["engagement"] for it in group) / 400)
    breadth = min(1.0, (len(group) - 1 + sum(len(it.get("also") or []) for it in group)) / 3)
    score = 0.35 * (j["relevance"] / 3) + 0.25 * fresh + 0.2 * pop + 0.2 * breadth
    if lead["title"].lower().startswith("sample:"):
        score -= 0.3  # made-up sample posts never outrank recorded real stories
    if j["about_ai"] < 0.3:
        lane = "hidden" if lead["adapter"] in ("hn", "x") else "low"
    elif j["kind"] == "evergreen":
        lane = "idea_bank"
    else:
        lane = "postable"
    flags = [k for k in ("sensitive", "allegation", "unconfirmed") if j[k] >= 0.7]
    agent = "person_only" if ("sensitive" in flags or "allegation" in flags) else ("verify_first" if flags or len(group) == 1 and lead["adapter"] == "x" else "agent_ok")
    return {"lane": lane, "score": round(score, 3), "agent": agent, "flags": flags}


# ================================================================================================ captions per channel
STYLE = {  # what each channel wants from a news post: caption target, hashtags, the frame, the format
    "instagram": {"target": 900, "hashtags": 5, "format": "carousel", "link": "bio"},
    "youtube": {"target": 280, "hashtags": 3, "format": "short", "link": "description", "title": 95},
    "tiktok": {"target": 300, "hashtags": 4, "format": "short", "link": "bio"},
    "twitter": {"target": 260, "hashtags": 1, "format": "card", "link": "inline"},
    "linkedin": {"target": 1100, "hashtags": 3, "format": "carousel", "link": "inline"},
    "threads": {"target": 430, "hashtags": 1, "format": "card", "link": "inline"},
    "bluesky": {"target": 280, "hashtags": 0, "format": "card", "link": "inline"},
    "facebook": {"target": 600, "hashtags": 2, "format": "card", "link": "inline"},
    "mastodon": {"target": 440, "hashtags": 2, "format": "card", "link": "inline"},
}


GENERIC = set("introducing introduces announcing meet open models model new our today launch launches launched ai the how why what "
              "with free faster better first now here build building making make get using use your sample platform platforms".split())
TOPIC_TAG = {"model_release": "#OpenModels", "product_feature": "#Startups", "research": "#AIResearch", "business_funding": "#Startups",
             "policy_law": "#AIPolicy", "safety_society": "#AISafety", "hardware_infra": "#AIChips", "howto_tools": "#AITools"}
WHY = {"model_release": "Another model to weigh up: check the licence, the size and the price before you switch.",
       "product_feature": "A new tool to try if you have this exact problem.",
       "research": "Early work, but it shows where the field is heading.",
       "business_funding": "Follow the money: it shows what investors think comes next.",
       "policy_law": "Rules like this change what products can ship, and where.",
       "safety_society": "This is about how AI lands on real people, not just on benchmarks.",
       "hardware_infra": "Cheaper, faster compute is what makes AI cheaper for everyone.",
       "howto_tools": "Free, and you can try it today."}


def _hashtags(story: dict, kit: dict, n: int) -> list[str]:
    """The brand's own tags first, then the story's topic tag, then names from the headline (product and company names:
    capitalised words that aren't the first word or a generic one)."""
    tags = [t if t.startswith("#") else "#" + t for t in (kit.get("hashtags") or [])]
    if story.get("topic_tag"):
        tags.append(story["topic_tag"])
    if story.get("company") and re.fullmatch(r"[A-Za-z0-9]+", story["company"].replace(" ", "")):
        tags.append("#" + story["company"].replace(" ", ""))
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", story.get("headline") or story.get("title") or "")
    names = [w for i, w in enumerate(words) if w[0].isupper() and w.lower() not in GENERIC and w.lower() not in STOP and len(w) > 2
             and (i > 0 or any(ch.isupper() for ch in w[1:]) or any(ch.isdigit() for ch in w))]
    names.sort(key=lambda w: (not (any(ch.isupper() for ch in w[1:]) or any(ch.isdigit() for ch in w)), words.index(w)))
    for w in names:
        t = "#" + w
        if t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    return tags[:n]


def _sentence(s: str, n: int = 220) -> str:
    s = " ".join((s or "").split())
    first = re.split(r"(?<=[.!?])\s+", s)[0] if s else ""
    return first[:n].rstrip()


def caption(service: str, story: dict, kit: dict) -> str:
    """The built-in writer: one caption per channel in the channel's own shape. The brand's AI engine, when set, rewrites
    it in the brand voice; either way the Style Ledger's text rules and Buffer's character count apply after."""
    st = STYLE.get(service, STYLE["facebook"])
    head = story["headline"]
    line = story.get("line") or ""
    why = story.get("why") or ""
    url = story.get("url") or ""
    tags = " ".join(_hashtags(story, kit, st["hashtags"]))
    via = story.get("source") or ""
    if service == "twitter":
        body = f"{head.rstrip('.!?')}. {line or why}".strip()
        text = f"{body}\n\n{url}" + (f" {tags}" if tags else "")
    elif service == "linkedin":
        bullets = "\n".join(f"→ {b}" for b in story.get("facts") or [] if b)
        text = (f"{head}\n\n{line}\n\n{bullets}\n\nWhy it matters: {why}\n\nSource: {via} {url}\n\n{tags}").replace("\n\n\n", "\n\n")
    elif service == "instagram":
        bullets = "\n".join(f"• {b}" for b in story.get("facts") or [] if b)
        text = f"{head.upper() if kit.get('font') == 'anton' else head}\n\n{line}\n\n{bullets}\n\nWhy it matters: {why}\n\nSource: {via} (link in bio)\n\n{tags}"
    elif service in ("youtube", "tiktok"):
        text = f"{head}\n\n{(line + ' ' + why).strip()}\n\n{('Source: ' + url) if service == 'youtube' else 'Source: ' + via}\n\n{tags}"
    elif service == "bluesky":
        text = f"{head.rstrip('.!?')}. {line}\n\n{url}"
    else:
        text = f"{head}\n\n{(line + ' ' + why).strip()}\n\n{url}" + (f"\n\n{tags}" if tags else "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def story_from(group: list[dict]) -> dict:
    lead = max(group, key=lambda it: (it["adapter"] != "hn", len(it.get("summary") or ""), it["engagement"]))
    others = [it for it in group if it is not lead] + [dict(a, adapter="also") for it in group for a in it.get("also") or []]
    title = re.sub(r"\s*[|\-–]\s*[^|\-–]{2,30}$", "", lead["title"]).strip() or lead["title"]
    line = _sentence(lead.get("summary") or "", 200)
    if line.lower().startswith(title.lower()[:40]):
        line = ""
    if line and not line.endswith((".", "!", "?")):
        line += "."
    tk = tokens(title)
    facts = [s for s in re.split(r"(?<=[.!?])\s+", lead.get("summary") or "") if 25 < len(s) < 180
             and len(tokens(s) & tk) < max(2, len(tokens(s)) // 2) and s.rstrip(".!? ") != line.rstrip(".!? ")][:3]
    if lead["adapter"] == "yc":
        facts = [f"{lead.get('company') or 'The company'} is in YC {lead.get('batch') or ''}".strip(), *facts][:3]
        line = line or lead.get("summary", "")
    return {"headline": title[:110], "line": line, "facts": facts, "why": "", "url": lead.get("company_url") if lead["adapter"] == "yc" and not lead.get("url") else lead["url"],
            "source": lead["source"], "also": sorted({o["source"] for o in others if o.get("source")})[:4], "image": lead.get("image", ""),
            "company": lead.get("company", "")}


def finish_story(st: dict, category: str) -> dict:
    """Topic-aware 'why it matters' and topic hashtag (the built-in writer; the brand's AI engine writes its own)."""
    st["why"] = WHY.get(category, WHY["product_feature"])
    if st.get("also"):
        st["why"] += f" Also covered by {', '.join(st['also'][:2])}."
    st["topic_tag"] = TOPIC_TAG.get(category, "#AI")
    return st


# ================================================================================================ the service
class SourcesAgent:
    def __init__(self, house):
        self.house, self.studio, self.store, self.http = house, house.studio, house.store, house.http
        ensure(self.store)

    # ---------------------------------------------------------------- settings
    def settings(self, a) -> dict:
        rows = self.store.q("SELECT data, secret FROM source_settings WHERE user_id=?", (a.uid,))
        cfg = json.loads(rows[0]["data"]) if rows else {}
        sec = self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}
        on = {k: cfg.get("on", {}).get(k, k != "x") for k in ADAPTERS}
        return {"on": on, "rss": cfg.get("rss") or DEFAULT_RSS, "x_query": cfg.get("x_query", ""), "hn_query": cfg.get("hn_query", ""),
                "x_key": ("…" + sec["x_bearer"][-4:]) if sec.get("x_bearer") else "",
                "adapters": [{"key": ad.key, "label": ad.label, "about": ad.about, "needs_key": ad.needs_key} for ad in ADAPTERS.values()],
                "sample": demo_guard.is_demo(a.uid)}

    def save_settings(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "sources_settings")
        rows = self.store.q("SELECT data, secret FROM source_settings WHERE user_id=?", (a.uid,))
        cfg = json.loads(rows[0]["data"]) if rows else {}
        sec = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        if isinstance(body.get("on"), dict):
            cfg["on"] = {k: bool(body["on"].get(k, cfg.get("on", {}).get(k, k != "x"))) for k in ADAPTERS}
        if isinstance(body.get("rss"), list):
            feeds = []
            for u in body["rss"][:MAX_RSS]:
                u = str(u).strip()
                try:
                    netguard.check_url(u, check_host=not demo_guard.is_demo(a.uid))
                except netguard.FetchError as e:
                    raise SourceError(f"{u[:80]}: {e}")
                feeds.append(u[:400])
            cfg["rss"] = feeds
        for k in ("x_query", "hn_query"):
            if k in body:
                cfg[k] = str(body[k] or "")[:400]
        if "x_key" in body:
            if a.kind == "agent":
                raise SourceError("Only a person can add or remove the X API key.")
            key = str(body.get("x_key") or "").strip()
            if key and not re.fullmatch(r"[A-Za-z0-9%_\-.=]{20,200}", key):
                raise SourceError("That doesn't look like an X API bearer token.")
            if key:
                sec["x_bearer"] = key
                cfg.setdefault("on", {})["x"] = True
            else:
                sec.pop("x_bearer", None)
        self.store.x("INSERT INTO source_settings(user_id,data,secret,updated) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                     "data=excluded.data, secret=excluded.secret, updated=excluded.updated",
                     (a.uid, json.dumps(cfg), self.store.seal(sec) if sec else "", time.time()))
        self.store.audit(a.uid, a.label, "sources_settings", "read", {"fields": sorted(k for k in body if k != "x_key"),
                                                                      "x_key": "set" if body.get("x_key") else ("removed" if "x_key" in body else "")})
        return self.settings(a)

    # ---------------------------------------------------------------- scan
    def scan(self, a, body: dict | None = None) -> dict:
        body = body or {}
        self.studio._guard_write(a, "sources_scan")
        sample = demo_guard.is_demo(a.uid) or bool(body.get("sample"))
        cfg = self.settings(a)
        rows = self.store.q("SELECT secret FROM source_settings WHERE user_id=?", (a.uid,))
        secret = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        want = [k for k in (body.get("sources") or [k for k, v in cfg["on"].items() if v]) if k in ADAPTERS]
        if sample and "x" not in want and not body.get("sources"):
            want.append("x")  # the sample shows what the X adapter looks like (made-up posts, no call)
        if not sample:
            day = time.time() - 86400
            used = self.store.q("SELECT COUNT(*) AS n FROM audit WHERE user_id=? AND action='sources_scan' AND ok=1 AND ts>?", (a.uid, day))[0]["n"]
            if used >= SCANS_PER_DAY:
                raise SourceError(f"That's {SCANS_PER_DAY} live scans today. The cap resets in 24 hours.")
        items, status = [], []
        for k in want:
            ad = ADAPTERS[k]
            t0 = time.time()
            try:
                got = ad.sample() if sample else ad.live(cfg, secret, self.http)
                status.append({"source": k, "label": ad.label, "ok": True, "items": len(got), "ms": int((time.time() - t0) * 1000),
                               "mode": "recorded sample" if sample else "live"})
                items += got
            except SourceError as e:
                status.append({"source": k, "label": ad.label, "ok": False, "error": str(e), "items": 0})
        items = items[:MAX_ITEMS]
        now = float(body.get("now") or time.time())
        unique = dedupe(items)
        kit = self.house.kit(a)
        terms = tokens(" ".join([kit.get("voice") or "", " ".join(kit.get("hashtags") or []),
                                 "ai model models tool tools agent agents open launch startup developer"]))
        clusters = []
        for g in cluster(unique):
            g.sort(key=lambda it: -it["engagement"])
            j = judge(g[0], terms)
            dec = decide(g, j, now)
            st = finish_story(story_from(g), j["category"])
            clusters.append({"id": hashlib.sha256("|".join(sorted(it["id"] for it in g)).encode()).hexdigest()[:10],
                             "topic": TOPIC[j["category"]], "category": j["category"], "judgments": j, **dec, "story": st,
                             "items": [{k: it[k] for k in ("id", "adapter", "source", "title", "url", "published", "engagement")} for it in g],
                             "sources": sorted({it["adapter"] for it in g})})
        clusters.sort(key=lambda c: ({"postable": 0, "idea_bank": 1, "low": 2, "hidden": 3}[c["lane"]], -c["score"]))
        result = {"sample": sample, "status": status, "counts": {"fetched": len(items), "unique": len(unique), "clusters": len(clusters)},
                  "topics": _count([c["topic"] for c in clusters if c["lane"] != "hidden"]), "clusters": clusters[:60],
                  "classifier": "rules (Jev and SubQ answer the same questions when a key is configured)", "at": time.time()}
        rid = self.store.x("INSERT INTO source_runs(user_id,input,result,created) VALUES(?,?,?,?)",
                           (a.uid, json.dumps({"sources": want, "sample": sample}), json.dumps(result), time.time()))
        self.store.audit(a.uid, a.label, "sources_scan", "read", {"run": rid, "sources": want, "sample": sample, "items": len(items)})
        return {"run_id": rid, **result}

    def get_run(self, a, rid: int) -> dict:
        rows = self.store.q("SELECT * FROM source_runs WHERE id=? AND user_id=?", (int(rid), a.uid))
        if not rows:
            raise SourceError("No such Sources run.")
        return {"run_id": rows[0]["id"], **json.loads(rows[0]["result"])}

    def latest(self, a) -> dict | None:
        rows = self.store.q("SELECT id FROM source_runs WHERE user_id=? ORDER BY id DESC LIMIT 1", (a.uid,))
        return self.get_run(a, rows[0]["id"]) if rows else None

    # ---------------------------------------------------------------- draft
    def draft(self, a, body: dict) -> dict:
        """Packages for the chosen clusters on the chosen channels. dry_run (default) returns them; dry_run false sends
        each one to Buffer as a DRAFT (preview_post -> submit_post), so the agent token's policy still applies."""
        run = self.get_run(a, int(body.get("run_id") or 0))
        want = set(body.get("cluster_ids") or [])
        picks = [c for c in run["clusters"] if c["id"] in want] if want else [c for c in run["clusters"] if c["lane"] == "postable"][:1]
        if not picks:
            raise SourceError("Pick at least one story cluster from the scan.")
        if len(picks) > 5:
            raise SourceError("Draft at most 5 stories at a time.")
        chans = [c for c in self.studio.channels(a) if not c.get("isDisconnected")]
        if body.get("channel_ids"):
            chans = [c for c in chans if c["id"] in set(body["channel_ids"])]
        if not chans:
            raise SourceError("Pick at least one connected channel.")
        dry = body.get("dry_run", True) is not False
        if not dry:
            self.studio._guard_write(a, "sources_draft")
        for c in picks:
            if c["agent"] == "person_only" and a.kind == "agent":
                raise SourceError(f"“{c['story']['headline'][:60]}” touches a sensitive subject, so only a person can draft it.")
        kit = self.house.kit(a)
        from .brand_studio import BrandStudio
        bs = BrandStudio(self.house)
        bkit = bs.kit(a)
        rules = self.house.rules(a)
        eng = None
        if not dry:
            from . import llm
            eng = llm.resolve(self.store, a.uid, self.studio.s)
        out = []
        for c in picks:
            st = c["story"]
            rewritten = self._rewrite(a, eng, st, kit, [ch["service"] for ch in chans]) if eng else {}
            covers: dict[str, str] = {}
            for ch in chans:
                svc = ch["service"]
                text = rewritten.get(svc) or caption(svc, st, bkit)
                text = brand.apply_text_rules(text, rules)
                text = drafter.fit(svc, text)
                ratio = templates.CHANNELS.get(svc, templates.CHANNELS["instagram"])["ratio"]
                if ratio not in covers:
                    covers[ratio] = bs.render_cover(a, st, ratio, bkit) if not dry else ""
                pkg = {"cluster_id": c["id"], "headline": st["headline"], "channel_id": ch["id"], "service": svc,
                       "channel_name": ch.get("displayName") or ch.get("name"), "format": STYLE.get(svc, STYLE["facebook"])["format"],
                       "ratio": ratio, "text": text, "length": drafter.count(svc, text), "limit": drafter.limit_for(svc),
                       "hashtags": len(brand.HASHTAG_RE.findall(text)), "media_url": covers[ratio], "agent": c["agent"],
                       "check": brand.brand_check(text, None, rules, kit)}
                if not dry:
                    pkg["result"] = self._send(a, pkg)
                out.append(pkg)
        if not dry:
            self.store.audit(a.uid, a.label, "sources_draft", "draft", {"run": run["run_id"], "clusters": [c["id"] for c in picks],
                                                                          "packages": len(out)})
        return {"dry_run": dry, "packages": out, "engine": (eng or {}).get("source", "built-in writer"),
                "note": "Nothing is published: each package becomes a Buffer draft (or waits in Approvals for an agent token)."}

    def _send(self, a, pkg: dict) -> dict:
        from .actions import ActionError
        p = {"channel_id": pkg["channel_id"], "text": pkg["text"], "mode": "draft", "override_warnings": True}
        if pkg["media_url"]:
            p["media_url"] = pkg["media_url"]
        try:
            pv = self.studio.preview_post(a, p)
            r = self.studio.submit_post(a, pv["confirm_token"])
        except ActionError as e:
            return {"status": "blocked", "error": str(e)}
        except Exception as e:  # a Buffer error on one channel never stops the others
            return {"status": "failed", "error": str(e)[:200]}
        return {"status": r.get("status"), "post_id": (r.get("post") or {}).get("id"), "approval_id": r.get("approval_id")}

    def _rewrite(self, a, eng, st: dict, kit: dict, services: list[str]) -> dict:
        from . import llm
        eng = self.house._ai_reserve(a, eng)
        if not eng:
            return {}
        try:
            d = llm.chat_json(eng, "You write social posts for a brand. Use only the facts given; never invent numbers or quotes.",
                              "Brand voice: " + (kit.get("voice") or "plain, clear") + "\nWrite one post per channel as JSON {service: text}. "
                              "Channel shapes: " + json.dumps({s: STYLE.get(s, STYLE["facebook"]) for s in services}) +
                              "\nStory: " + json.dumps({k: st[k] for k in ("headline", "line", "facts", "why", "url", "source")}),
                              http=self.http, max_tokens=1400)
            self.house._ai_audit(a, eng, "sources_draft")
        except Exception:
            return {}
        return {s: str(d.get(s))[:3000] for s in services if isinstance(d, dict) and d.get(s)}


def _count(xs: list[str]) -> list[dict]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return [{"topic": k, "n": v} for k, v in sorted(out.items(), key=lambda kv: -kv[1])]
