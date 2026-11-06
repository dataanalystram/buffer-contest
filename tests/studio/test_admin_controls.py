"""Operator controls in /admin (studio/opsctl.py, studio/admin.py; docs/ADMIN.md): kill switches, per-token caps and
revocation, usage meters, and that every new admin route is 404 to non-admins, CSRF-checked and audit-logged."""
from __future__ import annotations

import time

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import admin, budget, commerce, free_quota, opsctl, r2usage

from .conftest import agent_token
from .test_admin_storage import not_found_body, other_user, s3  # noqa: F401  (s3 is a fixture)
from .test_commerce import c3  # noqa: F401  (fixture)

NEW_URLS = [("GET", "/api/admin/switches"), ("POST", "/api/admin/switches/publishing"), ("POST", "/api/admin/switches/nope"),
            ("GET", "/api/admin/usage"), ("GET", "/api/admin/users/acc_1/tokens"), ("GET", "/api/admin/users/x/tokens?bad=%ZZ"),
            ("POST", "/api/admin/users/acc_1/tokens/1/revoke"), ("POST", "/api/admin/users/acc_1/tokens/abc/revoke"),
            ("POST", "/api/admin/users/acc_1/tokens/1/limit")]


@pytest.fixture(autouse=True)
def _clean_ops():
    opsctl.token_reset()
    yield
    opsctl.reset()


@pytest.fixture
def adm(app, monkeypatch):
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")
    return other_user(app, "adm_1", "boss@example.com")


def _pause(adm, name, on=True, reason=""):
    r = adm.post(f"/api/admin/switches/{name}", json={"paused": on, **({"reason": reason} if reason else {})})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ who gets in (every new route)
def test_new_admin_routes_are_404_to_anonymous_signed_in_and_agents(app, human, monkeypatch):
    nf = not_found_body(app)
    tok = agent_token(human)
    bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {tok}"})
    anon = TestClient(app, base_url="https://hq.test")
    monkeypatch.setenv("HQ_ADMIN_IDS", "someone-else@example.com")
    for who in (anon, human, bearer):
        for m, url in NEW_URLS:
            r = who.request(m, url, content=b"not json" if m == "POST" else None)
            assert r.status_code == 404 and r.json() == nf, (m, url, r.status_code)
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")      # an admin's own agent token still never gets in
    assert bearer.get("/api/admin/switches").status_code == 404
    assert human.get("/api/admin/switches").status_code == 200
    assert not app.state.store.q("SELECT 1 AS x FROM ops_switches")   # nothing was written by any refused call


def test_new_admin_writes_need_csrf(app, human, adm):
    tok = agent_token(human)
    tid = app.state.store.q("SELECT id FROM agent_tokens WHERE user_id='acc_1'")[0]["id"]
    del adm.headers["X-HQ-CSRF"]
    for url, body in ((f"/api/admin/switches/publishing", {"paused": True}), (f"/api/admin/users/acc_1/tokens/{tid}/revoke", {}),
                      (f"/api/admin/users/acc_1/tokens/{tid}/limit", {"hourly_cap": 5})):
        assert adm.post(url, json=body).status_code == 403, url
    adm.headers["X-HQ-CSRF"] = "wrong"
    assert adm.post("/api/admin/switches/publishing", json={"paused": True}).status_code == 403
    assert not opsctl.paused("publishing", app.state.store)
    assert TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {tok}"}).get("/api/me").status_code == 200
    assert admin.audit_rows(app.state.store) == []


# ------------------------------------------------------------------ switches
def test_switch_round_trip_is_audit_logged_and_validated(app, adm):
    items = {s["name"]: s for s in adm.get("/api/admin/switches").json()["items"]}
    assert set(items) == set(opsctl.SWITCHES) and not any(s["paused"] for s in items.values())
    _pause(adm, "ai", True, "runaway loop")
    assert next(s for s in adm.get("/api/admin/switches").json()["items"] if s["name"] == "ai")["paused"] is True
    _pause(adm, "ai", False)
    assert adm.post("/api/admin/switches/nope", json={"paused": True}).status_code == 404
    assert adm.post("/api/admin/switches/ai", json={"paused": "yes"}).status_code == 400
    assert adm.post("/api/admin/switches/ai", content=b"[1]").status_code == 400
    rows = admin.audit_rows(app.state.store)
    assert [(r["action"], r["detail"].get("switch")) for r in rows[:2]] == [("resume", "ai"), ("pause", "ai")]
    assert rows[1]["detail"]["reason"] == "runaway loop" and rows[1]["admin"] == "adm_1"


