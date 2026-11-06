"""Phase 1 of docs/plans/AGENT_RUNS_EDITING.md (owner decisions, 4 Nov 2026): one user request = one run over MCP.

One prompt in Claude ("find the best headphones under EUR 150, make a carousel with images, edit the images nicely") left
several Make a post threads and many Library items. These tests replay that flow and every rule that stops it: run reuse,
the burst guard (no hourly or daily caps), idempotency, Library dedupe, consent for new runs / templates / kits, the paid-API approval gate and the cleanup tool.
"""
import json
import time

import pytest

from studio import consent, mcp, run_discipline
from studio.actions import Actor

from .conftest import agent_token, mcp_call
from .test_mcp_client_brain import no_anthropic, page  # noqa: F401  (fixtures)
from .test_mcp_templates_locks import rpc

URL = "https://news.example.com/ai-launch"
IMG = "https://news.example.com/hero.jpg"


def _init(client, tok, name="claude-ai"):
    out = rpc(client, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": name, "version": "1"}})
    assert "result" in out


def _slides(n=3, tag=""):
    out = [{"eyebrow": "AUDIO", "headline": f"Best Headphones Under 150 {tag}".strip(), "accent_word": "Headphones", "body": "Five picks."}]
    out += [{"eyebrow": f"PICK {i}", "headline": f"Pick Number {i} {tag}".strip(), "body": f"Reason {i}."} for i in range(1, n)]
    return out


def _uid(client):
    return client.app.state.store.q("SELECT id FROM users")[0]["id"]


def _call(client, tok, name, args=None):
    res, out = mcp_call(client, tok, name, args or {})
    return res["isError"], out


def _start(client, tok, title="Best headphones under EUR 150", **kw):
    err, out = _call(client, tok, "start_post", {"title": title, "user_asked": True, "reason": "the person asked", **kw})
    assert not err, out
    return out


# ------------------------------------------------------------------------------------------------ the headphones flow
def test_headphones_flow_is_one_run_one_library_item_one_thread(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    _init(human, tok)
    store = human.app.state.store
    st = _start(human, tok)
    rid = st["run_id"]
    assert st["reused"] is False and st["status"] == "draft"
    err, _ = _call(human, tok, "research_link", {"url": URL})
    assert not err
    urls = None
    for i in range(3):  # two "fit" retries
        err, car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(3, f"v{i}")})
        assert not err, car
        assert car["run_id"] == rid
        urls = car["media_urls"]
    for ratio in ("4:5", "1:1"):
        err, he = _call(human, tok, "hero_edit", {"run_id": rid, "image_url": IMG, "ratio": ratio})
        assert not err, he
    for cap in ("Five headphones under EUR 150.", "Five picks under EUR 150, ranked."):
        err, pk = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": cap}, "media_urls": urls,
                                                        "title": "Best headphones under EUR 150"})
        assert not err, pk
        assert pk["run_id"] == rid and pk["updated"] is True
    uid = _uid(human)
    assert store.q("SELECT COUNT(*) AS n FROM agent_runs WHERE user_id=?", (uid,))[0]["n"] == 1  # ONE thread
    lib = store.q("SELECT id, run_id, meta FROM library_items WHERE user_id=? AND storage NOT IN ('pending','trash')", (uid,))
    assert len(lib) == 1 and lib[0]["run_id"] == rid  # ONE Library item, the post's latest carousel
    assert json.loads(lib[0]["meta"])["tokens"] == [u.rsplit("/m/", 1)[-1] for u in urls]
    run = human.get(f"/api/agent/runs/{rid}").json()
    tools = [s["tool"] for s in run["steps"] if s.get("via") == "mcp"]
    for want in ("start_post", "research_link", "render_carousel", "hero_edit", "create_package"):
        assert want in tools, (want, tools)
    assert len(tools) >= 8 and tools.count("render_carousel") == 3
    assert run["result"]["packages"][0]["text"].startswith("Five picks")
    # Make a post shows one thread for it
    assert [r["id"] for r in human.get("/api/agent/runs").json()] == [rid]


