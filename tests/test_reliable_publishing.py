"""M3 reliable publishing: error classification, attempt log, jittered backoff, rate-limit holds, missed-post requeue,
worker heartbeat and never posting twice."""
from datetime import datetime, timedelta

import httpx
import pytest
import respx

from huhqueue.connectors import (ChannelAuth, PermanentError, PublishContext, PublishOutcome, RetryableError,
                                 Unconfirmed)
from huhqueue.connectors.fake import FakeConnector
from huhqueue.connectors.instagram import InstagramConnector
from huhqueue.connectors.linkedin import LinkedInConnector
from huhqueue.connectors.tiktok import TikTokConnector
from huhqueue.connectors.x import API, XConnector
from huhqueue.connectors.youtube import YouTubeConnector
from huhqueue.models import Channel, Event, PostTarget, PublishAttempt
from huhqueue.services import scheduling
from huhqueue.worker import BACKOFF_CAP, MISSED_GRACE, Worker, backoff

from .test_scheduling_worker import create, target


def resp(status, body=None, headers=None):
    return httpx.Response(status, json=body if body is not None else {}, headers=headers or {},
                          request=httpx.Request("GET", "https://example.invalid"))


CONNECTORS = [InstagramConnector, YouTubeConnector, XConnector, TikTokConnector, LinkedInConnector]


# --------------------------------------------------------------------------- error classification
@pytest.mark.parametrize("cls", CONNECTORS)
def test_status_codes_classify_the_same_on_every_network(cls, settings):
    c = cls(settings, httpx.Client())
    for status in (408, 429, 500, 502, 503):
        with pytest.raises(RetryableError) as e:
            c._handle(resp(status, {"error": {"message": "boom"}}))
        assert e.value.status == status
    with pytest.raises(PermanentError) as e:
        c._handle(resp(400, {"error": {"message": "caption too long"}}))
    assert e.value.status == 400 and not e.value.retryable


@pytest.mark.parametrize("cls", CONNECTORS)
def test_retry_after_header_wins_when_longer(cls, settings):
    c = cls(settings, httpx.Client())
    with pytest.raises(RetryableError) as e:
        c._handle(resp(429, {"error": {"message": "slow down"}}, {"Retry-After": "7200"}))
    assert e.value.retry_after == 7200 and e.value.rate_limited
    with pytest.raises(RetryableError) as e:   # an HTTP-date Retry-After works too
        when = datetime.utcnow() + timedelta(hours=3)
        c._handle(resp(503, {}, {"Retry-After": when.strftime("%a, %d %b %Y %H:%M:%S GMT")}))
    assert 3 * 3600 - 120 <= e.value.retry_after <= 3 * 3600 and not e.value.rate_limited


@respx.mock
def test_timeouts_are_retryable(settings):
    respx.post(f"{API}/tweets").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(RetryableError):
        XConnector(settings, httpx.Client())._req("POST", f"{API}/tweets", "T", json={})


