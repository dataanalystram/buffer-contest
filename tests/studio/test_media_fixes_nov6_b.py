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


def test_a_product_shot_zooms_in_but_a_busy_wide_shot_stays_full_frame():
    import numpy as np
    from studio.media.reframe import render as rr
    th = []
    for k in range(3):                                   # a phone (striped, moving a little) on a plain table
        g = np.full((180, 320), 90, np.uint8)
        g[50:140, 200 + k:240 + k] = 30
        g[55:135:6, 204 + k:236 + k] = 220
        th.append(g)
    box = screen.focus_box(th, 1920, 1080, min_share=rr.PRODUCT_SHARE)
    assert box and box[0] <= 1200 <= box[0] + box[2]
    rng = np.random.default_rng(1)
    busy = [rng.integers(0, 255, (180, 320), dtype=np.uint8) for _ in range(3)]   # detail everywhere: a landscape
    assert screen.focus_box(busy, 1920, 1080, min_share=rr.PRODUCT_SHARE) is None
    shots = [{"start": 0, "end": 3, "layout": "fit", "rule": "R8-noface"}, {"start": 3, "end": 6, "layout": "fit", "rule": "R8"}]
    thumbs = {t: th[i % 3] for i, t in enumerate((0.75, 1.5, 2.25, 3.75, 4.5, 5.25))}
    rr.apply_focus(shots, thumbs, 1920, 1080)
    assert "focus" in shots[0] and "focus" not in shots[1]   # crowds and low-confidence shots keep the full frame


def test_a_title_card_wider_than_the_crop_stays_full_frame():
    """26 Nov Mac run: the 1.6x screen zoom cut the demo's "MAKE A POST" card to "AKE A PO". Big still text that runs
    past the crop keeps the plain fit; typing inside a crop still zooms even when the code lines run on."""
    card = []
    for _ in range(3):
        g = np.full((173, 320), 20, np.uint8)
        for k in range(9):                                   # nine big "letters" across 80% of the width
            x = 32 + k * 29
            g[60:110, x:x + 6] = 240
            g[60:66, x:x + 20] = 240
            g[104:110, x:x + 20] = 240
        card.append(g)
    assert screen.focus_box(card, 1376, 744) is None
    code = []
    for k in range(3):
        g = np.full((173, 320), 235, np.uint8)
        for y in range(30, 160, 6):
            g[y:y + 2, 10:310] = 30                          # code lines across the whole editor
        g[80:88, 150 + 12 * k:162 + 12 * k] = 0              # the cursor typing in the middle
        code.append(g)
    assert screen.focus_box(code, 1376, 744) is not None


def test_a_side_by_side_comparison_graphic_is_not_cropped_to_one_side():
    """26 Nov Mac run: the review's comparison graphic came out cut off. Two panels of text across the frame keep the
    plain fit when nothing moves inside the crop, on the screen path and on the no-face (product) path."""
    graphic = []
    for k in range(3):
        g = np.full((180, 320), 15, np.uint8)
        for x0 in (20, 175):                                  # two panels, "iPhone 17" | "iPhone 16"
            g[30:150, x0:x0 + 125] = 40
            for y in range(45, 140, 12):
                g[y:y + 4, x0 + 10:x0 + 115] = 230            # rows of specs in each panel
        graphic.append(g)
    assert screen.focus_box(graphic, 1920, 1080) is None
    assert screen.focus_box(graphic, 1920, 1080, min_share=rr.PRODUCT_SHARE) is None


def test_a_title_card_with_no_face_is_never_cut_but_a_product_in_a_room_still_zooms():
    card = []
    for _ in range(3):
        g = np.full((180, 320), 12, np.uint8)
        for k in range(8):                                    # "ONE TAKE": big letters across 70% of a flat backdrop
            x = 50 + k * 28
            g[70:120, x:x + 18] = 235
            g[78:112, x + 5:x + 13] = 12
        card.append(g)
    assert screen.focus_box(card, 1920, 1080, min_share=rr.PRODUCT_SHARE) is None


def test_an_animated_title_card_is_not_cut_even_when_its_letters_move():
    """26 Nov Mac run (third): "ONE TAKE" (45% of the width, letters scaling in) still came out "NE TAK": the title's
    own motion sat inside the crop, so the typing exception let the crop cut it."""
    card = []
    for f in range(3):
        g = np.full((180, 320), 12, np.uint8)
        grow = 2 * f                                          # the letters scale in over the shot
        for k in range(8):                                    # 8 letters across x 88..232 (45% of 320)
            x = 88 + k * 18
            g[80 - grow:112 + grow, x:x + 12] = 235
            g[86:106, x + 4:x + 8] = 12
        card.append(g)
    assert screen.focus_box(card, 1920, 1080, min_share=rr.PRODUCT_SHARE) is None
