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
import threading
import time
from collections import OrderedDict
from typing import Any

from . import brand, brand_examples, brand_onboard, demo_guard, design_system, templates

SCHEMA = "CREATE TABLE IF NOT EXISTS brand_kit_library (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, name TEXT, data TEXT, created REAL, updated REAL)"
MAX_KITS = 12
CAPTION_STYLES = {"opus": "Opus: big word-by-word captions", "news": "News: a sentence under the video",
                  "minimal": "Minimal: calm lower captions", "hype": "Hype: one huge word at a time"}
GALLERY_PER_MIN = 40  # the builder redraws as you type (debounced); kit cards and themes are separate calls
_gallery: dict[str, list[float]] = {}
HOOK = "Shipped this week"
KIT_CARDS = ("carousel_cover", "thumbnail", "end_card")
FONT_LABELS = {"anton": "Anton", "poppins": "Poppins", "inter": "Inter", "figtree": "Figtree", "serif": "DM Serif Display",
               "dot": "Doto (dot matrix)", "inter-semibold": "Inter SemiBold", "garamond": "EB Garamond",
               "sourceserif": "Source Serif 4", "roboto": "Roboto"}
BODY_CHOICES = ("inter", "inter-semibold", "poppins", "figtree", "roboto", "sourceserif")


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
    # brand kit v2: the generated theme (layout, surface, radius, scale) and the site's DESIGN.md travel with the kit
    if "theme" in body:
        th = design_system.clean_theme(body.get("theme"))
        if th:
            k["theme"] = th
        else:
            k.pop("theme", None)
    if body.get("layout") in design_system.LAYOUTS:  # the builder's Layout picker
        th = dict(k.get("theme") or design_system.theme_for_kit({**k, "font": k.get("font") or "inter"}))
        th["layout"] = body["layout"]
        th["accent_usage"] = {"bold_news": "fill", "playful": "fill", "minimal": "line", "editorial": "text"}[body["layout"]]
        k["theme"] = design_system.clean_theme(th) or th
    if isinstance(base, dict) and isinstance(base.get("design"), dict):
        k["design"] = base["design"]
    d = body.get("design")
    if isinstance(d, dict) and isinstance(d.get("md"), str) and len(d["md"]) < 20000:  # a website draft saved from the builder
        k["design"] = {"engine": str(d.get("engine") or "")[:20], "site": str(d.get("site") or "")[:120], "url": str(d.get("url") or "")[:300],
                       "md": d["md"], "ms": int(d.get("ms") or 0) if str(d.get("ms") or "0").isdigit() else 0,
                       "tokens": d.get("tokens") if isinstance(d.get("tokens"), dict) and len(json.dumps(d.get("tokens"))) < 20000 else {},
                       "extracted_at": int(time.time())}
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


CLASSIC_SAMPLES = ("nothing",)  # sample kits that keep their pre-v2 look (no generated theme)


def example_kit(eid: str) -> dict:
    """An example brand (HuhByte AI) as a stored kit, with its generated theme."""
    ex = brand_examples.EXAMPLES[eid]
    k = normalize({**ex["kit"], "logo": f"example:{eid}", "caption_style": "news" if eid == "huhbyte" else "minimal"})
    k["theme"] = design_system.theme_for_kit(k)
    return k


def not_this_samples_logo(ref, name) -> bool:
    """True for a sample brand's logo (sample:<id>) on a kit that is no longer called that sample's name."""
    ref = str(ref or "")
    if not ref.startswith("sample:") or not (name or "").strip():  # mid-edit (an empty name) keeps it
        return False
    s = brand_onboard.sample(ref[7:])
    return not s or (name or "").strip().casefold() != str(s["kit"].get("name") or "").strip().casefold()


def _drop_sample_badge(k: dict, sample_name: str | None) -> None:
    """A kit started from a sample brand keeps the "Sample brand, not affiliated" badge only while it is still that brand.
    Saved under another name it is the person's own kit, so the badge (and the sample's logo) no longer describe it:
    the badge goes, and source.derived_from records where it started."""
    if k.get("label") != brand_onboard.SAMPLE_LABEL or not sample_name:
        return
    if (k.get("name") or "").strip().casefold() == str(sample_name).strip().casefold():
        return
    k.pop("label", None)
    if not_this_samples_logo(k.get("logo"), k.get("name")):
        k["logo"] = ""  # the sample's logo isn't theirs: the builder asks for their own
    src = dict(k.get("source") or {})
    src["derived_from"] = sample_name
    k["source"] = src


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
    from .media import styles
    head = templates.HEAD_FONTS.get(kit.get("font") or "inter", "Inter-Bold.ttf")
    body = templates.BODY_FONTS.get(kit.get("body_font") or "inter", "Inter-Regular.ttf")
    bold = {"Inter-Regular.ttf": "Inter-Bold.ttf", "Poppins-Medium.ttf": "Poppins-Bold.ttf", "Figtree-Medium.ttf": "Figtree-ExtraBold.ttf",
            "Roboto-Regular.ttf": "Roboto-Bold.ttf", "SourceSerif4-Regular.ttf": "SourceSerif4-SemiBold.ttf"}.get(body, body)
    mixed = templates.mix(templates.hex_rgb(text), templates.hex_rgb(bg), 0.4)
    acc = c.get("accent") or brand.NEUTRAL_ACCENT
    return {"name": kit.get("name") or "", "handle": ("@" + kit["handle"]) if kit.get("handle") else "", "accent": acc,
            "bg": bg, "text": text, "muted": "#%02X%02X%02X" % mixed, "logo": logo_path, "head_font": head, "body_font": body,
            "bold_font": bold, "end_line": (kit.get("end_card") or {}).get("line") or "", "highlight": styles.highlight_for(c),
            "on_accent": "#111114" if templates._lum(templates.hex_rgb(acc)) > 0.35 else "#FFFFFF"}


