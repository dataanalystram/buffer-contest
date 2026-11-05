"""Face detection truly works (owner ask, 4 Nov 2026): the worker's own detectors (studio/media/facedetect.py, used by
studio.take.face_track) find the face in a committed public-domain portrait with a sane box, and none in a frame with no
face. Runs wherever OpenCV / MediaPipe are installed; the studio-ci worker-imports take_analyze job runs the same check
with ``python -m studio.media.facedetect --check`` in the worker's own venv, where a missing or broken detector FAILS.

Fixture: tests/studio/fixtures/media/face_armstrong_pd.jpg, NASA portrait of Neil Armstrong (1969), public domain
(https://commons.wikimedia.org/wiki/File:Neil_Armstrong_pose.jpg), credited in NOTICE."""
import shutil
import subprocess
import time

import pytest

from studio.media import facedetect

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")


def _gray(rgb):
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def _off(p=None):
    raise facedetect.DetectorError("forced off")


def test_fixture_is_a_small_upright_jpeg_without_metadata():
    from PIL import Image
    with Image.open(facedetect.FIXTURE) as im:
        assert im.format == "JPEG" and im.size == (480, 600)
        assert not im.info.get("exif") and not im.getexif()
    assert facedetect.FIXTURE.stat().st_size < 120_000


def test_opencv_haar_finds_the_face_with_a_sane_box_and_none_in_a_blank_frame():
    rgb = facedetect._load(facedetect.FIXTURE)
    h, w = rgb.shape[:2]
    t0 = time.perf_counter()
    faces = facedetect.detect_haar(_gray(rgb))
    ms = (time.perf_counter() - t0) * 1000
    assert len(faces) >= 1, "OpenCV's Haar cascade found no face in the portrait"
    x, y, bw, bh, _ = faces[0]
    assert facedetect.sane(faces[0], w, h)
    # the face sits in the upper middle-right of the portrait (hand-checked box ~ (245, 118, 95, 95))
    assert 0.4 < (x + bw / 2) / w < 0.75 and 0.15 < (y + bh / 2) / h < 0.4 and 0.12 < bw / w < 0.35
    assert facedetect.detect_haar(facedetect.blank_frame(w, h)[..., 0]) == []
    print(f"OpenCV {cv2.__version__} Haar: {ms:.1f} ms for one {w}x{h} frame")


def test_mediapipe_finds_the_face_when_it_can_load():
    try:
        det = facedetect.mediapipe_detector()
    except facedetect.DetectorError as e:  # no libEGL here; the worker-imports CI job runs it for real and fails on this
        pytest.skip(str(e))
    try:
        rgb = facedetect._load(facedetect.FIXTURE)
        faces = facedetect.detect_mediapipe(det, rgb)
        assert faces and facedetect.sane(faces[0], rgb.shape[1], rgb.shape[0]) and faces[0][4] >= 0.6
        assert facedetect.detect_mediapipe(det, facedetect.blank_frame(rgb.shape[1], rgb.shape[0])) == []
    finally:
        det.close()


def test_the_cascade_never_loads_empty_and_says_why(monkeypatch):
    class Empty:
        def __init__(self, *_):
            pass

        def empty(self):
            return True
    monkeypatch.setattr(facedetect, "_haar", None)
    monkeypatch.setattr(cv2, "CascadeClassifier", Empty)
    with pytest.raises(facedetect.DetectorError, match="loaded empty"):
        facedetect.haar()


def test_selfcheck_proves_the_detector_on_the_portrait(monkeypatch):
    from studio.media import selfcheck
    ok, which = selfcheck.face_detector()
    assert ok and "Haar" in which
    monkeypatch.setattr(facedetect, "detect_haar", lambda g, c=None: [])     # a cascade that loads but finds nothing
    monkeypatch.setattr(facedetect, "mediapipe_detector", _off)
    ok, which = selfcheck.face_detector()
    assert not ok and "found no face in the test portrait" in which and "forced off" in which


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
def test_the_worker_face_track_finds_the_face_in_a_video_of_the_portrait(tmp_path, monkeypatch):
    """End to end through studio.take: a 3 s video of the portrait padded to 16:9, sampled at 2 fps."""
    from studio import take
    vid = tmp_path / "face.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(facedetect.FIXTURE), "-t", "3", "-r", "10",
                    "-vf", "pad=1068:600:294:0:black,format=yuv420p", "-c:v", "libx264", str(vid)], check=True, timeout=60)
    monkeypatch.setattr(facedetect, "mediapipe_detector", _off)
    faces, how = take.face_track(str(vid), 3.0)
    assert how.startswith("OpenCV Haar cascade") and len(faces) >= 5
    cx = sorted(f["cx"] for f in faces)[len(faces) // 2]
    assert 0.45 < cx < 0.65 and all(f["score"] >= 0.7 for f in faces)   # (294 + 293) / 1068 = 0.55


def test_check_cli_reports_timings_or_fails_loudly(capsys):
    try:
        facedetect.mediapipe_detector().close()
    except facedetect.DetectorError:
        pytest.skip("MediaPipe can't load here; CI runs the full check")
    assert facedetect.main(["--check", "--runs", "3"]) == 0
    out = capsys.readouterr().out
    assert "ms_per_frame" in out and '"blank_faces": 0' in out
