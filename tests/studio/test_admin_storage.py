"""Admin panel (studio/admin.py) and the media storage backend (studio/blobstore.py, studio/migrate_media.py).

Run against SQLite and, in CI, Postgres (STUDIO_TEST_PG). The S3 side is an in-memory stub behind httpx.MockTransport.
"""
from __future__ import annotations

import hashlib
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import admin, blobstore, migrate_media, r2
from studio.actions import Actor
from studio.store import OWNED_ELSEWHERE, TABLES

from .conftest import connect, image_bytes


# ------------------------------------------------------------------ an in-memory S3 bucket
class StubS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.fail_put = False
        self.corrupt_get = False
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acct.r2.test" and "X-Amz-Signature" in str(request.url)
        path = request.url.path
        assert path.startswith("/hq-media/")
        key = path[len("/hq-media/"):]
        self.calls.append((request.method, key))
        if request.method == "PUT":
            if self.fail_put:
                return httpx.Response(503)
            self.objects[key] = request.content
            return httpx.Response(200, headers={"ETag": '"x"'})
        if request.method == "GET":
            if key not in self.objects:
                return httpx.Response(404)
            data = self.objects[key]
            if self.corrupt_get:
                data = data[:-1] + bytes([data[-1] ^ 1])
            m = re.fullmatch(r"bytes=(\d+)-(\d*)", request.headers.get("range", ""))
            if m:
                s, e = int(m.group(1)), int(m.group(2)) if m.group(2) else len(data) - 1
                return httpx.Response(206, content=data[s:e + 1])
            return httpx.Response(200, content=data)
        if request.method == "DELETE":
            self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(405)


