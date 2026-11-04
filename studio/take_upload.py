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
import hmac
import os
import re
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx
from fastapi import Body, Request

from .actions import ActionError, Actor

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
  id TEXT PRIMARY KEY, user_id TEXT, name TEXT, size INTEGER, part_size INTEGER, parts INTEGER, status TEXT,
  object_key TEXT, s3_upload TEXT, take_id INTEGER, created REAL, updated REAL);
"""


def max_bytes() -> int:
    return int(os.environ.get("HQ_TAKE_UPLOAD_MAX_MB", "4096")) * MB


def config() -> dict | None:
    """The bucket, or None (feature off) unless all four settings are there."""
    acct, key, secret, bucket = (os.environ.get(k, "").strip() for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))
    if not (acct and key and secret and bucket):
        return None
    endpoint = os.environ.get("R2_ENDPOINT", "").strip().rstrip("/") or f"https://{acct}.r2.cloudflarestorage.com"
    if not endpoint.startswith("https://"):
        return None
    return {"endpoint": endpoint, "key": key, "secret": secret, "bucket": bucket, "region": os.environ.get("R2_REGION", "auto")}


def presign(cfg: dict, method: str, key: str, params: dict | None = None, expires: int = URL_TTL, now: datetime | None = None) -> str:
    """AWS Signature V4 query-string auth (path-style), UNSIGNED-PAYLOAD. Stdlib only."""
    t = now or datetime.now(timezone.utc)
    amz, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    host = urlparse(cfg["endpoint"]).netloc
    uri = "/" + quote(cfg["bucket"], safe="") + "/" + quote(key, safe="/~")
    scope = f"{day}/{cfg['region']}/s3/aws4_request"
    q = {**(params or {}), "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{cfg['key']}/{scope}",
         "X-Amz-Date": amz, "X-Amz-Expires": str(int(expires)), "X-Amz-SignedHeaders": "host"}
    qs = "&".join(f"{quote(str(k), safe='-_.~')}={quote(str(v), safe='-_.~')}" for k, v in sorted(q.items()))
    creq = "\n".join([method, uri, qs, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"])
    sts = "\n".join(["AWS4-HMAC-SHA256", amz, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = ("AWS4" + cfg["secret"]).encode()
    for part in (day, cfg["region"], "s3", "aws4_request"):
        k = hmac.new(k, part.encode(), hashlib.sha256).digest()
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f"{cfg['endpoint']}{uri}?{qs}&X-Amz-Signature={sig}"


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
            root = ET.fromstring(r.content)
            for el in root.iter(f"{S3NS}Part"):
                out[int(el.findtext(f"{S3NS}PartNumber"))] = (_etag(el.findtext(f"{S3NS}ETag")), int(el.findtext(f"{S3NS}Size") or 0))
            if (root.findtext(f"{S3NS}IsTruncated") or "false").lower() != "true":
                break
            marker = root.findtext(f"{S3NS}NextPartNumberMarker")
        return out

    # ------------------------------------------------------------------ helpers
    def _row(self, a: Actor, upload_id) -> dict:
        """Scope before existence: a missing id and someone else's id are the same 404."""
        if not isinstance(upload_id, str) or not re.fullmatch(r"tu_[A-Za-z0-9_-]{8,40}", upload_id):
            raise ActionError("No such upload.", 404)
        rows = self.store.q("SELECT * FROM take_uploads WHERE id=? AND user_id=?", (upload_id, a.uid))
        if not rows:
            raise ActionError("No such upload.", 404)
        return dict(rows[0])

    def _out(self, d: dict, have: dict | None = None) -> dict:
        return {"upload_id": d["id"], "name": d["name"], "size": d["size"], "part_size": d["part_size"], "parts": d["parts"],
                "status": d["status"], "max_bytes": max_bytes(),
                **({"received": [{"part_number": n, "etag": e, "size": s} for n, (e, s) in sorted(have.items())]} if have is not None else {})}

    def _drop(self, d: dict) -> None:
        try:
            if d["status"] == "open" and d["s3_upload"]:
                self._s3("DELETE", d["object_key"], {"uploadId": d["s3_upload"]})
            else:
                self._s3("DELETE", d["object_key"])
        except ActionError as e:
            print(f"take upload: store cleanup of {d['id']} failed: {e}", flush=True)
        self.store.x("DELETE FROM take_uploads WHERE id=?", (d["id"],))

    def purge_stale(self, now: float | None = None) -> int:
        if not config():
            return 0
        cut = (now or time.time()) - TTL_OPEN
        old = [dict(r) for r in self.store.q("SELECT * FROM take_uploads WHERE take_id IS NULL AND updated<?", (cut,))]
        for d in old:
            self._drop(d)
        return len(old)

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
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ActionError("size is the file's size in bytes (a whole number).")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in VIDEO_EXT or (ctype and ctype not in VIDEO_TYPES):
            raise ActionError("One take reads MP4, MOV or WebM videos.", 415)
        if size > max_bytes():
            raise ActionError(f"Videos up to {max_bytes() // MB:,} MB. This one is {size / MB:,.0f} MB: export a shorter cut "
                              "or a smaller file (1080p H.264 is plenty).", 413)
        self.purge_stale()
        same = self.store.q("SELECT * FROM take_uploads WHERE user_id=? AND name=? AND size=? AND status='open' AND take_id IS NULL "
                            "ORDER BY created DESC", (a.uid, name, size))
        if same:  # the same file again: resume where it stopped, with what the store already has
            d = dict(same[0])
            try:
                return {**self._out(d, self._list_parts(d)), "resumed": True}
            except ActionError:
                self._drop(d)
        uid_ = "tu_" + secrets.token_urlsafe(18)
        key = f"takes/{hashlib.sha256(a.uid.encode()).hexdigest()[:16]}/{uid_}.{ext}"
        r = self._s3("POST", key, {"uploads": ""}, headers={"content-type": ctype or "video/mp4"})
        if r.status_code >= 300:
            raise ActionError(f"The upload store refused a new upload (HTTP {r.status_code}).", 502)
        s3id = ET.fromstring(r.content).findtext(f"{S3NS}UploadId") or ""
        if not s3id:
            raise ActionError("The upload store didn't return an upload id.", 502)
        now = time.time()
        self.store.x("INSERT INTO take_uploads(id,user_id,name,size,part_size,parts,status,object_key,s3_upload,take_id,created,updated) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uid_, a.uid, name, size, PART, -(-size // PART), "open", key, s3id, None, now, now))
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
            {"part_number": n, "size": min(d["part_size"], d["size"] - (n - 1) * d["part_size"]),
             "url": presign(cfg, "PUT", d["object_key"], {"partNumber": str(n), "uploadId": d["s3_upload"]}, URL_TTL)} for n in nums]}

    def status(self, a: Actor, upload_id) -> dict:
        d = self._row(a, upload_id)
        return self._out(d, self._list_parts(d) if d["status"] == "open" else None)

    def complete(self, a: Actor, upload_id, body: dict | None = None) -> dict:
        d = self._row(a, upload_id)
        if d["status"] == "ready":
            return self._out(d)
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
        h = self._s3("HEAD", d["object_key"])
        if h.status_code >= 300 or int(h.headers.get("content-length") or -1) != d["size"]:
            self._drop({**d, "status": "ready"})
            raise ActionError("The stored file isn't the size you sent. Upload it again.", 422)
        g = self._s3("GET", d["object_key"], headers={"range": "bytes=0-15"})
        if not is_video(g.content[:16]):
            self._drop({**d, "status": "ready"})
            raise ActionError("That file isn't a video One take can read (MP4, MOV or WebM).", 415)
        self.store.x("UPDATE take_uploads SET status='ready', updated=? WHERE id=?", (time.time(), d["id"]))
        self.store.audit(a.uid, a.label, "upload_take_source", "write", {"bytes": d["size"], "parts": d["parts"]})
        return {**self._out(self._row(a, upload_id)), "next": "Start the take: clips_from_video(upload_id=...), POST /api/takes with upload_id."}

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
        self.check_ready(a, upload_id)
        self.store.x("UPDATE take_uploads SET take_id=?, updated=? WHERE id=? AND take_id IS NULL", (rid, time.time(), upload_id))

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