def test_a_creator_without_run_id_reuses_the_open_run(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    err, pk = _call(human, tok, "create_package", {"captions": {"instagram": "Hello"}})
    assert not err, pk
    assert pk["run_id"] == rid and pk["reused"] is True and "start_post" in pk["note"] and f"run_id: {rid}" in pk["note"]
    # the same request again (a retried start_post) is the same run; a NEW request is a new run (owner, 4 Nov 2026 15:07)
    retry = _start(human, tok)  # an identical retry within 120 s replays the first answer
    assert retry["run_id"] == rid and retry["idempotent_replay"] is True
    dup = _start(human, tok, brief="same ask, worded again")  # the same request again: deduped onto the open run
    assert dup["run_id"] == rid and dup["reused"] is True and dup["deduped"] is True
    other_req = _start(human, tok, title="Something else")
    assert other_req["run_id"] != rid and other_req["reused"] is False
    # another app never lands in this app's open run
    other = agent_token(human, policy="trusted", cap=100)
    err, pk2 = _call(human, other, "create_package", {"captions": {"instagram": "Mine"}})
    assert not err and pk2["run_id"] not in (rid, other_req["run_id"]) and not pk2.get("reused")


def test_new_post_needs_user_asked_and_there_is_no_hourly_or_daily_cap(human, page, monkeypatch):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    err, msg = _call(human, tok, "start_post", {"title": "No consent"})
    assert err and "user_asked" in msg
    r1 = _start(human, tok)["run_id"]
    err, msg = _call(human, tok, "create_package", {"captions": {"instagram": "x"}, "new_post": True})
    assert err and "user_asked" in msg and "ask them" in msg
    err, pk = _call(human, tok, "create_package", {"captions": {"instagram": "x"}, "new_post": True, "user_asked": True, "reason": "asked"})
    assert not err and pk["run_id"] != r1
    # 25 new requests (25 new chats) from one app: none refused - the per-hour (3) and per-day (20) caps are gone
    ids = {_start(human, tok, title=f"Request {i}")["run_id"] for i in range(25)}
    assert len(ids) == 25
    store = human.app.state.store
    assert store.q("SELECT COUNT(*) AS n FROM agent_runs")[0]["n"] == 27
    assert not hasattr(run_discipline, "PER_HOUR") and not hasattr(run_discipline, "PER_DAY")
    # only a runaway loop is stopped (burst guard, per minute), and the answer names the open run
    monkeypatch.setattr(run_discipline, "PER_MINUTE", 27)
    err, msg = _call(human, tok, "start_post", {"title": "One too many", "user_asked": True})
    assert err and "last minute" in msg and "loop" in msg and "run_id" in msg
    store.x("UPDATE agent_runs SET created=created-120")  # a minute later it works again
    assert _start(human, tok, title="One too many")["reused"] is False
    # a signed-in person is never limited (the UI / REST path)
    for _ in range(4):
        assert human.post("/api/agent/runs", json={"prompt": "make a post from https://news.example.com/agents"}).status_code == 200


def test_idempotency_key_replays_and_refuses_a_different_call(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    args = {"run_id": rid, "captions": {"instagram": "Once"}, "idempotency_key": "k-1"}
    err, a = _call(human, tok, "create_package", args)
    assert not err, a
    err, b = _call(human, tok, "create_package", args)
    assert not err and b["idempotent_replay"] is True and b["run_id"] == a["run_id"]
    run = human.get(f"/api/agent/runs/{rid}").json()
    assert not run["result"].get("package_versions")  # the replay did not stage a second version
    err, msg = _call(human, tok, "create_package", {**args, "captions": {"instagram": "Other"}})
    assert err and "different call" in msg
    # the MCP _meta form works too
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
        "name": "create_package", "arguments": {"run_id": rid, "captions": {"instagram": "Meta"}}, "_meta": {"huhqueue/idempotency_key": "m-1"}}})
    first = r.json()["result"]["structuredContent"]
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
        "name": "create_package", "arguments": {"run_id": rid, "captions": {"instagram": "Meta"}}, "_meta": {"huhqueue/idempotency_key": "m-1"}}})
    assert r.json()["result"]["structuredContent"]["idempotent_replay"] is True and first["run_id"] == rid
    # without a key, an identical render right after (a retry) is the same render
    err, c1 = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2)})
    err2, c2 = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2)})
    assert not err and not err2 and c2["idempotent_replay"] is True and c1["media_urls"] == c2["media_urls"]
    # keys are per token: another app's key never replays this one's result
    other = agent_token(human, policy="trusted", cap=100)
    err, msg = _call(human, other, "create_package", args)
    assert not err or "different call" not in msg


