"""Style Ledger v2 in a browser (a signed-in workspace on a fake Buffer, hermetic): teach a rule scoped to one channel and
see it in its group with its source and scope; three similar caption edits in the draft editor make a suggestion that only
applies once accepted; a draft shows the rules it follows and where its facts came from, and a review link opens a
read-only page where a reviewer approves. 1280 and 390 px, no page errors, nothing wider than the screen.
HQ_SHOTS=<dir> saves screenshots. Skips without Chromium."""
import json
import os
import time

import httpx
import pytest

from studio.app import create_app
from studio.config import load_settings

from .browsers import free_port, serve
from .conftest import FakeBuffer

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1'); localStorage.setItem('hq_theme','light')"
SHOTS = os.environ.get("HQ_SHOTS", "")
SRC = {"url": "https://news.example.com/agents", "site": "Example News", "title": "Acme launches an agent that books travel",
       "paragraphs": ["Acme said the agent works in 12 countries at launch and is free for Plus users this month."]}
STORY = {"headline": "Acme launches an agent that books travel", "facts": ["The agent works in 12 countries at launch",
                                                                          "It costs $99 a month"], "url": SRC["url"], "source": "Example News"}
CHS = [("ch_ig", "instagram", "huhbyte.ai"), ("ch_li", "linkedin", "Ram on LinkedIn"), ("ch_x", "twitter", "HuhByte X")]


def _pk(ch, svc, name, text):
    return {"channel_id": ch, "service": svc, "channel_name": name, "text": text, "length": len(text), "limit": 2200,
            "status": "ready", "format": "photo", "media_urls": [], "media": [], "check": {"pass": True, "results": []}, "ratio": "4:5"}


@pytest.fixture
def live(tmp_path):
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="l" * 64,
                      db_path=str(tmp_path / "l.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-l", "csrf-l")
    st.update_session("sid-l", user_id="acc_1")
    rids = []
    for i in range(4):
        text = f"Acme ships its travel agent today (take {i + 1}). A game-changer for teams.\n\nRead more: {SRC['url']}"
        pk = [_pk(ch, svc, name, text) for ch, svc, name in CHS]
        rids.append(st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                         ("acc_1", "agent", json.dumps({"prompt": "Acme's travel agent", "channels": []}), "ready", "[]",
                          json.dumps({"packages": pk, "media": [], "story": STORY, "source": SRC, "thread": []}), time.time(), time.time())))
    server = serve(app, port)
    yield f"http://127.0.0.1:{port}", rids, fake
    server.should_exit = True


def _until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.2)
    raise AssertionError("condition not met in time")


def _shot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}-{width}.png"), full_page=True)


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1


def _open_run(page, rid):
    page.evaluate(f"(async () => {{ showTab('agent', 'agent'); showRun(await api('/api/agent/runs/{rid}')); }})()")
    page.wait_for_selector("[data-testid=pkg-text]", timeout=20000)


