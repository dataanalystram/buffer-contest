"""Analytics (Buffer Analyze parity): audience growth snapshots, Reels watch time, post types, hashtags, hashtag groups,
campaigns, best time heat-map + "use as posting slots", custom report sections/filters, CSV, IG 2026 metrics."""
import csv
import io
from datetime import datetime, timedelta

import respx

from huhqueue.connectors import PublishContext
from huhqueue.models import FollowerSnapshot, Post, PostTarget
from huhqueue.services import analytics, reports
from huhqueue.worker import Worker


def add(core, channel_id, days_ago, insights, caption="", fmt="", campaign="", tags=None, hour=None):
    with core.db.session() as s:
        p = Post(caption=caption, campaign=campaign, tags=tags or [])
        s.add(p)
        s.flush()
        when = datetime.utcnow() - timedelta(days=days_ago, hours=1)
        if hour is not None:
            when = when.replace(hour=hour, minute=5)
        s.add(PostTarget(post_id=p.id, channel_id=channel_id, status="sent", published_at=when,
                         options={"fk_format": fmt} if fmt else {}, insights=insights))


def snap(core, channel_id, day, followers):
    with core.db.session() as s:
        s.add(FollowerSnapshot(channel_id=channel_id, day=day, followers=followers))


# ----------------------------------------------------------------------------------------------- audience growth
def test_follower_snapshot_once_per_day_and_refreshes_profile(api, core, channel):
    assert analytics.snapshot_followers(core) == 1
    assert analytics.snapshot_followers(core) == 0                       # one per channel per day
    with core.db.session() as s:
        row = s.query(FollowerSnapshot).one()
        assert row.followers == 1240 and row.media_count == 57            # fake fetch_profile's fresh numbers
    assert api.get("/api/channels").json()[0]["followers_count"] == 1240


def test_worker_takes_daily_snapshot(api, core, channel):
    now = datetime.utcnow()
    assert Worker(core).tick(now)["followers"] == 1
    assert Worker(core).tick(now + timedelta(minutes=5))["followers"] == 0
    assert Worker(core).tick(now + timedelta(days=1))["followers"] == 1


def test_audience_growth_vs_previous_period(api, core, channel):
    today = datetime.utcnow().date()
    for back, n in ((13, 900), (8, 950), (6, 1000), (3, 1030), (0, 1100)):
        snap(core, channel["id"], (today - timedelta(days=back)).isoformat(), n)
    a = api.get("/api/reports/audience?days=7").json()
    assert a["net"] == 150 and a["previous_net"] == 50 and a["change_pct"] == 200.0   # start = 950 carried into the period
    ch = a["channels"][0]
    assert ch["start"] == 950 and ch["end"] == 1100 and ch["growth_pct"] == round(100 * 150 / 950, 2)
    vals = [d["followers"] for d in a["series"]]
    assert vals[-1] == 1100 and None not in vals                           # missing days carry the last value forward
    assert a["series"][-1]["followers"] == 1100 and a["followers"] == 1100


def test_audience_without_history_explains(api, core, channel):
    a = api.get("/api/reports/audience?days=30").json()
    assert a["net"] is None and "once a day" in a["note"]


# ----------------------------------------------------------------------------------------------- posts / reels / types
def test_reels_watch_time_and_types(api, core, channel):
    add(core, channel["id"], 1, {"views": 4000, "reach": 3000, "likes": 200, "engagement_rate": 8.0, "avg_watch_time": 6.5,
                                 "watch_time": 26000.0, "reels_skip_rate": 40.0}, "reel one", fmt="reel")
    add(core, channel["id"], 2, {"views": 2000, "reach": 1500, "likes": 60, "engagement_rate": 5.0, "avg_watch_time": 3.5,
                                 "watch_time": 7000.0, "reels_skip_rate": 50.0}, "reel two", fmt="reel")
    add(core, channel["id"], 2, {"views": 500, "reach": 400, "likes": 30, "engagement_rate": 9.0}, "a photo", fmt="post")
    add(core, channel["id"], 10, {"views": 1000, "reach": 900, "avg_watch_time": 2.0}, "old reel", fmt="reel")
    d = api.get("/api/reports/summary?days=7").json()
    r = d["reels"]
    assert r["count"] == 2 and r["views"] == 6000 and r["avg_watch_time"] == 5.0 and r["watch_time"] == 33000.0
    assert r["skip_rate"] == 45.0 and r["previous"]["count"] == 1 and r["previous"]["avg_watch_time"] == 2.0
    assert r["top"][0]["caption"] == "reel one"
    t = {x["format"]: x for x in d["types"]}
    assert t["reel"]["posts"] == 2 and t["reel"]["avg_reach"] == 2250.0 and t["post"]["engagement_rate"] == 9.0
    assert len(d["posts"]) == 3 and d["posts"][0]["insights"]["views"] == 4000


