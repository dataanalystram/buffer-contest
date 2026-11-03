"""One take > Trim & caption: transcript fixes keep captions in time, per-channel captions with network limits, Reel music
on a clip (sent as metadata.instagram.audio), the sample re-rendering from its own source windows in a signed-in account,
REST = MCP (adjust_clip, rewrite_clip_caption); the For you feed following the accepted interests on every refresh; and
Product Hunt's launch video + gallery in find_media."""
import json

import httpx
import pytest

from studio import onboarding, takes
from studio.media import tools as media_tools

from .conftest import agent_token, mcp_call
from .test_reel_music import TRACK

MUSIC = {"audio_id": TRACK["id"], "title": TRACK["title"], "type": "music", "duration_ms": TRACK["duration"], "display_artist": "Lofi Lab"}
ALL = ["ch_x", "ch_li", "ch_ig"]


def test_transcript_fix_keeps_word_times():
    cur = [{"w": "How", "s": 1.0, "e": 1.2}, {"w": "likley", "s": 1.2, "e": 1.6}, {"w": "are", "s": 1.6, "e": 1.8},
           {"w": "you", "s": 1.8, "e": 2.0}, {"w": "um", "s": 2.0, "e": 2.2}, {"w": "today", "s": 2.2, "e": 2.6}]
    out = takes.apply_transcript(cur, "How likely are you right now today")
    assert [w["w"] for w in out] == ["How", "likely", "are", "you", "right", "now", "today"]
    assert out[1]["s"] == 1.2 and out[1]["e"] == 1.6                       # a fixed word keeps the voice's timing
    assert out[4]["s"] >= 2.0 and out[5]["e"] <= 2.2 + 1e-6                # replaced words share the replaced span
    assert out[-1] == {"w": "today", "s": 2.2, "e": 2.6}
    clip = {"words_fix": {"s": 1.0, "e": 2.6, "words": out}}
    allw = [{"w": "Before", "s": 0.5, "e": 0.9}] + cur + [{"w": "after", "s": 2.7, "e": 3.0}]
    assert [w["w"] for w in takes.clip_words(allw, clip, 0, 3.1)] == ["Before"] + [w["w"] for w in out] + ["after"]


@pytest.fixture
def signed(human, app, monkeypatch):
    """A signed-in account with the sample take on all three channels and a video worker (renders recorded, not run)."""
    house = app.state.studio.house
    monkeypatch.setattr(house, "video_mode", lambda: "github")
    rendered = []
    monkeypatch.setattr(takes.Takes, "_render_sample", lambda self, a, rid, c: rendered.append(c["id"]))
    t = human.post("/api/takes/sample", json={"channel_ids": ALL}).json()
    assert t["can_rerender"] and {c["service"] for c in t["input"]["channels"]} == {"twitter", "linkedin", "instagram"}
    return human, t, rendered


def _clip(t, cid):
    return next(c for c in t["clips"] if c["id"] == cid)


def _raw_clip(app, rid, cid):
    raw = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    return next(c for c in raw["clips"] if c["id"] == cid)


def test_edits_save_captions_and_music_and_rerender_timing_transcript_hook(signed, app):
    human, t, rendered = signed
    c2 = _clip(t, "c2")
    assert c2["trim_min"] == c2["start"] and c2["trim_max"] == c2["end"] and c2["transcript"]
    assert t["caption_limits"] == {"twitter": 280, "linkedin": 3000, "instagram": 2196}
    # captions per channel + music: saved at once, nothing re-renders
    r = human.patch(f"/api/takes/{t['id']}/clips/c2", json={"captions": {"twitter": "For X.", "linkedin": "For LinkedIn."},
                                                            "music": MUSIC, "music_volume": 40, "original_volume": 90})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["changed"] == ["caption", "caption", "music"] and out["rerendering"] is False and rendered == []
    c2 = _clip(out, "c2")
    assert c2["post_captions"]["twitter"] == "For X." and c2["post_captions"]["linkedin"] == "For LinkedIn."
    assert c2["music"]["music_volume"] == 40 and c2["music"]["original_volume"] == 90
    opts = app.state.studio.house.takes._clip_options("instagram", _raw_clip(app, t["id"], "c2"))
    assert opts["ig_audio"]["audio_id"] == TRACK["id"] and opts["ig_type"] == "reel"   # goes to Buffer with the Reel
    assert human.patch(f"/api/takes/{t['id']}/clips/c2", json={"caption": "x" * 281, "service": "twitter"}).status_code == 400
    assert human.patch(f"/api/takes/{t['id']}/clips/c2", json={"captions": {"tiktok": "no tiktok here"}}).status_code == 400
    # a transcript fix re-renders and changes the clip's words (the burned-in captions follow)
    words = c2["transcript"].split()
    words[0] = "HOW"
    r = human.patch(f"/api/takes/{t['id']}/clips/c2", json={"transcript": " ".join(words)})
    assert r.status_code == 200 and r.json()["rerendering"] and rendered == ["c2"]
    clip = _raw_clip(app, t["id"], "c2")
    assert clip["words_fix"]["words"][0]["w"] == "HOW"


