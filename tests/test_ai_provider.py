"""Bring-your-own AI model: provider presets, .env fallback, test connection, model list, reasoning models, the caption
writer, the agent-visible status tool and the `huhqueue ai` CLI. Keys never leave the server."""
import json

import httpx
import respx
from fastapi.testclient import TestClient

from huhqueue import actions as reg
from huhqueue import cli
from huhqueue.services import assistant

NIM = "https://integrate.api.nvidia.com/v1"


def _answer(content: str, reasoning: str = "") -> dict:
    msg = {"role": "assistant", "content": content}
    if reasoning:
        msg["reasoning_content"] = reasoning
    return {"choices": [{"message": msg}]}


def test_presets_cover_free_and_local_providers(api):
    st = api.get("/api/ai/settings").json()
    for p, url in {"nim": NIM, "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
                   "groq": "https://api.groq.com/openai/v1", "openrouter": "https://openrouter.ai/api/v1",
                   "ollama": "http://localhost:11434/v1"}.items():
        assert st["providers"][p]["base_url"] == url and st["providers"][p]["key_url"].startswith("https://")
    assert st["configured"] is False and st["max_tokens"] == assistant.DEFAULT_MAX_TOKENS


@respx.mock
def test_nim_key_is_encrypted_and_used_as_bearer(api, core):
    r = api.put("/api/ai/settings", {"provider": "nim", "model": "nvidia/nemotron-3-ultra-550b-a55b", "api_key": "nvapi-secret",
                                     "max_tokens": 8192})
    assert r.status_code == 200 and r.json()["has_key"] and "api_key" not in r.json() and "nvapi-secret" not in r.text
    with core.db.session() as s:
        from huhqueue.models import KV
        assert "nvapi-secret" not in json.dumps(s.get(KV, "ai").value)
    route = respx.post(f"{NIM}/chat/completions").respond(json=_answer("<think>user wants OK</think>\nOK", "thinking…"))
    out = api.post("/api/ai/test").json()
    assert out["ok"] and out["reply"] == "OK" and out["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and out["base_url"] == NIM
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer nvapi-secret"
    assert json.loads(req.content)["max_tokens"] == 8192


@respx.mock
def test_test_connection_reports_provider_errors(api):
    api.put("/api/ai/settings", {"provider": "gemini", "api_key": "bad"})
    respx.post("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions").respond(
        400, json=[{"error": {"message": "API key not valid"}}][0])
    out = api.post("/api/ai/test").json()
    assert out["ok"] is False and "HTTP 400" in out["error"] and "API key not valid" in out["error"]
    assert out["model"] == "gemini-2.5-flash"


@respx.mock
def test_model_list_and_custom_base_url(api):
    api.put("/api/ai/settings", {"provider": "openai", "base_url": "http://localhost:1234/v1", "model": "local"})
    assert api.get("/api/ai/settings").json()["configured"] is False      # openai needs a key
    api.put("/api/ai/settings", {"api_key": "lm-studio"})
    respx.get("http://localhost:1234/v1/models").respond(json={"data": [{"id": "qwen3-8b"}, {"id": "models/gemma-3"}]})
    assert api.get("/api/ai/models").json() == {"models": ["gemma-3", "qwen3-8b"], "error": ""}
    assert api.put("/api/ai/settings", {"base_url": "http://evil.example/v1"}).status_code == 400
    # switching provider drops the old custom URL so the new preset applies
    api.put("/api/ai/settings", {"provider": "groq"})
    assert api.get("/api/ai/settings").json()["base_url"] == ""


@respx.mock
def test_env_fallback_for_headless_installs(api, monkeypatch):
    monkeypatch.setenv("HUHQ_AI_PROVIDER", "nim")
    monkeypatch.setenv("HUHQ_AI_MODEL", "deepseek-ai/deepseek-v4.1-flash")
    monkeypatch.setenv("HUHQ_AI_API_KEY", "nvapi-env")
    st = api.get("/api/ai/settings").json()
    assert st["from_env"] and st["configured"] and st["has_key"] and st["provider"] == "nim"
    route = respx.post(f"{NIM}/chat/completions").respond(json=_answer("OK"))
    assert api.post("/api/ai/test").json()["ok"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer nvapi-env"
    # saving in the UI wins over .env
    api.put("/api/ai/settings", {"provider": "ollama"})
    st = api.get("/api/ai/settings").json()
    assert st["provider"] == "ollama" and not st["from_env"] and st["configured"]


def test_caption_writer(api):
    assert api.post("/api/ai/write", {"text": "launch"}).status_code == 400      # no provider yet
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"options": ["Short one", "x" * 400, ""]})]
    out = api.post("/api/ai/write", {"text": "we shipped scheduled Shorts", "mode": "shorten", "tone": "bold",
                                     "providers": ["x", "instagram"]}).json()
    assert out["limit"] == 280 and out["options"][0] == "Short one" and len(out["options"][1]) == 280 and len(out["options"]) == 2
    sent = assistant.FakeLLM.calls[-1]
    assert "shorter" in sent["system"] and "bold" in sent["system"] and "280" in sent["system"] and sent["user"].startswith("Draft:")
    assistant.FakeLLM.replies[:] = ["Plain text answer"]                     # a model that ignores the JSON format
    assert api.post("/api/ai/write", {"text": "x"}).json()["options"] == ["Plain text answer"]
    assert api.post("/api/ai/write", {"text": "  "}).status_code == 400


