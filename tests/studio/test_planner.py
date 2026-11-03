"""Planner: one edit path (studio.update_post) behind drag-to-reschedule, the edit dialog, REST and MCP."""
from datetime import datetime, timedelta, timezone

from .conftest import agent_token, mcp_call


def _soon(hours=48):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat()


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_reschedule_scheduled_post_calls_buffer_editpost(human, fake):
    due = _soon()
    r = human.post("/api/posts/p9/reschedule", json={"due_at": due})
    assert r.status_code == 200, r.text
    edit = fake.edits[-1]
    assert edit["id"] == "p9" and edit["mode"] == "customScheduled" and "saveToDraft" not in edit
    assert _iso(edit["dueAt"]) == _iso(due)
    row = human.get("/api/audit").json()[0]
    assert row["action"] == "update_post" and row["detail"]["buffer_post_id"] == "p9"


def test_draft_stays_a_draft_when_moved_and_can_be_edited(human, fake):
    r = human.patch("/api/posts/d1", json={"due_at": _soon(30), "text": "a better draft"}).json()
    assert r["status"] == "updated"
    assert fake.edits[-1]["saveToDraft"] is True and fake.edits[-1]["text"] == "a better draft"


def test_sent_posts_cannot_move_and_past_times_are_refused(human, fake):
    assert human.post("/api/posts/s1/reschedule", json={"due_at": _soon()}).status_code == 409
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert human.post("/api/posts/p9/reschedule", json={"due_at": past}).status_code == 400
    assert human.post("/api/posts/nope/reschedule", json={"due_at": _soon()}).status_code == 404
    assert human.patch("/api/posts/p9", json={"text": "x" * 400}).status_code == 400  # X limit
    assert not fake.edits


def test_delete_draft_only(human, fake):
    assert human.delete("/api/posts/p9").status_code == 409
    assert human.delete("/api/posts/d1").json() == {"status": "deleted", "id": "d1"}
    assert fake.deleted == ["d1"]


def test_csrf_needed_for_planner_writes(client, human):
    client.headers.pop("X-HQ-CSRF")
    assert client.post("/api/posts/p9/reschedule", json={"due_at": _soon()}).status_code == 403


def test_rest_and_mcp_reschedule_send_identical_buffer_calls(human, fake):
    due = _soon(72)
    human.post("/api/posts/p9/reschedule", json={"due_at": due})
    tok = agent_token(human, "trusted")
    res, out = mcp_call(human, tok, "reschedule_post", {"post_id": "p9", "due_at": due})
    assert not res["isError"], out
    assert fake.edits[0] == fake.edits[1]


def test_review_agent_may_only_move_drafts(human, fake):
    tok = agent_token(human, "review")
    res, msg = mcp_call(human, tok, "reschedule_post", {"post_id": "p9", "due_at": _soon()})
    assert res["isError"] and "only change drafts" in msg
    res, out = mcp_call(human, tok, "update_post", {"post_id": "d1", "due_at": _soon()})
    assert not res["isError"] and out["status"] == "updated"
    res, out = mcp_call(human, tok, "delete_draft", {"post_id": "d1"})
    assert not res["isError"]


def test_kill_switch_stops_agent_reschedule(human, fake):
    tok = agent_token(human, "trusted")
    human.post("/api/safety/kill", json={"on": True})
    res, msg = mcp_call(human, tok, "reschedule_post", {"post_id": "p9", "due_at": _soon()})
    assert res["isError"] and "kill switch" in msg and not fake.edits


def test_approval_gets_a_time_and_keeps_it_when_approved(human, fake):
    tok = agent_token(human, "review")
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_li", "text": "Agent post", "mode": "queue"})
    _, held = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    due = _soon(26)
    r = human.post(f"/api/approvals/{held['approval_id']}/reschedule", json={"due_at": due}).json()
    assert r["status"] == "pending_approval"
    human.post(f"/api/approvals/{held['approval_id']}/approve", json={})
    assert fake.created[-1]["mode"] == "customScheduled" and _iso(fake.created[-1]["dueAt"]) == _iso(due)
    # an agent can't move items in the human's approval inbox
    assert human.post(f"/api/approvals/{held['approval_id']}/reschedule", json={"due_at": due},
                      headers={"Authorization": f"Bearer {tok}"}, cookies={}).status_code in (403, 404)


def test_calendar_feed_has_drafts_states_and_posting_slots(human):
    cal = human.get("/api/calendar").json()
    states = {i["state"] for i in cal["items"]}
    assert {"scheduled", "sent", "draft"} <= states
    by = {i["id"]: i for i in cal["items"]}
    assert by["s1"]["draggable"] is False and by["p9"]["draggable"] is True and by["d1"]["when"] == ""
    assert by["p9"]["ref"] == "buffer:p9" and by["p9"]["channel_id"] == "ch_x" and by["p9"]["type"] == "text"
    assert cal["slots"][0]["channel_id"] == "ch_x" and cal["slots"][0]["schedule"][0]["times"] == ["09:00", "17:00"]


def test_demo_planner_has_undated_drafts_slots_and_moves_without_buffer(client, fake):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    cal = client.get("/api/calendar").json()
    undated = [i for i in cal["items"] if i["state"] == "draft" and not i["when"]]
    assert len(undated) >= 2 and len(cal["slots"]) == 5
    # the view-only demo shows the Planner but a drag (reschedule) is refused with the friendly line and nothing moves
    r = client.post(f"/api/posts/{undated[0]['id']}/reschedule", json={"due_at": _soon(5)})
    assert r.status_code == 403 and r.json()["view_only"]
    same = {i["id"]: i for i in client.get("/api/calendar").json()["items"]}[undated[0]["id"]]
    assert not same["when"] and same["state"] == "draft"
    assert not fake.calls  # the sample workspace never reaches Buffer
