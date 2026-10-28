"""Bio pages v2: brand buttons, new blocks, themes, QR, analytics per block/device, subscribers, vCard, export/import,
SEO/share, custom domain, registry/MCP parity for every bio route."""
import base64

import pytest

from huhqueue import actions as reg
from huhqueue.services import bio as bio_svc

MOBILE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile/15E148"
DESKTOP = "Mozilla/5.0 (X11; Linux x86_64) Chrome/120"

BLOCKS = [
    {"type": "brand", "brand": "spotify", "url": "https://open.spotify.com/show/abc"},
    {"type": "brand", "brand": "youtube", "label": "My channel", "url": "https://youtube.com/@huhbyte"},
    {"type": "image", "src": "https://huhbyte.ai/cover.jpg", "alt": "Cover", "url": "https://huhbyte.ai/shop"},
    {"type": "embed", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    {"type": "embed", "url": "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"},
    {"type": "spacer", "size": "l", "divider": True},
    {"type": "email", "title": "AI in 5 minutes", "button": "Join", "ask_name": True},
    {"type": "contact", "name": "Ram Somaraju", "org": "HuhByte AI", "job_title": "Founder", "email": "ram@huhbyte.ai",
     "phone": "+353 89 000 0000", "website": "https://huhbyte.ai"},
    {"type": "socials", "items": [{"network": "github", "url": "https://github.com/x"}, {"network": "email", "url": "hi@huhbyte.ai"}]},
]


def _page(api, slug="ram", **kw):
    r = api.post("/api/bio", {"slug": slug, "title": "Ram", "blocks": BLOCKS, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_brand_catalogue_from_littlelink(api):
    d = api.get("/api/bio/brands").json()
    assert d["count"] >= 90 and "LittleLink" in d["credit"]
    keys = {b["key"]: b for b in d["brands"]}
    assert {"spotify", "yt", "instagram", "github", "default"} <= set(keys) and "youtube" in keys["yt"]["aliases"]
    assert keys["spotify"]["icon"] == "/static/bio/icons/spotify.svg" and keys["spotify"]["background"].startswith("#")
    assert api.get("/static/bio/icons/spotify.svg").status_code == 200
    assert api.get("/static/bio/brands.css").status_code == 200


def test_new_blocks_render_on_edge(api, edge):
    p = _page(api)
    types = [b["type"] for b in p["blocks"]]
    assert types == ["brand", "brand", "image", "embed", "embed", "spacer", "email", "contact", "socials"]
    b0 = p["blocks"][0]
    assert b0["label"] == "Listen on Spotify" and b0["link_id"] and b0["short_url"].split("/l/")[1]
    assert p["blocks"][3]["embed_url"] == "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"
    assert p["blocks"][4]["embed_url"].startswith("https://open.spotify.com/embed/playlist/") and p["blocks"][4]["height"] == 352
    html = edge.get("/bio/ram").text
    assert 'data-brand="spotify"' in html and ".brand.button-spotify{" in html and "data:image/svg+xml;base64," in html
    assert p["blocks"][1]["brand"] == "yt" and 'class="btn brand button-yt"' in html and "My channel" in html
    assert "youtube-nocookie.com/embed/dQw4w9WgXcQ" in html and "open.spotify.com/embed/playlist" in html
    assert 'data-block="email"' in html and 'action="/bio/ram/subscribe"' in html and 'name="website"' in html and 'name="name"' in html
    assert 'data-block="contact"' in html and "Save my contact" in html
    assert 'data-block="spacer"' in html and "<hr>" in html
    assert "mailto:hi@huhbyte.ai" in html
    assert "<script" not in html                       # still no JavaScript


@pytest.mark.parametrize("block, msg", [
    ({"type": "brand", "brand": "myspace", "url": "https://x.y"}, "Unknown brand"),
    ({"type": "brand", "brand": "github", "url": "javascript:alert(1)"}, "https://"),
    ({"type": "embed", "url": "https://vimeo.com/1"}, "YouTube"),
    ({"type": "image", "src": "data:image/png;base64,xx"}, "https://"),
    ({"type": "spacer", "size": "xl"}, "Spacer"),
    ({"type": "contact", "name": ""}, "name"),
    ({"type": "contact", "name": "A", "email": "nope"}, "email"),
    ({"type": "carousel"}, "Unknown block type"),
])
def test_block_validation(api, block, msg):
    r = api.post("/api/bio", {"slug": "bad", "blocks": [block]})
    assert r.status_code == 400 and msg in r.text


def test_themes_auto_light_dark_and_styles(api, edge):
    p = _page(api, theme={"preset": "auto", "style": "outline", "buttons": "rounded"})
    assert {k: p["theme"][k] for k in ("preset", "accent", "buttons", "style", "font", "bg_type")} == \
        {"preset": "auto", "accent": "#7C5CE0", "buttons": "rounded", "style": "outline", "font": "", "bg_type": ""}
    html = edge.get("/bio/ram").text
    assert "@media (prefers-color-scheme:dark)" in html and "color-scheme:light dark" in html and 'data-theme="auto"' in html
    assert api.patch(f"/api/bio/{p['id']}", {"theme": {"preset": "dark"}}).json()["theme"]["preset"] == "dark"
    assert "--bg:#121212" in edge.get("/bio/ram").text
    assert api.patch(f"/api/bio/{p['id']}", {"theme": {"style": "glow"}}).status_code == 400


def test_qr_code(api):
    p = _page(api)
    d = api.get(f"/api/bio/{p['id']}/qr").json()
    assert d["url"].endswith("/bio/ram") and d["svg"].lstrip().startswith("<svg") and d["png_data_uri"].startswith("data:image/png;base64,")
    assert base64.b64decode(d["png_data_uri"].split(",")[1])[:8] == b"\x89PNG\r\n\x1a\n"
    png = api.get(f"/api/bio/{p['id']}/qr", params={"format": "png", "download": "true"})
    assert png.headers["content-type"] == "image/png" and "bio-ram-qr.png" in png.headers["content-disposition"]
    svg = api.get(f"/api/bio/{p['id']}/qr", params={"format": "svg", "dark": "#000000"})
    assert svg.headers["content-type"].startswith("image/svg+xml") and "#000000" in svg.text.lower() or "#000" in svg.text


def test_clicks_per_block_and_device(api, edge):
    p = _page(api)
    code = p["blocks"][0]["short_url"].split("/l/")[1]
    img_code = p["blocks"][2]["short_url"].split("/l/")[1]
    edge.get("/bio/ram")
    edge.get(f"/l/{code}", headers={"user-agent": MOBILE, "referer": "https://instagram.com/"}, follow_redirects=False)
    edge.get(f"/l/{code}", headers={"user-agent": DESKTOP}, follow_redirects=False)
    edge.get(f"/l/{img_code}", headers={"user-agent": MOBILE}, follow_redirects=False)
    st = api.get(f"/api/bio/{p['id']}").json()["stats"]
    assert st["clicks"] == 3 and st["views"] == 1
    top = st["blocks"][0]
    assert top["id"] == p["blocks"][0]["id"] and top["type"] == "brand" and top["clicks"] == 2 and top["clicks_in_range"] == 2
    assert {r["device"]: r["clicks"] for r in st["devices"]} == {"mobile": 2, "desktop": 1}
    assert st["referrers"][0] == {"referrer": "direct", "clicks": 2}


def test_email_signup_flow(api, edge):
    p = _page(api)
    eb = next(b for b in p["blocks"] if b["type"] == "email")
    r = edge.post("/bio/ram/subscribe", data={"email": "Fan@Example.com", "name": "Fan", "block": eb["id"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/bio/ram?subscribed=ok#{eb['id']}"
    assert "You&#x27;re on the list" in edge.get("/bio/ram?subscribed=ok").text or "on the list" in edge.get("/bio/ram?subscribed=ok").text
    assert edge.post("/bio/ram/subscribe", data={"email": "fan@example.com"}, follow_redirects=False).headers["location"].startswith("/bio/ram?subscribed=exists")
    assert "subscribed=invalid" in edge.post("/bio/ram/subscribe", data={"email": "nope"}, follow_redirects=False).headers["location"]
    assert "subscribed=spam" in edge.post("/bio/ram/subscribe", data={"email": "bot@x.io", "website": "http://spam"}, follow_redirects=False).headers["location"]
    subs = api.get(f"/api/bio/{p['id']}/subscribers").json()
    assert subs["count"] == 1 and subs["items"][0]["email"] == "fan@example.com" and subs["items"][0]["name"] == "Fan"
    csv = api.get(f"/api/bio/{p['id']}/subscribers", params={"format": "csv"})
    assert csv.headers["content-type"].startswith("text/csv") and "fan@example.com" in csv.text
    st = api.get(f"/api/bio/{p['id']}").json()
    assert st["subscribers"] == 1 and st["stats"]["signups"] == 1
    sid = subs["items"][0]["id"]
    assert api.delete(f"/api/bio/{p['id']}/subscribers/{sid}").json()["deleted"] == sid
    assert api.get(f"/api/bio/{p['id']}/subscribers").json()["count"] == 0
    api.patch(f"/api/bio/{p['id']}", {"published": False})
    assert edge.post("/bio/ram/subscribe", data={"email": "a@b.co"}).status_code == 404


def test_signup_rate_limit(api, edge, monkeypatch):
    _page(api)
    monkeypatch.setattr(bio_svc, "SUBSCRIBE_LIMIT", (2, 600))
    bio_svc._hits.clear()
    locs = [edge.post("/bio/ram/subscribe", data={"email": f"u{i}@x.io"}, headers={"x-forwarded-for": "9.9.9.9"},
                      follow_redirects=False).headers["location"] for i in range(3)]
    assert "subscribed=ok" in locs[0] and "subscribed=ok" in locs[1] and "subscribed=limited" in locs[2]
    bio_svc._hits.clear()


def test_contact_card_vcard_counts_click(api, edge):
    p = _page(api)
    cb = next(b for b in p["blocks"] if b["type"] == "contact")
    code = cb["short_url"].split("/l/")[1]
    r = edge.get(f"/l/{code}", headers={"user-agent": MOBILE}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == f"/bio/vcard/{p['id']}/{cb['id']}.vcf"
    v = edge.get(r.headers["location"])
    assert v.headers["content-type"].startswith("text/vcard") and "Ram-Somaraju.vcf" in v.headers["content-disposition"]
    assert "BEGIN:VCARD" in v.text and "FN:Ram Somaraju" in v.text and "EMAIL;TYPE=INTERNET:ram@huhbyte.ai" in v.text and "ORG:HuhByte AI" in v.text
    assert api.get(f"/api/bio/{p['id']}").json()["stats"]["blocks"][0]["type"] == "contact"
    assert edge.get(f"/bio/vcard/{p['id']}/nope.vcf").status_code == 404


def test_export_import_duplicate(api):
    p = _page(api, theme={"preset": "light"}, settings={"seo_title": "Ram's links"})
    ex = api.get(f"/api/bio/{p['id']}/export").json()
    assert ex["kind"] == "huhqueue.bio" and ex["page"]["slug"] == "ram" and "link_id" not in ex["page"]["blocks"][0]
    dl = api.get(f"/api/bio/{p['id']}/export", params={"download": "true"})
    assert "bio-ram.json" in dl.headers["content-disposition"]
    assert api.post("/api/bio/import", {"data": ex}).status_code == 409          # slug taken
    n = api.post("/api/bio/import", {"data": ex, "slug": "ram2"}).json()
    assert n["slug"] == "ram2" and not n["published"] and n["theme"]["preset"] == "light" and n["settings"]["seo_title"] == "Ram's links"
    assert [b["type"] for b in n["blocks"]] == [b["type"] for b in p["blocks"]]
    assert n["blocks"][0]["link_id"] != p["blocks"][0]["link_id"]          # own tracked links
    dup = api.post(f"/api/bio/{p['id']}/duplicate", {}).json()
    assert dup["slug"] == "ram-copy" and not dup["published"]
    assert api.post("/api/bio/import", {"data": {"nope": 1}, "slug": "x3"}).status_code in (400, 200)


def test_seo_share_and_branding(api, edge):
    p = _page(api, settings={"seo_title": "Ram | AI", "seo_description": "News", "og_image": "https://huhbyte.ai/og.png",
                             "share_button": True, "hide_branding": True})
    html = edge.get("/bio/ram").text
    assert "<title>Ram | AI</title>" in html and 'property="og:image" content="https://huhbyte.ai/og.png"' in html
    assert 'aria-label="Share this page"' in html and "x.com/intent/post" in html and "Made with HuhQueue" not in html
    assert api.patch(f"/api/bio/{p['id']}", {"settings": {"og_image": "javascript:x"}}).status_code == 400


def test_custom_domain(api, app, edge):
    from fastapi.testclient import TestClient
    p = _page(api)
    assert api.patch(f"/api/bio/{p['id']}", {"domain": "not a domain"}).status_code == 400
    d = api.patch(f"/api/bio/{p['id']}", {"domain": "https://Links.HuhByte.ai/"}).json()
    assert d["domain"] == "links.huhbyte.ai" and d["domain_url"] == "https://links.huhbyte.ai/"
    other = api.post("/api/bio", {"slug": "other"}).json()
    assert api.patch(f"/api/bio/{other['id']}", {"domain": "links.huhbyte.ai"}).status_code == 409
    for client in (edge, TestClient(app)):
        r = client.get("/", headers={"host": "links.huhbyte.ai"})
        assert r.status_code == 200 and 'data-bio="ram"' in r.text
    assert 'data-bio="ram"' not in TestClient(app).get("/").text          # normal host still gets the app
    assert api.get(f"/api/bio/{p['id']}/qr").json()["url"] == "https://links.huhbyte.ai/"
    chk = api.get(f"/api/bio/{p['id']}/domain-check").json()
    assert chk["domain"] == "links.huhbyte.ai" and len(chk["steps"]) == 3 and "resolves" in chk
    r = edge.post("/bio/ram/subscribe", data={"email": "d@x.io"}, headers={"referer": "https://links.huhbyte.ai/"}, follow_redirects=False)
    assert r.headers["location"].startswith("/?subscribed=ok")
    assert api.patch(f"/api/bio/{p['id']}", {"domain": ""}).json()["domain"] == ""


def test_delete_removes_subscribers(api, edge):
    p = _page(api)
    edge.post("/bio/ram/subscribe", data={"email": "z@x.io"})
    assert api.delete(f"/api/bio/{p['id']}").json() == {"deleted": p["id"], "subscribers_deleted": 1}


def test_every_bio_route_is_an_mcp_tool(app):
    bio = [a for a in reg.ACTIONS if a.id.startswith("bio.") and a.method]
    assert len(bio) >= 14
    tools = set(reg.mcp_tool_names())
    assert all(a.tool in tools for a in bio)
    assert {"bio_qr", "bio_subscribers", "bio_brands", "bio_export", "bio_import", "bio_domain_check"} <= tools
    assert reg.find_route_action("GET", "/api/bio/brands").id == "bio.brands"
    assert reg.find_route_action("POST", "/api/bio/import").id == "bio.import"


def test_mcp_runs_bio_actions(api, channel):
    p = _page(api)
    r = api.post("/api/actions/bio.qr:run", {"input": {"id": p["id"]}}).json()
    assert r["status"] == "done" and r["output"]["svg"].lstrip().startswith("<svg")
    r = api.post("/api/actions/bio.update:run", {"input": {"id": p["id"], "theme": {"preset": "auto"}}, "dry_run": True}).json()
    assert r.get("confirm_token") or r.get("status") in ("preview", "confirm")


# ------------------------------------------------------------------ Linktree-style editor (feat/bio-linktree)
def test_preview_renders_draft_without_saving(api):
    p = _page(api)
    draft = {"title": "Draft title", "theme": {"preset": "ocean"}, "highlight": "x1",
             "blocks": [{"id": "x1", "type": "link", "label": "Unsaved link", "url": "https://huhbyte.ai/unsaved"},
                        {"id": "x2", "type": "link", "label": "Not set up", "url": "https://"}]}
    r = api.post(f"/api/bio/{p['id']}/preview", draft)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Draft title" in html and "Unsaved link" in html and 'data-theme="ocean"' in html
    assert "Not set up" not in html                                     # links without a URL are not shown
    assert '[data-id]:not([data-id="x1"])' in html and 'data-id="x1"' in html
    saved = api.get(f"/api/bio/{p['id']}").json()
    assert saved["title"] == "Ram" and saved["theme"]["preset"] == "lavender" and len(saved["blocks"]) == len(BLOCKS)
    assert not [ln for ln in api.get("/api/links").json() if "unsaved" in str(ln)]   # rolled back: no short link made
    assert api.post(f"/api/bio/{p['id']}/preview", {"blocks": [{"type": "link", "url": "javascript:x"}]}).status_code == 400
    assert reg.find_route_action("POST", f"/api/bio/{p['id']}/preview").id == "bio.preview_draft"
    assert reg.find_route_action("GET", "/api/bio/themes").id == "bio.themes"


def test_link_setup_state_layout_thumb_spotlight(api, edge):
    p = _page(api, blocks=[{"type": "link", "label": "", "url": ""},
                           {"type": "link", "label": "Shop", "url": "https://huhbyte.ai/shop", "layout": "featured",
                            "thumb": "https://huhbyte.ai/t.jpg", "spotlight": True},
                           {"type": "link", "url": "https://huhbyte.ai/news", "thumb": "/m/tok/a.png"}])
    b0, b1, _ = p["blocks"]
    assert b0["url"] == "" and "link_id" not in b0                     # saved as "needs setup"
    assert b1["layout"] == "featured" and b1["spotlight"] and b1["link_id"]
    html = edge.get("/bio/ram").text
    assert html.count('data-block="link"') == 2
    assert 'class="btn spot feat"' in html and 'src="https://huhbyte.ai/t.jpg"' in html
    assert 'class="th" src="/m/tok/a.png"' in html and ">huhbyte.ai</a>" in html     # label falls back to the host
    assert api.patch(f"/api/bio/{p['id']}", {"blocks": [{"type": "link", "url": "https://x.io", "layout": "grid"}]}).status_code == 400


def test_schedule_filters_blocks(api, edge):
    p = _page(api, blocks=[{"type": "link", "label": "Past", "url": "https://a.io", "end_at": "2020-01-01T00:00:00Z"},
                           {"type": "link", "label": "Future", "url": "https://b.io", "start_at": "2999-01-01T09:00"},
                           {"type": "link", "label": "Now", "url": "https://c.io", "start_at": "2020-01-01T00:00+02:00",
                            "end_at": "2999-01-01T00:00Z"}])
    assert p["blocks"][2]["start_at"] == "2019-12-31T22:00Z"
    html = edge.get("/bio/ram").text
    assert ">Now<" in html and ">Past<" not in html and ">Future<" not in html
    bad = {"blocks": [{"type": "text", "text": "x", "start_at": "2026-02-01", "end_at": "2026-01-01"}]}
    assert "after the start" in api.patch(f"/api/bio/{p['id']}", bad).text
    assert api.patch(f"/api/bio/{p['id']}", {"blocks": [{"type": "text", "start_at": "soon"}]}).status_code == 400


def test_custom_theme_fields_and_fonts(api, edge):
    cat = api.get("/api/bio/themes").json()
    keys = {t["key"] for t in cat["presets"]}
    assert len(keys) >= 12 and {"auto", "lavender", "ocean", "brutal"} <= keys
    assert {"shadow", "hard"} <= set(cat["styles"]) and len(cat["gradients"]) == 12 and len(cat["fonts"]) >= 8
    font = next(f for f in cat["fonts"] if f["key"] == "playfair-display")
    assert edge.get(font["files"][0]).headers["content-type"] == "font/woff2"
    assert edge.get("/bio/fonts/nope-400.woff2").status_code == 404 and edge.get("/bio/fonts/brands.css").status_code == 404
    p = _page(api, theme={"preset": "light", "font": "playfair-display", "bg_type": "gradient", "bg_gradient": "frost",
                          "button_color": "#ffe14d", "style": "hard"})
    assert p["theme"]["button_color"] == "#FFE14D"
    html = edge.get("/bio/ram").text
    assert "font-family:'Playfair Display'" in html and "/bio/fonts/playfair-display-700.woff2" in html
    assert "linear-gradient(160deg,#000428,#004E92)" in html and "--ink:#FFFFFF" in html          # auto ink on a dark gradient
    assert "--button-ink:#121212" in html and "box-shadow:4px 4px 0 var(--ink)" in html
    img = api.patch(f"/api/bio/{p['id']}", {"theme": {"bg_type": "image", "bg_image": "https://x.io/a b(1).jpg"}}).json()
    html = edge.get("/bio/ram").text
    assert img["theme"]["bg_type"] == "image" and "url(https://x.io/a%20b%281%29.jpg)" in html
    for bad in ({"font": "comic-sans"}, {"bg_type": "video"}, {"bg_type": "solid", "bg_color": ""}, {"text_color": "red"},
                {"bg_gradient": "rainbow"}, {"style": "glow"}):
        assert api.patch(f"/api/bio/{p['id']}", {"theme": bad}).status_code == 400, bad


@pytest.mark.parametrize("key", [k for k, t in bio_svc.THEMES.items() if "bg" in t])
def test_preset_contrast_is_wcag_aa(key):
    t = bio_svc.THEMES[key]
    for stop in bio_svc.hexes(t["bg"]):
        assert bio_svc.contrast(t["ink"], stop) >= 4.5, (key, "text", stop)
    assert bio_svc.contrast(t["button_ink"], t["button"]) >= 4.5, (key, "button")
    assert bio_svc.contrast(t["ink"], t["card"]) >= 4.5, (key, "card")       # outline / soft styles draw ink on card / page
    assert t["font"] in bio_svc.FONTS and t["buttons"] in bio_svc.BUTTONS and t["style"] in bio_svc.STYLES


def test_page_socials_top_and_bottom(api, edge):
    p = _page(api, settings={"socials": [{"network": "github", "url": "https://github.com/ram"}, {"network": "x", "url": "https://"}]})
    assert p["settings"]["socials"] == [{"network": "github", "url": "https://github.com/ram"}]
    html = edge.get("/bio/ram").text
    assert html.index('class="page-socials socials"') < html.index('data-block="brand"')
    api.patch(f"/api/bio/{p['id']}", {"settings": {"socials_position": "bottom"}})
    html = edge.get("/bio/ram").text
    assert html.index('class="page-socials socials"') > html.index('data-block="contact"')
    assert api.patch(f"/api/bio/{p['id']}", {"settings": {"socials_position": "left"}}).status_code == 400
    assert api.patch(f"/api/bio/{p['id']}", {"settings": {"socials": [{"network": "x", "url": "ftp://x"}]}}).status_code == 400


def test_avatar_upload_square_crop(api, tmp_path):
    from .conftest import make_image
    path = make_image(tmp_path / "wide.png", size=(900, 300))
    with open(path, "rb") as fh:
        r = api.post("/api/media", files={"files": ("wide.png", fh, "image/png")}, data={"crop": "square"})
    m = r.json()["media"][0]
    assert (m["width"], m["height"]) == (400, 400) and m["public_path"].startswith("/m/")
    assert _page(api, avatar_url=m["public_path"])["avatar_url"] == m["public_path"]
