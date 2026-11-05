"""Video-link approvals, 5 Nov 2026 (owner report + asks). UI = REST = MCP.

1. The bug: Approve (and Reject) on a "Claude wants One take to fetch a video" card answered "No pending approval with that
   id" (approval 136 on the live app: GET /api/approvals listed it as pending, POST approve and reject both 404). The
   decision re-read ``decided`` and compared it with the float it had just written using ``==``; through the production
   database the time did not come back bit-identical, so every decision rolled back as "someone else decided it". It now
   uses the UPDATE's changed-row count (``Store.xn``). The tests below make the store hand times back rounded: they fail
   on main and pass here.
2. approve_link_ingest / reject_link_ingest / list_pending_approvals over MCP: owner-scoped (404 for a foreign, missing,
   decided or non-link id), approve needs ``i_own_or_have_rights: true`` (else 400), and the consent record stores which
   app approved it and when.
3. Settings -> Auto-approve video links: off by default, the rights statement and its time recorded when turned on, every
   app or picked apps, revocable, logged as auto-approved; it never covers paid APIs and never posts.
"""
import json
import time

import pytest
from fastapi.testclient import TestClient

from studio import consent, link_ingest, mcp

from .conftest import agent_token, mcp_call
from .test_take_opus import YT, _github, _hosts

YT2 = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
URL = "/api/settings/link-auto-approve"


def _lossy_times(monkeypatch, store):
    """Times read back rounded to 15 significant digits, as the production database connection returned them."""
    real = store.q

    def q(sql, args=()):
        out = []
        for r in real(sql, args):
            d = dict(r)
            for k in ("decided", "created"):
                if isinstance(d.get(k), float):
                    d[k] = float(f"{d[k]:.15g}")
            out.append(d)
        return out
    monkeypatch.setattr(store, "q", q)
    # a time with more than 15 significant digits, so the rounding always shows (time.time() usually has 16-17)
    monkeypatch.setattr(consent.time, "time", lambda: 1762338000.1234567)


def _ask(human, tok, url=YT, mid=1):
    res, out = mcp_call(human, tok, "ingest_video_link", {"url": url}, mid=mid)
    assert not res["isError"], out
    assert out["needs_user_approval"] is True
    return out["approval_id"]


def _consents(app):
    st = app.state.store
    link_ingest.ensure(st)
    return [dict(r) for r in st.q("SELECT * FROM link_consents ORDER BY id")]


def _err(human, tok, name, args, mid=9):
    res, out = mcp_call(human, tok, name, args, mid=mid)
    assert res["isError"], out
    return res["structuredContent"]["status"], out


def _status(app, aid):
    return app.state.store.q("SELECT status FROM approvals WHERE id=?", (aid,))[0]["status"]


# ------------------------------------------------------------------------------------------------ 1. the bug
def test_approve_works_when_the_database_hands_times_back_rounded(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = _ask(human, tok)
    _lossy_times(monkeypatch, app.state.store)
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True, "attestation_version": link_ingest.ATTESTATION_VERSION})
    assert r.status_code == 200, r.text                      # main: 404 "No pending approval with that id."
    assert r.json()["status"] == "approved"
    c = _consents(app)
    assert len(c) == 1 and c[0]["approval_id"] == aid and c[0]["approved_by_app"] == "HuhQueue" and c[0]["approved_at"]
    monkeypatch.undo()
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    t = mcp_call(human, tok, "ingest_video_link", {"url": YT, "approval_id": aid}, mid=3)[1]
    assert t["input"]["consent_id"] == c[0]["id"] and len(sent) == 1


def test_reject_works_when_the_database_hands_times_back_rounded(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = _ask(human, tok)
    _lossy_times(monkeypatch, app.state.store)
    r = human.post(f"/api/approvals/{aid}/reject")
    assert r.status_code == 200, r.text
    assert _status(app, aid) == "rejected"
    # a second decision on the same item is still refused (the changed-row count is 0), and nothing was recorded
    assert human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True}).status_code == 404
    assert _consents(app) == []


def test_a_paid_api_approval_decides_with_the_changed_row_count_too(human, app, monkeypatch):
    tok = agent_token(human, policy="trusted", cap=50)
    _, wait = mcp_call(human, tok, "run_agent", {"query": "best headphones", "server_ai": True})
    assert wait["needs_user_approval"] is True
    _lossy_times(monkeypatch, app.state.store)
    r = human.post(f"/api/approvals/{wait['approval_id']}/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "approved", r.text
    assert human.post(f"/api/approvals/{wait['approval_id']}/approve", json={}).status_code == 404


def test_xn_returns_the_number_of_rows_changed(app):
    st = app.state.store
    aid = st.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
               ("u1", 1, "x", "{}", "pending", time.time()))
    assert st.xn("UPDATE approvals SET status='approved' WHERE id=? AND status='pending'", (aid,)) == 1
    assert st.xn("UPDATE approvals SET status='approved' WHERE id=? AND status='pending'", (aid,)) == 0


