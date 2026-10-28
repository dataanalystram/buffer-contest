"""Insights extras: demographics, story insights saved before they expire, takeaways across all channels."""
from datetime import timedelta

import httpx
import respx

from huhqueue import actions as reg
from huhqueue.connectors.base import ChannelAuth
from huhqueue.connectors.fake import FakeConnector
from huhqueue.connectors.instagram import InstagramConnector
from huhqueue.models import Channel, PostTarget, StorySnapshot
from huhqueue.services import audience


def test_demographics_cached_with_percentages(api, channel):
    d = api.get("/api/insights/demographics").json()
    row = d["channels"][0]
    assert row["supported"] and row["fetched_at"]
    ages = {x["key"]: x for x in row["followers"]["age"]}
    assert ages["25-34"]["value"] == 520 and round(sum(x["pct"] for x in row["followers"]["age"])) == 100
    assert row["followers"]["country"][0] == {"key": "US", "label": "United States", "value": 480, "pct": 45.7}
    assert row["followers"]["gender"][0]["label"] == "Women"
    assert row["engaged"]["age"]
    again = api.get("/api/insights/demographics").json()["channels"][0]
    assert again["fetched_at"] == row["fetched_at"]          # cached for a day
    assert api.get("/api/insights/demographics?refresh=true").status_code == 200


def test_instagram_demographics_parses_breakdowns_and_notes_small_accounts(settings):
    ig = InstagramConnector(settings, http=httpx.Client())
    auth = ChannelAuth(id="ch", external_id="178", username="huh", access_token="tok")

    def resp(request):
        q = dict(request.url.params)
        if q["metric"] == "engaged_audience_demographics":
            return httpx.Response(400, json={"error": {"message": "Not enough viewers", "code": 100}})
        assert q["period"] == "lifetime" and q["metric_type"] == "total_value"
        res = {"age": [(["18-24"], 30), (["25-34"], 70)], "gender": [(["F"], 60), (["M"], 40)],
               "country": [(["IN"], 55), (["US"], 45)], "city": [(["Pune, Maharashtra"], 10)]}[q["breakdown"]]
        return httpx.Response(200, json={"data": [{"name": q["metric"], "total_value": {"breakdowns": [
            {"dimension_keys": [q["breakdown"]], "results": [{"dimension_values": dv, "value": v} for dv, v in res]}]}}]})
    with respx.mock:
        respx.get(url__regex=r".*/178/insights.*").mock(side_effect=resp)
        out = ig.audience_demographics(auth)
    assert out["followers"]["age"] == {"18-24": 30, "25-34": 70} and out["followers"]["country"]["IN"] == 55
    assert out["engaged"] == {} and any("Not enough viewers" in n for n in out["notes"])


def test_instagram_stories_and_story_insights(settings):
    ig = InstagramConnector(settings, http=httpx.Client())
    auth = ChannelAuth(id="ch", external_id="178", username="huh", access_token="tok")
    with respx.mock:
        respx.get(url__regex=r".*/178/stories.*").mock(return_value=httpx.Response(200, json={"data": [
            {"id": "st1", "media_type": "IMAGE", "media_url": "https://cdn/x.jpg", "timestamp": "2026-10-27T10:00:00+0000"}]}))

        def ins(request):
            q = dict(request.url.params)
            if q["metric"] == "navigation":
                return httpx.Response(200, json={"data": [{"name": "navigation", "total_value": {"breakdowns": [{"results": [
                    {"dimension_values": ["TAP_FORWARD"], "value": 40}, {"dimension_values": ["TAP_EXIT"], "value": 6}]}]}}]})
            return httpx.Response(200, json={"data": [{"name": m, "values": [{"value": 10}]} for m in q["metric"].split(",")]})
        respx.get(url__regex=r".*/st1/insights.*").mock(side_effect=ins)
        live = ig.list_stories(auth)
        m = ig.story_insights(auth, "st1")
    assert live[0]["external_id"] == "st1" and live[0]["timestamp"].startswith("2026-10-27")
    assert m["reach"] == 10 and m["tap_forward"] == 40 and m["tap_exit"] == 6


