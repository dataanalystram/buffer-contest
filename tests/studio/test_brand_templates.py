"""Brand templates and example brands (studio/templates.py, brand_studio.py, brand_examples.py): front card first and end
card last in every template and frame, per-channel slide caps, contrast guard, recap from the week's posts, the two
example brands, live preview, recurring slots that create Buffer DRAFTS on a real account, and the MCP tools."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import brand_examples, templates as T

from .conftest import CHANNELS, agent_token, mcp_call


def _brand(ex="huhbyte"):
    return T.Brand(brand_examples.EXAMPLES[ex]["kit"], brand_examples.logo_bytes(ex))


@pytest.mark.parametrize("tpl", list(T.TEMPLATES))
@pytest.mark.parametrize("ratio", ["4:5", "9:16", "16:9"])
def test_every_template_starts_with_the_front_card_and_ends_with_the_end_card(tpl, ratio):
    b = _brand()
    ims = T.slides(tpl, None, b, ratio)
    W, H = T.SIZES[ratio]
    assert all(im.size == (W, H) for im in ims)
    if tpl == "breaking":
        assert len(ims) == 1
        return
    from studio import hb_templates
    spec = T.TEMPLATES[tpl]
    if spec.get("kind") == "single":
        assert len(ims) == 1
        return
    end = hb_templates.save_card(b, ratio, n=(len(ims), len(ims))) if spec.get("end") == "save" else T.end_card(b, ratio, n=(len(ims), len(ims)))
    assert list(ims[-1].getdata())[::997] == list(end.getdata())[::997]  # the last slide IS the brand's end card (recaps: + SAVE THIS)
    if spec.get("front") == "editorial":  # the HuhByte formats open on the editorial cover (hero band, logo divider)
        cover = hb_templates.editorial_cover(b, ratio, "x", n=(1, len(ims)))
        assert ims[0].size == cover.size
    elif tpl != "quote":
        front = T.front_card(b, ratio, "x", "y")
        assert ims[0].getpixel((5, 5)) == front.getpixel((5, 5))  # same ground as the front card


def test_channel_caps_keep_the_end_card_last():
    b = _brand("buffer")
    data = {"stories": [{"title": f"Story {i}", "body": "x"} for i in range(9)]}
    ims = T.for_channel("weekly_recap", data, b, "twitter")
    assert len(ims) == 4 and ims[-1].size == T.SIZES["16:9"]
    from studio import hb_templates
    assert list(ims[-1].getdata())[::991] == list(hb_templates.save_card(b, "16:9", n=(4, 4)).getdata())[::991]


def test_uploaded_front_and_end_cards_replace_the_styles():
    import io
    buf = io.BytesIO()
    Image.new("RGB", (800, 1000), (10, 200, 30)).save(buf, "PNG")
    b = T.Brand(brand_examples.EXAMPLES["huhbyte"]["kit"], None, front=buf.getvalue(), end=buf.getvalue())
    ims = T.slides("tutorial", None, b, "4:5")
    assert ims[-1].getpixel((540, 675))[:3] == (10, 200, 30)
    assert ims[0].getpixel((540, 40))[1] > 150  # the uploaded image shows through at the top of the front card


def test_unreadable_text_colour_falls_back_to_black_or_white():
    b = T.Brand({"colors": {"primary": "#FFFFFF", "accent": "#C9ACFA", "text": "#FAFAFA"}})
    assert T.contrast(b.text, b.bg) >= 3


def test_buffer_example_is_labelled_not_affiliated_and_uses_buffers_public_palette():
    ex = brand_examples.EXAMPLES["buffer"]
    assert "not affiliated" in ex["kit"]["label"].lower()
    assert ex["kit"]["colors"]["primary"] == "#213130" and ex["kit"]["colors"]["accent"] == "#B0EC9C"
    logo = Image.open(__import__("io").BytesIO(brand_examples.logo_bytes("buffer")))
    assert logo.width > logo.height * 2  # mark + wordmark


def test_recap_stories_come_from_the_weeks_posts_once_per_story():
    now = datetime(2026, 10, 31, 20, tzinfo=timezone.utc)
    iso = lambda h: (now - timedelta(hours=h)).isoformat()
    posts = [{"text": "Gemini 4 Carbon is out. Google's next frontier model. https://x.y", "dueAt": iso(30), "service": "twitter"},
             {"text": "Gemini 4 Carbon is out: Google's next frontier model #AI", "dueAt": iso(29), "service": "linkedin"},
             {"text": "Mistral Large 4 lands on Hugging Face. Open weights.", "dueAt": iso(5), "service": "instagram"},
             {"text": "Too old to count. Really.", "dueAt": iso(24 * 9), "service": "twitter"}]
    st = T.stories_from_posts(posts, now - timedelta(days=7), now)
    assert [s["title"] for s in st] == ["Gemini 4 Carbon is out", "Mistral Large 4 lands on Hugging Face"]
    assert st[0]["channels"] == ["linkedin", "twitter"]


def test_next_fire_is_the_coming_monday_at_20_dublin_time():
    nf = T.next_fire("mon", "20:00", "Europe/Dublin", datetime(2026, 10, 31, 20, tzinfo=timezone.utc))
    assert nf == datetime(2026, 11, 2, 20, tzinfo=timezone.utc)


@pytest.mark.skipif(not T.have_ffmpeg(), reason="needs ffmpeg")
def test_reel_and_quote_clip_are_real_mp4s():
    b = _brand()
    mp4 = T.reel(T.slides("midweek_recap", None, b, "9:16")[:3], secs=0.8, end_secs=0.8)
    assert mp4[4:8] == b"ftyp" and len(mp4) > 10_000
    q = T.quote_clip(b, "Agents draft. People decide.", "HuhByte AI", wps=8)
    assert q[4:8] == b"ftyp"


# ---------------------------------------------------------------- through the app
def _demo(app):
    c = TestClient(app, base_url="https://hq.test")
    c.get("/demo")
    c.headers["X-HQ-CSRF"] = c.get("/api/session").json()["csrf"]
    return c


def test_sample_switches_between_the_two_example_brands_and_previews_live(app):
    c = _demo(app)
    ex = c.get("/api/brand-studio/examples").json()
    assert [e["id"] for e in ex["examples"]] == ["huhbyte", "buffer"]
    for eid, name in (("huhbyte", "HuhByte AI"), ("buffer", "Buffer")):
        k = c.post(f"/api/brand-studio/examples/{eid}").json()["kit"]
        assert k["name"] == name and k["example"] == eid and k["logo_url"]
    p = c.post("/api/brand-studio/preview", json={"template": "launch", "kit": {"colors": {"accent": "#FF5A1F"}}}).json()
    assert p["count"] >= 3 and p["slides"][0].startswith("data:image/jpeg;base64,")
    assert c.get("/static/examples/buffer/recap-01.jpg").status_code == 200
    assert c.get("/static/examples/buffer/../huhbyte/index.json").status_code in (404, 200)
    assert c.get("/static/examples/nope/x.jpg").status_code == 404
    r = c.post("/api/templates/render", json={"template": "breaking", "services": ["instagram", "twitter"]}).json()
    assert [ch["ratio"] for ch in r["channels"]] == ["4:5", "16:9"] and r["buffer_cost"] == {"requests": 0, "live": False}


def test_real_account_slot_puts_a_dated_buffer_draft_on_each_channel(human, fake):
    c = human
    k = c.post("/api/brand-studio", json={"front_card": {"style": "grid"}, "hashtags": ["AI", "#HuhByte"], "font": "figtree"}).json()
    assert k["front_card"]["style"] == "grid" and k["font"] == "figtree" and k["hashtags"] == ["#AI", "#HuhByte"]
    s = c.post("/api/templates/slots", json={"template": "weekly_recap", "weekday": "mon", "time": "20:00", "tz": "Europe/Dublin",
                                              "channel_ids": ["ch_x", "ch_li"]}).json()
    assert s["weekday"] == "mon" and s["at"] == "20:00"
    out = c.post(f"/api/templates/slots/{s['id']}/run").json()
    assert {d["status"] for d in out["drafts"]} == {"sent_to_buffer"}
    assert len(fake.created) == 2 and all(i.get("saveToDraft") and i.get("dueAt") for i in fake.created)
    assert all(i.get("assets") for i in fake.created)  # the rendered slides ride along
    assert out["buffer_cost"]["live"] is True and out["buffer_cost"]["requests"] >= 2
    assert c.delete(f"/api/templates/slots/{s['id']}").json()["deleted"] == s["id"]


def test_mcp_tools_render_and_apply_examples(human):
    tok = agent_token(human, policy="draft")
    _, ex = mcp_call(human, tok, "list_brand_examples")
    assert len(ex["examples"]) == 2
    _, ap = mcp_call(human, tok, "apply_brand_example", {"example_id": "huhbyte"})
    assert ap["kit"]["name"] == "HuhByte AI"
    _, r = mcp_call(human, tok, "render_template", {"template": "quote", "services": ["linkedin"]})
    assert r["channels"][0]["slides"]
    res, _ = mcp_call(human, tok, "list_templates")
    assert not res["isError"]


def test_slot_ticker_skips_accounts_whose_kill_switch_is_on(app, human):
    """The ticker (HQ_SLOT_TICK=1) acts without a person, so the kill switch stops it too (SECURITY_RULES 4)."""
    s = human.post("/api/templates/slots", json={"template": "quote", "weekday": "mon", "time": "09:00", "tz": "Europe/Dublin",
                                                  "channel_ids": ["ch_x"]}).json()
    store, bs = app.state.store, app.state.house.brand_studio
    store.x("UPDATE template_slots SET next_fire=? WHERE id=?", (1.0, s["id"]))
    uid = store.q("SELECT user_id FROM template_slots WHERE id=?", (s["id"],))[0]["user_id"]
    assert (uid, s["id"]) in bs.due_slots()
    store.set_kill(uid, True)
    assert (uid, s["id"]) not in bs.due_slots()
