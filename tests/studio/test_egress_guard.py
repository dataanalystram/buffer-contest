"""Egress guard (5 Nov 2026): Supabase's free plan reported 30 GB of egress against 5 GB while Render sent ~6 GB, because
media bytes left Postgres on every request (a Range answer used to read the whole BYTEA; renders and Buffer pushes read
whole files again each time). Each media file now leaves the database at most once per instance (a disk cache under
every ownership check), media answers are private + immutable with ETag/If-Range, a meter counts the bytes with a
daily budget and an 80% warning, and one client repeating the same download is slowed. Most tests fail on main.
"""
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import egress, mediacache
from studio.library import CHUNK, SESSION_COOKIE, record_owner_deletes

ROOT = Path(__file__).resolve().parents[2]
TOK = "egrVideo0123456789abcdef.mp4"
MB = 1024 * 1024


def _blob(n: int, salt: int = 3) -> bytes:
    return bytes((i * 7 + salt) % 251 for i in range(n))


def _put(store, token: str, data: bytes, uid: str = "acc_1", mime: str = "video/mp4"):
    store.x("INSERT INTO media(token,user_id,name,mime,data,size,meta,created) VALUES(?,?,?,?,?,?,?,?)",
            (token, uid, "v.mp4", mime, data, len(data), "{}", time.time()))


@pytest.fixture
def blob_reads(app, monkeypatch):
    """Every SQL statement that reads media bytes (whole column, substr of it, or a Library chunk)."""
    store = app.state.house.store
    seen: list[str] = []
    orig = store.q

    def q(sql, args=()):
        low = " ".join(sql.lower().split())
        head = low.split(" from ")[0]
        if (" from media" in low or " from library_chunks" in low) and re.search(r"\bdata\b", head.replace("length(data)", "")):
            seen.append(sql)
        return orig(sql, args)
    monkeypatch.setattr(store, "q", q)
    return seen


# ------------------------------------------------------------------ (a) each file leaves the database once
def test_second_fetch_of_the_same_file_reads_nothing_from_the_database(app, client, blob_reads):
    data = _blob(3 * CHUNK + 777)
    _put(app.state.house.store, TOK, data)
    r = client.get(f"/m/{TOK}")
    assert r.status_code == 200 and r.content == data
    first = len(blob_reads)
    assert first > 0
    r = client.get(f"/m/{TOK}")
    assert r.status_code == 200 and r.content == data
    r = client.get(f"/m/{TOK}", headers={"Range": f"bytes={CHUNK - 3}-{CHUNK + 3}"})
    assert r.status_code == 206 and r.content == data[CHUNK - 3:CHUNK + 4]
    assert r.headers["content-range"] == f"bytes {CHUNK - 3}-{CHUNK + 3}/{len(data)}" and r.headers["accept-ranges"] == "bytes"
    assert len(blob_reads) == first, "a cached file was read from the database again"
    st = mediacache.of(app.state.house.store).stats()
    assert st["files"] == 1 and st["hits"] >= 2 and st["misses"] == 1


def test_a_first_range_request_fills_once_then_seeking_is_local(app, client, blob_reads):
    data = _blob(2 * CHUNK + 5)
    _put(app.state.house.store, TOK, data)
    for a, b in ((0, 99), (CHUNK, CHUNK + 10), (2 * CHUNK, 2 * CHUNK + 4)):
        r = client.get(f"/m/{TOK}", headers={"Range": f"bytes={a}-{b}"})
        assert r.status_code == 206 and r.content == data[a:b + 1]
    n = len(blob_reads)
    client.get(f"/m/{TOK}", headers={"Range": "bytes=-3"})
    assert len(blob_reads) == n


def test_internal_readers_use_the_cache_and_never_select_the_whole_column(app, blob_reads):
    h = app.state.house
    data = _blob(CHUNK + 10)
    _put(h.store, TOK, data)
    assert h.get_media(TOK)["data"] == data
    assert not [s for s in blob_reads if "substr(" not in s.lower()], "get_media read the whole data column"
    n = len(blob_reads)
    m = h.get_media(TOK)
    assert m["data"] == data and m["user_id"] == "acc_1" and m["mime"] == "video/mp4" and m["size"] == len(data)
    assert len(blob_reads) == n, "a render / Buffer push read the same file from the database twice"


