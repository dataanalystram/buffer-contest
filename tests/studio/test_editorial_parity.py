"""Editorial parity (owner, 1 Nov 2026): the agent makes the ernd carousel itself, and NO VIDEO -> NO REEL.

research_company -> find_brand_assets -> hero_edit -> write_editorial -> render_editorial -> write_captions -> check, the
same tools over REST and MCP; YouTube/TikTok are skipped with a note when there is no real video, never a text-on-gradient
Reel; captions lead with the keyword and carry at most 5 hashtags; editorial_story is a Brand -> Templates template.
Recorded data, no live calls.
"""
import io
import json

import pytest
from PIL import Image, ImageDraw

from studio import agent_tools, assets, editorial, editorial_tools, hero_edit, templates

from . import conftest
from .test_agent_core import OWNER_CHANNELS, TWEET, owner  # noqa: F401  (FakeBuffer, recorded X oEmbed, sync brain)

PROMPT = "make a post about ernd (YC F26), the credit card for the AI stack"
LAUNCH = ("Get faster inference, better access to GPUs, and 4% back on AI\nernd is the credit card for companies that pay for AI. "
          "Cardholders earn 4% back on AI spend, and benefit from partnerships for better pricing and capacity. **The problem** "
          "When we tried to get better pricing from AI vendors we were too small to matter. We pool startups' purchasing power to "
          "negotiate discounts. Firecrawl already gives cardholders 2x usage limits. A $10K a month AI bill earns about $4,800 a "
          "year back. There is no annual fee and it is paid off once a month.")
SITE = "4% back on AI, plus enterprise benefits on inference and GPUs. 1.5% back on everything else. No annual fee for US companies."
YC = "The Credit Card for the AI stack. 4% back on AI spend and 2% on everything else planned for launch."


def _jpeg(w, h, color):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def _card_on_stage():
    im = Image.new("RGB", (1200, 630), (236, 236, 240))
    ImageDraw.Draw(im).rounded_rectangle((330, 140, 870, 480), 36, fill=(120, 110, 220))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=95)
    return b.getvalue()


RESEARCH = {
    "name": "ernd", "slug": "ernd", "notes": ["Product Hunt answered 403 (it blocks servers); skipped"],
    "yc": {"name": "ernd", "batch": "Fall 2026", "batch_name": "F26", "one_liner": "The credit card for the AI stack",
           "website": "https://ernd.io/", "tags": ["Fintech", "AI"], "url": "https://www.ycombinator.com/companies/ernd"},
    "founders": [{"name": "Connor Dawson", "title": "CEO", "bio": "Ex Bridgewater.", "photo": "https://img.test/f1.jpg"},
                 {"name": "Nikhil Ruia", "title": "CTO", "bio": "Ex IMC Trading.", "photo": "https://img.test/f2.jpg"},
                 {"name": "Malcolm Wengel", "title": "COO", "bio": "Ex Ripple.", "photo": "https://img.test/f3.jpg"}],
    "sources": [{"url": "https://ernd.io/", "kind": "website", "title": "ernd", "date": "current", "text": SITE},
                {"url": "https://www.ycombinator.com/launches/Whe-ernd", "kind": "launch", "title": "ernd: the credit card for the AI stack",
                 "date": "2026-10-31", "text": LAUNCH},
                {"url": "https://www.ycombinator.com/companies/ernd", "kind": "yc_profile", "title": "ernd (F26)", "date": "", "text": YC}],
    "images": [{"url": "https://img.test/logo.png", "role": "logo", "page": "yc"},
               {"url": "https://img.test/f1.jpg", "role": "founder", "page": "yc", "name": "Connor Dawson"},
               {"url": "https://img.test/f2.jpg", "role": "founder", "page": "yc", "name": "Nikhil Ruia"},
               {"url": "https://img.test/f3.jpg", "role": "founder", "page": "yc", "name": "Malcolm Wengel"},
               {"url": "https://ernd.io/og.jpg", "role": "product", "page": "https://ernd.io/"},
               {"url": "https://ernd.io/art/sky-p.webp", "role": "hero", "page": "https://ernd.io/"}],
}
IMAGES = {"https://img.test/logo.png": (220, 220, (255, 255, 255)), "https://img.test/f1.jpg": (300, 300, (180, 120, 90)),
          "https://img.test/f2.jpg": (300, 300, (120, 90, 80)), "https://img.test/f3.jpg": (300, 300, (90, 110, 70)),
          "https://ernd.io/art/sky-p.webp": (1080, 1600, (90, 100, 170))}


@pytest.fixture
def company(monkeypatch):
    calls = []
    monkeypatch.setattr(assets, "research", lambda name, urls=None, http=None: calls.append((name, urls)) or json.loads(json.dumps(RESEARCH)))

    def fetch(url, http=None, min_side=200):
        if url == "https://ernd.io/og.jpg":
            data = _card_on_stage()
        elif url in IMAGES:
            w, h, c = IMAGES[url]
            data = _jpeg(w, h, c)
        else:
            return None
        im = Image.open(io.BytesIO(data))
        return data, {"width": im.width, "height": im.height, "ext": "jpg", "source_url": url, "rights": assets.RIGHTS}
    monkeypatch.setattr(assets, "fetch_image", fetch)
    return calls