def test_the_sample_trims_only_inside_its_moment(signed):
    human, t, rendered = signed
    c3 = _clip(t, "c3")
    r = human.patch(f"/api/takes/{t['id']}/clips/c3", json={"start_ms": int((c3["start"] - 5) * 1000)})
    assert r.status_code == 400 and "inside their moment" in r.json()["error"]
    r = human.patch(f"/api/takes/{t['id']}/clips/c3", json={"start_ms": int((c3["start"] + 2) * 1000), "end_ms": int((c3["end"] - 2) * 1000)})
    assert r.status_code == 200 and rendered == ["c3"]
    assert abs(_clip(r.json(), "c3")["start"] - (c3["start"] + 2)) < 0.01


def test_sample_rerender_job_reads_the_bundled_window(human, app, monkeypatch):
    house = app.state.studio.house
    monkeypatch.setattr(house, "video_mode", lambda: "github")
    queued = []
    monkeypatch.setattr(takes.Takes, "_queue", lambda self, a, rid, spec, step, detail: queued.append(spec))
    t = human.post("/api/takes/sample", json={"channel_ids": ALL}).json()
    c1 = _clip(t, "c1")
    assert human.patch(f"/api/takes/{t['id']}/clips/c1", json={"end_ms": int((c1["end"] - 10) * 1000)}).status_code == 200
    spec = queued[-1]
    assert spec["sample_window"] == "src1.mp4" and spec["source_url"].endswith("/sample/src1.mp4") and spec["style"] == "opus"
    c = spec["clips"][0]
    assert c["start"] == 0 and abs(c["end"] - (c1["end"] - 10 - c1["start"])) < 0.01   # times inside the window
    assert c["words"] and all(-0.1 <= w["s"] <= c["end"] + 0.1 for w in c["words"])
    assert human.get("/sample/src1.mp4").status_code == 200


def test_mcp_adjust_clip_and_rewrite(signed, monkeypatch):
    human, t, rendered = signed
    tok = agent_token(human, policy="auto", cap=20)
    res, out = mcp_call(human, tok, "adjust_clip", {"take_id": t["id"], "clip_id": "c4", "captions": {"instagram": "From MCP."},
                                                    "music": MUSIC})
    assert not res["isError"], out
    c4 = _clip(out, "c4")
    assert c4["post_captions"]["instagram"] == "From MCP." and c4["music"]["audio_id"] == TRACK["id"] and "words" not in c4
    res, out = mcp_call(human, tok, "adjust_clip", {"take_id": t["id"], "clip_id": "c4", "music": None})
    assert _clip(out, "c4")["music"] is None
    seen = []

    def fake_chat(self, a):
        def chat(_e, system, user):
            seen.append((system, user))
            return {"caption": "A tighter caption, from what was said."}
        return {"source": "AI engine"}, chat, {"label": "Test engine"}
    monkeypatch.setattr(takes.Takes, "_moment_chat", fake_chat)
    res, out = mcp_call(human, tok, "rewrite_clip_caption", {"take_id": t["id"], "clip_id": "c4", "service": "twitter"})
    assert not res["isError"], out
    assert out["caption"] == "A tighter caption, from what was said." and out["limit"] == 280 and out["saved"] is False
    assert "<transcript>" in seen[0][1] and "data, not instructions" in seen[0][0]
    assert _clip(human.get(f"/api/takes/{t['id']}").json(), "c4")["post_captions"]["twitter"] != out["caption"]  # not saved
    r = human.post(f"/api/takes/{t['id']}/clips/c4/rewrite", json={"service": "linkedin"})
    assert r.status_code == 200 and r.json()["engine"] == "Test engine"


