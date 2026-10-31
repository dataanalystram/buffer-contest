"""REST routes and MCP tools for the Brand studio (templates, example brands, recurring slots) and the Sources agent.

UI = REST = MCP: every button on the Brand studio and Sources screens is one of these routes, and every route has the
MCP tool of the same job (TOOLS below, handled by call_tool). Registered from app.create_app with one call.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from . import brand_examples, buffer_api, demo_guard
from .brand_studio import BrandStudio, StudioError, start_ticker
from .sources_agent import SourceError, SourcesAgent

EXAMPLES_DIR = Path(__file__).parent / "static" / "examples"


def _err(e: Exception, status: int = 400):
    from .actions import ActionError
    raise ActionError(str(e), status)


def counted(fn, a, *args):
    """Run one action and report what it cost in Buffer API requests (real GraphQL requests on a connected account; the
    sample's in-memory Buffer makes none)."""
    with buffer_api.counting() as c:
        out = fn(a, *args)
    if isinstance(out, dict):
        out = {**out, "buffer_cost": {"requests": c.n, "live": not demo_guard.is_demo(a.uid)}}
    return out


def attach(house) -> tuple[BrandStudio, SourcesAgent]:
    if not getattr(house, "brand_studio", None):
        house.brand_studio = BrandStudio(house)
        house.sources = SourcesAgent(house)
    return house.brand_studio, house.sources


def add_routes(app: FastAPI, house, actor) -> None:
    bs, sa = attach(house)
    start_ticker(bs)

    def call(fn, *args):
        try:
            return counted(fn, *args)
        except (StudioError, SourceError) as e:
            _err(e)

    # ---------------------------------------------------------------- example renders (committed, read-only files)
    @app.get("/static/examples/{brand}/{name}", include_in_schema=False)
    def example_file(brand: str, name: str):
        if brand not in brand_examples.EXAMPLES:
            return JSONResponse({"error": "not found"}, status_code=404)
        p = (EXAMPLES_DIR / brand / name).resolve()
        if p.parent != (EXAMPLES_DIR / brand).resolve() or not p.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, headers={"Cache-Control": "public, max-age=3600"})

    # ---------------------------------------------------------------- brand studio
    @app.get("/api/brand-studio")
    def bs_get(request: Request):
        a = actor(request)
        return {"kit": bs.kit(a), **bs.list_templates(), **bs.examples(a), "slots": bs.slots(a)}

    @app.post("/api/brand-studio")
    def bs_save(request: Request, body: dict = Body(...)):
        return call(bs.save, actor(request, write=True), body)

    @app.post("/api/brand-studio/preview")
    def bs_preview(request: Request, body: dict = Body(default={})):
        return call(bs.preview, actor(request), body)

    @app.get("/api/brand-studio/examples")
    def bs_examples(request: Request):
        return bs.examples(actor(request))

    @app.post("/api/brand-studio/examples/{example_id}")
    def bs_apply(example_id: str, request: Request):
        return call(bs.apply_example, actor(request, write=True), example_id)

    @app.get("/api/templates")
    def tpl_list(request: Request):
        actor(request)
        return bs.list_templates()

    @app.get("/api/templates/recap")
    def tpl_recap(request: Request, template: str = "weekly_recap"):
        return call(bs.recap_data, actor(request), template)

    @app.post("/api/templates/render")
    def tpl_render(request: Request, body: dict = Body(...)):
        return call(bs.render, actor(request, write=True), body)

    @app.get("/api/templates/slots")
    def slot_list(request: Request):
        return bs.slots(actor(request))

    @app.post("/api/templates/slots")
    def slot_add(request: Request, body: dict = Body(...)):
        return call(bs.add_slot, actor(request, write=True), body)

    @app.delete("/api/templates/slots/{sid}")
    def slot_del(sid: int, request: Request):
        return call(bs.delete_slot, actor(request, write=True), sid)

    @app.post("/api/templates/slots/{sid}/run")
    def slot_run(sid: int, request: Request):
        return call(bs.run_slot, actor(request, write=True), sid)

    # ---------------------------------------------------------------- sources agent
    @app.get("/api/sources")
    def src_get(request: Request):
        a = actor(request)
        return {"settings": sa.settings(a), "latest": sa.latest(a)}

    @app.post("/api/sources/settings")
    def src_settings(request: Request, body: dict = Body(...)):
        return call(sa.save_settings, actor(request, write=True), body)

    @app.post("/api/sources/scan")
    def src_scan(request: Request, body: dict = Body(default={})):
        return call(sa.scan, actor(request, write=True), body)

    @app.get("/api/sources/runs/{rid}")
    def src_run(rid: int, request: Request):
        return call(sa.get_run, actor(request), rid)

    @app.post("/api/sources/draft")
    def src_draft(request: Request, body: dict = Body(...)):
        return call(sa.draft, actor(request, write=True), body)


