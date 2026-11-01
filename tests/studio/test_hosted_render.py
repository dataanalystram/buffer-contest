"""Hosted rendering: the hosted app (Render free, 512 MB; an FFmpeg Reel peaked at 1.5 GB) never renders video in-process.
Reels and Brand studio template Reels go to the GitHub render worker; the sample workspace only ever shows the committed
example Reel, and it is always labelled as an example, never passed off as the person's own content."""
from fastapi.testclient import TestClient

from studio import house as house_mod


def test_hosted_app_never_renders_video_in_process(app, monkeypatch):
    h = app.state.house
    monkeypatch.setattr(house_mod.render, "have_ffmpeg", lambda: True)
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("HQ_NO_LOCAL_VIDEO", raising=False)
    h.worker_mode = ""
    assert h.video_mode() == "local"  # self-host with FFmpeg: render here
    monkeypatch.setenv("RENDER", "true")  # Render sets this on every service
    assert not h.local_video_ok()
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    assert h.video_mode() == "none"
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "x")
    assert h.video_mode() == "github"
    h.worker_mode = "local"  # even a stale RENDER_WORKER=local can't make the hosted app run FFmpeg
    assert h.video_mode() == "github"
    monkeypatch.delenv("RENDER")
    monkeypatch.setenv("HQ_NO_LOCAL_VIDEO", "1")
    assert h.video_mode() == "github"
    h.worker_mode = "github"
    assert h.video_mode() == "github"
    assert h.takes._mode() == "github"  # One take follows the same rule


def test_sample_template_reel_is_a_labelled_example(app):
    c = TestClient(app, base_url="https://hq.test")
    c.get("/demo")
    c.headers["X-HQ-CSRF"] = c.get("/api/session").json()["csrf"]
    r = c.post("/api/templates/render", json={"template": "weekly_recap", "services": ["instagram"], "video": True}).json()
    v = r["video"]
    assert v["example"] is True and v["label"] == "Example"
    assert v["url"].startswith("/static/examples/") and v["note"].startswith("EXAMPLE REEL, not your content")


def test_template_reel_renders_on_the_worker_for_a_real_account(human, app, monkeypatch):
    h = app.state.house
    h.worker_mode = "github"
    seen = {}

    def dispatch(self, jid, workflow="render.yml", inputs=None, ttl=1800):
        tok = self.issue_job_token(jid)
        seen["spec"] = human.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": tok}).json()
        self.finish_video(jid, b"\x00\x00\x00\x18ftypmp42fake", {"type": "video", "template": "weekly_recap"})  # what the worker posts back
        return True, "204"
    monkeypatch.setattr(house_mod.House, "_dispatch_github", dispatch)
    r = human.post("/api/templates/render", json={"template": "weekly_recap", "services": ["instagram"], "video": True}).json()
    assert seen["spec"]["spec"]["kind"] == "template_video" and seen["spec"]["spec"]["template"] == "weekly_recap"
    assert "_job_token" not in seen["spec"]["spec"] and "front" in seen["spec"]["urls"]
    v = r["video"]
    assert v["example"] is False and v["rendered_by"] == "video worker" and "/m/" in v["url"] and "/static/examples/" not in v["url"]
    got = human.get(f"/api/templates/video/{r['video_job']['id']}").json()
    assert got["status"] == "done" and got["video"]["url"] == v["url"]


def test_template_reel_still_rendering_shows_no_canned_clip(human, app, monkeypatch):
    h = app.state.house
    h.worker_mode = "github"
    monkeypatch.setenv("HQ_TEMPLATE_VIDEO_WAIT", "0.2")
    monkeypatch.setattr(house_mod.House, "_dispatch_github", lambda self, jid, workflow="render.yml", inputs=None, ttl=1800: (True, "204"))
    r = human.post("/api/templates/render", json={"template": "quote", "services": ["instagram"], "video": True}).json()
    assert r["video"] is None and r["video_job"]["status"] == "queued"
    assert human.get(f"/api/templates/video/{r['video_job']['id']}").json()["status"] == "queued"
    # a worker that can't start is reported, still with no example clip in its place
    monkeypatch.setattr(house_mod.House, "_dispatch_github", lambda self, jid, workflow="render.yml", inputs=None, ttl=1800: (False, "GitHub answered 401"))
    r = human.post("/api/templates/render", json={"template": "quote", "services": ["instagram"], "video": True}).json()
    assert r["video"] is None and r["video_job"]["status"] == "failed" and "401" in r["video_job"]["note"]
