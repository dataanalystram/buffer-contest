"""UI smoke after the product-search removal (6 Nov 2026): the landing page, Make a post, Settings -> Integrations and /admin
load at 390 and 1280 px with no page or console errors and none of the removed feature's words. Words built at run time."""
import time

import httpx
import pytest

from studio import budget
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
BANNED = ["chan" + "nel3", "shop" + "-ready", "commis" + "sion", "product " + "search"]
PAGES = [  # (path, signed in, selector that says the page is ready)
    ("/", False, "body"),
    ("/?tab=agent", True, "[data-testid=agent-chips]"),
    ("/?tab=settings&sub=integrations", True, "[data-testid=integ-tile-ph]"),
    ("/admin", True, "[data-testid=admin-switch]"),
]


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="e" * 64,
                      db_path=str(tmp_path / "pages.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-p", "csrf-p")
    st.update_session("sid-p", user_id="acc_1")
    budget.record(st, "acc_1", 0.10)
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("path,signed_in,ready", PAGES)
def test_page_loads_clean_without_the_removed_feature(live, chromium, width, path, signed_in, ready):
    base = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900})
    errors = []
    try:
        ctx.add_init_script(NO_TOUR)
        if signed_in:
            ctx.add_cookies([{"name": "hq_sid", "value": "sid-p", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())   # hermetic: nothing leaves the test server
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "net::ERR_FAILED" not in m.text else None)
        resp = page.goto(base + path)
        assert resp and resp.status == 200, (path, resp and resp.status)
        page.wait_for_selector(ready, state="attached", timeout=15000)
        page.wait_for_timeout(1500)   # late loads (meters, lists) get their chance to fail
        html = page.content().lower()
        text = page.inner_text("body").lower()
        for w in BANNED:
            assert w not in html and w not in text, (path, w)
        assert 'id="shop"' not in html and "product-panel" not in html
    finally:
        ctx.close()
    assert not errors, (path, width, errors)
