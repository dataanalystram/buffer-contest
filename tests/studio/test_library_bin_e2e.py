"""Library Bin, bulk select, delete choice, Create draft and View run in a real browser at 1280 and 390 px (owner ask,
3 Nov 2026; tests/studio/test_library_bin.py is the unit side). Buffer is FakeBuffer; drafts land there, never published.
Covers: checkbox select + shift-click range + select all on the page, the sticky bulk bar (Move to Bin), the Bin view with
days left and Restore, Delete permanently behind its confirm, Create draft prefilled with the run's per-channel captions,
and View run opening that run (hidden for an upload with no run). No horizontal overflow at 390 px.
HQ_SHOTS_DIR=<dir> saves screenshots."""
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

pw = pytest.importorskip("playwright.sync_api")
SHOTS = os.environ.get("HQ_SHOTS_DIR", "")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
A = house.Actor("acc_1")
CAPTIONS = {"ch_li": "LinkedIn caption from the run: what shipped and why it matters.", "ch_fb": "Facebook caption from the run."}


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="l" * 64,
                      db_path=str(tmp_path / "lb.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    app.state.studio.media_reach = lambda urls: []  # the draft save never probes the media over the network
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-lb", "csrf-lb"); st.update_session("sid-lb", user_id="acc_1")
    h = app.state.house
    ups = [h.library.save_bytes(A, jpeg(600 + i * 7, 750), f"photo {i + 1}.jpg")["id"] for i in range(5)]
    tok = h.put_media(A, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350})
    url = h.media_url(tok)
    res = {"story": {"headline": "Agent Desktop ships"}, "packages": [
        {"channel_id": c, "service": svc, "text": CAPTIONS[c], "media_urls": [url], "media": [{"url": url, "type": "photo", "token": tok}],
         "status": "ready"} for c, svc in (("ch_li", "linkedin"), ("ch_fb", "facebook"))]}
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               ("acc_1", "agent", json.dumps({"prompt": "Agent Desktop ships"}), "ready", "[]", json.dumps(res), time.time(), time.time()))
    gen = h.library.register_generated(A, [{"token": tok, "url": url, "type": "photo"}], "photo", rid)
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "fake": fake, "ups": ups, "gen": gen, "rid": rid}
    server.should_exit = True


def _ctx(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-lb", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


def _library(page, base):
    for i in range(3):
        try:
            page.goto(base + "/?tab=library", wait_until="domcontentloaded")
            page.get_by_test_id("library-item").first.wait_for(timeout=15000)
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


@pytest.mark.parametrize("w", [1280, 390])
def test_bulk_select_bin_restore_and_permanent_delete(live, chromium, close_contexts, w):
    base, app = live["base"], live["app"]
    ctx, page, errors = _ctx(chromium, base, w)
    _library(page, base)
    boxes = page.get_by_test_id("library-select")
    assert boxes.count() == 6
    boxes.nth(0).click()
    boxes.nth(3).click(modifiers=["Shift"])  # a range: items 1-4
    assert page.get_by_test_id("library-bulk-count").inner_text() == "4 selected"
    _shot(page, f"bulk-select-{w}.png")
    if w == 390:
        _no_overflow(page)
    page.get_by_test_id("library-bulk-cancel").click()
    assert page.get_by_test_id("library-bulk-bar").is_hidden()
    page.get_by_test_id("library-select-all").check()
    assert page.get_by_test_id("library-bulk-count").inner_text() == "6 selected"
    page.get_by_test_id("library-bulk-cancel").click()
    boxes.nth(4).click(); boxes.nth(5).click()
    page.get_by_test_id("library-bulk-bin").click()
    page.get_by_test_id("library-delete-dialog").wait_for()
    assert page.get_by_test_id("library-delete-bin").is_checked()  # Move to Bin is the default
    _shot(page, f"delete-choice-{w}.png")
    page.get_by_test_id("library-delete-go").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 4")
    page.wait_for_function("() => document.getElementById('libBinCount').textContent === '2'")
    # the Bin: days left, Restore one, Delete the other permanently behind its confirm
    page.get_by_test_id("library-view-bin").click()
    page.get_by_test_id("bin-item").first.wait_for()
    assert page.get_by_test_id("bin-item").count() == 2
    assert "7 days left" in page.get_by_test_id("bin-days-left").first.inner_text()
    _shot(page, f"bin-{w}.png")
    if w == 390:
        _no_overflow(page)
    page.get_by_test_id("bin-restore").first.click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=bin-item]').length === 1")
    page.get_by_test_id("bin-delete-permanently").first.click()
    assert "can't be undone" in page.get_by_test_id("ask-message").inner_text()
    page.get_by_test_id("ask-ok").click()
    page.get_by_test_id("bin-empty").wait_for()
    st = app.state.store
    assert st.q("SELECT COUNT(*) n FROM library_items WHERE user_id='acc_1' AND storage='trash'")[0]["n"] == 0
    assert st.q("SELECT COUNT(*) n FROM library_items WHERE user_id='acc_1' AND storage<>'trash'")[0]["n"] == 5
    assert not errors, errors


