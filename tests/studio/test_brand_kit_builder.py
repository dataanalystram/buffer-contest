"""Brand kits, the website extractor, the "Your brand" stills, the sample brands, and no HuhByte leakage into another
brand's renders (owner, 2 Nov 2026). Extraction runs on fixture HTML/CSS; nothing here reaches the network."""
from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from studio import brand_extract as X
from studio import brand_kits, brand_onboard, svgraster, templates as T

from .conftest import agent_token, mcp_call

LAV = (201, 172, 250)

ACME_HTML = """<!doctype html><html><head><title>Acme Rockets – Launch anything</title>
<meta name="application-name" content="Acme Rockets"><meta name="theme-color" content="#0B0D12">
<meta name="description" content="Rockets for small teams. You build the payload, we handle the launch.">
<meta name="twitter:site" content="@acmerockets">
<link rel="stylesheet" href="/app.css"><link rel="apple-touch-icon" href="/touch.png" sizes="180x180">
<style>:root{--font-heading:"Playfair Display",Georgia,serif}</style></head><body>
<header><a href="/" aria-label="Acme Rockets home"><svg viewBox="0 0 300 100" fill="currentColor" aria-label="Acme Rockets">
<path d="M10 90 L50 10 L90 90 Z"/><rect x="110" y="20" width="180" height="60" rx="8"/></svg></a></header>
<main><h1>Launch anything, this week</h1><p>Book a slot, upload your payload and watch it fly. No paperwork, no waiting lists, no surprises.</p>
<section class="customers"><svg aria-label="Globex logo" viewBox="0 0 400 100"><rect width="400" height="100"/></svg></section>
<a href="https://x.com/acmerockets">X</a></main></body></html>"""
ACME_CSS = """:root{--color-bg-primary:#0b0d12;--color-text-primary:#f5f6f7;--color-brand:#ff5a1f;--color-accent:var(--color-brand);
--plan-card-accent:#21b3ff;--font-sans:"Inter Variable",system-ui,sans-serif;--font-size-regular:1rem}
body{font-family:var(--font-sans);background:var(--color-bg-primary)} .btn{background:var(--color-brand)} h1{font-family:var(--font-heading)}"""
LIGHT_HTML = """<html><head><meta property="og:site_name" content="Paper Co"><title>Paper Co</title></head>
<body><h1>Write it down.</h1></body></html>"""
LIGHT_CSS = ":root{--brand-primary:#b0ec9c;--color-brand-dark:#213130;--color-text-primary:#213130} body{background:#fff}"


def test_extract_reads_name_handle_colours_fonts_logo_and_voice_from_fixture_html():
    got = X.extract("https://acme.test/", ACME_HTML, [ACME_CSS])
    k, p = got["kit"], got["provenance"]
    assert k["name"] == "Acme Rockets" and k["handle"] == "acmerockets"
    assert k["colors"]["primary"] == "#0B0D12" and k["colors"]["accent"] == "#FF5A1F" and k["colors"]["text"] == "#F5F6F7"
    assert "theme-color" in p["primary"] and "--color-brand" in p["accent"]
    assert got["fonts"]["head"]["family"] == "Playfair Display" and k["font"] == "serif"
    assert got["fonts"]["body"]["family"] == "Inter Variable" and k["body_font"] == "inter"
    assert got["logo_png"], "the header SVG is drawn to a PNG"
    logo = Image.open(io.BytesIO(got["logo_png"]))
    assert logo.width / logo.height > 2.5, "the wordmark, not the customer logo or a square icon"
    assert "acme rockets" in p["logo"].lower()
    assert "Launch anything" in k["voice"] and "talks to the reader (you)" in k["voice_notes"]


def test_a_pale_brand_colour_on_a_white_site_uses_the_dark_brand_colour_as_the_ground():
    k = X.extract("https://paper.test/", LIGHT_HTML, [LIGHT_CSS])["kit"]
    assert k["name"] == "Paper Co"
    assert k["colors"]["primary"] == "#213130" and k["colors"]["accent"] == "#B0EC9C" and k["colors"]["text"] == "#FFFFFF"


