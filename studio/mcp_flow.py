"""One recommended path for AI apps over MCP (owner report, 6 Nov 2026).

Claude was asked for ONE post on three channels from a topic and a launch video. It went wrong eight ways: it ran One take
(ingest_video_link said "use whenever someone gives a video link"), create_package wiped the previous channel's package so
it split the work into three runs, post_render_reel silently returned slides, find_video called a YouTube video
"downloadable" that the worker could not fetch, extract_facts needed read_url first and nothing said so, replace_channel_media
refused start_post runs, two tool families had no "use this one", and the rights step behaved differently per tool.

This module is the contract an AI app reads:

* ``get_started`` (tool), the ``make_a_post`` prompt and the ``huhqueue://workflow/make-a-post`` resource: the steps.
* ``DESCRIPTIONS``: the descriptions of the flow tools, applied over the modules' own text in ``apply`` (the in-app
  agent's specs stay as they are: the post_* tools only get an MCP-side note). ``BEFORE`` keeps the old text.
* ``PREREQS``: what each flow tool needs first. It is in the description and in every answer (``prerequisites``).
* ``StepError``: an error with a code, what is missing and the next steps, sent as MCP structuredContent.
"""
from __future__ import annotations

import copy
from typing import Any

from .actions import ActionError  # the only studio import: actions reaches this module, and the worker reaches actions


class StepError(ActionError):
    """An ActionError with a structured answer: ``code``, ``missing_step`` / ``next_steps`` and any detail. mcp.handle merges
    ``data`` into structuredContent; the message itself also names the next step, for apps that only read the text."""

    def __init__(self, message: str, status: int = 400, **data: Any):
        super().__init__(message, status)
        self.data = {k: v for k, v in data.items() if v is not None}


def upload_steps(run_id: Any = None, output: int | None = None) -> list[str]:
    """The person's own file instead of a video the worker can't fetch: the Send a file page (any device), then a render."""
    r = f"run_id={run_id}" if run_id else "run_id"
    o = f", output={output}" if output else ""
    return [f"create_send_code({r}): give the person the link or code; they drop the video file on the Send a file page "
            "(phone or computer, no app needed)",
            "list_recent_uploads: the new file's media_token",
            f"render_reel({r}, media_token=<it>, rights='own' | 'permission' | 'news_credit'{o}) then render_status(job_id)",
            f"create_package({r}, captions, channel_media={{<channel>: {{output: <n>}}}})"]


WORKFLOW = {
    "title": "Make a post (one request, any number of channels)",
    "steps": [
        "1. start_post(title, user_asked: true, reason, channel_ids): ONCE for the person's request. Keep run_id and pass it to "
        "every call below. Several channels, a different video or cover per channel: still ONE run.",
        "2. Source: research_link(url) for an article / launch page (facts, figures, images); find_video(url or product name) "
        "for its launch video. Only video.status 'found' (the worker probed it just now) can be fetched. "
        "'found_not_downloadable': ask the person for the file: create_send_code(run_id), then list_recent_uploads.",
        "3. Media, one output per different video or slide set: render_reel(run_id, url or media_token, rights, credit, "
        "output: 1) / render_carousel(run_id, slides, output: 2) / hero_edit(run_id, image_url) for a cover. Poll "
        "render_status(job_id) every 10-20 s. A failed video is an error with next steps, never slides in its place.",
        "4. Stage: create_package(run_id, captions={channel: text}, channel_media={channel: {output: n, cover: {url}}}). One call "
        "for every channel, or one call per channel: the channels you don't name keep their packages.",
        "5. Show the person the media and captions. Only after their yes: schedule_package(run_id, channel_id, mode='draft').",
    ],
    "rules": [
        "One user request = one run. Never open a run per channel or per video. A new start_post only for a NEW request from the "
        "person (start_post(new_request: true)); if unsure, ask them and use continue_run(run_id).",
        "A video LINK you fetch (not the person's own upload) goes through the same rights step whichever tool fetches it: the "
        "answer is needs_user_approval with approval_id; the person approves in HuhQueue → Inbox → Approvals (or tells you "
        "they own it / have permission: approve_link_ingest with i_own_or_have_rights: true). Then repeat the call with "
        "approval_id. Their standing 'Auto-approve video links' setting answers it at once (rights.basis: auto_approved).",
        "Change one channel later: render the new media (output: n), then replace_channel_media(run_id, channel, media_token) "
        "or create_package(run_id, captions={that channel: ...}, channel_media={that channel: {output: n}}).",
        "Drafts only. Nothing is published without the person.",
    ],
    "not_for_a_post": {
        "ingest_video_link / clips_from_video / split_video": "One take only: cut ONE long video (talk, podcast, stream) into "
        "several short clips. Never for a normal post that uses a video.",
        "make_post(brain='external') + post_* tools": "Deprecated for AI apps (kept for older clients): HuhQueue's own agent "
        "steps. Use the steps above instead.",
        "make_post / run_agent / reply_post with server_ai: true": "HuhQueue's server AI, only when the person asks for it "
        "(needs their approval).",
    },
}

