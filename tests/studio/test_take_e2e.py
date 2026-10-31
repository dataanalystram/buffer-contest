"""Browser test of One take -> a week in the sample workspace (Playwright): sample take, clip cards, trim/caption editor,
The Spread onto the Planner (and its reduced-motion version), saving the plan as Buffer drafts. Skips without Chromium."""
import os
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from studio.app import create_app
from studio.config import load_settings

pw = pytest.importorskip("playwright.sync_api")


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


@pytest.fixture
def live(tmp_path):
    port = _free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="t" * 64,
                      db_path=str(tmp_path / "take.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503))))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(50):
        try:
            httpx.get(f"http://127.0.0.1:{port}/healthz"); break
        except httpx.HTTPError:
            time.sleep(0.1)
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
    ctx.route(lambda u: not u.startswith(live), lambda r: r.abort())  # hermetic
    page = ctx.new_page()
    _open_sample(page, live)
    assert page.get_by_test_id("clip-card").count() == 3
    assert page.locator("[data-testid=take-step][data-status=done]").count() == 8
    assert page.get_by_test_id("clip-preflight").count() == 3
    assert page.locator("[data-testid=clip-preflight][data-status=ok]").count() == 3
    if shots:
        page.evaluate("() => window.scrollTo(0, document.querySelector('#tkHead').getBoundingClientRect().top + scrollY - 90)")
        page.wait_for_timeout(600)
        page.screenshot(path=f"{shots}/take-clips-{scheme}.png", full_page=False)
    # per-clip editor: transcript words, nudges, caption edit saved through PATCH
    page.locator("[data-testid=clip-card][data-clip=c2] [data-testid=clip-edit]").click()
    dlg = page.get_by_test_id("clip-dialog")
    dlg.wait_for()
    assert page.locator("#ceWords .w").count() > 20
    first_in = page.get_by_test_id("clip-in").inner_text()
    page.get_by_test_id("clip-in-later").click()
    assert page.get_by_test_id("clip-in").inner_text() != first_in
    page.get_by_test_id("clip-in-earlier").click()
    if shots:
        page.screenshot(path=f"{shots}/take-editor-{scheme}.png")
    page.get_by_test_id("clip-caption").fill("Biofilms on a space station, in 30 seconds.")
    page.get_by_test_id("clip-edit-save").click()
    page.get_by_test_id("toast").filter(has_text="Caption saved").wait_for()
    # The Spread: plan first (ghost cards on the Planner), nothing saved yet
    page.get_by_test_id("spread-go").click()
    page.get_by_test_id("planner-spread").wait_for()
    ghosts = page.locator("[data-ref^='ghost:']")
    ghosts.first.wait_for()
    assert ghosts.count() == 3
    page.wait_for_function("() => !document.querySelector('.spread-layer')")  # the flight finished
    assert page.locator(".pl-chip.s-ghost.landed").count() == 3
    if shots:
        page.screenshot(path=f"{shots}/take-spread-{scheme}.png")
    page.get_by_test_id("planner-spread-save").click()
    page.get_by_test_id("toast").filter(has_text="Buffer drafts").wait_for()
    page.wait_for_function("() => !document.querySelector(\"[data-ref^='ghost:']\")")
    assert page.get_by_test_id("planner-spread").is_hidden()
    assert page.locator("[data-testid=planner-item][data-state=draft]").count() >= 3
    if shots:
        page.screenshot(path=f"{shots}/take-saved-{scheme}.png")
    ctx.close()


def test_spread_respects_reduced_motion(live, chromium):
    browser = chromium
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")
    ctx.route(lambda u: not u.startswith(live), lambda r: r.abort())
    page = ctx.new_page()
    _open_sample(page, live)
    page.evaluate("() => { window.__flights = 0; new MutationObserver((m) => m.forEach((r) => r.addedNodes.forEach((n) => { if (n.classList && n.classList.contains('spread-layer')) window.__flights++; }))).observe(document.body, {childList: true}); }")
    page.get_by_test_id("spread-go").click()
    page.locator("[data-ref^='ghost:']").first.wait_for()
    assert page.locator("[data-ref^='ghost:']").count() == 3
    assert page.evaluate("window.__flights") == 0          # no cards in flight, the plan just appears
    page.get_by_test_id("planner-spread-cancel").click()
    page.get_by_test_id("toast").filter(has_text="Nothing was saved").wait_for()
    assert page.locator("[data-ref^='ghost:']").count() == 0
    ctx.close()
