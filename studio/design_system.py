"""Brand kit v2: a website's design system -> tokens -> a DESIGN.md -> a GENERATED theme per kit (owner, 2 Nov 2026).

Before v2 a kit from a URL got the right logo and colours but every light site was drawn with the same "minimal" card in
Inter, so ycombinator.com, wisprflow.ai, youtube.com and notion.com all looked alike. v2 reads the site's design system and
turns it into a theme the renderers follow: light or dark ground, the surface (card) colour, headline and body fonts (the
nearest bundled Google Font when the site's face is proprietary), corner radius, the headline scale and case, how the
accent is used, and one of four base layouts picked from the site's style:

  bold_news   condensed / display / dot-matrix headlines, upper case, accent as a solid band (HuhByte AI, Nothing)
  editorial   serif headlines, thin rules, a small-caps kicker, generous margins (Wispr Flow, YC's serif pages)
  minimal     a sans headline, plain ground, the accent used sparingly (Linear dark, Notion light, YC)
  playful     rounded surface cards, pill buttons and a saturated accent (YouTube)

Engines (the first that answers wins; every kit records which one in design.engine):
  1. context.dev Styleguide API (GET https://api.context.dev/v1/web/styleguide?domain=...) when CONTEXT_DEV_API_KEY is
     set: the site is rendered in a real browser and the computed styles come back (mode, colours, h1..p typography, button
     and card CSS, spacing, font links). 10 credits a call; the key never leaves the server and is never logged.
  2. HuhQueue's own CSS reader (no key, no browser): the stylesheets the page links, CSS custom properties resolved, the
     font-family on h1/h2/heading/hero selectors, Google Fonts links and preloaded font files in the HTML, the border
     radius on button/btn/cta selectors, the h1 font size and text-transform. Built on studio.brand_extract.

The open-source extractors (dembrandt, MIT; design-md-extractor) were evaluated as option (b): they need Playwright, so
they would run as an InstaBox worker job; not wired in v2 (see docs/brand-kit-v2.md). No code is copied from them.

Pure functions (``tokens_from_css``, ``tokens_from_styleguide``, ``theme_from_tokens``, ``theme_for_kit``,
``design_md``) so tests run on fixtures; ``context_dev_styleguide`` is the only network call here.
"""
from __future__ import annotations

import os
import re
import statistics
import time
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from . import brand_extract as bx

LAYOUTS = {
    "bold_news": "Bold news: condensed upper-case headlines, the accent as a solid band",
    "editorial": "Editorial: serif headlines, thin rules, a small-caps kicker",
    "minimal": "Minimal: a plain ground, a sans headline, the accent used sparingly",
    "playful": "Playful: rounded cards, pill buttons and a saturated accent",
}
SERIF_IDS = ("serif", "garamond", "sourceserif")
DISPLAY_IDS = ("anton", "dot")
CONTEXT_DEV_URL = "https://api.context.dev/v1/web/styleguide"

_RULE = re.compile(r"([^{}@;]+)\{([^{}]*)\}")
_HEAD_SEL = re.compile(r"(?:^|[\s,>+~.#-])h[12]\b|heading|headline|display|hero|title", re.I)
_H1_SEL = re.compile(r"(?:^|[\s,>+~])h1\b", re.I)
_BTN_SEL = re.compile(r"button|\bbtn|\.cta|\[type=.?submit", re.I)
_CARD_SEL = re.compile(r"card|tile|panel", re.I)


# ================================================================================================== small helpers
def _px(v: str, vars_: dict | None = None) -> float | None:
    """A CSS length -> px (rem/em at 16 px; clamp()/min()/max() -> the largest value; 50% / 9999px -> 999 = a pill)."""
    v = bx.resolve(str(v or ""), vars_ or {}).strip().lower().replace("!important", "").strip()
    if not v:
        return None
    if v.endswith("%"):
        try:
            return 999.0 if float(v[:-1]) >= 50 else None
        except ValueError:
            return None
    nums = re.findall(r"(-?[\d.]+)(px|rem|em)?", v)
    vals = []
    for n, unit in nums:
        try:
            x = float(n)
        except ValueError:
            continue
        vals.append(x * 16 if unit in ("rem", "em") else x)
    if not vals:
        return None
    x = max(vals) if v.startswith(("clamp", "max", "min")) else vals[0]
    return 999.0 if x >= 500 else x


