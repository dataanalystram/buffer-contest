"""The Library's One take filter, View run of a One take clip, and menu layering, in a real browser (owner bugs, 5 Nov 2026;
tests/studio/test_library_one_take.py is the REST side).

* Created by › One take asks the server for created_by=one_take and lists only One take's clips and the video uploaded to
  it, each with the usual actions (⋯: View run, Open full size, Move to bin). Moving a clip to the bin puts it in the
  Library Bin (it's the take's Bin), and Undo brings it back.
* View run on a One take clip goes straight to One take: Make a post (Threads) is never shown in between, even while the
  run is slow to answer, and Back returns to the Library.
* Menus layer correctly: closing a card's ⋯ menu leaves no raised card behind (the stale z-index that put the first card
  over the Created by menu), a menu opened later paints above every card, and no menu is open after a reload or after
  leaving the Library and coming back. HQ_SHOTS_DIR=<dir> saves screenshots."""
import json
import os
import time

import httpx
import pytest

from studio import house
from studio.app import create_app
from studio.config import load_settings

from . import conftest
from .browsers import free_port, serve
from .conftest import FakeBuffer
from .test_agent_core import OWNER_CHANNELS, Router, jpeg
from .test_makepost_library import MP4

pw = pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_SHOTS_DIR", "")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
A = house.Actor("acc_1")
# every screen shown, in order (window.__tabs), so a flash of another screen is caught however short
TABS = """(() => { window.__tabs = []; const rec = () => { const t = document.querySelector('.tab:not([hidden])');
  const id = t ? t.id : ''; if (window.__tabs[window.__tabs.length - 1] !== id) window.__tabs.push(id); };
  new MutationObserver(rec).observe(document, { subtree: true, attributes: true, attributeFilter: ['hidden'] }); })()"""


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="o" * 64,
                      db_path=str(tmp_path / "ot.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(FakeBuffer()))))
    st, h = app.state.store, app.state.house
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-ot", "csrf-ot"); st.update_session("sid-ot", user_id="acc_1")
    ups = [h.library.save_bytes(A, jpeg(600 + i * 7, 750, (40 + i * 9, 60, 120)), f"photo {i + 1}.jpg")["id"] for i in range(8)]
    talk = h.library.save_bytes(A, MP4 + b"talk", "conference talk.mp4", "take")["id"]
    t1 = h.put_media(A, MP4 + b"1", "c1.mp4", kind="render", meta={"type": "video"})
    t2 = h.put_media(A, MP4 + b"2", "c2.mp4", kind="render", meta={"type": "video"})
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               ("acc_1", "take", json.dumps({"name": "GTA 6 Extended Look", "channels": []}), "ready", "[]",
                json.dumps({"clips": [{"id": "c1", "status": "ready", "token": t1, "hook": "The map is huge", "start": 1, "end": 21, "score": 80},
                                      {"id": "c2", "status": "ready", "token": t2, "hook": "Vice City is back", "start": 30, "end": 52, "score": 70}]}),
                time.time(), time.time()))
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "rid": rid, "talk": talk, "t1": t1, "ups": ups, "h": h}
    server.should_exit = True


def _page(chromium, base, w, h=900, url="/?tab=library"):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin", color_scheme="dark")
    ctx.add_init_script(NO_TOUR)
    ctx.add_init_script(TABS)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-ot", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors, calls = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: calls.append(r.url) if "/api/library?" in r.url else None)
    for i in range(3):
        try:
            page.goto(base + url, wait_until="domcontentloaded")
            page.get_by_test_id("library-item").first.wait_for(timeout=15000)
            break
        except Exception:  # noqa: BLE001  a slow first paint on a busy box: retry
            if i == 2:
                raise
    return page, errors, calls


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def _filter_one_take(page):
    page.get_by_test_id("library-filter-created-by").click()
    page.locator("#libPills [data-testid=library-filter-option][data-v=one_take]").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 3")


def _clip_status(app, rid, cid):
    res = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    return {c["id"]: c["status"] for c in res["clips"]}[cid]


@pytest.mark.parametrize("w", [1280, 390])
def test_the_one_take_filter_lists_only_one_takes_items_with_the_usual_actions(live, chromium, close_contexts, w):
    base, app = live["base"], live["app"]
    page, errors, calls = _page(chromium, base, w)
    assert page.get_by_test_id("library-item").count() == 11
    _filter_one_take(page)
    assert any("created_by=one_take" in u for u in calls), calls                         # filtered on the server
    assert "One take" in page.get_by_test_id("library-filter-created-by").inner_text()
    pw.expect(page.get_by_test_id("library-by-one-take")).to_have_count(3)
    titles = page.locator("[data-testid=library-item] .lp-t").all_inner_texts()
    assert "conference talk.mp4" in titles and not any(t.startswith("photo") for t in titles)
    _shot(page, f"library-one-take-{w}.png")
    # the same ⋯ actions as any item; Move to bin on a clip sends it to its take's Bin, Undo brings it back
    clip = page.locator("[data-testid=library-item]", has=page.locator("text=Made by One take")).first
    clip.get_by_test_id("library-more").click()
    for tid in ("library-view-run", "library-open-full", "library-use", "library-delete"):
        assert clip.get_by_test_id(tid).count() == 1, tid
    clip.get_by_test_id("library-delete").click()
    page.get_by_test_id("library-delete-go").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 2")
    binned = [c for c in ("c1", "c2") if _clip_status(app, live["rid"], c) == "binned"]
    assert len(binned) == 1
    page.get_by_test_id("lib-undo").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 3")
    assert _clip_status(app, live["rid"], binned[0]) == "ready"
    # moved again, it is listed in the Library Bin as a One take clip
    clip.get_by_test_id("library-more").click()
    clip.get_by_test_id("library-delete").click()
    page.get_by_test_id("library-delete-go").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 2")
    page.locator("#libViewBin").click()
    pw.expect(page.locator("[data-testid=bin-item][data-kind=clip]")).to_have_count(1)
    assert not errors, errors


