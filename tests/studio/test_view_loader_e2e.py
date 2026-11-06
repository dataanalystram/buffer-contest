"""View transitions (owner ask, 5 Nov 2026): "switching tabs or views flashes the wrong page, then it vanishes". In a real
browser at 1280 and 390 px (Buffer is FakeBuffer; the Library fixture from test_library_bin_e2e):
  - after a tab click, no frame shows the previous visit's content or another screen: the HuhQueue loader shows in its
    place, then goes, and the screen is drawn once with fresh data (requests slowed in the page so the window is wide);
  - Back (the history from #115) returns through the same loader and leaves nothing hidden;
  - prefers-reduced-motion gets a still loader (no animation, the whole outline drawn), full motion gets the trace;
  - a deep link (View run) that fails shows an inline error with Retry on the screen still open, and Retry opens the run.
studio/static/viewswap.js is the helper; on main (before it) the loader never appears and the old cards stay on view."""
import pytest

from .test_library_bin_e2e import _ctx, _library, live  # noqa: F401  (live is the shared fixture)

pytest.importorskip("playwright.sync_api")

# slow chosen requests inside the page (window.__hqDelay[urlPart] = ms), count clicks, and sample every painted frame
PROBE = """(() => {
  const of = window.fetch.bind(window); window.__hqDelay = {}; window.__hqClicked = 0;
  window.fetch = async (u, o) => { const s = typeof u === "string" ? u : (u && u.url) || "";
    for (const [k, ms] of Object.entries(window.__hqDelay)) if (s.includes(k)) { await new Promise((r) => setTimeout(r, ms)); break; }
    return of(u, o); };
  document.addEventListener("click", () => { window.__hqClicked += 1; }, true);
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility === "visible" && !e.closest("[hidden]"); };
  window.__hqWatch = (stale) => { const F = []; let on = true; window.__hqClicked = 0;
    window.__hqStop = () => { on = false; return F; };
    const tick = () => { if (!on) return;
      F.push({ after: window.__hqClicked > 0, tabs: [...document.querySelectorAll(".tab")].filter((t) => !t.hidden).map((t) => t.id),
               loader: [...document.querySelectorAll("[data-testid=view-loader]")].some(vis), stale: [...document.querySelectorAll(stale)].some(vis) });
      requestAnimationFrame(tick); };
    tick(); };
})();"""
VIEWPORTS = [1280, 390]


def _page(chromium, base, w, motion="no-preference"):
    ctx, page, errors = _ctx(chromium, base, w)
    page.emulate_media(reduced_motion=motion)
    page.add_init_script(PROBE)
    return ctx, page, errors


def _go(page, tab, watch=""):
    """Click a rail tab like a person (on a phone, through the More sheet), counting only that click. `watch` starts the
    frame sampler (with that stale selector) once the sheet is open, so only frames after the tab click count."""
    b = page.locator(f".rail button[data-tab={tab}]")
    if not b.is_visible():
        page.locator("#moreBtn").click()
        b.wait_for(state="visible")
    if watch:
        page.evaluate("(s) => window.__hqWatch(s)", watch)
    page.evaluate("window.__hqClicked = 0")
    b.click()


def _settled(page, tab):
    page.locator(f"#tab-{tab}").wait_for(state="visible")
    page.locator("[data-testid=view-loader]").wait_for(state="detached")


@pytest.mark.parametrize("w", VIEWPORTS)
def test_tab_switch_never_shows_the_old_view_and_the_loader_comes_and_goes(live, chromium, close_contexts, w):  # noqa: F811
    _, page, errors = _page(chromium, live["base"], w)
    _library(page, live["base"])
    _settled(page, "library")
    # the cards drawn on this visit are marked: none of them may be on view when the Library is opened again
    page.evaluate("document.querySelectorAll('[data-testid=library-item]').forEach((e) => (e.dataset.stale = '1'))")
    _go(page, "planner")
    _settled(page, "planner")
    page.evaluate("window.__hqDelay['/api/library'] = 700")
    _go(page, "library", watch="#tab-library [data-stale], #tab-planner *")
    page.get_by_test_id("library-item").first.wait_for(state="visible")
    _settled(page, "library")
    page.evaluate("() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))")  # one more sampled frame
    frames = [f for f in page.evaluate("window.__hqStop()") if f["after"]]
    assert frames, "no frame was painted after the click"
    assert not [f for f in frames if f["stale"]], "the previous view's content was on view after the click"
    assert all(f["tabs"] == ["tab-library"] for f in frames), {tuple(f["tabs"]) for f in frames}
    assert any(f["loader"] for f in frames), "the HuhQueue loader never showed"
    assert not frames[-1]["loader"]
    assert page.locator("#tab-library [data-stale]").count() == 0, "the Library was drawn from the old cards, not fresh data"
    # Back returns to the Planner (one history entry per screen, #115), through the loader, with nothing left hidden
    page.go_back()
    _settled(page, "planner")
    assert "tab=planner" in page.url
    assert page.locator(".hq-vt-wait").count() == 0
    assert not errors, errors


