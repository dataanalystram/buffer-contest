"""Transcript reuse: when a video already has captions, use them instead of running Whisper.

Captions the platform or the person already has are faster (no model download, no CPU minutes), cheaper, and usually
spell names right (a human-made track always does). Whisper only runs when none of these exist.

Order (``resolve_transcript``):

1. **An uploaded caption file** (``.srt`` / ``.vtt``) the person attached with the video.
2. **The platform's own track** for a YouTube / Vimeo / X / ... link: the creator's manual captions first, then the
   platform's automatic ones (YouTube auto-captions carry per-word timings). Read with yt-dlp, metadata only, ONLY
   that platform's extractor enabled, no video download (``platform_captions``).
3. **A caption stream inside the file** (MP4 ``mov_text``, MKV SRT/ASS), extracted by our FFmpeg.
4. **Whisper** (``studio.media.transcribe``), the only branch that costs CPU minutes.

Every branch returns the same word list (``[{"w", "s", "e"}]``) the caption and clip-picking code already uses.
Cue-level captions (SRT, most VTT) have no per-word times, so each cue's words are spread over the cue by length.
YouTube's "rolling" captions repeat the previous line in every cue; ``words_from_cues`` stitches them by overlap so
no word appears twice.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

MAX_CAPTION_BYTES = 8 * 1024 * 1024  # a 3-hour talk's VTT with word timings is ~2 MB
_TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE = re.compile(rf"{_TS}\s*-->\s*{_TS}")
_INLINE = re.compile(r"<(\d+:\d{2}:\d{2}[.,]\d{3}|\d{1,2}:\d{2}[.,]\d{3})>")
_TAG = re.compile(r"</?[^>]+>")
_NOISE = re.compile(r"^[\[(♪][^\])]*[\])♪]$|^♪+$")  # [Music], [ Sound Effects ], (applause), ♪


class CaptionError(ValueError):
    pass


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")[:3]) / 1000


def _ts(t: str) -> float:
    m = re.fullmatch(_TS, t.strip())
    if not m:
        raise CaptionError(f"Bad timestamp {t!r}")
    return _secs(*m.groups())


def _clean(text: str) -> str:
    text = _TAG.sub("", text).replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<").replace("&nbsp;", " ")
    text = re.sub(r"^\s*(?:>>|-)\s*", "", text)  # speaker-change marks
    return re.sub(r"\s+", " ", text).strip()


def parse_cues(text: str) -> list[dict]:
    """SRT or WebVTT -> [{"s", "e", "lines": [raw lines]}]. Raw lines keep VTT inline word timings for ``words_from_cues``."""
    if len(text.encode("utf-8", "ignore")) > MAX_CAPTION_BYTES:
        raise CaptionError("That caption file is too large.")
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    cues: list[dict] = []
    lines = text.split("\n")
    cur: dict | None = None
    for i, ln in enumerate(lines):
        m = _CUE.search(ln)
        if m:
            if cur and cur["lines"] and cur["e"] > cur["s"]:
                cues.append(cur)
            g = m.groups()
            cur = {"s": _secs(*g[:4]), "e": _secs(*g[4:]), "lines": []}
            continue
        if cur is None or not ln.strip():
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if ln.strip().isdigit() and _CUE.search(nxt):  # the SRT index of the next cue
            continue
        if ln.strip().upper().startswith(("NOTE", "STYLE", "REGION")) and not cur["lines"]:
            continue
        cur["lines"].append(ln)
    if cur and cur["lines"] and cur["e"] > cur["s"]:
        cues.append(cur)
    if not cues:
        raise CaptionError("No timed captions found (expected SRT or WebVTT).")
    return sorted(cues, key=lambda c: c["s"])


def _spread(tokens: list[str], s: float, e: float) -> list[dict]:
    """Cue-level timing -> word timing, proportional to each word's length (long words take longer to say)."""
    if not tokens:
        return []
    weights = [len(t) + 2 for t in tokens]
    total, t, out = sum(weights), s, []
    for tok, w in zip(tokens, weights):
        d = (e - s) * w / total
        out.append({"w": tok, "s": round(t, 3), "e": round(t + d * 0.92, 3)})
        t += d
    return out