def test_agents_see_status_and_writer_but_never_the_key(app, api):
    api.put("/api/ai/settings", {"provider": "nim", "api_key": "nvapi-secret"})
    tok = api.post("/api/tokens", {"name": "agent", "scopes": ["read", "write"]}).json()["token"]
    tc = TestClient(app, headers={"Authorization": f"Bearer {tok}"})
    st = tc.get("/api/ai/status").json()
    assert st["configured"] and st["provider"] == "nim" and st["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert "nvapi" not in json.dumps(st)
    assert tc.get("/api/ai/settings").status_code == 403
    assert tc.put("/api/ai/settings", json={"api_key": "x"}).status_code == 403
    tools = set(reg.mcp_tool_names())
    assert {"ai_status", "ai_write", "ai_test", "ai_models"} <= tools
    assert "ai_settings_get" not in tools and "ai_settings_set" not in tools


@respx.mock
def test_cli_ai_set_reads_key_from_env_not_argv(core, monkeypatch, capsys):
    monkeypatch.setenv("MY_NV_KEY", "nvapi-cli")
    cli.main(["ai", "set", "--provider", "nim", "--model", "nvidia/nemotron-3-ultra-550b-a55b", "--key-env", "MY_NV_KEY"])
    out = json.loads(capsys.readouterr().out)
    assert out["configured"] and out["model"] == "nvidia/nemotron-3-ultra-550b-a55b" and "nvapi" not in json.dumps(out)
    respx.post(f"{NIM}/chat/completions").respond(json=_answer("OK"))
    cli.main(["ai", "test"])
    assert json.loads(capsys.readouterr().out)["ok"]
    respx.get(f"{NIM}/models").respond(json={"data": [{"id": "a/b"}]})
    cli.main(["ai", "models"])
    assert capsys.readouterr().out.strip() == "a/b"


def test_vision_request_shape(monkeypatch):
    """OpenAI-compatible vision: text part + image_url data URL (alt text on Gemini / NIM vision / GPT-4o)."""
    seen = {}

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json=_answer("A purple square"))

    st = {"provider": "openai", "base_url": "", "model": "gpt-4o-mini", "max_tokens": 100, "api_key": "k"}
    monkeypatch.setattr(assistant, "ai_settings", lambda core, reveal=False: st)
    out = assistant.chat(None, "sys", "Describe", transport=httpx.MockTransport(handler), image=("image/png", b"\x89PNG"))
    parts = seen["messages"][1]["content"]
    assert out == "A purple square" and parts[0] == {"type": "text", "text": "Describe"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


@respx.mock
def test_retired_model_error_is_shown_not_hidden(api):
    """NIM answers 410 for retired models (meta/llama-3.3-70b-instruct, Aug 2026): tests and ideas must say so."""
    api.put("/api/ai/settings", {"provider": "nim", "model": "meta/llama-3.3-70b-instruct", "api_key": "nvapi-x"})
    respx.post(f"{NIM}/chat/completions").respond(410, json={"error": {"message": "The model has reached its end of life"}})
    out = api.post("/api/ai/test").json()
    assert not out["ok"] and "HTTP 410" in out["error"] and "end of life" in out["error"]
    ideas = api.post("/api/ideas/generate", {"topic": "AI news", "count": 2}).json()
    assert ideas["source"] == "rules" and "end of life" in ideas["note"]
    assert api.post("/api/ai/write", {"text": "x"}).status_code == 502
