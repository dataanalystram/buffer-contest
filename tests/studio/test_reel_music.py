"""Instagram music on Reels (studio/reel_audio.py): search through Buffer's audio catalog, pick a track on a Reel package,
send it as metadata.instagram.audio (the shape probed against api.buffer.com on 2 Nov 2026), REST = MCP parity, and the
plain "connect through a Facebook Page" answer for Instagram-Login channels."""
import json
import time
from pathlib import Path

import httpx

from studio import mcp
from studio.actions import Actor, audio_input, build_post_input, clean_audio, clean_options

from .conftest import image_bytes

TRACK = {"id": "101953222865674", "title": "Soft Focus", "displayArtist": "Lofi Lab", "creatorUsername": None, "duration": 90139,
         "type": "music", "previewUrl": "https://video.fbcdn.example/p.mp4", "coverArtworkUrl": "https://scontent.example/c.jpg"}
SOUND = {"id": "555", "title": "keyboard clicks", "displayArtist": None, "creatorUsername": "someone", "duration": 15000,
         "type": "originalSound", "previewUrl": None, "coverArtworkUrl": None}
IG = {"id": "ch_ig", "service": "instagram", "name": "huhbyte.ai"}
VIDEO = "https://cdn.example.com/reel.mp4"


def _with_audio(fake, mode="ok"):
    """Teach the conftest FakeBuffer the audio queries (ok | refresh | notig)."""
    orig = fake.__call__

    def call(request):
        if request.url.host != "auth.buffer.com":
            body = json.loads(request.content)
            q = body["query"]
            if "InstagramAudioCatalog" in q or "instagramAudioCatalog" in q:
                fake.calls.append((q, body.get("variables") or {}, request.headers.get("authorization")))
                if mode == "notig":
                    return httpx.Response(200, json={"errors": [{"message": "An Instagram channel with credentials is required",
                                                                 "extensions": {"code": "BAD_USER_INPUT"}}], "data": None})
                if mode == "refresh":
                    return httpx.Response(200, json={"data": {"r": {"__typename": "ChannelRefreshRequired", "message": "Refresh",
                                                                    "channelIds": ["ch_ig"]}}})
                return httpx.Response(200, json={"data": {"r": {"__typename": "InstagramAudioCatalogSuccess", "failedAudioTypes": [],
                                                                "audio": [SOUND, TRACK]}}})
        return orig(request)
    return call


def _uid(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


def _run(c, urls=None, service="instagram", channel_id="ch_ig"):
    urls = urls or [VIDEO]
    pk = {"channel_id": channel_id, "service": service, "channel_name": "huhbyte.ai", "text": "A new model shipped today",
          "status": "ready", "format": "reel", "media_urls": urls, "check": {"pass": True},
          "media": [{"url": u, "type": "video" if u.endswith(".mp4") else "photo", "ratio": "9:16"} for u in urls]}
    return c.app.state.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                               (_uid(c), "agent", json.dumps({"prompt": "x"}), "ready", "[]", json.dumps({"packages": [pk]}),
                                time.time(), time.time()))


def _patch_transport(client, fake, mode="ok"):
    b = client.app.state.studio.buffer
    getattr(b, "real", b).http = httpx.Client(transport=httpx.MockTransport(_with_audio(fake, mode)))


# ------------------------------------------------------------------ the input Buffer takes
def test_clean_audio_keeps_only_a_usable_track_and_clamps_the_mix():
    au = clean_audio({**TRACK, "audio_id": TRACK["id"], "duration_ms": 90139, "music_volume": 140, "original_volume": -3, "start_ms": 999999})
    assert au["audio_id"] == "101953222865674" and au["music_volume"] == 100 and au["original_volume"] == 0
    assert au["start_ms"] == 90139 - 1000 and au["display_artist"] == "Lofi Lab"
    assert clean_audio({"audio_id": "abc", "title": "x", "duration_ms": 1, "type": "music"}) is None  # Meta ids are numeric
    assert clean_audio({"audio_id": "1", "title": "x", "duration_ms": 1000, "type": "song"}) is None
    assert clean_audio({"audio_id": "1", "title": "", "duration_ms": 1000, "type": "music"}) is None


