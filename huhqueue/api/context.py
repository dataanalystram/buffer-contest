"""Machine-readable page context for AI agents (headless / agent-first).

  GET /api/context?page=<route>   what the page shows, the actions on it (with REST route, MCP tool, risk, required
                                  fields and JSON schema) and the IDs currently on it (channels, posts, ideas...).
  GET /openapi.json               the OpenAPI spec at the root (same document as /api/openapi.json).

The browser UI also carries the same document in <script type="application/json" id="agent-context"> on every page:
the server embeds the page catalog (no private data, the page is public before login) and the SPA replaces it with
GET /api/context for the current route on every navigation, so a browser agent can read the page instead of guessing.
Actions come from the action registry (huhqueue/actions.py) by matching each action's `ui` location, so a new action
appears on its page's context automatically.
"""
from __future__ import annotations

import json
import re

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

from .. import actions as reg
from .. import safety

# route -> title, what it shows, the `ui` prefixes of registry actions available there, and query parameters it reads
PAGES: dict[str, dict] = {
    "home": {"title": "Home", "shows": "Today at a glance: channels, what's queued next, posts needing attention, quick create.",
             "ui": ("Home", "Composer", "Header", "Left rail", "Onboarding", "PostCard"), "query": {"compose": "new opens the composer"}},
    "queue": {"title": "Publish", "shows": "All posts as a list: Queue, Drafts, Approvals and Sent tabs, with filters.",
              "ui": ("Publish list", "PostCard", "Composer", "Filter bar", "Approvals tab"),
              "query": {"tab": "queue|drafts|approvals|sent", "post": "a post target id to open"}},
    "calendar": {"title": "Calendar", "shows": "Scheduled and sent posts on a week/month calendar; drag to reschedule.",
                 "ui": ("Calendar", "PostCard", "Composer", "Filter bar")},
    "ideas": {"title": "Ideas", "shows": "Idea board (columns) to turn into posts.", "ui": ("Ideas",)},
    "community": {"title": "Community", "shows": "Comments and mentions inbox across channels; reply, resolve, saved replies.",
                  "ui": ("Community",)},
    "insights": {"title": "Insights", "shows": "Analytics across channels: totals, best posts, best times, exports and reports.",
                 "ui": ("Insights", "Reports")},
    "library": {"title": "Media library", "shows": "Uploaded images, videos and PDFs with folders, tags and alt text.",
                "ui": ("Library",)},
    "contacts": {"title": "Contacts", "shows": "People you work with (collaborators, guests, clients).", "ui": ("Contacts",)},
    "growth": {"title": "Growth", "shows": "Campaigns, short links, bio page, hashtag groups, automations and CSV import.",
               "ui": ("Growth",), "query": {"tab": "campaigns|links|bio|hashtags|automations|import"}},
    "channels": {"title": "Channels", "shows": "Connected channels and the Connect-a-channel picker.",
                 "ui": ("Channels", "After connecting a channel")},
    "connect": {"title": "Connect a channel", "shows": "Pick a network and connect it (OAuth or pasted token).",
                "ui": ("Channels", "After connecting a channel")},
    "channel": {"title": "Channel", "shows": "One channel: Publish (queue, drafts, approvals, sent, grid, bulk upload), "
                "Community, Insights and Settings tabs.", "ui": ("Channel", "Channel page", "PostCard", "Composer"),
                "query": {"tab": "publish|community|insights|settings", "ptab": "queue|drafts|approvals|sent"}},
    "settings": {"title": "Settings", "shows": "Preferences, posting defaults, team, API tokens, app keys, AI, audit log.",
                 "ui": ("Settings",), "query": {"section": "general|team|api|keys|ai|audit|library"}},
}


def parse_route(page: str) -> tuple[str, list[str], dict]:
    raw = (page or "").strip().lstrip("#") or "/home"
    path, _, qs = raw.partition("?")
    parts = [p for p in path.split("/") if p]
    query = dict(x.split("=", 1) if "=" in x else (x, "") for x in qs.split("&") if x)
    return (parts[0] if parts else "home"), parts, query


def page_actions(key: str) -> list[dict]:
    prefixes = PAGES[key]["ui"]
    out = []
    for a in reg.ACTIONS:
        locs = [x.strip() for x in re.split(r"[;/]", a.ui or "")]
        if not any(loc.startswith(p) for loc in locs for p in prefixes):
            continue
        d = {"id": a.id, "title": a.title, "risk": a.risk, "confirm": a.risk in reg.CONFIRM_RISKS,
             "required": list(a.required), "input_schema": a.schema(), "where": a.ui}
        if a.method:
            d.update(method=a.method, path=a.path, run=f"POST /api/actions/{a.id}:run")
        if a.nav:
            d["nav"] = a.nav
        d["mcp_tool"] = a.tool if (a.method and a.mcp and a.agent_visible()) else None
        d["agent_allowed"] = a.agent_visible()
        out.append(d)
    return out


