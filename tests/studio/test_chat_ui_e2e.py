"""Make a post as a chat + the Settings sidebar in a real browser (owner, 3 Nov 2026), at 1280 and 390 px.

Start a thread from Discover (Enter sends), see the chat: the steps folded into one row, the post cards inline with a
Preview side panel, the Ready to send bar, the model and the context drawer; open an older thread from the sidebar and
rename it; a long thread says "Earlier messages summarized"; Esc closes each layer; outside text never runs as HTML.
Settings: a left nav with one page each for Account, AI engine, Integrations, Brand & templates and Privacy & data,
deep-linkable (?tab=settings&sub=ai). Screenshots go to shots/chatui/ (uploaded by ui-smoke)."""
import json
import pathlib
import re
import time

import httpx
import pytest

from studio import drafter, house, netguard, reader, sources
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer
from .test_house import ARTICLE, jpeg

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
SHOTS = pathlib.Path(__file__).resolve().parents[2] / "shots" / "chatui"
STORY = {"title": "Acme launches an agent that books travel with one approval", "url": ARTICLE["url"], "source": "news.example.com",
         "summary": "The agent plans trips end to end and asks once before paying. An AI launch.", "published": int(time.time()) - 3600, "image": ""}


@pytest.fixture
def live(tmp_path, monkeypatch):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="c" * 64,
                      db_path=str(tmp_path / "chat.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True, render_worker="none")
    monkeypatch.setattr(sources, "search", lambda q, feeds, http=None, limit=12, **kw: [
        {"title": ARTICLE["title"], "url": ARTICLE["url"], "source": "news.example.com", "published": 0, "via": "bing_news"}])
    monkeypatch.setattr(sources, "bing_news", lambda q, http=None, limit=10: [])
    monkeypatch.setattr(sources, "google_news", lambda q, http=None, limit=10: [])
    monkeypatch.setattr(drafter, "fetch_article", lambda url, http=None, check_host=True: dict(ARTICLE))
    monkeypatch.setattr(reader, "read_url", lambda url, http=None, x_bearer="": {**ARTICLE, "via": "direct", "post": None, "media": [], "media_note": ""})
    monkeypatch.setattr(reader, "read_article", lambda url, http=None: {**ARTICLE, "url": url})
    monkeypatch.setattr(netguard, "_host_ok", lambda host: True)
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-chat", "csrf-chat"); st.update_session("sid-chat", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True


def _page(chromium, base, w, h):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-chat", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    ctx.route(re.compile(r".*/api/radar.*"), lambda r: r.fulfill(json={"stories": [STORY]}))
    page = ctx.new_page()
    page.set_default_timeout(30000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(350)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def _no_overflow(page, w):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w + 1, f"horizontal overflow at {w}px: {sw}"


def _settled(page):
    """Wait until the run has finished and its post cards stopped redrawing. The poll redraws #packages while the run
    works, and a card scrolled to or clicked mid-redraw was "not attached to the DOM" (flaky at 390 px)."""
    page.wait_for_function("() => !document.getElementById('runView').classList.contains('hc-busy')", timeout=90000)
    page.evaluate("""() => new Promise((ok) => { const box = document.getElementById('packages'); let t = setTimeout(done, 400);
        const mo = new MutationObserver(() => { clearTimeout(t); t = setTimeout(done, 400); }); mo.observe(box, {childList: true, subtree: true});
        function done() { mo.disconnect(); ok(); } setTimeout(done, 5000); })""")


def _scroll_to(loc, tries=3):
    for i in range(tries):
        try:
            return loc.scroll_into_view_if_needed(timeout=5000)
        except Exception:  # redrawn under us: find it again
            if i == tries - 1:
                raise


def _long_thread(app, rid):
    """A thread that ran long: 16 turns, the rights decided early on, compacted server-side."""
    st = app.state.store
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    th = res.get("thread") or [{"role": "user", "text": "Post this", "at": 1}]
    th.append({"role": "user", "text": "Yes, I have the rights to repost it with credit", "at": 2})
    for i in range(14):
        th.append({"role": "agent" if i % 2 else "user", "text": (f"Make change number {i}" if i % 2 == 0 else f"Done: change {i - 1} is in."), "at": 3 + i})
    res["thread"] = th
    res["rights"] = {"mode": "news_credit"}
    from studio import chat_context
    res["thread_summary"] = chat_context.summarize({"result": res})
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), rid))


