"""A brand kit looks the same in the builder's gallery and in a real render (2 Nov 2026): the same fonts, the same accent
rule (the kit's accent inside a theme whenever a kit is active, the theme's otherwise), and the builder's fourth colour,
Accent (colors.secondary), on the highlighted headline words and the theme progress bar."""
from studio import brand_kits, brand_onboard, brand_examples, templates
from studio.media import compose, styles, worker


def test_the_render_font_tables_are_the_builders():
    assert worker.KIT_HEAD_FONTS == templates.HEAD_FONTS
    assert worker.KIT_BODY_FONTS == templates.BODY_FONTS


def _kit(sample):
    return brand_kits.normalize({**brand_onboard.sample(sample)["kit"], "logo": ""})


def test_every_kit_font_reaches_the_renderer_like_the_gallery():
    kits = [_kit(s) for s in brand_onboard.SAMPLE_IDS] + [brand_kits.normalize(dict(e["kit"])) for e in brand_examples.EXAMPLES.values()]
    kits += [brand_kits.normalize({"name": "X", "font": f, "body_font": b}) for f in templates.HEAD_FONTS for b in brand_kits.BODY_CHOICES]
    for k in kits:
        g, r = brand_kits.compose_brand(k, None), worker.brand_from_kit(k)
        for key in ("head_font", "body_font", "bold_font"):
            assert r[key] == g[key], (k.get("name"), k.get("font"), k.get("body_font"), key)
    nothing = worker.brand_from_kit(_kit("nothing"))
    assert nothing["head_font"] == "Doto-Black.ttf", "Nothing's dot-matrix headline font is used in real renders"


def test_a_legacy_kit_without_a_body_font_keeps_its_old_bold():
    assert worker.brand_from_kit({"font": "anton"})["bold_font"] == "Poppins-Bold.ttf"
    assert worker.brand_from_kit({"font": "inter"})["bold_font"] == "Inter-Bold.ttf"
    assert "body_font" not in worker.brand_from_kit({"font": "anton"})


def test_the_builders_accent_marks_highlight_words_and_the_progress_bar():
    k = _kit("nothing")  # accent #D61821 (red), Accent/secondary #006479 (teal) on black
    b = worker.brand_from_kit(k)
    assert b["accent"] == "#D61821" and b["highlight"] == "#006479"
    assert brand_kits.compose_brand(k, None)["highlight"] == "#006479"
    assert compose._hl({**compose.DEFAULT_BRAND, **b}) == "#006479"
    assert compose._hl(dict(compose.DEFAULT_BRAND)) == compose.DEFAULT_BRAND["accent"], "no kit Accent: the brand accent"
    cd = styles.config("countdown")
    L = {"progress": (10, 10, 240, 6)}
    assert "0x006479@1" in compose._theme_text(cd, L, 10.0, "#006479")
    theme_acc = cd["palette"]["accent"].lstrip("#")
    assert f"0x{theme_acc}@1" in compose._theme_text(cd, L, 10.0, None)


def test_an_unreadable_accent_falls_back_to_the_brand_accent():
    assert styles.highlight_for({"primary": "#000000", "secondary": "#111111"}) is None
    assert "highlight" not in worker.brand_from_kit({"colors": {"primary": "#000000", "accent": "#FF0000", "secondary": "#0A0A0A"}})


def test_gallery_and_default_render_share_one_accent_rule():
    k = _kit("nothing")
    for sid in styles.ALL_IDS:
        cfg = styles.config(sid)
        if not cfg.get("theme"):
            continue
        gallery = styles.apply_palette(dict(brand_kits.compose_brand(k, None)), cfg, keep_brand=True)
        render = styles.apply_palette({**compose.DEFAULT_BRAND, **worker.brand_from_kit(k)}, cfg, keep_brand=True)
        assert gallery["accent"] == render["accent"] == "#D61821", sid
        assert compose._hl(gallery) == compose._hl(render), sid
        plain = styles.apply_palette({**compose.DEFAULT_BRAND, **worker.brand_from_kit(k)}, cfg, keep_brand=False)
        if (cfg.get("palette") or {}).get("accent"):
            assert plain["accent"] == plain["highlight"] == cfg["palette"]["accent"], sid


def test_a_default_render_keeps_the_kit_accent_only_with_an_active_kit(human):
    house = human.app.state.studio.house
    a = type("A", (), {"uid": human.get("/api/session").json()["user"]["id"], "label": "you", "kind": "human"})()
    assert brand_kits.brand_accent_wins(house, a) is False, "no kit: a theme brings its own accent"
    human.post("/api/brand-kits", json={"name": "Calm", "colors": {"primary": "#101010", "accent": "#FF5A1F"}, "activate": True})
    assert brand_kits.brand_accent_wins(house, a) is True, "an active kit: its accent stays inside every theme"


def test_a_renamed_sample_previews_without_the_sample_logo(human):
    k = brand_kits.normalize({**brand_onboard.sample("nothing")["kit"], "logo": "sample:nothing"})
    assert brand_kits.not_this_samples_logo("sample:nothing", "Nothing") is False
    assert brand_kits.not_this_samples_logo("sample:nothing", "") is False, "mid-edit keeps it"
    assert brand_kits.not_this_samples_logo("sample:nothing", "Acme") is True
    assert brand_kits.not_this_samples_logo("example:huhbyte", "Acme") is False
    same = human.post("/api/brand-kits/preview", json={"kit": k, "part": "kit", "format": "jpeg", "inline": True}).json()
    mine = human.post("/api/brand-kits/preview", json={"kit": {**k, "name": "Acme"}, "part": "kit", "format": "jpeg", "inline": True}).json()
    bare = human.post("/api/brand-kits/preview", json={"kit": {**k, "name": "Acme", "logo": ""}, "part": "kit", "format": "jpeg", "inline": True}).json()
    assert all(c.get("url") for c in same["cards"] + mine["cards"])
    assert [c["url"] for c in mine["cards"]] == [c["url"] for c in bare["cards"]], "drawn with no logo"
    assert [c["url"] for c in mine["cards"]] != [c["url"] for c in same["cards"]]