def catalog() -> dict:
    """Static description of every page (safe to embed before login)."""
    return {"app": "HuhQueue", "kind": "agent-context", "version": 1,
            "how": "GET /api/context?page=<hash route, e.g. /queue?tab=drafts or /channel/<id>> for the live IDs; "
                   "run actions with their REST route or POST /api/actions/<id>:run (dry_run=true previews public/destructive "
                   "actions and returns a confirm_token). MCP tools have the same names with '.' -> '_'.",
            "docs": {"llms_txt": "/llms.txt", "openapi": "/openapi.json", "actions": "/api/actions", "agents": "/agents"},
            "pages": [{"route": f"/{k}" + ("/{channel_id}" if k == "channel" else ""), "title": v["title"], "shows": v["shows"],
                       "query": v.get("query", {})} for k, v in PAGES.items()]}


def _ids(core, key: str, parts: list[str], query: dict) -> dict:
    from ..models import Channel, Comment, Idea, Media, PostTarget
    from ..services.posts import iso
    out: dict = {}
    with core.db.session() as s:
        chans = s.scalars(select(Channel).order_by(Channel.created_at)).all()
        out["channels"] = [{"id": c.id, "provider": c.provider, "username": c.username, "status": c.status,
                            "paused": c.paused} for c in chans]

        def targets(q, limit=50):
            return [{"id": t.id, "post_id": t.post_id, "channel_id": t.channel_id, "status": t.status,
                     "scheduled_at": iso(t.scheduled_at), "caption": (t.caption or "")[:80]}
                    for t in s.scalars(q.limit(limit))]
        tabs = {"drafts": ("draft",), "approvals": ("pending_approval",), "sent": ("sent",)}
        statuses = tabs.get(query.get("tab") or query.get("ptab") or "", ("scheduled", "sending", "processing", "needs_action"))
        base = select(PostTarget).where(PostTarget.status.in_(statuses)).order_by(PostTarget.scheduled_at)
        if key in ("home", "queue", "calendar"):
            out["post_targets"] = targets(base)
        elif key == "channel" and len(parts) > 1:
            ch = s.get(Channel, parts[1])
            if ch:
                out["channel"] = {"id": ch.id, "provider": ch.provider, "username": ch.username, "status": ch.status,
                                  "timezone": ch.timezone, "daily_limit": ch.daily_limit,
                                  "token_expires_at": iso(ch.token_expires_at)}
                out["post_targets"] = targets(base.where(PostTarget.channel_id == ch.id))
            else:
                out["error"] = "channel not found"
        elif key == "ideas":
            out["ideas"] = [{"id": i.id, "title": i.title or (i.body or "")[:80], "column": i.column}
                            for i in s.scalars(select(Idea).order_by(Idea.position).limit(100))]
        elif key == "library":
            out["media"] = [{"id": m.id, "filename": m.filename, "kind": m.kind}
                            for m in s.scalars(select(Media).order_by(Media.created_at.desc()).limit(100))]
        elif key == "community":
            out["comments"] = [{"id": c.id, "channel_id": c.channel_id, "author": c.author, "text": (c.text or "")[:80],
                                "status": c.status, "kind": c.kind}
                               for c in s.scalars(select(Comment).order_by(Comment.at.desc()).limit(50))]
    return out


def context(core, page: str) -> dict:
    key, parts, query = parse_route(page)
    if key not in PAGES:
        return {"page": page, "known": False, "error": f"unknown page '{key}'", "pages": catalog()["pages"]}
    p = PAGES[key]
    return {"kind": "agent-context", "version": 1, "page": page or "/home", "route": key, "title": p["title"],
            "shows": p["shows"], "query": query, "query_params": p.get("query", {}), "known": True,
            "ids": _ids(core, key, parts, query), "actions": page_actions(key),
            "agents": {k: v for k, v in safety.state(core).items() if k in ("paused", "paused_at", "paused_by", "reason")},
            "docs": catalog()["docs"]}


def embed(html: str) -> str:
    """Put the static catalog into the SPA shell as <script type="application/json" id="agent-context">."""
    blob = json.dumps(catalog(), separators=(",", ":")).replace("</", "<\\/")
    tag = f'<script type="application/json" id="agent-context">{blob}</script>'
    return html.replace("</head>", f"    {tag}\n  </head>", 1) if "</head>" in html else tag + html


def add_context_routes(app: FastAPI, core) -> None:
    @app.get("/api/context", tags=["agents"])
    def page_context(page: str = Query("/home", description="hash route, e.g. /queue?tab=drafts or /channel/ch_123")):
        return context(core, page)

    @app.get("/openapi.json", include_in_schema=False)
    def root_openapi(request: Request):
        return JSONResponse(app.openapi())


reg.register(reg.Action("context.get", "Describe a page for agents (what it shows, actions, IDs, required fields)", "GET",
                        "/api/context", {"page": {"type": "string", "description": "hash route, e.g. /queue?tab=drafts"}},
                        group="agents", ui="Every page › <script id=agent-context>"))