def test_audio_input_is_the_exact_buffer_selection_shape():
    au = clean_audio({"audio_id": "555", "title": "clicks", "duration_ms": 15000, "type": "originalSound", "creator_username": "someone",
                      "music_volume": 60, "original_volume": 20, "start_ms": 3000})
    assert audio_input(au) == {"audioId": "555", "title": "clicks", "duration": 15000, "type": "originalSound",
                               "audioVolume": 60, "videoVolume": 20, "creatorUsername": "someone"}  # no start field: Buffer has none


def test_build_post_input_puts_music_on_a_reel_only():
    opts = clean_options({"ig_audio": {"audio_id": "1", "title": "t", "duration_ms": 30000, "type": "music"}})
    inp = build_post_input(IG, "hi", VIDEO, "draft", None, None, opts)
    ig = inp["metadata"]["instagram"]
    assert ig["type"] == "reel" and ig["audio"]["audioId"] == "1" and ig["audio"]["audioVolume"] == 100
    photo = build_post_input(IG, "hi", "https://cdn.example.com/a.png", "draft", None, None, opts)
    assert "audio" not in photo["metadata"]["instagram"]  # an image can't carry music
    li = build_post_input({"id": "ch_li", "service": "linkedin"}, "hi", VIDEO, "draft", None, None, opts)
    assert "instagram" not in json.dumps(li)


