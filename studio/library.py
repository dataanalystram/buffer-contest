"""Library: every file a person uploaded and every post media the agent made, in one place (UI = REST = MCP).

Storage is the app's own database for now (Supabase Postgres in production, SQLite locally):

* ``library_items``   one row per item: name, MIME (sniffed from the bytes, never trusted from the client), kind
                      (video | image | carousel), source (upload | take | generated), size, SHA-256, where the bytes are.
* ``library_chunks``  an upload's bytes in 1 MB chunks (bytea), so a 100 MB video is never one row and a video player's
                      range request reads only the chunks it needs.
* ``library_media``   media tokens that belong to a library item. A token here is never purged, and an uploaded item's
                      own token works everywhere a media token does (the agent, the render worker, Buffer's /m/ fetch).

Generated media already live in the ``media`` table (the renderer writes them there); their library row points at
those tokens instead of copying the bytes. Caps: 100 MB a file, 200 MB a person (uploads and renders together), both
checked before a byte is read (SECURITY_RULES 6). The free sample workspace sees bundled sample items, read-only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import struct
import time
from pathlib import Path

from fastapi import Body, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import blobstore, demo_guard, egress, mediacache, r2usage
from . import metastrip
from .actions import ActionError, Actor
from .buffer_api import BufferError

MB = 1024 * 1024
CHUNK = MB
# a chunk's database bytes only when it has no object key and R2 is off: a copied chunk is read from R2 and its database
# copy (kept until Ram approves the cleanup) never leaves Postgres (Supabase egress). With R2 off, the copy is the source.
CHUNK_DATA = "CASE WHEN storage_key IS NULL THEN data END AS data"
FILE_MAX = int(os.environ.get("HQ_LIBRARY_FILE_MB", "100")) * MB
QUOTA = int(os.environ.get("HQ_LIBRARY_QUOTA_MB", "200")) * MB

SCHEMA = """
CREATE TABLE IF NOT EXISTS library_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, token TEXT, name TEXT, mime TEXT, kind TEXT, source TEXT,
  size INTEGER, sha256 TEXT, storage TEXT, meta TEXT, run_id INTEGER, created REAL);
CREATE TABLE IF NOT EXISTS library_chunks (item_id INTEGER, n INTEGER, data BLOB, PRIMARY KEY (item_id, n));
CREATE TABLE IF NOT EXISTS library_media (token TEXT PRIMARY KEY, item_id INTEGER, user_id TEXT);
CREATE TABLE IF NOT EXISTS library_purged (token TEXT PRIMARY KEY, user_id TEXT, at REAL);
"""
TABLES = ("library_items", "library_chunks", "library_media", "library_purged")
TRASH_DAYS = 7  # a deleted item stays restorable this long (see Library.delete)
BIN_SQL_MAX = 900  # binned tokens bound into the Library list's NOT IN (well under SQLite/Postgres parameter caps)
TRASH_TOKEN = "trash~"  # a trashed item's media rows keep their bytes under this prefix, so /m/<token> no longer serves them

# (mime, extension, kind) from the file's first bytes. Anything else is refused.
def sniff(head: bytes) -> tuple[str, str, str] | None:
    h = head or b""
    if h[:3] == b"\xff\xd8\xff":
        return "image/jpeg", "jpg", "image"
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png", "image"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image/webp", "webp", "image"
    if h[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif", "gif", "image"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand.startswith(b"qt"):
            return "video/quicktime", "mov", "video"
        if brand in (b"heic", b"heix", b"mif1", b"msf1", b"avif"):
            return None  # HEIC/AVIF stills: not something every network takes
        return "video/mp4", "mp4", "video"
    if h[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm", "webm", "video"
    return None


def safe_name(name: str, ext: str) -> str:
    """A display name with no path, no control characters and the sniffed extension (never the client's)."""
    base = Path(str(name or "").replace("\\", "/")).name
    stem = base.rsplit(".", 1)[0] if "." in base else base
    stem = re.sub(r"[^\w\- .()]+", "_", stem, flags=re.UNICODE).strip(" ._") or "file"
    return f"{stem[:72]}.{ext}"


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = (stmt.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
                    .replace(" BLOB", " BYTEA"))
        store.x(stmt)
    # which AI app (agent token) made the item: cleanup_agent_drafts tidies only what apps left (studio/run_discipline.py)
    if store.pg:
        store.x("ALTER TABLE library_items ADD COLUMN IF NOT EXISTS agent_id INTEGER")
    elif "agent_id" not in {r["name"] for r in store.q("PRAGMA table_info(library_items)")}:
        store.x("ALTER TABLE library_items ADD COLUMN agent_id INTEGER")
    # a chunk's object key when its bytes are in R2, and their hash (studio/blobstore.py, docs/STORAGE.md)
    have = set() if store.pg else {r["name"] for r in store.q("PRAGMA table_info(library_chunks)")}
    for col, typ in (("storage_key", "TEXT"), ("sha256", "TEXT"), ("nbytes", "INTEGER")):
        if store.pg:
            store.x(f"ALTER TABLE library_chunks ADD COLUMN IF NOT EXISTS {col} {typ}")
        elif col not in have:
            store.x(f"ALTER TABLE library_chunks ADD COLUMN {col} {typ}")
    store.x("CREATE INDEX IF NOT EXISTS library_chunks_storage_key ON library_chunks(storage_key)")
    store.x("CREATE INDEX IF NOT EXISTS library_items_user ON library_items(user_id, id)")
    store.x("CREATE INDEX IF NOT EXISTS library_items_run ON library_items(user_id, run_id)")
    store.x("CREATE INDEX IF NOT EXISTS library_items_token ON library_items(token)")
    if store.pg:  # closed to the hosted database's public data API, like every other table
        for t in TABLES:
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def record_owner_deletes(store, pairs) -> None:
    """Note (token, user_id) pairs as deleted by their owner in library_purged: /healthz then counts a post that used one
    as "deleted by owner", not as a lost file. Call it inside the same store.tx() as the DELETE it explains, so the
    record and the delete commit together or not at all. Every permanent media / Library delete goes through here
    (tests/studio/test_owner_delete_record.py lists them)."""
    pairs = list(pairs)
    now = _now()
    mediacache.of(store).forget([t for t, _ in pairs] + [TRASH_TOKEN + t for t, _ in pairs])  # no copy outlives the row
    for i in range(0, len(pairs), 300):
        part = pairs[i:i + 300]
        store.x("INSERT INTO library_purged(token,user_id,at) VALUES " + ",".join(["(?,?,?)"] * len(part)) +
                " ON CONFLICT(token) DO NOTHING", tuple(v for tok, uid in part for v in (tok, uid, now)))


def _now() -> float:
    return time.time()


def _agent_id(a: Actor) -> int | None:
    return a.agent["id"] if getattr(a, "kind", "") == "agent" and a.agent else None


def _sample_items() -> list[dict]:
    """The free sample's read-only Library: the sample take's clips and the example HuhByte carousel."""
    from .takes import sample_meta
    out = []
    meta = sample_meta() or {}
    base = Path(__file__).parent
    for i, c in enumerate(meta.get("clips") or [], 1):
        f = base / "sample_take" / c.get("file", "")
        if not f.is_file():
            continue
        out.append({"id": f"sample-{i}", "name": f"Sample clip {i}.mp4", "kind": "video", "mime": "video/mp4", "source": "sample",
                    "size": f.stat().st_size, "created": 0, "url": f"/sample/{c['file']}",
                    "thumb": f"/sample/{c['cover']}" if c.get("cover") else "", "used_in": [], "read_only": True})
    src = meta.get("source") or {}
    # the long video the sample clips were cut from: only when a bundle hosts it (the bundled sample doesn't any more; its
    # YouTube credit travels with each clip)
    if src.get("media_url", "").startswith("https://") and meta.get("clips"):
        out.append({"id": "sample-source", "name": f"{src.get('title') or 'Sample source video'}.mp4", "kind": "video",
                       "mime": "video/mp4", "source": "sample", "size": int(src.get("bytes") or 0), "created": 0,
                       "url": src["media_url"], "thumb": f"/sample/{src['poster']}" if src.get("poster") else "",
                       "duration": src.get("duration"), "credit": src.get("credit", ""), "origin_url": src.get("url", ""),
                       "note": src.get("note", ""), "clips": [f"sample-{i}" for i in range(1, len(meta["clips"]) + 1)],
                       "used_in": [], "read_only": True})
    ex = base / "static" / "examples" / "huhbyte"
    slides = sorted(p.name for p in ex.glob("launch-*.jpg")) if ex.is_dir() else []
    if slides:
        out.append({"id": "sample-carousel", "name": "Sample launch carousel", "kind": "carousel", "mime": "image/jpeg", "source": "sample",
                    "size": sum((ex / s).stat().st_size for s in slides), "created": 0, "url": f"/static/examples/huhbyte/{slides[0]}",
                    "thumb": f"/static/examples/huhbyte/{slides[0]}", "slides": [f"/static/examples/huhbyte/{s}" for s in slides],
                    "used_in": [], "read_only": True})
    for p in sorted(ex.glob("cover-*.jpg")) if ex.is_dir() else []:
        out.append({"id": f"sample-{p.stem}", "name": p.name, "kind": "image", "mime": "image/jpeg", "source": "sample",
                    "size": p.stat().st_size, "created": 0, "url": f"/static/examples/huhbyte/{p.name}",
                    "thumb": f"/static/examples/huhbyte/{p.name}", "used_in": [], "read_only": True})
    return out


# ---------------------------------------------------------------- Library filters (GET /api/library ?type= &created_by= ...)
TYPES = ("video", "carousel", "image", "audio")
SOURCES = {"agent": ("generated",), "you": ("upload", "take", "link", "send"), "sample": ("sample",)}
# "one_take" (owner ask, 5 Nov 2026): what One take made or was given, i.e. the clips its takes rendered (generated items
# whose run is a take) and the videos uploaded to it (source "take"). Not a source list like the others: matched by run.
CREATED_BY = (*SOURCES, "one_take")
USED_IN = ("used", "unused", "draft", "scheduled", "sent")
DATES = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "365d": 365}
GENERIC_NAME = re.compile(r"^(Reel\.mp4|Image\.jpg|Carousel \(\d+ slides\))$")


def _filters(type: str = "", created_by: str = "", used_in: str = "", run="", date: str = "") -> dict:
    """Checks the Library filters and returns them normalised; ``all`` / empty means no filter. A bad value is a 400."""
    def one(name, v, allowed):
        v = str(v or "").strip().lower()
        if v in ("", "all"):
            return ""
        if v not in allowed:
            raise ActionError(f"{name} must be one of: all, {', '.join(allowed)}.", 400)
        return v
    out = {"type": one("type", type, TYPES), "created_by": one("created_by", created_by, CREATED_BY),
           "used_in": one("used_in", used_in, USED_IN), "date": one("date", date, tuple(DATES)), "run": None}
    r = str(run or "").strip().lower()
    if r not in ("", "all"):
        if not r.isdigit():
            raise ActionError("run must be a run id.", 400)
        out["run"] = int(r)
    out["since"] = _now() - DATES[out["date"]] * 86400 if out["date"] else 0
    return out


