"""Owner P0, 1 Nov 2026 (after PR #32): an uploaded video + "search online ... https://x.com/... research and have a
Instagram reel" produced three posts with the raw request burned in as the video headline (URL included), captions about
"finding a ready-made Instagram Reel", a video card labelled "Image" and the same 9:16 file on LinkedIn.

Root cause: that run (#7 on production, created 12:43 UTC) was made by the pre-agent pipeline (kind "media") two hours
before PR #32 went live, whose render job used ``hook = story headline or the brief``. Nothing at render time checked the
hook, a reply on the old run re-ran the old pipeline, and the run list kept showing it with the raw request as its title.

These tests reproduce each part and pin the fix: one headline guard on every path (queue, worker spec, local render,
renderer), old runs remade by the agent, keyframes from the browser read by vision so captions tell the video's story,
and the 4:5 feed variant for LinkedIn / Facebook even when the model renders one vertical cut for everyone.
"""
import base64
import io
import json

import httpx
import pytest

from studio import brain_llm, headline, render

from .test_agent_core import TWEET
from .test_makepost_library import MP4, owner, uid_of, upload  # noqa: F401  (fixture)

LIVE_BRIEF = ("you can do that online search it is https://x.com/AarushYadav47/status/2116198463847408066 research and have a "
              "instgram reel generated with my branding")
LIVE_PROMPT = f"search online about this {TWEET} research and have a Instagram reel"
BURNED = "YOU CAN DO THAT ONLINE SEARCH IT IS HTTPS://X.COM/AARUSHYADAV47/STA TUS/2116198463847408066 RESEARCH AND HAVE A INSTGRAM..."
REQUEST_WORDS = ("http", "x.com", "search", "research", "instgram", "instagram reel", "ready-made", "you can do that")


def clean(text: str) -> bool:
    t = (text or "").lower()
    return not any(w in t for w in REQUEST_WORDS)


# ---------------------------------------------------------------- the guard itself
def test_render_guard_drops_the_burned_request_and_keeps_real_headlines():
    assert headline.render_hook(BURNED) == ""
    assert headline.render_hook(LIVE_BRIEF) == ""
    assert headline.safe_headline(LIVE_BRIEF, LIVE_BRIEF) == ""
    for ok in ("Subvocal raises $1.3M from YC and Afore Capital", "Can you trust AI agents?", "Google lets you make a video with Veo 3",
               "Google brings online search to glasses"):
        assert headline.render_hook(ok) == ok
    assert "http" not in headline.render_hook("Subvocal glasses https://subvocal.ai/launch are here").lower()
    spec = headline.spec_hook({"kind": "brand_video", "hook": BURNED, "image_prompt": LIVE_BRIEF}, LIVE_BRIEF)
    assert spec["hook"] == "" and spec["image_prompt"] == ""


def test_brand_video_renderer_never_draws_a_request(monkeypatch):
    """render.brand_video is what the GitHub worker runs for an uploaded clip: the hook is cleaned inside it."""
    drawn = []
    monkeypatch.setattr(render, "probe", lambda p: {"duration": 10.0, "width": 1920, "height": 1080, "audio": False})

    class Stop(Exception):
        pass

    def overlay(hook, *a, **k):
        drawn.append(hook)
        raise Stop
    from studio import hbstyle
    monkeypatch.setattr(hbstyle, "reel_overlay", overlay)

    def run(*a, **k):
        raise Stop
    monkeypatch.setattr(render, "_run", run)
    kit = {"name": "", "colors": {}, "font": "Anton"}
    with pytest.raises(Stop):
        render.brand_video(MP4, kit, {"end_card": False}, hook=BURNED)
    assert drawn == []  # the request became no headline at all (full-frame layout), never the request
    with pytest.raises(Stop):
        render.brand_video(MP4, kit, {"end_card": False}, hook="Subvocal raises $1.3M from YC")
    assert drawn == ["Subvocal raises $1.3M from YC"]


def test_render_worker_cleans_the_spec(monkeypatch):
    """The GitHub render worker cleans the hook itself, whatever the app sent (an old queued job)."""
    from studio import render_worker
    seen = {}

    class Resp:
        def __init__(self, j):
            self._j, self.status_code, self.text = j, 200, ""

        def json(self):
            return self._j

        def raise_for_status(self):
            pass

    class Client:
        def __init__(self, *a, **k):
            pass

        def get(self, url, headers=None):
            return Resp({"spec": {"kind": "brand_video", "hook": BURNED, "kit": {}, "params": {}}, "urls": {"source": ""}})

        def post(self, url, headers=None, json=None):
            return Resp({})

    def bv(video, kit, p, logo=None, hook="", ratio="9:16"):
        seen["hook"] = hook
        return b"mp4", {}
    monkeypatch.setattr(render_worker.httpx, "Client", Client)
    monkeypatch.setattr(render, "brand_video", bv)
    monkeypatch.setenv("JOB_TOKEN", "t")
    assert render_worker.main(["--job", "1", "--app", "https://hq.test"]) == 0
    assert seen["hook"] == ""