def test_view_run_goes_straight_to_one_take_and_back_returns_to_the_library(live, chromium, close_contexts):
    base = live["base"]
    page, errors, _ = _page(chromium, base, 1280)
    _filter_one_take(page)
    # a slow run answer: the old code showed Make a post (Threads) while it waited
    page.route("**/api/agent/runs/*", lambda r: (time.sleep(0.8), r.continue_()))
    clip = page.locator("[data-testid=library-item]", has=page.locator("text=Made by One take")).first
    clip.get_by_test_id("library-more").click()
    page.evaluate("() => { window.__tabs = ['tab-library']; window.__h0 = history.length; }")
    clip.get_by_test_id("library-view-run").click()
    page.wait_for_function("() => !document.getElementById('tab-take').hidden && !document.getElementById('takeView').hidden")
    page.get_by_test_id("clip-card").first.wait_for()
    tabs = page.evaluate("() => window.__tabs")
    assert tabs == ["tab-library", "tab-take"], tabs                                     # no Threads in between
    assert page.evaluate("() => history.state && history.state.hq") == "take"
    assert page.evaluate("() => history.length - window.__h0") == 1                       # one entry, not two
    _shot(page, "view-run-one-take.png")
    page.go_back()
    page.wait_for_function("() => !document.getElementById('tab-library').hidden")
    assert page.locator("#tab-agent").is_hidden()
    assert not errors, errors


MENU_POINTS = """(sel) => { const m = document.querySelector(sel); if (!m) return null; const r = m.getBoundingClientRect(); const out = [];
  for (const fx of [.1, .5, .9]) for (const fy of [.15, .5, .85]) { const x = r.left + r.width * fx, y = r.top + r.height * fy;
    out.push({ x, y, ok: m.contains(document.elementFromPoint(x, y)) }); } return out; }"""


@pytest.mark.parametrize("w", [1280, 900])
def test_menus_layer_above_cards_after_a_card_menu_was_closed_and_never_open_on_load(live, chromium, close_contexts, w):
    base = live["base"]
    page, errors, _ = _page(chromium, base, w)
    # a card in the first row and the card right below it
    pair = page.evaluate("""() => { const cs = [...document.querySelectorAll('[data-testid=library-item]')];
      const r = cs.map((c) => c.getBoundingClientRect()); const a = 0; const b = r.findIndex((x, i) => i > 0 && Math.abs(x.left - r[0].left) < 2);
      return [cs[a].dataset.id, cs[b].dataset.id]; }""")
    top = page.locator(f"[data-testid=library-item][data-id='{pair[0]}']")
    below = page.locator(f"[data-testid=library-item][data-id='{pair[1]}']")
    below.get_by_test_id("library-more").click()
    page.keyboard.press("Escape")
    assert below.evaluate("(el) => el.style.zIndex") == ""                                # the card it raised is reset
    top.scroll_into_view_if_needed()
    page.evaluate("() => window.scrollTo(0, 0)")
    top.get_by_test_id("library-more").click()
    pts = page.evaluate(MENU_POINTS, f"[data-testid=library-item][data-id='{pair[0]}'] .lp-menu:not([hidden])")
    assert pts and all(p["ok"] for p in pts), pts                                          # never under the card below
    page.keyboard.press("Escape")
    # the Created by menu paints above every card
    page.get_by_test_id("library-filter-created-by").click()
    pts = page.evaluate(MENU_POINTS, "#libPills .lp-menu:not([hidden])")
    assert pts and all(p["ok"] for p in pts), pts
    _shot(page, f"library-menu-{w}.png")
    # leaving the Library with a menu open, then coming back: no menu is open
    page.evaluate("() => { showTab('inbox'); showTab('library'); }")
    page.get_by_test_id("library-item").first.wait_for()
    assert page.locator(".lp-menu:not([hidden])").count() == 0
    # a reload never opens a menu either
    page.get_by_test_id("library-filter-created-by").click()
    page.reload(wait_until="domcontentloaded")
    page.get_by_test_id("library-item").first.wait_for()
    assert page.locator(".lp-menu:not([hidden])").count() == 0
    assert page.locator("[data-testid=library-filter-created-by]").get_attribute("aria-expanded") == "false"
    assert not errors, errors


def test_a_failing_one_take_bin_still_shows_the_librarys_bin_items(live, chromium, close_contexts):
    """CodeRabbit (PR #115): /api/takes/bin failing must not hide the Library's own Bin items; a toast says what's missing."""
    base = live["base"]
    live["h"].library.delete(A, live["ups"][0])
    page, errors, _ = _page(chromium, base, 1280)
    page.route("**/api/takes/bin", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error": "Takes Bin is resting."}'))
    page.locator("#libViewBin").click()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(1)
    pw.expect(page.locator("#toast")).to_contain_text("Takes Bin is resting.")
    # CodeRabbit round 2: a partial Bin can't be emptied (the purge would take One take's unseen takes and clips too)
    pw.expect(page.get_by_test_id("library-empty-bin")).to_be_disabled()
    pw.expect(page.get_by_test_id("library-bin-note")).to_contain_text("didn't load")
    page.unroute("**/api/takes/bin")
    page.locator("#libViewItems").click()
    page.locator("#libViewBin").click()
    pw.expect(page.get_by_test_id("library-empty-bin")).to_be_enabled()
    pw.expect(page.get_by_test_id("library-bin-note")).to_contain_text("7 days")
    assert not errors, errors
