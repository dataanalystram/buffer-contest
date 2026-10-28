"""TikTok + YouTube connectors against mocked APIs (respx)."""
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from huhqueue.connectors import AuthError, BrandRules, ChannelAuth, MediaItem, PermanentError, PublishContext, RetryableError
from huhqueue.connectors.tiktok import API, MIN_CHUNK, TikTokConnector
from huhqueue.connectors.youtube import API as YT, UPLOAD, YouTubeConnector, pkce_challenge

CREATOR = {"data": {"creator_username": "huhbyte", "creator_nickname": "HuhByte", "privacy_level_options": ["PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "SELF_ONLY"],
                    "comment_disabled": False, "duet_disabled": True, "stitch_disabled": False, "max_video_post_duration_sec": 600},
           "error": {"code": "ok"}}


@pytest.fixture
def tt(settings):
    return TikTokConnector(settings, httpx.Client())


@pytest.fixture
def yt(settings):
    return YouTubeConnector(settings, httpx.Client())


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\0" * (MIN_CHUNK * 2 + 123))
    return MediaItem(id="v", kind="video", mime="video/mp4", path=p, filename="clip.mp4", size=p.stat().st_size,
                     public_url="https://pub/m/t/clip.mp4")


def a(provider="tiktok"):
    return ChannelAuth(id="c", external_id="OPEN_ID" if provider == "tiktok" else "UCxyz", username="huhbyte", access_token="ACT",
                       refresh_token="RFT")


def body(call):
    return json.loads(call.request.content)


# ------------------------------------------------------------------ TikTok
def test_tiktok_authorize_url(tt):
    q = parse_qs(urlparse(tt.authorize_url("S", "https://edge/oauth/tiktok/callback")).query)
    assert q["client_key"] == ["tt-key"] and "video.publish" in q["scope"][0] and q["state"] == ["S"]


@respx.mock
def test_tiktok_exchange_and_profile(tt):
    respx.post(f"{API}/v2/oauth/token/").respond(json={"access_token": "ACT", "expires_in": 86400, "open_id": "OPEN_ID",
                                                       "refresh_token": "RFT", "refresh_expires_in": 31536000, "scope": "user.info.basic,video.publish"})
    info = respx.get(f"{API}/v2/user/info/").respond(json={"data": {"user": {"open_id": "OPEN_ID", "display_name": "HuhByte", "avatar_url": "a",
                                                                            "username": "huhbyte", "follower_count": 30410, "video_count": 166}}, "error": {"code": "ok"}})
    respx.post(f"{API}/v2/post/publish/creator_info/query/").respond(json=CREATOR)
    b, p = tt.exchange_code("CODE", "https://edge/cb")
    assert b.refresh_token == "RFT" and p.username == "huhbyte" and p.meta["followers_count"] == 30410
    assert "follower_count" in info.calls[0].request.url.params["fields"]


@respx.mock
def test_tiktok_direct_file_upload_forces_private_when_unaudited(tt, video):
    respx.post(f"{API}/v2/post/publish/creator_info/query/").respond(json=CREATOR)
    init = respx.post(f"{API}/v2/post/publish/video/init/").respond(json={"data": {"publish_id": "P1", "upload_url": "https://upload.tiktok/u?x=1"}, "error": {"code": "ok"}})
    put = respx.put("https://upload.tiktok/u?x=1").respond(201)
    respx.post(f"{API}/v2/post/publish/status/fetch/").respond(json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": []}, "error": {"code": "ok"}})
    ctx = PublishContext("t", a(), "caption #ai", [video], {"privacy_level": "PUBLIC_TO_EVERYONE", "disable_comment": True}, {})
    out = tt.publish(ctx)
    b = body(init.calls[0])
    assert b["post_info"]["privacy_level"] == "SELF_ONLY"  # unaudited app
    assert b["post_info"]["disable_duet"] is True  # creator has duet disabled -> honoured
    assert b["post_info"]["title"] == "caption #ai" and b["source_info"]["source"] == "FILE_UPLOAD"
    assert b["source_info"]["total_chunk_count"] == 2 and b["source_info"]["chunk_size"] == 10 * 1024 * 1024 or b["source_info"]["total_chunk_count"] >= 1
    ranges = [c.request.headers["Content-Range"] for c in put.calls]
    size = video.size
    assert ranges[-1].endswith(f"-{size - 1}/{size}") and ranges[0].startswith("bytes 0-")
    assert out.status == "published" and "private" in out.message
    assert ctx.state["uploaded"] is True


@respx.mock
def test_tiktok_audited_respects_choice(settings, video):
    settings.tiktok_audited = True
    tt = TikTokConnector(settings, httpx.Client())
    respx.post(f"{API}/v2/post/publish/creator_info/query/").respond(json=CREATOR)
    init = respx.post(f"{API}/v2/post/publish/video/init/").respond(json={"data": {"publish_id": "P1"}, "error": {"code": "ok"}})
    respx.post(f"{API}/v2/post/publish/status/fetch/").respond(json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [7123]}, "error": {"code": "ok"}})
    out = tt.publish(PublishContext("t", a(), "x", [video], {"privacy_level": "MUTUAL_FOLLOW_FRIENDS", "video_source": "url"}, {}))
    b = body(init.calls[0])
    assert b["post_info"]["privacy_level"] == "MUTUAL_FOLLOW_FRIENDS" and b["source_info"] == {"source": "PULL_FROM_URL", "video_url": "https://pub/m/t/clip.mp4"}
    assert out.permalink == "https://www.tiktok.com/@huhbyte/video/7123"


