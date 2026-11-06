"""6 Nov 2026 (owner): an AI app's product carousel showed only the first product's picture; later slides were dropped
silently. render_carousel now gives every content slide its own picture (editorial.render pictures=, band the fallback)
and reports each picture it could not use in image_problems, with the reason (House._fetch_bytes_why)."""
from studio import editorial, house

from .conftest import agent_token, image_bytes, mcp_call

COLORS = {"https://shop.example.com/a.jpg": (200, 30, 30), "https://shop.example.com/b.jpg": (30, 200, 30),
          "https://shop.example.com/c.jpg": (30, 30, 200)}
LOCKED = "https://cdn.example.com/locked.jpg"


def _fetch(self, url, limit, https_only=False):
    if url in COLORS:
        return image_bytes(800, 600, "JPEG", COLORS[url]), ""
    return None, "forbidden"


def _slides(urls):
    s = [{"eyebrow": "AUDIO", "headline": "Three Headphones Worth Buying", "accent_word": "Headphones", "body": "Three picks."}]
    for i, u in enumerate(urls):
        s.append({"eyebrow": f"PICK {i + 1}", "headline": f"Pick Number {i + 1} Is Good", "body": "A short reason.", "image": u})
    return s


def test_three_products_three_pictures(human, monkeypatch):
    monkeypatch.setattr(house.House, "_fetch_bytes_why", _fetch)
    seen = {}
    real = editorial.render

    def spy(b, spec, **kw):
        seen.update(kw, flags=[x.get("image") for x in spec["slides"]])
        return real(b, spec, **kw)
    monkeypatch.setattr(editorial, "render", spy)
    tok = agent_token(human, policy="trusted", cap=50)
    res, car = mcp_call(human, tok, "render_carousel", {"slides": _slides(list(COLORS))})
    assert not res["isError"], car
    assert car["template"] == "editorial_story" and car["image_problems"] == []
    pics = seen["pictures"]
    assert len(pics) == 3 and all(p is not None for p in pics) and seen["flags"] == [True, True, True]
    assert [p.getpixel((5, 5)) for p in pics] == [p.getpixel((5, 5)) for p in pics] and \
        len({tuple(p.getpixel((5, 5))) for p in pics}) == 3  # three different pictures, not the first one three times
    assert car["slides"] == 5  # cover + 3 picks + follow card


def test_a_403_picture_is_reported_with_the_reason(human, monkeypatch):
    monkeypatch.setattr(house.House, "_fetch_bytes_why", _fetch)
    tok = agent_token(human, policy="trusted", cap=50)
    urls = ["https://shop.example.com/a.jpg", LOCKED, "https://shop.example.com/c.jpg"]
    res, car = mcp_call(human, tok, "render_carousel", {"slides": _slides(urls)})
    assert not res["isError"], car
    probs = car["image_problems"]
    assert len(probs) == 1 and probs[0]["slide"] == 3 and probs[0]["reason"] == "forbidden" and probs[0]["url"] == LOCKED
    assert "403" in probs[0]["say"] and "Slide 3" in probs[0]["say"]
    # the text_slides path says it too
    res, car = mcp_call(human, tok, "render_carousel", {"slides": _slides(urls), "template": "text_slides"})
    assert not res["isError"], car
    assert [p["reason"] for p in car["image_problems"]] == ["forbidden"]


def test_editorial_render_uses_each_slides_picture_and_band_as_fallback(monkeypatch):
    from PIL import Image

    from studio import templates as T
    got = []
    real = editorial.idea

    def spy(*a, **kw):
        got.append(kw.get("image"))
        return real(*a, **kw)
    monkeypatch.setattr(editorial, "idea", spy)
    red, blue = Image.new("RGB", (400, 300), (220, 20, 20)), Image.new("RGB", (400, 300), (20, 20, 220))
    spec = {"headline": "Cover Headline Here", "slides": [
        {"blocks": [{"head": "One Two", "body": "x"}], "image": True},
        {"blocks": [{"head": "Three Four", "body": "y"}], "image": True},
        {"blocks": [{"head": "Five Six", "body": "z"}], "image": False}], "follow_line": ""}
    out, _ = editorial.render(T.Brand({}), spec, pictures=[red, None, red], band=blue)
    assert len(out) == 5 and got == [red, blue, None]  # own picture, band as the fallback, none when not asked for
