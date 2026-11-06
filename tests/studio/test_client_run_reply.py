"""Live run #83 (6 Nov 2026 16:52): "What templates can you use?" typed in the thread of a post an AI app made over MCP
(kind 'client') went to the pre-agent revise pipeline, which crashed on res['format'] being a string ("TypeError: string
indices must be integers"), left the run 'running', and the cut-off sweep then said "The server restarted". A reply on a
client run is answered in the thread and the run stays as it was; revise refuses it with 409."""
import time

from .conftest import agent_token, mcp_call


def _client_run(human):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "start_post", {"title": "Stacktrace launch reel", "user_asked": True, "reason": "asked",
                                                   "format": "reel"})
    assert not res["isError"], out
    return out["run_id"]


def test_a_message_on_an_ai_app_post_is_answered_and_never_crashes(human, app):
    rid = _client_run(human)
    before = human.get(f"/api/agent/runs/{rid}").json()["status"]
    r = human.post(f"/api/agent/runs/{rid}/messages", json={"text": "What templates can you use?"})
    assert r.status_code == 200, r.text
    time.sleep(0.5)  # main spawned a background re-render that crashed and left the run 'running'
    run = human.get(f"/api/agent/runs/{rid}").json()
    assert run["status"] == before and run["status"] not in ("running", "rendering")
    thread = run["result"]["thread"]
    assert thread[-2]["role"] == "user" and thread[-2]["text"] == "What templates can you use?"
    assert thread[-1]["role"] == "agent" and "AI app" in thread[-1]["text"]
    assert not run["result"].get("revisions")


def test_revise_refuses_an_ai_app_post(human, app):
    rid = _client_run(human)
    r = human.post(f"/api/agent/runs/{rid}/revise", json={"correction": "brighter"})
    assert r.status_code == 409, r.text
    assert human.get(f"/api/agent/runs/{rid}").json()["status"] not in ("running", "rendering")
