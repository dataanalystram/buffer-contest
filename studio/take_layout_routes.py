"""REST routes for One take layouts (studio/take_layout.py). Kept apart because the video worker imports take_layout
through takes.py and its venv has no FastAPI."""
from fastapi import Body, Request

from . import clip_draft
from .take_layout import set_layout, set_smart_layout, smart_layout_info, view


def add_routes(app, house, actor) -> None:
    t = house.takes

    @app.get("/api/takes/settings/smart-layout")
    def take_smart_layout(request: Request):
        return smart_layout_info(house, actor(request))

    @app.post("/api/takes/settings/smart-layout")
    def take_smart_layout_set(request: Request, body: dict = Body(default={})):
        return set_smart_layout(house, actor(request, write=True), (body or {}).get("smart_layout"))

    @app.get("/api/takes/{rid}/clips/{clip_id}/layout")
    def take_clip_layout(rid: str, clip_id: str, request: Request):
        return view(t, actor(request), rid, clip_id)

    @app.post("/api/takes/{rid}/clips/{clip_id}/layout")
    def take_clip_layout_set(rid: str, clip_id: str, request: Request, body=Body(default={})):
        return set_layout(t, actor(request, write=True), rid, clip_id, body)

    # Clip Studio drafts (studio/clip_draft.py): one editing state shared by the person and the AI director
    @app.get("/api/takes/{rid}/clips/{clip_id}/draft")
    def take_clip_draft(rid: str, clip_id: str, request: Request):
        return clip_draft.get(t, actor(request), rid, clip_id)

    @app.put("/api/takes/{rid}/clips/{clip_id}/draft")
    def take_clip_draft_put(rid: str, clip_id: str, request: Request, body=Body(default={})):
        return clip_draft.put(t, actor(request, write=True), rid, clip_id, body)

    @app.post("/api/takes/{rid}/clips/{clip_id}/direct")
    def take_clip_direct(rid: str, clip_id: str, request: Request, body=Body(default={})):
        return clip_draft.direct(t, actor(request, write=True), rid, clip_id, body)

    @app.post("/api/takes/{rid}/clips/{clip_id}/draft/render")
    def take_clip_draft_render(rid: str, clip_id: str, request: Request):
        return clip_draft.render(t, actor(request, write=True), rid, clip_id)
