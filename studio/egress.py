"""Egress meter and daily budget for media (Egress guard, 5 Nov 2026; docs/STORAGE.md#egress).

Two numbers per UTC day, counted in this process:

* ``db``: media bytes read from the storage backend (the Postgres BYTEA columns today). Supabase's free plan counts every
  byte the database sends as egress (5 GB a month, then requests answer 402 after a grace period).
* ``served``: media bytes this server sent to clients (/m/, /ms/, Library files). Render bills outbound bandwidth over
  the workspace allowance ($0.15/GB).

Bytes that came from R2 are not database egress, so they never count toward ``db`` (6 Nov 2026: once every file moved
to R2, cache fills from the bucket were still filling the 130 MB ``db`` budget and could 429 other people's browsers).
They are kept apart, numbers only and with no budget (R2 has no egress fees; its operations are capped in
studio/r2usage.py): ``r2`` counts bytes this server read from the bucket (a disk-cache fill, a range read), measured
where the read actually happens (``blobstore.read`` reports into ``r2_tally()``), so a row that falls back to its
database copy still counts as ``db``; ``r2_redirected`` counts the size of files a browser was sent straight to the
bucket for (HQ_MEDIA_REDIRECT=1: those bytes never pass through this server or the database).

Each has a daily budget (``HQ_EGRESS_DB_DAILY_MB``, default 130; ``HQ_EGRESS_SERVE_DAILY_MB``, default 150: a 30-day
month stays under ~4 GB and ~4.5 GB). At 80% a warning is logged once that day; past 100% previews degrade: a browser
fetch of someone else's media (or an anonymous one) answers 429 with Retry-After until the UTC day turns. The owner's
own requests, server-to-server fetchers (Buffer, Instagram, the render worker: no Sec-Fetch headers), HEAD and 304 are
never refused, so publishing and the owner's screens keep working.

Repeat downloads: one browser client (by IP; server-to-server fetchers are exempt) that has already been sent more than 3x a file's size of that same file in
``HQ_EGRESS_REPEAT_S`` seconds (default 600) gets 429 for it until the window passes (a player seeking is fine; a page
re-downloading the same video in a loop is not). The owner is never limited.

The day's totals and the last 14 daily summaries are kept in a small JSON file on the instance's disk
(``HQ_EGRESS_STATE``), so a process restart keeps the day's count; a deploy starts a new disk (documented). Numbers
only: no token, IP or user id is ever written there or to /healthz.
"""
from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

MB = 1024 * 1024
_LOCK = threading.Lock()
_S: dict = {}
_REPEAT: dict[tuple[str, str], list] = {}
_SAVE_EVERY = 30.0
_R2_TALLY: contextvars.ContextVar = contextvars.ContextVar("egress_r2_tally", default=None)


def _env_mb(name: str, default: float) -> int:
    try:
        return max(0, int(float(os.environ.get(name, str(default))) * MB))
    except ValueError:
        return int(default * MB)


def budgets() -> dict:
    return {"db": _env_mb("HQ_EGRESS_DB_DAILY_MB", 130), "served": _env_mb("HQ_EGRESS_SERVE_DAILY_MB", 150)}


def _state_path() -> str:
    return os.environ.get("HQ_EGRESS_STATE") or os.path.join(tempfile.gettempdir(), "hq-egress.json")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fresh(day: str) -> dict:
    return {"day": day, "db": 0, "served": 0, "db_media": 0, "db_library": 0, "refused_budget": 0, "refused_repeat": 0,
            "db_migration": 0, "r2": 0, "r2_redirected": 0, "warned": []}


def _load() -> None:
    """First use in this process: pick up today's totals and the history from the state file (if it's ours and sane)."""
    global _S
    if _S:
        return
    _S = {"cur": _fresh(_today()), "history": [], "saved": 0.0}
    try:
        with open(_state_path(), encoding="utf-8") as fh:
            d = json.load(fh)
        cur, hist = d.get("cur") or {}, d.get("history") or []
        if isinstance(cur, dict) and cur.get("day") == _today():
            for k, v in _fresh(_today()).items():
                if isinstance(v, int) and isinstance(cur.get(k), int):
                    _S["cur"][k] = cur[k]
            _S["cur"]["warned"] = [w for w in cur.get("warned") or [] if w in ("db", "served")]
        elif isinstance(cur, dict) and cur.get("day"):
            hist = [_summary(cur)] + hist
        _S["history"] = [h for h in hist if isinstance(h, dict)][:14]
    except (OSError, ValueError, TypeError):
        pass


