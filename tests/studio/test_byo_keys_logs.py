"""Bring-your-own AI keys (studio/byo_keys.py) and the AI engine logs (studio/ai_logs.py).

Keys: one per provider, tested before saving, sealed in the existing ai_settings secret, masked everywhere, deletable,
and the person's key runs its engine ahead of HuhQueue's key. Agent tokens can read the masked list but never add or
delete a key. Logs: every model call (and every failed one) with engine, model, task, latency, tokens, cost, fallback and
ok/error, filterable and paged, the same over REST and MCP. No real model calls."""
import json

import httpx
import pytest

from studio import activity, ai_engines, brain_llm, budget, byo_keys, drafter, llm

from .conftest import agent_token, mcp_call

KEY = "fake-" + "nv-key-for-tests-WXYZ"  # split so secret scanners see no key


@pytest.fixture
def ok_test(monkeypatch):
    seen = []

    def fake(engine, http=None):
        seen.append({k: engine[k] for k in ("provider", "base_url", "model")})
        if "bad" in engine["api_key"]:
            raise llm.LLMError(f"nvidia answered HTTP 401: invalid key {engine['api_key']}")
        return '{"ok":true}'
    monkeypatch.setattr(llm, "test_key", fake)
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    return seen


def _uid(app):
    return app.state.store.q("SELECT id FROM users LIMIT 1")[0]["id"]


def _mock(*responses):
    seen, queue = [], list(responses)

    def handler(req):
        seen.append(req)
        status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _ok(text="done"):
    return 200, {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": text}}],
                 "usage": {"prompt_tokens": 120, "completion_tokens": 30}}


# ------------------------------------------------------------------ keys
def test_save_tests_seals_masks_and_deletes(app, human, ok_test):
    r = human.post("/api/ai/keys/nvidia", json={"api_key": KEY})
    assert r.status_code == 200, r.text
    assert KEY not in r.text
    row = next(k for k in r.json()["keys"] if k["provider"] == "nvidia")
    assert row["saved"] and row["hint"] == "…WXYZ" and row["engine"] == "nvidia"
    assert ok_test[-1]["base_url"] == "https://integrate.api.nvidia.com/v1"  # tested against the provider's own host
    raw = app.state.store.q("SELECT data FROM ai_settings")[0]["data"]
    assert KEY not in raw and "nvapi" not in raw  # sealed, never plain
    for path in ("/api/ai/keys", "/api/ai", "/api/ai/engine"):
        assert KEY not in human.get(path).text
    eng = r.json()["engine"]
    nv = next(o for o in eng["engines"] if o["id"] == "nvidia")
    assert nv["byo"] and nv["tier"] == "your key" and nv["available"] and "…WXYZ" in nv["credit"]["label"]
    d = human.delete("/api/ai/keys/nvidia")
    assert d.status_code == 200 and not next(k for k in d.json()["keys"] if k["provider"] == "nvidia")["saved"]
    assert human.delete("/api/ai/keys/nvidia").status_code == 404
    acts = [x["action"] for x in app.state.store.audit_rows(_uid(app))]
    assert "save_ai_key" in acts and "delete_ai_key" in acts
    assert KEY not in json.dumps(app.state.store.audit_rows(_uid(app)))


def test_a_bad_key_is_refused_without_echoing_it(app, human, ok_test):
    bad = "nvapi-bad-key-0000000000000"
    r = human.post("/api/ai/keys/nvidia", json={"api_key": bad})
    assert r.status_code == 400 and "didn't work" in r.text and bad not in r.text
    assert not app.state.store.q("SELECT data FROM ai_settings")
    assert human.post("/api/ai/keys/nope", json={"api_key": KEY}).status_code == 400
    assert human.post("/api/ai/keys/openai", json={"api_key": "has space in it"}).status_code == 400


def test_custom_endpoint_needs_https(app, human, ok_test):
    r = human.post("/api/ai/keys/custom", json={"api_key": KEY, "base_url": "http://example.com/v1"})
    assert r.status_code == 400 and "https" in r.text


def test_their_key_runs_its_engine_ahead_of_ours(app, human, ok_test, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-server-not-real")
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "AIza-server-not-real")
    h = app.state.house
    budget.ensure(h.store)
    uid = _uid(app)
    human.post("/api/ai/keys/nvidia", json={"api_key": KEY})
    engs = {e["id"]: e for e in brain_llm.engines(h.store, uid, h.s, h.http)}
    assert engs["nvidia"]["api_key"] == KEY and engs["nvidia"]["source"] == "your key" and not engs["nvidia"].get("free_engine")
    assert engs["gemini-studio"]["api_key"] == "AIza-server-not-real"  # other engines keep HuhQueue's key
    ai_engines.mark_down("nvidia", "the server's key was refused")  # our key down: theirs still runs
    assert "nvidia" in [e["id"] for e in brain_llm.engines(h.store, uid, h.s, h.http)]
    ai_engines.clear_down()
    # their own key is also the "own" engine, and a demo sample never uses it
    assert human.get("/api/ai/engine").json()["engines"][-2]["id"] == "own"
    assert brain_llm.engines(h.store, uid, h.s, h.http, demo=True) == [] or all(
        e.get("source") != "your key" for e in brain_llm.engines(h.store, uid, h.s, h.http, demo=True))


