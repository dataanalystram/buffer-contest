"""Words behind you: a One take clip's hook word drawn BEHIND the speaker for the first seconds (owner ask, 25 Nov 2026).

The look: one to three words of the hook, huge, in the band above the speaker's head; the head and hair pass in front of
the letters, so the word sits in the scene instead of on top of it. Then it fades and the clip carries on as rendered.
Plan: /mnt/project-files/plans/one-take-clip-studio-plan.md, slice 1. Ideas from the owner's reel-kit ("behind text":
big sizes, the head overlaps the word's lower part, at most a few per minute); no code copied.

How: the finished 9:16 clip is decoded once; inside the window only, MediaPipe's Image Segmenter (selfie model,
Apache-2.0, downloaded once from Google's model bucket like the face model) gives a person mask per frame; the word is
drawn once with the brand kit's display font and composited UNDER the person. Frames outside the window pass through,
the audio is copied byte for byte. Off unless HQ_HOOK_BEHIND=1 (every merge to main changes the live worker).

Fail open: no MediaPipe, no model, no person in the window, or ffmpeg trouble -> BehindError, and the caller keeps the
clip as rendered with the reason in its meta.

Pure parts (pick_words, place, alpha_at, composite) are tested without MediaPipe or ffmpeg.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

SEG_MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/"
                 "selfie_segmenter.tflite")
FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
WINDOW_S = 2.6          # how long the word stays (after the cover frame)
FADE_IN, FADE_OUT = 0.22, 0.35
RISE = 0.035            # the word rises this share of the frame height while it fades in
TOP_SAFE = 0.07         # Reels / TikTok top UI
MAX_HIDDEN = 0.22       # at most this share of the word's ink may sit behind the person, or it stops reading ("H...W")
MAX_WORDS, MAX_CHARS = 3, 18
STOP = {"a", "an", "the", "and", "but", "or", "so", "to", "of", "in", "on", "at", "for", "is", "it", "its", "it's",
        "i", "we", "you", "my", "our", "your", "this", "that", "was", "are", "be", "with", "just", "really", "very"}
MIN_PERSON = 0.04       # share of the frame the mask must cover to count as a person


class BehindError(RuntimeError):
    """A plain reason, kept in the clip's meta (the clip renders without the effect)."""


def enabled() -> bool:
    return os.environ.get("HQ_HOOK_BEHIND", "").strip().lower() in ("1", "true", "yes", "on")


def _clean(w: str) -> str:
    return re.sub(r"[^\w'’-]", "", w, flags=re.UNICODE)


def pick_words(hook: str, words: str | None = None) -> str:
    """The text to draw: the given words when they are said in the hook (extract, don't invent), else the hook's
    strongest word (the longest one that isn't a stop word). Upper case, at most MAX_WORDS words / MAX_CHARS chars."""
    said = [_clean(w) for w in (hook or "").split()]
    said = [w for w in said if w]
    if words and words.strip():
        want = [_clean(w) for w in words.split() if _clean(w)]
        low = [w.lower() for w in said]
        if want and len(want) <= MAX_WORDS and all(w.lower() in low for w in want):
            out = " ".join(want)
            if len(out) <= MAX_CHARS:
                return out.upper()
        raise BehindError("The words behind you must be 1 to 3 words said in the hook (at most 18 letters).")
    best = sorted((w for w in said if w.lower() not in STOP and len(w) <= MAX_CHARS), key=lambda w: -len(w))
    if not best:
        raise BehindError("The hook has no word to put behind the speaker.")
    return best[0].upper()


def head_top(mask, thr: float = 0.5, min_share: float = 0.02) -> int | None:
    """The first row (from the top) where the person mask is at least min_share of the width: the top of the head."""
    import numpy as np
    rows = (mask > thr).mean(axis=1)
    hit = np.nonzero(rows >= min_share)[0]
    return int(hit[0]) if len(hit) else None


