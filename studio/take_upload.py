"""Large One take sources: chunked, resumable uploads straight to disk (owner bug, 4 Nov 2026: "One take won't accept more
than ~1500 MB").

Why the old paths stopped short:
* the One take screen sent the video as ONE base64 JSON body to ``/api/media`` (40 MB client check, 40 MB ``MAX_UPLOAD``,
  56 MB body cap), decoded in memory on a 512 MB instance and kept as a database row;
* a link was streamed by the worker but refused past ``HQ_MAX_TAKE_BYTES`` (1500 MB by default): the ~1500 MB Ram saw;
* the Library's own uploads stop at 100 MB per file and 200 MB in all (they live in the database).

This path never holds a file in memory and never puts it in the database:
* ``POST /api/takes/uploads`` opens an upload (name, size). The answer gives ``chunk_size`` (8 MiB) and the chunks the server
  already has, so the same call resumes an unfinished upload of the same file after a dropped connection or a reload.
* ``PUT /api/takes/uploads/{id}/chunks/{n}`` with the raw chunk as the body and ``X-Chunk-Sha256``. The body is streamed to
  its place in one file on disk (offset n * chunk_size) while it is hashed; a hash or size mismatch is refused (422) and the
  chunk isn't counted, so the client sends it again. Chunks may arrive in any order and any number of times.
* ``POST /api/takes/uploads/{id}/finish`` re-reads the file once (streamed) and checks every chunk's hash again and, when
  given, the whole file's ``sha256``; then it checks the bytes are a video container. Only then can ``clips_from_video`` use
  it (``upload_id``).
* ``DELETE /api/takes/uploads/{id}`` aborts and removes the file. Unfinished uploads are removed after a day; a finished one
  that no take uses is removed after a day too; a take's source goes when the take is purged from the Bin.

The video worker fetches the file from a signed, expiring URL (``/takesrc/<id>?e=&s=``, HMAC over the id and expiry),
streamed with Range support, and caps what it downloads at the same limit.

The cap: ``HQ_TAKE_UPLOAD_MAX_MB`` (4096 MB by default), and never more than the spool disk's free space minus 1 GB of
headroom. The hosted app is a free Render web service with no persistent disk (Render API, 4 Nov 2026: plan free,
disk null), so its spool is the instance's ephemeral disk: a source lives until the instance restarts or redeploys, which
is why the take should start right after the upload (the UI does both in one go). Self-hosts point ``HQ_TAKE_SPOOL_DIR``
at a persistent disk.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from fastapi import Body, Request
from fastapi.responses import FileResponse, JSONResponse

from .actions import ActionError, Actor

MB = 1024 * 1024
CHUNK = 8 * MB                        # every chunk but the last is exactly this big
CHUNK_BODY_MAX = CHUNK + 64 * 1024    # the body cap on the chunk route (limits.cap_for)
HEADROOM = 1024 * MB                  # free disk kept back for renders and the database
TTL_OPEN = 24 * 3600                  # an unfinished upload, or a finished one no take uses, is removed after this
URL_TTL = 6 * 3600                    # the worker's signed source URL
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS take_uploads (
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, size INTEGER, chunk_size INTEGER, chunks INTEGER, status TEXT,
  sha256 TEXT, take_id INTEGER, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS take_upload_chunks (upload_id TEXT, n INTEGER, sha256 TEXT, size INTEGER, PRIMARY KEY (upload_id, n));
"""


def max_bytes() -> int:
    return int(os.environ.get("HQ_TAKE_UPLOAD_MAX_MB", "4096")) * MB


def spool_dir(settings=None) -> Path:
    env = os.environ.get("HQ_TAKE_SPOOL_DIR")
    if env:
        p = Path(env)
    else:
        db = str(getattr(settings, "db_path", "") or "")
        p = (Path(db).resolve().parent / "take_uploads") if db and not db.startswith(("postgres://", "postgresql://")) \
            else Path(tempfile.gettempdir()) / "hq_take_uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


def effective_max(settings=None) -> int:
    """What one upload may be right now: the configured cap, never more than free disk minus the headroom."""
    try:
        free = shutil.disk_usage(spool_dir(settings)).free
    except OSError:
        free = 0
    return max(0, min(max_bytes(), free - HEADROOM))