def _keep(i: dict, f: dict) -> bool:
    """One item against every filter but type (the tabs count per type): who made it, date, used in, run."""
    if f.get("created_by") == "one_take" and not i.get("one_take"):
        return False
    src = SOURCES.get(f.get("created_by") or "")
    if src and i.get("source") not in src:
        return False
    if f.get("since") and (i.get("created") or 0) < f["since"]:
        return False
    uses = i.get("used_in") or []
    u = f.get("used_in")
    if (u == "used" and not uses) or (u == "unused" and uses):
        return False
    if u in ("draft", "scheduled", "sent") and not any(x.get(u) for x in uses):
        return False
    if f.get("run") and i.get("run_id") != f["run"] and not any(x.get("run_id") == f["run"] for x in uses):
        return False
    return True


NOT_MEDIA = "Upload a JPG, PNG, WebP or GIF image, or an MP4, MOV or WebM video."


def not_media(head: bytes) -> str:
    """Why a file isn't one we take, saying what it actually is when that's knowable (a web page is the usual one: a
    share or artifact link that answered with its sign-in page)."""
    h = (head or b"").lstrip()[:64].lower()
    if h.startswith((b"<!doctype html", b"<html", b"<head", b"<body", b"<!--", b"<?xml")) or h.startswith(b"<"):
        return ("That file is a web page (HTML, probably a sign-in or preview page), not a photo or video. "
                + NOT_MEDIA)
    if h.startswith((b"{", b"[")):
        return "That file is JSON text (probably an error message), not a photo or video. " + NOT_MEDIA
    return NOT_MEDIA


class _Writer:
    """One upload into library_chunks: cap and quota while bytes arrive, MIME sniffed from the first bytes, 1 MB chunks."""

    def __init__(self, lib: "Library", a: Actor, declared: int | None, source: str):
        lib.check_room(a, declared)
        self.lib, self.a, self.source = lib, a, source
        self.room = min(FILE_MAX, QUOTA - lib.used(a.uid))
        self.iid, self.tok = lib._new_item(a, source, "upload")
        self.h, self.buf, self.n, self.size, self.kind = hashlib.sha256(), bytearray(), 0, 0, None
        self.out, self.head = 0, False  # bytes stored (after metadata is stripped); True while a photo's header is held back

    def _strip(self, final: bool = False) -> None:
        """Hold a photo's header back until it is complete, then drop its EXIF/GPS (studio/metastrip.py)."""
        r = metastrip.head(self.kind[1], bytes(self.buf))
        if r is metastrip.INCOMPLETE and not final and len(self.buf) < metastrip.HEAD_MAX:
            return
        self.head = False
        if r:
            self.buf = bytearray(r)

    def _flush(self) -> None:
        while len(self.buf) >= CHUNK:
            part, self.buf = bytes(self.buf[:CHUNK]), self.buf[CHUNK:]
            self.h.update(part)
            self.lib._put_chunk(self.a.uid, self.iid, self.n, part)
            self.n += 1
            self.out += len(part)

    def needs_io(self, piece: bytes) -> bool:
        """Would feed(piece) write a chunk (a database row and, with R2, a bucket PUT)? Then it runs off the event loop."""
        return len(self.buf) + len(piece) >= CHUNK

    def feed(self, piece: bytes) -> None:
        self.size += len(piece)
        if self.size > self.room:
            raise ActionError(f"Files up to {FILE_MAX // MB} MB, and your Library holds {QUOTA // MB} MB in all.", 413)
        self.buf += piece
        if self.kind is None and len(self.buf) >= 16:
            self.kind = sniff(bytes(self.buf[:32]))
            if not self.kind:
                raise ActionError(not_media(bytes(self.buf[:64])), 415)
            self.head = self.kind[1] in ("jpg", "png")
        if self.head:
            self._strip()
            if self.head:
                return
        self._flush()

    def finish(self, name: str) -> dict:
        if self.kind is None:
            self.kind = sniff(bytes(self.buf[:32]))
            if not self.kind:
                raise ActionError(not_media(bytes(self.buf[:64])), 415)
            self.head = self.kind[1] in ("jpg", "png")
        if self.head:
            self._strip(final=True)
        self._flush()
        if self.buf:
            self.h.update(bytes(self.buf))
            self.lib._put_chunk(self.a.uid, self.iid, self.n, bytes(self.buf))
            self.out += len(self.buf)
        return self.lib._finish(self.a, self.iid, self.tok, name, self.kind, self.out, self.h.hexdigest(), self.source)

    def abort(self) -> None:
        self.lib._drop(self.iid)


class _ScrubStorageError(Exception):
    """The GPS check couldn't read the upload's bytes from storage (never an odd file: that's a parse error)."""


