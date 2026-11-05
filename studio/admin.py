"""The operator's admin panel: /admin (page) and /api/admin (REST). docs/ADMIN.md.

Who: a signed-in browser session whose Buffer account id or Buffer email is listed in ``HQ_ADMIN_IDS`` (comma-separated).
Everyone else (signed out, signed in but not listed, any agent token) gets the same 404 as a path that doesn't exist, so
the panel can't be found by probing. There are no MCP tools for any of this, on purpose.

What: the users table (Buffer id, name and email, created, last sign-in time with a coarse IP and browser, last active,
runs and posts, storage used, status), and three actions, each CSRF-checked and written to ``admin_audit``:

* **Block** (reversible): every browser session is deleted at once, agent and MCP tokens stop resolving (they are not
  revoked, so Unblock restores them), new MCP grants and refreshes are refused, sign-in answers a neutral message, pending
  device links, send codes and upload sessions are deleted.
* **Unblock**.
* **Delete user** (type the id to confirm): the same erasure as the person's own "Disconnect and delete" (GDPR): every row
  in every table, media objects in R2 (released, then swept), sealed Buffer OAuth tokens; a tombstone keeps only the
  hashed id, the time and how. The admin audit row keeps the hashed id, time and admin.

An admin can't block or delete their own account.
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
import time
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .store import sha

UI = Path(__file__).parent / "admin_ui"
UI_FILES = {"admin.js": "text/javascript", "admin.css": "text/css"}
UID_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,100}")
SIGNIN_OK = ("connect_buffer", "device_signed_in")


def admin_ids() -> set[str]:
    return {x.strip().lower() for x in os.environ.get("HQ_ADMIN_IDS", "").split(",") if x.strip()}


def is_admin(user: dict | None) -> bool:
    ids = admin_ids()
    if not user or not ids:
        return False
    return str(user.get("id") or "").lower() in ids or (bool(user.get("email")) and str(user["email"]).strip().lower() in ids)


def subject_hash(uid: str) -> str:
    return sha("hq-subject:" + uid)


# ------------------------------------------------------------------ coarse sign-in details (never the full IP or UA)
def coarse_ip(ip: str | None) -> str:
    try:
        a = ipaddress.ip_address(str(ip or "").strip())
    except ValueError:
        return ""
    net = ipaddress.ip_network(f"{a}/{24 if a.version == 4 else 48}", strict=False)
    return str(net)


def coarse_ua(ua: str | None) -> str:
    u = str(ua or "")
    if not u:
        return ""
    browser = next((name for pat, name in ((r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"),
                                           (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari"), (r"curl/", "curl"),
                                           (r"python|httpx|requests", "script")) if re.search(pat, u, re.I)), "Other")
    os_ = next((name for pat, name in ((r"iPhone|iPad|iOS", "iOS"), (r"Android", "Android"), (r"Windows", "Windows"),
                                       (r"Mac OS X|Macintosh", "macOS"), (r"CrOS", "ChromeOS"), (r"Linux", "Linux"))
                if re.search(pat, u)), "")
    return f"{browser} on {os_}" if os_ else browser


# ------------------------------------------------------------------ data
def _by_user(store, sql: str, args: tuple = ()) -> dict:
    try:
        return {r["user_id"]: r["n"] for r in store.q(sql, args)}
    except Exception:  # noqa: BLE001  (a table this store hasn't created yet)
        return {}


def users(store, q: str = "", limit: int = 100, offset: int = 0, include_demo: bool = False) -> dict:
    from . import demo
    where, args = [], []
    if not include_demo:
        where.append("id NOT LIKE ?")
        args.append(demo.PREFIX + "%")
    q = (q or "").strip()[:100]
    if q:
        where.append("(LOWER(id) LIKE ? OR LOWER(COALESCE(email,'')) LIKE ? OR LOWER(COALESCE(name,'')) LIKE ?)")
        args += [f"%{q.lower()}%"] * 3
    w = (" WHERE " + " AND ".join(where)) if where else ""
    total = int(store.q("SELECT COUNT(*) AS n FROM users" + w, tuple(args))[0]["n"] or 0)
    rows = [dict(r) for r in store.q("SELECT id,email,name,org_name,kill_switch,created,updated,blocked,blocked_at,last_seen,"
                                     "CASE WHEN tokens IS NULL OR tokens='' THEN 0 ELSE 1 END AS has_tokens FROM users" + w +
                                     " ORDER BY created DESC LIMIT ? OFFSET ?", (*args, limit, offset))]
    runs = _by_user(store, "SELECT user_id, COUNT(*) AS n FROM agent_runs GROUP BY user_id")
    posts = _by_user(store, "SELECT user_id, COUNT(*) AS n FROM audit WHERE action='submit_post' AND ok=1 GROUP BY user_id")
    media = _by_user(store, "SELECT user_id, COALESCE(SUM(size),0) AS n FROM media GROUP BY user_id")
    lib = _by_user(store, "SELECT user_id, COALESCE(SUM(size),0) AS n FROM library_items WHERE storage IN ('chunks','pending') "
                          "OR (storage='trash' AND meta LIKE ?) GROUP BY user_id", ('%"storage": "chunks"%',))
    agent_seen = _by_user(store, "SELECT user_id, MAX(last_used) AS n FROM agent_tokens GROUP BY user_id")
    signin: dict = {}
    first: dict = {}
    if rows:
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        first = {r["user_id"]: r["n"] for r in store.q(
            f"SELECT user_id, MIN(ts) AS n FROM audit WHERE user_id IN ({marks}) AND action IN ('connect_buffer','device_signed_in') "
            f"AND ok=1 GROUP BY user_id", tuple(ids))}
        for r in store.q(f"SELECT user_id, ts, ip, ua FROM audit WHERE id IN (SELECT MAX(id) FROM audit WHERE user_id IN ({marks}) "
                         f"AND action IN ('connect_buffer','device_signed_in') AND ok=1 GROUP BY user_id)", tuple(ids)):
            signin[r["user_id"]] = dict(r)
    out = []
    for r in rows:
        s = signin.get(r["id"]) or {}
        status = "blocked" if r["blocked"] else ("disconnected" if not r["has_tokens"] else
                                                 ("agents paused" if r["kill_switch"] else "active"))
        if demo.is_demo(r["id"]):
            status = "demo"
        seen = max(float(r["last_seen"] or 0), float(agent_seen.get(r["id"]) or 0)) or None
        out.append({"id": r["id"], "name": r["name"] or "", "email": r["email"] or "", "org": r["org_name"] or "",
                    # first sign-in: the account row is created by the first Buffer OAuth sign-in (the activity log
                    # only keeps 90 days, so its oldest sign-in is used only when it is older, e.g. a copied database)
                    "created": r["created"], "first_signin": min(x for x in (r["created"], first.get(r["id"])) if x) if
                    (r["created"] or first.get(r["id"])) else None, "last_signin": s.get("ts"), "last_signin_ip": coarse_ip(s.get("ip")),
                    "last_signin_ua": coarse_ua(s.get("ua")), "last_seen": seen,
                    "runs": int(runs.get(r["id"]) or 0), "posts": int(posts.get(r["id"]) or 0),
                    "storage_bytes": int(media.get(r["id"]) or 0) + int(lib.get(r["id"]) or 0),
                    "status": status, "blocked_at": r["blocked_at"]})
    return {"users": out, "total": total, "limit": limit, "offset": offset}


def overview(store) -> dict:
    from . import blobstore
    db_bytes = None
    try:
        if store.pg:
            db_bytes = int(store.q("SELECT pg_database_size(current_database()) AS n")[0]["n"])
        else:
            pc = store.q("PRAGMA page_count")[0][0]
            ps = store.q("PRAGMA page_size")[0][0]
            db_bytes = int(pc) * int(ps)
    except Exception:  # noqa: BLE001
        pass
    use = blobstore.usage(store)
    return {"storage_backend": blobstore.backend_name(), "db_bytes": db_bytes, "db_media_bytes": use["db"],
            "r2_media_bytes": use["r2"], "r2_chunks": use.get("r2_chunks", 0), "objects_pending_delete": blobstore.pending(store),
            "users": int(store.q("SELECT COUNT(*) AS n FROM users")[0]["n"] or 0),
            "blocked": int(store.q("SELECT COUNT(*) AS n FROM users WHERE blocked=1")[0]["n"] or 0)}


def log(store, admin_uid: str, action: str, subject: str, detail: dict | None = None, ok: bool = True) -> None:
    store.x("INSERT INTO admin_audit(ts,admin,action,subject_hash,detail,ok) VALUES(?,?,?,?,?,?)",
            (time.time(), admin_uid, action, subject_hash(subject) if subject else "", json.dumps(detail or {})[:2000], 1 if ok else 0))


def audit_rows(store, limit: int = 100) -> list[dict]:
    out = []
    for r in store.q("SELECT * FROM admin_audit ORDER BY id DESC LIMIT ?", (limit,)):
        d = dict(r)
        d["detail"] = json.loads(d["detail"] or "{}")
        d["ok"] = bool(d["ok"])
        out.append(d)
    return out


# ------------------------------------------------------------------ actions
class AdminError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _target(store, uid) -> dict:
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
        raise AdminError("No such user.", 404)
    u = store.user(uid)
    if not u:
        raise AdminError("No such user.", 404)
    return u


def block(store, admin_uid: str, uid: str, reason: str = "") -> dict:
    u = _target(store, uid)
    if uid == admin_uid:
        raise AdminError("You can't block your own account.", 400)
    reason = str(reason or "")[:200]
    with store.tx():
        store.x("UPDATE users SET blocked=1, blocked_at=?, blocked_by=?, block_reason=? WHERE id=?",
                (time.time(), subject_hash(admin_uid), reason, uid))
        store.x("DELETE FROM sessions WHERE user_id=?", (uid,))       # signed out everywhere, now
        store.x("DELETE FROM device_links WHERE user_id=?", (uid,))
        store.x("DELETE FROM oauth_codes WHERE user_id=?", (uid,))    # unexchanged MCP grants
        log(store, admin_uid, "block", uid, {"reason": reason} if reason else {})
    for t in ("send_codes", "upload_sessions"):  # one-time upload credentials (outside the tx: the table may not exist yet)
        try:
            store.x(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        except Exception:  # noqa: BLE001
            pass
    store.audit(uid, "system", "account_blocked", "auth", {})
    return {"id": uid, "status": "blocked", "was": "blocked" if u.get("blocked") else "active"}


def unblock(store, admin_uid: str, uid: str) -> dict:
    _target(store, uid)
    store.x("UPDATE users SET blocked=0, blocked_at=NULL, blocked_by=NULL, block_reason=NULL WHERE id=?", (uid,))
    log(store, admin_uid, "unblock", uid)
    store.audit(uid, "system", "account_unblocked", "auth", {})
    return {"id": uid, "status": "active"}


def erase_user(store, uid: str, via: str, house=None, admin_uid: str | None = None) -> None:
    """GDPR erasure (self-service Disconnect and delete, or an admin's Delete user): every row, the media objects, One
    take's uploads in the bucket, the sealed Buffer tokens; a tombstone keeps only the hashed id, the time and how.
    The rows, the tombstone and (for an admin's delete) the admin log entry commit together or not at all; the bucket
    objects go after the commit (CodeRabbit, PR #110). An erased admin's own id in admin_audit becomes its hash."""
    from . import blobstore
    tu = getattr(house, "take_uploads", None)
    if tu is not None:
        try:  # One take's uploads in the bucket go with the account (best effort; the bucket lifecycle rule backs it up)
            tu.remove_for_user(uid)
        except Exception as e:  # noqa: BLE001 - never blocks deleting the account
            print(f"erase: take uploads not removed ({e.__class__.__name__})", flush=True)
    with store.tx():
        store.delete_user(uid)
        store.x("UPDATE admin_audit SET admin=? WHERE admin=?", (subject_hash(uid), uid))
        store.x("INSERT INTO user_tombstones(id_hash,at,via) VALUES(?,?,?) ON CONFLICT(id_hash) DO UPDATE SET at=excluded.at, via=excluded.via",
                (subject_hash(uid), time.time(), via))
        if admin_uid:
            log(store, admin_uid, "delete", uid)
        blobstore.sweep_soon(store)  # runs once this transaction has committed


def delete(store, admin_uid: str, uid: str, confirm, house=None) -> dict:
    _target(store, uid)
    if uid == admin_uid:
        raise AdminError("You can't delete your own account here. Use Disconnect and delete in Settings.", 400)
    if not isinstance(confirm, str) or confirm != uid:
        raise AdminError("Type the user's id exactly to confirm.", 400)
    erase_user(store, uid, via="admin", house=house, admin_uid=admin_uid)  # the log entry commits with the erasure
    return {"deleted": True, "subject_hash": subject_hash(uid)}


# ------------------------------------------------------------------ routes
def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except (TypeError, ValueError):
        return default


def add_routes(app, store, studio, cookie: str) -> None:
    """Every handler takes only the request (and a str path part), so nothing is validated, parsed or answered before
    gate(): a non-admin can't tell these routes from a missing one by sending a bad query or body (422 would say so)."""
    from starlette.concurrency import run_in_threadpool

    def gate(request: Request, write: bool = False) -> str:
        """The admin's id, or the same 404 as a missing route. Browser sessions only: a bearer token never gets in."""
        sess = store.session(request.cookies.get(cookie))
        user = store.user(sess["user_id"]) if sess and sess.get("user_id") else None
        if not is_admin(user):
            raise HTTPException(status_code=404)
        if write and request.headers.get("x-hq-csrf") != sess["csrf"]:
            raise HTTPException(status_code=403, detail="Missing or wrong CSRF token. Reload the page.")
        return user["id"]

    async def body_of(request: Request) -> dict:
        try:
            b = await request.json()
        except ValueError:
            return {}
        return b if isinstance(b, dict) else {}

    async def act(fn):
        try:
            return await run_in_threadpool(fn)
        except AdminError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request):
        gate(request)
        return FileResponse(UI / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    @app.get("/admin/{name}", include_in_schema=False)
    def admin_asset(name: str, request: Request):
        gate(request)
        if name not in UI_FILES:
            raise HTTPException(status_code=404)
        return FileResponse(UI / name, media_type=UI_FILES[name], headers={"Cache-Control": "no-store"})

    @app.get("/api/admin/users", include_in_schema=False)
    def admin_users(request: Request):
        gate(request)
        p = request.query_params
        return {**users(store, p.get("q", ""), _int(p.get("limit"), 100, 1, 500), _int(p.get("offset"), 0, 0, 10**7),
                        p.get("demo") == "1"), "overview": overview(store)}

    @app.post("/api/admin/users/{uid}/block", include_in_schema=False)
    async def admin_block(uid: str, request: Request):
        me = gate(request, write=True)
        b = await body_of(request)
        return await act(lambda: block(store, me, uid, b.get("reason", "") if isinstance(b.get("reason", ""), str) else ""))

    @app.post("/api/admin/users/{uid}/unblock", include_in_schema=False)
    async def admin_unblock(uid: str, request: Request):
        me = gate(request, write=True)
        return await act(lambda: unblock(store, me, uid))

    @app.post("/api/admin/users/{uid}/delete", include_in_schema=False)
    async def admin_delete(uid: str, request: Request):
        me = gate(request, write=True)
        b = await body_of(request)
        return await act(lambda: delete(store, me, uid, b.get("confirm"), getattr(studio, "house", None)))

    @app.get("/api/admin/egress", include_in_schema=False)
    def admin_egress(request: Request):
        """Media bandwidth today against the daily budgets, the disk cache and the last 14 daily summaries (numbers only)."""
        gate(request)
        from . import egress, mediacache, r2, r2usage
        # R2 against its free-tier caps (studio/r2usage.py), only when a bucket is configured: numbers only
        r2u = {"configured": True, **r2usage.snapshot(store)} if r2.config() else {"configured": False}
        return JSONResponse({"today": egress.snapshot(), "days": egress.history(), "cache": mediacache.of(store).stats(), "r2": r2u},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/admin/audit", include_in_schema=False)
    def admin_audit(request: Request):
        gate(request)
        return {"items": audit_rows(store, _int(request.query_params.get("limit"), 100, 1, 500))}
