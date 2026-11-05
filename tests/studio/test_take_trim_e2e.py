"""Browser test of One take > Trim & caption in a signed-in account (Playwright, fake Buffer, FFmpeg on this machine):
the sample take's clip list, the editor window (nudge, drag a filmstrip handle, fix the transcript, a caption counter per
channel, Instagram music from a mocked catalog), Save -> the clip re-renders on the local video worker from the sample's
source window -> a new media URL. TAKE_SHOTS=<dir> saves 1280 and 390 px screenshots. Skips without Chromium or FFmpeg."""
import json
import os
import shutil
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
pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs FFmpeg for the local re-render")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.delenv("HQ_NO_LOCAL_VIDEO", raising=False)
    monkeypatch.setenv("RENDER_WORKER", "local")
    fake = FakeBuffer()
    orig = fake.__call__
    searches = []

    def call(request):
        if request.url.host != "auth.buffer.com" and request.content:
            q = json.loads(request.content).get("query", "")
            if "AudioCatalog" in q:
                searches.append(q)
                return httpx.Response(200, json={"data": {"r": {"__typename": "InstagramAudioCatalogSuccess", "failedAudioTypes": [],
                                                                "audio": [TRACK, SOUND]}}})
        return orig(request)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="k" * 64,
                      db_path=str(tmp_path / "t.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(call)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-t", "csrf-t"); st.update_session("sid-t", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app, searches
    server.should_exit = True


def _page(chromium, base, w, h):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, color_scheme="light")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-t", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())  # hermetic
    page = ctx.new_page()
    page.goto(base)
    page.get_by_test_id("inbox-title").wait_for()
    return ctx, page


def _sample(page):
    page.evaluate("showTab('take')")
    page.get_by_test_id("take-chip").first.wait_for()
    page.get_by_test_id("take-sample").click()
    page.get_by_test_id("clip-card").nth(4).wait_for()


