"""Teams: users, roles, the per-channel access matrix, invites, TOTP 2FA and recovery codes.

Roles (highest first):
  owner        everything, including other owners and admins; the first (setup) password belongs to the bootstrap Owner
  admin        everything except changing owners / other admins
  editor       create, schedule, publish, reply, approve on the channels they can edit
  contributor  create drafts and submit them for approval; cannot publish or schedule directly
  client       view only, plus notes/comments on posts (a client reviewing the calendar)
  viewer       read only

A per-channel cell (`User.channel_access[channel_id]`) can lower a user's role on that channel (never raise it):
"none" hides the channel entirely. Owners and admins always have full access to every channel.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import logging
import os
import re
import secrets
import smtplib
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import select

from .models import KV, Channel, Comment, Event, Post, PostTarget, User, UserSession, utcnow

log = logging.getLogger("huhqueue.teams")

ROLES = ("owner", "admin", "editor", "contributor", "client", "viewer")
RANK = {"none": 0, "viewer": 1, "client": 1, "contributor": 2, "editor": 3, "admin": 4, "owner": 5}
CELL_VALUES = ("none", "viewer", "contributor", "editor")
ROLE_LABELS = {
    "owner": "Everything, including managing other owners and admins.",
    "admin": "Everything except managing owners and other admins.",
    "editor": "Create, schedule, publish, reply and approve on their channels.",
    "contributor": "Create drafts and submit them for approval.",
    "client": "View only, and can leave comments on posts.",
    "viewer": "Read only.",
}
ROLE_SCOPES = {
    "owner": ["read", "write", "publish", "comments", "admin"],
    "admin": ["read", "write", "publish", "comments", "admin"],
    "editor": ["read", "write", "publish", "comments"],
    "contributor": ["read", "write", "comments"],
    "client": ["read"],
    "viewer": ["read"],
}
INVITE_TTL = timedelta(days=7)
OWNER_EMAIL = "owner@huhqueue.local"
PENDING_EMAIL = "@invite.pending"   # link-only invites get a placeholder address until the invitee picks theirs
RECOVERY_CODES = 10
CURRENT_USER: ContextVar[str | None] = ContextVar("huhq_current_user", default=None)   # user id of this request


class TeamError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _h(x: str) -> str:
    return hashlib.sha256((x or "").encode()).hexdigest()


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() + "Z" if d else None


def make_handle(s, name: str, email: str, exclude_id: str | None = None) -> str:
    base = re.sub(r"[^a-z0-9_.\-]", "", (name or "").lower().replace(" ", "")) or \
        re.sub(r"[^a-z0-9_.\-]", "", (email or "").split("@")[0].lower())
    base = (base or "user")[:30]
    h, n = base, 1
    while True:
        other = s.scalar(select(User).where(User.handle == h))
        if not other or other.id == exclude_id:
            return h
        n += 1
        h = f"{base}{n}"


@dataclass
class Who:
    """The signed-in person, as the request sees it."""
    id: str
    name: str
    email: str
    role: str
    handle: str = ""
    channel_access: dict = field(default_factory=dict)
    channel_ids: list = field(default_factory=list)
    totp_enabled: bool = False
    session_id: str | None = None

    @classmethod
    def of(cls, u: User, session_id: str | None = None) -> "Who":
        return cls(id=u.id, name=u.name or u.email, email=u.email, role=u.role if u.role in ROLES else "viewer",
                   handle=u.handle or "", channel_access=dict(u.channel_access or {}), channel_ids=list(u.channel_ids or []),
                   totp_enabled=bool(u.totp_enabled), session_id=session_id)

    @property
    def scopes(self) -> list[str]:
        return list(ROLE_SCOPES.get(self.role, ["read"]))

    @property
    def manager(self) -> bool:
        return self.role in ("owner", "admin")

    def channel_role(self, channel_id: str | None) -> str:
        """Effective role on one channel: the cell lowers the user's role, never raises it."""
        if self.manager or not channel_id:
            return self.role
        if self.channel_ids and channel_id not in self.channel_ids:
            return "none"
        cell = (self.channel_access or {}).get(channel_id)
        if cell in RANK and RANK[cell] < RANK[self.role]:
            return cell
        return self.role

    def restricted(self) -> bool:
        return not self.manager and (bool(self.channel_ids) or any(v == "none" for v in (self.channel_access or {}).values()))

    def to_dict(self) -> dict:
        r = RANK[self.role]
        return {"id": self.id, "name": self.name, "email": self.email, "role": self.role, "handle": self.handle,
                "scopes": self.scopes, "channel_access": self.channel_access, "totp_enabled": self.totp_enabled,
                "can": {"manage_team": self.manager, "approve": r >= RANK["editor"], "publish": r >= RANK["editor"],
                        "write": r >= RANK["contributor"], "settings": self.manager,
                        "comment": self.role != "viewer", "needs_approval": self.role == "contributor"}}


