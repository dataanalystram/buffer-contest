"""The owner's 3 Nov 2026 sequence in a real browser (Make a post thread), with the video render mocked: a GitHub repo post
in the repo template with its Template line and Change template chips, "change the template style the second slide",
"where is the post" (the cards light up), one rights question answered with a button -> a Reel, "what templates can you
use", and an Upload video in the composer on a later turn. Screenshots at 1280 and 390 px go to shots/agentux/."""
import pathlib
import shutil
import subprocess
import time

import httpx
import pytest

from studio import house
from studio.agent_tools import Tools
from studio.app import create_app
from studio.config import load_settings

from . import conftest
from .browsers import free_port, serve
from .conftest import FakeBuffer
from .test_agent_core import OWNER_CHANNELS, Router, jpeg
from .test_agent_ux_nov3 import FACTS, GH, PROMPT, VID

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
SHOTS = pathlib.Path(__file__).resolve().parents[2] / "shots" / "agentux"


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="u" * 64,
                      db_path=str(tmp_path / "ux.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True)
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    h = app.state.house
    h.sync, h.worker_mode = True, "local"
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)

    def job(self, run, spec, frames, note_extra=""):  # the render itself is mocked: a credited Reel comes back
        tok = self.h.put_media(self.a, (tmp_path / "clip.mp4").read_bytes() if (tmp_path / "clip.mp4").exists() else b"\0" * 64,
                               "reel.mp4", kind="render", meta={"type": "video", "width": 1080, "height": 1920, "duration": 2.0})
        return [{"token": tok, "url": self.h.media_url(tok), "type": "video", "name": "reel.mp4"}], "Rendered on this server."
    monkeypatch.setattr(Tools, "_video_job", job)
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-ux", "csrf-ux"); st.update_session("sid-ux", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app, tmp_path
    server.should_exit = True


def _seed(app):
    h = app.state.house
    a = house.Actor("acc_1")
    run = h.brain.start(a, {"prompt": PROMPT, "channel_ids": ["ch_ig"], "brain": "external"})
    rid = run["id"]
    run = h.run(a, rid)
    res = run["result"]
    res["source"] = {"url": GH, "title": "vercel-labs/agent-desktop", "site": "github.com", "description": FACTS[0], "paragraphs": FACTS,
                     "image": "https://example.com/hero.jpg", "media": []}
    res["found_media"] = {"best_downloadable": dict(VID), "best_video": dict(VID)}
    res["video_credit"] = VID["credit"]
    res["repo_info"] = {"stars": "2.1k", "license": "Apache-2.0", "language": "TypeScript"}
    h._save(rid, run["steps"], res)
    h.brain.tool(a, rid, "extract_facts", {"headline": "Vercel's Agent Desktop lets AI agents use your computer", "facts": FACTS})
    h.brain.tool(a, rid, "render_reel", {"channel_ids": ["ch_ig"]})
    h.brain.tool(a, rid, "write_captions", {})
    h.brain.tool(a, rid, "check", {})
    return rid


def _page(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-ux", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    return ctx, page


def _open(page, base, rid):
    page.goto(base + "/#/agent")
    page.wait_for_function("() => typeof window.showRun === 'function' && typeof api === 'function'")
    page.evaluate(f"async () => {{ window.showTab && showTab('agent'); const r = await api('/api/agent/runs/{rid}'); showRun(r); }}")
    page.get_by_test_id("package").first.wait_for()


def _say(page, text):
    n = page.get_by_test_id("thread-msg").count()
    page.get_by_test_id("reply-input").fill(text)
    page.get_by_test_id("reply-go").click()
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=thread-msg]').length >= {n + 2}"
                           " && !document.querySelector('#mpStatus .spin')")


def _no_overflow(page, w):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w + 1, f"horizontal overflow at {w}px: {sw}"


SLOW_BOOT = """(() => { const f = window.fetch.bind(window);  // a slow boot: /api/channels answers 2.5 s late
  window.fetch = (u, o) => String(u).includes("/api/channels") ? new Promise((r) => setTimeout(r, 2500)).then(() => f(u, o)) : f(u, o); })()"""


