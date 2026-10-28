"""Link in bio v2: a self-hosted Linktree / Start Page alternative.

* Blocks: link, brand button (93 LittleLink brands with icons), image, YouTube / Spotify embed, spacer / divider,
  email signup, contact card (vCard download), latest posts grid, social icons, heading, text.
* Themes: auto (follows the visitor's light/dark setting), light, dark, plus the HuhByte lavender / night / lilac
  presets; accent colour, button shape and button style (solid / outline / soft).
* Every clickable block goes through a tracked `/l/{code}` short link, so analytics show clicks per block, per day,
  per referrer and per device; email signups are counted as conversions.
* QR code of the public URL (segno, BSD), JSON export / import, SEO + share image, an optional share button and an
  optional custom domain that serves the page at `/`.

Brand button colours (static/bio/brands.css) and icons (static/bio/icons/*.svg) are copied from LittleLink
(https://github.com/sethcottle/littlelink, MIT, see static/bio/LICENSE-littlelink.md and NOTICE). The logos remain
trademarks of their owners. The page is server-rendered with no JavaScript, and the CSS and icons for the brands a
page uses are inlined, so it also renders on the edge app (which serves no /static).
"""
from __future__ import annotations

import base64
import csv
import functools
import html
import io
import json
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from sqlalchemy import func, select

from ..models import BioPage, BioSubscriber, Channel, Link, LinkClick, Media, PostTarget
from .growth import FULL_URL, HEX, SLUG, GrowthError, base_url, iso, new_code, utcnow

STATIC = Path(__file__).resolve().parents[1] / "static" / "bio"
BLOCK_TYPES = ("link", "brand", "image", "embed", "spacer", "email", "contact", "posts", "socials", "text", "heading")
TRACKED = ("link", "brand", "image", "contact")        # blocks whose clicks go through a /l/ short link
SOCIAL_URLS = {"instagram": "https://instagram.com/{u}", "x": "https://x.com/{u}", "tiktok": "https://www.tiktok.com/@{u}",
               "youtube": "https://www.youtube.com/@{u}", "threads": "https://www.threads.net/@{u}",
               "linkedin": "https://www.linkedin.com/in/{u}", "facebook": "https://www.facebook.com/{u}"}
SOCIAL_ICON = {"instagram": "instagram", "x": "x", "tiktok": "tiktok", "youtube": "youtube", "threads": "threads",
               "linkedin": "linkedin", "facebook": "facebook", "website": "generic-website", "github": "github",
               "bluesky": "bluesky", "mastodon": "mastodon", "pinterest": "pinterest", "snapchat": "snapchat",
               "twitch": "twitch", "discord": "discord", "spotify": "spotify", "email": "generic-email"}
_V = ("bg", "card", "ink", "muted", "accent", "button", "button_ink", "border")
# Each preset: colours + the font, button shape and button style the gallery applies when it is picked.
# tests/test_bio_v2.py checks every preset keeps text and button text at WCAG AA (4.5:1) on every background stop.
THEMES = {
    "auto": {"label": "Auto (light / dark)", "font": "inter", "buttons": "pill", "style": "solid"},
    "light": {"label": "Light", "bg": "#FFFFFF", "card": "#F4F4F5", "ink": "#121212", "muted": "#52525B", "accent": "#2457F5",
              "button": "#FFFFFF", "button_ink": "#121212", "border": "#D4D4D8", "font": "inter", "buttons": "pill", "style": "shadow"},
    "dark": {"label": "Dark", "bg": "#121212", "card": "#1E1E1E", "ink": "#FFFFFF", "muted": "#A1A1AA", "accent": "#4899F7",
             "button": "#1E1E1E", "button_ink": "#FFFFFF", "border": "#3F3F46", "font": "inter", "buttons": "pill", "style": "solid"},
    "lavender": {"label": "Lavender", "bg": "linear-gradient(180deg,#F6F1FF 0%,#EDE3FF 55%,#E2D3FF 100%)", "card": "#FFFFFF", "ink": "#1C1530",
                 "muted": "#5B5275", "accent": "#7C5CE0", "button": "#FFFFFF", "button_ink": "#1C1530", "border": "#DCCBFB",
                 "font": "inter", "buttons": "pill", "style": "solid"},
    "night": {"label": "Night", "bg": "radial-gradient(circle at 30% 0%,#2A1F4A 0%,#120D22 70%)", "card": "#1B1433", "ink": "#F6F1FF",
              "muted": "#B7A9DA", "accent": "#C9ACFA", "button": "#251B45", "button_ink": "#F6F1FF", "border": "#3A2C66",
              "font": "inter", "buttons": "pill", "style": "solid"},
    "lilac": {"label": "Lilac", "bg": "#C9ACFA", "card": "#F6F1FF", "ink": "#1C1530", "muted": "#3F3560", "accent": "#5B3CC4",
              "button": "#1C1530", "button_ink": "#F6F1FF", "border": "#1C1530", "font": "poppins", "buttons": "pill", "style": "solid"},
    "mint": {"label": "Mint", "bg": "#E3F6EC", "card": "#FFFFFF", "ink": "#0F2E22", "muted": "#2F5546", "accent": "#0F7B55",
             "button": "#0F2E22", "button_ink": "#E3F6EC", "border": "#0F2E22", "font": "dm-sans", "buttons": "rounded", "style": "solid"},
    "ocean": {"label": "Ocean", "bg": "linear-gradient(160deg,#000428 0%,#004E92 100%)", "card": "#0B2A55", "ink": "#FFFFFF",
              "muted": "#C7DAF2", "accent": "#5CC8FF", "button": "#FFFFFF", "button_ink": "#000428", "border": "#FFFFFF",
              "font": "outfit", "buttons": "pill", "style": "shadow"},
    "sunset": {"label": "Sunset", "bg": "linear-gradient(160deg,#FFE259 0%,#FFA751 100%)", "card": "#FFF6E0", "ink": "#2B1300",
               "muted": "#5A3510", "accent": "#B4410B", "button": "#2B1300", "button_ink": "#FFF1D6", "border": "#2B1300",
               "font": "poppins", "buttons": "pill", "style": "solid"},
    "peach": {"label": "Peach", "bg": "linear-gradient(160deg,#F7BB97 0%,#F5A3B8 100%)", "card": "#FFF1EC", "ink": "#2A1020",
              "muted": "#4F2638", "accent": "#A3214F", "button": "#FFF1EC", "button_ink": "#2A1020", "border": "#2A1020",
              "font": "outfit", "buttons": "rounded", "style": "shadow"},
    "forest": {"label": "Forest", "bg": "#14301F", "card": "#1D4029", "ink": "#EAF5DD", "muted": "#B9D3A6", "accent": "#A8E063",
               "button": "#A8E063", "button_ink": "#10260F", "border": "#A8E063", "font": "dm-sans", "buttons": "rounded", "style": "solid"},
    "brutal": {"label": "Brutal", "bg": "#FFE14D", "card": "#FFFFFF", "ink": "#111111", "muted": "#333333", "accent": "#111111",
               "button": "#FFFFFF", "button_ink": "#111111", "border": "#111111", "font": "space-grotesk", "buttons": "square", "style": "hard"},
    "editorial": {"label": "Editorial", "bg": "#FBF7F0", "card": "#FFFFFF", "ink": "#2B2118", "muted": "#5C4D3E", "accent": "#8A3B12",
                  "button": "#FBF7F0", "button_ink": "#2B2118", "border": "#2B2118", "font": "playfair-display", "buttons": "square", "style": "outline"},
    "terminal": {"label": "Terminal", "bg": "#0B0F0C", "card": "#111A13", "ink": "#7CFFB2", "muted": "#7FBF97", "accent": "#7CFFB2",
                 "button": "#0B0F0C", "button_ink": "#7CFFB2", "border": "#7CFFB2", "font": "jetbrains-mono", "buttons": "square", "style": "outline"},
    "notebook": {"label": "Notebook", "bg": "#FFF8E7", "card": "#FFFFFF", "ink": "#2D2A26", "muted": "#57524A", "accent": "#C2410C",
                 "button": "#2D2A26", "button_ink": "#FFF8E7", "border": "#2D2A26", "font": "caveat", "buttons": "rounded", "style": "solid"},
    "poster": {"label": "Poster", "bg": "#FF5A1F", "card": "#FFE8DC", "ink": "#140A05", "muted": "#3B1A0B", "accent": "#140A05",
               "button": "#140A05", "button_ink": "#FFFFFF", "border": "#140A05", "font": "archivo-black", "buttons": "pill", "style": "solid"},
}
# Self-hosted Google Fonts (SIL OFL 1.1), woff2 latin 400/700 from @fontsource 5.3.0 (MIT packaging), see NOTICE.
FONTS = {
    "inter": ("Inter", "sans-serif", (400, 700)), "poppins": ("Poppins", "sans-serif", (400, 700)),
    "dm-sans": ("DM Sans", "sans-serif", (400, 700)), "outfit": ("Outfit", "sans-serif", (400, 700)),
    "space-grotesk": ("Space Grotesk", "sans-serif", (400, 700)), "playfair-display": ("Playfair Display", "serif", (400, 700)),
    "lora": ("Lora", "serif", (400, 700)), "jetbrains-mono": ("JetBrains Mono", "monospace", (400, 700)),
    "caveat": ("Caveat", "cursive", (400, 700)), "archivo-black": ("Archivo Black", "sans-serif", (400,)),
}
# Background gradients picked from uiGradients (ghosh/uiGradients @ afb0184, MIT), see NOTICE.
GRADIENTS = {
    "purple-bliss": ("Purple Bliss", ("#360033", "#0B8793")), "kashmir": ("Kashmir", ("#614385", "#516395")),
    "moonlit-asteroid": ("Moonlit Asteroid", ("#0F2027", "#203A43", "#2C5364")), "royal": ("Royal", ("#141E30", "#243B55")),
    "frost": ("Frost", ("#000428", "#004E92")), "sea-blue": ("Sea Blue", ("#2B5876", "#4E4376")),
    "celestial": ("Celestial", ("#C33764", "#1D2671")), "cosmic-fusion": ("Cosmic Fusion", ("#FF00CC", "#333399")),
    "sublime-light": ("Sublime Light", ("#FC5C7D", "#6A82FB")), "ibiza-sunset": ("Ibiza Sunset", ("#EE0979", "#FF6A00")),
    "mango": ("Mango", ("#FFE259", "#FFA751")), "lush": ("Lush", ("#56AB2F", "#A8E063")),
}
DEFAULT_THEME = {"preset": "lavender", "accent": "#7C5CE0", "buttons": "pill", "style": "solid", "font": "",
                 "bg_type": "", "bg_color": "", "bg_gradient": "", "bg_image": "",
                 "text_color": "", "button_color": "", "button_text_color": ""}
