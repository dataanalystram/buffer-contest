"""Posting slots, the queue, and the daily rate guard.

* Every channel has weekly posting slots (weekday + HH:MM) in its own timezone.
* Posts in "queue" mode flow into the next free slot ("Next available").
  "Prioritize" puts a post at the front of the queue and the others shift down.
* "Custom" posts are pinned to a time; "now" posts go out on the next worker tick.
* The rate guard caps posts per channel per local day (default 2), unless a post
  carries the "major news" override.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Channel, PostTarget, Slot

COUNTED = ("scheduled", "sending", "processing", "needs_action", "sent")
HORIZON_DAYS = 120

PRESETS = {
    "huhbyte": {"days": [0, 1, 2, 3, 4, 5, 6], "times": ["09:30", "18:30"]},
    "weekdays-3": {"days": [0, 1, 2, 3, 4], "times": ["08:30", "12:30", "18:00"]},
    "daily-1": {"days": [0, 1, 2, 3, 4, 5, 6], "times": ["12:00"]},
}


class RateLimited(Exception):
    def __init__(self, message: str, day: date):
        super().__init__(message)
        self.message = message
        self.day = day


def tz(channel: Channel) -> ZoneInfo:
    try:
        return ZoneInfo(channel.timezone or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def to_utc_naive(local: datetime, zone: ZoneInfo) -> datetime:
    if local.tzinfo is None:
        local = local.replace(tzinfo=zone)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def to_local(utc_naive: datetime, zone: ZoneInfo) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(zone)


def local_day(utc_naive: datetime, zone: ZoneInfo) -> date:
    return to_local(utc_naive, zone).date()


def slot_times(channel: Channel, after_utc: datetime, days: int = HORIZON_DAYS):
    """Yield upcoming slot datetimes (naive UTC) strictly after `after_utc`."""
    zone = tz(channel)
    by_day: dict[int, list[str]] = {}
    off = {int(d) for d in (channel.meta or {}).get("disabled_days") or []}   # per-day on/off switch keeps the times
    for s in channel.slots:
        if s.weekday not in off:
            by_day.setdefault(s.weekday, []).append(s.time)
    if not by_day:
        return
    start = to_local(after_utc, zone).date()
    for i in range(days):
        d = start + timedelta(days=i)
        for t in sorted(set(by_day.get(d.weekday(), []))):
            hh, mm = (int(x) for x in t.split(":"))
            utc = to_utc_naive(datetime.combine(d, time(hh, mm)), zone)
            if utc > after_utc:
                yield utc


def day_count(session: Session, channel: Channel, day: date, exclude_id: str | None = None,
              statuses=COUNTED, extra: list[datetime] | None = None) -> int:
    zone = tz(channel)
    lo = to_utc_naive(datetime.combine(day, time(0, 0)), zone)
    hi = to_utc_naive(datetime.combine(day + timedelta(days=1), time(0, 0)), zone)
    q = select(PostTarget).where(PostTarget.channel_id == channel.id, PostTarget.status.in_(statuses))
    n = 0
    for t in session.scalars(q):
        if exclude_id and t.id == exclude_id:
            continue
        when = t.published_at if t.status == "sent" and t.published_at else t.scheduled_at
        if when and lo <= when < hi:
            n += 1
    for w in extra or []:
        if lo <= w < hi:
            n += 1
    return n


def check_rate(session: Session, channel: Channel, when_utc: datetime, target_id: str | None, override: bool) -> None:
    if override or not channel.daily_limit or channel.daily_limit <= 0:
        return
    day = local_day(when_utc, tz(channel))
    if day_count(session, channel, day, exclude_id=target_id) >= channel.daily_limit:
        raise RateLimited(
            f"{channel.username or channel.provider} already has {channel.daily_limit} post(s) on {day.isoformat()} "
            f"(daily limit). Pick another day or tick 'Major news override'.", day)


def reflow(session: Session, channel: Channel, now: datetime) -> list[PostTarget]:
    """Re-assign every queued post on a channel to slots, in queue order. Returns posts with no slot."""
    session.flush()
    queued = list(session.scalars(select(PostTarget).where(
        PostTarget.channel_id == channel.id, PostTarget.schedule_mode == "queue",
        PostTarget.status == "scheduled")))
    # prioritized first (newest priority on top), then by current position, then by age
    far = datetime.max
    queued.sort(key=lambda t: (0 if t.prioritized else 1,
                               -(t.updated_at.timestamp()) if t.prioritized else 0,
                               t.scheduled_at or far, t.created_at))
    queued_ids = {t.id for t in queued}
    pinned = [t for t in session.scalars(select(PostTarget).where(
        PostTarget.channel_id == channel.id, PostTarget.status.in_(COUNTED)))
        if t.id not in queued_ids]
    taken = {(t.published_at if t.status == "sent" and t.published_at else t.scheduled_at) for t in pinned}
    taken.discard(None)
    zone = tz(channel)
    per_day: dict[date, int] = {}
    for w in taken:
        per_day[local_day(w, zone)] = per_day.get(local_day(w, zone), 0) + 1
    unslotted: list[PostTarget] = []
    cands = list(slot_times(channel, now))
    for t in queued:
        assigned = None
        for cand in cands:
            if cand in taken:
                continue
            d = local_day(cand, zone)
            if not t.override_rate_limit and channel.daily_limit and per_day.get(d, 0) >= channel.daily_limit:
                continue
            assigned = cand
            break
        t.scheduled_at = assigned
        if assigned is None:
            unslotted.append(t)
            continue
        taken.add(assigned)
        d = local_day(assigned, zone)
        per_day[d] = per_day.get(d, 0) + 1
    return unslotted


def set_slots(session: Session, channel: Channel, slots: list[dict]) -> None:
    clean = set()
    for s in slots:
        wd = int(s["weekday"])
        hh, mm = str(s["time"]).split(":")[:2]
        hh, mm = int(hh), int(mm)
        if not (0 <= wd <= 6 and 0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError(f"Bad slot {s}")
        clean.add((wd, f"{hh:02d}:{mm:02d}"))
    channel.slots.clear()
    session.flush()
    for wd, t in sorted(clean):
        channel.slots.append(Slot(weekday=wd, time=t))


def preset_slots(name: str) -> list[dict]:
    p = PRESETS.get(name)
    if not p:
        raise ValueError(f"Unknown preset {name}. Options: {', '.join(PRESETS)}")
    return [{"weekday": d, "time": t} for d in p["days"] for t in p["times"]]


def next_day_same_time(when_utc: datetime, zone: ZoneInfo) -> datetime:
    loc = to_local(when_utc, zone)
    nxt = datetime.combine(loc.date() + timedelta(days=1), loc.time().replace(tzinfo=None))
    return to_utc_naive(nxt, zone)
