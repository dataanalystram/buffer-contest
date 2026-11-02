"""Brand onboarding: a website URL -> a brand kit, with no AI and no paid API (owner, 2 Nov 2026).

What it reads, and where each field comes from (every field reports its source in `provenance`):

  name        <meta name=application-name>, og:site_name, then <title>, cut at the first " | ", " - ", ": "
  handle      twitter:site / twitter:creator, else the first x.com / twitter.com / instagram.com profile link
  logo        an inline <svg> in the header that is the brand's (aria-label or class names the brand / "logo", or it sits in
              the link home), drawn by studio.svgraster; else an SVG favicon; else apple-touch-icon / the largest PNG icon;
              else og:image as the last resort. `mark` is the square symbol when there is one (favicon / touch icon).
  colours     the site's CSS custom properties (var() chains resolved): --*brand*, --*accent*, --*primary* buttons and links
              give the accent; theme-color / --*bg-primary* / body background give the ground (dark or light site);
              --*text-primary* (or black / white by contrast) gives the text. No usable variable: the most frequent
              saturated colour in the CSS, then the logo's own palette.
  fonts       font-family declarations and @font-face rules (vars resolved, system and monospace stacks skipped). The
              headline family is the one named for headings / display when the site names one, else the most used. Each
              is mapped to the closest bundled face (Inter, Poppins, Figtree, Anton, DM Serif Display) and says so.
  voice       the homepage's own words: the h1, the description and the first real paragraphs, plus plain notes on how
              they read (sentence length, "you" vs "we", exclamation marks). Used as the caption writer's voice sample.

Pure functions (`extract(url, html, css_texts)`) so tests run on fixture HTML; `fetch_site` does the network part with
netguard (every redirect hop re-checked, private hosts refused, size caps).
"""
from __future__ import annotations

import colorsys
import io
import math
import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from . import svgraster

HEX6 = re.compile(r"^#[0-9A-F]{6}$")
MAX_CSS_FILES = 20
MAX_CSS_BYTES = 700_000
MAX_HTML_BYTES = 3_000_000
GENERIC = {"inherit", "initial", "unset", "sans-serif", "serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif",
           "ui-monospace", "-apple-system", "blinkmacsystemfont", "segoe ui", "helvetica", "helvetica neue", "arial",
           "roboto", "apple color emoji", "segoe ui emoji", "segoe ui symbol", "noto color emoji", "emoji", "cursive",
           "fantasy", "math", "fangsong", "ui-rounded", "noto sans", "ubuntu", "cantarell", "oxygen", "fira sans",
           "droid sans", "open sans", "liberation sans", "sf pro text", "sf pro display", "menlo", "monaco", "consolas",
           "courier new", "courier", "times new roman", "times", "georgia"}


# ================================================================================================= colours
def _clamp(v: float) -> int:
    return int(max(0, min(255, round(v))))


def to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(_clamp(v) for v in rgb[:3])


def parse_css_color(v: str) -> tuple[tuple[int, int, int], float] | None:
    """A CSS colour value -> ((r, g, b), alpha). Handles #rgb(a), #rrggbb(aa), rgb()/rgba() (comma or space syntax),
    hsl()/hsla(), the bare 'h s% l%' triplet some design systems store in variables, and a few names."""
    v = (v or "").strip().lower().rstrip(";").replace("!important", "").strip()
    if not v:
        return None
    names = {"white": (255, 255, 255), "black": (0, 0, 0), "transparent": None}
    if v in names:
        return (names[v], 1.0) if names[v] else ((0, 0, 0), 0.0)
    m = re.fullmatch(r"#([0-9a-f]{3,8})", v)
    if m:
        h = m.group(1)
        if len(h) in (3, 4):
            a = int(h[3] * 2, 16) / 255 if len(h) == 4 else 1.0
            h = "".join(c * 2 for c in h[:3])
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), a  # type: ignore[return-value]
        if len(h) in (6, 8):
            a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)), a  # type: ignore[return-value]
        return None
    m = re.fullmatch(r"(rgba?|hsla?)\(([^)]*)\)", v)
    body = m.group(2) if m else (v if re.fullmatch(r"[\d.]+(?:deg)?\s+[\d.]+%\s+[\d.]+%(?:\s*/\s*[\d.]+%?)?", v) else None)
    kind = (m.group(1)[:3] if m else "hsl")
    if body is None:
        return None
    parts = [p for p in re.split(r"[\s,/]+", body.strip()) if p]
    if len(parts) < 3:
        return None
    try:
        alpha = 1.0
        if len(parts) >= 4:
            alpha = float(parts[3].rstrip("%")) / (100 if parts[3].endswith("%") else 1)
        if kind == "rgb":
            vals = [float(p.rstrip("%")) * (2.55 if p.endswith("%") else 1) for p in parts[:3]]
            return tuple(_clamp(x) for x in vals), alpha  # type: ignore[return-value]
        h = float(parts[0].replace("deg", "")) / 360
        s = float(parts[1].rstrip("%")) / 100
        l_ = float(parts[2].rstrip("%")) / 100
        r, g, b = colorsys.hls_to_rgb(h % 1, l_, s)
        return (_clamp(r * 255), _clamp(g * 255), _clamp(b * 255)), alpha
    except ValueError:
        return None


