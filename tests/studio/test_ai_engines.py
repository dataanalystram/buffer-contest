"""The AI engine switch (studio/ai_engines.py, brain_llm.engines, budget.oci_*, oci_genai): the chosen engine runs first,
the rest follow in the default order, an engine that is out of credit or erroring hands over to the next one with a note
(never a crash), Gemini on Oracle Cloud has its own ledger (source=oci) and caps, the Oracle request signature verifies,
and REST + MCP expose get/set. No real model calls."""
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from studio import agent_routes, ai_engines, brain_llm, budget, drafter, llm, mcp, oci_genai

from .conftest import agent_token, mcp_call


@pytest.fixture
def oci_env(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    monkeypatch.setenv("HQ_ORACLE_GEMINI", "1")  # Oracle Gemini is off by default; these tests keep the code path honest
    monkeypatch.setenv("OCI_USER", "ocid1.user.oc1..u")
    monkeypatch.setenv("OCI_TENANCY", "ocid1.tenancy.oc1..t")
    monkeypatch.setenv("OCI_FINGERPRINT", "aa:bb")
    monkeypatch.setenv("OCI_REGION", "eu-paris-1")
    monkeypatch.setenv("OCI_PRIVATE_KEY", pem)
    monkeypatch.setenv("OCI_GEMINI_FLASH_MODEL", "ocid1.generativeairoutingprofile.oc1.eu-paris-1.flash")
    monkeypatch.delenv("OCI_GEMINI_PRO_MODEL", raising=False)
    ai_engines.clear_down()
    ai_engines.oracle_set(True)  # the 6-hourly Oracle check passed (no real probe in tests)
    yield key
    ai_engines.clear_down()
    ai_engines.oracle_reset()


@pytest.fixture
def claude_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setattr(brain_llm, "resolve_models", lambda key, http=None, force=False: {"plan": "claude-sonnet-4-5", "fast": "claude-haiku-4-5"})


def _uid(app):
    return app.state.store.q("SELECT id FROM users LIMIT 1")[0]["id"]


def _engines(app, uid, demo=False):
    h = app.state.house
    budget.ensure(h.store)
    return [e["id"] for e in brain_llm.engines(h.store, uid, h.s, h.http, demo=demo)]


# ------------------------------------------------------------------ selection
def test_default_order_and_the_chosen_engine_first(app, human, oci_env, claude_env):
    uid = _uid(app)
    assert _engines(app, uid) == ["gemini-flash", "claude"]  # no free-engine keys, no own key, no free model here
    ai_engines.set_pref(app.state.store, uid, "claude")
    assert _engines(app, uid) == ["claude", "gemini-flash"]
    ai_engines.set_pref(app.state.store, uid, "gemini-pro")  # Pro only runs when chosen; Flash stays as the backup
    assert _engines(app, uid) == ["gemini-pro", "gemini-flash", "claude"]
    eng = brain_llm.engines(app.state.store, uid, app.state.house.s, app.state.house.http)[0]
    assert eng["model"] == "google.gemini-2.5-pro" and eng["fast_model"] == "google.gemini-2.5-flash"
    assert eng["oci_targets"]["google.gemini-2.5-flash"].startswith("ocid1.generativeairoutingprofile")
    assert eng["oci_targets"]["google.gemini-2.5-pro"] == "google.gemini-2.5-pro"  # no profile set: the model name
    ai_engines.set_pref(app.state.store, uid, "auto")
    assert _engines(app, uid) == ["gemini-flash", "claude"]
    assert _engines(app, uid, demo=True) == []  # the free sample never reaches the app's paid engines


def test_unknown_engine_is_refused_and_aliases_work(app, human, monkeypatch):
    uid = _uid(app)
    budget.ensure(app.state.store)
    with pytest.raises(ai_engines.EngineError):
        ai_engines.set_pref(app.state.store, uid, "gpt-9")
    assert ai_engines.set_pref(app.state.store, uid, "Gemini") == "gemini-studio"
    with pytest.raises(ai_engines.EngineError):  # Oracle Gemini is off unless HQ_ORACLE_GEMINI=1
        ai_engines.set_pref(app.state.store, uid, "oracle")
    monkeypatch.setenv("HQ_ORACLE_GEMINI", "1")
    assert ai_engines.set_pref(app.state.store, uid, "oracle") == "gemini-flash"
    monkeypatch.delenv("HQ_ORACLE_GEMINI")
    assert ai_engines.set_pref(app.state.store, uid, "NIM") == "nvidia"
    assert ai_engines.set_pref(app.state.store, uid, "Hugging Face") == "hf"
    assert ai_engines.set_pref(app.state.store, uid, "anthropic") == "claude"


def test_marked_down_engine_is_skipped_and_shown(app, human, oci_env, claude_env):
    uid = _uid(app)
    ai_engines.mark_down("gemini-flash", "Oracle's Gemini quota for this account is 0")
    assert _engines(app, uid) == ["claude"]
    st = human.get("/api/ai/engine").json()
    g = next(o for o in st["engines"] if o["id"] == "gemini-flash")
    assert not g["available"] and g["reason"].startswith("Unavailable right now: Oracle's Gemini quota")
    human.put("/api/ai/engine", json={"engine": "gemini-flash"})
    st = human.get("/api/ai/engine").json()
    assert st["selected"] == "gemini-flash" and st["runs"] == "claude"
    assert "Gemini Flash (Oracle) isn't available" in st["note"] and "Claude runs instead" in st["note"]


# ------------------------------------------------------------------ credits and budgets
def test_credit_lines_in_plain_words_and_warnings(app, human, oci_env, claude_env, monkeypatch):
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    monkeypatch.setenv("AI_OCI_BUDGET_PER_USER_USD", "3")
    uid = _uid(app)
    budget.ensure(app.state.store)
    budget.record(app.state.store, uid, 0.34)
    st = human.get("/api/ai/engine").json()
    by = {o["id"]: o for o in st["engines"]}
    assert by["claude"]["credit"]["label"] == "Claude: $0.34 of your $1 used" and by["claude"]["credit"]["level"] == "ok"
    assert by["gemini-flash"]["credit"]["label"] == "Gemini (Oracle): free during the contest, $0.00 of your $3 used"
    assert st["claude_pool"]["label"].startswith("App-wide Claude pool: $59.66 of $60")
    assert st["oracle_pool"]["label"] == "App-wide Gemini pool: $200.00 of $200 left"
    budget.record(app.state.store, uid, 0.40)  # 74%: the 70% warning
    c = next(o for o in human.get("/api/ai/engine").json()["engines"] if o["id"] == "claude")["credit"]
    assert c["level"] == "low" and c["used_pct"] == 74
    budget.record(app.state.store, uid, 0.30)  # past 100%: out, and Claude hands over
    st = human.get("/api/ai/engine").json()
    cl = next(o for o in st["engines"] if o["id"] == "claude")
    assert cl["credit"]["level"] == "out" and not cl["available"]
    assert st["runs"] == "gemini-flash"
    # the existing allowance meter (70% / 100% nudges) is unchanged
    assert human.get("/api/agent/brain").json()["meter"]["out"]


def test_oci_budget_caps_per_user_and_app_wide(app, human, monkeypatch):
    monkeypatch.setenv("AI_OCI_BUDGET_PER_USER_USD", "3")
    monkeypatch.setenv("AI_OCI_BUDGET_TOTAL_USD", "200")
    st, uid = app.state.store, _uid(app)
    budget.ensure(st)
    budget.oci_check(st, uid, "google.gemini-2.5-flash", 2000, 1500)  # fine
    budget.oci_record(st, uid, 2.999)
    with pytest.raises(budget.BudgetExceeded, match=r"\$3 of included Gemini"):
        budget.oci_check(st, uid, "google.gemini-2.5-flash", 2000, 1500)
    monkeypatch.setenv("AI_OCI_BUDGET_TOTAL_USD", "1")
    with pytest.raises(budget.BudgetExceeded, match="shared Gemini credit"):
        budget.oci_check(st, "someone-else", "google.gemini-2.5-flash", 10, 10)
    # Oracle spend never touches the Claude allowance
    assert budget.claude_credit(st, uid)["used_usd"] == 0
    assert oci_genai.price("google.gemini-2.5-flash", 1_000_000, 1_000_000) == 2.80
    assert oci_genai.price("ocid1.generativeairoutingprofile.x", 1_000_000, 0) == 1.25  # unknown: the dearest rate


# ------------------------------------------------------------------ Oracle signing and the call
def test_oci_signature_verifies_and_covers_the_body(oci_env):
    key = oci_env
    body = b'{"a":1}'
    url = "https://inference.generativeai.eu-paris-1.oci.oraclecloud.com/20231130/actions/chat"
    h = oci_genai.sign_headers("POST", url, body, date="Mon, 02 Nov 2026 10:00:00 GMT")
    auth = h["authorization"]
    assert 'keyId="ocid1.tenancy.oc1..t/ocid1.user.oc1..u/aa:bb"' in auth and 'algorithm="rsa-sha256"' in auth
    assert 'headers="date (request-target) host x-content-sha256 content-type content-length"' in auth
    sig = base64.b64decode(auth.split('signature="')[1].rstrip('"'))
    signed = "\n".join(["date: Mon, 02 Nov 2026 10:00:00 GMT", "(request-target): post /20231130/actions/chat",
                        "host: inference.generativeai.eu-paris-1.oci.oraclecloud.com",
                        f"x-content-sha256: {h['x-content-sha256']}", "content-type: application/json", "content-length: 7"])
    key.public_key().verify(sig, signed.encode(), padding.PKCS1v15(), hashes.SHA256())  # raises when wrong
    assert "PRIVATE" not in json.dumps(h)


def test_generic_request_and_response_shapes():
    msgs = [{"role": "user", "content": "hi"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read", "arguments": "{\"u\":1}"}}]},
            {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "{\"ok\":true}"}]
    req = oci_genai.to_generic("sys", msgs, [{"name": "read", "description": "d", "input_schema": {"type": "object"}}], 300,
                               "gemini-2.5-flash")
    cr = req["chatRequest"]
    assert req["servingMode"] == {"servingType": "ON_DEMAND", "modelId": "google.gemini-2.5-flash"}
    assert [m["role"] for m in cr["messages"]] == ["SYSTEM", "USER", "ASSISTANT", "TOOL"]
    assert cr["messages"][2]["toolCalls"][0]["name"] == "read" and cr["messages"][3]["toolCallId"] == "c1"
    assert cr["tools"][0]["type"] == "FUNCTION" and cr["maxTokens"] == 300
    out = oci_genai.from_generic({"chatResponse": {"choices": [{"finishReason": "tool_calls", "message": {
        "content": [{"type": "TEXT", "text": "ok"}], "toolCalls": [{"id": "x", "name": "finish", "arguments": {"message": "done"}}]}}],
        "usage": {"promptTokens": 12, "completionTokens": 3}}})
    assert out["text"] == "ok" and out["in"] == 12 and out["out"] == 3
    assert json.loads(out["tool_calls"][0]["function"]["arguments"]) == {"message": "done"}


