"""End-to-end: the Publish bulk bar (select all, reschedule, tag, campaign, duplicate, move, approve, CSV) and Settings
(bulk channel edit, a switch per notification kind, pause, Send test)."""
from .conftest import PASSWORD, T, api, ask_ok, goto, toast


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "bulk_one"})
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "bulk_two"})
    api(page, "PATCH", f"/api/channels/{a['id']}", {"daily_limit": 20})
    for i in range(4):
        api(page, "POST", "/api/posts", {"caption": f"bulk seed {i}", "channel_ids": [a["id"]], "when": "queue"})
    page.reload()
    T(page, "page-home").wait_for()


def _select_all(page):
    T(page, "post-select").first.check()
    T(page, "bulk-bar").wait_for()
    T(page, "bulk-select-all").check()


def test_01_bulk_bar(page, server):
    one, two = api(page, "GET", "/api/channels")
    goto(page, server, f"/queue/{one['id']}")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length === 4")
    _select_all(page)
    assert T(page, "bulk-count").inner_text().startswith("4")
    T(page, "bulk-reschedule").click()
    T(page, "bulk-shift").fill("1")
    T(page, "bulk-shift-apply").click(); toast(page, "4 post\\(s\\) moved")

    _select_all(page)
    T(page, "bulk-campaign").click(); ask_ok(page, "DevDay"); toast(page, "updated")
    posts = api(page, "GET", f"/api/posts?channel_id={one['id']}")
    assert {t["campaign"] for t in posts} == {"DevDay"}

    _select_all(page)
    with page.expect_download() as dl:
        T(page, "bulk-export").click()
    assert dl.value.suggested_filename.startswith("huhqueue-posts-")

    T(page, "post-select").first.check()
    T(page, "bulk-duplicate").click(); toast(page, "duplicated")

    T(page, "post-select").first.check()
    T(page, "bulk-move").click()
    T(page, "bulk-move-channel").select_option(two["id"])
    T(page, "bulk-move-apply").click(); toast(page, "1 post\\(s\\) moved")
    assert len(api(page, "GET", f"/api/posts?channel_id={two['id']}")) == 1


def test_02_bulk_approve(page, server):
    import httpx
    tok = api(page, "POST", "/api/tokens", {"name": "agent", "scopes": ["read", "write", "publish"], "require_approval": True})["token"]
    cid = api(page, "GET", "/api/channels")[0]["id"]
    for cap in ("agent one", "agent two"):
        r = httpx.Client(trust_env=False).post(server["base"] + "/api/posts", json={"caption": cap, "channel_ids": [cid], "when": "queue"},
                                               headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
    goto(page, server, "/queue?tab=approvals")
    page.wait_for_function("document.querySelectorAll('[data-testid=post-card]').length === 2")
    _select_all(page)
    T(page, "bulk-approve").click(); toast(page, "2 post\\(s\\) approved")
    assert not [t for t in api(page, "GET", "/api/posts?status=pending_approval")]


def test_03_settings_bulk_channel_edit(page, server):
    goto(page, server, "/settings?section=channels")
    T(page, "bulk-channel-edit").wait_for()
    T(page, "bulk-ch-timezone").fill("America/New_York")
    T(page, "bulk-ch-link_shortening").select_option("on")
    T(page, "bulk-ch-day-6").click()
    T(page, "bulk-ch-apply").click(); toast(page, "Updated 2 channel")
    for ch in api(page, "GET", "/api/channels"):
        assert ch["timezone"] == "America/New_York"
        st = api(page, "GET", f"/api/channels/{ch['id']}/settings")
        assert st["link_shortening"] is True and st["disabled_days"] == [6]


def test_04_notification_switches_and_test(page, server):
    goto(page, server, "/settings?section=notifications")
    T(page, "settings-notifications").wait_for()
    assert T(page, "notify-test").is_disabled()
    T(page, "notify-webhook").fill(server["base"] + "/api/health")
    T(page, "notify-save").click(); toast(page, "Saved")
    T(page, "notify-test").click(); toast(page, "Test sent")
    T(page, "notify-kind-failed").click(); toast(page, "Saved")
    assert api(page, "GET", "/api/settings")["notify_events"]["failed"] is False
    T(page, "notify-pause").click(); toast(page, "Push paused")
    assert api(page, "GET", "/api/settings")["notify_paused"] is True
    assert T(page, "notify-kind-approval").is_disabled()
    T(page, "notify-pause").click(); toast(page, "Push resumed")
    assert T(page, "notify-email-failed").is_disabled()          # needs an address first
    T(page, "notify-email-to").fill("ram@example.com")
    T(page, "notify-recap-time").fill("07:45")
    T(page, "notify-email-save").click(); toast(page, "Saved")
    T(page, "notify-email-failed").click(); toast(page, "Saved")
    st = api(page, "GET", "/api/settings")
    assert st["notify_email"]["failed"] is True and st["notify_email_to"] == "ram@example.com" and st["recap_time"] == "07:45"


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
