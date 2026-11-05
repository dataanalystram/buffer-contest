"""MCP tool registry (studio/mcp_registry.py): search_tools, describe_tool, call_tool.

call_tool must be exactly a direct call: same authz (token scope and policy), consent / paid-API gate (studio/consent.py),
kill switch, caps and rate limits. No recursion, unknown tool 404, arguments checked against the schema (400, nothing
runs). tools/list stays the full list by default; ?tools=registry gives only the three.
"""
import json

import pytest

from studio import commerce, mcp, mcp_registry
from studio.actions import ActionError, Actor

from .conftest import agent_token, mcp_call
from .test_commerce import c3  # noqa: F401  (fixture: mocked Channel3)
from .test_mcp_run_discipline import fake_server_run  # noqa: F401  (fixture: server-AI run without a model)
from .test_mcp_templates_locks import rpc


def _via(client, tok, name, args=None):
    """The same call through call_tool."""
    return mcp_call(client, tok, "call_tool", {"name": name, "arguments": args or {}})


def _err(res):
    return res["isError"], (res.get("structuredContent") or {}).get("status")


# ------------------------------------------------------------------------------------------------ tools/list
def test_tools_list_is_the_full_list_by_default_and_registry_on_request(human, app):
    from fastapi.testclient import TestClient
    tok = agent_token(human)
    full = rpc(human, tok, "tools/list")["result"]["tools"]
    names = [t["name"] for t in full]
    assert len(full) == len(mcp.TOOLS) and {"search_tools", "describe_tool", "call_tool", "preview_post"} <= set(names)
    bare = TestClient(app, base_url="https://hq.test")
    r = bare.post("/mcp?tools=registry", headers={"Authorization": f"Bearer {tok}"},
                  json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert [t["name"] for t in r.json()["result"]["tools"]] == ["search_tools", "describe_tool", "call_tool"]
    meta = rpc(human, tok, "tools/list", {"_meta": {"huhqueue/tools": "registry"}})["result"]["tools"]
    assert [t["name"] for t in meta] == ["search_tools", "describe_tool", "call_tool"]
    # registry mode still lets every tool be called directly
    r = bare.post("/mcp?tools=registry", headers={"Authorization": f"Bearer {tok}"},
                  json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}})
    assert r.json()["result"]["isError"] is False


def test_unauthenticated_mcp_still_401_in_registry_mode(app):
    from fastapi.testclient import TestClient
    r = TestClient(app, base_url="https://hq.test").post("/mcp?tools=registry", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


# ------------------------------------------------------------------------------------------------ search / describe
def test_search_tools_finds_by_keyword_capped(human):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "search_tools", {"query": "preview post"})
    assert not res["isError"]
    assert out["results"][0]["name"] == "preview_post" and out["count"] <= mcp_registry.DEFAULT_RESULTS
    assert out["results"][0]["required"] == ["channel_id", "text"] and len(out["results"][0]["summary"]) <= 160
    assert all(r["name"] not in mcp_registry.REGISTRY for r in out["results"])
    _, out = mcp_call(human, tok, "search_tools", {"query": "post", "limit": 500})
    assert out["count"] == mcp_registry.MAX_RESULTS
    _, out = mcp_call(human, tok, "search_tools", {"query": "product search"})
    assert "search_products" in [r["name"] for r in out["results"][:3]]
    _, out = mcp_call(human, tok, "search_tools", {"query": "zzzqqq"})
    assert out["results"] == []


@pytest.mark.parametrize("args,status", [({}, 400), ({"query": ""}, 400), ({"query": 5}, 400), ({"query": "x" * 201}, 400),
                                         ({"query": "post", "limit": "3"}, 400), ({"query": "post", "limit": True}, 400),
                                         ({"query": "!!!"}, 400)])
def test_search_tools_bad_input_is_400(human, args, status):
    tok = agent_token(human)
    res, _ = mcp_call(human, tok, "search_tools", args)
    assert _err(res) == (True, status)


def test_describe_tool(human):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "describe_tool", {"name": "preview_post"})
    want = next(t for t in mcp.TOOLS if t["name"] == "preview_post")
    assert not res["isError"] and out["inputSchema"] == want["inputSchema"] and out["read_only"] is True
    res, _ = mcp_call(human, tok, "describe_tool", {"name": "nope_tool"})
    assert _err(res) == (True, 404)
    res, _ = mcp_call(human, tok, "describe_tool", {"name": ["preview_post"]})
    assert _err(res) == (True, 400)