class Library:
    def __init__(self, house):
        self.h, self.store = house, house.store
        ensure(self.store)

    # ---------------------------------------------------------------- caps
    def used(self, uid: str) -> int:
        return int(self.store.q("SELECT COALESCE(SUM(size),0) AS n FROM library_items WHERE user_id=?", (uid,))[0]["n"] or 0)

    def usage(self, uid: str) -> dict:
        """The storage meter ("X of 2 GB used"), counted from the rows right now (never cached): every answer that changes
        it (upload, delete, Bin move, restore, purge) carries it, so the meter moves at once (owner bug, 3 Nov 2026)."""
        if demo_guard.is_demo(uid):
            return {"used": 0, "quota": QUOTA, "file_max": FILE_MAX}
        return {"used": self.used(uid), "quota": QUOTA, "file_max": FILE_MAX}

    def check_room(self, a: Actor, size: int | None) -> None:
        """Before a byte is read: the file cap, then the person's quota."""
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace's Library is read-only. Connect Buffer to keep your own files.", 403)
        if size is not None and size > FILE_MAX:
            raise ActionError(f"Files up to {FILE_MAX // MB} MB.", 413)
        if size is not None and self.used(a.uid) + size > QUOTA:
            raise ActionError(f"Your Library is full ({QUOTA // MB} MB). Delete files you no longer need in Library.", 413)

    # ---------------------------------------------------------------- uploads (chunked bytes)
    def _new_item(self, a: Actor, source: str, name: str) -> tuple[int, str]:
        tok = secrets.token_urlsafe(18)
        iid = self.store.x("INSERT INTO library_items(user_id,token,name,mime,kind,source,size,sha256,storage,meta,run_id,created,agent_id)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (a.uid, tok, name[:80], "", "", source, 0, "", "pending", "{}", None, _now(),
                                                                _agent_id(a)))
        return iid, tok

    def _put_chunk(self, uid: str, iid: int, n: int, data: bytes) -> None:
        """One 1 MB chunk: its bytes in the row (db backend) or in R2 under the owner's prefix (studio/blobstore.py)."""
        inline, key, digest = blobstore.put("lib", uid, data)
        try:
            self.store.x("INSERT INTO library_chunks(item_id,n,data,storage_key,sha256,nbytes) VALUES(?,?,?,?,?,?)",
                         (iid, n, inline, key, digest, len(data)))
        except BaseException:
            blobstore.discard(self.store, key)  # no row points at it
            raise
        blobstore.undo_on_rollback(self.store, key)  # a surrounding transaction that rolls back takes the object with it

    def _chunk(self, r, iid: int | None = None, n: int | None = None) -> bytes:
        try:
            if r["storage_key"] and r["data"] is None and not blobstore.r2_readable() and iid is not None:
                # copied to R2 but R2 is off here (or this month's read cap is reached, studio/r2usage.py): the database
                # copy, kept until cleanup, is the source
                got = self.store.q("SELECT data FROM library_chunks WHERE item_id=? AND n=?", (iid, n))
                if got and got[0]["data"] is not None:
                    return bytes(got[0]["data"])
            return blobstore.read(r["data"], r["storage_key"])
        except blobstore.StorageError as e:
            raise ActionError(str(e), 503)

    def _drop(self, iid: int) -> None:
        blobstore.release(self.store, "library_chunks", "item_id=?", (iid,))
        blobstore.sweep_soon(self.store)
        self.store.x("DELETE FROM library_media WHERE item_id=?", (iid,))
        self.store.x("DELETE FROM library_items WHERE id=?", (iid,))

    async def save_stream(self, a: Actor, name: str, stream, declared: int | None, source: str = "upload") -> dict:
        """Stream a request body into chunks: capped while it streams, MIME sniffed from the first bytes, nothing
        decoded. A file that breaks a rule leaves nothing behind. Every step that writes a chunk (the database and, with
        R2, a bucket call of up to 20 s) runs in the thread pool, never on the event loop, so one upload can't stall other
        requests or /healthz (Greptile, PR #110)."""
        w = await run_in_threadpool(_Writer, self, a, declared, source)
        try:
            async for piece in stream:
                if w.needs_io(piece):
                    await run_in_threadpool(w.feed, piece)
                else:
                    w.feed(piece)
            return await run_in_threadpool(w.finish, name)
        except BaseException:
            w.abort()  # database only (the objects go to blob_gc, swept on a thread): safe even while being cancelled
            raise

    def save_iter(self, a: Actor, name: str, pieces, declared: int | None, source: str = "upload") -> dict:
        """The same writer for bytes that arrive as a plain iterable (an MCP chunk upload being joined)."""
        w = _Writer(self, a, declared, source)
        try:
            for piece in pieces:
                w.feed(piece)
            return w.finish(name)
        except BaseException:
            w.abort()
            raise

    def save_bytes(self, a: Actor, data: bytes, name: str, source: str = "upload") -> dict:
        self.check_room(a, len(data))
        kind = sniff(data[:32])
        if not kind:
            raise ActionError(not_media(data[:64]), 415)
        data = metastrip.strip_bytes(kind[1], data)  # no EXIF/GPS from a phone photo at a public /m/ URL
        iid, tok = self._new_item(a, source, "upload")
        try:
            for n, i in enumerate(range(0, len(data), CHUNK)):
                self._put_chunk(a.uid, iid, n, data[i:i + CHUNK])
            return self._finish(a, iid, tok, name, kind, len(data), hashlib.sha256(data).hexdigest(), source)
        except BaseException:
            self._drop(iid)  # a refused finish (e.g. the GPS check couldn't read storage) leaves nothing half-made
            raise

    def _finish(self, a, iid, tok, name, kind, size, digest, source) -> dict:
        mime, ext, k = kind
        if not size:
            self._drop(iid)
            raise ActionError("That file is empty.")
        same = self.store.q("SELECT id FROM library_items WHERE user_id=? AND sha256=? AND storage='chunks' AND id<>?", (a.uid, digest, iid))
        if same:  # the same file again (same bytes, SHA-256): keep the one already there and say so
            self._drop(iid)
            self.store.audit(a.uid, a.label, "library_upload", "read", {"kind": k, "bytes": size, "source": source, "deduped": True})
            return {**self.item(a, same[0]["id"]), "deduped": True}
        if self.used(a.uid) + size > QUOTA:  # uploads that ran at the same time each saw room for themselves: recount now
            self._drop(iid)
            raise ActionError(f"Your Library is full ({QUOTA // MB} MB). Delete files you no longer need in Library.", 413)
        if k == "video":
            self._scrub_video(iid, size)
        tok = f"{tok}.{ext}"
        meta = {"type": "video" if k == "video" else "photo"}
        if k == "image":
            meta.update(self._image_size(iid))
        self.store.x("UPDATE library_items SET token=?, name=?, mime=?, kind=?, size=?, sha256=?, storage='chunks', meta=? WHERE id=?",
                     (tok, safe_name(name, ext), mime, k, size, digest, json.dumps(meta), iid))
        self.store.x("INSERT INTO library_media(token,item_id,user_id) VALUES(?,?,?)", (tok, iid, a.uid))
        self.store.audit(a.uid, a.label, "library_upload", "read", {"kind": k, "bytes": size, "source": source})
        return self.item(a, iid)

    SCRUB_UNAVAILABLE = ("This video couldn't be checked for location data because file storage didn't answer, so it "
                         "wasn't saved. Upload it again in a minute.")

    def _scrub_video(self, iid: int, size: int) -> None:
        """Blank the GPS position a phone writes into a video's moov box, in place (same length: nothing moves).
        Fails closed: when storage can't be read or rewritten the upload is refused (503) and the caller drops it, so a
        video is never published with its location still in it (Greptile, PR #110). Only a box layout the parser doesn't
        understand keeps the file as it came."""
        def read(s, e):
            try:
                return self._read(iid, s, e)
            except ActionError as err:  # _chunk(): the object couldn't be read (StorageError -> 503)
                raise _ScrubStorageError(str(err)) from None
        try:
            patches = metastrip.video_location_patches(read, size)
        except _ScrubStorageError:
            print("library: a video's location check couldn't read storage; upload refused", flush=True)
            raise ActionError(self.SCRUB_UNAVAILABLE, 503) from None
        except (struct.error, ValueError, IndexError, OverflowError):  # an odd box layout: keep the file as it came
            return
        try:
            for off, new in patches:
                for n in range(off // CHUNK, (off + len(new) - 1) // CHUNK + 1):
                    rows = self.store.q("SELECT data, storage_key FROM library_chunks WHERE item_id=? AND n=?", (iid, n))
                    if not rows:
                        continue
                    buf = bytearray(self._chunk(rows[0]))
                    lo, hi = max(off, n * CHUNK), min(off + len(new), (n + 1) * CHUNK)
                    buf[lo - n * CHUNK:hi - n * CHUNK] = new[lo - off:hi - off]
                    blobstore.rewrite(self.store, "library_chunks", "item_id=? AND n=?", (iid, n), bytes(buf))
        except (blobstore.StorageError, ActionError):
            print("library: a video's location couldn't be blanked in storage; upload refused", flush=True)
            raise ActionError(self.SCRUB_UNAVAILABLE, 503) from None

    def _image_size(self, iid: int) -> dict:
        try:
            import io

            from PIL import Image
            data = self._read(iid, 0, 2 * CHUNK - 1)  # the header is enough for the size: never the whole file in memory
            with Image.open(io.BytesIO(data)) as im:
                return {"width": im.width, "height": im.height}
        except Exception:  # noqa: BLE001
            return {}

    # ---------------------------------------------------------------- generated media (bytes stay in `media`)
    def register_generated(self, a: Actor, media: list[dict], fmt: str, run_id: int | None, created: float | None = None) -> int | None:
        """One library row per finished channel render (a video, an image or a whole carousel). Idempotent per token set."""
        if demo_guard.is_demo(a.uid):
            return None
        items = [m for m in media if m.get("token") and m.get("role") not in ("slide",)]
        if not items:
            return None
        toks = [m["token"] for m in items]
        have = {r["token"] for r in self.store.q(
            f"SELECT token FROM library_media WHERE token IN ({','.join('?' * len(toks))})", tuple(toks))}
        if set(toks) <= have:
            return None
        video = next((m for m in items if m.get("type") == "video"), None)
        if video:
            items, kind = [video], "video"
        elif fmt == "carousel" and len(items) > 1:
            kind = "carousel"
        else:
            items, kind = items[:1], "image"
        toks = [m["token"] for m in items]
        rows = self.store.q(f"SELECT token, mime, size, meta, user_id FROM media WHERE token IN ({','.join('?' * len(toks))})", tuple(toks))
        rows = [r for r in rows if r["user_id"] == a.uid]
        if not rows:
            return None
        by = {r["token"]: r for r in rows}
        first = by.get(toks[0]) or rows[0]
        meta = json.loads(first["meta"] or "{}")
        info = {k: meta[k] for k in ("width", "height", "duration") if meta.get(k)}
        info["tokens"] = [t for t in toks if t in by]
        info["format"] = fmt
        name = {"video": "Reel.mp4", "carousel": f"Carousel ({len(info['tokens'])} slides)", "image": "Image.jpg"}[kind]
        agent = _agent_id(a)
        if agent is None and run_id:  # a render finished by the worker callback (a human Actor) for an app's run: the app made it
            got = self.store.q("SELECT agent_id FROM agent_runs WHERE id=? AND user_id=?", (int(run_id), a.uid))
            agent = got[0]["agent_id"] if got and got[0]["agent_id"] is not None else None
        iid = self.store.x("INSERT INTO library_items(user_id,token,name,mime,kind,source,size,sha256,storage,meta,run_id,created,agent_id)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (a.uid, info["tokens"][0], name, first["mime"], kind, "generated", sum(int(by[t]["size"] or 0) for t in info["tokens"]),
                            "", "media", json.dumps(info), run_id, created or _now(), agent))
        for t in info["tokens"]:
            self.store.x("INSERT INTO library_media(token,item_id,user_id) VALUES(?,?,?) ON CONFLICT(token) DO NOTHING", (t, iid, a.uid))
        return iid

    def register_run(self, a: Actor, media: list[dict], fmt: str, run_id: int, output: int = 1) -> int | None:
        """ONE Library item per run output (owner, 4 Nov 2026): a run's latest render of an output replaces its earlier one
        in place (same item id, new files), so fixing a slide, re-rendering or switching carousel -> reel never adds another
        item. A request for several outputs ("make 3 reels") keeps one item per output (meta.output 2, 3, ...; output 1 has
        none), all on the same run. The earlier files stay with the run (its Undo); the item only points at the newest."""
        if demo_guard.is_demo(a.uid) or not run_id:
            return None
        output = int(output or 1)
        rows = self.store.q("SELECT * FROM library_items WHERE user_id=? AND run_id=? AND source='generated' AND storage='media' "
                            "ORDER BY id", (a.uid, int(run_id)))
        rows = [r for r in rows if int((json.loads(r["meta"] or "{}") or {}).get("output") or 1) == output]
        if not rows:
            iid = self.register_generated(a, media, fmt, int(run_id))
            if iid and output != 1:
                got = self.store.q("SELECT meta FROM library_items WHERE id=? AND user_id=?", (iid, a.uid))
                info = {**json.loads((got[0]["meta"] if got else "") or "{}"), "output": output}
                self.store.x("UPDATE library_items SET meta=? WHERE id=? AND user_id=?", (json.dumps(info), iid, a.uid))
            return iid
        d = dict(rows[0])
        items = [m for m in media if m.get("token") and m.get("role") not in ("slide",)]
        video = next((m for m in items if m.get("type") == "video"), None)
        if video:
            items, kind = [video], "video"
        elif fmt == "carousel" and len(items) > 1:
            kind = "carousel"
        else:
            items, kind = items[:1], "image"
        toks = [m["token"] for m in items]
        if not toks:
            return d["id"]
        got = self.store.q(f"SELECT token, mime, size, meta FROM media WHERE user_id=? AND token IN ({','.join('?' * len(toks))})",
                           (a.uid, *toks))
        by = {r["token"]: r for r in got}
        toks = [t for t in toks if t in by]
        if not toks:
            return d["id"]
        old = json.loads(d["meta"] or "{}")
        if old.get("tokens") == toks:
            return d["id"]
        meta = json.loads(by[toks[0]]["meta"] or "{}")
        info = {k: meta[k] for k in ("width", "height", "duration") if meta.get(k)}
        info.update(tokens=toks, format=fmt, versions=int(old.get("versions") or 1) + 1)
        if output != 1:
            info["output"] = output
        name = {"video": "Reel.mp4", "carousel": f"Carousel ({len(toks)} slides)", "image": "Image.jpg"}[kind]
        with self.store.tx():
            self.store.x("UPDATE library_items SET token=?, name=?, mime=?, kind=?, size=?, meta=? WHERE id=? AND user_id=?",
                         (toks[0], name, by[toks[0]]["mime"], kind, sum(int(by[t]["size"] or 0) for t in toks), json.dumps(info), d["id"], a.uid))
            for t in old.get("tokens") or []:  # the replaced files stay run media (the run's Undo still finds them)
                if t not in toks:
                    self.store.x("DELETE FROM library_media WHERE token=? AND item_id=? AND user_id=?", (t, d["id"], a.uid))
            for t in toks:
                self.store.x("INSERT INTO library_media(token,item_id,user_id) VALUES(?,?,?) ON CONFLICT(token) DO NOTHING", (t, d["id"], a.uid))
        return d["id"]

    def attach_run(self, a: Actor, iid, run_id: int) -> None:
        """An upload made for a post (run_id) belongs to that post: cleanup and "View run" know it. Never moves an item that
        already belongs to another run, never touches another person's item."""
        try:
            self.store.x("UPDATE library_items SET run_id=? WHERE id=? AND user_id=? AND run_id IS NULL", (int(run_id), int(iid), a.uid))
        except (TypeError, ValueError):
            pass

    # ---------------------------------------------------------------- reading
    def _row(self, a: Actor, iid) -> dict:
        try:
            iid = int(iid)
        except (TypeError, ValueError):
            raise ActionError("No such Library item.", 404)
        rows = self.store.q("SELECT * FROM library_items WHERE id=? AND user_id=? AND storage NOT IN ('pending','trash')", (iid, a.uid))
        if not rows:
            raise ActionError("No such Library item.", 404)
        d = dict(rows[0])
        d["meta"] = json.loads(d["meta"] or "{}")
        return d

    def _public(self, d: dict, runs: list[dict] | None = None) -> dict:
        tokens = d["meta"].get("tokens") or [d["token"]]
        url = f"/api/library/{d['id']}/file"
        out = {"id": d["id"], "name": d["name"], "kind": d["kind"], "mime": d["mime"], "source": d["source"], "size": int(d["size"] or 0),
               "created": d["created"], "url": url, "thumb": url if d["kind"] != "video" else "",
               "width": d["meta"].get("width"), "height": d["meta"].get("height"), "duration": d["meta"].get("duration"),
               "format": d["meta"].get("format") or "", "media_token": d["token"], "read_only": False}
        if d["kind"] == "carousel":
            out["slides"] = [f"{url}?i={i}" for i in range(len(tokens))]
        if runs is not None:
            rid = d.get("run_id")  # "View run": only for an item a run made, and only while that run still exists
            out["run_id"] = rid if rid and any(r["run_id"] == rid for r in runs) else None
            out["used_in"] = [r for r in runs if any(t in r["_blob"] for t in tokens)]
            out["used_in"] = [{k: v for k, v in r.items() if not k.startswith("_")} for r in out["used_in"]]
        return out

    def _runs(self, a: Actor) -> list[dict]:
        from .house import _run_thumb, _run_thumb_src
        out = []
        # the latest 200 runs, and every One take run however old (the One take filter, its clips' delete and the one Bin
        # need them all), in one read
        from . import run_text  # the text only when it changed (Supabase egress: md5s on Postgres, a memo on the Store)
        for r in run_text.attach(self.store, self.store.q(
                f"SELECT id, kind, {run_text.cols(self.store)}, status, created FROM agent_runs WHERE user_id=? AND (kind='take' OR id IN "
                "(SELECT id FROM agent_runs WHERE user_id=? ORDER BY id DESC LIMIT 200)) ORDER BY id DESC", (a.uid, a.uid))):
            try:
                inp, res = json.loads(r["input"] or "{}"), json.loads(r["result"] or "{}")
            except ValueError:
                continue
            title = (res.get("story") or {}).get("headline") or inp.get("prompt") or inp.get("query") or inp.get("name") or "A post"
            sent = [p.get("sent") for p in res.get("packages") or [] if p.get("sent")]
            scheduled = any((x or {}).get("mode") in ("queue", "schedule", "now") for x in sent)
            draft = bool(sent) and not scheduled
            out.append({"run_id": r["id"], "kind": r["kind"], "title": str(title)[:90], "status": r["status"], "sent": bool(sent),
                        "draft": draft, "scheduled": scheduled, "_created": r["created"], "_thumb": _run_thumb(res, inp), "_thumb_src": _run_thumb_src(res, inp), "_res": res,
                        "_blob": (r["input"] or "") + (r["result"] or "")})
        return out

    @staticmethod
    def _run_media(res: dict) -> list[tuple[str, list[dict]]]:
        """Every finished render a run holds, as (format, media) groups: per channel (agent runs), the packages (older runs),
        the run's own media list (the first News / media runs) and a take's rendered clips. Media without a token get it
        from their /m/<token> URL."""
        groups: list[tuple[str, list[dict]]] = []

        def norm(ms):
            out = []
            for m in ms or []:
                if not isinstance(m, dict):
                    continue
                tok = m.get("token") or (str(m.get("url") or "").rsplit("/m/", 1)[-1] if "/m/" in str(m.get("url") or "") else "")
                if tok and m.get("role") != "original":
                    out.append({**m, "token": tok.split("?")[0], "type": m.get("type") or "photo"})
            return out
        for v in (res.get("channel_media") or {}).values():
            if isinstance(v, dict) and v.get("media"):
                groups.append((v.get("format") or "", norm(v["media"])))
        for p in res.get("packages") or []:
            groups.append((p.get("format") or "", norm(p.get("media"))))
        if res.get("media"):
            groups.append(("", norm(res["media"])))
        for c in res.get("clips") or []:
            if c.get("token"):
                groups.append(("reel", [{"token": c["token"], "type": "video"}]))
        return [(f, ms) for f, ms in groups if ms]

    def backfill(self, a: Actor, runs: list[dict] | None = None) -> int:
        """Runs made before the Library existed (or rendered on the GitHub worker) left no Library rows, so "View all"
        opened an empty Library while Recent content showed five posts (owner, 1 Nov 2026). Register every run's renders
        once; register_generated is idempotent per token set and only takes the person's own media rows."""
        if demo_guard.is_demo(a.uid):
            return 0
        have = {r["run_id"] for r in self.store.q("SELECT DISTINCT run_id FROM library_items WHERE user_id=? AND run_id IS NOT NULL", (a.uid,))}
        # a run whose item was deleted for good stays done: its other renders (a cover, another channel's picture) never
        # come back as new Library items after Empty bin (owner bug, 3 Nov 2026)
        for r in self.store.q("SELECT token FROM library_purged WHERE user_id=? AND token LIKE ?", (a.uid, "run:%")):
            try:
                have.add(int(r["token"][4:]))
            except ValueError:
                continue
        n = 0
        for r in runs if runs is not None else self._runs(a):
            if r["run_id"] in have or r["status"] in ("running", "rendering"):
                continue
            for fmt, media in self._run_media(r["_res"]):
                try:
                    if self.register_generated(a, media, fmt, r["run_id"], r.get("_created")):
                        n += 1
                except Exception:  # noqa: BLE001  one odd old run never breaks the Library
                    continue
        return n

    def list(self, a: Actor, q: str = "", kind: str = "", limit: int = 200, offset: int = 0, per: int = 0,
             type: str = "", created_by: str = "", used_in: str = "", run: str | int = "", date: str = "") -> dict:
        """The Library (newest first). ``per``/``offset`` page it (``total`` is the count before paging); the answer also
        carries ``bin_count`` so the Library screen shows its Bin badge without a second request.

        Server-side filters (Library premium, 3 Nov 2026), all optional and combinable: ``type`` video | carousel | image |
        audio, ``created_by`` agent | you | sample | one_take (the clips One take rendered and the videos uploaded to it),
        ``used_in`` used | unused | draft | scheduled | sent, ``run`` a run id
        (items that run made or that a post of it uses), ``date`` 1d | 7d | 30d | 90d | 365d, and ``q`` (name, title, the
        runs that used it, the type). ``facets`` carries per-type counts (every filter but ``type``) for the tabs and the
        runs to offer in the Generation filter. Unknown filter values are a 400, never a silent "everything"."""
        flt = _filters(type, created_by, used_in, run, date)
        out = self._list(a, q, kind, 500 if per else limit, flt)
        take_bin_n = out.pop("_take_bin_n", 0)
        out["total"] = len(out["items"])
        if per:
            per, offset = max(1, min(int(per), 200)), max(0, int(offset or 0))
            out["items"], out["offset"], out["per"] = out["items"][offset:offset + per], offset, per
        # one Bin (owner ask, 5 Nov 2026): the count covers Library items and One take's binned takes and clips
        out["bin_count"] = 0 if demo_guard.is_demo(a.uid) else int(self.store.q(
            "SELECT COUNT(*) AS n FROM library_items WHERE user_id=? AND storage='trash'", (a.uid,))[0]["n"] or 0) + take_bin_n
        out["bin_days"] = TRASH_DAYS
        return out

    def _list(self, a: Actor, q: str = "", kind: str = "", limit: int = 200, flt: dict | None = None) -> dict:
        flt = flt or {}
        if demo_guard.is_demo(a.uid):
            items = _sample_items()
            for i in items:
                i.setdefault("title", i["name"])
                i.setdefault("run_id", None)
            used, take_bin_n = 0, 0
        else:
            runs = self._runs(a)  # one query for every run; each item's "used in" is matched in memory (no N+1)
            self.backfill(a, runs)
            takes = self._take_index(runs)
            where, args = ["user_id=?", "storage NOT IN ('pending','trash')"], [a.uid]
            if flt.get("since"):  # the cheap filters go to SQL (date, who made it); type stays in memory for the tab counts
                where.append("created >= ?")
                args.append(flt["since"])
            src = SOURCES.get(flt.get("created_by") or "")
            if src:
                where.append(f"source IN ({','.join('?' * len(src))})")
                args.extend(src)
            elif flt.get("created_by") == "one_take":  # owner-scoped: only this person's take runs
                where.append("(source='take' OR run_id IN (SELECT id FROM agent_runs WHERE user_id=? AND kind='take'))")
                args.append(a.uid)
            # binned takes and clips leave in SQL, before LIMIT, so a page never comes back short because the newest
            # rows were in the Bin. The bound list is capped (BIN_SQL_MAX); binned tokens past it are skipped as rows are
            # read, and more rows are read until the page is full (CodeRabbit, #121: a token can have several rows).
            lim = max(1, min(int(limit or 200), 500))
            binned = sorted(takes["binned"])
            in_sql, rest = binned[:BIN_SQL_MAX], binned[BIN_SQL_MAX:]
            if in_sql:
                where.append(f"token NOT IN ({','.join('?' * len(in_sql))})")
                args.extend(in_sql)
            sql = f"SELECT * FROM library_items WHERE {' AND '.join(where)} ORDER BY created DESC, id DESC LIMIT ? OFFSET ?"  # noqa: S608  where: fixed clauses, values bound
            rows, off, batch = [], 0, lim + len(rest)
            while True:  # refill by rows (a token can have several) until the page is full or no rows remain
                part = self.store.q(sql, (*args, batch, off))
                rows.extend(r for r in part if r["token"] not in takes["binned"])
                if len(rows) >= lim or len(part) < batch or not rest:
                    break
                off += len(part)
                batch = min(batch * 2, 2000)  # skipped rows grow the next read, so a long run of them takes few queries
            by_run = {r["run_id"]: r for r in runs}
            items = []
            for r in rows:
                d = dict(r)
                if d["token"] in takes["binned"]:  # a take or clip in the Bin: it shows there, never here with a dead file
                    continue
                if len(items) >= lim:
                    break
                d["meta"] = json.loads(d["meta"] or "{}")
                it = self._public(d, runs)
                it["one_take"] = d["source"] == "take" or d.get("run_id") in takes["runs"]
                if d["token"] in takes["clips"]:  # Move to bin goes to the take's Bin (DELETE /api/takes/{take_id}/clips/{clip_id})
                    rid, cid = takes["clips"][d["token"]]
                    it["take_clip"] = {"take_id": rid, "clip_id": cid}
                own = by_run.get(d.get("run_id"))
                if it["kind"] == "video" and own:  # a still for a video card: the run's own picture, else the story's
                    it["thumb"] = own["_thumb"] or ""
                    it["thumb_src"] = own["_thumb_src"]
                # a generic generated name ("Reel.mp4", "Carousel (4 slides)") reads as its run's headline on the card
                it["title"] = own["title"] if own and GENERIC_NAME.match(it["name"]) else it["name"]
                items.append(it)
            used, take_bin_n = self.used(a.uid), takes["bin_n"]
        if kind in ("video", "image", "carousel"):
            items = [i for i in items if i["kind"] == kind]
        elif kind == "draft":  # Make a post's "Drafts" filter, carried over by "View all"
            items = [i for i in items if any(u.get("draft") for u in i.get("used_in") or [])]
        elif kind in ("upload", "generated"):
            items = [i for i in items if (i["source"] in ("upload", "take", "link", "send")) == (kind == "upload")]
        items = [i for i in items if _keep(i, flt)]
        if q:
            ql = q.lower().strip()
            items = [i for i in items if ql in i["name"].lower() or ql in (i.get("title") or "").lower() or ql in i["kind"]
                     or any(ql in u["title"].lower() for u in i.get("used_in") or [])]
        types = {t: sum(1 for i in items if i["kind"] == t) for t in TYPES}
        seen: dict = {}
        for i in items:  # the Generation filter offers the runs behind what's listed (newest first, at most 50)
            for rid, title in [(i.get("run_id"), i.get("title"))] + [(u["run_id"], u["title"]) for u in i.get("used_in") or []]:
                if rid and rid not in seen and len(seen) < 50:
                    seen[rid] = {"run_id": rid, "title": str(title or "A post")[:90]}
        if flt.get("type"):
            items = [i for i in items if i["kind"] == flt["type"]]
        facets = {"types": {"all": sum(types.values()), **types},
                  "runs": sorted(seen.values(), key=lambda r: -int(r["run_id"]) if str(r["run_id"]).isdigit() else 0)}
        return {"items": items, "used": used, "quota": QUOTA, "file_max": FILE_MAX, "read_only": demo_guard.is_demo(a.uid),
                "facets": facets, "filters": {k: v for k, v in flt.items() if k != "since" and v not in ("", None)},
                "_take_bin_n": take_bin_n}

    @staticmethod
    def _take_index(runs: list[dict]) -> dict:
        """This person's One take runs (from _runs, the list's one read of agent_runs, which holds every take run): their ids, which media token is which clip (so a Library delete of a clip
        goes to the take's Bin), the tokens of takes and clips in the Bin (hidden from the Library list) and how many
        takes and clips the Bin holds (the Library's Bin count). Owner-scoped; a malformed run is skipped."""
        out: dict = {"runs": set(), "clips": {}, "binned": set(), "bin_n": 0}
        for r in runs:
            if r.get("kind") != "take":
                continue
            out["runs"].add(r["run_id"])
            res = r.get("_res")
            if not isinstance(res, dict):
                continue
            binned = r["status"] == "binned"
            if binned:
                out["bin_n"] += 1
                out["binned"].update(t for t in ((res.get("bin") or {}).get("tokens") or []) if isinstance(t, str))
            for c in res.get("clips") or []:
                if not isinstance(c, dict):
                    continue
                variants = [v.get("token") for v in (c.get("variants") or {}).values() if isinstance(v, dict)]
                toks = [t.removeprefix(TRASH_TOKEN) for t in [c.get("token"), c.get("cover_token"), *variants] if isinstance(t, str) and t]
                if binned or c.get("status") == "binned":
                    out["binned"].update(toks)
                    out["binned"].update(t for t in ((c.get("bin") or {}).get("tokens") or []) if isinstance(t, str))
                    if not binned:
                        out["bin_n"] += 1
                elif toks and isinstance(c.get("token"), str) and c.get("id"):
                    out["clips"][c["token"].removeprefix(TRASH_TOKEN)] = (r["run_id"], str(c["id"]))
        return out

    def item(self, a: Actor, iid) -> dict:
        return self._public(self._row(a, iid), self._runs(a))

    def _read(self, iid: int, start: int, end: int | None) -> bytes:
        """Bytes [start, end] of an uploaded item, reading only the chunks that hold them."""
        first = start // CHUNK
        last = (end // CHUNK) if end is not None else None
        sql = f"SELECT n, {CHUNK_DATA}, storage_key FROM library_chunks WHERE item_id=? AND n>=?" + (" AND n<=?" if last is not None else "") + " ORDER BY n"
        rows = self.store.q(sql, (iid, first, last) if last is not None else (iid, first))
        data = b"".join(self._chunk(r, iid, r["n"]) for r in rows)
        off = start - first * CHUNK
        return data[off:(end - first * CHUNK + 1) if end is not None else None]

    def _iter(self, iid: int):
        """Every chunk in order, one database read at a time (a whole-file answer never holds the file in memory)."""
        n = 0
        while True:
            rows = self.store.q(f"SELECT {CHUNK_DATA}, storage_key FROM library_chunks WHERE item_id=? AND n=?", (iid, n))
            if not rows:
                return
            yield self._chunk(rows[0], iid, n)
            n += 1

    def file(self, a: Actor, iid, i: int = 0) -> dict:
        """What GET /api/library/{id}/file serves: (mime, size, reader)."""
        d = self._row(a, iid)
        return self._file_of(d, i)

    def _file_of(self, d: dict, i: int = 0) -> dict:
        if d["storage"] == "chunks":
            size = int(d["size"] or 0)
            tok = str(d.get("token") or "")
            return mediacache.wrap(self.store, {"mime": d["mime"], "size": size, "read": lambda s, e: self._read(d["id"], s, e),
                                                "name": d["name"], "iter": lambda: self._iter(d["id"]), "owner": d.get("user_id"),
                                                "etag": etag_for(d.get("sha256"), tok, size)}, tok or f"item:{d['id']}", "library")
        toks = d["meta"].get("tokens") or [d["token"]]
        tok = toks[max(0, min(int(i or 0), len(toks) - 1))]
        f = media_file(self.store, tok)  # a rendered post's file: read by range, streamed, never loaded whole
        if not f:
            raise ActionError("This file is gone.", 404)
        return {**f, "name": d["name"]}

    # ---------------------------------------------------------------- tokens: library uploads work as media tokens
    def by_token(self, token: str) -> dict | None:
        if not token or token.startswith(TRASH_TOKEN):  # a binned item is served to its owner only (bin_file)
            return None
        rows = self.store.q("SELECT * FROM library_items WHERE token=? AND storage='chunks'", (token,))
        if not rows:
            return None
        d = dict(rows[0])
        d["meta"] = json.loads(d["meta"] or "{}")
        return d

    def blob(self, token: str) -> dict | None:
        d = self.by_token(token)
        if not d:
            return None
        f = self._file_of(d)  # through the disk cache: a render or Buffer push reads the database once per file
        return {"token": token, "user_id": d["user_id"], "mime": d["mime"], "data": b"".join(f["iter"]()),
                "size": int(d["size"] or 0), "meta": json.dumps({"kind": "upload", **d["meta"]})}

    def meta(self, a: Actor, token: str) -> dict | None:
        d = self.by_token(token)
        return {"kind": "upload", **d["meta"]} if d and d["user_id"] == a.uid else None

    # ---------------------------------------------------------------- delete / use
    def delete(self, a: Actor, iid, force: bool = False, mode: str = "bin") -> dict:
        """mode=bin (the default): the item moves to the Bin for TRASH_DAYS and can be restored. mode=permanent: the file
        and its record are removed now (this can't be undone). Both warn (409, force=true goes ahead) while a post that
        hasn't gone out, or a post scheduled in Buffer, uses the item. A permanent delete is refused outright (409,
        force can't override) while a post scheduled in Buffer uses it: Buffer fetches the file again when it publishes."""
        if mode not in ("bin", "permanent"):
            raise ActionError("mode is bin or permanent.")
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be deleted. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "delete_library_item")
        with self.store.tx():
            out = self._delete_row(a, self._row(a, iid), self._runs(a), force, mode)
        return {**out, **self.usage(a.uid)}

    @staticmethod
    def _uses(d: dict, runs: list[dict], tokens: list[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
        """(every run using the item, the ones not gone out yet, the ones scheduled in Buffer)."""
        toks = [t for t in (tokens or d["meta"].get("tokens") or [d["token"]]) if t]
        used = [r for r in runs if any(t in r["_blob"] for t in toks)]
        live = [u for u in used if not u["sent"] and u["status"] in ("ready", "running", "rendering")]
        sched = [u for u in used if u.get("scheduled")]
        return used, live, sched

    def _delete_row(self, a: Actor, d: dict, runs: list[dict], force: bool, mode: str, batch: list | None = None) -> dict:
        used, live, sched = self._uses(d, runs)
        if mode == "permanent" and sched:
            raise ActionError(f"“{d['name']}” can't be deleted permanently: it's used in a post scheduled in Buffer "
                              f"(“{sched[0]['title'][:60]}”), and Buffer fetches the file again when it publishes. "
                              "Unschedule that post first, or move the item to the Bin.", 409)
        risky = live + sched
        if risky and not force:
            what = "scheduled in Buffer" if sched else "that hasn't gone out yet"
            n = len(risky)
            raise ActionError(f"“{d['name']}” is used in {n} post{'s' if n > 1 else ''} {what} "
                              f"(“{risky[0]['title'][:60]}”). Delete it anyway?", 409)
        if batch is not None:  # bulk delete: the writes go out together afterwards (_trash_many / _purge_rows)
            batch.append(d)
        elif mode == "permanent":
            self._purge_row(d)
        else:
            self._trash(a, d)
        self.store.audit(a.uid, a.label, "delete_library_item", "safety", {"kind": d["kind"], "bytes": int(d["size"] or 0),
                                                                            "forced": bool(risky), "mode": mode, "trash_days": TRASH_DAYS})
        out = {"deleted": True, "id": d["id"], "mode": mode, "was_used_in": [u["run_id"] for u in used]}
        if mode == "bin":
            out.update({"in_trash_days": TRASH_DAYS, "restore": f"POST /api/library/{d['id']}/restore"})
        return out

    def _purge_row(self, d: dict) -> None:
        self._purge_rows([d])

    def _purge_rows(self, rows: list[dict]) -> None:
        """Remove items' bytes and records for good, in one transaction and a handful of statements whatever the count
        (owner bug, 3 Nov 2026: Empty bin ran ~6 statements per item against the remote Postgres, a refresh while it
        ran showed half of it, the next refresh none). Their media rows go (live or binned), their tokens are noted as
        deleted by the person (/healthz doesn't count posts that used them as missing), and the runs that made them are
        noted so the Library's backfill never registers a run's other renders as new items after the purge."""
        if not rows:
            return
        toks, media, purged, runs, ids = [], [], [], set(), []
        for d in rows:
            t = d["meta"].get("trash") or {}
            mine = [x for x in (t.get("tokens") or d["meta"].get("tokens") or [t.get("token") or d["token"]]) if x]
            mine = [x.removeprefix(TRASH_TOKEN) for x in mine]
            if (t.get("storage") or d["storage"]) == "media":
                media += [(d["user_id"], x) for x in mine] + [(d["user_id"], TRASH_TOKEN + x) for x in mine]
            purged += [(x, d["user_id"]) for x in mine]
            if d.get("run_id"):
                runs.add((f"run:{d['run_id']}", d["user_id"]))
            ids.append(int(d["id"]))
        st = self.store
        with st.tx():
            for uid in {u for u, _ in media}:
                ts = [x for u, x in media if u == uid]
                for i in range(0, len(ts), 400):
                    part = ts[i:i + 400]
                    blobstore.release(st, "media", f"user_id=? AND token IN ({','.join('?' * len(part))})", (uid, *part))
            record_owner_deletes(st, purged + sorted(runs))
            for i in range(0, len(ids), 400):
                part = ids[i:i + 400]
                marks = ",".join("?" * len(part))
                blobstore.release(st, "library_chunks", f"item_id IN ({marks})", tuple(part))
                st.x(f"DELETE FROM library_media WHERE item_id IN ({marks})", tuple(part))
                st.x(f"DELETE FROM library_items WHERE id IN ({marks})", tuple(part))
        blobstore.sweep_soon(st)  # the objects go after the rows' transaction committed (studio/blobstore.py)

    MAX_BULK = 200

    def _ids(self, ids) -> list[int]:
        try:
            out = list(dict.fromkeys(int(i) for i in (ids or [])))
        except (TypeError, ValueError):
            raise ActionError("ids is a list of Library item ids.")
        if not out:
            raise ActionError("Pick at least one item.")
        if len(out) > self.MAX_BULK:
            raise ActionError(f"Up to {self.MAX_BULK} items at a time.")
        return out

    def bulk_delete(self, a: Actor, ids, mode: str = "bin", force: bool = False) -> dict:
        """Delete several items (mode bin | permanent). Each item is checked on its own, exactly like a single delete: the
        ones a post needs come back under ``refused`` with the reason (``needs_force`` when force=true would go ahead)."""
        if mode not in ("bin", "permanent"):
            raise ActionError("mode is bin or permanent.")
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be deleted. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "delete_library_item")
        ids = self._ids(ids)
        done, refused = [], []
        with self.store.tx():  # one unit: a refresh meanwhile never shows half a bulk delete
            runs, batch = self._runs(a), []
            have = {}
            if ids:  # every row in one read (was one query per id)
                for r in self.store.q(f"SELECT * FROM library_items WHERE user_id=? AND storage NOT IN ('pending','trash') "
                                      f"AND id IN ({','.join('?' * len(ids))})", (a.uid, *ids)):
                    d = dict(r)
                    d["meta"] = json.loads(d["meta"] or "{}")
                    have[d["id"]] = d
            for iid in ids:
                try:
                    if iid not in have:
                        raise ActionError("No such Library item.", 404)
                    done.append(self._delete_row(a, have[iid], runs, force, mode, batch)["id"])
                except ActionError as e:
                    refused.append({"id": iid, "error": str(e), "status": e.status,
                                    "needs_force": e.status == 409 and "can't be deleted permanently" not in str(e)})
            if mode == "permanent":
                self._purge_rows(batch)
            else:
                self._trash_many(a, batch)
        return {"mode": mode, "deleted": done, "refused": refused, "in_trash_days": TRASH_DAYS if mode == "bin" else None,
                **self.usage(a.uid)}

    # ---------------------------------------------------------------- trash (owner bug, 2 Nov 2026)
    # Nine Library items were deleted in 43 s on 2 Nov (20:50 GMT), five of them past the "used in a post that hasn't gone
    # out" warning, and the files of six ready posts were gone for good (/healthz missing_files 9 -> 42). A delete now
    # moves the item to the Trash for TRASH_DAYS: it leaves the Library at once and its files stop being served (the
    # posts that used them show "missing" exactly as before), but the bytes are kept and POST /api/library/{id}/restore
    # brings the item and every post's files back. purge_trash() removes them for good after TRASH_DAYS.
    def _trash(self, a: Actor, d: dict) -> None:
        toks = d["meta"].get("tokens") or [d["token"]]
        meta = {**d["meta"], "trash": {"at": _now(), "storage": d["storage"], "tokens": toks, "token": d["token"]}}
        if d["storage"] == "media":
            for t in toks:  # the media row keeps its bytes under a token nothing resolves
                self.store.x("UPDATE media SET token=? WHERE token=? AND user_id=?", (TRASH_TOKEN + t, t, a.uid))
        self.store.x("UPDATE library_items SET storage='trash', token=?, meta=? WHERE id=?",
                     (TRASH_TOKEN + d["token"], json.dumps(meta), d["id"]))

    def _trash_many(self, a: Actor, rows: list[dict]) -> None:
        """_trash for several items: their media rows move under the Bin prefix in one statement per 400 tokens."""
        toks = [t for d in rows if d["storage"] == "media" for t in (d["meta"].get("tokens") or [d["token"]])]
        with self.store.tx():
            for i in range(0, len(toks), 400):
                part = toks[i:i + 400]
                self.store.x(f"UPDATE media SET token=? || token WHERE user_id=? AND token IN ({','.join('?' * len(part))})",
                             (TRASH_TOKEN, a.uid, *part))
            for d in rows:
                meta = {**d["meta"], "trash": {"at": _now(), "storage": d["storage"], "tokens": d["meta"].get("tokens") or [d["token"]],
                                               "token": d["token"]}}
                self.store.x("UPDATE library_items SET storage='trash', token=?, meta=? WHERE id=?",
                             (TRASH_TOKEN + d["token"], json.dumps(meta), d["id"]))

    def _bin_rows(self, a: Actor, ids: list[int] | None = None) -> list[dict]:
        sql, args = "SELECT * FROM library_items WHERE user_id=? AND storage='trash'", [a.uid]
        if ids is not None:
            sql += f" AND id IN ({','.join('?' * len(ids))})"
            args += ids
        out = []
        for r in self.store.q(sql + " ORDER BY id DESC", tuple(args)):
            d = dict(r)
            d["meta"] = json.loads(d["meta"] or "{}")
            out.append(d)
        return out

    def trash(self, a: Actor, offset: int = 0, per: int = 0) -> dict:
        """The Bin: items deleted in the last TRASH_DAYS days, newest first, each with how long until it's removed for good."""
        if demo_guard.is_demo(a.uid):
            return {"items": [], "days": TRASH_DAYS, "total": 0, **self.usage(a.uid)}
        rows, now = self._bin_rows(a), _now()
        runs = self._runs(a) if rows else []
        out = []
        for d in rows:
            t = d["meta"].get("trash") or {}
            at = t.get("at") or 0
            purge = at + TRASH_DAYS * 86400
            left = max(0.0, purge - now)
            used, _, sched = self._uses(d, runs, t.get("tokens") or [t.get("token")])
            url = f"/api/library/bin/{d['id']}/file"
            ntok = len(t.get("tokens") or [1])
            out.append({"id": d["id"], "name": d["name"], "kind": d["kind"], "mime": d["mime"], "source": d["source"],
                        "size": int(d["size"] or 0), "created": d["created"], "deleted_at": at, "purge_at": purge,
                        "days_left": int(-(-left // 86400)) if left else 0, "url": url, "thumb": url if d["kind"] != "video" else "",
                        "slides": [f"{url}?i={i}" for i in range(ntok)] if d["kind"] == "carousel" else None,
                        "used_in": [{k: v for k, v in u.items() if not k.startswith("_")} for u in used],
                        "scheduled_use": bool(sched)})
        total = len(out)
        if per:
            per, offset = max(1, min(int(per), 200)), max(0, int(offset or 0))
            out = out[offset:offset + per]
        return {"items": out, "days": TRASH_DAYS, "total": total, **self.usage(a.uid)}

    def bin_file(self, a: Actor, iid, i: int = 0) -> dict:
        """A binned item's file, for its owner only (the Bin's thumbnails and preview); /m/<token> still serves nothing."""
        rows = self._bin_rows(a, [int(iid)])
        if not rows:
            raise ActionError("That item isn't in the Bin.", 404)
        d = rows[0]
        t = d["meta"].get("trash") or {}
        if (t.get("storage") or "media") == "chunks":
            return self._file_of({**d, "storage": "chunks"}, i)
        toks = [TRASH_TOKEN + x for x in (t.get("tokens") or [t.get("token")]) if x]
        return self._file_of({**d, "storage": "media", "meta": {"tokens": toks}, "token": toks[0] if toks else ""}, i)

    def restore(self, a: Actor, iid) -> dict:
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be changed. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "restore_library_item")
        try:
            iid = int(iid)
        except (TypeError, ValueError):
            raise ActionError("No such Library item.", 404)
        rows = self.store.q("SELECT * FROM library_items WHERE id=? AND user_id=? AND storage='trash'", (iid, a.uid))
        if not rows:
            raise ActionError("That item isn't in the Bin (it may have been removed for good after "
                              f"{TRASH_DAYS} days).", 404)
        with self.store.tx():
            out = self._restore_row(a, dict(rows[0]))
        return {**out, **self.usage(a.uid)}

    def _restore_row(self, a: Actor, r: dict) -> dict:
        m = json.loads(r["meta"] or "{}")
        t = m.pop("trash", {}) or {}
        storage, tok = t.get("storage") or "media", t.get("token") or r["token"].removeprefix(TRASH_TOKEN)
        if storage == "media":
            for x in t.get("tokens") or [tok]:
                self.store.x("UPDATE media SET token=? WHERE token=? AND user_id=?", (x, TRASH_TOKEN + x, a.uid))
        self.store.x("UPDATE library_items SET storage=?, token=?, meta=? WHERE id=?", (storage, tok, json.dumps(m), r["id"]))
        self.store.audit(a.uid, a.label, "restore_library_item", "safety", {"kind": r["kind"], "bytes": int(r["size"] or 0)})
        return {"restored": True, "id": r["id"]}

    def restore_many(self, a: Actor, ids) -> dict:
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be changed. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "restore_library_item")
        ids = self._ids(ids)
        done = []
        with self.store.tx():
            rows = {d["id"]: d for d in self._bin_rows(a, ids)}
            for iid in ids:
                if iid in rows:
                    d = rows[iid]
                    self._restore_row(a, {**d, "meta": json.dumps(d["meta"])})
                    done.append(iid)
        return {"restored": done, "missing": [i for i in ids if i not in rows], **self.usage(a.uid)}

    def purge(self, a: Actor, ids=None, everything: bool = False) -> dict:
        """Delete permanently from the Bin: the listed ids, or everything=true (Empty bin). Can't be undone. An item a post
        scheduled in Buffer still uses is refused (Buffer fetches the file again when it publishes)."""
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be deleted. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "purge_library_bin")
        want = None if everything else self._ids(ids)
        with self.store.tx():  # read, check and delete as one unit: a refresh meanwhile sees the Bin before or after
            rows = self._bin_rows(a, want)
            runs = self._runs(a) if rows else []
            ok, done, refused = [], [], []
            for d in rows:
                t = d["meta"].get("trash") or {}
                _, _, sched = self._uses(d, runs, t.get("tokens") or [t.get("token")])
                if sched:
                    refused.append({"id": d["id"], "status": 409, "needs_force": False,
                                    "error": f"“{d['name']}” can't be deleted permanently: it's used in a post scheduled in Buffer "
                                             f"(“{sched[0]['title'][:60]}”), and Buffer fetches the file again when it publishes."})
                    continue
                ok.append(d)
                done.append(d["id"])
            self._purge_rows(ok)
            if want is not None:
                have = {d["id"] for d in rows}
                refused += [{"id": i, "status": 404, "needs_force": False, "error": "That item isn't in the Bin."} for i in want if i not in have]
            if done:
                self.store.audit(a.uid, a.label, "purge_library_bin", "safety", {"items": len(done), "everything": bool(everything)})
        return {"purged": done, "refused": refused, **self.usage(a.uid)}

    def purge_trash(self, days: float | None = None) -> int:
        """Remove for good what has been in the Bin longer than ``days`` (TRASH_DAYS). Returns how many items."""
        cutoff = _now() - (TRASH_DAYS if days is None else days) * 86400
        old: list[dict] = []
        for r in self.store.q("SELECT * FROM library_items WHERE storage='trash'"):
            d = dict(r)
            d["meta"] = json.loads(d["meta"] or "{}") or {}
            if ((d["meta"].get("trash") or {}).get("at") or 0) > cutoff:
                continue
            old.append(d)
        self._purge_rows(old)
        return len(old)

    def use(self, a: Actor, iid, body: dict | None = None) -> dict:
        """Attach an item to a new post. With a prompt it starts the agent run right away (make_post); without, it
        returns the media token the Make a post screen (or make_post) takes."""
        body = body or {}
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be attached in the sample workspace; upload a file in Make a post instead.", 403)
        d = self._row(a, iid)
        tok = (d["meta"].get("tokens") or [d["token"]])[0]
        out = {"item": self._public(d), "media_token": tok, "type": "video" if d["kind"] == "video" else "photo"}
        if body.get("prompt"):
            self.h.studio._guard_write(a, "use_library_item")
            out["run"] = self.h.brain.start(a, {"prompt": body["prompt"], "media_token": tok, "channel_ids": body.get("channel_ids"),
                                                "format": body.get("format")})
        return out


    # ---------------------------------------------------------------- Create draft (owner ask, 3 Nov 2026)
    def draft_prefill(self, a: Actor, iid) -> dict:
        """What "Create draft" opens with: the item's media and, per channel, the caption (and options) its run wrote, so a
        draft can be saved without re-opening the run. Channels the run posted to (with this media) start ticked."""
        d = self._row(a, iid)
        runs = self._runs(a)
        pub = self._public(d, runs)
        toks = d["meta"].get("tokens") or [d["token"]]
        rid = pub.get("run_id") or next((u["run_id"] for u in pub["used_in"]), None)
        run = next((r for r in runs if r["run_id"] == rid), None)
        pkgs = {p.get("channel_id"): p for p in ((run or {}).get("_res") or {}).get("packages") or [] if isinstance(p, dict)}

        def uses_item(p):
            blob = json.dumps(p.get("media_urls") or []) + json.dumps(p.get("media") or [])
            return any(t in blob for t in toks)
        mine = [c for c, p in pkgs.items() if uses_item(p)] or list(pkgs)
        chans = []
        for c in self.h.studio.channels(a):
            if c.get("isDisconnected"):
                continue
            p = pkgs.get(c["id"]) or {}
            chans.append({"channel_id": c["id"], "service": c.get("service"), "name": c.get("displayName") or c.get("name"),
                          "text": p.get("text") or "", "options": p.get("options") or {}, "from_run": bool(p),
                          "selected": c["id"] in mine})
        media = [{"url": self.h.media_url(t), "type": "video" if d["kind"] == "video" else "photo"} for t in toks]
        return {"item": pub, "run_id": rid, "media": media, "media_urls": [m["url"] for m in media], "channels": chans,
                "default_text": next((p.get("text") for p in pkgs.values() if p.get("text")), "")}

    def draft_from_library(self, a: Actor, iid, body: dict) -> dict:
        """Save the item as a Buffer DRAFT on each picked channel, through the same preview -> submit path as every other
        composer (preflight, media reach check, agent policy). body: {channels: [{channel_id, text, options?}], dry_run?}.
        Never publishes; one channel's refusal doesn't stop the others."""
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be drafted in the sample workspace. Connect Buffer to use your Library.", 403)
        want = body.get("channels") or [{"channel_id": c} for c in (body.get("channel_ids") or [])]
        if not want or not isinstance(want, list):
            raise ActionError("Pick at least one channel.")
        if len(want) > 20:
            raise ActionError("Up to 20 channels at a time.")
        pre = self.draft_prefill(a, iid)
        known = {c["channel_id"]: c for c in pre["channels"]}
        out = []
        for w in want:
            cid = str((w or {}).get("channel_id") or "")
            c = known.get(cid)
            if not c:
                out.append({"channel_id": cid, "ok": False, "error": "That channel isn't connected."})
                continue
            text = str(w.get("text") if w.get("text") is not None else (c["text"] or pre["default_text"]))
            opts = w.get("options") if isinstance(w.get("options"), dict) else c["options"]
            try:
                prev = self.h.studio.preview_post(a, {"channel_id": cid, "text": text, "media_urls": pre["media_urls"], "mode": "draft",
                                                      "options": opts or {}})
                if body.get("dry_run"):
                    out.append({"channel_id": cid, "ok": True, "preview": prev["preview"], "warnings": prev["warnings"]})
                    continue
                res = self.h.studio.submit_post(a, prev["confirm_token"])
                out.append({"channel_id": cid, "ok": True, "status": res.get("status"), "mode": res.get("mode") or "draft",
                            "buffer_post_id": (res.get("post") or {}).get("id"), "approval_id": res.get("approval_id"),
                            "warnings": prev["warnings"]})
            except (ActionError, BufferError) as e:
                out.append({"channel_id": cid, "ok": False, "error": str(e)})
        return {"item_id": pre["item"]["id"], "run_id": pre["run_id"], "results": out, "saved": sum(1 for r in out if r["ok"])}


