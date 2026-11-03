"""Brand kit v2 polish (owner, 2 Nov 2026): YC and Wispr Flow came out nearly identical, the YC logo was tiny, the square
post's "In our own words" ran into "@ycombinator", pulled quotes read "type,just", the Templates tab drew its previews in
another kit's style, and deleted Library items lost their files for good. Fixtures only: nothing reaches the network."""
from __future__ import annotations

import io

from PIL import Image, ImageDraw

from studio import brand_extract as X, brand_kits, brand_onboard, design_system as DS, layouts, templates as T

# context.dev answers recorded for the two sites (trimmed to what the theme reads)
YC_SG = {"styleguide": {"mode": "light", "colors": {"accent": "#000000", "background": "#f5f5ee", "text": "#16140f"},
         "typography": {"headings": {"h1": {"fontFamily": "Outfit", "fontSize": "32px", "fontWeight": 700}},
                        "p": {"fontFamily": "Source Serif 4", "fontSize": "18px"}},
         "components": {"button": {"primary": {"backgroundColor": "#000000", "borderRadius": "9999px"}},
                        "card": {"backgroundColor": "#f5f5ee", "borderRadius": "8px"}}}}
WISPR_SG = {"styleguide": {"mode": "light", "colors": {"accent": "#034f46", "background": "#ffffeb", "text": "#1a1a1a"},
            "typography": {"headings": {"h1": {"fontFamily": "EB Garamond", "fontSize": "80px"}}, "p": {"fontFamily": "Figtree"}},
            "components": {"button": {"primary": {"backgroundColor": "#f0d7ff", "borderRadius": "8px"}}}}}
YC_GOT = {"kit": {"colors": {"primary": "#F5F5EE", "accent": "#FF6600", "text": "#111114", "secondary": "#0A2540"}}}
WISPR_GOT = {"kit": {"colors": {"primary": "#FFFFEB", "accent": "#FF6C4C", "text": "#1A1A1A", "secondary": "#034F46"}}}


def _theme(sg, got):
    return DS.theme_from_tokens(DS.tokens_from_styleguide(sg, got), got["kit"])


def test_yc_and_wispr_land_in_different_layouts_and_variants():
    yc, wf = _theme(YC_SG, YC_GOT), _theme(WISPR_SG, WISPR_GOT)
    assert (yc["layout"], yc["variant"]) == ("minimal", "bar")  # plain sans over a serif body, an orange bar: Hacker News
    assert (wf["layout"], wf["variant"]) == ("editorial", "masthead")
    assert wf["surface"].upper() == "#F0D7FF"  # Wispr's lavender button is its surface, not its accent
    t = DS.tokens_from_styleguide(WISPR_SG, WISPR_GOT)
    assert t["colors"]["accent"].lower() == "#034f46"  # the green context.dev reports, not the coral CTA


def test_two_serif_sites_still_get_two_compositions():
    base = {"mode": "light", "fonts": {"head": {"family": "EB Garamond"}, "body": {"family": "Figtree"}}, "radius": {"button": 8}}
    plain = DS.theme_from_tokens({**base, "colors": {"background": "#FAFAF7", "text": "#111111", "accent": "#FF6600"}})
    tinted = DS.theme_from_tokens({**base, "colors": {"background": "#FFFFEB", "surface": "#F0D7FF", "text": "#1A1A1A", "accent": "#034F46"}})
    assert plain["layout"] == tinted["layout"] == "editorial"
    assert plain["variant"] == "rules" and tinted["variant"] == "masthead"
    a = T.Brand({"name": "A", "colors": {"primary": "#FAFAF7", "text": "#111111", "accent": "#FF6600"}, "font": "garamond", "theme": plain})
    b = T.Brand({"name": "B", "colors": {"primary": "#FAFAF7", "text": "#111111", "accent": "#FF6600"}, "font": "garamond", "theme": tinted})
    ia, ib = (layouts.front(x, "4:5", "Update", "Three things we shipped").convert("L").resize((54, 67)) for x in (a, b))
    diff = sum(abs(p - q) for p, q in zip(ia.getdata(), ib.getdata())) / (54 * 67)
    assert diff > 6  # clearly different compositions, not a recolour


def test_clean_theme_keeps_a_valid_variant_only():
    assert DS.clean_theme({"layout": "minimal", "variant": "bar"})["variant"] == "bar"
    assert "variant" not in DS.clean_theme({"layout": "minimal", "variant": "masthead"})
    assert DS.theme_for_kit({"colors": {"primary": "#111114", "text": "#FFFFFF", "accent": "#C6B8FF"}, "font": "anton"})["variant"] == ""


def test_a_cta_or_error_colour_is_not_picked_over_the_brand_colour():
    css = """:root{--base-color--flare:#ff6c4c;--base-color--fathom:#034f46}
    .orange-strikethrough{color:#ff6c4c}.text-color-orange{color:#ff6c4c}.phone-error{border:1px solid #ff6c4c}
    .pricing-block{background-color:#034f46}.card-2{background-color:#034f46}.x{color:#034f46}"""
    pal = X.pick_palette("", X.css_vars(css), css)
    assert pal["colors"]["accent"] == "#034F46"


