"""Owner-reported global UI issues (1 Nov 2026): every footer and landing link works (the API link used to open a blank
Swagger page the CSP blocked), the API reference lists every endpoint, the app shell uses a wide screen, Planner posts
show their network's mark, and the Inbox folds a Make a post run's AI calls into one row."""
import re
import time

import pytest

from studio import activity, docs


# ------------------------------------------------------------------ API reference
def test_api_docs_redirects_to_a_first_party_reference(client):
    r = client.get("/api/docs", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/docs/api"
    page = client.get("/docs/api")
    assert page.status_code == 200 and 'data-testid="api-reference"' in page.text
    # no third-party script or inline script: it renders under script-src 'self'
    assert "cdn.jsdelivr" not in page.text and "SwaggerUIBundle" not in page.text
    assert not re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/ld\+json)[^>]*>", page.text)
    for anchor in ('id="auth"', 'id="mcp"', 'id="schema"', "/openapi.json", "Authorization: Bearer", "/mcp"):
        assert anchor in page.text


def test_api_reference_covers_every_endpoint_with_a_description(app, client):
    schema = app.openapi()
    want = {(m.upper(), p) for p, ops in schema["paths"].items() for m in ops if p != "/mcp"}
    eps = docs.api_endpoints(schema)
    assert {(e["method"], e["path"]) for e in eps} == want
    missing = [f'{e["method"]} {e["path"]}' for e in eps if not e["described"]]
    assert not missing, f"add a line to docs._DESC for: {missing}"
    page = client.get("/docs/api").text
    assert page.count('data-testid="api-endpoint"') == len(eps)
    from studio import mcp
    assert page.count('data-testid="api-mcp-tool"') == len(mcp.TOOLS)


def test_sitemap_and_robots_point_at_the_api_reference(client):
    assert "/docs/api</loc>" in client.get("/sitemap.xml").text
    robots = client.get("/robots.txt").text
    assert "Allow: /docs/api" in robots and "/api/docs" not in robots


# ------------------------------------------------------------------ every footer / landing / docs link answers
def _links(html: str) -> set[str]:
    return set(re.findall(r'<a [^>]*href="(/[^"#?]*)', html))


def test_every_internal_link_on_landing_docs_and_app_footer_answers(app, client):
    from pathlib import Path
    pages = {"/": client.get("/").text, "/docs": client.get("/docs").text, "/docs/api": client.get("/docs/api").text}
    app_html = (Path(__file__).resolve().parents[2] / "studio" / "static" / "index.html").read_text()
    foot = app_html[app_html.index('<footer class="foot">'):]
    links = set().union(*(_links(h) for h in pages.values())) | _links(foot)
    for must in ("/docs", "/privacy", "/terms", "/security", "/docs/api"):
        assert must in _links(foot), f"app footer lost {must}"
        assert must in _links(pages["/"]), f"landing footer lost {must}"
    bad = []
    for href in sorted(links - {"/login", "/demo", "/logout"}):
        r = client.get(href, follow_redirects=True)
        if r.status_code != 200 or len(r.text.strip()) < 200:
            bad.append(f"{href} -> {r.status_code}")
    assert not bad, bad
    assert "https://github.com/dataanalystram/buffer-contest" in foot and "https://github.com/dataanalystram/buffer-contest" in pages["/"]


def test_footer_links_click_through_in_a_browser(demo_live, chromium, close_contexts):
    """Click every footer link on the landing page and in the sample app: each lands on a 200 page with content."""
    base, _ = demo_live
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    for start, sel in (("/", "footer a[href^='/']"), ("/demo", "footer.foot a[href^='/']")):
        pg.goto(base + start, wait_until="networkidle")
        hrefs = pg.eval_on_selector_all(sel, "els => els.map(e => e.getAttribute('href'))")
        assert {"/docs", "/privacy", "/terms", "/docs/api"} <= set(hrefs), hrefs
        for h in hrefs:
            pg.goto(base + start, wait_until="networkidle")
            with pg.expect_navigation():
                pg.click(f"{sel.split(' ')[0]} a[href='{h}']")
            resp = pg.evaluate("() => performance.getEntriesByType('navigation')[0].responseStatus")
            assert resp == 200, (start, h, resp)
            assert len(pg.inner_text("body").strip()) > 100, (start, h)
            if h == "/docs/api":
                assert pg.locator("[data-testid=api-endpoint]").count() > 50


# ------------------------------------------------------------------ wide screens and network marks
def test_app_shell_uses_wide_screens(demo_live, chromium, close_contexts):
    base, _ = demo_live
    widths = {}
    for w in (1280, 1920, 2560):
        ctx = chromium.new_context(viewport={"width": w, "height": 1000})
        ctx.add_init_script("try{localStorage.setItem('hq_tour_done','1')}catch(e){}")
        pg = ctx.new_page()
        pg.goto(base + "/demo", wait_until="networkidle")  # the sample opens on Brand > Kit; the Inbox is one click away
        pg.goto(base + "/?tab=inbox", wait_until="networkidle")
        pg.wait_for_selector("#tab-inbox:not([hidden])")
        widths[w] = pg.evaluate("document.getElementById('tab-inbox').getBoundingClientRect().width")
        if w == 1920:  # agent activity and recommendations side by side
            a = pg.evaluate("document.getElementById('ibAgents').getBoundingClientRect().left")
            r = pg.evaluate("document.getElementById('ibRecs').getBoundingClientRect().left")
            assert r > a + 200
        if w == 2560:  # centred, capped at 1680
            box = pg.evaluate("(() => { const t = document.getElementById('tab-inbox').getBoundingClientRect(), p = document.querySelector('.pane').getBoundingClientRect(); return [t.left - p.left, p.right - t.right]; })()")
            assert abs(box[0] - box[1]) < 4
        ctx.close()
    assert widths[1920] > 1400 and widths[2560] <= 1680 and widths[1280] < widths[1920]


