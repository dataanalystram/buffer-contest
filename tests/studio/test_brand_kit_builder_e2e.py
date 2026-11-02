"""The brand kit builder in a browser (Playwright, the sample workspace at /demo, no Buffer behind it): the sample opens on
Brand > Kit with the Nothing sample brand filled in next to HuhByte AI; one click switches the active kit and the whole
gallery restyles; Start your own clears the form; Save makes the new kit active and Settings shows it. Desktop light and
a 390 px phone in dark, with no console errors and nothing wider than the screen. Skips without Chromium."""
import time

import pytest

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')"


def _until(fn, timeout=40.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.15)
    raise AssertionError("condition not met in time")


def _open(chromium, base, width, scheme):
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, color_scheme=scheme,
                               has_touch=width < 1024)
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    errors = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/demo")
    page.wait_for_selector("[data-testid=bk-themes] [data-card=opus] img", timeout=60000)
    return ctx, page, errors


def _overflow(page) -> int:
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.parametrize("width,scheme", [(1440, "light"), (390, "dark")])
def test_demo_opens_on_the_builder_filled_with_the_nothing_sample(demo_live, chromium, width, scheme):
    base, buffer_calls = demo_live
    ctx, page, errors = _open(chromium, base, width, scheme)
    try:
        assert page.locator("#tab-brand").is_visible() and page.locator("#bkBuilder").is_visible()
        assert page.input_value("[data-testid=bk-name]") == "Nothing" and page.input_value("[data-testid=bk-handle]") == "nothing"
        assert page.input_value("[data-testid=bk-head-font]") == "dot"
        assert "Sample brand, not affiliated" in page.inner_text("[data-testid=bk-label]")
        assert "Active" in page.inner_text("[data-testid=bk-kit-nothing]")
        assert page.locator("[data-testid=bk-kit-huhbyte-ai]").count() == 1
        assert page.locator("[data-testid=bk-add-linear]").count() == 1 and page.locator("[data-testid=bk-add-notion]").count() == 1
        for c in ("carousel_cover", "thumbnail", "end_card"):
            assert page.locator(f"[data-testid=bk-card-{c}] img").count() == 1
        _until(lambda: page.locator("[data-testid=bk-themes] [data-card] img").count() >= 20)  # the rest load after the visible ones
        assert _overflow(page) <= 0
        assert not errors, errors
    finally:
        ctx.close()
    assert buffer_calls == []


def test_switch_kits_restyles_the_gallery_start_your_own_and_save(demo_live, chromium):
    base, buffer_calls = demo_live
    ctx, page, errors = _open(chromium, base, 1440, "light")
    try:
        before = page.get_attribute("[data-testid=bk-themes] [data-card=opus] img", "src")
        page.click("[data-testid=bk-kit-huhbyte-ai]")  # one click: the active kit and the whole gallery
        _until(lambda: page.input_value("[data-testid=bk-name]") == "HuhByte AI")
        _until(lambda: page.get_attribute("[data-testid=bk-themes] [data-card=opus] img", "src") != before)
        _until(lambda: "Active" in page.inner_text("[data-testid=bk-kit-huhbyte-ai]"))
        _until(lambda: page.input_value("[data-testid=bs-name]") == "HuhByte AI")  # the kit every template reads
        page.click("[data-testid=bk-add-linear]")  # a sample brand from the bar
        _until(lambda: page.input_value("[data-testid=bk-name]") == "Linear")
        page.click("[data-testid=bk-start-own]")
        assert page.input_value("[data-testid=bk-name]") == "" and page.locator("[data-testid=bk-kit-draft]").count() == 1
        kit_before = page.get_attribute("[data-testid=bk-card-end_card] img", "src")
        page.fill("[data-testid=bk-name]", "Acme Rockets")
        page.fill("[data-testid=bk-handle]", "acmerockets")
        page.fill("[data-testid=bk-hex-primary]", "#FF5A1F")
        page.locator("[data-testid=bk-hex-primary]").dispatch_event("change")
        page.click("[data-testid=bk-cap-hype]")
        page.fill("[data-testid=bk-end-cta]", "Book a launch")
        _until(lambda: page.get_attribute("[data-testid=bk-card-end_card] img", "src") != kit_before)  # live as you edit
        page.click("[data-testid=bk-save]")
        _until(lambda: page.locator("[data-testid=bk-kit-acme-rockets]").count() == 1
               and "Active" in page.inner_text("[data-testid=bk-kit-acme-rockets]"))
        page.goto(base + "/?tab=settings")
        page.wait_for_selector("[data-testid=settings-brand-name]")
        assert page.inner_text("[data-testid=settings-brand-name]") == "Acme Rockets"
        assert not errors, errors
    finally:
        ctx.close()
    assert buffer_calls == []
