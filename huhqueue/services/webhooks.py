"""Outgoing webhooks for Zapier, Make, n8n, IFTTT or your own server (Buffer's integrations, free).

Every event HuhQueue records (post sent / failed, approval requested, new comment, channel needs reconnecting, ...)
becomes a JSON POST to each active webhook that subscribed to it. Deliveries are queued in `webhook_deliveries` in the
same transaction as the event and sent by the worker, with retries (1 min, 5 min, 30 min, 2 h, 12 h). A webhook that
keeps failing is switched off after 15 failed deliveries in a row.

Each request carries:
  X-HuhQueue-Event: post.sent
  X-HuhQueue-Delivery: wd_...
  X-HuhQueue-Timestamp: 1730000000
  X-HuhQueue-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>.<raw body>" with the webhook's secret>
Zapier "Catch Hook", Make "Custom webhook" and n8n's "Webhook" node accept it as is. To go the other way (a Zap
creating posts) call the REST API with a scoped token.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select

from ..models import Channel, Event, PostTarget, Webhook, WebhookDelivery, new_id

log = logging.getLogger("huhqueue.webhooks")

# Event.kind -> public event name
KINDS = {"sent": "post.sent", "failed": "post.failed", "notify": "post.notify", "approval": "approval.updated",
         "comment": "comment.received", "mention": "mention.received", "reconnect": "channel.reconnect",
         "token_expiring": "channel.token_expiring", "connected": "channel.connected", "report": "report.sent",
         "automation": "automation.ran", "duplicate": "post.duplicate_blocked", "rate_guard": "post.rate_guard"}
EVENTS = sorted(set(KINDS.values()) | {"ping"})
BACKOFF = [60, 300, 1800, 7200, 43200]          # seconds before attempts 2..6
MAX_ATTEMPTS = len(BACKOFF) + 1
DISABLE_AFTER = 15
BATCH = 50


class WebhookError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.utcnow()


def check_url(url: str) -> str:
    url = (url or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise WebhookError("Use a full http(s):// URL, e.g. https://hooks.zapier.com/hooks/catch/...")
    try:   # local n8n on a LAN address is fine; link-local (cloud metadata endpoints) never is
        ip = ipaddress.ip_address(parts.hostname)
    except ValueError:
        ip = None
    if ip is not None and ip.is_link_local:
        raise WebhookError("Link-local addresses (cloud metadata) are not allowed")
    return url[:2000]


def check_events(events) -> list[str]:
    evs = sorted({str(e).strip() for e in events or [] if str(e).strip()})
    bad = [e for e in evs if e not in EVENTS and e != "*"]
    if bad:
        raise WebhookError(f"Unknown event(s): {', '.join(bad)}. Choose from: {', '.join(EVENTS)}")
    return [] if "*" in evs else evs


def to_dict(core, w: Webhook, secret: bool = False) -> dict:
    d = {"id": w.id, "url": w.url, "events": w.events or [], "all_events": not w.events, "active": w.active,
         "failures": w.failures, "created_at": w.created_at.isoformat() + "Z"}
    if secret:
        d["secret"] = core.cipher.decrypt(w.secret_enc)
    return d


def create(core, s, url: str, events=None, active: bool = True) -> dict:
    w = Webhook(id=new_id("wh"), url=check_url(url), events=check_events(events), active=bool(active),
                secret_enc=core.cipher.encrypt("whsec_" + secrets.token_urlsafe(24)))
    s.add(w)
    s.flush()
    return to_dict(core, w, secret=True)


def update(core, s, w: Webhook, patch: dict) -> dict:
    if "url" in patch:
        w.url = check_url(patch["url"])
    if "events" in patch:
        w.events = check_events(patch["events"])
    if "active" in patch:
        w.active = bool(patch["active"])
        if w.active:
            w.failures = 0
    rotated = bool(patch.get("rotate_secret"))
    if rotated:
        w.secret_enc = core.cipher.encrypt("whsec_" + secrets.token_urlsafe(24))
    s.flush()
    return to_dict(core, w, secret=rotated)


# --------------------------------------------------------------------------------------------- queueing
def _context(s, ev: Event) -> dict:
    out: dict = {}
    if ev.channel_id:
        ch = s.get(Channel, ev.channel_id)
        if ch:
            out["channel"] = {"id": ch.id, "provider": ch.provider, "username": ch.username}
    if ev.target_id:
        t = s.get(PostTarget, ev.target_id)
        if t:
            p = t.post
            out["post"] = {"target_id": t.id, "post_id": t.post_id, "status": t.status, "caption": t.caption_override or (p.caption if p else ""),
                           "permalink": t.permalink, "external_id": t.external_id, "error": t.last_error or "",
                           "scheduled_at": t.scheduled_at.isoformat() + "Z" if t.scheduled_at else None,
                           "published_at": t.published_at.isoformat() + "Z" if t.published_at else None,
                           "tags": (p.tags if p else []) or [], "campaign": p.campaign if p else ""}
            if "channel" not in out and t.channel:
                out["channel"] = {"id": t.channel.id, "provider": t.channel.provider, "username": t.channel.username}
    return out


def queue_event(s, ev: Event) -> int:
    """Called by events.record() inside the event's transaction: one pending delivery per subscribed webhook."""
    name = KINDS.get(ev.kind or "")
    if not name:
        return 0
    hooks = [w for w in s.scalars(select(Webhook).where(Webhook.active.is_(True))) if not w.events or name in w.events]
    if not hooks:
        return 0
    now = utcnow()
    payload = {"event": name, "created_at": now.isoformat() + "Z", "level": ev.level, "message": ev.message, **_context(s, ev)}
    for w in hooks:
        s.add(WebhookDelivery(id=new_id("wd"), webhook_id=w.id, event=name, payload=payload, status="pending",
                              next_attempt_at=now))
    return len(hooks)


