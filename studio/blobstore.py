"""Where media bytes live: a storage backend behind the ``media`` and ``library_chunks`` tables (docs/STORAGE.md).

* ``db`` (the default, and the fallback whenever R2 isn't configured): the bytes stay in the row (``data`` BYTEA/BLOB),
  as before.
* ``r2``: the row keeps its metadata and an object key (``storage_key``, ``sha256``); the bytes go to the configured
  S3-compatible bucket (studio/r2.py, the same ``R2_*`` settings as One take's uploads). Keys sit under a per-person prefix
  (a hash of the account id, never the id itself) and end in a random part: ``media/<owner>/<random>``,
  ``lib/<owner>/<random>``. The bucket stays private: every read goes through this server's own /m/, /ms/ and
  /api/library routes, with their ownership checks unchanged.

Why: the hosted database is Supabase's free Postgres (500 MB for everything) and media made up most of it; R2's free tier
holds 10 GB and has no egress fees.

Deleting a row never deletes an object inline. ``release()`` writes the row's key into ``blob_gc`` in the same statement
batch (inside the caller's transaction when there is one) and then deletes the row; ``sweep()`` deletes the objects
afterwards and keeps a key until the bucket confirms, so a rolled-back transaction can't lose bytes and a failed delete is
retried rather than forgotten.

``HQ_MEDIA_STORAGE=db`` keeps new media in the database even when R2 is configured (One take can use R2 on its own).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time

from . import r2, r2usage

KINDS = ("media", "lib")
_HTTP = None            # tests put a stub S3 transport here (an httpx.Client)
_SWEEP = threading.Lock()
_THREADS: list = []     # background sweeps still running (join_background: tests and shutdown)
_THREADS_LOCK = threading.Lock()
# Every bucket call is bounded: 5 s to connect, 20 s for the rest (a 1 MB chunk), never the old 60 s (Greptile, PR #110).
CONNECT_S, CALL_S = 5.0, 20.0


class StorageError(Exception):
    pass


def set_http(client) -> None:
    global _HTTP
    _HTTP = client


_SHARED = None


def _client():
    """One pooled client for every bucket call (tests swap in a stub with set_http)."""
    global _SHARED
    if _HTTP is not None:
        return _HTTP
    if _SHARED is None:
        import httpx
        _SHARED = httpx.Client(timeout=httpx.Timeout(CALL_S, connect=CONNECT_S), trust_env=False)
    return _SHARED


def _bucket(cfg) -> "r2.Bucket":
    import httpx
    return r2.Bucket(cfg, http=_client(), timeout=httpx.Timeout(CALL_S, connect=CONNECT_S))


def bucket(write: bool = False) -> "r2.Bucket | None":
    """The bucket, or None. ``write``: where NEW bytes go, which HQ_MEDIA_STORAGE=db turns off; reads, rewrites and
    deletes of objects already in R2 always use the bucket while it is configured (Greptile, PR #110)."""
    if write and os.environ.get("HQ_MEDIA_STORAGE", "auto").strip().lower() == "db":
        return None
    cfg = r2.config()
    return _bucket(cfg) if cfg else None


def r2_readable() -> bool:
    """R2 is configured and this month's read cap isn't reached: a row with an object key is read from the bucket."""
    return bool(bucket()) and not r2usage.can_read(grace=False)


def backend_name() -> str:
    """Where new media goes."""
    return "r2" if bucket(write=True) else "db"


def owner_prefix(uid: str) -> str:
    """The per-person folder: a hash of the account id, so object keys never carry it."""
    return hashlib.sha256(("hq-owner:" + str(uid or "")).encode()).hexdigest()[:20]


def new_key(kind: str, uid: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown blob kind {kind!r}")
    return f"{kind}/{owner_prefix(uid)}/{secrets.token_urlsafe(24)}"


def put(kind: str, uid: str, data: bytes, mime: str = "application/octet-stream") -> tuple[bytes | None, str | None, str]:
    """Store bytes: (what goes in the row's ``data``, its ``storage_key``, sha256). With R2 the row gets no bytes; when the
    bucket refuses or is down the bytes stay in the row (the upload never fails because the bucket did) and it's logged."""
    digest = hashlib.sha256(data).hexdigest()
    b = bucket(write=True)
    if not b:
        return data, None, digest
    capped = r2usage.reserve(len(data))
    if capped:  # the free tier is never exceeded (studio/r2usage.py): new bytes stay in the database instead
        print(f"storage: {capped}; kept this {kind} blob in the database", flush=True)
        return data, None, digest
    key = new_key(kind, uid)
    try:
        b.put(key, data, mime)
    except r2.R2Error as e:
        r2usage.release(len(data))
        print(f"storage: R2 put failed ({e}); kept this {kind} blob in the database", flush=True)
        return data, None, digest
    return None, key, digest


def read(data, key: str | None, start: int | None = None, end: int | None = None) -> bytes:
    """A row's bytes (all, or [start, end] inclusive) wherever they are. A row with an object key is read from the
    bucket whenever the bucket is configured, even if the row still holds a database copy (a copy-only migration keeps
    the BYTEA until Ram approves the cleanup: reading R2 is what stops the database egress). With the bucket off, the
    database copy (if any) is used."""
    b = bucket() if key else None
    capped = r2usage.can_read(grace=False) if b else None
    if data is not None and (not key or not b or capped):  # past the monthly read cap a database copy still serves
        blob = bytes(data or b"")
        if start is None and end is None:
            return blob
        return blob[int(start or 0):(int(end) + 1) if end is not None else None]
    if not key:
        return b""
    if not b:
        raise StorageError("This file is in object storage, which isn't configured on this server.")
    capped = r2usage.can_read() if capped else None  # the owner's grace (r2usage.owner_grace) applies to R2-only rows
    if capped:
        raise StorageError(f"This file is in object storage and the {capped}; it is back when the month turns (UTC).")
    try:
        got = b.get(key, start, end)
    except r2.R2Error as e:
        raise StorageError(f"Couldn't read this file from storage ({e}).") from None
    from . import egress
    egress.note_r2_read(len(got))  # R2 bytes are not database egress (studio/egress.py r2_tally)
    return got


def rewrite(store, table: str, where: str, args: tuple, data: bytes) -> None:
    """Replace one row's bytes in place (e.g. a video's GPS blanked): same key in the bucket, or the row's data."""
    rows = store.q(f"SELECT storage_key FROM {table} WHERE {where}", args)
    if not rows:
        return
    key = rows[0]["storage_key"]
    digest = hashlib.sha256(data).hexdigest()
    if key:
        b = bucket()
        if not b:
            raise StorageError("This file is in object storage, which isn't configured on this server.")
        capped = r2usage.can_write(0)
        if capped:  # privacy steps fail closed (a 503 upstream), never a paid write
            raise StorageError(f"Couldn't update this file in storage: {capped}.")
        try:
            b.put(key, data)
        except r2.R2Error as e:
            raise StorageError(f"Couldn't update this file in storage ({e}).") from None
        # a copy-only row (migrate_media keeps the BYTEA until cleanup) gets the same new bytes, so the database copy
        # never keeps what the rewrite removed (e.g. a video's GPS)
        store.x(f"UPDATE {table} SET sha256=?, data=CASE WHEN data IS NULL THEN NULL ELSE ? END WHERE {where}", (digest, data, *args))
    else:
        store.x(f"UPDATE {table} SET data=?, sha256=? WHERE {where}", (data, digest, *args))


def discard(store, key: str | None) -> None:
    """An object no row ever pointed at (its row's INSERT failed): delete it now; when the bucket doesn't confirm,
    queue it in blob_gc so the sweep retries (CodeRabbit, PR #110)."""
    if not key:
        return
    b = bucket()
    try:
        ok = bool(b and b.delete(key))
    except r2.R2Error:
        ok = False
    if not ok:
        try:
            store.x("INSERT INTO blob_gc(key, at, tries) VALUES(?,?,0) ON CONFLICT(key) DO UPDATE SET at=excluded.at", (key, time.time()))
        except Exception:  # noqa: BLE001  (the store itself is failing: that's what is being raised already)
            print("storage: couldn't queue an unreferenced object for deletion", flush=True)


def undo_on_rollback(store, key: str | None) -> None:
    """The row pointing at this new object was written inside a transaction: if that transaction rolls back, the object
    has no row and is discarded (Greptile, PR #110). Outside a transaction the row is already committed: nothing to do."""
    hook = getattr(store, "on_rollback", None)
    if key and hook:
        hook(lambda: discard(store, key))


def release(store, table: str, where: str, args: tuple = ()) -> None:
    """DELETE FROM table WHERE where, noting every object key those rows had in blob_gc first, in one transaction
    (joins the caller's when there is one)."""
    if table not in ("media", "library_chunks"):
        raise ValueError(f"not a blob table: {table!r}")
    with store.tx():  # a key already noted (an in-flight migration's note) becomes due now
        store.x(f"INSERT INTO blob_gc(key, at, tries) SELECT storage_key, ?, 0 FROM {table} WHERE ({where}) AND storage_key IS NOT NULL "
                "ON CONFLICT(key) DO UPDATE SET at=excluded.at", (time.time(), *args))
        store.x(f"DELETE FROM {table} WHERE {where}", args)


def referenced(store, key: str) -> bool:
    """A row still points at this object (never delete it, whatever blob_gc says)."""
    if store.q("SELECT 1 AS x FROM media WHERE storage_key=? LIMIT 1", (key,)):
        return True
    try:
        return bool(store.q("SELECT 1 AS x FROM library_chunks WHERE storage_key=? LIMIT 1", (key,)))
    except Exception as e:  # only a store without the Library's tables counts as "no chunk uses it"; anything else raises
        msg = str(e).lower()
        if "no such table" in msg or ("library_chunks" in msg and "does not exist" in msg):
            return False
        raise


def pending(store) -> int:
    return int(store.q("SELECT COUNT(*) AS n FROM blob_gc WHERE at<=?", (time.time(),))[0]["n"] or 0)


def sweep(store, limit: int = 500, wait: float = 0) -> dict:
    """Delete released objects from the bucket; a key leaves blob_gc only once the bucket confirms. Never inside a
    transaction (it could still roll back), never two at once."""
    # inside a transaction on THIS thread: skip (it could still roll back). Another thread's open transaction holds the
    # store's lock, so a background sweep's first query simply waits for it to commit (CodeRabbit, PR #110).
    in_tx = getattr(store, "_tx_depth", 0) and getattr(store._lock, "_is_owned", lambda: True)()
    if in_tx or not (_SWEEP.acquire(timeout=wait) if wait else _SWEEP.acquire(blocking=False)):  # wait: tests, for a running sweep
        return {"deleted": 0, "failed": 0, "skipped": True}
    try:
        # at > now: an upload in flight noted ahead of time (migrate_media); it becomes due if nothing ever claims it
        rows = store.q("SELECT key FROM blob_gc WHERE key IS NOT NULL AND at<=? ORDER BY at LIMIT ?", (time.time(), limit))
        if not rows:
            return {"deleted": 0, "failed": 0}
        cfg = r2.config()
        if not cfg:  # keys stay noted until the bucket is configured again
            return {"deleted": 0, "failed": len(rows), "why": "R2 not configured"}
        b = _bucket(cfg)
        done = failed = 0
        for r in rows:
            try:
                used = referenced(store, r["key"])
            except Exception:  # noqa: BLE001  can't tell: keep the key queued and try again later
                failed += 1
                continue
            if used:  # belt and braces: a key some row still uses leaves the queue, undeleted
                store.x("DELETE FROM blob_gc WHERE key=?", (r["key"],))
                continue
            try:
                ok = b.delete(r["key"])
            except r2.R2Error:
                ok = False
            if ok:
                store.x("DELETE FROM blob_gc WHERE key=?", (r["key"],))
                done += 1
            else:
                store.x("UPDATE blob_gc SET tries=tries+1 WHERE key=?", (r["key"],))
                failed += 1
        return {"deleted": done, "failed": failed}
    finally:
        _SWEEP.release()


def sweep_soon(store) -> None:
    """sweep() on a background thread when there is anything to delete (a purge never waits on the bucket). Inside a
    transaction it is scheduled for after the commit (dropped on rollback), so it never runs against uncommitted rows
    (CodeRabbit, PR #110)."""
    hook = getattr(store, "after_commit", None)
    if hook:
        hook(lambda: _kick(store))
    else:
        _kick(store)


def _kick(store) -> None:
    try:
        if not pending(store):
            return
    except Exception:  # noqa: BLE001  (no blob_gc yet)
        return
    _spawn(store)


def _spawn(store) -> None:
    def run():
        try:
            sweep(store)
        except Exception as e:  # noqa: BLE001  the periodic sweep retries
            print(f"storage: sweep failed ({e.__class__.__name__})", flush=True)
    t = threading.Thread(target=run, name="hq-blob-sweep", daemon=True)
    with _THREADS_LOCK:
        _THREADS[:] = [x for x in _THREADS if x.is_alive()]
        _THREADS.append(t)
    t.start()


def join_background(timeout: float = 10) -> bool:
    """Wait for every background sweep started so far; True when none is left running (tests' teardown, shutdown)."""
    end = time.monotonic() + timeout
    with _THREADS_LOCK:
        threads = list(_THREADS)
    for t in threads:
        t.join(max(0.0, end - time.monotonic()))
    with _THREADS_LOCK:
        _THREADS[:] = [x for x in _THREADS if x.is_alive()]
        return not _THREADS


def usage(store, uid: str | None = None) -> dict:
    """Bytes per backend (media rows + Library chunks), for one person or everyone."""
    w, a = ("WHERE user_id=?", (uid,)) if uid else ("", ())
    m = store.q(f"SELECT COALESCE(SUM(CASE WHEN storage_key IS NULL THEN size ELSE 0 END),0) AS db, "
                f"COALESCE(SUM(CASE WHEN storage_key IS NOT NULL THEN size ELSE 0 END),0) AS r2 FROM media {w}", a)[0]
    out = {"db": int(m["db"] or 0), "r2": int(m["r2"] or 0)}
    try:
        cw = "WHERE c.item_id IN (SELECT id FROM library_items WHERE user_id=?)" if uid else ""
        c = store.q("SELECT COALESCE(SUM(CASE WHEN c.storage_key IS NULL THEN COALESCE(c.nbytes, LENGTH(c.data)) ELSE 0 END),0) AS db, "
                    "COALESCE(SUM(CASE WHEN c.storage_key IS NOT NULL THEN COALESCE(c.nbytes,0) ELSE 0 END),0) AS r2, "
                    "COUNT(CASE WHEN c.storage_key IS NOT NULL THEN 1 END) AS r2n FROM library_chunks c " + cw, a)[0]
        out["db"] += int(c["db"] or 0)
        out["r2"] += int(c["r2"] or 0)
        out["r2_chunks"] = int(c["r2n"] or 0)
    except Exception:  # noqa: BLE001  (no Library tables in this store)
        pass
    return out