def test_switches_fail_closed_when_state_cant_be_read(app):
    store = app.state.store
    assert not opsctl.paused("publishing", store)

    class Broken:
        pg = False

        def q(self, *a, **k):
            raise RuntimeError("db down")
    assert all(opsctl.paused(n, Broken()) for n in opsctl.SWITCHES)
    assert opsctl.state(Broken())["ai"]["unreadable"] is True


def test_publishing_switch_stops_everything_reaching_buffer(app, human, adm, fake):
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi", "mode": "queue"}).json()
    _pause(adm, "publishing")
    r = human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    assert r.status_code == 503 and "paused" in r.json()["error"] and fake.created == []
    _pause(adm, "publishing", False)
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi again", "mode": "queue"}).json()
    assert human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}).status_code == 200
    assert len(fake.created) == 1


def test_ai_switch_refuses_every_operator_key_path(app, adm):
    store = app.state.store
    budget.ensure(store)
    _pause(adm, "ai")
    for fn in (lambda: budget.check(store, "acc_1", "claude-sonnet-4-5", 10, 10),
               lambda: budget.oci_check(store, "acc_1", "google.gemini-2.5-flash", 10, 10)):
        with pytest.raises(budget.BudgetExceeded):
            fn()
    ledger = free_quota.Ledger(store)
    ledger._table()
    ok, why = ledger.reserve("groq", 10)
    assert ok is False and "paused" in why
    assert not store.q("SELECT 1 AS x FROM free_usage")      # nothing was reserved
    house = app.state.studio.house
    from studio.actions import Actor
    assert house._ai_reserve(Actor("acc_1"), {"source": "free starter"}) is None
    _pause(adm, "ai", False)
    assert free_quota.Ledger(store).reserve("groq", 10)[0] is True


def test_channel3_switch_sends_nothing(app, adm, monkeypatch):
    calls = []
    monkeypatch.setattr(commerce, "HTTP", httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200, json={}))))
    _pause(adm, "channel3")
    with pytest.raises(commerce.CommerceError) as e:
        commerce._request("dummy-key-for-tests", "GET", "/v0/products/x", uid="acc_1")
    assert e.value.status == 503 and calls == []


def test_r2_uploads_switch_refuses_writes(app, adm):
    store = app.state.store
    assert r2usage.can_write(0, store) is None
    _pause(adm, "r2_uploads")
    assert "paused" in (r2usage.can_write(0, store) or "")
    assert r2usage.reserve(10, store)


def test_link_ingest_switch_refuses_new_links_before_any_consent(app, human, adm):
    _pause(adm, "link_ingest")
    r = human.post("/api/takes", json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "channel_ids": ["ch_x"], "owns": True})
    assert r.status_code == 503 and "paused" in r.json()["error"], r.text
    assert not app.state.store.q("SELECT 1 AS x FROM render_jobs")


