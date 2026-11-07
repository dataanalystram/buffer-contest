"""The Spread, per post (owner, 6 Nov 2026: "why it says only 2 when all 4 are done ... I want the same options like Make a
post where I can schedule or add to queue ... there should be an option to remove"). A person can clear a Needs review
clip ("Looks fine, use it": adjust_clip review_ok), and the confirm call takes choices per placement (draft | schedule |
queue | skip) and times (a moved card). Schedule and queue publish on their own, so agents stay drafts-only."""
import base64
from pathlib import Path

from fastapi.testclient import TestClient

from .conftest import agent_token, mcp_call
from .test_face_safe import _jpeg

ROOT = Path(__file__).resolve().parents[2]


def _take_with_a_review_clip(human, app, monkeypatch):
    from tests.studio.test_take_opus import YT, _analysis, _github, _hosts
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    house = app.state.house
    monkeypatch.setattr(house.takes, "_channels", lambda a, ids: [c for c in house.studio.channels(a) if c["id"] == "ch_ig"])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "best moments"}).json()
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt})
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": _analysis()}).json()["ok"]
    rjid, _, _, rjt = sent[1]
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    base = {"type": "video", "width": 1080, "height": 1920, "duration": 40.0, "cover_offset_ms": 400, "layout": "track",
            "face_coverage": 0.98}
    for k, c in enumerate(rspec["clips"]):
        fs = {"status": "ok", "max_cover": 0.31 if k == 0 else 0.0, "qa_jpegs": [{"t": 1.0, "jpeg": _jpeg()}]}
        for cid in [c["id"], *c["variant_ids"]]:
            r = w.post(f"/api/worker/jobs/{rjid}/clips/{cid.replace(':', '%3A')}", headers={"x-job-token": rjt},
                       json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + cid.encode()).decode(),
                             "cover": base64.b64encode(b"\xff\xd8\xff c").decode(), "meta": {**base, "face_safe": dict(fs)}})
            assert r.json()["ok"], r.text
    assert w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {}}).json()["ok"]
    return human.get(f"/api/takes/{t['id']}").json()


def test_looks_fine_clears_needs_review_for_a_person_only(human, app, fake, monkeypatch):
    t = _take_with_a_review_clip(human, app, monkeypatch)
    first = t["clips"][0]
    assert first["status"] == "needs_review"
    tok = agent_token(human, "trusted", cap=50)
    _, err = mcp_call(human, tok, "adjust_clip", {"take_id": t["id"], "clip_id": first["id"], "review_ok": True})
    assert "Only a person" in err
    assert human.patch(f"/api/takes/{t['id']}/clips/{first['id']}", json={"review_ok": "yes"}).status_code == 400
    r = human.patch(f"/api/takes/{t['id']}/clips/{first['id']}", json={"review_ok": True})
    assert r.status_code == 200, r.text
    c = next(x for x in r.json()["clips"] if x["id"] == first["id"])
    assert c["status"] == "ready" and c["review_ok"]["by"] == "you" and "face" in c["review_ok"]["reason"]
    assert r.json()["changed"] == ["review_ok"]
    assert human.patch(f"/api/takes/{t['id']}/clips/{first['id']}", json={"review_ok": True}).status_code == 409
    dry = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True, "week_of": "next"}).json()
    assert first["id"] in {p["clip_id"] for p in dry["plan"]["placements"]}  # all clips count now


