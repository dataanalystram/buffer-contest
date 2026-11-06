"""Recurring template slots in a browser (a signed-in workspace on a fake Buffer, hermetic; never Ram's real account):
add a weekly slot in Brand > Templates, see its next time in the account's time zone, Run now puts one Buffer DRAFT per
channel at that time (the post assigned to the slot), the ticker drafts the next occurrence a week later on the same
weekday and wall-clock time, Edit moves it to another day and time and the next draft follows, Remove deletes it.
1280 and 390 px, no page errors, nothing wider than the screen. HQ_SHOTS=<dir> saves screenshots. Skips without Chromium."""
import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from studio import brand_studio as BS
from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1'); localStorage.setItem('hq_theme','light')"
SHOTS = os.environ.get("HQ_SHOTS", "")
DUB = ZoneInfo("Europe/Dublin")
UTC = timezone.utc


@pytest.fixture
def live(tmp_path):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="s" * 64,
                      db_path=str(tmp_path / "s.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    app.state.studio.media_reach = lambda urls: []  # the 127.0.0.1 slide links would fail Buffer's reach check
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-s", "csrf-s")
    st.update_session("sid-s", user_id="acc_1")
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", app, fake
    server.should_exit = True


def _until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def _shot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}-{width}.png"), full_page=True)


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1


def _slots(app):
    return [dict(r) for r in app.state.store.q("SELECT * FROM template_slots WHERE user_id='acc_1' ORDER BY id")]


def _local(ts):
    return datetime.fromtimestamp(ts, UTC).astimezone(DUB)


@pytest.mark.parametrize("width", [1280, 390])
def test_slot_create_repeat_assign_edit_delete(live, chromium, width):
    base, app, fake = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, has_touch=width < 1024,
                               color_scheme="light", timezone_id="Europe/Dublin", locale="en-GB")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-s", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base + "/?tab=templates")  # Brand > Templates: render a template, recurring slots
        page.wait_for_selector("[data-testid=bs-slot-add]", state="visible", timeout=60000)
        _until(lambda: page.locator("#bsSlotTpl option[value=weekly_recap]").count() == 1)
        add = page.locator("[data-testid=bs-slot-add]")
        add.scroll_into_view_if_needed()

        # 1. create: the weekly recap every Monday 20:00, in the browser's (the account's) time zone
        page.select_option("#bsSlotTpl", "weekly_recap")
        page.select_option("#bsSlotDay", "mon")
        page.fill("#bsSlotTime", "20:00")
        add.click()
        page.wait_for_selector("[data-testid=bs-slot]", timeout=20000)
        s = _until(lambda: _slots(app))[0]
        assert (s["template"], s["weekday"], s["at"], s["tz"], s["active"]) == ("weekly_recap", "mon", "20:00", "Europe/Dublin", 1)
        first = _local(s["next_fire"])
        assert first.strftime("%a %H:%M") == "Mon 20:00" and first > datetime.now(DUB)
        row = page.locator("[data-testid=bs-slot]").first
        assert "Built-in" in row.inner_text() and "every mon 20:00" in row.inner_text()
        assert "Mon" in row.locator("[data-testid=bs-slot-next]").inner_text()
        _shot(page, "slot-added", width)

        # 2. assign a post: Run now puts one Buffer draft per channel at the slot's next time
        row.locator("[data-testid=bs-slot-run]").click()
        _until(lambda: "Buffer draft" in page.inner_text("#toast"))
        made = list(fake.created)
        assert made and {i["channelId"] for i in made} == set(json.loads(_slots(app)[0]["channels"]))
        assert all(i["saveToDraft"] and i["dueAt"][:16] == first.astimezone(UTC).strftime("%Y-%m-%dT%H:%M") for i in made), \
            [i["dueAt"] for i in made]

        # 3. repeats: the ticker drafts the next occurrence, one week on, same weekday and wall-clock time in Dublin
        s = _slots(app)[0]
        second = _local(s["next_fire"])
        assert second.strftime("%a %H:%M") == "Mon 20:00" and (second - first).days in (6, 7)
        assert BS.tick_once(app.state.house.brand_studio, now=second.astimezone(UTC) - timedelta(minutes=55)) == 1
        new = fake.created[len(made):]
        assert len(new) == len(made) and all(i["dueAt"][:16] == second.astimezone(UTC).strftime("%Y-%m-%dT%H:%M") for i in new)
        third = _local(_slots(app)[0]["next_fire"])
        assert third.strftime("%a %H:%M") == "Mon 20:00" and (third - second).days in (6, 7)

        # 4. edit: Wednesday 08:30; the list and the next draft follow
        page.reload()
        page.wait_for_selector("[data-testid=bs-slot]", state="visible", timeout=30000)
        page.locator("[data-testid=bs-slot-edit]").first.click()
        _until(lambda: page.inner_text("[data-testid=bs-slot-add]") == "Save changes")
        assert page.input_value("#bsSlotDay") == "mon" and page.input_value("#bsSlotTime") == "20:00"
        page.select_option("#bsSlotDay", "wed")
        page.fill("#bsSlotTime", "08:30")
        page.click("[data-testid=bs-slot-add]")
        _until(lambda: _slots(app)[0]["weekday"] == "wed")
        s = _slots(app)[0]
        assert (s["weekday"], s["at"], s["tz"]) == ("wed", "08:30", "Europe/Dublin")
        moved = _local(s["next_fire"])
        assert moved.strftime("%a %H:%M") == "Wed 08:30"
        _until(lambda: "every wed 08:30" in page.inner_text("[data-testid=bs-slot]"))
        assert "Wed" in page.inner_text("[data-testid=bs-slot-next]")
        assert page.inner_text("[data-testid=bs-slot-add]") == "Add slot" and page.locator("[data-testid=bs-slot-cancel]").is_hidden()
        n = len(fake.created)
        assert BS.tick_once(app.state.house.brand_studio, now=moved.astimezone(UTC) - timedelta(minutes=55)) == 1
        assert all(i["dueAt"][:16] == moved.astimezone(UTC).strftime("%Y-%m-%dT%H:%M") for i in fake.created[n:])
        _shot(page, "slot-edited", width)

        # 5. delete
        page.locator("[data-testid=bs-slot-del]").first.click()
        _until(lambda: "No slots yet" in page.inner_text("[data-testid=bs-slots]"))
        assert _slots(app) == []
        _no_overflow(page)
        assert not errors, errors
    finally:
        ctx.close()
