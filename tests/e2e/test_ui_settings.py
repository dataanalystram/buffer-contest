"""End-to-end UI test - part 4: channel dashboard, settings, global chrome, disconnect. A real browser clicks every control, backed by the demo connector.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e -q      (or after `playwright install chromium`)
"""
import re

import httpx

from ..conftest import make_image
from .conftest import PASSWORD, T, api, goto, toast, ask_ok

def close_palette(page):
    """The command palette is a full-screen overlay; make sure it is gone before the next click."""
    pal = page.get_by_test_id("command-palette")
    if pal.count() and pal.is_visible():
        page.keyboard.press("Escape")
    pal.wait_for(state="hidden")


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


def test_11_channel_dashboard_and_posting_schedule(page, server):
    cid = api(page, "GET", "/api/channels")[-1]["id"]
    goto(page, server, f"/channel/{cid}?tab=schedule")
    T(page, "channel-schedule").wait_for()
    T(page, "channel-timezone").select_option("Asia/Kolkata"); toast(page, "Saved")
    T(page, "channel-limit-plus").click(); T(page, "channel-limit-minus").click()
    for p in ("weekdays-3", "daily-1", "huhbyte"):
        T(page, f"channel-preset-{p}").click()
    T(page, "slot-day").select_option("2"); T(page, "slot-time").fill("07:15"); T(page, "slot-add").click()
    page.get_by_test_id("slot").filter(has_text="07:15").wait_for()
    T(page, "slot-day-toggle-6").click()
    T(page, "slot-day-toggle-6").click()
    T(page, "slot").first.hover(); T(page, "slot-remove").first.click()
    T(page, "slot-clear").click(); T(page, "slot-clear-confirm").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=slot]').length === 0")
    T(page, "channel-preset-huhbyte").click()
    T(page, "channel-tabs-overview").click()
    T(page, "channel-pause").click(); toast(page, "Saved"); T(page, "channel-pause").click()
    T(page, "channel-refresh-token").click(); toast(page, "Token refreshed")
    T(page, "channel-refresh-profile").click(); toast(page, "Profile refreshed")
    T(page, "channel-queue").click(); T(page, "page-queue").wait_for()
    goto(page, server, f"/channel/{cid}")
    T(page, "channel-new-post").click(); T(page, "composer").wait_for(); T(page, "composer-close").click()
    T(page, "channel-reconnect").click(); T(page, "connect-explainer").wait_for()


def test_12_settings_every_section(page, server):
    goto(page, server, "/settings")
    T(page, "settings-general").wait_for()
    T(page, "pref-hashtag-limit").fill("4"); T(page, "pref-default-when").select_option("prioritize")
    T(page, "pref-cta-handle").fill("@huhbyte"); T(page, "pref-cta-default").select_option("both")
    T(page, "pref-cta-text").fill("Follow @huhbyte for daily AI news"); T(page, "pref-daily-limit").fill("3"); T(page, "pref-timezone").fill("Europe/Dublin")
    T(page, "pref-cta-card").fill("Follow @huhbyte")
    T(page, "pref-save").click(); toast(page, "saved")
    T(page, "settings-nav-schedule").click(); T(page, "settings-schedule-channel").first.wait_for()
    T(page, "settings-nav-public-url").click()
    T(page, "tunnel-start-cloudflared").click()
    T(page, "tunnel-error").wait_for()  # cloudflared is not installed in CI -> clear message
    T(page, "tunnel-ngrok-domain").fill("demo.ngrok-free.app"); T(page, "tunnel-start-ngrok").click()
    T(page, "tunnel-stop").click()
    T(page, "public-url-input").fill("https://media.example.com"); T(page, "public-url-save").click(); toast(page, "Public URL saved")
    page.wait_for_function("document.querySelector('[data-testid=public-url-current]').innerText.includes('media.example.com')")
    assert "media.example.com" in T(page, "public-url-current").inner_text()
    T(page, "settings-nav-library").click()
    for kind in ("hashtag_groups", "saved_replies", "tags", "templates"):
        T(page, f"library-{kind}").click()
        T(page, "library-add").click()
        T(page, "library-item").last.get_by_label("Name").fill(f"New {kind}")
        T(page, "library-save").click(); toast(page, "Library saved")
    T(page, "library-remove").last.click(); T(page, "library-save").click(); toast(page, "Library saved")
    T(page, "settings-nav-notifications").click()
    T(page, "notify-webhook").fill("https://ntfy.sh/huhbyte-test"); T(page, "notify-save").click(); toast(page, "Saved")
    T(page, "notify-webhook").fill(""); T(page, "notify-save").click()
    T(page, "settings-nav-api").click()
    T(page, "token-name").fill("claude-desktop"); T(page, "token-rate").fill("30")
    T(page, "token-scope-comments").click(); T(page, "token-tool-list_channels").click(); T(page, "token-approval").click()
    T(page, "token-create").click(); T(page, "token-minted").wait_for()
    assert "mcpServers" in T(page, "token-minted").inner_text()
    T(page, "token-toggle-approval").first.click()
    T(page, "token-revoke").first.click(); toast(page, "revoked")
    assert T(page, "audit-table").is_visible()
    T(page, "settings-nav-security").click()
    T(page, "security-current").fill(PASSWORD); T(page, "security-new").fill(PASSWORD + "x"); T(page, "security-change").click(); toast(page, "changed")
    T(page, "security-current").fill(PASSWORD + "x"); T(page, "security-new").fill(PASSWORD); T(page, "security-change").click(); toast(page, "changed")
    T(page, "settings-nav-shortcuts").click(); T(page, "settings-shortcuts").wait_for()
    T(page, "settings-nav-about").click(); T(page, "settings-about").wait_for()


