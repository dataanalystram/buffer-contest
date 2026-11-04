"""3 Nov 2026 (owner): a Reel made from Claude over MCP said "Model: nvidia/nemotron..." and "Worked through 0 steps"; the
Make a post Model switch kept NVIDIA; MCP parity (templates an AI app makes are its own, draft media swap on any draft,
media preview stills, per-page context)."""
import base64
import io
import json
import shutil

import pytest
from PIL import Image

from studio import ai_engines, brain_llm, custom_templates, mcp, mcp_parity, mcp_runs
from studio.actions import Actor

from .conftest import agent_token, image_bytes, mcp_call
from .test_mcp_templates_locks import FUNDING, rpc


def _init(human, tok, name="claude-ai", ver="0.1.0"):
    out = rpc(human, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": ver}})
    assert "result" in out


# ------------------------------------------------------------------------------------------------ 1) MCP run origin + steps
def test_mcp_run_shows_the_app_not_our_model_and_its_tool_calls_as_steps(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _init(human, tok)
    res, chk = mcp_call(human, tok, "brand_check", {"text": "Click launches a managed agents API for any model"})  # before the run
    assert not res["isError"], chk
    res, pk = mcp_call(human, tok, "create_package", {"title": "Click: managed agents API for any model",
                                                      "captions": {"instagram": "Click ships a managed agents API for any model."}})
    assert not res["isError"], pk
    rid = pk["run_id"]
    run = human.get(f"/api/agent/runs/{rid}").json()
    br = run["result"]["brain"]
    assert br["mode"] == "mcp" and br["label"] == "Claude · via MCP"
    assert "chosen in Claude" in br["hint"] and "nemotron" not in json.dumps(br).lower()
    assert br["client"] == "claude-ai" and br["client_version"] == "0.1.0" and br["token"] == "Claude"
    tools = [s["tool"] for s in run["steps"]]
    assert tools[:2] == ["brand_check", "create_package"], tools  # the earlier call was claimed by the run
    assert all(s["via"] == "mcp" and s["status"] == "done" and s["label"] for s in run["steps"])
    assert "captions for 1 channel" in run["steps"][1]["detail"]
    assert "Click ships" not in json.dumps(run["steps"])  # never the caption text itself
    # later calls on the run are steps too, failures included
    res, _ = mcp_call(human, tok, "set_cover", {"run_id": rid, "channel": "nope", "index": 0})
    run = human.get(f"/api/agent/runs/{rid}").json()
    assert run["steps"][-1]["tool"] == "set_cover" and run["steps"][-1]["status"] == ("failed" if res["isError"] else "done")
    # MCP get_run says the same
    _, got = mcp_call(human, tok, "get_run", {"run_id": rid})
    assert got["result"]["brain"]["label"] == "Claude · via MCP" and len(got["steps"]) >= 3


def test_mcp_steps_are_never_stored_in_the_run_and_never_cross_accounts(human, app):
    tok = agent_token(human, policy="trusted", cap=50)
    _init(human, tok)
    _, pk = mcp_call(human, tok, "create_package", {"captions": {"instagram": "hello"}})
    rid = pk["run_id"]
    house = app.state.house
    row = house.store.q("SELECT steps, result FROM agent_runs WHERE id=?", (rid,))[0]
    before = row["steps"]
    run = house.run(Actor(house.store.q("SELECT user_id FROM agent_runs WHERE id=?", (rid,))[0]["user_id"]), rid)
    house._save(rid, run["steps"], run["result"])  # a later save (any tool) must not persist the merged MCP steps
    row = house.store.q("SELECT steps, result FROM agent_runs WHERE id=?", (rid,))[0]
    assert json.loads(row["steps"]) == json.loads(before) == []
    assert "mcp" not in json.loads(row["result"])
    # a run id of someone else is never attached
    other = house.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                          ("acc_other", "client", "{}", "ready", "[]", "{}", 0, 0))
    mcp_call(human, tok, "set_cover", {"run_id": other, "channel": "x", "index": 0})
    assert house.store.q("SELECT COUNT(*) n FROM mcp_calls WHERE run_id=?", (other,))[0]["n"] == 0


def test_external_make_post_run_gets_the_mcp_brain(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _init(human, tok, "claude-ai", "1.2")
    res, run = mcp_call(human, tok, "make_post", {"prompt": "AI agents news", "brain": "external"})
    assert not res["isError"], run
    got = human.get(f"/api/agent/runs/{run['id']}").json()
    assert got["result"]["brain"]["label"] == "Claude · via MCP"
    assert [s["tool"] for s in got["steps"]] == ["make_post"]


def test_model_hint_from_meta_is_shown_and_human_runs_are_untouched(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _init(human, tok)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "create_package", "arguments": {"captions": {"instagram": "hi"}}, "_meta": {"model": "claude-opus-4-5"}}})
    rid = json.loads(r.json()["result"]["content"][0]["text"])["run_id"]
    br = human.get(f"/api/agent/runs/{rid}").json()["result"]["brain"]
    assert br["model"] == "claude-opus-4-5" and "reported the model claude-opus-4-5" in br["hint"]
    assert mcp_runs.origin(Actor("u")) is None  # a person in the app is never an MCP origin


