"""Onboarding + recommended times, best times, human approvals, revisions, notes, media library, contacts, report."""
from datetime import datetime, timedelta

from huhqueue.models import Comment, Event, Post, PostTarget

from .conftest import make_image, upload


def test_onboarding_research_times_saved_as_slots(api, channel):
    ob = api.get(f"/api/channels/{channel['id']}/onboarding").json()
    assert ob["channel"]["username"] == "huhbyte_demo" and ob["default_goal"] == 3 and not ob["done"]
    rec = api.post(f"/api/channels/{channel['id']}/onboarding/recommend", {"goal": 5}).json()
    assert len(rec["times"]) == 5 and rec["source"] == "research" and "buffer.com" in rec["source_url"]
    assert len({t["weekday"] for t in rec["times"]}) == 5                     # spread over the week
    assert rec["times"][0]["time"][:2] == rec["times"][0]["start"][:2]        # inside the window
    r = api.post(f"/api/channels/{channel['id']}/onboarding/finish", {"goal": 5, "times": [dict(t, enabled=i != 0) for i, t in enumerate(rec["times"])]}).json()
    assert len(r["slots"]) == 4
    ch = api.get(f"/api/channels/{channel['id']}").json()
    assert len(ch["slots"]) == 4
    assert api.get(f"/api/channels/{channel['id']}/onboarding").json()["done"]
    # queue posts now flow into those slots
    t = api.post("/api/posts", {"caption": "x", "channel_ids": [channel["id"]], "when": "queue"}).json()["targets"][0]
    assert t["scheduled_local_iso"][11:16] in {s["time"] for s in ch["slots"]}


def test_recommendations_from_own_data(api, core, channel):
    from huhqueue.services import scheduling
    with core.db.session() as s:
        from huhqueue.models import Channel
        ch = s.get(Channel, channel["id"])
        zone = scheduling.tz(ch)
        base = datetime(2026, 9, 1, tzinfo=zone)   # a Tuesday
        for wk in range(6):
            for (day, hour, er) in ((1, 21, 9.0), (3, 7, 1.0), (5, 14, 2.0)):
                local = base + timedelta(weeks=wk, days=day - 1, hours=hour)
                p = Post(caption="p")
                s.add(p)
                s.flush()
                s.add(PostTarget(post_id=p.id, channel_id=ch.id, status="sent", published_at=local.astimezone().replace(tzinfo=None) if False else
                                 local.astimezone(__import__("zoneinfo").ZoneInfo("UTC")).replace(tzinfo=None), insights={"engagement_rate": er}))
    s_ = api.get(f"/api/insights/best-times?channel_id={channel['id']}&days=3650").json()
    assert s_["heatmap"]["posts"] == 18
    rec = api.post(f"/api/channels/{channel['id']}/onboarding/recommend", {"goal": 1}).json()
    assert rec["source"] == "own_data" and rec["times"][0]["weekday"] == 1 and rec["times"][0]["start"] == "21:00"


