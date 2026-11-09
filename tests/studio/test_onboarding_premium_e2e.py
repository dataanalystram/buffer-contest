"""Setup in the browser (onboarding.js, Playwright): a new account opens on the welcome, walks the steps (each one drawn at
once, sliding in), picks interests, and lands on "Your feed is ready" with stories from the starter feeds picked for them
(studio/starter_feeds.py). Skipping instead still gives News current stories. Desktop and a 390 px phone, light and dark.
Skips without Chromium."""
import os
import time

import httpx
import pytest

from studio import radar, starter_feeds as sf
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_ONB_SHOTS", "")  # a folder to save screenshots into (for a person to look at)


def _feed(url, *a, **k):
    title = sf.LABELS.get(url, "Feed")
    return {"title": title, "stories": [{"title": f"{title}: open model study tops the charts", "url": f"https://news.example.com/{abs(hash(url)) % 10**8}",
                                         "summary": "", "published": time.time() - 3600, "image": ""}]}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("HQ_STARTER_FEEDS", "1")
    monkeypatch.setattr(radar, "fetch", _feed)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="o" * 64,
                      db_path=str(tmp_path / "onb.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(FakeBuffer())))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-onb", "csrf-onb")
    st.update_session("sid-onb", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app
    server.should_exit = True


def _page(chromium, base, width, scheme="light"):
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 800}, color_scheme=scheme)
    ctx.add_init_script("localStorage.setItem('hq_tour_done', '1')")
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-onb", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    return ctx, ctx.new_page()


def _shot(page, name):
    if SHOTS:
        page.wait_for_timeout(400)
        page.screenshot(path=os.path.join(SHOTS, name + ".png"))


@pytest.mark.parametrize("width,scheme", [(1280, "light"), (1280, "dark"), (390, "light")])
def test_setup_walks_from_welcome_to_a_ready_feed(live, chromium, width, scheme):
    base, app = live
    ctx, page = _page(chromium, base, width, scheme)
    try:
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/")
        page.locator("[data-testid=onboarding][data-step='-1']").wait_for()
        assert "Welcome to HuhQueue" in page.get_by_test_id("onboarding").inner_text()
        _shot(page, f"0-welcome-{width}-{scheme}")
        page.get_by_test_id("onboarding-start").click()
        page.locator("[data-testid=onboarding][data-step='0']").wait_for()
        assert page.get_by_test_id("onboarding-progress").inner_text().startswith("Step 1 of 5")
        assert page.get_by_test_id("onboarding-next").inner_text() == "Continue with top tech news"
        page.locator("[data-testid=onboarding-types] [data-v=research]").click()
        page.locator("[data-testid=onboarding-types] [data-v=ai_news]").click()
        assert page.get_by_test_id("onboarding-picked").inner_text().startswith("2 picked")
        _shot(page, f"1-interests-{width}-{scheme}")
        page.get_by_test_id("onboarding-next").click()
        for step in (1, 2, 3):
            page.locator(f"[data-testid=onboarding][data-step='{step}']").wait_for()
            if step == 2:
                page.get_by_test_id("onboarding-kit").wait_for()
                _shot(page, f"3-brand-{width}-{scheme}")
            page.get_by_test_id("onboarding-next").click()
        page.locator("[data-testid=onboarding][data-step='4']").wait_for()
        page.locator("[data-testid=onboarding-recs] [data-testid=rec-card]").first.wait_for()
        assert page.locator("[data-testid=onboarding-recs] [data-testid=rec-card]").count() == 4
        follow = page.get_by_test_id("onboarding-feeds")
        follow.wait_for()
        assert "arXiv cs.AI" in follow.inner_text() and "TechCrunch AI" in follow.inner_text()
        _shot(page, f"4-feed-{width}-{scheme}")
        page.get_by_test_id("onboarding-open-news").click()
        page.locator("#tab-news:not([hidden]) [data-testid=story-item]").first.wait_for()
        assert page.get_by_test_id("onboarding").count() == 0 or not page.locator("#onb").is_visible()
        _shot(page, f"5-news-{width}-{scheme}")
        assert not errors, errors
    finally:
        ctx.close()
    urls = [r["url"] for r in app.state.store.q("SELECT url FROM feeds WHERE user_id='acc_1'")]
    assert sf.BY_TYPE["research"][0] in urls and sf.BY_TYPE["ai_news"][0] in urls


def test_skipping_setup_still_shows_current_news(live, chromium):
    base, app = live
    ctx, page = _page(chromium, base, 1280)
    try:
        page.goto(base + "/")
        page.locator("[data-testid=onboarding][data-step='-1']").wait_for()
        page.keyboard.press("Escape")  # = Skip setup
        page.locator("#onb").wait_for(state="hidden")
        page.goto(base + "/?tab=news")
        page.locator("#tab-news:not([hidden]) [data-testid=story-item]").first.wait_for()
        assert page.locator("[data-testid=story-item]").count() == len(sf.DEFAULT)
        assert "No stories yet" not in page.locator("#stories").inner_text()
    finally:
        ctx.close()