def test_library_upload_is_cached_and_still_owner_only(app, human, blob_reads):
    from .test_admin_storage import other_user
    data = _blob(CHUNK + 4321)
    up = human.post("/api/library/upload?name=a.mp4", content=b"\x00\x00\x00\x18ftypmp42" + data[12:]).json()
    iid, tok = up["id"], up["media_token"]
    body = human.get(f"/api/library/{iid}/file").content
    n = len(blob_reads)
    assert human.get(f"/api/library/{iid}/file").content == body and len(blob_reads) == n
    assert human.get(f"/m/{tok}").content == body and len(blob_reads) == n  # the same copy under its media token
    # someone else asking for that id gets the same 404 as a missing one, cache or not
    stranger = other_user(app, "acc_2", "two@example.com")
    assert stranger.get(f"/api/library/{iid}/file").status_code == 404
    assert stranger.get("/api/library/999999/file").status_code == 404
    assert TestClient(app, base_url="https://hq.test").get("/m/nope-not-a-token.mp4").status_code == 404


def test_a_failed_or_short_fill_keeps_no_copy(tmp_path):
    c = mediacache.MediaCache(str(tmp_path / "c"), cap=10 * MB)

    def boom():
        yield b"abc"
        raise RuntimeError("storage went away")
    with pytest.raises(RuntimeError):
        c.path_for("t1", '"e"', 6, boom)
    assert c.path_for("t1", '"e"', 6, lambda: iter([b"abc"])) is None  # short: not cached
    assert [p for p in c.dir.iterdir()] == []
    p = c.path_for("t1", '"e"', 6, lambda: iter([b"abc", b"def"]))
    assert p and p.read_bytes() == b"abcdef" and c.stats()["files"] == 1


def test_lru_eviction_keeps_the_cache_under_its_cap(tmp_path):
    c = mediacache.MediaCache(str(tmp_path / "c"), cap=4 * MB)
    one = lambda: iter([b"x" * MB])  # noqa: E731
    for t in ("a", "b", "c", "d"):
        assert c.path_for(t, '"e"', MB, one)
    c.path_for("a", '"e"', MB, one)  # touch a: b is now the oldest
    assert c.path_for("e", '"e"', MB, one)
    st = c.stats()
    assert st["bytes"] <= 4 * MB and st["evictions"] == 1
    names = {p.name.split("-")[0] for p in c.dir.iterdir()}
    assert mediacache._tok_part("b") not in names and mediacache._tok_part("a") in names
    assert c.path_for("big", '"e"', 2 * MB, one) is None  # over a quarter of the cap: served without a copy


def test_a_new_etag_never_gets_the_old_copy(tmp_path):
    c = mediacache.MediaCache(str(tmp_path / "c"), cap=10 * MB)
    p1 = c.path_for("t", '"v1"', 3, lambda: iter([b"old"]))
    p2 = c.path_for("t", '"v2"', 3, lambda: iter([b"new"]))
    assert p1.read_bytes() == b"old" and p2.read_bytes() == b"new"


def test_a_permanent_delete_removes_the_copy(app, client):
    store = app.state.house.store
    _put(store, TOK, _blob(5000))
    assert client.get(f"/m/{TOK}").status_code == 200
    assert mediacache.of(store).stats()["files"] == 1
    record_owner_deletes(store, [(TOK, "acc_1")])
    assert mediacache.of(store).stats()["files"] == 0 and not list(mediacache.of(store).dir.iterdir())


def test_cache_lives_on_the_store_and_off_means_off(app, client, monkeypatch, tmp_path):
    from studio.store import Store
    s2 = Store(str(tmp_path / "other.db"), "c" * 64)
    assert mediacache.of(s2) is not mediacache.of(app.state.house.store)
    monkeypatch.setenv("HQ_MEDIA_CACHE_MB", "0")
    assert not mediacache.MediaCache(str(tmp_path / "off")).on


