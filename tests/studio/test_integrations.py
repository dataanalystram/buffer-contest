"""Settings -> Integrations (studio/integrations.py): one card per key-based integration, keys sealed with the same mechanism
as every other user secret and never returned, live tests that report a status only (mocked here: no real Product Hunt,
X or Google calls in CI), person-only writes, and the Product Hunt callback (friendly page; code exchanged only with our
own state)."""
import json
import re
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from studio import integrations as I

from .conftest import agent_token

PH_TOKEN = "phDevTok_" + "A1b2C3d4" * 4 + "wxyz"
X_TOKEN = "AAAAAAAAAAAAAAAAAAAAA" + "%2Fbearer" + "Q" * 30 + "x9Zk"
YT_KEY = "AIza" + "S" * 31 + "yt42"


@pytest.fixture
def services(human, app, monkeypatch):
    """Every outside service answered by a mock: valid keys pass, anything else is refused. Records calls (no keys)."""
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda host: True)  # DNS pinned for the mock (as test_sources_agent does)
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        host, auth = req.url.host, req.headers.get("authorization", "")
        calls.append(f"{req.method} {host}{req.url.path}")
        if host == "api.producthunt.com" and req.url.path == "/v2/api/graphql":
            if auth == f"Bearer {PH_TOKEN}":
                return httpx.Response(200, json={"data": {"posts": {"edges": [{"node": {"id": "1"}}]}}})
            return httpx.Response(401, json={"error": "invalid_oauth_token"})
        if host == "api.producthunt.com" and req.url.path == "/v2/oauth/token":
            body = json.loads(req.content)
            ok = body.get("code") == "goodcode" and body.get("client_secret") == "phsec" and body["redirect_uri"].endswith(I.PH_CALLBACK_PATH)
            return httpx.Response(200, json={"access_token": "oauthTok_" + "Z" * 30}) if ok else httpx.Response(400, json={"error": "invalid_grant"})
        if host == "api.x.com":
            return httpx.Response(200, json={"data": {}}) if auth == f"Bearer {X_TOKEN}" else httpx.Response(401)
        if host == "www.googleapis.com":
            return httpx.Response(200, json={"items": []}) if req.url.params.get("key") == YT_KEY else httpx.Response(400, json={"error": {"message": "API key not valid"}})
        return httpx.Response(404)
    app.state.house.http = httpx.Client(transport=httpx.MockTransport(handler))
    return human, calls


def _card(d, key):
    return next(c for c in d["integrations"] if c["key"] == key)


def test_cards_list_every_key_integration_with_how_to_and_the_redirect_uri(human):
    d = human.get("/api/integrations").json()
    assert [c["key"] for c in d["integrations"]] == ["ph", "x", "yt"]
    ph = _card(d, "ph")
    assert ph["redirect_uri"] == "https://hq.test/integrations/producthunt/callback"
    assert ph["get_url"] == "https://api.producthunt.com/v2/oauth/applications" and len(ph["steps"]) == 3
    assert any("Create Token" in s for s in ph["steps"]) and not ph["connected"] and ph["hint"] == ""
    assert d["ai_keys"]  # AI keys stay in Settings -> AI engine (linked, not duplicated)


def test_save_seals_the_key_never_returns_it_and_feeds_the_sources_agent(services, app):
    c, _ = services
    d = c.post("/api/integrations/ph", json={"value": PH_TOKEN}).json()
    ph = _card(d, "ph")
    assert ph["connected"] and ph["hint"] == "…wxyz"
    assert PH_TOKEN not in json.dumps(d) and PH_TOKEN not in c.get("/api/integrations").text
    raw = app.state.store.q("SELECT data, secret FROM source_settings")[0]
    assert PH_TOKEN not in raw["secret"] and PH_TOKEN not in raw["data"]  # sealed at rest
    s = c.get("/api/sources").json()["settings"]  # the Product Hunt adapter reads the same sealed secret
    assert s["ph_key"] == "…wxyz" and s["on"]["ph"]
    audit = json.dumps(app.state.store.audit_rows(app.state.store.q("SELECT user_id FROM source_settings")[0]["user_id"]))
    assert PH_TOKEN not in audit and "integration_saved" in audit


def test_test_button_reports_status_only_for_valid_and_invalid_keys(services):
    c, calls = services
    bad = c.post("/api/integrations/ph/test", json={"value": "not-a-real-token-but-long-enough"})
    assert bad.status_code == 200 and bad.json()["ok"] is False and "refused" in bad.json()["message"]
    assert "not-a-real-token" not in bad.text
    good = c.post("/api/integrations/ph/test", json={"value": PH_TOKEN}).json()
    assert good["ok"] is True and PH_TOKEN not in json.dumps(good)
    assert c.post("/api/integrations/x/test", json={"value": X_TOKEN}).json()["ok"] is True
    assert c.post("/api/integrations/x/test", json={"value": "B" * 40}).json()["ok"] is False
    assert c.post("/api/integrations/yt/test", json={"value": YT_KEY}).json()["ok"] is True
    yt_bad = c.post("/api/integrations/yt/test", json={"value": "AIza" + "N" * 35}).json()
    assert yt_bad["ok"] is False and "AIza" not in yt_bad["message"]
    assert "POST api.producthunt.com/v2/api/graphql" in calls and "GET api.x.com/2/usage/tweets" in calls


