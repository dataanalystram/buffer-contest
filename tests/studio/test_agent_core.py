"""The agent core end to end: the owner's exact prompt, recorded fixtures, no live calls.

Bugs this pins (docs/AGENT_PIPELINE.md): the 120-character cut, the pasted link replaced by a search, the canned Reel,
clipped text, the hang after the video, duplicate drafts, and a figure picked between two sources without asking.
"""
import io
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import agent_tools, brand, factcheck, hbstyle, house, intake, reader, render, sources

from . import conftest
from .conftest import FakeBuffer, connect

FIX = Path(__file__).parent / "fixtures"
PROMPT = ("i want to post about this in all my channels https://x.com/AarushYadav47/status/2116198463847408066 and here you also have "
          "to make it a short punchy explainer, and end every video with Follow @huhbyte.ai")
TWEET = "https://x.com/AarushYadav47/status/2116198463847408066"
OWNER_CHANNELS = [
    {"id": "ch_li", "name": "HughByte AI", "displayName": "HughByte AI", "service": "linkedin", "avatar": "", "isDisconnected": False, "isQueuePaused": False},
    {"id": "ch_fb", "name": "HuhByte.AI", "displayName": "HuhByte.AI", "service": "facebook", "avatar": "", "isDisconnected": False, "isQueuePaused": False},
    {"id": "ch_yt", "name": "Huhbyte", "displayName": "Huhbyte", "service": "youtube", "avatar": "", "isDisconnected": False, "isQueuePaused": False},
    {"id": "ch_ig", "name": "huhbyte.ai", "displayName": "huhbyte.ai", "service": "instagram", "avatar": "", "isDisconnected": False, "isQueuePaused": False},
]


def jpeg(w=1200, h=675, color=(40, 80, 160)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


class Router:
    """One transport for the whole app: Buffer -> FakeBuffer; X oEmbed -> the recorded answer; everything else 404."""
    def __init__(self, fake):
        self.fake, self.calls = fake, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(str(req.url))
        if req.url.host in ("api.buffer.com", "auth.buffer.com"):
            return self.fake(req)
        if req.url.host == "publish.x.com":
            return httpx.Response(200, json=json.loads((FIX / "x_oembed_aarush.json").read_text()))
        return httpx.Response(404, text="not in the fixture")


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = FakeBuffer()
    router = Router(fake)
    from studio.app import create_app
    from studio.config import load_settings
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="s" * 64,
                      db_path=str(tmp_path / "a.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True)
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(router)))
    app.state.studio.media_loader = lambda url: jpeg()
    h = app.state.house
    h.sync, h.worker_mode = True, "local"
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)
    # a second outlet that reports the round differently (the msn.com case from live QA)
    monkeypatch.setattr(house.House, "_corroborate", lambda self, article: [
        {"title": "Subvocal raises $13 million for glasses that read silent speech", "url": "https://www.msn.com/subvocal", "source": "msn.com"}])
    monkeypatch.setattr(reader, "read_article", lambda url, http=None: {"url": url, "title": "", "description": "", "paragraphs": []})
    searched = []
    monkeypatch.setattr(sources, "search", lambda *a, **k: searched.append(a) or [])
    c = TestClient(app, base_url="https://hq.test")
    c.headers["X-HQ-CSRF"] = connect(c)
    c.searched, c.fake, c.router = searched, fake, router
    return c


def test_intake_reads_the_whole_prompt():
    long = PROMPT + " " + "context " * 400
    p = intake.parse(long, OWNER_CHANNELS)
    assert len(p["prompt"]) <= intake.MAX_PROMPT and len(long) > 3000
    assert p["source_url"] == TWEET and p["kind"] == "link"
    assert p["channel_ids"] == [c["id"] for c in OWNER_CHANNELS] and "all" in p["channels_why"]
    assert p["tone"] in ("punchy", "explainer") and p["rules"] == ["end every video with Follow @huhbyte.ai"] or \
        any("Follow @huhbyte.ai" in r for r in p["rules"])
    p2 = intake.parse("make a carousel just for LinkedIn and Instagram about x.com/a/status/123456", OWNER_CHANNELS)
    assert p2["format"] == "carousel" and set(p2["channel_ids"]) == {"ch_li", "ch_ig"} and p2["source_url"].startswith("https://x.com/")


def test_oembed_parse():
    post = reader.parse_oembed(json.loads((FIX / "x_oembed_aarush.json").read_text()))
    assert post["handle"] == "AarushYadav47" and post["author_name"] == "Aarush Yadav"
    assert post["text"].startswith("We’ve raised $1.3M from YC") and "pic.twitter.com" not in post["text"] and post["has_media"]


