"""MCP reliability, owner report 6 Nov 2026 (Ram's live session: ONE post for several channels from a topic and a launch
video went wrong in eight ways, then five more at 11:00). Each test fails on main and passes here.

(a) one recommended path: get_started, the make_a_post prompt/resource, ingest_video_link is One take only, post_* deprecated
(b) create_package per channel (channel_media), never wiping another channel's package
(c) no silent format fallback (create_package reel without a video, post_render_reel without a video)
(d) "downloadable" only after a probe on the worker
(e) prerequisites in every flow answer; post_* auto-runs read_url -> extract_facts
(f) replace_channel_media on start_post runs
(g) one rights step for every video link; an approval counts a day from the DECISION (approval 136)
(h) one request = one run: the warning on a quick second start_post
(i) drafts-only token: schedule fails fast with a structured error, policy said up front
(ii) the token policy switch: confirm, audit, humans only, in the UI
(iii) YouTube category / title / madeForKids from create_package and autofix_post (default 28)
(iv) no duplicate drafts across runs (update via editPost), delete_drafts scoped to this app's drafts
(v) never a dueAt in the past
"""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from studio import consent, link_probe, mcp, mcp_flow
from studio.actions import Actor

from .conftest import agent_token, mcp_call
from .test_mcp_client_brain import no_anthropic, page  # noqa: F401  (fixtures)
from .test_mcp_templates_locks import rpc

YT = "https://www.youtube.com/watch?v=-pt7OGoNZ7g"


def _call(client, tok, name, args=None, mid=1):
    res, out = mcp_call(client, tok, name, args or {}, mid=mid)
    return res, out


def _ok(client, tok, name, args=None):
    res, out = _call(client, tok, name, args)
    assert not res["isError"], out
    return out


def _err(client, tok, name, args=None):
    res, out = _call(client, tok, name, args)
    assert res["isError"], out
    return res["structuredContent"]


def _start(client, tok, title="Taan AI: music that listens", **kw):
    return _ok(client, tok, "start_post", {"title": title, "user_asked": True, "reason": "the person asked", **kw})


def _slides(tag):
    return [{"eyebrow": "AI", "headline": f"Taan Scores Music {tag}", "accent_word": "Music", "body": "It listens."},
            {"eyebrow": "WHY", "headline": f"Exact Timing {tag}", "body": "80% vs 22%."}]


def _uid(client):
    return client.app.state.store.q("SELECT id FROM users")[0]["id"]


def _tools(client, tok):
    return {t["name"]: t for t in rpc(client, tok, "tools/list")["result"]["tools"]}


# ------------------------------------------------------------------------------------------------ (a) one path
def test_one_recommended_path_and_ingest_is_one_take_only(human):
    tok = agent_token(human)
    tools = _tools(human, tok)
    ing = tools["ingest_video_link"]["description"]
    assert ing.startswith("ONE TAKE ONLY") and "NEVER use it for a normal post" in ing and "whenever" not in ing
    assert tools["clips_from_video"]["description"].startswith("ONE TAKE ONLY")
    assert tools["make_post"]["description"].startswith("DEPRECATED for AI apps")
    assert tools["post_render_reel"]["description"].startswith("Deprecated for AI apps")
    assert "Prerequisites:" in tools["create_package"]["description"] and "channel_media" in tools["create_package"]["inputSchema"]["properties"]
    gs = _ok(human, tok, "get_started")
    assert gs["steps"][0].startswith("1. start_post") and "ingest_video_link / clips_from_video / split_video" in gs["not_for_a_post"]
    assert gs["prerequisites"]["render_reel"]
    prompts = rpc(human, tok, "prompts/list")["result"]["prompts"]
    assert "make_a_post" in [p["name"] for p in prompts]
    got = rpc(human, tok, "prompts/get", {"name": "make_a_post", "arguments": {"request": "one post, 3 channels"}})["result"]
    assert "start_post" in got["messages"][0]["content"]["text"]
    md = rpc(human, tok, "resources/read", {"uri": "huhqueue://workflow/make-a-post"})["result"]["contents"][0]["text"]
    assert md.startswith("# Make a post") and "channel_media" in md
    ins = rpc(human, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "c", "version": "1"}})
    text = ins["result"]["instructions"]
    assert "get_started" in text and "call ingest_video_link, ask them" not in text
    # ingest_video_link inside a post: refused with the right tool named
    sc = _err(human, tok, "ingest_video_link", {"url": YT, "run_id": 5})
    assert sc["code"] == "wrong_tool" and any("render_reel" in s for s in sc["next_steps"])
    assert mcp_flow.BEFORE["ingest_video_link"].startswith("One take from a video LINK")


