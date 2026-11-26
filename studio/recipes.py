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


def clean_length(v) -> str:
    return v if isinstance(v, str) and v in LENGTHS else ""


def public() -> dict:
    """For the setup card and the MCP tool description."""
    return {"recipes": [{"id": k, "label": r["label"], "about": r["about"], "length": r["length"],
                         "punch_in": r["punch_in"], "trim_pauses": r["trim_pauses"], "split": r["split"], "cold_open": r["cold_open"]} for k, r in RECIPES.items()],
            "lengths": [{"id": k, "label": v} for k, v in LENGTHS.items()]}
