"""Channel pages, publishing-pro and engagement extras: Instagram collaborators (validation + Graph API pass-through,
mocked), mentions sync (mocked Graph `tags` edge), bulk resolve / bulk reply with a saved reply, AI reply suggestions
(fake LLM provider + rule fallback), turn a comment into a post, and the Instagram planned grid."""
import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
import respx

from huhqueue.connectors import BrandRules, ChannelAuth, MediaItem, PublishContext
from huhqueue.connectors.fake import FakeConnector
from huhqueue.connectors.instagram import GRAPH, InstagramConnector
from huhqueue.models import Comment
from huhqueue.services import assistant

G = f"{GRAPH}/v26.0"


def form(req):
    return {k: v[0] for k, v in parse_qs(req.content.decode()).items()}


def auth(mode="fb_login"):
    return ChannelAuth(id="ch1", external_id="1789", username="huhbyte.ai", access_token="IGTOKEN", meta={"auth_mode": mode})


def img(i=1):
    return MediaItem(id=f"m{i}", kind="image", mime="image/jpeg", path=Path("/x.jpg"), filename=f"a{i}.jpg", size=1000,
                     width=1080, height=1350, public_url=f"https://pub.example/a{i}", jpeg_public_url=f"https://pub.example/a{i}.jpg")


def vid(i=1):
    return MediaItem(id=f"v{i}", kind="video", mime="video/mp4", path=Path("/x.mp4"), filename=f"v{i}.mp4", size=1000,
                     public_url=f"https://pub.example/v{i}.mp4")


@pytest.fixture
def ig(settings):
    return InstagramConnector(settings, httpx.Client())


def quota():
    respx.get(f"{G}/1789/content_publishing_limit").respond(json={"data": [{"quota_usage": 0, "config": {"quota_total": 50}}]})


# ------------------------------------------------------------------------------------------------ collaborators
def test_collaborators_parse_and_validate(ig):
    assert InstagramConnector.parse_collaborators("@Alice, bob  @alice") == ["alice", "bob"]
    assert InstagramConnector.parse_collaborators(["@a.b", "c_d"]) == ["a.b", "c_d"]
    r = BrandRules(username="huhbyte.ai", auth_mode="fb_login")
    codes = lambda media, opts: {i.code for i in ig.validate("hi", media, opts, r)}  # noqa: E731
    assert not {"collaborators", "collaborators_invalid", "collaborators_story", "collaborators_self"} & codes([img()], {"collaborators": "a, b, c"})
    assert "collaborators" in codes([img()], {"collaborators": "a, b, c, d"})
    assert "collaborators_invalid" in codes([img()], {"collaborators": "good, bad name!, .dot"})
    assert "collaborators_invalid" in codes([img()], {"collaborators": "x" * 31})
    assert "collaborators_story" in codes([img()], {"ig_type": "story", "collaborators": "a"})
    assert "collaborators_self" in codes([img()], {"collaborators": "@HuhByte.ai"})


def test_collaborators_and_location_warn_on_instagram_login(ig):
    issues = {i.code: i for i in ig.validate("hi", [img()], {"collaborators": "a", "location_id": "123"}, BrandRules(auth_mode=""))}
    assert issues["collaborators_needs_fb_login"].level == "warning" and "Facebook Login" in issues["collaborators_needs_fb_login"].message
    assert "location_id_needs_fb_login" in issues
    fb = {i.code for i in ig.validate("hi", [img()], {"collaborators": "a", "location_id": "123"}, BrandRules(auth_mode="fb_login"))}
    assert not {"collaborators_needs_fb_login", "location_id_needs_fb_login"} & fb


@respx.mock
def test_instagram_login_never_sends_collaborators_or_location(ig):
    quota()
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    respx.get(f"{G}/C1").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M1"})
    respx.get(f"{G}/M1").respond(json={})
    ig.publish(PublishContext("t1", auth("ig_login"), "Hi", [img()], {"collaborators": "a, b", "location_id": "123"}, {}))
    body = form(create.calls[0].request)
    assert "collaborators" not in body and "location_id" not in body and body["caption"] == "Hi"


@respx.mock
def test_collaborators_sent_on_reel_container(ig):
    quota()
    create = respx.post(f"{G}/1789/media").respond(json={"id": "C1"})
    respx.get(f"{G}/C1").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M1"})
    respx.get(f"{G}/M1").respond(json={"permalink": "https://instagram.com/reel/x"})
    out = ig.publish(PublishContext("t1", auth(), "Reel", [vid()], {"ig_type": "reel", "collaborators": "@one, two, @One"}, {}))
    body = form(create.calls[0].request)
    assert body["media_type"] == "REELS" and json.loads(body["collaborators"]) == ["one", "two"]
    assert out.status == "published"


