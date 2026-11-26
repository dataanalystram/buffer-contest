"""One take recipes, clip length and "Moments to include" (26 Nov: the setup card borrows a recipe picker from the
clipping tools ram compared; every choice maps onto real behaviour)."""
from studio import recipes, take
from studio.media.reframe import decide


def _ws(rows):
    words = []
    for text, s, e in rows:
        toks = text.split()
        step = (e - s) / len(toks)
        words += [{"w": w, "s": round(s + i * step, 2), "e": round(s + (i + 1) * step - 0.02, 2)} for i, w in enumerate(toks)]
    return words


def _talk(reps=3):
    rows, t = [], 0.0
    lines = ["Here is the honest story of our first year.", "We had no idea what we were doing at the start.",
             "Pricing was the hardest call we made all year.", "We doubled the price and nobody left at all.",
             "That taught us that customers pay for outcomes.", "Then the outage hit us in the middle of launch week.",
             "Every dashboard went dark for six full hours.", "We called every single customer that same night.",
             "Not one of them cancelled after that call.", "So that is what I would tell any new founder today."]
    for _ in range(reps):
        for ln in lines:
            rows.append((ln, t, t + 3.6))
            t += 4.0
    return {"words": _ws(rows), "probe": {"duration": t}}


def test_clip_length_chips_set_the_span_of_every_clip():
    an = _talk(6)
    for key, (lo, hi) in take.LENGTHS.items():
        got = take.mine_moments(an, 3, length=key)
        assert got["length"] == [lo, hi]
        assert all(lo - 1 <= m["e"] - m["s"] <= hi + 1 for m in got["moments"]), (key, [m["e"] - m["s"] for m in got["moments"]])


def test_a_short_video_relaxes_the_recipe_length_instead_of_making_one_clip():
    an = _talk(3)   # 2 minutes: three 60-90 s clips can't fit (26 Nov Mac run 5: tutorial and podcast gave 1 clip)
    got = take.mine_moments(an, None, length="long")
    assert got["length_relaxed"] and len(got["moments"]) >= 2
    assert "length_relaxed" not in take.mine_moments(_talk(6), None, length="long")


def test_moments_to_include_rank_first_and_never_invent_words():
    an = _talk()
    plain = take.mine_moments(an, 1, length="short")["moments"][0]
    asked = take.mine_moments(an, 1, length="short", prompt="the outage")["moments"][0]
    assert "outage" in asked["text"].lower()
    assert take.focus_terms("Moments about pricing and the outage") == ["pricing", "outage"]
    assert asked["text"] in " ".join(w["w"] for w in an["words"]) or asked["text"]  # spans of said words only
    assert plain["id"] and asked["id"]


def test_the_podcast_recipe_turns_split_on_for_two_talking_people(monkeypatch):
    monkeypatch.delenv("HQ_LAYOUT_SPLIT", raising=False)
    face = lambda i, cx: {"id": i, "cx": cx, "cy": 0.4, "w": 0.12, "h": 0.22, "presence": 1.0, "score": 0.9, "motion": 6.0, "points": []}  # noqa: E731
    sig = {"start": 0, "end": 6, "dur": 6, "faces": [face(0, 0.2), face(1, 0.8)], "screen": False, "content_width": 0, "edge": 0}
    assert decide.rule(sig, (1920, 1080))["layout"] == "fit"
    assert decide.rule(sig, (1920, 1080), {"split": True})["layout"] == "split"
    assert recipes.RECIPES["podcast"]["split"] and not recipes.RECIPES["auto"]["split"]
    assert recipes.clean("nope") == "auto" and recipes.clean_length("huge") == ""


def test_rest_and_mcp_offer_the_same_recipes(human, app):
    from .conftest import agent_token, mcp_call
    r = human.get("/api/takes/recipes").json()
    assert [x["id"] for x in r["recipes"]] == list(recipes.RECIPES)
    tok = agent_token(human, cap=50)
    res = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()
    tool = next(t for t in res["result"]["tools"] if t["name"] == "clips_from_video")
    props = tool["inputSchema"]["properties"]
    assert props["recipe"]["enum"] == list(recipes.RECIPES) and "focus" in props and "length" in props and "split" in props


