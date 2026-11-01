"""One take -> a week: the media engine.

A long video you own becomes 3-7 short vertical clips of the best things you already said. Extract, don't invent:
every clip is a span of your own transcript, its hook is a line you actually said, and nothing is generated that
you didn't say.

Pure parts (tested without any ML libraries) and heavy parts (run on the video worker, never the web dyno):

  analyze(path)            probe -> silences (ffmpeg silencedetect) -> transcript with word timestamps (faster-whisper,
                           CPU int8, `small` by default, Silero VAD on) -> scene cuts (PySceneDetect, ffmpeg fallback)
                           -> a face track sampled at 2 fps (MediaPipe Face Detector, none if unavailable)
  mine_moments(...)        sentences -> candidate windows (20-60 s, on sentence boundaries) -> LLM scoring through the
                           app's provider abstraction (llm.resolve/chat_json), validated span by span against the
                           transcript, or a deterministic scorer when no model is configured or the model misbehaves
  crop_plan(...)           9:16 window per clip from the face track: dead-zone + smoothing, hard cut on scene changes,
                           blurred-pad layout when there is no face
  captions_ass(...)        burned-in captions as ASS (libass): brand font and colours, current word highlighted, kept
                           above the bottom 20% (Instagram's UI covers it)
  render_clip(...)         FFmpeg: cut -> reframe -> captions -> logo -> branded cover frame first -> end card last
  open_slots / plan_spread the week's open Buffer posting slots and a no-double-booking placement

Third-party pieces are called as libraries or separate programs and credited in NOTICE.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

FONT_DIR = Path(__file__).parent / "fonts"
CLIP_W, CLIP_H = int(os.environ.get("HQ_CLIP_W", "720")), int(os.environ.get("HQ_CLIP_H", "1280"))
MIN_CLIP, MAX_CLIP, TARGET_CLIP = 20.0, 60.0, 38.0
COVER_SECS = 0.5            # branded cover frame prepended to every clip; thumbnailOffset points into it
END_SECS = 2.0
SAFE_BOTTOM = 0.20          # bottom 20% stays clear of captions (Instagram/TikTok UI)
FACE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
FILLER = {"um", "uh", "erm", "uhm", "hmm", "like", "basically", "actually", "literally"}
WEAK_START = {"and", "but", "so", "because", "or", "which", "that", "then", "also", "well"}
HOOK_WORDS = {"why", "how", "what", "secret", "mistake", "never", "always", "best", "worst", "first", "only", "most",
              "surprising", "important", "problem", "question", "imagine", "you", "your", "biggest", "real", "truth"}
ASS_FONT = {"anton": "Anton", "poppins": "Poppins", "inter": "Inter"}


class TakeError(Exception):
    pass


# ====================================================================== transcript -> sentences
def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9']+", " ", (s or "").lower()).strip()


def sentences(words: list[dict]) -> list[dict]:
    """Group timed words into sentences. Each sentence: {i, s, e, text, words:[...]} (times in seconds)."""
    out, cur = [], []
    for w in words:
        if not str(w.get("w", "")).strip():
            continue
        cur.append(w)
        t = str(w["w"]).strip()
        if t.endswith((".", "?", "!")) or len(cur) >= 40:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    res = []
    for i, ws in enumerate(out):
        res.append({"i": i, "s": float(ws[0]["s"]), "e": float(ws[-1]["e"]), "text": " ".join(str(w["w"]).strip() for w in ws),
                    "words": ws})
    return res


def span_text(sents: list[dict], a: int, b: int) -> str:
    return " ".join(s["text"] for s in sents[a:b + 1])


def words_in(words: list[dict], s: float, e: float) -> list[dict]:
    return [w for w in words if w["s"] >= s - 0.05 and w["e"] <= e + 0.05]


def verbatim(quote: str, transcript: str) -> bool:
    """A hook may only use words the speaker said, in order (punctuation and case ignored)."""
    q, t = norm(quote), norm(transcript)
    return bool(q) and len(q.split()) >= 3 and f" {q} " in f" {t} "


# ====================================================================== candidate windows + scoring
def candidates(sents: list[dict], min_s: float = MIN_CLIP, max_s: float = MAX_CLIP) -> list[tuple[int, int]]:
    out = []
    for a in range(len(sents)):
        for b in range(a, len(sents)):
            d = sents[b]["e"] - sents[a]["s"]
            if d > max_s:
                break
            if d >= min_s:
                out.append((a, b))
    return out


def heuristic_score(sents: list[dict], a: int, b: int, scenes: list[float] | None = None) -> float:
    """Deterministic score 0-100: a strong first line, standalone start, clean end, pace, little filler, length near
    the sweet spot. Same input -> same output (the fallback when no model is configured)."""
    first = norm(sents[a]["text"]).split()
    text = norm(span_text(sents, a, b)).split()
    if not text:
        return 0.0
    dur = sents[b]["e"] - sents[a]["s"]
    score = 50.0
    score += 6 * min(3, sum(1 for w in first[:14] if w in HOOK_WORDS))
    if sents[a]["text"].strip().endswith("?"):
        score += 8
    if any(ch.isdigit() for ch in sents[a]["text"]):
        score += 5
    if first and first[0] in WEAK_START:
        score -= 14
    if not sents[b]["text"].strip().endswith((".", "!", "?")):
        score -= 8
    filler = sum(1 for w in text if w in FILLER) / len(text)
    score -= 120 * filler
    wps = len(text) / max(dur, 1)
    score += max(-10, min(8, (wps - 2.0) * 8))
    score -= abs(dur - TARGET_CLIP) * 0.35
    if scenes:
        if any(abs(sc - sents[a]["s"]) < 0.8 for sc in scenes):
            score += 3
    return round(max(0.0, min(100.0, score)), 1)


def target_count(duration: float, want: int | None = None) -> int:
    if want:
        return max(1, min(7, int(want)))
    return max(3, min(7, round(duration / 150) + 2))


def _non_overlapping(picks: list[dict], n: int) -> list[dict]:
    chosen = []
    for p in sorted(picks, key=lambda x: -x["score"]):
        if all(p["e"] <= c["s"] or p["s"] >= c["e"] for c in chosen):
            chosen.append(p)
        if len(chosen) == n:
            break
    return sorted(chosen, key=lambda x: x["s"])


def best_line(sents: list[dict], a: int, b: int, max_chars: int = 90) -> str:
    """The hook for the cover: the first sentence of the span, cut on a word boundary. Always verbatim."""
    t = sents[a]["text"].strip()
    if len(t) <= max_chars:
        return t
    cut = t[:max_chars].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"


def heuristic_moments(sents: list[dict], n: int, scenes: list[float] | None = None) -> list[dict]:
    picks = []
    for a, b in candidates(sents):
        picks.append({"a": a, "b": b, "s": sents[a]["s"], "e": sents[b]["e"], "score": heuristic_score(sents, a, b, scenes),
                      "hook": best_line(sents, a, b), "why": "Strong opening line, clean ending, steady pace (built-in scorer)."})
    chosen = _non_overlapping(picks, n)
    if not chosen and sents:  # a short take: offer the whole thing (up to MAX_CLIP)
        b = 0
        while b + 1 < len(sents) and sents[b + 1]["e"] - sents[0]["s"] <= MAX_CLIP:
            b += 1
        chosen = [{"a": 0, "b": b, "s": sents[0]["s"], "e": sents[b]["e"], "score": heuristic_score(sents, 0, b, scenes),
                   "hook": best_line(sents, 0, b), "why": "The take is short, so the whole span."}]
    return chosen


MINER_SYSTEM = ("You are a short-form video editor. You find the moments in a creator's own talk that work as standalone "
                "vertical clips. You never invent words: hooks are quoted exactly from the transcript.")


def miner_prompt(sents: list[dict], n: int) -> str:
    lines = [f"[{s['i']}] ({s['s']:.1f}-{s['e']:.1f}s) {s['text']}" for s in sents]
    return (f"Pick the {n} best moments for {int(MIN_CLIP)}-{int(MAX_CLIP)} second clips. A moment is a run of whole sentences "
            "[start..end] that makes sense on its own, opens with a strong line and ends cleanly. Moments must not overlap. "
            "Return JSON {\"moments\": [{\"start\": <sentence index>, \"end\": <sentence index>, \"hook\": <an exact quote of "
            "3-14 words from those sentences>, \"why\": <one short reason>, \"score\": <0-100>}]}.\nTRANSCRIPT:\n" + "\n".join(lines))


def validate_llm(data, sents: list[dict], n: int) -> list[dict]:
    """Keep only spans that exist in the transcript, last 15-75 s, don't overlap, and whose hook is verbatim."""
    items = data.get("moments") if isinstance(data, dict) else data
    out = []
    for m in items or []:
        try:
            a, b = int(m["start"]), int(m["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= a <= b < len(sents)):
            continue
        dur = sents[b]["e"] - sents[a]["s"]
        if not (15 <= dur <= 75):
            continue
        text = span_text(sents, a, b)
        hook = str(m.get("hook") or "").strip().strip('"“”')
        if not verbatim(hook, text):
            hook = best_line(sents, a, b)
        try:
            score = float(m.get("score", 70))
        except (TypeError, ValueError):
            score = 70.0
        out.append({"a": a, "b": b, "s": sents[a]["s"], "e": sents[b]["e"], "score": round(max(0, min(100, score)), 1),
                    "hook": hook[:120], "why": str(m.get("why") or "")[:200]})
    return _non_overlapping(out, n)


def snap(t: float, marks: list[float], window: float) -> float:
    best = min(marks, key=lambda m: abs(m - t), default=None) if marks else None
    return best if best is not None and abs(best - t) <= window else t


def finalize(m: dict, sents: list[dict], silences: list[list[float]], scenes: list[float], duration: float) -> dict:
    """Clip edges: start a hair before the first word and end a hair after the last, snapped to a pause or a cut."""
    s, e = sents[m["a"]]["s"], sents[m["b"]]["e"]
    sil_ends = [x[1] for x in silences or []]
    sil_starts = [x[0] for x in silences or []]
    s2 = snap(s - 0.12, sil_ends + list(scenes or []), 0.5)
    e2 = snap(e + 0.25, sil_starts + list(scenes or []), 0.6)
    s2 = max(0.0, min(s2, s - 0.02))
    e2 = min(duration or e + 1, max(e2, e + 0.05))
    return {**m, "start": round(s2, 2), "end": round(e2, 2), "duration": round(e2 - s2, 2),
            "text": span_text(sents, m["a"], m["b"])}


def mine_moments(analysis: dict, want: int | None = None, engine: dict | None = None, chat_json=None) -> dict:
    """Pick the best moments. With an engine (the app's provider abstraction) the model scores and picks; every span it
    returns is validated against the transcript, and anything invalid falls back to the deterministic scorer."""
    words = analysis.get("words") or []
    sents = sentences(words)
    if not sents:
        raise TakeError("No speech found in this video, so there is nothing to clip. One take works on talks, "
                        "podcasts, tutorials and interviews.")
    duration = float((analysis.get("probe") or {}).get("duration") or sents[-1]["e"])
    n = target_count(duration, want)
    picks, how = [], "built-in scorer"
    if engine and chat_json:
        try:
            data = chat_json(engine, MINER_SYSTEM, miner_prompt(sents, n))
            picks = validate_llm(data, sents, n)
            how = engine.get("source", "AI model")
        except Exception as e:  # noqa: BLE001 - any model failure falls back to the deterministic scorer
            how = f"built-in scorer (model failed: {str(e)[:80]})"
            picks = []
    if len(picks) < min(n, 3):
        extra = heuristic_moments(sents, n, analysis.get("scenes"))
        merged = _non_overlapping(picks + [x for x in extra if all(x["e"] <= p["s"] or x["s"] >= p["e"] for p in picks)], n)
        if picks and how != "built-in scorer":
            how += " + built-in scorer"
        picks = merged
    moments = [finalize(m, sents, analysis.get("silences") or [], analysis.get("scenes") or [], duration) for m in picks]
    for i, m in enumerate(moments):
        m["id"] = f"m{i + 1}"
    return {"moments": moments, "picked_by": how, "sentences": len(sents), "target": n}


# ====================================================================== reframe (9:16 crop plan)
def crop_plan(faces: list[dict], start: float, end: float, src_w: int, src_h: int, scenes: list[float] | None = None,
              out_ratio: float = 9 / 16) -> dict:
    """Where the 9:16 window sits over time. faces: [{t, cx, cy, w, score}] with cx/cy/w as 0-1 of the frame.
    Dead-zone: the window only moves when the face leaves its middle 30%; moves ease over 0.4 s; scene cuts jump.
    No face on most samples -> 'pad' (blurred background, whole frame centred)."""
    cw = min(src_w, int(round(src_h * out_ratio / 2)) * 2)
    if cw >= src_w:  # already vertical (or narrower): nothing to track
        return {"mode": "fit", "crop_w": src_w, "keys": [(0.0, 0)]}
    span = [f for f in faces or [] if start <= f["t"] <= end and f.get("score", 1) >= 0.7]
    samples = max(1, int((end - start) * 2))
    if len(span) < 0.35 * samples:
        return {"mode": "pad", "crop_w": cw, "keys": [(0.0, (src_w - cw) // 2)], "face_ratio": round(len(span) / samples, 2)}
    max_x = src_w - cw
    cuts = [c - start for c in scenes or [] if start < c < end]
    keys: list[tuple[float, int]] = []
    cur = None
    # median of a 1.5 s window to steady jitter
    for k in range(int((end - start) * 2) + 1):
        t = k / 2
        win = [f["cx"] for f in span if abs((f["t"] - start) - t) <= 0.75]
        if not win:
            continue
        cx = sorted(win)[len(win) // 2] * src_w
        target = int(min(max_x, max(0, cx - cw / 2)))
        if cur is None:
            cur = target
            keys.append((0.0, cur))
            continue
        jumped = any(abs(c - t) <= 0.5 for c in cuts)
        face_in_window = (cx - cur) / cw
        if jumped and abs(target - cur) > cw * 0.08:
            keys.append((max(0.0, t - 0.001), cur))
            cur = target
            keys.append((t, cur))
        elif not (0.35 <= face_in_window <= 0.65):
            keys.append((t, cur))
            cur = target
            keys.append((t + 0.4, cur))
    # drop no-op keys
    slim: list[tuple[float, int]] = []
    for t, x in keys:
        if slim and slim[-1][1] == x and len(slim) > 1 and slim[-2][1] == x:
            slim[-1] = (t, x)
        else:
            slim.append((round(t, 3), x))
    return {"mode": "track", "crop_w": cw, "keys": slim[:80] or [(0.0, max_x // 2)], "face_ratio": round(len(span) / samples, 2)}


def crop_x_at(plan: dict, t: float) -> float:
    keys = plan["keys"]
    if t <= keys[0][0]:
        return keys[0][1]
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return x0 if t1 == t0 else x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def crop_expr(plan: dict) -> str:
    """ffmpeg expression for the crop x over clip time t (piecewise linear between keys)."""
    keys = plan["keys"]
    if len(keys) == 1:
        return str(int(keys[0][1]))
    expr = str(int(keys[-1][1]))
    for (t0, x0), (t1, x1) in reversed(list(zip(keys, keys[1:]))):
        seg = f"{x0}" if t1 == t0 or x0 == x1 else f"({x0}+({x1}-{x0})*(t-{t0})/({t1}-{t0}))"
        expr = f"if(lt(t\\,{t1})\\,{seg}\\,{expr})"
    return f"if(lt(t\\,{keys[0][0]})\\,{keys[0][1]}\\,{expr})"


def face_coverage(plan: dict, faces: list[dict], start: float, end: float, src_w: int) -> float:
    """Share of face samples in the clip whose centre falls inside the crop window (the reframe test)."""
    span = [f for f in faces or [] if start <= f["t"] <= end and f.get("score", 1) >= 0.7]
    if not span or plan["mode"] != "track":
        return 1.0 if plan["mode"] in ("fit", "pad") else 0.0
    inside = 0
    for f in span:
        x = crop_x_at(plan, f["t"] - start)
        cx = f["cx"] * src_w
        inside += x <= cx <= x + plan["crop_w"]
    return inside / len(span)


# ====================================================================== captions (ASS for libass)
def _ass_color(hex_: str, alpha: int = 0) -> str:
    h = hex_.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _ass_text(s: str) -> str:
    return s.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def caption_chunks(words: list[dict], max_words: int = 4, max_chars: int = 22) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        t = str(w["w"]).strip()
        if not t:
            continue
        if cur and (len(cur) >= max_words or len(" ".join(x["w"].strip() for x in cur + [w])) > max_chars
                    or w["s"] - cur[-1]["e"] > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if t.endswith((".", "?", "!")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def captions_ass(words: list[dict], clip_start: float, offset: float, kit: dict, p: dict, W: int = CLIP_W, H: int = CLIP_H,
                 upper: bool | None = None) -> str:
    """ASS subtitles: brand font, text colour, accent on the word being said, outline in the brand ground colour.
    Times are shifted so the clip's first frame is 0 (offset = seconds of cover frame before the speech starts).
    Bottom margin keeps every line above the bottom SAFE_BOTTOM of the frame."""
    colors = p.get("colors") or kit.get("colors") or {}
    txt, acc, ground = colors.get("text", "#FFFFFF"), colors.get("accent", "#C9ACFA"), colors.get("primary", "#111114")
    font = ASS_FONT.get(p.get("font") or kit.get("font") or "anton", "Anton")
    size = int(H * 0.066)
    margin_v = int(H * (SAFE_BOTTOM + 0.07))
    upper = (p.get("headline_case", "upper") == "upper") if upper is None else upper
    head = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
            "MarginR, MarginV, Encoding\n"
            f"Style: Cap,{font},{size},{_ass_color(txt)},{_ass_color(acc)},{_ass_color(ground)},{_ass_color(ground, 0x60)},"
            f"-1,0,0,0,100,100,0,0,1,{max(3, size // 12)},2,2,{int(W * 0.08)},{int(W * 0.08)},{margin_v},1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    lines = []
    for chunk in caption_chunks(words):
        toks = [_ass_text(w["w"].strip()) for w in chunk]
        if upper:
            toks = [t.upper() for t in toks]
        for i, w in enumerate(chunk):
            s = w["s"] - clip_start + offset
            e = (chunk[i + 1]["s"] if i + 1 < len(chunk) else w["e"] + 0.15) - clip_start + offset
            if e <= s:
                e = s + 0.08
            body = " ".join(("{\\c" + _ass_color(acc) + "}" + t + "{\\c" + _ass_color(txt) + "}") if j == i else t
                            for j, t in enumerate(toks))
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{body}")
    return head + "\n".join(lines) + "\n"


def caption_box(ass: str, H: int = CLIP_H) -> tuple[int, int]:
    """(top, bottom) pixel band the captions can occupy, from the style (two lines at most)."""
    line = next(x for x in ass.splitlines() if x.startswith("Style: Cap,"))
    f = line[len("Style: "):].split(",")   # Name, Fontname, Fontsize, ..., MarginL, MarginR, MarginV, Encoding
    size, margin_v = int(f[2]), int(f[-2])
    bottom = H - margin_v
    return bottom - int(size * 1.25 * 2), bottom


# ====================================================================== heavy: probe / analyze (worker)
def have(binary: str) -> bool:
    return bool(shutil.which(binary))


def run(cmd: list[str], timeout: int = 1800) -> subprocess.CompletedProcess:
    from . import mediasafe  # protocol + demuxer whitelists before every input (a crafted file can't open URLs or files)
    r = mediasafe.run(cmd, timeout=timeout, text=True)
    if r.returncode != 0:
        raise TakeError(f"{Path(cmd[0]).name} failed: {r.stderr[-800:]}")
    return r


def probe(path: str) -> dict:
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path], timeout=120)
    j = json.loads(r.stdout or "{}")
    v = next((s for s in j.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if rot in (90, 270):
        w, h = h, w
    return {"duration": float((j.get("format") or {}).get("duration") or v.get("duration") or 0), "width": w, "height": h,
            "audio": a is not None, "codec": v.get("codec_name"), "raw": j}


def detect_silences(path: str, noise_db: int = -32, min_s: float = 0.35) -> list[list[float]]:
    from . import mediasafe
    r = mediasafe.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn", "-af", f"silencedetect=noise={noise_db}dB:d={min_s}",
                       "-f", "null", "-"], text=True, timeout=1800)
    out, start = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            start = float(m.group(1))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and start is not None:
            out.append([round(start, 2), round(float(m.group(1)), 2)])
            start = None
    return out


def detect_scenes(path: str) -> tuple[list[float], str]:
    try:
        from scenedetect import ContentDetector, detect  # PySceneDetect (BSD-3-Clause)
        scenes = detect(path, ContentDetector(threshold=27.0), show_progress=False)
        return [round(s[0].get_seconds(), 2) for s in scenes[1:]], "PySceneDetect"
    except ImportError:
        pass
    from . import mediasafe
    r = mediasafe.run(["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-an", "-vf", "scale=320:-2,select='gt(scene,0.35)',showinfo",
                       "-f", "null", "-"], text=True, timeout=1800)
    return [round(float(m.group(1)), 2) for m in re.finditer(r"pts_time:([\d.]+)", r.stderr)], "ffmpeg scene filter"


TRANSCRIBER = None  # tests and self-hosters can plug in their own: fn(path) -> (words, language, provider)


def transcribe(path: str, model: str = "small") -> tuple[list[dict], str, str]:
    if TRANSCRIBER:
        return TRANSCRIBER(path)
    try:
        from faster_whisper import WhisperModel  # MIT
    except ImportError:
        raise TakeError("Transcription runs on the video worker (faster-whisper isn't installed here).")
    import numpy as np
    # decode with ffmpeg ourselves (16 kHz mono float): no dependency on a particular PyAV version
    from . import mediasafe
    pcm = mediasafe.run(["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                        timeout=1800, check=True).stdout
    audio = np.frombuffer(pcm, dtype=np.float32)
    wm = WhisperModel(model, device="cpu", compute_type="int8", cpu_threads=os.cpu_count() or 2)
    segs, info = wm.transcribe(audio, word_timestamps=True, vad_filter=True, vad_parameters={"min_silence_duration_ms": 400},
                               beam_size=1, condition_on_previous_text=False)
    words = []
    for seg in segs:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2), "p": round(w.probability, 2)})
    return words, info.language, f"faster-whisper {model} (CPU int8, Silero VAD)"


def face_track(path: str, duration: float, fps: float = 2.0, model_path: str | None = None) -> tuple[list[dict], str]:
    """Faces sampled at `fps` with MediaPipe's Face Detector (BlazeFace short range). [] when MediaPipe isn't there."""
    try:
        import mediapipe as mp  # Apache-2.0
        import numpy as np
        from mediapipe.tasks.python import BaseOptions, vision
    except Exception:  # noqa: BLE001
        return [], "none (MediaPipe not installed: centre/blur layout)"
    model_path = model_path or os.environ.get("HQ_FACE_MODEL") or str(Path(tempfile.gettempdir()) / "blaze_face_short_range.tflite")
    if not Path(model_path).exists():
        import httpx
        Path(model_path).write_bytes(httpx.get(FACE_MODEL_URL, timeout=60, follow_redirects=True).content)
    try:  # MediaPipe needs libEGL/libGLES; a box without them reframes with the centre/blur layout instead of failing
        det = vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path), min_detection_confidence=0.6))
    except OSError as e:
        return [], f"none (MediaPipe couldn't load: {str(e)[:80]})"
    w, h = 320, 180
    info = probe(path)
    if info["width"] and info["height"]:
        h = int(round(w * info["height"] / info["width"] / 2)) * 2
    from . import mediasafe
    proc = mediasafe.popen(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    out, k, size = [], 0, w * h * 3
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            img = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 3)
            res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(img)))
            if res.detections:
                d = max(res.detections, key=lambda d: d.bounding_box.width * d.bounding_box.height)
                bb = d.bounding_box
                out.append({"t": round(k / fps, 2), "cx": round((bb.origin_x + bb.width / 2) / w, 4),
                            "cy": round((bb.origin_y + bb.height / 2) / h, 4), "w": round(bb.width / w, 4),
                            "score": round(d.categories[0].score, 2)})
            k += 1
    finally:
        proc.kill()
        try:
            det.close()
        except Exception:  # noqa: BLE001
            pass
    return out, "MediaPipe Face Detector (BlazeFace)"


def analyze(path: str, model: str = "small", log=print, max_secs: float = 3600, transcript: dict | None = None) -> dict:
    """transcript: captions that already exist (studio.media.subtitles.resolve_transcript: an uploaded .srt/.vtt, the
    platform's track, a caption stream in the file). When it has words, Whisper is skipped entirely."""
    t0 = time.time()
    from . import mediasafe
    try:  # plain video only, before PySceneDetect/MediaPipe (their own decoders) ever open it
        mediasafe.check_source(path)
    except mediasafe.MediaRefused as e:
        raise TakeError(str(e))
    info = probe(path)
    if not info["audio"]:
        raise TakeError("This video has no audio track, so there is nothing to transcribe.")
    if info["duration"] > max_secs:
        raise TakeError(f"This video is {info['duration'] / 60:.0f} min; the free worker takes up to {max_secs / 60:.0f} min.")
    timings = {}
    log(f"probe: {info['duration']:.1f}s {info['width']}x{info['height']}")
    t = time.time()
    silences = detect_silences(path)
    timings["silences"] = round(time.time() - t, 1)
    t = time.time()
    if transcript and transcript.get("words"):
        words, lang = transcript["words"], transcript.get("language") or ""
        provider = f"reused captions: {transcript.get('detail') or transcript.get('source')} (no Whisper run)"
    else:
        words, lang, provider = transcribe(path, model)
    timings["transcribe"] = round(time.time() - t, 1)
    log(f"transcribe: {len(words)} words in {timings['transcribe']}s ({provider})")
    t = time.time()
    scenes, scene_how = detect_scenes(path)
    timings["scenes"] = round(time.time() - t, 1)
    t = time.time()
    faces, face_how = face_track(path, info["duration"])
    timings["faces"] = round(time.time() - t, 1)
    log(f"scenes: {len(scenes)} ({scene_how}); faces: {len(faces)} samples ({face_how})")
    try:  # subtitles already burned into the picture: renders crop them off instead of stacking a second layer
        from .media.compose import detect_burned_subs
        burned = detect_burned_subs(path, info["duration"])
    except Exception:  # noqa: BLE001
        burned = False
    return {"probe": {k: info[k] for k in ("duration", "width", "height", "audio", "codec")}, "words": words, "language": lang,
            "transcript_source": (transcript or {}).get("source") if transcript and transcript.get("words") else "whisper",
            "burned_subs": burned, "face_detector": not face_how.startswith("none"),
            "silences": silences, "scenes": scenes, "faces": faces,
            "providers": {"stt": provider, "scenes": scene_how, "faces": face_how, "vad": "Silero VAD (inside faster-whisper)"
                          if "faster-whisper" in provider else "ffmpeg silencedetect"},
            "timings": {**timings, "total": round(time.time() - t0, 1)}}


# ====================================================================== heavy: render one clip (worker)
def _font(name: str, size: int):
    from PIL import ImageFont
    return ImageFont.truetype(str(FONT_DIR / name), size)


def frame_at(path: str, t: float, out: str) -> None:
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", path, "-frames:v", "1", "-q:v", "3", out], timeout=60)


def sharpness(jpg: bytes) -> float:
    from PIL import Image, ImageFilter, ImageStat
    im = Image.open(io.BytesIO(jpg)).convert("L").resize((320, 180))
    return ImageStat.Stat(im.filter(ImageFilter.FIND_EDGES)).var[0]


def pick_cover_time(path: str, m: dict, faces: list[dict], tmp: Path) -> float:
    """Sharpest of five frames in the first half of the moment, preferring frames with a face."""
    s, e = m["start"], m["end"]
    cands = [s + (e - s) * f for f in (0.08, 0.18, 0.28, 0.38, 0.48)]
    best, best_t = -1.0, cands[0]
    for i, t in enumerate(cands):
        f = tmp / f"cand{i}.jpg"
        try:
            frame_at(path, t, str(f))
            sc = sharpness(f.read_bytes())
        except Exception:  # noqa: BLE001
            continue
        if any(abs(x["t"] - t) <= 0.6 and x.get("score", 1) >= 0.7 for x in faces or []):
            sc *= 1.5
        if sc > best:
            best, best_t = sc, t
    return best_t


def cover_image(frame: bytes, plan: dict, src_w: int, hook: str, kit: dict, p: dict, logo: bytes | None, t_rel: float,
                W: int = CLIP_W, H: int = CLIP_H) -> bytes:
    """The branded cover: the clip's best frame in 9:16, the hook (a line the speaker said) in the brand font."""
    from PIL import Image, ImageDraw, ImageFilter

    from . import render
    src = Image.open(io.BytesIO(frame)).convert("RGB")
    sw, sh = src.size
    if plan["mode"] == "track":
        x = crop_x_at(plan, t_rel) * sw / src_w
        cw = plan["crop_w"] * sw / src_w
        canvas = src.crop((int(x), 0, int(x + cw), sh)).resize((W, H), Image.LANCZOS)
    else:
        bg = render.cover(src, W, H).filter(ImageFilter.GaussianBlur(28))
        fg = src.copy()
        fg.thumbnail((W, H))
        bg.paste(fg, ((W - fg.width) // 2, (H - fg.height) // 2))
        canvas = bg
    render._gradient(canvas, render.hex_to_rgb(p["colors"]["primary"]), 0.42, 0.85)
    d = ImageDraw.Draw(canvas)
    m = int(p.get("safe_margin", 64) * W / 1080) + 8
    from .brand import FONTS
    head = render.shape_headline(hook, {**p, "headline_max_words": 14})
    size = int(W * 0.088)
    f = _font(FONTS.get(p.get("font"), "Anton.ttf"), size)
    head = render.drawable(head, f)
    while True:
        lines = render.wrap(d, head, f, W - 2 * m)
        if len(lines) <= 4 or size < int(W * 0.05):
            break
        size = int(size * 0.9)
        f = _font(FONTS.get(p.get("font"), "Anton.ttf"), size)
    lh = int(f.size * 1.1)
    y = int(H * (1 - SAFE_BOTTOM)) - lh * len(lines[:4]) - int(H * 0.02)
    acc, txt = render.hex_to_rgb(p["colors"]["accent"]), render.hex_to_rgb(p["colors"]["text"])
    bar_w = int(W * 0.12)
    d.rectangle((m, y - int(H * 0.025), m + bar_w, y - int(H * 0.025) + max(6, H // 220)), fill=acc)
    for i, line in enumerate(lines[:4]):
        render._draw(d, (m, y + i * lh), line, font=f, fill=acc if i == 0 and len(lines) > 1 else txt)
    render.place_logo(canvas, render.load_logo(logo), p, kit)
    return render._jpeg(canvas)


def segments(start: float, end: float, scenes: list[float] | None, min_len: float = 1.2) -> list[tuple[float, float]]:
    """Split a clip at scene cuts so each shot gets its own layout (face-tracked or blurred-pad)."""
    cuts = [c for c in sorted(scenes or []) if start + min_len <= c <= end - min_len]
    bounds = [start]
    for c in cuts:
        if c - bounds[-1] >= min_len:
            bounds.append(c)
    bounds.append(end)
    return [(round(a, 3), round(b, 3)) for a, b in zip(bounds, bounds[1:])]


def _frame_filter(plan: dict, sh: int, W: int, H: int) -> str:
    if plan["mode"] == "track":
        return f"[0:v]crop={plan['crop_w']}:{sh}:x='{crop_expr(plan)}':y=0,scale={W}:{H},setsar=1,fps=30,format=yuv420p[v]"
    if plan["mode"] == "fit":
        return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30,format=yuv420p[v]"
    return (f"[0:v]split=2[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2[bg];"
            f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30,format=yuv420p[v]")


def render_clip(path: str, m: dict, words: list[dict], faces: list[dict], scenes: list[float], src: dict, kit: dict, p: dict,
                logo: bytes | None = None, W: int = CLIP_W, H: int = CLIP_H, crf: int = 26) -> tuple[bytes, bytes, dict]:
    """One finished clip: [cover 0.5 s] + [moment, reframed to 9:16 shot by shot, captions burned in, logo bug] + [end card].
    Returns (mp4, cover_jpg, meta). meta.cover_offset_ms is the frame Buffer's thumbnailOffset should point at."""
    from PIL import Image

    from . import render
    tmp = Path(tempfile.mkdtemp(prefix="hq_take_"))
    try:
        start, end = float(m["start"]), float(m["end"])
        dur = end - start
        sw, sh = src["width"], src["height"]
        venc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-maxrate", "5M", "-bufsize", "10M", "-r", "30",
                "-pix_fmt", "yuv420p"]
        aenc = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]
        # 1) reframe shot by shot
        shots, plans = segments(start, end, scenes), []
        lst = tmp / "shots.txt"
        with open(lst, "w") as fh:
            for k, (a, b) in enumerate(shots):
                plan = crop_plan(faces, a, b, sw, sh, scenes)
                plans.append({**plan, "start": a, "end": b})
                out = tmp / f"shot{k}.mp4"
                run(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", path, "-filter_complex",
                     _frame_filter(plan, sh, W, H), "-map", "[v]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                     "-r", "30", str(out)])
                fh.write(f"file '{out}'\n")
        framed = tmp / "framed.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(framed)])
        # 2) captions + logo over the reframed video, with the original audio of the span
        ass = captions_ass(words, start, 0.0, kit, p, W, H)
        (tmp / "cap.ass").write_text(ass, encoding="utf-8")
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        render.place_logo(ov, render.load_logo(logo), p, kit)
        ov.save(tmp / "logo.png")
        fonts = str(FONT_DIR).replace("\\", "/").replace(":", "\\:")
        assf = str(tmp / "cap.ass").replace("\\", "/").replace(":", "\\:")
        body = tmp / "body.mp4"
        vf = f"[0:v]ass=filename='{assf}':fontsdir='{fonts}'[cap];[cap][2:v]overlay=0:0,format=yuv420p[v]"
        run(["ffmpeg", "-v", "error", "-y", "-i", str(framed), "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
             "-i", str(tmp / "logo.png"), "-filter_complex", vf, "-map", "[v]", "-map", "1:a?", "-t", f"{dur:.3f}", *venc, *aenc,
             "-movflags", "+faststart", str(body)])
        # 3) branded cover from the best frame, as the first half second
        main = max(plans, key=lambda x: (x["mode"] == "track", x["end"] - x["start"]))
        ct = pick_cover_time(path, m, faces, tmp)
        cplan = next((x for x in plans if x["start"] <= ct <= x["end"]), main)
        frame_at(path, ct, str(tmp / "cover_src.jpg"))
        cover = cover_image((tmp / "cover_src.jpg").read_bytes(), cplan, sw, m.get("hook") or "", kit, p, logo, ct - cplan["start"], W, H)
        (tmp / "cover.jpg").write_bytes(cover)
        segs = []
        cov = tmp / "cov.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{COVER_SECS}", "-i", str(tmp / "cover.jpg"), "-f", "lavfi", "-t",
             f"{COVER_SECS}", "-i", "anullsrc=r=44100:cl=stereo", "-vf", f"scale={W}:{H},setsar=1,fps=30,format=yuv420p",
             *venc, *aenc, "-shortest", str(cov)])
        segs += [cov, body]
        # 4) end card
        has_end = bool(p.get("end_card", True))
        if has_end:
            ec, _ = render.end_card(kit, p, logo=logo, ratio="9:16")
            Image.open(io.BytesIO(ec)).convert("RGB").resize((W, H), Image.LANCZOS).save(tmp / "end.jpg", quality=92)
            endv = tmp / "end.mp4"
            run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", f"{END_SECS}", "-i", str(tmp / "end.jpg"), "-f", "lavfi", "-t",
                 f"{END_SECS}", "-i", "anullsrc=r=44100:cl=stereo", "-vf", f"scale={W}:{H},setsar=1,fps=30,format=yuv420p",
                 *venc, *aenc, "-shortest", str(endv)])
            segs.append(endv)
        final = tmp / "clip.mp4"
        ins = []
        for sgm in segs:
            ins += ["-i", str(sgm)]
        n = len(segs)
        fc = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
        run(["ffmpeg", "-v", "error", "-y", *ins, "-filter_complex", fc, "-map", "[v]", "-map", "[a]", *venc, *aenc,
             "-movflags", "+faststart", str(final)])
        data = final.read_bytes()
        info = probe(str(final))
        from .preflight import parse_ffprobe
        pf = parse_ffprobe(info["raw"])
        pf.update(kind="video", bytes=len(data))
        modes = [x["mode"] for x in plans]
        tracked = [x for x in plans if x["mode"] == "track"]
        cov_ratio = (sum(face_coverage(x, faces, x["start"], x["end"], sw) * (x["end"] - x["start"]) for x in tracked)
                     / sum(x["end"] - x["start"] for x in tracked)) if tracked else None
        meta = {"type": "video", "width": W, "height": H, "duration": round(info["duration"], 2), "params": render._pub(p),
                "has_logo": True, "has_end_card": has_end, "headline": m.get("hook", ""), "credit": "own video",
                "cover_offset_ms": int(COVER_SECS * 1000 / 2),
                "layout": "track" if set(modes) == {"track"} else ("mixed" if "track" in modes else modes[0]),
                "shots": [{"start": x["start"], "end": x["end"], "mode": x["mode"]} for x in plans],
                "face_coverage": round(cov_ratio, 3) if cov_ratio is not None else None,
                "moment": {k: m.get(k) for k in ("start", "end")}, "caption_band": caption_box(ass, H), "probe": pf}
        return data, cover, meta
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ====================================================================== the week: open slots + spread plan
DOW = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
SUGGESTED_TIMES = ["09:00", "12:30", "18:00"]


def week_start(week_of: str | None, tz: str, now: datetime | None = None) -> date:
    """Monday of the week to fill. week_of: 'this' | 'next' (default) | an ISO date inside the week."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz)).date()
    if week_of and re.fullmatch(r"\d{4}-\d{2}-\d{2}", week_of):
        d = date.fromisoformat(week_of)
    elif week_of == "this":
        d = today
    else:
        d = today + timedelta(days=7)
    return d - timedelta(days=d.weekday())


def open_slots(schedules: list[dict], busy: list[dict], channel_ids: list[str], monday: date, now: datetime | None = None,
               lead_minutes: int = 30) -> dict[str, list[dict]]:
    """Free posting slots per channel for the week starting `monday` (each channel in its own timezone).
    schedules: Buffer channels [{id, timezone, postingSchedule:[{day, times, paused}]}]. busy: posts with channelId+dueAt.
    A channel with no posting times gets SUGGESTED_TIMES, marked suggested."""
    now = now or datetime.now(timezone.utc)
    taken = {}
    for b in busy or []:
        if b.get("dueAt"):
            try:
                taken.setdefault(b.get("channelId"), set()).add(
                    datetime.fromisoformat(b["dueAt"].replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M"))
            except ValueError:
                continue
    by_id = {s.get("id"): s for s in schedules or []}
    out: dict[str, list[dict]] = {}
    for cid in channel_ids:
        s = by_id.get(cid) or {}
        tz = s.get("timezone") or "UTC"
        try:
            zi = ZoneInfo(tz)
        except Exception:  # noqa: BLE001
            zi, tz = ZoneInfo("UTC"), "UTC"
        sched = {x.get("day"): x for x in s.get("postingSchedule") or []}
        has_times = any((x.get("times") and not x.get("paused")) for x in sched.values())
        slots = []
        for k in range(7):
            day = monday + timedelta(days=k)
            if has_times:
                entry = sched.get(DOW[k]) or {}
                if entry.get("paused"):
                    continue
                times, suggested = entry.get("times") or [], False
            else:
                times, suggested = SUGGESTED_TIMES, True
            for tm in times:
                try:
                    hh, mm = (int(x) for x in tm.split(":")[:2])
                except ValueError:
                    continue
                local = datetime(day.year, day.month, day.day, hh, mm, tzinfo=zi)
                utc = local.astimezone(timezone.utc)
                if utc <= now + timedelta(minutes=lead_minutes):
                    continue
                if utc.strftime("%Y-%m-%dT%H:%M") in taken.get(cid, set()):
                    continue
                slots.append({"channel_id": cid, "at": utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "day": day.isoformat(),
                              "local": f"{DOW[k].title()} {tm}", "timezone": tz, "suggested": suggested})
        out[cid] = sorted(slots, key=lambda x: x["at"])
    return out


def plan_spread(clip_ids: list[str], slots: dict[str, list[dict]], daily_room: dict[str, dict[str, int]] | None = None) -> dict:
    """Put each clip on each channel in a free slot, one clip per channel per day where the week allows, spreading
    clips across the week in order. Never two clips in one slot; never more posts on a day than Buffer's daily room.
    daily_room: {channel_id: {day: posts still allowed}} (missing = no limit known)."""
    placements, unplaced = [], []
    for cid, free in slots.items():
        used_slots, per_day = set(), {}
        room = (daily_room or {}).get(cid) or {}
        days = sorted({s["day"] for s in free})
        n = len(clip_ids)
        # preferred day per clip: spread evenly over the days that have slots
        pref = [days[min(len(days) - 1, int(i * len(days) / max(n, 1)))] for i in range(n)] if days else []
        for i, clip in enumerate(clip_ids):
            if not days:
                unplaced.append({"clip_id": clip, "channel_id": cid, "why": "No open posting slot left this week on this channel."})
                continue
            order = sorted(free, key=lambda s: (s["day"] < pref[i], abs(date.fromisoformat(s["day"]).toordinal()
                                                                         - date.fromisoformat(pref[i]).toordinal()), s["at"]))
            pick = None
            for one_per_day in (True, False):
                for s in order:
                    if s["at"] in used_slots:
                        continue
                    d = s["day"]
                    if d in room and per_day.get(d, 0) >= room[d]:
                        continue
                    if one_per_day and per_day.get(d, 0) >= 1:
                        continue
                    pick = s
                    break
                if pick:
                    break
            if not pick:
                unplaced.append({"clip_id": clip, "channel_id": cid, "why": "No open posting slot left this week on this channel."})
                continue
            used_slots.add(pick["at"])
            per_day[pick["day"]] = per_day.get(pick["day"], 0) + 1
            placements.append({"clip_id": clip, **pick})
    placements.sort(key=lambda x: (x["at"], x["channel_id"]))
    return {"placements": placements, "unplaced": unplaced}


def caption_for(service: str, m: dict, kit: dict) -> str:
    """The post caption: the hook's whole sentence in quotes and the sentence after it, both said in the clip.
    Nothing is invented; the person can edit it per network."""
    hook = (m.get("hook") or "").strip().rstrip("…")
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+", (m.get("text") or "").strip()) if x.strip()]
    i = next((k for k, x in enumerate(sents) if norm(hook) and norm(hook) in norm(x)), None)
    quote = sents[i] if i is not None else hook
    more = sents[i + 1] if i is not None and i + 1 < len(sents) else ""
    return f"“{quote}”" + (f"\n\n{more}" if more else "")