def test_the_same_image_uploaded_twice_is_one_library_item(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    err, one = _call(human, tok, "library_upload_url", {"url": IMG + "?a=1", "run_id": rid})
    assert not err, one
    err, two = _call(human, tok, "library_upload_url", {"url": IMG + "?a=2", "run_id": rid})
    assert not err, two
    assert two["deduped"] is True and two["library_id"] == one["library_id"] and two["media_token"] == one["media_token"]
    store = human.app.state.store
    rows = store.q("SELECT run_id, agent_id FROM library_items WHERE storage='chunks'")
    assert len(rows) == 1 and rows[0]["run_id"] == rid and rows[0]["agent_id"]


def test_create_package_updates_in_place_and_undo_restores_the_previous_version(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    err, car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "a")})
    err, _ = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "Version A"}, "media_urls": car["media_urls"]})
    assert not err
    err, car2 = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "b")})
    err, pk = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "Version B"}, "media_urls": car2["media_urls"]})
    assert not err and pk["versions_kept"] == 1
    run = human.get(f"/api/agent/runs/{rid}").json()
    p = run["result"]["packages"][0]
    assert p["text"] == "Version B" and p["undo"]["label"] == "Back to the previous version"
    err, back = _call(human, tok, "undo_package_media", {"run_id": rid})
    assert not err, back
    p = back["result"]["packages"][0]
    assert p["text"] == "Version A" and p["media_urls"] == car["media_urls"]
    # the run's ONE Library item follows the post back
    store = human.app.state.store
    lib = store.q("SELECT meta FROM library_items WHERE run_id=? AND storage='media'", (rid,))
    assert len(lib) == 1 and json.loads(lib[0]["meta"])["tokens"] == [u.rsplit("/m/", 1)[-1] for u in car["media_urls"]]
    err, msg = _call(human, tok, "undo_package_media", {"run_id": rid})
    assert err and "no earlier version" in msg


def test_another_persons_run_and_an_archived_run_answer_404(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store = human.app.state.store
    now = time.time()
    theirs = store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                     ("someone-else", "client", "{}", "ready", "[]", json.dumps({"packages": []}), now, now))
    for name, args in (("create_package", {"captions": {"instagram": "x"}}), ("render_carousel", {"slides": _slides(2)}),
                       ("start_post", {"title": "x", "user_asked": True}), ("hero_edit", {"image_url": IMG}),
                       ("library_upload_url", {"url": IMG}), ("create_upload", {"name": "a.mp4"})):
        err, msg = _call(human, tok, name, {**args, "run_id": theirs})
        assert err and "No such post run" in msg, (name, msg)
    mine = _start(human, tok)["run_id"]
    store.x("UPDATE agent_runs SET status='archived' WHERE id=?", (mine,))
    err, msg = _call(human, tok, "create_package", {"run_id": mine, "captions": {"instagram": "x"}})
    assert err and "No such post run" in msg
    # someone else's run never changed
    assert json.loads(store.q("SELECT result FROM agent_runs WHERE id=?", (theirs,))[0]["result"]) == {"packages": []}


