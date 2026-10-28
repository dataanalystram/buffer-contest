"""Link-in-bio routes (v2) and their registry entries.

Private (login or token): pages CRUD, analytics, brand catalogue, QR code, subscribers, JSON export / import,
custom-domain check. Public (no login, also on the edge app): /bio/{slug}, the email signup POST, contact-card
vCards and the custom-domain root. Every private route has an Action below, so it is also a REST action, an MCP
tool, a ⌘K entry and a CLI command.
"""
from __future__ import annotations

import re

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import actions as reg
from ..actions import B, I, O, S, Action
from ..services import bio as bio_svc

NOT_FOUND = "<!doctype html><title>Not found</title><h1>Page not found</h1>"
BLOCKS = {"type": "array", "items": O, "description": (
    "ordered blocks: {type:link,label,url,highlight,spotlight,layout:classic|featured,thumb} (url '' = still being set up, not shown) |{type:brand,brand,label?,url} (brand keys from bio.brands) | "
    "{type:image,src,alt,caption?,url?} | {type:embed,url} (YouTube or Spotify) | {type:spacer,size:s|m|l,divider} | "
    "{type:email,title,text?,button,placeholder,success,ask_name} | {type:contact,name,org,job_title,email,phone,website,address,note,button} | "
    "{type:posts,count,channel_ids} | {type:socials,items:[{network,url}],auto} | {type:text,text} | {type:heading,text}; "
    "keep a block's id to keep its click history; hidden:true hides it; start_at / end_at (ISO, UTC) schedule any block")}
THEME = {**O, "description": (
    "{preset (bio.themes lists them: auto, light, dark, lavender, night, lilac, mint, ocean, ...), accent '#RRGGBB', "
    "buttons: pill|rounded|square, style: solid|outline|soft|shadow|hard, font (bio.themes fonts; '' = the preset's), "
    "bg_type: ''|solid|gradient|image with bg_color '#RRGGBB' | bg_gradient (key) | bg_image (https), "
    "text_color, button_color, button_text_color ('#RRGGBB' or '' to follow the preset)}")}
SETTINGS = {**O, "description": ("{seo_title, seo_description, og_image, share_button, hide_branding, stats_public, "
                                 "socials: [{network,url}] (page icons), socials_position: top|bottom}")}
PAGE = {"slug": S, "title": S, "bio": S, "avatar_url": S, "theme": THEME, "blocks": BLOCKS, "published": B,
        "domain": {**S, "description": "custom domain that serves this page at / (links.example.com); '' removes it"}, "settings": SETTINGS}


