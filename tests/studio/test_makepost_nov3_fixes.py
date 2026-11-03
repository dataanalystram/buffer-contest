"""Owner bug list, 3 Nov 2026 (Make a post): "In Buffer" stayed after a re-render, a template switch dropped the Reel's
video, no music control (and a default piano score on silent uploads), "All templates…" did nothing, blank template previews
and a title + subtitle cover headline, kit questions answered with the sample brands, and "the story isn't from your link"
on a youtu.be link. UI = REST = MCP for every new control."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from studio import brand_kits, brand_onboard, house, reel_audio, template_pick
from studio.agent_tools import Tools, canon_source, same_source, stale_since_sent, version_label
from studio.headline import cover_hook

from .conftest import mcp_call
from .test_agent_core import owner  # noqa: F401
from .test_agent_ux_nov3 import _fake_reels, _run, _say, _seed

TRACKS = [{"audio_id": "101953222865674", "title": "Soft Focus", "display_artist": "Lofi Lab", "duration_ms": 90139, "type": "music",
           "preview_url": "", "cover_url": ""},
          {"audio_id": "222", "title": "Night Drive", "display_artist": "Chill Co", "duration_ms": 60000, "type": "music",
           "preview_url": "", "cover_url": ""}]


def _reel(owner, monkeypatch):  # noqa: F811
    """A run whose Instagram package is a Reel of the (credited) video."""
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    run = _say(owner, rid, "Yes, I have the rights to repost it with credit")
    pk = run["result"]["packages"][0]
    assert pk["format"] == "reel", pk
    return rid, a


def _pk(owner, rid, cid="ch_ig"):  # noqa: F811
    return next(p for p in _run(owner, rid)["result"]["packages"] if p["channel_id"] == cid)


def _search(monkeypatch, tracks=TRACKS):
    seen = []

    def fake(studio, a, channel_id, query=""):
        seen.append(query)
        return {"channel_id": channel_id, "query": query, "trending": not query, "needs_facebook": False,
                "tracks": [dict(t) for t in tracks if not query or query.lower() in (t["title"] + " " + t["display_artist"]).lower()]}
    monkeypatch.setattr(reel_audio, "search", fake)
    return seen


# ---------------------------------------------------------------- 7 the story IS from your link
@pytest.mark.parametrize("a,b,same", [
    ("https://youtu.be/MxAa8g8qFz4", "https://www.youtube.com/watch?v=MxAa8g8qFz4&t=3", True),
    ("https://youtu.be/MxAa8g8qFz4?si=abc", "https://www.youtube.com/shorts/MxAa8g8qFz4", True),
    ("https://youtu.be/MxAa8g8qFz4", "https://www.youtube.com/watch?v=OTHERvideo1", False),
    ("https://twitter.com/a/status/1?s=20", "https://x.com/b/status/1", True),
    ("https://www.example.com/post/?utm_source=x", "https://example.com/post", True),
    ("https://news.site/a?id=1", "https://news.site/a?id=2", False),
    ("https://example.com/a", "https://example.com/b", False),
])
def test_same_source(a, b, same):
    assert same_source(a, b) is same
    assert canon_source("") == ""


def test_youtu_be_link_is_about_the_source(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    h = owner.app.state.house
    run = h.run(a, rid)
    run["input"]["source_url"] = "https://youtu.be/MxAa8g8qFz4"
    run["result"]["source"].update(url="https://www.youtube.com/watch?v=MxAa8g8qFz4", requested_url="https://youtu.be/MxAa8g8qFz4")
    h.store.x("UPDATE agent_runs SET input=? WHERE id=?", (json.dumps(run["input"]), rid))
    h._save(rid, run["steps"], run["result"])
    out = owner.post(f"/api/agent/runs/{rid}/tools/check", json={}).json()
    assert out["about_source"] is True and "the story isn't from your link" not in out["problems"]
    run = h.run(a, rid)
    run["result"]["source"].update(url="https://www.youtube.com/watch?v=SomethingElse", requested_url="")
    h._save(rid, run["steps"], run["result"])
    assert Tools(h, a, rid).t_check()["about_source"] is False  # (the endpoint skips a check when nothing it signs changed)


# ---------------------------------------------------------------- 5 a short cover hook, a real picture
def test_cover_hook_is_short_and_never_title_plus_subtitle():
    long = ("Screen Studio 4.0: The Mac recorder that edits for you: The biggest update yet for the Mac screen recorder that "
            "handles zooms, motion, sound and framing automatically.")
    assert cover_hook(long) == "Screen Studio 4.0: The Mac recorder that edits for you"
    assert cover_hook("OpenAI launches GPT-6") == "OpenAI launches GPT-6"
    assert cover_hook("Anthropic raises $5B - the biggest round in AI history so far this year by a very long margin indeed") == "Anthropic raises $5B"
    assert cover_hook("Sora 2: Sora 2") == "Sora 2"  # a subtitle that only repeats the title is dropped
    run = {"result": {"story": {"headline": long, "facts": ["a", "b"], "source": "YouTube"}}, "input": {}}
    d = template_pick.fill("launch", run)
    assert d["title"] == d["hook"] == "Screen Studio 4.0: The Mac recorder that edits for you"


def test_cover_falls_back_to_the_source_thumbnail(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    h = owner.app.state.house
    run = h.run(a, rid)
    run["result"]["story"]["image"] = "https://cdn.example/broken.jpg"
    run["result"]["source"].update(image="", video_url="https://www.youtube.com/watch?v=MxAa8g8qFz4")
    h._save(rid, run["steps"], run["result"])
    from .test_agent_core import jpeg
    asked = []
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: asked.append(url) or (None if "broken" in url else jpeg()))
    t = Tools(h, a, rid)
    assert t._source_thumbs(h.run(a, rid))[0] == "https://i.ytimg.com/vi/MxAa8g8qFz4/maxresdefault.jpg"
    data, use = t._hero(h.run(a, rid))
    assert data and use in ("hero", "inset") and any("i.ytimg.com" in u for u in asked)


def test_all_templates_have_previews(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    t = owner.get(f"/api/agent/runs/{rid}/templates?all=1").json()
    assert len(t["options"]) >= 6 and all(o["preview"].startswith("data:image/jpeg") for o in t["options"]), [o["id"] for o in t["options"] if not o["preview"]]
    assert {"launch", template_pick.NEWS} <= {o["id"] for o in t["options"]} and t["kit"]
    few = owner.get(f"/api/agent/runs/{rid}/templates").json()
    assert len(few["options"]) <= 4


# ---------------------------------------------------------------- 4 a template switch never drops the Reel's video
def test_carousel_template_on_a_reel_asks_then_keeps_the_video_and_undo_brings_the_reel_back(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    vid = _pk(owner, rid)["media_urls"][0]
    r = owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch"})
    assert r.status_code == 200, r.text
    res = _run(owner, rid)["result"]
    assert res["channel_media"]["ch_ig"]["format"] == "reel" and _pk(owner, rid)["media_urls"] == [vid]  # nothing changed yet
    assert res["ask"]["kind"] == "reel_to_carousel" and [c["label"] for c in res["ask"]["choices"]] == ["Keep Reel", "Switch"]
    assert "Turn the Reel into a carousel? The video becomes slide 2." in res["thread"][-1]["text"]
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "keep"})
    res = _run(owner, rid)["result"]
    assert res["channel_media"]["ch_ig"]["format"] == "reel" and "stays a Reel" in res["thread"][-1]["text"] and not res.get("ask")
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "switch"})
    res = _run(owner, rid)["result"]
    pk = _pk(owner, rid)
    assert pk["format"] == "carousel" and pk["media_urls"][1] == vid and len(pk["media_urls"]) >= 3
    assert pk["undo"]["label"] == "Back to the Reel" and "video as slide 2" in res["thread"][-1]["text"]
    assert res["template"]["id"] == "launch"
    r = owner.post(f"/api/agent/runs/{rid}/undo", json={"channel": "ch_ig"})
    assert r.status_code == 200, r.text
    pk = _pk(owner, rid)
    assert pk["format"] == "reel" and pk["media_urls"] == [vid] and _run(owner, rid)["result"]["template"]["id"] != "launch"
    assert "back to the Reel" in _run(owner, rid)["result"]["thread"][-1]["text"]


def test_keep_or_switch_typed_in_the_thread_and_undo_in_words(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch"})
    _say(owner, rid, "switch")
    assert _pk(owner, rid)["format"] == "carousel"
    run = _say(owner, rid, "back to the reel")
    assert _pk(owner, rid)["format"] == "reel" and "back to the Reel" in run["result"]["thread"][-1]["text"]
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch"})
    _say(owner, rid, "keep the reel")
    assert _pk(owner, rid)["format"] == "reel"


def test_mcp_template_switch_on_a_reel_needs_a_choice_and_undo_parity(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 30}).json()["token"]
    res, out = mcp_call(owner, tok, "post_set_template", {"run_id": rid, "template": "launch"})
    assert not res["isError"] and out["needs_choice"] and out["choices"] == ["Keep Reel", "Switch"]
    res, out = mcp_call(owner, tok, "post_set_template", {"run_id": rid, "template": "launch", "reel": "switch"})
    assert not res["isError"] and out["video_slide"] == 2
    owner.post(f"/api/agent/runs/{rid}/tools/check", json={})
    res, out = mcp_call(owner, tok, "undo_package_media", {"run_id": rid, "channel": "instagram"})
    assert not res["isError"], out
    assert _pk(owner, rid)["format"] == "reel"


def test_version_label_and_versions_are_capped():
    assert version_label({"format": "reel"}) == "Back to the Reel"
    assert version_label({"format": "carousel", "template": "news"}, {"id": "news", "name": "News carousel"}) == "Back to the News carousel"
    assert version_label({"format": "carousel", "template": "launch"}, {"id": "launch", "name": "Launch / product"}) == "Back to the Launch / product carousel"
    t = Tools.__new__(Tools)
    res: dict = {}
    for i in range(10):
        t._push_version(res, "c", {"format": "carousel", "media": [{"url": f"u{i}"}]})
    assert len(res["media_versions"]["c"]) == Tools.MAX_VERSIONS


# ---------------------------------------------------------------- 1 "In Buffer" clears when the media changes
def test_stale_since_sent():
    assert stale_since_sent({}, "reel", ["a"]) is None
    prev = {"sent": {"mode": "draft", "buffer_post_id": "p1", "at": 1}, "media_urls": ["a"], "format": "reel"}
    assert stale_since_sent(prev, "reel", ["a"]) is None
    assert stale_since_sent(prev, "carousel", ["b", "a"])["buffer_post_id"] == "p1"


def _save_draft(owner, rid):  # noqa: F811
    owner.app.state.studio.media_reach = lambda urls: []
    r = owner.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "draft", "override_warnings": True})
    assert r.status_code == 200, r.text
    pid = r.json()["post"]["id"]
    owner.fake.posts.append({"id": pid, "text": "x", "status": "draft", "dueAt": None, "channelId": "ch_ig"})
    return pid


def test_rerender_clears_in_buffer_and_update_draft_replaces_the_media(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    pid = _save_draft(owner, rid)
    pk = _pk(owner, rid)
    assert pk["status"] == "sent_to_buffer" and pk["sent"]["buffer_post_id"] == pid
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "switch"})
    pk = _pk(owner, rid)
    assert pk["status"] == "ready" and not pk.get("sent")  # never "In Buffer" with media Buffer doesn't have
    assert pk["changed_since_sent"]["mode"] == "draft" and pk["buffer_draft"]["buffer_post_id"] == pid
    n_created = len(owner.fake.created)
    r = owner.post(f"/api/agent/runs/{rid}/packages/ch_ig/update-draft")
    assert r.status_code == 200, r.text
    assert r.json()["draft"] == "updated" and len(owner.fake.created) == n_created  # the same draft, no second one
    ed = owner.fake.edits[-1]
    assert ed["id"] == pid and ed["saveToDraft"] is True and len(ed["assets"]) == len(pk["media_urls"])
    assert any("video" in x for x in ed["assets"])
    pk = _pk(owner, rid)
    assert pk["status"] == "sent_to_buffer" and not pk.get("changed_since_sent") and pk["sent"]["buffer_post_id"] == pid


def test_update_draft_recreates_a_deleted_draft_and_save_draft_never_makes_a_second(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    pid = _save_draft(owner, rid)
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "switch"})
    owner.fake.posts = [p for p in owner.fake.posts if p["id"] != pid]  # deleted in Buffer meanwhile
    n = len(owner.fake.created)
    r = owner.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "draft", "override_warnings": True})
    assert r.status_code == 200, r.text
    assert r.json()["draft"] == "recreated" and len(owner.fake.created) == n + 1
    assert _pk(owner, rid)["status"] == "sent_to_buffer"


def test_update_draft_never_touches_a_scheduled_post(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    pid = _save_draft(owner, rid)
    next(p for p in owner.fake.posts if p["id"] == pid)["status"] = "scheduled"  # scheduled in Buffer after it was saved
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "switch"})
    n_edits = len(owner.fake.edits)
    r = owner.post(f"/api/agent/runs/{rid}/packages/ch_ig/update-draft")
    assert r.status_code == 409 and "no longer a draft" in r.json()["error"] and len(owner.fake.edits) == n_edits


def test_mcp_update_package_draft_matches_rest(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    pid = _save_draft(owner, rid)
    owner.post(f"/api/agent/runs/{rid}/template", json={"template": "launch", "reel": "switch"})
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "trusted", "daily_cap": 30}).json()["token"]
    res, out = mcp_call(owner, tok, "update_package_draft", {"run_id": rid, "channel_id": "ch_ig"})
    assert not res["isError"], out
    assert out["draft"] == "updated" and owner.fake.edits[-1]["id"] == pid


# ---------------------------------------------------------------- 3 music: None by default, Instagram music on a Reel
def test_music_is_none_by_default_and_lofi_picks_an_instagram_track(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    seen = _search(monkeypatch)
    lst = owner.get(f"/api/agent/runs/{rid}/music").json()
    ig = lst["packages"][0]
    assert ig["music"] == "none" and ig["supports"] == "instagram" and lst["default"] == "none" and lst["tracks"]
    run = _say(owner, rid, "use lofi")
    pk = _pk(owner, rid)
    assert pk["options"]["ig_audio"]["title"] == "Soft Focus" and pk["options"]["ig_type"] == "reel" and "lofi" in seen
    assert "Soft Focus" in run["result"]["thread"][-1]["text"]
    run = _say(owner, rid, "no music")
    assert "ig_audio" not in (_pk(owner, rid).get("options") or {}) and "no music" in run["result"]["thread"][-1]["text"]


def test_add_music_with_nothing_to_go_on_asks(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    _search(monkeypatch)
    run = _say(owner, rid, "add music")
    res = run["result"]
    assert res["ask"]["kind"] == "music" and [c["label"] for c in res["ask"]["choices"]] == ["Soft Focus", "Night Drive"]
    assert "Which music?" in res["thread"][-1]["text"] and "ig_audio" not in (_pk(owner, rid).get("options") or {})
    c = res["ask"]["choices"][1]["body"]
    r = owner.post(f"/api/agent/runs/{rid}/music", json=c)
    assert r.status_code == 200 and r.json()["track"]["title"] == "Night Drive"
    assert _pk(owner, rid)["options"]["ig_audio"]["audio_id"] == "222"


def test_carousel_music_is_added_in_the_instagram_app(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)  # slides for now: an Instagram carousel
    assert _pk(owner, rid)["format"] == "carousel"
    out = owner.post(f"/api/agent/runs/{rid}/music", json={"music": "instagram", "query": "lofi"}).json()
    assert out["music"] == "unsupported" and out["supports"] == "app_only" and "Instagram app" in out["message"]
    _search(monkeypatch)
    run = _say(owner, rid, "add lofi music")
    assert "Instagram app" in run["result"]["thread"][-1]["text"]


def test_music_mcp_parity(owner, monkeypatch):  # noqa: F811
    from studio import mcp
    rid, a = _reel(owner, monkeypatch)
    _search(monkeypatch)
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 30}).json()["token"]
    res, lst = mcp_call(owner, tok, "list_music", {"run_id": rid})
    assert not res["isError"] and lst == owner.get(f"/api/agent/runs/{rid}/music").json()
    res, ch = mcp_call(owner, tok, "set_post_music", {"run_id": rid, "music": "instagram"})
    assert ch["needs_choice"] and len(ch["choices"]) == 2
    res, out = mcp_call(owner, tok, "set_post_music", {"run_id": rid, "music": "instagram", "query": "night", "music_volume": 40,
                                                       "original_volume": 100})
    assert out["track"]["title"] == "Night Drive" and _pk(owner, rid)["options"]["ig_audio"]["music_volume"] == 40
    res, out = mcp_call(owner, tok, "set_post_music", {"run_id": rid, "music": "none"})
    assert out["music"] == "none" and out["changed"] == ["instagram"]
    assert "list_music" in mcp.READ_ONLY and "set_post_music" not in mcp.READ_ONLY


def test_media_worker_default_is_no_music():
    from studio.media import compose
    assert compose.picked_music("auto") == "none" and compose.picked_music(None) == "none"


# ---------------------------------------------------------------- 6 brand kits: the account's own, ask when unnamed
def _kits(owner, a):  # noqa: F811
    bk = brand_kits.attach(owner.app.state.house)
    bk.list(a)  # seeds HuhByte AI
    s = brand_kits.normalize({"name": "Linear", "handle": "linear", "colors": {"primary": "#08090A", "accent": "#5E6AD2"}})
    s["label"] = brand_onboard.SAMPLE_LABEL
    sample_id = bk._insert(a, s)
    mine = bk._insert(a, brand_kits.normalize({"name": "Acme", "handle": "acme", "colors": {"primary": "#FFFFFF", "accent": "#FF5A1F"}}))
    hb = next(k["id"] for k in bk.list(a)["kits"] if k["name"] == "HuhByte AI")
    bk.activate(a, hb)
    return bk, hb, mine, sample_id


def test_available_kits_leave_out_samples_and_mcp_matches_rest(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    bk, hb, mine, sample_id = _kits(owner, a)
    names = [k["name"] for k in bk.available(a)["kits"]]
    assert "Linear" not in names and {"HuhByte AI", "Acme"} <= set(names)
    rest = owner.get("/api/brand-kits?available=1").json()
    assert "samples" not in rest and "examples" not in rest and [k["name"] for k in rest["kits"]] == names
    full = owner.get("/api/brand-kits").json()
    assert "Linear" in [k["name"] for k in full["kits"]] and full["samples"]  # the builder still has everything
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 30}).json()["token"]
    _, lst = mcp_call(owner, tok, "list_brand_kits")
    assert [k["name"] for k in lst["kits"]] == names and "samples" not in lst
    _, lst = mcp_call(owner, tok, "list_brand_kits", {"include_samples": True})
    assert "Linear" in [k["name"] for k in lst["kits"]]


def test_change_brand_kit_without_a_name_asks_with_the_available_kits(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    bk, hb, mine, sample_id = _kits(owner, a)
    for text in ("change brand kit", "can you change the kit", "use another brand kit"):
        run = _say(owner, rid, text)
        res = run["result"]
        assert res["ask"]["kind"] == "brand_kit", text
        labels = [c["label"] for c in res["ask"]["choices"]]
        assert "Linear" not in labels and {"HuhByte AI", "Acme"} <= set(labels), labels
        assert "Which brand kit" in res["thread"][-1]["text"] and not res.get("kit_override")
    run = _say(owner, rid, "list my brand kits")
    assert "**Acme**" in run["result"]["thread"][-1]["text"] and "Linear" not in run["result"]["thread"][-1]["text"]
    run = _say(owner, rid, "change the brand kit to Acme")
    assert run["result"]["kit_override"]["id"] == mine and bk._active(a) == hb  # this run only
    run = _say(owner, rid, "change the brand kit to Nope")
    assert "I don't have a kit called “Nope”" in run["result"]["thread"][-1]["text"]


def test_use_brand_kit_rest_and_mcp(owner, monkeypatch):  # noqa: F811
    rid, a = _reel(owner, monkeypatch)
    bk, hb, mine, sample_id = _kits(owner, a)
    out = owner.post(f"/api/agent/runs/{rid}/kit", json={}).json()
    assert out["needs_choice"] and "Acme" in out["choices"] and "Linear" not in out["choices"]
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 30}).json()["token"]
    _, m = mcp_call(owner, tok, "use_brand_kit", {"run_id": rid})
    assert m["needs_choice"] and m["choices"] == out["choices"]
    mcp_call(owner, tok, "use_brand_kit", {"run_id": rid, "kit": "Acme"})
    assert _run(owner, rid)["result"]["kit_override"]["id"] == mine and bk._active(a) == hb
    mcp_call(owner, tok, "use_brand_kit", {"run_id": rid, "kit": "Acme", "default": True})
    assert bk._active(a) == mine


# ---------------------------------------------------------------- the UI ships the controls
def test_ui_contract_for_the_new_controls():
    st = Path(__file__).parents[2] / "studio" / "static"
    js = (st / "pkgcontrols.js").read_text()
    html = (st / "index.html").read_text()
    assert "pkgcontrols.js" in html and "pkgcontrols.css" in html
    for tid in ("pkg-changed", "pkg-update-draft", "pkg-undo", "pkg-music", "music-seg", "music-off", "music-add", "music-carousel-note",
                "ask-card", "ask-choice", "tpl-all-dialog", "tpl-all-item"):
        assert f'data-testid="{tid}"' in js or f'"data-testid", "{tid}"' in js, tid
    hj = (st / "house.js").read_text()
    assert "hqPkgControls" in hj and "hqAskCard" in hj and "hqAllTemplates" in hj


# ---------------------------------------------------------------- 5b the launch cover never says the title twice
def test_launch_cover_title_is_not_repeated(monkeypatch):
    from studio import templates
    head = "Agent Desktop lets AI agents use your computer like a person"
    d = {"product": head, "tagline": head + ", clicking and typing in any app on your Mac", "company": ""}
    assert templates.launch_title(d) == head
    assert templates.launch_title({"product": head, "tagline": head}) == head
    assert templates.launch_title({"product": "Sample Studio", "tagline": "Turn one long video into a week of shorts"}) == \
        "Sample Studio: Turn one long video into a week of shorts"
    assert templates.launch_title({"product": "Sora 2", "tagline": ""}) == "Sora 2"
    seen = []
    real = templates.front_card
    monkeypatch.setattr(templates, "front_card", lambda b, ratio, kicker, title, *a, **k: seen.append(title) or real(b, ratio, kicker, title, *a, **k))
    templates.slides("launch", {**d, "features": ["Runs locally"]}, templates.Brand({"name": "Acme"}), max_slides=2)
    assert seen == [head] and seen[0].lower().count("agent desktop") == 1