# ------------------------------------------------------------------------------------------------ (b) per channel
def test_create_package_per_channel_media_never_wipes_another_channel(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    c1 = _ok(human, tok, "render_carousel", {"run_id": rid, "slides": _slides("A"), "output": 1})
    c2 = _ok(human, tok, "render_carousel", {"run_id": rid, "slides": _slides("B"), "output": 2})
    assert c1["media_urls"] != c2["media_urls"]
    # one channel per call (what Claude did): the second call must keep the first channel's package
    _ok(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "IG caption"}, "channel_media": {"instagram": {"output": 1}}})
    pk = _ok(human, tok, "create_package", {"run_id": rid, "captions": {"linkedin": "LI caption"}, "channel_media": {"linkedin": {"output": 2}}})
    assert pk["kept_channels"] == ["ch_ig"]
    run = human.get(f"/api/agent/runs/{rid}").json()
    by = {p["channel_id"]: p for p in run["result"]["packages"]}
    assert set(by) == {"ch_ig", "ch_li"}
    assert by["ch_ig"]["media_urls"] == c1["media_urls"] and by["ch_li"]["media_urls"] == c2["media_urls"]
    assert by["ch_ig"]["text"] == "IG caption" and by["ch_li"]["text"] == "LI caption"
    # all channels in ONE call, each its own media and cover
    pk = _ok(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "a", "linkedin": "b", "twitter": "c"},
                                            "channel_media": {"instagram": {"output": 2, "cover": {"url": "https://cdn.example.com/a.png"}},
                                                              "linkedin": {"output": 1}}})
    run = human.get(f"/api/agent/runs/{rid}").json()
    by = {p["channel_id"]: p for p in run["result"]["packages"]}
    assert by["ch_ig"]["media_urls"] == c2["media_urls"] and by["ch_li"]["media_urls"] == c1["media_urls"]
    assert by["ch_ig"]["cover_url"] == "https://cdn.example.com/a.png" and by["ch_x"]["format"] == "text"
    assert human.app.state.store.q("SELECT COUNT(*) AS n FROM agent_runs")[0]["n"] == 1  # still ONE run
    # replace_all drops the channels not named; bad shapes are 400
    _ok(human, tok, "create_package", {"run_id": rid, "captions": {"twitter": "only X"}, "replace_all": True})
    run = human.get(f"/api/agent/runs/{rid}").json()
    assert [p["channel_id"] for p in run["result"]["packages"]] == ["ch_x"]
    for bad in ({"instagram": "x"}, {"instagram": {"media_urls": "nope"}}, {"instagram": {"oops": 1}}, ["x"]):
        sc = _err(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "x"}, "channel_media": bad})
        assert sc["status"] == 400
    sc = _err(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "x"}, "channel_media": {"linkedin": {"output": 1}}})
    assert "no caption" in sc["error"]


# ------------------------------------------------------------------------------------------------ (c) no silent fallback
def test_reel_is_a_video_or_a_structured_error_never_slides(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    sc = _err(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "x"}, "format": "reel",
                                             "media_urls": ["https://cdn.example.com/a.png"]})
    assert sc["code"] == "reel_needs_video" and any("create_send_code" in s for s in sc["next_steps"])
    assert human.get(f"/api/agent/runs/{rid}").json()["result"]["packages"] == []  # nothing staged
    # the deprecated agent family: post_render_reel on a run with no video answers an error, the channels keep nothing
    tok = agent_token(human, policy="trusted", cap=100)
    run = _ok(human, tok, "make_post", {"prompt": "post this https://news.example.com/ai-launch", "brain": "external"})
    assert run.get("deprecated")
    sc = _err(human, tok, "post_render_reel", {"run_id": run["id"]})
    assert sc["code"] == "no_video" and sc["produced"] == "nothing" and sc["format_requested"] == "reel"
    after = human.get(f"/api/agent/runs/{run['id']}").json()["result"]
    assert not any(v.get("format") == "carousel" for v in (after.get("channel_media") or {}).values())


def test_render_status_failed_job_has_upload_next_steps(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    store = human.app.state.store
    jid = store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,error,created,updated) VALUES(?,?,?,?,?,?,?)",
                  (_uid(human), None, json.dumps({"kind": "media_render", "for_run": 7, "for_output": 2}), "failed",
                   "This video is not available", time.time(), time.time()))
    st = _ok(human, tok, "render_status", {"job_id": jid})
    assert st["status"] == "failed" and st["code"] == "render_failed" and "create_send_code(run_id=7)" in st["next_steps"][0]


