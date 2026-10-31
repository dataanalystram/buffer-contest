"""Browser test of the Studio UI against a fake Buffer (Playwright). Skips if Chromium can't start."""
import time

import httpx
import pytest

from studio import drafter
from studio.app import create_app
from studio.config import load_settings

from .conftest import ARTICLE, FakeBuffer, image_bytes

from .browsers import free_port, serve

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1')"  # the guided tour has its own test (test_tour_e2e.py)


def _free_port():
    return free_port()


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(drafter, "fetch_article", lambda url, *a, **k: dict(ARTICLE, url=url))
    fake = FakeBuffer()
    port = _free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="e" * 64,
                      db_path=str(tmp_path / "e2e.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    app.state.studio.media_loader = fake.load_media  # hermetic: preflight "downloads" media from the fake
    from studio import preflight
    preflight._CACHE.clear()
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-e2e", "csrf-e2e"); st.update_session("sid-e2e", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", fake, app
    server.should_exit = True


def test_link_to_buffer_draft_and_agent_approval(live, chromium):
    base, fake, app = live
    browser = chromium
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic: no fonts or images from the internet
        page = ctx.new_page()
        page.goto(base)
        page.get_by_test_id("agent-go").wait_for()  # HuhQueue opens on the agent
        page.get_by_test_id("tab-inbox").click()
        page.get_by_test_id("inbox-item").first.wait_for()
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
        for c in list(browser.contexts):
            c.close()


def test_planner_views_filters_and_drag_to_reschedule(live, chromium):
    """Planner in the sample workspace: List/Week/Month, channel filter, and dragging a No date draft onto the week."""
    base, fake, app = live
    browser = chromium
    try:
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Europe/Dublin")
        ctx.add_init_script(NO_TOUR)
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
        for c in list(browser.contexts):
            c.close()



def test_preflight_panel_blocks_then_fix_it_makes_posts_pass(live, chromium):
    """Composer: red chip blocks the send, Fix it threads the X post and crops the Instagram image, then both go."""
    base, fake, app = live
    fake.media["https://cdn.example.com/tall.png"] = image_bytes(900, 2000)
    browser = chromium
    try:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_init_script(NO_TOUR)
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base)
        page.get_by_test_id("tab-studio").click()
        page.get_by_test_id("url-input").fill("https://news.example.com/ai-launch")
        page.get_by_test_id("draft-btn").click()
        page.get_by_test_id("draft-card").first.wait_for()
        page.get_by_test_id("preflight-strip").wait_for()
        page.locator("[data-testid=preflight-strip-chip]").nth(2).wait_for()
        assert page.get_by_test_id("preflight-strip-chip").count() == 3
        x = page.locator("[data-testid=draft-card][data-service=twitter]")
        long = " ".join(f"Point {i} about the model launch today." for i in range(20))
        x.get_by_test_id("draft-text").fill(long)
        x.locator("[data-testid=preflight-chip][data-status=error]").wait_for()
        x.get_by_test_id("preflight-finding").first.wait_for()
        assert "too long for X" in x.get_by_test_id("preflight-findings").inner_text()
        n = len(fake.created)
        x.get_by_test_id("draft-mode").select_option("draft")
        x.get_by_test_id("send-draft").click()
        x.get_by_test_id("draft-err").wait_for()
        assert len(fake.created) == n  # blocked before createPost
        x.get_by_test_id("preflight-fix").click()
        x.locator("[data-testid=preflight-chip][data-status=ok]").wait_for()
        assert "---" in x.get_by_test_id("draft-text").input_value()
        x.get_by_test_id("preflight-undo").wait_for()
        x.get_by_test_id("send-draft").click()
        x.get_by_test_id("draft-ok").wait_for()
        assert "thread" in fake.created[-1]["metadata"]["twitter"]
        # Instagram: a 9:20 PNG is red (aspect) and amber (PNG); Fix it crops and converts it with Pillow
        ig = page.locator("[data-testid=draft-card][data-service=instagram]")
        ig.get_by_test_id("draft-media").fill("https://cdn.example.com/tall.png")
        ig.locator("[data-testid=preflight-chip][data-status=error]").wait_for()
        ig.get_by_test_id("preflight-fix").click()
        ig.locator("[data-testid=preflight-chip][data-status=ok]").wait_for()
        assert ig.get_by_test_id("draft-media").input_value().startswith(base + "/m/")
        ig.get_by_test_id("draft-mode").select_option("draft")
        ig.get_by_test_id("send-draft").click()
        ig.get_by_test_id("draft-ok").wait_for()
        # a warning needs the Send anyway box: an image the server can't download
        li = page.locator("[data-testid=draft-card][data-service=linkedin]")
        li.get_by_test_id("draft-media").fill("https://cdn.example.com/missing.jpg")
        li.locator("[data-testid=preflight-chip][data-status=warning]").wait_for()
        li.get_by_test_id("draft-mode").select_option("draft")
        li.get_by_test_id("send-draft").click()
        li.get_by_test_id("draft-err").wait_for()
        li.get_by_test_id("preflight-override").check()
        li.get_by_test_id("send-draft").click()
        li.get_by_test_id("draft-ok").wait_for()
        assert not errors, errors
    finally:
        for c in list(browser.contexts):
            c.close()