def _oci_http(status, payload):
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(status, json=payload)
    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def test_gemini_call_records_the_oci_ledger(app, human, oci_env, monkeypatch):
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, seen = _oci_http(200, {"chatResponse": {"choices": [{"finishReason": "stop", "message": {"content": [{"type": "TEXT", "text": "{\"ok\":true}"}]}}],
                                                  "usage": {"promptTokens": 1000, "completionTokens": 100}}})
    eng = brain_llm._gemini("gemini-flash")
    out = brain_llm.call(eng, app.state.store, uid, "Ram", system="s", messages=[{"role": "user", "content": "hi"}], http=http)
    assert out["text"] == '{"ok":true}' and out["usage"]["cost"] == oci_genai.price("google.gemini-2.5-flash", 1000, 100)
    body = json.loads(seen[0].content)
    assert body["servingMode"]["modelId"] == "ocid1.generativeairoutingprofile.oc1.eu-paris-1.flash"  # routed via the profile
    assert seen[0].url.host == "inference.generativeai.eu-paris-1.oci.oraclecloud.com"
    assert "Signature version" in seen[0].headers["authorization"]
    assert budget.oci_credit(app.state.store, uid)["used_usd"] == round(out["usage"]["cost"], 2)
    row = app.state.store.q("SELECT detail FROM audit WHERE action='ai_call' ORDER BY id DESC LIMIT 1")[0]
    d = json.loads(row["detail"])
    assert d["source"] == "oci" and d["provider"] == "oci"


