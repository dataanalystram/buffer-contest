"""Live E2E, 3 Nov 2026: a custom template's stat slide ({type: stat, title: "{amount}"}) rendered the slide number "02"
as the big figure and the amount small under it (stat went through the numbered story card). The figure is the big text."""
from __future__ import annotations

from studio import brand_examples as E
from studio import custom_templates as C
from studio import templates as T


def _brand():
    return T.Brand(E.EXAMPLES["huhbyte"]["kit"], E.logo_bytes("huhbyte"))


def test_stat_slide_draws_the_figure_not_the_slide_number(monkeypatch):
    calls = []
    real = T.story_slide

    def spy(*a, **k):
        calls.append((a, k))
        return real(*a, **k)
    monkeypatch.setattr(T, "story_slide", spy)
    spec = {"name": "funding", "slides": [{"type": "stat", "title": "{amount}", "body": "Pre-seed led by {investors}"},
                                           {"type": "story", "title": "Why", "body": "Because"}]}
    ims = C.render_slides(spec, {"amount": "$1.3M", "investors": "YC"}, _brand(), "4:5")
    assert len(ims) == 3  # stat, story, the Follow end card
    (sa, sk), (ta, tk) = calls
    assert sk["big"] == "$1.3M" and sa[3] == "Pre-seed led by YC"
    assert not tk.get("big")  # a story card keeps its number


def test_a_long_figure_shrinks_to_fit():
    for ratio in ("4:5", "9:16", "16:9", "1:1"):
        im = T.story_slide(_brand(), ratio, 2, "raised", "", big="$1,300,000,000")
        assert im.size == T.SIZES[ratio] if hasattr(T, "SIZES") else im.size[0] > 0
