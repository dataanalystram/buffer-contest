"""Live pass 2 Nov 2026 fixes for the video agent:
- a screencast Reel was 14.4 s of picture under 38 s of audio (a 30 s cut asked of a shorter clip): the cut stays inside
  the source and the audio never outlasts the picture;
- edit_video rights "own" sent an empty credit and the worker refused it ("A credit line is required");
- a 404 from a source link on the app's own host was reported as "couldn't talk to HuhQueue";
- replies named "the GitHub video worker" when InstaBox rendered."""
import shutil
import subprocess

import httpx
import pytest

from studio import render_worker
from studio.house import House
from studio.media import compose, ff


def _src(tmp_path, secs=4):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=30:duration={secs}",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    return str(src)


def _lens(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "csv=p=0", path],
                       capture_output=True, text=True, check=True)
    out = {}
    for line in r.stdout.split():
        kind, d = line.split(",")[:2]
        out[kind] = float(d)
    return out


def test_clamp_clip_stays_inside_the_source():
    assert compose.clamp_clip((0, 30), 9.0) == (0.0, 9.0)
    assert compose.clamp_clip((3, 603), 15.0) == (3.0, 15.0)  # edit_video cut_first_s 3 sends [3, 603]
    assert compose.clamp_clip((2, 5), 9.0) == (2.0, 5.0)
    assert compose.clamp_clip((20, 30), 9.0) == (8.5, 9.0)  # a start past the end still leaves a frame
    assert compose.clamp_clip((0, 30), 0.0) == (0.0, 30.0)  # unknown length: as asked


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
def test_plain_render_with_a_long_cut_keeps_audio_and_video_together(tmp_path):
    src = _src(tmp_path, 4)
    m = compose.render_plain(src, out=str(tmp_path / "o.mp4"), ratio="9:16", clip=(0, 30), logo=False, end=False)
    ln = _lens(m["path"])
    assert ln["video"] < 6, ln  # 4 s of source, not 30
    assert abs(ln["audio"] - ln["video"]) < 0.35, ln


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
def test_mux_audio_never_outlasts_the_picture(tmp_path):
    src = _src(tmp_path, 3)
    vid = tmp_path / "v.mp4"  # 3 s of picture, no audio
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-an", "-c:v", "copy", str(vid)], check=True)
    out = tmp_path / "o.mp4"
    compose._mux_audio(str(vid), src, 0.0, 30.0, 0.0, 38.0, None, str(out))  # planned 38 s like the screencast Reel
    ln = _lens(str(out))
    assert ln["audio"] <= ln["video"] + 0.35, ln
    assert ff.probe(str(out))["duration"] < 4


def test_media_render_own_video_needs_no_credit(monkeypatch, tmp_path):
    from studio.media import compose as comp
    from studio.media import tools
    seen = {}

    def fake(source, style, **kw):
        seen.update(kw, style=style)
        return {"path": kw["out"], "ratio": "9:16", "width": 1080, "height": 1920, "duration": 9.0, "clip": None,
                "captions": False, "music": "none", "caption_problems": []}
    monkeypatch.setattr(comp, "render_style", fake)
    r = tools.media_render("minimal", "", source_path=str(tmp_path / "x.mp4"), credit="", owns=True, run_dir=str(tmp_path))
    assert "error" not in r and "needs_confirmation" not in r and seen["style"] == "minimal"
    r = tools.media_render("minimal", "", source_path=str(tmp_path / "x.mp4"), credit="", rights_confirmed=True,
                           run_dir=str(tmp_path))
    assert "credit line is required" in r["error"]  # someone else's video still needs its credit


def test_edit_video_spec_marks_own_video():
    from studio import mcp_client_brain as m
    assert m._rights({"rights": "own"})["owns"] is True
    assert m._rights({"rights": "news_credit", "credit": "Video: X"})["owns"] is False


def _err(status, url):
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))


def test_friendly_names_a_source_link_on_our_own_host():
    base = "https://hq.test"
    assert render_worker.friendly(_err(404, "https://hq.test/m/abc.mp4"), base) == \
        "Couldn't download the video: the source link returned 404"
    assert render_worker.friendly(_err(404, "https://hq.test/status/404"), base) == \
        "Couldn't download the video: the source link returned 404"
    assert "couldn't talk to HuhQueue (HTTP 500)" in render_worker.friendly(_err(500, "https://hq.test/api/worker/jobs/3"), base)
    assert render_worker.friendly(_err(404, "https://example.com/v.mp4"), base) == \
        "Couldn't download the video: the link returned 404"


def test_backend_label_names_the_real_worker():
    assert House.backend_label("InstaBox exec 200") == "the InstaBox video worker"
    assert House.backend_label("GitHub answered 204") == "the GitHub Actions video worker"
    assert House.backend_label("InstaBox exec 500 exit 1; fallback: GitHub answered 204") == \
        "the GitHub Actions video worker (InstaBox fallback)"
