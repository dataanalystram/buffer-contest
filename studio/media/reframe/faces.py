"""Faces on one clip span: YuNet (OpenCV ``cv2.FaceDetectorYN``), sampled at 5 fps, IoU-tracked.

* The model is vendored (``models/face_detection_yunet_2023mar.onnx``, MIT, from opencv_zoo) and checked against its
  sha256 before it loads, so a swapped or truncated file never runs. Credited in NOTICE.
* Detection runs at 640 px wide on every sample. Where that finds no face, or only a small one (a webcam bubble in a
  screen recording is ~110 px tall at 1080p and ~37 px at 640), it runs again at full resolution once a second.
* Only the clip span is decoded, never the whole source.
* Without OpenCV (or with a bad model file) the analysis' face samples (MediaPipe / Haar, 2 fps) are used instead.

A sample is ``{"t": clip seconds, "faces": [{"x","y","w","h" (0-1 of the frame), "score", "mouth": patch|None}]}``.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

MODEL = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
FPS = 5.0
DET_W = 640            # detection width of the fast pass
SMALL_PX = 36          # a face under this many px tall at DET_W gets a full-resolution look
MAX_W = 1920           # "full resolution" is capped at 1920 wide (4K sources decode at 1920)
THUMB_W = 320          # grey thumbnails kept for the screen signals
MIN_SCORE = 0.7


class FaceModelError(RuntimeError):
    pass


def model_ok(path: Path = MODEL) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == MODEL_SHA256
    except OSError:
        return False


def detector(w: int, h: int, path: Path = MODEL, score: float = MIN_SCORE):
    """A YuNet detector for w x h frames. Raises FaceModelError when OpenCV or the checked model isn't there."""
    try:
        import cv2  # opencv-python-headless (Apache-2.0)
    except Exception as e:  # noqa: BLE001
        raise FaceModelError(f"OpenCV isn't installed ({e.__class__.__name__})")
    if not hasattr(cv2, "FaceDetectorYN"):
        raise FaceModelError("this OpenCV has no FaceDetectorYN")
    if not model_ok(path):
        raise FaceModelError("the YuNet model file is missing or its sha256 doesn't match")
    return cv2.FaceDetectorYN.create(str(path), "", (int(w), int(h)), score, 0.3, 50)


def _boxes(det, img, sx: float, sy: float, W: int, H: int) -> list[dict]:
    """YuNet rows (x, y, w, h, 5 landmarks, score) -> boxes as 0-1 of the full frame, landmarks in full-frame px."""
    det.setInputSize((img.shape[1], img.shape[0]))
    _, rows = det.detect(img)
    out = []
    for r in rows if rows is not None else []:
        x, y, w, h = float(r[0]) * sx, float(r[1]) * sy, float(r[2]) * sx, float(r[3]) * sy
        lm = [(float(r[4 + 2 * k]) * sx, float(r[5 + 2 * k]) * sy) for k in range(5)]
        out.append({"x": max(0.0, x / W), "y": max(0.0, y / H), "w": w / W, "h": h / H, "score": round(float(r[14]), 3), "lm": lm})
    return out


def _mouth(gray, f: dict, W: int, H: int):
    """A small grey patch around the mouth (between the two mouth-corner landmarks), for frame-to-frame motion."""
    import cv2
    (rx, ry), (lx, ly) = f["lm"][3], f["lm"][4]
    cx, cy = (rx + lx) / 2, (ry + ly) / 2
    half = max(4.0, abs(lx - rx) * 0.75)
    x0, x1 = int(max(0, cx - half)), int(min(W, cx + half))
    y0, y1 = int(max(0, cy - half * 0.6)), int(min(H, cy + half * 0.6))
    if x1 - x0 < 4 or y1 - y0 < 3:
        return None
    return cv2.resize(gray[y0:y1, x0:x1], (24, 12), interpolation=cv2.INTER_AREA).astype("float32")


