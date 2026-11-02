"""Brand kits: a workspace keeps several kits and one is ACTIVE; every template, theme, carousel, thumbnail and end card
renders from the active one (owner, 2 Nov 2026). UI = REST = MCP.

  GET    /api/brand-kits                     list the kits and which is active            MCP list_brand_kits
  POST   /api/brand-kits                     create (from fields, {url} to read a site, or {sample} for
                                             one of the sample brands: nothing, linear, notion)    MCP create_brand_kit
  PATCH  /api/brand-kits/{id}                update fields                                 MCP update_brand_kit
  POST   /api/brand-kits/{id}/activate       make it the active kit                        MCP set_active_brand_kit
  DELETE /api/brand-kits/{id}                remove one (never the active one)
  POST   /api/brand-kits/preview             the whole gallery in a kit as you edit: every video theme card, the
                                             carousel cover, the thumbnail and the end card (nothing stored)  MCP preview_brand_kit
  POST   /api/brand-kits/palette             colours suggested from a logo (background, primary, accent, text)

A kit's fields, as the builder shows them -> where they live in the kit the renderers read:
  name, handle, logo (media token), website, voice
  background -> colors.primary (the ground)   primary -> colors.accent   accent -> colors.secondary   text -> colors.text
  head_font / body_font (anton, poppins, inter, figtree, serif, dot), headline_case, caption_style (opus, news, minimal,
  hype), end_card {cta, line, style}, front_card {style}

Activating a kit writes it through BrandStudio.save, the one place templates read the brand from, so nothing else changes
and every renderer follows. The free sample workspace starts with two kits: the "Nothing" sample brand (active; extracted
from nothing.tech, "Sample brand, not affiliated") and HuhByte AI.
"""
from __future__ import annotations

from fastapi import Body, Request  # module level: FastAPI resolves the string annotations here

import base64
import hashlib
import io
import json
import os
import tempfile
import time
from typing import Any

from . import brand, brand_examples, brand_onboard, demo_guard, templates

SCHEMA = "CREATE TABLE IF NOT EXISTS brand_kit_library (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, name TEXT, data TEXT, created REAL, updated REAL)"
MAX_KITS = 12
CAPTION_STYLES = {"opus": "Opus: big word-by-word captions", "news": "News: a sentence under the video",
                  "minimal": "Minimal: calm lower captions", "hype": "Hype: one huge word at a time"}
GALLERY_PER_MIN = 40  # the builder redraws as you type (debounced); kit cards and themes are separate calls
_gallery: dict[str, list[float]] = {}
_cache: dict[str, dict] = {}
HOOK = "Shipped this week"
KIT_CARDS = ("carousel_cover", "thumbnail", "end_card")
FONT_LABELS = {"anton": "Anton", "poppins": "Poppins", "inter": "Inter", "figtree": "Figtree", "serif": "DM Serif Display",
               "dot": "Doto (dot matrix)", "inter-semibold": "Inter SemiBold"}
BODY_CHOICES = ("inter", "inter-semibold", "poppins", "figtree")


class KitError(ValueError):
    pass


def ensure(store) -> None:
    sql = SCHEMA
    if store.pg:
        sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
    store.x(sql)
    if store.pg:
        store.x("ALTER TABLE brand_kit_library ENABLE ROW LEVEL SECURITY")


def _hex(v) -> str | None:
    v = str(v or "").strip()
    return "#" + v.lstrip("#").upper() if brand.HEX_RE.fullmatch(v) else None