def test_quiet_reads_are_not_steps():
    assert {"get_run", "render_status", "list_channels", "upload_media_chunk"} <= mcp_runs.QUIET


def test_summary_keeps_secrets_out():
    label, detail = mcp_runs.summary("library_upload_url", {"url": "https://x.com/a.mp4", "api_key": "sk-123", "token": "hq_abc"}, {}, True)
    assert "sk-123" not in detail and "hq_abc" not in detail and "https://x.com/a.mp4" in detail


# ------------------------------------------------------------------------------------------------ 2) the Model switch
def _eng(eid, tools=True):
    return {"id": eid, "source": "x", "provider": eid, "model": eid + "-m", "fast_model": eid + "-f", "tools": tools}


def test_a_pick_without_tool_use_runs_itself_not_nvidia(human, app, monkeypatch):
    house = app.state.house
    uid = house.store.q("SELECT id FROM users")[0]["id"]
    ai_engines.set_pref(house.store, uid, "free")
    a = Actor(uid)
    rid = house.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                        (uid, "agent", "{}", "running", "[]", json.dumps({"thread": []}), 0, 0))
    engs = [_eng("free", tools=False), _eng("nvidia")]
    assert house.brain._honour_pick(a, rid, engs, [e for e in engs if e["tools"]]) == []  # -> the standard plan with "free"
    ai_engines.set_pref(house.store, uid, "nvidia")
    assert house.brain._honour_pick(a, rid, [_eng("nvidia"), _eng("hf")], [_eng("nvidia"), _eng("hf")])[0]["id"] == "nvidia"
    # a pick that can't run is said once in the thread, never silently replaced
    ai_engines.set_pref(house.store, uid, "gemini-studio")
    for _ in range(2):
        house.brain._honour_pick(a, rid, [_eng("nvidia")], [_eng("nvidia")])
    notes = house.run(a, rid)["result"]["notes"]
    assert len([n for n in notes if "isn't available" in n]) == 1 and "NVIDIA" in notes[0]


def test_engines_carry_their_pick_id(app, monkeypatch):
    house = app.state.house
    uid = house.store.q("SELECT id FROM users")[0]["id"] if house.store.q("SELECT id FROM users") else "u1"
    for e in brain_llm.engines(house.store, uid, house.s, house.http):
        assert e.get("id")


def test_the_switch_refreshes_the_chip_and_footer():
    js = open("studio/static/aiengine.js").read()
    assert "H_STATE.brain = b.brain" in js and "hcDrawModel" in js
    chat = open("studio/static/chat.js").read()
    assert "window.hcDrawModel" in chat and "next reply:" in chat
    assert "tr.hidden = !busy && !steps.length" in chat  # never "Worked through 0 steps"
    house = open("studio/static/house.js").read()
    assert 'b.mode === "mcp"' in house