@respx.mock
def test_collaborators_only_on_carousel_parent_not_children(ig):
    quota()
    ids = iter(["K1", "K2", "PARENT"])
    create = respx.post(f"{G}/1789/media").mock(side_effect=lambda r: httpx.Response(200, json={"id": next(ids)}))
    for k in ("K1", "K2", "PARENT"):
        respx.get(f"{G}/{k}").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M2"})
    respx.get(f"{G}/M2").respond(json={"permalink": "https://instagram.com/p/c"})
    ctx = PublishContext("t1", auth(), "Swipe", [img(1), img(2)], {"collaborators": ["a", "b", "c", "d"]}, {})
    first = ig.publish(ctx)
    if first.status == "processing":
        first = ig.poll(ctx)
    bodies = [form(c.request) for c in create.calls]
    assert all("collaborators" not in b for b in bodies[:2])          # children never carry collaborators
    assert bodies[2]["media_type"] == "CAROUSEL" and json.loads(bodies[2]["collaborators"]) == ["a", "b", "c"]  # capped at 3
    assert first.status == "published"


@respx.mock
def test_story_never_sends_collaborators(ig):
    quota()
    create = respx.post(f"{G}/1789/media").respond(json={"id": "S1"})
    respx.get(f"{G}/S1").respond(json={"status_code": "FINISHED"})
    respx.post(f"{G}/1789/media_publish").respond(json={"id": "M3"})
    respx.get(f"{G}/M3").respond(json={})
    ig.publish(PublishContext("t1", auth(), "", [img()], {"ig_type": "story", "collaborators": "a"}, {}))
    assert "collaborators" not in form(create.calls[0].request)


@respx.mock
def test_ig_mentions_sync(ig):
    respx.get(f"{G}/1789/tags").respond(json={"data": [
        {"id": "T9", "caption": "big thanks @huhbyte.ai", "username": "partner", "permalink": "https://instagram.com/p/t9",
         "timestamp": "2026-10-20T09:30:00+0000", "like_count": 4}]})
    rows = ig.list_mentions(auth())
    assert rows[0]["kind"] == "mention" and rows[0]["external_id"] == "tag-T9" and rows[0]["author"] == "partner"
    assert rows[0]["media_external_id"] == "T9" and rows[0]["text"] == "big thanks @huhbyte.ai" and not rows[0]["is_own"]
    rep = respx.post(f"{G}/T9/comments").respond(json={"id": "K5"})       # replying to a mention comments on that media
    assert ig.reply_comment(auth(), "tag-T9", "thank you!") == "K5" and form(rep.calls[0].request)["message"] == "thank you!"


# ------------------------------------------------------------------------------------------------ mentions via the API
def test_mentions_tab_sync_and_list(api, channel):
    cid = channel["id"]
    assert api.post(f"/api/channels/{cid}/mentions/sync").json()["new"] == 1
    assert api.post(f"/api/channels/{cid}/mentions/sync").json()["new"] == 0         # idempotent
    d = api.get(f"/api/channels/{cid}/mentions").json()
    assert d["supported"] and d["open"] == 1 and d["items"][0]["author"] == "tech_reviewer"
    # comment sync pulls mentions too, but they never leak into the comments views
    api.post("/api/comments/sync", {"channel_id": cid})
    assert all(c["kind"] == "comment" for c in api.get(f"/api/comments?channel_id={cid}").json())
    assert [c["kind"] for c in api.get(f"/api/comments?channel_id={cid}&kind=mention").json()] == ["mention"]
    cv = api.get(f"/api/channels/{cid}/community?view=list").json()["comments"]
    assert cv and all(c["kind"] == "comment" for c in cv)
    assert len(api.get(f"/api/channels/{cid}/community?view=list&kind=mention").json()["comments"]) == 1
    r = api.post(f"/api/comments/{d['items'][0]['id']}/reply", {"text": "thanks for the shoutout"})
    assert r.status_code == 200 and FakeConnector.replies[-1]["to"] == "tag-m77"
    assert api.get(f"/api/channels/{cid}/mentions?status=open").json()["items"] == []


# ------------------------------------------------------------------------------------------------ bulk
def _sync(api, cid):
    api.post("/api/comments/sync", {"channel_id": cid})
    return api.get(f"/api/comments?channel_id={cid}").json()


def test_comments_bulk_resolve(api, channel):
    rows = _sync(api, channel["id"])
    ids = [r["id"] for r in rows]
    r = api.post("/api/comments/bulk", {"ids": ids, "action": "resolve"}).json()
    assert r["ok"] == 2 and r["failed"] == 0
    assert {c["status"] for c in api.get(f"/api/comments?channel_id={channel['id']}").json()} == {"resolved"}
    assert api.post("/api/comments/bulk", {"ids": ids[:1], "action": "reopen"}).json()["results"][0]["status"] == "open"
    assert api.post("/api/comments/bulk", {"ids": [], "action": "resolve"}).status_code == 400
    assert api.post("/api/comments/bulk", {"ids": ids, "action": "explode"}).status_code == 400


