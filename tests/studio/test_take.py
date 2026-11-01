"""One take -> a week: moment mining, reframe, captions, slots, The Spread, worker protocol, UI = REST = MCP parity."""
import base64
import json
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import actions, take, takes

from .conftest import CHANNELS, agent_token, mcp_call

SAMPLE = json.loads((Path(__file__).parent / "fixtures" / "nasa_take.json").read_text())  # the NASA talk, word level
BUNDLE = json.loads((Path(takes.SAMPLE_DIR) / "sample.json").read_text())  # the bundled sample take (Buffer kickoff, 5 clips)
WORDS = SAMPLE["analysis"]["words"]
ANALYSIS = {**SAMPLE["analysis"], "faces": []}


# ---------------------------------------------------------------- moment mining (extract, don't invent)
def test_moments_are_verbatim_spans_on_sentence_boundaries_and_deterministic():
    a = take.mine_moments(ANALYSIS)
    b = take.mine_moments(ANALYSIS)
    assert a == b and a["picked_by"] == "built-in scorer"
    ms = a["moments"]
    assert 3 <= len(ms) <= 7
    full = " ".join(w["w"] for w in WORDS)
    sents = take.sentences(WORDS)
    for m in ms:
        assert take.norm(m["text"]) in take.norm(full)                    # the clip is something actually said
        assert take.verbatim(m["hook"].rstrip("…"), m["text"])            # so is the hook on its cover
        assert any(abs(s["s"] - m["start"]) < 0.8 for s in sents)         # starts at a sentence
        assert take.MIN_CLIP - 1 <= m["duration"] <= take.MAX_CLIP + 1
    for x, y in zip(ms, ms[1:]):
        assert x["end"] <= y["start"]                                     # never overlap


def test_llm_picks_are_validated_and_invented_hooks_replaced():
    sents = take.sentences(WORDS)
    calls = []

    def chat(engine, system, user):
        calls.append(user)
        return {"moments": [
            {"start": 9, "end": 13, "hook": "Biofilms are very important to NASA.", "why": "clear stakes", "score": 91},
            {"start": 18, "end": 20, "hook": "This changes everything forever", "score": 88},     # hook never said
            {"start": 10, "end": 12, "hook": "overlaps the first", "score": 80},                  # overlaps
            {"start": 40, "end": 99, "hook": "out of range", "score": 99},                        # not in transcript
            {"start": 2, "end": 2, "hook": "too short", "score": 70}]}                             # 6 seconds
    out = take.mine_moments(ANALYSIS, want=3, engine={"source": "your key"}, chat_json=chat)
    assert "TRANSCRIPT" in calls[0] and "[9]" in calls[0]
    hooks = [m["hook"] for m in out["moments"]]
    assert "Biofilms are very important to NASA." in hooks
    assert "This changes everything forever" not in hooks
    for m in out["moments"]:
        assert take.verbatim(m["hook"].rstrip("…"), m["text"])
    assert len(out["moments"]) == 3 and out["picked_by"].startswith("your key")
    assert sents[9]["text"].startswith("Biofilms")


def test_model_failure_falls_back_to_the_built_in_scorer():
    def boom(*a):
        raise RuntimeError("HTTP 429")
    out = take.mine_moments(ANALYSIS, engine={"source": "free starter"}, chat_json=boom)
    assert out["picked_by"].startswith("built-in scorer") and "429" in out["picked_by"]
    assert out["moments"] == take.mine_moments(ANALYSIS)["moments"]


def test_no_speech_is_a_clear_error():
    with pytest.raises(take.TakeError, match="No speech"):
        take.mine_moments({"words": [], "probe": {"duration": 60}})


# ---------------------------------------------------------------- reframe
def _track(xs, start=0.0):
    return [{"t": start + i / 2, "cx": x, "cy": 0.4, "w": 0.12, "score": 0.95} for i, x in enumerate(xs)]


