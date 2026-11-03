"""Live pass 2 Nov 2026 (video agent):
- plain-word thread replies ("trim the first 3 seconds and add captions", "switch to the Nothing theme", "make a 4:5
  version") become edit_video on that run's video and the thread gets the agent's answer;
- set_theme accepts "auto" and a re-render refreshes packages[].media_urls / note, not only channel_media;
- a package with no media fails the checks; "No video found" is said only once the search is over;
- video_first prefers the launch film (YC launch page / YouTube) over a website's hero loop;
- the Spread plans only the selected channels."""
import json

import pytest

from studio import intake
from studio.actions import ActionError
from studio.agent_tools import Tools
from studio.media import video_first

from .test_agent_core import TWEET, owner  # noqa: F401  (fixture)


# ---------------------------------------------------------------- the words
@pytest.mark.parametrize("text,want", [
    ("Trim the first 3 seconds and add captions", {"cut_first_s": 3.0, "captions": True}),
    ("trim first 3 s and add captions", {"cut_first_s": 3.0, "captions": True}),
    ("cut the first three seconds", {"cut_first_s": 3.0}),
    ("switch to the Nothing theme", {"theme": "nothing"}),
    ("make a 4:5 version", {"ratio": "4:5"}),
    ("no captions please", {"captions": False}),
    ("theme auto", {"theme": "auto"}),
    ("make the hook stronger", {}),
    ("redo YouTube at 30s", {}),
])
def test_video_edits_from_plain_words(text, want):
    assert intake.video_edits(text) == want


# ---------------------------------------------------------------- the run
def _video_run(owner, channels=("ch_yt", "ch_ig")):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "channel_ids": list(channels), "format": "auto"}).json()
    rid = r["id"]
    h = owner.app.state.house
    res = json.loads(h.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    tok = "upl_test.mp4"
    res.update(clip_token=tok, clip_owned=True, upload={"token": tok, "type": "video", "name": "mine.mp4"})
    h.store.x("UPDATE agent_runs SET result=?, status='ready' WHERE id=?", (json.dumps(res), rid))
    return rid


@pytest.fixture
def renders(monkeypatch):
    calls = []

    def fake(self, run, style, seconds=None, music=None, rights_confirmed=False, ratio="9:16", clip=None, captions_mode=None):
        n = len(calls)
        calls.append({"style": style, "ratio": ratio, "clip": clip, "captions_mode": captions_mode})
        return ([{"token": f"r{n}.mp4", "url": f"https://hq.test/m/r{n}.mp4", "type": "video", "name": "reel.mp4", "duration": 12.0}],
                "Rendered by the InstaBox video worker, 12.0 s long. Style: x.", "")
    monkeypatch.setattr(Tools, "_media_reel", fake)
    monkeypatch.setattr(Tools, "_has_video", lambda self, run: True)
    return calls


def _thread(owner, rid):  # noqa: F811
    return [t["text"] for t in owner.get(f"/api/agent/runs/{rid}").json()["result"]["thread"] if t["role"] == "agent"]


def test_reply_trim_and_captions_edits_the_runs_video(owner, renders):  # noqa: F811
    rid = _video_run(owner)
    n0 = len(renders)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "Trim the first 3 seconds and add captions"})
    mine = renders[n0:]
    assert mine and all(c["clip"] == [3.0, 603.0] and c["captions_mode"] == "on" for c in mine)
    assert mine[0]["style"] != "none"  # captions need a captioned look
    said = _thread(owner, rid)[-1]
    assert said.startswith("Done: cut the first 3 s, captions on") and "12.0 s" in said and "InstaBox" in said
    run = owner.get(f"/api/agent/runs/{rid}").json()
    assert "edit_video" in [s["tool"] for s in run["steps"]]
    # the edits stay: a later theme switch keeps the cut and the captions
    n = len(renders)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "switch to the cinematic theme"})
    assert renders[n]["style"] == "cinematic" and renders[n]["clip"] == [3.0, 603.0] and renders[n]["captions_mode"] == "on"