def test_css_colour_parsing_and_var_resolution():
    assert X.parse_css_color("hsl(176 20% 16%)")[0] == (33, 49, 48)
    assert X.parse_css_color("rgba(255, 0, 0, .5)") == ((255, 0, 0), 0.5)
    assert X.parse_css_color("#abc")[0] == (170, 187, 204)
    v = X.css_vars(":root{--a:var(--b, #000);--b:#123456}")
    assert X.resolve("var(--a)", v) == "#123456"
    assert X.map_font("Lyon Text")[0] == "serif" and X.map_font("NDot 57")[0] == "dot" and X.map_font("Stolzl")[0] == "figtree"


def test_svg_rasteriser_draws_paths_arcs_and_holes():
    svg = ('<svg viewBox="0 0 100 100"><path fill-rule="evenodd" d="M0 0h100v100H0z M30 30h40v40H30z"/>'
           '<circle cx="50" cy="50" r="5" fill="#ff0000"/></svg>')
    im = svgraster.rasterize(svg, height=100)
    assert im.size == (100, 100)
    assert im.getpixel((10, 10))[3] > 200 and im.getpixel((40, 40))[3] < 30, "the inner square is a hole"
    assert im.getpixel((50, 50))[:3] == (255, 0, 0)
    assert svgraster.rasterize("<svg><script>x</script></svg>") is None