def _logo(w, h, pad=0, opaque=False):
    im = Image.new("RGBA" if not opaque else "RGB", (w + 2 * pad, h + 2 * pad), (255, 255, 255, 0) if not opaque else (255, 255, 255))
    ImageDraw.Draw(im).rectangle((pad, pad, pad + w - 1, pad + h - 1), fill=(10, 10, 10))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_logos_are_sized_by_their_visual_bounds():
    wide = T._open_logo(_logo(930, 93, pad=50))  # a 10:1 lockup with transparent padding
    assert wide.size == (930, 93)
    boxed = T._open_logo(_logo(300, 100, pad=80, opaque=True))  # a JPG-style logo on a white box
    assert boxed.size == (300, 100)
    w, h = T.logo_box(930, 93, 194)
    assert h >= 31 and w <= int(194 * 1.6) + 1  # YC's lockup: readable, never a 19 px sliver
    sw, sh = T.logo_box(200, 200, 194)
    assert sw == sh and 90 <= sh <= 100  # a square mark gets about the same visual area
    b = T.Brand({"name": "Y Combinator", "colors": {"primary": "#F5F5EE", "text": "#111114", "accent": "#FF6600"}}, _logo(930, 93, pad=50))
    im = Image.new("RGB", (1080, 1350), (245, 245, 238))
    assert T._logo(im, b, 194, (80, 80)) >= 31


def test_pulled_quotes_get_their_missing_spaces_back_and_nothing_else_changes():
    assert T.fix_spacing("Don\u2019t type,just speak.") == "Don\u2019t type, just speak."
    assert T.fix_spacing("Fast;simple!Try it.Now with AI") == "Fast; simple! Try it. Now with AI"
    for keep in ("Visit wisprflow.ai today", "e.g. this", "3.5x faster, 1,000 users", "mail hi@yc.com", "https://a.b/c,d",
                 "Node.js and Next.js", "Don\u2019t type, just speak."):
        assert T.fix_spacing(keep) == keep
    got = X.extract("https://w.test/", "<html><head><title>Flow</title></head><body><h1>Don\u2019t type,<br>just speak.</h1></body></html>", [""])
    assert "type,just" not in got["kit"]["voice"]


def test_the_square_post_footer_never_overlaps_a_long_name():
    for th in (_theme(YC_SG, YC_GOT), _theme(WISPR_SG, WISPR_GOT), {"layout": "playful", "radius": 20}, {"layout": "bold_news"},
               {"layout": "minimal", "variant": "line"}, {"layout": "editorial", "variant": "rules"}):
        kit = {"name": "Y Combinator", "handle": "ycombinator", "colors": {"primary": "#F5F5EE", "text": "#111114", "accent": "#FF6600"},
               "font": "figtree", "theme": th}
        b = T.Brand(kit)
        im = T.quote_card(b, "1:1", "Y Combinator created a new model for funding early stage startups. Four times a year we invest",
                          "Y Combinator Continuity Fund Partners", "In our own words")
        assert im.size == (1080, 1080)
    # classic (no theme) path too
    T.quote_card(T.Brand({"name": "N", "handle": "averyveryverylonghandle", "colors": {}}), "1:1", "x " * 80, "A very long speaker name here", "Role")


def test_template_preview_cards_use_the_kit_asked_for():
    nothing = brand_onboard.sample("nothing")["kit"]
    hb = brand_kits.example_kit("huhbyte")
    a = brand_kits.template_card(nothing, brand_onboard.sample_logo("nothing"), "launch")
    b = brand_kits.template_card(hb, None, "launch")
    assert a is not None and b is not None and list(a.getdata())[:500] != list(b.getdata())[:500]
    assert brand_kits.template_card(nothing, None, "no-such-template") is None


def _csrf(client):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]


def test_templates_tab_previews_come_from_the_active_kit_id_and_are_cached(client):
    _csrf(client)
    kits = {k["name"]: k["id"] for k in client.get("/api/brand-kits").json()["kits"]}
    client.post(f"/api/brand-kits/{kits['Nothing']}/activate", json={})
    body = {"kit_id": kits["Nothing"], "part": "templates", "ids": ["launch"], "width": 240}
    r1 = client.post("/api/brand-kits/preview", json=body).json()
    assert [c["id"] for c in r1["cards"]] == ["launch"] and r1["cards"][0]["group"] == "template"
    assert r1["cards"][0]["url"].startswith("/api/brand-kits/preview-img/")
    r2 = client.post("/api/brand-kits/preview", json=body).json()
    assert r2["cached"] and r2["cards"][0]["url"] == r1["cards"][0]["url"]
    hb = client.post("/api/brand-kits/preview", json={**body, "kit_id": kits["HuhByte AI"]}).json()
    assert hb["cards"][0]["url"] != r1["cards"][0]["url"]
    img = client.get(r1["cards"][0]["url"])
    assert img.status_code == 200 and img.headers["content-type"].startswith("image/")
    allc = client.post("/api/brand-kits/preview", json={"kit_id": kits["Nothing"], "part": "templates", "width": 240}).json()
    assert {c["id"] for c in allc["cards"]} == set(T.TEMPLATES)