BUTTONS = ("pill", "rounded", "square")
STYLES = ("solid", "outline", "soft", "shadow", "hard")
BG_TYPES = ("", "solid", "gradient", "image")
COLOR_KEYS = ("bg_color", "text_color", "button_color", "button_text_color")
LAYOUTS = ("classic", "featured")
SETTINGS_KEYS = {"seo_title": 80, "seo_description": 200, "og_image": 1000, "socials_position": 6}
SETTINGS_BOOLS = ("share_button", "hide_branding", "stats_public")
HOST = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
EMAIL = re.compile(r"^[^@\s<>\"']{1,64}@[^@\s<>\"']+\.[A-Za-z]{2,}$")
PHONE = re.compile(r"^[+0-9 ().\-]{5,30}$")
YT = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
SPOTIFY = re.compile(r"^https?://open\.spotify\.com/(?:intl-[a-z]{2}/)?(track|album|playlist|episode|show|artist)/([A-Za-z0-9]{10,40})")
SUBSCRIBE_LIMIT = (8, 600)                           # 8 signups per IP per 10 minutes
_hits: dict[str, deque] = defaultdict(deque)


# ------------------------------------------------------------------------------------------------ brand catalogue
@functools.lru_cache(maxsize=1)
def _brands() -> dict:
    data = json.loads((STATIC / "brands.json").read_text(encoding="utf-8"))
    return {b["key"]: b for b in data["brands"]}


@functools.lru_cache(maxsize=1)
def _brand_css() -> dict:
    """class key -> CSS rule text, from LittleLink's brands.css."""
    css = (STATIC / "brands.css").read_text(encoding="utf-8")
    out = {}
    for m in re.finditer(r"(\.button(?:\.button-default|-[\w-]+))\s*\{([^}]*)\}", css):
        key = m.group(1).split("button-")[-1]
        out[key] = m.group(2).strip()
    return out


@functools.lru_cache(maxsize=256)
def icon_data_uri(name: str) -> str:
    p = STATIC / "icons" / f"{re.sub(r'[^a-z0-9-]', '', name)}.svg"
    if not p.exists():
        return ""
    return "data:image/svg+xml;base64," + base64.b64encode(p.read_bytes()).decode()


BRAND_ALIASES = {"youtube": "yt", "youtube-alt": "yt-alt", "youtube-music": "yt-music", "facebook": "faceb", "linkedin": "linked",
                 "tumblr": "tumb", "buy-me-a-coffee": "coffee", "google-play": "playstore", "app-store": "appstore",
                 "apple-app-store": "appstore", "cashapp": "cash-app", "twitter": "x", "website": "default", "littlelink": "default"}


