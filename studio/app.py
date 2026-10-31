"""HuhQueue — the Buffer Commit edition web app (FastAPI).

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

import base64
import hmac
import os

from . import buffer_api, demo, demo_guard, house as house_mod, inbox as inbox_mod, limits as reqlimits, mcp, mcp_oauth
from .actions import ActionError, Actor, Studio
from .buffer_api import BufferClient, BufferError
from .config import Settings, load_settings
from .store import Store, sha

STATIC = Path(__file__).parent / "static"
PAGES = ("/", "/app", "/privacy", "/terms", "/security")
COOKIE = "hq_sid"


def create_app(settings: Settings | None = None, http: httpx.Client | None = None) -> FastAPI:
    s = settings or load_settings()
    store = Store(s.db_path, s.session_secret)
    if s.migrate_from and s.migrate_from != s.db_path:
        copied = store.copy_from(Store(s.migrate_from, s.session_secret))
        print(f"migrate: copied {copied or 'nothing (target already has data)'}", flush=True)
    demo_guard.ensure(store)
    buffer = BufferClient(s, store, http=http)
    studio = Studio(s, store, demo.BufferRouter(buffer, demo.DemoBuffer()))
    app = FastAPI(title="HuhQueue — built on the Buffer API", docs_url="/api/docs", redoc_url=None)
    house = house_mod.House(studio, http=http)
    studio.house = house
    app.state.settings, app.state.store, app.state.buffer, app.state.studio, app.state.house = s, store, buffer, studio, house

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
            if write:  # the kill switch stops every agent write, whatever the route (SECURITY_RULES 4)
                studio.kill_switch_check(a, request.url.path)
            return a
        raise ActionError("Not connected. Connect your Buffer account first.", 401)

    @app.exception_handler(ActionError)
    async def _action_err(_, e: ActionError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.exception_handler(BufferError)
    async def _buffer_err(_, e: BufferError):
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else {}
        return JSONResponse({"error": str(e), "buffer": True}, status_code=e.status, headers=headers)

    # Anonymous and free-sample traffic: a per-IP request rate limit (SECURITY_RULES 3). Real accounts, by cookie or by
    # a real account's agent token, are never slowed here. Media links (/m/) are left alone: Buffer fetches them.
    rate = reqlimits.RateLimit(per_min=int(os.environ.get("HQ_ANON_RPM", "240")), demo_per_min=int(os.environ.get("HQ_DEMO_PER_MIN", "6")))
    app.state.rate = rate

    def _real_account(request: Request) -> bool:
        sess = store.session(request.cookies.get(COOKIE))
        uid = sess.get("user_id") if sess else None
        if not uid:
            auth = request.headers.get("authorization", "")
            ag = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
            uid = ag.uid if ag else None
        return bool(uid) and not demo_guard.is_demo(uid)

    @app.middleware("http")
    async def _rate(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/api/", "/mcp", "/oauth/", "/demo")) and not path.startswith("/api/worker/") \
                and not _real_account(request):
            ip = demo_guard.client_ip(request)
            wait = rate.hit("ip:" + sha("ip:" + ip), rate.per_min)
            if not wait and path == "/demo":
                wait = rate.hit("demo:" + sha("ip:" + ip), rate.demo_per_min)
            if wait:
                return JSONResponse({"error": "Too many requests from this network. Wait a minute, or connect Buffer."},
                                    status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        return await call_next(request)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path in PAGES or request.url.path.startswith(("/static/", "/docs")):
            resp.headers["Cache-Control"] = "no-cache"  # always revalidate, so a deploy is picked up at once
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; img-src 'self' https: data: blob:; media-src 'self' https: blob:; style-src 'self' 'unsafe-inline'; "
                                "font-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
        return resp

    # ------------------------------------------------------------ pages
    # "/" is the landing page for visitors (crawlable, no app JS) and the app for a connected browser. Both read the same
    # rule as /api/session's "connected", so the landing never bounces a connected browser and the app never shows signed out.
    def _connected(request: Request) -> bool:
        sess = store.session(request.cookies.get(COOKIE))
        return bool(sess and sess["user_id"] and store.user(sess["user_id"]) and store.tokens(sess["user_id"]))

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return FileResponse(STATIC / ("index.html" if _connected(request) else "landing.html"))

    @app.get("/app", include_in_schema=False)
    def app_page(request: Request):
        return RedirectResponse("/", 302) if not _connected(request) else FileResponse(STATIC / "index.html")

    for _path, _file in (("/privacy", "privacy.html"), ("/terms", "terms.html"), ("/security", "security.html")):
        def _legal(_f=_file):
            return FileResponse(STATIC / _f)
        app.add_api_route(_path, _legal, methods=["GET"], include_in_schema=False)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "favicon.ico", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/og.png", include_in_schema=False)
    def og_image():
        return FileResponse(STATIC / "brand" / "og.png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        body = ("User-agent: *\nAllow: /\nAllow: /docs\nAllow: /privacy\nAllow: /terms\nAllow: /security\nAllow: /api/docs\n"
                "Disallow: /api/\nDisallow: /mcp\nDisallow: /callback\nDisallow: /login\nDisallow: /demo\nDisallow: /device/\n"
                "Disallow: /oauth/\nDisallow: /m/\n"
                f"Sitemap: {s.public_url}/sitemap.xml\n")
        return Response(body, media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        from . import docs as docs_mod
        urls = ["/", *[f"/docs/{d}" if d else "/docs" for d in docs_mod.slugs()], "/privacy", "/terms", "/security", "/api/docs"]
        body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
            f"  <url><loc>{s.public_url}{u}</loc><lastmod>{docs_mod.LASTMOD}</lastmod></url>\n" for u in urls) + "</urlset>\n"
        return Response(body, media_type="application/xml")

    @app.get("/docs/search.json", include_in_schema=False)
    def docs_search():
        from . import docs as docs_mod
        return JSONResponse(docs_mod.search_index(), headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/docs", include_in_schema=False)
    @app.get("/docs/{slug}", include_in_schema=False)
    def docs_page(slug: str = ""):
        from . import docs as docs_mod
        html = docs_mod.page(slug)
        if html is None:
            return Response(docs_mod.missing(), status_code=404, media_type="text/html")
        return Response(html, media_type="text/html")

    @app.get("/static/{name:path}", include_in_schema=False)
    def static(name: str):
        root = STATIC.resolve()
        p = (STATIC / name).resolve()
        if root not in p.parents or not p.is_file() or p.parent.name == "src":
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
        # Touches the database so uptime pings also keep a free Postgres (Supabase) from pausing for inactivity.
        return {"ok": True, "configured": s.configured, "db": store.ping()}

    # ------------------------------------------------------------ OAuth
    @app.get("/login", include_in_schema=False)
    def login(request: Request, next: str = ""):
        if not s.configured:
            return RedirectResponse("/?error=" + quote("Buffer app client is not configured on this server."), 302)
        sid, sess, fresh = ensure_session(request)
        verifier, challenge = buffer_api.pkce_pair()
        state = secrets.token_urlsafe(24)
        # only our own consent page may be the place to come back to (no open redirect)
        back = next if next.startswith("/oauth/authorize?") else ""
        store.update_session(sid, oauth=store.seal({"state": state, "verifier": verifier, "next": back}))
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
        resp = RedirectResponse(pending.get("next") or "/?connected=1", 302)
        set_cookie(resp, new_sid)
        return resp

    # try-it workspace: sample channels and an agent post waiting for approval; nothing reaches Buffer
    # at most CAPS['workspace'] new sample workspaces per IP a day; stale demo users are purged after a day on each new demo
    @app.get("/demo", include_in_schema=False)
    def demo_start(request: Request, url: str = ""):
        ip = demo_guard.client_ip(request)
        if not demo_guard.workspace_ok(store, ip):
            return RedirectResponse("/?error=" + quote(demo_guard.WHY["workspace"]), 302)
        sid = request.cookies.get(COOKIE)
        if sid:
            store.drop_session(sid)
        uid = demo.create_user(store)
        demo_guard.register(store, uid, ip)
        new_sid = secrets.token_urlsafe(32)
        store.new_session(new_sid, secrets.token_urlsafe(24), ttl=demo.TTL)
        store.update_session(new_sid, user_id=uid)
        # the landing's link field: carry the link into the sample, where research is capped (demo_guard) like any draft
        link = url.strip()[:2000]
        if link and not link.lower().startswith(("http://", "https://")):
            link = "https://" + link
        ok = bool(link) and " " not in link and "." in link.split("/")[2] if link.count("/") >= 2 else False
        resp = RedirectResponse("/?demo=1" + ("&url=" + quote(link, safe="") if ok else ""), 302)
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
        if body["demo"]:
            body["demo_left"] = demo_guard.left(store, sess["user_id"])  # what the free sample may still spend today
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

    # one edit path for posts already in Buffer: the Planner's drag-and-drop, its edit dialog, REST and MCP all land here
    @app.patch("/api/posts/{post_id}")
    def post_update(post_id: str, request: Request, body: dict = Body(...)):
        return studio.update_post(actor(request, write=True), post_id, body.get("text"), body.get("due_at"),
                                  bool(body.get("override_warnings")))

    @app.post("/api/preflight")
    def preflight_run(request: Request, body: dict = Body(...)):
        """Dry run: check a post against each channel's Buffer and network rules. Read-only."""
        return studio.preflight(actor(request), body)

    @app.post("/api/preflight/fix")
    def preflight_fix(request: Request, body: dict = Body(...)):
        """Apply preflight fixes (trim/thread text, crop/convert images) and return the fixed post. Nothing is sent."""
        return studio.autofix(actor(request, write=True), body)

    @app.get("/api/preflight/rules")
    def preflight_rules(request: Request, service: str = ""):
        return studio.preflight_rules(actor(request), service or None)

    @app.post("/api/posts/{post_id}/reschedule")
    def post_reschedule(post_id: str, request: Request, body: dict = Body(...)):
        return studio.reschedule_post(actor(request, write=True), post_id, body.get("due_at") or "", bool(body.get("override_warnings")))

    @app.delete("/api/posts/{post_id}")
    def post_delete(post_id: str, request: Request):
        return studio.delete_draft(actor(request, write=True), post_id)

    @app.post("/api/approvals/{approval_id}/reschedule")
    def approval_reschedule(approval_id: int, request: Request, body: dict = Body(...)):
        return studio.reschedule_approval(actor(request, write=True), approval_id, body.get("due_at") or "")

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

    @app.get("/api/plan")
    def plan(request: Request):
        return studio.plan(actor(request))

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

    # ------------------------------------------------------------ HuhQueue: media, brand kit, ledger, agent, calendar
    @app.get("/m/{token}", include_in_schema=False)
    def media(token: str, request: Request):
        m = house.get_media(token)
        if not m:
            return JSONResponse({"error": "not found"}, status_code=404)
        data, size = m["data"], m["size"]
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
        rng = request.headers.get("range", "")
        if rng.startswith("bytes="):
            a_, _, b_ = rng[6:].partition("-")
            start = int(a_) if a_.isdigit() else 0
            end = min(int(b_) if b_.isdigit() else size - 1, size - 1)
            if start <= end:
                return Response(data[start:end + 1], status_code=206, media_type=m["mime"],
                                headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
        return Response(data, media_type=m["mime"], headers=headers)

    @app.post("/api/media")
    async def upload(request: Request):
        """Authenticate first, then read the body with a cap (Content-Length checked, then counted as it streams), so
        nothing is buffered or decoded for a caller who isn't allowed in (SECURITY_RULES 6)."""
        a = actor(request, write=True)
        cap = reqlimits.DEMO_MEDIA_MAX if demo_guard.is_demo(a.uid) else reqlimits.MEDIA_MAX
        body = await reqlimits.read_json(request, cap)
        return house.upload(a, str(body.get("name", "")), str(body.get("data", "")))

    @app.get("/api/brand")
    def brand_get(request: Request):
        a = actor(request)
        return {"kit": house.kit(a), "rules": house.rules(a, include_inactive=True)}

    @app.post("/api/brand")
    def brand_save(request: Request, body: dict = Body(...)):
        return house.save_kit(actor(request, write=True), body)

    @app.post("/api/brand/preview")
    def brand_preview(request: Request):
        return house.preview_kit(actor(request, write=True))

    @app.post("/api/brand/from-website")
    def brand_site(request: Request, body: dict = Body(...)):
        return house.kit_from_website(actor(request, write=True), body.get("url", ""))

    @app.post("/api/ledger")
    def ledger_add(request: Request, body: dict = Body(...)):
        return house.remember(actor(request, write=True), body.get("correction", ""))

    @app.post("/api/ledger/{rule_id}")
    def ledger_toggle(rule_id: int, request: Request, body: dict = Body(...)):
        return house.set_rule(actor(request, write=True), rule_id, bool(body.get("active")))

    @app.delete("/api/ledger/{rule_id}")
    def ledger_delete(rule_id: int, request: Request):
        return house.delete_rule(actor(request, write=True), rule_id)

    @app.post("/api/check")
    def check(request: Request, body: dict = Body(...)):
        return house.check(actor(request), body.get("text", ""), body.get("media_token", ""))

    @app.get("/api/ai")
    def ai_get(request: Request):
        return house.ai_settings(actor(request))

    @app.post("/api/ai")
    def ai_save(request: Request, body: dict = Body(...)):
        return house.save_ai(actor(request, write=True), body)

    @app.post("/api/agent/runs")
    def run_start(request: Request, body: dict = Body(...)):
        return house.start_run(actor(request, write=True), body)

    @app.get("/api/agent/runs")
    def run_list(request: Request):
        return house.runs(actor(request))

    @app.get("/api/agent/runs/{rid}")
    def run_get(rid: int, request: Request):
        return house.run(actor(request), rid)

    @app.post("/api/agent/runs/{rid}/revise")
    def run_revise(rid: int, request: Request, body: dict = Body(...)):
        return house.revise(actor(request, write=True), rid, body.get("correction", ""))

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}")
    def run_pkg(rid: int, channel_id: str, request: Request, body: dict = Body(...)):
        return house.update_package(actor(request, write=True), rid, channel_id, body.get("text", ""))

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/schedule")
    def run_schedule(rid: int, channel_id: str, request: Request, body: dict = Body(default={})):
        return house.schedule_package(actor(request, write=True), rid, channel_id, body.get("mode") or "queue", body.get("due_at"),
                                      bool(body.get("override_warnings")))

    # Inbox: what needs a person, grouped by urgency (the home screen, the nav badge and MCP get_inbox)
    @app.get("/api/inbox")
    def inbox(request: Request):
        return inbox_mod.build(studio, actor(request))

    @app.get("/api/calendar")
    def calendar(request: Request):
        return house.calendar(actor(request))

    # ------------------------------------------------------------ One take -> a week (UI = REST = MCP)
    @app.post("/api/takes")
    def take_start(request: Request, body: dict = Body(...)):
        """clips_from_video: a long video you own (upload media_token, or a direct https url) -> 3-7 captioned 9:16 clips."""
        return house.takes.start(actor(request, write=True), body)

    @app.post("/api/takes/sample")
    def take_sample(request: Request, body: dict = Body(default={})):
        return house.takes.start_sample(actor(request, write=True), body)

    @app.get("/api/takes")
    def take_list(request: Request):
        return house.takes.list(actor(request))

    @app.get("/api/takes/{rid}")
    def take_get(rid: int, request: Request):
        return house.takes.get(actor(request), rid)

    @app.patch("/api/takes/{rid}/clips/{clip_id}")
    def take_adjust(rid: int, clip_id: str, request: Request, body: dict = Body(...)):
        return house.takes.adjust(actor(request, write=True), rid, clip_id, body)

    @app.post("/api/takes/{rid}/place")
    def take_place(rid: int, request: Request, body: dict = Body(default={})):
        """place_clips_in_week: dry_run (default) returns the plan + confirm_token; confirm_token creates Buffer drafts."""
        return house.takes.place(actor(request, write=True), rid, body)

    @app.get("/sample/{name}", include_in_schema=False)
    def sample_file(name: str, request: Request):
        from .takes import SAMPLE_DIR
        p = (SAMPLE_DIR / name).resolve()
        if p.parent != SAMPLE_DIR.resolve() or not p.is_file() or p.suffix not in (".mp4", ".jpg"):
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})

    # render worker (GitHub Actions) callbacks. The run claims its job once with its GitHub OIDC identity and gets a
    # token that reads the spec once, posts each of the job's clips once and dies with the result (SECURITY_RULES 2).
    @app.post("/api/worker/jobs/{jid}/claim", include_in_schema=False)
    def worker_claim(jid: int, request: Request):
        auth = request.headers.get("authorization", "")
        code, body = house.claim_job(jid, auth[7:].strip() if auth.lower().startswith("bearer ") else "")
        return JSONResponse(body, status_code=code, headers={"Cache-Control": "no-store"})

    @app.get("/api/worker/jobs/{jid}", include_in_schema=False)
    def worker_job(jid: int, request: Request):
        if not house.job_token_ok(jid, request.headers.get("x-job-token", ""), "read"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        j = house.job_spec(jid)
        return j or JSONResponse({"error": "not found"}, status_code=404)

    @app.post("/api/worker/jobs/{jid}", include_in_schema=False)
    async def worker_done(jid: int, request: Request):
        if not house.job_token_ok(jid, request.headers.get("x-job-token", ""), "write"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        body = await reqlimits.read_json(request, reqlimits.WORKER_MAX)
        try:
            data = base64.b64decode(body["data"]) if body.get("data") else None
            return house.finish_video(jid, data, body.get("meta"), body.get("error", ""), body=body)
        finally:
            house.burn_job_token(jid)

    @app.post("/api/worker/jobs/{jid}/clips/{clip_id}", include_in_schema=False)
    async def worker_clip(jid: int, clip_id: str, request: Request):
        """One take: the worker posts each finished clip (mp4 + cover) as soon as it is rendered, once per clip."""
        if not house.job_token_ok(jid, request.headers.get("x-job-token", ""), "clip", clip_id):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        body = await reqlimits.read_json(request, reqlimits.WORKER_MAX)
        return house.takes.on_clip(jid, clip_id, base64.b64decode(body["data"]), base64.b64decode(body["cover"]), body.get("meta") or {})

    # ------------------------------------------------------------ MCP
    mcp_oauth.add_mcp_oauth_routes(app, store, studio, s.public_url, lambda r: store.session(r.cookies.get(COOKIE)))

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        auth = request.headers.get("authorization", "")
        a = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if not a:
            # resource_metadata lets Claude/ChatGPT discover our OAuth server and sign the person in
            meta = mcp_oauth.resource_metadata_url(s.public_url)
            return JSONResponse({"error": "Agent token required: Authorization: Bearer hq_..."}, status_code=401,
                                headers={"WWW-Authenticate": f'Bearer realm="huhqueue", resource_metadata="{meta}"'})
        msg = await request.json()
        if isinstance(msg, list):
            out = [r for r in (mcp.handle(studio, a, m) for m in msg) if r]
            return JSONResponse(out) if out else Response(status_code=202)
        r = mcp.handle(studio, a, msg)
        return JSONResponse(r) if r else Response(status_code=202)

    @app.get("/mcp", include_in_schema=False)
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    app.add_middleware(reqlimits.BodyCap)  # every request body capped by path before anything parses it (SECURITY_RULES 6)
    return app