# ------------------------------------------------------------------------------------------------ 3) templates are the AI app's own
def test_ai_app_creates_its_own_template_and_never_overwrites_the_persons(human):
    made = human.post("/api/templates/custom", json=FUNDING)
    assert made.status_code == 200, made.text
    mine = made.json()
    tok = agent_token(human, policy="trusted", cap=50)
    res, msg = mcp_call(human, tok, "update_template", {"template": mine["id"], "name": "Hijacked"})
    assert res["isError"] and "create_template" in msg
    res, msg = mcp_call(human, tok, "delete_template", {"template": mine["id"]})
    assert res["isError"]
    res, own = mcp_call(human, tok, "create_template", {**FUNDING, "user_asked": True})  # same name: a NEW template, the person's is untouched
    assert not res["isError"], own
    assert own["id"] != mine["id"] and own["yours"] and own["made_by"] == "Claude (via MCP)"
    res, up = mcp_call(human, tok, "update_template", {"template": own["id"], "about": "Claude's version"})
    assert not res["isError"], up
    lst = {t["id"]: t for t in human.get("/api/templates").json()["templates"]}
    assert lst[mine["id"]]["name"] == FUNDING["name"] and lst[mine["id"]]["made_by"] == "you"
    assert lst[own["id"]]["custom"] and lst[own["id"]]["owner"] == "yours"
    res, got = mcp_call(human, tok, "get_template", {"template": own["id"]})
    assert not res["isError"] and got["about"] == "Claude's version"
    res, got = mcp_call(human, tok, "get_template", {"template": FUNDING["name"]})
    assert not res["isError"] and got["custom"]
    builtin = next(iter(custom_templates.templates.TEMPLATES))
    res, got = mcp_call(human, tok, "get_template", {"template": builtin})
    assert not res["isError"] and got["built_in"]
    res, msg = mcp_call(human, tok, "get_template", {"template": "no such thing"})
    assert res["isError"]
    # a person can still change their own and the app's
    assert human.patch(f"/api/templates/custom/{own['db_id']}", json={"about": "edited by Ram"}).status_code == 200


# ------------------------------------------------------------------------------------------------ 3) replace media on any draft
def test_replace_draft_media_edits_any_draft_in_place(human, fake):
    tok = agent_token(human, policy="draft", cap=50)
    res, out = mcp_call(human, tok, "replace_draft_media", {"post_id": "d1", "media_urls": ["https://cdn.example.com/a.png"]})
    assert not res["isError"], out
    assert out["strategy"] == "edit" and fake.edits[-1]["id"] == "d1" and fake.edits[-1]["text"] == "a draft"
    assert fake.edits[-1]["assets"] and not fake.deleted
    # scheduled and sent posts are refused, nothing is changed
    n = len(fake.edits)
    for pid in ("p9", "s1"):
        res, msg = mcp_call(human, tok, "replace_draft_media", {"post_id": pid, "media_urls": ["https://cdn.example.com/a.png"]})
        assert res["isError"]
    assert len(fake.edits) == n and not fake.deleted


def test_replace_draft_media_recreates_and_deletes_the_old_only_on_confirm(human, fake, app, monkeypatch):
    from studio.buffer_api import BufferError
    studio = app.state.studio

    def refuse(uid, inp):
        raise BufferError("Buffer can't change this post's media", 422)
    monkeypatch.setattr(studio.buffer, "edit_post", refuse)
    tok = agent_token(human, policy="trusted", cap=50)
    res, out = mcp_call(human, tok, "replace_draft_media", {"post_id": "d1", "media_urls": ["https://cdn.example.com/a.png"]})
    assert not res["isError"], out
    assert out["strategy"] == "recreate" and out["new_post_id"] and out["old_deleted"] is False and "confirm=true" in out["next"]
    assert fake.created[-1]["text"] == "a draft" and fake.created[-1]["saveToDraft"] and not fake.deleted
    # delete_old without confirm keeps it; with both it goes, after the new draft exists
    res, out = mcp_call(human, tok, "replace_draft_media", {"post_id": "d1", "media_urls": ["https://cdn.example.com/a.png"], "delete_old": True})
    assert out["old_deleted"] is False and not fake.deleted
    res, out = mcp_call(human, tok, "replace_draft_media", {"post_id": "d1", "media_urls": ["https://cdn.example.com/a.png"],
                                                            "delete_old": True, "confirm": True})
    assert not res["isError"], out
    assert fake.deleted == ["d1"]


# ------------------------------------------------------------------------------------------------ 4) see what you made
def _own_media(app, uid, data, mime, name):
    house = app.state.house
    import secrets
    tok = secrets.token_urlsafe(12) + "." + name.rsplit(".", 1)[-1]
    house.store.x("INSERT INTO media(token,user_id,name,mime,data,size,meta,created) VALUES(?,?,?,?,?,?,?,?)",
                  (tok, uid, name, mime, data, len(data), "{}", 0))
    return tok


def other_tok(t):
    return ("A" if t[0] != "A" else "B") + t[1:]


def _uid(app):
    return app.state.house.store.q("SELECT id FROM users")[0]["id"]


