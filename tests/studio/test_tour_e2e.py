"""The guided tour of the sample workspace (Playwright): it opens by itself on the first /demo visit, walks seven steps
over the real screens with the mouse and the keyboard, can be skipped, doesn't open again in that browser, and reopens
from "Take the tour". Desktop and a 390 px phone. Skips without Chromium."""
import pytest

pytest.importorskip("playwright.sync_api")
TABS = ["inbox", "agent", "take", "planner", "brand", "agents"]  # the tab each of steps 1-6 shows


def _ctx(chromium, base, width):
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 780}, has_touch=width < 1024)
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic
    return ctx


def _step(page, n):
    page.locator(f"[data-testid=tour][data-step='{n}']").wait_for()
    assert page.get_by_test_id("tour-step").inner_text() == f"Step {n} of 7"


@pytest.mark.parametrize("width", [1280, 390])
def test_tour_opens_on_first_visit_and_walks_seven_steps(demo_live, chromium, width):
    base, buffer_calls = demo_live
    ctx = _ctx(chromium, base, width)
    try:
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/demo")
        _step(page, 1)  # opened by itself
        assert page.get_by_test_id("tour-title").inner_text() == "Your Inbox"
        assert page.locator("#tab-inbox").is_visible()
        page.wait_for_function("document.activeElement && document.activeElement.dataset.testid === 'tour-next'")
        for n, tab in enumerate(TABS[1:], start=2):
            page.get_by_test_id("tour-next").click()
            _step(page, n)
            assert page.locator(f"#tab-{tab}").is_visible(), (n, tab)
        page.get_by_test_id("tour-back").click()
        _step(page, 5)
        page.keyboard.press("ArrowRight")
        _step(page, 6)
        page.keyboard.press("ArrowRight")
        _step(page, 7)
        assert page.get_by_test_id("tour-connect").get_attribute("href") == "/login"
        assert page.get_by_test_id("tour-skip").count() == 0
        page.get_by_test_id("tour-done").click()
        assert page.get_by_test_id("tour").count() == 0
        assert page.evaluate("localStorage.getItem('hq_tour_done')") == "1"
        assert page.get_by_test_id("sample-note").count() >= len(TABS)  # every screen says it is sample data
        page.reload()
        page.locator("#app:not([hidden])").wait_for()
        page.wait_for_timeout(800)
        assert page.get_by_test_id("tour").count() == 0  # once per browser
        assert not errors, errors
    finally:
        ctx.close()
    assert buffer_calls == []


def test_tour_keyboard_skip_and_reopen(demo_live, chromium):
    base, _ = demo_live
    ctx = _ctx(chromium, base, 1280)
    try:
        page = ctx.new_page()
        page.goto(base + "/demo")
        _step(page, 1)
        page.wait_for_function("document.activeElement && document.activeElement.dataset.testid === 'tour-next'")
        page.keyboard.press("ArrowRight")
        _step(page, 2)
        page.keyboard.press("ArrowLeft")
        _step(page, 1)
        page.keyboard.press("Escape")  # skip
        page.get_by_test_id("tour").wait_for(state="detached")
        assert page.evaluate("document.activeElement.id") == "tourOpen"  # focus returns to the sample bar
        page.get_by_test_id("tour-open").click()  # Take the tour
        _step(page, 1)
        page.get_by_test_id("tour-skip").click()
        page.get_by_test_id("tour").wait_for(state="detached")
    finally:
        ctx.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_sample_note_never_overlaps_the_page_subtitle(demo_live, chromium, width):
    """The "Sample data" note sits between each page title and its subtitle; on a phone the subtitle used to slide up
    under it (seen on Activity)."""
    base, _ = demo_live
    ctx = _ctx(chromium, base, width)
    try:
        page = ctx.new_page()
        page.add_init_script("localStorage.setItem('hq_tour_done','1')")
        page.goto(base + "/demo")
        page.locator("[data-testid=sample-note]").first.wait_for(state="attached")
        hits = page.evaluate("""() => {
          const out = [];
          document.querySelectorAll('.tab').forEach((t) => {
            const was = t.hidden; t.hidden = false;
            const n = t.querySelector('[data-testid=sample-note]'), s = n && n.nextElementSibling;
            if (n && s) {
              const a = n.getBoundingClientRect(), b = s.getBoundingClientRect();
              if (a.height && b.height && a.bottom > b.top + 0.5 && b.bottom > a.top && a.right > b.left + 0.5 && b.right > a.left) out.push(t.id + ': ' + Math.round(a.bottom - b.top) + 'px');
            }
            t.hidden = was;
          });
          return out;
        }""")
        assert not hits, hits
    finally:
        ctx.close()
