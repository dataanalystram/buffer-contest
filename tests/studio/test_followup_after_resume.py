"""A follow-up in a resumed run used to answer "Unexpected error: AttributeError" (live, 4 Nov 2026).

Root cause: Google AI Studio's OpenAI-compatible endpoint answers an error as a JSON LIST ([{"error": {...}}]);
brain_llm._short_error called .get on it, the AttributeError escaped the per-engine fallback in Brain._go, and the run
failed with the exception's class name. Now the error is read whatever its shape, the next engine (or the standard plan)
takes over, and an unexpected crash shows a plain sentence with a reference and a Retry button, never a class name."""
import time

import httpx

from studio import ai_engines, brain_llm
from studio.agent_brain import oops

from .test_agent_ux_nov3 import _fake_reels, _run, _seed, owner  # noqa: F401

LIST_ERR = [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}]


def test_short_error_reads_every_shape():
    def resp(j=None, text=None):
        return httpx.Response(400, json=j) if text is None else httpx.Response(400, text=text)
    assert "API key not valid" in brain_llm._short_error(resp(LIST_ERR))  # raised AttributeError on main
    assert brain_llm._short_error(resp({"error": {"message": "slow down"}})) == "slow down"
    assert brain_llm._short_error(resp({"detail": "nope"})) == "nope"
    assert brain_llm._short_error(resp("just a string"))
    assert brain_llm._short_error(resp([])) == "[]"
    assert brain_llm._short_error(resp(text="<html>bad gateway</html>")) == "(not JSON)"


def test_resume_then_follow_up_succeeds_when_the_engine_answers_a_list(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    h, st = owner.app.state.house, owner.app.state.store
    # the server restarted mid-run: the run is interrupted, then resumed
    run = h.run(a, rid)
    run["result"]["_boot"] = "deadprocess9"
    h._save(rid, run["steps"], run["result"], "running")
    st.x("INSERT INTO agent_procs(boot,seen) VALUES(?,?)", ("deadprocess9", time.time() - 600))
    assert rid in h.brain.interrupt_orphans()
    assert owner.post(f"/api/agent/runs/{rid}/resume", json={}).status_code == 200
    assert _run(owner, rid)["status"] == "ready"
    # now the free Gemini engine is configured and answers its error as a JSON list
    monkeypatch.setenv("GOOGLE_AI_STUDIO_KEY", "test-dummy-key")
    inner = h.http._transport

    def route(req):
        if req.url.host == "generativelanguage.googleapis.com":
            return httpx.Response(400, json=LIST_ERR)
        return inner.handle_request(req)
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(route)))
    try:
        for text in ("can you generate one more nice cover image", "Make the hook stronger"):
            r = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": text})
            assert r.status_code == 200, r.text
            got = _run(owner, rid)
            said = " ".join(m["text"] for m in got["result"]["thread"])
            assert "AttributeError" not in said and "Unexpected error" not in said, said[-400:]
            assert got["status"] == "ready", got["result"]["thread"][-3:]
        assert "stronger hook" in got["result"]["thread"][-1]["text"].lower()
    finally:
        ai_engines.clear_down()


def test_a_crash_shows_a_plain_message_with_a_reference_and_retry(owner, monkeypatch, capsys):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    h = owner.app.state.house

    def boom(*args, **kw):
        raise AttributeError("'list' object has no attribute 'get'")
    monkeypatch.setattr(h.brain, "_planner", boom)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "Make the hook stronger"})
    got = _run(owner, rid)
    last = got["result"]["thread"][-1]["text"]
    assert got["status"] == "failed"
    assert "AttributeError" not in last and "Unexpected error" not in last and "Retry" in last
    err = got["result"]["error"]
    assert err["ref"] in last and err["retry_text"] == "Make the hook stronger"
    assert last == oops(err["ref"])
    logged = capsys.readouterr().out
    assert f"ref={err['ref']}" in logged and "Traceback" in logged or f"ref={err['ref']}" in logged
    # Retry (the same words again) clears the error and works once the cause is gone
    monkeypatch.undo()
    _fake_reels(monkeypatch, owner)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": err["retry_text"]})
    got = _run(owner, rid)
    assert got["status"] == "ready" and "error" not in got["result"]


def test_stop_stops_at_the_next_step_and_resume_carries_on(owner, monkeypatch):  # noqa: F811
    from studio import agent_brain
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    h = owner.app.state.house
    real, seen = agent_brain.Brain._step, {"n": 0}

    def step(self, a_, rid_, tool, args, label=None):
        seen["n"] += 1
        if seen["n"] == 2:  # Stop pressed while the first step of the follow-up works
            with agent_brain._rlock:
                agent_brain._stop_req.add(rid_)
        return real(self, a_, rid_, tool, args, label)
    monkeypatch.setattr(agent_brain.Brain, "_step", step)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "Make the hook stronger and redo the captions"})
    got = _run(owner, rid)
    assert seen["n"] >= 2
    assert got["status"] == "failed" and got["result"]["thread"][-1]["text"] == agent_brain.STOPPED
    assert "error" not in got["result"] and got["result"]["stopped"]
    monkeypatch.setattr(agent_brain.Brain, "_step", real)
    assert owner.post(f"/api/agent/runs/{rid}/resume", json={}).status_code == 200
    assert _run(owner, rid)["status"] == "ready" and "stopped" not in _run(owner, rid)["result"]
    # Stop on a run nothing works on is a no-op that says so; an unknown run is a 404
    out = owner.post(f"/api/agent/runs/{rid}/stop", json={})
    assert out.status_code == 200 and out.json()["stopping"] is False
    assert owner.post("/api/agent/runs/999999/stop", json={}).status_code == 404
    assert not agent_brain._stop_req
    assert h.run(a, rid)