def test_chat_thread_and_settings_1280(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("mp-discover").wait_for()
    assert page.get_by_test_id("mp-stepper").is_hidden()  # the 3-step stepper is replaced by the chat
    page.get_by_test_id("mp-disc-use").first.wait_for()
    _shot(page, "01-discover-1280")
    # start a thread from Discover; Enter sends
    page.get_by_test_id("mp-disc-use").first.click()
    q = page.get_by_test_id("agent-query")
    assert ARTICLE["url"] in q.input_value()
    q.press("End"); q.press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    page.wait_for_function("() => document.getElementById('tab-agent').classList.contains('mp-in-thread')")
    assert page.get_by_test_id("chat-sidebar").is_visible()
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(1)
    # the steps: one folded row
    assert not page.get_by_test_id("mp-trace").evaluate("d => d.open")
    assert re.search(r"(Worked through|Stopped after) \d+ steps?", page.locator("#mpTraceSum").inner_text())
    # the post cards inline, with Preview; the Ready to send bar; the model; message actions
    assert page.get_by_test_id("pkg-preview").count() == page.get_by_test_id("package").count() >= 1
    assert "ready to send" in page.get_by_test_id("chat-ready-text").inner_text()
    assert page.get_by_test_id("chat-model").inner_text().strip()
    assert page.locator("[data-testid=mp-user-msg] [data-testid=chat-copy]").count() == 1
    assert page.locator("[data-testid=mp-user-msg] [data-testid=chat-edit]").count() == 1
    page.evaluate("window.scrollTo(0, 0)")
    _shot(page, "02-thread-1280")
    page.get_by_test_id("mp-trace-toggle").click()
    assert page.get_by_test_id("mp-trace").evaluate("d => d.open")
    page.get_by_test_id("mp-trace-toggle").click()
    # Preview side panel; Esc closes it and gives focus back
    page.get_by_test_id("pkg-preview").first.click()
    pw.expect(page.get_by_test_id("chat-preview-panel")).to_be_visible()
    assert page.get_by_test_id("chat-preview-caption").inner_text().strip()
    _shot(page, "03-preview-1280")
    page.keyboard.press("Escape")
    pw.expect(page.get_by_test_id("chat-preview-panel")).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.testid") == "pkg-preview"
    # the context drawer: what the agent knows
    page.get_by_test_id("chat-context").click()
    page.get_by_test_id("ctx-compaction").wait_for()
    for tid in ("ctx-source", "ctx-rights", "ctx-kit", "ctx-template", "ctx-channels", "ctx-rules"):
        assert page.get_by_test_id(tid).is_visible(), tid
    assert "news.example.com" in page.get_by_test_id("ctx-source").inner_text() or "Acme" in page.get_by_test_id("ctx-source").inner_text()
    _shot(page, "04-context-1280")
    page.keyboard.press("Escape")
    pw.expect(page.get_by_test_id("chat-context-drawer")).to_be_hidden()
    # Shift+Enter is a new line, not a send
    ri = page.get_by_test_id("reply-input")
    n = page.get_by_test_id("thread-msg").count()
    ri.click(); ri.type("one"); ri.press("Shift+Enter"); ri.type("two")
    assert ri.input_value() == "one\ntwo" and page.get_by_test_id("thread-msg").count() == n
    ri.fill("")
    # an older thread, opened from the sidebar; rename it; a long thread says it was summarized
    first = page.evaluate("window.H_STATE.run.id")
    second = page.evaluate("api('/api/agent/runs', {method: 'POST', body: {query: 'AI agents', format: 'carousel'}}).then(r => r.id)")
    _long_thread(app, first)
    st = app.state.store  # a headline that tries to run script stays text
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (second,))[0]["result"])
    res.setdefault("story", {})["headline"] = '<img src=x onerror="window.__xss=1">Robots <b>win</b>'
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), second))
    page.evaluate("loadRuns()")
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(2)
    assert page.locator("#hcList img, #hcList b").count() == 0 and page.evaluate("window.__xss") is None
    page.locator(f"#hcList [data-open='{first}']").click()
    page.get_by_test_id("chat-summarized").wait_for()
    assert "Earlier messages summarized" in page.get_by_test_id("chat-summarized").inner_text()
    assert page.locator("#hcList .hc-li.on").get_attribute("data-id") == str(first)
    page.get_by_test_id("chat-summarized").scroll_into_view_if_needed()
    _shot(page, "05-summarized-1280")
    page.get_by_test_id("chat-summarized-toggle").click()
    pw.expect(page.locator("#agentThread .msg.hc-old").first).to_be_hidden()
    page.get_by_test_id("chat-context").click()
    page.get_by_test_id("ctx-summary").wait_for(state="attached")
    assert "summarized" in page.get_by_test_id("ctx-compaction").inner_text()
    assert "rights" in page.get_by_test_id("ctx-rights").inner_text().lower()
    page.keyboard.press("Escape")
    page.locator(f"#hcList [data-rename='{first}']").click()
    page.get_by_test_id("chat-rename-input").fill("Travel agent launch")
    page.get_by_test_id("chat-rename-input").press("Enter")
    pw.expect(page.get_by_test_id("chat-title")).to_have_text("Travel agent launch")
    pw.expect(page.locator(f"#hcList [data-open='{first}'] .hc-th-t")).to_have_text("Travel agent launch")
    page.get_by_test_id("chat-search").fill("travel agent")
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(1)
    page.get_by_test_id("chat-search").fill("")
    _shot(page, "06-sidebar-renamed-1280")
    _no_overflow(page, 1280)
    # Settings: a left nav, one page each, deep links
    page.goto(base + "/?tab=settings&sub=ai")
    page.get_by_test_id("ai-engine-picker").wait_for()
    pw.expect(page.get_by_test_id("settings-tab-ai")).to_have_attribute("aria-selected", "true")
    assert page.get_by_test_id("settings-integrations").is_hidden() and page.get_by_test_id("settings-account").is_hidden()
    _shot(page, "07-settings-ai-1280")
    page.get_by_test_id("settings-tab-integrations").click()
    page.get_by_test_id("integrations").wait_for()
    assert "sub=integrations" in page.url and page.get_by_test_id("ai-engine-picker").is_hidden()
    _shot(page, "08-settings-integrations-1280")
    page.get_by_test_id("settings-tab-integrations").focus(); page.keyboard.press("ArrowDown")
    pw.expect(page.get_by_test_id("settings-tab-brand")).to_have_attribute("aria-selected", "true")
    for sub in ("account", "privacy"):
        page.get_by_test_id(f"settings-tab-{sub}").click()
        pw.expect(page.get_by_test_id(f"settings-{sub}")).to_be_visible()
    page.goto(base + "/?tab=settings&sub=integ-ph")  # an old deep link lands on Integrations
    pw.expect(page.get_by_test_id("settings-tab-integrations")).to_have_attribute("aria-selected", "true")
    page.goto(base + "/?tab=settings")
    pw.expect(page.get_by_test_id("settings-account")).to_be_visible()
    assert page.get_by_test_id("settings-disconnect").is_visible()
    _no_overflow(page, 1280)
    assert not errors, errors