# ---------------------------------------------------------------- REST + MCP
TOOLS = [
    {"name": "list_library", "description": "The person's Library: every uploaded photo/video and every image, carousel and Reel the agent made, "
                                            "with type, size, date and which posts used each one. q searches names, titles and post titles; kind: video | image | carousel | upload | generated. "
                                            "Filters: type video|carousel|image|audio, created_by agent|you|sample|one_take (what One take made: its clips and the videos uploaded to it), used_in used|unused|draft|scheduled|sent, "
                                            "run <run id>, date 1d|7d|30d|90d|365d; facets has per-type counts and the runs behind the items.",
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "kind": {"type": "string"},
                                                                    "type": {"type": "string", "enum": ["all", "video", "carousel", "image", "audio"]},
                                                                    "created_by": {"type": "string", "enum": ["all", "agent", "you", "sample", "one_take"]},
                                                                    "used_in": {"type": "string", "enum": ["all", "used", "unused", "draft", "scheduled", "sent"]},
                                                                    "run": {"type": "string", "description": "a run id: items it made or that its posts use"},
                                                                    "date": {"type": "string", "enum": ["all", "1d", "7d", "30d", "90d", "365d"]},
                                                                    "offset": {"type": "integer"}, "per": {"type": "integer", "description": "page size (1-200); total is the count before paging"}}}},
    {"name": "delete_library_item", "description": "Delete one Library item. mode=bin (default) moves it to the Bin for 7 days (restore_library_items brings it back); "
                                                  "mode=permanent removes the file and its record now and can't be undone. Refused with a warning (409) while a post that hasn't gone out "
                                                  "or a post scheduled in Buffer uses it; force=true goes ahead, except that a permanent delete of media a scheduled post uses is always refused.",
     "inputSchema": {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}, "force": {"type": "boolean"},
                                                                             "mode": {"type": "string", "enum": ["bin", "permanent"]}}}},
    {"name": "bulk_delete_library", "description": "Delete several Library items at once (up to 200): mode=bin (default, restorable for 7 days) or mode=permanent (can't be undone). "
                                                  "Each item is checked like a single delete; the ones a post needs come back under refused with the reason (needs_force: force=true would go ahead).",
     "inputSchema": {"type": "object", "required": ["ids"], "properties": {"ids": {"type": "array", "items": {"type": "integer"}},
                                                                         "mode": {"type": "string", "enum": ["bin", "permanent"]}, "force": {"type": "boolean"}}}},
    {"name": "list_library_bin", "description": "The Library Bin: items deleted in the last 7 days, newest first, with days_left before each is removed for good and the posts that used it.",
     "inputSchema": {"type": "object", "properties": {"offset": {"type": "integer"}, "per": {"type": "integer"}}}},
    {"name": "restore_library_items", "description": "Restore items from the Bin to the Library (their files come back for every post that used them).",
     "inputSchema": {"type": "object", "required": ["ids"], "properties": {"ids": {"type": "array", "items": {"type": "integer"}}}}},
    {"name": "purge_library_bin", "description": "Delete items in the Bin permanently (ids, or all=true to empty the Bin). Can't be undone. Media a post scheduled in Buffer uses is refused.",
     "inputSchema": {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "integer"}}, "all": {"type": "boolean"}}}},
    {"name": "draft_from_library", "description": "Save a Library item as a Buffer DRAFT (never published) on the channels you pick, with its media and the per-channel captions its run wrote. "
                                                 "Without channels it returns the prefill (media, channels with captions, run_id) to choose from. channels: [{channel_id, text?}] (text defaults to the run's caption); dry_run=true previews only.",
     "inputSchema": {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}, "dry_run": {"type": "boolean"},
                                                                             "channels": {"type": "array", "items": {"type": "object", "required": ["channel_id"],
                                                                                          "properties": {"channel_id": {"type": "string"}, "text": {"type": "string"}}}}}}},
    {"name": "use_library_item", "description": "Use a Library item in a new post. With prompt (what to post, which channels) it starts make_post with the file attached and returns the run; without, it returns the media_token to pass to make_post.",
     "inputSchema": {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}, "prompt": {"type": "string"},
                                                                             "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                                             "format": {"type": "string", "enum": ["auto", "reel", "carousel", "photo"]}}}},
]
READ_ONLY = {"list_library", "list_library_bin"}


