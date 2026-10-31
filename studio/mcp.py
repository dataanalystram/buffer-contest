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
         "tone": {"type": "string"},
         "thread": {"type": "boolean", "description": "Write X/Bluesky/Threads/Mastodon drafts as a thread; parts are separated by a line containing only ---"}}}},
    {"name": "preview_post", "description": "Validate a post for one channel and get a single-use confirm_token. Show the preview to your user before submitting. mode: draft | queue | next | now | schedule (needs due_at ISO UTC).",
     "inputSchema": {"type": "object", "required": ["channel_id", "text"], "properties": {
         "channel_id": {"type": "string"}, "text": {"type": "string"}, "media_url": {"type": "string"},
         "mode": {"type": "string", "enum": ["draft", "queue", "next", "now", "schedule"]}, "due_at": {"type": "string"},
         "media_urls": {"type": "array", "items": {"type": "string"}}, "options": {"type": "object"},
         "override_warnings": {"type": "boolean", "description": "Accept preflight warnings (errors can't be overridden)"}}}},
    {"name": "preflight_post", "description": "Dry run (read-only): check a post against every chosen channel's real Buffer and network rules before you preview or submit it: text length counted the way Buffer counts it (UTF-16, Instagram line breaks 2, LinkedIn URLs 24, X weighted, Bluesky graphemes), hashtags/mentions, required fields (YouTube title and category, Pinterest board), media count, type, aspect, size and video specs. Returns per channel status ok | warning | error, findings with the doc they come from, and fix ids for autofix_post.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {
         "channel_id": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}}, "text": {"type": "string"},
         "media_url": {"type": "string"}, "media_urls": {"type": "array", "items": {"type": "string"}},
         "mode": {"type": "string", "enum": ["draft", "queue", "next", "now", "schedule"]},
         "options": {"type": "object", "description": "title, category_id (YouTube), board_id (Pinterest), topic (Threads), x_long_posts"}}}},
    {"name": "autofix_post", "description": "Apply preflight fixes for one channel (fixes: list of fix ids from preflight_post, or omit for all): trim or thread the caption under the network's count, keep the first N media, crop/convert/recompress images to the network's ratio and size (stored as new HuhQueue media). Returns the fixed text, media_urls and options plus a fresh preflight. Nothing is sent to Buffer; pass the result to preview_post.",
     "inputSchema": {"type": "object", "required": ["channel_id", "text"], "properties": {
         "channel_id": {"type": "string"}, "text": {"type": "string"}, "media_url": {"type": "string"},
         "media_urls": {"type": "array", "items": {"type": "string"}}, "mode": {"type": "string"}, "options": {"type": "object"},
         "fixes": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "list_rules", "description": "The preflight rule pack: every limit HuhQueue enforces per network, with the official doc URL it was verified against and the date, plus rules that could not be verified (not enforced).",
     "inputSchema": {"type": "object", "properties": {"network": {"type": "string", "description": "Buffer service name, e.g. instagram"}}}},
    {"name": "submit_post", "description": "Submit exactly what preview_post validated. With the default 'review' policy it waits for a human in HuhQueue's Approvals inbox; 'draft' tokens create Buffer drafts only.",
     "inputSchema": {"type": "object", "required": ["confirm_token"], "properties": {"confirm_token": {"type": "string"}}}},
    {"name": "list_approvals", "description": "Your submissions and their status (pending, approved, rejected).",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "approved", "rejected"]}}}},
    {"name": "list_queue", "description": "Posts in Buffer by status: scheduled (default), draft, sent or error (failed to publish). Each has an id for update_post, reschedule_post and delete_draft.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["scheduled", "draft", "sent", "error"]}}}},
    {"name": "update_post", "description": "Edit a post already in Buffer: new caption (text), new time (due_at, ISO UTC), or both. This is the same path as dragging a post on the HuhQueue Planner. Sent posts can't change; drafts stay drafts. 'review' and 'draft' tokens may only change drafts.",
     "inputSchema": {"type": "object", "required": ["post_id"], "properties": {"post_id": {"type": "string"}, "text": {"type": "string"}, "due_at": {"type": "string"},
         "override_warnings": {"type": "boolean"}}}},
    {"name": "reschedule_post", "description": "Move a scheduled post or draft in Buffer to a new time (due_at, ISO UTC). Same as dragging it on the Planner.",
     "inputSchema": {"type": "object", "required": ["post_id", "due_at"], "properties": {"post_id": {"type": "string"}, "due_at": {"type": "string"}}}},
    {"name": "delete_draft", "description": "Delete a Buffer draft (drafts only; scheduled and sent posts are refused).",
     "inputSchema": {"type": "object", "required": ["post_id"], "properties": {"post_id": {"type": "string"}}}},
    {"name": "create_idea", "description": "Save an idea to the Buffer organization's Ideas board (private, not published).",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"title": {"type": "string"}, "text": {"type": "string"}}}},
    {"name": "list_stories", "description": "Newest stories from the news feeds your user follows in HuhQueue (news radar). Pass a story's url to draft_from_link to write posts about it.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "run_agent", "description": "HuhQueue's research agent. Give a topic (query) or a link (url): it finds a recent story, verifies it on the source page, structures the facts, renders on-brand media (carousel, card or 9:16 Reel) with the user's brand kit and Style Ledger, writes one caption per channel and brand-checks everything. Returns a run id; poll get_run until status is ready. Nothing is posted.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "url": {"type": "string"},
         "channel_ids": {"type": "array", "items": {"type": "string"}}, "format": {"type": "string", "enum": ["auto", "carousel", "photo", "reel"]}}}},
    {"name": "edit_media", "description": "Edit the user's own photo or video (public https URL) to their brand kit: crop to the network frame, logo bug, hook headline, end card, captions, brand check. Returns a run id like run_agent.",
     "inputSchema": {"type": "object", "required": ["media_url"], "properties": {"media_url": {"type": "string"}, "brief": {"type": "string", "description": "What the post is about; used for the hook and captions"},
         "channel_ids": {"type": "array", "items": {"type": "string"}}, "format": {"type": "string", "enum": ["auto", "photo", "reel"]}}}},
    {"name": "get_run", "description": "An agent run: every step with what it did, the rendered media URLs, and one ready package per channel (caption, media, brand check).",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}}}},
    {"name": "revise", "description": "Give a correction in plain words (\"logo smaller, top left\", \"no hype\", \"use #FF5A1F\"). It becomes a permanent Style Ledger rule, then the run re-renders, re-captions and re-checks. Show the user before/after.",
     "inputSchema": {"type": "object", "required": ["run_id", "correction"], "properties": {"run_id": {"type": "integer"}, "correction": {"type": "string"}}}},
    {"name": "remember_rule", "description": "Save a brand rule to the Style Ledger without a run (e.g. from the user's feedback on past posts). Every later render and caption follows it.",
     "inputSchema": {"type": "object", "required": ["correction"], "properties": {"correction": {"type": "string"}}}},
    {"name": "brand_check", "description": "Check a caption (and optionally a HuhQueue media token) against every Style Ledger rule. Failing results cite the exact rule.",
     "inputSchema": {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}, "media_token": {"type": "string"}}}},
    {"name": "get_brand", "description": "The user's brand kit (name, handle, colors, font, logo, end card, voice) and the Style Ledger rules.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "schedule_package", "description": "Send one ready package from a run to Buffer (mode draft | queue | next | now | schedule with due_at ISO UTC). Goes through preview and the token's policy: with 'review' it waits in the Approvals inbox.",
     "inputSchema": {"type": "object", "required": ["run_id", "channel_id"], "properties": {"run_id": {"type": "integer"}, "channel_id": {"type": "string"},
         "mode": {"type": "string", "enum": ["draft", "queue", "next", "now", "schedule"]}, "due_at": {"type": "string"},
         "override_warnings": {"type": "boolean"}}}},
    {"name": "get_inbox", "description": "Your user's Inbox: everything waiting for a person, most urgent first. Groups: approvals (agent posts held for a human), failed (Buffer posts in status error, with Buffer's reason), preflight (scheduled posts in the next 7 days that preflight would block or warn about), undated (drafts with no date), open_slots (empty posting times this week), plan (Buffer plan meters at 80%+). Each group has urgency now | soon | fyi, a count, a one-line summary and items with ids you can pass to update_post or reschedule_post. needs_you is the badge count. Read-only.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_calendar", "description": "Everything on the user's Planner: scheduled, sent and draft Buffer posts, agent posts waiting for approval, ready packages not yet sent, and each channel's Buffer posting slots.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "clips_from_video", "description": "One take -> a week. Turn one long video the user owns (a talk, podcast, tutorial) into 3-7 vertical clips of the best things they actually said: transcript with word timestamps, moments picked on sentence boundaries (hooks are exact quotes, never invented), 9:16 face-tracked reframe, burned-in captions in the brand font, a branded cover frame and end card, preflight per channel. Heavy work runs on the video worker; poll list_clips(take_id) until status is ready. Give url (a direct https link to the video file, not a social media page) or media_url (a HuhQueue upload), and owns: true. sample: true uses the bundled sample talk (sample workspace).",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "media_url": {"type": "string"},
         "owns": {"type": "boolean", "description": "Must be true: the user owns this video or has the rights to it"},
         "channel_ids": {"type": "array", "items": {"type": "string"}}, "count": {"type": "integer", "minimum": 1, "maximum": 7},
         "name": {"type": "string"}, "sample": {"type": "boolean"}}}},
    {"name": "list_clips", "description": "Without take_id: the user's takes. With take_id: the take's progress steps and its clips (video and cover URLs, start/end in the source, hook, transcript, layout, preflight per channel, the caption per network, and where each clip was placed).",
     "inputSchema": {"type": "object", "properties": {"take_id": {"type": "integer"}}}},
    {"name": "adjust_clip", "description": "Edit one clip of a take: trim (start_ms/end_ms in the source video; re-renders on the worker), a new hook for the cover (must be at least 3 words quoted from the clip), or the post caption (caption, optionally for one service). Returns the take.",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id"], "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"},
         "start_ms": {"type": "integer"}, "end_ms": {"type": "integer"}, "hook": {"type": "string"}, "caption": {"type": "string"},
         "service": {"type": "string", "description": "Network the caption is for (instagram, tiktok, youtube, ...); omit for all"}}}},
    {"name": "place_clips_in_week", "description": "The Spread: put a take's ready clips into the open Buffer posting slots of a week as DRAFTS (never published). Step 1 (default dry_run): returns the plan (clip, channel, time in the channel's timezone, caption, preflight) and a single-use confirm_token. Show it to the user. Step 2: call again with confirm_token to create the drafts; with a 'review' token each one waits in the Approvals inbox first. week_of: 'next' (default), 'this' or a date in the week.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"},
         "channel_ids": {"type": "array", "items": {"type": "string"}}, "clip_ids": {"type": "array", "items": {"type": "string"}},
         "week_of": {"type": "string"}, "dry_run": {"type": "boolean", "default": True}, "confirm_token": {"type": "string"},
         "override_warnings": {"type": "boolean"}}}},
    {"name": "get_limits", "description": "Buffer API budget left for this app, per-network character limits, and how close the Buffer plan is to its limits (scheduled posts, channels, weekly caps).",
     "inputSchema": {"type": "object", "properties": {}}},
]