# ------------------------------------------------------------------ search
def test_search_returns_tracks_music_first(human, fake):
    _patch_transport(human, fake)
    r = human.get("/api/instagram/audio", params={"channel_id": "ch_ig", "q": "lofi"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["needs_facebook"] is False and [t["type"] for t in j["tracks"]] == ["music", "originalSound"]
    t = j["tracks"][0]
    assert t["audio_id"] == TRACK["id"] and t["duration_ms"] == 90139 and t["preview_url"].startswith("https://")
    q, v, _ = [c for c in fake.calls if "AudioCatalog" in c[0]][-1]
    assert "searchInstagramAudioCatalog" in q and v == {"c": "ch_ig", "q": "lofi"}
    human.get("/api/instagram/audio", params={"channel_id": "ch_ig", "q": "lofi"})
    assert len([c for c in fake.calls if "AudioCatalog" in c[0]]) == 1  # cached: the API budget is shared


def test_blank_search_is_trending(human, fake):
    _patch_transport(human, fake)
    assert human.get("/api/instagram/audio", params={"channel_id": "ch_ig"}).json()["trending"] is True
    assert "instagramAudioCatalog(" in [c for c in fake.calls if "AudioCatalog" in c[0]][-1][0]


def test_instagram_login_channel_gets_the_facebook_page_message(human, fake):
    _patch_transport(human, fake, "refresh")
    j = human.get("/api/instagram/audio", params={"channel_id": "ch_ig", "q": "x"}).json()
    assert j["needs_facebook"] is True and j["tracks"] == []
    assert j["message"] == "Connect Instagram through a Facebook Page in Buffer to add music."
    assert j["help_url"].startswith("https://support.buffer.com/")


def test_buffer_refusing_the_channel_says_how_to_fix_it(human, fake):
    _patch_transport(human, fake, "notig")
    r = human.get("/api/instagram/audio", params={"channel_id": "ch_ig", "q": "x"})
    assert r.status_code == 409 and "Facebook Page" in r.json()["error"]


def test_music_is_instagram_only(human, fake):
    r = human.get("/api/instagram/audio", params={"channel_id": "ch_x", "q": "x"})
    assert r.status_code == 400 and "Instagram" in r.json()["error"]


# ------------------------------------------------------------------ pick, save, send
def test_set_audio_saves_on_the_package_and_goes_to_buffer(human, fake):
    rid = _run(human)
    track = {"audio_id": TRACK["id"], "title": "Soft Focus", "display_artist": "Lofi Lab", "duration_ms": 90139, "type": "music"}
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": track, "music_volume": 70, "original_volume": 30, "start_ms": 12000})
    assert r.status_code == 200, r.text
    pk = r.json()
    assert pk["options"]["ig_audio"]["music_volume"] == 70 and pk["options"]["ig_type"] == "reel"
    saved = human.get(f"/api/agent/runs/{rid}").json()["result"]["packages"][0]
    assert saved["options"]["ig_audio"]["start_ms"] == 12000
    human.app.state.studio.media_reach = lambda urls: []
    fake.media[VIDEO] = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
    s = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/schedule", json={"mode": "draft", "override_warnings": True})
    assert s.status_code == 200, s.text
    ig = fake.created[-1]["metadata"]["instagram"]
    assert ig["type"] == "reel" and ig["audio"] == {"audioId": TRACK["id"], "title": "Soft Focus", "duration": 90139, "type": "music",
                                                    "audioVolume": 70, "videoVolume": 30, "displayArtist": "Lofi Lab"}
    assert fake.created[-1]["saveToDraft"] is True


def test_remove_audio(human):
    rid = _run(human)
    human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": {"audio_id": "1", "title": "t", "duration_ms": 9000, "type": "music"}})
    pk = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": None}).json()
    assert "ig_audio" not in pk["options"]


def test_audio_needs_a_video_and_a_whole_track(human):
    tok = human.app.state.house.put_media(Actor(_uid(human)), image_bytes(64, 80, "JPEG"), "a.jpg", kind="render", meta={"type": "photo"})
    rid = _run(human, [f"https://hq.test/m/{tok}"])
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": {"audio_id": "1", "title": "t", "duration_ms": 9000, "type": "music"}})
    assert r.status_code == 400 and "no video" in r.json()["error"]
    rid = _run(human)
    r = human.post(f"/api/agent/runs/{rid}/packages/ch_ig/audio", json={"audio": {"title": "t"}})
    assert r.status_code == 400 and "incomplete" in r.json()["error"]


def test_preview_post_refuses_music_without_a_video(human):
    r = human.post("/api/posts/preview", json={"channel_id": "ch_ig", "text": "hi", "media_url": "https://cdn.example.com/a.png", "mode": "draft",
                                               "options": {"ig_audio": {"audio_id": "1", "title": "t", "duration_ms": 9000, "type": "music"}}})
    assert r.status_code == 400 and "Reel" in r.json()["error"]


# ------------------------------------------------------------------ MCP parity
def test_mcp_tools_listed_and_search_is_read_only():
    names = {t["name"] for t in mcp.TOOLS}
    assert {"search_instagram_audio", "set_reel_audio"} <= names
    assert "search_instagram_audio" in mcp.READ_ONLY and "set_reel_audio" not in mcp.READ_ONLY


def test_mcp_set_reel_audio_matches_rest(human):
    rid = _run(human)
    studio = human.app.state.studio
    out = mcp.call_tool(studio, Actor(_uid(human)), "set_reel_audio", {"run_id": rid, "channel_id": "ch_ig", "audio": {
        "audio_id": "555", "title": "clicks", "duration_ms": 15000, "type": "originalSound", "creator_username": "someone"}, "original_volume": 0})
    assert out["options"]["ig_audio"]["original_volume"] == 0 and out["options"]["ig_audio"]["creator_username"] == "someone"


def test_mcp_search_matches_rest(human, fake):
    _patch_transport(human, fake)
    out = mcp.call_tool(human.app.state.studio, Actor(_uid(human)), "search_instagram_audio", {"channel_id": "ch_ig", "query": "lofi"})
    assert out["tracks"][0]["audio_id"] == TRACK["id"]


def test_ui_ships_the_picker_and_the_facebook_message():
    st = Path(__file__).parents[2] / "studio" / "static"
    js, html, hj = (st / "reelmusic.js").read_text(), (st / "index.html").read_text(), (st / "house.js").read_text()
    assert "reelmusic.js" in html and "reelmusic.css" in html
    assert "window.pkgMusic" in hj and "wireMusic" in hj
    for tid in ("music-add", "music-q", "music-preview", "music-pick", "music-vol", "music-orig", "music-save", "music-needs-fb"):
        assert f'data-testid="{tid}"' in js or f"data-testid=\"{tid}\"" in js
    assert 'data-testid="music-start"' not in js   # Buffer has no start-time field: Instagram picks the part
