"""Split asked for: two people apart are stacked even when only one talks (a podcast host who mostly listens)."""
from studio.media.reframe import decide


def sig(dur=20.0, ml=0.9, mr=0.0):
    f = lambda i, cx, m: {"id": i, "presence": 1.0, "cx": cx, "w": 0.12, "h": 0.30, "motion": m}
    return {"faces": [f(1, 0.25, ml), f(2, 0.75, mr)], "dur": dur, "edge": 0.1, "flat": 0.1}


def test_split_asked_stacks_two_people_even_if_one_listens():
    r = decide.rule(sig(), (1920, 1080), {"split": True})
    assert r["layout"] == "split" and r["rule"] == "R6"
    assert decide.rule(sig(), (1920, 1080), {})["layout"] != "split"          # not asked: the old rules (one talker: track)
    short = decide.rule(sig(dur=1.0), (1920, 1080), {"split": True})
    assert short["layout"] == "fit" and "split" in short["why"].lower()      # and it says why when it can't
