"""Browser tests (Playwright) for the Make a post "Video style" card + Change modal and the AI engine switch
(owner, 2 Nov 2026: the inline theme gallery made the page scroll forever; pick the model with transparent credits).
At 1440 and 390, light and dark: the page never scrolls sideways, the modal scrolls inside itself while the page stays
put, search / category / sort / favourites / Apply work, Esc closes and gives focus back, and arrow keys move the
selection. Set HQ_SHOTS=<dir> to keep screenshots."""
import os
from pathlib import Path

import pytest

from .test_e2e_studio import NO_TOUR, live  # noqa: F401  (the signed-in live server fixture)

pw = pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_SHOTS", "")


def _page(live, chromium, w, h, scheme):
    base, fake, app = live
    ctx = chromium.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    return ctx, ctx.new_page()


def _shot(page, name):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _no_sideways(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


@pytest.mark.parametrize("w,h", [(1440, 900), (390, 844)])
@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_e2e_video_style_card_and_modal(live, chromium, close_contexts, w, h, scheme):
    base = live[0]
    errors = []
    ctx, page = _page(live, chromium, w, h, scheme)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/?tab=agent")
    card = page.get_by_test_id("video-style")
    card.wait_for()
    page.wait_for_function("document.querySelector('[data-testid=video-style-about]').textContent.length > 0")
    assert "Let the agent choose" in page.get_by_test_id("video-style-name").inner_text()
    assert card.bounding_box()["height"] < 140  # one compact card, not a gallery on the page
    assert page.get_by_test_id("theme-picker").count() == 1 and not page.get_by_test_id("theme-picker").is_visible()
    assert page.get_by_test_id("agent-format").is_visible()  # format stays its own control
    page.get_by_test_id("agent-format").select_option("reel")
    assert page.get_by_test_id("video-style-format").inner_text() == "Reel 9:16"
    assert _no_sideways(page)
    page.get_by_test_id("mp-theme").scroll_into_view_if_needed()
    _shot(page, f"page-{w}-{scheme}")

    y0 = page.evaluate("scrollY")
    page.get_by_test_id("theme-change").click()
    modal = page.get_by_test_id("theme-modal")
    modal.wait_for()
    page.get_by_test_id("theme-cinematic").wait_for()
    assert page.evaluate("document.documentElement.classList.contains('hqt-lock')")
    box = modal.bounding_box()
    if w >= 1000:
        assert 900 <= box["width"] <= 1100 and box["height"] <= 0.85 * h + 1
        assert page.locator("[data-testid=theme-picker]").evaluate("g => getComputedStyle(g).gridTemplateColumns.split(' ').length") == 3
        scroller = "[data-testid=theme-picker]"
    else:
        assert box["width"] >= w - 1  # a full-screen sheet on phones
        assert page.locator("[data-testid=theme-picker]").evaluate("g => getComputedStyle(g).gridTemplateColumns.split(' ').length") == 2
        scroller = "[data-testid=theme-modal] .hqt-dlg-body"
    assert page.locator(scroller).evaluate("e => e.scrollHeight > e.clientHeight")  # the modal scrolls inside itself
    first = page.locator("[data-testid=theme-picker] .hqt-tc").first
    assert first.get_attribute("data-theme") == "auto"  # "Let the agent choose" first
    _shot(page, f"modal-{w}-{scheme}")
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 900)
    page.wait_for_timeout(250)
    assert page.locator(scroller).evaluate("e => e.scrollTop") > 0
    assert page.evaluate("scrollY") == y0  # the page behind never moves
    assert _no_sideways(page)

    # search
    page.get_by_test_id("theme-search").fill("terminal")
    page.wait_for_timeout(100)
    assert page.locator("[data-testid=theme-picker] .hqt-tc").count() == 1
    assert page.get_by_test_id("theme-crt").is_visible() and "1 style" in page.get_by_test_id("theme-count").inner_text()
    page.get_by_test_id("theme-search").fill("nothing-matches-this")
    assert page.get_by_test_id("theme-empty").is_visible()
    page.get_by_test_id("theme-search").fill("")
    # category filter with counts, and sort A-Z
    news = page.get_by_test_id("theme-cat-news")
    n = int(news.locator(".hqt-n").inner_text())
    news.click()
    assert page.locator("[data-testid=theme-picker] .hqt-tc").count() == n and n >= 3
    page.get_by_test_id("theme-sort").select_option("az")
    names = page.locator("[data-testid=theme-picker] .hqt-tc-n").all_inner_texts()
    assert names == sorted(names, key=str.lower)
    # pick, preview, favourite, apply
    page.get_by_test_id("theme-caption_news").click()
    assert page.get_by_test_id("theme-caption_news").get_attribute("aria-checked") == "true"
    assert page.get_by_test_id("theme-caption_news").locator(".hqt-ck").count() == 1  # the check mark
    assert "Caption News" in page.get_by_test_id("theme-preview-name").inner_text()
    page.get_by_test_id("theme-fav").click()
    assert page.get_by_test_id("theme-fav").get_attribute("aria-pressed") == "true"
    assert page.get_by_test_id("theme-cat-saved").locator(".hqt-n").inner_text() == "1"
    _shot(page, f"modal-picked-{w}-{scheme}")
    page.get_by_test_id("theme-apply").click()
    assert not modal.is_visible()
    assert not page.evaluate("document.documentElement.classList.contains('hqt-lock')")
    assert "Caption News" in page.get_by_test_id("video-style-name").inner_text()
    assert page.evaluate("localStorage.getItem('hq_theme')") == "caption_news"
    assert page.evaluate("JSON.parse(localStorage.getItem('hq_theme_favs'))") == ["caption_news"]

    # Esc closes without changing anything; focus comes back to Change
    page.get_by_test_id("theme-change").click()
    modal.wait_for()
    page.get_by_test_id("theme-cat-saved").click()
    assert page.locator("[data-testid=theme-picker] .hqt-tc").count() == 1
    page.get_by_test_id("theme-cat-all").click()
    page.get_by_test_id("theme-hype").click()
    page.keyboard.press("Escape")
    assert not modal.is_visible()
    assert "Caption News" in page.get_by_test_id("video-style-name").inner_text()
    assert page.evaluate("document.activeElement && document.activeElement.dataset.testid") == "theme-change"
    assert not errors, errors
    ctx.close()