def test_factcheck_flags_a_conflict_and_ignores_agreement():
    c = factcheck.conflicts({"source": "X @a", "text": "We raised $1.3M from YC"}, [{"source": "msn.com", "text": "raises $13 million"}])
    assert c and c[0]["ours"] == "$1.3M" and "Which is right" in c[0]["question"]
    assert not factcheck.conflicts({"source": "X", "text": "raised $1.3M"}, [{"source": "TC", "text": "a $1.3 million seed round"}])


def test_end_card_never_clips_long_cta():
    kit = dict(brand.DEFAULT_KIT, handle="huhbyte.ai", end_card={"on": True, "cta": "Follow for daily AI news explained", "line": "AI news, explained simply."})
    p = brand.visual_params(kit, [])
    p["headline_case"] = "upper"
    for ratio in ("4:5", "9:16"):
        data, meta = render.end_card(kit, dict(p), ratio=ratio)
        assert not hbstyle.overflow(meta, (meta["width"], meta["height"]), margin=40), meta["text_boxes"]
        # pixel proof: nothing but the ground colour in the outer 40 px on the left and right
        im = Image.open(io.BytesIO(data)).convert("L")
        w, h = im.size
        for x in list(range(0, 40)) + list(range(w - 40, w)):
            col = [im.getpixel((x, y)) for y in range(0, h, 7)]
            assert max(col) < 60, f"ink at x={x} ({ratio})"


def test_cover_headline_fits_for_a_very_long_headline():
    kit = dict(brand.DEFAULT_KIT, handle="huhbyte.ai")
    p = brand.visual_params(kit, [])
    hd = "Subvocal raises $1.3M from YC and Afore Capital for glasses that read your internal speech muscles so you never type again " * 2
    for ratio in ("4:5", "9:16"):
        _, meta = render.news_card(hd, kit, dict(p), image=jpeg(), ratio=ratio, label="Launch", credit="X @AarushYadav47", n=(1, 4))
        assert not hbstyle.overflow(meta, (meta["width"], meta["height"]))


def test_exact_prompt_end_to_end(owner):
    r = owner.post("/api/agent/runs", json={"prompt": PROMPT, "channel_ids": ["ch_li"], "format": "auto"}).json()
    assert r["kind"] == "agent", r
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    res = run["result"]
    assert run["status"] == "ready", run["steps"]
    # the prompt was read whole, and its link is the source: no search happened
    assert run["input"]["prompt"] == PROMPT and owner.searched == []
    assert any("publish.x.com/oembed" in u for u in owner.router.calls)
    assert res["source"]["url"] == TWEET and res["story"]["url"] == TWEET
    assert res["checks"]["about_source"] is True
    # "all my channels" beats the one chip that was selected
    # NO VIDEO -> NO REEL (owner, 1 Nov 2026): the post has no video, so YouTube is skipped with a note, never a slideshow
    assert {p["service"] for p in res["packages"]} == {"linkedin", "facebook", "instagram"}
    assert [x["service"] for x in res["checks"]["skipped"]] == ["youtube"] and res["channel_media"]["ch_yt"]["format"] == "skipped"
    assert any("no video found" in p.lower() for p in res["plan"])
    # the standing rule went to the ledger and drives the end card
    rules = owner.get("/api/brand").json()["rules"]
    assert any(x["check"].get("cta") == "Follow @huhbyte.ai" for x in rules)
    # every channel has real media, nothing clipped; no video was made from text (no slideshow job)
    for p in res["packages"]:
        assert p["media_urls"], p
        assert not p["clipped"], p
    assert not owner.app.state.store.q("SELECT id FROM render_jobs")
    ig = next(p for p in res["packages"] if p["service"] == "instagram")
    assert ig["format"] == "carousel" and len(ig["media_urls"]) == 4  # cover, the post card, facts, end card
    # the headline on the cover is about THAT post
    meta = owner.app.state.house.media_meta(house.Actor(owner.app.state.store.q("SELECT user_id FROM agent_runs")[0]["user_id"]),
                                            ig["media_urls"][0].split("/m/")[1])
    assert "1.3M" in meta["headline"] or "SUBVOCAL" in meta["headline"].upper() or "RAISED" in meta["headline"].upper(), meta["headline"]
    # tone applied
    assert all(res["captions"][p["channel_id"]]["tone"] in ("punchy", "explainer") for p in res["packages"])
    # the msn.com figure disagrees: packages are held and the agent asks, never guesses
    assert res["conflicts"] and all(p["status"] == "hold" for p in res["packages"])
    assert "Which is right" in res["thread"][-1]["text"] or any("Which is right" in t["text"] for t in res["thread"])
    blocked = owner.post(f"/api/agent/runs/{r['id']}/tools/save_buffer_drafts", json={})
    assert blocked.status_code == 409 and not owner.fake.created
    # the person answers in the thread -> resolved, re-rendered, then drafts once (idempotent)
    owner.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "it's $1.3M"})
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["status"] == "ready" and not run["result"]["conflicts"], run["result"]["thread"][-2:]
    out = owner.post(f"/api/agent/runs/{r['id']}/tools/save_buffer_drafts", json={"channel_ids": ["ch_li", "ch_ig"]}).json()
    assert len(owner.fake.created) == 2 and all(x.get("saveToDraft") for x in owner.fake.created)
    again = owner.post(f"/api/agent/runs/{r['id']}/tools/save_buffer_drafts", json={"channel_ids": ["ch_li", "ch_ig"]}).json()
    assert len(owner.fake.created) == 2 and {d["status"] for d in again["drafts"]} == {"already saved"}
    assert out["drafts"]