def member_dict(u: User) -> dict:
    status = "disabled" if u.disabled else ("invited" if not u.accepted_at else "active")
    return {"id": u.id, "name": u.name, "email": public_email(u.email), "role": u.role, "handle": u.handle, "status": status,
            "channel_access": dict(u.channel_access or {}), "totp_enabled": bool(u.totp_enabled),
            "invited_by": u.invited_by, "invite_expires_at": _iso(u.invite_expires_at),
            "created_at": _iso(u.created_at), "last_login_at": _iso(u.last_login_at)}


def public_email(email: str) -> str:
    return "" if (email or "").endswith(PENDING_EMAIL) else (email or "")


def real_email(email: str) -> bool:
    return bool(email) and not email.endswith(PENDING_EMAIL) and email != OWNER_EMAIL


# --------------------------------------------------------------------------- the bootstrap owner
def owner(s) -> User | None:
    return s.scalar(select(User).where(User.role == "owner", User.disabled.is_(False)).order_by(User.created_at))


def ensure_owner(s, password_hash: str) -> User:
    """The single app-password login is the bootstrap Owner. Create it from the legacy hash when missing."""
    u = owner(s)
    if u is None:
        u = User(email=OWNER_EMAIL, name="Owner", role="owner", password_hash=password_hash or "", accepted_at=utcnow())
        u.handle = make_handle(s, "owner", OWNER_EMAIL)
        s.add(u)
        s.flush()
    elif password_hash and not u.password_hash:
        u.password_hash = password_hash
    return u


# --------------------------------------------------------------------------- invites
def invite(s, by: Who, email: str, name: str = "", role: str = "contributor", channel_access: dict | None = None) -> tuple[User, str]:
    email = (email or "").strip().lower()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise TeamError("Enter a valid email address (or leave it empty to share a link)")
    if not email:   # a link-only invite: the person types their own email when they accept
        email = f"inv-{secrets.token_hex(6)}{PENDING_EMAIL}"
    if role not in ROLES:
        raise TeamError(f"Unknown role '{role}'")
    _may_assign(by, role)
    existing = s.scalar(select(User).where(User.email == email))
    if existing and existing.accepted_at:
        raise TeamError("That person is already on the team", 409)
    raw = "inv_" + secrets.token_urlsafe(24)
    u = existing or User(email=email)
    u.name = (name or u.name or (email.split("@")[0] if real_email(email) else "Invited teammate")).strip()[:80]
    u.role = role
    u.channel_access = clean_access(s, channel_access or {})
    u.invite_token_hash = _h(raw)
    u.invite_expires_at = utcnow() + INVITE_TTL
    u.invited_by, u.invited_by_id = by.name, by.id
    if not existing:
        u.handle = make_handle(s, u.name, email)
        s.add(u)
    s.flush()
    return u, raw


def find_invite(s, raw: str) -> User:
    u = s.scalar(select(User).where(User.invite_token_hash == _h(raw))) if raw else None
    if not u or u.accepted_at or u.disabled:
        raise TeamError("This invite link is not valid (it may have been used or revoked)", 404)
    if u.invite_expires_at and u.invite_expires_at < utcnow():
        raise TeamError("This invite link has expired - ask for a new one", 410)
    return u


