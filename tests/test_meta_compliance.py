"""Meta deauthorize / data-deletion callbacks, the Instagram 50-per-24h publish cap, and PNG/WebP -> JPEG."""
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from huhqueue.connectors import ChannelAuth, MediaItem, PublishContext, RetryableError
from huhqueue.connectors.instagram import GRAPH, InstagramConnector
from huhqueue.models import Channel, Media, PostTarget
from huhqueue.services import media as media_svc
from huhqueue.services.meta_compliance import make_signed_request, parse_signed_request, SignedRequestError
from huhqueue.worker import Worker

from .conftest import make_image

G = f"{GRAPH}/v26.0"
ME = {"user_id": "1789", "id": "as-55", "username": "huhbyte.ai", "name": "Huh Byte", "account_type": "BUSINESS",
      "followers_count": 12, "media_count": 3}


def connect_ig(api):
    with respx.mock:
        respx.get(f"{G}/me").respond(json=ME)
        r = api.post("/api/connect/instagram/token", {"access_token": "IGTOKEN"})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ signed_request
def test_signed_request_roundtrip_and_tamper():
    sr = make_signed_request({"algorithm": "HMAC-SHA256", "user_id": "1789", "issued_at": 1}, "ig-secret")
    assert parse_signed_request(sr, "ig-secret")["user_id"] == "1789"
    with pytest.raises(SignedRequestError):
        parse_signed_request(sr, "other-secret")
    sig, body = sr.split(".")
    with pytest.raises(SignedRequestError):
        parse_signed_request(sig + "." + body[:-2] + "AA", "ig-secret")
    with pytest.raises(SignedRequestError):
        parse_signed_request("garbage", "ig-secret")
    with pytest.raises(SignedRequestError):
        parse_signed_request(sr, "")


def test_deauthorize_wipes_tokens_but_keeps_channel(api, core, edge):
    ch = connect_ig(api)
    bad = edge.post("/oauth/instagram/deauthorize", data={"signed_request": make_signed_request({"user_id": "1789"}, "nope")})
    assert bad.status_code == 400
    with core.db.session() as s:
        assert s.get(Channel, ch["id"]).access_token_enc  # untouched by a forged request
    r = edge.post("/oauth/instagram/deauthorize",
                  data={"signed_request": make_signed_request({"algorithm": "HMAC-SHA256", "user_id": "1789"}, "ig-secret")})
    assert r.status_code == 200 and r.json()["channels"] == 1
    with core.db.session() as s:
        row = s.get(Channel, ch["id"])
        assert row.status == "disconnected" and row.access_token_enc is None and row.refresh_token_enc is None


def test_data_deletion_removes_channel_and_has_status_page(api, core, edge):
    ch = connect_ig(api)
    # matches on the app-scoped id too
    r = edge.post("/oauth/instagram/data-deletion",
                  data={"signed_request": make_signed_request({"algorithm": "HMAC-SHA256", "user_id": "as-55"}, "ig-secret")})
    assert r.status_code == 200, r.text
    body = r.json()
    code = body["confirmation_code"]
    assert set(body) == {"url", "confirmation_code"} and body["url"].endswith(f"/data-deletion/{code}")
    with core.db.session() as s:
        assert s.get(Channel, ch["id"]) is None
    page = edge.get(f"/data-deletion/{code}")
    assert page.status_code == 200 and code in page.text and "complete" in page.text
    assert edge.get("/data-deletion/doesnotexist").status_code == 404
    # json body works as well, and a forged one is refused
    assert edge.post("/oauth/instagram/data-deletion", json={"signed_request": "x.y"}).status_code == 400


def test_app_keys_show_compliance_urls(api):
    ig = api.get("/api/app-keys").json()["instagram"]
    assert ig["deauthorize_url"].endswith("/oauth/instagram/deauthorize")
    assert ig["data_deletion_url"].endswith("/oauth/instagram/data-deletion")


# ------------------------------------------------------------------ publish cap
def _ctx(recent=None):
    a = ChannelAuth(id="ch1", external_id="1789", username="huhbyte.ai", access_token="IGTOKEN")
    m = MediaItem(id="m1", kind="image", mime="image/jpeg", path=Path("/x.jpg"), filename="a.jpg", size=1,
                  width=1080, height=1080, public_url="https://pub/a.jpg", jpeg_public_url="https://pub/a.jpg")
    return PublishContext("t1", a, "hi", [m], {}, {}, recent_publishes=recent)