# ------------------------------------------------------------------ tokens
def test_admin_lists_limits_and_revokes_one_token(app, human, adm):
    store = app.state.store
    tok = agent_token(human)
    tid = store.q("SELECT id FROM agent_tokens WHERE user_id='acc_1'")[0]["id"]
    bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {tok}"})
    items = adm.get("/api/admin/users/acc_1/tokens").json()["items"]
    assert [t["id"] for t in items] == [tid] and items[0]["hourly_cap"] is None
    assert "token" not in items[0] and "token_hash" not in items[0] and tok not in adm.get("/api/admin/users/acc_1/tokens").text

    # bad values answer 400, never 500
    for body in ({"hourly_cap": "5"}, {"hourly_cap": 0}, {"hourly_cap": True}, {"daily_cap": 101}, {"daily_cap": None}, {}, []):
        assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json=body).status_code == 400, body
    # a token of another account, a missing one and a bad id all answer 404
    other = store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires) VALUES(?,?,?,?,?,?,?,?)",
                    ("adm_1", "Mine", "z" * 64, "review", 10, None, time.time(), time.time() + 3600))
    for t in (str(other), "999999", "abc", "1;drop"):
        assert adm.post(f"/api/admin/users/acc_1/tokens/{t}/revoke").status_code == 404, t
        assert adm.post(f"/api/admin/users/acc_1/tokens/{t}/limit", json={"hourly_cap": 3}).status_code == 404, t
    assert adm.get("/api/admin/users/nobody/tokens").status_code == 404

    r = adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json={"hourly_cap": 3, "daily_cap": 20})
    assert r.status_code == 200 and r.json() == {"id": tid, "hourly_cap": 3, "daily_cap": 20}
    assert [bearer.get("/api/me").status_code for _ in range(4)] == [200, 200, 200, 429]
    mcp = bearer.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert mcp.status_code == 429 and int(mcp.headers["Retry-After"]) > 0
    assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json={"hourly_cap": None}).status_code == 200
    assert bearer.get("/api/me").status_code == 200

    assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/revoke").status_code == 200
    assert bearer.get("/api/me").status_code == 401
    assert adm.get("/api/admin/users/acc_1/tokens").json()["items"][0]["revoked"] is True
    assert store.q("SELECT revoked FROM agent_tokens WHERE id=?", (other,))[0]["revoked"] == 0
    acts = [(r["action"], r["detail"].get("token")) for r in admin.audit_rows(store)]
    assert acts[:3] == [("revoke_token", tid), ("limit_token", tid), ("limit_token", tid)]
    assert all(r["subject_hash"] == admin.subject_hash("acc_1") for r in admin.audit_rows(store)[:3])


def test_token_hit_window():
    a = {"id": 7, "admin_hourly_cap": 2}
    assert opsctl.token_hit(a, 1000.0) == 0 and opsctl.token_hit(a, 1001.0) == 0
    assert opsctl.token_hit(a, 1002.0) > 0
    assert opsctl.token_hit(a, 1000.0 + 3600) == 0          # the oldest one left the window
    assert opsctl.token_hit({"id": 8, "admin_hourly_cap": None}) == 0 and opsctl.token_hit(None) == 0


# ------------------------------------------------------------------ usage
def test_usage_meters_are_numbers_only(app, adm, monkeypatch):
    monkeypatch.setenv("CHANNEL3_API_KEY", "dummy-key-for-tests")
    d = adm.get("/api/admin/usage").json()
    assert {"claude", "oci", "channel3", "free_tiers"} <= set(d)
    assert d["channel3"]["cap"] <= commerce.FREE_CREDITS and d["channel3"]["configured"] is True
    assert set(d["free_tiers"]) == set(free_quota.LIMITS)
    assert "dummy-key-for-tests" not in adm.get("/api/admin/usage").text


def test_admin_page_has_the_new_sections():
    from pathlib import Path
    root = Path(admin.__file__).parent / "admin_ui"
    js, html = (root / "admin.js").read_text(), (root / "index.html").read_text()
    assert 'data-testid="admin-switches"' in html and 'data-testid="admin-usage"' in html and 'id="tokDlg"' in html
    assert "/api/admin/switches" in js and "/api/admin/usage" in js and "/tokens" in js
    assert "prompt(" not in js and "confirm(" not in js and "alert(" not in js


# ------------------------------------------------------------------ CodeRabbit round 1 (PR #128)
def test_publishing_pause_keeps_the_confirm_token_and_holds_still_work(app, human, adm, fake):
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "keep me", "mode": "queue"}).json()
    _pause(adm, "publishing")
    assert human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}).status_code == 503
    tok = agent_token(human)              # a review-policy agent's post is only held: holding works while paused
    from .conftest import mcp_call
    _, apv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "held", "mode": "queue"})
    _, held = mcp_call(human, tok, "submit_post", {"confirm_token": apv["confirm_token"]})
    assert held["status"] == "pending_approval"
    r = human.post(f"/api/approvals/{held['approval_id']}/approve", json={})
    assert r.status_code == 503 and human.get("/api/approvals").json()      # still pending, nothing sent
    _pause(adm, "publishing", False)
    assert human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}).status_code == 200   # same token
    assert [p["text"] for p in fake.created] == ["keep me"]


def test_paused_channel3_search_reserves_no_credit(app, human, adm, c3):
    _pause(adm, "channel3")
    r = human.post("/api/commerce/search", json={"query": "wireless headphones"})
    assert r.status_code == 503 and "paused" in r.json()["error"]
    commerce.ensure(app.state.store)
    assert not app.state.store.q("SELECT 1 AS x FROM commerce_credits") and not app.state.store.q("SELECT 1 AS x FROM commerce_user_day")


