"""MCP scenarios for run IDs over the JSON-RPC endpoint (owner, 4 Nov 2026 15:07): no per-hour or per-day new-run caps;
everything that belongs to one request is ONE run and ONE thread, and a new request or a new chat is a new run.

(a) carousel, then "make it a reel instead": same run, same thread, same Library item
(b) "make 3 reels": one run with 3 outputs, one thread
(c) repeated edits update in place: no extra runs or Library items
(d) a new chat / new request with user_asked: a new run, never blocked
(e) the paid-API approval gate
(f) cleanup lists first, then moves to the Bins
(g) a NEW unrelated request in the same chat: a new run and thread (refined model, 16:14)
(h) an ambiguous request: the instructions say ask the person; continue_run joins the old run
"""
import json
import time

from studio import mcp_client_brain
from studio.actions import Actor

from .conftest import agent_token, mcp_call
from .test_mcp_client_brain import no_anthropic, page  # noqa: F401  (fixtures)
from .test_mcp_run_discipline import fake_server_run  # noqa: F401  (fixture)
from .test_mcp_templates_locks import rpc

import pytest

URL = "https://news.example.com/ai-launch"


@pytest.fixture
def fake_video(monkeypatch):
    """Video renders finish at once: a real render_jobs row and finish_video, just no FFmpeg."""
    made = []

    def queue(h, a, spec):
        jid = h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                        (a.uid, None, json.dumps(spec), "queued", time.time(), time.time()))
        made.append(jid)
        h.finish_video(jid, b"\x00\x00\x00\x18ftypmp42" + str(jid).encode() * 64, {"type": "video", "width": 1080, "height": 1920,
                                                                                    "duration": 12}, run_inline=False)
        return {"job_id": jid, "status": "queued", "rendered_by": "test", "poll": f"render_status(job_id={jid})"}
    monkeypatch.setattr(mcp_client_brain, "_queue_video", queue)
    return made


def _call(client, tok, name, args=None):
    res, out = mcp_call(client, tok, name, args or {})
    assert not res["isError"], out
    return out


def _err(client, tok, name, args=None):
    res, out = mcp_call(client, tok, name, args or {})
    assert res["isError"], out
    return out


def _chat(client, name="claude-ai"):
    """A fresh MCP session (one chat in the person's AI app) on its own token."""
    tok = agent_token(client, policy="trusted", cap=100)
    out = rpc(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": name, "version": "1"}})
    assert "result" in out
    return tok


def _slides(tag=""):
    return [{"eyebrow": "AUDIO", "headline": f"Best Headphones Under 150 {tag}".strip(), "accent_word": "Headphones", "body": "Five picks."},
            {"eyebrow": "PICK 1", "headline": f"Pick Number One {tag}".strip(), "body": "Reason one."},
            {"eyebrow": "PICK 2", "headline": f"Pick Number Two {tag}".strip(), "body": "Reason two."}]


def _uid(client):
    return client.app.state.store.q("SELECT id FROM users")[0]["id"]


def _runs(client):
    return client.app.state.store.q("SELECT COUNT(*) AS n FROM agent_runs WHERE user_id=?", (_uid(client),))[0]["n"]


def _lib(client):
    return client.app.state.store.q("SELECT id, kind, run_id, meta FROM library_items WHERE user_id=? AND storage NOT IN "
                                    "('pending','trash') ORDER BY id", (_uid(client),))


def _run(client, rid):
    return client.get(f"/api/agent/runs/{rid}").json()


def _wait(client, tok, job):
    st = _call(client, tok, "render_status", {"job_id": job["job_id"]})
    assert st["status"] == "done", st
    return st


# ------------------------------------------------------------------------------------------------ (a)
def test_a_carousel_then_switch_to_reel_is_the_same_run_and_thread(human, page, fake_video):  # noqa: F811
    tok = _chat(human)
    st = _call(human, tok, "start_post", {"title": "Best headphones under EUR 150", "format": "carousel",
                                          "user_asked": True, "reason": "make a carousel of the best headphones"})
    rid = st["run_id"]
    car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides()})
    assert car["run_id"] == rid and car["output"] == 1
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "Five picks."}, "media_urls": car["media_urls"]})
    # the person: "I don't want a carousel, make it a reel"
    sw = _call(human, tok, "start_post", {"run_id": rid, "format": "reel", "title": "Best headphones under EUR 150",
                                          "user_asked": True, "reason": "I don't want a carousel, make it a reel"})
    assert sw["run_id"] == rid and sw["reused"] is True and sw["format"] == "reel" and sw["format_was"] == "carousel"
    job = _call(human, tok, "render_reel", {"run_id": rid, "images": car["media_urls"]})
    reel = _wait(human, tok, job)
    assert reel["run_id"] == rid and reel["output"] == 1
    pk = _call(human, tok, "create_package", {"run_id": rid, "output": 1, "captions": {"instagram": "Five picks, now a reel."}})
    assert pk["run_id"] == rid and pk["updated"] is True and pk["packages"][0]["format"] == "reel"
    assert _runs(human) == 1  # ONE run
    assert [r["id"] for r in human.get("/api/agent/runs").json()] == [rid]  # ONE thread in Make a post
    run = _run(human, rid)
    res = run["result"]
    assert res["format"] == "reel" and [o["format"] for o in res["outputs"]] == ["reel"]
    assert any("make it a reel" in m["text"] for m in res["thread"] if m["role"] == "user")
    assert any(m["text"].startswith("Reel 1 updated (was a carousel)") for m in res["thread"]), res["thread"]
    lib = _lib(human)
    assert len(lib) == 1 and lib[0]["run_id"] == rid and lib[0]["kind"] == "video"  # the carousel's item became the reel
    assert res["packages"][0]["media"][0]["url"] == reel["download_url"]


