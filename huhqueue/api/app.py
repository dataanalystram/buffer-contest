"""FastAPI app: the local UI + REST API (localhost), and the public edge app (media + OAuth callbacks)."""
from __future__ import annotations

import csv
import io
import json
import re
import secrets
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select

from .. import __version__
from .. import actions as reg
from ..connectors import PLANNED, ConnectorError, NotConfigured
from ..core import Core, utcnow
from ..models import KV, ApiToken, AuditLog, Channel, Comment, Event, Idea, Media, Post, PostTarget, PublishAttempt
from .. import safety
from ..security import (CSRF_COOKIE, SCOPES, clean_tools, SESSION_COOKIE, Auth, RateLimiter, install_log_redaction, apply_limits,
                        redact, required_scope)
from .. import teams
from ..services import community, meta_compliance, oauth, scheduling
from ..services import media as media_svc
from ..services import posts as posts_svc
from ..services.endcard import ffmpeg_available
from ..services.events import record
from ..tunnel import TunnelManager
from ..worker import Worker
from .actions_api import add_action_routes, add_assistant_routes
from .ai_tools_api import add_ai_tools_routes
from .agents import add_agent_routes
from .bulk_api import add_bulk_routes
from .safety_api import add_safety_routes
from .audience_api import add_audience_routes
from .webhooks_api import add_webhook_routes
from .context import add_context_routes
from .growth import add_growth_routes, add_public_growth_routes
from .channel_pages import add_channel_page_routes
from .threads_api import add_thread_routes
from .pro_content import add_content_routes, snapshot
from .reports_api import add_report_routes
from .teams_api import add_team_routes

STATIC = Path(__file__).resolve().parent.parent / "static"
UNAUDITED_UI = ("/api/notifications/read", "/api/validate", "/api/suggest-format", "/api/worker/tick", "/api/actions/", "/api/assistant/")
PUBLIC_API = {"/api/health", "/api/auth/status", "/api/auth/login", "/api/auth/setup"}
PUBLIC_PREFIXES = ("/api/invites/",)   # invite links: look up + accept (the token in the path is the credential)
LIBRARY_KINDS = {"hashtag_groups", "templates", "saved_replies", "tags"}
DEFAULT_LIBRARY = {
    "hashtag_groups": [{"id": "ai", "name": "AI news", "text": "#AI #ArtificialIntelligence #TechNews #HuhByte #AINews"}],
    "templates": [{"id": "launch", "name": "Launch", "text": "🚀 {product} just launched: {one_liner}\n\nWhy it matters: …\n\nFollow @huhbyte for daily AI news"},
                  {"id": "yc", "name": "YC launch", "text": "🟧 YC {batch}: {company} - {one_liner}\n\n3 things to know:\n1.\n2.\n3."},
                  {"id": "fact", "name": "AI fact", "text": "🤯 AI fact: …\n\nSave this for later."}],
    "saved_replies": [{"id": "thanks", "name": "Thanks", "text": "Thanks for watching! 🙌 Follow @huhbyte for more."}],
    "tags": [{"id": "news", "name": "News", "color": "#7C5CE0"}, {"id": "launch", "name": "Launch", "color": "#C9ACFA"},
             {"id": "yc", "name": "YC", "color": "#F59E0B"}, {"id": "fact", "name": "Fact", "color": "#10B981"}],
}


