"""App-level research keys (studio/appkeys.py), all mocked: Product Hunt's client_credentials token is fetched once and
cached, today's launches are read once for everyone and cached globally, live fetches are rate-limited, the public feed is
the fallback (no env keys, or the API failing), Settings show the app-level status and every redirect URL, and no key
value ever reaches a response, an error message or the log."""
import json

import httpx
import pytest

from studio import appkeys

from .test_sources_agent import live_sources  # noqa: F401 - the live Sources fixture

SECRET = "phAppSecret_" + "S" * 24
APP_TOKEN = "phAppToken_" + "T" * 30
FEED = (b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Product Hunt</title>'
        b'<entry><title>Feedly AI</title><link href="https://www.producthunt.com/products/feedly-ai"/><id>1</id>'
        b'<updated>2026-11-01T09:00:00Z</updated><summary>Agents that read the web</summary></entry>'
        b'<entry><title>Clipsmith</title><link href="https://www.producthunt.com/products/clipsmith"/><id>2</id>'
        b'<updated>2026-11-01T08:00:00Z</updated><summary>Video clips</summary></entry></feed>')
GQL = {"data": {"posts": {"edges": [{"node": {"id": "9", "name": "Launchy", "tagline": "Ship faster", "url": "https://www.producthunt.com/posts/launchy",
                                              "votesCount": 321, "createdAt": "2026-11-01T07:00:00Z", "topics": {"edges": []}}}]}}}


class World:
    def __init__(self):
        self.calls, self.api_status, self.feed_status = [], 200, 200

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(f"{req.method} {req.url.host}{req.url.path}")
        if req.url.path == "/v2/oauth/token":
            b = json.loads(req.content)
            ok = b == {"client_id": "phid", "client_secret": SECRET, "grant_type": "client_credentials"}
            return httpx.Response(200, json={"access_token": APP_TOKEN, "token_type": "Bearer", "scope": "public"}) if ok else httpx.Response(401)
        if req.url.path == "/v2/api/graphql":
            if req.headers.get("authorization") != f"Bearer {APP_TOKEN}":
                return httpx.Response(401)
            return httpx.Response(self.api_status, json=GQL if self.api_status == 200 else {"errors": [{"message": "boom"}]})
        if req.url.host == "www.producthunt.com":
            return httpx.Response(self.feed_status, content=FEED, headers={"content-type": "application/atom+xml"})
        return httpx.Response(404)


@pytest.fixture
def world(monkeypatch):
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    for k in ("PH_CLIENT_ID", "PH_CLIENT_SECRET", "PH_REDIRECT_URI"):
        monkeypatch.delenv(k, raising=False)
    w = World()
    return w, httpx.Client(transport=httpx.MockTransport(w))


def _app_env(monkeypatch):
    monkeypatch.setenv("PH_CLIENT_ID", "phid")
    monkeypatch.setenv("PH_CLIENT_SECRET", SECRET)


def test_client_credentials_token_is_fetched_once_and_launches_cached_for_everyone(world, monkeypatch, capsys):
    w, http = world
    _app_env(monkeypatch)
    items, mode = appkeys.ph_shared(http, "query { posts { edges { node { id } } } }")
    assert mode == "api" and items[0]["title"].startswith("Launchy") and items[0]["engagement"] == 321
    again, _ = appkeys.ph_shared(http, "q")
    assert again == items
    assert w.calls.count("POST api.producthunt.com/v2/oauth/token") == 1
    assert w.calls.count("POST api.producthunt.com/v2/api/graphql") == 1  # global cache: one read for everyone
    out = capsys.readouterr().out
    assert SECRET not in out and APP_TOKEN not in out


def test_without_env_keys_the_public_feed_is_read(world):
    w, http = world
    items, mode = appkeys.ph_shared(http, "q")
    assert mode == "feed" and {i["title"] for i in items} == {"Feedly AI", "Clipsmith"}
    assert all(i["adapter"] == "ph" and i["url"].startswith("https://www.producthunt.com/") for i in items)
    assert not any("api.producthunt.com" in c for c in w.calls)


def test_api_errors_fall_back_to_the_feed(world, monkeypatch):
    w, http = world
    _app_env(monkeypatch)
    w.api_status = 500
    items, mode = appkeys.ph_shared(http, "q")
    assert mode == "feed" and len(items) == 2


def test_live_fetches_are_rate_limited_and_serve_the_last_result(world, monkeypatch):
    w, http = world
    monkeypatch.setattr(appkeys, "PH_CACHE_TTL", 0)  # cache expired at once, but the global rate limit still holds
    appkeys.ph_shared(http, "q")
    n = len(w.calls)
    items, _ = appkeys.ph_shared(http, "q")
    assert len(w.calls) == n and len(items) == 2


def test_everything_down_and_nothing_cached_is_a_clean_error(world):
    from studio.sources_agent import SourceError
    w, http = world
    w.feed_status = 503
    with pytest.raises(SourceError) as e:
        appkeys.ph_shared(http, "q")
    assert "couldn't be read" in str(e.value)


def test_status_reports_on_off_only(monkeypatch):
    _app_env(monkeypatch)
    monkeypatch.setenv("X_BEARER_TOKEN", "xAppBearer" + "B" * 40)
    st = json.dumps(appkeys.status())
    assert SECRET not in st and "BBBB" not in st and '"app": true' in st


def test_sources_scan_uses_the_shared_product_hunt_read_for_everyone(live_sources, app, monkeypatch):
    c, _ = live_sources
    _app_env(monkeypatch)
    w = World()
    app.state.house.sources.http = httpx.Client(transport=httpx.MockTransport(w))
    d = c.post("/api/sources/scan", json={"sources": ["ph"]}).json()
    ph = next(s for s in d["status"] if s["source"] == "ph")
    assert ph["ok"] and ph["items"] == 1
    assert APP_TOKEN not in json.dumps(d)
    assert c.get("/api/sources").json()["settings"]["on"]["ph"]  # on by default: nobody has to add a key


def test_settings_show_the_app_level_status_and_every_redirect_url(human, monkeypatch):
    for k in ("PH_REDIRECT_URI",):
        monkeypatch.delenv(k, raising=False)
    _app_env(monkeypatch)
    d = human.get("/api/integrations").json()
    ph = next(x for x in d["integrations"] if x["key"] == "ph")
    assert ph["app"]["on"] and "Connected by HuhQueue" in ph["app"]["label"] and not ph["connected"]
    urls = {r["url"] for r in d["redirects"]}
    assert {"https://hq.test/integrations/producthunt/callback", "https://hq.test/callback", "https://hq.test/mcp"} <= urls
    assert SECRET not in json.dumps(d)
    monkeypatch.setenv("PH_REDIRECT_URI", "https://huhqueue-studio.onrender.com/integrations/producthunt/callback")
    d = human.get("/api/integrations").json()
    assert "https://huhqueue-studio.onrender.com/integrations/producthunt/callback" in {r["url"] for r in d["redirects"]}