# ------------------------------------------------------------------------------------------------ (b)
def test_b_three_reels_are_one_run_with_three_outputs_and_one_thread(human, page, fake_video):  # noqa: F811
    tok = _chat(human)
    rid = _call(human, tok, "start_post", {"title": "3 reels on the AI card launch", "format": "reel", "user_asked": True,
                                           "reason": "make 3 reels about this"})["run_id"]
    car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides("base"), "output": 1})
    done = []
    for n in (1, 2, 3):
        job = _call(human, tok, "render_reel", {"run_id": rid, "images": car["media_urls"][n - 1:] + car["media_urls"][:n - 1],
                                                "hook": f"Take {n}", "output": n})
        done.append(_wait(human, tok, job))
    assert [d["output"] for d in done] == [1, 2, 3] and {d["run_id"] for d in done} == {rid}
    assert _runs(human) == 1
    assert [r["id"] for r in human.get("/api/agent/runs").json()] == [rid]
    res = _run(human, rid)["result"]
    assert [(o["output"], o["format"]) for o in res["outputs"]] == [(1, "reel"), (2, "reel"), (3, "reel")]
    assert [o["media_urls"][0] for o in res["outputs"]] == [d["download_url"] for d in done]
    assert sum(1 for m in res["thread"] if m["text"] in ("Reel 2 ready", "Reel 3 ready")) == 2
    lib = _lib(human)  # one Library item per output, all on the one run
    assert len(lib) == 3 and {i["run_id"] for i in lib} == {rid} and all(i["kind"] == "video" for i in lib)
    assert sorted(json.loads(i["meta"]).get("output", 1) for i in lib) == [1, 2, 3]
    # polling a finished job again changes nothing
    _call(human, tok, "render_status", {"job_id": fake_video[1]})
    assert len(_run(human, rid)["result"]["outputs"]) == 3 and len(_lib(human)) == 3
    # an output that doesn't exist and one out of range are plain errors
    assert "no output 4" in _err(human, tok, "create_package", {"run_id": rid, "output": 4, "captions": {"instagram": "x"}})
    assert "1 to 10" in _err(human, tok, "render_reel", {"run_id": rid, "images": car["media_urls"], "output": 11})


# ------------------------------------------------------------------------------------------------ (c)
def test_c_repeated_edits_update_in_place_with_no_extra_library_items(human, page, fake_video):  # noqa: F811
    tok = _chat(human)
    rid = _call(human, tok, "start_post", {"title": "Headphones carousel", "user_asked": True, "reason": "asked"})["run_id"]
    for i in range(4):  # "brighter", "swap slide 2", "shorter text", "again"
        car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(f"v{i}")})
        pk = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": f"Caption v{i}"}, "media_urls": car["media_urls"]})
        assert pk["run_id"] == rid and pk["updated"] is True
    for i in range(2):  # and twice as a reel, same output
        _wait(human, tok, _call(human, tok, "render_reel", {"run_id": rid, "images": car["media_urls"], "hook": f"h{i}"}))
    assert _runs(human) == 1
    lib = _lib(human)
    assert len(lib) == 1 and lib[0]["run_id"] == rid
    res = _run(human, rid)["result"]
    assert len(res["outputs"]) == 1 and res["outputs"][0]["versions"] >= 6
    assert res["packages"][0]["text"] == "Caption v3" and len(res.get("package_versions") or []) >= 3  # Undo kept