def _hexs(v) -> str | None:
    p = bx.parse_css_color(str(v or ""))
    return bx.to_hex(p[0]) if p and p[1] > 0.5 else None


def _sat(h: str | None) -> float:
    return bx.sat(tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))) if h else 0.0


def _chroma(h: str | None) -> float:
    """How coloured a colour is, 0..1, pale tints included (bx.sat calls every very light colour grey)."""
    if not h:
        return 0.0
    c = _rgb(h)
    return (max(c) - min(c)) / 255


def _rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def radius_kind(px: float | None) -> str:
    if px is None:
        return "soft"
    return "sharp" if px <= 3 else "soft" if px < 14 else "round" if px < 100 else "pill"


def _family(decl: str, vars_: dict) -> str:
    return bx._first_family(decl, vars_)


# ================================================================================================== engine 2: CSS
def tokens_from_css(url: str, html: str, css_texts: list[str], got: dict) -> dict:
    """Design tokens from the page's own CSS (engine 2). ``got`` is brand_extract.extract()'s result for the same page
    (colours, fonts, logo), so this adds what a theme needs on top: heading family, radius, h1 size, case, surface."""
    css = "\n".join(bx.relevant_css(css_texts or [])) + "\n" + "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", html or "", re.S | re.I))
    css = bx._COMMENT.sub("", css[: bx.MAX_CSS_BYTES * 3])
    vars_ = bx.css_vars(css)
    heads: Counter = Counter()
    radii: list[float] = []
    cards: list[float] = []
    h1: list[float] = []
    upper = 0
    for sel, body in _RULE.findall(css):
        sel = sel.strip()
        if len(sel) > 300:
            continue
        if _HEAD_SEL.search(sel):
            m = re.search(r"font-family\s*:\s*([^;}]+)", body)
            if m:
                fam = _family(m.group(1), vars_)
                if fam:
                    heads[fam] += 3 if _H1_SEL.search(sel) else 1
            if re.search(r"text-transform\s*:\s*uppercase", body) and _H1_SEL.search(sel):
                upper += 1
            m = re.search(r"font-size\s*:\s*([^;}]+)", body)
            if m and _H1_SEL.search(sel):
                v = _px(m.group(1), vars_)
                if v and 16 <= v <= 200:
                    h1.append(v)
        m = re.search(r"border-radius\s*:\s*([^;}]+)", body)
        if m:
            v = _px(m.group(1).split()[0] if not m.group(1).strip().startswith(("var", "clamp", "calc", "min", "max")) else m.group(1), vars_)
            if v is not None and _BTN_SEL.search(sel):
                radii.append(v)
            elif v is not None and _CARD_SEL.search(sel):
                cards.append(v)
    # hints straight from the HTML: Google Fonts families and preloaded font files
    google = [re.sub(r"\+", " ", f) for f in re.findall(r"fonts\.googleapis\.com/css2?\?family=([A-Za-z0-9+]+)", html or "")]
    hints = list(google)
    hints += [re.sub(r"[-_](regular|bold|medium|semibold|italic|light|black|book|variable|vf).*$", "", f, flags=re.I)
              for f in re.findall(r"/([A-Za-z][A-Za-z0-9_-]{2,40})\.woff2", html or "")[:12]]
    fonts = got.get("fonts") or {}
    head_family = (fonts.get("head") or {}).get("family") or ""
    body_family = (fonts.get("body") or {}).get("family") or ""
    if heads:
        top = heads.most_common(1)[0][0]
        serif = next((f for f, _ in heads.most_common(6) if bx.map_font(f)[0] in SERIF_IDS), None)
        head_family = serif if serif and heads[serif] >= 2 else top
    for hnt in hints:  # a serif the page preloads (Garamond-Regular.woff2) is the headline face more often than not
        if bx.map_font(hnt)[0] in SERIF_IDS and bx.map_font(head_family or "x")[0] not in SERIF_IDS and \
                any(hnt.lower().split()[0] in f.lower() for f in heads):
            head_family = hnt
    for fam_key in ("head", "body"):  # a proprietary face we can only guess at: the Google Font the page loads is the truth
        fam = head_family if fam_key == "head" else body_family
        if google and (not fam or (bx.map_font(fam) == ("inter", "closest"))):
            if fam_key == "head":
                head_family = google[0]
            else:
                body_family = google[0]
    k = got.get("kit") or {}
    c = k.get("colors") or {}
    bg, text, accent = c.get("primary") or "#FFFFFF", c.get("text") or "#111114", c.get("accent") or ""
    surface = None
    for name in ("--surface", "--card", "--bg-secondary", "--background-secondary", "--color-bg-secondary", "--bg-elevated"):
        for vn, vals in vars_.items():
            if vn.lower().startswith(name) or name.strip("-") in vn.lower():
                surface = _hexs(bx.resolve(vals[-1], vars_))
                if surface:
                    break
        if surface:
            break
    return {
        "engine": "huhqueue-css",
        "mode": "dark" if bx.lum(_rgb(bg)) < 0.4 else "light",
        "colors": {"background": bg, "surface": surface or "", "text": text, "accent": accent, "accent2": c.get("secondary") or ""},
        "fonts": {"head": {"family": head_family}, "body": {"family": body_family or head_family}},
        "type": {"h1_px": round(statistics.median(h1)) if h1 else None, "body_px": 16, "upper": upper > 0},
        "radius": {"button": statistics.median(radii) if radii else None, "card": statistics.median(cards) if cards else None},
        "font_links": hints[:6],
    }


