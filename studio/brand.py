"""HuhQueue: brand kits, the Style Ledger, and brand_check.

A brand kit is what a brand looks like (colors, fonts, logo, handle, end card). The Style Ledger is what the brand has
*learned*: every correction a person gives ("logo smaller", "never say game-changer", "max 2 hashtags") becomes a written
rule with a machine check. Renders read the visual rules; captions read the text rules; brand_check runs every rule
against a finished post and cites the exact rule when something fails.
"""
from __future__ import annotations

import colorsys
import io
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

FONTS = {"anton": "Anton.ttf", "poppins": "Poppins-Bold.ttf", "inter": "Inter-Bold.ttf", "figtree": "Figtree-ExtraBold.ttf",
         "serif": "DMSerifDisplay-Regular.ttf", "dot": "Doto-Black.ttf", "garamond": "EBGaramond-SemiBold.ttf",
         "sourceserif": "SourceSerif4-SemiBold.ttf", "roboto": "Roboto-Bold.ttf"}
BODY_FONTS = {"anton": "Inter-SemiBold.ttf", "poppins": "Poppins-Medium.ttf", "inter": "Inter-Regular.ttf", "figtree": "Figtree-Medium.ttf",
              "serif": "Inter-Regular.ttf", "dot": "Inter-Regular.ttf", "garamond": "SourceSerif4-Regular.ttf",
              "sourceserif": "SourceSerif4-Regular.ttf", "roboto": "Roboto-Regular.ttf"}
# What a kit looks like before the brand is set: neutral greys and Inter, so nothing of the HuhByte sample (black,
# lavender #C9ACFA, Anton) leaks into another brand's renders (owner, 2 Nov 2026). Set colours win everywhere.
NEUTRAL_ACCENT = "#E8E8E8"

DEFAULT_KIT = {
    "name": "", "handle": "", "website": "",
    "colors": {"primary": "#111114", "accent": NEUTRAL_ACCENT, "text": "#FFFFFF"},
    "font": "inter", "logo": "", "end_card": {"on": True, "line": "", "cta": "Follow"},
    "voice": "", "updated": 0,
}

# Visual defaults the ledger can override, so a correction changes every later render.
VISUAL_DEFAULTS = {"logo_position": "tr", "logo_scale": 0.11, "headline_case": "upper", "overlay": 0.55,
                   "headline_max_words": 14, "safe_margin": 64, "end_card": True, "source_credit": True}

EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF]")
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
HEX_RE = re.compile(r"#?([0-9a-fA-F]{6})\b")
POS = {"top left": "tl", "top-left": "tl", "top right": "tr", "top-right": "tr", "bottom left": "bl",
       "bottom-left": "bl", "bottom right": "br", "bottom-right": "br"}
POS_NAME = {"tl": "top left", "tr": "top right", "bl": "bottom left", "br": "bottom right"}


# ------------------------------------------------------------------ colors
def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = (h or "#000000").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return (0, 0, 0)


def rgb_to_hex(rgb) -> str:
    return "#%02X%02X%02X" % tuple(int(v) for v in rgb[:3])


