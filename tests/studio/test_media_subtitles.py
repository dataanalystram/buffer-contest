"""Transcript reuse (studio/media/subtitles.py): existing captions beat Whisper, every branch tested."""
import shutil

import pytest

from studio.media import subtitles as S

SRT = """1
00:00:01,000 --> 00:00:03,000
Hello, I'm Fran Chiaramonte.

2
00:00:03,500 --> 00:00:06,000
Today: the Light Microscopy Module.
"""

# yt-dlp's SRT of YouTube auto-captions: the timestamp line, a blank line, then text; 10 ms "hold" cues; rolling lines
ROLLING_SRT = """1
00:00:00,320 --> 00:00:03,750

Welcome again everyone. This is our uh

2
00:00:03,750 --> 00:00:03,760
Welcome again everyone. This is our uh
 

3
00:00:03,760 --> 00:00:07,349
Welcome again everyone. This is our uh
very first Buffer commit live Q&A event.
"""

YT_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.320 --> 00:00:03.750 align:start position:0%
 
Welcome<00:00:00.880><c> again</c><00:00:01.360><c> everyone.</c>

00:00:03.750 --> 00:00:03.760 align:start position:0%
Welcome again everyone.
 

00:00:03.760 --> 00:00:07.349 align:start position:0%
Welcome again everyone.
This<00:00:04.200><c> is</c><00:00:04.400><c> Buffer</c><00:00:05.000><c> Commit.</c>
"""

NASA_VTT = """WEBVTT

00:00:00.506 --> 00:00:06.546 A:middle
[ Sound Effects ]

00:00:07.046 --> 00:00:09.886 A:middle
Hello. I'm Fran Chiaramonte,
Program Scientist

00:00:09.886 --> 00:00:14.636 A:middle
for Physical Sciences aboard
the International Space Station.
"""


def text(words):
    return " ".join(w["w"] for w in words)


def test_srt_cues_become_timed_words_inside_each_cue():
    w = S.parse_captions(SRT)
    assert text(w) == "Hello, I'm Fran Chiaramonte. Today: the Light Microscopy Module."
    assert w[0]["s"] == 1.0 and all(1.0 <= x["s"] < x["e"] <= 3.0 for x in w[:4])
    assert w[4]["s"] == 3.5 and w[-1]["e"] <= 6.0
    assert all(a["s"] <= b["s"] for a, b in zip(w, w[1:]))


def test_rolling_auto_captions_are_stitched_without_repeats_and_keep_their_real_start():
    w = S.parse_captions(ROLLING_SRT)
    assert text(w) == "Welcome again everyone. This is our uh very first Buffer commit live Q&A event."
    assert w[0]["s"] == pytest.approx(0.32)                      # the first line was said in the first cue, not the third
    assert next(x for x in w if x["w"] == "very")["s"] >= 3.76


def test_youtube_vtt_inline_word_timings_are_exact_and_repeat_lines_skipped():
    w = S.parse_captions(YT_VTT)
    assert text(w) == "Welcome again everyone. This is Buffer Commit."
    assert [x["s"] for x in w[:3]] == [pytest.approx(0.32), pytest.approx(0.88), pytest.approx(1.36)]
    assert next(x for x in w if x["w"] == "Buffer")["s"] == pytest.approx(4.4)


def test_vtt_cue_settings_and_sound_tags_are_ignored():
    w = S.parse_captions(NASA_VTT)
    assert text(w).startswith("Hello. I'm Fran Chiaramonte, Program Scientist for Physical Sciences")
    assert "Sound" not in text(w) and w[0]["s"] == pytest.approx(7.046)
    assert S.has_punctuation(w + w + w)


def test_garbage_and_oversized_captions_are_refused_before_parsing():
    with pytest.raises(S.CaptionError):
        S.parse_captions("not captions at all")
    with pytest.raises(S.CaptionError, match="too large"):
        S.parse_captions("x" * (S.MAX_CAPTION_BYTES + 1))


def test_choose_track_prefers_the_creators_captions_over_automatic_ones():
    info = {"subtitles": {"en": [{"ext": "vtt", "url": "https://yt/manual"}]},
            "automatic_captions": {"en": [{"ext": "vtt", "url": "https://yt/auto"}], "de": [{"ext": "vtt", "url": "x"}]}}
    assert S.choose_track(info) == {"lang": "en", "kind": "manual", "url": "https://yt/manual", "ext": "vtt"}
    assert S.choose_track({"automatic_captions": info["automatic_captions"]})["kind"] == "auto"
    assert S.choose_track({"subtitles": {"fr": [{"ext": "vtt", "url": "u"}]}}) is None   # no English track: Whisper
    assert S.choose_track({}) is None


def test_non_platform_links_never_reach_yt_dlp():
    assert S.platform_captions("https://example.com/talk.mp4") is None


# ------------------------------------------------------------------ the decision: uploaded > platform > embedded > whisper
def _never(*a, **k):
    raise AssertionError("must not be called")


def test_uploaded_captions_win_and_whisper_never_runs():
    tr = S.resolve_transcript("v.mp4", url="https://youtu.be/x", caption_text=SRT, caption_name="talk.srt",
                              platform=_never, embedded=_never, transcribe=_never)
    assert tr["source"] == "uploaded" and "talk.srt" in tr["detail"] and tr["count"] == 9


def test_platform_captions_are_used_for_a_video_link():
    plat = lambda url, lang: {"words": S.parse_captions(YT_VTT), "kind": "auto", "lang": "en", "platform": "youtube"}  # noqa: E731
    tr = S.resolve_transcript("v.mp4", url="https://youtu.be/x", platform=plat, embedded=_never, transcribe=_never)
    assert tr["source"] == "platform" and tr["kind"] == "auto" and "automatic captions on youtube" == tr["detail"]


def test_embedded_track_then_whisper_only_when_nothing_exists():
    tr = S.resolve_transcript("v.mp4", url="https://youtu.be/x", platform=lambda u, l: None,
                              embedded=lambda p: S.parse_captions(SRT), transcribe=_never)
    assert tr["source"] == "embedded"
    calls = []

    def whisper(path, names):
        calls.append((path, names))
        return {"words": [{"w": "hi.", "s": 0, "e": 0.4}], "model": "tiny", "language": "en"}
    tr = S.resolve_transcript("v.mp4", url="https://youtu.be/x", platform=lambda u, l: None, embedded=lambda p: None,
                              transcribe=whisper, names=["Buffer"])
    assert tr["source"] == "whisper" and calls == [("v.mp4", ["Buffer"])] and tr["detail"] == "Whisper tiny"
    assert S.resolve_transcript("v.mp4", embedded=lambda p: None, transcribe=False)["source"] == "none"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_a_caption_stream_inside_an_mp4_is_extracted(tmp_path):
    import subprocess
    srt = tmp_path / "s.srt"
    srt.write_text(SRT)
    mp4 = tmp_path / "v.mp4"
    # built with plain ffmpeg (the app's hardened wrapper refuses .srt inputs on purpose)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=6", "-i", str(srt),
                    "-c:v", "libx264", "-c:s", "mov_text", "-t", "6", str(mp4)], check=True)
    w = S.embedded_captions(str(mp4))
    assert w and text(w).startswith("Hello, I'm Fran")
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=320x180:d=2", "-c:v", "libx264", str(plain)],
                   check=True)
    assert S.embedded_captions(str(plain)) is None
