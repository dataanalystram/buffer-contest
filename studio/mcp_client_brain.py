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
    "You are the brain: read with research tools, write slides and captions yourself, then call render tools. "
    "HuhQueue is the hands: it reads links and X posts, finds official media, edits images and VIDEO (trim/cut, split a long "
    "video into clips, reframe 9:16/4:5/1:1, captions on/off/style, themes, end card, music), renders carousels and Reels "
    "in the user's brand kit and stages Buffer drafts that wait for the user's approval. You CAN edit videos with HuhQueue: "
    "list_library(kind=video) -> edit_video(media_token, start_s/end_s or cut_first_s, captions) -> poll render_status(job_id) "
    "-> show the download URL. Carousel: research_link -> write 5-7 slides yourself -> render_carousel(slides) -> "
    "brand_check(your caption) -> create_package(captions per channel) -> schedule_package(mode=draft) after the user says yes. "
    "Long video: split_video(media_token or url, n) -> poll render_status(take_id=...). Heavy jobs return an id at once; poll "
    "every 10-20 s. Tools marked 'server AI' (run_agent, revise, edit_media, make_post, reply_post) spend the user's "
    "HuhQueue AI allowance and only run with server_ai: true."
)

# Tools that spend HuhQueue's own model key. Over MCP they need server_ai: true (the person's explicit choice).
SERVER_AI_TOOLS = {"run_agent", "revise", "edit_media", "make_post", "reply_post"}
SERVER_AI_NOTE = " Uses HuhQueue's server AI (counts against your HuhQueue AI allowance): pass server_ai: true to run it."
SEQUENCES = {
    "run_agent": "research_link(url) or find_media(query) -> write the slides/hook yourself -> render_carousel / render_reel -> "
                 "create_package(captions) -> schedule_package(mode=draft)",
    "make_post": "research_link(url) -> write the slides yourself -> render_carousel(slides) -> create_package(captions) -> "
                 "schedule_package(mode=draft)",
    "edit_media": "library_upload_url(url) or list_library(kind=video) -> edit_video(media_token, ...) -> render_status(job_id)",
    "revise": "change your slides/caption yourself and call render_carousel / edit_video again; remember_rule saves a brand rule",
    "reply_post": "write the reply yourself, then preview_post / submit_post",
    "replace_channel_media": "render the new media (render_carousel / edit_video), then create_package with it",
    "edit_brand": "update_brand_kit(colors, font, handle, end_card...) changes the brand kit without AI",
}


def server_ai_hint(name: str) -> dict:
    """What a server-AI tool answers over MCP without server_ai: true: no model call, the client-brain sequence instead."""
    return {"server_ai_required": True, "tool": name, "spent_usd": 0,
            "message": f"{name} uses HuhQueue's server AI, which counts against the user's HuhQueue AI allowance. You are the "
                       "brain here: do it with the client-brain tools (no HuhQueue AI), or call again with server_ai: true if "
                       "the user wants HuhQueue's own agent to do it.",
            "client_brain_sequence": SEQUENCES.get(name, "research_link -> render_carousel / edit_video -> create_package"),
            "how": INSTRUCTIONS}


SLIDE = {"type": "object", "properties": {
    "eyebrow": {"type": "string", "description": "Small uppercase label above the headline, e.g. 'YC F26 · AI + FINTECH'"},
    "headline": {"type": "string", "description": "Max ~8 words (cover max 10). Title Case."},
    "accent_word": {"type": "string", "description": "ONE word of the headline drawn in the brand accent"},
    "body": {"type": "string", "description": "Plain words, max ~40; **bold** marks key facts. On the cover it is the one-line dek."},
    "footnote": {"type": "string", "description": "Small grey source / terms line"},
    "image": {"type": "string", "description": "https URL of a picture (official image from research_link / find_media)"}}}
VIDEO_SRC = {"media_token": {"type": "string", "description": "A HuhQueue media token (list_library -> media_token, or an upload)"},
             "library_id": {"type": "integer", "description": "A Library item id (list_library)"},
             "url": {"type": "string", "description": "A public https link to a video file you own (imported first)"}}

