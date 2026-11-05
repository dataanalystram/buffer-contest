"""Large One take sources: resumable multipart uploads straight from the browser to S3-compatible storage (Cloudflare R2).

Owner bug, 4 Nov 2026 ("One take won't accept more than ~1500 MB"). What actually limited it:
* a file upload on the One take screen was blocked at 40 MB in the browser (take.js), sent as ONE base64 JSON body to
  ``/api/media`` (56 MB body cap, ``house.MAX_UPLOAD`` 40 MB) and kept as a row in the database (Supabase free: 500 MB in all)
  by a free Render instance with 512 MB of RAM and no disk (Render API, 4 Nov 2026: plan free, disk null);
* a pasted link was streamed by the worker but refused past ``HQ_MAX_TAKE_BYTES`` (1500 MB by default): the ~1500 MB.

So big files never touch the web instance. When the operator configures a bucket (``R2_ACCOUNT_ID``, ``R2_ACCESS_KEY_ID``,
``R2_SECRET_ACCESS_KEY``, ``R2_BUCKET``; ``R2_ENDPOINT`` for another S3-compatible store) the browser uploads 16 MiB parts
directly to the bucket with presigned URLs; this server only signs (SigV4, query auth), then on complete checks the parts'
sizes and ETags against the store's own ListParts, completes the upload, checks the object's length and that its first
bytes are a video container. The worker downloads it from a presigned GET (streamed, size-capped). Without a bucket the
feature is off and One take's file uploads go through the raw Library upload (100 MB, no base64).

Resume: opening the same file again (same name and size) returns the unfinished upload and the parts the store already
has; the browser keeps going from there. Cap: ``HQ_TAKE_UPLOAD_MAX_MB`` (4096 by default: R2's free tier is 10 GB and a
GitHub runner has ~14 GB of disk for the source, its WAV and the renders).

Lifecycle: an unfinished upload is aborted after a day, a finished one no take uses is deleted after a day, and a take's
source is deleted when the take is purged from the Bin. Also add a bucket lifecycle rule deleting ``takes/`` objects after
7 days and aborting incomplete multipart uploads after 1 day (docs/ONE_TAKE_UPLOADS.md), so nothing outlives a missed sweep.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import threading
import time
import xml.etree.ElementTree as ET

import httpx
from .actions import ActionError, Actor
from .r2 import config, presign  # noqa: F401  the one R2 signer and settings (shared with studio/blobstore.py, PR #110)

MB = 1024 * 1024
PART = 16 * MB              # every part but the last (S3 / R2 need >= 5 MiB, all the same size)
MAX_PARTS = 10000
URL_TTL = 3600              # a part's presigned PUT
GET_TTL = 6 * 3600          # the worker's presigned GET
TTL_OPEN = 24 * 3600
FALLBACK_MAX = 100 * MB     # the raw Library upload when no bucket is configured (library.FILE_MAX)
VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm", "video/x-m4v", "video/x-matroska"}
VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "mkv"}
S3NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS take_uploads (
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, size BIGINT, part_size INTEGER, parts INTEGER, status TEXT,
  object_key TEXT, s3_upload TEXT, take_id INTEGER, created REAL, updated REAL, fingerprint TEXT);
"""
# status: open (parts arriving) -> joined (the store joined the parts; the checks are next) -> ready (a take can use it).
# del_open / del_ready: the store didn't confirm a delete; the record stays so the sweep retries it (never forgotten).
_CLAIM = threading.Lock()


def max_bytes() -> int:
    return int(os.environ.get("HQ_TAKE_UPLOAD_MAX_MB", "4096")) * MB