def test_reply_4x5_version_keeps_youtube_vertical(owner, renders):  # noqa: F811
    rid = _video_run(owner)
    n0 = len(renders)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "make a 4:5 version"})
    assert [c["ratio"] for c in renders[n0:]] == ["4:5"]
    said = _thread(owner, rid)[-1]
    assert "Instagram 4:5" in said and "Youtube stay 9:16" in said


def test_reply_names_a_brand_kit_theme(owner, renders, monkeypatch):  # noqa: F811
    rid = _video_run(owner)
    monkeypatch.setattr(Tools, "_kit_theme", lambda self, name: {"kit_id": 5, "name": "Nothing", "style": "opus", "active": True}
                        if "nothing" in name.lower() else None)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "switch to the Nothing theme"})
    assert renders and renders[-1]["style"] == "opus"
    assert "Nothing brand kit look" in _thread(owner, rid)[-1]


def test_set_theme_auto_and_packages_follow_the_rerender(owner, renders):  # noqa: F811
    rid = _video_run(owner)
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "style": "minimal"})
    owner.post(f"/api/agent/runs/{rid}/tools/write_captions", json={})
    owner.post(f"/api/agent/runs/{rid}/tools/check", json={})
    before = {p["channel_id"]: p["media_urls"] for p in owner.get(f"/api/agent/runs/{rid}").json()["result"]["packages"]}
    out = owner.post(f"/api/agent/runs/{rid}/theme", json={"theme": "auto"}).json()
    assert out.get("auto") and "ch_yt" in out["rerendered"] and out["why"].startswith("auto")
    run = owner.get(f"/api/agent/runs/{rid}").json()
    assert run["input"]["theme"] == "auto"
    pk = next(p for p in run["result"]["packages"] if p["channel_id"] == "ch_yt")
    assert pk["media_urls"] != before["ch_yt"] and pk["media_urls"] == [run["result"]["channel_media"]["ch_yt"]["media"][0]["url"]]
    assert pk["note"] == run["result"]["channel_media"]["ch_yt"]["note"]


def test_a_package_with_no_media_fails_the_checks(owner):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "channel_ids": ["ch_yt"], "format": "auto"}).json()
    h = owner.app.state.house
    res = json.loads(h.store.q("SELECT result FROM agent_runs WHERE id=?", (r["id"],))[0]["result"])
    res["channel_media"] = {"ch_yt": {"format": "carousel", "media": [{"type": "photo", "url": "https://hq.test/m/a.jpg", "role": "slide"}]}}
    res["captions"] = {"ch_yt": {"text": "hello", "trimmed": False}}
    h.store.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), r["id"]))
    out = owner.post(f"/api/agent/runs/{r['id']}/tools/check", json={}).json()
    assert any("youtube: no media" in p for p in out["problems"])
    pk = owner.get(f"/api/agent/runs/{r['id']}").json()["result"]["packages"][0]
    assert pk["status"] == "hold"


# ---------------------------------------------------------------- the launch video
def test_hero_loop_ranks_after_the_launch_video(monkeypatch):
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    hero = {"url": "https://www.aviern.com/renders/web-hero.av1.mp4", "platform": "direct", "kind": "video", "how": "video_tag",
            "official": True, "confidence": 0.84, "key": "a"}
    yt = {"url": "https://www.youtube.com/watch?v=RPzu5FBxUgg", "platform": "youtube", "kind": "video", "how": "yc_launch",
          "official": True, "confidence": 0.95, "key": "b"}
    assert video_first.is_hero_loop(hero) and not video_first.is_hero_loop(yt)
    assert sorted([hero, yt], key=video_first._rank_key)[0] is yt
    plain = {**hero, "url": "https://maker.com/launch-film.mp4", "key": "c"}
    assert sorted([plain, yt], key=video_first._rank_key)[0] is yt  # the launch film first, a site video next


