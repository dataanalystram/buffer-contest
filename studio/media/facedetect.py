"""The video worker's face detectors, one place (owner ask, 4 Nov 2026: "OpenCV and face detection must truly work, no
silent failures").

* MediaPipe Face Detector (BlazeFace short range, Apache-2.0) is the primary detector on every sampled frame.
* OpenCV's Haar frontal-face cascade (opencv-python-headless, Apache-2.0) is the fallback when MediaPipe can't load.

``studio.take.face_track`` and the worker self-check (studio/media/selfcheck.py) call these functions, and so does the CI
check (``python -m studio.media.facedetect --check``, run in the studio-ci worker-imports take_analyze job and by
tests/studio/test_face_detect.py): both detectors must find the face in the committed public-domain portrait
(tests/studio/fixtures/media/face_armstrong_pd.jpg, credited in NOTICE) with a sane box, and nothing in a frame without
a face. An OpenCV or MediaPipe upgrade that breaks detection fails CI, never a take.

Boxes are (x, y, w, h, score) in pixels of the frame given.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

FACE_MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite"
HAAR_SCORE = 0.8          # the cascade gives no score; its hits count as a confident sample (crop_plan keeps >= 0.7)
MP_MIN_CONF = 0.6
FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "studio" / "fixtures" / "media" / "face_armstrong_pd.jpg"

_haar = None


class DetectorError(RuntimeError):
    pass


def haar():
    """OpenCV's frontal-face cascade, loaded once. Raises DetectorError (never returns an empty classifier)."""
    global _haar
    if _haar is not None:
        return _haar
    try:
        import cv2  # opencv-python-headless (Apache-2.0)
        casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    except Exception as e:  # noqa: BLE001 - AttributeError when OpenCV 5 drops CascadeClassifier
        raise DetectorError(f"OpenCV CascadeClassifier unavailable ({e.__class__.__name__}: {str(e)[:80]})") from e
    if casc.empty():
        raise DetectorError("OpenCV's Haar cascade file loaded empty")
    _haar = casc
    return casc


