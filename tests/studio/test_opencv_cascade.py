"""Cover faces need cv2.CascadeClassifier (studio/media/covers.faces()). OpenCV 5 moved it to contrib, so with
opencv-python-headless 5 alone faces() silently returned [] in the media_render worker (4 Nov 2026)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_every_requirements_file_caps_opencv_headless_below_5():
    listed = 0
    for req in sorted(ROOT.glob("requirements*.txt")):
        for line in req.read_text(encoding="utf-8").splitlines():
            spec = line.split("#", 1)[0].replace(" ", "")
            if re.match(r"opencv-python-headless(?![\w-])", spec):
                listed += 1
                assert "<5" in spec, f"{req.name}: {spec!r} must cap opencv-python-headless below 5"
    assert listed, "opencv-python-headless is listed in no requirements file"


def test_cascade_classifier_loads_bundled_haarcascade():
    cv2 = pytest.importorskip("cv2")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from worker_imports import cv2_cascade_problem
    finally:
        sys.path.pop(0)
    assert hasattr(cv2, "CascadeClassifier")
    assert cv2_cascade_problem() is None