def test_face_stays_inside_the_9_16_window_while_the_speaker_moves():
    xs = [0.30] * 20 + [0.30 + 0.02 * i for i in range(20)] + [0.70] * 20      # walks from left to right
    faces = _track(xs)
    plan = take.crop_plan(faces, 0, 30, 1920, 1080)
    assert plan["mode"] == "track" and plan["crop_w"] == 608
    assert take.face_coverage(plan, faces, 0, 30, 1920) >= 0.9
    assert len(plan["keys"]) < 40                                               # smoothed, not a key per sample
    expr = take.crop_expr(plan)
    assert expr.startswith("if(lt(t") and "\\," in expr                         # ffmpeg-escaped piecewise expression


def test_no_face_uses_the_blurred_pad_layout_and_vertical_sources_fit():
    assert take.crop_plan([], 0, 20, 1920, 1080)["mode"] == "pad"
    assert take.crop_plan(_track([0.5] * 40), 0, 20, 1080, 1920)["mode"] == "fit"


def test_scene_cut_jumps_instead_of_panning():
    faces = _track([0.25] * 20 + [0.75] * 20)
    plan = take.crop_plan(faces, 0, 20, 1920, 1080, scenes=[10.0])
    xs = [x for t, x in plan["keys"] if 9.9 <= t <= 10.1]
    assert len(set(xs)) >= 2                                                    # both positions sit at the cut itself
    assert take.face_coverage(plan, faces, 0, 20, 1920) >= 0.9


def test_shots_split_at_scene_cuts():
    assert take.segments(10, 40, [5, 12, 12.5, 30, 39.5]) == [(10, 12), (12, 30), (30, 40)]


# ---------------------------------------------------------------- captions
def test_captions_use_the_brand_and_stay_above_the_bottom_20_percent():
    kit = {"font": "poppins", "colors": {"primary": "#101010", "accent": "#FF5A1F", "text": "#FFFFFF"}}
    p = {"font": "poppins", "colors": kit["colors"], "headline_case": "upper"}
    words = [w for w in WORDS if 71 <= w["s"] <= 80]
    ass = take.captions_ass(words, 71.0, 0.0, kit, p, 720, 1280)
    assert "Style: Cap,Poppins," in ass
    assert "&H001F5AFF" in ass                                                 # accent #FF5A1F as ASS BGR
    top, bottom = take.caption_box(ass, 1280)
    assert bottom <= 1280 * (1 - take.SAFE_BOTTOM) and top > 1280 * 0.4
    first = [line for line in ass.splitlines() if line.startswith("Dialogue")][0]
    assert first.split(",")[1].startswith("0:00:00") and "BIOFILMS" in ass


def test_caption_chunks_are_short():
    for ch in take.caption_chunks(WORDS):
        assert 1 <= len(ch) <= 4


# ---------------------------------------------------------------- the week: open slots + plan
SCHED = [{"id": "ig", "timezone": "Europe/Dublin", "postingSchedule": [
    {"day": d, "times": ["11:00", "19:00"], "paused": d == "sun"} for d in take.DOW]},
    {"id": "tt", "timezone": "America/Los_Angeles", "postingSchedule": [{"day": "mon", "times": ["09:00"], "paused": False}]}]


def test_open_slots_skip_paused_past_and_taken_and_follow_each_channels_timezone_across_dst():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    busy = [{"channelId": "ig", "dueAt": "2026-10-27T11:00:00.000Z"}]                    # Tue 11:00 Dublin (after DST)
    slots = take.open_slots(SCHED, busy, ["ig", "tt", "none"], date(2026, 10, 26), now)
    ig = slots["ig"]
    assert len(ig) == 6 * 2 - 1 and not any(s["day"] == "2026-11-01" for s in ig)       # Sunday paused, one taken
    assert ig[0]["at"] == "2026-10-26T11:00:00.000Z"                                    # Dublin is UTC+0 after 25 Oct
    assert slots["tt"][0]["at"] == "2026-10-26T16:00:00.000Z"                           # LA still on PDT (UTC-7)
    assert all(s["suggested"] for s in slots["none"]) and len(slots["none"]) == 21
    before = take.open_slots(SCHED, [], ["ig"], date(2026, 10, 19), datetime(2026, 10, 18, tzinfo=timezone.utc))["ig"]
    assert before[0]["at"] == "2026-10-19T10:00:00.000Z"                                # IST (UTC+1) before the change
    late = take.open_slots(SCHED, [], ["ig"], date(2026, 10, 26), datetime(2026, 10, 28, 12, tzinfo=timezone.utc))["ig"]
    assert late[0]["at"] == "2026-10-28T19:00:00.000Z"                                  # nothing in the past