def request_origin(request: Request) -> str:
    """scheme://host the browser used, honouring a TLS-terminating proxy (X-Forwarded-Proto / -Host)."""
    h = request.headers
    proto = (h.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (h.get("x-forwarded-host") or h.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": {"message": message, **extra}}, status_code=status)


def create_app(core: Core | None = None, start_worker: bool = True) -> FastAPI:
    core = core or Core()
    install_log_redaction()
    auth = Auth(core)
    worker = Worker(core)
    tunnel = TunnelManager(core)
    core.tunnel = tunnel
    app = FastAPI(title="HuhQueue", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.core, app.state.auth, app.state.worker, app.state.tunnel = core, auth, worker, tunnel
    limiter = RateLimiter()

    def _audit(actor, token_id, tool, method, path, status, detail="", **extra):
        with core.db.session() as s:
            s.add(AuditLog(actor=actor, token_id=token_id, tool=tool, method=method, path=path, status=status,
                           detail=redact(detail)[:2000], **extra))

    def approval_actor(request: Request) -> str | None:
        """Agent tokens either publish directly, need human approval, or may only make drafts."""
        tok = getattr(request.state, "token", None)
        if getattr(request.state, "needs_approval", False):   # a contributor (team role or per-channel cell)
            return tok.name if tok else (getattr(request.state, "user_name", "") or "contributor")
        if not tok:
            return None
        if tok.require_approval:
            return tok.name
        if not tok.can("publish"):
            raise HTTPException(403, f"Token '{tok.name}' can only create drafts (no 'publish' scope)")
        return None

    if start_worker:
        @app.on_event("startup")
        def _start():  # pragma: no cover - exercised by `huhqueue serve`
            worker.start()

        @app.on_event("shutdown")
        def _stop():  # pragma: no cover
            worker.stop()
            tunnel.stop()

    # ------------------------------------------------------------------ security middleware
    async def _team_check(request: Request, who, path: str, scope: str):
        """Role + per-channel access for a teammate (session or user-bound token). Returns an error response or None."""
        body = None
        if request.method in ("POST", "PUT", "PATCH") and "json" in request.headers.get("content-type", ""):
            try:
                body = json.loads(await request.body() or b"null")
            except ValueError:
                body = None
        with core.db.session() as s:
            d = teams.authorize(s, who, request.method, path, dict(request.query_params), body, scope)
        if not d.ok:
            return _err(d.status, d.message)
        request.state.needs_approval = d.needs_approval
        request.state.visible_channels = d.visible
        teams.CURRENT_USER.set(who.id)
        return None

    async def _filtered(request: Request, resp):
        """List responses only carry the channels this teammate may see."""
        vis = getattr(request.state, "visible_channels", None)
        if vis is None or request.method != "GET" or request.url.path not in teams.FILTERED_LISTS or resp.status_code != 200:
            return resp
        raw = b"".join([chunk async for chunk in resp.body_iterator])
        try:
            data = teams.filter_listing(request.url.path, json.loads(raw), vis)
        except ValueError:
            return Response(raw, status_code=resp.status_code, media_type=resp.media_type)
        return JSONResponse(data, status_code=resp.status_code)

    # ------------------------------------------------------------------ agent requests (API tokens, MCP, CLI)
    confirms = safety.ConfirmStore()
    app.state.confirms = confirms

    async def _token_request(request: Request, call_next, info, path: str):
        """Every API-token request: kill switch, scope, tool allow-list, rate limit, channel scope, daily caps,
        MCP preview+confirm for public actions, then the audit row (action, risk, input hash, created ids)."""
        method = request.method
        scope = required_scope(method, path)
        tool = request.headers.get("x-huhq-tool", "")
        raw = b""
        body = None
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            raw = await request.body()
            if "json" in request.headers.get("content-type", "") and raw:
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
        action_id, risk = safety.risk_of(method, path, body)
        audit_extra = {"action": request.headers.get("x-huhq-action", "") or action_id, "risk": risk,
                       "input_hash": safety.input_hash(raw, request.url.query)}
        resp = None
        if (method, path) not in safety.PAUSE_ALLOWED and safety.is_paused(core):
            st = safety.state(core)
            resp = _err(423, f"Agents are paused (kill switch, by {st['paused_by'] or 'a human'}"
                             f"{': ' + st['reason'] if st['reason'] else ''}). A person can resume them in Settings › API & MCP agents.",
                        code="agents_paused")
        elif not info.can(scope):
            resp = _err(403, f"Token '{info.name}' lacks the '{scope}' scope")
        elif tool and info.tools and tool not in info.tools:
            resp = _err(403, f"Token '{info.name}' may not use the tool '{tool}'")
        elif not limiter.allow(info.id or "env", info.rate_per_min):
            resp = _err(429, f"Rate limit: {info.rate_per_min} requests/minute for this token")
        if resp is None and info.allowed_channel_ids and not path.startswith(safety.RUNNER_PREFIXES):
            with core.db.session() as s:
                named, via_ids = safety.scope_refs(s, method, path, dict(request.query_params), body)
            allowed = set(info.allowed_channel_ids)
            outside = named - allowed
            if via_ids - allowed:
                # a post / target / comment on another channel answers exactly like one that doesn't exist (no probing)
                resp = _err(404, "Not found")
            elif outside:
                resp = _err(403, f"Token '{info.name}' is limited to {len(info.allowed_channel_ids)} channel(s); "
                                 f"not allowed: {', '.join(sorted(outside))}", code="channel_scope")
        counted = method != "GET" and not path.startswith(safety.RUNNER_PREFIXES)
        if resp is None and counted and (info.max_actions_per_day or (risk == "public" and info.max_public_per_day)):
            used = safety.used_today(core, info.id)
            if info.max_actions_per_day and used["actions"] >= info.max_actions_per_day:
                resp = _err(429, f"Daily cap: token '{info.name}' may make {info.max_actions_per_day} changes a day "
                                 f"(used {used['actions']}). It resets at midnight ({core.prefs().get('timezone') or 'UTC'}).",
                            code="daily_action_cap")
            elif risk == "public" and info.max_public_per_day and used["public"] >= info.max_public_per_day:
                resp = _err(429, f"Daily cap: token '{info.name}' may make {info.max_public_per_day} public actions a day "
                                 f"(used {used['public']}).", code="daily_public_cap")
        if resp is None and tool and risk == "public" and not info.auto_confirm \
                and not (info.require_approval and (action_id or "").startswith("post.")):
            sig = safety.ConfirmStore.signature(info.id or info.name, method, path, raw)
            if not confirms.take(request.headers.get(safety.CONFIRM_HEADER, ""), sig):
                prev = safety.preview_for(method, path, body, action_id)
                resp = JSONResponse(safety.needs_confirmation_body(prev, confirms.issue(sig, prev)), status_code=428)
                audit_extra["result"] = "needs_confirmation"
        if resp is None:
            request.state.via, request.state.token = "token", info
            denied = None
            if info.user is not None:   # a teammate's token: their role and channel access apply too
                request.state.user, request.state.role_scopes = info.user, info.scopes
                denied = await _team_check(request, info.user, path, scope)
            if info.allowed_channel_ids:
                vis = getattr(request.state, "visible_channels", None)
                allowed = set(info.allowed_channel_ids)
                request.state.visible_channels = allowed if vis is None else set(vis) & allowed
            resp = denied or await _filtered(request, await call_next(request))
            if method != "GET" and resp.status_code < 400 and "json" in (resp.headers.get("content-type") or ""):
                chunks = [c async for c in resp.body_iterator]
                payload = b"".join(chunks)
                try:
                    data = json.loads(payload or b"null")
                    audit_extra["created_ids"] = safety.created_ids_of(data)
                    if isinstance(data, dict):
                        st = data.get("status") or ("pending_approval" if any(
                            (t or {}).get("status") == "pending_approval" for t in data.get("targets") or []) else "")
                        audit_extra["result"] = str(st)[:40]
                except ValueError:
                    pass
                headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
                resp = Response(payload, status_code=resp.status_code, headers=headers, media_type=resp.media_type)
        if method != "GET" or tool:
            audit_extra.setdefault("result", "ok" if resp.status_code < 400 else "denied" if resp.status_code in (401, 403, 423, 428, 429) else "error")
            _audit(info.name, info.id, tool, method, path, resp.status_code, **audit_extra)
        return _secure_headers(resp)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        needs = path.startswith("/api/") and path not in PUBLIC_API and not path.startswith("/api/docs") \
            and path != "/api/openapi.json" and not path.startswith(PUBLIC_PREFIXES)
        needs = needs or path.startswith("/files/")
        request.state.via = None
        request.state.token = None
        request.state.user = None
        request.state.needs_approval = False
        request.state.visible_channels = None
        if needs:
            bearer = request.headers.get("authorization", "")
            info = auth.bearer(bearer[7:].strip()) if bearer.lower().startswith("bearer ") else None
            sess = None if info else auth.session(request.cookies.get(SESSION_COOKIE))
            if info:
                return await _token_request(request, call_next, info, path)
            elif sess:
                who = sess[1]
                if who.manager:   # hosted installs follow the URL the UI is served on (owners/admins only: it sets media URLs)
                    core.note_origin(request_origin(request))
                request.state.via, request.state.user = "session", who
                request.state.user_name, request.state.role_scopes = who.name, who.scopes
                if request.method not in ("GET", "HEAD", "OPTIONS"):
                    sent = request.headers.get("x-csrf-token", "")
                    if not secrets.compare_digest(sent, auth.csrf_for(request.cookies[SESSION_COOKIE])):
                        return _err(403, "CSRF check failed - reload the page")
                denied = await _team_check(request, who, path, required_scope(request.method, path))
                if denied:
                    return _secure_headers(denied)
                resp = await _filtered(request, await call_next(request))
                if request.method not in ("GET", "HEAD", "OPTIONS") and not path.startswith(UNAUDITED_UI):
                    _audit(who.name or "ui", None, request.headers.get("x-huhq-action", ""), request.method, path, resp.status_code)
                return _secure_headers(resp)
            else:
                if bearer.lower().startswith("bearer "):
                    msg = safety.token_expired_message(core, bearer[7:].strip())
                    if msg:
                        return _err(401, msg, code="token_expired")
                return _err(401, "Login required")
        resp = await call_next(request)
        return _secure_headers(resp)

    def _secure_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp

    @app.exception_handler(posts_svc.PostError)
    async def _post_error(_, e: posts_svc.PostError):
        return JSONResponse({"detail": e.detail()}, status_code=e.status)

    @app.exception_handler(oauth.OAuthFailure)
    async def _oauth_error(_, e: oauth.OAuthFailure):
        return _err(400, str(e))

    @app.exception_handler(ConnectorError)
    async def _conn_error(_, e: ConnectorError):
        return _err(400 if isinstance(e, NotConfigured) else 502, e.message, code=e.code)

    @app.exception_handler(media_svc.MediaError)
    async def _media_error(_, e: media_svc.MediaError):
        return _err(400, str(e))

    def _set_session(resp: Response, user_id: str | None = None, request: Request | None = None) -> Response:
        tok, csrf = auth.new_session(user_id, ip=(request.client.host if request and request.client else ""),
                                     user_agent=request.headers.get("user-agent", "") if request else "")
        secure = core.settings.cookie_secure
        resp.set_cookie(SESSION_COOKIE, tok, httponly=True, samesite="strict", secure=secure, max_age=14 * 86400)
        resp.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="strict", secure=secure, max_age=14 * 86400)
        return resp

    # ------------------------------------------------------------------ auth
    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "agents_paused": safety.state(core)["paused"], "worker": worker.heartbeat()}

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        sess = auth.session(request.cookies.get(SESSION_COOKIE))
        out = {"has_password": auth.has_password(), "logged_in": bool(sess)}
        if sess:
            out["user"] = sess[1].to_dict()
        return out

    @app.post("/api/auth/setup")
    def auth_setup(request: Request, payload: dict = Body(...)):
        if auth.has_password():
            return _err(409, "A password is already set - log in instead")
        try:
            auth.set_password(payload.get("password", ""))
        except ValueError as e:
            return _err(400, str(e))
        return _set_session(JSONResponse({"ok": True}), None, request)

    @app.post("/api/auth/login")
    def auth_login(request: Request, payload: dict = Body(...)):
        """{password} = the Owner's app password; {email, password} = a teammate. Add {code} when 2FA is on."""
        try:
            u = auth.authenticate(payload.get("password", ""), str(payload.get("email") or ""))
        except PermissionError as e:
            return _err(429, str(e))
        if not u:
            return _err(401, "Wrong email or password" if payload.get("email") else "Wrong password")
        if u.totp_enabled:
            code = str(payload.get("code") or "").strip()
            if not code:
                return _err(401, "Enter the 6-digit code from your authenticator app (or a recovery code)", needs_2fa=True)
            with core.db.session() as s:
                ok = teams.verify_totp(core, s, s.get(teams.User, u.id), code)
            if not ok:
                auth.fail()
                return _err(401, "That code didn't match", needs_2fa=True)
        return _set_session(JSONResponse({"ok": True}), u.id, request)

    @app.post("/api/auth/logout")
    def auth_logout(request: Request):
        auth.end_session(request.cookies.get(SESSION_COOKIE))
        r = JSONResponse({"ok": True})
        r.delete_cookie(SESSION_COOKIE)
        r.delete_cookie(CSRF_COOKIE)
        return r

    @app.post("/api/auth/password")
    def auth_password(request: Request, payload: dict = Body(...)):
        """Change your own password; your other sessions are logged out."""
        from ..security import MIN_PASSWORD, hash_password, verify_password
        who = getattr(request.state, "user", None)
        if not who or request.state.via != "session":
            return _err(403, "Passwords are changed by the person, in the web UI")
        new = payload.get("new", "")
        with core.db.session() as s:
            u = s.get(teams.User, who.id)
            if not verify_password(payload.get("current", ""), u.password_hash or ""):
                return _err(401, "Current password is wrong")
            if len(new or "") < MIN_PASSWORD:
                return _err(400, f"Password must be at least {MIN_PASSWORD} characters")
            u.password_hash = new_hash = hash_password(new)
            boot = teams.owner(s)
            is_boot = bool(boot and boot.id == u.id)
            teams.revoke_sessions(s, u.id)
        if is_boot:   # keep the legacy app-password row in step for the bootstrap owner
            auth._save(hash=new_hash, epoch=int(auth._state().get("epoch", 0)) + 1)
        return _set_session(JSONResponse({"ok": True}), who.id, request)

    @app.get("/api/tokens")
    def tokens_list():
        with core.db.session() as s:
            return {"scopes": SCOPES, "tools": reg.mcp_tool_names(), "tokens": [
                {"id": t.id, "name": t.name, "prefix": t.prefix, "scopes": t.scopes, "tools": t.tools,
                 "require_approval": t.require_approval, "rate_per_min": t.rate_per_min,
                 "max_public_per_day": t.max_public_per_day or 0, "max_actions_per_day": t.max_actions_per_day or 0,
                 "allowed_channel_ids": list(t.allowed_channel_ids or []), "auto_confirm": bool(t.auto_confirm),
                 "expires_at": posts_svc.iso(t.expires_at), "expired": bool(t.expires_at and t.expires_at < utcnow()),
                 "used_today": safety.used_today(core, t.id),
                 "created_at": posts_svc.iso(t.created_at), "last_used_at": posts_svc.iso(t.last_used_at)}
                for t in s.scalars(select(ApiToken))]}

    @app.get("/api/me")
    def me(request: Request):
        tok = getattr(request.state, "token", None)
        if tok:
            return tok.to_dict()
        who = getattr(request.state, "user", None)
        return {"name": who.name if who else "ui", "scopes": who.scopes if who else list(SCOPES), "tools": [],
                "require_approval": bool(who and who.role == "contributor"), "user": who.to_dict() if who else None}

    @app.post("/api/tokens")
    def tokens_create(request: Request, payload: dict = Body(default={})):
        who = getattr(request.state, "user", None)
        # a teammate's token is capped by their role; the bootstrap owner's stay unbound, exactly as before teams
        uid = who.id if who and who.email != teams.OWNER_EMAIL else None
        try:
            row, raw = auth.mint_token(payload.get("name", "agent"), payload.get("scopes"), payload.get("tools"),
                                       bool(payload.get("require_approval", True)), int(payload.get("rate_per_min", 60)), user_id=uid,
                                       limits=payload)
        except ValueError as e:
            return _err(400, f"Bad token setting: {e}")
        _audit("ui", None, "", "POST", "/api/tokens", 200, f"minted token {row.name} scopes={row.scopes}")
        return {"id": row.id, "name": row.name, "token": raw, "scopes": row.scopes, "tools": row.tools,
                "require_approval": row.require_approval, "max_public_per_day": row.max_public_per_day or 0,
                "max_actions_per_day": row.max_actions_per_day or 0, "allowed_channel_ids": list(row.allowed_channel_ids or []),
                "auto_confirm": bool(row.auto_confirm), "expires_at": posts_svc.iso(row.expires_at),
                "note": "Copy it now - it is shown only once."}

    @app.patch("/api/tokens/{tid}")
    def tokens_patch(tid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            row = s.get(ApiToken, tid)
            if not row:
                return _err(404, "Token not found")
            if "scopes" in payload:
                row.scopes = [x for x in payload["scopes"] if x in SCOPES]
            if "tools" in payload:
                try:
                    row.tools = clean_tools(payload["tools"])
                except ValueError as e:
                    return _err(400, str(e))
            if "require_approval" in payload:
                row.require_approval = bool(payload["require_approval"])
            if "rate_per_min" in payload:
                row.rate_per_min = max(1, int(payload["rate_per_min"]))
            try:
                apply_limits(row, payload)
            except ValueError as e:
                return _err(400, f"Bad token limit: {e}")
            return {"ok": True}

    @app.delete("/api/tokens/{tid}")
    def tokens_delete(tid: str):
        with core.db.session() as s:
            row = s.get(ApiToken, tid)
            if row:
                s.delete(row)
        return {"ok": True}

    # ------------------------------------------------------------------ providers & channels
    @app.get("/api/providers")
    def providers(request: Request):
        out = [c.describe() for c in core.connectors.values()]
        common = posts_svc.common_options(core.prefs())
        for d in out:
            d["common_options"] = common
        return {"connectors": out, "planned": PLANNED, "redirect_uris": {k: core.redirect_uri(k, request_origin(request)) for k in core.connectors}}

    def channel_dict(ch: Channel, s=None) -> dict:
        meta = ch.meta or {}
        d = {"id": ch.id, "provider": ch.provider, "external_id": ch.external_id, "username": ch.username,
             "display_name": ch.display_name, "avatar_url": ch.avatar_url, "status": ch.status,
             "status_detail": ch.status_detail, "timezone": ch.timezone, "daily_limit": ch.daily_limit,
             "paused": ch.paused, "scopes": ch.scopes, "token_expires_at": posts_svc.iso(ch.token_expires_at),
             "followers_count": meta.get("followers_count"), "media_count": meta.get("media_count"),
             "account_type": meta.get("account_type"), "profile_synced_at": meta.get("profile_synced_at"),
             "auth_mode": meta.get("auth_mode") or ("ig_login" if ch.provider == "instagram" else ""),
             "comments_note": meta.get("comments_note") or "",
             "slots": [{"weekday": x.weekday, "time": x.time} for x in sorted(ch.slots, key=lambda z: (z.weekday, z.time))],
             "created_at": posts_svc.iso(ch.created_at)}
        if s is not None:
            counts = dict(s.execute(select(PostTarget.status, func.count()).where(PostTarget.channel_id == ch.id)
                                    .group_by(PostTarget.status)).all())
            d["counts"] = counts
        return d

    def get_channel(s, cid: str) -> Channel:
        ch = s.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return ch

    @app.get("/api/channels")
    def channels():
        with core.db.session() as s:
            return [channel_dict(c, s) for c in s.scalars(select(Channel).order_by(Channel.created_at))]

    @app.get("/api/channels/{cid}")
    def channel_get(cid: str):
        with core.db.session() as s:
            return channel_dict(get_channel(s, cid), s)

    @app.post("/api/connect/{provider}")
    def connect(provider: str, request: Request):
        if provider not in core.connectors:
            return _err(404, f"No connector for {provider} yet")
        return oauth.start(core, provider, request_origin(request))

    @app.post("/api/connect/{provider}/token")
    def connect_token(provider: str, payload: dict = Body(...)):
        cid = oauth.connect_token(core, provider, payload.get("access_token", ""), payload)
        return channel_get(cid)

    @app.post("/api/oauth/complete")
    def oauth_paste(payload: dict = Body(...)):
        return channel_get(oauth.complete_from_url(core, payload.get("url", "")))

    @app.patch("/api/channels/{cid}")
    def channel_update(cid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            ch = get_channel(s, cid)
            if "timezone" in payload:
                from zoneinfo import ZoneInfo
                try:
                    ZoneInfo(payload["timezone"])
                except Exception:
                    return _err(400, f"Unknown timezone {payload['timezone']}")
                ch.timezone = payload["timezone"]
            if "daily_limit" in payload:
                ch.daily_limit = max(0, int(payload["daily_limit"]))
            if "paused" in payload:
                ch.paused = bool(payload["paused"])
            if "display_name" in payload:
                ch.display_name = payload["display_name"]
            s.flush()
            scheduling.reflow(s, ch, utcnow())
            return channel_dict(ch, s)

    @app.post("/api/channels/{cid}/refresh-profile")
    def channel_refresh_profile(cid: str):
        oauth.refresh_profile(core, cid)
        return channel_get(cid)

    @app.post("/api/channels/{cid}/refresh-token")
    def channel_refresh_token(cid: str):
        with core.db.session() as s:
            ch = get_channel(s, cid)
            b = core.connector(ch.provider).refresh(core.channel_auth(ch))
            if not b:
                return _err(400, "This network has no refresh token - reconnect instead")
            core.store_tokens(ch, b)
            ch.status = "active"
            return channel_dict(ch, s)

    @app.delete("/api/channels/{cid}")
    def channel_delete(cid: str):
        oauth.disconnect(core, cid)
        return {"ok": True}

    @app.get("/api/channels/{cid}/account-info")
    def channel_account_info(cid: str):
        with core.db.session() as s:
            ch = get_channel(s, cid)
            return core.connector(ch.provider).account_info(core.channel_auth(ch))

    @app.get("/api/channels/{cid}/history")
    def channel_history(cid: str, limit: int = 24, after: str = ""):
        """Past posts on the network itself (also ones made outside HuhQueue) - the Sent/History grid."""
        with core.db.session() as s:
            ch = get_channel(s, cid)
            auth_ = core.channel_auth(ch)
            provider = ch.provider
        conn = core.connector(provider)
        data = conn.list_media(auth_, limit=limit, after=after)
        return {"supported": conn.describe()["history"], **data}

    @app.get("/api/channels/{cid}/insights")
    def channel_insights(cid: str, days: int = 28):
        with core.db.session() as s:
            ch = get_channel(s, cid)
            auth_ = core.channel_auth(ch)
            provider = ch.provider
        return core.connector(provider).account_insights(auth_, days=days) or {"totals": {}, "series": {}}

    # ------------------------------------------------------------------ developer-app keys (UI-only, never via agent tokens)
    def _ui_only(request: Request):
        if request.state.via != "session":
            raise HTTPException(403, "App keys can only be managed from the web UI")

    @app.get("/api/app-keys")
    def app_keys_get(request: Request):
        _ui_only(request)
        return core.app_keys(request_origin(request))

    @app.put("/api/app-keys/{provider}")
    def app_keys_put(provider: str, request: Request, payload: dict = Body(...)):
        _ui_only(request)
        try:
            out = core.set_app_keys(provider, payload, request_origin(request))
        except KeyError as e:
            return _err(404, str(e.args[0]))
        _audit("ui", None, "", "PUT", f"/api/app-keys/{provider}", 200, f"updated {provider} app keys")
        return out

    @app.get("/api/channels/{cid}/slots")
    def slots_get(cid: str):
        return channel_get(cid)["slots"]

    @app.put("/api/channels/{cid}/slots")
    def slots_put(cid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            ch = get_channel(s, cid)
            try:
                scheduling.set_slots(s, ch, payload.get("slots", []))
            except (ValueError, KeyError) as e:
                return _err(400, str(e))
            s.flush()
            scheduling.reflow(s, ch, utcnow())
            return channel_dict(ch, s)

    @app.post("/api/channels/{cid}/slots/preset")
    def slots_preset(cid: str, payload: dict = Body(...)):
        try:
            slots = scheduling.preset_slots(payload.get("preset", "huhbyte"))
        except ValueError as e:
            return _err(400, str(e))
        return slots_put(cid, {"slots": slots})

    # ------------------------------------------------------------------ media
    async def _save_upload(f: UploadFile, alt: str = "", crop: str = "") -> Media:
        with tempfile.NamedTemporaryFile(delete=False, dir=core.settings.media_dir, prefix=".up-") as tmp:
            while chunk := await f.read(1024 * 1024):
                tmp.write(chunk)
        if crop == "square":
            media_svc.square_crop(Path(tmp.name))
        return media_svc.save_file(core, Path(tmp.name), f.filename or "upload", f.content_type, alt)

    @app.post("/api/media")
    async def media_upload(files: list[UploadFile] = File(...), alt_text: str = Form(""), crop: str = Form("")):
        out, errors = [], []
        for f in files:
            name = (f.filename or "").lower()
            if name.endswith(".zip"):
                out += await _unzip(f, errors)
                continue
            try:
                out.append(media_svc.media_dict(core, await _save_upload(f, alt_text, crop)))
            except media_svc.MediaError as e:
                errors.append(f"{f.filename}: {e}")
        return {"media": out, "errors": errors}

    async def _unzip(f: UploadFile, errors: list) -> list:
        """Bulk upload: every image/video inside a .zip becomes a media item (folders flattened, sorted)."""
        data = await f.read()
        out = []
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            errors.append(f"{f.filename}: not a valid zip")
            return out
        for info in sorted(z.infolist(), key=lambda i: i.filename):
            if info.is_dir() or "/." in "/" + info.filename or info.filename.startswith("__MACOSX"):
                continue
            name = Path(info.filename).name
            try:
                media_svc.classify(name)
            except media_svc.MediaError:
                continue
            with tempfile.NamedTemporaryFile(delete=False, dir=core.settings.media_dir, prefix=".zip-") as tmp:
                with z.open(info) as src:
                    while chunk := src.read(1024 * 1024):
                        tmp.write(chunk)
            try:
                out.append(media_svc.media_dict(core, media_svc.save_file(core, Path(tmp.name), name)))
            except media_svc.MediaError as e:
                errors.append(f"{name}: {e}")
        return out

    @app.post("/api/media/from-url")
    def media_from_url(payload: dict = Body(...)):
        return media_svc.media_dict(core, media_svc.download(core, payload.get("url", ""), payload.get("filename")))

    @app.get("/api/media")
    def media_list(q: str = "", folder: str | None = None, tag: str = "", kind: str = "", unused: bool = False,
                   duplicates: bool = False, limit: int = 500):
        """Media library: search (filename, title, alt text, tags), folder, tag, kind, unused, duplicates (exact + near)."""
        return app.state.list_media(q, folder, tag, kind, unused, duplicates, limit)

    @app.patch("/api/media/{mid}")
    def media_patch(mid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            m = s.get(Media, mid)
            if not m:
                return _err(404, "Media not found")
            if "alt_text" in payload:
                m.alt_text = payload["alt_text"][:1000]
            if "title" in payload:
                m.title = str(payload["title"] or "")[:200]
            if "folder" in payload:
                m.folder = "/".join(x.strip() for x in str(payload["folder"] or "").split("/") if x.strip())[:200]
            if "tags" in payload:
                m.tags = sorted({str(x).strip().lower()[:40] for x in payload["tags"] or [] if str(x).strip()})
            s.flush()
            return app.state.media_dict(m)

    @app.delete("/api/media/{mid}")
    def media_delete(mid: str, request: Request, force: bool = False):
        from .pro_content import filter_usage, media_usage
        with core.db.session() as s:
            m = s.get(Media, mid)
            if m:
                use = media_usage(s, mid)
                if use["uses"] and not force:   # block on the FULL usage; list only the caller's visible channels' posts
                    shown = filter_usage(use, getattr(request.state, "visible_channels", None), recount=False)
                    return _err(409, f"This file is used in {use['uses']} post(s) or idea(s) - delete with force=true", usage=shown)
                media_svc.delete(core, m)
                s.delete(m)
        return {"ok": True}

    @app.get("/files/{mid}")
    def file_get(mid: str):
        with core.db.session() as s:
            m = s.get(Media, mid)
            if not m:
                raise HTTPException(404)
            return FileResponse(core.settings.media_dir / m.stored_name, media_type=m.mime, filename=m.filename)

    # ------------------------------------------------------------------ posts
    @app.post("/api/validate")
    def validate(payload: dict = Body(...)):
        out = {}
        with core.db.session() as s:
            chans = posts_svc.resolve_channels(s, payload.get("channel_ids") or [])
            per = {c.get("channel_id"): c for c in payload.get("channels") or []}
            base = posts_svc.load_media(s, payload.get("media_ids") or [])
            for ch in chans:
                spec = per.get(ch.id, {})
                medias = posts_svc.load_media(s, spec["media_ids"]) if spec.get("media_ids") is not None else base
                cap = spec.get("caption") if spec.get("caption") is not None else payload.get("caption", "")
                opts = posts_svc._merge_options(core, ch, {**(payload.get("options") or {}), **(spec.get("options") or {})})
                out[ch.id] = posts_svc.validate_one(core, ch, cap, medias, opts, payload.get("topic_key") or "",
                                                    exclude_post_id=payload.get("post_id"),
                                                    exclude_target_id=payload.get("target_id"))
        return {"issues": out}

    @app.post("/api/suggest-format")
    def suggest_format(payload: dict = Body(...)):
        """Per channel: the format we suggest (video → Reel, text-heavy → carousel, …) and why."""
        with core.db.session() as s:
            return posts_svc.suggest_formats(core, s, payload.get("channel_ids") or [], payload.get("caption", ""),
                                             payload.get("media_ids") or [])

    @app.post("/api/posts")
    def post_create(request: Request, payload: dict = Body(...)):
        src = payload.pop("source", None) or ("api" if request.state.via == "token" else "ui")
        actor = approval_actor(request) if (payload.get("when") or "queue") != "draft" else None
        return posts_svc.create_post(core, payload, source=src, approval_by=actor)

    @app.get("/api/posts")
    def post_list(status: str | None = None, channel_id: str | None = None, provider: str | None = None,
                  tag: str | None = None, q: str | None = None, start: str | None = None, end: str | None = None,
                  post_type: str | None = None, campaign: str | None = None, undated: bool = False, limit: int = 300):
        """Filters combine with AND; comma-separated values inside one filter combine with OR."""
        with core.db.session() as s:
            qry = select(PostTarget).join(Channel, Channel.id == PostTarget.channel_id).join(Post, Post.id == PostTarget.post_id)
            if status:
                qry = qry.where(PostTarget.status.in_(status.split(",")))
            if channel_id:
                qry = qry.where(PostTarget.channel_id.in_(channel_id.split(",")))
            if provider:
                qry = qry.where(Channel.provider.in_(provider.split(",")))
            if campaign:
                qry = qry.where(Post.campaign.in_(campaign.split(",")))
            if undated:
                qry = qry.where(PostTarget.scheduled_at.is_(None), PostTarget.published_at.is_(None))
            if start:
                qry = qry.where(or_(PostTarget.scheduled_at >= posts_svc.parse_when(start, core.prefs()["timezone"]),
                                    PostTarget.published_at >= posts_svc.parse_when(start, core.prefs()["timezone"])))
            if end:
                e = posts_svc.parse_when(end, core.prefs()["timezone"])
                qry = qry.where(or_(PostTarget.scheduled_at < e, PostTarget.published_at < e))
            # every filter runs in SQL (or as an id pre-filter) BEFORE the LIMIT, so no matching row is silently dropped
            if tag:
                qry = qry.where(or_(*[Post.tags.like(f'%{json.dumps(x.strip())}%') for x in tag.split(",") if x.strip()]))
            if q:
                like = f"%{q}%"
                qry = qry.where(or_(func.coalesce(PostTarget.caption_override, Post.caption).ilike(like), Post.note.ilike(like)))
            if post_type:
                types = set(post_type.split(","))
                kinds = dict(s.execute(select(Media.id, Media.kind)).all())
                ids = []
                for tid, prov, opts, ov, mids in s.execute(select(PostTarget.id, Channel.provider, PostTarget.options,
                                                                  PostTarget.media_override, Post.media_ids)
                                                           .join(Channel, Channel.id == PostTarget.channel_id)
                                                           .join(Post, Post.id == PostTarget.post_id)):
                    media = [{"kind": kinds.get(m)} for m in (ov if ov is not None else (mids or [])) if m in kinds]
                    fmt = posts_svc.post_format(prov, opts or {}, media)
                    # "image" is the older UI value (= no video); the rest are formats: post|carousel|reel|story|short|video
                    if fmt in types or ("image" in types and not any(m["kind"] == "video" for m in media)):
                        ids.append(tid)
                qry = qry.where(PostTarget.id.in_(ids))
            newest_first = bool(status and set(status.split(",")) <= {"sent", "failed", "needs_action"})
            key = func.coalesce(PostTarget.published_at, PostTarget.scheduled_at, PostTarget.updated_at)
            qry = qry.order_by(key.desc() if newest_first else key.asc())
            rows = list(s.scalars(qry.limit(max(1, min(limit, 5000)))))
            out = [posts_svc.serialize_target(core, t) for t in rows]
        sent = {"sent", "failed", "needs_action"}
        out.sort(key=lambda t: ((t["published_at"] or t["updated_at"] or "") if t["status"] in sent else (t["scheduled_at"] or "9999")),
                 reverse=bool(status and set(status.split(",")) <= sent))
        return out

    @app.get("/api/campaigns")
    def campaigns():
        from ..services import growth as growth_svc
        return growth_svc.list_campaigns(core)   # [{id, name, color, dates, posts, stats}]

    @app.get("/api/views")
    def views_get():
        with core.db.session() as s:
            row = s.get(KV, "saved_views")
            return row.value.get("items", []) if row else []

    @app.put("/api/views")
    def views_put(payload: dict = Body(...)):
        items = []
        for v in (payload.get("items") or [])[:50]:
            name = str(v.get("name") or "").strip()[:60]
            if name:
                items.append({"id": v.get("id") or secrets.token_hex(4), "name": name, "page": str(v.get("page") or "calendar")[:20],
                              "filters": {k: str(x)[:400] for k, x in (v.get("filters") or {}).items() if x not in (None, "")}})
        with core.db.session() as s:
            row = s.get(KV, "saved_views") or KV(key="saved_views", value={})
            row.value = {"items": items}
            s.merge(row)
        return items

    def _target(s, tid) -> PostTarget:
        t = s.get(PostTarget, tid)
        if not t:
            raise HTTPException(404, "Post not found")
        return t

    @app.get("/api/targets/{tid}")
    def target_get(tid: str):
        with core.db.session() as s:
            return posts_svc.serialize_target(core, _target(s, tid), validate=True)

    @app.patch("/api/targets/{tid}")
    def target_patch(tid: str, request: Request, payload: dict = Body(...)):
        if {"caption", "media_ids", "options", "tags", "note", "campaign"} & set(payload):
            with core.db.session() as s:
                t = s.get(PostTarget, tid)
                if t:
                    snapshot(core, s, t.post, getattr(request.state, "user_name", "") or ("token" if request.state.via == "token" else "ui"))
        actor = approval_actor(request) if payload.get("when") and payload.get("when") != "draft" else None
        return posts_svc.update_target(core, tid, payload, approval_by=actor)

    @app.delete("/api/targets/{tid}")
    def target_delete(tid: str):
        posts_svc.delete_target(core, tid)
        return {"ok": True}

    @app.post("/api/targets/{tid}/publish-now")
    def target_now(tid: str, request: Request, payload: dict = Body(default={})):
        actor = approval_actor(request)
        out = posts_svc.update_target(core, tid, {"when": "now", "override_rate_limit": payload.get("override_rate_limit", False)},
                                      approval_by=actor)
        if actor:
            return out
        if payload.get("run_worker", True):
            worker.tick()
        return target_get(tid) if out else out

    @app.post("/api/targets/{tid}/approve")
    def target_approve(tid: str, request: Request, payload: dict = Body(default={})):
        if request.state.via == "token":
            return _err(403, "Approvals are made by a human in the app")
        who = getattr(request.state, "user", None)
        name = who.name if who else "ui"
        note = str((payload or {}).get("note") or "").strip()[:1000]
        out = posts_svc.approve(core, tid, name, bool(payload.get("override_rate_limit")))
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            if t:
                t.approval = {**(t.approval or {}), "decided_by": name, "decision": "approved", "decision_note": note}
                if note:
                    record(core, s, f"Approved by {name}: {note}", target_id=tid, channel_id=t.channel_id, kind="approval")
                teams.notify_user(s, (t.approval or {}).get("requested_by_user_id"),
                                  f"{name} approved your post" + (f": {note}" if note else ""), t.id, t.channel_id, level="success")
        _audit(name, None, "", "POST", f"/api/targets/{tid}/approve", 200, "approved" + (f": {note}" if note else ""))
        return target_get(tid)

    @app.post("/api/targets/{tid}/reject")
    def target_reject(tid: str, request: Request, payload: dict = Body(default={})):
        if request.state.via == "token":
            return _err(403, "Approvals are made by a human in the app")
        note = str((payload or {}).get("note") or "").strip()[:1000]
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            if t and (t.approval or {}).get("kind") == "human" and not note:
                return _err(400, "Say what needs to change (a note is required when rejecting a teammate's post)")
        out = posts_svc.reject(core, tid, getattr(request.state, "user_name", "") or "ui")
        who = getattr(request.state, "user", None)
        name = who.name if who else "ui"
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            if t:
                t.approval = {**(t.approval or {}), "reject_note": note, "decided_by": name, "decision": "rejected"}
                if note:
                    record(core, s, f"Changes requested: {note}", target_id=tid, channel_id=t.channel_id, notify=True, kind="approval")
                teams.notify_user(s, (t.approval or {}).get("requested_by_user_id"),
                                  f"{name} asked for changes" + (f": {note}" if note else ""), t.id, t.channel_id, level="warning")
        out = target_get(tid)
        _audit(name, None, "", "POST", f"/api/targets/{tid}/reject", 200, "rejected" + (f": {note}" if note else ""))
        return out

    @app.post("/api/targets/{tid}/retry")
    def target_retry(tid: str):
        with core.db.session() as s:
            t = _target(s, tid)
            if t.status != "failed":
                return _err(409, "Only failed posts can be retried")
            t.status, t.attempts, t.next_attempt_at, t.last_error = "scheduled", 0, None, ""
            if not t.scheduled_at or t.scheduled_at < utcnow():
                t.scheduled_at, t.schedule_mode = utcnow(), "now"
            record(core, s, "Retry requested", target_id=t.id, channel_id=t.channel_id)
        worker.tick()
        return target_get(tid)

    @app.post("/api/targets/{tid}/duplicate")
    def target_duplicate(tid: str, payload: dict = Body(default={})):
        """Duplicate as a draft - to the same channel, or to other channels (duplicate-to-channel)."""
        ids = payload.get("channel_ids")
        if not ids:
            return posts_svc.duplicate_target(core, tid)
        with core.db.session() as s:
            t = _target(s, tid)
            body = {"caption": t.caption, "media_ids": list(t.media_list), "channel_ids": ids, "tags": t.post.tags,
                    "when": payload.get("when", "draft")}
        return posts_svc.create_post(core, body)

    @app.post("/api/targets/{tid}/mark-posted")
    def target_mark_posted(tid: str, payload: dict = Body(default={})):
        with core.db.session() as s:
            t = _target(s, tid)
            t.status, t.published_at = "sent", utcnow()
            t.permalink = payload.get("permalink", t.permalink)
            record(core, s, "Marked as posted manually", level="success", target_id=t.id, channel_id=t.channel_id)
        return target_get(tid)

    @app.post("/api/targets/{tid}/refresh-insights")
    def target_insights(tid: str):
        worker.refresh_insights(utcnow(), target_id=tid)
        return target_get(tid)

    @app.get("/api/targets/{tid}/events")
    def target_events(tid: str):
        with core.db.session() as s:
            return [{"at": posts_svc.iso(e.at), "level": e.level, "message": e.message}
                    for e in s.scalars(select(Event).where(Event.target_id == tid).order_by(Event.id))]

    @app.get("/api/targets/{tid}/attempts")
    def target_attempts(tid: str):
        with core.db.session() as s:
            _target(s, tid)
            return [{"attempt": a.attempt, "started_at": posts_svc.iso(a.started_at),
                     "finished_at": posts_svc.iso(a.finished_at), "outcome": a.outcome, "error_code": a.error_code,
                     "error": a.error, "retry_after": a.retry_after, "http_status": a.http_status}
                    for a in s.scalars(select(PublishAttempt).where(PublishAttempt.target_id == tid)
                                       .order_by(PublishAttempt.id))]

    @app.post("/api/queue/{cid}/order")
    def queue_order(cid: str, payload: dict = Body(...)):
        """Drag-and-drop reorder: the queued posts keep the channel's slot times, in the new order."""
        ids = payload.get("ids") or []
        with core.db.session() as s:
            ch = get_channel(s, cid)
            rows = {t.id: t for t in s.scalars(select(PostTarget).where(
                PostTarget.channel_id == cid, PostTarget.status == "scheduled", PostTarget.schedule_mode == "queue"))}
            if set(ids) != set(rows):
                return _err(400, "Send every queued post id of this channel exactly once")
            times = sorted([t.scheduled_at for t in rows.values() if t.scheduled_at])
            for i, tid in enumerate(ids):
                t = rows[tid]
                t.prioritized = False
                t.scheduled_at = times[i] if i < len(times) else None
            s.flush()
            scheduling.reflow(s, ch, utcnow())
        return post_list(channel_id=cid, status="scheduled")

    # ------------------------------------------------------------------ ideas
    def idea_dict(i: Idea) -> dict:
        return {"id": i.id, "title": i.title, "body": i.body, "column": i.column, "tags": i.tags,
                "media_ids": i.media_ids, "position": i.position, "created_at": posts_svc.iso(i.created_at)}

    @app.get("/api/ideas")
    def ideas():
        with core.db.session() as s:
            return {"columns": core.prefs()["idea_columns"],
                    "ideas": [idea_dict(i) for i in s.scalars(select(Idea).order_by(Idea.position, Idea.created_at))]}

    @app.post("/api/ideas")
    def idea_create(payload: dict = Body(...)):
        with core.db.session() as s:
            i = Idea(title=payload.get("title", "")[:200], body=payload.get("body", ""), column=payload.get("column", "unassigned"),
                     tags=payload.get("tags", []), media_ids=payload.get("media_ids", []))
            s.add(i)
            s.flush()
            return idea_dict(i)

    @app.patch("/api/ideas/{iid}")
    def idea_patch(iid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            i = s.get(Idea, iid)
            if not i:
                return _err(404, "Idea not found")
            for k in ("title", "body", "column", "tags", "media_ids", "position"):
                if k in payload:
                    setattr(i, k, payload[k])
            return idea_dict(i)

    @app.delete("/api/ideas/{iid}")
    def idea_delete(iid: str):
        with core.db.session() as s:
            i = s.get(Idea, iid)
            if i:
                s.delete(i)
        return {"ok": True}

    @app.put("/api/idea-columns")
    def idea_columns(payload: dict = Body(...)):
        cols = [{"id": c.get("id") or c["label"].lower().replace(" ", "_"), "label": c["label"][:40]} for c in payload["columns"]]
        return core.update_prefs({"idea_columns": cols})["idea_columns"]

    # ------------------------------------------------------------------ community
    @app.get("/api/comments")
    def comments(channel_id: str | None = None, status: str | None = None, q: str | None = None,
                 author: str | None = None, flagged: bool | None = None, assignee: str | None = None, kind: str = "comment"):
        """status: open | replied | resolved (comma-separated), plus 'answered' = replied+resolved and 'flagged'.
        kind: comment (default) | mention | all."""
        with core.db.session() as s:
            qry = select(Comment, Channel).join(Channel, Channel.id == Comment.channel_id).where(Comment.is_own.is_(False))
            if kind != "all":
                qry = qry.where(func.coalesce(Comment.kind, "comment") == kind)
            if channel_id:
                qry = qry.where(Comment.channel_id.in_(channel_id.split(",")))
            if status:
                sts = set(status.split(","))
                if "flagged" in sts:
                    qry = qry.where(Comment.flagged.is_(True))
                    sts.discard("flagged")
                if "answered" in sts:
                    sts |= {"replied", "resolved"}
                    sts.discard("answered")
                if sts:
                    qry = qry.where(Comment.status.in_(sts))
            if flagged is not None:
                qry = qry.where(Comment.flagged.is_(flagged))
            if author:
                qry = qry.where(Comment.author == author)
            if assignee is not None:
                qry = qry.where(Comment.assignee == assignee)
            if q:
                qry = qry.where(or_(Comment.text.ilike(f"%{q}%"), Comment.author.ilike(f"%{q}%")))
            rows = s.execute(qry.order_by(Comment.at.desc()).limit(500)).all()
            out = [community.serialize(c, ch) for c, ch in rows]
            replies = {}
            for c in s.scalars(select(Comment).where(Comment.is_own.is_(True))):
                replies.setdefault(c.parent_external_id, []).append({"text": c.text, "at": posts_svc.iso(c.at)})
        for c in out:
            c["my_replies"] = replies.get(c["external_id"], [])
        return out

    @app.post("/api/comments/sync")
    def comments_sync(payload: dict = Body(default={})):
        n = community.sync_channel(core, payload["channel_id"]) if payload.get("channel_id") else community.sync_all(core)
        with core.db.session() as s:
            q = select(Channel).where(Channel.status == "active")
            if payload.get("channel_id"):
                q = q.where(Channel.id == payload["channel_id"])
            notes = [{"channel_id": c.id, "username": c.username, "note": (c.meta or {}).get("comments_note")}
                     for c in s.scalars(q) if (c.meta or {}).get("comments_note")]
        return {"new": n, "notes": notes}

    @app.post("/api/comments/{cid}/reply")
    def comment_reply(cid: str, payload: dict = Body(...)):
        try:
            return community.reply(core, cid, payload.get("text", ""))
        except ValueError as e:
            return _err(400, str(e))
        except KeyError as e:
            return _err(404, str(e))

    @app.patch("/api/comments/{cid}")
    def comment_patch(cid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            c = s.get(Comment, cid)
            if not c:
                return _err(404, "Comment not found")
            if "flagged" in payload:
                c.flagged = bool(payload["flagged"])
            if "assignee" in payload:
                c.assignee = str(payload["assignee"] or "").strip()[:60]
            if payload.get("status") in ("open", "replied", "resolved"):
                c.status = payload["status"]
            return community.serialize(c, s.get(Channel, c.channel_id))

    @app.post("/api/comments/{cid}/{action}")
    def comment_status(cid: str, action: str):
        if action not in ("resolve", "reopen"):
            return _err(404, "Unknown action")
        with core.db.session() as s:
            c = s.get(Comment, cid)
            if not c:
                return _err(404, "Comment not found")
            c.status = "resolved" if action == "resolve" else "open"
            return community.serialize(c, s.get(Channel, c.channel_id))

    # ------------------------------------------------------------------ insights
    def _range(days: str | None, start: str | None, end: str | None) -> tuple[datetime, datetime]:
        now = utcnow()
        if start:
            tzname = core.prefs()["timezone"]
            return posts_svc.parse_when(start, tzname), posts_svc.parse_when(end, tzname) if end else now
        if days == "mtd":
            return datetime(now.year, now.month, 1), now
        return now - timedelta(days=int(days or 30)), now

    @app.get("/api/insights")
    def insights(days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None):
        lo, hi = _range(days, start, end)
        with core.db.session() as s:
            qry = select(PostTarget).where(PostTarget.status == "sent", PostTarget.published_at >= lo, PostTarget.published_at <= hi)
            if channel_id:
                qry = qry.where(PostTarget.channel_id.in_(channel_id.split(",")))
            rows = [posts_svc.serialize_target(core, t) for t in s.scalars(qry)]
            chans = [channel_dict(c) for c in s.scalars(select(Channel))]
        k = {"posts": len(rows)}
        for m in ("views", "reach", "likes", "comments", "shares", "saved"):
            k[m] = sum(int((r["insights"] or {}).get(m) or 0) for r in rows)
        rates = [r["insights"].get("engagement_rate") for r in rows if (r["insights"] or {}).get("engagement_rate") is not None]
        k["engagement_rate"] = round(sum(rates) / len(rates), 2) if rates else None
        k["followers"] = sum(c["followers_count"] or 0 for c in chans)
        by_day: dict[str, int] = {}
        for r in rows:
            d = (r["published_at"] or "")[:10]
            by_day[d] = by_day.get(d, 0) + 1
        top = sorted(rows, key=lambda r: (r["insights"] or {}).get("views") or (r["insights"] or {}).get("likes") or 0, reverse=True)[:20]
        return {"range": [posts_svc.iso(lo), posts_svc.iso(hi)], "kpis": k, "top_posts": top, "posts_by_day": by_day,
                "channels": [{"id": c["id"], "provider": c["provider"], "username": c["username"],
                              "followers_count": c["followers_count"], "media_count": c["media_count"]} for c in chans]}

    @app.get("/api/insights/export.csv")
    def insights_csv(days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None):
        """Every sent post in the range (not just the top 20), honouring start/end and channel_id."""
        lo, hi = _range(days, start, end)
        with core.db.session() as s:
            qry = select(PostTarget).where(PostTarget.status == "sent", PostTarget.published_at >= lo, PostTarget.published_at <= hi)
            if channel_id:
                qry = qry.where(PostTarget.channel_id.in_(channel_id.split(",")))
            rows = [posts_svc.serialize_target(core, t) for t in s.scalars(qry.order_by(PostTarget.published_at))]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["published_at", "network", "channel", "format", "caption", "views", "reach", "likes", "comments", "shares",
                    "saved", "engagement_rate", "tags", "campaign", "permalink"])
        for r in rows:
            i = r["insights"] or {}
            w.writerow([r["published_at"], r["channel"]["provider"], r["channel"]["username"], r["format"], r["caption"][:200],
                        i.get("views"), i.get("reach"), i.get("likes"), i.get("comments"), i.get("shares"), i.get("saved"),
                        i.get("engagement_rate"), " ".join(r["tags"] or []), r["campaign"], r["permalink"]])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=huhqueue-insights.csv"})

    # ------------------------------------------------------------------ home / notifications / settings
    @app.get("/api/home")
    def home():
        now = utcnow()
        with core.db.session() as s:
            upcoming = [posts_svc.serialize_target(core, t) for t in s.scalars(
                select(PostTarget).where(PostTarget.status == "scheduled", PostTarget.scheduled_at.is_not(None))
                .order_by(PostTarget.scheduled_at).limit(5))]
            week = now - timedelta(days=7)
            sent_week = s.scalar(select(func.count()).select_from(PostTarget).where(
                PostTarget.status == "sent", PostTarget.published_at >= week)) or 0
            counts = dict(s.execute(select(PostTarget.status, func.count()).group_by(PostTarget.status)).all())
            unanswered = s.scalar(select(func.count()).select_from(Comment).where(
                Comment.status == "open", Comment.is_own.is_(False))) or 0
            chans = [channel_dict(c) for c in s.scalars(select(Channel))]
            failed = [posts_svc.serialize_target(core, t) for t in s.scalars(
                select(PostTarget).where(PostTarget.status.in_(("failed", "needs_action"))).limit(5))]
            # scorecard: posts per calendar week (Mon-Sun, UTC) for the last 8 weeks -> week streak
            monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
            sent_times = s.scalars(select(PostTarget.published_at).where(
                PostTarget.status == "sent", PostTarget.published_at >= monday - timedelta(weeks=52))).all()
            weeks = [0] * 52
            for ts in sent_times:
                if ts:
                    i = (monday - (ts - timedelta(days=ts.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)).days // 7
                    if 0 <= i < 52:
                        weeks[i] += 1
            streak, i = 0, (0 if weeks[0] else 1)  # this week still counts as "in progress"
            while i < 52 and weeks[i]:
                streak, i = streak + 1, i + 1
            month = now - timedelta(days=30)
            c_rows = dict(s.execute(select(Comment.status, func.count()).where(
                Comment.is_own.is_(False), Comment.at >= month).group_by(Comment.status)).all())
            total_c = sum(c_rows.values())
            comment_score = round(100 * (c_rows.get("replied", 0) + c_rows.get("resolved", 0)) / total_c) if total_c else None
            recent_comments = [{"id": c.id, "author": c.author, "text": c.text, "at": posts_svc.iso(c.at), "channel_id": c.channel_id,
                                "media_caption": (c.media_caption or "")[:80]}
                               for c in s.scalars(select(Comment).where(Comment.status == "open", Comment.is_own.is_(False))
                                                  .order_by(Comment.at.desc()).limit(4))]
        goal = sum((c["daily_limit"] or 0) for c in chans) * 7
        return {"upcoming": upcoming, "sent_this_week": sent_week, "weekly_goal": goal, "counts": counts,
                "week_streak": streak, "weekly_sent": list(reversed(weeks[:8])), "comment_score": comment_score,
                "recent_comments": recent_comments,
                "unanswered_comments": unanswered, "channels": chans, "attention": failed,
                "public_base_url": core.public_base_url(), "worker_last_tick": posts_svc.iso(worker.last_tick)}

    def _seen_key(who) -> str:
        return f"notif_seen:{who.id}"

    @app.get("/api/notifications")
    def notifications(request: Request, unread: bool = False):
        """The bell: broadcast events (user_id NULL) plus the ones addressed to you. Read state is per teammate."""
        who = getattr(request.state, "user", None)
        with core.db.session() as s:
            vis = {c for (c,) in s.execute(select(Channel.id)) if who.channel_role(c) != "none"} if who and who.restricted() else None
            q = select(Event).where(Event.notify.is_(True), or_(Event.user_id.is_(None), Event.user_id == (who.id if who else "")))
            row = s.get(KV, _seen_key(who)) if who else None
            seen = int((row.value or {}).get("id", 0)) if row else 0
            out = []
            for e in s.scalars(q.order_by(Event.id.desc()).limit(200)):
                if vis is not None and e.channel_id and e.channel_id not in vis:
                    continue   # a channel this teammate can't see
                read = bool(e.read) if (e.user_id or not who) else e.id <= seen
                if unread and read:
                    continue
                out.append({"id": e.id, "at": posts_svc.iso(e.at), "level": e.level, "kind": e.kind, "message": e.message,
                            "target_id": e.target_id, "channel_id": e.channel_id, "read": read, "personal": bool(e.user_id)})
                if len(out) >= 50:
                    break
            return out

    @app.post("/api/notifications/read")
    def notifications_read(request: Request):
        who = getattr(request.state, "user", None)
        with core.db.session() as s:
            mine = Event.user_id == who.id if who else Event.user_id.is_(None)
            for e in s.scalars(select(Event).where(Event.notify.is_(True), Event.read.is_(False), mine)):
                e.read = True
            if who:
                top = s.scalar(select(func.max(Event.id)).where(Event.notify.is_(True))) or 0
                s.merge(KV(key=_seen_key(who), value={"id": int(top)}))
        return {"ok": True}

    @app.get("/api/settings")
    def settings_get():
        p = core.prefs()
        cfg = core.settings
        from ..services.events import NOTIFY_KINDS
        return {**p, "notify_kinds": NOTIFY_KINDS,
                "notify_events": {k: bool((p.get("notify_events") or {}).get(k, True)) for k in NOTIFY_KINDS},
                "notify_email": {k: bool((p.get("notify_email") or {}).get(k, False)) for k in NOTIFY_KINDS},
                "smtp_configured": bool(teams.smtp_config()),
                "effective_public_base_url": core.public_base_url(),
                "public_base_url_from_env": bool(cfg.public_base_url), "edge_port": cfg.public_port,
                "ffmpeg": ffmpeg_available(), "version": __version__, "tiktok_audited": cfg.tiktok_audited,
                "youtube_verified": cfg.youtube_verified, "data_dir": str(cfg.data_dir),
                "configured": {k: c.is_configured() for k, c in core.connectors.items()}}

    @app.put("/api/settings")
    def settings_put(payload: dict = Body(...)):
        allowed = {"timezone", "hashtag_limit", "default_when", "default_daily_limit", "notify_webhook_url",
                   "public_base_url", "cta_handle", "cta_text", "cta_card_text", "cta_default", "theme", "notify_events", "notify_paused",
                   "notify_email", "notify_email_to", "recap_time"}
        changes = {k: v for k, v in payload.items() if k in allowed}
        if "timezone" in changes:
            from zoneinfo import ZoneInfo
            try:
                ZoneInfo(str(changes["timezone"]))
            except Exception:
                return _err(400, f"Unknown timezone {changes['timezone']}")
        if "notify_events" in changes:
            from ..services.events import NOTIFY_KINDS
            ev = changes["notify_events"]
            if not isinstance(ev, dict) or set(ev) - set(NOTIFY_KINDS):
                return _err(400, f"notify_events: an object of {{kind: true|false}} with kinds {', '.join(NOTIFY_KINDS)}")
            changes["notify_events"] = {**(core.prefs().get("notify_events") or {}), **{k: bool(v) for k, v in ev.items()}}
        if "notify_paused" in changes:
            changes["notify_paused"] = bool(changes["notify_paused"])
        if "notify_email" in changes:
            from ..services.events import NOTIFY_KINDS
            ev = changes["notify_email"]
            if not isinstance(ev, dict) or set(ev) - set(NOTIFY_KINDS):
                return _err(400, f"notify_email: an object of {{kind: true|false}} with kinds {', '.join(NOTIFY_KINDS)}")
            changes["notify_email"] = {**(core.prefs().get("notify_email") or {}), **{k: bool(v) for k, v in ev.items()}}
        if "notify_email_to" in changes:
            to = str(changes["notify_email_to"] or "").strip()
            if to and (len(to) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", to)):
                return _err(400, "notify_email_to must be an email address")
            changes["notify_email_to"] = to
        if "recap_time" in changes and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(changes["recap_time"] or "")):
            return _err(400, "recap_time must be HH:MM")
        if "public_base_url" in changes and changes["public_base_url"] and not str(changes["public_base_url"]).startswith("https://"):
            return _err(400, "Public URL must start with https://")
        if "hashtag_limit" in changes:
            changes["hashtag_limit"] = max(0, min(30, int(changes["hashtag_limit"])))
        core.update_prefs(changes)
        return settings_get()

    @app.get("/api/library/{kind}")
    def library_get(kind: str):
        if kind not in LIBRARY_KINDS:
            return _err(404, "Unknown library")
        with core.db.session() as s:
            row = s.get(KV, f"lib:{kind}")
            return row.value.get("items", []) if row else DEFAULT_LIBRARY[kind]

    @app.put("/api/library/{kind}")
    def library_put(kind: str, payload: dict = Body(...)):
        if kind not in LIBRARY_KINDS:
            return _err(404, "Unknown library")
        items = [{**i, "id": i.get("id") or secrets.token_hex(4)} for i in payload.get("items", [])]
        with core.db.session() as s:
            row = s.get(KV, f"lib:{kind}")
            if not row:
                row = KV(key=f"lib:{kind}", value={})
                s.add(row)
            row.value = {"items": items}
        return items

    add_content_routes(app, core)
    add_report_routes(app, core)
    add_team_routes(app, core, auth, _set_session)
    add_channel_page_routes(app, core)
    add_bulk_routes(app, core, approval_actor, post_list, lambda request: getattr(request.state, "via", None) != "token")
    add_ai_tools_routes(app, core)
    add_thread_routes(app, core)
    add_audience_routes(app, core)
    add_webhook_routes(app, core)

    # ------------------------------------------------------------------ worker / tunnel
    @app.post("/api/worker/tick")
    def worker_tick():
        return worker.tick()

    @app.get("/api/worker/status")
    def worker_status():
        return worker.heartbeat()

    @app.get("/api/tunnel")
    def tunnel_status():
        return tunnel.status()

    @app.post("/api/tunnel/start")
    def tunnel_start(payload: dict = Body(default={})):
        return tunnel.start(payload.get("kind", "cloudflared"), payload.get("domain", ""))

    @app.post("/api/tunnel/stop")
    def tunnel_stop():
        return tunnel.stop()

    # ------------------------------------------------------------------ demo consent page (fake connector only)
    if "fake" in core.connectors:
        @app.get("/fake-consent", response_class=HTMLResponse)
        def fake_consent(redirect_uri: str, state: str):
            from html import escape
            target = f"{redirect_uri}?code=fakecode&state={escape(state)}"
            return f"""<!doctype html><html><head><title>Demo network - Log in</title>
<style>body{{font-family:system-ui;background:#fafafa;display:grid;place-items:center;height:100vh;margin:0}}
.card{{background:#fff;border:1px solid #ddd;border-radius:12px;padding:32px;width:340px;text-align:center}}
a.btn{{display:block;background:#4f46e5;color:#fff;padding:12px;border-radius:8px;text-decoration:none;margin-top:16px}}</style>
</head><body><div class="card"><h2>Demo network</h2><p><b>HuhQueue</b> wants to publish posts and read insights.</p>
<a class="btn" data-testid="consent-continue" href="{escape(target)}">Continue as demo_huhbyte</a>
<p style="font-size:12px;color:#777">This page stands in for Instagram's / TikTok's own consent screen.</p></div></body></html>"""

    # ------------------------------------------------------------------ UI
    @app.get("/", include_in_schema=False)
    def index():
        page = STATIC / "app" / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>UI not built</h1><p>Run <code>cd web && npm install && npm run build</code>.</p>", 500)
        from .context import embed
        return HTMLResponse(embed(page.read_text(encoding="utf-8")), headers={"Cache-Control": "no-store"})

    # Hosted installs (one public https origin): the UI app also answers the edge routes, so
    # <public-url>/oauth/<network>/callback and <public-url>/m/... work on the same URL as the UI.
    add_edge_routes(app, core, ui="")
    add_agent_routes(app)
    add_assistant_routes(app, core)
    add_growth_routes(app, core)
    add_context_routes(app, core)
    add_safety_routes(app, core, _audit)
    add_action_routes(app, core)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def create_edge_app(core: Core) -> FastAPI:
    """Public app: ONLY media files (by unguessable token) and OAuth callbacks. Tunnel this port, nothing else."""
    edge = FastAPI(title="HuhQueue edge", docs_url=None, redoc_url=None, openapi_url=None)
    add_edge_routes(edge, core, ui=core.settings.ui_base_url or core.settings.local_base_url)
    return edge


def add_edge_routes(edge: FastAPI, core: Core, ui: str) -> None:
    add_public_growth_routes(edge, core)   # /l/{code} short links and /bio/{slug} pages are public

    @edge.get("/healthz", include_in_schema=False)
    def healthz():
        return {"ok": True}

    @edge.api_route("/m/{token}/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    def media_file(token: str, name: str):
        with core.db.session() as s:
            m = s.scalar(select(Media).where(Media.public_token == token))
        if not m:
            raise HTTPException(404)
        allowed = {m.stored_name, m.jpeg_name} | ({n.name for n in core.settings.media_dir.glob(f"{m.id}-endcard-*.mp4")})
        if name not in allowed:
            raise HTTPException(404)
        mime = "image/jpeg" if name == m.jpeg_name else ("video/mp4" if "-endcard-" in name else m.mime)
        return FileResponse(core.settings.media_dir / name, media_type=mime)

    @edge.get("/oauth/{provider}/callback", include_in_schema=False)
    def callback(provider: str, request: Request):
        q = request.query_params
        try:
            cid = oauth.complete(core, provider, q.get("code"), q.get("state"),
                                 q.get("error_description") or q.get("error"))
        except oauth.OAuthFailure as e:
            from urllib.parse import quote
            return RedirectResponse(f"{ui}/#/channels?connect_error={quote(str(e))}", status_code=303)
        return RedirectResponse(f"{ui}/#/channel/{cid}?connected=1", status_code=303)

    # ---- Meta platform-compliance callbacks (Instagram app dashboard -> App settings / Data deletion)
    async def _signed(request: Request) -> str:
        ct = request.headers.get("content-type", "")
        if "json" in ct:
            return (await request.json()).get("signed_request", "")
        return (await request.form()).get("signed_request", "")

    def _public_base(request: Request) -> str:
        return core.public_base_url() or core.settings.ui_base_url or str(request.base_url).rstrip("/")

    @edge.post("/oauth/instagram/deauthorize", include_in_schema=False)
    async def ig_deauthorize(request: Request):
        try:
            return meta_compliance.deauthorize(core, await _signed(request))
        except meta_compliance.SignedRequestError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @edge.post("/oauth/instagram/data-deletion", include_in_schema=False)
    async def ig_data_deletion(request: Request):
        try:
            return meta_compliance.data_deletion(core, await _signed(request), _public_base(request))
        except meta_compliance.SignedRequestError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @edge.get("/data-deletion/{code}", response_class=HTMLResponse, include_in_schema=False)
    def data_deletion_status(code: str):
        from html import escape
        st = meta_compliance.deletion_status(core, code)
        if not st:
            return HTMLResponse("<!doctype html><title>Data deletion</title><h1>Unknown confirmation code</h1>"
                                "<p>No deletion request matches this code.</p>", status_code=404)
        return (f"<!doctype html><html><head><title>Data deletion {escape(code)}</title><meta name=viewport content='width=device-width'>"
                "<style>body{font-family:system-ui;max-width:560px;margin:10vh auto;padding:0 20px;color:#1c1530}"
                "code{background:#f3ecff;padding:2px 6px;border-radius:6px}</style></head><body>"
                f"<h1>Data deletion request</h1><p>Confirmation code: <code>{escape(code)}</code></p>"
                f"<p>Status: <b data-status>{escape(st.get('status', 'unknown'))}</b></p>"
                f"<p>Requested {escape(st.get('requested_at', ''))}. Instagram channels removed: {int(st.get('channels_deleted', 0))}. "
                "Access tokens, scheduled posts and pulled comments for that account were deleted from this HuhQueue server.</p>"
                "</body></html>")

    @edge.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
    def privacy():
        return ("<h1>Privacy</h1><p>HuhQueue is a self-hosted tool run by its owner for their own accounts. "
                "Tokens are stored encrypted on the owner's computer and never shared.</p>")

    @edge.get("/terms", response_class=HTMLResponse, include_in_schema=False)
    def terms():
        return "<h1>Terms</h1><p>Personal, self-hosted publishing tool. No third-party users.</p>"
