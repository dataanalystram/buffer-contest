"""Cut memory and bandwidth (5 Nov 2026): Render killed the service for exceeding 512 MB (08:10 and 11:31 GMT) and then
suspended it for "Monthly Quota Exceeded" (the Hobby workspace's 5 GB of outbound bandwidth: 5.44 GB of HTTP answers
and 0.61 GB service-initiated by 5 Nov). Each test fails on main and passes here.

- /m/<token> and a rendered Library file read only the asked range (SQL substr, or a ranged GET from R2) and stream a
  whole answer in 1 MB pieces: no query ever pulls a media row's whole ``data`` column into memory.
- Media answers carry a strong ETag; If-None-Match answers 304 with no body.
- Text answers (HTML, JS, CSS, JSON) are gzipped; media, Range answers and HEAD never are.
- A poll in a hidden tab waits at least 30 s and catches up when the tab is shown again.
"""
import gzip
import io
import re
import time
from pathlib import Path

import pytest

from studio import blobstore
from studio.library import CHUNK

ROOT = Path(__file__).resolve().parents[2]
ST = ROOT / "studio" / "static"
TOK = "cmbVideo0123456789abcdef.mp4"


def _blob(n: int) -> bytes:
    return bytes((i * 7 + 3) % 251 for i in range(n))


def _put(store, token: str, data: bytes | None, size, key=None):
    store.x("INSERT INTO media(token,user_id,name,mime,data,size,meta,created) VALUES(?,?,?,?,?,?,?,?)",
            (token, "u-cmb", "v.mp4", "video/mp4", data, size, "{}", time.time()))
    if key:
        store.x("UPDATE media SET storage_key=? WHERE token=?", (key, token))


@pytest.fixture
def spy(app, monkeypatch):
    """Every SQL statement run, and a failure on any read of a media row's whole bytes."""
    store = app.state.house.store
    seen: list[str] = []
    orig = store.q

    def q(sql, args=()):
        seen.append(sql)
        low = " ".join(sql.lower().split())
        if " from media" in low and re.search(r"\bdata\b", low.split(" from ")[0].replace("substr(data", "").replace("length(data)", "")):
            raise AssertionError(f"a media row's whole bytes were read: {sql}")
        return orig(sql, args)
    monkeypatch.setattr(store, "q", q)
    return seen


def test_m_range_reads_only_the_range_and_a_whole_answer_streams(app, client, spy):
    data = _blob(3 * CHUNK + 1234)
    _put(app.state.house.store, TOK, data, len(data))
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == data[10:20] and r.headers["content-range"] == f"bytes 10-19/{len(data)}"
    r = client.get(f"/m/{TOK}", headers={"Range": f"bytes={CHUNK - 5}-{CHUNK + 4}"})
    assert r.status_code == 206 and r.content == data[CHUNK - 5:CHUNK + 5]
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=-7"})
    assert r.status_code == 206 and r.content == data[-7:]
    r = client.get(f"/m/{TOK}")
    assert r.status_code == 200 and r.content == data and r.headers["content-length"] == str(len(data))
    assert "content-encoding" not in r.headers  # video is never gzipped


def test_m_range_answer_is_capped_at_8_mb(app, client, spy):
    data = _blob(9 * CHUNK)
    _put(app.state.house.store, TOK, data, len(data))
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=0-"})
    assert r.status_code == 206 and len(r.content) == 8 * CHUNK and r.headers["content-range"] == f"bytes 0-{8 * CHUNK - 1}/{len(data)}"


def test_head_without_a_stored_size_never_loads_the_bytes(app, client, spy):
    data = _blob(2 * CHUNK)
    _put(app.state.house.store, TOK, data, None)
    r = client.head(f"/m/{TOK}")
    assert r.status_code == 200 and r.headers["content-length"] == str(len(data)) and r.headers.get("etag")
    assert app.state.house.media_head(TOK) == {"mime": "video/mp4", "size": len(data)}


