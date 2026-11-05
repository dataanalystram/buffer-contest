"""One take's Bin in a real browser at 1280 and 390 px (owner ask, 5 Nov 2026; tests/studio/test_take_bin_ui.py is the
REST and wiring side). Covers: the Bin tab and its count, a delete from Your takes with Undo in the toast, the Bin's rows
with days left, Restore, Delete forever behind its confirm (Cancel keeps it), a failed load showing its error with Retry,
a view change cancelling the Bin request in flight, and no horizontal overflow at 390 px. HQ_SHOTS_DIR=<dir> saves
screenshots."""
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
# a /api/takes/bin answer held back 1.5 s while window.__slowBin is set, so a view change can abort it; aborts are counted
SLOW_BIN = """(() => { const f = window.fetch.bind(window); window.__aborted = 0;
  window.fetch = (u, o) => {
    if (!window.__slowBin || !String(u).includes('/api/takes/bin')) return f(u, o);
    return new Promise((ok, bad) => {
      const t = setTimeout(() => f(u, o).then(ok, bad), 1500);
      if (o && o.signal) o.signal.addEventListener('abort', () => { clearTimeout(t); window.__aborted++; bad(new DOMException('aborted', 'AbortError')); });
    });
  }; })()"""


def _run(st, name, status, res):
    return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                ("acc_1", "take", json.dumps({"name": name, "channels": []}), status, "[]", json.dumps(res), time.time(), time.time()))


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = conftest.FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="b" * 64,
                      db_path=str(tmp_path / "tb.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-tb", "csrf-tb"); st.update_session("sid-tb", user_id="acc_1")
    now = time.time()
    old = _run(st, "Old podcast", "binned", {"clips": [{"id": "c1", "status": "ready"}], "bin": {"at": now - 2 * 86400, "status": "ready", "tokens": []}})
    kick = _run(st, "Kickoff talk", "ready", {"clips": [
        {"id": "c1", "status": "ready", "hook": "Ship it small"},
        {"id": "c2", "status": "binned", "hook": "The one we cut", "bin": {"at": now - 6.5 * 86400, "status": "ready", "tokens": []}}]})
    launch = _run(st, "Launch Q&A", "ready", {"clips": [{"id": "c1", "status": "ready", "hook": "Why now"}]})
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "old": old, "kick": kick, "launch": launch}
    server.should_exit = True


