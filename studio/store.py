"""SQLite store: users (encrypted Buffer tokens), sessions, agent tokens, approvals, audit log.

Secrets at rest (Buffer OAuth tokens, bring-your-own AI keys, the pending OAuth state) are sealed with a keyring that is
separate from SESSION_SECRET:

* ``TOKEN_KEY``           the current key; every new seal uses it.
* ``TOKEN_KEY_PREVIOUS``  comma-separated older keys, still accepted for reading during a rotation.
* the old key derived from SESSION_SECRET is kept for reading only, so data sealed before TOKEN_KEY existed still opens.

``rotate_keys()`` (run on every boot) re-seals every row that is not already under the current key, so rotating is:
set the new TOKEN_KEY, move the old value into TOKEN_KEY_PREVIOUS, deploy, then drop TOKEN_KEY_PREVIOUS on a later deploy.
A row no key can open is left untouched (and counted), never overwritten. Without TOKEN_KEY (old self-hosted installs)
the SESSION_SECRET-derived key stays current, so nothing breaks.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

# (table, key column, sealed column): every place a sealed blob is stored
SEALED = (("users", "id", "tokens"), ("ai_settings", "user_id", "data"), ("sessions", "id_hash", "oauth"))


def fernet_key(value: str) -> bytes:
    """A Fernet key from a configured value: used as is when it already is one, otherwise derived from it."""
    v = (value or "").strip()
    try:
        if len(v) == 44 and len(base64.urlsafe_b64decode(v.encode())) == 32:
            return v.encode()
    except ValueError:
        pass
    return base64.urlsafe_b64encode(hashlib.sha256(("hq-token-key:" + v).encode()).digest())


def legacy_key(session_secret: str) -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(("tokens:" + session_secret).encode()).digest())


def keyring(session_secret: str, token_key: str = "", previous: tuple[str, ...] | list[str] = ()) -> tuple[Fernet, MultiFernet, bool]:
    """(current key, every readable key, dedicated?). Without TOKEN_KEY the SESSION_SECRET-derived key stays current."""
    prev = [fernet_key(k) for k in previous if k and k.strip()]
    keys = [fernet_key(token_key), *prev, legacy_key(session_secret)] if token_key else [legacy_key(session_secret), *prev]
    fs = [Fernet(k) for k in dict.fromkeys(keys)]
    return fs[0], MultiFernet(fs), bool(token_key)

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
CREATE TABLE IF NOT EXISTS brand_kits (
  user_id TEXT PRIMARY KEY, kit TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS style_rules (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, kind TEXT, spec TEXT, rule TEXT, source TEXT,
  active INTEGER DEFAULT 1, hits INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS media (
  token TEXT PRIMARY KEY, user_id TEXT, name TEXT, mime TEXT, data BLOB, size INTEGER, meta TEXT, created REAL);
CREATE TABLE IF NOT EXISTS agent_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, kind TEXT, input TEXT, status TEXT, steps TEXT, result TEXT,
  created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS render_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, run_id INTEGER, spec TEXT, status TEXT, media_token TEXT,
  error TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS ai_settings (
  user_id TEXT PRIMARY KEY, data TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, actor TEXT, action TEXT, risk TEXT,
  detail TEXT, ok INTEGER, ts REAL);
CREATE TABLE IF NOT EXISTS blob_gc (
  key TEXT PRIMARY KEY, at REAL, tries INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS r2_usage (
  month TEXT, cls TEXT, n INTEGER DEFAULT 0, PRIMARY KEY (month, cls));
CREATE TABLE IF NOT EXISTS admin_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, admin TEXT, action TEXT, subject_hash TEXT, detail TEXT, ok INTEGER);
CREATE TABLE IF NOT EXISTS user_tombstones (
  id_hash TEXT PRIMARY KEY, at REAL, via TEXT);
CREATE TABLE IF NOT EXISTS ops_switches (
  name TEXT PRIMARY KEY, paused INTEGER DEFAULT 0, at REAL, by_hash TEXT, reason TEXT);
"""


