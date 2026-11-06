"""MCP parity tools (3 Nov 2026): what the UI could do and an AI app couldn't.

* get_media_preview  An external agent SEES what it made: frame stills (JPEG, inline MCP image content, default 0/1/2 s)
                     of the user's own video or image, plus a short-lived signed URL (/ms/<exp>/<sig>/<token>, 15 min).
                     Claude's sandbox got 403 downloading /m/<id>.mp4; the stills need no download at all.
* replace_draft_media  New media on ANY Buffer draft, also one HuhQueue didn't make (Claude: "refuses to swap media because
                     the draft was built outside its agent"). Drafts only. strategy edit (Buffer editPost with new assets)
                     or recreate (a new draft with the same caption, channel and settings + the new media); the old draft
                     is deleted only with delete_old=true AND confirm=true, after the new one exists.
* get_page_context   Per page of the app: what it shows and which MCP tools do the same things, so an agent knows where
                     each tool fits (the agent-facing map of the UI).

Security (work/plans/SECURITY_RULES.md): ownership is checked on every token/post (another person's media or draft is
"not found"); writes pass the kill switch and the token's daily cap (Studio._guard_write); scheduled and sent posts are
never touched; previews are rate-limited per person (PREVIEW_PER_10MIN) and capped in size (PREVIEW_MAX_BYTES).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import tempfile
import threading
import time
from typing import Any

from .actions import ActionError

PREVIEW_PER_10MIN = 20
PREVIEW_MAX_BYTES = 150 * 1024 * 1024
SIGNED_TTL = 15 * 60
MAX_FRAMES = 6
_hits: dict[str, list[float]] = {}
_hlock = threading.Lock()


def _limit(uid: str) -> None:
    now = time.time()
    with _hlock:
        h = [t for t in _hits.get(uid, []) if t > now - 600]
        if len(h) >= PREVIEW_PER_10MIN:
            _hits[uid] = h
            raise ActionError(f"Too many previews: up to {PREVIEW_PER_10MIN} every 10 minutes. Wait a few minutes.", 429)
        h.append(now)
        _hits[uid] = h


# ------------------------------------------------------------------------------------------------ signed media URLs
def _key(settings) -> bytes:
    return hashlib.sha256(("hq-signed-media:" + (getattr(settings, "session_secret", "") or "dev")).encode()).digest()


def sign(settings, token: str, ttl: int = SIGNED_TTL, now: float | None = None) -> tuple[int, str]:
    exp = int((now or time.time()) + ttl)
    sig = hmac.new(_key(settings), f"{exp}:{token}".encode(), hashlib.sha256).hexdigest()[:32]
    return exp, sig


def verify(settings, exp: str, sig: str, token: str) -> bool:
    try:
        e = int(exp)
    except (TypeError, ValueError):
        return False
    if e < time.time() or e > time.time() + 24 * 3600:
        return False
    want = hmac.new(_key(settings), f"{e}:{token}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(want, str(sig or ""))


def signed_url(house, token: str) -> dict:
    exp, sig = sign(house.s, token)
    return {"url": f"{house.s.public_url.rstrip('/')}/ms/{exp}/{sig}/{token}", "expires_at": exp, "expires_in_s": SIGNED_TTL}


# ------------------------------------------------------------------------------------------------ media preview
def _own_token(house, a, args: dict) -> str:
    tok = str(args.get("media_token") or "").strip()
    if not tok and args.get("url"):
        tok = house.media_token_of(str(args["url"]))
        if not tok:
            raise ActionError("url must be one of HuhQueue's own media links (/m/<token>), e.g. a render_status or get_run URL.", 400)
    if not tok:
        raise ActionError("Give media_token or url (a HuhQueue /m/<token> link).", 400)
    tok = tok.split("/")[0]
    rows = house.store.q("SELECT user_id FROM media WHERE token=?", (tok,))
    owner = rows[0]["user_id"] if rows else (house.library.by_token(tok) or {}).get("user_id")
    if owner != a.uid:
        raise ActionError("No such media in your account.", 404)
    return tok


def _frames(path: str, times: list[float], width: int) -> list[tuple[float, bytes]]:
    from . import mediasafe
    from .media import ff
    try:
        mediasafe.check_source(path)  # a real media file before any decoder (SECURITY_RULES: uploads checked first)
    except Exception as e:  # noqa: BLE001
        raise ActionError(f"That file can't be previewed: {str(e)[:120]}", 415)
    out = []
    for t in times:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            jpg = f.name
        try:
            mediasafe.run([ff.ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
                           "-vf", f"scale={width}:-2", "-q:v", "5", jpg], timeout=30, check=True)  # protocol/format whitelists
            data = open(jpg, "rb").read()
            if data:
                out.append((t, data))
        except Exception:  # noqa: BLE001  past the end, or no video stream: that frame is skipped
            pass
        finally:
            try:
                os.unlink(jpg)
            except OSError:
                pass
    return out


def _thumb(data: bytes, width: int) -> bytes:
    import io

    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.thumbnail((width, width * 3))
    buf = io.BytesIO()
    im.convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


def media_preview(studio, a, args: dict) -> dict:
    house = studio.house
    tok = _own_token(house, a, args)
    _limit(a.uid)
    width = max(160, min(int(args.get("width") or 480), 720))
    times = args.get("frames_at")
    times = [0, 1, 2] if not isinstance(times, list) or not times else times
    try:
        times = sorted({max(0.0, min(float(t), 3600.0)) for t in times})[:MAX_FRAMES]
    except (TypeError, ValueError):
        raise ActionError("frames_at is a list of seconds, e.g. [0, 1, 2].", 400)
    head = house.media_head(tok) or {}
    if int(head.get("size") or 0) > PREVIEW_MAX_BYTES:
        raise ActionError(f"That file is over {PREVIEW_MAX_BYTES // (1024 * 1024)} MB; preview a shorter cut.", 413)
    mime = str(head.get("mime") or "")
    # a video is decoded from the instance's disk cache (filled once from the database/R2), never held in memory or
    # downloaded again for every preview (Render RAM, Supabase egress); images and a cache miss read the bytes as before
    cached = house.media_path(tok) if mime.startswith("video/") else None
    m = None if cached else house.get_media(tok)
    if not cached and not m:
        raise ActionError("No such media in your account.", 404)
    mime = mime or str((m or {}).get("mime") or "")
    size = int(head.get("size") or 0) or (len(m["data"]) if m else 0)
    out: dict[str, Any] = {"media_token": tok, "mime": mime, "bytes": size, "signed": signed_url(house, tok),
                           "public_url": house.media_url(tok)}
    imgs = []
    if mime.startswith("image/") and m:
        imgs = [(0.0, _thumb(m["data"], width))]
    elif mime.startswith("video/") and cached:
        imgs = _frames(str(cached), times, width)
    elif mime.startswith("video/") and m:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(m["data"])
            src = f.name
        try:
            imgs = _frames(src, times, width)
        finally:
            os.unlink(src)
    else:
        raise ActionError(f"Can't preview {mime or 'this file'}: only images and videos.", 415)
    out["frames"] = [{"at_s": t, "bytes": len(d)} for t, d in imgs]
    out["_mcp_images"] = [{"data": base64.b64encode(d).decode(), "mimeType": "image/jpeg"} for _, d in imgs]
    out["note"] = ("The stills are attached as images. The signed URL works for 15 minutes; if your sandbox can't download "
                   "it, judge from the stills.") if imgs else "No frame could be read at those times (the video may be shorter)."
    house.store.audit(a.uid, a.label, "get_media_preview", "read", {"token": tok[:8], "frames": len(imgs)})
    return out


# ------------------------------------------------------------------------------------------------ replace draft media
def replace_draft_media(studio, a, args: dict) -> dict:
    from .buffer_api import BufferError
    pid = str(args.get("post_id") or "").strip()
    urls = [str(u) for u in (args.get("media_urls") or ([args["media_url"]] if args.get("media_url") else []))][:10]
    if not pid or not urls:
        raise ActionError("Give post_id (a Buffer draft from list_queue status=draft) and media_urls (the new media).", 400)
    strategy = str(args.get("strategy") or "auto")
    if strategy not in ("auto", "edit", "recreate"):
        raise ActionError("strategy is auto, edit or recreate.", 400)
    post, ch = studio._find_post(a, pid)
    if post["status"] != "draft":
        raise ActionError("Only drafts: that post is scheduled or sent in Buffer, so its media is never changed here. "
                          "Ask the person to move it back to drafts first.", 409)
    text = str(args["text"]) if args.get("text") else str(post.get("text") or "")
    options = dict(args.get("options") or {}) if isinstance(args.get("options"), dict) else {}
    if ch.get("service") == "instagram" and "ig_audio" not in options:
        try:  # keep the Reel's music when Buffer will say what it is
            au = studio.buffer.post_audio(a.uid, pid)
        except BufferError:
            au = None
        if au and au.get("id"):
            options["ig_audio"] = {"audio_id": au["id"], "title": au.get("title") or "", "type": au.get("type") or "music",
                                   "duration_ms": au.get("duration"),
                                   **({"music_volume": au["audioVolume"]} if au.get("audioVolume") is not None else {}),
                                   **({"original_volume": au["videoVolume"]} if au.get("videoVolume") is not None else {})}
    body = {"text": text, "media_urls": urls, "options": options, "override_warnings": bool(args.get("override_warnings"))}
    if strategy in ("auto", "edit"):
        try:
            out = studio.replace_draft(a, pid, body)
            return {**out, "strategy": "edit", "post_id": pid, "kept": ["caption", "channel"] + (["music"] if "ig_audio" in options else [])}
        except BufferError as e:
            if strategy == "edit":
                raise
            edit_error = str(e)
    else:
        edit_error = ""
    # recreate: a new draft with the same caption, channel and settings + the new media
    prev = studio.preview_post(a, {"channel_id": ch["id"], "text": text, "media_urls": urls, "mode": "draft", "options": options,
                                   "override_warnings": bool(args.get("override_warnings"))})
    made = studio.submit_post(a, prev["confirm_token"])
    new_id = ((made.get("post") or {}).get("id")) if isinstance(made, dict) else None
    res = {"strategy": "recreate", "old_post_id": pid, "new": made, "new_post_id": new_id,
           **({"edit_error": edit_error} if edit_error else {})}
    if args.get("delete_old") and args.get("confirm") is True and new_id:
        res["old_deleted"] = studio.delete_draft(a, pid)
    elif new_id:
        res["old_deleted"] = False
        res["next"] = (f"The old draft {pid} is still in Buffer. To remove it, call again with delete_old=true and confirm=true "
                       "after the person says yes, or delete_draft(post_id).")
    else:
        res["old_deleted"] = False
        res["next"] = "The new draft waits for approval (review token); the old one is kept until it exists."
    return res


# ------------------------------------------------------------------------------------------------ per-page agent context
PAGES: dict[str, dict] = {
    "make_post": {"title": "Make a post", "shows": "The chat thread of one post run: the request, the agent's steps, one post card per channel.",
                  "tools": ["start_post", "continue_run", "make_post", "get_run", "reply_post", "create_package", "replace_channel_media", "set_template", "get_post_templates",
                            "set_theme", "set_cover", "list_music", "set_post_music", "use_brand_kit", "update_package_draft",
                            "undo_package_media", "schedule_package", "get_post_context", "rename_post_thread", "list_threads", "get_thread", "delete_thread", "restore_thread", "get_media_preview",
                            "get_ai_engine", "set_ai_engine"]},
    "planner": {"title": "Planner", "shows": "Scheduled, sent and draft Buffer posts on a calendar, plus each channel's slots.",
                "tools": ["list_calendar", "list_queue", "update_post", "reschedule_post", "replace_draft_media", "delete_draft", "preflight_post"]},
    "inbox": {"title": "Inbox", "shows": "What waits for a person: approvals, failed posts, preflight problems, open slots.",
              "tools": ["get_inbox", "list_approvals", "list_pending_approvals", "approve_link_ingest", "reject_link_ingest", "update_post", "reschedule_post"]},
    "library": {"title": "Library", "shows": "Every upload and every rendered image, carousel and Reel; the Bin.",
                "tools": ["list_library", "delete_library_item", "bulk_delete_library", "list_library_bin", "restore_library_items",
                          "cleanup_agent_drafts",
                          "purge_library_bin", "draft_from_library", "use_library_item", "get_media_preview", "create_upload",
                          "create_send_code", "import_from_link", "list_recent_uploads"]},
    "templates": {"title": "Templates (Brand studio)", "shows": "Built-in and custom templates; custom ones are the person's (\"Yours\").",
                  "tools": ["list_templates", "get_template", "create_template", "update_template", "delete_template", "render_template"]},
    "brand": {"title": "Brand kit", "shows": "Brand kits (colors, font, logo, end card, voice) and the Style Ledger.",
              "tools": ["get_brand", "list_brand_kits", "create_brand_kit", "update_brand_kit", "edit_brand", "remember_rule", "brand_check"]},
    "one_take": {"title": "One take", "shows": "A long video split into short clips placed in a week.",
                 "tools": ["clips_from_video", "ingest_video_link", "list_clips", "adjust_clip", "rewrite_clip_caption", "place_clips_in_week", "split_video", "get_clip_layout", "set_clip_layout",
                           "delete_take", "list_take_bin", "restore_take", "purge_take_bin",
                           "list_threads", "get_thread", "delete_thread", "restore_thread", "list_thread_bin", "purge_thread_bin"]},
    "settings_ai": {"title": "Settings > AI engine", "shows": "Which engine the in-app agent uses, keys, logs.",
                    "tools": ["get_ai_engine", "set_ai_engine", "get_ai_logs", "list_ai_keys", "get_brain"]},
    "activity": {"title": "Activity", "shows": "Every action, sign-in and model call.", "tools": ["get_activity"]},
}


def page_context(studio, a, args: dict) -> dict:
    from . import mcp
    desc = {t["name"]: t["description"].split(". ")[0][:200] for t in mcp.TOOLS}
    page = str(args.get("page") or "").strip()
    pick = {page: PAGES[page]} if page in PAGES else PAGES
    if page and page not in PAGES:
        raise ActionError(f"Unknown page {page!r}. Pages: {', '.join(PAGES)}.", 404)
    return {"pages": [{"page": k, "title": v["title"], "shows": v["shows"],
                       "tools": [{"name": n, "does": desc.get(n, "")} for n in v["tools"] if n in desc]} for k, v in pick.items()],
            "tool_count": len(mcp.TOOLS)}


TOOLS: list[dict[str, Any]] = [
    {"name": "get_media_preview", "description": "SEE what you made before deciding: frame stills (JPEG images in this answer; default at 0, 1 and 2 seconds) "
     "of the person's own HuhQueue video, or a thumbnail of an image, plus a signed download URL that works for 15 minutes. Give "
     f"media_token or url (a HuhQueue /m/<token> link from render_status / get_run / list_library). Read-only; up to {PREVIEW_PER_10MIN} "
     "previews every 10 minutes.",
     "inputSchema": {"type": "object", "properties": {"media_token": {"type": "string"}, "url": {"type": "string"},
                                                      "frames_at": {"type": "array", "items": {"type": "number"}, "maxItems": MAX_FRAMES,
                                                                    "description": "Seconds into the video (default [0, 1, 2])"},
                                                      "width": {"type": "integer", "minimum": 160, "maximum": 720}}}},
    {"name": "replace_draft_media", "description": "Put new media on a Buffer DRAFT, including drafts HuhQueue didn't make. The caption, channel and "
     "settings (and an Instagram Reel's music when Buffer reports it) stay. strategy auto (default): edit the draft in place; if Buffer "
     "refuses, make a NEW draft with the same caption + the new media. recreate: always a new draft. The old draft is deleted only with "
     "delete_old=true AND confirm=true (ask the person first), after the new one exists. Scheduled and sent posts are refused.",
     "inputSchema": {"type": "object", "required": ["post_id", "media_urls"], "properties": {
         "post_id": {"type": "string", "description": "A draft id from list_queue(status='draft')"},
         "media_urls": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "text": {"type": "string", "description": "New caption (default: keep it)"},
         "options": {"type": "object", "description": "Network options to set (title, thumbnail_offset_ms for the cover frame, ig_audio...)"},
         "strategy": {"type": "string", "enum": ["auto", "edit", "recreate"]}, "delete_old": {"type": "boolean"}, "confirm": {"type": "boolean"},
         "override_warnings": {"type": "boolean"}}}},
    {"name": "get_page_context", "description": "What each page of the HuhQueue app shows and which MCP tools do the same things there "
     "(Make a post, Planner, Inbox, Library, Templates, Brand kit, One take, Settings > AI engine, Activity). page: one of them, or omit for all.",
     "inputSchema": {"type": "object", "properties": {"page": {"type": "string", "enum": list(PAGES)}}}},
]
READ_ONLY = {"get_media_preview", "get_page_context"}
NAMES = {t["name"] for t in TOOLS}


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    if name == "get_media_preview":
        return media_preview(studio, actor, args)
    if name == "replace_draft_media":
        studio._guard_write(actor, "replace_draft_media")
        return replace_draft_media(studio, actor, args)
    return page_context(studio, actor, args)
