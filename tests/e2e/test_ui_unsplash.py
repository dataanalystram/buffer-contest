"""End-to-end: Unsplash picker from the composer (Unsplash API mocked at the server's HTTP client)."""
import io

import httpx
from PIL import Image

from .conftest import PASSWORD, T, api, goto, toast


def _jpeg():
    b = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(b, "JPEG")
    return b.getvalue()


PHOTO = {"id": "abc123", "width": 400, "height": 300, "color": "#333", "alt_description": "servers",
         "urls": {"raw": "https://images.unsplash.com/photo-1?ixid=x", "small": "/static/logo.png"},
         "links": {"html": "https://unsplash.com/photos/abc123", "download_location": "https://api.unsplash.com/photos/abc123/download"},
         "user": {"name": "Ada Lens", "links": {"html": "https://unsplash.com/@ada"}}}


def handler(request):
    u = str(request.url)
    if "/search/photos" in u:
        return httpx.Response(200, json={"total": 1, "total_pages": 1, "results": [PHOTO]})
    if u.endswith("/download"):
        return httpx.Response(200, json={})
    if "/photos/abc123" in u:
        return httpx.Response(200, json=PHOTO)
    if "images.unsplash.com" in u:
        return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})
    return httpx.Response(404)


def test_00_login(page, server):
    server["core"].http = httpx.Client(transport=httpx.MockTransport(handler))
    page.goto(server["base"] + "/")
    T(page, "login-password").fill(PASSWORD)
    T(page, "login-password2").fill(PASSWORD)
    T(page, "login-submit").click()
    T(page, "page-home").wait_for()
    api(page, "POST", "/api/connect/fake/token", {"access_token": "tok-aaaa", "username": "huhbyte_demo"})


def test_01_missing_key_points_to_app_keys(page, server):
    goto(page, server, "/library")
    T(page, "library-unsplash").click()
    T(page, "unsplash-q").fill("servers")
    T(page, "unsplash-search").click()
    T(page, "unsplash-error").wait_for()
    assert T(page, "unsplash-keys-link").is_visible()
    T(page, "unsplash-close").click()


def test_02_pick_into_composer(page, server):
    api(page, "PUT", "/api/app-keys/unsplash", {"unsplash_access_key": "unsplash-key-123456"})
    goto(page, server, "/home?compose=new")
    T(page, "composer-unsplash").click()
    T(page, "unsplash-q").fill("servers")
    T(page, "unsplash-search").click()
    T(page, "unsplash-photo").first.click()
    toast(page, "Photo by Ada Lens")
    T(page, "composer-media-item").first.wait_for()
    assert any(m["title"] == "Photo by Ada Lens on Unsplash" for m in api(page, "GET", "/api/media"))
    assert not page.errors, page.errors
