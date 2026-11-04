"""Aggregate reads honour channel scope (4 Nov 2026 audit).

A channel-scoped API token and a teammate whose channel matrix hides a channel must not see that channel in aggregate
reads: insights and its CSV, reports (summary, CSV, Markdown, audience, archive), best times, demographics, stories,
takeaways, comment insights, hashtag stats and suggestions, the home summary, agent page context and contacts. Before
the fix every one of these built its totals across all channels. The owner still sees everything (so the seed is
visible and the test can tell a leak from an empty answer)."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from huhqueue.core import utcnow
from huhqueue.models import PostTarget

from .test_agent_safety import _tok
from .test_teams import member

HIDDEN_MARKS = ("SECRETZZ", "otherbrandzz", "tagzz")

AGGREGATES = [
    "/api/insights", "/api/insights/export.csv", "/api/insights/best-times", "/api/insights/demographics",
    "/api/insights/stories", "/api/insights/takeaways", "/api/comments/insights", "/api/hashtags/stats",
    "/api/hashtags/suggest?caption=hello", "/api/home", "/api/reports/summary", "/api/reports/report.csv",
    "/api/reports/report.md", "/api/reports/audience", "/api/context?page=/queue", "/api/context?page=/community",
    "/api/contacts", "/api/posts/export.csv", "/api/posts",
]


@pytest.fixture
def world(app, api, channel, core):
    other = api.post("/api/connect/fake/token", {"access_token": "tok-2", "username": "otherbrandzz"}).json()
    for ch, cap in ((channel, "MINE_CAPTION #mine"), (other, "SECRETZZ_CAPTION #tagzz")):
        r = api.post("/api/posts", {"caption": cap, "channel_ids": [ch["id"]], "when": "draft"})
        assert r.status_code == 200, r.text
        tid = r.json()["targets"][0]["id"]
        with core.db.session() as s:   # a sent post with numbers, as the worker + insights sync leave it
            t = s.get(PostTarget, tid)
            t.status, t.published_at = "sent", utcnow() - timedelta(days=2)
            t.insights = {"views": 1000, "likes": 50, "comments": 5, "engagement_rate": 5.5}
        api.post("/api/comments/sync", {"channel_id": ch["id"]})
    return other


def _leaks(text: str, other_id: str) -> list[str]:
    return [m for m in (other_id, *HIDDEN_MARKS) if m in text]


def test_owner_sees_the_hidden_channel_in_aggregates(api, world):
    seen = {p: _leaks(api.get(p).text, world["id"]) for p in ("/api/insights", "/api/insights/export.csv", "/api/home",
                                                              "/api/hashtags/stats", "/api/reports/summary")}
    assert all(seen.values()), seen   # the seed is real: an unscoped caller sees the second channel


def test_scoped_token_aggregates_only_show_its_channels(app, api, channel, world):
    raw = _tok(api, allowed_channel_ids=[channel["id"]], scopes=["read", "write", "publish", "comments"])["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    out = {}
    for p in AGGREGATES:
        r = c.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:200])
        out[p] = _leaks(r.text, world["id"])
    assert not any(out.values()), {p: v for p, v in out.items() if v}
    # its own channel is still there
    assert "MINE_CAPTION" in c.get("/api/insights/export.csv").text
    assert c.get("/api/insights").json()["kpis"]["posts"] == 1


def test_restricted_teammate_aggregates_only_show_their_channels(app, api, world):
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    out = {}
    for p in AGGREGATES:
        r = ed.get(p)
        assert r.status_code == 200, (p, r.status_code, r.text[:200])
        out[p] = _leaks(r.text, world["id"])
    assert not any(out.values()), {p: v for p, v in out.items() if v}
    assert ed.get("/api/insights").json()["kpis"]["posts"] == 1


def test_asking_for_a_hidden_channel_is_refused_not_answered(app, api, channel, world):
    raw = _tok(api, allowed_channel_ids=[channel["id"]], scopes=["read"])["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    for p in ("/api/insights", "/api/insights/export.csv", "/api/reports/summary", "/api/insights/best-times"):
        r = c.get(f"{p}?channel_id={world['id']}")
        assert r.status_code == 403, (p, r.status_code)
        assert not _leaks(r.text.replace(world["id"], ""), "\x00")
    # a teammate who can't see the channel is refused by teams.authorize (#100: a read on a hidden channel is a 404)
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    for p in ("/api/insights", "/api/insights/export.csv", "/api/reports/summary", "/api/insights/best-times"):
        r = ed.get(f"{p}?channel_id={world['id']}")
        assert r.status_code == 404, (p, r.status_code)
        assert not _leaks(r.text, world["id"])


def test_report_archive_is_hidden_from_a_channel_limited_caller(app, api, channel, world, core):
    """Archived reports are made for every channel, so a channel-limited caller gets none of them."""
    from huhqueue.services import reports
    name = "huhqueue-report-all-channels-test.pdf"
    (reports.archive_dir(core) / name).write_bytes(b"%PDF-1.4 SECRETZZ otherbrandzz")
    assert name in api.get("/api/reports/archive").text   # the owner sees it
    raw = _tok(api, allowed_channel_ids=[channel["id"]], scopes=["read"])["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    assert c.get("/api/reports/archive").json() == {"reports": []}
    r = c.get(f"/api/reports/archive/{name}")
    assert r.status_code == 404 and b"SECRETZZ" not in r.content
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    assert ed.get("/api/reports/archive").json() == {"reports": []}
    assert ed.get(f"/api/reports/archive/{name}").status_code == 404


def _campaign_world(api, core, channel, other):
    """One campaign with a sent post on each channel, plus a UTM link made for each target and one manual link."""
    from huhqueue.models import Link
    api.post("/api/campaigns", {"name": "Launchzz"})
    tids = {}
    for ch, cap in ((channel, "MINE_CAMPAIGN"), (other, "SECRETZZ_CAMPAIGN")):
        r = api.post("/api/posts", {"caption": cap, "channel_ids": [ch["id"]], "when": "draft", "campaign": "Launchzz"})
        tid = r.json()["targets"][0]["id"]
        tids[ch["id"]] = tid
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            t.status, t.published_at = "sent", utcnow() - timedelta(days=1)
            t.insights = {"views": 1000 if ch is other else 10, "likes": 1}
            s.add(Link(code=f"c{tid[-6:]}", url=f"https://example.com/{cap}", campaign="Launchzz", source="utm",
                       target_id=tid, clicks=500 if ch is other else 3))
    with core.db.session() as s:
        s.add(Link(code="manualzz", url="https://example.com/manual", campaign="Launchzz", clicks=1))
    return tids


def test_campaigns_and_links_only_count_visible_channels(app, api, channel, world, core):
    tids = _campaign_world(api, core, channel, world)
    owner = next(c for c in api.get("/api/campaigns").json() if c["name"] == "Launchzz")
    assert owner["stats"]["views"] == 1010 and owner["stats"]["clicks"] == 504 and owner["posts"] == 2   # seed is real
    raw = _tok(api, allowed_channel_ids=[channel["id"]], scopes=["read", "write"])["token"]
    tok = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    hidden_link = next(ln for ln in api.get("/api/links").json() if ln.get("target_id") == tids[world["id"]])
    for c in (tok, ed):
        row = next(x for x in c.get("/api/campaigns").json() if x["name"] == "Launchzz")
        assert row["stats"]["views"] == 10 and row["stats"]["clicks"] == 4 and row["posts"] == 1, row["stats"]
        assert row["stats"]["channels"] == 1
        d = c.get(f"/api/campaigns/{row['id']}")
        assert d.status_code == 200 and not _leaks(d.text, world["id"]), _leaks(d.text, world["id"])
        assert "MINE_CAMPAIGN" in d.text
        links = c.get("/api/links")
        assert links.status_code == 200 and "SECRETZZ" not in links.text and "MINE_CAMPAIGN" in links.text
        # another channel's link answers exactly like a missing one
        for method, path in (("get", f"/api/links/{hidden_link['id']}"), ("get", "/api/links/ln_missingzz")):
            r = c.get(path)
            assert r.status_code == 404 and "SECRETZZ" not in r.text, (path, r.status_code)
    # a channel-limited editor can't edit or delete another channel's link either: the same 404 as a missing one
    assert ed.patch(f"/api/links/{hidden_link['id']}", {"title": "x"}).status_code == 404
    assert ed.delete(f"/api/links/{hidden_link['id']}").status_code == 404
    assert api.get(f"/api/links/{hidden_link['id']}").status_code == 200   # still there for the owner


def test_owner_campaign_clicks_count_links_on_channels_without_a_target_in_it(api, channel, world, core):
    """CodeRabbit/Greptile on #102: campaign_stats reused its `channels` argument for the channels it saw, so the owner
    (channels=None) went down the restricted branch and lost clicks on links whose target isn't in this campaign."""
    from huhqueue.models import Link
    _campaign_world(api, core, channel, world)
    third = api.post("/api/connect/fake/token", {"access_token": "tok-3", "username": "thirdzz"}).json()
    r = api.post("/api/posts", {"caption": "ELSEWHERE", "channel_ids": [third["id"]], "when": "draft"})   # no campaign
    tid = r.json()["targets"][0]["id"]
    with core.db.session() as s:   # a link still labelled with the campaign after its post moved out of it
        s.add(Link(code="movedzz", url="https://example.com/moved", campaign="Launchzz", source="utm", target_id=tid, clicks=40))
    owner = next(c for c in api.get("/api/campaigns").json() if c["name"] == "Launchzz")
    assert owner["stats"]["clicks"] == 544 and owner["stats"]["channels"] == 2, owner["stats"]