def theme_cards_for(kit: dict, logo: bytes | None, W: int = 360, H: int = 640, hook: str = HOOK, ids=None) -> list[dict]:
    """Every look in the Video style gallery (the four edit styles and every theme), its title card drawn in this kit:
    the kit's logo, name, @handle, fonts and accent; a theme keeps its own ground and type (that is the theme).
    ids limits it to some looks (the builder asks for the cards scrolled into view)."""
    from PIL import Image
    from .media import compose, styles, theme_cards
    out = []
    want = [i for i in styles.ALL_IDS if ids is None or i in ids]
    if not want:
        return out
    with tempfile.TemporaryDirectory(prefix="hqkit-") as td:
        lp = None
        if logo:
            lp = os.path.join(td, "logo.png")
            with open(lp, "wb") as f:
                f.write(logo)
        b0 = compose_brand(kit, lp)
        for sid in want:
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


KIT_LABELS = {"carousel_cover": "Carousel cover 4:5", "thumbnail": "Thumbnail 16:9", "end_card": "End card"}
TPL_PREFIX = "tpl:"  # a template's first slide in a kit: the Templates tab's "Every template in <kit>" previews


def template_card(kit: dict, logo: bytes | None, tpl: str):
    """The first slide of a template, drawn in this kit (4:5, the sample data), or None for an unknown template."""
    if tpl not in templates.TEMPLATES:
        return None
    b = brand_onboard.brand_for(kit, logo)
    ims = templates.slides(tpl, None, b, "4:5")
    return ims[0] if ims else None


def kit_cards(kit: dict, logo: bytes | None, ids=None) -> list[dict]:
    b = brand_onboard.brand_for(kit, logo)
    st = brand_onboard.STORY
    draw = {"carousel_cover": lambda: templates.front_card(b, "4:5", st["kicker"], st["title"], st["sub"], n=(1, 5)),
            "thumbnail": lambda: templates.front_card(b, "16:9", "New", st["reel"], ""),
            "end_card": lambda: templates.end_card(b, "4:5")}
    out = []
    for cid in KIT_CARDS:
        if ids is not None and cid not in ids:
            continue
        try:
            out.append({"id": cid, "label": KIT_LABELS[cid], "image": draw[cid]()})
        except Exception as e:
            out.append({"id": cid, "label": KIT_LABELS[cid], "error": e.__class__.__name__})
    return out


# ================================================================================================== preview cache
# Every preview card is cached on its own, keyed by what it is drawn from (the kit's render fields, the logo's bytes, the
# card, the size, the format and PREVIEW_VERSION), in memory (LRU) and on disk, so a kit seen once (by anyone: the two
# sample kits are drawn at start-up) is instant after that, across workers and restarts on the same disk. The builder
# gets small WebP files by URL (/api/brand-kits/preview-img/<key>.webp) that the browser caches as immutable.
PREVIEW_VERSION = 4  # 4: layout variants, logo sizing by visual bounds, template preview cards
RENDER_FIELDS = ("name", "handle", "colors", "font", "body_font", "headline_case", "front_card", "end_card", "theme")
MEM_MAX = 400
DISK_MAX = 3000
_mem: "OrderedDict[str, bytes]" = OrderedDict()
_mem_lock = threading.Lock()
_render_lock = threading.Lock()  # one card at a time: a small instance gets no faster drawing two at once
_warm_lock = threading.Lock()  # the background warm-up's own lock (see render_cards)
_disk_dir: str | None = None
_warmed = False  # the sample kits are drawn once per process
# Requests come first: a request that has a card to draw counts itself here while it draws, and the background warm-up
# (low priority) waits between cards until no request has drawn for WARM_YIELD_S, so it never holds a request up.
_demand = 0
_demand_lock = threading.Lock()
_last_demand = 0.0
WARM_YIELD_S = 0.4


