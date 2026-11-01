"""The edit styles and video THEMES an agent (or the UI) can pick, described in plain words plus the knobs compose.py reads.

Pick by intent: "short punchy explainer" -> opus or hype; "news / announcement" -> huhbyte_news; "calm walkthrough,
tutorial, B2B" -> minimal. Every style renders 9:16, 4:5 and 1:1 and always ends with the Follow end card.

THEMES (below the four base styles) are complete looks built on one of the four layouts: a colour grade as an FFmpeg
filter graph, picture effects (grain, flicker, gate weave, scanlines, tilt-shift...), an ASS caption style
(studio/media/captions.py), a title card + facts card layout (studio/media/theme_cards.py), on-screen chrome, a
transition and a music mood. Sixteen of them are reimplemented from the film styles of lemomo-ai/lemo-opuscar (MIT,
Copyright (c) 2026 LemoLab; see NOTICE): their palettes, grades, type and caption grammar, adapted from canvas/WebGL
films made from scratch to FFmpeg edits of real launch footage. ``cinematic`` is our own.
"""
from __future__ import annotations

STYLE_PRESETS: dict[str, dict] = {
    "opus": {
        "label": "Opus-style clip", "layout": "opus", "fill": "fill", "captions": "opus", "autoclip": True,
        "clip_range": (20.0, 45.0), "cover_secs": 0.0, "facts_secs": 0.0, "facts_title": "", "cover_label": "",
        "music": "pulse", "flash": False,
        "about": "Auto-picks the best 20-45 s moment from the transcript, reframes it full screen, hook title on top from "
                 "frame 0, big bouncing word-by-word captions (Opus Clip / Submagic look), Follow end card.",
        "best_for": ["talking-head launch videos", "founder demos", "podcast or keynote moments", "short punchy explainers"],
    },
    "huhbyte_news": {
        "label": "HuhByte news", "layout": "huhbyte_news", "fill": "panel", "captions": "news", "autoclip": False,
        "clip_range": (0, 0), "cover_secs": 0.9, "facts_secs": 3.5, "facts_title": "What it does", "cover_label": "WATCH",
        "music": "cinematic", "flash": False,
        "about": "The HuhByte layout: branded cover with the hook in the first second, logo + hook headline over the FULL "
                 "product video in a panel (never trimmed), word-highlight captions, credit line, Follow pill, a facts "
                 "card with the product info, then the Follow end card. Cinematic score under silent demos.",
        "best_for": ["launch announcements", "product demos without speech", "AI news"],
    },
    "minimal": {
        "label": "Minimal explainer", "layout": "minimal", "fill": "blurpanel", "captions": "minimal", "autoclip": False,
        "clip_range": (0, 0), "cover_secs": 0.0, "facts_secs": 3.5, "facts_title": "Key points", "cover_label": "",
        "music": "cinematic", "flash": False,
        "about": "Calm and clean: blurred-video backdrop, the video in a framed panel, a small left-aligned title, boxed "
                 "sentence captions, a key-points card and the end card.",
        "best_for": ["tutorials", "B2B and dev tools", "LinkedIn"],
    },
    "hype": {
        "label": "Hype", "layout": "hype", "fill": "fill", "captions": "hype", "autoclip": True,
        "clip_range": (12.0, 30.0), "cover_secs": 0.0, "facts_secs": 0.0, "facts_title": "", "cover_label": "",
        "music": "pulse", "flash": True,
        "about": "Fast and loud: white flash in, full-screen video, one giant word at a time punching in with a tilt, "
                 "driving beat under the speech, end card. 12-30 s.",
        "best_for": ["TikTok", "big funding or launch news", "consumer hardware"],
    },
}

# Short names agents and people use ("news" is the HuhByte news layout).
ALIASES = {"news": "huhbyte_news", "huhbyte": "huhbyte_news", "opus_clip": "opus", "clean": "minimal"}


def resolve(style: str | None) -> str:
    """A style id or alias -> the preset id (unknown names pass through so compose.py can refuse them clearly)."""
    s = (style or "").strip().lower()
    return ALIASES.get(s, s)


CHANNEL_RATIO = {"instagram_reel": "9:16", "tiktok": "9:16", "youtube": "9:16", "youtube_shorts": "9:16",
                 "instagram": "4:5", "facebook": "4:5", "linkedin": "4:5", "threads": "4:5", "x": "1:1",
                 "twitter": "1:1", "bluesky": "1:1", "mastodon": "1:1"}


def describe() -> list[dict]:
    return [{"id": k, "label": v["label"], "about": v["about"], "best_for": v["best_for"]} for k, v in STYLE_PRESETS.items()]


