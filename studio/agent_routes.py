"""REST routes and MCP tools for the agent brain, natural-language edits, onboarding and recommendations.
UI = REST = MCP: each route has the MCP tool of the same job (TOOLS), so a person's own ChatGPT/Claude can drive the
same tools step by step (make_post with brain=external, then post_* tools) or let HuhQueue's brain run (make_post).
"""
from __future__ import annotations

from typing import Any

from fastapi import Body, FastAPI, Request

from . import agent_brain, brain_llm, budget
from .actions import ActionError
from .agent_tools import Tools
from .onboarding import Onboarding, OnboardingError


def attach(house) -> Onboarding:
    if not getattr(house, "onboarding", None):
        from . import brand_routes
        brand_routes.attach(house)
        house.onboarding = Onboarding(house)
    return house.onboarding


def edit_brand(house, a, instruction: str) -> dict:
    t = Tools(house, a, 0)
    t.guard("edit_brand")
    out = t.t_edit_brand(instruction or "")
    return {**out, "preview": house.preview_kit(a)}


def brain_status(house, a) -> dict:
    """Which brain runs for this account and how much included Claude budget is left (Activity / AI engine)."""
    from . import demo_guard
    engs = brain_llm.engines(house.store, a.uid, house.s, house.http, demo=demo_guard.is_demo(a.uid))
    first = engs[0] if engs else None
    return {"brain": ({"source": first["source"], "provider": first["provider"], "model": first["model"], "fast_model": first.get("fast_model"),
                       "tool_use": bool(first.get("tools"))} if first else {"source": "built-in writer", "tool_use": False}),
            "fallbacks": [e["source"] for e in engs[1:]] + ["built-in writer"],
            "budget": budget.remaining(house.store, a.uid) if brain_llm.operator_key() else None,
            "operator_key": bool(brain_llm.operator_key())}


def add_routes(app: FastAPI, house, actor) -> None:
    ob = attach(house)

    def call(fn, *args):
        try:
            return fn(*args)
        except OnboardingError as e:
            raise ActionError(str(e))

    @app.post("/api/agent/runs/{rid}/messages")
    def run_message(rid: int, request: Request, body: dict = Body(...)):
        """Reply in a run's thread: "make the hook stronger", "redo YouTube at 30s", "it's $1.3M"."""
        return house.brain.reply(actor(request, write=True), rid, body.get("text", ""))

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/media")
    def run_channel_media(rid: int, channel_id: str, request: Request, body: dict = Body(default={})):
        """One channel's media: {"token": ...} uses the person's own file (Library item / upload); {} regenerates it."""
        return house.brain.channel_media(actor(request, write=True), rid, channel_id, str((body or {}).get("token") or ""))

    @app.post("/api/agent/runs/{rid}/tools/{name}")
    def run_tool(rid: int, name: str, request: Request, body: dict = Body(default={})):
        return house.brain.tool(actor(request, write=True), rid, name, body or {})

    @app.get("/api/themes")
    def themes_list(request: Request):
        """Every video look (the four edit styles + the themes) with its gallery still and loop, for the theme picker."""
        actor(request)
        from .media import styles as media_styles
        return {"themes": media_styles.describe_themes(), "default": "auto"}

    @app.post("/api/agent/runs/{rid}/theme")
    def run_theme(rid: int, request: Request, body: dict = Body(...)):
        """Set a run's video theme ({"theme": "cinematic"}) and re-render its video channels in it."""
        return house.brain.tool(actor(request, write=True), rid, "set_theme", {"theme": str((body or {}).get("theme") or ""),
                                                                              "why": "you picked it"})

    @app.post("/api/brand/edit")
    def brand_edit(request: Request, body: dict = Body(...)):
        return edit_brand(house, actor(request, write=True), body.get("instruction", ""))

    @app.get("/api/agent/brain")
    def brain_get(request: Request):
        return brain_status(house, actor(request))

    @app.get("/api/onboarding")
    def onboarding_get(request: Request):
        return call(ob.get, actor(request))

    @app.put("/api/onboarding")
    def onboarding_put(request: Request, body: dict = Body(...)):
        return call(ob.put, actor(request, write=True), body)

    @app.get("/api/recommendations")
    def recs(request: Request, refresh: int = 0):
        a = actor(request, write=bool(refresh))
        return call(ob.recommendations, a, bool(refresh))


