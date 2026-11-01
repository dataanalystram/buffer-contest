"""Long video -> Opus-style clips (studio/media/longform.py): request parsing, moment picking with scores and reasons,
hook titles, subject-aware reframing, and real FFmpeg renders (track / pad / burned-in subtitles)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import take
from studio.media import longform as L
from studio.media import subtitles as S

SAMPLE = json.loads((Path(__file__).resolve().parents[2] / "studio" / "sample_take" / "sample.json").read_text())
WORDS = SAMPLE["analysis"]["words"]          # the NASA talk the owner split before (public domain), 179 s


# ------------------------------------------------------------------ the request
@pytest.mark.parametrize("prompt,n,split", [
    ("split this into 5 shorts https://youtu.be/QJf0H64BnFo", 5, True),
    ("Split it into five clips for TikTok", 5, True),
    ("find the best moments of this video", None, True),
    ("give me the top 3 moments from this talk", 3, True),
    ("make 4 reels out of this podcast", 4, True),
    ("opus clip this keynote", None, True),
    ("post about this launch in all my channels", None, False),
    ("make a reel about this", None, False),
])
def test_split_requests_are_recognised_with_their_count(prompt, n, split):
    r = L.parse_request(prompt)
    assert r["split"] is split and r["n"] == n


def test_length_words_set_the_clip_range():
    assert L.parse_request("split into 3 shorts, 30-45 seconds")[["lo", "hi"][0]] == 30.0
    r = L.parse_request("make 3 clips under 30 seconds")
    assert r["hi"] == 30.0 and r["lo"] <= 15.0
    assert L.parse_request("best moments")["lo"] == L.MIN_S and L.parse_request("best moments")["hi"] == L.MAX_S
    assert L.parse_request("split into 50 clips")["n"] == L.MAX_CLIPS


def test_default_count_is_three_to_five():
    assert L.default_count(60) == 3 and L.default_count(185) == 4 and L.default_count(5400) == 5


# ------------------------------------------------------------------ picking moments
def test_moments_are_non_overlapping_whole_sentences_with_score_reason_and_hook():
    out = L.pick_moments(WORDS, 179.1, n=3)
    ms = out["moments"]
    assert len(ms) == 3 and out["picked_by"] == "built-in scorer"
    full = take.norm(" ".join(w["w"] for w in WORDS))
    for m in ms:
        assert L.MIN_S - 1 <= m["duration"] <= L.MAX_S + 1
        assert take.norm(m["text"]) in full                          # extracted, never invented
        assert 0 <= m["score"] <= 100 and m["reason"] and m["reasons"]
        assert m["hook"] and len(m["hook"].split()) <= 12
        hook_words = [w for w in take.norm(m["hook"]).split() if w]
        assert all(w in take.norm(m["text"]).split() for w in hook_words)   # only words said in the clip
    assert sorted(m["rank"] for m in ms) == [1, 2, 3]
    for x, y in zip(ms, ms[1:]):
        assert x["end"] <= y["start"]
    assert L.pick_moments(WORDS, 179.1, n=3) == out                  # deterministic


def test_a_model_can_rank_but_every_span_is_validated():
    sents = L.sentences(WORDS)

    def chat(engine, system, user):
        assert "TRANSCRIPT" in user and "20-60 second" in user
        return {"moments": [{"start": 9, "end": 13, "hook": "Biofilms are very important to NASA.", "why": "clear stakes", "score": 91},
                            {"start": 40, "end": 99, "hook": "made up", "score": 99}]}
    out = L.pick_moments(WORDS, 179.1, n=3, engine={"source": "your key"}, chat_json=chat)
    assert out["picked_by"].startswith("your key")
    first = next(m for m in out["moments"] if m["reason"] == "clear stakes")
    assert first["hook"] == "Biofilms are very important to NASA." and sents[9]["text"].startswith("Biofilms")
    assert len(out["moments"]) == 3


def test_auto_count_leaves_out_weak_moments_instead_of_padding():
    w = S.parse_captions(Path(__file__).with_name("fixtures").joinpath("nasa_grc_2016.vtt").read_text())
    out = L.pick_moments(w, 185.1)
    assert 3 <= len(out["moments"]) <= 4
    assert min(m["score"] for m in out["moments"]) >= 40


def test_auto_captions_without_punctuation_split_on_pauses():
    words, t = [], 0.0
    for chunk in (["so", "here", "is", "the", "thing"] * 3, ["why", "does", "this", "matter", "to", "you"] * 2):
        for x in chunk:
            words.append({"w": x, "s": t, "e": t + 0.3})
            t += 0.35
        t += 1.0                                                    # a pause between thoughts
    sents = L.sentences(words)
    assert len(sents) == 2 and sents[1]["text"].startswith("why does")


def test_hook_titles_drop_throat_clearing_and_cut_at_a_clause():
    def sent(text, i=0):
        return {"i": i, "s": 0.0, "e": 5.0, "text": text, "words": []}
    s = [sent("First of all, I'd like to mention the fact that we've received two awards from the ISS research program.")]
    assert L.hook_title(s, 0, 0) == "We've received two awards from the ISS research program."
    s = [sent("Biofilms are very important to NASA--this is something that is undesired aboard the station.")]
    assert L.hook_title(s, 0, 0) == "Biofilms are very important to NASA"
    s = [sent("Um, how do you want to how do you want to approach it?")]
    assert L.hook_title(s, 0, 0) == "How do you want to approach it?"
    s = [sent("Our next question is, when judging real usefulness, what evidence would convince you most?")]
    assert L.hook_title(s, 0, 0) == "When judging real usefulness, what evidence would convince you most?"


def test_reasons_explain_the_score():
    sents = [{"i": 0, "s": 0, "e": 3, "text": "Why do 3 of 4 launches fail?", "words": []},
             {"i": 1, "s": 3, "e": 25, "text": "Because nobody tells the founders the real number.", "words": []}]
    rs = L.reasons(sents, 0, 1)
    assert "opens with a question" in rs and "a number up front" in rs and "ends on a full sentence" in rs
    assert L.stutters("how do you want to how do you want to go") == 1


# ------------------------------------------------------------------ reframe
def _faces(xs, start=0.0):
    return [{"t": start + i / 2, "cx": x, "cy": 0.4, "w": 0.12, "score": 0.95} for i, x in enumerate(xs)]


def test_reframe_tracks_the_speaker_per_ratio_and_pads_when_no_one_is_on_camera():
    faces = _faces([0.7] * 40)
    p916 = L.reframe_plan(faces, 0, 20, 1920, 1080, "9:16")
    p45 = L.reframe_plan(faces, 0, 20, 1920, 1080, "4:5")
    assert p916["mode"] == p45["mode"] == "track"
    assert p916["crop_w"] == 608 and p45["crop_w"] == 864               # 1080 * 9/16, 1080 * 4/5
    assert take.face_coverage(p916, faces, 0, 20, 1920) == 1.0
    assert L.reframe_plan([], 0, 20, 1920, 1080)["mode"] == "pad"     # screen recording / slides: whole frame
    assert L.reframe_plan(faces, 0, 20, 1920, 1080, detector=False)["mode"] == "center"
    burned = L.reframe_plan(faces, 0, 20, 1920, 1080, burned_subs=True)
    assert burned["src_h"] == 906 and burned["crop_w"] < p916["crop_w"]  # planned on the frame without the subtitle band


# ------------------------------------------------------------------ real renders (FFmpeg)
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


def _talk(path: Path, secs: float = 9.0, subs: bool = False) -> None:
    """A 16:9 'talk': a bright square (the speaker) on the right, a tone, optionally white subtitle text at the bottom."""
    vf = "drawbox=x=1150:y=300:w=260:h=300:color=0xF0C8A0:t=fill"
    if subs:
        vf += ",drawbox=x=480:y=940:w=960:h=70:color=black@0.6:t=fill,drawtext=text='THIS IS A BURNED SUBTITLE':fontcolor=white:fontsize=56:x=(w-tw)/2:y=950"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c=0x203040:s=1920x1080:d={secs}:r=30",
                    "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}", "-vf", vf, "-c:v", "libx264", "-preset",
                    "ultrafast", "-c:a", "aac", "-shortest", str(path)], check=True)


def _words(secs: float):
    out, t = [], 0.3
    for i in range(int(secs / 0.5) - 1):
        out.append({"w": ("Why" if i == 0 else "word") + ("." if i % 6 == 5 else ""), "s": t, "e": t + 0.4})
        t += 0.5
    return out


@needs_ffmpeg
def test_split_renders_reframed_clips_with_hook_captions_credit_and_end_card(tmp_path):
    src = tmp_path / "talk.mp4"
    _talk(src)
    words = _words(9)
    faces = _faces([0.66] * 18)
    man = L.split_video(str(src), out_dir=str(tmp_path / "out"), words=words, n=1, lo=4, hi=8, ratios=["9:16", "1:1"],
                        credit="Video: Test", official=True, faces=faces, scenes=[], sheets=True)
    assert man["transcript"]["source"] == "given" and man["burned_subs"] is False
    c = man["clips"][0]
    assert c["hook"] and c["reason"] and c["score"] >= 0
    by = {r["ratio"]: r for r in c["renders"]}
    assert (by["9:16"]["width"], by["9:16"]["height"]) == (1080, 1920) and (by["1:1"]["width"], by["1:1"]["height"]) == (1080, 1080)
    for r in c["renders"]:
        assert r["reframe"]["mode"] == "track" and r["reframe"]["face_coverage"] == 1.0
        assert r["captions"] is True and not r["caption_problems"]
        assert r["duration"] >= (c["end"] - c["start"]) + 2.5           # the clip + the 3 s Follow end card
        assert Path(r["sheet"]).is_file()
        hook = r["boxes"]["hook"]
        assert 0 <= hook[0] and hook[2] <= r["width"] and hook[3] < r["height"] * 0.5   # hook title on top, on canvas
    # the speaker (bright box on the right of the source) is in the 9:16 frame: its centre column is skin-coloured
    from PIL import Image
    fr = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "2", "-i", by["9:16"]["path"], "-frames:v", "1", str(fr)], check=True)
    px = Image.open(fr).convert("RGB").getpixel((540, 860))
    assert px[0] > 180 and px[2] < 200, px
    assert json.loads((tmp_path / "out" / "manifest.json").read_text())["clips"][0]["id"] == "m1"


@needs_ffmpeg
def test_no_face_pads_and_burned_subtitles_are_cropped_not_doubled(tmp_path):
    src = tmp_path / "subs.mp4"
    _talk(src, subs=True)
    m = {"start": 0.5, "end": 6.0, "hook": "Why word word"}
    r = L.render_moment(str(src), m, _words(9), ratio="9:16", out=str(tmp_path / "pad.mp4"), faces=[], detector=True,
                        burned_subs=True, credit="Video: Test")
    assert r["reframe"]["mode"] == "pad" and r["burned_subs"] is True
    from PIL import Image
    fr = tmp_path / "p.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "1.5", "-i", r["path"], "-frames:v", "1", str(fr)], check=True)
    im = Image.open(fr).convert("L")
    # the source's subtitle band (bottom 16%) is cropped before the frame is fitted: no white subtitle text under the video
    W, H = im.size
    fitted_h = int(W * 1080 * 0.84 / 1920)
    band = im.crop((200, (H + fitted_h) // 2 - 40, W - 200, (H + fitted_h) // 2 - 2))
    assert max(band.getdata()) < 235


def test_rights_gate_and_credit_line():
    out = L.split_video("x.mp4", out_dir="/tmp/never", credit="Video: someone on YouTube")
    assert out["needs_confirmation"] and "right to repost" in out["question"]
    assert "credit line" in L.split_video("x.mp4", out_dir="/tmp/never", rights_confirmed=True)["error"]
