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
            "meter": budget.meter(house.store, a.uid) if brain_llm.operator_key() else None,
            "operator_key": bool(brain_llm.operator_key()),
            "engine": ai_engine(house, a)}


def ai_engine(house, a) -> dict:
    """The AI engine picker (Settings > AI engine, the composer switch, MCP get_ai_engine): every engine with its credit."""
    from . import ai_engines, demo_guard
    budget.ensure(house.store)
    return ai_engines.status(house.store, a.uid, house.s, house.http, demo=demo_guard.is_demo(a.uid))


def set_ai_engine(house, a, engine: str) -> dict:
    from . import ai_engines
    budget.ensure(house.store)
    try:
        chosen = ai_engines.set_pref(house.store, a.uid, engine)
    except ai_engines.EngineError as e:
        raise ActionError(str(e))
    house.store.audit(a.uid, a.label, "set_ai_engine", "write", {"engine": chosen})
    return ai_engine(house, a)


def ai_logs(house, a, engine: str = "", status: str = "", task: str = "", q: str = "", page: int = 1, limit: int = 25) -> dict:
    """Recent model calls (Settings > AI engine > Logs, GET /api/ai/logs, MCP get_ai_logs)."""
    from . import ai_logs as _logs
    return _logs.logs(house.store, a.uid, engine=engine, status=status, task=task, q=q, page=page, limit=limit)


def ai_keys(house, a) -> dict:
    """The person's own AI keys per provider, masked (Settings > AI engine > Your keys, GET /api/ai/keys, MCP list_ai_keys)."""
    from . import byo_keys
    return byo_keys.view(house.store, a.uid)


def save_ai_key(house, a, provider: str, body: dict) -> dict:
    """Test then save one provider key. People only: an agent token can never add or swap a key."""
    from . import byo_keys
    if a.kind != "human":
        raise ActionError("Only a person can add an AI key: open Settings > AI engine > Your keys.", 403)
    try:
        out = byo_keys.save(house.store, a.uid, provider, str(body.get("api_key") or ""), str(body.get("model") or ""),
                            str(body.get("base_url") or ""), http=house.http)
    except byo_keys.KeyError_ as e:
        raise ActionError(str(e), e.status)
    house.store.audit(a.uid, a.label, "save_ai_key", "safety", {"provider": byo_keys.norm(provider), "model": str(body.get("model") or "")[:80]})
    return {**out, "engine": ai_engine(house, a)}