def lum(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(rgb[0]) + 0.7152 * ch(rgb[1]) + 0.0722 * ch(rgb[2])


def contrast(a, b) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def sat(rgb) -> float:
    h, l_, s = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
    return s if 0.08 < l_ < 0.92 else 0.0


def hexnorm(rgb) -> str:
    return to_hex(rgb)


# ================================================================================================= css
_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_DECL_VAR = re.compile(r"(--[\w-]+)\s*:\s*([^;{}]+)")
_FONT_FAMILY = re.compile(r"font-family\s*:\s*([^;{}]+)", re.I)
_FONT_FACE = re.compile(r"@font-face\s*{([^}]*)}", re.I)
_COLOR_LIT = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)|hsla?\([^)]*\)")


def css_vars(css: str) -> dict[str, list[str]]:
    """Every custom property and each value it is given, in order (light theme first on most sites)."""
    out: dict[str, list[str]] = {}
    for k, v in _DECL_VAR.findall(_COMMENT.sub("", css)):
        out.setdefault(k.strip().lower(), []).append(v.strip())
    return out


def resolve(value: str, vars_: dict[str, list[str]], depth: int = 0) -> str:
    """var(--a, fallback) chains -> the literal value (first definition wins), at most 8 deep."""
    if depth > 8 or "var(" not in value:
        return value
    def sub(m):
        name, fb = m.group(1).lower(), (m.group(2) or "").strip()
        got = vars_.get(name)
        return resolve(got[0], vars_, depth + 1) if got else resolve(fb, vars_, depth + 1)
    return resolve(re.sub(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^()]*(?:\([^()]*\))?[^()]*))?\)", sub, value, count=1), vars_, depth + 1)


ACCENT_WORDS = [("brand", 6.0), ("accent", 4.0), ("button-primary-bg", 3.5), ("button-primary-background", 3.5),
                ("primary-bg", 3.0), ("primary", 2.2), ("cta", 2.5), ("highlight", 1.5), ("link", 1.4), ("blue-500", 0.8),
                ("main", 0.6)]
GLOBAL_PREFIX = ("--color-", "--brand", "--accent", "--primary", "--theme", "--c-", "--clr-")
NOT_ACCENT = ("text", "fg", "foreground", "border", "line", "overlay", "shadow", "divider", "muted", "neutral", "gray", "grey",
              "surface", "background", "-bg-primary", "bg-primary", "ink", "outline", "placeholder", "scrollbar", "selection")


# A CSS framework's own default link / button colour is not the brand's (brand kit v2: wisprflow.ai came out Webflow blue)
FRAMEWORK_DEFAULTS = {"#3898EC", "#0082F3", "#007BFF", "#0D6EFD", "#337AB7", "#428BCA", "#0000EE", "#551A8B"}


def color_candidates(vars_: dict[str, list[str]], css: str) -> list[dict]:
    """Accent candidates: named variables first (scored by name), then raw colours by frequency."""
    out = []
    uses_all = Counter(m.lower() for m in re.findall(r"var\(\s*(--[\w-]+)", css))
    for name, vals in vars_.items():
        w = next((s for k, s in ACCENT_WORDS if k in name), 0.0)
        if not w:
            continue
        if any(b in name for b in NOT_ACCENT) and not any(k in name for k in ("brand", "accent", "button-primary")):
            continue
        if "hover" in name or "active" in name or "pressed" in name or "disabled" in name:
            w *= 0.4
        if not name.startswith(GLOBAL_PREFIX) and not re.match(r"--[a-z0-9]+-(color-)?(brand|accent|primary)", name):
            w *= 0.6  # a component's own variable (--plan-tail-accent), not the site's palette
        if re.fullmatch(r"--(color-)?(brand|accent|primary)(-color|-bg|-base|-500|-default|-main)?", name):
            w += 3.0  # the site's global brand token
        got = parse_css_color(resolve(vals[0], vars_))
        if not got or got[1] < 0.6:
            continue
        rgb = got[0]
        uses = uses_all.get(name, 0)  # a token the site actually uses beats one defined and forgotten
        out.append({"rgb": rgb, "score": w * (0.6 + 0.4 * sat(rgb)) * (0.7 + 0.3 * math.log2(2 + uses)), "source": f"CSS variable {name}"})
    freq = Counter()
    for lit in _COLOR_LIT.findall(_COMMENT.sub("", css)):
        got = parse_css_color(lit)
        if got and got[1] >= 0.6 and sat(got[0]) >= 0.3 and to_hex(got[0]) not in FRAMEWORK_DEFAULTS:
            freq[to_hex(got[0])] += 1
    # Where a raw colour is used says more than how often. A brand colour fills blocks and cards (Wispr Flow's dark green
    # pricing blocks); a colour used only on a call-to-action button, or for error / warning states and strikethroughs
    # (Wispr's coral #FF6C4C), is not the brand colour even at the same count.
    fill, cta, state = Counter(), Counter(), Counter()
    for sel, body in re.findall(r"([^{}@;]+)\{([^{}]*)\}", _COMMENT.sub("", css)):
        is_cta = bool(re.search(r"button|\bbtn|\bcta|\[type=.?submit", sel, re.I))
        is_state = bool(re.search(r"error|invalid|warning|danger|strike|alert", sel, re.I))
        for prop, val in re.findall(r"([a-z-]+)\s*:\s*([^;]+)", body, re.I):
            for lit in _COLOR_LIT.findall(val):
                got = parse_css_color(lit)
                if not got:
                    continue
                hx = to_hex(got[0])
                if is_state:
                    state[hx] += 1
                elif "background" in prop.lower():
                    (cta if is_cta else fill)[hx] += 1
    for hx, n in freq.most_common(6):
        rgb = tuple(int(hx[i:i + 2], 16) for i in (1, 3, 5))
        w, why = 1.0, ""
        if fill.get(hx):
            w, why = 1.35, ", fills blocks and cards"
        elif cta.get(hx):
            w, why = 0.55, ", only on call-to-action buttons"
        elif state.get(hx) and state[hx] * 2 >= n:
            w, why = 0.5, ", mostly error / state styles"
        out.append({"rgb": rgb, "score": min(1.2, 0.15 * n) * (0.35 + sat(rgb)) * w,
                    "source": f"most used colour in the CSS ({n}x{why})"})
    out.sort(key=lambda c: -c["score"])
    return out


