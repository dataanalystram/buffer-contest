"""View transitions (5 Nov 2026), the static side of tests/studio/test_view_loader_e2e.py: the helper loads before app.js,
the loader has a reduced-motion state and no gradient, every screen change goes through it, and the idea is credited."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ST = ROOT / "studio" / "static"


def test_viewswap_loads_before_the_app_and_admin():
    html = (ST / "index.html").read_text()
    assert html.index("/static/loading.js") < html.index("/static/viewswap.js") < html.index("/static/app.js")
    assert "/static/viewswap.css" in html
    adm = (ROOT / "studio" / "admin_ui" / "index.html").read_text()
    assert adm.index("/static/viewswap.js") < adm.index("/admin/admin.js") and "/static/viewswap.css" in adm


def test_loader_css_is_flat_and_still_under_reduced_motion():
    css = (ST / "viewswap.css").read_text()
    assert "gradient(" not in css.lower()
    assert "@media (prefers-reduced-motion:reduce)" in css and "@media (prefers-reduced-motion:no-preference)" in css
    # every animation is declared only inside the no-preference block
    outside = re.sub(r"@media \(prefers-reduced-motion:no-preference\)\{.*?\n\}", "", css, flags=re.S)
    assert "animation:" not in outside


def test_every_screen_change_goes_through_the_helper():
    app = (ST / "app.js").read_text()
    assert "HQView.swap(el, [subWork, () => tabLoader(name)()]" in app        # rail tabs, Back / Forward, deep links
    assert "HQView.swap(pane, [subTab.work]" in app                           # sub-tabs
    assert 'showTab("agent", "agent");\n    try { const run = await api' not in app  # Inbox no longer draws Make a post first
    js = (ST / "viewswap.js").read_text()
    assert "new AbortController()" in js and "ctl.abort()" in js and "MIN_MS = 150" in js and "SLOW_MS = 300" in js
    fail = js.split("function fail")[1].split("function begin")[0]
    assert re.findall(r"\.innerHTML = [^;]+;", fail) == ['.innerHTML = "";'] and "p.textContent =" in fail  # error text never HTML


def test_borrowed_idea_is_credited():
    notice = (ROOT / "NOTICE").read_text()
    assert "logo-trace-loader" in notice and "viewswap.js" in notice
