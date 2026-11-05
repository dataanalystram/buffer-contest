"""Homepage: Shop-ready posts, powered by Channel3 (owner ask, 5 Nov 2026).

Text-only attribution (Channel3's Publisher Terms grant no logo licence: https://trychannel3.com/terms/publishers, s. 8),
no catalogue images, the commission disclosure next to the mention, and no product search from the public page (no
Channel3 credits spent by anonymous visitors)."""
import re

from studio import commerce


def _section(html: str) -> str:
    m = re.search(r'<section[^>]*id="shop".*?</section>', html, re.S)
    assert m, "the Shop-ready posts section is on the homepage"
    return m.group(0)


def test_shop_section_text_only_attribution_and_disclosure(client):
    sec = _section(client.get("/").text)
    assert "Shop-ready posts, powered by" in sec
    assert re.search(r'<a href="https://trychannel3\.com" rel="noopener noreferrer"[^>]*>Channel3</a>', sec)
    assert "<img" not in sec and "<picture" not in sec and "channel3-logo" not in sec  # no logo, no catalogue image
    assert "#Ad" in sec
    assert "commission" in sec.lower()


def test_homepage_never_searches_channel3(client, monkeypatch):
    calls = []
    monkeypatch.setattr(commerce, "search", lambda *a, **k: calls.append(1), raising=False)
    html = client.get("/").text
    js = client.get(re.search(r'src="(/static/landing\.js[^"]*)"', html).group(1)).text
    for body in (html, js):
        assert "/api/commerce" not in body and "api.trychannel3.com" not in body
    assert not calls
