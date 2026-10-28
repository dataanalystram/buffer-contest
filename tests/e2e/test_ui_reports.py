"""End-to-end: Insights analytics + the custom report builder (build report -> download PDF), backed by the demo connector.

Run:  CHROMIUM_PATH=/path/to/chromium pytest tests/e2e/test_ui_reports.py -q
"""
from datetime import datetime, timedelta
from pathlib import Path

from huhqueue.models import FollowerSnapshot, Post, PostTarget

from .conftest import PASSWORD, T, api, goto, toast


def test_00_login_and_seed(page, server):
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    ch = api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-rep", "username": "huhbyte_reports"})
    server["ch"] = ch["id"]
    core = server["core"]
    today = datetime.utcnow().date()
    with core.db.session() as s:
        for back, n in ((10, 1180), (6, 1200), (3, 1230), (0, 1264)):
            s.add(FollowerSnapshot(channel_id=ch["id"], day=(today - timedelta(days=back)).isoformat(), followers=n))
        for i in range(14):
            p = Post(caption=f"AI story {i} #AI #TechNews #tag{i % 3}", campaign="GPT-6 week" if i % 2 else "YC", tags=["ai"])
            s.add(p)
            s.flush()
            reel = i % 2 == 0
            s.add(PostTarget(post_id=p.id, channel_id=ch["id"], status="sent", options={"fk_format": "reel" if reel else "post"},
                             published_at=datetime.utcnow() - timedelta(days=i % 6, hours=i),
                             insights={"views": 500 + 100 * i, "reach": 400 + 80 * i, "likes": 20 + i, "comments": 3, "shares": 2, "saved": 4,
                                       "engagement_rate": 3.0 + (i % 4), **({"avg_watch_time": 4.5 + i % 3, "watch_time": 900.0} if reel else {})}))


def test_01_views(page, server):
    goto(page, server, "/insights")
    page.reload()                                                  # pick up the channel connected through the API
    T(page, "page-insights").wait_for()
    T(page, "kpi-views-delta").wait_for()
    T(page, "compare-chart").wait_for()

    T(page, "insights-view-audience").click()
    T(page, "aud-chart").wait_for()
    assert T(page, "aud-net").inner_text().find("+") >= 0
    T(page, "aud-snapshot").click()
    toast(page, "followers")

    T(page, "insights-view-posts").click()
    T(page, "posts-row").first.wait_for()
    T(page, "posts-sort-avg_watch_time").click()
    T(page, "insights-format").select_option("reel")
    page.wait_for_function("document.querySelectorAll('[data-testid=posts-row]').length === 7")
    T(page, "insights-format").select_option("")

    T(page, "insights-view-reels").click()
    T(page, "reels-row").first.wait_for()
    assert "s" in T(page, "reels-avg-watch").inner_text()

    T(page, "insights-view-hashtags").click()
    T(page, "hashtag-row").first.wait_for()
    T(page, "campaign-row").first.wait_for()
    assert "#ai" in T(page, "hashtag-table").inner_text()


def test_02_best_times_use_as_slots(page, server):
    T(page, "insights-view-best").click()
    T(page, "best-heatmap").wait_for()
    assert T(page, "best-cell").count() >= 1
    assert T(page, "best-apply").is_disabled()                    # needs one channel
    T(page, "insights-channel").select_option(server["ch"])
    T(page, "best-count").select_option("3")
    T(page, "best-apply").click()
    toast(page, "Posting times updated")
    slots = api(page, "GET", f"/api/channels/{server['ch']}/slots")
    assert len(slots) == 3


def test_03_build_report_and_download_pdf(page, server, tmp_path):
    T(page, "insights-view-report").click()
    T(page, "report-sections").wait_for()
    T(page, "report-section-campaigns").uncheck()
    T(page, "report-section-hashtags").uncheck()
    href = T(page, "report-download-pdf").get_attribute("href")
    assert "sections=" in href and "campaigns" not in href and f"channel_id={server['ch']}" in href
    with page.expect_download() as dl:
        T(page, "report-download-pdf").click()
    f = Path(tmp_path) / dl.value.suggested_filename
    dl.value.save_as(f)
    assert f.name.endswith(".pdf") and f.read_bytes().startswith(b"%PDF-1.4") and f.stat().st_size > 2000
    with page.expect_download() as dl2:
        T(page, "report-download-csv").click()
    assert dl2.value.suggested_filename.endswith(".csv")

    T(page, "report-generate").click()
    toast(page, "archive")
    T(page, "report-archive-row").first.wait_for()

    T(page, "report-schedule-enabled").click()
    T(page, "report-schedule-day").select_option("0")
    T(page, "report-schedule-time").fill("09:00")
    T(page, "report-schedule-save").click()
    toast(page, "Weekly report on")
    T(page, "report-schedule-next").wait_for()
    sch = api(page, "GET", "/api/reports/schedule")
    assert sch["enabled"] and "campaigns" not in sch["sections"] and sch["channel_id"] == server["ch"]

    T(page, "report-archive-delete").first.click()
    page.wait_for_function("!document.querySelector('[data-testid=report-archive-row]')")
    assert not page.errors, page.errors