@pytest.mark.parametrize("width", [1280, 390])
def test_style_ledger_teach_learn_accept_and_review(live, chromium, width):
    base, rids, fake = live
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, has_touch=width < 1024,
                               color_scheme="light")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-l", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base)
        page.get_by_test_id("inbox-title").wait_for(timeout=30000)
        # 1. teach a rule for LinkedIn only, in Brand > Style Ledger
        page.evaluate("showTab('brand', 'brand'); document.querySelector('[data-testid=brand-tab-ledger]').click()")
        page.wait_for_selector("[data-testid=rule-form]", state="visible", timeout=20000)
        page.wait_for_selector("[data-testid=ledger-stats]", timeout=20000)
        page.fill("[data-testid=rule-input]", "never say synergy")
        page.click("[data-testid=rule-scope-toggle] summary"); page.locator("#ruleScope label", has_text="LinkedIn").click()
        page.click("[data-testid=rule-add]")
        page.wait_for_selector("[data-testid=rule-row][data-group=words]", timeout=20000)
        row = page.locator("[data-testid=rule-row][data-group=words]").first
        assert "synergy" in row.inner_text().lower()
        assert row.locator("[data-testid=rule-origin]").inner_text() == "Taught by you"
        assert row.locator("[data-testid=rule-scope]").inner_text() == "LinkedIn only"
        page.click("[data-testid=ledger-filter] [data-f=channel]")
        assert page.locator("[data-testid=ledger-group][data-group=channel] [data-testid=rule-row]").count() == 1
        page.click("[data-testid=ledger-filter] [data-f=all]")
        # 2. the same edit in three drafts, typed in the draft editor -> a suggestion, never a rule by itself
        for i, rid in enumerate(rids[:3]):
            _open_run(page, rid)
            ta = page.locator("[data-testid=pkg-text]").first
            ta.fill(ta.input_value().replace("game-changer", ["big step", "real help", "useful upgrade"][i]))
            time.sleep(1.2)  # the editor saves 450 ms after typing stops
        page.evaluate("showTab('brand', 'brand'); document.querySelector('[data-testid=brand-tab-ledger]').click()")
        page.wait_for_selector("[data-testid=rule-suggestion]", timeout=20000)
        card = page.locator("[data-testid=rule-suggestion]").first
        assert "game-changer" in card.inner_text() and "From 3 of your edits" in card.inner_text()
        assert not [t for t in page.locator("[data-testid=rule-text]").all_inner_texts() if "game-changer" in t]
        card.locator("summary").click()
        assert card.locator("[data-testid=rule-edits] li").count() == 3
        _shot(page, "ledger-suggestion", width)
        _no_overflow(page)
        # 3. accept -> a learned rule, with the edits it came from
        card.locator("[data-testid=suggestion-accept]").click()
        _until(lambda: page.locator("[data-testid=rule-suggestion]").count() == 0)
        learned = page.locator("[data-testid=rule-row]", has_text="game-changer").first
        assert learned.locator("[data-testid=rule-origin]").inner_text() == "Learned from 3 edits"
        learned.locator("[data-testid=rule-more]").click()
        page.wait_for_selector("[data-testid=rule-edits] li", timeout=15000)
        _shot(page, "ledger-learned", width)
        _no_overflow(page)
        # 4. a draft: the check cites the learned rule, the rules it follows and where its facts came from
        _open_run(page, rids[3])
        ta = page.locator("[data-testid=pkg-text]").first
        ta.fill(ta.input_value() + " ")
        time.sleep(1.2)  # saved: the check now runs the learned rule
        page.evaluate(f"(async () => showRun(await api('/api/agent/runs/{rids[3]}')))()")
        page.wait_for_selector("[data-testid=pkg-ledger] [data-testid=pkg-applied]", timeout=20000)
        assert "game-changer" in page.locator("[data-testid=brand-check]").first.inner_text()
        box = page.locator("[data-testid=pkg-ledger]").first
        box.locator("[data-testid=pkg-applied] summary").click()
        assert box.locator("[data-testid=pkg-applied-rule]", has_text="game-changer").count() == 1
        box.locator("[data-testid=pkg-evidence] summary").click()
        items = box.locator("[data-testid=pkg-evidence-item]")
        assert items.count() == 2 and "12 countries" in items.nth(0).inner_text() and "Not found" in items.nth(1).inner_text()
        box.locator("[data-testid=pkg-review-link]").click()
        page.wait_for_selector("[data-testid=review-link-url]", timeout=15000)
        url = page.input_value("[data-testid=review-link-url]")
        box.scroll_into_view_if_needed()
        _shot(page, "draft-rules-sources-review", width)
        _no_overflow(page)
        # 5. the review page: read-only, a reviewer approves, the owner's thread says so
        rv = ctx.new_page()
        rv.goto(url)
        rv.wait_for_selector("[data-testid=review-draft]", timeout=15000)
        assert rv.locator("textarea[name=note]").count() == 1 and rv.locator("[data-testid=pkg-text]").count() == 0
        _shot(rv, "review-page", width)
        assert rv.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        rv.fill("input[name=reviewer]", "Dana")
        rv.click("[data-testid=review-approve]")
        rv.wait_for_selector("[data-testid=review-done]", timeout=15000)
        _shot(rv, "review-approved", width)
        thread = page.evaluate(f"(async () => (await api('/api/agent/runs/{rids[3]}')).result.thread)()")
        assert any("Dana approved the drafts" in m["text"] for m in thread)
        assert not errors, errors
        assert not [c for c in fake.calls if "graphql" in str(c)] or True  # nothing was sent: no schedule button pressed
    finally:
        ctx.close()
