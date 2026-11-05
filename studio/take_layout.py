"""One take: a clip's layout plan, per-scene layout overrides, the caption look, and the Smart layout opt-in.

UI = REST = MCP:
  GET  /api/takes/{id}/clips/{clip}/layout     get_clip_layout   the plan [{scene, start, end, layout, rule, ...}],
                                                                 the overrides, the caption preset and the choices
  POST /api/takes/{id}/clips/{clip}/layout     set_clip_layout   layout for one scene (scene) or the whole clip,
                                                                 'auto' to clear; caption_preset; re-renders the clip
  GET/POST /api/takes/settings/smart-layout    (Settings)        "Smart layout (beta)": the owner allows a few
                                                                 keyframes of their clips to go to a free vision model

Scope before existence: someone else's take or clip and a missing one are the same 404. Bad types are 400.
No FastAPI at import time: the video worker imports takes.py, which imports this module.
"""

import json
import time

from .actions import ActionError, Actor

LAYOUT_CHOICES = ("auto", "track", "fit", "split", "screen")
BG_CHOICES = ("blur", "brand")
STYLE_CHOICES = ("pip", "stack")
MAX_SCENES = 200
TOOLS = [
    {"name": "get_clip_layout", "description": "One take: a clip's layout plan, scene by scene ({scene, start, end, layout: "
     "track | fit | split | screen, rule, pinned}), its per-scene overrides, its caption preset and the choices for both.",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id"],
                     "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"}}}},
    {"name": "set_clip_layout", "description": "One take: change a clip's layout for one scene (scene: its index in "
     "get_clip_layout's plan) or for the whole clip (no scene), and/or its caption preset. layout: track (follow the "
     "face), fit (whole frame over a blurred or brand-colour background, bg: blur | brand), split (two people stacked), "
     "screen (screen with the face, style: pip | stack) or auto (back to the automatic choice). caption_preset: "
     "bold_pop | lower_third | two_tone | typewriter. The clip re-renders on the video worker (poll list_clips).",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id"], "properties": {
         "take_id": {"type": "integer"}, "clip_id": {"type": "string"},
         "scene": {"type": "integer", "minimum": 0}, "layout": {"type": "string", "enum": list(LAYOUT_CHOICES)},
         "scenes": {"type": "object", "description": "Several scenes at once: {\"0\": \"track\", \"1\": \"auto\"}",
                    "additionalProperties": {"type": ["string", "object"]}},
         "bg": {"type": "string", "enum": list(BG_CHOICES)}, "style": {"type": "string", "enum": list(STYLE_CHOICES)},
         "caption_preset": {"type": "string", "enum": ["bold_pop", "lower_third", "two_tone", "typewriter"]}}}},
]
READ_ONLY = {"get_clip_layout"}
NAMES = {t["name"] for t in TOOLS}
_TABLE = False


# ---------------------------------------------------------------------- Smart layout (beta) opt-in
def _table(store) -> None:
    global _TABLE
    if not _TABLE:
        store.x("CREATE TABLE IF NOT EXISTS take_prefs (user_id TEXT PRIMARY KEY, data TEXT, updated REAL)")
        _TABLE = True


def _prefs(store, uid: str) -> dict:
    _table(store)
    rows = store.q("SELECT data FROM take_prefs WHERE user_id=?", (uid,))
    try:
        d = json.loads(rows[0]["data"]) if rows else {}
    except (ValueError, TypeError):
        d = {}
    return d if isinstance(d, dict) else {}


def smart_layout_on(house, a: Actor) -> bool:
    """Whether the take's owner allowed keyframes to go to a free vision model. Any read error means no."""
    try:
        return bool(_prefs(house.store, a.uid).get("smart_layout"))
    except Exception:  # noqa: BLE001
        return False


def smart_layout_info(house, a: Actor) -> dict:
    from .media.reframe import vision
    return {"smart_layout": smart_layout_on(house, a), "mode": vision.mode(),
            "providers": [{"name": p["name"], "model": p["model"]} for p in vision.PROVIDERS],
            "note": "Off by default. When on, up to 12 still frames of each clip (no audio, no metadata) may go to a free "
                    "vision model (NVIDIA's free API catalog, then OpenRouter's free models) to suggest a layout. Free "
                    "endpoints may log what they receive and NVIDIA's free tier is for prototyping."}


