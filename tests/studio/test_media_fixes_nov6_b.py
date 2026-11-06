"""Fixes from the real-video One take test (6 Nov 2026), part 2: a screen recording gets a readable crop of its active
region instead of a tiny full-width strip, and captions on a light background get a heavier stroke and shadow."""
import re

import numpy as np
import pytest

from studio.media import captions_ass
from studio.media.reframe import render as rr, screen

cv2 = pytest.importorskip("cv2")


def _screen_thumbs(active_x=(200, 300), moving=True):
    """Three 320x173 grey 'screens': a flat light page, a toolbar line, dense text in one column that changes."""
    out = []
    for k in range(3):
        g = np.full((173, 320), 235, np.uint8)
        g[20:24, 10:310] = 40                                    # a toolbar line across the whole width
        for y in range(40, 160, 8):
            g[y:y + 2, active_x[0]:active_x[1]] = 30              # dense "text" in the active column
        if moving:
            g[60 + 10 * k:70 + 10 * k, active_x[0] + 10:active_x[0] + 60] = 0  # typing / scrolling there
        out.append(g)
    return out


# ---------------------------------------------------------------- 9. screen recordings
def test_focus_box_crops_the_active_region_at_a_readable_size():
    box = screen.focus_box(_screen_thumbs(), 1376, 744)
    assert box is not None
    x, y, w, h = box
    assert 1064 / w >= 1.4                                         # UI shown at >= 1.4x (fit shows it at 0.77x)
    assert x <= 200 / 320 * 1376 and x + w >= 300 / 320 * 1376     # covers the active column
    assert 0 <= y and y + h <= 744 and x + w <= 1376 and w % 2 == 0 and h % 2 == 0
    # a thin full-height line (a scrollbar) at the edge doesn't pull the crop off the content
    bar = [g.copy() for g in _screen_thumbs()]
    for g in bar:
        g[:, 316:318] = 60
    bx, _, bw, _ = screen.focus_box(bar, 1376, 744)
    assert bx <= 200 / 320 * 1376 and bx + bw >= 300 / 320 * 1376
    assert screen.focus_box([], 1376, 744) is None
    assert screen.focus_box(_screen_thumbs(), 700, 400) is None     # a small screen: nearly all of it, plain fit


def test_only_unpinned_r2_screen_shots_get_a_focus_and_the_graph_crops_it(monkeypatch):
    thumbs = {t: g for t, g in zip((1.0, 2.0, 3.0), _screen_thumbs())}
    shots = [{"start": 0.0, "end": 4.0, "layout": "fit", "rule": "R2", "bg": "blur"},
             {"start": 0.0, "end": 4.0, "layout": "fit", "rule": "R2", "bg": "blur", "pinned": True},
             {"start": 0.0, "end": 4.0, "layout": "fit", "rule": "R8", "bg": "blur"}]
    assert rr.apply_focus(shots, thumbs, 1376, 744) == 1
    assert shots[0].get("focus") and "focus" not in shots[1] and "focus" not in shots[2]
    x, y, w, h = shots[0]["focus"]
    monkeypatch.setattr(rr, "mask", lambda kind, w_, h_, r=40, color=None: f"/tmp/{kind}_{w_}x{h_}.png")  # graph text only
    chain = rr.seg_graph(rr.Graph(), 0, shots[0], (1376, 744), 3, None, "#C9ACFA", "#0B0B12")
    assert f"[f0]crop={w}:{h}:{x}:{y}," in chain and f"overlay=(W-w)/2:{rr.FOCUS_TOP}" in chain
    plain = rr.seg_graph(rr.Graph(), 1, shots[2], (1376, 744), 3, None, "#C9ACFA", "#0B0B12")
    assert "[f1]crop=" not in plain and "overlay=(W-w)/2:(H-h)/2" in plain
    for bad in ([0, 0, 5000, 10], "x", [1.5, 0, 100, 100], [-2, 0, 100, 100]):  # a bad focus is ignored, never rendered
        assert "[f2]crop=" not in rr.seg_graph(rr.Graph(), 2, {**shots[2], "focus": bad}, (1376, 744), 3, None, "#C9ACFA", "#0B0B12")


# ---------------------------------------------------------------- 10. caption contrast
def _style(ass):
    line = next(x for x in ass.splitlines() if x.startswith("Style: Cap,"))
    f = line.split(",")
    return {"primary": f[3], "outline_c": f[5], "back": f[6], "outline": int(f[16]), "shadow": int(f[17])}


WORDS = [{"w": "another", "s": 1.0, "e": 1.3}, {"w": "page,", "s": 1.35, "e": 1.7}, {"w": "but", "s": 2.0, "e": 2.2}]


def test_light_backgrounds_get_a_heavy_stroke_and_the_lavender_stays():
    dark = _style(captions_ass.build(WORDS, 0.0, bg_luma=0.12))
    light = _style(captions_ass.build(WORDS, 0.0, bg_luma=0.86))
    default = _style(captions_ass.build(WORDS, 0.0))
    assert dark == default                                            # dark footage: unchanged
    assert light["outline"] >= captions_ass.STROKE_MIN and light["shadow"] >= captions_ass.SHADOW_MIN
    assert light["outline"] > default["outline"] and light["outline_c"] == "&H00100E0E"
    assert light["primary"] == default["primary"] == "&H00FAACC9"    # lavender #C9ACFA kept
    assert captions_ass.contrast("#C9ACFA", 0.86) < captions_ass.MIN_CONTRAST < captions_ass.contrast("#C9ACFA", 0.1)
    assert not captions_ass.needs_stroke(["#FFFFFF"], None) and not captions_ass.needs_stroke(["#FFFFFF"], True)
    ass = captions_ass.build(WORDS, 0.0, bg_luma=0.86)
    assert not re.search(r"gradient|\\\\[1-4]?va\(", ass, re.I)       # solid colours only, no gradients


def test_caption_background_is_measured_under_the_band():
    white = {1.0: np.full((180, 320), 240, np.uint8)}
    black = {1.0: np.full((180, 320), 10, np.uint8)}
    track = [{"start": 0.0, "end": 4.0, "layout": "track", "path": {"crop_w": 608, "crop_h": 1080, "keys": [(0.0, 600, 0)]}}]
    fit = [{"start": 0.0, "end": 4.0, "layout": "fit"}]
    assert rr.caption_bg_luma(track, white, 1920, 1080) > 0.85 and rr.caption_bg_luma(track, black, 1920, 1080) < 0.1
    assert 0.75 < rr.caption_bg_luma(fit, white, 1920, 1080) < 0.9   # the blurred background is darkened a little
    assert rr.caption_bg_luma(fit, {}, 1920, 1080) is None
    half = np.full((180, 320), 10, np.uint8)
    half[110:140, :] = 245                                           # light only where the track caption sits
    assert rr.caption_bg_luma(track, {1.0: half}, 1920, 1080) > 0.8