def head_x(mask, top: int | None, thr: float = 0.5) -> int | None:
    """The head's centre column: the middle of the person's pixels in the band just under the top of the head."""
    import numpy as np
    if top is None:
        return None
    H = mask.shape[0]
    band = mask[top:min(H, top + max(4, H // 12))] > thr
    cols = np.nonzero(band.any(axis=0))[0]
    return int((cols[0] + cols[-1]) // 2) if len(cols) else None


def place(W: int, H: int, top: int | None, text_w: int, text_h: int, cx: int | None = None) -> tuple[int, int]:
    """(x, y) of the word's box: over the head (centred on it, kept inside the frame), the head overlapping the lower
    part of the word (reel-kit's rule), never above the top UI. No head found: centred in the upper band at 18%."""
    x = (W - text_w) // 2 if cx is None else int(cx - text_w / 2)
    x = max(0, min(x, W - text_w)) if text_w <= W else (W - text_w) // 2
    y = int(H * 0.18) if top is None else int(top - text_h * 0.45)
    return x, max(int(H * TOP_SAFE), min(y, int(H * 0.45)))


def hidden_share(layer_a, x: int, y: int, person) -> float:
    """The share of the word's ink (alpha) that the person covers when the word's box is at (x, y)."""
    import numpy as np
    H, W = person.shape[:2]
    h, w = layer_a.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    ink = float(layer_a.sum())
    if ink <= 0 or x1 <= x0 or y1 <= y0:
        return 0.0
    a = layer_a[y0 - y:y1 - y, x0 - x:x1 - x]
    return float((a * np.clip(person[y0:y1, x0:x1], 0, 1)).sum()) / ink


def settle(H: int, pos: tuple[int, int], layer_a, person, max_hidden: float = MAX_HIDDEN) -> tuple[tuple[int, int], float]:
    """Raise the word until the person hides at most ``max_hidden`` of it, so it still reads (the head may only
    overlap its lower edge). Raises BehindError when even the highest safe place hides too much."""
    x, y = pos
    floor, step = int(H * TOP_SAFE), max(2, int(H * 0.01))
    hid = hidden_share(layer_a, x, y, person)
    while hid > max_hidden and y > floor:
        y = max(floor, y - step)
        hid = hidden_share(layer_a, x, y, person)
    if hid > max_hidden:
        raise BehindError("The person would hide too much of the word to read it, so the clip was left as it was.")
    return (x, y), round(hid, 3)


def fit_size(font_path: str, text: str, W: int, H: int) -> int:
    """The largest size that keeps the word within 92% of the width and 16% of the height."""
    from PIL import ImageFont
    size = int(H * 0.16)
    while size > 24:
        f = ImageFont.truetype(font_path, size)
        l, t, r, b = f.getbbox(text)
        if r - l <= W * 0.92 and b - t <= H * 0.16:
            return size
        size -= 4
    return size


def text_layer(text: str, font_path: str, size: int, color: str = "#FFFFFF"):
    """The word as an RGBA image cropped to its ink (a soft shadow under it so it reads on a bright wall)."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    f = ImageFont.truetype(font_path, size)
    l, t, r, b = f.getbbox(text)
    pad = max(8, size // 10)
    w, h = r - l + 2 * pad, b - t + 2 * pad
    shadow = Image.new("L", (w, h), 0)
    ImageDraw.Draw(shadow).text((pad - l + size // 40, pad - t + size // 30), text, font=f, fill=110)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(2, size // 30)))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.putalpha(shadow)
    ImageDraw.Draw(img).text((pad - l, pad - t), text, font=f, fill=color)
    return img


def alpha_at(t: float, start: float, dur: float) -> float:
    """The word's opacity at clip time t: eased in over FADE_IN, held, eased out over FADE_OUT."""
    u = t - start
    if u < 0 or u > dur:
        return 0.0
    a = min(1.0, u / FADE_IN) if FADE_IN else 1.0
    if dur - u < FADE_OUT:
        a = min(a, (dur - u) / FADE_OUT)
    return 1 - (1 - a) ** 3


def composite(frame, layer_rgb, layer_a, x: int, y: int, person, alpha: float):
    """frame (HxWx3 uint8) with the word (layer_rgb HxWx3 float, layer_a HxW 0..1) at (x, y) under the person mask
    (HxW 0..1, 1 = person). Returns a new uint8 frame."""
    import numpy as np
    H, W = frame.shape[:2]
    h, w = layer_a.shape
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    if x1 <= x0 or y1 <= y0 or alpha <= 0:
        return frame
    out = frame.astype(np.float32)
    lx0, ly0 = x0 - x, y0 - y
    a = layer_a[ly0:ly0 + (y1 - y0), lx0:lx0 + (x1 - x0)] * alpha * (1.0 - person[y0:y1, x0:x1])
    a = a[..., None]
    out[y0:y1, x0:x1] = out[y0:y1, x0:x1] * (1 - a) + layer_rgb[ly0:ly0 + (y1 - y0), lx0:lx0 + (x1 - x0)] * a
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def model_path(path: str | None = None) -> str:
    p = Path(path or os.environ.get("HQ_SEG_MODEL") or str(Path(tempfile.gettempdir()) / "selfie_segmenter.tflite"))
    if not p.exists() or p.stat().st_size < 1000:
        import httpx  # a fixed first-party URL (no user input), so no netguard
        try:
            r = httpx.get(SEG_MODEL_URL, timeout=60, follow_redirects=True)
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise BehindError(f"The person-mask model couldn't be downloaded ({e.__class__.__name__}).") from e
        if len(r.content) < 1000:
            raise BehindError("The person-mask model download was empty.")
        p.write_bytes(r.content)
    return str(p)


def segmenter(path: str | None = None):
    """MediaPipe ImageSegmenter (VIDEO mode, confidence mask) with the selfie model. BehindError if it can't load."""
    try:
        from mediapipe.tasks.python import BaseOptions, vision  # Apache-2.0, already a take-worker dependency
    except Exception as e:  # noqa: BLE001
        raise BehindError(f"MediaPipe isn't installed on this worker ({e.__class__.__name__}).") from e
    try:
        return vision.ImageSegmenter.create_from_options(vision.ImageSegmenterOptions(
            base_options=BaseOptions(model_asset_path=model_path(path)), running_mode=vision.RunningMode.VIDEO,
            output_confidence_masks=True, output_category_mask=False))
    except BehindError:
        raise
    except Exception as e:  # noqa: BLE001 - no libEGL, a bad model
        raise BehindError(f"MediaPipe couldn't load the person-mask model: {str(e)[:80]}") from e


def person_mask(seg, rgb, ts_ms: int):
    import numpy as np
    try:
        import mediapipe as mp  # only reached with a segmenter from segmenter(); optional on the light worker
    except ImportError as e:
        raise BehindError(f"MediaPipe isn't installed on this worker ({e.__class__.__name__}).") from e
    res = seg.segment_for_video(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)), ts_ms)
    m = res.confidence_masks[-1].numpy_view()  # selfie model: one mask, 1 = person
    return np.array(m, dtype=np.float32, copy=True).reshape(rgb.shape[:2])  # a copy: the view dies with res


def _font(kit_font: str | None) -> str:
    from ..brand import FONTS
    f = FONT_DIR / FONTS.get((kit_font or "").lower(), "Anton.ttf")
    return str(f if f.exists() else FONT_DIR / "Anton.ttf")


def _probe(path: str) -> dict:
    import json
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,nb_frames:format=duration", "-of", "json", path],
                       capture_output=True, text=True, timeout=60)
    j = json.loads(r.stdout or "{}")
    s = (j.get("streams") or [{}])[0]
    num, _, den = str(s.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    return {"w": int(s.get("width") or 0), "h": int(s.get("height") or 0), "fps": fps or 30.0,
            "duration": float((j.get("format") or {}).get("duration") or 0)}


def apply(src: str, out: str, text: str, *, start: float, dur: float = WINDOW_S, kit_font: str | None = None,
          color: str = "#FFFFFF", seg=None, crf: int = 20, timeout: int = 900) -> dict:
    """Writes `out`: `src` with `text` behind the person from `start` for `dur` seconds. Returns what was done."""
    import numpy as np
    t0 = time.time()
    info = _probe(src)
    W, H, fps = info["w"], info["h"], info["fps"]
    if not W or not H:
        raise BehindError("The clip couldn't be read.")
    dur = max(0.6, min(dur, info["duration"] - start - 0.1))
    if dur < 0.6:
        raise BehindError("The clip is too short for words behind you.")
    own = seg is None
    seg = seg or segmenter()
    font = _font(kit_font)
    size = fit_size(font, text, W, H)
    layer = text_layer(text, font, size, color)
    lrgb = np.asarray(layer.convert("RGB"), dtype=np.float32)
    la = np.asarray(layer.getchannel("A"), dtype=np.float32) / 255.0
    lh, lw = la.shape
    f0, f1 = int(round(start * fps)), int(round((start + dur) * fps))
    dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           stdout=subprocess.PIPE)
    enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                            "-r", f"{fps:.6f}", "-i", "-", "-i", src, "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
                            "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "copy",
                            "-movflags", "+faststart", out], stdin=subprocess.PIPE)
    n, masked, prev, pos, tops, hid = 0, 0, None, None, [], None
    size_b = W * H * 3
    try:
        while True:
            buf = dec.stdout.read(size_b)
            if len(buf) < size_b:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            if f0 <= n < f1:
                m = person_mask(seg, frame, int(n * 1000 / fps))
                prev = m if prev is None else prev * 0.35 + m * 0.65   # a little temporal smoothing: no edge shimmer
                if pos is None:
                    if (prev > 0.5).mean() < MIN_PERSON:
                        raise BehindError("No person was found at the start of the clip, so there is nothing to go behind.")
                    tops.append(head_top(prev))
                    pos = place(W, H, tops[0], lw, lh, head_x(prev, tops[0]))
                    pos, hid = settle(H, pos, la, np.clip(prev, 0, 1))
                t = n / fps
                a = alpha_at(t, start, dur)
                rise = int(H * RISE * (1 - min(1.0, (t - start) / FADE_IN)) ** 2) if FADE_IN else 0
                frame = composite(frame, lrgb, la, pos[0], pos[1] + rise, np.clip(prev, 0, 1), a)
                masked += 1
            enc.stdin.write(frame.tobytes())
            n += 1
        enc.stdin.close()
        if enc.wait(timeout=timeout) != 0:
            raise BehindError("The clip couldn't be encoded again with the words behind you.")
    except BrokenPipeError as e:
        raise BehindError("The encoder stopped while writing the clip.") from e
    finally:
        dec.kill()
        if enc.poll() is None:
            enc.kill()
        if own:
            try:
                seg.close()
            except Exception:  # noqa: BLE001
                pass
    if not masked:
        raise BehindError("The clip ended before the words behind you could start.")
    return {"on": True, "text": text, "start": round(start, 3), "secs": round(dur, 3), "frames": masked,
            "font": Path(font).stem, "size": size, "xy": list(pos) if pos else None, "head_top": tops[0] if tops else None,
            "hidden": hid,
            "mask": "mediapipe selfie_segmenter", "ms": int((time.time() - t0) * 1000)}


def on_clip(data: bytes, hook: str, words: str | None, kit: dict | None, start: float) -> tuple[bytes, dict]:
    """The worker's entry point: the rendered clip's bytes in, (new bytes, meta) out. Fails open with the reason."""
    try:
        text = pick_words(hook, words)
        with tempfile.TemporaryDirectory(prefix="hq_behind_") as d:
            a, b = Path(d) / "in.mp4", Path(d) / "out.mp4"
            a.write_bytes(data)
            kit = kit or {}
            meta = apply(str(a), str(b), text, start=start, kit_font=kit.get("font"),
                         color=(kit.get("colors") or {}).get("text") or "#FFFFFF")
            return b.read_bytes(), meta
    except BehindError as e:
        return data, {"on": False, "why": str(e)}
    except Exception as e:  # noqa: BLE001 - never lose a rendered clip to the effect
        return data, {"on": False, "why": f"Words behind you failed ({e.__class__.__name__}); the clip is kept as rendered."}
