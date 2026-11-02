"""Send a file to HuhQueue from any device, with a short code (owner bug, 2 Nov 2026: Claude.ai's sandbox can't reach
the one-time upload URL, and base64 chunks of a 27 MB video are too much for a chat).

The person (in the app) or their AI app (MCP ``create_send_code``) gets a short code such as ``K7PD-3QXA``, a link
``/send/K7PD3QXA`` and a QR code. They open the link on any device (phone, laptop, a second browser), or type the code
at ``/send``, and drag a photo or video in. The file streams straight into their Library through the Library's own
writer (MIME sniffed from the bytes, per-file cap, quota), so it is an upload like any other. The AI app then finds it
with ``list_recent_uploads``.

The code is the only credential: 8 characters from a 32-letter alphabet (40 bits), stored only as a hash, valid
30 minutes for up to 10 files. Wrong codes are counted per network (10 per 10 minutes, then 429), so guessing is
hopeless. Sample workspaces can't make codes (their Library is read-only).
"""
from __future__ import annotations

import base64
import io
import secrets
import threading
import time
from datetime import datetime, timezone

from fastapi import Body, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import demo_guard, staticver
from .actions import ActionError, Actor
from .store import sha
from .uploads import _actor, _agent_of

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # no I, O, 0, 1: nothing to misread
CODE_LEN = 8
TTL = 30 * 60
MAX_FILES = 10
WRONG_MAX, WRONG_WINDOW = 10, 600
RECENT_SOURCES = ("upload", "take", "link", "send")

SCHEMA = """
CREATE TABLE IF NOT EXISTS send_codes (
  id TEXT PRIMARY KEY, code_hash TEXT, user_id TEXT, agent TEXT, created REAL, expires REAL, files INTEGER, max_files INTEGER,
  items TEXT)
"""

TOOLS = [
    {"name": "create_send_code", "description": "BEST WAY to get a file from the user when your sandbox can't reach HuhQueue's "
     "upload URL (Claude.ai, ChatGPT and most chat apps): returns a short code, a link and a QR code. Tell the user: open the "
     "link (or go to /send and type the code) on any device, then drag the photo or video in. Up to 10 files in 30 minutes, "
     "each up to the Library's per-file limit (max_mb). Then call list_recent_uploads to get the file's media_token.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_recent_uploads", "description": "The newest files the user put in their Library by upload, the Send a file "
     "page, or a share link (newest first): library_id, media_token, name, kind, bytes, from, when. Use it after "
     "create_send_code or import_from_link to find the file the user just sent.",
     "inputSchema": {"type": "object", "properties": {
         "minutes": {"type": "integer", "minimum": 1, "maximum": 10080, "description": "How far back to look (default 60)"},
         "limit": {"type": "integer", "minimum": 1, "maximum": 50, "description": "At most this many (default 10)"}}}},
]
READ_ONLY = {"list_recent_uploads"}
NAMES = {t["name"] for t in TOOLS}

_wrong: dict[str, list[float]] = {}
_wrong_lock = threading.Lock()


def ensure(store) -> None:
    if getattr(store, "_send_ready", False):
        return
    stmt = SCHEMA.strip()
    if store.pg:
        stmt = stmt.replace(" REAL", " DOUBLE PRECISION")
    store.x(stmt)
    if store.pg:
        store.x("ALTER TABLE send_codes ENABLE ROW LEVEL SECURITY")
    store._send_ready = True


def norm(code: str) -> str:
    return "".join(c for c in str(code or "").upper() if c in ALPHABET)[:CODE_LEN]


