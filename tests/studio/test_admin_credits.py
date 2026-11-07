"""Claude credit per person and per group (studio/credits.py, docs/ADMIN.md): the allowance rule (personal, then group,
then the default), the admin routes (404 to non-admins and agents, CSRF, validation, audit rows), and the Automatic order
handing over to NVIDIA once a person's credit is used up (owner, 7 Nov 2026)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from studio import admin, ai_engines, brain_llm, budget, credits, free_engines

from .conftest import agent_token
from .test_admin_storage import not_found_body, other_user

URLS = [("GET", "/api/admin/users/acc_1/credit"), ("POST", "/api/admin/users/acc_1/credit"), ("GET", "/api/admin/groups"),
        ("POST", "/api/admin/groups"), ("POST", "/api/admin/groups/1"), ("GET", "/api/admin/groups/1/members"),
        ("POST", "/api/admin/groups/1/members"), ("POST", "/api/admin/groups/1/delete"), ("POST", "/api/admin/groups/abc")]


@pytest.fixture
def adm(app, human, monkeypatch):
    monkeypatch.setenv("HQ_ADMIN_IDS", "adm_1")
    monkeypatch.delenv("AI_BUDGET_PER_USER_USD", raising=False)
    monkeypatch.delenv("HQ_BRAIN_USER_USD", raising=False)
    monkeypatch.delenv("HQ_BRAIN_TESTERS", raising=False)
    budget.ensure(app.state.store)
    return other_user(app, "adm_1", "boss@example.com")


@pytest.fixture
def claude_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    monkeypatch.setattr(brain_llm, "resolve_models", lambda key, http=None, force=False: {"plan": "claude-sonnet-4-5", "fast": "claude-haiku-4-5"})


def _set(adm, uid, body, code=200):
    r = adm.post(f"/api/admin/users/{uid}/credit", json=body)
    assert r.status_code == code, r.text
    return r.json()


def _group(adm, name, usd):
    r = adm.post("/api/admin/groups", json={"name": name, "allowance_usd": usd})
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ who gets in
def test_credit_routes_are_404_to_anonymous_signed_in_and_agents(app, human, monkeypatch):
    nf = not_found_body(app)
    bearer = TestClient(app, base_url="https://hq.test", headers={"Authorization": f"Bearer {agent_token(human)}"})
    anon = TestClient(app, base_url="https://hq.test")
    monkeypatch.setenv("HQ_ADMIN_IDS", "someone-else@example.com")
    for who in (anon, human, bearer):
        for m, url in URLS:
            r = who.request(m, url, content=b"not json" if m == "POST" else None)
            assert r.status_code == 404 and r.json() == nf, (m, url, r.status_code)
    monkeypatch.setenv("HQ_ADMIN_IDS", "acc_1")      # an admin's own agent token still never gets in
    assert bearer.get("/api/admin/groups").status_code == 404
    assert human.get("/api/admin/groups").status_code == 200
    assert not app.state.store.q("SELECT 1 AS x FROM credit_groups")


def test_credit_writes_need_csrf(app, adm):
    del adm.headers["X-HQ-CSRF"]
    assert adm.post("/api/admin/users/acc_1/credit", json={"allowance_usd": 20}).status_code == 403
    assert adm.post("/api/admin/groups", json={"name": "Judges", "allowance_usd": 15}).status_code == 403
    assert credits.allowance(app.state.store, "acc_1")["source"] == "default"
    assert admin.audit_rows(app.state.store) == []


# ------------------------------------------------------------------ the allowance rule
def test_default_is_five_dollars_and_render_env_wins(app, adm, monkeypatch):
    st = app.state.store
    assert budget.caps()["user"] == 5.0
    assert credits.allowance(st, "acc_1") == {"cap_usd": 5.0, "source": "default", "group": None, "personal_usd": None, "default_usd": 5.0}
    monkeypatch.setenv("AI_BUDGET_PER_USER_USD", "7")
    assert credits.cap(st, "acc_1") == 7.0 and budget.remaining(st, "acc_1")["pool_cap_usd"] == 7.0


def test_personal_then_group_then_default(app, adm):
    st = app.state.store
    g = _group(adm, "Contest judges", 15)
    assert g["members"] == 0 and g["allowance_usd"] == 15.0
    v = _set(adm, "acc_1", {"group_id": g["id"]})
    assert v["source"] == "group" and v["cap_usd"] == 15.0 and v["group"]["name"] == "Contest judges"
    v = _set(adm, "acc_1", {"allowance_usd": 20, "note": "judge, heavy demo"})
    assert v["source"] == "personal" and v["cap_usd"] == 20.0
    v = _set(adm, "acc_1", {"allowance_usd": None})          # cleared: back to the group
    assert v["source"] == "group" and v["cap_usd"] == 15.0
    v = _set(adm, "acc_1", {"group_id": None})               # out of the group: the default
    assert v["source"] == "default" and v["cap_usd"] == 5.0
    acts = [r["action"] for r in admin.audit_rows(st)]
    assert acts[:5] == ["credit_group", "credit_clear", "credit_set", "credit_group", "group_create"]
    row = next(r for r in admin.audit_rows(st) if r["action"] == "credit_set")
    assert row["detail"]["from"] == {"cap_usd": 15.0, "source": "group", "group": "Contest judges"}
    assert row["detail"]["to"] == {"cap_usd": 20.0, "source": "personal", "group": "Contest judges"} and row["detail"]["note"] == "judge, heavy demo"
    assert row["subject_hash"] == admin.subject_hash("acc_1") and row["admin"] == "adm_1"


def test_spend_always_counts_and_add_raises_the_current_allowance(app, adm):
    st = app.state.store
    budget.record(st, "acc_1", 4.80)
    m = budget.meter(st, "acc_1")
    assert m["label"] == "$0.20 of $5 left" and not m["out"]
    budget.record(st, "acc_1", 0.30)
    assert budget.meter(st, "acc_1")["out"]
    with pytest.raises(budget.BudgetExceeded, match=r"Your \$5.00 of included Claude usage"):
        budget.check(st, "acc_1", "claude-haiku-4-5", 100, 100)
    v = _set(adm, "acc_1", {"add_usd": 5})                    # $5 more: personal $10, $5.10 spent
    assert v["source"] == "personal" and v["cap_usd"] == 10.0 and v["left_usd"] == 4.9
    budget.check(st, "acc_1", "claude-haiku-4-5", 100, 100)   # Claude runs again
    assert budget.claude_credit(st, "acc_1")["label"] == "$5.10 of your $10 used"
    v = _set(adm, "acc_1", {"allowance_usd": 2})              # lowered below the spend: out at once
    assert v["left_usd"] == 0 and budget.claude_credit(st, "acc_1")["out"]
    ad = next(r for r in admin.audit_rows(st) if r["action"] == "credit_add")
    assert ad["detail"]["add_usd"] == 5 and ad["detail"]["from"]["cap_usd"] == 5.0 and ad["detail"]["to"]["cap_usd"] == 10.0


def test_group_amount_applies_to_every_member_and_delete_releases_them(app, adm):
    st = app.state.store
    other_user(app, "acc_2", "two@example.com")
    g = _group(adm, "Beta", 8)
    r = adm.post(f"/api/admin/groups/{g['id']}/members", json={"add": ["acc_1", "acc_2"]})
    assert r.status_code == 200 and r.json()["added"] == 2 and r.json()["members"] == 2
    assert {m["id"] for m in adm.get(f"/api/admin/groups/{g['id']}/members").json()["items"]} == {"acc_1", "acc_2"}
    r = adm.post(f"/api/admin/groups/{g['id']}", json={"allowance_usd": 12, "name": "Beta testers"})
    assert r.status_code == 200 and r.json()["allowance_usd"] == 12.0 and r.json()["name"] == "Beta testers"
    assert credits.cap(st, "acc_1") == 12.0 and credits.cap(st, "acc_2") == 12.0
    g2 = _group(adm, "VIP", 30)                               # one group each: joining another moves the person
    adm.post(f"/api/admin/groups/{g2['id']}/members", json={"add": ["acc_2"]})
    assert credits.cap(st, "acc_2") == 30.0
    assert adm.post(f"/api/admin/groups/{g['id']}/members", json={"remove": ["acc_1"]}).json()["removed"] == 1
    assert credits.cap(st, "acc_1") == 5.0
    assert adm.post(f"/api/admin/groups/{g2['id']}/delete", json={"confirm": "vip"}).status_code == 400
    r = adm.post(f"/api/admin/groups/{g2['id']}/delete", json={"confirm": "VIP"})
    assert r.status_code == 200 and r.json()["members_released"] == 1
    assert credits.allowance(st, "acc_2")["source"] == "default"
    users = {u["id"]: u for u in adm.get("/api/admin/users").json()["users"]}
    assert users["acc_1"]["credit"]["cap_usd"] == 5.0 and users["acc_1"]["credit"]["source"] == "default"


def test_users_table_shows_credit_left_and_where_it_comes_from(app, adm):
    st = app.state.store
    budget.record(st, "acc_1", 1.25)
    g = _group(adm, "Judges", 15)
    _set(adm, "acc_1", {"group_id": g["id"]})
    c = {u["id"]: u for u in adm.get("/api/admin/users").json()["users"]}["acc_1"]["credit"]
    assert c == {"cap_usd": 15.0, "spent_usd": 1.25, "left_usd": 13.75, "source": "group",
                 "group": {"id": g["id"], "name": "Judges", "allowance_usd": 15.0}, "tester": False}
    v = adm.get("/api/admin/users/acc_1/credit").json()
    assert v["spent_usd"] == 1.25 and [x["name"] for x in v["groups"]] == ["Judges"]
    u = adm.get("/api/admin/usage").json()["claude"]
    assert u["default_user_usd"] == 5.0 and u["left_usd"] == 58.75


def test_validation(app, adm):
    for body in ({}, {"allowance_usd": -1}, {"allowance_usd": 501}, {"allowance_usd": "5"}, {"allowance_usd": True},
                 {"add_usd": 0}, {"add_usd": 101}, {"allowance_usd": 5, "add_usd": 5}, {"group_id": 999}, {"allowance_usd": float("nan")}):
        r = adm.post("/api/admin/users/acc_1/credit", content=json.dumps(body, allow_nan=True),
                     headers={"Content-Type": "application/json"})
        assert r.status_code in (400, 404), (body, r.status_code)
    _set(adm, "acc_1", {"allowance_usd": 498})
    assert "the most is $500" in _set(adm, "acc_1", {"add_usd": 5}, code=400)["error"]
    assert _set(adm, "nobody", {"allowance_usd": 5}, code=404)["error"] == "No such user."
    _group(adm, "Judges", 15)
    assert adm.post("/api/admin/groups", json={"name": " judges ", "allowance_usd": 5}).status_code == 400
    assert adm.post("/api/admin/groups", json={"name": "", "allowance_usd": 5}).status_code == 400
    assert adm.post("/api/admin/groups", json={"name": "x" * 61, "allowance_usd": 5}).status_code == 400
    assert adm.post("/api/admin/groups", json={"name": "Big", "allowance_usd": 900}).status_code == 400
    assert adm.post("/api/admin/groups/abc", json={"allowance_usd": 5}).status_code == 404
    assert adm.post("/api/admin/groups/99999999999", json={"allowance_usd": 5}).status_code == 404
    assert adm.post("/api/admin/groups/1/members", json={"add": ["nobody"]}).status_code == 404
    assert adm.post("/api/admin/groups/1/members", json={}).status_code == 400


def test_testers_keep_the_testing_pool(app, adm, monkeypatch):
    st = app.state.store
    monkeypatch.setenv("HQ_BRAIN_TESTERS", "acc_1")
    _set(adm, "acc_1", {"allowance_usd": 50})
    assert budget.remaining(st, "acc_1")["pool"] == "testing pool" and budget.remaining(st, "acc_1")["pool_cap_usd"] == 30.0
    assert adm.get("/api/admin/users/acc_1/credit").json()["tester"] is True


def test_app_wide_cap_still_wins(app, adm, monkeypatch):
    st = app.state.store
    monkeypatch.setenv("AI_BUDGET_TOTAL_USD", "6")
    _set(adm, "acc_1", {"allowance_usd": 100})
    budget.record(st, "someone-else", 5.99)
    with pytest.raises(budget.BudgetExceeded, match="shared AI budget"):
        budget.check(st, "acc_1", "claude-haiku-4-5", 100_000, 100_000)


def test_erasing_a_user_removes_their_group_membership(app, adm):
    st = app.state.store
    other_user(app, "acc_2", "two@example.com")
    g = _group(adm, "Beta", 8)
    adm.post(f"/api/admin/groups/{g['id']}/members", json={"add": ["acc_2"]})
    admin.erase_user(st, "acc_2", via="test")
    assert not st.q("SELECT 1 AS x FROM credit_group_members WHERE user_id='acc_2'")


# ------------------------------------------------------------------ Claude first, NVIDIA once the credit is used up
def test_out_of_credit_person_goes_straight_to_nvidia_and_a_top_up_brings_claude_back(app, adm, monkeypatch, claude_env):
    monkeypatch.setenv("NVIDIA_API_KEY", "nv-test-not-real")
    monkeypatch.delenv("GOOGLE_AI_STUDIO_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)
    st, h = app.state.store, app.state.house
    ids = lambda: [e["id"] for e in brain_llm.engines(st, "acc_1", h.s, h.http)]  # noqa: E731
    assert free_engines.configured("nvidia") and ai_engines.get_pref(st, "acc_1") == "auto"
    assert ids() == ["claude", "nvidia"]
    budget.record(st, "acc_1", 5.0)
    assert brain_llm.claude_out(st, "acc_1") and ids() == ["nvidia"]
    _set(adm, "acc_1", {"add_usd": 5})
    assert not brain_llm.claude_out(st, "acc_1") and ids() == ["claude", "nvidia"]
