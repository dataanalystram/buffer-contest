"""Browser flows (Playwright, the sample workspace at /demo, no Buffer behind it): the Brand studio's first-run path and live
preview, switching example brands, the gallery with its Reels, a recurring slot; and the Sources agent from scan to Buffer
drafts. Skips when Chromium can't start. SHOTS=dir saves the docs screenshots."""
import os

import pytest

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen', '1')"
SHOTS = os.environ.get("SHOTS")


def _page(chromium, base, width=1280):
    ctx = chromium.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    page.add_init_script(NO_TOUR)
    page.goto(base + "/demo")
    page.wait_for_selector("#app:not([hidden])")
    return ctx, page


def _until(fn, timeout=15.0):
    """Poll in Python (the app's CSP forbids the string evaluation wait_for_function uses)."""
    import time
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.15)
    raise AssertionError("condition not met in time")


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def test_brand_studio_first_run_preview_examples_and_slot(demo_live, chromium, monkeypatch):
    monkeypatch.setenv("HQ_DEMO_VIEW_ONLY", "0")  # real writes on the sample workspace: the capped demo (the default view-only demo refuses them, 2 Nov 2026)
    base, buffer_calls = demo_live
    ctx, page = _page(chromium, base)
    try:
        page.goto(base + "/?tab=brandstudio")
        page.wait_for_selector("[data-testid=bk-more]")
        page.eval_on_selector("[data-testid=bk-more]", "d => { d.open = true; }")  # the older kit editor sits under the brand kit builder
        page.wait_for_selector("[data-testid=bs-slide]")
        steps = page.locator("[data-testid=bs-steps] li")
        assert steps.count() == 4 and "Connect Buffer" in steps.nth(3).inner_text()
        first = page.locator("[data-testid=bs-slide]").first.get_attribute("src")
        page.fill("[data-testid=bs-color-accent]", "#ff5a1f")
        page.locator("[data-testid=bs-color-accent]").dispatch_event("input")
        _until(lambda: page.locator("[data-testid=bs-slide]").first.get_attribute("src") != first)
        page.click("[data-testid=brand-tab-templates]")  # one Brand screen: Kit, Templates, Style Ledger
        page.click("[data-testid=bs-use-buffer]")
        _until(lambda: page.input_value("[data-testid=bs-name]") == "Buffer")
        page.wait_for_timeout(600)
        _shot(page, "brand-studio.png")
        page.click("[data-testid=bs-gallery-buffer]")
        page.wait_for_selector("[data-testid=bs-gallery] [data-testid=bs-video]")
        assert page.locator("[data-testid=bs-gallery] img").count() >= 20
        page.locator("[data-testid=bs-gallery]").scroll_into_view_if_needed()
        _shot(page, "brand-gallery.png")
        page.click("[data-testid=bs-use-huhbyte]")
        _until(lambda: page.input_value("[data-testid=bs-name]") == "HuhByte AI")
        page.click("[data-testid=bs-slot-add]")
        page.wait_for_selector("[data-testid=bs-slot]")
        page.click("[data-testid=bs-slot-run]")
        _until(lambda: "Buffer drafts" in page.inner_text("#toast"))
        assert not buffer_calls  # the sample never calls Buffer
    finally:
        ctx.close()


def test_sources_agent_scan_then_make_a_post_opens_the_composer(demo_live, chromium, monkeypatch):
    """Scan now reads; ticking stories and pressing Make a post (N) opens Make a post with their links. Nothing is written on
    News and the sample never calls Buffer (owner bug, 6 Nov 2026: Write posts generated every post inline)."""
    monkeypatch.setenv("HQ_DEMO_VIEW_ONLY", "0")
    base, buffer_calls = demo_live
    ctx, page = _page(chromium, base)
    try:
        page.goto(base + "/?tab=sources")  # the old name lands on News > Story scan
        page.wait_for_selector("[data-testid=sa-scan]")
        page.wait_for_selector("[data-testid=sa-src-yc]", state="attached")
        page.click("[data-testid=sa-scan]")
        page.wait_for_selector("[data-testid=sa-cluster]")
        assert page.locator("[data-testid=sa-src-status]").count() == 5
        page.locator("[data-testid=sa-cluster] input[type=checkbox]").first.check()
        _shot(page, "sources-scan.png")
        seen = []
        page.on("request", lambda r: seen.append(r.url))
        page.click("[data-testid=sa-make-picked]")
        page.wait_for_selector("#tab-agent", state="visible")
        assert page.input_value("[data-testid=agent-query]").startswith("Make posts from these stories:")
        assert not page.locator("[data-testid=sa-package]").count()
        assert not any("/api/sources/draft" in u for u in seen)
        assert not buffer_calls
    finally:
        ctx.close()
