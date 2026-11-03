"""Make a post package controls (UI = REST = MCP), owner bug list of 3 Nov 2026.

* ``update_package_draft``: a package that changed after it was saved to Buffer ("Changed since saved to Buffer") puts its
  new media and caption on that same Buffer DRAFT (editPost); a deleted draft is saved again. Scheduled posts are never
  touched (Actions.replace_draft refuses anything but a draft).
* ``undo_package_media``: every channel keeps the versions its media had (result.media_versions); Undo restores the last one
  ("Back to the Reel" after a template switch made it a carousel).
* ``list_music`` / ``set_post_music``: one Music control per package. None (the default: HuhQueue never adds music by
  itself) or Instagram music on a Reel (Buffer's metadata.instagram.audio, picked from search_instagram_audio with volumes).
  Carousels: Buffer's API takes music on Reels only (InstagramPostMetadataInput has no audio field for a carousel; the
  documented schema, developers.buffer.com/reference, checked 3 Nov 2026), so the answer says to add it in the Instagram
  app. "use lofi" searches and picks the top track; "add music" with nothing to go on asks (needs_choice with trending tracks).
* ``use_brand_kit``: change the run's brand kit. No kit named -> needs_choice listing the account's available kits (never the
  sample catalogue); a name -> this run only; default=true -> the account's active kit too.
"""
from __future__ import annotations

import re
import time
from typing import Any

from fastapi import Body, Request

from .actions import ActionError, Actor

VIDEO = (".mp4", ".mov", ".m4v", ".webm")
CAROUSEL_MUSIC = ("Instagram adds music to a carousel only in the Instagram app: Buffer's API takes music on Reels only. "
                  "After it's posted (or from Buffer's reminder), open it in Instagram and pick a song there.")
OTHER_MUSIC = "Buffer can't add music to {svc} posts. Add a sound in the {svc} app after it is posted."
GENERIC = re.compile(r"^(?:some|a|an|the|any|nice|good|cool|music|song|songs|track|tracks|sound|sounds|audio|background|bgm|"
                     r"instagram|ig|please|it|to|on|for|with|this|reel|me|add|use|put)$", re.I)


def _is_video(u: str) -> bool:
    return str(u or "").lower().split("?")[0].endswith(VIDEO)


def _run(house, a: Actor, rid: int) -> dict:
    run = house.run(a, rid)
    if run["kind"] != "agent":
        raise ActionError("This works on posts made in Make a post.", 409)
    return run


def _busy_guard(rid: int) -> None:
    from . import agent_brain
    with agent_brain._rlock:
        if rid in agent_brain._running:
            raise ActionError("The agent is still working on this post. Try again when it's done.", 409)


def _packages(run: dict, channel: str = "") -> list[dict]:
    pks = (run.get("result") or {}).get("packages") or []
    if not channel:
        return pks
    c = channel.strip().lower()
    return [p for p in pks if p["channel_id"] == channel or (p.get("service") or "").lower() == c]


def music_support(pk: dict) -> str:
    """instagram (a Reel: Buffer attaches Instagram music) | app_only (an Instagram carousel or image) | none."""
    if pk.get("service") != "instagram":
        return "none"
    if pk.get("format") == "reel" and any(_is_video(u) for u in pk.get("media_urls") or []):
        return "instagram"
    return "app_only"


def music_note(pk: dict) -> str:
    sup = music_support(pk)
    if sup == "instagram":
        return "Instagram music, through Buffer. Instagram picks which part of the track plays."
    if sup == "app_only":
        return CAROUSEL_MUSIC
    return OTHER_MUSIC.format(svc=(pk.get("service") or "this network").title())


# ---------------------------------------------------------------- music
def list_music(house, a: Actor, rid: int, channel: str = "", query: str = "") -> dict:
    """Every package's music state ({channel_id, service, format, supports, music: none | the track, note}) and, for a
    Reel, Instagram tracks for the query (blank = trending)."""
    run = _run(house, a, rid)
    out = []
    reel = None
    for pk in _packages(run, channel):
        au = (pk.get("options") or {}).get("ig_audio")
        sup = music_support(pk)
        out.append({"channel_id": pk["channel_id"], "service": pk["service"], "format": pk.get("format"), "supports": sup,
                    "music": au or "none", "note": music_note(pk)})
        if sup == "instagram" and not reel:
            reel = pk
    res: dict = {"run_id": rid, "packages": out, "choices": ["none", "instagram"], "default": "none"}
    if reel:
        from . import reel_audio
        found = reel_audio.search(house.studio, a, reel["channel_id"], query)
        res.update(tracks=found.get("tracks") or [], query=found.get("query", ""), needs_facebook=bool(found.get("needs_facebook")))
        if found.get("needs_facebook"):
            res.update(message=found.get("message"), help_url=found.get("help_url"))
    return res


