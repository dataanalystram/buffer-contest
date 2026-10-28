"""LinkedIn connector against a mocked api.linkedin.com (respx). Nothing here talks to the real LinkedIn."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
import respx

from huhqueue.connectors import (AuthError, BrandRules, ChannelAuth, MediaItem, PermanentError, Profile,
                                 PublishContext, RetryableError, TokenBundle)
from huhqueue.connectors.linkedin import (AUTH_URL, REST, TOKEN_URL, USERINFO_URL, LinkedInConnector,
                                          LinkedInPagesConnector, escape_commentary, first_url)
from huhqueue.models import Channel, Event
from huhqueue.services import oauth
from huhqueue.worker import Worker

URN = "urn:li:share:7001"


@pytest.fixture
def li(settings):
    return LinkedInConnector(settings, httpx.Client())


def auth(**kw):
    meta = kw.pop("meta", {"author_urn": "urn:li:person:abc"})
    return ChannelAuth(id="c", external_id="abc", username="ram", access_token="LITOK", meta=meta, **kw)


def media(tmp_path, name, kind, mime, data=b"x" * 100, **kw):
    p = Path(tmp_path) / name
    p.write_bytes(data)
    return MediaItem(id=name, kind=kind, mime=mime, path=p, filename=name, size=len(data), **kw)


def ctx(caption="", items=(), options=None, state=None, a=None):
    return PublishContext(target_id="t", channel=a or auth(), caption=caption, media=list(items),
                          options=options or {}, state=state if state is not None else {})


def codes(issues):
    return {i.code for i in issues if i.level == "error"}


# ------------------------------------------------------------------ auth
def test_authorize_url_scopes_and_state(li):
    u = urlparse(li.authorize_url("STATE", "https://hq/oauth/linkedin/callback"))
    q = parse_qs(u.query)
    assert f"{u.scheme}://{u.netloc}{u.path}" == AUTH_URL
    assert q["client_id"] == ["li-id"] and q["state"] == ["STATE"] and q["response_type"] == ["code"]
    assert set(q["scope"][0].split(" ")) == {"openid", "profile", "email", "w_member_social"}


def test_not_configured_without_keys(li, settings):
    settings.linkedin_client_secret = ""
    assert not li.is_configured()
    with pytest.raises(Exception):
        li.authorize_url("S", "https://x/cb")


@respx.mock
def test_exchange_reads_userinfo_and_60_day_expiry(li):
    tok = respx.post(TOKEN_URL).respond(json={"access_token": "AT", "expires_in": 5184000, "scope": "openid,profile,email,w_member_social"})
    respx.get(USERINFO_URL).respond(json={"sub": "abc", "name": "Ram S", "email": "r@example.com", "picture": "https://p/x.jpg"})
    bundle, prof = li.exchange_code("CODE", "https://hq/oauth/linkedin/callback")
    form = {k: v[0] for k, v in parse_qs(tok.calls[0].request.content.decode()).items()}
    assert form["grant_type"] == "authorization_code" and form["client_secret"] == "li-secret"
    assert bundle.refresh_token is None and bundle.expires_at > datetime.utcnow() + timedelta(days=59)
    assert prof.external_id == "abc" and prof.meta["author_urn"] == "urn:li:person:abc"
    assert prof.display_name == "Ram S" and "w_member_social" in bundle.scopes


@respx.mock
def test_api_version_header_from_config(li, settings):
    settings.linkedin_api_version = "202612"
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    li.publish(ctx("hello"))
    h = route.calls[0].request.headers
    assert h["linkedin-version"] == "202612" and h["x-restli-protocol-version"] == "2.0.0"


# ------------------------------------------------------------------ text helpers
def test_escape_keeps_mentions_and_hashtags():
    s = "Big (news) #AI @[Ram S](urn:li:person:abc) a_b *x* [y] <z> ~ | {q}"
    out = escape_commentary(s)
    assert "#AI" in out and "@[Ram S](urn:li:person:abc)" in out
    assert r"\(news\)" in out and r"a\_b" in out and r"\*x\*" in out and r"\[y\]" in out and r"\<z\>" in out
    assert r"\~" in out and r"\|" in out and r"\{q\}" in out
    assert first_url("read https://ex.com/a?b=1. now") == "https://ex.com/a?b=1"


# ------------------------------------------------------------------ validation
def test_validate_limits_and_mixing(li, tmp_path):
    r = BrandRules()
    assert "caption_too_long" in codes(li.validate("x" * 3001, [], {}, r))
    img = lambda i: media(tmp_path, f"i{i}.jpg", "image", "image/jpeg")  # noqa: E731
    vid = media(tmp_path, "v.mp4", "video", "video/mp4", duration=20)
    pdf = media(tmp_path, "deck.pdf", "document", "application/pdf")
    assert "li_mix" in codes(li.validate("x", [img(1), vid], {}, r))
    assert "li_mix" in codes(li.validate("x", [pdf, vid], {}, r))
    assert "li_max_images" in codes(li.validate("x", [img(i) for i in range(21)], {}, r))
    assert "li_format" in codes(li.validate("x", [img(1)], {"li_format": "multi_image"}, r))
    assert "li_format" in codes(li.validate("x", [], {"li_format": "article"}, r))
    assert not codes(li.validate("x https://ex.com", [], {"li_format": "article"}, r))
    assert not codes(li.validate("x", [pdf], {}, r))
    short = media(tmp_path, "s.mp4", "video", "video/mp4", duration=1)
    assert "video_length" in codes(li.validate("x", [short], {}, r))
    assert "first_comment_too_long" in codes(li.validate("x", [], {"first_comment": "y" * 1300}, r))
    assert li.resolve_format("x", [img(1), img(2)], {}) == "multi_image"
    assert li.resolve_format("see https://ex.com", [], {}) == "article"
    assert li.resolve_format("see https://ex.com", [], {"link_preview": False}) == "text"


def test_pages_only_public_and_behind_config(settings):
    pg = LinkedInPagesConnector(settings, httpx.Client())
    settings.linkedin_pages_client_id, settings.linkedin_pages_client_secret = "p-id", "p-secret"
    assert not pg.is_configured()           # LINKEDIN_PAGES_ENABLED is off
    settings.linkedin_pages_enabled = True
    assert pg.is_configured()
    assert "li_visibility" in codes(pg.validate("x", [], {"visibility": "CONNECTIONS"}, BrandRules()))


# ------------------------------------------------------------------ publishing
def _init(kind, urn, url="https://upload.example/{}"):
    key = {"images": "image", "documents": "document"}[kind]
    return respx.post(f"{REST}/{kind}?action=initializeUpload").respond(
        json={"value": {"uploadUrl": url.format(urn), key: urn}})


@respx.mock
def test_text_post_body(li):
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    out = li.publish(ctx("Hello (world)", options={"visibility": "CONNECTIONS"}))
    body = json.loads(route.calls[0].request.content)
    assert body["author"] == "urn:li:person:abc" and body["commentary"] == r"Hello \(world\)"
    assert body["visibility"] == "CONNECTIONS" and body["lifecycleState"] == "PUBLISHED"
    assert body["distribution"]["feedDistribution"] == "MAIN_FEED" and "content" not in body
    assert out.status == "published" and out.external_id == URN
    assert out.permalink == f"https://www.linkedin.com/feed/update/{URN}/"


@respx.mock
def test_single_and_multi_image_with_alt_text(li, tmp_path):
    init = respx.post(f"{REST}/images?action=initializeUpload")
    init.side_effect = [httpx.Response(200, json={"value": {"uploadUrl": f"https://up/{i}", "image": f"urn:li:image:{i}"}}) for i in range(3)]
    puts = respx.put(url__startswith="https://up/").respond(201)
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    items = [media(tmp_path, "a.jpg", "image", "image/jpeg", alt_text="A cat"), media(tmp_path, "b.jpg", "image", "image/jpeg")]
    li.publish(ctx("two", items, {"alt_text": "fallback"}))
    body = json.loads(route.calls[0].request.content)
    assert body["content"]["multiImage"]["images"] == [{"id": "urn:li:image:0", "altText": "A cat"},
                                                         {"id": "urn:li:image:1", "altText": "fallback"}]
    assert puts.call_count == 2 and puts.calls[0].request.headers["authorization"] == "Bearer LITOK"
    li.publish(ctx("one", items[:1]))
    body = json.loads(route.calls[1].request.content)
    assert body["content"] == {"media": {"id": "urn:li:image:2", "altText": "A cat"}}


@respx.mock
def test_document_carousel(li, tmp_path):
    _init("documents", "urn:li:document:9")
    put = respx.put("https://upload.example/urn:li:document:9").respond(201)
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    li.publish(ctx("deck", [media(tmp_path, "deck.pdf", "document", "application/pdf", b"%PDF-1.4")],
                   {"document_title": "Q4 deck"}))
    assert put.calls[0].request.headers["content-type"] == "application/pdf"
    assert json.loads(route.calls[0].request.content)["content"] == {"media": {"id": "urn:li:document:9", "title": "Q4 deck"}}


@respx.mock
def test_video_byte_ranges_finalize_and_processing(li, tmp_path):
    data = bytes(range(256)) * 4   # 1024 bytes
    respx.post(f"{REST}/videos?action=initializeUpload").respond(json={"value": {
        "video": "urn:li:video:5", "uploadToken": "UT",
        "uploadInstructions": [{"uploadUrl": "https://vup/1", "firstByte": 0, "lastByte": 599},
                               {"uploadUrl": "https://vup/2", "firstByte": 600, "lastByte": 1023}]}})
    p1 = respx.put("https://vup/1").respond(200, headers={"etag": '"e1"'})
    p2 = respx.put("https://vup/2").respond(200, headers={"etag": "e2"})
    fin = respx.post(f"{REST}/videos?action=finalizeUpload").respond(200)
    status = respx.get(f"{REST}/videos/{quote('urn:li:video:5', safe='')}")
    status.side_effect = [httpx.Response(200, json={"status": "PROCESSING"}), httpx.Response(200, json={"status": "AVAILABLE"})]
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    c = ctx("vid", [media(tmp_path, "v.mp4", "video", "video/mp4", data, duration=10)])
    out = li.publish(c)
    assert out.status == "processing" and not route.called
    assert p1.calls[0].request.content == data[:600] and p2.calls[0].request.content == data[600:]
    assert json.loads(fin.calls[0].request.content)["finalizeUploadRequest"] == {
        "video": "urn:li:video:5", "uploadToken": "UT", "uploadedPartIds": ["e1", "e2"]}
    out = li.publish(c)   # worker resumes via publish/poll: no second upload
    assert out.status == "published" and p1.call_count == 1
    assert json.loads(route.calls[0].request.content)["content"]["media"]["id"] == "urn:li:video:5"


@respx.mock
def test_video_processing_failed_is_permanent(li, tmp_path):
    st = {"uploaded": True, "format": "video", "video": "urn:li:video:5"}
    respx.get(f"{REST}/videos/{quote('urn:li:video:5', safe='')}").respond(json={"status": "PROCESSING_FAILED"})
    with pytest.raises(PermanentError):
        li.poll(ctx("v", state=st))


@respx.mock
def test_link_preview_reads_og_tags_and_uploads_thumbnail(li):
    respx.get("https://ex.com/post").respond(200, html='<html><head><meta property="og:title" content="Big &amp; News">'
                                             '<meta property="og:description" content="Desc"><meta property="og:image" content="/img.jpg"></head></html>')
    respx.get("https://ex.com/img.jpg").respond(200, content=b"jpg", headers={"content-type": "image/jpeg"})
    _init("images", "urn:li:image:7")
    respx.put("https://upload.example/urn:li:image:7").respond(201)
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    li.publish(ctx("read this https://ex.com/post"))
    art = json.loads(route.calls[0].request.content)["content"]["article"]
    assert art == {"source": "https://ex.com/post", "title": "Big & News", "description": "Desc", "thumbnail": "urn:li:image:7"}


@respx.mock
def test_first_comment_once_even_on_retry(li):
    respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    com = respx.post(f"{REST}/socialActions/{quote(URN, safe='')}/comments").respond(201, headers={"x-restli-id": "urn:li:comment:1"})
    c = ctx("hi", options={"first_comment": "Link: https://ex.com"})
    li.publish(c)
    body = json.loads(com.calls[0].request.content)
    assert body == {"actor": "urn:li:person:abc", "object": URN, "message": {"text": "Link: https://ex.com"}}
    li.publish(c)   # retry with saved state
    assert com.call_count == 1


@respx.mock
def test_first_comment_failure_is_a_warning(li):
    respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    respx.post(f"{REST}/socialActions/{quote(URN, safe='')}/comments").respond(403, json={"message": "nope"})
    out = li.publish(ctx("hi", options={"first_comment": "c"}))
    assert out.status == "published" and "first comment failed" in out.warnings[0]


@respx.mock
def test_error_mapping(li):
    route = respx.post(f"{REST}/posts")
    route.respond(401, json={"message": "expired", "serviceErrorCode": 65601})
    with pytest.raises(AuthError):
        li.publish(ctx("a"))
    route.respond(429, headers={"retry-after": "42"}, json={"message": "slow down"})
    with pytest.raises(RetryableError) as e:
        li.publish(ctx("a"))
    assert e.value.retry_after == 42
    route.respond(503, json={"message": "down"})
    with pytest.raises(RetryableError):
        li.publish(ctx("a"))
    route.respond(422, json={"message": "bad"})
    with pytest.raises(PermanentError):
        li.publish(ctx("a"))


# ------------------------------------------------------------------ company pages
@respx.mock
def test_pages_login_creates_one_channel_per_admin_page(core, settings):
    settings.linkedin_pages_enabled = True
    settings.linkedin_pages_client_id, settings.linkedin_pages_client_secret = "p-id", "p-secret"
    respx.post(TOKEN_URL).respond(json={"access_token": "PT", "expires_in": 5184000})
    respx.get(f"{REST}/organizationAcls").respond(json={"elements": [
        {"organization": "urn:li:organization:11", "role": "ADMINISTRATOR", "state": "APPROVED"},
        {"organization": "urn:li:organization:22", "role": "CONTENT_ADMINISTRATOR", "state": "APPROVED"},
        {"organization": "urn:li:organization:33", "role": "ANALYST", "state": "APPROVED"}]})
    respx.get(f"{REST}/organizations/11").respond(json={"localizedName": "HuhByte", "vanityName": "huhbyte"})
    respx.get(f"{REST}/organizations/22").respond(json={"localizedName": "Other Co"})
    start = oauth.start(core, "linkedin_page")
    assert "rw_organization_admin" in parse_qs(urlparse(start["url"]).query)["scope"][0]
    oauth.complete(core, "linkedin_page", "CODE", start["state"])
    with core.db.session() as s:
        chans = s.query(Channel).filter_by(provider="linkedin_page").order_by(Channel.external_id).all()
        assert [c.external_id for c in chans] == ["11", "22"]
        assert chans[0].meta["author_urn"] == "urn:li:organization:11" and "_pages" not in chans[0].meta
    a = ChannelAuth(id="x", external_id="11", username="huhbyte", access_token="PT", meta={"author_urn": "urn:li:organization:11"})
    route = respx.post(f"{REST}/posts").respond(201, headers={"x-restli-id": URN})
    LinkedInPagesConnector(settings, httpx.Client()).publish(ctx("page post", a=a))
    assert json.loads(route.calls[0].request.content)["author"] == "urn:li:organization:11"


# ------------------------------------------------------------------ token expiry
def _channel(core, expires_in):
    b = TokenBundle(access_token="AT", expires_at=datetime.utcnow() + expires_in)
    return oauth.upsert_channel(core, "linkedin", b, Profile(external_id="abc", username="ram",
                                                             meta={"author_urn": "urn:li:person:abc"}))


def test_worker_warns_7_days_before_expiry_once_then_flags(core):
    cid = _channel(core, timedelta(days=6))
    w = Worker(core)
    now = datetime.utcnow()
    assert w.warn_expiring(now) == 1
    assert w.warn_expiring(now) == 0          # one warning per token
    with core.db.session() as s:
        ev = s.query(Event).filter_by(kind="token_expiring", channel_id=cid).one()
        assert "expires in 6 days" in ev.message and ev.notify
    assert w.warn_expiring(now + timedelta(days=7)) == 1
    with core.db.session() as s:
        assert s.get(Channel, cid).status == "needs_reconnect"


def test_no_warning_far_from_expiry(core):
    _channel(core, timedelta(days=30))
    assert Worker(core).warn_expiring(datetime.utcnow()) == 0


def test_provider_listed_with_formats(api):
    d = api.get("/api/providers").json()
    provs = {p["id"]: p for p in d["connectors"]}
    assert "linkedin" not in {p["id"] for p in d["planned"]}
    assert d["redirect_uris"]["linkedin"].endswith("/oauth/linkedin/callback")
    li = provs["linkedin"]
    assert li["configured"] and li["format_key"] == "li_format"
    assert {"document", "multi_image", "video", "article"} <= {f[0] for f in li["formats"]}
    assert not provs["linkedin_page"]["configured"]


# ------------------------------------------------------------------ CSV bulk upload (Buffer parity)
def _li_channel(core):
    return oauth.upsert_channel(core, "linkedin", TokenBundle(access_token="AT", expires_at=datetime.utcnow() + timedelta(days=50)),
                                Profile(external_id="abc", username="ram", meta={"author_urn": "urn:li:person:abc"}))


def test_buffer_csv_imports_unchanged_with_posting_time(api, core):
    cid = _li_channel(core)
    csv_text = "Text,Image URL,Tags,Posting Time\n\"Hello LinkedIn\",,news,2030-01-02 09:30\n\"Next slot\",,,\n"
    pre = api.post("/api/import/csv/preview", {"csv": csv_text, "channel_ids": [cid], "when": "queue",
                                               "timezone": "Europe/Dublin"}).json()
    assert pre["valid"] == 2 and pre["unknown_columns"] == []
    r1, r2 = pre["rows"]
    assert r1["when"] == "custom" and r1["scheduled_at"].startswith("2030-01-02T09:30") and r1["tags"] == ["news"]
    assert r2["when"] == "queue"
    drafts = api.post("/api/import/csv/preview", {"csv": csv_text, "channel_ids": [cid], "when": "draft"}).json()
    assert {r["when"] for r in drafts["rows"]} == {"draft"}


def test_csv_linkedin_columns_and_formats(api, core):
    cid = _li_channel(core)
    csv_text = ("Text,Format,Document URL,Document Title,Link,Alt Text,First Comment,Privacy\n"
                "\"Deck\",pdf,https://ex.com/deck.pdf,Q4,,,Sources below,connections\n"
                "\"Read\",link,,,https://ex.com/a,,,\n")
    pre = api.post("/api/import/csv/preview", {"csv": csv_text, "channel_ids": [cid]}).json()
    assert pre["valid"] == 2, pre
    o1, o2 = pre["rows"][0]["options"][cid], pre["rows"][1]["options"][cid]
    assert o1 == {"li_format": "document", "document_title": "Q4", "visibility": "CONNECTIONS", "first_comment": "Sources below"}
    assert pre["rows"][0]["media"] == [{"url": "https://ex.com/deck.pdf", "kind": "document"}]
    assert o2 == {"li_format": "article", "link": "https://ex.com/a"}


def test_csv_pdf_rejected_for_other_networks(api, core, channel):
    pre = api.post("/api/import/csv/preview", {"csv": "Text,Document URL\nx,https://ex.com/d.pdf\n",
                                               "channel_ids": [channel["id"]]}).json()
    assert pre["invalid"] == 1 and "only post to LinkedIn" in " ".join(pre["rows"][0]["errors"])


def test_csv_over_limit_rows_flagged_not_dropped(api, core):
    cid = _li_channel(core)
    rows = "\n".join(f"post {i}," for i in range(503))
    pre = api.post("/api/import/csv/preview", {"csv": "Text,Posting Time\n" + rows + "\n", "channel_ids": [cid]}).json()
    assert pre["total"] == 503 and pre["over_limit"] == 3 and pre["valid"] == 500
    assert pre["rows"][-1]["over_limit"] and "500-row limit" in pre["rows"][-1]["errors"][0]


def test_csv_templates(api):
    b = api.get("/api/import/csv/template?style=buffer")
    assert b.text.splitlines()[0] == "Text,Image URL,Tags,Posting Time" and "buffer-template" in b.headers["content-disposition"]
    assert "Document URL" in api.get("/api/import/csv/template?style=linkedin").text
    assert api.get("/api/import/csv/template").text.startswith("caption,channels")