@pytest.mark.parametrize("w", [1280, 390])
def test_single_delete_permanently_needs_its_confirm(live, chromium, close_contexts, w):
    base, app = live["base"], live["app"]
    ctx, page, errors = _ctx(chromium, base, w)
    _library(page, base)
    card = page.locator(f"[data-testid=library-item][data-id='{live['ups'][0]}']")
    card.get_by_test_id("library-delete").click()
    page.get_by_test_id("library-delete-permanent").check()
    assert page.get_by_test_id("library-delete-go").inner_text() == "Delete permanently"
    assert "can't be undone" in page.get_by_test_id("library-delete-dialog").inner_text()
    _shot(page, f"delete-permanent-{w}.png")
    page.get_by_test_id("library-delete-cancel").click()
    assert app.state.store.q("SELECT COUNT(*) n FROM library_items WHERE id=?", (live["ups"][0],))[0]["n"] == 1  # Cancel: nothing happened
    card.get_by_test_id("library-delete").click()
    page.get_by_test_id("library-delete-permanent").check()
    page.get_by_test_id("library-delete-go").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 5")
    assert app.state.store.q("SELECT COUNT(*) n FROM library_items WHERE id=?", (live["ups"][0],))[0]["n"] == 0
    assert page.get_by_test_id("library-bin-count").is_hidden()  # it never went to the Bin
    assert not errors, errors


@pytest.mark.parametrize("w", [1280, 390])
def test_create_draft_prefill_and_view_run(live, chromium, close_contexts, w):
    base, fake = live["base"], live["fake"]
    ctx, page, errors = _ctx(chromium, base, w)
    _library(page, base)
    up = page.locator(f"[data-testid=library-item][data-id='{live['ups'][0]}']")
    assert up.get_by_test_id("library-view-run").count() == 0  # an upload no run made: no View run
    gen = page.locator(f"[data-testid=library-item][data-id='{live['gen']}']")
    assert gen.get_by_test_id("library-view-run").get_attribute("href") == f"/?tab=library&run={live['rid']}"
    gen.get_by_test_id("library-create-draft").click()
    page.get_by_test_id("library-draft-channel").first.wait_for()
    li = page.locator("[data-testid=library-draft-channel][data-channel=ch_li]")
    fb = page.locator("[data-testid=library-draft-channel][data-channel=ch_fb]")
    assert li.get_by_test_id("library-draft-text").input_value() == CAPTIONS["ch_li"]
    assert fb.get_by_test_id("library-draft-text").input_value() == CAPTIONS["ch_fb"]
    assert li.get_by_test_id("library-draft-pick").is_checked() and fb.get_by_test_id("library-draft-pick").is_checked()
    assert not page.locator("[data-testid=library-draft-channel][data-channel=ch_ig] [data-testid=library-draft-pick]").is_checked()
    _shot(page, f"create-draft-{w}.png")
    if w == 390:
        _no_overflow(page)
    fb.get_by_test_id("library-draft-pick").uncheck()
    n0 = len(fake.created)
    page.get_by_test_id("library-draft-save").click()
    page.get_by_test_id("library-draft-dialog").wait_for(state="hidden")
    assert len(fake.created) == n0 + 1  # one Buffer draft, on the one channel left ticked
    assert fake.created[-1]["channelId"] == "ch_li" and fake.created[-1]["text"] == CAPTIONS["ch_li"]
    assert fake.created[-1].get("saveToDraft") is True
    # View run: the run opens
    gen.get_by_test_id("library-view-run").click()
    page.wait_for_function("() => document.getElementById('runView') && !document.getElementById('runView').hidden")
    _shot(page, f"view-run-{w}.png")
    # a /?tab=library&run=<id> link (open in a new tab) opens the same run
    page.goto(base + f"/?tab=library&run={live['rid']}", wait_until="domcontentloaded")
    page.wait_for_function("() => document.getElementById('runView') && !document.getElementById('runView').hidden", timeout=20000)
    assert not errors, errors


