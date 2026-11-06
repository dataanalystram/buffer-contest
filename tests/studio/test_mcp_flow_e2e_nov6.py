"""End-to-end MCP pass (owner ask, 6 Nov 2026): one topic -> one run -> per-channel media for 4 channels -> preflight ->
drafts mode and schedule mode, every step asserted, over the real /mcp endpoint with the fake Buffer. Each step is written
to HQ_E2E_REPORT (JSON lines) when that is set, for work/review/mcp_e2e.md."""
import json
import os
from datetime import datetime, timedelta, timezone

from studio.actions import Actor

from . import conftest
from .conftest import agent_token, mcp_call
from .test_mcp_client_brain import no_anthropic, page  # noqa: F401  (fixtures)
from .test_mcp_templates_locks import rpc

YT_CH = {"id": "ch_yt", "name": "Huhbyte", "displayName": "Huhbyte", "service": "youtube", "avatar": "", "isDisconnected": False,
         "isQueuePaused": False}
URL = "https://news.example.com/ai-launch"
LOG: list[dict] = []


def step(name, ok, detail):
    LOG.append({"step": name, "ok": bool(ok), "detail": detail})
    path = os.environ.get("HQ_E2E_REPORT")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps(LOG[-1], default=str) + "\n")
    assert ok, (name, detail)


def call(client, tok, name, args=None):
    res, out = mcp_call(client, tok, name, args or {})
    return res, out


def ok(client, tok, name, args=None):
    res, out = call(client, tok, name, args)
    assert not res["isError"], (name, out)
    return out


def _video(human, name):
    """A rendered Reel as render_status would hand it back (the test worker renders nothing)."""
    uid = human.app.state.store.q("SELECT id FROM users")[0]["id"]
    h = human.app.state.house
    tok = h.put_media(Actor(uid), b"\x00\x00\x00\x18ftypmp42" + os.urandom(2048), name, kind="render",
                      meta={"type": "video", "width": 1080, "height": 1920, "duration": 30.0})
    return h.media_url(tok)


def _four_channel_post(human, tok, title, **kw):
    st = ok(human, tok, "start_post", {"title": title, "user_asked": True, "reason": "one post, four channels",
                                       "channel_ids": ["ch_ig", "ch_li", "ch_x", "ch_yt"], **kw})
    rid = st["run_id"]
    step("start_post: one run for the request", st["reused"] is False and rid, {"run_id": rid})
    src = ok(human, tok, "research_link", {"url": URL, "run_id": rid})
    step("research_link: facts with source", src["figures"] and src["prerequisites"] == [], {"figures": len(src["figures"])})
    slides = [{"eyebrow": "AI", "headline": "Acme Card Pays Back On AI", "accent_word": "Card", "body": "4% back on AI tools."},
              {"eyebrow": "WHY", "headline": "Zero Fee", "body": "**$0** a year, 12 countries."}]
    c1 = ok(human, tok, "render_carousel", {"run_id": rid, "slides": slides, "output": 1})
    c2 = ok(human, tok, "render_carousel", {"run_id": rid, "slides": slides, "output": 2, "ratio": "1:1"})
    step("render_carousel x2: outputs 1 (4:5) and 2 (1:1) in the same run", c1["run_id"] == c2["run_id"] == rid
         and c1["media_urls"] != c2["media_urls"], {"output1": len(c1["media_urls"]), "output2": len(c2["media_urls"])})
    v_yt, v_x = _video(human, "yt.mp4"), _video(human, "x.mp4")
    caps = {"instagram": "Acme's card pays 4% back on AI tools.", "linkedin": "Acme launches a card that pays 4% back on AI tools.",
            "twitter": "Acme's new card: 4% back on AI tools, $0 a year.", "youtube": "Acme's AI card in 30 seconds"}
    pk = ok(human, tok, "create_package", {"run_id": rid, "captions": caps, "channel_media": {
        "instagram": {"output": 1}, "linkedin": {"output": 2}, "youtube": {"media_urls": [v_yt], "format": "reel"},
        "twitter": {"media_urls": [v_x]}}, "youtube": {"title": "Acme's AI card", "categoryId": "28", "madeForKids": False}})
    by = {p["channel_id"]: p for p in pk["packages"]}
    step("create_package: 4 channels, each its own media, one call", set(by) == {"ch_ig", "ch_li", "ch_x", "ch_yt"}
         and by["ch_ig"]["media_urls"] == c1["media_urls"] and by["ch_li"]["media_urls"] == c2["media_urls"]
         and by["ch_yt"]["media_urls"] == [v_yt] and by["ch_x"]["media_urls"] == [v_x] and by["ch_yt"]["format"] == "reel",
         {k: (v["format"], len(v["media_urls"])) for k, v in by.items()})
    n_runs = human.app.state.store.q("SELECT COUNT(*) AS n FROM agent_runs WHERE kind='client'")[0]["n"]
    run = human.get(f"/api/agent/runs/{rid}").json()
    yt = next(p for p in run["result"]["packages"] if p["channel_id"] == "ch_yt")
    step("youtube fields staged (category 28, title, madeForKids)", yt["options"].get("category_id") == "28"
         and yt["options"].get("title") == "Acme's AI card", yt["options"])
    return rid, caps, run, n_runs


