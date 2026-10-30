"""End-to-end: Link in bio (block editor). Add by pasting a link and from the Add dialog tiles, autosave, the live
preview updating without saving, hide toggle, drag and keyboard reorder, design draft (undo / cancel / save), QR from the
Share popover, a visitor subscribes and the owner sees it, export / duplicate.

Run:  CHROMIUM_PATH=/usr/bin/chromium pytest tests/e2e/test_ui_bio_v2.py -q
"""
import time

from .conftest import PASSWORD, T, api, ask_ok, goto, toast


def saved(page, pred, timeout=8):
    """Poll the API until pred(page_json) holds (autosave is debounced)."""
    end = time.time() + timeout
    while time.time() < end:
        p = api(page, "GET", "/api/bio")[0]
        if pred(p):
            return p
        time.sleep(0.25)
    raise AssertionError(f"autosave never matched; last: {[(b['type'], b.get('label')) for b in p['blocks']]}")


def test_00_login(page, server):
    # the sandbox has no internet: answer the YouTube / Spotify embeds locally so their frames load at once
    for host in ("youtube-nocookie.com", "open.spotify.com"):
        page.context.route(f"**://*{host}/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<!doctype html><title>embed</title>"))
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()


def test_01_add_by_paste_and_tiles_autosaves(page, server):
    goto(page, server, "/growth/bio")
    T(page, "bio-empty").wait_for()
    T(page, "bio-create-slug").fill("ram")
    T(page, "bio-create-submit").click()
    toast(page, "Page created")
    T(page, "bio-profile").wait_for()

    # paste a link: it becomes the matching brand button
    T(page, "bio-add-open").click()
    T(page, "bio-add-search").fill("open.spotify.com/show/huhbyte")
    T(page, "bio-add-paste-embed").wait_for()                     # Spotify links can also be embedded
    T(page, "bio-add-paste").click()
    T(page, "bio-add-dialog").wait_for(state="hidden")
    # tiles: embed, email signup, contact card
    for kind in ("embed", "email", "contact"):
        T(page, "bio-add-open").click()
        T(page, f"bio-add-{kind}").click()
        if kind == "embed":                                       # a new block opens expanded
            T(page, "bio-embed-url").fill("https://youtu.be/dQw4w9WgXcQ")
    # search a brand by name
    T(page, "bio-add-open").click()
    T(page, "bio-add-search").fill("githu")
    T(page, "bio-brand-github").click()
    T(page, "bio-block-setup").first.wait_for()                   # no URL yet: "Setup" chip, not on the public page
    T(page, "bio-block-url").first.fill("https://github.com/huhbyte")

    p = saved(page, lambda p: [b["type"] for b in p["blocks"]][:5] == ["brand", "contact", "email", "embed", "brand"]
              and p["blocks"][0].get("url") == "https://github.com/huhbyte" and p["blocks"][3].get("embed_url"))
    assert p["blocks"][4]["brand"] == "spotify" and p["blocks"][4]["url"] == "https://open.spotify.com/show/huhbyte"
    T(page, "bio-autosave").filter(has_text="Saved").wait_for()
    frame = page.frame_locator("[data-testid=bio-preview]")
    frame.get_by_text("Listen on Spotify").wait_for()
    frame.locator("iframe[src*='youtube-nocookie.com/embed/dQw4w9WgXcQ']").wait_for()


def test_02_preview_updates_before_save_and_highlights(page, server):
    T(page, "bio-title").fill("Ram Live")
    page.frame_locator("[data-testid=bio-preview]").locator("h1", has_text="Ram Live").wait_for()
    T(page, "bio-block-label").first.click()                      # focusing a link dims the others in the preview
    page.wait_for_function("document.querySelector('[data-testid=bio-preview]').contentDocument.documentElement.innerHTML.includes('[data-id]:not')")
    saved(page, lambda p: p["title"] == "Ram Live")


def test_03_hide_toggle_and_reorder(page, server):
    T(page, "bio-block-hide").first.click()
    saved(page, lambda p: p["blocks"][0]["hidden"] is True)
    pub = page.context.new_page()
    pub.goto(server["base"] + "/bio/ram")
    assert pub.locator("a.btn.brand.button-spotify").count() == 1 and pub.locator("a.btn.brand.button-github").count() == 0  # hidden
    pub.close()
    T(page, "bio-block-hide").first.click()

    # keyboard: arrow down on the grip
    T(page, "bio-block-grip").first.focus()
    page.keyboard.press("ArrowDown")
    saved(page, lambda p: [b["type"] for b in p["blocks"]][:2] == ["contact", "brand"] and not p["blocks"][1]["hidden"])

    # drag the third card (email) above the first with the grip handle
    T(page, "bio-block-more").first.click()                       # collapse the GitHub card opened when it was added
    page.wait_for_timeout(400)
    T(page, "bio-add-open").scroll_into_view_if_needed()
    grip = T(page, "bio-block-grip").nth(2).bounding_box()
    top = T(page, "bio-block").first.bounding_box()
    page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2)
    page.mouse.down()
    for i in range(1, 31):
        page.mouse.move(grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2 - (grip["y"] - top["y"] + 40) * i / 30)
        time.sleep(0.03)
    page.mouse.up()
    saved(page, lambda p: p["blocks"][0]["type"] == "email")


