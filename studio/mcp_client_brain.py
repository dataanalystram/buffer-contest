"""Client brain: over MCP the CLIENT's model (Claude, ChatGPT, Cursor...) does the thinking and HuhQueue does the editing.

Owner decision, 1 Nov 2026 (docs/MCP_TOOLS.md): an MCP call never spends HuhQueue's Anthropic key unless the person asks
for it with ``server_ai: true``. These tools are the agent's hands without its brain: they read sources, find media, edit
images and video, render carousels and Reels in the person's brand, check the brand rules (rule-based) and stage drafts.
The client writes the slide text, the hooks and the captions itself and passes them in.

    research_link / read_x_post / find_media / scrape_brand_assets   -> source text, facts with source URLs, media candidates
    render_carousel(slides=[{eyebrow, headline, accent_word, body, footnote, image}], template, ratio)
    hero_edit(image_url)                                              -> the premium cover edit of an official image
    edit_video / render_reel / split_video  -> job id; poll render_status(job_id) for the download URL
    create_package(captions per channel)    -> a run with ready packages; schedule_package sends them as drafts (approval)
    update_brand_kit, library_upload_url, integrations_status, weekly_recap_plan

Every call is recorded in the activity log and the budget ledger as source=mcp_client_brain at $0.
"""
from __future__ import annotations

import io
import json
import re
import time
from typing import Any

from .actions import ActionError, Actor

S = {"type": "string"}
SOURCE = "mcp_client_brain"
INSTRUCTIONS = (
    "You are the brain; HuhQueue is the hands. Do your own web research with your own tools (search, browsing); HuhQueue's "
    "tools read a link you give it, edit images and video, render in the person's brand kit and stage Buffer drafts. Work in "
    "ONE post per request: "
    "1) Start: call start_post(title, brief, user_asked: true, reason) once for the person's request and keep its run_id. If a "
    "tool answers reused: true, keep working on that run. "
    "2) Pass run_id to every render, edit, upload and create_package call. Follow-ups ('brighter', 'swap slide 3', 'shorter "
    "caption') update the same run: render again with run_id, then create_package(run_id, ...), which updates the channels "
    "you name (the others keep their packages) and keeps the previous version for undo_package_media. A different video "
    "or cover per channel is still ONE run: render each as output 1, 2, 3 and stage them with create_package(run_id, "
    "captions, channel_media={channel: {output: n}}). Never start another post, thread, template or brand kit "
    "unless the person asked; then pass new_post: true (or the tool's user_asked: true) with a one-line reason. "
    "A change of format ('not a carousel, make it a reel') is the SAME run: start_post(run_id, format='reel'), then render "
    "with that run_id. Several outputs for one request ('make 3 reels') are ONE run: pass output: 1, 2, 3 to each render "
    "and create_package. One request = one run = one thread: follow-ups about the SAME content (switch the format, edit slide "
    "3, two more reels of the same story) stay in that run_id. A NEW request with new content, even in this same chat, is a "
    "new post: start_post(new_request: true), never more of the old thread. If you can't tell whether a request is about "
    "the same content, ask the person: 'Continue in the current post or start a new one?' Then use continue_run(run_id) or "
    "start_post(new_request: true). Earlier work: when the person refers to something from before ('the reel from yesterday', "
    "'that talk I clipped', 'the post about X'), call list_threads(query) and get_thread(thread_id) FIRST, then "
    "continue_run(run_id) on the matching run; if more than one thread could be it, ask the person which one. Every answer carries run_id and thread_url (link the person to it). New posts are "
    "never refused for how many you made; the same request repeated within 10 minutes returns the post already open. "
    "3) Images: give HuhQueue the image URL (hero_edit(image_url), render_carousel slides[].image, library_upload_url). It "
    "downloads and edits on its side; don't paste images or upload throwaway copies. If a site blocks the download, ask the "
    "person for the file with create_send_code. "
    "4) Carousel: research_link -> write the slides -> render_carousel(run_id, slides) -> brand_check -> "
    "create_package(run_id, captions) -> show the person -> schedule_package(run_id, mode='draft') only after they say yes. "
    "You CAN edit videos with HuhQueue: edit_video(run_id, media_token, start_s/end_s or cut_first_s, captions) / "
    "split_video(run_id, ...) -> poll render_status every 10-20 s. "
    "5) Fit problems: shorten the text and render again with the same run_id (it replaces the slides, it doesn't add a post "
    "or a Library item). "
    "6) Paid or credit-spending tools (server AI: run_agent, revise, edit_media, make_post, reply_post with server_ai: true) "
    "never run on your say-so: they answer needs_user_approval with a cost estimate until the person approves "
    "it in HuhQueue's Approvals. Ask them; don't retry in a loop. "
    "7) Drafts only by default; posts wait in Approvals unless the token's policy says otherwise. Same call twice is safe: "
    "pass idempotency_key on writes you might retry."
)

# Tools that spend HuhQueue's own model key. Over MCP they need server_ai: true (the person's explicit choice).
SERVER_AI_TOOLS = {"run_agent", "revise", "edit_media", "make_post", "reply_post"}
SERVER_AI_NOTE = " Uses HuhQueue's server AI (counts against your HuhQueue AI allowance): pass server_ai: true to run it."
SEQUENCES = {
    "run_agent": "start_post(title, user_asked: true) -> research_link(url) or find_media(query) -> write the slides/hook yourself -> "
                 "render_carousel(run_id) / render_reel(run_id) -> create_package(run_id, captions) -> schedule_package(run_id, mode=draft)",
    "make_post": "start_post(title, user_asked: true) -> research_link(url) -> write the slides yourself -> render_carousel(run_id, slides) "
                 "-> create_package(run_id, captions) -> schedule_package(run_id, mode=draft)",
    "edit_media": "start_post(title, user_asked: true) -> create_upload (a local file) or library_upload_url(public url, run_id) or "
                  "list_library(kind=video) -> edit_video(run_id, media_token, ...) -> render_status(job_id)",
    "revise": "change your slides/caption yourself and call render_carousel / edit_video again with the SAME run_id, then "
              "create_package(run_id) (it updates the post in place); remember_rule saves a brand rule",
    "reply_post": "write the reply yourself, then preview_post / submit_post",
    "replace_channel_media": "render the new media (render_carousel / edit_video with run_id), then create_package(run_id) with it",
    "edit_brand": "update_brand_kit(colors, font, handle, end_card...) changes the brand kit without AI",
}


def server_ai_hint(name: str) -> dict:
    """What a server-AI tool answers over MCP without server_ai: true: no model call, the client-brain sequence instead."""
    return {"server_ai_required": True, "tool": name, "spent_usd": 0,
            "message": f"{name} uses HuhQueue's server AI, which counts against the user's HuhQueue AI allowance. You are the "
                       "brain here: do it with the client-brain tools (no HuhQueue AI), or call again with server_ai: true if "
                       "the user wants HuhQueue's own agent to do it.",
            "client_brain_sequence": SEQUENCES.get(name, "start_post -> research_link -> render_carousel(run_id) / edit_video(run_id) -> "
                                                         "create_package(run_id)"),
            "how": INSTRUCTIONS}


SLIDE = {"type": "object", "properties": {
    "eyebrow": {"type": "string", "description": "Small uppercase label above the headline, e.g. 'YC F26 · AI + FINTECH'"},
    "headline": {"type": "string", "description": "Max ~8 words (cover max 10). Title Case."},
    "accent_word": {"type": "string", "description": "ONE word of the headline drawn in the brand accent"},
    "body": {"type": "string", "description": "Plain words, max ~40; **bold** marks key facts. On the cover it is the one-line dek."},
    "footnote": {"type": "string", "description": "Small grey source / terms line"},
    "image": {"type": "string", "description": "https URL of a picture (official image from research_link / find_media)"}}}
RIGHTS = {"type": "string", "enum": ["own", "permission", "news_credit"],
          "description": "Whose video this is, said once (no separate confirmation step): own = the user made it; "
                         "permission = the user has the right to use it; news_credit = a news or launch clip used with "
                         "credit: the credit line (credit, or the source's site when omitted) goes in the caption and "
                         "as a lower-third on the video."}
VIDEO_SRC = {"media_token": {"type": "string", "description": "A HuhQueue media token (list_library -> media_token, or an upload)"},
             "library_id": {"type": "integer", "description": "A Library item id (list_library)"},
             "url": {"type": "string", "description": "A public https link to a video file you own (imported first)"}}

RUN_ID = {"type": "integer", "description": "The post this belongs to (start_post / create_package returned it). Pass it on "
          "every call for the same request: the result is kept with that post instead of starting another."}
IDEM = {"type": "string", "description": "Optional: the same key with the same arguments returns the first result (safe retry)"}
OUTPUT = {"type": "integer", "minimum": 1, "maximum": 10, "description": "Which output of this run (default 1). One request "
          "for several things ('make 3 reels') is ONE run: render each with output 1, 2, 3. Rendering an output again replaces "
          "it in place (same Library item)."}
COVER = {"type": ["boolean", "object"], "description": "Branded cover (thumbnail): true, or {title (cover hook, max ~10 words), "
         "image_url (an official image for the cover), index (0-2, which of the 3 variants opens the Reel), opening_frame "
         "(default true: the cover is the Reel's first frames, so Instagram and TikTok show it as the thumbnail)}. render_status "
         "returns the 3 cover variants (covers[].urls) and cover_url.",
         "properties": {"title": {"type": "string"}, "image_url": {"type": "string"}, "index": {"type": "integer", "minimum": 0, "maximum": 2},
                        "opening_frame": {"type": "boolean"}}}

