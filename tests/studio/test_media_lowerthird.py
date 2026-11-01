"""Captions vs lower-thirds: when the source has a lower-third graphic (a name strap, a ticker), our word captions sit
above it in every ratio (studio.media.compose.detect_lower_third + band_in_output), never on top of it."""
import shutil
import subprocess

import pytest

from studio.media import captions, compose

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="needs FFmpeg")


def _video(path, strap: bool = True, busy: bool = False, secs: int = 6):
    src = "testsrc2=s=1280x720:r=30" if busy else "color=c=0x2a3a55:s=1280x720:r=30"
    vf = ["null"]
    if strap:  # a dark bar at 74-86% of the height with text-like strokes on it
        vf = ["drawbox=x=0:y=530:w=1280:h=90:color=0x101018:t=fill"] + \
             [f"drawbox=x={60 + i * 28}:y=545:w=12:h=60:color=white:t=fill" for i in range(30)]
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"{src}:d={secs}", "-f", "lavfi", "-i", f"sine=f=300:d={secs}",
                    "-vf", ",".join(vf), "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(path)], check=True)
    return str(path)


def test_detects_a_lower_third_and_nothing_else(tmp_path):
    b = compose.detect_lower_third(_video(tmp_path / "lt.mp4"))
    assert b and 0.72 <= b["top"] <= 0.78 and 0.82 <= b["bottom"] <= 0.88
    assert compose.detect_lower_third(_video(tmp_path / "plain.mp4", strap=False)) is None
    assert compose.detect_lower_third(_video(tmp_path / "busy.mp4", strap=False, busy=True)) is None  # texture everywhere


def test_band_position_in_every_ratio_and_reframe():
    band = {"top": 0.75, "bottom": 0.85}
    assert compose.band_in_output(band, 1280, 720, 1080, 1080) == 810           # 1:1 cover crop keeps the full height
    assert compose.band_in_output(band, 1280, 720, 1080, 1920, {"mode": "track"}) == 1440
    y = compose.band_in_output(band, 1280, 720, 1080, 1920, {"mode": "pad"})
    assert 1100 < y < 1130                                                       # the fitted frame sits mid-screen
    assert compose.band_in_output({"top": 0.9}, 1280, 720, 1080, 1080, burned_subs=True) is None  # cropped off with the subs


@pytest.mark.parametrize("ratio", ["1:1", "9:16"])
def test_captions_sit_above_the_lower_third(tmp_path, ratio):
    src = _video(tmp_path / "lt.mp4")
    words = [{"w": w, "s": 0.3 + i * 0.45, "e": 0.7 + i * 0.45} for i, w in enumerate("our captions never cover the name strap".split())]
    r = compose.render_style(src, "opus", out=str(tmp_path / f"o_{ratio[0]}.mp4"), ratio=ratio, hook="Captions above the strap",
                             words=words, clip=(0, 5), music_mood="none", burned_subs=False)
    lt = r["lower_third"]
    assert r["captions"] and lt and lt.get("out_top")
    W, H = compose.SIZES[ratio]
    st = captions.STYLES["opus"]
    half = H * st.size_frac * 1.25 * st.max_lines / 2
    assert r["captions_center"] + half <= lt["out_top"]                         # the caption block ends above the band
    assert not r["caption_problems"]
    plain = compose.render_style(_video(tmp_path / "p.mp4", strap=False), "opus", out=str(tmp_path / "p.mp4.out.mp4"), ratio=ratio,
                                 hook="No strap", words=words, clip=(0, 5), music_mood="none", burned_subs=False)
    assert plain["lower_third"] is None and plain["captions_center"] >= r["captions_center"]
    if ratio == "1:1":  # the square crop keeps the strap where the default caption line would be: lifted
        assert plain["captions_center"] > r["captions_center"]
