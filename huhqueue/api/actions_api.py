"""Generated surfaces of the action registry: catalog, generic runner, OpenAPI extensions, command bar, AI settings."""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time

import anyio
import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import actions as reg
from ..models import KV
from ..security import SESSION_COOKIE


def _caller_scopes(request: Request) -> list[str] | None:
    tok = getattr(request.state, "token", None)
    if tok:
        return list(tok.scopes)
    rs = getattr(request.state, "role_scopes", None)
    return list(rs) if rs is not None else None


async def dispatch(app: FastAPI, request: Request, action: reg.Action, inp: dict) -> tuple[int, object]:
    """Run one action through its REST route, in-process, with the caller's own credentials.

    The inner request goes through the same guard middleware (auth, scopes, tool allow-list, rate limit, audit),
    so a registry run can never do more than the caller could do by calling the route directly.
    """
    try:
        method, path, query, body = action.request(inp)
    except ValueError as e:
        return 400, {"detail": {"message": str(e)}}
    headers = {"x-huhq-tool": request.headers.get("x-huhq-tool") or action.tool, "x-huhq-action": action.id}
    for h in ("authorization", "x-csrf-token", "cookie", "x-huhq-confirm"):
        if request.headers.get(h):
            headers[h] = request.headers[h]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal") as c:
        r = await c.request(method, path, params=query, json=body if method not in ("GET", "DELETE") else None, headers=headers)
    data = r.json() if "json" in r.headers.get("content-type", "") else r.text
    return r.status_code, data


def describe(action: reg.Action, inp: dict) -> str:
    """One readable sentence for the confirm step."""
    bits = [action.title]
    for k in ("caption", "text", "when", "scheduled_at", "timezone", "channel_ids", "media_ids", "ids", "action", "id", "tag", "status"):
        v = inp.get(k)
        if v not in (None, "", []):
            if isinstance(v, list):
                v = ", ".join(map(str, v[:6])) + (f" +{len(v) - 6}" if len(v) > 6 else "")
            bits.append(f"{k.replace('_', ' ')}: {str(v)[:120]}")
    if inp.get("options"):
        bits.append("options: " + ", ".join(f"{k}={v}" for k, v in inp["options"].items()))
    return " · ".join(bits)


def _is_pending(data) -> bool:
    if isinstance(data, dict):
        if data.get("status") == "pending_approval":
            return True
        ts = data.get("targets")
        if isinstance(ts, list) and ts and all(t.get("status") == "pending_approval" for t in ts):
            return True
    return False


