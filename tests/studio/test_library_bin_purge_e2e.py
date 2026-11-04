"""Empty bin in a real browser (owner bug, live 3 Nov 2026; tests/studio/test_library_bin_purge.py is the unit side):
26 items in the Bin, Empty bin -> the Bin is empty and the storage meter drops at once, without leaving the Bin; a
reload shows an empty Bin and an empty Library (no purged item, no purged run's other renders); and a page that loads
the Bin while the purge is still running sees it before or after, never half of it."""
import json
import threading
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
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
A = house.Actor("acc_1")


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="p" * 64,
                      db_path=str(tmp_path / "bp.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(FakeBuffer()))))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-bp", "csrf-bp"); st.update_session("sid-bp", user_id="acc_1")
    h = app.state.house
    ids = [h.library.save_bytes(A, jpeg(600 + i * 7, 750), f"photo {i + 1}.jpg")["id"] for i in range(16)]
    for i in range(10):  # runs that rendered a picture (in the Library) and another channel's cover (never registered)
        tok = h.put_media(A, jpeg(1080, 1350), "card.jpg", kind="render", meta={"type": "photo"})
        cov = h.put_media(A, jpeg(320, 400), "cover.jpg", kind="render", meta={"type": "photo"})
        res = {"story": {"headline": f"Story {i}"}, "packages": [
            {"channel_id": "ch_li", "service": "linkedin", "text": "x", "status": "ready", "media": [{"url": h.media_url(tok), "type": "photo", "token": tok}]},
            {"channel_id": "ch_fb", "service": "facebook", "text": "x", "status": "ready", "media": [{"url": h.media_url(cov), "type": "photo", "token": cov}]}]}
        rid = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                   ("acc_1", "agent", json.dumps({"prompt": f"Story {i}"}), "ready", "[]", json.dumps(res), time.time() - i, time.time() - i))
        ids.append(h.library.register_generated(A, [{"token": tok, "type": "photo"}], "photo", rid))
    keep = h.library.save_bytes(A, jpeg(900, 900), "keep me.jpg")["id"]  # stays in the Library: the meter never hits 0
    h.library.bulk_delete(A, ids, "bin", True)
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "ids": ids, "keep": keep}
    server.should_exit = True


def _ctx(chromium, base):
    ctx = chromium.new_context(viewport={"width": 1280, "height": 900}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-bp", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


def _open_bin(page, base):
    for i in range(3):
        try:
            page.goto(base + "/?tab=library", wait_until="domcontentloaded")
            page.get_by_test_id("library-item").first.wait_for(timeout=15000)
            break
        except Exception:  # noqa: BLE001  a slow first paint on a busy box: retry
            if i == 2:
                raise
    page.get_by_test_id("library-view-bin").click()


def _used(page):
    page.wait_for_function("() => !!document.getElementById('libUsage').dataset.used")
    return int(page.get_by_test_id("library-usage").get_attribute("data-used"))


def test_empty_bin_reload_shows_nothing_and_the_meter_drops(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    ctx, page, errors = _ctx(chromium, base)
    _open_bin(page, base)
    page.get_by_test_id("bin-item").first.wait_for()
    page.wait_for_function("() => document.getElementById('libBinCount').textContent === '26'")
    used0 = _used(page)
    page.locator("#libEmptyBin").click()
    page.get_by_test_id("ask-ok").click()
    page.get_by_test_id("bin-empty").wait_for()
    page.wait_for_function(f"() => +document.getElementById('libUsage').dataset.used < {used0}")  # in the Bin, no reload
    used1 = _used(page)
    st = app.state.store
    assert st.q("SELECT COUNT(*) n FROM library_items WHERE user_id='acc_1'")[0]["n"] == 1
    assert used1 == st.q("SELECT SUM(size) n FROM library_items WHERE user_id='acc_1'")[0]["n"]
    for _ in range(2):  # reload twice: the Bin stays empty, the Library holds only the kept item, nothing comes back
        _open_bin(page, base)
        page.get_by_test_id("bin-empty").wait_for()
        assert page.get_by_test_id("bin-item").count() == 0 and _used(page) == used1
        page.get_by_test_id("library-view-items").click()
        page.wait_for_function("() => document.querySelectorAll('[data-testid=library-item]').length === 1")
    assert not errors, errors


def test_a_page_loading_the_bin_mid_purge_never_sees_half(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    st, real_x = app.state.store, app.state.store.x

    def slow_x(sql, args=()):  # a remote database: each statement takes a while
        time.sleep(0.05)
        return real_x(sql, args)
    st.x = slow_x
    ctx, page, errors = _ctx(chromium, base)
    seen = []
    t = threading.Thread(target=lambda: seen.append(httpx.post(
        base + "/api/library/bin/purge", json={"all": True}, cookies={"hq_sid": "sid-bp"},
        headers={"X-HQ-CSRF": "csrf-bp"}, timeout=60, trust_env=False).status_code))
    t.start()
    time.sleep(0.15)
    _open_bin(page, base)  # a refresh while it runs
    page.wait_for_selector("[data-testid=bin-empty], [data-testid=bin-item]")
    n = page.get_by_test_id("bin-item").count()
    t.join()
    st.x = real_x
    assert seen == [200]
    assert n in (0, 12), n  # 12 = the first page of a full Bin (26): before or after, never part of it
    _open_bin(page, base)
    page.get_by_test_id("bin-empty").wait_for()
    assert not errors, errors
