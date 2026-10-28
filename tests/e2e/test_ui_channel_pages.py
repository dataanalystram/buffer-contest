"""Browser tests for the channel page tabs: Publish (goal pill, empty slots, list/calendar/planned grid with reorder),
Community (Comments · Mentions, bulk resolve, bulk reply with a saved reply, AI reply, emoji, Start a post),
Insights (ranges, summary, takeaways, CSV, share as post), Posting schedule (goal, day off, generate) and Settings
(notify default, UTM, queue actions with in-app confirm). Uses the demo network only."""
from .conftest import PASSWORD, T, api, goto, toast


def test_00_login_and_seed(page, server):
    goto(page, server, "/home")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    ch = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-pages", "username": "pages_demo"})
    server["cid"] = ch["id"]
    api(page, "PUT", f"/api/channels/{ch['id']}/slots", {"slots": [{"weekday": d, "time": t} for d in range(7) for t in ("09:00", "18:00")]})
    for cap in ("first queued", "second queued"):
        api(page, "POST", "/api/posts", {"caption": cap, "channel_ids": [ch["id"]], "when": "queue"})
    api(page, "PUT", "/api/library/saved_replies", {"items": [{"id": "ty", "name": "Thanks", "text": "Thanks {author}! 🙌"}]})
    page.reload()
    T(page, "page-home").wait_for()


def test_01_publish_tab_slots_calendar_grid(page, server):
    cid = server["cid"]
    goto(page, server, f"/channel/{cid}")
    T(page, "channel-publish-header").wait_for()
    assert "posts sent this week" in T(page, "channel-goal-pill").inner_text()
    T(page, "empty-slot").first.wait_for()
    assert page.get_by_test_id("post-card").count() == 2
    T(page, "channel-view-calendar").click()
    T(page, "channel-calendar").wait_for()
    assert page.get_by_test_id("calendar-day").count() == 7
    T(page, "channel-view-grid").click()
    T(page, "planned-grid").wait_for()
    tiles = page.get_by_test_id("grid-planned")
    assert tiles.count() == 2 and "second queued" in tiles.first.inner_text()      # last to go out = top-left
    tiles.first.hover()
    T(page, "grid-move-earlier").first.click()
    toast(page, "Queue reordered")
    page.wait_for_function("(document.querySelector('[data-testid=grid-planned]')?.innerText || '').includes('first queued')")
    # an empty slot chip opens the composer at that time
    T(page, "channel-view-list").click()
    T(page, "empty-slot").first.click()
    T(page, "composer").wait_for()
    T(page, "composer-close").click()


def test_02_community_comments_bulk_ai_start_post(page, server):
    cid = server["cid"]
    goto(page, server, f"/channel/{cid}?tab=community")
    T(page, "channel-community").wait_for()
    T(page, "channel-comments-sync").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=channel-comment]').length === 2")
    # reply box pre-fills @author, emoji picker appends
    T(page, "channel-comment-input").first.click()
    assert T(page, "channel-comment-input").first.input_value().startswith("@")
    T(page, "comment-emoji-toggle").first.click()
    T(page, "comment-emoji-option").first.click()
    assert "🙌" in T(page, "channel-comment-input").first.input_value()
    # AI reply (no provider configured -> quick replies), pick one into the box
    T(page, "comment-ai-suggest").first.click()
    T(page, "ai-suggestion").first.wait_for()
    T(page, "ai-suggestion").first.click()
    assert T(page, "channel-comment-input").first.input_value().startswith("@")
    # bulk reply with the saved reply to both comments
    T(page, "comments-select-all").check()
    assert "2 selected" in T(page, "comments-selected-count").inner_text()
    T(page, "bulk-reply-toggle").click()
    T(page, "bulk-saved-reply").select_option("ty")
    assert "Thanks {author}" in T(page, "bulk-saved-preview").inner_text()
    T(page, "bulk-reply-send").click()
    toast(page, "2 comments replied to")
    T(page, "channel-community-empty").wait_for()                                  # nothing unanswered any more
    T(page, "channel-comments-status-replied").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=channel-comment]').length === 2")
    # bulk resolve
    T(page, "comments-select-all").check()
    T(page, "bulk-resolve").click()
    toast(page, "marked done")
    T(page, "channel-comments-status-resolved").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=channel-comment]').length === 2")
    # Start a post opens the composer with the comment quoted
    text = page.get_by_test_id("channel-comment").first.locator("p").first.inner_text()
    T(page, "comment-start-post").first.click()
    T(page, "composer").wait_for()
    assert text in T(page, "composer-caption").input_value()
    T(page, "composer-close").click()


def test_03_mentions_tab(page, server):
    goto(page, server, f"/channel/{server['cid']}?tab=community")
    T(page, "channel-community-kind-mention").click()
    T(page, "channel-comments-sync").click()
    toast(page, "new mention")          # the comment sync already pulled it, so this may say 0
    T(page, "comment-mention-badge").wait_for()
    assert "tech_reviewer" in T(page, "channel-comment").first.inner_text()


def test_04_insights_tab(page, server):
    cid = server["cid"]
    api(page, "POST", "/api/posts", {"caption": "sent now", "channel_ids": [cid], "when": "now"})
    api(page, "POST", "/api/worker/tick")
    goto(page, server, f"/channel/{cid}?tab=insights")
    T(page, "channel-insights-summary").wait_for()
    T(page, "summary-posts").wait_for()
    T(page, "channel-insights-range").select_option("7d")
    T(page, "channel-insights-summary").wait_for()
    T(page, "channel-insights-range").select_option("custom")
    T(page, "channel-insights-start").wait_for()
    T(page, "channel-insights-summary").wait_for()
    assert "range=custom" in T(page, "channel-insights-csv").get_attribute("href")
    with page.expect_download() as dl:
        T(page, "channel-insights-csv").click()
    assert dl.value.suggested_filename.endswith("-insights-posts.csv")
    T(page, "channel-insights-share").click()
    T(page, "composer").wait_for()
    assert "@pages_demo" in T(page, "composer-caption").input_value()
    T(page, "composer-close").click()


def test_05_schedule_and_settings(page, server):
    cid = server["cid"]
    goto(page, server, f"/channel/{cid}?tab=schedule")
    T(page, "channel-schedule").wait_for()
    g = int(T(page, "channel-goal").inner_text())
    T(page, "channel-goal-plus").click()
    page.wait_for_function(f"document.querySelector('[data-testid=channel-goal]').innerText === '{g + 1}'")
    T(page, "slot-day-toggle-6").click()
    toast(page, "day off")
    page.locator('[data-testid=slot-day-col][data-day="6"][data-off="1"]').wait_for()
    assert api(page, "GET", f"/api/channels/{cid}/settings")["disabled_days"] == [6]
    T(page, "slot-day-toggle-6").click()
    page.locator('[data-testid=slot-day-col][data-day="6"][data-off="0"]').wait_for()
    T(page, "generate-goal").click()
    toast(page, "posting time")
    T(page, "channel-tabs-overview").click()
    T(page, "channel-general").wait_for()
    T(page, "channel-notify-default").click()
    toast(page, "Saved")
    T(page, "channel-utm").click()
    T(page, "channel-utm_campaign").fill("launch")
    T(page, "channel-utm_campaign").blur()
    page.wait_for_function("1")
    T(page, "queue-shuffle").click()
    T(page, "queue-shuffle-dialog").wait_for()
    T(page, "queue-shuffle-confirm").click()
    toast(page, "Shuffled")
    st = api(page, "GET", f"/api/channels/{cid}/settings")
    assert st["notify_default"] and st["utm_enabled"]


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
