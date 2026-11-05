"""One take: pause & filler trim + two-pass loudness (studio/media/cleancut.py)."""
import base64
import re
import shutil
import subprocess

import pytest

from studio import take, takes

from .conftest import agent_token, mcp_call
from studio.media import cleancut

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


def _inside_one_keep(word, keep):
    return any(a - 1e-6 <= word["s"] and word["e"] <= b + 1e-6 for a, b in keep)


# ---------------------------------------------------------------- the plan (pure)
def test_fillers_and_long_pauses_go_and_no_kept_word_is_ever_cut():
    words = [W("So", 0.0, 0.3), W("um", 0.5, 0.8), W("today", 1.0, 1.4), W("we", 1.45, 1.6), W("ship.", 1.65, 2.0),
             W("Uh,", 3.5, 3.8), W("it", 4.0, 4.2), W("works.", 4.25, 4.7)]
    plan = cleancut.plan_cuts(words, 0.0, 5.0)
    assert plan["fillers"] == 2 and plan["pauses"] >= 1 and plan["removed"] > 1.5
    kept = [w for w in words if take.norm(w["w"]) not in ("um", "uh")]
    for w in kept:
        assert _inside_one_keep(w, plan["keep"]), w                       # never cut inside a word
    for w in words:
        if take.norm(w["w"]) in ("um", "uh"):
            assert not any(a < w["e"] and w["s"] < b for a, b in plan["keep"]), w  # the filler is gone entirely
    # PAD (80 ms) stays next to speech wherever the gap allows it
    for a, b in plan["keep"]:
        inner = [w for w in kept if a <= w["s"] and w["e"] <= b]
        if inner and a > 0:
            assert inner[0]["s"] - a >= min(cleancut.PAD, inner[0]["s"]) - 1e-6
        if inner and b < 5.0:
            assert b - inner[-1]["e"] >= cleancut.PAD - 1e-6


def test_like_is_only_a_filler_between_pauses_or_commas():
    real = [W("It's", 0.0, 0.2), W("like", 0.25, 0.45), W("a", 0.5, 0.55), W("dream.", 0.6, 1.0)]
    assert cleancut.plan_cuts(real, 0.0, 1.0)["fillers"] == 0
    fill = [W("I", 0.0, 0.1), W("was,", 0.15, 0.4), W("like,", 0.6, 0.85), W("totally", 1.05, 1.5), W("lost.", 1.55, 1.9)]
    plan = cleancut.plan_cuts(fill, 0.0, 1.9)
    assert plan["fillers"] == 1
    assert all(not (a < 0.85 and 0.6 < b) for a, b in plan["keep"])


def test_pause_cuts_need_the_silence_detector_to_agree():
    words = [W("One", 0.0, 0.4), W("two", 2.0, 2.4), W("three", 2.45, 2.9), W("four.", 2.95, 4.0)]
    assert cleancut.plan_cuts(words, 0.0, 4.0, silences=None)["removed"] > 1.0      # no detector output: the gap decides
    assert cleancut.plan_cuts(words, 0.0, 4.0, silences=[])["skipped"] == "nothing to trim"  # detector heard sound there
    part = cleancut.plan_cuts(words, 0.0, 4.0, silences=[[1.0, 1.6]])
    assert 0.5 < part["removed"] < 0.7                                              # only the agreed stretch


def test_a_plan_cutting_half_the_span_is_dropped_and_junk_is_ignored():
    words = [W("Hi.", 0.0, 0.3), W("Bye.", 9.5, 9.8)]
    plan = cleancut.plan_cuts(words, 0.0, 10.0)
    assert plan["keep"] == [[0.0, 10.0]] and plan["skipped"].startswith("would remove")
    junk = [{"w": "x"}, {"w": "y", "s": "nan", "e": 1}, {"w": "z", "s": 2, "e": 1}, None and {}]
    assert cleancut.plan_cuts([j for j in junk if j], 0.0, 5.0)["skipped"] == "no words"


