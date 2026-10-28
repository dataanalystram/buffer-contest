"""M1 fixes + versioned migrations + the action registry (M5) + the natural-language command bar (M6)."""
import csv
import io
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select

from huhqueue import actions as reg
from huhqueue.models import AuditLog, Post, PostTarget
from huhqueue.services import assistant

from .conftest import future, make_image, upload


def _sent(core, channel_id, n, days_ago=1):
    with core.db.session() as s:
        for i in range(n):
            p = Post(caption=f"sent {i}", tags=[])
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=channel_id, status="sent",
                             published_at=datetime.utcnow() - timedelta(days=days_ago, minutes=i), insights={"views": i}))


# ---------------------------------------------------------------- M1
def test_insights_csv_all_rows_and_custom_range(api, core, channel):
    _sent(core, channel["id"], 25, days_ago=2)
    _sent(core, channel["id"], 3, days_ago=40)
    rows = list(csv.reader(io.StringIO(api.get("/api/insights/export.csv?days=30").text)))
    assert len(rows) == 1 + 25
    start = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() - timedelta(days=35)).strftime("%Y-%m-%d")
    rows = list(csv.reader(io.StringIO(api.get(f"/api/insights/export.csv?start={start}&end={end}").text)))
    assert len(rows) == 1 + 3
    assert len(list(csv.reader(io.StringIO(api.get("/api/insights/export.csv?days=90&channel_id=nope").text)))) == 1


def test_posts_filter_sql_before_limit(api, core, channel):
    with core.db.session() as s:
        for i in range(400):
            p = Post(caption=f"draft {i}", tags=["needle"] if i == 349 else ["hay"], note="find-me" if i == 10 else "")
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=channel["id"], status="draft"))
    assert [t["caption"] for t in api.get("/api/posts?tag=needle&limit=50").json()] == ["draft 349"]
    assert [t["caption"] for t in api.get("/api/posts?q=draft%20399&limit=5").json()] == ["draft 399"]
    assert [t["caption"] for t in api.get("/api/posts?q=find-me&limit=5").json()] == ["draft 10"]
    assert len(api.get("/api/posts?post_type=post&limit=1000").json()) == 400


def test_comments_q_in_sql(api, core, channel):
    from huhqueue.models import Comment
    with core.db.session() as s:
        for i in range(600):
            s.add(Comment(channel_id=channel["id"], external_id=f"c{i}", author="fan", text="needle" if i == 0 else "hay",
                          at=datetime.utcnow() - timedelta(minutes=600 - i)))
    assert len(api.get("/api/comments?q=needle").json()) == 1


def test_llms_txt_and_agents_public(app, api, channel):
    anon = TestClient(app)
    for path in ("/llms.txt", "/agents", "/llms-full.txt"):
        r = anon.get(path)
        assert r.status_code == 200, path
        assert channel["id"] not in r.text and "huhbyte_demo" not in r.text
    assert "post.schedule" in anon.get("/llms-full.txt").text
    assert "post.schedule" in anon.get("/llms.txt").text and "token.create" not in anon.get("/llms.txt").text


def test_target_patch_note(api, channel):
    t = api.post("/api/posts", {"caption": "x", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    assert api.patch(f"/api/targets/{t['id']}", {"note": "check the logo"}).json()["note"] == "check the logo"
    assert api.get(f"/api/targets/{t['id']}").json()["note"] == "check the logo"


def test_ui_session_actions_audited(api, core, channel):
    api.post("/api/ideas", {"title": "audit me"})
    with core.db.session() as s:
        rows = list(s.scalars(select(AuditLog).where(AuditLog.path == "/api/ideas")))
    assert rows and rows[-1].actor == "Owner" and rows[-1].method == "POST"  # the signed-in teammate (bootstrap owner)


def test_mcp_get_analytics_custom_range(api, app, core, channel):
    from huhqueue.mcp_server import McpServer
    _sent(core, channel["id"], 2, days_ago=40)
    tok = api.post("/api/tokens", {"name": "a", "scopes": ["read"]}).json()["token"]
    srv = McpServer("http://t", tok, transport=httpx.MockTransport(lambda req: _asgi(app, req)))
    start = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%d")
    out = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "get_analytics", "arguments": {"start": start, "end": (datetime.utcnow() - timedelta(days=35)).strftime("%Y-%m-%d")}}})
    assert out["result"]["structuredContent"]["kpis"]["posts"] == 2


