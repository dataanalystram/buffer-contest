"""Loading states + adaptive layout in a real browser (owner ask, 4 Nov 2026). Buffer is FakeBuffer; nothing is posted.

Covers: a rapid Library -> Bin switch while the Library answer is still in flight never shows Library cards in the Bin;
a filter change shows the skeleton at once, then only matching results; the multi-channel review tabs switch the card and
the count never reads "undefined"; the labelled Hide threads toggle (aria-expanded, remembered); the Planner No date panel
(count, All / Drafts / Ready chips, rows); no sideways scroll at 390 / 1280 / 1920 on the touched screens.
Slow answers are made in the page (a fetch wrapper that holds /api/library for a moment), never on the server."""
import pytest

from .test_library_bin_e2e import _ctx, _library, live  # noqa: F401  (fixture)

pw = pytest.importorskip("playwright.sync_api")

SLOW_LIB = """(() => { const f = window.fetch; window.fetch = (u, o) => { const p = f(u, o);
  if (window.__slowLib && String(u).startsWith('/api/library?')) return new Promise((ok, no) => { const t = setTimeout(() => p.then(ok, no), window.__slowLib);
    if (o && o.signal) o.signal.addEventListener('abort', () => { clearTimeout(t); no(new DOMException('Aborted', 'AbortError')); }); });
  return p; }; })()"""


def _no_overflow(page):
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("w", [1280, 390])
def test_rapid_library_to_bin_never_shows_library_cards(live, chromium, close_contexts, w):
    ctx, page, errors = _ctx(chromium, live["base"], w)
    ctx.add_init_script(SLOW_LIB)
    _library(page, live["base"])
    page.get_by_test_id("library-item").first.wait_for()
    # put an item in the Bin, so the Bin has something of its own
    page.evaluate(f"() => api('/api/library/{live['ups'][0]}?mode=bin&force=1', {{ method: 'DELETE' }})")
    # record every Library card that ever lands in the grid after the Bin was asked for
    page.evaluate("""() => { window.__slowLib = 900; window.__leak = 0; window.__binAt = 0;
      new MutationObserver(() => { if (window.__binAt && document.querySelector('#libGrid [data-testid=library-item]')) window.__leak++; })
        .observe(document.getElementById('libGrid'), { childList: true, subtree: true }); }""")
    page.locator("#libTabs [data-type=image]").click()          # a Library request, held 900 ms
    page.wait_for_timeout(60)
    page.evaluate("() => { window.__binAt = performance.now(); document.querySelector('[data-testid=library-view-bin]').click(); }")
    page.get_by_test_id("bin-item").first.wait_for()
    page.wait_for_timeout(1300)                                  # the held Library answer would have landed by now
    assert page.evaluate("window.__leak") == 0
    assert page.get_by_test_id("library-item").count() == 0
    assert page.get_by_test_id("bin-item").count() >= 1
    if w == 390:
        _no_overflow(page)
    assert not errors, errors


def test_filter_change_shows_skeleton_then_only_matching(live, chromium, close_contexts):
    ctx, page, errors = _ctx(chromium, live["base"], 1280)
    ctx.add_init_script(SLOW_LIB)
    _library(page, live["base"])
    page.evaluate("() => { window.__slowLib = 400; }")
    t = page.evaluate("""() => new Promise((ok) => { const g = document.getElementById('libGrid'), t0 = performance.now(); let sk = null, stale = 0;
      const obs = new MutationObserver(() => { if (sk === null && g.querySelector('.lp-skel')) sk = performance.now() - t0; });
      obs.observe(g, { childList: true, subtree: true });
      document.querySelector('#libTabs [data-type=carousel]').click();
      if (g.querySelector('.lp-skel')) sk = performance.now() - t0;                       // drawn in the click itself
      stale = g.querySelectorAll('[data-testid=library-item]').length;                       // old cards left up at once?
      const done = () => { if (g.getAttribute('aria-busy') === 'false' && !g.querySelector('.lp-skel')) { obs.disconnect(); ok({ sk, stale, content: performance.now() - t0 }); } else setTimeout(done, 10); };
      setTimeout(done, 10); })""")
    assert t["sk"] is not None and t["sk"] < 100, t       # skeleton within 100 ms (it is drawn synchronously)
    assert t["stale"] == 0, t                             # the old view's cards are gone the moment the filter changes
    items = page.get_by_test_id("library-item")
    assert items.count() == 0 or all(k == "carousel" for k in page.evaluate(
        "() => [...document.querySelectorAll('[data-testid=library-item]')].map((e) => e.dataset.kind || 'carousel')"))
    assert page.locator("#libGrid .lp-skel").count() == 0
    assert not errors, errors


