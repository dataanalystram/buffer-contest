"""Agent safety layer: kill switch, per-token caps / expiry / channel scope,
MCP preview + confirm for public actions, audit log filters + CSV export."""
import csv
import io
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from huhqueue import cli, safety
from huhqueue.models import PostTarget

from .conftest import upload
from .test_mcp_cli import call, mcp_for


def _tok(api, **kw):
    body = {"name": kw.pop("name", "agent"), "scopes": kw.pop("scopes", ["read", "write", "publish", "comments"]),
            "require_approval": kw.pop("require_approval", False), **kw}
    r = api.post("/api/tokens", body)
    assert r.status_code == 200, r.text
    return r.json()


def _client(app, raw):
    return TestClient(app, headers={"Authorization": f"Bearer {raw}"})


def test_kill_switch_blocks_tokens_and_holds_agent_posts(app, api, channel, core):
    raw = _tok(api)["token"]
    c = _client(app, raw)
    # an agent schedules a post for now-ish, a human schedules one too
    agent_post = c.post("/api/posts", json={"caption": "agent post", "channel_ids": [channel["id"]], "when": "draft"}).json()
    assert agent_post["targets"][0]["status"] == "draft"
    st = c.post("/api/agents/pause", json={"reason": "testing the brake"}).json()   # an agent may pull the brake itself
    assert st["paused"] and st["paused_by"] == "agent:agent" and st["reason"] == "testing the brake"
    r = c.get("/api/channels")
    assert r.status_code == 423 and r.json()["detail"]["code"] == "agents_paused" and "testing the brake" in r.json()["detail"]["message"]
    assert c.get("/api/agents/safety").json()["paused"] is True          # still allowed: see why you are blocked
    assert c.post("/api/agents/resume").status_code == 423               # agents can never resume themselves
    # the worker holds posts agents created, but not a person's posts
    tid = agent_post["targets"][0]["id"]
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        t.status, t.scheduled_at = "scheduled", datetime.utcnow() - timedelta(minutes=1)
    human = api.post("/api/posts", {"caption": "human post", "channel_ids": [channel["id"]], "when": "now"}).json()
    assert human["targets"][0]["status"] in ("scheduled", "sent")
    api.post("/api/worker/tick")
    assert api.get(f"/api/targets/{tid}").json()["status"] == "scheduled"
    assert api.get(f"/api/targets/{human['targets'][0]['id']}").json()["status"] == "sent"
    # only a person resumes; then the held post goes out
    assert api.post("/api/agents/resume").json()["paused"] is False
    assert c.get("/api/channels").status_code == 200
    api.post("/api/worker/tick")
    assert api.get(f"/api/targets/{tid}").json()["status"] == "sent"
    rows = api.get("/api/audit?q=agents").json()
    assert {"agents.pause", "agents.resume"} <= {a["action"] for a in rows}


def test_cli_kill_switch(app, api, capsys):
    cli.main(["agents", "pause", "--reason", "maintenance"])
    assert safety.is_paused(app.state.core)
    cli.main(["agents", "status"])
    assert '"paused": true' in capsys.readouterr().out
    cli.main(["agents", "resume"])
    assert not safety.is_paused(app.state.core)


