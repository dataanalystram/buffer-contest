"""Instagram history/community/insights, App keys in Settings, hosted single-origin OAuth, YouTube PKCE flow."""
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from huhqueue.connectors import ChannelAuth, PermanentError
from huhqueue.connectors.instagram import GRAPH, InstagramConnector
from huhqueue.connectors.youtube import API as YT, TOKEN_URL as YT_TOKEN, pkce_challenge
from fastapi.testclient import TestClient

G = f"{GRAPH}/v26.0"


@pytest.fixture
def ig(settings):
    return InstagramConnector(settings, httpx.Client())


def a():
    return ChannelAuth(id="c", external_id="1789", username="huhbyte.ai", access_token="IGTOKEN")


MEDIA = {"data": [
    {"id": "M2", "caption": "Reel", "media_type": "VIDEO", "media_product_type": "REELS", "media_url": "https://cdn/v.mp4",
     "thumbnail_url": "https://cdn/t.jpg", "permalink": "https://www.instagram.com/reel/M2/", "timestamp": "2026-10-21T09:30:00+0000",
     "like_count": 40, "comments_count": 3},
    {"id": "M1", "caption": "Photo", "media_type": "IMAGE", "media_product_type": "FEED", "media_url": "https://cdn/p.jpg",
     "permalink": "https://www.instagram.com/p/M1/", "timestamp": "2026-10-20T18:30:00+0000", "like_count": 12, "comments_count": 0},
    {"id": "M0", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED", "media_url": "https://cdn/c.jpg",
     "permalink": "https://www.instagram.com/p/M0/", "timestamp": "2026-10-19T18:30:00+0000"}],
    "paging": {"cursors": {"before": "B", "after": "AFTER1"}, "next": "https://graph.instagram.com/next"}}


@respx.mock
def test_instagram_history_reads_all_past_posts(ig):
    r = respx.get(f"{G}/1789/media").respond(json=MEDIA)
    out = ig.list_media(a(), limit=3)
    p = r.calls[0].request.url.params
    assert p["access_token"] == "IGTOKEN" and p["limit"] == "3"
    for f in ("caption", "media_url", "thumbnail_url", "permalink", "timestamp", "like_count", "comments_count"):
        assert f in p["fields"]
    assert out["next"] == "AFTER1" and [i["external_id"] for i in out["items"]] == ["M2", "M1", "M0"]
    reel, photo, album = out["items"]
    assert reel["thumbnail_url"] == "https://cdn/t.jpg" and reel["product_type"] == "REELS" and reel["like_count"] == 40
    assert photo["thumbnail_url"] == "https://cdn/p.jpg" and album["caption"] == ""
    ig.list_media(a(), after="AFTER1")
    assert r.calls[1].request.url.params["after"] == "AFTER1"


@respx.mock
def test_instagram_history_last_page_has_no_cursor(ig):
    respx.get(f"{G}/1789/media").respond(json={"data": [], "paging": {"cursors": {"after": "X"}}})
    assert ig.list_media(a()) == {"items": [], "next": ""}


@respx.mock
def test_instagram_account_insights_and_fallback(ig):
    calls = []

    def handler(req):
        m = req.url.params["metric"]
        calls.append(m)
        if m == "follower_count":
            return httpx.Response(200, json={"data": [{"name": "follower_count", "period": "day",
                                                       "values": [{"value": 5, "end_time": "2026-10-20T07:00:00+0000"}]}]})
        if "," in m:
            return httpx.Response(400, json={"error": {"message": "(#100) metric[9] must be one of ...", "code": 100}})
        if m == "replies":
            return httpx.Response(400, json={"error": {"message": "unsupported", "code": 100}})
        return httpx.Response(200, json={"data": [{"name": m, "period": "day", "total_value": {"value": 10}}]})

    respx.get(f"{G}/1789/insights").mock(side_effect=handler)
    out = ig.account_insights(a(), days=90)
    assert out["days"] == 30  # Meta's max window
    assert out["totals"]["reach"] == 10 and out["totals"]["views"] == 10 and "replies" not in out["totals"]
    assert out["series"]["follower_count"] == [["2026-10-20", 5]]
    assert calls[0].count(",") >= 5  # tried one batched call first


@respx.mock
def test_instagram_insights_batch_ok(ig):
    respx.get(f"{G}/1789/insights").mock(side_effect=lambda req: httpx.Response(200, json={"data": [
        {"name": n, "total_value": {"value": i}} for i, n in enumerate(req.url.params["metric"].split(","))]}))
    out = ig.account_insights(a(), days=7)
    assert out["totals"]["reach"] == 0 and out["totals"]["views"] == 1 and out["days"] == 7


