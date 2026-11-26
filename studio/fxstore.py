"""Where the editor's motion and template decisions are kept: one row per effect a clip got or was refused
(kind, template, the words, chose/skipped, why, how long Chromium took). Read by the editor's notes and for tuning.

Two backends behind one small interface, so moving later is a setting, not a rewrite:

* ``d1``: Cloudflare D1 over its REST API (HQ_FX_STORE=d1 with HQ_D1_ACCOUNT, HQ_D1_DATABASE and HQ_D1_TOKEN, a token
  scoped to "D1 Edit" on that account only). The app's live database is not involved.
* ``store`` (default): the app's own database (SQLite locally, Postgres live), table ``fx_events``.

Both run the same plain SQL (``SCHEMA``: TEXT / INTEGER / REAL only, ``?`` placeholders), and ``export_rows`` /
``import_rows`` move every row between them as JSON-ready dicts. Writes never fail a take: a backend error is logged and
the row dropped (these are notes about renders, not the renders). Erasing an account erases its rows in both places.
"""
from __future__ import annotations

import os
import secrets
import time

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS fx_events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, take_id TEXT NOT NULL, "
    "clip_id TEXT NOT NULL, kind TEXT NOT NULL, template TEXT, text TEXT, decision TEXT NOT NULL, why TEXT, "
    "render_ms INTEGER, created REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS fx_events_user ON fx_events(user_id, created)",
    "CREATE INDEX IF NOT EXISTS fx_events_clip ON fx_events(take_id, clip_id)",
]
COLS = ("id", "user_id", "take_id", "clip_id", "kind", "template", "text", "decision", "why", "render_ms", "created")
D1_API = "https://api.cloudflare.com/client/v4/accounts/{acct}/d1/database/{db}/query"


class StoreBackend:
    name = "store"

    def __init__(self, store):
        self.store = store
        for sql in SCHEMA:
            store.x(sql)

    def run(self, sql: str, args: tuple = ()) -> list[dict]:
        if sql.lstrip().upper().startswith("SELECT"):
            return [dict(r) for r in self.store.q(sql, args)]
        self.store.x(sql, args)
        return []


class D1Backend:
    name = "d1"

    def __init__(self, account: str, database: str, token: str, client=None):
        self.url = D1_API.format(acct=account, db=database)
        self.token = token
        self.client = client
        self._ready = False

    def _post(self, sql: str, args: tuple) -> list[dict]:
        import httpx
        c = self.client or httpx.Client(timeout=8.0)
        try:
            r = c.post(self.url, headers={"Authorization": f"Bearer {self.token}"},
                       json={"sql": sql, "params": [a for a in args]})
            body = r.json()
        finally:
            if self.client is None:
                c.close()
        if r.status_code >= 400 or not body.get("success"):
            errs = "; ".join(str(e.get("message", ""))[:120] for e in body.get("errors") or [])
            raise RuntimeError(f"D1 answered {r.status_code}: {errs or 'no detail'}")
        res = body.get("result") or []
        return list((res[0] if res else {}).get("results") or [])

    def run(self, sql: str, args: tuple = ()) -> list[dict]:
        if not self._ready:
            for s in SCHEMA:
                self._post(s, ())
            self._ready = True
        return self._post(sql, args)


def backend(store=None):
    """The configured backend: D1 when HQ_FX_STORE=d1 and its three settings are there, else the app's store."""
    if os.environ.get("HQ_FX_STORE", "").strip().lower() == "d1":
        acct, db, tok = (os.environ.get(k, "").strip() for k in ("HQ_D1_ACCOUNT", "HQ_D1_DATABASE", "HQ_D1_TOKEN"))
        if acct and db and tok:
            return D1Backend(acct, db, tok)
        print("fx store: HQ_FX_STORE=d1 but HQ_D1_ACCOUNT / HQ_D1_DATABASE / HQ_D1_TOKEN aren't all set; using the app's database",
              flush=True)
    return StoreBackend(store) if store is not None else None


def rows_from_motion(uid: str, take_id: str, clip_id: str, motion: dict | None) -> list[dict]:
    """fx_events rows for one rendered clip's meta["motion"] (studio/media/motion.plan + any template render)."""
    if not isinstance(motion, dict) or not motion.get("on"):
        return []
    now = round(time.time(), 3)
    out = []
    for f in motion.get("fx") or []:
        out.append({"id": secrets.token_hex(8), "user_id": uid, "take_id": take_id, "clip_id": clip_id,
                    "kind": str(f.get("kind") or "")[:20], "template": (f.get("template") or {}).get("template")
                    if isinstance(f.get("template"), dict) else None, "text": str(f.get("text") or "")[:120],
                    "decision": "chose", "why": str(f.get("why") or "")[:300],
                    "render_ms": (f.get("template") or {}).get("ms") if isinstance(f.get("template"), dict) else None,
                    "created": now})
    for why in motion.get("skipped") or []:
        out.append({"id": secrets.token_hex(8), "user_id": uid, "take_id": take_id, "clip_id": clip_id, "kind": "none",
                    "template": None, "text": None, "decision": "skipped", "why": str(why)[:300], "render_ms": None,
                    "created": now})
    return out


def record(be, rows: list[dict]) -> int:
    """Write rows; returns how many were kept. Never raises."""
    n = 0
    if be is None:
        return 0
    for r in rows:
        try:
            be.run(f"INSERT INTO fx_events ({', '.join(COLS)}) VALUES ({', '.join('?' for _ in COLS)})",
                   tuple(r.get(c) for c in COLS))
            n += 1
        except Exception as e:  # noqa: BLE001 - notes about a render never fail the take
            print(f"fx store ({be.name}): row dropped ({e.__class__.__name__}: {str(e)[:120]})", flush=True)
            break
    return n


def for_clip(be, take_id: str, clip_id: str, uid: str) -> list[dict]:
    try:
        return be.run("SELECT kind, template, text, decision, why, render_ms, created FROM fx_events "
                      "WHERE take_id=? AND clip_id=? AND user_id=? ORDER BY created", (take_id, clip_id, uid)) if be else []
    except Exception:  # noqa: BLE001
        return []


def forget_user(be, uid: str) -> None:
    if be is not None:
        be.run("DELETE FROM fx_events WHERE user_id=?", (uid,))


def export_rows(be, batch: int = 500) -> list[dict]:
    """Every row, oldest first: to move to the other backend with import_rows."""
    out, last, lid = [], -1.0, ""
    while True:
        rows = be.run(f"SELECT {', '.join(COLS)} FROM fx_events WHERE created > ? OR (created = ? AND id > ?) "
                      f"ORDER BY created, id LIMIT {int(batch)}", (last, last, lid))
        if not rows:
            return out
        out += rows
        last, lid = float(rows[-1]["created"]), str(rows[-1]["id"])
        if len(rows) < batch:
            return out


def import_rows(be, rows: list[dict]) -> int:
    n = 0
    for r in rows:
        be.run(f"INSERT INTO fx_events ({', '.join(COLS)}) VALUES ({', '.join('?' for _ in COLS)}) ON CONFLICT (id) DO NOTHING",
               tuple(r.get(c) for c in COLS))
        n += 1
    return n
