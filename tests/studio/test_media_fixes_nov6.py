"""Fixes from the real-video One take test (6 Nov 2026): punch-in on 720p and on full-height crops, readable alt text,
no doubled punctuation, no 1-word sentence fragments, whole watermark handles, pauses inside ASR-stretched words, the
face-track provider string."""
import shutil
import subprocess

import pytest

from studio import take
from studio.media import cleancut, facedetect, ocr
from studio.media.reframe import punch
from studio.media.reframe.decide import OUT_H

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


# ---------------------------------------------------------------- 1. punch-in on 720p
def test_punch_fires_on_a_720p_source_within_the_quality_cap():
    shot = {"layout": "track", "start": 0, "end": 14, "path": {"crop_w": 404, "crop_h": 720}}
    pp = punch.plan_shot(shot, [3.9, 9.3], {"w": 0.0856, "h": 0.2097, "cy": 0.5068}, (1280, 720))
    assert pp and 1.02 <= pp["peak"] <= punch.ZMAX
    assert OUT_H / (720 / pp["peak"]) <= punch.PUNCH_UPSCALE_MAX + 1e-9  # total upscale stays within the cap
    small = {"layout": "track", "start": 0, "end": 14, "path": {"crop_w": 304, "crop_h": 540}}  # 540p: past the cap
    assert punch.plan_shot(small, [3.9], {"w": 0.1, "h": 0.2, "cy": 0.5}, (960, 540)) is None


# ---------------------------------------------------------------- 2. the vertical axis uses its own drift
def test_a_full_height_crop_does_not_add_the_horizontal_dead_zone_vertically():
    assert punch.safe_peak(0.448, 0.332, 0.296, dead_y=0.0) >= 1.1
    shot = {"layout": "track", "start": 0, "end": 14, "path": {"crop_w": 608, "crop_h": 1080}}
    face = {"w": 0.142, "h": 0.332, "cy": 0.296, "dy": 0.0}
    pp = punch.plan_shot(shot, [3.94, 9.3], face, (1920, 1080))
    assert pp and pp["peak"] >= 1.1 and [w[0] for w in pp["windows"]] == [0.0, 3.94, 9.3]
    # a face that moves up and down gets a smaller peak, and still never leaves the frame at the peak
    moving = punch.plan_shot(shot, [3.94], {**face, "dy": 0.08}, (1920, 1080))
    z = moving["peak"] if moving else 1.0
    assert z < pp["peak"] and face["cy"] - (0.08 + face["h"] / 2) * z >= punch.MARGIN - 1e-9
    # a crop shorter than the frame can drift vertically: the dead zone counts there, as before
    assert punch.safe_peak(0.2, 0.2, 0.4) == punch.safe_peak(0.2, 0.2, 0.4, punch.DEAD, punch.DEAD)
    short = {"layout": "track", "start": 0, "end": 14, "path": {"crop_w": 506, "crop_h": 900}}
    sp = punch.plan_shot(short, [3.94], {"w": 0.1, "h": 0.15, "cy": 0.4}, (1920, 1080))
    fh = 0.15 * 1080 / 900
    assert sp and punch.FACE_Y - (punch.DEAD + fh / 2) * sp["peak"] >= punch.MARGIN - 1e-9


def test_face_size_reports_the_vertical_spread():
    pts = [(t, {"x": 0.4, "y": 0.2 + 0.01 * (t % 5), "w": 0.1, "h": 0.2}) for t in range(20)]
    f = punch.face_size(pts)
    assert 0.015 <= f["dy"] <= 0.025 and f["w"] == 0.1
    # two samples far above the median (CodeRabbit #141): the drift is their distance, so the peak keeps them inside
    pts = [(t, {"x": 0.4, "y": 0.15, "w": 0.1, "h": 0.2}) for t in range(18)] + [(t, {"x": 0.4, "y": 0.01, "w": 0.1, "h": 0.2}) for t in (18, 19)]
    f = punch.face_size(pts)
    assert f["dy"] >= 0.14 - 1e-9
    shot = {"layout": "track", "start": 0, "end": 14, "path": {"crop_w": 608, "crop_h": 1080}}
    pp = punch.plan_shot(shot, [3.0], f, (1920, 1080))
    z = pp["peak"] if pp else 1.0
    assert f["cy"] - (f["dy"] + f["h"] / 2) * z >= punch.MARGIN - 1e-9 or pp is None