def add_bio_routes(app: FastAPI, core) -> None:
    @app.get("/api/bio", tags=["bio"])
    def bio_list(request: Request):
        return bio_svc.list_bio(core, request)

    @app.post("/api/bio", tags=["bio"])
    def bio_create(request: Request, payload: dict = Body(...)):
        return bio_svc.save_bio(core, payload, None, request)

    @app.get("/api/bio/brands", tags=["bio"])
    def bio_brands():
        return {"count": len(bio_svc.brands_catalog()), "brands": bio_svc.brands_catalog(),
                "credit": "Brand button colours and icons from LittleLink (MIT). Logos are trademarks of their owners."}

    @app.get("/api/bio/themes", tags=["bio"])
    def bio_themes():
        return bio_svc.themes_catalog()

    @app.post("/api/bio/import", tags=["bio"])
    def bio_import(request: Request, payload: dict = Body(...)):
        return bio_svc.import_bio(core, payload, request)

    @app.get("/api/bio/{pid}", tags=["bio"])
    def bio_get(pid: str, request: Request, days: int = 30):
        return bio_svc.get_bio(core, pid, request, max(1, min(days, 365)))

    @app.patch("/api/bio/{pid}", tags=["bio"])
    def bio_update(pid: str, request: Request, payload: dict = Body(...)):
        return bio_svc.save_bio(core, payload, pid, request)

    @app.delete("/api/bio/{pid}", tags=["bio"])
    def bio_delete(pid: str):
        return bio_svc.delete_bio(core, pid)

    @app.post("/api/bio/{pid}/duplicate", tags=["bio"])
    def bio_duplicate(pid: str, request: Request, payload: dict = Body(default={})):
        data = bio_svc.export_bio(core, pid)
        slug = (payload.get("slug") or "").strip().lower() or f"{data['page']['slug']}-copy"[:40]
        return bio_svc.import_bio(core, {"data": data, "slug": slug, "published": False}, request)

    @app.get("/api/bio/{pid}/preview", tags=["bio"], response_class=HTMLResponse)
    def bio_preview(pid: str):
        p = bio_svc.get_bio(core, pid)
        page = bio_svc.render_bio(core, p["slug"], preview=True)
        return HTMLResponse(page, headers={"Cache-Control": "no-store", "X-Frame-Options": "SAMEORIGIN"})

    @app.post("/api/bio/{pid}/preview", tags=["bio"], response_class=HTMLResponse)
    def bio_preview_draft(pid: str, payload: dict = Body(default={})):
        page = bio_svc.preview_bio(core, pid, payload, str(payload.get("highlight") or ""))
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/api/bio/{pid}/export", tags=["bio"])
    def bio_export(pid: str, download: bool = False):
        data = bio_svc.export_bio(core, pid)
        if download:
            return JSONResponse(data, headers={"Content-Disposition": f"attachment; filename=bio-{data['page']['slug']}.json"})
        return data

    @app.get("/api/bio/{pid}/qr", tags=["bio"])
    def bio_qr(pid: str, request: Request, format: str = "json", scale: int = 8, dark: str = "", light: str = "#FFFFFF",  # noqa: A002
               download: bool = False):
        fmt = format if format in ("json", "svg", "png") else "json"
        out = bio_svc.qr(core, pid, request, fmt, scale, dark, light)
        if fmt == "json":
            return out
        p = bio_svc.get_bio(core, pid)
        headers = {"Content-Disposition": f"attachment; filename=bio-{p['slug']}-qr.{fmt}"} if download else {}
        return Response(out, media_type="image/png" if fmt == "png" else "image/svg+xml", headers=headers)

    @app.get("/api/bio/{pid}/subscribers", tags=["bio"])
    def bio_subscribers(pid: str, format: str = "json"):  # noqa: A002
        if format == "csv":
            slug, text = bio_svc.list_subscribers(core, pid, "csv")
            return Response(text, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=bio-{slug}-subscribers.csv"})
        return bio_svc.list_subscribers(core, pid)

    @app.delete("/api/bio/{pid}/subscribers/{sid}", tags=["bio"])
    def bio_subscriber_delete(pid: str, sid: str):
        return bio_svc.delete_subscriber(core, pid, sid)

    @app.get("/api/bio/{pid}/domain-check", tags=["bio"])
    def bio_domain_check(pid: str, request: Request):
        return bio_svc.domain_check(core, pid, request)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")


def add_public_bio_routes(app: FastAPI, core) -> None:
    """Public pages: no login. Added to the UI app and the edge app."""

    @app.middleware("http")
    async def bio_custom_domain(request: Request, call_next):
        if request.method in ("GET", "HEAD") and request.url.path == "/":
            slug = bio_svc.page_for_domain(core, request.headers.get("host", ""))
            if slug:
                page = bio_svc.render_bio(core, slug, flash=bio_svc.parse_flash(request.url.query))
                if page is not None:
                    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})
        return await call_next(request)

    @app.get("/bio/fonts/{name}", include_in_schema=False)
    def bio_font(name: str):
        f = bio_svc.STATIC / "fonts" / name
        if not re.fullmatch(r"[a-z0-9-]+-[47]00\.woff2", name) or not f.is_file():
            return HTMLResponse(NOT_FOUND, status_code=404)
        return Response(f.read_bytes(), media_type="font/woff2", headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/bio/vcard/{page_id}/{block_id}.vcf", include_in_schema=False)
    def bio_vcard(page_id: str, block_id: str):
        out = bio_svc.vcard(core, page_id, block_id)
        if not out:
            return HTMLResponse(NOT_FOUND, status_code=404)
        fn, text = out
        return Response(text, media_type="text/vcard; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fn}"'})

    @app.get("/bio/{slug}", include_in_schema=False, response_class=HTMLResponse)
    def bio_page(slug: str, request: Request):
        page = bio_svc.render_bio(core, slug, flash=bio_svc.parse_flash(request.url.query))
        if page is None:
            return HTMLResponse(NOT_FOUND, status_code=404)
        return HTMLResponse(page, headers={"Cache-Control": "no-cache"})

    @app.post("/bio/{slug}/subscribe", include_in_schema=False)
    async def bio_subscribe(slug: str, request: Request):
        form = dict(await request.form())
        status, _ = bio_svc.subscribe(core, slug, {k: str(v) for k, v in form.items()}, _client_ip(request),
                                      request.headers.get("referer", ""))
        if status == "missing":
            return HTMLResponse(NOT_FOUND, status_code=404)
        ref = request.headers.get("referer", "")
        back = "/" if re.match(r"^https?://[^/]+/?(\?.*)?$", ref) else f"/bio/{slug}"   # custom-domain root posts back to /
        anchor = f"#{re.sub(r'[^A-Za-z0-9_-]', '', str(form.get('block') or ''))}" if form.get("block") else ""
        return RedirectResponse(f"{back}?subscribed={status}{anchor}", status_code=303)


# --------------------------------------------------------------------------------------------- registry entries
for _a in (
    Action("bio.list", "List link-in-bio pages", "GET", "/api/bio", group="bio", ui="Growth › Link in bio"),
    Action("bio.create", "Create a link-in-bio page", "POST", "/api/bio", PAGE, ("slug",), "write", "public",
           examples=("make a link in bio page at /bio/huhbyte",), group="bio", ui="Growth › Link in bio › Create"),
    Action("bio.brands", "List the brand buttons (key, label, colours, icon) for brand blocks", "GET", "/api/bio/brands",
           group="bio", ui="Growth › Link in bio › Add block › Brand button"),
    Action("bio.themes", "Bio page design catalogue: theme presets, fonts, background gradients, button shapes and styles", "GET",
           "/api/bio/themes", group="bio", ui="Growth › Link in bio › Design"),
    Action("bio.import", "Create a bio page from a JSON export", "POST", "/api/bio/import",
           {"data": {**O, "description": "the object bio.export returned"}, "slug": S, "published": B}, ("data",), "write", "public",
           group="bio", ui="Growth › Link in bio › Import JSON"),
    Action("bio.get", "Read a link-in-bio page with analytics (views, clicks, signups, CTR per day; clicks per block, referrer, device)",
           "GET", "/api/bio/{id}", {"id": S, "days": I}, ("id",), examples=("how many clicks did my link in bio get?",), group="bio",
           ui="Growth › Link in bio › Analytics"),
    Action("bio.update", "Edit a link-in-bio page (blocks, theme, SEO, custom domain, publish)", "PATCH", "/api/bio/{id}",
           {"id": S, **PAGE}, ("id",), "write", "public",
           examples=("add a Spotify button to my bio page", "switch my bio page to the auto light/dark theme"), group="bio",
           ui="Growth › Link in bio › Save"),
    Action("bio.delete", "Delete a link-in-bio page (and its email subscribers)", "DELETE", "/api/bio/{id}", {"id": S}, ("id",),
           "write", "destructive", group="bio", ui="Growth › Link in bio › Delete page"),
    Action("bio.duplicate", "Duplicate a bio page as an unpublished draft", "POST", "/api/bio/{id}/duplicate", {"id": S, "slug": S},
           ("id",), "write", "write", group="bio", ui="Growth › Link in bio › Duplicate"),
    Action("bio.preview", "Preview a bio page as HTML (also unpublished)", "GET", "/api/bio/{id}/preview", {"id": S}, ("id",), group="bio",
           ui="Growth › Link in bio › Preview"),
    Action("bio.preview_draft", "Render unsaved edits as the public page would look (nothing is saved)", "POST", "/api/bio/{id}/preview",
           {"id": S, "title": S, "bio": S, "avatar_url": S, "theme": THEME, "blocks": BLOCKS, "settings": SETTINGS,
            "highlight": {**S, "description": "block id to emphasise (the others are dimmed)"}}, ("id",), group="bio",
           ui="Growth › Link in bio › Live preview"),
    Action("bio.export", "Export a bio page as JSON (blocks, theme, settings)", "GET", "/api/bio/{id}/export", {"id": S, "download": B},
           ("id",), group="bio", ui="Growth › Link in bio › Export JSON"),
    Action("bio.qr", "QR code of the page's public URL (json: svg + png data URI; svg; png)", "GET", "/api/bio/{id}/qr",
           {"id": S, "format": {"type": "string", "enum": ["json", "svg", "png"]}, "scale": I, "dark": S, "light": S, "download": B},
           ("id",), examples=("give me a QR code for my bio page",), group="bio", ui="Growth › Link in bio › QR code"),
    Action("bio.subscribers", "Email signups from a bio page (json, or format=csv)", "GET", "/api/bio/{id}/subscribers",
           {"id": S, "format": {"type": "string", "enum": ["json", "csv"]}}, ("id",), examples=("who signed up on my bio page?",),
           group="bio", ui="Growth › Link in bio › Subscribers"),
    Action("bio.subscriber_delete", "Remove one email subscriber", "DELETE", "/api/bio/{id}/subscribers/{sid}", {"id": S, "sid": S},
           ("id", "sid"), "write", "destructive", group="bio", ui="Growth › Link in bio › Subscribers › Remove"),
    Action("bio.domain_check", "Custom domain: DNS steps and whether it already points here", "GET", "/api/bio/{id}/domain-check",
           {"id": S}, ("id",), group="bio", ui="Growth › Link in bio › Custom domain › Check"),
):
    reg.register(_a)
