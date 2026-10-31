"""Activity log: categories, attribution (agent token, OAuth client, app, model), cursor pagination, filters, CSV,
MCP get_activity, retention, and the rule that no secret or token value is ever logged."""
import base64
import csv
import hashlib
import io
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import activity, llm

from .conftest import agent_token, connect, mcp_call


def rows(app, uid=None):
    st = app.state.store
    return [dict(r) for r in st.q("SELECT * FROM audit" + (" WHERE user_id=?" if uid else "") + " ORDER BY id", (uid,) if uid else ())]


def uid_of(human):
    return human.get("/api/me").json()["id"]


# ------------------------------------------------------------------ categories and attribution
def test_human_sign_in_is_a_signin_row_with_ip_and_user_agent(client):
    connect(client)
    me = client.get("/api/me").json()["id"]
    r = [x for x in rows(client.app, me) if x["action"] == "connect_buffer"][0]
    assert r["category"] == "signin" and r["ua"] == "testclient" and r["ip"]
    page = client.get("/api/activity", params={"tab": "signin"}).json()
    assert [i["action"] for i in page["items"]] == ["connect_buffer"] and page["items"][0]["ip"]


def test_logout_and_failed_sign_ins_are_logged_separately(human, app):
    me = uid_of(human)
    tok = agent_token(human)
    tid = [t for t in human.get("/api/safety").json()["tokens"]][0]["id"]
    human.delete(f"/api/agent-tokens/{tid}")
    anon = TestClient(app, base_url="https://hq.test", headers={"User-Agent": "curl/8.5"})
    assert anon.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
    assert anon.get("/api/me", headers={"Authorization": "Bearer hq_totally_unknown_token_value_123456"}).status_code == 401
    assert anon.get("/device/not-a-real-link", follow_redirects=False).status_code == 302
    failed = [x for x in rows(app, me) if x["action"] == "signin_failed"]
    assert len(failed) == 1 and failed[0]["category"] == "signin" and failed[0]["ok"] == 0
    assert failed[0]["ua"] == "curl/8.5" and json.loads(failed[0]["detail"])["reason"] == "revoked or expired"
    assert any(x["action"] == "signin_failed" and x["user_id"] == "" for x in rows(app))  # unknown token: logged, owner-less
    human.post("/api/logout")
    assert [x for x in rows(app, me) if x["action"] == "logout"][0]["category"] == "signin"


def test_every_agent_write_is_attributable(human, app):
    me = uid_of(human)
    tok = agent_token(human)
    agent = TestClient(app, base_url="https://hq.test")
    r = agent.post("/api/ideas", headers={"Authorization": f"Bearer {tok}", "X-Agent-Model": "gpt-5-codex"},
                   json={"title": "t", "text": "an idea"})
    assert r.status_code == 200, r.text
    row = [x for x in rows(app, me) if x["action"] == "create_idea"][0]
    assert row["category"] == "agent" and row["token_id"] and row["token_label"] == "Claude"
    assert row["surface"] == "rest" and row["model"] == "gpt-5-codex" and row["app"] == "claude"
    # every agent row in the log carries the token that did it
    assert all(x["token_id"] for x in rows(app, me) if x["category"] == "agent" and x["actor"].startswith("agent:"))


def test_mcp_client_info_and_model_identify_the_app(human, app):
    me = uid_of(human)
    tok = human.post("/api/agent-tokens", json={"name": "laptop", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    human.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                        "params": {"clientInfo": {"name": "claude-code", "version": "2.1.0"}}})
    human.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                        "params": {"name": "create_idea", "arguments": {"text": "x"}, "_meta": {"model": "claude-opus-4-1"}}})
    row = [x for x in rows(app, me) if x["action"] == "create_idea"][0]
    assert row["app"] == "claude-code" and row["surface"] == "mcp" and row["model"] == "claude-opus-4-1"
    groups = human.get("/api/activity", params={"tab": "agent"}).json()["agents"]
    g = [g for g in groups if g["app"] == "claude-code"][0]
    assert g["label"] == "Claude Code" and g["agents"][0]["label"] == "laptop"


