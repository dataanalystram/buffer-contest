"""Growth routes (campaigns, UTM, short links, link in bio, automations, CSV import) and their registry entries.

Every route below has an Action in the registry at the bottom, so it shows up in REST (/api/actions), MCP, the
⌘K command bar and the CLI with no extra wiring. Public pages (/l/{code}, /bio/{slug}) are added to both the
UI app and the edge app by `add_public_growth_routes`.
"""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .. import actions as reg
from ..actions import B, I, O, S, SA, Action
from ..services import growth as g
from .bio_api import add_bio_routes, add_public_bio_routes   # also registers the bio.* actions

UTM = {"type": "object", "description": "{source, medium, campaign, term, content}; {provider} {username} {campaign} placeholders"}
CAMPAIGN_BODY = {"name": S, "color": {**S, "description": "#RRGGBB"}, "description": S,
                 "start_date": {**S, "description": "YYYY-MM-DD"}, "end_date": {**S, "description": "YYYY-MM-DD"}}
AUTOMATION_CONFIG = {**O, "description": "recurring: {caption, media_ids, options, frequency daily|weekly|monthly, interval, weekdays[0-6], "
                     "day_of_month, time HH:MM, timezone, start_date, end_date, max_occurrences, lead_hours} · evergreen: {min_age_days, "
                     "max_recycles, per_run, every_days, min_score, tag, campaign, when queue|draft} · rss: {url, mode draft|queue, "
                     "template '{title} {link} {summary}', max_per_run, every_minutes, backfill, include_image} · first_comment: {text, "
                     "mode if_empty|append, tag, campaign, video_only}"}


def _approval_actor(request: Request) -> str | None:
    tok = getattr(request.state, "token", None)
    if not tok:
        return None
    if tok.require_approval:
        return tok.name
    if not tok.can("publish"):
        raise HTTPException(403, f"Token '{tok.name}' can only create drafts (no 'publish' scope)")
    return None


