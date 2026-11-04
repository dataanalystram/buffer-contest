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
    a, b = c.patch("/api/comments/cm_other", json={"flagged": True}), c.patch("/api/comments/cm_nope", json={"flagged": True})
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


def test_teammate_without_channel_access_gets_404_for_its_objects(app, api, core, channel):
    """The same pattern on the teammate path (teams.authorize): a teammate with 'none' on a channel gets 404, not 403,
    for that channel's posts / targets / comments, on reads and writes, and body comment ids are resolved too."""
    from .test_teams import member
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    theirs = api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "when": "draft"}).json()
    with core.db.session() as s:
        s.add(Comment(id="cm_other", channel_id=other["id"], external_id="x1", author="fan", text="hi", status="open"))
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Eli")
    _hidden(ed.get(f"/api/posts/{theirs['id']}/notes"))
    _hidden(ed.post(f"/api/posts/{theirs['id']}/notes", {"text": "x"}))
    _hidden(ed.patch(f"/api/targets/{theirs['targets'][0]['id']}", {"caption": "hijack"}))
    _hidden(ed.patch("/api/comments/cm_other", {"flagged": True}))
    _hidden(ed.post("/api/comments/suggest-reply", {"comment_id": "cm_other"}))
    _hidden(ed.post("/api/comments/start-post", {"comment_id": "cm_other"}))
    _hidden(ed.post("/api/comments/bulk", {"ids": ["cm_other"], "action": "resolve"}))
    with core.db.session() as s:
        assert s.get(Comment, "cm_other").status == "open"


def test_media_delete_409_lists_only_visible_posts_but_still_blocks(app, api, core, channel, image_file):
    """CodeRabbit (4 Nov 2026): DELETE /api/media/{id} returned the unfiltered usage in its 409 body. The block is still
    decided on the FULL usage (a hidden channel's post keeps the file), but the body lists only the caller's posts."""
    from .conftest import upload
    m = upload(api, image_file)[0]
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "media_ids": [m["id"]], "when": "draft"})
    c = TestClient(app, headers={"Authorization": f"Bearer {_tok(api, [channel['id']])}"})
    r = c.delete(f"/api/media/{m['id']}")
    assert r.status_code == 409 and "secret" not in r.text and other["id"] not in r.text
    assert api.get(f"/api/media/{m['id']}/usage").json()["uses"] == 1   # not deleted


def test_non_list_ids_do_not_crash_the_scope_check(app, api, core, channel):
    _setup(app, api, core, channel)
    from huhqueue import safety
    with core.db.session() as s:
        assert safety.scope_refs(s, "POST", "/api/bulk", {}, {"ids": 5}) == (set(), set())
        assert safety.scope_refs(s, "POST", "/api/comments/bulk", {}, {"ids": "cm_other"}) == (set(), set())


def test_teammate_moving_own_post_to_a_forbidden_channel_gets_403_not_404(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): the 404 is chosen per denied channel. A channel the request NAMES (the move destination)
    keeps its 403; only a channel reached through an object id answers 404."""
    from .test_teams import member
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    mine = api.post("/api/posts", {"caption": "mine", "channel_ids": [channel["id"]], "when": "draft"}).json()
    theirs = api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "when": "draft"}).json()
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Mo")
    r = ed.post(f"/api/targets/{mine['targets'][0]['id']}/move", {"channel_id": other["id"]})
    assert r.status_code == 403, r.text
    _hidden(ed.post(f"/api/targets/{theirs['targets'][0]['id']}/move", {"channel_id": channel["id"]}))


def test_oversized_ids_list_is_refused_before_any_lookup(app, api, core, channel, monkeypatch):
    """CodeRabbit (4 Nov 2026): scope_refs runs one lookup per id before any route; a list over MAX_SCOPE_IDS is refused
    without looking any id up."""
    from huhqueue import safety
    c, *_ = _setup(app, api, core, channel)
    calls = []
    real = safety._target_channels
    monkeypatch.setattr(safety, "_target_channels", lambda s, t: calls.append(t) or real(s, t))
    ids = [f"tgt_{i}" for i in range(safety.MAX_SCOPE_IDS + 1)]
    r = c.post("/api/bulk", json={"action": "delete", "ids": ids})
    assert r.status_code == 400 and not any(str(x).startswith("tgt_") for x in calls), r.text
    with core.db.session() as s:
        assert safety.TOO_MANY in safety.scope_refs(s, "POST", "/api/comments/bulk", {}, {"ids": ids})[1]


def test_restricted_teammate_gets_404_for_a_missing_post_like_a_forbidden_one(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): GET /api/posts/{missing}/notes answered 200 [] to a restricted teammate but 404 for another
    channel's post, so ids could be probed. A path object that doesn't resolve is 404 for restricted teammates too."""
    from .test_teams import member
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    theirs = api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "when": "draft"}).json()
    mine = api.post("/api/posts", {"caption": "mine", "channel_ids": [channel["id"]], "when": "draft"}).json()
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Ana")
    a, b = ed.get(f"/api/posts/{theirs['id']}/notes"), ed.get("/api/posts/post_doesnotexist/notes")
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
    assert ed.get(f"/api/posts/{mine['id']}/notes").status_code == 200
    assert api.get("/api/posts/post_doesnotexist/notes").status_code == 200   # the owner: unchanged