def test_older_single_key_shape_still_reads(app, human):
    st, uid = app.state.store, _uid(app)
    st.x("INSERT INTO ai_settings(user_id,data,updated) VALUES(?,?,?)",
         (uid, st.seal({"provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-old-1234567890", "model": "gpt-4.1-mini"}), 1.0))
    ks = byo_keys.keys(st, uid)
    assert ks["openai"]["api_key"] == "sk-old-1234567890"
    assert llm.resolve(st, uid, app.state.house.s)["provider"] == "openai"
    v = human.get("/api/ai/keys").json()
    assert next(k for k in v["keys"] if k["provider"] == "openai")["hint"] == "…7890" and "sk-old" not in json.dumps(v)


def test_agents_read_masked_keys_but_never_write(app, human, ok_test):
    human.post("/api/ai/keys/nvidia", json={"api_key": KEY})
    tok = agent_token(human)
    _, got = mcp_call(human, tok, "list_ai_keys")
    assert "nvidia" in got["saved"] and KEY not in json.dumps(got)
    res, err = mcp_call(human, tok, "save_ai_key", {"provider": "openai", "api_key": "sk-agent-1234567890"})
    assert res["isError"] and "Only a person" in err
    res, err = mcp_call(human, tok, "delete_ai_key", {"provider": "nvidia"})
    assert res["isError"] and "Only a person" in err
    r = human.post("/api/ai/keys/openai", json={"api_key": "sk-agent-1234567890"}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)


# ------------------------------------------------------------------ logs
def _engine():
    return {"id": "nvidia", "source": "huhqueue free", "provider": "nvidia", "base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": "nvapi-server-not-real", "model": "nvidia/nemotron", "fast_model": "nvidia/nemotron-fast", "style": "openai",
            "tools": True, "budgeted": False, "free_engine": True}


def test_logs_record_ok_error_latency_fallback_and_page(app, human, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-server-not-real")
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    st, uid = app.state.store, _uid(app)
    budget.ensure(st)
    tok = activity.begin(internal_app="huhqueue-agent")
    try:
        activity.note(fallback="Claude out of credit: today's allowance is used")
        http, _ = _mock(_ok("fine"))
        brain_llm.call(_engine(), st, uid, "Ram", system="s", messages=[{"role": "user", "content": "hi"}], http=http,
                       purpose="write captions")
        http, _ = _mock((500, {"error": {"message": "upstream exploded"}}))
        with pytest.raises(llm.LLMError):
            brain_llm.call(_engine(), st, uid, "Ram", system="s", messages=[{"role": "user", "content": "hi"}], http=http,
                           purpose="plan")
    finally:
        activity.end(tok)
    r = human.get("/api/ai/logs").json()
    assert r["total"] == 2 and r["summary"]["errors"] == 1
    bad, good = r["items"]
    assert good["ok"] and good["engine"] == "nvidia" and good["model"] == "nvidia/nemotron" and good["task"] == "write captions"
    assert good["tokens_in"] == 120 and good["tokens_out"] == 30 and good["cost_usd"] == 0
    assert isinstance(good["latency_ms"], int) and good["fallback"].startswith("Claude out of credit")
    assert not bad["ok"] and "upstream exploded" in bad["error"] and bad["fallback"] == ""  # the fallback note is used once
    assert "nvapi-server-not-real" not in json.dumps(r)
    assert human.get("/api/ai/logs?status=error").json()["total"] == 1
    assert human.get("/api/ai/logs?status=ok&engine=nvidia").json()["total"] == 1
    assert human.get("/api/ai/logs?engine=claude").json()["total"] == 0
    assert human.get("/api/ai/logs?q=exploded").json()["total"] == 1
    assert human.get("/api/ai/logs?task=plan").json()["total"] == 1
    p = human.get("/api/ai/logs?limit=10&page=1").json()
    assert p["pages"] == 1 and p["limit"] == 10 and {"id": "nvidia", "label": "NVIDIA"} in p["facets"]["engines"]
    # MCP parity
    tok = agent_token(human)
    _, got = mcp_call(human, tok, "get_ai_logs", {"status": "error"})
    assert got["total"] == 1 and got["items"][0]["error"]


def test_logs_paging(app, human):
    st, uid = app.state.store, _uid(app)
    for i in range(23):
        st.audit(uid, "you", "ai_call", "read", {"engine_id": "claude", "for": "t", "latency_ms": 100 + i, "model": "claude-x"})
    p1 = human.get("/api/ai/logs?limit=10").json()
    p3 = human.get("/api/ai/logs?limit=10&page=3").json()
    assert p1["pages"] == 3 and len(p1["items"]) == 10 and len(p3["items"]) == 3 and p1["items"][0]["id"] > p3["items"][-1]["id"]
    assert human.get("/api/ai/logs?limit=7").json()["limit"] == 25  # only the offered page sizes
