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
        page = ctx.new_page()
        page.goto(base)
        page.get_by_test_id("agent-go").wait_for()  # House Style opens on the agent
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