@respx.mock
def test_publish_cap_uses_content_publishing_limit(settings):
    ig = InstagramConnector(settings, httpx.Client())
    respx.get(f"{G}/1789/content_publishing_limit").respond(
        json={"data": [{"quota_usage": 50, "config": {"quota_total": 100}}]})
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    with pytest.raises(RetryableError) as e:
        ig.publish(_ctx(recent=3))
    assert e.value.code == "publish_cap" and "50/50" in e.value.message
    assert not create.called  # nothing created over the cap


@respx.mock
def test_publish_cap_falls_back_to_local_count(settings):
    ig = InstagramConnector(settings, httpx.Client())
    respx.get(f"{G}/1789/content_publishing_limit").respond(status_code=400, json={"error": {"message": "unsupported", "code": 100}})
    respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    with pytest.raises(RetryableError) as e:
        ig.publish(_ctx(recent=50))
    assert e.value.code == "publish_cap" and "last 24h" in e.value.message


@respx.mock
def test_publish_under_cap_goes_ahead(settings):
    ig = InstagramConnector(settings, httpx.Client())
    respx.get(f"{G}/1789/content_publishing_limit").respond(
        json={"data": [{"quota_usage": 49, "config": {"quota_total": 100}}]})
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    respx.get(f"{G}/C1").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "IG1"})
    respx.get(f"{G}/IG1").respond(json={"id": "IG1", "permalink": "https://www.instagram.com/p/IG1/"})
    out = ig.publish(_ctx(recent=10))
    assert create.called and out.status in ("published", "processing")


def test_worker_counts_rolling_24h_sends(api, core, channel, image_file):
    m = media_upload(api, image_file)
    r = api.post("/api/posts", {"caption": "x", "media_ids": [m["id"]], "channel_ids": [channel["id"]], "when": "draft"})
    tid = r.json()["targets"][0]["id"]
    now = datetime.utcnow()
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        for i, age in enumerate((1, 5, 23, 25, 48)):  # 3 inside the window
            s.add(PostTarget(post_id=t.post_id, channel_id=channel["id"], status="sent", caption_override=f"s{i}",
                             published_at=now - timedelta(hours=age)))
    with core.db.session() as s:
        assert Worker(core).recent_publishes(s, channel["id"], tid) == 3


# ------------------------------------------------------------------ JPEG conversion
def media_upload(api, path):
    with open(path, "rb") as fh:
        r = api.post("/api/media", files={"files": (path.name, fh)})
    assert r.status_code == 200, r.text
    return r.json()["media"][0]


@pytest.mark.parametrize("fmt,ext", [("PNG", "png"), ("WEBP", "webp")])
def test_non_jpeg_converted_before_instagram_publish(api, core, tmp_path, fmt, ext):
    src = tmp_path / f"card.{ext}"
    Image.new("RGBA", (400, 500), (201, 172, 250, 128)).save(src, fmt)
    m = media_upload(api, src)
    with core.db.session() as s:  # simulate an older upload whose JPEG copy is gone
        row = s.get(Media, m["id"])
        (core.settings.media_dir / row.jpeg_name).unlink()
        row.jpeg_name = None
    ch = connect_ig(api)
    r = api.post("/api/posts", {"caption": "hello", "media_ids": [m["id"]], "channel_ids": [ch["id"]], "when": "draft"})
    tid = r.json()["targets"][0]["id"]
    with core.db.session() as s:
        ctx = Worker(core).build_context(s, s.get(PostTarget, tid))
        row = s.get(Media, m["id"])
        assert row.jpeg_name and row.jpeg_name.endswith(".jpg")
        path = core.settings.media_dir / row.jpeg_name
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.mode == "RGB" and im.size == (400, 500)
    assert ctx.media[0].jpeg_path == path


def test_ensure_jpeg_leaves_real_jpegs_alone(api, core, tmp_path):
    m = media_upload(api, make_image(tmp_path / "a.jpg", fmt="JPEG"))
    with core.db.session() as s:
        row = s.get(Media, m["id"])
        assert media_svc.ensure_jpeg(core, row) is False and row.jpeg_name is None


# ------------------------------------------------------------------ agent docs
def test_llms_txt_and_agents_page_are_public(app):
    from fastapi.testclient import TestClient
    c = TestClient(app)  # no login
    t = c.get("/llms.txt")
    assert t.status_code == 200 and t.text.startswith("# HuhQueue") and "/api/openapi.json" in t.text and "create_post" in t.text
    a = c.get("/agents")
    assert a.status_code == 200 and "/api/openapi.json" in a.text and "/api/docs" in a.text and "huhqueue mcp" in a.text
    assert c.get("/api/openapi.json").status_code == 200


def test_home_scorecard_fields(api, core, channel):
    d = api.get("/api/home").json()
    assert {"week_streak", "weekly_sent", "comment_score", "recent_comments"} <= set(d)
    assert len(d["weekly_sent"]) == 8