_EXPLICIT = [("hype", r"\bhype\s+(?:style|video|edit|reel|cut)\b|\bin\s+hype\b"), ("opus", r"\bopus(?:[\s-]?clip)?\b"),
             ("minimal", r"\bminimal(?:ist)?\s+(?:style|video|edit|look|reel)\b"),
             ("news", r"\b(?:huhbyte|news)\s+(?:style|layout|look)\b")]


def explicit(text: str) -> str | None:
    """The style the person NAMED ("opus clip", "hype style", "minimal look", "news layout"), else None. The fixed
    (no-AI) planner only uses the media pipeline when asked by name; the AI brain picks a style itself."""
    import re
    s = (text or "").lower()
    for sid, rx in _EXPLICIT:
        if re.search(rx, s):
            return sid
    return None


def pick(intent: str) -> str:
    """Map a person's words to a style id ("short punchy explainer" -> opus)."""
    s = (intent or "").lower()
    if any(w in s for w in ("hype", "loud", "energetic", "tiktok trend")):
        return "hype"
    if any(w in s for w in ("opus", "clip", "punchy", "viral", "short", "highlight")):
        return "opus"
    if any(w in s for w in ("minimal", "calm", "clean", "tutorial", "walkthrough", "linkedin")):
        return "minimal"
    return "huhbyte_news"