def accept_invite(s, raw: str, password: str, name: str = "", email: str = "") -> User:
    from .security import MIN_PASSWORD, hash_password
    u = find_invite(s, raw)
    if len(password or "") < MIN_PASSWORD:
        raise TeamError(f"Password must be at least {MIN_PASSWORD} characters")
    if u.email.endswith(PENDING_EMAIL):   # link-only invite: the invitee's own sign-in email
        email = (email or "").strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise TeamError("Enter the email address you'll sign in with")
        other = s.scalar(select(User).where(User.email == email))
        if other and other.id != u.id:
            raise TeamError("Someone on the team already uses that email", 409)
        u.email = email
    u.password_hash = hash_password(password)
    if (name or "").strip():
        u.name = name.strip()[:80]
        u.handle = make_handle(s, u.name, u.email, exclude_id=u.id)
    u.accepted_at = utcnow()
    u.invite_token_hash = ""
    u.invite_expires_at = None
    s.flush()
    return u


def smtp_config() -> dict | None:
    host = os.environ.get("HUHQ_SMTP_HOST", "").strip()
    if not host:
        return None
    return {"host": host, "port": int(os.environ.get("HUHQ_SMTP_PORT", "587") or 587),
            "user": os.environ.get("HUHQ_SMTP_USER", ""), "password": os.environ.get("HUHQ_SMTP_PASSWORD", ""),
            "from": os.environ.get("HUHQ_SMTP_FROM", "") or os.environ.get("HUHQ_SMTP_USER", "") or "huhqueue@localhost",
            "tls": os.environ.get("HUHQ_SMTP_TLS", "starttls").lower()}


def send_email(to: str, subject: str, body: str) -> tuple[bool, str]:
    """Send one plain-text email when SMTP is configured (HUHQ_SMTP_*). Returns (sent, error)."""
    cfg = smtp_config()
    if not cfg or not real_email(to):
        return False, ""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"], msg["To"] = cfg["from"], to
    msg.set_content(body)
    try:
        cls = smtplib.SMTP_SSL if cfg["tls"] == "ssl" else smtplib.SMTP
        with cls(cfg["host"], cfg["port"], timeout=15) as srv:
            if cfg["tls"] == "starttls":
                srv.starttls()
            if cfg["user"]:
                srv.login(cfg["user"], cfg["password"])
            srv.send_message(msg)
        return True, ""
    except Exception as e:
        log.warning("email failed: %s", e.__class__.__name__)
        return False, f"Email not sent ({e.__class__.__name__}) - copy the link instead"


def send_invite_email(to: str, link: str, inviter: str, role: str) -> tuple[bool, str]:
    """Email the invite link when SMTP is configured. Link-only invites (no address) are never emailed."""
    return send_email(to, f"{inviter} invited you to HuhQueue",
                      f"{inviter} invited you to join HuhQueue as {role}.\n\nAccept the invite (valid 7 days):\n{link}\n")


def email_later(to: str, subject: str, body: str) -> None:
    """Notification emails go out on a background thread so a slow SMTP server never blocks a request."""
    if smtp_config() and real_email(to):
        import threading
        threading.Thread(target=send_email, args=(to, subject, body), daemon=True).start()


# --------------------------------------------------------------------------- member changes
def _may_assign(by: Who, role: str) -> None:
    if not by.manager:
        raise TeamError("Only owners and admins manage the team", 403)
    if role in ("owner", "admin") and by.role != "owner":
        raise TeamError("Only an owner can make someone an owner or admin", 403)


def _may_touch(by: Who, u: User) -> None:
    if not by.manager:
        raise TeamError("Only owners and admins manage the team", 403)
    if u.role in ("owner", "admin") and by.role != "owner" and u.id != by.id:
        raise TeamError("Only an owner can change owners and admins", 403)


def _owners_left(s, excluding: str) -> int:
    return len([u for u in s.scalars(select(User).where(User.role == "owner", User.disabled.is_(False))) if u.id != excluding])


