"""One take run #80 (live, 6 Nov 2026): "Interrupted, Stopped: the server restarted" while the video worker was still
reading the video. The chat agent's cut-off-run sweep treated a take silent for 90 s as orphaned; a take's heavy work runs
on the worker, which calls back whatever the web process does. Pins: the sweep leaves takes with an open worker job alone
and a late analysis still lands; an in-process job whose process died is run again; a take with nothing open and silent
for IDLE_S is interrupted with Try again; Stop cancels the open job (late worker calls are refused) and keeps what's made;
Try again carries on (render what's missing, else read the video again). UI = REST = MCP."""
import base64
import json
import time

from fastapi.testclient import TestClient

from studio import agent_brain, take_control

from .conftest import agent_token, mcp_call
from .test_take import ANALYSIS, _fake_mp4_meta

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42"


def _github_take(human, app, monkeypatch):
    house, sent = app.state.house, []

    def dispatch(jid, workflow="", inputs=None, ttl=1800):
        sent.append((jid, house.issue_job_token(jid, ttl)))
        return True, ""
    monkeypatch.setattr(house, "_dispatch_github", dispatch)
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "name": "My talk"}).json()
    return t["id"], sent, TestClient(app, base_url="https://hq.test")


def test_a_take_waiting_on_the_worker_is_never_marked_interrupted(human, app, monkeypatch):
    rid, sent, w = _github_take(human, app, monkeypatch)
    st, h = app.state.store, app.state.house
    # the worker has been reading the video for 10 minutes; the web process restarted meanwhile (no live boot but ours)
    st.x("UPDATE agent_runs SET updated=? WHERE id=?", (time.time() - 600, rid))
    st.x("INSERT INTO agent_procs(boot,seen) VALUES(?,?)", ("deadprocess9", time.time() - 600))
    assert rid not in h.brain.interrupt_orphans()
    assert human.get(f"/api/takes/{rid}").json()["status"] == "running"
    jid, jt = sent[0]
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": ANALYSIS}).json()["ok"]
    t = human.get(f"/api/takes/{rid}").json()
    assert t["status"] == "rendering" and t["clips"]


def test_a_take_with_nothing_open_and_silent_is_interrupted_then_try_again_renders_whats_missing(human, app, monkeypatch):
    rid, sent, w = _github_take(human, app, monkeypatch)
    st, h = app.state.store, app.state.house
    jid, jt = sent[0]
    w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": ANALYSIS})
    rjid, rjt = sent[1]
    w.post(f"/api/worker/jobs/{rjid}/clips/c1", headers={"x-job-token": rjt},
           json={"data": base64.b64encode(MP4_HEAD + b"c1").decode(), "cover": base64.b64encode(b"\xff\xd8\xffcov").decode(),
                 "meta": _fake_mp4_meta()})
    # the render job vanished without a callback (closed by hand here) and the take sat silent past IDLE_S
    st.x("UPDATE render_jobs SET status='done' WHERE id=?", (rjid,))
    st.x("UPDATE agent_runs SET updated=? WHERE id=?", (time.time() - take_control.IDLE_S - 5, rid))
    h.brain.interrupt_orphans()
    t = human.get(f"/api/takes/{rid}").json()
    assert t["status"] == "interrupted"
    assert any(s["detail"] == take_control.CUT_OFF for s in t["steps"])
    out = human.post(f"/api/takes/{rid}/retry").json()
    assert out["retried"] == "render" and out["status"] == "rendering"
    nj, njt = sent[2]
    spec = json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (nj,))[0]["spec"])
    assert [c["id"] for c in spec["clips"]] == ["c2", "c3"]  # c1 is kept, not rendered again
    assert human.post(f"/api/takes/{rid}/retry").status_code == 409  # it's running again


