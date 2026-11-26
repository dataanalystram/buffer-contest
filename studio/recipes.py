"""One take recipes: a named set of the switches One take already has, picked in one tap on the setup card
(and by an AI app through REST / MCP). A recipe decides which edits run; the brand kit decides the look.

Each value maps onto real behaviour, nothing decorative:

* ``split``: two people talking get Split (one speaker per half) even when the server's HQ_LAYOUT_SPLIT is off.
* ``length``: the clip length the moment picker aims for (take.LENGTHS).
* ``caption``: the caption preset clips start with (captions_ass.PRESETS); the person can still change it per clip.
* ``punch_in`` / ``trim_pauses``: the defaults of those two switches.
* ``cold_open``: a clip may open on a stronger line said earlier in the video, then cut to its moment (take.cold_open).
"""
from __future__ import annotations

RECIPES = {
    "auto": {"label": "Let it decide", "about": "Reads each shot and picks the framing, length and style.",
             "split": False, "length": "", "caption": None, "punch_in": True, "trim_pauses": True, "cold_open": True},
    "podcast": {"label": "Podcast or interview", "about": "Two people talking: each speaker gets half the frame, 30-60 s clips.",
                "split": True, "length": "mid", "caption": "bold_pop", "punch_in": False, "trim_pauses": True, "cold_open": True},
    "talking": {"label": "Talking head", "about": "One person to camera: tight crop, gentle zoom on the strong words.",
                "split": False, "length": "", "caption": "bold_pop", "punch_in": True, "trim_pauses": True, "cold_open": True},
    "tutorial": {"label": "Screen tutorial", "about": "A screen recording: zooms in where you type, longer clips, clean captions.",
                 "split": False, "length": "long", "caption": "lower_third", "punch_in": False, "trim_pauses": True, "cold_open": False},
    "product": {"label": "Product demo", "about": "A product on camera or on screen: zooms in on it, short punchy clips.",
                "split": False, "length": "short", "caption": "two_tone", "punch_in": True, "trim_pauses": True, "cold_open": True},
}
LENGTHS = {"": "Auto (20-60 s)", "short": "15-30 s", "mid": "30-60 s", "long": "60-90 s"}


def clean(v) -> str:
    return v if isinstance(v, str) and v in RECIPES else "auto"


def clean_caption(v) -> str | None:
    return v if isinstance(v, str) and v in CAPTION_IDS else None


def clean_length(v) -> str:
    return v if isinstance(v, str) and v in LENGTHS else ""


def public() -> dict:
    """For the setup card and the MCP tool description."""
    return {"recipes": [{"id": k, "label": r["label"], "about": r["about"], "length": r["length"],
                         "punch_in": r["punch_in"], "trim_pauses": r["trim_pauses"], "split": r["split"], "cold_open": r["cold_open"],
                         "caption": r["caption"] or "bold_pop"} for k, r in RECIPES.items()],
            "lengths": [{"id": k, "label": v} for k, v in LENGTHS.items()],
            "captions": _captions()}


def _captions() -> list[dict]:
    from .media import captions_ass
    order = ["clean", "minimal", "bold_pop", "cinematic", "documentary", "lower_third", "two_tone", "typewriter"]
    by = {c["id"]: c for c in captions_ass.presets()}
    return [by[k] for k in order if k in by]


