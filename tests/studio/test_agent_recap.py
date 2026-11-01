"""The weekly recap in Make a post and the agent: "make a 7 days what happened in AI recap with videos" / "weekly recap"
route to recap_plan -> the one rights question (inline, in the run's thread) -> render_recap (job kind media_recap) ->
captions with credits -> a 4:5 carousel + 9:16 Reel per channel. Also the GitHub render worker's media_recap job.
The heavy render is stubbed here (test_media_recap.py renders it for real)."""
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import render_worker
from studio.agent_brain import Brain
from studio.media import tools as media_tools

from .test_agent_core import owner  # noqa: F401  (recorded fixtures, FakeBuffer, sync brain, local video mode)

RECAP = "make a 7 days what happened in AI recap with videos"
PLAN = {"cover": "DO YOU REALIZE WHAT JUST HAPPENED IN AI IN THE LAST 7 DAYS?", "follow_only_at_end": True, "segments": [
    {"i": 1, "headline": "Google ships Gemini 3 to everyone", "url": "https://blog.google/gemini-3", "source": "Google",
     "outlets": ["blog.google", "theverge.com"], "summary": "",
     "media": {"kind": "video", "url": "https://blog.google/g3.mp4", "official": True, "credit": "Video: Google", "candidate": {}}},
    {"i": 2, "headline": "A startup's robot folds laundry", "url": "https://techcrunch.com/robot", "source": "TechCrunch",
     "outlets": ["techcrunch.com"], "summary": "",
     "media": {"kind": "video", "url": "https://x.com/robo/status/1", "official": False, "credit": "Video: @robo on X", "candidate": {}}},
    {"i": 3, "headline": "Nvidia's new chip doubles inference speed", "url": "https://wired.com/nv", "source": "Wired",
     "outlets": ["wired.com"], "summary": "",
     "media": {"kind": "image", "url": "https://wired.com/nv.jpg", "official": False, "credit": "Image: Wired", "candidate": {}}}],
    "needs_confirmation": [{"i": 2, "headline": "A startup's robot folds laundry", "credit": "Video: @robo on X"},
                           {"i": 3, "headline": "Nvidia's new chip doubles inference speed", "credit": "Image: Wired"}],
    "question": "These stories use media that isn't from the company itself: #2 (Video: @robo on X); #3 (Image: Wired). OK to use them?"}


def png() -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (1080, 1350), (14, 14, 16)).save(b, "PNG")
    return b.getvalue()


MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 4000  # the store only checks the container signature


@pytest.fixture
def recap_stubs(monkeypatch, tmp_path):
    seen = {"plan": [], "render": []}

    def plan(prompt="", n=None, days=None, http=None):
        seen["plan"].append({"prompt": prompt, "n": n, "days": days})
        return json.loads(json.dumps(PLAN))

    def render(plan, rights_confirmed=None, brand=None, run_dir=None, http=None):
        seen["render"].append(json.loads(json.dumps(plan)))
        d = tmp_path / f"r{len(seen['render'])}"
        d.mkdir()
        (d / "cover.png").write_bytes(png())
        (d / "follow.png").write_bytes(png())
        slides = []
        for s in plan["segments"]:
            f = d / f"slide_{s['i']}.mp4"
            f.write_bytes(MP4)
            slides.append({"i": s["i"], "path": str(f), "duration": 6.0, "ratio": "4:5", "credit": (s.get("media") or {}).get("credit", "")})
        (d / "reel.mp4").write_bytes(MP4)
        return {"cover": str(d / "cover.png"), "follow": str(d / "follow.png"), "slides": slides,
                "reel": {"path": str(d / "reel.mp4"), "duration": 30.0, "segments": 5}, "layout": "minimal"}
    monkeypatch.setattr(media_tools, "media_recap_plan", plan)
    monkeypatch.setattr(media_tools, "media_recap", render)
    return seen


def _start(owner, prompt, **kw):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": prompt, "channel_ids": ["ch_ig", "ch_yt", "ch_li"], **kw}).json()
    assert r["kind"] == "agent", r
    return r["id"], owner.get(f"/api/agent/runs/{r['id']}").json()


