"""Library premium layout in a real browser at 1280 and 390 px (owner ask, 3 Nov 2026; test_library_filters.py is the
server side). Covers: the type tabs filter on the server (aria-selected, counts), search, a filter pill, grid | list (kept
across reloads), select + the selection bar (Use in post / Create draft need exactly one), a card's ⋯ menu (keyboard,
Escape, Open full size; a bottom sheet at 390), pagination (12 a page, Showing x–y of N, the next page asks the server
for offset=12), the Bin toggle with its count, and no horizontal overflow. HQ_SHOTS_DIR=<dir> saves screenshots."""
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


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="p" * 64,
                      db_path=str(tmp_path / "lp.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(FakeBuffer()))))
    st, h = app.state.store, app.state.house
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-lp", "csrf-lp"); st.update_session("sid-lp", user_id="acc_1")
    ups = [h.library.save_bytes(A, jpeg(600 + i * 7, 750, (40 + i * 9, 60, 120)), f"photo {i + 1}.jpg")["id"] for i in range(11)]
    vid = h.library.save_bytes(A, MP4, "launch clip.mp4")["id"]
    tok = h.put_media(A, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350})
    url = h.media_url(tok)
    res = {"story": {"headline": "Agent Desktop ships"}, "packages": [
        {"channel_id": "ch_li", "service": "linkedin", "text": "x", "media": [{"url": url, "type": "photo", "token": tok}], "status": "ready"}]}
    rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
               ("acc_1", "agent", json.dumps({"prompt": "Agent Desktop ships"}), "ready", "[]", json.dumps(res), time.time(), time.time()))
    gen = h.library.register_generated(A, [{"token": tok, "url": url, "type": "photo"}], "photo", rid)
    h.library.delete(A, ups[0])  # one in the Bin: 12 in the Library + the Bin's 1
    gone = h.library.save_bytes(A, jpeg(640, 640), "extra.jpg")["id"]  # 13 in the Library: two pages
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "vid": vid, "gen": gen, "rid": rid, "extra": gone}
    server.should_exit = True


def _page(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin", color_scheme="dark")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-lp", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors, lib_calls = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: lib_calls.append(r.url) if "/api/library?" in r.url else None)
    for i in range(3):
        try:
            page.goto(base + "/?tab=library", wait_until="domcontentloaded")
            page.get_by_test_id("library-item").first.wait_for(timeout=15000)
            break
        except Exception:  # noqa: BLE001  a slow first paint on a busy box: retry
            if i == 2:
                raise
    return page, errors, lib_calls


def _shot(page, name):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, name), full_page=False)


def _no_overflow(page):
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")


def _cards(page):
    return page.get_by_test_id("library-item")


def _tab(page, name):
    return page.locator(f"[data-testid=library-tab][data-type='{name}']")


@pytest.mark.parametrize("w", [1280, 390])
def test_tabs_search_filters_layout_and_pages(live, chromium, close_contexts, w):
    page, errors, calls = _page(chromium, live["base"], w)
    _no_overflow(page)
    # pagination: 12 a page, the next page comes from the server
    pw.expect(_cards(page)).to_have_count(12)
    assert page.get_by_test_id("library-showing").inner_text() == "Showing 1–12 of 13 items"
    _shot(page, f"lp-grid-{w}.png")
    page.get_by_test_id("library-pager").get_by_test_id("pager-next").click()
    pw.expect(_cards(page)).to_have_count(1)
    assert page.get_by_test_id("library-showing").inner_text() == "Showing 13–13 of 13 items"
    assert any("offset=12" in u and "per=12" in u for u in calls), calls
    page.get_by_test_id("library-pager").get_by_test_id("pager-prev").click()
    pw.expect(_cards(page)).to_have_count(12)
    # type tabs: counts, aria-selected, the server filters
    assert _tab(page, "video").locator(".lp-tab-n").inner_text() == "1"
    _tab(page, "video").click()
    pw.expect(_cards(page)).to_have_count(1)
    assert _tab(page, "video").get_attribute("aria-selected") == "true" and _tab(page, "").get_attribute("aria-selected") == "false"
    assert any("type=video" in u for u in calls)
    _tab(page, "audio").click()
    page.get_by_test_id("library-empty").wait_for()
    assert "No audio yet" in page.get_by_test_id("library-empty").inner_text()
    page.get_by_test_id("library-show-all").click()  # Clear filters
    pw.expect(_cards(page)).to_have_count(12)
    # search: matches the run's headline on a generated card
    page.get_by_test_id("library-search").fill("desktop")
    pw.expect(_cards(page)).to_have_count(1)
    assert page.locator(f"[data-testid=library-item][data-id='{live['gen']}'] .lp-t").inner_text() == "Agent Desktop ships"
    page.get_by_test_id("library-search").fill("")
    pw.expect(_cards(page)).to_have_count(12)
    # a filter pill: Created by -> Made by the agent
    page.get_by_test_id("library-filter-created-by").click()
    page.get_by_role("menuitemradio", name="Made by the agent").click()
    pw.expect(_cards(page)).to_have_count(1)
    assert "Made by the agent" in page.get_by_test_id("library-filter-created-by").inner_text()
    assert any("created_by=agent" in u for u in calls)
    _shot(page, f"lp-filtered-{w}.png")
    page.get_by_test_id("library-filter-created-by").click()
    page.get_by_role("menuitemradio", name="All").click()
    pw.expect(_cards(page)).to_have_count(12)
    # grid | list, kept across a reload
    page.get_by_test_id("library-layout-list").click()
    assert "list" in page.get_by_test_id("library-grid").get_attribute("class")
    assert page.get_by_test_id("library-layout-list").get_attribute("aria-pressed") == "true"
    _shot(page, f"lp-list-{w}.png")
    _no_overflow(page)
    page.reload(wait_until="domcontentloaded")
    _cards(page).first.wait_for()
    assert "list" in page.get_by_test_id("library-grid").get_attribute("class")
    page.get_by_test_id("library-layout-grid").click()
    assert "list" not in page.get_by_test_id("library-grid").get_attribute("class")
    assert not errors, errors


