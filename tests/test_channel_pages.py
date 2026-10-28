"""Channel pages: settings, publish header + empty slots, generate slots, per-day off switch, queue actions,
community by post + bulk resolve + mentions notice, channel insights (ranges, previous period, takeaways, CSV,
share as post), UTM rewriting at publish time, and the AI key never being echoed."""
import csv
import io
from datetime import datetime, timedelta

from huhqueue import actions as reg
from huhqueue.api.channel_pages import add_utm, rewrite_links
from huhqueue.models import KV, Channel, Comment, Post, PostTarget

from .conftest import future


def _sent(core, cid, n, days_ago=1, views=100, likes=10, fmt=None, prefix="sent"):
    with core.db.session() as s:
        for i in range(n):
            p = Post(caption=f"{prefix} {i} https://huhbyte.ai/a", tags=["ai"])
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=cid, status="sent", external_id=f"{prefix}-ext-{i}",
                             permalink=f"https://example.com/{prefix}/{i}", options=fmt or {},
                             published_at=datetime.utcnow() - timedelta(days=days_ago, minutes=i),
                             insights={"views": views + i, "likes": likes + i, "comments": 2, "shares": 1}))


def test_settings_roundtrip_and_validation(api, channel):
    cid = channel["id"]
    st = api.get(f"/api/channels/{cid}/settings").json()
    assert st["posting_goal"] >= 1 and st["utm_source"] == "fake" and st["link_shortening"] is False and st["disabled_days"] == []
    st = api.patch(f"/api/channels/{cid}/settings", {"posting_goal": 5, "notify_default": True, "utm_enabled": True,
                                                    "link_shortening": True, "utm_campaign": "launch week"}).json()
    assert st["posting_goal"] == 5 and st["notify_default"] and st["utm_enabled"] and st["utm_campaign"] == "launch week"
    assert api.patch(f"/api/channels/{cid}/settings", {"posting_goal": 0}).status_code == 400
    assert api.patch(f"/api/channels/{cid}/settings", {"disabled_days": [9]}).status_code == 400
    assert api.patch(f"/api/channels/{cid}/settings", {"utm_source": "bad&value"}).status_code == 400
    assert api.get(f"/api/channels/{cid}/settings").json()["posting_goal"] == 5


def test_publish_summary_goal_pill_and_open_slots(api, core, channel):
    cid = channel["id"]
    api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": d, "time": "10:00"} for d in range(7)]})
    api.patch(f"/api/channels/{cid}/settings", {"posting_goal": 4})
    _sent(core, cid, 1, days_ago=0)
    d = api.get(f"/api/channels/{cid}/publish-summary").json()
    assert d["goal"] == 4 and d["sent_this_week"] == 1 and d["timezone"] == channel["timezone"]
    assert d["timezone_label"] == channel["timezone"].split("/")[-1].replace("_", " ")
    n = len(d["open_slots"])
    assert n in (6, 7) and all(s["time"] == "10:00" for s in d["open_slots"])
    assert d["tags"] == ["ai"] and d["counts"]["sent"] == 1
    # a queued post takes the first free slot, so that chip disappears
    api.post("/api/posts", {"caption": "queued", "channel_ids": [cid], "when": "queue"})
    d2 = api.get(f"/api/channels/{cid}/publish-summary").json()
    assert len(d2["open_slots"]) == n - 1 and d2["counts"]["queue"] == 1
    assert d2["open_slots"][0]["at"] != d["open_slots"][0]["at"]


def test_disabled_day_keeps_times_but_skips_them(api, channel):
    cid = channel["id"]
    api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": d, "time": "09:00"} for d in range(7)]})
    api.patch(f"/api/channels/{cid}/settings", {"disabled_days": [0, 1, 2, 3, 4, 5]})
    d = api.get(f"/api/channels/{cid}/publish-summary").json()
    assert len(d["open_slots"]) == 1 and d["open_slots"][0]["day"].startswith("Sun")
    assert len(api.get(f"/api/channels/{cid}").json()["slots"]) == 7          # times are kept
    t = api.post("/api/posts", {"caption": "q", "channel_ids": [cid], "when": "queue"}).json()["targets"][0]
    assert t["scheduled_local"].startswith("Sun")


