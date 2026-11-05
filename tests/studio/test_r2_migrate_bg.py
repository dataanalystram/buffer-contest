"""The in-process R2 migration (studio/r2_migrate_bg.py, docs/STORAGE.md#in-process-migration): env-gated, one instance,
verified copy before the switch, verified cleanup before a database copy is dropped, numbers-only progress, never in
tests unless enabled. The bucket is the in-memory stub from test_admin_storage behind httpx.MockTransport.
"""
from __future__ import annotations

import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import blobstore, egress, migrate_media, r2, r2_migrate_bg
from studio.actions import Actor

from .conftest import image_bytes
from .test_admin_storage import StubS3, other_user  # noqa: F401

R2ENV = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
         "R2_BUCKET": "hq-media", "R2_ENDPOINT": "https://acct.r2.test"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in (*R2ENV, "R2_JURISDICTION", "HQ_MEDIA_REDIRECT", "HQ_R2_MIGRATE", "HQ_MEDIA_STORAGE", "HQ_R2_MAX_BYTES",
              "HQ_R2_MAX_CLASS_A", "HQ_R2_MAX_CLASS_B"):
        monkeypatch.delenv(k, raising=False)
    r2_migrate_bg.reset()
    egress.reset()
    yield
    r2_migrate_bg.reset()
    blobstore.join_background(10)


@pytest.fixture
def s3(monkeypatch):
    stub = StubS3()
    for k, v in R2ENV.items():
        monkeypatch.setenv(k, v)
    blobstore.set_http(httpx.Client(transport=httpx.MockTransport(stub)))
    yield stub
    assert blobstore.join_background(10)
    blobstore.set_http(None)


def _bucket():
    return r2.Bucket(r2.config(), http=blobstore._client())


def _db_media(app, monkeypatch, data: bytes, name: str) -> str:
    monkeypatch.setenv("HQ_MEDIA_STORAGE", "db")
    tok = app.state.house.put_media(Actor("acc_1"), data, name)
    monkeypatch.delenv("HQ_MEDIA_STORAGE")
    return tok


def test_off_unless_hq_r2_migrate_says_copy_or_cleanup(app, s3, monkeypatch):
    assert r2_migrate_bg.start(app.state.store) is None                     # unset: no thread, nothing touched
    for v in ("off", "yes", "COPYY", ""):
        monkeypatch.setenv("HQ_R2_MIGRATE", v)
        assert r2_migrate_bg.start(app.state.store) is None
        assert r2_migrate_bg.run(app.state.store, _bucket(), log=lambda *_: None)["state"] == "off"
    assert not s3.calls
    assert TestClient(app).get("/healthz").json()["r2_migrate"]["state"] == "off"


def test_copy_verifies_then_switches_and_keeps_the_db_copy(app, human, s3, monkeypatch):
    store = app.state.store
    a, b = image_bytes(40, 30), image_bytes(31, 33)
    ta, tb = _db_media(app, monkeypatch, a, "a.png"), _db_media(app, monkeypatch, b, "b.png")
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    lines: list[str] = []
    s = r2_migrate_bg.run(store, _bucket(), log=lines.append)
    assert s["state"] == "done" and s["copied"] == 2 and s["verified"] == 2 and s["failed"] == 0
    assert s["before"]["media"]["rows"] == 2 and s["after"]["media"]["rows"] == 0
    for tok, data in ((ta, a), (tb, b)):
        row = store.q("SELECT data, storage_key, sha256 FROM media WHERE token=?", (tok,))[0]
        assert bytes(row["data"]) == data and row["sha256"] == hashlib.sha256(data).hexdigest()
        assert s3.objects[row["storage_key"]] == data
    # every PUT was read back before its row switched
    puts = [k for m, k in s3.calls if m == "PUT"]
    assert all(("GET", k) in s3.calls for k in puts)
    # metered apart: the daily preview budget is untouched
    snap = egress.snapshot()
    assert snap["db_migration_bytes"] == len(a) + len(b) and snap["db_bytes"] == 0
    # report lines carry numbers and words only
    assert lines[-1].startswith("r2 migrate: report") and "acc_1" not in " ".join(lines) and ta not in " ".join(lines)
    # idempotent: a second run moves nothing
    s3.calls.clear()
    assert r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)["copied"] == 0
    assert not [c for c in s3.calls if c[0] == "PUT"]


def test_a_copy_that_fails_its_check_never_switches_and_reports_done_with_errors(app, human, s3, monkeypatch):
    store = app.state.store
    tok = _db_media(app, monkeypatch, image_bytes(30, 30), "a.png")
    s3.corrupt_get = True
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    s = r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)
    assert s["state"] == "done_with_errors" and s["failed"] == 1 and s["copied"] == 0
    assert store.q("SELECT storage_key FROM media WHERE token=?", (tok,))[0]["storage_key"] is None


