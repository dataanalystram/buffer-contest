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