def _external(owner, channels):  # noqa: F811
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    res, run = conftest.mcp_call(owner, tok, "make_post", {"prompt": PROMPT, "brain": "external", "channel_ids": channels})
    assert not res["isError"], res
    return tok, run["id"]


def _tool(owner, rid, name, body=None):  # noqa: F811
    r = owner.post(f"/api/agent/runs/{rid}/tools/{name}", json=body or {})
    assert r.status_code == 200, r.text
    return r.json()


def _run(owner, rid):  # noqa: F811
    return owner.get(f"/api/agent/runs/{rid}").json()


# ---------------------------------------------------------------------------------------------------- units
def test_company_request_spots_company_and_launch_posts():
    assert editorial_tools.company_request(PROMPT) == "ernd"
    assert editorial_tools.company_request("carousel about Acme Robotics, the YC startup") == "Acme Robotics"
    assert editorial_tools.company_request("x", ["https://www.ycombinator.com/companies/ernd"]) == "ernd"
    assert editorial_tools.company_request("post about this https://x.com/a/status/1") == ""
    assert editorial_tools.company_request("make a post about AI news today") == ""


def test_captions_keyword_first_and_at_most_five_hashtags():
    t = agent_tools.keyword_first("Your AI bill could pay you back.\nmore", "ernd")
    assert t.startswith("ernd: Your AI bill") and t.endswith("\nmore")
    assert agent_tools.keyword_first("ernd (YC F26) pays 4% back on AI", "ernd").startswith("ernd (YC F26)")
    capped = agent_tools.cap_hashtags("Hook\n\n#a #b #c #d #e #f #g")
    assert capped.count("#") == 5 and "#f" not in capped and "#g" not in capped


def test_conflicting_figures_use_the_newest_official_page():
    c = assets.conflicts(RESEARCH["sources"])
    assert c and c[0]["topic"] == "everything else" and c[0]["use"] == "1.5%" and c[0]["from"] == "website"


def test_check_spec_minimum_content_and_one_idea_per_slide():
    thin = {"headline": "x", "slides": [{"blocks": [{"head": "a", "body": "b"}]}]}
    assert any("only 1 content slides" in p for p in editorial.check_spec(thin))
    busy = {"headline": "x", "slides": [{"blocks": [{"head": "a"}, {"head": "b"}, {"head": "c"}]}] * 4}
    assert any("one idea per slide" in p for p in editorial.check_spec(busy))
    assert editorial.check_spec(templates.SAMPLE["editorial_story"]) == []


def test_hero_edit_cuts_the_product_out_and_rings_the_founders():
    card = Image.open(io.BytesIO(_card_on_stage()))
    sky = Image.new("RGB", (1080, 1600), (90, 100, 170))
    faces = [Image.new("RGB", (300, 300), c) for c in ((180, 120, 90), (120, 90, 80))]
    out, rep = hero_edit.premium_hero(1080, 1350, scene=sky, product=card, founders=faces)
    assert out.size == (1080, 1350) and rep["cutout"] and rep["product_box"]
    assert any("founder avatar" in s for s in rep["steps"]) and any("tilted" in s for s in rep["steps"])


def test_editorial_story_is_a_brand_template():
    assert "editorial_story" in templates.TEMPLATES and templates.TEMPLATES["editorial_story"]["kind"] == "carousel"
    b = templates.Brand({"name": "HuhByte AI", "handle": "huhbyte.ai", "colors": {"primary": "#000000", "accent": "#C9ACFA"}})
    ims = templates.slides("editorial_story", None, b, "4:5")
    assert 5 <= len(ims) <= 7 and all(im.size == (1080, 1350) for im in ims)
    assert len(templates.slides("editorial_story", None, b, "4:5", max_slides=4)) == 4


# ---------------------------------------------------------------------------------------------------- no video -> no reel
def test_render_reel_without_a_video_skips_youtube_and_gives_the_rest_the_carousel(owner):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "channel_ids": ["ch_yt", "ch_ig"], "format": "auto"}).json()
    rid = r["id"]
    out = _tool(owner, rid, "render_reel", {"channel_ids": ["ch_yt", "ch_ig"], "style": "opus"})
    assert out["no_video"] and out["skipped"] == ["youtube"] and out["format"] == "carousel" and out["channels"] == ["ch_ig"]
    assert all(not u.endswith(".mp4") for u in out["media"])
    run = _run(owner, rid)
    res = run["result"]
    assert res["channel_media"]["ch_yt"]["format"] == "skipped" and "needs a video" in res["channel_media"]["ch_yt"]["note"]
    assert any("no video found" in p.lower() for p in res["plan"])
    jobs = owner.app.state.store.q("SELECT spec FROM render_jobs")
    assert not any(json.loads(j["spec"])["kind"] in ("slideshow", "media_render") for j in jobs)  # no text-on-gradient video
    caps = _tool(owner, rid, "write_captions", {})
    assert "youtube" not in caps["captions"] and caps["skipped"] == ["youtube"]
    chk = _tool(owner, rid, "check", {})
    assert [s["service"] for s in chk["skipped"]] == ["youtube"] and not any("youtube" in p for p in chk["problems"])
    assert [p["service"] for p in _run(owner, rid)["result"]["packages"]] == ["instagram"]