def test_planner_and_inbox_show_network_marks(demo_live, chromium, close_contexts):
    base, _ = demo_live
    ctx = chromium.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script("try{localStorage.setItem('hq_tour_done','1')}catch(e){}")
    pg = ctx.new_page()
    pg.goto(base + "/demo", wait_until="networkidle")
    pg.wait_for_selector("[data-testid=inbox-today-item]")
    assert pg.locator("[data-testid=inbox-today-item] .net[data-net]").count() >= 1
    pg.evaluate("showTab('planner')")
    for view in ("week", "month", "list"):
        pg.evaluate(f"plSetView('{view}')")
        pg.wait_for_timeout(300)
        marks = pg.eval_on_selector_all("#plMain .net[data-net]", "els => els.map(e => [e.dataset.net, !!e.querySelector('svg'), e.textContent])")
        assert marks, view
        for net, svg, txt in marks:  # a real simple-icons mark, or LinkedIn's "in" monogram (not in simple-icons)
            assert svg or (net == "linkedin" and txt == "in"), (view, net, txt)
        assert {"twitter", "instagram", "linkedin"} & {m[0] for m in marks}


def test_channel_avatar_shows_with_its_network_mark():
    """chMark() puts the network on the avatar's corner when Buffer gave an https avatar, else just the mark."""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "studio" / "static" / "net-logos.js").read_text()
    assert "chmark-av" in js and "^https:" in js
    data = (Path(__file__).resolve().parents[2] / "studio" / "static" / "net-logos-data.js").read_text()
    for k in ("twitter", "instagram", "facebook", "youtube", "threads", "tiktok", "bluesky", "mastodon", "pinterest"):
        assert f'"{k}":' in data


# ------------------------------------------------------------------ Inbox agent activity per run
def test_ai_calls_of_a_run_fold_into_one_row(app, human):
    store = app.state.store
    uid = store.q("SELECT id FROM users")[0]["id"]
    now = time.time()
    rid = store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                  (uid, "news", "{}", "done", "[]", "{}", now - 60, now))
    tok = activity.begin(internal_app="huhqueue-agent", run_id=rid)
    try:
        for i in range(3):
            activity.set_usage("anthropic", "claude-sonnet-4-5", 1000, 200, "huhqueue claude")
            store.audit(uid, "you", "ai_call", "read", {"for": f"step {i}"})
    finally:
        activity.end(tok)
    rows = human.get("/api/activity?tab=agent&limit=100").json()["items"]
    calls = [r for r in rows if r["action"] == "ai_call"]
    assert len(calls) == 3 and all(r["detail"]["run"] == rid and r["app_label"] == "HuhQueue agent" for r in calls)
    grouped = human.get("/api/activity?tab=agent&limit=100&group=run").json()["items"]
    g = [r for r in grouped if r.get("group") == "run"]
    assert len(g) == 1 and g[0]["n_calls"] == 3 and g[0]["words"] == "made a post" and g[0]["model"] == "claude-sonnet-4-5"
    assert g[0]["app_label"] == "HuhQueue agent" and g[0]["can_open"] and len(g[0]["calls"]) == 3
    assert g[0]["cost_usd"] == pytest.approx(sum(c["cost_usd"] or 0 for c in calls))
    assert not [r for r in grouped if r["action"] == "ai_call"]


def test_older_calls_without_a_run_id_join_the_run_they_happened_in(app, human):
    store = app.state.store
    uid = store.q("SELECT id FROM users")[0]["id"]
    now = time.time()
    store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
            (uid, "news", "{}", "done", "[]", "{}", now - 30, now + 30))
    for _ in range(2):
        store.audit(uid, "you", "ai_call", "read", {"for": "old row"})  # no run id: the old "Sources agent" label
    g = [r for r in human.get("/api/activity?tab=agent&limit=100&group=run").json()["items"] if r.get("group") == "run"]
    assert len(g) == 1 and g[0]["n_calls"] == 2


def test_house_runs_are_attributed_to_the_huhqueue_agent(app):
    """The legacy pipeline ran in a pool thread with no activity context, so its calls read "HuhQueue Sources agent"."""
    house = app.state.house
    seen = {}

    def probe(x):
        seen.update(activity._ctx.get() or {}, x=x)
    house._spawn_run(42, probe, 1)
    deadline = time.time() + 5
    while not seen and time.time() < deadline:
        time.sleep(0.05)
    assert seen.get("internal_app") == "huhqueue-agent" and seen.get("run_id") == 42 and seen.get("x") == 1
