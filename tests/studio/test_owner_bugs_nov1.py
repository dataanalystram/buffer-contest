"""Owner bug reports, 1 Nov 2026 (live site screenshots): regression tests.

A1  Make a post › Recent content › "View all →" opened an EMPTY Library while five runs showed: runs made before the
    Library existed (or rendered on the GitHub worker) had no Library rows. The Library now registers every run's
    renders (idempotent), "View all" carries the Recent filter (Reels -> Videos, Drafts -> Drafts), 12 per page; and a
    One take card's cover was overwritten with "" (the blank 5th card), video-only cards get a still.
A2  "From a link" did nothing visible: it is now a real mode (selected, URL field focused, paste -> preview, Create).
A3  No light/dark toggle: a sun/moon in the top bar and Settings › Appearance, saved in localStorage and the account.
"""
import json
import time

import pytest

from studio import link_preview, reader

from .conftest import connect
from .test_e2e_studio import NO_TOUR, live  # noqa: F401  (the signed-in live server fixture)

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64 + b"moov" + bytes(range(256)) * 8


def _uid(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


def _legacy_runs(c):
    """The owner's live shape: a legacy News run (Reel per channel in packages + res.media), an agent run saved as a
    Buffer draft, and a One take (no clips yet, the source video as media_token). No library_items rows at all."""
    from studio.actions import Actor
    h, st, uid = c.app.state.house, c.app.state.store, _uid(c)
    a = Actor(uid)
    v1, v2, src = (h.put_media(a, MP4 + bytes([i]), f"r{i}.mp4", kind="render", meta={"type": "video", "duration": 14}) for i in range(3))
    news = {"source": {"url": "https://news.example.com/yc-gps", "image": "https://news.example.com/hero.jpg", "title": "YC names GPs"},
            "media": [{"type": "video", "url": h.media_url(v1)}],
            "packages": [{"channel_id": "ch_ig", "format": "reel", "media": [{"type": "video", "url": h.media_url(v1)}]}]}
    agent = {"source": {"url": "https://x.com/a/status/1", "media": [{"type": "video", "url": "https://video.twimg.com/x.mp4",
                                                                      "poster": "https://pbs.twimg.com/p.jpg"}]},
             "channel_media": {"ch_ig": {"format": "reel", "media": [{"token": v2, "type": "video", "url": h.media_url(v2)}]}},
             "packages": [{"channel_id": "ch_ig", "format": "reel", "media": [{"token": v2, "type": "video", "url": h.media_url(v2)}],
                           "sent": {"mode": "draft"}}]}
    now = time.time()
    ids = []
    for kind, inp, res, t in (("news", {"url": "https://news.example.com/yc-gps"}, news, now - 300),
                              ("agent", {"prompt": "post this"}, agent, now - 200),
                              ("take", {"name": "ISS talk", "media_token": src}, {"moments": []}, now - 100)):
        ids.append(st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                        (uid, kind, json.dumps(inp), "ready", "[]", json.dumps(res), t, t)))
    return ids


def test_view_all_library_lists_every_run_render(human):
    ids = _legacy_runs(human)
    assert not human.app.state.store.q("SELECT id FROM library_items")
    d = human.get("/api/library").json()
    assert len(d["items"]) == 2, d  # the News Reel and the agent Reel (the take has no render yet)
    assert {i["kind"] for i in d["items"]} == {"video"}
    assert all(i["used_in"] for i in d["items"])
    # idempotent: listing again adds nothing
    assert len(human.get("/api/library").json()["items"]) == 2
    assert len(human.app.state.store.q("SELECT id FROM library_items")) == 2
    # the Recent filters carried by "View all"
    assert len(human.get("/api/library?kind=video").json()["items"]) == 2
    drafts = human.get("/api/library?kind=draft").json()["items"]
    assert len(drafts) == 1 and drafts[0]["used_in"][0]["run_id"] == ids[1]
    assert human.get("/api/library?kind=carousel").json()["items"] == []
    # a video card gets a still: the story's picture through /api/thumb (never a hotlink)
    news_item = next(i for i in d["items"] if i["used_in"][0]["run_id"] == ids[0])
    assert news_item["thumb_src"] == {"url": "https://news.example.com/yc-gps", "img": "https://news.example.com/hero.jpg"}


def test_recent_cards_take_cover_and_video_stills(human):
    ids = _legacy_runs(human)
    rows = {r["id"]: r for r in human.get("/api/agent/runs?limit=60").json()}
    take = rows[ids[2]]
    assert take["cover"].endswith(".mp4") and "/m/" in take["cover"]  # was "": the blank 5th card
    assert rows[ids[1]]["thumb_src"]["img"] == "https://pbs.twimg.com/p.jpg"  # the X video's poster
    assert rows[ids[0]]["thumb_src"]["url"] == "https://news.example.com/yc-gps"
    assert rows[ids[1]]["state"] == "draft"


def test_page_contract_view_all_link_mode_and_theme():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / "studio" / "static"
    html, mp, lib = (root / "index.html").read_text(), (root / "makepost.js").read_text(), (root / "library.js").read_text()
    assert 'data-testid="agent-mode-link"' in html and 'role="tab" data-mode="link"' in html
    assert 'data-testid="mp-link-url"' in html and 'data-testid="mp-link-preview"' in html
    assert "/api/link/preview" in mp and "LIB_PRESET" in mp and "LIB_PRESET" in lib
    assert '<option value="draft">Drafts</option>' in html and "per = 12" in lib
    assert 'data-testid="theme-toggle"' in html and 'data-testid="settings-theme"' in html
    assert html.index("/static/appearance.js") < html.index("/static/style.css")  # no flash of the other theme
    for css in ("style.css", "premium.css", "makepost.css"):
        t = (root / css).read_text()
        assert ":root[data-theme=dark]" in t and ":not([data-theme=light])" in t, css


