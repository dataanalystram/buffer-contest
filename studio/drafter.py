"""Link -> per-channel caption drafts that respect Buffer's character rules.

1. fetch_article(): safe fetch (http/https only, public IPs only, size/time capped) and parse
   title, description, image, site name and the first paragraphs.
2. draft(): one caption per channel. Uses an OpenAI-compatible model when AI_BASE_URL is set,
   otherwise a deterministic writer. Every caption is measured with Buffer's counting rules
   (developers.buffer.com/guides/character-limits) and trimmed to fit before it is returned.
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

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


def limit_for(service: str) -> int:
    return LIMITS.get(service, 2000)


def utf16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def count(service: str, text: str) -> int:
    """Approximate Buffer's per-network character count."""
    if service == "bluesky":
        t = URL_RE.sub(lambda m: (urlparse(m.group()).hostname or "")[:40] + "x" * 16, text)
        return len(t)
    if service == "twitter":
        return utf16(URL_RE.sub("x" * 23, text))
    if service == "mastodon":
        return utf16(URL_RE.sub("x" * 23, text))
    if service == "linkedin":
        return utf16(URL_RE.sub("x" * 24, text))
    if service == "instagram":
        return utf16(text) + text.count("\n")
    return utf16(text)


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


class FetchError(ValueError):
    pass


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def fetch_article(url: str, http: httpx.Client | None = None, check_host: bool = True) -> dict:
    url = (url or "").strip()
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Paste a full link that starts with http:// or https://")
    if check_host and not _public(p.hostname):
        raise FetchError("That link points to a private address, so it can't be read.")
    client = http or httpx.Client(timeout=10, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0 (HuhQueue link preview)"})
    try:
        r = client.get(url)
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
    return {"url": str(r.url), "title": title[:300], "description": desc[:600], "image": image,
            "site": site[:80], "paragraphs": m.paras[:8]}


# ---------------------------------------------------------------- writing
STOP = set("the a an and or of to in on for with from by at is are was were be this that it its as new how why what your you our we".split())


def _hashtags(article: dict, n: int = 3) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]+", article["title"])
    tags, seen = [], set()
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
        parts = [f"{t} ✨", d, k, f"Source: {article['site']} (link in bio)", tags]
        text = "\n\n".join(p for p in parts if p)
    elif service == "youtube":
        text = "\n\n".join(p for p in [t, d, k, f"Read more: {u}", tags] if p)
    elif service in ("tiktok", "pinterest"):
        text = "\n\n".join(p for p in [t, d, tags] if p)
    else:
        text = "\n\n".join(p for p in [t, d, u] if p)
    return fit(service, text, min(limit_for(service), TARGET.get(service, limit_for(service))))


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
                                     {"text": " ".join(article.get("paragraphs", []))[:2500]})
    )
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    client = http or httpx.Client(timeout=25)
    r = client.post(base_url + "/chat/completions", headers=headers, json={
        "model": model or "gpt-4o-mini", "temperature": 0.6,
        "messages": [{"role": "system", "content": "You are a concise social media editor."},
                     {"role": "user", "content": prompt}]})
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", content, re.S)
    data = json.loads(m.group(0) if m else content)
    return {s: str(data[s]).strip() for s in services if isinstance(data.get(s), str) and data[s].strip()}


def draft(channels: list[dict], article: dict, *, ai: dict | None = None, http: httpx.Client | None = None,
          tone: str = "") -> list[dict]:
    services = sorted({c["service"] for c in channels})
    written, engine = {}, "built-in"
    if ai and ai.get("base_url"):
        try:
            written = ai_draft(services, article, ai["base_url"], ai.get("api_key", ""), ai.get("model", ""), http, tone)
            engine = "ai"
        except Exception:
            written = {}
    out = []
    for c in channels:
        svc = c["service"]
        text = written.get(svc) or rule_draft(svc, article)
        text = fit(svc, text)
        out.append({"channel_id": c["id"], "service": svc, "channel_name": c.get("displayName") or c.get("name"),
                    "text": text, "length": count(svc, text), "limit": limit_for(svc),
                    "media_url": article.get("image") or "", "engine": engine if svc in written else "built-in"})
    return out