def test_templates_and_brand_kits_need_user_asked(human):
    tok = agent_token(human, policy="trusted", cap=100)
    err, msg = _call(human, tok, "create_template", {"name": "Funding", "slides": [{"type": "front", "title": "x"}]})
    assert err and "user_asked" in msg
    err, msg = _call(human, tok, "create_brand_kit", {"name": "Beta"})
    assert err and "user_asked" in msg
    err, kit = _call(human, tok, "create_brand_kit", {"name": "Beta", "user_asked": True, "reason": "asked for a kit"})
    assert not err, kit
    err, msg = _call(human, tok, "set_active_brand_kit", {"kit_id": kit["id"]})
    assert err and "user_asked" in msg
    tools = {t["name"]: t for t in rpc(human, tok, "tools/list")["result"]["tools"]}
    for n in run_discipline.NEEDS_USER_ASKED:
        assert "user_asked" in tools[n]["inputSchema"]["properties"], n
    # the person in the app is never asked for it
    assert human.post("/api/templates/custom", json={"name": "Mine", "slides": [{"type": "front", "title": "x"}]}).status_code in (200, 404, 405)


# ------------------------------------------------------------------------------------------------ cleanup
def _age(store, table, hours=2):
    store.x(f"UPDATE {table} SET created=created-?" + (", updated=updated-?" if table == "agent_runs" else ""),
            (hours * 3600, hours * 3600) if table == "agent_runs" else (hours * 3600,))


def test_cleanup_lists_first_then_moves_to_the_bin_and_archives(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store, h = human.app.state.store, human.app.state.house
    uid = _uid(human)
    rid = _start(human, tok)["run_id"]
    err, car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2)})
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "left over"}, "media_urls": car["media_urls"]})
    err, up = _call(human, tok, "library_upload_url", {"url": IMG + "?stray=1"})  # an app upload no post uses
    assert not err, up
    sent = _start(human, tok, title="Sent one", new_post=True)["run_id"]  # one that went to Buffer: never touched
    store.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps({"packages": [{"channel_id": "ch_ig", "status": "sent_to_buffer",
                                                                                  "sent": {"mode": "draft"}}]}), sent))
    from PIL import Image  # noqa: F401
    from .conftest import image_bytes
    mine = h.library.save_bytes(Actor(uid), image_bytes(300, 300, "PNG", (1, 2, 3)), "mine.png")  # the person's own upload
    theirs = store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated,agent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                     ("someone-else", "client", "{}", "ready", "[]", "{}", 0, 0, 1))
    _age(store, "agent_runs")
    _age(store, "library_items")
    err, dry = _call(human, tok, "cleanup_agent_drafts", {})
    assert not err, dry
    assert dry["dry_run"] is True and dry["confirm_token"]
    assert [r["run_id"] for r in dry["runs"]] == [rid]
    listed = {i["library_id"] for i in dry["library_items"]}
    assert up["library_id"] in listed and mine["id"] not in listed
    assert all(r["app"] for r in dry["runs"] + dry["library_items"])
    # the dry run changed nothing
    assert store.q("SELECT status FROM agent_runs WHERE id=?", (rid,))[0]["status"] == "ready"
    assert store.q("SELECT COUNT(*) AS n FROM library_items WHERE storage='trash'")[0]["n"] == 0
    # another token can't use this confirm_token
    other = agent_token(human, policy="trusted", cap=100)
    err, msg = _call(human, other, "cleanup_agent_drafts", {"confirm_token": dry["confirm_token"]})
    assert err and "different caller" in msg
    err, done = _call(human, tok, "cleanup_agent_drafts", {"confirm_token": dry["confirm_token"]})
    assert not err, done
    assert done["archived_runs"] == [rid] and up["library_id"] in done["binned_items"]
    assert store.q("SELECT status FROM agent_runs WHERE id=?", (rid,))[0]["status"] == "archived"
    assert store.q("SELECT status FROM agent_runs WHERE id=?", (sent,))[0]["status"] != "archived"
    assert store.q("SELECT status FROM agent_runs WHERE id=?", (theirs,))[0]["status"] == "ready"
    assert store.q("SELECT storage FROM library_items WHERE id=?", (mine["id"],))[0]["storage"] == "chunks"
    gone = {r["id"] for r in store.q("SELECT id FROM library_items WHERE storage='trash'")}
    assert up["library_id"] in gone  # in the Bin, not deleted
    assert rid not in [r["id"] for r in human.get("/api/agent/runs").json()]  # hidden from the threads
    err, msg = _call(human, tok, "cleanup_agent_drafts", {"confirm_token": dry["confirm_token"]})
    assert err and "used or expired" in msg
    # restorable
    err, back = _call(human, tok, "restore_library_items", {"ids": [up["library_id"]]})
    assert not err, back
    assert store.q("SELECT storage FROM library_items WHERE id=?", (up["library_id"],))[0]["storage"] != "trash"