# ================================================================================================== engine 1: context.dev
def tokens_from_styleguide(sg: dict, got: dict | None = None) -> dict:
    """Tokens from a context.dev Styleguide response (engine 1). The logo and, when the site's rendered accent is only
    black / white / grey, the brand colour still come from HuhQueue's own read (got)."""
    s = (sg or {}).get("styleguide") or sg or {}
    col = s.get("colors") or {}
    typ = s.get("typography") or {}
    h1 = ((typ.get("headings") or {}).get("h1")) or {}
    p = typ.get("p") or {}
    comp = s.get("components") or {}
    btn = (comp.get("button") or {}).get("primary") or {}
    card = comp.get("card") or {}
    k = (got or {}).get("kit") or {}
    own = (k.get("colors") or {})
    accent = _hexs(col.get("accent")) or own.get("accent") or ""
    if _sat(accent) < 0.18 and own.get("accent") and _sat(own.get("accent")) >= 0.18:
        accent = own["accent"]  # the site's buttons are black: the brand colour is the logo's / the CSS brand variable
    bg = _hexs(col.get("background")) or own.get("primary") or "#FFFFFF"
    text = _hexs(col.get("text")) or own.get("text") or "#111114"
    if bx.contrast(_rgb(text), _rgb(bg)) < 3 or _sat(text) >= 0.45:  # e.g. YouTube's "text" is the link blue: keep the ink
        text = own.get("text") if own.get("text") and bx.contrast(_rgb(own["text"]), _rgb(bg)) >= 3 else (
            "#111114" if bx.lum(_rgb(bg)) > 0.4 else "#FFFFFF")
    surface = _hexs(card.get("backgroundColor") or card.get("background-color") or "")
    btn_bg = _hexs(btn.get("backgroundColor") or btn.get("background-color") or "")
    if btn_bg and (not surface or surface.lower() == bg.lower()) and _chroma(btn_bg) >= 0.08 and bx.lum(_rgb(btn_bg)) > 0.55:
        surface = btn_bg  # a pale tinted button (Wispr Flow's lavender #F0D7FF) is the brand's surface colour, not its accent
    prov = str(((got or {}).get("provenance") or {}).get("accent") or "")
    if btn_bg and accent and accent.lower() == btn_bg.lower() and own.get("accent") and own["accent"].lower() != accent.lower() \
            and _sat(own["accent"]) >= 0.18 and ("brand" in prov or "logo" in prov):
        accent = own["accent"]  # the rendered accent is only the CTA button's colour; the brand colour is the site's own
    return {
        "engine": "context.dev",
        "mode": s.get("mode") or ("dark" if bx.lum(_rgb(bg)) < 0.4 else "light"),
        "colors": {"background": bg, "surface": surface or "", "text": text, "accent": accent, "accent2": own.get("secondary") or ""},
        "fonts": {"head": {"family": str(h1.get("fontFamily") or "")[:60]}, "body": {"family": str(p.get("fontFamily") or "")[:60]}},
        "type": {"h1_px": _px(str(h1.get("fontSize") or "")), "body_px": _px(str(p.get("fontSize") or "")) or 16,
                 "h1_weight": h1.get("fontWeight"), "upper": str(h1.get("textTransform") or "").lower() == "uppercase"},
        "radius": {"button": _px(str(btn.get("borderRadius") or btn.get("border-radius") or "")),
                   "card": _px(str(card.get("borderRadius") or card.get("border-radius") or ""))},
        "spacing": s.get("elementSpacing") or {},
        "font_links": sorted((s.get("fontLinks") or {}).keys())[:6],
    }