def test_slow_boot_keeps_the_run_the_user_opened(live, chromium):
    """boot() ends with showTab(?tab or "inbox"). When it lands after showRun (or a tab the user opened), it must not
    throw them back to the Inbox and hide the chat thread; with nothing opened meanwhile it still lands on the Inbox."""
    base, app, tmp = live
    rid = _seed(app)
    ctx, page = _page(chromium, base, 1280)
    ctx.add_init_script(SLOW_BOOT)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base + "/")
        page.wait_for_function("() => typeof window.showRun === 'function' && typeof api === 'function'")
        assert page.evaluate("() => !document.body.dataset.booted"), "boot finished before the delayed /api/channels"
        page.evaluate(f"async () => {{ showTab('agent'); const r = await api('/api/agent/runs/{rid}'); showRun(r); }}")
        page.get_by_test_id("package").first.wait_for()
        page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
        page.wait_for_timeout(300)
        assert page.locator("#tab-agent").is_visible() and page.locator("#tab-inbox").is_hidden()
        assert page.get_by_test_id("package").first.is_visible() and page.locator("#runView").is_visible()
        # a sub-tab picked while boot loads is kept too
        page.goto(base + "/?tab=settings&sub=ai")
        page.wait_for_function("() => typeof window.showTab === 'function'")
        page.evaluate("() => { showTab('settings', 'ai'); document.querySelector('[data-testid=settings-tab-integrations]').click(); }")
        page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
        page.wait_for_timeout(300)
        pw.expect(page.get_by_test_id("settings-tab-integrations")).to_have_attribute("aria-selected", "true")
        assert page.get_by_test_id("ai-engine-picker").is_hidden()
        # nothing opened while boot loads: it lands on the Inbox as before
        page.goto(base + "/")
        page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
        assert page.locator("#tab-inbox").is_visible() and page.locator("#tab-agent").is_hidden()
        assert not errors, errors
    finally:
        ctx.close()


def test_slow_boot_fills_the_screen_opened_while_it_loaded(live, chromium):
    """Settings -> Integrations opened while boot waited on /api/channels drew with no channels. When boot finishes it
    draws the open screen again, once: the Settings channel list and the Integrations tiles fill without a reload and
    without a second /api/channels or /api/integrations; a screen opened after boot's data arrived is not drawn twice."""
    base, app, tmp = live
    ctx, page = _page(chromium, base, 1280)
    ctx.add_init_script(SLOW_BOOT)
    errors, hits = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: hits.append(r.url.split("?")[0].replace(base, "")) if "/api/" in r.url else None)
    try:
        page.goto(base + "/")
        page.wait_for_function("() => typeof window.showTab === 'function' && typeof api === 'function'")
        assert page.evaluate("() => !document.body.dataset.booted"), "boot finished before the delayed /api/channels"
        page.evaluate("() => showTab('settings', 'integrations')")
        page.get_by_test_id("integ-tile-buffer").wait_for()  # the tiles drew while boot was still waiting
        assert page.evaluate("() => !document.body.dataset.booted")
        assert page.get_by_test_id("settings-channel").count() == 0
        assert page.get_by_test_id("integ-tile-instagram").get_attribute("data-on") == "0"
        page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
        pw.expect(page.get_by_test_id("integ-tile-instagram")).to_have_attribute("data-on", "1")
        pw.expect(page.get_by_test_id("settings-channel")).to_have_count(len(OWNER_CHANNELS))
        pw.expect(page.get_by_test_id("settings-tab-integrations")).to_have_attribute("aria-selected", "true")
        assert page.locator("#tab-settings").is_visible() and page.locator("#tab-inbox").is_hidden()
        assert page.evaluate("() => window.HQ_RELOADS") == 1
        page.wait_for_timeout(500)
        assert hits.count("/api/channels") == 1, hits
        assert hits.count("/api/integrations") == 1, hits
        # nothing opened while boot loaded: boot lands on the Inbox itself and draws nothing twice
        page.goto(base + "/")
        page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
        assert page.locator("#tab-inbox").is_visible()
        assert not page.evaluate("() => window.HQ_RELOADS")
        assert not errors, errors
    finally:
        ctx.close()