def test_registry_reads_are_quiet_and_work_under_the_kill_switch(human):
    tok = agent_token(human)
    human.post("/api/safety/kill", json={"on": True})
    res, out = mcp_call(human, tok, "search_tools", {"query": "queue"})
    assert not res["isError"] and out["count"] >= 1   # a read of the static list, like whoami
    from studio import mcp_runs
    mcp_runs.ensure(human.app.state.store)
    assert not human.app.state.store.q("SELECT 1 AS n FROM mcp_calls WHERE tool IN ('search_tools','describe_tool')")


# ------------------------------------------------------------------------------------------------ call_tool basics
def test_call_tool_returns_the_same_answer_as_a_direct_call(human):
    tok = agent_token(human)
    for name, args in (("whoami", {}), ("list_channels", {}), ("list_rules", {"network": "instagram"}),
                       ("preflight_post", {"channel_id": "ch_x", "text": "Hello"}), ("list_queue", {"status": "draft"})):
        r1, d1 = mcp_call(human, tok, name, args)
        r2, d2 = _via(human, tok, name, args)
        assert r1["isError"] is r2["isError"] is False, (name, d2)
        assert d1 == d2, name


@pytest.mark.parametrize("inner", ["call_tool", "search_tools", "describe_tool"])
def test_call_tool_cannot_recurse(human, inner):
    tok = agent_token(human)
    res, msg = _via(human, tok, inner, {"name": "whoami"} if inner != "search_tools" else {"query": "x"})
    assert _err(res) == (True, 400) and "can't call" in msg


def test_python_dispatch_never_runs_call_tool():
    """mcp.call_tool (used by MCP prompts and the in-app agent) can't be a back door around the gates in handle()."""
    with pytest.raises(ActionError) as e:
        mcp.call_tool(None, Actor("u1"), "call_tool", {"name": "whoami"})
    assert e.value.status == 400


@pytest.mark.parametrize("args,status", [
    ({"name": "no_such_tool"}, 404),
    ({"name": "x" * 500}, 404),
    ({}, 400), ({"name": ""}, 400), ({"name": 7}, 400),
    ({"name": "whoami", "arguments": "nope"}, 400),
    ({"name": "whoami", "arguments": [1]}, 400),
    ({"name": "whoami", "extra": 1}, 400),
])
def test_call_tool_bad_names_and_args(human, args, status):
    tok = agent_token(human)
    res, _ = mcp_call(human, tok, "call_tool", args)
    assert _err(res) == (True, status)


@pytest.mark.parametrize("args,frag", [
    ({}, "Missing required argument: channel_id"),
    ({"channel_id": "ch_x"}, "Missing required argument: text"),
    ({"channel_id": 5, "text": "hi"}, "channel_id must be string"),
    ({"channel_id": "ch_x", "text": "hi", "mode": "yesterday"}, "mode must be one of"),
    ({"channel_id": "ch_x", "text": "hi", "media_urls": ["ok", 3]}, "media_urls[1] must be string"),
    ({"channel_id": "ch_x", "text": "hi", "override_warnings": "yes"}, "override_warnings must be boolean"),
])
def test_call_tool_validates_against_the_schema_before_running(human, monkeypatch, args, frag):
    ran = []
    real = mcp.call_tool
    monkeypatch.setattr(mcp, "call_tool", lambda *a, **k: ran.append(a) or real(*a, **k))
    tok = agent_token(human)
    res, msg = _via(human, tok, "preview_post", args)
    assert _err(res) == (True, 400) and frag in msg and ran == []


def test_schema_validator_unit():
    v = mcp_registry.validate
    sch = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer", "minimum": 1, "maximum": 7},
                                                              "b": {"type": "boolean"}, "x": {"type": ["string", "null"]}}}
    assert v(sch, {"n": 3}) is None and v(sch, {"n": 3, "x": None, "other": 1}) is None
    assert "at least" in v(sch, {"n": 0}) and "at most" in v(sch, {"n": 8}) and "integer" in v(sch, {"n": True})
    assert "Missing" in v(sch, {"n": None}) and v(sch, "x") == "arguments must be an object."
    assert v(sch, {"n": 2, "b": None}) is None   # an optional argument sent as null passes, as on a direct call
    strict = {**sch, "additionalProperties": False}
    assert v(strict, {"n": 1, "other": 1}) == "Unknown argument: other."
    assert v(strict, {"n": 1, "idempotency_key": "k", "user_asked": True, "approval_id": 3, "server_ai": False}) is None


def test_every_tool_schema_accepts_an_empty_call_shape():
    """Our own schemas must be checkable: every property schema is a dict and every required name is declared."""
    for t in mcp.TOOLS:
        sch = t["inputSchema"]
        props = sch.get("properties") or {}
        assert all(isinstance(p, dict) for p in props.values()), t["name"]
        assert set(sch.get("required") or []) <= set(props), t["name"]


