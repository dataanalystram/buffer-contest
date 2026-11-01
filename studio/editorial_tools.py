"""The editorial agent tools: a post about ONE company (a YC launch, a product launch) made the way Hark makes the HuhByte
carousel by hand (owner, 1 Nov 2026: "the agent must make the ernd carousel itself").

    research_company -> find_brand_assets -> hero_edit -> write_editorial -> render_editorial -> write_captions -> check

* research_company   the company's own pages (studio.assets.research): YC page data + Launch YC post + website (+ Product
                     Hunt when it answers); figures that differ between pages are resolved to the newest official page and
                     the choice is written down. No model.
* find_brand_assets  downloads the official images (logo, founder avatars, the website's share image and hero art) through
                     netguard, re-encoded, each with its source URL and the rights note. No model.
* hero_edit          the premium cover image (studio.hero_edit.premium_hero): brand art + the product cut out and tilted +
                     ringed founder avatars, plus a before/after sheet. No model.
* write_editorial    the slide copy as an editorial_story spec (cover, 4-5 one-idea slides, founders, follow). Uses
                     HuhQueue's server AI (Claude, budgeted) when it is there, else a rule writer from the sources. Checked
                     against the minimum-content rule (studio.editorial.check_spec) and rewritten once when it fails.
* render_editorial   draws the carousel in the brand kit (studio.editorial.render) for every image channel; a client that
                     wrote its own copy passes ``spec`` (MCP: no HuhQueue AI needed). Video-only channels (YouTube, TikTok)
                     are skipped with a note when there is no real video: no text-on-gradient video, ever.

Every tool is the same over REST (POST /api/agent/runs/{rid}/tools/{name}) and MCP (post_{name}); docs/AGENT_PARITY.md.
"""
from __future__ import annotations

import io
import json
import re

from PIL import Image

from . import assets, demo_guard, editorial, hero_edit
from .actions import ActionError

S = {"type": "string"}
IDS = {"type": "array", "items": {"type": "string"}, "description": "Channel ids; omit for every channel in the run"}
NO_VIDEO_NOTE = "No video found, carousel only."
SKIP_NOTE = "Skipped: {svc} needs a video and none was found (no video, no Reel). The carousel is on the other channels."
VIDEO_ONLY = {"tiktok", "youtube"}

SPEC_SCHEMA = {
    "type": "object",
    "description": "editorial_story copy: {eyebrow, headline, accent[], dek, slides:[{eyebrow, blocks:[{head, body}], accent[], "
                   "note, image}], founders:{eyebrow, blocks:[{head, body}], accent[], note, names[]}, follow_line}. One idea per "
                   "slide, heads <= 7 words, bodies <= 40 words with **bold** key facts, 4-5 content slides.",
}

SPECS = [
    {"name": "research_company",
     "description": "A post about ONE company or launch (a YC startup, a product launch): read its official pages (YC company page "
                    "data, the Launch YC post, its website, Product Hunt when it answers), newest first, and resolve figures that "
                    "differ between pages to the newest official page. No AI. Call first for a company/launch post.",
     "input_schema": {"type": "object", "required": ["name"], "properties": {
         "name": {"type": "string", "description": "The company, e.g. 'ernd' (a '(YC F26)' suffix is fine)"},
         "urls": {"type": "array", "items": S, "description": "Links the person gave (its website, YC page)"}}}},
    {"name": "find_brand_assets",
     "description": "Download the company's official images found by research_company: logo, founder avatars, the website's share "
                    "image (often the product) and hero art. Each keeps its source URL and the rights note 'official press/brand "
                    "image'. No AI.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "hero_edit",
     "description": "Make the editorial cover image from the official assets: the brand's hero art, the product cut out of its stage, "
                    "tilted with a soft shadow, colour-matched, faded into the brand ground, ringed founder avatars. Returns the "
                    "hero and a before/after. No AI.",
     "input_schema": {"type": "object", "properties": {
         "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16"]},
         "founders": {"type": "boolean", "description": "Put the founder avatars on the cover (default true)"}}}},
    {"name": "write_editorial",
     "description": "Write the carousel copy (editorial_story spec: cover, 4-5 one-idea slides, founders, follow) from the research. "
                    "Uses HuhQueue server AI (Claude, budgeted) when available, else the rule writer. Rewrites once when it has "
                    "fewer than 4 content slides. feedback: what to change (e.g. a slide that overflowed).",
     "input_schema": {"type": "object", "properties": {"feedback": S, "angle": S}}},
    {"name": "render_editorial",
     "description": "Render the editorial_story carousel in the brand kit for every image channel (Instagram, LinkedIn, Facebook, "
                    "X...). Uses the spec from write_editorial, or pass spec yourself (an MCP client can write its own copy). "
                    "Video-only channels (YouTube, TikTok) are skipped with a note when there is no real video.",
     "input_schema": {"type": "object", "properties": {"spec": SPEC_SCHEMA, "channel_ids": IDS}}},
]
LABELS = {"research_company": "Research the company", "find_brand_assets": "Find the official images",
          "hero_edit": "Edit the hero image", "write_editorial": "Write the slides", "render_editorial": "Render the editorial carousel"}