def test_stories_are_saved_and_survive_expiry(api, core, channel):
    now = audience.utcnow()
    FakeConnector.stories[:] = [{"external_id": "s-1", "media_type": "IMAGE", "media_url": "https://example.invalid/1.jpg",
                                 "timestamp": (now - timedelta(hours=3)).isoformat() + "+00:00", "permalink": "https://example.invalid/s/1"}]
    try:
        r = api.post("/api/insights/stories/sync").json()
        assert r["stories"] == 1 and not r["errors"]
        api.post("/api/insights/stories/sync")
        FakeConnector.stories.clear()                      # the story expired: the network no longer lists it
        api.post("/api/insights/stories/sync")
        d = api.get("/api/insights/stories").json()
        assert len(d["items"]) == 1
        it = d["items"][0]
        assert it["live"] and it["reads"] == 2 and it["metrics"]["reach"] > 0 and it["exit_rate"] is not None
        assert d["summary"]["stories"] == 1 and d["summary"]["avg_reach"] == it["metrics"]["reach"]
        with core.db.session() as s:
            row = s.query(StorySnapshot).one()
            row.posted_at = now - timedelta(hours=30)
        assert api.get("/api/insights/stories").json()["items"][0]["live"] is False
    finally:
        FakeConnector.stories.clear()


def test_worker_runs_stories_every_two_hours(core, channel):
    now = audience.utcnow()
    FakeConnector.stories[:] = [{"external_id": "s-9", "timestamp": now.isoformat() + "+00:00"}]
    try:
        assert audience.run_due(core, now)["stories"] == 1
        assert "stories" not in audience.run_due(core, now + timedelta(minutes=30))
        assert audience.run_due(core, now + timedelta(hours=2, minutes=1))["stories"] == 1
    finally:
        FakeConnector.stories.clear()


def test_takeaways_across_channels_and_feedback(api, core, channel):
    other = api.post("/api/connect/fake/token", {"access_token": "tok-5678", "username": "second"}).json()
    now = audience.utcnow()
    with core.db.session() as s:
        for cid, n, ins in ((channel["id"], 4, {"views": 1000, "likes": 90, "comments": 10}),
                            (other["id"], 1, {"views": 200, "likes": 2})):
            for i in range(n):
                api.post("/api/posts", {"caption": f"p{i} {cid}", "channel_ids": [cid], "when": "draft"})
        for t in s.query(PostTarget).all():
            t.status, t.published_at = "sent", now - timedelta(days=2)
            t.insights = {"views": 1000, "likes": 90, "comments": 10} if t.channel_id == channel["id"] else {"views": 200, "likes": 2}
            t.permalink = "https://example.invalid/p"
        # previous period: the second channel did better before
        t0 = s.query(PostTarget).filter_by(channel_id=other["id"]).first()
        s.add(PostTarget(post_id=t0.post_id, channel_id=other["id"], status="sent", published_at=now - timedelta(days=40),
                         insights={"views": 900}))
        s.add(PostTarget(post_id=t0.post_id, channel_id=channel["id"], status="sent", published_at=now - timedelta(days=45),
                         insights={"views": 100}))
    d = api.get("/api/insights/takeaways?range=30d").json()
    keys = {t["key"]: t for t in d["takeaways"]}
    assert d["channels"] == 2
    assert "top_grower" in keys and "@huhbyte_demo" in keys["top_grower"]["text"]
    assert "top_decliner" in keys and "@second" in keys["top_decliner"]["text"]
    assert "best_engagement" in keys and "top_post" in keys
    assert api.post("/api/insights/takeaways/feedback", {"key": "top_post", "helpful": True}).json()["helpful"] is True
    again = {t["key"]: t for t in api.get("/api/insights/takeaways").json()["takeaways"]}
    assert again["top_post"]["feedback"] is True
    assert api.post("/api/insights/takeaways/feedback", {"key": "", "helpful": True}).status_code == 400


def test_new_insights_actions_are_mcp_tools():
    import huhqueue.api.app  # noqa: F401
    tools = set(reg.mcp_tool_names())
    for a in ("insights.demographics", "insights.stories", "insights.stories_sync", "insights.takeaways", "insights.takeaway_feedback"):
        assert reg.BY_ID[a].tool in tools
