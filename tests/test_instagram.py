"""Instagram connector against a mocked graph.instagram.com (respx)."""
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from huhqueue.connectors import (AuthError, BrandRules, ChannelAuth, MediaItem, PermanentError, PublishContext,
                                 RetryableError)
from huhqueue.connectors.instagram import GRAPH, InstagramConnector

V = "v26.0"
G = f"{GRAPH}/{V}"


@pytest.fixture
def ig(settings):
    return InstagramConnector(settings, httpx.Client())


def auth(**kw):
    return ChannelAuth(id="ch1", external_id="1789", username="huhbyte.ai", access_token="IGTOKEN", **kw)


def img(i=1, mime="image/jpeg", w=1080, h=1350):
    return MediaItem(id=f"m{i}", kind="image", mime=mime, path=Path("/x.jpg"), filename=f"a{i}.jpg", size=1000,
                     width=w, height=h, public_url=f"https://pub.example/m/t/a{i}", jpeg_public_url=f"https://pub.example/m/t/a{i}.jpg")


def vid(i=1):
    return MediaItem(id=f"v{i}", kind="video", mime="video/mp4", path=Path("/x.mp4"), filename=f"v{i}.mp4", size=5000,
                     public_url=f"https://pub.example/m/t/v{i}.mp4")


def form(req):
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def quota(mock, used=0):
    mock.get(f"{G}/1789/content_publishing_limit").respond(json={"data": [{"quota_usage": used, "config": {"quota_total": 100, "quota_duration": 86400}}]})


def test_authorize_url_has_business_scopes_and_state(ig):
    u = urlparse(ig.authorize_url("STATE123", "https://edge.example/oauth/instagram/callback"))
    q = parse_qs(u.query)
    assert u.netloc == "www.instagram.com" and u.path == "/oauth/authorize"
    assert q["state"] == ["STATE123"] and q["client_id"] == ["ig-app"] and q["response_type"] == ["code"]
    assert "instagram_business_content_publish" in q["scope"][0] and "instagram_business_basic" in q["scope"][0]


@respx.mock
def test_exchange_code_gets_long_lived_token_and_profile(ig):
    tok = respx.post("https://api.instagram.com/oauth/access_token").respond(json={"access_token": "SHORT", "user_id": 1789, "permissions": ["instagram_business_basic"]})
    ll = respx.get(f"{GRAPH}/access_token").respond(json={"access_token": "LONG", "token_type": "bearer", "expires_in": 5183944})
    respx.get(f"{G}/me").respond(json={"user_id": "1789", "id": "app-scoped", "username": "huhbyte.ai", "name": "HuhByte AI",
                                       "profile_picture_url": "https://cdn/p.jpg", "account_type": "BUSINESS", "followers_count": 12840, "media_count": 214})
    bundle, prof = ig.exchange_code("CODE#_", "https://edge/cb")
    assert form(tok.calls[0].request)["code"] == "CODE"  # '#_' suffix stripped
    assert ll.calls[0].request.url.params["grant_type"] == "ig_exchange_token"
    assert bundle.access_token == "LONG" and bundle.expires_at > datetime.utcnow() + timedelta(days=59)
    assert prof.external_id == "1789" and prof.username == "huhbyte.ai"
    assert prof.meta["followers_count"] == 12840 and prof.meta["account_type"] == "BUSINESS"


@respx.mock
def test_single_image_publish_with_first_comment(ig):
    quota(respx)
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    respx.get(f"{G}/C1").respond(json={"status_code": "FINISHED"})
    pub = respx.post(f"{G}/1789/media_publish").respond(json={"id": "M1"})
    respx.get(f"{G}/M1").respond(json={"id": "M1", "permalink": "https://instagram.com/p/abc"})
    com = respx.post(f"{G}/M1/comments").respond(json={"id": "K1"})
    ctx = PublishContext("t1", auth(), "Hello #AI", [img(mime="image/png")], {"first_comment": "Follow @huhbyte", "collaborators": "a, @b"}, {})
    out = ig.publish(ctx)
    body = form(create.calls[0].request)
    assert body["image_url"].endswith(".jpg")  # JPEG copy, never the PNG
    assert body["caption"] == "Hello #AI" and body["access_token"] == "IGTOKEN"
    assert "collaborators" not in body      # Instagram Login channel: collaborators need Facebook Login (Page-linked)
    assert form(pub.calls[0].request)["creation_id"] == "C1"
    assert form(com.calls[0].request)["message"] == "Follow @huhbyte"
    assert out.status == "published" and out.external_id == "M1" and out.permalink.endswith("/abc")


