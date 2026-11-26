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