# Columns added after the first release: added in place on boot (SQLite and Postgres), never dropped.
ADDED_COLUMNS = {
    # admin panel (studio/admin.py, docs/ADMIN.md): a reversible block, and when the person was last active
    "users": (("blocked", "INTEGER"), ("blocked_at", "REAL"), ("blocked_by", "TEXT"), ("block_reason", "TEXT"), ("last_seen", "REAL")),
    # media storage backend (studio/blobstore.py, docs/STORAGE.md): the object key when the bytes are in R2, and their hash
    "media": (("storage_key", "TEXT"), ("sha256", "TEXT")),
    "audit": (("category", "TEXT"), ("surface", "TEXT"), ("app", "TEXT"), ("client_id", "TEXT"), ("token_id", "INTEGER"),
              ("token_label", "TEXT"), ("provider", "TEXT"), ("model", "TEXT"), ("tokens_in", "INTEGER"),
              ("tokens_out", "INTEGER"), ("cost_usd", "REAL"), ("ip", "TEXT"), ("ua", "TEXT")),
    "agent_tokens": (("client_id", "TEXT"), ("client_info", "TEXT"), ("app", "TEXT"),
                     ("allow_paid_apis", "INTEGER"),  # the person's "Allow paid APIs" switch for this app (studio/consent.py)
                     ("admin_hourly_cap", "INTEGER"),
                     ("parent_id", "INTEGER")),  # the token an MCP refresh rotated this one from (admin revoke follows it)  # an admin's hourly request cap for this token (studio/opsctl.py), NULL = none  # the person's "Allow paid APIs" switch for this app (studio/consent.py), off by default
    # which agent token made the run (studio/run_discipline.py: one request = one run, run caps, cleanup)
    "agent_runs": (("agent_id", "INTEGER"),),
    "render_jobs": (("result", "TEXT"),),
    # Style Ledger v2 (studio/ledger.py): per-channel scope, where a rule came from, its before -> after examples and the
    # edit ids a learned rule came from
    "style_rules": (("scope", "TEXT"), ("origin", "TEXT"), ("examples", "TEXT"), ("evidence", "TEXT"), ("updated", "REAL")),  # a transcribe job's transcript (studio.media transcribe on the worker)
}
INDEXES = ("CREATE INDEX IF NOT EXISTS media_storage_key ON media(storage_key)",  # blobstore.sweep's "still referenced?" check
           "CREATE INDEX IF NOT EXISTS audit_user_id ON audit(user_id, id)",
           "CREATE INDEX IF NOT EXISTS agent_runs_agent ON agent_runs(user_id, agent_id, updated)",
           "CREATE INDEX IF NOT EXISTS audit_user_cat ON audit(user_id, category, id)",
           "CREATE INDEX IF NOT EXISTS audit_ts ON audit(ts)")


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


ID_TABLES = ("admin_audit", "agent_tokens", "approvals", "audit", "feeds", "style_rules", "agent_runs", "render_jobs",
             "template_slots", "source_runs", "library_items", "brand_kit_library",
             "style_edits", "style_suggestions", "review_links", "link_consents")  # Brand studio slots, Sources runs and brand kits (own schemas in brand_studio.py, sources_agent.py, brand_kits.py)
TABLES = ("users", "sessions", "agent_tokens", "confirms", "approvals", "device_links", "feeds", "audit",
          "oauth_clients", "oauth_codes", "oauth_refresh", "brand_kits", "style_rules", "media", "agent_runs", "render_jobs",
          "ai_settings", "blob_gc", "admin_audit", "user_tombstones", "r2_usage", "ops_switches")


# Per-person tables created by other modules (each on first use). Disconnect and delete removes them too (found in the
# 2 Nov 2026 privacy review: brand kits, templates, sources and upload/send sessions outlived the account). Not here, on
# purpose: the per-day usage counters (brain_tokens, classifier_usage) and demo_owners, which hold no content and keep
# a reconnect from resetting an allowance.
OWNED_ELSEWHERE = ("brand_kit_library", "brand_studio", "custom_templates", "template_slots", "template_slot_runs", "source_runs",
                   "source_settings",
                   "source_refresh", "send_codes", "upload_sessions", "ai_engine_pref", "chat_titles", "mcp_calls", "mcp_idem",
                   "thread_ids", "thread_deleted", "jev_usage", "link_consents", "take_prefs", "link_auto_approve")

def _run_callbacks(fns) -> None:
    for fn in fns or ():
        try:
            fn()
        except Exception as e:  # noqa: BLE001  a callback never turns a committed transaction into an error
            print(f"store: after-transaction callback failed ({e.__class__.__name__})", flush=True)


