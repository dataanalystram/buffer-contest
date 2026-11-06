"""OAuth 2.1 for the MCP endpoint, so people can add HuhQueue from Claude.ai or ChatGPT settings without pasting a token.

Claude and ChatGPT discover the server through RFC 9728 / RFC 8414 metadata, register themselves (RFC 7591 dynamic
client registration), and send the person through /oauth/authorize with PKCE (S256 only). The person signs in with
Buffer if needed and picks the same leash an agent token has (review / draft / trusted, daily cap). The access token
we hand out *is* an ordinary agent token (hq_...), so every rule, cap, kill switch and audit entry applies unchanged
and the token shows up (and can be revoked) on the Agents screen.
"""
from __future__ import annotations

import base64
import hashlib
import html
import json
import secrets
import time
from urllib.parse import parse_qs, quote, urlencode, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .actions import POLICIES, Actor, Studio
from .store import Store, sha

CODE_TTL = 600
ACCESS_DAYS = 30
REFRESH_DAYS = 90


# Assistants whose redirect host we know (SECURITY_RULES 5). Any other client is labelled "unverified" on the consent
# screen, can't be given the "trusted" policy, and its token is named for its redirect host, not only the name it chose.
KNOWN_HOSTS = ("claude.ai", "claude.com", "anthropic.com", "chatgpt.com", "openai.com")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
REGISTER_PER_HOUR = 20  # per client IP


def redirect_host(uri: str) -> str:
    return (urlparse(uri).hostname or "").lower()


def verified_host(host: str) -> bool:
    """Known assistant hosts (and their subdomains), or this computer (a local MCP client the person runs)."""
    return host in LOCAL_HOSTS or any(host == d or host.endswith("." + d) for d in KNOWN_HOSTS)


def resource_metadata_url(public_url: str) -> str:
    return public_url + "/.well-known/oauth-protected-resource/mcp"


def _ok_redirect(uri: str) -> bool:
    u = urlparse(uri)
    if u.scheme == "https" and u.netloc:
        return True
    return u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")


def _err(error: str, desc: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": desc}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def _form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8", "replace")
    if request.headers.get("content-type", "").startswith("application/json"):
        data = json.loads(body or "{}")
        return {k: str(v) for k, v in data.items()}
    return {k: v[0] for k, v in parse_qs(body).items()}