# ------------------------------------------------------------------ (b) no list / poll query carries blob bytes
# The only places allowed to SELECT a bare `data` column of a blob table: one file / one chunk / one upload part at a
# time, and the one-off R2 migration. A list, poll or aggregate query must name its columns and never include `data`.
ALLOWED_BLOB_READS = {
    ("studio/library.py", "read"),  # _scrub_video's patch loop (after its nested read helper): one chunk at upload
    ("studio/library.py", "_read"), ("studio/library.py", "_iter"),  # an item's chunks, behind the disk cache
    ("studio/uploads.py", "parts"),  # one resumable-upload part at a time, while assembling it
    ("studio/migrate_media.py", "one"),  # the one-off DB -> R2 move (not run; needs Ram's OK)
}


def _blob_selects():
    found = set()
    for p in sorted((ROOT / "studio").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        fn = "<module>"
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*def (\w+)\(", line)
            if m:
                fn = m.group(1)
            low = line.lower()
            for sel in re.findall(r"select\s+(.*?)\s+from\s+\{?(\w+)", low):
                cols, table = sel
                if table in ("media", "library_chunks", "upload_parts", "table") and (
                        cols.strip() == "*" or re.search(r"(^|[\s,])data($|[\s,])", cols)):
                    found.add((rel, fn))
    return found


def test_no_query_outside_the_single_file_readers_selects_blob_bytes():
    extra = _blob_selects() - ALLOWED_BLOB_READS
    assert not extra, f"a query selects a blob column outside the single-file readers: {sorted(extra)}"


def test_list_and_poll_answers_carry_no_blob_bytes(app, human, monkeypatch):
    store = app.state.house.store
    _put(store, TOK, _blob(4000))
    bad = []
    orig = store.q

    def q(sql, args=()):
        rows = orig(sql, args)
        if len(rows) > 1 or "limit" in sql.lower() or " in (" in sql.lower():
            for r in rows:
                if any(isinstance(v, (bytes, bytearray, memoryview)) for v in dict(r).values()):
                    bad.append(sql)
                    break
        return rows
    monkeypatch.setattr(store, "q", q)
    for url in ("/api/library", "/api/library/bin", "/api/agent/runs", "/api/calendar", "/api/takes", "/api/inbox",
                "/api/media/health", "/healthz"):
        human.get(url)
    assert bad == []


# ------------------------------------------------------------------ (d) caching headers, If-Range
def test_media_is_private_immutable_with_an_etag_and_304(app, client):
    _put(app.state.house.store, TOK, _blob(3000))
    r = client.get(f"/m/{TOK}")
    cc = r.headers["cache-control"]
    assert "private" in cc and "immutable" in cc and "max-age=31536000" in cc and "public" not in cc
    et = r.headers["etag"]
    assert client.get(f"/m/{TOK}", headers={"If-None-Match": et}).status_code == 304


def test_if_range_applies_the_range_only_for_the_current_etag(app, client):
    data = _blob(3000)
    _put(app.state.house.store, TOK, data)
    et = client.head(f"/m/{TOK}").headers["etag"]
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=0-9", "If-Range": et})
    assert r.status_code == 206 and r.content == data[:10]
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=0-9", "If-Range": '"something-else"'})
    assert r.status_code == 200 and r.content == data
    r = client.get(f"/m/{TOK}", headers={"Range": "bytes=0-9", "If-Range": "Wed, 21 Oct 2015 07:28:00 GMT"})
    assert r.status_code == 200 and r.content == data


# ------------------------------------------------------------------ (c) meter, budget, repeat downloads
def test_meter_counts_database_and_served_bytes_and_healthz_shows_numbers_only(app, client):
    data = _blob(CHUNK + 100)
    _put(app.state.house.store, TOK, data)
    client.get(f"/m/{TOK}")
    client.get(f"/m/{TOK}", headers={"Range": "bytes=0-99"})
    h = client.get("/healthz").json()
    eg = h["egress"]
    assert eg["db_bytes_today"] == len(data) and eg["served_bytes_today"] == len(data) + 100
    assert eg["cache_files"] == 1 and eg["degraded"] is False
    assert all(isinstance(v, (int, bool)) for v in eg.values())
    assert TOK not in client.get("/healthz").text


def test_over_budget_pauses_other_peoples_browser_previews_but_never_the_owner_or_buffer(app, human, monkeypatch):
    store = app.state.house.store
    _put(store, TOK, _blob(5000))
    monkeypatch.setenv("HQ_EGRESS_SERVE_DAILY_MB", "0.001")  # ~1 KB
    assert human.get(f"/m/{TOK}").status_code == 200  # the owner, and it pushes the meter past the budget
    assert egress.snapshot()["degraded"] is True
    anon = TestClient(app, base_url="https://hq.test")
    r = anon.get(f"/m/{TOK}", headers={"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "video"})
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 60
    assert anon.get(f"/m/{TOK}").status_code == 200  # Buffer / Instagram / the render worker: no Sec-Fetch headers
    assert anon.head(f"/m/{TOK}", headers={"Sec-Fetch-Mode": "no-cors"}).status_code == 200
    et = human.get(f"/m/{TOK}", headers={"Sec-Fetch-Mode": "no-cors"}).headers["etag"]
    assert anon.get(f"/m/{TOK}", headers={"Sec-Fetch-Mode": "no-cors", "If-None-Match": et}).status_code == 304
    assert egress.snapshot()["refused_budget"] == 1


def test_one_browser_repeating_the_same_download_is_slowed_but_not_the_owner_or_a_fetcher(app, human):
    store = app.state.house.store
    _put(store, TOK, _blob(400 * 1024))
    sf = {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "video"}
    anon = TestClient(app, base_url="https://hq.test", headers={"X-Forwarded-For": "198.51.100.7", **sf})
    codes = [anon.get(f"/m/{TOK}").status_code for _ in range(8)]
    assert codes[:3] == [200, 200, 200] and codes[-1] == 429
    assert egress.snapshot()["refused_repeat"] >= 1
    other = TestClient(app, base_url="https://hq.test", headers={"X-Forwarded-For": "198.51.100.8", **sf})
    assert other.get(f"/m/{TOK}").status_code == 200  # another client is unaffected
    worker = TestClient(app, base_url="https://hq.test", headers={"X-Forwarded-For": "198.51.100.7"})
    assert all(worker.get(f"/m/{TOK}").status_code == 200 for _ in range(6))  # a render worker / Buffer re-fetching
    human.headers.update({"X-Forwarded-For": "198.51.100.7", **sf})
    assert all(human.get(f"/m/{TOK}").status_code == 200 for _ in range(6))  # the owner never is


def test_eighty_percent_warning_is_logged_once_and_days_roll_into_a_summary(monkeypatch, capsys):
    monkeypatch.setenv("HQ_EGRESS_DB_DAILY_MB", "1")
    egress.db_read(int(0.85 * MB))
    egress.db_read(1000)
    out = capsys.readouterr().out
    assert out.count("egress warning: db") == 1
    egress._S["cur"]["day"] = "2026-11-04"  # pretend the day turned
    snap = egress.snapshot()
    assert snap["db_bytes"] == 0
    days = egress.history()
    assert days[0]["day"] == "2026-11-04" and days[0]["db_bytes"] == int(0.85 * MB) + 1000
    assert "egress daily 2026-11-04" in capsys.readouterr().out


def test_meter_survives_a_process_restart_from_its_state_file(monkeypatch):
    egress.served(12345)
    egress._save(force=True)
    egress._S.clear()
    assert egress.snapshot()["served_bytes"] == 12345


def test_admin_sees_the_bandwidth_panel_and_nobody_else(app, human, monkeypatch):
    from .test_admin_storage import other_user
    adm = other_user(app, "adm_1", "boss@example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "boss@example.com")
    egress.served(2048)
    d = adm.get("/api/admin/egress").json()
    assert d["today"]["served_bytes"] == 2048 and "days" in d and d["cache"]["on"] is True
    assert human.get("/api/admin/egress").status_code == 404
    page = adm.get("/admin").text
    assert 'data-testid="admin-egress"' in page and 'data-testid="admin-egress-warning"' in page
    js = (ROOT / "studio/admin_ui/admin.js").read_text()
    assert "/api/admin/egress" in js and "egressDraw" in js


def test_session_cookie_name_matches_the_app():
    from studio import app as app_mod
    assert SESSION_COOKIE == app_mod.COOKIE


def test_healthz_pings_the_database_at_most_every_15_seconds(app, client, monkeypatch):
    store = app.state.store
    n = {"c": 0}
    orig = store.ping

    def ping():
        n["c"] += 1
        return orig()
    monkeypatch.setattr(store, "ping", ping)
    for _ in range(5):
        assert client.get("/healthz").json()["db"] is True
    assert n["c"] == 1


# ------------------------------------------------------------------ list queries: run text only when it changed
class _PgLike:
    """A Postgres-shaped store over SQLite with md5(): exercises run_text's Postgres path without a server."""
    pg = True

    def __init__(self):
        import hashlib
        import sqlite3
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("md5", 1, lambda s: hashlib.md5((s or "").encode()).hexdigest())
        self.db.execute("CREATE TABLE agent_runs (id INTEGER PRIMARY KEY, user_id TEXT, input TEXT, result TEXT, updated REAL)")
        self.sql: list[str] = []

    def q(self, sql, args=()):
        self.sql.append(sql)
        return self.db.execute(sql, args).fetchall()


def test_run_lists_read_a_runs_text_only_when_it_changed():
    from studio import run_text
    st = _PgLike()
    for i in range(1, 6):
        st.db.execute("INSERT INTO agent_runs VALUES(?,?,?,?,?)", (i, "u", '{"p":%d}' % i, '{"r":"' + "x" * 5000 + '"}', 1.0))

    def lst():
        return run_text.attach(st, st.q(f"SELECT id, {run_text.cols(st)}, updated FROM agent_runs WHERE user_id=? ORDER BY id", ("u",)))
    a = lst()
    assert [r["input"] for r in a] == ['{"p":%d}' % i for i in range(1, 6)] and all("_hr" not in r for r in a)
    st.sql.clear()
    b = lst()
    assert b == a and len(st.sql) == 1 and "md5(" in st.sql[0] and "input, result" not in st.sql[0]
    # a writer that changes the text without touching `updated` is still seen at once (content hash, not time)
    st.db.execute("UPDATE agent_runs SET result=? WHERE id=3", ('{"r":"changed"}',))
    st.sql.clear()
    c = lst()
    assert c[2]["result"] == '{"r":"changed"}' and len(st.sql) == 2 and "id IN (?)" in st.sql[1]


def test_run_text_reads_the_columns_on_sqlite_and_md5s_on_postgres(app):
    from studio import run_text
    st = app.state.house.store
    assert run_text.cols(st) == ("md5(COALESCE(input,'')) AS _hi, md5(COALESCE(result,'')) AS _hr" if st.pg else "input, result")


def test_a_disk_error_or_an_evicted_copy_falls_back_to_the_backend(tmp_path, monkeypatch):
    c = mediacache.MediaCache(str(tmp_path / "c"), cap=10 * MB)

    def full(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(mediacache.tempfile, "mkstemp", full)
    assert c.path_for("t", '"e"', 3, lambda: iter([b"abc"])) is None and c.stats()["bypass"] == 1
    monkeypatch.undo()

    class S:
        pass
    st = S()
    st._media_cache = c
    data = b"0123456789"
    f = mediacache.wrap(st, {"mime": "video/mp4", "size": 10, "etag": '"x"', "read": lambda s, e: data[s:e + 1],
                             "iter": lambda: iter([data])}, "tok")
    assert f["read"](2, 4) == b"234"  # fills the copy
    for p in c.dir.iterdir():
        p.unlink()  # evicted behind its back
    assert f["read"](5, 9) == b"56789" and b"".join(f["iter"]()) == data
