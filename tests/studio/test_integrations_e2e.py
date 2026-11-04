"""Browser flow (Playwright, the sample workspace at /demo, nothing outside is called): Settings -> Integrations shows a
logo grid with one tile per key integration; a tile's Connect opens the key form in a dialog with its how-to and the
Product Hunt Redirect URI, a key is saved and shown masked only, Test answers with a clean status, Remove clears it, and
News -> Sources links to the grid. Skips when Chromium
can't start. SHOTS=dir saves Settings -> Integrations at 1440 and 390 wide."""
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen', '1')"
SHOTS = os.environ.get("SHOTS")
PH = "phDevTok_" + "A1b2C3d4" * 4 + "wxyz"


def _page(chromium, base, width=1440, height=900):
    ctx = chromium.new_context(viewport={"width": width, "height": height})
    page = ctx.new_page()
    page.add_init_script(NO_TOUR)
    page.goto(base + "/demo")
    page.wait_for_selector("#app:not([hidden])")
    return ctx, page


def _until(fn, timeout=15.0):
    import time
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.15)
    raise AssertionError("condition not met in time")


def test_settings_integrations_cards_save_test_remove(demo_live, chromium, monkeypatch):
    monkeypatch.setenv("HQ_DEMO_VIEW_ONLY", "0")  # real writes on the sample workspace: the capped demo (the default view-only demo refuses them, 2 Nov 2026)
    base, buffer_calls = demo_live
    ctx, page = _page(chromium, base)
    try:
        page.goto(base + "/?tab=settings&sub=integrations")
        page.wait_for_selector("[data-testid=integ-tile-ph]")
        assert page.locator("[data-testid=integ-group-research] .integ-tile").count() == 3  # one logo tile per key
        for t in ("ph", "x-api", "yt-api"):
            assert page.locator(f"[data-testid=integ-tile-{t}]").count() == 1, t
        page.click("[data-testid=integ-act-ph]")  # the key form opens in a dialog
        page.wait_for_selector("[data-testid=integ-dialog] [data-testid=integ-card-ph]")
        assert "/integrations/producthunt/callback" in page.locator("[data-testid=integ-redirect]").text_content()
        assert page.get_attribute("[data-testid=integ-input-ph]", "type") == "password"
        page.fill("[data-testid=integ-input-ph]", PH)
        page.click("[data-testid=integ-save-ph]")
        _until(lambda: "…wxyz" in page.inner_text("[data-testid=integ-card-ph] [data-testid=integ-status]"))
        assert PH not in page.content()  # never sent back to the page
        page.click("[data-testid=integ-test-ph]")
        _until(lambda: page.inner_text("[data-testid=integ-msg-ph]").strip() not in ("", "Checking with the service…"))
        msg = page.inner_text("[data-testid=integ-msg-ph]"); assert "sample" in msg.lower(), msg
        page.click("[data-testid=integ-remove-ph]")
        page.get_by_test_id("ask-ok").click()  # the in-app confirm (askdialog.js), not a native dialog
        _until(lambda: "by HuhQueue" in page.inner_text("[data-testid=integ-card-ph] [data-testid=integ-status]"))  # the shared app-level read
        page.click("[data-testid=integ-dialog-close]")
        page.wait_for_selector("[data-testid=integ-dialog]", state="hidden")
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.locator("#integrations").evaluate("el => el.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(SHOTS, "integrations-1440.png"))
        assert not buffer_calls
    finally:
        ctx.close()


def test_phone_width_and_sources_link_to_the_grid(demo_live, chromium):
    base, _ = demo_live
    ctx, page = _page(chromium, base, width=390, height=844)
    try:
        page.goto(base + "/?tab=sources")
        page.wait_for_selector("[data-testid=sa-scan]")
        page.goto(base + "/?tab=news&sub=sources")
        page.wait_for_selector("[data-testid=sa-keys-link]", state="attached")
        page.locator("[data-testid=sa-keys-link]").click()
        page.wait_for_selector("[data-testid=integ-tile-ph]")
        _until(lambda: not page.locator("#tab-settings").is_hidden())
        widths = page.evaluate("[...document.querySelectorAll('.integ-tile')].map(e => e.getBoundingClientRect().right)")
        assert widths and max(widths) <= 390  # no tile spills past a phone screen
        assert page.evaluate("document.documentElement.scrollWidth") <= 392
        page.click("[data-testid=integ-act-ph]")
        page.wait_for_selector("[data-testid=integ-dialog] [data-testid=integ-card-ph]")
        box = page.locator("[data-testid=integ-dialog]").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390  # the key dialog fits a phone
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.screenshot(path=os.path.join(SHOTS, "integrations-390-ph-dialog.png"))
    finally:
        ctx.close()