DEPRECATED_NOTE = ("Deprecated for AI apps: use start_post -> render_reel / render_carousel -> create_package (get_started). "
                   "Kept for older clients.")

# what each flow tool needs first (in its description and in every answer it gives)
PREREQS: dict[str, list[str]] = {
    "start_post": [],
    "research_link": [],
    "find_video": [],
    "render_carousel": ["start_post -> run_id", "research_link (you write the slides from its facts)"],
    "hero_edit": ["start_post -> run_id", "an image URL (research_link images / find_media)"],
    "render_reel": ["start_post -> run_id", "a video: find_video status 'found' (probed), the person's upload "
                    "(create_send_code -> list_recent_uploads -> media_token) or list_library; or images for a slideshow",
                    "a video link that isn't the person's upload: the rights step (needs_user_approval -> approval_id)"],
    "edit_video": ["start_post -> run_id", "a video: media_token / library_id (the person's file) or url",
                   "a video link that isn't the person's upload: the rights step (needs_user_approval -> approval_id)"],
    "render_status": ["job_id from render_reel / edit_video (or take_id from split_video)"],
    "create_package": ["start_post -> run_id", "a caption you wrote for each channel",
                       "media: render_status download_url / render_carousel media_urls, or channel_media {channel: {output: n}}"],
    "schedule_package": ["create_package(run_id) staged that channel", "the person said yes"],
    "replace_channel_media": ["the channel is staged in the run (create_package, or the agent's check)",
                              "media_token of the new file (render_status / list_recent_uploads / list_library)"],
    "ingest_video_link": ["a LONG video the person wants cut into clips (One take only)", "the rights step (approval_id)"],
    "clips_from_video": ["a LONG video the person owns or may clip (One take only)"],
    "split_video": ["a LONG video the person owns or may clip (One take only)"],
    "post_read_url": ["make_post(brain='external') -> run_id"],
    "post_search_news": ["make_post(brain='external') -> run_id"],
    "post_extract_facts": ["make_post(brain='external') -> run_id", "a source: post_read_url or post_search_news (run "
                           "automatically from the run's link when missing)"],
    "post_find_video": ["make_post(brain='external') -> run_id"],
}
for _t in ("post_render_reel", "post_render_carousel", "post_render_cover", "post_render_template", "post_write_captions",
           "post_set_template", "post_edit_video", "post_edit_channel_media", "post_set_theme"):
    PREREQS[_t] = ["make_post(brain='external') -> run_id", "post_read_url (a source)", "post_extract_facts (the facts)",
                   "both run automatically from the run's link when missing"]
PREREQS["post_check"] = ["make_post(brain='external') -> run_id", "media rendered and post_write_captions"]


def _needs(name: str) -> str:
    p = PREREQS.get(name)
    if p is None:
        return ""
    return " Prerequisites: " + ("; ".join(p) if p else "none") + "."


