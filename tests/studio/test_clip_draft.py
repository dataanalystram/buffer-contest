"""Clip Studio drafts and the AI director (studio/clip_draft.py): one editing state for the person and agents."""
import json

import pytest


@pytest.fixture
def draft_clip(human, app, monkeypatch):
    from studio import takes
    house = app.state.studio.house
    monkeypatch.setattr(house, "video_mode", lambda: "github")
    rendered = []
    monkeypatch.setattr(takes.Takes, "_render_sample", lambda self, a, rid, c: rendered.append(c["id"]))
    monkeypatch.setattr(takes.Takes, "_render", lambda self, a, rid, ids: rendered.append(tuple(ids)))
    t = human.post("/api/takes/sample", json={"channel_ids": ["ch_x"]}).json()
    store = app.state.store
    row = store.q("SELECT input,result FROM agent_runs WHERE id=?", (t["id"],))[0]
    inp, res = json.loads(row["input"]), json.loads(row["result"])
    inp["style"], res["sample"] = "take", False
    base = 10.0
    said = "So we shipped the whole thing in 2 days and 69% of people said it was the best launch we ever did".split()
    res["analysis"] = {"probe": {"duration": 120},
                       "words": [{"w": w, "s": base + i * 0.4, "e": base + i * 0.4 + 0.35} for i, w in enumerate(said)]}
    c = res["clips"][0]
    c.update(status="ready", start=base, end=base + len(said) * 0.4, hook="we shipped the whole thing in 2 days",
             meta={"layout_plan": [{"scene": 0, "start": 0, "end": 4, "layout": "track", "rule": "R3"}]})
    c["text"] = " ".join(said)
    store.x("UPDATE agent_runs SET input=?, result=?, status='ready' WHERE id=?", (json.dumps(inp), json.dumps(res), t["id"]))
    return human, t["id"], c["id"], rendered, app


def _raw(app, rid, cid):
    raw = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    return next(x for x in raw["clips"] if x["id"] == cid)


