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


def test_recommendations_follow_the_choices_cache_and_hand_off_to_make_a_post(app, monkeypatch):
    """The sample serves the pre-made set: filtered by the choices, never a scan, a render or a model call."""
    from studio import render, sources_agent
    c = sample(app)
    boom = lambda *a, **k: (_ for _ in ()).throw(AssertionError("the sample must not scan or render for recommendations"))  # noqa: E731
    monkeypatch.setattr(sources_agent.SourcesAgent, "scan", boom)
    monkeypatch.setattr(render, "news_card", boom)
    chans = [ch["id"] for ch in c.get("/api/onboarding").json()["options"]["channels"]]
    c.put("/api/onboarding", json={"content_types": ["ai_news", "product_launches"], "channel_ids": chans[:2], "tone": "punchy"})
    spendy = "SELECT COUNT(*) AS n FROM audit WHERE action IN ('recs_build','sources_scan','ai_call')"
    before = app.state.store.q(spendy)[0]["n"]
    r = c.get("/api/recommendations").json()
    assert r["sample"] is True and r["static"] is True and r["run_id"] is None and r["content_types"] == ["ai_news", "product_launches"]
    assert r["feed"], "the pre-made sample set has AI news"
    assert all(f["why"].startswith("Matches") for f in r["feed"]) and all(f["lane"] != "hidden" for f in r["feed"])
    assert 1 <= len(r["recommended"]) <= 3
    for rec in r["recommended"]:
        assert rec["cover"].startswith("/static/sample_recs/") and rec["headline"] and rec["tone"] == "punchy"
        assert c.get(rec["cover"]).status_code == 200
        assert [cap["channel_id"] for cap in rec["captions"]] == chans[:2] and all(cap["text"] for cap in rec["captions"])
        assert rec["make_post"]["channel_ids"] == chans[:2]
        assert rec["make_post"]["prompt"].startswith("Post about this in all my channels")
        if rec["source_url"]:
            assert rec["source_url"] in rec["make_post"]["prompt"]
    c.put("/api/onboarding", json={"content_types": ["funding"]})  # new choices re-filter the same set
    f = c.get("/api/recommendations?refresh=1").json()
    assert f["content_types"] == ["funding"] and f["static"] is True
    assert app.state.store.q(spendy)[0]["n"] == before  # no scan, no model call, no charged rebuild


def test_signed_in_recommendations_rate_limited_and_charged(human, monkeypatch):
    from studio import budget
    h = human.app.state.house
    calls = []
    clusters = [{"id": "c1", "topic": "Launch", "category": "product_feature", "lane": "postable", "score": 0.9,
                 "story": {"headline": "Acme launches a new AI model", "source": "HN"},
                 "items": [{"title": "Acme launches a new AI model", "url": "https://news.example.com/acme", "source": "HN"}]}]
    monkeypatch.setattr(type(h.sources), "scan", lambda self, a, body=None: calls.append(body) or {"run_id": len(calls), "clusters": clusters})
    monkeypatch.setattr(ob_mod, "RECS_COST", 0.25)
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")  # the default is $5 since 7 Nov 2026; this test spends against $1
    human.put("/api/onboarding", json={"x_watchlist": []})  # nothing to read from X here
    r = human.get("/api/recommendations").json()
    assert r["cached"] is False and len(calls) == 1 and r["recommended"][0]["cover"]
    uid = human.get("/api/me").json()["id"]
    store = human.app.state.store
    assert budget.remaining(store, uid)["spent_usd"] == 0.25  # charged to the person's own allowance
    assert budget._spent(store, "global") == 0  # not the shared AI pool
    # refresh within the hour: the cached set, no new scan, and when the next refresh is allowed
    again = human.get("/api/recommendations?refresh=1").json()
    assert again["cached"] is True and again["next_refresh_in"] > 3000 and len(calls) == 1
    # new choices mark the set stale but can't bypass the hourly limit
    human.put("/api/onboarding", json={"content_types": ["funding"]})
    stale = human.get("/api/recommendations").json()
    assert stale["cached"] is True and stale["stale"] is True and len(calls) == 1
    # an hour later it rebuilds; once the budget can't cover a rebuild, the last set is served without a scan
    store.x("UPDATE audit SET ts=ts-4000 WHERE action='recs_build'")
    assert human.get("/api/recommendations?refresh=1").json()["cached"] is False and len(calls) == 2
    store.x("UPDATE audit SET ts=ts-4000 WHERE action='recs_build'")
    store.x("UPDATE brain_budget SET spent=0.9 WHERE scope=?", (f"user:{uid}",))
    out = human.get("/api/recommendations?refresh=1").json()
    assert out["cached"] is True and "budget" in out["note"] and len(calls) == 2


def test_onboarding_mcp_tools_match_rest(human):
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "set_onboarding", {"content_types": ["dev_tools"], "tone": "explainer"})
    assert not res["isError"] and out["profile"]["content_types"] == ["dev_tools"] and out["profile"]["tone"] == "explainer"
    res, out = mcp_call(human, tok, "get_onboarding")
    assert out["profile"] == human.get("/api/onboarding").json()["profile"]