def _first_color(vars_, names_any: tuple[str, ...], exclude: tuple[str, ...] = ()) -> list[tuple[str, tuple]]:
    hits = []
    for name, vals in vars_.items():
        if any(n in name for n in names_any) and not any(x in name for x in exclude):
            for v in vals:
                got = parse_css_color(resolve(v, vars_))
                if got and got[1] >= 0.9:
                    hits.append((name, got[0]))
    return hits


def _body_background(css: str, vars_) -> tuple | None:
    for sel, body in re.findall(r"(?:^|})\s*((?:html|body|:root)[^{]{0,40}){([^}]*)}", _COMMENT.sub("", css)):
        m = re.search(r"background(?:-color)?\s*:\s*([^;]+)", body)
        if m:
            got = parse_css_color(resolve(m.group(1).split()[0] if m.group(1).strip().startswith("#") else m.group(1), vars_))
            if got and got[1] >= 0.9:
                return got[0]
    return None


def pick_palette(theme_color: str, vars_: dict, css: str, logo_palette: list[tuple] | None = None) -> dict:
    """The kit colours (primary = ground, accent, text, secondary) and where each came from."""
    prov = {}
    ground = None
    tc = parse_css_color(theme_color) if theme_color else None
    if tc and tc[1] >= 0.9:
        ground, prov["primary"] = tc[0], "theme-color meta tag"
    if ground is None:
        bgs = [(n, c) for n, c in _first_color(vars_, ("bg-primary", "background-primary", "background-base", "-bg-base",
                                                        "page-bg", "page-background", "background-default", "bg-default"),
                                               ("hover", "inverse", "accent", "button", "brand", "active", "focus", "pressed",
                                                "selected", "disabled", "alpha", "overlay", "card", "elevated"))
               if sat(c) < 0.25]  # a page ground is neutral; a saturated "background" token is a state or a block
        bgs.sort(key=lambda t: (not re.search(r"(bg|background)-(primary|base|default)$", t[0]), len(t[0])))
        if bgs:
            ground, prov["primary"] = bgs[0][1], f"CSS variable {bgs[0][0]}"
    if ground is None:
        bb = _body_background(css, vars_)
        if bb:
            ground, prov["primary"] = bb, "body background in the CSS"
    if ground is None:
        ground, prov["primary"] = (255, 255, 255), "default (light site)"
    dark = lum(ground) < 0.2
    # text: the site's own text colour that reads on this ground, else black / white
    text = None
    for name, rgb in _first_color(vars_, ("text-primary", "fg-primary", "foreground", "color-text", "text-default", "body-text"),
                                  ("inverse", "button", "brand", "link", "secondary", "tertiary", "muted")):
        if contrast(rgb, ground) >= 4.5:
            text, prov["text"] = rgb, f"CSS variable {name}"
            break
    if text is None:
        text = (255, 255, 255) if dark else (17, 17, 20)
        prov["text"] = "white on a dark ground" if dark else "near-black on a light ground"
    # accent: the best-scored brand colour that is not the ground or the text and can be seen on the ground
    cands = color_candidates(vars_, css)
    for rgb in logo_palette or []:
        cands.append({"rgb": tuple(rgb), "score": 0.5 * (0.35 + sat(rgb)), "source": "the logo's colours"})
    cands.sort(key=lambda c: -c["score"])
    # A pale brand colour (Buffer's mint) vanishes on a white page; when the site also has a dark brand colour, the cards
    # use that as the ground, the way the brand's own social posts do.
    if not dark and cands:
        top = cands[0]["score"]
        pale = next((c for c in cands if c["score"] >= 0.5 * top and sat(c["rgb"]) >= 0.3 and contrast(c["rgb"], ground) < 1.6), None)
        deep = next((c for c in cands if "brand" in c["source"] and lum(c["rgb"]) < 0.06), None)
        if pale and deep:
            ground, dark = deep["rgb"], True
            prov["primary"] = deep["source"] + " (the brand's dark colour; its main colour is too pale for a white card)"
            text = (255, 255, 255) if contrast((255, 255, 255), ground) >= 4.5 else text
            prov["text"] = "white on the brand's dark colour"
            cands = [pale] + [c for c in cands if c is not pale and c is not deep]
    accent, second = None, None
    for c in cands:
        rgb = c["rgb"]
        if contrast(rgb, ground) < 1.6 or sat(rgb) < 0.18 or (accent and _close(rgb, accent)):
            continue
        if accent is None:
            accent, prov["accent"] = rgb, c["source"]
        elif second is None:
            second, prov["secondary"] = rgb, c["source"]
            break
    if accent is None:  # a monochrome brand: the text colour is the accent (pills in ink)
        accent, prov["accent"] = text, "monochrome site: accent is the text colour"
    out = {"primary": to_hex(ground), "accent": to_hex(accent), "text": to_hex(text)}
    if second:
        out["secondary"] = to_hex(second)
    return {"colors": out, "dark": dark, "provenance": prov}


