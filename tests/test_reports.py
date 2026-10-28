"""Branded PDF report, period comparison, weekly schedule, archive and email."""
import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from huhqueue import actions as reg
from huhqueue.models import Post, PostTarget
from huhqueue.security import Auth
from huhqueue.services import reports
from huhqueue.worker import Worker


def seed(core, channel_id, rows):
    """rows: (days_ago, views, likes, caption)"""
    with core.db.session() as s:
        for days, views, likes, cap in rows:
            p = Post(caption=cap)
            s.add(p)
            s.flush()
            s.add(PostTarget(post_id=p.id, channel_id=channel_id, status="sent",
                             published_at=datetime.utcnow() - timedelta(days=days, hours=1),
                             insights={"views": views, "likes": likes, "comments": 2, "engagement_rate": 4.5}))


@pytest.fixture
def seeded(core, channel):
    seed(core, channel["id"], [(1, 1000, 50, "GPT-6 is here (and it's fast) – what changed"), (3, 500, 20, "YC W27: Orgo ships agents"),
                               (5, 250, 10, "AI fact: transformers are from 2017 🤯"),
                               (9, 300, 15, "previous period post"), (12, 100, 5, "older previous post")])
    return channel


def test_summary_compares_with_previous_period(api, seeded):
    d = api.get("/api/reports/summary?days=7").json()
    k = {x["metric"]: x for x in d["kpis"]}
    assert k["posts"]["current"] == 3 and k["posts"]["previous"] == 2
    assert k["views"]["current"] == 1750 and k["views"]["previous"] == 400
    assert k["views"]["change_pct"] == round(100 * (1750 - 400) / 400, 1)
    assert len(d["series"]) == len(d["previous_series"]) == d["days"]
    assert sum(x["views"] for x in d["series"]) == 1750 and sum(x["views"] for x in d["previous_series"]) == 400
    ch = d["channels"][0]
    assert ch["id"] == seeded["id"] and ch["metrics"]["views"]["current"] == 1750 and ch["metrics"]["posts"]["previous"] == 2
    assert d["top_posts"][0]["caption"].startswith("GPT-6")


def test_summary_custom_range_and_channel_filter(api, seeded):
    start = (datetime.utcnow() - timedelta(days=4)).strftime("%Y-%m-%dT00:00")
    d = api.get(f"/api/reports/summary?start={start}&channel_id={seeded['id']}").json()
    assert {x["metric"]: x["current"] for x in d["kpis"]}["posts"] == 2
    assert api.get("/api/reports/summary?days=7&channel_id=ch_nope").json()["channels"] == []


def test_pdf_is_a_valid_branded_pdf(api, seeded):
    r = api.get("/api/reports/report.pdf?days=7")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"] and ".pdf" in r.headers["content-disposition"]
    body = r.content
    assert body.startswith(b"%PDF-1.4") and body.rstrip().endswith(b"%%EOF")
    assert b"/Subtype /Image" in body and b"/SMask" in body            # the logo, alpha kept
    assert b"/Helvetica-Bold" in body
    xref = int(re.search(rb"startxref\n(\d+)", body).group(1))         # xref offsets point at real objects
    assert body[xref:xref + 4] == b"xref"
    offs = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", body)]
    assert offs and all(re.match(rb"\d+ 0 obj", body[o:o + 12]) for o in offs)
    assert api.get("/api/reports/report.pdf?days=7&download=false").headers["content-disposition"].startswith("inline")


def test_pdf_handles_empty_and_many_channels(api, core):
    assert api.get("/api/reports/report.pdf?days=30").content.startswith(b"%PDF")      # no channels, no posts
    for i in range(7):
        api.post("/api/connect/fake/token", {"access_token": f"tok-{i}", "username": f"chan_{i}"})
    pdf = reports.render_pdf(reports.report_data(core, "90"))
    assert pdf.count(b"/Type /Page ") >= 2                                           # channel cards flow onto a 2nd page


def test_pdf_text_helpers():
    from huhqueue.services.pdf import _clean, fit, text_width, wrap
    assert _clean("a – b 🤯") == "a - b ?"
    assert text_width("Hello", 10) == pytest.approx(22.78, abs=0.01)
    assert fit("x" * 200, 8, 60).endswith("...") and text_width(fit("x" * 200, 8, 60), 8) <= 60
    lines = wrap("word " * 80, 8, 100, max_lines=2)
    assert len(lines) == 2 and lines[-1].endswith("...")
    assert wrap("short one", 8, 100) == ["short one"]


