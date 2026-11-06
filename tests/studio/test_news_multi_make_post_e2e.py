"""News › Story scan in a real browser at 1280 and 390 px (owner bug, 6 Nov 2026: "2 stories · Write posts" generated every
carousel inline on News). With a thread open: tick 2 story cards, press "Make a post (2)" and a NEW Make a post thread opens
(the address leaves /threads/...) with the starter prompt and both stories' titles and links in the composer, and not one
request is made to a generate / draft / Buffer endpoint, then or a moment later. A card's own "Make post" does the same with
one story. More than 10 ticked: the button waits and says why."""
import re

import pytest

from .test_chat_ui_e2e import _page, live  # noqa: F401  (live is a fixture)
from .test_new_thread_entry_e2e import _fresh, _open_thread

pw = pytest.importorskip("playwright.sync_api")
STARTER = "Make posts from these stories:"
WRITES = re.compile(r"/api/(sources/draft|agent/|link/preview|preview|submit|posts|house|run|takes|templates/render|media)")


def _story_scan(page):
    page.evaluate("() => api('/api/sources/scan', { method: 'POST', body: { sample: true } })")  # a recorded scan (no network)
    page.evaluate("showTab('sources')")  # the old name lands on News › Story scan
    page.get_by_test_id("sa-cluster").first.wait_for(timeout=30000)


def _watch(page):
    seen = []
    page.on("request", lambda r: seen.append((r.method, r.url)))
    return seen


def _writes(seen):
    """Every request that could write or generate: any non-GET, and GETs on the agent / draft / Buffer endpoints."""
    return [(m, u) for m, u in seen if m != "GET" or WRITES.search(u)]


def _links(text):
    return [ln for ln in text.splitlines() if "http" in ln]


def _flow(page, base, w):
    thread_url = _open_thread(page, base)
    _story_scan(page)
    cards = page.get_by_test_id("sa-cluster")
    picks = []
    for i in range(2):
        card = cards.nth(i)
        card.get_by_test_id("sa-pick").check()
        a = card.locator("h3 a")
        picks.append((a.inner_text().strip(), a.get_attribute("href")))
    btn = page.get_by_test_id("sa-make-picked")
    pw.expect(btn).to_have_text("Make a post (2)")
    seen = _watch(page)
    btn.click()
    _fresh(page)  # a NEW thread: not the one left open
    text = page.get_by_test_id("agent-query").input_value()
    assert text.startswith(STARTER), text
    for title, url in picks:
        assert url in text and title[:40] in text, (title, url, text)
    assert len(_links(text)) == 2  # one link per line
    page.wait_for_timeout(2000)
    _fresh(page)  # still nothing ran on its own
    assert not _writes(seen), _writes(seen)
    # the single card "Make post": the same, with one story (from a thread again, so it must leave it)
    page.goto(thread_url)
    page.get_by_test_id("package").first.wait_for(timeout=30000)
    page.evaluate("showTab('sources')")
    page.get_by_test_id("sa-cluster").first.wait_for(timeout=30000)
    one = page.get_by_test_id("sa-cluster").first
    url = one.locator("h3 a").get_attribute("href")
    seen.clear()
    one.get_by_test_id("sa-make").click()
    _fresh(page)
    text = page.get_by_test_id("agent-query").input_value()
    assert text.startswith(STARTER) and url in text and len(_links(text)) == 1, text
    page.wait_for_timeout(1500)
    assert not _writes(seen), _writes(seen)


def _cap(page):
    page.evaluate("showTab('sources')")
    page.get_by_test_id("sa-cluster").first.wait_for(timeout=30000)
    boxes = page.get_by_test_id("sa-pick")
    if boxes.count() < 11:
        pytest.skip("the recorded scan has fewer than 11 stories on a page")
    for i in range(11):
        boxes.nth(i).check()
    pw.expect(page.get_by_test_id("sa-make-picked")).to_be_disabled()
    pw.expect(page.get_by_test_id("sa-pick-cap")).to_contain_text("up to 10")
    boxes.nth(10).uncheck()
    pw.expect(page.get_by_test_id("sa-make-picked")).to_be_enabled()
    pw.expect(page.get_by_test_id("sa-make-picked")).to_have_text("Make a post (10)")


def test_story_scan_make_a_post_opens_a_new_prefilled_thread_1280(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    _flow(page, base, 1280)
    _cap(page)
    assert not errors, errors


def test_story_scan_make_a_post_opens_a_new_prefilled_thread_390(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 390, 844)
    _flow(page, base, 390)
    assert not errors, errors