def set_post_music(house, a: Actor, rid: int, channel: str = "", music: str = "", audio: dict | None = None, query: str = "",
                   music_volume: Any = None, original_volume: Any = None) -> dict:
    """music: none | instagram. With instagram: audio (a track from list_music / search_instagram_audio) is put on the Reel;
    else query ("lofi") searches and picks the top track; else needs_choice with trending tracks to pick from."""
    from . import reel_audio
    _busy_guard(rid)
    run = _run(house, a, rid)
    m = str(music or "").strip().lower()
    if not m:
        m = "instagram" if (audio or query) else ""
    if m in ("off", "remove", "no", "null"):
        m = "none"
    if m not in ("none", "instagram"):
        raise ActionError("music is none or instagram (Instagram music on a Reel, through Buffer).")
    pks = _packages(run, channel)
    if not pks:
        raise ActionError("No post for that channel in this run.", 404)
    if m == "none":
        changed = []
        for pk in pks:
            if (pk.get("options") or {}).get("ig_audio"):
                reel_audio.set_reel_audio(house, a, rid, pk["channel_id"], None)
                changed.append(pk["service"])
        return {"music": "none", "changed": changed, "packages": _state(house, a, rid, channel)}
    reels = [p for p in pks if music_support(p) == "instagram"]
    if not reels:
        p = next((x for x in pks if x.get("service") == "instagram"), pks[0])
        return {"music": "unsupported", "supports": music_support(p), "message": music_note(p), "packages": _state(house, a, rid, channel)}
    alternatives: list[dict] = []
    track = audio if isinstance(audio, dict) and audio else None
    if not track:
        q = " ".join(w for w in re.findall(r"[\w'&-]+", str(query or "")) if not GENERIC.match(w)).strip()
        found = reel_audio.search(house.studio, a, reels[0]["channel_id"], q)
        if found.get("needs_facebook"):
            return {"music": "unavailable", "needs_facebook": True, "message": found.get("message"), "help_url": found.get("help_url")}
        tracks = [t for t in found.get("tracks") or [] if t.get("type") == "music"] or found.get("tracks") or []
        if not q or not tracks:
            # "add music" with nothing to go on (or nothing found): ask, with tracks to pick from
            return {"music": "none", "needs_choice": True,
                    "question": (f"I couldn't find “{q}” on Instagram. " if q and not tracks else "") +
                                "Which music? Pick a track, or say a mood or artist (lofi, upbeat, piano…).",
                    "choices": [_brief(t) for t in (tracks or [])[:5]], "query": q}
        track, alternatives = tracks[0], tracks[1:4]
    done = []
    for pk in reels:
        reel_audio.set_reel_audio(house, a, rid, pk["channel_id"], track, music_volume, original_volume)
        done.append(pk["service"])
    return {"music": "instagram", "track": _brief(track), "channels": done, "alternatives": [_brief(t) for t in alternatives],
            "packages": _state(house, a, rid, channel)}


def _brief(t: dict) -> dict:
    keys = ("audio_id", "title", "display_artist", "creator_username", "duration_ms", "type", "cover_url")
    return {k: t.get(k) for k in keys if t.get(k) not in (None, "")}


def _state(house, a, rid, channel=""):
    run = house.run(a, rid)
    return [{"channel_id": p["channel_id"], "service": p["service"], "format": p.get("format"), "supports": music_support(p),
             "music": (p.get("options") or {}).get("ig_audio") or "none"} for p in _packages(run, channel)]