def test_migrations_fresh_and_upgrade(tmp_path):
    from huhqueue.db import Database
    from huhqueue.migrations import applied, head
    fresh = Database(f"sqlite:///{tmp_path}/fresh.db")
    assert max(applied(fresh.engine)) == head()
    old = tmp_path / "old.db"
    con = sqlite3.connect(old)
    con.executescript("CREATE TABLE channels (id TEXT PRIMARY KEY, provider TEXT, external_id TEXT);"
                      "INSERT INTO channels VALUES ('ch_1','fake','e1');")
    con.close()
    db = Database(f"sqlite:///{old}")
    assert max(applied(db.engine)) == head()
    with db.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA integrity_check").scalar() == "ok"
        assert c.exec_driver_sql("SELECT count(*) FROM channels").scalar() == 1


# ---------------------------------------------------------------- registry
ALLOW = {("GET", "/api/health"), ("GET", "/api/auth/status"), ("POST", "/api/actions/{}:run"),
         ("POST", "/api/assistant/command"), ("POST", "/api/assistant/confirm"), ("POST", "/api/comments/{}/{}")}


def test_registry_parity(app):
    """Every /api route is an action (or on the allow-list) and every REST action has a real route."""
    norm = lambda p: re.sub(r"{\w+}", "{}", p)  # noqa: E731
    routes = {(m, norm(r.path)) for r in app.routes if getattr(r, "path", "").startswith("/api/")
              for m in (getattr(r, "methods", None) or []) if m != "HEAD"}
    routes -= {(m, p) for m, p in routes if p.startswith("/api/docs") or p == "/api/openapi.json"}
    have = {(a.method, norm(a.path)) for a in reg.rest_actions()}
    assert not sorted(routes - have - ALLOW), sorted(routes - have - ALLOW)
    resolve = {("POST", "/api/comments/{}/resolve"), ("POST", "/api/comments/{}/reopen")}
    assert not sorted(have - routes - resolve), sorted(have - routes - resolve)


def test_registry_covers_ui_inventory_and_mcp_coverage():
    assert len(reg.ACTIONS) >= 54
    non_secret = [a for a in reg.ACTIONS if a.method and a.agent_visible()]
    assert len([a for a in non_secret if a.mcp]) / len(non_secret) >= 0.9
    assert {a.id for a in reg.ACTIONS if a.palette} >= {"post.schedule", "nav.home", "ui.theme"}
    assert all(t in reg.mcp_tool_names() for t in reg.LEGACY_TOOLS)


def test_actions_catalog_filtered_for_token(api, app):
    tok = api.post("/api/tokens", {"name": "reader", "scopes": ["read"]}).json()["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {tok}"})
    ids = {a["id"] for a in c.get("/api/actions").json()["actions"]}
    assert "post.list" in ids and "post.schedule" not in ids and "token.create" not in ids and "post.approve" not in ids
    full = {a["id"] for a in api.get("/api/actions").json()["actions"]}
    assert {"post.schedule", "token.create", "nav.home"} <= full


def test_run_action_matches_rest_and_confirm(api, channel):
    r = api.post("/api/actions/post.create_draft:run", {"input": {"caption": "via registry", "channel_ids": [channel["id"]]}}).json()
    assert r["status"] == "done", r
    tid = r["output"]["targets"][0]["id"]
    assert api.get(f"/api/targets/{tid}").json()["status"] == "draft"
    r = api.post("/api/actions/post.update:run", {"input": {"id": tid, "when": "custom", "scheduled_at": future(120)}}).json()
    assert r["output"]["status"] == "scheduled"
    r = api.post("/api/actions/post.delete:run", {"input": {"id": tid}, "dry_run": True}).json()
    assert r["status"] == "needs_confirmation"
    assert api.get(f"/api/targets/{tid}").status_code == 200
    r = api.post("/api/actions/post.delete:run", {"confirm_token": r["confirm_token"]}).json()
    assert r["status"] == "done" and api.get(f"/api/targets/{tid}").status_code == 404
    assert api.post("/api/actions/token.create:run", {"input": {}}).status_code == 403


def test_mcp_matches_rest_for_core_actions(api, channel):
    """schedule, reschedule, reply, resolve and bulk through the runner give the same result as REST."""
    from huhqueue.models import Comment  # noqa: F401
    r = api.post("/api/actions/post.schedule:run", {"input": {"caption": "s", "channel_ids": [channel["id"]], "when": "custom",
                                                               "scheduled_at": future(90)}}).json()
    tid = r["output"]["targets"][0]["id"]
    assert api.get(f"/api/targets/{tid}").json()["status"] == "scheduled"
    api.post("/api/actions/post.bulk:run", {"input": {"action": "draft", "ids": [tid]}})
    assert api.get(f"/api/targets/{tid}").json()["status"] == "draft"