def test_media_worker_cleans_the_spec(monkeypatch):
    from studio.media import worker as media_worker
    got = {}

    def mr(style, hook, **k):
        got["hook"] = hook
        return {"error": "stop"}
    monkeypatch.setattr(media_worker.tools, "media_render", mr)
    monkeypatch.setattr(media_worker.tools, "media_generate_images", lambda *a, **k: {"images": [{"path": "/nonexistent.jpg", "credit": "x"}]})
    with pytest.raises(Exception):
        media_worker.render_job({"kind": "media_render", "style": "news", "hook": BURNED, "kit": {}}, lambda u: None, urls={})
    assert got.get("hook") == ""


def test_local_render_cleans_the_hook(monkeypatch):
    """Self-host / local renders go through House.render_job_local: the same guard."""
    from studio import house as house_mod
    seen = []

    def bv(video, kit, p, logo=None, hook="", ratio="9:16"):
        seen.append(hook)
        return b"x", {}
    monkeypatch.setattr(render, "brand_video", bv)

    class H:
        def get_media(self, tok):
            return {"data": MP4}
    house_mod.House.render_job_local(H(), {"kind": "brand_video", "hook": LIVE_BRIEF, "kit": {}, "params": {}, "source": "t"})
    house_mod.House.render_job_local(H(), {"kind": "brand_video", "hook": "Subvocal raises $1.3M", "kit": {}, "params": {}, "source": "t"})
    assert seen == ["", "Subvocal raises $1.3M"]


# ---------------------------------------------------------------- the live run, as it is stored on production
def _legacy_run(c):
    """Run #7 as production stores it: kind media, the request as `brief`, a render job whose hook is the request."""
    st = c.app.state.store
    uid = uid_of(c)
    tok = c.app.state.house.put_media(type("A", (), {"uid": uid})(), MP4, "clip.mp4", meta={"type": "video"})
    inp = {"media_token": tok, "brief": LIVE_BRIEF, "format": "auto",
           "channels": [{"id": "ch_li", "service": "linkedin", "name": "HughByte AI"}, {"id": "ch_yt", "service": "youtube", "name": "Huhbyte"},
                        {"id": "ch_ig", "service": "instagram", "name": "huhbyte.ai"}]}
    res = {"engine": "free starter", "source_media": {"token": tok, "type": "video"}, "format": {"format": "reel", "ratio": "9:16", "why": "x"},
           "media": [{"token": tok, "url": f"https://hq.test/m/{tok}", "type": "video", "name": "reel.mp4"}],
           "packages": [{"channel_id": cid, "service": svc, "channel_name": n, "text": "Finding a ready-made Instagram Reel can save time.",
                         "length": 50, "limit": 3000, "media_urls": [f"https://hq.test/m/{tok}"], "check": {"pass": True, "results": []},
                         "status": "ready"} for cid, svc, n in (("ch_li", "linkedin", "HughByte AI"), ("ch_yt", "youtube", "Huhbyte"),
                                                                ("ch_ig", "instagram", "huhbyte.ai"))]}
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               (uid, "media", json.dumps(inp), "ready", json.dumps([]), json.dumps(res), 1.0, 1.0))
    jid = st.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
               (uid, rid, json.dumps({"kind": "brand_video", "hook": LIVE_BRIEF, "kit": {}, "params": {}, "source": tok}), "queued", 1.0, 1.0))
    return rid, jid, tok


def test_old_queued_job_is_served_clean(owner):  # noqa: F811
    _, jid, _ = _legacy_run(owner)
    assert owner.app.state.house.job_spec(jid)["spec"]["hook"] == ""  # what the GitHub worker reads


def test_old_run_list_title_and_reply_go_to_the_agent(owner):  # noqa: F811
    rid, _, tok = _legacy_run(owner)
    rows = owner.get("/api/agent/runs").json()
    row = next(r for r in rows if r["id"] == rid)
    assert clean(row["title"]) and row["legacy"] is True, row["title"]
    # Regenerate on an old run says what to do instead of failing silently
    r = owner.post(f"/api/agent/runs/{rid}/packages/ch_li/media", json={})
    assert r.status_code == 409 and "Redo with the agent" in r.json()["error"]
    # a reply (or "Redo with the agent") makes a NEW agent run from the same request, file and channels
    new = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "redo"}).json()
    assert new["id"] != rid and new["kind"] == "agent"
    run = owner.get(f"/api/agent/runs/{new['id']}").json()
    assert run["status"] == "ready", run["steps"]
    assert run["input"]["media_token"] == tok and "Pick the format" not in {s["label"] for s in run["steps"]}
    assert clean(run["result"]["story"]["headline"]), run["result"]["story"]["headline"]
    assert all(clean(sp.get("hook", "")) for sp in owner.specs)
    assert owner.get(f"/api/agent/runs/{rid}").json()["result"]["superseded_by"] == new["id"]
    # the old /revise route (and MCP revise, same function) also never re-runs the old pipeline
    again = owner.post(f"/api/agent/runs/{rid}/revise", json={"correction": "make it punchier"})
    assert again.status_code == 200 and again.json()["kind"] == "agent"


