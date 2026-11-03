"""The owner's 3 Nov 2026 Make a post fixes in a real browser (tests/studio/test_makepost_nov3_fixes.py is the unit side).
Buffer is FakeBuffer, the video render and Instagram's music catalogue are mocked, previews draw from a mocked hero image.
Covers: "Changed since saved" + Update draft edits the same Buffer draft (a scheduled post is refused), no music by default,
the Music control (None / Instagram music) and the carousel note, "use lofi" / "add music" / "no music", the Reel -> carousel
template question (Keep Reel / Switch, the video as slide 2, Undo back to the Reel), All templates… with real previews and
3-line descriptions, "change brand kit" with no name (asks, sample kits hidden) and a named kit for this run only, youtu.be vs
youtube.com with no false "isn't from your link" warning, and the 390 px layout (no overflow, 44 px tap targets)."""
import json
import time

import httpx
import pytest

from studio import brand_kits, brand_onboard, house, reel_audio
from studio.agent_tools import Tools
from studio.app import create_app
from studio.config import load_settings

from . import conftest
from .browsers import free_port, serve
from .conftest import FakeBuffer
from .test_agent_core import OWNER_CHANNELS, Router, jpeg
from .test_agent_ux_nov3 import FACTS, GH, PROMPT, VID

pw = pytest.importorskip("playwright.sync_api")
NO_TOUR = "localStorage.setItem('hq_tour_done', '1'); localStorage.setItem('hq_onb_seen', '1')"
RIGHTS = "Yes, I have the rights to repost it with credit"
TRACKS = [{"audio_id": "101953222865674", "title": "Soft Focus", "display_artist": "Lofi Lab", "duration_ms": 90139, "type": "music",
           "preview_url": "", "cover_url": ""},
          {"audio_id": "222", "title": "Night Drive", "display_artist": "Chill Co", "duration_ms": 60000, "type": "music",
           "preview_url": "", "cover_url": ""}]
MOVIE = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = FakeBuffer()
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="n" * 64,
                      db_path=str(tmp_path / "n3.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True)
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    h = app.state.house
    h.sync, h.worker_mode = True, "local"
    app.state.studio.media_reach = lambda urls: []  # the draft save never probes the media over the network
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)

    def job(self, run, spec, frames, note_extra=""):  # the render is mocked: a credited Reel comes back
        tok = self.h.put_media(self.a, MOVIE, "reel.mp4", kind="render", meta={"type": "video", "width": 1080, "height": 1920, "duration": 12.0})
        return [{"token": tok, "url": self.h.media_url(tok), "type": "video", "name": "reel.mp4"}], "Rendered on this server."
    monkeypatch.setattr(Tools, "_video_job", job)
    asked = []

    def search(studio, a, channel_id, query=""):  # Instagram's music catalogue, mocked
        asked.append(query)
        return {"channel_id": channel_id, "query": query, "trending": not query, "needs_facebook": False,
                "tracks": [dict(t) for t in TRACKS if not query or query.lower() in (t["title"] + " " + t["display_artist"]).lower()]}
    monkeypatch.setattr(reel_audio, "search", search)
    st = app.state.store
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-n3", "csrf-n3"); st.update_session("sid-n3", user_id="acc_1")
    server = serve(app, port)
    yield {"base": f"http://127.0.0.1:{port}", "app": app, "fake": fake, "asked": asked}
    server.should_exit = True


A = house.Actor("acc_1")


def _seed(app, reel=True):
    """The owner's repo post: facts read, a third-party video found, slides for now; reel=True answers the one rights
    question so the Instagram package is a credited Reel."""
    h = app.state.house
    rid = h.brain.start(A, {"prompt": PROMPT, "channel_ids": ["ch_ig"], "brain": "external"})["id"]
    run = h.run(A, rid)
    res = run["result"]
    res["source"] = {"url": GH, "title": "vercel-labs/agent-desktop", "site": "github.com", "description": FACTS[0], "paragraphs": FACTS,
                     "image": "https://example.com/hero.jpg", "media": []}
    res["found_media"] = {"best_downloadable": dict(VID), "best_video": dict(VID)}
    res["video_credit"] = VID["credit"]
    res["repo_info"] = {"stars": "2.1k", "license": "Apache-2.0", "language": "TypeScript"}
    h._save(rid, run["steps"], res)
    h.brain.tool(A, rid, "extract_facts", {"headline": "Vercel's Agent Desktop lets AI agents use your computer", "facts": FACTS})
    h.brain.tool(A, rid, "render_reel", {"channel_ids": ["ch_ig"]})
    h.brain.tool(A, rid, "write_captions", {})
    h.brain.tool(A, rid, "check", {})
    if reel:
        h.brain.reply(A, rid, RIGHTS)
        assert _pk(app, rid)["format"] == "reel"
    return rid