# ------------------------------------------------------------------------------------------------ (d) probe on the worker
def _found(monkeypatch):
    from studio import mcp_client_brain
    vf = {"status": "found", "video": {"url": YT, "platform": "youtube", "downloadable": True}, "launch_video": None, "credit": "Video: Taan AI",
          "maker": "Taan AI", "website": "", "product": {}, "images": [], "steps": [], "plan_line": "Video found", "ask": "",
          "upload_limit_mb": 100, "upload_accept": "MP4"}
    monkeypatch.setattr(mcp_client_brain, "_video_first", lambda studio, a, q, **k: dict(vf))


def _worker(app, monkeypatch, answer):
    """The worker takes the probe job and posts `answer` (or nothing when answer is None)."""
    house = app.state.house
    monkeypatch.setattr(house, "video_mode", lambda: "github")

    def dispatch(jid, workflow="render.yml", inputs=None, ttl=1800):
        if answer is not None:
            house.finish_video(jid, None, None, body={"probe": answer})
        return True, "InstaBox"
    monkeypatch.setattr(house, "_dispatch_github", dispatch)


def test_find_video_is_downloadable_only_after_a_worker_probe(human, monkeypatch):
    tok = agent_token(human)
    _found(monkeypatch)
    _worker(human.app, monkeypatch, {"ok": False, "reason": "This video is not available", "code": "gone"})
    out = _ok(human, tok, "find_video", {"query": YT})
    assert out["status"] == "found_not_downloadable" and out["video"]["downloadable"] is False
    assert "not available" in out["probe"]["reason"] and any("create_send_code" in s for s in out["next_steps"])
    # the answer is kept: the same link again starts no second probe job
    n = human.app.state.store.q("SELECT COUNT(*) AS n FROM render_jobs")[0]["n"]
    assert _ok(human, tok, "find_video", {"query": YT})["probe"]["cached"] is True
    assert human.app.state.store.q("SELECT COUNT(*) AS n FROM render_jobs")[0]["n"] == n


def test_find_video_found_when_the_probe_reads_the_stream(human, monkeypatch):
    tok = agent_token(human)
    _found(monkeypatch)
    _worker(human.app, monkeypatch, {"ok": True, "via": "yt-dlp", "bytes_read": 65536})
    out = _ok(human, tok, "find_video", {"query": YT})
    assert out["status"] == "found" and out["video"]["downloadable"] is True and out["video"]["probed"] is True


def test_a_worker_that_does_not_answer_is_not_downloadable(human, monkeypatch):
    tok = agent_token(human)
    _found(monkeypatch)
    _worker(human.app, monkeypatch, None)
    monkeypatch.setattr(link_probe, "WAIT", 0.2)
    out = _ok(human, tok, "find_video", {"query": YT})
    assert out["status"] == "found_not_downloadable" and out["probe"]["status"] == "pending" and out["probe"]["job_id"]


def test_probe_link_on_the_worker_reports_the_platform_refusal(monkeypatch):
    import sys
    import types

    from studio import netguard
    from studio.media import download

    class YDL:
        def __init__(self, opts):
            assert opts["skip_download"] is True and opts["allowed_extractors"]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            raise RuntimeError("ERROR: [youtube] -pt7OGoNZ7g: This video is not available")
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=YDL))
    monkeypatch.setattr(netguard, "check_url", lambda url, *a, **k: url)
    out = download.probe_link(YT)
    assert out["ok"] is False and out["via"] == "yt-dlp"