# ---------------------------------------------------------------- 4. OCR noise stays out of alt text
T1 = [{"t": 1, "boxes": [{"text": "a", "conf": 73.3}, {"text": "N", "conf": 78.6}]},
      {"t": 3, "boxes": [{"text": "a", "conf": 62.5}, {"text": "IN", "conf": 60.6}]},
      {"t": 5, "boxes": [{"text": "J", "conf": 62.7}]}, {"t": 9, "boxes": [{"text": "mi", "conf": 62.5}, {"text": "i", "conf": 72.6}]}]
T2 = [{"t": 1, "boxes": [{"text": "4", "conf": 94.7}]}, {"t": 5, "boxes": [{"text": "4", "conf": 95.5}, {"text": "fr", "conf": 70.9}]}]
T4 = [{"t": 1, "boxes": [{"text": "TikTok", "conf": 87.6}, {"text": "@s", "conf": 88.4}, {"text": "CGC", "conf": 64.6}]},
      {"t": 3, "boxes": [{"text": "TikTok", "conf": 87.6}, {"text": "CGC", "conf": 64.9}]},
      {"t": 5, "boxes": [{"text": "TikTok", "conf": 91.6}, {"text": "scrollclips", "conf": 74.1}, {"text": "CGC", "conf": 70.5}]},
      {"t": 7, "boxes": [{"text": "TikTok", "conf": 90.0}, {"text": "liclip", "conf": 68.6}]},
      {"t": 9, "boxes": [{"text": "TikTok", "conf": 87.8}, {"text": "ollclips", "conf": 60.4}]},
      {"t": 11, "boxes": [{"text": "TikTok", "conf": 88.4}, {"text": "“A", "conf": 69.0}, {"text": "GS", "conf": 81.0}]},
      {"t": 13, "boxes": [{"text": "TikTok", "conf": 89.8}, {"text": "G", "conf": 81.1}, {"text": "YY", "conf": 67.7}]}]


def test_alt_text_drops_ocr_noise():
    assert ocr.alt_text("time.", "a N IN J mi i") == "Video clip: “time.”"
    assert ocr.readable_from_frames(T1) == "" and ocr.readable_from_frames(T2) == ""
    assert ocr.readable_from_frames(T4) == "TikTok"
    real = [{"t": 1, "boxes": [{"text": "‘The", "conf": 86}, {"text": "Free", "conf": 93}, {"text": "Encyclopedia", "conf": 66},
                               {"text": "6", "conf": 75}, {"text": "articles", "conf": 80}, {"text": "ould", "conf": 62}]}]
    assert ocr.readable_from_frames(real) == "The Free Encyclopedia 6 articles"
    assert ocr.alt_text("We shipped it", "Launch day v2 CGC GS") == "Video clip: “We shipped it”. On-screen text: Launch day v2"


# ---------------------------------------------------------------- 5. no doubled punctuation
def test_alt_text_never_doubles_punctuation():
    assert ocr.alt_text("time.", "Launch day") == "Video clip: “time.” On-screen text: Launch day"
    assert ocr.alt_text("Is it live?", "Launch day") == "Video clip: “Is it live?” On-screen text: Launch day"
    assert ocr.alt_text("Done.", "") == "Video clip: “Done.”"
    long = ocr.alt_text("word " * 200, "Launch day")
    assert len(long) <= 420 and long.endswith("On-screen text: Launch day")
    for hook in ("a.", "b?", "c!", "d…", "e"):
        out = ocr.alt_text(hook, "Launch day")
        assert ".”." not in out and "?”." not in out and "!”." not in out and ".." not in out


# ---------------------------------------------------------------- 6. no sentence fragments
def test_a_run_on_never_leaves_a_one_word_sentence():
    ws = [W("w%d" % i, i * .3, i * .3 + .2) for i in range(40)] + [W("time.", 12, 12.2), W("Now", 13, 13.2), W("next.", 13.3, 13.5)]
    s = take.sentences(ws)
    assert s[0]["text"].endswith("w39 time.") and s[1]["text"] == "Now next." and len(s) == 2


def test_a_long_run_on_splits_at_its_comma_and_every_piece_is_whole():
    ws = [W(("w%d," if i in (30, 70) else "w%d") % i, i * .3, i * .3 + .2) for i in range(100)] + [W("end.", 30.1, 30.3)]
    s = take.sentences(ws)
    assert s[0]["text"].endswith("w30,")
    assert all(len(x["words"]) >= take.SENT_MIN for x in s) and all(len(x["words"]) <= take.SENT_MAX + take.SENT_MIN for x in s)
    assert sum(len(x["words"]) for x in s) == 101
    # no comma: the longest pause in the second half
    ws = [W("w%d" % i, i * .3 + (0.8 if i >= 35 else 0), i * .3 + .2 + (0.8 if i >= 35 else 0)) for i in range(50)]
    assert take.sentences(ws)[0]["text"].endswith("w34")