TOOLS: list[dict[str, Any]] = [
    {"name": "research_link", "description": "Client brain (no HuhQueue AI). Read an article, launch page or post URL: title, site, "
     "description, the article paragraphs, figures found in the text (each with the source URL), the page's images and videos, and "
     "for a product / launch link the video-first result (video: status found | youtube_only | none, the maker video to use, the "
     "credit line, and the upload question to ask when it can't be downloaded). You write the facts, slides and captions from this.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S, "max_paragraphs": {"type": "integer", "maximum": 60},
                     "find_video": {"type": "boolean", "description": "Also run the video-first search (always on for Product Hunt links)"}}}},
    {"name": "find_video", "description": "Client brain (no HuhQueue AI). VIDEO FIRST for a product or launch link (Product Hunt, the maker's "
     "site, a name): Product Hunt's API (launch media + website) -> the maker's own site (og:video, <video>, YouTube/Vimeo/Loom embeds, "
     "press kit) -> the official YouTube/X channel. status found = a maker video HuhQueue can fetch (use video.url with render_reel / "
     "edit_video(url) and the credit 'Video: <maker>' on screen and in the caption); youtube_only = only on YouTube, which blocks "
     "downloads from servers: ask the user to upload the file (ask has the exact words and upload_limit_mb), then library_upload_url "
     "-> edit_video; none = no video: make a carousel and offer the upload.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": S}}},
    {"name": "read_x_post", "description": "Client brain (no HuhQueue AI). The free X reader: an X/Twitter post's author, handle, text, "
     "date and its photos/videos (direct MP4 when the post has one), without an X API key.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S}}},
    {"name": "find_media", "description": "Client brain (no HuhQueue AI). Find the REAL launch/demo video or official images for a story "
     "or link on X, YouTube, Vimeo, Loom, YC launch pages, HN, GitHub and company pages (og:video). Ranked candidates with credit "
     "lines; best_downloadable is the one HuhQueue's worker can fetch.",
     "inputSchema": {"type": "object", "required": ["query"], "properties": {"query": S, "max_results": {"type": "integer", "maximum": 10}}}},
    {"name": "scrape_brand_assets", "description": "Client brain (no HuhQueue AI). Read a company's website (read-only, nothing is "
     "saved): name, logo candidates, palette, theme colour, social handles, og image. Use update_brand_kit to apply what you pick.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S}}},
    {"name": "render_carousel", "description": "Render a carousel YOU wrote in the user's brand kit. slides[0] is the cover (eyebrow, "
     "headline with ONE accent_word, body = one-line dek, image = hero picture); every other slide is one idea (eyebrow, headline, "
     "body, footnote, optional image). The Follow card is added last. template: editorial (the HuhByte 'ernd' editorial carousel, "
     "default) or text_slides. Pass run_id (start_post): the slides become that post's carousel, and rendering again with the same "
     "run_id REPLACES them (one Library item per post, never a new one per render). Returns media_urls to pass to "
     "create_package(run_id) and fit_problems to fix by writing shorter text. No HuhQueue AI is used.",
     "inputSchema": {"type": "object", "required": ["slides"], "properties": {
         "slides": {"type": "array", "minItems": 2, "maxItems": 9, "items": SLIDE},
         "template": {"type": "string", "enum": ["editorial", "editorial_story", "text_slides"]},
         "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16"]}, "follow_line": S,
         "keep_case": {"type": "array", "items": S, "description": "Brand names to keep exactly as written in Title Case "
                       "headlines, e.g. ['ernd', 'iPhone']"}, "run_id": RUN_ID, "output": OUTPUT, "idempotency_key": IDEM}}},
    {"name": "hero_edit", "description": "The premium cover edit of an official image (no HuhQueue AI): saliency crop to the frame, "
     "colour match to the brand ground, vignette and fade; optional product cut-out tilted with a shadow. Returns the edited "
     "image URL to use as slides[0].image in render_carousel. Give the image's URL (HuhQueue downloads it); pass run_id so the "
     "edit is kept with that post (it is never added to the Library on its own).",
     "inputSchema": {"type": "object", "required": ["image_url"], "properties": {"image_url": S, "product_url": S,
                     "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16"]}, "run_id": RUN_ID, "idempotency_key": IDEM}}},
    {"name": "edit_video", "description": "Edit or cut a video on the HuhQueue server (no HuhQueue AI): trim to start_s..end_s or "
     "cut_first_s seconds off the start, reframe to 9:16 / 4:5 / 1:1 (speaker-aware), burned-in word captions on or off "
     "(transcribed with Whisper on the video worker), a theme/style (list_themes), music only when asked (music: cinematic | pulse; auto = none), a hook title; the "
     "brand's Follow end card is always added last (brand rule). "
     "Give the video as media_token, library_id or url, and run_id for the post it is for. Returns a job id at once; poll "
     "render_status(job_id) every 10-20 s until status is done, then show download_url and pass it to create_package(run_id).",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC,
         "start_s": {"type": "number"}, "end_s": {"type": "number"},
         "rerender_finished": {"type": "boolean", "description": "A finished HuhQueue reel is re-edited from its original "
                               "upload (no second cover / end card). true keeps the finished reel as the source."},
         "cut_first_s": {"type": "number", "description": "Remove this many seconds from the start"},
         "ratio": {"type": "string", "enum": ["9:16", "4:5", "1:1"]},
         "captions": {"type": ["boolean", "string"], "enum": [True, False, "auto", "replace", "on", "off"],
                      "description": "Word-by-word captions. Default (true / auto): added unless the video already has captions "
                                     "burned in (then none are added, so they never double; render_status says so). "
                                     "\"replace\": crop the old captions off and caption it again. \"on\": caption it anyway. false: none."},
         "caption_style": {"type": "string", "enum": ["opus", "huhbyte_news", "minimal", "hype"], "description": "Caption/edit style"},
         "theme": {"type": "string", "description": "A video theme id from list_themes (overrides caption_style's look)"},
         "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]},
         "hook": {"type": "string", "description": "On-screen title you wrote (max ~12 words, facts only)"},
         "credit": {"type": "string", "description": "Credit line for a video the user didn't make"},
         "rights": RIGHTS, "cover": COVER,
         "rights_confirmed": {"type": "boolean", "description": "Older form of rights=own/permission"},
         "run_id": RUN_ID, "output": OUTPUT, "idempotency_key": IDEM}}},
    {"name": "render_reel", "description": "Make a branded 9:16 Reel (no HuhQueue AI): from a video (same options as edit_video) or "
     "from slides (image URLs, e.g. render_carousel's media_urls) as a slideshow with the end card. Returns a job id; poll "
     "render_status(job_id). With run_id it becomes that post's output (output: 1..10 for several reels in one request).",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC, "images": {"type": "array", "items": S, "maxItems": 10},
         "hook": S, "theme": S, "captions": {"type": ["boolean", "string"], "enum": [True, False, "auto", "replace", "on", "off"]}, "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]},
         "ratio": {"type": "string", "enum": ["9:16", "4:5", "1:1"]}, "rights": RIGHTS, "rights_confirmed": {"type": "boolean"}, "credit": S,
         "run_id": RUN_ID, "output": OUTPUT, "idempotency_key": IDEM}}},
    {"name": "split_video", "description": "Split a long video into 3-7 short vertical clips on the video worker (no HuhQueue AI: "
     "Whisper transcript, built-in moment scorer, face-tracked reframe, captions, cover and end card). Give media_token / "
     "library_id / url (a file or a YouTube/Vimeo/X post) and owns: true (or rights_confirmed + credit). Returns take_id; poll "
     "render_status(take_id=...) or list_clips(take_id). sample: true shows the bundled example (Buffer's Buffer Commit kickoff).",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC, "n": {"type": "integer", "minimum": 1, "maximum": 7},
         "rights": RIGHTS, "owns": {"type": "boolean"}, "rights_confirmed": {"type": "boolean"}, "credit": S, "name": S,
         "channel_ids": {"type": "array", "items": S}, "sample": {"type": "boolean"}, "run_id": RUN_ID, "idempotency_key": IDEM}}},
    {"name": "render_status", "description": "Status of a video job: job_id (edit_video / render_reel / render_template video) or "
     "take_id (split_video). status queued | running | done | failed, with download_url(s) when done and the error when failed.",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "integer"}, "take_id": {"type": "integer"}}}},
    {"name": "create_package", "description": "Stage a post YOU wrote: captions per channel (channel id or service name -> text) and "
     "the media URLs (render_carousel / render_status). Pass run_id (start_post): the post is UPDATED IN PLACE (new captions "
     "and media replace the old ones, the previous version is kept for undo_package_media), never a second post. Without "
     "run_id, this app's open post is updated (reused: true), or a new one is made if there is none. new_post: true with "
     "user_asked: true only when the person asked for another post. Returns run_id with one ready package per channel. "
     "Nothing is posted: show the person, then schedule_package(run_id, channel_id, mode=draft) creates a Buffer draft (it "
     "waits in Approvals when the token needs approval). No HuhQueue AI is used.",
     "inputSchema": {"type": "object", "required": ["captions"], "properties": {
         "captions": {"type": "object", "description": "{channel_id or service: caption}", "additionalProperties": S},
         "media_urls": {"type": "array", "items": S, "maxItems": 10}, "title": S,
         "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"]},
         "rights": RIGHTS, "credit": {"type": "string", "description": "Credit line, e.g. 'Video: OpenAI on YouTube'"},
         "source_url": {"type": "string", "description": "Where the media came from (used for the credit when credit is empty)"},
         "cover": {"type": "object", "description": "The post's cover: {url (render_status cover_url or any https image; shown on "
                   "the Planner and in previews), frame_s (thumbnail frame in seconds; default 0 = the opening frame, which is "
                   "the branded cover when edit_video made one)}. Instagram, TikTok and Pinterest get it as Buffer's thumbnailOffset.",
                   "properties": {"url": S, "frame_s": {"type": "number", "minimum": 0}}},
         "run_id": RUN_ID, "output": {**OUTPUT, "description": "Which output of the run this post uses (default 1); without "
                                      "media_urls, that output's media is staged"},
         "new_post": {"type": "boolean", "description": "true: a separate new post (needs user_asked: true)"},
         "user_asked": {"type": "boolean"}, "reason": S, "idempotency_key": IDEM}}},
    {"name": "library_upload_url", "description": "Only for a photo or video that is ALREADY at a public https URL (up to 40 MB; "
     "a Google Drive, Dropbox, OneDrive or iCloud share link goes through import_from_link, up to the Library's per-file "
     "limit): adds it to the user's Library (the same file twice is kept once: deduped: true) and returns media_token and "
     "library_id for edit_video / split_video / render_carousel. Pass run_id to keep it with that post. Don't upload throwaway "
     "copies of web images: give their URL straight to hero_edit or render_carousel. For a file on the user's computer or in "
     "your sandbox, use create_upload; if your sandbox can't reach its URL, create_send_code (the user drags the file in on any device). "
     "A VIDEO link that isn't the person's own HuhQueue file goes through the same rights step as every tool, before anything is "
     "fetched or saved: needs_user_approval with approval_id; call again with approval_id once the person approved it. A "
     "YouTube / Vimeo / X post page is not a file: use render_reel(run_id, url) or edit_video(run_id, url).",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S, "run_id": RUN_ID, "idempotency_key": IDEM,
                     "approval_id": {"type": "integer", "description": "The approval_id needs_user_approval returned for this "
                                     "video link, once the person approved it"}}}},
    {"name": "integrations_status", "description": "Which integrations are connected for this user (Product Hunt, X reader key, "
     "image models...) and whether each key last tested OK. Never returns a key.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "weekly_recap_plan", "description": "Client brain (no HuhQueue AI): the week's top AI stories from the news sources, "
     "each with its official video/image and credit line. Write the recap slides yourself, then render_template(template="
     "weekly_recap, data={stories:[{title, summary, source, image}]}).",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "maximum": 9}, "days": {"type": "integer", "maximum": 14}}}},
]
NAMES = {t["name"] for t in TOOLS}
READ_ONLY = {"research_link", "read_x_post", "find_media", "find_video", "scrape_brand_assets", "render_status", "integrations_status",
             "weekly_recap_plan"}