@respx.mock
def test_instagram_channel_page_endpoints(api, core):
    """Connect by token, then the channel page's History, Community and Insights tabs read live data."""
    respx.get(f"{G}/me").respond(json={"user_id": "1789", "id": "as", "username": "huhbyte.ai", "name": "Huh Byte",
                                       "profile_picture_url": "https://cdn/av.jpg", "account_type": "BUSINESS",
                                       "followers_count": 1200, "media_count": 3})
    ch = api.post("/api/connect/instagram/token", {"access_token": "IGTOKEN"}).json()
    assert ch["username"] == "huhbyte.ai" and ch["followers_count"] == 1200 and ch["media_count"] == 3
    assert ch["avatar_url"] == "https://cdn/av.jpg"
    respx.get(f"{G}/1789/media").respond(json=MEDIA)
    h = api.get(f"/api/channels/{ch['id']}/history?limit=3").json()
    assert h["supported"] and len(h["items"]) == 3 and h["next"] == "AFTER1"
    respx.get(f"{G}/M2/comments").respond(json={"data": [
        {"id": "C1", "text": "great reel", "username": "fan1", "timestamp": "2026-10-21T10:00:00+0000", "like_count": 1,
         "replies": {"data": [{"id": "C2", "text": "thanks!", "username": "huhbyte.ai", "timestamp": "2026-10-21T11:00:00+0000"}]}}]})
    tags = respx.get(f"{G}/1789/tags").respond(json={"data": [
        {"id": "T1", "caption": "shoutout @huhbyte.ai", "username": "partner", "permalink": "https://instagram.com/p/t1",
         "timestamp": "2026-10-21T10:00:00+0000", "media_type": "IMAGE"}]})
    assert api.post("/api/comments/sync", {"channel_id": ch["id"]}).json()["new"] >= 1
    assert tags.called
    rows = api.get(f"/api/comments?channel_id={ch['id']}").json()
    assert [r["text"] for r in rows] == ["great reel"]          # mentions stay out of the comments list
    men = api.get(f"/api/channels/{ch['id']}/mentions").json()
    assert men["supported"] and [m["author"] for m in men["items"]] == ["partner"] and men["items"][0]["kind"] == "mention"
    rep = respx.post(f"{G}/C1/replies").respond(json={"id": "C3"})
    r = api.post(f"/api/comments/{rows[0]['id']}/reply", {"text": "glad you liked it"})
    assert r.status_code == 200, r.text
    assert "glad you liked it" in rep.calls[0].request.content.decode().replace("+", " ")
    respx.get(f"{G}/1789/insights").respond(json={"data": [{"name": "reach", "total_value": {"value": 999}}]})
    ins = api.get(f"/api/channels/{ch['id']}/insights?days=28").json()
    assert ins["totals"]["reach"] == 999


def test_history_unsupported_network_is_empty(api, channel):
    h = api.get(f"/api/channels/{channel['id']}/history").json()
    assert h["items"] == [] and h["supported"] is False


# ----------------------------------------------------------------------------- App keys from the Settings UI
def test_app_keys_set_in_ui_configure_connector(api, core, settings):
    settings.instagram_app_id = settings.instagram_app_secret = ""
    core._env_keys.update(instagram_app_id="", instagram_app_secret="")
    core.apply_app_keys()
    r = api.post("/api/connect/instagram")
    assert r.status_code == 400 and "App keys" in r.json()["detail"]["message"]
    keys = api.get("/api/app-keys").json()
    assert not keys["instagram"]["configured"] and keys["instagram"]["redirect_uri"].endswith("/oauth/instagram/callback")
    r = api.put("/api/app-keys/instagram", {"instagram_app_id": "1234567890", "instagram_app_secret": "s3cr3t-value-xyz"})
    assert r.status_code == 200 and r.json()["configured"]
    f = {x["key"]: x for x in r.json()["fields"]}
    assert f["instagram_app_id"]["value"] == "1234567890" and f["instagram_app_id"]["source"] == "ui"
    assert "value" not in f["instagram_app_secret"] and f["instagram_app_secret"]["hint"] == "••••-xyz"
    # stored encrypted, never in plain text
    from huhqueue.models import KV
    with core.db.session() as s:
        raw = str(s.get(KV, "app_keys").value)
    assert "s3cr3t" not in raw and "1234567890" not in raw
    q = parse_qs(urlparse(api.post("/api/connect/instagram").json()["url"]).query)
    assert q["client_id"] == ["1234567890"]
    # leaving the secret out keeps it; empty string clears back to .env
    api.put("/api/app-keys/instagram", {"instagram_app_id": "999"})
    assert core.settings.instagram_app_secret == "s3cr3t-value-xyz" and core.settings.instagram_app_id == "999"
    api.put("/api/app-keys/instagram", {"instagram_app_id": "", "instagram_app_secret": ""})
    assert not core.connector("instagram").is_configured()
    # survives a restart
    api.put("/api/app-keys/x", {"x_client_id": "XID"})
    from huhqueue.core import Core
    assert Core(core.settings).settings.x_client_id == "XID"


