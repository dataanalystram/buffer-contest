"""Delete a thread from the conversation sidebar in a real browser, at 1280 and 390 px (owner asks, 4 Nov 2026 23:45;
tests/studio/test_thread_bin.py is the unit side). Deleting a thread is like deleting chat history: every thread has a
trash button (Make a post, One take, failed ones too); Cancel changes nothing; Delete removes the conversation only, and
the take stays in One take. Make a post threads keep their rename button. No horizontal overflow at 390 px."""
import json
import time

import pytest

from studio.actions import Actor

from .test_chat_ui_e2e import ARTICLE, _no_overflow, _page, _settled, live  # noqa: F401  (live is a fixture)

pw = pytest.importorskip("playwright.sync_api")


def _seed_takes(app):
    st = app.state.store
    ids = []
    for name in ("Keynote talk", "Podcast episode"):
        ids.append(st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                        ("acc_1", "take", json.dumps({"name": name, "channels": []}), "ready", "[]",
                         json.dumps({"clips": []}), time.time() - 60, time.time() - 60)))
    return ids


def _open_thread(page, base):
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)


def _flow(page, app, keep_id, del_id, phone):
    st = app.state.store

    def side():
        if phone:
            page.get_by_test_id("chat-sidebar-open").click()
        pw.expect(page.get_by_test_id("chat-sidebar")).to_be_visible()
    side()
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(3)
    assert page.get_by_test_id("chat-thread-delete").count() == 3          # every thread, the post thread too
    assert page.get_by_test_id("chat-thread-rename").count() == 1          # the post thread keeps rename
    # Cancel changes nothing
    page.locator(f"#hcList [data-del-thread='{keep_id}']").click()
    dlg = page.get_by_test_id("ask-dialog")
    pw.expect(dlg).to_be_visible()
    pw.expect(page.get_by_test_id("ask-message")).to_contain_text("Only the conversation is deleted")
    if phone:
        _no_overflow(page, 390)
        box = dlg.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= 391
    page.get_by_test_id("ask-cancel").click()
    pw.expect(dlg).to_have_count(0)
    pw.expect(page.get_by_test_id("chat-sidebar")).to_be_visible()          # the drawer stays open behind the dialog
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(3)
    # Delete: gone from the sidebar, the take stays in One take, untouched
    page.locator(f"#hcList [data-del-thread='{keep_id}']").click()
    page.get_by_test_id("ask-ok").click()
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(2)
    pw.expect(page.locator("#toast")).to_contain_text("Your media stays")
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (keep_id,))[0]["status"] == "ready"
    # the second take thread too: its take is never deleted either
    if phone and page.get_by_test_id("chat-sidebar").is_hidden():
        side()
    page.locator(f"#hcList [data-del-thread='{del_id}']").click()
    page.get_by_test_id("ask-ok").click()
    pw.expect(page.get_by_test_id("chat-thread")).to_have_count(1)
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (del_id,))[0]["status"] == "ready"
    assert {t["kind"] for t in app.state.house.threads.list(Actor("acc_1"))["threads"]} == {"take"}
    assert len(app.state.house.threads.list(Actor("acc_1"))["threads"]) == 2
    if phone:
        _no_overflow(page, 390)


def test_delete_one_take_threads_1280(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    keep_id, del_id = _seed_takes(app)
    ctx, page, errors = _page(chromium, base, 1280, 900)
    _open_thread(page, base)
    _flow(page, app, keep_id, del_id, phone=False)
    assert not errors, errors


def test_delete_one_take_threads_390(live, chromium, close_contexts):  # noqa: F811
    base, app = live
    keep_id, del_id = _seed_takes(app)
    ctx, page, errors = _page(chromium, base, 390, 844)
    _open_thread(page, base)
    _flow(page, app, keep_id, del_id, phone=True)
    assert not errors, errors
