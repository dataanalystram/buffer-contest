"""End-to-end: takeaways across channels, audience demographics and saved Story insights on the Insights page."""
from datetime import datetime, timedelta

from huhqueue.connectors.fake import FakeConnector

from .conftest import PASSWORD, T, api, goto, toast

SHOTS = None  # set to a folder path to save screenshots while developing


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "huhbyte_demo"})
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "second"})
    for i in range(2):
        api(page, "POST", "/api/posts", {"caption": f"seed {i}", "channel_ids": [a["id"]], "when": "now", "override_rate_limit": True})
    api(page, "POST", "/api/worker/tick")


def test_01_takeaways_with_feedback(page, server):
    goto(page, server, "/insights")
    T(page, "insights-takeaways").wait_for()
    first = T(page, "insights-takeaway").first
    first.wait_for()
    first.get_by_test_id("insights-takeaway-helpful").click()
    toast(page, "feedback")
    assert first.get_by_test_id("insights-takeaway-helpful").get_attribute("aria-pressed") == "true"
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/takeaways.png")


def test_02_demographics(page, server):
    goto(page, server, "/insights?view=audience")
    T(page, "insights-demographics").wait_for()
    T(page, "demographics-channel").first.wait_for()
    assert T(page, "demographics-age").first.is_visible()
    assert "United States" in T(page, "demographics-country").first.inner_text()
    T(page, "demographics-who").get_by_text("Engaged audience").click()
    T(page, "demographics-timeframe").select_option("last_30_days")
    T(page, "demographics-age").first.wait_for()
    T(page, "demographics-refresh").click()
    toast(page, "refreshed")
    if SHOTS:
        T(page, "insights-demographics").screenshot(path=f"{SHOTS}/demographics.png")


def test_03_stories_saved(page, server):
    FakeConnector.stories[:] = [{"external_id": "st-e2e", "media_type": "IMAGE",
                                 "timestamp": (datetime.utcnow() - timedelta(hours=2)).isoformat() + "+00:00",
                                 "permalink": "https://example.invalid/s/1"}]
    try:
        goto(page, server, "/insights?view=stories")
        T(page, "insights-stories").wait_for()
        T(page, "stories-sync").click()
        toast(page, "Saved insights")
        T(page, "stories-row").first.wait_for()
        assert T(page, "stories-live").count() >= 1
        if SHOTS:
            page.screenshot(path=f"{SHOTS}/stories.png")
    finally:
        FakeConnector.stories.clear()
    assert not page.errors, page.errors
