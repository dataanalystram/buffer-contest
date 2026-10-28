"""Threads (Buffer's threaded posts, free here).

`options.thread` on a channel that has the `threads` capability (X; the demo network) holds the posts that
follow the main one. `POST /api/threads/split` turns one long text into parts that fit the network's limit.
"""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException

from .. import actions as reg
from ..actions import B, I, S, Action
from ..connectors.base import THREAD_MAX_PARTS, split_thread, utf16_len


def add_thread_routes(app: FastAPI, core) -> None:
    @app.post("/api/threads/split", tags=["threads"])
    def thread_split(payload: dict = Body(...)):
        """Split a long text into thread posts that each fit the network (X counts links as 23 and emoji as 2)."""
        provider = payload.get("provider") or "x"
        conn = core.connectors.get(provider)
        if not conn or not conn.capabilities.get("threads"):
            raise HTTPException(400, f"{provider} does not take threads")
        limit = int(payload.get("limit") or conn.capabilities.get("thread_limit") or conn.caption_limit)
        if provider == "x":
            from ..connectors.x import weighted_len as length
        else:
            length = utf16_len
        parts = split_thread(payload.get("text") or "", limit, length, bool(payload.get("numbering")))
        if len(parts) > THREAD_MAX_PARTS:
            raise HTTPException(400, f"That makes {len(parts)} posts - a thread can have at most {THREAD_MAX_PARTS}")
        return {"parts": parts, "count": len(parts), "limit": limit,
                "caption": parts[0] if parts else "", "thread": parts[1:]}


for _a in (
    Action("thread.split", "Split a long text into thread posts that fit the network", "POST", "/api/threads/split",
           {"text": S, "provider": {**S, "description": "x (default) or another network with threads"},
            "numbering": {**B, "description": "add ' 1/n' to each post"}, "limit": I}, ("text",),
           examples=("turn this into an X thread",), group="post", ui="Composer › Thread › Split into thread"),
):
    reg.register(_a)
