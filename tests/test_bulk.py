"""Bulk actions: every post action, approval holds for agent tokens, contributor limits, bulk approve for people only,
CSV export, bulk channel settings, notification switches and the test notification."""
import csv
import io

import httpx
import respx

from .conftest import future
from .test_teams import member


def drafts(api, channel, n=3, caption="bulk draft", **extra):
    out = []
    for i in range(n):
        r = api.post("/api/posts", {"caption": f"{caption} {i}", "channel_ids": [channel["id"]], "when": "draft", **extra})
        assert r.status_code == 200, r.text
        out.append(r.json()["targets"][0]["id"])
    return out


def bulk(api, action, ids, **kw):
    r = api.post("/api/bulk", {"action": action, "ids": ids, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def target(api, tid):
    return api.get(f"/api/targets/{tid}").json()


def second_channel(api):
    r = api.post("/api/connect/fake/token", {"access_token": "tok-5678", "username": "huhbyte_two"})
    assert r.status_code == 200, r.text
    return r.json()


def test_queue_prioritize_draft_and_reschedule(api, channel):
    ids = drafts(api, channel)
    out = bulk(api, "queue", ids)
    assert out["ok"] and out["count"] == 3 and out["action"] == "queue"
    assert {target(api, t)["status"] for t in ids} == {"scheduled"}
    assert bulk(api, "prioritize", ids[2:])["ok"]
    assert target(api, ids[2])["prioritized"] is True
    at = future(24 * 60)
    out = bulk(api, "reschedule", ids, scheduled_at=at)          # each item still meets the daily rate guard (2/day)
    assert out["count"] == 2 and out["failed"] == 1 and any("daily limit" in r.get("error", "") for r in out["results"])
    api.patch(f"/api/channels/{channel['id']}", {"daily_limit": 10})
    assert bulk(api, "reschedule", ids, scheduled_at=at)["ok"]
    before = {t: target(api, t)["scheduled_at"] for t in ids}
    assert all(target(api, t)["schedule_mode"] == "custom" for t in ids)
    assert bulk(api, "reschedule", ids, shift_minutes=60)["ok"]
    after = {t: target(api, t)["scheduled_at"] for t in ids}
    assert all(after[t] != before[t] for t in ids)
    assert bulk(api, "draft", ids)["ok"]
    assert {target(api, t)["status"] for t in ids} == {"draft"}
    out = bulk(api, "reschedule", ids[:1], shift_minutes=30)   # a draft has no time to shift
    assert not out["ok"] and "Not scheduled" in out["results"][0]["error"]


def test_tags_campaign_duplicate_move_delete(api, core, channel):
    ids = drafts(api, channel, 2)
    assert bulk(api, "tag", ids, tag="launch")["ok"]
    assert bulk(api, "tag", ids, tags=["ai", "launch"])["ok"]
    assert target(api, ids[0])["tags"] == ["ai", "launch"]
    assert bulk(api, "untag", ids, tag="launch")["ok"]
    assert target(api, ids[1])["tags"] == ["ai"]
    assert bulk(api, "set_tags", ids, tags=["news"])["ok"]
    assert target(api, ids[0])["tags"] == ["news"]
    assert bulk(api, "campaign", ids, campaign="DevDay")["ok"]
    assert target(api, ids[0])["campaign"] == "DevDay"
    assert bulk(api, "campaign", ids, campaign="")["ok"] and target(api, ids[0])["campaign"] == ""

    dup = bulk(api, "duplicate", ids[:1])
    assert dup["ok"] and (dup["results"][0].get("new_id") or dup["results"][0].get("new_ids"))

    two = second_channel(api)
    mv = bulk(api, "move", ids[1:], channel_id=two["id"])
    assert mv["ok"], mv
    moved = target(api, mv["results"][0]["new_id"])
    assert moved["channel"]["id"] == two["id"] and moved["caption"] == "bulk draft 1"
    assert api.get(f"/api/targets/{ids[1]}").status_code == 404
    assert not bulk(api, "move", ids[:1])["ok"]                       # needs channel_id

    out = bulk(api, "delete", [ids[0], "pt_missing"])                  # one bad id never aborts the rest
    assert out["count"] == 1 and out["failed"] == 1 and not out["ok"]
    assert api.get(f"/api/targets/{ids[0]}").status_code == 404


def test_publish_now_and_request_approval(api, channel):
    ids = drafts(api, channel, 2)
    out = bulk(api, "publish_now", ids[:1])
    assert out["ok"]
    api.post("/api/worker/tick")
    assert target(api, ids[0])["status"] == "sent"
    out = bulk(api, "request_approval", ids[1:], note="please check")
    assert out["ok"] and target(api, ids[1])["status"] == "pending_approval"


def test_validation(api, channel):
    ids = drafts(api, channel, 1)
    assert api.post("/api/bulk", {"action": "explode", "ids": ids}).status_code == 400
    assert api.post("/api/bulk", {"action": "queue", "ids": []}).status_code == 400
    assert api.post("/api/bulk", {"action": "queue", "ids": [f"pt_{i}" for i in range(501)]}).status_code == 400
    assert api.post("/api/bulk", {"action": "reschedule", "ids": ids}).status_code == 400


def test_agent_token_bulk_lands_in_approvals_and_cannot_bulk_approve(app, api, channel):
    from fastapi.testclient import TestClient
    tok = api.post("/api/tokens", {"name": "agent", "scopes": ["read", "write", "publish"], "require_approval": True}).json()["token"]
    ids = drafts(api, channel, 2)
    agent = TestClient(app)
    H = {"Authorization": f"Bearer {tok}"}
    r = agent.post("/api/bulk", json={"action": "queue", "ids": ids}, headers=H)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] and out["pending_approval"] == 2
    assert {target(api, t)["status"] for t in ids} == {"pending_approval"}
    r = agent.post("/api/bulk/approve", json={"ids": ids}, headers=H)
    assert r.status_code == 403          # a token (agent or admin scope) can never approve

    out = api.post("/api/bulk/approve", {"ids": ids}).json()      # a person in the app can
    assert out["ok"] and out["decision"] == "approve"
    assert {target(api, t)["status"] for t in ids} == {"scheduled"}


def test_bulk_reject_with_note(api, app, channel):
    con = member(app, api, "contributor", name="Cory")
    ids = drafts(con, channel, 2)
    assert con.post("/api/bulk", {"action": "request_approval", "ids": ids}).json()["ok"]
    out = api.post("/api/bulk/approve", {"ids": ids, "decision": "reject", "note": "needs a hook"}).json()
    assert out["ok"] and out["decision"] == "reject"
    assert {target(api, t)["status"] for t in ids} == {"draft"}
    assert api.post("/api/bulk/approve", {"ids": ids, "decision": "maybe"}).status_code == 400


def test_contributor_bulk_limits(app, api, channel):
    con = member(app, api, "contributor", name="Cory")
    ids = drafts(con, channel, 2)
    for action, kw in (("queue", {}), ("prioritize", {}), ("publish_now", {}), ("reschedule", {"scheduled_at": future(90)})):
        assert con.post("/api/bulk", {"action": action, "ids": ids, **kw}).status_code == 403, action
    assert con.post("/api/bulk", {"action": "tag", "ids": ids, "tag": "x"}).json()["ok"]
    assert con.post("/api/bulk", {"action": "campaign", "ids": ids, "campaign": "C"}).json()["ok"]
    assert con.post("/api/bulk/approve", {"ids": ids}).status_code == 403
    assert con.post("/api/channels/bulk-settings", {"channel_ids": ["all"], "daily_limit": 9}).status_code == 403


def test_export_csv_selected_and_filtered(api, channel):
    ids = drafts(api, channel, 3, caption="csv")
    bulk(api, "tag", ids[:2], tag="pick")
    r = api.get("/api/posts/export.csv", params={"ids": ",".join(ids[:2])})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["id"] for x in rows] == ids[:2] and rows[0]["tags"] == "pick" and rows[0]["channel"] == "@huhbyte_demo"
    rows = list(csv.DictReader(io.StringIO(api.get("/api/posts/export.csv", params={"tag": "pick"}).text)))
    assert sorted(x["id"] for x in rows) == sorted(ids[:2])
    rows = list(csv.DictReader(io.StringIO(api.get("/api/posts/export.csv", params={"status": "draft"}).text)))
    assert len(rows) == 3


