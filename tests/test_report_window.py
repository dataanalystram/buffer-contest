"""The report window on a pinned clock (4 Nov 2026: tests/e2e/test_ui_reports.py::test_01_views was flaky).

build_report() serialises its range with iso(), which keeps whole seconds. report_data() used to re-parse those strings
and query `published_at < hi`, so hi landed up to 999 ms before "now" and a post published in the same second as the
request disappeared from /api/reports/summary (13 of 14 posts, 6 of 7 reels) while the KPIs, computed from the
full-precision bounds, still said 14. These tests pin the clock to a time with a sub-second part so they fail on that code.
"""
from datetime import datetime, timedelta

import pytest

from huhqueue.api import pro_content
from huhqueue.models import Post, PostTarget
from huhqueue.services import reports

NOW = datetime(2026, 11, 4, 19, 24, 7, 650000)          # naive UTC, like utcnow(); .65 s into the second


@pytest.fixture
def pinned(monkeypatch):
    monkeypatch.setattr(pro_content, "utcnow", lambda: NOW)
    return NOW


def _seed(core, channel_id, when: list[datetime], fmt="reel"):
    with core.db.session() as s:
        for i, at in enumerate(when):
            p = Post(caption=f"post {i}")
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=channel_id, status="sent", published_at=at, options={"fk_format": fmt},
                             insights={"views": 100 + i, "likes": 1}))


def _captions(d):
    return sorted(p["caption"] for p in d["posts"])


def test_post_published_in_the_same_second_as_now_is_in_the_report(core, channel, pinned):
    _seed(core, channel["id"], [NOW - timedelta(milliseconds=200), NOW - timedelta(days=2, hours=3)])
    d = reports.report_data(core, "30", channel_id=channel["id"])
    assert _captions(d) == ["post 0", "post 1"]
    assert {k["metric"]: k["current"] for k in d["kpis"]}["posts"] == 2 == len(d["posts"])   # list and KPIs agree
    assert "window" not in d                                                                    # internal, never in the JSON


def test_format_filter_keeps_the_newest_reel(core, channel, pinned):
    _seed(core, channel["id"], [NOW - timedelta(milliseconds=50), NOW - timedelta(hours=6)])
    _seed(core, channel["id"], [NOW - timedelta(hours=1)], fmt="post")
    d = reports.report_data(core, "30", channel_id=channel["id"], fmt="reel")
    assert len(d["posts"]) == 2 and d["reels"]["count"] == 2


def test_window_bounds_are_inclusive_at_now_and_split_cleanly_at_lo(core, channel, pinned):
    lo = NOW - timedelta(days=30)
    _seed(core, channel["id"], [NOW, lo, lo - timedelta(microseconds=1)])     # exactly now, exactly lo, just before lo
    d = reports.report_data(core, "30", channel_id=channel["id"])
    k = {x["metric"]: x for x in d["kpis"]}
    assert _captions(d) == ["post 0", "post 1"]
    assert k["posts"]["current"] == 2 and k["posts"]["previous"] == 1                # the lo post is counted once


def test_custom_end_date_includes_that_whole_local_day(core, channel, pinned):
    tz = core.prefs()["timezone"]
    from huhqueue.services import posts as posts_svc
    end_of_day = posts_svc.parse_when("2026-11-03T23:30", tz)
    _seed(core, channel["id"], [end_of_day, posts_svc.parse_when("2026-11-04T00:30", tz)])
    d = reports.report_data(core, start="2026-11-01", end="2026-11-03", channel_id=channel["id"])
    assert _captions(d) == ["post 0"]


def test_end_before_start_is_a_400(api, pinned):
    assert api.get("/api/reports/summary?start=2026-11-03&end=2026-11-01").status_code == 400
    assert api.get("/api/reports/report.md?start=2026-11-03&end=2026-11-01").status_code == 400
    assert api.get("/api/reports/report.md?days=abc").status_code == 400
