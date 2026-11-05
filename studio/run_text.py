"""A run's ``input`` and ``result`` text, read from the database only when it changed (Egress guard, 5 Nov 2026).

The Library list, the threads list, Make a post's Recent and the calendar read ``agent_runs.result`` (~15 KB a run, up
to 50 KB) for up to 200 runs on every request; ``pg_stat_statements`` showed ~0.5 GB of these in 6 days, a third of the
Supabase free plan's monthly 5 GB on its own. On Postgres those lists now ask for an md5 of each text column (32 bytes)
and take the text from a memo on the Store, fetching only runs whose text changed since the last read. The md5 is of
the content itself, so any writer (with or without ``updated``) is seen at once: no stale list. SQLite (local runs and
most tests) has no md5 and no egress, so it reads the columns as before.
"""
from __future__ import annotations

import threading
from collections import OrderedDict

MEMO_MAX = 3000
_LOCK = threading.Lock()


def cols(store) -> str:
    """The select-list part for a run's input and result: the text itself (SQLite) or its md5 (Postgres)."""
    if getattr(store, "pg", False):
        return "md5(COALESCE(input,'')) AS _hi, md5(COALESCE(result,'')) AS _hr"
    return "input, result"


def _memo(store) -> OrderedDict:
    m = getattr(store, "_run_text_memo", None)
    if m is None:
        m = OrderedDict()
        store._run_text_memo = m
    return m


def attach(store, rows) -> list[dict]:
    """Rows (dicts) with ``input`` and ``result`` filled in. Rows from a ``cols(store)`` query on Postgres carry the two
    md5s; text for a run whose md5s match the memo comes from it, the rest in one ``id IN (...)`` read per 200 runs."""
    out = [dict(r) for r in rows]
    if not getattr(store, "pg", False):
        return out
    memo = _memo(store)
    need = []
    with _LOCK:
        for d in out:
            hit = memo.get(d["id"])
            if hit and hit[0] == d.get("_hi") and hit[1] == d.get("_hr"):
                memo.move_to_end(d["id"])
                d["input"], d["result"] = hit[2], hit[3]
            else:
                need.append(d)
    for i in range(0, len(need), 200):
        part = need[i:i + 200]
        got = {r["id"]: r for r in store.q(f"SELECT id, input, result, md5(COALESCE(input,'')) AS _hi, md5(COALESCE(result,'')) AS _hr "
                                            f"FROM agent_runs WHERE id IN ({','.join('?' * len(part))})", tuple(d["id"] for d in part))}
        with _LOCK:
            for d in part:
                r = got.get(d["id"])
                d["input"], d["result"] = (r["input"], r["result"]) if r else (None, None)
                if r:
                    memo[d["id"]] = (r["_hi"], r["_hr"], r["input"], r["result"])
                    memo.move_to_end(d["id"])
            while len(memo) > MEMO_MAX:
                memo.popitem(last=False)
    for d in out:
        d.pop("_hi", None)
        d.pop("_hr", None)
    return out