# ==================================================================================================== MCP
TOOLS: list[dict[str, Any]] = [
    {"name": "sources_scan", "description": "The Sources agent: read the user's sources (YC launches, Hacker News front page, RSS/Atom lab blogs, Product Hunt with the user's free developer token, and X only with the user's own X API key), dedupe, cluster the same story across sources, and rank each cluster with a lane (postable, idea_bank, low, hidden) and what an agent may do (agent_ok, verify_first, person_only). Returns a run_id and clusters. The sample workspace reads recorded fixtures only. Nothing is posted.",
     "inputSchema": {"type": "object", "properties": {"sources": {"type": "array", "items": {"type": "string", "enum": ["yc", "hn", "rss", "ph", "x"]}}}}},
    {"name": "sources_draft", "description": "Turn clusters from a sources_scan run into one package per channel in the brand: the caption written for the channel (length, hashtag count, format), a breaking-news cover rendered in the brand kit at the channel's frame. dry_run (default true) returns the packages; dry_run false sends each as a Buffer DRAFT through preview/submit (a 'review' token waits in Approvals). Never publishes.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"},
         "cluster_ids": {"type": "array", "items": {"type": "string"}}, "channel_ids": {"type": "array", "items": {"type": "string"}},
         "dry_run": {"type": "boolean", "default": True}}}},
    {"name": "sources_settings", "description": "Read (no arguments) or change which sources the agent reads: on {yc, hn, rss, ph, x}, rss (feed URLs, up to 8), x_query, hn_query. The X key and the Product Hunt token can only be added by a person in the app.",
     "inputSchema": {"type": "object", "properties": {"on": {"type": "object"}, "rss": {"type": "array", "items": {"type": "string"}},
         "x_query": {"type": "string"}, "hn_query": {"type": "string"}}}},
    {"name": "templates_slot_run", "description": "Fire a recurring template slot now: render it from this week's posts and put one Buffer DRAFT per channel at the slot's next time. Never publishes.",
     "inputSchema": {"type": "object", "required": ["slot_id"], "properties": {"slot_id": {"type": "integer"}}}},
    {"name": "list_templates", "description": "The brand's post templates (weekly recap, mid-week recap, breaking cover, launch carousel, quote/CEO clip, free-way-to tutorial), the channel frames and slide limits, and the front/end card styles.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "render_template", "description": "Render a template in the user's brand for each channel (front card first, end card last). data gives the content (recaps without data are built from the week's sent and scheduled posts). video: true adds a 9:16 Reel where FFmpeg runs. Returns media URLs; pass them to preview_post.",
     "inputSchema": {"type": "object", "required": ["template"], "properties": {"template": {"type": "string"},
         "services": {"type": "array", "items": {"type": "string"}}, "data": {"type": "object"}, "video": {"type": "boolean"}}}},
    {"name": "list_brand_examples", "description": "The example brands in the sample workspace (HuhByte AI; Buffer, a concept example not affiliated with Buffer) with their palettes, fonts, channels and pre-rendered example gallery.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "apply_brand_example", "description": "Switch the workspace's brand kit to an example brand (huhbyte or buffer): logo, colours, fonts, front and end cards, hashtags, channels.",
     "inputSchema": {"type": "object", "required": ["example_id"], "properties": {"example_id": {"type": "string", "enum": list(brand_examples.EXAMPLES)}}}},
    {"name": "schedule_template_slot", "description": "Add a recurring Planner slot for a template (e.g. weekly_recap every mon 20:00 Europe/Dublin). When it fires it renders the template from that week's posts and puts a Buffer DRAFT on each channel at the slot time. Without template: lists the slots.",
     "inputSchema": {"type": "object", "properties": {"template": {"type": "string"}, "weekday": {"type": "string"}, "time": {"type": "string"},
         "tz": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}}}}},
]
READ_ONLY = {"list_templates", "list_brand_examples"}


def call_tool(studio, actor, name: str, args: dict):
    """Returns NotImplemented when the tool isn't one of ours, so mcp.call_tool can raise its own Unknown tool."""
    names = {t["name"] for t in TOOLS}
    if name not in names:
        return NotImplemented
    bs, sa = attach(studio.house)
    try:
        return counted(lambda a, nm, ag: _tool(bs, sa, a, nm, ag), actor, name, args)
    except (StudioError, SourceError) as e:
        _err(e)


def _tool(bs, sa, actor, name: str, args: dict):
    if True:
        if name == "sources_scan":
            return sa.scan(actor, {"sources": args.get("sources")} if args.get("sources") else {})
        if name == "sources_draft":
            return sa.draft(actor, args)
        if name == "sources_settings":
            body = {k: args[k] for k in ("on", "rss", "x_query", "hn_query") if k in args}
            return sa.save_settings(actor, body) if body else sa.settings(actor)
        if name == "list_templates":
            return bs.list_templates()
        if name == "templates_slot_run":
            return bs.run_slot(actor, int(args.get("slot_id") or 0))
        if name == "render_template":
            return bs.render(actor, args)
        if name == "list_brand_examples":
            return bs.examples(actor)
        if name == "apply_brand_example":
            return bs.apply_example(actor, args.get("example_id", ""))
        if name == "schedule_template_slot":
            return bs.add_slot(actor, args) if args.get("template") else {"slots": bs.slots(actor)}