# ------------------------------------------------------------------------------------------------ paid / external APIs
@pytest.fixture
def fake_server_run(monkeypatch):
    calls = []
    from studio import house

    def start_run(self, a, body):
        calls.append(body)
        return {"id": 4242, "kind": "agent", "status": "running"}
    monkeypatch.setattr(house.House, "start_run", start_run)
    return calls


def test_paid_api_is_refused_until_the_person_approves_and_runs_once(human, fake_server_run, no_anthropic):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    args = {"query": "best headphones under 150", "server_ai": True}
    err, wait = _call(human, tok, "run_agent", args)
    assert not err and wait["needs_user_approval"] is True and wait["spent_usd"] == 0
    assert wait["estimate"]["provider"] == "HuhQueue server AI" and wait["estimate"]["usd_max"] > 0
    assert fake_server_run == [] and no_anthropic == []
    err, again = _call(human, tok, "run_agent", args)  # asking again is the same approval item
    assert again["approval_id"] == wait["approval_id"]
    items = human.get("/api/approvals").json()
    mine = [i for i in items if i["id"] == wait["approval_id"]]
    assert len(mine) == 1 and mine[0]["kind"] == "paid_api" and mine[0]["payload"]["tool"] == "run_agent"
    assert "best headphones" in mine[0]["payload"]["summary"]
    # an agent can never approve it: not over REST with its token, and no MCP tool approves a paid API (approve_link_ingest,
    # 5 Nov 2026, relays the person's rights statement for a video link only; a paid item's id answers 404 there)
    r = human.post(f"/api/approvals/{wait['approval_id']}/approve", headers={"Authorization": f"Bearer {tok}", "X-HQ-CSRF": ""},
                   cookies={}, json={})
    assert r.status_code == 403  # (a bare bearer client is checked in the next test)
    names = {t["name"] for t in mcp.TOOLS}
    assert {n for n in names if "approve" in n} == {"approve_link_ingest"}
    err, msg = _call(human, tok, "approve_link_ingest", {"approval_id": wait["approval_id"], "i_own_or_have_rights": True})
    assert err and "No pending video-link approval" in msg
    # the person approves in HuhQueue
    ok = human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).json()
    assert ok["status"] in ("approved",) and ok["kind"] == "paid_api"
    err, ran = _call(human, tok, "run_agent", args)
    assert not err and ran.get("id") == 4242 and len(fake_server_run) == 1
    err, used = _call(human, tok, "run_agent", {**args, "query": "best headphones under 150 "})  # a different call
    assert used["needs_user_approval"] is True and used["approval_id"] != wait["approval_id"]
    err, once = _call(human, tok, "run_agent", args)  # used up: the same call asks again
    assert once["needs_user_approval"] is True and len(fake_server_run) == 1


