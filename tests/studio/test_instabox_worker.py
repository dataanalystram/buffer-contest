"""studio.instabox_worker: opt-in only, no secret in the exec command, same (ok, why) contract as the GitHub dispatch."""
import json

import httpx

from studio import instabox_worker as ib


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("HQ_RENDER_BACKEND", raising=False)
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test")
    assert not ib.enabled()
    monkeypatch.setenv("HQ_RENDER_BACKEND", "instabox")
    assert ib.enabled()


def test_command_has_no_token_and_quotes():
    cmd = ib.command(42, "https://x.example/app?a=1;rm -rf /", "take_render", "main")
    assert "jt_" not in cmd and "read -r JOB_TOKEN" in cmd
    assert "--job 42" in cmd and "'https://x.example/app?a=1;rm -rf /'" in cmd
    assert "flock /data/hq.lock" in cmd


class _House:
    def __init__(self, http):
        self.http = http
        self.s = type("S", (), {"public_url": "https://huhqueue-studio.onrender.com"})()
        self.issued = []

    def issue_job_token(self, jid, ttl=1800):
        self.issued.append((jid, ttl))
        return "jt_secret"


def test_dispatch_resumes_then_execs_with_token_in_stdin(monkeypatch):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test")
    monkeypatch.setenv("HQ_INSTABOX_MACHINE", "m_test")
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls, state = [], {"s": "suspended"}

    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/resume"):
            assert req.headers.get("Idempotency-Key")
            state["s"] = "running"
            return httpx.Response(202, json={})
        if req.url.path.endswith("/exec"):
            body = json.loads(req.content)
            assert body["stdin"] == "jt_secret\n" and "jt_secret" not in body["cmd"]
            return httpx.Response(200, json={"exitCode": 0, "stdout": "started\n"})
        return httpx.Response(200, json={"state": state["s"], "runtime": {"ready": state["s"] == "running"}})

    h = _House(httpx.Client(transport=httpx.MockTransport(handler)))
    ok, why = ib.dispatch(h, 7, "take.yml", {"kind": "take_analyze"}, 5400)
    assert ok, why
    assert h.issued == [(7, 5400)]
    assert ("POST", "/v1/machines/m_test/resume") in calls and calls[-1] == ("POST", "/v1/machines/m_test/exec")


def test_dispatch_without_machine(monkeypatch):
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test")
    monkeypatch.delenv("HQ_INSTABOX_MACHINE", raising=False)
    assert ib.dispatch(_House(None), 1) == (False, "HQ_INSTABOX_MACHINE is not set")