def luminance(rgb) -> float:
    def ch(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def palette_from_image(data: bytes, n: int = 4) -> list[str]:
    """Dominant non-background colors of a logo, most saturated first."""
    from PIL import Image
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    im.thumbnail((128, 128))
    px = [p[:3] for p in im.getdata() if p[3] > 200]
    if not px:
        return []
    q = Image.new("RGB", (len(px), 1))
    q.putdata(px)
    pal = q.quantize(colors=8).convert("RGB")
    counts = sorted(pal.getcolors(len(px)) or [], reverse=True)
    out = []
    for _, rgb in counts:
        h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
        out.append((s * (0.3 < l < 0.85), rgb))
    out.sort(key=lambda t: -t[0])
    seen = []
    for _, rgb in out:
        hx = rgb_to_hex(rgb)
        if hx not in seen:
            seen.append(hx)
    return seen[:n]


# ------------------------------------------------------------------ kit extraction from a website
class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.icons, self.title, self._t = {}, [], "", False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "link" and "icon" in a.get("rel", "").lower() and a.get("href"):
            self.icons.append((a.get("rel", "").lower(), a.get("sizes", ""), a["href"]))
        elif tag == "title":
            self._t = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._t = False

    def handle_data(self, data):
        if self._t:
            self.title += data


def kit_from_html(url: str, html: str) -> dict:
    """Name, handle, theme color and logo candidates from a homepage, no AI needed."""
    p = _Meta()
    try:
        p.feed(html[:400_000])
    except Exception:
        pass
    m = p.meta
    name = (m.get("og:site_name") or p.title.split("|")[0].split(" - ")[0]).strip()[:60] or urlparse(url).hostname or ""
    handle = (m.get("twitter:site") or m.get("twitter:creator") or "").lstrip("@").strip()[:40]
    icons = sorted(p.icons, key=lambda t: ("apple-touch" not in t[0], -int((t[1].split("x")[0] or "0") if t[1][:1].isdigit() else 0)))
    logo_candidates = [urljoin(url, h) for _, _, h in icons] + ([urljoin(url, m["og:image"])] if m.get("og:image") else [])
    theme = m.get("theme-color", "")
    return {"name": name, "handle": handle, "website": url, "theme_color": theme if HEX_RE.fullmatch(theme.strip()) else "",
            "logo_candidates": logo_candidates[:5], "description": (m.get("og:description") or m.get("description") or "")[:300]}


def pick_colors(theme: str, logo_palette: list[str]) -> dict:
    cands = ([theme] if theme else []) + logo_palette
    accent = next((c for c in cands if 0.08 < luminance(hex_to_rgb(c)) < 0.85), cands[0] if cands else DEFAULT_KIT["colors"]["accent"])
    acc = hex_to_rgb(accent)
    # dark editorial ground unless the accent itself is very dark
    primary = "#111114" if luminance(acc) > 0.12 else "#F6F4EF"
    text = "#FFFFFF" if contrast(hex_to_rgb(primary), (255, 255, 255)) >= 4.5 else "#111114"
    return {"primary": primary, "accent": rgb_to_hex(acc), "text": text}


# ------------------------------------------------------------------ the ledger: corrections -> rules
def _rule(kind: str, check: dict, text: str, source: str) -> dict:
    return {"kind": kind, "check": check, "rule": text, "source": source}


def parse_correction(text: str) -> list[dict]:
    """Turn a plain-language correction into ledger rules without any AI. Returns [] when nothing matched
    (the caller may then ask an AI model to propose a rule)."""
    src = (text or "").strip()
    t = src.lower()
    out: list[dict] = []
    # banned words / phrases
    for m in re.finditer(r"(?:never|don'?t|do not|stop|no more)\s+(?:say|use|write|saying|using)\s+[\"“']?([^\"”'.,;!]+?)[\"”']?(?:[.,;!]|$| again| anymore)", t):
        words = [w.strip() for w in re.split(r"\s+or\s+|\s*,\s*|\s+and\s+", m.group(1)) if w.strip()]
        if words:
            out.append(_rule("text", {"type": "banned_words", "words": words}, f"Never use: {', '.join(words)}", src))
    for m in re.finditer(r"\b(?:use|say|write)\s+[\"“']?([^\"”',.;!]+?)[\"”']?\s+(?:instead of|not|rather than)\s+[\"“']?([^\"”'.;!]+?)[\"”']?(?:[.;!]|$)", t):
        to, frm = m.group(1).strip(), [w.strip() for w in re.split(r"\s+or\s+|\s*,\s*", m.group(2)) if w.strip()]
        if to and frm and len(to) <= 40:
            out.append(_rule("text", {"type": "prefer_words", "from": frm, "to": to}, f"Say “{to}”, not {', '.join('“' + w + '”' for w in frm)}", src))
    for m in re.finditer(r"\breplace\s+[\"“']?([^\"”',.;!]+?)[\"”']?\s+with\s+[\"“']?([^\"”',.;!]+?)[\"”']?(?:[.;!]|$)", t):
        frm, to = m.group(1).strip(), m.group(2).strip()
        if to and frm and len(to) <= 40 and "logo" not in frm:
            out.append(_rule("text", {"type": "prefer_words", "from": [frm], "to": to}, f"Say “{to}”, not “{frm}”", src))
    if re.search(r"\bno (?:more )?emojis?\b|\bwithout emojis?\b|\bdon'?t use emojis?\b", t):
        out.append(_rule("text", {"type": "no_emoji"}, "No emojis in captions", src))
    m = re.search(r"(?:max(?:imum)?|at most|no more than|only)\s+(\d+)\s+hashtags?", t)
    if m:
        out.append(_rule("text", {"type": "max_hashtags", "n": int(m.group(1))}, f"At most {m.group(1)} hashtags", src))
    elif re.search(r"\bno hashtags?\b", t):
        out.append(_rule("text", {"type": "max_hashtags", "n": 0}, "No hashtags", src))
    if re.search(r"\bno exclamation|\bstop shouting\b|\bless hype\b|\bno hype\b|\btoo hyp", t):
        out.append(_rule("text", {"type": "no_exclamation"}, "No exclamation marks", src))
        if "hype" in t:
            out.append(_rule("text", {"type": "banned_words", "words": ["revolutionary", "game-changer", "game changer",
                                                                          "insane", "mind-blowing", "10x", "ultimate"]},
                             "No hype words (revolutionary, game-changer, insane, mind-blowing, 10x, ultimate)", src))
    vm = re.search(r"(?:end|finish|close)\s+(?:every|each|all(?: my)?|the)\s+(?:videos?|reels?|shorts?|clips?)\s+(?:with|on)\s+"
                   r"(?:an? |the )?(?:end ?card (?:saying |that says |with )?)?[\"“']?(.+?)[\"”']?\s*[.!]?\s*$", t)
    if vm:
        cta = src[vm.start(1):vm.end(1)].strip()[:60]
        out.append(_rule("visual", {"type": "end_card", "on": True, "cta": cta}, f"Every video ends on an end card: “{cta}”", src))
        t = t[:vm.start()] + t[vm.end():]
    m = re.search(r"(?:always\s+)?(?:end|finish|sign off)\s+(?:every post\s+|captions?\s+)?with\s+[\"“']?([^\"”']+?)[\"”']?\s*$", t)
    if m:
        out.append(_rule("text", {"type": "ends_with", "text": src[m.start(1):m.end(1)].strip()}, f"Captions end with “{src[m.start(1):m.end(1)].strip()}”", src))
    m = re.search(r"(?:always\s+)?(?:include|mention|tag)\s+[\"“']?(@\w+|#\w+)", src, re.I)
    if m:
        out.append(_rule("text", {"type": "require_text", "text": m.group(1)}, f"Always include {m.group(1)}", src))
    if re.search(r"\b(?:all )?lower ?case captions?\b|\bcaptions? in lower ?case\b", t):
        out.append(_rule("text", {"type": "caption_case", "value": "lower"}, "Captions in lowercase", src))
    m = re.search(r"(?:keep|captions?)\s+(?:it\s+|them\s+|captions?\s+)?(?:under|below|max(?:imum)?|at most)\s+(\d+)\s+(?:chars|characters)", t)
    if m:
        out.append(_rule("text", {"type": "max_length", "n": int(m.group(1))}, f"Captions under {m.group(1)} characters", src))
    # visual
    if "logo" in t:
        pos = next((v for k, v in POS.items() if k in t), None)
        if pos:
            out.append(_rule("visual", {"type": "logo_position", "pos": pos}, f"Logo sits {POS_NAME[pos]}", src))
        clause = " ".join(c for c in re.split(r"[.;!?\n]", t) if "logo" in c)
        if re.search(r"\b(?:smaller|too big|too large|huge|shrink|less prominent)\b", clause):
            out.append(_rule("visual", {"type": "logo_scale", "scale": 0.075}, "Logo stays small (7.5% of width)", src))
        elif re.search(r"\b(?:bigger|larger|too small|tiny|more prominent)\b", clause):
            out.append(_rule("visual", {"type": "logo_scale", "scale": 0.15}, "Logo is prominent (15% of width)", src))
    if re.search(r"(?:no|drop|remove|skip) (?:the )?end ?card", t):
        out.append(_rule("visual", {"type": "end_card", "on": False}, "No end card", src))
    elif re.search(r"(?:always|add|keep) (?:an |the )?end ?card", t):
        out.append(_rule("visual", {"type": "end_card", "on": True}, "Every video ends on the brand end card", src))
    if re.search(r"headlines?\s+(?:in\s+)?(?:sentence|normal|regular) case|(?:no|stop) (?:all )?caps|not all caps|less shouty", t):
        out.append(_rule("visual", {"type": "headline_case", "value": "sentence"}, "Headlines in sentence case, not all caps", src))
    m = re.search(r"(?:headlines?|text on (?:the )?image)\s+(?:under|max(?:imum)?|at most|no more than)\s+(\d+)\s+words", t)
    if m:
        out.append(_rule("visual", {"type": "headline_max_words", "n": int(m.group(1))}, f"On-image headline at most {m.group(1)} words", src))
    hexes = HEX_RE.findall(src)
    if hexes and re.search(r"colou?r|accent|brand|use", t):
        h = "#" + hexes[0].upper()
        out.append(_rule("visual", {"type": "accent_color", "hex": h}, f"Accent color is {h}", src))
    for name in FONTS:
        if re.search(rf"\b{name}\b", t) and "font" in t:
            out.append(_rule("visual", {"type": "font", "name": name}, f"Headline font is {name.title()}", src))
    if re.search(r"(?:darker|more) overlay|text (?:is )?hard to read|can'?t read", t):
        out.append(_rule("visual", {"type": "overlay", "value": 0.72}, "Darker image overlay so text reads", src))
    elif re.search(r"(?:lighter|less) overlay|too dark", t):
        out.append(_rule("visual", {"type": "overlay", "value": 0.35}, "Lighter image overlay", src))
    if re.search(r"(?:always )?credit (?:the )?source|show (?:the )?source", t):
        out.append(_rule("visual", {"type": "source_credit", "on": True}, "Always credit the source on the image", src))
    return out


RULE_SCHEMA_HINT = (
    'Return JSON {"rules":[{"kind":"text"|"visual","check":{...},"rule":"short human rule"}]}. check.type is one of: '
    'banned_words{words}, prefer_words{from:[words], to}, no_emoji, max_hashtags{n}, no_exclamation, ends_with{text}, require_text{text}, caption_case{value:"lower"}, '
    'max_length{n}, logo_position{pos:tl|tr|bl|br}, logo_scale{scale:0.05-0.2}, end_card{on, cta?}, headline_case{value:upper|sentence}, '
    'headline_max_words{n}, accent_color{hex}, font{name:anton|poppins|inter|figtree|serif|dot}, overlay{value:0-0.9}, source_credit{on}. '
    'Only rules the correction clearly implies; [] if none.')

VALID_TYPES = {"banned_words", "prefer_words", "no_emoji", "max_hashtags", "no_exclamation", "ends_with", "require_text", "caption_case",
               "max_length", "logo_position", "logo_scale", "end_card", "headline_case", "headline_max_words", "accent_color",
               "font", "overlay", "source_credit"}


def clean_rules(rules: list, source: str) -> list[dict]:
    out = []
    for r in rules or []:
        if not isinstance(r, dict) or not isinstance(r.get("check"), dict):
            continue
        ty = r["check"].get("type")
        if ty not in VALID_TYPES:
            continue
        kind = "visual" if ty in {"logo_position", "logo_scale", "end_card", "headline_case", "headline_max_words", "accent_color",
                                  "font", "overlay", "source_credit"} else "text"
        out.append(_rule(kind, r["check"], str(r.get("rule") or ty)[:160], source))
    return out


def for_channel(rules: list[dict], service: str | None) -> list[dict]:
    """The rules that apply on one network: every rule for all channels, plus the ones scoped to this network."""
    svc = (service or "").lower()
    return [r for r in rules if not r.get("scope") or (svc and svc in r["scope"])]


def visual_params(kit: dict, rules: list[dict]) -> dict:
    """What a render should do, after every visual rule in the ledger is applied (newest wins)."""
    p = dict(VISUAL_DEFAULTS)
    p["colors"] = dict(kit.get("colors") or DEFAULT_KIT["colors"])
    p["font"] = kit.get("font") or "inter"
    p["end_card"] = bool((kit.get("end_card") or {}).get("on", True))
    applied = []
    for r in rules:
        if r.get("kind") != "visual" or not r.get("active", True):
            continue
        c = r["check"]
        ty = c["type"]
        if ty == "logo_position":
            p["logo_position"] = c.get("pos", "tr")
        elif ty == "logo_scale":
            p["logo_scale"] = max(0.04, min(0.22, float(c.get("scale", 0.11))))
        elif ty == "end_card":
            p["end_card"] = bool(c.get("on"))
            if c.get("cta"):
                p["end_card_text"] = str(c["cta"])[:60]
        elif ty == "headline_case":
            p["headline_case"] = c.get("value", "upper")
        elif ty == "headline_max_words":
            p["headline_max_words"] = int(c.get("n", 14))
        elif ty == "accent_color":
            p["colors"]["accent"] = c.get("hex") or p["colors"]["accent"]
        elif ty == "font" and c.get("name") in FONTS:
            p["font"] = c["name"]
        elif ty == "overlay":
            p["overlay"] = max(0.0, min(0.9, float(c.get("value", 0.55))))
        elif ty == "source_credit":
            p["source_credit"] = bool(c.get("on"))
        applied.append(r.get("id"))
    p["applied_rules"] = [a for a in applied if a is not None]
    return p


def apply_text_rules(text: str, rules: list[dict], trace: list | None = None) -> str:
    """Fix what can be fixed mechanically (banned words, emoji, hashtags, case, sign-off) before checking. With ``trace``,
    every rule that changed the text appends {rule_id, before, after}: the Style Ledger keeps them as the rule's examples."""
    out = text or ""
    for r in rules:
        if r.get("kind") != "text" or not r.get("active", True):
            continue
        c = r["check"]
        ty = c["type"]
        was = out
        if ty == "banned_words":
            for w in c.get("words", []):
                out = re.sub(rf"\s*\b{re.escape(w)}\b", "", out, flags=re.I)
        elif ty == "no_emoji":
            out = EMOJI_RE.sub("", out)
        elif ty == "max_hashtags":
            tags = HASHTAG_RE.findall(out)
            for tag in tags[int(c.get("n", 3)):]:
                out = re.sub(rf"\s*{re.escape(tag)}\b", "", out, count=1)
        elif ty == "no_exclamation":
            out = re.sub(r"!+", ".", out).replace("..", ".")
        elif ty == "caption_case" and c.get("value") == "lower":
            out = re.sub(r"https?://\S+|[^\s]+", lambda m: m.group(0) if m.group(0).startswith("http") else m.group(0).lower(), out)
        elif ty == "ends_with" and c.get("text") and not out.rstrip().endswith(c["text"]):
            out = out.rstrip() + "\n\n" + c["text"]
        elif ty == "require_text" and c.get("text") and c["text"].lower() not in out.lower():
            out = out.rstrip() + " " + c["text"]
        elif ty == "prefer_words" and c.get("to"):
            for w in c.get("from", []):
                out = re.sub(rf"\b{re.escape(w)}\b", lambda m, to=c["to"]: to[:1].upper() + to[1:] if m.group(0)[:1].isupper() else to,
                             out, flags=re.I)
        if trace is not None and out != was and r.get("id") is not None:
            trace.append({"rule_id": r["id"], "before": was, "after": out})
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def brand_check(text: str, render_meta: dict | None, rules: list[dict], kit: dict) -> dict:
    """Every active rule against the finished caption and render. Each result cites the rule it checked."""
    results = []

    def add(r, ok, why=""):
        results.append({"rule_id": r.get("id"), "rule": r["rule"], "kind": r["kind"], "pass": bool(ok), "why": why})

    text = text or ""
    for r in rules:
        if not r.get("active", True):
            continue
        c, ty = r["check"], r["check"]["type"]
        if ty == "banned_words":
            hit = [w for w in c.get("words", []) if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
            add(r, not hit, f"uses {', '.join(hit)}" if hit else "")
        elif ty == "prefer_words":
            hit = [w for w in c.get("from", []) if re.search(rf"\b{re.escape(w)}\b", text, re.I)]
            add(r, not hit, f"says {', '.join(hit)} (use {c.get('to')})" if hit else "")
        elif ty == "no_emoji":
            e = EMOJI_RE.findall(text)
            add(r, not e, f"{len(e)} emoji" if e else "")
        elif ty == "max_hashtags":
            n = len(HASHTAG_RE.findall(text))
            add(r, n <= int(c.get("n", 3)), f"{n} hashtags" if n > int(c.get("n", 3)) else "")
        elif ty == "no_exclamation":
            add(r, "!" not in text, "has an exclamation mark" if "!" in text else "")
        elif ty == "ends_with":
            add(r, text.rstrip().endswith(c.get("text", "")), "missing the sign-off")
        elif ty == "require_text":
            ok = c.get("text", "").lower() in text.lower()
            add(r, ok, "" if ok else f"missing {c.get('text')}")
        elif ty == "caption_case":
            letters = re.sub(r"https?://\S+|@\w+|#\w+", "", text)
            ok = letters == letters.lower()
            add(r, ok, "" if ok else "has capitals")
        elif ty == "max_length":
            add(r, len(text) <= int(c.get("n", 0)), f"{len(text)} characters" if len(text) > int(c.get("n", 0)) else "")
        elif render_meta is not None:
            p = render_meta.get("params") or {}
            if ty == "logo_position":
                ok = (not render_meta.get("has_logo")) or p.get("logo_position") == c.get("pos")
                add(r, ok, "" if ok else f"logo is {POS_NAME.get(p.get('logo_position'), '?')}")
            elif ty == "logo_scale":
                ok = abs(float(p.get("logo_scale", 0)) - float(c.get("scale", 0))) < 0.011 or not render_meta.get("has_logo")
                add(r, ok, "" if ok else f"logo at {round(100 * float(p.get('logo_scale', 0)), 1)}% width")
            elif ty == "end_card":
                if render_meta.get("type") == "video":
                    ok = bool(render_meta.get("has_end_card")) == bool(c.get("on"))
                    add(r, ok, "" if ok else ("no end card" if c.get("on") else "has an end card"))
                else:
                    add(r, True, "photo: not applicable")
            elif ty == "headline_case":
                h = render_meta.get("headline", "")
                ok = (h == h.upper()) if c.get("value") == "upper" else (h != h.upper() or not re.search(r"[A-Za-z]{2}", h))
                add(r, ok, "" if ok else "headline case differs")
            elif ty == "headline_max_words":
                n = len((render_meta.get("headline") or "").split())
                add(r, n <= int(c.get("n", 14)), f"{n} words" if n > int(c.get("n", 14)) else "")
            elif ty == "accent_color":
                ok = (p.get("colors") or {}).get("accent", "").upper() == c.get("hex", "").upper()
                add(r, ok, "" if ok else f"accent is {(p.get('colors') or {}).get('accent')}")
            elif ty == "font":
                add(r, p.get("font") == c.get("name"), "" if p.get("font") == c.get("name") else f"font is {p.get('font')}")
            elif ty == "overlay":
                add(r, abs(float(p.get("overlay", 0)) - float(c.get("value", 0))) < 0.06, "")
            elif ty == "source_credit":
                ok = bool(render_meta.get("credit")) or not c.get("on")
                add(r, ok, "" if ok else "no source credit on the image")
    # built-in brand checks that need no ledger rule
    if render_meta is not None:
        p = render_meta.get("params") or {}
        cols = p.get("colors") or kit.get("colors") or {}
        cr = contrast(hex_to_rgb(cols.get("text", "#FFFFFF")), hex_to_rgb(cols.get("primary", "#111114")))
        results.append({"rule_id": None, "rule": "Text is readable on the brand ground (contrast 4.5:1 or more)", "kind": "builtin",
                        "pass": cr >= 4.5, "why": f"contrast {cr}:1"})
        if render_meta.get("width") and render_meta.get("height"):
            ratio = render_meta["width"] / render_meta["height"]
            results.append({"rule_id": None, "rule": "Frame is a network size (9:16, 4:5 or 1:1)", "kind": "builtin",
                            "pass": any(abs(ratio - x) < 0.01 for x in (9 / 16, 4 / 5, 1.0)),
                            "why": f"{render_meta['width']}x{render_meta['height']}"})
    failed = [r for r in results if not r["pass"]]
    return {"pass": not failed, "results": results, "failed": failed,
            "summary": "Passes every rule" if not failed else "; ".join(f"“{f['rule']}”: {f['why'] or 'failed'}" for f in failed)}


def rules_prompt(rules: list[dict]) -> str:
    lines = [f"- {r['rule']}" for r in rules if r.get("active", True) and r.get("kind") == "text"]
    return ("Brand rules you must follow:\n" + "\n".join(lines)) if lines else ""


def dumps(o) -> str:
    return json.dumps(o, separators=(",", ":"))
