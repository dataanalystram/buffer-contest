"""Caption word repairs found in the 25 Nov baseline: "RE -RENDER" and doubled words at recogniser seams."""
from studio.media import captions_ass as ca


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


def test_hyphen_and_apostrophe_pieces_join_the_word_before():
    out = ca.clean_words([W("re", 1.0, 1.2), W("-render", 1.2, 1.6), W("don", 2.0, 2.2), W("'t", 2.2, 2.3)])
    assert [w["w"] for w in out] == ["re-render", "don't"] and out[0]["e"] == 1.6


def test_a_free_standing_dash_or_a_late_piece_stays_apart():
    out = ca.clean_words([W("well,", 1.0, 1.3), W("-no", 1.35, 1.6), W("up", 2.0, 2.2), W("-front", 2.6, 2.9)])
    assert [w["w"] for w in out] == ["well,", "-no", "up", "-front"]


def test_seam_repeats_drop_but_real_repeats_stay():
    seam = ca.clean_words([W("we", 1.0, 1.4), W("we", 1.3, 1.5), W("shipped", 1.5, 1.9)])
    assert [w["w"] for w in seam] == ["we", "shipped"] and seam[0]["e"] == 1.5
    said = ca.clean_words([W("no,", 1.0, 1.2), W("no", 1.3, 1.5)])
    assert [w["w"] for w in said] == ["no,", "no"]


def test_build_draws_the_repaired_words():
    ass = ca.build([W("we", 0.0, 0.3), W("re", 0.3, 0.5), W("-render", 0.5, 0.9), W("re-render", 0.8, 1.0)], 0.0)
    body = "".join(x for x in ass.splitlines() if x.startswith("Dialogue"))
    assert "RE-RENDER" in body and "RE -RENDER" not in body and body.count("RE-RENDER") == 1


def test_a_repeat_right_after_a_sentence_end_or_of_a_long_word_is_the_recogniser():
    from studio.media import captions_ass as ca
    ws = [{"w": "this", "s": 0.0, "e": 0.2}, {"w": "week.", "s": 0.2, "e": 0.5}, {"w": "week,", "s": 0.52, "e": 0.8},
          {"w": "complaints", "s": 1.0, "e": 1.5}, {"w": "complaints", "s": 1.53, "e": 2.0}, {"w": "with", "s": 2.0, "e": 2.2},
          {"w": "no,", "s": 3.0, "e": 3.2}, {"w": "no", "s": 3.22, "e": 3.4}]
    assert [w["w"] for w in ca.clean_words(ws)] == ["this", "week.", "complaints", "with", "no,", "no"]


def test_genuine_repeats_stay():
    from studio.media import captions_ass as ca
    ws = [{"w": "really", "s": 0.0, "e": 0.3}, {"w": "really", "s": 0.32, "e": 0.6}, {"w": "good,", "s": 0.6, "e": 0.9},
          {"w": "faster,", "s": 1.0, "e": 1.3}, {"w": "faster!", "s": 1.32, "e": 1.6},
          {"w": "seriously,", "s": 2.0, "e": 2.5}, {"w": "seriously.", "s": 2.52, "e": 3.0}]
    assert [w["w"] for w in ca.clean_words(ws)] == [w["w"] for w in ws]