# ------------------------------------------------------------------------------------------------ no HuhByte leakage
def _lav_pixels(im) -> int:
    sm = im.convert("RGB").resize((im.width // 3, im.height // 3))
    return sum(1 for p in sm.getdata() if abs(p[0] - LAV[0]) < 16 and abs(p[1] - LAV[1]) < 16 and abs(p[2] - LAV[2]) < 16)


def _red_logo() -> bytes:
    im = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle((0, 0, 299, 99), fill=(220, 30, 40, 255))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


@pytest.fixture
def drawn_text(monkeypatch):
    seen: list[str] = []
    real = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **k):
        seen.append(str(text))
        return real(self, xy, text, *a, **k)
    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    return seen


@pytest.mark.parametrize("tpl", list(T.TEMPLATES))
def test_every_carousel_template_renders_only_from_the_active_kit(tpl, drawn_text):
    kit = {"name": "Acme", "handle": "acme", "colors": {"primary": "#0B3D2E", "accent": "#FFB000", "text": "#FFFFFF"}, "font": "poppins",
           "front_card": {"style": "glow"}, "end_card": {"style": "bold", "cta": "Follow Acme"}}
    for ratio in ("4:5", "9:16"):
        for im in T.slides(tpl, None, T.Brand(kit, _red_logo()), ratio):
            assert _lav_pixels(im) == 0, f"{tpl} {ratio}: HuhByte lavender on another brand's render"
    text = " ".join(drawn_text).lower()
    assert "huhbyte" not in text and "huh byte" not in text and "ai news made simple" not in text


def test_a_kit_without_colours_falls_back_to_neutral_not_lavender():
    b = T.Brand({"name": "Plain"})
    assert b.accent != LAV
    from studio import brand
    assert brand.DEFAULT_KIT["colors"]["accent"].upper() != "#C9ACFA" and brand.DEFAULT_KIT["font"] != "anton"


def test_every_video_theme_card_in_the_gallery_follows_the_kit(drawn_text):
    kit = brand_kits.normalize({"name": "Acme", "handle": "acme", "colors": {"background": "#0B3D2E", "primary": "#FFB000",
                                                                           "accent": "#FF5A1F", "text": "#FFFFFF"}, "head_font": "figtree"})
    cards = brand_kits.theme_cards_for(kit, _red_logo(), W=270, H=480) + brand_kits.kit_cards(kit, _red_logo())
    from studio.media import styles
    assert len(cards) == len(styles.ALL_IDS) + 3 and not [c for c in cards if "error" in c]
    for c in cards:
        assert _lav_pixels(c["image"]) == 0, c["id"]
    text = " ".join(drawn_text).lower()
    assert "huhbyte" not in text


# ------------------------------------------------------------------------------------------------ kits over REST + MCP
def test_kit_lifecycle_rest_create_preview_activate_and_templates_follow(human):
    r = human.post("/api/brand-kits", json={"name": "Acme", "handle": "@acme", "colors": {"background": "#101820", "primary": "#FEE715",
                                                                                         "accent": "#FF5A1F", "text": "#FFFFFF"},
                                             "head_font": "anton", "body_font": "inter", "caption_style": "hype",
                                             "end_card": {"cta": "Follow Acme", "line": "Rockets weekly"}})
    assert r.status_code == 200, r.text
    kid = r.json()["id"]
    assert r.json()["kit"]["colors"] == {"primary": "#101820", "accent": "#FEE715", "secondary": "#FF5A1F", "text": "#FFFFFF"}
    assert r.json()["kit"]["builder_colors"]["primary"] == "#FEE715" and r.json()["kit"]["handle"] == "acme"
    pv = human.post("/api/brand-kits/preview", json={"kit": {**r.json()["kit"], "name": "Acme Live"}, "width": 160}).json()
    ids = [c["id"] for c in pv["cards"]]
    assert ids[:3] == ["carousel_cover", "thumbnail", "end_card"] and "crt" in ids and all(c.get("url", "").startswith("/api/brand-kits/preview-img/") for c in pv["cards"])
    act = human.post(f"/api/brand-kits/{kid}/activate").json()
    assert act["active"] and act["applied"]
    kit = human.get("/api/brand-studio").json()["kit"]
    assert kit["name"] == "Acme" and kit["colors"]["accent"] == "#FEE715" and kit["font"] == "anton" and kit["end_card"]["cta"] == "Follow Acme"
    human.patch(f"/api/brand-kits/{kid}", json={"colors": {"background": "#101820", "primary": "#00C2A8"}})
    assert human.get("/api/brand-studio").json()["kit"]["colors"]["accent"] == "#00C2A8", "editing the active kit restyles at once"
    lst = human.get("/api/brand-kits").json()
    assert lst["active_id"] == kid and len(lst["kits"]) == 1
    assert human.delete(f"/api/brand-kits/{kid}").status_code == 400  # never the active kit


def test_kits_over_mcp(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _, made = mcp_call(human, tok, "create_brand_kit", {"name": "Beta", "colors": {"background": "#FFFFFF", "primary": "#2563EB"}})
    assert made["kit"]["colors"]["accent"] == "#2563EB" and made["kit"]["colors"]["text"] == "#111114"
    _, lst = mcp_call(human, tok, "list_brand_kits")
    assert [k["name"] for k in lst["kits"]] == ["Beta"]
    _, up = mcp_call(human, tok, "update_brand_kit", {"kit_id": made["id"], "head_font": "serif", "end_cta": "Read more"})
    assert up["kit"]["font"] == "serif" and up["kit"]["end_card"]["cta"] == "Read more"
    _, act = mcp_call(human, tok, "set_active_brand_kit", {"kit_id": made["id"]})
    assert act["active"]
    _, pv = mcp_call(human, tok, "preview_brand_kit", {"kit_id": made["id"]})
    assert len(pv["cards"]) > 10 and pv["cards"][0]["url"].startswith("https://hq.test/m/")


def test_demo_workspace_opens_with_nothing_active_and_huhbyte_one_click_away(client):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    lst = client.get("/api/brand-kits").json()
    names = [k["name"] for k in lst["kits"]]
    assert names == ["Nothing", "HuhByte AI"]
    nothing = lst["kits"][0]
    assert nothing["active"] and nothing["kit"]["font"] == "dot" and nothing["kit"]["label"] == "Sample brand, not affiliated"
    assert nothing["kit"]["colors"]["primary"] == "#000000" and nothing["logo_url"] == "/static/brands/nothing/logo.png"
    assert client.get(nothing["logo_url"]).status_code == 200 and client.get(lst["kits"][1]["logo_url"]).status_code == 200
    hb = lst["kits"][1]
    client.post(f"/api/brand-kits/{hb['id']}/activate")
    assert client.get("/api/brand-studio").json()["kit"]["name"] == "HuhByte AI"


# ------------------------------------------------------------------------------------------------ onboarding + samples
def test_sample_brands_are_cached_and_the_demo_extracts_them_offline(client, monkeypatch):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    monkeypatch.setattr(brand_onboard.BrandOnboard, "_live", lambda *a, **k: pytest.fail("no network for a sample brand"))
    r = client.post("/api/brand/extract", json={"url": "https://www.notion.com"}).json()
    assert r["source"] == "sample" and r["draft"]["name"] == "Notion" and r["label"] == "Sample brand, not affiliated"
    pv = client.post("/api/brand/themes-preview", json={"kit": r["draft"], "logo": r["logo"]}).json()
    assert [s["ratio"] for s in pv["stills"]] == ["9:16", "4:5", "1:1"]
    sm = client.get("/api/brand/samples").json()["samples"]
    assert [s["id"] for s in sm] == ["nothing", "linear", "notion"]
    for s in sm:
        for u in list(s["renders"].values()) + s["carousel"] + [s["logo_url"]]:
            assert client.get(u).status_code == 200, u
    q = client.post("/api/brand/samples/linear/schedule", json={}).json()
    assert {x["channel_id"] for x in q["queued"]} == {"demo_ig", "demo_li"} and all(x["status"] == "sent_to_buffer" for x in q["queued"])


def test_scheduling_the_sample_story_is_refused_outside_the_sample(human):
    r = human.post("/api/brand/samples/linear/schedule", json={})
    assert r.status_code == 400 and "sample" in r.json()["error"]


def test_extract_brand_over_mcp_uses_the_extractor(human, monkeypatch):
    def fake_live(self, url):
        got = X.extract("https://acme.test/", ACME_HTML, [ACME_CSS])
        got["kit"]["website"] = url
        return got
    monkeypatch.setattr(brand_onboard.BrandOnboard, "_live", fake_live)
    tok = agent_token(human, policy="trusted", cap=50)
    _, ex = mcp_call(human, tok, "extract_brand", {"url": "acme.test"})
    assert ex["draft"]["name"] == "Acme Rockets" and ex["logo"] and ex["provenance"]["accent"].startswith("CSS variable")
    _, pv = mcp_call(human, tok, "preview_brand_themes", {"kit": ex["draft"], "logo": ex["logo"]})
    assert [s["label"] for s in pv["stills"]] == ["Reel cover", "Carousel cover", "Square post"]
    _, made = mcp_call(human, tok, "create_brand_kit", {"url": "acme.test", "activate": True})
    assert made["kit"]["name"] == "Acme Rockets" and made["active"]


# ------------------------------------------------------------------------------------------------ the builder's calls
def test_builder_list_has_fonts_body_fonts_and_the_sample_brands(human):
    lst = human.get("/api/brand-kits").json()
    assert lst["fonts"]["dot"].startswith("Doto") and lst["fonts"]["serif"] == "DM Serif Display"
    assert set(lst["body_fonts"]) == set(brand_kits.BODY_CHOICES)
    assert [s["id"] for s in lst["samples"]] == ["nothing", "linear", "notion"]
    assert all(s["label"] == "Sample brand, not affiliated" for s in lst["samples"])


def test_create_from_a_sample_brand_over_rest_and_mcp(human):
    r = human.post("/api/brand-kits", json={"sample": "linear"})
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["name"] == "Linear" and k["kit"]["label"] == "Sample brand, not affiliated" and k["logo_url"] == "/static/brands/linear/logo.png"
    assert k["kit"]["source"]["url"]  # where it was read from
    assert human.post("/api/brand-kits", json={"sample": "acme"}).status_code == 400
    tok = agent_token(human, policy="trusted", cap=50)
    _, made = mcp_call(human, tok, "create_brand_kit", {"sample": "notion", "activate": True})
    assert made["name"] == "Notion" and made["active"]
    assert human.get("/api/brand-studio").json()["kit"]["name"] == "Notion"


def test_preview_in_parts_kit_cards_then_every_theme(human):
    from studio.media import styles
    kit = {"name": "Acme", "colors": {"background": "#0B0D12", "primary": "#FF5A1F", "accent": "#21B3FF", "text": "#F5F6F7"},
           "head_font": "dot", "body_font": "inter", "caption_style": "hype", "end_text": "Rockets weekly", "end_cta": "Book a launch"}
    a = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "kit"}).json()
    assert [c["id"] for c in a["cards"]] == ["carousel_cover", "thumbnail", "end_card"] and a["part"] == "kit"
    assert all(c["group"] == "kit" and c["url"].startswith("/api/brand-kits/preview-img/") and c["url"].endswith(".webp")
               for c in a["cards"])
    img = human.get(a["cards"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"] == "image/webp" and "immutable" in img.headers["cache-control"]
    assert len(img.content) < 60_000, "a small WebP thumbnail, not a full-size render"
    inline = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "kit", "inline": True}).json()
    assert all(c["url"].startswith("data:image/webp;base64,") for c in inline["cards"])
    b = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "themes"}).json()
    assert [c["id"] for c in b["cards"]] == list(styles.ALL_IDS) and all(c.get("url") for c in b["cards"])
    again = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "themes"}).json()
    assert again["cached"], "switching back to a kit is instant"
    assert b["kit"]["end_card"]["cta"] == "Book a launch" and b["kit"]["end_card"]["line"] == "Rockets weekly"


