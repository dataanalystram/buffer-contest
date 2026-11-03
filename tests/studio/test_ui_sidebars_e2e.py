"""Independently collapsible sidebars in a real browser (owner, 3 Nov 2026), at 1280 and 390 px.

The main sidebar collapses to a 64 px icon rail with tooltips; the threads column has its own toggle in the chat bar
(aria-expanded) and the chat widens when it is hidden; both choices survive a reload; on a phone the threads open as an
overlay (scrim, Esc, outside click, focus trap) instead of squeezing the conversation. The selected thread and the
Settings nav are neutral (no purple fill), the composer draft survives every toggle, nothing in the composer overlaps,
"0 steps" is never shown, and prefers-reduced-motion turns the motion off. Screenshots go to shots/uisidebars/."""
import pathlib
import re

import pytest

from .test_chat_ui_e2e import ARTICLE, _no_overflow, _page, live  # noqa: F401  (live is a fixture)

pw = pytest.importorskip("playwright.sync_api")
SHOTS = pathlib.Path(__file__).resolve().parents[2] / "shots" / "uisidebars"


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def _thread(page):
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    page.wait_for_function("() => !document.getElementById('runView').classList.contains('hc-busy')", timeout=90000)


def _width(page, sel):
    return page.evaluate(f"() => document.querySelector({sel!r}).getBoundingClientRect().width")


def _neutral(rgb):
    """A background with no hue to speak of (a grey or transparent): no lavender, no pink."""
    nums = [float(x) for x in re.findall(r"[\d.]+", rgb)[:4]]
    if len(nums) == 4 and nums[3] == 0:
        return True
    r, g, b = nums[:3]
    return max(r, g, b) - min(r, g, b) <= 12


def _no_overlap(page, sels):
    boxes = page.evaluate("""(sels) => sels.map((s) => { const n = document.querySelector(s); if (!n || !n.offsetParent) return null;
        const r = n.getBoundingClientRect(); return [s, r.left, r.top, r.right, r.bottom]; }).filter(Boolean)""", sels)
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            ix = min(a[3], b[3]) - max(a[1], b[1])
            iy = min(a[4], b[4]) - max(a[2], b[2])
            assert ix <= 0.5 or iy <= 0.5, f"{a[0]} overlaps {b[0]}: {a} {b}"
    return boxes


COMPOSER = ["[data-testid=reply-input]", "[data-testid=reply-upload]", "[data-testid=chat-composer-model]", "[data-testid=reply-go]", "#replyChips"]