def _summary(cur: dict) -> dict:
    b = budgets()
    return {"day": str(cur.get("day", "")), "db_bytes": int(cur.get("db", 0)), "served_bytes": int(cur.get("served", 0)),
            "db_budget": b["db"], "served_budget": b["served"], "refused_budget": int(cur.get("refused_budget", 0)),
            "refused_repeat": int(cur.get("refused_repeat", 0)), "r2_bytes": int(cur.get("r2", 0)),
            "r2_redirected_bytes": int(cur.get("r2_redirected", 0))}


def _roll() -> None:
    """A new UTC day: yesterday's totals become a summary line (logged: the daily alert) and the counters restart."""
    day = _today()
    if _S["cur"]["day"] != day:
        s = _summary(_S["cur"])
        _S["history"] = ([s] + _S["history"])[:14]
        print(f"egress daily {s['day']}: db {s['db_bytes'] / MB:.1f} MB of {s['db_budget'] / MB:.0f} MB, "
              f"served {s['served_bytes'] / MB:.1f} MB of {s['served_budget'] / MB:.0f} MB, "
              f"refused {s['refused_budget']} (budget) {s['refused_repeat']} (repeat)", flush=True)
        _S["cur"] = _fresh(day)
        _save(force=True)


def _save(force: bool = False) -> None:
    now = time.time()
    if not force and now - _S.get("saved", 0) < _SAVE_EVERY:
        return
    _S["saved"] = now
    p = _state_path()
    try:
        d = os.path.dirname(p) or "."
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".egress-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"cur": _S["cur"], "history": _S["history"]}, fh)
        os.replace(tmp, p)
    except OSError:
        pass


def _warn(kind: str) -> None:
    b = budgets()[kind]
    cur = _S["cur"]
    if b and cur[kind] >= 0.8 * b and kind not in cur["warned"]:
        cur["warned"].append(kind)
        print(f"egress warning: {kind} media bytes today {cur[kind] / MB:.1f} MB = "
              f"{100 * cur[kind] / b:.0f}% of the daily budget ({b / MB:.0f} MB)", flush=True)
        _save(force=True)


def db_read(n: int, kind: str = "media") -> None:
    if n <= 0:
        return
    with _LOCK:
        _load()
        _roll()
        _S["cur"]["db"] += int(n)
        k = "db_library" if kind == "library" else "db_media"
        _S["cur"][k] += int(n)
        _warn("db")
        _save()


@contextlib.contextmanager
def r2_tally():
    """Measure how many of the bytes read inside this block came from R2: ``blobstore.read`` adds every successful
    bucket read to the innermost open tally (per thread / task: a context variable). Yields a one-item list."""
    t = [0]
    tok = _R2_TALLY.set(t)
    try:
        yield t
    finally:
        _R2_TALLY.reset(tok)


def note_r2_read(n: int) -> None:
    """Called by ``blobstore.read`` after a bucket read. Inside an open ``r2_tally`` (mediacache.wrap) the bytes are
    credited to the tally and booked once by ``backend_read``; any other R2 read (a direct ``blobstore.read``) is
    added to ``r2`` here, so every bucket read is counted exactly once and never as ``db`` (CodeRabbit, PR #133)."""
    if n <= 0:
        return
    t = _R2_TALLY.get()
    if t is not None:
        t[0] += int(n)
        return
    with _LOCK:
        _load()
        _roll()
        _S["cur"]["r2"] = int(_S["cur"].get("r2", 0)) + int(n)
        _save()


def backend_read(n: int, kind: str = "media", r2_n: int = 0) -> None:
    """``n`` bytes read from the storage backend, ``r2_n`` of them from R2: only the rest is database egress (``db``)."""
    if n <= 0:
        return
    r2_n = max(0, min(int(r2_n or 0), int(n)))
    if r2_n:
        with _LOCK:
            _load()
            _roll()
            _S["cur"]["r2"] = int(_S["cur"].get("r2", 0)) + r2_n
            _save()
    db_read(int(n) - r2_n, kind)


