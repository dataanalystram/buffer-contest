"""Reports: period comparison JSON, branded PDF, weekly schedule (email and/or archive) and the report archive."""
from __future__ import annotations

import anyio
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from .. import actions as reg
from ..actions import I, S, SA, Action
from ..scope_filter import narrow, visible
from ..services import analytics, reports


def require_admin(request: Request) -> None:
    """Admin-only routes whose path the middleware would otherwise treat as read/write."""
    tok = getattr(request.state, "token", None)
    if tok is not None and not tok.can("admin"):
        raise HTTPException(403, f"Token '{tok.name}' lacks the 'admin' scope")
    rs = getattr(request.state, "role_scopes", None)       # people with roles (teams), when that feature is present
    if tok is None and rs is not None and "admin" not in rs:
        raise HTTPException(403, "Only an admin can change this")


def add_report_routes(app: FastAPI, core) -> None:
    def build(days, start, end, channel_id, tag, campaign, format, sections):
        try:
            return reports.report_data(core, days, start, end, channel_id, tag, campaign, format, sections)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/reports/summary", tags=["reports"])
    def report_summary(request: Request, days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None,
                       tag: str | None = None, campaign: str | None = None, format: str | None = None, sections: str | None = None):
        """KPIs vs the previous period of the same length, daily series for both periods, per-channel breakdown, top posts,
        audience growth, Reels, post types, best times, hashtags and campaigns."""
        return build(days, start, end, narrow(request, channel_id), tag, campaign, format, sections)

    @app.get("/api/reports/report.pdf", tags=["reports"])
    async def report_pdf(request: Request, days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None,
                         tag: str | None = None, campaign: str | None = None, format: str | None = None, sections: str | None = None,
                         download: bool = True):
        data = await anyio.to_thread.run_sync(lambda: build(days, start, end, narrow(request, channel_id), tag, campaign, format, sections))
        pdf = await anyio.to_thread.run_sync(lambda: reports.render_pdf(data))
        disp = "attachment" if download else "inline"
        return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'{disp}; filename="{reports.filename(data)}"'})

    @app.get("/api/reports/report.csv", tags=["reports"])
    def report_csv(request: Request, days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None,
                   tag: str | None = None, campaign: str | None = None, format: str | None = None, sections: str | None = None):
        """The custom report as CSV: one block per chosen section."""
        data = build(days, start, end, narrow(request, channel_id), tag, campaign, format, sections)
        name = reports.filename(data).replace(".pdf", ".csv")
        return Response(analytics.to_csv(data, data["sections"]), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/reports/audience", tags=["reports"])
    def report_audience(request: Request, days: str | None = "30", start: str | None = None, end: str | None = None, channel_id: str | None = None):
        """Followers over time (daily snapshots) and net growth vs the previous period."""
        d = build(days, start, end, narrow(request, channel_id), None, None, None, None)
        return {"range": d["range"], "previous_range": d["previous_range"], **d["audience"]}

    @app.post("/api/reports/audience/snapshot", tags=["reports"])
    def report_audience_snapshot(payload: dict = Body(default={})):
        """Record today's follower totals now (the worker also does this once a day)."""
        return {"written": analytics.snapshot_followers(core, refresh=payload.get("refresh", True) is not False)}

    @app.post("/api/insights/best-times/apply", tags=["insights"])
    def best_times_apply(request: Request, payload: dict = Body(...)):
        """Use the best weekday x hour cells from this channel's own results as its posting slots."""
        require_admin(request)
        cid = payload.get("channel_id")
        if not cid:
            raise HTTPException(400, "channel_id is required")
        try:
            return analytics.apply_best_slots(core, cid, int(payload.get("count") or 5), int(payload.get("days") or 180),
                                              payload.get("replace", True) is not False)
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/reports/schedule", tags=["reports"])
    def report_schedule_get():
        return reports.get_schedule(core)

    @app.put("/api/reports/schedule", tags=["reports"])
    def report_schedule_put(request: Request, payload: dict = Body(...)):
        require_admin(request)
        try:
            return reports.set_schedule(core, payload)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/reports/send", tags=["reports"])
    async def report_send(request: Request, payload: dict = Body(default={})):
        """Generate a PDF now, keep it in the archive and email it to `email_to` (when SMTP is set up)."""
        to = payload.get("email_to") or []
        if isinstance(to, str):
            to = [x.strip() for x in to.replace(";", ",").split(",") if x.strip()]
        bad = [x for x in to if not reports.EMAIL.match(x)]
        if bad:
            raise HTTPException(400, f"Not an email address: {', '.join(bad)}")
        try:
            return await anyio.to_thread.run_sync(lambda: reports.generate(
                core, str(payload.get("days") or "7"), payload.get("start"), payload.get("end"), narrow(request, payload.get("channel_id")), to,
                tag=payload.get("tag"), campaign=payload.get("campaign"), fmt=payload.get("format"), sections=payload.get("sections")))
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/api/reports/archive", tags=["reports"])
    def report_archive(request: Request):
        if visible(request) is not None:   # archived reports cover every channel: none for a channel-limited caller (4 Nov 2026)
            return {"reports": []}
        return {"reports": reports.list_archive(core)}

    @app.get("/api/reports/archive/{name}", tags=["reports"])
    def report_archive_file(name: str, request: Request):
        p = reports.archive_path(core, name) if visible(request) is None else None
        if not p:
            raise HTTPException(404, "Report not found")
        return FileResponse(p, media_type="application/pdf", filename=name)

    @app.delete("/api/reports/archive/{name}", tags=["reports"])
    def report_archive_delete(name: str, request: Request):
        p = reports.archive_path(core, name) if visible(request) is None else None
        if not p:
            raise HTTPException(404, "Report not found")
        p.unlink()
        return {"ok": True}


RANGE = {"days": {**S, "description": "7, 30, 90, mtd …"}, "start": {**S, "description": "ISO date"}, "end": S,
         "channel_id": {**S, "description": "comma-separated channel ids"}}
FILTERS = {"tag": {**S, "description": "comma-separated post tags"}, "campaign": {**S, "description": "campaign name(s)"},
           "format": {**S, "description": "post, carousel, reel, story, short, video"},
           "sections": {**S, "description": "comma list of: " + ", ".join(analytics.SECTIONS)}}

for _a in (
    Action("report.summary", "Report data: KPIs vs previous period, audience growth, Reels, best times, hashtags, campaigns",
           "GET", "/api/reports/summary", {**RANGE, **FILTERS},
           examples=("compare this week with last week", "how did instagram do vs the previous 30 days?"),
           group="report", ui="Insights › Report"),
    Action("report.pdf", "Download the branded PDF report", "GET", "/api/reports/report.pdf", {**RANGE, **FILTERS, "download": {"type": "boolean"}},
           mcp=False, palette=False, group="report", ui="Insights › Report › Download PDF (data-testid report-download-pdf)"),
    Action("report.send", "Generate the PDF report now (archive + optional email)", "POST", "/api/reports/send",
           {**RANGE, **FILTERS, "email_to": {**SA, "description": "email addresses (needs SMTP in .env)"}}, (), "write", "write",
           examples=("make a pdf report for the last 7 days",), group="report", ui="Insights › Report › Generate now"),
    Action("report.schedule_get", "Read the weekly report schedule", "GET", "/api/reports/schedule", group="report",
           ui="Insights › Report › Weekly email"),
    Action("report.schedule_set", "Set the weekly report schedule (day, time, recipients)", "PUT", "/api/reports/schedule",
           {"enabled": {"type": "boolean"}, "weekday": {**I, "description": "0=Mon … 6=Sun"}, "time": {**S, "description": "HH:MM"},
            "days": {**S, "description": "7, 14, 28, 30 or mtd"}, "channel_id": S, "timezone": S, "email_to": SA,
            "tag": S, "campaign": S, "format": S, "sections": SA},
           (), "admin", "write", examples=("email me the report every monday at 9",), group="report", ui="Insights › Report › Weekly email"),
    Action("report.archive", "List generated PDF reports", "GET", "/api/reports/archive", group="report", ui="Insights › Report › Archive"),
    Action("report.csv", "Download the custom report as CSV", "GET", "/api/reports/report.csv", {**RANGE, **FILTERS},
           mcp=False, palette=False, group="report", ui="Insights › Report › Download CSV (data-testid report-download-csv)"),
    Action("report.audience", "Followers over time and net growth vs the previous period", "GET", "/api/reports/audience", RANGE,
           examples=("how many followers did I gain this month?",), group="report", ui="Insights › Audience"),
    Action("report.audience_snapshot", "Record today's follower totals now", "POST", "/api/reports/audience/snapshot",
           {"refresh": {"type": "boolean"}}, (), "write", "write", group="report", ui="Insights › Audience › Refresh"),
    Action("channel.best_times_apply", "Use your best times (own data) as the channel's posting slots", "POST",
           "/api/insights/best-times/apply", {"channel_id": S, "count": {**I, "description": "slots per week (1-21)"}, "days": I,
                                              "replace": {"type": "boolean"}}, ("channel_id",), "admin", "write",
           examples=("use my best times as posting slots",), group="report", ui="Insights › Best times › Use as posting slots"),
    Action("report.archive_download", "Download an archived PDF report", "GET", "/api/reports/archive/{name}", {"name": S}, ("name",),
           mcp=False, palette=False, group="report", ui="Insights › Report › Archive"),
    Action("report.archive_delete", "Delete an archived PDF report", "DELETE", "/api/reports/archive/{name}", {"name": S}, ("name",),
           "write", "destructive", group="report", ui="Insights › Report › Archive"),
    Action("nav.reports", "Go to Reports (period comparison, PDF, weekly email)", nav="#/insights?view=report", mcp=False, ui="⌘K"),
):
    reg.register(_a)