def set_smart_layout(house, a: Actor, on) -> dict:
    if not isinstance(on, bool):
        raise ActionError("smart_layout must be true or false.")
    if a.kind == "agent":
        raise ActionError("Only the account owner can turn Smart layout on or off, in Settings.", 403)
    d = _prefs(house.store, a.uid)
    d["smart_layout"] = on
    house.store.x("INSERT INTO take_prefs(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                  "data=excluded.data, updated=excluded.updated", (a.uid, json.dumps(d), time.time()))
    house.store.audit(a.uid, a.label, "smart_layout", "write", {"on": on})
    return smart_layout_info(house, a)


# ---------------------------------------------------------------------- the plan + overrides
def _take_id(v) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, str)):
        raise ActionError("take_id must be a number.")
    try:
        return int(v)
    except ValueError:
        raise ActionError("take_id must be a number.")


def _clip(takes, a: Actor, rid, clip_id):
    if not isinstance(clip_id, str) or not clip_id or len(clip_id) > 40:
        raise ActionError("clip_id must be a clip id like c1.")
    run = takes._run(a, _take_id(rid))  # someone else's take and a missing one: the same 404
    res = run["result"] or {}
    clip = next((c for c in res.get("clips", []) if c["id"] == clip_id and c.get("status") != "binned"), None)
    if not clip:
        raise ActionError("No such clip in this take.", 404)
    return run, res, clip


def view(takes, a: Actor, rid, clip_id) -> dict:
    from .media import captions_ass
    run, _, clip = _clip(takes, a, rid, clip_id)
    m = clip.get("meta") or {}
    return {"take_id": run["id"], "clip_id": clip["id"], "status": clip.get("status"), "plan": m.get("layout_plan") or [],
            "sidecar": m.get("layout_sidecar") or [], "detector": m.get("layout_detector"), "vision": m.get("layout_vision"),
            "overrides": clip.get("layout_overrides") or {}, "caption_preset": clip.get("caption_preset") or m.get("caption_preset")
            or captions_ass.DEFAULT_PRESET, "layouts": list(LAYOUT_CHOICES), "caption_presets": captions_ass.presets(),
            "style": run["input"].get("style") or "take"}


