"""Threads (owner asks, 4 Nov 2026; studio/thread_bin.py). UI = REST = MCP.

Public ids: every thread has a stable thr_ id (128 random bits), never a sequential int in tools or URLs.
Reading earlier work: list_threads / get_thread, own threads only, paginated; start_post / continue_run carry thread_id.
Deleting a thread is like deleting chat history (owner, 4 Nov 23:45): the conversation only, NEVER media (takes, clips,
uploads, posts stay); every thread can be deleted (Make a post and One take, failed ones too); someone else's thread and a
missing one are the same 404; restore brings it back; purge removes the conversation and leaves a tombstone (404 for good);
the 7-day loop purges too, re-checking the deletion time under the lock."""
import json
import re
import time

from studio import thread_bin

from .conftest import agent_token, mcp_call
from .test_take_delete_upload import _rendered_take


def _rows(human):
    return human.get("/api/agent/runs").json()


def _sidebar(human):
    return [x["id"] for x in _rows(human)]


def _tid(human, rid):
    return next(x["thread_id"] for x in _rows(human) if x["id"] == rid)


def _takes(human):
    return [x["id"] for x in human.get("/api/takes").json()]


def _run(st, uid="acc_1", kind="agent", inp=None, res=None, status="ready", updated=None):
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                (uid, kind, json.dumps(inp or {"prompt": "a post"}), status, "[]", json.dumps(res or {}), time.time(), updated or time.time()))


def test_thread_ids_are_public_random_and_stable(human, app):
    st = app.state.store
    a, b = _run(st), _run(st)
    ids = {x["id"]: x["thread_id"] for x in _rows(human)}
    assert all(re.fullmatch(r"thr_[a-z2-7]{26}", ids[r]) for r in (a, b)) and ids[a] != ids[b]
    assert {x["id"]: x["thread_id"] for x in _rows(human)} == ids                                # stable
    assert thread_bin.new_public_id() != thread_bin.new_public_id()


def test_list_threads_and_get_thread_read_earlier_work(human, app):
    st = app.state.store
    old = _run(st, inp={"prompt": "Reel about the Agent Desktop launch"}, updated=time.time() - 86400,
               res={"story": {"headline": "Agent Desktop ships"},
                    "thread": [{"role": "user", "text": f"message {i}", "at": i} for i in range(60)],
                    "packages": [{"channel_id": "ch_li", "service": "linkedin", "format": "reel", "text": "What shipped",
                                  "media": [{"url": "https://hq.test/m/x.mp4"}], "sent": {"mode": "draft"}}]})
    new = _run(st, inp={"prompt": "Carousel on pricing"}, res={"story": {"headline": "Pricing explained"}})
    _run(st, uid="acc_other", inp={"prompt": "theirs: secret plan"})
    page = human.get("/api/threads?limit=1").json()
    assert [t["run_ids"] for t in page["threads"]] == [[new]] and page["next_cursor"]
    t = page["threads"][0]
    assert set(t) >= {"id", "title", "updated_at", "run_ids", "summary"} and t["id"].startswith("thr_")
    page2 = human.get(f"/api/threads?limit=1&cursor={page['next_cursor']}").json()
    assert [x["run_ids"] for x in page2["threads"]] == [[old]]
    allt = human.get("/api/threads?limit=50").json()["threads"]
    assert "theirs" not in json.dumps(allt) and {x["run_ids"][0] for x in allt} == {old, new}
    q = human.get("/api/threads?query=agent desktop").json()["threads"]
    assert [x["run_ids"] for x in q] == [[old]]
    g = human.get(f"/api/threads/{q[0]['id']}").json()
    assert g["id"] == q[0]["id"] and g["runs"][0]["run_id"] == old and len(g["messages"]) == 50 and g["messages_total"] == 60
    assert g["runs"][0]["outputs"]["packages"][0]["text"] == "What shipped" and "continue_run" in g["next"]
    g2 = human.get(f"/api/threads/{q[0]['id']}?cursor={g['next_cursor']}").json()
    assert [m["text"] for m in g2["messages"]] == [f"message {i}" for i in range(50, 60)] and g2["next_cursor"] is None
    assert human.get("/api/threads?cursor=nonsense!!").status_code == 400
    assert human.get("/api/threads?limit=0").json()["threads"]                                   # clamped to 1