# --------------------------------------------------------------------------- backoff
def test_backoff_doubles_with_equal_jitter_capped_at_an_hour():
    for n in range(1, 10):
        base = min(BACKOFF_CAP, 60 * 2 ** (n - 1))
        delays = {backoff(n) for _ in range(50)}
        assert all(base // 2 <= d <= base for d in delays), (n, delays)
        assert len(delays) > 1   # jittered
    assert backoff(1, retry_after=5000) == 5000          # Retry-After wins when larger
    assert backoff(7, retry_after=10) >= BACKOFF_CAP // 2  # ...and never shortens the backoff


# --------------------------------------------------------------------------- attempt log
def test_attempt_log_rows_and_api(core, api, channel):
    t = create(core, channel["id"], "RETRY please", when="now")
    w = Worker(core)
    w.tick()
    w.tick(datetime.utcnow() + timedelta(hours=2))
    assert target(core, t["id"]).status == "sent"
    rows = api.get(f"/api/targets/{t['id']}/attempts").json()
    assert [r["outcome"] for r in rows] == ["retry", "published"] and [r["attempt"] for r in rows] == [1, 2]
    assert rows[0]["retry_after"] == 1 and "temporary outage" in rows[0]["error"] and rows[0]["finished_at"]
    assert api.get("/api/targets/pt_nope/attempts").status_code == 404


def test_attempt_errors_are_redacted(core, channel, monkeypatch):
    def leak(self, ctx):
        raise PermanentError("bad request https://graph.x/?access_token=SECRET123", code="100", status=400)
    monkeypatch.setattr(FakeConnector, "publish", leak)
    t = create(core, channel["id"], "x", when="now")
    Worker(core).tick()
    with core.db.session() as s:
        a = s.query(PublishAttempt).filter_by(target_id=t["id"]).one()
        assert a.outcome == "failed" and a.http_status == 400 and a.error_code == "100"
        assert "SECRET123" not in a.error


# --------------------------------------------------------------------------- rate-limit reset
def test_rate_limited_channel_is_held_without_burning_tries(core, channel, monkeypatch):
    calls = []

    def limited(self, ctx):
        calls.append(ctx.caption)
        if len(calls) == 1:
            raise RetryableError("429 slow down", code="rate_limit", retry_after=600, status=429)
        return self._done(ctx)
    monkeypatch.setattr(FakeConnector, "publish", limited)
    a = create(core, channel["id"], "first", when="now")
    b = create(core, channel["id"], "second", when="now")
    now = datetime.utcnow()
    w = Worker(core)
    w.tick(now)
    assert calls == ["first"]                     # the second post was not attempted into a known limit
    with core.db.session() as s:
        until = s.get(Channel, channel["id"]).rate_limited_until
    assert now + timedelta(seconds=590) <= until <= now + timedelta(seconds=610)
    ta = target(core, a["id"])
    assert ta.status == "scheduled" and ta.attempts == 0   # a rate-limit wait is not one of the post's tries
    w.tick(now + timedelta(minutes=5))
    assert calls == ["first"]
    w.tick(now + timedelta(minutes=11))
    assert target(core, a["id"]).status == "sent" and target(core, b["id"]).status == "sent"


# --------------------------------------------------------------------------- missed posts
def _backdate(core, tid, when):
    with core.db.session() as s:
        s.get(PostTarget, tid).scheduled_at = when


def test_missed_post_within_grace_goes_out_late(core, channel):
    t = create(core, channel["id"], "late but fine", when="now")
    _backdate(core, t["id"], datetime.utcnow() - MISSED_GRACE + timedelta(minutes=30))
    Worker(core).tick()
    assert target(core, t["id"]).status == "sent"


def test_missed_queued_post_moves_to_next_free_slot(core, channel):
    t = create(core, channel["id"], "queued")
    _backdate(core, t["id"], datetime.utcnow() - timedelta(hours=7))
    now = datetime.utcnow()
    stats = Worker(core).tick(now)
    moved = target(core, t["id"])
    assert stats["missed"] == 1 and stats["published"] == 0
    assert moved.status == "scheduled" and moved.scheduled_at > now
    with core.db.session() as s:
        ch = s.get(Channel, channel["id"])
        assert moved.scheduled_at == next(scheduling.slot_times(ch, now))
        assert s.query(Event).filter_by(target_id=t["id"], kind="missed").count() == 1


def test_missed_custom_post_waits_for_a_person(core, channel):
    t = create(core, channel["id"], "pinned", when="now")
    _backdate(core, t["id"], datetime.utcnow() - timedelta(hours=8))
    Worker(core).tick()
    assert target(core, t["id"]).status == "needs_action" and not FakeConnector.published
    with core.db.session() as s:
        ev = s.query(Event).filter_by(target_id=t["id"], kind="missed").one()
        assert ev.notify


# --------------------------------------------------------------------------- heartbeat
def test_health_reports_worker_heartbeat(core, api, channel, app):
    h = api.get("/api/health").json()
    assert h["ok"] and h["worker"]["last_tick"] is None and h["worker"]["thread_alive"] is False
    create(core, channel["id"], "due", when="now")
    assert api.get("/api/health").json()["worker"]["due_now"] == 1
    app.state.worker.tick()
    w = api.get("/api/health").json()["worker"]
    assert w["last_tick"] and 0 <= w["age_seconds"] < 60 and not w["stale"] and w["due_now"] == 0 and w["interval"] > 0


# --------------------------------------------------------------------------- never post twice
class Crash(BaseException):
    """Simulates the process dying mid-publish (not an Exception, so nothing inside the worker catches it)."""


def test_crash_after_checkpoint_resumes_instead_of_reposting(core, channel, monkeypatch):
    sent = []

    def publish(self, ctx):
        if ctx.state.get("remote_id"):   # resume: the network already has it
            return PublishOutcome("published", external_id=ctx.state["remote_id"], state=ctx.state)
        sent.append(ctx.caption)
        ctx.state["remote_id"] = "r1"
        ctx.checkpoint()
        raise Crash()
    monkeypatch.setattr(FakeConnector, "publish", publish)
    t = create(core, channel["id"], "once only", when="now")
    now = datetime.utcnow()
    w = Worker(core)
    with pytest.raises(Crash):
        w.tick(now)
    assert target(core, t["id"]).provider_state["remote_id"] == "r1"   # survived the crash
    with core.db.session() as s:   # the crash is recovered once the attempt is stale
        s.query(PublishAttempt).update({"started_at": datetime.utcnow() - timedelta(minutes=30)})
    w.tick(now + timedelta(minutes=25))
    done = target(core, t["id"])
    assert done.status == "sent" and done.external_id == "r1" and sent == ["once only"]
    with core.db.session() as s:
        assert [a.outcome for a in s.query(PublishAttempt).order_by(PublishAttempt.id)] == ["interrupted", "published"]


def _xctx(**state):
    return PublishContext("t", ChannelAuth(id="c", external_id="42", username="huhbyte", access_token="T"),
                          "Big news https://huhbyte.ai/x", [], {},
                          {"uploaded": True, "media_ids": [], "pending": [], **state})


@respx.mock
def test_x_lost_reply_is_confirmed_not_reposted(settings):
    xc = XConnector(settings, httpx.Client())
    create_call = respx.post(f"{API}/tweets").mock(side_effect=httpx.ReadTimeout("lost reply"))
    ctx = _xctx()
    with pytest.raises(RetryableError):
        xc.poll(ctx)
    assert ctx.state["publish_called"]          # ambiguous: we don't know if X posted it
    later = (datetime.utcnow() + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    respx.get(f"{API}/users/42/tweets").respond(json={"data": [
        {"id": "OLD", "text": "Big news https://t.co/old", "created_at": "2020-01-01T00:00:00.000Z"},
        {"id": "T9", "text": "Big news https://t.co/abc", "created_at": later}]})
    out = xc.poll(ctx)
    assert out.status == "published" and out.external_id == "T9" and create_call.call_count == 1


@respx.mock
def test_x_unconfirmed_when_not_found_and_clean_retry_when_never_sent(settings):
    xc = XConnector(settings, httpx.Client())
    respx.get(f"{API}/users/42/tweets").respond(json={"data": []})
    with pytest.raises(Unconfirmed):
        xc.poll(_xctx(publish_called=True, publish_called_at=datetime.utcnow().isoformat()))
    respx.post(f"{API}/tweets").mock(side_effect=httpx.ConnectError("offline"))
    ctx = _xctx()
    with pytest.raises(RetryableError):
        xc.poll(ctx)
    assert "publish_called" not in ctx.state   # never connected: safe to send on the next try


def test_unconfirmed_post_needs_a_person(core, channel, monkeypatch):
    def unsure(self, ctx):
        raise Unconfirmed("may already be posted")
    monkeypatch.setattr(FakeConnector, "publish", unsure)
    t = create(core, channel["id"], "x", when="now")
    Worker(core).tick()
    assert target(core, t["id"]).status == "needs_action"
    with core.db.session() as s:
        assert s.query(PublishAttempt).filter_by(target_id=t["id"]).one().outcome == "unconfirmed"
