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
    res.update(ok=True, text=" ".join(words)[:MAX_TEXT], ms=int((time.monotonic() - t0) * 1000))
    return res


def watermarks(text: str, own_handle: str = "") -> list[str]:
    """Platform watermarks and @handles in on-screen text, the brand's own handle left out. Lower-case, deduplicated."""
    low = (text or "").lower()
    own = (own_handle or "").lower().lstrip("@").strip()
    hits = [m for m in PLATFORM_MARKS if re.search(rf"(?<![a-z]){m}(?![a-z])", low)]
    for h in HANDLE_RX.findall(text or ""):
        hl = h.lower()
        if hl != own and f"@{hl}" not in hits:
            hits.append(f"@{hl}")
    return hits[:10]


CAPTION_CONF = 75.0        # caption placement only trusts a box this confident (alt text / search keep MIN_CONF)


def usable(b: dict, min_conf: float = CAPTION_CONF) -> bool:
    """A box that is real on-screen text, not a shirt pattern, a logo stroke or a texture Tesseract read as letters:
    confident, two or more letters / digits, a word-like shape and size (fractions of the source frame)."""
    try:
        conf, w, h = float(b.get("conf", 0)), float(b["w"]), float(b["h"])
        text = str(b.get("text") or "")
    except (KeyError, TypeError, ValueError):
        return False
    alnum = sum(ch.isalnum() for ch in text)
    if conf < min_conf or alnum < 2 or alnum < len(text.strip()) * 0.6:
        return False
    return 0.008 <= h <= 0.25 and 0 < w <= 1.0 and w / h <= 60


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
                   line_h: int = 170, safe_top: int = 290, safe_bottom: int = 1470, crop_at=None, crop_x=None,
                   avoid: list[tuple[int, int]] | None = None) -> int | None:
    """A caption y for the sidecar segment `seg` ({start, end, layout} in clip seconds) that stays off on-screen text
    and off the ``avoid`` spans (the face, studio/media/facesafe.track_face_spans: (y0, y1) on the clip), or None to keep
    the default (nothing in the band, an unmapped layout, or no free band). crop_at(t) gives a tracked crop's (top,
    height) in source pixels at clip time t, or None for the full frame; crop_x(t) its (left, width): text outside the
    visible crop is never on screen and never moves the captions. Only ``usable`` boxes count (bug 3, 6 Nov 2026: a
    shirt pattern read as text pushed the captions onto the face)."""
    lay = seg.get("layout", "track")
    spans = []
    for f in frames or []:
        if not isinstance(f, dict) or not (seg.get("start", 0) - 1e-6 <= f.get("t", -1) < seg.get("end", 0) + 1e-6):
            continue
        boxes = [b for b in f.get("boxes") or [] if isinstance(b, dict) and usable(b)]
        for b in boxes:
            if lay == "track" and crop_x:
                cx = crop_x(f["t"])
                if cx and (float(b["x"] + b["w"]) * src_w <= cx[0] or float(b["x"]) * src_w >= cx[0] + cx[1]):
                    continue  # outside the crop: not on screen
            y = _to_out_y(b, lay, src_w, src_h, H, W, crop_at(f["t"]) if crop_at and lay == "track" else None)
            if y is None:
                return None
            if y[1] <= 0 or y[0] >= H:
                continue  # above or below the crop window
            spans.append((y[0] - 12, y[1] + 12))
    face = [(float(a), float(b)) for a, b in (avoid or []) if b > a] if lay == "track" else []
    if not spans and not face:
        return None
    spans = spans + face

    def clear(y):
        return all(y + line_h / 2 <= a or y - line_h / 2 >= b for a, b in spans)
    if clear(default_y):
        return None
    lo, hi = safe_top + line_h // 2, safe_bottom - line_h // 2
    edges = {int(b + line_h / 2 + 0.999) for _, b in spans} | {int(a - line_h / 2) for a, _ in spans}  # snug spots too
    grid = sorted(set(range(lo, hi + 1, 20)) | {y for y in edges if lo <= y <= hi})
    # nearest first, lower first; with a face to avoid, under the chin before above the head
    cands = sorted(grid, key=lambda y: (bool(face) and y < default_y, abs(y - default_y), y < default_y))
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


def alt_text(hook: str, text: str, limit: int = 420) -> str:
    """A plain description of a clip: what is said (the hook) and the text on screen."""
    hook = (hook or "").strip().rstrip("…")
    text = re.sub(r"\s+", " ", text or "").strip()
    out = f"Video clip: “{hook}”" if hook else "Video clip"
    if text:
        out += f". On-screen text: {text}"
    return out[:limit]


if __name__ == "__main__":
    raise SystemExit(main())
