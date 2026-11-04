"""The fast cut-out (studio/hero_edit.py), docs/plans/AGENT_RUNS_EDITING_COMMERCE.md 2.1-2.2 and 2.7.

Regression (owner report, 4 Nov 2026, the CMF / Soundcore case): off-white earcups on a white stage came out torn (the
near-white key at threshold 70 erased them), only one cushion was kept and the hero zoomed into it."""
import io
import random

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from studio import hero_edit as he

PRODUCT = (120, 60, 880, 900)  # where the headphones are drawn (x0, y0, x1, y1)


def headphones() -> Image.Image:
    """The plan's 2.7 repro: dark headband, off-white earcups, light-grey cushions on a near-white stage."""
    im = Image.new("RGB", (1000, 1000), (250, 250, 250))
    d = ImageDraw.Draw(im)
    d.arc((180, 60, 820, 700), 180, 360, fill=(70, 70, 72), width=40)            # dark headband
    for x in (120, 640):
        d.rounded_rectangle((x, 330, x + 240, 900), 90, fill=(236, 234, 230))      # off-white earcup
        d.rounded_rectangle((x + 35, 380, x + 205, 850), 70, fill=(200, 198, 193)) # light-grey cushion
        d.rectangle((x + 105, 300, x + 135, 340), fill=(70, 70, 72))
    return im


