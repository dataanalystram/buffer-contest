"""The source library (studio/sources.py): each source type parses its recorded fixture, videos stay link/embed-only,
research papers are never products, the same story across sources becomes one item with "also on", freshness, the
shared per-source cache (TTL, status dots, one failing source isolated), and Refresh (rate limit, sample read-only) with
UI = REST = MCP."""
import json
import time
from pathlib import Path

import httpx
import pytest

from studio import netguard, sources as S, sources_agent as SA
from studio.drafter import FetchError
from studio.store import Store

from .conftest import agent_token, mcp_call

F = Path(__file__).parent / "sources_fixtures"
RAW = Path(SA.__file__).parent / "sample_sources" / "raw"
NOW = 1_800_000_000.0


# ---------------------------------------------------------------- parsing, one fixture per source type
@pytest.mark.parametrize("sid,fixture,typ", [
    ("yt_yc", "youtube.xml", "video"), ("show_hn", "hnrss_show.xml", "launch"), ("arxiv_ai", "arxiv.xml", "paper"),
    ("google_news", "google_news.xml", "article"), ("github_trending", "gh_trending.html", "repo"),
    ("hackertube", "hackertube.json", "video"), ("anthropic", "anthropic.html", "article"),
    ("openai", RAW / "openai.xml", "article"), ("yc_launches", RAW / "yc.json", "launch"),
])
def test_every_source_type_parses_its_fixture(sid, fixture, typ):
    body = (fixture if isinstance(fixture, Path) else F / fixture).read_bytes()
    items = S.parse_source(S.BY_ID[sid], body, NOW)
    assert items, sid
    for it in items:
        assert it["title"] and it["url"].startswith("https://") and it["source_id"] == sid
        assert it["type"] == typ and it["type_label"] == S.TYPE_LABEL[typ] and it["category"] == S.BY_ID[sid]["category"]
        assert len(it["summary"]) <= 240


def test_video_items_keep_the_official_url_and_thumbnail_and_are_never_reusable():
    for sid, fx in (("yt_openai", "youtube.xml"), ("hackertube", "hackertube.json")):
        for it in S.parse_source(S.BY_ID[sid], (F / fx).read_bytes()):
            v = it["video"]
            assert it["url"] == v["url"] and v["url"].startswith("https://www.youtube.com/watch?v=")
            assert v["thumbnail"].startswith("https://i.ytimg.com/vi/") and it["image"] == v["thumbnail"]
            assert v["embed"].startswith("https://www.youtube-nocookie.com/embed/")
            assert v["reuse"] == "permission unknown" and "never downloads" in v["note"]


def test_research_papers_are_labelled_and_judged_as_research_not_products():
    paper = S.parse_source(S.BY_ID["arxiv_cv"], (F / "arxiv.xml").read_bytes())[0]
    assert paper["type_label"] == "Research paper — not a product"
    it = SA._item("library", "arXiv cs.CV", "Introducing a new app that launches today", paper["url"], type="paper")
    assert SA.judge(it, set())["category"] == "research"


def test_hnrss_keeps_points_and_the_discussion_but_not_the_boilerplate():
    it = S.parse_source(S.BY_ID["show_hn"], (F / "hnrss_show.xml").read_bytes())[0]
    assert it["summary"] == "" and it["discussion"].startswith("https://news.ycombinator.com/item?id=")


def test_google_news_titles_lose_the_publisher_suffix():
    it = S.parse_source(S.BY_ID["google_news"], (F / "google_news.xml").read_bytes())[0]
    assert it["publisher"] and not it["title"].endswith(it["publisher"])


def test_anthropic_skips_category_links_and_reads_dates_and_the_firecrawl_markdown():
    items = S.parse_source(S.BY_ID["anthropic"], (F / "anthropic.html").read_bytes())
    assert [i["url"].rsplit("/", 1)[-1] for i in items][:1] == ["s1-sec"] and all(i["published"] > 0 for i in items)
    md = S._parse_anthropic_md(S.BY_ID["anthropic"], (F / "anthropic.md").read_text())
    assert md and all(i["url"].startswith("https://www.anthropic.com/") and len(i["title"]) >= 20 for i in md)


