"""On-screen text with Tesseract OCR (worker side: stdlib + the tesseract and ffmpeg programs, no Python binding).

Tesseract (Apache-2.0) is called as a separate program with fixed arguments, a timeout per frame and no shell; no
pytesseract. HuhQueue requires Tesseract >= 5.5.4 (MIN_VERSION): an older or missing binary turns OCR off and every
caller keeps its default (fail open). The worker builds the pinned release once (scripts/worker_tesseract.sh).

  scan(path, start, end, budget)   frames sampled at most one per 2 s (<= 60 per take, <= 30 s of OCR per take, shared
                                   through one Budget), words with their boxes as fractions of the source frame
  watermarks(text, own)            platform watermarks / @handles a re-posted video carries (TikTok, CapCut, ...),
                                   minus the brand's own handle
  free_caption_y(...)              the caption line's y on a 1080x1920 clip, moved off on-screen text when the default
                                   band would cover it (track and fit layouts; other layouts keep their default)
  alt_text(hook, text)             a plain description for screen readers and the Library search index

Uses: caption placement (studio/media/reframe/take_clip.py), the watermark warning before publishing (takes preflight)
and the Library search / alt text (studio/takes.py, studio/library.py). OCR never blocks a render or a post.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache
import gzip
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

MIN_VERSION = (5, 5, 4)
STEP_S = 2.0               # at most one frame per 2 s
MAX_FRAMES = 60            # per take
MAX_SECONDS = 30.0         # of OCR per take
FRAME_TIMEOUT = 8          # seconds per tesseract call
MIN_CONF = 60.0
FRAME_W = 960              # frames are scaled to this width before OCR
MAX_TEXT = 2000            # characters kept per clip
PREFIXES = ("/data/tesseract/bin/tesseract",)
PLATFORM_MARKS = ("tiktok", "capcut", "likee", "kwai", "snapchat", "triller", "douyin", "kuaishou")
HANDLE_RX = re.compile(r"(?<![\w.])@([A-Za-z0-9_](?:[A-Za-z0-9_.]{1,28}[A-Za-z0-9_])?)")


class Budget:
    """The take's OCR allowance, shared by every clip of one render job."""

    def __init__(self, seconds: float = MAX_SECONDS, frames: int = MAX_FRAMES):
        self.seconds, self.frames, self.used_s, self.used_frames = float(seconds), int(frames), 0.0, 0

    def left(self) -> bool:
        return self.used_s < self.seconds and self.used_frames < self.frames


def binary() -> str | None:
    """The tesseract program: HQ_TESSERACT, the worker's pinned build, else PATH."""
    for p in (os.environ.get("HQ_TESSERACT", ""), *PREFIXES):
        if p and Path(p).is_file() and os.access(p, os.X_OK):
            return p
    return shutil.which("tesseract")


def parse_version(out: str) -> tuple[int, int, int] | None:
    m = re.match(r"\s*tesseract\s+v?(\d+)\.(\d+)\.(\d+)", out or "")
    return tuple(int(x) for x in m.groups()) if m else None  # type: ignore[return-value]


def tessdata_for(exe: str) -> str | None:
    """The traineddata folder next to a prefix build (<prefix>/share/tessdata), else Tesseract's own default."""
    d = Path(exe).resolve().parent.parent / "share" / "tessdata"
    return str(d) if (d / "eng.traineddata").is_file() else None


