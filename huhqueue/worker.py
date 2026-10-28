"""The publishing worker.

Every tick it:
  1. refreshes tokens that are about to expire,
  2. claims due posts (scheduled -> sending), publishes them through their connector,
     and moves them to sent / processing / needs_action / failed (with retries + backoff),
  3. polls posts that the network is still processing,
  4. recovers posts stuck in 'sending' after a crash (connectors resume, never double-post),
  5. refreshes insights for recent sent posts, and syncs the community inbox.

Reliability rules: every network call is logged in publish_attempts; retries back off 60 s doubling to 1 h with
jitter (a network's Retry-After wins when longer); a rate-limited channel is held until its reset time without
spending the post's tries; posts missed while the app was down or asleep go out late within MISSED_GRACE, otherwise
move to the next free queue slot (queue mode) or wait for a person (custom time).
"""
from __future__ import annotations

import logging
import random
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy import func, or_, select, update

from .connectors import AuthError, ConnectorError, PermanentError, PublishContext, RetryableError, Unconfirmed
from .connectors.base import thread_parts
from .models import Channel, Media, PostTarget, PublishAttempt
from .security import redact
from .services import community, scheduling
from .services import media as media_svc
from .services.endcard import EndCardError, ensure_end_card
from .services.events import record

log = logging.getLogger("huhqueue.worker")

BACKOFF_BASE, BACKOFF_CAP = 60, 3600    # seconds: 60 s doubling to a 1 h cap
MISSED_GRACE = timedelta(hours=6)       # a post this late (app was down/asleep) is re-queued or flagged, not sent
PROCESSING_TIMEOUT = timedelta(minutes=45)
STALE_SENDING = timedelta(minutes=20)
INSIGHTS_EVERY = timedelta(hours=6)
INSIGHTS_WINDOW = timedelta(days=30)
COMMENTS_EVERY = timedelta(minutes=15)


def utcnow() -> datetime:
    return datetime.utcnow()


