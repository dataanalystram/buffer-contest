"""Inbox: what needs a person, grouped by urgency. REST = MCP, the sample workspace shows every group, a failed post can
be fixed and rescheduled, and the open-slot maths is pure and timezone-correct."""
from datetime import datetime, timezone

from studio import inbox

from .conftest import agent_token, mcp_call

KEYS = ["approvals", "channels", "failed", "preflight", "undated", "open_slots", "plan"]


def demo(client):
    client.get("/demo", follow_redirects=False)
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    return client


def test_sample_inbox_has_every_group_most_urgent_first(client, fake):
    d = demo(client).get("/api/inbox").json()
    assert sorted(g["key"] for g in d["groups"]) == sorted(KEYS)
    order = [g["urgency"] for g in d["groups"]]
    assert order == sorted(order, key=["now", "soon", "fyi"].index)
    g = {x["key"]: x for x in d["groups"]}
    assert g["approvals"]["count"] == 1 and g["approvals"]["items"][0]["agent"] == "Claude"
    assert g["failed"]["count"] == 1 and "aspect ratio" in g["failed"]["items"][0]["reason"]
    assert g["failed"]["items"][0]["actions"][0]["go"].startswith("planner:buffer:")
    assert g["preflight"]["count"] == 1 and "too long for X" in g["preflight"]["items"][0]["reason"]
    assert g["undated"]["count"] == 2
    assert g["open_slots"]["count"] > 0 and g["open_slots"]["actions"][0]["go"] == "take"
    assert d["waiting"] == 1 + 1 + 1 + 2
    for grp in d["groups"]:
        assert grp["summary"] and grp["title"]
    assert not fake.calls  # the sample never calls Buffer


def test_fix_and_reschedule_a_failed_post_clears_it(client):
    c = demo(client)
    failed = {g["key"]: g for g in c.get("/api/inbox").json()["groups"]}["failed"]["items"][0]
    r = c.post(f"/api/posts/{failed['id']}/reschedule", json={"due_at": "2099-01-05T09:00:00Z"})
    assert r.status_code == 200, r.text
    after = {g["key"]: g for g in c.get("/api/inbox").json()["groups"]}
    assert after["failed"]["count"] == 0 and after["failed"]["summary"] == "Nothing has bounced."
    cal = c.get("/api/calendar").json()["items"]
    assert any(i["id"] == failed["id"] and i["state"] == "scheduled" for i in cal)


def test_calendar_shows_failed_posts_with_the_reason(client):
    cal = demo(client).get("/api/calendar").json()["items"]
    f = [i for i in cal if i["state"] == "failed"]
    assert len(f) == 1 and f[0]["error"].startswith("Instagram rejected") and f[0]["draggable"]


def test_mcp_get_inbox_matches_rest_and_agents_see_no_approvals(human, fake):
    tok = agent_token(human)
    res, body = mcp_call(human, tok, "get_inbox")
    assert not res["isError"]
    assert [g["key"] for g in body["groups"]] == [g["key"] for g in human.get("/api/inbox").json()["groups"]]
    assert {g["key"]: g for g in body["groups"]}["approvals"]["count"] == 0  # approvals are a human's job
    tools = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                       json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    desc = next(t["description"] for t in tools if t["name"] == "get_inbox")
    assert "Inbox" in desc and "Needs you" not in desc


def test_open_slots_skip_taken_paused_and_past_times():
    now = datetime(2026, 11, 2, 10, 30, tzinfo=timezone.utc)  # Monday 10:30 UTC = 10:30 Dublin (winter)
    sched = [{"id": "c1", "timezone": "Europe/Dublin", "postingSchedule": [
        {"day": "mon", "times": ["09:00", "12:00", "18:00"], "paused": False},
        {"day": "tue", "times": ["09:00"], "paused": True}]}]
    taken = [{"channelId": "c1", "dueAt": "2026-11-02T12:00:00.000Z"}]
    out = inbox.open_slots(sched, taken, now, days=1)
    assert [s["when"] for s in out] == ["2026-11-02T18:00:00Z"]  # 09:00 is past, 12:00 is taken, Tuesday is paused
    ny = [{"id": "c2", "timezone": "America/New_York", "postingSchedule": [{"day": "mon", "times": ["09:00"]}]}]
    assert inbox.open_slots(ny, [], now, days=1)[0]["when"] == "2026-11-02T14:00:00Z"  # 09:00 EST


def test_no_needs_you_left_in_the_app():
    """The screen is called Inbox everywhere: the app, the MCP descriptions, the API field and the docs."""
    import re
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    root = repo / "studio"
    files = [f for f in list((root / "static").glob("*.*")) + list(root.glob("*.py")) if f.suffix in (".js", ".html", ".css", ".py")]
    files += [repo / n for n in ("README.md", "CONTEST.md", "DESIGN.md", "SUBMISSION.md", "SETUP.md")] + list((repo / "docs").glob("*.md"))
    old = re.compile(r"needs[ _-]you\b", re.I)
    for f in files:
        if f.exists():
            assert not old.search(f.read_text(encoding="utf-8")), f