def normalize(body: dict, base: dict | None = None) -> dict:
    """Builder fields (or a renderer-shaped kit) -> the stored kit, validated. Unknown fields are dropped."""
    k: dict[str, Any] = json.loads(json.dumps(base or {}))
    for f, n in (("name", 60), ("handle", 40), ("website", 300), ("voice", 300), ("label", 60)):
        if f in body:
            k[f] = str(body.get(f) or "").strip()[:n]
    if "handle" in k:
        k["handle"] = k["handle"].lstrip("@")
    cols = dict(k.get("colors") or {})
    src = body.get("colors") if isinstance(body.get("colors"), dict) else {}
    if "background" in src:  # the builder's names
        for bk, kk in (("background", "primary"), ("primary", "accent"), ("accent", "secondary"), ("text", "text")):
            if _hex(src.get(bk)):
                cols[kk] = _hex(src.get(bk))
    else:  # the renderers' names (primary = ground)
        for kk in ("primary", "accent", "secondary", "text"):
            if _hex(src.get(kk)):
                cols[kk] = _hex(src.get(kk))
    if cols:
        bg = templates.hex_rgb(cols.get("primary") or "#111114")
        if not cols.get("text") or templates.contrast(templates.hex_rgb(cols["text"]), bg) < 3:
            cols["text"] = "#FFFFFF" if templates._lum(bg) < 0.4 else "#111114"
        k["colors"] = cols
    hf = body.get("head_font", body.get("font"))
    if hf in templates.HEAD_FONTS:
        k["font"] = hf
    if body.get("body_font") in templates.BODY_FONTS:
        k["body_font"] = body["body_font"]
    if body.get("headline_case") in ("upper", "title"):
        k["headline_case"] = body["headline_case"]
    elif hf in templates.HEAD_FONTS:
        k["headline_case"] = "upper" if hf in ("anton", "dot") else "title"
    if body.get("caption_style") in CAPTION_STYLES:
        k["caption_style"] = body["caption_style"]
    if "logo" in body:
        k["logo"] = str(body.get("logo") or "")[:200]
    ec = dict(body.get("end_card")) if isinstance(body.get("end_card"), dict) else {}
    if "end_cta" in body:
        ec["cta"] = body["end_cta"]
    if "end_text" in body:
        ec["line"] = body["end_text"]
    if ec:
        cur = dict(k.get("end_card") or {})
        for f, n in (("cta", 28), ("line", 70)):
            if f in ec:
                cur[f] = str(ec.get(f) or "")[:n]
        if ec.get("style") in templates.STYLES:
            cur["style"] = ec["style"]
        k["end_card"] = cur
    fc = body.get("front_card") if isinstance(body.get("front_card"), dict) else {}
    if fc.get("style") in templates.STYLES:
        k["front_card"] = {**(k.get("front_card") or {}), "style": fc["style"]}
    if isinstance(body.get("hashtags"), list):
        k["hashtags"] = [str(t)[:40] for t in body["hashtags"][:8]]
    k.setdefault("colors", {"primary": "#111114", "accent": brand.NEUTRAL_ACCENT, "text": "#FFFFFF"})
    k.setdefault("font", "inter")
    k.setdefault("caption_style", "opus")
    dark = templates._lum(templates.hex_rgb(k["colors"].get("primary") or "#111114")) < 0.4
    k.setdefault("front_card", {"style": "glow" if dark else "minimal"})
    k.setdefault("end_card", {})
    k["end_card"].setdefault("style", k["front_card"].get("style") or "glow")
    k["end_card"].setdefault("cta", f"Follow {k.get('name') or 'us'}"[:28])
    k["end_card"].setdefault("line", "")
    return k


def builder_view(k: dict) -> dict:
    """The stored kit with the builder's colour names alongside."""
    c = k.get("colors") or {}
    return {**k, "builder_colors": {"background": c.get("primary"), "primary": c.get("accent"), "accent": c.get("secondary") or c.get("accent"),
                                    "text": c.get("text")}}


# ================================================================================================== the gallery
def compose_brand(kit: dict, logo_path: str | None) -> dict:
    """The video renderer's brand dict (studio/media/compose.py) from a kit, with every key set so nothing falls back to
    another brand's defaults."""
    c = kit.get("colors") or {}
    bg, text = c.get("primary") or "#111114", c.get("text") or "#FFFFFF"
    head = templates.HEAD_FONTS.get(kit.get("font") or "inter", "Inter-Bold.ttf")
    body = templates.BODY_FONTS.get(kit.get("body_font") or "inter", "Inter-Regular.ttf")
    bold = {"Inter-Regular.ttf": "Inter-Bold.ttf", "Poppins-Medium.ttf": "Poppins-Bold.ttf", "Figtree-Medium.ttf": "Figtree-ExtraBold.ttf"}.get(body, body)
    mixed = templates.mix(templates.hex_rgb(text), templates.hex_rgb(bg), 0.4)
    acc = c.get("accent") or brand.NEUTRAL_ACCENT
    return {"name": kit.get("name") or "", "handle": ("@" + kit["handle"]) if kit.get("handle") else "", "accent": acc,
            "bg": bg, "text": text, "muted": "#%02X%02X%02X" % mixed, "logo": logo_path, "head_font": head, "body_font": body,
            "bold_font": bold, "end_line": (kit.get("end_card") or {}).get("line") or "",
            "on_accent": "#111114" if templates._lum(templates.hex_rgb(acc)) > 0.35 else "#FFFFFF"}


