"""R2 media behind flags (off by default): SigV4 signing, the browser redirect, copy-only migration, the cleanup gate,
the bucket setup script and the free-tier caps (studio/r2.py, studio/r2usage.py, studio/migrate_media.py,
scripts/r2_bucket_setup.py; docs/STORAGE.md, docs/DATA_RESET_PLAN.md). The bucket is the in-memory stub from
test_admin_storage behind httpx.MockTransport: nothing here reaches Cloudflare.
"""
from __future__ import annotations

import hashlib
import importlib.util
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import blobstore, migrate_media, r2, r2usage
from studio.actions import Actor

from .conftest import image_bytes
from .test_admin_storage import StubS3, other_user  # noqa: F401  (the shared stub bucket)

ROOT = Path(__file__).resolve().parents[2]
BROWSER = {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "video"}


@pytest.fixture
def s3(monkeypatch):
    stub = StubS3()
    for k, v in {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
                 "R2_BUCKET": "hq-media", "R2_ENDPOINT": "https://acct.r2.test"}.items():
        monkeypatch.setenv(k, v)
    for k in ("HQ_MEDIA_STORAGE", "R2_JURISDICTION", "HQ_MEDIA_REDIRECT", "HQ_R2_MAX_BYTES", "HQ_R2_MAX_CLASS_A", "HQ_R2_MAX_CLASS_B"):
        monkeypatch.delenv(k, raising=False)
    blobstore.set_http(httpx.Client(transport=httpx.MockTransport(stub)))
    yield stub
    assert blobstore.join_background(10)
    blobstore.set_http(None)


@pytest.fixture(autouse=True)
def _no_bucket_by_default(monkeypatch):
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT", "R2_JURISDICTION",
              "HQ_MEDIA_REDIRECT"):
        monkeypatch.delenv(k, raising=False)
    yield
    blobstore.join_background(10)


def _db_media(app, monkeypatch, data: bytes, name: str = "old.png") -> str:
    """A media row written the old way (bytes in the database)."""
    monkeypatch.setenv("HQ_MEDIA_STORAGE", "db")
    tok = app.state.house.put_media(Actor("acc_1"), data, name)
    monkeypatch.delenv("HQ_MEDIA_STORAGE")
    return tok


def _bucket():
    return r2.Bucket(r2.config(), http=blobstore._client())


# ------------------------------------------------------------------ signing and endpoints
def test_sigv4_presign_matches_an_independent_signer():
    """Known vector: botocore's S3SigV4QueryAuth (path-style, UNSIGNED-PAYLOAD) signed this exact request at this time
    with the AWS documentation's example secret; our stdlib signer must give the same signature."""
    cfg = {"endpoint": "https://acct.r2.test", "key": "AKIDEXAMPLE", "secret": "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
           "bucket": "hq-media", "region": "auto"}
    url = r2.presign(cfg, "GET", "media/abc/x~y.png", None, 900, now=datetime(2026, 11, 5, 18, 22, 42, tzinfo=timezone.utc))
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).path == "/hq-media/media/abc/x~y.png"
    assert q["X-Amz-Credential"] == ["AKIDEXAMPLE/20261105/auto/s3/aws4_request"] and q["X-Amz-SignedHeaders"] == ["host"]
    assert q["X-Amz-Signature"] == ["1ada206c6125ee490d3e8a46d48fce209044c2978ce03cac6a97242fa30978ee"]


