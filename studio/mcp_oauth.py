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

    def mint(uid: str, c: dict, policy: str, cap: int) -> dict:
        tok = studio.create_agent_token(Actor(uid), f"{c['client_name']} (connected)", policy, cap, days=ACCESS_DAYS)
        refresh = "hqr_" + secrets.token_urlsafe(32)
        store.x("INSERT INTO oauth_refresh(token_hash,client_id,user_id,agent_id,policy,daily_cap,expires,used) "
                "VALUES(?,?,?,?,?,?,?,0)", (sha(refresh), c["client_id"], uid, tok["id"], policy, cap,
                                             time.time() + REFRESH_DAYS * 86400))
        return {"access_token": tok["token"], "token_type": "Bearer", "expires_in": ACCESS_DAYS * 86400,
                "refresh_token": refresh, "scope": "huhqueue"}

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
    @app.post("/oauth/register", include_in_schema=False)
    async def register(request: Request):
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
        radios = "".join(
            f'<label class="opt"><input type="radio" name="policy" value="{p}" data-testid="oauth-policy-{p}"'
            f'{" checked" if p == "review" else ""}> {html.escape(opts[p])}</label>' for p in POLICIES if p in opts)
        body = f"""
<p><b>{html.escape(c['client_name'])}</b> wants to use HuhQueue for
<b>{html.escape(u.get('name') or u.get('email') or 'your Buffer account')}</b>
({html.escape(u.get('org_name') or 'Buffer')}).</p>
<form method="post" action="/oauth/authorize" class="stack" data-testid="oauth-consent">
<input type="hidden" name="csrf" value="{html.escape(sess['csrf'])}">{hidden}
<fieldset class="stack"><legend>What it may do</legend>{radios}</fieldset>
<label class="field">Posts per day at most
<input type="number" name="daily_cap" value="10" min="1" max="100" data-testid="oauth-cap" aria-label="Posts per day at most"></label>
<p class="hint">You can revoke it any time under Agents. The kill switch stops it at once.</p>
<div class="row"><button class="btn" name="decision" value="allow" data-testid="oauth-allow">Allow</button>
<button class="btn2" name="decision" value="deny" data-testid="oauth-deny">Cancel</button></div>
</form>"""
        return HTMLResponse(page(f"Connect {c['client_name']}", body))

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
        try:
            cap = max(1, min(int(f.get("daily_cap") or 10), 100))
        except ValueError:
            cap = 10
        code = secrets.token_urlsafe(32)
        store.x("INSERT INTO oauth_codes(code_hash,client_id,user_id,redirect_uri,challenge,policy,daily_cap,expires,used) "
                "VALUES(?,?,?,?,?,?,?,?,0)", (sha(code), c["client_id"], sess["user_id"], f["redirect_uri"],
                                               f["code_challenge"], policy, cap, time.time() + CODE_TTL))
        store.audit(sess["user_id"], "you", "connect_assistant", "safety", {"app": c["client_name"], "policy": policy})
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
                return _err("invalid_grant", "Code is unknown, used or expired.")
            store.x("UPDATE oauth_codes SET used=1 WHERE code_hash=?", (row["code_hash"],))
            if f.get("redirect_uri") and f["redirect_uri"] != row["redirect_uri"]:
                return _err("invalid_grant", "redirect_uri doesn't match.")
            digest = base64.urlsafe_b64encode(hashlib.sha256(f.get("code_verifier", "").encode()).digest()).rstrip(b"=")
            if not secrets.compare_digest(digest.decode(), row["challenge"]):
                return _err("invalid_grant", "PKCE check failed.")
            return JSONResponse(mint(row["user_id"], c, row["policy"], row["daily_cap"]), headers={"Cache-Control": "no-store"})
        if grant == "refresh_token":
            rows = store.q("SELECT * FROM oauth_refresh WHERE token_hash=?", (sha(f.get("refresh_token", "")),))
            row = dict(rows[0]) if rows else None
            if not row or row["used"] or row["expires"] < time.time() or row["client_id"] != c["client_id"]:
                return _err("invalid_grant", "Refresh token is unknown, used or expired.")
            old = store.q("SELECT revoked FROM agent_tokens WHERE id=?", (row["agent_id"],))
            if not old or old[0]["revoked"]:
                return _err("invalid_grant", "Access was revoked in HuhQueue. Connect again.")
            store.x("UPDATE oauth_refresh SET used=1 WHERE token_hash=?", (row["token_hash"],))
            store.x("UPDATE agent_tokens SET revoked=1 WHERE id=?", (row["agent_id"],))
            return JSONResponse(mint(row["user_id"], c, row["policy"], row["daily_cap"]), headers={"Cache-Control": "no-store"})
        return _err("unsupported_grant_type", "Use authorization_code or refresh_token.")


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} — HuhQueue</title><link rel="stylesheet" href="/static/style.css">
<style>fieldset{{border:0;padding:0;margin:0;gap:12px}}legend{{font-weight:600;margin-bottom:8px}}
.opt{{display:flex;gap:8px;align-items:flex-start}}.opt input{{margin-top:4px}}</style></head>
<body><header class="top"><a class="brand" href="/" style="text-decoration:none;color:inherit"><span class="mark">hq</span>
<span class="name">HuhQueue</span><span class="built">built on the Buffer API</span></a></header>
<main style="max-width:560px" data-testid="oauth-page"><div class="panel stack">
<h1 style="font-size:28px;font-weight:300">{html.escape(title)}</h1>{body}</div></main></body></html>"""
