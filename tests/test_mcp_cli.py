"""MCP server (JSON-RPC over stdio) and the CLI, both driven against the real app through TestClient."""
import io
import json

from fastapi.testclient import TestClient

from huhqueue import cli
from huhqueue.mcp_server import McpServer

from .conftest import make_image


def mcp_for(app, token):
    srv = McpServer(url="http://testserver", token=token)
    srv.http = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    return srv


def rpc(srv, method, params=None, id=1):
    return srv.handle({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}})


def call(srv, name, args=None):
    r = rpc(srv, "tools/call", {"name": name, "arguments": args or {}})
    res = r["result"]
    return res, (json.loads(res["content"][0]["text"]) if not res["isError"] else res["content"][0]["text"])


def test_mcp_handshake_and_tool_listing(app, api):
    tok = api.post("/api/tokens", {"name": "claude", "scopes": ["read", "write", "publish", "comments"]}).json()["token"]
    srv = mcp_for(app, tok)
    init = rpc(srv, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}})
    assert init["result"]["serverInfo"]["name"] == "huhqueue" and "tools" in init["result"]["capabilities"]
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    names = {t["name"] for t in rpc(srv, "tools/list")["result"]["tools"]}
    assert {"list_channels", "create_post", "schedule_post", "upload_media", "list_queue", "reschedule", "get_analytics",
            "list_comments", "reply_comment"} <= names
    assert rpc(srv, "nope")["error"]["code"] == -32601
    # read-only token sees only read tools
    ro = mcp_for(app, api.post("/api/tokens", {"name": "ro", "scopes": ["read"]}).json()["token"])
    ro_names = {t["name"] for t in rpc(ro, "tools/list")["result"]["tools"]}
    assert "schedule_post" not in ro_names and "list_channels" in ro_names
    res, text = call(ro, "schedule_post", {"channel_ids": ["all"]})
    assert res["isError"] and "not allowed" in text


def test_mcp_full_agent_flow_with_approval(app, api, channel, tmp_path, core):
    tok = api.post("/api/tokens", {"name": "claude", "scopes": ["read", "write", "publish", "comments"], "require_approval": True}).json()["token"]
    srv = mcp_for(app, tok)
    _, chans = call(srv, "list_channels")
    assert chans["items"][0]["username"] == "huhbyte_demo" if isinstance(chans, dict) else chans[0]["username"] == "huhbyte_demo"
    img = make_image(tmp_path / "x.png")
    _, up = call(srv, "upload_media", {"paths": [str(img)]})
    assert len(up["media_ids"]) == 1
    _, out = call(srv, "schedule_post", {"caption": "GPT-6 is out #AI", "channel_ids": ["all"], "media_ids": up["media_ids"], "when": "now"})
    pid = out["posts"][0]["post_id"]
    assert out["posts"][0]["status"] == "pending_approval" and "approval" in out["notes"][0]
    _, q = call(srv, "list_queue", {"status": "pending_approval"})
    assert q["items"][0]["post_id"] == pid if isinstance(q, dict) else q[0]["post_id"] == pid
    assert api.post(f"/api/targets/{pid}/approve").json()["status"] == "scheduled"
    api.post("/api/worker/tick")
    _, post = call(srv, "get_post", {"post_id": pid})
    assert post["status"] == "sent" and any("Approved" in e["message"] for e in post["events"])
    _, draft = call(srv, "create_post", {"caption": "idea", "channel_ids": [channel["id"]]})
    _, moved = call(srv, "reschedule", {"post_id": draft["posts"][0]["post_id"], "caption": "better idea"})
    assert moved["status"] == "draft"
    _, an = call(srv, "get_analytics", {"days": "7"})
    assert an["kpis"]["posts"] == 1
    _, cm = call(srv, "list_comments", {"sync": True, "status": "open"})
    items = cm["items"] if isinstance(cm, dict) else cm
    # a public reply first comes back as a preview + confirm_token (agent safety layer); nothing is sent yet
    _, pre = call(srv, "reply_comment", {"comment_id": items[0]["id"], "text": "Thanks!"})
    assert pre["status"] == "needs_confirmation" and pre["confirm_token"].startswith("cf_") and "reply" in pre["preview"]["title"].lower()
    _, bad = call(srv, "reply_comment", {"comment_id": items[0]["id"], "text": "Different text", "confirm_token": pre["confirm_token"]})
    assert bad["status"] == "needs_confirmation"   # a token only confirms the exact request it was issued for
    _, rep = call(srv, "reply_comment", {"comment_id": items[0]["id"], "text": "Thanks!", "confirm_token": pre["confirm_token"]})
    assert rep["status"] == "replied"
    audit = api.get("/api/audit").json()
    assert {"schedule_post", "upload_media", "reply_comment"} <= {a["tool"] for a in audit}


def test_mcp_stdio_loop(app, api):
    tok = api.post("/api/tokens", {"name": "s", "scopes": ["read"]}).json()["token"]
    srv = mcp_for(app, tok)
    inp = io.StringIO('{"jsonrpc":"2.0","id":1,"method":"ping"}\nnot json\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    out = io.StringIO()
    srv.serve(inp, out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert lines[0] == {"jsonrpc": "2.0", "id": 1, "result": {}} and lines[1]["error"]["code"] == -32700 and len(lines) == 2


def test_cli_schedule_list_and_channels(app, api, channel, tmp_path, monkeypatch, capsys):
    tok = api.post("/api/tokens", {"name": "cli", "scopes": ["read", "write", "publish"], "require_approval": False}).json()["token"]
    tc = TestClient(app, headers={"Authorization": f"Bearer {tok}"})
    orig = cli.Client.__init__

    def patched(self, url=None, token=None, transport=None):
        orig(self, "http://testserver", tok)
        self.http = tc
    monkeypatch.setattr(cli.Client, "__init__", patched)
    folder = tmp_path / "renders"
    folder.mkdir()
    for i in range(2):
        make_image(folder / f"{i}.png")
    cli.main(["schedule", "--all-channels", "-m", "From the pipeline #AI", "--media", str(folder), "--tag", "news", "--major-news"])
    out = capsys.readouterr().out
    assert "scheduled" in out and "huhbyte_demo" in out
    cli.main(["list", "--status", "scheduled"])
    assert "From the pipeline" in capsys.readouterr().out
    cli.main(["channels"])
    assert "fake" in capsys.readouterr().out
    cli.main(["schedule", "-c", f"fake:huhbyte_demo", "-m", "now!", "--now"])
    assert "fake" in capsys.readouterr().out
    posts = api.get("/api/posts?status=sent").json()
    assert posts and posts[0]["caption"] == "now!" and posts[0]["source"] == "cli"