# ------------------------------------------------------------------------------------------------ (e) prerequisites
def test_answers_list_prerequisites_and_post_steps_run_cheap_ones(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    pk = _ok(human, tok, "create_package", {"run_id": rid, "captions": {"twitter": "x"}})
    assert pk["prerequisites"] == mcp_flow.PREREQS["create_package"]
    sc = _err(human, tok, "create_package", {"run_id": rid, "captions": "nope"})
    assert sc["prerequisites"]
    tok2 = agent_token(human, policy="trusted", cap=100)
    run = _ok(human, tok2, "make_post", {"prompt": "post this https://news.example.com/ai-launch", "brain": "external"})
    out = _ok(human, tok2, "post_extract_facts", {"run_id": run["id"]})  # read_url first, by itself
    assert out["auto_ran"] == ["read_url"] and out["deprecated"]
    steps = [st.get("tool") or st.get("key") for st in human.get(f"/api/agent/runs/{run['id']}").json()["steps"]]
    assert "read_url" in " ".join(map(str, steps)) or out["auto_ran"]
    # a post_* step on a start_post run: the right tools named
    sc = _err(human, tok, "post_render_reel", {"run_id": rid})
    assert sc["code"] == "wrong_run_type"


# ------------------------------------------------------------------------------------------------ (f) replace_channel_media
def test_replace_channel_media_on_a_start_post_run(human, page):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    rid = _start(human, tok)["run_id"]
    c1 = _ok(human, tok, "render_carousel", {"run_id": rid, "slides": _slides("A"), "output": 1})
    _ok(human, tok, "create_package", {"run_id": rid, "captions": {"instagram": "a", "linkedin": "b"}, "output": 1})
    up = _ok(human, tok, "library_upload_url", {"url": "https://news.example.com/hero.jpg", "run_id": rid})
    out = _ok(human, tok, "replace_channel_media", {"run_id": rid, "channel": "linkedin", "media_token": up["media_token"]})
    assert out["channel_id"] == "ch_li" and out["versions_kept"] >= 1
    by = {p["channel_id"]: p for p in human.get(f"/api/agent/runs/{rid}").json()["result"]["packages"]}
    assert by["ch_ig"]["media_urls"] == c1["media_urls"] and by["ch_li"]["media_urls"] != c1["media_urls"]
    sc = _err(human, tok, "replace_channel_media", {"run_id": rid, "channel": "linkedin"})
    assert sc["code"] == "media_token_required"
    sc = _err(human, tok, "replace_channel_media", {"run_id": rid, "channel": "twitter", "media_token": up["media_token"]})
    assert sc["code"] == "channel_not_staged"
    assert _err(human, tok, "replace_channel_media", {"run_id": 99999, "channel": "linkedin"})["status"] == 404


# ------------------------------------------------------------------------------------------------ (g) rights
def test_an_approval_counts_a_day_from_the_decision_and_the_app_can_find_it(human, monkeypatch):
    from .test_take_opus import _github, _hosts
    _hosts(monkeypatch)
    _github(human.app, monkeypatch)
    tok = agent_token(human)
    rid = _start(human, tok)["run_id"]
    out = _ok(human, tok, "edit_video", {"url": YT, "run_id": rid, "rights": "news_credit", "credit": "Video: Taan AI"})
    assert out["needs_user_approval"] is True and out["run_id"] == rid  # the same rights step as One take
    aid = out["approval_id"]
    store = human.app.state.store
    store.x("UPDATE approvals SET created=? WHERE id=?", (time.time() - 23 * 3600, aid))  # asked 23 h ago (approval 136)
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True})
    assert r.status_code == 200, r.text
    store.x("UPDATE approvals SET created=? WHERE id=?", (time.time() - 25 * 3600, aid))  # now past a day since the ASK
    waiting = _ok(human, tok, "list_pending_approvals")["approved_waiting"]
    assert [w["approval_id"] for w in waiting] == [aid] and waiting[0]["run_id"] == rid
    thread = human.get(f"/api/agent/runs/{rid}").json()["result"]["thread"]
    assert any("Approved the video link" in t["text"] for t in thread)
    got = _ok(human, tok, "edit_video", {"url": YT, "run_id": rid, "rights": "news_credit", "credit": "Video: Taan AI", "approval_id": aid})
    assert got.get("job_id"), got
    assert got["rights"]["basis"] == "approved" and got["rights"]["approval_id"] == aid
    spec = json.loads(store.q("SELECT spec FROM render_jobs WHERE id=?", (got["job_id"],))[0]["spec"])
    assert spec["candidate"]["url"] == YT and spec["source"] == ""  # the worker fetches the post itself, nothing imported here


def test_post_render_reel_of_a_third_party_link_takes_the_same_rights_step(human, page, monkeypatch):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    run = _ok(human, tok, "make_post", {"prompt": "post this https://news.example.com/ai-launch", "brain": "external"})
    h = human.app.state.house
    a = Actor(_uid(human))
    r = h.run(a, run["id"])
    r["result"]["found_media"] = {"best_downloadable": {"url": YT, "platform": "youtube"}}
    h._save(run["id"], r["steps"], r["result"])
    out = _ok(human, tok, "post_render_reel", {"run_id": run["id"], "rights_confirmed": True})  # the app's own say-so isn't it
    assert out["needs_user_approval"] is True and out["tool"] == "post_render_reel"


# ------------------------------------------------------------------------------------------------ (h) one request, one run
def test_a_quick_second_start_post_is_warned_about_one_run(human):
    tok = agent_token(human, policy="trusted", cap=100)
    first = _start(human, tok)["run_id"]
    second = _start(human, tok, title="Taan AI: LinkedIn version")
    assert second["previous_run_id"] == first and "One user request = one run" in second["warning"]
    third = _start(human, tok, title="Something new", new_request=True)
    assert "warning" not in third