# ---------------------------------------------------------------- undo (media versions)
def undo_package_media(house, a: Actor, rid: int, channel: str = "") -> dict:
    """Restore a channel's previous media (or, with no channel, every channel changed by the latest edit). The
    restored version brings its own template record back; the packages are checked again."""
    _busy_guard(rid)
    run = _run(house, a, rid)
    res = run["result"]
    vs_all = res.get("media_versions") or {}
    if channel:
        ids = [p["channel_id"] for p in _packages(run, channel)] or [channel]
    else:
        last = max((v[-1]["at"] for v in vs_all.values() if v), default=0)
        ids = [cid for cid, v in vs_all.items() if v and v[-1]["at"] >= last - 120]
    ids = [cid for cid in ids if vs_all.get(cid)]
    if not ids:
        raise ActionError("There's no earlier version of this post to go back to.", 409)
    cm = res.setdefault("channel_media", {})
    labels, tpl = [], None
    for cid in ids:
        v = vs_all[cid].pop()
        cm[cid] = v["cm"]
        labels.append(v.get("label") or "Undo")
        tpl = v.get("template") or tpl
    if tpl:
        res["template"] = tpl
    res.pop("ask", None)
    seen, allm = set(), []
    for v in cm.values():
        for mm in v.get("media") or []:
            if mm.get("url") not in seen:
                seen.add(mm.get("url"))
                allm.append(mm)
    res["media"] = allm
    house._save(rid, run["steps"], res)
    house.brain.tool(a, rid, "check", {})
    run = house.run(a, rid)
    lab = labels[0]
    what = ("back to the" + lab[len("Back to the"):]) if lab.startswith("Back to the") else "undone"
    run["result"].setdefault("thread", []).append({"role": "agent", "at": time.time(),
                                                   "text": f"Done: {what}. The newer version is off this post (its files stay in your Library)."})
    house._save(rid, run["steps"], run["result"], "ready" if run["result"].get("packages") else run["status"])
    return house.run(a, rid)


# ---------------------------------------------------------------- brand kits
def available_kits(house, a: Actor) -> list[dict]:
    """The account's own kits that can be used (BrandKits.available): never the untouched sample brands unless active."""
    from . import brand_kits
    return brand_kits.attach(house).available(a)["available"]


def kit_choice(house, a: Actor, why: str = "") -> dict:
    kits = available_kits(house, a)
    names = [k["name"] + (" (your default)" if k["active"] else "") for k in kits]
    q = (why + " " if why else "") + ("Which brand kit should this post use? " + ", ".join(names) + "." if kits else
                                      "You have no brand kit yet: make one in Brand › Brand kits.")
    return {"needs_choice": True, "question": q.strip(), "choices": [k["name"] for k in kits], "kits": kits}


def use_brand_kit(house, a: Actor, rid: int, kit: Any = None, default: bool = False) -> dict:
    """The run's brand kit. kit: a name or id from the account's available kits. None -> needs_choice (never guessed)."""
    from .agent_tools import Tools
    _busy_guard(rid)
    _run(house, a, rid)
    k = str(kit if kit is not None else "").strip()
    if not k:
        return kit_choice(house, a)
    kits = available_kits(house, a)
    hit = next((x for x in kits if str(x["id"]) == k or x["name"].strip().casefold() == k.casefold()), None)
    if not hit:
        return kit_choice(house, a, f"I don't have a kit called “{k[:40]}”.")
    tools = Tools(house, a, rid)
    kt = tools._kit_theme(hit["name"])
    if not kt:
        return kit_choice(house, a, f"I couldn't open the {hit['name']} kit.")
    house.studio._guard_write(a, "agent_reply")
    run = house.run(a, rid)
    run["result"].setdefault("thread", []).append({"role": "user", "at": time.time(),
                                                   "text": f"Use the {hit['name']} brand kit" + (" and make it my default" if default else "")})
    run["result"].pop("ask", None)
    house._save(rid, run["steps"], run["result"], "running")
    house.brain._spawn(house.brain._bg, a, rid, lambda a_, r_, t_: house.brain._kit_go(a_, r_, t_, kt, bool(default)))
    return house.run(a, rid)


