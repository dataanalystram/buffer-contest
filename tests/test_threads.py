"""Threads: options.thread on X (mocked api.x.com) and the demo network, the split helper and its API action."""
import json

import httpx
import pytest
import respx

from huhqueue.connectors import BrandRules, ChannelAuth, MediaItem, PublishContext, RetryableError
from huhqueue.connectors.base import split_thread, thread_parts
from huhqueue.connectors.fake import FakeConnector
from huhqueue.connectors.x import API, XConnector, weighted_len
from huhqueue.worker import Worker

from .conftest import upload


@pytest.fixture
def xc(settings):
    return XConnector(settings, httpx.Client())


def auth():
    return ChannelAuth(id="c", external_id="42", username="huhbyte", access_token="XTOK")


def tweets(*ids):
    return [httpx.Response(201, json={"data": {"id": i}}) for i in ids]


def img(tmp_path, name="a.png"):
    p = tmp_path / name
    p.write_bytes(b"\0" * 50)
    return MediaItem(id=name, kind="image", mime="image/png", path=p, filename=name, size=50)


# ---------------------------------------------------------------------------------------------- helpers
def test_thread_parts_accepts_strings_and_objects_and_drops_blanks():
    opts = {"thread": ["two", {"text": " three ", "media_ids": ["m1", ""]}, "  ", {"text": "", "media_ids": []}, 7]}
    assert thread_parts(opts) == [{"text": "two", "media_ids": []}, {"text": "three", "media_ids": ["m1"]}]
    assert thread_parts({}) == [] and thread_parts(None) == []


def test_split_thread_breaks_at_paragraphs_sentences_and_words():
    text = "First paragraph is short.\n\n" + "This sentence is long enough. " * 12 + "\n\n" + "x" * 300
    parts = split_thread(text, 280, weighted_len)
    assert len(parts) >= 3 and all(weighted_len(p) <= 280 for p in parts)
    assert parts[0].startswith("First paragraph is short.")
    assert "".join(parts).replace(" ", "").replace("\n", "") == text.replace(" ", "").replace("\n", "")
    numbered = split_thread(text, 280, weighted_len, numbering=True)
    assert numbered[0].endswith(f" 1/{len(numbered)}") and all(weighted_len(p) <= 280 for p in numbered)
    assert split_thread("short", 280) == ["short"] and split_thread("  ") == []


def test_x_validates_each_thread_post(xc):
    rules = BrandRules()
    ok = xc.validate("one", [], {"thread": ["two", "three"]}, rules)
    assert not [i for i in ok if i.level == "error"]
    bad = xc.validate("one", [], {"thread": ["two", "y" * 281]}, rules)
    assert any(i.code == "thread_part_too_long" and "post 3" in i.message for i in bad)
    assert not [i for i in xc.validate("one", [], {"thread": ["y" * 281], "long_post": True}, rules) if i.level == "error"]
    many = xc.validate("one", [], {"thread": ["p"] * 25}, rules)
    assert any(i.code == "thread_too_long" for i in many)
    media = xc.validate("one", [], {"thread": [{"text": "t", "media_ids": list("abcde")}]}, rules)
    assert any(i.code == "x_max_media" for i in media)
    assert xc.capabilities["threads"] and any(f["key"] == "thread" for f in xc.option_schema())


# ---------------------------------------------------------------------------------------------- X publishing
@respx.mock
def test_x_posts_a_thread_as_a_reply_chain_with_part_media_and_reply_last(xc, tmp_path):
    respx.post(f"{API}/media/upload/initialize").respond(json={"data": {"id": "M2"}})
    respx.post(f"{API}/media/upload/M2/append").respond(204)
    respx.post(f"{API}/media/upload/M2/finalize").respond(json={"data": {"id": "M2"}})
    tw = respx.post(f"{API}/tweets").mock(side_effect=tweets("T1", "T2", "T3", "T4"))
    opts = {"thread": ["second", {"text": "third", "media_ids": ["a.png"]}], "first_comment": "Follow @huhbyte"}
    ctx = PublishContext("t", auth(), "first", [], opts, {}, attachments={"thread": [[], [img(tmp_path)]]})
    out = xc.publish(ctx)
    assert out.status == "published" and out.external_id == "T1" and not out.warnings
    bodies = [json.loads(c.request.content) for c in tw.calls]
    assert bodies[0] == {"text": "first"}
    assert bodies[1] == {"text": "second", "reply": {"in_reply_to_tweet_id": "T1"}}
    assert bodies[2] == {"text": "third", "reply": {"in_reply_to_tweet_id": "T2"}, "media": {"media_ids": ["M2"]}}
    assert bodies[3] == {"text": "Follow @huhbyte", "reply": {"in_reply_to_tweet_id": "T3"}}   # the CTA goes under the last post
    assert ctx.state["thread_ids"] == ["T2", "T3"]
    xc.publish(ctx)                       # a retry after success posts nothing again
    assert tw.call_count == 4


