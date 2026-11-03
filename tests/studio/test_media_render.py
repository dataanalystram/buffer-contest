"""Edit styles end to end (studio/media/compose.py) on a synthetic clip: length, audio, layout, no clipping."""
import itertools
import shutil

import pytest
from PIL import Image

from studio.media import compose, ff, textfit

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg")
WORDS = [{"w": w, "s": 0.3 + i * 0.45, "e": 0.3 + i * 0.45 + 0.4} for i, w in
         enumerate("Subvocal glasses decode silent speech into text at two hundred words per minute.".split())]
FACTS = ["Decodes silent mouthing into text at 200+ words per minute", "RF sensing in glasses", "Raised $1.3M"]


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    talk, mute = str(d / "talk.mp4"), str(d / "mute.mp4")
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=5", "-f", "lavfi", "-i",
            "sine=f=330:d=5", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", talk])
    ff.run(["-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=720x1280:r=30:d=4", "-c:v", "libx264", "-preset",
            "ultrafast", "-pix_fmt", "yuv420p", mute])
    return d, talk, mute


def _check_layout(m):
    l, t, r, b = m["safe_area"]
    boxes = {k: v for k, v in m["boxes"].items() if isinstance(v, tuple) and k in
             ("hook", "logo", "credit", "pill", "cap_band", "hook_text")}
    for k, v in boxes.items():
        if k != "credit":  # the credit chip sits on the video panel, which may be full-bleed
            assert textfit.inside(v, (l - 12, t - 12, r + 12, b + 12)), f"{k} {v} outside safe area {m['safe_area']}"
    for (ka, a), (kb, bb) in itertools.combinations(boxes.items(), 2):
        if {ka, kb} in ({"hook", "hook_text"},):
            continue
        assert not textfit.overlaps(a, bb), f"{ka} {a} overlaps {kb} {bb}"
    panel = m["boxes"].get("panel")
    if panel and "cap_band" in boxes:
        assert not textfit.overlaps(panel, boxes["cap_band"])
    assert m["caption_problems"] == []


@pytest.mark.parametrize("style", ["huhbyte_news", "opus", "minimal", "hype"])
def test_each_style_renders_a_complete_branded_short(clips, style, tmp_path):
    d, talk, _ = clips
    m = compose.render_style(talk, style, out=str(tmp_path / f"{style}.mp4"), ratio="9:16", hook="Glasses that let you talk without a sound",
                             facts=FACTS, words=WORDS, credit="Video: Aarush Yadav on X", clip=(0, 4), preset="ultrafast", crf=32,
                             burned_subs=False)
    want = m["cover_secs"] + 4 + m["facts_secs"] + compose.CARD_SECS
    assert (m["width"], m["height"]) == (1080, 1920)
    assert abs(m["duration"] - want) < 0.2, (m["duration"], want)
    assert ff.probe(m["path"])["has_audio"] and not ff.is_silent(m["path"])
    assert m["captions"] is True
    assert "hook" in m["boxes"], "every style shows the hook in the first second"
    _check_layout(m)
    last = str(tmp_path / "last.png")
    ff.run(["-y", "-v", "error", "-sseof", "-0.5", "-i", m["path"], "-frames:v", "1", "-update", "1", last])
    px = Image.open(last).convert("RGB").resize((108, 192)).getdata()
    assert sum(1 for p in px if max(p) < 40) / len(px) > 0.6           # the end card: dark brand ground
    assert any(abs(p[0] - 201) < 30 and abs(p[2] - 250) < 30 for p in px)  # with the lavender @handle


@pytest.mark.parametrize("ratio,size", [("4:5", (1080, 1350)), ("1:1", (1080, 1080))])
def test_feed_ratios(clips, ratio, size, tmp_path):
    _, talk, _ = clips
    m = compose.render_style(talk, "huhbyte_news", out=str(tmp_path / "n.mp4"), ratio=ratio, hook="A very long hook " * 6,
                             words=WORDS, credit="Video: X", clip=(0, 2), preset="ultrafast", crf=32, burned_subs=False)
    assert (m["width"], m["height"]) == size
    _check_layout(m)


def test_silent_demo_stays_silent_by_default_and_vertical_source_fits(clips, tmp_path):
    """Owner, 3 Nov 2026: a piano/cinematic bed was added to his uploaded silent Reel. No music unless it is picked."""
    _, _, mute = clips
    m = compose.render_style(mute, "huhbyte_news", out=str(tmp_path / "m.mp4"), hook="Robot hand demo", credit="Video: Axonnic",
                             preset="ultrafast", crf=32, burned_subs=False)
    assert m["source_silent"] and m["music"] == "none" and ff.is_silent(m["path"])
    _check_layout(m)


def test_silent_demo_gets_the_score_only_when_picked(clips, tmp_path):
    _, _, mute = clips
    m = compose.render_style(mute, "huhbyte_news", out=str(tmp_path / "m2.mp4"), hook="Robot hand demo", credit="Video: Axonnic",
                             preset="ultrafast", crf=32, burned_subs=False, music_mood="cinematic", clip=(0, 2))
    assert m["music"] == "cinematic" and not ff.is_silent(m["path"])


@pytest.mark.parametrize("mood,want", [("auto", "none"), (None, "none"), ("", "none"), ("none", "none"), ("lofi", "none"),
                                       ("cinematic", "cinematic"), ("pulse", "pulse"), ("hype", "pulse")])
def test_picked_music_never_defaults_to_a_score(mood, want):
    assert compose.picked_music(mood) == want


def test_images_only_slideshow(tmp_path):
    for i in range(2):
        Image.new("RGB", (1600, 900), (40 * i, 80, 160)).save(tmp_path / f"i{i}.jpg")
    m = compose.render_style(None, "minimal", out=str(tmp_path / "s.mp4"), hook="No video? Still a short.",
                             images=[str(tmp_path / "i0.jpg"), str(tmp_path / "i1.jpg")], preset="ultrafast", crf=32)
    assert m["duration"] > 6 and m["music"] == "none"


def test_unknown_style_and_ratio_are_refused(clips, tmp_path):
    _, talk, _ = clips
    with pytest.raises(ff.MediaError):
        compose.render_style(talk, "glitter", out=str(tmp_path / "x.mp4"))
    with pytest.raises(ff.MediaError):
        compose.render_style(talk, "opus", out=str(tmp_path / "x.mp4"), ratio="21:9")