# ---------------------------------------------------------------------- the creative instruction
# "Make this look like a premium technology explainer. Use bold typography, relevant motion graphics and subtle sound
# effects. Keep the product demonstration readable." -> the switches One take already has, each with the words that set
# it, plus what it asked for that One take can't do yet (said, never silently dropped).
SETTING_KEYS = ("recipe", "length", "caption", "headline", "cold_open", "trim_pauses", "punch_in", "split", "motion")
CAPTION_IDS = ("clean", "minimal", "bold_pop", "cinematic", "documentary", "lower_third", "two_tone", "typewriter")
_RULES = [  # (words that ask for it, setting, value, why)
    (("podcast", "interview", "two people", "two speakers", "conversation"), "recipe", "podcast", "a conversation between two people"),
    (("tutorial", "screen recording", "walkthrough", "how-to", "how to", "explainer", "demo of the app"), "recipe", "tutorial", "a screen walkthrough"),
    (("product", "unboxing", "review of", "gadget", "device"), "recipe", "product", "a product on camera"),
    (("talking head", "to camera", "vlog"), "recipe", "talking", "one person to camera"),
    (("short", "punchy", "snappy", "quick"), "length", "short", "short and punchy"),
    (("long", "in-depth", "in depth", "deep dive", "full context"), "length", "long", "longer clips with full context"),
    (("bold", "creator", "loud", "hype", "energetic", "big text", "bold typography"), "caption", "bold_pop", "bold creator captions"),
    (("cinematic", "editorial", "premium", "elegant", "film"), "caption", "cinematic", "cinematic editorial captions"),
    (("documentary", "serious", "journalistic", "news"), "caption", "documentary", "documentary subtitles"),
    (("minimal", "minimalist", "clean", "subtle captions", "simple"), "caption", "minimal", "minimal captions"),
    (("educational", "teach", "lesson", "course", "learn"), "caption", "lower_third", "clear educational captions"),
    (("headline", "title card", "hook text", "big title"), "headline", True, "a headline over the opening"),
    (("motion graphics", "animated", "animation", "kinetic", "counters", "stats", "graphics"), "motion", True, "motion graphics where the words support them"),
    (("no zoom", "static", "don't zoom", "do not zoom"), "punch_in", False, "no automatic zoom"),
    (("zoom", "punch in", "punch-in", "dynamic"), "punch_in", True, "gentle zooms on strong words"),
    (("readable", "legible", "keep the product", "keep the demo", "show the screen"), "punch_in", False, "keep the screen readable (no punch-in over it)"),
    (("keep pauses", "no cuts", "uncut", "natural pace"), "trim_pauses", False, "the natural pace, pauses kept"),
    (("tight", "no pauses", "fast paced", "fast-paced", "remove filler", "cut filler"), "trim_pauses", True, "pauses and filler cut"),
]
_NOT_YET = [
    (("sound effect", "sfx", "whoosh", "swoosh"), "Sound effects aren't in One take yet, so none will be added."),
    (("music", "soundtrack", "background track"), "Music: add an Instagram track per clip in Trim & caption; One take doesn't pick music yet."),
    (("b-roll", "broll", "b roll", "stock footage"), "B-roll isn't in One take yet, so none will be added."),
    (("transition", "wipe", "glitch"), "Transitions between scenes aren't in One take yet."),
    (("voiceover", "voice over", "ai voice", "narration"), "One take only uses your own words; it never adds a voice."),
]


def _has(text: str, words) -> str | None:
    for w in words:
        if w in text:
            return w
    return None


def interpret_rules(instruction: str) -> dict:
    """The deterministic reading of a creative instruction: {settings, why: {key: reason}, not_yet: [..]}. Only settings
    the words ask for are returned; everything else stays as the person set it."""
    t = " " + " ".join(str(instruction or "").lower().split())[:600] + " "
    settings, why = {}, {}
    for words, key, value, reason in _RULES:
        if key in settings:
            continue   # first rule wins: "no zoom" before "zoom", "readable" before a later "zoom"
        hit = _has(t, words)
        if hit:
            settings[key], why[key] = value, f'"{hit.strip()}": {reason}'
    if settings.get("recipe") == "podcast":
        settings.setdefault("split", True)
        why.setdefault("split", "a conversation: each speaker gets half the frame")
    not_yet = [msg for words, msg in _NOT_YET if _has(t, words)]
    return {"settings": settings, "why": why, "not_yet": not_yet}


def clean_settings(raw) -> dict:
    """Keep only known keys with valid values (an AI's reading goes through this too)."""
    out = {}
    if not isinstance(raw, dict):
        return out
    if raw.get("recipe") in RECIPES:
        out["recipe"] = raw["recipe"]
    if raw.get("length") in LENGTHS:
        out["length"] = raw["length"]
    if raw.get("caption") in CAPTION_IDS:
        out["caption"] = raw["caption"]
    for k in ("headline", "cold_open", "trim_pauses", "punch_in", "split", "motion"):
        if isinstance(raw.get(k), bool):
            out[k] = raw[k]
    return out


def recommend_caption(recipe: str, kit: dict | None = None, instruction: str = "") -> dict:
    """The caption style to start with and why: the instruction first, then the brand kit's own type, then the recipe."""
    asked = interpret_rules(instruction).get("settings", {}).get("caption") if instruction else None
    if asked:
        return {"id": asked, "why": "From your instruction."}
    fonts = " ".join(str((kit or {}).get(k) or "") for k in ("font", "heading_font", "head_font", "body_font")).lower()
    if any(f in fonts for f in ("serif", "garamond", "playfair", "georgia", "times")) and "sans" not in fonts:
        return {"id": "cinematic", "why": "Your brand kit uses a serif, so the captions match it."}
    rid = clean(recipe)
    cap = RECIPES[rid]["caption"]
    if cap:
        return {"id": cap, "why": f"Suits a {RECIPES[rid]['label'].lower()}."}
    return {"id": "bold_pop", "why": "Reads well on a phone for most talking videos."}


AI_SYSTEM = ("You turn a creator's description of the edit they want into One take's settings. Use only these keys and "
             "values: recipe (auto|podcast|talking|tutorial|product), length (\"\"|short|mid|long), caption (clean|minimal|"
             "bold_pop|cinematic|documentary|lower_third|two_tone|typewriter), headline, cold_open, trim_pauses, punch_in, "
             "split, motion (true|false). Only set what the description asks for. For each key you set, give a short why "
             "that quotes the words that asked for it. Reply as JSON: {\"settings\": {...}, \"why\": {key: reason}}.")