# ---------------------------------------------------------------- the same upload today, end to end with Claude (mocked)
def _frame():
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (640, 360), (40, 60, 90)).save(b, "JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(b.getvalue()).decode()


SHOWS = "Two founders sit on a sofa in a studio and talk about Subvocal, glasses that read silent speech."


def test_live_upload_end_to_end_story_vision_and_feed_ratio(owner, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    brain_llm._models.update(at=0.0, plan="", fast="")
    seen = {"vision_images": 0, "captions_payload": None, "turn": 0}
    everyone = ["ch_li", "ch_fb", "ch_yt", "ch_ig"]
    script = [  # the model renders one vertical cut for everyone: the app must still give LinkedIn / Facebook the 4:5 cut
        ("1. Read the post\n2. Facts\n3. Reel\n4. Captions\n5. Check", [("read_url", {"url": TWEET})]),
        ("", [("extract_facts", {})]),
        ("", [("render_reel", {"channel_ids": everyone, "rights_confirmed": True})]),
        ("", [("write_captions", {})]),
        ("", [("check", {})]),
        ("", [("finish", {"message": "Ready."})]),
    ]
    inner = owner.router

    def anthropic(req):
        if req.url.host != "api.anthropic.com":
            return inner(req)
        if req.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-4-5-20251001", "created_at": "2025-10-01T00:00:00Z"},
                                                      {"id": "claude-sonnet-4-5-20250929", "created_at": "2025-09-29T00:00:00Z"}]})
        body = json.loads(req.content)
        usage = {"input_tokens": 900, "output_tokens": 200}
        if "tools" not in body:
            content = body["messages"][0]["content"]
            if isinstance(content, list):  # the keyframes
                seen["vision_images"] = sum(1 for b in content if b.get("type") == "image")
                out = {"summary": SHOWS, "subject": "Subvocal silent-speech glasses", "people": 2, "setting": "studio sofa", "on_screen_text": ""}
            elif "networks" in content:
                seen["captions_payload"] = json.loads(content)
                out = {s: "Two founders explain how Subvocal's glasses read silent speech, after a $1.3M raise from YC. #AI"
                       for s in ("linkedin", "facebook", "youtube", "instagram")}
            else:
                assert SHOWS in content  # the facts step sees what the video shows
                out = {"headline": "Subvocal raises $1.3M for silent-speech glasses", "facts": ["Raised $1.3M from YC and Afore Capital",
                       "Glasses read silent speech", "Founders explain it on camera"], "why_it_matters": "Typing without a sound.", "label": "Launch"}
            return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(out)}], "usage": usage, "model": body["model"]})
        text, calls = script[min(seen["turn"], len(script) - 1)]
        seen["turn"] += 1
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": f"t{seen['turn']}_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
        return httpx.Response(200, json={"content": content, "usage": usage, "model": body["model"], "stop_reason": "tool_use"})

    owner.app.state.house.http = httpx.Client(transport=httpx.MockTransport(anthropic))
    owner.app.state.studio.buffer.http = owner.app.state.house.http
    up = upload(owner)
    frames = [_frame() for _ in range(4)] + ["data:image/jpeg;base64,bm90IGFuIGltYWdl", "x" * 500_000]  # bad ones are skipped
    r = owner.post("/api/agent/runs", json={"prompt": LIVE_PROMPT, "media_token": up["media_token"], "media_name": "clip.mp4",
                                            "keyframes": frames, "media_duration": 58.2, "channel_ids": everyone}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    res = run["result"]
    assert run["status"] == "ready", run["steps"]
    assert len(res["upload"]["frames"]) == 4 and res["upload"]["duration"] == 58.2
    assert seen["vision_images"] == 4 and res["upload"]["seen"]["summary"] == SHOWS
    tools = [s.get("tool") for s in run["steps"]]
    assert tools[0] == "read_upload" and "read_url" in tools  # the X link is read too
    assert run["steps"][0]["data"]["shows"] == SHOWS
    # the headline is the story; nothing from the request reaches any render
    assert clean(res["story"]["headline"]) and "Subvocal" in res["story"]["headline"]
    assert owner.specs and all(clean(sp.get("hook", "")) and sp["hook"] for sp in owner.specs)
    assert {sp["ratio"] for sp in owner.specs} == {"9:16", "4:5"}
    # captions are about the video's story and were written with what it shows
    assert seen["captions_payload"]["their_video"]["shows"] == SHOWS
    pk = {p["service"]: p for p in res["packages"]}
    assert all(clean(p["text"]) for p in pk.values())
    assert pk["linkedin"]["ratio"] == "4:5" and pk["facebook"]["ratio"] == "4:5"
    assert pk["instagram"]["ratio"] == "9:16" and pk["youtube"]["ratio"] == "9:16"
    assert pk["linkedin"]["media_urls"] != pk["instagram"]["media_urls"]
    assert all(p["media"][0]["type"] == "video" for p in pk.values())