# ==================================================================================================== video themes
# Each theme: base (the layout + caption engine it extends), fill (how the footage sits: fill | panel | blurpanel |
# stage), grade (FFmpeg filters on the footage only, never on type), fx (picture effects), palette (None keeps the
# brand colour), captions (a captions.STYLES id), card (title + facts card layout in theme_cards), chrome (overlay
# decorations + hook treatment in theme_cards), cover_secs (title card length; the hook is on screen from frame 0),
# transition (into the footage), music, fonts, gate (the picture's aspect inside a panel), source (where it came from).
LEMO = "lemomo-ai/lemo-opuscar styles/{} (MIT, LemoLab)"
THEMES: dict[str, dict] = {
    "caption_news": {
        "label": "Caption News", "base": "opus", "source": LEMO.format("caption-news"),
        "about": "Sound-off social news: full-screen footage under a bottom fade, huge captions slammed in a few words at a time "
                 "with the spoken word on a honey-yellow block, a yellow channel bug top left, light grain.",
        "best_for": ["data and report stories", "funding news", "sound-off feeds"],
        "words": [r"caption[\s-]?news", r"now\s?this", r"\bsound[\s-]?off\b", r"aj\+", r"social news"],
        "content": [r"\b\d+(?:\.\d+)?\s?%", r"\breport\b", r"\bstudy\b", r"\bsurvey\b", r"\braised?\b", r"\bfunding\b"],
        "palette": {"bg": "#0E0E0C", "text": "#F7F5EF", "accent": "#FFC21A", "muted": "#8D8B83", "on_accent": "#0E0E0C"},
        "grade": "eq=contrast=1.07:saturation=0.92:brightness=-0.02,colorbalance=rm=0.025:gm=0.01:bm=-0.025",
        "fx": ["grain:5", "botfade"], "captions": "slam", "card": "ledger", "chrome": "bug", "cover_secs": 1.0,
        "transition": "flash", "music": "pulse"},
    "news_package": {
        "label": "Broadcast News Package", "base": "huhbyte_news", "source": LEMO.format("news-package"),
        "about": "An on-air news package: a lemon sting card, the story in a framed panel, a paper lower-third strap that never "
                 "leaves the bottom with the captions in it, a lemon tag on the headline. Built for round-ups and bulletins.",
        "best_for": ["weekly and daily round-ups", "bulletins", "market updates"],
        "words": [r"news\s?package", r"broadcast", r"bulletin", r"on[\s-]?air", r"\btv news\b", r"weekly recap", r"round[\s-]?up"],
        "content": [r"\bthis week\b", r"\brecap\b", r"\bround[\s-]?up\b", r"\bbulletin\b", r"\bmarkets?\b"],
        "palette": {"bg": "#121417", "text": "#F4F1E8", "accent": "#F2CF1D", "muted": "#D4B064", "on_accent": "#121417"},
        "grade": "eq=contrast=1.05:saturation=1.02", "fx": [], "captions": "strap", "card": "sting", "chrome": "strap",
        "cover_secs": 1.0, "transition": "cut", "music": "pulse"},
    "newsreel": {
        "label": "1920s Newsreel", "base": "minimal", "fill": "panel", "gate": (1.37, 1.0), "source": LEMO.format("newsreel"),
        "about": "A late-1920s sound newsreel: the footage in a 1.37 projected gate, silver grade with warm highlights, flicker, "
                 "gate weave, heavy grain and a vignette, cream type on a dark strip, an ornate title card.",
        "best_for": ["history and anniversaries", "\"100 years ago\" stories", "retro brands"],
        "words": [r"newsreel", r"\b1920s?\b", r"old[\s-]?timey", r"black and white", r"\bvintage news\b"],
        "content": [r"\b(?:18|19)\d0s\b", r"\bhistory\b", r"\banniversary\b", r"\byears ago\b", r"\bcentury\b"],
        "palette": {"bg": "#0A0908", "text": "#F3EEE4", "accent": "#D6CFC0", "muted": "#9E978A", "on_accent": "#0A0908"},
        "grade": "hue=s=0,eq=contrast=1.24:brightness=-0.015:gamma=0.94,colorchannelmixer=rr=1.0:gg=0.965:bb=0.88",
        "fx": ["weave", "flicker", "vignette", "grain:14"], "captions": "serif", "card": "intertitle", "chrome": "gate",
        "cover_secs": 1.2, "transition": "fade", "music": "cinematic",
        "fonts": {"head_font": "DMSerifDisplay-Regular.ttf", "bold_font": "DMSerifDisplay-Regular.ttf"}},
    "dark_keynote": {
        "label": "Dark Tech Keynote", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("dark-keynote"),
        "about": "A software launch keynote: near-black stage with a soft top light and a hairline grid, the product video in a "
                 "hairline frame, one accent colour, clean white captions, a one-line title card.",
        "best_for": ["software launches", "SaaS and AI product reveals", "keynote moments"],
        "words": [r"keynote", r"apple[\s-]?(?:style|like)", r"\blaunch film\b", r"dark (?:tech|mode)", r"\bsleek\b"],
        "content": [r"\blaunch(?:es|ed)?\b", r"\bintroduc", r"\bunveil", r"\bapp\b", r"\bsoftware\b"],
        "palette": {"bg": "#07080A", "text": "#FFFFFF", "accent": None, "muted": "#8E9299"},
        "grade": "eq=contrast=1.04:saturation=1.03", "fx": [], "captions": "keynote", "card": "keynote", "chrome": "keynote",
        "cover_secs": 1.0, "transition": "fade", "music": "cinematic",
        "fonts": {"head_font": "Inter-Bold.ttf", "bold_font": "Inter-SemiBold.ttf", "body_font": "Inter-Regular.ttf"}},
    "glass_studio": {
        "label": "Glass Product Studio", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("glass-product"),
        "about": "A dark-field product studio: the video on a black stage with a reflective floor, a strip-light sweep that "
                 "travels across the frame, cool highlights, no grain. Hardware and gadget reveals.",
        "best_for": ["consumer hardware", "gadget launches", "beauty and design products"],
        "words": [r"\bglass\b", r"product (?:render|studio|shot)", r"\bstudio look\b", r"\bpremium product\b"],
        "content": [r"\bearbuds?\b", r"\bheadphones?\b", r"\bphone\b", r"\bwatch\b", r"\bglasses\b", r"\bdevice\b", r"\bgadget\b"],
        "palette": {"bg": "#050607", "text": "#F5F7FA", "accent": "#9FD8FF", "muted": "#8A96A3", "on_accent": "#050607"},
        "grade": "eq=contrast=1.08:saturation=1.05:gamma=0.98,colorbalance=bh=0.03:rh=-0.01", "fx": ["sweep"],
        "captions": "keynote", "card": "studio", "chrome": "studio", "cover_secs": 1.0, "transition": "fade", "music": "cinematic",
        "fonts": {"head_font": "Inter-Bold.ttf", "bold_font": "Inter-SemiBold.ttf", "body_font": "Inter-Regular.ttf"}},
    "swiss": {
        "label": "Swiss Motion", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("swiss-motion"),
        "about": "International Typographic Style: a light page on a modular grid, flush-left Inter in ink, one red signal "
                 "square, the video set into the grid, captions in ink with the spoken word in red. No gradients, no grain.",
        "best_for": ["design and brand news", "B2B and dev tools", "LinkedIn"],
        "words": [r"\bswiss\b", r"\bbauhaus\b", r"\btypographic\b", r"\bgrid (?:style|look)\b", r"\beditorial grid\b"],
        "content": [r"\bdesign\b", r"\bfigma\b", r"\btypeface\b", r"\bbrand identity\b"],
        "palette": {"bg": "#EAE9E5", "text": "#111111", "accent": "#E30613", "muted": "#6B6B6B", "on_accent": "#FFFFFF"},
        "grade": "eq=contrast=1.03:saturation=0.95", "fx": [], "captions": "swiss", "card": "swiss", "chrome": "swiss",
        "cover_secs": 1.0, "transition": "cut", "music": "pulse",
        "fonts": {"head_font": "Inter-Bold.ttf", "bold_font": "Inter-Bold.ttf", "body_font": "Inter-Regular.ttf"}},
    "kino": {
        "label": "Constructivist Montage", "base": "opus", "source": LEMO.format("kino-montage"),
        "about": "Ink-and-paper print with a single red: the footage in a hard black-and-white print grade on warm paper, a "
                 "diagonal red band carrying the headline, condensed caps captions on red, film grain, cut on the beat.",
        "best_for": ["milestones and records", "industry stories", "collective effort, workers, factories"],
        "words": [r"constructivist", r"\bkino\b", r"soviet", r"propaganda poster", r"\bmontage\b"],
        "content": [r"\bfactory\b", r"\bworkers?\b", r"\bmilestone\b", r"\brecord\b", r"\bbillion\b", r"\bgigafactory\b"],
        "palette": {"bg": "#141210", "text": "#EDE4D0", "accent": "#D52B1E", "muted": "#9A9080", "on_accent": "#EDE4D0"},
        "grade": "hue=s=0,curves=preset=strong_contrast,eq=contrast=1.08:brightness=0.01,colorchannelmixer=rr=0.95:gg=0.9:bb=0.81",
        "fx": ["grain:8"], "captions": "kino", "card": "kino", "chrome": "kino", "cover_secs": 1.0, "transition": "flash",
        "music": "pulse", "fonts": {"head_font": "Oswald-SemiBold.ttf", "bold_font": "Oswald-SemiBold.ttf"}},
    "silent_film": {
        "label": "1920s Silent Film", "base": "minimal", "fill": "panel", "gate": (4, 3), "source": LEMO.format("silent-film"),
        "about": "A silent film in a picture house: a 4:3 sepia-silver print that flickers and weaves, heavy grain and a "
                 "vignette, ornate intertitle cards for the hook and the facts, serif captions on a dark strip, piano-like score.",
        "best_for": ["comedy and quirky stories", "retro brands", "\"what if\" explainers"],
        "words": [r"silent[\s-]?(?:film|movie)", r"chaplin", r"charlie chaplin", r"intertitles?", r"\bsepia\b"],
        "content": [r"\bfunny\b", r"\bfail\b", r"\boops\b", r"\bmeme\b"],
        "palette": {"bg": "#0B0A08", "text": "#EFE6D2", "accent": "#CDBB93", "muted": "#8F8370", "on_accent": "#0B0A08"},
        "grade": "hue=s=0,colorbalance=rs=0.07:gs=0.03:bs=-0.06:rm=0.06:gm=0.02:bm=-0.05,eq=contrast=1.18:gamma=0.92",
        "fx": ["weave", "flicker", "vignette", "grain:16"], "captions": "serif", "card": "intertitle", "chrome": "gate",
        "cover_secs": 1.4, "transition": "fade", "music": "cinematic",
        "fonts": {"head_font": "DMSerifDisplay-Regular.ttf", "bold_font": "DMSerifDisplay-Regular.ttf"}},
    "risograph": {
        "label": "Risograph Print", "base": "opus", "source": LEMO.format("risograph"),
        "about": "Looks printed on a stencil duplicator: the footage as a blue ink on warm paper with a misregistered plate, "
                 "ink grain, pink-and-blue double-hit titles and captions. Zine energy.",
        "best_for": ["indie tools and creators", "lifestyle and culture", "free-tool roundups"],
        "words": [r"\briso(?:graph)?\b", r"\bzine\b", r"\bprinted look\b", r"\bduotone\b", r"\bindie\b"],
        "content": [r"\bcreators?\b", r"\bzine\b", r"\bart\b", r"\bmusic\b", r"\bindie\b"],
        "palette": {"bg": "#F4EEE2", "text": "#0078BF", "accent": "#FF48B0", "muted": "#3D5588", "on_accent": "#F4EEE2"},
        "grade": "hue=s=0,eq=contrast=1.25:brightness=0.02,lutrgb=r='0+244*val/255':g='120+118*val/255':b='191+35*val/255'",
        "fx": ["misreg", "grain:9"], "captions": "riso", "card": "riso", "chrome": "riso", "cover_secs": 1.0,
        "transition": "cut", "music": "pulse", "fonts": {"head_font": "Anton.ttf", "bold_font": "Figtree-ExtraBold.ttf"}},
    "hologram_hud": {
        "label": "Sci-fi Hologram HUD", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("hologram-hud"),
        "about": "A spec walkthrough in a hologram HUD: cyan-tinted footage with scanlines inside target brackets, a dot-grid "
                 "stage, mono labels and tick rulers, monospace captions with a glow, a glitch into the footage.",
        "best_for": ["hardware and chips", "robots and spec walkthroughs", "AI model benchmarks"],
        "words": [r"hologram", r"\bhud\b", r"sci[\s-]?fi", r"iron[\s-]?man", r"futuristic", r"\bcyberpunk\b"],
        "content": [r"\bchips?\b", r"\bgpu\b", r"\brobot", r"\bhumanoid\b", r"\bsensor\b", r"\bspecs?\b", r"\bbenchmark"],
        "palette": {"bg": "#04090C", "text": "#DFF7FF", "accent": "#3FE0FF", "muted": "#5D8A96", "on_accent": "#04090C"},
        "grade": "hue=s=0.3,colorbalance=rs=-0.06:gs=0.04:bs=0.1:rm=-0.04:gm=0.03:bm=0.08,eq=contrast=1.12:brightness=-0.02",
        "fx": ["scanlines"], "captions": "mono", "card": "hud", "chrome": "hud", "cover_secs": 1.0, "transition": "glitch",
        "music": "pulse", "fonts": {"head_font": "IBMPlexMono-SemiBold.ttf", "bold_font": "IBMPlexMono-SemiBold.ttf",
                                     "body_font": "IBMPlexMono-SemiBold.ttf"}},
    "screencast": {
        "label": "Living Screencast", "base": "minimal", "fill": "stage", "source": LEMO.format("living-screencast"),
        "about": "A product walkthrough that looks like a real screen recording: the video in an app window with a title bar "
                 "and a soft shadow on a gradient desk, a short label above, boxed captions. Your brand colours.",
        "best_for": ["product tutorials", "feature launches", "onboarding and how-tos"],
        "words": [r"screen\s?cast", r"screen recording", r"walkthrough", r"\bhow[\s-]to\b", r"tutorial"],
        "content": [r"\bhow to\b", r"\bstep\b", r"\bfeature\b", r"\bdashboard\b", r"\bextension\b", r"\bplugin\b"],
        "palette": None, "grade": "eq=contrast=1.02", "fx": [], "captions": "minimal", "card": "window", "chrome": "window",
        "cover_secs": 1.0, "transition": "fade", "music": "cinematic"},
    "investigation": {
        "label": "Visual Investigation", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("visual-investigation"),
        "about": "A cold investigation desk: greyscale high-contrast footage with grain on a dark desk, an exhibit label and a "
                 "running timecode on the evidence, hairline frames, amber highlights on the spoken word.",
        "best_for": ["fact-checks", "leaks, lawsuits and incidents", "\"what really happened\" explainers"],
        "words": [r"investigat", r"fact[\s-]?check", r"forensic", r"evidence", r"\bdossier\b", r"documentary"],
        "content": [r"\blawsuit\b", r"\bleak(?:ed|s)?\b", r"\bincident\b", r"\boutage\b", r"\bhack(?:ed)?\b", r"\bbreach\b", r"\bprobe\b"],
        "palette": {"bg": "#0B0C0E", "text": "#ECEAE4", "accent": "#F2B544", "muted": "#8B9097", "on_accent": "#0B0C0E"},
        "grade": "hue=s=0,eq=contrast=1.22:brightness=-0.03", "fx": ["grain:10", "timecode"], "captions": "doc",
        "card": "dossier", "chrome": "evidence", "cover_secs": 1.0, "transition": "cut", "music": "cinematic",
        "fonts": {"bold_font": "Inter-SemiBold.ttf"}},
    "countdown": {
        "label": "Live Countdown", "base": "opus", "source": LEMO.format("countdown"),
        "about": "Event-day energy: full-screen footage in a cool-shadow, warm-highlight control-room grade, a T-minus clock "
                 "block that runs to zero over the clip, a progress bar, signal-orange accents, an inverted T-0 title card.",
        "best_for": ["launch days and live events", "keynotes and countdowns", "\"it's out now\" drops"],
        "words": [r"count\s?down", r"\bt[\s-]?minus\b", r"\blive event\b", r"launch day", r"mission control"],
        "content": [r"\blive\b", r"\btoday\b", r"\brocket\b", r"\bspacex\b", r"\bliftoff\b", r"\bevent\b", r"\bkeynote\b"],
        "palette": {"bg": "#06080A", "text": "#F2F5F7", "accent": "#FF5B1F", "muted": "#7C878D", "on_accent": "#0A0B0C"},
        "grade": "eq=contrast=1.07:saturation=0.9:gamma=0.97:brightness=-0.01,colorbalance=rs=-0.02:bs=0.03:rh=0.03:bh=-0.02",
        "fx": ["grain:3", "vignette", "clock"], "captions": "count", "card": "countdown", "chrome": "clock",
        "cover_secs": 1.0, "transition": "flash", "music": "pulse", "fonts": {"bold_font": "IBMPlexMono-SemiBold.ttf"}},
    "crt": {
        "label": "Amber CRT Terminal", "base": "minimal", "tall_aspect": (1, 1), "fill": "stage", "source": LEMO.format("ascii-crt"),
        "about": "A monochrome terminal: the footage glowing in amber phosphor with scanlines and a curved glass, a status "
                 "line, VT323 captions with a glow, a blinking-cursor title card. Dev tools and hacker stories.",
        "best_for": ["GitHub repos and dev tools", "open source", "security and hacker stories"],
        "words": [r"\bcrt\b", r"terminal", r"retro (?:computer|tech)", r"hacker", r"\bascii\b", r"\bmatrix\b"],
        "content": [r"github", r"\brepo\b", r"open[\s-]?source", r"\bcli\b", r"\bterminal\b", r"\bcode\b", r"\bdevelopers?\b"],
        "palette": {"bg": "#0A0702", "text": "#FFB000", "accent": "#FFE3A3", "muted": "#8A5E00", "on_accent": "#0A0702"},
        "grade": "hue=s=0,eq=contrast=1.32:brightness=0.03,lutrgb=r='val':g='val*0.7':b='val*0.16'",
        "fx": ["curve", "scanlines"], "captions": "crt", "card": "terminal", "chrome": "terminal", "cover_secs": 1.0,
        "transition": "glitch", "music": "pulse",
        "fonts": {"head_font": "VT323-Regular.ttf", "bold_font": "VT323-Regular.ttf", "body_font": "VT323-Regular.ttf"}},
    "tilt_shift": {
        "label": "Tilt-Shift Miniature", "base": "opus", "source": LEMO.format("tilt-shift"),
        "about": "The world as a tabletop model: blurred top and bottom bands, punchy saturation, warm highlights and a "
                 "vignette, captions and the title on road-sign panels.",
        "best_for": ["cities, transport and robotaxis", "real estate and infrastructure", "travel"],
        "words": [r"tilt[\s-]?shift", r"miniature", r"toy town", r"tabletop model"],
        "content": [r"\bcity\b", r"\btraffic\b", r"\brobotaxi", r"\bwaymo\b", r"\btrain\b", r"\bairport\b", r"\bdrone\b"],
        "palette": {"bg": "#0F2A1E", "text": "#FFFFFF", "accent": "#FFD23F", "muted": "#BFD3C6", "on_accent": "#0F2A1E"},
        "grade": "eq=saturation=1.45:contrast=1.08,colorbalance=rh=0.04:gh=0.02:bh=-0.03", "fx": ["tiltshift", "vignette"],
        "captions": "sign", "card": "sign", "chrome": "sign", "cover_secs": 1.0, "transition": "fade", "music": "cinematic"},
    "game_show": {
        "label": "Game Show Flat", "base": "hype", "source": LEMO.format("game-show"),
        "about": "A rhythm-game variety show: candy stripes and a sunburst title card, a round label, giant telop words with a "
                 "thick ink stroke popping on the beat, bright saturated footage.",
        "best_for": ["year-in-review and timelines", "leaderboards and model battles", "listicles and rankings"],
        "words": [r"game[\s-]?show", r"\bquiz\b", r"\bversus\b|\bvs\.?\b", r"\bbattle\b", r"\bleaderboard\b", r"\branking\b"],
        "content": [r"\bleaderboard\b", r"\branking\b", r"\btop \d+\b", r"\bversus\b|\bvs\.?\b", r"\bwinner\b", r"\barena\b"],
        "palette": {"bg": "#2B1B4F", "text": "#FFFFFF", "accent": "#FFE14D", "muted": "#FFB870", "on_accent": "#1B1530"},
        "grade": "eq=saturation=1.25:contrast=1.06", "fx": [], "captions": "telop", "card": "candy", "chrome": "candy",
        "cover_secs": 1.0, "transition": "flash", "music": "pulse",
        "fonts": {"head_font": "Figtree-ExtraBold.ttf", "bold_font": "Figtree-ExtraBold.ttf"}},
    "cinematic": {
        "label": "Cinematic", "base": "opus", "source": "HuhQueue (own design)",
        "about": "Film look: 2.39 letterbox bars, a teal-and-orange grade with soft contrast, fine grain and a vignette, a "
                 "serif title card, small elegant captions, the cinematic score. For when it should feel like a trailer.",
        "best_for": ["big emotional launches", "trailers and teasers", "\"the future is here\" stories"],
        "words": [r"cinematic", r"\bmovie\b", r"\bfilmic\b", r"\btrailer\b", r"interstellar", r"\bdramatic\b"],
        "content": [r"\bfuture\b", r"\bspace\b", r"\bagi\b", r"\bbreakthrough\b"],
        "palette": {"bg": "#000000", "text": "#F4EFE6", "accent": "#E8B86B", "muted": "#9A948A", "on_accent": "#000000"},
        "grade": "eq=contrast=1.06:saturation=0.92:gamma=0.97,colorbalance=rs=-0.05:gs=0.0:bs=0.06:rh=0.06:gh=0.02:bh=-0.05",
        "fx": ["grain:4", "vignette", "letterbox"], "captions": "film", "card": "film", "chrome": "film", "cover_secs": 1.2,
        "transition": "fade", "music": "cinematic", "fonts": {"head_font": "DMSerifDisplay-Regular.ttf"}},
}