def test_draft_saves_without_rendering_and_checks_every_field(draft_clip):
    human, rid, cid, rendered, app = draft_clip
    v = human.get(f"/api/takes/{rid}/clips/{cid}/draft").json()
    assert v["dirty"] is False and v["draft"]["hook"] == "we shipped the whole thing in 2 days" and v["can_render"]
    u = f"/api/takes/{rid}/clips/{cid}/draft"
    assert human.put(u, json={"draft": {"hook": "we invented this line"}}).status_code == 400   # never invents a quote
    assert human.put(u, json={"draft": {"fx": [{"kind": "stat", "text": "500 users"}]}}).status_code == 400
    assert human.put(u, json={"draft": {"colour": "red"}}).status_code == 400
    r = human.put(u, json={"draft": {"caption_preset": "cinematic", "place": {"captions": {"y": 0.5}}}, "note": "moved"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["dirty"] and d["draft"]["caption_preset"] == "cinematic" and d["log"][-1]["what"] == "moved"
    assert rendered == []                                       # a draft never renders by itself
    assert _raw(app, rid, cid).get("caption_preset") != "cinematic"
    # undo = send the earlier whole draft back; equal to what's rendered means no draft
    r = human.put(u, json={"draft": v["draft"], "merge": False}).json()
    assert r["dirty"] is False


def test_director_makes_the_number_pop_as_a_real_edit_and_rules_need_no_model(draft_clip):
    human, rid, cid, rendered, app = draft_clip
    r = human.post(f"/api/takes/{rid}/clips/{cid}/direct",
                   json={"instruction": "Make this number pop without covering my face", "ai": False})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["draft"]["fx"] == [{"kind": "stat", "text": "2 days", "nth": 0}] and out["read_by"] == "rules"
    assert out["ops"][0]["focus"]["track"] == "graphics" and "face" in out["ops"][0]["why"]
    assert out["log"][-1]["by"] == "agent" and rendered == []
    # a selection picks the words; asking for cinematic captions changes only that
    sel = {"start": 10.0 + 10 * 0.4, "end": 10.0 + 12 * 0.4 - 0.01}   # "69% of"
    out = human.post(f"/api/takes/{rid}/clips/{cid}/direct", json={"instruction": "make this pop", "selection": sel,
                                                                    "ai": False}).json()
    assert {"kind": "stat", "text": "69% of", "nth": 0} in out["draft"]["fx"]
    out = human.post(f"/api/takes/{rid}/clips/{cid}/direct", json={"instruction": "use more cinematic captions", "ai": False}).json()
    assert out["draft"]["caption_preset"] == "cinematic" and out["ops"][0]["focus"] == {"element": "captions"}
    out = human.post(f"/api/takes/{rid}/clips/{cid}/direct", json={"instruction": "add b-roll and a whoosh", "ai": False}).json()
    assert any("B-roll" in x for x in out["not_yet"]) and "Sound effects" in out["not_yet"] and not out["ops"]


def test_render_commits_the_draft_in_one_rerender_and_mcp_shares_it(draft_clip):
    from .conftest import agent_token, mcp_call
    human, rid, cid, rendered, app = draft_clip
    tok = agent_token(human, "trusted", cap=50)
    res, out = mcp_call(human, tok, "direct_clip", {"take_id": rid, "clip_id": cid, "instruction": "fewer effects",
                                                    "ai": False})
    assert not res["isError"], out
    assert out["draft"]["fx"] == []
    v = human.get(f"/api/takes/{rid}/clips/{cid}/draft").json()            # the person sees the agent's edit
    assert v["draft"]["fx"] == [] and v["log"][-1]["by"] == "agent"
    human.put(f"/api/takes/{rid}/clips/{cid}/draft", json={"draft": {"place": {"headline": {"x": 0.4, "y": 0.2}}}})
    r = human.post(f"/api/takes/{rid}/clips/{cid}/draft/render")
    assert r.status_code == 200, r.text
    assert rendered == [(cid,)]
    c = _raw(app, rid, cid)
    assert c["fx"] == [] and c["place"] == {"headline": {"x": 0.4, "y": 0.2}} and "draft" not in c
    assert human.post(f"/api/takes/{rid}/clips/{cid}/draft/render").status_code in (400, 409)  # nothing left to render


def test_chosen_motion_picks_replace_the_automatic_plan():
    from studio.media import motion
    ws = [{"w": w, "s": i * 0.5, "e": i * 0.5 + 0.4} for i, w in enumerate("it took 2 days then 2 days again".split())]
    p = motion.plan(ws, 0.0, picks=[{"kind": "stat", "text": "2 days", "nth": 1}, {"kind": "line", "text": "then", "nth": 0},
                                    {"kind": "stat", "text": "never said", "nth": 0}])
    assert [(f["kind"], f["s"]) for f in p["fx"]] == [("line", 2.0), ("stat", 2.5)] and p["chosen"]
    assert "isn't said" in p["skipped"][0]
    assert motion.plan(ws, 0.0, picks=[])["fx"] == []


def test_director_adds_and_removes_broll_and_says_so_without_a_key(draft_clip, monkeypatch):
    from studio.media import broll
    human, rid, cid, rendered, app = draft_clip
    u = f"/api/takes/{rid}/clips/{cid}/direct"
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    out = human.post(u, json={"instruction": "add b-roll of a busy kitchen", "ai": False}).json()
    assert not out["ops"] and any("Pexels key" in x for x in out["not_yet"]) and out["draft"]["broll"] == []
    monkeypatch.setenv("PEXELS_API_KEY", "k")
    hit = {"url": "https://videos.pexels.com/a.mp4", "thumb": "", "credit": "Video by Ann on Pexels", "query": "busy kitchen"}
    monkeypatch.setattr(broll, "search", lambda q, **kw: [{**hit, "query": q}])
    out = human.post(u, json={"instruction": "add b-roll of a busy kitchen", "ai": False}).json()
    b = out["draft"]["broll"]
    assert len(b) == 1 and "busy kitchen" in b[0]["query"] and out["ops"][0]["focus"]["track"] == "broll"
    assert 1.0 <= b[0]["s"] and 1.5 <= b[0]["e"] - b[0]["s"] <= 5 and rendered == []
    out = human.post(u, json={"instruction": "remove the b-roll", "ai": False}).json()
    assert out["draft"]["broll"] == []


def test_take_agent_chats_edits_drafts_and_renders_only_when_asked(draft_clip):
    human, rid, cid, rendered, app = draft_clip
    u = f"/api/takes/{rid}/chat"
    r = human.post(u, json={"message": "make the number pop in clip 1", "clip_id": cid})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["did"][0]["clip"] == cid and out["did"][0]["ops"] and "draft" in out["reply"].lower() and rendered == []
    assert human.get(f"/api/takes/{rid}/clips/{cid}/draft").json()["draft"]["fx"]
    r = human.post(u, json={"message": "what can you do?"}).json()
    assert r["did"] == [] and "B-roll" in r["reply"]
    msgs = human.get(u).json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "agent", "user", "agent"]
    out = human.post(u, json={"message": "use cinematic captions and render it", "clip_id": cid}).json()
    assert rendered and out["did"][-1]["ops"] == ["Rendering the draft on the video worker"]
    assert human.post(u, json={"message": ""}).status_code == 400


def test_director_knows_zoom_pauses_split_and_length(draft_clip):
    human, rid, cid, rendered, app = draft_clip
    u = f"/api/takes/{rid}/clips/{cid}/direct"
    d = human.post(u, json={"instruction": "no zoom, cut the pauses and make it split screen", "ai": False}).json()["draft"]
    assert d["punch_in"] is False and d["trim_pauses"] is True and d["split"] is True
    d = human.post(u, json={"instruction": "add gentle zooms and keep the pauses", "ai": False}).json()["draft"]
    assert d["punch_in"] is True and d["trim_pauses"] is False
    out = human.post(u, json={"instruction": "make it 8 seconds", "ai": False}).json()
    assert abs((out["draft"]["end"] - out["draft"]["start"]) - 8) < 0.1
    assert human.post(f"/api/takes/{rid}/clips/{cid}/draft/render").status_code in (200, 409)
