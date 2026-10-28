"""Teams (M9): invites, roles, the per-channel access matrix, approvals with notes, comments, notifications, 2FA, tokens."""
import time

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

from huhqueue import teams
from huhqueue.models import User, UserSession

from .conftest import PASSWORD, Api, future

TEAM_PW = "teammate-pass-1"


def invite(api, role, email=None, name=None, access=None, **extra):
    body = {"role": role, "name": name or role.title(), "channel_access": access or {}, **extra}
    if email is not False:
        body["email"] = email or f"{(name or role).lower().replace(' ', '.')}@example.com"
    r = api.post("/api/team/invites", body)
    assert r.status_code == 200, r.text
    return r.json()


def join(app, inv, email=None, name=None, password=TEAM_PW):
    """Accept an invite in a fresh browser; returns that teammate's logged-in client."""
    c = TestClient(app)
    body = {"password": password}
    if name:
        body["name"] = name
    if email:
        body["email"] = email
    r = c.post(f"/api/invites/{inv['token']}/accept", json=body)
    assert r.status_code == 200, r.text
    return Api(c)


def member(app, api, role, access=None, name=None):
    inv = invite(api, role, name=name, access=access)
    return join(app, inv)


def login(app, email, password=TEAM_PW, code=None):
    c = TestClient(app)
    body = {"email": email, "password": password}
    if code:
        body["code"] = code
    return c, c.post("/api/auth/login", json=body)


def draft(cli, channel, caption="team draft"):
    r = cli.post("/api/posts", {"caption": caption, "channel_ids": [channel["id"]], "when": "draft"})
    assert r.status_code == 200, r.text
    return r.json()["targets"][0]


def kinds(cli, kind=None):
    return [n for n in cli.get("/api/notifications").json() if kind is None or n["kind"] == kind]


# ------------------------------------------------------------------------------------------------- owner + invites
def test_migrate_password_to_owner(app, api, core):
    """The setup password is the bootstrap Owner; a database from before teams is migrated and still logs in."""
    st = api.get("/api/auth/status").json()
    assert st["logged_in"] and st["user"]["role"] == "owner"
    with core.db.session() as s:   # simulate a pre-teams database: no users, only the legacy app-password hash
        s.execute(delete(UserSession))
        s.execute(delete(User))
    import importlib
    mig = importlib.import_module("huhqueue.migrations.0010_teams_bootstrap_owner")
    with core.db.engine.begin() as conn:
        mig.up(conn)
        assert conn.execute(text("SELECT role FROM users")).scalar() == "owner"
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200
    assert c.get("/api/auth/status").json()["user"]["role"] == "owner"
    assert c.post("/api/auth/login", json={"password": "wrong-password"}).status_code == 401


def test_invite_link_accept_and_login(app, api, channel):
    inv = invite(api, "editor", "ana@example.com", "Ana Silva")
    assert inv["invite_url"].endswith(f"#/invite/{inv['token']}") and not inv["emailed"]
    team = api.get("/api/team").json()
    assert any(m["email"] == "ana@example.com" and m["status"] == "invited" for m in team["members"])
    assert {r["id"] for r in team["roles"]} >= {"owner", "admin", "editor", "contributor", "client", "viewer"}
    anon = TestClient(app)
    info = anon.get(f"/api/invites/{inv['token']}").json()
    assert info["role"] == "editor" and info["email"] == "ana@example.com" and not info["needs_email"]
    assert anon.post(f"/api/invites/{inv['token']}/accept", json={"password": "short"}).status_code == 400
    ana = join(app, inv)
    me = ana.get("/api/me").json()["user"]
    assert me["role"] == "editor" and me["email"] == "ana@example.com" and me["can"]["approve"]
    assert anon.get(f"/api/invites/{inv['token']}").status_code == 404          # one use only
    _, r = login(app, "ana@example.com")
    assert r.status_code == 200
    _, r = login(app, "ana@example.com", "wrong-pass-123")
    assert r.status_code == 401
    assert any(n["kind"] == "team" and "accepted your invite" in n["message"] for n in kinds(api))
    assert [m["status"] for m in api.get("/api/team").json()["members"] if m["email"] == "ana@example.com"] == ["active"]