def add_routes(app, house, actor) -> None:
    up = house.take_uploads

    @app.get("/api/takes/uploads/config")
    def take_upload_config(request: Request):
        """How One take takes a file here: multipart (direct to the bucket, up to max_bytes) or the Library fallback."""
        return up.info(actor(request))

    @app.post("/api/takes/uploads")
    def take_upload_create(request: Request, body: dict = Body(default={})):
        """upload_take_source: open (or resume) a multipart upload of a long video, straight to the bucket."""
        return up.create(actor(request, write=True), body or {})

    @app.get("/api/takes/uploads/{upload_id}")
    def take_upload_status(upload_id: str, request: Request):
        return up.status(actor(request), upload_id)

    @app.post("/api/takes/uploads/{upload_id}/parts")
    def take_upload_parts(upload_id: str, request: Request, body: dict = Body(default={})):
        return up.part_urls(actor(request, write=True), upload_id, (body or {}).get("parts"))

    @app.post("/api/takes/uploads/{upload_id}/complete")
    def take_upload_complete(upload_id: str, request: Request, body: dict = Body(default={})):
        return up.complete(actor(request, write=True), upload_id, body or {})

    @app.delete("/api/takes/uploads/{upload_id}")
    def take_upload_abort(upload_id: str, request: Request):
        return up.abort(actor(request, write=True), upload_id)
