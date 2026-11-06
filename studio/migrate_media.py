"""Move media bytes out of the database into R2 (docs/STORAGE.md).

    python -m studio.migrate_media --to r2 --dry-run      # counts and bytes only: nothing is read from or written to R2
    python -m studio.migrate_media --to r2 --report --real-email you@example.com   # row by row: size, owner hash, demo/real/other
    python -m studio.migrate_media --to r2                # COPY (default): each row gets its R2 key after a verified copy and
                                                          # keeps its BYTEA; readers switch to R2 (needs R2_* set)
    python -m studio.migrate_media --to r2 --limit 200    # at most 200 rows this run
    python -m studio.migrate_media --to r2 --cleanup --i-have-a-verified-backup     # later, with Ram's OK: drop DB copies
                                                          # whose object re-verifies (sha256)

For every ``media`` row and every Library chunk (``library_chunks``) whose bytes are still in the row:

1. sha256 of the bytes;
2. PUT to a fresh random key under the owner's prefix (``media/<owner>/…`` or ``lib/<owner>/…``); a new key every attempt,
   so a failed attempt's queued delete can never hit the copy a later attempt verified (Greptile, PR #110);
3. GET it back and compare length and sha256;
4. only then one UPDATE, guarded on the row not having moved meanwhile: ``storage_key`` and ``sha256`` set. By default
   (copy-only, Ram 5 Nov 2026) the BYTEA stays as a second copy until ``--cleanup``; ``--drop-db-copy`` sets it NULL
   at once. Both need ``--i-have-a-verified-backup`` before any database copy is dropped.

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

from . import blobstore, r2, r2usage

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


def _digest(b: "r2.Bucket", key: str) -> tuple[int, str]:
    """(length, sha256) of the object as the bucket serves it, streamed (never a second copy of the file in memory)."""
    if hasattr(b, "digest"):
        return b.digest(key)
    got = b.get(key)
    return len(got), hashlib.sha256(got).hexdigest()


def _verify_put(b: "r2.Bucket", key: str, data: bytes, mime: str) -> bool:
    digest = hashlib.sha256(data).hexdigest()
    b.put(key, data, mime or "application/octet-stream")
    n, got = _digest(b, key)
    return n == len(data) and got == digest


def migrate(store, bucket: "r2.Bucket", limit: int | None = None, log=print, keep_db_copy: bool = True, usage=None,
            on_row=None) -> dict:
    """Move rows (see the module docstring). Returns counts; one bad row never stops the rest. ``keep_db_copy``: copy
    only: the row takes the key and hash but keeps its BYTEA (readers use R2 from then on; ``cleanup`` drops the
    database copy later, after Ram approves). ``usage``: the store whose r2_usage table keeps the operation counts
    (default ``store``; the in-process run passes the app's own store and does the row work on a dedicated
    connection). ``on_row(stats)`` is called after every row (progress)."""
    from . import egress
    stats = {"moved": 0, "bytes": 0, "failed": 0, "skipped": 0, "capped": ""}
    left = limit if limit is not None else float("inf")
    u = usage if usage is not None else store
    r2usage.bind(u)
    r2usage.forget_cache()
    base = r2usage.stored_bytes(u)  # what R2 already holds; this run adds stats["bytes"] (no stale cache mid-run)

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
        data = got[0]["data"]
        del got
        data = data if isinstance(data, bytes) else bytes(data)
        egress.migration_read(len(data))  # metered apart: a one-off copy never trips the daily preview budget
        # the free tier is never exceeded (studio/r2usage.py): stop before a copy that would pass a cap; the remaining
        # rows stay in the database, untouched. A verified copy is one PUT (Class A) + one GET (Class B).
        cap = r2usage.caps()
        why = "" if base + stats["bytes"] + len(data) <= cap["bytes"] else \
            f"R2 storage cap reached ({cap['bytes'] / r2usage.GB:.1f} GB, HQ_R2_MAX_BYTES)"
        if not why and r2usage.ops(u)["A"] + 1 > cap["A"]:
            why = f"R2 monthly write cap reached ({cap['A']:,} Class A operations, HQ_R2_MAX_CLASS_A)"
        if not why:  # the operator's r2_uploads kill switch (studio/opsctl.py) stops the copy too
            from . import opsctl
            why = opsctl.message("r2_uploads") if opsctl.paused("r2_uploads", u) else ""
        why = why or r2usage.can_read(u) or ""
        if why:
            stats["capped"] = why
            log(f"stopping: {why}; the remaining rows stay in the database")
            left = 0
            return
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
        drop = "" if keep_db_copy else ", data=NULL"
        store.x(f"UPDATE {table} SET storage_key=?, sha256=?{extra}{drop} WHERE {where} AND storage_key IS NULL",
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
        if on_row:
            on_row(stats)
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
        if on_row:
            on_row(stats)
    r2usage.flush(u)
    return stats


def report(store, real_emails: list[str] | None = None, limit: int = 2000) -> dict:
    """Dry run, row by row: size, owner (a short hash, never the id or email), whose content it is, and where it lives.
    ``kind``: ``demo`` (a free-sample workspace), ``real`` (an account whose email is in ``real_emails``, e.g. Ram's
    own) or ``other``. Tokens are shown by their first 6 characters only (a token is the file's capability URL).
    Reads metadata only: no bytes leave the database."""
    from . import demo_guard
    real = {e.strip().lower() for e in (real_emails or []) if e and e.strip()}
    emails = {r["id"]: (r["email"] or "").lower() for r in store.q("SELECT id, email FROM users")}

    def kind(uid: str) -> str:
        if uid and demo_guard.is_demo(uid):
            return "demo"
        return "real" if emails.get(uid or "") in real else "other"

    def who(uid: str) -> str:
        return hashlib.sha256(("owner:" + (uid or "")).encode()).hexdigest()[:10]
    rows, totals = [], {}
    for r in store.q("SELECT token, user_id, mime, size, storage_key, CASE WHEN data IS NULL THEN 0 ELSE 1 END AS in_db, "
                     "created FROM media ORDER BY size DESC LIMIT ?", (int(limit),)):
        k = kind(r["user_id"])
        where = ("r2+db" if r["in_db"] else "r2") if r["storage_key"] else ("db" if r["in_db"] else "none")
        rows.append({"table": "media", "token": str(r["token"])[:6] + "…", "owner": who(r["user_id"]), "kind": k,
                     "mime": r["mime"], "bytes": int(r["size"] or 0), "where": where})
        t = totals.setdefault(k, {"rows": 0, "bytes": 0})
        t["rows"] += 1
        t["bytes"] += int(r["size"] or 0)
    try:
        for r in store.q("SELECT i.id, i.user_id, i.mime, i.size, SUM(CASE WHEN c.storage_key IS NULL THEN 0 ELSE 1 END) AS in_r2, "
                         "SUM(CASE WHEN c.data IS NULL THEN 0 ELSE 1 END) AS in_db, COUNT(c.n) AS chunks FROM library_items i JOIN library_chunks c ON c.item_id=i.id GROUP BY i.id, i.user_id, i.mime, i.size"):
            k = kind(r["user_id"])
            rows.append({"table": "library_chunks", "item": int(r["id"]), "owner": who(r["user_id"]), "kind": k, "mime": r["mime"],
                         "bytes": int(r["size"] or 0), "where": (("r2+db" if int(r["in_db"] or 0) == int(r["chunks"] or 0) else "r2+partial-db") if int(r["in_db"] or 0)
                                   else "r2") if int(r["in_r2"] or 0) == int(r["chunks"] or 0) else "db"})
            t = totals.setdefault(k, {"rows": 0, "bytes": 0})
            t["rows"] += 1
            t["bytes"] += int(r["size"] or 0)
    except Exception:  # noqa: BLE001  (no Library tables)
        pass
    return {"rows": rows, "totals": totals}


def cleanup(store, bucket: "r2.Bucket", log=print, usage=None, on_row=None) -> dict:
    """After a copy-only run and Ram's approval: drop the database copy of every row whose object verifies (same
    length and sha256 as recorded at copy time). A row that doesn't verify keeps its bytes. ``usage`` / ``on_row``: as
    for migrate()."""
    stats = {"dropped": 0, "kept": 0, "capped": ""}
    u = usage if usage is not None else store
    r2usage.bind(u)
    for table, where_cols in (("media", ("token",)), ("library_chunks", ("item_id", "n"))):
        if table != "media" and table not in store._table_names():
            continue  # no Library tables; any other failed scan raises, never "nothing to clean" (CodeRabbit, PR #120)
        todo = store.q(f"SELECT {', '.join(where_cols)}, storage_key, sha256 FROM {table} "
                       "WHERE storage_key IS NOT NULL AND data IS NOT NULL")
        for r in todo:
            where = " AND ".join(f"{c}=?" for c in where_cols)
            args = tuple(r[c] for c in where_cols)
            why = r2usage.can_read(u)
            if why:  # re-verifying reads the object (Class B): stop at the cap, every remaining row keeps its copy
                stats["capped"] = why
                log(f"stopping: {why}")
                r2usage.flush(u)
                return stats
            try:
                _n, got = _digest(bucket, r["storage_key"])
                ok = bool(r["sha256"]) and got == r["sha256"]
            except r2.R2Error as e:
                log(f"{table}: {e}")
                ok = False
            if not ok:
                stats["kept"] += 1
                if on_row:
                    on_row(stats)
                continue
            # guarded on the key and the hash it verified: a row rewritten meanwhile keeps its database copy
            n = store.xn(f"UPDATE {table} SET data=NULL WHERE {where} AND storage_key=? AND sha256=?",
                         (*args, r["storage_key"], r["sha256"]))
            stats["dropped" if n else "kept"] += 1  # only copies the UPDATE actually dropped count (CodeRabbit, PR #127)
            if on_row:
                on_row(stats)
    r2usage.flush(u)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.migrate_media", description="Move media bytes from the database to R2.")
    ap.add_argument("--to", choices=["r2"], required=True, help="where the bytes go (only r2 for now)")
    ap.add_argument("--dry-run", action="store_true", help="count rows and bytes; read and write nothing in R2")
    ap.add_argument("--limit", type=int, default=None, help="move at most this many rows")
    ap.add_argument("--report", action="store_true", help="dry run, row by row (size, owner hash, demo/real/other, where)")
    ap.add_argument("--real-email", action="append", default=[], help="an account email whose rows count as real content")
    ap.add_argument("--drop-db-copy", action="store_true",
                    help="move instead of copy: the row's BYTEA is set NULL once its copy verifies (needs --i-have-a-verified-backup)")
    ap.add_argument("--cleanup", action="store_true",
                    help="drop the database copy of rows already copied, after re-verifying each object (needs --i-have-a-verified-backup)")
    ap.add_argument("--i-have-a-verified-backup", dest="backup_ok", action="store_true",
                    help="confirms a pg_dump was taken and restored (docs/DATA_RESET_PLAN.md) and Ram approved dropping copies")
    a = ap.parse_args(argv)
    if (a.drop_db_copy or a.cleanup) and not a.backup_ok:
        print("Dropping the database copy needs a verified backup and Ram's approval: add --i-have-a-verified-backup "
              "(docs/DATA_RESET_PLAN.md).", file=sys.stderr)
        return 2
    from . import library
    from .config import load_settings
    from .store import Store
    s = load_settings()
    store = Store(s.db_path, s.session_secret, s.token_key, s.token_keys_previous)
    library.ensure(store)  # the chunk table's storage_key column
    before = plan(store)
    cfg = r2.config()
    if a.report:
        print(json.dumps({"dry_run": True, "r2_configured": bool(cfg), **report(store, a.real_email)}, indent=2))
        return 0
    if a.dry_run:
        print(json.dumps({"dry_run": True, "r2_configured": bool(cfg), "would_move": before}, indent=2))
        return 0
    if not cfg:
        print("R2 is not configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET).", file=sys.stderr)
        return 2
    if a.cleanup:
        stats = cleanup(store, r2.Bucket(cfg))
        print(json.dumps({"cleanup": stats, "next": "VACUUM FULL media, library_chunks; returns the freed space"}, indent=2))
        return 0 if not (stats["kept"] or stats["capped"]) else 1
    stats = migrate(store, r2.Bucket(cfg), limit=a.limit, keep_db_copy=not a.drop_db_copy)
    print(json.dumps({"before": before, "result": stats, "after": plan(store), "kept_db_copy": not a.drop_db_copy,
                      "next": ("readers now use R2; the database copies stay until --cleanup (Ram's approval)" if not a.drop_db_copy
                               else "VACUUM FULL media, library_chunks; returns the freed space to the database quota")}, indent=2))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