def test_chat_thread_and_settings_390(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 390, 844)
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    assert page.get_by_test_id("chat-sidebar").is_hidden()  # a drawer on a phone
    assert page.get_by_test_id("reply-go").is_visible() and page.get_by_test_id("reply-upload").is_visible()
    page.evaluate("window.scrollTo(0, 0)")
    _no_overflow(page, 390)
    _shot(page, "09-thread-390")
    page.get_by_test_id("chat-sidebar-open").click()
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_visible()
    _shot(page, "10-sidebar-390")
    page.keyboard.press("Escape")
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_hidden()
    _scroll_to(page.get_by_test_id("package").first)
    _shot(page, "11-card-390")
    page.get_by_test_id("pkg-preview").first.click()
    pw.expect(page.get_by_test_id("chat-preview-panel")).to_be_visible()
    box = page.get_by_test_id("chat-preview-panel").bounding_box()
    assert box and box["width"] <= 390
    _shot(page, "12-preview-390")
    page.keyboard.press("Escape")
    page.get_by_test_id("chat-context").click()
    page.get_by_test_id("ctx-compaction").wait_for()
    _shot(page, "13-context-390")
    page.keyboard.press("Escape")
    page.goto(base + "/?tab=settings&sub=ai")
    page.get_by_test_id("ai-engine-picker").wait_for()
    _no_overflow(page, 390)
    _shot(page, "14-settings-ai-390")
    page.get_by_test_id("settings-tab-integrations").click()
    page.get_by_test_id("integrations").wait_for()
    _no_overflow(page, 390)
    _shot(page, "15-settings-integrations-390")
    assert not errors, errors