def test_migration_stops_while_r2_uploads_is_paused(app, human, adm, monkeypatch, s3):
    from studio import migrate_media, r2
    from studio.actions import Actor as _A
    from .conftest import image_bytes
    monkeypatch.setenv("HQ_MEDIA_STORAGE", "db")
    app.state.house.put_media(_A("acc_1"), image_bytes(40, 40), "old.png")
    monkeypatch.delenv("HQ_MEDIA_STORAGE")
    _pause(adm, "r2_uploads")
    from studio import blobstore
    st = migrate_media.migrate(app.state.store, r2.Bucket(r2.config(), http=blobstore._client()), log=lambda *_: None)
    assert st["moved"] == 0 and "paused" in st.get("capped", "") and not [c for c in s3.calls if c[0] == "PUT"]


def test_publishing_pause_keeps_a_spread_token_usable(app, human, adm, monkeypatch):
    """CodeRabbit round 2: One take's spread token is checked against the pause before it is used up."""
    import json as _json
    from studio.actions import Actor as _A
    from studio.buffer_api import BufferError
    from studio.store import sha
    store, takes = app.state.store, app.state.house.takes
    monkeypatch.setattr(takes, "_run", lambda a, rid: {"status": "ready", "result": {}})
    store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires) VALUES(?,?,?,?,?)",
            (sha("spread-ct"), "acc_1", None, _json.dumps({"kind": "spread", "take": 5, "plan": {"placements": []}}), time.time() + 600))
    _pause(adm, "publishing")
    with pytest.raises(BufferError) as e:
        takes.place(_A("acc_1"), 5, {"confirm_token": "spread-ct"})
    assert e.value.status == 503
    assert store.q("SELECT used FROM confirms WHERE token_hash=?", (sha("spread-ct"),))[0]["used"] == 0


# ------------------------------------------------------------------ CodeRabbit round 3: the MCP refresh lineage
def _mcp_grant(store, uid="acc_1"):
    from studio.store import sha
    store.x("INSERT INTO oauth_clients(client_id,client_name,redirect_uris,created) VALUES(?,?,?,?) ON CONFLICT(client_id) DO NOTHING",
            ("cl_1", "Claude", '["https://claude.ai/cb"]', time.time()))
    tid = store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires,client_id) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (uid, "Claude", "p" * 64, "review", 10, None, time.time(), time.time() + 3600, "cl_1"))
    store.x("INSERT INTO oauth_refresh(token_hash,client_id,user_id,agent_id,policy,daily_cap,expires) VALUES(?,?,?,?,?,?,?)",
            (sha("rt-lineage"), "cl_1", uid, tid, "review", 10, time.time() + 3600))
    return tid


def _refresh(app, rt="rt-lineage"):
    return TestClient(app, base_url="https://hq.test").post("/oauth/token", data={"grant_type": "refresh_token", "client_id": "cl_1",
                                                                                  "refresh_token": rt})


def test_admin_revoke_follows_the_refresh_lineage_not_other_connections(app, human, adm):
    store = app.state.store
    tid = _mcp_grant(store)
    other = store.x("INSERT INTO agent_tokens(user_id,name,token_hash,policy,daily_cap,channels,created,expires,client_id) "
                    "VALUES(?,?,?,?,?,?,?,?,?)", ("acc_1", "Claude 2", "q" * 64, "review", 10, None, time.time(), time.time() + 3600, "cl_1"))
    r = _refresh(app)
    assert r.status_code == 200
    succ = store.q("SELECT id, revoked FROM agent_tokens WHERE parent_id=?", (tid,))
    assert len(succ) == 1 and succ[0]["revoked"] == 0
    new_bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert new_bearer.get("/api/me").status_code == 200
    out = adm.post(f"/api/admin/users/acc_1/tokens/{tid}/revoke").json()     # the old (rotated) row: its successor goes too
    assert out["successors_revoked"] == 1
    assert new_bearer.get("/api/me").status_code == 401
    assert _refresh(app, r.json()["refresh_token"]).status_code == 400
    assert store.q("SELECT revoked FROM agent_tokens WHERE id=?", (other,))[0]["revoked"] == 0   # a separate connection stays


