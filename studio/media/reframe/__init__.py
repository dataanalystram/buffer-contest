"""Per-scene reframe for One take clips: four layouts chosen shot by shot, then rendered to 1080x1920.

    shots.py   shot cuts inside a clip span (the analysis' scene cuts, PySceneDetect when there are none)
    faces.py   YuNet face detector (OpenCV FaceDetectorYN, vendored ONNX, sha256-checked), sampled on the clip span only,
               IoU tracking into tracks, mouth-motion per track; the analysis' MediaPipe faces are the fallback
    screen.py  screen / slide / screen-recording signals (edges, motion, flat histogram) and the content width fraction
    decide.py  the layout rules (track, fit, split, screen) per shot, with the stability rules (hold, vote, change cap)
    path.py    the tracked crop path (dead zone, EMA, pan cap, face at 40% height) as ffmpeg sendcmd lines
    render.py  ffmpeg filter graphs for the four layouts, captions and logo in one pass

The decision output is a layout sidecar, ``[{start, end, layout, ...}]`` in clip time, that captions use to find a
free band (the seam of a split, under the face of a tracked crop) and that the API shows as the clip's layout plan.

Pure parts (decide, path, captions placement) need no OpenCV and are tested with recorded signals; faces/screen need
``cv2`` and ``numpy`` and degrade to the analysis' face samples when they're missing.
"""
from __future__ import annotations

import os

LAYOUTS = ("track", "fit", "split", "screen")


def enabled() -> bool:
    """Per-scene layouts render One take clips unless HQ_LAYOUTS=0 (then the older single-crop path renders)."""
    return os.environ.get("HQ_LAYOUTS", "1").strip().lower() not in ("0", "false", "no", "off")


def split_enabled() -> bool:
    """SPLIT is opt-in (HQ_LAYOUT_SPLIT=1): a wrong split shows half the frame of someone who never speaks."""
    return os.environ.get("HQ_LAYOUT_SPLIT", "0").strip().lower() in ("1", "true", "yes", "on")