def test_app_keys_never_via_agent_tokens(app, api, core):
    raw = api.post("/api/tokens", {"name": "bot", "scopes": ["read", "write", "publish", "admin"], "require_approval": False}).json()["token"]
    c = TestClient(app)
    h = {"Authorization": f"Bearer {raw}"}
    assert c.get("/api/app-keys", headers=h).status_code == 403
    assert c.put("/api/app-keys/instagram", json={"instagram_app_id": "evil"}, headers=h).status_code == 403
    assert TestClient(app).get("/api/app-keys").status_code == 401
    assert api.put("/api/app-keys/nope", {}).status_code == 404


# ----------------------------------------------------------------------------- hosted install: one public origin
@respx.mock
def test_single_origin_callback_on_ui_app(api, core):
    core.update_prefs({"public_base_url": "https://8787-m.instabox.tech"})
    start = api.post("/api/connect/instagram").json()
    assert start["redirect_uri"] == "https://8787-m.instabox.tech/oauth/instagram/callback"
    q = parse_qs(urlparse(start["url"]).query)
    assert q["redirect_uri"] == [start["redirect_uri"]]
    respx.post("https://api.instagram.com/oauth/access_token").respond(json={"access_token": "S", "user_id": 1789})
    respx.get(f"{GRAPH}/access_token").respond(json={"access_token": "LONG", "expires_in": 5184000})
    respx.get(f"{G}/me").respond(json={"user_id": "1789", "username": "huhbyte.ai", "followers_count": 5, "media_count": 1})
    r = api.c.get(f"/oauth/instagram/callback?code=abc&state={q['state'][0]}#_", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/#/channel/") and "connected=1" in r.headers["location"]
    bad = api.c.get("/oauth/instagram/callback?code=abc&state=forged", follow_redirects=False)
    assert bad.headers["location"].startswith("/#/channels?connect_error=")
    denied = api.c.get(f"/oauth/instagram/callback?error=access_denied&error_description=User+denied&state=x", follow_redirects=False)
    assert "User%20denied" in denied.headers["location"]
    assert api.c.get("/privacy").status_code == 200


# ----------------------------------------------------------------------------- YouTube flow through oauth.start
@respx.mock
def test_youtube_connect_flow_with_pkce(api, edge):
    start = api.post("/api/connect/youtube").json()
    q = parse_qs(urlparse(start["url"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"] and q["prompt"] == ["consent"]
    tok = respx.post(YT_TOKEN).respond(json={"access_token": "YA", "refresh_token": "YR", "expires_in": 3599, "scope": "s"})
    respx.get(f"{YT}/channels").respond(json={"items": [{"id": "UC1", "snippet": {"title": "Huh Byte", "customUrl": "@huhbyte",
                                                       "thumbnails": {"default": {"url": "https://yt/a.jpg"}}},
                                                       "statistics": {"subscriberCount": "10", "videoCount": "2"}}]})
    r = edge.get(f"/oauth/youtube/callback?code=G1&state={q['state'][0]}", follow_redirects=False)
    assert r.status_code == 303 and "connected=1" in r.headers["location"], r.headers["location"]
    sent = parse_qs(tok.calls[0].request.content.decode())
    assert pkce_challenge(sent["code_verifier"][0]) == q["code_challenge"][0]
    assert sent["redirect_uri"] == [start["redirect_uri"]]


def test_callback_url_follows_the_browser_origin(api, core):
    """No public URL configured: the callback is wherever the UI is served (localhost, or any hosted URL)."""
    h = api._h()
    local = api.c.post("/api/connect/instagram", headers=h).json()
    assert local["redirect_uri"] == "http://testserver/oauth/instagram/callback"
    assert core.public_base_url() == ""   # plain http is never used as the public media base

    hosted = {**h, "X-Forwarded-Proto": "https", "X-Forwarded-Host": "8787-new.instabox.tech"}
    start = api.c.post("/api/connect/instagram", headers=hosted).json()
    assert start["redirect_uri"] == "https://8787-new.instabox.tech/oauth/instagram/callback"
    assert parse_qs(urlparse(start["url"]).query)["redirect_uri"] == [start["redirect_uri"]]
    assert core.public_base_url() == "https://8787-new.instabox.tech"   # the owner's https origin is remembered
    keys = api.c.get("/api/app-keys", headers=hosted).json()
    assert keys["instagram"]["deauthorize_url"] == "https://8787-new.instabox.tech/oauth/instagram/deauthorize"

    core.update_prefs({"public_base_url": "https://fixed.example"})   # an explicit URL wins
    assert api.c.post("/api/connect/instagram", headers=hosted).json()["redirect_uri"] == "https://fixed.example/oauth/instagram/callback"