WRITES = {"find_brand_assets", "hero_edit", "write_editorial", "render_editorial"}
NO_LLM = {"research_company", "find_brand_assets", "hero_edit", "render_editorial"}
USES_SERVER_AI = {"write_editorial"}

_COMPANY_RE = re.compile(
    r"\b(?:post|carousel|thread|story|piece)\s+(?:about|on)\s+([A-Za-z0-9][\w.&'-]*(?:\s+[A-Z0-9][\w.&'-]*){0,2})"
    r"(?:\s*\((?:YC|Y Combinator)\s*([A-Z]\d{2})\))?", re.I)


def company_request(prompt: str, urls: list[str] | None = None) -> str:
    """The company a request is about when it is a company / launch post ('make a post about ernd (YC F26)...', a YC company
    link), else ''."""
    for u in urls or []:
        m = re.search(r"ycombinator\.com/(?:companies|launches)/([\w-]+)", u or "")
        if m and "companies" in u:
            return m.group(1)
    p = prompt or ""
    m = _COMPANY_RE.search(p)
    if not m:
        return ""
    yc = bool(m.group(2)) or bool(re.search(r"\b(?:YC|Y Combinator)\b", p))
    launch = bool(re.search(r"\b(?:launch(?:es|ed)?|startup|company|founders?)\b", p, re.I))
    if not (yc or launch):
        return ""
    name = m.group(1).strip(" .,'")
    return "" if name.lower() in ("the", "this", "that", "a", "an", "ai", "it") else name


def _plain(s: str) -> str:
    return re.sub(r"\*\*", "", s or "").strip()


def _sentences(text: str) -> list[str]:
    t = re.sub(r"\s+", " ", re.sub(r"[*#\\]+", " ", text or ""))
    return [x.strip() for x in re.split(r"(?<=[.!?])\s+", t) if 6 <= len(x.split()) <= 40]


def _batch(yc: dict | None) -> str:
    b = (yc or {}).get("batch") or ""
    m = re.match(r"(Fall|Summer|Winter|Spring)\s+(\d{4})", b or "")
    if m:
        return m.group(1)[0] + m.group(2)[2:]
    return b if re.match(r"^[A-Z]\d{2}$", b) else ""


