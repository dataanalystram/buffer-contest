"""End-to-end: calendar / publish-list / sent filters (channel, type, status, tag, campaign, search, timezone),
URL + localStorage persistence, saved views, per-day counts, "N more", click-to-schedule; Community contact view.
"""
from datetime import datetime, timedelta, timezone

from huhqueue.models import Comment

from .conftest import PASSWORD, T, api, goto, toast

DAY = (datetime.now(timezone.utc) + timedelta(days=2)).replace(hour=10, minute=0, second=0, microsecond=0)
DAY_S = DAY.strftime("%Y-%m-%d")


def count(page, tid):
    return page.locator(f"[data-testid={tid}]").count()


def wait_count(page, tid, n):
    try:
        page.wait_for_function(f"document.querySelectorAll('[data-testid={tid}]').length === {n}", timeout=8000)
    except Exception as e:
        raise AssertionError(f"{tid}: wanted {n}, saw {count(page, tid)} at {page.url}") from e


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "alpha"})
    b = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "beta"})
    tag = api(page, "GET", "/api/library/tags")[0]["id"]
    seeds = [  # caption, channel, format, campaign, tags, hour
        ("Launch recap reel", a, "reel", "DevDay", [tag], 9),
        ("Carousel of YC startups", a, "carousel", "YC W27", [], 11),
        ("Plain post about GPUs", b, "post", "DevDay", [], 13),
        ("Beta reel teaser", b, "reel", "", [tag], 15),
        ("Extra one", a, "post", "", [], 16),
    ]
    for cap, ch, fmt, camp, tags, h in seeds:
        r = api(page, "POST", "/api/posts", {"caption": cap, "channel_ids": [ch["id"]], "when": "custom", "campaign": camp, "tags": tags,
                                             "scheduled_at": f"{DAY_S}T{h:02d}:00+00:00", "options": {"fk_format": fmt}, "override_rate_limit": True})
        assert "targets" in r, r
    assert "targets" in api(page, "POST", "/api/posts", {"caption": "Undated idea draft", "channel_ids": [a["id"]], "when": "draft"})
    server["chans"] = (a, b)
    page.reload(); T(page, "page-home").wait_for()


def test_01_calendar_filters_list_view(page, server):
    a, b = server["chans"]
    goto(page, server, f"/calendar?view=list&date={DAY_S}")
    T(page, "calendar-list").wait_for()
    wait_count(page, "calendar-post", 0)  # list view renders post cards
    wait_count(page, "post-card", 5)
    # channel multi-select with avatars
    T(page, "filter-channels").click()
    T(page, "filter-channels-menu").wait_for()
    T(page, "filter-channels-option").filter(has_text="@beta").click()
    wait_count(page, "post-card", 2)
    assert "ch=" in page.url and b["id"] in page.url
    T(page, "filter-channels-option").filter(has_text="@alpha").click()
    wait_count(page, "post-card", 5)
    T(page, "filter-channels-all").click()
    page.locator("h1").first.click()
    # post type
    T(page, "filter-type").click(); T(page, "filter-type-option").filter(has_text="Reel").click()
    wait_count(page, "post-card", 2)
    T(page, "filter-type-option").filter(has_text="Carousel").click()
    wait_count(page, "post-card", 3)
    page.locator("h1").first.click()
    assert T(page, "filter-type-count").inner_text() == "2"
    # campaign + search stack on top
    T(page, "filter-campaign").click(); T(page, "filter-campaign-option").filter(has_text="DevDay").click()
    wait_count(page, "post-card", 1)
    page.locator("h1").first.click()
    T(page, "filter-search").fill("yc")
    wait_count(page, "post-card", 0)
    T(page, "empty").wait_for()
    T(page, "filter-search").fill("")
    wait_count(page, "post-card", 1)
    # chips remove one filter; clear removes all
    assert count(page, "filter-chip") == 3
    T(page, "filter-chip").filter(has_text="DevDay").click()
    wait_count(page, "post-card", 3)
    T(page, "filter-clear").click()
    wait_count(page, "post-card", 5)