# ------------------------------------------------------------------------------------------------ call_tool = direct call (gates)
def test_call_tool_hits_the_kill_switch_like_a_direct_call(human, fake_server_run):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    human.post("/api/safety/kill", json={"on": True})
    r1, m1 = mcp_call(human, tok, "run_agent", {"query": "x", "server_ai": True})
    r2, m2 = _via(human, tok, "run_agent", {"query": "x", "server_ai": True})
    assert r1["isError"] and r2["isError"] and "kill switch" in m1 and m1 == m2
    r3, m3 = _via(human, tok, "create_idea", {"text": "an idea"})
    assert r3["isError"] and "kill switch" in m3
    assert fake_server_run == [] and not human.get("/api/approvals").json()


def test_call_tool_paid_server_ai_needs_the_same_approval(human, fake_server_run):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    args = {"query": "best headphones under 150", "server_ai": True}
    _, wait = _via(human, tok, "run_agent", args)
    assert wait["needs_user_approval"] is True and wait["spent_usd"] == 0 and fake_server_run == []
    # without server_ai: the client-brain hint, no model call (same as direct)
    _, d_hint = mcp_call(human, tok, "run_agent", {"query": "x"})
    _, v_hint = _via(human, tok, "run_agent", {"query": "x"})
    assert d_hint == v_hint and fake_server_run == []
    assert human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).status_code == 200
    _, ran = _via(human, tok, "run_agent", {**args, "approval_id": wait["approval_id"]})
    assert ran.get("id") == 4242 and len(fake_server_run) == 1
    _, again = _via(human, tok, "run_agent", args)   # used up: asks again, never runs twice on one yes
    assert again["needs_user_approval"] is True and len(fake_server_run) == 1


def test_call_tool_channel3_search_needs_the_persons_approval(human, c3):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=50)
    _, wait = _via(human, tok, "search_products", {"query": "headphones"})
    assert wait["needs_user_approval"] is True and wait["estimate"]["provider"] == "Channel3" and c3.calls == []
    assert human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).status_code == 200
    res, out = _via(human, tok, "search_products", {"query": "headphones", "approval_id": wait["approval_id"]})
    assert not res["isError"] and out["credits_spent"] == 1 and len(c3.calls) == 1
    _, again = _via(human, tok, "search_products", {"query": "headphones"})
    assert again["needs_user_approval"] is True and len(c3.calls) == 1


def test_call_tool_hits_the_same_rate_limit(human, c3, monkeypatch):  # noqa: F811
    monkeypatch.setattr(commerce, "USER_PER_MIN", 1)
    monkeypatch.setattr(commerce, "_hits", {})
    tok = agent_token(human, policy="trusted", cap=50)
    h = [x for x in human.get("/api/safety").json()["tokens"] if x["name"] == "Claude"][0]
    human.patch(f"/api/agent-tokens/{h['id']}", json={"allow_paid_apis": True})   # the person's own standing yes
    res, _ = _via(human, tok, "search_products", {"query": "kettle"})
    assert not res["isError"]
    res, msg = _via(human, tok, "search_products", {"query": "toaster"})
    assert res["isError"] and "Too many" in msg and len(c3.calls) == 1


def test_call_tool_respects_token_channel_scope(human):
    scoped = human.post("/api/agent-tokens", json={"name": "Scoped", "policy": "trusted", "daily_cap": 10,
                                                    "channels": ["ch_ig"]}).json()["token"]
    args = {"channel_id": "ch_x", "text": "Hello"}
    r1, m1 = mcp_call(human, scoped, "preview_post", args)
    r2, m2 = _via(human, scoped, "preview_post", args)
    assert r1["isError"] and r2["isError"] and m1 == m2
    assert r1["structuredContent"]["status"] == r2["structuredContent"]["status"]


def test_call_tool_draft_policy_is_the_same(human):
    tok = human.post("/api/agent-tokens", json={"name": "Drafty", "policy": "draft", "daily_cap": 10}).json()["token"]
    args = {"channel_id": "ch_x", "text": "Hello world", "mode": "now"}
    r1, d1 = mcp_call(human, tok, "preview_post", args)
    r2, d2 = _via(human, tok, "preview_post", args)
    assert r1["isError"] == r2["isError"]
    if not r1["isError"]:
        d1.pop("confirm_token", None), d2.pop("confirm_token", None)
        d1.pop("expires_at", None), d2.pop("expires_at", None)
    assert json.dumps(d1, sort_keys=True, default=str) == json.dumps(d2, sort_keys=True, default=str)


def test_call_tool_records_the_inner_tool_in_activity(human):
    tok = agent_token(human, policy="trusted", cap=10)
    _via(human, tok, "create_idea", {"text": "An idea via the registry"})
    tools = [r["tool"] for r in human.app.state.store.q("SELECT tool FROM mcp_calls")]
    assert "create_idea" in tools and "call_tool" not in tools
