"""End-to-end: Settings › AI model (NVIDIA key, test connection, model list, remove key) and the composer AI writer."""
import json

import pytest
import respx

from huhqueue.services import assistant

from .conftest import PASSWORD, T, api, goto, toast

NIM = "https://integrate.api.nvidia.com/v1"


@pytest.fixture(scope="module", autouse=True)
def nim():
    with respx.mock(assert_all_called=False, assert_all_mocked=False) as m:
        m.route(host="127.0.0.1").pass_through()
        m.route(host="localhost").pass_through()
        m.post(f"{NIM}/chat/completions").respond(json={"choices": [{"message": {"content": "<think>hm</think>OK"}}]})
        m.get(f"{NIM}/models").respond(json={"data": [{"id": "nvidia/nemotron-3-ultra-550b-a55b"}, {"id": "meta/llama-3.3-70b-instruct"}]})
        yield m


def test_00_login(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "ai_writer"})


def test_01_set_up_nvidia(page, server, nim):
    goto(page, server, "/settings?section=ai")
    T(page, "settings-ai").wait_for()
    assert "Not set up" in T(page, "ai-status").inner_text()
    T(page, "ai-provider").select_option("nim")
    assert T(page, "ai-key-link").get_attribute("href").startswith("https://build.nvidia.com")
    T(page, "ai-key").fill("nvapi-e2e-secret")
    T(page, "ai-model").fill("nvidia/nemotron-3-ultra-550b-a55b")
    T(page, "ai-max-tokens").fill("8192")
    T(page, "ai-test").click()                    # saves first, then tests
    T(page, "ai-test-result").wait_for()
    assert "answered “OK”" in T(page, "ai-test-result").inner_text()
    assert "Connected" in T(page, "ai-status").inner_text()
    assert T(page, "ai-key").input_value() == ""  # the key is never sent back
    assert nim.calls.last.request.headers["Authorization"] == "Bearer nvapi-e2e-secret"
    st = api(page, "GET", "/api/ai/settings")
    assert st["provider"] == "nim" and st["max_tokens"] == 8192 and "api_key" not in st


def test_02_model_list(page, server):
    T(page, "ai-models-load").click()
    T(page, "ai-models-count").wait_for()
    assert "2 models" in T(page, "ai-models-count").inner_text()


def test_03_remove_key(page, server):
    T(page, "ai-clear-key").click(); toast(page, "Key removed")
    assert not api(page, "GET", "/api/ai/settings")["has_key"]


def test_04_composer_ai_writer(page, server):
    api(page, "PUT", "/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"options": ["Scheduled Shorts are here 🚀", "Shorts, on autopilot."]})]
    goto(page, server, "/home?compose=new")
    T(page, "composer-caption").fill("we shipped scheduled Shorts")
    T(page, "composer-ai-writer").click()
    T(page, "ai-writer-dialog").wait_for()
    assert T(page, "ai-writer-mode").input_value() == "rephrase"   # a draft is there, so it offers to rephrase
    T(page, "ai-writer-tone").select_option("bold")
    T(page, "ai-writer-run").click()
    page.wait_for_function("document.querySelectorAll('[data-testid=ai-writer-option]').length === 2")
    T(page, "ai-writer-use").nth(1).click(); toast(page, "Caption replaced")
    assert T(page, "composer-caption").input_value() == "Shorts, on autopilot."
    assert "bold" in assistant.FakeLLM.calls[-1]["system"]
