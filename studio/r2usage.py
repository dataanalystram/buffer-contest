"""R2 free-tier caps, enforced in code (Ram, 5 Nov 2026: R2 must never cost money; docs/STORAGE.md#r2-free-tier-caps).

Cloudflare R2's free tier each month: 10 GB-month stored, 1,000,000 Class A operations (writes: PUT, multipart, list,
bucket config) and 10,000,000 Class B operations (reads: GET, HEAD). Deletes are free. HuhQueue stays well inside it:

* ``HQ_R2_MAX_BYTES`` (default 8 GB): bytes stored in R2, counted from the rows that point at an object (``media.size``,
  ``library_chunks.nbytes`` where ``storage_key`` is set, and One take uploads' ``take_uploads.size``). A new write that
  would pass it is refused: the bytes stay in the database (``blobstore.put``), the copy stops (``migrate_media``) or a
  large upload is declined (``take_upload``), each with a clear message, never a paid overage.
* ``HQ_R2_MAX_CLASS_A`` (default 800,000) and ``HQ_R2_MAX_CLASS_B`` (default 8,000,000) per calendar month (UTC),
  counted in the small ``r2_usage`` table (studio/store.py SCHEMA, row-level security on like every table; one row per
  month and class, months older than three pruned). Every SigV4 signature counts as one operation of its class
  (studio/r2.py presign): this server's own calls and each presigned URL it hands out. Past the Class A cap nothing new
  is written to R2 (database fallback); past the Class B cap reads use the row's database copy when it has one, and
  otherwise answer a clear 503 until the month turns. The file's owner (signed in, browser) may still read it past
  that cap, up to ``HQ_R2_HARD_CLASS_B`` (default 9,500,000, never above 9,900,000): a hard ceiling under the free
  tier's 10,000,000, so nobody loses access to their own file and the bill still stays at zero.
* A warning is logged once per month and cap when usage reaches 70% (``WARN_AT``).

Counts are kept in memory and written in batches (every 30 s or 200 operations, never inside someone's transaction) so
a busy page isn't a database write per object call; the caps sit 20% under the free tier, which covers what a crash
could lose. Numbers only: no key, token or user id is stored. Worker-safe: no FastAPI import.
"""
from __future__ import annotations

import contextlib
import contextvars
import os
import threading
import time
from datetime import datetime, timezone

GB = 1024 ** 3
CLASS_A = ("PUT", "POST")        # PutObject, UploadPart, Create/CompleteMultipartUpload, bucket config
CLASS_B = ("GET", "HEAD")
FLUSH_S, FLUSH_N, READ_S = 30.0, 200, 60.0
KEEP_MONTHS = 3
WARN_AT = 0.70
HARD_B_MAX = 9_900_000           # the owner's grace never reaches the free tier's 10,000,000 Class B
_GRACE = contextvars.ContextVar("r2_owner_grace", default=False)
_WARNED: set = set()             # (month, what) already warned about
REDIRECT_FRAC = 0.5              # browser redirects stop at this share of the Class B cap
RESERVE_S = 2 * 60.0             # a reservation outlives two stored-bytes reads, so its row is counted by then
_RESERVED: list = []             # [at, nbytes] written or about to be written in this process (one uvicorn process)

_LOCK = threading.Lock()
_RES_LOCK = threading.Lock()
_PENDING: dict[str, int] = {"A": 0, "B": 0}
_LAST_FLUSH = [time.time()]
_STORE = [None]
_CACHE: dict = {}                # what -> (at, value, store), refreshed every READ_S


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(float(os.environ.get(name, str(default)))))
    except ValueError:
        return default


def caps() -> dict:
    """The three caps (env overrides; defaults 20% under the free tier)."""
    return {"bytes": _env_int("HQ_R2_MAX_BYTES", 8 * GB), "A": _env_int("HQ_R2_MAX_CLASS_A", 800_000),
            "B": _env_int("HQ_R2_MAX_CLASS_B", 8_000_000)}


def hard_class_b() -> int:
    """The owner's ceiling past the Class B cap: HQ_R2_HARD_CLASS_B (default 9.5M), clamped to 9.9M and never under the
    cap itself."""
    return max(caps()["B"], min(_env_int("HQ_R2_HARD_CLASS_B", 9_500_000), HARD_B_MAX))