def test_gemini_quota_zero_marks_it_down_and_never_crashes(app, human, oci_env, monkeypatch):
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    uid = _uid(app)
    budget.ensure(app.state.store)
    http, _ = _oci_http(429, {"code": "429", "message": "Access to model google.gemini-2.5-flash is disabled because tenancy limit "
                                                       "gemini-2-5-flash-chat-tokens-per-minute-count is set to 0."})
    with pytest.raises(llm.LLMError, match="HTTP 429"):
        brain_llm.call(brain_llm._gemini("gemini-flash"), app.state.store, uid, "Ram", system="s",
                       messages=[{"role": "user", "content": "hi"}], http=http)
    assert ai_engines.down("gemini-flash") == "Oracle's Gemini quota for this account is 0"
    assert budget.oci_credit(app.state.store, uid)["used_usd"] == 0  # nothing charged for a refused call


def test_agent_falls_back_engine_by_engine_with_a_note(app, human, monkeypatch):
    """Claude out of credit -> Gemini errors -> the standard plan: two notes in the run, the run still finishes."""
    from studio import agent_brain
    tried = []
    monkeypatch.setattr(brain_llm, "engines", lambda *a, **k: [
        {"id": "claude", "source": "huhqueue claude", "provider": "anthropic", "tools": True, "model": "m", "style": "anthropic"},
        {"id": "gemini-flash", "source": "huhqueue gemini", "provider": "oci", "tools": True, "model": "g", "style": "oci"}])

    def loop(self, a, rid, tools, eng, message):
        tried.append(eng["id"])
        if eng["id"] == "claude":
            raise budget.BudgetExceeded("Your $1.00 of included Claude usage is used up. Add your own key")
        raise llm.LLMError("Oracle Cloud answered HTTP 429: quota")
    monkeypatch.setattr(agent_brain.Brain, "_llm_loop", loop)
    planned = []
    monkeypatch.setattr(agent_brain.Brain, "_planner", lambda self, a, rid, tools, message, engs: planned.append(rid))
    h = app.state.house
    h.sync = True
    r = human.post("/api/agent/runs", json={"prompt": "Write a short post about a new AI launch https://news.example.com/ai-launch",
                                            "format": "text"}).json()
    run = human.get(f"/api/agent/runs/{r['id']}").json()
    assert tried == ["claude", "gemini-flash"] and planned == [r["id"]]  # then the standard plan
    notes = run["result"].get("notes") or []
    assert any(n.startswith("Claude is out of credit") and n.endswith("so I switched to Gemini Flash (Oracle).") for n in notes), notes
    assert any(n.startswith("Gemini Flash (Oracle) didn't answer") and n.endswith("so I switched to the standard plan.") for n in notes), notes
    assert "Unexpected error" not in json.dumps(run["result"].get("thread"))