def test_bulk_channel_settings(api, channel):
    two = second_channel(api)
    api.put(f"/api/channels/{channel['id']}/slots", {"slots": [{"weekday": 0, "time": "09:15"}, {"weekday": 2, "time": "18:00"}]})
    out = api.post("/api/channels/bulk-settings", {"channel_ids": ["all"], "timezone": "America/New_York", "daily_limit": 4,
                                                  "settings": {"link_shortening": True, "posting_goal": 6, "disabled_days": [6]},
                                                  "copy_slots_from": channel["id"]}).json()
    assert out["ok"] and out["count"] == 2, out
    for cid in (channel["id"], two["id"]):
        ch = api.get(f"/api/channels/{cid}").json()
        assert ch["timezone"] == "America/New_York" and ch["daily_limit"] == 4
        st = api.get(f"/api/channels/{cid}/settings").json()
        assert st["link_shortening"] is True and st["posting_goal"] == 6 and st["disabled_days"] == [6]
    slots = {(s["weekday"], s["time"]) for s in api.get(f"/api/channels/{two['id']}").json()["slots"]}
    assert slots == {(0, "09:15"), (2, "18:00")}

    out = api.post("/api/channels/bulk-settings", {"channel_ids": [channel["id"]], "timezone": "Not/AZone"}).json()
    assert not out["ok"] and out["results"][0]["error"]
    assert api.post("/api/channels/bulk-settings", {"channel_ids": [channel["id"]]}).status_code == 400
    assert api.post("/api/channels/bulk-settings", {"channel_ids": []}).status_code == 400


