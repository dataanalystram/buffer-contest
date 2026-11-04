"""Official brand assets + the facts behind them, for a post about one company (owner, 1 Nov 2026: "the agent must make
the ernd carousel itself").

``research(name, urls)`` reads the company's own pages and returns sources (each with its text and how new it is) and
``find_images`` collects the official pictures:

* the YC company page (ycombinator.com/companies/<slug>): its embedded page data gives the batch, one-liner, long
  description, website, tags, logo, the founders (name, title, bio, YC avatar) and its Launch YC posts (title, body, date);
* the Launch YC post itself (the newest official words);
* the company's website: og:image / twitter:image (the share image, often the product on a stage), hero art referenced in
  the page (``/art/``, ``hero``, ``bg``, ``cover``...), the apple-touch icon as a logo fallback, and the page text;
* Product Hunt (producthunt.com/products/<slug>): read when it answers; it usually blocks servers (403), which is noted.

Every fetch goes through netguard (no auto-redirects, every hop re-checked, bytes capped: SECURITY_RULES 1 and 6). Images
are size-checked while streaming, decoded only after the cap, size-checked again (too small = skipped) and re-encoded by
Pillow (so the bytes we keep are ours, metadata stripped). Each keeps its source URL and the rights note
"official press/brand image" (the company's own published material, used to report on it, credited in the caption).
"""
from __future__ import annotations

import html as _html
import io
import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from . import netguard

RIGHTS = "official press/brand image"
PAGE_MAX = 2_000_000
IMAGE_MAX = 8_000_000
UA = {"User-Agent": "Mozilla/5.0 (compatible; HuhQueue/1.0; +https://huhqueue-studio.onrender.com)"}
_META = re.compile(r"<meta[^>]+(?:property|name)=[\"'](og:image(?::secure_url)?|twitter:image(?::src)?)[\"'][^>]*>", re.I)
_CONTENT = re.compile(r"content=[\"']([^\"']+)[\"']", re.I)
_IMG_REF = re.compile(r"[\"'(]((?:https?:)?/[^\"'()\s]+?\.(?:webp|jpe?g|png))(?:\?[^\"'()\s]*)?[\"')]", re.I)
_HERO_WORDS = re.compile(r"(hero|/art/|sky|banner|cover|backdrop|background|/bg|header|home|splash|key-?art|product|card)", re.I)
_SKIP_WORDS = re.compile(r"(favicon|sprite|icon-|/icons?/|logo-?small|pixel|grain|noise|avatar|emoji|badge|flag)", re.I)


class AssetError(ValueError):
    pass


def slug_of(name: str) -> str:
    s = re.sub(r"\(.*?\)", "", name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60]


def _get(url: str, http, limit: int = PAGE_MAX, timeout: float = 15):
    """netguard.safe_get, with the httpx errors a background research thread can hit turned into FetchError: a run
    that outlives its client (shutdown, a test's closed client) raises RuntimeError("Cannot send a request, as the
    client has been closed"), and every caller here already treats FetchError as "source unavailable"."""
    try:
        return netguard.safe_get(url, http=http, limit=limit, timeout=timeout, headers=UA)
    except netguard.FetchError:
        raise
    except (RuntimeError, httpx.HTTPError) as e:
        raise netguard.FetchError(f"request failed ({type(e).__name__})") from e


