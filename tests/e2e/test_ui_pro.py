"""Browser tests for the Pro features: post-connect onboarding, ⌘K command bar, media library, contacts."""
from datetime import datetime, timedelta

from huhqueue.models import Comment

from .conftest import PASSWORD, T, api, goto, make_image, toast


def test_00_login_and_seed(page, server):
    goto(page, server, "/home")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    a = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-pro", "username": "huhbyte_demo"})
    server["ch"] = a


def test_01_onboarding_goal_and_recommended_times(page, server):
    ch = server["ch"]
    goto(page, server, f"/channel/{ch['id']}?connected=1")
    T(page, "onboarding-step-confirm").wait_for()
    assert "huhbyte_demo" in T(page, "onboarding-account-card").inner_text()
    T(page, "onboarding-finish-connection").click()
    T(page, "onboarding-step-goal").wait_for()
    assert T(page, "onboarding-goal-3").get_attribute("aria-checked") == "true"   # default, like Buffer
    T(page, "onboarding-help").click()
    T(page, "onboarding-help-text").wait_for()
    T(page, "onboarding-goal-5").click()
    T(page, "onboarding-next").click()
    T(page, "onboarding-step-times").wait_for()
    assert T(page, "onboarding-time").count() == 5
    assert "Buffer" in T(page, "onboarding-basis").inner_text()
    T(page, "onboarding-change-goal").click()
    T(page, "onboarding-goal-custom").click()
    T(page, "onboarding-goal-custom-input").fill("4")
    T(page, "onboarding-next").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=onboarding-time]').length === 4")
    T(page, "onboarding-time-toggle").first.uncheck()          # block one day
    T(page, "onboarding-got-it").click()
    toast(page, "Posting schedule saved: 3 times a week")
    T(page, "onboarding").wait_for(state="detached")
    got = api(page, "GET", f"/api/channels/{ch['id']}")
    assert len(got["slots"]) == 3
    ob = api(page, "GET", f"/api/channels/{ch['id']}/onboarding")
    assert ob["done"] and ob["goal"] == 4


def test_02_onboarding_reopen_and_close(page, server):
    ch = server["ch"]
    goto(page, server, f"/channel/{ch['id']}?onboard={ch['id']}")
    T(page, "onboarding-step-confirm").wait_for()
    T(page, "onboarding-close").click()
    T(page, "onboarding").wait_for(state="detached")


def test_03_command_bar_rules_and_confirm(page, server):
    ch = server["ch"]
    for i in range(2):
        api(page, "POST", "/api/posts", {"caption": f"draft {i}", "channel_ids": [ch["id"]], "when": "draft"})
    goto(page, server, "/home")
    T(page, "page-home").wait_for()
    page.keyboard.press("Control+k")
    T(page, "command-input").fill("show my drafts")
    T(page, "command-ask").click()
    T(page, "command-result").wait_for()
    assert "draft" in T(page, "command-result").inner_text().lower()
    T(page, "command-input").fill("delete all drafts")
    T(page, "command-ask").click()
    T(page, "command-confirm-panel").wait_for()
    assert "2 item" in T(page, "command-preview").inner_text()
    assert len(api(page, "GET", "/api/posts?status=draft")) == 2      # nothing happened before Confirm
    T(page, "command-confirm").click()
    T(page, "command-result").wait_for()
    assert api(page, "GET", "/api/posts?status=draft") == []
    page.keyboard.press("Escape")


def test_04_command_bar_registry_action_form(page, server):
    goto(page, server, "/home")
    T(page, "page-home").wait_for()
    T(page, "open-command-palette").click()
    T(page, "command-input").fill("idea.create")
    page.wait_for_function("[...document.querySelectorAll('[data-testid=command-item]')].some(e => e.innerText.includes('Create an idea'))")
    T(page, "command-item").filter(has_text="Create an idea").first.click()
    T(page, "command-form").wait_for()
    T(page, "command-field-title").fill("Idea from the command bar")
    T(page, "command-form-run").click()
    T(page, "command-result").wait_for()
    assert any(i["title"] == "Idea from the command bar" for i in api(page, "GET", "/api/ideas")["ideas"])
    page.keyboard.press("Escape")


def test_05_library_folders_tags_search_usage(page, server):
    d = server["dir"]
    make_image(d / "orgo-launch.png", color=(10, 200, 30))
    goto(page, server, "/library")
    T(page, "page-library").wait_for()
    page.prompt_answer = None
    T(page, "library-file-input").set_input_files(str(d / "orgo-launch.png"))
    toast(page, "1 file")
    T(page, "library-item").first.click()
    T(page, "library-drawer").wait_for()
    T(page, "library-tags-input").fill("launch, orgo")
    T(page, "library-tags-input").press("Enter")
    toast(page, "Saved")
    T(page, "library-folder-input").fill("launches/2026")
    T(page, "library-title").click()   # blur folder -> save
    toast(page, "Saved")
    T(page, "library-drawer-close").click()
    T(page, "library-folder").filter(has_text="2026").first.click()
    page.wait_for_function("document.querySelectorAll('[data-testid=library-item]').length === 1")
    T(page, "library-search").fill("orgo")
    page.wait_for_function("document.querySelectorAll('[data-testid=library-item]').length === 1")
    T(page, "library-search").fill("nothing-matches")
    T(page, "library-empty").wait_for()
    T(page, "library-search").fill("")
    T(page, "library-smart-unused").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=library-item]').length >= 1")


def test_06_contacts_profile_note_reply(page, server):
    ch = server["ch"]
    with server["core"].db.session() as s:
        for i, txt in enumerate(["love this?", "more please", "great video"]):
            s.add(Comment(channel_id=ch["id"], external_id=f"ct{i}", author="superfan_1", text=txt, status="open",
                          media_caption="Launch recap", at=datetime.utcnow() - timedelta(hours=i)))
    goto(page, server, "/contacts")
    T(page, "page-contacts").wait_for()
    T(page, "contacts-segment-superfans").click()
    T(page, "contact-row").filter(has_text="superfan_1").first.click()
    T(page, "contact-profile").wait_for()
    assert T(page, "contact-timeline-item").count() == 3
    T(page, "contact-notes").fill("Asked about pricing")
    T(page, "contact-tags").click()
    toast(page, "Saved")
    T(page, "contact-reply-input").first.fill("Thanks! 🙌")
    T(page, "contact-reply-send").first.click()
    toast(page, "Reply sent")
    c = api(page, "GET", "/api/contacts/superfan_1")
    assert c["notes"] == "Asked about pricing" and c["open"] == 2


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