def test_foreign_missing_and_int_thread_ids_are_404(human, app):
    st = app.state.store
    theirs = _run(st, uid="acc_other", kind="take", inp={"name": "theirs", "channels": []}, res={"clips": []})
    their_tid = thread_bin.public_id(st, "acc_other", theirs)
    mine = _run(st)
    for tid in (their_tid, "thr_" + "a" * 26, str(mine), "abc", "1", "thr_x"):
        for r in (human.get(f"/api/threads/{tid}"), human.delete(f"/api/threads/{tid}"), human.post(f"/api/threads/{tid}/restore")):
            assert r.status_code == 404, (tid, r.text)
            assert "theirs" not in r.text
    p = human.post("/api/threads/bin/purge", json={"thread_ids": [their_tid]}).json()
    assert p["purged_threads"] == [] and p["refused"]
    assert st.q("SELECT COUNT(*) n FROM thread_deleted WHERE run_id=?", (theirs,))[0]["n"] == 0
    for bad in ({"thread_ids": "1"}, {"thread_ids": [1]}, {"thread_ids": ["x"] * 300}, {}):
        assert human.post("/api/threads/bin/purge", json=bad).status_code == 400


def test_deleting_a_take_thread_never_deletes_media_then_restore_and_purge(human, app, monkeypatch):
    rid, w, _, src = _rendered_take(human, app, monkeypatch)
    st = app.state.store
    tok = next(c["url"] for c in human.get(f"/api/takes/{rid}").json()["clips"] if c.get("url")).rsplit("/m/", 1)[1]
    human.post(f"/api/agent/runs/{rid}/title", json={"title": "My named take thread"})
    before = dict(st.q("SELECT status, result FROM agent_runs WHERE id=?", (rid,))[0])
    tid = _tid(human, rid)
    r = human.delete(f"/api/threads/{tid}?takes=delete")                                # the old option: refused, nothing changes
    assert r.status_code == 400 and "never deletes media" in r.json()["error"] and rid in _sidebar(human)
    out = human.delete(f"/api/threads/{tid}").json()
    assert out["deleted"] and out["thread_id"] == tid and out["media"] == "kept" and out["in_bin_days"] == 7
    assert rid not in _sidebar(human) and rid in _takes(human)                           # gone from the sidebar, take stays
    assert w.get(f"/m/{tok}").status_code == 200
    assert dict(st.q("SELECT status, result FROM agent_runs WHERE id=?", (rid,))[0]) == before  # a delete never touches the run
    assert tid not in json.dumps(human.get("/api/threads").json())
    assert human.get(f"/api/threads/{tid}").status_code == 404
    b = human.get("/api/threads/bin").json()
    assert [(x["thread_id"], x["kind"], x["days_left"]) for x in b["threads"]] == [(tid, "take", 7)]
    assert human.delete(f"/api/threads/{tid}").status_code == 404                        # already in the Bin
    r = human.post(f"/api/threads/{tid}/restore").json()
    assert r["restored"] and rid in _sidebar(human) and _tid(human, rid) == tid
    assert human.post(f"/api/threads/{tid}/restore").status_code == 404
    human.delete(f"/api/threads/{tid}")
    p = human.post("/api/threads/bin/purge", json={"thread_ids": [tid]}).json()
    assert p["purged_threads"] == [tid] and not p["refused"]
    assert rid not in _sidebar(human) and rid in _takes(human) and w.get(f"/m/{tok}").status_code == 200
    assert st.q("SELECT COUNT(*) n FROM media WHERE token=?", (tok,))[0]["n"] == 1        # the clip file is still there
    assert app.state.house.get_media(src) is not None                                     # and so is the upload
    assert st.q("SELECT COUNT(*) n FROM chat_titles WHERE run_id=?", (rid,))[0]["n"] == 0
    assert human.get("/api/threads/bin").json()["threads"] == []
    # the tombstone: the id stays a 404 for good, and the thread never comes back
    for r in (human.get(f"/api/threads/{tid}"), human.delete(f"/api/threads/{tid}"), human.post(f"/api/threads/{tid}/restore")):
        assert r.status_code == 404
    assert rid not in _sidebar(human)
    assert app.state.house.media_audit()["unique_files_missing"] == 0