def test_generate_slots_goal_recommended_copy(api, channel):
    cid = channel["id"]
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "second"}).json()
    api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": 0, "time": "08:15"}]})
    api.patch(f"/api/channels/{cid}/settings", {"posting_goal": 3})
    r = api.post(f"/api/channels/{cid}/slots/generate", {"mode": "goal"}).json()
    assert r["added"] == 2 and len(r["slots"]) == 3 and {"weekday": 0, "time": "08:15"} in r["slots"]
    r = api.post(f"/api/channels/{cid}/slots/generate", {"mode": "goal"}).json()
    assert r["added"] == 0 and "already" in r["message"]
    r = api.post(f"/api/channels/{cid}/slots/generate", {"mode": "recommended", "goal": 5}).json()
    assert len(r["slots"]) == 5 and {"weekday": 0, "time": "08:15"} not in r["slots"]
    assert api.post(f"/api/channels/{other['id']}/slots/generate", {"mode": "copy", "source_channel_id": cid}).json()["slots"] == r["slots"]
    assert api.post(f"/api/channels/{cid}/slots/generate", {"mode": "copy", "source_channel_id": cid}).status_code == 400
    assert api.post(f"/api/channels/{cid}/slots/generate", {"mode": "nope"}).status_code == 400


def test_queue_actions(api, core, channel):
    cid = channel["id"]
    api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": d, "time": t} for d in range(7) for t in ("09:00", "18:00")]})
    for i in range(5):
        api.post("/api/posts", {"caption": f"q{i}", "channel_ids": [cid], "when": "queue"})
    for i in range(2):
        api.post("/api/posts", {"caption": f"d{i}", "channel_ids": [cid], "when": "draft"})
    with core.db.session() as s:
        p = Post(caption="broken")
        s.add(p)
        s.flush()
        s.add(PostTarget(post_id=p.id, channel_id=cid, status="failed"))
    before = sorted(t["scheduled_at"] for t in api.get(f"/api/posts?channel_id={cid}&status=scheduled").json())
    r = api.post(f"/api/channels/{cid}/queue/shuffle").json()
    assert r["count"] == 5
    after = sorted(t["scheduled_at"] for t in api.get(f"/api/posts?channel_id={cid}&status=scheduled").json())
    assert before == after                                           # same slots, new order
    assert api.post(f"/api/channels/{cid}/queue/clean-failed").json()["count"] == 1
    assert api.post(f"/api/channels/{cid}/queue/delete-drafts").json()["count"] == 2
    assert api.post(f"/api/channels/{cid}/queue/empty").json()["count"] == 5
    assert api.get(f"/api/posts?channel_id={cid}").json() == []
    assert api.post(f"/api/channels/{cid}/queue/explode").status_code in (404, 405)


def _comments(core, cid):
    with core.db.session() as s:
        for i, (media, st) in enumerate([("m1", "open"), ("m1", "replied"), ("m2", "open"), ("m2", "open"), ("m3", "resolved")]):
            s.add(Comment(channel_id=cid, external_id=f"c{i}", media_external_id=media, media_caption=f"post {media}",
                          author=f"fan{i}", text=f"comment {i}", status=st, at=datetime.utcnow() - timedelta(hours=10 - i)))


