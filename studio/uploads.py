"""Uploads for any MCP client or script, with no public file host (owner bug, 2 Nov 2026).

Claude had a 26 MB MP4 on its side, but HuhQueue only imported from public https links; Claude's sandbox blocks file hosts,
and an artifact link answered with a sign-in page. Two ways in, both landing in the person's Library:

* ``create_upload``  one-time upload URL on our own domain: ``PUT /u/<token>`` with the raw file as the body (POST works too).
                     Valid 15 minutes, used once, capped at the Library's per-file limit and the person's quota. The answer
                     gives the exact curl command.
* ``upload_media_chunk`` + ``finish_upload``  for clients that can't make their own HTTP request: the file in base64 chunks
                     of at most 2 MB each (decoded), in order, under one upload_id; ``finish_upload`` turns them into a
                     Library item. Same caps; parts are kept for 15 minutes, then dropped.

The bytes go through the Library's own writer (MIME sniffed from the bytes, never trusted from the client, 1 MB chunks), so
an upload's media_token works everywhere a media token does (edit_video, render_reel, split_video, create_package, Buffer).
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
import time

from fastapi import Body, Request
from fastapi.responses import JSONResponse

from . import demo_guard
from .actions import ActionError, Actor
from .store import sha

TTL = 15 * 60
CHUNK_MAX = 2 * 1024 * 1024          # decoded bytes per upload_media_chunk call
MAX_PARTS = 200

SCHEMA = """
CREATE TABLE IF NOT EXISTS upload_sessions (
  id TEXT PRIMARY KEY, token_hash TEXT, user_id TEXT, agent TEXT, name TEXT, mode TEXT, size INTEGER, parts INTEGER,
  created REAL, expires REAL, used INTEGER);