# --------------------------------------------------------------------------------------------- sending
def sign(secret: str, ts: int, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


def _send(core, w: Webhook, d: WebhookDelivery) -> tuple[bool, int, str]:
    body = json.dumps({"id": d.id, **(d.payload or {})}, separators=(",", ":"), default=str).encode()
    ts = int(time.time())
    headers = {"Content-Type": "application/json", "User-Agent": "HuhQueue-Webhooks/1", "X-HuhQueue-Event": d.event,
               "X-HuhQueue-Delivery": d.id, "X-HuhQueue-Timestamp": str(ts),
               "X-HuhQueue-Signature": sign(core.cipher.decrypt(w.secret_enc) or "", ts, body)}
    try:
        r = core.http.post(w.url, content=body, headers=headers, timeout=10, follow_redirects=False)
    except httpx.HTTPError as e:
        return False, 0, e.__class__.__name__
    return 200 <= r.status_code < 300, r.status_code, (r.text or "")[:200]


def _attempt(core, s, w: Webhook, d: WebhookDelivery, now: datetime) -> bool:
    ok, code, _ = _send(core, w, d)
    d.attempts = (d.attempts or 0) + 1
    d.response_code = code
    if ok:
        d.status, d.next_attempt_at = "delivered", None
        w.failures = 0
    elif d.attempts >= MAX_ATTEMPTS or not w.active:
        d.status, d.next_attempt_at = "failed", None
        w.failures = (w.failures or 0) + 1
        if w.failures >= DISABLE_AFTER:
            w.active = False
            log.warning("webhook %s switched off after %d failed deliveries", w.id, w.failures)
    else:
        d.next_attempt_at = now + timedelta(seconds=BACKOFF[d.attempts - 1])
    return ok


def deliver_due(core, now: datetime | None = None) -> int:
    """Worker hook: send pending deliveries whose time has come. Returns how many were delivered."""
    now = max(now or utcnow(), utcnow())   # events recorded earlier in the same worker tick are due too
    n = 0
    with core.db.session() as s:
        due = list(s.scalars(select(WebhookDelivery).where(WebhookDelivery.status == "pending",
                                                           WebhookDelivery.next_attempt_at <= now)
                             .order_by(WebhookDelivery.next_attempt_at).limit(BATCH)))
        for d in due:
            w = s.get(Webhook, d.webhook_id)
            if not w:
                d.status, d.next_attempt_at = "failed", None
                continue
            n += _attempt(core, s, w, d, now)
    return n


def test(core, s, w: Webhook) -> dict:
    """Send a `ping` right now (not queued) and report what the endpoint answered."""
    d = WebhookDelivery(id=new_id("wd"), webhook_id=w.id, event="ping", status="pending", next_attempt_at=None,
                        payload={"event": "ping", "created_at": utcnow().isoformat() + "Z", "level": "info",
                                 "message": "Test from HuhQueue: your webhook works."})
    s.add(d)
    ok, code, text = _send(core, w, d)
    d.attempts, d.response_code, d.status = 1, code, "delivered" if ok else "failed"
    return {"ok": ok, "status_code": code, "response": text, "delivery_id": d.id}


def deliveries(s, webhook_id: str, limit: int = 50) -> list[dict]:
    rows = s.scalars(select(WebhookDelivery).where(WebhookDelivery.webhook_id == webhook_id)
                     .order_by(WebhookDelivery.created_at.desc()).limit(max(1, min(limit, 200))))
    return [{"id": d.id, "event": d.event, "status": d.status, "attempts": d.attempts, "response_code": d.response_code,
             "created_at": d.created_at.isoformat() + "Z",
             "next_attempt_at": d.next_attempt_at.isoformat() + "Z" if d.next_attempt_at else None} for d in rows]


def redeliver(core, s, d: WebhookDelivery) -> dict:
    d.status, d.attempts, d.next_attempt_at = "pending", 0, utcnow()
    return {"id": d.id, "status": d.status}
