"""X connector against a mocked api.x.com (respx): OAuth 2.0 PKCE, refresh rotation, chunked media, posts, history."""
import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from huhqueue.connectors import (AuthError, BrandRules, ChannelAuth, MediaItem, PermanentError, PublishContext,
                                 RetryableError)
from huhqueue.connectors.x import API, AUTH_URL, TOKEN_URL, XConnector, pkce_challenge, weighted_len
from huhqueue.services import oauth


@pytest.fixture
def xc(settings):
    return XConnector(settings, httpx.Client())


def auth(**kw):
    return ChannelAuth(id="c", external_id="42", username="huhbyte", access_token="XTOK", **kw)


def form(req):
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


ME = {"data": {"id": "42", "name": "Huh Byte", "username": "huhbyte", "verified": False,
               "profile_image_url": "https://pbs.twimg.com/p_normal.jpg",
               "public_metrics": {"followers_count": 321, "tweet_count": 77, "following_count": 5}}}


def test_authorize_url_pkce_scopes_state(xc):
    u = urlparse(xc.authorize_url("STATE", "https://hq.example/oauth/x/callback", "v" * 50))
    q = parse_qs(u.query)
    assert f"{u.scheme}://{u.netloc}{u.path}" == AUTH_URL
    assert q["client_id"] == ["x-id"] and q["state"] == ["STATE"] and q["response_type"] == ["code"]
    assert q["code_challenge"] == [pkce_challenge("v" * 50)] and q["code_challenge_method"] == ["S256"]
    for s in ("tweet.read", "tweet.write", "users.read", "offline.access", "media.write"):
        assert s in q["scope"][0].split(" ")
    assert q["redirect_uri"] == ["https://hq.example/oauth/x/callback"]


def test_authorize_without_verifier_or_keys(xc, settings):
    with pytest.raises(PermanentError):
        xc.authorize_url("S", "https://x/cb", "")
    settings.x_client_id = ""
    assert not xc.is_configured()


@respx.mock
def test_exchange_uses_basic_auth_verifier_and_reads_profile(xc):
    tok = respx.post(TOKEN_URL).respond(json={"access_token": "AT", "refresh_token": "RT", "expires_in": 7200,
                                              "scope": "tweet.read tweet.write users.read offline.access media.write"})
    respx.get(f"{API}/users/me").respond(json=ME)
    bundle, prof = xc.exchange_code("CODE", "https://hq/oauth/x/callback", "VERIFIER")
    req = tok.calls[0].request
    f = form(req)
    assert f["grant_type"] == "authorization_code" and f["code"] == "CODE" and f["code_verifier"] == "VERIFIER"
    assert f["redirect_uri"] == "https://hq/oauth/x/callback" and f["client_id"] == "x-id"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(b"x-id:x-secret").decode()
    assert bundle.access_token == "AT" and bundle.refresh_token == "RT"
    assert bundle.expires_at > datetime.utcnow() + timedelta(minutes=110)
    assert prof.external_id == "42" and prof.username == "huhbyte"
    assert prof.meta["followers_count"] == 321 and prof.meta["media_count"] == 77
    assert prof.avatar_url.endswith("p_400x400.jpg")


@respx.mock
def test_refresh_rotates_refresh_token_and_needs_refresh(xc):
    r = respx.post(TOKEN_URL).respond(json={"access_token": "AT2", "refresh_token": "RT2", "expires_in": 7200})
    now = datetime.utcnow()
    a = auth(refresh_token="RT1", token_expires_at=now + timedelta(minutes=5))
    assert xc.needs_refresh(a, now)
    assert not xc.needs_refresh(auth(refresh_token="RT1", token_expires_at=now + timedelta(hours=1)), now)
    b = xc.refresh(a)
    assert form(r.calls[0].request)["refresh_token"] == "RT1" and b.access_token == "AT2" and b.refresh_token == "RT2"


@respx.mock
def test_invalid_grant_is_auth_error(xc):
    respx.post(TOKEN_URL).respond(400, json={"error": "invalid_grant", "error_description": "Value passed for the token was invalid."})
    with pytest.raises(AuthError):
        xc.refresh(auth(refresh_token="dead"))