def test_refine_one_channel_only(owner):
    r = owner.post("/api/agent/runs", json={"prompt": f"post this everywhere {TWEET}"}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()

    def firsts(cm):
        return {cid: (v["media"][0]["url"] if v["media"] else v["format"]) for cid, v in cm.items()}
    before = firsts(run["result"]["channel_media"])
    owner.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "it's $1.3M"})
    owner.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "redo YouTube at 20s"})
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    after = firsts(run["result"]["channel_media"])
    # no video in this post: "redo YouTube at 20s" still makes no Reel, YouTube stays skipped with the note
    assert run["result"]["channel_media"]["ch_yt"]["format"] == "skipped" and "needs a video" in run["result"]["channel_media"]["ch_yt"]["note"]
    # LinkedIn's carousel didn't change when only YouTube was asked for... (the figure answer re-rendered all once)
    owner.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "make LinkedIn more formal"})
    run2 = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run2["result"]["captions"]["ch_li"]["tone"] == "formal"
    assert run2["result"]["captions"]["ch_fb"]["tone"] != "formal"
    assert after["ch_li"] == firsts(run2["result"]["channel_media"])["ch_li"]
    assert before.keys() == after.keys()


def test_brand_edit_and_mcp_parity(owner):
    out = owner.post("/api/brand/edit", json={"instruction": "use #FF5A1F and make the logo smaller"}).json()
    assert any("#FF5A1F" in c for c in out["changed"]) and any("Logo" in c for c in out["changed"])
    assert owner.get("/api/brand").json()["kit"]["colors"]["accent"] == "#FF5A1F"
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    from studio import mcp
    names = {t["name"] for t in mcp.TOOLS}
    assert {"make_post", "reply_post", "edit_brand", "get_onboarding", "set_onboarding", "get_recommendations", "post_read_url",
            "post_render_reel", "post_write_captions", "post_save_buffer_drafts", "post_check"} <= names
    res, run = conftest.mcp_call(owner, tok, "make_post", {"prompt": f"post this {TWEET}", "brain": "external"})
    assert not res["isError"] and run["kind"] == "agent"
    res, got = conftest.mcp_call(owner, tok, "post_read_url", {"run_id": run["id"], "url": TWEET})
    assert not res["isError"] and got["handle"] == "AarushYadav47"


def test_stale_run_is_reported_not_spinning(owner):
    st = owner.app.state.store
    uid = st.q("SELECT id FROM users")[0]["id"]
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               (uid, "news", json.dumps({"channels": []}), "rendering",
                json.dumps([{"key": "caption", "label": "Write captions", "status": "running", "detail": ""}]), "{}", time.time() - 5000, time.time() - 5000))
    run = owner.get(f"/api/agent/runs/{rid}").json()
    assert run["status"] == "failed" and run["steps"][0]["status"] == "failed"


def test_tidy_keeps_money_and_plain_hyphens():
    st = {"facts": ["Raised $1.3M from YC and Afore Capital"]}
    assert agent_tools.tidy("A noise\u2011free option, 1.3M YC-backed", st) == "A noise-free option, $1.3M YC-backed"