def add_action_routes(app: FastAPI, core) -> None:
    pending: dict[str, dict] = {}   # confirm_token -> {action, input, exp, who}

    def _who(request: Request) -> str:
        tok = getattr(request.state, "token", None)
        if tok:
            return f"token:{tok.id or tok.name}"
        return "session:" + hashlib.sha256((request.cookies.get(SESSION_COOKIE) or "").encode()).hexdigest()[:16]

    def _new_confirm(request: Request, action: reg.Action, inp: dict) -> str:
        now = time.time()
        for k in [k for k, v in pending.items() if v["exp"] < now]:
            pending.pop(k, None)
        token = "cf_" + secrets.token_urlsafe(18)
        pending[token] = {"action": action.id, "input": inp, "exp": now + 600, "who": _who(request)}
        return token

    def _take_confirm(request: Request, token: str) -> dict:
        p = pending.pop(token or "", None)
        if not p or p["exp"] < time.time() or p["who"] != _who(request):
            raise HTTPException(400, "This confirmation expired or is not yours - run the command again")
        return p

    async def preview(request: Request, action: reg.Action, inp: dict) -> dict:
        out = {"action": action.id, "title": action.title, "risk": action.risk, "scope": action.scope, "input": inp,
               "summary": describe(action, inp)}
        if action.id == "post.schedule":
            st, val = await dispatch(app, request, reg.get("post.validate"), {k: v for k, v in inp.items() if k in reg.POST_BODY})
            out["issues"] = val.get("issues") if st == 200 and isinstance(val, dict) else {}
        if action.id == "post.bulk":
            out["count"] = len(inp.get("ids") or [])
        return out

    async def run_action(request: Request, action: reg.Action, inp: dict, *, dry_run=False, confirm_token="",
                         require_confirm=False, idempotency_key="") -> JSONResponse:
        if not action.method:
            return JSONResponse({"status": "client", "nav": action.nav, "action": action.id})
        if action.risk == "secret":
            return JSONResponse({"status": "error", "message": f"'{action.id}' can only be done in the web UI"}, status_code=403)
        if action.risk == "human" and getattr(request.state, "via", None) == "token":
            return JSONResponse({"status": "error", "message": "Approvals are made by a human in the app"}, status_code=403)
        if confirm_token:
            p = _take_confirm(request, confirm_token)
            if p["action"] != action.id:
                raise HTTPException(400, "Confirmation is for a different action")
            inp = p["input"]
        missing = [r for r in action.required if (inp or {}).get(r) in (None, "")]
        if missing:
            return JSONResponse({"status": "error", "message": f"Missing: {', '.join(missing)}"}, status_code=400)
        if not confirm_token and (dry_run or (require_confirm and action.risk in reg.CONFIRM_RISKS)):
            return JSONResponse({"status": "needs_confirmation", "preview": await preview(request, action, inp),
                                 "confirm_token": _new_confirm(request, action, inp)})
        idem_key = f"idem:{action.id}:{idempotency_key}" if idempotency_key else ""
        if idem_key:
            with core.db.session() as s:
                row = s.get(KV, idem_key)
                if row:
                    return JSONResponse({**row.value, "idempotent_replay": True})
        status, data = await dispatch(app, request, action, inp)
        if status == 428 and isinstance(data, dict) and data.get("status") == "needs_confirmation":
            return JSONResponse(data)   # MCP public action: preview + confirm_token (agent safety layer)
        if status >= 400:
            msg = data.get("detail") if isinstance(data, dict) else data
            if isinstance(msg, dict):
                msg = msg.get("message") or json.dumps(msg)
            return JSONResponse({"status": "error", "http_status": status, "message": str(msg)[:2000], "detail": data},
                                status_code=status)
        result = {"status": "pending_approval" if _is_pending(data) else "done", "action": action.id, "output": data}
        if idem_key:
            with core.db.session() as s:
                s.merge(KV(key=idem_key, value=result))
        return JSONResponse(result)

    app.state.run_action = run_action
    app.state.peek_confirm = lambda token: (pending.get(token or "") or {}).get("action")

    @app.get("/api/actions", tags=["actions"])
    def actions_list(request: Request, palette: bool = False):
        agent = getattr(request.state, "via", None) == "token"
        return {"actions": reg.catalog(_caller_scopes(request) if agent else None, palette=palette, agent=agent),
                "risks": reg.RISKS, "confirm_risks": sorted(reg.CONFIRM_RISKS)}

    @app.post("/api/actions/{aid}:run", tags=["actions"])
    async def actions_run(aid: str, request: Request, payload: dict = Body(default={})):
        """Run any action: {input, dry_run?, confirm_token?, idempotency_key?} -> done | needs_confirmation | pending_approval | error."""
        action = reg.get(aid)
        if not action:
            return JSONResponse({"status": "error", "message": f"Unknown action '{aid}'"}, status_code=404)
        return await run_action(request, action, payload.get("input") or {}, dry_run=bool(payload.get("dry_run")),
                                confirm_token=payload.get("confirm_token") or "",
                                idempotency_key=payload.get("idempotency_key") or request.headers.get("idempotency-key", ""))

    base_openapi = app.openapi

    def openapi_with_actions():
        if app.openapi_schema:
            return app.openapi_schema
        schema = base_openapi()
        paths = schema.get("paths", {})
        for a in reg.rest_actions():
            pat = "^" + re.sub(r"\\{\w+\\}", "{[^/]+}", re.escape(a.path)) + "$"
            for p, ops in paths.items():
                op = ops.get(a.method.lower())
                if op is not None and re.match(pat, p):
                    op.setdefault("x-huhq-actions", []).append(a.id)
                    op["x-huhq-action"] = op["x-huhq-actions"][0]
                    op["x-huhq-risk"], op["x-huhq-scope"] = a.risk, a.scope
                    break
        schema["x-huhq-catalog"] = "/api/actions"
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_actions