@pytest.mark.parametrize("cache_mb", ["1024", "0"])
def test_r2_objects_are_read_by_range_in_1_mb_pieces(app, client, monkeypatch, cache_mb):
    # with the disk cache on (Egress guard) the first request fills the copy in 1 MB ranged pieces and later ones read
    # nothing from storage; with it off, each request reads only its range. Never a whole-object read either way.
    monkeypatch.setenv("HQ_MEDIA_CACHE_MB", cache_mb)
    data = _blob(2 * CHUNK + 99)
    _put(app.state.house.store, TOK, None, len(data), key="media/owner/obj1")
    calls = []

    def fake_read(d, key, start=None, end=None):
        calls.append((key, start, end))
        assert key == "media/owner/obj1" and start is not None and end is not None, "a whole-object read"
        return data[start:end + 1]
    monkeypatch.setattr(blobstore, "read", fake_read)
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=5-9"})
    assert r.status_code == 206 and r.content == data[5:10]
    assert all(e - s + 1 <= CHUNK for _, s, e in calls)
    if cache_mb == "0":
        assert calls == [("media/owner/obj1", 5, 9)]
    else:
        assert len(calls) == 3
    calls.clear()
    r = client.get(f"/m/{TOK}")
    assert r.content == data and all(e - s + 1 <= CHUNK for _, s, e in calls)
    assert len(calls) == (3 if cache_mb == "0" else 0)


def test_m_etag_and_if_none_match_answer_304(app, client):
    data = _blob(4096)
    _put(app.state.house.store, TOK, data, len(data))
    r = client.get(f"/m/{TOK}")
    tag = r.headers.get("etag")
    assert tag and tag.startswith('"') and "max-age" in r.headers["cache-control"]
    r2 = client.get(f"/m/{TOK}", headers={"If-None-Match": tag})
    assert r2.status_code == 304 and r2.content == b"" and r2.headers["etag"] == tag
    assert client.get(f"/m/{TOK}", headers={"If-None-Match": f'"nope", W/{tag}'}).status_code == 304
    assert client.get(f"/m/{TOK}", headers={"If-None-Match": '"other"'}).status_code == 200
    assert client.head(f"/m/{TOK}").headers["etag"] == tag


def test_binned_media_token_stays_hidden(app, client):
    from studio.library import TRASH_TOKEN
    _put(app.state.house.store, TRASH_TOKEN + TOK, _blob(100), 100)
    assert client.get(f"/m/{TRASH_TOKEN}{TOK}").status_code == 404
    assert client.head(f"/m/{TRASH_TOKEN}{TOK}").status_code == 404
    assert client.get("/m/missing-token.mp4").status_code == 404


def test_a_rendered_library_file_reads_by_range(app, spy):
    data = _blob(2 * CHUNK + 5)
    _put(app.state.house.store, TOK, data, len(data))
    lib = app.state.house.library
    f = lib._file_of({"storage": "media", "meta": {"tokens": [TOK]}, "token": TOK, "name": "Reel.mp4", "mime": "video/mp4"})
    assert f["size"] == len(data) and f["read"](CHUNK, CHUNK + 3) == data[CHUNK:CHUNK + 4] and f["etag"]
    assert b"".join(f["iter"]()) == data


def _png() -> bytes:
    from PIL import Image
    b = io.BytesIO()
    Image.new("RGB", (64, 64), (201, 172, 250)).save(b, "PNG")
    return b.getvalue()