def add_growth_routes(app: FastAPI, core) -> None:
    # ---------------------------------------------------------------- campaigns (GET /api/campaigns is in app.py)
    @app.post("/api/campaigns", tags=["campaigns"])
    def campaign_create(payload: dict = Body(...)):
        return g.save_campaign(core, payload)

    @app.get("/api/campaigns/{cid}", tags=["campaigns"])
    def campaign_get(cid: str):
        return g.get_campaign(core, cid)

    @app.patch("/api/campaigns/{cid}", tags=["campaigns"])
    def campaign_update(cid: str, payload: dict = Body(...)):
        return g.save_campaign(core, payload, cid)

    @app.delete("/api/campaigns/{cid}", tags=["campaigns"])
    def campaign_delete(cid: str):
        return g.delete_campaign(core, cid)

    # ---------------------------------------------------------------- UTM
    @app.get("/api/utm/defaults", tags=["utm"])
    def utm_defaults_get():
        return g.utm_defaults(core)

    @app.put("/api/utm/defaults/{channel_id}", tags=["utm"])
    def utm_defaults_put(channel_id: str, payload: dict = Body(...)):
        return g.set_utm_defaults(core, channel_id, payload)

    @app.post("/api/utm/preview", tags=["utm"])
    def utm_preview(payload: dict = Body(...)):
        return g.utm_preview(core, payload)

    # ---------------------------------------------------------------- short links
    @app.get("/api/links", tags=["links"])
    def links_list(request: Request, q: str = "", campaign: str = "", source: str = ""):
        return g.list_links(core, q, campaign, source, request)

    @app.post("/api/links", tags=["links"])
    def link_create(request: Request, payload: dict = Body(...)):
        return g.create_link(core, payload, request)

    @app.get("/api/links/{lid}", tags=["links"])
    def link_get(lid: str, request: Request, days: int = 30):
        return g.get_link(core, lid, request, max(1, min(days, 365)))

    @app.patch("/api/links/{lid}", tags=["links"])
    def link_update(lid: str, request: Request, payload: dict = Body(...)):
        return g.update_link(core, lid, payload, request)

    @app.delete("/api/links/{lid}", tags=["links"])
    def link_delete(lid: str):
        return g.delete_link(core, lid)

    # ---------------------------------------------------------------- link in bio (api/bio_api.py)
    add_bio_routes(app, core)

    # ---------------------------------------------------------------- automations
    @app.get("/api/automations", tags=["automations"])
    def automations_list(kind: str = ""):
        return g.list_automations(core, kind)

    @app.post("/api/automations", tags=["automations"])
    def automation_create(payload: dict = Body(...)):
        return g.save_automation(core, payload)

    @app.get("/api/automations/{aid}", tags=["automations"])
    def automation_get(aid: str):
        return g.get_automation(core, aid)

    @app.patch("/api/automations/{aid}", tags=["automations"])
    def automation_update(aid: str, payload: dict = Body(...)):
        return g.save_automation(core, payload, aid)

    @app.delete("/api/automations/{aid}", tags=["automations"])
    def automation_delete(aid: str):
        return g.delete_automation(core, aid)

    @app.post("/api/automations/{aid}/run", tags=["automations"])
    def automation_run(aid: str):
        return g.run_automation(core, aid)

    @app.get("/api/automations/{aid}/preview", tags=["automations"])
    def automation_preview(aid: str):
        return g.preview_automation(core, aid)

    @app.post("/api/rss/check", tags=["automations"])
    def rss_check(payload: dict = Body(...)):
        """Fetch a feed and show its newest items (before saving an RSS rule)."""
        url = (payload.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            raise g.GrowthError("Feed URL must start with http:// or https://")
        feed = g.fetch_feed(core, url)
        tpl = payload.get("template") or "{title}\n\n{link}"
        return {"title": feed["title"], "count": len(feed["items"]),
                "items": [{"title": it["title"], "link": it["link"], "caption": g.render_template(tpl, it, feed["title"])}
                          for it in feed["items"][:10]]}

    # ---------------------------------------------------------------- hashtags
    @app.get("/api/hashtags/stats", tags=["hashtags"])
    def hashtags_stats(request: Request, days: int = 90):
        from ..scope_filter import visible
        return g.hashtag_stats(core, max(1, min(days, 730)), channels=visible(request))

    @app.get("/api/hashtags/suggest", tags=["hashtags"])
    def hashtags_suggest(request: Request, caption: str = "", limit: int = 10):
        from ..scope_filter import visible
        return g.suggest_hashtags(core, caption, max(1, min(limit, 30)), channels=visible(request))

    @app.get("/api/hashtags/groups", tags=["hashtags"])
    def hashtag_groups_list():
        return g.list_hashtag_groups(core)

    @app.post("/api/hashtags/groups", tags=["hashtags"])
    def hashtag_group_create(payload: dict = Body(...)):
        return g.save_hashtag_group(core, payload)

    @app.patch("/api/hashtags/groups/{gid}", tags=["hashtags"])
    def hashtag_group_update(gid: str, payload: dict = Body(...)):
        return g.save_hashtag_group(core, payload, gid)

    @app.delete("/api/hashtags/groups/{gid}", tags=["hashtags"])
    def hashtag_group_delete(gid: str):
        return g.delete_hashtag_group(core, gid)

    # ---------------------------------------------------------------- CSV import
    @app.get("/api/import/csv/template", tags=["import"], response_class=PlainTextResponse)
    def csv_template(style: str = "huhqueue"):
        style = style if style in g.CSV_TEMPLATES else "huhqueue"
        name = "huhqueue-import-template.csv" if style == "huhqueue" else f"huhqueue-{style}-template.csv"
        return PlainTextResponse(g.CSV_TEMPLATES[style], media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.post("/api/import/csv/preview", tags=["import"])
    def csv_preview(payload: dict = Body(...)):
        return g.preview_csv(core, payload.get("csv") or "", payload)

    @app.post("/api/import/csv", tags=["import"])
    def csv_import(request: Request, payload: dict = Body(...)):
        actor = _approval_actor(request) if (payload.get("when") or "") != "draft" else None
        return g.import_csv(core, payload.get("csv") or "", payload, approval_by=actor)


def add_public_growth_routes(app: FastAPI, core) -> None:
    """Public, no login: short-link redirects and link-in-bio pages."""

    @app.api_route("/l/{code}", methods=["GET", "HEAD"], include_in_schema=False)
    def short_link(code: str, request: Request):
        url = g.resolve_click(core, code, request.headers.get("referer", ""), request.headers.get("user-agent", ""),
                              count=request.method == "GET")
        if not url:
            return HTMLResponse("<!doctype html><title>Link not found</title><h1>Link not found</h1>", status_code=404)
        return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer-when-downgrade"})

    add_public_bio_routes(app, core)


# --------------------------------------------------------------------------------------------- registry entries
for _a in (
    Action("campaign.create", "Create a campaign", "POST", "/api/campaigns", CAMPAIGN_BODY, ("name",), "write", "write",
           examples=("new campaign DevDay week from 2026-11-03 to 2026-11-07",), group="campaign", ui="Growth › Campaigns › New"),
    Action("campaign.get", "Campaign detail: posts across channels and stats", "GET", "/api/campaigns/{id}", {"id": S}, ("id",),
           examples=("how is the DevDay campaign doing?",), group="campaign", ui="Growth › Campaign"),
    Action("campaign.update", "Edit or rename a campaign (colour, dates)", "PATCH", "/api/campaigns/{id}", {"id": S, **CAMPAIGN_BODY}, ("id",),
           "write", "write", group="campaign", ui="Growth › Campaign › Edit"),
    Action("campaign.delete", "Delete a campaign (posts are kept, untagged)", "DELETE", "/api/campaigns/{id}", {"id": S}, ("id",),
           "write", "destructive", group="campaign", ui="Growth › Campaign › Delete"),
    Action("utm.defaults", "Read UTM defaults per channel", "GET", "/api/utm/defaults", group="utm", ui="Growth › Links › UTM defaults"),
    Action("utm.set_defaults", "Set a channel's UTM defaults ('*' = all channels; auto/shorten apply to every post)", "PUT",
           "/api/utm/defaults/{channel_id}",
           {"channel_id": S, "source": S, "medium": S, "campaign": S, "term": S, "content": S, "reset": B,
            "auto": {**B, "description": "'*' only: tag links in every post at publish"}, "shorten": {**B, "description": "'*' only: also use tracked short links"}}, ("channel_id",), "write", "write",
           group="utm", ui="Growth › Links › UTM defaults"),
    Action("utm.preview", "Preview UTM-tagged links per channel", "POST", "/api/utm/preview",
           {"url": S, "caption": S, "channel_ids": SA, "campaign": S, "utm": UTM}, ("channel_ids",), group="utm", ui="Composer › UTM"),
    Action("link.list", "List short links with clicks", "GET", "/api/links", {"q": S, "campaign": S, "source": S}, group="link",
           ui="Growth › Links"),
    Action("link.create", "Create a short link (/l/code)", "POST", "/api/links",
           {"url": S, "code": {**S, "description": "optional custom code"}, "title": S, "campaign": S, "utm": UTM}, ("url",), "write", "write",
           examples=("shorten https://huhbyte.ai/gpt6 for the DevDay campaign",), group="link", ui="Growth › Links › New"),
    Action("link.get", "Short link stats (daily clicks, referrers)", "GET", "/api/links/{id}", {"id": S, "days": I}, ("id",), group="link",
           ui="Growth › Links › row"),
    Action("link.update", "Edit a short link", "PATCH", "/api/links/{id}", {"id": S, "url": S, "title": S, "campaign": S}, ("id",),
           "write", "write", group="link", ui="Growth › Links › Edit"),
    Action("link.delete", "Delete a short link", "DELETE", "/api/links/{id}", {"id": S}, ("id",), "write", "destructive", group="link",
           ui="Growth › Links › Delete"),
    Action("automation.list", "List automations (recurring, evergreen, RSS, first comment)", "GET", "/api/automations",
           {"kind": {**S, "description": "recurring,evergreen,rss,first_comment"}}, group="automation", ui="Growth › Automations"),
    Action("automation.create", "Create an automation (recurring post, evergreen recycling, RSS feed or auto first comment)", "POST", "/api/automations",
           {"kind": {"type": "string", "enum": list(g.KINDS)}, "name": S, "channel_ids": SA, "config": AUTOMATION_CONFIG, "active": B},
           ("kind", "channel_ids"), "publish", "public",
           examples=("post 'Weekly AI recap' every Monday 9am on instagram", "always add 'Sources in bio' as the first comment on instagram",
                     "auto-post https://openai.com/blog/rss.xml to x as drafts"),
           group="automation", ui="Growth › Automations › New"),
    Action("automation.get", "Automation detail with the posts it made", "GET", "/api/automations/{id}", {"id": S}, ("id",),
           group="automation", ui="Growth › Automations"),
    Action("automation.update", "Edit, pause or resume an automation", "PATCH", "/api/automations/{id}",
           {"id": S, "name": S, "channel_ids": SA, "config": AUTOMATION_CONFIG, "active": B}, ("id",), "publish", "public",
           examples=("pause the RSS automation",), group="automation", ui="Growth › Automations › Pause / Edit"),
    Action("automation.delete", "Delete an automation", "DELETE", "/api/automations/{id}", {"id": S}, ("id",), "write", "destructive",
           group="automation", ui="Growth › Automations › Delete"),
    Action("automation.run", "Run an automation now", "POST", "/api/automations/{id}/run", {"id": S}, ("id",), "publish", "public",
           group="automation", ui="Growth › Automations › Run now"),
    Action("automation.preview", "What an automation would do now (dry run)", "GET", "/api/automations/{id}/preview", {"id": S}, ("id",),
           group="automation", ui="Growth › Automations › Preview"),
    Action("rss.check", "Check an RSS / Atom feed", "POST", "/api/rss/check", {"url": S, "template": S}, ("url",), group="automation",
           ui="Growth › Automations › RSS › Check feed"),
    Action("hashtag.stats", "Hashtag performance: uses, networks, average engagement", "GET", "/api/hashtags/stats", {"days": I},
           examples=("which hashtags perform best?",), group="hashtag", ui="Growth › Hashtags"),
    Action("hashtag.suggest", "Suggest hashtags for a caption (from your best performers and groups)", "GET", "/api/hashtags/suggest",
           {"caption": S, "limit": I}, group="hashtag", ui="Growth › Hashtags › Suggest"),
    Action("hashtag.groups", "List hashtag groups with usage and performance", "GET", "/api/hashtags/groups", group="hashtag",
           ui="Growth › Hashtags / Composer › Hashtags"),
    Action("hashtag.group_create", "Create a hashtag group", "POST", "/api/hashtags/groups",
           {"name": S, "text": {**S, "description": "hashtags separated by spaces"}, "tags": SA}, ("name",), "write", "write",
           examples=("save #AI #LLM #OpenAI as a hashtag group called LLMs",), group="hashtag", ui="Growth › Hashtags › New group"),
    Action("hashtag.group_update", "Edit a hashtag group", "PATCH", "/api/hashtags/groups/{id}", {"id": S, "name": S, "text": S, "tags": SA},
           ("id",), "write", "write", group="hashtag", ui="Growth › Hashtags › Edit"),
    Action("hashtag.group_delete", "Delete a hashtag group", "DELETE", "/api/hashtags/groups/{id}", {"id": S}, ("id",), "write", "destructive",
           group="hashtag", ui="Growth › Hashtags › Delete"),
    Action("import.csv_template", "Download the CSV import template", "GET", "/api/import/csv/template",
           {"style": {"type": "string", "enum": ["huhqueue", "buffer", "linkedin"],
                      "description": "huhqueue (all columns), buffer (Buffer's Text/Image URL/Tags/Posting Time) or linkedin"}},
           group="import", ui="Growth › Import › Template; Channel › Publish › Bulk upload › Template"),
    Action("import.csv_preview", "Preview a CSV bulk import (validation per row)", "POST", "/api/import/csv/preview",
           {"csv": {**S, "description": "CSV text"}, "channel_ids": SA, "when": S, "timezone": S}, ("csv",), group="import",
           ui="Growth › Import › Preview; Channel › Publish › Bulk upload"),
    Action("import.csv", "Bulk import posts from CSV", "POST", "/api/import/csv",
           {"csv": S, "channel_ids": {**SA, "description": "default channels for rows without a channels column"}, "when": S,
            "timezone": S, "skip_invalid": B}, ("csv",), "publish", "public", group="import",
           ui="Growth › Import › Import; Channel › Publish › Bulk upload (Queue or Drafts)"),
    Action("nav.growth", "Go to Growth (campaigns, links, bio, automations, import)", nav="#/growth", mcp=False, ui="⌘K"),
    Action("nav.campaigns", "Go to Campaigns", nav="#/growth/campaigns", mcp=False, ui="⌘K"),
    Action("nav.links", "Go to Short links", nav="#/growth/links", mcp=False, ui="⌘K"),
    Action("nav.bio", "Go to Link in bio", nav="#/growth/bio", mcp=False, ui="⌘K"),
    Action("nav.automations", "Go to Automations", nav="#/growth/automations", mcp=False, ui="⌘K"),
    Action("nav.import", "Go to CSV import", nav="#/growth/import", mcp=False, ui="⌘K"),
    Action("nav.hashtags", "Go to Hashtags", nav="#/growth/hashtags", mcp=False, ui="⌘K"),
):
    reg.register(_a)

# campaign.list already exists in the core registry; it now returns stats too
if reg.BY_ID.get("campaign.list"):
    reg.BY_ID["campaign.list"].title = "List campaigns with stats (posts, clicks, engagement)"
    reg.BY_ID["campaign.list"].ui = "Growth › Campaigns / Filter bar"
