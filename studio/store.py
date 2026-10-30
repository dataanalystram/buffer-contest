"""SQLite store: users (encrypted Buffer tokens), sessions, agent tokens, approvals, audit log.

Tokens are encrypted at rest with a key derived from SESSION_SECRET and never leave the server.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT, name TEXT, org_id TEXT, org_name TEXT,
  tokens TEXT, kill_switch INTEGER DEFAULT 0, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS sessions (
  id_hash TEXT PRIMARY KEY, user_id TEXT, csrf TEXT, oauth TEXT, created REAL, expires REAL);
CREATE TABLE IF NOT EXISTS agent_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, name TEXT, token_hash TEXT UNIQUE,
  policy TEXT, daily_cap INTEGER, channels TEXT, created REAL, expires REAL, revoked INTEGER DEFAULT 0, last_used REAL);
CREATE TABLE IF NOT EXISTS confirms (
  token_hash TEXT PRIMARY KEY, user_id TEXT, agent_id INTEGER, payload TEXT, expires REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS approvals (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, agent_id INTEGER, agent_name TEXT, payload TEXT,
  status TEXT, result TEXT, created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS device_links (
  token_hash TEXT PRIMARY KEY, user_id TEXT, expires REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS feeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, url TEXT, created REAL);
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY, client_name TEXT, redirect_uris TEXT, created REAL);
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT, user_id TEXT, redirect_uri TEXT, challenge TEXT, policy TEXT,
  daily_cap INTEGER, expires REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS oauth_refresh (
  token_hash TEXT PRIMARY KEY, client_id TEXT, user_id TEXT, agent_id INTEGER, policy TEXT, daily_cap INTEGER,
  expires REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, actor TEXT, action TEXT, risk TEXT,
  detail TEXT, ok INTEGER, ts REAL);
"""


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


ID_TABLES = ("agent_tokens", "approvals", "audit", "feeds")
TABLES = ("users", "sessions", "agent_tokens", "confirms", "approvals", "device_links", "feeds", "audit",
          "oauth_clients", "oauth_codes", "oauth_refresh")