def _demand_add(n: int) -> None:
    global _demand, _last_demand
    with _demand_lock:
        _demand += n
        _last_demand = time.monotonic()


def requests_drawing() -> bool:
    """True while a request draws cards, or did in the last WARM_YIELD_S (the warm-up then waits)."""
    with _demand_lock:
        return _demand > 0 or time.monotonic() - _last_demand < WARM_YIELD_S


def _yield_to_requests(max_wait: float = 120.0) -> None:
    end = time.monotonic() + max_wait
    while requests_drawing() and time.monotonic() < end:
        time.sleep(0.05)


def _low_priority_thread() -> None:
    """Best effort: drop this thread's OS priority (Linux nice is per thread), so drawing that runs outside the GIL
    (Pillow) loses the CPU to request threads."""
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (AttributeError, OSError):
        pass


def cache_dir() -> str:
    global _disk_dir
    if _disk_dir is None:
        d = os.environ.get("HUHQ_PREVIEW_CACHE") or os.path.join(tempfile.gettempdir(), "huhq-kit-previews")
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            d = ""
        _disk_dir = d
    return _disk_dir


def card_key(kit: dict, logo: bytes | None, card: str, w: int, fmt: str) -> str:
    src = {f: kit.get(f) for f in RENDER_FIELDS}
    raw = json.dumps([PREVIEW_VERSION, src, hashlib.sha256(logo or b"").hexdigest(), card, w, fmt], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def cache_get(key: str) -> bytes | None:
    with _mem_lock:
        v = _mem.get(key)
        if v is not None:
            _mem.move_to_end(key)
            return v
    d = cache_dir()
    if d:
        try:
            with open(os.path.join(d, key), "rb") as f:
                v = f.read()
        except OSError:
            return None
        _mem_put(key, v)
        return v
    return None


def _mem_put(key: str, v: bytes) -> None:
    with _mem_lock:
        _mem[key] = v
        _mem.move_to_end(key)
        while len(_mem) > MEM_MAX:
            _mem.popitem(last=False)


def cache_put(key: str, v: bytes) -> None:
    _mem_put(key, v)
    d = cache_dir()
    if not d:
        return
    try:
        tmp = os.path.join(d, f".{key}.{os.getpid()}.{threading.get_ident()}")
        with open(tmp, "wb") as f:
            f.write(v)
        os.replace(tmp, os.path.join(d, key))
        if int(key[:4], 16) % 50 == 0:  # now and then, keep the disk cache bounded (oldest first)
            names = [n for n in os.listdir(d) if not n.startswith(".")]
            if len(names) > DISK_MAX:
                names.sort(key=lambda n: os.path.getmtime(os.path.join(d, n)))
                for n in names[: len(names) - DISK_MAX]:
                    os.remove(os.path.join(d, n))
    except OSError:
        pass


def cache_clear() -> None:
    with _mem_lock:
        _mem.clear()
    d = cache_dir()
    if d:
        for n in os.listdir(d):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass


def encode(im, w: int | None, fmt: str) -> bytes:
    t = im
    if w:
        t = im.copy()
        t.thumbnail((w, w * 2))
    if fmt == "webp":
        buf = io.BytesIO()
        t.save(buf, "WEBP", quality=78, method=4)
        return buf.getvalue()
    return templates.jpeg(t, 84 if not w else 80)


def theme_size(w: int) -> tuple[int, int]:
    """Theme title cards are drawn at the width they are shown at (9:16), never under 240 px."""
    W = max(240, min(int(w), 400))
    return W, round(W * 16 / 9)


def render_cards(kit: dict, logo: bytes | None, ids: list[str], w: int, fmt: str, stats: dict | None = None,
                 background: bool = False) -> dict[str, dict]:
    """The asked-for cards as encoded bytes, from the cache or drawn now (each drawn card is cached at once). A request
    (background False) counts as demand while it draws; the warm-up (background True) waits for requests before each
    card it has to draw, so the cards a request asks for are drawn first."""
    from .media import styles
    out: dict[str, dict] = {}
    labels = {**KIT_LABELS, **{i: (styles.THEMES.get(i) or styles.STYLE_PRESETS.get(i) or {}).get("label", i) for i in styles.ALL_IDS},
              **{TPL_PREFIX + t: v.get("name", t) for t, v in templates.TEMPLATES.items()}}
    keys = {cid: card_key(kit, logo, cid, w, fmt) for cid in ids}
    demand = not background and any(cache_get(k) is None for k in keys.values())
    if demand:
        _demand_add(1)
    try:
        return _render_cards(kit, logo, ids, w, fmt, stats, background, out, labels, keys)
    finally:
        if demand:
            _demand_add(-1)


def _render_cards(kit, logo, ids, w, fmt, stats, background, out, labels, keys) -> dict[str, dict]:
    for cid in ids:
        key = keys[cid]
        v = cache_get(key)
        if v is None:
            if background:
                _yield_to_requests()
            # requests draw one at a time under _render_lock; the warm-up has its own lock, so a request never queues
            # behind a warm-up card (at worst both draw the same card once and the cache keeps one)
            with (_warm_lock if background else _render_lock):
                v = cache_get(key)  # drawn by another request (or the warm-up) while this one waited
                if v is None:
                    t0 = time.perf_counter()
                    if cid.startswith(TPL_PREFIX):
                        try:
                            im = template_card(kit, logo, cid[len(TPL_PREFIX):])
                        except Exception as e:  # noqa: BLE001  (one template that can't draw never blanks the others)
                            print(f"template preview {cid}: {e.__class__.__name__}", flush=True)
                            im = None
                        got = [{"image": im}] if im is not None else [{"error": "This template can't be drawn in this kit."}]
                    elif cid in KIT_CARDS:
                        got = kit_cards(kit, logo, ids=[cid])
                    else:
                        W, H = theme_size(w or 360)
                        got = theme_cards_for(kit, logo, W=W, H=H, ids=[cid])
                    c = got[0] if got else {"error": "Missing"}
                    if "image" not in c:
                        out[cid] = {"label": labels.get(cid, cid), "error": c.get("error")}
                        continue
                    v = encode(c["image"], w, fmt)
                    cache_put(key, v)
                    if stats is not None:
                        stats.setdefault("drawn", {})[cid] = int((time.perf_counter() - t0) * 1000)
        out[cid] = {"label": labels.get(cid, cid), "key": key, "bytes": v}
    return out


def _jpeg_url(im, w: int) -> str:
    t = im.copy()
    t.thumbnail((w, w * 2))
    return "data:image/jpeg;base64," + base64.b64encode(templates.jpeg(t, 80)).decode()


def _theme_list() -> list[dict]:
    from .media import styles
    return [{"id": i, "label": (styles.THEMES.get(i) or styles.STYLE_PRESETS.get(i) or {}).get("label", i)} for i in styles.ALL_IDS]


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
        from .userlocks import held
        with held(a.uid):  # B3: two first loads at once seed once
            if self._rows(a):
                return
            self._seed_demo_locked(a)

    def _seed_demo_locked(self, a) -> None:
        s = brand_onboard.sample("nothing")
        nid = None
        if s:
            k = normalize({**s["kit"], "logo": "sample:nothing"})  # Nothing keeps its classic grid look (owner, 2 Nov 2026)
            k["label"] = brand_onboard.SAMPLE_LABEL
            k["source"] = {"url": s.get("source_url"), "provenance": s.get("provenance"), "fonts": s.get("fonts"),
                           "extracted_at": s.get("extracted_at")}
            nid = self._insert(a, k)
        hb = brand_examples.EXAMPLES.get("huhbyte")
        if hb:
            self._insert(a, example_kit("huhbyte"))
        if nid:
            self.activate(a, nid)

    def _seed_huhbyte(self, a) -> None:
        """Every signed-in workspace gets HuhByte AI as a ready kit next to the samples (owner, 2 Nov 2026), once. It is
        never activated here, so a person's own active kit stays active; deleting it is respected."""
        if self.bs.extras(a).get("seeded_huhbyte"):
            return
        from .userlocks import held
        with held(a.uid):  # B3: check-then-insert under the workspace lock, so parallel first loads seed one HuhByte kit
            if self.bs.extras(a).get("seeded_huhbyte"):
                return
            rows = self._rows(a)
            if not any((r["name"] or "").strip().casefold() == "huhbyte ai" for r in rows) and len(rows) < MAX_KITS:
                self._insert(a, example_kit("huhbyte"))
            self.bs.update_extras(a, {"seeded_huhbyte": True})

    def _insert(self, a, k: dict) -> int:
        from .userlocks import held
        with held(a.uid):  # B3: the cap is checked and the row inserted under one lock (no 13th kit from a race)
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
        d = k.get("design") if isinstance(k.get("design"), dict) else {}
        view = builder_view({kk: v for kk, v in k.items() if kk != "design"})
        if d:  # "Style taken from <site>" + the DESIGN.md (tokens stay server-side; GET .../design.md has them all)
            view["design"] = {"site": d.get("site"), "engine": d.get("engine"), "ms": d.get("ms"), "url": f"/api/brand-kits/{row['id']}/design.md"}
        return {"id": row["id"], "name": row["name"], "active": row["id"] == active, "kit": view,
                "logo_url": self._logo_url(a, k.get("logo")), "updated": row["updated"]}

    def _backfill_themes(self, a, rows: list[dict]) -> None:
        """Brand kit v2: kits saved before generated themes get one from their own fonts and colours, once."""
        for r in rows:
            k = json.loads(r["data"] or "{}")
            if isinstance(k.get("theme"), dict) and k["theme"].get("layout"):
                continue
            if k.get("theme_classic") or str(k.get("logo") or "") in {f"sample:{c}" for c in CLASSIC_SAMPLES}:
                continue  # the Nothing sample keeps the look the owner likes
            k["theme"] = design_system.theme_for_kit(k)
            r["data"] = json.dumps(k)
            self.store.x("UPDATE brand_kit_library SET data=? WHERE id=? AND user_id=?", (r["data"], r["id"], a.uid))
            if r["id"] == self._active(a):  # the active kit's renders follow at once
                self.bs.update_extras(a, {"theme": k["theme"]})

    def design_md(self, a, kid: int) -> str:
        k = json.loads(self._get(a, kid)["data"] or "{}")
        d = k.get("design") if isinstance(k.get("design"), dict) else {}
        if d.get("md"):
            return d["md"] + ("\n## Raw tokens\n\n```json\n" + json.dumps(d.get("tokens") or {}, indent=2) + "\n```\n")
        th = k.get("theme") or design_system.theme_for_kit(k)
        return (f"# DESIGN.md: {k.get('name') or 'Untitled kit'}\n\nThis kit was not read from a website, so its theme comes from its "
                f"own fonts and colours.\n\n- Layout: **{th.get('layout')}**: {design_system.LAYOUTS.get(th.get('layout'), '')}\n"
                f"- Ground: {th.get('mode')} {(k.get('colors') or {}).get('primary', '')}, surface {th.get('surface')}, {th.get('radius')}px corners\n"
                f"- Type: {k.get('font')} headlines ({th.get('case')}), {k.get('body_font')} body\n")

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
        else:
            self._seed_huhbyte(a)
        act = self._active(a)
        rows = self._rows(a)
        self._backfill_themes(a, rows)
        return {"kits": [self._out(a, r, act) for r in rows], "active_id": act, "caption_styles": CAPTION_STYLES,
                "layouts": design_system.LAYOUTS,
                "examples": [{"id": e["id"], "name": e["kit"]["name"], "logo_url": f"/api/brand-kits/example-logo/{e['id']}"}
                             for e in brand_examples.EXAMPLES.values()],
                "preview": self._cached_kit_cards(a, next((r for r in rows if r["id"] == act), None)),
                "fonts": {k: FONT_LABELS.get(k, k) for k in templates.HEAD_FONTS},
                "body_fonts": {k: FONT_LABELS.get(k, k) for k in BODY_CHOICES}, "styles": templates.STYLES,
                "samples": [{"id": s["id"], "name": s["kit"].get("name"), "label": brand_onboard.SAMPLE_LABEL,
                             "logo_url": f"/static/brands/{s['id']}/logo.png"} for s in brand_onboard.samples()],
                "max": MAX_KITS, "themes": _theme_list(), "kit_cards": [{"id": i, "label": KIT_LABELS[i]} for i in KIT_CARDS]}

    def _cached_kit_cards(self, a, row: dict | None) -> dict | None:
        """The active kit's own cards when they are already in the preview cache (the warmed sample kits, or a kit seen
        before), so the builder can paint them straight from the list, one round trip before the preview call."""
        if not row:
            return None
        try:
            k = json.loads(row["data"] or "{}")
            logo = self.logo_bytes(a, str(k.get("logo") or ""))
            cards = []
            for cid in KIT_CARDS:
                key = card_key(k, logo, cid, 420, "webp")
                if cache_get(key) is None:
                    return None
                cards.append({"id": cid, "label": KIT_LABELS[cid], "group": "kit", "url": f"/api/brand-kits/preview-img/{key}.webp"})
            return {"kit_id": row["id"], "width": 420, "cards": cards}
        except Exception:
            return None

    def create(self, a, body: dict) -> dict:
        self.studio._guard_write(a, "create_brand_kit")
        base, src = None, None
        if body.get("example"):  # HuhByte AI (the owner's brand) as a kit of its own
            if body["example"] not in brand_examples.EXAMPLES:
                raise KitError(f"No such example brand. Pick one of {', '.join(brand_examples.EXAMPLES)}.")
            base = example_kit(body["example"])
        elif body.get("sample"):
            s = brand_onboard.sample(str(body["sample"]))
            if not s:
                raise KitError(f"No sample brand {body['sample']!r}. Pick one of {', '.join(brand_onboard.SAMPLE_IDS)}.")
            base = {**s["kit"], "logo": f"sample:{s['id']}", "label": brand_onboard.SAMPLE_LABEL}
            if s["id"] not in CLASSIC_SAMPLES:
                base["theme"] = design_system.theme_for_kit(base)
            src = {"url": s.get("source_url"), "provenance": s.get("provenance"), "fonts": s.get("fonts")}
        elif body.get("url"):
            ex = self.ob.extract(a, str(body["url"]))
            base = {**ex["draft"], "logo": ex.get("logo") or ""}
            src = {"url": ex["draft"].get("website"), "provenance": ex.get("provenance"), "fonts": ex.get("fonts")}
            if not base.get("theme"):
                base["theme"] = design_system.theme_for_kit(base)
        k = normalize(body, base)
        if src:
            k["source"] = src
        _drop_sample_badge(k, (base or {}).get("name") if body.get("sample") else None)
        self._check_logo(a, k)
        kid = self._insert(a, k)
        self.store.audit(a.uid, a.label, "create_brand_kit", "read", {"kit": kid, "from_url": bool(body.get("url"))})
        if body.get("activate"):
            return self.activate(a, kid)
        return self._out(a, self._get(a, kid), self._active(a))

    def update(self, a, kid: int | None, body: dict) -> dict:
        """kit_id missing = the active kit (B1: one update_brand_kit tool; the client brain's copy patched the house kit)."""
        self.studio._guard_write(a, "update_brand_kit")
        from .userlocks import held
        with held(a.uid):
            return self._update_locked(a, kid, body)

    def _update_locked(self, a, kid: int | None, body: dict) -> dict:
        if not kid:
            kid = self._active(a)
            if not kid:  # no kit library yet (a workspace that never opened the builder): the house kit is the brand
                patch = {k: body[k] for k in ("name", "handle", "website", "font", "voice", "colors", "end_card") if k in body}
                if body.get("head_font") and "font" not in patch:
                    patch["font"] = body["head_font"]
                if not patch:
                    raise KitError("Pass the fields to change (name, handle, website, font, voice, colors, end_card).")
                return {"kit": self.house.save_kit(a, patch), "active": True, "id": None}
        row = self._get(a, kid)
        k = normalize(body, json.loads(row["data"] or "{}"))
        _drop_sample_badge(k, row["name"])
        self._check_logo(a, k)
        self.store.x("UPDATE brand_kit_library SET name=?, data=?, updated=? WHERE id=?", (k.get("name") or row["name"], json.dumps(k), time.time(), row["id"]))
        self.store.audit(a.uid, a.label, "update_brand_kit", "read", {"kit": row["id"], "fields": sorted(body)[:20]})
        if row["id"] == self._active(a) or body.get("activate"):
            out = self.activate(a, row["id"])
            if isinstance(body.get("end_card"), dict) and "on" in body["end_card"]:  # the render switch lives on the house kit
                self.house.save_kit(a, {"end_card": {"on": bool(body["end_card"]["on"])}})
            return out
        return self._out(a, self._get(a, kid), self._active(a))

    def _check_logo(self, a, k: dict) -> None:
        ref = str(k.get("logo") or "")
        if ref and not ref.startswith(("sample:", "example:")) and not self.house.media_meta(a, ref):
            raise KitError("Unknown logo. Upload the logo first.")

    def activate(self, a, kid: int) -> dict:
        self.studio._guard_write(a, "set_active_brand_kit")
        from .userlocks import held
        with held(a.uid):  # B2: one lock, one extras write (the kit fields, active_kit, example and caption style together)
            return self._activate_locked(a, kid)

    def kit_view(self, a, kid: int) -> tuple[dict, str]:
        """One kit in house.kit's shape (logo as a media token) and its caption style, for ONE agent run. It never becomes
        the account's active kit (owner, 3 Nov 2026: "use the Nothing kit" in a thread switched every later Reel)."""
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
        out = {**brand.DEFAULT_KIT, "name": k.get("name", ""), "handle": k.get("handle", ""), "website": k.get("website", ""),
               "voice": k.get("voice", ""), "font": font, "body_font": k.get("body_font") or ("inter" if font in ("anton", "serif", "dot") else font),
               "headline_case": k.get("headline_case") or ("upper" if font in ("anton", "dot") else "title"),
               "front_card": {"style": (k.get("front_card") or {}).get("style") or "glow"},
               "hashtags": k.get("hashtags") or [], "label": k.get("label") or "", "theme": k.get("theme") or None, "logo": logo_tok}
        out["colors"] = {**brand.DEFAULT_KIT["colors"], **(k.get("colors") or {})}
        out["end_card"] = {**brand.DEFAULT_KIT["end_card"], "style": ec.get("style") or "glow", "cta": ec.get("cta") or "Follow for more",
                           "line": ec.get("line") or ""}
        out["logo_url"] = self.house.media_url(logo_tok) if logo_tok else ""
        out["is_set"], out["kit_id"] = True, row["id"]
        return out, k.get("caption_style") or "opus"

    def _activate_locked(self, a, kid: int) -> dict:
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
                 "hashtags": k.get("hashtags") or [], "label": k.get("label") or "", "theme": k.get("theme") or None}
        if logo_tok:
            patch["logo"] = logo_tok
        else:  # a kit without a logo clears the old one, so no other brand's mark stays on the renders
            self.house.save_kit(a, {"logo": ""})
        self.bs.save(a, patch, extra={"active_kit": row["id"], "caption_style": k.get("caption_style") or "opus",
                                      "example": next((e for e, v in brand_examples.EXAMPLES.items() if v["kit"]["name"] == k.get("name")), "")})
        self.store.audit(a.uid, a.label, "set_active_brand_kit", "read", {"kit": row["id"]})
        return {**self._out(a, row, row["id"]), "applied": True}

    def delete(self, a, kid: int) -> dict:
        self.studio._guard_write(a, "delete_brand_kit")
        from .userlocks import held
        with held(a.uid):  # never delete a kit another session is activating right now
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
        """Gallery cards in the kit as it is being edited: the carousel cover, the thumbnail and the end card (part "kit"),
        the video themes (part "themes", or just the ids asked for) or all of them. Each card is cached on its own (see
        card_key), so switching back to a kit, or opening the builder on a sample kit, is instant. The builder gets
        WebP URLs; inline: true gives data URLs; MCP (as_urls) gets stored media URLs."""
        from .media import styles
        t0 = time.time()
        if demo_guard.is_demo(a.uid) and not demo_guard.allowed(self.store, a.uid, "run"):
            raise KitError(demo_guard.WHY["run"])
        if body.get("kit_id") and not body.get("kit"):
            k = json.loads(self._get(a, int(body["kit_id"]))["data"] or "{}")
        else:
            k = normalize(body.get("kit") if isinstance(body.get("kit"), dict) else {})
        ref = str(body.get("logo") if body.get("logo") is not None else k.get("logo") or "")
        if not_this_samples_logo(ref, k.get("name")):  # a renamed sample previews without the sample's logo
            ref = ""
        logo = self.logo_bytes(a, ref)
        part = body.get("part") if body.get("part") in ("kit", "themes", "templates") else "all"
        w = max(120, min(int(body.get("width") or (420 if part == "kit" else 240)), 420))
        if part == "templates":  # each template's first slide in this kit (the Templates tab asks one template per call)
            ids = [TPL_PREFIX + t for t in templates.TEMPLATES]
        else:
            ids = (list(KIT_CARDS) if part != "themes" else []) + (list(styles.ALL_IDS) if part != "kit" else [])
        if isinstance(body.get("ids"), list) and part in ("themes", "templates"):
            asked = {str(i) if part == "themes" or str(i).startswith(TPL_PREFIX) else TPL_PREFIX + str(i) for i in body["ids"]}
            ids = [i for i in ids if i in asked]
        fmt, size = ("jpg", 0) if as_urls else (("jpg" if body.get("format") == "jpeg" else "webp"), w)
        misses = [i for i in ids if cache_get(card_key(k, logo, i, size, fmt)) is None]
        if misses:
            from .userlocks import rate_hit
            # B4: one lock for the shared window; the Templates tab asks one template per call, so it has its own, wider one
            tpl = part == "templates"
            if not rate_hit(_gallery, a.uid + (":tpl" if tpl else ""), time.time(), GALLERY_PER_MIN * (3 if tpl else 1)):
                raise KitError("Too many gallery redraws this minute. Pause for a moment.")
        stats: dict = {}
        got = render_cards(k, logo, ids, size, fmt, stats)
        out = []
        for cid in ids:
            c = got[cid]
            item = {"id": cid[len(TPL_PREFIX):] if cid.startswith(TPL_PREFIX) else cid, "label": c["label"],
                    "group": "kit" if cid in KIT_CARDS else "template" if cid.startswith(TPL_PREFIX) else "theme"}
            if "bytes" not in c:
                item["error"] = c.get("error")
            elif as_urls:
                tok = self.house.put_media(a, c["bytes"], f"kit-{cid}.jpg", kind="preview",
                                           meta={"type": "photo", "template": "brand_kit_preview"})
                item["url"] = self.house.media_url(tok)
            elif body.get("inline"):
                item["url"] = f"data:image/{'webp' if fmt == 'webp' else 'jpeg'};base64," + base64.b64encode(c["bytes"]).decode()
            else:
                item["url"] = f"/api/brand-kits/preview-img/{c['key']}.{fmt}"
                item["bytes"] = len(c["bytes"])
            out.append(item)
        return {"cards": out, "kit": builder_view(k), "part": part, "cached": not misses,
                "drawn_ms": stats.get("drawn", {}), "ms": int((time.time() - t0) * 1000)}

    def warm(self) -> dict:
        """Draw the two sample-workspace kits (Nothing, HuhByte AI) into the preview cache at start-up, every kit's own
        cards first, so the builder's first preview on a fresh instance comes from the cache. Runs at low priority: it
        waits before each card while a request is drawing (render_cards background=True)."""
        from .media import styles
        t0, n = time.time(), 0
        kits = []
        s = brand_onboard.sample("nothing")
        if s:
            nk = normalize({**s["kit"], "logo": "sample:nothing"})
            kits.append((nk, brand_onboard.sample_logo("nothing")))
        hb = brand_examples.EXAMPLES.get("huhbyte")
        if hb:
            try:
                lb = brand_examples.logo_bytes("huhbyte")
            except Exception:
                lb = None
            kits.append((example_kit("huhbyte"), lb))
        for k, lb in kits:
            n += len(render_cards(k, lb, list(KIT_CARDS), 420, "webp", background=True))
        for k, lb in kits:
            n += len(render_cards(k, lb, list(styles.ALL_IDS), 240, "webp", background=True))
        for k, lb in kits:  # the Templates tab's previews for the sample kits
            n += len(render_cards(k, lb, [TPL_PREFIX + t for t in templates.TEMPLATES], 240, "webp", background=True))
        return {"cards": n, "ms": int((time.time() - t0) * 1000)}


