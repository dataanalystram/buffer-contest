"""Caption styles (studio/media/captions.py) on the real Aarush launch transcript: every chunk fits every frame."""
import json
from pathlib import Path

import pytest

from studio.media import captions

WORDS = json.loads((Path(__file__).parent / "fixtures/media/aarush_words_tiny.json").read_text())["words"]


def test_fix_names_repairs_a_misheard_product_name():
    ws = [{"w": "So", "s": 0.0, "e": 0.2}, {"w": "vocal", "s": 0.2, "e": 0.5}, {"w": "detects", "s": 0.5, "e": 0.9}]
    out = captions.fix_names(ws, ["Subvocal"])
    assert [w["w"] for w in out] == ["Subvocal", "detects"] and out[0]["s"] == 0.0 and out[0]["e"] == 0.5
    assert captions.fix_names(ws, None) == ws


def test_chunks_respect_word_and_char_limits():
    for ch in captions.chunk_words(WORDS, 3, 16):
        assert 1 <= len(ch) <= 3


@pytest.mark.parametrize("style", list(captions.STYLES))
@pytest.mark.parametrize("size", [(1080, 1920), (1080, 1350), (1080, 1080)])
def test_every_style_fits_every_ratio(style, size):
    W, H = size
    ass = captions.build_ass(captions.fix_names(WORDS, ["Subvocal"]), style, W=W, H=H)
    assert "WrapStyle: 2" in ass  # libass never re-wraps our lines
    assert ass.count("Dialogue:") >= len(WORDS) * 0.9
    assert captions.check_ass_fits(ass, W, H) == []


def test_a_huge_word_shrinks_instead_of_running_off_frame():
    ws = [{"w": "Theoystication-hyperinterconnectedness", "s": 1.0, "e": 2.0}]
    for style in captions.STYLES:
        ass = captions.build_ass(ws, style, W=1080, H=1920)
        assert captions.check_ass_fits(ass, 1080, 1920) == []


def test_times_are_clipped_to_the_section_end():
    ass = captions.build_ass(WORDS, "news", end=5.0)
    for ln in ass.splitlines():
        if ln.startswith("Dialogue:"):
            h, m, s = ln.split(",")[2].split(":")
            assert int(h) * 3600 + int(m) * 60 + float(s) <= 5.0 + 0.07