def test_contributor_request_approval_and_reject_needs_note(api, channel):
    t = api.post("/api/posts", {"caption": "please review", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    r = api.post(f"/api/targets/{t['id']}/request-approval", {"when": "queue", "note": "first draft"}).json()
    assert r["status"] == "pending_approval" and r["approval"]["kind"] == "human"
    assert api.post(f"/api/targets/{t['id']}/reject", {}).status_code == 400
    r = api.post(f"/api/targets/{t['id']}/reject", {"note": "Shorter hook please"}).json()
    assert r["status"] == "draft" and r["approval"]["reject_note"] == "Shorter hook please"


def test_revision_restore(api, channel):
    t = api.post("/api/posts", {"caption": "v1", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    api.patch(f"/api/targets/{t['id']}", {"caption": "v2"})
    api.patch(f"/api/targets/{t['id']}", {"caption": "v3"})
    revs = api.get(f"/api/posts/{t['post_id']}/revisions").json()
    assert [r["rev"] for r in revs] == [2, 1]
    api.post(f"/api/posts/{t['post_id']}/revisions/1/restore")
    assert api.get(f"/api/targets/{t['id']}").json()["caption"] == "v1"


def test_mention_creates_notification(api, core, channel):
    t = api.post("/api/posts", {"caption": "x", "channel_ids": [channel["id"]], "when": "draft"}).json()["targets"][0]
    n = api.post(f"/api/posts/{t['post_id']}/notes", {"text": "@ana can you check the thumbnail?"}).json()
    assert n["mentions"] == ["ana"]
    assert len(api.get(f"/api/posts/{t['post_id']}/notes").json()) == 1
    # @ana is not on the team, so nobody is notified (teams: mentions go to the matching teammate only - see test_teams.py)
    assert not any(e["kind"] == "mention" for e in api.get("/api/notifications").json())


def _pattern(path, size, fmt="PNG", flip=False):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (800, 800), (240, 240, 240))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 400, 800) if not flip else (0, 0, 800, 300), fill=(20, 20, 20))
    d.ellipse((450, 450, 750, 750), fill=(200, 40, 40))
    im.resize(size).save(path, fmt)
    return path


def test_media_library_folders_tags_usage_and_dedup(api, channel, tmp_path):
    a = upload(api, _pattern(tmp_path / "a.png", (800, 800)), "a.png")[0]
    b = upload(api, _pattern(tmp_path / "b.jpg", (400, 400), "JPEG"), "b.jpg")[0]   # same picture, resized + recompressed
    c = upload(api, _pattern(tmp_path / "c.png", (600, 600), flip=True), "c.png")[0]
    api.patch(f"/api/media/{a['id']}", {"folder": "launches/2026", "tags": ["Launch", "orgo"], "title": "Orgo launch"})
    assert [m["id"] for m in api.get("/api/media?folder=launches/2026").json()] == [a["id"]]
    assert [m["id"] for m in api.get("/api/media?tag=launch").json()] == [a["id"]]
    assert [m["id"] for m in api.get("/api/media?q=orgo").json()] == [a["id"]]
    folders = api.get("/api/media-folders").json()
    assert {"name": "launches/2026", "count": 1} in folders["folders"]
    api.post("/api/posts", {"caption": "x", "media_ids": [a["id"]], "channel_ids": [channel["id"]], "when": "draft"})
    api.post("/api/posts", {"caption": "y", "media_ids": [a["id"]], "channel_ids": [channel["id"]], "when": "draft"})
    assert api.get(f"/api/media/{a['id']}/usage").json()["uses"] == 2
    unused = {m["id"] for m in api.get("/api/media?unused=true").json()}
    assert a["id"] not in unused and c["id"] in unused
    dups = {m["id"] for m in api.get("/api/media?duplicates=true").json()}
    assert {a["id"], b["id"]} <= dups and c["id"] not in dups
    assert api.delete(f"/api/media/{a['id']}").status_code == 409
    assert api.delete(f"/api/media/{a['id']}?force=true").status_code == 200


def test_contacts_segments_profile(api, core, channel):
    with core.db.session() as s:
        for i, (who, txt, st) in enumerate([("fan", "wow?", "open"), ("fan", "more", "replied"), ("fan", "yes", "open"), ("newbie", "hi", "open")]):
            s.add(Comment(channel_id=channel["id"], external_id=f"x{i}", author=who, text=txt, status=st, at=datetime.utcnow() - timedelta(days=i)))
    rows = api.get("/api/contacts").json()
    assert {r["handle"] for r in rows} == {"fan", "newbie"}
    segs = {x["id"]: x["count"] for x in api.get("/api/contact-segments").json()}
    assert segs["superfans"] == 1 and segs["questions"] == 1 and segs["unanswered"] == 2
    assert [r["handle"] for r in api.get("/api/contacts?segment=superfans").json()] == ["fan"]
    api.patch("/api/contacts/fan", {"notes": "VIP", "tags": ["vip"], "assignee": "Ram"})
    c = api.get("/api/contacts/fan").json()
    assert c["notes"] == "VIP" and c["tags"] == ["vip"] and len(c["history"]) == 3
    assert [r["handle"] for r in api.get("/api/contacts?q=vip").json()] == ["fan"]


def test_report_markdown_matches_kpis(api, core, channel):
    with core.db.session() as s:
        for days, views in ((2, 100), (3, 50), (40, 30)):
            p = Post(caption=f"p{days}")
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=channel["id"], status="sent", published_at=datetime.utcnow() - timedelta(days=days), insights={"views": views}))
    md = api.get("/api/reports/report.md?days=30").text
    assert "| Views | 150 | 30 | +400.0% |" in md and "| Posts | 2 | 1 | +100.0% |" in md