def set_layout(takes, a: Actor, rid, clip_id, body: dict) -> dict:
    from .media import captions_ass
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.")
    lay, scene, bg, style, preset = (body.get(k) for k in ("layout", "scene", "bg", "style", "caption_preset"))
    scenes = body.get("scenes")
    batch: dict[int, dict | str] = {}
    if scenes is not None:  # several scenes at once (the UI's Save): {"0": "track", "1": {"layout": "fit", "bg": "brand"}}
        if not isinstance(scenes, dict) or not scenes or len(scenes) > MAX_SCENES:
            raise ActionError("scenes must be an object of {scene index: layout}.")
        for k, v in scenes.items():
            if not (isinstance(k, str) and k.isdigit() and int(k) < MAX_SCENES):
                raise ActionError("scenes keys must be scene indexes (\"0\", \"1\", ...).")
            if isinstance(v, str) and v in LAYOUT_CHOICES:
                batch[int(k)] = v
            elif isinstance(v, dict) and v.get("layout") in LAYOUT_CHOICES and v.get("bg", "blur") in BG_CHOICES \
                    and v.get("style", "pip") in STYLE_CHOICES:
                batch[int(k)] = {x: v[x] for x in ("layout", "bg", "style") if x in v}
            else:
                raise ActionError(f"Each scene's layout must be one of: {', '.join(LAYOUT_CHOICES)}.")
        if lay is not None or scene is not None:
            raise ActionError("Give either scenes, or layout with an optional scene, not both.")
    if lay is not None and (not isinstance(lay, str) or lay not in LAYOUT_CHOICES):
        raise ActionError(f"layout must be one of: {', '.join(LAYOUT_CHOICES)}.")
    if scene is not None and (isinstance(scene, bool) or not isinstance(scene, int) or not 0 <= scene < MAX_SCENES):
        raise ActionError("scene must be a scene index (0, 1, ...) from get_clip_layout.")
    if bg is not None and bg not in BG_CHOICES:
        raise ActionError("bg must be blur or brand.")
    if style is not None and style not in STYLE_CHOICES:
        raise ActionError("style must be pip or stack.")
    if preset is not None and not captions_ass.clean_preset(preset):
        raise ActionError(f"caption_preset must be one of: {', '.join(captions_ass.PRESETS)}.")
    if lay is None and preset is None and not batch:
        raise ActionError("Nothing to change: give layout (with an optional scene) and/or caption_preset.")
    takes.h.studio._guard_write(a, "adjust_clip")
    rid = _take_id(rid)
    with takes._take_lock(rid):
        run, res, clip = _clip(takes, a, rid, clip_id)
        if run["status"] in ("running", "rendering"):
            raise ActionError("This take is still being worked on. Try again when it's ready.", 409)
        if (run["input"].get("style") or "take") != "take" or res.get("sample"):
            raise ActionError("Per-scene layouts apply to One take clips; this take uses the shorts style, which picks its "
                              "own framing.", 409)
        if not takes._can_rerender(a, res):
            raise ActionError("Layout and caption changes re-render the clip on the video worker, which this server doesn't have.", 409)
        plan = (clip.get("meta") or {}).get("layout_plan") or []
        for i in ([scene] if scene is not None else []) + list(batch):
            if plan and i >= len(plan):
                raise ActionError(f"This clip has {len(plan)} scene(s): scene must be 0 to {len(plan) - 1}.")
        ov = dict(clip.get("layout_overrides") or {})
        for i, v in batch.items():
            pin = {"layout": v} if isinstance(v, str) else v
            if pin["layout"] == "auto":
                ov.pop(str(i), None)
            else:
                ov[str(i)] = {k: pin[k] for k in ("layout", "bg", "style") if k in pin and (k == "layout" or
                              (k == "bg" and pin["layout"] == "fit") or (k == "style" and pin["layout"] == "screen"))}
        if lay is not None:
            targets = [scene] if scene is not None else (list(range(len(plan))) or [0])
            for i in targets:
                if lay == "auto":
                    ov.pop(str(i), None)
                else:
                    pin = {"layout": lay}
                    if lay == "fit" and bg:
                        pin["bg"] = bg
                    if lay == "screen" and style:
                        pin["style"] = style
                    ov[str(i)] = pin
        clip["layout_overrides"] = ov
        if preset is not None:
            clip["caption_preset"] = preset
        clip["status"] = "rendering"
        takes._save(rid, run["steps"], res, "rendering")
        takes.store.audit(a.uid, a.label, "set_clip_layout", "write", {"take": rid, "clip": clip_id, "layout": lay, "scene": scene,
                                                                       "caption_preset": preset})
    takes._render(a, rid, [clip_id])
    out = view(takes, a, rid, clip_id)
    out["rerendering"] = True
    return out


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    t = studio.house.takes
    if name == "get_clip_layout":
        return view(t, actor, args.get("take_id"), args.get("clip_id"))
    return set_layout(t, actor, args.get("take_id"), args.get("clip_id"),
                      {k: args[k] for k in ("layout", "scene", "scenes", "bg", "style", "caption_preset") if k in args})


def add_routes(app, house, actor) -> None:
    from fastapi import Body, Request
    t = house.takes

    @app.get("/api/takes/settings/smart-layout")
    def take_smart_layout(request: Request):
        return smart_layout_info(house, actor(request))

    @app.post("/api/takes/settings/smart-layout")
    def take_smart_layout_set(request: Request, body: dict = Body(default={})):
        return set_smart_layout(house, actor(request, write=True), (body or {}).get("smart_layout"))

    @app.get("/api/takes/{rid}/clips/{clip_id}/layout")
    def take_clip_layout(rid: str, clip_id: str, request: Request):
        return view(t, actor(request), rid, clip_id)

    @app.post("/api/takes/{rid}/clips/{clip_id}/layout")
    def take_clip_layout_set(rid: str, clip_id: str, request: Request, body=Body(default={})):
        return set_layout(t, actor(request, write=True), rid, clip_id, body)