def _text(html: str) -> str:
    t = re.sub(r"<script.*?</script>|<style.*?</style>|<noscript.*?</noscript>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _date(s) -> str:
    s = str(s or "")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    for fmt in ("%a %Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:14], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


# ---------------------------------------------------------------------------------------------------- YC
def yc_company(slug: str, http=None) -> dict | None:
    """The YC company page's embedded data -> {name, batch, one_liner, description, website, tags, logo, founders, launches}."""
    url = f"https://www.ycombinator.com/companies/{slug}"
    try:
        r = _get(url, http)
    except netguard.FetchError:
        return None
    if r.status_code != 200:
        return None
    m = re.search(r'data-page="([^"]+)"', r.text)
    if not m:
        return None
    try:
        props = json.loads(_html.unescape(m.group(1))).get("props") or {}
    except ValueError:
        return None
    c = props.get("company") or {}
    if not c.get("name"):
        return None

    def lst(v):
        if isinstance(v, list):
            return v
        try:  # some fields arrive as Python-literal strings
            import ast
            return ast.literal_eval(v) if isinstance(v, str) and v.startswith("[") else []
        except (ValueError, SyntaxError):
            return []
    founders = []
    for f in lst(c.get("founders")):
        if isinstance(f, dict) and f.get("full_name"):
            founders.append({"name": f["full_name"], "title": str(f.get("title") or "").replace("Founder/", ""),
                             "bio": str(f.get("founder_bio") or "")[:400], "photo": f.get("avatar_thumb_url") or "",
                             "linkedin": f.get("linkedin_url") or "", "x": f.get("twitter_url") or ""})
    launches = []
    for ln in props.get("launches") or []:
        if isinstance(ln, dict) and ln.get("title"):
            launches.append({"title": ln["title"], "tagline": ln.get("tagline") or "", "body": str(ln.get("body") or "")[:6000],
                             "url": ln.get("url") or (f"https://www.ycombinator.com/launches/{ln['slug']}" if ln.get("slug") else ""),
                             "date": _date(ln.get("created_at") or ln.get("approved_at"))})
    tags = c.get("tags")
    return {"url": url, "name": c["name"], "slug": c.get("slug") or slug, "batch": c.get("batch") or "", "batch_name": c.get("batch_name") or "",
            "one_liner": (c.get("one_liner") or "").strip(), "description": (c.get("long_description") or "").strip(),
            "website": c.get("website") or "", "tags": lst(tags) if not isinstance(tags, list) else tags,
            "logo": c.get("logo_url") or c.get("small_logo_url") or "", "location": c.get("location") or "",
            "team_size": c.get("team_size") or "", "founders": founders, "launches": launches}


# ---------------------------------------------------------------------------------------------------- a website
def site(url: str, http=None) -> dict | None:
    try:
        r = _get(url, http)
    except netguard.FetchError:
        return None
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return None
    h = r.text
    base = str(r.url)
    og = []
    for m in _META.finditer(h):
        c = _CONTENT.search(m.group(0))
        if c:
            og.append(urljoin(base, _html.unescape(c.group(1))))
    title = _html.unescape((re.search(r"<title[^>]*>(.*?)</title>", h, re.S | re.I) or [None, ""])[1]).strip()
    desc_m = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]*>", h, re.I)
    desc = _html.unescape(_CONTENT.search(desc_m.group(0)).group(1)) if desc_m and _CONTENT.search(desc_m.group(0)) else ""
    refs, seen = [], set()
    for m in _IMG_REF.finditer(h):
        u = urljoin(base, m.group(1))
        if u in seen or _SKIP_WORDS.search(u) or urlparse(u).hostname != urlparse(base).hostname:
            continue
        seen.add(u)
        refs.append(u)
    hero = [u for u in refs if _HERO_WORDS.search(u)]
    # one variant per art name, portrait first ("-p" / "portrait"), webp/jpg before png
    def rank(u):
        return (0 if re.search(r"[-_](p|portrait|tall)\.", u) else 1, 0 if re.search(r"\.(webp|jpe?g)$", u, re.I) else 1)
    hero.sort(key=rank)
    icon = re.search(r"<link[^>]+rel=[\"'](?:apple-touch-icon|icon)[\"'][^>]*href=[\"']([^\"']+)[\"']", h, re.I)
    return {"url": base, "title": title[:200], "description": desc[:400], "text": _text(h)[:12000], "og": og[:3],
            "hero": hero[:8], "icon": urljoin(base, icon.group(1)) if icon else ""}


def product_hunt(slug: str, http=None) -> dict:
    url = f"https://www.producthunt.com/products/{slug}"
    try:
        r = _get(url, http, timeout=10)
    except netguard.FetchError as e:
        return {"url": url, "ok": False, "note": f"Product Hunt not reachable ({e})"}
    if r.status_code != 200:
        return {"url": url, "ok": False, "note": f"Product Hunt answered {r.status_code} (it blocks servers); skipped"}
    og = [urljoin(url, _CONTENT.search(m.group(0)).group(1)) for m in _META.finditer(r.text) if _CONTENT.search(m.group(0))]
    return {"url": url, "ok": True, "text": _text(r.text)[:6000], "og": og[:2]}


# ---------------------------------------------------------------------------------------------------- images
def fetch_image(url: str, http=None, min_side: int = 200) -> tuple[bytes, dict] | None:
    """An image through netguard, capped while streaming, decoded only after the cap, re-encoded (JPEG, or PNG when it
    has transparency). None when it isn't an image, is too small, or can't be decoded (e.g. AVIF on a Pillow without it)."""
    try:
        r = _get(url, http, limit=IMAGE_MAX, timeout=15)
    except netguard.FetchError:
        return None
    if r.status_code != 200 or not r.content:
        return None
    try:
        im = Image.open(io.BytesIO(r.content))  # lazy: only the header is read here
        if min(im.size) < min_side or im.width * im.height > 40_000_000:  # size check BEFORE decoding (SECURITY_RULES 6)
            return None
        im.load()
    except Exception:  # noqa: BLE001
        return None
    alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    out = io.BytesIO()
    if alpha:
        im.convert("RGBA").save(out, "PNG", optimize=True)
        ext = "png"
    else:
        im.convert("RGB").save(out, "JPEG", quality=92)
        ext = "jpg"
    return out.getvalue(), {"width": im.width, "height": im.height, "ext": ext, "source_url": url, "rights": RIGHTS}


def research(name: str, urls: list[str] | None = None, http=None) -> dict:
    """Every official source about the company, newest first: [{url, kind, title, date, text}], plus the YC data and the
    image candidates (not yet downloaded). urls: links the person gave (the company site, its YC page...)."""
    urls = [u for u in (urls or []) if isinstance(u, str) and u.startswith("http")]
    slug = ""
    for u in urls:
        m = re.search(r"ycombinator\.com/companies/([\w-]+)", u)
        if m:
            slug = m.group(1)
    slug = slug or slug_of(name)
    out: dict = {"name": name, "slug": slug, "sources": [], "notes": [], "images": [], "founders": [], "yc": None}
    yc = yc_company(slug, http) if slug else None
    if yc:
        out["yc"] = {k: v for k, v in yc.items() if k not in ("launches",)}
        out["name"] = yc["name"]
        out["founders"] = yc["founders"]
        for ln in yc["launches"][:2]:
            out["sources"].append({"url": ln["url"], "kind": "launch", "title": ln["title"], "date": ln["date"],
                                   "text": (ln["tagline"] + "\n" + ln["body"]).strip()})
        out["sources"].append({"url": yc["url"], "kind": "yc_profile", "title": f"{yc['name']} ({yc['batch_name'] or yc['batch']})",
                               "date": "", "text": f"{yc['one_liner']}\n{yc['description']}\nFounders: " +
                               "; ".join(f"{f['name']} ({f['title']}): {f['bio']}" for f in yc["founders"])})
        if yc.get("logo"):
            out["images"].append({"url": yc["logo"], "role": "logo", "page": yc["url"]})
        for f in yc["founders"]:
            if f.get("photo"):
                out["images"].append({"url": f["photo"], "role": "founder", "page": yc["url"], "name": f["name"]})
    else:
        out["notes"].append(f"No YC company page at ycombinator.com/companies/{slug}.")
    web = next((u for u in urls if "ycombinator.com" not in u and "producthunt.com" not in u), "") or (yc or {}).get("website", "")
    if web:
        st = site(web, http)
        if st:
            out["sources"].insert(0, {"url": st["url"], "kind": "website", "title": st["title"], "date": "current",
                                      "text": (st["description"] + "\n" + st["text"]).strip()})
            for u in st["og"][:1]:
                out["images"].append({"url": u, "role": "product", "page": st["url"]})
            for u in st["hero"][:4]:
                out["images"].append({"url": u, "role": "hero", "page": st["url"]})
            if st["icon"] and not (yc or {}).get("logo"):
                out["images"].append({"url": st["icon"], "role": "logo", "page": st["url"]})
        else:
            out["notes"].append(f"Couldn't read {web}.")
    ph = product_hunt(slug, http) if slug else {"ok": False, "note": ""}
    if ph.get("ok"):
        out["sources"].append({"url": ph["url"], "kind": "product_hunt", "title": "Product Hunt", "date": "", "text": ph["text"]})
        for u in ph.get("og") or []:
            out["images"].append({"url": u, "role": "product_hunt", "page": ph["url"]})
    elif ph.get("note"):
        out["notes"].append(ph["note"])
    return out


# the order official words are trusted in when two pages disagree (newest official first)
TRUST = {"website": 0, "launch": 1, "product_hunt": 2, "yc_profile": 3}


def figures(text: str) -> list[str]:
    """Money and percentage figures with a little context ('4% back on AI', '$4,800 a year')."""
    out = []
    for m in re.finditer(r"(\$\s?[\d,.]+\s?(?:[kKmMbB]|million|billion)?|\b\d+(?:\.\d+)?\s?%)([^.\n]{0,40})", text or ""):
        out.append((m.group(1).replace(" ", "") + m.group(2)).strip())
    return out


def conflicts(sources: list[dict]) -> list[dict]:
    """Percent figures for the same thing that differ between sources ('back on everything else': 1.5% vs 2%).
    Returns [{topic, values: {kind: value}, use, why}] preferring the most trusted (newest official) source."""
    topics = {"ai spend": r"(?:back\s+)?on\s+(?:AI|ai)\b|AI\s+spend|AI\s+vendor", "everything else": r"everything\s+else|all\s+other"}
    found: dict[str, dict[str, str]] = {}
    for s in sources:
        t = s.get("text") or ""
        for topic, pat in topics.items():
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s?%\s?(?:back\s+)?(?:\w+\s+){0,4}?(?:" + pat + ")", t):
                found.setdefault(topic, {}).setdefault(s["kind"], m.group(1) + "%")
            for m in re.finditer(r"(?:" + pat + r")[^.%\d]{0,25}?(\d+(?:\.\d+)?)\s?%", t):
                found.setdefault(topic, {}).setdefault(s["kind"], m.group(1) + "%")
    out = []
    for topic, vals in found.items():
        if len(set(vals.values())) > 1:
            best = min(vals, key=lambda k: TRUST.get(k, 9))
            out.append({"topic": topic, "values": vals, "use": vals[best], "from": best,
                        "why": f"sources differ on {topic} ({', '.join(f'{k}: {v}' for k, v in vals.items())}); "
                               f"using the newest official page ({best})"})
    return out
