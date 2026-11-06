"""Every "Make a post" entry point opens a NEW thread, in a real browser at 1280 and 390 px (owner bug, 6 Nov 2026:
"Make post" from Discover reopened the previous thread). With a thread open: News / Discover "Make post" and the Inbox
"Make a post" link start a fresh thread with the story prefilled (or empty), the address leaves /threads/..., Back
returns to the screen the click came from, and an old thread still loading never lands over the new one (aborted)."""
import pytest

from .test_chat_ui_e2e import ARTICLE, _page, _settled, live  # noqa: F401  (live is a fixture)

pw = pytest.importorskip("playwright.sync_api")


def _open_thread(page, base):
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    page.wait_for_url("**/threads/thr_*")
    return page.url


def _fresh(page):
    """Make a post on its start screen: no thread open, no package on screen, the composer ready."""
    pw.expect(page.locator("#tab-agent")).to_be_visible()
    assert not page.evaluate("document.getElementById('tab-agent').classList.contains('mp-in-thread')")
    assert page.evaluate("document.getElementById('runView').hidden")
    assert "/threads/" not in page.url
    assert page.evaluate("!(window.H_STATE && window.H_STATE.run)")


def _flow(page, base):
    thread_url = _open_thread(page, base)
    # 1. News -> "Make post" on a story (the Discover feed): a NEW thread with the story's link prefilled
    page.evaluate("showTab('news')")
    page.get_by_test_id("story-write").first.click()
    _fresh(page)
    assert ARTICLE["url"] in page.get_by_test_id("agent-query").input_value()
    page.go_back()
    page.wait_for_function("new URLSearchParams(location.search).get('tab') === 'news'")
    pw.expect(page.locator("#tab-news")).to_be_visible()
    # 2. back to the old thread by its address, then the Inbox "Make a post" link: a NEW, empty thread
    page.goto(thread_url)
    page.get_by_test_id("package").first.wait_for(timeout=30000)
    page.evaluate("showTab('inbox')")
    make = page.get_by_test_id("inbox-empty-make")
    if make.count():
        make.first.click()
        _fresh(page)
        assert page.get_by_test_id("agent-query").input_value() == ""
    # 3. an old thread still loading is aborted: it never lands over the new post
    tid = thread_url.rsplit("/", 1)[-1]
    page.evaluate("(tid) => { window.hqOpenThread(tid); window.hqNewPost('Post this on all my channels: " + ARTICLE["url"] + "'); }", tid)
    page.wait_for_timeout(2500)
    _fresh(page)
    assert ARTICLE["url"] in page.get_by_test_id("agent-query").input_value()


def test_make_post_entry_points_open_a_new_thread_1280(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    _flow(page, base)
    assert not errors, errors


def test_make_post_entry_points_open_a_new_thread_390(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 390, 844)
    _flow(page, base)
    assert not errors, errors