@respx.mock
def test_notification_switches_pause_and_test_button(api, core, channel):
    hook = respx.post("https://hooks.example/push").respond(200)
    assert api.post("/api/notifications/test").status_code == 400          # no webhook yet
    api.put("/api/settings", {"notify_webhook_url": "https://hooks.example/push"})
    r = api.post("/api/notifications/test")
    assert r.status_code == 200 and r.json()["ok"] and "failed" in r.json()["kinds"]
    assert hook.call_count == 1

    st = api.get("/api/settings").json()
    assert st["notify_events"]["failed"] is True
    assert api.put("/api/settings", {"notify_events": {"nope": False}}).status_code == 400
    api.put("/api/settings", {"notify_events": {"failed": False}})
    assert api.get("/api/settings").json()["notify_events"]["failed"] is False

    from huhqueue.services.events import push_enabled
    assert not push_enabled(core, "failed") and push_enabled(core, "approval")
    api.put("/api/settings", {"notify_paused": True})
    assert not push_enabled(core, "approval")
    api.put("/api/settings", {"notify_paused": False})
    assert push_enabled(core, "approval")

    t = drafts(api, channel, 1, caption="FAIL now")[0]                     # the fake network fails this one
    api.post(f"/api/targets/{t}/publish-now")
    api.post("/api/worker/tick")
    assert target(api, t)["status"] == "failed"
    import json as _json                                                   # 'failed' push is switched off
    assert not [c for c in hook.calls[1:] if _json.loads(c.request.content).get("kind") == "failed"]
    assert any(n["kind"] == "failed" for n in api.get("/api/notifications").json())   # the bell still gets it