class EditorialTools:
    """Mixed into agent_tools.Tools (same run state, guard, model_json, _keep, _set_media)."""

    # ---------------------------------------------------------------- research
    def t_research_company(self, name: str, urls: list | None = None) -> dict:
        name = re.sub(r"\s+", " ", str(name or "")).strip()[:80]
        if not name:
            raise ActionError("Name the company (research_company name='ernd').")
        if self.demo and not demo_guard.take(self.store, self.a.uid, "research"):
            raise ActionError(demo_guard.WHY["research"])
        run = self.state()
        res = run["result"]
        urls = [u for u in (urls or (run["input"].get("intake") or {}).get("urls") or []) if isinstance(u, str)][:4]
        r = assets.research(name, urls, http=self.h.http)
        if not r["sources"]:
            raise ActionError(f"I couldn't find official pages for {name} (no YC page, no website). Paste its website or YC link.")
        conf = assets.conflicts(r["sources"])
        yc = r.get("yc") or {}
        comp = {"name": r["name"], "slug": r["slug"], "batch": _batch(yc), "batch_name": yc.get("batch_name") or yc.get("batch") or "",
                "one_liner": yc.get("one_liner") or "", "website": yc.get("website") or next(
                    (s["url"] for s in r["sources"] if s["kind"] == "website"), ""),
                "tags": (yc.get("tags") or [])[:4], "founders": r["founders"][:4], "notes": r["notes"], "decisions": conf,
                "sources": [{**s, "text": (s.get("text") or "")[:5000]} for s in sorted(r["sources"], key=lambda s: assets.TRUST.get(s["kind"], 9))],
                "candidates": r["images"][:14]}
        res["company"] = comp
        site = next((s for s in comp["sources"] if s["kind"] == "website"), None)
        launch = next((s for s in comp["sources"] if s["kind"] == "launch"), None)
        primary = (urls[0] if urls else "") or comp["website"] or (yc.get("url") or "")
        facts = self._rule_facts(comp)
        res["source"] = {"url": primary, "title": comp["one_liner"] or (site or {}).get("title", ""), "description": comp["one_liner"],
                         "image": next((c["url"] for c in comp["candidates"] if c["role"] == "product"), ""),
                         "site": comp["name"], "via": "research", "post": None, "media": [], "media_note": "",
                         "paragraphs": [x for x in [(launch or {}).get("text", "")[:1500], (site or {}).get("text", "")[:1500]] if x]}
        who = " ".join(x for x in ["Launch YC" if launch else "", "its website" if site else ""] if x) or "its YC page"
        res["story"] = {"headline": (comp["one_liner"] or f"{comp['name']} launches")[:120], "facts": facts[:4],
                        "why": comp["one_liner"], "label": "Launch", "source": f"{comp['name']} ({who})", "url": primary,
                        "image": res["source"]["image"], "company": comp["name"], "keyword": comp["name"], "engine": "research"}
        res.setdefault("conflicts", [])  # differing figures are resolved to the newest official page, never held
        self.save(run)
        return {"company": comp["name"], "batch": comp["batch"], "one_liner": comp["one_liner"], "website": comp["website"],
                "sources": [{"kind": s["kind"], "url": s["url"], "date": s["date"], "words": len(s["text"].split())} for s in comp["sources"]],
                "founders": [f["name"] for f in comp["founders"]], "images_found": len(comp["candidates"]),
                "decisions": [c["why"] for c in conf], "notes": comp["notes"]}

    @staticmethod
    def _rule_facts(comp: dict) -> list[str]:
        out = []
        for s in comp["sources"]:
            for x in _sentences(s.get("text", "")):
                if re.search(r"\d", x) and not any(_same_fact(x, y) for y in out):
                    out.append(x)
        for c in comp.get("decisions") or []:  # the newest official figure wins in the facts too
            for k, v in c["values"].items():
                if v != c["use"]:
                    out = [f for f in out if v not in f or c["use"] in f]
        return out[:6]

    # ---------------------------------------------------------------- images
    def t_find_brand_assets(self) -> dict:
        run = self.state()
        res = run["result"]
        comp = res.get("company")
        if not comp:
            raise ActionError("Research the company first (research_company).")
        want = {"logo": 1, "founder": 4, "product": 1, "hero": 1, "product_hunt": 1}
        got: list[dict] = []
        skipped = []
        for c in comp["candidates"]:
            if sum(1 for g in got if g["role"] == c["role"]) >= want.get(c["role"], 0):
                continue
            fetched = assets.fetch_image(c["url"], http=self.h.http, min_side=96 if c["role"] in ("founder", "logo") else 300)
            if not fetched:
                skipped.append({"role": c["role"], "url": c["url"].split("?")[0][:160]})
                continue
            data, meta = fetched
            src = c["url"].split("?")[0]  # signed S3 links expire: keep the stable part as the credit
            m = self._keep(data, f"{c['role']}.{meta['ext']}", {"type": "photo", "role": c["role"], "source_url": src, "page": c.get("page", ""),
                                                               "rights": meta["rights"], "width": meta["width"], "height": meta["height"],
                                                               **({"name": c["name"]} if c.get("name") else {})}, c["role"])
            got.append({**m, "source_url": src, "page": c.get("page", ""), "rights": meta["rights"], "name": c.get("name", ""),
                        "width": meta["width"], "height": meta["height"]})
        res["brand_assets"] = got
        self.save(run)
        return {"assets": [{"role": g["role"], "url": g["url"], "source": g["source_url"], "size": f"{g['width']}x{g['height']}",
                            **({"name": g["name"]} if g["name"] else {})} for g in got],
                "rights": assets.RIGHTS, "skipped": skipped[:6]}

    def _asset_img(self, a: dict | None) -> Image.Image | None:
        if not a:
            return None
        m = self.h.get_media(a["token"])
        if not m:
            return None
        try:
            im = Image.open(io.BytesIO(m["data"]))
            im.load()
            return im
        except Exception:  # noqa: BLE001
            return None

    def _founder_imgs(self, res: dict) -> tuple[list[Image.Image], list[str]]:
        ims, names = [], []
        for a in res.get("brand_assets") or []:
            if a["role"] == "founder":
                im = self._asset_img(a)
                if im is not None:
                    ims.append(im)
                    names.append(a.get("name") or "")
        return ims, names

    def t_hero_edit(self, ratio: str = "4:5", founders: bool | None = None) -> dict:
        run = self.state()
        res = run["result"]
        if not res.get("brand_assets"):
            raise ActionError("Find the official images first (find_brand_assets).")
        W, H = editorial.SIZES.get(ratio if ratio in editorial.SIZES else "4:5")
        by = {}
        for a in res["brand_assets"]:
            by.setdefault(a["role"], a)
        scene = self._asset_img(by.get("hero"))
        product = self._asset_img(by.get("product") or by.get("product_hunt"))
        fims, _names = self._founder_imgs(res) if founders is not False else ([], [])
        kit = self._ctx()[0]
        from . import templates as T
        ground = T.Brand(kit).bg
        if scene is None and product is None:
            raise ActionError("No hero art or product image was found; the cover uses the brand ground instead (render_editorial).")
        hero, rep = hero_edit.premium_hero(W, H, scene=scene, product=product, founders=fims[:3], ground=ground)
        band, _ = hero_edit.premium_hero(W, 560, scene=scene, product=product, founders=None, ground=ground, fade_from=0.7,
                                         product_y=0.1, product_scale=0.5)
        buf = io.BytesIO()
        hero.save(buf, "JPEG", quality=92)
        h = self._keep(buf.getvalue(), "hero.jpg", {"type": "photo", "role": "hero_edit", "width": W, "height": H, "steps": rep["steps"]}, "hero")
        buf = io.BytesIO()
        band.save(buf, "JPEG", quality=92)
        bd = self._keep(buf.getvalue(), "band.jpg", {"type": "photo", "role": "band", "width": W, "height": 560}, "band")
        before = product or scene
        buf = io.BytesIO()
        hero_edit.before_after(before, hero).save(buf, "JPEG", quality=88)
        ba = self._keep(buf.getvalue(), "before_after.jpg", {"type": "photo", "role": "before_after"}, "before_after")
        credits = sorted({a["source_url"].split("/")[2] for a in res["brand_assets"] if a.get("source_url", "").startswith("http")})
        res["hero"] = {"token": h["token"], "url": h["url"], "band": bd["token"], "before_after": ba["url"], "steps": rep["steps"],
                       "credit": "Images: official press/brand images (" + ", ".join(credits) + ")"}
        self.save(run)
        return {"hero": h["url"], "before_after": ba["url"], "steps": rep["steps"], "cutout": rep["cutout"], "founders": len(fims[:3])}

    # ---------------------------------------------------------------- copy
    def t_write_editorial(self, feedback: str = "", angle: str = "") -> dict:
        run = self.state()
        res = run["result"]
        comp = res.get("company")
        if not comp:
            raise ActionError("Research the company first (research_company).")
        facts_n = len(self._rule_facts(comp)) + sum(len(_sentences(s["text"])) // 3 for s in comp["sources"])
        spec, engine, problems, rewrote = None, "rules", [], False
        for attempt in range(2):
            d = self._llm_spec(comp, feedback=feedback if attempt == 0 else "; ".join(problems), angle=angle,
                               prior=spec if attempt else None)
            if not isinstance(d, dict):
                break
            spec, engine = self._clean_spec(d, comp), "claude"
            problems = editorial.check_spec(spec, facts_n)
            if not problems:
                break
            rewrote = attempt == 0
        if spec is None:
            spec = self._rule_spec(comp)
            problems = editorial.check_spec(spec, facts_n)
        res["editorial"] = {"spec": spec, "engine": engine, "problems": problems, "rewrote": rewrote}
        self._story_from_spec(res, spec)
        self.save(run)
        return {"engine": engine, "slides": editorial.content_slides(spec) + 2, "content_slides": editorial.content_slides(spec),
                "headline": spec.get("headline"), "problems": problems, "rewrote_once": rewrote,
                "outline": [((s.get("eyebrow") or "") + ": " + "; ".join(b.get("head", "") for b in s.get("blocks") or [])) for s in spec.get("slides") or []]}

    def _llm_spec(self, comp: dict, feedback: str = "", angle: str = "", prior: dict | None = None):
        kit = self._ctx()[0]
        src = [{"kind": s["kind"], "url": s["url"], "date": s["date"], "text": s["text"][:3500]} for s in comp["sources"]]
        brief = {
            "company": comp["name"], "yc_batch": comp["batch"], "one_liner": comp["one_liner"], "tags": comp["tags"],
            "founders": [{"name": f["name"], "title": f["title"], "bio": f["bio"][:300]} for f in comp["founders"]],
            "figure_decisions": [c["why"] for c in comp.get("decisions") or []],
            "sources_newest_official_first": src, "brand": {"name": kit.get("name", ""), "handle": kit.get("handle", "")},
            **({"angle": angle} if angle else {}), **({"fix_these": feedback} if feedback else {}),
            **({"previous_spec": prior} if prior else {}),
            "format": (
                "Return JSON {eyebrow, headline, accent, dek, slides, founders, follow_line}. eyebrow: 'YC F26 · AI + FINTECH' style "
                "(batch + 1-2 categories, upper case). headline: max 10 words, Title Case, a hook with the key figure (e.g. 'A Credit "
                "Card That Pays You 4% Back On AI'). accent: the 1-2 words of the headline in the accent colour (the figure). dek: "
                "one plain line, max 14 words, naming the company and batch. slides: 3 or 4 content slides (the founders slide comes on "
                "top, 7 slides at most in all), ONE idea each: "
                "{eyebrow (2-4 words, e.g. HOW IT WORKS, WHY IT CAN BE CHEAPER, THE MATH), blocks: 1-2 x {head (max 6 words, Title "
                "Case, lead with a figure when there is one), body (max 32 words, plain words a friend would get, **bold** the key "
                "facts and figures)}, accent: [1 word of a head], note: an optional small grey footnote (source/terms, max 12 "
                "words), image: true on exactly one slide (the numbers slide: it shows the product)}. founders: {eyebrow 'The "
                "founders', blocks: [1 block: a fun short head + who they are and where they worked], accent, note: 'Left to right: "
                "<full names>', names: [full names in order]}. follow_line: 'AI news made simple, every day.'. Use ONLY facts from "
                "the sources; keep every figure exactly as written; where pages differ use figure_decisions. No hype words.")}
        sys_ = ("You are the editor of an AI news brand writing an Instagram editorial carousel about one company. Plain words, "
                "one idea per slide, real figures only.")
        return self.model_json("editorial", sys_, json.dumps(brief), max_tokens=4000)

    @staticmethod
    def _clean_spec(d: dict, comp: dict) -> dict:
        def blocks(v):
            out = []
            for b in (v or [])[:2]:
                if isinstance(b, dict) and str(b.get("head") or "").strip():
                    out.append({"head": str(b["head"]).strip()[:80], "body": str(b.get("body") or "").strip()[:400]})
            return out

        def acc(v):  # accent words one by one ("4% Back" -> "4%", "Back"): the renderer matches single words
            words = [w for x in (v if isinstance(v, list) else [v] if v else []) for w in str(x).split()]
            return [w[:30] for w in words][:3]
        slides = []
        for s in (d.get("slides") or [])[:6]:
            if isinstance(s, dict) and blocks(s.get("blocks")):
                slides.append({"eyebrow": str(s.get("eyebrow") or "")[:40], "blocks": blocks(s.get("blocks")), "accent": acc(s.get("accent")),
                               "note": str(s.get("note") or "")[:120], "image": bool(s.get("image"))})
        fo = d.get("founders") if isinstance(d.get("founders"), dict) else {}
        names = [str(n)[:60] for n in (fo.get("names") or [f["name"] for f in comp["founders"]])][:4]
        spec = {"eyebrow": str(d.get("eyebrow") or "")[:48], "headline": str(d.get("headline") or comp["one_liner"])[:110],
                "accent": acc(d.get("accent")), "dek": str(d.get("dek") or "")[:140], "slides": slides,
                "follow_line": str(d.get("follow_line") or "")[:60]}
        if blocks(fo.get("blocks")):
            spec["founders"] = {"eyebrow": str(fo.get("eyebrow") or "The founders")[:40], "blocks": blocks(fo.get("blocks"))[:1],
                                "accent": acc(fo.get("accent")), "note": str(fo.get("note") or "")[:120], "names": names}
        return spec

    @staticmethod
    def _rule_spec(comp: dict) -> dict:
        """No model: the spec from the sources' own words (launch-post sections, figure sentences, the founders)."""
        name = comp["name"]
        sents: list[str] = []
        for s in comp["sources"]:
            for x in _sentences(s["text"]):
                if not any(_same_fact(x, y) for y in sents):
                    sents.append(x)
        ranked = sorted(sents, key=lambda x: (0 if re.search(r"\d", x) else 1, len(x)))
        slides = []
        heads = ["How it works", "Why it matters", "The numbers", "What's next", "The details"]
        for i, x in enumerate(ranked[:4 if comp.get("founders") else 5]):  # 7 slides at most with the cover, founders, follow
            words = re.sub(r"[^\w$%.,' -]", "", x).split()
            fig = next((w for w in words if re.search(r"\d", w)), "")
            head = " ".join(([fig] if fig else []) + [w for w in words if w != fig][:5 - (1 if fig else 0)]).rstrip(",.")
            body = re.sub(r"(\$?\d[\d,.]*\s?%?(?:\s?(?:back|a year|a month))?)", r"**\1**", x)
            slides.append({"eyebrow": heads[i % len(heads)], "blocks": [{"head": head, "body": body}], "accent": [fig] if fig else [],
                           "note": "", "image": i == 2})
        fo = comp["founders"]
        spec = {"eyebrow": " · ".join(x for x in [f"YC {comp['batch']}" if comp["batch"] else "", (comp["tags"] or [""])[0].upper()] if x),
                "headline": comp["one_liner"] or f"Meet {name}", "accent": [], "dek": f"{name}" + (f" (YC {comp['batch']})" if comp["batch"] else "")
                + (": " + comp["one_liner"] if comp["one_liner"] else ""), "slides": slides, "follow_line": ""}
        if fo:
            who = ", ".join(f"{f['name']} ({f['title']})" if f.get("title") else f["name"] for f in fo[:3])
            spec["founders"] = {"eyebrow": "The founders", "blocks": [{"head": "Who Is Building It", "body": who[:240]}], "accent": [],
                                "note": "", "names": [f["name"] for f in fo[:4]]}
        return spec

    @staticmethod
    def _story_from_spec(res: dict, spec: dict) -> None:
        """The story the captions are written from follows the slides (same facts, same figures)."""
        st = res.get("story") or {}
        facts = []
        for s in spec.get("slides") or []:
            for b in s.get("blocks") or []:
                facts.append((_plain(b.get("head", "")) + ": " + _plain(b.get("body", ""))).strip(": ")[:220])
        fo = spec.get("founders") or {}
        if fo.get("blocks"):
            facts.append("Founders: " + ", ".join(fo.get("names") or []) + ". " + _plain(fo["blocks"][0].get("body", ""))[:160])
        st.update(headline=_plain(spec.get("headline") or st.get("headline", ""))[:120], facts=facts[:6] or st.get("facts") or [],
                  why=_plain(spec.get("dek") or st.get("why", ""))[:200])
        res["story"] = st

    # ---------------------------------------------------------------- render
    def t_render_editorial(self, spec: dict | None = None, channel_ids: list | None = None) -> dict:
        from . import templates as T
        run = self.state()
        res = run["result"]
        given = isinstance(spec, dict) and bool(spec)
        if given:
            spec = self._clean_spec(spec, res.get("company") or {"name": "", "one_liner": "", "founders": []})
            res["editorial"] = {"spec": spec, "engine": "client", "problems": editorial.check_spec(spec), "rewrote": False}
            if res.get("story") or res.get("company"):
                self._story_from_spec(res, spec)
        ed = res.get("editorial") or {}
        spec = ed.get("spec")
        if not spec:
            raise ActionError("Write the slides first (write_editorial), or pass spec.")
        if not res.get("story"):
            res["story"] = {"headline": _plain(spec.get("headline", ""))[:120], "facts": [], "why": _plain(spec.get("dek", "")),
                            "label": "Launch", "source": "", "url": "", "image": ""}
            self._story_from_spec(res, spec)
        kit, _rules, _p, logo = self._ctx()
        b = T.Brand(kit, logo)
        hero = self._asset_img({"token": (res.get("hero") or {}).get("token")}) if (res.get("hero") or {}).get("token") else None
        band = self._asset_img({"token": res["hero"]["band"]}) if (res.get("hero") or {}).get("band") else None
        fims, fnames = self._founder_imgs(res)
        names = (spec.get("founders") or {}).get("names") or []
        if names and fnames:  # photos in the order the copy names them
            order = sorted(range(len(fims)), key=lambda i: next((k for k, n in enumerate(names) if fnames[i] and fnames[i].split()[0] in n), 99))
            fims = [fims[i] for i in order]
        chans = self.channels(run, channel_ids)
        has_video = self._has_video(run)
        skipped, by_ratio = [], {}
        for c in chans:
            if c["service"] in VIDEO_ONLY:
                if not has_video:
                    skipped.append(c)
                continue  # a real video exists: render_reel makes that channel's video
            r = (T.CHANNELS.get(c["service"]) or {}).get("ratio", "4:5")
            by_ratio.setdefault(r if r in editorial.SIZES else "4:5", []).append(c["id"])
        out = {"channels": [], "media": [], "format": "carousel", "skipped": [], "fit_problems": [], "problems": ed.get("problems") or []}
        allm = {}
        for ratio, ids in by_ratio.items():
            ims, fit = editorial.render(b, spec, hero=hero, band=band, founders=fims, ratio=ratio)
            media = []
            for i, im in enumerate(ims):
                buf = io.BytesIO()
                im.convert("RGB").save(buf, "JPEG", quality=92)
                media.append(self._keep(buf.getvalue(), f"editorial-{i + 1}.jpg", {"type": "photo", "template": "editorial_story",
                                                                                     "width": im.width, "height": im.height}))
            note = (NO_VIDEO_NOTE + " " if not has_video else "") + ((res.get("hero") or {}).get("credit") or "")
            self._set_media(run, ids, media, "carousel", {"template": "editorial_story", "ratio": ratio, "note": note.strip()})
            allm[ratio] = media
            out["channels"] += ids
            out["media"] += [m["url"] for m in media]
            out["fit_problems"] += [p for p in fit.problems if p not in out["fit_problems"]]
        res["editorial_media"] = {r: m for r, m in allm.items()}
        for c in skipped:
            self._skip_channel(res, c)
            out["skipped"].append({"channel": c["service"], "note": SKIP_NOTE.format(svc=c["service"].title())})
        if not has_video:
            self._plan_note(res)
        res["editorial"]["fit_problems"] = out["fit_problems"]
        self.save(run)
        out["clipped"] = out["fit_problems"]
        out["slides"] = len(next(iter(allm.values()), []))
        return out

    # ---------------------------------------------------------------- no video, no Reel
    def _has_video(self, run) -> bool:
        """A REAL video exists for this run: the person's upload, the source's own clip, or one find_media can fetch."""
        res = run["result"]
        if res.get("clip_token") or (res.get("upload") or {}).get("type") == "video":
            return True
        if any(m.get("type") == "video" for m in (res.get("source") or {}).get("media") or []):
            return True
        found = res.get("found_media") if isinstance(res.get("found_media"), dict) else {}
        return bool(found.get("best_downloadable"))

    @staticmethod
    def _skip_channel(res: dict, c: dict) -> None:
        res.setdefault("channel_media", {})[c["id"]] = {"format": "skipped", "media": [],
                                                        "note": SKIP_NOTE.format(svc=c["service"].title())}
        (res.get("captions") or {}).pop(c["id"], None)

    @staticmethod
    def _plan_note(res: dict) -> None:
        plan = res.get("plan") if isinstance(res.get("plan"), list) else []
        if not any("no video found" in str(p).lower() for p in plan):
            plan.append(NO_VIDEO_NOTE[:-1])
            res["plan"] = plan


def _same_fact(a: str, b: str) -> bool:
    wa, wb = set(re.findall(r"[a-z0-9$%.]+", a.lower())), set(re.findall(r"[a-z0-9$%.]+", b.lower()))
    return bool(wa and wb) and len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.6