def test_planner_dialog_preflight(live, chromium):
    """Planner new-post dialog in the sample workspace: a too-long X post is blocked until Fix it threads it."""
    base, fake, app = live
    browser = chromium
    try:
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, timezone_id="Europe/Dublin")
        ctx.add_init_script(NO_TOUR)
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/demo")
        page.get_by_test_id("tab-planner").click()
        page.get_by_test_id("planner-new").click()
        page.get_by_test_id("planner-dialog-channel").select_option("demo_x")
        page.get_by_test_id("planner-dialog-text").fill("word " * 80)
        dlg = page.get_by_test_id("planner-preflight")
        dlg.locator("[data-testid=preflight-chip][data-status=error]").wait_for()
        page.get_by_test_id("planner-dialog-save").click()
        pw.expect(page.get_by_test_id("planner-dialog-error")).to_contain_text("Preflight")  # set after an await
        dlg.get_by_test_id("preflight-fix").click()
        dlg.locator("[data-testid=preflight-chip][data-status=ok]").wait_for()
        page.get_by_test_id("planner-dialog-save").click()
        page.get_by_test_id("toast").get_by_text("Scheduled in Buffer").wait_for()
        assert not errors, errors
        assert not fake.calls
    finally:
        for c in list(browser.contexts):
            c.close()


def test_planner_week_opens_at_its_first_post_after_leaving_and_coming_back(live, chromium):
    """Week opens scrolled to the week's first post, also when the Planner is opened a second time (a hidden scroller
    reads scrollTop 0, which used to be kept, so the week opened at 00:00 with every post below the fold)."""
    base, fake, app = live
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900}, timezone_id="Europe/Dublin")
    try:
        ctx.add_init_script(NO_TOUR)
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
        page = ctx.new_page()
        page.goto(base + "/demo")
        for _ in range(2):
            page.get_by_test_id("tab-planner").click()
            page.get_by_test_id("planner-view-week").click()
            page.get_by_test_id("planner-week").wait_for()
            page.wait_for_timeout(300)
            seen = page.evaluate("""() => {
              const sc = document.querySelector('.pl-week-scroll');
              const items = [...sc.querySelectorAll('[data-testid=planner-item]:not([data-kind=ghost])')];
              if (!items.length) return null;
              const box = sc.getBoundingClientRect();
              const top = Math.min(...items.map((i) => i.getBoundingClientRect().top));
              return top >= box.top && top <= box.bottom;
            }""")
            assert seen in (True, None), "the week's first post is outside the visible grid"
            page.get_by_test_id("tab-inbox").click()
    finally:
        ctx.close()
