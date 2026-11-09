"""Instagram music for Reels (UI = REST = MCP).

Buffer's API (experimental, Oct 2026) can search Instagram's licensed music and original sounds for a channel and attach
one track to a Reel: createPost/editPost `metadata.instagram.audio` (InstagramAudioSelectionInput: audioId, title,
duration, type, displayArtist, creatorUsername, audioVolume, videoVolume). It works only for Instagram channels connected
through a Facebook Page; Instagram-Login channels answer ChannelRefreshRequired, which we show as a plain next step.

A track picked here is saved on the run's package (options.ig_audio) and goes to Buffer with the package, through the same
preview -> submit path as every post. Buffer has no start-time field, so start_ms stays with the package only.
"""
from __future__ import annotations

from typing import Any

from fastapi import Body, Request

from .actions import ActionError, Actor, clean_audio
from .buffer_api import BufferError

HELP_URL = "https://support.buffer.com/en-us/articles/getting-instagram-locations-facebook-authorization-required-acaC8V633l"
from .audio_check import NEEDS_FACEBOOK  # noqa: E402  (one wording for the search and the check)
SEARCH_TTL = 300  # one Buffer call per channel + query every 5 minutes, whoever asks (the API budget is shared)
VIDEO = (".mp4", ".mov", ".m4v", ".webm")


def _ig_channel(studio, a: Actor, channel_id: str) -> dict:
    ch = {c["id"]: c for c in studio.channels(a)}.get(channel_id or "")
    if not ch:
        raise ActionError("Unknown channel, or this agent token isn't allowed to use it.", 404)
    if ch["service"] != "instagram":
        raise ActionError("Music can only be added to Instagram Reels.")
    return ch


def _track(x: dict) -> dict:
    return {"audio_id": x.get("id"), "title": x.get("title") or "", "display_artist": x.get("displayArtist") or "",
            "creator_username": x.get("creatorUsername") or "", "duration_ms": int(x.get("duration") or 0),
            "type": x.get("type") or "music", "preview_url": x.get("previewUrl") or "", "cover_url": x.get("coverArtworkUrl") or ""}


def search(studio, a: Actor, channel_id: str, query: str = "") -> dict:
    """Search Instagram music for one channel (blank query = what's trending). Read-only."""
    ch = _ig_channel(studio, a, channel_id)
    q = (query or "").strip()[:100]
    buf = studio.buffer
    try:
        if hasattr(buf, "_cached"):
            raw = buf._cached(("audio", a.uid, ch["id"], q.lower()), SEARCH_TTL, lambda: buf.search_instagram_audio(a.uid, ch["id"], q))
        else:
            raw = buf.search_instagram_audio(a.uid, ch["id"], q)
    except BufferError as e:
        if "with credentials is required" in str(e) or "BAD_USER_INPUT" in str(e):
            raise ActionError("Buffer couldn't search music for this channel. " + NEEDS_FACEBOOK, 409) from e
        raise
    studio.store.audit(a.uid, a.label, "search_instagram_audio", "read", {"channel": "instagram", "query": bool(q),
                                                                          "needs_facebook": bool(raw.get("needs_facebook"))})
    if raw.get("needs_facebook"):
        return {"channel_id": ch["id"], "query": q, "tracks": [], "needs_facebook": True, "message": NEEDS_FACEBOOK, "help_url": HELP_URL}
    tracks = [_track(x) for x in raw.get("audio") or [] if x.get("id")]
    tracks.sort(key=lambda t: 0 if t["type"] == "music" else 1)  # licensed music first, then original sounds
    out = {"channel_id": ch["id"], "query": q, "trending": not q, "tracks": tracks[:40], "needs_facebook": False}
    if raw.get("failed_types"):
        out["note"] = "Instagram didn't answer for: " + ", ".join("original sounds" if t == "originalSound" else t for t in raw["failed_types"])
    return out


from .audio_check import CHECK_TTL, UNLOADABLE, check_track, ensure_playable  # noqa: E402,F401  (fastapi-free: actions.py uses it)


