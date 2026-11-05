"""Worker self-checks: nothing fails silently (owner ask, 4 Nov 2026; OpenCV 5 dropped CascadeClassifier and face
detection quietly returned nothing, so every clip fell back to a centred crop with no error anywhere).

Before a One take job does any work, the worker asserts what that job needs and stops with one clear error if it's
missing, instead of degrading:

* take_analyze: a face detector loads (OpenCV's Haar CascadeClassifier, non-empty, or MediaPipe's Face Detector API) and
  faster-whisper imports (``--load-model`` also loads a model; CI keeps it import-only);
* every One take job: FFmpeg and ffprobe are installed (take.probe reads every source with ffprobe);
* take_render (every kind but take_analyze): FFmpeg has the filters and encoders the render uses (loudnorm, ass /
  subtitles, afade, libx264, aac). Analysis renders nothing, so an analysis-only worker isn't held to them.

    python -m studio.media.selfcheck --kind take_analyze    # exit 1 with the reasons when something is missing
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

FILTERS = ("loudnorm", "ass", "subtitles", "afade", "crop", "boxblur")
ENCODERS = ("libx264", "aac")


class CapabilityError(RuntimeError):
    pass


def _ff(args: list[str]) -> str:
    try:
        return subprocess.run(["ffmpeg", "-hide_banner", *args], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def face_detector() -> tuple[bool, str]:
    """(ok, which): a detector that loads here AND finds the face in the committed public-domain portrait (a loaded but
    broken detector would otherwise reframe every take as "no face" without a word). studio/media/facedetect.py."""
    from . import facedetect
    why = []
    try:
        import cv2  # opencv-python-headless (Apache-2.0)
        casc = facedetect.haar()
        if facedetect.FIXTURE.exists():
            rgb = facedetect._load(facedetect.FIXTURE)
            g = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            found = facedetect.detect_haar(g, casc)
            if not found or not facedetect.sane(found[0], g.shape[1], g.shape[0]):
                raise facedetect.DetectorError("OpenCV's Haar cascade found no face in the test portrait")
        return True, f"OpenCV {cv2.__version__} Haar cascade"
    except Exception as e:  # noqa: BLE001 - AttributeError when OpenCV 5 drops CascadeClassifier, DetectorError
        why.append(str(e)[:120] if isinstance(e, facedetect.DetectorError) else
                   f"OpenCV CascadeClassifier unavailable ({e.__class__.__name__}: {str(e)[:80]})")
    try:  # MediaPipe really loads (libEGL, the model) and finds the same face, or it doesn't count
        det = facedetect.mediapipe_detector()
        try:
            if facedetect.FIXTURE.exists():
                rgb = facedetect._load(facedetect.FIXTURE)
                found = facedetect.detect_mediapipe(det, rgb)
                if not found or not facedetect.sane(found[0], rgb.shape[1], rgb.shape[0]):
                    raise facedetect.DetectorError("MediaPipe found no face in the test portrait")
        finally:
            det.close()
        return True, "MediaPipe Face Detector"
    except Exception as e:  # noqa: BLE001
        why.append(str(e)[:120] if isinstance(e, facedetect.DetectorError) else f"MediaPipe unavailable ({e.__class__.__name__})")
    return False, "; ".join(why)


def check(kind: str = "take_render", load_model: bool = False) -> dict:
    """What this worker can do for ``kind``; raises CapabilityError naming everything missing."""
    missing, have = [], {}
    if not shutil.which("ffprobe"):
        missing.append("ffprobe isn't installed")
    if not shutil.which("ffmpeg"):
        missing.append("FFmpeg isn't installed")
    elif kind != "take_analyze":
        flt, enc = _ff(["-filters"]), _ff(["-encoders"])
        names = {ln.split()[1] for ln in flt.splitlines() if len(ln.split()) > 2 and len(ln.split()[0]) <= 4}
        encs = {ln.split()[1] for ln in enc.splitlines() if len(ln.split()) > 1 and len(ln.split()[0]) == 6}
        for f in FILTERS:
            (have.setdefault("filters", []).append(f) if f in names else missing.append(f"FFmpeg filter '{f}' is missing"))
        for e in ENCODERS:
            (have.setdefault("encoders", []).append(e) if e in encs else missing.append(f"FFmpeg encoder '{e}' is missing"))
    if kind == "take_analyze":  # faces are found at analysis; take_render reframes from them (its slim venv has no cv2)
        ok, which = face_detector()
        have["face_detector"] = which if ok else None
        if not ok:
            missing.append(f"No face detector loads ({which})")
    if kind == "take_render":  # per-shot layouts find faces on each clip span with YuNet (studio/media/reframe)
        from .reframe import enabled as layouts_on
        if layouts_on():
            try:
                from .reframe.faces import detector
                detector(64, 64)
                have["layout_faces"] = "YuNet (OpenCV FaceDetectorYN)"
            except Exception as e:  # noqa: BLE001 - FaceModelError, a broken cv2
                missing.append(f"The layout face detector doesn't load ({str(e)[:100]})")
    if kind == "take_analyze":
        try:
            import faster_whisper  # noqa: F401  MIT
            have["stt"] = "faster-whisper"
            if load_model:
                from faster_whisper import WhisperModel
                WhisperModel("tiny", device="cpu", compute_type="int8")
        except Exception as e:  # noqa: BLE001
            missing.append(f"faster-whisper doesn't load ({e.__class__.__name__}: {str(e)[:80]})")
    if missing:
        raise CapabilityError("The video worker is missing what this job needs: " + "; ".join(missing) + ".")
    return have


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="take_render")
    ap.add_argument("--load-model", action="store_true")
    a = ap.parse_args(argv)
    try:
        print("self-check ok:", check(a.kind, a.load_model))
        return 0
    except CapabilityError as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
