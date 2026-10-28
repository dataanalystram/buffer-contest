"""Fixes from the live test of 2026-10-26: hidden-comment note, sync notes in the API."""
import httpx
import pytest
import respx

from huhqueue.connectors import ChannelAuth
from huhqueue.connectors.fake import FakeConnector
from huhqueue.connectors.instagram import GRAPH, InstagramConnector
from sqlalchemy import select

from huhqueue.models import Channel, Event

G = f"{GRAPH}/v26.0"


def ig_auth():
    return ChannelAuth(id="ch1", external_id="1789", username="huhbyte.ai", access_token="IGTOKEN")


@respx.mock
def test_instagram_notes_comments_meta_hides(settings):
    ig = InstagramConnector(settings, httpx.Client())
    respx.get(f"{G}/1789/media").respond(json={"data": [{"id": "M1", "comments_count": 2}, {"id": "M2", "comments_count": 0}]})
    respx.get(f"{G}/M1/comments").respond(json={"data": []})
    assert ig.list_comments(ig_auth()) == []
    assert "returned none" in ig.last_comments_note and "Development mode" in ig.last_comments_note


@respx.mock
def test_instagram_no_note_when_comments_arrive(settings):
    ig = InstagramConnector(settings, httpx.Client())
    respx.get(f"{G}/1789/media").respond(json={"data": [{"id": "M1", "comments_count": 1}]})
    respx.get(f"{G}/M1/comments").respond(json={"data": [{"id": "c1", "text": "hi", "username": "fan"}]})
    assert len(ig.list_comments(ig_auth())) == 1 and ig.last_comments_note == ""


def test_sync_surfaces_note_on_channel_and_api(api, channel, core, monkeypatch):
    note = "Instagram reports 2 comment(s) on your recent posts but returned none."
    monkeypatch.setattr(FakeConnector, "list_comments", lambda self, auth, limit_media=10: [])
    monkeypatch.setattr(FakeConnector, "last_comments_note", note, raising=False)
    monkeypatch.setattr(FakeConnector, "list_mentions", lambda self, auth, *a, **k: [], raising=False)
    r = api.post("/api/comments/sync", {}).json()
    assert r["new"] == 0 and r["notes"] and r["notes"][0]["note"] == note
    assert api.get(f"/api/channels/{channel['id']}").json()["comments_note"] == note
    # a second sync with the same note does not log the warning again
    api.post("/api/comments/sync", {})
    with core.db.session() as s:
        assert len([e for e in s.scalars(select(Event)) if "returned none" in (e.message or "")]) == 1
    monkeypatch.setattr(FakeConnector, "last_comments_note", "", raising=False)
    assert api.post("/api/comments/sync", {}).json()["notes"] == []
    assert api.get(f"/api/channels/{channel['id']}").json()["comments_note"] == ""