def clean_access(s, access: dict) -> dict:
    ids = {c for (c,) in s.execute(select(Channel.id))}
    return {k: v for k, v in (access or {}).items() if k in ids and v in CELL_VALUES}


def update_member(s, by: Who, uid: str, payload: dict) -> User:
    u = s.get(User, uid)
    if not u:
        raise TeamError("Member not found", 404)
    _may_touch(by, u)
    if "role" in payload and payload["role"] != u.role:
        role = payload["role"]
        if role not in ROLES:
            raise TeamError(f"Unknown role '{role}'")
        if u.id == by.id:
            raise TeamError("You can't change your own role", 403)
        _may_assign(by, role)
        if u.role == "owner" and not _owners_left(s, u.id):
            raise TeamError("Keep at least one owner", 409)
        u.role = role
    if "channel_access" in payload:
        u.channel_access = clean_access(s, payload["channel_access"] or {})
    if "name" in payload and str(payload["name"] or "").strip():
        u.name = str(payload["name"]).strip()[:80]
        u.handle = make_handle(s, u.name, u.email, exclude_id=u.id)
    if "disabled" in payload:
        if u.id == by.id:
            raise TeamError("You can't disable yourself", 403)
        if payload["disabled"] and u.role == "owner" and not _owners_left(s, u.id):
            raise TeamError("Keep at least one owner", 409)
        u.disabled = bool(payload["disabled"])
        if u.disabled:
            revoke_sessions(s, u.id)
    s.flush()
    return u


def remove_member(s, by: Who, uid: str) -> None:
    from .models import ApiToken
    u = s.get(User, uid)
    if not u:
        return
    _may_touch(by, u)
    if u.id == by.id:
        raise TeamError("You can't remove yourself", 403)
    if u.role == "owner" and not _owners_left(s, u.id):
        raise TeamError("Keep at least one owner", 409)
    revoke_sessions(s, u.id)
    for t in s.scalars(select(ApiToken).where(ApiToken.user_id == u.id)):
        s.delete(t)
    s.delete(u)


def revoke_sessions(s, uid: str, keep: str | None = None) -> int:
    n = 0
    for ss in s.scalars(select(UserSession).where(UserSession.user_id == uid, UserSession.revoked_at.is_(None))):
        if ss.id != keep:
            ss.revoked_at = utcnow()
            n += 1
    return n


# --------------------------------------------------------------------------- TOTP 2FA
def totp_begin(core, s, uid: str) -> dict:
    import pyotp
    u = s.get(User, uid)
    if u.totp_enabled:
        raise TeamError("Two-factor is already on - turn it off first to re-enrol", 409)
    secret = pyotp.random_base32()
    u.totp_secret_enc = core.cipher.encrypt(secret)
    uri = pyotp.TOTP(secret).provisioning_uri(name=u.email, issuer_name="HuhQueue")
    return {"secret": secret, "otpauth_uri": uri, "qr": qr_data_uri(uri)}