def test_remap_keeps_captions_faces_and_scenes_in_sync():
    keep = [[1.0, 2.0], [3.0, 4.5]]
    assert cleancut.remap(1.5, keep) == 0.5 and cleancut.remap(3.25, keep) == 1.25 and cleancut.remap(2.5, keep) is None
    words = [W("a", 1.1, 1.4), W("um", 2.2, 2.6), W("b", 3.1, 3.5)]
    assert cleancut.remap_words(words, keep) == [W("a", 0.1, 0.4), W("b", 1.1, 1.5)]
    assert cleancut.remap_points([{"t": 1.5, "cx": 0.4}, {"t": 2.5}], keep, "t") == [{"t": 0.5, "cx": 0.4}]
    assert cleancut.remap_points([2.5, 3.5], keep) == [1.5]


def test_audio_chain_takes_only_a_loudnorm_filter():
    two = "loudnorm=I=-14:TP=-1:LRA=11:measured_I=-20.00:measured_TP=-3.00:measured_LRA=4.00:measured_thresh=-30.00:offset=0.20:linear=true"
    assert take.audio_chain(10.0, two).startswith(two + ",afade")
    for bad in ("volume=9,loudnorm", "loudnorm=I=-14;[0:a]amovie=/etc/passwd", "loudnorm=I=-14,volume=20", 3):
        assert take.audio_chain(10.0, bad).startswith(take.LOUDNESS + ",")
    assert "TP=-1:" in take.LOUDNESS


def test_measured_loudness_parsing_refuses_silence_and_garbage():
    good = 'x\n{\n "input_i" : "-23.10", "input_tp" : "-6.0", "input_lra" : "3.2", "input_thresh" : "-33.5", "target_offset" : "0.1"\n}\n'
    assert cleancut._measured(good)["input_i"] == -23.1
    assert cleancut._measured(good.replace('"-23.10"', '"-inf"')) is None
    assert cleancut._measured("no json here") is None


def test_render_clip_strips_a_loudness_filter_from_the_job_spec(monkeypatch):
    seen = {}
    monkeypatch.setattr(take, "_render_clip", lambda path, m, *a, **k: (seen.update(m=m), (b"", b"", {"audio": {"loudnorm": "x"}}))[1])
    monkeypatch.setattr(cleancut, "loudnorm_filter", lambda *a: cleancut.LOUDNESS_1PASS)
    take.render_clip("x.mp4", {"start": 0, "end": 3, "_loud": "volume=50"}, [], [], [], {"audio": True}, {}, {})
    assert seen["m"]["_loud"] == cleancut.LOUDNESS_1PASS


# ---------------------------------------------------------------- the media (ffmpeg on a synthetic fixture)
def _fixture(tmp_path):
    """3.5 s: a tone ('speech') 0-1 s, silence 1-2.5 s, a tone 2.5-3.5 s; 25 fps colour bars."""
    src = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3.5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5,volume=0.3,"
                    "volume=enable='between(t,1,2.5)':volume=0", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-ar", "48000", str(src)], check=True)
    return src


def _ebur128(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    i = float(re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)[-1])
    tp = float(re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)[-1])
    return i, tp


@needs_ffmpeg
def test_prepare_cuts_the_silence_and_two_pass_hits_minus_14_lufs(tmp_path):
    src = _fixture(tmp_path)
    words = [W("Hello", 0.05, 0.5), W("there.", 0.55, 0.95), W("We", 2.55, 2.9), W("ship.", 2.95, 3.45)]
    sil = take.detect_silences(str(src))
    assert sil and 0.9 <= sil[0][0] <= 1.1
    got = cleancut.prepare(str(src), {"start": 0.0, "end": 3.5, "silences": sil}, words, [{"t": 3.0}], [2.6], {"audio": True}, str(tmp_path))
    assert got["path"] and 1.1 <= got["stats"]["trimmed_s"] <= 1.5 and got["stats"]["pauses"] == 1
    dur = take.probe(got["path"])["duration"]
    assert abs(dur - (3.5 - got["stats"]["trimmed_s"])) < 0.15
    assert got["m"]["start"] == 0.0 and "silences" not in got["m"] and got["words"][2]["s"] < 1.4
    f = cleancut.loudnorm_filter(got["path"], 0.0, dur)
    assert "measured_I=" in f and "linear=true" in f
    out = tmp_path / "loud.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", got["path"], "-af", f + ",aresample=48000", str(out)], check=True)
    i, tp = _ebur128(out)
    assert abs(i - (-14.0)) <= 1.5 and tp <= -0.5


