"""Style Ledger v2 in a browser (the sample workspace at /demo, hermetic): teach a rule scoped to one channel and see it
in its group with its source and scope; three similar caption edits make a suggestion that only applies once accepted;
a draft shows the rules it followed and where its facts came from, and a review link opens a read-only page where a
reviewer approves. 1280 and 390 px, no console errors, nothing wider than the screen. HQ_SHOTS=<dir> saves screenshots.
Skips without Chromium."""
import os
import re
import time

import pytest

pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')"
SHOTS = os.environ.get("HQ_SHOTS", "")

GO = """(where) => { const b = document.createElement('button'); b.dataset.go = where; b.hidden = true; document.body.append(b); b.click(); b.remove(); }"""
RUNS = """async () => {
  const out = [];
  for (let n = 0; n < 4 && out.length < 3; n++) {
    const r = await api('/api/agent/runs', { method: 'POST', body: { query: 'travel agents', format: 'carousel' } });
    let run = r;
    for (let i = 0; i < 240 && !['ready', 'failed'].includes(run.status); i++) { await new Promise(s => setTimeout(s, 500)); run = await api('/api/agent/runs/' + r.id); }
    for (const p of (run.result.packages || [])) out.push({ rid: run.id, ch: p.channel_id, text: p.text });
  }
  return out.slice(0, 3);
}"""
# the same kind of edit three times: one content word the agent used in all three drafts, taken out
EDIT = """async (pk) => {
  const words = (t) => new Set((t.toLowerCase().match(/[a-z][a-z-]{5,}/g) || []));
  const common = [...words(pk[0].text)].filter(w => pk.every(p => words(p.text).has(w)) && !/^(https?|follow|channel)/.test(w));
  const w = common.sort((a, b) => b.length - a.length)[0];
  let last = null;
  for (const p of pk) {
    const t = p.text.replace(new RegExp('\\\\s*\\\\b' + w + '\\\\b', 'gi'), '');
    last = await api(`/api/agent/runs/${p.rid}/packages/${p.ch}`, { method: 'POST', body: { text: t } });
  }
  return { word: w, suggestions: last.suggestions || [] };
}"""


def _until(fn, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.25)
    raise AssertionError("condition not met in time")


def _shot(page, name, width):
    if SHOTS:
        os.makedirs(SHOTS, exist_ok=True)
        page.screenshot(path=os.path.join(SHOTS, f"{name}-{width}.png"), full_page=True)


def _no_overflow(page):
    assert page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1


@pytest.mark.parametrize("width", [1280, 390])
def test_style_ledger_teach_learn_accept_and_review(demo_live, chromium, width):
    base, buffer_calls = demo_live
    ctx = chromium.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, has_touch=width < 1024)
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        page.goto(base + "/demo")
        page.wait_for_selector("[data-testid=brand-tab-ledger]", state="attached", timeout=60000)
        page.evaluate(GO, "brand:ledger")
        page.wait_for_selector("[data-testid=rule-form]", state="visible", timeout=30000)
        # 1. teach a rule for LinkedIn only
        page.fill("[data-testid=rule-input]", "never say synergy")
        page.locator("#ruleScope label", has_text="LinkedIn").click()
        page.click("[data-testid=rule-add]")
        page.wait_for_selector("[data-testid=rule-row][data-group=words]", timeout=30000)
        row = page.locator("[data-testid=rule-row][data-group=words]").first
        assert "synergy" in row.inner_text().lower()
        assert row.locator("[data-testid=rule-origin]").inner_text() == "Taught by you"
        assert row.locator("[data-testid=rule-scope]").inner_text() == "LinkedIn only"
        page.click("[data-testid=ledger-filter] [data-f=channel]")
        assert page.locator("[data-testid=ledger-group][data-group=channel] [data-testid=rule-row]").count() == 1
        page.click("[data-testid=ledger-filter] [data-f=all]")
        # 2. three drafts, the same kind of edit on each -> a suggestion, not a rule
        pk = page.evaluate(RUNS)
        assert len(pk) == 3, pk
        got = page.evaluate(EDIT, pk)
        word = got["word"]
        assert got["suggestions"] and word in got["suggestions"][0]["rule"].lower(), got
        page.evaluate("loadLedger()")
        page.wait_for_selector("[data-testid=rule-suggestion]", timeout=30000)
        card = page.locator("[data-testid=rule-suggestion]").first
        assert word in card.inner_text().lower() and "From 3 of your edits" in card.inner_text()
        assert not [t for t in page.locator("[data-testid=rule-row] [data-testid=rule-text]").all_inner_texts() if word in t.lower()]
        card.locator("summary").click()
        assert card.locator("[data-testid=rule-edits] li").count() == 3
        _shot(page, "ledger-suggestion", width)
        _no_overflow(page)
        # 3. accept -> a learned rule with its edits
        card.locator("[data-testid=suggestion-accept]").click()
        _until(lambda: page.locator("[data-testid=rule-suggestion]").count() == 0)
        learned = page.locator("[data-testid=rule-row]", has_text=word).first
        assert learned.locator("[data-testid=rule-origin]").inner_text() == "Learned from 3 edits"
        learned.locator("[data-testid=rule-more]").click()
        page.wait_for_selector("[data-testid=rule-edits] li", timeout=15000)
        _shot(page, "ledger-learned", width)
        _no_overflow(page)
        # 4. the next draft follows it, says which rules it followed and where its facts came from
        page.evaluate(GO, "agent")
        run = page.evaluate("""async () => { const r = await api('/api/agent/runs', { method: 'POST', body: { query: 'travel agents', format: 'carousel' } });
            let run = r; for (let i = 0; i < 240 && !['ready', 'failed'].includes(run.status); i++) { await new Promise(s => setTimeout(s, 500)); run = await api('/api/agent/runs/' + r.id); }
            showRun(run); return run; }""")
        for p in run["result"]["packages"]:
            assert not re.search(rf"\b{re.escape(word)}\b", p["text"], re.I), p["text"]
        page.wait_for_selector("[data-testid=pkg-ledger]", timeout=30000)
        box = page.locator("[data-testid=pkg-ledger]").first
        box.locator("[data-testid=pkg-applied] summary").click()
        assert box.locator("[data-testid=pkg-applied-rule]", has_text=word).count() == 1
        if box.locator("[data-testid=pkg-evidence]").count():
            box.locator("[data-testid=pkg-evidence] summary").click()
        box.locator("[data-testid=pkg-review-link]").click()
        page.wait_for_selector("[data-testid=review-link-url]", timeout=15000)
        url = page.input_value("[data-testid=review-link-url]")
        box.scroll_into_view_if_needed()
        _shot(page, "draft-rules-sources-review", width)
        _no_overflow(page)
        # 5. the review page: read-only, approve
        rv = ctx.new_page()
        rv.goto(url)
        rv.wait_for_selector("[data-testid=review-draft]", timeout=15000)
        _shot(rv, "review-page", width)
        assert rv.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 1
        rv.fill("textarea[name=note]", "Looks good")
        rv.click("[data-testid=review-approve]")
        rv.wait_for_selector("[data-testid=review-done]", timeout=15000)
        _shot(rv, "review-approved", width)
        assert not errors, errors
        assert not buffer_calls
    finally:
        ctx.close()