def test_stop_cancels_the_job_keeps_the_take_and_try_again_reads_the_video_again(human, app, monkeypatch):
    rid, sent, w = _github_take(human, app, monkeypatch)
    st = app.state.store
    jid, jt = sent[0]
    out = human.post(f"/api/takes/{rid}/stop").json()
    assert out["status"] == "stopped" and out["cancelled_jobs"] == 1
    assert any(s["detail"] == take_control.STOPPED_BY_YOU for s in out["steps"])
    assert st.q("SELECT status FROM render_jobs WHERE id=?", (jid,))[0]["status"] == "cancelled"
    late = w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": ANALYSIS})
    assert late.status_code == 403  # the worker's token is burned: nothing lands on a stopped take
    assert human.get(f"/api/takes/{rid}").json()["status"] == "stopped"
    assert human.post(f"/api/takes/{rid}/stop").status_code == 409
    again = human.post(f"/api/takes/{rid}/retry").json()
    assert again["retried"] == "analyze" and again["status"] == "running"
    nj, njt = sent[1]
    spec = json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (nj,))[0]["spec"])
    assert spec["kind"] == "take_analyze" and spec["take"] == rid and spec["source"]
    assert w.post(f"/api/worker/jobs/{nj}", headers={"x-job-token": njt}, json={"analysis": ANALYSIS}).json()["ok"]
    assert human.get(f"/api/takes/{rid}").json()["clips"]


def test_someone_elses_take_cant_be_stopped(human, app):
    st = app.state.store
    theirs = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                  ("acc_other", "take", json.dumps({"name": "theirs", "channels": []}), "running", "[]", "{}", time.time(), time.time()))
    assert human.post(f"/api/takes/{theirs}/stop").status_code == 404
    assert human.post(f"/api/takes/{theirs}/retry").status_code == 404
    assert human.post("/api/takes/999999/stop").status_code == 404
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (theirs,))[0]["status"] == "running"


def test_a_local_job_cut_off_by_a_restart_runs_again_then_gives_up(human, app, monkeypatch):
    house, st = app.state.house, app.state.store
    ran = []
    monkeypatch.setattr(house.takes, "_mode", lambda: "local")
    monkeypatch.setattr(house.takes, "_local_job", lambda jid: ran.append(jid))
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    rid = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()["id"]
    jid = ran[0]
    spec = json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))[0]["spec"])
    assert spec["_boot"] == agent_brain.BOOT
    # its process is gone
    spec["_boot"] = "deadprocess9"
    st.x("UPDATE render_jobs SET spec=? WHERE id=?", (json.dumps(spec), jid))
    st.x("INSERT INTO agent_procs(boot,seen) VALUES(?,?)", ("deadprocess9", time.time() - 600))
    house.brain.interrupt_orphans()
    assert ran[-1] == jid and len(ran) == 2
    assert json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))[0]["spec"])["_retries"] == 1
    for _ in range(take_control.LOCAL_RETRIES + 1):
        s = json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))[0]["spec"])
        s["_boot"] = "deadprocess9"
        st.x("UPDATE render_jobs SET spec=? WHERE id=?", (json.dumps(s), jid))
        house.brain.interrupt_orphans()
    assert human.get(f"/api/takes/{rid}").json()["status"] == "interrupted"
    assert st.q("SELECT status FROM render_jobs WHERE id=?", (jid,))[0]["status"] == "cancelled"


def test_mcp_stop_take_and_retry_take(human, app, monkeypatch):
    rid, sent, w = _github_take(human, app, monkeypatch)
    tok = agent_token(human)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert {"stop_take", "retry_take"} <= {t["name"] for t in r.json()["result"]["tools"]}
    _, out = mcp_call(human, tok, "stop_take", {"take_id": rid})
    assert out["status"] == "stopped", out
    _, out = mcp_call(human, tok, "retry_take", {"take_id": rid})
    assert out["status"] == "running", out


def test_ui_has_stop_and_try_again():
    from pathlib import Path
    st = Path(__file__).parents[2] / "studio" / "static"
    html, js = (st / "index.html").read_text(), (st / "take.js").read_text()
    assert 'data-testid="take-stop"' in html and 'data-testid="take-retry"' in html
    assert "/api/takes/${encodeURIComponent(t.id)}/${which}" in js and 'interrupted: "Interrupted"' in js


def test_reading_a_take_whose_worker_is_still_busy_never_fails_it(human, app, monkeypatch):
    """A long video reads for more than STALE_RUN (15 min): the worker heartbeats its job, not the run row."""
    rid, sent, w = _github_take(human, app, monkeypatch)
    st = app.state.store
    st.x("UPDATE agent_runs SET updated=? WHERE id=?", (time.time() - 3600, rid))
    assert human.get(f"/api/takes/{rid}").json()["status"] == "running"