def test_spread_never_double_books_spreads_days_and_respects_daily_room():
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    slots = take.open_slots(SCHED, [], ["ig"], date(2026, 10, 26), now)
    plan = take.plan_spread(["c1", "c2", "c3", "c4", "c5"], slots)
    ats = [p["at"] for p in plan["placements"]]
    assert len(ats) == len(set(ats)) == 5 and not plan["unplaced"]
    assert len({p["day"] for p in plan["placements"]}) == 5                             # one a day while days last
    room = {"ig": {d: 0 for d in ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30")}}
    plan = take.plan_spread(["c1", "c2", "c3"], slots, room)
    assert {p["day"] for p in plan["placements"]} == {"2026-10-31"} and len(plan["unplaced"]) == 1
    assert take.plan_spread(["a"], {"ig": []})["unplaced"][0]["why"].startswith("No open")


def test_week_start():
    now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)                                # a Friday
    assert take.week_start(None, "Europe/Dublin", now) == date(2026, 11, 2)
    assert take.week_start("this", "Europe/Dublin", now) == date(2026, 10, 26)
    assert take.week_start("2026-11-12", "UTC", now) == date(2026, 11, 9)


# ---------------------------------------------------------------- Buffer input for a placed clip
def test_dated_draft_reel_with_thumbnail_offset_only_where_buffer_honours_it():
    url = "https://hq.test/m/abc.mp4"
    ig = actions.build_post_input({"id": "ig", "service": "instagram"}, "hi", url, "draft", "2026-11-02T11:00:00.000Z",
                                  options={"ig_type": "reel", "thumbnail_offset_ms": 250})
    assert ig["mode"] == "customScheduled" and ig["saveToDraft"] is True and ig["dueAt"] == "2026-11-02T11:00:00.000Z"
    assert ig["assets"] == [{"video": {"url": url, "metadata": {"thumbnailOffset": 250}}}]
    assert ig["metadata"]["instagram"] == {"type": "reel", "shouldShareToFeed": True}
    yt = actions.build_post_input({"id": "yt", "service": "youtube"}, "hi", url, "draft", None,
                                  options={"title": "A hook", "category_id": "22", "thumbnail_offset_ms": 250})
    assert yt["assets"] == [{"video": {"url": url}}] and yt["mode"] == "addToQueue" and yt["saveToDraft"] is True
    assert yt["metadata"]["youtube"] == {"title": "A hook", "categoryId": "22"}
    assert actions.clean_options({"ig_type": "carousel", "thumbnail_offset_ms": "x"}) == {}


# ---------------------------------------------------------------- the sample workspace: no worker, no Buffer
@pytest.fixture
def demo(client):
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]
    return client


