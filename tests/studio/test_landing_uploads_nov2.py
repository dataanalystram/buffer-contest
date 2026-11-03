"""Owner asks, 2 Nov 2026 (evening): versioned static URLs so a deploy is seen at once; Claude.ai's sandbox can't reach the
one-time PUT upload URL, so files come in by a cloud-drive share link (import_from_link) or the Send a file page (a short
code + QR, then list_recent_uploads); upload tools say so when the sandbox is blocked; the size limit is shown in the API.
All data here is made up (fake hosts and bytes); no network."""
import base64
import os
import re
import time

import httpx
import pytest

from studio import netguard, send as send_mod, share_import, staticver
from studio.actions import ActionError

from .conftest import agent_token, mcp_call
from .test_e2e_studio import live  # noqa: F401  (the signed-in live server fixture)

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 4000


# ------------------------------------------------------------------ versioned static URLs
def test_landing_links_versioned_assets_and_versioned_urls_are_immutable(client):
    html = client.get("/").text
    css = re.search(r'href="/static/landing\.css\?v=([0-9a-f]{10})"', html)
    assert css and css.group(1) == staticver.version("landing.css")
    assert re.search(r'src="/static/landing\.js\?v=[0-9a-f]{10}"', html)
    assert 'href="/static/brand/apple-touch-icon.png"' in html  # images keep their plain URL
    r = client.get(f"/static/landing.css?v={css.group(1)}")
    assert r.status_code == 200 and r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/static/landing.css?v=0000000000").headers["cache-control"] == "no-cache"  # an old version revalidates
    assert client.get("/static/landing.css").headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_version_follows_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(staticver, "STATIC", tmp_path)
    monkeypatch.setattr(staticver, "_hash", {})
    monkeypatch.setattr(staticver, "_pages", {})
    (tmp_path / "a.css").write_text("a{}")
    (tmp_path / "p.html").write_text('<link rel="stylesheet" href="/static/a.css"><img src="/static/x.png">')
    v1 = staticver.version("a.css")
    assert f"/static/a.css?v={v1}" in staticver.page("p.html") and '/static/x.png"' in staticver.page("p.html")
    (tmp_path / "a.css").write_text("a{color:red}")
    os.utime(tmp_path / "a.css", (time.time() + 5, time.time() + 5))
    v2 = staticver.version("a.css")
    assert v2 != v1 and f"/static/a.css?v={v2}" in staticver.page("p.html")


def test_landing_has_the_integrations_section(client):
    html = client.get("/").text
    assert "Works with the tools you already use" in html and 'data-testid="nav-integrations"' in html
    for g in ("publish", "create", "engines", "mcp"):
        assert f'data-testid="integ-group-{g}"' in html
    assert html.count('class="ig-card"') >= 24


# ------------------------------------------------------------------ share links -> direct download URLs
@pytest.mark.parametrize("url,want", [
    ("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"),
    ("https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"),
    ("https://docs.google.com/uc?export=download&id=1AbCdEfGhIjKlMnOp",
     "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"),
    ("https://www.dropbox.com/scl/fi/abc123/clip.mp4?rlkey=xyz&dl=0", "https://www.dropbox.com/scl/fi/abc123/clip.mp4?rlkey=xyz&dl=1"),
    ("https://www.dropbox.com/s/abc123/clip.mp4?dl=0", "https://www.dropbox.com/s/abc123/clip.mp4?dl=1"),
    ("https://contoso.sharepoint.com/:v:/g/personal/someone/EabcDEF?e=x1",
     "https://contoso.sharepoint.com/:v:/g/personal/someone/EabcDEF?e=x1&download=1"),
])
def test_share_links_become_download_urls(url, want):
    assert share_import.direct(url)[0] == want


def test_onedrive_uses_the_shares_api_and_folders_are_refused():
    u, prov = share_import.direct("https://1drv.ms/v/s!AkL0abc")
    assert prov == "onedrive" and u.startswith("https://api.onedrive.com/v1.0/shares/u!") and u.endswith("/root/content")
    assert "=" not in u.split("u!")[1]
    for folder in ("https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp", "https://www.dropbox.com/sh/abc/xyz?dl=0",
                   "https://www.dropbox.com/scl/fo/abc/xyz?rlkey=1"):
        with pytest.raises(ActionError, match="folder"):
            share_import.direct(folder)
    assert share_import.direct("https://example.com/a.mp4") == ("https://example.com/a.mp4", "")


DRIVE_WARN = """<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title></head><body>
<p>Google Drive can't scan this file for viruses.</p>
<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
<input type="submit" id="uc-download-link" class="goog-inline-block jfk-button jfk-button-action" value="Download anyway"/>
<input type="hidden" name="id" value="1AbCdEfGhIjKlMnOp"><input type="hidden" name="export" value="download">
<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="u-123"></form></body></html>"""