def _close(a, b, d: int = 40) -> bool:
    return sum(abs(a[i] - b[i]) for i in range(3)) < d


# ================================================================================================= fonts
BUNDLED = {"inter": "Inter", "poppins": "Poppins", "figtree": "Figtree", "anton": "Anton", "serif": "DM Serif Display", "dot": "Doto",
           "garamond": "EB Garamond", "sourceserif": "Source Serif 4", "roboto": "Roboto"}
_FONT_MAP = [  # (substring in the family, bundled face)
    ("ndot", "dot"), ("ntype", "dot"), ("doto", "dot"), ("dot matrix", "dot"), ("dotmatrix", "dot"), ("lcd", "dot"), ("pixel", "dot"),
    ("inter", "inter"), ("poppins", "poppins"), ("figtree", "figtree"), ("anton", "anton"), ("oswald", "anton"),
    ("bebas", "anton"), ("league gothic", "anton"), ("condensed", "anton"), ("compressed", "anton"), ("druk", "anton"),
    ("dm serif", "serif"), ("serif display", "serif"), ("lyon", "garamond"), ("tiempos", "sourceserif"), ("garamond", "garamond"), ("caslon", "garamond"), ("cormorant", "garamond"),
    ("playfair", "serif"), ("freight", "serif"), ("canela", "serif"), ("recoleta", "serif"), ("gt super", "serif"),
    ("noe ", "serif"), ("merriweather", "sourceserif"), ("libre baskerville", "garamond"), ("source serif", "sourceserif"),
    ("newsreader", "sourceserif"), ("georgia", "sourceserif"), ("charter", "sourceserif"), ("youtube sans", "roboto"), ("yt sans", "roboto"),
    ("verdana", "roboto"), ("tahoma", "roboto"),
    ("fraunces", "serif"), ("instrument serif", "garamond"), ("ivar", "serif"), ("domaine", "serif"), ("editorial new", "serif"),
    ("montserrat", "poppins"), ("gilroy", "poppins"), ("futura", "poppins"), ("product sans", "poppins"),
    ("google sans", "poppins"), ("circular", "figtree"), ("walsheim", "figtree"), ("dm sans", "figtree"),
    ("jakarta", "figtree"), ("manrope", "figtree"), ("satoshi", "figtree"), ("outfit", "figtree"), ("avenir", "figtree"),
    ("nunito", "figtree"), ("rubik", "figtree"), ("general sans", "figtree"), ("cera", "figtree"), ("roobert", "figtree"),
    ("stolzl", "figtree"), ("geist", "inter"), ("sohne", "inter"), ("söhne", "inter"), ("suisse", "inter"), ("haas", "inter"), ("graphik", "inter"),
    ("aeonik", "inter"), ("untitled sans", "inter"), ("sf pro", "inter"), ("ibm plex sans", "inter"), ("work sans", "inter"),
    ("public sans", "inter"), ("helvetica", "inter"), ("arial", "inter"), ("roboto", "roboto"), ("lato", "figtree"),
]


def map_font(family: str) -> tuple[str, str]:
    """(bundled id, 'exact'|'closest') for a CSS family name."""
    f = family.lower().strip()
    for key, bid in _FONT_MAP:
        if key in f:
            return bid, ("exact" if f.replace(" variable", "").replace(" display", "").strip() == BUNDLED[bid].lower()
                         or f.startswith(BUNDLED[bid].lower()) else "closest")
    if re.search(r"(?<!sans[- ])serif", f) or "slab" in f:
        return "sourceserif", "closest"
    return "inter", "closest"


def _first_family(decl: str, vars_) -> str:
    v = resolve(decl, vars_)
    for part in v.split(","):
        name = part.strip().strip("'\"").strip()
        low = name.lower()
        if not name or low in GENERIC or low.startswith(("var(", "--")) or re.match(r"^[\d.]", low) or "mono" in low or "emoji" in low or "icon" in low \
                or "awesome" in low or "symbol" in low:
            continue
        return name[:60]
    return ""


