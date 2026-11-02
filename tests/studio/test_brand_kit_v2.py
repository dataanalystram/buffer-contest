"""Brand kit v2 (owner, 2 Nov 2026): a website's design system -> tokens -> DESIGN.md -> a generated theme, so kits from
different sites look clearly different; the Templates tab's kit list; HuhByte AI for every user; the "Notion" -> "Nothing"
swap. Fixture HTML/CSS and a recorded context.dev response only: nothing here reaches the network."""
from __future__ import annotations

from studio import brand_extract as X
from studio import brand_kits, brand_onboard, design_system as DS, templates as T
from studio.media import captions

SERIF_HTML = """<html><head><title>Quill</title><meta property="og:site_name" content="Quill">
<link rel="preload" href="/fonts/Garamond-Regular.woff2" as="font"></head><body><h1>Write slower.</h1></body></html>"""
SERIF_CSS = """:root{--bg:#fffeeb;--ink:#1a1a1a;--brand:#034f46}
body{background:var(--bg);color:var(--ink);font-family:Figtree,Arial,sans-serif}
h1{font-family:"EB Garamond",Georgia,serif;font-size:4.5rem} .hero-title{font-family:"EB Garamond",serif}
.button{border-radius:8px;background:var(--brand)} .w-button{color:#3898ec}"""
PLAY_HTML = """<html><head><title>Tube</title><link href="https://fonts.googleapis.com/css2?family=Roboto:wght@400;700" rel="stylesheet">
</head><body><h1>Watch</h1></body></html>"""
PLAY_CSS = """:root{--brand-red:#e1002d} body{background:#fff;color:#0f0f0f;font-family:"Tube Sans",Roboto,sans-serif}
.btn-primary{border-radius:18px;background:var(--brand-red)} .chip-button{border-radius:50%}"""
DARK_CSS = """:root{--color-bg-primary:#08090a;--color-text-primary:#f7f8f8;--color-brand:#5e6ad2}
body{background:var(--color-bg-primary);font-family:"Inter Variable",sans-serif} .button{border-radius:9999px}"""
STYLEGUIDE = {"styleguide": {"mode": "light", "colors": {"accent": "#000000", "background": "#f5f5ee", "text": "#16140f"},
              "typography": {"headings": {"h1": {"fontFamily": "Outfit", "fontSize": "56px", "fontWeight": 600}},
                             "p": {"fontFamily": "Source Serif 4", "fontSize": "18px"}},
              "components": {"button": {"primary": {"borderRadius": "9999px"}}, "card": {"borderRadius": "8px"}},
              "fontLinks": {"Outfit": {}, "Source Serif 4": {}}}}


def _kit(url, html, css):
    got = X.extract(url, html, [css])
    DS.build(url, html, [css], got)
    return got["kit"]


def _csrf(client):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]


def test_a_serif_site_gets_the_editorial_layout_its_garamond_and_a_design_md(monkeypatch):
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    k = _kit("https://quill.test/", SERIF_HTML, SERIF_CSS)
    th, d = k["theme"], k["design"]
    assert th["layout"] == "editorial" and th["mode"] == "light" and k["font"] == "garamond"
    assert k["colors"]["accent"] != "#3898EC"  # Webflow's default link blue is never the brand
    assert d["engine"] == "huhqueue-css" and d["site"] == "quill.test"
    assert "Style taken from quill.test" in d["md"] and "editorial" in d["md"] and "EB Garamond" in d["md"]


def test_a_rounded_vivid_light_site_is_playful_in_roboto_and_a_dark_inter_site_is_minimal(monkeypatch):
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    p = _kit("https://tube.test/", PLAY_HTML, PLAY_CSS)
    assert p["theme"]["layout"] == "playful" and p["font"] == "roboto" and p["theme"]["radius"] >= 14
    d = _kit("https://lin.test/", "<html><head><title>Lin</title></head><body></body></html>", DARK_CSS)
    assert d["theme"]["layout"] == "minimal" and d["theme"]["mode"] == "dark" and d["font"] == "inter"