def test_endpoint_follows_the_jurisdiction_and_fails_closed(monkeypatch):
    acct = "3a319edacac176a5c9a271bd951a6af8"
    for k, v in {"R2_ACCOUNT_ID": acct, "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
                 "R2_BUCKET": "huhqueue-media"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("R2_ENDPOINT", raising=False)
    assert r2.config()["endpoint"] == f"https://{acct}.r2.cloudflarestorage.com"
    monkeypatch.setenv("R2_JURISDICTION", "eu")
    assert r2.config()["endpoint"] == f"https://{acct}.eu.r2.cloudflarestorage.com"
    monkeypatch.setenv("R2_JURISDICTION", "mars")
    assert r2.config() is None                                  # an unknown jurisdiction: off, never a guessed host
    monkeypatch.delenv("R2_JURISDICTION")
    monkeypatch.setenv("R2_ENDPOINT", "http://plain.example")
    assert r2.config() is None                                  # https only
    monkeypatch.setenv("R2_ENDPOINT", f"https://{acct}.eu.r2.cloudflarestorage.com/")
    assert r2.config()["endpoint"] == f"https://{acct}.eu.r2.cloudflarestorage.com"
    monkeypatch.delenv("R2_ENDPOINT")
    monkeypatch.setenv("R2_ACCOUNT_ID", "evil.example/x")
    assert r2.config() is None                                  # nothing but one DNS label goes into the host


def test_r2_is_off_by_default(app, human):
    assert r2.config() is None and blobstore.backend_name() == "db"
    tok = app.state.house.put_media(Actor("acc_1"), image_bytes(20, 20), "a.png")
    assert app.state.store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"] is None


# ------------------------------------------------------------------ /m/ redirect (HQ_MEDIA_REDIRECT=1)
def test_browser_gets_a_presigned_get_of_at_most_15_minutes(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")
    data = image_bytes(64, 64)
    tok = app.state.house.put_media(Actor("acc_1"), data, "card.png")
    r = TestClient(app, base_url="https://hq.test").get(f"/m/{tok}", headers=BROWSER, follow_redirects=False)
    assert r.status_code == 302
    loc = urlparse(r.headers["location"])
    q = parse_qs(loc.query)
    assert loc.hostname == "acct.r2.test" and int(q["X-Amz-Expires"][0]) <= 900
    assert r.headers["cache-control"] == "no-store" and r.headers["referrer-policy"] == "no-referrer"
    assert "dummy-secret" not in r.headers["location"]


def test_buffer_and_the_worker_get_bytes_from_the_proxy_never_a_redirect(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")
    data = image_bytes(64, 48)
    tok = app.state.house.put_media(Actor("acc_1"), data, "card.png")
    c = TestClient(app, base_url="https://hq.test")
    r = c.get(f"/m/{tok}", follow_redirects=False)              # no Sec-Fetch headers: Buffer, Instagram, the worker
    assert r.status_code == 200 and r.content == data
    h = c.head(f"/m/{tok}", headers=BROWSER, follow_redirects=False)
    assert h.status_code == 200 and h.headers["content-length"] == str(len(data))


def test_ids_are_checked_before_anything_is_signed(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")
    signed = []
    real = r2.presign
    monkeypatch.setattr(r2, "presign", lambda *a, **k: signed.append(a[1]) or real(*a, **k))
    c = TestClient(app, base_url="https://hq.test")
    assert c.get("/m/doesNotExist123456.png", headers=BROWSER, follow_redirects=False).status_code == 404
    assert c.get("/m/trash~whatever.png", headers=BROWSER, follow_redirects=False).status_code == 404
    item = app.state.house.library.save_bytes(Actor("acc_1"), image_bytes(300, 200), "p.png")
    signed.clear()
    stranger = other_user(app, "acc_2", "x@example.com")
    assert stranger.get(f"/api/library/{item['id']}/file", headers=BROWSER, follow_redirects=False).status_code == 404
    assert stranger.get("/api/library/999999/file", headers=BROWSER, follow_redirects=False).status_code == 404
    assert signed == []                                         # a foreign or missing id never reaches the signer


def test_a_redirect_loop_falls_back_to_the_repeat_limit(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")
    tok = app.state.house.put_media(Actor("acc_1"), image_bytes(80, 80), "loop.png")
    c = TestClient(app, base_url="https://hq.test")
    codes = [c.get(f"/m/{tok}", headers=BROWSER, follow_redirects=False).status_code for _ in range(8)]
    assert codes[0] == 302 and codes[-1] == 429                 # an anonymous loop stops being signed



def test_r2_reads_never_count_against_the_supabase_egress_budget(app, human, s3, monkeypatch):
    """Egress follow-up (6 Nov 2026): bytes read from R2 (a disk-cache fill, a range read with the cache off) and files a
    browser is redirected to R2 for cost no Supabase egress, so they go to their own counters (``r2_bytes``,
    ``r2_redirected_bytes``) and never fill the daily ``db`` budget that answers 429 to other people's browsers. Bytes
    still read from the database are counted as ``db`` exactly as before."""
    from studio import egress, mediacache
    from studio.library import media_file
    store = app.state.store
    monkeypatch.setenv("HQ_EGRESS_DB_DAILY_MB", "0.0001")      # ~100 bytes: any R2 byte counted as db would trip it
    data = image_bytes(120, 90)
    tok = app.state.house.put_media(Actor("acc_1"), data, "r2.png")
    assert store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"]
    mediacache.of(store).forget([tok])
    anon = TestClient(app, base_url="https://hq.test")
    assert anon.get(f"/m/{tok}").content == data              # Buffer / the worker: R2 -> disk cache -> response
    snap = egress.snapshot()
    assert snap["db_bytes"] == 0 and snap["r2_bytes"] == len(data) and not snap["degraded"]

    store._media_cache = mediacache.MediaCache(cap=0)         # cache off: every range is a backend read
    f = media_file(store, tok)
    assert f["read"](0, 9) == data[:10]
    snap = egress.snapshot()
    assert snap["db_bytes"] == 0 and snap["r2_bytes"] == len(data) + 10

    # a browser that isn't the owner, past nothing: still a 206 for a Range (the db budget wasn't touched)
    r = anon.get(f"/m/{tok}", headers={**BROWSER, "Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data[:100]

    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")              # a browser is sent to R2: counted as redirected, not db
    assert anon.get(f"/m/{tok}", headers=BROWSER, follow_redirects=False).status_code == 302
    snap = egress.snapshot()
    assert snap["db_bytes"] == 0 and snap["r2_redirected_bytes"] == len(data) and not snap["degraded"]
    hz = anon.get("/healthz").json()["egress"]
    assert hz["db_bytes_today"] == 0 and hz["r2_bytes_today"] == snap["r2_bytes"]
    assert hz["r2_redirected_bytes_today"] == len(data)

    old = _db_media(app, monkeypatch, image_bytes(40, 40), "db.png")  # a row whose bytes are in the database
    got = anon.get(f"/m/{old}").content
    assert egress.snapshot()["db_bytes"] == len(got)          # database bytes still fill the db budget



def test_every_r2_read_is_counted_once_inside_or_outside_the_cache(app, s3):
    """A direct ``blobstore.read`` from R2 (no media-cache tally open) is added to ``r2`` itself; inside a tally the
    bytes go to the tally only (``backend_read`` books them), so nothing is counted twice (CodeRabbit, PR #133)."""
    from studio import egress
    store = app.state.store
    tok = app.state.house.put_media(Actor("acc_1"), image_bytes(60, 40), "direct.png")
    key = store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"]
    assert key
    base = egress.snapshot()["r2_bytes"]
    assert len(blobstore.read(None, key, 0, 9)) == 10
    assert egress.snapshot()["r2_bytes"] == base + 10 and egress.snapshot()["db_bytes"] == 0
    with egress.r2_tally() as t:
        assert len(blobstore.read(None, key, 0, 4)) == 5
    assert t[0] == 5 and egress.snapshot()["r2_bytes"] == base + 10


def test_a_library_range_across_an_r2_chunk_and_a_db_chunk_meters_each_part(app, s3, monkeypatch):
    """Cache off, a Library range spanning a chunk in R2 and a chunk kept in the database: only the R2 chunk's share of
    the answer is ``r2``; the database chunk's share still fills ``db`` (CodeRabbit, PR #133)."""
    from studio import egress, library, mediacache
    store = app.state.store
    monkeypatch.setattr(library, "CHUNK", 64)
    item = app.state.house.library.save_bytes(Actor("acc_1"), image_bytes(200, 150, "JPEG"), "mixed.jpg")
    iid = item["id"]
    rows = store.q("SELECT n, storage_key FROM library_chunks WHERE item_id=? ORDER BY n", (iid,))
    assert len(rows) >= 3 and all(r["storage_key"] for r in rows)
    whole = b"".join(blobstore.read(None, r["storage_key"]) for r in rows)
    store.x("UPDATE library_chunks SET data=?, storage_key=NULL WHERE item_id=? AND n=2", (whole[128:192], iid))
    store._media_cache = mediacache.MediaCache(cap=0)
    f = app.state.house.library.file(Actor("acc_1"), iid)
    before = egress.snapshot()
    assert f["read"](100, 160) == whole[100:161]
    after = egress.snapshot()
    assert after["r2_bytes"] - before["r2_bytes"] == 28          # 100..127 from the R2 chunk
    assert after["db_bytes"] - before["db_bytes"] == 33          # 128..160 from the database chunk

# ------------------------------------------------------------------ migration
def test_copy_only_migration_keeps_the_bytea_and_reads_switch_to_r2(app, human, s3, monkeypatch):
    store = app.state.store
    data = image_bytes(50, 40)
    tok = _db_media(app, monkeypatch, data)
    st = migrate_media.migrate(store, _bucket(), keep_db_copy=True, log=lambda *_: None)
    assert st["moved"] == 1 and st["failed"] == 0
    row = store.q("SELECT data, storage_key, sha256 FROM media WHERE token=?", (tok,))[0]
    assert bytes(row["data"]) == data and row["storage_key"] and row["sha256"] == hashlib.sha256(data).hexdigest()
    s3.calls.clear()
    assert TestClient(app, base_url="https://hq.test").get(f"/m/{tok}").content == data
    assert ("GET", row["storage_key"]) in s3.calls              # read from R2: the database copy stays put
    for k in ("R2_ACCOUNT_ID", "R2_BUCKET"):
        monkeypatch.delenv(k)                                   # R2 switched off again: the kept copy serves
    from studio import mediacache
    mediacache.of(store).forget([tok])
    assert TestClient(app, base_url="https://hq.test").get(f"/m/{tok}").content == data


def test_a_copy_that_fails_its_checksum_never_switches_the_row(app, human, s3, monkeypatch):
    store = app.state.store
    tok = _db_media(app, monkeypatch, image_bytes(30, 30))
    s3.corrupt_get = True
    st = migrate_media.migrate(store, _bucket(), keep_db_copy=True, log=lambda *_: None)
    assert st["moved"] == 0 and st["failed"] == 1
    assert store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"] is None


def test_dropping_db_copies_refuses_without_a_verified_backup(app, monkeypatch, capsys, s3):
    monkeypatch.setenv("DATABASE_URL", app.state.settings.db_path)
    tok = _db_media(app, monkeypatch, image_bytes(20, 20))
    for flags in (["--drop-db-copy"], ["--cleanup"]):
        assert migrate_media.main(["--to", "r2", *flags]) == 2
        assert "--i-have-a-verified-backup" in capsys.readouterr().err
    assert app.state.store.q("SELECT data FROM media WHERE token=?", (tok,))[0]["data"] is not None and not s3.calls


def test_cleanup_drops_only_copies_whose_object_reverifies(app, human, s3, monkeypatch):
    store = app.state.store
    a, b = image_bytes(20, 20), image_bytes(21, 21)
    ta, tb = _db_media(app, monkeypatch, a, "a.png"), _db_media(app, monkeypatch, b, "b.png")
    migrate_media.migrate(store, _bucket(), keep_db_copy=True, log=lambda *_: None)
    kb = store.q("SELECT storage_key FROM media WHERE token=?", (tb,))[0]["storage_key"]
    s3.objects[kb] = b"tampered"
    st = migrate_media.cleanup(store, _bucket(), log=lambda *_: None)
    assert st["dropped"] == 1 and st["kept"] == 1
    assert store.q("SELECT data FROM media WHERE token=?", (ta,))[0]["data"] is None
    assert bytes(store.q("SELECT data FROM media WHERE token=?", (tb,))[0]["data"]) == b


def test_rewrite_of_a_copy_only_row_updates_both_copies(app, s3):
    store = app.state.store
    store.x("INSERT INTO media(token,user_id,name,mime,data,size,meta,created,storage_key,sha256) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("rw1", "acc_1", "v.mp4", "video/mp4", b"old-bytes", 9, "{}", time.time(), "media/x/rw1", "0"))
    s3.objects["media/x/rw1"] = b"old-bytes"
    blobstore.rewrite(store, "media", "token=?", ("rw1",), b"new-bytes")
    assert s3.objects["media/x/rw1"] == b"new-bytes"
    assert bytes(store.q("SELECT data FROM media WHERE token=?", ("rw1",))[0]["data"]) == b"new-bytes"


# ------------------------------------------------------------------ bucket setup script
def _setup_script():
    spec = importlib.util.spec_from_file_location("r2_bucket_setup", ROOT / "scripts" / "r2_bucket_setup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bucket_setup_is_a_dry_run_by_default(monkeypatch, capsys, s3):
    mod = _setup_script()
    monkeypatch.setenv("PUBLIC_URL", "https://huhqueue-studio.onrender.com")
    sent = []
    http = httpx.Client(transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200)))
    assert mod.main([], http=http) == 0
    out = capsys.readouterr().out
    assert sent == [] and "Dry run" in out and "<AllowedOrigin>https://huhqueue-studio.onrender.com</AllowedOrigin>" in out
    assert "*" not in mod.cors_xml("https://huhqueue-studio.onrender.com")
    assert mod.main(["--apply"], http=http) == 0 and sorted(next(p for p in r.url.query.decode().split("&") if p in ("cors=", "lifecycle=")) for r in sent) == ["cors=", "lifecycle="]


# ------------------------------------------------------------------ free-tier caps
def test_storage_cap_keeps_new_media_in_the_database(app, human, s3, monkeypatch, capsys):
    monkeypatch.setenv("HQ_R2_MAX_BYTES", "100")
    data = image_bytes(120, 80)
    assert len(data) > 100
    tok = app.state.house.put_media(Actor("acc_1"), data, "big.png")
    row = app.state.store.q("SELECT data, storage_key FROM media WHERE token=?", (tok,))[0]
    assert row["storage_key"] is None and bytes(row["data"]) == data and not s3.objects
    assert "R2 storage cap reached" in capsys.readouterr().out


def test_class_a_cap_stops_writes_and_the_migration(app, human, s3, monkeypatch):
    store = app.state.store
    tok = _db_media(app, monkeypatch, image_bytes(20, 20))
    monkeypatch.setenv("HQ_R2_MAX_CLASS_A", "0")
    t2 = app.state.house.put_media(Actor("acc_1"), image_bytes(22, 22), "n.png")
    assert store.q("SELECT storage_key FROM media WHERE token=?", (t2,))[0]["storage_key"] is None
    st = migrate_media.migrate(store, _bucket(), keep_db_copy=True, log=lambda *_: None)
    assert st["moved"] == 0 and "write cap" in st["capped"] and not s3.objects
    assert store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"] is None


def test_class_b_cap_serves_db_copies_and_refuses_r2_only_files(app, human, s3, monkeypatch):
    store = app.state.store
    data = image_bytes(40, 40)
    tok = _db_media(app, monkeypatch, data)
    migrate_media.migrate(store, _bucket(), keep_db_copy=True, log=lambda *_: None)
    only = app.state.house.put_media(Actor("acc_1"), image_bytes(41, 41), "r2only.png")
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "0")
    s3.calls.clear()
    c = TestClient(app, base_url="https://hq.test")
    assert c.get(f"/m/{tok}").content == data and not s3.calls   # the kept copy, no R2 read
    assert c.get(f"/m/{only}").status_code == 503                # clear error, never a paid read


def test_operation_counts_persist_prune_and_show_in_admin(app, human, s3, monkeypatch):
    store = app.state.store
    for m in ("2025-01", "2025-02", "2025-03", "2025-04"):
        store.x("INSERT INTO r2_usage(month, cls, n) VALUES(?,?,?)", (m, "A", 5))
    app.state.house.put_media(Actor("acc_1"), image_bytes(30, 30), "x.png")
    r2usage.flush(store)
    months = {r["month"] for r in store.q("SELECT DISTINCT month FROM r2_usage")}
    assert r2usage.month() in months and len(months) <= r2usage.KEEP_MONTHS and "2025-01" not in months
    assert r2usage.ops(store)["A"] >= 1
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")
    r2usage.forget_cache()                                      # stored bytes are re-read at most once a minute
    got = human.get("/api/admin/egress").json()["r2"]
    assert got["configured"] and got["class_a"] >= 1 and got["max_bytes"] == 8 * 1024 ** 3 and got["stored_bytes"] > 0
    assert set(got) >= {"class_b", "max_class_a", "max_class_b", "write_blocked", "read_blocked"}


def test_past_the_class_b_cap_only_the_owner_still_reads_an_r2_only_file(app, human, s3, monkeypatch):
    """Non-owners (other people, Buffer, the worker) get a clear 503; the owner's signed-in browser still gets the file,
    up to the hard ceiling under the free tier (HQ_R2_HARD_CLASS_B, clamped to 9.9M)."""
    data = image_bytes(42, 42)
    only = app.state.house.put_media(Actor("acc_1"), data, "mine.png")
    other = app.state.house.put_media(Actor("acc_1"), image_bytes(43, 43), "mine2.png")
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "0")
    stranger = other_user(app, "acc_9", "nine@example.com")
    assert stranger.get(f"/m/{only}", headers=BROWSER).status_code == 503
    assert TestClient(app, base_url="https://hq.test").get(f"/m/{only}").status_code == 503   # Buffer / worker
    assert human.get(f"/m/{only}", headers=BROWSER).content == data                          # the owner
    assert r2usage.can_read()                                                                # still blocked outside the request
    monkeypatch.setenv("HQ_R2_HARD_CLASS_B", "0")                                            # the ceiling itself is hard
    assert human.get(f"/m/{other}", headers=BROWSER).status_code == 503                      # (not in the disk cache yet)
    monkeypatch.setenv("HQ_R2_HARD_CLASS_B", "50000000")
    assert r2usage.hard_class_b() == r2usage.HARD_B_MAX < 10_000_000


def test_usage_warns_once_at_70_percent(app, s3, monkeypatch, capsys):
    r2usage.bind(app.state.store)
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "10")
    r2usage.note("GET", 6)
    r2usage.can_read()
    assert "WARNING" not in capsys.readouterr().out
    r2usage.note("GET", 1)
    r2usage.can_read()
    r2usage.can_read()
    out = capsys.readouterr().out
    assert out.count("WARNING Class B operations at 70%") == 1 and "acc_" not in out


def test_healthz_shows_r2_numbers_only_when_configured(app, monkeypatch):
    c = TestClient(app, base_url="https://hq.test")
    assert c.get("/healthz").json()["r2"] == {"on": False}
    for k, v in {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
                 "R2_BUCKET": "hq-media", "R2_ENDPOINT": "https://acct.r2.test"}.items():
        monkeypatch.setenv(k, v)
    got = c.get("/healthz").json()["r2"]
    assert got["on"] and got["max_class_b"] == 8_000_000 and got["max_bytes"] == 8 * 1024 ** 3
    assert all(isinstance(v, (int, bool)) for v in got.values())                              # numbers only, no key or host


# ------------------------------------------------------------------ CodeRabbit, PR #120
def test_two_writes_in_one_cache_window_cant_pass_the_storage_cap_together(app, human, s3, monkeypatch, capsys):
    data = image_bytes(60, 60)
    monkeypatch.setenv("HQ_R2_MAX_BYTES", str(int(len(data) * 1.5)))
    r2usage.bind(app.state.store)
    assert r2usage.stored_bytes() == 0                                   # the total is now cached for a minute
    a = app.state.house.put_media(Actor("acc_1"), data, "a.png")
    b = app.state.house.put_media(Actor("acc_1"), data, "b.png")
    keys = [app.state.store.q("SELECT storage_key FROM media WHERE token=?", (t,))[0]["storage_key"] for t in (a, b)]
    assert keys[0] and keys[1] is None and len(s3.objects) == 1           # the second stays in the database
    assert "R2 storage cap reached" in capsys.readouterr().out


def test_a_failed_put_releases_its_reservation(app, s3, monkeypatch):
    r2usage.reserve(1000)
    r2usage.release(1000)
    assert r2usage._reserved() == 0


def test_browser_redirects_stop_at_half_the_class_b_cap(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_REDIRECT", "1")
    data = image_bytes(66, 66)
    tok = app.state.house.put_media(Actor("acc_1"), data, "r.png")
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "10")
    r2usage.note("GET", 5)
    r = TestClient(app, base_url="https://hq.test").get(f"/m/{tok}", headers=BROWSER, follow_redirects=False)
    assert r.status_code == 200 and r.content == data                     # served through /m/, no new presigned URL


def test_migrate_is_copy_only_unless_told_otherwise(app, human, s3, monkeypatch):
    store = app.state.store
    data = image_bytes(24, 24)
    tok = _db_media(app, monkeypatch, data)
    st = migrate_media.migrate(store, _bucket(), log=lambda *_: None)
    row = store.q("SELECT data, storage_key FROM media WHERE token=?", (tok,))[0]
    assert st["moved"] >= 1 and row["storage_key"] and bytes(row["data"]) == data


def test_cleanup_stopped_at_the_read_cap_reports_it(app, human, s3, monkeypatch):
    store = app.state.store
    _db_media(app, monkeypatch, image_bytes(25, 25))
    migrate_media.migrate(store, _bucket(), log=lambda *_: None)
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "0")
    st = migrate_media.cleanup(store, _bucket(), log=lambda *_: None)
    assert st["capped"] and st["dropped"] == 0



def test_with_ops_refuses_a_batch_that_would_cross_the_cap(app, monkeypatch):
    r2usage.bind(app.state.store)
    monkeypatch.setenv("HQ_R2_MAX_CLASS_A", str(r2usage.ops()["A"] + 1))
    ran = []
    why, out = r2usage.with_ops("A", 2, lambda: ran.append(1))
    assert why and out is None and not ran
    why, _ = r2usage.with_ops("A", 1, lambda: ran.append(1))
    assert why is None and ran == [1]


def test_admin_reports_writes_blocked_when_storage_is_exactly_full(app, s3, monkeypatch):
    r2usage.bind(app.state.store)
    monkeypatch.setenv("HQ_R2_MAX_BYTES", "0")
    assert r2usage.snapshot()["write_blocked"]



def test_counts_being_flushed_still_count_and_unconfirmed_link_puts_stay_counted(app, monkeypatch):
    store = app.state.store
    r2usage.bind(store)
    r2usage.note("PUT", 3)
    seen = []
    real_x = store.x

    def spy(sql, args=()):
        if "r2_usage" in sql and "INSERT" in sql:
            seen.append(r2usage.ops(store)["A"])              # mid-flush: the 3 are neither pending nor saved
        return real_x(sql, args)
    monkeypatch.setattr(store, "x", spy)
    r2usage.flush(store)
    assert seen and seen[0] >= 3
    monkeypatch.setattr(store, "x", real_x)
    store.x("INSERT INTO take_uploads(id,user_id,name,size,part_size,parts,status,object_key,s3_upload,take_id,created,updated,"
            "fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("tl_old", "acc_1", "link", 777, 0, 1, "link_put", "takes/x/tl_old.mp4", "", 1, 0.0, 0.0, None))
    r2usage.forget_cache()
    assert r2usage.stored_bytes(store) >= 777                # a day-old unconfirmed link PUT still counts


# ------------------------------------------------------------------ Library uploads never fall back to the database
def test_library_upload_is_refused_not_kept_in_the_database_when_r2_is_full(app, human, s3, monkeypatch, capsys):
    """Past the R2 storage cap a Library upload answers 507 and leaves nothing behind: no item, no chunk, no bytes in the
    database (Ram, 9 Nov 2026: blobs kept in the database drove the Supabase egress outage)."""
    monkeypatch.delenv("HQ_LIBRARY_DB_FALLBACK", raising=False)
    monkeypatch.setenv("HQ_R2_MAX_BYTES", "100")
    store = app.state.store
    r = human.post("/api/library/upload?name=big.png", content=image_bytes(120, 80),
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 507, r.text
    assert "storage is full" in r.text
    assert not store.q("SELECT id FROM library_items WHERE storage IN ('chunks','pending')")
    assert not store.q("SELECT item_id FROM library_chunks") and not s3.objects
    assert "not kept in the database" in capsys.readouterr().out


def test_library_upload_is_refused_when_the_bucket_is_down(app, human, s3, monkeypatch):
    monkeypatch.delenv("HQ_LIBRARY_DB_FALLBACK", raising=False)

    def down(*a, **k):
        raise r2.R2Error("bucket down")
    monkeypatch.setattr(r2.Bucket, "put", down)
    r = human.post("/api/library/upload?name=a.png", content=image_bytes(30, 30),
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 507
    assert not app.state.store.q("SELECT item_id FROM library_chunks")


def test_library_db_fallback_switch_keeps_the_old_behaviour(app, human, s3, monkeypatch):
    monkeypatch.setenv("HQ_LIBRARY_DB_FALLBACK", "1")
    monkeypatch.setenv("HQ_R2_MAX_BYTES", "100")
    data = image_bytes(120, 80)
    r = human.post("/api/library/upload?name=big.png", content=data, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    rows = app.state.store.q("SELECT data, storage_key FROM library_chunks")
    assert rows and all(x["storage_key"] is None and x["data"] is not None for x in rows) and not s3.objects


def test_library_upload_goes_to_r2_under_the_cap(app, human, s3, monkeypatch):
    monkeypatch.delenv("HQ_LIBRARY_DB_FALLBACK", raising=False)
    r = human.post("/api/library/upload?name=ok.png", content=image_bytes(30, 30),
                   headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    rows = app.state.store.q("SELECT data, storage_key FROM library_chunks")
    assert rows and all(x["storage_key"] and x["data"] is None for x in rows)
