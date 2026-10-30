"""Browser test of the Studio UI against a fake Buffer (Playwright). Skips if Chromium can't start."""
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from studio import drafter
from studio.app import create_app
from studio.config import load_settings

from .conftest import ARTICLE, FakeBuffer

pw = pytest.importorskip("playwright.sync_api")


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(drafter, "fetch_article", lambda url, *a, **k: dict(ARTICLE, url=url))
    fake = FakeBuffer()
    port = _free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="e" * 64,
                      db_path=str(tmp_path / "e2e.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-e2e", "csrf-e2e"); st.update_session("sid-e2e", user_id="acc_1")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}", fake, app
    server.should_exit = True


def test_link_to_buffer_draft_and_agent_approval(live):
    base, fake, app = live
    try:
        p = pw.sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as e:  # no browser on this machine
        pytest.skip(f"chromium unavailable: {e}")
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic: no fonts or images from the internet
        page = ctx.new_page()
        page.goto(base)
        page.get_by_test_id("agent-go").wait_for()  # HuhQueue opens on the agent
        page.get_by_test_id("tab-home").click()
        page.get_by_test_id("todo-item").first.wait_for()
        page.get_by_test_id("tab-studio").click()
        page.get_by_test_id("channel-chip").first.wait_for()
        assert page.get_by_test_id("channel-chip").count() == 3
        assert not page.get_by_test_id("landing").is_visible()
        page.get_by_test_id("url-input").fill("https://news.example.com/ai-launch")
        page.get_by_test_id("draft-btn").click()
        page.get_by_test_id("draft-card").first.wait_for()
        assert page.get_by_test_id("draft-card").count() == 3
        x = page.locator("[data-testid=draft-card][data-service=twitter]")
        x.get_by_test_id("draft-mode").select_option("draft")
        x.get_by_test_id("send-draft").click()
        x.get_by_test_id("draft-ok").wait_for()
        assert fake.created[-1]["saveToDraft"] is True
        # an agent submits; a human approves it in the inbox
        studio = app.state.studio
        from studio.actions import Actor
        tok = studio.create_agent_token(Actor("acc_1"), "Claude")["token"]
        agent = studio.resolve_agent(tok)
        pv = studio.preview_post(agent, {"channel_id": "ch_li", "text": "Agent post", "mode": "draft"})
        studio.submit_post(agent, pv["confirm_token"])
        page.get_by_test_id("tab-approvals").click()
        page.get_by_test_id("approval-item").wait_for()
        page.get_by_test_id("approve").click()
        page.get_by_test_id("approval-status").get_by_text("Sent to Buffer").wait_for()
        assert fake.created[-1]["text"] == "Agent post"
        page.get_by_test_id("tab-agents").click()
        page.locator("label.switch").click()
        page.get_by_text("Agents paused").wait_for()
        assert app.state.store.user("acc_1")["kill_switch"] == 1
        page.get_by_test_id("tab-activity").click()
        page.get_by_test_id("audit-row").first.wait_for()
    finally:
        browser.close(); p.stop()


def test_planner_views_filters_and_drag_to_reschedule(live):
    """Planner in the sample workspace: List/Week/Month, channel filter, and dragging a No date draft onto the week."""
    base, fake, app = live
    try:
        p = pw.sync_playwright().start()
        browser = p.chromium.launch()
    except Exception as e:
        pytest.skip(f"chromium unavailable: {e}")
    try:
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Europe/Dublin")
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/demo")
        page.get_by_test_id("tab-planner").click()
        assert page.get_by_test_id("tab-planner").get_attribute("aria-current") == "page"
        for view in ("list", "week", "month"):
            page.get_by_test_id(f"planner-view-{view}").click()
            page.get_by_test_id(f"planner-{view}").wait_for()
            assert f"view={view}" in page.url or view == "week"
        # filter by one channel in the list
        page.get_by_test_id("planner-view-list").click()
        page.get_by_test_id("planner-card").first.wait_for()
        assert len({c.get_attribute("data-ch") for c in page.get_by_test_id("planner-card").all()}) > 1
        page.locator("[data-testid=planner-channel][data-ch=demo_x]").click()
        assert "ch=demo_x" in page.url
        chans = {c.get_attribute("data-ch") for c in page.get_by_test_id("planner-card").all()}
        assert chans == {"demo_x"}, chans
        # filters and view survive a reload (URL + localStorage)
        page.reload()
        page.get_by_test_id("planner-list").wait_for()
        assert page.locator("[data-testid=planner-channel][data-ch=demo_x]").get_attribute("aria-pressed") == "true"
        page.get_by_test_id("planner-clear").click()
        # a status filter with no match shows the empty state
        page.get_by_test_id("planner-search").fill("zzz-nothing-matches")
        page.get_by_test_id("planner-empty").wait_for()
        page.get_by_test_id("planner-empty-clear").click()
        # drag a No date draft onto next week's Wednesday, 14:00
        page.get_by_test_id("planner-view-week").click()
        page.get_by_test_id("planner-nodate-toggle").check(force=True)
        page.get_by_test_id("planner-next").click()
        page.get_by_test_id("planner-week").wait_for()
        draft = page.get_by_test_id("planner-nodate-item").first
        ref = draft.get_attribute("data-ref")
        cell = page.locator("[data-testid=planner-slot][data-hour='14']").nth(2)
        day = cell.get_attribute("data-day")
        draft.drag_to(cell)
        page.get_by_test_id("toast").get_by_text("Draft moved").wait_for()
        page.locator(f"[data-testid=planner-slot][data-day='{day}'][data-hour='14'] [data-ref='{ref}']").wait_for()
        # sent posts are locked
        assert page.locator("[data-testid=planner-item][data-state=sent][draggable]").count() == 0
        assert not errors, errors
        assert not fake.calls  # the sample workspace never reaches Buffer
    finally:
        browser.close(); p.stop()