# ------------------------------------------------------------------------------------------------ ledger
def record(studio, actor: Actor, tool: str) -> None:
    """source=mcp_client_brain at $0: one activity row (Agent tab) and one ledger row per client-brain call."""
    st = studio.store
    try:
        st.audit(actor.uid, actor.label, "ai_call", "read", {"engine": "client", "source": SOURCE, "for": tool, "cost_usd": 0,
                                                             "provider": "your MCP client", "model": "client model"})
    except Exception:  # noqa: BLE001 - attribution never breaks a tool
        pass
    try:
        from . import budget
        budget.ensure(st)
        st.x("INSERT INTO brain_budget(scope,spent,calls,updated) VALUES(?,?,1,?) ON CONFLICT(scope) DO UPDATE SET "
             "calls=brain_budget.calls+1, updated=excluded.updated", (f"{SOURCE}:{actor.uid}", 0.0, time.time()))
    except Exception:  # noqa: BLE001
        pass


def ledger(store, uid: str) -> dict:
    rows = store.q("SELECT spent, calls FROM brain_budget WHERE scope=?", (f"{SOURCE}:{uid}",))
    return {"source": SOURCE, "calls": int(rows[0]["calls"]) if rows else 0, "spent_usd": float(rows[0]["spent"]) if rows else 0.0}


# ------------------------------------------------------------------------------------------------ helpers
FIG_RX = re.compile(r"[^.!?]*(?:[$€£₹]\s?\d[\d.,]*\s?(?:[kmb]n?|million|billion|thousand)?|\d[\d.,]*\s?%|\b\d[\d,]{2,}\b)[^.!?]*[.!?]", re.I)


def _figures(paragraphs: list[str], url: str, limit: int = 12) -> list[dict]:
    out, seen = [], set()
    for p in paragraphs:
        for m in FIG_RX.finditer(p):
            s = m.group(0).strip()
            if 20 <= len(s) <= 300 and s not in seen:
                seen.add(s)
                out.append({"fact": s, "source_url": url})
            if len(out) >= limit:
                return out
    return out


def _video_source(h, a: Actor, args: dict) -> tuple[str, dict]:
    """(media token, meta) for media_token / library_id / url."""
    tok = str(args.get("media_token") or "").strip()
    if not tok and args.get("library_id") not in (None, ""):
        item = h.library.item(a, int(args["library_id"]))
        tok = item.get("media_token") or ""
    if not tok and args.get("url"):
        url = str(args["url"]).strip()
        base = h.s.public_url.rstrip("/") + "/m/"
        tok = url[len(base):] if url.startswith(base) else h.import_url(a, url)["token"]
    if not tok:
        raise ActionError("Give the video as media_token, library_id or url (list_library(kind=video) lists the user's videos).")
    meta = h.media_meta(a, tok)
    if not meta:
        raise ActionError("Unknown media token. Upload or import the video first (library_upload_url).", 404)
    return tok, meta


def _is_finished_reel(meta: dict) -> bool:
    """A video HuhQueue already rendered (its cover frame, title card, logo and end card are in the picture)."""
    return meta.get("kind") == "render" and meta.get("type", "video") == "video" and (
        meta.get("renderer") == "studio.media" or "cover_frame_secs" in meta or "source_token" in meta)


def _unfinished(h, a: Actor, tok: str, meta: dict, args: dict) -> tuple[str, dict]:
    """edit_video / render on a finished HuhQueue reel would brand it twice: a second end card after the first, the logo
    bug over the old cover and cards (owner bug 3 Nov 2026: the Click reel re-rendered over MCP came back 1.6 s longer with
    two end cards). Start again from the video it was made from; refuse when that is unknown, unless the caller says
    rerender_finished: true (then the old cards stay in the picture, which is what they asked for)."""
    if not _is_finished_reel(meta) or args.get("rerender_finished") is True:
        return tok, meta
    seen = {tok}
    src = meta.get("source_token")
    while src and src not in seen:
        seen.add(src)
        m2 = h.media_meta(a, src)
        if not m2:
            break
        if not _is_finished_reel(m2):
            return src, {**m2, "_from_finished": tok}
        src = m2.get("source_token")
    raise ActionError("That video is already a finished HuhQueue reel (its cover, title card and end card are baked in), so "
                      "editing it again would add a second end card and logo. Pass the original upload (media_token / "
                      "library_id) instead, or rerender_finished: true to keep the old cards in the picture.", 409)


def _img_bytes(h, url: str) -> bytes | None:
    if not url:
        return None
    base = h.s.public_url.rstrip("/") + "/m/"
    if url.startswith(base):
        m = h.get_media(url[len(base):])
        return m["data"] if m else None
    return h._fetch_bytes(url, 8 * 1024 * 1024)


def _keep_image(h, a: Actor, im, name: str, meta: dict) -> dict:
    from . import templates
    tok = h.put_media(a, templates.jpeg(im), name, kind="render", meta={"type": "photo", "width": im.width, "height": im.height, **meta})
    return {"token": tok, "url": h.media_url(tok), "type": "photo", "name": name}


def _run_arg(args: dict) -> int | None:
    """The run_id an MCP call carries (studio/run_discipline.py already checked it is the person's own run)."""
    try:
        v = int(args.get("run_id") or 0)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


MAX_OUTPUTS = 10


def _slot(args: dict) -> int:
    """output: 1..10, the output of the run a render or package is for (one request, several outputs = one run)."""
    v = args.get("output")
    if v in (None, ""):
        return 1
    try:
        n = int(v)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= MAX_OUTPUTS or (isinstance(v, float) and v != n):
        raise ActionError(f"output is a whole number from 1 to {MAX_OUTPUTS}: one per thing the person asked for.")
    return n


def record_output(h, a: Actor, rid: int, slot: int, fmt: str, media: list[dict], job_id: int | None = None) -> dict | None:
    """Put a finished render on its run as output `slot` (owner, 4 Nov 2026: one request = one run, its outputs grouped in it
    and in one thread). The same output rendered again replaces it in place (and its ONE Library item); the same files
    twice change nothing. Only the person's own app-made (client) run that has not gone to Buffer or Approvals."""
    from . import run_discipline
    try:
        row = run_discipline.own_run(h.store, a, int(rid))
    except (ActionError, TypeError, ValueError):
        return None
    if row["kind"] != "client":
        return None
    res = json.loads(row["result"] or "{}") or {}
    if run_discipline.closed(res):
        return None
    media = [m for m in media if m.get("token")]
    toks = [m["token"] for m in media]
    if not toks:
        return None
    outs = res.setdefault("outputs", [])
    cur = next((o for o in outs if o.get("output") == slot), None)
    if cur and cur.get("media_tokens") == toks:
        return cur
    lib = None
    try:
        lib = h.library.register_run(a, media, fmt, int(rid), output=slot)
    except Exception:  # noqa: BLE001 - the Library never breaks a render
        pass
    entry = {"output": slot, "format": fmt, "media_tokens": toks, "media_urls": [m.get("url") or h.media_url(m["token"]) for m in media],
             "library_id": lib if lib is not None else (cur or {}).get("library_id"),
             "versions": int((cur or {}).get("versions") or 0) + 1, "at": time.time(), **({"job_id": job_id} if job_id else {})}
    label = {"reel": "Reel", "carousel": "Carousel", "photo": "Image", "text": "Post"}.get(fmt, fmt.capitalize() or "Output")
    if cur:
        outs[outs.index(cur)] = entry
        said = f"{label} {slot} updated" + (f" (was a {cur.get('format')})" if cur.get("format") not in (None, "", fmt) else "")
    else:
        outs.append(entry)
        outs.sort(key=lambda o: int(o.get("output") or 0))
        said = f"{label} {slot} ready"
    res.setdefault("thread", []).append({"role": "agent", "text": said, "at": time.time()})
    h.store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=? AND user_id=?",
              (json.dumps(res, default=str), time.time(), int(rid), a.uid))
    return entry


