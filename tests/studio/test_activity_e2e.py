"""Browser flow for the Activity log (Playwright): tabs, cursor pages, agent groups by app and model, search, CSV export."""
import asyncio
import json
import os
import threading
import time

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"


def until(fn, timeout=10.0, page=None):
    """Poll a Python check against the page (the app's CSP has no 'unsafe-eval', so no string wait_for_function).
    Pass page= when the check reads only Python state set by a Playwright callback (a route handler): sync Playwright
    dispatches callbacks only while it is called, so a bare time.sleep would never let the handler run."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:  # noqa: BLE001 - the element may be re-rendering
            pass
        if page is not None:
            page.wait_for_timeout(100)
        else:
            time.sleep(0.1)
    raise AssertionError("timed out waiting for the page")


def cats(page):
    # short timeout: a row that re-renders away mid-read (the list swapped to another tab) must fail fast and be re-polled;
    # the default 30 s wait for a vanished nth() row outlived until()'s 10 s budget (the CI smoke timeout)
    return [r.get_attribute("data-category", timeout=1000) for r in page.get_by_test_id("audit-row").all()]


@pytest.fixture
def live(tmp_path):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="a" * 64,
                      db_path=str(tmp_path / "act.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    # Test hook: hold the Activity tab's Agents answer on the server until the test releases it (a real slow answer).
    # Holding it with page.route instead depends on sync Playwright dispatching route callbacks, which flaked in CI.
    app.state.hold_agent = None  # a threading.Event while a test holds; None lets everything through
    app.state.held_agent = 0

    @app.middleware("http")
    async def _hold_agent(request, call_next):
        ev = app.state.hold_agent
        q = request.url.query
        if ev is not None and request.url.path == "/api/activity" and "tab=agent" in q and "group=run" not in q:
            app.state.held_agent += 1
            while not ev.is_set():
                await asyncio.sleep(0.05)
        return await call_next(request)

    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-act", "csrf-act")
    st.update_session("sid-act", user_id="acc_1")
    now = time.time()
    for i in range(30):  # enough of a person's own actions for two pages of 25
        st.x("INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts,category) VALUES(?,?,?,?,?,?,?,?)",
             ("acc_1", "you", f"seeded_{i:02d}", "read", json.dumps({"i": i}), 1, now - 3600 + i, "human"))
    server = serve(app, port)
    base = f"http://127.0.0.1:{port}"
    lo = httpx.Client(trust_env=False)  # loopback calls must not go through a sandbox HTTP(S)_PROXY
    tok = lo.post(f"{base}/api/agent-tokens", cookies={"hq_sid": "sid-act"}, headers={"X-HQ-CSRF": "csrf-act"},
                     json={"name": "my editor", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    lo.post(f"{base}/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                               "params": {"clientInfo": {"name": "cursor-vscode", "version": "1.2"}}})
    lo.post(f"{base}/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                               "params": {"name": "create_idea", "arguments": {"text": "idea from cursor"},
                                                          "_meta": {"model": "claude-sonnet-4-5"}}})
    lo.get(f"{base}/api/me", headers={"Authorization": "Bearer hq_wrong_token_for_the_failed_signin_row"})
    yield base, app
    server.should_exit = True


def test_activity_tabs_pages_agents_search_and_csv(live, chromium, tmp_path):
    base, app = live
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-act", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.goto(base)
        page.get_by_test_id("tab-activity").click()
        page.get_by_test_id("audit-row").first.wait_for()
        assert page.get_by_test_id("audit-row").count() == 25  # default page size
        first_page = page.get_by_test_id("audit-row").first.inner_text()
        page.get_by_test_id("act-next").click()
        until(lambda: page.get_by_test_id("act-page").inner_text().startswith("Page 2"))
        assert page.get_by_test_id("audit-row").first.inner_text() != first_page
        page.get_by_test_id("act-prev").click()
        until(lambda: page.get_by_test_id("act-page").inner_text().startswith("Page 1"))
        page.get_by_test_id("act-limit").select_option("50")
        until(lambda: page.get_by_test_id("audit-row").count() > 25)

        # AI agents: grouped by app, with the model the client reported
        page.get_by_test_id("act-tab-agent").click()
        page.get_by_test_id("act-group").first.wait_for()
        g = page.get_by_test_id("act-group").first.inner_text()
        assert "Cursor" in g and "claude-sonnet-4-5" in g and "my editor" in g and "model reported by the client" in g
        until(lambda: cats(page) and set(cats(page)) == {"agent"})
        assert "create_idea" in page.get_by_test_id("audit-list").inner_text()

        # Sign-ins: the failed attempt with the bad token is not in this account (the token matched nobody)
        page.get_by_test_id("act-tab-signin").click()
        until(lambda: page.get_by_test_id("act-tab-signin").get_attribute("aria-selected") == "true" and set(cats(page)) <= {"signin"})

        # System & Buffer API: the meter against Buffer's limits
        page.get_by_test_id("act-tab-system").click()
        page.get_by_test_id("act-buffer").wait_for()
        assert "limit 100" in page.get_by_test_id("act-buffer").inner_text()

        # search across everything
        page.get_by_test_id("act-tab-all").click()
        page.get_by_test_id("act-q").fill("seeded_07")
        page.get_by_test_id("act-apply").click()
        until(lambda: page.get_by_test_id("audit-row").count() == 1)
        assert "seeded_07" in page.get_by_test_id("audit-row").first.inner_text()

        # CSV export of exactly what is filtered
        with page.expect_download() as dl:
            page.get_by_test_id("act-csv").click()
        text = open(dl.value.path(), encoding="utf-8").read()
        assert text.splitlines()[0].startswith("id,time_utc,category") and "seeded_07" in text and "seeded_08" not in text
        page.get_by_test_id("act-q").fill("")
        page.get_by_test_id("act-tab-agent").click()
        page.get_by_test_id("act-group").first.wait_for()
        page.screenshot(path=os.environ.get("HQ_ACTIVITY_SHOT") or str(tmp_path / "activity.png"), full_page=True)
    finally:
        ctx.close()


def test_a_slow_answer_for_a_tab_you_left_never_overwrites_the_list(live, chromium):
    """Sweep 2 Nov 2026 (the flaky Sign-ins check in ui-smoke): tab answers arriving out of order showed the old tab's rows."""
    base, app = live
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-act", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())

        page = ctx.new_page()
        page.goto(base)
        page.get_by_test_id("tab-activity").click()
        page.get_by_test_id("audit-row").first.wait_for()
        release = threading.Event()
        app.state.hold_agent = release  # the Agents answer now waits on the server
        try:
            page.get_by_test_id("act-tab-agent").click()
            until(lambda: app.state.held_agent >= 1, page=page)
            page.get_by_test_id("act-tab-signin").click()
            until(lambda: set(cats(page)) <= {"signin"}, page=page)
        finally:
            release.set()  # now the old tab's answer lands, last
            app.state.hold_agent = None
        page.wait_for_timeout(1500)
        assert set(cats(page)) <= {"signin"}, cats(page)
    finally:
        ctx.close()
