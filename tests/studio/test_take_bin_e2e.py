"""One take and the one Bin in a real browser (owner ask, 5 Nov 2026; tests/studio/test_take_bin_ui.py is the REST and
wiring side). One take has no Bin of its own any more: a take deleted from Your takes goes to Library › Bin (Undo in the
toast brings it back), and the Library Bin lists binned takes and clips with days left, Restore and Delete permanently
(behind its confirm). Your takes cards never overlap at 1280, 1440, 2000 and 390 px: each card keeps its grid track, a long
title ends in an ellipsis before the ⋯ button, and the ⋯ menu paints above the cards. A failed list load shows Retry, and
leaving One take aborts the list request in flight. HQ_SHOTS_DIR=<dir> saves screenshots."""
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
# a /api/takes list answer held back 1.5 s while window.__slowTakes is set, so leaving One take can abort it; aborts counted
SLOW_TAKES = """(() => { const f = window.fetch.bind(window); window.__aborted = 0;
  window.fetch = (u, o) => {
    if (!window.__slowTakes || !/\\/api\\/takes(\\?|$)/.test(String(u))) return f(u, o);
    return new Promise((ok, bad) => {
      const t = setTimeout(() => f(u, o).then(ok, bad), 1500);
      if (o && o.signal) o.signal.addEventListener('abort', () => { clearTimeout(t); window.__aborted++; bad(new DOMException('aborted', 'AbortError')); });
    });
  }; })()"""
LONG = ["GTA 6 Extended Look - One Take", "Everything You Need to Know Before the Big Launch Keynote, Part Two (Extended Cut)",
        "The Light Microscopy Module on the ISS and what it means for biology", "hq-take-talk",
        "Everything You Need to Know Before You Start Investing in Index Funds as a Beginner in 2026"]


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
    long_ids = [_run(st, t, "failed" if i % 2 else "ready", {"clips": [{"id": "c1", "status": "ready", "hook": "x"}] * (i % 3)}) for i, t in enumerate(LONG * 2)]
    old = _run(st, "Old podcast", "binned", {"clips": [{"id": "c1", "status": "ready"}], "bin": {"at": now - 2 * 86400, "status": "ready", "tokens": []}})
    kick = _run(st, "Kickoff talk", "ready", {"clips": [
        {"id": "c1", "status": "ready", "hook": "Ship it small"},
        {"id": "c2", "status": "binned", "hook": "The one we cut", "bin": {"at": now - 6.5 * 86400, "status": "ready", "tokens": []}}]})
    launch = _run(st, "Launch Q&A", "ready", {"clips": [{"id": "c1", "status": "ready", "hook": "Why now"}]})
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "old": old, "kick": kick, "launch": launch, "n": len(long_ids) + 2}
    server.should_exit = True


def _ctx(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin", color_scheme="dark")
    ctx.add_init_script(NO_TOUR)
    ctx.add_init_script(SLOW_TAKES)
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


def _delete_take(page, title):
    card = page.locator("[data-testid=take-card]", has_text=title)
    card.get_by_test_id("take-more").click()
    card.get_by_test_id("take-delete").click()
    page.get_by_test_id("ask-ok").click()


@pytest.mark.parametrize("w", [1280, 390])
def test_one_take_deletes_go_to_the_library_bin_with_undo_restore_and_delete_forever(live, chromium, close_contexts, w):
    base, app, n = live["base"], live["app"], live["n"]
    ctx, page, errors = _ctx(chromium, base, w)
    _one_take(page, base)
    # one Bin: no Takes / Bin toggle on One take, a line pointing at Library › Bin instead
    assert page.get_by_test_id("take-view-bin").count() == 0 and page.get_by_test_id("take-bin").count() == 0
    assert "Library › Bin" in page.get_by_test_id("take-bin-hint").inner_text()
    assert page.get_by_test_id("take-row").count() == n
    # delete from the card's ⋯ menu: it leaves Your takes, and the toast's Undo brings it straight back
    _delete_take(page, "Launch Q&A")
    page.get_by_test_id("toast-undo").wait_for()
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=take-row]').length === {n - 1}")
    assert _status(app, live["launch"]) == "binned"
    page.get_by_test_id("toast-undo").click()
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=take-row]').length === {n}")
    assert _status(app, live["launch"]) == "ready"
    _delete_take(page, "Launch Q&A")
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=take-row]').length === {n - 1}")
    # the Library's Bin lists the binned takes and the binned clip, each with its days left
    page.evaluate("() => showTab('library')")
    page.locator("#libViewBin").click()
    page.get_by_test_id("bin-item").first.wait_for()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(3)
    page.wait_for_function("() => document.getElementById('libBinCount').textContent === '3'")
    old = page.locator("[data-testid=bin-item][data-kind=take]", has_text="Old podcast")
    clip = page.locator("[data-testid=bin-item][data-kind=clip]")
    assert "5 days left" in old.get_by_test_id("bin-days-left").inner_text()
    assert "1 day left" in clip.get_by_test_id("bin-days-left").inner_text()
    assert "The one we cut" in clip.inner_text() and "Kickoff talk" in clip.inner_text()
    _shot(page, f"one-bin-{w}.png")
    if w == 390:
        _no_overflow(page)
    # Restore the clip: it leaves the Bin and is back on its take
    clip.get_by_test_id("bin-restore").click()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(2)
    res = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (live["kick"],))[0]["result"])
    assert {c["id"]: c["status"] for c in res["clips"]}["c2"] == "ready"
    # Delete permanently asks first; Cancel keeps it
    old.get_by_test_id("bin-delete-permanently").click()
    assert "can't be undone" in page.get_by_test_id("ask-message").inner_text()
    page.get_by_test_id("ask-cancel").click()
    assert _status(app, live["old"]) == "binned"
    old.get_by_test_id("bin-delete-permanently").click()
    page.get_by_test_id("ask-ok").click()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(1)
    assert _status(app, live["old"]) is None
    # Restore the take from the Library Bin: it's back in Your takes
    page.locator("[data-testid=bin-item][data-kind=take]", has_text="Launch Q&A").get_by_test_id("bin-restore").click()
    page.get_by_test_id("bin-empty").wait_for()
    assert _status(app, live["launch"]) == "ready"
    assert not errors, errors


