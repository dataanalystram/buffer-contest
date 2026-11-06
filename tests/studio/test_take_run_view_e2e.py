"""One take, one run at a time, in a real browser (owner ask, 6 Nov 2026; tests/studio/test_take_run_view.py is the REST and
wiring side). Opening a run from Your takes shows a run bar (All takes, the run's title, when it started, its status, New
take) and the upload form steps aside; the run is in the address, so browser Back returns to Your takes and Forward reopens
it; All takes keeps what was typed, New take clears the form; every clip row shows "Score 8.9" and its reason (or "Not
scored"), sorted by score; opening shows a loader in place, and leaving while a run is still opening aborts it so it is
never drawn. 390 and 1280 px, no sideways scroll. HQ_SHOTS_DIR=<dir> saves screenshots (onetake_run_<w>.png)."""
import json
import os
import time

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from . import conftest
from .browsers import free_port, serve
from .test_agent_core import OWNER_CHANNELS, Router

pw = pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_SHOTS_DIR", "")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
# GET /api/takes/<id> held back 1.5 s while window.__slowTake is set, so a view change can abort it; aborts counted
SLOW_TAKE = """(() => { const f = window.fetch.bind(window); window.__aborted = 0;
  window.fetch = (u, o) => {
    if (!window.__slowTake || !/\\/api\\/takes\\/\\d+$/.test(String(u))) return f(u, o);
    return new Promise((ok, bad) => {
      const t = setTimeout(() => f(u, o).then(ok, bad), 1500);
      if (o && o.signal) o.signal.addEventListener('abort', () => { clearTimeout(t); window.__aborted++; bad(new DOMException('aborted', 'AbortError')); });
    });
  }; })()"""
STEPS = [{"key": k, "label": l, "status": "done", "detail": d, "data": None} for k, l, d in (
    ("upload", "Read your video", "Kickoff talk.mp4 · 412 MB"), ("transcribe", "Transcribe", "5,120 words with timestamps"),
    ("moments", "Find the best moments", "3 moments · picked by the built-in scorer"), ("reframe", "Reframe to 9:16", "Face-tracked"),
    ("captions", "Burn in brand captions", "Word-by-word captions"), ("covers", "Branded covers + end card", "On every clip"),
    ("preflight", "Preflight every clip", "3 clips × 0 channel(s): 3 pass"), ("ready", "Ready to spread", "Buffer drafts"))]


def _clip(base, cid, n, start, **kw):
    return {"id": cid, "moment_id": "m" + cid[1:], "status": "ready", "start": start, "end": start + 31.5, "text": "…", "captions": {},
            "placements": [], "cover_url": f"{base}/sample/cover{n}.jpg", **kw}