def test_04_design_draft_cancel_and_save(page, server):
    T(page, "bio-tab-design").click()
    T(page, "bio-theme-ocean").click()
    T(page, "bio-design-bar").wait_for()
    frame = page.frame_locator("[data-testid=bio-preview]")
    frame.locator("main[data-theme=ocean]").wait_for()
    assert api(page, "GET", "/api/bio")[0]["theme"]["preset"] == "lavender"     # a draft, not saved
    T(page, "bio-font-playfair-display").click()
    T(page, "bio-design-undo").click()
    T(page, "bio-design-cancel").click()
    ask_ok(page)
    T(page, "bio-design-bar").wait_for(state="hidden")
    frame.locator("main[data-theme=lavender]").wait_for()

    T(page, "bio-theme-brutal").click()
    T(page, "bio-bg-gradient").click()
    T(page, "bio-gradient-frost").click()
    T(page, "bio-btn-hard-pill").click()
    T(page, "bio-design-save").click()
    toast(page, "Design saved")
    th = api(page, "GET", "/api/bio")[0]["theme"]
    assert (th["preset"], th["bg_type"], th["bg_gradient"], th["style"], th["buttons"]) == ("brutal", "gradient", "frost", "hard", "pill")


def test_05_share_popover_qr_and_publish(page, server):
    page.frame_locator("[data-testid=bio-preview]").locator("main[data-theme=brutal]").wait_for()   # saved design in the preview
    assert T(page, "bio-status").inner_text() == "Live"           # new pages are live
    T(page, "bio-publish").click()
    toast(page, "Page unpublished")
    assert T(page, "bio-status").inner_text() == "Draft" and api(page, "GET", "/api/bio")[0]["published"] is False
    T(page, "bio-publish").click()
    toast(page, "Page published")
    T(page, "bio-share").click()
    assert T(page, "bio-share-url").inner_text().endswith("/bio/ram")
    T(page, "bio-qr-open").click()
    T(page, "bio-qr-image").wait_for()
    assert T(page, "bio-qr-url").inner_text().endswith("/bio/ram")
    with page.expect_download() as dl:
        T(page, "bio-qr-png").click()
    assert dl.value.suggested_filename == "bio-ram-qr.png"
    T(page, "bio-qr-close").click()


def test_06_visitor_subscribes_owner_sees_it(page, server):
    pub = page.context.new_page()
    pub.goto(server["base"] + "/bio/ram")
    assert pub.locator("a.btn.brand.button-spotify").count() == 1 and pub.locator("a.btn.brand.button-github").count() == 1
    assert pub.locator("iframe[src*='youtube-nocookie.com/embed/dQw4w9WgXcQ']").count() == 1
    pub.get_by_label("Email address").fill("fan@example.com")
    pub.get_by_role("button", name="Subscribe").click()
    pub.get_by_test_id("bio-subscribed").wait_for()
    with pub.expect_download() as dl:
        pub.get_by_role("link", name="Save my contact").click()
    assert dl.value.suggested_filename == "Your-name.vcf"
    pub.close()

    T(page, "bio-tab-settings").click()
    T(page, "bio-subscribers-refresh").click()
    T(page, "bio-subscriber-row").first.wait_for()
    assert "fan@example.com" in T(page, "bio-subscriber-row").first.inner_text()
    T(page, "bio-tab-analytics").click()
    T(page, "bio-refresh-stats").click()
    page.wait_for_function("document.querySelector('[data-testid=bio-stat-signups]')?.innerText.includes('1')")


def test_07_settings_export_and_duplicate(page, server):
    T(page, "bio-tab-settings").click()
    T(page, "bio-seo-title").fill("Ram | AI")
    T(page, "bio-save-settings").click()
    toast(page, "Settings saved")
    assert api(page, "GET", "/api/bio")[0]["settings"]["seo_title"] == "Ram | AI"
    with page.expect_download() as dl:
        T(page, "bio-export").click()
    assert dl.value.suggested_filename == "bio-ram.json"
    T(page, "bio-duplicate").click()
    toast(page, "Duplicated as /bio/ram-copy")
    assert len(api(page, "GET", "/api/bio")) == 2
    T(page, "bio-page-switch").click()
    assert T(page, "bio-page-tab").count() == 2
    assert not page.errors, page.errors