def has_eng(exe: str, tessdata: str | None) -> bool:
    """Whether the tesseract program sees English data in the folder read_frame() will use (the prefix's tessdata, else
    Tesseract's own default location). Fixed arguments, no shell, a timeout."""
    cmd = [exe] + (["--tessdata-dir", tessdata] if tessdata else []) + ["--list-langs"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=10, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return any(line.strip() == "eng" for line in ((r.stdout or "") + "\n" + (r.stderr or "")).splitlines())


def status() -> dict:
    """{ok, version, path, why}: what the worker can report about its OCR (read-only, no frames)."""
    exe = binary()
    if not exe:
        return {"ok": False, "version": None, "path": None, "why": "tesseract not installed"}
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, errors="replace", timeout=10,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"ok": False, "version": None, "path": exe, "why": f"tesseract --version failed: {e.__class__.__name__}"}
    v = parse_version((r.stdout or "") + (r.stderr or ""))
    if not v:
        return {"ok": False, "version": None, "path": exe, "why": "unknown tesseract version"}
    ver = ".".join(map(str, v))
    if v < MIN_VERSION:
        return {"ok": False, "version": ver, "path": exe, "why": f"tesseract {ver} is older than {'.'.join(map(str, MIN_VERSION))}"}
    td = tessdata_for(exe)
    if not has_eng(exe, td):
        return {"ok": False, "version": ver, "path": exe, "why": "English language data (eng.traineddata) not found"}
    return {"ok": True, "version": ver, "path": exe, "why": "", "tessdata": td}


def sample_times(start: float, end: float, step: float = STEP_S, cap: int = MAX_FRAMES) -> list[float]:
    """Frame times inside [start, end]: one per `step` seconds (never denser), centred, at most `cap`."""
    start, end = float(start), float(end)
    if end <= start:
        return []
    step = max(float(step), STEP_S)
    n = max(1, min(int(cap), int((end - start) // step) or 1))
    gap = (end - start) / n
    return [round(start + gap * (k + 0.5), 3) for k in range(n)]


def parse_tsv(tsv: str, w: int, h: int, min_conf: float = MIN_CONF) -> list[dict]:
    """Word boxes from `tesseract ... tsv` as fractions of the frame: [{x, y, w, h, text, conf}]."""
    out = []
    for line in (tsv or "").splitlines()[1:]:
        f = line.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        try:
            left, top, bw, bh, conf = int(f[6]), int(f[7]), int(f[8]), int(f[9]), float(f[10])
        except ValueError:
            continue
        text = f[11].strip()
        if not text or conf < min_conf or bw <= 0 or bh <= 0 or not re.search(r"[A-Za-z0-9]", text):
            continue
        out.append({"x": round(left / w, 4), "y": round(top / h, 4), "w": round(bw / w, 4), "h": round(bh / h, 4),
                    "text": text[:64], "conf": round(conf, 1)})
    return out


def _grab(path: str, t: float, out: Path, timeout: float = 30) -> bool:
    """One frame at t, scaled to FRAME_W wide, within `timeout` seconds (never raised: the take's budget is a ceiling)."""
    from .. import mediasafe
    if timeout <= 0:
        return False
    try:
        r = mediasafe.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                           f"scale={FRAME_W}:-2", str(out)], timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and out.is_file()


def read_frame(exe: str, png: str, tessdata: str | None, timeout: float = FRAME_TIMEOUT) -> tuple[list[dict], tuple[int, int]]:
    """One frame -> word boxes. Fixed arguments, no shell, a timeout; a failure is an empty frame."""
    from PIL import Image
    with Image.open(png) as im:
        w, h = im.size
    cmd = [exe, png, "stdout", "-l", "eng", "--psm", "11", "--oem", "1"]
    if tessdata:
        cmd += ["--tessdata-dir", tessdata]
    cmd.append("tsv")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "OMP_THREAD_LIMIT": "1"})
    except (OSError, subprocess.SubprocessError, ValueError):
        return [], (w, h)
    if r.returncode != 0:
        return [], (w, h)
    return parse_tsv(r.stdout, w, h), (w, h)


