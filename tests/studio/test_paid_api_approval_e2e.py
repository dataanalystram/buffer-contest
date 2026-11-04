"""Paid-API approvals in a real browser (owner decision, 4 Nov 2026; tests/studio/test_mcp_run_discipline.py is the unit side).

An AI app asks to use HuhQueue's server AI over MCP: the request waits in Inbox -> Approvals as its own card (who, which
API, the cost estimate, "nothing has run yet"). The person approves it there and the same call then runs once. In AI apps,
"Paid APIs: ask me" is off by default and turning it on goes through a confirm. Buffer is FakeBuffer; nothing is posted."""
import json
import time

import httpx
import pytest

from studio import house
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"


@pytest.fixture
def live(tmp_path, monkeypatch):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="p" * 64,
                      db_path=str(tmp_path / "paid.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-paid", "csrf-paid"); st.update_session("sid-paid", user_id="acc_1")
    ran = []
    monkeypatch.setattr(house.House, "start_run", lambda self, a, body: ran.append(body) or {"id": 777, "kind": "agent", "status": "running"})
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "ran": ran}
    server.should_exit = True


def _mcp(base, tok, name, args):
    r = httpx.post(base + "/mcp", headers={"Authorization": f"Bearer {tok}"}, timeout=20,
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r.json()["result"]
    return res["structuredContent"] if not res["isError"] else {"error": res["content"][0]["text"]}


def test_a_paid_api_request_is_approved_in_the_inbox_and_runs_once(live, chromium):
    base, app = live["base"], live["app"]
    studio = app.state.studio
    tok = studio.create_agent_token(house.Actor("acc_1"), "Claude", policy="trusted", daily_cap=50)["token"]
    args = {"query": "best headphones under 150", "server_ai": True}
    wait = _mcp(base, tok, "run_agent", args)
    assert wait["needs_user_approval"] is True and live["ran"] == []
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-paid", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.set_default_timeout(20000)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/?tab=inbox")
        card = page.locator("[data-testid=approval-item][data-kind=paid_api]")
        card.wait_for()
        assert "HuhQueue server AI" in card.inner_text() and "Claude" in card.inner_text()
        assert "up to $" in card.get_by_test_id("approval-cost").inner_text()
        assert "Nothing has run yet" in card.inner_text()
        card.get_by_test_id("approve").click()
        card.get_by_test_id("approval-status").get_by_text("Approved").wait_for()
        row = app.state.store.q("SELECT status FROM approvals WHERE id=?", (wait["approval_id"],))[0]
        assert row["status"] == "approved"
        ran = _mcp(base, tok, "run_agent", args)
        assert ran.get("id") == 777 and len(live["ran"]) == 1
        again = _mcp(base, tok, "run_agent", args)  # used up: it asks again
        assert again["needs_user_approval"] is True and len(live["ran"]) == 1
        # AI apps: "Paid APIs: ask me" by default; turning it on goes through a confirm
        page.get_by_test_id("tab-apps").click()
        sw = page.get_by_test_id("token-paid").first
        sw.wait_for()
        assert "ask me" in sw.inner_text()
        sw.click()
        page.get_by_test_id("ask-ok").click()
        page.get_by_test_id("token-paid").first.get_by_text("allowed").wait_for()
        t = app.state.store.q("SELECT allow_paid_apis FROM agent_tokens WHERE name='Claude'")[0]
        assert t["allow_paid_apis"] == 1
        assert errors == [], errors
    finally:
        ctx.close()