def test_sample_take_end_to_end_in_the_demo_drafts_only(demo):
    t = demo.post("/api/takes/sample", json={}).json()
    assert t["status"] == "ready" and t["sample"] and len(t["clips"]) == 5
    assert "Video: Buffer" in t["steps"][0]["detail"]                                       # credited
    assert [s["status"] for s in t["steps"]][-1] == "done"
    assert t["input"]["channels"] == [{"id": "demo_ig", "service": "instagram", "name": "brightdesk.news"}]
    for c in t["clips"]:
        assert c["url"].startswith("https://hq.test/sample/clip") and c["cover_url"].endswith(".jpg")
        pf = c["preflight"]["demo_ig"]
        assert pf["status"] == "ok" and "1080×1920" in pf["chip"]               # measured by the worker, not re-probed
        assert c["post_captions"]["instagram"].startswith("“")
    assert demo.get("/sample/clip1.mp4").headers["content-type"] == "video/mp4"
    assert demo.get("/sample/../store.py").status_code == 404
    before = len(demo.get("/api/calendar").json()["items"])
    dry = demo.post(f"/api/takes/{t['id']}/place", json={"dry_run": True}).json()
    plan = dry["plan"]
    assert dry["dry_run"] and dry["confirm_token"].startswith("cf_") and len(plan["placements"]) == 5
    assert len(demo.get("/api/calendar").json()["items"]) == before                    # a dry run creates nothing
    assert len({p["day"] for p in plan["placements"]}) == 5
    out = demo.post(f"/api/takes/{t['id']}/place", json={"confirm_token": dry["confirm_token"]}).json()
    assert out["placed"] == 5 and out["failed"] == 0 and "drafts" in out["message"]
    items = demo.get("/api/calendar").json()["items"]
    new = [i for i in items if i["state"] == "draft" and i["type"] == "video"]
    assert sorted(i["when"] for i in new) == sorted(p["at"] for p in plan["placements"])
    assert not any(i["state"] in ("scheduled", "sent") and i["type"] == "video" for i in items)  # never published
    again = demo.post(f"/api/takes/{t['id']}/place", json={"confirm_token": dry["confirm_token"]})
    assert again.status_code == 410
    t2 = demo.get(f"/api/takes/{t['id']}").json()
    assert all(c["placements"][0]["status"] == "draft" for c in t2["clips"])
    # the second spread skips the slots the first one took
    plan2 = demo.post(f"/api/takes/{t['id']}/place", json={"dry_run": True}).json()["plan"]
    assert not {p["at"] for p in plan2["placements"]} & {p["at"] for p in plan["placements"]}


def test_sample_clip_caption_edit_works_but_trim_needs_a_worker(demo):
    t = demo.post("/api/takes/sample", json={}).json()
    r = demo.patch(f"/api/takes/{t['id']}/clips/c2", json={"caption": "Biofilms, explained in 30 seconds.", "service": "instagram"})
    assert r.status_code == 200
    assert next(c for c in r.json()["clips"] if c["id"] == "c2")["post_captions"]["instagram"] == "Biofilms, explained in 30 seconds."
    r = demo.patch(f"/api/takes/{t['id']}/clips/c2", json={"start_ms": int(BUNDLE["moments"][1]["start"] * 1000) + 1000})
    assert r.status_code == 409 and "worker" in r.json()["error"]
    r = demo.patch(f"/api/takes/{t['id']}/clips/c2", json={"hook": "The most amazing breakthrough ever"})
    assert r.status_code == 400 and "extract, don't invent" in r.json()["error"]


