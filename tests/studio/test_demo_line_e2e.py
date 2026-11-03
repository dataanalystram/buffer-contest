"""The view-only demo answers "Add and use" on a sample brand kit and "Disconnect and delete my data" with
"In the real app this would ..." on screen, at once, instead of doing nothing (found in the 3 Nov 2026 live check: the
kit add waited behind the template-preview burst and Disconnect swallowed its 403). The demo is unchanged afterwards.
The browser test skips if Chromium can't start."""
import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"


def _kits(store, uid):
    return sorted(r["name"] for r in store.q("SELECT name FROM brand_kit_library WHERE user_id=?", (uid,)))


def test_server_says_view_only_and_names_the_kit_add(client):
    client.get("/demo")
    me = client.get("/api/session").json()
    assert me["demo"] and me["view_only"] is True
    h = {"X-HQ-CSRF": me["csrf"]}
    r = client.post("/api/brand-kits", json={"sample": "linear"}, headers=h)
    assert r.status_code == 403 and r.json()["view_only"]
    assert r.json()["error"].startswith("In the real app this would add this brand kit")
    d = client.post("/api/disconnect", headers=h)
    assert d.status_code == 403 and "In the real app this would disconnect Buffer" in d.json()["error"]
    assert client.get("/api/session").json()["demo"]  # still signed in to the same demo


@pytest.fixture
def live(tmp_path):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="d" * 64,
                      db_path=str(tmp_path / "demo-line.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", fake, app
    server.should_exit = True


def test_demo_add_sample_kit_and_disconnect_show_the_real_app_line(live, chromium):
    pw = pytest.importorskip("playwright.sync_api")
    base, fake, app = live
    errors, posts = [], []
    try:
        ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(NO_TOUR)
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/demo")
        page.get_by_test_id("tab-brand").wait_for()
        sid = next(c["value"] for c in ctx.cookies() if c["name"] == "hq_sid")
        uid = app.state.store.session(sid)["user_id"]
        page.get_by_test_id("tab-brand").click()
        page.get_by_test_id("brand-tab-templates").click()
        add = page.locator("[data-testid^=tpl-kit-add-][data-sample]").first
        add.wait_for()
        name = add.locator("b").first.text_content()
        before = _kits(app.state.store, uid)
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith(("/api/brand-kits", "/api/disconnect")) else None)
        add.click()
        line = page.get_by_test_id("toast")
        pw.expect(line).to_contain_text(f"In the real app this would add the {name} brand kit", timeout=2000)
        pw.expect(add).to_be_visible()  # still offered: nothing was added
        assert _kits(app.state.store, uid) == before

        page.get_by_test_id("tab-settings").click()
        page.get_by_test_id("settings-disconnect").click()
        page.get_by_role("button", name="Disconnect", exact=True).click()
        pw.expect(line).to_contain_text("In the real app this would disconnect Buffer", timeout=2000)
        assert "demo=1" in page.url  # still in the demo, not sent to the landing page
        assert app.state.store.q("SELECT id FROM users WHERE id=?", (uid,))  # the demo user still exists
        assert posts == []  # answered on the page; nothing was sent
        assert not errors, errors
        assert not fake.calls
    finally:
        for c in list(chromium.contexts):
            c.close()
