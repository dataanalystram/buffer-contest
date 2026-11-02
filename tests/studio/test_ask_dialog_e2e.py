"""In-app confirm/prompt (askdialog.js) instead of window.confirm/prompt. Native dialogs block the page and can be suppressed
by the browser, so "Delete draft" in the Planner looked like it did nothing. These tests drive the real UI against a fake
Buffer: Cancel sends nothing, Delete draft sends Buffer's deletePost, and the token "Change limit" saves through PATCH."""
import pathlib
import time

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
STATIC = pathlib.Path(__file__).resolve().parents[2] / "studio" / "static"


def test_no_native_confirm_or_prompt_left():
    """Every confirm and prompt in the app goes through the in-app dialog."""
    import re
    bad = []
    for f in STATIC.glob("*.js"):
        if f.name == "askdialog.js":
            continue
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if re.search(r"(?<![\w.])(window\.)?(confirm|prompt|alert)\(", line):
                bad.append(f"{f.name}:{n}")
    assert not bad, bad
    html = (STATIC / "index.html").read_text()
    assert html.index("/static/askdialog.js") < html.index("/static/app.js")


@pytest.fixture
def live(tmp_path):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="f" * 64,
                      db_path=str(tmp_path / "ask.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-ask", "csrf-ask"); st.update_session("sid-ask", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", fake, app
    server.should_exit = True


def _page(chromium, base, w=1440, h=1000):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-ask", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(15000)
    page.native_dialogs = []
    page.on("dialog", lambda d: (page.native_dialogs.append(d.message), d.dismiss()))
    return ctx, page


def test_planner_delete_draft_confirm_sends_delete(live, chromium):
    base, fake, app = live
    ctx, page = _page(chromium, base)
    try:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.get_by_test_id("tab-planner").click()
        page.get_by_test_id("planner-nodate-toggle").check(force=True)  # the fake's draft has no date
        btn = page.locator("[data-testid=planner-delete][data-ref]").first
        btn.wait_for()
        # Cancel: the dialog closes and nothing reaches Buffer
        btn.click()
        dlg = page.get_by_test_id("ask-dialog")
        pw.expect(dlg).to_be_visible()
        pw.expect(page.get_by_test_id("ask-message")).to_contain_text("Delete this draft from Buffer?")
        page.get_by_test_id("ask-cancel").click()
        pw.expect(dlg).to_have_count(0)
        assert fake.deleted == []
        # Esc also answers no
        btn.click(); pw.expect(dlg).to_be_visible(); page.keyboard.press("Escape")
        pw.expect(dlg).to_have_count(0)
        assert fake.deleted == []
        # Delete draft: Buffer's deletePost is sent and the draft leaves the Planner
        btn.click()
        page.get_by_test_id("ask-ok").click()
        page.get_by_test_id("toast").get_by_text("Draft deleted").wait_for()
        assert fake.deleted == ["d1"]
        pw.expect(page.locator("[data-testid=planner-delete]")).to_have_count(0)
        assert not errors, errors
        assert page.native_dialogs == []
    finally:
        ctx.close()


def test_token_change_limit_uses_in_app_prompt(live, chromium):
    base, fake, app = live
    tok = app.state.studio.create_agent_token(_human(app), "Smoke test agent", "review", 10, None, 30)
    ctx, page = _page(chromium, base)
    try:
        page.goto(base)
        page.get_by_test_id("inbox-title").wait_for()
        page.evaluate("loadTokens()")
        edit = page.get_by_test_id("token-cap-edit").first
        edit.wait_for(state="attached")
        edit.evaluate("b => b.click()")
        inp = page.get_by_test_id("ask-input")
        pw.expect(inp).to_have_value("10")
        inp.fill("60")
        page.get_by_test_id("ask-ok").click()
        page.get_by_test_id("toast").get_by_text("Daily limit changed").wait_for()
        rows = app.state.store.q("SELECT daily_cap FROM agent_tokens WHERE id=?", (tok["id"],))
        assert rows[0]["daily_cap"] == 60
    finally:
        ctx.close()


def _human(app):
    from studio.actions import Actor
    return Actor(uid="acc_1")