def _pk(app, rid):
    return next(p for p in app.state.house.run(A, rid)["result"]["packages"] if p["channel_id"] == "ch_ig")


def _ctx(chromium, base, w, h=900):
    ctx = chromium.new_context(viewport={"width": w, "height": h}, timezone_id="Europe/Dublin")
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-n3", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, page, errors


def _open(page, base, rid, tries=3):
    """Open the run's Make a post view, retrying the page load (a slow first paint on a busy CI box is not a failure)."""
    last = None
    for i in range(tries):
        try:
            page.goto(base + "/#/agent", wait_until="domcontentloaded")
            page.wait_for_function("() => typeof window.showRun === 'function' && typeof api === 'function'", timeout=15000)
            page.evaluate(f"async () => {{ window.showTab && showTab('agent'); showRun(await api('/api/agent/runs/{rid}')); }}")
            page.get_by_test_id("package").first.wait_for(timeout=15000)
            return
        except Exception as e:  # noqa: BLE001 (retry any load failure, then raise the last one)
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


def _reload(page, rid):
    page.evaluate(f"async () => showRun(await api('/api/agent/runs/{rid}'))")
    page.get_by_test_id("package").first.wait_for()


def _api(page, path, body=None):
    js = "async ([p, b]) => { try { return await api(p, b === null ? {} : { method: 'POST', body: b }); } catch (e) { return { _error: e.message }; } }"
    return page.evaluate(js, [path, body])


def _say(page, text):
    n = page.get_by_test_id("thread-msg").count()
    page.get_by_test_id("reply-input").fill(text)
    page.get_by_test_id("reply-go").click()
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=thread-msg]').length >= {n + 2}"
                           " && !document.querySelector('#mpStatus .spin')")
    return page.get_by_test_id("thread-msg").last.inner_text()


def _ig(page):
    return page.locator("[data-testid=package]").filter(has=page.locator("[data-testid=pkg-music]")).first


def _save_draft(page, live, rid):
    out = _api(page, f"/api/agent/runs/{rid}/packages/ch_ig/schedule", {"mode": "draft", "override_warnings": True})
    assert "_error" not in out, out
    pid = out["post"]["id"]
    live["fake"].posts.append({"id": pid, "text": "x", "status": "draft", "dueAt": None, "channelId": "ch_ig"})
    return pid


def _no_overflow(page, w):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w + 1, f"horizontal overflow at {w}px: {sw}"


def _tap(loc, name, least=44):
    box = loc.bounding_box()
    assert box and box["height"] >= least - 0.5 and box["width"] >= least - 0.5, (name, box)


# ---------------------------------------------------------------- 1 Changed since saved -> Update draft (the same draft)
def test_changed_since_saved_and_update_draft_edits_the_same_draft(live, chromium, close_contexts):
    base, app, fake = live["base"], live["app"], live["fake"]
    rid = _seed(app)
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    pid = _save_draft(page, live, rid)
    out = _api(page, f"/api/agent/runs/{rid}/template", {"template": "launch", "reel": "switch"})
    assert "_error" not in out, out
    _reload(page, rid)
    row = page.get_by_test_id("pkg-changed")
    row.wait_for()
    assert "Changed since saved to Buffer" in row.inner_text()
    assert row.evaluate("e => getComputedStyle(e, '::before').content") in ("none", "normal")  # no stray "Note." label
    assert "Note." not in row.inner_text()
    assert "Changed since saved" in page.get_by_test_id("mp-ready-item").first.inner_text()
    n_created = len(fake.created)
    page.get_by_test_id("pkg-update-draft").click()
    page.get_by_test_id("pkg-changed").wait_for(state="detached")
    assert len(fake.created) == n_created and fake.edits[-1]["id"] == pid and fake.edits[-1]["saveToDraft"] is True
    assert len(fake.edits[-1]["assets"]) == len(_pk(app, rid)["media_urls"])
    assert "In Buffer" in page.get_by_test_id("mp-ready-item").first.inner_text()
    assert not errors, errors


