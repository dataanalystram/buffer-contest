"""Make a post composer in a real browser at 1280 and 390 px (owner live check of #137, 6 Nov 2026):
(1) "test" gets the short reply with no agent card ("HuhQueue agent · Standard plan, written with built-in") and no
"Try again with a different angle" chip; (2) the Inbox's top-right "Make a post" opens a clean new thread, never the
previous story's text; (3) a text-only post's review chip says "Text only", never "Image"; and a regenerate answer that
lands after New post never reopens the old run over the new post."""
import time

import pytest

from studio import agent_brain

from .test_chat_ui_e2e import ARTICLE, _page, live  # noqa: F401  (live is a fixture)
from .test_new_thread_entry_e2e import _fresh, _open_thread

pw = pytest.importorskip("playwright.sync_api")


def _send(page, base, text):
    page.goto(base + "/?tab=agent")
    q = page.get_by_test_id("agent-query")
    q.fill(text)
    q.press("Enter")


def _flow(page, base, monkeypatch):
    # (1) a short reply: just the reply
    _send(page, base, "test")
    page.get_by_test_id("thread-msg").first.wait_for(timeout=30000)
    page.wait_for_timeout(600)
    pw.expect(page.get_by_test_id("mp-agent-msg")).to_be_hidden()
    pw.expect(page.get_by_test_id("agent-brain")).to_be_hidden()
    chips = [c.inner_text() for c in page.get_by_test_id("reply-chip").all()]
    assert not any("different angle" in c for c in chips), chips
    # (3) a text-only post: the review chip says Text only
    _send(page, base, 'Post this exact caption on X only: "Plain words, no picture."')
    page.get_by_test_id("mp-ready-item").first.wait_for(timeout=60000)
    meta = page.get_by_test_id("mp-ready-meta").first.inner_text()
    assert meta.startswith("Text only") and "Image" not in meta, meta
    # (2) the Inbox's top-right Make a post: a clean new thread, never the previous story's text
    page.evaluate("(t) => window.hqNewPost(t)", "Post this on all my channels: " + ARTICLE["url"])
    assert ARTICLE["url"] in page.get_by_test_id("agent-query").input_value()
    page.evaluate("showTab('inbox')")
    page.get_by_test_id("inbox-make").first.click()
    _fresh(page)
    assert page.get_by_test_id("agent-query").input_value() == ""
    # a late regenerate answer never reopens the old run over a new post
    _open_thread(page, base)
    real = agent_brain.AgentBrain.reply

    def slow(self, *a, **k):
        time.sleep(2.5)
        return real(self, *a, **k)
    monkeypatch.setattr(agent_brain.AgentBrain, "reply", slow)
    regen = page.get_by_test_id("mp-regenerate")
    regen.scroll_into_view_if_needed()
    regen.click()
    page.wait_for_timeout(300)
    page.evaluate("() => window.hqNewPost('')")
    page.wait_for_timeout(4000)
    _fresh(page)
    monkeypatch.setattr(agent_brain.AgentBrain, "reply", real)


def test_composer_fixes_1280(live, chromium, close_contexts, monkeypatch):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    _flow(page, base, monkeypatch)
    assert not errors, errors


def test_composer_fixes_390(live, chromium, close_contexts, monkeypatch):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, 390, 844)
    _flow(page, base, monkeypatch)
    assert not errors, errors