def test_anthropic_falls_back_to_firecrawl_only_when_the_html_has_no_items(monkeypatch):
    calls = []
    monkeypatch.setattr(S, "_http_get", lambda url, http, accept="": b"<html><div id=root></div></html>")
    monkeypatch.setattr(S, "_firecrawl_md", lambda url, http: calls.append(url) or (F / "anthropic.md").read_text())
    items, via = S.fetch_source(S.BY_ID["anthropic"])
    assert via == "firecrawl" and items and calls == ["https://www.anthropic.com/news"]
    monkeypatch.setattr(S, "_http_get", lambda url, http, accept="": (F / "anthropic.html").read_bytes())
    items, via = S.fetch_source(S.BY_ID["anthropic"])
    assert via == "http" and len(calls) == 1  # plain HTTP worked: no Firecrawl credit spent


def test_every_fetch_goes_through_netguard(monkeypatch):
    seen = []

    def spy(url, **kw):
        seen.append((url, kw.get("https_only"), kw.get("limit")))
        raise netguard.FetchError("blocked in test")
    monkeypatch.setattr(netguard, "safe_get", spy)
    with pytest.raises(FetchError):
        S.fetch_source(S.BY_ID["techcrunch"])
    assert seen == [("https://techcrunch.com/feed/", True, S.radar.MAX_BYTES)]


def test_catalog_is_complete_and_categorised():
    assert {s["category"] for s in S.LIBRARY} == set(S.CATEGORIES)
    assert len(S.BY_ID) == len(S.LIBRARY) >= 35
    assert all(s["url"].startswith("https://") for s in S.LIBRARY)


# ---------------------------------------------------------------- dedupe + freshness
def _it(sid, title, url, published=NOW - 3600, **kw):
    return S.lib_item(S.BY_ID[sid], title, url, published, **kw)


def test_dedupe_joins_the_same_story_across_sources_and_counts_where_else_it_ran():
    items = [_it("hn_front", "OpenAI launches GPT-6 with longer memory", "https://openai.com/index/gpt-6/?utm_source=hn"),
             _it("openai", "OpenAI launches GPT-6 with longer memory", "https://openai.com/index/gpt-6"),
             _it("techcrunch", "OpenAI launches GPT-6, its model with longer memory", "https://techcrunch.com/2026/11/02/gpt-6/"),
             _it("google_news", "OpenAI launches GPT-6 with a longer memory", "https://news.google.com/rss/articles/abc"),
             _it("verge", "Apple ships a new iPad", "https://www.theverge.com/ipad")]
    out = S.dedupe_library(items)
    assert len(out) == 2
    lead = next(o for o in out if "GPT-6" in o["title"])
    assert lead["source"] == "OpenAI News"  # the original leads; aggregators never do
    assert sorted(lead["also"]) == ["Google News", "Hacker News", "TechCrunch"] and lead["also_n"] == 3


def test_dedupe_never_merges_two_items_from_the_same_source():
    a = _it("arxiv_ai", "Benchmarking self evolving agents on long lists", "https://arxiv.org/abs/1")
    b = _it("arxiv_ai", "Benchmarking self evolving agents on longer lists", "https://arxiv.org/abs/2")
    assert len(S.dedupe_library([a, b])) == 2


@pytest.fixture
def store(tmp_path):
    st = Store(str(tmp_path / "lib.db"), "s" * 64)
    SA.ensure(st)
    return st


def _fake_fetch(table, calls=None):
    def fetch(src, http=None, now=None):
        if calls is not None:
            calls.append(src["id"])
        got = table.get(src["id"], [])
        if isinstance(got, Exception):
            raise got
        return got, "http"
    return fetch


def test_freshness_hides_old_items_but_keeps_evergreen_picks(store):
    table = {"verge": [_it("verge", "Fresh story about chips", "https://v.example/1", NOW - 3600),
                       _it("verge", "Old story about phones", "https://v.example/2", NOW - 72 * 3600)],
             "hackertube": [dict(_it("hackertube", "Neural networks from scratch", "https://www.youtube.com/watch?v=VMj-3S1tku0", 0.0), evergreen=True)]}
    S.refresh_library(store, ["verge", "hackertube"], now=NOW, fetch=_fake_fetch(table))
    v = S.library_view(store, now=NOW)
    titles = [i["title"] for i in v["items"]]
    assert titles == ["Fresh story about chips", "Neural networks from scratch"]  # news first, evergreen after
    assert len(S.library_view(store, now=NOW, fresh_hours=96)["items"]) == 3
    assert {c["name"]: c["items"] for c in v["categories"]}["Videos"] == 1
    assert [i["title"] for i in S.library_view(store, now=NOW, category="Videos")["items"]] == ["Neural networks from scratch"]


