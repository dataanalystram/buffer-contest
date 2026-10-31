"""The Sources agent (studio/sources_agent.py): adapters parse the recorded fixtures, dedupe and clustering, the lanes code
decides, per-channel captions, Buffer DRAFTS only on a real connected account (real GraphQL calls, counted), the sample
never touches the network, keys stay sealed and person-only, and every live fetch goes through the safe-fetch helper."""
import json
from pathlib import Path

import httpx
import pytest

from studio import drafter, netguard, sources_agent as S

from .conftest import CHANNELS, mcp_call, agent_token

RAW = Path(S.__file__).parent / "sample_sources" / "raw"


# ---------------------------------------------------------------- pure parts
def test_every_adapter_parses_its_recorded_fixture():
    got = {k: ad.sample() for k, ad in S.ADAPTERS.items()}
    assert set(got) == {"yc", "hn", "rss", "ph", "x"}
    assert len(got["yc"]) >= 10 and all(i["url"].startswith("https://www.ycombinator.com/launches/") for i in got["yc"])
    assert all(i["engagement"] >= 0 and i["title"] for i in got["hn"])
    assert {i["source"] for i in got["rss"]} >= {"OpenAI News", "Google DeepMind News"}
    assert all(i["title"].lower().startswith("sample") for i in got["x"] + got["ph"])  # made-up, and labelled so


def test_canonical_urls_drop_tracking_and_www():
    assert S.canonical("https://www.Example.com/a/?utm_source=x&id=3#top") == S.canonical("https://example.com/a?id=3")


def test_dedupe_merges_the_same_link_and_keeps_where_else_it_appeared():
    a = S._item("yc", "YC Launches", "Composal: agents test your product", "https://www.ycombinator.com/launches/x?utm_medium=feed")
    b = S._item("x", "X @dev", "agents that test your product", "https://ycombinator.com/launches/x/")
    out = S.dedupe([a, b])
    assert len(out) == 1 and out[0]["also"][0]["source"] == "X @dev"


def test_clusters_join_the_same_story_but_never_two_yc_companies():
    items = [S._item("rss", "Lab", "Gemini 4 Carbon frontier model released", "https://a.example/1"),
             S._item("hn", "HN", "Gemini 4 Carbon: the new frontier model", "https://b.example/2"),
             S._item("rss", "Lab", "Mapping forest types worldwide", "https://a.example/3"),
             S._item("yc", "YC", "Agent swarm for testing apps", "https://y.example/1"),
             S._item("yc", "YC", "Agent swarm for testing games", "https://y.example/2")]
    sizes = sorted(len(g) for g in S.cluster(items))
    assert sizes == [1, 1, 1, 2]


def test_decide_sends_sensitive_stories_to_a_person_and_hides_off_topic_hn():
    it = S._item("hn", "HN", "Serbia elected a new parliament", "https://x.example/p", published=0)
    j = S.judge(it, set())
    assert S.decide([it], j, 0)["lane"] == "hidden"
    bad = S._item("rss", "Lab", "AI model linked to a death, lawsuit filed", "https://x.example/q")
    assert S.decide([bad], S.judge(bad, set()), 0)["agent"] == "person_only"


@pytest.mark.parametrize("svc", ["twitter", "linkedin", "instagram", "youtube", "bluesky", "threads", "facebook"])
def test_captions_fit_each_network_and_use_its_hashtag_count(svc):
    st = S.finish_story({"headline": "Mistral Large 4 lands on Hugging Face", "line": "Open weights, available today.",
                         "facts": ["Free to download", "Runs on one node"], "url": "https://huggingface.co/blog/x",
                         "source": "Hugging Face", "also": [], "image": "", "company": ""}, "model_release")
    text = drafter.fit(svc, S.caption(svc, st, {"hashtags": ["#AI"]}))
    assert drafter.count(svc, text) <= drafter.limit_for(svc)
    assert len([w for w in text.split() if w.startswith("#")]) <= S.STYLE[svc]["hashtags"]


def test_live_fetches_go_through_netguard(monkeypatch):
    seen = []
    real = netguard.safe_get

    def spy(url, **kw):
        seen.append(url)
        raise netguard.FetchError("blocked in test")
    monkeypatch.setattr(netguard, "safe_get", spy)
    with pytest.raises(S.SourceError):
        S.YC().live({}, {}, None)
    with pytest.raises(S.SourceError):
        S.HN().live({}, {}, None)
    assert seen[0].startswith("https://www.ycombinator.com/") and "hn.algolia.com" in seen[1]
    monkeypatch.setattr(netguard, "safe_get", real)


def test_keyed_sources_refuse_without_a_key():
    with pytest.raises(S.SourceError, match="never scrapes"):
        S.X().live({}, {}, None)
    with pytest.raises(S.SourceError, match="Product Hunt"):
        S.ProductHunt().live({}, {}, None)


# ---------------------------------------------------------------- through the app
def _sources_transport(fake):
    """Buffer goes to the FakeBuffer; source hosts answer with the recorded fixtures (as the real sites would)."""
    files = {"www.ycombinator.com": "yc.json", "hn.algolia.com": "hn.json", "openai.com": "openai.xml",
             "deepmind.google": "deepmind.xml", "huggingface.co": "huggingface.xml", "blog.google": "googleai.xml",
             "api.x.com": "x.json", "api.producthunt.com": "producthunt.json"}
    calls = []

    def handler(request: httpx.Request):
        host = request.url.host
        if host in files:
            calls.append(str(request.url))
            body = (RAW / files[host]).read_bytes()
            return httpx.Response(200, content=body, headers={"content-type": "application/json" if files[host].endswith("json") else "application/rss+xml"})
        if host.endswith("buffer.com"):
            return fake(request)
        calls.append(str(request.url))
        return httpx.Response(404)  # story images and anything else: not found (covers fall back to the brand ground)
    return handler, calls


