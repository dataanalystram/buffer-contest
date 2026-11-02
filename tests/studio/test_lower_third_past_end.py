"""edit_video with cut_first_s sends clip [cut, cut + 600]; detect_lower_third must not sample past the end (live 2 Nov:
the job failed with FileNotFoundError .../l0.png)."""
import shutil
import subprocess

import pytest


def test_detect_lower_third_end_past_duration(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    from studio.media import compose
    src = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-t", "4", str(src)],
                   check=True)
    compose.detect_lower_third(str(src), 3.0, 603.0)  # used to raise FileNotFoundError
