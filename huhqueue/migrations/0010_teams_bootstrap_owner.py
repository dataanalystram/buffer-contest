"""Teams: the existing single app password becomes the bootstrap Owner user.

Runs after create_all (the `users` / `user_sessions` tables and the new columns exist by then). Nothing else needs moving:
posts, notes and audit rows keep their actor names, API tokens without a user_id keep their own scopes (the owner minted
them), and broadcast notifications (user_id NULL) stay visible to everyone.
Old signed session cookies are not carried over - everyone logs in once after the upgrade.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import text

AFTER_CREATE_ALL = True


def up(conn) -> None:
    if conn.execute(text("SELECT count(*) FROM users")).scalar():
        return
    row = conn.execute(text("SELECT value FROM settings WHERE key = 'auth'")).fetchone()
    value = row[0] if row else None
    if isinstance(value, str):
        value = json.loads(value or "{}")
    pw_hash = (value or {}).get("hash") or ""
    if not pw_hash:
        return   # fresh install: first-run setup creates the owner
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conn.execute(text(
        "INSERT INTO users (id, email, name, role, password_hash, totp_secret_enc, totp_enabled, recovery_hashes, "
        "invite_token_hash, channel_ids, channel_access, handle, invited_by, disabled, created_at, accepted_at) "
        "VALUES (:id, 'owner@huhqueue.local', 'Owner', 'owner', :h, '', 0, '[]', '', '[]', '{}', 'owner', '', 0, :now, :now)"),
        {"id": f"us_{secrets.token_hex(6)}", "h": pw_hash, "now": now})