S = {"type": "string"}
DESCRIPTIONS: dict[str, str] = {
    "start_post": (
        "STEP 1 of the recommended path for ANY post (get_started). Open ONE post for the person's request and get its run_id "
        "(no HuhQueue AI, nothing is rendered or posted). Call it once per request, with user_asked: true and the request as "
        "reason, even when the post goes to several channels with a different video or cover on each: never one run per "
        "channel. Then pass run_id to every research, render_carousel, hero_edit, edit_video, render_reel, upload and "
        "create_package call; render each different video or slide set as its own output (output: 1, 2, 3) and stage them per "
        "channel with create_package(channel_media). Follow-ups ('brighter', 'swap slide 3', 'shorter caption') and a change "
        "of format ('not a carousel, make it a reel': start_post(run_id, format='reel')) stay in this same post. A NEW request "
        "from the person, or a new chat, is start_post(new_request: true); when you can't tell, ask the person ('continue in "
        "the current post or start a new one?') and use continue_run(run_id). Never open extra runs without asking. The same "
        "request again within 10 minutes returns the post it opened (deduped: true). Every answer carries run_id and "
        "thread_url."),
    "research_link": (
        "Client brain (no HuhQueue AI). Read an article, launch page or post URL: title, site, description, the article "
        "paragraphs, figures found in the text (each with the source URL), the page's images and videos, and for a product / "
        "launch link the video-first result (video.status found | found_not_downloadable | none). 'found' means the worker "
        "probed the video just now and can fetch it; found_not_downloadable says why it can't (probe.reason) and how to get "
        "the file from the person (create_send_code). You write the facts, slides and captions from this."),
    "find_video": (
        "Client brain (no HuhQueue AI). VIDEO FIRST for a product or launch link (Product Hunt, YC launch, the maker's site, "
        "a name): Product Hunt's API -> the maker's site (og:video, <video>, YouTube/Vimeo/Loom embeds, press kit) -> the "
        "official channel. Every candidate is PROBED on the video worker (yt-dlp metadata and the first bytes, the same fetch "
        "path renders use) before it is called downloadable. status found = probed OK: use video.url with "
        "render_reel(run_id, url, rights, credit) and the credit 'Video: <maker>' on screen and in the caption. "
        "found_not_downloadable = the video exists but the worker can't fetch it (probe.reason: removed, private, region, bot "
        "check, or no worker answered): ask the person for the file with create_send_code(run_id) (ask has the words and "
        "upload_limit_mb). none = no video: make a carousel and offer the upload."),
    "render_reel": (
        "Make a branded 9:16 Reel (no HuhQueue AI): from a video (same options as edit_video: media_token / library_id for "
        "the person's file, or url) or from slides (images: image URLs, e.g. render_carousel's media_urls) as a slideshow "
        "with the end card. Returns a job id; poll render_status(job_id). With run_id it becomes that post's output "
        "(output: 1..10: one per different video, e.g. 1 for YouTube and 2 for LinkedIn). A video link that isn't the "
        "person's upload goes through the rights step first (needs_user_approval -> call again with approval_id). Never a "
        "silent fallback: when the video can't be fetched or rendered, the answer (or render_status) is an error with next "
        "steps (the person uploads the file); a video is never swapped for slides."),
    "edit_video": (
        "Edit or cut a video on the HuhQueue server (no HuhQueue AI): trim to start_s..end_s or cut_first_s seconds off the "
        "start, reframe to 9:16 / 4:5 / 1:1 (speaker-aware), burned-in word captions on or off (transcribed with Whisper on "
        "the video worker), a theme/style (list_themes), music only when asked (music: cinematic | pulse; auto = none), a "
        "hook title; the brand's Follow end card is always added last (brand rule). Give the video as media_token, "
        "library_id or url, and run_id for the post it is for (output: n for one video per channel). A video link that isn't "
        "the person's upload goes through the rights step first (needs_user_approval -> call again with approval_id). Returns "
        "a job id at once; poll render_status(job_id) every 10-20 s until status is done, then pass the output to "
        "create_package(run_id, channel_media={channel: {output: n}})."),
    "render_status": (
        "Status of a video job: job_id (edit_video / render_reel / render_template video / a link probe) or take_id "
        "(split_video). status queued | running | done | failed, with download_url(s) when done; failed answers carry the "
        "reason and next_steps (the person uploads the file through create_send_code). A failed video is never replaced by "
        "slides."),
    "create_package": (
        "STEP 4: stage the post YOU wrote, per channel. captions: {channel id or service: text}. Media, either for every "
        "channel (media_urls, or output: n) or PER CHANNEL with channel_media: {channel id or service: {output: n | media_urls: "
        "[...], cover: {url, frame_s}, format}}, so one run holds a different video and cover on each channel. Pass run_id "
        "(start_post): only the channels you name are updated; every other channel's package stays as it was (the previous "
        "version is kept for undo_package_media). replace_all: true drops the channels you didn't name. Without run_id, this "
        "app's open post is used (reused: true), or a new one is made if there is none. new_post: true with user_asked: true "
        "only when the person asked for another post. format='reel' needs a video on that channel: images answer an error, "
        "never a slideshow in its place. Returns run_id with one ready package per channel. Nothing is posted: show the "
        "person, then schedule_package(run_id, channel_id, mode=draft) creates a Buffer draft (it waits in Approvals when the "
        "token needs approval). No HuhQueue AI is used."),
    "replace_channel_media": (
        "Change ONE channel's media in a post run, on every kind of post run: start_post / create_package runs (pass "
        "media_token: a render's media_token from render_status, an upload from list_recent_uploads, or a Library item) and "
        "make_post runs (media_token, or omit it to have HuhQueue's agent regenerate it). Only that channel changes; the "
        "previous media is kept for undo_package_media and the brand check runs again. A video-only network (YouTube, TikTok) "
        "needs a video. One take runs: use adjust_clip."),
    "ingest_video_link": (
        "ONE TAKE ONLY: cut ONE LONG video (a talk, podcast, livestream, interview) from a link into several short vertical "
        "clips. NEVER use it for a normal post that has a video (a launch video, a news clip, a demo): for that follow "
        "get_started (start_post -> find_video -> render_reel(run_id, url) -> create_package(run_id)). It takes no run_id: a "
        "take is not a post run. Links: a YouTube, Vimeo or X video post, or a direct https link to an MP4. Rights first, "
        "the same step every tool uses for a link: the first call answers needs_user_approval with an approval_id and the "
        "rights statement; the person approves it in HuhQueue → Inbox → Approvals (or, only once they tell you they own it "
        "or have permission and are responsible for rights, approve_link_ingest with i_own_or_have_rights: true; never "
        "without their word). With their standing 'Auto-approve video links' setting on, it starts at once and says "
        "rights.basis: auto_approved. Then call again with the same url and approval_id; poll list_clips(take_id). The worker "
        "fetches it with yt-dlp (no cookies or accounts; up to 4 GB and 3 h); if the platform refuses (age-restricted, "
        "private, removed, a bot check) the take fails with the exact reason and the person uploads the file "
        "(create_send_code). licence_hint shows a Creative Commons flag when the platform states one."),
    "make_post": (
        "DEPRECATED for AI apps: use the recommended path instead (get_started: start_post -> research_link / find_video -> "
        "render_reel / render_carousel -> create_package, one run_id). make_post runs HuhQueue's own agent on one prompt "
        "(prose + links, up to 4,000 characters; server_ai: true, which needs the person's approval), or with brain='external' "
        "opens an agent run that you drive with the post_* tools (also deprecated, kept for older clients; they work on "
        "make_post runs only). Nothing is published."),
}
ONE_TAKE_PREFIX = "ONE TAKE ONLY (one long video -> several clips); never for a normal post with a video (see get_started). "
BEFORE: dict[str, str] = {}
YOUTUBE_SCHEMA = {"type": "object", "description": "YouTube fields: title (1-100), categoryId (e.g. '28' Science & Technology, "
                  "the suggested default), madeForKids (true/false; default false)",
                  "properties": {"title": S, "categoryId": S, "madeForKids": {"type": "boolean"}}}