def test_filters_by_format_campaign_and_tag(api, core, channel):
    add(core, channel["id"], 1, {"views": 100, "reach": 80}, "r", fmt="reel", campaign="Launch", tags=["ai"])
    add(core, channel["id"], 1, {"views": 50, "reach": 40}, "p", fmt="post", campaign="Launch", tags=["news"])
    add(core, channel["id"], 9, {"views": 10, "reach": 9}, "prev", fmt="reel", campaign="Launch", tags=["ai"])
    k = lambda d: {x["metric"]: x for x in d["kpis"]}  # noqa: E731
    d = api.get("/api/reports/summary?days=7&format=reel").json()
    assert k(d)["views"]["current"] == 100 and k(d)["views"]["previous"] == 10 and k(d)["posts"]["current"] == 1
    assert k(api.get("/api/reports/summary?days=7&campaign=launch").json())["posts"]["current"] == 2
    assert k(api.get("/api/reports/summary?days=7&tag=news").json())["views"]["current"] == 50
    assert api.get("/api/reports/summary?days=7&campaign=launch").json()["filters"]["campaign"] == "launch"


# ----------------------------------------------------------------------------------------------- hashtags / campaigns
def test_hashtag_and_group_stats(api, core, channel):
    api.put("/api/library/hashtag_groups", {"items": [{"id": "ai", "name": "AI news", "text": "#AI #TechNews"}]})
    add(core, channel["id"], 1, {"views": 900, "reach": 1000, "engagement_rate": 6.0}, "big news #AI #TechNews")
    add(core, channel["id"], 2, {"views": 300, "reach": 600, "engagement_rate": 4.0}, "more #ai and #Robots")
    add(core, channel["id"], 3, {"views": 100, "reach": 200, "engagement_rate": 2.0}, "no tags here")
    h = api.get("/api/reports/summary?days=7").json()["hashtags"]
    tags = {x["hashtag"]: x for x in h["hashtags"]}
    assert tags["#ai"]["posts"] == 2 and tags["#ai"]["avg_reach"] == 800.0 and tags["#robots"]["posts"] == 1
    assert h["hashtags"][0]["hashtag"] in ("#ai", "#technews")
    g = h["groups"][0]
    assert g["name"] == "AI news" and g["posts"] == 2 and g["avg_reach"] == 800.0
    assert g["vs_others_reach_pct"] == 300.0                                # 800 vs 200 for posts without the group


def test_campaign_and_tag_performance(api, core, channel):
    add(core, channel["id"], 1, {"views": 100, "reach": 90, "engagement_rate": 5.0}, "a", campaign="GPT-6 week", tags=["ai"])
    add(core, channel["id"], 2, {"views": 300, "reach": 250, "engagement_rate": 7.0}, "b", campaign="GPT-6 week", tags=["ai", "video"])
    add(core, channel["id"], 2, {"views": 50, "reach": 40}, "c", campaign="YC", tags=[])
    c = api.get("/api/reports/summary?days=7").json()["campaigns"]
    assert c["campaigns"][0] == {**c["campaigns"][0], "campaign": "GPT-6 week", "posts": 2, "reach": 340, "views": 400, "engagement_rate": 6.0}
    assert {t["tag"]: t["posts"] for t in c["tags"]} == {"ai": 2, "video": 1}


# ----------------------------------------------------------------------------------------------- best time
def test_best_time_heatmap_top_slot_and_apply(api, core, channel):
    for i in range(6):                                   # the planted best hour: Tue 18:00 (channel tz Europe/Dublin)
        add(core, channel["id"], 0, {"engagement_rate": 12.0 + i}, f"tue {i}")
    with core.db.session() as s:                          # move them onto a Tuesday 18:05 local (17:05/18:05 UTC by season)
        from huhqueue.services import scheduling
        from zoneinfo import ZoneInfo
        rows = list(s.query(PostTarget))
        base = datetime.utcnow().date()
        tue = base - timedelta(days=(base.weekday() - 1) % 7 or 7)
        for t in rows:
            local = datetime(tue.year, tue.month, tue.day, 18, 5, tzinfo=ZoneInfo("Europe/Dublin"))
            t.published_at = local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
        assert scheduling.tz(rows[0].channel)
    for i in range(6):
        add(core, channel["id"], 1 + i, {"engagement_rate": 1.0}, f"meh {i}", hour=6)
    bt = api.get(f"/api/reports/summary?days=30&channel_id={channel['id']}").json()["best_times"]
    assert bt["top"][0]["day"] == "Tue" and bt["top"][0]["hour"] == 18 and bt["enough_data"]
    out = api.post("/api/insights/best-times/apply", {"channel_id": channel["id"], "count": 2})
    assert out.status_code == 200, out.text
    slots = out.json()["slots"]
    assert len(slots) == 2 and {"weekday": 1, "hour": "18"} in [{"weekday": x["weekday"], "hour": x["time"][:2]} for x in slots]
    assert len(api.get(f"/api/channels/{channel['id']}/slots").json()) == 2


def test_best_time_apply_needs_data_and_channel(api, core, channel):
    assert api.post("/api/insights/best-times/apply", {"channel_id": channel["id"]}).status_code == 400
    assert api.post("/api/insights/best-times/apply", {}).status_code == 400