# ------------------------------------------------------------------ REST and MCP parity
def test_rest_and_mcp_get_and_set(app, human, oci_env, claude_env):
    st = human.get("/api/ai/engine").json()
    assert st["selected"] == "auto" and [o["id"] for o in st["engines"]] == list(ai_engines.ids())
    assert human.put("/api/ai/engine", json={"engine": "nope"}).status_code == 400
    assert human.put("/api/ai/engine", json={"engine": "gemini-pro"}).json()["selected"] == "gemini-pro"
    assert human.get("/api/agent/brain").json()["engine"]["selected"] == "gemini-pro"
    names = {t["name"] for t in agent_routes.TOOLS}
    assert {"get_ai_engine", "set_ai_engine"} <= names and "get_ai_engine" in mcp.READ_ONLY
    tok = agent_token(human)
    _, got = mcp_call(human, tok, "get_ai_engine")
    assert got["selected"] == "gemini-pro" and any(o["id"] == "claude" for o in got["engines"])
    _, got = mcp_call(human, tok, "set_ai_engine", {"engine": "claude"})
    assert got["selected"] == "claude"
    res, err = mcp_call(human, tok, "set_ai_engine", {"engine": "nope"})
    assert res["isError"] and "Unknown AI engine" in err


def test_list_themes_carries_categories_and_descriptions(human):
    r = human.get("/api/themes").json()
    cats = {c["id"]: c["count"] for c in r["categories"]}
    assert set(cats) == {"news", "startups", "clips", "explainers", "cinematic"} and sum(cats.values()) == len(r["themes"])
    for t in r["themes"]:
        assert t["category"] in cats and t["short"] and len(t["short"]) <= 90 and t["duration"] and t["popularity"] < 99
