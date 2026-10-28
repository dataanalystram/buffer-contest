"""Routes for AI ideas, image alt text, comment insights and channel groups, with their registry actions."""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException
from sqlalchemy import select

from .. import actions as reg
from ..actions import I, S, SA, Action
from ..models import Channel, ChannelGroup, Media
from ..services import ai_tools


def _group(g: ChannelGroup) -> dict:
    return {"id": g.id, "name": g.name, "channel_ids": list(g.channel_ids or [])}


def _clean_channels(s, ids) -> list[str]:
    known = {c for (c,) in s.execute(select(Channel.id))}
    return [str(x) for x in dict.fromkeys(ids or []) if str(x) in known]


def add_ai_tools_routes(app: FastAPI, core) -> None:
    @app.post("/api/ideas/generate", tags=["ideas"])
    def ideas_generate(payload: dict = Body(...)):
        try:
            out = ai_tools.generate_ideas(core, payload.get("topic") or "", payload.get("audience") or "", payload.get("count") or 5)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if payload.get("save"):
            out["saved_ids"] = ai_tools.save_ideas(core, out["ideas"], payload.get("column") or "unassigned", payload.get("tags"))
        return out

    @app.post("/api/media/{mid}/alt-text", tags=["media"])
    def media_alt_text(mid: str, payload: dict = Body(default={})):
        try:
            text = ai_tools.alt_text(core, mid)
        except KeyError as e:
            raise HTTPException(404, "Media not found") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if (payload or {}).get("save"):
            with core.db.session() as s:
                s.get(Media, mid).alt_text = text
        return {"id": mid, "alt_text": text}

    @app.get("/api/comments/insights", tags=["community"])
    def comments_insights(days: int = 30, channel_id: str | None = None):
        return ai_tools.comment_insights(core, days, channel_id)

    @app.get("/api/channel-groups", tags=["channels"])
    def groups_list():
        with core.db.session() as s:
            return [_group(g) for g in s.scalars(select(ChannelGroup).order_by(ChannelGroup.name))]

    @app.post("/api/channel-groups", tags=["channels"])
    def groups_create(payload: dict = Body(...)):
        name = str(payload.get("name") or "").strip()[:80]
        if not name:
            raise HTTPException(400, "Give the group a name")
        with core.db.session() as s:
            g = ChannelGroup(name=name, channel_ids=_clean_channels(s, payload.get("channel_ids")))
            s.add(g)
            s.flush()
            return _group(g)

    @app.patch("/api/channel-groups/{gid}", tags=["channels"])
    def groups_update(gid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            g = s.get(ChannelGroup, gid)
            if not g:
                raise HTTPException(404, "Group not found")
            if "name" in payload:
                g.name = str(payload["name"] or "").strip()[:80] or g.name
            if "channel_ids" in payload:
                g.channel_ids = _clean_channels(s, payload["channel_ids"])
            return _group(g)

    @app.delete("/api/channel-groups/{gid}", tags=["channels"])
    def groups_delete(gid: str):
        with core.db.session() as s:
            g = s.get(ChannelGroup, gid)
            if g:
                s.delete(g)
        return {"ok": True}


for _a in (
    Action("idea.generate", "Generate post ideas with AI (optionally save them to the Ideas board)", "POST", "/api/ideas/generate",
           {"topic": S, "audience": S, "count": {**I, "description": "1-10, default 5"}, "save": {"type": "boolean"},
            "column": S, "tags": SA}, ("topic",), "write", "write", group="idea",
           examples=("give me 5 post ideas about AI agents for founders",), ui="Ideas › Generate ideas"),
    Action("media.alt_text", "Generate alt text for an image with AI (vision model)", "POST", "/api/media/{id}/alt-text",
           {"id": S, "save": {"type": "boolean", "description": "also store it on the media item"}}, ("id",), "write", "write",
           group="media", ui="Composer › Alt text › Generate"),
    Action("comment.insights", "Comment insights: themes, questions and unanswered counts", "GET", "/api/comments/insights",
           {"days": I, "channel_id": S}, group="comment", examples=("what are people asking in my comments this month?",),
           ui="Community › Insights"),
    Action("channel_group.list", "List channel groups", "GET", "/api/channel-groups", group="channel", ui="Composer › Groups; Settings › Channels"),
    Action("channel_group.create", "Create a channel group", "POST", "/api/channel-groups", {"name": S, "channel_ids": SA}, ("name",),
           "write", "write", group="channel", ui="Settings › Channels › Groups"),
    Action("channel_group.update", "Rename a channel group or change its channels", "PATCH", "/api/channel-groups/{id}",
           {"id": S, "name": S, "channel_ids": SA}, ("id",), "write", "write", group="channel", ui="Settings › Channels › Groups"),
    Action("channel_group.delete", "Delete a channel group (channels are kept)", "DELETE", "/api/channel-groups/{id}", {"id": S}, ("id",),
           "write", "destructive", group="channel", ui="Settings › Channels › Groups"),
):
    reg.register(_a)