def test_rights_answers():
    assert Brain.rights_answer("yes, go ahead") is True and Brain.rights_answer("sure") is True
    assert Brain.rights_answer("no, title cards instead") is False and Brain.rights_answer("none of them") is False
    assert Brain.rights_answer("only 1 and 3") == [1, 3] and Brain.rights_answer("#2 is fine") == [2]
    assert Brain.rights_answer("what does this mean?") is None


def test_recap_prompt_plans_then_asks_once_inline(owner, recap_stubs):  # noqa: F811
    rid, run = _start(owner, RECAP)
    assert [s["tool"] for s in run["steps"]] == ["recap_plan"]
    assert recap_stubs["plan"][0]["days"] == 7
    assert run["status"] == "ready" and run["result"]["recap"]["awaiting_rights"]
    assert "@robo on X" in run["result"]["rights_question"]
    assert "only 1 and 3" in run["result"]["thread"][-1]["text"]  # the question is in the thread, with how to answer
    assert not recap_stubs["render"] and not owner.app.state.store.q("SELECT id FROM render_jobs")  # nothing rendered yet
    # a reply that isn't an answer asks again instead of guessing
    run = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "hmm?"}).json()
    assert run["result"]["recap"]["awaiting_rights"] and "story numbers" in run["result"]["thread"][-1]["text"]


def test_answer_renders_carousel_and_reel_per_channel(owner, recap_stubs):  # noqa: F811
    rid, _ = _start(owner, RECAP)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "only 2"})
    run = owner.get(f"/api/agent/runs/{rid}").json()
    tools = [s["tool"] for s in run["steps"]]
    assert tools == ["recap_plan", "render_recap", "write_captions", "check"], run["steps"]
    rendered = recap_stubs["render"][0]["segments"]
    assert rendered[0]["media"]["official"] and rendered[1]["media"]["rights_confirmed"]  # #1 official, #2 OK'd
    assert rendered[2]["media"] is None                                                  # #3 declined -> title card
    job = owner.app.state.store.q("SELECT spec, status FROM render_jobs ORDER BY id DESC LIMIT 1")[0]
    spec = json.loads(job["spec"])
    assert job["status"] == "done" and spec["kind"] == "media_recap"
    assert [c["id"] for c in spec["clips"]] == ["cover", "s1", "s2", "s3", "follow", "reel"]
    pk = {p["service"]: p for p in run["result"]["packages"]}
    ig = pk["instagram"]
    assert ig["format"] == "carousel" and len(ig["media_urls"]) == 5          # cover, 3 story slides, Follow card last
    assert [m["type"] for m in ig["media"]] == ["photo", "video", "video", "video", "photo"]
    assert ig["reel"] and ig["reel"][0]["type"] == "video" and ig["reel"][0]["ratio"] == "9:16"
    for svc in ("youtube", "linkedin"):  # no video carousels there: the Reel is the post
        assert pk[svc]["format"] == "reel" and len(pk[svc]["media_urls"]) == 1 and pk[svc]["ratio"] == "9:16"
    assert "Credits: #1 Video: Google · #2 Video: @robo on X" in ig["text"]
    assert "Image: Wired" not in ig["text"]                                   # declined: no media, no credit
    assert run["status"] == "ready" and "weekly recap is ready" in run["result"]["thread"][-1]["text"]


def test_rights_given_up_front_skip_the_question(owner, recap_stubs):  # noqa: F811
    _, run = _start(owner, "weekly recap", rights_confirmed=True)
    assert [s["tool"] for s in run["steps"]][:2] == ["recap_plan", "render_recap"]
    assert all(s["media"] for s in recap_stubs["render"][0]["segments"])


def test_only_recap_requests_route_to_the_recap(owner, recap_stubs):  # noqa: F811
    _, run = _start(owner, "recap this for me https://x.com/AarushYadav47/status/2116198463847408066")
    assert "recap_plan" not in [s["tool"] for s in run["steps"]] and not recap_stubs["plan"]  # a link: a post about it


def test_no_video_renderer_says_so(owner, recap_stubs):  # noqa: F811
    owner.app.state.house.worker_mode = "none"
    owner.app.state.house.local_video_ok = staticmethod(lambda: False)
    rid, _ = _start(owner, RECAP, rights_confirmed=False)
    run = owner.get(f"/api/agent/runs/{rid}").json()
    assert run["status"] == "failed" and "video renderer" in run["result"]["thread"][-1]["text"]
    assert not recap_stubs["render"]