# ---------------------------------------------------------------- the Claude brain (tool use), scripted
def test_claude_brain_tool_loop_budget_and_workspace_header(owner, monkeypatch):
    from studio import brain_llm, budget
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "1")
    brain_llm._models.update(at=0.0, plan="", fast="")
    seen = {"headers": [], "tool_turns": 0, "fast": 0, "models": 0}
    script = [
        ("1. Read the post\n2. Facts\n3. Render\n4. Captions\n5. Check", [("read_url", {"url": TWEET})]),
        ("", [("extract_facts", {})]),
        ("", [("render_carousel", {"channel_ids": ["ch_li", "ch_fb", "ch_ig"]}), ("render_reel", {"channel_ids": ["ch_yt"]})]),
        ("", [("write_captions", {"tone": "punchy"})]),
        ("", [("check", {})]),
        ("", [("finish", {"message": "Four posts ready."})]),
    ]
    inner = owner.router

    def anthropic(req):
        if req.url.host != "api.anthropic.com":
            return inner(req)
        seen["headers"].append(dict(req.headers))
        if req.url.path.endswith("/models"):
            seen["models"] += 1
            return httpx.Response(200, json={"data": [{"id": "claude-haiku-4-5-20251001", "created_at": "2025-10-01T00:00:00Z"},
                                                      {"id": "claude-sonnet-4-5-20250929", "created_at": "2025-09-29T00:00:00Z"},
                                                      {"id": "claude-3-5-sonnet-20240620", "created_at": "2024-06-20T00:00:00Z"}]})
        body = json.loads(req.content)
        usage = {"input_tokens": 1200, "output_tokens": 300, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 800}
        if "tools" not in body:  # a fast step: facts or captions
            seen["fast"] += 1
            assert body["model"] == "claude-haiku-4-5-20251001"
            txt = body["messages"][0]["content"]
            if "networks" in txt:
                out = {s: f"Subvocal raised $1.3M to build glasses that read silent speech. #{s}" for s in ("linkedin", "facebook", "youtube", "instagram")}
            else:
                out = {"headline": "Subvocal raises $1.3M for silent-speech glasses", "facts": ["Raised $1.3M from YC and Afore Capital",
                       "Glasses read internal speech muscles", "No typing, no talking"], "why_it_matters": "Typing without a sound.", "label": "Launch"}
            return httpx.Response(200, json={"content": [{"type": "text", "text": json.dumps(out)}], "usage": usage, "model": body["model"]})
        assert body["model"] == "claude-sonnet-4-5-20250929"
        assert body["system"][0]["cache_control"] == {"type": "ephemeral"} and body["tools"][-1]["cache_control"]
        text, calls = script[min(seen["tool_turns"], len(script) - 1)]
        seen["tool_turns"] += 1
        content = ([{"type": "text", "text": text}] if text else []) + [
            {"type": "tool_use", "id": f"tu{seen['tool_turns']}_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
        return httpx.Response(200, json={"content": content, "usage": usage, "model": body["model"], "stop_reason": "tool_use"})

    owner.app.state.house.http = httpx.Client(transport=httpx.MockTransport(anthropic))
    owner.app.state.studio.buffer.http = owner.app.state.house.http
    r = owner.post("/api/agent/runs", json={"prompt": PROMPT}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    res = run["result"]
    assert run["status"] == "ready", run["steps"]
    assert res["brain"]["mode"] == "tools" and res["plan"][0] == "Read the post"
    assert [s["tool"] for s in run["steps"]][:5] == ["read_url", "extract_facts", "render_carousel", "render_reel", "write_captions"]
    assert res["story"]["engine"] == "claude" and seen["fast"] >= 2
    assert all(h.get("anthropic-workspace-id") == "wrkspc_test" for h in seen["headers"])
    assert {p["service"] for p in res["packages"]} == {"linkedin", "facebook", "instagram"}  # no video: YouTube skipped
    assert res["captions"]["ch_li"]["engine"] == "claude" and "$1.3M" in res["captions"]["ch_li"]["text"]
    uid = owner.app.state.store.q("SELECT user_id FROM agent_runs")[0]["user_id"]
    left = budget.remaining(owner.app.state.store, uid)
    assert 0 < left["spent_usd"] < 0.2 and left["left_usd"] < 1
    rows = owner.app.state.store.q("SELECT provider, model, cost_usd FROM audit WHERE action='ai_call'")
    assert rows and all(r["cost_usd"] is not None for r in rows)
    # over budget -> refused before the call, the standard plan runs instead, and the UI note says why
    owner.app.state.store.x("UPDATE brain_budget SET spent=5 WHERE scope=?", (f"user:{uid}",))
    calls_before = seen["tool_turns"]
    r2 = owner.post("/api/agent/runs", json={"prompt": f"post this on linkedin {TWEET}"}).json()
    run2 = owner.get(f"/api/agent/runs/{r2['id']}").json()
    assert run2["status"] == "ready" and seen["tool_turns"] == calls_before
    assert run2["result"]["brain"]["mode"] == "plan" and any("used up" in n for n in run2["result"]["notes"])