@pytest.fixture
def s3(monkeypatch):
    stub = StubS3()
    for k, v in {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
                 "R2_BUCKET": "hq-media", "R2_ENDPOINT": "https://acct.r2.test"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("HQ_MEDIA_STORAGE", raising=False)
    blobstore.set_http(httpx.Client(transport=httpx.MockTransport(stub)))
    yield stub
    blobstore.set_http(None)


@pytest.fixture(autouse=True)
def _no_bucket_by_default(monkeypatch):
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_ENDPOINT", "HQ_ADMIN_IDS"):
        monkeypatch.delenv(k, raising=False)
    yield


def other_user(app, uid: str, email: str) -> TestClient:
    """A second signed-in browser (the FakeBuffer always signs in acc_1, so this one is made in the store)."""
    store = app.state.store
    store.upsert_user(uid, email, "Admin Person", "org_2", "Ops", {"access_token": "at_x", "refresh_token": "rt_x"})
    sid = "sid-" + uid + "-" + "x" * 24
    store.new_session(sid, "csrf-" + uid)
    store.update_session(sid, user_id=uid)
    c = TestClient(app, base_url="https://hq.test")
    c.cookies.set("hq_sid", sid)
    c.headers["X-HQ-CSRF"] = "csrf-" + uid
    return c


def not_found_body(app):
    return TestClient(app, base_url="https://hq.test").get("/no-such-page-at-all").json()


ADMIN_URLS = [("GET", "/admin"), ("GET", "/admin/admin.js"), ("GET", "/api/admin/users"), ("GET", "/api/admin/users?limit=abc"),
              ("GET", "/api/admin/audit"), ("POST", "/api/admin/users/acc_1/block"), ("POST", "/api/admin/users/acc_1/unblock"),
              ("POST", "/api/admin/users/acc_1/delete")]


# ------------------------------------------------------------------ who gets in
def test_admin_is_404_for_anonymous_signed_in_non_admins_and_agents(app, human, monkeypatch):
    nf = not_found_body(app)
    anon = TestClient(app, base_url="https://hq.test")
    tok = human.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {tok}"})
    monkeypatch.setenv("HQ_ADMIN_IDS", "someone-else@example.com, adm_9")
    for who in (anon, human, bearer):
        for m, url in ADMIN_URLS:
            r = who.request(m, url, content=b"not json" if m == "POST" else None)
            assert r.status_code == 404, (m, url, r.status_code)
            assert r.json() == nf, (m, url)
    # an admin's id in the list still doesn't let an agent token in: browser sessions only
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")
    assert bearer.get("/api/admin/users").status_code == 404
    assert human.get("/api/admin/users").status_code == 200
    # nothing is listed when the variable is empty
    monkeypatch.setenv("HQ_ADMIN_IDS", "")
    assert human.get("/admin").status_code == 404


def test_admin_by_email_sees_users_with_coarse_signin_details(app, human, monkeypatch):
    store = app.state.store
    adm = other_user(app, "adm_1", "Boss@Example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "boss@example.com")
    store.x("INSERT INTO audit(user_id,actor,action,risk,detail,ok,ts,category,ip,ua) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("acc_1", "you", "connect_buffer", "auth", "{}", 1, time.time(), "signin", "203.0.113.77",
             "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 Version/17.0 Safari/605.1.15"))
    page = adm.get("/admin")
    assert page.status_code == 200 and "Admin" in page.text and page.headers["cache-control"] == "no-store"
    assert adm.get("/admin/admin.js").status_code == 200
    d = adm.get("/api/admin/users").json()
    me = next(u for u in d["users"] if u["id"] == "acc_1")
    assert me["email"] == "ram@example.com" and me["status"] == "active"
    assert me["last_signin_ip"] == "203.0.113.0/24" and me["last_signin_ua"] == "Safari on macOS"
    assert "203.0.113.77" not in adm.get("/api/admin/users").text
    assert {"db_bytes", "storage_backend", "db_media_bytes"} <= set(d["overview"]) and d["overview"]["storage_backend"] == "db"
    assert adm.get("/api/admin/users?q=ram@").json()["total"] == 1


# ------------------------------------------------------------------ block
def test_block_revokes_session_and_agent_tokens_now_and_unblock_restores_tokens(app, client, human, monkeypatch):
    store = app.state.store
    tok = human.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {tok}"})
    assert bearer.get("/api/me").status_code == 200
    adm = other_user(app, "adm_1", "boss@example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")

    r = adm.post("/api/admin/users/acc_1/block", json={"reason": "spam"})
    assert r.status_code == 200 and r.json()["status"] == "blocked"
    assert human.get("/api/me").status_code == 401                     # the browser session is gone at once
    assert not store.q("SELECT 1 AS x FROM sessions WHERE user_id=?", ("acc_1",))
    assert bearer.get("/api/me").status_code == 401                    # agent / MCP tokens stop resolving
    mcp = bearer.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert mcp.status_code == 401
    # signing in again is refused with a neutral message, and nothing is stored
    fresh = TestClient(app, base_url="https://hq.test")
    r = fresh.get("/login", follow_redirects=False)
    state = re.search(r"state=([^&]+)", r.headers["location"]).group(1)
    r = fresh.get(f"/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"].startswith("/?error=") and "can%27t%20sign%20in" in r.headers["location"].replace("'", "%27")
    assert fresh.get("/api/session").json()["connected"] is False
    rows = adm.get("/api/admin/users").json()["users"]
    assert next(u for u in rows if u["id"] == "acc_1")["status"] == "blocked"

    assert adm.post("/api/admin/users/acc_1/unblock").status_code == 200
    assert bearer.get("/api/me").status_code == 200                    # reversible: the same token works again
    connect(fresh)
    assert fresh.get("/api/session").json()["connected"] is True
    acts = [a["action"] for a in adm.get("/api/admin/audit").json()["items"]]
    assert acts[:2] == ["unblock", "block"]
    assert all(a["subject_hash"] == admin.subject_hash("acc_1") for a in adm.get("/api/admin/audit").json()["items"])


def test_block_refuses_mcp_oauth_refresh(app, human, monkeypatch):
    """A refresh token minted before the block can't mint new access while blocked."""
    store = app.state.store
    store.x("INSERT INTO oauth_clients(client_id,client_name,redirect_uris,created) VALUES(?,?,?,?)",
            ("cl_1", "Claude", '["https://claude.ai/cb"]', time.time()))
    tid = store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) VALUES(?,?,?,?,?,?,?,?)",
                  ("acc_1", "Claude", "h" * 64, "review", 10, None, time.time(), time.time() + 3600))
    from studio.store import sha
    store.x("INSERT INTO oauth_refresh(token_hash,client_id,user_id,agent_id,policy,daily_cap,expires) VALUES(?,?,?,?,?,?,?)",
            (sha("rt-test"), "cl_1", "acc_1", tid, "review", 10, time.time() + 3600))
    adm = other_user(app, "adm_1", "boss@example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")
    adm.post("/api/admin/users/acc_1/block")
    r = TestClient(app, base_url="https://hq.test").post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "cl_1",
                                                                               "refresh_token": "rt-test"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