def test_a_channel_scoped_token_cannot_approve_a_paid_api_request(app, human, fake_server_run):
    from fastapi.testclient import TestClient
    tok = agent_token(human, policy="trusted", cap=100)
    err, wait = _call(human, tok, "run_agent", {"query": "x", "server_ai": True})
    scoped = human.post("/api/agent-tokens", json={"name": "Scoped", "policy": "trusted", "daily_cap": 10, "channels": ["ch_ig"]}).json()["token"]
    bare = TestClient(app, base_url="https://hq.test")  # no session cookie: only the bearer token
    for t in (scoped, tok):
        r = bare.post(f"/api/approvals/{wait['approval_id']}/approve", headers={"Authorization": f"Bearer {t}"}, json={})
        assert r.status_code == 403, r.text
        r = bare.patch("/api/agent-tokens/1", headers={"Authorization": f"Bearer {t}"}, json={"allow_paid_apis": True})
        assert r.status_code == 403, r.text
    assert human.get("/api/approvals").json()[0]["status"] == "pending"
    assert fake_server_run == []


def test_allow_paid_apis_is_off_by_default_and_only_the_person_turns_it_on(human, fake_server_run):
    tok = agent_token(human, policy="trusted", cap=100)
    t = [x for x in human.get("/api/safety").json()["tokens"] if x["name"] == "Claude"][0]
    assert t["allow_paid_apis"] is False
    r = human.patch(f"/api/agent-tokens/{t['id']}", json={"allow_paid_apis": True})
    assert r.status_code == 200 and r.json()["allow_paid_apis"] is True
    err, ran = _call(human, tok, "run_agent", {"query": "x", "server_ai": True})
    assert not err and ran.get("id") == 4242
    acts = [r["action"] for r in human.app.state.store.q("SELECT action FROM audit WHERE action='paid_api'")]
    assert acts  # written to Activity
    assert human.patch(f"/api/agent-tokens/{t['id']}", json={"allow_paid_apis": "yes"}).status_code == 400
    human.patch(f"/api/agent-tokens/{t['id']}", json={"allow_paid_apis": False})
    err, wait = _call(human, tok, "run_agent", {"query": "x", "server_ai": True})
    assert wait["needs_user_approval"] is True


def test_the_kill_switch_answers_before_the_gate(human, fake_server_run):
    tok = agent_token(human, policy="trusted", cap=100)
    human.post("/api/safety/kill", json={"on": True})
    err, msg = _call(human, tok, "run_agent", {"query": "x", "server_ai": True})
    assert err and "kill switch" in msg
    assert not human.get("/api/approvals").json()


def test_registered_external_tools_go_through_the_same_gate(human, monkeypatch):
    """A paid external API plugs in with consent.register: the generic gate holds any tool registered there."""
    monkeypatch.setitem(consent.PAID, "get_limits", {"provider": "ExampleAPI", "what": "a paid lookup", "estimate": lambda a: {"credits": 1}})
    tok = agent_token(human, policy="trusted", cap=100)
    err, wait = _call(human, tok, "get_limits", {})
    assert not err and wait["needs_user_approval"] is True and wait["estimate"] == {"provider": "ExampleAPI", "what": "a paid lookup", "credits": 1}
    human.post(f"/api/approvals/{wait['approval_id']}/approve", json={})
    err, out = _call(human, tok, "get_limits", {})
    assert not err and "needs_user_approval" not in out
    # a human in the app is never gated
    assert consent.gate(human.app.state.studio, Actor(_uid(human)), "get_limits", {}) is None


# ------------------------------------------------------------------------------------------------ parity
def test_every_tool_is_documented_and_the_new_ones_are_listed(human):
    tok = agent_token(human)
    names = [t["name"] for t in rpc(human, tok, "tools/list")["result"]["tools"]]
    assert {"start_post", "cleanup_agent_drafts"} <= set(names)
    doc = open("docs/MCP_TOOLS.md").read()
    missing = [n for n in names if f"`{n}`" not in doc]
    assert not missing, missing
    for n in ("render_carousel", "hero_edit", "edit_video", "render_reel", "split_video", "library_upload_url", "create_upload",
              "import_from_link", "create_package", "start_post"):
        t = next(x for x in mcp.TOOLS if x["name"] == n)
        assert "run_id" in t["inputSchema"]["properties"], n
    assert "store them in the Library" not in json.dumps(mcp.TOOLS)