GEOMETRY = """() => {
  const cards = [...document.querySelectorAll('#takeList [data-testid=take-card]')].map((c) => {
    const r = c.getBoundingClientRect(), b = c.querySelector('[data-testid=take-title]'), m = c.querySelector('[data-testid=take-more]');
    const br = b.getBoundingClientRect();
    return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, title: b.textContent, tr: br.right, tl: br.left,
             ml: m ? m.getBoundingClientRect().left : null, ell: getComputedStyle(b).textOverflow, clipped: b.scrollWidth > b.clientWidth };
  });
  const list = document.getElementById('takeList').getBoundingClientRect();
  return { cards, list: { l: list.left, r: list.right } };
}"""


@pytest.mark.parametrize("w", [1280, 1440, 2000, 390])
def test_take_cards_never_overlap_and_long_titles_end_in_an_ellipsis(live, chromium, close_contexts, w):
    base = live["base"]
    ctx, page, errors = _ctx(chromium, base, w)
    _one_take(page, base)
    g = page.evaluate(GEOMETRY)
    cards = g["cards"]
    assert len(cards) == live["n"]
    for i, a in enumerate(cards):
        assert a["l"] >= g["list"]["l"] - 0.5 and a["r"] <= g["list"]["r"] + 0.5, a          # inside its grid
        assert abs(a["w"] - cards[0]["w"]) < 1, (a, cards[0])                                   # one track width
        if a["ml"] is not None:
            assert a["tr"] <= a["ml"] + 0.5, a                                                  # the title stops before ⋯
        assert a["ell"] == "ellipsis"
        for b in cards[i + 1:]:
            overlap = min(a["r"], b["r"]) - max(a["l"], b["l"]) > 0.5 and min(a["b"], b["b"]) - max(a["t"], b["t"]) > 0.5
            assert not overlap, (a, b)
    assert any(c["clipped"] for c in cards if "Index Funds" in c["title"])                  # a long title is cut, not spilled
    _no_overflow(page)
    # the ⋯ menu opens above the cards: every point of it is the menu, never a card under or after it
    page.get_by_test_id("take-more").first.click()
    inside = page.evaluate("""() => { const m = document.querySelector('#takeList .lp-menu:not([hidden])'); const r = m.getBoundingClientRect();
      const pts = []; for (const fx of [.1, .5, .9]) for (const fy of [.25, .75]) pts.push(m.contains(document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy)));
      return pts; }""")
    assert all(inside), inside
    _shot(page, f"take-cards-{w}.png")
    page.keyboard.press("Escape")
    assert page.locator("#takeList .lp-menu:not([hidden])").count() == 0
    assert not errors, errors


@pytest.mark.parametrize("w", [1280, 390])
def test_take_list_error_retry_and_leaving_one_take_aborts_the_load(live, chromium, close_contexts, w):
    base = live["base"]
    ctx, page, errors = _ctx(chromium, base, w)
    fail = lambda r: r.fulfill(status=500, content_type="application/json", body='{"error": "Takes are resting."}')  # noqa: E731
    page.route("**/api/takes", fail)
    page.goto(base + "/?tab=take", wait_until="domcontentloaded")
    err = page.get_by_test_id("take-list-error")
    err.wait_for()
    assert "Takes are resting." in err.inner_text() and page.get_by_test_id("take-row").count() == 0
    if w == 390:
        _no_overflow(page)
    page.unroute("**/api/takes", fail)
    page.get_by_test_id("take-list-retry").click()
    page.get_by_test_id("take-row").first.wait_for()
    # leaving One take while its list loads aborts that request; the late answer never draws
    page.evaluate("() => { window.__slowTakes = true; window.__aborted = 0; showTab('library'); showTab('take'); }")
    page.evaluate("() => showTab('library')")
    page.wait_for_function("() => window.__aborted >= 1")
    page.evaluate("() => { window.__slowTakes = false; }")
    assert not errors, errors
