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
import time
from pathlib import Path

from fastapi import Body, Request
from fastapi.responses import Response, StreamingResponse

from . import demo_guard
from .actions import ActionError, Actor
from .buffer_api import BufferError

MB = 1024 * 1024
CHUNK = MB
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
    store.x("CREATE INDEX IF NOT EXISTS library_items_user ON library_items(user_id, id)")
    store.x("CREATE INDEX IF NOT EXISTS library_items_token ON library_items(token)")
    if store.pg:  # closed to the hosted database's public data API, like every other table
        for t in TABLES:
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def _now() -> float:
    return time.time()


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

    def feed(self, piece: bytes) -> None:
        self.size += len(piece)
        if self.size > self.room:
            raise ActionError(f"Files up to {FILE_MAX // MB} MB, and your Library holds {QUOTA // MB} MB in all.", 413)
        self.buf += piece
        if self.kind is None and len(self.buf) >= 16:
            self.kind = sniff(bytes(self.buf[:32]))
            if not self.kind:
                raise ActionError(not_media(bytes(self.buf[:64])), 415)
        while len(self.buf) >= CHUNK:
            part, self.buf = bytes(self.buf[:CHUNK]), self.buf[CHUNK:]
            self.h.update(part)
            self.lib.store.x("INSERT INTO library_chunks(item_id,n,data) VALUES(?,?,?)", (self.iid, self.n, part))
            self.n += 1

    def finish(self, name: str) -> dict:
        if self.kind is None:
            self.kind = sniff(bytes(self.buf[:32]))
            if not self.kind:
                raise ActionError(not_media(bytes(self.buf[:64])), 415)
        if self.buf:
            self.h.update(bytes(self.buf))
            self.lib.store.x("INSERT INTO library_chunks(item_id,n,data) VALUES(?,?,?)", (self.iid, self.n, bytes(self.buf)))
        return self.lib._finish(self.a, self.iid, self.tok, name, self.kind, self.size, self.h.hexdigest(), self.source)

    def abort(self) -> None:
        self.lib._drop(self.iid)


