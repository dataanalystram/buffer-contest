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


def test_blurry_background_faces_do_not_make_a_shot_unsure_and_a_third_face_is_ignored():
    s = sig()
    s["faces"].append({"id": 3, "presence": 1.0, "cx": 0.5, "w": 0.04, "h": 0.1, "motion": 0.0, "score": 0.2})
    for f in s["faces"][:2]:
        f["score"] = 0.9
    s["low_conf"] = True            # the old summary flag: min score over ALL faces
    assert decide.rule(s, (1920, 1080), {"split": True})["layout"] == "split"
    third = {"id": 4, "presence": 1.0, "cx": 0.9, "w": 0.1, "h": 0.25, "motion": 0.0, "score": 0.9}
    s["faces"].append(third)
    assert decide.rule(s, (1920, 1080), {"split": True})["layout"] == "split"