def test_yc_launch_page_video_is_the_official_pick(monkeypatch):
    from studio.media import find
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    yc = "https://www.ycombinator.com/launches/WdT-aviern-unmanned-cargo-airplanes-for-autonomous-airports"

    def from_page(url, http):
        if "ycombinator" in url:
            return ([find._cand("https://www.youtube.com/watch?v=RPzu5FBxUgg", "iframe", kind="video")],
                    {"title": "Launch YC: Aviern: Unmanned cargo airplanes for autonomous airports | Y Combinator"})
        return ([{**find._cand("https://www.aviern.com/renders/web-hero.av1.mp4", "video_tag", kind="video"), "official": True}], {})
    monkeypatch.setattr(find, "from_page", from_page)
    monkeypatch.setattr(find, "enrich", lambda c, http, deep=True: None)
    r = video_first.research_video(f"{yc} https://aviern.com/", http=None, website_hint="https://aviern.com/", name_hint="")
    assert r["status"] == "found" and "youtube.com" in r["video"]["url"] and r["maker"] == "Aviern"
    assert any(c["url"].endswith("web-hero.av1.mp4") for c in r["candidates"])  # still there as the fallback


def test_with_the_youtube_fallback_on_the_launch_film_beats_the_site_hero(monkeypatch):
    from . import test_video_first as tv
    monkeypatch.setattr(tv.drafter, "_public", lambda h: True)
    monkeypatch.setenv("HQ_MEDIA_YTDLP", "0")
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    http = tv._client({video_first.PH_GRAPHQL: (200, tv.PH_JSON, "application/json"), "https://screen.example/": (200, tv.SITE_HTML, "text/html")}, [])
    r = video_first.research_video(tv.PROMPT, http=http, ph_token="tok", upload_limit_mb=40)
    assert r["status"] == "found" and r["video"]["url"] == "https://www.youtube.com/watch?v=abc123def45"


def test_no_video_ask_waits_for_the_end_of_the_search(owner, monkeypatch):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "channel_ids": ["ch_li"], "format": "auto"}).json()
    rid = r["id"]
    none = {"status": "none", "video": None, "launch_video": None, "best_downloadable": None, "best_video": None, "candidates": [],
            "credit": "Video: X", "maker": "X", "website": "", "product": None, "images": [], "steps": [], "plan_line": "No video found",
            "ask": "No video found. Upload one (MP4/MOV, up to 40 MB) and I'll brand it.",
            "upload": {"accept": "video/mp4", "max_mb": 40, "label": "MP4/MOV"}}
    monkeypatch.setattr(video_first, "research_video", lambda *a, **k: json.loads(json.dumps(none)))
    owner.post(f"/api/agent/runs/{rid}/tools/find_video", json={"query": "x"})
    assert none["ask"] not in _thread(owner, rid)  # not yet: a later step may still find it


# ---------------------------------------------------------------- the Spread
def test_spread_plans_only_the_selected_channels():
    from studio.takes import Takes
    t = Takes.__new__(Takes)
    run = {"input": {"channels": [{"id": "yt"}, {"id": "ig"}]}, "result": {"clips": [{"id": "c1", "url": "u", "status": "ready"}]}}
    with pytest.raises(ActionError, match="None of the selected channels"):
        t._plan(None, run, ["other"], None, None)


def test_trim_cuts_inside_the_window_the_reel_shows(owner, renders, monkeypatch):  # noqa: F811
    """Live 3 Nov 2026: the news Reel showed an auto-picked window (14-74 s) of an 89 s film; "trim the first 3 seconds"
    re-cut from 3 s to the film's end (86 s). The cut is relative to what the person sees: 17-74 s."""
    from studio.house import House
    rid = _video_run(owner)
    owner.post(f"/api/agent/runs/{rid}/tools/render_reel", json={"channel_ids": ["ch_yt"], "style": "huhbyte_news"})
    first = renders[-1]
    real = House.media_meta
    monkeypatch.setattr(House, "media_meta", lambda self, a, t: {"type": "video", "clip": [14.0, 74.0, "auto"]}
                        if t == f"r{len(renders) - 1}.mp4" else real(self, a, t))
    n0 = len(renders)
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "trim the first 3 seconds"})
    assert first["clip"] is None and renders[n0:] and all(c["clip"] == [17.0, 74.0] for c in renders[n0:])
    n1 = len(renders)  # a later edit keeps that cut; a second trim adds up
    owner.post(f"/api/agent/runs/{rid}/messages", json={"text": "trim the first 2 seconds"})
    assert all(c["clip"] == [19.0, 74.0] for c in renders[n1:])