def test_media_preview_image_and_ownership(human, app):
    mcp_parity._hits.clear()
    tok = agent_token(human)
    mt = _own_media(app, _uid(app), image_bytes(1080, 1350, "JPEG"), "image/jpeg", "c.jpg")
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "get_media_preview", "arguments": {"media_token": mt, "width": 320}}})
    res = r.json()["result"]
    assert not res["isError"], res
    imgs = [c for c in res["content"] if c["type"] == "image"]
    assert len(imgs) == 1 and imgs[0]["mimeType"] == "image/jpeg"
    assert Image.open(io.BytesIO(base64.b64decode(imgs[0]["data"]))).size[0] <= 320
    meta = json.loads(res["content"][0]["text"])
    assert "_mcp_images" not in meta and meta["signed"]["url"].startswith("http")
    path = meta["signed"]["url"].split("://", 1)[1].split("/", 1)[1]
    got = human.get("/" + path)
    assert got.status_code == 200 and got.content[:2] == b"\xff\xd8"
    exp, sig, t = path.split("/")[1:4]
    assert human.get(f"/ms/{exp}/{'0' * 32}/{t}").status_code == 403  # tampered signature
    assert human.get(f"/ms/{exp}/{sig}/{other_tok(t)}").status_code == 403  # signature for another file
    assert human.get(f"/ms/{int(exp) - 99999}/{sig}/{t}").status_code == 403  # expired
    other = _own_media(app, "acc_other", image_bytes(10, 10), "image/png", "o.png")
    res, msg = mcp_call(human, tok, "get_media_preview", {"media_token": other})
    assert res["isError"] and "No such media" in msg
    res, msg = mcp_call(human, tok, "get_media_preview", {"url": "https://evil.example.com/x.mp4"})
    assert res["isError"]


def test_media_preview_rate_limit(human, app):
    mcp_parity._hits.clear()
    tok = agent_token(human)
    mt = _own_media(app, _uid(app), image_bytes(64, 64), "image/png", "s.png")
    for _ in range(mcp_parity.PREVIEW_PER_10MIN):
        res, _ = mcp_call(human, tok, "get_media_preview", {"media_token": mt})
        assert not res["isError"]
    res, msg = mcp_call(human, tok, "get_media_preview", {"media_token": mt})
    assert res["isError"] and "Too many previews" in msg
    mcp_parity._hits.clear()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_media_preview_video_frames_at_0_1_2(human, app, tmp_path):
    import subprocess
    mcp_parity._hits.clear()
    out = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=320x568:rate=10", "-t", "3", "-pix_fmt", "yuv420p",
                    str(out)], check=True)
    mt = _own_media(app, _uid(app), out.read_bytes(), "video/mp4", "v.mp4")
    tok = agent_token(human)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "get_media_preview", "arguments": {"media_token": mt}}})
    res = r.json()["result"]
    assert not res["isError"], res
    assert len([c for c in res["content"] if c["type"] == "image"]) == 3
    assert [f["at_s"] for f in json.loads(res["content"][0]["text"])["frames"]] == [0, 1, 2]


# ------------------------------------------------------------------------------------------------ parity: tools/list
def test_tools_list_parity_and_page_context(human):
    tok = agent_token(human)
    tools = rpc(human, tok, "tools/list")["result"]["tools"]
    names = [t["name"] for t in tools]
    assert len(names) == len(set(names)) == len(mcp.TOOLS)
    need = {"create_template", "update_template", "delete_template", "list_templates", "get_template", "reschedule_post", "update_post",
            "replace_draft_media", "set_cover", "set_post_music", "list_music", "set_reel_audio", "list_library_bin",
            "restore_library_items", "purge_library_bin", "bulk_delete_library", "draft_from_library", "get_media_preview",
            "get_page_context", "delete_draft"}
    assert need <= set(names), need - set(names)
    for t in tools:  # every tool: a real description and an object input schema
        assert len(t["description"]) >= 30, t["name"]
        assert t["inputSchema"]["type"] == "object", t["name"]
    _, ctx = mcp_call(human, tok, "get_page_context", {})
    assert ctx["tool_count"] == len(names)
    listed = {x["name"] for p in ctx["pages"] for x in p["tools"]}
    for p in mcp_parity.PAGES.values():  # every tool a page names exists
        assert set(p["tools"]) <= set(names), set(p["tools"]) - set(names)
    assert "replace_draft_media" in listed
    _, one = mcp_call(human, tok, "get_page_context", {"page": "templates"})
    assert [p["page"] for p in one["pages"]] == ["templates"]


def test_docs_list_every_new_tool():
    doc = open("docs/MCP_TOOLS.md").read()
    for n in ("get_media_preview", "replace_draft_media", "get_page_context", "get_template"):
        assert f"`{n}`" in doc, n