# ------------------------------------------------------------------------------------------------ (i) drafts-only
def test_drafts_only_token_fails_fast_before_any_buffer_call(human, fake, page):  # noqa: F811
    tok = agent_token(human, policy="draft", cap=100)
    rid = _start(human, tok)["run_id"]
    _ok(human, tok, "create_package", {"run_id": rid, "captions": {"linkedin": "Hello LinkedIn"}})
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    calls = len(fake.calls)
    sc = _err(human, tok, "schedule_package", {"run_id": rid, "channel_id": "ch_li", "mode": "schedule", "due_at": due})
    assert sc["code"] == "policy_drafts_only" and sc["status"] == 403 and "Agents › token policy" in sc["error"]
    assert sc["token_policy"]["policy"] == "draft" and sc["token_policy"]["change_at"].startswith("https://hq.test/")
    sc = _err(human, tok, "preview_post", {"channel_id": "ch_li", "text": "x", "mode": "schedule", "due_at": due})
    assert sc["code"] == "policy_drafts_only"
    assert not fake.created and not any("createPost" in str(c) for c in fake.calls[calls:])
    pv = _ok(human, tok, "preview_post", {"channel_id": "ch_li", "text": "x", "mode": "draft"})
    assert pv["token_policy"]["allowed_modes"] == ["draft"]
    d = _ok(human, tok, "describe_tool", {"name": "schedule_package"})
    assert d["token_policy"]["policy"] == "draft"


# ------------------------------------------------------------------------------------------------ (ii) policy switch
def test_token_policy_switch_needs_a_human_a_confirm_and_is_audited(human):
    tok = agent_token(human, policy="draft")
    tid = human.get("/api/safety").json()["tokens"][0]["id"]
    assert human.patch(f"/api/agent-tokens/{tid}", json={"policy": "trusted"}).status_code == 400  # no confirm
    assert human.patch(f"/api/agent-tokens/{tid}", json={"policy": "nope", "confirm": True}).status_code == 400
    r = human.patch(f"/api/agent-tokens/{tid}", json={"policy": "trusted", "confirm": True})
    assert r.status_code == 200 and r.json()["policy"] == "trusted" and r.json()["was"] == "draft"
    st = human.app.state.store
    row = st.q("SELECT detail FROM audit WHERE action='set_agent_policy' ORDER BY id DESC LIMIT 1")[0]
    assert json.loads(row["detail"]) == {"id": tid, "from": "draft", "to": "trusted"}
    assert human.get("/api/safety").json()["tokens"][0]["policy"] == "trusted"
    assert human.patch("/api/agent-tokens/99999", json={"policy": "draft", "confirm": True}).status_code == 404
    # an agent can never change its own policy
    a = Actor(_uid(human), "agent", dict(st.q("SELECT * FROM agent_tokens WHERE id=?", (tid,))[0]))
    with pytest.raises(Exception):
        human.app.state.studio.set_agent_policy(a, tid, "trusted", True)
    js = open("studio/static/app.js").read()
    assert 'data-testid="token-policy"' in js and "Change policy" in js and "confirm: true" in js


# ------------------------------------------------------------------------------------------------ (iii) YouTube fields
def test_youtube_category_title_and_made_for_kids(human, page):  # noqa: F811
    from studio import actions, preflight
    tok = agent_token(human, policy="trusted", cap=100)
    ch = {"id": "ch_yt", "service": "youtube", "name": "yt"}
    pf = preflight.check(ch, "Taan AI", ["https://cdn.example.com/v.mp4"], "queue", {}) if hasattr(preflight, "check") else None
    if pf:
        cat = [f for f in pf["findings"] if f.get("rule") == "field.youtube.category" or "category" in str(f)]
        assert cat and "28" in json.dumps(cat)
    _, opts, applied = preflight.apply_text_fixes(ch, "Taan AI", {}, ["youtube_category"])
    assert opts["category_id"] == "28" and applied == ["youtube_category"]
    inp = actions.build_post_input({"id": "ch_yt", "service": "youtube"}, "Taan AI", "https://cdn.example.com/v.mp4", "draft", None, None,
                                   {"category_id": "28", "made_for_kids": True, "title": "T"})
    md = inp["metadata"]["youtube"]
    assert md["categoryId"] == "28" and md["madeForKids"] is True and md["title"] == "T"
    from studio.mcp_client_brain import youtube_options
    assert youtube_options({"categoryId": "28", "title": "Hi", "madeForKids": False}) == {"category_id": "28", "title": "Hi",
                                                                                           "made_for_kids": False}
    for bad in ({"categoryId": "abc"}, {"title": ""}, {"madeForKids": "yes"}):
        with pytest.raises(Exception):
            youtube_options(bad)
    tools = _tools(human, tok)
    assert "youtube" in tools["create_package"]["inputSchema"]["properties"] and "youtube" in tools["autofix_post"]["inputSchema"]["properties"]


