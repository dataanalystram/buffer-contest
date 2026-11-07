"""Free AI engines on HuhQueue's keys (studio/free_engines.py): Google AI Studio, NVIDIA NIM and Hugging Face come first in
the automatic order, carry their own credit lines and daily call caps, survive each provider's tool-call quirks, hand over
to the next engine when refused, and Oracle Gemini is re-checked every 6 h instead of on every run. No real model calls."""
import json
import time

import httpx
import pytest

from studio import ai_engines, brain_llm, budget, drafter, free_engines, llm, oci_genai


@pytest.fixture
def free_keys(monkeypatch):
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "AIza-test-not-real")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-not-real")
    monkeypatch.setenv("HF_TOKEN", "hf_test_not_real")
    monkeypatch.setattr(drafter, "_public", lambda host: True)


@pytest.fixture
def claude_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setattr(brain_llm, "resolve_models", lambda key, http=None, force=False: {"plan": "claude-sonnet-4-5", "fast": "claude-haiku-4-5"})


def _uid(app):
    return app.state.store.q("SELECT id FROM users LIMIT 1")[0]["id"]


def _ids(app, uid, demo=False):
    h = app.state.house
    budget.ensure(h.store)
    return [e["id"] for e in brain_llm.engines(h.store, uid, h.s, h.http, demo=demo)]


def _mock(*responses):
    """An httpx client answering each request with the next (status, json) pair; returns (client, seen requests)."""
    seen, queue = [], list(responses)

    def handler(req):
        seen.append(req)
        status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _ok(text="", tool_calls=None, finish="stop"):
    return 200, {"choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": text,
                                                                   **({"tool_calls": tool_calls} if tool_calls else {})}}],
                 "usage": {"prompt_tokens": 120, "completion_tokens": 30}}