def test_admin_cannot_block_or_delete_themselves(app, human, monkeypatch):
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")
    r = human.post("/api/admin/users/acc_1/block")
    assert r.status_code == 400 and "your own" in r.json()["error"]
    r = human.post("/api/admin/users/acc_1/delete", json={"confirm": "acc_1"})
    assert r.status_code == 400
    assert human.get("/api/me").status_code == 200
    assert human.post("/api/admin/users/nobody_here/block").status_code == 404


def test_admin_actions_need_the_csrf_token(app, human, monkeypatch):
    adm = other_user(app, "adm_1", "boss@example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")
    del adm.headers["X-HQ-CSRF"]
    for path in ("block", "unblock", "delete"):
        r = adm.post(f"/api/admin/users/acc_1/{path}", json={"confirm": "acc_1"})
        assert r.status_code == 403, path
    adm.headers["X-HQ-CSRF"] = "wrong"
    assert adm.post("/api/admin/users/acc_1/block").status_code == 403
    assert human.get("/api/me").status_code == 200 and app.state.store.user("acc_1")


# ------------------------------------------------------------------ delete (GDPR erasure)
def _rows_for(store, uid: str) -> dict:
    left = {}
    for t in (*TABLES, *OWNED_ELSEWHERE, "library_items", "library_media", "library_purged"):
        try:
            col = "id" if t == "users" else "user_id"
            n = store.q(f"SELECT COUNT(*) AS n FROM {t} WHERE {col}=?", (uid,))[0]["n"]
        except Exception:  # noqa: BLE001  (a table this store never created, or one without user_id)
            continue
        if n:
            left[t] = n
    return left


def test_delete_user_erases_rows_media_and_tokens_and_leaves_a_tombstone(app, human, monkeypatch, s3):
    store, house = app.state.store, app.state.house
    human.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"})
    a = Actor("acc_1")
    house.put_media(a, image_bytes(64, 64), "card.png", kind="render")
    house.library.save_bytes(a, image_bytes(800, 600), "photo.png")
    assert s3.objects and store.q("SELECT COUNT(*) AS n FROM library_chunks")[0]["n"] >= 1
    adm = other_user(app, "adm_1", "boss@example.com")
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")

    r = adm.post("/api/admin/users/acc_1/delete", json={"confirm": "acc_"})
    assert r.status_code == 400 and store.user("acc_1")
    r = adm.post("/api/admin/users/acc_1/delete", json={"confirm": "acc_1"})
    assert r.status_code == 200 and r.json()["deleted"] is True
    assert _rows_for(store, "acc_1") == {}
    assert store.q("SELECT COUNT(*) AS n FROM library_chunks")[0]["n"] == 0
    assert store.user("acc_1") is None and human.get("/api/me").status_code == 401
    blobstore.sweep(store)
    assert s3.objects == {} and blobstore.pending(store) == 0       # the R2 objects went too
    tomb = store.q("SELECT * FROM user_tombstones")
    assert [(t["id_hash"], t["via"]) for t in tomb] == [(admin.subject_hash("acc_1"), "admin")]
    log = adm.get("/api/admin/audit").json()["items"][0]
    assert log["action"] == "delete" and log["admin"] == "adm_1" and log["subject_hash"] == admin.subject_hash("acc_1")
    assert "acc_1" not in str(store.q("SELECT * FROM admin_audit")) and "ram@example.com" not in str(store.q("SELECT * FROM admin_audit"))