# ------------------------------------------------------------------------------------------------ (iv) duplicates
def _draft_run(human, tok, fake, title):
    rid = _start(human, tok, title=title, new_request=True)["run_id"]
    _ok(human, tok, "create_package", {"run_id": rid, "captions": {"linkedin": f"{title} caption"}})
    return rid


def test_a_second_run_of_the_same_story_updates_the_earlier_draft(human, fake, page):  # noqa: F811
    tok = agent_token(human, policy="draft", cap=100)
    r1 = _draft_run(human, tok, fake, "Taan AI hits exact timing")
    out = _ok(human, tok, "schedule_package", {"run_id": r1, "channel_id": "ch_li", "mode": "draft"})
    pid = out["post"]["id"]
    fake.posts.append({"id": pid, "text": "x", "status": "draft", "channelId": "ch_li"})
    assert len(fake.created) == 1
    r2 = _draft_run(human, tok, fake, "Taan AI hits exact timing")
    sc = _err(human, tok, "schedule_package", {"run_id": r2, "channel_id": "ch_li", "mode": "draft"})
    assert sc["code"] == "duplicate_draft" and sc["duplicate_of"]["buffer_post_id"] == pid and sc["choices"] == ["update", "new"]
    assert len(fake.created) == 1  # nothing created
    up = _ok(human, tok, "schedule_package", {"run_id": r2, "channel_id": "ch_li", "mode": "draft", "on_duplicate": "update"})
    assert up["status"] == "updated" and up["replaced_run_id"] == r1 and len(fake.created) == 1
    assert fake.edits and fake.edits[-1]["id"] == pid
    # delete_drafts: this app's drafts only, dry run then confirm; another app's draft is never listed
    other = agent_token(human, policy="draft", cap=100)
    r3 = _draft_run(human, other, fake, "Something else entirely")
    o = _ok(human, other, "schedule_package", {"run_id": r3, "channel_id": "ch_li", "mode": "draft"})
    fake.posts.append({"id": o["post"]["id"], "text": "y", "status": "draft", "channelId": "ch_li"})
    dry = _ok(human, tok, "delete_drafts")
    assert [d["buffer_post_id"] for d in dry["drafts"]] == [pid] and dry["confirm_token"]
    done = _ok(human, tok, "delete_drafts", {"confirm_token": dry["confirm_token"]})
    assert done["deleted"] == [pid] and fake.deleted == [pid]
    assert _err(human, tok, "delete_drafts", {"confirm_token": dry["confirm_token"]})["status"] == 410  # single use


def test_delete_drafts_never_touches_a_post_that_was_scheduled_meanwhile(human, fake, page):  # noqa: F811
    tok = agent_token(human, policy="draft", cap=100)
    r1 = _draft_run(human, tok, fake, "A story")
    pid = _ok(human, tok, "schedule_package", {"run_id": r1, "channel_id": "ch_li", "mode": "draft"})["post"]["id"]
    fake.posts.append({"id": pid, "text": "x", "status": "scheduled", "channelId": "ch_li"})  # the person scheduled it in Buffer
    dry = _ok(human, tok, "delete_drafts")
    done = _ok(human, tok, "delete_drafts", {"confirm_token": dry["confirm_token"]})
    assert done["deleted"] == [] and done["refused"][0]["status"] == 409 and not fake.deleted


# ------------------------------------------------------------------------------------------------ (v) never a past dueAt
def test_never_a_due_at_in_the_past(human, fake, page, monkeypatch):  # noqa: F811
    tok = agent_token(human, policy="trusted", cap=100)
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    sc = _err(human, tok, "preview_post", {"channel_id": "ch_li", "text": "x", "mode": "schedule", "due_at": past})
    assert sc["code"] == "due_at_past" and sc["now_utc"]
    # a confirm made for the future that is submitted after its time: refused before Buffer
    soon = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()
    pv = _ok(human, tok, "preview_post", {"channel_id": "ch_li", "text": "x", "mode": "schedule", "due_at": soon})
    from studio import actions
    real = actions.datetime

    class Later(real):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(minutes=5)
    monkeypatch.setattr(actions, "datetime", Later)
    sc = _err(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert sc["code"] == "due_at_past" and not fake.created


# ------------------------------------------------------------------------------------------------ PR #139 gaps (6 Nov 2026)
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _fetch(human, monkeypatch, body):
    calls = []

    def fake(url, limit=None, *a, **k):
        calls.append(url)
        return body
    monkeypatch.setattr(human.app.state.house, "_fetch_bytes", fake)
    return calls


def _approve(human, aid):
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True})
    assert r.status_code == 200, r.text