def context_dev_key() -> str:
    return (os.environ.get("CONTEXT_DEV_API_KEY") or "").strip()


def context_dev_styleguide(url: str, http=None, timeout: float = 60.0) -> dict | None:
    """The context.dev Styleguide for a URL's domain, or None (no key, an error, a timeout). The key is sent only to
    api.context.dev and never logged; only the HTTP status is."""
    key = context_dev_key()
    if not key:
        return None
    import httpx
    host = (urlparse(url if "//" in url else "https://" + url).hostname or "").removeprefix("www.")
    if not host:
        return None
    client = http or httpx.Client(timeout=timeout)
    try:
        r = client.get(CONTEXT_DEV_URL, params={"domain": host}, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"context.dev styleguide: {e.__class__.__name__}", flush=True)
        return None
    finally:
        if http is None:
            client.close()
    if r.status_code != 200:
        print(f"context.dev styleguide: HTTP {r.status_code}", flush=True)
        return None
    try:
        d = r.json()
    except ValueError:
        return None
    return d if (d or {}).get("styleguide") else None


# ================================================================================================== tokens -> theme
def theme_from_tokens(t: dict, kit: dict | None = None) -> dict:
    """A generated theme from tokens: layout, mode, surface, radius, fonts (bundled ids), case, headline scale and how the
    accent is used. kit gives fallbacks (its colours and fonts) for anything the tokens lack."""
    kit = kit or {}
    c = t.get("colors") or {}
    kc = kit.get("colors") or {}
    bg = c.get("background") or kc.get("primary") or "#111114"
    text = c.get("text") or kc.get("text") or "#FFFFFF"
    accent = c.get("accent") or kc.get("accent") or ""
    dark = (t.get("mode") or ("dark" if bx.lum(_rgb(bg)) < 0.4 else "light")) == "dark"
    hf = ((t.get("fonts") or {}).get("head") or {}).get("family") or ""
    bf = ((t.get("fonts") or {}).get("body") or {}).get("family") or ""
    head = bx.map_font(hf)[0] if hf else (kit.get("font") or "inter")
    body = bx.map_font(bf)[0] if bf else (kit.get("body_font") or "inter")
    if body in ("anton", "dot", "serif", "garamond"):
        body = "sourceserif" if body in ("serif", "garamond") and head in SERIF_IDS else "inter"
    rb = (t.get("radius") or {}).get("button")
    rc = (t.get("radius") or {}).get("card")
    rk = radius_kind(rb)
    typ = t.get("type") or {}
    upper = bool(typ.get("upper")) or head in DISPLAY_IDS
    vivid = _sat(accent) >= 0.45 and bx.lum(_rgb(accent)) > 0.04 if accent else False
    serif_body = body in SERIF_IDS or bx.map_font(bf or "x")[0] in SERIF_IDS
    if head in DISPLAY_IDS or (upper and dark):
        layout = "bold_news"
    elif head in SERIF_IDS:
        layout = "editorial"
    elif not dark and rk in ("round", "pill") and vivid and not serif_body:
        layout = "playful"
    else:  # a plain sans headline (over a serif body too: YC's Hacker-News feel) is minimal, whatever the buttons' corners
        layout = "minimal"
    h1 = typ.get("h1_px")
    scale = 1.0
    if h1:
        scale = 1.15 if h1 >= 64 else 0.92 if h1 <= 36 else 1.0
    surface = c.get("surface") or ""
    if not surface or bx.contrast(_rgb(surface), _rgb(bg)) > 1.6 or surface == bg:
        surface = bx.to_hex(tuple(int(_rgb(bg)[i] + (_rgb(text)[i] - _rgb(bg)[i]) * (0.07 if dark else 0.045)) for i in range(3)))
    radius = 0 if rk == "sharp" else int(rb if rb and rb < 100 else (40 if rk == "pill" else 14))
    variant = pick_variant(layout, dark, bg, surface, accent, vivid, serif_body)
    return {"layout": layout, "variant": variant, "mode": "dark" if dark else "light", "surface": surface,
            "radius": max(0, min(radius, 48)), "radius_kind": rk, "card_radius": int(rc) if rc and rc < 100 else radius,
            "head_font": head, "body_font": body, "case": "upper" if upper else "title", "scale": scale,
            "accent_usage": "fill" if layout in ("bold_news", "playful") else "line" if layout == "minimal" else "text"}