def _queue_video(h, a: Actor, spec: dict) -> dict:
    """One render job, never waited on here: the MCP client polls render_status (MCP tool calls must answer fast). The post
    it is for rides in spec['for_run'], not render_jobs.run_id: that column makes finish_video resume a server-AI run."""
    from . import demo_guard
    mode = h.video_mode()
    if demo_guard.is_demo(a.uid):
        raise ActionError("The sample workspace doesn't render video. Connect Buffer to edit your own videos "
                          "(split_video with sample: true shows the bundled example).", 403)
    if mode == "none":
        raise ActionError("No video renderer is configured on this server (FFmpeg locally, or the GitHub render worker).", 503)
    jid = h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                    (a.uid, None, json.dumps(spec), "queued", time.time(), time.time()))
    if mode == "local":
        def run_local(jid=jid, spec=spec):
            try:
                data, meta = h.render_job_local(spec)
                h.finish_video(jid, data, meta, run_inline=False)
            except Exception as e:  # noqa: BLE001
                h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (str(e)[:500], time.time(), jid))
        h._spawn(run_local)
        where = "this server"
    else:
        ok, why = h._dispatch_github(jid)
        if not ok:
            h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, time.time(), jid))
            raise ActionError(f"The video worker didn't start ({why}).", 502)
        where = h.backend_label(why)
    return {"job_id": jid, "status": "queued", "rendered_by": where, "poll": f"render_status(job_id={jid}) every 10-20 s",
            "spent_usd": 0}


