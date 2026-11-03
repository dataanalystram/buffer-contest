"""Live E2E, 3 Nov 2026: edit_video(cover={title: "Screen Studio: polished demos in one click"}) rendered the cover as
"SCREEN STUDIO POLISHED DEMOS ONE": the 5-word auto-shortener (stopwords out) ran on a title the client wrote itself.
A client-written cover title (up to ~10 words, the tool's own contract) is kept as written; auto hooks still shorten."""
from __future__ import annotations

import inspect

from PIL import Image, ImageDraw

from studio import mcp_client_brain
from studio.media import covers, worker

TITLE = "Screen Studio: polished demos in one click"


def _frame(color, w=1280, h=720):
    im = Image.new("RGB", (w, h), color)
    d = ImageDraw.Draw(im)
    for i in range(0, w, 80):
        d.rectangle((i, h // 3, i + 40, h // 3 + 200), fill=(250 - i % 200, 120, 40 + i % 180))
    d.ellipse((w // 2 - 120, h // 2 - 120, w // 2 + 120, h // 2 + 120), fill=(30, 200, 220))
    return im


def test_client_title_is_kept_as_written_and_fits():
    brand = {"accent": "#C9ACFA", "bg": "#111114", "text": "#FFFFFF", "font": "anton"}
    r = covers.make_covers(frames=[(1.0, _frame((90, 60, 140))), (2.0, _frame((20, 110, 90)))], hook=TITLE, brand=brand,
                           as_written=True)
    assert r["words"] == ["Screen", "Studio:", "polished", "demos", "in", "one", "click"] and r["key"] == 6
    assert len(r["variants"]) == 3
    for x in r["variants"]:
        for ratio, info in x["info"].items():
            assert info["inside_safe"] and info["fit_ok"], (x["layout"], ratio, info)


def test_auto_hook_still_shortens_and_overlong_title_falls_back():
    assert covers.hook_words(TITLE)[0] == ["Screen", "Studio", "polished", "demos", "one"]  # default unchanged
    assert len(covers.hook_words(" ".join(["word"] * 14), as_written=True)[0]) <= 5
    assert covers.hook_words("Raised $4M seed today", as_written=True) == (["Raised", "$4M", "seed", "today"], 1)


def test_edit_video_and_worker_pass_the_flag():
    assert 'spec["cover_title_given"]' in inspect.getsource(mcp_client_brain)
    assert 'as_written=bool(spec.get("cover_title_given"))' in inspect.getsource(worker)