def scan(path: str, start: float, end: float, budget: Budget | None = None, st: dict | None = None) -> dict:
    """OCR the span's frames within the budget. {ok, version, frames: [{t, boxes}], text, ms, skipped}."""
    budget = budget or Budget()
    st = st or status()
    res = {"ok": False, "version": st.get("version"), "frames": [], "text": "", "ms": 0, "skipped": ""}
    if not st.get("ok"):
        res["skipped"] = st.get("why") or "tesseract unavailable"
        return res
    if not budget.left():
        res["skipped"] = "take OCR budget used"
        return res
    t0 = time.monotonic()
    tmp = Path(tempfile.mkdtemp(prefix="hq_ocr_"))
    seen, words = set(), []
    try:
        for k, t in enumerate(sample_times(start, end, cap=budget.frames - budget.used_frames)):
            if not budget.left():
                res["skipped"] = "take OCR budget used"
                break
            allow = min(30.0, budget.seconds - budget.used_s)
            if allow <= 0.05:
                res["skipped"] = "take OCR budget used"
                break
            f0 = time.monotonic()
            png = tmp / f"f{k}.png"
            boxes = []
            if _grab(path, t, png, timeout=allow):
                left = budget.seconds - budget.used_s - (time.monotonic() - f0)  # what the extraction left of the budget
                if left > 0.2:
                    boxes, _ = read_frame(st["path"], str(png), st.get("tessdata"), timeout=min(FRAME_TIMEOUT, left))
                png.unlink(missing_ok=True)
            budget.used_s += time.monotonic() - f0
            budget.used_frames += 1
            res["frames"].append({"t": round(t - float(start), 3), "boxes": boxes[:80]})
            for b in boxes:
                key = b["text"].lower()
                if key not in seen:
                    seen.add(key)
                    words.append(b["text"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res.update(ok=True, text=" ".join(words)[:MAX_TEXT], readable=readable_from_frames(res["frames"]),
               ms=int((time.monotonic() - t0) * 1000))
    return res


HANDLE_MIN = 3      # a handle read shorter than this is a fragment ("@s" while the watermark slides in)
_HANDLE_TOK = re.compile(r"(?<![\w@.])([A-Za-z0-9_][A-Za-z0-9_.]{2,28}[A-Za-z0-9_])(?![\w@])")


def _complete_handle(h: str, text: str) -> str | None:
    """A short handle fragment completed from the other frames' reads ("@s" + "scrollclips" -> "scrollclips"): the most
    frequent (then longest) non-dictionary token that starts with it. None when nothing completes a < HANDLE_MIN read."""
    if len(h) >= 4:
        return h
    hl = h.lower()
    toks = [t for t in _HANDLE_TOK.findall(text or "") if t.lower().startswith(hl) and len(t) > len(h)
            and not _in_dict(t.rstrip("."))]
    if toks:
        return max(sorted(set(toks)), key=lambda t: (toks.count(t), len(t)))
    return h if len(h) >= HANDLE_MIN else None


def watermarks(text: str, own_handle: str = "") -> list[str]:
    """Platform watermarks and @handles in on-screen text, the brand's own handle left out. Lower-case, deduplicated.
    OCR of a moving watermark reads pieces of the handle: a short fragment is completed from the other reads, a fragment
    nothing completes is dropped, and a handle that is the start of a longer one found too is folded into it."""
    low = (text or "").lower()
    own = (own_handle or "").lower().lstrip("@").strip()
    hits = [m for m in PLATFORM_MARKS if re.search(rf"(?<![a-z]){m}(?![a-z])", low)]
    handles: list[str] = []
    for h in HANDLE_RX.findall(text or ""):
        full = _complete_handle(h, text)
        if not full:
            continue
        hl = full.lower()
        if hl != own and hl not in handles:
            handles.append(hl)
    handles = [h for h in handles if not any(o != h and o.startswith(h) for o in handles)]
    hits += [f"@{h}" for h in handles if f"@{h}" not in hits]
    return hits[:10]


def _to_out_y(b: dict, layout: str, src_w: int, src_h: int, H: int, W: int,
              crop: tuple[float, float] | None = None) -> tuple[float, float] | None:
    """A source box's vertical extent on the WxH clip, for layouts whose vertical mapping is known. crop: the tracked
    crop's (top, height) in source pixels at that moment (a zoomed track crop), else the full frame height."""
    if layout == "track":  # the crop window scaled to the clip's height
        top, ch = crop if crop and crop[1] > 0 else (0.0, float(src_h))
        return (b["y"] * src_h - top) / ch * H, ((b["y"] + b["h"]) * src_h - top) / ch * H
    if layout == "fit":   # the whole frame scaled to the clip's width, centred
        fh = W * src_h / max(1, src_w)
        top = (H - fh) / 2
        return top + b["y"] * fh, top + (b["y"] + b["h"]) * fh
    return None


def free_caption_y(frames: list[dict], seg: dict, default_y: int, src_w: int, src_h: int, W: int = 1080, H: int = 1920,
                   line_h: int = 170, safe_top: int = 290, safe_bottom: int = 1470, crop_at=None) -> int | None:
    """A caption y for the sidecar segment `seg` ({start, end, layout} in clip seconds) that stays off on-screen text,
    or None to keep the default (no text in the band, an unmapped layout, or no free band). crop_at(t) gives a tracked
    crop's (top, height) in source pixels at clip time t, or None for the full frame."""
    lay = seg.get("layout", "track")
    spans = []
    for f in frames or []:
        if not (seg.get("start", 0) - 1e-6 <= f.get("t", -1) < seg.get("end", 0) + 1e-6):
            continue
        boxes = [b for b in f.get("boxes") or [] if isinstance(b, dict)]
        for b in boxes:
            y = _to_out_y(b, lay, src_w, src_h, H, W, crop_at(f["t"]) if crop_at and lay == "track" else None)
            if y is None:
                return None
            spans.append((y[0] - 12, y[1] + 12))
    if not spans:
        return None

    def clear(y):
        return all(y + line_h / 2 <= a or y - line_h / 2 >= b for a, b in spans)
    if clear(default_y):
        return None
    lo, hi = safe_top + line_h // 2, safe_bottom - line_h // 2
    cands = sorted(range(lo, hi + 1, 20), key=lambda y: (abs(y - default_y), y < default_y))  # nearest, lower first
    for y in cands:
        if clear(y):
            return int(y)
    return None


def main(argv: list[str] | None = None) -> int:
    """`python -m studio.media.ocr` prints the worker's OCR status as JSON (read-only: no frames, no network)."""
    import json
    import sys
    st = status()
    print(json.dumps({**st, "min_version": ".".join(map(str, MIN_VERSION))}))
    return 0 if st["ok"] or "--soft" in (argv if argv is not None else sys.argv[1:]) else 1


# ---------------------------------------------------------------------- readable on-screen text (alt text)
WORDS_FILE = Path(__file__).resolve().parent / "lexicon" / "words_en.txt.gz"  # SCOWL (en_US + en_GB size 50), see NOTICE
ALT_MIN_CONF = 60.0     # a dictionary word needs at least this confidence
ALT_SEEN_CONF = 65.0    # a word not in the dictionary needs this mean confidence over 2+ frames ...
ALT_SURE_CONF = 90.0    # ... or this in one frame
SHORT_OK = frozenset({"a", "i", "an", "to", "of", "in", "on", "at", "by", "be", "is", "it", "or", "as", "we", "us", "my",
                      "no", "do", "go", "so", "up", "if", "me", "he", "ai", "tv", "uk", "eu", "vs", "ok"})
_EDGE = "\"'“”‘’«»()[]{}<>.,;:!?…*_|~`^"
_VOWEL = re.compile(r"[aeiouyàáâäãåèéêëìíîïòóôöõùúûüýæœ]", re.I)


@lru_cache(maxsize=1)
def _words() -> frozenset:
    """The bundled English word list (lower case). Empty when the file is missing: then only repeated, confident reads
    pass, never noise."""
    try:
        with gzip.open(WORDS_FILE, "rt", encoding="utf-8") as fh:
            return frozenset(w.strip() for w in fh if w.strip())
    except OSError:
        return frozenset()


def _kind(core: str) -> str:
    if re.fullmatch(r"\d[\d.,:%+/-]*", core):
        return "num"
    if re.fullmatch(r"[^\W\d_]+(?:['’.-][^\W\d_]+)*", core):
        return "word"
    if re.fullmatch(r"[^\W_]+", core) and re.search(r"\d", core) and re.search(r"[^\W\d_]", core):
        return "mixed"  # v2, 4K, iOS17
    return "junk"


def _in_dict(core: str) -> bool:
    w, d = core.lower().replace("’", "'"), _words()
    return w in d or (w.endswith("s") and w[:-1] in d) or (w.endswith("'s") and w[:-2] in d)


def readable_text(seq: list[list[tuple[str, float]]], limit: int = 500) -> str:
    """On-screen text fit to read aloud: from OCR reads in order (one list of (text, conf) per frame), only real words
    (in the dictionary at >= ALT_MIN_CONF, or seen in 2+ frames at >= ALT_SEEN_CONF mean, or one read at >=
    ALT_SURE_CONF; letters with a vowel, 3+ long, 4+ outside the dictionary), with 1-2 letter words, numbers and short
    codes (v2) kept only next to such a word. Single letters, shirt patterns and torn pieces of a moving watermark
    ("CGC", "liclip", "GS G YY") are dropped. Deduplicated, in reading order."""
    stats: dict[str, list[float]] = {}
    for frame in seq:
        best: dict[str, float] = {}
        for t, c in frame:
            k = t.strip(_EDGE).lower()
            best[k] = max(best.get(k, 0.0), float(c))
        for k, c in best.items():
            stats.setdefault(k, []).append(c)

    def strong(core: str, conf: float) -> bool:
        if _kind(core) != "word" or len(core) < 3 or not _VOWEL.search(core):
            return False
        if _in_dict(core):
            return conf >= ALT_MIN_CONF
        seen = stats.get(core.lower(), [])
        return len(core) >= 4 and ((len(seen) >= 2 and sum(seen) / len(seen) >= ALT_SEEN_CONF) or conf >= ALT_SURE_CONF)

    out, seen_out = [], set()
    for frame in seq:
        cores = [(t.strip(_EDGE), float(c)) for t, c in frame]
        good = [strong(core, c) for core, c in cores]
        for i, (core, c) in enumerate(cores):
            if not core:
                continue
            before, after = i > 0 and good[i - 1], i + 1 < len(good) and good[i + 1]
            # a 1-letter word needs real words on both sides ("Plan a trip"), anything else short one beside it
            near = (before and after) if len(core) == 1 and _kind(core) == "word" else (before or after)
            k = _kind(core)
            keep = good[i] or (near and c >= ALT_MIN_CONF and (
                (k == "word" and core.lower() in SHORT_OK) or k == "num" or (k == "mixed" and len(core) >= 2)))
            if keep and core.lower() not in seen_out:
                seen_out.add(core.lower())
                out.append(core)
    return " ".join(out)[:limit]


def readable_from_frames(frames: list[dict] | None, limit: int = 500) -> str:
    """readable_text over scan()'s frames (boxes in Tesseract's reading order, with their confidence)."""
    seq = []
    for f in frames or []:
        if isinstance(f, dict):
            seq.append([(str(b.get("text") or ""), float(b.get("conf") or 0.0)) for b in f.get("boxes") or []
                        if isinstance(b, dict)])
    return readable_text(seq, limit)


def alt_text(hook: str, text: str, limit: int = 420, cleaned: bool = False) -> str:
    """A plain description of a clip: what is said (the hook) and the readable text on screen. ``text`` is OCR output;
    unless ``cleaned`` (already readable_text), it is filtered here (no confidence known, so dictionary words only)."""
    hook = re.sub(r"\s+", " ", (hook or "").strip().rstrip("…").strip())
    if len(hook) > limit // 2:  # a long hook never pushes the on-screen text out of the limit
        hook = hook[:limit // 2].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    text = re.sub(r"\s+", " ", text or "").strip()
    if text and not cleaned:
        text = readable_text([[(t, ALT_MIN_CONF) for t in text.split(" ")]])
    out = f"Video clip: “{hook}”" if hook else "Video clip"
    if text:
        # one sentence end, never two: “time.” On-screen text ... (not “time.”. On-screen text)
        out += (" " if re.search(r"[.?!]$", hook) else ". ") + f"On-screen text: {text}"
    out = re.sub(r"([.?!])(”?)\s*\.", r"\1\2", out)
    return out[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
