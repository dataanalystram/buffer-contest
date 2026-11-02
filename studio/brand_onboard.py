"""Brand onboarding (UI = REST = MCP): paste a website -> the brand kit is read from it -> "Your brand" shows three theme
stills in that brand (a 9:16 Reel cover, a 4:5 carousel cover, a 1:1 card) in about a second -> tweak -> save.

  POST /api/brand/extract          {url}            -> a DRAFT kit (nothing saved) + where every field came from   MCP extract_brand
  POST /api/brand/themes-preview   {kit, logo?}     -> the three stills, rendered in-process with Pillow           MCP preview_brand_themes
  POST /api/brand/onboard          {kit, logo?}     -> saves the draft as the workspace's kit (a person's click; agents
                                                       change the kit through the Style Ledger, as everywhere else)
  GET  /api/brand/samples                           -> the sample brands (pre-extracted, cached assets)          MCP list_sample_brands
  POST /api/brand/samples/{id}/schedule             -> the sample story in that brand into the SAMPLE Buffer queue MCP schedule_sample_brand_story
                                                       (the free sample workspace only; nothing reaches Buffer)

The sample brands (Linear and Notion; "Sample brand, not affiliated") were extracted from their public homepages with this
same extractor on 2 Nov 2026 (scripts/build_sample_brands.py) and are cached in studio/sample_brands/ with their renders in
studio/static/brands/<id>/, so the demo is instant, needs no network and spends no AI. Extraction never calls a model.

Security: every fetch goes through netguard (each redirect hop re-checked, private hosts refused) with size caps; the sample
workspace extracts live at most demo_guard's "import" cap a day and gets the cached result for the sample brands' URLs;
previews are rate-limited per workspace (CPU guard) and store nothing; agent writes pass the kill switch.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from . import brand, brand_extract, demo_guard, netguard, templates

SAMPLE_DIR = Path(__file__).parent / "sample_brands"
STATIC_BRANDS = Path(__file__).parent / "static" / "brands"
SAMPLE_IDS = ("nothing", "linear", "notion")
SAMPLE_LABEL = "Sample brand, not affiliated"
PREVIEWS_PER_MIN = 30
_previews: dict[str, list[float]] = {}
UA = {"User-Agent": "Mozilla/5.0 (compatible; HuhQueueBrandReader/1.0; +https://huhqueue-studio.onrender.com)",
      "Accept": "text/html,application/xhtml+xml,text/css,*/*;q=0.8", "Accept-Language": "en"}

# The one story every brand tells in the demo (made up; the same words in every style is the point)
STORY = {"kicker": "Product update", "title": "Three things we shipped this week",
         "sub": "Faster search, a calmer inbox and one new shortcut.",
         "bullets": ["Search answers in under 100 ms", "The inbox groups what needs you", "Press / to jump anywhere"],
         "reel": "What we shipped this week, in 30 seconds"}


class OnboardError(ValueError):
    pass


# ================================================================================================== sample brands
def sample(bid: str) -> dict | None:
    if bid not in SAMPLE_IDS:
        return None
    p = SAMPLE_DIR / f"{bid}.json"
    if not p.is_file():
        return None
    d = json.loads(p.read_text())
    d["logo_url"] = f"/static/brands/{bid}/logo.png"
    d["renders"] = {k: f"/static/brands/{bid}/{v}" for k, v in (d.get("renders") or {}).items()}
    d["carousel"] = [f"/static/brands/{bid}/{v}" for v in d.get("carousel") or []]
    return d


def samples() -> list[dict]:
    return [s for s in (sample(b) for b in SAMPLE_IDS) if s]


def sample_for_url(url: str) -> str | None:
    host = (urlparse(url if "//" in url else "https://" + url).hostname or "").lower().removeprefix("www.")
    for bid in SAMPLE_IDS:
        s = sample(bid)
        if s and host and host in {h.lower().removeprefix("www.") for h in s.get("hosts") or []}:
            return bid
    return None


def sample_logo(bid: str) -> bytes | None:
    p = STATIC_BRANDS / bid / "logo.png"
    return p.read_bytes() if p.is_file() else None


# ================================================================================================== rendering
def brand_for(kit: dict, logo: bytes | None) -> templates.Brand:
    """templates.Brand from a draft kit (the extractor's shape, or the saved kit)."""
    k = dict(kit)
    k.setdefault("front_card", {"style": "glow"})
    k.setdefault("end_card", {"style": (k.get("front_card") or {}).get("style") or "glow"})
    if k.get("font") not in templates.HEAD_FONTS:
        k["font"] = "inter"
    if k.get("body_font") not in templates.BODY_FONTS:
        k["body_font"] = "inter" if k["font"] in ("anton", "serif", "dot") else k["font"]
    return templates.Brand(k, logo)


def theme_stills(b: templates.Brand, story: dict | None = None) -> list[tuple[str, str, Any]]:
    """The three "Your brand" stills: (ratio, label, PIL image). Same story, this brand's look."""
    st = {**STORY, **(story or {})}
    reel = templates.front_card(b, "9:16", "Reel", st["reel"], st["sub"])
    cover = templates.front_card(b, "4:5", st["kicker"], st["title"], st["sub"], n=(1, 4))
    voice = (b.kit.get("voice") or "").strip()
    quote_txt = st.get("quote") or (re.split(r"(?<=[.!?])\s+", voice)[0][:140] if voice else "") \
        or "Agents should draft. People should decide what goes out."
    square = templates.quote_card(b, "1:1", quote_txt, b.name or "Your brand", "In our own words")
    return [("9:16", "Reel cover", reel), ("4:5", "Carousel cover", cover), ("1:1", "Square post", square)]


def story_carousel(b: templates.Brand, story: dict | None = None) -> list:
    st = {**STORY, **(story or {})}
    return [templates.front_card(b, "4:5", st["kicker"], st["title"], st["sub"], n=(1, 3)),
            templates.bullets_slide(b, "4:5", "What changed", st["bullets"], n=(2, 3)),
            templates.end_card(b, "4:5", n=(3, 3))]


def data_url(im, w: int = 360) -> str:
    t = im.copy()
    t.thumbnail((w, w * 2))
    return "data:image/jpeg;base64," + base64.b64encode(templates.jpeg(t, 84)).decode()


# ================================================================================================== the service
class BrandOnboard:
    def __init__(self, house, bs):
        self.house, self.bs, self.studio, self.store = house, bs, house.studio, house.store

    # ---------------------------------------------------------------- extract
    def extract(self, a, url: str) -> dict:
        t0 = time.time()
        self.studio._guard_write(a, "extract_brand")
        url = (url or "").strip()
        if not url:
            raise OnboardError("Paste your brand's website, e.g. linear.app")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        host = urlparse(url).hostname or ""
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host.lower()):
            raise OnboardError("That doesn't look like a website address. Try something like linear.app")
        sid = sample_for_url(url)
        demo = demo_guard.is_demo(a.uid)
        if sid and demo:  # the sample workspace gets the cached extraction: instant, no network
            return self._from_sample(sid, t0)
        if demo and not demo_guard.take(self.store, a.uid, "import"):
            raise OnboardError("Live website import is limited in the free sample (3 a day). Try the Linear or Notion sample "
                               "brands, or connect Buffer to import any site.")
        got = self._live(url)
        out = self._store_draft(a, got)
        out["ms"] = int((time.time() - t0) * 1000)
        self.store.audit(a.uid, a.label, "extract_brand", "read", {"host": host, "logo": got["found"]["logo"]})
        return out

    def _from_sample(self, sid: str, t0: float) -> dict:
        s = sample(sid)
        return {"source": "sample", "sample_id": sid, "draft": s["kit"], "logo": f"sample:{sid}", "logo_url": s["logo_url"],
                "mark_url": f"/static/brands/{sid}/mark.png" if (STATIC_BRANDS / sid / "mark.png").is_file() else "",
                "provenance": s["provenance"], "fonts": s["fonts"], "found": s["found"], "label": SAMPLE_LABEL,
                "extracted_at": s.get("extracted_at"), "ms": int((time.time() - t0) * 1000)}

    def _fetch(self, url: str, limit: int) -> bytes | None:
        return self.house._fetch_bytes(url, limit)

    def _live(self, url: str) -> dict:
        try:
            r = netguard.safe_get(url, http=self.house.http, headers=UA, limit=brand_extract.MAX_HTML_BYTES, timeout=15)
        except netguard.FetchError as e:
            raise OnboardError(str(e))
        except httpx.HTTPError as e:
            raise OnboardError(f"Couldn't open {urlparse(url).hostname} ({e.__class__.__name__}).")
        if r.status_code >= 400:
            raise OnboardError(f"{urlparse(url).hostname} answered HTTP {r.status_code}.")
        final = str(r.url)
        html = r.text
        sheets = brand_extract.stylesheet_urls(final, html)
        css: list[str] = []
        if sheets:
            with cf.ThreadPoolExecutor(max_workers=6) as ex:
                for data in ex.map(lambda u: self._fetch(u, brand_extract.MAX_CSS_BYTES), sheets):
                    if data:
                        css.append(data.decode("utf-8", "replace"))
        got = brand_extract.extract(final, html, css, fetch=self._fetch)
        got["kit"]["website"] = final
        return got

    def _store_draft(self, a, got: dict) -> dict:
        logo_tok = mark_tok = ""
        if got.get("logo_png"):
            logo_tok = self.house.put_media(a, got["logo_png"], "logo.png", kind="logo")
        if got.get("mark_png"):
            mark_tok = self.house.put_media(a, got["mark_png"], "mark.png", kind="logo")
        return {"source": "website", "draft": got["kit"], "logo": logo_tok, "logo_url": self.house.media_url(logo_tok) if logo_tok else "",
                "mark": mark_tok, "mark_url": self.house.media_url(mark_tok) if mark_tok else "", "provenance": got["provenance"],
                "fonts": got["fonts"], "found": got["found"]}

    # ---------------------------------------------------------------- preview
    def _logo(self, a, ref: str | None) -> bytes | None:
        ref = str(ref or "")
        if ref.startswith("sample:"):
            return sample_logo(ref[7:]) if ref[7:] in SAMPLE_IDS else None
        if ref and self.house.media_meta(a, ref):
            m = self.house.get_media(ref)
            return m["data"] if m else None
        return None

    def _draft(self, a, body: dict) -> dict:
        kit = body.get("kit") if isinstance(body.get("kit"), dict) else None
        if kit is None and body.get("sample_id") in SAMPLE_IDS:
            return {**sample(body["sample_id"])["kit"], "_logo": f"sample:{body['sample_id']}"}
        if kit is None:
            kit = self.bs.kit(a)
            return {**kit, "_logo": kit.get("logo") or ""}
        clean: dict[str, Any] = {"name": str(kit.get("name") or "")[:60], "handle": str(kit.get("handle") or "").lstrip("@")[:40],
                                 "voice": str(kit.get("voice") or "")[:300], "label": str(kit.get("label") or "")[:60]}
        cols = {}
        for k in ("primary", "accent", "text", "secondary"):
            v = str((kit.get("colors") or {}).get(k) or "").strip()
            if brand.HEX_RE.fullmatch(v):
                cols[k] = "#" + v.lstrip("#").upper()
        clean["colors"] = cols
        clean["font"] = kit.get("font") if kit.get("font") in templates.HEAD_FONTS else "inter"
        clean["body_font"] = kit.get("body_font") if kit.get("body_font") in templates.BODY_FONTS else None
        clean["headline_case"] = kit.get("headline_case") if kit.get("headline_case") in ("upper", "title") else None
        for card in ("front_card", "end_card"):
            c = kit.get(card) if isinstance(kit.get(card), dict) else {}
            clean[card] = {"style": c.get("style") if c.get("style") in templates.STYLES else "glow",
                           "cta": str(c.get("cta") or "")[:28], "line": str(c.get("line") or "")[:70]}
        clean = {k: v for k, v in clean.items() if v is not None}
        clean["_logo"] = body.get("logo") or ""
        return clean

    def preview(self, a, body: dict, as_urls: bool = False) -> dict:
        t0 = time.time()
        if demo_guard.is_demo(a.uid) and not demo_guard.allowed(self.store, a.uid, "run"):
            raise OnboardError(demo_guard.WHY["run"])
        now = time.time()
        hits = [t for t in _previews.get(a.uid, []) if now - t < 60]
        if len(hits) >= PREVIEWS_PER_MIN:
            raise OnboardError("Too many preview redraws this minute. Pause for a moment.")
        _previews[a.uid] = hits + [now]
        if body.get("url") and not body.get("kit"):  # MCP convenience: extract and preview in one call
            ex = self.extract(a, body["url"])
            body = {"kit": ex["draft"], "logo": ex["logo"]}
        kit = self._draft(a, body)
        b = brand_for(kit, self._logo(a, kit.pop("_logo", "")))
        story = body.get("story") if isinstance(body.get("story"), dict) else None
        out = []
        for ratio, label, im in theme_stills(b, story):
            item = {"ratio": ratio, "label": label}
            if as_urls:
                tok = self.house.put_media(a, templates.jpeg(im), f"brand-{ratio.replace(':', 'x')}.jpg", kind="preview",
                                           meta={"type": "photo", "template": "brand_preview"})
                item["url"] = self.house.media_url(tok)
            else:
                item["url"] = data_url(im, max(160, min(int(body.get("width") or 360), 540)))
            out.append(item)
        return {"stills": out, "kit": kit, "ms": int((time.time() - t0) * 1000)}

    # ---------------------------------------------------------------- save
    def save(self, a, body: dict) -> dict:
        kit = self._draft(a, body)
        ref = kit.pop("_logo", "")
        patch: dict[str, Any] = {k: kit[k] for k in ("name", "handle", "voice", "colors", "font", "body_font", "headline_case",
                                                    "front_card", "end_card", "label") if k in kit}
        if isinstance(body.get("kit"), dict) and body["kit"].get("website"):
            patch["website"] = str(body["kit"]["website"])[:300]
        if str(ref).startswith("sample:") and ref[7:] in SAMPLE_IDS:
            patch["logo"] = self.house.put_media(a, sample_logo(ref[7:]), f"{ref[7:]}-logo.png", kind="logo")
            patch["label"] = SAMPLE_LABEL
        elif ref:
            patch["logo"] = ref  # bs.save checks the token belongs to this workspace
        patch["front_card"] = {"style": (patch.get("front_card") or {}).get("style") or "glow"}
        ec = patch.get("end_card") or {}
        patch["end_card"] = {"style": ec.get("style") or "glow", "cta": ec.get("cta") or "Follow for more", "line": ec.get("line") or ""}
        out = self.bs.save(a, patch)
        self.store.audit(a.uid, a.label, "save_brand_onboarding", "read", {"fields": sorted(patch)})
        return {"kit": out, "saved": True}

    # ---------------------------------------------------------------- the sample queue
    def schedule_sample(self, a, bid: str, body: dict | None = None) -> dict:
        """The sample story in a sample brand into the sample workspace's stand-in Buffer queue. Sample workspace only."""
        if not demo_guard.is_demo(a.uid):
            raise OnboardError("This puts the sample story in the sample queue, so it only runs in the free sample workspace.")
        s = sample(bid)
        if not s:
            raise OnboardError(f"No sample brand {bid!r}. Pick one of {', '.join(SAMPLE_IDS)}.")
        base = (self.house.s.public_url or "").rstrip("/")
        chans = {c["id"]: c for c in self.studio.channels(a)}
        plan = [("demo_ig", s["carousel"], "carousel"), ("demo_li", [s["renders"]["cover"]], "cover")]
        text = (f"{STORY['title']}: {STORY['sub']}\n\n" + " ".join(s["kit"].get("hashtags") or []) +
                f"\n\n({s['kit']['name']}: {SAMPLE_LABEL}, made with HuhQueue)").strip()
        results = []
        for cid, media, what in plan:
            if cid not in chans:
                continue
            p = {"channel_id": cid, "text": text, "mode": (body or {}).get("mode") or "queue", "override_warnings": True,
                 "media_urls": [base + m for m in media]}
            try:
                pv = self.studio.preview_post(a, p)
                r = self.studio.submit_post(a, pv["confirm_token"])
                results.append({"channel_id": cid, "service": chans[cid]["service"], "what": what, "status": r.get("status"),
                                "post_id": (r.get("post") or {}).get("id"), "due_at": (r.get("post") or {}).get("dueAt")})
            except Exception as e:  # one channel failing never stops the other
                results.append({"channel_id": cid, "service": chans[cid]["service"], "what": what, "status": "failed", "error": str(e)[:200]})
        self.store.audit(a.uid, a.label, "schedule_sample_brand", "read", {"brand": bid, "posts": len(results)})
        return {"brand": bid, "queued": results, "note": "Sample queue only: nothing reaches Buffer."}