def pick_fonts(vars_: dict, css: str, html_styles: str = "") -> dict:
    allcss = _COMMENT.sub("", css + "\n" + html_styles)
    counts: Counter = Counter()
    for decl in _FONT_FAMILY.findall(allcss):
        fam = _first_family(decl, vars_)
        if fam:
            counts[fam] += 1
    for body in _FONT_FACE.findall(allcss):
        m = re.search(r"font-family\s*:\s*([^;]+)", body)
        if m:
            fam = m.group(1).strip().strip("'\"")
            if fam and "mono" not in fam.lower() and fam.lower() not in GENERIC:
                counts[fam] += 0.5
    head = body_f = ""
    prov = {}
    ranked0 = [f for f, _ in counts.most_common()]
    top_serif = bool(ranked0) and map_font(ranked0[0])[0] in ("serif", "garamond", "sourceserif")
    for name, vals in vars_.items():  # a family the site names for headings / display wins for the headline
        if re.search(r"size|weight|line|height|spacing|leading|tracking|feature|variation|settings|letter|stretch|style", name):
            continue
        if re.search(r"font[\w-]*(heading|display|title|headline)", name) and not head and ("serif" not in name or top_serif) \
                and "mono" not in name:
            fam = _first_family(vals[0], vars_)
            if fam:
                head, prov["head_font"] = fam, f"CSS variable {name}"
        if re.search(r"font[\w-]*(body|text|sans|base|regular)", name) and not body_f:
            fam = _first_family(vals[0], vars_)
            if fam:
                body_f, prov["body_font"] = fam, f"CSS variable {name}"
    ranked = [f for f, _ in counts.most_common()]
    if not body_f and ranked:
        body_f, prov["body_font"] = ranked[0], f"most used font-family ({counts[ranked[0]]:g}x)"
    if not head:
        head = body_f
        prov["head_font"] = prov.get("body_font", "")
    hb, hm = map_font(head) if head else ("inter", "closest")
    bb, bm = map_font(body_f) if body_f else ("inter", "closest")
    if bb in ("anton", "serif", "dot", "garamond"):  # a condensed / display face never sets body copy
        bb = "sourceserif" if bb == "garamond" else "inter"
    return {"head": {"family": head, "bundled": hb, "match": hm if head else "default"},
            "body": {"family": body_f, "bundled": bb, "match": bm if body_f else "default"},
            "provenance": prov}


# ================================================================================================= html
class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.icons: list[dict] = []
        self.css_links: list[str] = []
        self.styles: list[str] = []
        self.title = ""
        self.h1: list[str] = []
        self.paras: list[str] = []
        self.socials: list[str] = []
        self.svgs: list[dict] = []          # {"svg": text, "hint": str, "home": bool, "in_header": bool, "order": n}
        self.logo_imgs: list[dict] = []
        self._stack: list[str] = []
        self._text_tag = ""
        self._buf: list[str] = []
        self._svg_depth = 0
        self._svg_buf: list[str] = []
        self._svg_ctx: dict = {}
        self._a: list[dict] = []
        self._in_style = False
        self._header = 0
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if self._svg_depth:
            self._svg_depth += 1
            self._svg_buf.append(self.get_starttag_text() or "")
            return
        if tag in ("script", "noscript", "template"):
            self._skip += 1
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"].strip())
        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "")
            if "stylesheet" in rel and href:
                self.css_links.append(href)
            elif ("icon" in rel or rel == "mask-icon") and href:
                self.icons.append({"rel": rel, "href": href, "sizes": a.get("sizes", ""), "type": a.get("type", "")})
        elif tag == "title":
            self._text_tag, self._buf = "title", []
        elif tag == "style":
            self._in_style, self._buf = True, []
        elif tag in ("header", "nav"):
            self._header += 1
        elif tag == "a":
            href = a.get("href", "")
            if re.match(r"https?://(www\.)?(x|twitter|instagram)\.com/[A-Za-z0-9_.]{2,30}/?$", href):
                self.socials.append(href)
            self._a.append({"href": href, "label": (a.get("aria-label", "") + " " + a.get("class", "") + " " + a.get("title", "")).lower()})
        elif tag == "img":
            hint = " ".join(a.get(k, "") for k in ("alt", "class", "id", "src")).lower()
            if "logo" in hint and a.get("src") and not a["src"].startswith("data:"):
                home = bool(self._a and self._a[-1]["href"] in ("/", "./", "#") or (self._a and "home" in self._a[-1]["label"]))
                self.logo_imgs.append({"src": a["src"], "home": home, "in_header": self._header > 0, "hint": hint})
        elif tag == "svg":
            self._svg_depth = 1
            self._svg_buf = [self.get_starttag_text() or ""]
            parent = self._a[-1] if self._a else {}
            self._svg_ctx = {"hint": " ".join(a.get(k, "") for k in ("aria-label", "class", "id")).lower() + " " + parent.get("label", ""),
                             "home": parent.get("href", "") in ("/", "./", "#") or "home" in parent.get("label", ""),
                             "in_header": self._header > 0, "order": len(self.svgs), "w": a.get("width", ""), "h": a.get("height", "")}
        elif tag in ("h1", "p") and not self._skip:
            self._text_tag, self._buf = tag, []

    def handle_startendtag(self, tag, attrs):
        if self._svg_depth:
            self._svg_buf.append(self.get_starttag_text() or "")
            return
        self.handle_starttag(tag, attrs)
        if tag == "svg":  # <svg/> with nothing inside
            self._svg_depth = 0

    def handle_endtag(self, tag):
        if self._svg_depth:
            self._svg_buf.append(f"</{tag}>")
            self._svg_depth -= 1
            if self._svg_depth == 0:
                svg = "".join(self._svg_buf)
                if len(svg) < svgraster.MAX_BYTES:
                    self.svgs.append({**self._svg_ctx, "svg": svg})
            return
        if tag in ("script", "noscript", "template") and self._skip:
            self._skip -= 1
        if tag == "style" and self._in_style:
            self.styles.append("".join(self._buf))
            self._in_style = False
        elif tag in ("header", "nav") and self._header:
            self._header -= 1
        elif tag == "a" and self._a:
            self._a.pop()
        elif tag == self._text_tag:
            t = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = t
            elif tag == "h1" and t:
                self.h1.append(t[:200])
            elif tag == "p" and len(t) >= 40:
                self.paras.append(t[:400])
            self._text_tag = ""

    def handle_data(self, data):
        if self._svg_depth:
            self._svg_buf.append(data.replace("&", "&amp;").replace("<", "&lt;"))
        elif self._in_style or self._text_tag:
            self._buf.append(data)