@respx.mock
def test_x_thread_resumes_after_a_rate_limit_without_double_posting(xc):
    tw = respx.post(f"{API}/tweets").mock(side_effect=[
        *tweets("T1", "T2"), httpx.Response(429, json={"title": "Too Many Requests", "detail": "slow down"}), *tweets("T3")])
    ctx = PublishContext("t", auth(), "first", [], {"thread": ["second", "third"]}, {})
    with pytest.raises(RetryableError):
        xc.publish(ctx)
    assert ctx.state["tweet_id"] == "T1" and ctx.state["thread_ids"] == ["T2"]
    out = xc.publish(ctx)                 # the worker retries with the saved state
    assert out.status == "published" and ctx.state["thread_ids"] == ["T2", "T3"]
    assert json.loads(tw.calls[-1].request.content)["reply"] == {"in_reply_to_tweet_id": "T2"}
    assert tw.call_count == 4


@respx.mock
def test_x_thread_part_refused_keeps_what_is_live_with_a_warning(xc):
    respx.post(f"{API}/tweets").mock(side_effect=[*tweets("T1", "T2"),
                                                  httpx.Response(403, json={"title": "Forbidden", "detail": "duplicate content"})])
    out = xc.publish(PublishContext("t", auth(), "first", [], {"thread": ["second", "second"]}, {}))
    assert out.status == "published" and out.external_id == "T1"
    assert "Posted 2 of 3" in out.warnings[0] and "duplicate" in out.warnings[0]


# ---------------------------------------------------------------------------------------------- API + worker
def test_split_action(api):
    r = api.post("/api/threads/split", {"text": "word " * 120, "numbering": True}).json()
    assert r["count"] == 3 and r["caption"] == r["parts"][0] and r["thread"] == r["parts"][1:]
    assert all(weighted_len(p) <= 280 for p in r["parts"]) and r["parts"][-1].endswith("3/3")
    assert api.post("/api/threads/split", {"text": "x", "provider": "instagram"}).status_code == 400
    run = api.post("/api/actions/thread.split:run", {"input": {"text": "a. " * 200, "provider": "fake"}}).json()
    assert run["status"] == "done" and run["output"]["limit"] == 500


def test_thread_validates_and_publishes_through_the_worker(core, api, channel, image_file):
    v = api.post("/api/validate", {"caption": "one", "channel_ids": [channel["id"]], "options": {"thread": ["two", "z" * 501]}}).json()
    assert any(i["code"] == "thread_part_too_long" for i in v["issues"][channel["id"]])
    m = upload(api, image_file)[0]
    r = api.post("/api/posts", {"caption": "one", "channel_ids": [channel["id"]], "when": "now",
                                "options": {"thread": ["two", {"text": "three", "media_ids": [m["id"]]}]}})
    assert r.status_code == 200, r.text
    tid = r.json()["targets"][0]["id"]
    assert api.get(f"/api/targets/{tid}").json()["options"]["thread"][0] == "two"
    assert api.get(f"/api/media/{m['id']}/usage").json()["uses"] == 1    # thread media counts as used
    Worker(core).tick()
    last = FakeConnector.published[-1]
    assert last["target"] == tid and last["thread"] == ["two", "three"] and last["thread_media"] == [0, 1]
    caps = next(c for c in api.get("/api/providers").json()["connectors"] if c["id"] == "x")["capabilities"]
    assert caps["threads"] and caps["thread_limit"] == 280


@respx.mock
def test_x_thread_part_with_a_lost_reply_is_unconfirmed_not_reposted(xc):
    from huhqueue.connectors import Unconfirmed
    tw = respx.post(f"{API}/tweets").mock(side_effect=[*tweets("T1"), httpx.Response(503, json={"title": "Service Unavailable"})])
    ctx = PublishContext("t", auth(), "first", [], {"thread": ["second", "third"]}, {})
    with pytest.raises(RetryableError):
        xc.publish(ctx)
    assert ctx.state["thread_sending"] == 0 and ctx.state["thread_ids"] == []
    with pytest.raises(Unconfirmed, match="thread post 2"):
        xc.publish(ctx)                   # X may have posted part 2: hand it to a person, never post it again
    assert tw.call_count == 2
