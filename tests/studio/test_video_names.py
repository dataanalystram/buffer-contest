"""Each channel uses its own word for a video post (owner, 6 Nov 2026: every chip said "Instagram · Reel",
"YouTube · Reel", "Facebook · Reel", "LinkedIn · Reel"). studio/video_names.py and house.js must agree."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import video_names
from studio.actions import build_post_input, clean_options

HOUSE = Path(__file__).resolve().parents[2] / "studio" / "static" / "house.js"
VIDEO = "https://hq.example/m/a.mp4"

CASES = [  # service, ratio, options, duration, expected
    ("instagram", "9:16", {}, 30, "Reel"),
    ("instagram", "4:5", {"ig_type": "reel"}, 30, "Reel"),
    ("instagram", "9:16", {"ig_type": "story"}, 10, "Story"),
    ("facebook", "9:16", {"fb_type": "reel"}, 30, "Reel"),
    ("facebook", "4:5", {}, 30, "Video"),
    ("facebook", "9:16", {"fb_type": "story"}, 10, "Story"),
    ("youtube", "9:16", {}, 45, "Short"),
    ("youtube", "1:1", {}, 170, "Short"),
    ("youtube", "9:16", {}, 240, "Video"),  # over 3 minutes is a regular video
    ("youtube", "16:9", {}, 30, "Video"),
    ("youtube", "abc", {}, 30, "Video"),  # a ratio we can't read is not called a Short
    ("youtube", "0:0", {}, 30, "Video"),
    ("youtube", "9:16", {}, "181", "Video"),
    ("linkedin", "4:5", {}, 30, "Video"),
    ("linkedin", "9:16", {}, 30, "Video"),
    ("tiktok", "9:16", {}, 30, "Video"),
    ("twitter", "16:9", {}, 30, "Video"),
    ("threads", "4:5", {}, 30, "Video"),
    ("pinterest", "9:16", {}, 30, "Video Pin"),
]


@pytest.mark.parametrize("svc,ratio,opts,dur,want", CASES)
def test_each_network_has_its_own_video_word(svc, ratio, opts, dur, want):
    assert video_names.video_name(svc, ratio, opts, dur) == want


def test_no_channel_but_instagram_calls_every_video_a_reel():
    for svc in ("linkedin", "tiktok", "twitter", "threads", "bluesky", "mastodon", "pinterest", "googlebusiness"):
        assert "Reel" not in video_names.video_name(svc, "9:16")


def test_sentences_read_naturally():
    assert video_names.phrase("instagram", "Reel") == "Instagram Reel"
    assert video_names.phrase("youtube", "Short") == "YouTube Short"
    assert video_names.phrase("linkedin", "Video") == "LinkedIn video"


def test_label_matches_what_buffer_is_sent_for_facebook():
    """The chip says Reel only when Buffer gets metadata.facebook.type = reel."""
    for opts in ({}, {"fb_type": "reel"}, {"fb_type": "post"}):
        sent = build_post_input({"id": "fb", "service": "facebook"}, "hi", VIDEO, "draft", None, None, clean_options(opts))
        label = video_names.video_name("facebook", "9:16", opts)
        assert (sent["metadata"]["facebook"]["type"] == "reel") == (label == "Reel"), opts


def test_facebook_reel_is_9_16_only():
    assert video_names.facebook_reel("9:16") and not video_names.facebook_reel("4:5") and not video_names.facebook_reel("16:9")


def test_house_js_table_matches_python():
    js = HOUSE.read_text(encoding="utf-8")
    match = re.search(r"const VIDEO_NAMES = (\{[^}]*\});", js)
    assert match, "house.js does not contain a VIDEO_NAMES table"
    table = match.group(1)
    as_json = re.sub(r"(\w+):", r'"\1":', table)
    assert json.loads(as_json) == video_names.VIDEO_NAMES


def test_house_js_video_name_agrees_with_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    js = HOUSE.read_text(encoding="utf-8")
    fn = js[js.index("const VIDEO_NAMES"):js.index("window.HQ_VIDEO_NAME")]
    cases = [{"p": {"service": s, "options": o}, "ratio": r, "dur": d} for s, r, o, d, _ in CASES]
    prog = fn + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map((c) => videoName(c.p, c.ratio, c.dur))));"
    got = json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True).stdout)
    assert got == [w for *_, w in CASES]
    # the server's own word wins when the package carries it
    prog = fn + '\nconsole.log(JSON.stringify(videoName({service: "youtube", video_name: "Video"}, "9:16", 20)));'
    assert json.loads(subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True).stdout) == "Video"


def test_review_chips_use_the_network_word_not_a_fixed_reel():
    js = HOUSE.read_text(encoding="utf-8")
    fmt = js[js.index("const fmtLine"):js.index("function pkgTabs")]
    assert '"Reel"' not in fmt and "videoName(" in fmt
    ml = js[js.index("function mediaLabel"):js.index("window.HQ_MEDIA_LABEL")]
    assert '["Video",' not in ml and "videoName(" in ml