def call_tool(studio, actor, name: str, args: dict):
    lib = studio.house.library
    if name == "list_library":
        return lib.list(actor, str(args.get("q") or ""), str(args.get("kind") or ""), offset=int(args.get("offset") or 0),
                        per=int(args.get("per") or 0), **{k: str(args.get(k) or "") for k in ("type", "created_by", "used_in", "run", "date")})
    if name == "delete_library_item":
        return lib.delete(actor, args.get("item_id"), bool(args.get("force")), str(args.get("mode") or "bin"))
    if name == "bulk_delete_library":
        return lib.bulk_delete(actor, args.get("ids"), str(args.get("mode") or "bin"), bool(args.get("force")))
    if name == "list_library_bin":
        return lib.trash(actor, int(args.get("offset") or 0), int(args.get("per") or 0))
    if name == "restore_library_items":
        return lib.restore_many(actor, args.get("ids"))
    if name == "purge_library_bin":
        return lib.purge(actor, args.get("ids"), bool(args.get("all")))
    if name == "draft_from_library":
        if not args.get("channels"):
            return lib.draft_prefill(actor, args.get("item_id"))
        return lib.draft_from_library(actor, args.get("item_id"), {"channels": args.get("channels"), "dry_run": bool(args.get("dry_run"))})
    if name == "use_library_item":
        return lib.use(actor, args.get("item_id"), {k: args.get(k) for k in ("prompt", "channel_ids", "format") if args.get(k)})
    return NotImplemented