def test_named_forbidden_channel_answers_the_same_whether_the_object_exists(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): PATCH /api/comments/{id}?channel_id={forbidden} gave 404 for a missing comment but 403 for
    one on that channel. The named channel is checked first, so both answer 403."""
    from .test_teams import member
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    with core.db.session() as s:
        s.add(Comment(id="cm_other", channel_id=other["id"], external_id="x1", author="fan", text="hi", status="open"))
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Bo")
    a = ed.patch(f"/api/comments/cm_other?channel_id={other['id']}", {"flagged": True})
    b = ed.patch(f"/api/comments/cm_nope?channel_id={other['id']}", {"flagged": True})
    assert a.status_code == b.status_code == 403 and a.json() == b.json()


def test_named_forbidden_channel_wins_over_the_objects_own_forbidden_channel(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): with no access to channels A and B, PATCH /api/comments/{id on A}?channel_id=B answered 404
    (A was checked first, object-only) while a missing comment answered 403. A forbidden channel the request names is now
    checked before any reached only through the object, so both answer 403."""
    from .test_teams import member
    a_ch = api.post("/api/connect/fake/token", {"access_token": "tok-a", "username": "brand_a"}).json()
    b_ch = api.post("/api/connect/fake/token", {"access_token": "tok-b", "username": "brand_b"}).json()
    with core.db.session() as s:
        s.add(Comment(id="cm_on_a", channel_id=a_ch["id"], external_id="xa", author="fan", text="hi", status="open"))
    ed = member(app, api, "editor", access={a_ch["id"]: "none", b_ch["id"]: "none"}, name="Cy")
    a = ed.patch(f"/api/comments/cm_on_a?channel_id={b_ch['id']}", {"flagged": True})
    b = ed.patch(f"/api/comments/cm_nope?channel_id={b_ch['id']}", {"flagged": True})
    assert a.status_code == b.status_code == 403 and a.json() == b.json()
    _hidden(ed.patch("/api/comments/cm_on_a", {"flagged": True}))   # no channel named: still 404 like a missing one
    with core.db.session() as s:
        assert not s.get(Comment, "cm_on_a").flagged


def test_token_naming_a_forbidden_channel_gets_403_whether_the_object_exists(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): the token path checked object ids first, so PATCH /api/comments/{id}?channel_id={forbidden}
    answered 404 for an existing or missing comment and the named channel never decided. A named forbidden channel is now
    403 first, the same for both; with no channel named, another channel's object still answers like a missing one."""
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    a = c.patch(f"/api/comments/cm_other?channel_id={other['id']}", json={"flagged": True})
    b = c.patch(f"/api/comments/cm_nope?channel_id={other['id']}", json={"flagged": True})
    assert a.status_code == b.status_code == 403 and a.json() == b.json()
    assert a.json().get("code") == "channel_scope" or "channel_scope" in a.text
    _hidden(c.patch("/api/comments/cm_other", json={"flagged": True}))
    with core.db.session() as s:
        assert not s.get(Comment, "cm_other").flagged