def test_library_upload_url_video_link_takes_the_rights_step(human, monkeypatch):
    tok = agent_token(human)
    rid = _start(human, tok)["run_id"]
    calls = _fetch(human, monkeypatch, MP4)
    url = "https://cdn.example.com/launch.mp4"
    out = _ok(human, tok, "library_upload_url", {"url": url, "run_id": rid})
    assert out["needs_user_approval"] is True and out["tool"] == "library_upload_url" and out["run_id"] == rid
    assert calls == []  # nothing fetched before the person's yes
    store = human.app.state.store
    assert store.q("SELECT COUNT(*) AS n FROM library_items")[0]["n"] == 0
    _approve(human, out["approval_id"])
    got = _ok(human, tok, "library_upload_url", {"url": url, "run_id": rid, "approval_id": out["approval_id"]})
    assert got["media_token"] and got["type"] == "video" and got["rights"]["approval_id"] == out["approval_id"]
    assert calls == [url]
    # the approval is used once: the same link again asks again
    again = _ok(human, tok, "library_upload_url", {"url": url, "run_id": rid})
    assert again["needs_user_approval"] is True


def test_library_upload_url_extensionless_video_is_held_and_nothing_is_saved(human, monkeypatch):
    tok = agent_token(human)
    _fetch(human, monkeypatch, MP4)
    store = human.app.state.store
    before = store.q("SELECT COUNT(*) AS n FROM media")[0]["n"]
    out = _ok(human, tok, "library_upload_url", {"url": "https://cdn.example.com/v?id=7"})
    assert out["needs_user_approval"] is True and out["saved"] is False and "nothing was saved" in out["message"]
    assert store.q("SELECT COUNT(*) AS n FROM media")[0]["n"] == before


def test_library_upload_url_photo_and_platform_page(human, monkeypatch):
    tok = agent_token(human)
    _fetch(human, monkeypatch, PNG)
    img = _ok(human, tok, "library_upload_url", {"url": "https://cdn.example.com/hero.png"})
    assert img["media_token"] and "needs_user_approval" not in img  # a photo needs no rights step
    sc = _err(human, tok, "library_upload_url", {"url": YT})
    assert sc["code"] == "wrong_tool" and any("render_reel" in s for s in sc["next_steps"])
    tools = _tools(human, tok)
    assert "approval_id" in tools["library_upload_url"]["inputSchema"]["properties"]
    assert "rights step" in tools["library_upload_url"]["description"]


def _ready_run(human, tok, prompt):
    run = _ok(human, tok, "make_post", {"prompt": prompt, "brain": "external"})
    h = human.app.state.house
    a = Actor(_uid(human))
    r = h.run(a, run["id"])
    r["result"]["packages"] = [{"channel_id": "ch_li", "service": "linkedin", "text": "Acme launches its AI card", "media_urls": [],
                                "status": "ready", "idempotency_key": f"k{run['id']}", "options": {}}]
    h._save(run["id"], r["steps"], r["result"], "ready")
    return run["id"]


