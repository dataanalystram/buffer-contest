"""Make a post v2 + Story scan (owner, 1 Nov 2026): story pictures read server-side (netguard, re-encoded, cached, rate-
limited), the Recent content fields, the Text format, and the page contract (stepper, mode cards, Discover, thread,
pagination). The browser states are screenshotted by scripts/mpv2_shots.py in the ui-smoke workflow."""
import io
import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import netguard, thumbs

from .conftest import connect
from .test_agent_core import TWEET
from .test_makepost_library import owner, upload  # noqa: F401  (fixture)

STATIC = Path(__file__).parents[2] / "studio" / "static"


def _jpeg(w=1200, h=630):
    b = io.BytesIO()
    Image.new("RGB", (w, h), (200, 30, 90)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(autouse=True)
def _clear():
    thumbs._cache.clear()
    thumbs._hits.clear()


def test_thumb_reads_og_image_server_side_and_reencodes(client, monkeypatch):
    connect(client)
    seen = []

    def fake_get(url, **k):
        seen.append(url)
        if url.endswith("/story"):
            return httpx.Response(200, text='<html><head><meta property="og:image" content="/img/hero.jpg"></head></html>',
                                  request=httpx.Request("GET", url))
        return httpx.Response(200, content=_jpeg(), request=httpx.Request("GET", url))
    monkeypatch.setattr(netguard, "safe_get", fake_get)
    r = client.get("/api/thumb", params={"url": "https://news.example/story"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(r.content)).size == (640, 360)
    assert seen == ["https://news.example/story", "https://news.example/img/hero.jpg"]  # relative og:image resolved
    client.get("/api/thumb", params={"url": "https://news.example/story"})
    assert len(seen) == 2  # cached


def test_thumb_without_a_picture_or_blocked_is_204_never_an_error(client, monkeypatch):
    connect(client)

    def blocked(url, **k):
        raise netguard.FetchError("private address")
    monkeypatch.setattr(netguard, "safe_get", blocked)
    r = client.get("/api/thumb", params={"url": "http://169.254.169.254/latest/meta-data"})
    assert r.status_code == 204 and not r.content
    assert client.get("/api/thumb", params={"url": "file:///etc/passwd"}).status_code == 400
    monkeypatch.setattr(netguard, "safe_get", lambda url, **k: httpx.Response(200, content=b"not an image", request=httpx.Request("GET", url)))
    assert client.get("/api/thumb", params={"url": "https://a.example/x", "img": "https://a.example/x.jpg"}).status_code == 204


def test_thumb_needs_a_session_and_is_rate_limited(app, client, monkeypatch):
    anon = TestClient(app, base_url="https://hq.test")
    assert anon.get("/api/thumb", params={"url": "https://a.example/"}).status_code in (401, 403)
    connect(client)
    monkeypatch.setattr(netguard, "safe_get", lambda url, **k: httpx.Response(404, request=httpx.Request("GET", url)))
    monkeypatch.setattr(thumbs, "PER_MIN", 3)
    codes = [client.get("/api/thumb", params={"url": f"https://a.example/{i}"}).status_code for i in range(5)]
    assert codes[:3] == [204, 204, 204] and codes[3] == 429


def test_recent_content_fields_and_text_format(owner):  # noqa: F811
    r = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "format": "text", "channel_ids": ["ch_li", "ch_ig"]}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["status"] == "ready", run["steps"]
    for p in run["result"]["packages"]:
        assert p["format"] == "text" and p["media_urls"] == [] and p["text"]
    up = upload(owner)
    r2 = owner.post("/api/agent/runs", json={"prompt": f"post this {TWEET}", "media_token": up["media_token"], "media_duration": 58.2,
                                             "channel_ids": ["ch_ig"]}).json()
    rows = {x["id"]: x for x in owner.get("/api/agent/runs?limit=5").json()}
    assert rows[r["id"]]["format"] == "text" and rows[r["id"]]["state"] == "ready"
    assert rows[r2["id"]]["format"] == "reel" and rows[r2["id"]]["duration"]
    assert all(set(x) >= {"format", "slides", "duration", "thumb", "state", "legacy"} for x in rows.values())


def test_page_contract_for_v2():
    html = (STATIC / "index.html").read_text()
    for tid in ("mp-stepper", "agent-mode-agent", "agent-mode-quick", "agent-mode-link", "agent-query", "mp-example", "mp-quick-chips",
                "agent-file", "agent-from-library", "agent-format", "mp-theme", "agent-go", "agent-chips", "mp-discover", "run-view",
                "mp-user-msg", "mp-trace", "mp-sources", "mp-status", "packages", "mp-actions", "reply-form", "reply-input", "mp-recent",
                "run-list", "mp-recent-pager", "news-pager", "library-pager"):
        assert f'data-testid="{tid}"' in html, tid
    assert '<option value="text">Text</option>' in html and "Create with agent" in html
    assert 'placeholder="Ask the agent to change something…"' in html
    js = (STATIC / "makepost.js").read_text()
    assert "HQThemes.mount" in js and "hqPager" in js and "Split a long video" in js
    for name in ("makepost.js", "pager.js"):
        for tag in re.findall(r"<(?:button|input|select|textarea)\b[^>]*>", (STATIC / name).read_text()):
            assert "data-testid" in tag, (name, tag)
    bs = (STATIC / "brandstudio.js").read_text()
    assert "sa-thumb" in bs and "sa-why" in bs and "sa-make" in bs and "hqPager" in bs
    css = (STATIC / "makepost.css").read_text()
    assert "prefers-color-scheme:dark" in css and "min-height:44px" in css and "max-width:720px" in css