def frames(path: str, start: float, end: float, fps: float = FPS, width: int | None = None, src_w: int = 1920, src_h: int = 1080):
    """Yield (t_clip, bgr frame) over [start, end] of the source at ``fps``, decoded by ffmpeg (hardened)."""
    import numpy as np

    from ... import mediasafe
    W = min(src_w, width or MAX_W)
    W -= W % 2
    H = int(round(W * src_h / max(1, src_w) / 2)) * 2
    proc = mediasafe.popen(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{max(0.1, end - start):.3f}",
                            "-i", path, "-vf", f"fps={fps},scale={W}:{H}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE)
    size, k = W * H * 3, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield round(k / fps, 3), np.frombuffer(buf, dtype=np.uint8).reshape(H, W, 3)
            k += 1
    finally:
        proc.kill()
        proc.wait()


def sample(path: str, start: float, end: float, src_w: int, src_h: int, fps: float = FPS) -> dict:
    """Face samples + grey thumbnails over the clip span. Raises FaceModelError when YuNet can't run here.
    Decodes at 640 wide (fast); when most samples find no face or only a small one, a second 1 fps pass at full
    resolution looks again (a webcam bubble in a screen recording)."""
    import cv2
    W = min(src_w, MAX_W) - min(src_w, MAX_W) % 2
    H = int(round(W * src_h / max(1, src_w) / 2)) * 2
    dw = min(DET_W, W)  # frames() never upscales: a source narrower than 640 decodes at its own width
    dh = int(round(dw * H / W / 2)) * 2
    fast = detector(dw, dh)
    out, thumbs = [], {}
    th = int(round(THUMB_W * H / W / 2)) * 2
    small_or_none = 0
    for t, img in frames(path, start, end, fps, dw, src_w, src_h):
        dh = img.shape[0]
        found = [f for f in _boxes(fast, img, 1.0, 1.0, img.shape[1], dh) if f["score"] >= MIN_SCORE]
        if not found or max(f["h"] for f in found) * dh < SMALL_PX:
            small_or_none += 1
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        for f in found:
            f["mouth"] = _mouth(gray, f, img.shape[1], dh)
            f.pop("lm", None)
        out.append({"t": t, "faces": found})
        thumbs[t] = cv2.resize(gray, (THUMB_W, th), interpolation=cv2.INTER_AREA)
    if out and W > dw and small_or_none >= len(out) / 2:
        full = detector(W, H)
        by_t = {s["t"]: s for s in out}
        for t, img in frames(path, start, end, 1.0, W, src_w, src_h):
            big = [f for f in _boxes(full, img, 1.0, 1.0, img.shape[1], img.shape[0]) if f["score"] >= MIN_SCORE]
            near = min(by_t, key=lambda x: abs(x - t)) if by_t else None
            if near is None or not big or len(big) <= len(by_t[near]["faces"]):
                continue
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            for f in big:
                f["mouth"] = _mouth(gray, f, img.shape[1], img.shape[0])
                f.pop("lm", None)
            # the full-resolution faces hold for the second around this sample (5 samples at 5 fps)
            for x in by_t:
                if abs(x - t) < 0.5 + 1e-6 and len(by_t[x]["faces"]) < len(big):
                    by_t[x]["faces"] = [dict(f) for f in big]
    return {"samples": out, "thumbs": thumbs, "size": (W, H), "detector": "YuNet (OpenCV FaceDetectorYN)", "fps": fps}


def from_analysis(faces: list[dict] | None, start: float, end: float, src_w: int, src_h: int) -> dict:
    """The analysis' face samples ({t, cx, cy, w, score} in source seconds, one face each) as clip samples: the fallback
    when YuNet can't run on this worker. Box height is estimated from the width (faces are ~1.2x taller than wide)."""
    out = []
    for f in faces or []:
        if not (start <= float(f["t"]) <= end) or float(f.get("score", 1)) < MIN_SCORE:
            continue
        w = float(f["w"])
        h = min(1.0, w * src_w / max(1, src_h) * 1.2)
        out.append({"t": round(float(f["t"]) - start, 3),
                    "faces": [{"x": float(f["cx"]) - w / 2, "y": float(f.get("cy", 0.4)) - h / 2, "w": w, "h": h,
                               "score": float(f.get("score", 1)), "mouth": None}]})
    return {"samples": out, "thumbs": {}, "size": (src_w, src_h), "detector": "analysis faces (fallback)", "fps": 2.0}


# ---------------------------------------------------------------------- tracking (pure)
def iou(a: dict, b: dict) -> float:
    ax1, ay1, bx1, by1 = a["x"] + a["w"], a["y"] + a["h"], b["x"] + b["w"], b["y"] + b["h"]
    iw, ih = max(0.0, min(ax1, bx1) - max(a["x"], b["x"])), max(0.0, min(ay1, by1) - max(a["y"], b["y"]))
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def track(samples: list[dict], min_iou: float = 0.2, max_gap: float = 1.0) -> list[dict]:
    """Greedy IoU tracking: [{id, points: [(t, face)]}]. A track survives up to max_gap seconds without a match."""
    tracks: list[dict] = []
    for s in samples:
        free = list(range(len(tracks)))
        for f in sorted(s["faces"], key=lambda f: -f["w"] * f["h"]):
            best, bi = 0.0, None
            for i in free:
                tr = tracks[i]
                lt, lf = tr["points"][-1]
                if s["t"] - lt > max_gap:
                    continue
                cx_d = abs((lf["x"] + lf["w"] / 2) - (f["x"] + f["w"] / 2))
                sc = iou(lf, f) or (0.01 if cx_d < lf["w"] * 0.6 else 0.0)
                if sc > best:
                    best, bi = sc, i
            if bi is not None and best >= min(min_iou, 0.01):
                tracks[bi]["points"].append((s["t"], f))
                free.remove(bi)
            else:
                tracks.append({"id": len(tracks), "points": [(s["t"], f)]})
    return tracks


def mouth_motion(tr: dict, spans: list[tuple[float, float]] | None = None) -> float | None:
    """Mean frame-to-frame change of a track's mouth patch, counted only inside speech spans (word timestamps): a nod or
    a laugh outside speech doesn't count as talking. None when the track has no mouth patches (fallback faces)."""
    import numpy as np
    diffs = []
    pts = [(t, f["mouth"]) for t, f in tr["points"] if f.get("mouth") is not None]
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if t1 - t0 > 0.45:
            continue
        if spans is not None and not any(s - 0.1 <= t1 <= e + 0.1 for s, e in spans):
            continue
        diffs.append(float(np.mean(np.abs(a - b))))
    if not pts:
        return None
    return float(np.mean(diffs)) if diffs else 0.0