def _open_run(page, base, rid, w):
    page.goto(base + "/?tab=agent", wait_until="domcontentloaded")
    page.wait_for_function("() => typeof showRun === 'function' && typeof api === 'function'")
    page.evaluate(f"async () => showRun(await api('/api/agent/runs/{rid}'))")
    page.get_by_test_id("package").first.wait_for()


@pytest.mark.parametrize("w", [1920, 1280, 390])
def test_multichannel_tabs_and_threads_toggle(live, chromium, close_contexts, w):
    ctx, page, errors = _ctx(chromium, live["base"], w)
    _open_run(page, live["base"], live["rid"], w)
    tabs = page.get_by_test_id("pkg-tab")
    assert tabs.count() == 2
    assert tabs.nth(0).get_attribute("aria-selected") == "true"
    vis = lambda: page.locator("[data-testid=package]:visible")  # noqa: E731
    assert vis().count() == 1 and vis().first.get_attribute("data-ch") == "ch_li"
    tabs.nth(1).click()
    assert vis().count() == 1 and vis().first.get_attribute("data-ch") == "ch_fb"
    assert tabs.nth(1).get_attribute("aria-selected") == "true"
    tabs.nth(1).press("ArrowLeft")
    assert vis().first.get_attribute("data-ch") == "ch_li"
    for c in page.get_by_test_id("pkg-count").all_inner_texts():
        assert "undefined" not in c and "/" in c
    # the labelled threads toggle, always visible, its state in aria-expanded and remembered
    tog = page.get_by_test_id("chat-sidebar-open")
    assert tog.is_visible()
    if w > 1100:
        assert tog.get_attribute("aria-expanded") == "true" and "Hide threads" in tog.inner_text()
        main0 = page.locator("#hcMain").bounding_box()["width"]
        tog.click()
        page.wait_for_timeout(350)
        assert tog.get_attribute("aria-expanded") == "false" and "Show threads" in tog.inner_text()
        assert page.locator("#hcMain").bounding_box()["width"] > main0 + 150   # the review takes the room
        assert page.evaluate("localStorage.getItem('hq_threads')") == "hidden"
        page.reload(wait_until="domcontentloaded")
        _open_run(page, live["base"], live["rid"], w)
        assert page.get_by_test_id("chat-sidebar-open").get_attribute("aria-expanded") == "false"
        # wide: the post card puts media left and the caption right (both on one row)
        m = page.locator("[data-testid=package]:visible [data-testid=pkg-media]").bounding_box()
        t = page.locator("[data-testid=package]:visible [data-testid=pkg-text]").bounding_box()
        assert t["x"] > m["x"] + m["width"] - 1
        # no floating middle column: the conversation reaches near the right edge of the page
        mb = page.locator("#hcMain").bounding_box()
        assert mb["x"] + mb["width"] > w - 120
    else:
        tog.click()
        page.get_by_test_id("chat-sidebar").wait_for(state="visible")
        assert tog.get_attribute("aria-expanded") == "true"
        page.keyboard.press("Escape")
        assert tog.get_attribute("aria-expanded") == "false"
    _no_overflow(page)
    assert not errors, errors