# ---------------------------------------------------------------- the shared cache
def test_one_failing_source_never_breaks_the_others_and_keeps_its_last_good_items(store):
    good = {"verge": [_it("verge", "Story one about robots", "https://v.example/1")],
            "ars": [_it("ars", "Story two about rockets", "https://a.example/2")]}
    S.refresh_library(store, ["verge", "ars"], now=NOW, fetch=_fake_fetch(good))
    bad = {"verge": FetchError("answered HTTP 503"), "ars": ValueError("odd JSON"),
           "engadget": [_it("engadget", "Story three about tablets", "https://e.example/3")]}
    S.refresh_library(store, ["verge", "ars", "engadget"], now=NOW + 1000, fetch=_fake_fetch(bad))
    v = S.library_view(store, now=NOW + 1000)
    st = {s["id"]: s for s in v["sources"]}
    assert st["verge"]["status"] == "failing" and "503" in st["verge"]["error"]
    assert st["ars"]["status"] == "failing" and "unreadable" in st["ars"]["error"]
    assert st["engadget"]["status"] == "ok" and st["engadget"]["last_checked"] == NOW + 1000
    assert {i["title"] for i in v["items"]} == {"Story one about robots", "Story two about rockets", "Story three about tablets"}
    assert st["techcrunch"]["status"] == "unchecked" and st["techcrunch"]["last_checked"] is None


def test_the_cache_ttl_means_one_fetch_for_many_readers(store):
    calls = []
    fetch = _fake_fetch({"verge": [_it("verge", "A story about chips", "https://v.example/1")]}, calls)
    S.refresh_library(store, ["verge"], now=NOW, fetch=fetch)
    S.refresh_library(store, ["verge"], now=NOW + 60, fetch=fetch)                         # inside the TTL: reused
    S.refresh_library(store, ["verge"], now=NOW + 60, fetch=fetch, floor=S.REFRESH_FLOOR)  # Refresh, inside the floor
    S.refresh_library(store, ["verge"], now=NOW + 200, fetch=fetch, floor=S.REFRESH_FLOOR)  # Refresh, past the floor
    S.refresh_library(store, ["verge"], now=NOW + 1200, fetch=fetch)                       # past the TTL
    assert calls == ["verge", "verge", "verge"]


def test_status_dots_ok_slow_failing():
    assert S.status_of({"ok": 1, "ms": 300}) == "ok"
    assert S.status_of({"ok": 1, "ms": S.SLOW_MS + 1}) == "slow"
    assert S.status_of({"ok": 0, "ms": 10}) == "failing"
    assert S.status_of(None) == "unchecked"


def test_research_leads_come_from_the_cached_library_with_papers_ranked_down(store, monkeypatch):
    now = time.time()
    table = {"techcrunch": [_it("techcrunch", "Mistral releases a new open model", "https://tc.example/m", now - 600)],
             "arxiv_ai": [_it("arxiv_ai", "A new open model for reasoning: a study", "https://arxiv.org/abs/9", now - 600)]}
    S.refresh_library(store, list(table), fetch=_fake_fetch(table))
    monkeypatch.setattr(S, "bing_news", lambda *a, **k: [])
    monkeypatch.setattr(S, "hacker_news", lambda *a, **k: [])
    monkeypatch.setattr(S, "warm", lambda *a, **k: None)
    leads = S.search("open model", [], store=store)
    assert [l["via"] for l in leads] == ["source_library", "source_library"]
    assert leads[0]["url"] == "https://tc.example/m" and leads[1]["type_label"] == "Research paper — not a product"
    store.x("INSERT INTO source_settings(user_id,data,secret,updated) VALUES(?,?,?,?)", ("u1", json.dumps({"lib_off": ["techcrunch"]}), "", now))
    assert [l["url"] for l in S.search("open model", [], store=store, uid="u1")] == ["https://arxiv.org/abs/9"]


