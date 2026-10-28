"""End-to-end UI test - Teams: the owner invites a contributor (link), the contributor joins and drafts a post,
submits it for approval and comments, then the owner approves it from the Approvals tab with a note.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e/test_ui_teams.py -q   (or after `playwright install chromium`)
"""
from .conftest import PASSWORD, T, api, goto, toast

STATE = {}


def test_00_owner_setup_and_invite(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    STATE["channel"] = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-team", "username": "huhbyte_demo"})["id"]
    goto(page, server, "/settings?section=team")
    T(page, "settings-team").wait_for()
    T(page, "team-invite").click()
    T(page, "invite-name").fill("Cory Contributor")
    T(page, "invite-email").fill("cory@example.com")
    T(page, "invite-role-contributor").check()
    T(page, "invite-submit").click()
    link = T(page, "invite-link").input_value()
    assert "#/invite/inv_" in link
    STATE["link"] = link
    T(page, "invite-done").click()
    page.locator('[data-testid="team-member"][data-email="cory@example.com"]').get_by_test_id("member-status").wait_for()
    T(page, "team-matrix").wait_for()


def test_10_contributor_joins_drafts_and_submits(page, server):
    page.context.clear_cookies()
    page.goto(STATE["link"])
    T(page, "invite-accept").wait_for()
    assert "Contributor" in T(page, "invite-info").inner_text()
    T(page, "invite-accept-password").fill("cory-password-1")
    T(page, "invite-accept-password2").fill("cory-password-1")
    T(page, "invite-accept-submit").click()
    T(page, "user-role").filter(has_text="Contributor").wait_for()
    # contributors see "Submit for approval", not Schedule
    goto(page, server, f"/queue?compose=new&channels={STATE['channel']}")
    T(page, "composer").wait_for()
    T(page, "composer-caption").fill("Team launch post from Cory")
    T(page, "composer-save-draft").click()
    toast(page, "Saved to drafts")
    goto(page, server, "/queue?tab=drafts")
    card = page.get_by_test_id("post-card").filter(has_text="Team launch post from Cory")
    assert card.get_by_test_id("post-publish-now").count() == 0 and card.get_by_test_id("post-queue").count() == 0
    card.get_by_test_id("post-comments-toggle").click()
    card.get_by_test_id("comment-input").fill("@owner the thumbnail is final")
    card.get_by_test_id("comment-send").click()
    card.get_by_test_id("comment").filter(has_text="thumbnail is final").wait_for()
    card.get_by_test_id("post-request-approval").click()
    toast(page, "Sent for approval")
    goto(page, server, "/queue?tab=approvals")
    pend = page.get_by_test_id("post-card").filter(has_text="Team launch post from Cory")
    pend.get_by_test_id("post-approval-request").filter(has_text="waiting for an approver").wait_for()
    assert pend.get_by_test_id("post-approve").count() == 0


def test_20_owner_approves_with_note(page, server):
    page.context.clear_cookies()
    page.goto(server["base"] + "/#/home")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    # the bell shows the approval request and the @mention
    T(page, "notifications-button").click()
    T(page, "notifications-panel").get_by_text("Cory Contributor asked for approval").first.wait_for()
    T(page, "notifications-panel").get_by_text("mentioned you").first.wait_for()
    goto(page, server, "/queue?tab=approvals")
    card = page.get_by_test_id("post-card").filter(has_text="Team launch post from Cory")
    card.get_by_test_id("post-approve").click()
    T(page, "decision-note").fill("Looks great - ship it")
    T(page, "approve-confirm").click()
    toast(page, "Approved")
    posts = api(page, "GET", "/api/posts?status=scheduled")
    hit = [p for p in posts if p["caption"] == "Team launch post from Cory"]
    assert hit and hit[0]["approval"]["decided_by"] == "Owner" and hit[0]["approval"]["decision_note"] == "Looks great - ship it"
    goto(page, server, "/queue")
    page.get_by_test_id("post-approved-by").filter(has_text="Looks great").wait_for()
    assert not page.errors, page.errors


def test_30_reject_needs_note_and_contributor_sees_it(page, server):
    pid = api(page, "POST", "/api/posts", {"caption": "Second try", "channel_ids": [STATE["channel"]], "when": "draft"})["targets"][0]["id"]
    # make it a teammate's request by submitting as Cory through the API
    page.context.clear_cookies()
    page.goto(server["base"] + "/#/home")
    T(page, "login-email").fill("cory@example.com")
    T(page, "login-password").fill("cory-password-1")
    T(page, "login-submit").click()
    T(page, "user-role").wait_for()
    api(page, "POST", f"/api/targets/{pid}/request-approval", {"when": "queue"})
    page.context.clear_cookies()
    page.goto(server["base"] + "/#/queue?tab=approvals")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-submit").click()
    card = page.get_by_test_id("post-card").filter(has_text="Second try")
    card.get_by_test_id("post-reject").click()
    assert T(page, "reject-confirm").is_disabled()
    T(page, "decision-note").fill("Shorter caption please")
    T(page, "reject-confirm").click()
    toast(page, "Sent back")
    goto(page, server, "/queue?tab=drafts")
    page.get_by_test_id("post-changes-requested").filter(has_text="Shorter caption please").wait_for()
