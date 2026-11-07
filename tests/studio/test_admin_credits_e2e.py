"""/admin Claude credit in a real browser (studio/credits.py, docs/ADMIN.md): the users table shows "$x of $y left", a
group is created and a user added with the bulk bar, the Credit dialog adds $5, and group delete takes a second click.
No page or console errors at phone width either."""
import time

import httpx
import pytest

from studio import budget
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pytest.importorskip("playwright.sync_api")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")
    monkeypatch.delenv("AI_BUDGET_PER_USER_USD", raising=False)
    monkeypatch.delenv("HQ_BRAIN_TESTERS", raising=False)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="c" * 64,
                      db_path=str(tmp_path / "credits.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    st = app.state.store
    for uid, email, name in (("acc_1", "ram@example.com", "Ram"), ("acc_2", "judge@example.com", "Judge Person")):
        st.upsert_user(uid, email, name, "org_1", "HuhByte", {"access_token": "at_" + uid, "refresh_token": "rt", "expires_at": time.time() + 3600})
    st.new_session("sid-c", "csrf-c")
    st.update_session("sid-c", user_id="acc_1")
    budget.ensure(st)
    budget.record(st, "acc_2", 1.0)
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", st
    server.should_exit = True


@pytest.mark.parametrize("width", [390, 1280])
def test_admin_credit_flow(live, chromium, width):
    base, st = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900})
    errors = []
    try:
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-c", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "net::ERR_FAILED" not in m.text else None)
        page.goto(base + "/admin")
        judge = page.locator("[data-testid=admin-user]", has_text="judge@example.com")
        judge.locator("[data-testid=admin-user-credit]").filter(has_text="$4.00 of $5.00 left").wait_for(timeout=15000)
        assert "default" in judge.locator("[data-testid=admin-user-credit]").inner_text()
        # a group, and the judge added to it from the bulk bar
        page.fill("[data-testid=admin-group-name]", "Contest judges")
        page.fill("[data-testid=admin-group-usd]", "15")
        page.click("[data-testid=admin-group-create]")
        page.locator("[data-testid=admin-group]").filter(has=page.locator("input[value='Contest judges']")).wait_for()
        judge.locator("[data-testid=admin-select]").check()
        page.select_option("[data-testid=admin-bulk-group]", label="Contest judges ($15.00)")
        page.click("[data-testid=admin-bulk-add]")
        judge = page.locator("[data-testid=admin-user]", has_text="judge@example.com")
        judge.locator("[data-testid=admin-user-credit]").filter(has_text="$14.00 of $15.00 left").wait_for()
        assert "group: Contest judges" in judge.locator("[data-testid=admin-user-credit]").inner_text()
        # the Credit dialog: +$5 makes a personal $20
        judge.locator("[data-testid=admin-credit-open]").click()
        page.locator("[data-testid=admin-credit-now]").filter(has_text="from the group Contest judges").wait_for()
        page.fill("[data-testid=admin-credit-note]", "demo day")
        page.click("[data-testid=admin-credit-add5]")
        page.locator("[data-testid=admin-credit-now]").filter(has_text="$1.00 spent of $20.00 (from a personal allowance)").wait_for()
        page.click("[data-testid=admin-credit-close]")
        judge = page.locator("[data-testid=admin-user]", has_text="judge@example.com")
        judge.locator("[data-testid=admin-user-credit]").filter(has_text="$19.00 of $20.00 left").wait_for()
        # delete the group: the first click only arms it
        page.click("[data-testid=admin-group-delete]")
        assert st.q("SELECT COUNT(*) AS n FROM credit_groups")[0]["n"] == 1
        page.locator("[data-testid=admin-group-delete]", has_text="Confirm delete").click()
        page.locator("#grpRows", has_text="No groups yet.").wait_for()
        assert st.q("SELECT COUNT(*) AS n FROM credit_groups")[0]["n"] == 0
        acts = [r["action"] for r in st.q("SELECT action FROM admin_audit ORDER BY id")]
        assert acts == ["group_create", "group_members", "credit_add", "group_delete"]
    finally:
        ctx.close()
    assert not errors, (width, errors)