# ------------------------------------------------------------------------------------------------ 2. MCP approve / reject
def test_tools_list_has_the_link_approval_tools(human):
    tok = agent_token(human)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"approve_link_ingest", "reject_link_ingest", "list_pending_approvals"} <= names
    tool = next(t for t in mcp.TOOLS if t["name"] == "approve_link_ingest")
    assert tool["inputSchema"]["required"] == ["approval_id", "i_own_or_have_rights"]
    assert "list_pending_approvals" in mcp.READ_ONLY and "approve_link_ingest" not in mcp.READ_ONLY
    doc = open("docs/MCP_TOOLS.md").read()
    for n in ("approve_link_ingest", "reject_link_ingest", "list_pending_approvals"):
        assert f"`{n}`" in doc, n


def test_an_app_approves_a_link_only_with_the_rights_flag_and_the_record_names_it(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = _ask(human, tok)
    pend = mcp_call(human, tok, "list_pending_approvals", {}, mid=2)[1]["pending"]
    assert [p["approval_id"] for p in pend] == [aid] and pend[0]["url"] == YT and pend[0]["attestation"] == link_ingest.ATTESTATION
    for bad in ({}, {"i_own_or_have_rights": False}, {"i_own_or_have_rights": "true"}, {"i_own_or_have_rights": 1}):
        st, msg = _err(human, tok, "approve_link_ingest", {"approval_id": aid, **bad})
        assert st == 400 and "i_own_or_have_rights" in msg
    assert _consents(app) == [] and sent == []
    for bad_id in ("12", None, True, 1.5):
        st, _ = _err(human, tok, "approve_link_ingest", {"approval_id": bad_id, "i_own_or_have_rights": True})
        assert st == 400
    ok = mcp_call(human, tok, "approve_link_ingest", {"approval_id": aid, "i_own_or_have_rights": True}, mid=3)[1]
    assert ok["status"] == "approved" and ok["decided_by"] == "Claude"
    c = _consents(app)
    assert len(c) == 1 and c[0]["via"] == "mcp_app_approval" and c[0]["approved_by_app"] == "Claude" and c[0]["approved_at"] > 0
    row = app.state.store.q("SELECT payload FROM approvals WHERE id=?", (aid,))[0]
    assert json.loads(row["payload"])["decided_by"]["app"] == "Claude"
    acts = app.state.store.q("SELECT action, detail FROM audit WHERE action='approve_link_rights'")
    assert acts and json.loads(acts[0]["detail"])["by"] == "Claude"
    # decided: a second approve is 404, and the take starts once with that consent
    st, _ = _err(human, tok, "approve_link_ingest", {"approval_id": aid, "i_own_or_have_rights": True})
    assert st == 404
    t = mcp_call(human, tok, "ingest_video_link", {"url": YT, "approval_id": aid}, mid=4)[1]
    assert t["input"]["consent_id"] == c[0]["id"] and len(sent) == 1


def test_reject_link_ingest_and_foreign_missing_or_other_kind_ids_are_404(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = _ask(human, tok)
    st = app.state.store
    theirs = st.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                  ("someone-else", 999, "Other", json.dumps({"kind": "link_rights", "url": YT2}), "pending", time.time()))
    _, wait = mcp_call(human, tok, "run_agent", {"query": "x", "server_ai": True}, mid=2)
    paid = wait["approval_id"]
    for name in ("approve_link_ingest", "reject_link_ingest"):
        for other in (theirs, aid + 999, paid):
            code, msg = _err(human, tok, name, {"approval_id": other, "i_own_or_have_rights": True})
            assert code == 404 and "Other" not in msg and YT2 not in msg, (name, other, msg)
    assert _status(app, theirs) == "pending" and _status(app, paid) == "pending"   # paid stays in the app
    assert all(p["approval_id"] != theirs for p in mcp_call(human, tok, "list_pending_approvals", {}, mid=3)[1]["pending"])
    out = mcp_call(human, tok, "reject_link_ingest", {"approval_id": aid}, mid=4)[1]
    assert out["status"] == "rejected" and _consents(app) == [] and sent == []
    assert _status(app, aid) == "rejected"


def test_the_kill_switch_stops_approve_link_ingest(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = _ask(human, tok)
    assert human.post("/api/safety/kill", json={"on": True}).status_code == 200
    res, _ = mcp_call(human, tok, "approve_link_ingest", {"approval_id": aid, "i_own_or_have_rights": True}, mid=2)
    assert res["isError"] and _consents(app) == [] and _status(app, aid) == "pending"


# ------------------------------------------------------------------------------------------------ 3. auto-approve setting
def _token_id(app, name="Claude"):
    return app.state.store.q("SELECT id FROM agent_tokens WHERE name=? ORDER BY id DESC LIMIT 1", (name,))[0]["id"]


def test_auto_approve_is_off_by_default_and_validates_types(human, app):
    tok = agent_token(human)
    g = human.get(URL).json()
    assert g["on"] is False and g["attestation"] == link_ingest.ATTESTATION and g["available_apps"]
    for body in ({"on": "yes"}, {"on": True, "scope": "some"}, {"on": True, "apps": "1"}, {"on": True, "scope": "apps", "apps": [True]},
                 {"on": True, "scope": "apps", "apps": ["1"]}, {"on": True, "rights_attested": "true"},
                 {"on": True, "rights_attested": True, "attestation_version": 5}, {"on": True, "apps": list(range(101))}, [1, 2]):
        r = human.put(URL, json=body)
        assert r.status_code == 400, (body, r.text)
    r = human.put(URL, content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400
    # foreign app ids: 400 without saying whose
    r = human.put(URL, json={"on": True, "scope": "apps", "apps": [99999], "rights_attested": True})
    assert r.status_code == 400 and "connected to your account" in r.json()["error"]
    # turning it on needs the statement
    assert human.put(URL, json={"on": True}).status_code == 403
    assert human.get(URL).json()["on"] is False
    # an AI app can read whether it is covered but never change it
    bot = TestClient(app, base_url="https://hq.test")
    r = bot.put(URL, headers={"Authorization": f"Bearer {tok}"}, json={"on": True, "rights_attested": True})
    assert r.status_code == 403
    assert bot.get(URL, headers={"Authorization": f"Bearer {tok}"}).json()["on"] is False


def test_auto_approve_on_for_all_apps_starts_the_take_and_logs_it(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    r = human.put(URL, json={"on": True, "scope": "all", "rights_attested": True, "attestation_version": link_ingest.ATTESTATION_VERSION})
    assert r.status_code == 200 and r.json()["on"] is True and r.json()["attested_at"]
    t = mcp_call(human, tok, "ingest_video_link", {"url": YT})[1]
    assert t.get("id") and t["input"]["consent_id"] and len(sent) == 1   # no Inbox item to wait for
    c = _consents(app)
    assert len(c) == 1 and c[0]["via"] == "auto_approve" and c[0]["approved_by_app"] == "Claude"
    row = dict(app.state.store.q("SELECT status, payload, result FROM approvals WHERE id=?", (c[0]["approval_id"],))[0])
    assert row["status"] == "approved" and json.loads(row["payload"])["auto_approved"] is True and row["result"]
    assert human.get("/api/approvals").json() == []                     # nothing waits in the Inbox
    acts = {r["action"]: json.loads(r["detail"] or "{}") for r in app.state.store.q("SELECT action, detail FROM audit")}
    assert acts["auto_approve_link_rights"]["auto_approved"] is True and "link_auto_approve_on" in acts
    # never covers paid APIs: server AI still asks the person
    _, wait = mcp_call(human, tok, "run_agent", {"query": "x", "server_ai": True}, mid=2)
    assert wait["needs_user_approval"] is True
    # revocable: off -> the next link asks again
    r = human.put(URL, json={"on": False})
    assert r.status_code == 200 and r.json()["on"] is False and r.json()["revoked_at"]
    again = mcp_call(human, tok, "ingest_video_link", {"url": YT2}, mid=3)[1]
    assert again["needs_user_approval"] is True and len(sent) == 1


def test_auto_approve_for_picked_apps_only_and_a_new_statement_stops_it(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    other = human.post("/api/agent-tokens", json={"name": "ChatGPT", "policy": "trusted", "daily_cap": 50}).json()["token"]
    r = human.put(URL, json={"on": True, "scope": "apps", "apps": [_token_id(app, "ChatGPT")], "rights_attested": True})
    assert r.status_code == 200 and r.json()["scope"] == "apps"
    assert mcp_call(human, tok, "ingest_video_link", {"url": YT})[1]["needs_user_approval"] is True   # Claude: not picked
    assert mcp_call(human, other, "ingest_video_link", {"url": YT}, mid=2)[1].get("id") and len(sent) == 1
    monkeypatch.setattr(link_ingest, "ATTESTATION_VERSION", "2099-01-01.1")   # the statement changed: tick it again
    assert human.get(URL).json()["statement_changed"] is True
    assert mcp_call(human, other, "ingest_video_link", {"url": YT2}, mid=3)[1]["needs_user_approval"] is True


def test_the_setting_is_deleted_with_the_account(human, app):
    agent_token(human)
    assert human.put(URL, json={"on": True, "rights_attested": True}).status_code == 200
    uid = app.state.store.q("SELECT id FROM users")[0]["id"]
    app.state.store.delete_user(uid)
    assert app.state.store.q("SELECT COUNT(*) AS n FROM link_auto_approve")[0]["n"] == 0


@pytest.mark.parametrize("path", ["/privacy", "/docs/api"])
def test_privacy_and_api_docs_mention_auto_approve(client, path):
    body = client.get(path).text
    assert "uto-approve video links" in body or "link-auto-approve" in body