def test_daily_caps_count_public_and_all_changes(app, api, channel):
    raw = _tok(api, max_public_per_day=1, max_actions_per_day=4)["token"]
    c = _client(app, raw)
    body = {"caption": "one", "channel_ids": [channel["id"]], "when": "queue", "topic_key": "cap-1"}
    assert c.post("/api/posts", json=body).status_code == 200                 # public 1/1, actions 1/4
    r = c.post("/api/posts", json={**body, "caption": "two", "topic_key": "cap-2"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_public_cap"
    for i in range(3):                                                          # drafts are not public
        assert c.post("/api/posts", json={**body, "when": "draft", "caption": f"d{i}", "topic_key": f"d{i}"}).status_code == 200
    r = c.post("/api/posts", json={**body, "when": "draft", "caption": "d9", "topic_key": "d9"})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_action_cap"
    assert c.get("/api/channels").status_code == 200                            # reads are never capped
    used = c.get("/api/agents/safety").json()["token"]["used_today"]
    assert used == {"actions": 4, "public": 1}
    toks = api.get("/api/tokens").json()["tokens"]
    assert toks[0]["max_public_per_day"] == 1 and toks[0]["used_today"]["public"] == 1


def test_channel_scope(app, api, channel):
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    raw = _tok(api, allowed_channel_ids=[channel["id"]])["token"]
    c = _client(app, raw)
    assert [x["id"] for x in c.get("/api/channels").json()] == [channel["id"]]   # listings only show allowed channels
    ok = c.post("/api/posts", json={"caption": "mine", "channel_ids": [channel["id"]], "when": "draft"})
    assert ok.status_code == 200
    r = c.post("/api/posts", json={"caption": "theirs", "channel_ids": [other["id"]], "when": "draft"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "channel_scope"
    r = c.post("/api/posts", json={"caption": "all", "channel_ids": ["all"], "when": "draft"})
    assert r.status_code == 403
    theirs = api.post("/api/posts", {"caption": "human on other", "channel_ids": [other["id"]], "when": "draft"}).json()
    assert c.patch(f"/api/targets/{theirs['targets'][0]['id']}", json={"caption": "hijack"}).status_code == 403
    assert c.get(f"/api/channels/{other['id']}").status_code == 403
    # editing the scope later works too
    tid = api.get("/api/tokens").json()["tokens"][0]["id"]
    assert api.patch(f"/api/tokens/{tid}", {"allowed_channel_ids": []}).json()["ok"]
    assert len(c.get("/api/channels").json()) == 2


def test_token_expiry(app, api):
    info = _tok(api, expires_in_days=1)
    assert info["expires_at"]
    c = _client(app, info["token"])
    assert c.get("/api/channels").status_code == 200
    api.patch(f"/api/tokens/{info['id']}", {"expires_at": (datetime.utcnow() - timedelta(minutes=1)).isoformat()})
    r = c.get("/api/channels")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "token_expired"
    assert api.get("/api/tokens").json()["tokens"][0]["expired"] is True
    assert api.post("/api/tokens", {"name": "bad", "expires_at": "not a date"}).status_code == 400


def test_mcp_public_actions_need_preview_and_confirm(app, api, channel, tmp_path, core):
    from .conftest import make_image
    raw = _tok(api, name="claude")["token"]
    srv = mcp_for(app, raw)
    media = upload(api, make_image(tmp_path / "p.png"))
    args = {"caption": "Breaking: thing launched", "channel_ids": [channel["id"]], "media_ids": [media[0]["id"]], "when": "now"}
    _, pre = call(srv, "schedule_post", args)
    assert pre["status"] == "needs_confirmation" and pre["preview"]["action"] == "post.schedule"
    assert "Breaking" in pre["preview"]["summary"]
    assert api.get("/api/posts").json() == []                                   # nothing was created
    _, done = call(srv, "schedule_post", {**args, "confirm_token": pre["confirm_token"]})
    assert done["posts"][0]["status"] in ("scheduled", "sent")
    _, again = call(srv, "schedule_post", {**args, "confirm_token": pre["confirm_token"]})   # one use only
    assert again["status"] == "needs_confirmation"
    # registry tools (through /api/actions/{id}:run) get the same treatment
    tid = api.post("/api/posts", {"caption": "failed one", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]["id"]
    with core.db.session() as s:
        s.get(PostTarget, tid).status = "failed"
    _, pre2 = call(srv, "post_retry", {"id": tid})
    assert pre2["status"] == "needs_confirmation" and pre2["preview"]["action"] == "post.retry"
    _, ok = call(srv, "post_retry", {"id": tid, "confirm_token": pre2["confirm_token"]})
    assert ok["status"] == "done"
    # drafts and reads never ask
    _, d = call(srv, "create_post", {"caption": "idea", "channel_ids": [channel["id"]]})
    assert d["posts"][0]["status"] == "draft"
    # auto_confirm tokens skip the preview (a trusted pipeline)
    auto = mcp_for(app, _tok(api, name="pipeline", auto_confirm=True)["token"])
    _, direct = call(auto, "schedule_post", {**args, "caption": "pipeline post", "topic_key": "auto-1", "media_ids": [], "override_rate_limit": True})
    assert "posts" in direct
    # tokens that need human approval are already gated: their schedule goes straight to Approvals
    appr = mcp_for(app, _tok(api, name="careful", require_approval=True)["token"])
    _, held = call(appr, "schedule_post", {**args, "caption": "needs a human", "topic_key": "appr-1", "media_ids": [], "override_rate_limit": True})
    assert held["posts"][0]["status"] == "pending_approval"
    tools = {t["name"]: t for t in srv.allowed_tools()}
    assert "confirm_token" in tools["schedule_post"]["inputSchema"]["properties"]


def test_audit_log_filters_and_csv(app, api, channel):
    raw = _tok(api, name="bot")["token"]
    c = _client(app, raw)
    c.post("/api/posts", json={"caption": "a", "channel_ids": [channel["id"]], "when": "draft"})
    c.post("/api/posts", json={"caption": "b", "channel_ids": [channel["id"]], "when": "queue"})
    c.post("/api/posts", json={"caption": "c", "channel_ids": ["nope"], "when": "draft"})
    rows = api.get("/api/audit?actor=bot").json()
    assert len(rows) == 3 and all(r["actor"] == "bot" for r in rows)
    pub = api.get("/api/audit?actor=bot&risk=public").json()
    assert len(pub) == 1 and pub[0]["action"] == "post.schedule" and pub[0]["created_ids"] and pub[0]["input_hash"]
    assert len(api.get("/api/audit?actor=bot&status=error").json()) == 1
    assert len(api.get("/api/audit?actor=bot&status=ok").json()) == 2
    assert api.get("/api/audit?actor=bot&since=2999-01-01").json() == []
    exp = api.get("/api/audit/export?actor=bot")
    assert exp.status_code == 200 and exp.headers["content-type"].startswith("text/csv")
    got = list(csv.DictReader(io.StringIO(exp.text)))
    assert len(got) == 3 and set(safety.AUDIT_COLUMNS) <= set(got[0])
    facets = api.get("/api/audit/facets").json()
    assert "bot" in facets["actors"] and "public" in facets["risks"]
    # tokens without admin cannot read the audit log
    assert c.get("/api/audit").status_code == 403


def test_kill_switch_state_in_health_and_context(app, api):
    from fastapi.testclient import TestClient
    anon = TestClient(app)
    assert anon.get("/api/health").json()["agents_paused"] is False
    assert api.get("/api/context", params={"page": "/home"}).json()["agents"]["paused"] is False
    api.post("/api/agents/pause", {"reason": "checking a bad post"})
    assert anon.get("/api/health").json()["agents_paused"] is True
    ctx = api.get("/api/context", params={"page": "/queue"}).json()["agents"]
    assert ctx["paused"] is True and ctx["reason"] == "checking a bad post"
    api.post("/api/agents/resume")
    assert anon.get("/api/health").json()["agents_paused"] is False


def test_tool_allow_list_knows_feature_tools_and_rejects_unknown(app, api):
    """Tools registered by feature modules (growth, bio, AI tools...) can be allowed; unknown names are refused,
    never silently dropped (an empty allow-list means every tool)."""
    tools = api.get("/api/tokens").json()["tools"]
    assert {"idea_generate", "comment_insights", "channel_group_create", "agents_pause"} <= set(tools)
    r = api.post("/api/tokens", {"name": "narrow", "scopes": ["read", "write"], "tools": ["idea_generate"]})
    assert r.status_code == 200 and r.json()["tools"] == ["idea_generate"]
    r = api.post("/api/tokens", {"name": "typo", "scopes": ["read"], "tools": ["list_chanels"]})
    assert r.status_code == 400 and "list_chanels" in r.text
    tid = api.get("/api/tokens").json()["tokens"][0]["id"]
    assert api.patch(f"/api/tokens/{tid}", {"tools": ["nope"]}).status_code == 400
    assert "idea_generate" in api.get("/llms.txt").text