def test_run_action_scope_enforced_for_tokens(api, app, channel):
    tok = api.post("/api/tokens", {"name": "reader", "scopes": ["read"]}).json()["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {tok}"})
    assert c.post("/api/actions/post.create_draft:run", json={"input": {"caption": "x", "channel_ids": [channel["id"]]}}).status_code == 403
    assert c.post("/api/actions/channel.list:run", json={}).json()["status"] == "done"
    assert c.post("/api/actions/post.approve:run", json={"input": {"id": "pt_x"}}).status_code == 403


def test_idempotent_run_once(api, channel):
    body = {"input": {"caption": "once", "channel_ids": [channel["id"]]}, "idempotency_key": "abc"}
    a = api.post("/api/actions/post.create_draft:run", body).json()
    b = api.post("/api/actions/post.create_draft:run", body).json()
    assert b.get("idempotent_replay") and a["output"]["targets"][0]["id"] == b["output"]["targets"][0]["id"]
    assert len(api.get("/api/posts?q=once").json()) == 1


def test_openapi_has_action_extensions(api):
    op = api.get("/api/openapi.json").json()["paths"]["/api/posts"]["post"]
    assert "post.schedule" in op["x-huhq-actions"] and op["x-huhq-risk"]


def _asgi(app, req):
    c = TestClient(app)
    r = c.request(req.method, req.url.raw_path.decode(),
                  headers={k: v for k, v in req.headers.items() if k.lower() in ("authorization", "content-type", "x-huhq-tool")},
                  content=req.content)
    return httpx.Response(r.status_code, headers={"content-type": r.headers.get("content-type", "")}, content=r.content)


def test_mcp_tools_from_registry(api, app, channel):
    from huhqueue.mcp_server import McpServer
    tok = api.post("/api/tokens", {"name": "agent", "scopes": ["read", "write", "publish", "comments"], "require_approval": False}).json()["token"]
    srv = McpServer("http://t", tok, transport=httpx.MockTransport(lambda req: _asgi(app, req)))
    tools = {t["name"] for t in srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert {"schedule_post", "post_delete", "comment_resolve", "idea_create", "post_bulk"} <= tools
    assert not {"token_create", "appkeys_set", "post_approve"} & tools
    out = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "idea_create", "arguments": {"title": "from mcp"}}})
    assert not out["result"]["isError"], out
    assert any(i["title"] == "from mcp" for i in api.get("/api/ideas").json()["ideas"])


def test_cli_run(api, app, channel, capsys):
    from huhqueue import cli
    tok = api.post("/api/tokens", {"name": "cli", "scopes": ["read", "write"]}).json()["token"]
    import huhqueue.cli as c
    orig = c.Client.__init__

    def init(self, url=None, token=None, transport=None):
        orig(self, "http://t", tok, transport=httpx.MockTransport(lambda req: _asgi(app, req)))
    c.Client.__init__ = init
    try:
        cli.main(["run", "idea.create", "title=from cli"])
    finally:
        c.Client.__init__ = orig
    assert '"done"' in capsys.readouterr().out
    assert any(i["title"] == "from cli" for i in api.get("/api/ideas").json()["ideas"])


# ---------------------------------------------------------------- command bar
def test_parse_time_rules():
    tz = "Europe/Dublin"
    now = datetime(2026, 10, 22, 14, 0, tzinfo=ZoneInfo(tz))  # a Thursday
    assert assistant.parse_time("friday 7pm", tz, now) == "2026-10-23T19:00"
    assert assistant.parse_time("tomorrow at 9", tz, now) == "2026-10-23T09:00"
    assert assistant.parse_time("in 2 hours", tz, now) == "2026-10-22T16:00"
    assert assistant.parse_time("thursday 10am", tz, now) == "2026-10-29T10:00"
    assert assistant.parse_time("at 18:30", tz, now) == "2026-10-22T18:30"
    assert assistant.parse_time("nothing here", tz, now) is None


def _orgo(api, tmp_path):
    return upload(api, make_image(tmp_path / "orgo-launch.png"), "orgo-launch.png")[0]


def test_command_bar_schedule_rules(api, core, channel, tmp_path):
    m = _orgo(api, tmp_path)
    r = api.post("/api/assistant/command", {"text": "schedule the orgo launch image for Friday 7pm on demo"}).json()
    assert r["status"] == "needs_confirmation", r
    assert r["plan"]["input"]["media_ids"] == [m["id"]] and r["plan"]["source"] == "rules"
    assert api.get("/api/posts?status=scheduled").json() == []
    done = api.post("/api/assistant/confirm", {"confirm_token": r["confirm_token"]}).json()
    assert done["status"] == "done", done
    t = done["output"]["targets"][0]
    assert t["status"] == "scheduled" and t["scheduled_local_iso"].endswith("T19:00")
    assert datetime.fromisoformat(t["scheduled_local_iso"]).weekday() == 4