@respx.mock
def test_oauth_start_and_callback_end_to_end(core, api, edge):
    respx.post(TOKEN_URL).respond(json={"access_token": "AT", "refresh_token": "RT", "expires_in": 7200})
    respx.get(f"{API}/users/me").respond(json=ME)
    start = api.post("/api/connect/x").json()
    q = parse_qs(urlparse(start["url"]).query)
    assert start["url"].startswith(AUTH_URL) and q["code_challenge_method"] == ["S256"]
    r = edge.get(f"/oauth/x/callback?code=C1&state={q['state'][0]}", follow_redirects=False)
    assert r.status_code == 303 and "connected=1" in r.headers["location"]
    chans = api.get("/api/channels").json()
    x = [c for c in chans if c["provider"] == "x"][0]
    assert x["username"] == "huhbyte" and x["followers_count"] == 321
    # the PKCE verifier sent at exchange matches the challenge in the authorize URL
    sent = parse_qs(respx.calls[0].request.content.decode())["code_verifier"][0]
    assert pkce_challenge(sent) == q["code_challenge"][0]
    # state is single-use
    again = edge.get(f"/oauth/x/callback?code=C1&state={q['state'][0]}", follow_redirects=False)
    assert "connect_error" in again.headers["location"]


def test_validate_rules(xc):
    rules = BrandRules(hashtag_limit=2)
    long = "a" * 281
    assert any(i.code == "caption_too_long" for i in xc.validate(long, [], {}, rules))
    assert not any(i.code == "caption_too_long" for i in xc.validate(long, [], {"long_post": True}, rules))
    assert weighted_len("see https://example.com/very/long/url/that/is/long") == 4 + 23
    assert weighted_len("🤯") == 2
    imgs = [MediaItem(id=str(i), kind="image", mime="image/png", path=Path("/x"), filename=f"{i}.png", size=10) for i in range(5)]
    assert any(i.code == "x_max_media" for i in xc.validate("hi", imgs, {}, rules))
    vid = MediaItem(id="v", kind="video", mime="video/mp4", path=Path("/x"), filename="v.mp4", size=10)
    assert any(i.code == "x_mix" for i in xc.validate("hi", [vid, imgs[0]], {}, rules))
    assert not [i for i in xc.validate("hi #ai", [vid], {}, rules) if i.level == "error"]


def _media(tmp_path, kind="image", size=100):
    p = tmp_path / ("v.mp4" if kind == "video" else "a.png")
    p.write_bytes(b"\0" * size)
    return MediaItem(id="m", kind=kind, mime="video/mp4" if kind == "video" else "image/png", path=p,
                     filename=p.name, size=size, alt_text="alt" if kind == "image" else "")


@respx.mock
def test_publish_image_post_with_reply(xc, tmp_path):
    init = respx.post(f"{API}/media/upload/initialize").respond(json={"data": {"id": "M1", "media_key": "3_M1"}})
    app = respx.post(f"{API}/media/upload/M1/append").respond(204)
    respx.post(f"{API}/media/upload/M1/finalize").respond(json={"data": {"id": "M1", "size": 100}})
    meta = respx.post(f"{API}/media/metadata").respond(json={"data": {"id": "M1"}})
    tw = respx.post(f"{API}/tweets").mock(side_effect=[
        httpx.Response(201, json={"data": {"id": "T1", "text": "hi"}}),
        httpx.Response(201, json={"data": {"id": "T2", "text": "Follow"}})])
    ctx = PublishContext("t", auth(), "New model drop", [_media(tmp_path)], {"first_comment": "Follow @huhbyte"}, {})
    out = xc.publish(ctx)
    assert out.status == "published" and out.external_id == "T1" and out.permalink == "https://x.com/huhbyte/status/T1"
    assert json.loads(init.calls[0].request.content) == {"media_type": "image/png", "total_bytes": 100, "media_category": "tweet_image"}
    assert app.calls[0].request.headers["authorization"] == "Bearer XTOK"
    assert json.loads(meta.calls[0].request.content)["metadata"]["alt_text"]["text"] == "alt"
    body = json.loads(tw.calls[0].request.content)
    assert body == {"text": "New model drop", "media": {"media_ids": ["M1"]}}
    assert json.loads(tw.calls[1].request.content)["reply"] == {"in_reply_to_tweet_id": "T1"}
    # a retry after success never posts twice
    again = xc.publish(ctx)
    assert again.external_id == "T1" and tw.call_count == 2