TOOLS: list[dict[str, Any]] = [
    {"name": "get_started", "description": "READ THIS FIRST. The one recommended path to make a post (any number of channels, "
     "a different video or cover per channel), which tools are NOT for a post (One take, the deprecated post_* family), the "
     "rights step for video links, and what each flow tool needs first. Read-only; nothing is created.",
     "inputSchema": {"type": "object", "properties": {}}},
]
READ_ONLY = {"get_started"}


def apply(tools: list[dict], server_ai_note: str = "") -> None:
    """The flow tools' descriptions, prerequisites and deprecation notes over the modules' own text (idempotent)."""
    for t in tools:
        n = t["name"]
        if n == "autofix_post" and "youtube" not in t.get("inputSchema", {}).get("properties", {}):
            t.setdefault("inputSchema", {"type": "object"}).setdefault("properties", {})["youtube"] = YOUTUBE_SCHEMA
            t["description"] = t["description"].rstrip() + (" youtube: {title, categoryId, madeForKids} sets the YouTube fields "
                                                             "preflight asks for (suggested category 28, Science & Technology).")
        if n in BEFORE:
            continue
        old = t["description"]
        if n in DESCRIPTIONS:
            new = DESCRIPTIONS[n]
            if server_ai_note and server_ai_note.strip() in old and server_ai_note.strip() not in new:
                new = new.rstrip() + server_ai_note
        elif n in ("clips_from_video", "split_video"):
            new = ONE_TAKE_PREFIX + old
        elif n.startswith("post_"):
            new = DEPRECATED_NOTE + " " + old
        else:
            continue
        if n in ("make_post",) and "user_asked" in old and "user_asked" not in new:
            new += " Only when the person asked for it: pass user_asked: true."
        BEFORE[n] = old
        t["description"] = new.rstrip() + _needs(n)
        if n == "create_package":
            props = t.setdefault("inputSchema", {"type": "object"}).setdefault("properties", {})
            props["channel_media"] = {
                "type": "object", "description": "Per channel: {channel id or service: {output: n (a render of this run), "
                "media_urls: [...], cover: {url, frame_s}, format}}. A channel without an entry uses media_urls / output.",
                "additionalProperties": {"type": "object", "properties": {
                    "output": {"type": "integer", "minimum": 1, "maximum": 10}, "media_urls": {"type": "array", "items": S, "maxItems": 10},
                    "cover": {"type": "object", "properties": {"url": S, "frame_s": {"type": "number", "minimum": 0}}},
                    "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"]}}}}
            props["youtube"] = YOUTUBE_SCHEMA
            props["replace_all"] = {"type": "boolean", "description": "true: the channels you don't name are dropped from the post "
                                    "(default false: they keep their packages)"}
        if n in ("render_reel", "edit_video"):
            props = t.setdefault("inputSchema", {"type": "object"}).setdefault("properties", {})
            props.setdefault("approval_id", {"type": "integer", "description": "The approval_id needs_user_approval returned for "
                             "this video link, once the person approved it"})


def workflow() -> dict:
    return {**copy.deepcopy(WORKFLOW), "prerequisites": copy.deepcopy(PREREQS), "spent_usd": 0,
            "resource": "huhqueue://workflow/make-a-post", "prompt": "make_a_post"}


def workflow_markdown() -> str:
    w = WORKFLOW
    lines = [f"# {w['title']}", "", *w["steps"], "", "## Rules", *[f"- {r}" for r in w["rules"]], "", "## Not for a post"]
    lines += [f"- **{k}**: {v}" for k, v in w["not_for_a_post"].items()]
    lines += ["", "## Prerequisites per tool"]
    lines += [f"- `{k}`: {'; '.join(v) if v else 'none'}" for k, v in PREREQS.items()]
    return "\n".join(lines) + "\n"


def annotate(name: str, result: Any) -> Any:
    """Every answer of a flow tool lists what the tool needs first (the client sees it even if it skipped the description)."""
    if name in PREREQS and isinstance(result, dict) and "prerequisites" not in result:
        return {**result, "prerequisites": PREREQS[name]}
    return result


def call_tool(studio, actor, name: str, args: dict):
    if name != "get_started":
        return NotImplemented
    return workflow()
