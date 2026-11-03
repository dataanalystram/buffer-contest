"""Hostile text in the views that show outside content can't run script or inject tags.

Feed/source items (Product Hunt, YC, X, HN, RSS), Library filenames, run and take titles, story links and summaries all
come from places HuhQueue doesn't control. This test opens the sample workspace at /demo, rewrites those API answers on
the way into the browser so every text field carries ``<img onerror>``, ``</script><script>`` and every link is a
``javascript:`` URL, then visits News > Sources, Library, Make a post and One take and checks that:
- no injected element exists (the payload is shown as text instead) and the page-level canary never fires;
- no link the app built has a ``javascript:`` href (the app's own bookmarklet is the one intended exception).
safeUrl itself is also checked directly in Node when Node is installed. Skips without Chromium."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')"
EVIL = '<img src=x data-xss onerror="window.__pwned=1"></script><script data-xss>window.__pwned=1</script>"\'><b data-xss>x</b>'
JS_URL = "javascript:window.__pwned=1"
TEXT_KEYS = {"title", "name", "summary", "text", "source", "author", "label", "type_label", "caption", "transcript",
             "status", "note", "description", "site", "channel", "reason"}
URL_KEYS = {"url", "link", "discussion", "externalLink", "source_url", "launch_url"}
ROUTES = ("/api/sources/library", "/api/library", "/api/radar", "/api/agent/runs", "/api/takes")
STATIC = Path(__file__).resolve().parents[2] / "studio" / "static"


def _poison(v, key=""):
    if isinstance(v, dict):
        return {k: _poison(x, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_poison(x, key) for x in v]
    if isinstance(v, str):
        if key in URL_KEYS:
            return JS_URL
        if key in TEXT_KEYS:
            return v + EVIL
    return v


def _until(fn, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.25)
    return False


def _injected(page):
    return page.evaluate("""() => ({
        tags: document.querySelectorAll('[data-xss]').length,
        pwned: window.__pwned === 1,
        js_links: [...document.querySelectorAll('a[href]')].filter(a => a.id !== 'bookmarklet'
            && /^\\s*javascript:/i.test(a.getAttribute('href'))).map(a => a.outerHTML.slice(0, 120)),
    })""")


def test_safe_url_only_lets_web_links_through():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = (STATIC / "app.js").read_text()
    m = re.search(r"const safeUrl = .*?\n.*?;\s*};", src, re.S)
    assert m, "safeUrl helper missing from app.js"
    cases = {
        "https://example.com/a?b=1": "https://example.com/a?b=1", "http://x.io": "http://x.io", "/api/library": "/api/library",
        "#top": "#top", "mailto:hi@example.com": "mailto:hi@example.com", "page.html": "page.html",
        "javascript:alert(1)": "#", " JaVaScRiPt:alert(1)": "#", "java\tscript:alert(1)": "#", "java\nscript:alert(1)": "#",
        "vbscript:msgbox(1)": "#", "data:text/html,<script>alert(1)</script>": "#", "data:image/png;base64,AAAA": "data:image/png;base64,AAAA",
        "": "", None: "",
    }
    js = m.group(0) + "\nconst c = " + json.dumps(list(cases)) + ";\nconsole.log(JSON.stringify(c.map(safeUrl)));"
    out = json.loads(subprocess.run([node, "-e", js], capture_output=True, text=True, check=True).stdout)
    assert dict(zip(cases, out)) == {k: v for k, v in cases.items()}


def test_hostile_outside_content_renders_as_text(demo_live, chromium):
    pytest.importorskip("playwright.sync_api")
    base, buffer_calls = demo_live
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    ctx.add_init_script(NO_TOUR)
    hit = set()

    def poison(route):
        path = route.request.url[len(base):].split("?")[0]
        r = route.fetch()
        try:
            body = r.json()
        except Exception:
            return route.fulfill(response=r)
        hit.add(path)
        route.fulfill(response=r, body=json.dumps(_poison(body)), headers={**{k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "content-encoding")},
                                                                                 "content-type": "application/json"})

    ctx.route(lambda u: u.startswith(base) and u[len(base):].split("?")[0] in ROUTES, poison)
    page = ctx.new_page()
    try:
        page.goto(base + "/demo")
        page.wait_for_selector("[data-testid=tab-news]", timeout=60000)
        page.click("[data-testid=tab-news]")
        page.click("[data-testid=news-tab-sources]")
        assert _until(lambda: page.locator("[data-testid=lib-item]").count() > 0), "News > Sources showed no items"
        assert EVIL[:20] in page.inner_text("#libItems"), "the hostile title should show up as plain text"
        res = _injected(page)
        assert res == {"tags": 0, "pwned": False, "js_links": []}, res

        page.click("[data-testid=tab-library]")
        assert _until(lambda: page.locator("[data-testid=library-item]").count() > 0), "Library showed no items"
        res = _injected(page)
        assert res == {"tags": 0, "pwned": False, "js_links": []}, res

        for tab in ("tab-agent", "tab-take"):
            page.click(f"[data-testid={tab}]")
            time.sleep(2.5)  # let the discover/recent lists and the takes list draw
            res = _injected(page)
            assert res == {"tags": 0, "pwned": False, "js_links": []}, (tab, res)
        assert {"/api/sources/library", "/api/library"} <= hit, hit
        assert not buffer_calls
    finally:
        ctx.close()
