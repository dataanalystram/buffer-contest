"""One headline guard for every place a headline or video title is set (owner P0, 1 Nov 2026).

The bug: a person's request ("you can do that online search it is https://x.com/... research and have a instgram
reel") was burned into a Reel as its headline. It got there through the pre-agent pipeline (``hook = story headline
or the brief``), and nothing at render time stopped it. Now every headline passes through here, three times:

1. ``safe_headline(headline, prompt, fallbacks)`` where the story is written (agent tools, legacy pipeline): refuses a
   URL, the request's wording, or a long run copied from the prompt, and falls through to the next candidate.
2. ``spec_hook(spec, prompt)`` when a render job is queued, served to the worker and rendered locally (house.py).
3. ``render_hook(text)`` inside the renderers themselves (render.brand_video, the media worker, the GitHub render
   worker): no prompt is known there, so it removes URLs and drops text that reads as an instruction. Last line of
   defence: a hook that reads like a request renders as no headline rather than the request.
"""
from __future__ import annotations

import re

from . import intake

URLISH = re.compile(r"https?\s*:\s*/\s*/\s*\S+|\b(?:www\.)?[\w-]+\.(?:com|ai|io|co|org|net|dev|app|be|ly|gg)/\S*|"
                    r"\b(?:x|twitter|t)\.(?:com|co)\b\S*|/status/\d+|\bstatus/\d{6,}", re.I)
ORDERS = re.compile(r"\b(?:search(?: it| for| online)?|research|look (?:it )?up|post (?:it |this )?on|all (?:my |the )?channels|you can|"
                    r"can you|please|make (?:a|me|it)|have a|reel post|inst?a?g?ram reel|insta(?:gram)? post|do that|online|"
                    r"every ?where|my channels)\b", re.I)
# with no prompt to compare against (render time): wording that only ever appears in a request, never in a headline
REQUEST_ONLY = re.compile(r"\b(?:you can do that|search (?:it|online|for it)|research (?:it|and)|"
                          r"(?:have|generate) (?:a|an) (?:inst?a?g?ram |ig |tiktok |youtube |linkedin )?(?:reel|post|carousel|short)s?\b|"
                          r"(?:make|create|generate) me (?:a|an) |inst?a?g?ram reel|post (?:it|this) (?:on|to|in|everywhere)|"
                          r"(?:on|to|in) all (?:my |the )?channels|with my branding|i want to post|look it up)", re.I)
_WORD = re.compile(r"[a-z0-9$€£₹.%]+")
# a line that tells the app what to do (owner bug B2, 6 Nov 2026: "USE THE ATTACHED UPLOADED IMAGE AS THE MEDIA..." was
# burned onto a photo as its headline): an imperative opening, or wording only a request uses
INSTRUCTION = re.compile(
    r"\b(?:attached|uploaded) (?:image|photo|picture|video|file|media)\b|\bas the (?:media|image|photo|cover)\b|"
    r"\b(?:on|to|for) (?:instagram|insta|linkedin|facebook|tiktok|youtube|threads|bluesky) only\b|"
    r"\bdo not change\b|\bno hashtags\b|\bno extra text\b|\bverbatim\b|\bthis exact caption\b", re.I)
IMPERATIVE = re.compile(r"^\s*(?:please\s+|pls\s+|can you\s+|could you\s+)?(?:use|post|put|make|create|add|attach|upload|"
                        r"write|share|publish|schedule|send|replace|set|edit|change|don'?t|do not)\b", re.I)


def looks_like_instruction(text: str, prompt: str = "") -> bool:
    """True for a headline (or image text) that is the request, not words about the content: request-only wording
    anywhere, or an order ("Use the attached ...") whose opening words are copied from the person's prompt."""
    t = strip_urls(text)
    if not t:
        return False
    if INSTRUCTION.search(t) or REQUEST_ONLY.search(t):
        return True
    hw, pw = _WORD.findall(t.lower()), _WORD.findall((prompt or "").lower())
    if IMPERATIVE.match(t) and pw and len(hw) >= 3 and " ".join(hw[:4]) in " ".join(pw):
        return True
    if prompt and any(m.group(0).lower() in prompt.lower() for m in ORDERS.finditer(t)):
        return True
    return bool(pw) and len(hw) >= 6 and " ".join(hw[:6]) in " ".join(pw) and " ".join(hw[:6]) not in \
        " ".join(_WORD.findall(intake.describe(prompt or "").lower()))


