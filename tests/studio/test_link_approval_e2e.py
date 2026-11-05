"""Video-link approvals and the Inbox cards in a real browser (5 Nov 2026; tests/studio/test_link_approval_nov5.py is the
unit side).

* The "Claude wants One take to fetch a video" card is one column at 390 px and 1280 px: the link (one line, ellipsis,
  full in its title), the note, the rights statement row, then Reject / Approve. Before, the link, the statement and the
  note sat in three squeezed columns (owner screenshot).
* Approve works end to end: tick the statement, Approve, the item is approved and the app's next call starts the take.
* Needs attention cards carry urgency as a small pill, never a coloured bar down the left edge.
* Settings -> Privacy & data -> Auto-approve video links: off by default; turning it on shows the statement and the app
  scope and saves nothing until it is ticked; it can be turned off again.
Buffer is FakeBuffer, the video worker is stubbed; nothing is posted or fetched."""
import json
import time

import httpx
import pytest

from studio import drafter, house
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
LONG = "https://www.youtube.com/watch?v=QJf0H64BnFo&list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf&index=7&t=42s&pp=iAQB"


@pytest.fixture
def live(tmp_path, monkeypatch):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="l" * 64,
                      db_path=str(tmp_path / "link.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-link", "csrf-link"); st.update_session("sid-link", user_id="acc_1")
    monkeypatch.setattr(drafter, "_public", lambda h: h not in ("127.0.0.1", "localhost"))
    h = app.state.house
    sent = []
    monkeypatch.setattr(h, "_dispatch_github", lambda jid, workflow="render.yml", inputs=None, ttl=1800: sent.append(jid) or (True, ""))
    monkeypatch.setattr(h.takes, "_mode", lambda: "github")
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "sent": sent}
    server.should_exit = True


def _mcp(base, tok, name, args):
    r = httpx.post(base + "/mcp", headers={"Authorization": f"Bearer {tok}"}, timeout=20,
                   json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
    res = r.json()["result"]
    return res["structuredContent"] if not res["isError"] else {"error": res["content"][0]["text"]}


def _page(chromium, base, width):
    ctx = chromium.new_context(viewport={"width": width, "height": 900})
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-link", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


@pytest.mark.parametrize("width", [390, 1280])
def test_the_link_card_is_one_column_and_approve_works(live, chromium, width):
    base, app = live["base"], live["app"]
    tok = app.state.studio.create_agent_token(house.Actor("acc_1"), "Claude via claude.ai (connected)", policy="trusted", daily_cap=50)["token"]
    wait = _mcp(base, tok, "ingest_video_link", {"url": LONG})
    assert wait.get("needs_user_approval") is True, wait
    ctx, page, errors = _page(chromium, base, width)
    try:
        page.goto(base + "/?tab=inbox")
        card = page.locator("[data-testid=approval-item][data-kind=link_rights]")
        card.wait_for()
        assert "wants One take to fetch a video" in card.inner_text()
        box = card.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 1           # no sideways overflow
        link, note, row, act = (card.get_by_test_id(t).bounding_box() for t in ("approval-link", "approval-note", "approval-attest-row", "approve"))
        # stacked: link, then the note, then the statement row, then the buttons
        assert link["y"] + link["height"] <= note["y"] + 1 and note["y"] + note["height"] <= row["y"] + 1 and row["y"] + row["height"] <= act["y"] + 1
        assert row["width"] >= box["width"] * 0.8 and note["width"] >= box["width"] * 0.6   # full width, not a narrow column
        m = card.get_by_test_id("approval-link").evaluate(
            "a => ({sw: a.scrollWidth, cw: a.clientWidth, ws: getComputedStyle(a).whiteSpace, to: getComputedStyle(a).textOverflow, title: a.title})")
        assert m["ws"] == "nowrap" and m["to"] == "ellipsis" and m["title"] == LONG, m
        assert link["height"] < 32                                                # one line
        st = card.get_by_test_id("approval-attest").evaluate("i => getComputedStyle(i.closest('article')).backgroundImage")
        assert "gradient" not in st
        # Approve stays off until the statement is ticked, then works (main answered "No pending approval with that id")
        approve = card.get_by_test_id("approve")
        assert approve.is_disabled()
        card.get_by_test_id("approval-attest").check()
        approve.click()
        card.get_by_test_id("approval-status").get_by_text("Approved").wait_for()
        row = app.state.store.q("SELECT status FROM approvals WHERE id=?", (wait["approval_id"],))[0]
        assert row["status"] == "approved"
        t = _mcp(base, tok, "ingest_video_link", {"url": LONG, "approval_id": wait["approval_id"]})
        assert t.get("id") and t["input"]["consent_id"] and len(live["sent"]) == 1, t
        page.screenshot(path=f"/tmp/link-card-{width}.png", full_page=False)
        assert errors == [], errors
    finally:
        ctx.close()


INBOX = {"waiting": 3, "kill_switch": False, "groups": [
    {"key": "failed", "title": "Failed to send", "summary": "1 post bounced.", "urgency": "now", "count": 1, "items": [], "actions": []},
    {"key": "no_time", "title": "Drafts with no date", "summary": "7 drafts won't go out until you give them a time.", "urgency": "soon",
     "count": 7, "items": [], "actions": [{"id": "open", "label": "Open in Planner", "go": "planner"}]},
    {"key": "open_slots", "title": "Open slots", "summary": "2 open slots this week.", "urgency": "fyi", "count": 2, "items": [], "actions": []}]}


@pytest.mark.parametrize("width", [390, 1280])
def test_needs_attention_cards_have_no_coloured_bar(live, chromium, width):
    base = live["base"]
    ctx, page, errors = _page(chromium, base, width)
    try:
        page.route(lambda u: u.split("?")[0].endswith("/api/inbox"),
                   lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(INBOX)))
        page.goto(base + "/?tab=inbox")
        groups = page.get_by_test_id("inbox-group")
        groups.first.wait_for()
        assert groups.count() == 3
        for i in range(groups.count()):
            g = groups.nth(i)
            s = g.evaluate("e => { const c = getComputedStyle(e); return {bl: c.borderLeftWidth, br: c.borderRightWidth, blc: c.borderLeftColor,"
                           " brc: c.borderRightColor, sh: c.boxShadow, bg: c.backgroundImage} }")
            assert s["bl"] == s["br"] and s["blc"] == s["brc"], s                 # the left edge is like the others
            assert "inset" not in s["sh"] and "gradient" not in s["bg"], s
            pill = g.get_by_test_id("inbox-urgency")
            assert pill.count() == 1 and pill.locator(".ib-dot").count() == 1
            p = pill.evaluate("e => ({r: parseFloat(getComputedStyle(e).borderTopLeftRadius), h: e.getBoundingClientRect().height})")
            assert p["r"] >= 10 and p["h"] <= 26, p                                 # a small pill
        soon = page.locator("[data-testid=inbox-group][data-urgency=soon] [data-testid=inbox-urgency]")
        assert soon.inner_text().strip().lower() == "soon"
        assert errors == [], errors
    finally:
        ctx.close()


