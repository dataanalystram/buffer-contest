"""One take placements follow Buffer (owner, 7 Nov 2026: "I drafted it from the One take and also deleted the draft ... the
draft shown on the UI is not turned off ... make sure everything is in sync so it doesn't show wrong information").

Pins: GET /api/takes/<id> and the Your takes list re-check every clip's placements against the account's Buffer posts,
read from the SAME cached post lists the Planner uses (no extra Buffer request inside POSTS_TTL). A draft deleted in Buffer
becomes status "gone" (hidden, not counted as placed); a draft that was scheduled, sent or failed in Buffer shows that; a
time moved in Buffer moves the label; a post waiting for approval follows its approval. When Buffer can't be read, or a
post list is full (absence proves nothing), nothing changes."""
import json
import time
from pathlib import Path

from .test_take_run_view import _clip, _take

ST = Path(__file__).parents[2] / "studio" / "static"


def _placement(pid, status="draft", **kw):
    return {"clip_id": "c", "channel_id": "ch_x", "service": "youtube", "local": "Sat 19:58", "channel_name": "Huhbyte",
            "at": "2030-01-05T19:58:00Z", "mode": "draft", "status": status, "buffer_post_id": pid,
            "timezone": "Europe/London", **kw}


def _posts_calls(fake):
    return sum(1 for q, _v, _h in fake.calls if "posts(" in q)


def _takes(human):
    j = human.get("/api/takes").json()
    return j["takes"] if isinstance(j, dict) else j


def _saved(app, rid):
    return json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])


def test_a_draft_deleted_in_buffer_is_no_longer_shown_or_counted(human, app, fake):
    fake.posts += [{"id": "bp_keep", "text": "kept", "status": "draft", "dueAt": "2030-01-06T20:11:00.000Z", "channelId": "ch_x"}]
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("bp_gone")]),
                                  _clip("c2", "m2", placements=[_placement("bp_keep", at="2030-01-06T20:11:00.000Z")])])
    before = next(t for t in _takes(human) if t["id"] == rid)
    assert before["placed"] == 1  # the list already re-checked: the deleted draft isn't counted
    t = human.get(f"/api/takes/{rid}").json()
    by = {c["id"]: c["placements"] for c in t["clips"]}
    assert by["c1"][0]["status"] == "gone" and by["c1"][0]["gone"] == "deleted"
    assert by["c2"][0]["status"] == "draft"
    saved = _saved(app, rid)  # stored, so every surface (MCP, list, page) agrees
    assert saved["clips"][0]["placements"][0]["status"] == "gone"


def test_scheduled_sent_failed_and_moved_in_buffer_show_as_they_are(human, app, fake):
    fake.posts += [
        {"id": "bp_s", "text": "x", "status": "scheduled", "dueAt": "2030-01-07T09:30:00.000Z", "channelId": "ch_x"},
        {"id": "bp_sent", "text": "x", "status": "sent", "dueAt": "2030-01-05T19:58:00.000Z", "channelId": "ch_x"},
        {"id": "bp_err", "text": "x", "status": "error", "dueAt": "2030-01-05T19:58:00.000Z", "channelId": "ch_x",
         "error": {"message": "Video too long"}},
    ]
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("bp_s")]),
                                  _clip("c2", "m2", placements=[_placement("bp_sent")]),
                                  _clip("c3", "m3", placements=[_placement("bp_err")])])
    t = human.get(f"/api/takes/{rid}").json()
    by = {c["id"]: c["placements"][0] for c in t["clips"]}
    assert by["c1"]["status"] == "scheduled" and by["c1"]["at"] == "2030-01-07T09:30:00.000Z" and by["c1"]["local"] == "Mon 09:30"
    assert by["c2"]["status"] == "sent"
    assert by["c3"]["status"] == "failed" and "Video too long" in by["c3"]["error"]


def test_the_check_reuses_the_cached_buffer_read(human, app, fake):
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("d1")])])  # d1 is a draft in the fake
    human.get(f"/api/takes/{rid}")
    n = _posts_calls(fake)
    for _ in range(3):
        human.get(f"/api/takes/{rid}")
        human.get("/api/takes")
    assert _posts_calls(fake) == n  # inside POSTS_TTL: no more Buffer requests


def test_nothing_changes_when_buffer_cant_be_read_or_a_list_is_full(human, app, fake, monkeypatch):
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("bp_unknown")])])
    buf = app.state.studio.buffer

    def boom(*a, **k):
        raise RuntimeError("Buffer API rate limit reached")
    monkeypatch.setattr(buf, "posts", boom)
    assert human.get(f"/api/takes/{rid}").json()["clips"][0]["placements"][0]["status"] == "draft"
    from studio.buffer_api import POSTS_PAGE
    monkeypatch.setattr(buf, "posts", lambda uid, org, sts, first=30: [{"id": f"x{i}", "status": sts[0]} for i in range(POSTS_PAGE)]
                        if sts == ["draft"] else [])
    assert human.get(f"/api/takes/{rid}").json()["clips"][0]["placements"][0]["status"] == "draft"


def test_a_full_sent_list_says_deleted_or_sent(human, app, fake, monkeypatch):
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("bp_x")])])
    from studio.buffer_api import POSTS_PAGE
    monkeypatch.setattr(app.state.studio.buffer, "posts",
                        lambda uid, org, sts, first=30: [{"id": f"s{i}", "status": "sent"} for i in range(POSTS_PAGE)] if sts == ["sent"] else [])
    p = human.get(f"/api/takes/{rid}").json()["clips"][0]["placements"][0]
    assert p["status"] == "gone" and p["gone"] == "deleted_or_sent"


def test_a_post_waiting_for_approval_follows_its_approval(human, app, fake):
    st = app.state.store
    a_ok = st.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,result,created,decided) VALUES(?,?,?,?,?,?,?,?)",
                ("acc_1", 1, "bot", "{}", "approved", json.dumps({"status": "sent_to_buffer", "mode": "draft", "post": {"id": "d1"}}),
                 time.time(), time.time()))
    a_no = st.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,result,created,decided) VALUES(?,?,?,?,?,?,?,?)",
                ("acc_1", 1, "bot", "{}", "rejected", None, time.time(), time.time()))
    rid = _take(st, [_clip("c1", "m1", placements=[_placement(None, "pending_approval", approval_id=a_ok)]),
                     _clip("c2", "m2", placements=[_placement(None, "pending_approval", approval_id=a_no)])])
    by = {c["id"]: c["placements"][0] for c in human.get(f"/api/takes/{rid}").json()["clips"]}
    assert by["c1"]["status"] == "draft" and by["c1"]["buffer_post_id"] == "d1"
    assert by["c2"]["status"] == "gone" and by["c2"]["gone"] == "rejected"


def test_the_clip_row_names_a_sent_post_and_hides_gone_ones():
    js = (ST / "take.js").read_text()
    assert 'sent: "posted by Buffer"' in js
    assert '["draft", "scheduled", "queued", "pending_approval", "sent"].includes(p.status)' in js


def test_a_post_saved_moments_ago_is_never_called_deleted(human, app, fake):
    rid = _take(app.state.store, [_clip("c1", "m1", placements=[_placement("bp_new", placed_at=time.time() - 30)]),
                                  _clip("c2", "m2", placements=[_placement("bp_old", placed_at=time.time() - 600)])])
    by = {c["id"]: c["placements"][0]["status"] for c in human.get(f"/api/takes/{rid}").json()["clips"]}
    assert by == {"c1": "draft", "c2": "gone"}