# ------------------------------------------------------------------------------------------------ PR #104 review (CodeRabbit, 4 Nov 2026)
def _agent_actor(human, tok):
    store = human.app.state.store
    row = dict(store.q("SELECT * FROM agent_tokens ORDER BY id DESC LIMIT 1")[0])
    return Actor(_uid(human), "agent", row)


def test_parallel_identical_paid_asks_make_one_approval_item(human, monkeypatch):
    import threading
    tok = agent_token(human, policy="trusted", cap=100)
    a = _agent_actor(human, tok)
    studio = human.app.state.studio
    real = consent._rows

    def slow_rows(store, actor):  # widen the check-then-insert window
        out = real(store, actor)
        time.sleep(0.05)
        return out
    monkeypatch.setattr(consent, "_rows", slow_rows)
    got, gate = [], threading.Barrier(4)

    def ask():
        gate.wait()
        got.append(consent.gate(studio, a, "run_agent", {"query": "same thing", "server_ai": True})["approval_id"])
    ts = [threading.Thread(target=ask) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(got) == 4 and len(set(got)) == 1
    assert human.app.state.store.q("SELECT COUNT(*) AS n FROM approvals")[0]["n"] == 1


def test_a_token_cannot_flood_the_approvals_queue_and_post_approvals_stay_visible(human, fake_server_run):
    tok = agent_token(human, policy="trusted", cap=100)
    store, studio = human.app.state.store, human.app.state.studio
    uid = _uid(human)
    post_id = store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                      (uid, None, "Claude", json.dumps({"text": "a real post", "channel_ids": ["ch_ig"]}), "pending", time.time()))
    for i in range(consent.MAX_PENDING):
        err, w = _call(human, tok, "run_agent", {"query": f"q{i}", "server_ai": True})
        assert not err and w["needs_user_approval"] is True
    err, msg = _call(human, tok, "run_agent", {"query": "one too many", "server_ai": True})
    assert err and "too many requests waiting" in msg
    err, again = _call(human, tok, "run_agent", {"query": "q0", "server_ai": True})  # an existing ask still answers its item
    assert not err and again["needs_user_approval"] is True
    for i in range(60):  # paid rows from other apps never push a post approval out of the post list
        store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                (uid, 999, "Other", json.dumps({"kind": "paid_api", "tool": "run_agent", "args_hash": str(i)}), "pending", time.time()))
    posts = studio.approvals(Actor(uid), "pending", "post")
    assert [p["id"] for p in posts] == [post_id]
    assert all(p["kind"] == "paid_api" for p in studio.approvals(Actor(uid), "pending", "paid_api"))


def test_a_stale_second_approve_cannot_reset_a_used_approval(human, fake_server_run, no_anthropic):  # noqa: F811
    from studio.actions import ActionError
    tok = agent_token(human, policy="trusted", cap=100)
    store = human.app.state.store
    args = {"query": "approve twice", "server_ai": True}
    err, wait = _call(human, tok, "run_agent", args)
    stale = dict(store.q("SELECT * FROM approvals WHERE id=?", (wait["approval_id"],))[0])  # read while pending (a double submit)
    assert human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).json()["status"] == "approved"
    err, ran = _call(human, tok, "run_agent", args)
    assert not err and len(fake_server_run) == 1  # the approval is used up now
    with pytest.raises(ActionError) as e:
        consent.decide(store, Actor(_uid(human)), stale, True)
    assert e.value.status == 404
    assert store.q("SELECT result FROM approvals WHERE id=?", (wait["approval_id"],))[0]["result"]  # still used
    err, once = _call(human, tok, "run_agent", args)
    assert once["needs_user_approval"] is True and len(fake_server_run) == 1