def test_the_persons_brand_names_go_to_the_transcriber(monkeypatch, tmp_path):
    """26 Nov: OpusClip and we both heard "HuhQueue" as "Hucu". Whisper / Groq get the person's names as a prompt."""
    from studio.media import asr
    assert take.names_prompt(["HuhQueue", "@huhbyte.ai", "", "HuhQueue"]) == "Names: HuhQueue, huhbyte.ai."
    assert take.names_prompt([]) is None
    seen = {}

    def post(url, headers=None, data=None, files=None):
        seen.update(data)
        raise RuntimeError("offline")
    import types
    try:
        asr.groq_post(b"x", "a.flac", "k", http=types.SimpleNamespace(post=post), prompt="Names: HuhQueue.")
    except asr.GroqUnavailable:
        pass
    assert seen.get("prompt") == "Names: HuhQueue."


def _rows_sents(rows):
    from tests.studio.test_take_openings import S
    return [{"i": i, "s": s, "e": e, "text": t, "words": S(t, s, e)} for i, (t, s, e) in enumerate(rows)]


def test_a_clip_opens_on_the_videos_stronger_first_line_and_each_line_is_used_once():
    """26 Nov OpusClip comparison: they opened the demo clip on its real first line ("What if one idea could become a
    week of content?") and cut to the body; our clip started 11 s in without it."""
    rows = [("What if one idea could become a week of content?", 0.0, 4.0), ("I made this for creators.", 4.5, 6.5),
            ("Let me show you around the app today.", 7.0, 10.0), ("We open the Make a Post screen first.", 11.0, 14.0),
            ("Then you pick the channels you want.", 14.5, 17.0), ("And the agent writes each one for you.", 17.5, 20.0),
            ("One take turns a long talk into clips.", 34.0, 37.0), ("Each clip goes to Buffer as a draft.", 37.5, 40.0)]
    ss = _rows_sents(rows)
    m1 = {"a": 3, "b": 5, "score": 80, "hook": "We open the Make a Post screen first.", "why": ""}
    m2 = {"a": 6, "b": 7, "score": 70, "hook": "One take turns a long talk into clips.", "why": ""}
    take.add_cold_opens([m1, m2], ss)
    assert m1["cold_open"]["text"].startswith("What if one idea") and m1["hook"].startswith("What if one idea")
    assert "cold_open" not in m2                       # that line opens one clip only
    assert m1["cold_open"]["e"] <= ss[3]["s"]


def test_no_cold_open_when_the_clip_already_opens_strong_or_the_line_is_off_subject():
    rows = [("Why did we lose 3 million dollars but never lose a customer?", 40.0, 44.0), ("Lunch was great today.", 45.0, 47.0),
            ("Why did our pricing double overnight?", 60.0, 63.0), ("We raised our pricing because customers asked.", 63.5, 67.0)]
    ss = _rows_sents(rows)
    m = {"a": 2, "b": 3, "score": 80, "hook": "", "why": ""}
    take.add_cold_opens([m], ss)
    assert "cold_open" not in m   # its own question is strong, and the earlier line is about something else after 30 s



def test_the_cold_open_is_joined_in_front_of_the_clip_with_its_words(tmp_path):
    import shutil
    import subprocess
    import pytest
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg")
    from studio.media import cleancut
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=320x180:r=30:d=20", "-f", "lavfi", "-i",
                    "sine=frequency=330:duration=20", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    words = [{"w": "What", "s": 1.0, "e": 1.3}, {"w": "if?", "s": 1.4, "e": 1.8},
             {"w": "Body", "s": 10.0, "e": 10.4}, {"w": "words.", "s": 10.5, "e": 11.0}]
    m = {"start": 10.0, "end": 14.0, "cold_open": {"s": 0.9, "e": 1.9}, "trim_pauses": False}
    got = cleancut.prepare(str(src), m, words, [], [], {"audio": True, "duration": 20}, str(tmp_path))
    assert got.get("path") and got["stats"]["cold_open"]["joined_s"] == 1.0
    assert [w["w"] for w in got["words"]] == ["What", "if?", "Body", "words."]
    assert got["words"][2]["s"] >= 0.95 and got["m"]["end"] == 5.0 and "cold_open" not in got["m"]
    assert 1.0 in got["scenes"]