TOOLS: list[dict[str, Any]] = [
    {"name": "research_link", "description": "Client brain (no HuhQueue AI). Read an article, launch page or post URL: title, site, "
     "description, the article paragraphs, figures found in the text (each with the source URL), the page's images and videos. "
     "You write the facts, slides and captions from this.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S, "max_paragraphs": {"type": "integer", "maximum": 60}}}},
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
     "default) or text_slides. Returns media_urls (store them in the Library) and fit problems to fix by writing shorter text. "
     "No HuhQueue AI is used.",
     "inputSchema": {"type": "object", "required": ["slides"], "properties": {
         "slides": {"type": "array", "minItems": 2, "maxItems": 9, "items": SLIDE},
         "template": {"type": "string", "enum": ["editorial", "editorial_story", "text_slides"]},
         "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16"]}, "follow_line": S,
         "keep_case": {"type": "array", "items": S, "description": "Brand names to keep exactly as written in Title Case "
                       "headlines, e.g. ['ernd', 'iPhone']"}}}},
    {"name": "hero_edit", "description": "The premium cover edit of an official image (no HuhQueue AI): saliency crop to the frame, "
     "colour match to the brand ground, vignette and fade; optional product cut-out tilted with a shadow. Returns the edited "
     "image URL to use as slides[0].image in render_carousel.",
     "inputSchema": {"type": "object", "required": ["image_url"], "properties": {"image_url": S, "product_url": S,
                     "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16"]}}}},
    {"name": "edit_video", "description": "Edit or cut a video on the HuhQueue server (no HuhQueue AI): trim to start_s..end_s or "
     "cut_first_s seconds off the start, reframe to 9:16 / 4:5 / 1:1 (speaker-aware), burned-in word captions on or off "
     "(transcribed with Whisper on the video worker), a theme/style (list_themes), music under silent video, a hook title; the "
     "brand's Follow end card is always added last (brand rule). "
     "Give the video as media_token, library_id or url. Returns a job id at once; poll "
     "render_status(job_id) every 10-20 s until status is done, then show download_url.",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC,
         "start_s": {"type": "number"}, "end_s": {"type": "number"},
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
         "rights_confirmed": {"type": "boolean", "description": "The user owns the video or has the right to use it"}}}},
    {"name": "render_reel", "description": "Make a branded 9:16 Reel (no HuhQueue AI): from a video (same options as edit_video) or "
     "from slides (image URLs, e.g. render_carousel's media_urls) as a slideshow with the end card. Returns a job id; poll "
     "render_status(job_id).",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC, "images": {"type": "array", "items": S, "maxItems": 10},
         "hook": S, "theme": S, "captions": {"type": ["boolean", "string"], "enum": [True, False, "auto", "replace", "on", "off"]}, "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]},
         "ratio": {"type": "string", "enum": ["9:16", "4:5", "1:1"]}, "rights_confirmed": {"type": "boolean"}, "credit": S}}},
    {"name": "split_video", "description": "Split a long video into 3-7 short vertical clips on the video worker (no HuhQueue AI: "
     "Whisper transcript, built-in moment scorer, face-tracked reframe, captions, cover and end card). Give media_token / "
     "library_id / url (a file or a YouTube/Vimeo/X post) and owns: true (or rights_confirmed + credit). Returns take_id; poll "
     "render_status(take_id=...) or list_clips(take_id). sample: true shows the bundled example (Buffer's Buffer Commit kickoff).",
     "inputSchema": {"type": "object", "properties": {**VIDEO_SRC, "n": {"type": "integer", "minimum": 1, "maximum": 7},
         "owns": {"type": "boolean"}, "rights_confirmed": {"type": "boolean"}, "credit": S, "name": S,
         "channel_ids": {"type": "array", "items": S}, "sample": {"type": "boolean"}}}},
    {"name": "render_status", "description": "Status of a video job: job_id (edit_video / render_reel / render_template video) or "
     "take_id (split_video). status queued | running | done | failed, with download_url(s) when done and the error when failed.",
     "inputSchema": {"type": "object", "properties": {"job_id": {"type": "integer"}, "take_id": {"type": "integer"}}}},
    {"name": "create_package", "description": "Stage a post YOU wrote: captions per channel (channel id or service name -> text) and "
     "the media URLs (render_carousel / render_status). Returns run_id with one ready package per channel. Nothing is posted: "
     "show the user, then schedule_package(run_id, channel_id, mode=draft) creates a Buffer draft (it waits in Approvals when the "
     "token needs approval). No HuhQueue AI is used.",
     "inputSchema": {"type": "object", "required": ["captions"], "properties": {
         "captions": {"type": "object", "description": "{channel_id or service: caption}", "additionalProperties": S},
         "media_urls": {"type": "array", "items": S, "maxItems": 10}, "title": S,
         "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"]}}}},
    {"name": "update_brand_kit", "description": "Change the brand kit directly (no HuhQueue AI): name, handle, website, font, voice, "
     "colors {primary, accent, text} as hex, end_card {on, line, cta}.",
     "inputSchema": {"type": "object", "properties": {"name": S, "handle": S, "website": S, "font": S, "voice": S,
         "colors": {"type": "object", "properties": {"primary": S, "accent": S, "text": S}},
         "end_card": {"type": "object", "properties": {"on": {"type": "boolean"}, "line": S, "cta": S}}}}},
    {"name": "library_upload_url", "description": "Add a photo or video the user owns to their Library from a public https URL "
     "(up to 40 MB). Returns media_token and library_id for edit_video / split_video / render_carousel.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S}}},
    {"name": "integrations_status", "description": "Which integrations are connected for this user (Product Hunt, X reader key, "
     "image models...) and whether each key last tested OK. Never returns a key.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "weekly_recap_plan", "description": "Client brain (no HuhQueue AI): the week's top AI stories from the news sources, "
     "each with its official video/image and credit line. Write the recap slides yourself, then render_template(template="
     "weekly_recap, data={stories:[{title, summary, source, image}]}).",
     "inputSchema": {"type": "object", "properties": {"n": {"type": "integer", "maximum": 9}, "days": {"type": "integer", "maximum": 14}}}},
]
NAMES = {t["name"] for t in TOOLS}
READ_ONLY = {"research_link", "read_x_post", "find_media", "scrape_brand_assets", "render_status", "integrations_status",
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


def _queue_video(h, a: Actor, spec: dict) -> dict:
    """One render job, never waited on here: the MCP client polls render_status (MCP tool calls must answer fast)."""
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
        where = "the GitHub video worker"
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


def t_research_link(studio, a, args):
    from . import intake, netguard, reader
    h = studio.house
    try:
        url = netguard.check_url(intake.clean_url(str(args.get("url") or "")))
    except netguard.FetchError as e:
        raise ActionError(f"That link isn't allowed: {e}")
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
    return out


def t_read_x_post(studio, a, args):
    from . import reader
    url = str(args.get("url") or "")
    if not reader.x_status(url):
        raise ActionError("That isn't a link to an X post.")
    return t_research_link(studio, a, {"url": url})


def t_find_media(studio, a, args):
    from .media import tools as mt
    out = mt.media_find(str(args.get("query") or ""), max_results=int(args.get("max_results") or 5), http=studio.house.http)
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
    media = [_keep_image(h, a, im, f"slide{i + 1}.jpg", {"template": used, "ratio": ratio}) for i, im in enumerate(ims)]
    lib = None
    try:
        lib = h.library.register_generated(a, media, "carousel", None)
    except Exception:  # noqa: BLE001 - the Library never breaks a render
        pass
    return {"template": used, "ratio": ratio, "slides": len(media), "media_urls": [m["url"] for m in media],
            "media_tokens": [m["token"] for m in media], "library_id": lib, "fit_problems": problems, "spent_usd": 0,
            "next": "Fix any fit_problems by writing shorter text and render again; then brand_check your caption and "
                    "create_package(captions, media_urls)."}


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
    m = _keep_image(h, a, im.convert("RGB"), "hero.jpg", {"edit": how})
    return {"url": m["url"], "media_token": m["token"], "edit": how, "report": report, "spent_usd": 0}


def _captions_mode(v) -> str:
    """edit_video's captions argument -> the worker's mode (owner bug 2 Nov 2026: a captioned clip got captioned again)."""
    if v is False or v == "off":
        return "off"
    if v in ("replace", "on"):
        return v
    return "auto"  # true, missing or "auto": caption it unless captions are already burned in


def _video_spec(h, a, args, tok, meta) -> dict:
    from .media import styles as media_styles
    style = str(args.get("theme") or args.get("caption_style") or "opus")
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
    owned = True  # media_token / library_id / url imports are the user's own files (library_upload_url: "a video you own")
    spec = {"kind": "media_render", "style": sid, "ratio": args.get("ratio") if args.get("ratio") in ("9:16", "4:5", "1:1") else "9:16",
            "hook": str(args.get("hook") or "")[:120], "facts": [], "credit": str(args.get("credit") or "")[:160], "names": [],
            "music": args.get("music") or "auto", "official": False,
            "rights_confirmed": bool(args.get("rights_confirmed", owned)), "source": tok, "candidate": None, "slides": [],
            "captions": args.get("captions") not in (False, "off"),
            "captions_mode": _captions_mode(args.get("captions")),
            "brand_colors": True, **_kit_spec(h, a), "via": SOURCE}
    if clip:
        spec["clip"] = clip
    return spec


def t_edit_video(studio, a, args):
    h = studio.house
    studio._guard_write(a, "edit_video")
    tok, meta = _video_source(h, a, args)
    if meta.get("type") not in (None, "video"):
        raise ActionError("That media is not a video.")
    spec = _video_spec(h, a, args, tok, meta)
    out = _queue_video(h, a, spec)
    return {**out, "edit": {k: spec.get(k) for k in ("clip", "ratio", "style", "captions", "captions_mode", "music")}}


def t_render_reel(studio, a, args):
    h = studio.house
    studio._guard_write(a, "render_reel")
    if args.get("images"):
        from . import brand
        toks = []
        base = h.s.public_url.rstrip("/") + "/m/"
        for u in list(args["images"])[:10]:
            u = str(u)
            toks.append(u[len(base):] if u.startswith(base) else h.put_media(a, _img_bytes(h, u) or b"", "slide.jpg", kind="render",
                                                                              meta={"type": "photo"}))
        kit = h.kit(a)
        spec = {"kind": "slideshow", "kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "params": brand.visual_params(kit, h.rules(a)),
                "slides": toks, "logo": kit.get("logo") or "", "ratio": args.get("ratio") or "9:16", "via": SOURCE}
        return _queue_video(h, a, spec)
    return t_edit_video(studio, a, args)


def t_split_video(studio, a, args):
    h = studio.house
    if args.get("sample"):
        return h.takes.start_sample(a, {"channel_ids": args.get("channel_ids")})
    body = {k: args.get(k) for k in ("owns", "rights_confirmed", "credit", "name", "channel_ids") if args.get(k) is not None}
    if args.get("n"):
        body["count"] = int(args["n"])
    if args.get("media_token") or args.get("library_id") not in (None, ""):
        body["media_token"] = _video_source(h, a, args)[0]
    elif args.get("url"):
        body["url"] = str(args["url"])
    t = h.takes.start(a, body)
    return {"take_id": t["id"], "status": t.get("status"), "poll": f"render_status(take_id={t['id']}) every 20-30 s",
            "spent_usd": 0}


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
        m = h.media_meta(a, r["media_token"]) or {}
        out["meta"] = {k: m.get(k) for k in ("width", "height", "duration", "ratio", "style", "captions", "clip", "captions_note")
                       if m.get(k) not in (None, "")}
        if m.get("captions_note"):
            out["note"] = m["captions_note"]  # e.g. it already had captions, so none were added
    if st == "failed":
        out["error"] = (r["error"] or "")[:300]
    if st == "queued":
        out["next"] = "Still rendering: call render_status again in 15 s."
    return out


def t_create_package(studio, a, args):
    h = studio.house
    studio._guard_write(a, "create_package")
    caps = args.get("captions") if isinstance(args.get("captions"), dict) else {}
    if not caps:
        raise ActionError("captions is {channel_id or service: text} with the caption you wrote for each channel.")
    chans = [c for c in studio.channels(a) if not c.get("isDisconnected")]
    media_urls = [str(u) for u in args.get("media_urls") or []][:10]
    pk, unknown = [], []
    for key, text in caps.items():
        hits = [c for c in chans if c["id"] == key] or [c for c in chans if c.get("service") == key]
        if not hits:
            unknown.append(key)
        for c in hits:
            fmt = args.get("format") or ("reel" if any(u.lower().split("?")[0].endswith((".mp4", ".mov")) for u in media_urls) else
                                         "carousel" if len(media_urls) > 1 else "photo" if media_urls else "text")
            pk.append({"channel_id": c["id"], "service": c.get("service"), "channel": c.get("displayName") or c.get("name"),
                       "channel_name": c.get("displayName") or c.get("name"),  # every package reader (Planner, Make a post) uses this
                       "text": str(text)[:5000], "media_urls": media_urls, "format": fmt, "status": "ready",
                       "media": [{"url": u, "type": "video" if u.lower().split("?")[0].endswith((".mp4", ".mov")) else "photo"}
                                 for u in media_urls], "check": h.check(a, str(text))})
    if not pk:
        raise ActionError(f"No connected channel matches {', '.join(unknown)}. list_channels gives ids and services.")
    inp = {"prompt": str(args.get("title") or "Post from your AI app")[:200], "via": SOURCE,
           "channels": [{"id": p["channel_id"], "service": p["service"], "name": p["channel"]} for p in pk]}
    res = {"packages": pk, "media": pk[0]["media"], "story": {"headline": str(args.get("title") or "")[:120]}, "brain": "client"}
    rid = h.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                    (a.uid, "client", json.dumps(inp), "ready", json.dumps([]), json.dumps(res), time.time(), time.time()))
    return {"run_id": rid, "packages": [{k: p[k] for k in ("channel_id", "service", "format", "status")} | {"brand_check": p["check"]}
                                        for p in pk], "unknown_channels": unknown, "spent_usd": 0,
            "next": "Show the user. On their yes: schedule_package(run_id, channel_id, mode='draft')."}


def t_update_brand_kit(studio, a, args):
    patch = {k: args[k] for k in ("name", "handle", "website", "font", "voice", "colors", "end_card") if k in args}
    if not patch:
        raise ActionError("Pass the fields to change (name, handle, website, font, voice, colors, end_card).")
    return {"kit": studio.house.save_kit(a, patch), "spent_usd": 0}


def t_library_upload_url(studio, a, args):
    up = studio.house.import_url(a, str(args.get("url") or ""))
    return {"media_token": up["token"], "url": up["url"], "type": up["type"], "library_id": up.get("library_id"), "bytes": up.get("bytes")}


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
