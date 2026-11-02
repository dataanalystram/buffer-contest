"""Per-workspace locks for read-modify-write state (brand kit, brand studio extras, the kit library, custom templates).

Bug sweep B2-B4 (2 Nov 2026): BrandStudio.save() read the extras row, changed it and wrote it back with no lock, so two
MCP sessions saving at once lost one write; BrandKits.activate() wrote the extras twice; the HuhByte seed was
check-then-insert (two first loads made two HuhByte kits) and the kit cap was checked outside the insert; the preview
rate-limit dicts were mutated from many threads. Every one of those now runs under the workspace's lock.

The app runs as one uvicorn process (Dockerfile.studio), so an in-process re-entrant lock per (workspace, name) is the
whole fix; the database constraint next to it (custom_templates UNIQUE(user_id, slug)) also covers a second process.
Locks are re-entrant, so activate() -> save() -> save_kit() nest under one lock without deadlocking.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager

_guard = threading.Lock()
_locks: dict[tuple[str, str], threading.RLock] = {}


def lock_for(uid: str, name: str = "brand") -> threading.RLock:
    key = (str(uid or ""), name)
    with _guard:
        lk = _locks.get(key)
        if lk is None:
            lk = _locks[key] = threading.RLock()
        return lk


@contextmanager
def held(uid: str, name: str = "brand"):
    with lock_for(uid, name):
        yield


# rate-limit windows (B4): {uid: [timestamps]} dicts shared by request threads
_rate_guard = threading.Lock()


def rate_hit(bucket: dict, uid: str, now: float, per_min: int) -> bool:
    """Record one hit in a per-workspace one-minute window. False (and nothing recorded) when the window is full."""
    with _rate_guard:
        hits = [t for t in bucket.get(uid, []) if now - t < 60]
        if len(hits) >= per_min:
            bucket[uid] = hits
            return False
        bucket[uid] = hits + [now]
        if len(bucket) > 5000:  # drop idle workspaces
            for k in [k for k, v in bucket.items() if not v or now - v[-1] > 60][:2500]:
                bucket.pop(k, None)
        return True