def theme_cards_for(kit: dict, logo: bytes | None, W: int = 360, H: int = 640, hook: str = HOOK) -> list[dict]:
    """Every look in the Video style gallery (the four edit styles and every theme), its title card drawn in this kit:
    the kit's logo, name, @handle, fonts and accent; a theme keeps its own ground and type (that is the theme)."""
    from PIL import Image
    from .media import compose, styles, theme_cards
    out = []
    with tempfile.TemporaryDirectory(prefix="hqkit-") as td:
        lp = None
        if logo:
            lp = os.path.join(td, "logo.png")
            with open(lp, "wb") as f:
                f.write(logo)
        b0 = compose_brand(kit, lp)
        for sid in styles.ALL_IDS:
            try:
                cfg = styles.config(sid)
                b = styles.apply_palette(dict(b0), cfg, keep_brand=True) if cfg.get("theme") else dict(b0)
                p = os.path.join(td, f"{sid}.png")
                if cfg.get("card"):
                    theme_cards.cover(cfg, p, W, H, b, hook, None, label=cfg.get("cover_label") or "WATCH")
                else:
                    compose.cover_card(p, W, H, b, hook, None, label=cfg.get("cover_label") or "WATCH")
                im = Image.open(p).convert("RGB")
                meta = styles.THEMES.get(sid) or styles.STYLE_PRESETS.get(sid) or {}
                out.append({"id": sid, "label": meta.get("label") or sid, "image": im})
            except Exception as e:  # one theme failing never blanks the gallery
                out.append({"id": sid, "label": sid, "error": e.__class__.__name__})
    return out


def kit_cards(kit: dict, logo: bytes | None) -> list[dict]:
    b = brand_onboard.brand_for(kit, logo)
    st = brand_onboard.STORY
    return [{"id": "carousel_cover", "label": "Carousel cover 4:5", "image": templates.front_card(b, "4:5", st["kicker"], st["title"], st["sub"], n=(1, 5))},
            {"id": "thumbnail", "label": "Thumbnail 16:9", "image": templates.front_card(b, "16:9", "New", st["reel"], "")},
            {"id": "end_card", "label": "End card", "image": templates.end_card(b, "4:5")}]


def _jpeg_url(im, w: int) -> str:
    t = im.copy()
    t.thumbnail((w, w * 2))
    return "data:image/jpeg;base64," + base64.b64encode(templates.jpeg(t, 80)).decode()