def test_restricted_link_list_is_not_starved_by_newer_hidden_links(app, api, channel, world, core):
    """Greptile on #102: the newest 1,000 links were taken before hiding other channels, so a restricted caller could
    get an empty list while older links of its own existed. The filter now runs before the cap."""
    from huhqueue.models import Link
    from huhqueue.services import growth
    tids = _campaign_world(api, core, channel, world)          # one link per channel + one manual link
    later = utcnow() + timedelta(minutes=5)
    with core.db.session() as s:   # a full window of newer links on the hidden channel's target
        s.add_all([Link(code=f"hidzz{i}", url=f"https://example.com/SECRETZZ{i}", target_id=tids[world["id"]], created_at=later)
                   for i in range(growth.LINKS_LIMIT)])
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    r = ed.get("/api/links")
    assert r.status_code == 200 and "SECRETZZ" not in r.text
    assert len(r.json()) == 2 and "MINE_CAMPAIGN" in r.text, r.text[:300]
    assert len(api.get("/api/links").json()) == growth.LINKS_LIMIT   # the owner's window is still capped


def test_report_best_times_honour_a_multi_channel_filter(app, api, channel, world, core):
    """Greptile on #102: a caller with two visible channels got best times across every channel (the comma list was
    dropped to None). Three channels with one sent post each; a token on two sees two posts in the heatmap."""
    third = api.post("/api/connect/fake/token", {"access_token": "tok-3", "username": "thirdzz"}).json()
    r = api.post("/api/posts", {"caption": "THIRD", "channel_ids": [third["id"]], "when": "draft"})
    with core.db.session() as s:
        t = s.get(PostTarget, r.json()["targets"][0]["id"])
        t.status, t.published_at, t.insights = "sent", utcnow() - timedelta(days=2), {"likes": 3}
    assert api.get("/api/reports/summary").json()["best_times"]["heatmap"]["posts"] == 3   # the seed is real
    raw = _tok(api, allowed_channel_ids=[channel["id"], third["id"]], scopes=["read"])["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    assert c.get("/api/reports/summary").json()["best_times"]["heatmap"]["posts"] == 2
    assert api.get(f"/api/reports/summary?channel_id={channel['id']},{third['id']}").json()["best_times"]["heatmap"]["posts"] == 2


def test_contact_detail_and_segments_only_use_visible_channels(app, api, channel, world, core):
    """CodeRabbit on #102 (outside the diff): /api/contacts/{key} and /api/contact-segments read every channel's
    comments. A commenter seen only on a hidden channel must 404 like a missing one; one seen on both shows only the
    visible history; segment counts drop the hidden-only people."""
    from huhqueue.models import Comment
    now = utcnow()
    with core.db.session() as s:
        s.add_all([Comment(id="cm_bothzz1", channel_id=channel["id"], author="bothzz", text="mine?", at=now, external_id="xbz1", parent_external_id=""),
                   Comment(id="cm_bothzz2", channel_id=world["id"], author="bothzz", text="SECRETZZ reply", at=now, external_id="xbz2", parent_external_id=""),
                   Comment(id="cm_onlyzz", channel_id=world["id"], author="hiddenonlyzz", text="SECRETZZ only", at=now, external_id="xoz", parent_external_id="")])
    assert api.get("/api/contacts/hiddenonlyzz").status_code == 200                       # the seed is real
    assert len(api.get("/api/contacts/bothzz").json()["history"]) == 2
    owner_total = sum(x["count"] for x in api.get("/api/contact-segments").json())
    # what a one-channel caller should see: the same segments computed from the visible comments only
    from huhqueue.api.pro_content import SEGMENTS, contact_rows
    with core.db.session() as s:
        rows = contact_rows(s, "", {channel["id"]})
    visible_segs = {k: sum(1 for x in rows if v[2](x)) for k, v in SEGMENTS.items()}
    assert sum(visible_segs.values()) < owner_total
    raw = _tok(api, allowed_channel_ids=[channel["id"]], scopes=["read", "comments"])["token"]
    tok = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    ed = member(app, api, "editor", access={world["id"]: "none"}, name="Eli")
    for c in (tok, ed):
        for key in ("hiddenonlyzz", "nobodyzz"):
            r = c.get(f"/api/contacts/{key}")
            assert r.status_code == 404 and "SECRETZZ" not in r.text, (key, r.status_code)
        d = c.get("/api/contacts/bothzz")
        assert d.status_code == 200 and "SECRETZZ" not in d.text and len(d.json()["history"]) == 1
        assert d.json()["comments"] == 1, d.json()
        segs = {x["id"]: x["count"] for x in c.get("/api/contact-segments").json()}
        assert segs == visible_segs, (segs, visible_segs)
    # and no edits through the back door: for the editor a hidden-only person is a 404 and nothing changes
    assert ed.patch("/api/contacts/hiddenonlyzz", json={}).status_code == 404
    assert ed.patch("/api/contacts/hiddenonlyzz", json={"notes": "x"}).status_code == 404
    assert ed.patch("/api/contacts/bothzz", json={"notes": "ok"}).status_code == 200
    assert api.get("/api/contacts/hiddenonlyzz").json().get("notes", "") in ("", None)