def test_settings_auto_approve_toggle_needs_the_statement_and_turns_off(live, chromium):
    base, app = live["base"], live["app"]
    tok = app.state.studio.create_agent_token(house.Actor("acc_1"), "Claude", policy="trusted", daily_cap=50)["token"]
    ctx, page, errors = _page(chromium, base, 1280)
    try:
        page.goto(base + "/?tab=settings")
        page.get_by_test_id("settings-tab-privacy").click()
        panel = page.get_by_test_id("settings-link-auto")
        panel.get_by_test_id("link-auto-off").wait_for()
        toggle = panel.get_by_test_id("link-auto-toggle")
        assert not toggle.is_checked()
        panel.locator(".switch").click()
        form = panel.get_by_test_id("link-auto-form")
        form.wait_for()
        assert not toggle.is_checked()                      # nothing changes until the statement is ticked
        assert "I own this video or have permission" in form.inner_text()
        save = form.get_by_test_id("link-auto-save")
        assert save.is_disabled()
        form.get_by_test_id("link-auto-scope-apps").check()
        form.get_by_test_id("link-auto-attest").check()
        assert save.is_disabled()                           # "pick apps" with none picked
        form.get_by_test_id("link-auto-app").first.check()
        save.click()
        panel.get_by_test_id("link-auto-on").wait_for()
        assert toggle.is_checked() and "Claude" in panel.get_by_test_id("link-auto-on").inner_text()
        row = app.state.store.q("SELECT enabled, scope, attested_at FROM link_auto_approve WHERE user_id='acc_1'")[0]
        assert row["enabled"] == 1 and row["scope"] == "apps" and row["attested_at"]
        # covered: the app's link starts at once, and Activity marks it auto-approved
        t = _mcp(base, tok, "ingest_video_link", {"url": LONG})
        assert t.get("id") and len(live["sent"]) == 1, t
        page.get_by_test_id("tab-activity").click() if page.get_by_test_id("tab-activity").count() else page.goto(base + "/?tab=activity")
        page.get_by_test_id("activity-auto-approved").first.wait_for()
        # off again in one click
        page.goto(base + "/?tab=settings")
        page.get_by_test_id("settings-tab-privacy").click()
        panel.get_by_test_id("link-auto-on").wait_for()
        panel.locator(".switch").click()
        panel.get_by_test_id("link-auto-off").wait_for()
        assert app.state.store.q("SELECT enabled FROM link_auto_approve WHERE user_id='acc_1'")[0]["enabled"] == 0
        again = _mcp(base, tok, "ingest_video_link", {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"})
        assert again.get("needs_user_approval") is True
        assert errors == [], errors
    finally:
        ctx.close()