def etag_for(digest: str | None, token: str, size: int) -> str:
    """A strong validator for a stored file: its sha256 when the row has one, else its token and length (a token's bytes
    are never replaced by different content of the same length: a rewrite only blanks metadata at upload, before serving)."""
    raw = digest or f"{token}-{int(size or 0)}"
    return '"' + hashlib.sha256(raw.encode()).hexdigest()[:32] + '"'


def media_file(store, token: str) -> dict | None:
    """A ``media`` row as a ranged reader: type, length and ETag, with only the asked bytes read (SQL substr for rows kept
    in the database, a ranged GET for objects in R2) and a whole answer streamed in CHUNK pieces. The blob is never loaded
    whole: a player's every Range request used to read the full video into memory, which Render killed for exceeding
    512 MB (5 Nov 2026, 08:10 and 11:31 GMT)."""
    if not token:
        return None
    rows = store.q("SELECT user_id, mime, storage_key, sha256, size, length(data) AS n FROM media WHERE token=?", (token,))
    if not rows:
        return None
    r = rows[0]
    key = r["storage_key"] or None
    has_db_copy = bool(r["n"])
    size = int((r["size"] if key else r["n"]) or r["size"] or 0)

    def read(s: int, e: int | None) -> bytes:
        e = size - 1 if e is None else min(int(e), size - 1)
        if size <= 0 or s > e:
            return b""
        try:
            if key and (blobstore.r2_readable() or not has_db_copy):
                return blobstore.read(None, key, s, e)
            # no key, or a copy-only row read while R2 is off or past its read cap: the database copy, by range
            got = store.q("SELECT substr(data, CAST(? AS INTEGER), CAST(? AS INTEGER)) AS d FROM media WHERE token=?", (int(s) + 1, e - int(s) + 1, token))
        except blobstore.StorageError as ex:
            raise ActionError(str(ex), 503)
        return bytes(got[0]["d"] or b"") if got else b""

    def it():
        for s in range(0, size, CHUNK):
            yield read(s, min(s + CHUNK, size) - 1)
    # read through the instance's disk cache: the bytes leave the database once, then every request reads the copy
    # (Supabase egress, 5 Nov 2026: 30 GB against a 5 GB free plan, studio/mediacache.py)
    return mediacache.wrap(store, {"mime": r["mime"], "size": size, "read": read, "iter": it, "owner": r["user_id"], "r2_key": key,
                                   "etag": etag_for(r["sha256"], token, size)}, token, "media")


