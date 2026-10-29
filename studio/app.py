"""HuhQueue Studio — the Buffer Commit edition web app (FastAPI).

Run:  uvicorn studio.app:create_app --factory   (env: BUFFER_CLIENT_ID, BUFFER_CLIENT_SECRET, SESSION_SECRET, PUBLIC_URL)
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import buffer_api, demo, mcp
from .actions import ActionError, Actor, Studio
from .buffer_api import BufferClient, BufferError
from .config import Settings, load_settings
from .store import Store, sha

STATIC = Path(__file__).parent / "static"
COOKIE = "hq_sid"


def create_app(settings: Settings | None = None, http: httpx.Client | None = None) -> FastAPI:
    s = settings or load_settings()
    store = Store(s.db_path, s.session_secret)
    buffer = BufferClient(s, store, http=http)
    studio = Studio(s, store, demo.BufferRouter(buffer, demo.DemoBuffer()))
    app = FastAPI(title="HuhQueue Studio — built on the Buffer API", docs_url="/api/docs", redoc_url=None)
    app.state.settings, app.state.store, app.state.buffer, app.state.studio = s, store, buffer, studio

    # ------------------------------------------------------------ helpers
    def set_cookie(resp: Response, sid: str) -> None:
        resp.set_cookie(COOKIE, sid, max_age=30 * 86400, httponly=True, secure=s.secure_cookies, samesite="lax", path="/")

    def ensure_session(request: Request) -> tuple[str, dict, bool]:
        sid = request.cookies.get(COOKIE)
        sess = store.session(sid)
        if sess:
            return sid, sess, False
        sid = secrets.token_urlsafe(32)
        store.new_session(sid, secrets.token_urlsafe(24))
        return sid, store.session(sid), True

    def actor(request: Request, write: bool = False) -> Actor:
        # A signed-in browser session is the human (writes also need the page's CSRF token).
        # Without one, an agent authenticates with its bearer token and gets agent rules.
        sess = store.session(request.cookies.get(COOKIE))
        if sess and sess["user_id"] and store.user(sess["user_id"]):
            if write and request.headers.get("x-hq-csrf") != sess["csrf"]:
                raise ActionError("Missing or wrong CSRF token. Reload the page.", 403)
            return Actor(sess["user_id"])
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            a = studio.resolve_agent(auth[7:].strip())
            if not a:
                raise ActionError("Invalid or revoked agent token.", 401)
            return a
        raise ActionError("Not connected. Connect your Buffer account first.", 401)

    @app.exception_handler(ActionError)
    async def _action_err(_, e: ActionError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.exception_handler(BufferError)
    async def _buffer_err(_, e: BufferError):
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else {}
        return JSONResponse({"error": str(e), "buffer": True}, status_code=e.status, headers=headers)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith(("/static/", "/privacy")):
            resp.headers["Cache-Control"] = "no-cache"  # always revalidate, so a deploy is picked up at once
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' https: data:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                                "font-src https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        return resp

    # ------------------------------------------------------------ pages
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/privacy", include_in_schema=False)
    def privacy():
        return FileResponse(STATIC / "privacy.html")

    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p)

    # public counters for the landing page and the home screen (real rows only, sample workspaces excluded)
    @app.get("/api/stats")
    def stats():
        users = store.q("SELECT COUNT(*) AS n FROM users WHERE id NOT LIKE ?", (demo.PREFIX + "%",))[0]["n"]
        sent = store.q("SELECT COUNT(*) AS n FROM audit WHERE ok=1 AND action IN ('submit_post','approve_post') "
                       "AND risk IN ('public','draft') AND user_id NOT LIKE ?", (demo.PREFIX + "%",))[0]["n"]
        tries = store.q("SELECT COUNT(*) AS n FROM users WHERE id LIKE ?", (demo.PREFIX + "%",))[0]["n"]
        return {"buffer_accounts": users, "posts_sent_to_buffer": sent, "sample_workspaces_open": tries}

    @app.get("/healthz")
    def health():
        return {"ok": True, "configured": s.configured}

    # ------------------------------------------------------------ OAuth
    @app.get("/login", include_in_schema=False)
    def login(request: Request):
        if not s.configured:
            return RedirectResponse("/?error=" + quote("Buffer app client is not configured on this server."), 302)
        sid, sess, fresh = ensure_session(request)
        verifier, challenge = buffer_api.pkce_pair()
        state = secrets.token_urlsafe(24)
        store.update_session(sid, oauth=store.seal({"state": state, "verifier": verifier}))
        resp = RedirectResponse(buffer_api.authorize_url(s, state, challenge), 302)
        set_cookie(resp, sid)
        return resp

    @app.get("/callback", include_in_schema=False)
    def callback(request: Request, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        sid = request.cookies.get(COOKIE)
        sess = store.session(sid)
        if error:
            return RedirectResponse("/?error=" + quote(error_description or error), 302)
        pending = store.unseal(sess["oauth"]) if sess else None
        if not pending or not state or not secrets.compare_digest(state, pending["state"]):
            return RedirectResponse("/?error=" + quote("Sign-in expired or didn't start here. Try Connect again."), 302)
        store.update_session(sid, oauth=None)
        try:
            tokens = buffer_api.exchange_code(s, buffer.http, code, pending["verifier"])
            acct = buffer.account(token=tokens["access_token"])
        except BufferError as e:
            return RedirectResponse("/?error=" + quote(str(e)), 302)
        orgs = acct.get("organizations") or [{}]
        store.upsert_user(acct["id"], acct.get("email") or "", acct.get("name") or "", orgs[0].get("id", ""),
                          orgs[0].get("name", ""), tokens)
        buffer.invalidate(acct["id"])
        # rotate the session id at login (fixation defence)
        store.drop_session(sid)
        new_sid = secrets.token_urlsafe(32)
        store.new_session(new_sid, secrets.token_urlsafe(24))
        store.update_session(new_sid, user_id=acct["id"])
        store.audit(acct["id"], "you", "connect_buffer", "auth", {"scopes": tokens.get("scope", "")})
        resp = RedirectResponse("/?connected=1", 302)
        set_cookie(resp, new_sid)
        return resp

    # try-it workspace: sample channels and an agent post waiting for approval; nothing reaches Buffer
    # ponytail: no per-IP limit on demo creation; stale demo users are purged after a day on each new demo
    @app.get("/demo", include_in_schema=False)
    def demo_start(request: Request):
        sid = request.cookies.get(COOKIE)
        if sid:
            store.drop_session(sid)
        uid = demo.create_user(store)
        new_sid = secrets.token_urlsafe(32)
        store.new_session(new_sid, secrets.token_urlsafe(24), ttl=demo.TTL)
        store.update_session(new_sid, user_id=uid)
        resp = RedirectResponse("/?demo=1", 302)
        set_cookie(resp, new_sid)
        return resp

    # one-time link to open this account on another device (phone, second browser); 10 minutes, single use
    @app.post("/api/device-link")
    def device_link(request: Request):
        a = actor(request, write=True)
        if a.kind != "human":
            raise ActionError("Only a signed-in human can create a device link.", 403)
        tok = secrets.token_urlsafe(32)
        store.x("INSERT INTO device_links(token_hash,user_id,expires) VALUES(?,?,?)", (sha(tok), a.uid, time.time() + 600))
        store.audit(a.uid, a.label, "device_link", "auth", {"expires_in": 600})
        return {"url": f"{s.public_url}/device/{tok}", "expires_in": 600}

    @app.get("/device/{tok}", include_in_schema=False)
    def device_open(tok: str):
        rows = store.q("SELECT * FROM device_links WHERE token_hash=?", (sha(tok),))
        if not rows or rows[0]["used"] or rows[0]["expires"] < time.time():
            return RedirectResponse("/?error=" + quote("That device link is used or expired. Create a new one."), 302)
        store.x("UPDATE device_links SET used=1 WHERE token_hash=?", (sha(tok),))
        new_sid = secrets.token_urlsafe(32)
        store.new_session(new_sid, secrets.token_urlsafe(24))
        store.update_session(new_sid, user_id=rows[0]["user_id"])
        store.audit(rows[0]["user_id"], "you", "device_signed_in", "auth", {})
        resp = RedirectResponse("/?connected=1", 302)
        set_cookie(resp, new_sid)
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        sid = request.cookies.get(COOKIE)
        if sid:
            store.drop_session(sid)
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.post("/api/disconnect")
    def disconnect(request: Request):
        a = actor(request, write=True)
        if a.kind != "human":
            raise ActionError("Only a human can disconnect.", 403)
        store.delete_user(a.uid)
        resp = JSONResponse({"ok": True, "deleted": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @app.get("/api/session")
    def session(request: Request):
        sid, sess, fresh = ensure_session(request)
        connected = bool(sess["user_id"] and store.user(sess["user_id"]) and store.tokens(sess["user_id"]))
        body = {"configured": s.configured, "connected": connected, "csrf": sess["csrf"],
                "user": studio.me(Actor(sess["user_id"])) if connected else None, "mcp_url": s.public_url + "/mcp",
                "repo": s.repo_url, "demo": connected and demo.is_demo(sess["user_id"])}
        resp = JSONResponse(body)
        if fresh:
            set_cookie(resp, sid)
        return resp

    # ------------------------------------------------------------ REST (session or agent bearer token)
    @app.get("/api/me")
    def me(request: Request):
        return studio.me(actor(request))

    @app.get("/api/channels")
    def channels(request: Request, refresh: bool = False):
        return studio.channels(actor(request), refresh=refresh)

    @app.post("/api/draft")
    def draft(request: Request, body: dict = Body(...)):
        return studio.draft_from_link(actor(request, write=True), body.get("url", ""), body.get("channel_ids"), body.get("tone", ""),
                                     bool(body.get("thread")))

    @app.post("/api/posts/preview")
    def preview(request: Request, body: dict = Body(...)):
        return studio.preview_post(actor(request, write=True), body)

    @app.post("/api/posts/submit")
    def submit(request: Request, body: dict = Body(...)):
        return studio.submit_post(actor(request, write=True), body.get("confirm_token", ""))

    @app.get("/api/queue")
    def queue(request: Request, status: str = "scheduled"):
        return studio.queue(actor(request), status)

    @app.get("/api/approvals")
    def approvals(request: Request, status: str = "pending"):
        return studio.approvals(actor(request), status)

    @app.post("/api/approvals/{approval_id}/approve")
    def approve(approval_id: int, request: Request, body: dict = Body(default={})):
        return studio.decide(actor(request, write=True), approval_id, True, body.get("text"), body.get("mode"))

    @app.post("/api/approvals/{approval_id}/reject")
    def reject(approval_id: int, request: Request):
        return studio.decide(actor(request, write=True), approval_id, False)

    @app.post("/api/ideas")
    def ideas(request: Request, body: dict = Body(...)):
        return studio.create_idea(actor(request, write=True), body.get("title", ""), body.get("text", ""))

    @app.get("/api/radar")
    def radar_list(request: Request):
        return studio.radar(actor(request))

    @app.post("/api/radar/feeds")
    def radar_add(request: Request, body: dict = Body(...)):
        return studio.add_feed(actor(request, write=True), body.get("url", ""))

    @app.delete("/api/radar/feeds/{feed_id}")
    def radar_remove(feed_id: int, request: Request):
        return studio.remove_feed(actor(request, write=True), feed_id)

    @app.get("/api/limits")
    def limits(request: Request):
        return studio.limits(actor(request))

    @app.get("/api/safety")
    def safety(request: Request):
        a = actor(request)
        return {"kill_switch": studio.me(a)["kill_switch"], "tokens": studio.agent_tokens(a) if a.kind == "human" else []}

    @app.post("/api/safety/kill")
    def kill(request: Request, body: dict = Body(...)):
        return studio.set_kill_switch(actor(request, write=True), bool(body.get("on")))

    @app.post("/api/agent-tokens")
    def new_token(request: Request, body: dict = Body(...)):
        return studio.create_agent_token(actor(request, write=True), body.get("name", ""), body.get("policy", "review"),
                                         int(body.get("daily_cap", 10)), body.get("channels") or None, int(body.get("days", 30)))

    @app.delete("/api/agent-tokens/{token_id}")
    def revoke(token_id: int, request: Request):
        return studio.revoke_agent_token(actor(request, write=True), token_id)

    @app.get("/api/audit")
    def audit(request: Request, limit: int = 100):
        return studio.audit(actor(request), min(limit, 500))

    # ------------------------------------------------------------ MCP
    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        auth = request.headers.get("authorization", "")
        a = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if not a:
            return JSONResponse({"error": "Agent token required: Authorization: Bearer hq_..."}, status_code=401,
                                headers={"WWW-Authenticate": 'Bearer realm="huhqueue"'})
        msg = await request.json()
        if isinstance(msg, list):
            out = [r for r in (mcp.handle(studio, a, m) for m in msg) if r]
            return JSONResponse(out) if out else Response(status_code=202)
        r = mcp.handle(studio, a, msg)
        return JSONResponse(r) if r else Response(status_code=202)

    @app.get("/mcp", include_in_schema=False)
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    return app

