"""Owner live test, 3 Nov 2026 ("UI/UX is worst"): "Post this on Instagram channel https://github.com/vercel-labs/agent-desktop",
then "change the template style the second slide, first one is nice", "where is the post", the rights question asked twice,
"you said you found a video", "what else brand templates can you use", "give me option to upload". Plus the two live-test
findings: a kit named in a thread switched the ACCOUNT's kit, and runs cut off by a deploy stayed "running" forever."""
from __future__ import annotations

import json
import time

import pytest

from studio import agent_brain, brand_kits, house, template_pick
from studio.agent_tools import Tools

from .conftest import mcp_call
from .test_agent_core import owner  # noqa: F401  (FakeBuffer, sync brain, recorded network)

GH = "https://github.com/vercel-labs/agent-desktop"
PROMPT = f"Post this on Instagram channel {GH}"
VID = {"url": "https://cdn.example/danman-agent-desktop.mp4", "credit": "Video: DanManREAL on YouTube", "official": False,
       "platform": "youtube", "kind": "video", "downloadable": True}
FACTS = ["Agent Desktop lets AI agents use your computer like a person", "It is open source on GitHub",
         "It works with any model through one API"]


def _actor(owner, rid):  # noqa: F811
    return house.Actor(owner.app.state.store.q("SELECT user_id FROM agent_runs WHERE id=?", (rid,))[0]["user_id"])


def _fake_reels(monkeypatch, owner):  # noqa: F811
    """The video render itself is mocked (the worker is tested elsewhere): a credited Reel file comes back."""
    made = []

    def job(self, run, spec, frames, note_extra=""):
        made.append(spec)
        tok = self.h.put_media(self.a, b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "reel.mp4", kind="render",
                               meta={"type": "video", "width": 1080, "height": 1920, "duration": 12.0})
        return [{"token": tok, "url": self.h.media_url(tok), "type": "video", "name": "reel.mp4"}], "Rendered on this server."
    monkeypatch.setattr(Tools, "_video_job", job)
    return made


def _seed(owner, chans=("ch_ig",)):  # noqa: F811
    """The owner's run up to the first rights question: the repo read, a third-party video found, slides for now."""
    r = owner.post("/api/agent/runs", json={"prompt": PROMPT, "channel_ids": list(chans), "brain": "external"}).json()
    rid = r["id"]
    h, a = owner.app.state.house, _actor(owner, rid)
    run = h.run(a, rid)
    res = run["result"]
    res["source"] = {"url": GH, "title": "vercel-labs/agent-desktop", "site": "github.com", "description": FACTS[0],
                     "paragraphs": FACTS, "image": "", "media": []}
    res["found_media"] = {"best_downloadable": dict(VID), "best_video": dict(VID)}
    res["video_credit"] = VID["credit"]
    res["repo_info"] = {"stars": "2.1k", "license": "MIT", "language": "TypeScript"}
    h._save(rid, run["steps"], res)
    tool = lambda n, args=None: owner.post(f"/api/agent/runs/{rid}/tools/{n}", json=args or {})  # noqa: E731
    assert tool("extract_facts", {"headline": "Vercel's Agent Desktop lets AI agents use your computer", "facts": FACTS}).status_code == 200
    out = tool("render_reel", {"channel_ids": ["ch_ig"]}).json()
    assert out.get("needs_confirmation"), out
    tool("write_captions")
    tool("check")
    return rid, a


def _run(owner, rid):  # noqa: F811
    return owner.get(f"/api/agent/runs/{rid}").json()


