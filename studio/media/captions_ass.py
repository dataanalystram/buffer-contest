"""Animated word captions as ASS for libass (ffmpeg's ``ass`` filter), placed from the clip's layout sidecar.

* Karaoke timing (``\\k``) from the word timestamps: the word being said takes the accent colour (bold pop, lower
  third), keywords stay accented (two-tone), or words appear as they're said (typewriter).
* Lines: at most 3 words or about 18 characters, one line on screen at a time, a new line at punctuation or once
  0.8 s have passed since the line began. A line that would still be wider than the frame minus 80 px a side is
  scaled down to fit (measured with the bundled font), so nothing ever runs off the edge.
* Motion: a soft entrance (fade + a small grow from 92% to 100%, no overshoot), and an exit at 70% of the entrance
  time. No bounce on text.
* Placement from the sidecar: on the seam of a split (and of a stacked screen), in the face-safe band under a tracked
  face, between the screen card and the face circle of a PiP, under the frame of a fit; always clear of the top
  ~220 px and the bottom ~380 px of a 1920-tall frame (platform UI).
* Fonts: bundled OFL fonts only (studio/fonts, each with its OFL text).
* Contrast: when the background under the captions is light (``bg_luma``, from the clip's frames), and the text or
  the accent (lavender #C9ACFA by default, kept) would read below MIN_CONTRAST against it, the line gets a heavier
  near-black stroke and a firmer drop shadow (solid colours only, no gradients).
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"
REF_W, REF_H = 1080, 1920
SAFE_TOP, SAFE_BOTTOM = 220, 380
SIDE = 80
MAX_WORDS, MAX_CHARS, MAX_LINE_S = 3, 18, 0.8
ENTER_MS = 140
EXIT_MS = int(ENTER_MS * 0.7)   # exits at 65-75% of the entrance time

# preset -> font family (as libass sees it), font file (for measuring), size at 1920 tall, case, colours, box
PRESETS = {
    "bold_pop": {"label": "Bold pop", "family": "Poppins", "file": "Poppins-Bold.ttf", "bold": True, "size": 82, "upper": True,
                 "outline": 6, "shadow": 3, "box": False, "mode": "karaoke"},
    "lower_third": {"label": "Clean lower third", "family": "Inter", "file": "Inter-Bold.ttf", "bold": True, "size": 64, "upper": False,
                    "outline": 14, "shadow": 0, "box": True, "mode": "karaoke"},
    "two_tone": {"label": "Two-tone keyword", "family": "Anton", "file": "Anton.ttf", "bold": False, "size": 92, "upper": True,
                 "outline": 5, "shadow": 2, "box": False, "mode": "keywords"},
    "typewriter": {"label": "Typewriter", "family": "IBM Plex Mono", "file": "IBMPlexMono-SemiBold.ttf", "bold": True, "size": 66,
                   "upper": False, "outline": 5, "shadow": 0, "box": False, "mode": "reveal"},
}
DEFAULT_PRESET = "bold_pop"
STOP = {"the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "is", "it", "that", "this", "with", "as",
        "be", "are", "was", "so", "we", "i", "you", "they", "he", "she", "my", "your", "our", "its", "by", "from", "do", "just"}

MIN_CONTRAST = 3.0     # WCAG ratio for large text: below this the captions get the heavy stroke
STROKE_MIN, SHADOW_MIN = 9, 4   # the heavy stroke and shadow (px at 1920 tall)
STROKE_COLOR = "#0E0E10"

# caption centre line (y at 1920 tall) for each layout
BAND_Y = {"split": 960, "screen_stack": 960, "screen_pip": 1030, "track": 1330, "fit": 1400}


def presets() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in PRESETS.items()]


def clean_preset(v) -> str | None:
    return v if isinstance(v, str) and v in PRESETS else None


# ---------------------------------------------------------------------- text helpers
def _color(hex_: str, alpha: int = 0) -> str:
    h = (hex_ or "#FFFFFF").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def _lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_: str) -> float:
    """WCAG relative luminance of a #RRGGBB colour (white for anything malformed)."""
    h = (hex_ or "").lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = (_lin(int(h[i:i + 2], 16) / 255.0) for i in (0, 2, 4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(hex_: str, bg_luma: float) -> float:
    """WCAG contrast ratio of a colour over a grey background of luma bg_luma (0-1, gamma-encoded)."""
    a, b = luminance(hex_), _lin(min(1.0, max(0.0, float(bg_luma))))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def needs_stroke(colors: list[str], bg_luma: float | None) -> bool:
    """True when any caption colour reads below MIN_CONTRAST on the background (None: unknown, keep the default)."""
    if not isinstance(bg_luma, (int, float)) or isinstance(bg_luma, bool):
        return False
    return min(contrast(c, bg_luma) for c in colors) < MIN_CONTRAST


def _time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{c:02d}"


def _esc(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


@lru_cache(maxsize=16)
def _font(file: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_DIR / file), size)


def text_width(text: str, preset: str = DEFAULT_PRESET, H: int = REF_H, file: str | None = None,
               size: float | None = None) -> float:
    """Pixel width of one caption line in the preset's font at the frame height H (outline included). ``file`` and
    ``size`` (at REF_H): a Noto swap's font and size (script_preset)."""
    pr = PRESETS[preset]
    size = max(8, int(round((size or pr["size"]) * H / REF_H)))
    f = _font(file or pr["file"], size)
    return f.getlength(text) + 2 * pr["outline"] * H / REF_H


def lines(words: list[dict]) -> list[list[dict]]:
    """Caption lines: <= 3 words, <= ~18 characters, broken at punctuation or after 0.8 s."""
    out, cur = [], []
    for w in words:
        t = str(w.get("w", "")).strip()
        if not t:
            continue
        if cur:
            text = " ".join(str(x["w"]).strip() for x in cur + [w])
            if len(cur) >= MAX_WORDS or len(text) > MAX_CHARS or float(w["s"]) - float(cur[0]["s"]) >= MAX_LINE_S:
                out.append(cur)
                cur = []
        cur.append(w)
        if re.search(r"[.?!,;:]$", t):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def layout_at(sidecar: list[dict] | None, t: float) -> dict:
    for s in sidecar or []:
        if s["start"] - 1e-6 <= t < s["end"]:
            return s
    return (sidecar or [{"layout": "track"}])[-1]


def band_y(seg: dict, H: int = REF_H) -> int:
    lay = seg.get("layout", "track")
    key = f"screen_{seg.get('style') or 'pip'}" if lay == "screen" else lay
    y = BAND_Y.get(key, BAND_Y["track"])
    cy = seg.get("caption_y")  # moved off on-screen text (studio/media/ocr.free_caption_y), 1920-tall coordinates
    if isinstance(cy, int) and not isinstance(cy, bool):
        y = cy
    y = min(REF_H - SAFE_BOTTOM - 70, max(SAFE_TOP + 70, y))
    return int(round(y * H / REF_H))


def _keyword(t: str) -> bool:
    n = re.sub(r"[^a-z0-9']", "", t.lower())
    return bool(n) and (n not in STOP) and (len(n) >= 6 or any(ch.isdigit() for ch in n))


# ---------------------------------------------------------------------- the ASS file
def build(words: list[dict], clip_start: float, kit: dict | None = None, p: dict | None = None, preset: str | None = None,
          sidecar: list[dict] | None = None, W: int = REF_W, H: int = REF_H, offset: float = 0.0,
          bg_luma: float | None = None) -> str:
    """ASS for the clip's words (source seconds; the clip's first frame is clip_start - offset). bg_luma: the lightest
    background under the captions (0-1); a light one gets the heavy stroke and shadow (see the contrast note above)."""
    kit, p = kit or {}, p or {}
    pid = clean_preset(preset) or DEFAULT_PRESET
    pr = script_preset(PRESETS[pid], words)
    colors = p.get("colors") or kit.get("colors") or {}
    txt = colors.get("text") or "#FFFFFF"
    acc = colors.get("accent") or "#C9ACFA"
    k = H / REF_H
    size = int(round(pr["size"] * k))
    outline, shadow = max(1, int(round(pr["outline"] * k))), int(round(pr["shadow"] * k))
    max_w = W - 2 * SIDE * W / REF_W
    if pr["mode"] == "reveal":
        primary, secondary = _color(txt), _color(txt, 0xFF)       # unsaid words invisible until their \k starts
    elif pr["mode"] == "keywords":
        primary, secondary = _color(txt), _color(txt)
    else:
        primary, secondary = _color(acc), _color(txt)              # said words take the accent
    back = _color("#000000", 0x99) if pr["box"] else _color("#000000", 0x80)
    out_c = _color("#000000", 0x99) if pr["box"] else _color("#000000")
    if needs_stroke([txt, acc], bg_luma):  # light background: a heavier solid stroke and a firmer shadow
        if pr["box"]:
            out_c, back = _color(STROKE_COLOR, 0x22), _color(STROKE_COLOR, 0x22)
        else:
            outline = max(outline, int(round(STROKE_MIN * k)))
            shadow = max(shadow, int(round(SHADOW_MIN * k)))
            out_c, back = _color(STROKE_COLOR), _color(STROKE_COLOR, 0x30)
    style = (f"Style: Cap,{pr['family']},{size},{primary},{secondary},{out_c},{back},{-1 if pr['bold'] else 0},0,0,0,100,100,0,0,"
             f"{3 if pr['box'] else 1},{outline},{shadow},5,{int(SIDE * W / REF_W)},{int(SIDE * W / REF_W)},0,1")
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n" + style + "\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ev = []
    groups = lines(words)
    base = clip_start - offset
    for gi, g in enumerate(groups):
        s = float(g[0]["s"]) - base
        e_said = float(g[-1]["e"]) - base + 0.25
        nxt = float(groups[gi + 1][0]["s"]) - base if gi + 1 < len(groups) else None
        e = min(e_said, nxt) if nxt is not None else e_said     # one line on screen at a time
        if e <= s + 0.05:
            e = s + 0.3 if nxt is None else max(s + 0.05, nxt)
        toks = [_esc(str(w["w"]).strip()) for w in g]
        if pr["upper"]:
            toks = [t.upper() for t in toks]
        width = text_width(" ".join(toks), pid, H, file=pr["file"], size=pr["size"])
        fit = min(100, int(100 * max_w / width)) if width > max_w else 100
        y = band_y(layout_at(sidecar, s + 0.01), H)
        body = []
        for i, (w, t) in enumerate(zip(g, toks)):
            nxt_s = float(g[i + 1]["s"]) if i + 1 < len(g) else float(w["e"])
            dur_cs = max(1, int(round((nxt_s - float(w["s"])) * 100)))
            lead = ""
            if i == 0:
                gap = max(0, int(round((float(w["s"]) - float(g[0]["s"])) * 100)))
                lead = f"{{\\k{gap}}}" if gap else ""
            tag = f"\\k{dur_cs}"
            if pr["mode"] == "keywords" and _keyword(t):
                tag += f"\\c{_color(acc)}"
            elif pr["mode"] == "keywords":
                tag += f"\\c{_color(txt)}"
            body.append(f"{lead}{{{tag}}}{t}")
        grow = f"\\fscx{int(fit * 0.92)}\\fscy{int(fit * 0.92)}\\t(0,{ENTER_MS},\\fscx{fit}\\fscy{fit})"
        intro = f"{{\\an5\\pos({W // 2},{y})\\fad({ENTER_MS},{EXIT_MS}){grow}}}"
        ev.append(f"Dialogue: 0,{_time(s)},{_time(e)},Cap,,0,0,0,,{intro}{' '.join(body)}")
    return head + "\n".join(ev) + ("\n" if ev else "")


def script_preset(pr: dict, words: list[dict] | None) -> dict:
    """The preset with a Noto family + file when its font can't draw the words (Hindi, Tamil, Arabic ...;
    studio/media/fontcover). Same size, colours and motion; Bold, the weight of the bundled Noto files."""
    from . import fontcover
    swap = fontcover.pick(" ".join(str(w.get("w", "")) for w in words or []), pr["file"], pr["family"])
    if not swap:
        return pr
    k = fontcover.size_ratio(swap[1], pr["file"])  # same letter size as the preset's own font
    return {**pr, "family": swap[0], "file": swap[1], "bold": True, "size": int(round(pr["size"] * k))}


def line_boxes(ass: str, preset: str = DEFAULT_PRESET, W: int = REF_W, H: int = REF_H) -> list[dict]:
    """[{text, width, y, scale}] for every Dialogue line: the effective drawn width after the fit scale (for tests and
    the render's self-check)."""
    from . import fontcover
    fam = re.search(r"^Style: Cap,([^,]+),", ass, re.M)
    file = fontcover.NOTO.get(fam.group(1).strip()) if fam else None  # measured in the Noto font it draws with
    size = None
    if file:
        st = re.search(r"^Style: Cap,[^,]+,(\d+),", ass, re.M)
        size = int(st.group(1)) * REF_H / H if st else None
    out = []
    for ln in ass.splitlines():
        if not ln.startswith("Dialogue:"):
            continue
        text = ln.split(",,", 2)[-1]
        m = re.search(r"\\pos\((\d+),(\d+)\)", text)
        sc = re.search(r"\\t\(0,\d+,\\fscx(\d+)", text)
        plain = re.sub(r"\{[^}]*\}", "", text)
        scale = int(sc.group(1)) if sc else 100
        out.append({"text": plain, "width": text_width(plain, preset, H, file=file, size=size) * scale / 100, "y": int(m.group(2)) if m else None,
                    "scale": scale})
    return out