def test_link_only_invite_needs_email_on_accept(app, api):
    inv = invite(api, "client", email=False, name="Acme client")
    assert inv["member"]["email"] == "" and not inv["emailed"]
    anon = TestClient(app)
    assert anon.get(f"/api/invites/{inv['token']}").json()["needs_email"] is True
    r = anon.post(f"/api/invites/{inv['token']}/accept", json={"password": TEAM_PW})
    assert r.status_code == 400 and "email" in r.json()["detail"]["message"]
    cli = join(app, inv, email="client@acme.com", name="Cleo")
    assert cli.get("/api/me").json()["user"]["email"] == "client@acme.com"
    _, r = login(app, "client@acme.com")
    assert r.status_code == 200


def test_invite_expiry_resend_and_revoke(app, api, core):
    inv = invite(api, "contributor", "late@example.com")
    with core.db.session() as s:
        u = s.get(User, inv["member"]["id"])
        u.invite_expires_at = teams.utcnow().replace(year=2000)
    assert TestClient(app).get(f"/api/invites/{inv['token']}").status_code == 410
    again = api.post(f"/api/team/invites/{inv['member']['id']}/resend").json()
    assert again["token"] != inv["token"]
    assert TestClient(app).get(f"/api/invites/{again['token']}").status_code == 200
    assert api.delete(f"/api/team/members/{inv['member']['id']}").status_code == 200
    assert TestClient(app).get(f"/api/invites/{again['token']}").status_code == 404