THEME_ALIASES = {"opus clip": "opus", "like opus clip": "opus", "news style": "huhbyte_news", "news theme": "huhbyte_news",
                 "keynote": "dark_keynote", "glass": "glass_studio", "product studio": "glass_studio", "hud": "hologram_hud",
                 "hologram": "hologram_hud", "terminal": "crt", "retro terminal": "crt", "miniature": "tilt_shift",
                 "tiltshift": "tilt_shift", "silent": "silent_film", "riso": "risograph", "constructivist": "kino",
                 "weekly recap": "news_package", "bulletin": "news_package", "broadcast": "news_package",
                 "fact check": "investigation", "fact-check": "investigation", "trailer": "cinematic", "film": "cinematic",
                 "game show": "game_show", "t-minus": "countdown", "caption news": "caption_news"}
for _k, _v in list(THEME_ALIASES.items()):
    ALIASES.setdefault(_k.replace(" ", "_").replace("-", "_"), _v)
for _tid in THEMES:
    ALIASES.setdefault(_tid.replace("_", "-"), _tid)

BASE_IDS = list(STYLE_PRESETS)
ALL_IDS = BASE_IDS + list(THEMES)


def known(style: str | None) -> bool:
    """True for a base style or a theme (after aliases)."""
    return resolve(style) in STYLE_PRESETS or resolve(style) in THEMES