def test_make_a_post_and_failed_threads_can_be_deleted_and_keep_their_media(human, app):
    st = app.state.store
    post = _run(st, inp={"prompt": "Carousel on pricing"},
                res={"story": {"headline": "Pricing explained"}, "thread": [{"role": "user", "text": "make a carousel", "at": 1}],
                     "thread_summary": {"summary": "a carousel"},
                     "packages": [{"channel_id": "ch_li", "service": "linkedin", "format": "carousel", "text": "Pricing",
                                   "media": [{"url": "https://hq.test/m/slide1.png"}]}],
                     "media": [{"url": "https://hq.test/m/slide1.png"}]})
    failed = _run(st, inp={"prompt": "a reel that broke"}, status="failed", res={"error": "render failed"})
    failed_take = _run(st, kind="take", inp={"name": "Broken upload", "channels": []}, status="failed", res={"error": "no audio"})
    for rid in (post, failed, failed_take):
        tid = _tid(human, rid)
        r = human.delete(f"/api/threads/{tid}")
        assert r.status_code == 200 and r.json()["deleted"], r.text
        assert rid not in _sidebar(human)
    kinds = sorted(x["kind"] for x in human.get("/api/threads/bin").json()["threads"])
    assert kinds == ["post", "post", "take"]
    house = app.state.house
    assert house.threads.purge_expired() == 0                                              # not 7 days yet
    assert house.threads.purge_expired(days=0) == 3
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (post,))[0]["result"])
    assert res["thread"] == [] and "thread_summary" not in res                            # the conversation is gone ...
    assert res["packages"][0]["media"] and res["media"] and res["story"]                  # ... the post and its media stay
    assert st.q("SELECT COUNT(*) n FROM agent_runs WHERE id IN (?,?,?)", (post, failed, failed_take))[0]["n"] == 3


def test_a_running_post_thread_is_purged_only_once_it_ends(human, app):
    st, house = app.state.store, app.state.house
    rid = _run(st, status="running", res={"thread": [{"role": "user", "text": "still writing", "at": 1}]})
    human.delete(f"/api/threads/{_tid(human, rid)}")
    assert house.threads.purge_expired(days=0) == 0                                        # a running agent still writes it
    st.x("UPDATE agent_runs SET status='ready' WHERE id=?", (rid,))
    assert house.threads.purge_expired(days=0) == 1


def test_the_sweep_rechecks_the_deletion_time_under_the_lock(human, app):
    # the 7-day sweep read an old deletion time, then the thread was restored and deleted again: it isn't purged (Greptile)
    st, house = app.state.store, app.state.house
    rid = _run(st)
    human.delete(f"/api/threads/{_tid(human, rid)}")
    assert house.threads._purge_one("acc_1", rid, cut=time.time() - 86400) is False           # deleted after the cutoff
    assert len(human.get("/api/threads/bin").json()["threads"]) == 1
    assert house.threads._purge_one("acc_1", rid, cut=time.time() + 1) is True


def test_get_thread_hides_what_list_threads_hides(human, app, monkeypatch):
    st = app.state.store
    tidied = _run(st, status="archived")
    rid, _, _, _ = _rendered_take(human, app, monkeypatch)
    tid = {x["id"]: x["thread_id"] for x in _rows(human)}[rid]
    tidied_tid = thread_bin.public_id(st, "acc_1", tidied)
    assert human.delete(f"/api/takes/{rid}").status_code == 200                              # the take Bin, not the thread Bin
    assert human.get(f"/api/threads/{tid}").status_code == 404
    assert human.get(f"/api/threads/{tidied_tid}").status_code == 404
    listed = json.dumps(human.get("/api/threads").json())
    assert tid not in listed and tidied_tid not in listed


def test_purge_looks_for_optional_tables_instead_of_catching_a_failed_delete(app):
    st = app.state.store
    assert thread_bin._has_table(st, "agent_runs") and not thread_bin._has_table(st, "no_such_table_here")


def test_log_tail_redacts_presigned_signatures(monkeypatch):
    from studio import render_worker

    class T:
        buf = ("::add-mask::0123abcd4567ef\n"
               "GET https://r2.test/u/v.mp4?X-Amz-Credential=AK%2F1&X-Amz-Signature=0123abcd4567ef&x=1 failed; jt_abcdefghijkl\n")
    monkeypatch.setattr(render_worker, "_tails", [T()])
    out = render_worker.log_tail()
    assert "0123abcd4567ef" not in out and "X-Amz-Signature=[redacted]" in out and "jt_[redacted]" in out