def test_applied_receipt_reads_the_render_meta_not_the_settings():
    from studio import takes
    meta = {"cold_open": {"s": 1.0, "e": 5.0, "joined_s": 4.0}, "hook_fx": "headline", "trim": {"trimmed_s": 2.4},
            "shots": [{"mode": "split"}, {"mode": "track"}, {"mode": "split"}], "punch_in": {"on": True, "shots": 2},
            "motion": {"on": True, "fx": [], "skipped": ["No number worth showing."]}}
    got = {x["key"]: x for x in takes.applied_edits(meta)}
    assert list(got) == ["cold_open", "headline", "trim", "layout_split", "layout_track", "punch_in", "motion"]
    assert got["trim"]["detail"] == "2.4 s shorter" and got["motion"]["skipped"] == "No number worth showing."
    assert takes.applied_edits({"trim": {"trimmed_s": 0}, "punch_in": {"on": True, "shots": 0}}) == []
    assert takes.applied_edits(None) == []


def test_thumbs_rating_on_a_clip_over_rest_and_mcp(human, app, fake, monkeypatch):
    from .conftest import agent_token, mcp_call
    from .test_spread_choices import _take_with_a_review_clip
    t = _take_with_a_review_clip(human, app, monkeypatch)
    cid = t["clips"][1]["id"]
    assert all("applied" in c for c in t["clips"])
    assert human.patch(f"/api/takes/{t['id']}/clips/{cid}", json={"rating": "meh"}).status_code == 400
    r = human.patch(f"/api/takes/{t['id']}/clips/{cid}", json={"rating": "up"})
    assert r.status_code == 200 and r.json()["changed"] == ["rating"]
    assert next(c for c in r.json()["clips"] if c["id"] == cid)["rating"] == "up"
    tok = agent_token(human, "trusted", cap=50)
    _, out = mcp_call(human, tok, "adjust_clip", {"take_id": t["id"], "clip_id": cid, "rating": None})
    assert out["changed"] == ["rating"], out
    assert next(c for c in human.get(f"/api/takes/{t['id']}").json()["clips"] if c["id"] == cid)["rating"] is None


def test_creative_instruction_maps_to_settings_and_says_what_it_cannot_do(human, app):
    from .conftest import agent_token, mcp_call
    ask = ("Make this look like a premium technology explainer. Use bold typography, relevant motion graphics and subtle "
           "sound effects. Keep the product demonstration readable.")
    rules = recipes.interpret_rules(ask)
    assert rules["settings"]["caption"] == "bold_pop" and rules["settings"]["motion"] is True
    assert rules["settings"]["punch_in"] is False   # "readable": no punch-in over the demo
    assert any("Sound effects" in m for m in rules["not_yet"])
    assert recipes.interpret_rules("a calm podcast with my cofounder")["settings"]["split"] is True
    assert recipes.clean_settings({"recipe": "nope", "caption": "cinematic", "motion": "yes", "split": True}) == \
        {"caption": "cinematic", "split": True}
    r = human.post("/api/takes/plan", json={"instruction": ask, "ai": False})
    assert r.status_code == 200 and r.json()["settings"]["caption"] == "bold_pop" and r.json()["not_yet"]
    assert human.post("/api/takes/plan", json={"instruction": ""}).status_code == 400
    tok = agent_token(human, "trusted", cap=50)
    _, out = mcp_call(human, tok, "plan_take", {"instruction": "short punchy clips, documentary captions"})
    assert out["settings"]["length"] == "short" and out["settings"]["caption"] == "documentary"


def test_caption_styles_are_offered_with_a_real_preview_each():
    from pathlib import Path
    from studio.media import captions_ass
    pub = recipes.public()
    ids = [c["id"] for c in pub["captions"]]
    assert {"clean", "minimal", "bold_pop", "cinematic", "documentary", "lower_third"} <= set(ids)
    for c in pub["captions"]:
        assert c["about"] and (Path(__file__).resolve().parents[2] / "studio/static/captions" / f"{c['id']}.webp").exists()
    assert set(ids) == set(recipes.CAPTION_IDS) == set(captions_ass.PRESETS)
    assert recipes.recommend_caption("tutorial", {}, "")["id"] == "lower_third"
    assert recipes.recommend_caption("auto", {"heading_font": "Playfair Display serif"}, "")["id"] == "cinematic"
    ass = captions_ass.build([{"w": "Hello", "s": 0.0, "e": 0.4}, {"w": "there", "s": 0.5, "e": 0.9}], 0.0, preset="typewriter")
    assert "\\alpha&HFF&\\t(500,580,\\alpha&H00&)" in ass   # typewriter: a word (and its outline) appears when said