def brand_key(raw: str) -> str:
    k = (raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    return BRAND_ALIASES.get(k, k)


def brands_catalog() -> list[dict]:
    css = _brand_css()
    out = []
    for k, b in _brands().items():
        vars_ = dict(re.findall(r"--button-(text|background|border)\s*:\s*([^;]+);", css.get(k, "")))
        grad = re.search(r"background-image\s*:\s*([^;]+);", css.get(k, ""))
        out.append({"key": k, "aliases": sorted(a for a, t in BRAND_ALIASES.items() if t == k), "label": b["label"], "icon": f"/static/bio/icons/{b['icon']}.svg",
                    "text": vars_.get("text", "#000000").strip(), "background": (grad.group(1) if grad else vars_.get("background", "#FFFFFF")).strip(),
                    "border": vars_.get("border", "").strip()})
    return sorted(out, key=lambda r: (r["key"] != "default", r["label"].lower()))


# ------------------------------------------------------------------------------------------------ themes / colour
def _lum(hex_: str) -> float:
    """WCAG relative luminance of #RRGGBB."""
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a: str, b: str) -> float:
    x, y = sorted((_lum(a), _lum(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


def hexes(css: str) -> list[str]:
    return re.findall(r"#[0-9A-Fa-f]{6}\b", css or "")


def ink_for(colors) -> str:
    """Near-black or white, whichever reads better on every colour given."""
    colors = list(colors) or ["#FFFFFF"]
    dark, light = "#121212", "#FFFFFF"
    return dark if min(contrast(dark, c) for c in colors) >= min(contrast(light, c) for c in colors) else light


def gradient_css(key: str) -> str:
    return f"linear-gradient(160deg,{','.join(GRADIENTS[key][1])})"


def themes_catalog() -> dict:
    """Everything the Design tab draws: presets (with their font / button defaults), fonts, gradients, styles."""
    presets = []
    for k, t in THEMES.items():
        presets.append({"key": k, **({kk: t[kk] for kk in ("label", "font", "buttons", "style")}),
                        **({kk: t[kk] for kk in _V} if "bg" in t else {**{kk: THEMES["light"][kk] for kk in _V},
                                                                       "bg": "linear-gradient(90deg,#FFFFFF 50%,#121212 50%)"})})
    return {"presets": presets, "buttons": list(BUTTONS), "styles": list(STYLES), "layouts": list(LAYOUTS),
            "fonts": [{"key": k, "label": f[0], "family": f"'{f[0]}',{f[1]}", "weights": list(f[2]),
                       "files": [f"/bio/fonts/{k}-{w}.woff2" for w in f[2]]} for k, f in FONTS.items()],
            "gradients": [{"key": k, "label": g[0], "colors": list(g[1]), "css": gradient_css(k)} for k, g in GRADIENTS.items()],
            "defaults": DEFAULT_THEME,
            "credit": "Fonts: Google Fonts (SIL OFL 1.1) via Fontsource. Gradients: uiGradients (MIT)."}


def font_face(key: str, base: str = "") -> str:
    name, _, weights = FONTS[key]
    return "".join(f"@font-face{{font-family:'{name}';font-style:normal;font-weight:{w};font-display:swap;"
                   f"src:url({base}/bio/fonts/{key}-{w}.woff2) format('woff2')}}" for w in weights)


# ------------------------------------------------------------------------------------------------ blocks
def _https(url: str, what: str, allow_local: bool = False) -> str:
    url = (url or "").strip()
    if allow_local and url.startswith("/") and not url.startswith("//"):
        return url[:1000]
    if not FULL_URL.match(url):
        raise GrowthError(f"{what}: enter a full link that starts with https://")
    return url[:2000]


def _opt_url(url, what: str) -> str:
    url = (url or "").strip()
    return "" if url in ("", "https://", "http://") else _https(url, what)


def _when(v) -> str:
    try:
        d = datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise GrowthError(f"Schedule: '{str(v)[:40]}' is not a date/time (use ISO 8601)") from e
    if d.tzinfo:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.isoformat(timespec="minutes") + "Z"


def is_live(b: dict, now=None) -> bool:
    """Not hidden, set up, and inside its schedule window."""
    if b.get("hidden") or (b.get("type") in ("link", "brand") and not b.get("url")):
        return False
    now = (now or utcnow()).isoformat(timespec="minutes") + "Z"
    return not ((b.get("start_at") and now < b["start_at"]) or (b.get("end_at") and now >= b["end_at"]))


def _socials(items, max_n=16) -> list[dict]:
    out = []
    for it in (items or [])[:max_n]:
        if not isinstance(it, dict):
            continue
        net, url = (it.get("network") or "").strip().lower(), (it.get("url") or "").strip()
        if net and url and url not in ("https://", "http://"):
            if net == "email" and EMAIL.match(url.removeprefix("mailto:")):
                url = "mailto:" + url.removeprefix("mailto:")
            elif not re.match(r"^https?://", url):
                raise GrowthError(f"Social link for {net} must start with https://")
            out.append({"network": net[:20], "url": url[:500]})
    return out


def _track(s, prev: dict, url: str, title: str) -> str:
    """Reuse the block's short link while its target is unchanged; a new target starts a new link (old stats stay)."""
    ln = s.get(Link, prev.get("link_id")) if prev.get("link_id") else None
    if ln and ln.url != url:
        ln = None
    if not ln:
        ln = Link(code=new_code(s), url=url, title=title[:120], source="bio")
        s.add(ln)
        s.flush()
    elif ln.title != title[:120]:
        ln.title = title[:120]
    return ln.id


def embed_info(url: str) -> dict:
    m = YT.match(url or "")
    if m:
        return {"provider": "youtube", "embed_url": f"https://www.youtube-nocookie.com/embed/{m.group(1)}", "height": 0}
    m = SPOTIFY.match(url or "")
    if m:
        kind = m.group(1)
        return {"provider": "spotify", "embed_url": f"https://open.spotify.com/embed/{kind}/{m.group(2)}",
                "height": 152 if kind in ("track", "episode") else 352}
    raise GrowthError("Embed: paste a YouTube video / Short link or an open.spotify.com track, album, playlist, episode or show link")


def _clean_blocks(core, s, page_id: str, blocks: list, old: list) -> list:
    out = []
    old_by_id = {b.get("id"): b for b in old or []}
    if not isinstance(blocks, list):
        raise GrowthError("blocks must be a list")
    for b in blocks[:80]:
        if not isinstance(b, dict):
            raise GrowthError("Each block must be an object")
        t = b.get("type")
        if t not in BLOCK_TYPES:
            raise GrowthError(f"Unknown block type '{t}'. Use one of: {', '.join(BLOCK_TYPES)}")
        bid = re.sub(r"[^A-Za-z0-9_-]", "", str(b.get("id") or "")) or "b" + secrets.token_hex(4)
        prev = old_by_id.get(bid) or {}
        nb = {"id": bid, "type": t, "hidden": bool(b.get("hidden"))}
        for k in ("start_at", "end_at"):                 # schedule: shown only between start_at and end_at (UTC)
            if b.get(k):
                nb[k] = _when(b[k])
        if nb.get("start_at") and nb.get("end_at") and nb["end_at"] <= nb["start_at"]:
            raise GrowthError("Schedule: the end must be after the start")
        if t in ("link", "brand"):
            if t == "brand":
                key = brand_key(b.get("brand"))
                if key not in _brands():
                    raise GrowthError(f"Unknown brand '{key}'. GET /api/bio/brands lists the {len(_brands())} brand buttons")
                nb["brand"] = key
            label = (b.get("label") or "").strip()[:120] or (_brands()[nb["brand"]]["label"] if t == "brand" else "")
            url = _opt_url(b.get("url"), f"'{label or 'Link'}'")      # an empty URL is a link still being set up (not shown)
            layout = b.get("layout") or "classic"
            if layout not in LAYOUTS:
                raise GrowthError(f"Layout must be one of {', '.join(LAYOUTS)}")
            thumb = _https(b["thumb"], "Thumbnail", allow_local=True) if (b.get("thumb") or "").strip() else ""
            nb.update({"label": label, "url": url, "highlight": bool(b.get("highlight")), "spotlight": bool(b.get("spotlight")),
                       "layout": layout, "thumb": thumb})
            if url:
                nb["link_id"] = _track(s, prev, url, label or url)
        elif t == "image":
            src = _https(b.get("src") or b.get("image_url"), "Image", allow_local=True)
            nb.update({"src": src, "alt": (b.get("alt") or "").strip()[:200], "caption": (b.get("caption") or "").strip()[:200]})
            if (b.get("url") or "").strip():
                url = _https(b.get("url"), "Image link")
                nb.update({"url": url, "link_id": _track(s, prev, url, nb["alt"] or nb["caption"] or "Image")})
        elif t == "embed":
            url = (b.get("url") or "").strip()
            nb.update({"url": url, **embed_info(url), "title": (b.get("title") or "").strip()[:120]})
        elif t == "spacer":
            size = b.get("size") or "m"
            if size not in ("s", "m", "l"):
                raise GrowthError("Spacer size must be s, m or l")
            nb.update({"size": size, "divider": bool(b.get("divider"))})
        elif t == "email":
            nb.update({"title": (b.get("title") or "Get the newsletter").strip()[:120],
                       "text": (b.get("text") or "").strip()[:300],
                       "button": (b.get("button") or "Subscribe").strip()[:40],
                       "placeholder": (b.get("placeholder") or "you@example.com").strip()[:60],
                       "success": (b.get("success") or "Thanks! You're on the list.").strip()[:200],
                       "ask_name": bool(b.get("ask_name"))})
        elif t == "contact":
            c = {k: (b.get(k) or "").strip() for k in ("name", "org", "job_title", "email", "phone", "website", "address", "note")}
            if not c["name"]:
                raise GrowthError("The contact card needs a name")
            if c["email"] and not EMAIL.match(c["email"]):
                raise GrowthError(f"Contact card: '{c['email'][:60]}' is not an email address")
            if c["phone"] and not PHONE.match(c["phone"]):
                raise GrowthError("Contact card: phone may contain digits, spaces, + ( ) . and -")
            if c["website"]:
                c["website"] = _https(c["website"], "Contact card website")
            nb.update({k: v[:200] for k, v in c.items()})
            nb["button"] = (b.get("button") or "Save my contact").strip()[:40]
            nb["link_id"] = _track(s, prev, f"/bio/vcard/{page_id}/{bid}.vcf", f"Contact card: {c['name']}")
        elif t == "posts":
            nb.update({"count": max(1, min(int(b.get("count") or 6), 24)), "title": (b.get("title") or "Latest posts")[:80],
                       "channel_ids": [str(x) for x in (b.get("channel_ids") or [])][:20]})
        elif t == "socials":
            items = _socials(b.get("items"))
            nb.update({"items": items, "auto": bool(b.get("auto", not items))})
        else:
            nb["text"] = (b.get("text") or "")[:1000 if t == "text" else 120]
        out.append(nb)
    return out


# ------------------------------------------------------------------------------------------------ pages
def page_url(core, p: BioPage, request=None) -> str:
    return f"https://{p.domain}/" if p.domain else f"{base_url(core, request)}/bio/{p.slug}"


def bio_dict(core, p: BioPage, request=None, s=None) -> dict:
    links = {}
    ids = [b.get("link_id") for b in p.blocks or [] if b.get("link_id")]
    if s is not None and ids:
        links = {ln.id: ln for ln in s.scalars(select(Link).where(Link.id.in_(ids)))}
    blocks = []
    for b in p.blocks or []:
        nb = dict(b)
        if b.get("link_id") in links:
            nb["clicks"] = links[b["link_id"]].clicks or 0
            nb["short_url"] = f"{base_url(core, request)}/l/{links[b['link_id']].code}"
        blocks.append(nb)
    subs = s.scalar(select(func.count()).select_from(BioSubscriber).where(BioSubscriber.page_id == p.id)) if s is not None else 0
    return {"id": p.id, "slug": p.slug, "title": p.title, "bio": p.bio, "avatar_url": p.avatar_url,
            "theme": {**DEFAULT_THEME, **(p.theme or {})}, "blocks": blocks, "published": p.published, "views": p.views or 0,
            "clicks": sum(b.get("clicks", 0) for b in blocks), "subscribers": subs or 0,
            "public_url": f"{base_url(core, request)}/bio/{p.slug}", "domain": p.domain or "",
            "domain_url": f"https://{p.domain}/" if p.domain else "", "settings": dict(p.settings or {}),
            "updated_at": iso(p.updated_at), "created_at": iso(p.created_at)}


DEFAULT_BLOCKS = [{"type": "heading", "text": "Daily AI news, made simple"},
                  {"type": "posts", "count": 6, "title": "Latest posts"},
                  {"type": "socials", "items": [], "auto": True}]


def list_bio(core, request=None) -> list[dict]:
    with core.db.session() as s:
        return [bio_dict(core, p, request, s) for p in s.scalars(select(BioPage).order_by(BioPage.created_at))]


def _bio(s, key: str) -> BioPage:
    p = s.get(BioPage, key) or s.scalar(select(BioPage).where(BioPage.slug == key))
    if not p:
        raise GrowthError("Bio page not found", 404)
    return p


def clean_domain(raw: str) -> str:
    d = (raw or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0].rstrip(".")
    if not d:
        return ""
    if not HOST.match(d) or d in ("localhost",):
        raise GrowthError("Custom domain: enter a host name like links.example.com (no https://, no path)")
    return d


def clean_theme(cur: dict, patch: dict) -> dict:
    th = {**DEFAULT_THEME, **(cur or {}), **{k: v for k, v in (patch or {}).items() if k in DEFAULT_THEME}}
    th = {k: (str(v).strip() if isinstance(v, str) else v) for k, v in th.items()}
    if th["preset"] not in THEMES:
        raise GrowthError(f"Theme must be one of {', '.join(THEMES)}")
    if not HEX.match(th["accent"] or ""):
        raise GrowthError("Accent colour must look like #7C5CE0")
    if th["buttons"] not in BUTTONS:
        raise GrowthError("Buttons must be pill, rounded or square")
    if th["style"] not in STYLES:
        raise GrowthError(f"Button style must be one of {', '.join(STYLES)}")
    if th["font"] and th["font"] not in FONTS:
        raise GrowthError(f"Font must be one of {', '.join(FONTS)}")
    if th["bg_type"] not in BG_TYPES:
        raise GrowthError("Background must be solid, gradient or image ('' follows the theme)")
    for k in COLOR_KEYS:
        if th[k] and not HEX.match(th[k]):
            raise GrowthError(f"{k.replace('_', ' ').capitalize()} must look like #7C5CE0")
        th[k] = th[k].upper()
    if th["bg_gradient"] and th["bg_gradient"] not in GRADIENTS:
        raise GrowthError(f"Gradient must be one of {', '.join(GRADIENTS)}")
    if th["bg_image"]:
        th["bg_image"] = _https(th["bg_image"], "Background image", allow_local=True)
    need = {"solid": "bg_color", "gradient": "bg_gradient", "image": "bg_image"}.get(th["bg_type"])
    if need and not th[need]:
        raise GrowthError(f"Pick a {need.replace('bg_', '')} for the {th['bg_type']} background")
    return th


def save_bio(core, payload: dict, pid: str | None = None, request=None) -> dict:
    with core.db.session() as s:
        p = _bio(s, pid) if pid else BioPage()
        _apply(core, s, p, payload, pid)
        return bio_dict(core, p, request, s)


def preview_bio(core, pid: str, draft: dict, highlight: str = "") -> str:
    """Render unsaved edits exactly like the public page; nothing is written (the session is rolled back)."""
    s = core.db.Session()
    try:
        p = _bio(s, pid)
        draft = {k: v for k, v in (draft or {}).items() if k in ("title", "bio", "avatar_url", "theme", "blocks", "settings")}
        _apply(core, s, p, draft, pid)
        return _render(core, s, p, preview=True, highlight=highlight)
    finally:
        s.rollback()
        s.close()


def _apply(core, s, p: BioPage, payload: dict, pid: str | None) -> None:
    """Validate and copy a create / update payload onto the page (shared by save and the live preview)."""
    if "slug" in payload or not pid:
        slug = (payload.get("slug") or "").strip().lower()
        if not SLUG.match(slug):
            raise GrowthError("Page address: 2-40 lowercase letters, numbers or dashes")
        other = s.scalar(select(BioPage).where(BioPage.slug == slug))
        if other and other.id != p.id:
            raise GrowthError(f"/bio/{slug} is taken", 409)
        p.slug = slug
    if not p.id:
        s.add(p)
        s.flush()
    for k, n in (("title", 80), ("bio", 500), ("avatar_url", 1000)):
        if k in payload:
            setattr(p, k, str(payload.get(k) or "")[:n])
    if not pid and not p.title:
        p.title = core.prefs().get("cta_handle") or "@huhbyte"
    if p.avatar_url and not re.match(r"^(https?://|/)", p.avatar_url):
        raise GrowthError("Avatar must be an https:// image URL")
    if "theme" in payload or not pid:
        p.theme = clean_theme(p.theme, payload.get("theme"))
    if "domain" in payload:
        d = clean_domain(payload.get("domain") or "")
        if d:
            other = s.scalar(select(BioPage).where(BioPage.domain == d))
            if other and other.id != p.id:
                raise GrowthError(f"{d} is already used by /bio/{other.slug}", 409)
        p.domain = d
    if "settings" in payload:
        st = dict(p.settings or {})
        for k, v in (payload.get("settings") or {}).items():
            if k in SETTINGS_KEYS:
                st[k] = str(v or "")[:SETTINGS_KEYS[k]]
            elif k in SETTINGS_BOOLS:
                st[k] = bool(v)
            elif k == "socials":                       # page-level social icons (top or bottom of the page)
                st[k] = _socials(v)
        if st.get("og_image") and not re.match(r"^(https?://|/)", st["og_image"]):
            raise GrowthError("Share image must be an https:// image URL")
        if st.get("socials_position", "top") not in ("top", "bottom"):
            raise GrowthError("Social icons position must be top or bottom")
        p.settings = st
    if "blocks" in payload:
        p.blocks = _clean_blocks(core, s, p.id, payload.get("blocks") or [], p.blocks or [])
    elif not pid:
        p.blocks = _clean_blocks(core, s, p.id, DEFAULT_BLOCKS, [])
    if "published" in payload:
        p.published = bool(payload["published"])
    s.flush()


def get_bio(core, pid: str, request=None, days: int = 30) -> dict:
    """Page + analytics: views, clicks and signups per day, CTR, clicks per block, referrers and devices."""
    with core.db.session() as s:
        p = _bio(s, pid)
        out = bio_dict(core, p, request, s)
        by_link = {b["link_id"]: b for b in p.blocks or [] if b.get("link_id")}
        since = utcnow() - timedelta(days=days)
        start = since.date()
        clicks: dict[str, int] = {}
        refs: dict[str, int] = {}
        devices: dict[str, int] = {}
        per_block: dict[str, int] = {}
        if by_link:
            q = select(LinkClick.at, LinkClick.referrer, LinkClick.device, LinkClick.link_id).where(
                LinkClick.link_id.in_(list(by_link)), LinkClick.at >= since)
            for at, ref, dev, lid in s.execute(q):
                day = at.date().isoformat()
                clicks[day] = clicks.get(day, 0) + 1
                host = (urlsplit(ref).netloc if ref else "") or "direct"
                refs[host] = refs.get(host, 0) + 1
                devices[dev or "unknown"] = devices.get(dev or "unknown", 0) + 1
                per_block[by_link[lid]["id"]] = per_block.get(by_link[lid]["id"], 0) + 1
        signups: dict[str, int] = {}
        for (at,) in s.execute(select(BioSubscriber.created_at).where(BioSubscriber.page_id == p.id, BioSubscriber.created_at >= since)):
            signups[at.date().isoformat()] = signups.get(at.date().isoformat(), 0) + 1
        dv = p.daily_views or {}
        daily = []
        for i in range(days + 1):
            day = (start + timedelta(days=i)).isoformat()
            daily.append({"day": day, "views": int(dv.get(day, 0)), "clicks": clicks.get(day, 0), "signups": signups.get(day, 0)})
        v, c = sum(r["views"] for r in daily), sum(r["clicks"] for r in daily)
        su = sum(r["signups"] for r in daily)
        label = {"link": "label", "brand": "label", "image": "alt", "contact": "name"}
        out["stats"] = {
            "days": days, "daily": daily, "views": v, "clicks": c, "signups": su,
            "ctr": round(100 * c / v, 1) if v else 0.0, "signup_rate": round(100 * su / v, 1) if v else 0.0,
            "blocks": sorted(({"id": b["id"], "type": b["type"], "label": b.get(label.get(b["type"], "label")) or b["type"],
                               "clicks": b.get("clicks", 0), "clicks_in_range": per_block.get(b["id"], 0)}
                              for b in out["blocks"] if b["type"] in TRACKED and b.get("link_id")), key=lambda r: -r["clicks"]),
            "referrers": sorted(({"referrer": k, "clicks": n} for k, n in refs.items()), key=lambda r: -r["clicks"])[:10],
            "devices": sorted(({"device": k, "clicks": n} for k, n in devices.items()), key=lambda r: -r["clicks"])}
        return out


def delete_bio(core, pid: str) -> dict:
    with core.db.session() as s:
        p = _bio(s, pid)
        n = 0
        for sub in s.scalars(select(BioSubscriber).where(BioSubscriber.page_id == p.id)):
            s.delete(sub)
            n += 1
        s.delete(p)
        return {"deleted": p.id, "subscribers_deleted": n}


# ------------------------------------------------------------------------------------------------ export / import
EXPORT_KEYS = ("slug", "title", "bio", "avatar_url", "theme", "blocks", "published", "domain", "settings")
DROP_BLOCK_KEYS = ("link_id", "clicks", "short_url")


def export_bio(core, pid: str) -> dict:
    with core.db.session() as s:
        p = _bio(s, pid)
        d = bio_dict(core, p, None, s)
    page = {k: d[k] for k in EXPORT_KEYS}
    page["blocks"] = [{k: v for k, v in b.items() if k not in DROP_BLOCK_KEYS} for b in d["blocks"]]
    return {"kind": "huhqueue.bio", "version": 2, "exported_at": iso(utcnow()), "page": page}


def import_bio(core, payload: dict, request=None) -> dict:
    """Create a page from an export (or a bare page object); `slug` in the payload overrides the file's slug."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    page = data.get("page") if isinstance(data.get("page"), dict) else data
    if not isinstance(page, dict) or not isinstance(page.get("blocks", []), list):
        raise GrowthError("Not a HuhQueue bio export (expected {kind:'huhqueue.bio', page:{...}})")
    body = {k: page[k] for k in EXPORT_KEYS if k in page and k != "domain"}   # a domain is never copied (it is unique)
    body["slug"] = (payload.get("slug") or page.get("slug") or "").strip().lower()
    body["published"] = bool(payload.get("published", False))
    for b in body.get("blocks") or []:
        b.pop("id", None)
    return save_bio(core, body, None, request)


# ------------------------------------------------------------------------------------------------ QR code
def qr(core, pid: str, request=None, fmt: str = "json", scale: int = 8, dark: str = "", light: str = "#FFFFFF"):
    """QR code of the page's public URL (custom domain when set). json -> {url, svg, png_data_uri}; svg -> str; png -> bytes."""
    import segno
    with core.db.session() as s:
        p = _bio(s, pid)
        url = page_url(core, p, request)
        accent = (p.theme or {}).get("accent") or DEFAULT_THEME["accent"]
    dark = dark if HEX.match(dark or "") else "#1C1530" if accent == DEFAULT_THEME["accent"] else accent
    light = light if HEX.match(light or "") else "#FFFFFF"
    scale = max(2, min(int(scale or 8), 40))
    code = segno.make(url, error="m")
    if fmt == "png":
        buf = io.BytesIO()
        code.save(buf, kind="png", scale=scale, dark=dark, light=light, border=2)
        return buf.getvalue()
    buf = io.BytesIO()                 # standalone SVG with xmlns, so it also works as a file and a data URI
    code.save(buf, kind="svg", scale=scale, dark=dark, light=light, border=2, xmldecl=False, svgclass=None, lineclass=None)
    svg = buf.getvalue().decode()
    if fmt == "svg":
        return svg
    buf = io.BytesIO()
    code.save(buf, kind="png", scale=scale, dark=dark, light=light, border=2)
    return {"url": url, "svg": svg, "png_data_uri": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(),
            "svg_data_uri": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode(),
            "dark": dark, "light": light}


# ------------------------------------------------------------------------------------------------ subscribers
def _sub_dict(x: BioSubscriber) -> dict:
    return {"id": x.id, "email": x.email, "name": x.name, "block_id": x.block_id, "referrer": x.referrer, "created_at": iso(x.created_at)}


def list_subscribers(core, pid: str, fmt: str = "json"):
    with core.db.session() as s:
        p = _bio(s, pid)
        rows = [_sub_dict(x) for x in s.scalars(select(BioSubscriber).where(BioSubscriber.page_id == p.id)
                                                .order_by(BioSubscriber.created_at.desc()))]
        slug = p.slug
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["email", "name", "signed_up_at", "source"])
        for r in rows:
            w.writerow([r["email"], r["name"], r["created_at"], r["referrer"]])
        return slug, buf.getvalue()
    return {"page_id": pid, "count": len(rows), "items": rows}


def delete_subscriber(core, pid: str, sid: str) -> dict:
    with core.db.session() as s:
        p = _bio(s, pid)
        x = s.get(BioSubscriber, sid)
        if not x or x.page_id != p.id:
            raise GrowthError("Subscriber not found", 404)
        s.delete(x)
        return {"deleted": sid}


def _rate_ok(ip: str) -> bool:
    n, window = SUBSCRIBE_LIMIT
    now = time.monotonic()
    q = _hits[ip or "?"]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= n:
        return False
    q.append(now)
    return True


def subscribe(core, slug: str, form: dict, ip: str = "", referrer: str = "") -> tuple[str, str]:
    """Public email signup. Returns (status, slug): ok | exists | invalid | limited | spam | missing."""
    if (form.get("website") or "").strip():              # honeypot field, hidden from people
        return "spam", slug
    if not _rate_ok(ip):
        return "limited", slug
    email = (form.get("email") or "").strip().lower()[:200]
    with core.db.session() as s:
        p = s.scalar(select(BioPage).where(BioPage.slug == slug))
        if not p or not p.published:
            return "missing", slug
        block = next((b for b in p.blocks or [] if b.get("type") == "email" and b.get("id") == form.get("block")), None) \
            or next((b for b in p.blocks or [] if b.get("type") == "email"), None)
        if not block:
            return "missing", slug
        if not EMAIL.match(email):
            return "invalid", slug
        if s.scalar(select(BioSubscriber).where(BioSubscriber.page_id == p.id, BioSubscriber.email == email)):
            return "exists", slug
        s.add(BioSubscriber(page_id=p.id, block_id=block["id"], email=email, name=(form.get("name") or "").strip()[:120],
                            referrer=(urlsplit(referrer).netloc if referrer else "")[:200]))
        return "ok", slug


# ------------------------------------------------------------------------------------------------ vCard
def _vc(v: str) -> str:
    return (v or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def vcard(core, page_id: str, block_id: str) -> tuple[str, str] | None:
    with core.db.session() as s:
        p = s.get(BioPage, page_id)
        if not p or not p.published:
            return None
        b = next((x for x in p.blocks or [] if x.get("id") == block_id and x.get("type") == "contact" and not x.get("hidden")), None)
        if not b:
            return None
    parts = (b.get("name") or "").split(" ", 1)
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{_vc(b['name'])}",
             f"N:{_vc(parts[1] if len(parts) > 1 else '')};{_vc(parts[0])};;;"]
    for key, tag in (("org", "ORG"), ("job_title", "TITLE"), ("note", "NOTE")):
        if b.get(key):
            lines.append(f"{tag}:{_vc(b[key])}")
    if b.get("email"):
        lines.append(f"EMAIL;TYPE=INTERNET:{_vc(b['email'])}")
    if b.get("phone"):
        lines.append(f"TEL;TYPE=CELL:{_vc(b['phone'])}")
    if b.get("website"):
        lines.append(f"URL:{_vc(b['website'])}")
    if b.get("address"):
        lines.append(f"ADR;TYPE=WORK:;;{_vc(b['address'])};;;;")
    lines.append("END:VCARD")
    fn = re.sub(r"[^A-Za-z0-9_-]+", "-", b["name"]).strip("-") or "contact"
    return f"{fn}.vcf", "\r\n".join(lines) + "\r\n"


# ------------------------------------------------------------------------------------------------ custom domain
def page_for_domain(core, host: str) -> str | None:
    host = (host or "").split(":")[0].strip().lower().rstrip(".")
    if not host or not HOST.match(host):
        return None
    with core.db.session() as s:
        p = s.scalar(select(BioPage).where(BioPage.domain == host))
        return p.slug if p and p.published else None


def domain_check(core, pid: str, request=None) -> dict:
    """What to point at HuhQueue for the custom domain, and whether it already resolves here."""
    import socket
    with core.db.session() as s:
        p = _bio(s, pid)
        domain = p.domain
    target = urlsplit(base_url(core, request)).hostname or ""
    out = {"domain": domain, "target": target, "resolves": False, "records": [],
           "steps": [f"Add a CNAME record: {domain or 'links.example.com'} -> {target or 'your HuhQueue host'} (or an A record to its IP).",
                     "Route the domain to HuhQueue's UI (or edge) port in your proxy (Caddy / Cloudflare Tunnel / nginx); HTTPS comes from the proxy.",
                     "Open the domain: HuhQueue serves this bio page at / for that host name."]}
    if not domain:
        return out
    try:
        mine = {a[4][0] for a in socket.getaddrinfo(target, 443)} if target else set()
        theirs = {a[4][0] for a in socket.getaddrinfo(domain, 443)}
        out["records"] = sorted(theirs)
        out["resolves"] = bool(mine and theirs & mine)
    except OSError:
        out["records"] = []
    return out


# ------------------------------------------------------------------------------------------------ rendering
def latest_posts(core, s, count: int, channel_ids: list) -> list[dict]:
    qry = select(PostTarget).where(PostTarget.status == "sent").order_by(PostTarget.published_at.desc())
    if channel_ids:
        qry = qry.where(PostTarget.channel_id.in_(channel_ids))
    out, seen = [], set()
    for t in s.scalars(qry.limit(count * 4)):
        if t.post_id in seen:          # one tile per story even when it went to several channels
            continue
        seen.add(t.post_id)
        thumb, kind = "", ""
        for mid in t.media_list or []:
            m = s.get(Media, mid)
            if m:
                thumb, kind = f"/m/{m.public_token}/{m.jpeg_name or m.stored_name}", m.kind
                break
        out.append({"caption": (t.caption or "").split("\n")[0][:90], "permalink": t.permalink or "", "thumb": thumb, "kind": kind,
                    "provider": t.channel.provider})
        if len(out) >= count:
            break
    return out


def _e(v) -> str:
    return html.escape(str(v or ""), quote=True)


def _vars(c: dict, accent: str | None) -> str:
    return ";".join(f"--{k.replace('_', '-')}:{(accent if k == 'accent' and accent else c[k])}" for k in _V)


FLASH = {"ok": None, "exists": "You're already on the list.", "invalid": "Please enter a valid email address.",
         "limited": "Too many signups from your network. Try again in a few minutes.", "spam": None, "missing": "Signups are closed."}


def _social_icon(net: str) -> str:
    uri = icon_data_uri(SOCIAL_ICON.get(net, ""))
    return (f'<img src="{uri}" alt="" aria-hidden="true">' if uri
            else _e("X" if net == "x" else net[:2].upper()))


def render_bio(core, slug: str, preview: bool = False, flash: str = "", base: str = "") -> str | None:
    """Server-rendered public page (no JavaScript, so it also works on the edge app)."""
    with core.db.session() as s:
        p = s.scalar(select(BioPage).where(BioPage.slug == slug))
        if not p or (not p.published and not preview):
            return None
        if not preview:
            p.views = (p.views or 0) + 1
            dv = dict(p.daily_views or {})
            today = utcnow().date().isoformat()
            dv[today] = dv.get(today, 0) + 1
            p.daily_views = dict(sorted(dv.items())[-400:])
        return _render(core, s, p, preview=preview, flash=flash, base=base)


def _theme_css(th: dict, base: str) -> tuple[str, str]:
    """(:root variables incl. custom background / colours, extra CSS for the button style + font)."""
    preset = th["preset"]
    custom_accent = th["accent"] if th["accent"] != DEFAULT_THEME["accent"] else None
    if preset == "auto":
        root = f":root{{{_vars(THEMES['light'], custom_accent)};color-scheme:light dark}}" \
               f"@media (prefers-color-scheme:dark){{:root{{{_vars(THEMES['dark'], custom_accent)}}}}}"
    else:
        dark = _lum(hexes(THEMES[preset]["bg"])[-1]) < 0.2
        root = f":root{{{_vars(THEMES[preset], custom_accent)};color-scheme:{'dark' if dark else 'light'}}}"
    over, bg_cols = {}, None
    if th["bg_type"] == "solid":
        over["bg"], bg_cols = th["bg_color"], [th["bg_color"]]
    elif th["bg_type"] == "gradient":
        over["bg"], bg_cols = gradient_css(th["bg_gradient"]), list(GRADIENTS[th["bg_gradient"]][1])
    elif th["bg_type"] == "image":
        src = quote(th["bg_image"], safe=":/?&=%#@+,.;~-_!$*")          # no quotes / parens can break out of url()
        over["bg"], bg_cols = f"linear-gradient(rgba(0,0,0,.42),rgba(0,0,0,.42)),url({src}) center/cover fixed", ["#2A2A2A"]
    ink = th["text_color"] or (ink_for(bg_cols) if bg_cols else "")
    if ink:
        over.update({"ink": ink, "muted": f"color-mix(in srgb,{ink} 74%,transparent)",
                     "card": f"color-mix(in srgb,{ink} 9%,transparent)", "border": f"color-mix(in srgb,{ink} 26%,transparent)"})
    if th["button_color"]:
        over.update({"button": th["button_color"], "button_ink": th["button_text_color"] or ink_for([th["button_color"]])})
    elif th["button_text_color"]:
        over["button_ink"] = th["button_text_color"]
    if over:
        root += ":root{" + ";".join(f"--{k.replace('_', '-')}:{v}" for k, v in over.items()) + "}"
    font = th["font"] or THEMES[preset].get("font") or "inter"
    name, generic, _ = FONTS[font]
    css = font_face(font, base) + f"body{{font-family:'{name}',{generic}}}"
    css += {"solid": "", "outline": ".btn{background:transparent;color:var(--ink);border-color:var(--ink);box-shadow:none}",
            "soft": ".btn{background:var(--card);color:var(--ink);border-color:transparent;box-shadow:none}",
            "shadow": ".btn{border-color:transparent;box-shadow:0 12px 28px -10px rgba(0,0,0,.35)}",
            "hard": ".btn{border:2px solid var(--ink);box-shadow:4px 4px 0 var(--ink)}.btn:hover{transform:translate(-2px,-2px);box-shadow:6px 6px 0 var(--ink)}",
            }[th["style"]]
    return root, css


def _render(core, s, p: BioPage, preview: bool = False, flash: str = "", base: str = "", highlight: str = "") -> str:
    d = bio_dict(core, p, None, s)
    st = d["settings"]
    th = d["theme"]
    root, theme_css = _theme_css(th, base)
    radius = {"pill": "999px", "rounded": "16px", "square": "6px"}[th["buttons"]]
    brand_css, parts = [], []
    action = f"{base}/bio/{_e(p.slug)}/subscribe"
    now = utcnow()
    for b in d["blocks"]:
        if not is_live(b, now):
            continue
        t = b["type"]
        n0 = len(parts)
        ln = s.get(Link, b.get("link_id")) if b.get("link_id") else None
        href = f"{base}/l/{ln.code}" if ln else b.get("url", "")
        if t == "heading":
            parts.append(f'<h2 class="h" data-block="heading">{_e(b["text"])}</h2>')
        elif t == "text":
            parts.append(f'<p class="t" data-block="text">{_e(b["text"])}</p>')
        elif t in ("link", "brand"):
            label = b.get("label") or urlsplit(b["url"]).netloc or b["url"]
            cls = "btn" + (" hi" if b.get("highlight") else "") + (" spot" if b.get("spotlight") else "")
            extra, icon = "", ""
            if t == "brand":
                key = b["brand"]
                rule = _brand_css().get(key)
                if rule:
                    brand_css.append(f".brand.button-{key}{{{rule}}}")
                uri = icon_data_uri(_brands()[key]["icon"])
                icon = f'<img class="icon" src="{uri}" alt="" aria-hidden="true">' if uri else ""
                cls += f" brand button-{_e(key)}"
                extra = f' data-brand="{_e(key)}"'
            if b.get("layout") == "featured" and b.get("thumb"):
                parts.append(f'<a class="{cls} feat" data-block="{t}"{extra} href="{_e(href)}" rel="noopener">'
                             f'<img src="{_e(b["thumb"])}" alt="" loading="lazy"><span>{icon}{_e(label)}</span></a>')
            else:
                th_img = f'<img class="th" src="{_e(b["thumb"])}" alt="" aria-hidden="true">' if b.get("thumb") else icon
                parts.append(f'<a class="{cls}" data-block="{t}"{extra} href="{_e(href)}" rel="noopener">{th_img}{_e(label)}</a>')
        elif t == "image":
            img = f'<img src="{_e(b["src"])}" alt="{_e(b.get("alt"))}" loading="lazy">'
            inner = f'<a href="{_e(href)}" rel="noopener">{img}</a>' if b.get("url") else img
            cap = f'<figcaption>{_e(b["caption"])}</figcaption>' if b.get("caption") else ""
            parts.append(f'<figure class="img" data-block="image">{inner}{cap}</figure>')
        elif t == "embed":
            title = b.get("title") or ("YouTube video" if b["provider"] == "youtube" else "Spotify player")
            if b["provider"] == "youtube":
                parts.append(f'<div class="embed yt" data-block="embed" data-provider="youtube"><iframe src="{_e(b["embed_url"])}" title="{_e(title)}" '
                             'loading="lazy" allow="accelerometer; encrypted-media; gyroscope; picture-in-picture; web-share" allowfullscreen '
                             'referrerpolicy="strict-origin-when-cross-origin"></iframe></div>')
            else:
                parts.append(f'<div class="embed sp" data-block="embed" data-provider="spotify"><iframe src="{_e(b["embed_url"])}" title="{_e(title)}" '
                             f'height="{int(b.get("height") or 152)}" loading="lazy" allow="autoplay; clipboard-write; encrypted-media; fullscreen; picture-in-picture"></iframe></div>')
        elif t == "spacer":
            h = {"s": 12, "m": 28, "l": 56}[b.get("size", "m")]
            parts.append(f'<div class="sp-{b.get("size", "m")}" data-block="spacer" style="height:{h}px">{"<hr>" if b.get("divider") else ""}</div>')
        elif t == "email":
            msg = ""
            if flash and FLASH.get(flash) is not None:
                msg = f'<p class="flash err" role="alert">{_e(FLASH[flash])}</p>'
            if flash in ("ok", "spam"):
                body = f'<p class="flash ok" role="status" data-testid="bio-subscribed">{_e(b.get("success"))}</p>'
            else:
                name = ('<input name="name" type="text" autocomplete="name" placeholder="Your name" aria-label="Your name" maxlength="120">'
                        if b.get("ask_name") else "")
                body = (f'<form method="post" action="{action}" class="sub">{msg}<input type="hidden" name="block" value="{_e(b["id"])}">'
                        '<input class="hp" name="website" tabindex="-1" autocomplete="off" aria-hidden="true">'
                        f'{name}<input name="email" type="email" required autocomplete="email" placeholder="{_e(b.get("placeholder"))}" aria-label="Email address">'
                        f'<button type="submit">{_e(b.get("button"))}</button></form>')
            text = f'<p class="t">{_e(b["text"])}</p>' if b.get("text") else ""
            parts.append(f'<section class="card" data-block="email" id="{_e(b["id"])}"><h3>{_e(b.get("title"))}</h3>{text}{body}</section>')
        elif t == "contact":
            sub = " · ".join(x for x in (b.get("job_title"), b.get("org")) if x)
            parts.append(f'<section class="card contact" data-block="contact"><h3>{_e(b["name"])}</h3>'
                         f'{f"<p class=t>{_e(sub)}</p>" if sub else ""}'
                         f'<a class="btn" href="{_e(href)}" download rel="nofollow">{_e(b.get("button"))}</a></section>')
        elif t == "posts":
            tiles = []
            for it in latest_posts(core, s, b.get("count", 6), b.get("channel_ids") or []):
                if it["thumb"] and it["kind"] == "video":
                    media = f'<video src="{_e(it["thumb"])}#t=0.5" muted playsinline preload="metadata"></video>'
                elif it["thumb"]:
                    media = f'<img src="{_e(it["thumb"])}" alt="{_e(it["caption"])}" loading="lazy">'
                else:
                    media = f'<span class="cap">{_e(it["caption"])}</span>'
                inner = f'{media}<span class="net">{_e(it["provider"])}</span>'
                tiles.append(f'<a class="tile" href="{_e(it["permalink"])}" rel="noopener" target="_blank">{inner}</a>' if it["permalink"]
                             else f'<div class="tile">{inner}</div>')
            if tiles:
                parts.append(f'<section data-block="posts"><h3 class="st">{_e(b.get("title"))}</h3><div class="grid">{"".join(tiles)}</div></section>')
        elif t == "socials":
            items = list(b.get("items") or [])
            if b.get("auto") and not items:
                for ch in s.scalars(select(Channel).where(Channel.status == "active")):
                    if ch.provider in SOCIAL_URLS and ch.username:
                        items.append({"network": ch.provider, "url": SOCIAL_URLS[ch.provider].format(u=ch.username.lstrip("@"))})
            if items:
                parts.append(_socials_nav(items, "socials"))
        if preview and len(parts) > n0:                  # the editor dims every block but the one being edited
            parts[-1] = f'<div data-id="{_e(b["id"])}">{parts[-1]}</div>'
    avatar = f'<img class="av" src="{_e(d["avatar_url"] or "/static/mark.png")}" alt="">'
    draft = '<div class="draft">Preview - this page is not published</div>' if preview and not p.published else ""
    bio = f'<p class="bio">{_e(d["bio"])}</p>' if d["bio"] else ""
    page_socials = _socials_nav(st.get("socials") or [], "page-socials") if st.get("socials") else ""
    top_socials = page_socials if st.get("socials_position", "top") == "top" else ""
    bottom_socials = page_socials if not top_socials else ""
    url = page_url(core, p)
    share = ""
    if st.get("share_button"):
        share = (f'<nav class="share" aria-label="Share this page"><a href="https://x.com/intent/post?url={quote(url)}" rel="noopener" target="_blank">Share on X</a>'
                 f'<a href="https://www.linkedin.com/sharing/share-offsite/?url={quote(url)}" rel="noopener" target="_blank">LinkedIn</a>'
                 f'<a href="https://wa.me/?text={quote(url)}" rel="noopener" target="_blank">WhatsApp</a>'
                 f'<a href="mailto:?subject={quote(d["title"])}&amp;body={quote(url)}">Email</a></nav>')
    footer = "" if st.get("hide_branding") else '<footer>Made with HuhQueue · <a href="https://huhbyte.ai">HuhByte AI</a></footer>'
    title = st.get("seo_title") or d["title"]
    desc = st.get("seo_description") or d["bio"][:160]
    og_img = st.get("og_image") or d["avatar_url"]
    og = f'<meta property="og:image" content="{_e(og_img)}">' if og_img else ""
    hl = re.sub(r"[^A-Za-z0-9_-]", "", highlight or "")
    hl_css = f'[data-id]{{transition:opacity .25s}}[data-id]:not([data-id="{hl}"]){{opacity:.28}}' if preview and hl else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)}</title>