def test_update_draft_refuses_a_post_scheduled_in_buffer(live, chromium, close_contexts):
    base, app, fake = live["base"], live["app"], live["fake"]
    rid = _seed(app)
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    pid = _save_draft(page, live, rid)
    next(p for p in fake.posts if p["id"] == pid)["status"] = "scheduled"  # scheduled in Buffer after it was saved
    _api(page, f"/api/agent/runs/{rid}/template", {"template": "launch", "reel": "switch"})
    _reload(page, rid)
    n_edits = len(fake.edits)
    page.get_by_test_id("pkg-update-draft").click()
    page.wait_for_function("() => /no longer a draft/.test(document.querySelector('[data-testid=toast]')?.innerText || '')")
    assert len(fake.edits) == n_edits and page.get_by_test_id("pkg-update-draft").is_enabled()
    assert not errors, errors


# ---------------------------------------------------------------- 2 music: None by default, words and buttons
def test_music_none_by_default_words_and_buttons(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app)
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    ig = _ig(page)
    off, add = ig.get_by_test_id("music-off"), ig.get_by_test_id("music-add")
    assert off.get_attribute("aria-checked") == "true" and add.get_attribute("aria-checked") == "false"
    assert "ig_audio" not in (_pk(app, rid).get("options") or {}) and ig.get_by_test_id("music-chosen").count() == 0
    # "use lofi" picks a track
    assert "Soft Focus" in _say(page, "use lofi")
    ig = _ig(page)
    ig.get_by_test_id("music-chosen").wait_for()
    assert ig.get_by_test_id("music-add").get_attribute("aria-checked") == "true" and "lofi" in live["asked"]
    assert _pk(app, rid)["options"]["ig_audio"]["title"] == "Soft Focus"
    # "no music" clears it
    assert "no music" in _say(page, "no music").lower()
    assert _ig(page).get_by_test_id("music-off").get_attribute("aria-checked") == "true"
    assert "ig_audio" not in (_pk(app, rid).get("options") or {})
    # "add music" with nothing to go on asks first, with buttons
    assert "Which music?" in _say(page, "add music")
    card = page.locator("[data-testid=ask-card][data-kind=music]")
    card.wait_for()
    assert [b.inner_text() for b in card.get_by_test_id("ask-choice").all()] == ["Soft Focus", "Night Drive"]
    assert "ig_audio" not in (_pk(app, rid).get("options") or {})
    card.get_by_test_id("ask-choice").nth(1).click()
    page.wait_for_function("() => /Night Drive/.test(document.querySelector('[data-testid=music-chosen]')?.innerText || '')")
    assert _pk(app, rid)["options"]["ig_audio"]["audio_id"] == "222"
    # the None button clears; Instagram music opens the picker
    _ig(page).get_by_test_id("music-off").click()
    page.wait_for_function("() => document.querySelector('[data-testid=music-off]')?.getAttribute('aria-checked') === 'true'")
    assert "ig_audio" not in (_pk(app, rid).get("options") or {})
    _ig(page).get_by_test_id("music-add").click()
    page.get_by_test_id("music-dialog").wait_for()
    assert not errors, errors


def test_carousel_music_note(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app, reel=False)  # slides for now: an Instagram carousel
    assert _pk(app, rid)["format"] == "carousel"
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    ig = _ig(page)
    assert "Instagram app" in ig.get_by_test_id("music-carousel-note").inner_text()
    assert ig.get_by_test_id("music-seg").count() == 0
    assert not errors, errors