def caption_style_for(house, a) -> str:
    """The active kit's caption style (opus | news | minimal | hype): the default look of a captioned video edit when the
    person or agent names no theme. 'opus' when no kit was ever activated."""
    try:
        v = attach(house).bs.extras(a).get("caption_style")
    except Exception:
        v = None
    return v if v in CAPTION_STYLES else "opus"


def brand_accent_wins(house, a) -> bool:
    """The one accent rule, shared by the Video style gallery and a default render: with an active brand kit the kit's
    accent stays inside every theme (the theme keeps its ground and type); with none, a theme brings its own accent."""
    try:
        return bool(attach(house).bs.extras(a).get("active_kit"))
    except Exception:
        return False


def active_caption_style(house, a) -> str | None:
    """The caption style the person chose in their active brand kit, or None when no kit was ever activated (callers then
    keep their own default instead of switching every plain render to a styled edit)."""
    try:
        bk = attach(house)
        ex = bk.bs.extras(a)
    except Exception:
        return None
    v = ex.get("caption_style")
    return v if ex.get("active_kit") and v in CAPTION_STYLES else None


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
    global _warmed
    if os.environ.get("HUHQ_PREVIEW_WARM", "1") != "0" and not _warmed:
        _warmed = True

        def _warm():
            _low_priority_thread()
            time.sleep(float(os.environ.get("HUHQ_PREVIEW_WARM_DELAY", "2")))  # let start-up and the first requests go first
            try:
                print("brand kit previews warmed:", bk.warm(), flush=True)
            except Exception as e:  # warming is best effort; the builder draws on demand
                print("brand kit preview warm-up skipped:", e.__class__.__name__, flush=True)
        threading.Thread(target=_warm, name="brand-kit-warm", daemon=True).start()

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

    @app.get("/api/brand-kits/preview-img/{name}", include_in_schema=False)
    def kits_preview_img(name: str, request: Request):
        actor(request)  # signed-in (or sample) workspaces only
        key, _, ext = name.partition(".")
        if ext not in ("webp", "jpg") or not key.isalnum() or len(key) != 40:
            return Response(status_code=404)
        v = cache_get(key)
        if v is None:
            return Response(status_code=404)
        return Response(v, media_type="image/webp" if ext == "webp" else "image/jpeg",
                        headers={"Cache-Control": "private, max-age=31536000, immutable"})

    @app.get("/api/brand-kits/{kid}/design.md", include_in_schema=False)
    def kits_design_md(kid: int, request: Request):
        try:
            md = bk.design_md(actor(request), kid)
        except KitError:
            return Response(status_code=404)
        return Response(md, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "private, no-store"})

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
    {"name": "update_brand_kit", "description": "Change fields of a brand kit (no HuhQueue AI). Leave out kit_id to change the ACTIVE kit "
     "(what every render uses); give kit_id for another kit in list_brand_kits. When it is the active kit, every template, theme, "
     "carousel and end card follows at once. colors take background / primary / accent / text, or the renderer names "
     "primary (ground) / accent / text; font is the headline font; end_card {line, cta, on}.",
     "inputSchema": {"type": "object", "properties": {"kit_id": {"type": "integer", "description": "omit = the active kit"}, **_KIT_PROPS,
                     "font": {"type": "string", "enum": list(templates.HEAD_FONTS), "description": "same as head_font"},
                     "end_card": {"type": "object", "properties": {"on": {"type": "boolean"}, "line": {"type": "string"}, "cta": {"type": "string"}}}}}},
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
            return bk.update(actor, int(args.get("kit_id") or 0) or None, {k: v for k, v in args.items() if k != "kit_id"})
        if name == "set_active_brand_kit":
            return bk.activate(actor, int(args.get("kit_id") or 0))
        if name == "preview_brand_kit":
            return bk.preview(actor, args, as_urls=True)
    except (KitError, brand_onboard.OnboardError) as e:
        raise ActionError(str(e), 400)
    return NotImplemented