<meta name="description" content="{_e(desc)}"><meta property="og:title" content="{_e(title)}"><meta property="og:description" content="{_e(desc)}">
<meta property="og:type" content="profile"><meta property="og:url" content="{_e(url)}">{og}<meta name="twitter:card" content="summary">
<link rel="canonical" href="{_e(url)}">
<style>
{root}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:var(--bg);background-attachment:fixed;color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
{theme_css}
main{{max-width:560px;margin:0 auto;padding:48px 20px 64px;text-align:center}}
:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.av{{width:96px;height:96px;border-radius:50%;object-fit:cover;border:3px solid var(--accent);background:var(--card);padding:6px}}
h1{{font-size:26px;margin:14px 0 4px;letter-spacing:-.02em}}.bio{{color:var(--muted);margin:0 0 24px;line-height:1.5;white-space:pre-line}}
.h{{font-size:15px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:28px 0 12px}}.t{{color:var(--muted);line-height:1.6;margin:6px 0}}
.btn{{position:relative;display:flex;align-items:center;justify-content:center;gap:10px;margin:12px 0;padding:15px 56px;min-height:56px;border-radius:{radius};background:var(--button);color:var(--button-ink);text-decoration:none;font-weight:600;
border:1.5px solid var(--border);box-shadow:0 4px 18px rgba(0,0,0,.06);transition:transform .15s,box-shadow .15s,filter .15s}}
.btn:hover{{transform:translateY(-2px);box-shadow:0 10px 28px rgba(0,0,0,.14)}}.btn.hi{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.btn .th{{position:absolute;left:8px;top:50%;transform:translateY(-50%);width:40px;height:40px;object-fit:cover;border-radius:min({radius},12px)}}
.btn.feat{{flex-direction:column;padding:0;gap:0;overflow:hidden;border-radius:min({radius},22px)}}.btn.feat img{{width:100%;aspect-ratio:16/9;object-fit:cover;display:block}}
.btn.feat span{{display:flex;align-items:center;gap:10px;padding:14px 18px}}
.spot{{animation:wiggle 3.2s ease-in-out infinite}}@keyframes wiggle{{0%,78%,100%{{transform:none}}82%{{transform:rotate(-2deg) scale(1.03)}}86%{{transform:rotate(2deg) scale(1.03)}}90%{{transform:rotate(-1deg)}}}}
.btn.brand{{background-color:var(--button-background,var(--button));color:var(--button-text,var(--button-ink));border:var(--button-border,1.5px solid transparent)}}
.btn.brand:hover{{filter:brightness(92%)}}.icon{{width:20px;height:20px;flex-shrink:0}}
.img{{margin:14px 0}}.img img{{width:100%;border-radius:18px;display:block;border:1px solid var(--border)}}.img figcaption{{font-size:13px;color:var(--muted);margin-top:6px}}
.embed{{margin:14px 0;border-radius:16px;overflow:hidden}}.embed.yt{{aspect-ratio:16/9}}.embed iframe{{width:100%;height:100%;border:0;display:block}}.embed.sp iframe{{height:auto}}
hr{{border:0;border-top:1px solid var(--border);margin:0;position:relative;top:50%}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:20px;padding:18px;margin:16px 0;text-align:left}}.card h3{{margin:0 0 4px;font-size:17px}}
.sub{{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}}.sub input{{flex:1 1 180px;min-height:46px;border-radius:12px;border:1.5px solid var(--border);padding:0 14px;font:inherit;background:var(--bg);color:var(--ink)}}
.sub button{{min-height:46px;padding:0 18px;border-radius:12px;border:0;background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}}
.hp{{position:absolute;left:-9999px;width:1px;height:1px;opacity:0}}.flash{{margin:8px 0 0;font-weight:600;width:100%}}.flash.err{{color:#DC2626}}.flash.ok{{color:var(--accent)}}
.contact .btn{{margin-bottom:0}}
.st{{text-align:left;font-size:13px;text-transform:uppercase;letter-spacing:.12em;color:var(--muted);margin:32px 0 10px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}}.tile{{position:relative;aspect-ratio:1;border-radius:14px;overflow:hidden;background:var(--card);
border:1px solid var(--border);display:flex;align-items:center;justify-content:center;color:var(--ink);text-decoration:none}}
.tile img,.tile video{{width:100%;height:100%;object-fit:cover}}.cap{{font-size:12px;padding:8px;line-height:1.3}}
.net{{position:absolute;left:6px;bottom:6px;font-size:10px;background:rgba(28,21,48,.7);color:#fff;border-radius:999px;padding:2px 7px}}
.socials{{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-top:28px}}.page-socials{{margin:0 0 24px}}.soc{{width:44px;height:44px;border-radius:50%;display:grid;place-items:center;
background:#18181B;border:1.5px solid color-mix(in srgb,var(--ink) 18%,transparent);color:#fff;font-weight:700;text-decoration:none;font-size:13px}}.soc img{{width:20px;height:20px}}
.share{{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-top:24px}}.share a{{font-size:13px;color:var(--muted);border:1px solid var(--border);border-radius:999px;padding:6px 12px;text-decoration:none}}
footer{{margin-top:40px;font-size:12px;color:var(--muted)}}footer a{{color:inherit}}
.draft{{background:#F59E0B;color:#1C1530;font-weight:600;padding:8px;text-align:center;font-size:13px}}
@media (prefers-reduced-motion:reduce){{.btn,.spot{{transition:none;animation:none}}.btn:hover{{transform:none}}}}
{hl_css}
{''.join(dict.fromkeys(brand_css))}
</style></head><body>{draft}<main data-bio="{_e(p.slug)}" data-theme="{_e(th['preset'])}">{avatar}<h1>{_e(d['title'])}</h1>{bio}{top_socials}
{''.join(parts)}{bottom_socials}{share}{footer}</main></body></html>"""


def _socials_nav(items: list, cls: str) -> str:
    chips = "".join(f'<a class="soc" href="{_e(i["url"])}" rel="noopener" target="_blank" aria-label="{_e(i["network"])}">'
                    f'{_social_icon(i["network"])}</a>' for i in items)
    extra = " socials" if cls != "socials" else ""
    return f'<nav class="{cls}{extra}" data-block="socials" aria-label="Social profiles">{chips}</nav>'


def parse_flash(query: str) -> str:
    v = (parse_qs(query or "").get("subscribed") or [""])[0]
    return v if v in FLASH else ""