def test_self_service_disconnect_is_the_same_erasure(app, human, s3):
    store, house = app.state.store, app.state.house
    house.put_media(Actor("acc_1"), image_bytes(32, 32), "a.png")
    assert human.post("/api/disconnect").json()["deleted"] is True
    blobstore.sweep(store)
    assert s3.objects == {} and _rows_for(store, "acc_1") == {}
    assert store.q("SELECT via FROM user_tombstones")[0]["via"] == "self"


# ------------------------------------------------------------------ r2 backend
def test_r2_backend_keeps_bytes_out_of_the_database_and_serves_them(app, human, s3):
    store, house = app.state.store, app.state.house
    data = image_bytes(120, 80)
    tok = house.put_media(Actor("acc_1"), data, "card.png")
    row = store.q("SELECT data, storage_key, sha256, size FROM media WHERE token=?", (tok,))[0]
    assert row["data"] is None and row["size"] == len(data) and row["sha256"] == hashlib.sha256(data).hexdigest()
    prefix = blobstore.owner_prefix("acc_1")
    assert row["storage_key"].startswith(f"media/{prefix}/") and "acc_1" not in row["storage_key"]
    assert s3.objects[row["storage_key"]] == data
    anon = TestClient(app, base_url="https://hq.test")
    assert anon.get(f"/m/{tok}").content == data
    r = anon.get(f"/m/{tok}", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == data[:10]
    assert anon.head(f"/m/{tok}").headers["content-length"] == str(len(data))

    # a Library upload: its chunks are objects too, served through the owner-checked file route
    photo = image_bytes(900, 700)
    item = house.library.save_bytes(Actor("acc_1"), photo, "photo.png")
    ch = store.q("SELECT data, storage_key FROM library_chunks WHERE item_id=?", (item["id"],))
    assert ch and all(c["data"] is None and c["storage_key"].startswith(f"lib/{prefix}/") for c in ch)
    got = human.get(f"/api/library/{item['id']}/file")
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == hashlib.sha256(
        b"".join(s3.objects[c["storage_key"]] for c in ch)).hexdigest()
    stranger = other_user(app, "acc_2", "x@example.com")
    assert stranger.get(f"/api/library/{item['id']}/file").status_code == 404   # ownership checks unchanged

    # purge from the Library: rows go in one transaction, the objects after it (blob_gc), retried until confirmed
    keys = {c["storage_key"] for c in ch}
    human.delete(f"/api/library/{item['id']}?mode=permanent&force=1")
    assert not store.q("SELECT 1 AS x FROM library_chunks WHERE item_id=?", (item["id"],))
    blobstore.sweep(store)
    assert not keys & set(s3.objects)


def test_r2_down_falls_back_to_the_database(app, human, s3):
    s3.fail_put = True
    tok = app.state.house.put_media(Actor("acc_1"), b"\x89PNG" + b"0" * 50, "x.png")
    row = app.state.store.q("SELECT data, storage_key FROM media WHERE token=?", (tok,))[0]
    assert row["storage_key"] is None and bytes(row["data"]).startswith(b"\x89PNG")


def test_sweep_keeps_a_key_until_the_bucket_confirms(app, s3):
    store = app.state.store
    store.x("INSERT INTO blob_gc(key,at,tries) VALUES(?,?,0)", ("media/x/y", time.time()))
    s3.objects["media/x/y"] = b"1"
    orig = s3.__call__
    s3_fail = {"on": True}

    def flaky(req):
        if req.method == "DELETE" and s3_fail["on"]:
            return httpx.Response(500)
        return orig(req)
    blobstore.set_http(httpx.Client(transport=httpx.MockTransport(flaky)))
    assert blobstore.sweep(store)["failed"] == 1 and blobstore.pending(store) == 1
    s3_fail["on"] = False
    assert blobstore.sweep(store)["deleted"] == 1 and blobstore.pending(store) == 0 and "media/x/y" not in s3.objects


def test_db_backend_is_the_default_without_r2(app, human):
    tok = app.state.house.put_media(Actor("acc_1"), b"abc", "a.png")
    row = app.state.store.q("SELECT data, storage_key FROM media WHERE token=?", (tok,))[0]
    assert bytes(row["data"]) == b"abc" and row["storage_key"] is None and blobstore.backend_name() == "db"


def test_shared_signer_is_the_one_r2_module():
    cfg = {"endpoint": "https://acct.r2.test", "key": "AKIDTEST", "secret": "dummy-secret-for-tests", "bucket": "b", "region": "auto"}
    from datetime import datetime, timezone
    u = r2.presign(cfg, "GET", "media/a/b", None, 60, now=datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert u.startswith("https://acct.r2.test/b/media/a/b?") and "X-Amz-Expires=60" in u and "X-Amz-Signature=" in u


# ------------------------------------------------------------------ migration
def test_migration_dry_run_moves_nothing_then_verifies_and_is_idempotent(app, human, monkeypatch, s3):
    store, house = app.state.store, app.state.house
    monkeypatch.setenv("HQ_MEDIA_STORAGE", "db")          # write the "old" rows into the database first
    data = image_bytes(50, 50)
    tok = house.put_media(Actor("acc_1"), data, "old.png")
    item = house.library.save_bytes(Actor("acc_1"), image_bytes(700, 500), "old-photo.png")
    monkeypatch.delenv("HQ_MEDIA_STORAGE")
    before = migrate_media.plan(store)
    assert before["media"]["rows"] == 1 and before["library_chunks"]["rows"] >= 1 and not s3.calls   # dry run: no bucket calls

    s3.corrupt_get = True                                  # a copy that doesn't verify never nulls the row
    st = migrate_media.migrate(store, r2.Bucket(r2.config(), http=blobstore._client()), log=lambda *_: None)
    assert st["moved"] == 0 and st["failed"] >= 2
    assert bytes(store.q("SELECT data FROM media WHERE token=?", (tok,))[0]["data"]) == data

    s3.corrupt_get = False
    st = migrate_media.migrate(store, r2.Bucket(r2.config(), http=blobstore._client()))
    assert st["failed"] == 0 and st["moved"] == 1 + before["library_chunks"]["rows"]
    row = store.q("SELECT data, storage_key, sha256 FROM media WHERE token=?", (tok,))[0]
    assert row["data"] is None and s3.objects[row["storage_key"]] == data and row["sha256"] == hashlib.sha256(data).hexdigest()
    assert TestClient(app, base_url="https://hq.test").get(f"/m/{tok}").content == data
    assert human.get(f"/api/library/{item['id']}/file").status_code == 200
    again = migrate_media.migrate(store, r2.Bucket(r2.config(), http=blobstore._client()))
    assert again["moved"] == 0 and again["failed"] == 0                     # idempotent
    after = migrate_media.plan(store)
    assert after["media"]["rows"] == 0 and after["media"]["already_in_r2"] == 1


def test_migration_cli_dry_run(app, monkeypatch, capsys, s3):
    monkeypatch.setenv("DATABASE_URL", app.state.settings.db_path)
    monkeypatch.setenv("SESSION_SECRET", app.state.settings.session_secret)
    assert migrate_media.main(["--to", "r2", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert '"dry_run": true' in out and '"r2_configured": true' in out and not s3.calls