@pytest.mark.parametrize("w", [1920, 390])
def test_planner_nodate_panel(live, chromium, close_contexts, w):
    live["fake"].posts.append({"id": "ig1", "text": "Reel on the feed", "status": "scheduled", "dueAt": "2026-11-20T10:00:00Z", "channelId": "ch_ig"})
    ctx, page, errors = _ctx(chromium, live["base"], w)
    ctx.add_init_script("localStorage.setItem('hq_planner', JSON.stringify({ view: 'week', nodate: '1' }))")
    page.goto(live["base"] + "/?tab=planner", wait_until="domcontentloaded")
    page.get_by_test_id("planner-nodate-item").first.wait_for()
    n = int(page.get_by_test_id("planner-nodate-count").inner_text())
    assert n == page.get_by_test_id("planner-nodate-item").count() >= 2
    chips = page.get_by_test_id("planner-nodate-filter")
    assert chips.all_inner_texts()[0].startswith("All")
    chips.nth(1).click()   # Drafts
    states = page.evaluate("() => [...document.querySelectorAll('[data-testid=planner-nodate-item]')].map((e) => e.dataset.state)")
    assert states and set(states) == {"draft"}
    chips.nth(2).click()   # Ready
    states = page.evaluate("() => [...document.querySelectorAll('[data-testid=planner-nodate-item]')].map((e) => e.dataset.state)")
    assert set(states) <= {"ready", "approval"}
    # the ⋯ menu carries the actions; the row keeps drag-to-schedule
    row = page.get_by_test_id("planner-nodate-item").first
    assert row.get_attribute("draggable") == "true"
    row.get_by_test_id("planner-row-menu").click()
    assert row.get_by_test_id("planner-reschedule").is_visible()
    # Grid view fills the width: more than 3 columns on a wide screen
    if w >= 1280:
        page.evaluate("() => plSetView('grid')")
        page.get_by_test_id("planner-grid").wait_for()
        cols = page.evaluate("() => getComputedStyle(document.querySelector('[data-testid=planner-grid]')).gridTemplateColumns.split(' ').length")
        assert cols > 3
    _no_overflow(page)
    assert not errors, errors


SLOW_RUN = """(() => { const f = window.fetch; window.fetch = (u, o) => { const p = f(u, o);
  if (window.__slowRun && /^\\/api\\/agent\\/runs\\/[^/?]+$/.test(String(u))) return new Promise((ok, no) => { const t = setTimeout(() => p.then(ok, no), window.__slowRun);
    if (o && o.signal) o.signal.addEventListener('abort', () => { clearTimeout(t); no(new DOMException('Aborted', 'AbortError')); }); });
  return p; }; })()"""


def test_new_post_during_thread_load_never_reopens_the_thread(live, chromium, close_contexts):
    """Review fix: New post while a thread is loading aborts that load, so the thread never lands over the new-post screen
    and the skeleton / inert composer do not linger for the next post."""
    ctx, page, errors = _ctx(chromium, live["base"], 1280)
    ctx.add_init_script(SLOW_RUN)
    _open_run(page, live["base"], live["rid"], 1280)
    page.wait_for_function(f"() => !!document.querySelector('#hcList [data-open=\"{live['rid']}\"]')")
    page.evaluate(f"""() => {{ window.__slowRun = 900; document.querySelector('#hcList [data-open="{live['rid']}"]').click(); }}""")
    page.wait_for_function("() => document.getElementById('runView').classList.contains('hc-loading')")
    page.evaluate("() => document.getElementById('mpNew').click()")
    page.wait_for_timeout(1300)                               # the held run answer would have landed by now
    st = page.evaluate("""() => ({ hidden: document.getElementById('runView').hidden, loading: document.getElementById('runView').classList.contains('hc-loading'),
      inert: !!(document.getElementById('hcDock') || {}).inert, run: !!(window.H_STATE && H_STATE.run) })""")
    assert st == {"hidden": True, "loading": False, "inert": False, "run": False}, st
    assert not errors, errors
