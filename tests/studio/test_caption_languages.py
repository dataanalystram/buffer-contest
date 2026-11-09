"""Captions in the video's own language (9 Nov 2026).

* subtitles.choose_track picks the track in the language the video is SPOKEN in, never an auto-translated one.
* subtitles.snap_to_timing puts the creator's words on the automatic track's per-word times.
* fontcover + the three caption builders swap to a Noto font when the style's font can't draw the words.
"""
import pytest

from studio import take
from studio.media import captions, captions_ass, fontcover
from studio.media import subtitles as S


def vtt(url):
    return [{"ext": "vtt", "url": url}]


def words(text, start=0.0, step=0.4):
    return [{"w": w, "s": round(start + i * step, 3), "e": round(start + i * step + step * 0.8, 3)}
            for i, w in enumerate(text.split())]


# ------------------------------------------------------------------------------------------------ which track
HINDI_TALK = {"language": "hi", "subtitles": {},
              "automatic_captions": {"en": vtt("https://yt/auto-en"), "fr": vtt("https://yt/auto-fr"),
                                     "hi-orig": vtt("https://yt/auto-hi-orig"), "hi": vtt("https://yt/auto-hi")}}


def test_a_hindi_talk_gets_hindi_captions_not_the_english_translation():
    assert S.choose_track(HINDI_TALK) == {"lang": "hi-orig", "kind": "auto", "url": "https://yt/auto-hi-orig", "ext": "vtt"}


def test_the_creators_own_language_track_beats_their_english_one():
    info = {"language": "hi", "subtitles": {"en": vtt("https://yt/man-en"), "hi": vtt("https://yt/man-hi")},
            "automatic_captions": HINDI_TALK["automatic_captions"]}
    assert S.choose_track(info)["url"] == "https://yt/man-hi"


def test_the_orig_marker_tells_the_language_when_the_platform_doesnt():
    info = {"automatic_captions": {"en": vtt("https://yt/en"), "ta-orig": vtt("https://yt/ta-orig")}}
    assert S.spoken_language(info) == "ta" and S.choose_track(info)["url"] == "https://yt/ta-orig"


def test_only_translations_means_whisper():
    assert S.choose_track({"language": "hi", "automatic_captions": {"en": vtt("https://yt/en")}}) is None
    assert S.choose_track({"language": "hi", "subtitles": {"en": vtt("https://yt/en")}}) is None


def test_english_and_unknown_language_videos_pick_as_before():
    en = {"language": "en", "subtitles": {"en-US": vtt("https://yt/m")}, "automatic_captions": {"en-orig": vtt("https://yt/a")}}
    assert S.choose_track(en)["url"] == "https://yt/m"
    assert S.choose_track({"automatic_captions": {"en": vtt("https://yt/a"), "de": vtt("x")}})["url"] == "https://yt/a"


def test_timing_track_is_the_automatic_one_in_the_same_language():
    info = {"language": "hi", "subtitles": {"hi": vtt("https://yt/man-hi")}, "automatic_captions": HINDI_TALK["automatic_captions"]}
    assert S.timing_track(info, S.choose_track(info))["url"] == "https://yt/auto-hi-orig"
    assert S.timing_track(HINDI_TALK, S.choose_track(HINDI_TALK)) is None  # already automatic


# ------------------------------------------------------------------------------------------------ creator's words, exact timing
def test_creator_words_take_the_automatic_tracks_word_times():
    timed = words("welcome to who bite today we test cloud opus")
    creator = S._spread("Welcome to HuhByte, today we test Claude Opus.".split(), 0.0, 6.0)  # cue-level, drifted
    out, ratio = S.snap_to_timing(creator, timed)
    assert [w["w"] for w in out] == "Welcome to HuhByte, today we test Claude Opus.".split()
    assert ratio >= 0.6
    by = {w["w"]: w for w in out}
    assert by["today"]["s"] == timed[4]["s"] and by["test"]["e"] == timed[6]["e"]           # exact, not spread
    assert timed[2]["s"] <= by["HuhByte,"]["s"] < by["HuhByte,"]["e"] <= timed[3]["e"] + 1e-6  # "who bite" span
    assert all(a["s"] <= b["s"] for a, b in zip(out, out[1:]))


def test_words_only_the_creator_wrote_stay_between_their_neighbours():
    timed = words("we test the app now")
    creator = S._spread("we test the new app now".split(), 0.0, 2.0)
    out, _ = S.snap_to_timing(creator, timed)
    new = next(w for w in out if w["w"] == "new")
    assert timed[2]["e"] <= new["s"] <= timed[3]["s"]


def test_a_different_text_keeps_the_creators_own_times():
    creator = S._spread("completely different words here".split(), 0.0, 2.0)
    out, ratio = S.snap_to_timing(creator, words("nothing in common at all"))
    assert out == creator and ratio < S.SNAP_MIN_MATCH