def test_cleanup_drops_only_reverified_rows_and_media_still_plays(app, human, s3, monkeypatch):
    store = app.state.store
    a, b = image_bytes(20, 22), image_bytes(23, 21)
    ta, tb = _db_media(app, monkeypatch, a, "a.png"), _db_media(app, monkeypatch, b, "b.png")
    migrate_media.migrate(store, _bucket(), log=lambda *_: None)
    kb = store.q("SELECT storage_key FROM media WHERE token=?", (tb,))[0]["storage_key"]
    s3.objects[kb] = b"tampered"
    monkeypatch.setenv("HQ_R2_MIGRATE", "cleanup")
    s = r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)
    assert s["state"] == "done_with_errors" and s["dropped"] == 1 and s["kept"] == 1
    assert store.q("SELECT data FROM media WHERE token=?", (ta,))[0]["data"] is None
    assert bytes(store.q("SELECT data FROM media WHERE token=?", (tb,))[0]["data"]) == b
    from studio import mediacache
    mediacache.of(store).forget([ta])
    c = TestClient(app, base_url="https://hq.test")
    assert c.get(f"/m/{ta}").content == a                                   # served from R2 now
    r = c.get(f"/m/{ta}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == a[:10]


def test_cleanup_skips_a_row_rewritten_after_it_was_verified(app, human, s3, monkeypatch):
    """The drop is guarded on the key AND the hash it verified, so a row whose file changed keeps its database copy."""
    store = app.state.store
    tok = _db_media(app, monkeypatch, image_bytes(20, 20), "a.png")
    migrate_media.migrate(store, _bucket(), log=lambda *_: None)
    real = migrate_media._digest

    def racing(b, key):
        out = real(b, key)
        store.x("UPDATE media SET sha256=? WHERE token=?", ("0" * 64, tok))   # rewritten between check and drop
        return out
    monkeypatch.setattr(migrate_media, "_digest", racing)
    st = migrate_media.cleanup(store, _bucket(), log=lambda *_: None)
    assert st["dropped"] == 1                                               # counted as verified, but…
    assert store.q("SELECT data FROM media WHERE token=?", (tok,))[0]["data"] is not None   # …the guard kept the copy


def test_second_runner_does_nothing_while_one_holds_the_lock(app, s3, monkeypatch):
    """Postgres: another session holds the advisory lock (another instance mid-run); SQLite: the in-process lock."""
    store = app.state.store
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    if store.pg:
        held = r2_migrate_bg._Conn(store.path)
        assert held.q("SELECT pg_try_advisory_lock(?) AS ok", (r2_migrate_bg.LOCK_KEY,))[0]["ok"]
        release = held.close
    else:
        assert r2_migrate_bg._PROC.acquire(blocking=False)
        release = r2_migrate_bg._PROC.release
    try:
        assert r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)["state"] == "other_instance"
    finally:
        release()
    assert not s3.calls
    if store.pg:  # the lock is released when its holder's session ends: the next run gets it
        assert r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)["state"] == "done"


def test_not_configured_does_nothing(app, monkeypatch):
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    assert r2_migrate_bg.run(app.state.store, log=lambda *_: None)["state"] == "not_configured"


def test_progress_is_numbers_only_in_healthz_and_admin(app, human, s3, monkeypatch):
    store = app.state.store
    tok = _db_media(app, monkeypatch, image_bytes(25, 25), "a.png")
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    r2_migrate_bg.run(store, _bucket(), log=lambda *_: None)
    got = TestClient(app).get("/healthz").json()
    mg = got["r2_migrate"]
    assert mg["mode"] == "copy" and mg["state"] == "done" and mg["copied"] == 1
    text = str(mg)
    assert tok not in text and "acc_1" not in text and "media/" not in text and "dummy-secret" not in text
    assert got["egress"]["db_migration_bytes_today"] > 0


def test_streamed_digest_matches_and_a_missing_object_is_an_r2_error(s3):
    data = b"x" * (3 * 1024 * 1024 + 7)
    b = _bucket()
    b.put("media/o/k", data)
    assert b.digest("media/o/k") == (len(data), hashlib.sha256(data).hexdigest())
    with pytest.raises(r2.R2Error):
        b.digest("media/o/missing")


def test_start_runs_in_a_background_thread_after_its_delay(app, human, s3, monkeypatch):
    store = app.state.store
    _db_media(app, monkeypatch, image_bytes(22, 22), "a.png")
    monkeypatch.setenv("HQ_R2_MIGRATE", "copy")
    t = r2_migrate_bg.start(store, delay=0)
    assert t is not None
    t.join(20)
    assert r2_migrate_bg.snapshot()["state"] == "done" and r2_migrate_bg.snapshot()["copied"] == 1