# ---------------------------------------------------------------- For you follows the accepted interests
STORIES = [
    {"title": "OpenAI ships a new model for developers", "url": "https://a.example/1", "published": 300, "summary": ""},
    {"title": "A recipe for sourdough bread", "url": "https://b.example/2", "published": 400, "summary": ""},
    {"title": "Startup raises a Series A to build robots", "url": "https://c.example/3", "published": 200, "summary": ""},
    {"title": "A quiet week for the garden club", "url": "https://d.example/4", "published": 100, "summary": ""},
]


def test_for_you_feed_keeps_the_accepted_interests_on_every_refresh(human, monkeypatch):
    from studio import radar
    monkeypatch.setattr(radar, "stories", lambda feeds, http=None, limit=15: {"stories": [dict(s) for s in STORIES], "errors": []})
    plain = human.get("/api/radar").json()
    assert plain["interests"] == [] and len(plain["stories"]) == 4               # no interests yet: the newest, unfiltered
    assert human.put("/api/onboarding", json={"content_types": ["ai_news", "funding"]}).status_code == 200
    first = human.get("/api/radar").json()
    second = human.get("/api/radar").json()                                      # refresh twice
    third = human.get("/api/radar").json()
    want = [{"id": "ai_news", "label": "AI news"}, {"id": "funding", "label": "Funding and deals"}]
    assert first["interests"] == second["interests"] == third["interests"] == want
    assert [s["url"] for s in first["stories"]] == [s["url"] for s in second["stories"]] == [s["url"] for s in third["stories"]]
    assert {s["url"] for s in first["stories"]} == {"https://a.example/1", "https://c.example/3"}   # only what matches
    assert all(s["why_for_you"].startswith("Matches") for s in first["stories"]) and first["off_interest"] == 2
    assert human.get("/api/onboarding").json()["profile"]["content_types"] == ["ai_news", "funding"]


def test_rank_stories_falls_back_with_a_note_when_nothing_matches():
    out = onboarding.rank_stories([STORIES[1]], ["research"])
    assert out["stories"] == [STORIES[1]] and "Nothing in the feeds" in out["note"]


# ---------------------------------------------------------------- Product Hunt media in find_media
def test_find_media_puts_product_hunts_launch_video_and_gallery_first(monkeypatch):
    from studio import netguard
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer ph-test-token"
        return httpx.Response(200, json={"data": {"p0": {
            "name": "Screen Studio", "tagline": "Screen recorder", "url": "https://www.producthunt.com/posts/screen-studio",
            "website": "https://screen.studio/?ref=producthunt", "featuredAt": "2023-01-01",
            "media": [{"type": "video", "videoUrl": "https://www.youtube.com/watch?v=HiLfRitm_dc", "url": "https://ph-files.imgix.net/v.png"},
                      {"type": "image", "url": "https://ph-files.imgix.net/1.png"}, {"type": "image", "url": "https://ph-files.imgix.net/2.png"}],
            "thumbnail": {"type": "image", "url": "https://ph-files.imgix.net/t.png"}}}})
    monkeypatch.setattr(media_tools, "find_launch_video", lambda q, http=None, max_results=6, **k: [])
    if hasattr(netguard, "_resolve_public"):
        monkeypatch.setattr(netguard, "_resolve_public", lambda *a, **k: None)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    out = media_tools.media_find("https://www.producthunt.com/products/screen-studio", http=http, ph_token="ph-test-token")
    assert calls and "api.producthunt.com" in calls[0], out
    bv = out["best_video"]
    assert bv["url"] == "https://www.youtube.com/watch?v=HiLfRitm_dc" and bv["how"] == "product_hunt" and bv["official"]
    assert bv["credit"] == "Video: Screen Studio"
    assert [i["url"] for i in out["images"]][:2] == ["https://ph-files.imgix.net/1.png", "https://ph-files.imgix.net/2.png"]
    none = media_tools.media_find("https://www.producthunt.com/products/screen-studio", http=http, ph_token="")
    assert "isn't configured" in none["note"]