def test_new_request_is_never_answered_by_an_earlier_replay(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    first = _start(human, tok, title="Same words")
    err, second = _call(human, tok, "start_post", {"title": "Same words", "new_request": True, "reason": "a new one"})
    assert not err and second["run_id"] != first["run_id"] and not second.get("idempotent_replay")
    err, third = _call(human, tok, "start_post", {"title": "Same words", "new_post": True, "user_asked": True, "reason": "again"})
    assert not err and third["run_id"] not in (first["run_id"], second["run_id"])


def test_undo_keeps_the_library_item_on_the_packages_output_slot(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store = human.app.state.store
    rid = _start(human, tok)["run_id"]
    err, a = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "a"), "output": 2})
    assert not err, a
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "A"}, "media_urls": a["media_urls"], "output": 2})
    err, b = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "b"), "output": 2})
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "B"}, "media_urls": b["media_urls"], "output": 2})
    err, back = _call(human, tok, "undo_package_media", {"run_id": rid})
    assert not err, back
    lib = store.q("SELECT meta FROM library_items WHERE run_id=? AND storage='media'", (rid,))
    metas = [json.loads(r["meta"]) for r in lib]
    assert [int(m.get("output") or 1) for m in metas] == [2]  # no stray output-1 item
    assert metas[0]["tokens"] == [u.rsplit("/m/", 1)[-1] for u in a["media_urls"]]


def test_a_render_finished_by_the_worker_callback_keeps_the_apps_agent_id(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store, house = human.app.state.store, human.app.state.studio.house
    rid = _start(human, tok)["run_id"]
    err, car = _call(human, tok, "render_carousel", {"slides": _slides(2, "w")})  # no run_id: not in the Library yet
    assert not err, car
    token = car["media_urls"][0].rsplit("/m/", 1)[-1]
    iid = house.library.register_run(Actor(_uid(human)), [{"token": token, "type": "photo"}], "image", rid, output=3)
    assert iid
    agent = store.q("SELECT agent_id FROM agent_runs WHERE id=?", (rid,))[0]["agent_id"]
    assert agent is not None and store.q("SELECT agent_id FROM library_items WHERE id=?", (iid,))[0]["agent_id"] == agent


# ------------------------------------------------------------------------------------------------ PR #104 review round 2
def test_create_package_without_media_urls_restages_the_current_render_not_a_replay(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "a")})
    err, p1 = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "Same caption"}, "output": 1})
    assert not err, p1
    err, b = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "b")})
    err, p2 = _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "Same caption"}, "output": 1})
    assert not err and not p2.get("idempotent_replay")
    run = human.get(f"/api/agent/runs/{rid}").json()
    assert run["result"]["packages"][0]["media_urls"] == b["media_urls"]


def test_format_switches_inside_the_replay_window_are_never_replayed(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    seen = []
    for fmt in ("reel", "carousel", "reel"):
        err, out = _call(human, tok, "start_post", {"run_id": rid, "format": fmt, "reason": "switch"})
        assert not err, out
        seen.append(bool(out.get("idempotent_replay")))
    assert seen == [False, False, False]


def test_undo_is_refused_on_a_post_already_sent_or_waiting_in_approvals(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store = human.app.state.store
    rid = _start(human, tok)["run_id"]
    err, car = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "a")})
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "A"}, "media_urls": car["media_urls"]})
    err, car2 = _call(human, tok, "render_carousel", {"run_id": rid, "slides": _slides(2, "b")})
    _call(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "B"}, "media_urls": car2["media_urls"]})
    row = store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]
    res = json.loads(row["result"])
    res["packages"][0]["status"] = "sent_to_buffer"  # what schedule_package leaves behind
    store.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), rid))
    err, msg = _call(human, tok, "undo_package_media", {"run_id": rid})
    assert err and ("already sent" in msg or "404" in msg or "not found" in msg.lower())
    after = json.loads(store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    assert after["packages"][0]["text"] == "B" and after["packages"][0]["status"] == "sent_to_buffer"
