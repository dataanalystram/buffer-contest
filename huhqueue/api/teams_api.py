"""Settings › Team (members, roles, the per-channel access matrix, invites) and Settings › Security (2FA, sessions).

Every route is in the action registry; all of them are `secret` (UI only - never MCP, the palette or the LLM),
except the read-only teammate directory used for @mentions.
"""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from .. import actions as reg
from .. import teams
from ..actions import B, O, S, Action
from ..models import Channel, User, UserSession
from ..security import SESSION_COOKIE, verify_password


def _err(status: int, message: str, **extra) -> JSONResponse:
    return JSONResponse({"detail": {"message": message, **extra}}, status_code=status)


def add_team_routes(app: FastAPI, core, auth, set_session) -> None:
    def me(request: Request) -> teams.Who:
        who = getattr(request.state, "user", None)
        if not who or getattr(request.state, "via", None) != "session":
            raise HTTPException(403, "Team and security settings can only be changed by a person in the web UI")
        return who

    def _base(request: Request) -> str:
        origin = request.headers.get("origin") or ""
        return origin.rstrip("/") if origin.startswith("http") else str(request.base_url).rstrip("/")

    @app.exception_handler(teams.TeamError)
    async def _team_error(_, e: teams.TeamError):
        return _err(e.status, str(e))

    # ------------------------------------------------------------------ team (owners + admins)
    @app.get("/api/team", tags=["team"])
    def team_get(request: Request):
        who = me(request)
        with core.db.session() as s:
            members = [teams.member_dict(u) for u in s.scalars(select(User).order_by(User.created_at))]
            chans = [{"id": c.id, "provider": c.provider, "username": c.username, "avatar_url": c.avatar_url}
                     for c in s.scalars(select(Channel).order_by(Channel.created_at))]
        return {"members": members, "channels": chans, "roles": [{"id": r, "label": teams.ROLE_LABELS[r]} for r in teams.ROLES],
                "cells": list(teams.CELL_VALUES), "me": who.to_dict(), "smtp": bool(teams.smtp_config())}

    @app.post("/api/team/invites", tags=["team"])
    def team_invite(request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            u, raw = teams.invite(s, who, payload.get("email", ""), payload.get("name", ""), payload.get("role", "contributor"),
                                  payload.get("channel_access") or {})
            out = teams.member_dict(u)
        link = f"{_base(request)}/#/invite/{raw}"
        sent, err = (False, "")
        if payload.get("send_email", True) and out["email"]:
            sent, err = teams.send_invite_email(out["email"], link, who.name, out["role"])
        return {"member": out, "invite_url": link, "token": raw, "emailed": sent, "email_error": err,
                "note": "Emailed." if sent else "Copy the link and send it yourself - it works once and expires in 7 days."}

    @app.post("/api/team/invites/{uid}/resend", tags=["team"])
    def team_invite_resend(uid: str, request: Request):
        who = me(request)
        with core.db.session() as s:
            u = s.get(User, uid)
            if not u or u.accepted_at:
                raise teams.TeamError("No pending invite for that person", 404)
            u2, raw = teams.invite(s, who, u.email, u.name, u.role, u.channel_access)
            out = teams.member_dict(u2)
        link = f"{_base(request)}/#/invite/{raw}"
        sent, err = teams.send_invite_email(out["email"], link, who.name, out["role"]) if out["email"] else (False, "")
        return {"member": out, "invite_url": link, "token": raw, "emailed": sent, "email_error": err}

    @app.patch("/api/team/members/{uid}", tags=["team"])
    def team_member_update(uid: str, request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            return teams.member_dict(teams.update_member(s, who, uid, payload))

    @app.delete("/api/team/members/{uid}", tags=["team"])
    def team_member_remove(uid: str, request: Request):
        who = me(request)
        with core.db.session() as s:
            teams.remove_member(s, who, uid)
        return {"ok": True}

    @app.post("/api/team/members/{uid}/reset-2fa", tags=["team"])
    def team_member_reset_2fa(uid: str, request: Request):
        who = me(request)
        with core.db.session() as s:
            u = s.get(User, uid)
            if not u:
                raise teams.TeamError("Member not found", 404)
            teams._may_touch(who, u)
            teams.totp_disable(s, uid)
            teams.revoke_sessions(s, uid)
            teams.notify_user(s, uid, f"{who.name} turned off two-factor sign-in for you - set it up again in Settings › Security",
                              kind="security", level="warning")
        return {"ok": True}

    @app.get("/api/team/directory", tags=["team"])
    def team_directory():
        """Teammates you can @mention (any signed-in role)."""
        with core.db.session() as s:
            return [{"id": u.id, "name": u.name, "handle": u.handle, "role": u.role}
                    for u in s.scalars(select(User).where(User.disabled.is_(False)).order_by(User.name)) if u.accepted_at]

    # ------------------------------------------------------------------ invites (public: the token is the credential)
    @app.get("/api/invites/{token}", tags=["team"])
    def invite_get(token: str):
        with core.db.session() as s:
            u = teams.find_invite(s, token)
            return {"email": teams.public_email(u.email), "needs_email": not teams.public_email(u.email), "name": u.name,
                    "role": u.role, "role_label": teams.ROLE_LABELS.get(u.role, ""), "invited_by": u.invited_by,
                    "expires_at": teams._iso(u.invite_expires_at)}

    @app.post("/api/invites/{token}/accept", tags=["team"])
    def invite_accept(token: str, request: Request, payload: dict = Body(...)):
        with core.db.session() as s:
            u = teams.accept_invite(s, token, payload.get("password", ""), payload.get("name", ""), payload.get("email", ""))
            if u.invited_by_id:
                teams.notify_user(s, u.invited_by_id, f"{u.name} accepted your invite and joined as {u.role}", kind="team", level="success")
            uid, out = u.id, teams.member_dict(u)
        return set_session(JSONResponse({"ok": True, "member": out}), uid, request)

    # ------------------------------------------------------------------ my account: profile, 2FA, sessions
    @app.get("/api/account", tags=["account"])
    def account_get(request: Request):
        who = me(request)
        with core.db.session() as s:
            u = s.get(User, who.id)
            return {**teams.member_dict(u), "recovery_codes_left": len(u.recovery_hashes or []) if u.totp_enabled else 0}

    @app.patch("/api/account", tags=["account"])
    def account_update(request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            u = s.get(User, who.id)
            if "name" in payload and str(payload["name"]).strip():
                u.name = str(payload["name"]).strip()[:80]
                u.handle = teams.make_handle(s, u.name, u.email, exclude_id=u.id)
            if "email" in payload:
                email = str(payload["email"] or "").strip().lower()
                if "@" not in email:
                    raise teams.TeamError("Enter a valid email address")
                other = s.scalar(select(User).where(User.email == email))
                if other and other.id != u.id:
                    raise teams.TeamError("Someone on the team already uses that email", 409)
                u.email = email
            return teams.member_dict(u)

    @app.post("/api/account/2fa/setup", tags=["account"])
    def twofa_setup(request: Request):
        who = me(request)
        with core.db.session() as s:
            return teams.totp_begin(core, s, who.id)

    @app.post("/api/account/2fa/enable", tags=["account"])
    def twofa_enable(request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            codes = teams.totp_enable(core, s, who.id, str(payload.get("code") or ""))
        return {"ok": True, "recovery_codes": codes, "note": "Save these recovery codes now - each works once and they are shown only once."}

    def _reauth(s, who, payload) -> None:
        u = s.get(User, who.id)
        pw, code = str(payload.get("password") or ""), str(payload.get("code") or "")
        if pw and verify_password(pw, u.password_hash or ""):
            return
        if code and teams.verify_totp(core, s, u, code):
            return
        raise teams.TeamError("Confirm with your password or a current 2FA code", 401)

    @app.post("/api/account/2fa/disable", tags=["account"])
    def twofa_disable(request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            _reauth(s, who, payload)
            teams.totp_disable(s, who.id)
        return {"ok": True}

    @app.post("/api/account/2fa/recovery-codes", tags=["account"])
    def twofa_recovery(request: Request, payload: dict = Body(...)):
        who = me(request)
        with core.db.session() as s:
            u = s.get(User, who.id)
            if not u.totp_enabled:
                raise teams.TeamError("Turn on two-factor first")
            _reauth(s, who, payload)
            codes, hashes = teams.new_recovery_codes()
            u.recovery_hashes = hashes
        return {"ok": True, "recovery_codes": codes}

    @app.get("/api/account/sessions", tags=["account"])
    def sessions_list(request: Request):
        who = me(request)
        with core.db.session() as s:
            rows = s.scalars(select(UserSession).where(UserSession.user_id == who.id, UserSession.revoked_at.is_(None))
                             .order_by(UserSession.last_seen_at.desc()))
            return [{"id": r.id, "current": r.id == who.session_id, "ip": r.ip, "user_agent": r.user_agent,
                     "created_at": teams._iso(r.created_at), "last_seen_at": teams._iso(r.last_seen_at)} for r in rows]

    @app.delete("/api/account/sessions/{sid}", tags=["account"])
    def session_revoke(sid: str, request: Request):
        who = me(request)
        with core.db.session() as s:
            row = s.get(UserSession, sid)
            if not row or row.user_id != who.id:
                raise teams.TeamError("Session not found", 404)
            row.revoked_at = teams.utcnow()
        return {"ok": True, "was_current": sid == who.session_id}

    @app.post("/api/account/sessions/revoke-others", tags=["account"])
    def sessions_revoke_others(request: Request):
        who = me(request)
        with core.db.session() as s:
            n = teams.revoke_sessions(s, who.id, keep=who.session_id)
        return {"ok": True, "revoked": n}


# --------------------------------------------------------------------------------------------- registry entries
_SEC = dict(scope="admin", risk="secret")
for _a in (
    Action("team.get", "Team members, roles, channels and invites", "GET", "/api/team", ui="Settings › Team", **_SEC),
    Action("team.invite", "Invite a teammate or client (a link; emailed when an address is given and SMTP is set)", "POST",
           "/api/team/invites", {"email": S, "name": S, "role": S, "channel_access": O, "send_email": B}, ui="Settings › Team › Invite", **_SEC),
    Action("team.invite_resend", "Make a new invite link", "POST", "/api/team/invites/{id}/resend", {"id": S}, ("id",),
           ui="Settings › Team › Resend", **_SEC),
    Action("team.member_update", "Change a teammate's role, channel access or status", "PATCH", "/api/team/members/{id}",
           {"id": S, "role": S, "channel_access": O, "disabled": B, "name": S}, ("id",), ui="Settings › Team", **_SEC),
    Action("team.member_remove", "Remove a teammate", "DELETE", "/api/team/members/{id}", {"id": S}, ("id",),
           ui="Settings › Team › Remove", **_SEC),
    Action("team.member_reset_2fa", "Turn off a teammate's 2FA (lost phone)", "POST", "/api/team/members/{id}/reset-2fa", {"id": S}, ("id",),
           ui="Settings › Team", **_SEC),
    Action("team.directory", "Teammates you can @mention", "GET", "/api/team/directory", ui="Composer › Notes"),
    Action("invite.get", "Look up an invite link", "GET", "/api/invites/{token}", {"token": S}, ("token",), ui="Invite page", **_SEC),
    Action("invite.accept", "Accept an invite (set your password)", "POST", "/api/invites/{token}/accept",
           {"token": S, "name": S, "password": S, "email": S}, ("token", "password"), ui="Invite page", **_SEC),
    Action("account.get", "My account", "GET", "/api/account", ui="Settings › Security", **_SEC),
    Action("account.update", "Edit my name / email", "PATCH", "/api/account", {"name": S, "email": S}, ui="Settings › Security", **_SEC),
    Action("account.2fa_setup", "Start two-factor setup (QR code)", "POST", "/api/account/2fa/setup", ui="Settings › Security", **_SEC),
    Action("account.2fa_enable", "Turn on two-factor with a code", "POST", "/api/account/2fa/enable", {"code": S}, ("code",),
           ui="Settings › Security", **_SEC),
    Action("account.2fa_disable", "Turn off two-factor", "POST", "/api/account/2fa/disable", {"password": S, "code": S},
           ui="Settings › Security", **_SEC),
    Action("account.2fa_recovery", "New recovery codes", "POST", "/api/account/2fa/recovery-codes", {"password": S, "code": S},
           ui="Settings › Security", **_SEC),
    Action("account.sessions", "My signed-in sessions", "GET", "/api/account/sessions", ui="Settings › Security", **_SEC),
    Action("account.session_revoke", "Sign out one session", "DELETE", "/api/account/sessions/{id}", {"id": S}, ("id",),
           ui="Settings › Security", **_SEC),
    Action("account.sessions_revoke_others", "Sign out everywhere else", "POST", "/api/account/sessions/revoke-others",
           ui="Settings › Security", **_SEC),
    Action("nav.team", "Go to Team settings", nav="#/settings?section=team", mcp=False, ui="⌘K"),
):
    reg.register(_a)
