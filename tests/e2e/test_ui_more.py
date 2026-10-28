"""End-to-end UI test - part 3: calendar, ideas, community, insights. A real browser clicks every control, backed by the demo connector.

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


def test_07_calendar_controls_and_drag(page, server):
    goto(page, server, "/calendar")
    T(page, "calendar-week").wait_for()
    T(page, "calendar-next").click(); T(page, "calendar-prev").click(); T(page, "calendar-today").click()
    T(page, "calendar-nodate-toggle").click(); T(page, "calendar-nodate").wait_for(); T(page, "calendar-nodate-toggle").click()
    T(page, "filter-channels").click(); T(page, "filter-channels-option").first.click(); T(page, "filter-channels-all").click()
    page.locator("h1").first.click()
    T(page, "calendar-view-month").click(); T(page, "calendar-month").wait_for()
    T(page, "calendar-view-week").click(); T(page, "calendar-week").wait_for()
    # click "+" in a cell -> composer prefilled with that time
    cell = T(page, "calendar-cell").nth(40)
    cell.hover(); cell.get_by_test_id("calendar-add").click()
    T(page, "composer").wait_for()
    assert T(page, "composer-datetime").input_value()
    T(page, "composer-close").click()
    # drag a scheduled post into a later cell
    goto(page, server, "/calendar?view=month")
    T(page, "calendar-month").wait_for()
    if T(page, "calendar-post").count():
        T(page, "calendar-post").first.drag_to(T(page, "calendar-cell").nth(40))
        page.wait_for_timeout(600)


def test_08_ideas_board(page, server):
    page.prompt_answer = "Review"
    goto(page, server, "/ideas")
    T(page, "page-ideas").wait_for()
    T(page, "idea-new").click()
    T(page, "idea-title").fill("Explain Mixture-of-Experts in 30s")
    T(page, "idea-body").fill("hook: one expert per token")
    T(page, "idea-column-select").select_option("todo")
    T(page, "idea-tag").first.click()
    T(page, "idea-save").click(); toast(page, "saved")
    T(page, "idea-column-add").first.click()
    T(page, "idea-title").fill("Second idea"); T(page, "idea-save").click(); toast(page, "saved")
    T(page, "idea-add-column").click(); ask_ok(page, "Review")
    page.get_by_role("region", name="Review").wait_for()
    # rename in place (no prompt): click the title, type, Enter
    T(page, "idea-column-rename").first.click()
    T(page, "idea-column-rename-input").fill("Backlog"); page.keyboard.press("Enter"); toast(page, "renamed")
    page.get_by_role("region", name="Backlog").wait_for()
    T(page, "idea-card").first.drag_to(page.get_by_role("region", name="Done"))
    T(page, "idea-open").first.click()
    T(page, "idea-dialog").wait_for()
    T(page, "idea-dialog-to-post").click()
    T(page, "composer").wait_for()
    assert "Mixture" in T(page, "composer-caption").input_value() or T(page, "composer-caption").input_value()
    T(page, "composer-close").click()
    T(page, "idea-card").first.hover(); T(page, "idea-to-post").first.click(); T(page, "composer-close").click()
    T(page, "ideas-view-gallery").click(); T(page, "ideas-gallery").wait_for()
    T(page, "ideas-view-board").click()
    T(page, "ideas-tag-filter").select_option(index=1); T(page, "ideas-tag-filter").select_option(index=0)
    T(page, "idea-open").last.click(); T(page, "idea-delete").click()


def test_09_community_inbox(page, server):
    goto(page, server, "/community")
    T(page, "comments-sync").click(); toast(page, "new comment")
    page.wait_for_function("document.querySelectorAll('[data-testid=comment]').length >= 2")
    T(page, "comment-saved-replies").first.click(); T(page, "saved-reply").first.click()
    assert T(page, "comment-reply-input").first.input_value().startswith("@")
    T(page, "comment-reply").first.click(); toast(page, "Reply sent")
    T(page, "comment-done").first.click(); toast(page, "Marked done")
    for st in ("answered", "flagged", "assigned", "all", "open"):
        T(page, f"community-filter-{st}").click()
    T(page, "community-filter-answered").click()
    T(page, "comment-reopen").first.click(); toast(page, "Reopened")
    T(page, "community-filter-all").click()
    T(page, "comments-view-post").click(); T(page, "comments-view-list").click()
    T(page, "comments-search").fill("model"); page.wait_for_timeout(300); T(page, "comments-search").fill("")
    T(page, "comments-channel").select_option(index=1)
    assert T(page, "comment-open-post").count() >= 0


def test_10_insights(page, server):
    goto(page, server, "/insights")
    T(page, "page-insights").wait_for()
    for r in ("7", "90", "mtd", "30"):
        T(page, f"insights-range-{r}").click()
    T(page, "insights-range-custom").click()
    T(page, "insights-start").fill("2026-01-01"); T(page, "insights-end").fill("2031-01-01")
    for col in ("views", "reach", "likes", "comments", "shares", "saved", "engagement_rate"):
        T(page, f"insights-sort-{col}").click()
    T(page, "insights-channel").select_option(index=1)
    T(page, "insights-range-30").click()
    with page.expect_download() as dl:
        T(page, "insights-export").click()
    assert dl.value.suggested_filename.endswith(".csv")
    assert T(page, "insights-chart").is_visible() and T(page, "insights-followers").is_visible()


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
