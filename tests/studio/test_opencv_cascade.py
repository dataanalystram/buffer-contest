"""Cover faces need cv2.CascadeClassifier (studio/media/covers.faces()). OpenCV 5 moved it to contrib, so with
opencv-python-headless 5 alone faces() silently returned [] in the media_render worker (4 Nov 2026)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


OPENCV_DISTS = ("opencv-python-headless", "opencv-python", "opencv-contrib-python")


def test_every_requirements_file_caps_opencv_below_5():
    from packaging.requirements import Requirement

    listed = 0
    for req in sorted(ROOT.glob("requirements*.txt")):
        for line in req.read_text(encoding="utf-8").splitlines():
            spec = line.split("#", 1)[0].strip()
            if not spec or spec.startswith("-"):
                continue
            r = Requirement(spec)
            if r.name.lower() not in OPENCV_DISTS:
                continue
            listed += 1
            # every 5.x must be excluded, not just 5.0 (a "<5.1" bound would still allow 5.0.x)
            for v in ("5.0", "5.0.0.93", "5.1.0", "5.99"):
                assert not r.specifier.contains(v, prereleases=True), f"{req.name}: {r} allows OpenCV {v}"
            assert r.specifier.contains("4.14.0.94"), f"{req.name}: {r} should allow OpenCV 4.x"
    assert listed, "no requirements file lists an OpenCV distribution"


def test_cascade_classifier_loads_bundled_haarcascade():
    cv2 = pytest.importorskip("cv2")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from worker_imports import cv2_cascade_problem
    finally:
        sys.path.pop(0)
    assert hasattr(cv2, "CascadeClassifier")
    assert cv2_cascade_problem() is None
