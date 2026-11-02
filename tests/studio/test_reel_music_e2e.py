"""Browser test of the Instagram music picker on a Reel package (Playwright, fake Buffer). Skips if Chromium can't start.
HQ_SHOT_DIR=<dir> also saves a 1440 light screenshot of the open picker."""
import json
import os
import time

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer
from .test_reel_music import SOUND, TRACK

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1'); localStorage.setItem('hq_theme', 'light')"
VIDEO = "https://cdn.example.com/reel.mp4"


def _fake(mode):
    fake = FakeBuffer()
    orig = fake.__call__

    def call(request):
        if request.url.host != "auth.buffer.com":
            q = json.loads(request.content)["query"]
            if "AudioCatalog" in q:
                if mode["fb"] is False:
                    return httpx.Response(200, json={"data": {"r": {"__typename": "ChannelRefreshRequired", "message": "x", "channelIds": ["ch_ig"]}}})
                tracks = mode.get("tracks") or [TRACK, SOUND]
                return httpx.Response(200, json={"data": {"r": {"__typename": "InstagramAudioCatalogSuccess", "failedAudioTypes": [], "audio": tracks}}})
        return orig(request)
    return fake, call


@pytest.fixture
def live(tmp_path):
    mode = {"fb": True}
    fake, call = _fake(mode)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="m" * 64,
                      db_path=str(tmp_path / "m.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(call)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-m", "csrf-m"); st.update_session("sid-m", user_id="acc_1")
    pk = {"channel_id": "ch_ig", "service": "instagram", "channel_name": "huhbyte.ai", "text": "Buffer's API can now add Instagram music to Reels.",
          "length": 52, "limit": 2200, "status": "ready", "format": "reel", "media_urls": [os.environ.get("HQ_SHOT_VIDEO") or VIDEO],
          "check": {"pass": True, "results": []}, "ratio": "9:16",
          "media": [{"url": os.environ.get("HQ_SHOT_VIDEO") or VIDEO, "type": "video", "ratio": "9:16", "duration": 21}]}
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               ("acc_1", "agent", json.dumps({"prompt": "Reel about Buffer music"}), "ready", "[]", json.dumps({"packages": [pk]}), time.time(), time.time()))
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app, rid, mode
    server.should_exit = True


def _open(chromium, base, rid, hermetic=True):
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-m", "url": base}])
    if hermetic:
        ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.goto(base)
    page.get_by_test_id("inbox-title").wait_for()
    page.evaluate(f"(async () => {{ showTab('agent', 'agent'); showRun(await api('/api/agent/runs/{rid}')); }})()")
    page.get_by_test_id("pkg-music").wait_for()
    return ctx, page


def test_pick_a_track_and_it_is_saved_on_the_package(live, chromium, close_contexts):
    base, app, rid, mode = live
    shots = os.environ.get("HQ_SHOT_DIR")
    ctx, page = _open(chromium, base, rid, hermetic=not shots)
    page.get_by_test_id("music-add").click()
    dlg = page.get_by_test_id("music-dialog")
    dlg.get_by_test_id("music-track").first.wait_for()
    assert dlg.get_by_test_id("music-track").count() == 2
    assert "Trending" in dlg.get_by_test_id("music-status").inner_text()
    dlg.get_by_test_id("music-q").fill("lofi")
    dlg.get_by_test_id("music-search").click()
    dlg.get_by_test_id("music-status").get_by_text("Results for").wait_for()
    assert dlg.get_by_test_id("music-preview").count() == 1  # the original sound has no preview link
    dlg.get_by_test_id("music-pick").first.click()
    assert dlg.get_by_test_id("music-pick").first.inner_text() == "Picked"
    dlg.get_by_test_id("music-vol").fill("70")
    dlg.get_by_test_id("music-orig").fill("25")
    dlg.get_by_test_id("music-start").fill("12")
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.wait_for_timeout(1200)
        page.screenshot(path=os.path.join(shots, "music-picker-1440-light.png"))
    dlg.get_by_test_id("music-save").click()
    page.get_by_test_id("music-chosen").wait_for()
    assert "Music 70% · Video sound 25% · from 0:12" in page.get_by_test_id("music-levels").inner_text()
    run = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    au = run["packages"][0]["options"]["ig_audio"]
    assert au["audio_id"] == TRACK["id"] and au["music_volume"] == 70 and au["original_volume"] == 25 and au["start_ms"] == 12000
    if shots:
        page.get_by_test_id("pkg-music").scroll_into_view_if_needed()
        page.screenshot(path=os.path.join(shots, "music-on-package-1440-light.png"))
    page.get_by_test_id("music-remove").click()
    page.get_by_test_id("music-add").wait_for()


def test_instagram_login_channel_shows_the_facebook_page_step(live, chromium, close_contexts):
    base, app, rid, mode = live
    mode["fb"] = False
    ctx, page = _open(chromium, base, rid)
    page.get_by_test_id("music-add").click()
    msg = page.get_by_test_id("music-needs-fb")
    msg.wait_for()
    assert msg.inner_text() == "Connect Instagram through a Facebook Page in Buffer to add music."
    assert page.get_by_test_id("music-help").get_attribute("href").startswith("https://support.buffer.com/")
