"""Daily notifications Buffer sends: an empty-queue alert per channel and a daily recap (yesterday's posts and views,
comments waiting for a reply, today's queue). Both go through events.record, so the bell, push webhook and email
switches in Settings › Notifications apply. Each runs at most once per local day (state in the settings KV table)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..models import KV, Channel, Comment, PostTarget
from .events import record

STATE_KEY = "digest.state"


def _local(core, now: datetime) -> datetime:
    tz = ZoneInfo(core.prefs().get("timezone") or "UTC")
    return now.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)


def run_due(core, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    local = _local(core, now)
    today = local.date().isoformat()
    recap_at = core.prefs().get("recap_time") or "08:00"
    with core.db.session() as s:
        row = s.get(KV, STATE_KEY) or KV(key=STATE_KEY, value={})
        st = dict(row.value or {})
        out = {"empty_queue": 0, "recap": 0}
        empty_done = dict(st.get("empty_queue") or {})
        for ch in s.scalars(select(Channel).where(Channel.status == "active", Channel.paused.is_(False))):
            if empty_done.get(ch.id) == today or not ch.slots:
                continue
            queued = s.scalar(select(func.count()).select_from(PostTarget).where(
                PostTarget.channel_id == ch.id, PostTarget.status.in_(("scheduled", "pending_approval"))))
            empty_done[ch.id] = today
            if not queued:
                record(core, s, f"@{ch.username}'s queue is empty - nothing is scheduled. Add a post to keep the streak going.",
                       level="warning", kind="empty_queue", channel_id=ch.id, notify=True)
                out["empty_queue"] += 1
        st["empty_queue"] = empty_done
        if st.get("recap") != today and local.strftime("%H:%M") >= recap_at:
            st["recap"] = today
            record(core, s, recap_text(s, now), kind="daily_recap", notify=True)
            out["recap"] = 1
        row.value = st
        s.merge(row)
    return out


def recap_text(s, now: datetime) -> str:
    since = now - timedelta(days=1)
    sent = list(s.scalars(select(PostTarget).where(PostTarget.status == "sent", PostTarget.published_at >= since)))
    views = sum(int((t.insights or {}).get("views") or 0) for t in sent)
    top = max(sent, key=lambda t: int((t.insights or {}).get("views") or 0), default=None)
    open_c = s.scalar(select(func.count()).select_from(Comment).where(Comment.status == "open", Comment.is_own.is_(False))) or 0
    today = s.scalar(select(func.count()).select_from(PostTarget).where(
        PostTarget.status == "scheduled", PostTarget.scheduled_at >= now, PostTarget.scheduled_at < now + timedelta(days=1))) or 0
    failed = s.scalar(select(func.count()).select_from(PostTarget).where(PostTarget.status == "failed")) or 0
    lines = [f"Daily recap: {len(sent)} post(s) published in the last 24 h ({views} views so far)."]
    if top is not None and (top.insights or {}).get("views"):
        lines.append(f"Top post: “{(top.caption or '')[:80]}” ({top.insights['views']} views).")
    lines.append(f"{open_c} comment(s) waiting for a reply. {today} post(s) scheduled for the next 24 h.")
    if failed:
        lines.append(f"{failed} failed post(s) need a look.")
    return " ".join(lines)
