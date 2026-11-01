"""Auto-fit text (studio/media/textfit.py): the largest size that fits, never clipped, never outside its box."""
from PIL import Image, ImageDraw

from studio.media import textfit


def test_fit_picks_largest_size_that_fits_and_stays_inside_the_box():
    r = textfit.fit("Glasses that let you talk to your computer without making a sound", "Anton.ttf", 900, 400,
                    max_size=200, min_size=20, max_lines=4)
    assert r.ok and r.width <= 900 and r.height <= 400 and len(r.lines) <= 4
    bigger = textfit.fit("Glasses that let you talk to your computer without making a sound", "Anton.ttf", 900, 400,
                         max_size=r.size + 1, min_size=r.size + 1, max_lines=4)
    assert not bigger.ok  # one point larger no longer fits


def test_long_single_word_is_broken_not_clipped():
    r = textfit.fit("Supercalifragilisticexpialidocious-AI-Platform", "Anton.ttf", 300, 400, max_size=120, min_size=30)
    assert r.width <= 300


def test_impossible_text_is_ellipsised_inside_the_box():
    r = textfit.fit("word " * 200, "Poppins-Bold.ttf", 400, 120, max_size=40, min_size=30, max_lines=2)
    assert r.meta.get("truncated") and not r.ok and r.lines[-1].endswith("…") and r.height <= 120 and r.width <= 400


def test_draw_fit_ink_stays_in_box():
    im = Image.new("RGB", (1080, 1920))
    d = ImageDraw.Draw(im)
    box = (60, 230, 930, 600)
    r = textfit.fit("We've raised $1.3M from YC & Afore Capital", "Anton.ttf", box[2] - box[0], box[3] - box[1], max_size=140)
    ink = textfit.draw_fit(d, r, box, accent_words={"YC"})
    assert textfit.inside(ink, box)
    assert not textfit.overlaps(ink, (0, 700, 1080, 900))