def config(style: str | None) -> dict:
    """Everything compose.py needs for one style or theme: the base preset with the theme's fields on top.
    cfg["id"] is the resolved id, cfg["layout"] the base layout (opus / huhbyte_news / minimal / hype)."""
    sid = resolve(style)
    if sid in STYLE_PRESETS:
        return {**STYLE_PRESETS[sid], "id": sid, "theme": None, "fx": [], "grade": "", "palette": None}
    if sid not in THEMES:
        raise KeyError(sid)
    t = THEMES[sid]
    base = STYLE_PRESETS[t["base"]]
    cfg = {**base, **{k: v for k, v in t.items() if k not in ("about", "best_for", "words", "content")},
           "id": sid, "theme": sid, "about": t["about"], "best_for": t["best_for"]}
    cfg["fill"] = t.get("fill") or base["fill"]
    if t.get("cover_secs") is not None:
        cfg["cover_secs"] = t["cover_secs"]
    cfg["cover_label"] = cfg.get("cover_label") or "WATCH"
    return cfg


def apply_palette(brand: dict, cfg: dict, keep_brand: bool = False) -> dict:
    """The brand dict with the theme's colours and fonts on top (logo, name and @handle always stay the brand's).
    keep_brand=True keeps the brand's own accent with the theme's ground and type."""
    pal = cfg.get("palette") or {}
    out = dict(brand)
    for k in ("bg", "text", "muted"):
        if pal.get(k):
            out[k] = pal[k]
    if pal.get("accent") and not keep_brand:
        out["accent"] = pal["accent"]
    out["on_accent"] = pal.get("on_accent") or out.get("bg", "#0E0E10")
    out.update(cfg.get("fonts") or {})
    return out


