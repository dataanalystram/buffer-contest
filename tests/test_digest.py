"""Empty-queue alerts, the daily recap and the email notification channel."""
from datetime import datetime, timedelta
from unittest import mock

from huhqueue.models import Event, PostTarget
from huhqueue.services import digest, events

from .conftest import future


def _events(core, kind):
    with core.db.session() as s:
        return [e.message for e in s.query(Event).filter(Event.kind == kind)]


def test_empty_queue_alert_once_per_day(api, core, channel):
    morning = datetime(2026, 10, 28, 6, 0)          # 07:00 Dublin, before the 08:00 recap
    assert digest.run_due(core, morning)["empty_queue"] == 1
    assert "queue is empty" in _events(core, "empty_queue")[0]
    assert digest.run_due(core, morning + timedelta(hours=1))["empty_queue"] == 0       # once a day
    assert digest.run_due(core, morning + timedelta(days=1))["empty_queue"] == 1        # next day again
    api.post("/api/posts", {"caption": "queued", "channel_ids": [channel["id"]], "when": "queue"})
    assert digest.run_due(core, morning + timedelta(days=2))["empty_queue"] == 0        # something scheduled


def test_daily_recap_after_recap_time(api, core, channel):
    api.put("/api/settings", {"recap_time": "09:30"})
    assert api.put("/api/settings", {"recap_time": "9.30"}).status_code == 400
    with core.db.session() as s:                          # a post published an hour ago, with views
        from huhqueue.models import Post
        p = Post(caption="sent one")
        s.add(p)
        s.flush()
        s.add(PostTarget(post_id=p.id, channel_id=channel["id"], status="sent", published_at=datetime.utcnow() - timedelta(hours=1),
                         insights={"views": 42}))
    early = datetime.utcnow().replace(hour=7, minute=0)   # 08:00 Dublin winter time: before 09:30
    assert digest.run_due(core, early)["recap"] == 0
    assert digest.run_due(core, early + timedelta(hours=3))["recap"] == 1
    assert digest.run_due(core, early + timedelta(hours=4))["recap"] == 0
    msg = _events(core, "daily_recap")[0]
    assert msg.startswith("Daily recap: 1 post(s)") and "42 views" in msg


def test_email_channel_per_kind(api, core):
    st = api.get("/api/settings").json()
    assert st["notify_email"]["failed"] is False and st["smtp_configured"] is False and "empty_queue" in st["notify_kinds"]
    assert api.put("/api/settings", {"notify_email_to": "not-an-email"}).status_code == 400
    api.put("/api/settings", {"notify_email_to": "ram@example.com", "notify_email": {"failed": True}})
    with mock.patch("huhqueue.teams.email_later") as send:
        assert events.email_notify(core, "failed", "Post failed") is True
        assert events.email_notify(core, "sent", "Sent") is False                # off for this kind
        send.assert_called_once()
        assert send.call_args[0][0] == "ram@example.com"
        api.put("/api/settings", {"notify_paused": True})
        assert events.email_notify(core, "failed", "x") is False                 # pause covers email too