def test_admin_revoke_during_a_refresh_revokes_the_successor_it_made(app, human, adm, monkeypatch):
    store, studio = app.state.store, app.state.studio
    tid = _mcp_grant(store)
    real = studio.create_agent_token

    def racing(*a, **k):                     # the admin's revoke lands after the refresh claimed the old row, before mint ends
        out = real(*a, **k)
        assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/revoke").status_code == 200
        return out
    monkeypatch.setattr(studio, "create_agent_token", racing)
    r = _refresh(app)
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert all(x["revoked"] for x in store.q("SELECT revoked FROM agent_tokens WHERE user_id='acc_1' AND client_id='cl_1'"))


def test_a_refresh_after_an_admin_revoke_is_refused(app, human, adm):
    store = app.state.store
    tid = _mcp_grant(store)
    adm.post(f"/api/admin/users/acc_1/tokens/{tid}/revoke")
    assert _refresh(app).status_code == 400
    assert not store.q("SELECT 1 AS x FROM agent_tokens WHERE parent_id=?", (tid,))


def test_sample_ai_allowance_is_not_spent_while_ai_is_paused():
    from pathlib import Path
    src = (Path(admin.__file__).parent / "actions.py").read_text()
    assert 'if ai["base_url"] and demo_guard.is_demo(a.uid)' in src


# ------------------------------------------------------------------ CodeRabbit round 4: caps survive a refresh
def test_a_refresh_keeps_the_operators_caps_and_hourly_window(app, human, adm):
    """An operator-limited MCP token can't refresh its way out of the limit: the successor carries the parent's current
    daily and hourly caps (not the ones granted at connect time), its refresh record keeps the daily cap, and the hourly
    window carries over."""
    store = app.state.store
    tid = _mcp_grant(store)
    assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json={"hourly_cap": 2, "daily_cap": 3}).status_code == 200
    opsctl.token_hit({"id": tid, "admin_hourly_cap": 2})               # one request already spent this hour
    r = _refresh(app)
    assert r.status_code == 200
    succ = dict(store.q("SELECT id, daily_cap, admin_hourly_cap FROM agent_tokens WHERE parent_id=?", (tid,))[0])
    assert succ["daily_cap"] == 3 and succ["admin_hourly_cap"] == 2
    from studio.store import sha
    assert store.q("SELECT daily_cap FROM oauth_refresh WHERE token_hash=?", (sha(r.json()["refresh_token"]),))[0]["daily_cap"] == 3
    assert opsctl.token_window(succ["id"]) == 1
    new_bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert new_bearer.get("/api/me").status_code == 200                  # the second request of the hour
    assert new_bearer.get("/api/me").status_code == 429                  # past the cap the refresh didn't reset
    # a second refresh still carries the limit (the lineage, not only one hop)
    r2 = _refresh(app, r.json()["refresh_token"])
    assert r2.status_code == 200
    s2 = store.q("SELECT daily_cap, admin_hourly_cap FROM agent_tokens WHERE parent_id=?", (succ["id"],))[0]
    assert s2["daily_cap"] == 3 and s2["admin_hourly_cap"] == 2


def test_a_limit_saved_during_a_refresh_reaches_the_successor(app, human, adm, monkeypatch):
    store, studio = app.state.store, app.state.studio
    tid = _mcp_grant(store)
    real = studio.create_agent_token

    def racing(*a, **k):                     # the operator saves a limit on the old row while the refresh is minting
        out = real(*a, **k)
        assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json={"hourly_cap": 7, "daily_cap": 4}).status_code == 200
        return out
    monkeypatch.setattr(studio, "create_agent_token", racing)
    r = _refresh(app)
    assert r.status_code == 200
    s = store.q("SELECT daily_cap, admin_hourly_cap FROM agent_tokens WHERE parent_id=?", (tid,))[0]
    assert s["daily_cap"] == 4 and s["admin_hourly_cap"] == 7


def test_a_limit_on_a_rotated_token_reaches_its_successors(app, human, adm):
    store = app.state.store
    tid = _mcp_grant(store)
    assert _refresh(app).status_code == 200
    assert adm.post(f"/api/admin/users/acc_1/tokens/{tid}/limit", json={"hourly_cap": 9}).status_code == 200
    assert store.q("SELECT admin_hourly_cap FROM agent_tokens WHERE parent_id=?", (tid,))[0]["admin_hourly_cap"] == 9