def r2_redirected(n: int) -> None:
    """A browser was sent to a presigned R2 URL for a file of ``n`` bytes: no database or server bytes (numbers only)."""
    if n <= 0:
        return
    with _LOCK:
        _load()
        _roll()
        _S["cur"]["r2_redirected"] = int(_S["cur"].get("r2_redirected", 0)) + int(n)
        _save()


def migration_read(n: int) -> None:
    """Bytes the one-off R2 migration read from the database (studio/r2_migrate_bg.py): shown on their own and never
    added to ``db``, so moving every file once can't trip the daily budget that pauses other people's previews."""
    if n <= 0:
        return
    with _LOCK:
        _load()
        _roll()
        _S["cur"]["db_migration"] = int(_S["cur"].get("db_migration", 0)) + int(n)
        _save()


def served(n: int) -> None:
    if n <= 0:
        return
    with _LOCK:
        _load()
        _roll()
        _S["cur"]["served"] += int(n)
        _warn("served")
        _save()


def over_budget() -> bool:
    with _LOCK:
        _load()
        _roll()
        b, cur = budgets(), _S["cur"]
        return bool((b["db"] and cur["db"] >= b["db"]) or (b["served"] and cur["served"] >= b["served"]))


def retry_after() -> int:
    """Seconds until the UTC day turns (at least 60)."""
    now = datetime.now(timezone.utc)
    return max(60, 86400 - (now.hour * 3600 + now.minute * 60 + now.second))


def note_refused(why: str) -> None:
    with _LOCK:
        _load()
        _roll()
        k = "refused_repeat" if why == "repeat" else "refused_budget"
        _S["cur"][k] += 1


def _repeat_window() -> float:
    try:
        return max(1.0, float(os.environ.get("HQ_EGRESS_REPEAT_S", "600")))
    except ValueError:
        return 600.0


def repeat_blocked(client: str, etag: str, size: int) -> bool:
    """This client already had more than 3x this file's size of it within the window."""
    if not client or not etag or size <= 0:
        return False
    key = (hashlib.sha256(("ip:" + client).encode()).hexdigest()[:20], etag)
    now = time.time()
    with _LOCK:
        e = _REPEAT.get(key)
        if not e or now - e[0] > _repeat_window():
            return False
        return e[1] > 3 * size + MB


def repeat_count(client: str, etag: str, n: int) -> None:
    if not client or not etag or n <= 0:
        return
    key = (hashlib.sha256(("ip:" + client).encode()).hexdigest()[:20], etag)
    now = time.time()
    with _LOCK:
        e = _REPEAT.get(key)
        if not e or now - e[0] > _repeat_window():
            _REPEAT[key] = [now, int(n)]
        else:
            e[1] += int(n)
        if len(_REPEAT) > 20000:  # bounded: old windows go first
            for k in [k for k, v in _REPEAT.items() if now - v[0] > _repeat_window()][:10000] or list(_REPEAT)[:5000]:
                _REPEAT.pop(k, None)


def snapshot() -> dict:
    """Numbers only (for /healthz and the admin panel)."""
    with _LOCK:
        _load()
        _roll()
        b, cur = budgets(), _S["cur"]
        return {"day": cur["day"], "db_bytes": cur["db"], "served_bytes": cur["served"], "db_budget": b["db"],
                "served_budget": b["served"], "db_pct": round(100 * cur["db"] / b["db"], 1) if b["db"] else 0.0,
                "served_pct": round(100 * cur["served"] / b["served"], 1) if b["served"] else 0.0,
                "degraded": bool((b["db"] and cur["db"] >= b["db"]) or (b["served"] and cur["served"] >= b["served"])),
                "refused_budget": cur["refused_budget"], "refused_repeat": cur["refused_repeat"],
                "db_migration_bytes": int(cur.get("db_migration", 0)), "r2_bytes": int(cur.get("r2", 0)),
                "r2_redirected_bytes": int(cur.get("r2_redirected", 0))}


def history() -> list[dict]:
    with _LOCK:
        _load()
        _roll()
        return [dict(h) for h in _S["history"]]


def reset() -> None:
    """Tests: forget every count (and the state file)."""
    global _S
    with _LOCK:
        _S = {}
        _REPEAT.clear()
        try:
            os.unlink(_state_path())
        except OSError:
            pass
