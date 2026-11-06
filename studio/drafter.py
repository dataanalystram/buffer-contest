"""Link -> per-channel caption drafts that respect Buffer's character rules.

1. fetch_article(): safe fetch (http/https only, public IPs only, size/time capped) and parse
   title, description, image, site name and the first paragraphs.
2. draft(): one caption per channel. Uses an OpenAI-compatible model when AI_BASE_URL is set,
   otherwise a deterministic writer. Every caption is measured with Buffer's counting rules
   (developers.buffer.com/guides/character-limits) and trimmed to fit before it is returned.
"""
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from .netguard import FetchError  # one error type for every fetch

# Post-text limits Buffer validates against, per channel service.
LIMITS = {
    "twitter": 280, "linkedin": 3000, "instagram": 2196, "facebook": 5000, "threads": 500,
    "bluesky": 300, "mastodon": 500, "tiktok": 2200, "youtube": 5000, "pinterest": 500,
    "googlebusiness": 4000, "startPage": 2000,
}
# What we aim for (a good post is shorter than the hard limit).
TARGET = {"twitter": 260, "bluesky": 290, "threads": 450, "mastodon": 450, "linkedin": 1300,
          "instagram": 900, "facebook": 600, "tiktok": 600, "youtube": 900, "pinterest": 450,
          "googlebusiness": 700}
URL_RE = re.compile(r"https?://\S+")
# Networks Buffer can post as a thread (metadata.<service>.thread). Parts are separated by a "---" line.
THREAD_SERVICES = {"twitter", "bluesky", "threads", "mastodon"}
THREAD_SEP = "\n---\n"
THREAD_SPLIT = re.compile(r"\n[ \t]*---[ \t]*\n")


def split_thread(service: str, text: str) -> list[str]:
    """Thread parts for a thread-capable network; a single part everywhere else."""
    if service not in THREAD_SERVICES:
        return [text]
    return [p.strip() for p in THREAD_SPLIT.split(text) if p.strip()] or [text]


def limit_for(service: str) -> int:
    return LIMITS.get(service, 2000)


def utf16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def count(service: str, text: str) -> int:
    """Buffer's per-network character count (one implementation, in studio/preflight.py)."""
    from .preflight import count_text
    return count_text(service, text)


def fit(service: str, text: str, limit: int | None = None) -> str:
    """Trim at a sentence/word boundary until the post fits; keep a trailing URL if present."""
    limit = limit or limit_for(service)
    if count(service, text) <= limit:
        return text
    url = ""
    m = re.search(r"\s(https?://\S+)\s*$", text)
    if m:
        url, text = m.group(1), text[: m.start()].rstrip()
    suffix = ("…\n\n" + url) if url else "…"
    while text and count(service, text + suffix) > limit:
        cut = max(text.rfind(". ", 0, len(text) - 1), text.rfind(" ", 0, len(text) - 1))
        text = text[:cut].rstrip(" ,;:-") if cut > 0 else text[:-1]
    return (text + suffix).strip()


# ---------------------------------------------------------------- fetching
class _Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta, self.title, self.paras = {}, "", []
        self._in_title = self._in_p = False
        self._skip = 0
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k and a.get("content"):
                self.meta.setdefault(k, a["content"])
        elif tag == "title":
            self._in_title = True
        elif tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self._skip += 1
        elif tag == "p" and not self._skip:
            self._in_p, self._buf = True, []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag in ("script", "style", "nav", "footer", "header", "aside", "form"):
            self._skip = max(0, self._skip - 1)
        elif tag == "p" and self._in_p:
            self._in_p = False
            t = " ".join("".join(self._buf).split())
            if len(t) > 60 and len(self.paras) < 12:
                self.paras.append(t)

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_p and not self._skip:
            self._buf.append(data)



def _public(host: str) -> bool:
    """True only for a host whose every address is public (see netguard.public_host for the ranges refused)."""
    from . import netguard
    return netguard.public_host(host)