def test_agent_spread_is_two_step_and_review_tokens_go_to_the_approval_inbox(demo):
    t = demo.post("/api/takes/sample", json={}).json()
    tok = agent_token(demo, policy="review", cap=20)
    res, body = mcp_call(demo, tok, "place_clips_in_week", {"take_id": t["id"], "dry_run": False})
    assert res["isError"] and "two steps" in body
    res, dry = mcp_call(demo, tok, "place_clips_in_week", {"take_id": t["id"]})
    rest = demo.post(f"/api/takes/{t['id']}/place", json={"dry_run": True}).json()
    key = lambda p: (p["clip_id"], p["channel_id"], p["at"], p["text"], json.dumps(p["options"], sort_keys=True))  # noqa: E731
    assert [key(p) for p in dry["plan"]["placements"]] == [key(p) for p in rest["plan"]["placements"]]   # MCP = REST
    assert "Approvals" in dry["on_confirm"]
    res, out = mcp_call(demo, tok, "place_clips_in_week", {"take_id": t["id"], "confirm_token": dry["confirm_token"]}, mid=3)
    assert out["placed"] == 5 and {r["status"] for r in out["results"]} == {"pending_approval"}
    pend = demo.get("/api/approvals").json()
    clip_appr = [a for a in pend if a["payload"]["mode"] == "draft" and a["payload"].get("due_at")]
    assert len(clip_appr) == 5
    sent = demo.post(f"/api/approvals/{clip_appr[0]['id']}/approve", json={}).json()
    assert sent["status"] == "sent_to_buffer" and sent["post"]["status"] == "draft"                # approved -> a draft
    # list_clips over MCP mirrors GET /api/takes
    res, lst = mcp_call(demo, tok, "list_clips", {}, mid=4)
    assert lst["takes"][0]["id"] == t["id"]
    res, one = mcp_call(demo, tok, "list_clips", {"take_id": t["id"]}, mid=5)
    assert [c["id"] for c in one["clips"]] == ["c1", "c2", "c3", "c4", "c5"] and "words" not in one["clips"][0]


def test_mcp_lists_the_one_take_tools(demo):
    tok = agent_token(demo)
    r = demo.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    names = {x["name"] for x in r["result"]["tools"]}
    assert {"clips_from_video", "list_clips", "adjust_clip", "place_clips_in_week"} <= names
    res, t = mcp_call(demo, tok, "clips_from_video", {"sample": True}, mid=2)
    assert t["status"] == "ready" and len(t["clips"]) == 5


# ---------------------------------------------------------------- starting a real take: guards
def _public_hosts(monkeypatch):
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda h: h not in ("127.0.0.1", "localhost", "10.0.0.1"))  # hermetic: no DNS


def test_start_needs_ownership_and_a_direct_file_link(human, app, monkeypatch):
    _public_hosts(monkeypatch)
    monkeypatch.setattr(app.state.house.takes, "_mode", lambda: "none")
    r = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4"})
    assert r.status_code == 400 and "owns" in r.json()["error"]
    for bad in ("https://www.facebook.com/somebrand", "https://www.twitch.tv/someone"):     # pages, not video posts
        r = human.post("/api/takes", json={"url": bad, "owns": True})
        assert r.status_code == 400 and "not a profile or page" in r.json()["error"]
    # a video post on YouTube / TikTok is an Opus-style take now (test_take_opus.py covers it end to end)
    monkeypatch.setattr("studio.media.find.oembed", lambda url, http=None: None)
    yt = human.post("/api/takes", json={"url": "https://youtu.be/abc", "owns": True}).json()
    assert yt["style"] == "opus" and yt["input"]["platform_url"] == "https://youtu.be/abc"
    assert human.post("/api/takes", json={"url": "http://cdn.example.com/a.mp4", "owns": True}).status_code == 400
    assert human.post("/api/takes", json={"url": "https://127.0.0.1/a.mp4", "owns": True}).status_code == 400
    t = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4", "owns": True}).json()
    assert t["status"] == "failed" and "no video worker" in t["steps"][1]["detail"]                # honest, not spinning
    assert t["input"]["channels"] == [{"id": "ch_ig", "service": "instagram", "name": "huhbyte.ai"}]


def test_sample_clips_cannot_be_placed_in_a_real_buffer_account(human):
    t = human.post("/api/takes/sample", json={}).json()
    r = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True})
    assert r.status_code == 403


# ---------------------------------------------------------------- worker protocol (GitHub Actions path), restart-safe
def _fake_mp4_meta(dur=40.0):
    return {"type": "video", "width": 720, "height": 1280, "duration": dur, "cover_offset_ms": 250, "layout": "track",
            "face_coverage": 0.97, "probe": {"probed": True, "kind": "video", "codec": "h264", "width": 720, "height": 1280,
                                             "fps": 30.0, "duration": dur, "video_kbps": 2400, "audio_kbps": 128, "bytes": 4_000_000}}