def is_video(head: bytes) -> bool:
    """MP4 / MOV / M4V (ISO BMFF 'ftyp' or a QuickTime atom) or WebM / MKV (EBML)."""
    return (len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")) or head[:4] == b"\x1a\x45\xdf\xa3"


def ensure(store) -> None:
    if getattr(store, "_take_uploads_ready", False):
        return
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION")
        store.x(stmt)
    if store.pg:
        for t in ("take_uploads", "take_upload_chunks"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    store._take_uploads_ready = True


class TakeUploads:
    def __init__(self, house):
        self.h, self.store, self.s = house, house.store, house.s
        ensure(self.store)

    # ------------------------------------------------------------------ helpers
    def path(self, upload_id: str) -> Path:
        if not re.fullmatch(r"tu_[A-Za-z0-9_-]{8,40}", upload_id or ""):
            raise ActionError("No such upload.", 404)
        return spool_dir(self.s) / f"{upload_id}.part"

    def _row(self, a: Actor, upload_id: str) -> dict:
        """Scope before existence: a missing id and someone else's id are the same 404."""
        if not isinstance(upload_id, str) or not upload_id.startswith("tu_"):
            raise ActionError("No such upload.", 404)
        rows = self.store.q("SELECT * FROM take_uploads WHERE id=? AND user_id=?", (upload_id, a.uid))
        if not rows:
            raise ActionError("No such upload.", 404)
        return dict(rows[0])

    def _received(self, upload_id: str) -> list[int]:
        return sorted(int(r["n"]) for r in self.store.q("SELECT n FROM take_upload_chunks WHERE upload_id=?", (upload_id,)))

    def _out(self, d: dict) -> dict:
        got = self._received(d["id"])
        done = sum(min(d["chunk_size"], d["size"] - n * d["chunk_size"]) for n in got)
        return {"upload_id": d["id"], "name": d["name"], "size": d["size"], "chunk_size": d["chunk_size"], "chunks": d["chunks"],
                "received": got, "bytes_received": done, "status": d["status"], "max_bytes": effective_max(self.s),
                **({"next_missing": next(i for i in range(d["chunks"]) if i not in set(got))} if len(got) < d["chunks"] else {})}

    def _drop(self, upload_id: str) -> None:
        try:
            self.path(upload_id).unlink(missing_ok=True)
        except ActionError:
            pass
        self.store.x("DELETE FROM take_upload_chunks WHERE upload_id=?", (upload_id,))
        self.store.x("DELETE FROM take_uploads WHERE id=?", (upload_id,))

    def purge_stale(self, now: float | None = None) -> int:
        """Unfinished uploads, and finished ones no take uses, older than TTL_OPEN: file and records removed."""
        cut = (now or time.time()) - TTL_OPEN
        old = [r["id"] for r in self.store.q("SELECT id FROM take_uploads WHERE take_id IS NULL AND updated<?", (cut,))]
        for x in old:
            self._drop(x)
        return len(old)

    # ------------------------------------------------------------------ API
    def create(self, a: Actor, body: dict) -> dict:
        self.h.studio._guard_write(a, "upload_take_source")
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace runs One take on the bundled sample talk. Connect Buffer to upload your own video.", 403)
        name = str(body.get("name") or "video.mp4").strip()[:120] or "video.mp4"
        size = body.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ActionError("size is the file's size in bytes (a whole number).")
        cap = effective_max(self.s)
        if size > cap:
            limit = max_bytes()
            why = (f"Videos up to {limit // MB:,} MB." if cap >= limit else
                   f"There's room for videos up to {cap // MB:,} MB on the server right now (the limit is {limit // MB:,} MB).")
            raise ActionError(f"{why} This one is {size / MB:,.0f} MB. Export a shorter cut or a smaller file (1080p H.264 is plenty).", 413)
        self.purge_stale()
        same = self.store.q("SELECT * FROM take_uploads WHERE user_id=? AND name=? AND size=? AND status='open' ORDER BY created DESC",
                            (a.uid, name, size))
        if same and self.path(same[0]["id"]).exists():  # the same file again: resume where it stopped
            return {**self._out(dict(same[0])), "resumed": True}
        uid_ = "tu_" + secrets.token_urlsafe(18)
        chunks = -(-size // CHUNK)
        with open(self.path(uid_), "wb") as fh:  # sparse: chunks land at their own offsets
            fh.truncate(size)
        now = time.time()
        self.store.x("INSERT INTO take_uploads(id,user_id,name,size,chunk_size,chunks,status,sha256,take_id,created,updated) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uid_, a.uid, name, size, CHUNK, chunks, "open", "", None, now, now))
        return {**self._out(self._row(a, uid_)), "resumed": False}

    def status(self, a: Actor, upload_id: str) -> dict:
        return self._out(self._row(a, upload_id))

    async def put_chunk(self, a: Actor, upload_id: str, n, request: Request) -> dict:
        d = self._row(a, upload_id)
        if d["status"] != "open":
            raise ActionError("This upload is already finished.", 409)
        try:
            n = int(n)
        except (TypeError, ValueError):
            raise ActionError("The chunk number is 0, 1, 2, ...")
        if not 0 <= n < d["chunks"]:
            raise ActionError(f"Chunk numbers go from 0 to {d['chunks'] - 1}.", 400)
        want = str(request.headers.get("x-chunk-sha256") or "").strip().lower()
        if not _HEX64.match(want):
            raise ActionError("Send the chunk's SHA-256 (hex) in the X-Chunk-Sha256 header.")
        expect = min(d["chunk_size"], d["size"] - n * d["chunk_size"])
        p = self.path(upload_id)
        if not p.exists():
            raise ActionError("This upload's file is gone (the server restarted). Start the upload again.", 410)
        hs, got = hashlib.sha256(), 0
        with open(p, "r+b") as fh:
            fh.seek(n * d["chunk_size"])
            async for piece in request.stream():
                got += len(piece)
                if got > expect:
                    raise ActionError(f"Chunk {n} is {expect} bytes; this one is bigger.", 413)
                hs.update(piece)
                fh.write(piece)
        if got != expect:
            raise ActionError(f"Chunk {n} should be {expect} bytes, got {got}. Send it again.", 422)
        if hs.hexdigest() != want:
            raise ActionError(f"Chunk {n} arrived damaged (its SHA-256 doesn't match). Send it again.", 422)
        self.store.x("DELETE FROM take_upload_chunks WHERE upload_id=? AND n=?", (upload_id, n))
        self.store.x("INSERT INTO take_upload_chunks(upload_id,n,sha256,size) VALUES(?,?,?,?)", (upload_id, n, want, got))
        self.store.x("UPDATE take_uploads SET updated=? WHERE id=?", (time.time(), upload_id))
        return {"upload_id": upload_id, "received": n, "chunks": d["chunks"], "count": len(self._received(upload_id))}

    def finish(self, a: Actor, upload_id: str, body: dict | None = None) -> dict:
        d = self._row(a, upload_id)
        if d["status"] == "ready":
            return self._out(d)
        hashes = {int(r["n"]): r["sha256"] for r in self.store.q("SELECT n, sha256 FROM take_upload_chunks WHERE upload_id=?", (upload_id,))}
        missing = [i for i in range(d["chunks"]) if i not in hashes]
        if missing:
            raise ActionError(f"Chunk {missing[0]} hasn't arrived yet ({len(missing)} missing). Send the missing chunks, then finish.", 409)
        whole = str((body or {}).get("sha256") or "").strip().lower()
        if whole and not _HEX64.match(whole):
            raise ActionError("sha256 is the whole file's SHA-256 in hex.")
        p = self.path(upload_id)
        if not p.exists() or p.stat().st_size != d["size"]:
            raise ActionError("This upload's file is gone (the server restarted). Start the upload again.", 410)
        full = hashlib.sha256()
        with open(p, "rb") as fh:  # streamed: every chunk is hashed again from disk, and the whole file once
            head = fh.read(16)
            fh.seek(0)
            for i in range(d["chunks"]):
                piece = fh.read(min(d["chunk_size"], d["size"] - i * d["chunk_size"]))
                if hashlib.sha256(piece).hexdigest() != hashes[i]:
                    self.store.x("DELETE FROM take_upload_chunks WHERE upload_id=? AND n=?", (upload_id, i))
                    raise ActionError(f"Chunk {i} doesn't match what was sent. Send it again, then finish.", 422)
                full.update(piece)
        digest = full.hexdigest()
        if whole and whole != digest:
            raise ActionError("The file's SHA-256 doesn't match the one you gave. Upload it again.", 422)
        if not is_video(head):
            self._drop(upload_id)
            raise ActionError("That file isn't a video One take can read (MP4, MOV or WebM).", 415)
        self.store.x("UPDATE take_uploads SET status='ready', sha256=?, updated=? WHERE id=?", (digest, time.time(), upload_id))
        self.store.audit(a.uid, a.label, "upload_take_source", "write", {"bytes": d["size"], "chunks": d["chunks"]})
        return {**self._out(self._row(a, upload_id)), "sha256": digest,
                "next": "Start the take with clips_from_video(upload_id=...) (POST /api/takes with upload_id)."}

    def abort(self, a: Actor, upload_id: str) -> dict:
        d = self._row(a, upload_id)
        if d["take_id"]:
            raise ActionError("A take uses this video; delete the take instead (it goes to the Bin).", 409)
        self._drop(upload_id)
        return {"aborted": True, "upload_id": upload_id}

    # ------------------------------------------------------------------ for takes.py
    def claim_for_take(self, a: Actor, upload_id: str, rid: int) -> dict:
        d = self._row(a, upload_id)
        if d["status"] != "ready":
            raise ActionError("Finish the upload first (all chunks, then finish).", 409)
        if d["take_id"]:
            raise ActionError("Another take already uses this upload. Upload the video again for a new take.", 409)
        if not self.path(upload_id).exists():
            raise ActionError("This upload's file is gone (the server restarted). Upload it again.", 410)
        self.store.x("UPDATE take_uploads SET take_id=?, updated=? WHERE id=?", (rid, time.time(), upload_id))
        return d

    def check_ready(self, a: Actor, upload_id: str) -> dict:
        d = self._row(a, upload_id)
        if d["status"] != "ready":
            raise ActionError("Finish the upload first (all chunks, then finish).", 409)
        if d["take_id"]:
            raise ActionError("Another take already uses this upload. Upload the video again for a new take.", 409)
        return d

    def remove_for_take(self, rid: int, uid: str) -> int:
        rows = self.store.q("SELECT id FROM take_uploads WHERE take_id=? AND user_id=?", (rid, uid))
        for r in rows:
            self._drop(r["id"])
        return len(rows)

    def _sig(self, upload_id: str, exp: int) -> str:
        key = (self.s.session_secret or "").encode() + b"|takesrc"
        return hmac.new(key, f"{upload_id}|{exp}".encode(), hashlib.sha256).hexdigest()

    def signed_url(self, upload_id: str, ttl: int = URL_TTL) -> str:
        exp = int(time.time()) + ttl
        return f"{self.s.public_url.rstrip('/')}/takesrc/{upload_id}?e={exp}&s={self._sig(upload_id, exp)}"

    def signed_path(self, upload_id: str, e: str, s: str) -> Path | None:
        try:
            exp = int(e)
        except (TypeError, ValueError):
            return None
        if exp < time.time() or not hmac.compare_digest(self._sig(upload_id, exp), str(s or "")):
            return None
        rows = self.store.q("SELECT status FROM take_uploads WHERE id=?", (upload_id,))
        if not rows or rows[0]["status"] != "ready":
            return None
        p = self.path(upload_id)
        return p if p.exists() else None


def add_routes(app, house, actor) -> None:
    up = house.take_uploads

    @app.post("/api/takes/uploads")
    def take_upload_create(request: Request, body: dict = Body(default={})):
        """upload_take_source: open (or resume) a chunked upload of a long video for One take."""
        return up.create(actor(request, write=True), body or {})

    @app.get("/api/takes/uploads/{upload_id}")
    def take_upload_status(upload_id: str, request: Request):
        return up.status(actor(request), upload_id)

    @app.put("/api/takes/uploads/{upload_id}/chunks/{n}")
    async def take_upload_chunk(upload_id: str, n: int, request: Request):
        a = actor(request, write=True)  # auth before a byte is read
        try:
            return await up.put_chunk(a, upload_id, n, request)
        except ActionError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.post("/api/takes/uploads/{upload_id}/finish")
    def take_upload_finish(upload_id: str, request: Request, body: dict = Body(default={})):
        return up.finish(actor(request, write=True), upload_id, body or {})

    @app.delete("/api/takes/uploads/{upload_id}")
    def take_upload_abort(upload_id: str, request: Request):
        return up.abort(actor(request, write=True), upload_id)

    @app.api_route("/takesrc/{upload_id}", methods=["GET", "HEAD"], include_in_schema=False)
    def take_source(upload_id: str, e: str = "", s: str = ""):
        """The video worker's download of a take's uploaded source: signed, expiring, streamed (Range supported)."""
        try:
            p = up.signed_path(upload_id, e, s)
        except ActionError:
            p = None
        if not p:
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})