# ----------------------------------------------------------------------------------------------- custom report → PDF / CSV
def test_custom_sections_pdf_and_csv(api, core, channel):
    snap(core, channel["id"], (datetime.utcnow().date() - timedelta(days=3)).isoformat(), 1000)
    snap(core, channel["id"], datetime.utcnow().date().isoformat(), 1100)
    add(core, channel["id"], 1, {"views": 4000, "reach": 3000, "likes": 200, "engagement_rate": 8.0, "avg_watch_time": 6.5},
        "reel #AI", fmt="reel", campaign="Launch")
    full = api.get("/api/reports/report.pdf?days=7")
    small = api.get("/api/reports/report.pdf?days=7&sections=overview")
    assert full.status_code == small.status_code == 200
    assert full.content.startswith(b"%PDF") and len(full.content) > len(small.content)
    assert api.get("/api/reports/report.pdf?days=7&sections=bogus").status_code == 400
    r = api.get("/api/reports/report.csv?days=7&sections=overview,audience,posts,reels,hashtags,campaigns")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and ".csv" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    flat = [c for row in rows for c in row]
    assert ["metric", "this_period", "previous_period", "change_pct"] in rows
    assert "followers_total" in flat and "1100" in flat and "#ai" in flat and "Launch" in flat and "avg_watch_time" in flat
    only = list(csv.reader(io.StringIO(api.get("/api/reports/report.csv?days=7&sections=hashtags").text)))
    assert not any(row[:1] == ["metric"] for row in only)


def test_pdf_with_every_section_and_much_data_flows_pages(api, core, channel):
    for i in range(40):
        add(core, channel["id"], i % 7, {"views": 100 * i, "reach": 80 * i, "engagement_rate": 1.0 + i % 5, "avg_watch_time": 3.0},
            f"post {i} #tag{i % 9} #AI", fmt="reel" if i % 2 else "post", campaign=f"C{i % 4}", tags=[f"t{i % 3}"])
    pdf = reports.render_pdf(reports.report_data(core, "7"))
    assert pdf.count(b"/Type /Page ") >= 3


def test_schedule_keeps_builder_choices(api, core, channel):
    d = api.put("/api/reports/schedule", {"enabled": True, "sections": ["overview", "reels"], "format": "reel", "campaign": "Launch"}).json()
    assert d["sections"] == ["overview", "reels"] and d["format"] == "reel" and d["campaign"] == "Launch"
    assert api.put("/api/reports/schedule", {"sections": ["nope"]}).status_code == 400
    out = api.post("/api/reports/send", {"days": "7", "sections": "overview,reels", "format": "reel"}).json()
    assert out["file"].endswith(".pdf")


def test_new_actions_registered(api):
    from huhqueue import actions as reg
    for aid in ("report.csv", "report.audience", "report.audience_snapshot", "channel.best_times_apply", "report.archive_download"):
        assert reg.get(aid), aid
    r = api.post("/api/actions/report.audience:run", {"input": {"days": "30"}}).json()
    assert r["status"] == "done" and "series" in r["output"]


# ----------------------------------------------------------------------------------------------- Instagram 2026 metrics
G = "https://graph.instagram.com/v24.0"


def _ig(core):
    return core.connectors["instagram"]


@respx.mock
def test_ig_reel_watch_time_saved(core):
    from tests.test_instagram import auth  # noqa: F401  (same auth helper)
    ig = _ig(core)
    base = ig._url("M/insights")
    route = respx.get(base).respond(json={"data": [
        {"name": "views", "values": [{"value": 5000}]}, {"name": "reach", "values": [{"value": 4000}]},
        {"name": "likes", "values": [{"value": 300}]}, {"name": "total_interactions", "values": [{"value": 400}]},
        {"name": "ig_reels_avg_watch_time", "values": [{"value": 7250}]},
        {"name": "ig_reels_video_view_total_time", "values": [{"value": 36250000}]},
        {"name": "reels_skip_rate", "values": [{"value": 41.5}]}]})
    out = ig.fetch_insights(PublishContext("t", auth(), "", [], {}, {"type": "reel"}), "M")
    q = route.calls.last.request.url.params["metric"]
    assert "ig_reels_avg_watch_time" in q and "impressions" not in q and "plays" not in q
    assert out["avg_watch_time"] == 7.25 and out["watch_time"] == 36250.0 and out["engagement_rate"] == 10.0


@respx.mock
def test_ig_falls_back_to_base_metrics_when_one_is_unsupported(core):
    from tests.test_instagram import auth
    ig = _ig(core)
    calls = []

    def handler(request):
        m = request.url.params["metric"]
        calls.append(m)
        if "profile_visits" in m:
            return respx.MockResponse(400, json={"error": {"message": "(#100) metric not supported", "code": 100, "type": "OAuthException"}})
        return respx.MockResponse(200, json={"data": [{"name": "reach", "values": [{"value": 100}]},
                                                      {"name": "likes", "values": [{"value": 10}]}]})
    respx.get(ig._url("M/insights")).mock(side_effect=handler)
    out = ig.fetch_insights(PublishContext("t", auth(), "", [], {}, {"type": "feed"}), "M")
    assert len(calls) == 2 and "profile_visits" not in calls[1] and out["reach"] == 100 and out["engagement_rate"] == 10.0
