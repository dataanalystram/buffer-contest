"""The chat's model menu, Back inside the app, the working message and plain errors, in a real browser (4 Nov 2026).

Bugs this pins: clicking the model pill navigated to Settings (it was a data-go link); Back from Settings opened Buffer
(every tab change used history.replaceState, so the entry before the app was Buffer's OAuth page); the only sign of work
was a small spinner; a follow-up showed "Unexpected error: AttributeError". Runs at 1280 and 390 px, signed in (a fake
Buffer) and in the sample workspace (/demo). The browser test skips if Chromium can't start."""
import re

import pytest

from studio import free_engines

from .test_chat_ui_e2e import _page, _settled, live  # noqa: F401

pw = pytest.importorskip("playwright.sync_api")


def _thread(page):
    """A finished thread on screen (the agent runs synchronously in this fixture)."""
    page.wait_for_function("() => document.body.dataset.booted === '1'")
    page.evaluate("api('/api/agent/runs', {method: 'POST', body: {query: 'AI agents', format: 'carousel'}}).then((r) => { showTab('agent'); window.showRun(r); })")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    page.get_by_test_id("chat-composer-model").wait_for()


def _no_buffer(page):
    seen = []
    page.on("framenavigated", lambda f: seen.append(f.url) if f == page.main_frame else None)
    page.on("request", lambda r: seen.append(r.url) if r.is_navigation_request() else None)
    return seen


@pytest.mark.parametrize("w,h", [(1280, 900), (390, 844)])
def test_model_menu_opens_in_place_picks_and_back_stays_in_the_app(live, chromium, close_contexts, monkeypatch, w, h):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, w, h)
    navs = _no_buffer(page)
    page.goto(base + "/?tab=agent")
    _thread(page)
    # NVIDIA and Hugging Face are set up on this "server"; Gemini (AI Studio) is not, so it is disabled with a key link
    monkeypatch.setattr(free_engines, "configured", lambda eid: eid in ("nvidia", "hf"))
    url0 = page.url
    pill = page.get_by_test_id("chat-composer-model")
    before = pill.inner_text().strip()
    pill.click()
    menu = page.get_by_test_id("chat-model-menu")
    pw.expect(menu).to_be_visible()
    page.locator("#hcModelMenu [data-eng=nvidia]").wait_for()
    assert page.url == url0 and page.get_by_test_id("tab-agent").count() >= 0
    assert page.locator("#tab-agent").is_visible() and page.locator("#tab-settings").is_hidden()
    assert pill.get_attribute("aria-expanded") == "true"
    assert page.locator("#hcModelMenu [aria-checked=true]").count() == 1  # the current one is checked (Automatic)
    gem = page.locator("#hcModelMenu [data-eng=gemini-studio]")
    assert gem.get_attribute("aria-disabled") == "true" and gem.inner_text().strip()
    assert page.locator("#hcModelMenu [data-testid=chat-model-addkey]").count() >= 1
    if w <= 600:
        assert "sheet" in (menu.get_attribute("class") or "")
        assert page.evaluate("() => document.documentElement.scrollWidth") <= w + 1
        bb = menu.bounding_box()
        assert bb["x"] >= 0 and bb["x"] + bb["width"] <= w + 1
    # keyboard: focus starts on the checked item, arrows move, Esc closes and gives focus back to the pill
    assert page.evaluate("document.activeElement.getAttribute('role')") == "menuitemradio"
    first = page.evaluate("document.activeElement.dataset.eng")
    page.keyboard.press("ArrowDown")
    assert page.evaluate("document.activeElement.dataset.eng") != first
    page.keyboard.press("Escape")
    pw.expect(menu).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.testid") == "chat-composer-model"
    # pick NVIDIA: saved with PUT /api/ai/engine, the pill names it, the URL never changed
    pill.click()
    page.locator("#hcModelMenu [data-eng=nvidia]").click()
    pw.expect(menu).to_be_hidden()
    pw.expect(pill).to_contain_text("NVIDIA")
    assert pill.inner_text().strip() != before or "NVIDIA" in before
    assert page.evaluate("api('/api/ai/engine').then((s) => s.selected)") == "nvidia"
    assert page.url == url0
    # the Settings link in the menu, then Back: the chat again, never Buffer
    pill.click()
    page.locator("#hcModelMenu [data-testid=chat-model-addkey]").first.click()
    pw.expect(page.locator("#tab-settings")).to_be_visible()
    assert "tab=settings" in page.url
    page.go_back()
    pw.expect(page.locator("#tab-agent")).to_be_visible()
    pw.expect(page.locator("#tab-settings")).to_be_hidden()
    assert "tab=agent" in page.url and page.url.startswith(base)
    pw.expect(page.get_by_test_id("chat-composer-model")).to_be_visible()
    # Forward goes back to Settings; Back again returns to the chat; every tab change made its own entry
    page.go_forward()
    pw.expect(page.locator("#tab-settings")).to_be_visible()
    page.go_back()
    pw.expect(page.locator("#tab-agent")).to_be_visible()
    page.evaluate("showTab('planner')")
    pw.expect(page.locator("#tab-planner")).to_be_visible()
    page.go_back()
    pw.expect(page.locator("#tab-agent")).to_be_visible()
    assert not [u for u in navs if "buffer.com" in u], navs
    assert not errors, errors


