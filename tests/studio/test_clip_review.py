"""The editor's notes on a rendered One take clip (studio/media/review.py): read only from what the render measured."""
from studio.media import review


def test_a_clean_clip_gets_what_works_and_no_invented_problems():
    clip = {"hook": "We shipped the whole thing in 2 days.", "transcript": "We shipped the whole thing in 2 days. Nobody believed us.",
            "duration": 32.0, "face_coverage": 0.97, "trim": {"trimmed_s": 1.4},
            "layout_plan": [{"start": 0, "end": 30, "layout": "track", "rule": "R3"}]}
    r = review.review(clip, {"hook_fx": {"on": True, "text": "SHIPPED"}})
    assert not r["issues"] and len(r["works"]) >= 4 and "framing" in r["checked"]


def test_real_problems_come_with_why_and_a_specific_fix():
    clip = {"hook": "Seriously, we tripled revenue.",
            "transcript": "It was a strange year for us and the whole team. We kept going and we never stopped trying "
                          "new things. Seriously, we tripled revenue and",
            "duration": 80.0, "face_coverage": 0.6, "watermarks": ["@someone"],
            "layout_plan": [{"start": 0, "end": 40, "layout": "track", "rule": "R3"}, {"start": 40, "end": 80, "layout": "fit", "rule": "R4"}]}
    r = review.review(clip)
    whats = " ".join(i["what"] for i in r["issues"])
    assert "s in, not at the start" in whats and "mid-sentence" in whats and "opens on \"it\"" in whats
    assert "80 s" in whats and "small" in whats and "@someone" in whats
    assert all(i["why"] and i["fix"] for i in r["issues"])


def test_nothing_measured_nothing_claimed():
    assert review.review({}) == {"works": [], "issues": [], "checked": []}


def test_the_take_view_and_mcp_carry_the_notes(human, app, monkeypatch):
    import json
    from .conftest import agent_token, mcp_call
    from studio import takes
    monkeypatch.setattr(takes.Takes, "_render_sample", lambda self, a, rid, c: None)
    t = human.post("/api/takes/sample", json={"channel_ids": ["ch_x"]}).json()
    store = app.state.store
    row = store.q("SELECT input,result FROM agent_runs WHERE id=?", (t["id"],))[0]
    inp, res = json.loads(row["input"]), json.loads(row["result"])
    inp["style"], res["sample"] = "take", False
    for c in res["clips"]:
        c.update(status="ready", url="https://example.test/c.mp4", hook="We shipped it in two days.",
                 meta={"layout_plan": [{"start": 0, "end": 30, "layout": "track", "rule": "R3"}], "face_coverage": 0.95})
    store.x("UPDATE agent_runs SET input=?, result=? WHERE id=?", (json.dumps(inp), json.dumps(res), t["id"]))
    v = human.get(f"/api/takes/{t['id']}").json()
    r = v["clips"][0]["review"]
    assert r and "framing" in r["checked"] and any("in frame" in w for w in r["works"])
    tok = agent_token(human, cap=50)
    out = mcp_call(human, tok, "list_clips", {"take_id": t["id"]})[1]
    assert out["clips"][0]["review"] == r


def test_two_people_in_a_wide_shot_get_the_real_cause_and_split_not_track():
    """26 Nov Mac run (third): on the Waid podcast the notes said "too small" and suggested Track, which would crop one
    of the two men out."""
    clip = {"hook": "Here's the thing.", "transcript": "Here's the thing. We all saw it coming.", "duration": 30.0,
            "layout_plan": [{"start": 0, "end": 5.2, "layout": "fit", "rule": "R8"}, {"start": 5.2, "end": 30, "layout": "track", "rule": "R7"}]}
    r = review.review(clip)
    two = [i for i in r["issues"] if "wide shot stays full frame" in i["what"]]
    assert two and "Split" in two[0]["fix"] and not any("Pin that scene to Track" in i["fix"] for i in r["issues"])


def test_the_brands_own_handle_is_not_someone_elses_watermark():
    from studio.media import ocr
    assert ocr.watermarks("follow @huhbyte.ai for more", "huhbyteai") == []
    assert ocr.watermarks("follow @someone for more", "huhbyteai") == ["@someone"]


def test_a_connected_channel_name_is_the_persons_own_handle_too():
    from studio.media import ocr
    assert ocr.watermarks("by @huhbyte.ai", ["", "HuhByte AI", "huhbyte.ai"]) == []