VARIANTS = {"editorial": ("rules", "masthead"), "minimal": ("line", "bar")}


def pick_variant(layout: str, dark: bool, bg: str, surface: str, accent: str, vivid: bool, serif_body: bool) -> str:
    """Two sites in the same layout family should still look different, so each family has two compositions, picked
    from more of the site's signals:

      editorial  rules     logo + tracked kicker on a header rule, headline left (YC's serif pages, a plain paper ground)
                 masthead  centred masthead, the headline on the brand's tinted surface block (Wispr Flow's lavender)
      minimal    line      a short accent line over the headline (Linear, Notion)
                 bar       a solid accent bar across the top, plain sans, the logo in the bar's shadow (YC / Hacker News)
    """
    if layout == "editorial":
        tinted = bool(surface) and _chroma(surface) >= 0.08 and surface.lower() != (bg or "").lower()
        return "masthead" if tinted else "rules"
    if layout == "minimal":
        off_white = not dark and bg and bg.upper() not in ("#FFFFFF", "#FEFEFE", "#FDFDFD")
        return "bar" if (not dark and vivid and (serif_body or off_white)) else "line"
    return ""


def theme_for_kit(kit: dict) -> dict:
    """A theme for a kit that was never read from a site (a sample, HuhByte AI, one built by hand): from its fonts and
    colours, with the same rules."""
    c = kit.get("colors") or {}
    t = {"mode": "dark" if bx.lum(_rgb(c.get("primary") or "#111114")) < 0.4 else "light",
         "colors": {"background": c.get("primary") or "#111114", "text": c.get("text") or "#FFFFFF", "accent": c.get("accent") or ""},
         "fonts": {}, "type": {"upper": kit.get("headline_case") == "upper"},
         "radius": {"button": {"sharp": 0, "soft": 10, "round": 24, "pill": 999}.get(kit.get("radius_kind") or "", 10)}}
    th = theme_from_tokens(t, kit)
    th["head_font"], th["body_font"] = kit.get("font") or th["head_font"], kit.get("body_font") or th["body_font"]
    if th["head_font"] in DISPLAY_IDS:
        th["layout"], th["accent_usage"] = "bold_news", "fill"
    elif th["head_font"] in SERIF_IDS:
        th["layout"], th["accent_usage"] = "editorial", "text"
    if th.get("variant") not in VARIANTS.get(th["layout"], ("",)):
        th["variant"] = VARIANTS.get(th["layout"], ("",))[0]
    return th


def clean_theme(v: Any) -> dict | None:
    """A theme from a request body, validated (unknown keys dropped)."""
    if not isinstance(v, dict) or v.get("layout") not in LAYOUTS:
        return None
    out: dict[str, Any] = {"layout": v["layout"], "mode": "dark" if v.get("mode") == "dark" else "light"}
    s = _hexs(v.get("surface"))
    if s:
        out["surface"] = s
    for f, lo, hi in (("radius", 0, 48), ("card_radius", 0, 48)):
        try:
            out[f] = max(lo, min(hi, int(v.get(f) or 0)))
        except (TypeError, ValueError):
            out[f] = 0
    try:
        out["scale"] = max(0.8, min(1.25, float(v.get("scale") or 1.0)))
    except (TypeError, ValueError):
        out["scale"] = 1.0
    out["radius_kind"] = v.get("radius_kind") if v.get("radius_kind") in ("sharp", "soft", "round", "pill") else radius_kind(out["radius"])
    out["case"] = "upper" if v.get("case") == "upper" else "title"
    out["accent_usage"] = v.get("accent_usage") if v.get("accent_usage") in ("fill", "line", "text") else "line"
    if v.get("variant") in VARIANTS.get(out["layout"], ()):
        out["variant"] = v["variant"]
    for f in ("head_font", "body_font"):
        if isinstance(v.get(f), str) and len(v[f]) < 30:
            out[f] = v[f]
    return out