def detect_haar(gray, casc=None) -> list[tuple[int, int, int, int, float]]:
    """Faces in one grayscale frame (numpy HxW uint8), largest first."""
    casc = casc or haar()
    h, w = gray.shape[:2]
    found = casc.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(max(18, w // 40), max(18, h // 40)))
    out = [(int(x), int(y), int(fw), int(fh), HAAR_SCORE) for x, y, fw, fh in (found if len(found) else [])]
    return sorted(out, key=lambda b: -b[2] * b[3])


def model_path(path: str | None = None) -> str:
    """The BlazeFace model on disk (HQ_FACE_MODEL, else the temp dir), downloaded once from Google's model bucket."""
    p = Path(path or os.environ.get("HQ_FACE_MODEL") or str(Path(tempfile.gettempdir()) / "blaze_face_short_range.tflite"))
    if not p.exists() or p.stat().st_size < 1000:
        import httpx  # a fixed first-party URL (no user input), so no netguard
        r = httpx.get(FACE_MODEL_URL, timeout=60, follow_redirects=True)
        r.raise_for_status()
        if len(r.content) < 1000:
            raise DetectorError("The MediaPipe face model download was empty")
        p.write_bytes(r.content)
    return str(p)


def mediapipe_detector(path: str | None = None):
    """A MediaPipe FaceDetector (IMAGE mode). Raises DetectorError naming why it can't load (no package, no libEGL)."""
    try:
        from mediapipe.tasks.python import BaseOptions, vision  # Apache-2.0
    except Exception as e:  # noqa: BLE001
        raise DetectorError(f"MediaPipe not installed ({e.__class__.__name__})") from e
    try:
        return vision.FaceDetector.create_from_options(vision.FaceDetectorOptions(
            base_options=BaseOptions(model_asset_path=model_path(path)), min_detection_confidence=MP_MIN_CONF))
    except DetectorError:
        raise
    except Exception as e:  # noqa: BLE001 - OSError without libEGL/libGLES, RuntimeError on a bad model
        raise DetectorError(f"MediaPipe couldn't load: {str(e)[:80]}") from e


def detect_mediapipe(det, rgb) -> list[tuple[int, int, int, int, float]]:
    """Faces in one RGB frame (numpy HxWx3 uint8), largest first."""
    try:
        import mediapipe as mp  # only reached with a detector from mediapipe_detector()
        import numpy as np
    except ImportError as e:
        raise DetectorError(f"MediaPipe not installed ({e.__class__.__name__})") from e
    res = det.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
    out = []
    for d in res.detections or []:
        bb = d.bounding_box
        out.append((int(bb.origin_x), int(bb.origin_y), int(bb.width), int(bb.height), round(float(d.categories[0].score), 3)))
    return sorted(out, key=lambda b: -b[2] * b[3])


def sane(box, w: int, h: int) -> bool:
    """A box that could be a face: inside the frame (a few px slack) and 3-90% of the frame's width."""
    x, y, bw, bh = box[:4]
    return (bw > 0 and bh > 0 and x >= -4 and y >= -4 and x + bw <= w + 4 and y + bh <= h + 4
            and 0.03 <= bw / w <= 0.9 and 0.5 <= bw / bh <= 2.0)


# ---------------------------------------------------------------------- the CI check
def _cv2():
    try:
        import cv2
    except ImportError as e:
        raise DetectorError(f"OpenCV not installed ({e.__class__.__name__})") from e
    return cv2


def _load(path: Path):
    import numpy as np
    from PIL import Image, ImageOps
    with Image.open(path) as im:
        rgb = np.array(ImageOps.exif_transpose(im).convert("RGB"))
    return rgb


def blank_frame(w: int = 480, h: int = 600):
    """A frame with no face: a soft vertical ramp with a little noise (a wall, a slide background)."""
    import numpy as np
    rng = np.random.default_rng(7)
    ramp = np.linspace(40, 200, h, dtype=np.float32)[:, None, None].repeat(w, 1).repeat(3, 2)
    return np.clip(ramp + rng.normal(0, 6, ramp.shape), 0, 255).astype("uint8")


def check(image: str | Path | None = None, runs: int = 5) -> dict:
    """Both detectors on the face fixture and on a blank frame. Raises DetectorError on any miss; returns the boxes and
    the median detection time per frame (ms) for each detector."""
    import numpy as np
    rgb = _load(Path(image or FIXTURE))
    h, w = rgb.shape[:2]
    gray = (rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114).astype(np.uint8)
    blank = blank_frame(w, h)
    blank_gray = blank[..., 0]
    out: dict = {"image": str(image or FIXTURE.name), "size": [w, h]}
    problems = []

    def timed(fn, *a):
        ts, got = [], None
        for _ in range(max(1, runs)):
            t0 = time.perf_counter()
            got = fn(*a)
            ts.append((time.perf_counter() - t0) * 1000)
        return got, round(sorted(ts)[len(ts) // 2], 2)
    try:
        casc = haar()
        cv2 = _cv2()
        faces, ms = timed(detect_haar, gray, casc)
        none, _ = timed(detect_haar, blank_gray, casc)
        out["opencv"] = {"version": cv2.__version__, "faces": len(faces), "box": list(faces[0][:4]) if faces else None,
                         "ms_per_frame": ms, "blank_faces": len(none)}
        if not faces or not sane(faces[0], w, h):
            problems.append(f"OpenCV Haar cascade found no sane face in the fixture ({faces[:2]})")
        if none:
            problems.append(f"OpenCV Haar cascade found {len(none)} face(s) in a blank frame")
    except DetectorError as e:
        problems.append(str(e))
    try:
        det = mediapipe_detector()
        try:
            try:
                import mediapipe
            except ImportError as e:
                raise DetectorError(f"MediaPipe not installed ({e.__class__.__name__})") from e
            faces, ms = timed(detect_mediapipe, det, rgb)
            none, _ = timed(detect_mediapipe, det, blank)
            out["mediapipe"] = {"version": getattr(mediapipe, "__version__", "?"), "faces": len(faces),
                                "box": list(faces[0][:4]) if faces else None, "score": faces[0][4] if faces else None,
                                "ms_per_frame": ms, "blank_faces": len(none)}
            if not faces or not sane(faces[0], w, h):
                problems.append(f"MediaPipe found no sane face in the fixture ({faces[:2]})")
            if none:
                problems.append(f"MediaPipe found {len(none)} face(s) in a blank frame")
        finally:
            det.close()
    except DetectorError as e:
        problems.append(str(e))
    # both detectors must agree on where the face is (centres within a fifth of the frame)
    a, b = (out.get("opencv") or {}).get("box"), (out.get("mediapipe") or {}).get("box")
    if a and b:
        dx = abs((a[0] + a[2] / 2) - (b[0] + b[2] / 2)) / w
        dy = abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) / h
        out["agree"] = round(max(dx, dy), 3)
        if max(dx, dy) > 0.2:
            problems.append(f"OpenCV and MediaPipe disagree on the face ({a} vs {b})")
    if problems:
        raise DetectorError("; ".join(problems))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Face detection check: both detectors find the fixture's face, none in a blank frame.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--image", default=None)
    ap.add_argument("--runs", type=int, default=5)
    a = ap.parse_args(argv)
    try:
        print("face detection ok:", json.dumps(check(a.image, a.runs)))
        return 0
    except DetectorError as e:
        print(f"face detection FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