# ------------------------------------------------------------------------------------------------ (d)
def test_d_new_chats_and_new_requests_make_new_runs_and_are_never_blocked(human, page):  # noqa: F811
    tok = _chat(human)
    first = _call(human, tok, "start_post", {"title": "AI card launch carousel", "user_asked": True, "reason": "asked"})
    # the same request again a moment later (the app retried, worded differently): the same run
    again = _call(human, tok, "start_post", {"title": "AI card launch carousel", "brief": "the card post", "user_asked": True,
                                             "reason": "asked again"})
    assert again["run_id"] == first["run_id"] and again["deduped"] is True
    # new requests in this chat, then new chats (new tokens, as Claude.ai / ChatGPT open them): all new runs, none refused
    ids = {first["run_id"]}
    for i in range(6):
        ids.add(_call(human, tok, "start_post", {"title": f"New request {i}", "user_asked": True, "reason": "asked"})["run_id"])
    for i in range(6):
        t = _chat(human, name=f"chat-{i}")
        ids.add(_call(human, t, "start_post", {"title": f"New chat {i}", "user_asked": True, "reason": "asked"})["run_id"])
    assert len(ids) == 13 and _runs(human) == 13
    # still: no user_asked, no new run
    assert "user_asked" in _err(human, tok, "start_post", {"title": "Unasked"})
    assert _runs(human) == 13


def test_d2_a_token_past_its_daily_action_cap_can_still_open_a_new_run(human, page):  # noqa: F811
    """No per-day limit on new runs: the token's daily action cap (writes to Buffer, uploads, ...) never blocks start_post;
    the kill switch still does."""
    tok = agent_token(human, policy="trusted", cap=1)
    store = human.app.state.store
    label = store.q("SELECT name FROM agent_tokens ORDER BY id DESC LIMIT 1")[0]["name"]
    for _ in range(3):  # the token's counted actions for today are used up
        store.audit(_uid(human), f"agent:{label}", "upload_media", "write", {}, ok=True)
    assert "daily cap" in _err(human, tok, "render_carousel", {"slides": _slides()})  # a counted write is refused
    ids = {_call(human, tok, "start_post", {"title": f"Request {i}", "user_asked": True, "reason": "asked"})["run_id"] for i in range(3)}
    assert len(ids) == 3
    human.post("/api/safety/kill", json={"on": True})
    assert "kill switch" in _err(human, tok, "start_post", {"title": "Paused", "user_asked": True, "reason": "asked"})


# ------------------------------------------------------------------------------------------------ (e)
def test_e_paid_api_waits_for_the_person_then_runs_once(human, fake_server_run, no_anthropic):  # noqa: F811
    tok = _chat(human)
    ask = {"query": "best headphones under 150", "server_ai": True}
    wait = _call(human, tok, "run_agent", ask)
    assert wait["needs_user_approval"] is True and wait["spent_usd"] == 0 and fake_server_run == [] and no_anthropic == []
    assert _runs(human) == 0  # nothing ran, no thread made
    assert human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).json()["status"] == "approved"
    ran = _call(human, tok, "run_agent", ask)
    assert ran.get("id") == 4242 and len(fake_server_run) == 1
    assert _call(human, tok, "run_agent", ask)["needs_user_approval"] is True and len(fake_server_run) == 1  # used once


# ------------------------------------------------------------------------------------------------ (f)
def test_f_cleanup_lists_first_then_moves_to_the_bins(human, page):  # noqa: F811
    tok = _chat(human)
    store = human.app.state.store
    rid = _call(human, tok, "start_post", {"title": "Left over", "user_asked": True, "reason": "asked"})["run_id"]
    car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides()})
    store.x("UPDATE agent_runs SET created=created-7200, updated=updated-7200")
    store.x("UPDATE library_items SET created=created-7200")
    dry = _call(human, tok, "cleanup_agent_drafts", {})
    assert dry["dry_run"] is True and [r["run_id"] for r in dry["runs"]] == [rid]
    assert car["library_id"] in {i["library_id"] for i in dry["library_items"]}
    assert store.q("SELECT status FROM agent_runs WHERE id=?", (rid,))[0]["status"] != "archived"  # the list changed nothing
    done = _call(human, tok, "cleanup_agent_drafts", {"confirm_token": dry["confirm_token"]})
    assert done["archived_runs"] == [rid] and car["library_id"] in done["binned_items"] and done["bin_days"] == 7
    assert store.q("SELECT storage FROM library_items WHERE id=?", (car["library_id"],))[0]["storage"] == "trash"
    assert rid not in [r["id"] for r in human.get("/api/agent/runs").json()]
    assert Actor  # (import kept for parity with the other run tests)