def _clean_name(s: str) -> str:
    s = re.split(r"\s+[|–—·-]\s+|:\s+", s or "")[0].strip()
    return s[:60]


def page_info(url: str, html: str) -> dict:
    p = _Page()
    try:
        p.feed(html[:MAX_HTML_BYTES])
        p.close()
    except Exception:  # a broken page still gives what was parsed so far
        pass
    m = p.meta
    host = (urlparse(url).hostname or "").removeprefix("www.")
    name, name_src = "", ""
    for key, src in (("application-name", "application-name meta tag"), ("og:site_name", "og:site_name"),
                     ("apple-mobile-web-app-title", "apple-mobile-web-app-title")):
        if m.get(key) and _clean_name(m[key]):
            name, name_src = _clean_name(m[key]), src
            break
    if not name and p.title:
        name, name_src = _clean_name(p.title), "page title"
    if not name:
        name, name_src = host.split(".")[0].title(), "domain"
    handle, h_src = "", ""
    for key in ("twitter:site", "twitter:creator"):
        v = m.get(key, "").strip().lstrip("@")
        if re.fullmatch(r"[A-Za-z0-9_]{1,30}", v):
            handle, h_src = v, f"{key} meta tag"
            break
    if not handle and p.socials:
        handle = p.socials[0].rstrip("/").rsplit("/", 1)[-1]
        h_src = "social link on the homepage"
    return {"page": p, "name": name, "name_src": name_src, "handle": handle, "handle_src": h_src,
            "theme_color": m.get("theme-color", ""), "description": (m.get("og:description") or m.get("description") or "")[:300],
            "og_image": urljoin(url, m["og:image"]) if m.get("og:image") else "", "host": host}


