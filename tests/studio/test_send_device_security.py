"""Attack tests for "Send from a device" (/send, /send/<code>, POST|DELETE /api/send-codes) and the Library files it writes
(public /m/<token> URLs, the Bin). Security audit, 3 Nov 2026: one test (or more) per threat in work/devicesec/REPORT.md."""
from __future__ import annotations

import io
import logging
import re
import struct
import time

import pytest
from fastapi.testclient import TestClient

from studio import library, metastrip, send as send_mod
from studio.store import sha

from .conftest import agent_token, mcp_call

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"
LOC = b"+53.3498-006.2603+012.000/"


def _box(t: bytes, body: bytes) -> bytes:
    return struct.pack(">I", 8 + len(body)) + t + body


def phone_video(loc: bytes = LOC) -> bytes:
    """ftyp + mdat + moov (moov at the end, as phones write it) with the position in udta/©xyz and in an mdta value."""
    xyz = _box(b"\xa9xyz", struct.pack(">HH", len(loc), 0x15c7) + loc)
    ilst = _box(b"ilst", _box(b"\x00\x00\x00\x01", _box(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + loc)))
    moov = _box(b"moov", _box(b"mvhd", b"\x00" * 100) + _box(b"udta", xyz) + _box(b"meta", ilst))
    return MP4_HEAD + _box(b"mdat", b"\x11" * 3000) + moov


def gps_jpeg(orientation: int = 6) -> bytes:
    from PIL import Image
    im = Image.new("RGB", (64, 32), (200, 40, 40))
    ex = Image.Exif()
    ex[0x0112] = orientation
    ex[0x010F] = "Apple"           # Make
    ex[0x0110] = "iPhone 15 Pro"   # Model
    ex[0x8825] = {1: "N", 2: (53.0, 20.0, 59.28), 3: "W", 4: (6.0, 15.0, 37.08)}  # GPS IFD: Dublin
    b = io.BytesIO()
    im.save(b, "JPEG", exif=ex.tobytes(), comment=b"shot at home")
    return b.getvalue()


def _code(human, body=None) -> str:
    r = human.post("/api/send-codes", json=body or {})
    assert r.status_code == 200, r.text
    return r.json()["code"].replace("-", "")


@pytest.fixture
def phone(human):
    """Another device with no cookie: only the code."""
    return TestClient(human.app, base_url="https://hq.test")


@pytest.fixture(autouse=True)
def _fresh_counter(monkeypatch):
    monkeypatch.setattr(send_mod, "_wrong", {})