class Store:
    """SQLite locally; Postgres when the path is a postgres:// URL (production: survives restarts,
    so rotating Buffer refresh tokens are never lost)."""

    def __init__(self, path: str, secret: str, token_key: str = "", token_keys_previous: tuple[str, ...] | list[str] = ()):
        self.path = path
        self._lock = threading.RLock()
        self._after: list | None = None  # tx(): callbacks for after the outermost commit / rollback
        self._undo: list | None = None
        self._seen: dict[str, float] = {}  # touch(): when each person's last_seen was last written
        self.pg = path.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row
            self._psycopg, self._dict_row = psycopg, dict_row
            self._db = self._connect()
            with self._db.cursor() as c:
                c.execute(SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION").replace(" BLOB", " BYTEA"))
                # Supabase publishes the public schema through its Data API. Row-level security with no policies
                # shuts that door; the app connects as the table owner, which RLS does not apply to.
                for t in TABLES:
                    c.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        else:
            self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
        self._current, self._fernet, self.dedicated_key = keyring(secret, token_key, tuple(token_keys_previous))
        self._add_columns()

    def _add_columns(self) -> None:
        for table, cols in ADDED_COLUMNS.items():
            if self.pg:
                for name, typ in cols:
                    typ = typ.replace("REAL", "DOUBLE PRECISION")
                    self.x(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {typ}")
            else:
                have = {r["name"] for r in self.q(f"PRAGMA table_info({table})")}
                for name, typ in cols:
                    if name not in have:
                        self.x(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
        for ix in INDEXES:
            self.x(ix)

    def _connect(self):
        # prepare_threshold=None: PgBouncer-style poolers (Supabase) can't keep server-side prepared statements.
        return self._psycopg.connect(self.path, autocommit=True, row_factory=self._dict_row, prepare_threshold=None)

    @contextlib.contextmanager
    def tx(self):
        """One atomic unit (Library purge, 3 Nov 2026): every q/x inside commits together or not at all, and the store's
        lock is held throughout, so a request on another thread reads the state before or after, never half of it.
        Re-entrant: an inner tx() joins the outer one. after_commit() / on_rollback() callbacks registered inside run once
        the outermost transaction has committed / rolled back, after the lock is released (PR #110)."""
        after: list = []
        undo: list = []
        outer = False
        try:
            with self._lock:
                if getattr(self, "_tx_depth", 0):
                    self._tx_depth += 1
                    try:
                        yield self
                    finally:
                        self._tx_depth -= 1
                    return
                outer = True
                self._tx_depth, self._after, self._undo = 1, after, undo
                try:
                    if self.pg:
                        if self._db.closed:
                            self._db = self._connect()
                        with self._db.transaction():
                            yield self
                    else:
                        self._db.execute("BEGIN IMMEDIATE")
                        try:
                            yield self
                            self._db.execute("COMMIT")  # a failed COMMIT leaves the transaction open: roll it back too
                        except BaseException:
                            try:
                                self._db.execute("ROLLBACK")
                            except Exception as rb:  # noqa: BLE001  never mask the original error (already rolled back)
                                print(f"store: rollback failed ({rb.__class__.__name__})", flush=True)
                            raise
                finally:
                    self._tx_depth, self._after, self._undo = 0, None, None
        except BaseException:
            if outer:
                _run_callbacks(undo)
            raise
        if outer:
            _run_callbacks(after)

    def after_commit(self, fn) -> None:
        """Run fn once the current transaction commits (dropped on rollback); right away when none is open. Waits for
        another thread's open transaction (the lock), so it never joins someone else's."""
        with self._lock:
            if getattr(self, "_tx_depth", 0) and self._after is not None:
                self._after.append(fn)
                return
        _run_callbacks([fn])

    def on_rollback(self, fn) -> None:
        """Run fn if the current transaction rolls back (e.g. delete an object whose row never committed). No-op outside
        a transaction: a statement that already ran can't roll back."""
        with self._lock:
            if getattr(self, "_tx_depth", 0) and self._undo is not None:
                self._undo.append(fn)

    # ---- low level
    def _pg(self, sql: str, args: tuple, fetch: bool | str):
        sql = sql.replace("?", "%s")
        with self._lock:
            for attempt in (0, 1):
                try:
                    if self._db.closed:
                        self._db = self._connect()
                    with self._db.cursor() as c:
                        c.execute(sql, args)
                        if fetch == "rowcount":
                            return c.rowcount
                        return c.fetchall() if fetch else None
                except self._psycopg.OperationalError:
                    if attempt or getattr(self, "_tx_depth", 0):  # inside tx(): a reconnect would lose the transaction
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

    def xn(self, sql: str, args: tuple = ()) -> int:
        """Run an UPDATE/DELETE and return how many rows it changed. A conditional UPDATE (``... AND status='pending'``)
        that changed 1 row is how a caller knows it won a race. Never re-read a float it wrote and compare it with ``==``
        for that: a time that went through the production database (Postgres behind the Supabase pooler) need not come
        back bit-identical, so ``decided == stamp`` failed and Approve / Reject on a video-link approval answered "No
        pending approval with that id" while the item stayed pending (5 Nov 2026, approval 136)."""
        if self.pg:
            return int(self._pg(sql.strip(), args, "rowcount") or 0)
        with self._lock:
            return int(self._db.execute(sql, args).rowcount or 0)

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

    def rotate_keys(self) -> dict[str, int]:
        """Re-seal every stored blob under the current key (idempotent). Returns counts only, never values."""
        out = {"resealed": 0, "current": 0, "unreadable": 0}
        for table, key, col in SEALED:
            for r in self.q(f"SELECT {key} AS k, {col} AS v FROM {table} WHERE {col} IS NOT NULL AND {col} <> ''"):
                blob = r["v"]
                try:
                    self._current.decrypt(blob.encode())
                    out["current"] += 1
                    continue
                except InvalidToken:
                    pass
                try:
                    new = self._fernet.rotate(blob.encode()).decode()
                except InvalidToken:
                    out["unreadable"] += 1
                    continue
                self.x(f"UPDATE {table} SET {col}=? WHERE {key}=? AND {col}=?", (new, r["k"], blob))
                out["resealed"] += 1
        return out

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

    def blocked(self, uid: str) -> bool:
        rows = self.q("SELECT blocked FROM users WHERE id=?", (uid,)) if uid else []
        return bool(rows and rows[0]["blocked"])

    def touch(self, uid: str, every: float = 300) -> None:
        """users.last_seen, written at most once per ``every`` seconds per person (admin panel's "last active")."""
        now = time.time()
        if not uid or now - self._seen.get(uid, 0) < every:
            return
        self._seen[uid] = now
        try:
            self.x("UPDATE users SET last_seen=? WHERE id=?", (now, uid))
        except Exception:  # noqa: BLE001  (never fails the request it rides on)
            pass

    def set_kill(self, uid: str, on: bool) -> None:
        self.x("UPDATE users SET kill_switch=? WHERE id=?", (1 if on else 0, uid))

    def _table_names(self) -> set[str]:
        if self.pg:
            rows = self.q("SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema()")
        else:
            rows = self.q("SELECT name FROM sqlite_master WHERE type='table'")
        return {str(r["name"]) for r in rows}

    def delete_user(self, uid: str) -> None:
        """Every row the person has, here and in the tables other modules own, in ONE transaction (joins the caller's):
        a failure part-way erases nothing (CodeRabbit, PR #110). Only tables this store has are touched, so no statement
        fails (on Postgres a failed statement would abort the transaction). Media objects in R2 are released into blob_gc
        with their rows (studio/blobstore.py) and deleted by the sweep that follows the commit."""
        from . import blobstore, mediacache
        with self.tx():
            have = self._table_names()
            # the media disk cache (studio/mediacache.py) keeps no copy of an erased person's files (CodeRabbit, PR #119)
            toks = [r["token"] for r in self.q("SELECT token FROM media WHERE user_id=?", (uid,))]
            for t in ("library_items", "library_media"):
                if t in have:
                    toks += [r["token"] for r in self.q(f"SELECT token FROM {t} WHERE user_id=?", (uid,)) if r["token"]]
            mediacache.of(self).forget(toks + ["trash~" + t for t in toks])
            blobstore.release(self, "media", "user_id=?", (uid,))
            for t in ("sessions", "agent_tokens", "confirms", "approvals", "audit", "device_links", "feeds", "oauth_codes",
                      "oauth_refresh", "brand_kits", "style_rules", "agent_runs", "render_jobs", "ai_settings"):
                self.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            for t in ("style_edits", "style_suggestions", "review_links"):  # Style Ledger v2 (studio/ledger.py)
                if t in have:
                    self.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            if {"library_chunks", "library_items", "library_media"} <= have:  # the Library's own tables (studio/library.py)
                blobstore.release(self, "library_chunks", "item_id IN (SELECT id FROM library_items WHERE user_id=?)", (uid,))
                self.x("DELETE FROM library_media WHERE user_id=?", (uid,))
                self.x("DELETE FROM library_items WHERE user_id=?", (uid,))
            # retired tables (the product feature removed on 6 Nov 2026, see CHANGELOG.md): no code writes them any more, but
            # they still hold old rows, so deleting an account still erases that person's rows. Nothing else reads them.
            for t in ("commerce_links", "commerce_user_day"):
                if t in have:
                    self.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
            if "take_uploads" in have:  # One take's multipart uploads; the bucket objects are removed by erase_user first
                # a bucket delete that didn't confirm (del_open / del_ready) keeps its row, cut loose from the person (no
                # name, no fingerprint, no user), so purge_stale still removes the object; every other row goes now
                self.x("DELETE FROM take_uploads WHERE user_id=? AND status NOT IN ('del_open','del_ready')", (uid,))
                self.x("UPDATE take_uploads SET user_id='', name='', fingerprint=NULL, take_id=NULL, updated=0 WHERE user_id=?", (uid,))
            if "library_purged" in have:  # the purged-run token list (Bin purge never resurrects) goes with the account too
                self.x("DELETE FROM library_purged WHERE user_id=?", (uid,))
            if {"upload_parts", "upload_sessions"} <= have:
                # unfinished chunk uploads' bytes (studio/uploads.py): the expiry sweep finds them only through their
                # session, so they go before the sessions do (CodeRabbit, PR #110)
                self.x("DELETE FROM upload_parts WHERE upload_id IN (SELECT id FROM upload_sessions WHERE user_id=?)", (uid,))
            for t in OWNED_ELSEWHERE:  # tables other modules create on first use (privacy page: "Disconnect and delete")
                if t in have:
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
        # a blocked account's sessions are deleted when it is blocked; the join is the belt to those braces
        rows = self.q("SELECT s.* FROM sessions s LEFT JOIN users u ON u.id=s.user_id WHERE s.id_hash=? AND s.expires>? "
                      "AND COALESCE(u.blocked,0)=0", (sha(sid), time.time()))
        return dict(rows[0]) if rows else None

    def update_session(self, sid: str, **fields: Any) -> None:
        for k, v in fields.items():
            assert k in ("user_id", "oauth", "csrf")
            self.x(f"UPDATE sessions SET {k}=? WHERE id_hash=?", (v, sha(sid)))

    def drop_session(self, sid: str) -> None:
        self.x("DELETE FROM sessions WHERE id_hash=?", (sha(sid),))

    # ---- audit
    def audit(self, uid: str, actor: str, action: str, risk: str, detail: dict, ok: bool = True) -> None:
        """One activity row: categorised and attributed from the request context, with every secret scrubbed first."""
        from . import activity
        detail = activity.scrub(detail or {})
        run_id = (activity._ctx.get() or {}).get("run_id")
        if action == "ai_call" and run_id and "run" not in detail:  # ties the call to its Make a post run (Inbox groups by it)
            detail["run"] = run_id
        e = activity.enrich(uid, actor, action, risk, detail)
        cols = ("category", "surface", "app", "client_id", "token_id", "token_label", "provider", "model", "tokens_in",
                "tokens_out", "cost_usd", "ip", "ua")
        self.x(f"INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts,{','.join(cols)}) VALUES(?,?,?,?,?,?,?{',?' * len(cols)})",
               (uid or "", activity.clean_text(actor, 120), action, risk, json.dumps(detail)[:4000], 1 if ok else 0, time.time(),
                *(e[c] for c in cols)))

    def audit_rows(self, uid: str, limit: int = 100) -> list[dict]:
        out = []
        for r in self.q("SELECT * FROM audit WHERE user_id=? AND action<>'buffer_api' ORDER BY id DESC LIMIT ?", (uid, limit)):
            d = dict(r)
            d["detail"] = json.loads(d["detail"] or "{}")
            d["ok"] = bool(d["ok"])
            out.append(d)
        return out
