"""HuhQueue — the Buffer Commit edition web app (FastAPI).

Run:  uvicorn studio.app:create_app --factory   (env: BUFFER_CLIENT_ID, BUFFER_CLIENT_SECRET, SESSION_SECRET, PUBLIC_URL)
"""
from __future__ import annotations

import secrets
import time
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Body, FastAPI, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

import base64
import hmac
import json
import os

from . import activity as activity_mod
from . import staticver
from . import buffer_api, demo, demo_guard, demo_view, house as house_mod, inbox as inbox_mod, limits as reqlimits, mcp, mcp_oauth
from .actions import ActionError, Actor, Studio
from .buffer_api import BufferClient, BufferError
from .config import Settings, load_settings
from .store import Store, sha

STATIC = Path(__file__).parent / "static"
PAGES = ("/", "/app", "/privacy", "/terms", "/security")
COOKIE = "hq_sid"
HSTS = "max-age=31536000; includeSubDomains"
CSP = ("default-src 'self'; img-src 'self' https: data: blob:; media-src 'self' https: blob:; "
       "style-src 'self' 'unsafe-inline'; font-src 'self'; "  # fonts are self-hosted (studio/static/fonts): no third party
       "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
# Sign-in and token endpoints: a tighter per-IP limit for everyone who isn't already signed in (guessing, floods)
AUTH_PATHS = ("/login", "/callback", "/device/", "/oauth/authorize", "/oauth/token", "/oauth/register", "/integrations/")
SECURITY_TXT_EXPIRES = "2027-10-31T00:00:00Z"


def create_app(settings: Settings | None = None, http: httpx.Client | None = None) -> FastAPI:
    s = settings or load_settings()
    store = Store(s.db_path, s.session_secret, s.token_key, s.token_keys_previous)
    if s.migrate_from and s.migrate_from != s.db_path:
        copied = store.copy_from(Store(s.migrate_from, s.session_secret, s.token_key, s.token_keys_previous))
        print(f"migrate: copied {copied or 'nothing (target already has data)'}", flush=True)
    demo_guard.ensure(store)
    # migration: every sealed row moves to the current TOKEN_KEY (the log gets counts only, never values)
    print(f"token keys: {'dedicated TOKEN_KEY' if store.dedicated_key else 'derived from SESSION_SECRET'}; "
          f"{store.rotate_keys()}", flush=True)
    print(f"activity retention: {activity_mod.RETENTION_DAYS} days; {activity_mod.purge(store)}", flush=True)
    buffer = BufferClient(s, store, http=http)
    studio = Studio(s, store, demo.BufferRouter(buffer, demo.DemoBuffer()))
    # docs_url=None: Swagger UI needs a CDN script and an inline script, which the CSP (script-src 'self') blocks, so it
    # rendered blank. /docs/api is our own server-rendered reference; /api/docs redirects there.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        # the render-job watchdog: a worker job with no heartbeat for HQ_JOB_HEARTBEAT_MIN minutes is failed, so a killed
        # worker can't leave a job running forever (also swept lazily when a run or take is read). HQ_WATCHDOG_S=0: off.
        import threading
        stop = threading.Event()
        every = float(os.environ.get("HQ_WATCHDOG_S", "60"))

        def loop():
            while not stop.wait(every):
                try:
                    house.sweep_stale_jobs(force=True)
                except Exception:  # noqa: BLE001  keep watching
                    import traceback
                    traceback.print_exc()
        if every > 0:
            threading.Thread(target=loop, name="hq-job-watchdog", daemon=True).start()
        try:
            yield
        finally:
            stop.set()

    app = FastAPI(title="HuhQueue — built on the Buffer API", docs_url=None, redoc_url=None, lifespan=lifespan)
    house = house_mod.House(studio, http=http)
    studio.house = house
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        house_mod.start_media_check(house)
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

    def failed_token(tok: str, request: Request) -> None:
        """A rejected agent token is a failed sign-in. It is logged for the owner when the token was theirs (revoked or
        expired), with IP and user agent; an unknown token is logged without an owner. The token itself never is."""
        rows = store.q("SELECT user_id, name FROM agent_tokens WHERE token_hash=?", (sha(tok),)) if tok else []
        store.audit(rows[0]["user_id"] if rows else "", "system", "signin_failed", "auth",
                    {"via": "agent token", "reason": "revoked or expired" if rows else "unknown token",
                     "token_label": rows[0]["name"] if rows else "", "path": request.url.path}, ok=False)

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
                failed_token(auth[7:].strip(), request)
                raise ActionError("Invalid or revoked agent token.", 401)
            if write:  # the kill switch stops every agent write, whatever the route (SECURITY_RULES 4)
                studio.kill_switch_check(a, request.url.path)
            return a
        raise ActionError("Not connected. Connect your Buffer account first.", 401)

    @app.exception_handler(ActionError)
    async def _action_err(_, e: ActionError):
        return JSONResponse({"error": str(e)}, status_code=e.status)

    @app.exception_handler(Exception)
    async def _any_err(request: Request, e: Exception):
        # never a stack trace or an exception message to the client; the log keeps the class and the path
        print(f"error: {e.__class__.__name__} on {request.method} {request.url.path}", flush=True)
        return JSONResponse({"error": "Something went wrong on our side. Try again in a moment."}, status_code=500)

    @app.exception_handler(BufferError)
    async def _buffer_err(_, e: BufferError):
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else {}
        return JSONResponse({"error": str(e), "buffer": True}, status_code=e.status, headers=headers)

    # Anonymous and free-sample traffic: a per-IP request rate limit (SECURITY_RULES 3). Real accounts, by cookie or by
    # a real account's agent token, are never slowed here. Media links (/m/) are left alone: Buffer fetches them.
    rate = reqlimits.RateLimit(per_min=int(os.environ.get("HQ_ANON_RPM", "240")), demo_per_min=int(os.environ.get("HQ_DEMO_PER_MIN", "6")))
    rate.auth_per_min = int(os.environ.get("HQ_AUTH_PER_MIN", "30"))
    app.state.rate = rate

    def _real_account(request: Request) -> bool:
        sess = store.session(request.cookies.get(COOKIE))
        uid = sess.get("user_id") if sess else None
        if not uid:
            auth = request.headers.get("authorization", "")
            ag = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
            uid = ag.uid if ag else None
        return bool(uid) and not demo_guard.is_demo(uid)

    # Not counted: the render worker, and the brand kit preview images (cache reads by an unguessable key, 404 otherwise;
    # the builder loads ~24 of them per kit, which would eat the sample's per-IP budget in one visit).
    RATE_FREE = ("/api/worker/", "/api/brand-kits/preview-img/")

    @app.middleware("http")
    async def _rate(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/api/", "/mcp", "/oauth/", "/demo") + AUTH_PATHS) and not path.startswith(RATE_FREE) \
                and not _real_account(request):
            ip = demo_guard.client_ip(request)
            general = path.startswith(("/api/", "/mcp", "/oauth/", "/demo"))
            wait = rate.hit("ip:" + sha("ip:" + ip), rate.per_min) if general else 0
            if not wait and path == "/demo":
                wait = rate.hit("demo:" + sha("ip:" + ip), rate.demo_per_min)
            if not wait and path.startswith(AUTH_PATHS):
                wait = rate.hit("auth:" + sha("ip:" + ip), rate.auth_per_min)
            if wait:
                return JSONResponse({"error": "Too many requests from this network. Wait a minute, or connect Buffer."},
                                    status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        return await call_next(request)

    # The view-only demo (studio/demo_view.py): a demo session's writes are answered "In the real app this would ..."
    # without touching data, and nothing it does (or the background work it starts) may call out of the server.
    demo_view.install()

    def _demo_uid(request: Request) -> str | None:
        sess = store.session(request.cookies.get(COOKIE))
        uid = sess.get("user_id") if sess else None
        if not uid:
            auth = request.headers.get("authorization", "")
            ag = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
            uid = ag.uid if ag else None
        return uid if demo_guard.is_demo(uid) else None

    @app.middleware("http")
    async def _demo_view_only(request: Request, call_next):
        uid = _demo_uid(request) if request.url.path.startswith(("/api/", "/mcp", "/oauth/", "/send/", "/u/")) else None
        if not uid:
            return await call_next(request)
        why = demo_view.blocked(request.method, request.url.path)
        if why:
            return JSONResponse({"error": why, "demo": True, "view_only": True}, status_code=403, headers={"X-HQ-Demo": "view-only"})
        tok = demo_view.OFFLINE.set(True)
        try:
            return await call_next(request)
        finally:
            demo_view.OFFLINE.reset(tok)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static/") and resp.status_code == 200 and request.query_params.get("v") and \
                staticver.cache_header(request.url.path[len("/static/"):], request.query_params.get("v")):
            resp.headers["Cache-Control"] = staticver.IMMUTABLE  # a versioned URL (staticver): a deploy changes the URL
        elif request.url.path in PAGES or request.url.path.startswith(("/static/", "/docs", "/send")):
            resp.headers["Cache-Control"] = "no-cache"  # always revalidate, so a deploy is picked up at once
        elif request.url.path.startswith("/api/") and "cache-control" not in resp.headers:
            resp.headers["Cache-Control"] = "no-store"  # API answers carry CSRF tokens and private data: never cached
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=(), usb=()")
        if request.url.scheme == "https":  # behind Render's TLS the forwarded scheme is trusted (uvicorn --proxy-headers)
            resp.headers.setdefault("Strict-Transport-Security", HSTS)
        return resp

    @app.middleware("http")
    async def _activity_ctx(request: Request, call_next):
        # who is asking, for the activity log: surface, IP and user agent (kept only on sign-in rows), and an optional
        # model hint an agent may send (X-Agent-Model, or _meta.model on an MCP tools/call)
        path = request.url.path
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        tok = activity_mod.begin(surface="mcp" if path.startswith("/mcp") else ("rest" if bearer else "ui"),
                                 ip=demo_guard.client_ip(request), ua=request.headers.get("user-agent", ""),
                                 model_hint=(request.headers.get("x-agent-model") or "")[:80] or None)
        try:
            return await call_next(request)
        finally:
            activity_mod.end(tok)

    # ------------------------------------------------------------ pages
    # "/" is the landing page for visitors (crawlable, no app JS) and the app for a connected browser. Both read the same
    # rule as /api/session's "connected", so the landing never bounces a connected browser and the app never shows signed out.
    def _connected(request: Request) -> bool:
        sess = store.session(request.cookies.get(COOKIE))
        return bool(sess and sess["user_id"] and store.user(sess["user_id"]) and store.tokens(sess["user_id"]))

    @app.get("/", include_in_schema=False)
    def index(request: Request):
        return HTMLResponse(staticver.page("index.html" if _connected(request) else "landing.html"))

    @app.get("/app", include_in_schema=False)
    def app_page(request: Request):
        return RedirectResponse("/", 302) if not _connected(request) else HTMLResponse(staticver.page("index.html"))

    for _path, _file in (("/privacy", "privacy.html"), ("/terms", "terms.html"), ("/security", "security.html")):
        def _legal(_f=_file):
            return HTMLResponse(staticver.page(_f))
        app.add_api_route(_path, _legal, methods=["GET"], include_in_schema=False)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC / "favicon.ico", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/og.png", include_in_schema=False)
    def og_image():
        return FileResponse(STATIC / "brand" / "og.png", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/robots.txt", include_in_schema=False)
    def robots():
        body = ("User-agent: *\nAllow: /\nAllow: /docs\nAllow: /privacy\nAllow: /terms\nAllow: /security\nAllow: /docs/api\n"
                "Disallow: /api/\nDisallow: /mcp\nDisallow: /callback\nDisallow: /login\nDisallow: /demo\nDisallow: /device/\n"
                "Disallow: /oauth/\nDisallow: /m/\nDisallow: /integrations/\n"
                f"Sitemap: {s.public_url}/sitemap.xml\n")
        return Response(body, media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap():
        from . import docs as docs_mod
        urls = ["/", *[f"/docs/{d}" if d else "/docs" for d in docs_mod.slugs()], "/docs/api", "/privacy", "/terms", "/security"]
        body = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
            f"  <url><loc>{s.public_url}{u}</loc><lastmod>{docs_mod.LASTMOD}</lastmod></url>\n" for u in urls) + "</urlset>\n"
        return Response(body, media_type="application/xml")

    @app.get("/docs/search.json", include_in_schema=False)
    def docs_search():
        from . import docs as docs_mod
        return JSONResponse(docs_mod.search_index(), headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/docs/api", include_in_schema=False)
    def api_reference():
        from . import docs as docs_mod
        from . import mcp as mcp_mod
        return Response(docs_mod.api_page(app.openapi(), mcp_mod.TOOLS), media_type="text/html",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/api/docs", include_in_schema=False)
    def api_docs_moved():
        return RedirectResponse("/docs/api", 308)

    @app.get("/docs", include_in_schema=False)
    @app.get("/docs/{slug}", include_in_schema=False)
    def docs_page(slug: str = ""):
        from . import docs as docs_mod
        html = docs_mod.page(slug)
        if html is None:
            return Response(docs_mod.missing(), status_code=404, media_type="text/html")
        return Response(staticver.rewrite(html), media_type="text/html")

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

    @app.get("/.well-known/security.txt", include_in_schema=False)
    @app.get("/security.txt", include_in_schema=False)
    def security_txt():
        repo = (s.repo_url or "https://github.com/dataanalystram/buffer-contest").rstrip("/")
        body = (f"Contact: {repo}/security/advisories/new\n"
                f"Expires: {SECURITY_TXT_EXPIRES}\n"
                "Preferred-Languages: en\n"
                f"Canonical: {s.public_url}/.well-known/security.txt\n"
                f"Policy: {repo}/blob/main/docs/SECURITY.md\n")
        return Response(body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/healthz")
    def health():
        # Touches the database so uptime pings also keep a free Postgres (Supabase) from pausing for inactivity.
        mh = getattr(house, "media_health", None) or {}
        return {"ok": True, "configured": s.configured, "db": store.ping(),
                "media": {"checked_at": mh.get("checked_at"), "missing_files": mh.get("files_missing", 0),
                          "unique_missing_files": mh.get("unique_files_missing", 0)} if mh else {"checked_at": None}}

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
            store.audit((sess or {}).get("user_id") or "", "system", "signin_failed", "auth",
                        {"via": "buffer oauth", "reason": (error or "")[:60]}, ok=False)
            return RedirectResponse("/?error=" + quote(error_description or error), 302)
        pending = store.unseal(sess["oauth"]) if sess else None
        if not pending or not state or not secrets.compare_digest(state, pending.get("state") or ""):
            store.audit((sess or {}).get("user_id") or "", "system", "signin_failed", "auth",
                        {"via": "buffer oauth", "reason": "state mismatch or expired"}, ok=False)
            return RedirectResponse("/?error=" + quote("Sign-in expired or didn't start here. Try Connect again."), 302)
        store.update_session(sid, oauth=None)
        try:
            tokens = buffer_api.exchange_code(s, buffer.http, code, pending["verifier"])
        except BufferError as e:
            store.audit((sess or {}).get("user_id") or "", "system", "signin_failed", "auth",
                        {"via": "buffer oauth", "reason": "code exchange failed"}, ok=False)
            return RedirectResponse("/?error=" + quote(str(e)), 302)
        # rotate the session id at login (fixation defence)
        store.drop_session(sid)
        new_sid = secrets.token_urlsafe(32)
        store.new_session(new_sid, secrets.token_urlsafe(24))
        try:
            # login=True: never waits on a block another account's 429 set; Buffer's own 429 is handled below
            acct = buffer.account(token=tokens["access_token"], login=True)
        except BufferError as e:
            if e.status != 429:
                store.audit("", "system", "signin_failed", "auth", {"via": "buffer oauth", "reason": "account lookup failed"}, ok=False)
                resp = RedirectResponse("/?error=" + quote(str(e)), 302)
                set_cookie(resp, new_sid)
                return resp
            # Buffer is busy (its limits are shared by every HuhQueue user). Keep the tokens sealed on this session as a
            # pending sign-in and finish it once, after Retry-After + jitter, the next time the page asks /api/session.
            wait = _signin_wait(e.retry_after)
            store.update_session(new_sid, oauth=store.seal({"signin": {"tokens": tokens, "retry_at": time.time() + wait,
                                                                       "next": pending.get("next") or "", "tries": 0}}))
            store.audit("", "system", "signin_deferred", "auth", {"via": "buffer oauth", "reason": "buffer rate limit",
                                                                  "retry_in": int(wait)}, ok=True)
            resp = RedirectResponse("/?connecting=" + str(int(wait)), 302)
            set_cookie(resp, new_sid)
            return resp
        _finish_signin(new_sid, tokens, acct)
        resp = RedirectResponse(pending.get("next") or "/?connected=1", 302)
        set_cookie(resp, new_sid)
        return resp

    def _signin_wait(retry_after: int | None) -> float:
        """Buffer's Retry-After plus 5-30 s of jitter, so sign-ins deferred together don't all retry in the same second."""
        return float(max(1, int(retry_after or 60))) + 5 + secrets.randbelow(26)

    def _finish_signin(sid: str, tokens: dict, acct: dict) -> None:
        orgs = acct.get("organizations") or [{}]
        store.upsert_user(acct["id"], acct.get("email") or "", acct.get("name") or "", orgs[0].get("id", ""),
                          orgs[0].get("name", ""), tokens)
        for k in [k for k in buffer._cache if len(k) > 1 and k[1] == acct["id"] and k[0] != "account"]:
            buffer._cache.pop(k, None)  # a fresh sign-in re-reads channels and posts; the account it just read stays cached
        store.update_session(sid, user_id=acct["id"], oauth=None)
        store.audit(acct["id"], "you", "connect_buffer", "auth", {"scopes": tokens.get("scope", "")})

    def _pending_signin(sid: str, sess: dict) -> dict | None:
        """A sign-in Buffer's rate limit deferred: finish it now if Retry-After has passed (one Buffer call, only when the
        page asks: no polling), else say how long is left. Returns the banner state, or None once there is nothing pending."""
        box = store.unseal(sess.get("oauth")) if sess.get("oauth") else None
        p = (box or {}).get("signin")
        if not p:
            return None
        left = p["retry_at"] - time.time()
        if left <= 0:
            try:
                acct = buffer.account(token=p["tokens"]["access_token"], login=True)
            except BufferError as e:
                if e.status != 429:
                    store.update_session(sid, oauth=None)
                    store.audit("", "system", "signin_failed", "auth", {"via": "buffer oauth", "reason": "deferred lookup failed"}, ok=False)
                    return {"failed": True, "message": "Buffer didn't finish the sign-in. Connect again."}
                left = _signin_wait(e.retry_after)
                p.update(retry_at=time.time() + left, tries=int(p.get("tries") or 0) + 1)
                store.update_session(sid, oauth=store.seal({"signin": p}))
            else:
                _finish_signin(sid, p["tokens"], acct)
                return {"done": True, "next": p.get("next") or ""}
        mins = max(1, int((left + 59) // 60))
        return {"retry_in": int(left) + 1, "message": f"Buffer is busy; finishing connecting in {mins} min."}

    # try-it workspace: sample channels and an agent post waiting for approval; nothing reaches Buffer
    # at most CAPS['workspace'] new sample workspaces per IP a day; stale demo users are purged after a day on each new demo
    @app.get("/demo", include_in_schema=False)
    def demo_start(request: Request, url: str = ""):
        ip = demo_guard.client_ip(request)
        if not demo_guard.workspace_ok(store, ip):
            return RedirectResponse("/?error=" + quote(demo_guard.why("workspace")), 302)
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
            store.audit(rows[0]["user_id"] if rows else "", "system", "signin_failed", "auth",
                        {"via": "device link", "reason": "used or expired" if rows else "unknown link"}, ok=False)
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
            sess = store.session(sid)
            if sess and sess.get("user_id"):
                store.audit(sess["user_id"], "you", "logout", "auth", {})
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
        signin = None if sess["user_id"] else _pending_signin(sid, sess)
        if signin and signin.get("done"):
            sess = store.session(sid) or sess
        connected = bool(sess["user_id"] and store.user(sess["user_id"]) and store.tokens(sess["user_id"]))
        body = {"configured": s.configured, "connected": connected, "csrf": sess["csrf"],
                "user": studio.me(Actor(sess["user_id"])) if connected else None, "mcp_url": s.public_url + "/mcp",
                "repo": s.repo_url, "demo": connected and demo.is_demo(sess["user_id"])}
        if body["demo"]:
            body["demo_left"] = demo_guard.left(store, sess["user_id"])  # what the free sample may still spend today
        if signin and not connected:
            body["connecting"] = signin  # {"retry_in", "message"} or {"failed", "message"}: the landing's banner
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

    @app.get("/api/thumb")
    def story_thumb(request: Request, url: str = "", img: str = ""):
        """A story's picture, read and re-encoded server-side (studio/thumbs.py); 404 -> the page shows the favicon."""
        from . import thumbs
        a = actor(request)
        if demo_guard.is_demo(a.uid):  # the free sample never makes this server fetch a page (SECURITY_RULES 3)
            return Response(status_code=204, headers={"Cache-Control": "private, max-age=3600"})
        if not thumbs.allow(a.uid):
            return JSONResponse({"error": "Too many thumbnails at once. They load again in a minute."}, status_code=429)
        try:
            data = thumbs.get(url, img, http=house.http)
        except thumbs.ThumbError as e:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        if not data:
            return Response(status_code=204, headers={"Cache-Control": "private, max-age=3600"})  # no picture: the page shows the favicon
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/link/preview")
    def link_preview(request: Request, body: dict = Body(default={})):
        """Make a post › From a link: what the agent will read from a pasted link (the X reader for posts, the page reader
        for articles), read server-side through netguard. Same limiter as thumbnails; the free sample never fetches."""
        from . import link_preview as lp
        a = actor(request)
        return lp.preview(a, str((body or {}).get("url") or ""), http=house.http, demo=demo_guard.is_demo(a.uid))

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

    @app.patch("/api/agent-tokens/{token_id}")
    def token_cap(token_id: int, request: Request, body: dict = Body(...)):
        return studio.set_agent_cap(actor(request, write=True), token_id, body.get("daily_cap"))

    @app.delete("/api/agent-tokens/{token_id}")
    def revoke(token_id: int, request: Request):
        return studio.revoke_agent_token(actor(request, write=True), token_id)

    @app.get("/api/activity")
    def activity_page(request: Request, tab: str = "all", q: str = "", limit: int = 25, cursor: str = "",
                      app_: str = Query("", alias="app"), model: str = "", failed: bool = False,
                      from_: str = Query("", alias="from"), to: str = "", group: str = ""):
        """Activity log, newest first, cursor-paginated (limit 25 | 50 | 100; pass next_cursor back as cursor).
        group=run folds each Make a post run's AI calls into one row (with the calls kept in ``calls``)."""
        return studio.activity(actor(request), {"tab": tab, "q": q, "limit": limit, "cursor": cursor, "app": app_, "model": model,
                                                "failed": failed, "from": from_, "to": to, "group": group if group == "run" else ""})

    @app.get("/api/activity/export.csv")
    def activity_csv(request: Request, tab: str = "all", q: str = "", app_: str = Query("", alias="app"), model: str = "",
                     failed: bool = False, from_: str = Query("", alias="from"), to: str = ""):
        a = actor(request)
        f = {"tab": tab, "q": q, "app": app_, "model": model, "failed": failed, "from": from_, "to": to}
        try:
            body = activity_mod.export_csv(store, a.uid, f, show_ip=a.kind == "human")
        except ValueError as e:
            raise ActionError(str(e))
        store.audit(a.uid, a.label, "export_activity", "read", {"tab": tab})
        name = f"huhqueue-activity-{tab}-{time.strftime('%Y%m%d')}.csv"
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})

    @app.get("/api/audit")
    def audit(request: Request, limit: int = 100):
        return studio.audit(actor(request), min(limit, 500))

    # ------------------------------------------------------------ HuhQueue: media, brand kit, ledger, agent, calendar
    def _head(mime: str, size: int, cache: str) -> Response:
        """HEAD: the file's type and length, no body. Buffer's fetcher asks with HEAD before it downloads (Render logs,
        2 Nov 2026: HEAD /m/<token>.mp4 from Buffer answered 405, and Buffer said "Video could not be read from its URL")."""
        return Response(b"", media_type=mime, headers={"Content-Length": str(size), "Accept-Ranges": "bytes", "Cache-Control": cache})

    @app.api_route("/m/{token}", methods=["GET", "HEAD"], include_in_schema=False)
    def media(token: str, request: Request):
        head = request.method == "HEAD"
        lib_item = house.library.by_token(token)
        if lib_item:  # an uploaded Library file: read only the chunks a range asks for
            if head:
                return _head(lib_item["mime"], int(lib_item["size"] or 0), "public, max-age=86400")
            return app.state.library_serve(house.library._file_of(lib_item), request, "public, max-age=86400")
        if head:
            info = house.media_head(token)
            if not info:
                return JSONResponse({"error": "not found"}, status_code=404)
            return _head(info["mime"], info["size"], "public, max-age=86400")
        m = house.get_media(token)
        if not m:
            return JSONResponse({"error": "not found"}, status_code=404)
        data, size = m["data"], len(m["data"])
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=86400"}
        from .library import parse_range
        rng_h = request.headers.get("range", "")
        rng = parse_range(rng_h, size)
        if rng_h and rng is None:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        if rng:
            start, end = rng
            return Response(data[start:end + 1], status_code=206, media_type=m["mime"],
                            headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"})
        return Response(data, media_type=m["mime"], headers=headers)

    @app.get("/api/media/health")
    def media_health(request: Request, refresh: int = 0):
        """Ready posts whose files are gone (deleted from the Library): the startup / periodic check's result for you."""
        a = actor(request)
        if refresh:
            house.media_audit()
        return house.media_health_for(a)

    @app.post("/api/media")
    async def upload(request: Request):
        """Authenticate first, then read the body with a cap (Content-Length checked, then counted as it streams), so
        nothing is buffered or decoded for a caller who isn't allowed in (SECURITY_RULES 6)."""
        a = actor(request, write=True)
        cap = reqlimits.DEMO_MEDIA_MAX if demo_guard.is_demo(a.uid) else reqlimits.MEDIA_MAX
        body = await reqlimits.read_json(request, cap)
        return house.upload(a, str(body.get("name", "")), str(body.get("data", "")), str(body.get("source", "upload")))

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
        return house.ledger.add(actor(request, write=True), body.get("correction", ""), body.get("scope"))

    @app.post("/api/ledger/{rule_id}")
    def ledger_toggle(rule_id: int, request: Request, body: dict = Body(...)):
        return house.set_rule(actor(request, write=True), rule_id, bool(body.get("active")))

    @app.delete("/api/ledger/{rule_id}")
    def ledger_delete(rule_id: int, request: Request):
        return house.delete_rule(actor(request, write=True), rule_id)

    @app.post("/api/check")
    def check(request: Request, body: dict = Body(...)):
        return house.check(actor(request), body.get("text", ""), body.get("media_token", ""), str(body.get("service") or ""))

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
    def run_list(request: Request, limit: int = 20):
        return house.runs(actor(request), max(1, min(int(limit), 100)))

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
    def etagged(request: Request, data, ignore: tuple = ("now",)):
        """Buffer reads are cached server-side per account (buffer_api.POSTS_TTL); an ETag lets the browser skip the body
        too when nothing changed. ?refresh=1 (the Refresh buttons) re-reads Buffer once."""
        import hashlib
        body = {k: v for k, v in data.items() if k not in ignore} if isinstance(data, dict) else data
        tag = '"' + hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()[:20] + '"'
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers={"ETag": tag})
        return JSONResponse(data, headers={"ETag": tag, "Cache-Control": "private, no-cache"})

    @app.get("/api/inbox")
    def inbox(request: Request, refresh: int = 0):
        a = actor(request)
        if refresh:
            studio.buffer.refresh(a.uid)
        return etagged(request, inbox_mod.build(studio, a))

    @app.get("/api/calendar")
    def calendar(request: Request, refresh: int = 0):
        a = actor(request)
        if refresh:
            studio.buffer.refresh(a.uid)
        return etagged(request, house.calendar(a))

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

    @app.api_route("/sample/{name}", methods=["GET", "HEAD"], include_in_schema=False)
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
        error = str(body.get("error") or "")
        if body.get("status") == "failed" and not error:
            error = "The video worker failed."
        try:
            data = base64.b64decode(body["data"]) if body.get("data") else None
            return house.finish_video(jid, data, body.get("meta"), error, body=body)
        finally:
            house.burn_job_token(jid)
            if error and body.get("log_tail"):
                house.keep_job_log(jid, body["log_tail"])

    @app.post("/api/worker/jobs/{jid}/heartbeat", include_in_schema=False)
    def worker_heartbeat(jid: int, request: Request):
        """The worker is still alive (every ~60 s). The job's token, any number of times, until the result burns it."""
        if not house.job_token_ok(jid, request.headers.get("x-job-token", ""), "beat"):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return {"ok": house.job_heartbeat(jid)}

    @app.post("/api/worker/jobs/{jid}/clips/{clip_id}", include_in_schema=False)
    async def worker_clip(jid: int, clip_id: str, request: Request):
        """One take: the worker posts each finished clip (mp4 + cover) as soon as it is rendered, once per clip."""
        if not house.job_token_ok(jid, request.headers.get("x-job-token", ""), "clip", clip_id):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        body = await reqlimits.read_json(request, reqlimits.WORKER_MAX)
        if house.job_kind(jid) == "media_recap":  # the weekly recap: cover, story slides, Follow card, Reel (no cover frame)
            try:
                return house.on_recap_file(jid, clip_id, base64.b64decode(body["data"]), body.get("meta") or {})
            except ActionError as e:
                return JSONResponse({"error": str(e)}, status_code=e.status)
        return house.takes.on_clip(jid, clip_id, base64.b64decode(body["data"]), base64.b64decode(body["cover"]), body.get("meta") or {})

    # ------------------------------------------------------------ Brand studio + Sources agent (UI = REST = MCP)
    from . import brand_routes
    brand_routes.add_routes(app, house, actor)
    from . import ledger as ledger_mod  # Style Ledger v2: groups, scope, suggestions from edits, check_draft, review links
    ledger_mod.add_routes(app, house, actor)
    from . import brand_onboard  # website -> brand kit -> "Your brand" stills; the demo's sample brands (UI = REST = MCP)
    brand_onboard.add_routes(app, house, actor)
    from . import brand_kits  # several kits, one active; the builder's live gallery (UI = REST = MCP)
    brand_kits.add_routes(app, house, actor)
    from . import agent_routes  # agent brain, natural-language edits, onboarding, recommendations (UI = REST = MCP)
    agent_routes.add_routes(app, house, actor)
    from . import reel_audio  # Instagram music on Reels (search + pick per package; UI = REST = MCP)
    reel_audio.add_routes(app, house, actor)
    from . import library as library_mod  # the Library: uploads + generated media (UI = REST = MCP)
    library_mod.add_routes(app, house, actor)
    from . import uploads as uploads_mod  # one-time upload URLs + chunked uploads (UI = REST = MCP)
    uploads_mod.add_routes(app, house, actor)
    from . import send as send_mod  # Send a file page (/send): short code + QR (UI = REST = MCP)
    send_mod.add_routes(app, house, actor)
    from . import share_import as share_mod  # import_from_link: cloud-drive share links (UI = REST = MCP)
    share_mod.add_routes(app, house, actor)
    from . import integrations as integrations_mod  # Settings -> Integrations: per-user keys + the Product Hunt callback
    integrations_mod.add_routes(app, house, actor, COOKIE, s.public_url, s.secure_cookies)

    # ------------------------------------------------------------ MCP
    mcp_oauth.add_mcp_oauth_routes(app, store, studio, s.public_url, lambda r: store.session(r.cookies.get(COOKIE)))

    @app.post("/mcp")
    async def mcp_endpoint(request: Request):
        auth = request.headers.get("authorization", "")
        a = studio.resolve_agent(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
        if not a:
            if auth.lower().startswith("bearer ") and auth[7:].strip():
                failed_token(auth[7:].strip(), request)
            # resource_metadata lets Claude/ChatGPT discover our OAuth server and sign the person in
            meta = mcp_oauth.resource_metadata_url(s.public_url)
            return JSONResponse({"error": "Agent token required: Authorization: Bearer hq_..."}, status_code=401,
                                headers={"WWW-Authenticate": f'Bearer realm="huhqueue", resource_metadata="{meta}"'})
        msg = await request.json()
        # Tools run in the thread pool, never on the event loop: submit_post waits on Buffer, and Buffer downloads our
        # /m/ media while it decides. Run inline, the whole server stopped answering for the call (Render logs, 2 Nov 2026:
        # /healthz went silent for 13 s during create_package) and Buffer said "Image could not be read from its URL".
        if isinstance(msg, list):
            res = await run_in_threadpool(lambda: [mcp.handle(studio, a, m) for m in msg])
            out = [r for r in res if r]
            return JSONResponse(out) if out else Response(status_code=202)
        r = await run_in_threadpool(mcp.handle, studio, a, msg)
        return JSONResponse(r) if r else Response(status_code=202)

    @app.get("/mcp", include_in_schema=False)
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    app.add_middleware(reqlimits.BodyCap)  # every request body capped by path before anything parses it (SECURITY_RULES 6)
    return app

