"""The edit styles an agent (or the UI) can pick, described in plain words plus the knobs compose.py reads.

Pick by intent: "short punchy explainer" -> opus or hype; "news / announcement" -> huhbyte_news; "calm walkthrough,
tutorial, B2B" -> minimal. Every style renders 9:16, 4:5 and 1:1 and always ends with the Follow end card.
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