def test_command_bar_schedule_with_fake_llm(api, core, channel, tmp_path):
    m = _orgo(api, tmp_path)
    fri = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)   # next Friday (never in the past)
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"action": "post.schedule", "input": {
        "channel_ids": ["fake"], "media_ids": [m["id"]], "when": "custom", "scheduled_at": f"{fri.isoformat()}T19:00",
        "timezone": "Europe/Dublin", "options": {"fk_format": "reel"}, "caption": "Orgo"}, "reply": "ok"})]
    r = api.post("/api/assistant/command", {"text": "schedule the Orgo reel for Friday 7pm on Instagram"}).json()
    assert r["status"] == "needs_confirmation" and r["plan"]["source"] == "llm"
    assert r["plan"]["input"]["channel_ids"] == [channel["id"]]
    sys_prompt = assistant.FakeLLM.calls[-1]["system"]
    assert "token.create" not in sys_prompt and "appkeys" not in sys_prompt and "post.approve" not in sys_prompt
    done = api.post("/api/assistant/confirm", {"confirm_token": r["confirm_token"]}).json()
    assert "output" in done, done
    t = done["output"]["targets"][0]
    assert t["scheduled_local_iso"] == f"{fri.isoformat()}T19:00" and t["options"]["fk_format"] == "reel" and t["media"][0]["id"] == m["id"]


def test_command_bar_never_runs_secret_actions(api, channel):
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"action": "token.create", "input": {"name": "evil"}})]
    r = api.post("/api/assistant/command", {"text": "make me a token"}).json()
    assert r["status"] != "done"
    assert all(t["name"] != "evil" for t in api.get("/api/tokens").json()["tokens"])


def test_destructive_requires_confirm(api, channel):
    for i in range(3):
        api.post("/api/posts", {"caption": f"d{i}", "channel_ids": [channel["id"]], "when": "draft"})
    r = api.post("/api/assistant/command", {"text": "delete all drafts"}).json()
    assert r["status"] == "needs_confirmation" and r["preview"]["count"] == 3
    assert len(api.get("/api/posts?status=draft").json()) == 3
    api.post("/api/assistant/confirm", {"confirm_token": r["confirm_token"]})
    assert api.get("/api/posts?status=draft").json() == []


def test_command_bar_safe_runs_immediately(api, channel):
    api.post("/api/posts", {"caption": "d", "channel_ids": [channel["id"]], "when": "draft"})
    r = api.post("/api/assistant/command", {"text": "show my drafts"}).json()
    assert r["status"] == "done" and len(r["output"]) == 1
    assert api.post("/api/assistant/command", {"text": "go to calendar"}).json()["nav"] == "#/calendar"


def test_ai_settings_ui_only_and_key_hidden(api, app):
    r = api.put("/api/ai/settings", {"provider": "openai", "api_key": "sk-secret-123", "model": "gpt-4o-mini"}).json()
    assert r["has_key"] and "sk-secret" not in json.dumps(r)
    tok = api.post("/api/tokens", {"name": "adm", "scopes": ["admin"]}).json()["token"]
    assert TestClient(app, headers={"Authorization": f"Bearer {tok}"}).get("/api/ai/settings").status_code == 403


def test_providers_payload_shape(core):
    seen = []

    def handler(req):
        seen.append((str(req.url), json.loads(req.content), dict(req.headers)))
        if "anthropic" in str(req.url):
            return httpx.Response(200, json={"content": [{"type": "text", "text": "{\"action\":\"\"}"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"action\":\"\"}"}}]})
    t = httpx.MockTransport(handler)
    for prov, url in (("openai", "https://api.openai.com/v1/chat/completions"), ("nim", "https://integrate.api.nvidia.com/v1/chat/completions"),
                      ("ollama", "http://localhost:11434/v1/chat/completions"), ("anthropic", "https://api.anthropic.com/v1/messages")):
        assistant.set_ai_settings(core, {"provider": prov, "api_key": "k-1", "base_url": "", "model": ""})
        assert assistant.chat(core, "sys", "hi", transport=t) == "{\"action\":\"\"}"
        u, body, h = seen[-1]
        assert u == url
        if prov == "anthropic":
            assert h["x-api-key"] == "k-1" and h["anthropic-version"] == "2023-06-01" and body["system"] == "sys"
        else:
            assert h["authorization"] == "Bearer k-1" and body["messages"][0]["role"] == "system"