def test_community_by_post_list_sort_and_bulk_resolve(api, core, channel):
    cid = channel["id"]
    _comments(core, cid)
    _sent(core, cid, 2, prefix="quiet")
    d = api.get(f"/api/channels/{cid}/community").json()
    assert d["comments_supported"] and [p["media_external_id"] for p in d["posts"]] == ["m3", "m2", "m1"]
    assert d["totals"] == {"all": 5, "open": 3}
    d = api.get(f"/api/channels/{cid}/community?sort=unanswered").json()
    assert [p["media_external_id"] for p in d["posts"]] == ["m2", "m1", "m3"]
    d = api.get(f"/api/channels/{cid}/community?show_all=true").json()
    assert len(d["posts"]) == 5 and sum(p["count"] == 0 for p in d["posts"]) == 2
    lst = api.get(f"/api/channels/{cid}/community?view=list&status=open").json()["comments"]
    assert len(lst) == 3 and all(c["status"] == "open" for c in lst)
    lst = api.get(f"/api/channels/{cid}/community?view=list&sort=unanswered").json()["comments"]
    assert [c["status"] for c in lst][:3] == ["open"] * 3
    assert api.post(f"/api/channels/{cid}/comments/bulk-resolve", {"media_external_id": "m2"}).json()["resolved"] == 2
    assert api.post(f"/api/channels/{cid}/comments/bulk-resolve", {"ids": [lst[-1]["id"]]}).json()["resolved"] >= 0
    assert api.post(f"/api/channels/{cid}/comments/bulk-resolve", {}).status_code == 400
    assert api.post(f"/api/channels/{cid}/comments/bulk-resolve", {"all": True}).json()["resolved"] >= 1
    assert api.get(f"/api/channels/{cid}/community").json()["totals"]["open"] == 0


def test_mentions_unsupported_notice(api, channel, monkeypatch):
    from huhqueue.connectors.fake import FakeConnector
    monkeypatch.setitem(FakeConnector.capabilities, "mentions", False)
    d = api.get(f"/api/channels/{channel['id']}/mentions").json()
    assert d["supported"] is False and d["items"] == [] and "aren't available" in d["message"]


def test_insights_ranges_previous_period_takeaways(api, core, channel):
    cid = channel["id"]
    _sent(core, cid, 4, days_ago=2, views=200, likes=20)
    _sent(core, cid, 2, days_ago=40, views=100, likes=5, prefix="old")
    d = api.get(f"/api/channels/{cid}/insights/summary?range=30d").json()
    assert d["totals"]["posts"] == 4 and d["previous_totals"]["posts"] == 2
    views = next(m for m in d["summary"] if m["key"] == "views")
    assert views["value"] == sum(200 + i for i in range(4)) and views["previous"] == 201 and views["delta"] > 0
    assert len(d["posts"]["this"]) == 4 and len(d["posts"]["previous"]) == 2
    assert len(d["comparison"]) >= 30 and "previous_views" in d["comparison"][0]
    assert sum(r["posts"] for r in d["comparison"]) == 4 and sum(r["previous_posts"] or 0 for r in d["comparison"]) == 2
    assert {c["id"] for c in d["categories"]} >= {"content_impact", "engagement", "visibility", "engagement_rate"}
    kinds = {t["kind"] for t in d["takeaways"]}
    assert "goal" in kinds and "top_post" in kinds and "views" in kinds
    assert api.get(f"/api/channels/{cid}/insights/summary?range=7d").json()["totals"]["posts"] == 4
    assert api.get(f"/api/channels/{cid}/insights/summary?range=mtd").status_code == 200
    start = (datetime.utcnow() - timedelta(days=45)).strftime("%Y-%m-%d")
    end = (datetime.utcnow() - timedelta(days=35)).strftime("%Y-%m-%d")
    c = api.get(f"/api/channels/{cid}/insights/summary?range=custom&start={start}&end={end}").json()
    assert c["range"] == "custom" and c["totals"]["posts"] == 2
    assert api.get(f"/api/channels/{cid}/insights/summary?range=custom").status_code == 400
    assert api.get(f"/api/channels/{cid}/insights/summary?range=custom&start={end}&end={start}").status_code == 400
    empty = api.get(f"/api/channels/{cid}/insights/summary?range=custom&start=2020-01-01&end=2020-01-10").json()
    assert empty["totals"]["posts"] == 0 and empty["takeaways"] == []