def add_mcp_oauth_routes(app: FastAPI, store: Store, studio: Studio, public_url: str, session_of) -> None:
    """session_of(request) -> session row or None (the signed-in browser session)."""

    def client(cid: str) -> dict | None:
        rows = store.q("SELECT * FROM oauth_clients WHERE client_id=?", (cid,))
        if not rows:
            return None
        c = dict(rows[0])
        c["redirect_uris"] = json.loads(c["redirect_uris"] or "[]")
        return c

    def label(c: dict, redirect_uri: str) -> str:
        host = redirect_host(redirect_uri or (c["redirect_uris"] or [""])[0])
        return f"{c['client_name']} via {host}" + ("" if verified_host(host) else " (unverified)")

    def mint(uid: str, c: dict, policy: str, cap: int, redirect_uri: str = "", refreshed: bool = False, parent: int | None = None) -> dict:
        from . import activity
        tok = studio.create_agent_token(Actor(uid), f"{label(c, redirect_uri)} (connected)", policy, cap, days=ACCESS_DAYS)
        app = activity.classify_app(c.get("client_name", ""), redirect_host(redirect_uri or (c["redirect_uris"] or [""])[0]))
        store.x("UPDATE agent_tokens SET client_id=?, app=?, parent_id=? WHERE id=?", (c["client_id"], app, parent, tok["id"]))  # attributable writes
        activity.note(client_id=c["client_id"])
        store.audit(uid, "you", "oauth_token_refreshed" if refreshed else "oauth_token_issued", "auth",
                    {"client": activity.clean_text(c.get("client_name"), 80), "app": activity.APPS.get(app, app), "token_id": tok["id"]})
        refresh = "hqr_" + secrets.token_urlsafe(32)
        store.x("INSERT INTO oauth_refresh(token_hash,client_id,user_id,agent_id,policy,daily_cap,expires,used) "
                "VALUES(?,?,?,?,?,?,?,0)", (sha(refresh), c["client_id"], uid, tok["id"], policy, cap,
                                             time.time() + REFRESH_DAYS * 86400))
        return {"access_token": tok["token"], "token_type": "Bearer", "expires_in": ACCESS_DAYS * 86400,
                "refresh_token": refresh, "scope": "huhqueue"}

    def inherit_caps(parent: int, child: int) -> None:
        """Copy the parent's current daily and admin hourly caps onto its refresh successor (and the successor's refresh
        record), and carry the hourly request window over, so a refresh never resets an operator's limit."""
        from . import opsctl
        store.x("UPDATE agent_tokens SET daily_cap=COALESCE((SELECT p.daily_cap FROM agent_tokens p WHERE p.id=?), daily_cap), "
                "admin_hourly_cap=(SELECT p.admin_hourly_cap FROM agent_tokens p WHERE p.id=?) WHERE id=?", (parent, parent, child))
        store.x("UPDATE oauth_refresh SET daily_cap=(SELECT daily_cap FROM agent_tokens WHERE id=?) WHERE agent_id=?", (child, child))
        opsctl.token_inherit(parent, child)

    # ---------------------------------------------------------------- discovery
    @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
    def protected_resource():
        return {"resource": public_url + "/mcp", "authorization_servers": [public_url],
                "scopes_supported": ["huhqueue"], "bearer_methods_supported": ["header"],
                "resource_name": "HuhQueue"}

    @app.get("/.well-known/oauth-authorization-server", include_in_schema=False)
    @app.get("/.well-known/oauth-authorization-server/mcp", include_in_schema=False)
    def auth_server():
        return {"issuer": public_url, "authorization_endpoint": public_url + "/oauth/authorize",
                "token_endpoint": public_url + "/oauth/token", "registration_endpoint": public_url + "/oauth/register",
                "response_types_supported": ["code"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "code_challenge_methods_supported": ["S256"], "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": ["huhqueue"]}

    # ---------------------------------------------------------------- registration (RFC 7591)
    reg_hits: dict[str, list[float]] = {}

    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
        from . import demo_guard
        ip = sha("ip:" + demo_guard.client_ip(request))
        now = time.time()
        hits = [t for t in reg_hits.get(ip, []) if now - t < 3600]
        if len(hits) >= REGISTER_PER_HOUR:
            return _err("too_many_requests", "Too many app registrations from this network. Try again later.", 429)
        reg_hits[ip] = hits + [now]
        if len(reg_hits) > 5000:
            reg_hits.clear()
        try:
            meta = await request.json()
        except ValueError:
            return _err("invalid_client_metadata", "Send JSON.")
        uris = meta.get("redirect_uris") or []
        if not isinstance(uris, list) or not uris or not all(isinstance(u, str) and _ok_redirect(u) for u in uris):
            return _err("invalid_redirect_uri", "redirect_uris must be https (or http on localhost).")
        name = str(meta.get("client_name") or "AI assistant").strip()[:60]
        cid = "hqc_" + secrets.token_urlsafe(16)
        store.x("INSERT INTO oauth_clients(client_id,client_name,redirect_uris,created) VALUES(?,?,?,?)",
                (cid, name, json.dumps(uris[:10]), time.time()))
        return JSONResponse({"client_id": cid, "client_name": name, "redirect_uris": uris[:10],
                             "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                             "response_types": ["code"], "client_id_issued_at": int(time.time())}, status_code=201)

    # ---------------------------------------------------------------- consent
    def check(params: dict) -> tuple[dict | None, str]:
        c = client(params.get("client_id", ""))
        if not c:
            return None, "Unknown app. Remove HuhQueue from your assistant's connectors and add it again."
        if params.get("redirect_uri") not in c["redirect_uris"]:
            return None, "This app's redirect address doesn't match what it registered."
        if params.get("response_type") != "code":
            return None, "Only response_type=code is supported."
        if params.get("code_challenge_method") != "S256" or not params.get("code_challenge"):
            return None, "PKCE with S256 is required."
        return c, ""

    @app.get("/oauth/authorize", include_in_schema=False)
    def authorize(request: Request):
        params = dict(request.query_params)
        c, problem = check(params)
        if not c:
            return HTMLResponse(page("Can't connect", f"<p>{html.escape(problem)}</p>"), status_code=400)
        sess = session_of(request)
        if not sess or not sess.get("user_id"):
            nxt = "/oauth/authorize?" + urlencode(params)
            return RedirectResponse("/login?next=" + quote(nxt, safe=""), 302)
        u = store.user(sess["user_id"]) or {}
        hidden = "".join(f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">'
                         for k, v in params.items() if k in ("client_id", "redirect_uri", "state", "code_challenge",
                                                             "code_challenge_method", "scope", "resource"))
        opts = {"review": "Ask me first: posts wait in Approvals until I approve them",
                "draft": "Drafts only: posts land as Buffer drafts",
                "trusted": "Trusted: may schedule straight to my Buffer queue"}
        host = redirect_host(params.get("redirect_uri", ""))
        known = verified_host(host)
        if not known:
            opts.pop("trusted")  # a name an app picked for itself never earns a token that posts without you
        radios = "".join(
            f'<label class="opt"><input type="radio" name="policy" value="{p}" data-testid="oauth-policy-{p}"'
            f'{" checked" if p == "review" else ""}> {html.escape(opts[p])}</label>' for p in POLICIES if p in opts)
        badge = ('<span class="badge" data-testid="oauth-verified">Known assistant</span>' if known else
                 '<span class="badge warn" data-testid="oauth-unverified">Unverified app</span>')
        warn = "" if known else (
            f'<p class="warn" data-testid="oauth-unverified-note">HuhQueue can\'t confirm who this app is: the name '
            f'"{html.escape(c["client_name"])}" is what it calls itself. Only allow it if you started this from '
            f'<b>{html.escape(host)}</b>. It can\'t be given the Trusted option.</p>')
        body = f"""
<p><b>{html.escape(c['client_name'])}</b> {badge} wants to use HuhQueue for
<b>{html.escape(u.get('name') or u.get('email') or 'your Buffer account')}</b>
({html.escape(u.get('org_name') or 'Buffer')}).</p>
<p data-testid="oauth-redirect-host">After you choose, you'll be sent to <b>{html.escape(host)}</b>, and that site gets the access.</p>
{warn}
<form method="post" action="/oauth/authorize" class="stack" data-testid="oauth-consent">
<input type="hidden" name="csrf" value="{html.escape(sess['csrf'])}">{hidden}
<fieldset class="stack"><legend>What it may do</legend>{radios}</fieldset>
<label class="field">Buffer posts per day at most
<input type="number" name="daily_cap" value="50" min="1" max="500" data-testid="oauth-cap" aria-label="Buffer posts per day at most"></label>
<p class="hint">Only Buffer posts, drafts and ideas count. Renders and previews don't.</p>
<p class="hint">You can revoke it any time under Agents. The kill switch stops it at once.</p>
<div class="row"><button class="btn" name="decision" value="allow" data-testid="oauth-allow">Allow</button>
<button class="btn2" name="decision" value="deny" data-testid="oauth-deny">Cancel</button></div>
</form>"""
        return HTMLResponse(page(f"Connect {c['client_name']}" + ("" if known else " (unverified)"), body))

    @app.post("/oauth/authorize", include_in_schema=False)
    async def authorize_post(request: Request):
        f = await _form(request)
        sess = session_of(request)
        if not sess or not sess.get("user_id") or not secrets.compare_digest(f.get("csrf", ""), sess["csrf"]):
            return HTMLResponse(page("Can't connect", "<p>Your sign-in expired. Start again from your assistant.</p>"),
                                status_code=403)
        f["response_type"] = "code"
        c, problem = check(f)
        if not c:
            return HTMLResponse(page("Can't connect", f"<p>{html.escape(problem)}</p>"), status_code=400)
        sep = "&" if "?" in f["redirect_uri"] else "?"
        state = {"state": f["state"]} if f.get("state") else {}
        if f.get("decision") != "allow":
            return RedirectResponse(f["redirect_uri"] + sep + urlencode({"error": "access_denied", **state}), 302)
        policy = f.get("policy") if f.get("policy") in POLICIES else "review"
        if policy == "trusted" and not verified_host(redirect_host(f["redirect_uri"])):
            policy = "review"  # never trusted for an unverified client, whatever the form says (SECURITY_RULES 5)
        try:
            cap = max(1, min(int(f.get("daily_cap") or 50), 500))
        except ValueError:
            cap = 50
        code = secrets.token_urlsafe(32)
        store.x("INSERT INTO oauth_codes(code_hash,client_id,user_id,redirect_uri,challenge,policy,daily_cap,expires,used) "
                "VALUES(?,?,?,?,?,?,?,?,0)", (sha(code), c["client_id"], sess["user_id"], f["redirect_uri"],
                                               f["code_challenge"], policy, cap, time.time() + CODE_TTL))
        store.audit(sess["user_id"], "you", "connect_assistant", "safety", {"app": label(c, f["redirect_uri"]), "policy": policy,
                                                                            "redirect_host": redirect_host(f["redirect_uri"])})
        return RedirectResponse(f["redirect_uri"] + sep + urlencode({"code": code, **state}), 302)

    # ---------------------------------------------------------------- token
    @app.post("/oauth/token", include_in_schema=False)
    async def token(request: Request):
        f = await _form(request)
        grant = f.get("grant_type")
        c = client(f.get("client_id", ""))
        if not c:
            return _err("invalid_client", "Unknown client_id.", 401)
        if grant == "authorization_code":
            rows = store.q("SELECT * FROM oauth_codes WHERE code_hash=?", (sha(f.get("code", "")),))
            row = dict(rows[0]) if rows else None
            if not row or row["used"] or row["expires"] < time.time() or row["client_id"] != c["client_id"]:
                if row:
                    store.audit(row["user_id"], "system", "signin_failed", "auth", {"via": "mcp oauth code", "client": c["client_id"]}, ok=False)
                return _err("invalid_grant", "Code is unknown, used or expired.")
            store.x("UPDATE oauth_codes SET used=1 WHERE code_hash=?", (row["code_hash"],))
            if store.blocked(row["user_id"]):  # suspended by an admin (studio/admin.py): no new tokens, same neutral answer
                return _err("invalid_grant", "Code is unknown, used or expired.")
            if f.get("redirect_uri") and f["redirect_uri"] != row["redirect_uri"]:
                return _err("invalid_grant", "redirect_uri doesn't match.")
            digest = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=")
            if not secrets.compare_digest(digest.decode(), row["challenge"]):
                return _err("invalid_grant", "PKCE check failed.")
            return JSONResponse(mint(row["user_id"], c, row["policy"], row["daily_cap"], row["redirect_uri"]),
                                headers={"Cache-Control": "no-store"})
        if grant == "refresh_token":
            rows = store.q("SELECT * FROM oauth_refresh WHERE token_hash=?", (sha(f.get("refresh_token", "")),))
            row = dict(rows[0]) if rows else None
            if not row or row["used"] or row["expires"] < time.time() or row["client_id"] != c["client_id"]:
                if row:
                    store.audit(row["user_id"], "system", "signin_failed", "auth", {"via": "mcp oauth refresh", "client": c["client_id"]}, ok=False)
                return _err("invalid_grant", "Refresh token is unknown, used or expired.")
            old = store.q("SELECT revoked FROM agent_tokens WHERE id=?", (row["agent_id"],))
            if not old or old[0]["revoked"] or store.blocked(row["user_id"]):
                return _err("invalid_grant", "Access was revoked in HuhQueue. Connect again.")
            # conditional claims: two refreshes, or a refresh and a revoke, can't both win (store.xn counts changed rows)
            if not store.xn("UPDATE oauth_refresh SET used=1 WHERE token_hash=? AND used=0", (row["token_hash"],)):
                return _err("invalid_grant", "Refresh token is unknown, used or expired.")
            if not store.xn("UPDATE agent_tokens SET revoked=1 WHERE id=? AND revoked=0", (row["agent_id"],)):
                return _err("invalid_grant", "Access was revoked in HuhQueue. Connect again.")
            # the successor keeps the caps in force on the parent now (an operator's daily / hourly limit), not the
            # ones granted at connect time; read after the claim, synced again after the mint (admin.limit_token also
            # writes every successor), so a limit saved while this runs is never lost (CodeRabbit, PR #128)
            pc = store.q("SELECT daily_cap FROM agent_tokens WHERE id=?", (row["agent_id"],))
            daily = (pc[0]["daily_cap"] if pc else None) or row["daily_cap"]
            out = mint(row["user_id"], c, row["policy"], daily, refreshed=True, parent=row["agent_id"])
            child = store.q("SELECT agent_id FROM oauth_refresh WHERE token_hash=?", (sha(out["refresh_token"]),))[0]["agent_id"]
            inherit_caps(row["agent_id"], child)
            # an admin revoke that landed while this refresh ran marks the parent 2 (studio/admin.py revoke_token): the
            # successor it may not have seen goes too
            if (store.q("SELECT revoked FROM agent_tokens WHERE id=?", (row["agent_id"],)) or [{"revoked": 0}])[0]["revoked"] == 2:
                for k in store.q("SELECT id FROM agent_tokens WHERE parent_id=?", (row["agent_id"],)):
                    store.x("UPDATE agent_tokens SET revoked=2 WHERE id=?", (k["id"],))
                    store.x("UPDATE oauth_refresh SET used=1 WHERE agent_id=?", (k["id"],))
                return _err("invalid_grant", "Access was revoked in HuhQueue. Connect again.")
            return JSONResponse(out, headers={"Cache-Control": "no-store"})
        return _err("unsupported_grant_type", "Use authorization_code or refresh_token.")


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — HuhQueue</title><link rel="stylesheet" href="/static/style.css">
<style>fieldset{{border:0;padding:0;margin:0;gap:12px}}legend{{font-weight:600;margin-bottom:8px}}
.opt{{display:flex;gap:8px;align-items:flex-start}}.opt input{{margin-top:4px}}
.badge{{display:inline-block;font-size:12px;font-weight:600;padding:2px 8px;border-radius:99px;background:#e6f4ef;color:#0b5d4b;vertical-align:middle}}
.badge.warn{{background:#fdecea;color:#9b1c1c}}p.warn{{background:#fdecea;color:#7a1313;padding:10px 12px;border-radius:8px}}</style></head>
<body><header class="top"><a class="brand" href="/" style="text-decoration:none;color:inherit"><span class="mark">hq</span>
<span class="name">HuhQueue</span><span class="built">built on the Buffer API</span></a></header>
<main style="max-width:560px" data-testid="oauth-page"><div class="panel stack">
<h1 style="font-size:28px;font-weight:300">{html.escape(title)}</h1>{body}</div></main></body></html>"""