def test_the_active_kits_caption_style_is_the_default_for_video_edits(human):
    house = human.app.state.studio.house
    a = type("A", (), {"uid": human.get("/api/session").json()["user"]["id"], "label": "you", "kind": "human"})()
    assert brand_kits.caption_style_for(house, a) == "opus"
    kid = human.post("/api/brand-kits", json={"name": "Calm", "caption_style": "minimal", "activate": True}).json()["id"]
    assert brand_kits.caption_style_for(house, a) == "minimal"
    human.patch(f"/api/brand-kits/{kid}", json={"caption_style": "news"})
    assert brand_kits.caption_style_for(house, a) == "news"


def test_a_sample_kit_saved_under_a_new_name_drops_the_sample_badge(human):
    k = human.post("/api/brand-kits", json={"sample": "nothing"}).json()
    assert k["kit"]["label"] == brand_onboard.SAMPLE_LABEL
    same = human.patch(f"/api/brand-kits/{k['id']}", json={"head_font": "inter"}).json()
    assert same["kit"]["label"] == brand_onboard.SAMPLE_LABEL, "still the sample brand: the badge stays"
    mine = human.patch(f"/api/brand-kits/{k['id']}", json={"name": "Acme Audio", "handle": "acmeaudio"}).json()
    assert mine["name"] == "Acme Audio" and not mine["kit"].get("label")
    assert mine["kit"]["source"]["derived_from"] == "Nothing"
    assert not mine["kit"].get("logo") and not mine["logo_url"], "the sample's logo isn't theirs: it goes with the name"
    still = human.post("/api/brand-kits", json={"sample": "nothing"}).json()
    assert still["kit"]["logo"] == "sample:nothing" and still["logo_url"], "the sample itself keeps its logo"
    human.delete(f"/api/brand-kits/{still['id']}")
    act = human.post(f"/api/brand-kits/{k['id']}/activate").json()
    assert act["applied"] and not human.get("/api/brand-studio").json()["kit"].get("label")
    lst = human.get("/api/brand-kits").json()
    assert not next(x for x in lst["kits"] if x["id"] == k["id"])["kit"].get("label")
    # created from a sample but named differently in the same call: never badged
    own = human.post("/api/brand-kits", json={"sample": "linear", "name": "Orbit"}).json()
    assert own["name"] == "Orbit" and not own["kit"].get("label") and own["kit"]["source"]["derived_from"] == "Linear"
    assert not own["kit"].get("logo")
    # the sample itself, renamed back to its own name, stays a sample
    lin = human.post("/api/brand-kits", json={"sample": "linear", "name": "linear"}).json()
    assert lin["kit"]["label"] == brand_onboard.SAMPLE_LABEL


