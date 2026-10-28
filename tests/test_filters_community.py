"""Publish/calendar filters (channel, format, status, tag, campaign, search, undated), saved views, comment triage."""
from huhqueue.models import Comment

from .conftest import make_image, upload


def _post(api, channel, img, **kw):
    m = upload(api, img)[0]
    body = {"caption": kw.pop("caption", "hello"), "media_ids": [m["id"]], "channel_ids": [channel["id"]], "when": "draft", **kw}
    r = api.post("/api/posts", body)
    assert r.status_code == 200, r.text
    return r.json()["targets"][0]


def test_campaign_tag_search_format_filters(api, channel, tmp_path):
    a = _post(api, channel, make_image(tmp_path / "a.png"), caption="GPT launch recap", campaign="DevDay", tags=["news"])
    b = _post(api, channel, make_image(tmp_path / "b.png", color=(1, 2, 3)), caption="YC batch", campaign="YC W27", tags=["yc"])
    assert a["campaign"] == "DevDay" and a["format"] in {"post", "carousel", "reel", "story", "short", "video"}
    ids = lambda r: {t["id"] for t in r.json()}  # noqa: E731
    assert ids(api.get("/api/posts?campaign=DevDay")) == {a["id"]}
    assert ids(api.get("/api/posts?campaign=DevDay,YC%20W27")) == {a["id"], b["id"]}
    assert ids(api.get("/api/posts?tag=yc,other")) == {b["id"]}
    assert ids(api.get("/api/posts?q=gpt")) == {a["id"]}
    assert ids(api.get(f"/api/posts?post_type={a['format']}&campaign=DevDay")) == {a["id"]}
    assert ids(api.get("/api/posts?post_type=story")) == set()
    assert ids(api.get("/api/posts?undated=true&status=draft")) >= {a["id"], b["id"]}
    assert ids(api.get(f"/api/posts?channel_id={channel['id']},nope&status=draft,scheduled")) >= {a["id"], b["id"]}
    camps = api.get("/api/campaigns").json()
    assert {c["name"] for c in camps} == {"DevDay", "YC W27"}
    r = api.patch(f"/api/targets/{a['id']}", {"campaign": "Launch week"})
    assert r.status_code == 200, r.text
    assert ids(api.get("/api/posts?campaign=Launch%20week")) == {a["id"]}


def test_saved_views_roundtrip(api):
    assert api.get("/api/views").json() == []
    out = api.put("/api/views", {"items": [{"name": "IG reels", "page": "calendar", "filters": {"type": "reel", "ch": "ch_1", "empty": ""}},
                                           {"name": ""}]}).json()
    assert len(out) == 1 and out[0]["id"] and out[0]["filters"] == {"type": "reel", "ch": "ch_1"}
    assert api.get("/api/views").json() == out


def test_comment_flag_assign_and_filters(api, core, channel):
    with core.db.session() as s:
        for i, (who, st) in enumerate([("fan1", "open"), ("fan1", "replied"), ("fan2", "open")]):
            s.add(Comment(channel_id=channel["id"], external_id=f"c{i}", author=who, text=f"t{i}", status=st))
    rows = api.get(f"/api/comments?channel_id={channel['id']}").json()
    c = next(r for r in rows if r["author"] == "fan2")
    r = api.patch(f"/api/comments/{c['id']}", {"flagged": True, "assignee": "Ram"}).json()
    assert r["flagged"] and r["assignee"] == "Ram"
    assert [x["author"] for x in api.get("/api/comments?status=flagged").json()] == ["fan2"]
    assert {x["text"] for x in api.get("/api/comments?author=fan1").json()} == {"t0", "t1"}
    assert {x["text"] for x in api.get("/api/comments?status=answered").json()} == {"t1"}
    assert [x["author"] for x in api.get("/api/comments?assignee=Ram").json()] == ["fan2"]
    assert api.patch(f"/api/comments/{c['id']}", {"status": "resolved"}).json()["status"] == "resolved"