class Store:
    """SQLite locally; Postgres when the path is a postgres:// URL (production: survives restarts,
    so rotating Buffer refresh tokens are never lost)."""

    def __init__(self, path: str, secret: str):
        self.path = path
        self._lock = threading.RLock()
        self.pg = path.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self._psycopg, self._dict_row = psycopg, dict_row
            self._db = self._connect()
            with self._db.cursor() as c:
                c.execute(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION"))
                # Supabase publishes the public schema through its Data API. Row-level security with no policies
                # shuts that door; the app connects as the table owner, which RLS does not apply to.
                for t in TABLES:
                    c.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        else:
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
        key = base64.urlsafe_b64encode(hashlib.sha256(("tokens:" + secret).encode()).digest())
        self._fernet = Fernet(key)

    def _connect(self):
        # prepare_threshold=None: PgBouncer-style poolers (Supabase) can't keep server-side prepared statements.
        return self._psycopg.connect(self.path, autocommit=True, row_factory=self._dict_row, prepare_threshold=None)

    # ---- low level
    def _pg(self, sql: str, args: tuple, fetch: bool):
        sql = sql.replace("?", "%s")
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._db.closed:
                        self._db = self._connect()
                    with self._db.cursor() as c:
                        c.execute(sql, args)
                        return c.fetchall() if fetch else None
                except self._psycopg.OperationalError:
                    if attempt:
                        raise
                    try:
                        self._db.close()
                    except Exception:
                        pass

    def q(self, sql: str, args: tuple = ()) -> list:
        if self.pg:
            return self._pg(sql, args, True)
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        if self.pg:
            s = sql.strip()
            if s.upper().startswith("INSERT INTO") and s.split()[2].split("(")[0] in ID_TABLES:
                rows = self._pg(s + " RETURNING id", args, True)
                return rows[0]["id"]
            self._pg(s, args, False)
            return 0
        with self._lock:
            cur = self._db.execute(sql, args)
            return cur.lastrowid

    def ping(self) -> bool:
        return bool(self.q("SELECT 1 AS ok"))

    def copy_from(self, src: "Store") -> dict[str, int]:
        """One-time move to a new database (e.g. Render Postgres -> Supabase). Copies every row, ids included,
        and only into an empty store, so running it again on a later boot does nothing."""
        if self.q("SELECT COUNT(*) AS n FROM users")[0]["n"]:
            return {}
        copied = {}
        for t in TABLES:
            rows = [dict(r) for r in src.q(f"SELECT * FROM {t}")]
            for r in rows:
                cols = ",".join(r)
                self.x(f"INSERT INTO {t}({cols}) VALUES({','.join('?' * len(r))}) ON CONFLICT DO NOTHING", tuple(r.values()))
            if self.pg and t in ID_TABLES and rows:
                self.q(f"SELECT setval(pg_get_serial_sequence('{t}','id'), (SELECT MAX(id) FROM {t})) AS v")
            copied[t] = len(rows)
        return copied

    # ---- tokens (encrypted)
    def seal(self, data: dict) -> str:
        return self._fernet.encrypt(json.dumps(data).encode()).decode()

    def unseal(self, blob: str | None) -> dict | None:
        if not blob:
            return None
        try:
            return json.loads(self._fernet.decrypt(blob.encode()))
        except (InvalidToken, ValueError):
            return None

    # ---- users
    def upsert_user(self, uid: str, email: str, name: str, org_id: str, org_name: str, tokens: dict) -> None:
        now = time.time()
        self.x("""INSERT INTO users(id,email,name,org_id,org_name,tokens,created,updated) VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(id) DO UPDATE SET email=excluded.email, name=excluded.name, org_id=excluded.org_id,
                  org_name=excluded.org_name, tokens=excluded.tokens, updated=excluded.updated""",
               (uid, email, name, org_id, org_name, self.seal(tokens), now, now))

    def user(self, uid: str) -> dict | None:
        rows = self.q("SELECT * FROM users WHERE id=?", (uid,))
        return dict(rows[0]) if rows else None

    def tokens(self, uid: str) -> dict | None:
        u = self.user(uid)
        return self.unseal(u["tokens"]) if u else None

    def save_tokens(self, uid: str, tokens: dict | None) -> None:
        self.x("UPDATE users SET tokens=?, updated=? WHERE id=?", (self.seal(tokens) if tokens else None, time.time(), uid))

    def set_kill(self, uid: str, on: bool) -> None:
        self.x("UPDATE users SET kill_switch=? WHERE id=?", (1 if on else 0, uid))

    def delete_user(self, uid: str) -> None:
        for t in ("sessions", "agent_tokens", "confirms", "approvals", "audit", "device_links", "feeds", "oauth_codes",
                  "oauth_refresh"):
            self.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        self.x("DELETE FROM users WHERE id=?", (uid,))

    # ---- sessions
    def new_session(self, sid: str, csrf: str, ttl: float = 30 * 86400) -> None:
        now = time.time()
        self.x("INSERT INTO sessions(id_hash,user_id,csrf,oauth,created,expires) VALUES(?,?,?,?,?,?)",
               (sha(sid), None, csrf, None, now, now + ttl))

    def session(self, sid: str | None) -> dict | None:
        if not sid:
            return None
        rows = self.q("SELECT * FROM sessions WHERE id_hash=? AND expires>?", (sha(sid), time.time()))
        return dict(rows[0]) if rows else None

    def update_session(self, sid: str, **fields: Any) -> None:
        for k, v in fields.items():
            assert k in ("user_id", "oauth", "csrf")
            self.x(f"UPDATE sessions SET {k}=? WHERE id_hash=?", (v, sha(sid)))

    def drop_session(self, sid: str) -> None:
        self.x("DELETE FROM sessions WHERE id_hash=?", (sha(sid),))

    # ---- audit
    def audit(self, uid: str, actor: str, action: str, risk: str, detail: dict, ok: bool = True) -> None:
        self.x("INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts) VALUES(?,?,?,?,?,?,?)",
               (uid, actor, action, risk, json.dumps(detail)[:4000], 1 if ok else 0, time.time()))

    def audit_rows(self, uid: str, limit: int = 100) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM audit WHERE user_id=? ORDER BY id DESC LIMIT ?", (uid, limit)):
            d = dict(r)
            d["detail"] = json.loads(d["detail"] or "{}")
            d["ok"] = bool(d["ok"])
            out.append(d)
        return out