# ---------------------------------------------------------------- MCP
TOOLS: list[dict[str, Any]] = [
    {"name": "update_package_draft", "description": "A package that changed after it was saved as a Buffer draft (result.packages[].changed_since_sent with buffer_draft) puts its CURRENT media and caption on that same Buffer draft (editPost). If the draft was deleted in Buffer, a new draft is saved. Never touches a scheduled or sent post. Same as the card's 'Update draft'.",
     "inputSchema": {"type": "object", "required": ["run_id", "channel_id"], "properties": {"run_id": {"type": "integer"}, "channel_id": {"type": "string"}}}},
    {"name": "undo_package_media", "description": "Go back to a channel's previous media in a post run (the card's 'Undo' / 'Back to the Reel'): every media change (a template switch, a re-render, a replaced file) keeps the version before it (packages[].undo says what Undo goes back to). channel: a channel id or network name; leave it out to undo the latest change on every channel it touched. The packages are checked again.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}, "channel": {"type": "string"}}}},
    {"name": "list_music", "description": "Music on a post run's packages: each package's state (supports: instagram = a Reel, Buffer attaches Instagram music | app_only = an Instagram carousel or image, music is added in the Instagram app because Buffer's API has no music field for them | none) and the current track or 'none' (the default: HuhQueue never adds music by itself). For a Reel it also returns Instagram tracks for query (blank = trending). Read-only.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}, "channel": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "set_post_music", "description": "Set the Music on a post run's packages, like replying 'add music', 'use lofi' or 'no music'. music: none (remove; the default) | instagram. With instagram: audio = a track from list_music (exact), or query = a mood or artist ('lofi': the top Instagram track is picked and alternatives are returned), or neither -> needs_choice with trending tracks (ask the person, then call again with audio). music_volume / original_volume 0-100. Reels only: a carousel answers music='unsupported' with how to add it in the Instagram app. Nothing is posted here.",
     "inputSchema": {"type": "object", "required": ["run_id", "music"], "properties": {
         "run_id": {"type": "integer"}, "channel": {"type": "string"}, "music": {"type": "string", "enum": ["none", "instagram"]},
         "audio": {"type": ["object", "null"]}, "query": {"type": "string"},
         "music_volume": {"type": "integer", "minimum": 0, "maximum": 100}, "original_volume": {"type": "integer", "minimum": 0, "maximum": 100}}}},
    {"name": "use_brand_kit", "description": "Change the brand kit of ONE post run (it is re-rendered in that kit). kit: a kit name or id from the account's available kits (list_brand_kits). Without kit it returns needs_choice with the available kits as choices: ask the person which, never guess. default=true also makes it the account's active kit (only when the person said 'make it my default').",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}, "kit": {"type": ["string", "integer"]},
                                                                            "default": {"type": "boolean"}}}},
]
READ_ONLY = {"list_music"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in {t["name"] for t in TOOLS}:
        return NotImplemented
    h = studio.house
    rid = int(args.get("run_id") or 0)
    if name == "update_package_draft":
        return h.update_package_draft(actor, rid, str(args.get("channel_id") or ""))
    if name == "undo_package_media":
        return undo_package_media(h, actor, rid, str(args.get("channel") or ""))
    if name == "list_music":
        return list_music(h, actor, rid, str(args.get("channel") or ""), str(args.get("query") or ""))
    if name == "set_post_music":
        return set_post_music(h, actor, rid, str(args.get("channel") or ""), str(args.get("music") or ""), args.get("audio"),
                              str(args.get("query") or ""), args.get("music_volume"), args.get("original_volume"))
    return use_brand_kit(h, actor, rid, args.get("kit"), bool(args.get("default")))


# ---------------------------------------------------------------- REST
def add_routes(app, house, actor) -> None:
    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/update-draft")
    def pkg_update_draft(rid: int, channel_id: str, request: Request):
        """update_package_draft: the package's Buffer draft takes its current media and caption."""
        return house.update_package_draft(actor(request, write=True), rid, channel_id)

    @app.post("/api/agent/runs/{rid}/undo")
    def run_undo(rid: int, request: Request, body: dict = Body(default={})):
        """undo_package_media: {"channel": id or network} restores that channel's previous media; {} the latest change."""
        return undo_package_media(house, actor(request, write=True), rid, str((body or {}).get("channel") or ""))

    @app.get("/api/agent/runs/{rid}/music")
    def run_music(rid: int, request: Request, channel: str = "", q: str = ""):
        """list_music: each package's music state, and Instagram tracks for a Reel (blank q = trending)."""
        return list_music(house, actor(request), rid, channel, q)

    @app.post("/api/agent/runs/{rid}/music")
    def run_music_set(rid: int, request: Request, body: dict = Body(default={})):
        """set_post_music: {"music": "none"} or {"music": "instagram", "audio": track | "query": "lofi"}."""
        b = body or {}
        return set_post_music(house, actor(request, write=True), rid, str(b.get("channel") or ""), str(b.get("music") or ""),
                              b.get("audio"), str(b.get("query") or ""), b.get("music_volume"), b.get("original_volume"))

    @app.post("/api/agent/runs/{rid}/kit")
    def run_kit(rid: int, request: Request, body: dict = Body(default={})):
        """use_brand_kit: {"kit": name or id, "default": false}; no kit -> needs_choice with the available kits."""
        b = body or {}
        return use_brand_kit(house, actor(request, write=True), rid, b.get("kit"), bool(b.get("default")))