def is_video(head: bytes) -> bool:
    """MP4 / MOV / M4V (ISO BMFF or QuickTime atoms) or WebM / MKV (EBML)."""
    return (len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")) or head[:4] == b"\x1a\x45\xdf\xa3"


def _etag(v: str) -> str:
    return str(v or "").strip().strip('"').lower()


def ensure(store) -> None:
    if getattr(store, "_take_uploads_ready", False):
        return
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        store.x(stmt.replace(" REAL", " DOUBLE PRECISION") if store.pg else stmt)
    if store.pg:
        store.x("ALTER TABLE take_uploads ENABLE ROW LEVEL SECURITY")
        store.x("ALTER TABLE take_uploads ALTER COLUMN size TYPE BIGINT")  # 4 GB doesn't fit INTEGER (Greptile, PR #107)
        store.x("ALTER TABLE take_uploads ADD COLUMN IF NOT EXISTS fingerprint TEXT")
    else:
        try:
            store.x("ALTER TABLE take_uploads ADD COLUMN fingerprint TEXT")
        except Exception:  # noqa: BLE001 - already there (SQLite has no ADD COLUMN IF NOT EXISTS)
            pass
    store._take_uploads_ready = True


class TakeUploads:
    def __init__(self, house):
        self.h, self.store, self.s = house, house.store, house.s
        self.http: httpx.Client | None = None  # tests swap in a stub S3; else the app's shared client
        ensure(self.store)

    # ------------------------------------------------------------------ store calls (operator-configured host only)
    def _cfg(self) -> dict:
        cfg = config()
        if not cfg:
            raise ActionError(f"Large uploads aren't set up on this server, so videos up to {FALLBACK_MAX // MB} MB go through the "
                              "Library upload. For a longer talk, paste a direct link to the file.", 503)
        return cfg

    def _s3(self, method: str, key: str, params: dict | None = None, content: bytes | None = None, headers: dict | None = None):
        cfg = self._cfg()
        c = self.http or self.h.http
        try:
            r = c.request(method, presign(cfg, method, key, params, 300), content=content, headers=headers or {}, timeout=60)
        except httpx.HTTPError as e:
            raise ActionError(f"The upload store didn't answer ({e.__class__.__name__}). Try again in a minute.", 502)
        return r

    def _list_parts(self, d: dict) -> dict[int, tuple[str, int]]:
        out, marker = {}, None
        for _ in range(20):
            p = {"uploadId": d["s3_upload"], **({"part-number-marker": marker} if marker else {})}
            r = self._s3("GET", d["object_key"], p)
            if r.status_code == 404:
                raise ActionError("This upload expired in the store. Start it again.", 410)
            if r.status_code >= 300:
                raise ActionError(f"The upload store refused the part list (HTTP {r.status_code}).", 502)
            try:  # the operator's own store; still, a malformed answer is a clean 502, never a 500
                root = ET.fromstring(r.content)  # noqa: S314 - the configured bucket's answer, not user input
                for el in root.iter(f"{S3NS}Part"):
                    out[int(el.findtext(f"{S3NS}PartNumber"))] = (_etag(el.findtext(f"{S3NS}ETag")), int(el.findtext(f"{S3NS}Size") or 0))
            except (ET.ParseError, TypeError, ValueError):
                raise ActionError("The upload store sent a part list this server can't read.", 502)
            if (root.findtext(f"{S3NS}IsTruncated") or "false").lower() != "true":
                break
            marker = root.findtext(f"{S3NS}NextPartNumberMarker")
        return out

    # ------------------------------------------------------------------ helpers
    def _row(self, a: Actor, upload_id) -> dict:
        """Scope before existence: a missing id and someone else's id are the same 404."""
        if not isinstance(upload_id, str) or not re.fullmatch(r"tu_[A-Za-z0-9_-]{8,40}", upload_id):
            raise ActionError("No such upload.", 404)
        rows = self.store.q("SELECT * FROM take_uploads WHERE id=? AND user_id=? AND status NOT IN ('del_open', 'del_ready')", (upload_id, a.uid))
        if not rows:
            raise ActionError("No such upload.", 404)
        return dict(rows[0])

    def _out(self, d: dict, have: dict | None = None) -> dict:
        return {"upload_id": d["id"], "name": d["name"], "size": d["size"], "part_size": d["part_size"], "parts": d["parts"],
                "status": d["status"], "max_bytes": max_bytes(),
                **({"received": [{"part_number": n, "etag": e, "size": s} for n, (e, s) in sorted(have.items())]} if have is not None else {})}

    def _drop(self, d: dict) -> bool:
        """Delete the upload from the store, then its record. When the store doesn't confirm (timeout, 5xx), the record
        stays as del_open / del_ready, out of every listing, and purge_stale tries again; nothing is forgotten."""
        is_open = d["status"] in ("open", "del_open") and bool(d["s3_upload"])
        try:
            r = self._s3("DELETE", d["object_key"], {"uploadId": d["s3_upload"]} if is_open else None)
            ok = r.status_code < 300 or r.status_code == 404  # 404: already gone (aborted, or expired by the lifecycle rule)
            why = f"HTTP {r.status_code}"
        except ActionError as e:
            ok, why = False, str(e)
        if ok:
            self.store.x("DELETE FROM take_uploads WHERE id=?", (d["id"],))
            return True
        print(f"take upload: store cleanup of {d['id']} failed ({why}); kept for the next sweep", flush=True)
        self.store.x("UPDATE take_uploads SET status=?, take_id=NULL, updated=? WHERE id=?",
                     ("del_open" if is_open else "del_ready", 0, d["id"]))
        return False

    def purge_stale(self, now: float | None = None) -> int:
        if not config():
            return 0
        cut = (now or time.time()) - TTL_OPEN
        n = 0
        for r in self.store.q("SELECT id FROM take_uploads WHERE take_id IS NULL AND updated<?", (cut,)):
            with _CLAIM:  # re-read under the claim lock: an upload a take claimed meanwhile is never deleted (Greptile)
                rows = self.store.q("SELECT * FROM take_uploads WHERE id=? AND take_id IS NULL AND updated<?", (r["id"], cut))
                if not rows:
                    continue
                d = dict(rows[0])
                # Marked for deletion before the lock is let go: claim_for_take only takes a 'ready' row, so no take can
                # claim it now, and the slow bucket DELETE below runs outside the lock every user's take start needs.
                d["status"] = "del_open" if d["status"] in ("open", "del_open") and d["s3_upload"] else "del_ready"
                self.store.x("UPDATE take_uploads SET status=? WHERE id=? AND take_id IS NULL", (d["status"], d["id"]))
            n += self._drop(d)
        return n

    def remove_for_user(self, uid: str) -> int:
        """Account deletion (/api/disconnect): every upload of this person leaves the store. Best effort: the records go
        with the account either way (store.OWNED_ELSEWHERE) and the bucket's lifecycle rule removes anything left."""
        if not config():
            return 0
        rows = [dict(r) for r in self.store.q("SELECT * FROM take_uploads WHERE user_id=?", (uid,))]
        return sum(1 for d in rows if self._drop(d))

    # ------------------------------------------------------------------ API
    def info(self, a: Actor) -> dict:
        on = bool(config())
        return {"multipart": on, "max_bytes": max_bytes() if on else FALLBACK_MAX, "part_size": PART,
                "fallback": None if on else {"path": "/api/library/upload?source=take", "max_bytes": FALLBACK_MAX}}

    def create(self, a: Actor, body: dict) -> dict:
        self.h.studio._guard_write(a, "upload_take_source")
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace runs One take on the bundled sample talk. Connect Buffer to upload your own video.", 403)
        self._cfg()
        name = re.sub(r"[^\w .()-]", "_", str(body.get("name") or "video.mp4"))[:120].strip() or "video.mp4"
        size, ctype = body.get("size"), str(body.get("content_type") or "").lower().split(";")[0].strip()
        # The browser's file identity (name, size and lastModified). Without it an upload never resumes: the same name and
        # size alone don't make the same video, and joining parts of two files would be silent corruption (Greptile).
        fp = str(body.get("fingerprint") or "").strip()[:120]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ActionError("size is the file's size in bytes (a whole number).")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in VIDEO_EXT or (ctype and ctype not in VIDEO_TYPES):
            raise ActionError("One take reads MP4, MOV or WebM videos.", 415)
        if size > max_bytes():
            raise ActionError(f"Videos up to {max_bytes() // MB:,} MB. This one is {size / MB:,.0f} MB: export a shorter cut "
                              "or a smaller file (1080p H.264 is plenty).", 413)
        self.purge_stale()
        same = self.store.q("SELECT * FROM take_uploads WHERE user_id=? AND name=? AND size=? AND fingerprint=? AND status='open' "
                            "AND take_id IS NULL ORDER BY created DESC", (a.uid, name, size, fp)) if fp else []
        if same:  # the same file again: resume where it stopped, with what the store already has
            d = dict(same[0])
            try:
                return {**self._out(d, self._list_parts(d)), "resumed": True}
            except ActionError as e:
                if e.status != 410:  # a transient store error keeps the upload for the next try (CodeRabbit)
                    raise
                self._drop(d)
        uid_ = "tu_" + secrets.token_urlsafe(18)
        key = f"takes/{hashlib.sha256(a.uid.encode()).hexdigest()[:16]}/{uid_}.{ext}"
        r = self._s3("POST", key, {"uploads": ""}, headers={"content-type": ctype or "video/mp4"})
        if r.status_code >= 300:
            raise ActionError(f"The upload store refused a new upload (HTTP {r.status_code}).", 502)
        try:
            s3id = ET.fromstring(r.content).findtext(f"{S3NS}UploadId") or ""  # noqa: S314 - the configured bucket's answer
        except ET.ParseError:  # a malformed answer is the store's fault: a clean 502, as in _list_parts (CodeRabbit)
            raise ActionError("The upload store sent an answer that isn't XML.", 502) from None
        if not s3id:
            raise ActionError("The upload store didn't return an upload id.", 502)
        now = time.time()
        self.store.x("INSERT INTO take_uploads(id,user_id,name,size,part_size,parts,status,object_key,s3_upload,take_id,created,updated,"
                     "fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (uid_, a.uid, name, size, PART, -(-size // PART), "open", key, s3id, None, now, now, fp or None))
        return {**self._out(self._row(a, uid_), {}), "resumed": False}

    def part_urls(self, a: Actor, upload_id, numbers) -> dict:
        d = self._row(a, upload_id)
        if d["status"] != "open":
            raise ActionError("This upload is already finished.", 409)
        if not isinstance(numbers, list) or not numbers or len(numbers) > 100:
            raise ActionError("parts is a list of 1-100 part numbers.")
        try:
            nums = sorted({int(n) for n in numbers if not isinstance(n, bool)})
        except (TypeError, ValueError):
            raise ActionError("parts is a list of part numbers (1, 2, 3 ...).")
        if not nums or nums[0] < 1 or nums[-1] > d["parts"]:
            raise ActionError(f"Part numbers go from 1 to {d['parts']}.")
        cfg = self._cfg()
        self.store.x("UPDATE take_uploads SET updated=? WHERE id=?", (time.time(), d["id"]))
        return {"upload_id": d["id"], "expires_in": URL_TTL, "urls": [
            {"part_number": n, "size": (ln := min(d["part_size"], d["size"] - (n - 1) * d["part_size"])),
             "url": presign(cfg, "PUT", d["object_key"], {"partNumber": str(n), "uploadId": d["s3_upload"]}, URL_TTL, length=ln)}
            for n in nums]}

    def status(self, a: Actor, upload_id) -> dict:
        d = self._row(a, upload_id)
        return self._out(d, self._list_parts(d) if d["status"] == "open" else None)

    def _check_object(self, d: dict) -> None:
        """After the join: the object's length and its first bytes. A store timeout here leaves the row 'joined', so
        finishing again re-runs only these checks (the multipart upload id is spent once the parts are joined)."""
        h = self._s3("HEAD", d["object_key"])
        if h.status_code >= 500:
            raise ActionError(f"The upload store didn't answer the check (HTTP {h.status_code}). Finish again in a minute.", 502)
        try:
            length = int(h.headers.get("content-length") or -1)
        except ValueError:
            length = -1
        if h.status_code >= 300 or length != d["size"]:
            self._drop({**d, "status": "ready"})
            raise ActionError("The stored file isn't the size you sent. Upload it again.", 422)
        g = self._s3("GET", d["object_key"], headers={"range": "bytes=0-15"})
        if g.status_code >= 500:
            raise ActionError(f"The upload store didn't answer the check (HTTP {g.status_code}). Finish again in a minute.", 502)
        if not is_video(g.content[:16]):
            self._drop({**d, "status": "ready"})
            raise ActionError("That file isn't a video One take can read (MP4, MOV or WebM).", 415)

    def complete(self, a: Actor, upload_id, body: dict | None = None) -> dict:
        d = self._row(a, upload_id)
        if d["status"] == "ready":
            return self._out(d)
        if d["status"] == "joined":  # joined before, the checks didn't finish: run only those
            return self._finish(a, d)
        have = self._list_parts(d)  # the store's own view: sizes and ETags, never just the client's word
        missing = [n for n in range(1, d["parts"] + 1) if n not in have]
        if missing:
            raise ActionError(f"Part {missing[0]} hasn't arrived yet ({len(missing)} missing). Upload the missing parts, then finish.", 409)
        for n in range(1, d["parts"] + 1):
            want = min(d["part_size"], d["size"] - (n - 1) * d["part_size"])
            if have[n][1] != want:
                raise ActionError(f"Part {n} is {have[n][1]} bytes, not {want}. Upload it again.", 422)
        given = {}
        for p in (body or {}).get("parts") or []:
            try:
                given[int(p["part_number"])] = _etag(p["etag"])
            except (TypeError, ValueError, KeyError):
                raise ActionError("parts is a list of {part_number, etag}.")
        bad = [n for n, e in given.items() if n in have and e and e != have[n][0]]
        if bad:
            raise ActionError(f"Part {bad[0]} doesn't match what the store holds (ETag). Upload it again.", 422)
        xml = "<CompleteMultipartUpload>" + "".join(
            f"<Part><PartNumber>{n}</PartNumber><ETag>\"{have[n][0]}\"</ETag></Part>" for n in range(1, d["parts"] + 1)) + "</CompleteMultipartUpload>"
        r = self._s3("POST", d["object_key"], {"uploadId": d["s3_upload"]}, content=xml.encode(), headers={"content-type": "application/xml"})
        if r.status_code >= 300 or b"<Error>" in r.content[:200]:
            raise ActionError(f"The upload store couldn't join the parts (HTTP {r.status_code}). Try finishing again.", 502)
        self.store.x("UPDATE take_uploads SET status='joined', updated=? WHERE id=?", (time.time(), d["id"]))
        return self._finish(a, {**d, "status": "joined"})

    def _finish(self, a: Actor, d: dict) -> dict:
        self._check_object(d)
        self.store.x("UPDATE take_uploads SET status='ready', updated=? WHERE id=?", (time.time(), d["id"]))
        self.store.audit(a.uid, a.label, "upload_take_source", "write", {"bytes": d["size"], "parts": d["parts"]})
        return {**self._out(self._row(a, d["id"])), "next": "Start the take: clips_from_video(upload_id=...), POST /api/takes with upload_id."}

    def abort(self, a: Actor, upload_id) -> dict:
        d = self._row(a, upload_id)
        if d["take_id"]:
            raise ActionError("A take uses this video; delete the take instead (it goes to the Bin).", 409)
        self._drop(d)
        return {"aborted": True, "upload_id": d["id"]}

    # ------------------------------------------------------------------ for takes.py / take_bin.py
    def check_ready(self, a: Actor, upload_id) -> dict:
        d = self._row(a, upload_id)
        if d["status"] != "ready":
            raise ActionError("Finish the upload first (every part, then finish).", 409)
        if d["take_id"]:
            raise ActionError("Another take already uses this upload. Upload the video again for a new take.", 409)
        return d

    def claim_for_take(self, a: Actor, upload_id: str, rid: int) -> None:
        """One upload, one take: the check and the conditional update run under one lock, then the row is read back,
        so of two concurrent starts exactly one wins (the other gets 409 and its run is removed by the caller)."""
        with _CLAIM:
            self.check_ready(a, upload_id)
            self.store.x("UPDATE take_uploads SET take_id=?, updated=? WHERE id=? AND user_id=? AND take_id IS NULL AND status='ready'",
                         (rid, time.time(), upload_id, a.uid))
            got = self.store.q("SELECT take_id FROM take_uploads WHERE id=? AND user_id=?", (upload_id, a.uid))
        if not got or got[0]["take_id"] != rid:
            raise ActionError("Another take already uses this upload. Upload the video again for a new take.", 409)

    def signed_url(self, upload_id: str) -> str:
        rows = self.store.q("SELECT object_key, status FROM take_uploads WHERE id=?", (upload_id,))
        if not rows or rows[0]["status"] != "ready":
            return ""
        return presign(self._cfg(), "GET", rows[0]["object_key"], None, GET_TTL)

    def remove_for_take(self, rid: int, uid: str) -> int:
        rows = [dict(r) for r in self.store.q("SELECT * FROM take_uploads WHERE take_id=? AND user_id=?", (rid, uid))]
        for d in rows:
            self._drop(d)
        return len(rows)
