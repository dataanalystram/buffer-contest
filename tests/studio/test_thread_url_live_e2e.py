"""Threads in a real browser at 1280 and 390 px (owner asks, 5 Nov 2026).

1. A thread has its own address (/threads/thr_...): a reload stays on it (no fall back to the start screen); New post goes
   back to /?tab=agent and Back reopens the thread.
2. The thread's sticky bar follows the sticky app header even when the header grows after load (it used a height measured
   once, so a taller header covered the bar's top), and a post card's sticky media sits below the bar, never under it.
3. An open thread refreshes live while its run works: unchanged polls are 304s with no body; a change shows up; a hidden
   tab sends nothing until it is shown; another screen stops the poll. Screenshots go to shots/threads/."""
import json
import pathlib
import time

import pytest

from .test_chat_ui_e2e import ARTICLE, _page, _settled, live  # noqa: F401  (live is a fixture)

pw = pytest.importorskip("playwright.sync_api")
SHOTS = pathlib.Path(__file__).resolve().parents[2] / "shots" / "threads"

HIDE = """(h) => { window.__hid = h; if (!window.__hidDef) { window.__hidDef = 1;
  Object.defineProperty(document, 'hidden', {configurable: true, get: () => !!window.__hid});
  Object.defineProperty(document, 'visibilityState', {configurable: true, get: () => window.__hid ? 'hidden' : 'visible'}); }
  document.dispatchEvent(new Event('visibilitychange')); }"""


def _shot(page, name):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.wait_for_timeout(300)
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def _in_thread(page, rid):
    page.wait_for_function("(rid) => window.H_STATE && H_STATE.run && H_STATE.run.id === rid && "
                           "document.getElementById('tab-agent').classList.contains('mp-in-thread')", arg=rid, timeout=30000)


@pytest.mark.parametrize("w,h", [(1280, 900), (390, 844)])
def test_thread_address_header_and_live_refresh(live, chromium, close_contexts, w, h):  # noqa: F811
    base, app = live
    ctx, page, errors = _page(chromium, base, w, h)
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    rid = page.evaluate("H_STATE.run.id")
    tid = page.evaluate("H_STATE.run.thread_id")
    assert tid and tid.startswith("thr_")
    # 1. the address names the thread; a reload stays on it
    page.wait_for_function("(tid) => location.pathname === '/threads/' + tid", arg=tid)
    page.reload()
    _in_thread(page, rid)
    assert page.url == f"{base}/threads/{tid}"
    pw.expect(page.get_by_test_id("package").first).to_be_visible()
    _shot(page, f"01-reloaded-{w}")
    page.get_by_test_id("mp-new").click()
    page.wait_for_function("() => location.pathname === '/' && new URLSearchParams(location.search).get('tab') === 'agent'")
    assert not page.evaluate("document.getElementById('tab-agent').classList.contains('mp-in-thread')")
    page.go_back()
    _in_thread(page, rid)
    page.wait_for_function("(tid) => location.pathname === '/threads/' + tid", arg=tid)
    # someone else's (or a missing) thread id: Make a post's start screen, the address back to /?tab=agent
    page.goto(base + "/threads/thr_" + "a" * 26)
    page.wait_for_function("() => location.pathname === '/' && !document.getElementById('tab-agent').classList.contains('mp-in-thread')")

    # 2. the sticky bar under a header that grew after load; post media below the bar
    page.goto(base + f"/threads/{tid}")
    _in_thread(page, rid)
    page.evaluate("""() => { document.querySelector('header.top').style.paddingBottom = '56px';
        const s = document.createElement('div'); s.style.height = '2400px'; s.id = 'e2eSpacer';
        document.getElementById('hcMain').appendChild(s); }""")
    page.wait_for_timeout(250)
    page.evaluate("window.scrollTo(0, 900)")
    page.wait_for_timeout(250)
    geo = page.evaluate("""() => { const hd = document.querySelector('header.top').getBoundingClientRect();
        const bar = document.querySelector('#runView .mp-thread-bar').getBoundingClientRect();
        const m = document.querySelector('.pkg > .pkg-media'), cs = m && getComputedStyle(m);
        return {hb: hd.bottom, bt: bar.top, bb: bar.bottom, sticky: !!cs && cs.position === 'sticky', mtop: cs ? parseFloat(cs.top) : 0}; }""")
    assert geo["bt"] >= geo["hb"] - 1, f"the app header covers the thread bar at {w}px: {geo}"
    if geo["sticky"]:  # wide cards: their media column sticks below the bar
        assert geo["mtop"] >= geo["bb"] - 1, f"post media sticks under the thread bar at {w}px: {geo}"
    _shot(page, f"02-header-grown-{w}")

    # 3. live refresh while the run works, with no body for unchanged polls
    st = app.state.store
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    st.x("UPDATE agent_runs SET status='running', updated=? WHERE id=?", (time.time(), rid))
    seen = []
    page.on("response", lambda r: seen.append(r.status) if r.url.endswith(f"/api/agent/runs/{rid}") and r.request.method == "GET" else None)
    page.goto(base + f"/threads/{tid}")
    _in_thread(page, rid)
    deadline = time.time() + 20
    while 304 not in seen and time.time() < deadline:
        page.wait_for_timeout(300)
    assert 304 in seen, seen
    res.setdefault("thread", []).append({"role": "agent", "text": "Live line 42: drafting the carousel", "at": time.time()})
    st.x("UPDATE agent_runs SET result=?, updated=? WHERE id=?", (json.dumps(res), time.time(), rid))
    pw.expect(page.locator("#agentThread")).to_contain_text("Live line 42", timeout=20000)
    # a hidden tab asks nothing; shown again, it catches up at once
    page.evaluate(HIDE, True)
    page.wait_for_timeout(500)
    n = len(seen)
    page.wait_for_timeout(4000)
    assert len(seen) == n, f"polled while hidden: {seen[n:]}"
    page.evaluate(HIDE, False)
    page.wait_for_timeout(1500)
    assert len(seen) > n
    # another screen stops the poll
    page.evaluate("showTab('library')")
    page.wait_for_timeout(1500)
    n = len(seen)
    page.wait_for_timeout(4000)
    assert len(seen) == n, f"polled behind another screen: {seen[n:]}"
    st.x("UPDATE agent_runs SET status='ready' WHERE id=?", (rid,))
    assert not errors, errors
