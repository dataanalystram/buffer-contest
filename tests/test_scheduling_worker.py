"""Queue slots, rate guard, and the publishing worker (with the demo connector + monkeypatched connectors)."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from huhqueue.connectors import PublishOutcome, RetryableError, TokenBundle
from huhqueue.connectors.fake import FakeConnector
from huhqueue.models import Channel, Event, Media, PostTarget
from huhqueue.services import posts as posts_svc
from huhqueue.services import scheduling
from huhqueue.worker import Worker

from .conftest import future, make_image, upload


def ch_row(core, cid):
    with core.db.session() as s:
        return s.get(Channel, cid)


def target(core, tid):
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        s.expunge(t)
        return t


def create(core, cid, caption="hello", when="queue", **kw):
    return posts_svc.create_post(core, {"caption": caption, "channel_ids": [cid], "when": when, **kw})["targets"][0]


def test_slot_times_respect_timezone_and_dst(core, channel):
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        scheduling.set_slots(s, ch, [{"weekday": 6, "time": "09:30"}])  # Sundays 09:30 Dublin
        # 2026-10-25 is the Sunday clocks go back in Ireland (IST -> GMT)
        before = datetime(2026, 10, 18, 0, 0)
        times = list(scheduling.slot_times(ch, before, days=14))
    assert times[0] == datetime(2026, 10, 18, 8, 30)   # IST = UTC+1
    assert times[1] == datetime(2026, 10, 25, 9, 30)   # GMT = UTC+0


def test_queue_fills_slots_in_order_with_daily_limit(core, channel):
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        ch.daily_limit = 1
        scheduling.set_slots(s, ch, [{"weekday": d, "time": t} for d in range(7) for t in ("09:00", "18:00")])
    a, b, c = (create(core, channel["id"], f"p{i}") for i in range(3))
    days = [datetime.fromisoformat(x["scheduled_at"][:-1]).date() for x in (a, b, c)]
    assert len(set(days)) == 3  # limit 1/day -> three different days
    assert days == sorted(days)


def test_prioritize_jumps_the_queue(core, channel):
    a = create(core, channel["id"], "first")
    b = create(core, channel["id"], "second")
    p = create(core, channel["id"], "urgent", when="prioritize")
    ta, tb, tp = (target(core, x["id"]) for x in (a, b, p))
    assert tp.scheduled_at < ta.scheduled_at < tb.scheduled_at


def test_custom_time_rate_guard_and_override(core, channel):
    with core.db.session() as s:
        s.get(Channel, channel["id"]).daily_limit = 1
    at = (datetime.now(ZoneInfo("Europe/Dublin")) + timedelta(days=3)).replace(hour=12, minute=0).strftime("%Y-%m-%dT%H:%M")
    create(core, channel["id"], "one", when="custom", scheduled_at=at)
    with pytest.raises(posts_svc.PostError) as e:
        create(core, channel["id"], "two", when="custom", scheduled_at=at[:-2] + "30")
    assert e.value.status == 409 and "Major news" in e.value.message
    ok = create(core, channel["id"], "BREAKING", when="custom", scheduled_at=at[:-2] + "30", override_rate_limit=True)
    assert ok["status"] == "scheduled" and ok["override_rate_limit"]


def test_custom_in_past_rejected(core, channel):
    with pytest.raises(posts_svc.PostError, match="past"):
        create(core, channel["id"], when="custom", scheduled_at="2020-01-01T10:00")


def test_worker_publishes_due_post(core, channel):
    t = create(core, channel["id"], "go now", when="now")
    stats = Worker(core).tick()
    assert stats["published"] == 1
    done = target(core, t["id"])
    assert done.status == "sent" and done.external_id.startswith("fk_") and done.permalink
    assert FakeConnector.published[-1]["caption"] == "go now"
    with core.db.session() as s:
        msgs = [e.message for e in s.query(Event).filter_by(target_id=t["id"]).order_by(Event.id)]
    assert any("Sending" in m for m in msgs) and any("Published" in m for m in msgs)


def test_worker_retries_then_succeeds(core, channel):
    t = create(core, channel["id"], "RETRY me", when="now")
    w = Worker(core)
    w.tick()
    mid = target(core, t["id"])
    assert mid.status == "scheduled" and mid.attempts == 1 and "temporary outage" in mid.last_error
    w.tick(datetime.utcnow() + timedelta(minutes=2))
    assert target(core, t["id"]).status == "sent"


def test_worker_gives_up_after_max_attempts(core, channel, monkeypatch):
    def always(self, ctx):
        raise RetryableError("still down", retry_after=1)
    monkeypatch.setattr(FakeConnector, "publish", always)
    t = create(core, channel["id"], "x", when="now")
    w = Worker(core)
    now = datetime.utcnow()
    for i in range(core.settings.max_attempts):
        w.tick(now + timedelta(hours=2 * i))
    final = target(core, t["id"])
    assert final.status == "failed" and final.attempts == core.settings.max_attempts


def test_permanent_failure_and_auth_failure(core, channel):
    bad = create(core, channel["id"], "FAIL", when="now")
    Worker(core).tick()
    assert target(core, bad["id"]).status == "failed"
    auth = create(core, channel["id"], "AUTHFAIL", when="now")
    Worker(core).tick()
    assert target(core, auth["id"]).status == "failed"
    assert ch_row(core, channel["id"]).status == "needs_reconnect"
    # a reconnect-needed channel does not publish anything else
    nxt = create(core, channel["id"], "later", when="now")
    Worker(core).tick()
    assert target(core, nxt["id"]).status == "scheduled"


def test_processing_then_published(core, channel):
    t = create(core, channel["id"], "SLOW", when="now")
    w = Worker(core)
    w.tick()
    assert target(core, t["id"]).status == "processing"
    w.tick(datetime.utcnow() + timedelta(seconds=5))
    assert target(core, t["id"]).status == "sent"


def test_notify_mode_hands_over(core, channel):
    t = create(core, channel["id"], "manual", when="now", options={"posting_method": "notify"})
    Worker(core).tick()
    assert target(core, t["id"]).status == "needs_action"
    assert not FakeConnector.published


def test_daily_limit_rechecked_at_send_time(core, channel):
    with core.db.session() as s:
        s.get(Channel, channel["id"]).daily_limit = 1
    a = create(core, channel["id"], "a", when="now")
    Worker(core).tick()
    assert target(core, a["id"]).status == "sent"
    # an API caller pinned a second post into today by bypassing the guard... then turned the override off
    b = create(core, channel["id"], "b", when="now", override_rate_limit=True)
    with core.db.session() as s:
        s.get(PostTarget, b["id"]).override_rate_limit = False
    Worker(core).tick()
    tb = target(core, b["id"])
    assert tb.status == "scheduled" and tb.scheduled_at > datetime.utcnow() + timedelta(hours=12)


def test_paused_channel_holds_posts(core, channel):
    with core.db.session() as s:
        s.get(Channel, channel["id"]).paused = True
    t = create(core, channel["id"], "x", when="now")
    Worker(core).tick()
    assert target(core, t["id"]).status == "scheduled"


def test_cta_first_comment_for_video_posts(core, channel, tmp_path, monkeypatch):
    seen = {}

    def capture(self, ctx):
        seen.update(caption=ctx.caption, options=ctx.options, media=ctx.media)
        return PublishOutcome("published", external_id="x")
    monkeypatch.setattr(FakeConnector, "publish", capture)
    import subprocess
    vid = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=10", "-t", "1",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    from huhqueue.services import media as media_svc
    m = media_svc.save_file(core, vid, "v.mp4")
    create(core, channel["id"], "video!", when="now", media_ids=[m.id], options={"follow_cta": "both"})
    Worker(core).tick()
    assert "Follow @huhbyte" in seen["options"]["first_comment"]
    assert "-endcard-" in seen["media"][0].path.name and seen["media"][0].path.exists()
    from huhqueue.services.endcard import probe
    assert probe(seen["media"][0].path)["duration"] > 3.0  # 1 s clip + 2.5 s end card


def test_token_refresh_job(core, channel):
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        ch.token_expires_at = datetime.utcnow() + timedelta(hours=2)
    assert Worker(core).refresh_tokens(datetime.utcnow()) == 1
    assert ch_row(core, channel["id"]).token_expires_at > datetime.utcnow() + timedelta(days=50)


def test_crash_recovery_resets_stale_sending(core, channel):
    t = create(core, channel["id"], "x", when="now")
    with core.db.session() as s:
        row = s.get(PostTarget, t["id"])
        row.status, row.locked_at = "sending", datetime.utcnow() - timedelta(hours=1)
    stats = Worker(core).tick()
    assert stats["recovered"] == 1 and target(core, t["id"]).status == "sent"


def test_insights_refresh(core, channel):
    t = create(core, channel["id"], "x", when="now")
    w = Worker(core)
    w.tick()
    w.refresh_insights(datetime.utcnow(), target_id=t["id"])
    assert target(core, t["id"]).insights["views"] > 0


# ------------------------------------------------------------------ formats, dedup, never-trim
def test_suggest_format_heuristics(core, settings, tmp_path):
    from pathlib import Path as P
    from huhqueue.connectors import MediaItem
    from huhqueue.connectors.instagram import InstagramConnector
    from huhqueue.connectors.tiktok import TikTokConnector
    from huhqueue.connectors.youtube import YouTubeConnector
    img = lambda i: MediaItem(id=str(i), kind="image", mime="image/jpeg", path=P("/x"), filename="x")
    vid = lambda d=30, w=1080, h=1920: MediaItem(id="v", kind="video", mime="video/mp4", path=P("/v"), filename="v", duration=d, width=w, height=h)
    ig, tt, yt = InstagramConnector(settings), TikTokConnector(settings), YouTubeConnector(settings)
    assert ig.suggest_format("hi", [vid()])[0] == "reel"
    assert ig.suggest_format("hi", [img(1), img(2)])[0] == "carousel"
    assert ig.suggest_format("1. one\n2. two\n3. three\n4. four", [img(1)]) == ("carousel", "Text-heavy → split it into carousel slides")
    assert ig.suggest_format("short", [img(1)])[0] == "feed"
    assert tt.suggest_format("x", [img(1), img(2)])[0] == "photo" and tt.suggest_format("x", [vid()])[0] == "video"
    assert yt.suggest_format("x", [vid()])[0] == "short"
    assert yt.suggest_format("x", [vid(d=400)])[0] == "video" and yt.suggest_format("x", [vid(w=1920, h=1080)])[0] == "video"
    from huhqueue.connectors import BrandRules
    assert "short_too_long" in {i.code for i in yt.validate("x", [vid(d=400)], {"yt_format": "short"}, BrandRules())}
    assert "format_mismatch" in {i.code for i in tt.validate("x", [img(1)], {"tt_format": "video"}, BrandRules())}


def test_dedup_same_media_and_topic_blocked_per_channel(core, channel, tmp_path):
    from huhqueue.services import media as media_svc
    other = posts_svc.create_post  # noqa
    p1 = make_image(tmp_path / "a.png")
    m1 = media_svc.save_file(core, p1, "a.png")
    p2 = make_image(tmp_path / "copy-of-a.png")          # byte-identical re-upload -> same hash, new id
    m2 = media_svc.save_file(core, p2, "copy-of-a.png")
    assert m1.sha256 == m2.sha256 and m1.id != m2.id
    create(core, channel["id"], "first", media_ids=[m1.id], topic_key="2026-10-22 GPT-6")
    with pytest.raises(posts_svc.PostError) as e:
        create(core, channel["id"], "again, other format", media_ids=[m2.id], options={"ig_type": "story"})
    assert e.value.status == 409 and e.value.issues[channel["id"]][0]["code"] == "duplicate_media"
    with pytest.raises(posts_svc.PostError) as e:
        create(core, channel["id"], "same story new art", topic_key="2026-10-22-gpt-6")
    assert e.value.issues[channel["id"]][0]["code"] == "duplicate_topic"
    # drafts are allowed (warning only) ...
    d = create(core, channel["id"], "draft copy", when="draft", media_ids=[m2.id])
    assert d["status"] == "draft"
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        issues = posts_svc.validate_one(core, ch, "x", [s.get(Media, m2.id)], {}, exclude_target_id=d["id"])
    assert any(i["code"] == "duplicate_media" for i in issues)
    # ... but scheduling it is refused, and the worker refuses too if one slips through
    with pytest.raises(posts_svc.PostError):
        posts_svc.update_target(core, d["id"], {"when": "queue"})
    with core.db.session() as s:
        row = s.get(PostTarget, d["id"])
        row.status, row.scheduled_at = "scheduled", datetime.utcnow() - timedelta(minutes=1)
    Worker(core).tick()
    assert target(core, d["id"]).status == "failed" and "never posted" in target(core, d["id"]).last_error


def test_dedup_allows_other_channels(core, channel, tmp_path):
    from huhqueue.services import media as media_svc
    m = media_svc.save_file(core, make_image(tmp_path / "z.png"), "z.png")
    other = posts_svc.create_post(core, {"caption": "x", "channel_ids": [channel["id"]], "when": "queue", "media_ids": [m.id], "topic_key": "t1"})
    from huhqueue.services import oauth
    from huhqueue.connectors.fake import FakeConnector
    b, prof = FakeConnector(core.settings).connect_with_token("tok-xyz9", {"username": "second"})
    cid2 = oauth.upsert_channel(core, "fake", b, prof)
    ok = posts_svc.create_post(core, {"caption": "x", "channel_ids": [cid2], "when": "queue", "media_ids": [m.id], "topic_key": "t1"})
    assert ok["targets"][0]["status"] == "scheduled" and other


def test_end_card_never_trims_video(core, tmp_path):
    import subprocess
    from huhqueue.services.endcard import append_end_card, probe
    src = tmp_path / "long.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=24", "-f", "lavfi", "-i", "sine=f=300",
                    "-t", "7.3", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    before = probe(src)["duration"]
    out = append_end_card(src, tmp_path / "out.mp4", "Follow @huhbyte")
    after = probe(out)["duration"]
    assert after >= before + 2.4 and abs(after - (before + 2.5)) < 0.35
    assert probe(src)["duration"] == before  # original untouched