def test_edit_channel_media_never_makes_a_reel_without_a_video(owner):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "channel_ids": ["ch_yt", "ch_li"], "format": "auto"}).json()
    out = _tool(owner, r["id"], "edit_channel_media", {"channel": "youtube", "seconds": 30})
    assert out["channel"] == "youtube" and out["no_video"] and out["skipped"] == ["youtube"]
    out = _tool(owner, r["id"], "edit_channel_media", {"channel": "linkedin", "format": "reel"})
    assert out["format"] == "carousel" and out["channels"] == ["ch_li"]


# ---------------------------------------------------------------------------------------------------- the editorial chain
def test_editorial_chain_over_rest(owner, company):  # noqa: F811
    _tok, rid = _external(owner, ["ch_ig", "ch_li", "ch_yt"])
    r = _tool(owner, rid, "research_company", {"name": "ernd (YC F26)"})
    assert r["company"] == "ernd" and r["batch"] == "F26" and r["founders"][0] == "Connor Dawson"
    assert any("1.5%" in d for d in r["decisions"])  # the newest official page wins, and it says so
    a = _tool(owner, rid, "find_brand_assets")
    roles = [x["role"] for x in a["assets"]]
    assert roles.count("founder") == 3 and "product" in roles and "hero" in roles and a["rights"] == assets.RIGHTS
    h = _tool(owner, rid, "hero_edit")
    assert h["cutout"] and h["founders"] == 3 and h["hero"].endswith(".jpg")
    w = _tool(owner, rid, "write_editorial")
    assert w["engine"] == "rules" and w["content_slides"] >= 4 and 6 <= w["slides"] <= 7, w
    out = _tool(owner, rid, "render_editorial")
    assert set(out["channels"]) == {"ch_ig", "ch_li"} and out["skipped"][0]["channel"] == "youtube"
    assert 6 <= out["slides"] <= 7 and out["fit_problems"] == []
    res = _run(owner, rid)["result"]
    assert res["channel_media"]["ch_ig"]["template"] == "editorial_story" and "No video found" in res["channel_media"]["ch_ig"]["note"]
    assert res["channel_media"]["ch_yt"]["format"] == "skipped"
    caps = _tool(owner, rid, "write_captions")
    assert set(caps["captions"]) == {"instagram", "linkedin"}
    for text in caps["captions"].values():
        assert text.lower().startswith("ernd") and text.count("#") <= 5
    chk = _tool(owner, rid, "check")
    assert chk["packages"] == 2 and [s["service"] for s in chk["skipped"]] == ["youtube"] and chk["about_source"]


def test_editorial_tools_over_mcp_with_the_clients_own_copy(owner, company):  # noqa: F811
    from studio import mcp
    names = {t["name"] for t in mcp.TOOLS}
    assert {"post_research_company", "post_find_brand_assets", "post_hero_edit", "post_write_editorial", "post_render_editorial"} <= names
    desc = next(t for t in mcp.TOOLS if t["name"] == "post_write_editorial")["description"]
    assert "HuhQueue server AI" in desc
    tok, rid = _external(owner, ["ch_ig"])
    res, got = conftest.mcp_call(owner, tok, "post_research_company", {"run_id": rid, "name": "ernd"})
    assert not res["isError"] and got["company"] == "ernd"
    spec = json.loads(json.dumps(templates.SAMPLE["editorial_story"]))
    res, got = conftest.mcp_call(owner, tok, "post_render_editorial", {"run_id": rid, "spec": spec})
    assert not res["isError"] and got["channels"] == ["ch_ig"] and got["slides"] == 7, got
    assert _run(owner, rid)["result"]["editorial"]["engine"] == "client"


def test_the_fixed_plan_makes_the_company_post_and_says_no_video(owner, company, monkeypatch):  # noqa: F811
    from studio.media import tools as media_tools
    monkeypatch.setattr(media_tools, "media_find", lambda q, max_results=5, http=None, **k: {"best_video": None, "best_downloadable": None,
                                                                                               "candidates": [], "images": [], "note": "none"})
    r = owner.post("/api/agent/runs", json={"prompt": PROMPT, "channel_ids": ["ch_ig", "ch_yt"], "format": "auto"}).json()
    run = _run(owner, r["id"])
    steps = [s["tool"] for s in run["steps"]]
    order = ["research_company", "find_brand_assets", "hero_edit", "write_editorial", "render_editorial", "write_captions", "check"]
    assert [s for s in steps if s in order] == order, steps
    assert "search_news" not in steps and "extract_facts" not in steps and "render_reel" not in steps
    res = run["result"]
    assert any("no video found" in p.lower() for p in res["plan"])
    assert [p["service"] for p in res["packages"]] == ["instagram"] and res["packages"][0]["format"] == "carousel"
    assert any("skipped" in t["text"].lower() and "youtube" in t["text"].lower() for t in res["thread"])