def test_sidebars_1280(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    page.goto(base + "/?tab=agent")
    _thread(page)
    rid = page.evaluate("window.H_STATE.run.id")
    _shot(page, "01-thread-1280")
    # ---- the main sidebar: 232 px -> a 64 px icon rail with tooltips, and back
    tog = page.get_by_test_id("nav-collapse")
    pw.expect(tog).to_have_attribute("aria-expanded", "true")
    assert _width(page, "#rail") > 200
    tog.click()
    pw.expect(tog).to_have_attribute("aria-expanded", "false")
    page.wait_for_timeout(300)
    assert abs(_width(page, "#rail") - 64) <= 1
    assert page.get_by_test_id("tab-agent").get_attribute("aria-current") == "page"
    page.get_by_test_id("tab-planner").hover()
    tip = page.get_by_test_id("nav-tooltip")
    pw.expect(tip).to_be_visible()
    assert tip.inner_text().startswith("Planner")
    assert page.evaluate("() => document.querySelector('[data-testid=tab-planner]').textContent.includes('Planner')")  # the name stays for screen readers
    _shot(page, "02-rail-collapsed-1280")
    page.mouse.move(700, 450)
    pw.expect(tip).to_be_hidden()
    # ---- the threads column: its own toggle in the chat bar; the chat widens; the draft, the thread and the scroll stay
    tt = page.get_by_test_id("chat-sidebar-open")
    pw.expect(tt).to_have_attribute("aria-expanded", "true")
    assert page.get_by_test_id("chat-sidebar").is_visible()
    ri = page.get_by_test_id("reply-input")
    ri.fill("Make the hook about the one-approval part")
    w_open = _width(page, "#hcMain")
    page.evaluate("window.scrollTo(0, 400)")
    y0 = page.evaluate("scrollY")
    tt.click()
    pw.expect(tt).to_have_attribute("aria-expanded", "false")
    pw.expect(tt).to_have_attribute("aria-label", "Show threads")
    page.wait_for_timeout(300)
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_hidden()
    assert _width(page, "#hcMain") > w_open + 100, (w_open, _width(page, "#hcMain"))
    assert ri.input_value() == "Make the hook about the one-approval part"
    assert page.evaluate("window.H_STATE.run.id") == rid
    assert abs(page.evaluate("scrollY") - y0) < 400  # kept in place (the anchor), never thrown back to the top
    _shot(page, "03-threads-hidden-1280")
    # ---- both choices survive a reload, the draft too (it belongs to its thread)
    page.reload()
    page.get_by_test_id("agent-query").wait_for()
    assert page.evaluate("document.documentElement.classList.contains('rail-collapsed') && document.documentElement.classList.contains('threads-hidden')")
    assert abs(_width(page, "#rail") - 64) <= 1
    page.evaluate(f"api('/api/agent/runs/{rid}').then((x) => window.showRun(x))")
    page.get_by_test_id("package").first.wait_for()
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_hidden()
    pw.expect(page.get_by_test_id("reply-input")).to_have_value("Make the hook about the one-approval part")
    page.get_by_test_id("chat-sidebar-open").click()
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_visible()
    page.get_by_test_id("nav-collapse").click()
    page.wait_for_timeout(300)
    assert _width(page, "#rail") > 200
    # ---- the selected thread: an outline on a neutral background, never a purple fill
    on = page.locator("#hcList .hc-li.on")
    pw.expect(on).to_have_count(1)
    st = on.evaluate("n => { const c = getComputedStyle(n); return [c.backgroundColor, c.boxShadow]; }")
    assert _neutral(st[0]) and "inset" in st[1], st
    # ---- the composer: one prompt box, nothing overlapping; the section heading is never under the dock
    _no_overlap(page, COMPOSER)
    page.get_by_test_id("packages-head").scroll_into_view_if_needed()
    page.evaluate("document.querySelector('[data-testid=packages-head]').scrollIntoView({block: 'end'})")
    page.wait_for_timeout(150)
    hit = page.evaluate("""() => { const h = document.querySelector('[data-testid=packages-head]'), r = h.getBoundingClientRect();
        const n = document.elementFromPoint(r.left + 20, r.top + r.height / 2); return !!n && (n === h || h.contains(n)); }""")
    assert hit, "the dock covers 'Your posts — review and send'"
    assert "…" not in " ".join(page.get_by_test_id("mp-ready-meta").all_inner_texts())
    assert page.get_by_test_id("chat-composer-model").inner_text().strip()
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _shot(page, "04-composer-1280")
    # ---- the steps: never "0 steps"; Thinking… while a stepless run works
    trace = page.get_by_test_id("mp-trace")
    assert re.search(r"Worked through [1-9]\d* steps?", page.locator("#mpTraceSum").inner_text())
    page.evaluate("window.showRun({...window.H_STATE.run, steps: []})")
    page.wait_for_timeout(100)
    pw.expect(trace).to_be_hidden()
    assert "0 steps" not in page.locator("#runView").inner_text()
    page.evaluate("window.showRun({...window.H_STATE.run, status: 'running', steps: [], created: Date.now() / 1000 - 5})")
    page.wait_for_timeout(100)
    pw.expect(trace).to_be_visible()
    assert page.locator("#mpTraceSum").inner_text() == "Thinking…"
    assert re.match(r"\d+s", page.get_by_test_id("mp-trace-time").inner_text())
    page.evaluate(f"api('/api/agent/runs/{rid}').then((x) => window.showRun(x))")
    page.wait_for_function("() => /Worked through/.test(document.getElementById('mpTraceSum').textContent)")
    # ---- Settings: the same neutral selection
    page.goto(base + "/?tab=settings&sub=ai")
    page.get_by_test_id("ai-engine-picker").wait_for()
    sel = page.get_by_test_id("settings-tab-ai").evaluate("n => getComputedStyle(n).backgroundColor")
    assert _neutral(sel), sel
    _shot(page, "05-settings-1280")
    _no_overflow(page, 1280)
    assert not errors, errors


def test_sidebars_390(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 390, 844)
    page.goto(base + "/?tab=agent")
    _thread(page)
    assert page.get_by_test_id("nav-collapse").is_hidden()  # the bottom bar has no collapse
    side, tt = page.get_by_test_id("chat-sidebar"), page.get_by_test_id("chat-sidebar-open")
    assert side.is_hidden()
    w0 = _width(page, "#hcMain")
    ri = page.get_by_test_id("reply-input")
    ri.fill("shorter please")
    tt.click()
    pw.expect(side).to_be_visible()
    pw.expect(tt).to_have_attribute("aria-expanded", "true")
    assert side.get_attribute("role") == "dialog" and side.get_attribute("aria-modal") == "true"
    pw.expect(page.get_by_test_id("chat-sidebar-scrim")).to_be_visible()
    assert abs(_width(page, "#hcMain") - w0) <= 1  # an overlay: the conversation keeps its width
    _shot(page, "06-threads-overlay-390")
    # focus stays inside the panel
    for _ in range(8):
        page.keyboard.press("Tab")
        assert page.evaluate("document.getElementById('hcSide').contains(document.activeElement)")
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.getElementById('hcSide').contains(document.activeElement)")
    page.keyboard.press("Escape")
    pw.expect(side).to_be_hidden()
    assert page.evaluate("document.activeElement.dataset.testid") == "chat-sidebar-open"
    tt.click()
    pw.expect(side).to_be_visible()
    page.mouse.click(375, 420)  # the scrim, outside the panel
    pw.expect(side).to_be_hidden()
    pw.expect(tt).to_have_attribute("aria-expanded", "false")
    assert ri.input_value() == "shorter please"
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    _no_overlap(page, COMPOSER)
    _shot(page, "07-composer-390")
    _no_overflow(page, 390)
    assert not errors, errors


def test_sidebars_reduced_motion(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    page.emulate_media(reduced_motion="reduce")
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").wait_for()
    page.wait_for_function("document.documentElement.classList.contains('hq-anim')")
    assert page.evaluate("getComputedStyle(document.getElementById('rail')).transitionDuration").split(",")[0].strip() in ("0s", "")
    page.get_by_test_id("nav-collapse").click()
    assert abs(_width(page, "#rail") - 64) <= 1  # at once, no animation
    page.get_by_test_id("nav-collapse").click()
    assert not errors, errors