# ================================================================================================== DESIGN.md
def design_md(name: str, url: str, t: dict, th: dict, ms: int) -> str:
    c = t.get("colors") or {}
    f = t.get("fonts") or {}
    host = (urlparse(url).hostname or url or "").removeprefix("www.")
    r = t.get("radius") or {}
    ty = t.get("type") or {}
    fmt = lambda v: "—" if v in (None, "") else (f"{v:g}px" if isinstance(v, (int, float)) and v < 999 else "pill" if isinstance(v, (int, float)) else str(v))  # noqa: E731
    lines = [
        f"# DESIGN.md: {name or host}",
        "",
        f"Style taken from {host} by {('the context.dev Styleguide API' if t.get('engine') == 'context.dev' else 'HuhQueue’s CSS reader')} "
        f"in {ms} ms ({time.strftime('%Y-%m-%d', time.gmtime())}). Not affiliated; trademarks belong to their owners.",
        "",
        "## Tokens",
        "",
        "| Token | Value |",
        "|---|---|",
        f"| Mode | {t.get('mode') or th.get('mode')} |",
        f"| Background | {c.get('background') or '—'} |",
        f"| Surface | {th.get('surface') or c.get('surface') or '—'} |",
        f"| Text | {c.get('text') or '—'} |",
        f"| Accent | {c.get('accent') or '—'} |",
        f"| Second accent | {c.get('accent2') or '—'} |",
        f"| Headline font | {(f.get('head') or {}).get('family') or '—'} → {th.get('head_font')} (bundled) |",
        f"| Body font | {(f.get('body') or {}).get('family') or '—'} → {th.get('body_font')} (bundled) |",
        f"| H1 size | {fmt(ty.get('h1_px'))} |",
        f"| Headline case | {th.get('case')} |",
        f"| Button radius | {fmt(r.get('button'))} ({th.get('radius_kind')}) |",
        f"| Card radius | {fmt(r.get('card'))} |",
        "",
        "## Generated theme",
        "",
        f"- Layout: **{th.get('layout')}**: {LAYOUTS.get(th.get('layout'), '')}",
        f"- Ground: {th.get('mode')} {c.get('background') or ''}, cards on {th.get('surface')} with {th.get('radius')}px corners",
        f"- Type: {th.get('head_font')} headlines ({th.get('case')}, scale {th.get('scale')}), {th.get('body_font')} body",
        f"- Accent: used as {th.get('accent_usage')}",
    ]
    if t.get("font_links"):
        lines += ["", "## Fonts the site loads", ""] + [f"- {x}" for x in t["font_links"]]
    return "\n".join(lines) + "\n"


def build(url: str, html: str, css_texts: list[str], got: dict, http=None) -> dict:
    """Engine 1 when configured, else engine 2. Returns {"tokens", "theme", "md", "engine", "ms", "site"} and updates
    got["kit"] in place: font / body_font / headline_case / front_card / end_card follow the theme, colours keep the brand's."""
    t0 = time.time()
    tokens = None
    sg = context_dev_styleguide(url, http=None) if context_dev_key() else None
    if sg:
        tokens = tokens_from_styleguide(sg, got)
    if tokens is None:
        tokens = tokens_from_css(url, html, css_texts, got)
    kit = got.setdefault("kit", {})
    th = theme_from_tokens(tokens, kit)
    c = dict(kit.get("colors") or {})
    tc = tokens.get("colors") or {}
    if tokens.get("engine") == "context.dev":  # the rendered page's ground and ink are the truth; the accent stays the brand's
        c["primary"], c["text"] = tc.get("background") or c.get("primary"), tc.get("text") or c.get("text")
        if tc.get("accent"):
            c["accent"] = tc["accent"]
    kit["colors"] = c
    kit["font"], kit["body_font"] = th["head_font"], th["body_font"]
    kit["headline_case"] = th["case"]
    style = {"bold_news": "bold", "editorial": "minimal" if th["mode"] == "light" else "glow", "minimal": "minimal" if th["mode"] == "light" else "glow",
             "playful": "minimal"}[th["layout"]]
    kit["front_card"] = {**(kit.get("front_card") or {}), "style": style}
    kit["end_card"] = {**(kit.get("end_card") or {}), "style": style}
    kit["theme"] = th
    ms = int((time.time() - t0) * 1000)
    site = (urlparse(url).hostname or "").removeprefix("www.")
    md = design_md(kit.get("name") or site, url, tokens, th, ms)
    kit["design"] = {"engine": tokens.get("engine"), "site": site, "url": url, "tokens": tokens, "md": md, "ms": ms,
                     "extracted_at": int(time.time())}
    return kit["design"]