def test_13_global_chrome_palette_shortcuts_notifications_logout(page, server):
    goto(page, server, "/home")
    T(page, "page-home").wait_for()
    for s in ("stat-scheduled", "stat-drafts", "stat-sent", "stat-comments"):
        assert T(page, s).is_visible()
    T(page, "home-post-all").click(); T(page, "composer").wait_for()
    assert T(page, "composer-channel").count() == page.locator("[data-testid=composer-channel][aria-pressed=true]").count()
    T(page, "composer-close").click()
    T(page, "home-create").click(); T(page, "composer").wait_for(); T(page, "composer-close").click()
    T(page, "open-command-palette").click()
    # click the exact item: a 2+ word query puts "Ask HuhQueue" first, so Enter asked the assistant (the old flake)
    T(page, "command-input").fill("calendar week view")
    T(page, "command-item").filter(has_text="Calendar - week view").first.click()
    page.locator("[data-testid=calendar-view-week][aria-selected=true]").wait_for()
    close_palette(page)
    page.keyboard.press("Control+k"); T(page, "command-input").fill("community")
    T(page, "command-item").filter(has_text="Go to Community").first.click()
    T(page, "page-community").wait_for()
    close_palette(page)
    for key, tid in (("h", "page-home"), ("i", "page-ideas"), ("p", "page-queue"), ("c", "page-calendar"), ("m", "page-community"), ("a", "page-insights"), ("s", "page-settings")):
        page.locator("body").click(position={"x": 5, "y": 300})
        page.keyboard.press("g"); page.keyboard.press(key)
        T(page, tid).wait_for()
    for nav in ("home", "create", "publish", "calendar", "community", "insights", "settings"):
        T(page, f"nav-{nav}").click()
    T(page, "sidebar-channel").first.click(); T(page, "page-queue").wait_for()
    T(page, "sidebar-connect").click(); T(page, "page-channels").wait_for()
    T(page, "notifications-button").click(); T(page, "notifications-panel").wait_for()
    T(page, "notifications-button").click()
    T(page, "logout").click()
    T(page, "login-form").wait_for()
    T(page, "login-password").fill(PASSWORD); T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_14_disconnect_channel(page, server):
    chans = api(page, "GET", "/api/channels")
    goto(page, server, f"/channel/{chans[-1]['id']}?tab=overview")
    close_palette(page)
    T(page, "channel-disconnect").click(); T(page, "channel-disconnect-confirm").click()
    T(page, "page-channels").wait_for()
    assert len(api(page, "GET", "/api/channels")) == len(chans) - 1


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