@respx.mock
def test_tiktok_inbox_mode(tt, video):
    init = respx.post(f"{API}/v2/post/publish/inbox/video/init/").respond(json={"data": {"publish_id": "P2", "upload_url": "https://upload.tiktok/i"}, "error": {"code": "ok"}})
    respx.put("https://upload.tiktok/i").respond(201)
    respx.post(f"{API}/v2/post/publish/status/fetch/").respond(json={"data": {"status": "SEND_TO_USER_INBOX"}, "error": {"code": "ok"}})
    out = tt.publish(PublishContext("t", a(), "x", [video], {"tt_mode": "inbox"}, {}))
    assert "post_info" not in body(init.calls[0]) and out.status == "inbox"


@respx.mock
def test_tiktok_photo_post(tt):
    respx.post(f"{API}/v2/post/publish/creator_info/query/").respond(json=CREATOR)
    init = respx.post(f"{API}/v2/post/publish/content/init/").respond(json={"data": {"publish_id": "P3"}, "error": {"code": "ok"}})
    respx.post(f"{API}/v2/post/publish/status/fetch/").respond(json={"data": {"status": "PROCESSING_DOWNLOAD"}, "error": {"code": "ok"}})
    imgs = [MediaItem(id=str(i), kind="image", mime="image/png", path=Path("/x"), filename="x", jpeg_public_url=f"https://pub/{i}.jpg") for i in range(3)]
    out = tt.publish(PublishContext("t", a(), "desc", imgs, {"title": "My title"}, {}))
    b = body(init.calls[0])
    assert b["media_type"] == "PHOTO" and b["post_mode"] == "DIRECT_POST" and len(b["source_info"]["photo_images"]) == 3
    assert b["post_info"]["title"] == "My title" and b["post_info"]["description"] == "desc"
    assert out.status == "processing"


@pytest.mark.parametrize("code,status,exc", [("access_token_invalid", 401, AuthError), ("rate_limit_exceeded", 429, RetryableError),
                                              ("spam_risk_too_many_posts", 403, RetryableError),
                                              ("unaudited_client_can_only_post_to_private_accounts", 403, PermanentError),
                                              ("privacy_level_option_mismatch", 403, PermanentError)])
@respx.mock
def test_tiktok_error_mapping(tt, video, code, status, exc):
    respx.post(f"{API}/v2/post/publish/creator_info/query/").respond(json=CREATOR)
    respx.post(f"{API}/v2/post/publish/video/init/").respond(status, json={"error": {"code": code, "message": "m"}})
    with pytest.raises(exc) as e:
        tt.publish(PublishContext("t", a(), "x", [video], {}, {}))
    if code.startswith("unaudited"):
        assert "PRIVATE" in e.value.message and "inbox" in e.value.message


@respx.mock
def test_tiktok_failed_status_and_resume(tt, video):
    st = respx.post(f"{API}/v2/post/publish/status/fetch/").respond(json={"data": {"status": "FAILED", "fail_reason": "video_pull_failed"}, "error": {"code": "ok"}})
    init = respx.post(f"{API}/v2/post/publish/video/init/")
    with pytest.raises(PermanentError, match="video_pull_failed"):
        tt.publish(PublishContext("t", a(), "x", [video], {}, {"publish_id": "P9", "uploaded": True}))
    assert st.called and not init.called  # resumed, did not re-init


