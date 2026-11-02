"""Browser flow (Playwright, the sample workspace at /demo, nothing outside is called): Settings -> Integrations shows one
card per key integration with its how-to and the Product Hunt Redirect URI, a key is saved and shown masked only, Test
answers with a clean status, Remove clears it, and News -> Sources links a missing key to its card. Skips when Chromium
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


def test_settings_integrations_cards_save_test_remove(demo_live, chromium):
    base, buffer_calls = demo_live
    ctx, page = _page(chromium, base)
    try:
        page.goto(base + "/?tab=settings")
        page.wait_for_selector("[data-testid=integ-card-ph]")
        assert page.locator("[data-testid^=integ-card-]").count() == 3
        assert "/integrations/producthunt/callback" in page.text_content("[data-testid=integ-redirect]")  # inside a closed <details>: innerText is "" in current Chromium
        assert page.get_attribute("[data-testid=integ-input-ph]", "type") == "password"
        page.fill("[data-testid=integ-input-ph]", PH)
        page.click("[data-testid=integ-save-ph]")
        _until(lambda: "…wxyz" in page.inner_text("[data-testid=integ-card-ph] [data-testid=integ-status]"))
        assert PH not in page.content()  # never sent back to the page
        page.click("[data-testid=integ-test-ph]")
        _until(lambda: page.inner_text("[data-testid=integ-msg-ph]").strip() not in ("", "Checking with the service…"))
        msg = page.inner_text("[data-testid=integ-msg-ph]"); assert "sample" in msg.lower(), msg
        page.on("dialog", lambda d: d.accept())
        page.click("[data-testid=integ-remove-ph]")
        _until(lambda: "by HuhQueue" in page.inner_text("[data-testid=integ-card-ph] [data-testid=integ-status]"))  # the shared app-level read
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.locator("#integrations").scroll_into_view_if_needed()
            page.locator("#integrations").evaluate("el => el.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(SHOTS, "integrations-1440.png"))
        assert not buffer_calls
    finally:
        ctx.close()


def test_phone_width_and_sources_link_to_the_card(demo_live, chromium):
    base, _ = demo_live
    ctx, page = _page(chromium, base, width=390, height=844)
    try:
        page.goto(base + "/?tab=sources")
        page.wait_for_selector("[data-testid=sa-scan]")
        page.goto(base + "/?tab=news&sub=sources")
        page.wait_for_selector("[data-testid=sa-keys-link]", state="attached")
        page.locator("[data-testid=sa-keys-link]").click()
        page.wait_for_selector("[data-testid=integ-card-ph]")
        _until(lambda: not page.locator("#tab-settings").is_hidden())
        widths = page.evaluate("[...document.querySelectorAll('.integ-card')].map(e => e.getBoundingClientRect().right)")
        assert widths and max(widths) <= 390  # no card spills past a phone screen
        assert page.evaluate("document.documentElement.scrollWidth") <= 392
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            page.locator("#integrations").evaluate("el => el.scrollIntoView({block: 'start'})")
            page.wait_for_timeout(400)
            page.screenshot(path=os.path.join(SHOTS, "integrations-390.png"))
            page.locator("[data-testid=integ-card-ph]").screenshot(path=os.path.join(SHOTS, "integrations-390-ph-card.png"))
    finally:
        ctx.close()
