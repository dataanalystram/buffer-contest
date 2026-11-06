"""Make a post: the AI allowance notice has a dismiss (×). It stays hidden for that user for the rest of the local day on the
same sign-in, and shows again on a new day or after a fresh sign-in (/api/session signed_in_at changes with the session)."""
import time

import httpx
import pytest

from studio import budget
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"


def test_session_says_when_this_sign_in_began(app, human):
    from fastapi.testclient import TestClient
    s = human.get("/api/session").json()
    assert s["connected"] and isinstance(s["signed_in_at"], int) and s["signed_in_at"] > 1_600_000_000_000
    with TestClient(app) as anon:   # no session cookie: signed out, nothing to report
        assert anon.get("/api/session").json()["signed_in_at"] is None


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="d" * 64,
                      db_path=str(tmp_path / "dismiss.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-one", "csrf-one")
    st.update_session("sid-one", user_id="acc_1")
    budget.record(st, "acc_1", 0.80)  # 80% used: the notice shows on Make a post
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True


@pytest.mark.parametrize("width", [390, 1280])
def test_dismiss_hides_the_notice_until_a_new_day_or_a_fresh_sign_in(live, chromium, width):
    pytest.importorskip("playwright.sync_api")
    base, app = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900})
    errors = []
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-one", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(base + "/?tab=agent")
        box, x = page.get_by_test_id("agent-meter"), page.get_by_test_id("agent-meter-dismiss")
        x.wait_for()
        assert "Keep going free in Claude" in box.inner_text()
        bb = x.bounding_box()
        assert bb["width"] >= 32 and bb["height"] >= 32 and bb["x"] + bb["width"] <= width   # a real target, on screen
        assert x.get_attribute("aria-label")
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        x.click()
        assert box.is_hidden()
        key = page.evaluate("Object.keys(localStorage).find((k) => k.startsWith('hq_meter_dismissed:'))")
        assert key == "hq_meter_dismissed:acc_1"
        page.reload()
        page.get_by_test_id("agent-chips").wait_for(state="attached")
        page.wait_for_timeout(2500)  # the meter refresh runs ~1.5 s after load
        assert box.is_hidden()       # same day, same sign-in: still dismissed
        # a new day: yesterday's mark no longer hides it (and is removed)
        page.evaluate("(k) => localStorage.setItem(k, '2000-01-01|' + localStorage.getItem(k).split('|')[1])", key)
        page.reload()
        x.wait_for()
        assert page.evaluate("(k) => localStorage.getItem(k)", key) is None
        x.click()
        assert box.is_hidden()
        # a fresh sign-in (a new session row, as the OAuth callback makes): the notice is back
        st = app.state.store
        time.sleep(0.01)
        st.new_session("sid-two", "csrf-two")
        st.update_session("sid-two", user_id="acc_1")
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-two", "url": base}])
        page.reload()
        x.wait_for()
        assert box.is_visible()
    finally:
        ctx.close()
    assert not errors, errors