def test_trim_and_caption_rerenders_the_sample_clip(live, chromium, close_contexts):
    base, app, searches = live
    shots = os.environ.get("TAKE_SHOTS")
    ctx, page = _page(chromium, base, 1280, 900)
    _sample(page)
    rows = page.get_by_test_id("clip-card")
    assert rows.count() == 5
    scores = [float(x.inner_text().split()[-1]) for x in page.get_by_test_id("clip-score").all()]
    assert scores == sorted(scores, reverse=True)  # sorted by score
    assert page.get_by_test_id("take-target").count() == 3  # every channel of the take, with its Spread switch
    assert page.locator("[data-testid=take-step][data-status=done]").count() == 8
    if shots:
        os.makedirs(shots, exist_ok=True)
        page.locator("#takeView").scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        page.screenshot(path=f"{shots}/onetake-page-1280.png", full_page=False)
    # open the editor on clip 5
    page.locator("[data-testid=clip-card][data-clip=c5] [data-testid=clip-edit]").click()
    dlg = page.get_by_test_id("clip-dialog")
    dlg.wait_for()
    assert page.get_by_test_id("clip-tab-product").is_hidden()  # the sample's renders never draw a product card
    before = app.state.store.q("SELECT result FROM agent_runs WHERE kind='take'")[0]["result"]
    old_url = next(c for c in json.loads(before)["clips"] if c["id"] == "c5")["url"]
    first_in = page.get_by_test_id("clip-in").inner_text()
    page.get_by_test_id("clip-in-later").click()
    assert page.get_by_test_id("clip-in").inner_text() != first_in
    # drag the Out handle most of the way left: the clip gets short (it can't go under 3 s)
    h = page.get_by_test_id("clip-handle-out").bounding_box()
    strip = page.get_by_test_id("clip-filmstrip").bounding_box()
    page.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2)
    page.mouse.down()
    page.mouse.move(strip["x"] + strip["width"] * 0.3, h["y"] + h["height"] / 2, steps=8)
    page.mouse.up()
    length = float(page.get_by_test_id("clip-length").inner_text().split()[0])
    assert 3 <= length < 20, length
    assert "timing" in page.get_by_test_id("clip-edit-status").inner_text()
    if shots:
        page.wait_for_timeout(1500)  # the filmstrip frames
        page.screenshot(path=f"{shots}/trim-caption-trim-1280.png")
    # transcript: fix a word (the burned-in captions follow)
    page.get_by_test_id("clip-tab-transcript").click()
    tx = page.get_by_test_id("clip-transcript")
    words = tx.input_value().split()
    words[1] = "HUHQUEUE"
    tx.fill(" ".join(words))
    assert "transcript" in page.get_by_test_id("clip-edit-status").inner_text()
    # captions: one per channel of the take, each counted against its own limit
    page.get_by_test_id("clip-tab-caption").click()
    svcs = page.get_by_test_id("clip-caption-service")
    assert svcs.count() == 3
    page.locator("[data-testid=clip-caption-service][data-svc=twitter]").click()
    page.get_by_test_id("clip-caption").fill("x" * 300)
    assert "Too long" in page.get_by_test_id("clip-caption-count").inner_text()
    page.get_by_test_id("clip-caption").fill("Short and sweet for X.")
    assert page.get_by_test_id("clip-caption-count").inner_text() == "22 / 280"
    if shots:
        page.screenshot(path=f"{shots}/trim-caption-caption-1280.png")
    # music (Instagram is one of the channels): the catalog search is mocked, one call
    page.get_by_test_id("clip-tab-music").click()
    page.get_by_test_id("music-track").first.wait_for()
    assert "Instagram picks which part of the track plays" in page.get_by_test_id("clip-music").inner_text()
    page.get_by_test_id("music-pick").first.click()
    page.get_by_test_id("music-vol").fill("60")
    assert "Music 60%" in page.get_by_test_id("music-levels").inner_text()
    assert len(searches) == 1
    if shots:
        page.wait_for_timeout(300)
        page.screenshot(path=f"{shots}/trim-caption-music-1280.png")
    page.get_by_test_id("clip-edit-save").click()
    page.get_by_test_id("toast").filter(has_text="Re-rendering clip").wait_for()
    # the local worker re-renders from the sample's source window: a new video URL once the clip is ready again
    page.locator("[data-testid=clip-card][data-clip=c5][data-status=ready]").wait_for(timeout=240_000)
    res = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE kind='take'")[0]["result"])
    c5 = next(c for c in res["clips"] if c["id"] == "c5")
    assert not c5.get("rerender_error"), c5.get("rerender_error")
    assert c5["url"] != old_url and "/m/" in c5["url"]
    assert c5["end"] - c5["start"] < 20 and c5["captions"]["twitter"] == "Short and sweet for X."
    assert c5["music"]["audio_id"] == TRACK["id"] and c5["music"]["music_volume"] == 60
    assert any(w["w"] == "HUHQUEUE" for w in c5["words_fix"]["words"])
    assert page.get_by_test_id("clip-music-chip").count() == 1  # the chosen track shows on the clip row
    if shots:
        page.locator("#takeView").scroll_into_view_if_needed()
        page.screenshot(path=f"{shots}/onetake-after-save-1280.png")


def test_trim_and_caption_at_390(live, chromium, close_contexts):
    base, app, _ = live
    shots = os.environ.get("TAKE_SHOTS")
    ctx, page = _page(chromium, base, 390, 844)
    _sample(page)
    page.locator("[data-testid=clip-card][data-clip=c2] [data-testid=clip-edit]").click()
    page.get_by_test_id("clip-dialog").wait_for()
    # nothing wider than the phone
    over = page.evaluate("() => [...document.querySelectorAll('#clipDialog *')].filter(e => e.getBoundingClientRect().right > innerWidth + 1 && e.offsetParent).map(e => e.id || e.className).slice(0, 5)")
    assert over == [], over
    page.get_by_test_id("clip-in-later").click()
    assert page.get_by_test_id("clip-edit-save").is_enabled()
    if shots:
        page.wait_for_timeout(1200)
        page.screenshot(path=f"{shots}/trim-caption-390.png")
        page.get_by_test_id("clip-tab-caption").click()
        page.screenshot(path=f"{shots}/trim-caption-caption-390.png")
    page.keyboard.press("Escape")
    page.get_by_test_id("clip-dialog").wait_for(state="hidden")
    if shots:
        page.locator("#clipGrid").scroll_into_view_if_needed()
        page.screenshot(path=f"{shots}/onetake-page-390.png")