@pytest.mark.parametrize("hints,app", [(("Claude via claude.ai",), "claude"), (("ChatGPT via chatgpt.com",), "chatgpt"),
                                       (("grok",), "grok"), (("cursor-vscode 1.0",), "cursor"), (("codex-mcp-client",), "codex"),
                                       (("Claude Code",), "claude-code"), (("some-new-client",), "mcp")])
def test_app_classification(hints, app):
    assert activity.classify_app(*hints) == app


def test_mcp_oauth_token_records_client_id_and_app(client, app):
    cb = "https://chatgpt.com/connector_platform_oauth_redirect"
    reg = client.post("/oauth/register", json={"client_name": "ChatGPT", "redirect_uris": [cb]}).json()
    verifier = "w" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"client_id": reg["client_id"], "redirect_uri": cb, "state": "s1", "code_challenge": challenge, "code_challenge_method": "S256"}
    connect(client)
    csrf = client.get("/api/session").json()["csrf"]
    r = client.post("/oauth/authorize", data={**q, "csrf": csrf, "policy": "review", "daily_cap": "5", "decision": "allow"},
                    follow_redirects=False)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                            "redirect_uri": cb, "code_verifier": verifier}).json()["access_token"]
    me = client.get("/api/me").json()["id"]
    TestClient(app, base_url="https://hq.test").post("/api/ideas", headers={"Authorization": f"Bearer {tok}"}, json={"text": "via chatgpt"})
    row = [x for x in rows(app, me) if x["action"] == "create_idea"][0]
    assert row["client_id"] == reg["client_id"] and row["app"] == "chatgpt" and "ChatGPT" in row["token_label"]
    assert any(x["action"] == "oauth_token_issued" and x["category"] == "signin" for x in rows(app, me))


def test_ai_calls_record_provider_model_tokens_and_cost(human, app, monkeypatch):
    me = uid_of(human)
    from studio import actions
    h = app.state.house

    def fake_chat(engine, system, user, **kw):
        llm._usage(engine, None, 1200, 300)
        return '{"rules": []}'
    monkeypatch.setattr(llm, "chat", fake_chat)
    eng = {"source": "your key", "provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-x" * 5,
           "model": "gpt-4.1-mini", "style": "openai"}
    llm.chat_json(eng, "s", "u")
    h._ai_audit(actions.Actor(me), eng, "rules")
    row = [x for x in rows(app, me) if x["action"] == "ai_call"][0]
    assert row["category"] == "agent" and row["app"] == "huhqueue" and row["model"] == "gpt-4.1-mini"
    assert row["provider"] == "OpenAI (your key)" and row["tokens_in"] == 1200 and row["tokens_out"] == 300
    assert row["cost_usd"] == pytest.approx((1200 * 0.40 + 300 * 1.60) / 1e6)
    free = dict(eng, source="free starter", provider="free", model="openai")
    llm.chat_json(free, "s", "u")
    h._ai_audit(actions.Actor(me), free, "caption")
    assert [x for x in rows(app, me) if x["action"] == "ai_call"][-1]["cost_usd"] == 0.0


def test_buffer_api_calls_are_counted_in_the_system_tab(human, app):
    human.get("/api/channels", params={"refresh": True})
    page = human.get("/api/activity", params={"tab": "system"}).json()
    assert page["items"] and all(i["category"] == "system" for i in page["items"])
    assert page["items"][0]["action"] == "buffer_api" and page["items"][0]["detail"]["op"].startswith("query")
    b = page["buffer"]
    assert b["you_15min"] >= 1 and b["limit_15min"] == 100 and b["limit_24h"] == 500
    assert human.get("/api/audit").json() and all(r["action"] != "buffer_api" for r in human.get("/api/audit").json())


