"""Browser checks (Playwright) for the engines + integrations polish (2 Nov 2026), at 1440 light and 390 dark:
Settings > AI engine shows one clean card per engine (logo, name, Free/Paid badge, status, usage bar), Automatic is one
line with an (i) popover, Your keys lists every provider, the Logs panel shows the ai_call rows and filters/pages them;
Settings > Integrations is a logo grid with Connected / Connect, a one-line value and (i) details, and a key opens in a
dialog; the landing page carries the integrations section with named cards. Never sideways overflow, no console or
page errors. HQ_SHOTS=<dir> keeps screenshots."""
import os
from pathlib import Path

import pytest

from .test_e2e_studio import NO_TOUR, live  # noqa: F401  (the signed-in live server fixture)

pw = pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_SHOTS", "")
VIEWS = [(1440, 900, "light"), (390, 844, "dark")]


def _ctx(chromium, base, w, h, scheme, signed=True):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    ctx.add_init_script(NO_TOUR)
    if signed:
        ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
    return ctx, page, errors


def _no_sideways(page):
    return page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")


def _shot_region(page, first, last, name):
    """A screenshot from the top of `first` to the bottom of `last` (page coordinates), full width."""
    if not SHOTS:
        return
    Path(SHOTS).mkdir(parents=True, exist_ok=True)
    box = page.evaluate("""([a, b]) => { const A = document.querySelector(a).getBoundingClientRect(), B = document.querySelector(b).getBoundingClientRect();
        return {y: A.top + scrollY - 12, h: B.bottom - A.top + 24}; }""", [first, last])
    page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True,
                    clip={"x": 0, "y": max(0, box["y"]), "width": page.viewport_size["width"], "height": box["h"]})


def _seed_logs(app):
    st = app.state.store
    st.audit("acc_1", "you", "ai_call", "read", {"engine_id": "nvidia", "for": "write captions", "latency_ms": 2140,
                                                 "model": "nvidia/nemotron-3.5-lightning-30b-a3b", "provider": "nvidia", "cost_usd": 0,
                                                 "fallback": "Claude out of credit: today's allowance is used"})
    st.audit("acc_1", "you", "ai_call", "read", {"engine_id": "gemini-studio", "for": "plan", "latency_ms": 900, "model": "gemini-flash-latest",
                                                 "provider": "google-ai-studio", "error": "google-ai-studio answered HTTP 429: quota"}, ok=False)
    st.audit("acc_1", "you", "ai_call", "read", {"engine_id": "claude", "for": "plan", "latency_ms": 5320, "model": "claude-sonnet-4-5",
                                                 "provider": "anthropic", "cost_usd": 0.0123})


