"""CodeRabbit (4 Nov 2026), HIGH: a channel-limited API token could read and write another channel's post by its id.

safety.referenced_channels (the token channel-scope check in the guard middleware) only looked at channel ids in the body /
query, /api/channels/{id} and /api/targets/{id}. Routes that take a POST id (/api/posts/{id}/notes, /revisions, restore),
a COMMENT id (/api/comments/{id}..., comment_id in the body, /api/comments/bulk ids) or a channel id in another path shape
(/api/queue/{cid}/order, /api/utm/defaults/{cid}) were never resolved to their channel, so they passed the check.

Now every object id is resolved to its channel(s) before the route runs, and an object outside the token's channels
answers exactly like a missing one (404 Not found), so ids can't be probed for existence."""
from fastapi.testclient import TestClient

from huhqueue.models import Comment


def _tok(api, allowed):
    r = api.post("/api/tokens", {"name": "scoped", "scopes": ["read", "write", "publish", "comments"],
                                 "require_approval": False, "allowed_channel_ids": allowed})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _setup(app, api, core, channel):
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    mine = api.post("/api/posts", {"caption": "mine", "channel_ids": [channel["id"]], "when": "draft"}).json()
    theirs = api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "when": "draft"}).json()
    both = api.post("/api/posts", {"caption": "both", "channel_ids": [channel["id"], other["id"]], "when": "draft"}).json()
    note = api.post(f"/api/posts/{theirs['id']}/notes", {"text": "private plan"}).json()
    with core.db.session() as s:
        s.add(Comment(id="cm_other", channel_id=other["id"], external_id="x1", author="fan", text="hi", status="open"))
        s.add(Comment(id="cm_mine", channel_id=channel["id"], external_id="x2", author="fan", text="hi", status="open"))
    c = TestClient(app, headers={"Authorization": f"Bearer {_tok(api, [channel['id']])}"})
    return c, other, mine, theirs, both, note


def _hidden(r):
    """Same answer as an id that does not exist: 404, and nothing from the other channel in the body."""
    assert r.status_code == 404, (r.status_code, r.text)
    assert "secret" not in r.text and "private plan" not in r.text and "other_brand" not in r.text


def test_post_id_routes_resolve_the_post_channel(app, api, core, channel):
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    pid = theirs["id"]
    _hidden(c.get(f"/api/posts/{pid}/notes"))
    _hidden(c.post(f"/api/posts/{pid}/notes", json={"text": "agent was here"}))
    _hidden(c.delete(f"/api/posts/{pid}/notes/{note['id']}"))
    _hidden(c.get(f"/api/posts/{pid}/revisions"))
    _hidden(c.post(f"/api/posts/{pid}/revisions/1/restore"))
    # a post on an allowed AND a forbidden channel is not the token's either
    _hidden(c.get(f"/api/posts/{both['id']}/notes"))
    _hidden(c.post(f"/api/posts/{both['id']}/notes", json={"text": "x"}))
    # nothing was written to the other channel's post
    assert [n["text"] for n in api.get(f"/api/posts/{pid}/notes").json()] == ["private plan"]
    # the token's own post still works
    assert c.post(f"/api/posts/{mine['id']}/notes", json={"text": "ok"}).status_code == 200
    assert c.get(f"/api/posts/{mine['id']}/notes").status_code == 200
    assert c.get(f"/api/posts/{mine['id']}/revisions").status_code == 200


def test_forbidden_and_missing_ids_look_the_same(app, api, core, channel):
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    a = c.get(f"/api/posts/{theirs['id']}/notes")
    b = c.get("/api/posts/post_doesnotexist/notes")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    t = theirs["targets"][0]["id"]
    a, b = c.get(f"/api/targets/{t}"), c.get("/api/targets/tgt_doesnotexist")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    a, b = c.patch(f"/api/comments/cm_other", json={"flagged": True}), c.patch("/api/comments/cm_nope", json={"flagged": True})
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_target_routes_answer_404(app, api, core, channel):
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    t = theirs["targets"][0]["id"]
    _hidden(c.get(f"/api/targets/{t}"))
    _hidden(c.patch(f"/api/targets/{t}", json={"caption": "hijack"}))
    _hidden(c.get(f"/api/targets/{t}/events"))
    _hidden(c.post(f"/api/targets/{t}/duplicate"))
    _hidden(c.post("/api/bulk", json={"action": "delete", "ids": [t]}))
    assert api.get(f"/api/targets/{t}").json()["caption"] == "secret caption"


def test_comment_id_routes_resolve_the_comment_channel(app, api, core, channel):
    c, other, *_ = _setup(app, api, core, channel)
    _hidden(c.patch("/api/comments/cm_other", json={"flagged": True}))
    _hidden(c.post("/api/comments/cm_other/resolve"))
    _hidden(c.post("/api/comments/cm_other/reply", json={"text": "spam"}))
    _hidden(c.post("/api/comments/suggest-reply", json={"comment_id": "cm_other"}))
    _hidden(c.post("/api/comments/start-post", json={"comment_id": "cm_other"}))
    _hidden(c.post("/api/comments/bulk", json={"ids": ["cm_other"], "action": "resolve"}))
    with core.db.session() as s:
        cm = s.get(Comment, "cm_other")
        assert cm.status == "open" and not cm.flagged
    assert c.patch("/api/comments/cm_mine", json={"flagged": True}).status_code == 200


def test_channel_ids_in_other_path_shapes(app, api, core, channel):
    c, other, mine, theirs, *_ = _setup(app, api, core, channel)
    r = c.post(f"/api/queue/{other['id']}/order", json={"ids": []})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "channel_scope"
    r = c.put(f"/api/utm/defaults/{other['id']}", json={"utm_source": "x"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "channel_scope"


def test_media_usage_hides_other_channels(app, api, core, channel, image_file):
    from .conftest import upload
    m = upload(api, image_file)[0]
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "media_ids": [m["id"]], "when": "draft"})
    api.post("/api/posts", {"caption": "mine", "channel_ids": [channel["id"]], "media_ids": [m["id"]], "when": "draft"})
    c = TestClient(app, headers={"Authorization": f"Bearer {_tok(api, [channel['id']])}"})
    r = c.get(f"/api/media/{m['id']}/usage")
    assert r.status_code == 200 and "secret" not in r.text
    assert {p["channel_id"] for p in r.json()["posts"]} == {channel["id"]}