def test_context_dev_tokens_win_for_type_and_radius_but_the_brand_accent_survives_black_buttons():
    got = {"kit": {"colors": {"primary": "#F5F5EE", "accent": "#FF6600", "text": "#111114"}}}
    t = DS.tokens_from_styleguide(STYLEGUIDE, got)
    assert t["engine"] == "context.dev" and t["colors"]["accent"] == "#FF6600"  # YC's buttons are black; its brand is orange
    assert t["radius"]["button"] == 999 and t["type"]["h1_px"] == 56
    th = DS.theme_from_tokens(t)
    assert th["head_font"] == "figtree" and th["radius_kind"] == "pill" and th["layout"] in ("minimal", "playful")


def test_build_uses_context_dev_only_when_the_key_is_set(monkeypatch):
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    calls = []
    monkeypatch.setattr(DS, "context_dev_styleguide", lambda url, http=None: calls.append(url) or STYLEGUIDE)
    css = [":root{--brand:#ff6600} body{background:#f5f5ee}"]
    got = X.extract("https://yc.test/", "<html><title>YC</title></html>", css)
    DS.build("https://yc.test/", "", [], got)
    assert not calls and got["kit"]["design"]["engine"] == "huhqueue-css"
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "ctxt_test_not_a_key")
    got = X.extract("https://yc.test/", "<html><title>YC</title></html>", css)
    DS.build("https://yc.test/", "", [], got)
    assert calls and got["kit"]["design"]["engine"] == "context.dev" and got["kit"]["colors"]["primary"] == "#F5F5EE"
    assert "ctxt_test_not_a_key" not in got["kit"]["design"]["md"]


def test_the_four_layouts_draw_four_different_cards_and_a_kit_without_a_theme_draws_as_before():
    base = {"name": "Acme", "handle": "acme", "colors": {"primary": "#FFFFFF", "accent": "#E1002D", "text": "#111114"}}
    seen = []
    for layout, font in (("bold_news", "anton"), ("editorial", "garamond"), ("minimal", "inter"), ("playful", "roboto")):
        k = {**base, "font": font, "body_font": "inter", "theme": {"layout": layout, "radius": 16, "surface": "#F2F2F2"}}
        b = brand_onboard.brand_for(k, None)
        assert b.layout == layout
        im = T.front_card(b, "4:5", "Update", "Three things we shipped", "And one more.", n=(1, 3))
        assert im.size == (1080, 1350) and T.end_card(b, "4:5").size == (1080, 1350)
        assert T.front_card(b, "16:9", "New", "A thumbnail", "").size == (1600, 900)
        seen.append(im.resize((54, 68)).tobytes())
    assert len(set(seen)) == 4
    old = brand_onboard.brand_for({**base, "font": "inter"}, None)
    assert old.layout == "" and old.radius is None


def test_clean_theme_drops_junk_and_bounds_numbers():
    assert DS.clean_theme({"layout": "nope"}) is None
    th = DS.clean_theme({"layout": "playful", "radius": 999, "scale": 9, "surface": "javascript:x", "evil": 1})
    assert th["radius"] == 48 and th["scale"] == 1.25 and "surface" not in th and "evil" not in th


def test_demo_kits_nothing_stays_classic_huhbyte_is_bold_and_samples_get_themes(client):
    _csrf(client)
    lst = client.get("/api/brand-kits").json()
    names = {k["name"]: k for k in lst["kits"]}
    assert not names["Nothing"]["kit"].get("theme")  # the owner likes Nothing as it is: no generated theme
    assert names["Nothing"]["kit"]["front_card"]["style"] == "grid"
    assert names["HuhByte AI"]["kit"]["theme"]["layout"] == "bold_news" and names["HuhByte AI"]["kit"]["font"] == "anton"
    assert set(lst["layouts"]) == {"bold_news", "editorial", "minimal", "playful"}
    assert any(e["id"] == "huhbyte" for e in lst["examples"])
    r = client.post(f"/api/brand-kits/{names['HuhByte AI']['id']}/activate", json={}).json()
    assert r["active"] and r["applied"]
    k = client.get("/api/brand-studio").json()["kit"]
    assert k["name"] == "HuhByte AI" and k["theme"]["layout"] == "bold_news"
    n = client.post("/api/brand-kits", json={"sample": "notion", "activate": True}).json()
    assert n["kit"]["theme"]["layout"] == "minimal" and n["active"]
    md = client.get(f"/api/brand-kits/{n['id']}/design.md")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown") and "minimal" in md.text