class Library:
    def __init__(self, house):
        self.h, self.store = house, house.store
        ensure(self.store)

    # ---------------------------------------------------------------- caps
    def used(self, uid: str) -> int:
        return int(self.store.q("SELECT COALESCE(SUM(size),0) AS n FROM library_items WHERE user_id=?", (uid,))[0]["n"] or 0)

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
        iid = self.store.x("INSERT INTO library_items(user_id,token,name,mime,kind,source,size,sha256,storage,meta,run_id,created)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (a.uid, tok, name[:80], "", "", source, 0, "", "pending", "{}", None, _now()))
        return iid, tok

    def _drop(self, iid: int) -> None:
        self.store.x("DELETE FROM library_chunks WHERE item_id=?", (iid,))
        self.store.x("DELETE FROM library_media WHERE item_id=?", (iid,))
        self.store.x("DELETE FROM library_items WHERE id=?", (iid,))

    async def save_stream(self, a: Actor, name: str, stream, declared: int | None, source: str = "upload") -> dict:
        """Stream a request body into chunks: capped while it streams, MIME sniffed from the first bytes, nothing
        decoded. A file that breaks a rule leaves nothing behind."""
        w = _Writer(self, a, declared, source)
        try:
            async for piece in stream:
                w.feed(piece)
            return w.finish(name)
        except BaseException:
            w.abort()
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
        iid, tok = self._new_item(a, source, "upload")
        try:
            for n, i in enumerate(range(0, len(data), CHUNK)):
                self.store.x("INSERT INTO library_chunks(item_id,n,data) VALUES(?,?,?)", (iid, n, data[i:i + CHUNK]))
        except BaseException:
            self._drop(iid)
            raise
        return self._finish(a, iid, tok, name, kind, len(data), hashlib.sha256(data).hexdigest(), source)

    def _finish(self, a, iid, tok, name, kind, size, digest, source) -> dict:
        mime, ext, k = kind
        if not size:
            self._drop(iid)
            raise ActionError("That file is empty.")
        same = self.store.q("SELECT id FROM library_items WHERE user_id=? AND sha256=? AND storage='chunks' AND id<>?", (a.uid, digest, iid))
        if same:  # the same file again: keep the one already there
            self._drop(iid)
            return self.item(a, same[0]["id"])
        tok = f"{tok}.{ext}"
        meta = {"type": "video" if k == "video" else "photo"}
        if k == "image":
            meta.update(self._image_size(iid))
        self.store.x("UPDATE library_items SET token=?, name=?, mime=?, kind=?, size=?, sha256=?, storage='chunks', meta=? WHERE id=?",
                     (tok, safe_name(name, ext), mime, k, size, digest, json.dumps(meta), iid))
        self.store.x("INSERT INTO library_media(token,item_id,user_id) VALUES(?,?,?)", (tok, iid, a.uid))
        self.store.audit(a.uid, a.label, "library_upload", "read", {"kind": k, "bytes": size, "source": source})
        return self.item(a, iid)

    def _image_size(self, iid: int) -> dict:
        try:
            import io

            from PIL import Image
            data = self._read(iid, 0, None)
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
        iid = self.store.x("INSERT INTO library_items(user_id,token,name,mime,kind,source,size,sha256,storage,meta,run_id,created)"
                           " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                           (a.uid, info["tokens"][0], name, first["mime"], kind, "generated", sum(int(by[t]["size"] or 0) for t in info["tokens"]),
                            "", "media", json.dumps(info), run_id, created or _now()))
        for t in info["tokens"]:
            self.store.x("INSERT INTO library_media(token,item_id,user_id) VALUES(?,?,?) ON CONFLICT(token) DO NOTHING", (t, iid, a.uid))
        return iid

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
               "media_token": d["token"], "read_only": False}
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
        for r in self.store.q("SELECT id, kind, input, result, status, created FROM agent_runs WHERE user_id=? ORDER BY id DESC LIMIT 200", (a.uid,)):
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

    def list(self, a: Actor, q: str = "", kind: str = "", limit: int = 200, offset: int = 0, per: int = 0) -> dict:
        """The Library (newest first). ``per``/``offset`` page it (``total`` is the count before paging); the answer also
        carries ``bin_count`` so the Library screen shows its Bin badge without a second request."""
        out = self._list(a, q, kind, limit)
        out["total"] = len(out["items"])
        if per:
            per, offset = max(1, min(int(per), 200)), max(0, int(offset or 0))
            out["items"], out["offset"], out["per"] = out["items"][offset:offset + per], offset, per
        out["bin_count"] = 0 if demo_guard.is_demo(a.uid) else int(self.store.q(
            "SELECT COUNT(*) AS n FROM library_items WHERE user_id=? AND storage='trash'", (a.uid,))[0]["n"] or 0)
        out["bin_days"] = TRASH_DAYS
        return out

    def _list(self, a: Actor, q: str = "", kind: str = "", limit: int = 200) -> dict:
        if demo_guard.is_demo(a.uid):
            items = _sample_items()
            used = 0
        else:
            runs = self._runs(a)
            self.backfill(a, runs)
            rows = self.store.q("SELECT * FROM library_items WHERE user_id=? AND storage NOT IN ('pending','trash') "
                                "ORDER BY created DESC, id DESC LIMIT ?",
                                (a.uid, max(1, min(int(limit or 200), 500))))
            by_run = {r["run_id"]: r for r in runs}
            items = []
            for r in rows:
                d = dict(r)
                d["meta"] = json.loads(d["meta"] or "{}")
                it = self._public(d, runs)
                own = by_run.get(d.get("run_id"))
                if it["kind"] == "video" and own:  # a still for a video card: the run's own picture, else the story's
                    it["thumb"] = own["_thumb"] or ""
                    it["thumb_src"] = own["_thumb_src"]
                items.append(it)
            used = self.used(a.uid)
        if kind in ("video", "image", "carousel"):
            items = [i for i in items if i["kind"] == kind]
        elif kind == "draft":  # Make a post's "Drafts" filter, carried over by "View all"
            items = [i for i in items if any(u.get("draft") for u in i.get("used_in") or [])]
        elif kind in ("upload", "generated"):
            items = [i for i in items if (i["source"] in ("upload", "take", "link", "send")) == (kind == "upload")]
        if q:
            ql = q.lower()
            items = [i for i in items if ql in i["name"].lower() or any(ql in u["title"].lower() for u in i.get("used_in") or [])]
        return {"items": items, "used": used, "quota": QUOTA, "file_max": FILE_MAX, "read_only": demo_guard.is_demo(a.uid)}

    def item(self, a: Actor, iid) -> dict:
        return self._public(self._row(a, iid), self._runs(a))

    def _read(self, iid: int, start: int, end: int | None) -> bytes:
        """Bytes [start, end] of an uploaded item, reading only the chunks that hold them."""
        first = start // CHUNK
        last = (end // CHUNK) if end is not None else None
        sql = "SELECT n, data FROM library_chunks WHERE item_id=? AND n>=?" + (" AND n<=?" if last is not None else "") + " ORDER BY n"
        rows = self.store.q(sql, (iid, first, last) if last is not None else (iid, first))
        data = b"".join(bytes(r["data"]) for r in rows)
        off = start - first * CHUNK
        return data[off:(end - first * CHUNK + 1) if end is not None else None]

    def _iter(self, iid: int):
        """Every chunk in order, one database read at a time (a whole-file answer never holds the file in memory)."""
        n = 0
        while True:
            rows = self.store.q("SELECT data FROM library_chunks WHERE item_id=? AND n=?", (iid, n))
            if not rows:
                return
            yield bytes(rows[0]["data"])
            n += 1

    def file(self, a: Actor, iid, i: int = 0) -> dict:
        """What GET /api/library/{id}/file serves: (mime, size, reader)."""
        d = self._row(a, iid)
        return self._file_of(d, i)

    def _file_of(self, d: dict, i: int = 0) -> dict:
        if d["storage"] == "chunks":
            return {"mime": d["mime"], "size": int(d["size"] or 0), "read": lambda s, e: self._read(d["id"], s, e), "name": d["name"],
                    "iter": lambda: self._iter(d["id"])}
        toks = d["meta"].get("tokens") or [d["token"]]
        tok = toks[max(0, min(int(i or 0), len(toks) - 1))]
        m = self.store.q("SELECT mime, data, size FROM media WHERE token=?", (tok,))
        if not m:
            raise ActionError("This file is gone.", 404)
        blob = bytes(m[0]["data"])
        return {"mime": m[0]["mime"], "size": len(blob), "read": lambda s, e: blob[s:(e + 1) if e is not None else None], "name": d["name"]}

    # ---------------------------------------------------------------- tokens: library uploads work as media tokens
    def by_token(self, token: str) -> dict | None:
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
        return {"token": token, "user_id": d["user_id"], "mime": d["mime"], "data": self._read(d["id"], 0, None),
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
        d = self._row(a, iid)
        return self._delete_row(a, d, self._runs(a), force, mode)

    @staticmethod
    def _uses(d: dict, runs: list[dict], tokens: list[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
        """(every run using the item, the ones not gone out yet, the ones scheduled in Buffer)."""
        toks = [t for t in (tokens or d["meta"].get("tokens") or [d["token"]]) if t]
        used = [r for r in runs if any(t in r["_blob"] for t in toks)]
        live = [u for u in used if not u["sent"] and u["status"] in ("ready", "running", "rendering")]
        sched = [u for u in used if u.get("scheduled")]
        return used, live, sched

    def _delete_row(self, a: Actor, d: dict, runs: list[dict], force: bool, mode: str) -> dict:
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
        if mode == "permanent":
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
        """Remove an item's bytes and record for good (its media rows too, live or binned), and note its tokens as deleted
        by the person, so /healthz doesn't count posts that used them as missing files."""
        t = d["meta"].get("trash") or {}
        toks = [x for x in (t.get("tokens") or d["meta"].get("tokens") or [t.get("token") or d["token"]]) if x]
        toks = [x.removeprefix(TRASH_TOKEN) for x in toks]
        if (t.get("storage") or d["storage"]) == "media":
            for x in toks:
                self.store.x("DELETE FROM media WHERE token IN (?,?) AND user_id=?", (x, TRASH_TOKEN + x, d["user_id"]))
        for x in toks:
            self.store.x("INSERT INTO library_purged(token,user_id,at) VALUES(?,?,?) ON CONFLICT(token) DO NOTHING", (x, d["user_id"], _now()))
        self._drop(d["id"])

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
        ids, runs = self._ids(ids), self._runs(a)
        done, refused = [], []
        for iid in ids:
            try:
                d = self._row(a, iid)
                done.append(self._delete_row(a, d, runs, force, mode)["id"])
            except ActionError as e:
                refused.append({"id": iid, "error": str(e), "status": e.status,
                                "needs_force": e.status == 409 and "can't be deleted permanently" not in str(e)})
        return {"mode": mode, "deleted": done, "refused": refused, "in_trash_days": TRASH_DAYS if mode == "bin" else None}

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
            return {"items": [], "days": TRASH_DAYS, "total": 0}
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
        return {"items": out, "days": TRASH_DAYS, "total": total}

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
        return self._restore_row(a, dict(rows[0]))

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
        rows = {d["id"]: d for d in self._bin_rows(a, ids)}
        done = []
        for iid in ids:
            if iid in rows:
                d = rows[iid]
                self._restore_row(a, {**d, "meta": json.dumps(d["meta"])})
                done.append(iid)
        return {"restored": done, "missing": [i for i in ids if i not in rows]}

    def purge(self, a: Actor, ids=None, everything: bool = False) -> dict:
        """Delete permanently from the Bin: the listed ids, or everything=true (Empty bin). Can't be undone. An item a post
        scheduled in Buffer still uses is refused (Buffer fetches the file again when it publishes)."""
        if demo_guard.is_demo(a.uid):
            raise ActionError("Sample items can't be deleted. Connect Buffer to manage your own Library.", 403)
        self.h.studio._guard_write(a, "purge_library_bin")
        want = None if everything else self._ids(ids)
        rows = self._bin_rows(a, want)[:self.MAX_BULK]
        runs = self._runs(a) if rows else []
        done, refused = [], []
        for d in rows:
            t = d["meta"].get("trash") or {}
            _, _, sched = self._uses(d, runs, t.get("tokens") or [t.get("token")])
            if sched:
                refused.append({"id": d["id"], "status": 409, "needs_force": False,
                                "error": f"“{d['name']}” can't be deleted permanently: it's used in a post scheduled in Buffer "
                                         f"(“{sched[0]['title'][:60]}”), and Buffer fetches the file again when it publishes."})
                continue
            self._purge_row(d)
            done.append(d["id"])
        if want is not None:
            have = {d["id"] for d in rows}
            refused += [{"id": i, "status": 404, "needs_force": False, "error": "That item isn't in the Bin."} for i in want if i not in have]
        if done:
            self.store.audit(a.uid, a.label, "purge_library_bin", "safety", {"items": len(done), "everything": bool(everything)})
        return {"purged": done, "refused": refused}

    def purge_trash(self, days: float | None = None) -> int:
        """Remove for good what has been in the Bin longer than ``days`` (TRASH_DAYS). Returns how many items."""
        cutoff = _now() - (TRASH_DAYS if days is None else days) * 86400
        n = 0
        for r in self.store.q("SELECT * FROM library_items WHERE storage='trash'"):
            d = dict(r)
            d["meta"] = json.loads(d["meta"] or "{}") or {}
            if ((d["meta"].get("trash") or {}).get("at") or 0) > cutoff:
                continue
            self._purge_row(d)
            n += 1
        return n

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
                                            "with type, size, date and which posts used each one. q searches names and post titles; kind: video | image | carousel | upload | generated.",
     "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}, "kind": {"type": "string"}}}},
    {"name": "delete_library_item", "description": "Delete one Library item (the file and its record). Refused with a warning while a post that hasn't gone out uses it; force=true deletes anyway.",
     "inputSchema": {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}, "force": {"type": "boolean"}}}},
    {"name": "use_library_item", "description": "Use a Library item in a new post. With prompt (what to post, which channels) it starts make_post with the file attached and returns the run; without, it returns the media_token to pass to make_post.",
     "inputSchema": {"type": "object", "required": ["item_id"], "properties": {"item_id": {"type": "integer"}, "prompt": {"type": "string"},
                                                                             "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                                             "format": {"type": "string", "enum": ["auto", "reel", "carousel", "photo"]}}}},
]
READ_ONLY = {"list_library"}


def call_tool(studio, actor, name: str, args: dict):
    lib = studio.house.library
    if name == "list_library":
        return lib.list(actor, str(args.get("q") or ""), str(args.get("kind") or ""))
    if name == "delete_library_item":
        return lib.delete(actor, args.get("item_id"), bool(args.get("force")))
    if name == "use_library_item":
        return lib.use(actor, args.get("item_id"), {k: args.get(k) for k in ("prompt", "channel_ids", "format") if args.get(k)})
    return NotImplemented


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


def add_routes(app, house, actor) -> None:
    lib = house.library

    def serve(f: dict, request: Request, cache: str):
        size = f["size"]
        headers = {"Accept-Ranges": "bytes", "Cache-Control": cache, "Content-Disposition": "inline"}
        rng = parse_range(request.headers.get("range", ""), size)
        if request.headers.get("range") and rng is None:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        if rng:
            s, e = rng
            e = min(e, s + 8 * MB - 1)  # one range answer reads at most 8 MB (players ask again for the rest)
            return Response(f["read"](s, e), status_code=206, media_type=f["mime"], headers={**headers, "Content-Range": f"bytes {s}-{e}/{size}"})
        if f.get("iter"):
            return StreamingResponse(f["iter"](), media_type=f["mime"], headers={**headers, "Content-Length": str(size)})
        return Response(f["read"](0, None), media_type=f["mime"], headers=headers)

    @app.get("/api/library")
    def library_list(request: Request, q: str = "", kind: str = ""):
        return lib.list(actor(request), q, kind)

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
        return await lib.save_stream(a, nm, request.stream(), declared, "take" if source == "take" else "upload")

    @app.get("/api/library/trash")
    def library_trash(request: Request):
        """Items deleted in the last TRASH_DAYS days; restore one with POST /api/library/{id}/restore."""
        return lib.trash(actor(request))

    @app.post("/api/library/{iid}/restore")
    def library_restore(iid: int, request: Request):
        return lib.restore(actor(request, write=True), iid)

    @app.get("/api/library/{iid}")
    def library_item(iid: int, request: Request):
        return lib.item(actor(request), iid)

    @app.get("/api/library/{iid}/file")
    def library_file(iid: int, request: Request, i: int = 0):
        return serve(lib.file(actor(request), iid, i), request, "private, max-age=3600")

    @app.delete("/api/library/{iid}")
    def library_delete(iid: int, request: Request, force: int = 0):
        return lib.delete(actor(request, write=True), iid, bool(force))

    @app.post("/api/library/{iid}/use")
    def library_use(iid: int, request: Request, body: dict = Body(default={})):
        return lib.use(actor(request, write=True), iid, body or {})

    app.state.library_serve = serve