def _run(st, name, status, res, created):
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                ("acc_1", "take", json.dumps({"name": name, "channels": []}), status, json.dumps(STEPS), json.dumps(res), created, created))


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = conftest.FakeBuffer()
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    s = load_settings(client_id="cid", client_secret="sec", public_url=base, session_secret="b" * 64,
                      db_path=str(tmp_path / "rv.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-rv", "csrf-rv"); st.update_session("sid-rv", user_id="acc_1")
    now = time.time()
    kick = _run(st, "Kickoff talk", "ready", {"source": {"title": "Kickoff talk", "duration": 1803.4}, "picked_by": "the built-in scorer", "clips": [
        _clip(base, "c1", 1, 34.0, hook="How do you want to approach it?", score=82.4, why="Clear framework with a direct question that drives engagement."),
        _clip(base, "c2", 2, 120.0, hook="Why this moment is a great starting point", score=89.0, why="Strong opening with a clear question that hooks the viewer and sets up the topic."),
        _clip(base, "c3", 3, 300.0, hook="You can ask questions in the support channel"),       # an older clip: only its moment scored it
        _clip(base, "c4", 4, 400.0, hook="Justina, did you have anything to add there?"),      # never scored at all
    ], "moments": [{"id": "m3", "score": 79.0, "why": "A practical call to action with clear next steps for the audience."}]}, now - 3600)
    launch = _run(st, "Launch Q&A", "ready", {"clips": [_clip(base, "c1", 5, 10.0, hook="Why now")]}, now - 86400)
    server = serve(app, port)
    yield {"base": base, "app": app, "kick": kick, "launch": launch}
    server.should_exit = True


def _ctx(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin", color_scheme="dark")
    ctx.add_init_script(NO_TOUR)
    ctx.add_init_script(SLOW_TAKE)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-rv", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


def _one_take(page, base):
    for i in range(3):
        try:
            page.goto(base + "/?tab=take", wait_until="domcontentloaded")
            page.get_by_test_id("take-row").first.wait_for(timeout=15000)
            return
        except Exception:  # noqa: BLE001  a slow first paint on a busy box: retry
            if i == 2:
                raise


def _open(page, title):
    page.locator("[data-testid=take-card]", has_text=title).get_by_test_id("take-row").click()
    pw.expect(page.get_by_test_id("take-run-title")).to_have_text(title)
    page.get_by_test_id("clip-card").first.wait_for()


def _no_overflow(page):
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


@pytest.mark.parametrize("w", [1280, 390])
def test_a_run_has_back_new_take_and_a_score_and_reason_on_every_clip(live, chromium, close_contexts, w):
    base, kick, launch = live["base"], live["kick"], live["launch"]
    ctx, page, errors = _ctx(chromium, base, w)
    _one_take(page, base)
    compose, view = page.locator("#takeCompose"), page.get_by_test_id("take-view")
    assert compose.is_visible() and view.is_hidden()
    page.get_by_test_id("take-src-link").click()
    page.get_by_test_id("take-url").fill("https://youtu.be/abc123")              # typed before opening a run
    _open(page, "Kickoff talk")
    # which run this is: title, number, when it started, status, clip count; the form steps aside
    assert compose.is_hidden() and page.get_by_test_id("take-runbar").is_visible()
    assert f"Run #{kick}" in page.get_by_test_id("take-run-when").inner_text() and "started" in page.get_by_test_id("take-run-when").inner_text()
    assert page.get_by_test_id("take-run-status").inner_text() == "Ready to spread"
    assert page.get_by_test_id("take-back").is_visible() and page.get_by_test_id("take-new").is_visible()
    assert f"take={kick}" in page.url
    # every row: the score out of 10 and its reason (or Not scored), highest first, unscored last
    scores = [x.inner_text() for x in page.get_by_test_id("clip-score").all()]
    assert scores == ["Score 8.9", "Score 8.2", "Score 7.9", "Not scored"], scores
    whys = [x.inner_text() for x in page.get_by_test_id("clip-why").all()]
    assert whys[0].startswith("Strong opening with a clear question") and "practical call to action" in whys[2]
    assert all(x.strip() for x in whys) and "Not scored" in whys[3]
    page.get_by_test_id("clip-sort").select_option("time")
    assert [x.inner_text() for x in page.get_by_test_id("clip-score").all()] == ["Score 8.2", "Score 8.9", "Score 7.9", "Not scored"]
    page.get_by_test_id("clip-sort").select_option("score")
    assert page.get_by_test_id("clip-score").first.inner_text() == "Score 8.9"
    _no_overflow(page)
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.wait_for_timeout(300)
        page.screenshot(path=os.path.join(SHOTS, f"onetake_run_{w}.png"), full_page=w == 390)
    # All takes: back to the form and the list, what was typed is still there, the address drops the run
    page.get_by_test_id("take-back").click()
    assert view.is_hidden() and compose.is_visible() and "take=" not in page.url
    assert page.get_by_test_id("take-url").input_value() == "https://youtu.be/abc123"
    # browser Back reopens the run, Back again returns to Your takes
    page.go_back()
    pw.expect(page.get_by_test_id("take-run-title")).to_have_text("Kickoff talk")
    page.get_by_test_id("clip-card").first.wait_for()
    assert compose.is_hidden() and f"take={kick}" in page.url
    page.go_back()
    pw.expect(view).to_be_hidden()
    assert compose.is_visible() and "take=" not in page.url
    # another run is never confused with the first: its own title, number and clips
    _open(page, "Launch Q&A")
    assert f"Run #{launch}" in page.get_by_test_id("take-run-when").inner_text()
    assert [x.inner_text() for x in page.get_by_test_id("clip-score").all()] == ["Not scored"]
    # a reload keeps the open run (it is in the address)
    page.reload(wait_until="domcontentloaded")
    pw.expect(page.get_by_test_id("take-run-title")).to_have_text("Launch Q&A")
    # New take: a fresh form (no link, rights box unticked, upload selected), no run open
    page.get_by_test_id("take-new").click()
    assert view.is_hidden() and compose.is_visible() and "take=" not in page.url
    assert page.get_by_test_id("take-src-upload").get_attribute("aria-selected") == "true"
    assert page.get_by_test_id("take-url").input_value() == "" and not page.get_by_test_id("take-owns").is_checked()
    # a run still opening shows a loader in place; leaving it aborts the open, and its late answer is never drawn
    page.evaluate("() => { window.__slowTake = true; }")
    page.locator("[data-testid=take-card]", has_text="Kickoff talk").get_by_test_id("take-row").click()
    page.get_by_test_id("take-open-loading").wait_for()
    assert page.get_by_test_id("take-view").get_attribute("aria-busy") == "true"
    assert page.get_by_test_id("take-run-title").inner_text() == "Opening the take…"
    page.get_by_test_id("take-back").click()
    page.wait_for_timeout(2000)
    assert view.is_hidden() and compose.is_visible() and page.evaluate("() => window.__aborted") >= 1
    _no_overflow(page)
    assert not errors, errors


def test_a_take_that_does_not_open_says_so_with_retry(live, chromium, close_contexts):
    base = live["base"]
    ctx, page, errors = _ctx(chromium, base, 1280)
    page.goto(base + "/?tab=take&take=999999", wait_until="domcontentloaded")
    page.get_by_test_id("take-open-error").wait_for()
    assert page.get_by_test_id("take-open-retry").is_visible() and page.get_by_test_id("take-back").is_visible()
    page.get_by_test_id("take-back").click()
    assert page.locator("#takeCompose").is_visible() and "take=" not in page.url
    assert not errors, errors