# ------------------------------------------------------------------ pagination, filters, CSV
def _seed(app, uid, n, **over):
    st = app.state.store
    now = time.time()
    for i in range(n):
        st.x("INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts,category) VALUES(?,?,?,?,?,?,?,?)",
             (uid, over.get("actor", "you"), over.get("action", f"act_{i}"), "read", json.dumps({"i": i}), 1,
              over.get("ts", now - (n - i)), over.get("category", "human")))


def test_cursor_pagination_25_50_100(human, app):
    me = uid_of(human)
    _seed(app, me, 120)
    seen, cur, pages = [], "", 0
    while True:
        p = human.get("/api/activity", params={"limit": 50, "cursor": cur}).json()
        ids = [i["id"] for i in p["items"]]
        assert ids == sorted(ids, reverse=True) and not set(ids) & set(seen)
        seen += ids
        pages += 1
        if not p["next_cursor"]:
            break
        cur = p["next_cursor"]
    assert len(seen) == p["counts"]["all"] and pages == -(-len(seen) // 50)
    assert len(human.get("/api/activity", params={"limit": 100}).json()["items"]) == 100
    assert len(human.get("/api/activity", params={"limit": 25}).json()["items"]) == 25
    assert human.get("/api/activity", params={"limit": 30}).status_code == 400
    assert human.get("/api/activity", params={"cursor": "garbage!"}).status_code == 400


def test_search_date_range_and_tab_counts(human, app):
    me = uid_of(human)
    _seed(app, me, 3, action="special_thing")
    _seed(app, me, 2, action="old_thing", ts=time.time() - 10 * 86400)
    p = human.get("/api/activity", params={"q": "special"}).json()
    assert {i["action"] for i in p["items"]} == {"special_thing"}
    assert {i["action"] for i in human.get("/api/activity", params={"q": "special_thing"}).json()["items"]} == {"special_thing"}
    assert human.get("/api/activity", params={"q": "special%thing"}).json()["items"] == []  # % and _ are literal
    today = time.strftime("%Y-%m-%d", time.gmtime())
    p = human.get("/api/activity", params={"from": today, "q": "thing"}).json()
    assert {i["action"] for i in p["items"]} == {"special_thing"}
    old = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * 86400))
    p = human.get("/api/activity", params={"from": old, "to": old, "q": "thing"}).json()
    assert {i["action"] for i in p["items"]} == {"old_thing"}
    counts = human.get("/api/activity").json()["counts"]
    assert counts["all"] == counts["signin"] + counts["agent"] + counts["human"] + counts["system"]
    assert human.get("/api/activity", params={"tab": "nope"}).status_code == 400