@respx.mock
def test_publish_video_waits_for_processing(xc, tmp_path, monkeypatch):
    monkeypatch.setattr("huhqueue.connectors.x.CHUNK", 40)
    respx.post(f"{API}/media/upload/initialize").respond(json={"data": {"id": "V1"}})
    app = respx.post(f"{API}/media/upload/V1/append").respond(204)
    respx.post(f"{API}/media/upload/V1/finalize").respond(json={"data": {"id": "V1", "processing_info": {"state": "pending", "check_after_secs": 1}}})
    st = respx.get(f"{API}/media/upload").mock(side_effect=[
        httpx.Response(200, json={"data": {"processing_info": {"state": "in_progress"}}}),
        httpx.Response(200, json={"data": {"processing_info": {"state": "succeeded"}}})])
    tw = respx.post(f"{API}/tweets").respond(201, json={"data": {"id": "T9"}})
    ctx = PublishContext("t", auth(), "Watch this", [_media(tmp_path, "video", 100)], {}, {})
    first = xc.publish(ctx)
    assert first.status == "processing" and app.call_count == 3  # 100 bytes in 40-byte segments
    assert [form_seg for form_seg in (b"segment_index" in c.request.content for c in app.calls)] == [True] * 3
    assert st.calls[0].request.url.params["command"] == "STATUS"
    done = xc.poll(ctx)
    assert done.status == "published" and done.external_id == "T9" and tw.call_count == 1
    assert app.call_count == 3  # nothing re-uploaded


@respx.mock
def test_video_processing_failure_and_rate_limit(xc, tmp_path):
    respx.get(f"{API}/media/upload").respond(json={"data": {"processing_info": {"state": "failed", "error": {"message": "InvalidMedia"}}}})
    ctx = PublishContext("t", auth(), "x", [], {}, {"uploaded": True, "media_ids": ["V"], "pending": ["V"]})
    with pytest.raises(PermanentError, match="InvalidMedia"):
        xc.poll(ctx)
    respx.post(f"{API}/tweets").respond(429, json={"title": "Too Many Requests", "detail": "Too Many Requests"})
    with pytest.raises(RetryableError):
        xc.poll(PublishContext("t", auth(), "x", [], {}, {"uploaded": True, "media_ids": [], "pending": []}))


@respx.mock
def test_duplicate_is_permanent_and_401_needs_reconnect(xc):
    respx.post(f"{API}/tweets").mock(side_effect=[
        httpx.Response(403, json={"title": "Forbidden", "detail": "You are not allowed to create a Tweet with duplicate content."}),
        httpx.Response(401, json={"title": "Unauthorized", "detail": "Unauthorized"})])
    c = PublishContext("t", auth(), "same", [], {}, {"uploaded": True, "media_ids": [], "pending": []})
    with pytest.raises(PermanentError, match="duplicate"):
        xc.poll(c)
    with pytest.raises(AuthError):
        xc.poll(PublishContext("t", auth(), "same", [], {}, {"uploaded": True, "media_ids": [], "pending": []}))


@respx.mock
def test_insights_and_history(xc):
    respx.get(f"{API}/tweets/T1").respond(json={"data": {"id": "T1", "public_metrics": {
        "impression_count": 1000, "like_count": 50, "reply_count": 5, "retweet_count": 10, "quote_count": 2, "bookmark_count": 3}}})
    ins = xc.fetch_insights(PublishContext("t", auth(), "", [], {}, {}), "T1")
    assert ins["views"] == 1000 and ins["shares"] == 12 and ins["engagement_rate"] == 7.0
    tl = respx.get(f"{API}/users/42/tweets").respond(json={
        "data": [{"id": "T1", "text": "hello", "created_at": "2026-10-20T10:00:00.000Z", "attachments": {"media_keys": ["3_1"]},
                  "public_metrics": {"like_count": 4, "reply_count": 1}},
                 {"id": "T0", "text": "text only", "created_at": "2026-10-19T10:00:00.000Z", "public_metrics": {}}],
        "includes": {"media": [{"media_key": "3_1", "type": "photo", "url": "https://pbs.twimg.com/a.jpg"}]},
        "meta": {"next_token": "NEXT"}})
    h = xc.list_media(auth(), limit=10)
    assert tl.calls[0].request.url.params["max_results"] == "10"
    assert h["next"] == "NEXT" and h["items"][0]["thumbnail_url"] == "https://pbs.twimg.com/a.jpg"
    assert h["items"][0]["permalink"] == "https://x.com/huhbyte/status/T1" and h["items"][1]["media_type"] == "TEXT"
    xc.list_media(auth(), after="NEXT")
    assert tl.calls[1].request.url.params["pagination_token"] == "NEXT"


def test_x_is_a_builtin_plugin(core):
    d = core.connector("x").describe()
    assert d["configured"] and d["history"] and core.connector("x").uses_pkce
    assert core.redirect_uri("x").endswith("/oauth/x/callback")
    assert oauth  # imported for the end-to-end test above