def test_invite_email_sent_when_smtp_configured(api, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, u, p):
            pass

        def send_message(self, msg):
            sent.append(("msg", msg["To"], msg["Subject"], msg.get_content()))

    monkeypatch.setenv("HUHQ_SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(teams.smtplib, "SMTP", FakeSMTP)
    inv = invite(api, "editor", "mail@example.com")
    assert inv["emailed"] and api.get("/api/team").json()["smtp"] is True
    msg = [x for x in sent if x[0] == "msg"][0]
    assert msg[1] == "mail@example.com" and inv["invite_url"] in msg[3]
    # a link-only invite is never emailed
    assert invite(api, "editor", email=False)["emailed"] is False


# ------------------------------------------------------------------------------------------------- roles
@pytest.mark.parametrize("role,expect", [
    #             read  draft  schedule-direct  approve  comment  settings  team
    ("admin",       (200, 200, "scheduled",      200,     200,     200,      200)),
    ("editor",      (200, 200, "scheduled",      200,     200,     403,      403)),
    ("contributor", (200, 200, "pending_approval", 403,   200,     403,      403)),
    ("client",      (200, 403, None,             403,     200,     403,      403)),
    ("viewer",      (200, 403, None,             403,     403,     403,      403)),
])
def test_role_matrix(app, api, channel, role, expect):
    """CMS_PLAN §4.4: every role × action."""
    cli = member(app, api, role)
    pending = api.post(f"/api/targets/{draft(api, channel, 'owner draft')['id']}/request-approval", {"when": "queue"}).json()
    got = [cli.get("/api/posts").status_code]
    r = cli.post("/api/posts", {"caption": f"{role} draft", "channel_ids": [channel["id"]], "when": "draft"})
    got.append(r.status_code)
    r = cli.post("/api/posts", {"caption": f"{role} scheduled", "channel_ids": [channel["id"]], "when": "custom", "scheduled_at": future(90)})
    got.append(r.json()["targets"][0]["status"] if r.status_code == 200 else None)
    got.append(cli.post(f"/api/targets/{pending['id']}/approve").status_code)
    got.append(cli.post(f"/api/posts/{pending['post_id']}/notes", {"text": "looks good"}).status_code)
    got.append(cli.put("/api/settings", {"hashtag_limit": 7}).status_code)
    got.append(cli.get("/api/team").status_code)
    assert tuple(got) == expect


def test_contributor_cannot_schedule_only_request(app, api, channel):
    con = member(app, api, "contributor", name="Cory")
    t = draft(con, channel)
    assert con.post("/api/bulk", {"action": "queue", "ids": [t["id"]]}).status_code == 403
    assert con.post(f"/api/targets/{t['id']}/approve").status_code == 403
    r = con.patch(f"/api/targets/{t['id']}", {"when": "custom", "scheduled_at": future(60)}).json()
    assert r["status"] == "pending_approval" and r["approval"]["requested_by"] == "Cory" and r["approval"]["kind"] == "human"
    r = con.post(f"/api/targets/{t['id']}/publish-now").json()
    assert r["status"] == "pending_approval"
    assert con.get("/api/me").json()["user"]["can"]["needs_approval"] is True


def test_admin_limits_and_last_owner(app, api):
    adm = member(app, api, "admin", name="Ada")
    r = adm.post("/api/team/invites", {"email": "boss@example.com", "role": "admin"})
    assert r.status_code == 403   # only owners make admins
    owner_id = api.get("/api/me").json()["user"]["id"]
    assert adm.patch(f"/api/team/members/{owner_id}", {"role": "editor"}).status_code == 403
    assert api.patch(f"/api/team/members/{owner_id}", {"role": "editor"}).status_code == 403   # not your own role
    assert api.delete(f"/api/team/members/{owner_id}").status_code == 403
    ed = invite(api, "editor", "ed@example.com")
    assert adm.patch(f"/api/team/members/{ed['member']['id']}", {"role": "contributor"}).json()["role"] == "contributor"


def test_disabling_a_member_signs_them_out(app, api):
    inv = invite(api, "editor", "gone@example.com")
    cli = join(app, inv)
    assert cli.get("/api/me").status_code == 200
    api.patch(f"/api/team/members/{inv['member']['id']}", {"disabled": True})
    assert cli.get("/api/me").status_code == 401
    _, r = login(app, "gone@example.com")
    assert r.status_code == 401


# ------------------------------------------------------------------------------------------------- per-channel access
def test_channel_matrix_hides_and_lowers(app, api, channel):
    other = api.post("/api/connect/fake/token", {"access_token": "tok-5678", "username": "second_brand"}).json()
    ed = member(app, api, "editor", access={other["id"]: "none"}, name="Eli")
    assert [c["id"] for c in ed.get("/api/channels").json()] == [channel["id"]]
    secret = draft(api, other, "hidden")
    assert all(p["channel_id"] != other["id"] for p in ed.get("/api/posts").json())
    assert ed.get(f"/api/targets/{secret['id']}").status_code == 404
    assert ed.post("/api/posts", {"caption": "x", "channel_ids": [other["id"]], "when": "draft"}).status_code == 403
    # lower to contributor on one channel: scheduling there needs approval, elsewhere it doesn't
    eid = [m["id"] for m in api.get("/api/team").json()["members"] if m["name"] == "Eli"][0]
    api.patch(f"/api/team/members/{eid}", {"channel_access": {other["id"]: "contributor"}})
    r = ed.post("/api/posts", {"caption": "y", "channel_ids": [other["id"]], "when": "custom", "scheduled_at": future(70)}).json()
    assert r["targets"][0]["status"] == "pending_approval"
    r = ed.post("/api/posts", {"caption": "z", "channel_ids": [channel["id"]], "when": "custom", "scheduled_at": future(80)}).json()
    assert r["targets"][0]["status"] == "scheduled"
    # a cell never raises: viewer + "editor" cell stays read-only
    vw = member(app, api, "viewer", access={channel["id"]: "editor"})
    assert vw.post("/api/posts", {"caption": "no", "channel_ids": [channel["id"]], "when": "draft"}).status_code == 403


# ------------------------------------------------------------------------------------------------- approvals
def test_approval_flow_notifies_and_schedules(app, api, channel):
    con = member(app, api, "contributor", name="Cory")
    ed = member(app, api, "editor", name="Eve")
    t = draft(con, channel, "Launch day reel")
    r = con.post(f"/api/targets/{t['id']}/request-approval", {"when": "custom", "scheduled_at": future(120), "note": "for Friday"}).json()
    assert r["status"] == "pending_approval"
    # every approver is told (owner + editor), the requester is not
    assert any("Cory asked for approval" in n["message"] for n in kinds(ed, "approval"))
    assert any("Cory asked for approval" in n["message"] for n in kinds(api, "approval"))
    assert not kinds(con, "approval")
    assert [p["id"] for p in ed.get("/api/posts?status=pending_approval").json()] == [t["id"]]
    out = ed.post(f"/api/targets/{t['id']}/approve", {"note": "Ship it"}).json()
    assert out["status"] == "scheduled" and out["approval"]["decided_by"] == "Eve" and out["approval"]["decision_note"] == "Ship it"
    msgs = [n["message"] for n in kinds(con, "approval")]
    assert any("Eve approved your post: Ship it" in m for m in msgs)
    assert any("Approved by Eve" in e["message"] for e in api.get(f"/api/targets/{t['id']}/events").json())


def test_reject_requires_note(app, api, channel):
    con = member(app, api, "contributor", name="Cory")
    t = draft(con, channel)
    con.patch(f"/api/targets/{t['id']}", {"when": "queue"})
    r = api.post(f"/api/targets/{t['id']}/reject", {})
    assert r.status_code == 400 and "note" in r.json()["detail"]["message"]
    out = api.post(f"/api/targets/{t['id']}/reject", {"note": "Swap the thumbnail"}).json()
    assert out["status"] == "draft" and out["approval"]["reject_note"] == "Swap the thumbnail"
    assert any("asked for changes: Swap the thumbnail" in n["message"] for n in kinds(con, "approval"))
    # resubmitting after changes works
    assert con.patch(f"/api/targets/{t['id']}", {"caption": "new thumb", "when": "queue"}).json()["status"] == "pending_approval"


def test_editor_cannot_approve_on_contributor_channel(app, api, channel):
    ed = member(app, api, "editor", access={channel["id"]: "contributor"})
    t = draft(api, channel)
    api.post(f"/api/targets/{t['id']}/request-approval", {"when": "queue"})
    assert ed.post(f"/api/targets/{t['id']}/approve").status_code == 403


# ------------------------------------------------------------------------------------------------- comments + mentions
def test_mention_creates_notification(app, api, channel):
    ana = member(app, api, "editor", name="Ana Silva")
    bo = member(app, api, "editor", name="Bo Chen")
    t = draft(api, channel)
    api.post(f"/api/posts/{t['post_id']}/notes", {"text": "@anasilva can you check the thumbnail?"})
    assert any(n["kind"] == "mention" and "Owner mentioned you" in n["message"] for n in kinds(ana))
    assert not kinds(bo, "mention")
    # first-name mentions work too
    api.post(f"/api/posts/{t['post_id']}/notes", {"text": "@bo thoughts?"})
    assert kinds(bo, "mention")


def test_comments_thread_notifies_participants(app, api, channel):
    con = member(app, api, "contributor", name="Cory")
    cli = member(app, api, "client", name="Cleo")
    t = draft(con, channel)
    con.post(f"/api/targets/{t['id']}/request-approval", {"when": "queue"})
    n = cli.post(f"/api/posts/{t['post_id']}/notes", {"text": "Can we use the blue logo?"}).json()
    assert n["author"] == "Cleo" and n["author_id"]
    assert any(x["kind"] == "comment" and "Cleo commented" in x["message"] for x in kinds(con))
    con.post(f"/api/posts/{t['post_id']}/notes", {"text": "Done - swapped it"})
    assert any(x["kind"] == "comment" and "Cory commented" in x["message"] for x in kinds(cli))
    notes = api.get(f"/api/posts/{t['post_id']}/notes").json()
    assert [x["text"] for x in notes] == ["Can we use the blue logo?", "Done - swapped it"]
    # you delete your own comment; not someone else's (owners/admins can)
    assert con.delete(f"/api/posts/{t['post_id']}/notes/{n['id']}").status_code == 403
    assert cli.delete(f"/api/posts/{t['post_id']}/notes/{n['id']}").status_code == 200
    assert len(api.get(f"/api/posts/{t['post_id']}/notes").json()) == 1


def test_notifications_read_state_is_per_teammate(app, api, core, channel):
    ed = member(app, api, "editor")
    from huhqueue.models import Event
    with core.db.session() as s:
        s.add(Event(level="error", kind="failed", message="broadcast failure", notify=True))
    assert any(not n["read"] for n in kinds(api))
    api.post("/api/notifications/read")
    assert all(n["read"] for n in kinds(api))
    assert any(not n["read"] and n["message"] == "broadcast failure" for n in kinds(ed))


# ------------------------------------------------------------------------------------------------- 2FA + tokens
def test_totp_enrol_and_login(app, api):
    inv = invite(api, "editor", "totp@example.com")
    cli = join(app, inv)
    setup = cli.post("/api/account/2fa/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and setup["qr"].startswith("data:image/png;base64,")
    totp = pyotp.TOTP(setup["secret"])
    assert cli.post("/api/account/2fa/enable", {"code": "000000"}).status_code == 400
    codes = cli.post("/api/account/2fa/enable", {"code": totp.now()}).json()["recovery_codes"]
    assert len(codes) == 10
    _, r = login(app, "totp@example.com")
    assert r.status_code == 401 and r.json()["detail"]["needs_2fa"]
    time.sleep(0)   # the enable step consumed the current time-step; use the next window's code
    _, r = login(app, "totp@example.com", code=totp.at(time.time() + 30))
    assert r.status_code == 200
    _, r = login(app, "totp@example.com", code=codes[0])
    assert r.status_code == 200
    _, r = login(app, "totp@example.com", code=codes[0])   # recovery codes work once
    assert r.status_code == 401
    # an owner can reset a lost phone
    api.post(f"/api/team/members/{inv['member']['id']}/reset-2fa")
    _, r = login(app, "totp@example.com")
    assert r.status_code == 200


def test_token_capped_by_user_role(app, api, channel, core):
    con = member(app, api, "contributor", name="Cory")
    assert con.post("/api/tokens", {"name": "nope"}).status_code == 403          # contributors can't mint tokens
    # a teammate's token never exceeds their role: an editor made an admin-scoped token, then was demoted to contributor
    ed_inv = invite(api, "editor", "eddie@example.com", "Eddie")
    ed = join(app, ed_inv)
    api.patch(f"/api/team/members/{ed_inv['member']['id']}", {"role": "admin"})
    tok = ed.post("/api/tokens", {"name": "my-agent", "scopes": ["read", "write", "publish", "comments", "admin"],
                                  "require_approval": False})
    assert tok.status_code == 200, tok.text
    bot = TestClient(app, headers={"Authorization": f"Bearer {tok.json()['token']}"})
    assert bot.put("/api/settings", json={"hashtag_limit": 4}).status_code == 200
    api.patch(f"/api/team/members/{ed_inv['member']['id']}", {"role": "contributor"})
    assert bot.put("/api/settings", json={"hashtag_limit": 3}).status_code == 403
    r = bot.post("/api/posts", json={"caption": "agent", "channel_ids": [channel["id"]], "when": "custom", "scheduled_at": future(50)})
    assert r.status_code == 200 and r.json()["targets"][0]["status"] == "pending_approval"
    api.patch(f"/api/team/members/{ed_inv['member']['id']}", {"disabled": True})
    assert bot.get("/api/posts").status_code == 401


def test_team_routes_need_a_person(app, api):
    raw = api.post("/api/tokens", {"name": "ops", "scopes": ["read", "write", "publish", "comments", "admin"]}).json()["token"]
    bot = TestClient(app, headers={"Authorization": f"Bearer {raw}"})
    assert bot.get("/api/team").status_code == 403
    assert bot.post("/api/team/invites", json={"email": "x@example.com"}).status_code == 403