def set_reel_audio(house, a: Actor, rid: int, channel_id: str, audio: dict | None, music_volume: Any = None,
                   original_volume: Any = None, start_ms: Any = None) -> dict:
    """Put a track on one Instagram Reel package (or take it off with audio=None). Saved with the package; it reaches
    Buffer when the package is sent (schedule_package / save drafts). Returns the package."""
    run = house.run(a, rid)
    res = run["result"] or {}
    pk = next((p for p in res.get("packages") or [] if p["channel_id"] == channel_id), None)
    if not pk:
        raise ActionError("No package for that channel in this run.", 404)
    if pk.get("service") != "instagram":
        raise ActionError("Music can only be added to Instagram Reels.")
    if pk.get("status") in ("sent_to_buffer", "pending_approval"):
        raise ActionError("That package was already sent, so its music can't change here.", 409)
    opts = pk.setdefault("options", {})
    if not audio:
        opts.pop("ig_audio", None)
        house._save(rid, run["steps"], res)
        house.store.audit(a.uid, a.label, "set_reel_audio", "write", {"run": rid, "removed": True})
        return pk
    if not any(str(u).lower().split("?")[0].endswith(VIDEO) for u in pk.get("media_urls") or []):
        raise ActionError("Music goes on a Reel: this Instagram post has no video.")
    raw = dict(audio)
    for k, v in (("music_volume", music_volume), ("original_volume", original_volume), ("start_ms", start_ms)):
        if v is not None:
            raw[k] = v
    au = clean_audio(raw)
    if not au:
        raise ActionError("That music choice is incomplete: pass the track's audio_id, title, type and duration_ms from search_instagram_audio.")
    au = ensure_playable(house.studio, a, channel_id, au)  # never save a track Buffer can't load
    opts.update(ig_audio=au, ig_type="reel")
    house._save(rid, run["steps"], res)
    house.store.audit(a.uid, a.label, "set_reel_audio", "write", {"run": rid, "type": au["type"], "music_volume": au["music_volume"],
                                                                  "original_volume": au["original_volume"]})
    return pk


# ---------------------------------------------------------------- MCP
TOOLS: list[dict[str, Any]] = [
    {"name": "search_instagram_audio", "description": "Search Instagram's licensed music and original sounds for one Instagram channel (Buffer's audio catalog). Leave query blank for what's trending. Each track has audio_id, title, display_artist or creator_username, duration_ms, type (music | originalSound), preview_url (a short-lived Meta link) and cover_url. Works only when the channel is connected through a Facebook Page in Buffer; otherwise needs_facebook is true with a help_url. Read-only.",
     "inputSchema": {"type": "object", "required": ["channel_id"], "properties": {"channel_id": {"type": "string"}, "query": {"type": "string"}}}},
    {"name": "set_reel_audio", "description": "Put an Instagram music track on one Reel package of a run (or remove it with audio: null). Pass the track object from search_instagram_audio as audio. music_volume and original_volume are 0-100 (the track and the video's own sound). start_ms is saved with the package, but Buffer's API has no start-time field yet, so Instagram starts the track at its default. The music is sent to Buffer with the package (schedule_package); nothing is posted here.",
     "inputSchema": {"type": "object", "required": ["run_id", "channel_id"], "properties": {
         "run_id": {"type": "integer"}, "channel_id": {"type": "string"},
         "audio": {"type": ["object", "null"], "description": "A track from search_instagram_audio (audio_id, title, type, duration_ms, display_artist, creator_username)"},
         "music_volume": {"type": "integer", "minimum": 0, "maximum": 100}, "original_volume": {"type": "integer", "minimum": 0, "maximum": 100},
         "start_ms": {"type": "integer", "minimum": 0}}}},
]
READ_ONLY = {"search_instagram_audio"}


def call_tool(studio, actor, name: str, args: dict):
    if name == "search_instagram_audio":
        return search(studio, actor, args.get("channel_id", ""), args.get("query", ""))
    if name == "set_reel_audio":
        return set_reel_audio(studio.house, actor, int(args.get("run_id") or 0), args.get("channel_id", ""), args.get("audio"),
                              args.get("music_volume"), args.get("original_volume"), args.get("start_ms"))
    return NotImplemented


# ---------------------------------------------------------------- REST
def add_routes(app, house, actor) -> None:
    studio = house.studio

    @app.get("/api/instagram/audio")
    def audio_search(request: Request, channel_id: str = "", q: str = ""):
        """search_instagram_audio: Instagram music for one channel (blank q = trending)."""
        return search(studio, actor(request), channel_id, q)

    @app.post("/api/agent/runs/{rid}/packages/{channel_id}/audio")
    def audio_set(rid: int, channel_id: str, request: Request, body: dict = Body(default={})):
        """set_reel_audio: put a track on one Instagram Reel package, or remove it (audio: null)."""
        return set_reel_audio(house, actor(request, write=True), rid, channel_id, body.get("audio"), body.get("music_volume"),
                              body.get("original_volume"), body.get("start_ms"))