def backoff(attempt: int, retry_after: int | None = None) -> int:
    """Seconds before retry number `attempt` (1-based): equal jitter over 60 s * 2^(n-1), capped at 1 h,
    so failures spread out instead of hitting the network in lockstep. Retry-After wins when longer."""
    base = min(BACKOFF_CAP, BACKOFF_BASE * 2 ** max(0, attempt - 1))
    return max(retry_after or 0, base // 2 + random.randint(0, base // 2))


class Worker:
    def __init__(self, core):
        self.core = core
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.last_tick: datetime | None = None
        self._last_comments: datetime | None = None

    # ------------------------------------------------------------------ loop
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="huhqueue-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # never let the loop die
                log.exception("worker tick failed")
            self._stop.wait(self.core.settings.worker_interval)

    # ------------------------------------------------------------------ tick
    def tick(self, now: datetime | None = None) -> dict:
        with self._lock:
            now = now or utcnow()
            self.last_tick = now
            stats = {"refreshed": self.refresh_tokens(now), "expiring": self.warn_expiring(now),
                     "recovered": self.recover_stale(now), "missed": self.requeue_missed(now)}
            stats["published"] = self.publish_due(now)
            stats["polled"] = self.poll_processing(now)
            stats["insights"] = self.refresh_insights(now)
            try:   # recurring posts, evergreen recycling, RSS auto-post (huhqueue/services/growth.py)
                from .services.growth import run_due_automations
                stats["automations"] = run_due_automations(self.core, now)
            except Exception:
                log.exception("automations failed")
            if not self._last_comments or now - self._last_comments >= COMMENTS_EVERY:
                self._last_comments = now
                try:
                    stats["comments"] = community.sync_all(self.core)
                except Exception:
                    log.exception("comment sync failed")
            try:
                from .services import analytics
                stats["followers"] = analytics.run_due(self.core, now)
            except Exception:
                log.exception("follower snapshot failed")
            try:   # outgoing webhooks (Zapier / Make / n8n)
                from .services import webhooks
                stats["webhooks"] = webhooks.deliver_due(self.core, now)
            except Exception:
                log.exception("webhook delivery failed")
            try:   # story insights before they expire, daily demographics (services/audience.py)
                from .services import audience
                stats["audience"] = audience.run_due(self.core, now)
            except Exception:
                log.exception("story / demographics refresh failed")
            try:
                from .services import reports
                stats["reports"] = reports.run_due(self.core, now)
            except Exception:
                log.exception("scheduled report failed")
            try:   # empty-queue alerts + daily recap (services/digest.py)
                from .services import digest
                stats["digest"] = digest.run_due(self.core, now)
            except Exception:
                log.exception("daily digest failed")
            return stats

    # ------------------------------------------------------------------ tokens
    def refresh_tokens(self, now: datetime) -> int:
        n = 0
        with self.core.db.session() as s:
            for ch in s.scalars(select(Channel).where(Channel.status == "active")):
                conn = self.core.connectors.get(ch.provider)
                if not conn:
                    continue
                auth = self.core.channel_auth(ch)
                if not conn.needs_refresh(auth, now):
                    continue
                try:
                    bundle = conn.refresh(auth)
                    if bundle:
                        self.core.store_tokens(ch, bundle)
                        record(self.core, s, f"Token refreshed for {ch.username}", channel_id=ch.id)
                        n += 1
                except AuthError as e:
                    ch.status, ch.status_detail = "needs_reconnect", e.message
                    record(self.core, s, f"{ch.username}: login expired - reconnect the channel ({e.message})",
                           level="error", kind="reconnect", channel_id=ch.id, notify=True)
                except ConnectorError as e:
                    log.warning("token refresh for %s failed: %s", ch.id, e.message)
        return n

    def warn_expiring(self, now: datetime) -> int:
        """Tokens that can't be refreshed (LinkedIn personal: 60 days): warn N days before expiry (once per token),
        then flag the channel needs_reconnect when it has expired."""
        n = 0
        with self.core.db.session() as s:
            for ch in s.scalars(select(Channel).where(Channel.status == "active", Channel.token_expires_at.is_not(None))):
                conn = self.core.connectors.get(ch.provider)
                days = getattr(conn, "expiry_warning_days", 0) if conn else 0
                if not days or ch.refresh_token_enc:
                    continue
                exp = ch.token_expires_at
                if exp <= now:
                    ch.status, ch.status_detail = "needs_reconnect", "Login expired - reconnect the channel"
                    record(self.core, s, f"{ch.username}: {conn.name} login expired - reconnect the channel",
                           level="error", kind="reconnect", channel_id=ch.id, notify=True)
                    n += 1
                elif exp - now <= timedelta(days=days) and (ch.meta or {}).get("expiry_warned_for") != exp.isoformat():
                    left = max(1, round((exp - now).total_seconds() / 86400))
                    ch.meta = {**(ch.meta or {}), "expiry_warned_for": exp.isoformat()}
                    record(self.core, s, f"{ch.username}: {conn.name} login expires in {left} day{'s' if left != 1 else ''}"
                           f" ({exp:%d %b}) - reconnect it to keep posting", level="warning", kind="token_expiring",
                           channel_id=ch.id, notify=True)
                    n += 1
        return n

    def heartbeat(self, now: datetime | None = None) -> dict:
        now = now or utcnow()
        age = int((now - self.last_tick).total_seconds()) if self.last_tick else None
        return {"thread_alive": bool(self._thread and self._thread.is_alive()),
                "last_tick": self.last_tick.isoformat(timespec="seconds") + "Z" if self.last_tick else None,
                "age_seconds": age, "stale": age is None or age > max(300, 4 * self.core.settings.worker_interval),
                "interval": self.core.settings.worker_interval, "due_now": len(self._due_ids(now))}

    def recover_stale(self, now: datetime) -> int:
        """Posts stuck in 'sending' after a crash go back to the queue. publish() resumes from the provider_state the
        connector checkpointed, so a post that already reached the network is confirmed, never sent again."""
        with self.core.db.session() as s:
            r = s.execute(update(PostTarget).where(PostTarget.status == "sending",
                                                    PostTarget.locked_at < now - STALE_SENDING)
                          .values(status="scheduled", next_attempt_at=now))
            s.execute(update(PublishAttempt).where(PublishAttempt.outcome == "started",
                                                   PublishAttempt.started_at < utcnow() - STALE_SENDING)
                      .values(outcome="interrupted", finished_at=utcnow()))
            return r.rowcount or 0

    def requeue_missed(self, now: datetime) -> int:
        """Scheduled posts whose time passed while the app was down or asleep (InstaBox pauses after 30 idle min).
        Up to MISSED_GRACE late they simply go out on this tick; later than that, a queued post moves to the next free
        slot and a pinned (custom/now) post waits for a person with a notification - never silently dropped.
        Runs every tick before publishing (one indexed query), so posts held by a paused or disconnected channel
        are caught the moment it is back, before they could fire hours late."""
        n, reflow = 0, {}
        with self.core.db.session() as s:
            q = (select(PostTarget).join(Channel, Channel.id == PostTarget.channel_id)
                 .where(PostTarget.status == "scheduled", PostTarget.next_attempt_at.is_(None),
                        PostTarget.scheduled_at < now - MISSED_GRACE,
                        Channel.paused.is_(False), Channel.status == "active"))
            for t in list(s.scalars(q)):
                ch = t.channel
                if ch.rate_limited_until and ch.rate_limited_until > now - MISSED_GRACE:
                    continue   # held back by the network's rate limit, not missed
                if t.schedule_mode == "queue":
                    reflow.setdefault(ch.id, (ch, []))[1].append(t)
                    continue
                t.status, t.last_error = "needs_action", "Missed its scheduled time while HuhQueue was offline"
                record(self.core, s, f"Missed: was due {t.scheduled_at:%d %b %H:%M} UTC while HuhQueue was offline - "
                                     "reschedule or publish it now.", level="warning", kind="missed", target_id=t.id,
                       channel_id=ch.id, notify=True)
                n += 1
            for ch, missed in reflow.values():
                scheduling.reflow(s, ch, now)
                for t in missed:
                    n += 1
                    if t.scheduled_at:
                        record(self.core, s, f"Missed its slot while HuhQueue was offline - moved to the next free slot "
                                             f"({t.scheduled_at:%d %b %H:%M} UTC)", level="warning", kind="missed",
                               target_id=t.id, channel_id=ch.id)
                    else:
                        t.status, t.last_error = "needs_action", "Missed its slot and the queue has no free slot"
                        record(self.core, s, "Missed its slot while HuhQueue was offline and the queue has no free "
                                             "slot - reschedule it.", level="warning", kind="missed", target_id=t.id,
                               channel_id=ch.id, notify=True)
        return n

    # ------------------------------------------------------------------ publishing
    def _due_ids(self, now: datetime) -> list[str]:
        with self.core.db.session() as s:
            q = (select(PostTarget.id).join(Channel, Channel.id == PostTarget.channel_id)
                 .where(PostTarget.status == "scheduled", PostTarget.scheduled_at.is_not(None),
                        PostTarget.scheduled_at <= now,
                        or_(PostTarget.next_attempt_at.is_(None), PostTarget.next_attempt_at <= now),
                        Channel.paused.is_(False), Channel.status == "active",
                        or_(Channel.rate_limited_until.is_(None), Channel.rate_limited_until <= now))
                 .order_by(PostTarget.scheduled_at))
            from . import safety
            if safety.is_paused(self.core):   # kill switch: posts agents created wait until a person resumes agents
                q = q.where(~safety.held_post_filter())
            return list(s.scalars(q))

    def publish_due(self, now: datetime) -> int:
        n = 0
        for tid in self._due_ids(now):
            if self._claim(tid, now):
                self.process(tid, now)
                n += 1
        return n

    def _claim(self, tid: str, now: datetime) -> bool:
        with self.core.db.session() as s:
            r = s.execute(update(PostTarget).where(PostTarget.id == tid, PostTarget.status == "scheduled")
                          .values(status="sending", locked_at=now))
            return (r.rowcount or 0) == 1

    def build_context(self, s, t: PostTarget) -> PublishContext:
        ch = t.channel
        prefs = self.core.prefs()
        opts = dict(t.options or {})
        caption = t.caption or ""
        from .services.growth import tag_for_publish
        caption, opts = tag_for_publish(self.core, s, t, caption, opts)   # per-channel UTM tags / short links
        medias = [s.get(Media, mid) for mid in t.media_list]
        medias = [m for m in medias if m]
        if ch.provider == "instagram":
            for m in medias:  # Instagram only takes JPEG images: convert PNG/WebP before publishing
                media_svc.ensure_jpeg(self.core, m)
        items = [media_svc.to_item(self.core, m) for m in medias]
        has_video = any(m.kind == "video" for m in medias)
        cta = opts.get("follow_cta", "off") if has_video else "off"
        cta_text = prefs.get("cta_text") or f"Follow {prefs.get('cta_handle', '@huhbyte')} for daily AI news"
        if cta in ("card", "both"):
            for i, m in enumerate(medias):
                if m.kind == "video":
                    name = ensure_end_card(self.core, m, prefs.get("cta_card_text") or f"Follow {prefs.get('cta_handle', '@huhbyte')}\nfor daily AI news")
                    it = items[i]
                    it.path = self.core.settings.media_dir / name
                    it.size = it.path.stat().st_size
                    it.mime = "video/mp4"
                    base = self.core.public_base_url()
                    it.public_url = f"{base}/m/{m.public_token}/{name}" if base else None
        if cta in ("comment", "both"):
            conn = self.core.connector(ch.provider)
            if conn.capabilities.get("first_comment"):
                fc = (opts.get("first_comment") or "").strip()
                if cta_text not in fc:
                    opts["first_comment"] = (fc + "\n\n" + cta_text).strip() if fc else cta_text
            elif cta_text not in caption:
                caption = (caption.rstrip() + "\n\n" + cta_text).strip()
        from .api.channel_pages import rewrite_links      # channel settings: UTM tags / short links
        caption = rewrite_links(self.core, s, ch, caption)
        attachments = {}
        for k, v in opts.items():   # e.g. yt_thumbnail_media_id -> attachments["yt_thumbnail"]
            if k.endswith("_media_id") and v and (am := s.get(Media, v)):
                attachments[k[:-len("_media_id")]] = media_svc.to_item(self.core, am)
        parts = thread_parts(opts)
        if any(p["media_ids"] for p in parts):       # media of each thread part, one list per part
            attachments["thread"] = [[media_svc.to_item(self.core, am) for mid in p["media_ids"] if (am := s.get(Media, mid))]
                                     for p in parts]
        return PublishContext(target_id=t.id, channel=self.core.channel_auth(ch), caption=caption, media=items,
                              options=opts, state=dict(t.provider_state or {}),
                              recent_publishes=self.recent_publishes(s, ch.id, t.id), attachments=attachments)

    def recent_publishes(self, s, channel_id: str, exclude_id: str = "") -> int:
        """Posts sent on this channel in the rolling last 24 hours (for per-network publish caps)."""
        from sqlalchemy import func
        since = datetime.utcnow() - timedelta(hours=24)
        q = select(func.count()).select_from(PostTarget).where(
            PostTarget.channel_id == channel_id, PostTarget.status == "sent", PostTarget.published_at >= since,
            PostTarget.id != exclude_id)
        return int(s.scalar(q) or 0)

    def process(self, tid: str, now: datetime, polling: bool = False) -> str:
        core = self.core
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            ch = t.channel
            conn = core.connectors.get(ch.provider)
            if not conn:
                t.status, t.last_error = "failed", f"No connector installed for {ch.provider}"
                return t.status
            if not polling and t.external_id:   # the network already has it (a lost 'sent'): never post again
                t.status, t.last_error, t.locked_at = "sent", "", None
                return t.status
            if ch.rate_limited_until and ch.rate_limited_until > now:   # limit hit earlier in this tick: wait
                t.status = "processing" if polling else "scheduled"
                return t.status
            # "Notify me": hand over to the human at the scheduled time
            if not polling and (t.options or {}).get("posting_method") == "notify":
                t.status = "needs_action"
                record(core, s, f"Time to post on {ch.provider} ({ch.username}) - open HuhQueue, copy the caption and "
                                f"download the media.", level="warning", kind="notify", target_id=t.id,
                       channel_id=ch.id, notify=True)
                return t.status
            # daily rate guard, re-checked at send time
            if not polling and not t.override_rate_limit and ch.daily_limit:
                day = scheduling.local_day(now, scheduling.tz(ch))
                sent_today = scheduling.day_count(s, ch, day, exclude_id=t.id,
                                                  statuses=("sending", "processing", "sent", "needs_action"))
                if sent_today >= ch.daily_limit:
                    if t.schedule_mode == "queue":
                        t.status = "scheduled"
                        s.flush()
                        scheduling.reflow(s, ch, now + timedelta(hours=1))
                    else:
                        t.status = "scheduled"
                        t.scheduled_at = scheduling.next_day_same_time(t.scheduled_at or now, scheduling.tz(ch))
                    record(core, s, f"Daily limit ({ch.daily_limit}) reached for {ch.username} - moved to "
                                    f"{t.scheduled_at}. Use 'Major news override' to send anyway.",
                           level="warning", kind="rate_guard", target_id=t.id, channel_id=ch.id)
                    return t.status
            if not polling:
                from .services.posts import find_duplicates, load_media
                dups = find_duplicates(s, ch, load_media(s, t.media_list), t.post.topic_key, exclude_target_id=t.id)
                if dups:
                    t.status, t.last_error = "failed", dups[0]["message"]
                    record(core, s, f"Blocked: {dups[0]['message']}", level="error", kind="duplicate", target_id=t.id,
                           channel_id=ch.id, notify=True)
                    return t.status
                record(core, s, f"Sending to {ch.provider}…", target_id=t.id, channel_id=ch.id)
            try:
                ctx = self.build_context(s, t)
            except (EndCardError, media_svc.MediaError) as e:
                t.status, t.last_error = "failed", str(e)
                record(core, s, f"Failed: {e}", level="error", kind="failed", target_id=t.id, channel_id=ch.id, notify=True)
                return t.status
            def checkpoint(state: dict) -> None:   # connectors save progress before irreversible calls
                t.provider_state = dict(state)
                s.commit()
            ctx.on_checkpoint = checkpoint
            att = None
            if not polling:   # log the attempt before the call, so a crash leaves an 'interrupted' row
                n = s.scalar(select(func.count()).select_from(PublishAttempt).where(PublishAttempt.target_id == tid))
                att = PublishAttempt(target_id=tid, channel_id=ch.id, attempt=(n or 0) + 1, started_at=utcnow())
                s.add(att)
            s.commit()   # don't hold the database while talking to the network
            try:
                outcome = conn.poll(ctx) if polling else conn.publish(ctx)
            except AuthError as e:
                t.provider_state = ctx.state
                ch.status, ch.status_detail = "needs_reconnect", e.message
                t.status, t.last_error = "failed", e.message
                record(core, s, f"Failed: {e.message} - reconnect {ch.username}", level="error", kind="reconnect",
                       target_id=t.id, channel_id=ch.id, notify=True)
                return self._log(s, t, att, "auth", e)
            except Unconfirmed as e:
                t.provider_state = ctx.state
                t.status, t.last_error = "needs_action", e.message
                record(core, s, e.message, level="warning", kind="unconfirmed", target_id=t.id, channel_id=ch.id,
                       notify=True)
                return self._log(s, t, att, "unconfirmed", e)
            except RetryableError as e:
                t.provider_state = ctx.state
                held = bool(e.rate_limited and e.retry_after)
                if held:   # the whole account is limited: hold the channel until the reset
                    until = now + timedelta(seconds=e.retry_after)
                    ch.rate_limited_until = max(until, ch.rate_limited_until or until)
                status = self._retry(s, t, e.message, e.retry_after, now, count=not held)
                return self._log(s, t, att, "retry" if status == "scheduled" else "failed", e)
            except (PermanentError, ConnectorError) as e:
                t.provider_state = ctx.state
                t.status, t.last_error = "failed", e.message
                record(core, s, f"Failed: {e.message}", level="error", kind="failed", target_id=t.id, channel_id=ch.id,
                       notify=True)
                return self._log(s, t, att, "failed", e)
            except Exception as e:  # bug or unexpected response: retry, but log it
                log.exception("unexpected error publishing %s", tid)
                t.provider_state = ctx.state
                status = self._retry(s, t, f"Unexpected error: {e.__class__.__name__}: {e}", None, now)
                return self._log(s, t, att, "retry" if status == "scheduled" else "failed",
                                 ConnectorError(t.last_error, code=e.__class__.__name__))
            t.provider_state = outcome.state or ctx.state
            for w in outcome.warnings:
                record(core, s, w, level="warning", target_id=t.id, channel_id=ch.id)
            if outcome.status == "published":
                t.status, t.external_id, t.permalink = "sent", outcome.external_id, outcome.permalink
                t.published_at, t.last_error, t.locked_at = now, "", None
                record(core, s, outcome.message or "Sent", level="success", kind="sent", target_id=t.id,
                       channel_id=ch.id, notify=True)
                return self._log(s, t, att, "published")
            if outcome.status == "inbox":
                t.status, t.last_error = "needs_action", ""
                record(core, s, outcome.message, level="warning", kind="notify", target_id=t.id, channel_id=ch.id,
                       notify=True)
                return self._log(s, t, att, "inbox")
            started = (t.provider_state or {}).get("started_at")
            if not started:
                t.provider_state = {**(t.provider_state or {}), "started_at": now.isoformat()}
            elif now - datetime.fromisoformat(started) > PROCESSING_TIMEOUT:
                t.status, t.last_error = "failed", "Timed out waiting for the network to process the media"
                record(core, s, f"Failed: {t.last_error}", level="error", kind="failed", target_id=t.id,
                       channel_id=ch.id, notify=True)
                return self._log(s, t, att, "failed", ConnectorError(t.last_error, code="processing_timeout"))
            t.status = "processing"
            t.next_attempt_at = now + timedelta(seconds=outcome.poll_after_seconds)
            record(core, s, outcome.message or "Processing…", target_id=t.id, channel_id=ch.id)
            return self._log(s, t, att, "processing")

    def _log(self, s, t: PostTarget, att: PublishAttempt | None, outcome: str, e: ConnectorError | None = None) -> str:
        """Finish the attempt row (polls only get one when they end the wait). Returns the target's status."""
        if att is None and outcome != "processing":
            n = s.scalar(select(func.count()).select_from(PublishAttempt).where(PublishAttempt.target_id == t.id))
            att = PublishAttempt(target_id=t.id, channel_id=t.channel_id, attempt=(n or 0) + 1, started_at=utcnow())
            s.add(att)
        if att is not None:
            att.outcome, att.finished_at = outcome, utcnow()
            if e is not None:
                att.error_code, att.error = str(e.code or "")[:80], redact(e.message or "")[:1000]
                att.retry_after, att.http_status = e.retry_after, e.status
        return t.status

    def _retry(self, s, t: PostTarget, msg: str, retry_after: int | None, now: datetime, count: bool = True) -> str:
        """Back to 'scheduled' after a delay; connectors' publish() resumes from provider_state, so a retry never
        double-posts. A rate-limit wait (count=False) doesn't use up one of the post's tries."""
        if count:
            t.attempts = (t.attempts or 0) + 1
        t.last_error = msg
        if t.attempts >= self.core.settings.max_attempts:
            t.status = "failed"
            record(self.core, s, f"Failed after {t.attempts} attempts: {msg}", level="error", kind="failed",
                   target_id=t.id, channel_id=t.channel_id, notify=True)
            return t.status
        delay = backoff(max(1, t.attempts), retry_after)
        t.next_attempt_at = now + timedelta(seconds=delay)
        t.status = "scheduled"
        what = f"Retry {t.attempts}/{self.core.settings.max_attempts - 1}" if count else "Rate limited - retry"
        record(self.core, s, f"{what} in {delay // 60 or 1} min: {msg}", level="warning", target_id=t.id,
               channel_id=t.channel_id)
        return t.status

    def poll_processing(self, now: datetime) -> int:
        with self.core.db.session() as s:
            ids = list(s.scalars(select(PostTarget.id).join(Channel, Channel.id == PostTarget.channel_id).where(
                PostTarget.status == "processing",
                or_(PostTarget.next_attempt_at.is_(None), PostTarget.next_attempt_at <= now),
                or_(Channel.rate_limited_until.is_(None), Channel.rate_limited_until <= now))))
        for tid in ids:
            self.process(tid, now, polling=True)
        return len(ids)

    # ------------------------------------------------------------------ insights
    def refresh_insights(self, now: datetime, target_id: str | None = None, limit: int = 10) -> int:
        n = 0
        with self.core.db.session() as s:
            q = select(PostTarget).where(PostTarget.status == "sent", PostTarget.external_id != "")
            if target_id:
                q = q.where(PostTarget.id == target_id)
            else:
                q = q.where(PostTarget.published_at >= now - INSIGHTS_WINDOW,
                            or_(PostTarget.insights_at.is_(None), PostTarget.insights_at <= now - INSIGHTS_EVERY)).limit(limit)
            for t in s.scalars(q):
                conn = self.core.connectors.get(t.channel.provider)
                if not conn or not conn.capabilities.get("insights"):
                    continue
                ctx = PublishContext(t.id, self.core.channel_auth(t.channel), t.caption, [], t.options or {},
                                     dict(t.provider_state or {}))
                try:
                    t.insights = conn.fetch_insights(ctx, t.external_id) or {}
                    n += 1
                except ConnectorError as e:
                    t.insights = {**(t.insights or {}), "error": e.message}
                t.insights_at = now
        return n


def run_forever(core) -> None:  # pragma: no cover - used by `huhqueue worker`
    w = Worker(core)
    log.info("worker started (every %ss)", core.settings.worker_interval)
    while True:
        try:
            w.tick()
        except Exception:
            log.exception("tick failed")
        time.sleep(core.settings.worker_interval)