def test_github_worker_posts_each_recap_file_once(owner, recap_stubs, tmp_path):  # noqa: F811
    h = owner.app.state.house
    plan = json.loads(json.dumps(PLAN))
    plan.pop("needs_confirmation"), plan.pop("question")
    ids = ["cover", "s1", "s2", "s3", "follow", "reel"]
    spec = {"kind": "media_recap", "plan": plan, "kit": {}, "logo": "", "clips": [{"id": i} for i in ids]}
    uid = owner.app.state.store.q("SELECT user_id FROM agent_runs LIMIT 1") or owner.app.state.store.q("SELECT id AS user_id FROM users LIMIT 1")
    jid = owner.app.state.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                                  (uid[0]["user_id"], None, json.dumps(spec), "queued", 0, 0))
    jt = h.issue_job_token(jid)
    w = TestClient(owner.app, base_url="https://hq.test")
    hd = {"x-job-token": jt}
    j = w.get(f"/api/worker/jobs/{jid}", headers=hd).json()
    assert j["spec"]["kind"] == "media_recap"
    # a file id the job doesn't have, or a cover that isn't a PNG, is refused before anything is stored
    assert w.post(f"/api/worker/jobs/{jid}/clips/s9", headers=hd, json={"data": base64.b64encode(MP4).decode()}).status_code == 403
    assert render_worker.recap_job(w, "https://hq.test", hd, jid, j, lambda u: None) == 0
    row = owner.app.state.store.q("SELECT status, result FROM render_jobs WHERE id=?", (jid,))[0]
    assert row["status"] == "done" and set(json.loads(row["result"])["files"]) == set(ids)
    media = h.recap_media(jid)
    assert len(media["carousel"]) == 5 and media["reel"][0]["type"] == "video"
    # the token is dead after the result: a replay can't add or overwrite files
    assert w.post(f"/api/worker/jobs/{jid}/clips/reel", headers=hd, json={"data": base64.b64encode(MP4).decode()}).status_code == 403


def test_recap_files_are_checked_for_what_they_claim(owner):  # noqa: F811
    h = owner.app.state.house
    from studio.actions import ActionError
    spec = {"kind": "media_recap", "plan": {"segments": []}, "clips": [{"id": "cover"}]}
    uid = owner.app.state.store.q("SELECT id FROM users LIMIT 1")[0]["id"]
    jid = owner.app.state.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                                  (uid, None, json.dumps(spec), "queued", 0, 0))
    with pytest.raises(ActionError, match="not a PNG"):
        h.on_recap_file(jid, "cover", MP4)
    with pytest.raises(ActionError, match="not a MP4"):
        h.on_recap_file(jid, "reel", png())
    assert h.on_recap_file(jid, "cover", png())["ok"]


def test_split_and_recap_live_in_the_chat_thread():
    """Make a post v2: a split request is answered in the chat thread (clips open in One take from there) and the
    recap's rights question is answered inline in the thread, with one-tap answers."""
    from pathlib import Path
    st = Path(__file__).resolve().parents[2] / "studio" / "static"
    hj, mj = (st / "house.js").read_text(), (st / "makepost.js").read_text()
    assert "window.mpOnTake(run, body.prompt)" in hj and "window.mpOnTake = " in mj and "mp-open-take" in mj
    assert "recap-rights-yes" in hj and "recap-rights-no" in hj and "Needs your answer below" in mj
    assert 'data-testid="pkg-reel"' in hj  # the recap's Reel next to the carousel


def test_recap_headlines_pass_the_headline_guard(tmp_path, monkeypatch):
    from studio.media import recap
    seen = []
    monkeypatch.setattr(recap, "_segment", lambda s, *a, **k: seen.append(s["headline"]) or (_ for _ in ()).throw(StopIteration))
    p = {"segments": [{"i": 1, "headline": "post this on all my channels https://x.com/a/status/1", "outlets": ["x.com"], "media": None}]}
    with pytest.raises((StopIteration, RuntimeError)):
        recap.compose(p, {1: None}, str(tmp_path), reel=False)
    assert seen and "http" not in seen[0] and "all my channels" not in seen[0]