def delete_ai_key(house, a, provider: str) -> dict:
    from . import byo_keys
    if a.kind != "human":
        raise ActionError("Only a person can delete an AI key: open Settings > AI engine > Your keys.", 403)
    try:
        out = byo_keys.delete(house.store, a.uid, provider)
    except byo_keys.KeyError_ as e:
        raise ActionError(str(e), e.status)
    house.store.audit(a.uid, a.label, "delete_ai_key", "safety", {"provider": byo_keys.norm(provider)})
    return {**out, "engine": ai_engine(house, a)}


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
        return house.brain.reply(actor(request, write=True), rid, body.get("text", ""), str(body.get("media_token") or ""))

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/cover")
    def run_channel_cover(rid: int, channel_id: str, request: Request, body: dict = Body(...)):
        """Use another cover variant ({"index": 0-2}) for one channel: its Reel's opening frame is swapped."""
        a = actor(request, write=True)  # sign-in first: an anonymous call is a 401, never a validation message
        try:
            idx = int((body or {}).get("index"))
        except (TypeError, ValueError):
            raise ActionError("Give the cover index (0-2).")
        return house.brain.set_cover(a, rid, channel_id, idx)

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/media")
    def run_channel_media(rid: int, channel_id: str, request: Request, body: dict = Body(default={})):
        """One channel's media: {"token": ...} uses the person's own file (Library item / upload); {} regenerates it."""
        return house.brain.channel_media(actor(request, write=True), rid, channel_id, str((body or {}).get("token") or ""))

    @app.post("/api/agent/runs/{rid}/tools/{name}")
    def run_tool(rid: int, name: str, request: Request, body: dict = Body(default={})):
        return house.brain.tool(actor(request, write=True), rid, name, body or {})

    @app.get("/api/agent/runs/{rid}/templates")
    def run_templates(rid: int, request: Request):
        """The run's template (name and why), 3-4 suggestions with small previews drawn from the run's own facts in its
        brand kit, and every template by name."""
        return house.brain.templates(actor(request), rid)

    @app.post("/api/agent/runs/{rid}/template")
    def run_template(rid: int, request: Request, body: dict = Body(...)):
        """Switch the template by name or id ({"template": "funding", "slides": [2]}); same as replying "use the funding template"."""
        return house.brain.set_template(actor(request, write=True), rid, body or {})

    @app.post("/api/agent/runs/{rid}/resume")
    def run_resume(rid: int, request: Request):
        """Resume a run the server cut off (a deploy, a crash) from its last finished step."""
        return house.brain.resume(actor(request, write=True), rid)

    @app.get("/api/themes")
    def themes_list(request: Request):
        """Every video look (the four edit styles + the themes) with its gallery still and loop, for the theme picker."""
        actor(request)
        from .media import styles as media_styles
        return {"themes": media_styles.describe_themes(), "categories": media_styles.theme_categories(), "default": "auto"}

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

    @app.get("/api/ai/engine")
    def ai_engine_get(request: Request):
        """Which AI engine the agent uses first, each engine's credit in plain words, and the app-wide pools."""
        return ai_engine(house, actor(request))

    @app.put("/api/ai/engine")
    def ai_engine_put(request: Request, body: dict = Body(...)):
        """Pick the agent's engine: {"engine": "gemini-studio" | "nvidia" | "hf" | "claude" | "gemini-flash" | "gemini-pro" | "own" | "free" | "auto"}."""
        return set_ai_engine(house, actor(request, write=True), str((body or {}).get("engine") or ""))

    @app.get("/api/ai/logs")
    def ai_logs_get(request: Request, engine: str = "", status: str = "", task: str = "", q: str = "", page: int = 1, limit: int = 25):
        """Recent AI calls: time, engine, model, task, latency, tokens, cost, fallback reason, ok/error. Filter + page."""
        return ai_logs(house, actor(request), engine, status, task, q, page, limit)

    @app.get("/api/ai/keys")
    def ai_keys_get(request: Request):
        """Your own AI keys per provider (masked, never returned)."""
        return ai_keys(house, actor(request))

    @app.post("/api/ai/keys/{provider}")
    def ai_keys_post(provider: str, request: Request, body: dict = Body(...)):
        """Test and save your key for one provider: {"api_key": "...", "model": "optional", "base_url": "custom only"}."""
        return save_ai_key(house, actor(request, write=True), provider, body or {})

    @app.delete("/api/ai/keys/{provider}")
    def ai_keys_delete(provider: str, request: Request):
        return delete_ai_key(house, actor(request, write=True), provider)

    @app.get("/api/onboarding")
    def onboarding_get(request: Request):
        return call(ob.get, actor(request))

    @app.put("/api/onboarding")
    def onboarding_put(request: Request, body: dict = Body(...)):
        return call(ob.put, actor(request, write=True), body)

    @app.get("/api/prefs/appearance")
    def appearance_get(request: Request):
        return call(ob.appearance, actor(request))

    @app.put("/api/prefs/appearance")
    def appearance_put(request: Request, body: dict = Body(...)):
        return call(ob.set_appearance, actor(request, write=True), (body or {}).get("mode", ""))

    @app.get("/api/recommendations")
    def recs(request: Request, refresh: int = 0):
        a = actor(request, write=bool(refresh))
        return call(ob.recommendations, a, bool(refresh))


