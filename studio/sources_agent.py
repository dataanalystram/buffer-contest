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

from . import brand, demo_guard, drafter, netguard, radar, sources, templates

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
CREATE TABLE IF NOT EXISTS classifier_usage (day TEXT, user_id TEXT, provider TEXT, n INTEGER, PRIMARY KEY (day, user_id, provider));
CREATE TABLE IF NOT EXISTS source_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, input TEXT, result TEXT, created REAL);
"""


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        store.x(stmt)
    if store.pg:
        for t in ("source_settings", "source_runs", "classifier_usage"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    sources.ensure_library(store)


class SourceError(ValueError):
    status = 400


class SourceBusy(SourceError):
    status = 429  # the per-workspace Refresh limit


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


def parse_ph(data: dict) -> list[dict]:
    out = []
    for e in (((data or {}).get("data") or {}).get("posts") or {}).get("edges") or []:
        n = e.get("node") or {}
        url = n.get("url") or n.get("website") or ""
        if not n.get("name") or not url.startswith("https://"):
            continue
        topics = [t["node"]["name"] for t in ((n.get("topics") or {}).get("edges") or []) if (t.get("node") or {}).get("name")]
        out.append(_item("ph", "Product Hunt", f"{n['name']}: {n.get('tagline', '')}".strip(": "), url, n.get("tagline", ""),
                         _ts(n.get("createdAt")), engagement=n.get("votesCount") or 0, company=n["name"], tags=topics))
    return out


def _post(url: str, http, body: dict, headers: dict) -> httpx.Response:
    """POST to a fixed public API through the safe-fetch helper (netguard.safe_post: host checked on this call, pinned,
    no redirects, body capped). Older netguard without safe_post: the same checks by hand."""
    host = (urlparse(url).hostname or "").lower()
    try:
        if hasattr(netguard, "safe_post"):
            return netguard.safe_post(url, http=http, json=body, headers={**UA, **headers}, timeout=15, allowed_hosts={host}, limit=2_000_000)
        netguard.check_url(url, https_only=True)
        c = http or netguard.client(15)
        r = c.post(url, json=body, headers={**UA, **headers}, follow_redirects=False)
        if r.status_code in netguard.REDIRECTS:
            raise netguard.FetchError("The API answered with a redirect, which is not followed.")
        if len(r.content) > 2_000_000:
            raise netguard.FetchError("The API's answer is too large.")
        return r
    except netguard.FetchError as e:
        raise SourceError(str(e))
    except httpx.HTTPError as e:
        raise SourceError(f"{host} didn't answer ({e.__class__.__name__}).")


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


class ProductHunt(Adapter):
    key, label, about = "ph", "Product Hunt", ("Today's top launches. HuhQueue reads Product Hunt once for everyone (its own API app, or the "
                                               "public feed) and caches it; your own developer token is an optional override.")
    needs_key = False
    QUERY = ("query { posts(first: 20, order: VOTES) { edges { node { id name tagline url website votesCount createdAt "
             "topics(first: 3) { edges { node { name } } } } } } }")

    def live(self, cfg, secret, http):
        key = (secret or {}).get("ph_token", "")
        if not key:  # the shared, app-level read (studio/appkeys.py): app token or public feed, cached globally
            from . import appkeys
            return appkeys.ph_shared(http, self.QUERY)[0]
        r = _post("https://api.producthunt.com/v2/api/graphql", http, {"query": self.QUERY}, {"Authorization": f"Bearer {key}"})
        if r.status_code in (401, 403):
            raise SourceError("Product Hunt refused the token; check it in your Product Hunt API dashboard.")
        if r.status_code >= 400:
            raise SourceError(f"Product Hunt answered HTTP {r.status_code}.")
        try:
            return parse_ph(r.json())
        except ValueError:
            raise SourceError("Product Hunt didn't return JSON.")

    def sample(self):
        return parse_ph(json.loads((FIXTURES / "producthunt.json").read_text()))


class Library(Adapter):
    key, label, about = "library", "Source library", ("Free feeds by category: launches, AI labs, tech news, research papers, "
                                                       "official videos and open source (News › Sources). Shared cached copies, no keys.")

    def live(self, cfg, secret, http):
        store, off = cfg["_store"], set(cfg.get("lib_off") or [])
        sources.refresh_library(store, [i for i in sources.BY_ID if i not in off], http)  # only copies older than the TTL
        out = []
        for it in sources.library_view(store, off)["items"]:
            if it.get("evergreen"):
                continue  # evergreen video picks are not news to post
            out.append(_item("library", it["source"], it["title"], it["url"], it.get("summary", ""), it["published"],
                             image=it.get("image", ""), engagement=it.get("engagement", 0), type=it["type"]))
        return out[:60]

    def sample(self):  # the sample workspace's scan stays on its recorded fixtures (the scan skips this adapter there)
        return []


ADAPTERS: dict[str, Adapter] = {a.key: a for a in (YC(), HN(), RSS(), ProductHunt(), X(), Library())}
KEYED = {"x": ("x_bearer", r"[A-Za-z0-9%_\-.=]{20,200}", "X API bearer token"),
         "ph": ("ph_token", r"[A-Za-z0-9_\-.=]{20,200}", "Product Hunt developer token")}


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
    if it.get("type") == "paper":  # a research paper is never presented as a product someone can use
        cat = "research"
    about_ai = 0.9 if AI_RE.search(txt) else 0.1
    evergreen = bool(re.search(r"\b(how to|guide|tutorial|explained|what is|lessons|notes on|free way)\b", txt))
    rel = min(3, len(tokens(txt) & brand_terms))
    return {"about_ai": about_ai, "category": cat, "kind": "evergreen" if evergreen else "news",
            "relevance": rel, "unconfirmed": 0.9 if re.search(r"\b(reportedly|rumou?r|leak|sources say|expected to|could)\b", txt) else 0.1,
            "sensitive": 0.9 if re.search(r"\b(death|died|suicide|killed|violence|weapons?|abuse|shooting|disaster)\b", txt) else 0.1,
            "allegation": 0.9 if re.search(r"\b(lawsuit|sued|accus|alleg|investigat)\b", txt) else 0.1,
            "format": "carousel" if evergreen or len(re.findall(r"\d", txt)) > 3 else "single_card", "provider": "rules"}


# ---- System One judges: Jev first, then SubQ, then the rules (RADAR_CLASSIFIER.md §5). The app's own keys, capped per account
# per day; the sample never calls them (its scan is deterministic). A failed call is an error, never read as a "no".
SCHEMA_VERSION = "sources-q-2026-10-31.1"
JUDGE_CAP = int(os.environ.get("HQ_JUDGE_CAP", "150"))        # judged stories per account per day, across providers
JUDGE_PER_SCAN = 16
PROVIDERS = [  # (name, url, model, env key)
    ("jev", "https://api.typesafe.ai/v1/systemone", "jev-latest", "TYPESAFE_API_KEY"),
    ("subq", "https://api.subq.ai/v1/systemone", "subq-snap-1.1", "SUBQ_API_KEY"),
]
_judged: dict[str, dict] = {}  # ponytail: per-process cache by schema + url


def classifier_view() -> dict:
    live = [n for n, _, _, env in PROVIDERS if os.environ.get(env)]
    return {"order": live + ["rules"], "cap_per_day": JUDGE_CAP, "configured": bool(live)}


def judge_questions(cats: dict) -> dict:
    return {
        "about_ai": {"type": "noul", "instructions": "Is `story` about artificial intelligence or machine learning?",
                     "criteria": {"true": "AI, models or AI products are the main subject", "false": "AI is absent or only mentioned in passing"}},
        "category": {"type": "choice", "instructions": "What is `story` mainly about?", "criteria": cats},
        "kind": {"type": "choice", "instructions": "What kind of piece is `story`?",
                 "criteria": {"news": "Reports one specific thing that happened", "evergreen": "A guide, explainer or tutorial still useful in three months",
                              "promo": "A company marketing its own product with no outside news angle"}},
        "relevance": {"type": "score", "instructions": "How relevant is `story` to `brand`?",
                      "criteria": ["Nothing in it touches what the brand covers", "Loosely related, the audience would not act on it",
                                   "Clearly about what the brand covers; some of the audience would care", "Exactly what the audience follows the brand for"]},
        "unconfirmed": {"type": "noul", "instructions": "Does `story` report a rumor, leak, forecast or unconfirmed claim?"},
        "sensitive": {"type": "noul", "instructions": "Does `story` involve death, violence, weapons, self-harm, abuse or a disaster?"},
        "allegation": {"type": "noul", "instructions": "Does `story` describe an accusation or lawsuit against a named person or company?"},
    }


CATS = {"model_release": "A new or updated AI model is released or benchmarked", "product_feature": "A product, app or feature launches or changes",
        "research": "A research paper, technique, dataset or scientific finding", "business_funding": "Funding, deals, revenue or company strategy",
        "policy_law": "Rules, regulation, lawsuits, standards or copyright", "safety_society": "Harms, safety, jobs, ethics, effects on people",
        "hardware_infra": "Chips, data centers, energy or compute", "howto_tools": "A tutorial, guide or hands-on note about a tool"}


def _use(store, uid: str, provider: str, add: int = 0) -> int:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if add:
        store.x("INSERT INTO classifier_usage(day,user_id,provider,n) VALUES(?,?,?,?) ON CONFLICT(day,user_id,provider) DO UPDATE SET "
                "n=classifier_usage.n+excluded.n", (day, uid, provider, add))
    return sum(r["n"] for r in store.q("SELECT n FROM classifier_usage WHERE day=? AND user_id=?", (day, uid)))


def model_judge(store, uid: str, it: dict, brand_state: dict, http=None) -> dict | None:
    """One System One request per story (all questions at once), Jev then SubQ; None when neither answers or the
    account's daily cap is used (the caller keeps the rules answer, marked as such)."""
    key = hashlib.sha256(f"{SCHEMA_VERSION}|{it['url']}".encode()).hexdigest()
    if key in _judged:
        return _judged[key]
    state = {"brand": brand_state, "story": {"title": it["title"], "source": it["source"], "summary": (it.get("summary") or "")[:600]}}
    for name, url, model, env in PROVIDERS:
        k = os.environ.get(env)
        if not k or _use(store, uid, name) >= JUDGE_CAP:
            continue
        try:
            r = _post(url, http, {"model": model, "state": state, "questions": judge_questions(CATS)}, {"Authorization": f"Bearer {k}"})
            if r.status_code >= 400:
                continue
            ans = r.json().get("answers") or {}
        except (SourceError, ValueError):
            continue
        _use(store, uid, name, 1)
        nl = lambda q: float((ans.get(q) or {}).get("noul", 0.5))
        cat = (ans.get("category") or {}).get("choice")
        out = {"about_ai": nl("about_ai"), "category": cat if cat in CATS else "product_feature",
               "kind": (ans.get("kind") or {}).get("choice") or "news", "relevance": float((ans.get("relevance") or {}).get("score", 1.5)),
               "unconfirmed": nl("unconfirmed"), "sensitive": nl("sensitive"), "allegation": nl("allegation"), "provider": name}
        out["format"] = "carousel" if out["kind"] == "evergreen" else "single_card"
        _judged[key] = out
        return out
    return None


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
        on = {k: cfg.get("on", {}).get(k, not ADAPTERS[k].needs_key or demo_guard.is_demo(a.uid)) for k in ADAPTERS}
        return {"on": on, "rss": cfg.get("rss") or DEFAULT_RSS, "lib_off": sorted(cfg.get("lib_off") or []), "x_query": cfg.get("x_query", ""), "hn_query": cfg.get("hn_query", ""),
                "x_key": ("…" + sec["x_bearer"][-4:]) if sec.get("x_bearer") else "",
                "ph_key": ("…" + sec["ph_token"][-4:]) if sec.get("ph_token") else "",
                "classifier": classifier_view(),
                "adapters": [{"key": ad.key, "label": ad.label, "about": ad.about, "needs_key": ad.needs_key} for ad in ADAPTERS.values()],
                "sample": demo_guard.is_demo(a.uid)}

    def save_settings(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "sources_settings")
        rows = self.store.q("SELECT data, secret FROM source_settings WHERE user_id=?", (a.uid,))
        cfg = json.loads(rows[0]["data"]) if rows else {}
        sec = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        if isinstance(body.get("on"), dict):
            cfg["on"] = {k: bool(body["on"].get(k, cfg.get("on", {}).get(k, not ADAPTERS[k].needs_key))) for k in ADAPTERS}
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
        for src, (field, pattern, what) in KEYED.items():
            bk = f"{src}_key"
            if bk not in body:
                continue
            if a.kind == "agent":
                raise SourceError(f"Only a person can add or remove the {what}.")
            key = str(body.get(bk) or "").strip()
            if key and not re.fullmatch(pattern, key):
                raise SourceError(f"That doesn't look like a {what}.")
            if key:
                sec[field] = key
                cfg.setdefault("on", {})[src] = True
            else:
                sec.pop(field, None)
                if ADAPTERS[src].needs_key:  # Product Hunt keeps working on the shared app-level read
                    cfg.setdefault("on", {})[src] = False
        self.store.x("INSERT INTO source_settings(user_id,data,secret,updated) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                     "data=excluded.data, secret=excluded.secret, updated=excluded.updated",
                     (a.uid, json.dumps(cfg), self.store.seal(sec) if sec else "", time.time()))
        self.store.audit(a.uid, a.label, "sources_settings", "read", {"fields": sorted(k for k in body if not k.endswith("_key")),
                                                                      "keys": {k: ("set" if body.get(k) else "removed") for k in body if k.endswith("_key")}})
        return self.settings(a)

    # ---------------------------------------------------------------- the source library (News › Sources): UI = REST = MCP
    def _cfg(self, a) -> dict:
        rows = self.store.q("SELECT data FROM source_settings WHERE user_id=?", (a.uid,))
        return json.loads(rows[0]["data"]) if rows and rows[0]["data"] else {}

    def list_sources(self, a, body: dict | None = None) -> dict:
        """Every library source with its status dot and last check, and the fresh items (deduplicated across sources)
        from the ones this workspace keeps on. Reading never fetches."""
        body = body or {}
        cat = str(body.get("category") or "")
        if cat and cat not in sources.CATEGORIES:
            raise SourceError(f"category must be one of: {', '.join(sources.CATEGORIES)}.")
        hours = min(168.0, max(1.0, float(body.get("fresh_hours") or sources.FRESH_HOURS)))
        view = sources.library_view(self.store, sources.library_off(self.store, a.uid), category=cat, fresh_hours=hours)
        limit = max(1, min(200, int(body.get("limit") or 120)))
        demo = demo_guard.is_demo(a.uid)
        last = self.store.q("SELECT at FROM source_refresh WHERE user_id=?", (a.uid,))
        wait = max(0, int(sources.REFRESH_EVERY - (time.time() - float(last[0]["at"])))) if last else 0
        return {**view, "items": view["items"][:limit], "total_items": len(view["items"]), "read_only": demo,
                "can_refresh": not demo, "refresh_in": 0 if demo else wait, "refresh_every": sources.REFRESH_EVERY,
                "note": ("The sample shows the last shared copy. Sign in to refresh." if demo else
                         "Refresh re-reads every source you keep on; a source checked in the last 2 minutes is reused.")}

    def refresh_sources(self, a, body: dict | None = None) -> dict:
        """Re-read the workspace's sources now: once a minute per workspace, never in the signed-out sample."""
        if demo_guard.is_demo(a.uid):
            e = SourceError("The sample shows a saved copy of the sources. Sign in with Buffer to refresh them.")
            e.status = 403
            raise e
        self.studio._guard_write(a, "sources_refresh")
        now = time.time()
        last = self.store.q("SELECT at FROM source_refresh WHERE user_id=?", (a.uid,))
        if last and now - float(last[0]["at"]) < sources.REFRESH_EVERY:
            left = int(sources.REFRESH_EVERY - (now - float(last[0]["at"]))) + 1
            raise SourceBusy(f"Sources were refreshed a moment ago. You can refresh again in {left} s.")
        self.store.x("INSERT INTO source_refresh(user_id,at) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET at=excluded.at", (a.uid, now))
        off = sources.library_off(self.store, a.uid)
        t0 = time.time()
        sources.refresh_library(self.store, [i for i in sources.BY_ID if i not in off], self.http, floor=sources.REFRESH_FLOOR)
        view = self.list_sources(a, body)
        failing = [s["label"] for s in view["sources"] if s["enabled"] and s["status"] == "failing"]
        self.store.audit(a.uid, a.label, "sources_refresh", "read", {"sources_on": view["counts"]["on"], "items": view["total_items"],
                                                                     "failing": len(failing), "ms": int((time.time() - t0) * 1000)})
        return {**view, "refreshed": True, "failing": failing}

    def set_source_enabled(self, a, body: dict) -> dict:
        sid, on = str(body.get("source_id") or ""), body.get("enabled")
        if sid not in sources.BY_ID:
            raise SourceError("Unknown source. list_sources gives the ids.")
        if not isinstance(on, bool):
            raise SourceError("enabled must be true or false.")
        if demo_guard.is_demo(a.uid):
            e = SourceError("The sample's sources are read-only. Sign in with Buffer to choose your own.")
            e.status = 403
            raise e
        self.studio._guard_write(a, "sources_settings")
        cfg = self._cfg(a)
        off = set(cfg.get("lib_off") or []) - {sid} | (set() if on else {sid})
        cfg["lib_off"] = sorted(off & set(sources.BY_ID))
        self.store.x("INSERT INTO source_settings(user_id,data,secret,updated) VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                     "data=excluded.data, updated=excluded.updated", (a.uid, json.dumps(cfg), "", time.time()))
        self.store.audit(a.uid, a.label, "sources_settings", "read", {"source": sid, "enabled": on})
        src = next(s for s in sources.library_view(self.store, off)["sources"] if s["id"] == sid)
        return {"source": src, "lib_off": cfg["lib_off"]}

    # ---------------------------------------------------------------- scan
    def scan(self, a, body: dict | None = None) -> dict:
        body = body or {}
        self.studio._guard_write(a, "sources_scan")
        sample = demo_guard.is_demo(a.uid) or bool(body.get("sample"))
        cfg = self.settings(a)
        rows = self.store.q("SELECT secret FROM source_settings WHERE user_id=?", (a.uid,))
        secret = (self.store.unseal(rows[0]["secret"]) if rows and rows[0]["secret"] else {}) or {}
        want = [k for k in (body.get("sources") or [k for k, v in cfg["on"].items() if v]) if k in ADAPTERS]
        if sample and not body.get("sources"):
            want += [k for k in KEYED if k not in want]  # the sample shows the keyed adapters too (made-up items, no call)
        if sample:
            want = [k for k in want if k != "library"]  # the sample's scan is the recorded one; it never fetches
        cfg["_store"] = self.store
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
            except Exception as e:  # noqa: BLE001 - one source's bad answer (unparseable feed, odd JSON) never 500s the scan
                print(f"sources: {k} failed: {e.__class__.__name__}", flush=True)
                status.append({"source": k, "label": ad.label, "ok": False, "items": 0,
                               "error": f"{ad.label} sent something unreadable ({e.__class__.__name__})."})
        items = items[:MAX_ITEMS]
        now = float(body.get("now") or time.time())
        unique = dedupe(items)
        kit = self.house.kit(a)
        terms = tokens(" ".join([kit.get("voice") or "", " ".join(kit.get("hashtags") or []),
                                 "ai model models tool tools agent agents open launch startup developer"]))
        groups = cluster(unique)
        for g in groups:
            g.sort(key=lambda it: -it["engagement"])
        judged = {id(g): judge(g[0], terms) for g in groups}
        if not sample and classifier_view()["configured"]:  # the model judges the most promising stories; rules answer the rest
            top = sorted(groups, key=lambda g: -decide(g, judged[id(g)], now)["score"])[:JUDGE_PER_SCAN]
            bstate = {"name": kit.get("name") or "", "covers": kit.get("voice") or "AI news and tools", "avoids": ""}
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(4) as ex:
                for g, mj in zip(top, ex.map(lambda g: model_judge(self.store, a.uid, g[0], bstate, self.http), top)):
                    if mj:
                        judged[id(g)] = dict(mj, category="research") if g[0].get("type") == "paper" else mj
        clusters = []
        for g in groups:
            j = judged[id(g)]
            dec = decide(g, j, now)
            st = finish_story(story_from(g), j["category"])
            clusters.append({"id": hashlib.sha256("|".join(sorted(it["id"] for it in g)).encode()).hexdigest()[:10],
                             "topic": TOPIC[j["category"]], "category": j["category"], "judgments": j, **dec, "story": st,
                             "items": [{k: it[k] for k in ("id", "adapter", "source", "title", "url", "published", "engagement")} for it in g],
                             "sources": sorted({it["adapter"] for it in g})})
        clusters.sort(key=lambda c: ({"postable": 0, "idea_bank": 1, "low": 2, "hidden": 3}[c["lane"]], -c["score"]))
        result = {"sample": sample, "status": status, "counts": {"fetched": len(items), "unique": len(unique), "clusters": len(clusters)},
                  "topics": _count([c["topic"] for c in clusters if c["lane"] != "hidden"]), "clusters": clusters[:60],
                  "classifier": _cls_summary([c["judgments"].get("provider", "rules") for c in clusters]), "at": time.time()}
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
        if not dry and demo_guard.is_demo(a.uid):
            from .demo_view import message, view_only
            if view_only():  # the view-only demo previews the packages (dry run) but never sends them
                err = SourceError(message("save these posts as drafts in your Buffer"))
                err.status = 403
                raise err
        self.studio._guard_write(a, "sources_draft")
        if demo_guard.is_demo(a.uid) and not demo_guard.take(self.store, a.uid, "run"):
            raise SourceError(demo_guard.why("run"))
        for c in picks:
            if c["agent"] == "person_only" and a.kind == "agent":
                raise SourceError(f"“{c['story']['headline'][:60]}” touches a sensitive subject, so only a person can draft it.")
        kit = self.house.kit(a)
        from . import llm
        from .brand_studio import BrandStudio
        bs = BrandStudio(self.house)
        bkit = bs.kit(a)
        rules = self.house.rules(a)
        saved = {(p["cluster_id"], p["channel_id"]): p for p in (run.get("drafts") or [])}
        edits = body.get("texts") or {}  # {cluster_id: {channel_id: text}}: the person's edits to the previewed captions
        eng = llm.resolve(self.store, a.uid, self.studio.s)  # the person's own AI key first, then the app's capped starter
        out = []
        for c in picks:
            st = c["story"]
            need = [ch for ch in chans if (c["id"], ch["id"]) not in saved]
            rewritten = self._rewrite(a, eng, st, kit, sorted({ch["service"] for ch in need})) if (eng and need) else {}
            covers: dict[str, str] = {}
            for ch in chans:
                svc = ch["service"]
                prev = saved.get((c["id"], ch["id"]))
                text = (edits.get(c["id"]) or {}).get(ch["id"]) or (prev or {}).get("text")
                if not text:
                    text = rewritten.get(svc) or caption(svc, st, bkit)
                    text = brand.apply_text_rules(text, rules)
                text = drafter.fit(svc, str(text)[:5000])
                ratio = templates.CHANNELS.get(svc, templates.CHANNELS["instagram"])["ratio"]
                if prev and prev.get("media_url") and prev.get("ratio") == ratio:
                    covers.setdefault(ratio, prev["media_url"])
                if ratio not in covers:
                    covers[ratio] = bs.render_cover(a, st, ratio, bkit)
                pkg = {"cluster_id": c["id"], "headline": st["headline"], "channel_id": ch["id"], "service": svc,
                       "channel_name": ch.get("displayName") or ch.get("name"), "format": STYLE.get(svc, STYLE["facebook"])["format"],
                       "ratio": ratio, "text": text, "length": drafter.count(svc, text), "limit": drafter.limit_for(svc),
                       "hashtags": len(brand.HASHTAG_RE.findall(text)), "media_url": covers[ratio], "agent": c["agent"],
                       "writer": "your edit" if (edits.get(c["id"]) or {}).get(ch["id"]) else ((prev or {}).get("writer")
                                 or ((eng or {}).get("source", "built-in writer") if rewritten.get(svc) else "built-in writer")),
                       "check": brand.brand_check(text, None, rules, kit)}
                if not dry:
                    pkg["result"] = self._send(a, pkg)
                out.append(pkg)
        res = json.loads(self.store.q("SELECT result FROM source_runs WHERE id=?", (run["run_id"],))[0]["result"])
        keep = {(p["cluster_id"], p["channel_id"]): p for p in res.get("drafts") or []}
        keep.update({(p["cluster_id"], p["channel_id"]): {k: v for k, v in p.items() if k != "check"} for p in out})
        res["drafts"] = list(keep.values())[-60:]
        self.store.x("UPDATE source_runs SET result=? WHERE id=?", (json.dumps(res), run["run_id"]))
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


def _cls_summary(providers: list[str]) -> dict:
    return {"order": classifier_view()["order"], "judged_by": {p: providers.count(p) for p in sorted(set(providers))}}


def _count(xs: list[str]) -> list[dict]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return [{"topic": k, "n": v} for k, v in sorted(out.items(), key=lambda kv: -kv[1])]