@pytest.fixture
def live_sources(human, app, fake, monkeypatch):
    from studio import drafter as d
    monkeypatch.setattr(d, "_public", lambda host: True)
    for env in ("TYPESAFE_API_KEY", "SUBQ_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    handler, calls = _sources_transport(fake)
    app.state.house.http = httpx.Client(transport=httpx.MockTransport(handler))
    app.state.house.sources.http = app.state.house.http
    return human, calls


def test_real_account_scan_reads_live_sources_and_drafts_into_buffer_as_drafts_only(live_sources, fake):
    c, calls = live_sources
    r = c.post("/api/sources/scan", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["sample"] is False and any(s["mode"] == "live" for s in d["status"])
    assert any("ycombinator.com" in u for u in calls) and any("algolia" in u for u in calls)
    assert not any("api.x.com" in u or "producthunt" in u for u in calls)  # no keys, no calls
    cid = next(cl["id"] for cl in d["clusters"] if cl["lane"] == "postable")
    dry = c.post("/api/sources/draft", json={"run_id": d["run_id"], "cluster_ids": [cid]}).json()
    assert dry["dry_run"] and {p["service"] for p in dry["packages"]} == {ch["service"] for ch in CHANNELS}
    assert not fake.created
    edit = {cid: {"ch_x": "My own words for X https://example.com"}}
    sent = c.post("/api/sources/draft", json={"run_id": d["run_id"], "cluster_ids": [cid], "dry_run": False, "texts": edit}).json()
    assert len(fake.created) == len(CHANNELS)
    assert all(inp.get("saveToDraft") for inp in fake.created)  # Buffer drafts, never scheduled or published
    assert any(inp["text"].startswith("My own words for X") for inp in fake.created)
    assert sent["buffer_cost"]["live"] is True and sent["buffer_cost"]["requests"] >= len(CHANNELS)
    texts = {p["channel_id"]: p["text"] for p in dry["packages"]}
    assert any(inp["channelId"] == "ch_li" and inp["text"] == texts["ch_li"] for inp in fake.created)  # previewed == sent


def test_x_and_product_hunt_keys_are_sealed_person_only_and_used_when_set(live_sources, app):
    c, calls = live_sources
    s = c.post("/api/sources/settings", json={"x_key": "xkeyZQ7" * 6, "ph_key": "phtokW9" * 5}).json()
    assert s["x_key"].endswith("ZQ7") and s["ph_key"].endswith("kW9") and s["on"]["x"] and s["on"]["ph"]
    raw = app.state.store.q("SELECT secret FROM source_settings")[0]["secret"]
    assert "xkeyZQ7" not in raw and "phtokW9" not in raw  # sealed at rest
    c.post("/api/sources/scan", json={})
    assert any("api.x.com/2/tweets/search/recent" in u for u in calls) and any("producthunt" in u for u in calls)
    tok = agent_token(c, policy="draft")
    res, out = mcp_call(c, tok, "sources_settings", {"rss": ["https://openai.com/news/rss.xml"]})
    assert not res["isError"]
    r = c.post("/api/sources/settings", headers={"Authorization": f"Bearer {tok}", "X-HQ-CSRF": ""}, json={"x_key": "C" * 40},
               cookies={})
    assert r.status_code in (400, 401, 403)


def test_mcp_scan_and_draft_with_a_review_token_wait_for_a_human(live_sources, fake):
    c, _ = live_sources
    tok = agent_token(c, policy="review")
    _, scan = mcp_call(c, tok, "sources_scan", {"sources": ["yc"]})
    cid = scan["clusters"][0]["id"]
    _, out = mcp_call(c, tok, "sources_draft", {"run_id": scan["run_id"], "cluster_ids": [cid], "channel_ids": ["ch_x"], "dry_run": False})
    assert out["packages"][0]["result"]["status"] == "pending_approval" and not fake.created


def test_sample_scan_is_recorded_and_never_calls_out(app):
    from fastapi.testclient import TestClient
    c = TestClient(app, base_url="https://hq.test")
    c.get("/demo")
    c.headers["X-HQ-CSRF"] = c.get("/api/session").json()["csrf"]
    seen = []
    orig = netguard.safe_get
    netguard.safe_get = lambda *a, **k: seen.append(a) or orig(*a, **k)
    try:
        d = c.post("/api/sources/scan", json={}).json()
    finally:
        netguard.safe_get = orig
    assert d["sample"] is True and not seen
    assert {s["source"] for s in d["status"]} == {"yc", "hn", "rss", "ph", "x"}
    assert all(s["mode"] == "recorded sample" for s in d["status"])
    d2 = c.post("/api/sources/scan", json={}).json()
    assert [x["id"] for x in d2["clusters"]] == [x["id"] for x in d["clusters"]]  # deterministic
    sent = c.post("/api/sources/draft", json={"run_id": d["run_id"], "dry_run": False}).json()
    assert sent["buffer_cost"] == {"requests": 0, "live": False}
    assert all(p["result"]["status"] == "sent_to_buffer" for p in sent["packages"])
