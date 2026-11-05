"""Move media bytes out of the database into R2 (docs/STORAGE.md).

    python -m studio.migrate_media --to r2 --dry-run      # counts and bytes only: nothing is read from or written to R2
    python -m studio.migrate_media --to r2                # move (needs R2_* set, and the same database settings as the app)
    python -m studio.migrate_media --to r2 --limit 200    # at most 200 rows this run

For every ``media`` row and every Library chunk (``library_chunks``) whose bytes are still in the row:

1. sha256 of the bytes;
2. PUT to a fresh random key under the owner's prefix (``media/<owner>/…`` or ``lib/<owner>/…``); a new key every attempt,
   so a failed attempt's queued delete can never hit the copy a later attempt verified (Greptile, PR #110);
3. GET it back and compare length and sha256;
4. only then one UPDATE, guarded on the row not having moved meanwhile: ``storage_key`` and ``sha256`` set, ``data`` NULL.

A row whose copy fails the check keeps its bytes (that attempt's object goes to blob_gc). Rows already moved are skipped,
so running it again is safe and finishes what an interrupted run left. Nothing is deleted before it is verified. Chunks of
an upload still arriving (``library_items.storage='pending'``) are skipped: its GPS scrub may still rewrite them.

Postgres keeps the freed space for reuse; to give it back to the 500 MB quota run ``VACUUM FULL media, library_chunks;``
afterwards (it locks those tables while it runs: pick a quiet moment). Ram approves running this on production.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time

from . import blobstore, r2

def plan(store) -> dict:
    """What a run would move: rows and bytes per table, and how many rows are already in R2."""
    m = store.q("SELECT COUNT(*) AS n, COALESCE(SUM(size),0) AS b FROM media WHERE data IS NOT NULL AND storage_key IS NULL")[0]
    m_done = store.q("SELECT COUNT(*) AS n FROM media WHERE storage_key IS NOT NULL")[0]
    out = {"media": {"rows": int(m["n"] or 0), "bytes": int(m["b"] or 0), "already_in_r2": int(m_done["n"] or 0)}}
    try:
        c = store.q("SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(data)),0) AS b FROM library_chunks "
                    "WHERE data IS NOT NULL AND storage_key IS NULL")[0]
        c_done = store.q("SELECT COUNT(*) AS n FROM library_chunks WHERE storage_key IS NOT NULL")[0]
        out["library_chunks"] = {"rows": int(c["n"] or 0), "bytes": int(c["b"] or 0), "already_in_r2": int(c_done["n"] or 0)}
    except Exception:  # noqa: BLE001  (no Library tables)
        out["library_chunks"] = {"rows": 0, "bytes": 0, "already_in_r2": 0}
    return out


def _verify_put(b: "r2.Bucket", key: str, data: bytes, mime: str) -> bool:
    digest = hashlib.sha256(data).hexdigest()
    b.put(key, data, mime or "application/octet-stream")
    got = b.get(key)
    return len(got) == len(data) and hashlib.sha256(got).hexdigest() == digest


def migrate(store, bucket: "r2.Bucket", limit: int | None = None, log=print) -> dict:
    """Move rows (see the module docstring). Returns counts; one bad row never stops the rest."""
    stats = {"moved": 0, "bytes": 0, "failed": 0, "skipped": 0}
    left = limit if limit is not None else float("inf")

    def gc(key: str) -> None:  # due now
        store.x("INSERT INTO blob_gc(key,at,tries) VALUES(?,?,0) ON CONFLICT(key) DO UPDATE SET at=excluded.at", (key, time.time()))

    def noted(key: str) -> None:
        """Before the PUT: the key is in blob_gc, due in a day. If this run dies before the row takes the key, the
        sweep deletes the object then; once a row references it, the sweep drops the note instead. The note is never
        cleared here: a purge racing this run may delete the row after it took the key, and the note is then what
        deletes the object (Greptile, PR #110)."""
        store.x("INSERT INTO blob_gc(key,at,tries) VALUES(?,?,0) ON CONFLICT(key) DO NOTHING", (key, time.time() + 86400))

    def one(table: str, where: str, args: tuple, kind: str, uid: str, mime: str) -> None:
        nonlocal left
        got = store.q(f"SELECT data FROM {table} WHERE {where} AND storage_key IS NULL", args)
        if not got or got[0]["data"] is None:
            stats["skipped"] += 1
            return
        data = bytes(got[0]["data"])
        digest = hashlib.sha256(data).hexdigest()
        key = blobstore.new_key(kind, uid)
        noted(key)
        try:
            ok = _verify_put(bucket, key, data, mime)
        except r2.R2Error as e:
            ok = False
            log(f"{table}: {e}")
        if not ok:
            stats["failed"] += 1
            gc(key)
            return
        # the bytes leave the row only once their copy is verified, and only if the row hasn't moved meanwhile
        extra = ", nbytes=?" if table == "library_chunks" else ""
        store.x(f"UPDATE {table} SET storage_key=?, sha256=?{extra}, data=NULL WHERE {where} AND storage_key IS NULL",
                (key, digest, *((len(data),) if extra else ()), *args))
        now = store.q(f"SELECT storage_key FROM {table} WHERE {where}", args)
        if not now or now[0]["storage_key"] != key:  # renamed or deleted meanwhile: the row didn't take the key
            gc(key)
            stats["skipped"] += 1
            return
        stats["moved"] += 1
        stats["bytes"] += len(data)
        left -= 1

    # the list of ids first (small), so a failed row never stalls the ones after it
    todo = [(r["token"], r["user_id"], r["mime"]) for r in
            store.q("SELECT token, user_id, mime FROM media WHERE data IS NOT NULL AND storage_key IS NULL ORDER BY created")]
    for token, uid, mime in todo:
        if left <= 0:
            break
        one("media", "token=?", (token,), "media", uid or "", mime)
    try:
        todo_c = [(r["item_id"], r["n"], r["user_id"]) for r in store.q(
            "SELECT c.item_id, c.n, i.user_id FROM library_chunks c JOIN library_items i ON i.id=c.item_id "
            "WHERE c.data IS NOT NULL AND c.storage_key IS NULL AND i.storage<>'pending' ORDER BY c.item_id, c.n")]
    except Exception:  # noqa: BLE001  (no Library tables)
        todo_c = []
    for item_id, n, uid in todo_c:
        if left <= 0:
            break
        one("library_chunks", "item_id=? AND n=?", (item_id, n), "lib", uid or "", "application/octet-stream")
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.migrate_media", description="Move media bytes from the database to R2.")
    ap.add_argument("--to", choices=["r2"], required=True, help="where the bytes go (only r2 for now)")
    ap.add_argument("--dry-run", action="store_true", help="count rows and bytes; read and write nothing in R2")
    ap.add_argument("--limit", type=int, default=None, help="move at most this many rows")
    a = ap.parse_args(argv)
    from . import library
    from .config import load_settings
    from .store import Store
    s = load_settings()
    store = Store(s.db_path, s.session_secret, s.token_key, s.token_keys_previous)
    library.ensure(store)  # the chunk table's storage_key column
    before = plan(store)
    cfg = r2.config()
    if a.dry_run:
        print(json.dumps({"dry_run": True, "r2_configured": bool(cfg), "would_move": before}, indent=2))
        return 0
    if not cfg:
        print("R2 is not configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET).", file=sys.stderr)
        return 2
    stats = migrate(store, r2.Bucket(cfg), limit=a.limit)
    print(json.dumps({"before": before, "result": stats, "after": plan(store),
                      "next": "VACUUM FULL media, library_chunks; returns the freed space to the database quota"}, indent=2))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