@pytest.mark.parametrize("w", [1280, 390])
def test_selection_bar_menu_and_bin(live, chromium, close_contexts, w):
    page, errors, _ = _page(chromium, live["base"], w)
    bar = page.get_by_test_id("library-bulk-bar")
    assert page.get_by_test_id("library-bulk-count").inner_text() == "0 selected"
    if w == 1280:
        assert page.get_by_test_id("library-bulk-use").is_disabled() and page.get_by_test_id("library-bulk-bin").is_disabled()
    boxes = page.get_by_test_id("library-select")
    boxes.nth(0).click()
    assert page.get_by_test_id("library-bulk-count").inner_text() == "1 selected"
    assert page.get_by_test_id("library-bulk-use").is_enabled() and page.get_by_test_id("library-bulk-draft").is_enabled()
    boxes.nth(3).click(modifiers=["Shift"])  # a range: 1-4
    assert page.get_by_test_id("library-bulk-count").inner_text() == "4 selected"
    assert page.get_by_test_id("library-bulk-use").is_disabled()  # Use in post / Create draft take exactly one
    if w == 390:  # the selection bar sticks to the bottom of the screen
        box = bar.bounding_box()
        assert page.evaluate("() => getComputedStyle(document.getElementById('libBulk')).position") == "sticky"
        assert box["y"] + box["height"] <= 900 and box["y"] > 450, box
        _no_overflow(page)
    _shot(page, f"lp-selected-{w}.png")
    page.get_by_test_id("library-bulk-cancel").click()
    assert page.get_by_test_id("library-bulk-count").inner_text() == "0 selected"
    # a card's ⋯ menu: opens, keyboard moves through it, Escape closes it and gives focus back
    card = page.locator(f"[data-testid=library-item][data-id='{live['gen']}']")
    more = card.get_by_test_id("library-more")
    more.click()
    menu = card.get_by_role("menu")
    pw.expect(menu).to_be_visible()
    assert more.get_attribute("aria-expanded") == "true"
    assert card.get_by_test_id("library-view-run").get_attribute("href") == f"/?tab=library&run={live['rid']}"
    page.keyboard.press("ArrowDown")
    assert page.evaluate("() => document.activeElement.dataset.testid") == "library-create-draft"
    if w == 390:  # a bottom sheet over a scrim
        mb = menu.bounding_box()
        assert abs(mb["y"] + mb["height"] - 900) < 2 and mb["width"] >= 389, mb
        assert page.locator("#libScrim").is_visible()
    _shot(page, f"lp-menu-{w}.png")
    page.keyboard.press("Escape")
    pw.expect(menu).to_be_hidden()
    assert page.evaluate("() => document.activeElement.dataset.testid") == "library-more"
    more.click()
    card.get_by_test_id("library-open-full").click()
    page.locator("#lightbox[open]").wait_for()
    page.locator("#lbClose").click()
    # the Bin toggle with its count
    assert page.get_by_test_id("library-bin-count").inner_text() == "1"
    page.get_by_test_id("library-view-bin").click()
    pw.expect(page.get_by_test_id("bin-item")).to_have_count(1)
    assert page.get_by_test_id("library-bar").is_hidden() and page.get_by_test_id("library-bin-note").is_visible()
    assert page.get_by_test_id("library-bulk-restore").is_visible() and page.get_by_test_id("library-bulk-use").is_hidden()
    _shot(page, f"lp-bin-{w}.png")
    _no_overflow(page)
    page.get_by_test_id("library-view-items").click()
    pw.expect(_cards(page)).to_have_count(12)
    assert not errors, errors


def test_hover_preview_plays_only_on_hover(live, chromium, close_contexts):
    page, errors, _ = _page(chromium, live["base"], 1280)
    card = page.locator(f"[data-testid=library-item][data-id='{live['vid']}']")
    assert card.locator("video.lp-prev").count() == 0  # nothing fetched before a hover
    card.locator(".lp-th").hover()
    page.wait_for_timeout(500)
    assert card.locator("video.lp-prev").count() <= 1  # the test clip isn't a real video: play() may refuse and drop it
    if card.locator("video.lp-prev").count():
        assert card.locator("video.lp-prev").evaluate("v => v.muted")
    page.mouse.move(5, 5)
    page.wait_for_timeout(200)
    assert card.locator("video.lp-prev").count() == 0
    assert not errors, errors
