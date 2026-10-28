"""Routes for the agent safety layer: kill switch status / pause / resume and the filtered, exportable audit log.

Token limits (daily caps, channel scope, expiry, auto-confirm) are edited through /api/tokens in app.py; the checks
themselves run in the guard middleware (`_token_request`). Every route here is also a registry action, so the UI,
REST, ⌘K and MCP stay in step (tests/test_headless.py).
"""
from __future__ import annotations

from fastapi import Body, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from .. import actions as reg
from .. import safety
from ..models import AuditLog

S = {"type": "string"}
I = {"type": "integer"}
AUDIT_FILTERS = {"limit": I, "actor": S, "token_id": S, "status": {"type": "string", "enum": ["ok", "error", "denied"]},
                 "risk": S, "method": S, "q": S, "since": S, "until": S}

for _a in (
    reg.Action("agents.status", "Agent kill switch status", "GET", "/api/agents/safety", ui="Settings › API & MCP agents / header",
               summary="Whether agents are paused (kill switch), who paused them and why, plus today's usage per token"),
    reg.Action("agents.pause", "Pause all agents (kill switch)", "POST", "/api/agents/pause", {"reason": S}, (), "write", "write",
               ui="Settings › API & MCP agents / header", summary="Every API-token call gets HTTP 423 and the worker holds posts "
               "agents created, until a person resumes. An agent may pause itself; only a person can resume."),
    reg.Action("agents.resume", "Resume agents", "POST", "/api/agents/resume", scope="admin", risk="human",
               ui="Settings › API & MCP agents / header"),
    reg.Action("audit.export", "Export the audit log (CSV)", "GET", "/api/audit/export", AUDIT_FILTERS, scope="admin",
               ui="Settings › API & MCP agents › Audit log", summary="Same filters as audit.list; returns CSV text"),
):
    reg.register(_a)
# audit.list keeps its id; it now takes the same filters
reg.BY_ID["audit.list"].params = AUDIT_FILTERS


def _who(request: Request) -> str:
    tok = getattr(request.state, "token", None)
    if tok:
        return f"agent:{tok.name}"
    u = getattr(request.state, "user", None)
    return getattr(u, "name", "") or "owner"


def add_safety_routes(app: FastAPI, core, audit) -> None:
    from ..services.posts import iso

    @app.get("/api/agents/safety", tags=["agents"])
    def agents_safety(request: Request):
        st = safety.state(core)
        tok = getattr(request.state, "token", None)
        out = {**st, "day_starts_at": iso(safety.day_start_utc(core))}
        if tok:
            out["token"] = {**tok.to_dict(), "used_today": safety.used_today(core, tok.id)}
        else:
            from ..models import ApiToken
            with core.db.session() as s:
                out["tokens"] = [{"id": t.id, "name": t.name, "used_today": safety.used_today(core, t.id),
                                  "max_public_per_day": t.max_public_per_day or 0, "max_actions_per_day": t.max_actions_per_day or 0}
                                 for t in s.scalars(select(ApiToken))]
        return out

    @app.post("/api/agents/pause", tags=["agents"])
    def agents_pause(request: Request, payload: dict = Body(default={})):
        who = _who(request)
        st = safety.set_paused(core, True, who, str((payload or {}).get("reason") or ""))
        audit(who, getattr(getattr(request.state, "token", None), "id", None), "", "POST", "/api/agents/pause", 200,
              f"kill switch ON: {st['reason']}", action="agents.pause", risk="write", result="paused")
        return st

    @app.post("/api/agents/resume", tags=["agents"])
    def agents_resume(request: Request):
        if getattr(request.state, "via", None) == "token":
            return JSONResponse({"detail": {"message": "Only a person can resume agents (Settings › API & MCP agents)"}}, status_code=403)
        who = _who(request)
        st = safety.set_paused(core, False, who)
        audit(who, None, "", "POST", "/api/agents/resume", 200, "kill switch OFF", action="agents.resume", risk="human", result="resumed")
        return st

    def _rows(params: dict, cap: int) -> list[dict]:
        limit = max(1, min(int(params.get("limit") or 200), cap))
        with core.db.session() as s:
            return [safety.audit_row(a, iso) for a in s.scalars(safety.audit_query(params).limit(limit))]

    @app.get("/api/audit", tags=["agents"])
    def audit_list(request: Request):
        return _rows(dict(request.query_params), 1000)

    @app.get("/api/audit/export", tags=["agents"])
    def audit_export(request: Request):
        params = dict(request.query_params)
        body = safety.audit_csv(_rows({**params, "limit": params.get("limit") or 10000}, 10000))
        return Response(body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="huhqueue-audit.csv"'})

    @app.get("/api/audit/facets", tags=["agents"])
    def audit_facets():
        with core.db.session() as s:
            actors = sorted({a for a in s.scalars(select(AuditLog.actor).distinct()) if a})
            risks = sorted({r for r in s.scalars(select(AuditLog.risk).distinct()) if r})
        return {"actors": actors, "risks": risks}


reg.register(reg.Action("audit.facets", "Audit log filter values (actors, risks)", "GET", "/api/audit/facets", scope="admin",
                        ui="Settings › API & MCP agents › Audit log"))
