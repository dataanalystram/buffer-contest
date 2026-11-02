"""The video worker never leaves a job hanging (2 Nov 2026: job 15, a 404 source link on the InstaBox box, left only a
traceback in /data/logs/job-15.log). ANY exception reports status=failed with a short human message and the log tail on
the job's own token; a heartbeat keeps the job alive; the app's watchdog fails a job that goes silent."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import house as house_mod
from studio import render_worker

from .test_take import _public_hosts

BAD = "https://example.com/not-a-video"


# ---------------------------------------------------------------- the worker side (no app)
def _err(status, url):
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(status, request=req))


def test_friendly_messages():
    assert render_worker.friendly(_err(404, BAD), "https://hq.test") == "Couldn't download the video: the link returned 404"
    assert "couldn't talk to HuhQueue (HTTP 403)" in render_worker.friendly(_err(403, "https://hq.test/api/worker/jobs/1"),
                                                                             "https://hq.test")
    assert render_worker.friendly(httpx.ConnectError("x")) == "Couldn't download the video: the link didn't answer"
    assert render_worker.friendly(RuntimeError("The video is over 1500 MB.")) == "The video is over 1500 MB."
    assert render_worker.friendly(KeyError("spec")) == "The video worker hit an error (KeyError: 'spec')"
    assert "stopped before it finished" in render_worker.friendly(KeyboardInterrupt())


def _mock_app(monkeypatch, spec, urls, posts, heartbeats=None, fail_posts=0):
    left = {"n": fail_posts}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "example.com":
            return httpx.Response(404, request=req)
        if req.url.path.endswith("/heartbeat"):
            (heartbeats if heartbeats is not None else []).append(req.headers.get("x-job-token"))
            return httpx.Response(200, json={"ok": True})
        if req.method == "GET":
            return httpx.Response(200, json={"id": 15, "spec": spec, "urls": urls})
        if left["n"]:
            left["n"] -= 1
            return httpx.Response(503, json={"error": "asleep"})
        posts.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True})
    real = httpx.Client
    monkeypatch.setattr(render_worker.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))
    monkeypatch.setattr("studio.drafter._public", lambda h: True)  # hermetic: no DNS for example.com
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setenv("JOB_TOKEN", "jt_secretsecretsecret")


@pytest.mark.parametrize("spec", [{"kind": "take_analyze"}, {"kind": "transcribe"}, {"kind": "brand_video", "kit": {}, "params": {}}])
def test_a_bad_source_link_reports_failed_with_message_and_log_tail(monkeypatch, spec):
    posts, beats = [], []
    _mock_app(monkeypatch, spec, {"source": BAD, "logo": ""}, posts, beats)
    assert render_worker.main(["--job", "15", "--app", "https://hq.test"]) == 1  # the run fails, after reporting
    assert len(posts) == 1, posts
    p = posts[0]
    assert p["status"] == "failed" and p["error"] == "Couldn't download the video: the link returned 404"
    assert "HTTPStatusError" in p["log_tail"] and "Traceback" in p["log_tail"]
    assert "jt_secret" not in json.dumps(p)  # the token never travels in the report
    assert beats == ["jt_secretsecretsecret"] or beats == []  # the heartbeat thread used the same job token


def test_any_exception_even_before_the_job_kind_is_known_is_reported(monkeypatch):
    posts = []
    _mock_app(monkeypatch, {}, {}, posts)  # spec without "kind": a KeyError deep in the dispatch
    assert render_worker.main(["--job", "15", "--app", "https://hq.test"]) == 1
    assert posts[0]["status"] == "failed" and "KeyError" in posts[0]["error"]


def test_the_report_retries_while_the_app_wakes_up(monkeypatch):
    posts = []
    _mock_app(monkeypatch, {"kind": "take_analyze"}, {"source": BAD}, posts, fail_posts=2)  # Render cold start: 503, 503, 200
    assert render_worker.main(["--job", "15", "--app", "https://hq.test"]) == 1
    assert len(posts) == 1 and posts[0]["status"] == "failed"


def test_heartbeat_beats_until_stopped(monkeypatch):
    seen = []
    real = httpx.Client
    monkeypatch.setattr(render_worker.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda r: seen.append((r.url.path, r.headers.get("x-job-token"))) or httpx.Response(200, json={"ok": True}))))
    with render_worker.Heartbeat("https://hq.test", {"x-job-token": "jt_t"}, 9, every=0.01):
        t0 = time.time()
        while len(seen) < 3 and time.time() - t0 < 5:
            time.sleep(0.01)
    n = len(seen)
    time.sleep(0.05)
    assert n >= 3 and len(seen) <= n + 1 and seen[0] == ("/api/worker/jobs/9/heartbeat", "jt_t")


def test_the_lock_is_taken_after_the_heartbeat(monkeypatch, tmp_path):
    order = []
    monkeypatch.setenv("HQ_LOCK", str(tmp_path / "hq.lock"))
    monkeypatch.setattr(render_worker.Heartbeat, "__enter__", lambda self: order.append("beat") or self)
    real_lock = render_worker._lock
    monkeypatch.setattr(render_worker, "_lock", lambda: order.append("lock") or real_lock())
    _mock_app(monkeypatch, {"kind": "take_analyze"}, {"source": BAD}, [])
    render_worker.main(["--job", "15", "--app", "https://hq.test"])
    assert order == ["beat", "lock"]


# ---------------------------------------------------------------- end to end against the real app routes
def _route(monkeypatch, app_client):
    """The worker's httpx goes to the app (TestClient transport) for hq.test and gets a 404 for the bad link."""
    appt = app_client._transport
    real = httpx.Client

    def handler(req):
        return appt.handle_request(req) if req.url.host == "hq.test" else httpx.Response(404, request=req)
    monkeypatch.setattr(render_worker.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **{k: v for k, v in kw.items() if k != "transport"}))