def test_02_status_tag_and_persistence(page, server):
    goto(page, server, f"/calendar?view=list&date={DAY_S}")
    wait_count(page, "post-card", 5)
    T(page, "filter-tags").click(); T(page, "filter-tags-option").first.click()
    wait_count(page, "post-card", 2)
    page.locator("h1").first.click()
    T(page, "filter-status").click(); T(page, "filter-status-option").filter(has_text="Draft").click()
    wait_count(page, "post-card", 0)
    T(page, "filter-status-option").filter(has_text="Scheduled").click()
    wait_count(page, "post-card", 2)
    page.locator("h1").first.click()
    url = page.url
    assert "status=" in url and "tag=" in url
    # reload keeps the URL filters
    page.reload(); wait_count(page, "post-card", 2)
    # a bare URL restores the last filters from localStorage
    goto(page, server, "/home"); T(page, "page-home").wait_for()
    goto(page, server, f"/calendar?view=list&date={DAY_S}")
    page.wait_for_function("location.hash.includes('tag=')")
    wait_count(page, "post-card", 2)
    T(page, "filter-clear").click()
    wait_count(page, "post-card", 5)


def test_03_saved_views(page, server):
    goto(page, server, f"/calendar?view=list&date={DAY_S}")
    T(page, "filter-type").click(); T(page, "filter-type-option").filter(has_text="Reel").click()
    page.locator("h1").first.click()
    wait_count(page, "post-card", 2)
    T(page, "filter-views").click()
    T(page, "filter-view-name").fill("Reels only"); T(page, "filter-view-save").click()
    toast(page, "Reels only")
    page.locator("h1").first.click()
    T(page, "filter-clear").click(); wait_count(page, "post-card", 5)
    T(page, "filter-views").click(); T(page, "filter-view-apply").filter(has_text="Reels only").click()
    wait_count(page, "post-card", 2)
    assert api(page, "GET", "/api/views")[0]["filters"]["type"] == "reel"
    T(page, "filter-clear").click(); wait_count(page, "post-card", 5)


def test_04_week_month_counts_more_timezone_click_to_schedule(page, server):
    goto(page, server, f"/calendar?view=month&date={DAY_S}&tz=UTC")
    T(page, "calendar-month").wait_for()
    page.wait_for_function("[...document.querySelectorAll('[data-testid=calendar-day-count]')].some(e => e.innerText.trim() === '5')")
    # 5 posts on one day, 3 shown, then "2 more"
    more = T(page, "calendar-more").first
    assert more.inner_text().strip() == "2 more"
    before = count(page, "calendar-post")
    more.click()
    page.wait_for_function(f"document.querySelectorAll('[data-testid=calendar-post]').length === {before + 2}")
    # week view in UTC: the 09:00 post sits in the 09:00 row; switching timezone moves it
    T(page, "calendar-view-week").click(); T(page, "calendar-week").wait_for()
    assert any("5 posts" in t for t in T(page, "calendar-day-count").all_inner_texts())
    first_time = T(page, "calendar-post").first.inner_text()
    assert "09:00" in first_time, first_time
    T(page, "filter-timezone").select_option("Asia/Kolkata")
    page.wait_for_function("document.querySelector('[data-testid=calendar-post]').innerText.includes('14:30')")
    T(page, "filter-timezone").select_option("UTC")
    # click an empty cell -> composer at that time
    cell = page.locator("[data-testid=calendar-cell][data-empty='1']").nth(10)
    cell.click(position={"x": 8, "y": 30})
    T(page, "composer").wait_for()
    assert T(page, "composer-datetime").input_value()
    T(page, "composer-close").click()
    # posting-time markers and the No date tray
    assert count(page, "calendar-slot") > 0
    T(page, "calendar-slots-toggle").click(); wait_count(page, "calendar-slot", 0); T(page, "calendar-slots-toggle").click()
    T(page, "calendar-nodate-toggle").click()
    T(page, "calendar-nodate").get_by_text("Undated idea draft").wait_for()
    T(page, "calendar-nodate-toggle").click()


