"""Motion moments (studio/media/motion.py, HQ_MOTION=1): a stat pop and a kinetic line, only where the words earn them."""
from studio.media import captions_ass, motion


def W(text, s, step=0.35):
    return [{"w": w, "s": round(s + i * step, 2), "e": round(s + (i + 1) * step - 0.03, 2)} for i, w in enumerate(text.split())]


def test_off_by_default_and_says_so(monkeypatch):
    monkeypatch.delenv("HQ_MOTION", raising=False)
    p = motion.plan(W("We grew 300% in 2 days.", 5), 0.0)
    assert p["fx"] == [] and "HQ_MOTION" in p["skipped"][0]


def test_a_number_with_a_unit_gets_a_stat_and_a_punchy_line_gets_kinetic_type(monkeypatch):
    monkeypatch.setenv("HQ_MOTION", "1")
    ws = W("So here is what happened to us this year.", 0) + W("We grew 300% in 2 days.", 4) + W("That was the long version of the story.", 7) \
        + W("Nobody saw it coming!", 13)
    p = motion.plan(ws, 0.0, [{"start": 0, "end": 30, "layout": "track"}])
    kinds = [f["kind"] for f in p["fx"]]
    assert kinds == ["stat", "line"]
    st = p["fx"][0]
    assert st["text"] == "300%" and st["s"] >= motion.MIN_START and st["why"]
    ass = captions_ass.build(ws, 0.0, fx=p["fx"])
    assert "300%" in ass and "NOBODY" in ass
    # the caption line the stat replaces isn't drawn twice
    assert sum(1 for line in ass.splitlines() if line.startswith("Dialogue: 0") and "300%" in line) == 0


def test_it_says_no_where_an_effect_would_not_help(monkeypatch):
    monkeypatch.setenv("HQ_MOTION", "1")
    early = motion.plan(W("We grew 300% in 2 days.", 0.5), 0.0)
    assert early["fx"] == [] and "too early" in early["skipped"][0]
    screen = motion.plan(W("We grew 300% in 2 days.", 5), 0.0, [{"start": 0, "end": 30, "layout": "screen"}])
    assert screen["fx"] == [] and "cover what's shown" in screen["skipped"][0]
    bare = motion.plan(W("In 2017 we moved to step 3 of the plan.", 5), 0.0)
    assert bare["fx"] == [] and "Nothing in this clip needed it" in bare["skipped"][0]
    own = motion.plan(W("We grew 300% in 2 days.", 5), 0.0, captions=False)
    assert own["fx"] == [] and "own burned-in captions" in own["skipped"][0]


def test_money_and_multipliers(monkeypatch):
    monkeypatch.setenv("HQ_MOTION", "1")
    assert motion.plan(W("It made $5 million last year.", 5), 0.0)["fx"][0]["text"] == "$5 MILLION"
    assert motion.plan(W("It is 10x faster now.", 5), 0.0)["fx"][0]["text"] == "10X"


def test_sound_accents_are_made_by_ffmpeg_and_sit_well_under_the_voice(tmp_path):
    import shutil
    import subprocess
    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    g = ";".join(motion.sfx_graph([{"kind": "stat", "s": 0.5}, {"kind": "line", "s": 1.5}], "[0:a]", "[o]"))
    out = tmp_path / "sfx.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo:d=2.5", "-filter_complex", g,
                    "-map", "[o]", "-t", "2.5", str(out)], check=True)
    for ss, t in (("0.2", "0.35"), ("1.5", "0.25")):
        r = subprocess.run(["ffmpeg", "-hide_banner", "-ss", ss, "-t", t, "-i", str(out), "-af", "volumedetect", "-f", "null", "-"],
                           capture_output=True, text=True)
        peak = float(r.stderr.split("max_volume:")[1].split("dB")[0])
        assert -30 < peak < -15            # there, and well under a voice normalised to -14 LUFS
    assert motion.sfx_graph([], "[x]", "[y]") == ["[x]anull[y]"]
