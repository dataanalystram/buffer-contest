"""End-to-end UI test - part 1: first run, connect flows, composer. A real browser clicks every control, backed by the demo connector.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e -q      (or after `playwright install chromium`)
"""
import re

import httpx

from ..conftest import make_image
from .conftest import PASSWORD, T, api, goto, toast

# ------------------------------------------------------------------ the test, in order
def test_01_first_run_creates_password_and_logs_in(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill("mismatch-123")
    T(page, "login-submit").click()
    page.get_by_role("alert").filter(has_text="match").wait_for()
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_02_connect_channel_via_oauth_consent(page, server):
    T(page, "home-connect").click()
    T(page, "page-channels").wait_for()
    for net in ("instagram", "tiktok", "youtube", "x", "linkedin"):
        T(page, f"connect-{net}").click()
        T(page, "connect-explainer").wait_for()
        assert T(page, "connect-not-configured").is_visible()  # no app keys in the e2e env
        T(page, "connect-advanced").click()
        T(page, "connect-back").click()
    for planned in ("facebook", "threads"):  # LinkedIn is a real connector now
        assert T(page, f"planned-{planned}").is_visible()
    T(page, "connect-fake").click()
    T(page, "connect-oauth").click()
    # the network's own consent page ("Continue as …?")
    T(page, "consent-continue").click()
    # post-connect onboarding (like Buffer): confirm account -> goal -> recommended times
    T(page, "onboarding-step-confirm").wait_for()
    T(page, "onboarding-finish-connection").click()
    T(page, "onboarding-next").click()
    T(page, "onboarding-got-it").click()
    T(page, "onboarding").wait_for(state="detached")
    assert "1,234" in T(page, "channel-profile-stats").inner_text()


def test_03_connect_second_channel_with_token_and_paste_url(page, server):
    goto(page, server, "/channels?network=fake&advanced=1")
    T(page, "connect-token-input").fill("tok-second")
    T(page, "connect-token-submit").click()
    T(page, "onboarding").wait_for()
    T(page, "onboarding-close").click()
    start = api(page, "POST", "/api/connect/fake")
    goto(page, server, "/channels?network=fake&advanced=1")
    state = re.search(r"state=([^&]+)", start["url"]).group(1)
    T(page, "connect-paste-input").fill(f"https://x/oauth/fake/callback?code=fakecode&state={state}")
    T(page, "connect-paste-submit").click()
    T(page, "page-channeldetail").wait_for()
    goto(page, server, "/channels")
    page.wait_for_function("document.querySelectorAll('[data-testid=connected-channel]').length >= 2")


def test_04_composer_every_control(page, server, tmp_path):
    goto(page, server, "/home")
    T(page, "new-post").click()
    T(page, "composer").wait_for()
    T(page, "composer-all-channels").click()
    T(page, "composer-all-channels").click()  # clear
    T(page, "composer-channel").first.click()
    T(page, "composer-caption").fill("GPT-6 preview: what changed #AI #OpenAI")
    T(page, "composer-emoji").click(); T(page, "emoji").first.click()
    T(page, "composer-hashtags").click(); T(page, "hashtag-group").first.click()
    assert "#TechNews" in T(page, "composer-caption").input_value()
    T(page, "composer-templates").click(); T(page, "template").first.click()
    assert "just launched" in T(page, "composer-caption").input_value()
    T(page, "composer-caption").fill("Launch day! #AI")
    imgs = [make_image(tmp_path / f"c{i}.png", color=(100 + i * 40, 90, 220)) for i in range(2)]
    T(page, "composer-file-input").set_input_files([str(p) for p in imgs])
    page.wait_for_function("document.querySelectorAll('[data-testid=composer-media-item]').length === 2")
    T(page, "composer-media-item").first.hover()
    T(page, "composer-media-remove").first.click()
    page.wait_for_function("document.querySelectorAll('[data-testid=composer-media-item]').length === 1")
    assert T(page, "composer-upload").is_visible() and T(page, "composer-upload-folder").is_visible() and T(page, "composer-upload-zip").is_visible()
    T(page, "composer-formats").wait_for()
    T(page, "format-fake-carousel").click()
    assert T(page, "format-fake-carousel").get_attribute("aria-checked") == "true"
    T(page, "composer-suggest-format").click()
    page.wait_for_function("document.querySelector('[data-testid=format-fake-post]').getAttribute('aria-checked') === 'true'")
    assert "single post" in T(page, "format-reason-fake").inner_text().lower()
    T(page, "composer-topic-auto").click()
    assert T(page, "composer-topic").input_value().startswith("20")
    T(page, "composer-topic").fill("e2e-launch-day")
    T(page, "composer-tag").first.click()
    T(page, "composer-cta-toggle").click()
    T(page, "composer-customize").click()
    T(page, "customize-fake").wait_for()
    T(page, "customize-caption-fake").fill("Custom caption for the demo network")
    T(page, "opt-fake-first_comment").fill("First!")
    T(page, "opt-fake-posting_method").select_option("auto")
    assert T(page, "preview-fake").is_visible()
    for w in ("prioritize", "now", "custom", "queue"):
        T(page, f"when-{w}").click()
    T(page, "when-custom").click()
    T(page, "composer-datetime").fill("2031-05-05T09:30")
    T(page, "composer-major-news").check()
    T(page, "composer-create-another").check()
    T(page, "composer-submit").click()
    toast(page, "Scheduled")
    assert T(page, "composer-caption").input_value() == ""  # create another keeps it open (channels stay selected)
    T(page, "composer-caption").fill("A draft for later")
    T(page, "composer-save-draft").click()
    toast(page, "drafts")
    T(page, "composer-create-another").uncheck()
    T(page, "composer-caption").fill("x" * 600)
    T(page, "composer-issues").wait_for()
    assert T(page, "composer-submit").is_disabled()
    T(page, "composer-close").click()
    # keyboard: C opens, Escape closes, ⌘S saves draft
    page.keyboard.press("c")
    T(page, "composer").wait_for()
    T(page, "composer-caption").fill("Saved with shortcut")
    page.keyboard.press("Control+s")
    toast(page, "drafts")


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