def test_link_preview_reads_through_the_agents_reader(human, monkeypatch):
    seen = []
    monkeypatch.setattr(reader, "read_url", lambda url, http=None, x_bearer="": seen.append(url) or {
        "url": url, "title": "ernd: the credit card for the AI stack", "description": "4% back on AI spend", "site": "ernd.io",
        "image": "https://ernd.io/share/card.jpg", "via": "direct", "paragraphs": []})
    link_preview.CACHE.clear()
    r = human.post("/api/link/preview", json={"url": "https://ernd.io/"})
    assert r.status_code == 200, r.text
    assert r.json()["title"].startswith("ernd") and r.json()["kind"] == "page" and seen == ["https://ernd.io/"]
    assert human.post("/api/link/preview", json={"url": "http://169.254.169.254/latest"}).status_code == 400
    assert human.post("/api/link/preview", json={"url": "notalink"}).status_code == 400


def test_link_preview_needs_a_session_and_csrf(client):
    assert client.post("/api/link/preview", json={"url": "https://ernd.io/"}).status_code in (401, 403)


def test_appearance_pref_round_trip(human):
    assert human.get("/api/prefs/appearance").json()["mode"] == "system"
    assert human.put("/api/prefs/appearance", json={"mode": "dark"}).json()["saved"] == "account"
    assert human.get("/api/prefs/appearance").json()["mode"] == "dark"
    assert human.put("/api/prefs/appearance", json={"mode": "purple"}).status_code == 400


# ---------------------------------------------------------------- in the browser (Playwright; skips without Chromium)
def _page(live, chromium, w=1440, h=900, scheme="dark"):
    base, fake, app = live
    ctx = chromium.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    ctx.add_init_script(NO_TOUR)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    return ctx, ctx.new_page()


def test_e2e_view_all_opens_a_full_library_on_the_same_filter(live, chromium, close_contexts):
    base, fake, app = live
    from fastapi.testclient import TestClient  # noqa: F401
    st, h = app.state.store, app.state.house

    class C:  # _legacy_runs only needs .app
        pass
    c = C(); c.app = app
    _legacy_runs(c)
    ctx, page = _page(live, chromium)
    page.goto(base + "/?tab=agent")
    page.get_by_test_id("run-row").first.wait_for()
    assert page.get_by_test_id("run-row").count() == 3
    page.get_by_test_id("mp-filter").nth(1).click()  # Reels
    page.get_by_test_id("mp-view-all").click()
    page.get_by_test_id("library-item").first.wait_for()
    assert page.get_by_test_id("library-kind").input_value() == "video"
    assert page.get_by_test_id("library-item").count() == 2
    assert not page.get_by_test_id("library-empty").is_visible()


def test_e2e_from_a_link_mode(live, chromium, close_contexts, monkeypatch):
    monkeypatch.setattr(reader, "read_url", lambda url, http=None, x_bearer="": {
        "url": url, "title": "ernd: the credit card for the AI stack", "description": "4% back on AI spend", "site": "ernd.io",
        "image": "", "via": "direct", "paragraphs": []})
    link_preview.CACHE.clear()
    ctx, page = _page(live, chromium)
    page.goto(base_of(live) + "/?tab=agent")
    page.get_by_test_id("agent-mode-link").click()
    assert page.get_by_test_id("agent-mode-link").get_attribute("aria-selected") == "true"
    assert page.get_by_test_id("agent-mode-agent").get_attribute("aria-selected") == "false"
    url = page.get_by_test_id("mp-link-url")
    assert url.is_visible() and url.evaluate("e => e === document.activeElement")
    url.fill("https://ernd.io/")
    page.get_by_test_id("mp-link-title").wait_for()
    assert "credit card" in page.get_by_test_id("mp-link-title").inner_text()
    assert "from link" in page.get_by_test_id("agent-go").inner_text().lower()
    page.get_by_test_id("agent-mode-agent").click()  # leaving the mode hides the field
    assert not url.is_visible()


def base_of(live):
    return live[0]


@pytest.mark.parametrize("w", [1440, 390])
def test_e2e_theme_toggle_persists(live, chromium, close_contexts, w):
    ctx, page = _page(live, chromium, w=w, h=844 if w == 390 else 900, scheme="light")
    page.goto(base_of(live) + "/?tab=agent")
    t = page.get_by_test_id("theme-toggle")
    t.wait_for()
    assert t.is_visible() and t.bounding_box()["width"] >= 40 and t.bounding_box()["height"] >= 36
    bg = lambda: page.evaluate("getComputedStyle(document.body).backgroundColor")  # noqa: E731
    light = bg()
    t.click()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark" and bg() != light
    assert page.evaluate("localStorage.getItem('hq_mode')") == "dark"
    page.reload(); page.get_by_test_id("theme-toggle").wait_for()
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    page.goto(base_of(live) + "/?tab=settings")
    page.get_by_test_id("theme-light").click()
    assert page.evaluate("document.documentElement.dataset.theme") == "light" and bg() == light
    assert page.get_by_test_id("theme-light").get_attribute("aria-checked") == "true"
