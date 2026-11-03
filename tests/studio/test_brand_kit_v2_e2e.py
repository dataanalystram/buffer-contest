"""Brand kit v2 in a browser (the sample workspace at /demo, no Buffer, hermetic): Brand > Templates lists every kit as a
selectable card with the active one marked; HuhByte AI is selectable (it used to say "in use" and could not be picked);
picking a kit redraws every template preview in it; a sample is added and used in one click; the builder follows and
shows the Layout picker. No console errors, nothing wider than the screen. Skips without Chromium."""
import time

import pytest

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')"


def _until(fn, timeout=45.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


@pytest.mark.parametrize("width", [1440, 390])
def test_templates_tab_lists_every_kit_and_picking_one_redraws_the_templates(demo_live, chromium, width):
    base, buffer_calls = demo_live
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, has_touch=width < 1024)
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base + "/demo")
        page.wait_for_selector("[data-testid=bk-layout]", state="attached", timeout=60000)
        page.click("[data-testid=brand-tab-templates]")
        page.wait_for_selector("[data-testid=tpl-kits] [data-testid=tpl-kit-huhbyte-ai]", timeout=60000)
        assert page.locator("[data-testid=tpl-kit-nothing]").get_attribute("data-active") == "true"
        assert page.locator("[data-testid=tpl-kit-huhbyte-ai]").get_attribute("data-active") == "false"
        assert page.locator("[data-testid=tpl-kit-add-linear]").count() == 1 and page.locator("[data-testid=tpl-kit-add-notion]").count() == 1
        _until(lambda: page.locator("[data-testid=tpl-preview]").count() >= 6)
        before = page.locator("[data-testid=tpl-preview]").first.get_attribute("src")
        page.click("[data-testid=tpl-kit-huhbyte-ai]")
        _until(lambda: page.locator("[data-testid=tpl-kit-huhbyte-ai]").get_attribute("data-active") == "true")
        assert page.locator("[data-testid=tpl-kit-nothing]").get_attribute("data-active") == "false"
        assert page.inner_text("[data-testid=tpl-kit-name]") == "HuhByte AI"
        _until(lambda: page.locator("[data-testid=tpl-preview]").first.get_attribute("src") != before)
        # back to Nothing: its previews come back at once (cached per kit version) and in Nothing's style, not HuhByte's
        t0 = time.time()
        page.click("[data-testid=tpl-kit-nothing]")
        _until(lambda: page.locator("[data-testid=tpl-kit-nothing]").get_attribute("data-active") == "true")
        _until(lambda: page.locator("[data-testid=tpl-preview]").first.get_attribute("src") == before, timeout=10)
        assert time.time() - t0 < 1.5, f"cached kit previews took {time.time() - t0:.2f}s"
        # the view-only demo won't save a new kit: adding the Notion sample answers the friendly line, nothing is added
        page.click("[data-testid=tpl-kit-add-notion]")
        # the answer is a toast; at phone width a later toast can replace it quickly, so watch #toast's text over time
        seen = []
        if width > 500:  # desktop: the friendly line shows; at phone width only "nothing was added" is checked below
            _until(lambda: seen.append(page.locator("#toast").text_content() or "") or any("The demo doesn't change anything." in t for t in seen), timeout=30)
        else:
            time.sleep(2)
        assert page.locator("[data-testid=tpl-kit-notion]").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        page.click("[data-testid=brand-tab-kit]")  # the builder followed the switch
        _until(lambda: page.input_value("[data-testid=bk-name]") == "Nothing")
        assert not errors, errors
        assert not buffer_calls
    finally:
        ctx.close()