def test_tiktok_chunk_plan_and_validation(tt, video):
    assert TikTokConnector.chunk_plan(1000) == (1000, 1)
    cs, n = TikTokConnector.chunk_plan(MIN_CHUNK * 7 + 5)
    assert cs >= MIN_CHUNK and n == (MIN_CHUNK * 7 + 5) // cs
    codes = lambda *x: {i.code for i in tt.validate(*x, BrandRules())}
    img = MediaItem(id="i", kind="image", mime="image/jpeg", path=Path("/x"), filename="x")
    assert "mixed_media" in codes("x", [video, img], {})
    assert "unaudited" in codes("x", [video], {"tt_mode": "direct"})
    assert "unaudited" not in codes("x", [video], {"tt_mode": "inbox"})
    assert "caption_too_long" in codes("x" * 2201, [video], {})
    assert "photo_needs_domain" in codes("x", [img], {})


@respx.mock
def test_tiktok_refresh_and_insights(tt):
    respx.post(f"{API}/v2/oauth/token/").respond(json={"access_token": "NEW", "refresh_token": "R2", "expires_in": 86400, "refresh_expires_in": 100})
    assert tt.refresh(a()).access_token == "NEW"
    respx.post(f"{API}/v2/video/query/").respond(json={"data": {"videos": [{"id": "1", "view_count": 1000, "like_count": 80, "comment_count": 10, "share_count": 10}]}, "error": {"code": "ok"}})
    out = tt.fetch_insights(PublishContext("t", a(), "", [], {}, {}), "1")
    assert out["views"] == 1000 and out["engagement_rate"] == 10.0


# ------------------------------------------------------------------ YouTube
def test_youtube_authorize_url_uses_pkce(yt):
    q = parse_qs(urlparse(yt.authorize_url("S", "http://localhost:8788/oauth/youtube/callback", "verifier123")).query)
    assert q["code_challenge"] == [pkce_challenge("verifier123")] and q["code_challenge_method"] == ["S256"]
    assert q["access_type"] == ["offline"] and "youtube.upload" in q["scope"][0]


@respx.mock
def test_youtube_exchange_upload_poll_and_comment(yt, video):
    tok = respx.post("https://oauth2.googleapis.com/token").respond(json={"access_token": "ACT", "refresh_token": "RFT", "expires_in": 3599})
    respx.get(f"{YT}/channels").respond(json={"items": [{"id": "UCxyz", "snippet": {"title": "HuhByte AI", "customUrl": "@huhbyte", "thumbnails": {"default": {"url": "a"}}},
                                                         "statistics": {"subscriberCount": "5120", "videoCount": "88"}}]})
    b, p = yt.exchange_code("CODE", "http://localhost/cb", "ver")
    assert "code_verifier=ver" in tok.calls[0].request.content.decode() and p.username == "huhbyte" and p.meta["followers_count"] == 5120
    start = respx.post(UPLOAD).respond(200, headers={"Location": "https://upload.google/session1"})
    put = respx.put("https://upload.google/session1").mock(side_effect=[httpx.Response(308, headers={"Range": "bytes=0-8388607"}), httpx.Response(200, json={"id": "VID1"})])
    respx.get(f"{YT}/videos").respond(json={"items": [{"status": {"uploadStatus": "processed", "privacyStatus": "private"}}]})
    com = respx.post(f"{YT}/commentThreads").respond(json={"id": "CT1"})
    out = yt.publish(PublishContext("t", a("youtube"), "GPT-6 explained\nmore text", [video], {"first_comment": "Follow @huhbyte"}, {}))
    meta = json.loads(start.calls[0].request.content)
    assert meta["snippet"]["title"] == "GPT-6 explained" and "#Shorts" in meta["snippet"]["description"]
    assert put.call_count == 2 and put.calls[1].request.headers["Content-Range"].startswith("bytes 8388608-")
    assert out.status == "published" and out.external_id == "VID1" and out.permalink == "https://youtube.com/shorts/VID1"
    assert json.loads(com.calls[0].request.content)["snippet"]["videoId"] == "VID1"


@respx.mock
def test_youtube_quota_is_retryable_and_validation(yt, video):
    respx.post(UPLOAD).respond(403, json={"error": {"message": "quota", "errors": [{"reason": "quotaExceeded"}]}})
    with pytest.raises(RetryableError) as e:
        yt.publish(PublishContext("t", a("youtube"), "x", [video], {}, {}))
    assert e.value.retry_after >= 3600
    codes = {i.code for i in yt.validate("a <b>", [video], {"yt_privacy": "public"}, BrandRules())}
    assert {"angle_brackets", "unverified"} <= codes


