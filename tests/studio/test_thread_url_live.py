"""Threads (owner asks, 5 Nov 2026): a thread has its own address (/threads/thr_...), so a reload stays on it; an open
thread refreshes live while its run works without adding database egress (an unchanged busy run answers 304, read from
md5s, never the run's text); the poll stops when the view changes or the tab is hidden; the thread bar never overlaps."""
import json
import pathlib
import time

from fastapi.testclient import TestClient

from studio import thread_bin

ST = pathlib.Path(__file__).resolve().parents[2] / "studio" / "static"


def _run(st, uid="acc_1", status="running", res=None, updated=None):
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (uid, "agent", json.dumps({"prompt": "a post"}), status, "[]", json.dumps(res or {"thread": []}), time.time(),
                 time.time() if updated is None else updated))


def test_a_thread_address_serves_the_app_when_signed_in_and_redirects_otherwise(app, human):
    tid = thread_bin.public_id(app.state.store, "acc_1", _run(app.state.store, status="ready"))
    r = human.get(f"/threads/{tid}", follow_redirects=False)
    assert r.status_code == 200 and 'id="runView"' in r.text
    # only a public thread id: a run number or anything else opens Make a post
    for bad in ("12", "thr_short", "thr_" + "A" * 26, "thr_" + "a" * 27):
        r = human.get(f"/threads/{bad}", follow_redirects=False)
        assert r.status_code == 302, (bad, r.status_code)
        assert r.headers["location"] == "/?tab=agent", bad
    anon = TestClient(app, base_url="https://hq.test")
    r = anon.get(f"/threads/{tid}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"


def test_a_run_answer_names_its_thread_id_for_the_address_bar(app, human):
    rid = _run(app.state.store, status="ready")
    d = human.get(f"/api/agent/runs/{rid}").json()
    assert d["thread_id"] == thread_bin.public_id(app.state.store, "acc_1", rid)


def test_every_run_answer_names_its_thread_id_not_only_the_poll(app, human):
    # the e2e caught it (#124): a run shown from a POST answer (start, reply, resume) had no thread_id, so no address
    rid = _run(app.state.store, status="ready")
    from studio.house import Actor
    assert app.state.house.run(Actor("acc_1"), rid)["thread_id"] == thread_bin.public_id(app.state.store, "acc_1", rid)
    assert "H.run.id === r.id && H.run.thread_id) r.thread_id = H.run.thread_id" in (ST / "house.js").read_text()


def test_an_unchanged_busy_run_answers_304_with_no_body_and_a_change_answers_in_full(app, human):
    st = app.state.store
    rid = _run(st)
    r = human.get(f"/api/agent/runs/{rid}")
    tag = r.headers.get("etag")
    assert r.status_code == 200 and tag and r.json()["status"] == "running"
    r2 = human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": tag})
    assert r2.status_code == 304 and r2.content == b""
    assert human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": "W/" + tag}).status_code == 304   # gzip-weakened
    # the run moved on: the next poll gets it, with a new tag
    st.x("UPDATE agent_runs SET result=?, updated=? WHERE id=?", (json.dumps({"thread": [{"role": "agent", "text": "Drafting", "at": 1}]}), time.time(), rid))
    r3 = human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": tag})
    assert r3.status_code == 200 and r3.headers["etag"] != tag and r3.json()["result"]["thread"][0]["text"] == "Drafting"
    # an MCP step on the run (another table) changes the tag too
    from studio import mcp_runs
    mcp_runs.ensure(st)
    tag3 = r3.headers["etag"]
    st.x("INSERT INTO mcp_calls(user_id,agent_id,run_id,tool,label,detail,status,ms,client,app,token_name,model_hint,at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
         ("acc_1", 1, rid, "render_carousel", "Render", "", "done", 5, "claude", "Claude", "t", "", time.time()))
    assert human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": tag3}).status_code == 200
    # finished: no validator, always the full answer (media state and the rest are read fresh)
    st.x("UPDATE agent_runs SET status='ready' WHERE id=?", (rid,))
    r4 = human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": "*"})
    assert r4.status_code == 200 and "etag" not in r4.headers and r4.json()["status"] == "ready"


def test_a_stale_busy_run_takes_the_full_read_so_it_is_reported_stopped(app, human):
    rid = _run(app.state.store, updated=time.time() - 86400)
    r = human.get(f"/api/agent/runs/{rid}", headers={"If-None-Match": "*"})
    assert r.status_code == 200 and r.json()["status"] == "failed" and "etag" not in r.headers


def test_someone_elses_or_a_missing_run_is_404_even_with_a_validator(app, human):
    theirs = _run(app.state.store, uid="acc_other")
    for rid in (theirs, 999999):
        for h in ({}, {"If-None-Match": "*"}):
            r = human.get(f"/api/agent/runs/{rid}", headers=h)
            assert r.status_code == 404, (rid, h, r.status_code)
    assert app.state.house.run_tag(__import__("studio.house", fromlist=["Actor"]).Actor("acc_1"), theirs) is None


def test_the_live_poll_sends_its_tag_backs_off_stops_when_hidden_and_aborts_on_view_change():
    js = (ST / "house.js").read_text()
    assert 'o.headers["If-None-Match"] = tag' in js and "r.status === 304" in js
    assert "const POLL_MIN = 1400, POLL_MAX = 10000;" in js and "Math.min(POLL_MAX, Math.round(pace * 1.6))" in js
    assert "if (document.hidden) return later(POLL_MIN);" in js and "if (document.hidden) { H.unwake = wakeUp(); return; }" in js
    assert "const ac = H.abort = new AbortController();" in js and "H.abort.abort()" in js and "ac.signal" in js
    assert "window.stopWatch = stopWatch; window.resumeWatch = resumeWatch;" in js
    assert "if (inflight) { again = true; return; }" in js and "inflight = false;" in js   # one poll request at a time
    app = (ST / "app.js").read_text()
    assert 'if (from === "agent" && name !== "agent" && window.stopWatch) window.stopWatch();' in app
    assert "THREAD_PATH = /^\\/threads\\/(thr_[a-z2-7]{26})\\/?$/" in app and "openThreadAt(tm[1])" in app
    for f in ("chat.js", "makepost.js"):  # every place that stopped the poll by hand now aborts its request too
        assert "clearTimeout(H.poll); H.watching = null" not in (ST / f).read_text()
        assert "clearTimeout(H_STATE.poll)" not in (ST / f).read_text()


def test_the_thread_bar_tracks_the_header_and_post_media_sits_below_the_bar():
    chat = (ST / "chat.js").read_text()
    assert 'new ResizeObserver(top).observe(topEl, { box: "border-box" })' in chat and 'new ResizeObserver(barH).observe(bar, { box: "border-box" })' in chat
    assert "top:calc(var(--hc-top,64px) + var(--hc-bar,72px) + 8px)" in (ST / "adaptive.css").read_text()
    assert "92%,transparent" not in (ST / "chat.css").read_text()