# ------------------------------------------------------------------ order and choice
def test_automatic_order_is_claude_then_nvidia_then_free_engines(app, human, free_keys, claude_env):
    uid = _uid(app)
    assert _ids(app, uid) == ["claude", "nvidia", "gemini-studio", "hf"]  # owner, 7 Nov 2026: Claude first, NVIDIA next
    ai_engines.set_pref(app.state.store, uid, "claude")
    assert _ids(app, uid) == ["claude", "nvidia", "gemini-studio", "hf"]
    ai_engines.set_pref(app.state.store, uid, "hf")  # the person's choice first, then the automatic order
    assert _ids(app, uid) == ["hf", "claude", "nvidia", "gemini-studio"]
    assert _ids(app, uid, demo=True) == []  # the free sample never spends HuhQueue's keys, free or not
    tok = brain_llm.client_brain(True)
    try:
        assert _ids(app, uid) == []  # an MCP client is the brain: none of HuhQueue's engines
    finally:
        brain_llm.client_brain_end(tok)
    eng = free_engines.engine("nvidia")
    assert eng["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and eng["fast_model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert eng["tools"] and eng["style"] == "openai" and not eng["budgeted"]


def test_model_overrides_and_unset_keys(app, human, monkeypatch, free_keys):
    monkeypatch.setenv("NVIDIA_MODEL", "nvidia/other-model")
    assert free_engines.engine("nvidia")["model"] == "nvidia/other-model"
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY")
    monkeypatch.delenv("HF_TOKEN")
    assert _ids(app, _uid(app)) == ["nvidia"]


# ------------------------------------------------------------------ picker: credit lines and setup link
def test_picker_credit_lines_pools_and_ai_studio_link(app, human, monkeypatch, free_keys, claude_env):
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY")
    st = human.get("/api/ai/engine").json()
    by = {o["id"]: o for o in st["engines"]}
    assert [o["id"] for o in st["engines"]] == list(ai_engines.ids())
    assert by["nvidia"]["credit"]["label"] == "NVIDIA (free developer credits): 0 of your 150 calls today"
    assert by["hf"]["credit"]["label"] == "Hugging Face (free monthly credits): 0 of your 40 calls today"
    assert by["nvidia"]["credit"]["kind"] == "free-credit" and by["nvidia"]["available"]
    g = by["gemini-studio"]
    assert not g["available"] and g["reason"] == "Add a free Google AI Studio key"
    assert g["link"] == {"url": "https://aistudio.google.com/apikey", "label": "Get a free key"}
    assert st["runs"] == "claude" and st["selected"] == "auto"
    assert by["claude"]["credit"]["label"] == "Claude: $0.00 of your $5 used"  # $5 per person by default
    assert any(p["label"].startswith("NVIDIA today: 1,500 of 1,500") for p in st["free_pools"])
    human.put("/api/ai/engine", json={"engine": "gemini-studio"})
    st = human.get("/api/ai/engine").json()
    assert st["runs"] == "claude" and "Gemini (AI Studio) isn't available (add a free google ai studio key)" in st["note"]


def test_daily_call_caps_per_person_and_app_wide(app, human, monkeypatch, free_keys):
    monkeypatch.setenv("FREE_HF_USER_DAILY", "2")
    monkeypatch.setenv("FREE_NVIDIA_APP_DAILY", "1")
    st, uid = app.state.store, _uid(app)
    budget.ensure(st)
    budget.free_record(st, uid, "hf", 500)
    budget.free_check(st, uid, "hf")  # 1 of 2: fine
    budget.free_record(st, uid, "hf", 500)
    with pytest.raises(budget.BudgetExceeded, match="2 free Hugging Face calls for today"):
        budget.free_check(st, uid, "hf")
    budget.free_record(st, "someone-else", "nvidia")
    with pytest.raises(budget.BudgetExceeded, match="shared NVIDIA allowance"):
        budget.free_check(st, uid, "nvidia")  # the app-wide pool is shared by everyone
    assert _ids(app, uid) == ["gemini-studio"]
    by = {o["id"]: o for o in human.get("/api/ai/engine").json()["engines"]}
    assert by["hf"]["credit"]["level"] == "out" and by["hf"]["reason"] == "Today's free calls are used up."
    assert budget.claude_credit(st, uid)["used_usd"] == 0  # free calls never touch the Claude allowance


# ------------------------------------------------------------------ the call: tool-call quirks, ledger, failures
def test_nvidia_tool_call_round_trip_and_free_ledger(app, human, free_keys):
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, seen = _mock(_ok("<think>plan the call</think>Checking.", tool_calls=[
        {"type": "function", "function": {"name": "read_link", "arguments": {"url": "https://example.com/a"}}}],  # no id, dict args
        finish="tool_calls"))
    eng = free_engines.engine("nvidia")
    tools = [{"name": "read_link", "description": "Read a link", "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}}}]
    out = brain_llm.call(eng, app.state.store, uid, "Ram", system="s", messages=[{"role": "user", "content": "hi"}], tools=tools, http=http)
    req = seen[0]
    assert req.url.host == "integrate.api.nvidia.com" and req.url.path == "/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer nvapi-test-not-real"
    body = json.loads(req.content)
    assert body["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and body["tools"][0]["function"]["name"] == "read_link"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}  # Nemotron reasons by default: too slow for agent steps
    assert out["text"] == "Checking."  # the <think> block is not shown
    tc = out["tool_calls"][0]
    assert tc["name"] == "read_link" and tc["input"] == {"url": "https://example.com/a"} and tc["id"].startswith("call_0")
    hist = out["assistant"]["tool_calls"][0]
    assert hist["id"] == tc["id"] and json.loads(hist["function"]["arguments"]) == {"url": "https://example.com/a"}
    res = brain_llm.tool_result(eng, tc["id"], tc["name"], {"ok": True})
    assert res["role"] == "tool" and res["tool_call_id"] == tc["id"]  # matches the id kept in the history
    assert out["usage"]["cost"] == 0.0
    cr = budget.free_credit(app.state.store, uid, "nvidia")
    assert cr["calls"] == 1 and cr["label"] == "NVIDIA (free developer credits): 1 of your 150 calls today"
    d = json.loads(app.state.store.q("SELECT detail FROM audit WHERE action='ai_call' ORDER BY id DESC LIMIT 1")[0]["detail"])
    assert d["engine"] == "nvidia" and d["provider"] == "nvidia" and d["cost_usd"] == 0.0
    assert "nvapi" not in json.dumps(d)


def test_openai_message_normalises_provider_differences():
    text, calls, tcs = brain_llm.openai_message({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
        "tool_calls": [{"id": "", "function": {"name": "x", "arguments": "\"{\\\"k\\\": 1}\""}},  # doubly encoded
                       {"id": "g1", "function": {"name": "y", "arguments": ""}, "extra_content": {"google": {"thought_signature": "sig"}}},
                       {"id": "z", "function": {"arguments": "{}"}}]})  # no name: dropped
    assert text == "ab" and [c["name"] for c in calls] == ["x", "y"]
    assert calls[0]["input"] == {"k": 1} and calls[1]["input"] == {} and calls[0]["id"] == "call_0_x"
    assert tcs[1]["extra_content"] == {"google": {"thought_signature": "sig"}}  # Gemini needs it echoed back
    assert brain_llm.openai_message({"content": None}) == ("", [], [])


def test_gemini_studio_request_shape(app, human, free_keys):
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, seen = _mock(_ok("done"))
    eng = free_engines.engine("gemini-studio")
    tools = [{"name": "write", "description": "d", "input_schema": {"type": "object", "additionalProperties": False, "$schema": "x",
                                                                    "properties": {"title": {"type": "string", "default": "a"}}}}]
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "write", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "write", "content": "{}"}]
    brain_llm.call(eng, app.state.store, uid, "Ram", system="s", messages=msgs, tools=tools, http=http, fast=True)
    req = seen[0]
    assert req.url.host == "generativelanguage.googleapis.com" and req.url.path == "/v1beta/openai/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "gemini-flash-lite-latest" and body["reasoning_effort"] == "low"
    params = body["tools"][0]["function"]["parameters"]
    assert "additionalProperties" not in params and "$schema" not in params
    assert params["properties"] == {"title": {"type": "string"}}  # a property named "title" stays; its default goes
    assert "name" not in body["messages"][-1] and body["messages"][-1]["tool_call_id"] == "c1"


@pytest.mark.parametrize("eid,status,payload,reason", [
    ("hf", 402, {"error": "You have exceeded your monthly included credits for Inference Providers."},
     "Hugging Face's free credits are used up"),
    ("nvidia", 410, {"detail": "The model has reached its end of life"}, "the model was retired or moved"),
    ("nvidia", 401, {"detail": "Unauthorized"}, "the server's key was refused"),
])
def test_hard_refusals_mark_the_engine_down_and_charge_nothing(app, human, free_keys, eid, status, payload, reason):
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, _ = _mock((status, payload))
    with pytest.raises(llm.LLMError, match=f"HTTP {status}"):
        brain_llm.call(free_engines.engine(eid), app.state.store, uid, "Ram", system="s",
                       messages=[{"role": "user", "content": "hi"}], http=http)
    assert ai_engines.down(eid) == reason
    assert eid not in _ids(app, uid)
    assert budget.free_credit(app.state.store, uid, eid)["calls"] == 0
    o = next(o for o in human.get("/api/ai/engine").json()["engines"] if o["id"] == eid)
    assert not o["available"] and o["reason"] == f"Unavailable right now: {reason}"


def test_rate_limit_is_short_and_html_errors_stay_short(app, human, free_keys):
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, _ = _mock((429, {"error": {"message": "Too many requests"}}))
    with pytest.raises(llm.LLMError, match="Too many requests"):
        brain_llm.call(free_engines.engine("nvidia"), app.state.store, uid, "Ram", system="s",
                       messages=[{"role": "user", "content": "hi"}], http=http)
    assert ai_engines.down("nvidia") == "rate limited by the free tier"
    assert ai_engines._down["nvidia"][0] - time.time() < 100


def test_a_free_engine_only_talks_to_its_own_host(free_keys):
    eng = {**free_engines.engine("hf"), "base_url": "https://evil.example.com/v1"}
    with pytest.raises(llm.LLMError, match="doesn't belong to the chosen provider"):
        llm.check_engine(eng)
    assert llm.allowed_hosts(free_engines.engine("nvidia")) == frozenset({"integrate.api.nvidia.com"})


# ------------------------------------------------------------------ the agent loop on a free engine
def test_agent_loop_runs_on_nvidia_and_says_so(app, human, free_keys, monkeypatch):
    """A whole run on the NVIDIA engine: a tool call without an id, then finish; the run shows the engine it used."""
    http, seen = _mock(
        _ok("1. Read the link\n2. Finish", tool_calls=[{"type": "function", "function": {"name": "finish", "arguments": "{\"message\": \"All set.\"}"}}],
            finish="tool_calls"))
    real = brain_llm.call
    monkeypatch.setattr(brain_llm, "call", lambda *a, **k: real(*a, **{**k, "http": http}))
    monkeypatch.setattr(brain_llm, "engines", lambda *a, **k: [free_engines.engine("nvidia")])
    app.state.house.sync = True
    r = human.post("/api/agent/runs", json={"prompt": "Write a short post about a new AI launch https://news.example.com/ai-launch",
                                            "format": "text"}).json()
    run = human.get(f"/api/agent/runs/{r['id']}").json()
    assert seen and seen[0].url.host == "integrate.api.nvidia.com"
    brain = run["result"]["brain"]
    assert brain["mode"] == "tools" and brain["provider"] == "nvidia" and brain["source"] == "huhqueue free"
    assert any(t.get("text") == "All set." for t in run["result"]["thread"])
    assert "Unexpected error" not in json.dumps(run["result"]["thread"])


# ------------------------------------------------------------------ Oracle: checked every 6 h, never per run
@pytest.fixture
def oci_set(monkeypatch):
    for k, v in {"HQ_ORACLE_GEMINI": "1", "OCI_USER": "ocid1.user.oc1..u", "OCI_TENANCY": "ocid1.tenancy.oc1..t", "OCI_FINGERPRINT": "aa:bb",
                 "OCI_PRIVATE_KEY": "pem"}.items():
        monkeypatch.setenv(k, v)


PARIS_404 = ("Oracle Cloud answered HTTP 400: {\"error\": {\"code\": 404, \"message\": \"Publisher model `projects/p/locations/"
             "europe-west9/publishers/google/models/gemini-2.5-flash` was not found or your project does not have access to it.")


def test_oracle_probe_runs_at_most_every_six_hours(app, human, oci_set, monkeypatch, claude_env):
    calls = []

    def chat(*a, **k):
        calls.append(k.get("model"))
        raise oci_genai.OCIError(PARIS_404)
    monkeypatch.setattr(oci_genai, "chat", chat)
    st = ai_engines.oracle_check(background=False)
    assert st["ok"] is False and st["reason"] == "Unavailable: Oracle's Paris region can't serve Gemini yet"
    for _ in range(3):  # runs and picker loads within the 6 h never probe again
        ai_engines.oracle_check(background=False)
        human.get("/api/ai/engine")
        _ids(app, _uid(app))
    assert len(calls) == 1
    by = {o["id"]: o for o in human.get("/api/ai/engine").json()["engines"]}
    for eid in ("gemini-flash", "gemini-pro"):  # still listed, plainly unavailable
        assert not by[eid]["available"] and by[eid]["reason"] == "Unavailable: Oracle's Paris region can't serve Gemini yet"
    assert _ids(app, _uid(app)) == ["claude"]
    ai_engines._oracle["at"] -= ai_engines.ORACLE_RECHECK + 1  # six hours later: one new check, and it now answers
    monkeypatch.setattr(oci_genai, "chat", lambda *a, **k: calls.append("again") or {"text": "OK"})
    assert ai_engines.oracle_check(background=False)["ok"] is True and calls[-1] == "again"
    assert _ids(app, _uid(app)) == ["claude", "gemini-flash"]
    assert human.get("/api/ai/engine").json()["oracle"]["recheck_hours"] == 6


def test_a_real_paris_404_parks_oracle_until_the_next_check(app, human, oci_set, monkeypatch):
    ai_engines.oracle_set(True)
    monkeypatch.setattr(oci_genai, "chat", lambda *a, **k: (_ for _ in ()).throw(oci_genai.OCIError(PARIS_404)))
    budget.ensure(app.state.store)
    with pytest.raises(llm.LLMError):
        brain_llm.call(brain_llm._gemini("gemini-flash"), app.state.store, _uid(app), "Ram", system="s",
                       messages=[{"role": "user", "content": "hi"}])
    st = ai_engines.oracle_state()
    assert st["ok"] is False and "Paris region" in st["reason"]
    assert ai_engines.oracle_reason("tenancy limit gemini-2-5-flash is set to 0") == "Unavailable: Oracle's Gemini quota for this account is 0"


def test_oracle_is_off_by_default_no_probe_no_picker_no_order(app, human, monkeypatch, claude_env):
    for k, v in {"OCI_USER": "u", "OCI_TENANCY": "t", "OCI_FINGERPRINT": "aa:bb", "OCI_PRIVATE_KEY": "pem"}.items():
        monkeypatch.setenv(k, v)  # Oracle still configured on the server, but the flag is off
    monkeypatch.delenv("HQ_ORACLE_GEMINI", raising=False)
    probed = []
    monkeypatch.setattr(oci_genai, "chat", lambda *a, **k: probed.append(1))
    st = human.get("/api/ai/engine").json()
    ids = [o["id"] for o in st["engines"]]
    assert "gemini-flash" not in ids and "gemini-pro" not in ids
    assert "gemini-flash" not in st["order"] and "oracle" not in st and "oracle_pool" not in st
    assert ai_engines.oracle_check(background=False)["ok"] is None and probed == []  # no 6-hourly re-check
    assert "gemini-flash" not in _ids(app, _uid(app))
    assert "Gemini (AI Studio)" in st["auto_line"] and "Oracle" not in st["auto_line"]