@contextlib.contextmanager
def owner_grace():
    """Inside this block a read past the Class B cap is allowed up to hard_class_b() (the file's owner reading it)."""
    tok = _GRACE.set(True)
    try:
        yield
    finally:
        _GRACE.reset(tok)


def _warn(what: str, used: float, cap: float) -> None:
    """Log once per month and cap when usage reaches WARN_AT of it (numbers only)."""
    if cap <= 0 or used < WARN_AT * cap:
        return
    k = (month(), what)
    if k in _WARNED:
        return
    _WARNED.add(k)
    print(f"r2usage: WARNING {what} at {used / cap:.0%} of its free-tier cap ({int(used):,} of {int(cap):,})", flush=True)


def month() -> str:
    """The billing month (UTC), e.g. 2026-11."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


def op_class(method: str) -> str | None:
    """A (writes), B (reads) or None (DELETE: free)."""
    m = (method or "").upper()
    return "A" if m in CLASS_A else "B" if m in CLASS_B else None


def bind(store) -> None:
    """The store whose ``r2_usage`` table keeps the counts (the app binds its own at start-up; scripts such as
    migrate_media bind theirs)."""
    _STORE[0] = store


def note(method: str, n: int = 1) -> None:
    """Count bucket operations (studio/r2.py presign calls this for every signature)."""
    c = op_class(method)
    if not c or n <= 0:
        return
    with _LOCK:
        _PENDING[c] += int(n)
        due = sum(_PENDING.values()) >= FLUSH_N or time.time() - _LAST_FLUSH[0] >= FLUSH_S
    if due:
        flush()


def flush(store=None) -> None:
    """Write pending counts into this month's rows and prune old months. Never raises (a counter must not break a read)."""
    st = store or _STORE[0]
    if st is None or getattr(st, "_tx_depth", 0):
        return  # never inside a transaction (a failed write there would abort it): a later call saves them
    with _LOCK:
        todo = {k: v for k, v in _PENDING.items() if v}
        for k in todo:
            _PENDING[k] = 0
        _LAST_FLUSH[0] = time.time()
    if not todo:
        return
    m = month()
    try:
        for c, n in todo.items():
            st.x("INSERT INTO r2_usage(month, cls, n) VALUES(?,?,?) ON CONFLICT(month, cls) DO UPDATE SET n = r2_usage.n + excluded.n",
                 (m, c, int(n)))
        keep = sorted({m, *(r["month"] for r in st.q("SELECT DISTINCT month FROM r2_usage"))}, reverse=True)[:KEEP_MONTHS]
        st.x(f"DELETE FROM r2_usage WHERE month NOT IN ({','.join('?' * len(keep))})", tuple(keep))
        _CACHE.pop("ops", None)
    except Exception as e:  # noqa: BLE001  (the database is down: keep the counts for the next flush)
        with _LOCK:
            for c, n in todo.items():
                _PENDING[c] += n
        print(f"r2usage: couldn't save operation counts ({e.__class__.__name__})", flush=True)


def _cached(st, what: str, fn):
    hit = _CACHE.get(what)
    if hit and hit[2] is st and time.time() - hit[0] < READ_S:
        return hit[1]
    v = fn()
    _CACHE[what] = (time.time(), v, st)
    return v


def ops(store=None) -> dict:
    """This month's Class A / B counts (saved + pending)."""
    st = store or _STORE[0]
    saved = {"A": 0, "B": 0}
    if st is not None:
        try:
            saved.update(_cached(st, "ops", lambda: {r["cls"]: int(r["n"] or 0) for r in
                                                     st.q("SELECT cls, n FROM r2_usage WHERE month=?", (month(),))}))
        except Exception:  # noqa: BLE001
            pass
    with _LOCK:
        return {c: saved.get(c, 0) + _PENDING[c] for c in ("A", "B")}


