"""Clip openings and hooks (25 Nov baseline): clips started on an answer without its question, on a reaction without
the line it reacts to, on a stray "it."; the hook was always the first sentence; every offline clip had the same reason."""
from studio import take


def S(text, s, e):
    words, n = text.split(), len(text.split())
    step = (e - s) / n
    return [{"w": w, "s": round(s + i * step, 2), "e": round(s + (i + 1) * step - 0.02, 2)} for i, w in enumerate(words)]


def sents(*rows):
    words = []
    for r in rows:
        words += S(*r)
    return take.sentences(words)


def test_an_answer_reaches_back_for_its_question():
    ss = sents(("So what happened to the customers after the outage?", 0, 4), ("We lost zero customers, not one.", 4.5, 8),
               ("And that taught us something about trust that I still use every single day now.", 8.5, 30))
    a, b, notes = take.tidy_span(ss, 1, 2)
    assert (a, b) == (0, 2) and "question" in notes[0]


def test_a_reaction_keeps_the_line_it_reacts_to_and_a_stray_fragment_goes():
    ss = sents(("We had six weeks of money left.", 0, 3), ("Wow, what did you do on that first day?", 3.4, 6),
               ("We called every customer.", 6.5, 9))
    assert take.tidy_span(ss, 1, 2)[:2] == (0, 2)
    ss2 = sents(("it.", 0, 0.4), ("Most founders price their product far too low.", 0.6, 4), ("Here is why.", 4.2, 5))
    assert take.tidy_span(ss2, 0, 2)[:2] == (1, 2)


def test_a_clip_does_not_end_on_a_new_question():
    ss = sents(("Pricing is a signal.", 0, 2), ("So why do we fear it?", 2.2, 4), ("Because nobody taught us.", 4.3, 6))
    assert take.tidy_span(ss, 0, 1)[:2] == (0, 2)


def test_a_leading_so_alone_is_fine():
    ss = sents(("The launch went well.", 0, 2), ("So here's the thing: most founders price too low.", 2.5, 6))
    assert take.needs_setup(ss, 1) is None


def test_the_hook_is_the_strongest_line_not_the_first_and_stays_verbatim():
    ss = sents(("Okay so let me tell you about our week.", 0, 3), ("We shipped the whole thing in 2 days.", 3.2, 6),
               ("It was fine.", 6.2, 7))
    hook = take.best_line(ss, 0, 2)
    assert hook == "We shipped the whole thing in 2 days."
    assert take.verbatim(hook, take.span_text(ss, 0, 2))


def test_a_stutter_leaves_the_hook():
    assert take.clean_hook("How did how did that prep you for this?") == "How did that prep you for this?"
    assert take.clean_hook("No, no, no.") == "No, no, no."


def test_reasons_differ_between_clips():
    ss = sents(("Why do most launches fail?", 0, 3), ("Because nobody tells the story twice.", 3.2, 25),
               ("We grew 40 percent in a month.", 26, 30), ("That changed how we hire everyone.", 30.2, 50))
    r1 = take.reason(ss, 0, 1, take.best_line(ss, 0, 1), [])
    r2 = take.reason(ss, 2, 3, take.best_line(ss, 2, 3), [])
    assert r1 != r2 and "question" in r1 and "number" in r2


def test_no_speech_fails_without_waiting_for_the_face_track(monkeypatch, tmp_path):
    import threading
    import pytest
    from studio import mediasafe
    gate = threading.Event()
    monkeypatch.setattr(mediasafe, "check_source", lambda p: None)
    monkeypatch.setattr(take, "probe", lambda p: {"audio": True, "duration": 60.0, "width": 1920, "height": 1080})
    monkeypatch.setattr(take, "face_track", lambda p, d: (gate.wait(30), ([], "x"))[1])   # would hold 30 s
    monkeypatch.setattr(take, "detect_scenes", lambda p: ([], "x"))
    monkeypatch.setattr(take, "detect_silences", lambda p: [])
    monkeypatch.setattr(take, "detect_speech_gaps", lambda p: ([], "x"))
    monkeypatch.setattr(take, "transcribe", lambda p, m: ([], "", "test"))
    import time
    t = time.time()
    with pytest.raises(take.TakeError, match="No speech found"):
        take.analyze(str(tmp_path / "v.mp4"))
    gate.set()
    assert time.time() - t < 5


def test_reframe_line_says_what_the_scenes_used():
    from studio.takes import reframe_summary
    ok = [{"id": "c1", "meta": {"layout": "mixed", "layout_plan": [{"layout": "fit"}, {"layout": "fit"}, {"layout": "track"}]}},
          {"id": "c2", "meta": {"layout": "fit", "layout_plan": [{"layout": "fit"}]}}]
    line = reframe_summary(ok, ["mixed", "fit"], ["c2"], "no face found, full frame used")
    assert line == "Layouts across 4 scenes: following a face in 1, full frame in 3" and "centered" not in line
    old = [{"id": "c1", "meta": {"layout": "pad"}}]
    assert reframe_summary(old, ["pad"], ["c1"], "no face found, centered crop used").startswith("Face-tracked on 0 of 1")