def _say(owner, rid, text):  # noqa: F811
    r = owner.post(f"/api/agent/runs/{rid}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return _run(owner, rid)


# ---------------------------------------------------------------- 1 template choice
def test_picker_is_deterministic_by_story_type():
    gh = {"input": {"prompt": PROMPT, "intake": {"urls": [GH]}}, "result": {}}
    assert template_pick.pick(gh)["id"] == "github_repo" and template_pick.pick(gh)["why"] == "a GitHub repo link"
    assert template_pick.pick(gh, has_video=True)["id"] == "github_repo"  # the repo wins over its demo video
    fund = {"input": {"prompt": "post this", "intake": {"urls": ["https://techcrunch.com/x"]}},
            "result": {"story": {"headline": "Acme raises $12M Series A", "facts": ["Led by Sample Ventures"]}}}
    assert template_pick.pick(fund)["id"] == "funding"
    news = {"input": {"prompt": "post this", "intake": {"urls": ["https://news.example/a"]}}, "result": {"story": {"headline": "A new chip ships"}}}
    assert template_pick.pick(news)["id"] == "news_carousel"
    assert template_pick.pick(news, has_video=True)["id"] == "launch_reel"
    p = template_pick.pick(gh)
    assert 3 <= len(p["suggestions"]) <= 4 and p["suggestions"][0] == "github_repo"
    # their own template for the story type wins
    own = template_pick.pick(fund, custom=[{"id": "custom:funding-news", "name": "Funding news", "kind": "carousel"}])
    assert own["id"] == "custom:funding-news" and "your own template" in own["why"]


@pytest.mark.parametrize("text,want", [
    ("can you change the template style the second slide first one is nice", {"template": "", "slides": [2], "keep": [1]}),
    ("use the funding template", {"template": "funding", "slides": []}),
    ("editorial style", {"template": "editorial_cover"}),
    ("keep slide 1, restyle slide 2", {"slides": [2], "keep": [1]}),
    ("switch to the GitHub repo pillar", {"template": "github_repo"}),
])
def test_template_words(text, want):
    got = template_pick.template_reply(text)
    assert got and all(got[k] == v for k, v in want.items()), got


def test_list_and_non_template_replies():
    assert template_pick.template_reply("what templates can you use")["list"]
    assert template_pick.template_reply("you said video you found video and also can you check what else brand templates you can use")["list"]
    for t in ("make the hook stronger", "where is the post>", "yes i confirm it", "shorter captions"):
        assert template_pick.template_reply(t) is None, t


def test_repo_post_uses_the_repo_template_in_the_active_kit_and_never_shows_sample_data(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    res = _run(owner, rid)["result"]
    assert res["template"]["id"] == "github_repo" and res["template"]["why"] == "a GitHub repo link" and res["template"]["auto"]
    ig = res["channel_media"]["ch_ig"]
    assert ig["template"] == "github_repo" and ig["format"] == "carousel" and len(ig["media"]) >= 4
    pk = next(p for p in res["packages"] if p["service"] == "instagram")
    assert pk["media_urls"], pk  # template slides are postable photos
    t = owner.get(f"/api/agent/runs/{rid}/templates").json()
    assert t["template"]["name"] == "GitHub repo pillar" and 3 <= len(t["options"]) <= 4
    assert t["options"][0]["current"] and all(o["preview"].startswith("data:image/jpeg;base64,") for o in t["options"])
    assert {"News carousel", "Funding round", "Text slides"} <= {x["name"] for x in t["all"]}
    # the sample data of the template previews never reaches a real post
    filled = template_pick.fill("github_repo", owner.app.state.house.run(a, rid))
    assert filled["repo"] == "vercel-labs/agent-desktop" and "12.4k" not in json.dumps(filled) and "clip-maker" not in json.dumps(filled)


def test_template_by_name_and_per_slide(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    before = [m["url"] for m in _run(owner, rid)["result"]["channel_media"]["ch_ig"]["media"]]
    run = _say(owner, rid, "can you change the template style the second slide first one is nice")
    after = [m["url"] for m in run["result"]["channel_media"]["ch_ig"]["media"]]
    assert len(after) == len(before) and after[0] == before[0] and after[1] != before[1] and after[2:] == before[2:]
    assert run["result"]["template"]["slides"] == {"2": run["result"]["template"]["slides"]["2"]} and not run["result"]["template"]["auto"]
    assert "slide 2 now uses" in run["result"]["thread"][-1]["text"]
    run = _say(owner, rid, "use the funding template")
    assert run["result"]["template"]["id"] == "funding" and run["result"]["channel_media"]["ch_ig"]["template"] == "funding"
    # the kit never changed: every render was drawn in the account's active kit
    assert not run["result"].get("kit_override")
    run = _say(owner, rid, "what templates can you use")
    last = run["result"]["thread"][-1]["text"]
    assert "**Funding round** (in use)" in last and "GitHub repo pillar" in last and run["result"]["focus"]["target"] == "templates"


def test_mcp_chooses_the_template_by_name(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 20}).json()["token"]
    r = owner.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"set_template", "get_post_templates", "resume_post", "post_set_template"} <= names
    res, out = mcp_call(owner, tok, "set_template", {"run_id": rid, "template": "Funding round"})
    assert not res["isError"], out
    got = _run(owner, rid)["result"]
    assert got["template"]["id"] == "funding" and got["channel_media"]["ch_ig"]["template"] == "funding"
    res, t = mcp_call(owner, tok, "get_post_templates", {"run_id": rid})
    assert not res["isError"] and t["template"]["name"] == "Funding round"
    res, bad = mcp_call(owner, tok, "set_template", {"run_id": rid, "template": "no such thing"})
    assert res["isError"] and "Pick one of" in bad


# ---------------------------------------------------------------- 2 rights: answered once, the Reel right away
def test_rights_yes_records_news_credit_and_renders_the_reel_never_asking_again(owner, monkeypatch):  # noqa: F811
    made = _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    res = _run(owner, rid)["result"]
    q = res["rights_question"]
    assert "DanManREAL" in q
    run = _say(owner, rid, "yes i confirm it")
    res = run["result"]
    assert res["rights"]["mode"] == "news_credit" and res["rights"]["video"] == VID["url"] and not res.get("rights_question")
    ig = next(p for p in res["packages"] if p["service"] == "instagram")
    assert ig["format"] == "reel" and made and made[-1]["rights_confirmed"] is True and made[-1]["credit"] == VID["credit"]
    assert "now a Reel" in res["thread"][-1]["text"]
    n_jobs = len(made)
    run = _say(owner, rid, "yes")  # the owner said it again: no second question, no re-render
    asked = [t for t in run["result"]["thread"] if t["role"] == "agent" and "Do you have the right" in t["text"]]
    assert len(asked) <= 1 and not run["result"].get("rights_question")
    # a theme change re-renders without asking
    owner.post(f"/api/agent/runs/{rid}/theme", json={"theme": "cinematic"})
    assert not _run(owner, rid)["result"].get("rights_question") and len(made) > n_jobs


def test_use_slides_instead(owner, monkeypatch):  # noqa: F811
    made = _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    run = _say(owner, rid, "Use slides instead")
    res = run["result"]
    assert res["rights"]["mode"] == "slides" and not res.get("rights_question") and not made
    assert next(p for p in res["packages"] if p["service"] == "instagram")["format"] == "carousel"


def test_you_said_you_found_a_video_plus_templates(owner, monkeypatch):  # noqa: F811
    made = _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    _say(owner, rid, "yes i confirm it")
    owner.app.state.house  # noqa: B018
    run = _say(owner, rid, "you said video you found video and also can you check what else brand templates you can use")
    assert any("Templates I can use" in t["text"] for t in run["result"]["thread"][-3:])
    assert next(p for p in run["result"]["packages"] if p["service"] == "instagram")["format"] == "reel"
    assert made


# ---------------------------------------------------------------- 3 + 4 upload, where is the post, check once
def test_where_is_the_post_and_upload_option(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    run = _say(owner, rid, "where is the post>")
    last = run["result"]["thread"][-1]["text"]
    assert "Your posts" in last and "Instagram" in last and "workspace" not in last.lower()
    assert run["result"]["focus"]["target"] == "packages" and run["status"] == "ready"
    run = _say(owner, rid, "give me option to upload the video")
    last = run["result"]["thread"][-1]["text"]
    assert "Upload video" in last and "100 MB" in last and run["result"]["focus"]["target"] == "upload"
    run = _say(owner, rid, "no you can give option to upload here")
    assert run["result"]["focus"]["target"] == "upload"


def test_check_runs_once_per_change(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    n = sum(1 for s in _run(owner, rid)["steps"] if s["tool"] == "check")
    for _ in range(3):
        owner.post(f"/api/agent/runs/{rid}/tools/check", json={})
    assert sum(1 for s in _run(owner, rid)["steps"] if s["tool"] == "check") == n


def test_upload_limit_is_the_librarys():
    from studio import library
    assert library.FILE_MAX == 100 * 1024 * 1024


# ---------------------------------------------------------------- live finding 1: a kit named in a thread is for that run
def _kits(owner, a):  # noqa: F811
    bk = brand_kits.attach(owner.app.state.house)
    kid_main = bk._insert(a, brand_kits.normalize({"name": "HuhByte AI", "handle": "huhbyte.ai", "colors": {"primary": "#111114", "accent": "#B9A6FF"}}))
    kid_n = bk._insert(a, brand_kits.normalize({"name": "Nothing", "handle": "nothing", "colors": {"primary": "#000000", "accent": "#D71921"}}))
    bk.activate(a, kid_main)
    return bk, kid_main, kid_n


def test_kit_in_a_thread_applies_to_that_run_only(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    bk, kid_main, kid_n = _kits(owner, a)
    run = _say(owner, rid, "use the Nothing kit")
    res = run["result"]
    assert res["kit_override"]["id"] == kid_n and res["kit_override"]["kit"]["handle"] == "nothing"
    assert bk._active(a) == kid_main and owner.app.state.house.kit(a)["handle"] == "huhbyte.ai"  # the account is untouched
    assert "this post only" in res["thread"][-1]["text"]
    # a later run draws in the account kit again
    rid2, _ = _seed(owner)
    assert Tools(owner.app.state.house, a, rid2)._kit()["handle"] == "huhbyte.ai"
    assert Tools(owner.app.state.house, a, rid)._kit()["handle"] == "nothing"
    # "make it my default" is the only way to switch the account
    run = _say(owner, rid, "use the Nothing kit and make it my default")
    assert bk._active(a) == kid_n and not run["result"].get("kit_override")


def test_set_theme_with_a_kit_name_is_per_run_too(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    bk, kid_main, kid_n = _kits(owner, a)
    owner.post(f"/api/agent/runs/{rid}/theme", json={"theme": "Nothing"})
    assert bk._active(a) == kid_main
    assert _run(owner, rid)["result"]["kit_override"]["id"] == kid_n


# ---------------------------------------------------------------- live finding 2: runs cut off by a deploy
def test_orphaned_running_runs_become_interrupted_and_resume(owner, monkeypatch):  # noqa: F811
    _fake_reels(monkeypatch, owner)
    rid, a = _seed(owner)
    h, st = owner.app.state.house, owner.app.state.store
    brain = h.brain
    run = h.run(a, rid)
    run["result"]["_boot"] = "deadprocess1"
    h._save(rid, run["steps"], run["result"], "running")
    st.x("INSERT INTO agent_procs(boot,seen) VALUES(?,?)", ("deadprocess1", time.time() - 600))
    # a run of a live process (another instance during a deploy overlap) is left alone
    r2 = owner.post("/api/agent/runs", json={"prompt": PROMPT, "channel_ids": ["ch_ig"], "brain": "external"}).json()["id"]
    run2 = h.run(a, r2)
    run2["result"]["_boot"] = "liveprocess1"
    h._save(r2, run2["steps"], run2["result"], "running")
    st.x("INSERT INTO agent_procs(boot,seen) VALUES(?,?)", ("liveprocess1", time.time()))
    out = brain.interrupt_orphans()
    assert rid in out and r2 not in out
    got = _run(owner, rid)
    assert got["status"] == "interrupted" and got["result"]["interrupted"]["after"]
    assert "Press Resume" in got["result"]["thread"][-1]["text"]
    assert _run(owner, r2)["status"] == "running"
    res = owner.post(f"/api/agent/runs/{rid}/resume", json={})
    assert res.status_code == 200, res.text
    got = _run(owner, rid)
    assert got["status"] == "ready" and got["result"]["packages"] and "Picked up where I stopped" in got["result"]["thread"][-1]["text"]
    assert owner.post(f"/api/agent/runs/{rid}/resume", json={}).status_code == 409  # not stopped any more


def test_unstamped_silent_runs_are_interrupted_on_startup(owner):  # noqa: F811
    h, st = owner.app.state.house, owner.app.state.store
    r = owner.post("/api/agent/runs", json={"prompt": PROMPT, "channel_ids": ["ch_ig"], "brain": "external"}).json()["id"]
    st.x("UPDATE agent_runs SET status='running', updated=? WHERE id=?", (time.time() - 3 * agent_brain.ORPHAN_S, r))
    assert r in h.brain.interrupt_orphans()
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (r,))[0]["status"] == "interrupted"


def test_ui_contract():
    from pathlib import Path
    st = Path(__file__).parents[2] / "studio" / "static"
    html, js = (st / "index.html").read_text(), (st / "house.js").read_text()
    assert 'data-testid="reply-upload"' in html and 'data-testid="reply-file"' in html and 'id="mpTpl"' in html and 'id="mpReady"' in html
    for t in ("rights-yes", "rights-slides", "mp-resume", "mp-template-chip", "mp-ready-item", "step-count"):
        assert t in js, t
    assert "REPLY_CHIPS" not in js and "function md(" in js