def stored_bytes(store=None) -> int:
    """Bytes the database says are in R2 (metadata only: no blob column is read). Unknown counts as full."""
    st = store or _STORE[0]
    if st is None:
        return 0

    def load():
        have = st._table_names()  # only tables this store has: on Postgres a failed statement aborts a transaction
        total = int(st.q("SELECT COALESCE(SUM(size),0) AS b FROM media WHERE storage_key IS NOT NULL")[0]["b"] or 0)
        if "library_chunks" in have:
            total += int(st.q("SELECT COALESCE(SUM(nbytes),0) AS b FROM library_chunks WHERE storage_key IS NOT NULL")[0]["b"] or 0)
        if "take_uploads" in have:
            total += int(st.q("SELECT COALESCE(SUM(size),0) AS b FROM take_uploads WHERE object_key IS NOT NULL "
                              "AND status IN ('open','joined','ready')")[0]["b"] or 0)
        return total
    try:
        return _cached(st, "bytes", load)
    except Exception:  # noqa: BLE001  (can't tell: count as full, so a write falls back, never past the cap)
        return caps()["bytes"] + 1


def forget_cache() -> None:
    """Re-read the counts and stored bytes on the next call."""
    _CACHE.clear()


def _reserved() -> int:
    now = time.time()
    _RESERVED[:] = [r for r in _RESERVED if now - r[0] < RESERVE_S]
    return sum(r[1] for r in _RESERVED)


def reserve(nbytes: int, store=None) -> str | None:
    """Check and reserve in one step (under the lock): None and ``nbytes`` counted against the storage cap for
    RESERVE_S (by then its row is in the total; double counting meanwhile only errs safe), or the reason it's refused.
    Two writes inside one stored-bytes cache window can't both pass on the same old total (CodeRabbit, PR #120). The
    studio runs one uvicorn process (Dockerfile.studio), so the reservation is in memory."""
    with _RES_LOCK:
        why = can_write(nbytes, store)
        if not why and nbytes > 0:
            _RESERVED.append([time.time(), int(nbytes)])
        return why


def release(nbytes: int) -> None:
    """A reserved write that didn't happen."""
    with _RES_LOCK:
        for i, r in enumerate(_RESERVED):
            if r[1] == int(nbytes):
                del _RESERVED[i]
                return


def redirect_ok(store=None) -> bool:
    """Browser redirects (presigned GETs) only below REDIRECT_FRAC of the Class B cap."""
    return ops(store)["B"] < REDIRECT_FRAC * caps()["B"]


def can_write(nbytes: int = 0, store=None) -> str | None:
    """None when a new write of ``nbytes`` stays inside the caps; otherwise the reason (shown to the operator)."""
    cap = caps()
    o, have = ops(store), stored_bytes(store) + _reserved()
    _warn("Class A operations", o["A"], cap["A"])
    _warn("stored bytes", have, cap["bytes"])
    if o["A"] >= cap["A"]:
        return f"R2 monthly write cap reached ({cap['A']:,} Class A operations, HQ_R2_MAX_CLASS_A)"
    if have + max(0, int(nbytes)) > cap["bytes"]:
        return f"R2 storage cap reached ({cap['bytes'] / GB:.1f} GB, HQ_R2_MAX_BYTES)"
    return None


def can_read(store=None, grace: bool = True) -> str | None:
    """None while this month's Class B count is under its cap (or, inside owner_grace() and with ``grace``, under the
    hard ceiling); otherwise the reason."""
    cap, n = caps(), ops(store)["B"]
    _warn("Class B operations", n, cap["B"])
    if n >= cap["B"] and not (grace and _GRACE.get() and n < hard_class_b()):
        return f"R2 monthly read cap reached ({cap['B']:,} Class B operations, HQ_R2_MAX_CLASS_B)"
    return None


def snapshot(store=None) -> dict:
    """For /admin: numbers only."""
    cap, o = caps(), ops(store)
    return {"month": month(), "stored_bytes": stored_bytes(store), "max_bytes": cap["bytes"], "class_a": o["A"],
            "max_class_a": cap["A"], "class_b": o["B"], "max_class_b": cap["B"],
            "owner_ceiling_class_b": hard_class_b(),
            "write_blocked": bool(can_write(0, store)), "read_blocked": bool(can_read(store, grace=False))}


def reset() -> None:
    """Tests: drop pending counts, the bound store and the cache."""
    with _LOCK:
        _PENDING.update({"A": 0, "B": 0})
    _STORE[0] = None
    _CACHE.clear()
    _WARNED.clear()
    _RESERVED.clear()