def test_one_topic_one_run_four_channels_preflight_drafts_and_schedule(human, fake, page, monkeypatch):  # noqa: F811
    monkeypatch.setattr(conftest, "CHANNELS", conftest.CHANNELS + [YT_CH])
    human.app.state.studio.media_reach = lambda urls: []
    tok = agent_token(human, policy="draft", cap=100)
    ins = rpc(human, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "claude", "version": "1"}})
    step("initialize: says get_started and the token policy", "get_started" in ins["result"]["instructions"]
         and "drafts only" in ins["result"]["instructions"], {"len": len(ins["result"]["instructions"])})
    chans = ok(human, tok, "list_channels", {"refresh": True})
    step("list_channels: 4 channels", {c["id"] for c in chans} >= {"ch_ig", "ch_li", "ch_x", "ch_yt"}, [c["service"] for c in chans])
    gs = ok(human, tok, "get_started")
    step("get_started: the recommended path", gs["steps"][0].startswith("1. start_post"), {"steps": len(gs["steps"])})

    rid, caps, run, n_runs = _four_channel_post(human, tok, "Acme card pays 4% back on AI tools")
    step("still ONE run for the request", n_runs == 1, {"client_runs": n_runs})

    # preflight every channel with its own media
    for p in run["result"]["packages"]:
        pf = ok(human, tok, "preflight_post", {"channel_id": p["channel_id"], "text": p["text"], "media_urls": p["media_urls"],
                                               "mode": "draft", "options": p.get("options") or {}})
        res = pf["results"][0] if "results" in pf else pf
        errs = [f for f in res.get("findings") or [] if f.get("level") == "error"]
        step(f"preflight {p['service']}: no errors in draft mode", not errs, {"status": res.get("status"), "errors": errs[:2]})

    # drafts-only token: scheduling is refused before Buffer, then drafts go through (4 drafts, saveToDraft)
    due = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0).isoformat()
    res, out = call(human, tok, "schedule_package", {"run_id": rid, "channel_id": "ch_li", "mode": "schedule", "due_at": due})
    step("drafts-only token: schedule refused with policy_drafts_only, no Buffer call", res["isError"]
         and res["structuredContent"]["code"] == "policy_drafts_only" and not fake.created, res["structuredContent"].get("code"))
    for cid in ("ch_ig", "ch_li", "ch_x", "ch_yt"):
        d = ok(human, tok, "schedule_package", {"run_id": rid, "channel_id": cid, "mode": "draft"})
        fake.posts.append({"id": d["post"]["id"], "text": "x", "status": "draft", "channelId": cid})
    step("drafts mode: 4 Buffer drafts, no dueAt", len(fake.created) == 4 and all(c.get("saveToDraft") for c in fake.created)
         and not any(c.get("dueAt") for c in fake.created), [c["channelId"] for c in fake.created])
    yt_in = next(c for c in fake.created if c["channelId"] == "ch_yt")
    step("YouTube draft carries categoryId 28 and madeForKids false", yt_in["metadata"]["youtube"]["categoryId"] == "28"
         and yt_in["metadata"]["youtube"]["madeForKids"] is False, yt_in["metadata"]["youtube"])

    # the same story again in a new run: no second drafts, the earlier one is updated on request
    rid2, _, _, _ = _four_channel_post(human, tok, "Acme card pays 4% back on AI tools", new_request=True)
    res, out = call(human, tok, "schedule_package", {"run_id": rid2, "channel_id": "ch_li", "mode": "draft"})
    step("same story, new run: duplicate_draft, nothing created", res["isError"] and res["structuredContent"]["code"] == "duplicate_draft"
         and len(fake.created) == 4, res["structuredContent"].get("duplicate_of"))
    up = ok(human, tok, "schedule_package", {"run_id": rid2, "channel_id": "ch_li", "mode": "draft", "on_duplicate": "update"})
    step("on_duplicate=update edits the earlier draft (editPost)", up["status"] == "updated" and len(fake.created) == 4
         and fake.edits[-1]["id"] == up["buffer_post_id"], {"edited": up["buffer_post_id"]})

    # schedule mode: the owner allows scheduling (confirm + audit), the app schedules all 4 in the future
    tid = next(t["id"] for t in human.get("/api/safety").json()["tokens"] if t["policy"] == "draft")
    r = human.patch(f"/api/agent-tokens/{tid}", json={"policy": "trusted", "confirm": True})
    step("owner switches the policy to 'can schedule' (confirm, audited)", r.status_code == 200 and r.json()["was"] == "draft", r.json())
    rid3, _, _, _ = _four_channel_post(human, tok, "A different story: Acme raises $20M", new_request=True)
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    res, out = call(human, tok, "schedule_package", {"run_id": rid3, "channel_id": "ch_li", "mode": "schedule", "due_at": past})
    step("a dueAt in the past is refused (due_at_past)", res["isError"] and res["structuredContent"]["code"] == "due_at_past",
         res["structuredContent"].get("code"))
    before = len(fake.created)
    for cid in ("ch_ig", "ch_li", "ch_x", "ch_yt"):
        ok(human, tok, "schedule_package", {"run_id": rid3, "channel_id": cid, "mode": "schedule", "due_at": due})
    new = fake.created[before:]
    step("schedule mode: 4 posts scheduled at the future time", len(new) == 4 and all(c.get("dueAt") and not c.get("saveToDraft")
                                                                                        for c in new), [c.get("dueAt") for c in new])
    step("never a past dueAt", all(datetime.fromisoformat(c["dueAt"].replace("Z", "+00:00")) > datetime.now(timezone.utc) for c in new), "")
