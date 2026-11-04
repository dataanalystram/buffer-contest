"""Unsplash picker: needs a key, searches, imports with credit + download tracking, key lives in App keys."""
import io

import httpx
import pytest
import respx
from PIL import Image


@pytest.fixture(autouse=True)
def _unpinned_outbound(monkeypatch):
    """These tests mock outbound calls by host name (respx). netguard pins each vetted request to its resolved IP, which
    respx would see instead, so here DNS answers one public address and requests keep their host name; pinning itself is
    covered in tests/test_outbound_ssrf.py."""
    from huhqueue import netguard
    monkeypatch.setattr(netguard, "_getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(netguard, "_pinnable", lambda c, url: False)

PHOTO = {"id": "abc123", "width": 4000, "height": 3000, "color": "#333", "alt_description": "a rack of servers",
         "urls": {"raw": "https://images.unsplash.com/photo-1?ixid=x", "small": "https://images.unsplash.com/photo-1?w=400"},
         "links": {"html": "https://unsplash.com/photos/abc123", "download_location": "https://api.unsplash.com/photos/abc123/download"},
         "user": {"name": "Ada Lens", "links": {"html": "https://unsplash.com/@ada"}}}


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


def test_needs_a_key(api):
    r = api.get("/api/media/unsplash/search?q=servers")
    assert r.status_code == 400 and "App keys" in r.json()["detail"]["message"]
    keys = api.get("/api/app-keys").json()
    assert keys["unsplash"]["integration"] and not keys["unsplash"]["configured"] and keys["unsplash"]["redirect_uri"] is None


def test_search_and_import(api, core):
    out = api.put("/api/app-keys/unsplash", {"unsplash_access_key": "unsplash-key-123456"}).json()
    assert out["configured"]
    with respx.mock:
        s = respx.get("https://api.unsplash.com/search/photos").mock(return_value=httpx.Response(200, json={"total": 1, "total_pages": 1, "results": [PHOTO]}))
        r = api.get("/api/media/unsplash/search?q=servers&orientation=landscape").json()
        assert s.calls[0].request.headers["Authorization"] == "Client-ID unsplash-key-123456"
        assert s.calls[0].request.url.params["orientation"] == "landscape"
        assert r["results"][0]["credit"] == "Photo by Ada Lens on Unsplash" and "utm_source=huhqueue" in r["results"][0]["photographer_url"]
        respx.get("https://api.unsplash.com/photos/abc123").mock(return_value=httpx.Response(200, json=PHOTO))
        dl = respx.get("https://api.unsplash.com/photos/abc123/download").mock(return_value=httpx.Response(200, json={}))
        img = respx.get(url__startswith="https://images.unsplash.com/photo-1").mock(
            return_value=httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"}))
        res = api.post("/api/media/unsplash/import", {"id": "abc123"}).json()
        assert dl.called and "w=2160" in str(img.calls[0].request.url)
    m = res["media"]
    assert m["filename"] == "unsplash-abc123.jpg" and m["alt_text"] == "a rack of servers"
    assert m["title"] == "Photo by Ada Lens on Unsplash" and "unsplash" in m["tags"]


def test_rate_limit_message(api):
    api.put("/api/app-keys/unsplash", {"unsplash_access_key": "unsplash-key-123456"})
    with respx.mock:
        respx.get("https://api.unsplash.com/search/photos").mock(return_value=httpx.Response(403))
        r = api.get("/api/media/unsplash/search?q=x")
    assert r.status_code == 400 and "rate limit" in r.json()["detail"]["message"]


def test_download_ping_is_vetted_and_never_follows_redirects(api, core, monkeypatch):
    """CodeRabbit (4 Nov 2026): the download_location ping only checked the URL text and followed redirects with the
    Client-ID attached. It now goes through netguard: a private DNS answer is refused and a redirect is not followed.
    The import itself still succeeds (the ping is best effort)."""
    from huhqueue import netguard
    public = [(2, 1, 6, "", ("93.184.216.34", 0))]
    api.put("/api/app-keys/unsplash", {"unsplash_access_key": "unsplash-key-123456"})
    for private_dns in (True, False):
        monkeypatch.setattr(netguard, "_getaddrinfo", lambda h, *a, _p=private_dns, **k:
                            [(2, 1, 6, "", ("10.0.0.5", 0))] if _p and h == "api.unsplash.com" else public)
        with respx.mock:
            respx.get("https://api.unsplash.com/photos/abc123").mock(return_value=httpx.Response(200, json=PHOTO))
            dl = respx.get("https://api.unsplash.com/photos/abc123/download").mock(
                return_value=httpx.Response(302, headers={"location": "https://internal.example/steal"}))
            hop = respx.get("https://internal.example/steal").mock(return_value=httpx.Response(200, json={}))
            respx.get(url__startswith="https://images.unsplash.com/photo-1").mock(
                return_value=httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"}))
            r = api.post("/api/media/unsplash/import", {"id": "abc123"})
            assert r.status_code == 200, r.text
            assert dl.called is (not private_dns) and not hop.called
