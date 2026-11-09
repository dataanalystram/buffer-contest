"""Captions in any script: when a caption style's font can't draw the words, use a Noto font that can.

Every display font we bundle (Anton, Figtree, Poppins, Inter, ...) covers Latin only, so a Hindi, Tamil or Arabic
transcript would burn in as empty boxes ("tofu"). Before a caption file is written, ``pick(text, file)`` counts how
many of the caption's letters the style's font has (its cmap, read with fontTools). If some are missing, it picks the
bundled Noto Sans Bold that draws the most of them (each one also carries Latin, so "मैं AI पर" mixed with English
stays in one font). The style keeps its size, colours, pop and placement; only the family changes. Measuring
(Pillow) and drawing (libass, fontsdir = studio/fonts) use the same file, so captions still fit by construction.

Bundled (studio/fonts, SIL OFL 1.1, Google Fonts' static Bold builds, ~1.9 MB together): Noto Sans (Latin extended,
Cyrillic, Greek, Vietnamese), Devanagari (Hindi, Marathi, Nepali), Bengali, Gurmukhi, Gujarati, Tamil, Telugu,
Kannada, Malayalam, Arabic (Arabic, Urdu, Persian), Hebrew, Thai. Chinese / Japanese / Korean are NOT bundled (one
weight is ~10-16 MB): ``missing`` names those characters so the render can say so instead of burning boxes.

The idea (check the font covers the text before rendering) comes from MoneyPrinterTurbo (MIT,
``video.subtitle_font_supports_text``); this module is our own code. Credited in NOTICE.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"

# family name libass matches -> file in studio/fonts. Order = preference on a tie: the script's own font first (Google's
# Noto Sans also carries Devanagari, but the script fonts are the ones made and tested for it), the general one last.
NOTO = {
    "Noto Sans Devanagari": "NotoSansDevanagari-Bold.ttf",
    "Noto Sans Bengali": "NotoSansBengali-Bold.ttf",
    "Noto Sans Gurmukhi": "NotoSansGurmukhi-Bold.ttf",
    "Noto Sans Gujarati": "NotoSansGujarati-Bold.ttf",
    "Noto Sans Tamil": "NotoSansTamil-Bold.ttf",
    "Noto Sans Telugu": "NotoSansTelugu-Bold.ttf",
    "Noto Sans Kannada": "NotoSansKannada-Bold.ttf",
    "Noto Sans Malayalam": "NotoSansMalayalam-Bold.ttf",
    "Noto Sans Arabic": "NotoSansArabic-Bold.ttf",
    "Noto Sans Hebrew": "NotoSansHebrew-Bold.ttf",
    "Noto Sans Thai": "NotoSansThai-Bold.ttf",
    "Noto Sans": "NotoSans-Bold.ttf",
}
FAMILY_OF = {v: k for k, v in NOTO.items()}


@lru_cache(maxsize=64)
def charset(file: str) -> frozenset[int]:
    """Code points the font file can draw (empty when it can't be read: then nothing counts as covered)."""
    p = Path(file)
    if not p.is_absolute():
        p = FONT_DIR / file
    try:
        from fontTools.ttLib import TTFont
        with TTFont(str(p), lazy=True, fontNumber=0) as t:
            return frozenset((t.getBestCmap() or {}).keys())
    except Exception:  # noqa: BLE001 - unreadable font: treat as covering nothing, the caller keeps its own font
        return frozenset()


def _needs_glyph(ch: str) -> bool:
    """Characters a viewer would see as a box if missing: letters, marks, digits, punctuation, symbols. Spaces and
    invisible format characters (ZWJ / ZWNJ, direction marks) don't count."""
    return not ch.isspace() and unicodedata.category(ch) not in ("Cc", "Cf", "Zs", "Zl", "Zp")


def missing(text: str, file: str) -> list[str]:
    """The distinct characters of ``text`` the font can't draw, in order of first use."""
    have, seen, out = charset(file), set(), []
    for ch in text or "":
        if ch not in seen and _needs_glyph(ch) and ord(ch) not in have:
            seen.add(ch)
            out.append(ch)
    return out


def _covered(text: str, file: str) -> int:
    have = charset(file)
    return sum(1 for ch in text if _needs_glyph(ch) and ord(ch) in have)


def pick(text: str, file: str, family: str = "") -> tuple[str, str] | None:
    """(family, file) of the bundled Noto font to use instead of ``file`` for ``text``, or None to keep the style's
    own font (it draws everything, or no bundled font draws more of it, e.g. emoji or Chinese)."""
    if not text or not missing(text, file):
        return None
    best, best_n = None, _covered(text, file)
    for fam, f in NOTO.items():
        n = _covered(text, f)
        if n > best_n:
            best, best_n = (fam, f), n
    return best


MAX_GROW = 1.6


@lru_cache(maxsize=64)
def _line_height(file: str) -> int:
    from PIL import ImageFont
    p = Path(file)
    a, d = ImageFont.truetype(str(p if p.is_absolute() else FONT_DIR / file), 100).getmetrics()
    return max(1, a + d)


def size_ratio(new_file: str, old_file: str) -> float:
    """How much bigger the caption size must be in ``new_file`` for its letters to come out as big as in ``old_file``.
    Caption sizes are LINE heights (ASS Fontsize), and Noto fonts reserve tall room for stacked marks (Arabic's line is
    1.7x Inter's), so the same size would draw visibly smaller letters. 1.0-1.6; the fit-to-width step still shrinks a
    line that would get too wide."""
    try:
        return round(min(MAX_GROW, max(1.0, _line_height(new_file) / _line_height(old_file))), 3)
    except Exception:  # noqa: BLE001
        return 1.0


def note(text: str, file: str) -> str:
    """One plain sentence when some characters still have no font after ``pick`` (shown with the caption problems),
    else ""."""
    gone = missing(text, file)
    if not gone:
        return ""
    sample = "".join(gone[:6])
    n = len(gone)
    return (f"{n} character{'s' if n != 1 else ''} in the captions ({sample}) {'have' if n != 1 else 'has'} no bundled "
            "font and would show as an empty box (emoji, Chinese, Japanese and Korean aren't covered yet).")