def test_05_publish_list_and_sent_use_the_same_filters(page, server):
    goto(page, server, "/queue")
    T(page, "page-queue").wait_for()
    wait_count(page, "post-card", 5)
    T(page, "filter-campaign").click(); T(page, "filter-campaign-option").filter(has_text="YC W27").click()
    wait_count(page, "post-card", 1)
    page.locator("h1").first.click()
    assert T(page, "post-campaign").first.inner_text() == "YC W27"
    # switching to the calendar keeps the filter (shared URL/localStorage state)
    T(page, "queue-calendar").click(); T(page, "page-calendar").wait_for()
    page.wait_for_function("location.hash.includes('campaign=YC')")
    T(page, "filter-clear").click()
    # Sent tab has the bar too
    a, _ = server["chans"]
    api(page, "POST", "/api/posts", {"caption": "Sent reel now", "channel_ids": [a["id"]], "when": "now", "override_rate_limit": True, "options": {"fk_format": "reel"}})
    api(page, "POST", "/api/worker/tick")
    goto(page, server, "/queue?tab=sent")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length >= 1")
    T(page, "filter-type").click(); T(page, "filter-type-option").filter(has_text="Carousel").click()
    wait_count(page, "post-card", 0)
    T(page, "filter-type-option").filter(has_text="Reel").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length >= 1")
    page.locator("h1").first.click()
    T(page, "filter-clear").click()


def test_06_community_contact_view(page, server):
    a, b = server["chans"]
    core = server["core"]
    with core.db.session() as s:
        for i, (ch, who, st, cap) in enumerate([(a, "fan_one", "open", "Launch recap"), (b, "fan_one", "replied", "GPU post"), (a, "fan_two", "open", "Launch recap")]):
            s.add(Comment(channel_id=ch["id"], external_id=f"e2e{i}", author=who, text=f"comment {i} from {who}", status=st, media_caption=cap,
                          at=datetime.utcnow() - timedelta(hours=i)))
    goto(page, server, "/community")
    T(page, "page-community").wait_for()
    page.evaluate("window.dispatchEvent(new Event('huhq:refresh'))")  # seeded straight into the DB
    T(page, "comments-search").fill("from fan_")  # only the seeded comments (earlier tests synced others)
    T(page, "community-filter-open").click()
    wait_count(page, "comment-card", 2)
    T(page, "community-filter-answered").click()
    wait_count(page, "comment-card", 1)
    T(page, "community-filter-all").click()
    wait_count(page, "comment-card", 3)
    # flag one, then the Flagged filter shows it
    T(page, "comment-flag").first.click(); toast(page, "flag")
    T(page, "community-filter-flagged").click(); wait_count(page, "comment-card", 1)
    T(page, "community-filter-all").click(); wait_count(page, "comment-card", 3)
    # commenter profile chip -> their history across posts and channels
    T(page, "commenter-chip").filter(has_text="fan_one").first.click()
    T(page, "contact-panel").wait_for()
    page.wait_for_function("document.querySelectorAll('[data-testid=contact-history-item]').length === 2")
    assert "2 comments" in T(page, "contact-panel").inner_text()
    T(page, "contact-close").click()
    # saved reply into the box, assign, mark done
    T(page, "comment-saved-replies").first.click(); T(page, "saved-reply").first.click()
    assert T(page, "comment-reply-input").first.input_value()
    T(page, "comment-assign").first.click(); T(page, "comment-assign-input").fill("Ram"); page.keyboard.press("Enter"); toast(page, "Assigned")
    T(page, "comment-done").first.click(); toast(page, "done")


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