class _Resp:
    def __init__(self, body):
        self.content = body.encode()


class _Net:
    def __init__(self, body):
        self.body, self.urls = body, []

    def safe_get(self, url, **kw):
        self.urls.append(url)
        return _Resp(self.body)


AUTO_VTT = """WEBVTT

00:00:00.000 --> 00:00:03.000
welcome<00:00:00.400><c> to</c><00:00:00.800><c> who</c><00:00:01.200><c> bite</c><00:00:01.600><c> today</c>
"""


def test_snap_manual_uses_one_more_caption_fetch_and_falls_back_quietly():
    creator = S._spread("Welcome to HuhByte today".split(), 0.0, 3.0)
    net = _Net(AUTO_VTT)
    got = S._snap_manual(creator, {"url": "https://yt/auto"}, net)
    assert net.urls == ["https://yt/auto"] and got["timing"] == "snapped"
    assert [w["w"] for w in got["words"]] == ["Welcome", "to", "HuhByte", "today"]
    assert got["words"][-1]["s"] == pytest.approx(1.6)
    assert S._snap_manual(creator, None, net) == {}
    assert S._snap_manual(creator, {"url": "https://yt/x"}, _Net("WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nno marks\n")) == {}


# ------------------------------------------------------------------------------------------------ fonts
HINDI = "नमस्ते दोस्तों आज हम AI के बारे में बात करेंगे"


@pytest.mark.parametrize("text,family", [
    (HINDI, "Noto Sans Devanagari"), ("வணக்கம் நண்பர்களே", "Noto Sans Tamil"), ("నమస్కారం మిత్రులారా", "Noto Sans Telugu"),
    ("مرحبا بكم جميعا", "Noto Sans Arabic"), ("שלום לכולם", "Noto Sans Hebrew"), ("สวัสดีครับ", "Noto Sans Thai"),
    ("Привет друзья", "Noto Sans"),
])
def test_the_noto_font_for_each_script_draws_every_letter(text, family):
    fam, file = fontcover.pick(text, "Anton.ttf")
    assert fam == family and fontcover.missing(text, file) == []


def test_latin_captions_keep_the_styles_own_font():
    assert fontcover.pick("Hello friends, today we ship", "Anton.ttf") is None
    assert fontcover.pick("", "Anton.ttf") is None


def test_emoji_and_chinese_are_named_not_swapped_for_nothing():
    assert fontcover.pick("你好 🔥", "Anton.ttf") is None
    assert "你好" in fontcover.note("你好 world", "Anton.ttf")


def _family(ass):
    return ass.split("Style: Cap,", 1)[1].split(",", 1)[0]


@pytest.mark.parametrize("style", list(captions.STYLES))
def test_shorts_caption_styles_draw_hindi_and_still_fit(style):
    ass = captions.build_ass(words(HINDI), style, W=1080, H=1920)
    fam = _family(ass)
    assert fontcover.missing(HINDI, captions.FONTS[fam]) == []          # no empty boxes, whichever font it is
    if fontcover.missing(HINDI, captions.FONTS[captions.STYLES[style].family]):
        assert fam == "Noto Sans Devanagari"                            # swapped only when the style's font can't
    else:
        assert fam == captions.STYLES[style].family                     # Poppins draws Devanagari itself
    assert captions.check_ass_fits(ass, 1080, 1920) == []


def test_shorts_captions_report_characters_no_font_draws():
    ass = captions.build_ass(words("你好 世界 朋友"), "opus", W=1080, H=1920)
    assert any("no bundled font" in p for p in captions.check_ass_fits(ass, 1080, 1920))


def test_english_shorts_captions_are_unchanged():
    ass = captions.build_ass(words("we ship the new captions today"), "opus", W=1080, H=1920)
    assert f"Style: Cap,{captions.STYLES['opus'].family}," in ass


@pytest.mark.parametrize("preset", list(captions_ass.PRESETS))
def test_one_take_caption_presets_switch_font_and_fit_the_frame(preset):
    ass = captions_ass.build(words(HINDI), 0.0, preset=preset)
    fam = _family(ass)
    file = fontcover.NOTO.get(fam) or captions_ass.PRESETS[preset]["file"]
    assert fontcover.missing(HINDI, file) == []
    for b in captions_ass.line_boxes(ass, preset):
        assert b["width"] <= captions_ass.REF_W


def test_classic_take_captions_switch_font():
    assert "Style: Cap,Noto Sans Tamil," in take.captions_ass(words("வணக்கம் நண்பர்களே"), 0.0, 0.0, {}, {})
    assert "Style: Cap,Anton," in take.captions_ass(words("hello friends"), 0.0, 0.0, {}, {})