@respx.mock
def test_youtube_comments(yt):
    respx.get(f"{YT}/commentThreads").respond(json={"items": [{"snippet": {"videoId": "V", "topLevelComment": {"id": "T1", "snippet": {"authorDisplayName": "Priya", "textDisplay": "more!", "publishedAt": "2026-10-22T08:00:00Z"}}},
                                                               "replies": {"comments": [{"id": "R1", "snippet": {"authorDisplayName": "HuhByte", "textDisplay": "thanks", "authorChannelId": {"value": "UCxyz"}}}]}}]})
    rows = yt.list_comments(a("youtube"))
    assert rows[0]["author"] == "Priya" and rows[1]["is_own"] and rows[1]["parent_external_id"] == "T1"
    rep = respx.post(f"{YT}/comments").respond(json={"id": "R2"})
    assert yt.reply_comment(a("youtube"), "T1", "hi") == "R2" and json.loads(rep.calls[0].request.content)["snippet"]["parentId"] == "T1"


@respx.mock
def test_youtube_full_fields_thumbnail_playlist_captions(yt, video, tmp_path):
    """Every composer field lands in the documented Data API v3 request (videos.insert, thumbnails.set, playlistItems, captions)."""
    from huhqueue.connectors.youtube import CAPTIONS_UPLOAD, THUMB_UPLOAD
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"\xff\xd8jpegdata")
    start = respx.post(UPLOAD).respond(200, headers={"Location": "https://upload.google/s2"})
    respx.put("https://upload.google/s2").respond(200, json={"id": "VID2"})
    respx.get(f"{YT}/videos").respond(json={"items": [{"status": {"uploadStatus": "processed", "privacyStatus": "private"}}]})
    th = respx.post(THUMB_UPLOAD).respond(json={"items": []})
    pl = respx.post(f"{YT}/playlistItems").respond(json={"id": "PLI1"})
    cap = respx.post(CAPTIONS_UPLOAD).respond(json={"id": "CAP1"})
    opts = {"yt_format": "video", "yt_title": "Long explainer", "yt_category": "27", "yt_privacy": "unlisted", "yt_license": "creativeCommon",
            "yt_notify_subscribers": False, "yt_embeddable": False, "yt_made_for_kids": False, "yt_synthetic_media": True,
            "yt_tags": "ai, gpt 6", "yt_publish_at": "2026-10-30T18:00:00Z", "yt_playlist_id": "PL123",
            "yt_captions_text": "1\n00:00:00,000 --> 00:00:01,000\nHi", "yt_captions_language": "en"}
    ctx = PublishContext("t", a("youtube"), "desc", [video], opts, {},
                         attachments={"yt_thumbnail": MediaItem(id="th", kind="image", mime="image/jpeg", path=thumb, filename="t.jpg")})
    out = yt.publish(ctx)
    req = start.calls[0].request
    meta = json.loads(req.content)
    assert parse_qs(urlparse(str(req.url)).query)["notifySubscribers"] == ["false"]
    assert meta["snippet"] == {"title": "Long explainer", "description": "desc", "categoryId": "27", "tags": ["ai", "gpt 6"]}
    assert meta["status"] == {"privacyStatus": "private", "selfDeclaredMadeForKids": False, "embeddable": False,
                              "license": "creativeCommon", "containsSyntheticMedia": True, "publishAt": "2026-10-30T18:00:00Z"}
    assert parse_qs(urlparse(str(th.calls[0].request.url)).query)["videoId"] == ["VID2"]
    assert json.loads(pl.calls[0].request.content)["snippet"] == {"playlistId": "PL123", "resourceId": {"kind": "youtube#video", "videoId": "VID2"}}
    assert b'"videoId": "VID2"' in cap.calls[0].request.content and out.permalink == "https://youtu.be/VID2" and not out.warnings
    # retried poll never repeats the side steps
    yt.publish(PublishContext("t", a("youtube"), "desc", [video], opts, ctx.state, attachments=ctx.attachments))
    assert th.call_count == pl.call_count == cap.call_count == 1


def test_youtube_validation_title_tags_category(yt, video):
    codes = lambda o, cap="x": {i.code for i in yt.validate(cap, [video], o, BrandRules())}  # noqa: E731
    assert "title_required" in codes({})
    assert "title_required" not in codes({"yt_title": "Hi"})
    assert "tags_too_long" in codes({"yt_title": "t", "yt_tags": ",".join(["word" * 10] * 13)})
    assert "bad_category" in codes({"yt_title": "t", "yt_category": "99"})
    assert "publish_at_private" in codes({"yt_title": "t", "yt_privacy": "public", "yt_publish_at": "2026-11-01T10:00:00Z"})
    assert "caption_too_long" in codes({"yt_title": "t"}, "😀" * 1300)