def add_assistant_routes(app: FastAPI, core) -> None:
    from ..services import assistant

    def _ui_only(request: Request):
        if getattr(request.state, "via", None) != "session":
            raise HTTPException(403, "AI keys can only be managed from the web UI")

    @app.get("/api/ai/settings", tags=["assistant"])
    def ai_settings_get(request: Request):
        _ui_only(request)
        return {**assistant.ai_settings(core), "configured": assistant.configured(core)}

    @app.put("/api/ai/settings", tags=["assistant"])
    def ai_settings_put(request: Request, payload: dict = Body(...)):
        _ui_only(request)
        try:
            out = assistant.set_ai_settings(core, payload)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {**out, "configured": assistant.configured(core)}

    @app.get("/api/ai/status", tags=["assistant"])
    def ai_status():
        """Which AI provider and model are in use (never the key). Safe for agents."""
        return assistant.status(core)

    @app.post("/api/ai/test", tags=["assistant"])
    async def ai_test():
        """Send one tiny prompt to the configured provider and report the answer and latency."""
        return await anyio.to_thread.run_sync(lambda: assistant.test_connection(core))

    @app.get("/api/ai/models", tags=["assistant"])
    async def ai_models():
        """Model ids the configured provider offers (its GET /models)."""
        return await anyio.to_thread.run_sync(lambda: assistant.list_models(core))

    @app.post("/api/ai/write", tags=["assistant"])
    async def ai_write(payload: dict = Body(...)):
        """Caption writer: write / rephrase / shorten / expand / hook / hashtags / fix. Returns options, posts nothing."""
        from ..services import ai_tools
        try:
            return await anyio.to_thread.run_sync(lambda: ai_tools.write(
                core, payload.get("text") or "", payload.get("mode") or "write", payload.get("tone") or "friendly",
                payload.get("providers") or [], payload.get("instructions") or "", payload.get("count") or 3))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except assistant.LLMError as e:
            raise HTTPException(502, f"AI provider error: {e}") from e

    @app.post("/api/assistant/command", tags=["assistant"])
    async def assistant_command(request: Request, payload: dict = Body(...)):
        """Free text -> plan -> run now (safe / write) or needs_confirmation (public / destructive)."""
        text = str(payload.get("text") or "").strip()[:2000]
        if not text:
            raise HTTPException(400, "Type a command")
        p = await anyio.to_thread.run_sync(lambda: assistant.plan(core, text, payload.get("context")))
        base = {"plan": p, "message": p.get("message", ""), "source": p.get("source")}
        if not p.get("action"):
            return {"status": "clarify", **base}
        action = reg.get(p["action"])
        if not action or (action.method and not action.agent_visible()):
            return {"status": "clarify", **base, "message": "That can only be done by hand in the app."}
        if not action.method:
            return {"status": "client", "nav": action.nav, "action": action.id, **base}
        res = await app.state.run_action(request, action, p.get("input") or {}, require_confirm=True, dry_run=bool(payload.get("dry_run")))
        return JSONResponse({**json.loads(res.body), **base}, status_code=res.status_code)

    @app.post("/api/assistant/confirm", tags=["assistant"])
    async def assistant_confirm(request: Request, payload: dict = Body(...)):
        token = payload.get("confirm_token") or ""
        action = reg.get(app.state.peek_confirm(token) or "")
        if not action:
            raise HTTPException(400, "This confirmation expired - run the command again")
        return await app.state.run_action(request, action, {}, confirm_token=token)
