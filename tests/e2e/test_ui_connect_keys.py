"""End-to-end UI test - part 5: App keys typed in Settings make "Connect with Instagram" go to instagram.com,
the friendly "add your Meta App ID" screen, X connect, and the channel page's History/Community/Insights tabs.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e -q
"""
from urllib.parse import parse_qs, urlparse

from .conftest import PASSWORD, T, api, ask_ok, goto, toast


def _capture_redirect(page, host_prefix):
    seen = {}

    def handler(route):
        seen["url"] = route.request.url
        route.fulfill(status=200, body="<h1>stub consent page</h1>", content_type="text/html")
    page.route(f"{host_prefix}**", handler)
    return seen


def test_00_login(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_01_friendly_screen_when_meta_app_id_missing(page, server):
    goto(page, server, "/channels?network=instagram")
    T(page, "connect-not-configured").wait_for()
    assert "Meta App ID" in T(page, "connect-not-configured").inner_text()
    assert T(page, "connect-redirect-uri").inner_text().endswith("/oauth/instagram/callback")
    assert T(page, "connect-oauth").count() == 0


def test_02_paste_keys_then_connect_redirects_to_instagram(page, server):
    goto(page, server, "/channels?network=instagram")
    T(page, "appkeys-instagram_app_id").fill("PLACEHOLDER_APP_ID")
    T(page, "appkeys-instagram_app_secret").fill("placeholder-secret-123")
    T(page, "appkeys-instagram-save").click()
    toast(page, "ready to connect")
    T(page, "connect-oauth").wait_for()
    seen = _capture_redirect(page, "https://www.instagram.com/")
    T(page, "connect-oauth").click()
    page.wait_for_url("https://www.instagram.com/**")
    u = urlparse(seen["url"])
    q = parse_qs(u.query)
    assert u.path == "/oauth/authorize" and q["client_id"] == ["PLACEHOLDER_APP_ID"] and q["response_type"] == ["code"]
    for s in ("instagram_business_basic", "instagram_business_content_publish",
              "instagram_business_manage_comments", "instagram_business_manage_insights"):
        assert s in q["scope"][0]
    assert len(q["state"][0]) > 30 and q["redirect_uri"][0].endswith("/oauth/instagram/callback")
    page.unroute("https://www.instagram.com/**")


def test_03_settings_app_keys_section_and_x(page, server):
    goto(page, server, "/settings?section=app-keys")
    T(page, "settings-app-keys").wait_for()
    assert T(page, "appkeys-instagram-status").inner_text() == "Ready"
    assert T(page, "appkeys-instagram_app_secret").input_value() == ""  # secrets never come back
    T(page, "appkeys-x_client_id").fill("PLACEHOLDER_X_CLIENT")
    T(page, "appkeys-x-save").click()
    toast(page, "keys saved")
    goto(page, server, "/channels?network=x")
    seen = _capture_redirect(page, "https://x.com/")
    T(page, "connect-oauth").click()
    page.wait_for_url("https://x.com/**")
    q = parse_qs(urlparse(seen["url"]).query)
    assert q["client_id"] == ["PLACEHOLDER_X_CLIENT"] and q["code_challenge_method"] == ["S256"]
    assert "media.write" in q["scope"][0] and "offline.access" in q["scope"][0]
    page.unroute("https://x.com/**")
    goto(page, server, "/settings?section=app-keys")
    T(page, "appkeys-instagram-clear").click()
    ask_ok(page)  # in-app confirm since the live-test fixes
    toast(page, "Keys removed")
    T(page, "appkeys-x-clear").click()
    ask_ok(page)
    toast(page, "Keys removed")


def test_04_channel_tabs(page, server):
    ch = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-tabs", "username": "tabs_demo"})
    goto(page, server, f"/channel/{ch['id']}?tab=history")
    page.get_by_text("History isn’t available").wait_for()
    goto(page, server, f"/channel/{ch['id']}?tab=community")
    T(page, "channel-comments-sync").click()
    T(page, "channel-comment").first.wait_for()
    T(page, "channel-comment-input").first.fill("thanks!")
    T(page, "channel-comment-send").first.click()
    toast(page, "Reply posted")
    goto(page, server, f"/channel/{ch['id']}?tab=insights")
    T(page, "channel-insights-summary").wait_for()
    assert not page.errors, page.errors