# ================================================================================================== REST
def attach(house):
    from .brand_routes import attach as bs_attach
    bs, _ = bs_attach(house)
    if not getattr(house, "brand_onboard", None):
        house.brand_onboard = BrandOnboard(house, bs)
    return house.brand_onboard


def add_routes(app, house, actor) -> None:
    from fastapi import Body, Request
    from .actions import ActionError
    ob = attach(house)

    def call(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except OnboardError as e:
            raise ActionError(str(e), 400)

    @app.post("/api/brand/extract")
    def br_extract(request: Request, body: dict = Body(...)):
        return call(ob.extract, actor(request, write=True), str(body.get("url") or ""))

    @app.post("/api/brand/themes-preview")
    def br_preview(request: Request, body: dict = Body(default={})):
        return call(ob.preview, actor(request), body)

    @app.post("/api/brand/onboard")
    def br_save(request: Request, body: dict = Body(...)):
        return call(ob.save, actor(request, write=True), body)

    @app.get("/api/brand/samples")
    def br_samples(request: Request):
        actor(request)
        return {"samples": samples(), "story": STORY, "label": SAMPLE_LABEL}

    @app.post("/api/brand/samples/{bid}/schedule")
    def br_sched(bid: str, request: Request, body: dict = Body(default={})):
        return call(ob.schedule_sample, actor(request, write=True), bid, body)


# ================================================================================================== MCP
TOOLS: list[dict[str, Any]] = [
    {"name": "extract_brand", "description": "Read a brand kit from a public website, with no AI: name, @handle, logo (the header SVG "
     "drawn to PNG, else the touch icon / favicon), colours from the site's CSS tokens (ground, accent, text, second), fonts "
     "mapped to the closest bundled face, and a voice sample from the homepage copy. Returns a DRAFT (nothing is saved) with "
     "where each field came from; pass draft + logo to preview_brand_themes. The sample workspace gets the cached result for "
     "the sample brands (linear.app, notion.com) and a few live imports a day.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string", "description": "e.g. linear.app"}}}},
    {"name": "preview_brand_themes", "description": "Render three stills in a brand's style: a 9:16 Reel cover, a 4:5 carousel cover "
     "and a 1:1 square post, from a kit (extract_brand's draft and logo), a url (extract + preview in one call), a sample brand "
     "(sample_id linear | notion) or, with nothing, the workspace's saved kit. Nothing is saved to the kit; returns image URLs.",
     "inputSchema": {"type": "object", "properties": {"kit": {"type": "object"}, "logo": {"type": "string"}, "url": {"type": "string"},
                                                      "sample_id": {"type": "string", "enum": list(SAMPLE_IDS)},
                                                      "story": {"type": "object", "description": "kicker, title, sub, reel, quote"}}}},
    {"name": "list_sample_brands", "description": "The demo's sample brands (Linear, Notion: sample brands, not affiliated), pre-extracted "
     "from their public homepages, with the kit, where each field came from, and the same story rendered in each brand "
     "(cover, carousel, Reel cover).", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "schedule_sample_brand_story", "description": "Free sample workspace only: put the sample story, rendered in a sample brand, "
     "into the sample Buffer queue (the carousel on Instagram, the cover on LinkedIn). Nothing reaches Buffer.",
     "inputSchema": {"type": "object", "required": ["sample_id"], "properties": {"sample_id": {"type": "string", "enum": list(SAMPLE_IDS)}}}},
]
READ_ONLY = {"list_sample_brands"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    from .actions import ActionError
    ob = attach(studio.house)
    try:
        if name == "extract_brand":
            return ob.extract(actor, str(args.get("url") or ""))
        if name == "preview_brand_themes":
            return ob.preview(actor, args, as_urls=True)
        if name == "list_sample_brands":
            return {"samples": samples(), "story": STORY, "label": SAMPLE_LABEL}
        if name == "schedule_sample_brand_story":
            return ob.schedule_sample(actor, str(args.get("sample_id") or ""))
    except OnboardError as e:
        raise ActionError(str(e), 400)
    return NotImplemented