def test_worker_flow_analysis_then_clips_then_drafts_and_survives_a_web_restart(human, app, fake, monkeypatch, tmp_path):
    house = app.state.house
    dispatched = []

    def dispatch(jid, workflow="render.yml", inputs=None, ttl=1800):
        dispatched.append((jid, workflow, inputs, house.issue_job_token(jid, ttl)))
        return True, ""
    monkeypatch.setattr(house, "_dispatch_github", dispatch)
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "name": "My talk"}).json()
    assert t["status"] == "running" and dispatched[0][1:3] == ("take.yml", {"kind": "take_analyze"})
    jid, _, _, jt = dispatched[0]
    w = TestClient(app, base_url="https://hq.test")
    assert w.get(f"/api/worker/jobs/{jid}").status_code == 403
    spec = w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()
    assert spec["spec"]["kind"] == "take_analyze" and spec["urls"]["source"] == f"https://hq.test/m/{up['token']}"
    analysis = {**ANALYSIS, "providers": {"stt": "faster-whisper small (CPU int8, Silero VAD)"}, "timings": {"transcribe": 47.4}}
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": analysis}).json()["ok"]
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "rendering" and len(t["clips"]) == 3 and t["picked_by"] == "built-in scorer"
    rjid, wf, inputs, rjt = dispatched[1]
    assert inputs == {"kind": "take_render"}
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    assert [c["id"] for c in rspec["clips"]] == ["c1", "c2", "c3"] and rspec["clips"][0]["words"]
    assert rspec["kit"]["colors"] and rspec["params"]["font"]
    # the web process restarts mid-render: a fresh app on the same database takes the worker's callbacks
    from studio.app import create_app
    app2 = create_app(app.state.settings, http=app.state.house.http)
    app2.state.studio.media_loader = fake.load_media
    w2 = TestClient(app2, base_url="https://hq.test")
    for c in rspec["clips"]:
        r = w2.post(f"/api/worker/jobs/{rjid}/clips/{c['id']}", headers={"x-job-token": rjt},
                    json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42clip" + c["id"].encode()).decode(),
                          "cover": base64.b64encode(b"\xff\xd8\xff cover").decode(), "meta": _fake_mp4_meta()})
        assert r.json()["ok"]
    assert w2.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {"clips": 3}}).json()["ok"]
    assert w2.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {}}).status_code == 403  # token spent
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "ready" and all(c["preflight"]["ch_ig"]["status"] == "ok" for c in t["clips"])
    dry = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True, "week_of": "next"}).json()
    assert dry["plan"]["suggested_times"] is True                                                   # ch_ig has no posting times
    n0 = len(fake.created)
    out = human.post(f"/api/takes/{t['id']}/place", json={"confirm_token": dry["confirm_token"]}).json()
    assert out["placed"] == 3
    made = fake.created[n0:]
    assert len(made) == 3
    for inp in made:
        assert inp["saveToDraft"] is True and inp["mode"] == "customScheduled" and inp["dueAt"]
        assert inp["metadata"]["instagram"]["type"] == "reel"
        assert inp["assets"][0]["video"]["metadata"] == {"thumbnailOffset": 250}
        assert inp["text"].startswith("“")
    assert not any(i.get("mode") in ("shareNow", "shareNext", "addToQueue") and not i.get("saveToDraft") for i in fake.created)
    # trimming one clip re-renders only that clip on the worker, and its cover hook still quotes the new span
    c1 = t["clips"][0]
    r = human.patch(f"/api/takes/{t['id']}/clips/c1", json={"start_ms": int(c1["start"] * 1000) + 6000, "end_ms": int(c1["end"] * 1000)})
    assert r.status_code == 200 and r.json()["status"] == "rendering"
    jid3, wf3, inputs3, jt3 = dispatched[-1]
    spec3 = w.get(f"/api/worker/jobs/{jid3}", headers={"x-job-token": jt3}).json()["spec"]
    assert [c["id"] for c in spec3["clips"]] == ["c1"] and spec3["clips"][0]["start"] == round(c1["start"] + 6, 2)
    new = next(c for c in r.json()["clips"] if c["id"] == "c1")
    assert take.verbatim(new["hook"].rstrip("…"), new["text"])