def test_csv_export_is_authenticated_filtered_and_formula_safe(human, app):
    me = uid_of(human)
    _seed(app, me, 2, action="=HYPERLINK(\"http://evil\")")
    r = human.get("/api/activity/export.csv", params={"q": "hyperlink"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    data = list(csv.reader(io.StringIO(r.text)))
    assert data[0] == list(activity.CSV_COLS) and len(data) == 3
    assert all(row[4].startswith("'=") for row in data[1:])
    assert TestClient(app, base_url="https://hq.test").get("/api/activity/export.csv").status_code == 401
    assert TestClient(app, base_url="https://hq.test").get("/api/activity").status_code == 401


def test_agents_read_activity_without_ip_addresses(human, app):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "get_activity", {"tab": "signin", "limit": 25})
    assert not res["isError"] and out["items"] and all("ip" not in i and "ua" not in i for i in out["items"])
    r = TestClient(app, base_url="https://hq.test").get("/api/activity/export.csv", headers={"Authorization": f"Bearer {tok}"})
    data = list(csv.DictReader(io.StringIO(r.text)))
    assert data and all(d["ip"] == "" and d["user_agent"] == "" for d in data)
    tools = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    assert "get_activity" in {t["name"] for t in tools}


def test_retention_purges_old_rows_and_old_sign_in_ips(human, app):
    me = uid_of(human)
    _seed(app, me, 2, action="ancient", ts=time.time() - (activity.RETENTION_DAYS + 1) * 86400)
    st = app.state.store
    st.x("UPDATE audit SET ts=? WHERE action='connect_buffer'", (time.time() - (activity.SIGNIN_IP_DAYS + 1) * 86400,))
    assert activity.purge(st)["deleted"] == 2
    assert not [x for x in rows(app, me) if x["action"] == "ancient"]
    r = [x for x in rows(app, me) if x["action"] == "connect_buffer"][0]
    assert r["ip"] == "" and r["ua"] == ""


# ------------------------------------------------------------------ no secrets, ever
def test_scrub_drops_secret_keys_and_token_shaped_values():
    d = activity.scrub({"api_key": "x", "refresh_token": "y", "Authorization": "Bearer abc.def.ghi", "code": "c",
                        "note": "used hq_abcdefghijklmnopqrstuvwxyz and sk-abcdefghijklmnop", "token_id": 7, "id": 3,
                        "nested": [{"password": "p"}, "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.sig"]})
    assert d["api_key"] == d["refresh_token"] == d["Authorization"] == d["code"] == "[redacted]"
    assert "hq_" not in d["note"] and "sk-" not in d["note"] and d["token_id"] == 7 and d["id"] == 3
    assert d["nested"][0]["password"] == "[redacted]" and "eyJ" not in d["nested"][1]


def test_no_secret_or_token_value_is_ever_logged(client, app, fake, monkeypatch):
    """Drive the flows that touch secrets, then search every stored activity column for every secret value."""
    from studio import drafter
    csrf = connect(client)
    client.headers["X-HQ-CSRF"] = csrf
    sid = client.cookies.get("hq_sid")
    me = client.get("/api/me").json()["id"]
    tok = agent_token(client)
    agent = TestClient(app, base_url="https://hq.test")
    agent.post("/api/ideas", headers={"Authorization": f"Bearer {tok}"}, json={"text": "hello"})
    mcp_call(client, tok, "whoami")
    agent.get("/api/me", headers={"Authorization": f"Bearer {tok}x"})  # a near-miss token: failed sign-in row
    monkeypatch.setattr(drafter, "_public", lambda h: True)
    monkeypatch.setattr(llm, "test_key", lambda eng, http=None: "ok")
    byok = "gsk_" + "S3cretKeyValue" * 3
    client.post("/api/ai", json={"provider": "groq", "api_key": byok})
    link = client.post("/api/device-link").json()["url"]
    TestClient(app, base_url="https://hq.test").get(urlparse(link).path, follow_redirects=False)
    # an action whose error text carries a token must still be scrubbed
    app.state.store.audit(me, "you", "probe", "read", {"error": f"upstream said Bearer {tok}", "access_token": "at_zzz"})
    secrets_seen = [tok, tok + "x", sid, csrf, byok, urlparse(link).path.rsplit("/", 1)[-1], "at_zzz"]
    secrets_seen += list(fake.refresh_tokens)
    dump = json.dumps(rows(app), default=str)
    for s in secrets_seen:
        assert s and s not in dump, f"secret leaked into the activity log: {s[:6]}…"
    page = json.dumps(client.get("/api/activity", params={"limit": 100}).json())
    csv_text = client.get("/api/activity/export.csv").text
    for s in secrets_seen:
        assert s not in page and s not in csv_text


def test_demo_workspace_has_sample_rows_in_every_activity_tab(client, fake):
    client.get("/demo", follow_redirects=False)
    for tab in ("signin", "agent", "system"):
        items = client.get("/api/activity", params={"tab": tab}).json()["items"]
        assert items and {i["category"] for i in items} == {tab}, tab
    agents = client.get("/api/activity", params={"tab": "agent"}).json()
    assert "claude-sonnet-4-5" in json.dumps(agents)
    signins = client.get("/api/activity", params={"tab": "signin"}).json()["items"]
    assert any(not i["ok"] for i in signins)  # a failed sign-in is part of the sample
    assert all((i.get("ip") or "").startswith("203.0.113.") for i in signins if i.get("ip"))  # documentation range only
    assert fake.calls == []