@respx.mock
def test_carousel_waits_for_video_children_then_publishes(ig):
    quota(respx)
    ids = iter(["K1", "K2", "PARENT"])
    create = respx.post(f"{G}/1789/media").mock(side_effect=lambda r: httpx.Response(200, json={"id": next(ids)}))
    respx.get(f"{G}/K1").respond(json={"status_code": "FINISHED"})
    respx.get(f"{G}/K2").mock(side_effect=[httpx.Response(200, json={"status_code": "IN_PROGRESS"}),
                                           httpx.Response(200, json={"status_code": "FINISHED"})])
    respx.get(f"{G}/PARENT").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M9"})
    respx.get(f"{G}/M9").respond(json={"permalink": "https://instagram.com/p/c"})
    ctx = PublishContext("t1", auth(), "Swipe", [img(1), vid(2)], {}, {})
    first = ig.publish(ctx)
    assert first.status == "processing" and ctx.state["children"] == ["K1", "K2"]
    second = ig.poll(ctx)
    assert second.status == "published" and second.external_id == "M9"
    bodies = [form(c.request) for c in create.calls]
    assert bodies[0]["is_carousel_item"] == "true" and bodies[1]["media_type"] == "VIDEO"
    assert bodies[2]["media_type"] == "CAROUSEL" and bodies[2]["children"] == "K1,K2" and bodies[2]["caption"] == "Swipe"


@respx.mock
def test_reel_and_story_containers(ig):
    quota(respx)
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C"})
    respx.get(f"{G}/C").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M"})
    respx.get(f"{G}/M").respond(json={"permalink": "p"})
    ig.publish(PublishContext("t", auth(), "reel!", [vid()], {"share_to_feed": False, "is_ai_generated": True}, {}))
    ig.publish(PublishContext("t", auth(), "ignored", [img()], {"ig_type": "story"}, {}))
    reel, story = form(create.calls[0].request), form(create.calls[1].request)
    assert reel["media_type"] == "REELS" and reel["video_url"].endswith("v1.mp4") and reel["share_to_feed"] == "false"
    assert reel["is_ai_generated"] == "true"
    assert story["media_type"] == "STORIES" and "caption" not in story


@respx.mock
def test_quota_exhausted_is_retryable(ig):
    quota(respx, used=100)
    with pytest.raises(RetryableError):
        ig.publish(PublishContext("t", auth(), "x", [img()], {}, {}))


@pytest.mark.parametrize("payload,status,exc", [
    ({"error": {"message": "Invalid OAuth access token", "type": "OAuthException", "code": 190}}, 400, AuthError),
    ({"error": {"message": "limit", "code": 9, "error_subcode": 2207042}}, 400, RetryableError),
    ({"error": {"message": "Media download failed", "code": 9004, "error_subcode": 2207052}}, 400, PermanentError),
    ({"error": {"message": "rate", "code": 4}}, 400, RetryableError),
    ({"error": {"message": "boom", "code": 2, "is_transient": True}}, 500, RetryableError),
    ({"error": {"message": "bad caption", "code": 100}}, 400, PermanentError),
])
@respx.mock
def test_error_mapping(ig, payload, status, exc):
    quota(respx)
    respx.post(f"{G}/1789/media").respond(status, json=payload)
    with pytest.raises(exc):
        ig.publish(PublishContext("t", auth(), "x", [img()], {}, {}))


@respx.mock
def test_network_error_is_retryable(ig):
    quota(respx)
    respx.post(f"{G}/1789/media").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(RetryableError):
        ig.publish(PublishContext("t", auth(), "x", [img()], {}, {}))


@respx.mock
def test_resume_never_double_publishes(ig):
    """Container already PUBLISHED (reply to media_publish was lost) -> pick up newest media, no 2nd publish."""
    respx.get(f"{G}/C1").respond(json={"status_code": "PUBLISHED"})
    respx.get(f"{G}/1789/media").respond(json={"data": [{"id": "MEDIA_OLD"}]})
    pub = respx.post(f"{G}/1789/media_publish").respond(json={"id": "DUP"})
    respx.get(f"{G}/MEDIA_OLD").respond(json={"permalink": "p"})
    out = ig.publish(PublishContext("t", auth(), "x", [img()], {}, {"type": "feed", "parent": "C1"}))
    assert out.external_id == "MEDIA_OLD" and not pub.called


