"""Daily caps for the free sample workspace, so a visitor (or a script) can try every feature without burning the app's
AI credits, research requests or render minutes.

Every cap counts twice: per sample workspace (a browser session) and per visitor IP (hashed, never stored raw), and a
call goes ahead only while both are under their cap. Past a cap, the feature still works on sample data: the agent
researches the bundled sample story, the writer is the built-in rule writer, the brand import fills the sample kit,
a Reel falls back to the bundled sample clip. Real (connected) accounts are never capped here.

Counters live in the shared store (SQLite locally, Postgres in production), keyed by UTC day, so they survive restarts
and every web worker sees the same numbers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .store import Store, sha

# kind: (per sample workspace, per visitor IP) per UTC day
CAPS = {
    "research": (4, 10),   # agent runs that search the web and open source pages; Write from a link
    "ai": (12, 30),        # calls to the app's free starter model (a visitor's own key is never capped)
    "import": (3, 8),      # brand kit "import from website" fetches
    "feed": (3, 8),        # following a new feed in News radar
    "video": (1, 3),       # Reel renders (the free render worker's minutes)
    "workspace": (None, 20),  # new sample workspaces from one IP
    "run": (20, 50),       # agent runs, revisions and sample takes (render work on this server)
    "upload_kb": (25 * 1024, 60 * 1024),  # uploaded bytes (KB) a day; each sample file is also capped at 8 MB
}
DEMO_FILE_MAX = 8 * 1024 * 1024
WHY = {
    "research": "Live research is limited in the free sample, so the agent used the sample story instead.",
    "ai": "The free sample's AI allowance for today is used up, so the built-in writer wrote this.",
    "import": "Website import is limited in the free sample, so it filled in the sample brand instead.",
    "feed": "The free sample can follow a few new feeds a day. Connect Buffer to follow as many as you like.",
    "video": "Reel renders are limited in the free sample, so this shows the sample Reel.",
    "workspace": "Too many sample workspaces from this network today. Try again tomorrow, or connect Buffer.",
    "run": "The free sample has made its agent runs for today. Connect Buffer to keep going.",
    "upload_kb": "The free sample's uploads for today are used up (or the file is over 8 MB). Connect Buffer to upload more.",
}
PREFIX = "demo_"
# View-only demo (studio/demo_view.py): these kinds spend real money or call out, so a demo user never gets them and
# every run takes its bundled path. "run" and "upload_kb" are local work; uploads are refused at the route anyway.
SPEND_KINDS = frozenset({"research", "ai", "import", "feed", "video"})
VIEW_WHY = {
    "research": "The demo uses the bundled sample story: live research is off in the demo (it spends nothing).",
    "ai": "The demo uses the built-in writer: AI engines are off in the demo (it spends nothing).",
    "import": "The demo fills in the sample brand: importing from a website is off in the demo.",
    "feed": "In the real app this would follow this feed. The demo doesn't change anything.",
    "video": "The demo shows the bundled sample Reel: video renders are off in the demo (it spends nothing).",
}


def _view_only_closed(uid: str | None, kind: str) -> bool:
    from .demo_view import view_only
    return view_only() and is_demo(uid) and kind in SPEND_KINDS

SCHEMA = """
CREATE TABLE IF NOT EXISTS demo_usage (day TEXT, bucket TEXT, kind TEXT, n INTEGER, PRIMARY KEY (day, bucket, kind));
CREATE TABLE IF NOT EXISTS demo_owners (user_id TEXT PRIMARY KEY, ip_hash TEXT, created REAL);
"""


def why(kind: str) -> str:
    from .demo_view import view_only
    return VIEW_WHY[kind] if view_only() and kind in VIEW_WHY else WHY[kind]


def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def client_ip(request) -> str:
    """The visitor's address. Render sits behind Cloudflare, which sets CF-Connecting-IP (and True-Client-IP) to the real
    client and overwrites any value the client sent. X-Forwarded-For is only appended to, so its first hop is whatever
    the client typed; it is used only when no Cloudflare header is there (local runs, tests), then the socket peer."""
    for h in ("cf-connecting-ip", "true-client-ip"):
        v = (request.headers.get(h) or "").strip()
        if v:
            return v
    fwd = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "") or "unknown"


def ensure(store: Store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        store.x(stmt.replace(" REAL", " DOUBLE PRECISION") if store.pg else stmt)
    if store.pg:  # same as every other table: closed to the hosted database's public data API
        for t in ("demo_usage", "demo_owners"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def is_demo(uid: str | None) -> bool:
    return bool(uid) and uid.startswith(PREFIX)


def register(store: Store, uid: str, ip: str) -> None:
    store.x("INSERT INTO demo_owners(user_id,ip_hash,created) VALUES(?,?,?) ON CONFLICT(user_id) DO NOTHING",
            (uid, sha("ip:" + ip), time.time()))


def _ip_bucket(store: Store, uid: str) -> str | None:
    rows = store.q("SELECT ip_hash FROM demo_owners WHERE user_id=?", (uid,))
    return ("ip:" + rows[0]["ip_hash"]) if rows else None


def _count(store: Store, bucket: str, kind: str) -> int:
    rows = store.q("SELECT n FROM demo_usage WHERE day=? AND bucket=? AND kind=?", (_day(), bucket, kind))
    return int(rows[0]["n"]) if rows else 0


def _bump(store: Store, bucket: str, kind: str) -> None:
    store.x("INSERT INTO demo_usage(day,bucket,kind,n) VALUES(?,?,?,1) "
            "ON CONFLICT(day,bucket,kind) DO UPDATE SET n=demo_usage.n+1", (_day(), bucket, kind))


def _buckets(store: Store, uid: str, kind: str) -> list[tuple[str, int]]:
    per_ws, per_ip = CAPS[kind]
    out = []
    if per_ws is not None:
        out.append(("ws:" + uid, per_ws))
    ipb = _ip_bucket(store, uid)
    if per_ip is not None and ipb:
        out.append((ipb, per_ip))
    return out


def allowed(store: Store, uid: str | None, kind: str) -> bool:
    """True when this call may spend: always for real accounts; for a sample workspace, while every cap has room."""
    if not is_demo(uid):
        return True
    if _view_only_closed(uid, kind):
        return False
    return all(_count(store, b, kind) < cap for b, cap in _buckets(store, uid, kind))


def reserve(store: Store, uid: str | None, kind: str, n: int = 1) -> bool:
    """Atomically spend ``n`` units against every cap, or none: each bucket is raised by one conditional UPDATE that only
    succeeds while it has room, so parallel calls can't overshoot (the check and the spend are the same statement).
    Spend this BEFORE the costly call. Real accounts always pass."""
    if not is_demo(uid):
        return True
    if _view_only_closed(uid, kind):
        return False
    done = []
    for b, cap in _buckets(store, uid, kind):
        store.x("INSERT INTO demo_usage(day,bucket,kind,n) VALUES(?,?,?,0) ON CONFLICT(day,bucket,kind) DO NOTHING", (_day(), b, kind))
        ok = store.q("UPDATE demo_usage SET n=n+? WHERE day=? AND bucket=? AND kind=? AND n+?<=? RETURNING n",
                     (n, _day(), b, kind, n, cap))
        if not ok:
            for b2 in done:  # give back what this call took from the other buckets
                store.x("UPDATE demo_usage SET n=n-? WHERE day=? AND bucket=? AND kind=?", (n, _day(), b2, kind))
            store.audit(uid, "you", "demo_cap", "read", {"kind": kind})
            return False
        done.append(b)
    return True


def take(store: Store, uid: str | None, kind: str) -> bool:
    """Spend one unit if allowed (and count it against every bucket). Returns whether the call may go ahead."""
    return reserve(store, uid, kind, 1)


def spend(store: Store, uid: str | None, kind: str) -> None:
    """Count a unit that already happened (an AI call made after allowed() said yes)."""
    if is_demo(uid):
        for b, _ in _buckets(store, uid, kind):
            _bump(store, b, kind)


def workspace_ok(store: Store, ip: str) -> bool:
    """May this IP open one more sample workspace today? Counts it when yes."""
    b, cap = "ip:" + sha("ip:" + ip), CAPS["workspace"][1]
    if _count(store, b, "workspace") >= cap:
        return False
    _bump(store, b, "workspace")
    return True


def left(store: Store, uid: str) -> dict:
    """What a sample workspace has left today, per kind (the smaller of its own and its IP's allowance)."""
    out = {}
    for kind in CAPS:
        if kind == "workspace":
            continue
        rooms = [cap - _count(store, b, kind) for b, cap in _buckets(store, uid, kind)]
        out[kind] = 0 if _view_only_closed(uid, kind) else (max(0, min(rooms)) if rooms else None)
    return out
