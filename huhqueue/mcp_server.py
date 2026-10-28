"""HuhQueue as an MCP server (Model Context Protocol, stdio transport, JSON-RPC 2.0).

    huhqueue mcp            # reads HUHQ_URL + HUHQ_API_TOKEN

Every tool call goes through the same REST API the UI uses, authenticated with a SCOPED
API token (Settings -> API & MCP). The token decides what the agent may do:
  * scopes (read / write / publish / comments / admin) and an optional per-tool allow-list,
  * require_approval: scheduling/publishing becomes "pending approval" until you approve it in the app,
  * a per-token rate limit; every call is written to the audit log (tool name included).

Implemented without an SDK so it has no extra dependency: initialize, ping, tools/list, tools/call.
Example client config (Claude Desktop / Cursor / any MCP client):
  {"mcpServers": {"huhqueue": {"command": "huhqueue", "args": ["mcp"],
                                "env": {"HUHQ_URL": "http://localhost:8787", "HUHQ_API_TOKEN": "hq_..."}}}}
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

import httpx

from . import __version__

PROTOCOL = "2025-06-18"

WHEN = {"type": "string", "enum": ["queue", "prioritize", "now", "custom", "draft"],
        "description": "queue = next free slot, prioritize = top of queue, now, custom (needs scheduled_at), draft"}
TOOLS: list[dict] = [
    {"name": "list_channels", "description": "List connected channels (id, network, username, followers, daily limit, status).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "upload_media", "description": "Add media from a local file path, folder, or https URL. Returns media ids (in order).",
     "inputSchema": {"type": "object", "properties": {"paths": {"type": "array", "items": {"type": "string"}}},
                     "required": ["paths"]}},
    {"name": "create_post", "description": "Create a DRAFT post for one or more channels (nothing is published).",
     "inputSchema": {"type": "object", "properties": {
         "caption": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"},
                                                        "description": "ids or provider:username; ['all'] for every channel"},
         "media_ids": {"type": "array", "items": {"type": "string"}}, "tags": {"type": "array", "items": {"type": "string"}},
         "options": {"type": "object", "description": "network options, e.g. {ig_type:'reel', first_comment:'…', follow_cta:'comment'}"},
         "topic_key": {"type": "string", "description": "stable id of the story; the same topic is never posted to a channel twice"}},
         "required": ["channel_ids"]}},
    {"name": "schedule_post", "description": "Create and schedule a post (queue / prioritize / custom time / now). "
                                             "If the token requires approval it waits for a human.",
     "inputSchema": {"type": "object", "properties": {
         "caption": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}},
         "media_ids": {"type": "array", "items": {"type": "string"}}, "when": WHEN,
         "scheduled_at": {"type": "string", "description": "ISO time, e.g. 2026-10-23T09:30 (channel timezone if no offset)"},
         "override_rate_limit": {"type": "boolean", "description": "major news: ignore the daily limit"},
         "options": {"type": "object"}, "tags": {"type": "array", "items": {"type": "string"}},
         "topic_key": {"type": "string", "description": "stable id of the story - duplicates on a channel are refused"}},
         "required": ["channel_ids"]}},
    {"name": "publish_now", "description": "Publish an existing post (by post id) immediately.",
     "inputSchema": {"type": "object", "properties": {"post_id": {"type": "string"}, "override_rate_limit": {"type": "boolean"}},
                     "required": ["post_id"]}},
    {"name": "suggest_format", "description": "Suggest the best format per channel (video → Reel/Short, several images or text-heavy → carousel).",
     "inputSchema": {"type": "object", "properties": {"caption": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                      "media_ids": {"type": "array", "items": {"type": "string"}}}, "required": ["channel_ids"]}},
    {"name": "list_queue", "description": "List posts. Filter by status (draft,pending_approval,scheduled,processing,sent,failed,needs_action), "
                                          "channel, date range (start/end ISO), tag, text (q) and post_type.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "channel_id": {"type": "string"},
                                                      "start": {"type": "string"}, "end": {"type": "string"}, "tag": {"type": "string"},
                                                      "q": {"type": "string"}, "post_type": {"type": "string"},
                                                      "limit": {"type": "integer"}}}},
    {"name": "get_post", "description": "Get one post with its validation issues and event timeline.",
     "inputSchema": {"type": "object", "properties": {"post_id": {"type": "string"}}, "required": ["post_id"]}},
    {"name": "reschedule", "description": "Move a post: when=queue|prioritize|custom|now|draft (+scheduled_at for custom). Can also change caption/options.",
     "inputSchema": {"type": "object", "properties": {"post_id": {"type": "string"}, "when": WHEN,
                                                      "scheduled_at": {"type": "string"}, "caption": {"type": "string"},
                                                      "options": {"type": "object"}}, "required": ["post_id"]}},
    {"name": "get_analytics", "description": "KPIs and top posts for a range: days=7|30|90|mtd, or start/end (ISO dates).",
     "inputSchema": {"type": "object", "properties": {"days": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"},
                                                      "channel_id": {"type": "string"}}}},
    {"name": "list_comments", "description": "Community inbox: comments from Instagram / YouTube. status=open|replied|resolved.",
     "inputSchema": {"type": "object", "properties": {"status": {"type": "string"}, "channel_id": {"type": "string"},
                                                      "sync": {"type": "boolean", "description": "pull fresh comments first"}}}},
    {"name": "reply_comment", "description": "Reply to a comment by its HuhQueue comment id.",
     "inputSchema": {"type": "object", "properties": {"comment_id": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["comment_id", "text"]}},
]


class McpServer:
    def __init__(self, url: str | None = None, token: str | None = None, transport=None):
        import os

        from .config import _load_dotenv
        _load_dotenv(Path.cwd() / ".env")
        self.url = (url or os.environ.get("HUHQ_URL") or f"http://localhost:{os.environ.get('HUHQ_PORT', '8787')}").rstrip("/")
        tok = token or os.environ.get("HUHQ_API_TOKEN", "")
        self.http = httpx.Client(base_url=self.url, timeout=600, transport=transport,
                                 headers={"Authorization": f"Bearer {tok}"} if tok else {})
        self.handlers: dict[str, Callable[[dict], Any]] = {
            "list_channels": self.t_list_channels, "upload_media": self.t_upload_media, "create_post": self.t_create_post,
            "schedule_post": self.t_schedule_post, "publish_now": self.t_publish_now, "list_queue": self.t_list_queue,
            "get_post": self.t_get_post, "reschedule": self.t_reschedule, "get_analytics": self.t_get_analytics,
            "list_comments": self.t_list_comments, "reply_comment": self.t_reply_comment,
            "suggest_format": self.t_suggest_format,
        }
        self._allowed: list[str] | None = None
        self._confirm = ""   # confirm_token for the call in progress (public actions: preview first, then confirm)

    # ---- REST helper
    def _headers(self, tool: str) -> dict:
        h = {"X-HuhQ-Tool": tool}
        if self._confirm:
            h["X-HuhQ-Confirm"] = self._confirm
        return h

    def _api(self, tool: str, method: str, path: str, **kw):
        r = self.http.request(method, path, headers=self._headers(tool), **kw)
        try:
            data = r.json()
        except ValueError:
            data = r.text
        if r.status_code == 428 and isinstance(data, dict) and data.get("status") == "needs_confirmation":
            raise NeedsConfirmation(data)
        if r.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else data
            raise ToolError(f"HTTP {r.status_code}: {json.dumps(detail) if not isinstance(detail, str) else detail}")
        return data

    def _registry_tools(self) -> list[dict]:
        """Every agent-visible registry action the token's scopes allow (GET /api/actions filters for us)."""
        try:
            acts = self.http.get("/api/actions").json().get("actions", [])
        except Exception:
            return []
        from .actions import LEGACY_TOOLS
        aliased = set(LEGACY_TOOLS.values())
        out = []
        for a in acts:
            if not a.get("mcp") or a["id"] in aliased:
                continue
            desc = f"{a['summary']} [risk: {a['risk']}, scope: {a['scope']}]"
            if a.get("examples"):
                desc += " e.g. " + "; ".join(a["examples"])
            out.append({"name": a["tool"], "description": desc, "inputSchema": a["input_schema"], "_action": a["id"]})
        return out

    def allowed_tools(self) -> list[dict]:
        if self._allowed is None:
            try:
                me = self.http.get("/api/me").json()
                allow = me.get("tools") or []
                scopes = set(me.get("scopes") or [])
                legacy = [t for t in TOOLS]
                if "admin" not in scopes:
                    need = {"create_post": "write", "upload_media": "write", "schedule_post": "write", "reschedule": "write",
                            "publish_now": "publish", "reply_comment": "comments"}
                    legacy = [t for t in legacy if need.get(t["name"], "read") in scopes]
                tools = legacy + self._registry_tools()
                self._allowed = [t for t in tools if not allow or t["name"] in allow]
            except Exception:
                self._allowed = list(TOOLS)
        out = []
        for t in self._allowed:
            t = {k: v for k, v in t.items() if not k.startswith("_")}
            sch = dict(t.get("inputSchema") or {"type": "object", "properties": {}})
            sch["properties"] = {**(sch.get("properties") or {}), "confirm_token": {
                "type": "string", "description": "Only after a human approved the preview of a public action"}}
            out.append({**t, "inputSchema": sch})
        return out

    def _call_registry(self, name: str, args: dict):
        action = next((t["_action"] for t in self._allowed or [] if t["name"] == name and "_action" in t), None)
        r = self.http.post(f"/api/actions/{action}:run", json={"input": args}, headers=self._headers(name))
        try:
            data = r.json()
        except ValueError:
            raise ToolError(f"HTTP {r.status_code}: {r.text[:500]}")
        if data.get("status") == "needs_confirmation":
            raise NeedsConfirmation(data)
        if r.status_code >= 400 or data.get("status") == "error":
            raise ToolError(f"HTTP {r.status_code}: {data.get('message') or data}")
        return {"status": data["status"], "output": data.get("output")}

    # ---- tools
    def _channels(self, ids: list[str]) -> list[str]:
        if ids == ["all"]:
            return [c["id"] for c in self._api("list_channels", "GET", "/api/channels") if c["status"] == "active"]
        return ids

    def t_list_channels(self, a):
        return [{k: c.get(k) for k in ("id", "provider", "username", "display_name", "followers_count", "media_count",
                                       "status", "daily_limit", "timezone", "paused")}
                for c in self._api("list_channels", "GET", "/api/channels")]

    def t_upload_media(self, a):
        ids = []
        for p in a.get("paths") or []:
            if p.startswith(("http://", "https://")):
                ids.append(self._api("upload_media", "POST", "/api/media/from-url", json={"url": p})["id"])
                continue
            path = Path(p).expanduser()
            if not path.exists():
                raise ToolError(f"File not found: {p}")
            for f in (sorted(x for x in path.iterdir() if x.is_file()) if path.is_dir() else [path]):
                with open(f, "rb") as fh:
                    res = self._api("upload_media", "POST", "/api/media", files={"files": (f.name, fh)})
                ids += [m["id"] for m in res["media"]]
        return {"media_ids": ids}

    def _post(self, tool, a, when):
        body = {"caption": a.get("caption", ""), "channel_ids": self._channels(a.get("channel_ids") or []),
                "media_ids": a.get("media_ids") or [], "when": when, "scheduled_at": a.get("scheduled_at"),
                "override_rate_limit": bool(a.get("override_rate_limit")), "options": a.get("options") or {},
                "tags": a.get("tags") or [], "topic_key": a.get("topic_key") or "", "source": "mcp"}
        res = self._api(tool, "POST", "/api/posts", json=body)
        return {"posts": [{"post_id": t["id"], "channel": f"{t['channel']['provider']}:{t['channel']['username']}",
                           "status": t["status"], "scheduled": t["scheduled_local"]} for t in res["targets"]],
                "notes": res.get("notes", [])}

    def t_create_post(self, a):
        return self._post("create_post", a, "draft")

    def t_schedule_post(self, a):
        return self._post("schedule_post", a, a.get("when") or "queue")

    def t_publish_now(self, a):
        t = self._api("publish_now", "POST", f"/api/targets/{a['post_id']}/publish-now",
                      json={"override_rate_limit": bool(a.get("override_rate_limit"))})
        return {"post_id": t["id"], "status": t["status"], "permalink": t.get("permalink"), "error": t.get("last_error")}

    def t_suggest_format(self, a):
        return self._api("suggest_format", "POST", "/api/suggest-format",
                         json={"channel_ids": self._channels(a.get("channel_ids") or []), "caption": a.get("caption", ""),
                               "media_ids": a.get("media_ids") or []})

    def t_list_queue(self, a):
        params = {k: v for k, v in {"status": a.get("status"), "channel_id": a.get("channel_id"), "start": a.get("start"),
                                    "end": a.get("end"), "tag": a.get("tag"), "q": a.get("q"), "post_type": a.get("post_type"),
                                    "limit": a.get("limit", 50)}.items() if v}
        return [{"post_id": t["id"], "channel": f"{t['channel']['provider']}:{t['channel']['username']}", "status": t["status"],
                 "scheduled": t["scheduled_local"], "caption": (t["caption"] or "")[:140], "error": t["last_error"] or None,
                 "permalink": t["permalink"] or None} for t in self._api("list_queue", "GET", "/api/posts", params=params)]

    def t_get_post(self, a):
        t = self._api("get_post", "GET", f"/api/targets/{a['post_id']}")
        t["events"] = self._api("get_post", "GET", f"/api/targets/{a['post_id']}/events")
        return t

    def t_reschedule(self, a):
        body = {k: a[k] for k in ("when", "scheduled_at", "caption", "options") if k in a}
        t = self._api("reschedule", "PATCH", f"/api/targets/{a['post_id']}", json=body)
        return {"post_id": t["id"], "status": t["status"], "scheduled": t["scheduled_local"], "notes": t.get("notes", [])}

    def t_get_analytics(self, a):
        params = {k: v for k, v in {"days": a.get("days", "30"), "start": a.get("start"), "end": a.get("end"),
                                    "channel_id": a.get("channel_id")}.items() if v}
        d = self._api("get_analytics", "GET", "/api/insights", params=params)
        d["top_posts"] = [{"post_id": t["id"], "channel": t["channel"]["username"], "caption": t["caption"][:100],
                           "insights": t["insights"], "permalink": t["permalink"]} for t in d["top_posts"][:10]]
        return d

    def t_list_comments(self, a):
        if a.get("sync"):
            self._api("list_comments", "POST", "/api/comments/sync", json={})
        params = {k: v for k, v in {"status": a.get("status"), "channel_id": a.get("channel_id")}.items() if v}
        return [{k: c[k] for k in ("id", "provider", "channel_username", "author", "text", "at", "status", "media_caption")}
                for c in self._api("list_comments", "GET", "/api/comments", params=params)]

    def t_reply_comment(self, a):
        return self._api("reply_comment", "POST", f"/api/comments/{a['comment_id']}/reply", json={"text": a["text"]})

    # ---- JSON-RPC
    def handle(self, msg: dict) -> dict | None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                result = {"protocolVersion": params.get("protocolVersion") or PROTOCOL,
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "huhqueue", "version": __version__},
                          "instructions": "HuhByte social scheduler. Use list_channels first; schedule_post respects posting "
                                          "slots, daily limits and (if configured) human approval. Public actions (publish, "
                                          "schedule, reply) first return status=needs_confirmation with a preview and a "
                                          "confirm_token: show the preview to a human, and only after they agree call the "
                                          "same tool again with the same arguments plus confirm_token. If calls fail with "
                                          "HTTP 423, a human paused all agents (kill switch): stop and tell them."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.allowed_tools()}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments") or {}
                args = dict(args)
                self._confirm = str(args.pop("confirm_token", "") or "")
                try:
                    if name not in {t["name"] for t in self.allowed_tools()}:
                        raise ToolError(f"Tool '{name}' is not allowed for this token")
                    out = self.handlers[name](args) if name in self.handlers else self._call_registry(name, args)
                    result = {"content": [{"type": "text", "text": json.dumps(out, indent=2, default=str)}],
                              "structuredContent": out if isinstance(out, dict) else {"items": out}, "isError": False}
                except NeedsConfirmation as e:   # public action: nothing happened yet; show the preview to a human
                    result = {"content": [{"type": "text", "text": json.dumps(e.data, indent=2, default=str)}],
                              "structuredContent": e.data, "isError": False}
                except ToolError as e:
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
                finally:
                    self._confirm = ""
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}
            return {"jsonrpc": "2.0", "id": mid, "result": result}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": str(e)}}
        except Exception as e:  # pragma: no cover
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"{e.__class__.__name__}: {e}"}}

    def serve(self, stdin=sys.stdin, stdout=sys.stdout) -> None:
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                stdout.write(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}) + "\n")
                stdout.flush()
                continue
            batch = msg if isinstance(msg, list) else [msg]
            out = [r for r in (self.handle(m) for m in batch) if r]
            if out:
                stdout.write(json.dumps(out if isinstance(msg, list) else out[0]) + "\n")
                stdout.flush()


class ToolError(Exception):
    pass


def main() -> None:  # pragma: no cover
    McpServer().serve()


class NeedsConfirmation(Exception):
    """The server held a public action for a human look (HTTP 428): carries the preview + confirm_token."""

    def __init__(self, data: dict):
        super().__init__(data.get("message", "needs confirmation"))
        self.data = data