def strip_urls(text: str) -> str:
    t = URLISH.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", t).strip(" -–—:;,.\"'“”|/")


def safe_headline(headline: str, prompt: str = "", fallbacks: tuple | list = ()) -> str:
    """A headline is written from the story, never copied from the person's request. Refuses (falls through to the
    next candidate) anything with request wording or a long run copied from the prompt; URLs are always removed."""
    pw = _WORD.findall((prompt or "").lower())
    own = " ".join(_WORD.findall(intake.describe(prompt or "").lower()))  # their words about the content
    for cand in [headline, *fallbacks]:
        h = strip_urls(cand)
        if len(re.findall(r"\w{2,}", h)) < 2:
            continue
        if prompt and any(m.group(0).lower() in prompt.lower() for m in ORDERS.finditer(h)):
            continue  # the request's own wording ("search it", "post on all channels") is not a headline
        if REQUEST_ONLY.search(h) or INSTRUCTION.search(h):
            continue
        hw = _WORD.findall(h.lower())
        if pw and len(hw) >= 8 and " ".join(hw[:8]) in " ".join(pw) and " ".join(hw[:8]) not in own:
            continue  # a long verbatim run of the request itself (its instructions), not words about the content
        if pw and len(hw) >= 5 and len(pw) >= 5 and " ".join(hw) == " ".join(pw[:len(hw)]) and " ".join(hw) not in own:
            continue  # the request's opening, verbatim
        return h[:120]
    return ""


def render_hook(text: str) -> str:
    """At render time (no prompt known): URLs out; text that reads as an instruction becomes no headline at all."""
    h = strip_urls(text)
    if not h or REQUEST_ONLY.search(h) or INSTRUCTION.search(h) or len(re.findall(r"\w{2,}", h)) < 2:
        return ""
    return h[:120]


def spec_hook(spec: dict, prompt: str = "") -> dict:
    """Clean a render job's hook (and the image prompt built from it) in place; returns the spec."""
    if not isinstance(spec, dict):
        return spec
    for k in ("hook", "image_prompt"):
        if spec.get(k):
            spec[k] = render_hook(safe_headline(spec[k], prompt) if prompt else spec[k])
    return spec


def title(text: str, prompt: str = "", fallback: str = "") -> str:
    """A run's title in lists: the story headline when there is one, else the person's words about the content, never
    the raw request with its URL."""
    return safe_headline(text, prompt, [intake.describe(prompt or "")]) or fallback


_SEP = re.compile(r"\s*:\s+|\s+[–—|]\s+|\s+-\s+")


def cover_hook(text: str, limit: int = 72) -> str:
    """A short hook for a cover or a template's first slide, from a headline that may be a title + subtitle run together
    (owner, 3 Nov 2026: "SCREEN STUDIO 4.0: THE MAC RECORDER THAT EDITS FOR YOU: THE BIGGEST UPDATE YET FOR THE MAC SCREEN
    RECORDER THAT HANDLES ZOOMS..."). Keeps the title and its first subtitle when they fit, else the title; never more than
    two parts; a subtitle that only repeats the title is dropped. One plain headline is kept (the renderer fits it).
    Captions keep the full headline."""
    t = strip_urls(text)
    if not t:
        return ""
    parts = [p.strip(" .") for p in _SEP.split(t) if p.strip(" .")]
    if len(parts) <= 1:
        return t[:120]
    a, b = parts[0], parts[1]
    wa, wb = set(_WORD.findall(a.lower())), set(_WORD.findall(b.lower()))
    if wb and len(wa & wb) / len(wb) >= 0.6:  # the subtitle repeats the title
        b = ""
    two = f"{a}: {b}" if b else a
    if len(two) <= limit:
        return two
    if len(re.findall(r"\w{2,}", a)) >= 2:
        return a[:120]
    return t[:120]