def test_library_file_etag_304(human):
    up = human.post("/api/library/upload?name=a.png", content=_png(), headers={"Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    iid = up.json()["id"]
    r = human.get(f"/api/library/{iid}/file")
    tag = r.headers.get("etag")
    assert r.status_code == 200 and tag
    r2 = human.get(f"/api/library/{iid}/file", headers={"If-None-Match": tag})
    assert r2.status_code == 304 and r2.content == b""


def test_text_answers_are_gzipped_media_and_ranges_are_not(app, client):
    js = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert js.status_code == 200 and js.headers.get("content-encoding") == "gzip" and "accept-encoding" in js.headers.get("vary", "").lower()
    raw = client.get("/static/app.js", headers={"Accept-Encoding": "identity"})
    assert raw.headers.get("content-encoding") is None and raw.content == js.content  # httpx decodes: same bytes
    assert int(js.headers["content-length"]) < len(raw.content) / 2
    if js.headers.get("etag"):
        assert js.headers["etag"].startswith("W/")
    data = _blob(4096)
    _put(app.state.house.store, TOK, data, len(data))
    for h in ({"Accept-Encoding": "gzip"}, {"Accept-Encoding": "gzip", "Range": "bytes=0-2047"}):
        r = client.get(f"/m/{TOK}", headers=h)
        assert "content-encoding" not in r.headers
    assert client.head("/static/app.js", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") is None


def test_gzip_middleware_rules():
    from studio.gzipmw import accepts_gzip, compressible
    assert compressible("application/json") and compressible("text/html; charset=utf-8") and compressible("image/svg+xml")
    assert not compressible("video/mp4") and not compressible("image/png") and not compressible("text/event-stream")
    sc = lambda v: {"type": "http", "headers": [(b"accept-encoding", v.encode())]}  # noqa: E731
    assert accepts_gzip(sc("br, gzip")) and accepts_gzip(sc("*")) and not accepts_gzip(sc("gzip;q=0")) and not accepts_gzip(sc("br"))
    assert not accepts_gzip(sc("*;q=1, gzip;q=0.000")) and accepts_gzip(sc("gzip;q=0.5, *;q=0")) and not accepts_gzip(sc("*;q=0"))
    assert accepts_gzip(sc("deflate, gzip;q=1.0")) and not accepts_gzip(sc("gzip;q=bad"))
    assert gzip.decompress(gzip.compress(b"x", mtime=0)) == b"x"


def test_hidden_tabs_poll_slowly_and_catch_up_when_shown():
    ld = (ST / "loading.js").read_text()
    assert "const HIDDEN_MS = 30000;" in ld and "document.hidden ? Math.max(ms, HIDDEN_MS) : ms" in ld
    assert 'addEventListener("visibilitychange", on)' in ld and "pollMs, wake" in ld
    for name, key in (("house.js", "H"), ("take.js", "T")):
        js = (ST / name).read_text()
        assert f"{key}.poll = setTimeout(tick, d);" in js and "HQLoad.pollMs(ms)" in js and "HQLoad.wake(" in js
        assert "later(1400)" in js or "later(2000)" in js
        assert not re.search(rf"{key}\.poll = setTimeout\(tick, (1400|2000)\)", js)
        # a replaced watcher (even for the same id) drops its wake listener and its loop (CodeRabbit, #118)
        assert f"if ({key}.unwake) {key}.unwake();" in js and f"const gen = {key}.gen = ({key}.gen || 0) + 1" in js
        assert f"{key}.watching === id && {key}.gen === gen" in js
    assert "if (!mine()) return;  // a newer watcher owns the poll now" in (ST / "house.js").read_text()


def test_inbox_etag_matches_a_weakened_tag_from_a_gzipped_answer(human):
    r = human.get("/api/inbox")
    tag = r.headers.get("etag")
    assert r.status_code == 200 and tag
    strong = tag.removeprefix("W/")
    weak = human.get("/api/inbox", headers={"If-None-Match": "W/" + strong})
    assert weak.status_code == 304 and weak.headers["etag"] == "W/" + strong  # the validator the cache holds
    same = human.get("/api/inbox", headers={"If-None-Match": strong})
    assert same.status_code == 304 and same.headers["etag"] == strong


def test_inbox_etag_ignores_the_read_time(human, monkeypatch):
    # as_of moves every second; a poll that crosses a second boundary must still get its 304 (flaked on Postgres CI)
    from studio import inbox
    real, ticks = inbox.build, iter(range(60))
    monkeypatch.setattr(inbox, "build", lambda *a, **k: {**real(*a, **k), "as_of": f"2026-11-06T14:00:{next(ticks):02d}Z"})
    first = human.get("/api/inbox")
    tag = first.headers["etag"].removeprefix("W/")
    again = human.get("/api/inbox", headers={"If-None-Match": tag})
    assert again.status_code == 304 and again.headers["etag"] == tag