def test_e2e_theme_modal_keyboard(live, chromium, close_contexts):
    base = live[0]
    ctx, page = _page(live, chromium, 1440, 900, "light")
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("theme-change").click()
    page.get_by_test_id("theme-auto").wait_for()
    page.get_by_test_id("theme-auto").focus()
    page.keyboard.press("ArrowRight")
    second = page.locator("[data-testid=theme-picker] .hqt-tc").nth(1)
    assert second.get_attribute("aria-checked") == "true"
    page.keyboard.press("ArrowDown")  # down one row = three cards on
    assert page.locator("[data-testid=theme-picker] .hqt-tc").nth(4).get_attribute("aria-checked") == "true"
    tid = page.evaluate("document.activeElement.dataset.theme")
    # Tab never leaves the dialog
    for _ in range(12):
        page.keyboard.press("Tab")
        assert page.evaluate("!!document.activeElement.closest('[data-testid=theme-modal]')")
    page.get_by_test_id("theme-picker").locator(f"[data-theme='{tid}']").focus()
    page.keyboard.press("Enter")  # Enter applies
    assert not page.get_by_test_id("theme-modal").is_visible()
    assert page.evaluate("localStorage.getItem('hq_theme')") == tid
    ctx.close()


@pytest.mark.parametrize("w", [1440, 390])
def test_e2e_ai_engine_switch(live, chromium, close_contexts, monkeypatch, w):
    base, fake, app = live
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    for k in ("OCI_USER", "OCI_TENANCY", "OCI_FINGERPRINT"):
        monkeypatch.setenv(k, "ocid1.test")
    monkeypatch.setenv("OCI_PRIVATE_KEY", "not-a-real-key")  # only "is it set" matters to the picker
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-not-real")  # only "is it set" matters to the picker
    from studio import ai_engines
    ai_engines.clear_down()
    ai_engines.oracle_set(True)
    ctx, page = _page(live, chromium, w, 900 if w > 400 else 844, "light")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/?tab=settings")
    page.get_by_test_id("ai-engine-claude").wait_for()
    assert "Claude: $0.00 of your $1 used" in page.get_by_test_id("ai-engine-credit-claude").inner_text()
    assert "Gemini (Oracle): free during the contest, $0.00 of your $3 used" in page.get_by_test_id("ai-engine-credit-gemini-flash").inner_text()
    assert "App-wide" in page.get_by_test_id("ai-engine-pools").inner_text()
    assert "NVIDIA (free developer credits): 0 of your 150 calls today" in page.get_by_test_id("ai-engine-credit-nvidia").inner_text()
    link = page.get_by_test_id("ai-engine-key-link-gemini-studio")  # no AI Studio key on this server: how to add one
    assert link.inner_text() == "Add a free Google AI Studio key" and link.get_attribute("href") == "https://aistudio.google.com/apikey"
    assert page.get_by_test_id("ai-engine-own").is_disabled()  # no key saved yet
    page.get_by_test_id("ai-engine-gemini-flash").click()
    pw.expect(page.get_by_test_id("ai-engine-gemini-flash")).to_have_attribute("aria-checked", "true")
    assert _no_sideways(page)
    page.get_by_test_id("ai-engine-picker").scroll_into_view_if_needed()
    _shot(page, f"ai-engine-{w}")
    page.goto(base + "/?tab=agent")
    sel = page.get_by_test_id("agent-engine-switch")
    pw.expect(sel).to_have_value("gemini-flash")
    sel.select_option("claude")
    pw.expect(page.get_by_test_id("ai-engine-claude")).to_have_attribute("aria-checked", "true")
    pw.expect(sel).to_have_value("claude")
    from studio import ai_engines as ae
    assert ae.get_pref(app.state.store, "acc_1") == "claude"
    assert _no_sideways(page)
    assert not errors, errors
    ctx.close()
