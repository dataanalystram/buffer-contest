"""studio.instabox_worker: opt-in only, no secret in the exec command, same (ok, why) contract as the GitHub dispatch."""
import json

import httpx
import pytest

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
    assert "HQ_LOCK=/data/hq.lock" in cmd and "flock" not in cmd  # the worker locks after its heartbeat starts


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


# ---- fallback: InstaBox down or slow -> the GitHub Actions run, and the box's token stops working
from studio import house as house_mod  # noqa: E402


def _queued_job(h):
    import time as _t
    h.store.x("INSERT INTO users(id) VALUES(?) ON CONFLICT DO NOTHING", ("u_ib",))
    return h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                     ("u_ib", 0, json.dumps({"kind": "slideshow", "slides": [], "kit": {}, "params": {}}), "queued", _t.time(), _t.time()))


def _env(monkeypatch):
    monkeypatch.setenv("HQ_RENDER_BACKEND", "instabox")
    monkeypatch.setenv("INSTABOX_API_KEY", "ibk_test")
    monkeypatch.setenv("HQ_INSTABOX_MACHINE", "m_test")
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "ghp_test")
    monkeypatch.setattr(house_mod.House, "_worker_allowed", lambda self, uid: True)


def test_instabox_failure_falls_back_to_github_actions(client, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("HQ_ACTIONS_FALLBACK", "1")  # opt-in since 2 Nov 2026 (GitHub Actions billing)
    h = client.app.state.house
    seen, toks = [], []

    def handler(req: httpx.Request):
        seen.append(req.url.host + req.url.path)
        if req.url.host == "api.instabox.sh":
            if req.url.path.endswith("/exec"):
                toks.append(json.loads(req.content)["stdin"].strip())
                raise httpx.ReadTimeout("exec timed out", request=req)
            return httpx.Response(200, json={"state": "running", "runtime": {"ready": True}})
        return httpx.Response(204)  # GitHub workflow dispatch
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    jid = _queued_job(h)
    ok, why = h._dispatch_github(jid)
    assert ok and "ReadTimeout" in why and "fallback: GitHub answered 204" in why
    assert any(s.startswith("api.github.com") and s.endswith("/render.yml/dispatches") for s in seen)
    assert toks and not h.job_token_ok(jid, toks[0], "read")  # the token the box got is dead after the fallback


def test_instabox_success_does_not_touch_github(client, monkeypatch):
    _env(monkeypatch)
    h = client.app.state.house
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        if req.url.path.endswith("/exec"):
            return httpx.Response(200, json={"exitCode": 0, "stdout": "started\n"})
        return httpx.Response(200, json={"state": "running", "runtime": {"ready": True}})
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    ok, why = h._dispatch_github(_queued_job(h))
    assert ok and why == "InstaBox exec 200" and "api.github.com" not in seen


def test_instabox_failure_without_github_token_reports_it(client, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("HQ_ACTIONS_FALLBACK", "1")
    monkeypatch.delenv("GH_DISPATCH_TOKEN")
    h = client.app.state.house
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503, json={}))))
    assert h._dispatch_github(_queued_job(h)) == (False, "InstaBox answered 503")


@pytest.mark.parametrize("flag", [None, "0", "no"])
def test_actions_fallback_is_off_by_default_and_the_job_fails_clearly(client, monkeypatch, flag):
    """2 Nov 2026: GitHub Actions can't start jobs (billing), so a fallback run would only sit queued. Unless
    HQ_ACTIONS_FALLBACK=1, an InstaBox failure fails the job at once with the box's reason, and GitHub is never called."""
    _env(monkeypatch)
    if flag is None:
        monkeypatch.delenv("HQ_ACTIONS_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("HQ_ACTIONS_FALLBACK", flag)
    h = client.app.state.house
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.host)
        return httpx.Response(503, json={})
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(handler)))
    assert not ib.actions_fallback()
    ok, why = h._dispatch_github(_queued_job(h))
    assert not ok and why == "The InstaBox render worker didn't start (InstaBox answered 503)"
    assert "api.github.com" not in seen
