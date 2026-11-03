"""Browser test of One take -> a week in the sample workspace (Playwright): sample take, clip cards, trim/caption editor,
The Spread onto the Planner (and its reduced-motion version), saving the plan as Buffer drafts. Skips without Chromium."""
import os

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"  # the guided tour has its own test (test_tour_e2e.py)
CLIPS = 5  # the sample take: the Buffer Commit kickoff split into 5 credited clips (studio/sample_take/sample.json)


def _free_port():
    return free_port()


@pytest.fixture
def live(tmp_path):
    port = _free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="t" * 64,
                      db_path=str(tmp_path / "take.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True



def _open_sample(page, base):
    page.goto(base + "/demo")
    page.get_by_test_id("tab-take").click()
    page.get_by_test_id("take-chip").first.wait_for()
    assert page.get_by_test_id("take-chip").count() >= 1
    page.get_by_test_id("take-sample").click()
    page.get_by_test_id("clip-card").nth(2).wait_for()


def test_sample_take_spread_and_save_as_drafts(live, chromium):
    shots = os.environ.get("TAKE_SHOTS")
    browser = chromium
    scheme = os.environ.get("TAKE_SCHEME", "light")
    ctx = browser.new_context(viewport={"width": 1360, "height": 920}, color_scheme=scheme)
    ctx.add_init_script(NO_TOUR)
    ctx.route(lambda u: not u.startswith(live), lambda r: r.abort())  # hermetic
    page = ctx.new_page()
    _open_sample(page, live)
    assert page.get_by_test_id("clip-card").count() == CLIPS
    assert page.locator("[data-testid=take-step][data-status=done]").count() == 8
    # one preflight chip per clip per selected channel; the default is Make a post's (setup's channels, else every
    # connected one) minus channels without video
    chans = page.locator("[data-testid=take-chip][aria-pressed=true]").count()
    assert chans >= 1
    assert page.get_by_test_id("clip-preflight").count() == CLIPS * chans
    assert page.locator("[data-testid=clip-preflight][data-status=ok]").count() == CLIPS * chans
    if shots:
        page.evaluate("() => window.scrollTo(0, document.querySelector('#tkHead').getBoundingClientRect().top + scrollY - 90)")
        page.wait_for_timeout(600)
        page.screenshot(path=f"{shots}/take-clips-{scheme}.png", full_page=False)
    # Trim & caption in the view-only sample: timing, transcript and cover are shown but locked (no re-render here);
    # a caption edit saves through PATCH
    page.locator("[data-testid=clip-card][data-clip=c2] [data-testid=clip-edit]").click()
    dlg = page.get_by_test_id("clip-dialog")
    dlg.wait_for()
    assert page.get_by_test_id("clip-in-later").is_disabled() and page.get_by_test_id("clip-handle-out").is_disabled()
    assert "can't re-render" in page.get_by_test_id("clip-trim-note").inner_text()
    page.get_by_test_id("clip-tab-transcript").click()
    assert len(page.get_by_test_id("clip-transcript").input_value().split()) > 20
    if shots:
        page.screenshot(path=f"{shots}/take-editor-{scheme}.png")
    page.get_by_test_id("clip-tab-caption").click()
    page.get_by_test_id("clip-caption").fill("The Buffer Commit kickoff, in 40 seconds.")
    page.get_by_test_id("clip-edit-save").click()
    page.get_by_test_id("toast").filter(has_text="Caption saved").wait_for()
    # The Spread: plan first (ghost cards on the Planner), nothing saved yet
    page.get_by_test_id("spread-go").click()
    page.get_by_test_id("planner-spread").wait_for()
    ghosts = page.locator("[data-ref^='ghost:']")
    ghosts.first.wait_for()
    assert ghosts.count() == CLIPS * chans  # one ghost per clip per channel
    page.wait_for_function("() => !document.querySelector('.spread-layer')")  # the flight finished
    assert page.locator(".pl-chip.s-ghost.landed").count() == CLIPS * chans
    if shots:
        page.screenshot(path=f"{shots}/take-spread-{scheme}.png")
    page.get_by_test_id("planner-spread-save").click()
    page.get_by_test_id("toast").filter(has_text="Buffer drafts").wait_for()
    page.wait_for_function("() => !document.querySelector(\"[data-ref^='ghost:']\")")
    assert page.get_by_test_id("planner-spread").is_hidden()
    assert page.locator("[data-testid=planner-item][data-state=draft]").count() >= CLIPS * chans
    if shots:
        page.screenshot(path=f"{shots}/take-saved-{scheme}.png")
    ctx.close()


def test_spread_respects_reduced_motion(live, chromium):
    browser = chromium
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    ctx.add_init_script(NO_TOUR)
    ctx.route(lambda u: not u.startswith(live), lambda r: r.abort())
    page = ctx.new_page()
    _open_sample(page, live)
    page.evaluate("() => { window.__flights = 0; new MutationObserver((m) => m.forEach((r) => r.addedNodes.forEach((n) => { if (n.classList && n.classList.contains('spread-layer')) window.__flights++; }))).observe(document.body, {childList: true}); }")
    chans = page.locator("[data-testid=take-chip][aria-pressed=true]").count()
    page.get_by_test_id("spread-go").click()
    page.locator("[data-ref^='ghost:']").first.wait_for()
    assert page.locator("[data-ref^='ghost:']").count() == CLIPS * chans
    assert page.evaluate("window.__flights") == 0          # no cards in flight, the plan just appears
    page.get_by_test_id("planner-spread-cancel").click()
    page.get_by_test_id("toast").filter(has_text="Nothing was saved").wait_for()
    assert page.locator("[data-ref^='ghost:']").count() == 0
    ctx.close()


def test_make_clips_errors_stay_on_the_page(live, chromium):
    """Live test 2 Nov 2026: Make clips with the rights box unticked, no channel or a bad link only flashed a toast."""
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    page.goto(live + "/demo")
    page.get_by_test_id("tab-take").click()
    page.get_by_test_id("take-chip").first.wait_for()
    err = page.get_by_test_id("take-error")
    page.get_by_test_id("take-go").click()
    assert "Tick" in err.inner_text()
    page.get_by_test_id("take-owns").check()
    for chip in page.get_by_test_id("take-chip").all():
        if chip.get_attribute("aria-pressed") == "true":
            chip.click()
    page.get_by_test_id("take-go").click()
    assert "Pick at least one channel" in err.inner_text()
    page.get_by_test_id("take-chip").first.click()
    page.get_by_test_id("take-go").click()
    assert "Choose a video first" in err.inner_text()
    page.get_by_test_id("take-src-link").click()
    page.get_by_test_id("take-url").fill("https://example.com")
    page.get_by_test_id("take-go").click()
    assert "direct https:// link" in err.inner_text()
    ctx.close()


def test_sample_take_in_a_real_account_says_it_cannot_spread(live, chromium):
    """The server refuses to spread the sample's clips into a real Buffer (403); the page now says so instead of a dead button."""
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    _open_sample(page, live)
    assert page.get_by_test_id("spread-go").is_visible() and page.get_by_test_id("spread-sample-note").is_hidden()
    page.evaluate("() => { S.session.demo = false; showTake(T.take); }")  # what a signed-in Buffer account sees
    assert page.get_by_test_id("spread-go").is_hidden()
    assert "can't go to your Buffer" in page.get_by_test_id("spread-sample-note").inner_text()
    ctx.close()