# ---------------------------------------------------------------- Refresh, toggles: UI = REST = MCP
@pytest.fixture
def lib(human, app, monkeypatch):
    calls = []
    now = time.time()
    table = {"openai": [_it("openai", "OpenAI ships agent mode for everyone", "https://openai.com/index/agents", now - 1800)],
             "techcrunch": [_it("techcrunch", "OpenAI ships agent mode for everyone", "https://techcrunch.com/agents", now - 900)],
             "arxiv_ai": [_it("arxiv_ai", "Scaling laws for small agents", "https://arxiv.org/abs/7", now - 600)],
             "verge": FetchError("answered HTTP 500")}
    monkeypatch.setattr(S, "fetch_source", _fake_fetch(table, calls))
    for env in ("TYPESAFE_API_KEY", "SUBQ_API_KEY"):  # the scan's story judges stay on the rules here
        monkeypatch.delenv(env, raising=False)
    return human, calls


def test_refresh_reads_sources_once_a_minute_per_workspace(lib, app):
    c, calls = lib
    r = c.post("/api/sources/refresh", json={})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["refreshed"] and d["failing"] == ["The Verge"] and len(calls) == len(S.LIBRARY)
    item = next(i for i in d["items"] if "agent mode" in i["title"])
    assert item["source"] == "OpenAI News" and item["also"] == ["TechCrunch"]
    r2 = c.post("/api/sources/refresh", json={})
    assert r2.status_code == 429 and "refresh again in" in r2.json()["error"]
    assert len(calls) == len(S.LIBRARY)  # the limited Refresh fetched nothing
    v = c.get("/api/sources/library").json()
    assert v["refresh_in"] > 0 and v["can_refresh"] and {s["status"] for s in v["sources"]} >= {"ok", "failing"}


def test_toggle_hides_a_source_and_matches_over_mcp(lib, app):
    c, _ = lib
    c.post("/api/sources/refresh", json={})
    r = c.post("/api/sources/enabled", json={"source_id": "arxiv_ai", "enabled": False})
    assert r.status_code == 200 and r.json()["source"]["enabled"] is False
    v = c.get("/api/sources/library").json()
    assert not any(i["source_id"] == "arxiv_ai" for i in v["items"])
    assert c.post("/api/sources/enabled", json={"source_id": "nope", "enabled": True}).status_code == 400
    tok = agent_token(c, policy="draft")
    _, out = mcp_call(c, tok, "list_sources", {"category": "Research"})
    assert out["items"] == [] and next(s for s in out["sources"] if s["id"] == "arxiv_ai")["enabled"] is False
    _, out = mcp_call(c, tok, "set_source_enabled", {"source_id": "arxiv_ai", "enabled": True})
    assert out["source"]["enabled"] is True
    _, out = mcp_call(c, tok, "list_sources", {"category": "Research"})
    assert [i["type_label"] for i in out["items"]] == ["Research paper — not a product"]
    res, err = mcp_call(c, tok, "refresh_sources", {})
    assert res["isError"] and "refresh again" in err  # the same per-workspace limit as the button
    names = {t["name"] for t in c.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                                         json={"jsonrpc": "2.0", "id": 9, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"list_sources", "refresh_sources", "set_source_enabled"} <= names


def test_the_sample_reads_the_shared_copy_but_never_refreshes_or_toggles(lib, app):
    from fastapi.testclient import TestClient
    lib[0].post("/api/sources/refresh", json={})  # a real account filled the shared copy
    d = TestClient(app, base_url="https://hq.test")
    d.get("/demo")
    d.headers["X-HQ-CSRF"] = d.get("/api/session").json()["csrf"]
    v = d.get("/api/sources/library").json()
    assert v["read_only"] and not v["can_refresh"] and any("agent mode" in i["title"] for i in v["items"])
    assert d.post("/api/sources/refresh", json={}).status_code == 403
    assert d.post("/api/sources/enabled", json={"source_id": "verge", "enabled": False}).status_code == 403


def test_the_story_scan_uses_the_library_as_one_more_adapter(lib, app):
    c, calls = lib
    d = c.post("/api/sources/scan", json={"sources": ["library"]}).json()
    assert d["status"][0]["source"] == "library" and d["status"][0]["ok"] and d["counts"]["fetched"] >= 2
    assert any(cl["category"] == "research" for cl in d["clusters"] if any("arxiv" in i["url"] for i in cl["items"]))