@pytest.mark.parametrize("w", VIEWPORTS)
@pytest.mark.parametrize("motion", ["reduce", "no-preference"])
def test_loader_respects_reduced_motion(live, chromium, close_contexts, w, motion):  # noqa: F811
    _, page, errors = _page(chromium, live["base"], w, motion)
    _library(page, live["base"])
    _settled(page, "library")
    page.evaluate("window.__hqDelay['/api/calendar'] = 1500")
    _go(page, "planner")
    loader = page.get_by_test_id("view-loader")
    loader.wait_for(state="visible")
    trace, logo, dash = page.evaluate("""[getComputedStyle(document.querySelector('.hq-vt-trace')).animationName,
        getComputedStyle(document.querySelector('.hq-vt-logo')).animationName,
        getComputedStyle(document.querySelector('.hq-vt-trace')).strokeDasharray]""")
    if motion == "reduce":
        assert (trace, logo, dash) == ("none", "none", "none")
    else:
        assert (trace, logo) == ("hq-vt-trace", "hq-vt-breathe") and dash != "none"
    # the overlay never takes a click (#117 follow-up)
    assert page.evaluate("getComputedStyle(document.querySelector('[data-testid=view-loader]')).pointerEvents") == "none"
    # past 300 ms the loader adds list skeletons, then the Planner is drawn once
    page.locator("[data-testid=view-loader] [data-testid=loading-skeleton]").wait_for(state="attached")
    if motion == "reduce":  # nothing in the loader moves, its skeletons included
        assert page.evaluate("document.querySelector('[data-testid=view-loader]').getAnimations({subtree: true}).length") == 0
    _settled(page, "planner")
    assert not errors, errors


@pytest.mark.parametrize("w", VIEWPORTS)
def test_a_slow_screen_is_shown_after_the_1500ms_cap(live, chromium, close_contexts, w):  # noqa: F811
    """A screen whose data takes 4 s is shown with its own skeletons after 1.5 s; the loader never holds it longer."""
    _, page, errors = _page(chromium, live["base"], w)
    _library(page, live["base"])
    _settled(page, "library")
    page.evaluate("window.__hqDelay['/api/calendar'] = 4000")
    t0 = page.evaluate("performance.now()")
    _go(page, "planner")
    page.get_by_test_id("view-loader").wait_for(state="visible")
    page.get_by_test_id("view-loader").wait_for(state="detached", timeout=3500)
    took = page.evaluate("performance.now()") - t0
    assert took < 3000, f"the loader held the screen for {took:.0f} ms"
    assert page.locator("#tab-planner").is_visible()
    assert not errors, errors


@pytest.mark.parametrize("w", VIEWPORTS)
def test_a_deep_link_that_fails_shows_retry_in_place(live, chromium, close_contexts, w):  # noqa: F811
    _, page, errors = _page(chromium, live["base"], w)
    _library(page, live["base"])
    _settled(page, "library")
    fail = {"n": 1}

    def runs(route):
        if fail["n"]:
            fail["n"] -= 1
            route.fulfill(status=503, content_type="application/json", body='{"error": "The run store is busy"}')
        else:
            route.continue_()
    page.route(f"**/api/agent/runs/{live['rid']}", runs)
    page.evaluate(f"window.hqOpenRun({live['rid']})")
    err = page.get_by_test_id("view-error")
    err.wait_for(state="visible")
    assert err.evaluate("e => getComputedStyle(e).pointerEvents") == "auto"   # Retry still takes the click
    assert "The run store is busy" in err.inner_text()
    assert page.locator("#tab-library").is_visible() and not page.get_by_test_id("library-item").first.is_visible()
    assert "tab=library" in page.url, "a failed View run must not leave the Library"
    page.get_by_test_id("view-retry").click()
    _settled(page, "agent")
    page.locator("#runView").wait_for(state="visible")
    assert page.locator("[data-testid=view-error]").count() == 0
    assert not errors, errors


@pytest.mark.parametrize("w", VIEWPORTS)
def test_boot_never_hides_the_screen_already_on_view(live, chromium, close_contexts, w):  # noqa: F811
    """Make a post is on view before boot finishes. Someone who types in it then (the smoke tests do, right after goto)
    keeps it: boot opening that same screen reloads it in place, never under the loader, so focus and Enter survive.
    Before the fix boot's showTab("agent") swapped it (its sub went from none to "agent") and the composer went
    visibility:hidden mid-typing; the Enter that followed never started a run."""
    ctx, page, errors = _ctx(chromium, live["base"], w)
    # one init script: PROBE creates window.__hqDelay, then a slow boot (/api/channels held 1.5 s inside the page)
    page.add_init_script(PROBE + "\nwindow.__hqDelay['/api/channels'] = 1500;")
    page.add_init_script("""document.addEventListener('DOMContentLoaded', () => {
        const t = document.getElementById('tab-agent'); window.__hqHid = 0;
        new MutationObserver(() => { if (t.classList.contains('hq-vt-wait')) window.__hqHid += 1; })
          .observe(t, { attributes: true, attributeFilter: ['class'] }); });""")
    page.goto(live["base"] + "/?tab=agent", wait_until="domcontentloaded")
    q = page.get_by_test_id("agent-query")
    q.fill("Post this on all my channels")         # while boot still waits on /api/channels
    page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=30000)
    page.evaluate("() => new Promise((r) => setTimeout(r, 400))")   # past the loader's 150 ms floor
    assert page.evaluate("window.__hqHid") == 0, "boot put Make a post (already on view) under the loader"
    assert q.is_visible() and q.input_value() == "Post this on all my channels"
    assert page.evaluate("document.activeElement && document.activeElement.id") == "agQuery"
    assert not errors, errors