# ================================================================================================== the service
class BrandKits:
    def __init__(self, house, bs, ob):
        self.house, self.bs, self.ob, self.studio, self.store = house, bs, ob, house.studio, house.store
        ensure(self.store)

    # ---------------------------------------------------------------- storage
    def _rows(self, a) -> list[dict]:
        return [dict(r) for r in self.store.q("SELECT * FROM brand_kit_library WHERE user_id=? ORDER BY id", (a.uid,))]

    def _active(self, a) -> int | None:
        v = self.bs.extras(a).get("active_kit")
        return int(v) if v else None

    def _seed_demo(self, a) -> None:
        """The sample workspace starts with Nothing (active) and HuhByte AI, so the builder is filled the moment it opens."""
        if self._rows(a):
            return
        s = brand_onboard.sample("nothing")
        nid = None
        if s:
            k = normalize({**s["kit"], "logo": "sample:nothing"})
            k["label"] = brand_onboard.SAMPLE_LABEL
            k["source"] = {"url": s.get("source_url"), "provenance": s.get("provenance"), "fonts": s.get("fonts"),
                           "extracted_at": s.get("extracted_at")}
            nid = self._insert(a, k)
        hb = brand_examples.EXAMPLES.get("huhbyte")
        if hb:
            self._insert(a, normalize({**hb["kit"], "logo": "example:huhbyte", "caption_style": "news"}))
        if nid:
            self.activate(a, nid)

    def _insert(self, a, k: dict) -> int:
        if len(self._rows(a)) >= MAX_KITS:
            raise KitError(f"Up to {MAX_KITS} brand kits per workspace.")
        now = time.time()
        return self.store.x("INSERT INTO brand_kit_library(user_id,name,data,created,updated) VALUES(?,?,?,?,?)",
                            (a.uid, k.get("name") or "Untitled kit", json.dumps(k), now, now))

    def _get(self, a, kid: int) -> dict:
        rows = self.store.q("SELECT * FROM brand_kit_library WHERE id=? AND user_id=?", (int(kid), a.uid))
        if not rows:
            raise KitError("No such brand kit.")
        return dict(rows[0])

    def _out(self, a, row: dict, active: int | None) -> dict:
        k = json.loads(row["data"] or "{}")
        return {"id": row["id"], "name": row["name"], "active": row["id"] == active, "kit": builder_view(k),
                "logo_url": self._logo_url(a, k.get("logo")), "updated": row["updated"]}

    def _logo_url(self, a, ref: str | None) -> str:
        ref = str(ref or "")
        if ref.startswith("sample:"):
            return f"/static/brands/{ref[7:]}/logo.png" if ref[7:] in brand_onboard.SAMPLE_IDS else ""
        if ref.startswith("example:"):
            return f"/api/brand-kits/example-logo/{ref[8:]}" if ref[8:] in brand_examples.EXAMPLES else ""
        return self.house.media_url(ref) if ref and self.house.media_meta(a, ref) else ""

    def logo_bytes(self, a, ref: str | None) -> bytes | None:
        ref = str(ref or "")
        if ref.startswith("example:"):
            try:
                return brand_examples.logo_bytes(ref[8:]) if ref[8:] in brand_examples.EXAMPLES else None
            except Exception:
                return None
        return self.ob._logo(a, ref)

    # ---------------------------------------------------------------- actions
    def list(self, a) -> dict:
        if demo_guard.is_demo(a.uid):
            self._seed_demo(a)
        act = self._active(a)
        return {"kits": [self._out(a, r, act) for r in self._rows(a)], "active_id": act, "caption_styles": CAPTION_STYLES,
                "fonts": {k: FONT_LABELS.get(k, k) for k in templates.HEAD_FONTS},
                "body_fonts": {k: FONT_LABELS.get(k, k) for k in BODY_CHOICES}, "styles": templates.STYLES,
                "samples": [{"id": s["id"], "name": s["kit"].get("name"), "label": brand_onboard.SAMPLE_LABEL,
                             "logo_url": f"/static/brands/{s['id']}/logo.png"} for s in brand_onboard.samples()],
                "max": MAX_KITS}

    def create(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "create_brand_kit")
        base, src = None, None
        if body.get("sample"):
            s = brand_onboard.sample(str(body["sample"]))
            if not s:
                raise KitError(f"No sample brand {body['sample']!r}. Pick one of {', '.join(brand_onboard.SAMPLE_IDS)}.")
            base = {**s["kit"], "logo": f"sample:{s['id']}", "label": brand_onboard.SAMPLE_LABEL}
            src = {"url": s.get("source_url"), "provenance": s.get("provenance"), "fonts": s.get("fonts")}
        elif body.get("url"):
            ex = self.ob.extract(a, str(body["url"]))
            base = {**ex["draft"], "logo": ex.get("logo") or ""}
            src = {"url": ex["draft"].get("website"), "provenance": ex.get("provenance"), "fonts": ex.get("fonts")}
        k = normalize(body, base)
        if src:
            k["source"] = src
        self._check_logo(a, k)
        kid = self._insert(a, k)
        self.store.audit(a.uid, a.label, "create_brand_kit", "read", {"kit": kid, "from_url": bool(body.get("url"))})
        if body.get("activate"):
            return self.activate(a, kid)
        return self._out(a, self._get(a, kid), self._active(a))

    def update(self, a, kid: int, body: dict) -> dict:
        self.studio._guard_write(a, "update_brand_kit")
        row = self._get(a, kid)
        k = normalize(body, json.loads(row["data"] or "{}"))
        self._check_logo(a, k)
        self.store.x("UPDATE brand_kit_library SET name=?, data=?, updated=? WHERE id=?", (k.get("name") or row["name"], json.dumps(k), time.time(), row["id"]))
        self.store.audit(a.uid, a.label, "update_brand_kit", "read", {"kit": row["id"], "fields": sorted(body)[:20]})
        if row["id"] == self._active(a) or body.get("activate"):
            return self.activate(a, row["id"])
        return self._out(a, self._get(a, kid), self._active(a))

    def _check_logo(self, a, k: dict) -> None:
        ref = str(k.get("logo") or "")
        if ref and not ref.startswith(("sample:", "example:")) and not self.house.media_meta(a, ref):
            raise KitError("Unknown logo. Upload the logo first.")

    def activate(self, a, kid: int) -> dict:
        self.studio._guard_write(a, "set_active_brand_kit")
        row = self._get(a, kid)
        k = json.loads(row["data"] or "{}")
        ref = str(k.get("logo") or "")
        logo_tok = ""
        if ref.startswith(("sample:", "example:")):
            data = self.logo_bytes(a, ref)
            logo_tok = self.house.put_media(a, data, "logo.png", kind="logo") if data else ""
        elif ref:
            logo_tok = ref
        ec = k.get("end_card") or {}
        font = k.get("font") or "inter"
        patch = {"name": k.get("name", ""), "handle": k.get("handle", ""), "website": k.get("website", ""), "voice": k.get("voice", ""),
                 "colors": k.get("colors") or {}, "font": font,
                 "body_font": k.get("body_font") or ("inter" if font in ("anton", "serif", "dot") else font),
                 "headline_case": k.get("headline_case") or ("upper" if font in ("anton", "dot") else "title"),
                 "front_card": {"style": (k.get("front_card") or {}).get("style") or "glow"},
                 "end_card": {"style": ec.get("style") or "glow", "cta": ec.get("cta") or "Follow for more", "line": ec.get("line") or ""},
                 "hashtags": k.get("hashtags") or [], "label": k.get("label") or ""}
        if logo_tok:
            patch["logo"] = logo_tok
        self.bs.save(a, patch)
        if not logo_tok:  # a kit without a logo clears the old one, so no other brand's mark stays on the renders
            self.house.save_kit(a, {"logo": ""})
        ex = self.bs.extras(a)
        ex["active_kit"] = row["id"]
        ex["caption_style"] = k.get("caption_style") or "opus"
        self.bs._put(a, ex)
        self.store.audit(a.uid, a.label, "set_active_brand_kit", "read", {"kit": row["id"]})
        return {**self._out(a, row, row["id"]), "applied": True}

    def delete(self, a, kid: int) -> dict:
        self.studio._guard_write(a, "delete_brand_kit")
        row = self._get(a, kid)
        if row["id"] == self._active(a):
            raise KitError("That's the active kit. Make another kit active first.")
        self.store.x("DELETE FROM brand_kit_library WHERE id=? AND user_id=?", (row["id"], a.uid))
        return {"deleted": row["id"]}

    def palette(self, a, logo_ref: str) -> dict:
        data = self.logo_bytes(a, logo_ref)
        if not data:
            raise KitError("Upload a logo first.")
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        pal = brand.palette_from_image(data, 5)
        px = [p for p in im.resize((64, 64)).getdata() if p[3] > 200]
        avg = sum(brand.luminance(p[:3]) for p in px) / max(1, len(px))
        dark = avg > 0.45  # a light logo goes on a dark ground, a dark one on a light ground
        bg = "#0B0B0D" if dark else "#FAFAF7"
        vivid = [c for c in pal if brand.contrast(brand.hex_to_rgb(c), brand.hex_to_rgb(bg)) >= 1.6]
        primary = vivid[0] if vivid else ("#FFFFFF" if dark else "#111114")
        accent = vivid[1] if len(vivid) > 1 else primary
        return {"background": bg, "primary": primary, "accent": accent, "text": "#FFFFFF" if dark else "#111114", "palette": pal}

    def preview(self, a, body: dict, as_urls: bool = False) -> dict:
        """Every gallery card in the kit as it is being edited: the video themes, the carousel cover, the thumbnail and
        the end card. Cached by the kit's content, so switching back and forth between kits is instant."""
        t0 = time.time()
        if demo_guard.is_demo(a.uid) and not demo_guard.allowed(self.store, a.uid, "run"):
            raise KitError(demo_guard.WHY["run"])
        if body.get("kit_id") and not body.get("kit"):
            k = json.loads(self._get(a, int(body["kit_id"]))["data"] or "{}")
        else:
            k = normalize(body.get("kit") if isinstance(body.get("kit"), dict) else {})
        ref = str(body.get("logo") if body.get("logo") is not None else k.get("logo") or "")
        logo = self.logo_bytes(a, ref)
        w = max(120, min(int(body.get("width") or 220), 400))
        part = body.get("part") if body.get("part") in ("kit", "themes") else "all"
        key = hashlib.sha256(json.dumps([k, ref, w, part, hashlib.sha256(logo or b"").hexdigest()], sort_keys=True).encode()).hexdigest()
        if key in _cache and not as_urls:
            return {**_cache[key], "cached": True, "ms": int((time.time() - t0) * 1000)}
        now = time.time()
        hits = [t for t in _gallery.get(a.uid, []) if now - t < 60]
        if len(hits) >= GALLERY_PER_MIN:
            raise KitError("Too many gallery redraws this minute. Pause for a moment.")
        _gallery[a.uid] = hits + [now]
        cards = (kit_cards(k, logo) if part != "themes" else []) + (theme_cards_for(k, logo) if part != "kit" else [])
        out = []
        for c in cards:
            item = {"id": c["id"], "label": c["label"], "group": "kit" if c["id"] in KIT_CARDS else "theme"}
            if "image" in c:
                if as_urls:
                    tok = self.house.put_media(a, templates.jpeg(c["image"], 84), f"kit-{c['id']}.jpg", kind="preview",
                                               meta={"type": "photo", "template": "brand_kit_preview"})
                    item["url"] = self.house.media_url(tok)
                else:
                    item["url"] = _jpeg_url(c["image"], w)
            else:
                item["error"] = c.get("error")
            out.append(item)
        res = {"cards": out, "kit": builder_view(k), "part": part}
        if not as_urls:
            if len(_cache) > 48:
                _cache.clear()
            _cache[key] = res
        return {**res, "ms": int((time.time() - t0) * 1000)}