def describe_themes() -> list[dict]:
    """Every pickable look (the four base styles first, then the themes) for the gallery, REST, MCP and the agent."""
    out = [{"id": k, "label": v["label"], "about": v["about"], "best_for": v["best_for"], "base": k, "kind": "style",
            "source": "HuhQueue", "thumb": f"/static/themes/{k}.jpg", "loop": f"/static/themes/{k}.mp4"} for k, v in STYLE_PRESETS.items()]
    for k, t in THEMES.items():
        out.append({"id": k, "label": t["label"], "about": t["about"], "best_for": t["best_for"], "base": t["base"], "kind": "theme",
                    "source": t["source"], "thumb": f"/static/themes/{k}.jpg", "loop": f"/static/themes/{k}.mp4",
                    "music": t["music"], "captions": t["captions"]})
    return out


def _rx(pats, s: str) -> int:
    import re
    return sum(1 for p in pats if re.search(p, s))


def explicit_theme(text: str) -> str | None:
    """A theme or style the person NAMED ("cinematic", "switch to the news theme", "like opus clip", "crt look")."""
    import re
    s = (text or "").lower()
    m = re.search(r"\b(?:switch|change|go|make it|use|try|do it|redo it|in|with)\b[^.]{0,30}?\b([a-z0-9 +-]{3,28}?)\s+(?:theme|style|look)\b", s)
    if m:
        name = m.group(1).strip()
        name = re.sub(r"^(?:to|the|a|an|in|into|with|more|of)\s+", "", name)
        name = re.sub(r"^(?:the|a|an)\s+", "", name)
        r = resolve(name.replace(" ", "_"))
        if r in STYLE_PRESETS or r in THEMES:
            return r
        r = ALIASES.get(name) or THEME_ALIASES.get(name)
        if r:
            return r
    best, score = None, 0
    for tid, t in THEMES.items():
        n = _rx(t["words"], s)
        if n > score:
            best, score = tid, n
    if best:
        return best
    base = explicit(text)
    return resolve(base) if base else None


