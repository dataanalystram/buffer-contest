"""B-roll: Pexels search is allowlisted and keyed, the draft checks cutaways, the director adds/removes one, the render
lays it over the picture."""
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from studio.media import broll


def _transport(payload):
    def h(req):
        assert req.headers["authorization"] == "k"
        return httpx.Response(200, json=payload)
    return httpx.MockTransport(h)


VID = {"videos": [{"url": "https://www.pexels.com/video/1/", "image": "https://images.pexels.com/x.jpg", "duration": 9,
                   "user": {"name": "Ann"}, "video_files": [
                       {"file_type": "video/mp4", "width": 540, "height": 960, "link": "https://videos.pexels.com/a_540.mp4"},
                       {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://videos.pexels.com/a_1080.mp4"},
                       {"file_type": "video/mp4", "width": 720, "height": 1280, "link": "https://evil.example/a_720.mp4"}]}]}


def test_search_needs_a_key_and_picks_small_portrait_mp4(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    with pytest.raises(broll.BrollError, match="Pexels key"):
        broll.search("kitchen")
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    monkeypatch.setattr(broll.netguard, "safe_get", lambda url, **kw: httpx.Response(200, json=VID, request=httpx.Request("GET", url)))
    got = broll.search("busy kitchen")
    assert got[0]["url"].endswith("a_1080.mp4") and "Ann" in got[0]["credit"]   # evil host dropped, 540 too small


def test_clean_checks_window_and_host():
    ok = {"s": 2, "e": 5, "url": "https://videos.pexels.com/a.mp4", "query": "kitchen"}
    assert broll.clean([ok], 20)[0]["e"] == 5
    for bad in ({**ok, "s": 0.2}, {**ok, "e": 12}, {**ok, "e": 2.5}, {**ok, "url": "https://evil.example/a.mp4"},
                {**ok, "url": "http://videos.pexels.com/a.mp4"}):
        with pytest.raises(ValueError):
            broll.clean([bad], 20)
    with pytest.raises(ValueError):
        broll.clean([ok, {**ok, "s": 4, "e": 7}], 20)


def test_fetch_refuses_other_hosts(tmp_path):
    with pytest.raises(broll.BrollError):
        broll.fetch("https://evil.example/a.mp4", tmp_path / "x.mp4")


def test_slots_middle_and_clear():
    assert broll.slots([], 0, 30, []) == (13.5, 16.5)
    assert broll.slots([], 0, 3, []) is None


@pytest.mark.skipif(subprocess.run(["which", "ffmpeg"], capture_output=True).returncode != 0, reason="no ffmpeg")
def test_render_body_lays_the_cutaway_over_the_picture(tmp_path):
    from studio.media.reframe import render as rr
    src, cut, out = tmp_path / "s.mp4", tmp_path / "b.mp4", tmp_path / "o.mp4"
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)
    run("-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=30:d=6", "-f", "lavfi", "-i", "sine=f=300:d=6", "-shortest", str(src))
    run("-f", "lavfi", "-i", "color=c=red:s=540x960:r=30:d=1", str(cut))
    shots = [{"start": 0.0, "end": 6.0, "layout": "fit"}]
    cmd = rr.render_body_cmd(str(src), 0.0, 6.0, shots, (1280, 720), None, None, str(out), tmp_path, "#fff", "#000", "anull",
                             broll=[{"path": str(cut), "s": 2.0, "e": 4.0}])
    rr._ff(cmd)
    def px(t):
        r = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-vf", "scale=1:1,format=rgb24",
                            "-f", "rawvideo", "-"], capture_output=True, check=True)
        return tuple(r.stdout[:3])
    assert px(1.0)[2] > px(1.0)[0]        # blue-ish body
    assert px(3.0)[0] > 150 and px(3.0)[2] < 100   # red cutaway
    assert px(5.0)[2] > px(5.0)[0]