def _unrepeat(t: str) -> str:
    """'Ship it.Ship it.Ship it.' (the same headline in mobile and desktop spans) -> 'Ship it.'"""
    s = re.sub(r"\s+", "", t)
    for k in (4, 3, 2):
        if len(s) % k == 0 and s and s == s[: len(s) // k] * k:
            want, out = len(s) // k, []
            for ch in t:
                out.append(ch)
                if len(re.sub(r"\s+", "", "".join(out))) >= want:
                    break
            return "".join(out).strip()
    return t


def voice_from(h1: list[str], description: str, paras: list[str]) -> dict:
    """The homepage's own words as a voice sample, and plain notes on how they read. No model."""
    h1 = [_unrepeat(h) for h in h1]
    parts = []
    for t in (h1[:1] + [description] + paras[:3]):
        t = " ".join((t or "").split())
        if t and t not in parts and not re.search(r"cookie|javascript|©|all rights reserved", t, re.I):
            parts.append(t)
    sample = " ".join(p if p.endswith((".", "!", "?")) else p + "." for p in parts)[:420].strip()
    sents = [s for s in re.split(r"(?<=[.!?])\s+", sample) if s.strip()]
    words = sample.split()
    notes = []
    if sents:
        avg = len(words) / len(sents)
        notes.append("short, punchy sentences" if avg < 11 else "medium-length sentences" if avg < 20 else "long, explanatory sentences")
    low = " " + sample.lower() + " "
    you, we = len(re.findall(r"\byou(r)?\b", low)), len(re.findall(r"\b(we|our)\b", low))
    if you > we:
        notes.append("talks to the reader (you)")
    elif we > you:
        notes.append("speaks as a team (we)")
    notes.append("no exclamation marks" if "!" not in sample else "uses exclamation marks")
    if re.search(r"\b(ai|agents?|workflow|platform|teams?)\b", low):
        notes.append("product-led, plain terms")
    return {"sample": sample, "notes": notes}


# ================================================================================================= logo candidates
def svg_candidates(info: dict) -> list[dict]:
    """Inline SVGs that are the brand's logo, best first."""
    p, name = info["page"], info["name"].lower()
    out = []
    for s in p.svgs:
        hint = s["hint"]
        score = 0.0
        if name and name in hint:
            score += 3
        if "logo" in hint or "wordmark" in hint or "brand" in hint:
            score += 2.5
        if s["home"]:
            score += 2
        if s["in_header"]:
            score += 1
        score -= s["order"] * 0.02
        if any(w in hint for w in ("customer", "partner", "client", "trusted", "integration", "icon-", "arrow", "chevron", "menu", "close")):
            score -= 3
        if score >= 3:
            out.append({**s, "score": score})
    out.sort(key=lambda s: -s["score"])
    # a generic "logo" hint (a customer's logo in a trusted-by strip) never wins over the one that names the brand or goes home
    best = out[0]["score"] if out else 0
    return [s for s in out if s["score"] >= max(3.0, best - 2.5)]


def raster_candidates(url: str, info: dict) -> list[dict]:
    p = info["page"]
    icons = []
    for ic in p.icons:
        href = urljoin(url, ic["href"])
        size = 0
        m = re.match(r"(\d+)x(\d+)", ic["sizes"] or "")
        if m:
            size = int(m.group(1))
        is_svg = href.lower().split("?")[0].endswith(".svg") or "svg" in ic["type"]
        kind = "touch" if "apple-touch" in ic["rel"] else ("mask" if "mask" in ic["rel"] else "icon")
        rank = {"touch": 0, "icon": 1, "mask": 2}[kind] - (0.5 if is_svg and kind == "icon" else 0) - size / 1000
        icons.append({"url": href, "svg": is_svg, "kind": kind, "size": size or (180 if kind == "touch" else 0), "rank": rank})
    name = info["name"].lower()
    for im in p.logo_imgs:
        if not ((im["home"] or im["in_header"]) or (name and name in im["hint"])):
            continue  # a logo image elsewhere on the page is a customer's or a partner's, not this brand's
        icons.append({"url": urljoin(url, im["src"]), "svg": im["src"].lower().split("?")[0].endswith(".svg"), "kind": "img",
                      "size": 0, "rank": -1 if (im["home"] or im["in_header"]) else 1.5})
    if not any(i["kind"] == "touch" for i in icons):
        icons.append({"url": urljoin(url, "/apple-touch-icon.png"), "svg": False, "kind": "touch-guess", "size": 180, "rank": 0.8})
    icons.append({"url": urljoin(url, "/favicon.ico"), "svg": False, "kind": "favicon", "size": 32, "rank": 3})
    if info["og_image"]:
        icons.append({"url": info["og_image"], "svg": False, "kind": "og", "size": 1200, "rank": 9})
    icons.sort(key=lambda i: i["rank"])
    seen, out = set(), []
    for i in icons:
        if i["url"] not in seen:
            seen.add(i["url"])
            out.append(i)
    return out[:8]


def logo_palette(img) -> list[tuple]:
    """Saturated colours in a logo image (RGBA), most frequent first."""
    from PIL import Image
    im = img.convert("RGBA")
    im.thumbnail((96, 96))
    px = [p[:3] for p in im.getdata() if p[3] > 200 and sat(p[:3]) > 0.25]
    if not px:
        return []
    q = Image.new("RGB", (len(px), 1))
    q.putdata(px)
    pal = q.quantize(colors=4).convert("RGB")
    return [rgb for _, rgb in sorted(pal.getcolors(len(px)) or [], reverse=True)][:3]


def lockup(mark, name: str, font_id: str, ink) -> "object":
    """A square mark + the brand name in the headline face, as one wide logo (transparent PNG)."""
    from PIL import Image, ImageDraw, ImageFont
    from .templates import FONT_DIR, HEAD_FONTS
    if not name:
        return mark
    h = 200
    m = mark.copy()
    m.thumbnail((h, h), Image.LANCZOS)
    f = ImageFont.truetype(str(FONT_DIR / HEAD_FONTS.get(font_id, "Inter-Bold.ttf")), int(h * 0.62))
    d0 = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bb = d0.textbbox((0, 0), name, font=f)
    gap = int(h * 0.22)
    W = m.width + gap + (bb[2] - bb[0]) + 8
    out = Image.new("RGBA", (W, h), (0, 0, 0, 0))
    out.paste(m, (0, (h - m.height) // 2), m)
    ImageDraw.Draw(out).text((m.width + gap - bb[0], (h - (bb[3] - bb[1])) // 2 - bb[1]), name, font=f, fill=tuple(ink) + (255,))
    return out


def _to_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def open_raster(data: bytes):
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        if getattr(im, "n_frames", 1) > 1 or im.format == "ICO":
            try:
                im.size = max(im.info.get("sizes") or [im.size])  # the biggest frame of a .ico
            except Exception:
                pass
        im = im.convert("RGBA")
        return im if im.width >= 16 and im.height >= 16 else None
    except Exception:
        return None


# ================================================================================================= the whole kit
def extract(url: str, html: str, css_texts: list[str] | None = None, fetch=None) -> dict:
    """A brand kit from a homepage (and its stylesheets). fetch(url, limit) -> bytes|None is used for logo files; without
    it only inline SVG logos are drawn. Returns {"kit", "logo_png", "mark_png", "provenance", "found"}."""
    info = page_info(url, html)
    p = info["page"]
    css = "\n".join(relevant_css(css_texts or [])) + "\n" + "\n".join(p.styles)
    css = css[: MAX_CSS_BYTES * 3]
    vars_ = css_vars(css)
    prov = {"name": info["name_src"], "handle": info["handle_src"]}
    pal = pick_palette(info["theme_color"], vars_, css)
    ink = tuple(int(pal["colors"]["text"][i:i + 2], 16) for i in (1, 3, 5))
    # logo: an inline header SVG first, then the icon files
    logo = mark = None
    logo_src = mark_src = ""
    for cand in svg_candidates(info)[:6]:
        if svgraster.css_filled(cand["svg"]):
            continue
        im = svgraster.rasterize(cand["svg"], height=200, current=ink)
        if im is None or im.width < 24:
            continue
        if im.width / im.height >= 1.8 and logo is None:
            logo, logo_src = im, f"inline SVG in the page header ({(cand['hint'].strip() or 'logo')[:40]})"
        elif im.width / im.height < 1.8 and mark is None:
            mark, mark_src = im, "inline SVG mark in the page header"
        if logo is not None and mark is not None:
            break
    if fetch is not None:
        for c in raster_candidates(url, info):
            if mark is not None and (logo is not None or c["kind"] == "og"):
                break
            data = fetch(c["url"], 2_000_000)
            if not data:
                continue
            if c["svg"] or data.lstrip()[:5] in (b"<svg ", b"<?xml") or b"<svg" in data[:300]:
                im = svgraster.rasterize(data.decode("utf-8", "replace"), height=200, current=ink)
            else:
                im = open_raster(data)
            if im is None:
                continue
            if c["kind"] == "og":
                if logo is None and mark is None:
                    logo, logo_src = im, "og:image (no logo file found)"
                break
            if mark is None and im.width / im.height < 1.8:
                mark, mark_src = im, f"{c['kind']} icon {c['url'].split('?')[0].rsplit('/', 1)[-1]}"
            elif logo is None and im.width / im.height >= 1.8:
                logo, logo_src = im, f"logo image {c['url'].split('?')[0].rsplit('/', 1)[-1]}"
    fonts = pick_fonts(vars_, css)
    if logo is None and mark is not None:  # only a square mark: set the name beside it, the way a lockup does
        logo, logo_src = lockup(mark, info["name"], fonts["head"]["bundled"], ink), mark_src + " + the name set in the headline font"
    lp = logo_palette(logo) + (logo_palette(mark) if mark is not None and mark is not logo else []) if logo is not None else []
    if lp and pal["provenance"].get("accent", "").startswith(("monochrome", "most used")):
        pal = pick_palette(info["theme_color"], vars_, css, lp)
    prov.update(pal["provenance"])
    prov.update(fonts["provenance"])
    prov["logo"] = logo_src or "none found (the wordmark is set in the headline font)"
    prov["mark"] = mark_src
    voice = voice_from(p.h1, info["description"], p.paras)
    prov["voice"] = "the homepage's h1, description and first paragraphs" if voice["sample"] else ""
    head = fonts["head"]["bundled"]
    kit = {"name": info["name"], "handle": info["handle"], "website": url, "colors": pal["colors"],
           "font": head, "body_font": fonts["body"]["bundled"], "headline_case": "upper" if head == "anton" else "title",
           "voice": voice["sample"][:300], "voice_notes": voice["notes"],
           "front_card": {"style": "glow" if pal["dark"] else "minimal"}, "end_card": {"style": "glow" if pal["dark"] else "minimal",
                                                                                    "cta": f"Follow {info['name']}"[:28], "line": ""}}
    return {"kit": kit, "logo_png": _to_png(logo) if logo is not None else None, "mark_png": _to_png(mark) if mark is not None else None,
            "provenance": prov, "fonts": {"head": fonts["head"], "body": fonts["body"]}, "dark": pal["dark"],
            "found": {"logo": logo is not None, "mark": mark is not None, "css_vars": len(vars_), "stylesheets": len(css_texts or []),
                      "handle": bool(info["handle"]), "voice": bool(voice["sample"])}}


def relevant_css(texts: list[str]) -> list[str]:
    """The stylesheets that carry the design tokens (custom properties, font families), most relevant first."""
    def score(t: str) -> float:
        return t.count("--color") * 3 + t.count("brand") * 2 + t.count("font-family") + t.count(":root") * 5 + t.count("--") * 0.05
    ranked = sorted((t for t in texts if t), key=score, reverse=True)
    out, total = [], 0
    for t in ranked[:MAX_CSS_FILES]:
        if total + len(t) > MAX_CSS_BYTES * 3:
            break
        out.append(t)
        total += len(t)
    return out


def stylesheet_urls(url: str, html: str) -> list[str]:
    p = _Page()
    try:
        p.feed(html[:MAX_HTML_BYTES])
    except Exception:
        pass
    out = []
    for h in p.css_links:
        u = urljoin(url, h)
        if u.startswith(("http://", "https://")) and u not in out:
            out.append(u)
    # the site's own stylesheets first, then fonts.googleapis (family names) and other CDNs
    out.sort(key=lambda u: (0 if (urlparse(u).hostname or "").endswith((urlparse(url).hostname or "").removeprefix("www.")) else 1))
    return out[:MAX_CSS_FILES * 2]