def test_teammate_token_answers_the_same_whether_the_object_exists(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): a teammate's token still listing a channel the teammate lost. PATCH
    /api/comments/{missing}?channel_id=C answered 404 at the token check while an existing comment on C reached
    teams.authorize and answered 403, so the comment's existence leaked. The token check now gives the teammate's own
    denial first, so both answer 403; a teammate who still has the channel keeps the 404 for a missing comment."""
    from .test_teams import invite, join
    c_ch = api.post("/api/connect/fake/token", {"access_token": "tok-c", "username": "brand_c"}).json()
    with core.db.session() as s:
        s.add(Comment(id="cm_on_c", channel_id=c_ch["id"], external_id="xc", author="fan", text="hi", status="open"))
    inv = invite(api, "admin", name="Dee")   # an admin mints the token, then is made a restricted editor
    ed = join(app, inv)
    r = ed.post("/api/tokens", {"name": "dee-bot", "scopes": ["read", "write", "comments"], "require_approval": False,
                                "allowed_channel_ids": [c_ch["id"]]})
    assert r.status_code == 200, r.text
    tok = TestClient(app, headers={"Authorization": f"Bearer {r.json()['token']}"})
    gone = tok.patch("/api/comments/cm_nope", json={"flagged": True})
    assert gone.status_code == 404                                    # still has the channel: a missing id is 404
    r = api.patch(f"/api/team/members/{inv['member']['id']}", {"role": "editor", "channel_access": {c_ch["id"]: "none"}})
    assert r.status_code == 200, r.text
    a = tok.patch(f"/api/comments/cm_on_c?channel_id={c_ch['id']}", json={"flagged": True})
    b = tok.patch(f"/api/comments/cm_nope?channel_id={c_ch['id']}", json={"flagged": True})
    assert a.status_code == b.status_code == 403 and a.json() == b.json()
    with core.db.session() as s:
        assert not s.get(Comment, "cm_on_c").flagged


def test_missing_bulk_and_body_comment_ids_answer_like_forbidden_ones(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): /api/comments/bulk with a missing id answered 200 to a restricted caller while another
    channel's id answered 404, and the same for a body comment_id, an existence oracle. Missing ids are now 404 too."""
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    for body in ({"ids": ["cm_nope"], "action": "resolve"}, {"ids": ["cm_other"], "action": "resolve"}):
        _hidden(c.post("/api/comments/bulk", json=body))
    for cid in ("cm_nope", "cm_other"):
        _hidden(c.post("/api/comments/suggest-reply", json={"comment_id": cid}))
    _hidden(c.post("/api/bulk", json={"ids": ["tgt_nope"], "action": "draft"}))
    assert c.post("/api/comments/bulk", json={"ids": ["cm_mine"], "action": "resolve"}).status_code == 200
    with core.db.session() as s:
        assert s.get(Comment, "cm_other").status == "open"


def test_validate_with_a_missing_post_or_target_id_answers_like_a_forbidden_one(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): POST /api/validate excluded post_id / target_id; a missing one answered 200 to a scoped
    caller while another channel's answered 404. Missing ids there are 404 too; the token's own post still validates."""
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    base = {"caption": "hi", "channel_ids": [channel["id"]]}
    for extra in ({"post_id": theirs["id"]}, {"post_id": "post_nope"},
                  {"target_id": theirs["targets"][0]["id"]}, {"target_id": "tgt_nope"}):
        _hidden(c.post("/api/validate", json={**base, **extra}))
    assert c.post("/api/validate", json={**base, "post_id": mine["id"]}).status_code == 200
    assert api.post("/api/validate", json={**base, "post_id": "post_nope"}).status_code == 200   # the owner: unchanged


def test_trailing_slash_paths_answer_missing_and_forbidden_ids_alike(app, api, core, channel):
    """CodeRabbit (4 Nov 2026): POST /api/validate/ (trailing slash) skipped the exact-path check, so a missing id got
    FastAPI's 307 redirect while another channel's got 404. The first response (redirects off) is now 404 for both."""
    c, other, mine, theirs, both, note = _setup(app, api, core, channel)
    base = {"caption": "hi", "channel_ids": [channel["id"]]}
    for extra in ({"post_id": theirs["id"]}, {"post_id": "post_nope"},
                  {"target_id": theirs["targets"][0]["id"]}, {"target_id": "tgt_nope"}):
        _hidden(c.post("/api/validate/", json={**base, **extra}, follow_redirects=False))
    for ids in (["tgt_nope"], [theirs["targets"][0]["id"]]):
        _hidden(c.post("/api/bulk/", json={"ids": ids, "action": "draft"}, follow_redirects=False))


def test_bulk_approve_answers_a_missing_target_like_a_forbidden_one(app, api, core, channel):
    """Greptile (4 Nov 2026): POST /api/bulk/approve with a missing target id answered 200 (a failed item) to a restricted
    teammate while another channel's target answered 404, an existence oracle. Missing ids there are 404 too."""
    from .test_teams import member
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "other_brand"}).json()
    theirs = api.post("/api/posts", {"caption": "secret caption", "channel_ids": [other["id"]], "when": "draft"}).json()
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Cy")
    a = ed.post("/api/bulk/approve", {"ids": [theirs["targets"][0]["id"]], "decision": "approve"})
    b = ed.post("/api/bulk/approve", {"ids": ["tgt_nope"], "decision": "approve"})
    assert a.status_code == b.status_code == 404 and a.json() == b.json()