def fetch_article(url: str, http: httpx.Client | None = None, check_host: bool = True) -> dict:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Paste a full link that starts with http:// or https://")
    from . import netguard
    try:  # every redirect hop is checked again (SECURITY_RULES 1); never auto-follows
        r = netguard.safe_get(url, http=http, check_host=check_host, limit=4_000_000, timeout=10,
                              headers={"User-Agent": "Mozilla/5.0 (HuhQueue link preview)"})
    except httpx.HTTPError as e:
        raise FetchError(f"Couldn't open that link ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise FetchError(f"The page answered HTTP {r.status_code}.")
    body = r.text[:2_000_000]
    m = _Meta()
    try:
        m.feed(body)
    except Exception:  # malformed HTML still yields whatever was parsed
        pass
    g = m.meta.get
    title = html.unescape((g("og:title") or g("twitter:title") or m.title or p.hostname).strip())
    desc = html.unescape((g("og:description") or g("description") or g("twitter:description") or "").strip())
    image = g("og:image") or g("twitter:image") or ""
    if image:
        image = urljoin(str(r.url), image)
    site = (g("og:site_name") or p.hostname.removeprefix("www.")).strip()
    title = clean_title(title, site)
    return {"url": str(r.url), "title": title[:300], "description": desc[:600], "image": image,
            "site": site[:80], "paragraphs": m.paras[:8]}


def clean_title(title: str, site: str) -> str:
    """Drop a trailing ' - Site' / ' | Site' that pages append to <title>."""
    m = re.match(r"^(.{12,}?)\s+[-|–—·:]\s+([^-|–—·]{2,40})$", title)
    if m and (m.group(2).strip().lower() in (site or "").lower() or len(m.group(2).split()) <= 3):
        return m.group(1).strip()
    return title


# ---------------------------------------------------------------- writing
STOP = set("the a an and or of to in on for with from by at is are was were be this that it its as new how why what your you our we".split())


def _hashtags(article: dict, n: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", article["title"])
    tags, seen = [], set(w.lower() for w in re.findall(r"[A-Za-z0-9]+", article.get("site", "")))
    for w in sorted(words, key=lambda w: (not w[0].isupper(), -len(w))):
        lw = w.lower()
        if lw in STOP or len(w) < 3 or lw in seen:
            continue
        seen.add(lw)
        tags.append("#" + w[0].upper() + w[1:])
        if len(tags) == n:
            break
    return tags


def _key_point(article: dict) -> str:
    for para in article.get("paragraphs", []):
        s = re.split(r"(?<=[.!?])\s+", para)[0]
        if 40 < len(s) < 260 and s.lower() not in article["description"].lower():
            return s
    return ""


def rule_draft(service: str, article: dict) -> str:
    t, d, u = article["title"], article["description"], article["url"]
    k = _key_point(article)
    tags = " ".join(_hashtags(article))
    if service in ("twitter", "bluesky", "threads", "mastodon"):
        body = d if d and len(d) < 180 else (k or d)
        text = f"{t}\n\n{body}\n\n{u}" if body else f"{t}\n\n{u}"
    elif service == "linkedin":
        parts = [t, d]
        if k:
            parts.append(f"Why it matters: {k}")
        parts += ["What's your take?", u, tags]
        text = "\n\n".join(p for p in parts if p)
    elif service == "instagram":
        parts = [t, d, k, f"Source: {article['site']} (link in bio)", tags]
        text = "\n\n".join(p for p in parts if p)
    elif service == "youtube":
        text = "\n\n".join(p for p in [t, d, k, f"Read more: {u}", tags] if p)
    elif service in ("tiktok", "pinterest"):
        text = "\n\n".join(p for p in [t, d, tags] if p)
    else:
        text = "\n\n".join(p for p in [t, d, u] if p)
    return fit(service, text, min(limit_for(service), TARGET.get(service, limit_for(service))))


def rule_limit(service: str, rules: list[dict]) -> int:
    """The tightest of the network's hard limit and any active max_length rule for it (a preset's per-network length)."""
    lim = limit_for(service)
    for r in rules or []:
        c = r.get("check") or {}
        if r.get("active", True) and c.get("type") == "max_length" and int(c.get("n") or 0) > 40:
            lim = min(lim, int(c["n"]))
    return lim


_NUM = re.compile(r"(?<![\w.])(?:[$€£]\s?)?\d[\d,.]*\s?(?:%|percent|x|times|billion|million|bn|m|k|seconds|hours|days|GB|TB)?(?![\w])", re.I)


def _sentences(article: dict) -> list[str]:
    out = []
    for p in [article.get("description") or ""] + list(article.get("paragraphs") or []):
        for s in re.split(r"(?<=[.!?])\s+", p or ""):
            s = s.strip()
            if 30 <= len(s) <= 240 and s not in out:
                out.append(s)
    return out


def _short(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    if len(s) <= n:
        return s
    cut = s[:n].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return cut + "…"


def styled_draft(service: str, article: dict, style: dict) -> str:
    """The free drafter's caption in a preset's shape (studio/style_presets.py): hook first line (< 125 characters), one
    idea, the payoff, one CTA, hashtags within the preset's cap for this network. Deterministic: same input, same caption."""
    t, u, site = (article.get("title") or "").strip(), article.get("url") or "", article.get("site") or ""
    sents = _sentences(article)
    idea = _key_point(article) or (sents[0] if sents else article.get("description") or "")
    shape = style.get("shape") or "hook_idea_payoff"
    link = "" if service in ("instagram", "tiktok") else u
    if shape == "steps":
        steps = [s for s in sents if s != t][:3]
        hook = _short(f"{t}, in {len(steps)} points" if len(steps) >= 2 else t, 120)
        body = "\n".join(f"{i + 1}. {_short(s, 160)}" for i, s in enumerate(steps)) or idea
        lines = [hook, body]
    elif shape == "build":
        num = next((x for x in sents if _NUM.search(x) and x != idea), "")
        nxt = next((x for x in sents if x not in (idea, num)), "")
        lines = [_short(t, 120), f"What happened: {_short(idea, 200)}" if idea else "",
                 f"The number to watch: {_short(num, 180)}" if num else (f"What's next: {_short(nxt, 180)}" if nxt else "")]
    elif shape == "news":
        first = next((s for s in sents if _NUM.search(s)), sents[0] if sents else t)
        hook = _short(f"{site}: {first}" if site and site.lower() not in first.lower() else first, 120)
        why = next((s for s in sents if s != first), "")
        lines = [hook, f"Why it matters: {_short(why, 200)}" if why else "", f"Source: {site}" if site else ""]
    elif shape == "quote":
        quoted = next((m.group(1) for x in sents for m in [re.search(r"[\"“]([^\"“”]{12,}?)[\"”]", x)] if m), "")
        q = (quoted or idea or t).strip("\"“” ").rstrip(",")
        lines = [_short(f"“{q}”", 120), f"From {site}: {_short(t, 140)}" if site else _short(t, 140)]
    else:  # hook_idea_payoff
        # the most surprising number first: the title when it carries one, else the first sentence that does
        num = t if _NUM.search(t) else next((x for x in sents if _NUM.search(x)), "")
        hook = _short(num if num and len(num) <= 120 else t, 120)
        payoff = next((s for s in sents if s not in (num, idea)), "")
        lines = [hook, _short(idea, 200) if idea and idea != hook else "", f"Why it matters: {_short(payoff, 180)}" if payoff else ""]
    lim = min(limit_for(service), int((style.get("length") or {}).get(service) or TARGET.get(service, limit_for(service))))
    n = (style.get("hashtags") or {}).get(service, 3)
    tags = " ".join(_hashtags(article, max(0, min(int(n), 5))))
    # the hook and the one CTA always stay: the middle lines give way first, then the hashtags
    tail = [x for x in (link, style.get("cta") or "", tags) if x]
    head, middle = lines[0], [x for x in lines[1:] if x]
    while True:
        text = "\n\n".join([head] + middle + tail)
        if count(service, text) <= lim:
            return text
        if middle:
            room = lim - count(service, "\n\n".join([head] + tail)) - 4
            if len(middle) > 1:
                middle = middle[:-1]
            elif room >= 40 and count(service, middle[0]) > room:
                middle = [fit(service, middle[0], room)]
            else:
                middle = []
            continue
        if tags and tags in tail:
            tail = [x for x in tail if x != tags]
            continue
        return fit(service, text, lim)


def thread_draft(service: str, article: dict, max_parts: int = 5) -> str:
    """Hook, then one key sentence per post from the article body, then the link."""
    lim = min(limit_for(service), TARGET.get(service, limit_for(service)))
    parts = [fit(service, "\n\n".join(p for p in [article["title"], article["description"]] if p), lim)]
    seen = article["description"].lower()
    for para in article.get("paragraphs", []):
        if len(parts) >= max_parts - 1:
            break
        sents = re.split(r"(?<=[.!?])\s+", para)
        s = " ".join(sents[:2]) if count(service, " ".join(sents[:2])) <= lim else sents[0]
        if s.lower()[:80] in seen:
            continue
        seen += " " + s.lower()
        parts.append(fit(service, s, lim))
    parts.append(fit(service, f"Read the full story: {article['url']}", lim))
    return THREAD_SEP.join(parts)


def ai_draft(services: list[str], article: dict, base_url: str, api_key: str, model: str,
             http: httpx.Client | None = None, tone: str = "") -> dict[str, str]:
    prompt = (
        "Write one social post per network for the article below. Return ONLY a JSON object mapping "
        "network name to post text. Rules: plain, specific, no clickbait, no invented facts; Instagram has no "
        "clickable links (say 'link in bio'); include the URL on twitter/bluesky/threads/mastodon/linkedin/facebook; "
        "at most 3 hashtags; stay under these character targets: "
        + json.dumps({s: TARGET.get(s, limit_for(s)) for s in services})
        + (f"\nTone: {tone}" if tone else "")
        + "\n\nARTICLE\n" + json.dumps({k: article[k] for k in ("url", "title", "description", "site")} |
                                     {"text": " ".join(article.get("paragraphs", []))[:1200]})
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    client = http or httpx.Client(timeout=50)
    r = client.post(base_url + "/chat/completions", headers=headers, json={
        "model": model or "gpt-4o-mini", "max_tokens": 2500,
        "messages": [{"role": "system", "content": "You are a concise social media editor."},
                     {"role": "user", "content": prompt}]})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    data = json.loads(m.group(0) if m else content)
    return {s: str(data[s]).strip() for s in services if isinstance(data.get(s), str) and data[s].strip()}


def draft(channels: list[dict], article: dict, *, ai: dict | None = None, http: httpx.Client | None = None,
          tone: str = "", thread: bool = False, style: dict | None = None) -> list[dict]:
    """One caption per channel. style (a chosen Style Ledger preset, studio/style_presets.style_from_rules) shapes the
    free built-in drafter's captions; the AI drafter gets the preset's rules in its prompt through tone."""
    services = sorted({c["service"] for c in channels})
    threads = {s: thread_draft(s, article) for s in services if thread and s in THREAD_SERVICES}
    services = [s for s in services if s not in threads]
    written, engine = {}, "built-in"
    if services and ai and ai.get("base_url"):
        try:
            written = ai_draft(services, article, ai["base_url"], ai.get("api_key", ""), ai.get("model", ""), http, tone)
            engine = "ai"
        except Exception:
            written = {}
    out = []
    for c in channels:
        svc = c["service"]
        local = (styled_draft(svc, article, style) if style else rule_draft(svc, article)) if svc not in written else ""
        text = threads.get(svc) or fit(svc, written.get(svc) or local)
        parts = split_thread(svc, text)
        out.append({"channel_id": c["id"], "service": svc, "channel_name": c.get("displayName") or c.get("name"),
                    "text": text, "length": max(count(svc, t) for t in parts), "limit": limit_for(svc),
                    "parts": len(parts), "media_url": article.get("image") or "",
                    "engine": engine if svc in written else "built-in"})
    return out
