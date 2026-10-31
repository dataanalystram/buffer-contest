"""Premium UX contract (31 Oct 2026): the landing for visitors and the app for a connected browser at "/", crawl files,
docs, the legal pages, the brand assets and the CSP stay strict (no inline or third-party scripts, self-hosted fonts)."""
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from studio import docs

STATIC = Path(__file__).parents[2] / "studio" / "static"



def test_root_serves_landing_then_app(app):
    from fastapi.testclient import TestClient
    from .conftest import connect
    c = TestClient(app, base_url="https://hq.test")
    home = c.get("/").text
    assert 'data-testid="landing"' in home and "Paste one link." in home and 'data-testid="nav-connect"' in home
    assert home.count("<h1") == 1 and "application/ld+json" in home and 'property="og:image"' in home
    assert c.get("/app", follow_redirects=False).status_code == 302
    connect(c)
    app_html = c.get("/").text
    assert 'id="app"' in app_html and 'data-testid="landing"' not in app_html and "noindex" in app_html


def test_crawl_files(client):
    r = client.get("/robots.txt").text
    assert "Disallow: /api/" in r and "Sitemap:" in r
    sm = client.get("/sitemap.xml").text
    for u in ("/docs/planner", "/privacy", "/terms", "/security"):
        assert u + "</loc>" in sm
    assert client.get("/og.png").headers["content-type"] == "image/png"
    assert client.get("/favicon.ico").status_code == 200


def test_docs_pages_search_and_shots(client):
    assert client.get("/docs").status_code == 200
    for slug in docs.slugs()[1:]:
        h = client.get(f"/docs/{slug}").text
        assert 'data-testid="docs-nav"' in h and 'data-testid="docs-search"' in h and 'href="/demo"' in h
        for shot in re.findall(r'/static/docs-shots/([\w-]+\.webp)', h):
            assert (STATIC / "docs-shots" / shot).is_file(), shot
    assert client.get("/docs/nope").status_code == 404
    idx = client.get("/docs/search.json").json()
    assert any(e["url"].startswith("/docs/planner") for e in idx)


def test_legal_pages_have_the_parts(client):
    p = client.get("/privacy").text
    for part in ("privacy-summary", 'id="data"', 'id="retention"', 'id="processors"', 'id="rights"', 'id="contact"', "Last updated"):
        assert part in p, part
    assert client.get("/terms").status_code == 200 and client.get("/security").status_code == 200


def test_no_inline_or_third_party_scripts(client):
    for path in ("/", "/docs", "/docs/agents", "/privacy", "/terms", "/security"):
        h = client.get(path).text
        for tag in re.findall(r"<script\b[^>]*>", h):
            assert 'type="application/ld+json"' in tag or re.search(r'src="/static/[\w.-]+\.js"', tag), (path, tag)
        assert "fonts.googleapis.com" not in h and " on" + "click=" not in h and "onsubmit=" not in h
    csp = client.get("/").headers["content-security-policy"]
    assert "font-src 'self';" in csp and "googleapis" not in csp and "script-src 'self';" in csp


def test_static_nested_and_traversal(client):
    assert client.get("/static/brand/hb-mark-96.png").status_code == 200
    assert client.get("/static/fonts/AtkinsonHyperlegibleNext-latin.woff2").status_code == 200
    assert client.get("/static/brand/src/huhbyte-logo.png").status_code == 404
    assert client.get("/static/../app.py").status_code == 404 and client.get("/static/..%2Fapp.py").status_code == 404


def test_demo_carries_the_hero_link(client):
    r = client.get("/demo?url=theverge.com/tech/1", follow_redirects=False)
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["demo"] == ["1"] and q["url"] == ["https://theverge.com/tech/1"]
    r = client.get("/demo?url=javascript:alert(1)", follow_redirects=False)
    assert "url=" not in r.headers["location"]