def _drive(seen, warn=True, body=MP4):
    def handler(req: httpx.Request):
        seen.append(str(req.url))
        if req.url.host == "drive.usercontent.google.com" and "uuid" not in req.url.params and warn:
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=DRIVE_WARN)
        if req.url.host == "drive.usercontent.google.com":
            return httpx.Response(200, headers={"content-type": "application/octet-stream",
                                                "content-disposition": 'attachment; filename="launch.mp4"'}, content=body)
        return httpx.Response(404)
    return handler


@pytest.fixture()
def hermetic(monkeypatch):
    monkeypatch.setattr(netguard, "_host_ok", lambda host: True)  # no DNS for the example hosts


def _use(human, handler):
    imp = human.app.state.house.share_import
    imp.client = lambda: httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_drive_large_file_confirm_page_is_followed_into_the_library(human, hermetic):
    seen = []
    _use(human, _drive(seen))
    r = human.post("/api/library/import", json={"url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "video" and d["bytes"] == len(MP4) and d["media_token"] and d["from"] == "drive"
    assert d["name"].startswith("launch")
    assert len(seen) == 2 and "uuid=u-123" in seen[1]
    lib = human.get("/api/library").json()
    assert any(i["source"] == "link" and i["id"] == d["library_id"] for i in lib["items"])


def test_drive_sign_in_page_says_share_it_publicly(human, hermetic):
    page = '<html><head><title>Sign in</title></head><body><a href="https://accounts.google.com/ServiceLogin">Sign in</a></body></html>'
    _use(human, lambda req: httpx.Response(200, headers={"content-type": "text/html"}, text=page))
    r = human.post("/api/library/import", json={"url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"})
    assert r.status_code == 403 and "Anyone with the link" in r.json()["error"]


def test_import_refuses_files_over_the_limit_before_reading(human, hermetic, monkeypatch):
    from studio import library
    monkeypatch.setattr(library, "FILE_MAX", 2000)
    _use(human, lambda req: httpx.Response(200, headers={"content-type": "video/mp4", "content-length": "5000000"}, content=b"x" * 10))
    r = human.post("/api/library/import", json={"url": "https://www.dropbox.com/s/abc/clip.mp4?dl=0"})
    assert r.status_code == 413 and "up to" in r.json()["error"]


def test_import_from_link_over_mcp_and_library_upload_url_routes_share_links(human, hermetic):
    tok = agent_token(human)
    _use(human, _drive([], warn=False))
    res, out = mcp_call(human, tok, "import_from_link", {"url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view"})
    assert not res["isError"], out
    assert out["kind"] == "video" and out["media_token"]
    _use(human, _drive([], warn=False, body=MP4 + b"\x01"))  # another file (the same bytes would dedupe to one item)
    res, out = mcp_call(human, tok, "library_upload_url", {"url": "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOp"})
    assert not res["isError"], out
    assert out["media_token"] and out["from"] == "drive"


# ------------------------------------------------------------------ Send a file page
def test_send_code_page_upload_and_list_recent_uploads(human):
    d = human.post("/api/send-codes").json()
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", d["code"]) and d["url"].endswith("/send/" + d["code"].replace("-", ""))
    assert d["qr"].startswith("data:image/svg+xml;base64,") and d["max_files"] == 10 and d["max_mb"] >= 1
    assert base64.b64decode(d["qr"].split(",", 1)[1]).startswith(b"<svg xmlns=")
    code = d["code"].replace("-", "")
    page = human.get("/send/" + code)
    assert page.status_code == 200 and 'data-testid="send-page"' in page.text and "send.js?v=" in page.text
    assert page.headers["referrer-policy"] == "no-referrer"
    st = human.get(f"/send/{code}/status").json()
    assert st["files_left"] == 10 and st["max_mb"] >= 1
    from fastapi.testclient import TestClient
    other = TestClient(human.app, base_url="https://hq.test")  # another device, no cookie: the code is the credential
    r = other.put(f"/send/{d['code'].lower()}/file?name=launch.mp4", content=MP4)  # case and dash don't matter
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "video" and r.json()["files_left"] == 9
    assert other.get(f"/send/{code}/status").json()["received"][0]["name"].startswith("launch")
    rec = human.get("/api/uploads/recent").json()["items"]
    assert rec and rec[0]["from"] == "Send a file page" and rec[0]["media_token"]
    tok = agent_token(human)
    res, out = mcp_call(human, tok, "list_recent_uploads", {"minutes": 5})
    assert out["items"][0]["from"] == "Send a file page"
    res, out = mcp_call(human, tok, "create_send_code", {})
    assert not res["isError"], out
    assert "/send/" in out["url"] and "list_recent_uploads" in out["next"]


def test_send_rejects_wrong_codes_then_rate_limits(client, monkeypatch):
    monkeypatch.setattr(send_mod, "_wrong", {})
    for _ in range(send_mod.WRONG_MAX):
        assert client.get("/send/ABCDEFGH/status").status_code == 404
    assert client.get("/send/ABCDEFGH/status").status_code == 429


def test_send_code_limits_files_and_rejects_non_media(human, monkeypatch):
    monkeypatch.setattr(send_mod, "MAX_FILES", 1)
    code = human.post("/api/send-codes").json()["code"]
    assert human.put(f"/send/{code}/file?name=a.txt", content=b"just some text, not a video at all").status_code == 415
    assert human.put(f"/send/{code}/file?name=a.mp4", content=MP4).status_code == 200  # the failed one didn't count
    assert human.put(f"/send/{code}/file?name=b.mp4", content=MP4 + b"1").status_code == 409


def test_sample_workspace_cant_make_send_codes(client):
    client.get("/demo", follow_redirects=False)
    csrf = client.get("/api/session").json()["csrf"]
    r = client.post("/api/send-codes", headers={"X-HQ-CSRF": csrf})
    assert r.status_code == 403 and r.json()["error"].endswith("The demo doesn't change anything.")


# ------------------------------------------------------------------ guidance for a blocked sandbox + limits in the API
def test_upload_tools_suggest_the_send_page_and_share_links(human):
    from studio import mcp
    by = {t["name"]: t["description"] for t in mcp.TOOLS}
    for name in ("create_upload", "upload_media_chunk"):
        assert "create_send_code" in by[name] and "import_from_link" in by[name]
    assert {"create_send_code", "list_recent_uploads", "import_from_link"} <= set(by)
    up = human.post("/api/uploads", json={"name": "big.mp4", "size": 27_000_000}).json()
    assert "create_send_code" in up["if_unreachable"] and up["max_mb"] >= 1
    assert up["alternatives"]["send_page"].endswith("/send (create_send_code gives the code)")
    big = human.post("/api/uploads/chunk", json={"index": 0, "data": base64.b64encode(MP4).decode(), "name": "big.mp4",
                                                  "total_size": 27_000_000}).json()
    assert "create_send_code" in big["advice"]
    bad = human.post("/api/uploads/chunk", json={"index": 0, "data": "@@not base64@@"})
    assert bad.status_code == 400 and "create_send_code" in bad.json()["error"] and "import_from_link" in bad.json()["error"]


def test_upload_limits_endpoint(client):
    d = client.get("/api/uploads/limits").json()
    assert d["file_max_mb"] >= 1 and d["chunk_max_mb"] == 2 and d["send_code_files"] == 10 and d["link_import_max_mb"] == d["file_max_mb"]


# ------------------------------------------------------------------ the Send a file page in a browser
@pytest.mark.parametrize("w,h,scheme", [(1440, 900, "light"), (390, 844, "dark")])
def test_send_page_code_qr_and_drop_from_another_device(live, chromium, w, h, scheme):
    base = live[0]
    ctx = chromium.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)
    ctx.add_cookies([{"name": "hq_sid", "value": "sid-e2e", "url": base}])
    ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
    errors = []
    page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/send")
    page.get_by_test_id("send-qr").wait_for()  # shown once the code is made
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", page.get_by_test_id("send-code").inner_text().strip())
    assert page.get_by_test_id("send-qr").evaluate("i => i.complete && i.naturalWidth > 100")
    assert "100 MB" in page.get_by_test_id("send-limit").inner_text()
    code = page.get_by_test_id("send-code").inner_text().replace("-", "")
    phone = chromium.new_context(viewport={"width": w, "height": h}, color_scheme=scheme)  # no cookie: another device
    phone.route(lambda u: not u.startswith(base), lambda r: r.abort())
    p2 = phone.new_page()
    p2.on("pageerror", lambda e: errors.append(str(e)))
    p2.goto(base + "/send")
    p2.get_by_test_id("send-code-input").fill(code.lower())
    p2.get_by_test_id("send-code-go").click()
    p2.get_by_test_id("send-drop").wait_for()
    p2.set_input_files("#sdFile", files=[{"name": "launch.mp4", "mimeType": "video/mp4", "buffer": MP4 + scheme.encode()}])
    p2.wait_for_selector(".sd-list li.ok")
    assert "In your Library" in p2.get_by_test_id("send-list").inner_text()
    assert p2.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
    assert not errors, errors
    phone.close()
    ctx.close()