CREATE TABLE IF NOT EXISTS upload_parts (upload_id TEXT, n INTEGER, data BLOB, PRIMARY KEY (upload_id, n));
"""

# What an AI app should do when its sandbox can't reach our upload URL (owner bug, 2 Nov 2026: Claude.ai's sandbox is
# network-blocked, and 2 MB base64 chunks of a 27 MB video are too much for a chat). Said in the tool descriptions, in
# create_upload's answer and in every chunk-upload error, so the app suggests a way that works.
BLOCKED = ("If your sandbox can't reach this URL (Claude.ai, ChatGPT and most chat apps block outside hosts), don't send a "
           "big file as base64 chunks. Instead: (1) create_send_code, and ask the user to open its link on any device and "
           "drag the file in, then list_recent_uploads; or (2) ask the user for a Google Drive, Dropbox, OneDrive or iCloud "
           "share link (\"anyone with the link\") and call import_from_link.")
CHUNK_FIT = 4 * 1024 * 1024   # beyond this, chunks through a chat are slow and fragile: say so up front

S = {"type": "string"}
TOOLS = [
    {"name": "create_upload", "description": "For a photo or video that is on the user's computer or in your sandbox (a local "
     "file, an artifact, an attachment), when you can run curl: returns a one-time upload URL on HuhQueue's own domain "
     "(valid 15 minutes, one use, up to the Library's per-file limit, max_mb) and the exact curl command: "
     "curl -X PUT --data-binary @file.mp4 '<upload_url>'. The answer is the Library item with its media_token for "
     "edit_video / render_reel / split_video / create_package. " + BLOCKED + " upload_media_chunk is only for small files "
     "(a few MB). Use library_upload_url / import_from_link for a file that is already at a link.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "File name, e.g. launch.mp4"},
         "size": {"type": "integer", "description": "File size in bytes, if you know it (checked against the limit now)"}}}},
    {"name": "upload_media_chunk", "description": "Last resort for SMALL files (up to ~4 MB) when create_upload's URL can't be "
     "reached: send the file as base64 chunks of at most 2 MB each (decoded size), in order (index 0, 1, 2 ...). Leave "
     "upload_id empty on the first chunk; pass the upload_id it returns on every later chunk. Then call "
     "finish_upload(upload_id). Parts expire after 15 minutes. For a bigger file (a video), use create_send_code (the user "
     "drags the file in on any device) or import_from_link (a Drive / Dropbox / OneDrive / iCloud share link) instead.",
     "inputSchema": {"type": "object", "required": ["index", "data"], "properties": {
         "upload_id": S, "index": {"type": "integer", "minimum": 0}, "data": {"type": "string", "description": "base64 of this chunk (<= 2 MB decoded)"},
         "name": {"type": "string", "description": "File name (first chunk), e.g. launch.mp4"},
         "total_size": {"type": "integer", "description": "The whole file's size in bytes, if you know it (first chunk)"}}}},
    {"name": "finish_upload", "description": "Join the chunks sent with upload_media_chunk into one Library item. Returns "
     "library_id, media_token, url, kind and bytes.",
     "inputSchema": {"type": "object", "required": ["upload_id"], "properties": {"upload_id": S}}},
]
NAMES = {t["name"] for t in TOOLS}


def ensure(store) -> None:
    if getattr(store, "_uploads_ready", False):
        return
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION").replace(" BLOB", " BYTEA")
        store.x(stmt)
    if store.pg:
        for t in ("upload_sessions", "upload_parts"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    store._uploads_ready = True


def _purge(store) -> None:
    old = [r["id"] for r in store.q("SELECT id FROM upload_sessions WHERE expires<?", (time.time() - 60,))]
    for uid_ in old:
        store.x("DELETE FROM upload_parts WHERE upload_id=?", (uid_,))
        store.x("DELETE FROM upload_sessions WHERE id=?", (uid_,))


def _agent_of(a: Actor) -> str:
    return json.dumps({"id": a.agent.get("id"), "name": a.agent.get("name")}) if a.kind == "agent" and a.agent else ""


def _actor(row) -> Actor:
    ag = json.loads(row["agent"] or "null") if row["agent"] else None
    return Actor(row["user_id"], "agent", {**ag, "policy": "review"}) if ag else Actor(row["user_id"])


def _item_out(it: dict) -> dict:
    return {"library_id": it.get("id"), "media_token": it.get("media_token"), "url": it.get("public_url") or it.get("url"),
            "kind": it.get("kind"), "name": it.get("name"), "bytes": it.get("size"),
            "next": "Use media_token with edit_video / render_reel / split_video, or its url in create_package."}


class Uploads:
    def __init__(self, house):
        self.h, self.store, self.lib = house, house.store, house.library
        ensure(self.store)

    def _open(self, a: Actor, name: str, mode: str, size: int | None) -> tuple[str, str]:
        self.h.studio._guard_write(a, "library_upload")
        self.lib.check_room(a, size)
        _purge(self.store)
        uid_, tok = "up_" + secrets.token_urlsafe(12), "ut_" + secrets.token_urlsafe(24)
        self.store.x("INSERT INTO upload_sessions(id,token_hash,user_id,agent,name,mode,size,parts,created,expires,used) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid_, sha(tok), a.uid, _agent_of(a), (name or "upload")[:80], mode, 0, 0,
                                                       time.time(), time.time() + TTL, 0))
        return uid_, tok

    def create(self, a: Actor, name: str = "", size: int | None = None) -> dict:
        size = int(size) if size not in (None, "") else None
        uid_, tok = self._open(a, name, "put", size)
        url = f"{self.h.s.public_url.rstrip('/')}/u/{tok}"
        from .library import FILE_MAX, MB
        fname = (name or "file.mp4").replace("'", "")
        return {"upload_id": uid_, "upload_url": url, "method": "PUT", "expires_in": TTL, "max_bytes": FILE_MAX,
                "max_mb": FILE_MAX // MB, "curl": f"curl -sS --fail-with-body -X PUT --data-binary @'{fname}' '{url}'",
                "note": f"One use, 15 minutes, up to {FILE_MAX // MB} MB. The body is the raw file (not base64, not a form). "
                        "The answer is the Library item (media_token, url).",
                "if_unreachable": BLOCKED,
                "alternatives": {"send_page": f"{self.h.s.public_url.rstrip('/')}/send (create_send_code gives the code)",
                                 "share_link": "import_from_link(url) for Google Drive, Dropbox, OneDrive or iCloud links"}}

    def _session(self, a: Actor | None, upload_id: str = "", token: str = "") -> dict:
        rows = (self.store.q("SELECT * FROM upload_sessions WHERE token_hash=?", (sha(token),)) if token else
                self.store.q("SELECT * FROM upload_sessions WHERE id=? AND user_id=?", (upload_id, a.uid if a else "")))
        if not rows:
            raise ActionError("Unknown upload. Start again with create_upload (or upload_media_chunk without upload_id).", 404)
        row = dict(rows[0])
        if row["used"]:
            raise ActionError("This upload was already used. Start a new one with create_upload.", 410)
        if row["expires"] < time.time():
            raise ActionError("This upload expired (uploads last 15 minutes). Start a new one.", 410)
        return row

    async def put(self, token: str, request: Request) -> dict:
        row = self._session(None, token=token)
        if row["mode"] != "put":
            raise ActionError("This upload takes chunks (upload_media_chunk), not a PUT.", 409)
        self.store.x("UPDATE upload_sessions SET used=1 WHERE id=? AND used=0", (row["id"],))  # one use, even if it fails
        cl = request.headers.get("content-length")
        try:
            declared = int(cl) if cl is not None else None
        except ValueError:
            raise ActionError("Bad Content-Length.", 400)
        a = _actor(row)
        it = await self.lib.save_stream(a, row["name"], request.stream(), declared, "upload")
        return _item_out(self._with_public(it))

    def chunk(self, a: Actor, upload_id: str, index, data: str, name: str = "", total_size=None) -> dict:
        try:
            return self._chunk(a, upload_id, index, data, name, total_size)
        except ActionError as e:
            if BLOCKED in str(e):
                raise
            raise ActionError(f"{e} {BLOCKED}", e.status) from None

    def _chunk(self, a: Actor, upload_id: str, index, data: str, name: str = "", total_size=None) -> dict:
        advice = ""
        try:
            index = int(index)
        except (TypeError, ValueError):
            raise ActionError("index is the chunk number: 0 for the first chunk, then 1, 2, ...")
        try:
            raw = base64.b64decode(str(data or ""), validate=True)
        except (binascii.Error, ValueError):
            raise ActionError("data must be base64 (standard alphabet, no data: prefix).")
        if not raw:
            raise ActionError("This chunk is empty.")
        if len(raw) > CHUNK_MAX:
            raise ActionError(f"Each chunk is at most 2 MB once decoded (this one is {len(raw) / 1048576:.1f} MB). Send smaller chunks.", 413)
        if not upload_id:
            if index != 0:
                raise ActionError("Start with index 0 and no upload_id; later chunks pass the upload_id it returns.")
            try:
                total = int(total_size) if total_size not in (None, "") else None
            except (TypeError, ValueError):
                total = None
            upload_id, _ = self._open(a, name, "chunks", total)
            if total and total > CHUNK_FIT:
                advice = (f"This file is {total / 1048576:.0f} MB, about {-(-total // CHUNK_MAX)} chunks through the chat. "
                          "Faster and safer: " + BLOCKED.split("Instead: ", 1)[1])
        row = self._session(a, upload_id)
        if row["mode"] != "chunks":
            raise ActionError("This upload is a PUT URL (create_upload), not a chunk upload.", 409)
        parts = int(row["parts"] or 0)
        if index > parts:
            raise ActionError(f"Chunks go in order: the next one is index {parts}.", 409)
        if index >= MAX_PARTS:
            raise ActionError("Too many chunks for one file.", 413)
        old = self.store.q("SELECT LENGTH(data) AS n FROM upload_parts WHERE upload_id=? AND n=?", (upload_id, index))
        size = int(row["size"] or 0) - (int(old[0]["n"] or 0) if old else 0) + len(raw)
        self.lib.check_room(a, size)
        if old:  # the same chunk again (a retry): replace it
            self.store.x("DELETE FROM upload_parts WHERE upload_id=? AND n=?", (upload_id, index))
        self.store.x("INSERT INTO upload_parts(upload_id,n,data) VALUES(?,?,?)", (upload_id, index, raw))
        self.store.x("UPDATE upload_sessions SET size=?, parts=?, expires=? WHERE id=?",
                     (size, max(parts, index + 1), time.time() + TTL, upload_id))
        return {"upload_id": upload_id, "received": index, "chunks": max(parts, index + 1), "bytes_so_far": size,
                "next_index": max(parts, index + 1), "next": "Send the next chunk with this upload_id, or finish_upload(upload_id).",
                **({"advice": advice} if advice else {})}

    def finish(self, a: Actor, upload_id: str) -> dict:
        row = self._session(a, upload_id)
        if row["mode"] != "chunks":
            raise ActionError("This upload is a PUT URL (create_upload): PUT the file to it instead.", 409)
        n = int(row["parts"] or 0)
        if not n:
            raise ActionError("No chunks yet. Send them with upload_media_chunk first.")
        have = {r["n"] for r in self.store.q("SELECT n FROM upload_parts WHERE upload_id=?", (upload_id,))}
        missing = [i for i in range(n) if i not in have]
        if missing:
            raise ActionError(f"Chunk {missing[0]} is missing. Send it again with upload_media_chunk.", 409)
        self.store.x("UPDATE upload_sessions SET used=1 WHERE id=?", (upload_id,))

        def parts():
            for i in range(n):
                yield bytes(self.store.q("SELECT data FROM upload_parts WHERE upload_id=? AND n=?", (upload_id, i))[0]["data"])
        try:
            it = self.lib.save_iter(a, row["name"], parts(), int(row["size"] or 0), "upload")
        finally:
            self.store.x("DELETE FROM upload_parts WHERE upload_id=?", (upload_id,))
        return _item_out(self._with_public(it))

    def _with_public(self, it: dict) -> dict:
        tok = it.get("media_token")
        return {**it, "public_url": self.h.media_url(tok) if tok else ""}


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    up = studio.house.uploads
    if demo_guard.is_demo(actor.uid):
        raise ActionError("The sample workspace's Library is read-only. Connect Buffer to upload your own files.", 403)
    if name == "create_upload":
        return up.create(actor, str(args.get("name") or ""), args.get("size"))
    if name == "upload_media_chunk":
        return up.chunk(actor, str(args.get("upload_id") or ""), args.get("index"), args.get("data"), str(args.get("name") or ""),
                        args.get("total_size"))
    return up.finish(actor, str(args.get("upload_id") or ""))


def add_routes(app, house, actor) -> None:
    up = house.uploads

    @app.api_route("/u/{token}", methods=["PUT", "POST"], include_in_schema=False)
    async def upload_put(token: str, request: Request):
        """A create_upload URL: the raw file as the body. The token is the only credential (one use, 15 minutes)."""
        try:
            return await up.put(token, request)
        except ActionError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.post("/api/uploads")
    def uploads_create(request: Request, body: dict = Body(default={})):
        return call_tool(house.studio, actor(request, write=True), "create_upload", body or {})

    @app.post("/api/uploads/chunk")
    def uploads_chunk(request: Request, body: dict = Body(default={})):
        return call_tool(house.studio, actor(request, write=True), "upload_media_chunk", body or {})

    @app.post("/api/uploads/{upload_id}/finish")
    def uploads_finish(upload_id: str, request: Request):
        return call_tool(house.studio, actor(request, write=True), "finish_upload", {"upload_id": upload_id})
