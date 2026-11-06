"""Owner, 5 Nov 2026 (spotted live): the bottom "1 post ready to send" bar clipped the "Save all as Buffer drafts (not
posted)" button under the post cards, at 390 and 1280 px; and the One take Trim & caption window must not show inert In/Out
trim handles for a clip that isn't a video (an image or carousel). In a real browser:

* while the Ready to send bar is shown, the duplicate "Save all" button is hidden (the bar's "Approve all as Buffer
  drafts" presses it, so saving still works), and every button in the bar is whole: inside the bar, nothing cut off;
* the Trim & caption window for a non-video clip has no Trim tab and no In/Out handles (hidden and inert) and opens on
  Caption; a video clip keeps both."""
import pytest

from .test_chat_ui_e2e import ARTICLE, _page, _settled, live, pw  # noqa: F401  (live: the signed-in server fixture)

WHOLE = """() => { const a = document.getElementById('mpActs').getBoundingClientRect();
  return [...document.querySelectorAll('#mpActs button, #mpActs .hc-ready-t')].filter((b) => b.offsetParent && b.textContent.trim())
    .map((b) => { const r = b.getBoundingClientRect(); return { t: b.textContent.trim(), clipped: b.scrollWidth > b.clientWidth + 1 && !b.classList.contains('hc-ready-t'),
      out: r.left < a.left - 1 || r.right > a.right + 1 || r.top < a.top - 1 || r.bottom > a.bottom + 1 }; }); }"""


# the channel ids of the packages still waiting to be sent (a card with an enabled Send button, not on hold, not saved)
READY_IDS = """() => [...document.querySelectorAll('[data-testid=package]')]
  .filter((c) => { const b = c.querySelector('[data-testid=pkg-send]'); return b && !b.disabled; }).map((c) => c.dataset.ch)"""
# true once every one of those cards shows the saved state, as a Buffer draft (not posted)
ALL_SAVED = """(ids) => ids.every((id) => { const c = [...document.querySelectorAll('[data-testid=package]')].find((x) => x.dataset.ch === id);
  const st = c && c.querySelector('[data-testid=pkg-sent]'); return !!st && /as a draft/i.test(st.textContent); })"""


@pytest.mark.parametrize("w,h", [(1280, 800), (390, 844), (760, 900)])
def test_ready_bar_never_clips_save_all(live, chromium, close_contexts, w, h):
    base, app = live
    app.state.studio.media_reach = lambda urls: []  # the draft save never probes the media over the network
    ctx, page, errors = _page(chromium, base, w, h)
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("agent-query").fill("Post this on all my channels: " + ARTICLE["url"])
    page.get_by_test_id("agent-query").press("Enter")
    page.get_by_test_id("package").first.wait_for(timeout=90000)
    _settled(page)
    pw.expect(page.get_by_test_id("mp-actions")).to_be_visible()
    assert "ready to send" in page.get_by_test_id("chat-ready-text").inner_text()
    # the duplicate under the cards is out of the way (still in the DOM for the bar's button to press)
    assert page.get_by_test_id("save-drafts").count() == 1
    assert page.get_by_test_id("save-drafts").is_hidden()
    for y in (0.4, 0.7, 1.0):  # mid-scroll and at the end: every button in the bar whole
        page.evaluate(f"window.scrollTo(0, document.documentElement.scrollHeight * {y})")
        page.wait_for_timeout(250)
        bad = [b for b in page.evaluate(WHOLE) if b["clipped"] or b["out"]]
        assert not bad, (w, y, bad)
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w + 1, f"horizontal overflow at {w}px: {sw}"
    # the bar's button still saves every ready post as a draft
    # record every ready package (one with a live Send button) before the click; the wait below needs each of them saved
    ready = page.evaluate(READY_IDS)
    assert len(ready) >= 2, ready  # "all my channels": more than one post, so one saved card can't pass for all of them
    page.get_by_test_id("mp-approve").scroll_into_view_if_needed()
    page.get_by_test_id("mp-approve").click()
    page.wait_for_function(ALL_SAVED, arg=ready, timeout=30000)
    assert not errors, errors


def test_trim_handles_only_for_video_clips(live, chromium, close_contexts):
    base, app = live
    ctx, page, errors = _page(chromium, base, 1280, 900)
    page.goto(base + "/?tab=take")
    page.wait_for_function("() => typeof window.openClipEditor === 'function'")

    def open_clip(url):
        page.evaluate("""(url) => { const d = document.getElementById('clipDialog'); if (d.open) d.close();
            T.take = { id: 1, style: 'classic', can_rerender: true, input: { channels: [{ id: 'ch_li', service: 'linkedin' }] },
                       clips: [{ id: 'c1', start: 10, end: 30, url, cover_url: '', transcript: 'hello there', hook: 'hello' }] };
            window.openClipEditor('c1'); }""", url)
        pw.expect(page.get_by_test_id("clip-dialog")).to_be_visible()

    open_clip("/m/abc123.jpg")  # an image: nothing to trim
    assert page.get_by_test_id("clip-tab-trim").is_hidden()
    for h in ("clip-handle-in", "clip-handle-out"):
        assert page.get_by_test_id(h).is_hidden()
        assert page.get_by_test_id(h).evaluate("b => b.inert || b.hasAttribute('inert')")
    assert page.get_by_test_id("clip-tab-caption").get_attribute("aria-selected") == "true"
    open_clip("/m/abc123.mp4")  # a video: the handles are back and live
    assert page.get_by_test_id("clip-tab-trim").is_visible()
    for h in ("clip-handle-in", "clip-handle-out"):
        assert page.get_by_test_id(h).is_visible()
        assert not page.get_by_test_id(h).evaluate("b => b.hasAttribute('inert')")
    assert page.get_by_test_id("clip-tab-trim").get_attribute("aria-selected") == "true"
    assert not errors, errors
