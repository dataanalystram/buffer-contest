"""Agent safety layer: global kill switch, per-token daily caps, channel scope,
MCP preview + confirm for public actions, and the richer audit trail.

Outward-facing actions need an explicit `confirm` after a human looks. The code here is our own.

Everything an agent does arrives as an API-token request, so the checks live in one place (the guard middleware in
`api/app.py` calls `check_token_request`), and the REST API, the generic action runner and MCP all get them for free.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import secrets
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from . import actions as reg
from .models import KV, AuditLog, Channel, PostTarget, utcnow

KILL_KEY = "agents.safety"
AGENT_SOURCES = ("api", "mcp", "agent", "cli")   # Post.source values written by token callers
CONFIRM_TTL = 600                                   # seconds an MCP confirm_token stays valid
CONFIRM_HEADER = "x-huhq-confirm"
# requests a paused agent may still make (so it can see why it is blocked, and pause itself)
PAUSE_ALLOWED = {("GET", "/api/agents/safety"), ("GET", "/api/me"), ("POST", "/api/agents/pause")}
# routes that only re-enter real routes (counted once, at the inner route)
RUNNER_PREFIXES = ("/api/actions", "/api/assistant/")
_TGT_ONLY = re.compile(r"^/api/targets/[^/]+$")


# --------------------------------------------------------------------------- kill switch
def state(core) -> dict:
    with core.db.session() as s:
        row = s.get(KV, KILL_KEY)
        v = dict(row.value) if row else {}
    return {"paused": bool(v.get("paused")), "paused_at": v.get("paused_at"), "paused_by": v.get("paused_by", ""),
            "reason": v.get("reason", ""), "resumed_at": v.get("resumed_at"), "resumed_by": v.get("resumed_by", "")}


def set_paused(core, paused: bool, who: str, reason: str = "") -> dict:
    now = utcnow().isoformat(timespec="seconds") + "Z"
    with core.db.session() as s:
        row = s.get(KV, KILL_KEY)
        if not row:
            row = KV(key=KILL_KEY, value={})
            s.add(row)
        v = dict(row.value or {})
        if paused:
            v.update(paused=True, paused_at=now, paused_by=who[:80], reason=(reason or "")[:300])
        else:
            v.update(paused=False, resumed_at=now, resumed_by=who[:80])
        row.value = v
    return state(core)


def is_paused(core) -> bool:
    return state(core)["paused"]


def held_post_filter():
    """SQL condition for the worker: targets whose post an agent created (held while agents are paused)."""
    from .models import Post
    return PostTarget.post_id.in_(select(Post.id).where(Post.source.in_(AGENT_SOURCES)))


# --------------------------------------------------------------------------- classification
def route_action(method: str, path: str, body) -> reg.Action | None:
    """The registry action behind a REST call. POST /api/posts is a draft unless `when` says otherwise."""
    when = body.get("when") if isinstance(body, dict) else None
    if method == "POST" and path == "/api/posts":
        return reg.get("post.create_draft" if when == "draft" else "post.schedule")
    if method == "PATCH" and _TGT_ONLY.match(path):
        if when == "now":
            return reg.get("post.publish_now")
        return reg.get("post.update" if when and when != "draft" else "post.note")
    return reg.find_route_action(method, path)


def risk_of(method: str, path: str, body) -> tuple[str, str]:
    """(action id, risk) for the audit log and the caps. Editing a post without moving it is a write."""
    if path.startswith(RUNNER_PREFIXES):
        return "", ""
    a = route_action(method, path, body)
    if a is None:
        return "", "safe" if method in ("GET", "HEAD") else "write"
    return a.id, a.risk


def is_public(method: str, path: str, body) -> bool:
    return risk_of(method, path, body)[1] == "public"


def input_hash(body_raw: bytes | None, query: str = "") -> str:
    if not body_raw and not query:
        return ""
    return hashlib.sha256((query or "").encode() + b"|" + (body_raw or b"")).hexdigest()[:16]


# --------------------------------------------------------------------------- channel scope
_CH_PATH = re.compile(r"^/api/channels/([^/]+)")
_TGT_PATH = re.compile(r"^/api/targets/([^/]+)")
_POST_PATH = re.compile(r"^/api/posts/([^/]+)/")                      # /api/posts/{post_id}/notes|revisions/...
_COMMENT_PATH = re.compile(r"^/api/comments/([^/]+)")
_COMMENT_STATIC = {"bulk", "sync", "insights", "suggest-reply", "start-post"}
_CHANNEL_IN_PATH = (re.compile(r"^/api/queue/([^/]+)"), re.compile(r"^/api/utm/defaults/([^/]+)"))
UNRESOLVED = "\x00unresolved"   # an object named in the PATH that doesn't exist (or has no channel): never in any scope


def _post_channels(s, pid) -> set[str]:
    from .models import Post
    p = s.get(Post, str(pid)) if pid else None
    return {t.channel_id for t in p.targets} if p else set()


def _target_channels(s, tid) -> set[str]:
    t = s.get(PostTarget, str(tid)) if tid else None
    return {t.channel_id} if t else set()


def _comment_channels(s, cid) -> set[str]:
    from .models import Comment
    c = s.get(Comment, str(cid)) if cid else None
    return {c.channel_id} if c else set()


def scope_refs(s, method: str, path: str, query: dict, body) -> tuple[set[str], set[str]]:
    """(channels the request names directly, channels of the OBJECTS it names by id).

    Objects are posts, post targets and comments, named in the path (/api/posts/{id}/..., /api/targets/{id}...,
    /api/comments/{id}...) or the body (ids, post_id, target_id, comment_id). Each is resolved to its channel(s) here,
    before any route runs, so a channel-limited token can't reach another channel's object by id (CodeRabbit,
    4 Nov 2026). A path object that doesn't resolve adds UNRESOLVED, so a scoped token gets the same 404 for a missing
    id as for another channel's id (no existence probing); a body id that doesn't resolve adds nothing (the route 404s)."""
    from .services.posts import PostError, resolve_channels
    explicit: set[str] = set()
    objects: set[str] = set()
    refs: list = []
    if isinstance(body, dict):
        refs += list(body.get("channel_ids") or [])
        refs += [c.get("channel_id") for c in body.get("channels") or [] if isinstance(c, dict)]
        for k in ("channel_id", "source_channel_id"):
            if body.get(k):
                refs.append(body[k])
        on_comments = path.startswith("/api/comments")
        for oid in body.get("ids") or []:          # bulk actions: comment ids on /api/comments/*, post targets elsewhere
            objects |= _comment_channels(s, oid) if on_comments else _target_channels(s, oid)
        objects |= _post_channels(s, body.get("post_id"))
        objects |= _target_channels(s, body.get("target_id"))
        objects |= _comment_channels(s, body.get("comment_id"))
    for v in str(query.get("channel_id") or "").split(","):
        if v.strip():
            refs.append(v.strip())
    m = _CH_PATH.match(path)
    if m and m.group(1) not in ("order",):
        refs.append(m.group(1))
    for rx in _CHANNEL_IN_PATH:
        m = rx.match(path)
        if m:
            refs.append(m.group(1))
    for rx, resolve in ((_TGT_PATH, _target_channels), (_POST_PATH, _post_channels), (_COMMENT_PATH, _comment_channels)):
        m = rx.match(path)
        if m and not (rx is _COMMENT_PATH and m.group(1) in _COMMENT_STATIC):
            objects |= resolve(s, m.group(1)) or {UNRESOLVED}
    refs = [str(r) for r in refs if r and r != "all"]
    if refs:
        try:
            explicit |= {c.id for c in resolve_channels(s, refs)}
        except PostError:
            explicit |= set(refs)   # unknown refs count as "not allowed"
    if isinstance(body, dict) and body.get("channel_ids") == ["all"]:
        explicit |= set(s.scalars(select(Channel.id).where(Channel.status != "disconnected")))
    return explicit, objects


def referenced_channels(s, method: str, path: str, query: dict, body) -> set[str]:
    """Channel ids a request touches (by id, provider:username, or through a post / target / comment id)."""
    explicit, objects = scope_refs(s, method, path, query, body)
    return explicit | (objects - {UNRESOLVED})


# --------------------------------------------------------------------------- daily caps
def day_start_utc(core) -> datetime:
    try:
        zone = ZoneInfo(core.prefs().get("timezone") or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    local = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def used_today(core, token_id: str | None) -> dict:
    """Successful calls this token made today (app timezone): every action, and the public ones."""
    if not token_id:
        return {"actions": 0, "public": 0}
    since = day_start_utc(core)
    with core.db.session() as s:
        base = select(func.count()).select_from(AuditLog).where(
            AuditLog.token_id == token_id, AuditLog.at >= since, AuditLog.status < 400, AuditLog.method != "GET",
            ~AuditLog.path.startswith("/api/actions"), ~AuditLog.path.startswith("/api/assistant/"))
        n = int(s.scalar(base) or 0)
        p = int(s.scalar(base.where(AuditLog.risk == "public")) or 0)
    return {"actions": n, "public": p}


# --------------------------------------------------------------------------- MCP preview + confirm
class ConfirmStore:
    """confirm_token -> the exact request it approves (token id + method + path + body hash). One use, 10 minutes."""

    def __init__(self):
        self.pending: dict[str, dict] = {}

    def _gc(self):
        now = time.time()
        for k in [k for k, v in self.pending.items() if v["exp"] < now]:
            self.pending.pop(k, None)

    @staticmethod
    def signature(token_id, method, path, body_raw: bytes | None) -> str:
        norm = body_raw or b""
        try:
            norm = json.dumps(json.loads(norm or b"null"), sort_keys=True).encode()
        except ValueError:
            pass
        return hashlib.sha256(f"{token_id}|{method}|{path}|".encode() + norm).hexdigest()

    def issue(self, sig: str, preview: dict) -> str:
        self._gc()
        tok = "cf_" + secrets.token_urlsafe(18)
        self.pending[tok] = {"sig": sig, "exp": time.time() + CONFIRM_TTL, "preview": preview}
        return tok

    def take(self, tok: str, sig: str) -> bool:
        self._gc()
        p = self.pending.get(tok or "")
        if not p or p["sig"] != sig:
            return False
        self.pending.pop(tok, None)
        return True


def preview_for(method: str, path: str, body, action_id: str) -> dict:
    from .api.actions_api import describe
    a = reg.get(action_id) if action_id else None
    inp = body if isinstance(body, dict) else {}
    return {"action": action_id, "title": a.title if a else f"{method} {path}", "risk": "public", "method": method,
            "path": path, "summary": describe(a, inp) if a else f"{method} {path}"}


def needs_confirmation_body(preview: dict, token: str) -> dict:
    return {"status": "needs_confirmation", "preview": preview, "confirm_token": token, "expires_in": CONFIRM_TTL,
            "message": "This is public (it goes out to real followers). Show the preview to a human, then call the same "
                       "tool again with the same arguments plus confirm_token to go ahead."}


# --------------------------------------------------------------------------- audit log queries
AUDIT_COLUMNS = ("id", "at", "actor", "token_id", "tool", "action", "risk", "method", "path", "status", "input_hash",
                 "created_ids", "result", "detail")


def audit_query(params: dict):
    q = select(AuditLog)
    if params.get("actor"):
        q = q.where(AuditLog.actor == params["actor"])
    if params.get("token_id"):
        q = q.where(AuditLog.token_id == params["token_id"])
    if params.get("risk"):
        q = q.where(AuditLog.risk.in_(str(params["risk"]).split(",")))
    if params.get("method"):
        q = q.where(AuditLog.method.in_(str(params["method"]).upper().split(",")))
    st = params.get("status")
    if st == "ok":
        q = q.where(AuditLog.status < 400)
    elif st == "error":
        q = q.where(AuditLog.status >= 400)
    elif st == "denied":
        q = q.where(AuditLog.status.in_((401, 403, 423, 428, 429)))
    if params.get("q"):
        like = f"%{params['q']}%"
        q = q.where(AuditLog.path.ilike(like) | AuditLog.detail.ilike(like) | AuditLog.tool.ilike(like) | AuditLog.action.ilike(like))
    for k, op in (("since", "ge"), ("until", "lt")):
        v = params.get(k)
        if v:
            try:
                d = datetime.fromisoformat(str(v).replace("Z", ""))
            except ValueError:
                continue
            q = q.where(AuditLog.at >= d if op == "ge" else AuditLog.at < d)
    return q.order_by(AuditLog.id.desc())


def audit_row(a: AuditLog, iso) -> dict:
    return {"id": a.id, "at": iso(a.at), "actor": a.actor, "token_id": a.token_id, "tool": a.tool, "action": a.action or "",
            "risk": a.risk or "", "method": a.method, "path": a.path, "status": a.status, "input_hash": a.input_hash or "",
            "created_ids": list(a.created_ids or []), "result": a.result or "", "detail": a.detail}


def audit_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(AUDIT_COLUMNS)
    for r in rows:
        w.writerow([" ".join(r[c]) if isinstance(r[c], list) else r[c] for c in AUDIT_COLUMNS])
    return buf.getvalue()


def created_ids_of(data) -> list[str]:
    """Ids a successful write created or touched (post targets, media, posts, ideas…), for the audit trail."""
    out: list[str] = []
    if isinstance(data, dict):
        for k in ("id",):
            if isinstance(data.get(k), str):
                out.append(data[k])
        for k in ("targets", "media", "items", "created"):
            for x in data.get(k) or []:
                if isinstance(x, dict) and isinstance(x.get("id"), str):
                    out.append(x["id"])
    elif isinstance(data, list):
        out += [x["id"] for x in data[:50] if isinstance(x, dict) and isinstance(x.get("id"), str)]
    return out[:50]


def token_expired_message(core, raw: str) -> str | None:
    """'Token expired' instead of the generic 401 when a known token has passed its expiry."""
    from .models import ApiToken
    h = hashlib.sha256(raw.encode()).hexdigest()
    with core.db.session() as s:
        row = s.query(ApiToken).filter_by(token_hash=h).first()
        if row and row.expires_at and row.expires_at < datetime.utcnow():
            return f"Token '{row.name}' expired on {row.expires_at:%d %b %Y %H:%M} UTC - create a new one in Settings › API"
    return None


def parse_expiry(v) -> datetime | None:
    """expires_at as ISO date/time, or expires_in_days."""
    if v in (None, "", 0, "0"):
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        return utcnow() + timedelta(days=int(v))
    d = datetime.fromisoformat(str(v).replace("Z", ""))
    return d.replace(tzinfo=None) if d.tzinfo is None else d.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