def _take_job(human, app, monkeypatch):
    _public_hosts(monkeypatch)
    house = app.state.house
    toks = {}
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800:
                        (toks.setdefault(jid, house.issue_job_token(jid, ttl)), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    t = human.post("/api/takes", json={"url": BAD, "owns": True}).json()
    assert t["status"] == "running"
    jid, jt = next(iter(toks.items()))
    return t, jid, jt


def test_job_15_end_to_end_the_take_fails_with_the_human_message(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    _route(monkeypatch, human)
    monkeypatch.setenv("JOB_TOKEN", jt)
    assert render_worker.main(["--job", str(jid), "--app", "https://hq.test"]) == 1
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "failed" and t["steps"][1]["detail"] == "Couldn't download the video: the link returned 404"
    row = app.state.house.store.q("SELECT status, error, result FROM render_jobs WHERE id=?", (jid,))[0]
    assert row["status"] == "failed" and "404" in row["error"]
    tail = json.loads(row["result"])["log_tail"]
    assert "HTTPStatusError" in tail and jt not in tail
    w = TestClient(app, base_url="https://hq.test")
    assert w.post(f"/api/worker/jobs/{jid}/heartbeat", headers={"x-job-token": jt}).status_code == 403  # token burned


def test_heartbeat_route_needs_the_token_and_keeps_the_job_alive(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    h = app.state.house
    w = TestClient(app, base_url="https://hq.test")
    assert w.post(f"/api/worker/jobs/{jid}/heartbeat").status_code == 403
    assert w.post(f"/api/worker/jobs/{jid}/heartbeat", headers={"x-job-token": "jt_wrong"}).status_code == 403
    h.store.x("UPDATE render_jobs SET updated=? WHERE id=?", (time.time() - 3600, jid))
    for _ in range(3):  # any number of beats; none of them spends the token
        assert w.post(f"/api/worker/jobs/{jid}/heartbeat", headers={"x-job-token": jt}).json() == {"ok": True}
    assert h.sweep_stale_jobs(force=True) == []  # alive: the watchdog leaves it alone
    assert w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).status_code == 200  # read still works once


def test_watchdog_fails_a_silent_take_and_kills_its_token(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    h = app.state.house
    h.store.x("UPDATE render_jobs SET updated=? WHERE id=?", (time.time() - (house_mod.JOB_STALE_MIN * 60 + 5), jid))
    assert h.sweep_stale_jobs(force=True) == [jid]
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "failed" and "stopped responding" in t["steps"][1]["detail"]
    assert h.store.q("SELECT status FROM render_jobs WHERE id=?", (jid,))[0]["status"] == "failed"
    w = TestClient(app, base_url="https://hq.test")
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"meta": {}}).status_code == 403  # a late worker can't write
    assert h.sweep_stale_jobs(force=True) == []  # once


def test_watchdog_runs_lazily_when_the_take_is_read(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    h = app.state.house
    h._swept = 0.0
    h.store.x("UPDATE render_jobs SET updated=? WHERE id=?", (time.time() - 7200, jid))
    assert human.get(f"/api/takes/{t['id']}").json()["status"] == "failed"


def test_watchdog_leaves_local_and_fresh_jobs_alone(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    h = app.state.house
    old = time.time() - 7200
    local = h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                      ("u_x", 0, json.dumps({"kind": "brand_video"}), "queued", old, old))  # in-process job: no token, no heartbeat
    assert h.sweep_stale_jobs(force=True) == []  # the dispatched take is fresh, the local job is not a worker job
    assert h.store.q("SELECT status FROM render_jobs WHERE id=?", (local,))[0]["status"] == "queued"


def test_watchdog_fails_an_orphaned_render_job(app):
    h = app.state.house
    old = time.time() - 7200
    h.store.x("INSERT INTO users(id) VALUES(?) ON CONFLICT DO NOTHING", ("u_o",))
    jid = h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                    ("u_o", 0, json.dumps({"kind": "slideshow", "_job_token": {"claimed": True, "hash": "x"}}), "queued", old, old))
    assert h.sweep_stale_jobs(force=True) == [jid]
    row = h.store.q("SELECT status, error FROM render_jobs WHERE id=?", (jid,))[0]
    assert row["status"] == "failed" and "no heartbeat" in row["error"]


def test_status_failed_without_an_error_still_fails_the_job(human, app, monkeypatch):
    t, jid, jt = _take_job(human, app, monkeypatch)
    w = TestClient(app, base_url="https://hq.test")
    w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"status": "failed"})
    assert human.get(f"/api/takes/{t['id']}").json()["status"] == "failed"