def _kit_spec(h, a: Actor) -> dict:
    from . import brand
    kit = h.kit(a)
    return {"kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "logo": kit.get("logo") or ""}


# ------------------------------------------------------------------------------------------------ tools
_LOGO_LIKE = re.compile(r"(logo|favicon|apple-touch|/icons?[/._-]|[-_]icon\b|wordmark|brandmark|avatar|sprite|\.svg(\?|$)|\.ico(\?|$))", re.I)


def rank_images(share: str, photos: list[str]) -> tuple[list[str], list[str]]:
    """(images, logos): the page's og/share image first, then its other photos; anything that looks like a logo or icon
    (owner bug, 2 Nov 2026: the ernd cover used the logo) goes to logos, never ahead of a real picture."""
    seen, pics, logos = set(), [], []
    for u in [share] + list(photos or []):
        u = str(u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        (logos if _LOGO_LIKE.search(u.split("#")[0].split("?")[0]) else pics).append(u)
    return pics + logos, logos


def _video_first(studio, a, query: str, name: str = "", website: str = "") -> dict:
    from . import demo_guard
    from .house import MAX_UPLOAD
    from .media import video_first
    try:
        from . import appkeys
        ph = appkeys.ph_app_token(studio.house.http)
    except Exception:  # noqa: BLE001
        ph = ""
    limit = (demo_guard.DEMO_FILE_MAX if demo_guard.is_demo(a.uid) else MAX_UPLOAD) // (1024 * 1024)
    r = video_first.research_video(query, http=studio.house.http, ph_token=ph, upload_limit_mb=limit, name_hint=name, website_hint=website)
    return {"status": r["status"], "video": r["video"], "launch_video": r["launch_video"], "credit": r["credit"], "maker": r["maker"],
            "website": r["website"], "product": r["product"], "images": r["images"], "steps": r["steps"], "plan_line": r["plan_line"],
            "ask": r["ask"], "upload_limit_mb": limit, "upload_accept": r["upload"]["label"]}


def t_find_video(studio, a, args):
    from . import link_probe
    q = str(args.get("query") or "").strip()
    if not q:
        raise ActionError("Give find_video a product link or name.")
    return {**link_probe.apply(studio, a, _video_first(studio, a, q), _run_arg(args)), "spent_usd": 0}


def t_research_link(studio, a, args):
    from . import intake, netguard, reader
    from .media import find as media_find, video_first
    h = studio.house
    try:
        url = netguard.check_url(intake.clean_url(str(args.get("url") or "")))
    except netguard.FetchError as e:
        raise ActionError(f"That link isn't allowed: {e}")
    from . import link_probe
    if video_first.is_product_link(url):  # Product Hunt blocks servers: its API + the maker's site instead of the page
        vf = link_probe.apply(studio, a, _video_first(studio, a, url), _run_arg(args))
        prod = vf.get("product") or {}
        paras = [x for x in [prod.get("tagline"), prod.get("description")] if x]
        if vf.get("website"):
            try:
                site = reader.read_url(netguard.check_url(vf["website"]), h.http, "")
                paras += (site.get("paragraphs") or [])[:20]
            except Exception:  # noqa: BLE001
                pass
        if not paras and not prod:
            raise ActionError("Product Hunt blocks servers and its API didn't answer for this product. Give the product's website.")
        return {"url": url, "site": prod.get("name") or vf.get("maker"), "title": f"{prod.get('name') or vf.get('maker')}: {prod.get('tagline') or ''}".strip(": "),
                "description": prod.get("tagline") or "", "via": "product_hunt_api" if prod else "website", "paragraphs": paras[:30],
                "figures": _figures(paras, url), "images": vf.get("images") or [], "logos": [], "videos": [vf["video"]] if vf.get("video") else [],
                "video": vf, "spent_usd": 0,
                "next": ("Video found: render_reel / edit_video with video.url and credit '" + vf["credit"] + "'." if vf["status"] == "found"
                         else "No downloadable video: ask the user exactly: " + vf["ask"])}
    try:
        art = reader.read_url(url, h.http, "")
    except reader.ReadError as e:
        raise ActionError(str(e))
    paras = [p for p in art.get("paragraphs") or [] if p.strip()][: int(args.get("max_paragraphs") or 30)]
    media = [{"type": m.get("type"), "url": m.get("url"), "credit": m.get("credit") or art.get("site")} for m in art.get("media") or []]
    images, logos = rank_images(art.get("image") or "", [m["url"] for m in media if m["type"] == "photo"])
    out = {"url": art.get("url") or url, "site": art.get("site"), "title": art.get("title"), "description": art.get("description"),
           "via": art.get("via"), "paragraphs": paras, "figures": _figures(paras, art.get("url") or url),
           "images": images[:8], "logos": logos[:4], "videos": [m for m in media if m["type"] == "video"][:4], "spent_usd": 0,
           "next": "Write the slides and captions yourself from these facts (cite source_url), then render_carousel."}
    if art.get("post"):
        p = art["post"]
        out["post"] = {k: p.get(k) for k in ("author_name", "handle", "text", "date") if p.get(k)}
    if not out["videos"] and not media_find.is_video_post(url) and args.get("find_video") is True:
        try:  # a launch / product page: the video-first search, so the client knows whether a Reel is possible
            out["video"] = link_probe.apply(studio, a, _video_first(studio, a, url), _run_arg(args))
        except Exception:  # noqa: BLE001 - research never fails on the video search
            pass
    return out


def t_read_x_post(studio, a, args):
    from . import reader
    url = str(args.get("url") or "")
    if not reader.x_status(url):
        raise ActionError("That isn't a link to an X post.")
    return t_research_link(studio, a, {"url": url})


def t_find_media(studio, a, args):
    from .media import tools as mt
    try:
        from . import appkeys
        ph = appkeys.ph_app_token(studio.house.http)  # Product Hunt's launch video + gallery for a product link
    except Exception:  # noqa: BLE001
        ph = ""
    out = mt.media_find(str(args.get("query") or ""), max_results=int(args.get("max_results") or 5), http=studio.house.http, ph_token=ph)
    return {**out, "spent_usd": 0}


def t_scrape_brand_assets(studio, a, args):
    from urllib.parse import urlparse

    import httpx

    from . import brand, drafter, netguard, sources
    url = str(args.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).hostname or ""
    if not host or not drafter._public(host):
        raise ActionError("Give the brand's public website address.")
    try:
        r = netguard.safe_get(url, http=studio.house.http, headers=sources.UA, limit=3_000_000)
    except (netguard.FetchError, httpx.HTTPError) as e:
        raise ActionError(f"Couldn't open {host} ({e.__class__.__name__}).")
    info = brand.kit_from_html(str(r.url), r.text)
    keep = {k: info[k] for k in info if k not in ("html",) and isinstance(info[k], (str, int, float, list, dict, bool))}
    try:
        from . import assets  # official sources + images for one company (when this build has it)
        keep["official"] = assets.site(str(r.url), http=studio.house.http)
    except Exception:  # noqa: BLE001
        pass
    return {"url": str(r.url), **keep, "saved": False, "next": "update_brand_kit with the values you pick", "spent_usd": 0}


def _carousel_editorial(h, a, slides, ratio, follow_line, keep_case=()):
    from . import brand_routes, editorial  # editorial_story (the 'ernd' carousel); ImportError -> text_slides fallback
    from PIL import Image
    b = brand_routes.attach(h)[0].brand_obj(a)
    cov = slides[0]
    hero = _img_bytes(h, cov.get("image") or "")
    hero_im = Image.open(io.BytesIO(hero)).convert("RGB") if hero else None
    band, sl = None, []
    for s in slides[1:]:
        img = _img_bytes(h, s.get("image") or "") if band is None and s.get("image") else None
        if img:
            band = Image.open(io.BytesIO(img)).convert("RGB")
        sl.append({"eyebrow": s.get("eyebrow", ""), "blocks": [{"head": s.get("headline", ""), "body": s.get("body", "")}],
                   "accent": [s["accent_word"]] if s.get("accent_word") else [], "note": s.get("footnote", ""),
                   "image": bool(s.get("image")) and bool(img)})
    spec = {"eyebrow": cov.get("eyebrow", ""), "headline": cov.get("headline", ""), "dek": cov.get("body", ""),
            "accent": [cov["accent_word"]] if cov.get("accent_word") else [], "slides": sl, "follow_line": follow_line or "",
            "keep_case": [str(w) for w in keep_case or [] if isinstance(w, str)][:20]}
    problems = list(editorial.check_spec(spec)) if hasattr(editorial, "check_spec") else []
    ims, fit = editorial.render(b, spec, hero=hero_im, band=band, ratio=ratio)
    return ims, problems + list(getattr(fit, "problems", []) or []), "editorial_story"


def _carousel_text(h, a, slides, ratio):
    from . import brand_routes, templates
    b = brand_routes.attach(h)[0].brand_obj(a)
    cov = slides[0]
    data = {"title": cov.get("headline", ""), "accent_words": [cov["accent_word"]] if cov.get("accent_word") else None,
            "sub": cov.get("body", ""), "hero": _img_bytes(h, cov.get("image") or ""),
            "slides": [{"title": s.get("headline", ""), "text": " ".join(x for x in (s.get("body", ""), s.get("footnote", "")) if x),
                        "image": _img_bytes(h, s.get("image") or "")} for s in slides[1:]]}
    return templates.slides("text_slides", data, b, ratio, max_slides=10), [], "text_slides"


def t_render_carousel(studio, a, args):
    h = studio.house
    studio._guard_write(a, "render_carousel")
    slot = _slot(args)
    slides = [s for s in args.get("slides") or [] if isinstance(s, dict) and (s.get("headline") or s.get("body"))]
    if len(slides) < 2:
        raise ActionError("Give at least a cover and one content slide (slides[0] is the cover).")
    ratio = args.get("ratio") if args.get("ratio") in ("4:5", "1:1", "9:16") else "4:5"
    tpl = str(args.get("template") or "editorial")
    ims, problems, used = None, [], ""
    if tpl in ("editorial", "editorial_story"):
        try:
            ims, problems, used = _carousel_editorial(h, a, slides, ratio, args.get("follow_line"),
                                                          args.get("keep_case") if isinstance(args.get("keep_case"), list) else ())
        except ImportError:
            ims = None
    if ims is None:
        ims, problems, used = _carousel_text(h, a, slides, ratio)
    rid = _run_arg(args)
    extra = {"template": used, "ratio": ratio, **({"run_id": rid} if rid else {})}
    media = [_keep_image(h, a, im, f"slide{i + 1}.jpg", extra) for i, im in enumerate(ims)]
    lib = None
    if rid:  # one Library item per post output: this render replaces the output's earlier one (owner, 4 Nov 2026)
        got = record_output(h, a, rid, slot, "carousel", media)
        lib = (got or {}).get("library_id")
    # without run_id the slides stay run media until create_package stages them (no "Carousel (N slides)" per retry)
    return {"template": used, "ratio": ratio, "slides": len(media), "media_urls": [m["url"] for m in media],
            "media_tokens": [m["token"] for m in media], "library_id": lib, "run_id": rid, **({"output": slot} if rid else {}),
            "fit_problems": problems, "spent_usd": 0,
            "next": ("Fix any fit_problems by writing shorter text and render again with the same run_id (it replaces these "
                     "slides); then brand_check your caption and create_package(run_id, captions, media_urls)."
                     if rid else "Start the post with start_post and pass its run_id next time. Fix any fit_problems by writing "
                     "shorter text and render again; then brand_check your caption and create_package(captions, media_urls).")}


def t_hero_edit(studio, a, args):
    from PIL import Image, ImageEnhance, ImageOps
    h = studio.house
    studio._guard_write(a, "hero_edit")
    data = _img_bytes(h, str(args.get("image_url") or ""))
    if not data:
        raise ActionError("Couldn't read that image (a public https link, up to 8 MB).")
    W, H = {"4:5": (1080, 1350), "1:1": (1080, 1080), "9:16": (1080, 1920)}.get(args.get("ratio") or "4:5", (1080, 1350))
    scene = Image.open(io.BytesIO(data)).convert("RGB")
    prod = _img_bytes(h, str(args.get("product_url") or "")) if args.get("product_url") else None
    how = "basic"
    try:
        from . import hero_edit as he  # the full premium edit when this build has it
        product = Image.open(io.BytesIO(prod)).convert("RGBA") if prod else None
        out = he.premium_hero(W, H, scene=scene, product=product)
        im, report = (out if isinstance(out, tuple) else (out, {}))
        how = "premium_hero"
    except ImportError:
        im = ImageOps.fit(scene, (W, H), method=Image.LANCZOS, centering=(0.5, 0.42))
        im = ImageEnhance.Color(im).enhance(0.85)
        im = ImageEnhance.Brightness(im).enhance(0.86)
        report = {"steps": ["centre crop to the frame", "desaturate 15%", "darken 14%"]}
    rid = _run_arg(args)
    m = _keep_image(h, a, im.convert("RGB"), "hero.jpg", {"edit": how, **({"run_id": rid} if rid else {})})
    return {"url": m["url"], "media_token": m["token"], "edit": how, "report": report, "run_id": rid, "spent_usd": 0}


def _rights(args: dict, src_url: str = "") -> dict:
    """rights: own | permission | news_credit, one parameter instead of a back-and-forth (owner, 2 Nov 2026). Without it the
    older owns / rights_confirmed / credit fields still work. news_credit always ends with a credit line: the one given, or
    the source's site."""
    r = str(args.get("rights") or "").strip().lower()
    credit = str(args.get("credit") or "").strip()[:160]
    if not r:
        return {"rights": "", "owns": bool(args.get("owns")), "rights_confirmed": bool(args.get("rights_confirmed", True)),
                "credit": credit}
    if r == "own":
        return {"rights": r, "owns": True, "rights_confirmed": True, "credit": credit}
    if r == "permission":
        return {"rights": r, "owns": False, "rights_confirmed": True, "credit": credit}
    if r == "news_credit":
        if not credit:
            from urllib.parse import urlparse
            host = (urlparse(str(src_url or "")).hostname or "").removeprefix("www.")
            if host and not host.endswith("onrender.com"):
                credit = f"Video: {host}"
        if not credit:
            raise ActionError("rights: news_credit needs a credit line, e.g. credit: 'Video: OpenAI on YouTube' (or give source_url).")
        return {"rights": r, "owns": False, "rights_confirmed": True, "credit": credit}
    raise ActionError("rights is one of own | permission | news_credit.")


def credit_line(credit: str) -> str:
    c = credit.strip()
    return c if c.lower().startswith(("video:", "source:", "credit:", "via ", "photo:", "image:")) else f"Credit: {c}"


def _captions_mode(v) -> str:
    """edit_video's captions argument -> the worker's mode (owner bug 2 Nov 2026: a captioned clip got captioned again)."""
    if v is False or v == "off":
        return "off"
    if v in ("replace", "on"):
        return v
    return "auto"  # true, missing or "auto": caption it unless captions are already burned in


def _video_spec(h, a, args, tok, meta) -> dict:
    from .media import styles as media_styles
    style = str(args.get("theme") or args.get("caption_style") or "")
    if not style:  # no theme named: the active brand kit's caption style
        from . import brand_kits
        style = brand_kits.caption_style_for(h, a)
    sid = media_styles.resolve(style)
    if not media_styles.known(sid):
        raise ActionError(f"Unknown theme/style {style}. list_themes gives the ids.")
    dur = float(meta.get("duration") or 0)
    s = args.get("start_s")
    e = args.get("end_s")
    if args.get("cut_first_s") not in (None, ""):
        s = float(args["cut_first_s"])
    clip = None
    if s is not None or e is not None:
        s = max(0.0, float(s or 0))
        e = float(e) if e not in (None, "") else (dur or s + 600)
        if e <= s + 1:
            raise ActionError("The cut must leave at least one second (end_s after start_s).")
        clip = [round(s, 2), round(e, 2)]
    rg = _rights(args, str(args.get("source_url") or args.get("url") or ""))  # own | permission | news_credit
    spec = {"kind": "media_render", "style": sid, "ratio": args.get("ratio") if args.get("ratio") in ("9:16", "4:5", "1:1") else "9:16",
            "hook": str(args.get("hook") or "")[:120], "facts": [], "credit": rg["credit"], "names": [],  # credit: the lower-third
            "music": args.get("music") or "auto", "official": False,
            "rights_confirmed": rg["rights_confirmed"], "owns": bool(rg["owns"]), "source": tok, "candidate": None, "slides": [],
            "captions": args.get("captions") not in (False, "off"),
            "captions_mode": _captions_mode(args.get("captions")),
            "brand_colors": True, **_kit_spec(h, a), "via": SOURCE}
    if clip:
        spec["clip"] = clip
    cv = args.get("cover")
    if cv:  # branded cover variants from the worker (studio/media/covers.py), the chosen one as the opening frames
        cv = cv if isinstance(cv, dict) else {}
        spec["covers"] = True
        spec["cover_hook"] = str(cv.get("title") or args.get("hook") or "")[:120]
        spec["cover_title_given"] = bool(str(cv.get("title") or "").strip())  # keep the client's words on the cover
        try:
            spec["cover_index"] = max(0, min(2, int(cv.get("index") or 0)))
        except (TypeError, ValueError):
            spec["cover_index"] = 0
        if cv.get("opening_frame") is False:
            spec["cover_frame"] = False
        if cv.get("image_url"):
            raw = _img_bytes(h, str(cv["image_url"]))
            if not raw:
                raise ActionError("Couldn't read cover.image_url (a public https image).")
            spec["cover_image"] = h.put_media(a, raw, "cover-hero.jpg", kind="render", meta={"type": "photo", "role": "cover_image"})
    return spec


def _link_rights(studio, a: Actor, args: dict, tool: str) -> dict | None:
    """A video LINK an AI app wants fetched (not a HuhQueue upload): the same rights step every tool uses
    (consent.rights_for_link). None = go ahead (args then carries _rights); a dict = the needs_user_approval answer."""
    url = str(args.get("url") or "").strip()
    if a.kind != "agent" or not url or args.get("media_token") or args.get("library_id") not in (None, ""):
        return None
    if url.startswith(studio.house.s.public_url.rstrip("/") + "/m/"):
        return None
    from . import consent
    g = consent.rights_for_link(studio, a, url, approval_id=args.get("approval_id"), tool=tool, run_id=_run_arg(args))
    if g.get("needs_user_approval"):
        return {**g, **({"run_id": _run_arg(args)} if _run_arg(args) else {})}
    args["_rights"] = g["rights"]
    return None


def _platform_job(studio, a, args, tool: str) -> dict:
    """A YouTube / Vimeo / X video post: the worker fetches it itself (single-extractor yt-dlp, the probed path), it is
    never imported here as if the page were a file."""
    from .media import find
    h = studio.house
    url = str(args["url"]).strip()
    slot = _slot(args)
    rg = _rights(args, url)
    spec = _video_spec(h, a, {**args, "url": url}, "", {"duration": 0})
    spec.update(source="", candidate={"url": url, "platform": find.platform_of(url), "kind": "video", "credit": rg["credit"],
                                      "official": rg["rights"] == "own"})
    if _run_arg(args):
        spec["for_run"], spec["for_output"] = _run_arg(args), slot
    return _queue_video(h, a, spec)


def t_edit_video(studio, a, args):
    from .media import find
    h = studio.house
    studio._guard_write(a, "edit_video")
    held = _link_rights(studio, a, args, "edit_video")
    if held:
        return held
    if args.get("url") and find.platform_of(str(args["url"])) and not args.get("media_token") and args.get("library_id") in (None, ""):
        out = _platform_job(studio, a, args, "edit_video")
        return {**out, **({"rights": args["_rights"]} if args.get("_rights") else {})}
    tok, meta = _unfinished(h, a, *_video_source(h, a, args), args)
    if meta.get("type") not in (None, "video"):
        raise ActionError("That media is not a video.")
    slot = _slot(args)
    spec = _video_spec(h, a, args, tok, meta)
    if _run_arg(args):
        spec["for_run"], spec["for_output"] = _run_arg(args), slot
    out = _queue_video(h, a, spec)
    if meta.get("_from_finished"):
        out = {**out, "note": "That video was already a finished reel, so this edit starts again from its original upload "
                              "(one cover, one end card)."}
    return {**out, "edit": {k: spec.get(k) for k in ("clip", "ratio", "style", "captions", "captions_mode", "music", "covers", "cover_hook")
                            if spec.get(k) is not None}, **({"rights": args["_rights"]} if args.get("_rights") else {})}


def t_render_reel(studio, a, args):
    h = studio.house
    studio._guard_write(a, "render_reel")
    if args.get("images"):
        from . import brand
        slot = _slot(args)
        toks = []
        base = h.s.public_url.rstrip("/") + "/m/"
        for u in list(args["images"])[:10]:
            u = str(u)
            toks.append(u[len(base):] if u.startswith(base) else h.put_media(a, _img_bytes(h, u) or b"", "slide.jpg", kind="render",
                                                                              meta={"type": "photo"}))
        kit = h.kit(a)
        spec = {"kind": "slideshow", "kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "params": brand.visual_params(kit, h.rules(a)),
                "slides": toks, "logo": kit.get("logo") or "", "ratio": args.get("ratio") or "9:16", "via": SOURCE}
        if _run_arg(args):
            spec["for_run"], spec["for_output"] = _run_arg(args), slot
        return _queue_video(h, a, spec)
    return t_edit_video(studio, a, args)


def t_split_video(studio, a, args):
    h = studio.house
    if args.get("sample"):
        return h.takes.start_sample(a, {"channel_ids": args.get("channel_ids")})
    body = {k: args.get(k) for k in ("owns", "rights_confirmed", "credit", "name", "channel_ids") if args.get(k) is not None}
    if args.get("rights"):
        rg = _rights({**args, "credit": args.get("credit") or "x"} if args.get("rights") == "news_credit" and not args.get("credit")
                     and args.get("url") else args, str(args.get("url") or ""))
        body.update(owns=rg["owns"], rights_confirmed=rg["rights_confirmed"])
        if args.get("credit") or rg["rights"] != "news_credit":  # news_credit from a link: One take names the platform itself
            body["credit"] = rg["credit"]
    if args.get("n"):
        body["count"] = int(args["n"])
    if args.get("media_token") or args.get("library_id") not in (None, ""):
        body["media_token"] = _video_source(h, a, args)[0]
    elif args.get("url"):
        body["url"] = str(args["url"])
    if args.get("approval_id") not in (None, ""):
        body["approval_id"] = args["approval_id"]
    t = h.takes.start(a, {**body, "_tool": "split_video"})
    if t.get("needs_user_approval"):  # a video link: the person confirms the rights in the Inbox first (consent.link_gate)
        return t
    return {"take_id": t["id"], "status": t.get("status"), "poll": f"render_status(take_id={t['id']}) every 20-30 s",
            "spent_usd": 0, **({"run_id": _run_arg(args)} if _run_arg(args) else {})}


def t_render_status(studio, a, args):
    h = studio.house
    if args.get("take_id"):
        t = h.takes.get(a, int(args["take_id"]))
        clips = [{"id": c.get("id"), "status": c.get("status"), "start": c.get("start"), "end": c.get("end"), "hook": c.get("hook"),
                  "download_url": c.get("url"), "cover_url": c.get("cover_url")} for c in t.get("clips") or []]
        st = t.get("status")
        return {"take_id": t["id"], "status": "done" if st == "ready" else st, "steps": [{k: s.get(k) for k in ("label", "status", "detail")}
                                                                                     for s in t.get("steps") or []],
                "clips": clips, "download_urls": [c["download_url"] for c in clips if c.get("download_url")]}
    jid = int(args.get("job_id") or 0)
    rows = h.store.q("SELECT id, status, error, media_token, spec, created, updated FROM render_jobs WHERE id=? AND user_id=?", (jid, a.uid))
    if not rows:
        raise ActionError("No such video job.", 404)
    r = rows[0]
    st = r["status"]
    out = {"job_id": jid, "status": {"queued": "queued", "running": "running", "done": "done", "failed": "failed"}.get(st, st),
           "seconds": round(time.time() - float(r["created"] or time.time()))}
    if st == "done" and r["media_token"]:
        out["download_url"] = h.media_url(r["media_token"])
        out["media_token"] = r["media_token"]
        try:
            jspec = json.loads(r["spec"] or "{}")
        except ValueError:
            jspec = {}
        if jspec.get("for_run"):  # the render is an output of its post (also done in finish_video; same files = no change)
            got = record_output(h, a, int(jspec["for_run"]), int(jspec.get("for_output") or 1), "reel",
                                [{"token": r["media_token"], "type": "video", "url": out["download_url"]}], job_id=jid)
            out.update(run_id=int(jspec["for_run"]), output=int(jspec.get("for_output") or 1))
            if got and got.get("library_id"):
                out["library_id"] = got["library_id"]
        m = h.media_meta(a, r["media_token"]) or {}
        out["meta"] = {k: m.get(k) for k in ("width", "height", "duration", "ratio", "style", "captions", "clip", "captions_note",
                                             "engine", "model", "model_licence")
                       if m.get(k) not in (None, "")}
        if m.get("captions_note"):
            out["note"] = m["captions_note"]  # e.g. it already had captions, so none were added
        if m.get("covers"):  # edit_video(cover=...): the 3 branded variants; the chosen one opens the Reel
            ratio = m.get("ratio") or "9:16"
            out["covers"] = [{"variant": c.get("variant"), "layout": c.get("layout"), "frame_t": c.get("frame_t"),
                              "urls": {rr: h.media_url(t) for rr, t in (c.get("files") or {}).items()}} for c in m["covers"]]
            try:
                idx = int(json.loads(r["spec"] or "{}").get("cover_index") or 0)
            except (ValueError, TypeError):
                idx = 0
            pick = out["covers"][min(idx, len(out["covers"]) - 1)]["urls"]
            out["cover_url"] = pick.get(ratio) or next(iter(pick.values()), "")
            out["cover"] = {"url": out["cover_url"], "frame_s": 0}
    if st == "failed":
        from .mcp_flow import upload_steps
        out["error"] = (r["error"] or "")[:300]
        try:
            fspec = json.loads(r["spec"] or "{}")
        except ValueError:
            fspec = {}
        out.update(code="render_failed", produced="nothing (a failed video is never replaced by slides)",
                   next_steps=upload_steps(fspec.get("for_run"), fspec.get("for_output")))
    if st == "queued":
        out["next"] = "Still rendering: call render_status again in 15 s."
    return out


VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")
CM_KEYS = {"output", "media_urls", "cover", "format"}
FORMATS = ("carousel", "reel", "photo", "text")


def _is_video_url(h, a: Actor, u: str) -> bool:
    if str(u).lower().split("?")[0].endswith(VIDEO_EXT):
        return True
    tok = h.media_token_of(str(u)) if hasattr(h, "media_token_of") else ""
    return bool(tok) and (h.media_meta(a, tok) or {}).get("type") == "video"


def _channel_media_arg(args: dict) -> dict:
    """channel_media: {channel id or service: {output, media_urls, cover, format}}; every field type-checked (400)."""
    cm = args.get("channel_media")
    if cm in (None, "", {}):
        return {}
    if not isinstance(cm, dict):
        raise ActionError("channel_media is {channel id or service: {output, media_urls, cover, format}}.")
    if len(cm) > 20:
        raise ActionError("channel_media takes up to 20 channels.")
    out = {}
    for k, v in cm.items():
        if not isinstance(v, dict):
            raise ActionError(f"channel_media['{str(k)[:40]}'] is an object: {{output, media_urls, cover, format}}.")
        bad = set(v) - CM_KEYS
        if bad:
            raise ActionError(f"channel_media['{str(k)[:40]}'] takes only output, media_urls, cover and format.")
        if "media_urls" in v and (not isinstance(v["media_urls"], list) or len(v["media_urls"]) > 10
                                  or not all(isinstance(u, str) for u in v["media_urls"])):
            raise ActionError("channel_media[...].media_urls is a list of up to 10 URLs.")
        if v.get("format") not in (None, *FORMATS):
            raise ActionError("channel_media[...].format is one of carousel | reel | photo | text.")
        if v.get("cover") is not None and not isinstance(v["cover"], dict):
            raise ActionError("channel_media[...].cover is {url, frame_s}.")
        if "output" in v:
            _slot({"output": v["output"]})
        out[str(k)] = v
    return out


def _output_media(h, a: Actor, rid: int | None, slot: int) -> tuple[list[str], str]:
    """(media_urls, format) of output `slot` of this run (404 when it hasn't been rendered yet)."""
    from . import run_discipline
    if not rid:
        raise ActionError("output needs run_id (the post the render belongs to).")
    row = run_discipline.own_run(h.store, a, rid)
    hit = next((o for o in (json.loads(row["result"] or "{}") or {}).get("outputs") or [] if o.get("output") == slot), None)
    if not hit:
        raise ActionError(f"Run {rid} has no output {slot} yet: render it first (output: {slot}).", 404)
    return [str(u) for u in hit.get("media_urls") or []][:10], (hit.get("format") if hit.get("format") in FORMATS else "")


def _cover_of(cover: Any) -> tuple[str, int | None]:
    cover = cover if isinstance(cover, dict) else {}
    url = str(cover.get("url") or "")[:500]
    if url and not url.startswith("https://"):
        raise ActionError("cover.url must be an https image URL (render_status gives cover_url).")
    ms = None
    if cover:
        try:
            ms = max(0, int(round(float(cover.get("frame_s") or 0) * 1000)))
        except (TypeError, ValueError):
            raise ActionError("cover.frame_s is a number of seconds.")
    return url, ms


def t_create_package(studio, a, args):
    """Stage captions (+ media) per channel. With run_id only the channels named change; the others keep their packages
    (owner report 6 Nov 2026: staging one video per channel wiped the previous channel, so the app split one post into
    three runs). channel_media gives each channel its own output / media / cover / format."""
    from .mcp_flow import StepError, upload_steps
    h = studio.house
    studio._guard_write(a, "create_package")
    caps = args.get("captions") if isinstance(args.get("captions"), dict) else {}
    if not caps:
        raise ActionError("captions is {channel_id or service: text} with the caption you wrote for each channel.")
    if args.get("replace_all") not in (None, True, False):
        raise ActionError("replace_all is true or false.")
    cm = _channel_media_arg(args)
    chans = [c for c in studio.channels(a) if not c.get("isDisconnected")]
    slot = _slot(args)
    rid = _run_arg(args)
    media_urls = [str(u) for u in args.get("media_urls") or []][:10]
    shared_fmt = args.get("format") if args.get("format") in FORMATS else ""
    if not media_urls and args.get("output") not in (None, "") and rid:  # stage that output's media
        media_urls, ofmt = _output_media(h, a, rid, slot)
        shared_fmt = shared_fmt or ofmt
    credit = ""
    if args.get("rights"):
        rg = _rights(args, str(args.get("source_url") or ""))
        credit = rg["credit"] if rg["rights"] == "news_credit" else ""
    if credit:  # news_credit: the credit line goes in every caption (once)
        line = credit_line(credit)
        caps = {k: (v if credit.lower() in str(v).lower() else f"{str(v).rstrip()}\n\n{line}") for k, v in caps.items()}
    cover_url, thumb_ms = _cover_of(args.get("cover"))
    pk, unknown, used_cm = [], [], set()
    for key, text in caps.items():
        hits = [c for c in chans if c["id"] == key] or [c for c in chans if c.get("service") == key]
        if not hits:
            unknown.append(key)
        for c in hits:
            ck = c["id"] if c["id"] in cm else (c.get("service") if c.get("service") in cm else None)
            entry = cm.get(ck) if ck else None
            urls, fmt, c_slot, own = media_urls, shared_fmt, slot, False
            c_cover, c_thumb = cover_url, thumb_ms
            if entry is not None:
                used_cm.add(ck)
                own = True
                if "media_urls" in entry:
                    urls, fmt = [str(u) for u in entry["media_urls"]][:10], ""
                elif entry.get("output") not in (None, ""):
                    c_slot = _slot({"output": entry["output"]})
                    urls, fmt = _output_media(h, a, rid, c_slot)
                fmt = entry.get("format") or fmt or (shared_fmt if "media_urls" not in entry and entry.get("output") in (None, "") else "")
                if entry.get("cover") is not None:
                    c_cover, c_thumb = _cover_of(entry["cover"])
            vids = [u for u in urls if _is_video_url(h, a, u)]
            fmt = fmt or ("reel" if vids else "carousel" if len(urls) > 1 else "photo" if urls else "text")
            if fmt == "reel" and not vids:  # never a slideshow or a photo in place of the video that was asked for
                raise StepError(f"{c.get('displayName') or c.get('name') or key}: format 'reel' needs a video, and this "
                                "channel's media has none. Nothing was staged. Next: render the video (render_reel, output: n) "
                                "or get the person's file with create_send_code.", 400, code="reel_needs_video",
                                channel=c["id"], next_steps=upload_steps(rid, c_slot if entry else None))
            pk.append({"channel_id": c["id"], "service": c.get("service"), "channel": c.get("displayName") or c.get("name"),
                       "channel_name": c.get("displayName") or c.get("name"),  # every package reader (Planner, Make a post) uses this
                       "text": str(text)[:5000], "media_urls": urls, "format": fmt, "status": "ready",
                       "media": [{"url": u, "type": "video" if _is_video_url(h, a, u) else "photo"} for u in urls],
                       "check": h.check(a, str(text))})
            if args.get("output") not in (None, "") or (entry and entry.get("output") not in (None, "")):
                pk[-1]["output"] = c_slot
            if own:
                pk[-1]["_own_media"] = True
            if c_cover:
                pk[-1]["cover_url"] = c_cover
            if c_thumb is not None and fmt == "reel" and c.get("service") in ("instagram", "tiktok", "pinterest"):
                pk[-1]["options"] = {**(pk[-1].get("options") or {}), "thumbnail_offset_ms": c_thumb}
    stray = [k for k in cm if k not in used_cm]
    if stray:
        raise ActionError(f"channel_media names {', '.join(stray)[:120]} but captions has no caption for it: give each channel "
                          "in channel_media a caption too.")
    if not pk:
        raise ActionError(f"No connected channel matches {', '.join(unknown)}. list_channels gives ids and services.")
    _yt_options(pk, args)
    chans_in = [{"id": p["channel_id"], "service": p["service"], "name": p["channel"]} for p in pk]
    shared = [p for p in pk if not p.get("_own_media")]  # only these record the run's output below (before the flag is dropped)
    updated, versions, kept = False, 0, []
    if rid:  # one request = one run: update that post in place, keeping what it had for Undo (owner, 4 Nov 2026)
        rid, versions, kept = _update_package_run(h, a, rid, args, pk, chans_in)
        updated = True
    else:
        for p in pk:
            p.pop("_own_media", None)
        inp = {"prompt": str(args.get("title") or "Post from your AI app")[:200], "via": SOURCE, "channels": chans_in}
        res = {"packages": pk, "media": pk[0]["media"], "story": {"headline": str(args.get("title") or "")[:120]}, "brain": "client"}
        rid = h.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated,agent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                        (a.uid, "client", json.dumps(inp), "ready", json.dumps([]), json.dumps(res), time.time(), time.time(),
                         a.agent["id"] if a.kind == "agent" and a.agent else None))
    _library_for_run(h, a, rid, shared, slot)
    return {"run_id": rid, "updated": updated, "versions_kept": versions, **({"output": slot} if args.get("output") not in (None, "") else {}),
            "packages": [{k: p.get(k) for k in ("channel_id", "service", "format", "status", "output") if p.get(k) is not None}
                         | {"brand_check": p["check"], "media_urls": p["media_urls"]} for p in pk],
            "kept_channels": kept, "unknown_channels": unknown, "spent_usd": 0,
            "next": "Show the person. Changes: render again with this run_id and call create_package(run_id) for the channels "
                    "that change (the others keep their packages; undo_package_media goes back). On their yes: "
                    "schedule_package(run_id, channel_id, mode='draft')."}


MAX_PACKAGE_VERSIONS = 6


def _update_package_run(h, a: Actor, rid: int, args: dict, pk: list[dict], chans_in: list[dict]) -> tuple[int, int, list[str]]:
    """create_package(run_id): the new packages replace THOSE channels' packages in place; every other channel keeps its
    package (replace_all: true drops them). The previous set goes to result.package_versions (newest last, at most 6) and
    each package says what Undo goes back to. Returns (run_id, versions kept, channel ids kept as they were)."""
    from . import run_discipline
    row = run_discipline.own_run(h.store, a, rid)  # 404 for someone else's run (checked here too: every surface)
    if row["kind"] != "client":
        raise ActionError("That run was made by HuhQueue's own agent; change it with its own tools (reply_post / "
                          "replace_channel_media), or start your own post with start_post.", 409)
    res = json.loads(row["result"] or "{}") or {}
    inp = json.loads(row["input"] or "{}") or {}
    if run_discipline.closed(res):
        raise ActionError("That post was already sent to Buffer or is waiting in Approvals, so it isn't changed here. "
                          "update_package_draft updates its Buffer draft; or ask the person before starting a new post.", 409)
    old = res.get("packages") or []
    vs = res.setdefault("package_versions", [])
    if old:
        vs.append({"at": time.time(), "packages": json.loads(json.dumps(old, default=str)), "media": res.get("media") or [],
                   "label": "Back to the previous version"})
        del vs[:-MAX_PACKAGE_VERSIONS]
    for p in pk:
        p.pop("_own_media", None)
        if vs:
            p["undo"] = {"label": "Back to the previous version", "versions": len(vs)}
    new_ids = {p["channel_id"] for p in pk}
    keep = [] if args.get("replace_all") is True else [p for p in old if p.get("channel_id") not in new_ids]
    by_id = {p["channel_id"]: p for p in pk}
    merged = [by_id.pop(p["channel_id"]) if p.get("channel_id") in by_id else p for p in old
              if p.get("channel_id") in new_ids or p in keep] + list(by_id.values())
    res["packages"] = merged
    res["media"] = merged[0].get("media") or []
    if args.get("title"):
        res["story"] = {**(res.get("story") or {}), "headline": str(args["title"])[:120]}
        inp["prompt"] = str(args["title"])[:200]
    old_in = {c.get("id"): c for c in inp.get("channels") or [] if isinstance(c, dict)}
    for c in chans_in:
        old_in[c["id"]] = c
    inp["channels"] = [old_in[p["channel_id"]] for p in merged if p.get("channel_id") in old_in]
    h.store.x("UPDATE agent_runs SET input=?, result=?, status='ready', updated=? WHERE id=? AND user_id=?",
              (json.dumps(inp), json.dumps(res, default=str), time.time(), rid, a.uid))
    return rid, len(vs), [p["channel_id"] for p in keep]


def client_channel_media(studio, a: Actor, rid: int, channel: str, token: str) -> dict:
    """replace_channel_media on a start_post / create_package run (and every other non-agent post run): that channel's
    package takes the person's file (media_token); the other channels are untouched, the previous set is kept for Undo and
    the brand check runs again (owner report 6 Nov 2026: it answered "made before the agent" on start_post runs)."""
    from . import run_discipline
    from .house import VIDEO_ONLY
    from .mcp_flow import StepError, upload_steps
    h = studio.house
    studio._guard_write(a, "replace_channel_media")
    row = run_discipline.own_run(h.store, a, rid)
    if row["kind"] == "take":
        raise StepError("That is a One take run: change a clip with adjust_clip, nothing here.", 409, code="wrong_run_type",
                        next_steps=["list_clips(take_id)", "adjust_clip(take_id, clip_id, ...)"])
    res = json.loads(row["result"] or "{}") or {}
    if run_discipline.closed(res):
        raise ActionError("That post was already sent to Buffer or is waiting in Approvals, so it isn't changed here. "
                          "update_package_draft updates its Buffer draft.", 409)
    pk = res.get("packages") or []
    ch = str(channel or "").strip()
    hit = next((p for p in pk if p.get("channel_id") == ch), None) or next((p for p in pk if p.get("service") == ch), None)
    if not hit:
        raise StepError("That channel isn't staged in this post yet.", 404, code="channel_not_staged",
                        missing_step=f"create_package(run_id={rid}, captions={{'{ch[:40]}': '<caption>'}}, "
                                     f"channel_media={{'{ch[:40]}': {{'output': 1}}}})")
    tok = str(token or "").strip()
    if not tok:
        raise StepError("Give media_token (a render's media_token from render_status, an upload from list_recent_uploads, or "
                        "a Library item): a post you staged yourself has no HuhQueue agent to regenerate it.", 400,
                        code="media_token_required", next_steps=upload_steps(rid))
    meta = h.media_meta(a, tok)
    if meta is None:
        raise ActionError("Unknown file. Upload it or pick it from your Library first.", 404)
    typ = "video" if meta.get("type") == "video" else "photo"
    if hit.get("service") in VIDEO_ONLY and typ != "video":
        raise ActionError(f"{str(hit.get('service')).title()} needs a video.")
    vs = res.setdefault("package_versions", [])
    vs.append({"at": time.time(), "packages": json.loads(json.dumps(pk, default=str)), "media": res.get("media") or [],
               "label": "Back to the previous version"})
    del vs[:-MAX_PACKAGE_VERSIONS]
    url = h.media_url(tok)
    hit.update(media=[{"token": tok, "url": url, "type": typ, "name": "your file", "role": "original"}], media_urls=[url],
               format="reel" if typ == "video" else "photo", check=h.check(a, str(hit.get("text") or ""), tok),
               undo={"label": "Back to the previous version", "versions": len(vs)})
    hit.pop("buffer_draft_stale", None)
    if pk and pk[0] is hit:
        res["media"] = hit["media"]
    res.setdefault("thread", []).append({"role": "agent", "text": f"{hit.get('channel') or hit.get('service')}: media replaced", "at": time.time()})
    h.store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=? AND user_id=?", (json.dumps(res, default=str), time.time(), rid, a.uid))
    return {"run_id": rid, "channel_id": hit["channel_id"], "service": hit.get("service"), "format": hit["format"],
            "media_urls": hit["media_urls"], "brand_check": hit["check"], "versions_kept": len(vs), "spent_usd": 0,
            "packages": [{k: p.get(k) for k in ("channel_id", "service", "format", "status")} for p in pk],
            "next": "Show the person. undo_package_media goes back; schedule_package(run_id, channel_id, mode='draft') on their yes."}


YT_KEYS = ("title", "categoryId", "madeForKids")


def _yt_options(pk: list[dict], args: dict) -> None:
    """youtube: {title, categoryId, madeForKids} -> the YouTube packages' options (title, category_id, made_for_kids), the
    fields preflight asks for (owner report 6 Nov 2026: nothing could set the category, so preflight blocked YouTube)."""
    yt = args.get("youtube")
    if yt in (None, {}):
        return
    if not isinstance(yt, dict) or set(yt) - set(YT_KEYS):
        raise ActionError("youtube is {title, categoryId, madeForKids}.")
    opts = youtube_options(yt)
    for p in pk:
        if p.get("service") == "youtube":
            p["options"] = {**(p.get("options") or {}), **opts}


from .preflight import youtube_options  # noqa: E402,F401  worker-safe home (actions.autofix uses it too)


def _library_for_run(h, a: Actor, rid: int, pk: list[dict], slot: int = 1) -> None:
    """The staged post's media is the run's output (its ONE Library item, replaced in place when the post changes). Only
    the packages that use the shared media (media_urls / output) are recorded here: a channel_media entry with its own
    output is already that output (its render recorded it), and one with its own media_urls never overwrites a slot."""
    media = []
    for m in (pk[0].get("media") or []) if pk else []:
        tok = h.media_token_of(m.get("url") or "")
        if tok:
            media.append({"token": tok, "type": m.get("type") or "photo", "url": m.get("url")})
    if not media:
        return
    if record_output(h, a, rid, slot, pk[0].get("format") or "", media) is None:
        try:  # not an app-made run (or already sent): the Library still keeps one item for it
            h.library.register_run(a, media, pk[0].get("format") or "", rid, output=slot)
        except Exception:  # noqa: BLE001 - the Library never breaks staging
            pass


# update_brand_kit is ONE tool, in studio/brand_kits.py (B1, 2 Nov 2026: two tools had the name; kit_id omitted = the
# active kit). This helper stays for in-process callers.
def t_update_brand_kit(studio, a, args):
    patch = {k: args[k] for k in ("name", "handle", "website", "font", "voice", "colors", "end_card") if k in args}
    if not patch:
        raise ActionError("Pass the fields to change (name, handle, website, font, voice, colors, end_card).")
    return {"kit": studio.house.save_kit(a, patch), "spent_usd": 0}


VIDEO_EXTS = ("mp4", "mov", "m4v", "webm")


def _url_ext(url: str) -> str:
    from urllib.parse import urlparse
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    return path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""


def t_library_upload_url(studio, a, args):
    from . import share_import
    from .mcp_flow import StepError
    url = str(args.get("url") or "").strip()
    rid = _run_arg(args)
    rights = None
    if a.kind == "agent" and url and not share_import.provider(url) and not _own_url(studio, url):
        from .media import find
        if find.platform_of(url):  # a YouTube / Vimeo / X post is a page, not a file: the worker fetches it (rights step first)
            raise StepError("library_upload_url takes a file link, and that is a video post page. Nothing was fetched.", 400,
                            code="wrong_tool", next_steps=[f"render_reel(run_id={rid or 'run_id'}, url) or edit_video(run_id, url): the "
                                                           "worker fetches it after the rights step",
                                                           "ingest_video_link only for One take (one long video -> clips)"])
        lk = {"url": url, "approval_id": args.get("approval_id"), "run_id": rid}
        if _url_ext(url) in VIDEO_EXTS:  # a third-party video link: the same rights step as every tool, BEFORE the fetch
            held = _link_rights(studio, a, lk, "library_upload_url")
            if held:
                return held
            rights = lk.get("_rights")
        else:  # no video extension: a link that turns out to be a video is held before anything is saved
            def gate():
                held = _link_rights(studio, a, lk, "library_upload_url")
                if held:
                    held = {**held, "fetched": False, "saved": False,
                            "message": str(held.get("message") or "").replace("nothing was fetched", "nothing was saved"),
                            "note": "The link only showed it was a video once it was read; those bytes were discarded and "
                                    "nothing was saved."}
                return held
            up = studio.house.import_url(a, url, video_gate=gate)
            if up.get("_held"):
                return up["_held"]
            return _library_out(studio, a, up, rid, lk.get("_rights"))
    if share_import.provider(url):  # a cloud-drive share page: convert it and stream it in (Drive's confirm page included)
        out = share_import.item_out(studio.house, studio.house.share_import.import_link(a, url), share_import.provider(url))
        if rid and out.get("library_id"):
            studio.house.library.attach_run(a, out["library_id"], rid)
        return {**out, "token": out["media_token"]}
    return _library_out(studio, a, studio.house.import_url(a, url), rid, rights)


def _own_url(studio, url: str) -> bool:
    """True for a link on this HuhQueue server itself (its /sample clips, its /m/ media): not a third-party video, so
    no rights step. Exact hostname + port match (80/443 normalised); anything unparsable is not ours."""
    from urllib.parse import urlparse

    def hp(u):
        try:
            p = urlparse(u)
            port = p.port or {"https": 443, "http": 80}.get(p.scheme)
        except ValueError:
            return None
        return ((p.hostname or "").lower(), port) if p.hostname else None
    s = getattr(getattr(studio.house, "s", None), "public_url", "") or ""
    mine, theirs = hp(s), hp(url)
    return bool(mine and theirs and mine == theirs and urlparse(url).scheme == "https")


def _library_out(studio, a, up: dict, rid, rights: dict | None) -> dict:
    out = {"media_token": up["token"], "url": up["url"], "type": up["type"], "library_id": up.get("library_id"), "bytes": up.get("bytes")}
    if up.get("deduped"):
        out["deduped"] = True
    if rid and up.get("library_id"):
        studio.house.library.attach_run(a, up["library_id"], rid)
        out["run_id"] = rid
    if rights:
        out["rights"] = rights
    return out


def t_integrations_status(studio, a, args):
    integ = getattr(studio.house, "integrations", None)
    if not integ:
        return {"integrations": [], "note": "Integrations aren't set up on this server."}
    v = integ.view(a)
    return {"integrations": [{k: i.get(k) for k in ("key", "name", "connected", "tested_ok", "unlocks")} for i in v["integrations"]],
            "mcp": "connected (this call)", "sample": v.get("sample")}


def t_weekly_recap_plan(studio, a, args):
    from .media import tools as mt
    out = mt.call("media_recap_plan", {k: args[k] for k in ("n", "days") if args.get(k)}) if hasattr(mt, "call") else {}
    return {**(out or {}), "spent_usd": 0,
            "next": "Write each story's title + one-line summary yourself, then render_template(template='weekly_recap', data={stories})."}


def call_tool(studio, actor: Actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    fn = globals()["t_" + name]
    out = fn(studio, actor, args or {})
    record(studio, actor, name)
    return out
