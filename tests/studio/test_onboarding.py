"""Onboarding personalisation and recommendations (studio/onboarding.py, /api/onboarding, /api/recommendations and the
MCP tools get_onboarding / set_onboarding / get_recommendations): choices are validated, the brand comes from the
person's website (context.dev first, the site reader as fallback), and the feed is filtered to what they chose, with up
to three ready-to-approve posts in their brand that hand off to Make a post. The sample uses recorded fixtures only."""
from fastapi.testclient import TestClient

from studio import onboarding as ob_mod

from .conftest import agent_token, mcp_call


def sample(app):
    c = TestClient(app, base_url="https://hq.test")
    c.get("/demo")
    c.headers["X-HQ-CSRF"] = c.get("/api/session").json()["csrf"]
    return c


def test_options_and_defaults(human):
    o = human.get("/api/onboarding").json()
    assert {t["id"] for t in o["options"]["content_types"]} == set(ob_mod.CONTENT_TYPES)
    assert o["options"]["tones"] == ob_mod.TONES and "punchy" in o["options"]["tones"]
    chans = [c["id"] for c in o["options"]["channels"]]
    assert chans and o["profile"]["channel_ids"] == chans  # every connected channel until the person picks
    assert o["profile"]["tone"] == "plain" and o["profile"]["done"] is False and o["x_key"] is False


def test_put_keeps_only_valid_choices(human):
    o = human.put("/api/onboarding", json={
        "content_types": ["ai_news", "funding", "crypto_pumps"], "channel_ids": ["ch_li", "ch_nope"], "tone": "shouty",
        "x_watchlist": ["@AarushYadav47", "https://x.com/AarushYadav47/status/2116198463847408066?s=20", "not a handle!!", "x" * 40],
        "done": True}).json()
    p = o["profile"]
    assert p["content_types"] == ["ai_news", "funding"]
    assert p["channel_ids"] == ["ch_li"]
    assert p["tone"] == "plain"  # unknown tones fall back
    assert p["x_watchlist"] == [{"type": "account", "handle": "AarushYadav47"},
                                {"type": "post", "url": "https://x.com/AarushYadav47/status/2116198463847408066"}]
    assert p["done"] is True
    assert human.put("/api/onboarding", json={"tone": "punchy"}).json()["profile"]["tone"] == "punchy"
    assert human.get("/api/onboarding").json()["profile"]["content_types"] == ["ai_news", "funding"]  # partial PUTs keep the rest


def test_website_builds_the_brand_from_context_dev(human, monkeypatch):
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test")
    seen = {}

    def fake_cd(self, host, key):
        seen["host"] = host
        return {"title": "Acme Robots", "domain": "acme.dev", "description": "Robots for everyone.",
                "colors": [{"hex": "#112233"}, {"hex": "#FF5A1F"}], "socials": [{"type": "x", "url": "https://x.com/acmerobots"}], "logos": []}
    monkeypatch.setattr(ob_mod.Onboarding, "_context_dev", fake_cd)
    out = human.put("/api/onboarding", json={"website": "https://www.acme.dev/about"}).json()
    assert seen["host"] == "acme.dev"
    assert out["brand"]["source"] == "context.dev"
    assert out["brand"]["kit"]["name"] == "Acme Robots" and out["brand"]["kit"]["handle"] == "acmerobots"
    assert "#112233" in out["brand"]["found"]["palette"]
    assert human.get("/api/onboarding").json()["profile"]["website"] == "https://www.acme.dev/about"


def test_website_falls_back_to_the_site_reader_and_rejects_junk(human, monkeypatch):
    monkeypatch.setenv("CONTEXT_DEV_API_KEY", "test")

    def down(self, host, key):
        raise ob_mod.OnboardingError("context.dev answered HTTP 503")
    monkeypatch.setattr(ob_mod.Onboarding, "_context_dev", down)
    h = human.app.state.house
    monkeypatch.setattr(type(h), "kit_from_website", lambda self, a, url: {"kit": {"name": "Read from " + url}, "found": {"logo": False}})
    out = human.put("/api/onboarding", json={"website": "acme.dev"}).json()
    assert out["brand"] == {"source": "website", "kit": {"name": "Read from https://acme.dev"}, "found": {"logo": False}}
    r = human.put("/api/onboarding", json={"website": "not a website"})
    assert r.status_code == 400 and "website" in r.json()["error"]


def test_recommendations_follow_the_choices_cache_and_hand_off_to_make_a_post(app):
    c = sample(app)
    chans = [ch["id"] for ch in c.get("/api/onboarding").json()["options"]["channels"]]
    c.put("/api/onboarding", json={"content_types": ["ai_news", "product_launches"], "channel_ids": chans[:2], "tone": "punchy"})
    r = c.get("/api/recommendations").json()
    assert r["cached"] is False and r["sample"] is True and r["content_types"] == ["ai_news", "product_launches"]
    assert r["feed"], "the recorded sample sources have AI news"
    assert all(f["why"].startswith("Matches") for f in r["feed"])
    assert all(f["lane"] != "hidden" for f in r["feed"])
    assert 1 <= len(r["recommended"]) <= 3
    for rec in r["recommended"]:
        assert rec["cover"] and rec["headline"] and rec["tone"] == "punchy"
        assert [cap["channel_id"] for cap in rec["captions"]] == chans[:2] and all(cap["text"] for cap in rec["captions"])
        assert rec["make_post"]["channel_ids"] == chans[:2]
        assert rec["make_post"]["prompt"].startswith("Post about this in all my channels")
        if rec["source_url"]:
            assert rec["source_url"] in rec["make_post"]["prompt"]
    again = c.get("/api/recommendations").json()
    assert again["cached"] is True and again["run_id"] == r["run_id"]  # opening the screen costs no source requests
    c.put("/api/onboarding", json={"content_types": ["funding"]})  # new choices drop the cache
    assert c.get("/api/recommendations").json()["cached"] is False
    assert c.get("/api/recommendations?refresh=1").json()["cached"] is False


def test_onboarding_mcp_tools_match_rest(human):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "set_onboarding", {"content_types": ["dev_tools"], "tone": "explainer"})
    assert not res["isError"] and out["profile"]["content_types"] == ["dev_tools"] and out["profile"]["tone"] == "explainer"
    res, out = mcp_call(human, tok, "get_onboarding")
    assert out["profile"] == human.get("/api/onboarding").json()["profile"]