@needs_ffmpeg
def test_render_clip_reports_trimmed_seconds_and_keeps_the_source_span(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_LAYOUTS", "0")
    src = _fixture(tmp_path)
    words = [W("Hello", 0.05, 0.5), W("there.", 0.55, 0.95), W("We", 2.55, 2.9), W("ship.", 2.95, 3.45)]
    from studio import brand
    kit = {**brand.DEFAULT_KIT, "name": "Bright Desk", "handle": "brightdesk"}
    p = {**brand.visual_params(kit, []), "end_card": False}
    m = {"start": 0.0, "end": 3.5, "hook": "Hello there", "trim_pauses": True, "silences": take.detect_silences(str(src))}
    data, cover, meta = take.render_clip(str(src), m, words, [], [], take.probe(str(src)), kit, p, W=180, H=320)
    assert data[4:8] == b"ftyp" and meta["trim"]["trimmed_s"] > 1.0
    assert meta["moment"] == {"start": 0.0, "end": 3.5}
    assert meta["duration"] <= 3.5 - 1.0 + take.COVER_SECS + 0.2
    assert meta["audio"]["two_pass"] is True
    off = take.render_clip(str(src), {**m, "trim_pauses": False}, words, [], [], take.probe(str(src)), kit, p, W=180, H=320)[2]
    assert "trim" not in off and off["duration"] >= 3.5


# ---------------------------------------------------------------- the take: toggle, spec, view
def test_trim_pauses_is_on_for_one_take_off_when_asked_and_validated(human, app, monkeypatch):
    house = app.state.house
    jobs = []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="render.yml", inputs=None, ttl=1800: (jobs.append(jid), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "t.mp4", "data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64).decode()}).json()
    bad = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "trim_pauses": "yes"})
    assert bad.status_code == 400 and "trim_pauses" in bad.json()["error"]
    on = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    off = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "trim_pauses": False}).json()
    assert on["input"]["trim_pauses"] is True and off["input"]["trim_pauses"] is False
    opus = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "style": "opus", "trim_pauses": True}).json()
    assert opus["input"]["trim_pauses"] is False                                  # the Opus-style render keeps its own pacing


def test_render_spec_carries_trim_and_only_the_clips_silences():
    run = {"input": {"style": "take", "trim_pauses": True}}
    an = {"silences": [[1.0, 2.0], [10.0, 11.0], ["x", 1], [50.0, 51.0]]}
    cls = next(v for v in vars(takes).values() if isinstance(v, type) and hasattr(v, "_trim_spec"))
    spec = cls._trim_spec(run, an, {"start": 9.0, "end": 30.0})
    assert spec == {"trim_pauses": True, "silences": [[10.0, 11.0]]}
    assert cls._trim_spec({"input": {"style": "take"}}, an, {"start": 0, "end": 5}) == {}   # takes from before: off
    assert cls._trim_spec({"input": {"style": "opus", "trim_pauses": True}}, an, {"start": 0, "end": 5}) == {}



def test_mcp_clips_from_video_forwards_trim_pauses_false(human, app, monkeypatch):
    got = []
    monkeypatch.setattr(app.state.house.takes, "start", lambda actor, body: got.append(body) or {"id": 1})
    tok = agent_token(human)
    for want in (False, True, None):
        args = {"media_url": "https://x.test/m/abc", "owns": True, **({} if want is None else {"trim_pauses": want})}
        res, _ = mcp_call(human, tok, "clips_from_video", args)
        assert not res["isError"]
    assert [b["trim_pauses"] for b in got] == [False, True, None]   # an explicit false reaches Takes.start
