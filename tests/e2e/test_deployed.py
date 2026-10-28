"""Smoke test against a deployed HuhQueue (e.g. on InstaBox). Skipped unless HUHQ_E2E_URL is set.

  HUHQ_E2E_URL=https://8787-<machine>.instabox.tech HUHQ_E2E_PASSWORD=... pytest tests/e2e/test_deployed.py -q

Leaves the install as it found it: placeholder keys are only typed when no real keys are saved, and removed after.
"""
import os
from urllib.parse import parse_qs, urlparse

import pytest

pw = pytest.importorskip("playwright.sync_api")

URL = os.environ.get("HUHQ_E2E_URL", "").rstrip("/")
PW = os.environ.get("HUHQ_E2E_PASSWORD", "")
pytestmark = pytest.mark.skipif(not (URL and PW), reason="set HUHQ_E2E_URL and HUHQ_E2E_PASSWORD")


@pytest.fixture(scope="module")
def page():
    with pw.sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox", "--disable-dev-shm-usage"])
        pg = b.new_context(viewport={"width": 1440, "height": 900}).new_page()
        pg.set_default_timeout(20000)
        pg.errors = []
        pg.on("pageerror", lambda e: pg.errors.append(str(e)))
        pg.on("dialog", lambda d: d.accept())
        yield pg
        b.close()


def T(page, tid):
    return page.get_by_test_id(tid)


def call(page, method, path, body=None):
    return page.evaluate("""async ([m, p, b]) => {
        const csrf = document.cookie.split('; ').find(c => c.startsWith('huhq_csrf='))?.split('=')[1] || ''
        const r = await fetch(p, {method: m, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, body: b ? JSON.stringify(b) : undefined})
        return {status: r.status, body: await r.json()} }""", [method, path, body])


def test_https_health_and_login_page(page):
    r = page.request.get(URL + "/api/health")
    assert r.ok and r.json()["ok"]
    assert page.request.get(URL + "/api/channels").status == 401  # API is locked without a session
    page.goto(URL + "/")
    T(page, "login-form").wait_for()
    assert T(page, "login-password2").count() == 0, "no password set - first visitor could claim the app"
    T(page, "login-password").fill("definitely-wrong-password")
    T(page, "login-submit").click()
    page.get_by_text("Wrong password").wait_for()
    T(page, "login-password").fill(PW)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_privacy_terms_and_callback_guard(page):
    assert page.request.get(URL + "/privacy").ok and page.request.get(URL + "/terms").ok
    r = page.request.get(URL + "/oauth/instagram/callback?code=x&state=forged", max_redirects=0)
    assert r.status == 303 and "connect_error" in r.headers["location"]


def test_connect_instagram(page):
    keys = call(page, "GET", "/api/app-keys")["body"]["instagram"]
    redirect = keys["redirect_uri"]
    assert redirect == URL + "/oauth/instagram/callback", redirect
    page.goto(URL + "/#/channels?network=instagram")
    placeholder = not keys["configured"]
    if placeholder:
        T(page, "connect-not-configured").wait_for()
        assert "Meta App ID" in T(page, "connect-not-configured").inner_text()
        T(page, "appkeys-instagram_app_id").fill("PLACEHOLDER_APP_ID")
        T(page, "appkeys-instagram_app_secret").fill("placeholder-secret")
        T(page, "appkeys-instagram-save").click()
    T(page, "connect-oauth").wait_for()
    seen = {}
    page.route("https://www.instagram.com/**", lambda route: (seen.update(url=route.request.url),
                                                               route.fulfill(status=200, body="stub", content_type="text/html")))
    try:
        T(page, "connect-oauth").click()
        page.wait_for_url("https://www.instagram.com/**")
    finally:
        page.unroute("https://www.instagram.com/**")
        if placeholder:
            page.goto(URL + "/")
            call(page, "PUT", "/api/app-keys/instagram", {"instagram_app_id": "", "instagram_app_secret": ""})
    u = urlparse(seen["url"])
    q = parse_qs(u.query)
    assert u.netloc == "www.instagram.com" and u.path == "/oauth/authorize"
    if placeholder:
        assert q["client_id"] == ["PLACEHOLDER_APP_ID"]
    assert q["redirect_uri"] == [redirect] and q["response_type"] == ["code"] and len(q["state"][0]) > 30
    for s in ("instagram_business_basic", "instagram_business_content_publish",
              "instagram_business_manage_comments", "instagram_business_manage_insights"):
        assert s in q["scope"][0]


def test_youtube_and_x_redirect_uris(page):
    page.goto(URL + "/")
    keys = call(page, "GET", "/api/app-keys")["body"]
    assert keys["youtube"]["redirect_uri"] == URL + "/oauth/youtube/callback"
    assert keys["x"]["redirect_uri"] == URL + "/oauth/x/callback"
    page.goto(URL + "/#/settings?section=app-keys")
    T(page, "settings-app-keys").wait_for()
    assert not page.errors, page.errors