TOOLS: list[dict[str, Any]] = [
    {"name": "make_post", "description": "Make on-brand posts from one prompt (prose + links, up to 4,000 characters). A pasted link is THE source (X posts are read through X's public oEmbed). 'all my channels' = every connected channel. HuhQueue's agent plans, renders (carousel/Reel/cover), writes per-channel captions and checks them; poll get_run(run_id). brain='external' creates the run without running HuhQueue's agent, so YOU drive it with the post_* tools. Nothing is published.",
     "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                                            "format": {"type": "string", "enum": ["auto", "reel", "carousel", "photo"]},
                                                                            "theme": {"type": "string", "description": "A video theme id from list_themes, or auto (the agent picks from the content)"},
                                                                            "brain": {"type": "string", "enum": ["huhqueue", "external"]}}}},
    {"name": "reply_post", "description": "Refine a post run in plain words, like replying in its thread: 'make the hook stronger', 'redo YouTube at 30s', 'LinkedIn more formal', 'logo smaller', 'it's $1.3M' (answers a fact question). Standing instructions become Style Ledger rules.",
     "inputSchema": {"type": "object", "required": ["run_id", "text"], "properties": {"run_id": {"type": "integer"}, "text": {"type": "string"}}}},
    {"name": "replace_channel_media", "description": "Change ONE channel's media in a post run: media_token (a Library item's media_token from list_library / use_library_item, or an upload) uses the person's own file; omit it to regenerate that channel's media. The check runs again.",
     "inputSchema": {"type": "object", "required": ["run_id", "channel"], "properties": {"run_id": {"type": "integer"}, "channel": {"type": "string", "description": "channel id or network name"},
                                                                                       "media_token": {"type": "string"}}}},
    {"name": "edit_brand", "description": "Change the brand kit in plain words: 'make the logo smaller', 'use #FF5A1F', 'switch font to Poppins'. Records a ledger rule; returns a preview.",
     "inputSchema": {"type": "object", "required": ["instruction"], "properties": {"instruction": {"type": "string"}}}},
    {"name": "get_onboarding", "description": "The person's content types, channels, website, tone and X watchlist, with the options to choose from.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_onboarding", "description": "Save onboarding choices: content_types (ai_news, product_launches, yc_startups, tutorials, dev_tools, memes, india_tech, research, funding, open_source), channel_ids, tone, website (builds the brand kit), x_watchlist (handles or post links).",
     "inputSchema": {"type": "object", "properties": {"content_types": {"type": "array", "items": {"type": "string"}}, "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                      "tone": {"type": "string"}, "website": {"type": "string"}, "x_watchlist": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "get_recommendations", "description": "A feed tailored to the person's content types plus up to three ready-to-approve posts in their brand (cover + captions). Cached for an hour; refresh=true rescans.",
     "inputSchema": {"type": "object", "properties": {"refresh": {"type": "boolean"}}}},
    {"name": "list_themes", "description": "Every video look for Make a post: the four edit styles (opus, huhbyte_news, minimal, hype) and the video themes (caption_news, news_package, newsreel, dark_keynote, glass_studio, swiss, kino, silent_film, risograph, hologram_hud, screencast, investigation, countdown, crt, tilt_shift, game_show, cinematic) with what each looks like, what it is best for and its gallery thumbnail.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_theme", "description": "Set a post run's video theme and re-render its video channels in it (e.g. 'cinematic', 'news_package'). Same as replying 'switch to the X theme'.",
     "inputSchema": {"type": "object", "required": ["run_id", "theme"], "properties": {"run_id": {"type": "integer"}, "theme": {"type": "string"}}}},
    {"name": "get_brain", "description": "Which AI runs the in-app agent for this account (Claude, their own key, the free model or the rule writer) and the included budget left.",
     "inputSchema": {"type": "object", "properties": {}}},
] + agent_brain.tool_specs_for_mcp()
READ_ONLY = {"get_onboarding", "get_brain", "list_themes"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    h = studio.house
    ob = attach(h)
    try:
        if name == "make_post":
            return h.brain.start(actor, {k: args.get(k) for k in ("prompt", "channel_ids", "format", "brain", "theme") if args.get(k)} | {"prompt": args.get("prompt", "")})
        if name == "list_themes":
            from .media import styles as media_styles
            return {"themes": media_styles.describe_themes(), "default": "auto"}
        if name == "set_theme":
            return h.brain.tool(actor, int(args.get("run_id") or 0), "set_theme", {"theme": str(args.get("theme") or ""), "why": "you picked it"})
        if name == "reply_post":
            return h.brain.reply(actor, int(args.get("run_id") or 0), args.get("text", ""))
        if name == "replace_channel_media":
            return h.brain.channel_media(actor, int(args.get("run_id") or 0), str(args.get("channel") or ""), str(args.get("media_token") or ""))
        if name == "edit_brand":
            return edit_brand(h, actor, args.get("instruction", ""))
        if name == "get_onboarding":
            return ob.get(actor)
        if name == "set_onboarding":
            return ob.put(actor, args)
        if name == "get_recommendations":
            return ob.recommendations(actor, bool(args.get("refresh")))
        if name == "get_brain":
            return brain_status(h, actor)
        if name.startswith("post_"):
            rid = int(args.pop("run_id", 0) or 0)
            return h.brain.tool(actor, rid, name[5:], args)
    except OnboardingError as e:
        raise ActionError(str(e))
    return NotImplemented
