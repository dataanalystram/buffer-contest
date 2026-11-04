"""Client brain over MCP (owner, 1 Nov 2026): the client's model is the brain, HuhQueue spends none of its AI key.

An MCP run without server_ai makes zero Anthropic calls (checked on the wire and on the budget ledger); server-AI tools
answer with the client-brain sequence instead; the ledger records source=mcp_client_brain at $0; the video tools return a
job id that render_status polls.
"""
import json

import httpx
import pytest

from studio import brain_llm, budget, house, reader

from .conftest import agent_token, image_bytes, mcp_call


@pytest.fixture
def no_anthropic(monkeypatch):
    """The operator key is set; any request to api.anthropic.com is recorded and refused."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-operator-key-000000")
    seen = []
    real_send = httpx.Client.send

    def send(self, request, *a, **k):
        if request.url.host == "api.anthropic.com":
            seen.append(str(request.url))
            return httpx.Response(500, json={"error": "test: no Anthropic calls allowed"})
        return real_send(self, request, *a, **k)
    monkeypatch.setattr(httpx.Client, "send", send)
    monkeypatch.setattr(brain_llm, "resolve_models", lambda *a, **k: {"plan": "claude-sonnet-x", "fast": "claude-haiku-x", "all": []})
    return seen


@pytest.fixture
def page(monkeypatch):
    art = {"url": "https://news.example.com/ai-launch", "title": "Acme launches a card that pays 4% back on AI tools",
           "description": "The new card pays 4% back on AI subscriptions.", "image": "https://news.example.com/hero.jpg",
           "site": "Example News", "via": "page",
           "paragraphs": ["Acme said the card pays 4% back on AI tools and 1.5% on everything else.",
                          "It costs $0 a year and launches in 12 countries on Monday."], "media": []}
    monkeypatch.setattr(reader, "read_url", lambda url, *a, **k: dict(art, url=url))
    from studio import netguard
    monkeypatch.setattr(netguard, "check_url", lambda url, *a, **k: url)  # no DNS in tests
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: image_bytes(1200, 800, "JPEG") if url else None)
    return art


def _list(client, token):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return {t["name"]: t for t in r.json()["result"]["tools"]}


def test_tool_list_has_the_hands_and_marks_server_ai(human):
    tools = _list(human, agent_token(human))
    for n in ("research_link", "read_x_post", "find_media", "scrape_brand_assets", "render_carousel", "hero_edit", "edit_video",
              "render_reel", "split_video", "render_status", "create_package", "update_brand_kit", "library_upload_url",
              "integrations_status", "weekly_recap_plan", "list_library", "delete_library_item", "get_brand", "brand_check",
              "list_templates", "render_template", "list_themes", "schedule_package", "list_clips", "adjust_clip"):
        assert n in tools, n
    assert "CAN edit" not in tools["edit_video"]["description"]  # said in the instructions; the tool says what it does
    assert tools["edit_video"]["description"].startswith("Edit or cut a video on the HuhQueue server")
    for n in ("run_agent", "revise", "edit_media", "make_post"):
        assert "Uses HuhQueue's server AI (counts against your HuhQueue AI allowance)" in tools[n]["description"], n
        assert "server_ai" in tools[n]["inputSchema"]["properties"]
    assert all(len(t["description"]) > 40 for t in tools.values())


def test_initialize_tells_the_client_it_is_the_brain(human):
    tok = agent_token(human)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                   "params": {"protocolVersion": "2025-06-18", "capabilities": {"sampling": {}}, "clientInfo": {"name": "claude-ai"}}})
    ins = r.json()["result"]["instructions"]
    assert ins.startswith("You are the brain; HuhQueue is the hands.")
    assert "You CAN edit videos with HuhQueue" in ins
    # one request = one run (4 Nov 2026): start_post, run_id everywhere, ask before new posts, paid tools wait for approval
    for want in ("ONE post per request", "start_post", "Pass run_id", "unless the person asked", "needs_user_approval",
                 "your own web research"):
        assert want in ins, want


def test_server_ai_tool_without_flag_returns_the_sequence_and_spends_nothing(human, no_anthropic):
    tok = agent_token(human)
    for name, args in (("run_agent", {"url": "https://news.example.com/ai-launch"}), ("make_post", {"prompt": "post about this"}),
                       ("edit_media", {"media_url": "https://cdn.example.com/a.mp4"}), ("revise", {"run_id": 1, "correction": "x"})):
        res, out = mcp_call(human, tok, name, args)
        assert not res["isError"] and out["server_ai_required"] is True and out["spent_usd"] == 0, name
        assert "client_brain_sequence" in out
    assert no_anthropic == []
    assert not human.app.state.store.q("SELECT * FROM agent_runs")


def test_client_brain_run_makes_zero_anthropic_calls(human, no_anthropic, page):
    store = human.app.state.store
    tok = agent_token(human, policy="draft", cap=50)
    res, art = mcp_call(human, tok, "research_link", {"url": "https://news.example.com/ai-launch"})
    assert not res["isError"], art
    assert art["title"].startswith("Acme") and any("4%" in f["fact"] for f in art["figures"])
    assert all(f["source_url"] == "https://news.example.com/ai-launch" for f in art["figures"])
    slides = [{"eyebrow": "FINTECH · AI", "headline": "This Card Pays You Back For AI", "accent_word": "AI",
               "body": "4% back on AI tools.", "image": art["images"][0]},
              {"eyebrow": "THE NUMBERS", "headline": "4% On AI, 1.5% Elsewhere", "body": "**4%** on AI tools, **1.5%** on the rest.",
               "footnote": "Source: Example News"},
              {"eyebrow": "WHEN", "headline": "Twelve Countries On Monday", "body": "It costs **$0** a year."}]
    res, car = mcp_call(human, tok, "render_carousel", {"slides": slides})
    assert not res["isError"], car
    assert car["slides"] >= 4 and len(car["media_urls"]) == car["slides"]  # cover + 2 ideas + Follow card
    res, chk = mcp_call(human, tok, "brand_check", {"text": "This card pays 4% back on AI tools. Follow for more."})
    assert not res["isError"]
    res, pk = mcp_call(human, tok, "create_package", {"captions": {"instagram": "This card pays 4% back on AI tools.",
                                                                    "ch_li": "Acme's new card: 4% back on AI subscriptions."},
                                                       "media_urls": car["media_urls"], "title": "Acme AI card"})
    assert not res["isError"], pk
    assert {p["channel_id"] for p in pk["packages"]} == {"ch_ig", "ch_li"} and pk["spent_usd"] == 0
    # the person's own brain-swap rule: nothing hit Anthropic, nothing was charged
    assert no_anthropic == []
    budget.ensure(store)
    assert budget._spent(store, "global") == 0.0
    uid = store.q("SELECT user_id FROM agent_tokens")[0]["user_id"]
    assert budget._spent(store, f"user:{uid}") == 0.0
    from studio import mcp_client_brain as cb
    led = cb.ledger(store, uid)
    assert led["source"] == "mcp_client_brain" and led["calls"] >= 3 and led["spent_usd"] == 0.0
    rows = [json.loads(r["detail"]) for r in store.q("SELECT detail FROM audit WHERE action='ai_call'")]
    assert rows and all(r.get("source") == "mcp_client_brain" and r.get("cost_usd") == 0 for r in rows)


def test_engines_drop_the_operator_key_while_the_client_is_the_brain(human, no_anthropic):
    store, s = human.app.state.store, human.app.state.house.s
    assert any(e["source"] == "huhqueue claude" for e in brain_llm.engines(store, "u1", s))
    tok = brain_llm.client_brain(True)
    try:
        assert not any(e["source"] == "huhqueue claude" for e in brain_llm.engines(store, "u1", s))
        eng = {"source": "huhqueue claude", "budgeted": True, "model": "claude-sonnet-x", "fast_model": "claude-haiku-x",
               "style": "anthropic", "provider": "anthropic", "base_url": brain_llm.ANTHROPIC, "api_key": "k"}
        with pytest.raises(budget.BudgetExceeded):
            brain_llm.call(eng, store, "u1", "agent:x", system="s", messages=[{"role": "user", "content": "hi"}])
    finally:
        brain_llm.client_brain_end(tok)
    assert no_anthropic == []


def test_edit_video_returns_a_job_and_render_status_polls_it(human, monkeypatch, no_anthropic):
    h = human.app.state.house
    tok = agent_token(human, cap=50)
    uid = human.app.state.store.q("SELECT user_id FROM agent_tokens")[0]["user_id"]
    from studio.actions import Actor
    vid = h.put_media(Actor(uid), b"\x00\x00\x00\x18ftypmp42" + b"0" * 64, "talk.mp4", kind="upload", meta={"type": "video", "duration": 95})
    sent = []
    monkeypatch.setattr(house.House, "video_mode", lambda self: "github")
    monkeypatch.setattr(house.House, "_dispatch_github", lambda self, jid, **k: (sent.append(jid) or True, "GitHub answered 204"))
    res, job = mcp_call(human, tok, "edit_video", {"media_token": vid, "cut_first_s": 20, "captions": True})
    assert not res["isError"], job
    assert job["status"] == "queued" and sent == [job["job_id"]] and job["edit"]["clip"] == [20.0, 95.0] and job["edit"]["captions"]
    spec = json.loads(human.app.state.store.q("SELECT spec FROM render_jobs WHERE id=?", (job["job_id"],))[0]["spec"])
    assert spec["kind"] == "media_render" and spec["source"] == vid and spec["via"] == "mcp_client_brain"
    res, st = mcp_call(human, tok, "render_status", {"job_id": job["job_id"]})
    assert st["status"] == "queued"
    h.finish_video(job["job_id"], b"\x00\x00\x00\x18ftypmp42done", {"type": "video", "duration": 75.0, "captions": True}, run_inline=False)
    res, st = mcp_call(human, tok, "render_status", {"job_id": job["job_id"]})
    assert st["status"] == "done" and st["download_url"].startswith("https://hq.test/m/")
    res, err = mcp_call(human, tok, "render_status", {"job_id": 99999})
    assert res["isError"]
    assert no_anthropic == []


def test_edit_video_validates_the_cut(human, monkeypatch):
    h = human.app.state.house
    tok = agent_token(human, cap=50)
    uid = human.app.state.store.q("SELECT user_id FROM agent_tokens")[0]["user_id"]
    from studio.actions import Actor
    vid = h.put_media(Actor(uid), b"x" * 10, "v.mp4", kind="upload", meta={"type": "video", "duration": 30})
    monkeypatch.setattr(house.House, "video_mode", lambda self: "github")
    res, err = mcp_call(human, tok, "edit_video", {"media_token": vid, "start_s": 10, "end_s": 10.5})
    assert res["isError"] and "at least one second" in err
    res, err = mcp_call(human, tok, "edit_video", {})
    assert res["isError"] and "media_token" in err


def test_split_video_sample_and_status(human):
    tok = agent_token(human, cap=50)
    res, t = mcp_call(human, tok, "split_video", {"sample": True})
    assert not res["isError"], t
    res, st = mcp_call(human, tok, "render_status", {"take_id": t["id"]})
    assert not res["isError"] and st["clips"] and all(c["download_url"] for c in st["clips"])


def test_kill_switch_stops_client_brain_writes(human, page):
    tok = agent_token(human, cap=50)
    store = human.app.state.store
    store.x("UPDATE users SET kill_switch=1")
    res, err = mcp_call(human, tok, "render_carousel", {"slides": [{"headline": "A b c"}, {"headline": "D e", "body": "f"}]})
    assert res["isError"]
    res, ok = mcp_call(human, tok, "research_link", {"url": "https://news.example.com/x"})  # read-only still works
    assert not res["isError"]