# ------------------------------------------------------------------------------------------------ (g)
def test_g_a_new_unrelated_request_in_the_same_chat_is_a_new_run_and_thread(human, page):  # noqa: F811
    """Refined model (owner, 4 Nov 2026 16:14): one request = one run = one thread, not one giant thread per chat."""
    tok = _chat(human)
    first = _call(human, tok, "start_post", {"title": "Best headphones under EUR 150", "format": "carousel", "user_asked": True,
                                             "reason": "make a carousel of the best headphones"})
    rid1 = first["run_id"]
    assert first["thread_url"].endswith(f"/?tab=library&run={rid1}")
    _call(human, tok, "render_carousel", {"run_id": rid1, "slides": _slides()})
    # same chat, new content: "now make a reel about the OpenAI device launch"
    second = _call(human, tok, "start_post", {"title": "OpenAI device launch reel", "format": "reel", "new_request": True,
                                              "reason": "now make a reel about the OpenAI device launch"})
    rid2 = second["run_id"]
    assert rid2 != rid1 and second["reused"] is False and second["previous_run_id"] == rid1
    assert "continue_run" in second["note"] and second["thread_url"].endswith(f"run={rid2}")
    # new_request wins over the same-title dedupe too: the person asked again for a new one
    third = _call(human, tok, "start_post", {"title": "OpenAI device launch reel", "new_request": True, "reason": "another one"})
    assert third["run_id"] not in (rid1, rid2)
    assert _runs(human) == 3
    runs = {r["id"]: r for r in human.get("/api/agent/runs").json()}
    assert {rid1, rid2, third["run_id"]} <= set(runs)
    t1 = _run(human, rid1)["result"]["thread"]
    t2 = _run(human, rid2)["result"]["thread"]
    assert any("headphones" in m["text"] for m in t1) and not any("OpenAI" in m["text"] for m in t1)
    assert any("OpenAI" in m["text"] for m in t2) and not any("headphones" in m["text"] for m in t2)
    # the follow-up render with run_id lands on its own run only
    car = _call(human, tok, "render_carousel", {"run_id": rid2, "slides": _slides("openai")})
    assert car["run_id"] == rid2 and car["thread_url"].endswith(f"run={rid2}")


# ------------------------------------------------------------------------------------------------ (h)
def test_h_ambiguous_request_instructions_say_ask_and_continue_run_joins_the_old_run(human, page, fake_video):  # noqa: F811
    tok = _chat(human)
    init = rpc(human, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "c", "version": "1"}})
    text = init["result"]["instructions"]
    assert "Continue in the current post or start a new one?" in text and "continue_run(run_id)" in text
    assert "new_request: true" in text
    tools = {t["name"]: t for t in rpc(human, tok, "tools/list")["result"]["tools"]}
    assert "continue_run" in tools and tools["continue_run"]["inputSchema"]["required"] == ["run_id"]
    assert "new_request" in tools["start_post"]["inputSchema"]["properties"]
    rid1 = _call(human, tok, "start_post", {"title": "AI card launch", "format": "carousel", "user_asked": True,
                                            "reason": "a carousel on the AI card launch"})["run_id"]
    car = _call(human, tok, "render_carousel", {"run_id": rid1, "slides": _slides()})
    rid2 = _call(human, tok, "start_post", {"title": "Headphones roundup", "new_request": True, "reason": "headphones"})["run_id"]
    # "add 2 more reels" - of which? The app asked; the person said "the card one": continue_run joins run 1
    j = _call(human, tok, "continue_run", {"run_id": rid1, "reason": "add 2 more reels of the card story"})
    assert j["run_id"] == rid1 and j["joined"] is True and j["reused"] is True and j["thread_url"].endswith(f"run={rid1}")
    for n in (2, 3):
        r = _wait(human, tok, _call(human, tok, "render_reel", {"run_id": rid1, "images": car["media_urls"], "output": n}))
        assert r["run_id"] == rid1
    assert _runs(human) == 2
    res1 = _run(human, rid1)["result"]
    assert any(m["text"] == "add 2 more reels of the card story" for m in res1["thread"] if m["role"] == "user")
    assert sorted(o["output"] for o in res1["outputs"]) == [1, 2, 3]
    assert not _run(human, rid2)["result"].get("outputs")
    # a format switch through continue_run is the same run too
    sw = _call(human, tok, "continue_run", {"run_id": rid1, "format": "reel", "reason": "make the first one a reel"})
    assert sw["run_id"] == rid1 and sw["format"] == "reel" and _runs(human) == 2
    # continue_run needs a run_id, and never reaches another person's run or makes a run
    assert "run_id" in _err(human, tok, "continue_run", {"reason": "x"})
    assert "No such post run" in _err(human, tok, "continue_run", {"run_id": 999999})
    assert _runs(human) == 2
