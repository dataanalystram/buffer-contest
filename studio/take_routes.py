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


def add_thread_routes(app, house, actor) -> None:
    """Threads (studio/thread_bin.py): read earlier ones, delete / restore any thread (the conversation only, never media).
    Ids are public (thr_...)."""
    tb = house.threads

    @app.get("/api/threads/bin")
    def thread_bin_list(request: Request):
        """list_thread_bin: deleted threads in the Bin with days left."""
        return tb.list(actor(request))

    @app.post("/api/threads/bin/purge")
    def thread_bin_purge(request: Request, body: dict = Body(default={})):
        """purge_thread_bin: remove threads in the Bin for good ({thread_ids} or {all: true})."""
        b = body if isinstance(body, dict) else {}
        return tb.purge(actor(request, write=True), b.get("thread_ids"), b.get("all") is True)

    @app.get("/api/threads")
    def thread_list(request: Request, query: str = "", limit: int = 20, cursor: str = ""):
        """list_threads: earlier threads, newest first (id, title, updated_at, run_ids, summary), paginated."""
        return tb.list_threads(actor(request), query or None, limit, cursor or None)

    @app.get("/api/threads/{tid}")
    def thread_get(tid: str, request: Request, cursor: str = ""):
        """get_thread: one thread's messages (paginated) and the runs / takes inside with their outputs."""
        return tb.get_thread(actor(request), tid, cursor or None)

    @app.delete("/api/threads/{tid}")
    def thread_delete(tid: str, request: Request, takes: str = ""):
        """delete_thread: the conversation into the Bin (7 days); media is never deleted (?takes=delete is refused)."""
        return tb.delete(actor(request, write=True), tid, takes or None)

    @app.post("/api/threads/{tid}/restore")
    def thread_restore(tid: str, request: Request):
        """restore_thread: the thread back in the sidebar (within 7 days)."""
        return tb.restore(actor(request, write=True), tid)