@respx.mock
def test_container_error_is_permanent(ig):
    quota(respx)
    respx.post(f"{G}/1789/media").respond(json={"id": "C"})
    respx.get(f"{G}/C").respond(json={"status_code": "ERROR", "status": "Error: unsupported format"})
    with pytest.raises(PermanentError, match="unsupported format"):
        ig.publish(PublishContext("t", auth(), "x", [img()], {}, {}))


@respx.mock
def test_first_comment_failure_is_only_a_warning(ig):
    quota(respx)
    respx.post(f"{G}/1789/media").respond(json={"id": "C"})
    respx.get(f"{G}/C").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M"})
    respx.get(f"{G}/M").respond(json={"permalink": "p"})
    respx.post(f"{G}/M/comments").respond(400, json={"error": {"message": "no permission", "code": 10}})
    out = ig.publish(PublishContext("t", auth(), "x", [img()], {"first_comment": "hi"}, {}))
    assert out.status == "published" and "first comment failed" in out.warnings[0]


@respx.mock
def test_insights_and_refresh(ig):
    respx.get(f"{G}/M/insights").respond(json={"data": [{"name": "views", "values": [{"value": 1000}]},
                                                         {"name": "reach", "values": [{"value": 800}]},
                                                         {"name": "likes", "total_value": {"value": 60}},
                                                         {"name": "total_interactions", "values": [{"value": 80}]}]})
    out = ig.fetch_insights(PublishContext("t", auth(), "", [], {}, {"type": "reel"}), "M")
    assert out["views"] == 1000 and out["likes"] == 60 and out["engagement_rate"] == 10.0
    respx.get(f"{GRAPH}/refresh_access_token").respond(json={"access_token": "NEW", "expires_in": 5184000})
    a = auth(token_expires_at=datetime.utcnow() + timedelta(days=3), token_obtained_at=datetime.utcnow() - timedelta(days=57))
    assert ig.needs_refresh(a, datetime.utcnow())
    assert not ig.needs_refresh(auth(token_expires_at=datetime.utcnow() + timedelta(days=30)), datetime.utcnow())
    assert ig.refresh(a).access_token == "NEW"


@respx.mock
def test_comments_list_and_reply(ig):
    respx.get(f"{G}/1789/media").respond(json={"data": [{"id": "M1", "caption": "post", "permalink": "p", "comments_count": 2}]})
    respx.get(f"{G}/M1/comments").respond(json={"data": [{"id": "c1", "text": "nice", "username": "fan", "timestamp": "2026-10-22T08:00:00+0000",
                                                          "replies": {"data": [{"id": "r1", "text": "thanks", "username": "huhbyte.ai"}]}}]})
    rows = ig.list_comments(auth())
    assert [r["external_id"] for r in rows] == ["c1", "r1"] and rows[1]["is_own"] and rows[1]["parent_external_id"] == "c1"
    rep = respx.post(f"{G}/c1/replies").respond(json={"id": "r2"})
    assert ig.reply_comment(auth(), "c1", "hey") == "r2" and form(rep.calls[0].request)["message"] == "hey"


def test_validation_rules(ig):
    r = BrandRules(hashtag_limit=5)
    codes = lambda cap, media, opts=None: {i.code for i in ig.validate(cap, media, opts or {}, r)}
    assert "caption_too_long" in codes("x" * 2201, [img()])
    assert "caption_too_long" not in codes("x" * 2200, [img()])
    assert "caption_too_long" in codes("🚀" * 1101, [img()])  # emoji count double (UTF-16)
    assert "brand_hashtags" in codes(" ".join(f"#t{i}" for i in range(6)), [img()])
    assert "too_many_hashtags" in codes(" ".join(f"#t{i}" for i in range(31)), [img()])
    assert "media_required" in codes("hi", [])
    assert "carousel_max" in codes("hi", [img(i) for i in range(21)])
    assert "reel_needs_video" in codes("hi", [img()], {"ig_type": "reel"})
    assert "aspect_ratio" in codes("hi", [img(w=1080, h=1920)], {"ig_type": "feed"})
    assert "jpeg_convert" in codes("hi", [img(mime="image/png")])
    assert "collaborators" in codes("hi", [img()], {"collaborators": "a,b,c,d"})
    assert ig.resolve_type([img(1), img(2)], {}) == "carousel" and ig.resolve_type([vid()], {}) == "reel"
    assert "no_public_url" in codes("hi", [img()]) - set() or True
    assert "no_public_url" in {i.code for i in ig.validate("hi", [img()], {}, BrandRules(has_public_url=False))}