def test_owner_sequence_in_the_browser(live, chromium):
    base, app, tmp = live
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x568:d=1", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(tmp / "clip.mp4")], check=True)
    rid = _seed(app)
    SHOTS.mkdir(parents=True, exist_ok=True)
    ctx, page = _page(chromium, base, 1280)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        _open(page, base, rid)
        # 1 the template, why, and the chips with previews drawn from this post
        page.get_by_test_id("mp-template-chip").first.wait_for()
        assert page.get_by_test_id("mp-template-name").inner_text() == "GitHub repo pillar"
        assert "GitHub repo link" in page.get_by_test_id("mp-template-why").inner_text()
        chips = page.get_by_test_id("mp-template-chip")
        assert 3 <= chips.count() <= 4 and page.locator("[data-testid=mp-template-chip] img").count() == chips.count()
        # the one rights question is a card with buttons, and the replies render markdown (no raw **)
        page.get_by_test_id("rights-card").wait_for()
        assert page.get_by_test_id("mp-ready-item").count() == 1
        page.screenshot(path=str(SHOTS / "1-template-rights-1280.png"), full_page=True)
        # 2 "change the template style the second slide first one is nice"
        _say(page, "can you change the template style the second slide first one is nice")
        page.wait_for_function("() => /slide 2:/.test(document.querySelector('[data-testid=mp-template-line]')?.innerText || '')")
        # 3 "where is the post": answered on the page, the card lights up
        _say(page, "where is the post>")
        assert "Your posts" in page.get_by_test_id("thread-msg").last.inner_text()
        assert "**" not in page.get_by_test_id("agent-thread").inner_text()
        page.wait_for_selector("#packages .pkg.flash", timeout=5000)
        # 4 one rights question -> Yes -> a Reel, never asked again
        page.get_by_test_id("rights-yes").click()
        page.wait_for_function("() => /^(Reel|Short|Video) ·/.test(document.querySelector('[data-testid=pkg-format]')?.innerText || '')")
        assert page.get_by_test_id("rights-card").count() == 0
        txt = page.get_by_test_id("agent-thread").inner_text()
        assert txt.count("Do you have the right") <= 1 and "now your Instagram Reel" in txt
        assert "couldn't tell what to change" not in txt and "undefined" not in txt  # one click = one answer, no stray send
        # 5 "what templates can you use"
        _say(page, "you said video you found video and also can you check what else brand templates you can use")
        assert "Templates I can use" in page.get_by_test_id("agent-thread").inner_text()
        # 6 the upload stays in the composer on a later turn, and goes straight into this post's Reel
        _say(page, "give me option to upload the video")
        up = page.get_by_test_id("reply-upload")
        assert up.is_visible() and "100 MB" in page.get_by_test_id("thread-msg").last.inner_text()
        if (tmp / "clip.mp4").exists():
            n = page.get_by_test_id("thread-msg").count()
            page.get_by_test_id("reply-file").set_input_files(str(tmp / "clip.mp4"))
            page.wait_for_function(f"() => document.querySelectorAll('[data-testid=thread-msg]').length >= {n + 2}"
                                   " && !document.querySelector('#mpStatus .spin')")
            assert "branded and ready as a Reel" in page.get_by_test_id("thread-msg").last.inner_text()
        # steps that ran again are one row with a count, and the trace is collapsed once done
        assert not page.get_by_test_id("mp-trace").evaluate("d => d.open")
        page.screenshot(path=str(SHOTS / "2-after-sequence-1280.png"), full_page=True)
        _no_overflow(page, 1280)
        assert not errors, errors
    finally:
        ctx.close()
    ctx, page = _page(chromium, base, 390, 844)
    try:
        _open(page, base, rid)
        page.get_by_test_id("mp-template-chip").first.wait_for()
        _no_overflow(page, 390)
        assert page.get_by_test_id("reply-upload").is_visible() and page.get_by_test_id("reply-go").is_visible()
        box = page.get_by_test_id("reply-form").bounding_box()
        assert box and box["width"] <= 390
        page.screenshot(path=str(SHOTS / "3-thread-390.png"), full_page=True)
        page.get_by_test_id("package").first.scroll_into_view_if_needed()
        page.screenshot(path=str(SHOTS / "4-card-390.png"))
    finally:
        ctx.close()