def test_the_news_style_is_labelled_newsroom_and_keeps_its_id():
    from studio.media import styles
    assert styles.STYLE_PRESETS["huhbyte_news"]["label"] == "Newsroom"
    assert styles.resolve("newsroom") == "huhbyte_news" and styles.resolve("news") == "huhbyte_news"
    assert styles.resolve("huhbyte_news") == "huhbyte_news" and styles.known("huhbyte")
    assert styles.explicit("use the newsroom look") == "news"
    g = next(t for t in styles.describe_themes() if t["id"] == "huhbyte_news")
    assert g["label"] == "Newsroom" and "HuhByte" not in g["label"] + g["about"] + g["short"]
    assert "HuhByte" not in styles.pick_theme("a launch")[1]


def test_preview_cards_are_cached_one_by_one_in_memory_and_on_disk(human, tmp_path, monkeypatch):
    monkeypatch.setattr(brand_kits, "_disk_dir", str(tmp_path))
    brand_kits.cache_clear()
    kit = {"name": "Cachey", "colors": {"background": "#101820", "primary": "#FEE715", "accent": "#21B3FF", "text": "#FFFFFF"},
           "head_font": "anton"}
    first = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "themes", "ids": ["opus", "crt"]}).json()
    assert [c["id"] for c in first["cards"]] == ["opus", "crt"] and not first["cached"] and set(first["drawn_ms"]) == {"opus", "crt"}
    assert len([f for f in tmp_path.iterdir() if not f.name.startswith(".")]) == 2, "each drawn card goes to the disk cache"
    rest = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "themes"}).json()
    assert "opus" not in rest["drawn_ms"] and "crt" not in rest["drawn_ms"] and len(rest["cards"]) == 21
    with brand_kits._mem_lock:  # memory gone (a restart): the disk still answers and nothing is drawn
        brand_kits._mem.clear()
    again = human.post("/api/brand-kits/preview", json={"kit": kit, "part": "themes"}).json()
    assert again["cached"] and again["drawn_ms"] == {}
    assert human.get(again["cards"][0]["url"]).status_code == 200
    # an edit that changes the renders misses; one that doesn't (the voice) hits
    assert not human.post("/api/brand-kits/preview", json={"kit": {**kit, "head_font": "serif"}, "part": "themes", "ids": ["opus"]}).json()["cached"]
    assert human.post("/api/brand-kits/preview", json={"kit": {**kit, "voice": "calm"}, "part": "themes", "ids": ["opus"]}).json()["cached"]
    assert human.get("/api/brand-kits/preview-img/" + "0" * 40 + ".webp").status_code == 404
    assert human.get("/api/brand-kits/preview-img/..%2Fetc.webp").status_code == 404