def test_deleting_the_account_removes_its_thread_rows(human, app):
    st = app.state.store
    rid = _run(st)
    human.delete(f"/api/threads/{_tid(human, rid)}")
    st.delete_user("acc_1")
    assert st.q("SELECT COUNT(*) n FROM thread_deleted WHERE user_id='acc_1'")[0]["n"] == 0
    assert st.q("SELECT COUNT(*) n FROM thread_ids WHERE user_id='acc_1'")[0]["n"] == 0


def test_mcp_thread_tools_start_post_thread_id_instructions_and_docs(human, app, monkeypatch):
    rid, _, _, _ = _rendered_take(human, app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    listed = {t["name"]: t for t in r.json()["result"]["tools"]}
    tools = {"list_threads", "get_thread", "delete_thread", "restore_thread", "list_thread_bin", "purge_thread_bin"}
    assert tools <= set(listed)
    assert "takes" not in listed["delete_thread"]["inputSchema"]["properties"]              # no "delete its take" option
    assert "never deleted" in listed["delete_thread"]["description"].lower()
    _, lt = mcp_call(human, tok, "list_threads", {"query": "One take"})
    tid = next(t["id"] for t in lt["threads"] if t["run_ids"] == [rid])
    _, g = mcp_call(human, tok, "get_thread", {"thread_id": tid})
    assert g["runs"][0]["kind"] == "take" and g["runs"][0]["outputs"]["clips"]
    res, msg = mcp_call(human, tok, "delete_thread", {"thread_id": tid, "takes": "delete"})
    assert res["isError"] and "never deletes media" in msg
    _, out = mcp_call(human, tok, "delete_thread", {"thread_id": tid})
    assert out["deleted"] and rid in _takes(human) and rid not in _sidebar(human)
    _, b = mcp_call(human, tok, "list_thread_bin")
    assert [x["thread_id"] for x in b["threads"]] == [tid]
    _, out = mcp_call(human, tok, "restore_thread", {"thread_id": tid})
    assert out["restored"] and rid in _sidebar(human)
    res, msg = mcp_call(human, tok, "get_thread", {"thread_id": rid})                    # an int is never a thread id
    assert res["isError"] and "No such thread" in msg
    # start_post and continue_run answer with the thread's public id
    _, sp = mcp_call(human, tok, "start_post", {"title": "Launch post", "brief": "the launch", "user_asked": True, "reason": "asked"})
    assert sp.get("run_id") and re.fullmatch(r"thr_[a-z2-7]{26}", sp.get("thread_id", "")), sp
    _, cr = mcp_call(human, tok, "continue_run", {"run_id": sp["run_id"], "reason": "same post"})
    assert cr.get("thread_id") == sp["thread_id"], cr
    # the instructions tell the app to read earlier threads first
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}})
    ins = json.dumps(r.json())
    assert "list_threads" in ins and "get_thread" in ins
    doc = open("docs/MCP_TOOLS.md").read()
    assert all(f"`{n}`" in doc for n in tools)
    from studio import mcp_parity
    assert {"list_threads", "get_thread", "delete_thread", "restore_thread"} <= set(mcp_parity.PAGES["make_post"]["tools"])
    assert tools <= set(mcp_parity.PAGES["one_take"]["tools"])



def test_deleted_threads_never_use_up_the_sidebar_s_slots(human, app):
    st = app.state.store
    old = _run(st, inp={"prompt": "an older live thread"}, updated=time.time() - 3600)
    newer = [_run(st) for _ in range(5)]
    for rid in newer:
        human.delete(f"/api/threads/{_tid(human, rid)}")
    assert old in [x["id"] for x in app.state.house.runs(__import__("studio.actions", fromlist=["Actor"]).Actor("acc_1"), limit=3)]


def test_the_sweep_clears_a_conversation_a_late_write_brought_back(human, app):
    st, house = app.state.store, app.state.house
    rid = _run(st, res={"thread": [{"role": "user", "text": "old", "at": 1}]})
    human.delete(f"/api/threads/{_tid(human, rid)}")
    assert house.threads.purge_expired(days=0) == 1
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps({"thread": [{"role": "user", "text": "old", "at": 1}]}), rid))
    house.threads.purge_expired(days=0)
    assert json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])["thread"] == []