def test_a_draft_from_a_url_saves_its_theme_and_design_md_and_the_layout_picker_works(client, monkeypatch):
    monkeypatch.delenv("CONTEXT_DEV_API_KEY", raising=False)
    _csrf(client)
    d = _kit("https://quill.test/", SERIF_HTML, SERIF_CSS)
    body = {"name": "Quill", "colors": {"background": d["colors"]["primary"], "primary": d["colors"]["accent"], "text": d["colors"]["text"]},
            "head_font": d["font"], "body_font": d["body_font"], "theme": d["theme"], "design": d["design"], "activate": True}
    r = client.post("/api/brand-kits", json=body).json()
    assert r["kit"]["theme"]["layout"] == "editorial" and r["kit"]["design"]["site"] == "quill.test"
    assert "Style taken from quill.test" in client.get(r["kit"]["design"]["url"]).text
    r2 = client.patch(f"/api/brand-kits/{r['id']}", json={"layout": "playful"}).json()
    assert r2["kit"]["theme"]["layout"] == "playful"
    p = client.post("/api/brand-kits/preview", json={"kit_id": r["id"], "part": "kit", "width": 240, "inline": True}).json()
    assert all(c.get("url", "").startswith("data:image/") for c in p["cards"])


def test_template_previews_follow_the_active_kit(client):
    _csrf(client)
    kits = {k["name"]: k["id"] for k in client.get("/api/brand-kits").json()["kits"]}
    outs = []
    for name in ("Nothing", "HuhByte AI"):
        client.post(f"/api/brand-kits/{kits[name]}/activate", json={})
        outs.append(client.post("/api/brand-studio/preview", json={"template": "launch", "service": "instagram", "width": 200, "max": 1}).json()["slides"][0])
    assert outs[0] != outs[1]


def test_every_signed_in_user_gets_huhbyte_ai_without_losing_their_active_kit(human):
    mine = human.post("/api/brand-kits", json={"name": "Mine", "activate": True}).json()
    lst = human.get("/api/brand-kits").json()
    hb = [k for k in lst["kits"] if k["name"] == "HuhByte AI"]
    assert len(hb) == 1 and not hb[0]["active"] and hb[0]["kit"]["theme"]["layout"] == "bold_news"
    assert lst["active_id"] == mine["id"]
    assert human.get("/api/brand-studio").json()["kit"]["name"] == "Mine"
    human.delete(f"/api/brand-kits/{hb[0]['id']}")  # seeded once: deleting it is respected
    assert not [k for k in human.get("/api/brand-kits").json()["kits"] if k["name"] == "HuhByte AI"]
    again = human.post("/api/brand-kits", json={"example": "huhbyte", "activate": True}).json()
    assert again["active"] and again["name"] == "HuhByte AI"


def test_typed_text_keeps_notion_with_the_nothing_kit_active():
    w = lambda s: [{"w": x, "s": i, "e": i + 0.4} for i, x in enumerate(s.split())]  # noqa: E731
    assert " ".join(x["w"] for x in captions.fix_names(w("the Notion template was so good"), ["Nothing", "HuhByte"])) == \
        "the Notion template was so good"
    assert captions.fix_names(w("notion is great"), ["Nothing"])[0]["w"] == "notion"
    assert [x["w"] for x in captions.fix_names(w("try So vocal today"), ["Subvocal"])] == ["try", "Subvocal", "today"]


def test_new_fonts_are_bundled_with_their_licences():
    for f in ("EBGaramond-SemiBold.ttf", "SourceSerif4-SemiBold.ttf", "SourceSerif4-Regular.ttf", "Roboto-Bold.ttf", "Roboto-Regular.ttf"):
        assert (T.FONT_DIR / f).is_file()
    for lic in ("OFL-EBGaramond.txt", "OFL-SourceSerif4.txt", "OFL-Roboto.txt"):
        assert "Open Font License" in (T.FONT_DIR / lic).read_text()
    assert brand_kits.FONT_LABELS["garamond"] == "EB Garamond"