def pick_theme(prompt: str, content: str = "", fmt: str = "") -> tuple[str, str]:
    """Choose a theme from the person's words first, then the story's content. Returns (id, one-line reason) for the plan.
    Named looks win; then content cues ("GitHub repo" -> crt, "chip / robot" -> hologram_hud, "this week" -> news_package);
    then the base styles' intent words; default huhbyte_news."""
    named = explicit_theme(prompt)
    if named:
        lab = (THEMES.get(named) or STYLE_PRESETS.get(named) or {}).get("label", named)
        return named, f"you asked for {lab}"
    base = pick(prompt)
    if base != "huhbyte_news":  # "short punchy" -> opus, "hype" -> hype, "tutorial" -> minimal: their words beat the story
        return base, f"your words suggest {STYLE_PRESETS[base]['label']}"
    s = (content or "").lower()
    best, score = None, 0
    for tid, t in THEMES.items():
        n = _rx(t["content"], s)
        if n > score:
            best, score = tid, n
    if best and score >= 2:
        return best, f"{THEMES[best]['label']} fits the story ({THEMES[best]['best_for'][0]})"
    return "huhbyte_news", "HuhByte news fits a launch or announcement (the full video in a panel with a facts card)"


_FX_ORDER = ["misreg", "curve", "scanlines", "vignette", "flicker", "grain"]


