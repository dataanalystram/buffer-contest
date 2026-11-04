"""MCP prompts (playbooks) and resources for the HuhQueue server (2 Nov 2026: handle() advertised tools only, so
prompts/list, prompts/get and resources/list answered -32601 and a client had no recipes or brand context to start from).

Prompts are the owner's recipes as client-brain tool sequences: the client's own model follows them and HuhQueue spends no
AI. Resources are read-only context the client can attach: the active brand kit, its DESIGN.md, the video themes, the
templates (built-in + custom) and the MCP tools guide. Every resource is the signed-in workspace's own data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
GH = "https://github.com/dataanalystram/buffer-contest/blob/main/"

PROMPTS: list[dict[str, Any]] = [
    {"name": "launch_video_in_my_brand", "title": "Edit a launch video in my brand",
     "description": "Find a product's real launch video, cut it into a 30-45 s 9:16 Reel in the active brand kit with captions, a "
                    "credit line, a cover and the Follow end card, and save it as a Buffer DRAFT.",
     "arguments": [{"name": "link", "description": "The product or launch link (Product Hunt, the maker's site, X, YouTube)", "required": True},
                   {"name": "channel", "description": "Channel id or service (default instagram)", "required": False},
                   {"name": "seconds", "description": "Target length, 30-45 (default 40)", "required": False}]},
    {"name": "product_hunt_to_reel_draft", "title": "Turn a Product Hunt launch into a Reel draft",
     "description": "Product Hunt launch page -> its launch video -> a branded Reel with captions and credit -> Instagram draft.",
     "arguments": [{"name": "product_hunt_url", "description": "https://www.producthunt.com/products/... or /posts/...", "required": True}]},
    {"name": "carousel_from_link", "title": "Carousel from a link",
     "description": "Read an article or launch page and turn it into a 4:5 carousel in the brand (cover, 3-5 fact slides, end card) "
                    "with one caption per channel, saved as drafts.",
     "arguments": [{"name": "link", "description": "Article, blog post or launch page URL", "required": True},
                   {"name": "slides", "description": "Number of content slides, 3-5 (default 4)", "required": False}]},
    {"name": "one_take_clips", "title": "One take clips",
     "description": "One long video the user owns (talk, podcast, tutorial) -> 3-7 vertical clips of the best moments, captioned, "
                    "placed into next week's open slots as drafts.",
     "arguments": [{"name": "video_url", "description": "A direct https link to the video file, or a HuhQueue Library item", "required": True},
                   {"name": "clips", "description": "How many clips, 3-7 (default 5)", "required": False}]},
    {"name": "new_template", "title": "Build a new post template",
     "description": "Design a reusable template (e.g. a funding announcement or a job opening) in the active brand kit and preview it.",
     "arguments": [{"name": "purpose", "description": "What the template is for, e.g. 'funding announcement'", "required": True}]},
]

_RULES = ("Rules: ONE post for this request: keep the run_id from start_post and pass it to every render and create_package call "
          "(changes update the same post, even 'make it a reel instead': start_post(run_id, format='reel'); several outputs "
          "like '3 reels' are ONE post with output: 1, 2, 3; never start another one, a template or a brand kit unless the user "
          "asks). A new request with new content is a new post (start_post(new_request: true)); if unsure whether it is "
          "the same content, ask the user and use continue_run(run_id) to keep going. Drafts only "
          "(schedule_package mode='draft'); never publish or schedule. Do your own research with your own tools; use only facts "
          "from the source. The brand's Follow end card is always added last. Show the user the frames and caption before "
          "saving the draft.")


def _start(title: str) -> str:
    return f"0. start_post(title='{title}', user_asked=true, reason='<the request in a few words>') -> keep run_id.\n"


def _text(name: str, args: dict) -> str:
    a = {k: str(v or "").strip() for k, v in (args or {}).items()}
    if name == "launch_video_in_my_brand":
        secs = a.get("seconds") or "40"
        end = min(45, int(secs)) if secs.isdigit() else 40
        return (f"Make a {secs}-second Instagram Reel from the launch video at {a.get('link', '<link>')} in my active brand kit.\n"
                + _start("Launch Reel") +
                "1. find_video(url) -> pick the official launch/demo video (note its source and credit).\n"
                f"2. edit_video(run_id, url or media_token, rights='news_credit', credit='<the credit find_video gave>', ratio='9:16', "
                f"start_s=0, end_s<={end}, captions=true, hook='<max 10 words, facts only>', cover={{'title': '<the hook>'}}).\n"
                "3. render_status(job_id) every 15 s until done; look at the frames and the cover variants.\n"
                f"4. create_package(run_id, captions={{'{a.get('channel') or 'instagram'}': '<caption you wrote>'}}, media_urls=[download_url], "
                "format='reel', rights='news_credit', credit=..., source_url=..., cover=<render_status cover>).\n"
                f"5. schedule_package(run_id, channel_id, mode='draft') after the user says yes.\n{_RULES}")
    if name == "product_hunt_to_reel_draft":
        return (f"Turn the Product Hunt launch {a.get('product_hunt_url', '<url>')} into an Instagram Reel draft.\n"
                + _start("Product Hunt Reel") +
                "1. research_link(url) for the product name, tagline and maker; find_video(url) for its launch video.\n"
                "2. edit_video(run_id, ..., rights='news_credit', credit='Video: <maker> on Product Hunt', ratio='9:16', end_s<=45, "
                "captions=true, cover={'title': '<hook>'}).\n"
                "3. render_status until done -> create_package(run_id, captions={'instagram': ...}, media_urls=[...], format='reel', cover=...).\n"
                f"4. schedule_package(run_id, mode='draft').\n{_RULES}")
    if name == "carousel_from_link":
        n = a.get("slides") or "4"
        return (f"Make a carousel from {a.get('link', '<link>')}.\n"
                + _start("Carousel") +
                "1. research_link(url): title, facts, figures, official images.\n"
                "2. hero_edit(run_id, image_url=<the best official image's URL>) for the cover (give the URL; don't upload copies).\n"
                f"3. render_carousel(run_id, slides=[cover + {n} fact slides you write from the page; plain words, one figure per slide]). "
                "Fit problems: shorten and render again with the same run_id (it replaces the slides).\n"
                "4. create_package(run_id, captions per channel, media_urls=render_carousel's media_urls, format='carousel').\n"
                f"5. schedule_package(run_id, mode='draft').\n{_RULES}")
    if name == "one_take_clips":
        return (f"Turn {a.get('video_url', '<video>')} (the user owns it) into {a.get('clips') or '5'} clips.\n"
                "1. clips_from_video(url, owns=true, count=N) -> poll list_clips(take_id) until ready (one take, one request).\n"
                "2. Show each clip's hook and transcript; adjust_clip if the user asks.\n"
                "3. place_clips_in_week(take_id) dry run -> show the plan -> place_clips_in_week(take_id, confirm_token).\n"
                "Hooks are exact quotes from the video, never invented. Drafts only.")
    if name == "new_template":
        return (f"Build a reusable post template for: {a.get('purpose', '<purpose>')} (the user asked for a new template).\n"
                "1. list_templates and read the resource huhqueue://brand-kit/active so it fits the brand. If a template already "
                "fits, ask the user before making another.\n"
                "2. create_template(user_asked=true, reason='<what they asked for>', name, about, layout='kit' (or bold_news | "
                "editorial | minimal | playful), fields=[{key, label, sample}], slides=[front {kicker,title,sub}, then story / stat / "
                "bullets / quote slides with {field} placeholders]). The end card is added automatically.\n"
                "3. render_template(template='custom:<slug>', data={...}) and show the user the slides.\n"
                "4. update_template to fix anything they ask for (never a second create_template).")
    raise KeyError(name)


def get_prompt(name: str, args: dict) -> dict:
    p = next((x for x in PROMPTS if x["name"] == name), None)
    if not p:
        raise KeyError(name)
    missing = [x["name"] for x in p["arguments"] if x.get("required") and not str((args or {}).get(x["name"]) or "").strip()]
    if missing:
        raise ValueError(f"Missing argument(s): {', '.join(missing)}")
    return {"description": p["description"], "messages": [{"role": "user", "content": {"type": "text", "text": _text(name, args)}}]}


RESOURCES: list[dict[str, Any]] = [
    {"uri": "huhqueue://brand-kit/active", "name": "active_brand_kit", "title": "Active brand kit",
     "description": "The brand kit every render uses now: name, handle, colours, fonts, logo, end card, theme, plus the kit list.",
     "mimeType": "application/json"},
    {"uri": "huhqueue://brand-kit/active/design.md", "name": "design_md", "title": "DESIGN.md of the active kit",
     "description": "The active kit's design system as markdown (read from its website, or derived from its fonts and colours).",
     "mimeType": "text/markdown"},
    {"uri": "huhqueue://themes", "name": "themes", "title": "Video themes and edit styles",
     "description": "Every video look edit_video / render_reel / set_theme accept, with what each is best for.", "mimeType": "application/json"},
    {"uri": "huhqueue://templates", "name": "templates", "title": "Post templates",
     "description": "Built-in templates and this workspace's custom ones (custom:<slug>) with their fields.", "mimeType": "application/json"},
    {"uri": "huhqueue://docs/MCP_TOOLS.md", "name": "mcp_tools_guide", "title": "MCP tools guide",
     "description": "How to drive HuhQueue as a client brain: every tool, the recipes and the rules.", "mimeType": "text/markdown"},
    {"uri": "huhqueue://docs/DESIGN.md", "name": "huhqueue_design", "title": "HuhQueue DESIGN.md",
     "description": "HuhQueue's own design rules for renders.", "mimeType": "text/markdown"},
]
DOCS = {"MCP_TOOLS.md": "docs/MCP_TOOLS.md", "DESIGN.md": "DESIGN.md"}


def read_resource(studio, actor, uri: str) -> dict:
    from . import brand_kits, brand_routes
    if uri == "huhqueue://brand-kit/active":
        bk = brand_kits.attach(studio.house)
        lst = bk.list(actor)
        act = next((k for k in lst["kits"] if k["active"]), None)
        return _json(uri, {"active": act, "kits": [{"id": k["id"], "name": k["name"], "active": k["active"]} for k in lst["kits"]],
                           "house_kit": studio.house.kit(actor)})
    if uri == "huhqueue://brand-kit/active/design.md":
        bk = brand_kits.attach(studio.house)
        kid = bk._active(actor)
        text = bk.design_md(actor, kid) if kid else "# DESIGN.md\n\nNo brand kit is active yet. create_brand_kit(url=...) makes one.\n"
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
    if uri == "huhqueue://themes":
        from .mcp import call_tool
        return _json(uri, call_tool(studio, actor, "list_themes", {}))
    if uri == "huhqueue://templates":
        bs, _ = brand_routes.attach(studio.house)
        return _json(uri, bs.list_templates(actor))
    if uri.startswith("huhqueue://docs/") and uri[len("huhqueue://docs/"):] in DOCS:
        rel = DOCS[uri[len("huhqueue://docs/"):]]
        path = ROOT / rel
        text = path.read_text(encoding="utf-8")[:200_000] if path.is_file() else f"# {rel}\n\nRead it at {GH}{rel}\n"
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text}]}
    raise KeyError(uri)


def _json(uri: str, body) -> dict:
    return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(body, indent=1, default=str)}]}
