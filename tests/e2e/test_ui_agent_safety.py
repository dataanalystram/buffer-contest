"""End-to-end: agent safety UI - kill switch card + app-wide banner, token limits (create + edit), audit log filters + CSV."""
import httpx

from .conftest import PASSWORD, T, api, ask_ok, goto, toast


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "safety_one"})
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-bbbb", "username": "safety_two"})
    page.reload()
    T(page, "page-home").wait_for()


def test_01_token_with_limits(page, server):
    one = api(page, "GET", "/api/channels")[0]
    goto(page, server, "/settings?section=api")
    T(page, "settings-api").wait_for()
    T(page, "token-name").fill("capped-agent")
    T(page, "token-limits-section").locator("summary").click()
    T(page, "token-max-public").fill("2")
    T(page, "token-expires").fill("30")
    T(page, f"token-channel-{one['id']}").click()
    T(page, "token-create").click(); T(page, "token-minted").wait_for()
    tok = next(t for t in api(page, "GET", "/api/tokens")["tokens"] if t["name"] == "capped-agent")
    assert tok["max_public_per_day"] == 2 and tok["allowed_channel_ids"] == [one["id"]] and tok["expires_at"]
    row = T(page, "token-row").filter(has_text="capped-agent")
    assert "0/2 public today" in row.inner_text()
    row.get_by_test_id("token-edit-limits").click()
    dlg = T(page, "token-limits-dialog")
    dlg.wait_for()
    assert dlg.get_by_test_id("token-max-public").input_value() == "2"
    dlg.get_by_test_id("token-max-actions").fill("5")
    dlg.get_by_test_id("token-auto-confirm").click()
    T(page, "token-limits-save").click(); toast(page, "Limits saved")
    tok = next(t for t in api(page, "GET", "/api/tokens")["tokens"] if t["name"] == "capped-agent")
    assert tok["max_actions_per_day"] == 5 and tok["auto_confirm"] is True and tok["max_public_per_day"] == 2


def test_02_kill_switch_banner_blocks_tokens(page, server):
    raw = api(page, "POST", "/api/tokens", {"name": "agent", "scopes": ["read", "write"], "require_approval": True})["token"]
    goto(page, server, "/settings?section=api")
    T(page, "kill-switch-pause").click(); ask_ok(page, "testing the brakes"); toast(page, "Agents paused")
    T(page, "agents-paused-banner").wait_for()
    assert "testing the brakes" in T(page, "agents-paused-banner").inner_text()
    r = httpx.Client(trust_env=False).get(server["base"] + "/api/channels", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 423
    assert httpx.Client(trust_env=False).get(server["base"] + "/api/health").json()["agents_paused"] is True
    goto(page, server, "/queue")
    T(page, "agents-paused-banner").wait_for()                 # app-wide, on every page
    T(page, "agents-banner-resume").click(); toast(page, "Agents resumed")
    T(page, "agents-paused-banner").wait_for(state="hidden")
    r = httpx.Client(trust_env=False).get(server["base"] + "/api/channels", headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200


def test_03_audit_log_filters_and_export(page, server):
    goto(page, server, "/settings?section=api")
    T(page, "audit-log").wait_for()
    page.wait_for_function("document.querySelectorAll('[data-testid=audit-row]').length > 0")
    T(page, "audit-filter-q").fill("agents/pause")
    page.wait_for_function("[...document.querySelectorAll('[data-testid=audit-row]')].every(r => r.innerText.includes('/api/agents/pause'))")
    with page.expect_download() as dl:
        T(page, "audit-export").click()
    assert dl.value.suggested_filename == "huhqueue-audit.csv"


def test_99_no_javascript_errors(page):
    assert page.errors == [], page.errors