def test_send_now_archives_and_lists(api, seeded):
    r = api.post("/api/reports/send", {"days": "7"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["file"].endswith(".pdf") and out["emailed"] == [] and out["email_error"] == ""
    lst = api.get("/api/reports/archive").json()["reports"]
    assert lst[0]["name"] == out["file"]
    f = api.get(lst[0]["url"])
    assert f.status_code == 200 and f.content.startswith(b"%PDF")
    assert api.get("/api/reports/archive/..%2Fsecret.key").status_code == 404
    assert api.get("/api/reports/archive/nope.pdf").status_code == 404
    assert api.delete(lst[0]["url"]).json() == {"ok": True}
    assert api.get("/api/reports/archive").json()["reports"] == []


def test_send_now_rejects_bad_email(api, seeded):
    assert api.post("/api/reports/send", {"email_to": ["not-an-email"]}).status_code == 400


def test_send_now_reports_missing_smtp_but_keeps_file(api, seeded):
    out = api.post("/api/reports/send", {"days": "7", "email_to": ["ram@example.com"]}).json()
    assert out["emailed"] == [] and "HUHQ_SMTP_HOST" in out["email_error"] and out["file"]


def test_email_is_sent_through_smtp(api, seeded, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=30):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            sent.append(("starttls",))

        def login(self, u, p):
            sent.append(("login", u))

        def send_message(self, msg):
            sent.append(("msg", msg["To"], msg["Subject"], [p.get_filename() for p in msg.iter_attachments()]))

    monkeypatch.setenv("HUHQ_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("HUHQ_SMTP_FROM", "reports@huhbyte.ai")
    monkeypatch.setenv("HUHQ_SMTP_USER", "u")
    monkeypatch.setattr(reports.smtplib, "SMTP", FakeSMTP)
    assert api.get("/api/reports/schedule").json()["email_configured"] is True
    out = api.post("/api/reports/send", {"days": "7", "email_to": "ram@example.com, b@example.com"}).json()
    assert out["emailed"] == ["ram@example.com", "b@example.com"]
    msg = [x for x in sent if x[0] == "msg"][0]
    assert msg[1] == "ram@example.com, b@example.com" and "report" in msg[2] and msg[3][0].endswith(".pdf")
    assert ("starttls",) in sent and ("login", "u") in sent


def test_schedule_validation_and_next_run(api):
    d = api.get("/api/reports/schedule").json()
    assert d["enabled"] is False and d["email_configured"] is False and d["timezone"]
    assert api.put("/api/reports/schedule", {"time": "25:00"}).status_code == 400
    assert api.put("/api/reports/schedule", {"weekday": 9}).status_code == 400
    assert api.put("/api/reports/schedule", {"email_to": "nope"}).status_code == 400
    assert api.put("/api/reports/schedule", {"timezone": "Mars/Base"}).status_code == 400
    assert api.put("/api/reports/schedule", {"days": "3"}).status_code == 400
    d = api.put("/api/reports/schedule", {"enabled": True, "weekday": 0, "time": "09:00", "timezone": "Europe/Dublin",
                                          "email_to": "ram@example.com"}).json()
    assert d["enabled"] and d["email_to"] == ["ram@example.com"] and d["next_run_at"]
    assert datetime.fromisoformat(d["next_run_at"].rstrip("Z")) > datetime.utcnow()
    assert api.put("/api/reports/schedule", {"enabled": False}).json()["next_run_at"] is None


def test_next_run_respects_timezone():
    sch = {"weekday": 0, "time": "09:00", "timezone": "Europe/Dublin"}
    now = datetime(2026, 10, 23, 12, 0)                                    # Friday; Irish clocks go back on Sun 25 Oct
    assert reports.next_run(sch, now) == datetime(2026, 10, 26, 9, 0)      # Mon 09:00 GMT = 09:00 UTC
    sch["timezone"] = "Asia/Kolkata"
    assert reports.next_run(sch, now) == datetime(2026, 10, 26, 3, 30)
    assert reports.next_run({"weekday": 0, "time": "09:00", "timezone": "UTC"}, datetime(2026, 10, 26, 9, 0)) == datetime(2026, 11, 2, 9, 0)


def test_worker_generates_due_weekly_report(api, core, seeded):
    api.put("/api/reports/schedule", {"enabled": True, "weekday": 0, "time": "09:00"})
    assert reports.run_due(core, datetime.utcnow()) == 0                  # not due yet
    due = datetime.fromisoformat(reports.get_schedule(core)["next_run_at"].rstrip("Z"))
    assert Worker(core).tick(due + timedelta(minutes=1))["reports"] == 1
    sch = reports.get_schedule(core)
    assert sch["last_status"].startswith("saved") and datetime.fromisoformat(sch["next_run_at"].rstrip("Z")) > due
    assert len(api.get("/api/reports/archive").json()["reports"]) == 1
    assert any("Weekly report ready" in n["message"] for n in api.get("/api/notifications").json())


def test_schedule_set_needs_admin_token(app, core):
    _, raw = Auth(core).mint_token("agent", ["read", "write"])
    c = TestClient(app)
    h = {"Authorization": f"Bearer {raw}"}
    assert c.get("/api/reports/schedule", headers=h).status_code == 200
    assert c.put("/api/reports/schedule", json={"enabled": True}, headers=h).status_code == 403
    assert c.post("/api/reports/send", json={"days": "7"}, headers=h).status_code == 200


def test_report_actions_in_registry(api):
    for aid in ("report.summary", "report.pdf", "report.send", "report.schedule_get", "report.schedule_set", "report.archive",
                "report.archive_delete", "nav.reports"):
        assert reg.get(aid), aid
    assert reg.get("report.pdf").to_dict()["mcp"] is False
    r = api.post("/api/actions/report.summary:run", {"input": {"days": "7"}}).json()
    assert r["status"] == "done" and "kpis" in r["output"]