def _ctx(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_init_script(SLOW_BIN)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-tb", "url": base}])
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


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def _no_overflow(page):
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def _status(app, rid):
    rows = app.state.store.q("SELECT status FROM agent_runs WHERE id=?", (rid,))
    return rows[0]["status"] if rows else None


@pytest.mark.parametrize("w", [1280, 390])
def test_take_bin_delete_undo_restore_and_delete_forever(live, chromium, close_contexts, w):
    base, app = live["base"], live["app"]
    ctx, page, errors = _ctx(chromium, base, w)
    _one_take(page, base)
    assert page.get_by_test_id("take-row").count() == 2
    page.wait_for_function("() => document.getElementById('tkBinCount').textContent === '2'")
    # delete a take from Your takes: it goes to the Bin, and the toast's Undo brings it straight back
    row = page.locator(".take-rowwrap", has_text="Launch Q&A")
    row.get_by_test_id("take-delete").click()
    page.get_by_test_id("ask-ok").click()
    page.get_by_test_id("toast-undo").wait_for()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=take-row]').length === 1")
    assert _status(app, live["launch"]) == "binned"
    page.get_by_test_id("toast-undo").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=take-row]').length === 2")
    assert _status(app, live["launch"]) == "ready"
    # the Bin: a take and a clip, each with its days left
    page.get_by_test_id("take-view-bin").click()
    assert page.get_by_test_id("take-view-bin").get_attribute("aria-selected") == "true"
    page.get_by_test_id("take-bin-item").first.wait_for()
    assert page.get_by_test_id("take-list").is_hidden()
    assert page.get_by_test_id("take-bin-item").count() == 2
    take_row = page.locator("[data-testid=take-bin-item][data-kind=take]")
    clip_row = page.locator("[data-testid=take-bin-item][data-kind=clip]")
    assert "5 days left" in take_row.get_by_test_id("take-bin-days-left").inner_text()
    assert "1 day left" in clip_row.get_by_test_id("take-bin-days-left").inner_text()
    assert "Kickoff talk" in clip_row.inner_text() and "The one we cut" in clip_row.inner_text()
    _shot(page, f"take-bin-{w}.png")
    if w == 390:
        _no_overflow(page)
    # Restore the clip: it leaves the Bin and is back on its take
    clip_row.get_by_test_id("take-bin-restore").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=take-bin-item]').length === 1")
    res = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (live["kick"],))[0]["result"])
    assert {c["id"]: c["status"] for c in res["clips"]}["c2"] == "ready"
    # Delete forever asks first; Cancel keeps it
    take_row.get_by_test_id("take-bin-delete-forever").click()
    assert "can't be undone" in page.get_by_test_id("ask-message").inner_text()
    page.get_by_test_id("ask-cancel").click()
    assert _status(app, live["old"]) == "binned" and page.get_by_test_id("take-bin-item").count() == 1
    take_row.get_by_test_id("take-bin-delete-forever").click()
    page.get_by_test_id("ask-ok").click()
    page.get_by_test_id("take-bin-empty").wait_for()
    assert _status(app, live["old"]) is None
    page.wait_for_function("() => document.getElementById('tkBinCount').hidden")
    assert not errors, errors


@pytest.mark.parametrize("w", [1280, 390])
def test_take_bin_error_retry_and_a_view_change_cancels_the_load(live, chromium, close_contexts, w):
    base = live["base"]
    ctx, page, errors = _ctx(chromium, base, w)
    _one_take(page, base)
    # a failed load shows a plain message with Retry, never an empty Bin
    fail = lambda r: r.fulfill(status=500, content_type="application/json", body='{"error": "The Bin is resting."}')  # noqa: E731
    page.route("**/api/takes/bin", fail)
    page.get_by_test_id("take-view-bin").click()
    err = page.get_by_test_id("take-bin-error")
    err.wait_for()
    assert "The Bin is resting." in err.inner_text() and page.get_by_test_id("take-bin-item").count() == 0
    _shot(page, f"take-bin-error-{w}.png")
    if w == 390:
        _no_overflow(page)
    page.unroute("**/api/takes/bin", fail)
    page.get_by_test_id("take-bin-retry").click()
    page.get_by_test_id("take-bin-item").first.wait_for()
    assert page.get_by_test_id("take-bin-item").count() == 2
    # a view change while the Bin is loading aborts that request; its late answer never draws
    page.get_by_test_id("take-view-takes").click()
    page.get_by_test_id("take-row").first.wait_for()
    page.evaluate("() => { document.getElementById('takeBin').innerHTML = ''; window.__slowBin = true; window.__aborted = 0; }")
    page.get_by_test_id("take-view-bin").click()
    page.get_by_test_id("take-view-takes").click()
    page.wait_for_function("() => window.__aborted >= 1")
    page.wait_for_timeout(1800)  # past the held answer
    assert page.get_by_test_id("take-bin").is_hidden() and page.get_by_test_id("take-list").is_visible()
    assert page.locator("#takeBin [data-testid=take-bin-item]").count() == 0
    # leaving One take aborts a load in flight too
    page.get_by_test_id("take-view-bin").click()
    page.evaluate("() => { window.__aborted = 0; }")  # only the Bin's own request is in flight now
    page.evaluate("() => showTab('library')")
    page.wait_for_function("() => window.__aborted >= 1")
    page.evaluate("() => { window.__slowBin = false; }")
    assert not errors, errors