# ---------------------------------------------------------------- 3 All templates… and the Reel -> carousel question
def test_all_templates_and_reel_to_carousel_keep_switch_undo(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app)
    vid = _pk(app, rid)["media_urls"][0]
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)

    def pick_launch():
        page.get_by_test_id("mp-template-all").click()
        dlg = page.get_by_test_id("tpl-all-dialog")
        dlg.wait_for()
        page.wait_for_function("() => document.querySelectorAll('[data-testid=tpl-all-item]').length >= 6")
        return dlg

    dlg = pick_launch()
    items = dlg.get_by_test_id("tpl-all-item")
    page.wait_for_function("() => [...document.querySelectorAll('[data-testid=tpl-all-item] img')].every(i => i.complete && i.naturalWidth > 0)")
    assert dlg.locator("[data-testid=tpl-all-item] img").count() == items.count()  # a real preview on every one
    assert all(s.startswith("data:image/jpeg") for s in dlg.locator("[data-testid=tpl-all-item] img").evaluate_all("l => l.map(i => i.src)"))
    clamp = dlg.locator("[data-testid=tpl-all-item] small").first.evaluate("e => getComputedStyle(e).webkitLineClamp")
    assert clamp == "3"
    descs = dlg.locator("[data-testid=tpl-all-item]:not(.on) small").evaluate_all("l => l.map(e => e.textContent)")
    assert not any(d.endswith("…") for d in descs)  # the whole text is there; CSS clamps it
    dlg.locator("[data-tpl=launch]").click()
    ask = page.locator("[data-testid=ask-card][data-kind=reel_to_carousel]")
    ask.wait_for()
    assert [b.inner_text() for b in ask.get_by_test_id("ask-choice").all()] == ["Keep Reel", "Switch"]
    assert _pk(app, rid)["format"] == "reel"  # nothing changed yet
    ask.get_by_test_id("ask-choice").first.click()  # Keep Reel
    ask.wait_for(state="detached")
    assert _pk(app, rid)["format"] == "reel" and _pk(app, rid)["media_urls"] == [vid]
    pick_launch().locator("[data-tpl=launch]").click()
    page.locator("[data-testid=ask-card][data-kind=reel_to_carousel] [data-testid=ask-choice]").last.click()  # Switch
    page.wait_for_function("() => document.querySelectorAll('[data-testid=pkg-slide]').length >= 3")
    pk = _pk(app, rid)
    assert pk["format"] == "carousel" and pk["media_urls"][1] == vid
    assert page.locator("[data-testid=pkg-slide]").nth(1).locator("video").count() == 1  # the video is slide 2
    undo = page.get_by_test_id("pkg-undo")
    assert "Back to the Reel" in undo.inner_text()
    undo.click()
    page.get_by_test_id("pkg-video").wait_for()
    pk = _pk(app, rid)
    assert pk["format"] == "reel" and pk["media_urls"] == [vid]
    assert not errors, errors


# ---------------------------------------------------------------- 4 brand kits: ask when unnamed, a named kit is run-only
def test_change_brand_kit_asks_without_samples_and_named_kit_is_run_only(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app)
    bk = brand_kits.attach(app.state.house)
    bk.list(A)  # seeds HuhByte AI
    s = brand_kits.normalize({"name": "Linear", "handle": "linear", "colors": {"primary": "#08090A", "accent": "#5E6AD2"}})
    s["label"] = brand_onboard.SAMPLE_LABEL
    bk._insert(A, s)  # an untouched sample kit
    mine = bk._insert(A, brand_kits.normalize({"name": "Acme", "handle": "acme", "colors": {"primary": "#FFFFFF", "accent": "#FF5A1F"}}))
    hb = next(k["id"] for k in bk.list(A)["kits"] if k["name"] == "HuhByte AI")
    bk.activate(A, hb)
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    assert "Which brand kit" in _say(page, "change brand kit")
    card = page.locator("[data-testid=ask-card][data-kind=brand_kit]")
    card.wait_for()
    labels = [b.inner_text() for b in card.get_by_test_id("ask-choice").all()]
    assert "Linear" not in labels and {"HuhByte AI", "Acme"} <= set(labels), labels
    assert not app.state.house.run(A, rid)["result"].get("kit_override")
    n = page.get_by_test_id("thread-msg").count()
    card.get_by_test_id("ask-choice").filter(has_text="Acme").click()
    page.wait_for_function(f"() => document.querySelectorAll('[data-testid=thread-msg]').length >= {n + 2}"
                           " && !document.querySelector('#mpStatus .spin')")
    page.wait_for_function("() => !document.querySelector('#mpStatus .spin')")
    assert app.state.house.run(A, rid)["result"]["kit_override"]["id"] == mine and bk._active(A) == hb  # this run only
    assert not errors, errors