def not_modified(request, etag: str) -> bool:
    """If-None-Match matches this ETag (a list, W/ prefixes and * accepted, RFC 9110 13.1.2): answer 304."""
    inm = (request.headers.get("if-none-match") or "").strip()
    if not inm or not etag:
        return False
    if inm == "*":
        return True
    tags = {t.strip().removeprefix("W/") for t in inm.split(",")}
    return etag in tags


def parse_range(header: str, size: int) -> tuple[int, int] | None:
    """'bytes=a-b' | 'bytes=a-' | 'bytes=-n' -> (start, end) inclusive, or None when absent/unsatisfiable."""
    if not header.startswith("bytes=") or "," in header or size <= 0:
        return None
    a_, _, b_ = header[6:].strip().partition("-")
    if a_ == "" and b_.isdigit():
        n = int(b_)
        return (max(0, size - n), size - 1) if n else None
    if not a_.isdigit():
        return None
    start = int(a_)
    end = min(int(b_), size - 1) if b_.isdigit() else size - 1
    return (start, end) if start <= end else None


def range_unsatisfiable(header: str, size: int) -> bool:
    """416 only for a single, well-formed byte range that starts past the end (RFC 9110 15.5.17). A multi-range, an
    unknown unit or a malformed header is ignored and the whole file is served with 200, as RFC 9110 14.2 allows: media
    fetchers (Buffer, Instagram, sandboxed agent proxies) must never be refused a plain GET over a Range they sent."""
    if not header.startswith("bytes=") or "," in header or size <= 0:
        return False
    a_, _, b_ = header[6:].strip().partition("-")
    if a_.isdigit():
        return int(a_) >= size
    return a_ == "" and b_ == "0"