@pytest.mark.parametrize("w,h,scheme", VIEWS)
def test_settings_ai_engine_cards_keys_and_logs(live, chromium, close_contexts, monkeypatch, w, h, scheme):
    base, fake, app = live
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-not-real")
    from studio import ai_engines
    ai_engines.clear_down()
    _seed_logs(app)
    ctx, page, errors = _ctx(chromium, base, w, h, scheme)
    page.goto(base + "/?tab=settings&sub=ai")  # Settings sidebar: AI engine is its own page (3 Nov 2026)
    page.get_by_test_id("ai-engine-claude").wait_for()
    for eid in ("auto", "gemini-studio", "nvidia", "hf", "claude", "own", "free"):
        assert page.get_by_test_id(f"ai-engine-{eid}").count() == 1
    assert page.get_by_test_id("ai-engine-gemini-flash").count() == 0  # Oracle Gemini is gone from the picker
    assert page.locator("[data-testid=ai-engine-nvidia] .bmark svg").count() == 1  # the NVIDIA logo
    assert "0 of 150 calls today" in page.get_by_test_id("ai-engine-usage-nvidia").inner_text()
    assert "$0.00 of $5" in page.get_by_test_id("ai-engine-usage-claude").inner_text()
    assert page.locator("[data-testid=ai-engine-nvidia] .ai-c-tier").inner_text().strip().lower() == "free"
    assert page.locator("[data-testid=ai-engine-claude] .ai-c-tier").inner_text().strip().lower() == "paid"
    pw.expect(page.get_by_test_id("ai-engine-auto")).to_have_attribute("aria-checked", "true")
    info = page.get_by_test_id("ai-engine-auto-info")  # Automatic: one line + (i)
    info.click()
    tip = page.get_by_test_id("ai-engine-auto-tip")
    pw.expect(tip).to_be_visible()
    assert "NVIDIA" in tip.inner_text() and "Oracle" not in tip.inner_text()
    page.keyboard.press("Escape")
    pw.expect(tip).to_be_hidden()
    page.get_by_test_id("ai-engine-nvidia").click()
    pw.expect(page.get_by_test_id("ai-engine-nvidia")).to_have_attribute("aria-checked", "true")
    assert page.locator("[data-testid=ai-keys-list] > li").count() == 8  # Your keys
    page.get_by_test_id("ai-key-open-openai").click()
    assert page.get_attribute("[data-testid=ai-key-input-openai]", "type") == "password"
    page.get_by_test_id("ai-key-open-openai").click()
    page.get_by_test_id("ai-log-row").first.wait_for()  # Logs
    assert page.get_by_test_id("ai-log-row").count() == 3
    assert "Claude out of credit" in page.get_by_test_id("ai-logs-table").inner_text()
    page.get_by_test_id("ai-logs-status").select_option("error")
    pw.expect(page.get_by_test_id("ai-log-row")).to_have_count(1)
    assert "429" in page.get_by_test_id("ai-log-row").inner_text()
    page.get_by_test_id("ai-logs-status").select_option("")
    pw.expect(page.get_by_test_id("ai-log-row")).to_have_count(3)
    assert _no_sideways(page)
    page.get_by_test_id("ai-engine-picker").scroll_into_view_if_needed()
    _shot_region(page, "#aiH", "[data-testid=ai-logs]", f"settings-ai-engine-{w}-{scheme}")
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("w,h,scheme", VIEWS)
def test_settings_integrations_logo_grid(live, chromium, close_contexts, w, h, scheme):
    base, fake, app = live
    ctx, page, errors = _ctx(chromium, base, w, h, scheme)
    page.goto(base + "/?tab=settings&sub=integrations")
    page.get_by_test_id("integ-tile-ph").wait_for()
    for t in ("buffer", "instagram", "youtube", "linkedin", "x-api", "yt-api", "ai-gemini-studio", "ai-nvidia", "ai-claude", "mcp"):
        assert page.get_by_test_id(f"integ-tile-{t}").count() == 1, t
    assert page.locator("[data-testid=integ-tile-instagram] .bmark svg").count() == 1
    st = page.locator("[data-testid=integ-tile-buffer] [data-testid=integ-tile-status]").inner_text()
    assert st in ("Connected", "Connect")
    page.get_by_test_id("integ-info-ph").click()  # (i) details
    tip = page.locator("#integTip-ph")
    pw.expect(tip).to_be_visible()
    assert "Product Hunt" in tip.inner_text()
    page.keyboard.press("Escape")
    pw.expect(tip).to_be_hidden()
    page.get_by_test_id("integ-act-ph").click()  # a key opens in a dialog, masked
    pw.expect(page.get_by_test_id("integ-dialog")).to_be_visible()
    assert page.get_attribute("[data-testid=integ-input-ph]", "type") == "password"
    page.get_by_test_id("integ-dialog-close").click()
    pw.expect(page.get_by_test_id("integ-dialog")).to_be_hidden()
    assert _no_sideways(page)
    tiles = page.evaluate("[...document.querySelectorAll('.integ-tile')].map(e => e.getBoundingClientRect().right)")
    assert max(tiles) <= w
    _shot_region(page, "#integrations", "[data-testid=integ-group-apps]", f"settings-integrations-{w}-{scheme}")
    assert not errors, errors
    ctx.close()


@pytest.mark.parametrize("w,h,scheme", VIEWS)
def test_landing_integrations_section(live, chromium, close_contexts, w, h, scheme):
    """The landing's integrations section (owner, 2 Nov 2026: "a proper integrations-with-logos section"): the heading, four
    groups of cards (Publish, Create & research, AI engines, AI apps over MCP), each card a coloured logo tile, a name and
    a one-line value; the marks are decorative (the name is the text). No sideways scroll, no errors."""
    base = live[0]
    ctx, page, errors = _ctx(chromium, base, w, h, scheme, signed=False)
    page.goto(base + "/")
    sec = page.get_by_test_id("works-with")
    sec.wait_for()
    assert page.locator("#wwH").inner_text().strip() == "Works with the tools you already use"
    groups = page.evaluate("[...document.querySelectorAll('[data-testid^=integ-group-] h3')].map(h => h.textContent)")
    assert groups == ["Publish", "Create & research", "AI engines", "AI apps over MCP"]
    names = page.evaluate("[...document.querySelectorAll('[data-testid=works-with] .ig-card b')].map(b => b.textContent)")
    assert {"Buffer", "Instagram", "YouTube", "TikTok", "LinkedIn", "Facebook", "X", "Threads", "Pinterest", "Product Hunt",
            "Hugging Face", "GitHub", "Claude", "Gemini", "NVIDIA", "OpenAI", "Groq & OpenRouter", "ChatGPT", "Cursor"} <= set(names)
    cards = page.evaluate("""[...document.querySelectorAll('[data-testid=works-with] .ig-card')].map(c => ({
        value: c.querySelector('.ig-txt span').textContent.trim(), hidden: c.querySelector('svg').getAttribute('aria-hidden'),
        tile: getComputedStyle(c.querySelector('.ig-tile')).backgroundColor, w: c.getBoundingClientRect().right}))""")
    assert len(cards) >= 24 and all(c["value"] and c["hidden"] == "true" for c in cards)
    assert len({c["tile"] for c in cards}) >= 8  # colourful: brand tiles, not one grey
    assert max(c["w"] for c in cards) <= w
    assert _no_sideways(page)
    # versioned static URLs (staticver): the landing's CSS and JS carry ?v=<hash>
    assert page.evaluate("[...document.querySelectorAll('link[rel=stylesheet],script[src]')].every(e => /[?]v=[0-9a-f]{10}$/.test(e.href || e.src))")
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        sec.screenshot(path=str(Path(SHOTS) / f"landing-integrations-{w}-{scheme}.png"))
    assert not errors, errors
    ctx.close()