def test_post_save_buffer_drafts_runs_the_duplicate_draft_guard(human, fake, page):  # noqa: F811
    tok = agent_token(human, policy="draft", cap=100)
    r1 = _ready_run(human, tok, "Save as drafts: https://news.example.com/ai-launch")
    out = _ok(human, tok, "post_save_buffer_drafts", {"run_id": r1})
    pid = out["drafts"][0]["buffer_post_id"]
    assert pid and len(fake.created) == 1 and out["deprecated"]
    fake.posts.append({"id": pid, "text": "x", "status": "draft", "channelId": "ch_li"})
    r2 = _ready_run(human, tok, "Put it in Buffer as a draft https://news.example.com/ai-launch")
    sc = _err(human, tok, "post_save_buffer_drafts", {"run_id": r2})
    assert sc["code"] == "duplicate_draft" and sc["duplicate_of"]["buffer_post_id"] == pid and sc["choices"] == ["update", "new"]
    assert len(fake.created) == 1  # nothing created
    assert _err(human, tok, "post_save_buffer_drafts", {"run_id": r2, "on_duplicate": "maybe"})["status"] == 400
    assert _err(human, tok, "post_save_buffer_drafts", {"run_id": r2, "channel_ids": "ch_li"})["status"] == 400
    up = _ok(human, tok, "post_save_buffer_drafts", {"run_id": r2, "on_duplicate": "update"})
    assert up["drafts"] == [{"service": "linkedin", "status": "updated", "buffer_post_id": pid, "replaced_run_id": r1}]
    assert len(fake.created) == 1 and fake.edits and fake.edits[-1]["id"] == pid
    # 'new' on a third run: a second draft, only because the app said so
    r3 = _ready_run(human, tok, "Drafts please: https://news.example.com/ai-launch")
    new = _ok(human, tok, "post_save_buffer_drafts", {"run_id": r3, "on_duplicate": "new"})
    assert len(fake.created) == 2 and new["drafts"][0]["buffer_post_id"]
    tools = _tools(human, tok)
    assert tools["post_save_buffer_drafts"]["inputSchema"]["properties"]["on_duplicate"]["enum"] == ["update", "new"]
    assert "duplicate_draft" in tools["post_save_buffer_drafts"]["description"]


def test_cold_worker_pending_probe_is_documented_and_says_wait_first(human, monkeypatch):
    tok = agent_token(human)
    _found(monkeypatch)
    _worker(human.app, monkeypatch, None)
    monkeypatch.setattr(link_probe, "WAIT", 0.2)
    out = _ok(human, tok, "find_video", {"query": YT})
    assert out["probe"]["status"] == "pending" and out["video"]["probed"] is False
    assert out["next_steps"][0].startswith(f"render_status(job_id={out['probe']['job_id']})")
    tools = _tools(human, tok)
    for n in ("find_video", "research_link", "render_status"):
        assert "pending" in tools[n]["description"], n
    gs = _ok(human, tok, "get_started")
    assert "probe.status 'pending'" in gs["steps"][1]


def test_delete_drafts_token_claim_is_atomic(human, fake, page, monkeypatch):  # noqa: F811
    """Two calls that both read the token as unused: only the one whose conditional UPDATE changes a row deletes."""
    tok = agent_token(human, policy="draft", cap=100)
    r1 = _draft_run(human, tok, fake, "Race story")
    pid = _ok(human, tok, "schedule_package", {"run_id": r1, "channel_id": "ch_li", "mode": "draft"})["post"]["id"]
    fake.posts.append({"id": pid, "text": "x", "status": "draft", "channelId": "ch_li"})
    dry = _ok(human, tok, "delete_drafts")
    store = human.app.state.studio.store
    store.x("UPDATE confirms SET used=1")  # the other caller won the race
    real_q = store.q

    def stale(sql, args=()):
        rows = real_q(sql, args)
        if "FROM confirms" in sql:
            return [dict(r, used=0) for r in rows]  # what this caller read before the other one claimed it
        return rows
    monkeypatch.setattr(store, "q", stale)
    assert _err(human, tok, "delete_drafts", {"confirm_token": dry["confirm_token"]})["status"] == 410
    assert fake.deleted == []


def test_delete_drafts_buffer_error_on_one_draft_keeps_going(human, fake, page, monkeypatch):  # noqa: F811
    from studio.buffer_api import BufferError
    tok = agent_token(human, policy="draft", cap=100)
    pids = []
    for title in ("First story here", "Second different story"):
        rid = _draft_run(human, tok, fake, title)
        pid = _ok(human, tok, "schedule_package", {"run_id": rid, "channel_id": "ch_li", "mode": "draft"})["post"]["id"]
        fake.posts.append({"id": pid, "text": title, "status": "draft", "channelId": "ch_li"})
        pids.append(pid)
    dry = _ok(human, tok, "delete_drafts")
    assert sorted(d["buffer_post_id"] for d in dry["drafts"]) == sorted(pids)
    buf = human.app.state.studio.buffer
    real = buf.delete_post
    bad = dry["drafts"][0]["buffer_post_id"]

    def flaky(uid, post_id):
        if post_id == bad:
            raise BufferError("Buffer timed out", 503)
        return real(uid, post_id)
    monkeypatch.setattr(buf, "delete_post", flaky)
    done = _ok(human, tok, "delete_drafts", {"confirm_token": dry["confirm_token"]})
    good = [p for p in pids if p != bad]
    assert done["deleted"] == good and fake.deleted == good
    assert done["unknown"][0]["buffer_post_id"] == bad and done["unknown"][0]["outcome"] == "unknown"
