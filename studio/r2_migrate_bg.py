"""The one-shot R2 migration, run inside the web service (docs/STORAGE.md#in-process-migration).

Render's free web service has no shell, and a Render Job is billed per second (FREE ONLY, Ram 4 Nov 2026), so the copy
and the cleanup of ``python -m studio.migrate_media`` also run here, on a background thread after boot, when the
operator sets ``HQ_R2_MIGRATE``:

* ``copy``: every ``media`` row and Library chunk still holding bytes is PUT to R2, read back and checked (length and
  sha256), and only then switched to its R2 key. The database copy stays (copy-only).
* ``cleanup``: every row already on R2 that still has its database copy: the object is re-downloaded and its sha256
  checked against the one recorded at copy time; only then is that row's database copy dropped. A row that doesn't
  verify keeps its bytes. Setting ``HQ_R2_MIGRATE=cleanup`` on the service is the operator's explicit approval (the
  CLI asks for ``--i-have-a-verified-backup`` instead).

Anything else (unset, ``off``, a typo) does nothing. Safety:

* one instance at a time: a Postgres advisory lock (``pg_try_advisory_lock``) on the migration's own connection; an
  instance that doesn't get it does nothing (during a deploy the old and the new instance overlap);
* one row at a time on its own database connection, so the web requests' shared connection is never held by a 26 MB
  read; at most one file is in memory (the read-back check streams and hashes the object);
* the R2 free-tier caps (studio/r2usage.py) stop it before any cap; the rows left stay in the database, untouched;
* idempotent and resumable: rows already moved are skipped, so a restart finishes what an interrupted run left;
* its database reads are metered apart (``egress.migration_read``) so a one-off copy never trips the daily media
  budget that throttles other people's previews;
* never in tests unless a test calls ``run()`` itself or sets ``HQ_R2_MIGRATE``.

Progress and the final report are numbers only: ``/healthz`` -> ``r2_migrate`` and ``GET /api/admin/egress`` ->
``r2_migrate`` (plus the live counts of rows still in the database).
"""
from __future__ import annotations

import os
import threading
import time

LOCK_KEY = 0x48515232  # "HQR2": the advisory lock id; every instance of this app uses the same one
MODES = ("copy", "cleanup")
_LOCK = threading.Lock()
_STATE: dict = {}
_PROC = threading.Lock()  # SQLite (tests, local): one run per process stands in for the advisory lock


def mode() -> str:
    m = os.environ.get("HQ_R2_MIGRATE", "").strip().lower()
    return m if m in MODES else ""


def _fresh(m: str) -> dict:
    return {"mode": m, "state": "idle", "started": None, "finished": None, "copied": 0, "verified": 0, "bytes": 0,
            "failed": 0, "skipped": 0, "dropped": 0, "kept": 0, "capped": False, "error": "", "before": {}, "after": {}}


def snapshot() -> dict:
    """Numbers and fixed words only (mode, state, an exception class name): never a key, token, id or URL."""
    with _LOCK:
        if not _STATE:
            return {"mode": mode(), "state": "idle" if mode() else "off"}
        out = {k: v for k, v in _STATE.items() if k not in ("before", "after")}
        out["capped"] = bool(_STATE.get("capped"))
        for k in ("before", "after"):
            if _STATE.get(k):
                out[k] = {t: {kk: int(vv) for kk, vv in d.items()} for t, d in _STATE[k].items()}
        return out


def _set(**kw) -> None:
    with _LOCK:
        _STATE.update(kw)


def reset() -> None:
    """Tests: forget the last run."""
    with _LOCK:
        _STATE.clear()


class _Conn:
    """A dedicated Postgres connection with the calls migrate_media needs (q, x, _table_names). It holds the advisory
    lock for the whole run, and its reads never wait on (or block) the requests' shared connection."""

    pg = True
    _tx_depth = 0

    def __init__(self, url: str):
        import psycopg
        from psycopg.rows import dict_row
        self._db = psycopg.connect(url, autocommit=True, row_factory=dict_row, prepare_threshold=None)

    def q(self, sql: str, args: tuple = ()) -> list:
        with self._db.cursor() as c:
            c.execute(sql.replace("?", "%s"), args)
            return c.fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self._db.cursor() as c:
            c.execute(sql.replace("?", "%s"), args)
            return c.rowcount

    def _table_names(self) -> set[str]:
        return {str(r["name"]) for r in
                self.q("SELECT table_name AS name FROM information_schema.tables WHERE table_schema = current_schema()")}

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass


def _acquire(store):
    """(work store, release) when this instance may run, else (None, None)."""
    if getattr(store, "pg", False):
        conn = _Conn(store.path)
        try:
            ok = bool(conn.q("SELECT pg_try_advisory_lock(?) AS ok", (LOCK_KEY,))[0]["ok"])
        except Exception:
            conn.close()
            raise
        if not ok:
            conn.close()
            return None, None

        def release() -> None:
            try:
                conn.q("SELECT pg_advisory_unlock(?) AS ok", (LOCK_KEY,))
            except Exception:  # noqa: BLE001  (closing the session releases it anyway)
                pass
            conn.close()
        return conn, release
    if not _PROC.acquire(blocking=False):
        return None, None
    return store, _PROC.release


def run(store, bucket=None, log=print) -> dict:
    """The whole run, synchronously (the thread calls this; tests call it directly). Returns snapshot()."""
    from . import blobstore, library, migrate_media, r2
    m = mode()
    with _LOCK:
        _STATE.clear()
        _STATE.update(_fresh(m))
    if not m:
        _set(state="off")
        return snapshot()
    cfg = r2.config()
    if not cfg and bucket is None:
        _set(state="not_configured")
        log("r2 migrate: R2 is not configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET); nothing done")
        return snapshot()
    try:
        work, release = _acquire(store)
    except Exception as e:  # noqa: BLE001
        _set(state="failed", error=e.__class__.__name__)
        log(f"r2 migrate: couldn't take the lock ({e.__class__.__name__})")
        return snapshot()
    if work is None:
        _set(state="other_instance")
        log("r2 migrate: another instance holds the lock; this one does nothing")
        return snapshot()
    _set(state="running", started=int(time.time()))
    log(f"r2 migrate: {m} started")
    try:
        try:
            library.ensure(store)
        except Exception:  # noqa: BLE001  (no Library: the media table still migrates)
            pass
        bucket = bucket or r2.Bucket(cfg, http=blobstore._client())
        _set(before=migrate_media.plan(work))

        def quiet(msg: str) -> None:
            log(f"r2 migrate: {msg}")
        if m == "copy":
            def tick(st: dict) -> None:
                _set(copied=st["moved"], verified=st["moved"], bytes=st["bytes"], failed=st["failed"],
                     skipped=st["skipped"], capped=bool(st["capped"]))
            st = migrate_media.migrate(work, bucket, log=quiet, keep_db_copy=True, usage=store, on_row=tick)
            tick(st)
            bad = st["failed"] or st["capped"]
        else:
            def tick(st: dict) -> None:
                _set(dropped=st["dropped"], verified=st["dropped"], kept=st["kept"], capped=bool(st["capped"]))
            st = migrate_media.cleanup(work, bucket, log=quiet, usage=store, on_row=tick)
            tick(st)
            bad = st["kept"] or st["capped"]
        _set(after=migrate_media.plan(work), state="done_with_errors" if bad else "done", finished=int(time.time()))
    except Exception as e:  # noqa: BLE001  (a database or bucket outage mid-run: rows not yet switched keep their bytes)
        _set(state="failed", error=e.__class__.__name__, finished=int(time.time()))
    finally:
        release()
    s = snapshot()
    log("r2 migrate: report " + " ".join(f"{k}={s.get(k)}" for k in
                                         ("mode", "state", "copied", "verified", "failed", "skipped", "dropped", "kept",
                                          "capped", "bytes", "error")))
    return s


def start(store, delay: float | None = None) -> threading.Thread | None:
    """After boot: a daemon thread that waits ``HQ_R2_MIGRATE_DELAY_S`` (default 30 s, so the health check passes
    first) and then runs. Nothing when HQ_R2_MIGRATE isn't copy or cleanup."""
    if not mode():
        return None
    if delay is None:
        try:
            delay = max(0.0, float(os.environ.get("HQ_R2_MIGRATE_DELAY_S", "30")))
        except ValueError:
            delay = 30.0
    with _LOCK:
        _STATE.clear()
        _STATE.update({**_fresh(mode()), "state": "waiting"})

    def go() -> None:
        time.sleep(delay)
        try:
            run(store)
        except Exception as e:  # noqa: BLE001  (never take the web service down)
            _set(state="failed", error=e.__class__.__name__)
    t = threading.Thread(target=go, name="hq-r2-migrate", daemon=True)
    t.start()
    return t