def footage_chain(cfg: dict, W: int = 1080) -> str:
    """The theme's grade + picture effects as one FFmpeg filter chain, applied to the footage only (never to type).
    weave, tiltshift, sweep, letterbox, clock and timecode need their own graph pieces (compose._main_graph builds them)."""
    k = W / 1080
    parts = [cfg["grade"]] if cfg.get("grade") else []
    fx = {f.partition(":")[0]: f.partition(":")[2] for f in cfg.get("fx") or []}
    for name in _FX_ORDER:
        if name not in fx:
            continue
        arg = fx[name]
        if name == "grain":
            parts.append(f"noise=alls={int(arg or 6)}:allf=t+u")
        elif name == "vignette":
            parts.append("vignette=angle=PI/4.6")
        elif name == "flicker":  # projector flicker: two unrelated sines so it never looks like a pulse
            parts.append("eq=brightness='0.022*sin(2*PI*t*2.7)+0.014*sin(2*PI*t*11.3)':eval=frame")
        elif name == "scanlines":
            parts.append(f"drawgrid=w=iw:h={max(3, round(4 * k))}:t=1:c=black@0.32")
        elif name == "curve":  # CRT glass bulge
            parts.append("lenscorrection=k1=-0.07:k2=-0.015")
        elif name == "misreg":  # riso: the second plate a few pixels off
            parts.append(f"rgbashift=rh={-round(6 * k)}:rv={-round(3 * k)}:bh={round(5 * k)}:bv={round(4 * k)}:edge=smear")
    return ",".join(parts)


def has_fx(cfg: dict, name: str) -> bool:
    return any(f.partition(":")[0] == name for f in cfg.get("fx") or [])


def transition_chain(cfg: dict) -> str:
    """Into the footage: cut | flash (white) | fade (from the ground) | glitch (RGB split + noise for 0.18 s)."""
    tr = cfg.get("transition") or ("flash" if cfg.get("flash") else "cut")
    if tr == "flash":
        return "fade=t=in:st=0:d=0.2:color=white"
    if tr == "fade":
        return "fade=t=in:st=0:d=0.35"
    if tr == "glitch":
        return "rgbashift=rh=16:bh=-16:enable='lt(t,0.18)',noise=alls=45:allf=t:enable='lt(t,0.18)'"
    return ""


_NAMED = (r"\b(?:theme|style|look|cinematic|newsreel|keynote|crt|terminal look|silent[\s-]film|riso(?:graph)?|hud|hologram|"
          r"countdown|tilt[\s-]?shift|miniature|game[\s-]?show|caption[\s-]?news|swiss|constructivist|screen\s?cast|"
          r"investigation|news package|opus)\b")


def named_theme(text: str) -> str | None:
    """A look the person NAMED in a prompt or a thread reply ("make it more cinematic", "switch to the news theme",
    "like opus clip", "crt look") -> its id; None for anything that is only a topic or a tone. The fixed planner styles a
    video only when a look is named or picked in the gallery."""
    import re
    s = text or ""
    if not re.search(_NAMED, s, re.I):
        return None
    return explicit_theme(s)
