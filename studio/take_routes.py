"""One take's Bin and long-upload routes (REST). Kept apart from take_bin.py / take_upload.py, which the video worker
reaches through takes.py and which therefore must not import FastAPI (the worker venv has none)."""
from __future__ import annotations

from fastapi import Body, Request


def add_bin_routes(app, house, actor) -> None:
    t = house.takes

    @app.get("/api/takes/bin")
    def take_bin_list(request: Request):
        return t.take_bin(actor(request))

    @app.post("/api/takes/bin/purge")
    def take_bin_purge(request: Request, body: dict = Body(default={})):
        b = body or {}
        return t.purge_take_bin(actor(request, write=True), b.get("take_ids"), b.get("clips"), bool(b.get("all")))

    @app.delete("/api/takes/{rid}")
    def take_delete(rid: str, request: Request):
        return t.delete_take(actor(request, write=True), rid)

    @app.delete("/api/takes/{rid}/clips/{clip_id}")
    def take_clip_delete(rid: str, clip_id: str, request: Request):
        return t.delete_take(actor(request, write=True), rid, clip_id)

    @app.post("/api/takes/{rid}/restore")
    def take_restore(rid: str, request: Request):
        return t.restore_take(actor(request, write=True), rid)

    @app.post("/api/takes/{rid}/clips/{clip_id}/restore")
    def take_clip_restore(rid: str, clip_id: str, request: Request):
        return t.restore_take(actor(request, write=True), rid, clip_id)


def add_upload_routes(app, house, actor) -> None:
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
