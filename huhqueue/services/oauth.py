"""Connect / disconnect channels: OAuth with one-time state (CSRF) + PKCE, or a pasted token."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse

from sqlalchemy import delete, select

from ..connectors import ConnectorError
from ..models import Channel, OAuthState
from . import scheduling
from .events import record

STATE_TTL = timedelta(minutes=15)


class OAuthFailure(Exception):
    pass


def start(core, provider: str, origin: str = "") -> dict:
    conn = core.connector(provider)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48) if getattr(conn, "uses_pkce", False) else ""
    redirect = core.redirect_uri(provider, origin)
    url = conn.authorize_url(state, redirect, verifier)
    with core.db.session() as s:
        s.execute(delete(OAuthState).where(OAuthState.created_at < datetime.utcnow() - STATE_TTL))
        s.add(OAuthState(state=state, provider=provider, code_verifier=verifier, redirect_uri=redirect))
    return {"url": url, "redirect_uri": redirect, "state": state}


def complete(core, provider: str, code: str | None, state: str | None, error: str | None = None) -> str:
    if error:
        raise OAuthFailure(f"{provider} said: {error}")
    if not code or not state:
        raise OAuthFailure("Missing code/state in the callback")
    with core.db.session() as s:
        row = s.get(OAuthState, state)
        if not row or row.provider != provider:
            raise OAuthFailure("Unknown or reused login attempt (state mismatch) - start Connect again")
        fresh = datetime.utcnow() - row.created_at <= STATE_TTL
        verifier, redirect = row.code_verifier, row.redirect_uri
        s.delete(row)  # one-time use
    if not fresh:
        raise OAuthFailure("The login took too long - start Connect again")
    conn = core.connector(provider)
    try:
        bundle, profile = conn.exchange_code(code, redirect, verifier)
    except ConnectorError as e:
        raise OAuthFailure(e.message) from e
    expand = getattr(conn, "expand_profiles", None)
    profiles = expand(bundle, profile) if expand else [profile]   # e.g. one channel per LinkedIn company page
    ids = [upsert_channel(core, provider, bundle, p) for p in profiles]
    return ids[0]


def complete_from_url(core, pasted_url: str) -> str:
    """Fallback: paste the URL the browser landed on after consent."""
    u = urlparse(pasted_url.strip())
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    parts = [p for p in u.path.split("/") if p]
    provider = parts[-2] if len(parts) >= 2 and parts[-1] == "callback" else ""
    if not provider:
        with core.db.session() as s:
            row = s.get(OAuthState, q.get("state", ""))
            provider = row.provider if row else ""
    if not provider:
        raise OAuthFailure("Could not tell which network this URL is for")
    return complete(core, provider, q.get("code"), q.get("state"), q.get("error_description") or q.get("error"))


def connect_token(core, provider: str, token: str, extra: dict | None = None) -> str:
    if not (token or "").strip():
        raise OAuthFailure("Paste an access token")
    try:
        bundle, profile = core.connector(provider).connect_with_token(token.strip(), extra or {})
    except ConnectorError as e:
        raise OAuthFailure(e.message) from e
    return upsert_channel(core, provider, bundle, profile)


def upsert_channel(core, provider: str, bundle, profile) -> str:
    prefs = core.prefs()
    with core.db.session() as s:
        ch = s.scalar(select(Channel).where(Channel.provider == provider, Channel.external_id == profile.external_id))
        new = ch is None
        if new:
            ch = Channel(provider=provider, external_id=profile.external_id, timezone=prefs.get("timezone", "Europe/Dublin"),
                         daily_limit=int(prefs.get("default_daily_limit", 2)))
            s.add(ch)
        ch.username, ch.display_name, ch.avatar_url = profile.username, profile.display_name, profile.avatar_url
        ch.meta = {**(ch.meta or {}), **{k: v for k, v in (profile.meta or {}).items() if v is not None},
                   "profile_synced_at": datetime.utcnow().isoformat()}
        ch.status, ch.status_detail = "active", ""
        core.store_tokens(ch, bundle)
        s.flush()
        if new:
            scheduling.set_slots(s, ch, scheduling.preset_slots("huhbyte"))
        record(core, s, f"{'Connected' if new else 'Reconnected'} {provider} @{ch.username}", level="success",
               kind="connected", channel_id=ch.id)
        return ch.id


def disconnect(core, channel_id: str) -> None:
    with core.db.session() as s:
        ch = s.get(Channel, channel_id)
        if not ch:
            raise KeyError("Channel not found")
        conn = core.connectors.get(ch.provider)
        if conn:
            try:
                conn.revoke(core.channel_auth(ch))
            except ConnectorError:
                pass
        s.delete(ch)  # cascades its slots, posts and comments


def refresh_profile(core, channel_id: str) -> None:
    with core.db.session() as s:
        ch = s.get(Channel, channel_id)
        prof = core.connector(ch.provider).fetch_profile(core.channel_auth(ch))
        if prof:
            ch.username = prof.username or ch.username
            ch.display_name, ch.avatar_url = prof.display_name, prof.avatar_url
            ch.meta = {**(ch.meta or {}), **{k: v for k, v in (prof.meta or {}).items() if v is not None},
                       "profile_synced_at": datetime.utcnow().isoformat()}
