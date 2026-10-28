"""End-to-end UI test - part 2: queue, post actions, bulk, approvals. A real browser clicks every control, backed by the demo connector.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e -q      (or after `playwright install chromium`)
"""
import re

import httpx

from ..conftest import make_image
from .conftest import PASSWORD, T, api, goto, toast, ask_ok

# ------------------------------------------------------------------ the test, in order
def test_00_login_and_seed(page, server, tmp_path_factory):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "huhbyte_demo"})
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "second"})
    for i in range(3):
        api(page, "POST", "/api/posts", {"caption": f"seed {i}", "channel_ids": [a["id"]], "when": "queue"})
    api(page, "POST", "/api/posts", {"caption": "seed now", "channel_ids": [a["id"]], "when": "now", "override_rate_limit": True})
    api(page, "POST", "/api/posts", {"caption": "seed draft", "channel_ids": [a["id"]], "when": "draft"})
    api(page, "POST", "/api/worker/tick")
    page.reload()
    T(page, "page-home").wait_for()


def test_05_queue_tabs_filters_and_post_actions(page, server):
    core = server["core"]
    chans = api(page, "GET", "/api/channels")
    cid = chans[0]["id"]
    for cap, when, opts in [("q one", "queue", {}), ("q two", "queue", {}), ("q three", "queue", {}), ("FAIL on purpose", "now", {}),
                            ("manual notify", "now", {"posting_method": "notify"})]:
        r = api(page, "POST", "/api/posts", {"caption": cap, "channel_ids": [cid], "when": when, "options": opts, "override_rate_limit": when == "now"})
        assert "targets" in r, r
    api(page, "POST", "/api/worker/tick")
    goto(page, server, "/queue")
    T(page, "page-queue").wait_for()
    for tab in ("drafts", "approvals", "action", "sent", "failed", "queue"):
        T(page, f"queue-tabs-{tab}").click()
        page.wait_for_timeout(150)
    T(page, "filter-search").fill("q t")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length === 2")
    T(page, "filter-search").fill("")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length > 2")
    T(page, "filter-tags").click(); T(page, "filter-tags-option").first.click(); T(page, "filter-tags-all").click()
    T(page, "filter-type").click(); T(page, "filter-type-option").filter(has_text="Video").click(); T(page, "filter-type-all").click()
    page.keyboard.press("Escape"); page.locator("h1").first.click()
    T(page, "sidebar-channel").first.click()
    T(page, "page-queue").wait_for()
    # drag-and-drop reorder (per channel queue)
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length >= 3")
    first = T(page, "post-card").nth(0)
    third = T(page, "post-card").nth(2)
    first_id = first.get_attribute("data-id")
    first.locator("svg").first.drag_to(third)
    page.wait_for_timeout(800)
    # per-post actions
    card = T(page, "post-card").filter(has_text="q two").first
    card.get_by_test_id("post-copy").click(); toast(page, "copied")
    card.get_by_test_id("post-timeline-toggle").click(); card.get_by_test_id("post-timeline").wait_for()
    card.get_by_test_id("post-to-drafts").click(); toast(page, "drafts")
    T(page, "queue-tabs-drafts").click()
    card = T(page, "post-card").filter(has_text="q two").first
    card.get_by_test_id("post-queue").click(); toast(page, "queue")
    T(page, "queue-tabs-queue").click()
    card = T(page, "post-card").filter(has_text="q two").first
    card.get_by_test_id("post-duplicate").click()
    T(page, "duplicate-channel").last.check()
    T(page, "duplicate-confirm").click(); toast(page, "Copied")
    card.get_by_test_id("post-edit").click()
    T(page, "composer").wait_for()
    T(page, "composer-caption").fill("q two edited")
    T(page, "composer-submit").click(); toast(page, "updated")
    card = T(page, "post-card").filter(has_text="q two edited").first
    card.get_by_test_id("post-publish-now").click()
    try:  # the per-channel rate guard (409) asks "Publish anyway as major news?" once the fake channel is busy
        T(page, "ask").wait_for(timeout=2500)
        ask_ok(page)
    except Exception:
        pass
    toast(page, "Publishing")
    # bulk bar
    T(page, "post-select").nth(0).check()
    T(page, "bulk-bar").wait_for()
    T(page, "bulk-tag").click(); ask_ok(page, "news"); toast(page, "tagged")
    T(page, "post-select").nth(0).check()
    T(page, "bulk-draft").click(); toast(page, "moved to drafts")
    T(page, "queue-tabs-drafts").click()
    T(page, "post-select").nth(0).check()
    T(page, "bulk-queue").click(); toast(page, "added to the queue")
    T(page, "post-select").nth(0).check()
    T(page, "bulk-delete").click(); ask_ok(page); toast(page, "deleted")
    # failed -> retry ; your turn -> mark as posted ; sent -> insights + open
    goto(page, server, "/queue?tab=failed")
    T(page, "post-retry").first.click(); toast(page, "Retrying")
    goto(page, server, "/queue?tab=action")
    T(page, "post-mark-posted").first.click(); toast(page, "posted")
    goto(page, server, "/queue?tab=sent")
    T(page, "post-insights-refresh").first.click(); toast(page, "Insights")
    assert T(page, "post-insights").first.is_visible()
    assert T(page, "post-open").first.get_attribute("href").startswith("https://")
    T(page, "post-delete").first.click(); ask_ok(page); toast(page, "Deleted")
    T(page, "queue-calendar").click()
    T(page, "page-calendar").wait_for()


def test_06_approvals_from_an_agent_token(page, server):
    tok = api(page, "POST", "/api/tokens", {"name": "agent", "scopes": ["read", "write", "publish"], "require_approval": True})["token"]
    cid = api(page, "GET", "/api/channels")[0]["id"]
    for cap in ("agent wants this", "agent wants that"):
        r = httpx.Client(trust_env=False).post(server["base"] + "/api/posts", json={"caption": cap, "channel_ids": [cid], "when": "now"}, headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
    goto(page, server, "/queue?tab=approvals")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-approve]').length === 2")
    T(page, "post-approve").first.click(); T(page, "approve-confirm").click(); toast(page, "Approved")
    T(page, "post-reject").first.click(); T(page, "decision-note").fill("needs a source"); T(page, "reject-confirm").click(); toast(page, "Sent back")


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