# ---------------------------------------------------------------- 7. the whole watermark handle
def test_watermark_handle_is_never_a_fragment():
    assert ocr.watermarks("TikTok @s CGC scrollclips liclip ollclips", "huhqueue_test") == ["tiktok", "@scrollclips"]
    assert ocr.watermarks("TikTok @s CGC") == ["tiktok"]                     # a fragment nothing completes is dropped
    assert ocr.watermarks("@scr then @scrollclips") == ["@scrollclips"]     # a prefix folds into the longer read
    assert ocr.watermarks("@s said hello") == []                            # never completed with a dictionary word
    assert ocr.watermarks("TikTok @Cool.Creator_1 hello", own_handle="brightdesk") == ["tiktok", "@cool.creator_1"]


# ---------------------------------------------------------------- 8. pauses inside stretched words
STRETCH = [W("that", 15.63, 16.51), W("kind", 16.51, 18.07), W("of", 18.1, 18.3)]


def test_a_quiet_pause_inside_a_stretched_word_is_cut():
    plan = cleancut.plan_cuts(STRETCH, 15.6, 18.3, [[16.79, 17.6]], quiet_fn=lambda a, b: True)
    assert plan["pauses"] == 1 and 0.5 <= plan["removed"] <= 0.8, plan
    cut = [plan["keep"][0][1], plan["keep"][1][0]]
    assert 16.79 <= cut[0] < cut[1] <= 17.6
    assert [w["w"] for w in cleancut.remap_words(STRETCH, plan["keep"])] == ["that", "kind", "of"]  # the word stays
    assert cleancut.plan_cuts(STRETCH, 15.6, 18.3, [[16.79, 17.6]], quiet_fn=lambda a, b: False)["skipped"] == "nothing to trim"
    assert cleancut.plan_cuts(STRETCH, 15.6, 18.3, [[16.79, 17.6]])["skipped"] == "nothing to trim"  # no energy check
    assert cleancut.plan_cuts(STRETCH, 15.6, 18.3, None, quiet_fn=lambda a, b: True)["skipped"] == "nothing to trim"

    # a filler whose own cut was too short to make stays dropped even when a neighbour's in-word cut splits it
    um = [W("so", 0.0, 0.3), W("um", 1.0, 1.05), W("next", 1.1, 1.4)]
    assert [w["w"] for w in cleancut.remap_words(um, [[0.0, 1.02], [1.03, 1.4]])] == ["so", "next"]

    def boom(a, b):
        raise RuntimeError("x")
    assert cleancut.plan_cuts(STRETCH, 15.6, 18.3, [[16.79, 17.6]], quiet_fn=boom)["skipped"] == "nothing to trim"


def _fixture(tmp_path):
    src = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25:duration=3.5",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3.5,volume=0.3,"
                    "volume=enable='between(t,1,2.5)':volume=0", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-ar", "48000", str(src)], check=True)
    return src


@needs_ffmpeg
def test_energy_check_and_prepare_cut_a_silence_the_asr_put_inside_a_word(tmp_path):
    src = _fixture(tmp_path)
    q = cleancut.quiet_check(str(src), 0.0, 3.5)
    assert q and q(1.2, 2.3) and not q(0.1, 0.9)
    words = [W("Hello", 0.05, 0.5), W("there", 0.55, 2.9), W("ship.", 2.95, 3.45)]  # "there" stretched over the silence
    sil = take.detect_silences(str(src))
    got = cleancut.prepare(str(src), {"start": 0.0, "end": 3.5, "silences": sil}, words, [], [], {"audio": True}, str(tmp_path))
    assert got.get("path") and got["stats"]["pauses"] == 1 and got["stats"]["trimmed_s"] >= 1.0, got["stats"]
    assert [w["w"] for w in got["words"]] == ["Hello", "there", "ship."]


# ---------------------------------------------------------------- 11. the provider string
def test_face_track_names_the_mediapipe_failure_once(monkeypatch, capsys):
    def fail(*a, **k):
        raise facedetect.DetectorError("MediaPipe couldn't load: libEGL.so.1: cannot open shared object file")
    monkeypatch.setattr(facedetect, "mediapipe_detector", fail)
    monkeypatch.setattr(take, "_face_track_cv2", lambda path, fps, why: ([], f"OpenCV Haar cascade ({why})"))
    _, how = take.face_track("x.mp4", 10.0)
    assert how.count("MediaPipe couldn't load") == 1 and "libEGL" in how
    assert capsys.readouterr().out.count("MediaPipe couldn't load") == 1
