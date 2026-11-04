"""End-to-end: Growth. Build a link-in-bio page, visit the public page, click a link, see the click counted.
Then smoke every other Growth tab (short links + UTM, campaigns, hashtags, automations, CSV import).

Run:  CHROMIUM_PATH=/usr/bin/chromium pytest tests/e2e/test_ui_growth.py -q
"""
import re
from urllib.parse import urlsplit

from .conftest import PASSWORD, T, api, goto, poll, toast


def test_00_login_and_channel(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "huhbyte_demo"})
    page.reload()
    T(page, "page-home").wait_for()


def test_01_bio_page_click_counted(page, server):
    port = urlsplit(server["base"]).port
    target = f"http://127.0.0.1:{port}/api/health"           # public, so the redirect lands on a real page
    goto(page, server, "/growth/bio")
    T(page, "bio-empty").wait_for()
    T(page, "bio-create-slug").fill("huhbyte")
    T(page, "bio-create-submit").click()
    toast(page, "Page created")
    T(page, "bio-public-url").wait_for()
    assert T(page, "bio-public-url").inner_text().endswith("/bio/huhbyte")
    # three links (new ones go on top); content autosaves
    for label, url in [("TikTok", "https://tiktok.com/@huhbyte"), ("YouTube", "https://youtube.com/@huhbyte"), ("Read the news", target)]:
        T(page, "bio-add-open").click()
        T(page, "bio-add-link").click()
        T(page, "bio-block-label").first.fill(label)
        T(page, "bio-block-url").first.fill(url)
    T(page, "bio-title").fill("HuhByte AI")
    page.frame_locator("[data-testid=bio-preview]").get_by_text("Read the news").wait_for()
    # wait for the debounced autosave to reach the server (poll, since wait_for_function doesn't await async predicates)
    poll(page, """async () => { const r = await fetch('/api/bio'); const p = (await r.json())[0];
        return p.title === 'HuhByte AI' && p.blocks.filter(b => b.type === 'link' && b.url).length === 3 }""")

    # the public page, as a visitor sees it (no login cookie needed for /bio)
    pub = page.context.new_page()
    pub.goto(server["base"] + "/bio/huhbyte")
    assert pub.title() == "HuhByte AI"
    assert pub.locator("a.btn").count() == 3
    with pub.expect_navigation():
        pub.get_by_role("link", name="Read the news").click()
    assert pub.url.rstrip("/").endswith("/api/health") and '"ok"' in pub.content().replace(" ", "")
    pub.close()

    T(page, "bio-tab-analytics").click()
    T(page, "bio-refresh-stats").click()
    page.wait_for_function("document.querySelector('[data-testid=bio-stat-clicks]')?.innerText.includes('1')")
    assert re.search(r"\b1\b", T(page, "bio-stat-views").inner_text())
    T(page, "bio-tab-links").click()
    T(page, "bio-block-clicks").first.filter(has_text="1 clicks").wait_for()
    page.reload()
    T(page, "bio-tab-links").click()
    T(page, "bio-block-clicks").first.filter(has_text="1 clicks").wait_for()


def test_02_short_link_with_utm(page, server):
    goto(page, server, "/growth/links")
    T(page, "links-empty").or_(T(page, "link-row").first).first.wait_for()
    T(page, "link-url").fill("https://huhbyte.ai/gpt6")
    T(page, "link-code").fill("gpt6")
    T(page, "link-utm-toggle").click()
    T(page, "utm-source").fill("newsletter")
    assert "utm_source=newsletter" in T(page, "utm-preview").inner_text()
    T(page, "link-create").click()
    toast(page, "copied")
    T(page, "links-source-manual").click()
    T(page, "link-row").first.click()
    T(page, "link-detail").wait_for()
    assert "utm_source=newsletter" in T(page, "link-detail").inner_text()
    T(page, "utm-auto").click()
    toast(page, "UTM defaults saved")
    assert api(page, "GET", "/api/utm/defaults")["*"]["auto"] is True


def test_03_campaign_hashtags_automation_import(page, server):
    goto(page, server, "/growth/campaigns")
    T(page, "campaign-new").click()
    T(page, "campaign-name").fill("DevDay week")
    T(page, "campaign-save").click()
    toast(page, "Campaign saved")
    T(page, "campaign-card").first.click()
    T(page, "campaign-detail").wait_for()

    goto(page, server, "/growth/hashtags")
    T(page, "hashtag-new").click()
    T(page, "hashtag-name").fill("LLMs")
    T(page, "hashtag-text").fill("AI, LLM OpenAI")
    T(page, "hashtag-save").click()
    toast(page, "Group saved")
    T(page, "hashtag-group").filter(has_text="#OpenAI").wait_for()

    goto(page, server, "/growth/automations")
    T(page, "automation-new-first_comment").click()
    T(page, "automation-fc-text").fill("Sources in bio")
    T(page, "automation-save").click()
    toast(page, "Automation saved")
    T(page, "automation-row").filter(has_text="Sources in bio").wait_for()
    T(page, "automation-toggle").first.click()
    toast(page, "Paused")

    goto(page, server, "/growth/import")
    T(page, "import-csv").fill("caption,when\nFrom the spreadsheet,draft\n,draft\n")
    T(page, "import-channels-option").first.click()
    T(page, "import-preview").click()
    T(page, "import-result").wait_for()
    assert T(page, "import-row").count() == 2
    T(page, "import-run").click()
    T(page, "import-done").wait_for()
    assert "1 imported" in T(page, "import-done").inner_text()
    assert not page.errors, page.errors