def call_tool(studio: Studio, actor: Actor, name: str, args: dict) -> Any:
    args = args or {}
    if name == "whoami":
        return studio.me(actor)
    if name == "list_channels":
        return studio.channels(actor, refresh=bool(args.get("refresh")))
    if name == "draft_from_link":
        return studio.draft_from_link(actor, args.get("url", ""), args.get("channel_ids"), args.get("tone", ""),
                                     bool(args.get("thread")))
    if name == "preview_post":
        return studio.preview_post(actor, args)
    if name == "submit_post":
        return studio.submit_post(actor, args.get("confirm_token", ""))
    if name == "preflight_post":
        return studio.preflight(actor, args)
    if name == "autofix_post":
        return studio.autofix(actor, args)
    if name == "list_rules":
        return studio.preflight_rules(actor, args.get("network") or None)
    if name == "get_inbox":
        from . import inbox
        return inbox.build(studio, actor)
    if name == "list_approvals":
        rows = studio.approvals(actor, args.get("status") or "pending")
        return [r for r in rows if actor.agent is None or r["agent_id"] == actor.agent["id"]]
    if name == "list_queue":
        return studio.queue(actor, args.get("status") or "scheduled")
    if name == "update_post":
        return studio.update_post(actor, args.get("post_id", ""), args.get("text"), args.get("due_at"), bool(args.get("override_warnings")))
    if name == "reschedule_post":
        return studio.reschedule_post(actor, args.get("post_id", ""), args.get("due_at") or "", bool(args.get("override_warnings")))
    if name == "delete_draft":
        return studio.delete_draft(actor, args.get("post_id", ""))
    if name == "create_idea":
        return studio.create_idea(actor, args.get("title", ""), args.get("text", ""))
    if name == "list_stories":
        return studio.radar(actor)
    h = studio.house
    if name == "run_agent":
        return h.start_run(actor, {k: args.get(k) for k in ("query", "url", "channel_ids", "format")})
    if name == "edit_media":
        up = h.import_url(actor, args.get("media_url", ""))
        return h.start_run(actor, {"media_token": up["token"], "brief": args.get("brief", ""), "channel_ids": args.get("channel_ids"),
                                   "format": args.get("format")})
    if name == "get_run":
        return h.run(actor, int(args.get("run_id") or 0))
    if name == "revise":
        return h.revise(actor, int(args.get("run_id") or 0), args.get("correction", ""))
    if name == "remember_rule":
        return h.remember(actor, args.get("correction", ""))
    if name == "brand_check":
        return h.check(actor, args.get("text", ""), args.get("media_token", ""))
    if name == "get_brand":
        return {"kit": h.kit(actor), "rules": h.rules(actor)}
    if name == "schedule_package":
        return h.schedule_package(actor, int(args.get("run_id") or 0), args.get("channel_id", ""), args.get("mode") or "queue", args.get("due_at"),
                                  bool(args.get("override_warnings")))
    if name == "list_calendar":
        return h.calendar(actor)
    if name == "clips_from_video":
        if args.get("sample"):
            return h.takes.start_sample(actor, {"channel_ids": args.get("channel_ids")})
        return h.takes.start(actor, {k: args.get(k) for k in ("url", "media_url", "owns", "channel_ids", "count", "name")})
    if name == "list_clips":
        if args.get("take_id"):
            t = h.takes.get(actor, int(args["take_id"]))
            for c in t["clips"]:
                c.pop("words", None)
            return t
        return {"takes": h.takes.list(actor)}
    if name == "adjust_clip":
        t = h.takes.adjust(actor, int(args.get("take_id") or 0), args.get("clip_id", ""),
                           {k: args[k] for k in ("start_ms", "end_ms", "hook", "caption", "service") if k in args})
        for c in t["clips"]:
            c.pop("words", None)
        return t
    if name == "place_clips_in_week":
        body = {k: args[k] for k in ("channel_ids", "clip_ids", "week_of", "confirm_token", "override_warnings") if k in args}
        body["dry_run"] = args.get("dry_run", True) if not args.get("confirm_token") else None
        return h.takes.place(actor, int(args.get("take_id") or 0), body)
    if name == "get_limits":
        return {**studio.limits(actor), "plan": studio.plan(actor)}
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
                   "serverInfo": {"name": "huhqueue-studio", "title": "HuhQueue — brand-aware editor on the Buffer API", "version": "2.0.0"},
                   "instructions": "HuhQueue edits posts to the user's brand. One take -> a week: clips_from_video -> poll list_clips(take_id) -> adjust_clip if asked -> place_clips_in_week (dry_run, show the plan, then confirm_token; drafts only). Typical post loop: run_agent (topic or link) or edit_media (their photo/video) -> poll get_run until ready -> show the media and captions -> turn any feedback into revise(run_id, correction), which saves a Style Ledger rule -> schedule_package. Posts wait for human approval unless the token policy says otherwise."})
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