# ---------------------------------------------------------------- 5 youtu.be and youtube.com are the same video
def test_youtu_be_vs_youtube_com_gives_no_false_warning(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app)
    h = app.state.house
    run = h.run(A, rid)
    run["input"]["source_url"] = "https://youtu.be/MxAa8g8qFz4"
    run["result"]["source"].update(url="https://www.youtube.com/watch?v=MxAa8g8qFz4", requested_url="https://youtu.be/MxAa8g8qFz4")
    h.store.x("UPDATE agent_runs SET input=? WHERE id=?", (json.dumps(run["input"]), rid))
    h._save(rid, run["steps"], run["result"])
    ctx, page, errors = _ctx(chromium, base, 1280)
    _open(page, base, rid)
    out = _api(page, f"/api/agent/runs/{rid}/tools/check", {})
    assert "_error" not in out and out["about_source"] is True and "the story isn't from your link" not in out["problems"]
    _reload(page, rid)
    assert "isn't from your link" not in page.locator("body").inner_text()
    assert not errors, errors


# ---------------------------------------------------------------- 6 390 px: no overflow, 44 px tap targets
def test_phone_390_layout_and_tap_targets(live, chromium, close_contexts):
    base, app = live["base"], live["app"]
    rid = _seed(app)
    ctx, page, errors = _ctx(chromium, base, 390, 844)
    _open(page, base, rid)
    _save_draft(page, live, rid)
    _api(page, f"/api/agent/runs/{rid}/template", {"template": "launch", "reel": "switch"})
    _reload(page, rid)
    page.get_by_test_id("pkg-changed").wait_for()
    _no_overflow(page, 390)
    _tap(page.get_by_test_id("pkg-update-draft"), "Update draft")
    row = page.get_by_test_id("pkg-changed").bounding_box()
    assert row and row["x"] >= 0 and row["x"] + row["width"] <= 390 + 1
    # a Reel's Music control, and the Undo button, on a phone
    _api(page, f"/api/agent/runs/{rid}/undo", {"channel": "ch_ig"})
    _reload(page, rid)
    ig = _ig(page)
    ig.get_by_test_id("music-seg").scroll_into_view_if_needed()
    _tap(ig.get_by_test_id("music-off"), "Music: None")
    _tap(ig.get_by_test_id("music-add"), "Music: Instagram music")
    _no_overflow(page, 390)
    # the Keep Reel / Switch card
    _api(page, f"/api/agent/runs/{rid}/template", {"template": "launch"})
    _reload(page, rid)
    ask = page.locator("[data-testid=ask-card][data-kind=reel_to_carousel]")
    ask.wait_for()
    for b in ask.get_by_test_id("ask-choice").all():
        _tap(b, "ask choice " + b.inner_text())
    _no_overflow(page, 390)
    # All templates… fits the phone: two columns, a 44 px close button
    page.get_by_test_id("mp-template-all").scroll_into_view_if_needed()
    page.get_by_test_id("mp-template-all").click()
    page.wait_for_function("() => document.querySelectorAll('[data-testid=tpl-all-item]').length >= 6")
    dlg = page.get_by_test_id("tpl-all-dialog").bounding_box()
    assert dlg and dlg["x"] >= 0 and dlg["x"] + dlg["width"] <= 390 + 1
    cols = page.get_by_test_id("tpl-all-grid").evaluate("g => getComputedStyle(g).gridTemplateColumns.split(' ').length")
    assert cols == 2
    _tap(page.get_by_test_id("tpl-all-close"), "All templates close")
    assert not errors, errors