SLOW_BOOT = """(() => { const f = window.fetch.bind(window);  // a slow boot: /api/channels answers 2.5 s late
  window.fetch = (u, o) => String(u).includes("/api/channels") ? new Promise((r) => setTimeout(r, 2500)).then(() => f(u, o)) : f(u, o); })()"""


def _hits(page, base):
    hits = []
    page.on("request", lambda r: hits.append(r.url.split("?")[0].replace(base, "")) if "/api/" in r.url else None)
    return hits


def test_slow_boot_bin_view_is_kept_and_redrawn_without_a_second_fetch(live, chromium, close_contexts):
    """Library opened, then its Bin view, while boot still waits on /api/channels (main's boot-end redraw, PR #88): the Bin
    view bumps HQ_NAV so boot keeps it, and the boot-end redraw draws it again from the answer it has (HS_REDRAW.library):
    no second /api/library or /api/library/bin."""
    base, app = live["base"], live["app"]
    app.state.house.library.delete(A, live["ups"][4])  # one item in the Bin
    ctx, page, errors = _ctx(chromium, base, 1280)
    ctx.add_init_script(SLOW_BOOT)
    hits = _hits(page, base)
    page.goto(base + "/", wait_until="domcontentloaded")
    page.wait_for_function("() => typeof window.showTab === 'function' && typeof api === 'function'")
    assert page.evaluate("() => !document.body.dataset.booted"), "boot finished before the delayed /api/channels"
    nav0 = page.evaluate("() => window.HQ_NAV")
    page.evaluate("() => showTab('library')")
    page.get_by_test_id("library-item").first.wait_for()
    page.locator("#libViewBin").click()
    page.get_by_test_id("bin-item").first.wait_for()
    assert page.evaluate("() => window.HQ_NAV") >= nav0 + 2  # showTab + the Bin view
    assert page.evaluate("() => !document.body.dataset.booted")
    page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=15000)
    page.wait_for_timeout(500)
    assert page.locator("#tab-library").is_visible() and page.locator("#tab-inbox").is_hidden()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(1)
    assert page.locator("#libViewBin").get_attribute("aria-pressed") == "true"
    assert page.evaluate("() => window.HQ_RELOADS") == 1
    assert hits.count("/api/library") == 1, hits
    assert hits.count("/api/library/bin") == 1, hits
    # back to the Library view after boot: drawn from a fresh fetch as usual
    page.locator("#libViewItems").click()
    pw.expect(page.get_by_test_id("library-item")).to_have_count(5)
    assert not errors, errors


def test_slow_boot_view_run_deep_link_opens_the_run_once(live, chromium, close_contexts):
    """/?tab=library&run=<id> on a slow boot: boot opens the Library, which opens the run (no /api/library fetch at all),
    the run's open bumps HQ_NAV, and the boot end neither throws it back to the Inbox nor redraws it."""
    base, rid = live["base"], live["rid"]
    ctx, page, errors = _ctx(chromium, base, 1280)
    ctx.add_init_script(SLOW_BOOT)
    hits = _hits(page, base)
    page.goto(base + f"/?tab=library&run={rid}", wait_until="domcontentloaded")
    page.wait_for_function("() => document.body.dataset.booted === '1'", timeout=20000)
    page.wait_for_function("() => document.getElementById('runView') && !document.getElementById('runView').hidden", timeout=20000)
    page.wait_for_timeout(500)
    assert page.locator("#tab-agent").is_visible() and page.locator("#tab-inbox").is_hidden()
    assert page.get_by_test_id("package").first.is_visible()
    assert hits.count("/api/library") == 0, hits
    assert hits.count(f"/api/agent/runs/{rid}") == 1, hits
    assert not page.evaluate("() => window.HQ_RELOADS")
    assert not errors, errors