def test_working_message_appears_updates_and_clears_and_errors_read_plainly(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    page.goto(base + "/?tab=agent")
    _thread(page)
    work = page.get_by_test_id("chat-working")
    assert work.is_hidden()
    page.evaluate("""() => { const r = window.H_STATE.run; window.showRun({...r, status: 'running', created: Date.now() / 1000 - 5,
        steps: [...(r.steps || []).map((s) => ({...s, status: 'done'})), {key: 'sx', tool: 'render_carousel', label: 'Swap the cover', status: 'running', detail: ''}]}); }""")
    pw.expect(work).to_be_visible()
    pw.expect(page.get_by_test_id("chat-working-step")).to_contain_text(re.compile(r"Step \d+ of \d+ · Swap the cover"))
    pw.expect(page.get_by_test_id("chat-working-stop")).to_be_visible()
    pw.expect(page.get_by_test_id("chat-working-time")).to_have_text(re.compile(r"\d+s"))
    assert page.get_by_test_id("chat-working-slow").is_hidden()
    # it is the last thing above the composer, and the page followed it down
    page.wait_for_timeout(400)
    bb = work.bounding_box()
    assert bb and bb["y"] + bb["height"] <= 900 + 2, bb
    page.evaluate("window.HQ_CHAT_WORK.stale()")
    pw.expect(page.get_by_test_id("chat-working-slow")).to_be_visible()
    assert "this step can take a minute" in page.get_by_test_id("chat-working-slow").inner_text()
    # scrolled up: Jump to latest
    page.evaluate("window.scrollTo(0, 0)")
    page.wait_for_timeout(200)
    if page.evaluate("document.documentElement.scrollHeight > innerHeight + 200"):
        pw.expect(page.get_by_test_id("chat-jump-latest")).to_be_visible()
        page.get_by_test_id("chat-jump-latest").click()
        pw.expect(page.get_by_test_id("chat-jump-latest")).to_be_hidden()
    # done: it clears
    page.evaluate("() => { const r = window.H_STATE.run; window.showRun({...r, status: 'ready', steps: r.steps.map((s) => ({...s, status: 'done'}))}); }")
    pw.expect(work).to_be_hidden()
    pw.expect(page.get_by_test_id("chat-jump-latest")).to_be_hidden()
    # an old run that saved a bare exception class reads as plain words, with Retry
    page.evaluate("""() => { const r = window.H_STATE.run, res = r.result || {};
        window.showRun({...r, status: 'failed', result: {...res, thread: [...(res.thread || []), {role: 'user', text: 'Make the hook stronger', at: 1},
          {role: 'agent', text: 'Unexpected error: AttributeError', at: 2}]}}); }""")
    pw.expect(page.get_by_test_id("chat-retry")).to_be_visible()
    txt = page.locator("#runView").inner_text()
    assert "AttributeError" not in txt and "Unexpected error" not in txt
    assert "Something went wrong" in txt
    assert not errors, errors


def test_demo_model_menu_and_back(demo_live, chromium, close_contexts):
    base, calls = demo_live
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script("localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')")
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(30000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    navs = _no_buffer(page)
    page.goto(base + "/demo")
    page.wait_for_function("() => document.body.dataset.booted === '1'")
    page.evaluate("showTab('agent')")
    page.evaluate("api('/api/agent/runs', {method: 'POST', body: {query: 'AI agents', format: 'carousel'}}).then((r) => window.showRun(r))")
    page.get_by_test_id("chat-composer-model").wait_for()
    url0 = page.url
    page.get_by_test_id("chat-composer-model").click()
    menu = page.get_by_test_id("chat-model-menu")
    pw.expect(menu).to_be_visible()
    page.locator("#hcModelMenu [data-eng=nvidia]").wait_for()
    assert page.url == url0 and page.locator("#tab-agent").is_visible()
    # the sample workspace runs no hosted model: those are disabled and say why
    nv = page.locator("#hcModelMenu [data-eng=nvidia]")
    assert nv.get_attribute("aria-disabled") == "true" and "sample" in nv.inner_text().lower()
    page.keyboard.press("Escape")
    pw.expect(menu).to_be_hidden()
    page.evaluate("showTab('settings', 'ai')")
    pw.expect(page.locator("#tab-settings")).to_be_visible()
    page.go_back()
    pw.expect(page.locator("#tab-agent")).to_be_visible()
    assert page.url.startswith(base)
    assert not [u for u in navs if "buffer.com" in u], navs
    assert not errors, errors
