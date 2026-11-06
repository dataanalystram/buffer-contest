"""Homepage: Shop-ready posts, powered by Channel3 (owner ask, 5 Nov 2026).

Text-only attribution (Channel3's Publisher Terms grant no logo licence: https://trychannel3.com/terms/publishers, s. 8),
no catalogue images, the commission disclosure next to the mention, and no product search from the public page (no
Channel3 credits spent by anonymous visitors)."""
import re

import httpx
import pytest

from studio import commerce
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer


def _section(html: str) -> str:
    m = re.search(r'<section[^>]*id="shop".*?</section>', html, re.S)
    assert m, "the Shop-ready posts section is on the homepage"
    return m.group(0)


def test_shop_section_text_only_attribution_and_disclosure(client):
    sec = _section(client.get("/").text)
    assert "Shop-ready posts, powered by" in sec
    assert re.search(r'<a href="https://trychannel3\.com" rel="noopener noreferrer"[^>]*data-testid="land-shop-channel3"[^>]*>Channel3</a>', sec)
    assert "<img" not in sec and "<picture" not in sec and "channel3-logo" not in sec  # no logo, no catalogue image
    assert "#Ad" in sec
    assert "commission" in sec.lower()


def test_homepage_never_searches_channel3(client, monkeypatch):
    calls = []
    assert commerce.FNS["search_products"] is commerce.search_products

    def record(*a, **k):
        calls.append(1)
    monkeypatch.setattr(commerce, "search_products", record)  # both call paths: the function and its registry binding
    monkeypatch.setitem(commerce.FNS, "search_products", record)
    html = client.get("/").text
    js = client.get(re.search(r'src="(/static/landing\.js[^"]*)"', html).group(1)).text
    for body in (html, js):
        assert "/api/commerce" not in body and "api.trychannel3.com" not in body
    assert not calls


@pytest.fixture
def live(tmp_path):
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="e" * 64,
                      db_path=str(tmp_path / "landing-shop.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


def test_landing_page_in_a_browser_never_asks_channel3(live, chromium, close_contexts, monkeypatch):
    """CodeRabbit (#125): a search added to any landing script (oauth-open.js too) must not slip past the static checks.
    Every request the anonymous page makes is recorded from before it loads, including the ones the hermetic route
    rejects, and none goes to /api/commerce or Channel3."""
    calls = []
    monkeypatch.setattr(commerce, "search_products", lambda *a, **k: calls.append(1))
    monkeypatch.setitem(commerce.FNS, "search_products", lambda *a, **k: calls.append(1))
    base = live
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
    seen, errors = [], []
    ctx.on("request", lambda r: seen.append(r.url))  # fires for aborted requests as well
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic: nothing leaves the test
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/")
    page.locator("#shop").wait_for()
    page.locator("#shop").scroll_into_view_if_needed()
    page.wait_for_load_state("networkidle")
    assert any(u.startswith(base + "/static/") for u in seen), seen  # the recorder saw the page's own assets
    bad = [u for u in seen if "/api/commerce" in u or "channel3" in u.lower()]
    assert not bad, bad
    assert not calls
    assert not errors, errors