NO_STORE = "no-store, max-age=0"
# A /m/ token names one file's bytes for good (a new version is a new token), so a browser keeps it a year and never asks
# again; "private": no shared cache keeps someone's media. A Library file by id can change (a re-render replaces its
# files), so the browser revalidates every time: If-None-Match answers 304 before any byte is read (CodeRabbit, PR #119).
MEDIA_CACHE = "private, max-age=31536000, immutable"
LIB_FILE_CACHE = "private, no-cache"  # an item id can point at new files after a re-render: revalidate (304, no bytes)
SESSION_COOKIE = "hq_sid"  # studio/app.py COOKIE


def if_range_ok(header: str, etag: str) -> bool:
    """If-Range: a Range applies only when the validator is our current strong ETag (a date or weak tag never matches,
    RFC 9110 13.1.5). No If-Range: the Range applies."""
    h = (header or "").strip()
    if not h:
        return True
    return bool(etag) and not h.startswith("W/") and h == etag


def browser_fetch(request) -> bool:
    """A browser's own fetch (it sends Sec-Fetch-* headers); Buffer, Instagram and the render worker don't."""
    return bool(request.headers.get("sec-fetch-mode") or request.headers.get("sec-fetch-dest"))


def add_routes(app, house, actor) -> None:
    lib = house.library

    def serve(f: dict, request: Request, cache: str, viewer_owns: bool | None = None):
        """One media answer: 304 on a matching ETag, 206 for a Range (honouring If-Range), else the whole file streamed.
        Bytes come from the instance's disk cache after the first read (studio/mediacache.py) and are counted in the
        egress meter. Past the daily budget, or after repeated downloads of the same file by one client, a request that
        isn't the owner's gets 429 + Retry-After (studio/egress.py); HEAD, 304 and server-to-server fetchers past the
        budget are never refused."""
        size = f["size"]
        headers = {"Accept-Ranges": "bytes", "Cache-Control": cache, "Content-Disposition": "inline"}
        etag = f.get("etag") or ""
        if etag:
            headers["ETag"] = etag
            if not_modified(request, etag):  # the browser already holds these bytes: no body (bandwidth, 5 Nov 2026)
                return Response(status_code=304, headers=headers)
        rng_h = request.headers.get("range", "")
        if rng_h and not if_range_ok(request.headers.get("if-range", ""), etag):
            rng_h = ""  # If-Range names another version: the whole current file, 200 (RFC 9110 13.1.5)
        refused = _gate(f, request, viewer_owns)
        if refused:
            return refused
        f = _owner_grace(f, request, viewer_owns)
        client = demo_guard.client_ip(request)
        rng = parse_range(rng_h, size)
        if rng is None and range_unsatisfiable(rng_h, size):
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        if rng:
            s, e = rng
            e = min(e, s + 8 * MB - 1)  # one range answer reads at most 8 MB (players ask again for the rest)
            body = f["read"](s, e)
            egress.served(len(body))
            egress.repeat_count(client, etag, len(body))
            return Response(body, status_code=206, media_type=f["mime"], headers={**headers, "Content-Range": f"bytes {s}-{e}/{size}"})
        egress.repeat_count(client, etag, size)
        if f.get("iter"):
            # the first chunk is read BEFORE the headers go out: storage that's down answers a clean 503 (ActionError),
            # not a 200 that breaks mid-download (Greptile, PR #110). Later chunks still stream one at a time.
            it = f["iter"]()
            first = next(it, b"")

            def body():
                if first:
                    egress.served(len(first))
                    yield first
                for b in it:
                    egress.served(len(b))
                    yield b
            return StreamingResponse(body(), media_type=f["mime"], headers={**headers, "Content-Length": str(size)})
        data = f["read"](0, None)
        egress.served(len(data))
        return Response(data, media_type=f["mime"], headers=headers)

    def _owner_grace(f: dict, request: Request, viewer_owns: bool | None) -> dict:
        """Past R2's monthly Class B cap a file kept only in R2 answers 503, except to its owner (signed-in browser),
        whose reads run inside r2usage.owner_grace(): allowed up to the hard ceiling under the free tier."""
        if not r2usage.can_read(grace=False) or not browser_fetch(request):
            return f
        if viewer_owns is None:
            viewer_owns = bool(f.get("owner")) and viewer_uid(request) == f.get("owner")
        if not viewer_owns:
            return f
        read, it = f["read"], f.get("iter")

        def g_read(s, e):
            with r2usage.owner_grace():
                return read(s, e)

        def g_iter():
            src = it()
            while True:
                with r2usage.owner_grace():
                    b = next(src, None)
                if b is None:
                    return
                yield b
        return {**f, "read": g_read, "iter": g_iter if it else None}

    def _gate(f: dict, request: Request, viewer_owns: bool | None):
        """429 for a browser fetch that isn't the owner's, past the day's budget or repeating the same download."""
        if not browser_fetch(request):  # Buffer, Instagram, the render worker: never slowed (publishing must not break)
            return None
        etag, size = f.get("etag") or "", int(f.get("size") or 0)
        repeat = egress.repeat_blocked(demo_guard.client_ip(request), etag, size)
        budget = egress.over_budget()
        if not (repeat or budget):
            return None
        if viewer_owns is None:
            viewer_owns = bool(f.get("owner")) and viewer_uid(request) == f.get("owner")
        if viewer_owns:
            return None
        egress.note_refused("repeat" if repeat else "budget")
        wait = 60 if repeat else egress.retry_after()
        msg = ("This file was just downloaded several times from your network. It loads again in a few minutes." if repeat
               else "Media previews are paused for today to stay inside the free plan's bandwidth. They're back tomorrow (UTC).")
        return JSONResponse({"error": msg}, status_code=429, headers={"Retry-After": str(wait), "Cache-Control": "no-store"})

    def viewer_uid(request: Request) -> str:
        """The signed-in person behind a browser request (session cookie only; never raises, never audits)."""
        try:
            sess = house.store.session(request.cookies.get(SESSION_COOKIE))
            return str(sess["user_id"] or "") if sess else ""
        except Exception:  # noqa: BLE001 - a bad cookie is just "not the owner"
            return ""

    @app.get("/api/library")
    def library_list(request: Request, response: Response, q: str = "", kind: str = "", offset: int = 0, per: int = 0, type: str = "",
                     created_by: str = "", used_in: str = "", run: str = "", date: str = ""):
        response.headers["Cache-Control"] = NO_STORE  # never a cached list: a purged item must not come back on refresh
        return lib.list(actor(request), q, kind, offset=offset, per=per, type=type, created_by=created_by, used_in=used_in,
                        run=run, date=date)

    @app.post("/api/library/upload")
    async def library_upload(request: Request, name: str = "", source: str = "upload"):
        """Raw file body (not JSON, not base64). Authenticated first; Content-Length, the per-file cap and the quota are
        checked before reading; the body is counted as it streams and its type sniffed from the bytes."""
        a = actor(request, write=True)
        house.studio._guard_write(a, "library_upload")
        cl = request.headers.get("content-length")
        try:
            declared = int(cl) if cl is not None else None
        except ValueError:
            raise ActionError("Bad Content-Length.", 400)
        nm = name or request.headers.get("x-file-name", "") or "upload"
        out = await lib.save_stream(a, nm, request.stream(), declared, "take" if source == "take" else "upload")
        return {**out, "usage": lib.usage(a.uid)}  # the meter moves with the upload, no second request

    @app.get("/api/library/trash")
    @app.get("/api/library/bin")
    def library_trash(request: Request, response: Response, offset: int = 0, per: int = 0):
        """The Bin: items deleted in the last TRASH_DAYS days; restore with POST /api/library/{id}/restore."""
        response.headers["Cache-Control"] = NO_STORE
        return lib.trash(actor(request), offset, per)

    @app.get("/api/library/bin/{iid}/file")
    def library_bin_file(iid: int, request: Request, i: int = 0):
        return serve(lib.bin_file(actor(request), iid, i), request, "private, no-store", viewer_owns=True)

    @app.post("/api/library/bin/restore")
    def library_bin_restore(request: Request, body: dict = Body(...)):
        return lib.restore_many(actor(request, write=True), (body or {}).get("ids"))

    @app.post("/api/library/bin/purge")
    def library_bin_purge(request: Request, body: dict = Body(...)):
        b = body or {}
        return lib.purge(actor(request, write=True), b.get("ids"), bool(b.get("all")))

    @app.post("/api/library/bulk-delete")
    def library_bulk_delete(request: Request, body: dict = Body(...)):
        b = body or {}
        return lib.bulk_delete(actor(request, write=True), b.get("ids"), str(b.get("mode") or "bin"), bool(b.get("force")))

    @app.post("/api/library/{iid}/restore")
    def library_restore(iid: int, request: Request):
        return lib.restore(actor(request, write=True), iid)

    @app.get("/api/library/{iid}")
    def library_item(iid: int, request: Request):
        return lib.item(actor(request), iid)

    @app.get("/api/library/{iid}/file")
    def library_file(iid: int, request: Request, i: int = 0):
        return serve(lib.file(actor(request), iid, i), request, LIB_FILE_CACHE, viewer_owns=True)  # lib.file checked ownership

    @app.delete("/api/library/{iid}")
    def library_delete(iid: int, request: Request, force: int = 0, mode: str = "bin"):
        return lib.delete(actor(request, write=True), iid, bool(force), mode)

    @app.get("/api/library/{iid}/draft")
    def library_draft_prefill(iid: int, request: Request):
        return lib.draft_prefill(actor(request), iid)

    @app.post("/api/library/{iid}/draft")
    def library_draft(iid: int, request: Request, body: dict = Body(...)):
        return lib.draft_from_library(actor(request, write=True), iid, body or {})

    @app.post("/api/library/{iid}/use")
    def library_use(iid: int, request: Request, body: dict = Body(default={})):
        return lib.use(actor(request, write=True), iid, body or {})

    app.state.library_serve = serve