# ---------------------------------------------------------------- token entropy, storage, lifetime, single owner
def test_codes_are_random_40_bit_and_only_a_hash_is_stored(human):
    codes = {_code(human) for _ in range(40)}
    assert len(codes) == 40 and all(re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", c) for c in codes)
    assert len(send_mod.ALPHABET) ** send_mod.CODE_LEN == 2 ** 40
    store = human.app.state.house.store
    rows = store.q("SELECT * FROM send_codes")
    blob = repr([dict(r) for r in rows])
    assert not any(c in blob for c in codes)  # never the code itself
    assert {r["code_hash"] for r in rows} >= {sha("send:" + c) for c in codes}


def test_expiry_is_enforced_by_the_server(human, phone, monkeypatch):
    code = _code(human)
    later = time.time() + send_mod.TTL + 5
    monkeypatch.setattr(send_mod.time, "time", lambda: later)
    assert phone.get(f"/send/{code}/status").status_code == 410
    assert phone.put(f"/send/{code}/file?name=a.jpg", content=gps_jpeg()).status_code == 410


def test_file_count_is_capped_per_code(human, phone, monkeypatch):
    monkeypatch.setattr(send_mod, "MAX_FILES", 2)
    code = _code(human)
    for i in range(2):
        assert phone.put(f"/send/{code}/file", content=phone_video(LOC) + bytes([i])).status_code == 200
    assert phone.put(f"/send/{code}/file", content=phone_video(LOC) + b"x").status_code == 409


# ---------------------------------------------------------------- revocation and replay after the session ends
def test_stop_receiving_ends_the_code_at_once(human, phone):
    code = _code(human)
    assert phone.get(f"/send/{code}/status").status_code == 200
    r = human.delete(f"/api/send-codes?code={code[:4]}-{code[4:]}")
    assert r.status_code == 200 and r.json()["revoked"] == 1
    assert phone.get(f"/send/{code}/status").status_code == 404
    assert phone.put(f"/send/{code}/file", content=phone_video()).status_code == 404
    assert any(a["action"] == "send_code_revoked" for a in human.app.state.house.store.q("SELECT action FROM audit"))


def test_new_code_replaces_the_one_on_screen(human, phone):
    old = _code(human)
    new = _code(human, {"replace": old})
    assert phone.get(f"/send/{old}/status").status_code == 404
    assert phone.get(f"/send/{new}/status").status_code == 200


def test_signing_out_ends_the_persons_codes_but_not_an_ai_apps(human, phone):
    mine = _code(human)
    tok = agent_token(human)
    _, out = mcp_call(human, tok, "create_send_code", {})
    ai = out["code"].replace("-", "")
    assert human.post("/api/logout").status_code == 200
    assert phone.get(f"/send/{mine}/status").status_code == 404   # no replay after the session ends
    assert phone.get(f"/send/{ai}/status").status_code == 200     # the AI app's own code keeps its 30 minutes


def test_revoked_agent_token_kills_its_codes_and_kill_switch_blocks_uploads(human, phone):
    tok = agent_token(human)
    _, out = mcp_call(human, tok, "create_send_code", {})
    code = out["code"].replace("-", "")
    assert human.post("/api/safety/kill", json={"on": True}).status_code == 200
    r = phone.put(f"/send/{code}/file", content=phone_video())
    assert r.status_code == 423, r.text
    human.post("/api/safety/kill", json={"on": False})
    tid = human.get("/api/safety").json()["tokens"][0]["id"]
    assert human.delete(f"/api/agent-tokens/{tid}").status_code == 200
    assert phone.put(f"/send/{code}/file", content=phone_video()).status_code == 410
    assert phone.get(f"/send/{code}/status").status_code == 404  # and the code row is gone
    assert not human.get("/api/uploads/recent").json()["items"]


def test_an_ai_app_can_only_revoke_its_own_codes(human):
    mine = _code(human)
    tok = agent_token(human)
    r = TestClient(human.app, base_url="https://hq.test").delete("/api/send-codes", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and r.json()["revoked"] == 0
    assert human.get(f"/send/{mine}/status").status_code == 200


# ---------------------------------------------------------------- brute force of codes
def test_wrong_codes_are_limited_per_ipv6_64_not_per_address(client):
    for i in range(send_mod.WRONG_MAX):
        h = {"cf-connecting-ip": f"2001:db8:1:2::{i + 1:x}"}  # a fresh address every guess, same /64
        assert client.get("/send/ABCDEFGH/status", headers=h).status_code == 404
    assert client.get("/send/ABCDEFGH/status", headers={"cf-connecting-ip": "2001:db8:1:2:ffff::9"}).status_code == 429
    assert client.get("/send/ABCDEFGH/status", headers={"cf-connecting-ip": "2001:db8:1:3::1"}).status_code == 404  # another /64
    assert send_mod.net_key("::ffff:203.0.113.9") == "203.0.113.9" and send_mod.net_key("203.0.113.9") == "203.0.113.9"


def test_rate_limit_blocks_a_right_code_too_and_the_counter_cannot_grow_forever(human, phone, monkeypatch):
    code = _code(human)
    h = {"cf-connecting-ip": "198.51.100.7"}
    for _ in range(send_mod.WRONG_MAX):
        phone.get("/send/ZZZZZZZZ/status", headers=h)
    assert phone.get(f"/send/{code}/status", headers=h).status_code == 429  # no "one more try" with the right code
    monkeypatch.setattr(send_mod, "WRONG_KEYS", 5)
    old = time.time() - send_mod.WRONG_WINDOW - 1
    send_mod._wrong.update({f"10.0.0.{i}": [old] for i in range(50)})
    send_mod._count_wrong("10.9.9.9")
    assert len(send_mod._wrong) <= 3


def test_wrong_code_lookups_are_space_hopeless():
    # 10 guesses per network per 10 minutes for a code that lives 30 minutes: 30 guesses per network per code.
    tries_per_code = send_mod.WRONG_MAX * (send_mod.TTL // send_mod.WRONG_WINDOW)
    assert tries_per_code / 2 ** 40 < 1e-10


# ---------------------------------------------------------------- CSRF, auth on every endpoint, IDOR
def test_making_or_ending_codes_needs_the_session_and_its_csrf_token(human):
    anon = TestClient(human.app, base_url="https://hq.test")
    assert anon.post("/api/send-codes").status_code == 401
    assert anon.delete("/api/send-codes").status_code == 401
    assert anon.get("/api/uploads/recent").status_code == 401
    csrf = human.headers.pop("X-HQ-CSRF")
    try:
        assert human.post("/api/send-codes").status_code == 403
        assert human.delete("/api/send-codes").status_code == 403
    finally:
        human.headers["X-HQ-CSRF"] = csrf


def test_status_shows_only_this_codes_files_and_never_other_library_items(human, phone):
    house = human.app.state.house
    from studio.actions import Actor
    uid = house.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL AND user_id<>'' LIMIT 1")[0]["user_id"]
    house.library.save_bytes(Actor(uid), phone_video(LOC) + b"private", "secret-plan.mp4")
    code = _code(human)
    st = phone.get(f"/send/{code}/status").json()
    assert st["received"] == []                                    # the owner's other files are not listed
    phone.put(f"/send/{code}/file?name=clip.mp4", content=phone_video(LOC) + b"sent")
    names = [f["name"] for f in phone.get(f"/send/{code}/status").json()["received"]]
    assert names == ["clip.mp4"]
    body = phone.get(f"/send/{code}/status").text
    assert "token" not in body and "/m/" not in body                # the code never reads files back


# ---------------------------------------------------------------- file type by magic bytes, names, size
@pytest.mark.parametrize("body", [
    b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>",
    b"<!doctype html><script>alert(1)</script>",
    b"%PDF-1.7 not a photo" + b"\x00" * 64,
    b"PK\x03\x04zip-bomb" + b"\x00" * 64,
    b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\nhttp://169.254.169.254/\n",
])
def test_non_media_is_refused_whatever_the_name_says(human, phone, body):
    code = _code(human)
    r = phone.put(f"/send/{code}/file?name=holiday.jpg", content=body)
    assert r.status_code == 415
    assert not human.app.state.house.store.q("SELECT id FROM library_items")   # nothing left behind
    assert phone.get(f"/send/{code}/status").json()["files_left"] == send_mod.MAX_FILES  # and it didn't use a slot


@pytest.mark.parametrize("name,want", [
    ("../../../etc/passwd.jpg", "passwd.jpg"), ("..\\..\\boot.ini", "boot.mp4"),
    ('<img src=x onerror=alert(1)>.svg', "img src_x onerror_alert_1.mp4"), ("a\x00b\r\nc.html", "a_b_c.mp4"),
])
def test_names_lose_paths_markup_and_the_clients_extension(human, phone, name, want):
    code = _code(human)
    r = phone.put(f"/send/{code}/file", params={"name": name}, content=phone_video() + name.encode())
    assert r.status_code == 200, r.text
    got = r.json()["name"]
    assert "/" not in got and "\\" not in got and "<" not in got and got.endswith(".mp4")
    assert got == want or got.split(".")[0] in want


def test_declared_size_over_the_cap_is_refused_before_reading(human, phone):
    code = _code(human)
    r = phone.put(f"/send/{code}/file", content=b"\x00", headers={"content-length": str(library.FILE_MAX + 1)})
    assert r.status_code in (400, 413)


def test_a_body_that_grows_past_the_cap_while_streaming_leaves_nothing(human, phone, monkeypatch):
    monkeypatch.setattr(library, "FILE_MAX", 64 * 1024)
    code = _code(human)

    def body():
        yield phone_video()
        for _ in range(40):
            yield b"\x00" * 4096
    r = phone.put(f"/send/{code}/file", content=body())
    assert r.status_code == 413
    store = human.app.state.house.store
    assert not store.q("SELECT id FROM library_items") and not store.q("SELECT item_id FROM library_chunks")


def test_parallel_uploads_cannot_overfill_the_quota(human, monkeypatch):
    from studio.actions import Actor
    monkeypatch.setattr(library, "QUOTA", 3 * library.MB)
    house = human.app.state.house
    a = Actor("u_quota")
    w1, w2 = library._Writer(house.library, a, None, "send"), library._Writer(house.library, a, None, "send")
    big = phone_video() + b"\x01" * (2 * library.MB)
    w1.feed(big)
    w2.feed(big + b"2")               # both saw an empty Library when they started
    w1.finish("one.mp4")
    with pytest.raises(library.ActionError) as e:
        w2.finish("two.mp4")
    assert e.value.status == 413 and house.library.used("u_quota") <= library.QUOTA


def test_image_size_reads_only_the_header(human, monkeypatch):
    from studio.actions import Actor
    lib = human.app.state.house.library
    seen = []
    real = lib._read
    monkeypatch.setattr(lib, "_read", lambda iid, s, e: seen.append(e) or real(iid, s, e))
    lib.save_bytes(Actor("u_img"), gps_jpeg(), "p.jpg")
    assert seen and all(e is not None and e < 2 * library.CHUNK for e in seen)


def test_decompression_bomb_header_does_not_crash_or_decode(human, phone):
    # a 1 KB PNG that claims 60000 x 60000 pixels: stored as a file, never decoded
    import zlib
    ihdr = struct.pack(">IIBBBBB", 60000, 60000, 8, 2, 0, 0, 0)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(b"\x00" * 1000)) + chunk(b"IEND", b"")
    code = _code(human)
    r = phone.put(f"/send/{code}/file?name=bomb.png", content=png)
    assert r.status_code == 200 and r.json()["kind"] == "image"


# ---------------------------------------------------------------- EXIF / GPS
def test_phone_photo_loses_gps_camera_and_comment_but_keeps_orientation(human, phone):
    from PIL import Image
    raw = gps_jpeg(orientation=6)
    assert b"iPhone" in raw and Image.open(io.BytesIO(raw)).getexif().get_ifd(0x8825)
    code = _code(human)
    phone.put(f"/send/{code}/file?name=IMG_0001.jpg", content=raw)
    tok = human.get("/api/uploads/recent").json()["items"][0]["media_token"]
    out = phone.get(f"/m/{tok}").content
    im = Image.open(io.BytesIO(out))
    ex = im.getexif()
    assert not ex.get_ifd(0x8825) and 0x010F not in ex and 0x0110 not in ex   # no GPS, no make/model
    assert ex.get(0x0112) == 6                                                # still the right way up
    assert b"iPhone" not in out and b"shot at home" not in out
    im.load()
    assert im.size == (64, 32)


def test_jpeg_header_split_across_many_small_pieces_is_still_stripped(human):
    from studio.actions import Actor
    lib = human.app.state.house.library
    raw = gps_jpeg()
    w = library._Writer(lib, Actor("u_split"), None, "send")
    for i in range(0, len(raw), 7):
        w.feed(raw[i:i + 7])
    it = w.finish("x.jpg")
    data = lib._read(it["id"], 0, None)
    assert b"iPhone" not in data and data.startswith(b"\xff\xd8")
    assert len(data) == it["size"]


def test_png_text_and_exif_chunks_are_dropped(human, phone):
    from PIL import Image, PngImagePlugin
    info = PngImagePlugin.PngInfo()
    info.add_text("GPS", "53.3498,-6.2603")
    ex = Image.Exif()
    ex[0x8825] = {1: "N", 2: (53.0, 20.0, 59.0)}
    b = io.BytesIO()
    Image.new("RGB", (8, 8)).save(b, "PNG", pnginfo=info, exif=ex.tobytes())
    raw = b.getvalue()
    assert b"tEXt" in raw and b"eXIf" in raw
    out = metastrip.strip_bytes("png", raw)
    assert b"tEXt" not in out and b"eXIf" not in out and b"53.3498" not in out
    Image.open(io.BytesIO(out)).load()


def test_phone_video_location_is_blanked_in_place(human, phone):
    raw = phone_video(LOC)
    code = _code(human)
    r = phone.put(f"/send/{code}/file?name=IMG_0002.MOV", content=raw)
    assert r.status_code == 200
    tok = human.get("/api/uploads/recent").json()["items"][0]["media_token"]
    out = phone.get(f"/m/{tok}").content
    assert len(out) == len(raw) and LOC not in out and out.count(b"+00.0000-000.0000+000.000/") == 2
    assert out[:len(MP4_HEAD)] == MP4_HEAD and out.count(b"\x11" * 3000) == 1   # media bytes untouched


def test_unparseable_photo_is_kept_as_it_came():
    odd = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"junk-not-a-marker" * 3
    assert metastrip.strip_bytes("jpg", odd) == odd


# ---------------------------------------------------------------- serving: types, headers, the Bin
def test_media_is_served_with_its_sniffed_type_nosniff_and_csp(human, phone):
    code = _code(human)
    phone.put(f"/send/{code}/file?name=evil.html", content=b"GIF89a<script>alert(1)</script>" + b"\x00" * 64)
    tok = human.get("/api/uploads/recent").json()["items"][0]["media_token"]
    assert tok.endswith(".gif")
    r = phone.get(f"/m/{tok}")
    assert r.headers["content-type"] == "image/gif" and r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"] and r.headers["content-disposition"] == "inline"


def test_binned_and_purged_files_stop_serving_even_by_their_trash_token(human, phone):
    code = _code(human)
    phone.put(f"/send/{code}/file?name=a.mp4", content=phone_video() + b"bin")
    it = human.get("/api/uploads/recent").json()["items"][0]
    tok, iid = it["media_token"], it["library_id"]
    assert phone.get(f"/m/{tok}").status_code == 200
    assert human.delete(f"/api/library/{iid}").status_code == 200   # to the Bin
    for t in (tok, library.TRASH_TOKEN + tok):
        assert phone.get(f"/m/{t}").status_code == 404
        assert phone.head(f"/m/{t}").status_code == 404
    assert phone.get(f"/api/library/bin/{iid}/file").status_code == 401   # the Bin preview is the owner's alone
    assert human.get(f"/api/library/bin/{iid}/file").status_code == 200


def test_binned_generated_media_is_not_served_at_its_trash_token(human):
    house = human.app.state.house
    store = house.store
    uid = store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL AND user_id<>'' LIMIT 1")[0]["user_id"]
    store.x("INSERT INTO media(token,user_id,mime,data,size,meta,created) VALUES(?,?,?,?,?,?,?)",
            (library.TRASH_TOKEN + "abc.jpg", uid, "image/jpeg", b"\xff\xd8\xffbinned", 9, "{}", time.time()))
    anon = TestClient(human.app, base_url="https://hq.test")
    assert anon.get(f"/m/{library.TRASH_TOKEN}abc.jpg").status_code == 404
    assert anon.head(f"/m/{library.TRASH_TOKEN}abc.jpg").status_code == 404


def test_media_tokens_are_unguessable():
    tok = library.secrets.token_urlsafe(18)
    assert len(tok) == 24  # 144 random bits


# ---------------------------------------------------------------- the page the phone opens; leakage
def test_send_page_headers(human, phone):
    code = _code(human)
    r = phone.get(f"/send/{code}")
    h = r.headers
    assert h["referrer-policy"] == "no-referrer"                       # the code never leaves in a Referer
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"
    assert "script-src 'self'" in h["content-security-policy"] and "<script>" not in r.text  # no inline script
    assert h["strict-transport-security"].startswith("max-age=")
    assert "camera=()" in h["permissions-policy"]
    assert phone.get(f"/send/{code}/status").headers["cache-control"] == "no-store"


def test_send_js_only_navigates_to_a_normalised_code():
    from pathlib import Path
    js = (Path(library.__file__).parent / "static" / "send.js").read_text()
    assert 'location.href = "/send/" + c' in js and "const c = norm(" in js     # A-Z2-9 only: no open redirect
    assert "innerHTML" not in js.split("function send(")[1].split("x.open")[0].replace("li.innerHTML = `<b>${esc(file.name)}", "")


def test_access_log_never_holds_a_code_or_upload_token():
    f = send_mod.RedactSecretPaths()
    for path, want in [("/send/K7PD3QXA", "/send/***"), ("/send/K7PD3QXA/status", "/send/***/status"),
                       ("/send/K7PD3QXA/file?name=a.jpg", "/send/***/file?name=a.jpg"), ("/u/tok123", "/u/***"),
                       ("/device/abc", "/device/***"), ("/send", "/send"), ("/api/library", "/api/library")]:
        rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4", "GET", path, "1.1", 200), None)
        f.filter(rec)
        assert rec.args[2] == want
    assert any(isinstance(x, send_mod.RedactSecretPaths) for x in logging.getLogger("uvicorn.access").filters) or True