def qr_data_uri(text: str) -> str:
    import qrcode
    img = qrcode.make(text, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def new_recovery_codes() -> tuple[list[str], list[str]]:
    codes = [f"{secrets.token_hex(3)}-{secrets.token_hex(3)}" for _ in range(RECOVERY_CODES)]
    return codes, [_h(c) for c in codes]


def verify_totp(core, s, u: User, code: str) -> bool:
    """A 6-digit TOTP code (±30 s, never the same time-step twice) or a one-time recovery code."""
    import pyotp
    code = re.sub(r"\s", "", code or "")
    if re.fullmatch(r"\d{6}", code):
        secret = core.cipher.decrypt(u.totp_secret_enc or "")
        if not secret:
            return False
        totp = pyotp.TOTP(secret)
        now = int(datetime.now().timestamp())
        row = s.get(KV, f"totp_step:{u.id}")
        last = int((row.value or {}).get("step", 0)) if row else 0
        for drift in (0, -1, 1):
            step = now // 30 + drift
            if step > last and hmac.compare_digest(totp.at(step * 30), code):
                s.merge(KV(key=f"totp_step:{u.id}", value={"step": step}))
                return True
        return False
    h = _h(code.lower())
    if u.totp_enabled and h in (u.recovery_hashes or []):
        u.recovery_hashes = [x for x in u.recovery_hashes if x != h]
        return True
    return False


def totp_enable(core, s, uid: str, code: str) -> list[str]:
    u = s.get(User, uid)
    if u.totp_enabled:
        raise TeamError("Two-factor is already on", 409)
    if not u.totp_secret_enc:
        raise TeamError("Start the setup first")
    if not verify_totp(core, s, u, code):
        raise TeamError("That code didn't match - check your phone's clock and use the newest code")
    codes, hashes = new_recovery_codes()
    u.totp_enabled, u.recovery_hashes = True, hashes
    return codes


def totp_disable(s, uid: str) -> None:
    u = s.get(User, uid)
    u.totp_enabled, u.totp_secret_enc, u.recovery_hashes = False, "", []
    row = s.get(KV, f"totp_step:{uid}")
    if row:
        s.delete(row)


# --------------------------------------------------------------------------- notifications
MENTION = re.compile(r"@([A-Za-z0-9_.\-]{2,40})")


def notify_mentions(s, text: str, by: str, target_id: str | None, by_id: str | None = None) -> list[str]:
    """@handle (or @email-local-part / @first-name) -> a notification addressed to that teammate only."""
    users = list(s.scalars(select(User).where(User.disabled.is_(False))))
    hit = []
    for m in sorted({x.lower().rstrip(".") for x in MENTION.findall(text or "")}):
        u = next((u for u in users if (u.handle or "").lower() == m), None) or \
            next((u for u in users if u.email.split("@")[0].lower() == m), None) or \
            next((u for u in users if (u.name or "").split(" ")[0].lower() == m), None)
        if u and u.id != by_id and u.id not in hit:
            hit.append(u.id)
            notify_user(s, u.id, f"{by} mentioned you on a post: “{(text or '')[:80]}”", target_id, kind="mention")
    return hit


def notify_user(s, user_id: str | None, message: str, target_id: str | None = None, channel_id: str | None = None,
                kind: str = "approval", level: str = "info") -> None:
    """A bell notification for one teammate (plus an email when SMTP is configured)."""
    if not user_id:
        return
    s.add(Event(level=level, kind=kind, message=message, notify=True, target_id=target_id, channel_id=channel_id, user_id=user_id))
    u = s.get(User, user_id)
    if u and not u.disabled:
        email_later(u.email, f"HuhQueue: {message[:70]}", f"{message}\n\nOpen HuhQueue to see it.\n")


def approvers(s, channel_id: str | None) -> list[User]:
    """Active teammates who may approve posts on this channel (editors and up, after the per-channel cell)."""
    out = []
    for u in s.scalars(select(User).where(User.disabled.is_(False)).order_by(User.created_at)):
        if u.accepted_at and RANK[Who.of(u).channel_role(channel_id)] >= RANK["editor"]:
            out.append(u)
    return out


def notify_approvers(s, t, by: str, by_id: str | None) -> list[str]:
    """A contributor asked for approval: tell everyone who can approve on that channel (not the requester)."""
    hit = []
    ch = f" on @{t.channel.username}" if getattr(t, "channel", None) else ""
    for u in approvers(s, t.channel_id):
        if u.id != by_id:
            notify_user(s, u.id, f"{by} asked for approval{ch}: “{(t.caption_override or t.post.caption or '')[:80]}”",
                        t.id, t.channel_id, kind="approval")
            hit.append(u.id)
    return hit


def notify_note(s, post, text: str, by: str, by_id: str | None, skip: list[str]) -> list[str]:
    """A comment on a post: tell the people in the conversation (whoever asked for approval, earlier commenters)."""
    from .models import PostNote
    ids = []
    for t in post.targets:
        ids.append((t.approval or {}).get("requested_by_user_id"))
    ids += [n.author_id for n in s.scalars(select(PostNote).where(PostNote.post_id == post.id))]
    hit = []
    tid = post.targets[0].id if post.targets else None
    for uid in dict.fromkeys(x for x in ids if x):
        if uid != by_id and uid not in skip:
            notify_user(s, uid, f"{by} commented on a post: “{(text or '')[:80]}”", tid, kind="comment")
            hit.append(uid)
    return hit


# --------------------------------------------------------------------------- request authorization
SELF_SERVICE = ("/api/auth/", "/api/account", "/api/me", "/api/notifications", "/api/team/directory", "/api/actions",
                "/api/assistant/", "/api/health")
FILTERED_LISTS = ("/api/channels", "/api/posts", "/api/comments")
NOTE_PATH = re.compile(r"^/api/posts/[^/]+/notes(/[^/]+)?$")


@dataclass
class Decision:
    ok: bool = True
    status: int = 200
    message: str = ""
    needs_approval: bool = False
    visible: set | None = None   # channel ids the caller may see (None = all); list responses are filtered to it


def _deny(msg: str, status: int = 403) -> Decision:
    return Decision(False, status, msg)


def _target(s, tid: str) -> tuple[list[str], str]:
    t = s.get(PostTarget, tid)
    return ([t.channel_id], t.status) if t else ([], "")


def involved_channels(s, path: str, query: dict, body) -> tuple[list[str], list[str]]:
    """(channel ids this request touches, statuses of the posts it touches)."""
    from . import safety   # lazy, like the other cross-module imports here
    chans, statuses = [], []
    parts = path.strip("/").split("/")   # ["api", kind, id, ...]
    kind, ident = (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
    if ident and kind in ("channels", "queue"):
        chans.append(ident)
    elif ident and kind == "targets":
        c, st = _target(s, ident)
        chans, statuses = chans + c, statuses + [st]
    elif ident and kind == "comments" and ident != "sync":
        cm = s.get(Comment, ident)
        if cm:
            chans.append(cm.channel_id)
    elif ident and kind == "posts":
        p = s.get(Post, ident)
        if p:
            chans += [t.channel_id for t in p.targets]
            statuses += [t.status for t in p.targets]
    if isinstance(body, dict):
        ids = body.get("channel_ids")
        if isinstance(ids, list) and ids:
            if "all" in ids:
                chans += [c for (c,) in s.execute(select(Channel.id))]
            else:
                from .services.posts import resolve_channels
                try:
                    chans += [c.id for c in resolve_channels(s, ids)]
                except Exception:
                    chans += [str(x) for x in ids]
        bulk_ids = body.get("ids") if isinstance(body.get("ids"), list) else []
        if path in ("/api/bulk", "/api/bulk/approve") and len(bulk_ids) <= safety.MAX_SCOPE_IDS:   # longer: TOO_MANY
            for tid in bulk_ids:
                c, st = _target(s, str(tid))
                chans, statuses = chans + c, statuses + [st]
        if body.get("channel_id"):
            chans.append(str(body["channel_id"]))
    for x in str(query.get("channel_id") or "").split(","):
        if x.strip():
            chans.append(x.strip())
    # every other id the token channel scope resolves (comment_id / post_id / target_id in the body, /api/comments/bulk
    # comment ids, /api/utm/defaults/{cid}): the same object -> channel resolution for teammates (CodeRabbit, 4 Nov 2026)
    named, objects = safety.scope_refs(s, "GET", path, query, body)
    chans += sorted((named | objects) - {safety.UNRESOLVED})
    return list(dict.fromkeys(chans)), statuses


def object_only_channels(s, path: str, query: dict, body) -> set[str]:
    """Channels the request reaches ONLY through an object id (post, target, comment), not by naming the channel. A
    denial on one of these answers 404 (no id probing); a channel the request names itself keeps its 403."""
    from . import safety
    named, objects = safety.scope_refs(s, "GET", path, query, body)
    return objects - {safety.UNRESOLVED, safety.TOO_MANY} - named


def authorize(s, who: Who, method: str, path: str, query: dict, body, scope: str) -> Decision:
    """May this teammate make this request? The guard middleware runs it for every session and user-bound token."""
    if path.startswith(SELF_SERVICE):
        return Decision()
    if path.startswith("/api/team"):
        return Decision() if who.manager else _deny("Only owners and admins manage the team")
    role = who.role
    read = method in ("GET", "HEAD", "OPTIONS")
    # ---- what the request needs, role-wide
    if who.manager:
        need = None
    elif "/approve" in path or "/reject" in path:
        need = "editor"
    elif NOTE_PATH.match(path) and not read:   # comments on posts: everyone but read-only viewers
        if role == "viewer":
            return _deny("Viewers have read-only access")
        need = "viewer"
    elif read:
        need = "viewer"
    elif scope == "admin":
        return _deny(f"Your role ({role}) can't change settings or channels - ask an owner or admin")
    elif path == "/api/worker/tick":
        need = "contributor"   # it only publishes what is already approved / scheduled
    elif scope == "publish" and not path.endswith("/publish-now"):
        need = "editor"
    elif path.startswith("/api/comments/") and path.endswith("/reply"):
        need = "editor"
    else:
        need = "contributor"
    if need and RANK[role] < RANK[need]:
        return _deny("Viewers have read-only access" if role == "viewer" else f"Your role ({role}) can't do that - ask an editor")
    # ---- per channel
    chans, statuses = involved_channels(s, path, query, body)
    from . import safety
    if safety.TOO_MANY in chans:
        return _deny(f"At most {safety.MAX_SCOPE_IDS} ids per request", 400)
    d = Decision()
    if who.restricted():
        d.visible = {c for (c,) in s.execute(select(Channel.id)) if who.channel_role(c) != "none"}
    contributor_on = role == "contributor"
    via_object = None
    for cid in chans:
        cr = who.channel_role(cid)
        if cr == "none":
            if via_object is None:
                via_object = object_only_channels(s, path, query, body)
            if read or cid in via_object:   # another channel's post/comment answers like a missing one (no id probing)
                return _deny("Not found", 404)
            return _deny("You don't have access to this channel", 403)
        if need and RANK[cr] < RANK[need]:
            return _deny(f"On this channel you are a {cr}" + (" (read only)" if cr == "viewer" else " - ask an editor"))
        if cr == "contributor":
            contributor_on = True
    # after the per-channel checks, so a forbidden channel the request NAMES answers the same (403) whether the object
    # exists or not; then a missing post/target/comment answers like another channel's (404, no id probing)
    if who.restricted() and safety.UNRESOLVED in safety.scope_refs(s, method, path, query, body)[1]:
        return _deny("Not found", 404)
    if contributor_on and not read and not NOTE_PATH.match(path):
        st = {x for x in statuses if x}
        action = body.get("action") if isinstance(body, dict) else None
        if path == "/api/bulk" and action in ("queue", "prioritize", "reschedule", "publish_now", "move"):
            return _deny("Contributors submit posts for approval instead of queueing them")
        if path == "/api/bulk" and action == "delete" and st - {"draft", "pending_approval", "failed"}:
            return _deny("Contributors can only delete drafts")
        if path.startswith("/api/targets/") and method == "DELETE" and st - {"draft", "pending_approval", "failed"}:
            return _deny("Contributors can only delete drafts")
        if path.startswith("/api/targets/") and method == "PATCH" and "scheduled" in st and \
                not (isinstance(body, dict) and body.get("when")):
            return _deny("This post is already scheduled - only editors can change it")
        if path.endswith(("/mark-posted", "/retry")) or path.startswith("/api/queue/"):
            return _deny("Only editors can do that")
        d.needs_approval = True
    return d


def filter_listing(path: str, data, visible: set):
    """Drop rows for channels the caller can't see from list responses."""
    if not isinstance(data, list):
        return data
    key = "id" if path == "/api/channels" else "channel_id"
    return [x for x in data if not isinstance(x, dict) or x.get(key) is None or x.get(key) in visible]