def _overlap(prev: list[str], new: list[str], max_n: int = 60) -> int:
    """Length of the longest suffix of ``prev`` that is a prefix of ``new`` (rolling-caption repeats)."""
    norm = lambda x: re.sub(r"[^a-z0-9']", "", x.lower())  # noqa: E731
    p, n = [norm(x) for x in prev[-max_n:]], [norm(x) for x in new[:max_n]]
    for k in range(min(len(p), len(n)), 0, -1):
        if p[-k:] == n[:k]:
            return k
    return 0


def words_from_cues(cues: list[dict]) -> list[dict]:
    """Cues -> one de-duplicated word stream with times.

    * VTT with inline ``<00:00:01.234>`` marks (YouTube auto-captions): exact word times; lines without marks are the
      rolled-up repeat of the previous cue and are skipped.
    * Everything else: words spread over the cue, and the part of each cue that repeats the end of the previous one
      (rolling captions, the 10 ms "hold" cues yt-dlp's SRT conversion produces) is dropped.
    """
    inline = any(_INLINE.search(ln) for c in cues for ln in c["lines"])
    words: list[dict] = []
    if inline:
        for c in cues:
            for ln in c["lines"]:
                if not _INLINE.search(ln):
                    continue
                parts = _INLINE.split(ln)  # text, ts, text, ts, text ...
                t0 = c["s"]
                segs = [(t0, parts[0])] + [(_ts(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]
                for k, (st, chunk) in enumerate(segs):
                    toks = _clean(chunk).split()
                    en = segs[k + 1][0] if k + 1 < len(segs) else c["e"]
                    for tw in _spread(toks, st, max(st + 0.05, en)):
                        if not _NOISE.match(tw["w"]):
                            words.append(tw)
        return _monotonic(words)
    emitted: list[str] = []
    for c in cues:
        text = _clean(" ".join(c["lines"]))
        text = re.sub(r"[\[(][^\])]{0,40}[\])]", " ", text).strip()  # inline [Music] / (laughs)
        if not text or _NOISE.match(text) or c["e"] - c["s"] < 0.05:
            continue
        toks = [t for t in text.split() if not _NOISE.match(t)]
        k = _overlap(emitted, toks)
        new = toks[k:]
        if not new:
            continue
        # rolling captions: the repeated line was said during the previous cue, the new words during this one
        words += _spread(new, c["s"], c["e"])
        emitted += new
    return _monotonic(words)


def _monotonic(words: list[dict]) -> list[dict]:
    out: list[dict] = []
    for w in words:
        if out and not re.search(r"[\w$%€£]", w["w"]):  # a lone "." or "—" belongs to the word before it
            out[-1] = {**out[-1], "w": out[-1]["w"] + (w["w"] if w["w"] in ".,!?;:…" else ""), "e": max(out[-1]["e"], w["e"])}
            continue
        if out and w["s"] < out[-1]["s"]:
            w = {**w, "s": out[-1]["e"], "e": max(w["e"], out[-1]["e"] + 0.05)}
        out.append(w)
    return out


def parse_captions(text: str) -> list[dict]:
    return words_from_cues(parse_cues(text))


def has_punctuation(words: list[dict]) -> bool:
    """Auto-captions usually have none; the sentence splitter then cuts on pauses instead."""
    if not words:
        return False
    ends = sum(1 for w in words if str(w["w"]).rstrip().endswith((".", "?", "!")))
    return ends >= max(2, len(words) // 80)


# ------------------------------------------------------------------------------------------------ platform captions
PREFERRED_LANGS = ("en", "en-US", "en-GB", "en-orig")


def _pick_track(tracks: dict, lang: str | None) -> tuple[str, dict] | None:
    if not tracks:
        return None
    order = ([lang] if lang else []) + list(PREFERRED_LANGS)
    keys = list(tracks)
    for want in order:
        for k in keys:
            if k == want or k.split("-")[0] == want:
                fmts = tracks[k] or []
                vtt = next((f for f in fmts if f.get("ext") == "vtt"), None) or next((f for f in fmts if f.get("ext") == "srt"), None)
                if vtt and vtt.get("url"):
                    return k, vtt
    return None


def choose_track(info: dict, lang: str | None = None) -> dict | None:
    """Manual captions beat automatic ones. Returns {"lang", "kind": manual|auto, "url", "ext"} or None."""
    for kind, key in (("manual", "subtitles"), ("auto", "automatic_captions")):
        hit = _pick_track(info.get(key) or {}, lang)
        if hit:
            k, f = hit
            return {"lang": k, "kind": kind, "url": f["url"], "ext": f.get("ext", "vtt")}
    return None


def platform_captions(url: str, lang: str | None = None) -> dict | None:
    """The platform's own caption track for a video link, or None. Never downloads the video.
    Returns {"words", "kind": "manual"|"auto", "lang", "platform"}."""
    from .. import netguard
    from .find import PLATFORMS, _QuietLog, platform_of, watch_url
    plat = platform_of(url)
    if not plat:
        return None
    try:
        import yt_dlp
    except Exception:  # noqa: BLE001
        return None
    netguard.check_url(url, https_only=True)
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "logger": _QuietLog(), "cachedir": False,
            "skip_download": True, "allowed_extractors": PLATFORMS[plat]["ie"], "socket_timeout": 30, "retries": 1}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(watch_url(url), download=False)
            tr = choose_track(info or {}, lang)
            if not tr:
                return None
            # fetched by yt-dlp's own opener: same platform host, same cookies-free session
            raw = y.urlopen(tr["url"]).read(MAX_CAPTION_BYTES + 1)
    except Exception:  # noqa: BLE001 - no captions is not an error: Whisper runs instead
        return None
    if len(raw) > MAX_CAPTION_BYTES:
        return None
    try:
        words = parse_captions(raw.decode("utf-8", "replace"))
    except CaptionError:
        return None
    return {"words": words, "kind": tr["kind"], "lang": tr["lang"], "platform": plat} if words else None


def embedded_captions(path: str) -> list[dict] | None:
    """A caption stream inside the container (mov_text / subrip / ass / webvtt), or None."""
    from . import ff
    try:
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "subs.srt"
            ff.run(["-y", "-v", "error", "-i", path, "-map", "0:s:0", "-c:s", "srt", str(out)], timeout=120)
            if out.is_file() and out.stat().st_size > 0:
                return parse_captions(out.read_text("utf-8", "replace")) or None
    except Exception:  # noqa: BLE001 - no subtitle stream
        return None
    return None


# ------------------------------------------------------------------------------------------------ the decision
def resolve_transcript(path: str | None, *, url: str | None = None, caption_text: str | None = None,
                       caption_name: str = "", names: list[str] | None = None, lang: str | None = None,
                       transcribe=None, platform=platform_captions, embedded=embedded_captions) -> dict:
    """The transcript for a video, reusing captions when they exist. Returns
    {"words", "source": uploaded|platform|embedded|whisper|none, "detail", "language", "punctuated"}.
    ``transcribe(path, names)`` is the Whisper fallback (default: studio.media.transcribe)."""
    if caption_text:
        words = parse_captions(caption_text)
        if words:
            return _out(words, "uploaded", f"your caption file{(' ' + caption_name) if caption_name else ''}", lang)
    if url:
        got = platform(url, lang) if platform else None
        if got and got.get("words"):
            label = {"manual": "the creator's captions", "auto": "automatic captions"}.get(got.get("kind"), "captions")
            return _out(got["words"], "platform", f"{label} on {got.get('platform', 'the platform')}", got.get("lang") or lang,
                        kind=got.get("kind"))
    if path and embedded:
        words = embedded(path)
        if words:
            return _out(words, "embedded", "the caption track inside the file", lang)
    if path and transcribe is not False:
        if transcribe is None:
            from .transcribe import transcribe as _tr

            def transcribe(p, nm):  # noqa: E306
                return _tr(p, names=nm)
        r = transcribe(path, names)
        return _out(r.get("words") or [], "whisper", f"Whisper {r.get('model', '')}".strip(), r.get("language"))
    return _out([], "none", "no captions and no transcriber", lang)


def _out(words, source, detail, lang, kind=None) -> dict:
    return {"words": words, "source": source, "detail": detail, "kind": kind, "language": lang or "",
            "punctuated": has_punctuation(words), "count": len(words)}