def test_the_sample_kits_are_warmed_so_the_demo_builder_opens_from_the_cache(client, tmp_path, monkeypatch):
    monkeypatch.setattr(brand_kits, "_disk_dir", str(tmp_path))
    brand_kits.cache_clear()
    w = brand_kits.attach(client.app.state.studio.house).warm()
    assert w["cards"] == 2 * (3 + 21)
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    lst = client.get("/api/brand-kits").json()
    assert [t["id"] for t in lst["themes"]][:4] == ["opus", "huhbyte_news", "minimal", "hype"] and len(lst["kit_cards"]) == 3
    pre = lst["preview"]  # the active kit's cards ride on the list, one round trip early
    assert pre["kit_id"] == lst["active_id"] and [c["id"] for c in pre["cards"]] == ["carousel_cover", "thumbnail", "end_card"]
    assert all(client.get(c["url"]).status_code == 200 for c in pre["cards"])
    for kit in lst["kits"]:  # how the builder asks on landing: the saved kit by id
        k = client.post("/api/brand-kits/preview", json={"kit_id": kit["id"], "part": "kit", "width": 420}).json()
        t = client.post("/api/brand-kits/preview", json={"kit_id": kit["id"], "part": "themes", "width": 240}).json()
        assert k["cached"] and t["cached"], kit["name"]


def test_preview_images_do_not_eat_the_sample_rate_limit(client):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    kit = client.get("/api/brand-kits").json()["kits"][0]
    url = client.post("/api/brand-kits/preview", json={"kit_id": kit["id"], "part": "kit"}).json()["cards"][0]["url"]
    rate = client.app.state.rate
    before = sum(len(v) for v in rate._hits.values())
    for _ in range(30):
        assert client.get(url).status_code == 200
    assert sum(len(v) for v in rate._hits.values()) == before, "image reads are not counted against the per-IP budget"


def test_the_warm_up_waits_for_requests_and_never_holds_one_up(client, tmp_path, monkeypatch):
    """The start-up warm-up runs at low priority: while a request is drawing it draws nothing, and a request never
    queues behind a warm-up card (separate locks)."""
    import threading
    import time
    monkeypatch.setattr(brand_kits, "_disk_dir", str(tmp_path))
    monkeypatch.setattr(brand_kits, "WARM_YIELD_S", 0.2)
    brand_kits.cache_clear()
    kit = brand_kits.normalize({"name": "Warm Co", "colors": {"accent": "#ff5500"}})
    brand_kits._demand_add(1)  # a request is drawing
    done = {}
    t = threading.Thread(target=lambda: done.update(brand_kits.render_cards(kit, None, ["thumbnail"], 240, "webp", background=True)))
    t.start()
    time.sleep(0.6)
    assert not done and brand_kits.cache_get(brand_kits.card_key(kit, None, "thumbnail", 240, "webp")) is None
    brand_kits._demand_add(-1)  # the request finished: the warm-up goes on
    t.join(30)
    assert "bytes" in done["thumbnail"]
    with brand_kits._warm_lock:  # a warm-up card in flight does not block a request's card
        t0 = time.time()
        got = brand_kits.render_cards(kit, None, ["end_card"], 240, "webp")
        assert "bytes" in got["end_card"] and time.time() - t0 < 20
    assert not brand_kits.requests_drawing() or brand_kits._demand == 0