def pretty(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


def qr_data_uri(url: str) -> str:
    """The link as an SVG QR code (data: URI, allowed by the CSP's img-src). '' when segno isn't installed."""
    try:
        import segno
    except ImportError:
        return ""
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#1a1c1e", light="#ffffff", xmldecl=False)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def _too_many_wrong(ip: str) -> bool:
    now = time.time()
    with _wrong_lock:
        hits = [t for t in _wrong.get(ip, []) if now - t < WRONG_WINDOW]
        _wrong[ip] = hits
        return len(hits) >= WRONG_MAX


def _count_wrong(ip: str) -> None:
    with _wrong_lock:
        _wrong.setdefault(ip, []).append(time.time())


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Send:
    def __init__(self, house):
        self.h, self.store, self.lib = house, house.store, house.library
        ensure(self.store)

    def limits(self) -> dict:
        from .library import FILE_MAX, MB, QUOTA
        from .uploads import CHUNK_MAX
        return {"file_max_bytes": FILE_MAX, "file_max_mb": FILE_MAX // MB, "library_quota_mb": QUOTA // MB,
                "link_import_max_mb": FILE_MAX // MB, "chunk_max_mb": CHUNK_MAX // MB, "send_code_files": MAX_FILES,
                "send_code_minutes": TTL // 60}

    def create(self, a: Actor) -> dict:
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace's Library is read-only. Connect Buffer to send your own files.", 403)
        self.h.studio._guard_write(a, "library_upload")
        self.lib.check_room(a, None)
        self.store.x("DELETE FROM send_codes WHERE expires<?", (time.time() - 3600,))
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        now = time.time()
        self.store.x("INSERT INTO send_codes(id,code_hash,user_id,agent,created,expires,files,max_files,items) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("sc_" + secrets.token_urlsafe(9), sha("send:" + code), a.uid, _agent_of(a), now, now + TTL, 0, MAX_FILES, ""))
        base = self.h.s.public_url.rstrip("/")
        url = f"{base}/send/{code}"
        lim = self.limits()
        self.store.audit(a.uid, a.label, "send_code", "read", {"expires_in": TTL})
        return {"code": pretty(code), "url": url, "page": f"{base}/send", "qr": qr_data_uri(url), "expires_in": TTL,
                "max_files": MAX_FILES, "max_mb": lim["file_max_mb"],
                "say": f"Open {url} on any device (or go to {base}/send and type {pretty(code)}), then drag the photo or video in. "
                       f"Up to {MAX_FILES} files in 30 minutes, {lim['file_max_mb']} MB each.",
                "next": "When the user says it's sent, call list_recent_uploads to get its media_token."}

    def _row(self, code: str, ip: str) -> dict:
        if _too_many_wrong(ip):
            raise ActionError("Too many wrong codes from this network. Wait 10 minutes, or make a new code in HuhQueue.", 429)
        c = norm(code)
        rows = self.store.q("SELECT * FROM send_codes WHERE code_hash=?", (sha("send:" + c),)) if len(c) == CODE_LEN else []
        if not rows:
            _count_wrong(ip)
            raise ActionError("That code isn't right. Check it in HuhQueue (Library > Send from a device) or in your AI app.", 404)
        row = dict(rows[0])
        if row["expires"] < time.time():
            raise ActionError("This code expired (codes last 30 minutes). Make a new one in HuhQueue.", 410)
        return row

    def status(self, code: str, ip: str) -> dict:
        row = self._row(code, ip)
        lim = self.limits()
        ids = [int(x) for x in (row.get("items") or "").split(",") if x.strip().isdigit()][-MAX_FILES:]
        got = []
        if ids:
            marks = ",".join("?" for _ in ids)
            got = self.store.q(f"SELECT id, name, kind, size FROM library_items WHERE user_id=? AND id IN ({marks})",
                               (row["user_id"], *ids))
            got = sorted(got, key=lambda r: -ids.index(r["id"]))  # newest first (a file sent twice is the same Library item)
        return {"ok": True, "files_left": max(0, int(row["max_files"]) - int(row["files"] or 0)),
                "expires_in": int(row["expires"] - time.time()), "max_mb": lim["file_max_mb"], "max_bytes": lim["file_max_bytes"],
                "received": [{"name": r["name"], "kind": r["kind"], "bytes": r["size"]} for r in got]}

    async def receive(self, code: str, request: Request, name: str = "") -> dict:
        ip = demo_guard.client_ip(request)
        row = self._row(code, ip)
        full = f"This code already took {row['max_files']} files. Make a new code for more."
        if int(row["files"] or 0) >= int(row["max_files"]):
            raise ActionError(full, 409)
        claimed = self.store.q("UPDATE send_codes SET files=files+1 WHERE id=? AND files<max_files RETURNING files", (row["id"],))
        if not claimed:
            raise ActionError(full, 409)
        cl = request.headers.get("content-length")
        try:
            declared = int(cl) if cl is not None else None
        except ValueError:
            raise ActionError("Bad Content-Length.", 400)
        nm = (name or request.headers.get("x-file-name") or "sent-file")[:80]
        a = _actor(row)
        try:
            it = await self.lib.save_stream(a, nm, request.stream(), declared, "send")
        except BaseException:
            self.store.x("UPDATE send_codes SET files=files-1 WHERE id=? AND files>0", (row["id"],))  # a failed file doesn't count
            raise
        cur = self.store.q("SELECT items FROM send_codes WHERE id=?", (row["id"],))
        ids = [x for x in ((cur[0]["items"] if cur else "") or "").split(",") if x] + [str(it.get("id"))]
        self.store.x("UPDATE send_codes SET items=? WHERE id=?", (",".join(ids[-MAX_FILES * 2:]), row["id"]))
        self.store.audit(row["user_id"], a.label, "send_file", "read", {"kind": it.get("kind"), "bytes": it.get("size")})
        return {"ok": True, "name": it.get("name"), "kind": it.get("kind"), "bytes": it.get("size"),
                "files_left": max(0, int(row["max_files"]) - int(claimed[0]["files"]))}

    def recent(self, a: Actor, minutes=60, limit=10) -> dict:
        try:
            minutes = max(1, min(int(minutes or 60), 10080))
            limit = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            raise ActionError("minutes and limit are whole numbers.")
        if demo_guard.is_demo(a.uid):
            return {"items": [], "note": "The sample workspace has no uploads. Connect Buffer to send your own files."}
        marks = ",".join("?" for _ in RECENT_SOURCES)
        rows = self.store.q(f"SELECT id, token, name, kind, size, source, created FROM library_items WHERE user_id=? AND "
                            f"storage='chunks' AND source IN ({marks}) AND created>=? ORDER BY created DESC, id DESC LIMIT ?",
                            (a.uid, *RECENT_SOURCES, time.time() - minutes * 60, limit))
        names = {"upload": "upload", "take": "upload", "link": "share link", "send": "Send a file page"}
        items = [{"library_id": r["id"], "media_token": r["token"], "url": self.h.media_url(r["token"]), "name": r["name"],
                  "kind": r["kind"], "bytes": r["size"], "from": names.get(r["source"], r["source"]), "when": _iso(r["created"])}
                 for r in rows]
        return {"items": items, "minutes": minutes,
                "note": "" if items else f"Nothing new in the last {minutes} minutes. If the user is still sending, wait and ask again."}


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    sd = studio.house.send
    if name == "create_send_code":
        return sd.create(actor)
    return sd.recent(actor, (args or {}).get("minutes", 60), (args or {}).get("limit", 10))


def add_routes(app, house, actor) -> None:
    sd = house.send

    def _err(e: ActionError) -> JSONResponse:
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.get("/send", include_in_schema=False)
    @app.get("/send/{code}", include_in_schema=False)
    def send_page(code: str = ""):
        return HTMLResponse(staticver.page("send.html"), headers={"Referrer-Policy": "no-referrer"})

    @app.get("/send/{code}/status", include_in_schema=False)
    def send_status(code: str, request: Request):
        try:
            return JSONResponse(sd.status(code, demo_guard.client_ip(request)), headers={"Cache-Control": "no-store"})
        except ActionError as e:
            return _err(e)

    @app.api_route("/send/{code}/file", methods=["PUT", "POST"], include_in_schema=False)
    async def send_file(code: str, request: Request, name: str = ""):
        """The raw file as the body (not a form); the code is the only credential (30 minutes, 10 files, Library caps)."""
        try:
            return await sd.receive(code, request, name)
        except ActionError as e:
            return _err(e)

    @app.post("/api/send-codes")
    def send_code_create(request: Request, body: dict = Body(default={})):
        return call_tool(house.studio, actor(request, write=True), "create_send_code", body or {})

    @app.get("/api/uploads/recent")
    def uploads_recent(request: Request, minutes: int = 60, limit: int = 10):
        return call_tool(house.studio, actor(request), "list_recent_uploads", {"minutes": minutes, "limit": limit})

    @app.get("/api/uploads/limits")
    def uploads_limits():
        return sd.limits()
