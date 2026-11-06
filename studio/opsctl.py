"""Operator controls: global kill switches and per-token rate limits set from /admin (docs/ADMIN.md#kill-switches).

Kill switches (one row each in ``ops_switches``; no row = running). Each one stops new work of its kind for everyone,
the operator included, at the single place that work starts:

* ``publishing``: nothing is sent to Buffer (create and edit post). Deleting a post still works.
* ``ai``: no AI call on HuhQueue's own keys: Claude and OCI (studio/budget.py checks), the free engines and the free
  starter model, the Groq / NVIDIA / OpenRouter free ledger (studio/free_quota.py), the link drafter. The brain falls back
  to the rule writer; the video worker to local Whisper and the layout rules. A person's own key (BYO) keeps working.
* ``r2_uploads``: no new write to the R2 bucket (``r2usage.can_write`` answers the reason): new media stays in the
  database, One take large uploads answer 507. Reads and deletes go on.
* ``link_ingest``: no new video link is accepted (One take, ``ingest_video_link``, ``clips_from_video``, the agent's
  Opus path). Uploads still work; jobs already queued finish.

Fail closed: when the switch table can't be read (database error), every switch reads as paused until a read succeeds.
A process that has no store at all (the video worker, a script) has nothing to read, so it is never paused here; the
web app binds its store at start-up (``bind``). State is cached on the Store object for CACHE_S seconds; a change made
in /admin clears the cache of this process at once (the studio runs one uvicorn process).

Per-token rate limit: an admin can give one agent / MCP token an hourly request cap (``agent_tokens.admin_hourly_cap``,
NULL = none). Every authenticated request on that token counts (REST and /mcp), reads included; past the cap the request
answers 429 with Retry-After. In memory, per process, a sliding one-hour window. No FastAPI import: the worker reaches
modules that import this one.
"""
from __future__ import annotations

import threading
import time
from collections import deque

SWITCHES = {
    "publishing": "Publishing to Buffer",
    "ai": "AI calls on HuhQueue's keys",
    "r2_uploads": "New uploads to R2",
    "link_ingest": "Video link ingest",
}
MESSAGES = {
    "publishing": "Publishing is paused by the HuhQueue operator right now. Nothing was sent to Buffer. Try again later.",
    "ai": "AI calls on HuhQueue's keys are paused by the operator right now.",
    "r2_uploads": "New uploads to the media bucket are paused by the HuhQueue operator",
    "link_ingest": "Video links are paused by the HuhQueue operator right now. Upload the file instead, or try again later.",
}
CACHE_S = 5.0
HOURLY_MAX = 100_000
_STORE: list = [None]
_hits: dict[int, deque] = {}
_hlock = threading.Lock()


def bind(store) -> None:
    _STORE[0] = store


def _store(store=None):
    return store if store is not None else _STORE[0]


def state(store=None) -> dict:
    """{name: {"paused": bool, "at", "reason", "unreadable"}} for every switch. Fail closed on a read error."""
    st = _store(store)
    if st is None or not callable(getattr(st, "q", None)):  # no database here (a worker, a script, a test stub)
        return {n: {"paused": False, "at": None, "reason": "", "unreadable": False} for n in SWITCHES}
    now = time.time()
    cached = getattr(st, "_ops_switch_cache", None)
    if cached and now - cached[0] < CACHE_S:
        return cached[1]
    try:
        rows = {r["name"]: dict(r) for r in st.q("SELECT name, paused, at, reason FROM ops_switches")}
    except Exception:  # noqa: BLE001 - unreadable state: everything reads as paused (fail closed), not cached
        return {n: {"paused": True, "at": None, "reason": "switch state unreadable", "unreadable": True} for n in SWITCHES}
    out = {n: {"paused": bool((rows.get(n) or {}).get("paused")), "at": (rows.get(n) or {}).get("at"),
               "reason": (rows.get(n) or {}).get("reason") or "", "unreadable": False} for n in SWITCHES}
    st._ops_switch_cache = (now, out)
    return out


def paused(name: str, store=None) -> bool:
    if name not in SWITCHES:
        raise KeyError(name)
    return bool(state(store)[name]["paused"])


def message(name: str) -> str:
    return MESSAGES[name]


def set_switch(store, name: str, on: bool, by_hash: str, reason: str = "") -> None:
    """Write one switch (the caller logs it in admin_audit inside the same transaction)."""
    if name not in SWITCHES:
        raise KeyError(name)
    store.x("INSERT INTO ops_switches(name, paused, at, by_hash, reason) VALUES(?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
            "paused=excluded.paused, at=excluded.at, by_hash=excluded.by_hash, reason=excluded.reason",
            (name, 1 if on else 0, time.time(), by_hash, str(reason or "")[:200]))
    forget(store)


def forget(store=None) -> None:
    st = _store(store)
    if st is not None:
        st._ops_switch_cache = None


# ------------------------------------------------------------------ per-token hourly cap
def token_hit(agent: dict | None, now: float | None = None) -> int:
    """Count one request on this token. 0 when it may go on, else the seconds until the window has room (429)."""
    if not agent:
        return 0
    cap = agent.get("admin_hourly_cap")
    try:
        cap = int(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap = None
    if not cap or cap <= 0:
        return 0
    tid = int(agent["id"])
    t = time.time() if now is None else now
    with _hlock:
        q = _hits.setdefault(tid, deque())
        while q and t - q[0] >= 3600:
            q.popleft()
        if len(q) >= cap:
            return max(1, int(3600 - (t - q[0])) + 1)
        q.append(t)
        return 0


def token_reset(tid: int | None = None) -> None:
    with _hlock:
        if tid is None:
            _hits.clear()
        else:
            _hits.pop(int(tid), None)


def token_inherit(parent: int, child: int) -> None:
    """A refreshed MCP token takes over its parent's hourly window (a refresh doesn't buy a fresh hour)."""
    with _hlock:
        q = _hits.get(int(parent))
        if q:
            _hits[int(child)] = deque(q)


def token_window(tid: int, now: float | None = None) -> int:
    """Requests counted in the last hour for this token (this process)."""
    t = time.time() if now is None else now
    with _hlock:
        return sum(1 for x in _hits.get(int(tid), ()) if t - x < 3600)


def reset() -> None:
    """Tests."""
    forget()
    _STORE[0] = None
    token_reset()
