"""Burned-in caption styles as ASS subtitles (rendered by libass inside FFmpeg's ``ass`` filter).

Four looks, all driven by word timestamps ({"w","s","e"}):

  opus      Opus-Clip-style: 1-3 words at a time, huge heavy type, the word being said pops (scale 118% -> 100%) in
            the accent colour, thick outline. Centre-low, above the platform UI.
  news      HuhByte news: a short sentence chunk (<= 2 lines) under the video panel, current word in lavender.
  minimal   Clean explainer: sentence chunks in Inter SemiBold on a soft translucent box, bottom third.
  hype      One word at a time, giant Anton, alternating white/accent, punch-in with a small tilt.

No clipping, guaranteed by construction: every chunk is measured with Pillow using the SAME font file libass loads
(fontsdir = studio/fonts), wrapped by us (WrapStyle 2 = libass never re-wraps), and its size is reduced until every
line fits the safe width with a margin. ASS font size is the font's line height (ascent+descent), so the Pillow size
is converted before it is written. ``check_ass_fits`` re-measures a finished file for tests.

``fix_names`` repairs product names small Whisper models mishear ("So vocal" -> "Subvocal").
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from . import textfit

FONTS = {  # family name libass matches -> file in studio/fonts
    "Anton": "Anton.ttf", "Figtree Light ExtraBold": "Figtree-ExtraBold.ttf", "Poppins": "Poppins-Bold.ttf",
    "Inter SemiBold": "Inter-SemiBold.ttf", "Inter": "Inter-Bold.ttf", "Oswald SemiBold": "Oswald-SemiBold.ttf",
    "DM Serif Display": "DMSerifDisplay-Regular.ttf", "VT323": "VT323-Regular.ttf",
    "IBM Plex Mono SemiBold": "IBMPlexMono-SemiBold.ttf",
}


@dataclass
class CapStyle:
    name: str
    family: str
    bold: bool
    upper: bool
    max_words: int
    max_chars: int
    size_frac: float        # target line height as a fraction of frame height
    min_frac: float
    center_y_frac: float    # vertical centre of the caption block
    width_frac: float       # max text width as a fraction of frame width
    max_lines: int
    outline_frac: float
    box: bool = False       # translucent box behind (BorderStyle 4 style via 3)
    pop: bool = False
    one_word: bool = False
    tilt: bool = False
    # theme extras (studio/media/styles.py THEMES): colours are palette roles (text / accent / bg / on_accent / muted) or hex
    align: str = "center"   # "left": flush-left at the safe margin (Swiss, news strap)
    block: bool = False     # the spoken word sits on an accent block (thick accent border, ink text): Caption News
    box_color: str = ""     # BorderStyle 3 box colour (role or hex) instead of translucent black
    box_alpha: int = 0x70
    text_role: str = "text"
    accent_role: str = "accent"
    outline_role: str = "bg"
    blur: float = 0.0       # \blur glow (CRT phosphor, HUD)
    shadow: int = -1        # -1: the default (2, or 0 with a box)
    alternate: bool = False  # one_word styles: alternate text / accent per word (hype, telop)


STYLES = {
    "opus": CapStyle("opus", "Figtree Light ExtraBold", False, True, 3, 16, 0.062, 0.035, 0.66, 0.80, 2, 0.11, pop=True),
    "news": CapStyle("news", "Poppins", True, False, 6, 34, 0.036, 0.024, 0.0, 0.84, 2, 0.09),
    "minimal": CapStyle("minimal", "Inter SemiBold", False, False, 7, 38, 0.034, 0.022, 0.74, 0.82, 2, 0.0, box=True),
    "hype": CapStyle("hype", "Anton", False, True, 1, 14, 0.085, 0.04, 0.62, 0.84, 1, 0.10, pop=True, one_word=True, tilt=True,
                     alternate=True),
    # ---- theme caption styles (ported grammar from lemo-opuscar, MIT, LemoLab; see styles.THEMES and NOTICE)
    "slam": CapStyle("slam", "Poppins", True, False, 4, 22, 0.050, 0.030, 0.70, 0.84, 2, 0.05, pop=True, block=True,
                     accent_role="accent", outline_role="bg"),                      # caption-news: chunk slam, yellow keyword block
    "strap": CapStyle("strap", "Inter SemiBold", False, False, 7, 36, 0.031, 0.022, 0.0, 0.70, 2, 0.0, align="left",
                      text_role="bg", accent_role="#7A5E00", shadow=0),  # news-package: ink on the paper strap (theme chrome)
    "serif": CapStyle("serif", "DM Serif Display", False, False, 7, 34, 0.036, 0.024, 0.80, 0.82, 2, 0.0, box=True,
                      box_color="bg", box_alpha=0x28, accent_role="#E9C77E"),     # newsreel / silent film: cream on a dark strip
    "keynote": CapStyle("keynote", "Inter SemiBold", False, False, 6, 34, 0.034, 0.024, 0.80, 0.84, 2, 0.04, shadow=3),
    "swiss": CapStyle("swiss", "Inter", True, False, 5, 28, 0.036, 0.024, 0.80, 0.84, 2, 0.0, align="left", shadow=0),
    "kino": CapStyle("kino", "Oswald SemiBold", False, True, 3, 18, 0.050, 0.030, 0.68, 0.84, 2, 0.0, box=True,
                     box_color="accent", box_alpha=0, text_role="text", accent_role="bg"),  # paper caps on the red band
    "mono": CapStyle("mono", "IBM Plex Mono SemiBold", False, False, 5, 26, 0.032, 0.022, 0.80, 0.84, 2, 0.0, box=True,
                     box_color="bg", box_alpha=0x40, blur=0.6),                       # hologram HUD
    "crt": CapStyle("crt", "VT323", False, True, 4, 20, 0.052, 0.032, 0.80, 0.84, 2, 0.05, blur=2.2, outline_role="muted",
                    shadow=0),                                                        # amber phosphor with glow
    "riso": CapStyle("riso", "Figtree Light ExtraBold", False, True, 3, 16, 0.056, 0.034, 0.68, 0.82, 2, 0.0, pop=True,
                     text_role="accent", accent_role="text", outline_role="text", shadow=6),  # pink + blue double hit
    "sign": CapStyle("sign", "Inter", True, False, 5, 28, 0.034, 0.024, 0.74, 0.82, 2, 0.0, box=True, box_color="#1F6B45",
                     box_alpha=0),                                                    # tilt-shift road sign
    "telop": CapStyle("telop", "Figtree Light ExtraBold", False, True, 1, 14, 0.082, 0.04, 0.62, 0.84, 1, 0.16, pop=True,
                      one_word=True, alternate=True, outline_role="#1B1530"),          # game show: thick ink stroke
    "count": CapStyle("count", "Inter", True, False, 4, 22, 0.044, 0.028, 0.64, 0.84, 2, 0.07, pop=True),
    "doc": CapStyle("doc", "Inter SemiBold", False, False, 7, 36, 0.033, 0.022, 0.80, 0.84, 2, 0.0, box=True,
                    box_color="#141619", box_alpha=0x10),                            # investigation: amber word on a dark panel
    "film": CapStyle("film", "Inter SemiBold", False, False, 8, 40, 0.029, 0.021, 0.74, 0.80, 2, 0.03, shadow=2),
}


# ------------------------------------------------------------------------------------------------ text helpers
def ass_color(hex_: str, alpha: int = 0) -> str:
    h = hex_.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "\u29f5").replace("{", "(").replace("}", ")").replace("\n", " ")


def fix_names(words: list[dict], names: list[str] | None, cutoff: float = 0.74) -> list[dict]:
    """Merge 1-3 adjacent words that sound like a known name into that name (case and spacing from the name)."""
    if not names:
        return words
    keys = [(re.sub(r"[^a-z0-9]", "", n.lower()), n) for n in names if n and " " not in n.strip()]
    out, i = [], 0
    while i < len(words):
        hit = None
        for k in (3, 2, 1):
            if i + k > len(words):
                continue
            joined = re.sub(r"[^a-z0-9]", "", "".join(w["w"] for w in words[i:i + k]).lower())
            for key, name in keys:
                if joined and difflib.SequenceMatcher(None, joined, key).ratio() >= cutoff and abs(len(joined) - len(key)) <= 3:
                    tail = re.sub(r"^.*?([.,!?;:]*)$", r"\1", words[i + k - 1]["w"])
                    hit = ({"w": name + tail, "s": words[i]["s"], "e": words[i + k - 1]["e"]}, k)
                    break
            if hit:
                break
        if hit:
            out.append(hit[0])
            i += hit[1]
        else:
            out.append(words[i])
            i += 1
    return out


def chunk_words(words: list[dict], max_words: int, max_chars: int, gap: float = 0.6) -> list[list[dict]]:
    """Group words into caption chunks: break on sentence punctuation, pauses, and the word/char limits."""
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or len(" ".join(x["w"] for x in cur + [w])) > max_chars
                    or w["s"] - cur[-1]["e"] > gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.!?]$", w["w"]):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


# ------------------------------------------------------------------------------------------------ fitting
def pop_scale(st: CapStyle) -> float:
    """Worst-case width growth of a line during the pop animation (whole line scaled: conservative)."""
    return 1.35 if st.one_word else (1.18 if st.pop else 1.0)


def block_border(st: CapStyle, fs: int) -> int:
    """Border width of the accent block behind the spoken word (0 when the style has no block)."""
    return max(4, int(fs * 0.14)) if st.block else 0


def _role(v: str, pal: dict) -> str:
    return pal.get(v, v) if v and not v.startswith("#") else (v or "#FFFFFF")


def _pil_size_for_line_height(file: str, line_h: int) -> int:
    f = textfit.font(file, 100)
    a, d = f.getmetrics()
    return max(8, int(line_h * 100 / (a + d)))


def fit_chunk(text: str, st: CapStyle, W: int, H: int) -> tuple[int, list[str]]:
    """(ass_fontsize, lines) such that every line fits st.width_frac*W with margin, in <= st.max_lines lines."""
    file = FONTS[st.family]
    maxw = int(W * st.width_frac) - int(H * st.outline_frac * st.size_frac * 2) - 8  # room for the outline both sides
    maxw -= 2 * block_border(st, int(H * st.size_frac)) + (int(H * st.size_frac * 0.5) if st.box else 0)
    maxw = int(maxw / pop_scale(st))  # the pop's first frame is the widest the line ever gets
    target = _pil_size_for_line_height(file, int(H * st.size_frac))
    lowest = _pil_size_for_line_height(file, int(H * st.min_frac))
    r = textfit.fit(text, file, maxw, int(H * st.size_frac * 1.25 * st.max_lines), max_size=target, min_size=lowest,
                    max_lines=st.max_lines, spacing=1.0, upper=st.upper)
    size = r.size
    words = (text.upper() if st.upper else text).split()
    while size > 8:  # captions map whole words to lines, so the longest single word must fit on its own
        f = textfit.font(file, size)
        if max((f.getlength(w) for w in words), default=0) <= maxw:
            break
        size -= 2
    f = textfit.font(file, size)
    lines = textfit.wrap(text.upper() if st.upper else text, f, maxw)
    a, d = f.getmetrics()
    return a + d, lines


def build_ass(words: list[dict], style: str = "opus", *, W: int = 1080, H: int = 1920, accent: str = "#C9ACFA",
              text: str = "#FFFFFF", outline: str = "#0E0E10", offset: float = 0.0, center_y: float | None = None,
              band: tuple[int, int] | None = None, end: float | None = None, palette: dict | None = None,
              left: int | None = None) -> str:
    """ASS document for ``words`` (times in source seconds; ``offset`` is added, e.g. a cover card before the video).
    band=(top, bottom) pins the caption block's centre inside a pixel band (the news style puts it under the video).
    palette: the theme's colour roles (text / accent / bg / on_accent / muted) for the theme caption styles; left: the
    x of flush-left styles (defaults to the safe margin)."""
    st = STYLES[style]
    pal = {"text": text, "accent": accent, "bg": outline, "on_accent": outline, "muted": "#A0A0A8", **(palette or {})}
    text, accent, outline = _role(st.text_role, pal), _role(st.accent_role, pal), _role(st.outline_role, pal)
    on_acc = pal.get("on_accent") or outline
    cy = band and (band[0] + band[1]) // 2 or int(H * (center_y if center_y is not None else st.center_y_frac))
    ol = max(2, int(H * st.size_frac * st.outline_frac))
    box_col = ass_color(_role(st.box_color, pal), st.box_alpha) if st.box_color else ass_color("#000000", 0x70)
    back = box_col if st.box else (ass_color(text if style == "riso" else outline, 0x50 if style != "riso" else 0))
    border_style = 3 if st.box else 1
    lx = left if left is not None else int(W * (1 - st.width_frac) / 2)
    head = (
        f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: TV.709\n\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding\n"
        f"Style: Cap,{st.family},{int(H * st.size_frac)},{ass_color(text)},{ass_color(accent)},"
        f"{ass_color(outline) if not st.box else box_col},{back},{-1 if st.bold else 0},0,0,0,100,100,0,0,"
        f"{border_style},{ol if not st.box else int(H * 0.008)},{(0 if st.box else 2) if st.shadow < 0 else st.shadow},"
        f"5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    chunks = [[w] for w in words] if st.one_word else chunk_words(words, st.max_words, st.max_chars)
    for ci, ch in enumerate(chunks):
        raw = " ".join(w["w"].strip() for w in ch)
        fs, wrapped = fit_chunk(raw, st, W, H)
        # map each word to its line, keeping our wrap (\N) so libass never wraps on its own
        toks = [(t.upper() if st.upper else t) for t in (w["w"].strip() for w in ch)]
        line_of, li, used = [], 0, ""
        for t in toks:
            cand = (used + " " + t).strip()
            if li < len(wrapped) and len(cand) > len(wrapped[li]) and used:
                li, used = min(li + 1, len(wrapped) - 1), t
            else:
                used = cand
            line_of.append(li)
        glow = f"\\blur{st.blur:g}" if st.blur else ""
        pos = (f"\\an4\\pos({lx},{cy})\\fs{fs}{glow}" if st.align == "left" else f"\\an5\\pos({W // 2},{cy})\\fs{fs}{glow}")
        bb = block_border(st, fs)
        nxt_start = chunks[ci + 1][0]["s"] if ci + 1 < len(chunks) else None
        for i, w in enumerate(ch):
            s = w["s"] + offset
            e = (ch[i + 1]["s"] if i + 1 < len(ch) else min(w["e"] + 0.25, (nxt_start or 1e9))) + offset
            if end is not None:
                if s >= end:
                    continue
                e = min(e, end)
            if e - s < 0.06:
                e = s + 0.06
            parts = []
            for j, t in enumerate(toks):
                sep = "" if j == 0 else ("\\N" if line_of[j] != line_of[j - 1] else " ")
                t2 = ass_escape(t)
                if j == i and bb:  # the spoken word on an accent block, ink text (Caption News keyword block)
                    eff = ("{\\c" + ass_color(on_acc) + "\\3c" + ass_color(accent) + f"\\bord{bb}\\shad0"
                           + ("\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100)" if st.pop else "") + "}")
                    pad = "\\h" if sep != "\\N" and j else ""
                    parts.append(sep + pad + eff + t2 + "{\\c" + ass_color(text) + "\\3c" + ass_color(outline) + f"\\bord{ol}"
                                 + "\\fscx100\\fscy100}" + ("\\h" if j + 1 < len(toks) and line_of[j + 1] == line_of[j] else ""))
                elif j == i:
                    eff = ("{\\c" + ass_color(accent) + ("\\fscx118\\fscy118\\t(0,90,\\fscx100\\fscy100)" if st.pop else "") + "}")
                    parts.append(sep + eff + t2 + "{\\c" + ass_color(text) + "\\fscx100\\fscy100}")
                else:
                    parts.append(sep + t2)
            body = "".join(parts)
            if st.one_word:
                col = accent if (ci % 2 and st.alternate) else text
                tilt = (f"\\frz{(-3 if ci % 2 else 3)}" if st.tilt else "")
                body = ("{\\c" + ass_color(col) + "\\fscx135\\fscy135" + tilt + "\\t(0,110,\\fscx100\\fscy100)}"
                        + ass_escape(toks[0]))
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Cap,,0,0,0,,{{{pos}}}{body}")
    return head + "\n".join(lines) + "\n"


def check_ass_fits(ass: str, W: int, H: int, margin: int = 24) -> list[str]:
    """Re-measure every dialogue line of a built ASS file at its widest animation frame; returns problems."""
    family = re.search(r"^Style: Cap,([^,]+),", ass, re.M).group(1)
    named = re.search(r"^; huhqueue-style: (\w+)", ass, re.M)
    st = STYLES[named.group(1)] if named and named.group(1) in STYLES else next(v for v in STYLES.values() if v.family == family)
    file = FONTS[st.family]
    probs = []
    for ln in re.findall(r"^Dialogue: .*$", ass, re.M):
        fs = int(re.search(r"\\fs(\d+)", ln).group(1))
        x = int(re.search(r"\\pos\((\d+),", ln).group(1))
        left = "\\an4" in ln
        txt = re.sub(r"\{[^}]*\}", "", ln.split(",,", 1)[1].split(",", 4)[-1])
        f = textfit.font(file, _pil_size_for_line_height(file, fs))
        pad = 2 * max(2, int(H * st.size_frac * st.outline_frac)) + 2 * block_border(st, fs) + (int(fs * 0.3) if st.box else 0)
        for row in txt.split("\\N"):
            w = f.getlength(row) * pop_scale(st) + pad
            x0, x1 = (x - pad / 2, x + w - pad / 2) if left else (x - w / 2, x + w / 2)
            if x0 < margin or x1 > W - margin:
                probs.append(f"{row!r} is {w:.0f}px wide at x={x} (frame {W}px)")
    return probs
