"""Brain swap UI (BRAIN_SWAP_PLAN): the AI allowance meter ("$X of $1 left"), the free route at >=70% and at 100%
("Keep going free in Claude: connect HuhQueue"), the Settings -> Integrations card "Use HuhQueue in Claude / ChatGPT"
(MCP address + copy + 3 steps), the onboarding step and the README line. No model calls."""
import os
import time
from pathlib import Path

import httpx
import pytest

from studio import budget
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

ROOT = Path(__file__).parents[2]
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
SHOTS = os.environ.get("HQ_SHOTS_DIR")


def _uid(app):
    return app.state.store.q("SELECT id FROM users LIMIT 1")[0]["id"]


def _spend(app, uid, usd):
    budget.record(app.state.store, uid, usd)


def test_meter_levels_and_the_100_percent_prompt(app, human, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    uid = _uid(app)
    m = human.get("/api/agent/brain").json()["meter"]
    assert m["label"] == "$1.00 of $1 left" and m["used_pct"] == 0 and not m["nudge"] and not m["out"]
    _spend(app, uid, 0.38)
    m = human.get("/api/agent/brain").json()["meter"]
    assert m["label"] == "$0.62 of $1 left" and not m["nudge"]
    _spend(app, uid, 0.34)  # 72% used: the free route appears
    m = human.get("/api/agent/brain").json()["meter"]
    assert m["nudge"] and not m["out"] and m["message"] == "Keep going free in Claude: connect HuhQueue."
    _spend(app, uid, 0.40)  # past the cap: out of credit, the prompt stays
    m = human.get("/api/agent/brain").json()["meter"]
    assert m["out"] and m["nudge"] and m["label"] == "$0.00 of $1 left" and m["used_pct"] == 100
    assert m["message"] == "Out of credit? Keep going free in Claude: connect HuhQueue."


def test_no_meter_without_the_operator_key(human, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert human.get("/api/agent/brain").json()["meter"] is None


def test_static_card_onboarding_and_readme():
    html = (ROOT / "studio/static/index.html").read_text()
    js = (ROOT / "studio/static/brainswap.js").read_text()
    onb = (ROOT / "studio/static/onboarding.js").read_text()
    assert 'id="useInClaude"' in html and 'id="aiMeter"' in html and 'id="agMeter"' in html and "/static/brainswap.js" in html
    assert "Use HuhQueue in Claude / ChatGPT" in js and "Your model does the thinking, HuhQueue does the editing. Free." in js
    assert "connect HuhQueue" in js and "use-in-claude-copy" in js and js.count("<li>") == 3
    assert '"Your AI"' in onb and "Bring your own brain" in onb and "onboarding-mcp" in onb
    assert "**Bring your own brain.**" in (ROOT / "README.md").read_text()


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="b" * 64,
                      db_path=str(tmp_path / "bs.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-bs", "csrf-bs")
    st.update_session("sid-bs", user_id="acc_1")
    budget.record(st, "acc_1", 1.20)  # the whole $1 is used
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True


def _until(fn, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.1)
    raise AssertionError("timed out waiting for the page")


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (1440, "dark"), (390, "light"), (390, "dark")])
def test_browser_100_percent_prompt_and_integrations_card(live, chromium, width, scheme):
    pytest.importorskip("playwright.sync_api")
    base, _ = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-bs", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.goto(base + "/?tab=agent")
        nudge = page.get_by_test_id("agent-meter-nudge")
        nudge.wait_for()
        assert "Out of credit?" in nudge.inner_text() and "Keep going free in Claude: connect HuhQueue" in nudge.inner_text()
        assert "$0.00 of $1 left" in page.get_by_test_id("agent-meter-left").inner_text()
        if SHOTS:
            page.get_by_test_id("agent-meter").screenshot(path=f"{SHOTS}/agent_meter_{width}_{scheme}.png")
        page.get_by_test_id("agent-meter-connect").click()  # -> Settings, the Claude / ChatGPT card
        card = page.get_by_test_id("use-in-claude")
        card.wait_for()
        _until(lambda: base + "/mcp" in page.get_by_test_id("use-in-claude-url").inner_text())
        assert "Your model does the thinking, HuhQueue does the editing. Free." in card.inner_text()
        assert page.locator("[data-testid=use-in-claude-steps] li").count() == 3
        assert "Out of credit?" in page.get_by_test_id("ai-meter-nudge").inner_text()
        # nothing overflows sideways at this width
        assert page.evaluate("document.documentElement.scrollWidth") <= width + 1
        if SHOTS:
            card.scroll_into_view_if_needed()
            card.screenshot(path=f"{SHOTS}/card_{width}_{scheme}.png")
            page.get_by_test_id("ai-meter").screenshot(path=f"{SHOTS}/ai_meter_{width}_{scheme}.png")
    finally:
        ctx.close()
