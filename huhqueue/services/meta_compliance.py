"""Meta (Instagram) platform-compliance callbacks: Deauthorize and Data Deletion.

Meta POSTs a form field ``signed_request`` = ``<base64url(sig)>.<base64url(json payload)>`` where
``sig = HMAC-SHA256(payload_part, app_secret)``. We verify it with the Instagram app secret, then:

* deauthorize  - the person removed the app: wipe that IG user's tokens and mark the channel disconnected
                 (scheduled posts stay, so nothing is lost if they reconnect).
* data deletion - the person asked for their data to be deleted: remove the channel, its tokens, posts and
                 comments, and answer ``{"url", "confirmation_code"}``. ``GET /data-deletion/<code>`` shows status.

Docs: developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime

from sqlalchemy import select

from ..models import KV, Channel
from .events import record


class SignedRequestError(ValueError):
    pass


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def make_signed_request(payload: dict, secret: str) -> str:
    """Build a signed_request the way Meta does (used by tests and for manual checks)."""
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{_b64e(sig)}.{body}"


def parse_signed_request(signed_request: str, secret: str) -> dict:
    if not secret:
        raise SignedRequestError("Instagram app secret is not configured")
    try:
        sig_part, body = (signed_request or "").split(".", 1)
        sig = _b64d(sig_part)
        data = json.loads(_b64d(body))
    except Exception as e:  # malformed
        raise SignedRequestError("Malformed signed_request") from e
    if str(data.get("algorithm", "HMAC-SHA256")).upper() != "HMAC-SHA256":
        raise SignedRequestError("Unsupported signed_request algorithm")
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        raise SignedRequestError("Bad signed_request signature")
    if not data.get("user_id"):
        raise SignedRequestError("signed_request has no user_id")
    return data


def _channels_for(s, user_id: str) -> list[Channel]:
    rows = s.scalars(select(Channel).where(Channel.provider == "instagram")).all()
    uid = str(user_id)
    # external_id is the IG professional account id; the signed_request may carry the app-scoped id instead
    return [c for c in rows if uid in (str(c.external_id), str((c.meta or {}).get("app_scoped_id") or ""),
                                       str((c.meta or {}).get("user_id") or ""))]


def deauthorize(core, signed_request: str) -> dict:
    data = parse_signed_request(signed_request, core.settings.instagram_app_secret)
    n = 0
    with core.db.session() as s:
        for ch in _channels_for(s, data["user_id"]):
            ch.access_token_enc = ch.refresh_token_enc = None
            ch.token_expires_at = ch.refresh_expires_at = None
            ch.status, ch.status_detail = "disconnected", "App removed in Instagram settings (Meta deauthorize callback)."
            record(core, s, f"@{ch.username} removed HuhQueue in Instagram - tokens deleted", level="warning",
                   kind="reconnect", channel_id=ch.id, notify=True)
            n += 1
    return {"ok": True, "channels": n}


def data_deletion(core, signed_request: str, base_url: str) -> dict:
    data = parse_signed_request(signed_request, core.settings.instagram_app_secret)
    code = secrets.token_hex(8)
    with core.db.session() as s:
        chans = _channels_for(s, data["user_id"])
        names = [c.username for c in chans]
        for ch in chans:
            s.delete(ch)  # cascades tokens (stored on the row), slots, posts, comments
        s.add(KV(key=f"datadel:{code}", value={
            "status": "complete", "channels_deleted": len(names),
            "requested_at": datetime.utcnow().isoformat() + "Z", "completed_at": datetime.utcnow().isoformat() + "Z"}))
        record(core, s, f"Meta data-deletion request {code}: removed {len(names)} Instagram channel(s)",
               level="warning", kind="data_deletion", notify=True)
    return {"url": f"{base_url.rstrip('/')}/data-deletion/{code}", "confirmation_code": code}


def deletion_status(core, code: str) -> dict | None:
    if not code or not code.isalnum() or len(code) > 64:
        return None
    with core.db.session() as s:
        row = s.get(KV, f"datadel:{code}")
        return dict(row.value) if row else None