def test_comments_bulk_reply_with_saved_reply(api, channel):
    rows = _sync(api, channel["id"])
    api.put("/api/library/saved_replies", {"items": [{"id": "ty", "name": "Thanks", "text": "Thanks {author}! 🙌"}]})
    r = api.post("/api/comments/bulk", {"ids": [x["id"] for x in rows] + ["cm_missing"], "action": "reply", "saved_reply_id": "ty"}).json()
    assert r["ok"] == 2 and r["failed"] == 1 and r["results"][-1]["error"] == "Comment not found"
    sent = {x["text"] for x in FakeConnector.replies}
    assert sent == {"@ai_fan Thanks ai_fan! 🙌", "@dev_daily Thanks dev_daily! 🙌"}
    assert {c["status"] for c in api.get(f"/api/comments?channel_id={channel['id']}").json()} == {"replied"}
    assert api.post("/api/comments/bulk", {"ids": [rows[0]["id"]], "action": "reply", "saved_reply_id": "nope"}).status_code == 404
    assert api.post("/api/comments/bulk", {"ids": [rows[0]["id"]], "action": "reply", "text": "  "}).status_code == 400


# ------------------------------------------------------------------------------------------------ AI reply
def test_ai_reply_suggestion_rules_then_llm(api, channel):
    rows = _sync(api, channel["id"])
    q = next(r for r in rows if "?" in r["text"])
    d = api.post("/api/comments/suggest-reply", {"comment_id": q["id"]}).json()
    assert d["source"] == "rules" and len(d["suggestions"]) == 3 and all(s.startswith("@ai_fan") for s in d["suggestions"])
    assert "AI provider" in d["note"]
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"replies": ["@ai_fan It's GPT-6 preview!", "@ai_fan GPT-6 🙂", ""]})]
    d = api.post("/api/comments/suggest-reply", {"comment_id": q["id"], "tone": "playful"}).json()
    assert d["source"] == "llm" and d["suggestions"] == ["@ai_fan It's GPT-6 preview!", "@ai_fan GPT-6 🙂"] and d["tone"] == "playful"
    call = assistant.FakeLLM.calls[-1]
    assert "Which model is this?" in call["user"] and "playful" in call["system"]
    assistant.FakeLLM.replies[:] = ["Plain text answer"]
    assert api.post("/api/comments/suggest-reply", {"comment_id": q["id"]}).json()["suggestions"] == ["Plain text answer"]
    assert api.post("/api/comments/suggest-reply", {"comment_id": "cm_nope"}).status_code == 404


# ------------------------------------------------------------------------------------------------ comment -> post
def test_turn_comment_into_post(api, channel):
    rows = _sync(api, channel["id"])
    c = rows[0]
    d = api.post("/api/comments/start-post", {"comment_id": c["id"]}).json()
    assert c["text"] in d["caption"] and f"@{c['author']}" in d["caption"] and "draft" not in d
    d = api.post("/api/comments/start-post", {"comment_id": c["id"], "create_draft": True}).json()
    t = d["draft"]["targets"][0]
    assert t["status"] == "draft" and t["channel_id"] == channel["id"] and "from-comment" in t["tags"]
    assert api.post("/api/comments/start-post", {"comment_id": "nope"}).status_code == 404


# ------------------------------------------------------------------------------------------------ planned grid
def test_planned_grid_orders_queue_and_skips_stories(api, channel):
    cid = channel["id"]
    api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": d, "time": "10:00"} for d in range(7)]})
    a = api.post("/api/posts", {"caption": "first", "channel_ids": [cid], "when": "queue"}).json()["targets"][0]
    b = api.post("/api/posts", {"caption": "second", "channel_ids": [cid], "when": "queue"}).json()["targets"][0]
    api.post("/api/posts", {"caption": "draft", "channel_ids": [cid], "when": "draft"})
    g = api.get(f"/api/channels/{cid}/grid").json()
    assert [p["caption"] for p in g["planned"]] == ["second", "first"]      # last to go out = top-left
    assert g["queue_ids"] == [a["id"], b["id"]] and g["live"] == [] and g["live_supported"] is False
    # drag to reorder on the grid uses the queue order endpoint
    assert api.post(f"/api/queue/{cid}/order", {"ids": [b["id"], a["id"]]}).status_code == 200
    assert [p["caption"] for p in api.get(f"/api/channels/{cid}/grid").json()["planned"]] == ["first", "second"]


@respx.mock
def test_planned_grid_instagram_live_and_story_filter(api):
    respx.get(f"{G}/me").respond(json={"user_id": "1789", "id": "as", "username": "huhbyte.ai", "account_type": "BUSINESS"})
    ch = api.post("/api/connect/instagram/token", {"access_token": "IGTOKEN"}).json()
    respx.get(f"{G}/1789/media").respond(json={"data": [
        {"id": "L1", "media_type": "IMAGE", "media_url": "https://cdn/l1.jpg", "permalink": "p1", "timestamp": "2026-10-20T10:00:00+0000"},
        {"id": "L2", "media_type": "VIDEO", "media_product_type": "STORY", "permalink": "p2"}]})
    g = api.get(f"/api/channels/{ch['id']}/grid").json()
    assert g["live_supported"] and [m["external_id"] for m in g["live"]] == ["L1"]