def headphones_photo() -> Image.Image:
    """The same product as a real shot would come: anti-aliased, a soft floor shadow, a gentle light fall-off across
    the stage, sensor noise, JPEG."""
    S, W = 2, 2000
    bg = Image.linear_gradient("L").resize((W, W)).point(lambda v: 252 - v * 8 // 255)
    im = Image.merge("RGB", (bg, bg, bg))
    sh = Image.new("L", (W, W), 0)
    ImageDraw.Draw(sh).ellipse((150 * S, 870 * S, 850 * S, 950 * S), fill=90)
    im.paste((120, 120, 125), (0, 0), sh.filter(ImageFilter.GaussianBlur(30 * S)))
    d = ImageDraw.Draw(im)
    d.arc((180 * S, 60 * S, 820 * S, 700 * S), 180, 360, fill=(70, 70, 72), width=40 * S)
    for x in (120 * S, 640 * S):
        d.rounded_rectangle((x, 330 * S, x + 240 * S, 900 * S), 90 * S, fill=(240, 238, 234))
        d.rounded_rectangle((x + 35 * S, 380 * S, x + 205 * S, 850 * S), 70 * S, fill=(205, 203, 198))
        d.rectangle((x + 105 * S, 300 * S, x + 135 * S, 340 * S), fill=(70, 70, 72))
    im = im.resize((1000, 1000), Image.LANCZOS)
    random.seed(7)
    noise = Image.effect_noise((1000, 1000), 6)
    im = ImageChops.add(im, Image.merge("RGB", (noise,) * 3), scale=1.0, offset=-128)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return Image.open(io.BytesIO(b.getvalue())).convert("RGB")


def _alpha(r: dict, x: int, y: int) -> int:
    bx = r["box"]
    return r["image"].getchannel("A").getpixel((x - bx[0], y - bx[1]))


# points inside the drawn earcups: the off-white rim (left, right, top, bottom of each cup) and the cushion
EARCUP_POINTS = [(x + dx, y) for x in (120, 640) for dx, y in ((12, 600), (228, 600), (120, 345), (120, 885), (120, 600))]


def test_offwhite_earcups_survive_on_a_white_stage():
    r = he.cutout_detailed(headphones())
    assert r["ok"] and r["engine"] == "fast" and r["confidence"] >= 0.9, r
    assert r["components"] == 1  # one object: headband, both cups and cushions together, nothing dropped
    for x, y in EARCUP_POINTS:
        assert _alpha(r, x, y) >= 0.95 * 255, (x, y, _alpha(r, x, y))
    assert _alpha(r, 500, 80) >= 242  # the headband
    assert _alpha(r, 500, 600) == 0 and _alpha(r, 500, 880) == 0  # the stage between the cups is cut away


def test_box_covers_the_whole_product_within_5_percent():
    r = he.cutout_detailed(headphones())
    x0, y0, x1, y1 = r["box"]
    w, h = PRODUCT[2] - PRODUCT[0], PRODUCT[3] - PRODUCT[1]
    tol_x, tol_y = 0.05 * w, 0.05 * h
    assert abs(x0 - PRODUCT[0]) <= tol_x and abs(x1 - PRODUCT[2]) <= tol_x, r["box"]
    assert abs(y0 - PRODUCT[1]) <= tol_y and abs(y1 - PRODUCT[3]) <= tol_y, r["box"]
    assert he.cutout(headphones()).size == (x1 - x0, y1 - y0)


def test_old_bug_is_gone_no_single_cushion_crop():
    obj = he.cutout(headphones())
    assert obj is not None and obj.width > 700 and obj.height > 800  # was (178, 478): one cushion, alpha ~0
    assert obj.getchannel("A").getextrema()[1] == 255


def test_real_looking_shot_keeps_the_earcups_or_says_it_is_unsure():
    r = he.cutout_detailed(headphones_photo())
    if r["ok"]:  # a confident cut must be a whole one: never a torn cut-out with a high score
        for x, y in EARCUP_POINTS:
            assert _alpha(r, x, y) >= 0.9 * 255, (x, y, _alpha(r, x, y))
    else:
        assert r["confidence"] < he.MIN_CONFIDENCE and r["reason"]


def test_dark_product_on_white():
    im = Image.new("RGB", (900, 900), (255, 255, 255))
    ImageDraw.Draw(im).rounded_rectangle((300, 150, 600, 760), 40, fill=(18, 18, 20))  # a phone
    r = he.cutout_detailed(im)
    assert r["ok"] and r["components"] == 1
    x0, y0, x1, y1 = r["box"]
    assert abs(x0 - 300) <= 20 and abs(x1 - 600) <= 20 and abs(y0 - 150) <= 20 and abs(y1 - 760) <= 20


def test_text_next_to_the_product_is_left_out():
    im = Image.new("RGB", (1200, 900), (246, 246, 246))
    d = ImageDraw.Draw(im)
    d.ellipse((150, 200, 550, 700), fill=(200, 40, 40))  # the product
    f = ImageFont.truetype("studio/fonts/Figtree-ExtraBold.ttf", 34)
    for i, line in enumerate(("New wireless buds", "40 h battery", "From $79")):
        d.text((700, 380 + i * 50, ), line, font=f, fill=(30, 30, 30))
    r = he.cutout_detailed(im)
    assert r["ok"]
    x0, y0, x1, y1 = r["box"]
    assert x1 < 640 and abs(x0 - 150) <= 20 and abs(y0 - 200) <= 20 and abs(y1 - 700) <= 20, r["box"]


def test_logo_inside_the_product_is_filled_not_a_hole():
    im = Image.new("RGB", (800, 800), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((200, 200, 600, 600), 30, fill=(40, 60, 160))
    d.ellipse((370, 370, 430, 430), fill=(255, 255, 255))  # a small white logo, stage-coloured, inside the object
    r = he.cutout_detailed(im)
    assert r["ok"] and _alpha(r, 400, 400) == 255


def test_busy_photo_is_refused_never_torn():
    random.seed(3)
    im = Image.effect_noise((600, 600), 90).convert("RGB")
    d = ImageDraw.Draw(im)
    for _ in range(60):
        x, y = random.randrange(600), random.randrange(600)
        d.rectangle((x, y, x + 60, y + 40), fill=tuple(random.randrange(256) for _ in range(3)))
    r = he.cutout_detailed(im)
    assert not r["ok"] and r["image"] is None and r["reason"]
    assert he.cutout(im) is None


def test_low_confidence_gives_the_clean_card_not_a_zoomed_crop():
    random.seed(4)
    busy = Image.effect_noise((800, 600), 90).convert("RGB")
    out, rep = he.premium_hero(1080, 1350, product=busy)
    assert out.size == (1080, 1350)
    assert rep["clean_card"] and not rep["cutout"]
    assert any("clean card" in s for s in rep["steps"])
    x0, y0, x1, y1 = rep["product_box"]  # the whole photo is placed inside the canvas
    assert 0 <= x0 and 0 <= y0 and x1 <= 1080 and y1 <= 1350


def test_tall_product_fits_inside_the_canvas():
    im = Image.new("RGB", (1000, 1000), (250, 250, 250))
    ImageDraw.Draw(im).rounded_rectangle((440, 40, 560, 960), 50, fill=(30, 110, 60))  # a tall bottle
    out, rep = he.premium_hero(1080, 1350, product=im)
    assert rep["cutout"]
    x0, y0, x1, y1 = rep["product_box"]
    assert 0 <= x0 and 0 <= y0 and x1 <= 1080 and y1 <= 1350, rep["product_box"]
    p, _ = he.product_shot(Image.new("RGBA", (100, 900), (0, 0, 0, 255)), 600, tilt=0, max_h=500)
    assert p.height <= 502 and p.width <= 600


def test_transparent_png_uses_its_own_alpha():
    im = Image.new("RGBA", (500, 500), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((100, 120, 380, 400), fill=(250, 250, 250, 255))  # white product, transparent ground
    r = he.cutout_detailed(im)
    assert r["ok"] and r["engine"] == "alpha" and r["image"].size == (281, 281)


def test_hero_with_the_regression_product_keeps_it_whole():
    out, rep = he.premium_hero(1080, 1350, product=headphones())
    assert rep["cutout"] and not rep["clean_card"] and rep["cutout_confidence"] >= 0.9
    x0, y0, x1, y1 = rep["product_box"]
    assert (x1 - x0) > 500 and (y1 - y0) > 500 and x1 <= 1080 and y1 <= 1350  # the whole product, not one cushion
