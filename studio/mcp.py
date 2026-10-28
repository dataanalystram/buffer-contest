"""MCP server (Streamable HTTP, JSON responses) acting on the signed-in user's Buffer account.

Auth: `Authorization: Bearer hq_...` agent token minted in HuhQueue → Agents. Each tool maps 1:1 to a
Studio action, so an agent can do exactly what the UI and REST API can, under the same safety rules.
"""
from __future__ import annotations

import json
from typing import Any

from .actions import ActionError, Actor, Studio
from .buffer_api import BufferError

PROTOCOL = "2025-06-18"

TOOLS: list[dict[str, Any]] = [
    {"name": "whoami", "description": "The Buffer account and organization this token acts for, its policy, and whether the kill switch is on.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_channels", "description": "Buffer channels you may post to, with each network's character limit and media needs.",
     "inputSchema": {"type": "object", "properties": {"refresh": {"type": "boolean"}}}},
    {"name": "draft_from_link", "description": "Read an article URL and draft one caption per channel, each within that network's Buffer character limit. Nothing is posted.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {
         "url": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}},
         "tone": {"type": "string"}}}},
    {"name": "preview_post", "description": "Validate a post for one channel and get a single-use confirm_token. Show the preview to your user before submitting. mode: draft | queue | next | now | schedule (needs due_at ISO UTC).",
     "inputSchema": {"type": "object", "required": ["channel_id", "text"], "properties": {
         "channel_id": {"type": "string"}, "text": {"type": "string"}, "media_url": {"type": "string"},
         "mode": {"type": "string", "enum": ["draft", "queue", "next", "now", "schedule"]}, "due_at": {"type": "string"}}}},
    {"name": "submit_post", "description": "Submit exactly what preview_post validated. With the default 'review' policy it waits for a human in HuhQueue's Approvals inbox; 'draft' tokens create Buffer drafts only.",
     "inputSchema": {"type": "object", "required": ["confirm_token"], "properties": {"confirm_token": {"type": "string"}}}},
    {"name": "list_approvals", "description": "Your submissions and their status (pending, approved, rejected).",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected"]}}}},
    {"name": "list_queue", "description": "Posts in Buffer by status: scheduled (default), draft or sent.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["scheduled", "draft", "sent"]}}}},
    {"name": "create_idea", "description": "Save an idea to the Buffer organization's Ideas board (private, not published).",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"title": {"type": "string"}, "text": {"type": "string"}}}},
    {"name": "get_limits", "description": "Buffer API budget left for this app and per-network character limits.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call_tool(studio: Studio, actor: Actor, name: str, args: dict) -> Any:
    args = args or {}
    if name == "whoami":
        return studio.me(actor)
    if name == "list_channels":
        return studio.channels(actor, refresh=bool(args.get("refresh")))
    if name == "draft_from_link":
        return studio.draft_from_link(actor, args.get("url", ""), args.get("channel_ids"), args.get("tone", ""))
    if name == "preview_post":
        return studio.preview_post(actor, args)
    if name == "submit_post":
        return studio.submit_post(actor, args.get("confirm_token", ""))
    if name == "list_approvals":
        rows = studio.approvals(actor, args.get("status") or "pending")
        return [r for r in rows if actor.agent is None or r["agent_id"] == actor.agent["id"]]
    if name == "list_queue":
        return studio.queue(actor, args.get("status") or "scheduled")
    if name == "create_idea":
        return studio.create_idea(actor, args.get("title", ""), args.get("text", ""))
    if name == "get_limits":
        return studio.limits(actor)
    raise ActionError(f"Unknown tool {name}", 404)


def handle(studio: Studio, actor: Actor, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method", "")
    if mid is None:  # notification (e.g. notifications/initialized)
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": "huhqueue-studio", "title": "HuhQueue — built on the Buffer API", "version": "1.0.0"},
                   "instructions": "Draft from links, preview, then submit. Posts wait for human approval unless the token policy says otherwise."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        p = msg.get("params") or {}
        try:
            result = call_tool(studio, actor, p.get("name", ""), p.get("arguments") or {})
            return ok({"content": [{"type": "text", "text": json.dumps(result, indent=1, default=str)}],
                       "structuredContent": result if isinstance(result, dict) else {"items": result}, "isError": False})
        except (ActionError, BufferError) as e:
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
