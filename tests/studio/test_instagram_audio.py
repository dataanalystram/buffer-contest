"""Instagram's 128 kbps audio spec (owner, 6 Nov 2026: 'audio is 152 kbps' stopped drafts and schedules from Make a post and
One take). Every encoder writes ff.AAC_KBPS; a video that is already over (an import, an older render) gets only its sound
re-encoded on the video worker (picture copied) before the draft or schedule, and autofix_post can do it too."""
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import preflight as pf
from studio.media import ff

from .conftest import agent_token, mcp_call

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs FFmpeg")


def _video(path: Path, audio_k: int = 192, secs: float = 4.0) -> bytes:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=270x480:r=30:d={secs}",
                    "-f", "lavfi", "-i", f"anoisesrc=d={secs}:a=0.3", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", f"{audio_k}k", "-ar", "44100", "-ac", "2", str(path)], check=True)
    return path.read_bytes()


def test_every_finished_video_encoder_uses_the_shared_bitrate_under_instagrams_spec():
    assert ff.AAC_KBPS == pf.AUDIO_FIX_KBPS <= 124
    srcs = {p: (ROOT / p).read_text() for p in ("studio/media/compose.py", "studio/media/reframe/render.py", "studio/media/recap.py",
                                                  "studio/media/worker.py", "studio/take.py", "studio/render.py")}
    for p, s in srcs.items():
        assert '"160k"' not in s and '"128k"' not in s and '"192k"' not in s, p


def test_the_shared_setting_measures_at_or_under_128_kbps(tmp_path):
    out = tmp_path / "a.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anoisesrc=d=20:a=0.5", *ff.AENC, str(out)], check=True)
    kbps = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=bit_rate", "-of",
                               "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()) / 1000
    assert kbps <= 128


def test_fix_audio_copies_the_picture_and_brings_the_sound_under_spec(tmp_path):
    from studio.media.worker import fix_audio
    src = _video(tmp_path / "in.mp4", 192)
    before = pf.ffprobe_path(str(tmp_path / "in.mp4"))
    assert before["audio_kbps"] > 128
    data, meta = fix_audio(src)
    (tmp_path / "out.mp4").write_bytes(data)
    after = pf.ffprobe_path(str(tmp_path / "out.mp4"))
    assert after["audio_kbps"] <= 128 and meta["audio_fixed"] and meta["probe"]["kind"] == "video"
    assert (after["codec"], after["width"], after["height"]) == (before["codec"], before["width"], before["height"])
    assert after["video_kbps"] == before["video_kbps"]  # stream copy: the picture is byte-for-byte the same stream


def test_preflight_offers_the_audio_fix_instead_of_edit_media():
    fx = {}
    out = pf._video_rules("instagram", "Instagram", "Video", {"kind": "video", "probed": True, "codec": "h264", "width": 1080,
                                                              "height": 1920, "fps": 30.0, "duration": 30.0, "audio_kbps": 152}, fx, 0)
    assert [f["fix"] for f in out] == ["audio:0"] and "reencode" not in fx and "152 kbps" in out[0]["message"]
    assert pf.audio_fix_ids([{"findings": out}]) == [0]


def test_a_draft_with_loud_audio_is_fixed_before_it_reaches_buffer(human, fake, tmp_path):
    url = "https://cdn.example.com/cyclon.mp4"
    fake.media[url] = _video(tmp_path / "c.mp4", 192)
    post = {"channel_id": "ch_ig", "text": "Cyclon launch", "mode": "draft", "media_url": url}
    r = human.post("/api/posts/preview", json=post)
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["preview"]["media_url"].startswith("https://hq.test/m/") and pv["preflight"]["status"] != "warning"
    assert any("Re-encoded the sound" in w for w in pv["warnings"])
    assert not any(f.get("fix", "").startswith("audio:") for f in pv["preflight"]["findings"])
    s = human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    assert s.status_code == 200, s.text
    assert len(fake.created) == 1


def test_autofix_audio_matches_on_rest_and_mcp(human, fake, tmp_path):
    url = "https://cdn.example.com/loud.mp4"
    fake.media[url] = _video(tmp_path / "l.mp4", 192)
    tok = agent_token(human, "trusted")
    body = {"channel_id": "ch_ig", "text": "Launch", "media_url": url, "fixes": ["audio"]}
    rest = human.post("/api/preflight/fix", json=body).json()
    assert rest["applied"] == ["audio:0"] and rest["media_url"].startswith("https://hq.test/m/"), rest
    assert not any(f.get("fix", "").startswith("audio:") for f in rest["preflight"]["findings"])
    _, mcp = mcp_call(human, tok, "autofix_post", body)
    assert mcp["applied"] == ["audio:0"] and mcp["preflight"]["status"] == rest["preflight"]["status"]


def test_the_hosted_app_never_runs_the_fix_in_the_web_process(app, monkeypatch):
    house = app.state.studio.house
    monkeypatch.setenv("RENDER", "1")
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    house.worker_mode = ""
    from studio.actions import Actor
    got, why = house.fix_audio(Actor("u1"), "https://hq.test/m/nothing", b"x")
    assert got is None and why == "no video worker here"