def test_worker_failure_is_reported_on_the_take(human, app, monkeypatch):
    _public_hosts(monkeypatch)
    house = app.state.house
    toks = {}
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (toks.setdefault(jid, house.issue_job_token(jid, ttl)), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    t = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4", "owns": True}).json()
    jid, jt = next(iter(toks.items()))
    w = TestClient(app, base_url="https://hq.test")
    w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"error": "TakeError: This video has no audio track"})
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "failed" and "no audio" in t["steps"][1]["detail"]


# ---------------------------------------------------------------- the real renderer (FFmpeg), small and fast
needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


@needs_ffmpeg
def test_render_clip_cover_first_captions_end_card_and_passes_preflight(tmp_path):
    src = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=12", "-f", "lavfi",
                    "-i", "sine=frequency=330:duration=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    words = [{"w": w, "s": 2 + i * 0.5, "e": 2.4 + i * 0.5} for i, w in enumerate("This is the line we actually said on stage today.".split())]
    m = {"start": 1.5, "end": 8.0, "hook": "This is the line we actually said"}
    faces = _track([0.3] * 30)
    from studio import brand
    kit = {**brand.DEFAULT_KIT, "name": "Bright Desk", "handle": "brightdesk"}
    p = brand.visual_params(kit, [])
    data, cover, meta = take.render_clip(str(src), m, words, faces, [5.0], take.probe(str(src)), kit, p, W=360, H=640)
    assert data[4:8] == b"ftyp" and cover[:2] == b"\xff\xd8"
    expect = (8.0 - 1.5) + take.COVER_SECS + take.END_SECS
    assert abs(meta["duration"] - expect) <= 0.25
    assert meta["probe"]["codec"] == "h264" and meta["probe"]["width"] == 360 and meta["probe"]["fps"] == 30
    assert meta["cover_offset_ms"] < take.COVER_SECS * 1000 and meta["layout"] in ("track", "mixed")
    assert meta["caption_band"][1] <= 640 * (1 - take.SAFE_BOTTOM)
    from studio import preflight
    r = preflight.check({"id": "ig", "service": "instagram"}, "hi", ["https://x.test/c.mp4"], "draft",
                        loader=lambda u: data, known=lambda u: None)
    assert r["status"] == "ok", r["findings"]


@needs_ffmpeg
def test_local_worker_mode_runs_the_whole_take_in_process(human, app, monkeypatch, tmp_path):
    """Self-host / CI: FFmpeg on PATH, the take runs in the web process's background pool (sync in tests)."""
    src = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=50", "-f", "lavfi",
                    "-i", "sine=frequency=300:duration=50", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True)
    sents = take.sentences(WORDS)
    shifted = [{**w, "s": round(w["s"] - 71.5, 2), "e": round(w["e"] - 71.5, 2)} for w in WORDS if 71.5 <= w["s"] and w["e"] <= 120]
    monkeypatch.setattr(take, "TRANSCRIBER", lambda path: (shifted, "en", "test transcriber"))
    monkeypatch.setattr(take, "CLIP_W", 360)
    monkeypatch.setattr(take, "CLIP_H", 640)
    house = app.state.house
    house.sync = True
    monkeypatch.setattr(house.takes, "_mode", lambda: "local")
    orig = take.render_clip
    monkeypatch.setattr(take, "render_clip", lambda *a, **k: orig(*a, **{**k, "W": 360, "H": 640}))
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(src.read_bytes()).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "count": 1}).json()
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "ready", [(s["key"], s["status"], s["detail"]) for s in t["steps"]]
    assert len(t["clips"]) == 1 and t["clips"][0]["url"].startswith("https://hq.test/m/")
    assert t["providers"]["stt"] == "test transcriber"
    assert len(sents) > 5