def caption_style_for(house, a) -> str:
    """The active kit's caption style (opus | news | minimal | hype): the default look of a captioned video edit when the
    person or agent names no theme. 'opus' when no kit was ever activated."""
    try:
        v = attach(house).bs.extras(a).get("caption_style")
    except Exception:
        v = None
    return v if v in CAPTION_STYLES else "opus"


# ================================================================================================== REST + MCP
def attach(house):
    ob = brand_onboard.attach(house)
    if not getattr(house, "brand_kits", None):
        house.brand_kits = BrandKits(house, ob.bs, ob)
    return house.brand_kits


def add_routes(app, house, actor) -> None:
    from fastapi.responses import Response
    from .actions import ActionError
    bk = attach(house)

    def call(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except (KitError, brand_onboard.OnboardError) as e:
            raise ActionError(str(e), 400)

    @app.get("/api/brand-kits/example-logo/{eid}", include_in_schema=False)
    def kits_example_logo(eid: str):
        if eid not in brand_examples.EXAMPLES:
            return Response(status_code=404)
        return Response(brand_examples.logo_bytes(eid), media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/api/brand-kits")
    def kits_list(request: Request):
        return call(bk.list, actor(request))

    @app.post("/api/brand-kits")
    def kits_create(request: Request, body: dict = Body(...)):
        return call(bk.create, actor(request, write=True), body)

    @app.post("/api/brand-kits/preview")
    def kits_preview(request: Request, body: dict = Body(default={})):
        return call(bk.preview, actor(request), body)

    @app.post("/api/brand-kits/palette")
    def kits_palette(request: Request, body: dict = Body(...)):
        return call(bk.palette, actor(request), str(body.get("logo") or ""))

    @app.patch("/api/brand-kits/{kid}")
    def kits_update(kid: int, request: Request, body: dict = Body(...)):
        return call(bk.update, actor(request, write=True), kid, body)

    @app.post("/api/brand-kits/{kid}/activate")
    def kits_activate(kid: int, request: Request):
        return call(bk.activate, actor(request, write=True), kid)

    @app.delete("/api/brand-kits/{kid}")
    def kits_delete(kid: int, request: Request):
        return call(bk.delete, actor(request, write=True), kid)


_KIT_PROPS = {"name": {"type": "string"}, "handle": {"type": "string"}, "logo": {"type": "string", "description": "media token from upload_media"},
              "colors": {"type": "object", "description": "background, primary, accent, text (hex)"},
              "head_font": {"type": "string", "enum": list(templates.HEAD_FONTS)}, "body_font": {"type": "string", "enum": list(BODY_CHOICES)},
              "caption_style": {"type": "string", "enum": list(CAPTION_STYLES)}, "end_text": {"type": "string"}, "end_cta": {"type": "string"},
              "website": {"type": "string"}, "voice": {"type": "string"}}
TOOLS: list[dict[str, Any]] = [
    {"name": "list_brand_kits", "description": "The workspace's brand kits (name, handle, logo, colours as background / primary / accent / "
     "text, fonts, caption style, end card) and which one is active. Every template and video theme renders from the active kit.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "create_brand_kit", "description": "Create a brand kit from fields, from a website (url: logo, colours, fonts and voice are "
     "read from it, no AI), or from a sample brand (sample: nothing | linear | notion). activate: true makes it the active kit at once.",
     "inputSchema": {"type": "object", "properties": {**_KIT_PROPS, "url": {"type": "string"},
                                                      "sample": {"type": "string", "enum": list(brand_onboard.SAMPLE_IDS),
                                                                 "description": "start from a sample brand (not affiliated)"},
                                                      "activate": {"type": "boolean"}}}},
    {"name": "update_brand_kit", "description": "Change fields of a brand kit. When it is the active kit, every template follows at once.",
     "inputSchema": {"type": "object", "required": ["kit_id"], "properties": {"kit_id": {"type": "integer"}, **_KIT_PROPS}}},
    {"name": "set_active_brand_kit", "description": "Make a brand kit the active one: every template, video theme, carousel, thumbnail and "
     "end card renders from it from now on.", "inputSchema": {"type": "object", "required": ["kit_id"], "properties": {"kit_id": {"type": "integer"}}}},
    {"name": "preview_brand_kit", "description": "Render the whole gallery in a kit without saving: every video theme's title card, the "
     "carousel cover, the thumbnail and the end card. Give kit_id, or kit fields. Returns image URLs.",
     "inputSchema": {"type": "object", "properties": {"kit_id": {"type": "integer"}, "kit": {"type": "object", "properties": _KIT_PROPS}}}},
]
READ_ONLY = {"list_brand_kits"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    from .actions import ActionError
    bk = attach(studio.house)
    try:
        if name == "list_brand_kits":
            return bk.list(actor)
        if name == "create_brand_kit":
            return bk.create(actor, args)
        if name == "update_brand_kit":
            return bk.update(actor, int(args.get("kit_id") or 0), {k: v for k, v in args.items() if k != "kit_id"})
        if name == "set_active_brand_kit":
            return bk.activate(actor, int(args.get("kit_id") or 0))
        if name == "preview_brand_kit":
            return bk.preview(actor, args, as_urls=True)
    except (KitError, brand_onboard.OnboardError) as e:
        raise ActionError(str(e), 400)
    return NotImplemented