def test_each_planned_post_can_be_a_draft_scheduled_queued_moved_or_removed(human, app, fake, monkeypatch):
    t = _take_with_a_review_clip(human, app, monkeypatch)
    assert human.patch(f"/api/takes/{t['id']}/clips/{t['clips'][0]['id']}", json={"review_ok": True}).status_code == 200
    dry = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True, "week_of": "next"}).json()
    pl = dry["plan"]["placements"]
    assert len(pl) >= 3, pl
    tok = dry["confirm_token"]
    url = f"/api/takes/{t['id']}/place"
    # bad choices never burn the token
    for bad in ({"choices": {"0": "now"}}, {"choices": {"99": "draft"}}, {"times": {"0": "tomorrow"}},
                {"times": {"0": "2020-01-01T10:00:00Z"}}, {"times": {"0": "2030-01-01T10:00:00"}},
                {"choices": {str(i): "skip" for i in range(len(pl))}}, {"choices": ["draft"]}):
        assert human.post(url, json={"confirm_token": tok, **bad}).status_code == 400, bad
    moved = "2030-01-07T18:30:00Z"
    choices = {"0": "schedule", "1": "queue", "2": "skip"}
    out = human.post(url, json={"confirm_token": tok, "choices": choices, "times": {"0": moved}})
    assert out.status_code == 200, out.text
    out = out.json()
    by = {r["mode"]: r for r in out["results"]}
    assert set(by) >= {"schedule", "queue"} and "skip" not in by and len(out["results"]) == len(pl) - 1
    assert by["schedule"]["status"] == "scheduled" and by["schedule"]["at"] == moved
    assert by["queue"]["status"] == "queued" and by["queue"]["at"] is None
    sched = next(x for x in fake.created if str(x.get("dueAt") or "").startswith("2030-01-07T18:30"))
    assert sched["mode"] == "customScheduled" and not sched.get("saveToDraft")
    assert any(x["mode"] == "addToQueue" and not x.get("saveToDraft") for x in fake.created)
    assert all(x.get("saveToDraft") for x in fake.created if x is not sched and x["mode"] != "addToQueue")  # the rest: drafts
    assert "scheduled" in out["message"] and "publish on their own" in out["message"]
    clips = human.get(f"/api/takes/{t['id']}").json()["clips"]
    assert {p["status"] for c in clips for p in c.get("placements") or []} >= {"scheduled", "queued"}


def test_agents_spread_drafts_only(human, app, fake, monkeypatch):
    t = _take_with_a_review_clip(human, app, monkeypatch)
    tok = agent_token(human, "trusted", cap=50)
    _, dry = mcp_call(human, tok, "place_clips_in_week", {"take_id": t["id"], "week_of": "next"})
    _, err = mcp_call(human, tok, "place_clips_in_week", {"take_id": t["id"], "confirm_token": dry["confirm_token"],
                                                         "choices": {"0": "schedule"}})
    assert "drafts only" in err
    _, ok = mcp_call(human, tok, "place_clips_in_week", {"take_id": t["id"], "confirm_token": dry["confirm_token"],
                                                        "choices": {"0": "skip"}})
    assert ok["placed"] == len(dry["plan"]["placements"]) - 1 and all(r["mode"] == "draft" for r in ok["results"])


def test_the_ui_offers_looks_fine_remove_and_how_it_goes():
    js = (ROOT / "studio" / "static" / "take.js").read_text()
    for tid in ("clip-review-ok", "ghost-remove", "ghost-done", "ghost-how-${k}", "ghost-when"):
        assert f'data-testid="{tid}"' in js, tid
    assert "need${review === 1" in js and "Planned, not saved yet" in js and "choices, times" in js
    pl = (ROOT / "studio" / "static" / "planner.js").read_text()
    assert "window.plGhostOpen(i)" in pl and 'data-testid="planner-ghost-how"' in pl


def test_a_new_planner_post_shows_no_error_before_anything_is_typed():
    """Owner, 7 Nov 2026: opening New post in the Planner showed a red 'Post text is empty' at once. The check now waits
    for a caption or a media link; the dashed posting-time marks say what they are."""
    pl = (ROOT / "studio" / "static" / "planner.js").read_text()
    assert 'if (mode === "new" && plDlgBlank()) return null;' in pl and "!(isNew && plDlgBlank())" in pl
    assert pl.count('title="An open posting time in your Buffer schedule.') == 3
