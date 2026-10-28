"""Event log + notifications (in-app bell and optional webhook)."""
from __future__ import annotations

import logging

import httpx

from ..models import Event

log = logging.getLogger("huhqueue.events")

# Kinds of notification that can be pushed to the webhook; Settings › Notifications turns each one on or off
# (prefs["notify_events"] = {kind: bool}, missing = on). The in-app bell always gets every notification.
NOTIFY_KINDS = {
    "failed": "A post failed to publish", "notify": "A “Notify me” post is ready for you to finish",
    "approval": "Approval requests and decisions", "reconnect": "A channel needs reconnecting",
    "comment": "New comments that need a reply", "mention": "You were @mentioned by a teammate",
    "sent": "A post was published", "automation": "Automations (RSS, evergreen, recurring) ran or failed",
    "report": "Scheduled reports", "team": "Team invites and role changes", "security": "Security events",
    "rate_guard": "A post was held by a daily limit", "duplicate": "A duplicate post was blocked",
    "empty_queue": "A channel has nothing scheduled", "daily_recap": "Daily recap: yesterday's posts, open comments, today's queue",
}
# Email is a second channel next to push: off for every kind until switched on (prefs["notify_email"] = {kind: bool}),
# sent to prefs["notify_email_to"] through SMTP (HUHQ_SMTP_*).


def record(core, session, message: str, *, level: str = "info", kind: str = "log", target_id: str | None = None,
           channel_id: str | None = None, notify: bool = False) -> Event:
    ev = Event(message=message[:2000], level=level, kind=kind, target_id=target_id, channel_id=channel_id,
               notify=notify)
    session.add(ev)
    try:   # Zapier / Make / n8n webhooks (services/webhooks.py); queued in this transaction, sent by the worker
        from .webhooks import queue_event
        queue_event(session, ev)
    except Exception:
        log.exception("could not queue webhooks for event")
    if notify and push_enabled(core, kind):
        webhook(core, {"level": level, "kind": kind, "message": message, "target_id": target_id,
                       "channel_id": channel_id})
    if notify:
        email_notify(core, kind, message)
    return ev


def email_notify(core, kind: str, message: str) -> bool:
    prefs = core.prefs()
    to = (prefs.get("notify_email_to") or "").strip()
    if not to or prefs.get("notify_paused") or not (prefs.get("notify_email") or {}).get(kind):
        return False
    from ..teams import email_later
    email_later(to, f"HuhQueue: {NOTIFY_KINDS.get(kind, kind)}", message + "\n")
    return True


def push_enabled(core, kind: str) -> bool:
    prefs = core.prefs()
    if prefs.get("notify_paused"):
        return False
    return bool((prefs.get("notify_events") or {}).get(kind or "log", True))


def webhook(core, payload: dict) -> None:
    url = (core.prefs().get("notify_webhook_url") or "").strip()
    if not url:
        return
    try:
        if "ntfy" in url:  # ntfy.sh style topics take plain text
            core.http.post(url, content=payload["message"].encode(), headers={"Title": "HuhQueue", "Tags": payload["level"]},
                           timeout=10)
        else:
            core.http.post(url, json={"text": payload["message"], **payload}, timeout=10)
    except httpx.HTTPError as e:
        log.warning("notification webhook failed: %s", e.__class__.__name__)