def test_insights_csv_and_share_as_post(api, core, channel):
    cid = channel["id"]
    _sent(core, cid, 3, days_ago=1)
    rows = list(csv.reader(io.StringIO(api.get(f"/api/channels/{cid}/insights/export.csv?range=7d").text)))
    assert rows[0][:4] == ["period", "published_at", "format", "caption"] and len(rows) == 1 + 3
    daily = list(csv.reader(io.StringIO(api.get(f"/api/channels/{cid}/insights/export.csv?range=7d&kind=daily").text)))
    assert daily[0][:2] == ["day", "previous_day"] and len(daily) >= 8
    r = api.post(f"/api/channels/{cid}/insights/share", {"range": "7d"}).json()
    assert "@huhbyte_demo" in r["caption"] and "Posts: 3" in r["caption"] and "draft" not in r
    r = api.post(f"/api/channels/{cid}/insights/share", {"range": "7d", "create_draft": True}).json()
    drafts = api.get(f"/api/posts?channel_id={cid}&status=draft").json()
    assert len(drafts) == 1 and drafts[0]["caption"] == r["caption"] and "insights" in drafts[0]["tags"]


def test_utm_and_rewrite_links(core, api, channel):
    st = {"utm_source": "instagram", "utm_medium": "social", "utm_campaign": "huhqueue"}
    assert add_utm("https://huhbyte.ai/x?a=1", st) == "https://huhbyte.ai/x?a=1&utm_source=instagram&utm_medium=social&utm_campaign=huhqueue"
    assert add_utm("https://huhbyte.ai/x?utm_source=me", st) == "https://huhbyte.ai/x?utm_source=me"
    api.patch(f"/api/channels/{channel['id']}/settings", {"utm_enabled": True})
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        out = rewrite_links(core, s, ch, "Read https://huhbyte.ai/post. Now!")
        assert out == "Read https://huhbyte.ai/post?utm_source=fake&utm_medium=social&utm_campaign=huhqueue. Now!"
        assert rewrite_links(core, s, ch, "no links") == "no links"


def test_worker_applies_utm_when_publishing(api, core, channel):
    cid = channel["id"]
    api.patch(f"/api/channels/{cid}/settings", {"utm_enabled": True})
    t = api.post("/api/posts", {"caption": "see https://huhbyte.ai/news", "channel_ids": [cid], "when": "custom",
                                "scheduled_at": future(60)}).json()["targets"][0]
    from huhqueue.worker import Worker
    with core.db.session() as s:
        ctx = Worker(core).build_context(s, s.get(PostTarget, t["id"]))
    assert "utm_source=fake" in ctx.caption


def test_actions_registered_for_every_channel_page_route():
    ids = {"channel.settings_get", "channel.settings_update", "channel.publish_summary", "channel.slots_generate",
           "channel.queue_shuffle", "channel.queue_clean", "channel.drafts_delete", "channel.queue_empty", "channel.community",
           "channel.comments_bulk_resolve", "channel.mentions", "channel.insights_summary", "channel.insights_export",
           "channel.insights_share"}
    assert ids <= set(reg.BY_ID)
    assert reg.get("channel.queue_empty").risk == "destructive" and reg.get("channel_queue_empty")


def test_ai_key_encrypted_and_never_echoed(api, core):
    r = api.put("/api/ai/settings", {"provider": "openai", "api_key": "sk-secret-123", "model": "gpt-4o-mini"}).json()
    assert r["has_key"] and "sk-secret-123" not in str(r) and "api_key" not in r
    assert "sk-secret-123" not in str(api.get("/api/ai/settings").json())
    with core.db.session() as s:
        stored = s.get(KV, "ai").value
    assert "sk-secret-123" not in str(stored) and stored["key_enc"]
    r = api.put("/api/ai/settings", {"clear_key": True}).json()
    assert r["has_key"] is False