def test_saved_key_test_stamps_the_masked_status(services):
    c, _ = services
    c.post("/api/integrations/x", json={"value": X_TOKEN})
    r = c.post("/api/integrations/x/test", json={}).json()
    assert r["ok"] is True
    x = _card(c.get("/api/integrations").json(), "x")
    assert x["connected"] and x["hint"] == "…x9Zk" and x["tested_ok"] is True and x["tested"]


def test_validation_remove_and_unknown(services):
    c, _ = services
    r = c.post("/api/integrations/ph", json={"value": "short"})
    assert r.status_code == 400 and "doesn't look like" in r.json()["error"]
    assert c.post("/api/integrations/nope", json={"value": "A" * 30}).status_code == 404
    c.post("/api/integrations/yt", json={"value": YT_KEY})
    d = c.delete("/api/integrations/yt").json()
    assert not _card(d, "yt")["connected"]
    assert c.post("/api/integrations/yt/test", json={}).status_code == 400  # nothing saved to test


def test_agents_cannot_touch_keys_and_writes_need_csrf(services, client):
    c, _ = services
    tok = agent_token(c, policy="draft")
    hdr = {"Authorization": f"Bearer {tok}"}
    csrf = c.headers.pop("X-HQ-CSRF")
    try:
        assert c.post("/api/integrations/ph", json={"value": PH_TOKEN}).status_code == 403  # no CSRF token
        for m, path in (("POST", "/api/integrations/ph"), ("POST", "/api/integrations/ph/test"), ("DELETE", "/api/integrations/ph")):
            r = c.request(m, path, headers=hdr, cookies={}, json={"value": PH_TOKEN} if m == "POST" else None)
            assert r.status_code == 403, (m, path, r.text)
    finally:
        c.headers["X-HQ-CSRF"] = csrf


def test_key_tests_are_rate_capped(services, monkeypatch):
    c, _ = services
    monkeypatch.setattr(I, "TESTS_PER_HOUR", 2)
    for _ in range(2):
        c.post("/api/integrations/ph/test", json={"value": PH_TOKEN})
    assert c.post("/api/integrations/ph/test", json={"value": PH_TOKEN}).status_code == 429


def test_callback_without_code_is_a_friendly_public_page(client):
    r = client.get("/integrations/producthunt/callback")
    assert r.status_code == 200 and "Product Hunt app created" in r.text
    assert "Developer Token" in r.text and "Settings → Integrations" in r.text
    assert "https://hq.test/integrations/producthunt/callback" in r.text
    assert not re.search(r"<script", r.text)  # renders under script-src 'self'
    assert "Disallow: /integrations/" in client.get("/robots.txt").text


def test_callback_code_without_our_state_is_never_exchanged(services, monkeypatch):
    c, calls = services
    monkeypatch.setenv("PH_CLIENT_ID", "phid")
    monkeypatch.setenv("PH_CLIENT_SECRET", "phsec")
    r = c.get("/integrations/producthunt/callback?code=goodcode&state=forged")
    assert r.status_code == 200 and "didn't start from your HuhQueue session" in r.text
    assert not any("oauth/token" in x for x in calls)
    assert not _card(c.get("/api/integrations").json(), "ph")["connected"]


def test_oauth_connect_then_callback_exchanges_and_stores_the_token(services, monkeypatch, app):
    c, calls = services
    monkeypatch.setenv("PH_CLIENT_ID", "phid")
    monkeypatch.setenv("PH_CLIENT_SECRET", "phsec")
    r = c.get("/integrations/producthunt/connect", follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.netloc == "api.producthunt.com" and q["redirect_uri"] == ["https://hq.test/integrations/producthunt/callback"]
    done = c.get(f"/integrations/producthunt/callback?code=goodcode&state={q['state'][0]}")
    assert done.status_code == 200 and "Product Hunt connected" in done.text and "oauthTok_" not in done.text
    assert "POST api.producthunt.com/v2/oauth/token" in calls
    ph = _card(c.get("/api/integrations").json(), "ph")
    assert ph["connected"] and ph["hint"] == "…ZZZZ"
    # the state is single-use: the cookie is cleared, so a replay is refused
    again = c.get(f"/integrations/producthunt/callback?code=goodcode&state={q['state'][0]}")
    assert "didn't start from your HuhQueue session" in again.text


def test_connect_without_oauth_env_shows_the_token_steps(human, monkeypatch):
    monkeypatch.delenv("PH_CLIENT_ID", raising=False)
    r = human.get("/integrations/producthunt/connect", follow_redirects=False)
    assert r.status_code == 200 and "Create Token" in r.text


def test_sample_workspace_never_calls_out(client):
    client.get("/demo", follow_redirects=True)
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    r = client.post("/api/integrations/ph/test", json={"value": PH_TOKEN}).json()
    assert r["ok"] is False and "sample" in r["message"].lower()