TOOLS: list[dict[str, Any]] = [
    {"name": "make_post", "description": "Make on-brand posts from one prompt (prose + links, up to 4,000 characters). A pasted link is THE source (X posts are read through X's public oEmbed). 'all my channels' = every connected channel. HuhQueue's agent plans, renders (carousel/Reel/cover), writes per-channel captions and checks them; poll get_run(run_id). brain='external' creates the run without running HuhQueue's agent, so YOU drive it with the post_* tools. Nothing is published.",
     "inputSchema": {"type": "object", "required": ["prompt"], "properties": {"prompt": {"type": "string"}, "channel_ids": {"type": "array", "items": {"type": "string"}},
                                                                            "format": {"type": "string", "enum": ["auto", "reel", "carousel", "photo"]},
                                                                            "theme": {"type": "string", "description": "A video theme id from list_themes, auto (the agent picks from the content) or none (post the video as it is)"},
                                                                            "add_logo": {"type": "boolean", "description": "With theme none: put the brand logo on the video (default true)"},
                                                                            "brain": {"type": "string", "enum": ["huhqueue", "external"]}}}},
    {"name": "reply_post", "description": "Refine a post run in plain words, like replying in its thread: 'make the hook stronger', 'redo YouTube at 30s', 'LinkedIn more formal', 'logo smaller', 'it's $1.3M' (answers a fact question). Standing instructions become Style Ledger rules. When the run asked for a video upload (result.video_ask), pass media_token (a video from library_upload_url / list_library) and the agent brands it as the Reel.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}, "text": {"type": "string"}, "media_token": {"type": "string"}}}},
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
    {"name": "list_themes", "description": "Every video look for Make a post: the four edit styles (opus, huhbyte_news, minimal, hype) and the video themes (caption_news, news_package, newsreel, dark_keynote, glass_studio, swiss, kino, silent_film, risograph, hologram_hud, screencast, investigation, countdown, crt, tilt_shift, game_show, cinematic) with what each looks like, what it is best for, its gallery category (news, startups, clips, explainers, cinematic), a one-line description, a typical length and its gallery thumbnail.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_theme", "description": "Set a post run's video theme and re-render its video channels in it (e.g. 'cinematic', 'news_package'). Same as replying 'switch to the X theme'. theme 'none' = no style: post the video as it is (fit to the frame, logo, end card; no captions, theme or music).",
     "inputSchema": {"type": "object", "required": ["run_id", "theme"], "properties": {"run_id": {"type": "integer"}, "theme": {"type": "string", "description": "A theme id from list_themes, 'auto' or 'none'"}}}},
    {"name": "set_template", "description": "Switch a post run's template BY NAME or id ('funding', 'GitHub repo pillar', 'editorial', 'text slides', 'news carousel', or a custom template's name), drawn in the run's brand kit. slides: only those slides change (e.g. [2] for 'the second slide'); keep: slides that stay. Same as replying 'use the funding template'.",
     "inputSchema": {"type": "object", "required": ["run_id", "template"], "properties": {"run_id": {"type": "integer"}, "template": {"type": "string"},
                                                                                      "slides": {"type": "array", "items": {"type": "integer"}},
                                                                                      "keep": {"type": "array", "items": {"type": "integer"}}}}},
    {"name": "get_post_templates", "description": "A post run's template (name and why it was picked), the 3-4 suggested others, and every template the agent can use, by name.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}}}},
    {"name": "resume_post", "description": "Resume a post run the server cut off (status 'interrupted': a deploy or a crash) from its last finished step.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}}}},
    {"name": "set_cover", "description": "Pick another of the 3 branded cover variants (index 0-2, in the run's package covers) for one channel: its Reel's opening frame becomes that cover (Instagram/TikTok use it as the cover via the thumbnail offset); the 1280x720 variant is the YouTube thumbnail.",
     "inputSchema": {"type": "object", "required": ["run_id", "channel", "index"], "properties": {"run_id": {"type": "integer"}, "channel": {"type": "string", "description": "channel id or network name"},
                                                                                                "index": {"type": "integer", "minimum": 0, "maximum": 2}}}},
    {"name": "get_ai_engine", "description": "The AI engine HuhQueue's in-app agent uses first (gemini-studio, nvidia, hf, claude, own, free or auto), which one will actually run, and each engine's credit in plain words (e.g. 'NVIDIA (free developer credits): 3 of your 150 calls today', 'Claude: $0.34 of your $1 used') plus the app-wide pools. If the chosen engine is out of credit or erroring, the agent falls back to the next one automatically.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "set_ai_engine", "description": "Choose the in-app agent's AI engine: gemini-studio (Gemini on Google AI Studio's free tier), nvidia (NVIDIA NIM, free developer credits), hf (Hugging Face, free monthly credits), claude (Anthropic, included allowance), own (the person's saved API key), free (the free starter model) or auto (free engines first, then Claude, your key, then the free starter model). A key the person saved for an engine runs that engine. Returns the same status as get_ai_engine.",
     "inputSchema": {"type": "object", "required": ["engine"], "properties": {"engine": {"type": "string", "enum": ["auto", "gemini-studio", "nvidia", "hf", "claude", "own", "free"]}}}},
    {"name": "get_ai_logs", "description": "Recent AI model calls for this account, newest first: time (unix), engine, model, task, latency_ms, tokens in/out, cost_usd, fallback (why the agent moved to this engine), ok and error. Filters: engine (gemini-studio, nvidia, hf, claude, own, free, client), status (ok | error), task, q (free text). Paged: page (1-based), limit 10, 25, 50 or 100. Never includes prompts, replies or keys.",
     "inputSchema": {"type": "object", "properties": {"engine": {"type": "string"}, "status": {"type": "string", "enum": ["ok", "error"]}, "task": {"type": "string"},
                                                      "q": {"type": "string"}, "page": {"type": "integer"}, "limit": {"type": "integer", "enum": [10, 25, 50, 100]}}}},
    {"name": "list_ai_keys", "description": "The person's own AI keys per provider (anthropic, openai, gemini = Google AI Studio, nvidia, hf, groq, openrouter, custom): saved or not, masked hint (…abcd), model, and which engine each one powers. Key values are never returned.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "save_ai_key", "description": "Test and save the person's own key for one provider (it then runs that provider's engine ahead of HuhQueue's key). People only: an agent token is refused with a pointer to Settings > AI engine, so an agent can never add or swap a key.",
     "inputSchema": {"type": "object", "required": ["provider", "api_key"], "properties": {"provider": {"type": "string", "enum": ["anthropic", "openai", "gemini", "nvidia", "hf", "groq", "openrouter", "custom"]},
                                                                                       "api_key": {"type": "string"}, "model": {"type": "string"}, "base_url": {"type": "string"}}}},
    {"name": "delete_ai_key", "description": "Delete the person's own key for one provider. People only (agent tokens are refused).",
     "inputSchema": {"type": "object", "required": ["provider"], "properties": {"provider": {"type": "string"}}}},
    {"name": "get_brain", "description": "Which AI runs the in-app agent for this account (Claude, their own key, the free model or the rule writer) and the included budget left.",
     "inputSchema": {"type": "object", "properties": {}}},
] + agent_brain.tool_specs_for_mcp()
READ_ONLY = {"get_onboarding", "get_brain", "list_themes", "get_post_templates", "get_ai_engine", "get_ai_logs", "list_ai_keys"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    h = studio.house
    ob = attach(h)
    try:
        if name == "make_post":
            return h.brain.start(actor, {k: args.get(k) for k in ("prompt", "channel_ids", "format", "brain", "theme") if args.get(k)}
                                 | {"prompt": args.get("prompt", "")} | ({"add_logo": bool(args["add_logo"])} if "add_logo" in args else {}))
        if name == "list_themes":
            from .media import styles as media_styles
            return {"themes": media_styles.describe_themes(), "categories": media_styles.theme_categories(), "default": "auto"}
        if name == "set_theme":
            return h.brain.tool(actor, int(args.get("run_id") or 0), "set_theme", {"theme": str(args.get("theme") or ""), "why": "you picked it"})
        if name == "set_template":
            return h.brain.set_template(actor, int(args.get("run_id") or 0), args)
        if name == "get_post_templates":
            return h.brain.templates(actor, int(args.get("run_id") or 0), previews=False)
        if name == "resume_post":
            return h.brain.resume(actor, int(args.get("run_id") or 0))
        if name == "set_cover":
            return h.brain.tool(actor, int(args.get("run_id") or 0), "set_cover", {"channel": str(args.get("channel") or ""),
                                                                                    "index": int(args.get("index") or 0)})
        if name == "reply_post":
            return h.brain.reply(actor, int(args.get("run_id") or 0), args.get("text", ""), str(args.get("media_token") or ""))
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
        if name == "get_ai_engine":
            return ai_engine(h, actor)
        if name == "set_ai_engine":
            return set_ai_engine(h, actor, str(args.get("engine") or ""))
        if name == "get_ai_logs":
            return ai_logs(h, actor, str(args.get("engine") or ""), str(args.get("status") or ""), str(args.get("task") or ""),
                           str(args.get("q") or ""), int(args.get("page") or 1), int(args.get("limit") or 25))
        if name == "list_ai_keys":
            return ai_keys(h, actor)
        if name == "save_ai_key":
            return save_ai_key(h, actor, str(args.get("provider") or ""), args)
        if name == "delete_ai_key":
            return delete_ai_key(h, actor, str(args.get("provider") or ""))
        if name.startswith("post_"):
            rid = int(args.pop("run_id", 0) or 0)
            return h.brain.tool(actor, rid, name[5:], args)
    except OnboardingError as e:
        raise ActionError(str(e))
    return NotImplemented
