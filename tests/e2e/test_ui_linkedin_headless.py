"""E2E: per-channel Bulk upload (Buffer CSV, Queue/Drafts, over-limit flag), LinkedIn connect screen + guide link,
and the agent-context <script> that describes every page. Demo connector only; nothing leaves the machine."""
import json

from .conftest import PASSWORD, T, api, goto, toast


def test_00_login(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    server["ch"] = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-li", "username": "bulk_demo"})


def _ctx(page):
    page.wait_for_function("() => { const e = document.getElementById('agent-context'); return e && JSON.parse(e.textContent).route }")
    return json.loads(page.locator("#agent-context").text_content())


def test_01_agent_context_follows_navigation(page, server):
    goto(page, server, "/queue?tab=drafts")
    T(page, "page-queue").wait_for()
    page.wait_for_function("() => document.documentElement.dataset.page === 'queue'")
    ctx = _ctx(page)
    assert ctx["route"] == "queue" and ctx["query"] == {"tab": "drafts"}
    assert any(a["id"] == "post.schedule" and a["mcp_tool"] == "post_schedule" for a in ctx["actions"])
    assert ctx["ids"]["channels"][0]["id"] == server["ch"]["id"]
    goto(page, server, f"/channel/{server['ch']['id']}?tab=publish")
    page.wait_for_function("() => document.documentElement.dataset.page === 'channel'")
    assert _ctx(page)["ids"]["channel"]["id"] == server["ch"]["id"]


def test_02_bulk_upload_buffer_csv_to_drafts(page, server, tmp_path):
    goto(page, server, f"/channel/{server['ch']['id']}?tab=publish")
    T(page, "channel-bulk-upload").click()
    T(page, "bulk-upload-dialog").wait_for()
    T(page, "bulk-upload-mode-draft").click()
    rows = "\n".join(f'"bulk post {i}",,news,' for i in range(3))
    f = tmp_path / "buffer.csv"
    f.write_text("Text,Image URL,Tags,Posting Time\n" + rows + "\n")
    T(page, "bulk-upload-file").set_input_files(str(f))
    T(page, "bulk-upload-preview").wait_for()
    assert T(page, "bulk-upload-row").count() == 3
    T(page, "bulk-upload-run").click()
    toast(page, "3 posts added to Drafts")
    drafts = api(page, "GET", f"/api/posts?status=draft&channel_id={server['ch']['id']}")
    items = drafts if isinstance(drafts, list) else drafts.get("items", [])
    assert sum(1 for t in items if (t.get("caption") or "").startswith("bulk post")) == 3


def test_03_bulk_upload_flags_over_limit_rows(page, server, tmp_path):
    goto(page, server, f"/channel/{server['ch']['id']}?tab=publish")
    T(page, "channel-bulk-upload").click()
    f = tmp_path / "big.csv"
    f.write_text("Text\n" + "\n".join(f"row {i}" for i in range(502)) + "\n")
    T(page, "bulk-upload-file").set_input_files(str(f))
    T(page, "bulk-upload-over-limit").wait_for()
    assert "2 over the 500-row limit" in T(page, "bulk-upload-over-limit").inner_text()
    T(page, "bulk-upload-cancel").click()
    T(page, "bulk-upload-dialog").wait_for(state="hidden")


def test_04_linkedin_connect_screen_has_guide(page, server):
    goto(page, server, "/channels?network=linkedin")
    T(page, "connect-explainer").wait_for()
    assert "LinkedIn" in T(page, "connect-explainer").inner_text()
    href = T(page, "connect-guide-link").get_attribute("href")
    assert href.endswith("CONNECT_INTEGRATIONS.md#1-linkedin-personal-profile")


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
