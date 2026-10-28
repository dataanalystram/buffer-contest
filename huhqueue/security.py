"""App authentication, sessions, CSRF, API tokens and log redaction."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from datetime import datetime

from sqlalchemy import select

from dataclasses import dataclass, field

from .models import KV, ApiToken, User, UserSession, utcnow

SESSION_COOKIE = "huhq_session"
CSRF_COOKIE = "huhq_csrf"
SESSION_TTL = 14 * 24 * 3600
MIN_PASSWORD = 8


SCOPES = {
    "read": "Read channels, posts, media, analytics, comments",
    "write": "Create drafts, upload media, edit ideas",
    "publish": "Schedule and publish posts (subject to approval if the token requires it)",
    "comments": "Reply to / resolve comments",
    "admin": "Connect channels, change settings, manage tokens",
}
from .actions import mcp_tool_names  # noqa: E402  (the registry is the one source of truth)


def clean_tools(tools) -> list[str]:
    """A token's MCP tool allow-list, checked against the live registry (feature modules register tools after this
    module is imported, so never cache the list). Unknown names are an error: silently dropping them would turn a
    narrow allow-list into an empty one, and an empty list means every tool."""
    known = set(mcp_tool_names())
    tools = [str(x) for x in (tools or [])]
    bad = [x for x in tools if x not in known]
    if bad:
        raise ValueError(f"unknown MCP tool(s): {', '.join(bad[:5])}")
    return list(dict.fromkeys(tools))


@dataclass
class TokenInfo:
    id: str | None
    name: str
    scopes: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    require_approval: bool = False
    rate_per_min: int = 600
    user: object = None          # teams.Who when the token belongs to a teammate (capped by their role)
    max_public_per_day: int = 0  # agent safety layer: 0 = no cap
    max_actions_per_day: int = 0
    allowed_channel_ids: list = field(default_factory=list)   # [] = every channel
    auto_confirm: bool = False   # MCP public actions skip the preview + confirm_token step
    expires_at: object = None

    def can(self, scope: str) -> bool:
        return "admin" in self.scopes or scope in self.scopes

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "scopes": self.scopes, "tools": self.tools,
                "require_approval": self.require_approval, "rate_per_min": self.rate_per_min,
                "max_public_per_day": self.max_public_per_day, "max_actions_per_day": self.max_actions_per_day,
                "allowed_channel_ids": list(self.allowed_channel_ids), "auto_confirm": self.auto_confirm,
                "expires_at": self.expires_at.isoformat() + "Z" if self.expires_at else None,
                "user": self.user.to_dict() if self.user else None}


ADMIN_PREFIXES = ("/api/tokens", "/api/webhooks", "/api/connect", "/api/oauth", "/api/tunnel", "/api/auth/password", "/api/audit", "/api/app-keys",
                  "/api/ai/settings", "/api/ai/test", "/api/ai/models")   # /api/ai/status (read) and /api/ai/write (write) serve agents too


def required_scope(method: str, path: str) -> str:
    if path.startswith(ADMIN_PREFIXES):
        return "admin"
    if path.startswith(("/api/actions", "/api/assistant/")):
        return "read"   # the runner re-enters the real route, which enforces that action's own scope
    if method in ("GET", "HEAD"):
        return "read"
    if path.startswith(("/api/settings", "/api/library", "/api/channels", "/api/idea-columns")) or "/approve" in path or "/reject" in path:
        return "admin"
    if path.endswith(("/publish-now", "/retry")) or path.startswith("/api/worker"):
        return "publish"
    if path in ("/api/validate", "/api/suggest-format") or (path.startswith("/api/bio/") and path.endswith("/preview")):
        return "read"
    if path.startswith("/api/comments"):
        return "comments"
    return "write"


class RateLimiter:
    def __init__(self):
        self.hits: dict[str, list[float]] = {}

    def allow(self, key: str, per_min: int) -> bool:
        now = time.time()
        q = [t for t in self.hits.get(key, []) if now - t < 60]
        if len(q) >= max(1, per_min):
            self.hits[key] = q
            return False
        q.append(now)
        self.hits[key] = q
        return True


# --------------------------------------------------------------------------- passwords
def hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt_hex), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


class Auth:
    def __init__(self, core):
        self.core = core
        self._key = hashlib.sha256(b"huhq-session|" + core.cipher.key).digest()
        self._fails: list[float] = []
        if core.settings.admin_password and not self._state().get("hash"):
            if len(core.settings.admin_password) < MIN_PASSWORD:
                raise ValueError(f"HUHQ_ADMIN_PASSWORD must be at least {MIN_PASSWORD} characters")
            self.set_password(core.settings.admin_password)

    # ---- stored state
    def _state(self) -> dict:
        with self.core.db.session() as s:
            row = s.get(KV, "auth")
            return dict(row.value) if row else {}

    def _save(self, **changes) -> None:
        with self.core.db.session() as s:
            row = s.get(KV, "auth")
            if not row:
                row = KV(key="auth", value={})
                s.add(row)
            row.value = {**(row.value or {}), **changes}

    def has_password(self) -> bool:
        if self._state().get("hash"):
            return True
        with self.core.db.session() as s:
            return s.scalar(select(User.id).where(User.password_hash != "").limit(1)) is not None

    def set_password(self, pw: str) -> None:
        """The bootstrap Owner's app password (first-run setup / HUHQ_ADMIN_PASSWORD). Logs out every owner session."""
        from .teams import ensure_owner, revoke_sessions
        if len(pw or "") < MIN_PASSWORD:
            raise ValueError(f"Password must be at least {MIN_PASSWORD} characters")
        h = hash_password(pw)
        epoch = int(self._state().get("epoch", 0)) + 1
        self._save(hash=h, epoch=epoch)
        with self.core.db.session() as s:
            u = ensure_owner(s, h)
            u.password_hash = h
            revoke_sessions(s, u.id)

    def _throttle(self) -> None:
        now = time.time()
        self._fails = [t for t in self._fails if now - t < 300]
        if len(self._fails) >= 10:
            raise PermissionError("Too many attempts - wait 5 minutes")

    def fail(self) -> None:
        self._fails.append(time.time())

    def authenticate(self, password: str, email: str = "") -> User | None:
        """Email + password for teammates; a bare password is the bootstrap Owner's app password."""
        from .teams import owner
        self._throttle()
        ok = False
        with self.core.db.session() as s:
            u = s.scalar(select(User).where(User.email == email.strip().lower())) if email.strip() else owner(s)
            ok = bool(u and not u.disabled and u.password_hash and verify_password(password or "", u.password_hash))
            if not ok and u is not None and not email.strip():
                legacy = self._state().get("hash", "")   # databases from before teams
                ok = bool(legacy) and not u.password_hash and verify_password(password or "", legacy)
                if ok:
                    u.password_hash = legacy
        if not ok:
            self.fail()
            return None
        return u

    def check_password(self, pw: str) -> bool:
        return self.authenticate(pw) is not None

    def logout_everywhere(self) -> None:
        self._save(epoch=int(self._state().get("epoch", 0)) + 1)
        with self.core.db.session() as s:
            for ss in s.scalars(select(UserSession).where(UserSession.revoked_at.is_(None))):
                ss.revoked_at = utcnow()

    # ---- sessions: one server-side row per browser; cookie = v2.<session id>.<secret>
    def _sign(self, msg: str) -> str:
        return hmac.new(self._key, msg.encode(), hashlib.sha256).hexdigest()

    def new_session(self, user_id: str | None = None, ip: str = "", user_agent: str = "") -> tuple[str, str]:
        from .teams import ensure_owner
        secret = secrets.token_urlsafe(24)
        with self.core.db.session() as s:
            if not user_id:
                user_id = ensure_owner(s, self._state().get("hash", "")).id
            row = UserSession(user_id=user_id, secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
                              ip=(ip or "")[:64], user_agent=(user_agent or "")[:300])
            s.add(row)
            s.flush()
            u = s.get(User, user_id)
            if u:
                u.last_login_at = utcnow()
            token = f"v2.{row.id}.{secret}"
        return token, self.csrf_for(token)

    def csrf_for(self, session_token: str) -> str:
        return self._sign("csrf|" + session_token)[:40]

    def session(self, token: str | None):
        """(session id, teams.Who) for a live session cookie, else None."""
        from .teams import Who
        if not token or not token.startswith("v2.") or token.count(".") != 2:
            return None
        _, sid, secret = token.split(".")
        with self.core.db.session() as s:
            row = s.get(UserSession, sid)
            if not row or row.revoked_at or not hmac.compare_digest(row.secret_hash, hashlib.sha256(secret.encode()).hexdigest()):
                return None
            if (utcnow() - row.created_at).total_seconds() > SESSION_TTL:
                return None
            u = s.get(User, row.user_id)
            if not u or u.disabled:
                return None
            if (utcnow() - (row.last_seen_at or row.created_at)).total_seconds() > 60:
                row.last_seen_at = utcnow()
            return sid, Who.of(u, sid)

    def valid_session(self, token: str | None) -> bool:
        return self.session(token) is not None

    def end_session(self, token: str | None) -> None:
        got = self.session(token)
        if got:
            with self.core.db.session() as s:
                s.get(UserSession, got[0]).revoked_at = utcnow()

    # ---- API tokens
    def bearer(self, token: str | None) -> TokenInfo | None:
        if not token:
            return None
        env = self.core.settings.api_token
        if env and hmac.compare_digest(env, token):
            return TokenInfo(id=None, name="env:HUHQ_API_TOKEN", scopes=list(SCOPES), require_approval=False)
        h = hashlib.sha256(token.encode()).hexdigest()
        with self.core.db.session() as s:
            row = s.query(ApiToken).filter_by(token_hash=h).first()
            if row:
                if row.expires_at and row.expires_at < datetime.utcnow():
                    return None
                row.last_used_at = datetime.utcnow()
                info = TokenInfo(id=row.id, name=row.name, scopes=list(row.scopes or []), tools=list(row.tools or []),
                                 require_approval=bool(row.require_approval), rate_per_min=row.rate_per_min or 60,
                                 max_public_per_day=int(row.max_public_per_day or 0),
                                 max_actions_per_day=int(row.max_actions_per_day or 0),
                                 allowed_channel_ids=list(row.allowed_channel_ids or []),
                                 auto_confirm=bool(row.auto_confirm), expires_at=row.expires_at)
                if row.user_id:   # a teammate's token can never do more than that teammate
                    from .teams import Who
                    u = s.get(User, row.user_id)
                    if not u or u.disabled:
                        return None
                    info.user = Who.of(u)
                    info.scopes = [x for x in info.scopes if x in info.user.scopes]
                    if info.user.role == "contributor":
                        info.require_approval = True
                return info
        return None

    def valid_bearer(self, token: str | None) -> bool:
        return self.bearer(token) is not None

    def mint_token(self, name: str, scopes: list | None = None, tools: list | None = None,
                   require_approval: bool = True, rate_per_min: int = 60, user_id: str | None = None,
                   limits: dict | None = None) -> tuple[ApiToken, str]:
        scopes = [x for x in (scopes or ["read", "write", "publish", "comments"]) if x in SCOPES]
        tools = clean_tools(tools)
        raw = "hq_" + secrets.token_urlsafe(32)
        with self.core.db.session() as s:
            row = ApiToken(name=name or "token", token_hash=hashlib.sha256(raw.encode()).hexdigest(), prefix=raw[:7],
                           scopes=scopes, tools=tools, require_approval=require_approval,
                           rate_per_min=max(1, int(rate_per_min)))
            row.user_id = user_id
            apply_limits(row, limits or {})
            s.add(row)
        return row, raw


def apply_limits(row: ApiToken, payload: dict) -> None:
    """Agent safety limits on a token row (create and edit share this). Raises ValueError on bad input."""
    from .safety import parse_expiry
    if "max_public_per_day" in payload:
        row.max_public_per_day = max(0, int(payload.get("max_public_per_day") or 0))
    if "max_actions_per_day" in payload:
        row.max_actions_per_day = max(0, int(payload.get("max_actions_per_day") or 0))
    if "allowed_channel_ids" in payload:
        row.allowed_channel_ids = [str(x) for x in (payload.get("allowed_channel_ids") or []) if str(x).strip()][:200]
    if "auto_confirm" in payload:
        row.auto_confirm = bool(payload.get("auto_confirm"))
    if "expires_at" in payload or "expires_in_days" in payload:
        row.expires_at = parse_expiry(payload.get("expires_in_days") if "expires_in_days" in payload else payload.get("expires_at"))


# --------------------------------------------------------------------------- log redaction
_PATTERNS = [
    (re.compile(r"(access_token|refresh_token|client_secret|code|token)=([^&\s\"']+)", re.I), r"\1=***"),
    (re.compile(r"(Bearer|OAuth)\s+[A-Za-z0-9._~+/=-]+", re.I), r"\1 ***"),
    (re.compile(r"(\"(?:access_token|refresh_token|client_secret)\"\s*:\s*\")[^\"]+"), r"\1***"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        if redact(msg) == msg:
            return True
        # Redact each argument in place so formatters that unpack record.args (uvicorn's
        # AccessFormatter expects a 5-tuple) keep working; only collapse when the secret
        # sits in the format string itself.
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.args, dict) and record.args:
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        if not isinstance(record.msg, str) or redact(record.msg) != record.msg or redact(record.getMessage()) != record.getMessage():
            record.msg, record.args = redact(msg), ()
        return True


def install_log_redaction() -> None:
    f = RedactFilter()
    for name in ("", "uvicorn", "uvicorn.access", "uvicorn.error", "huhqueue", "httpx", "httpcore"):
        lg = logging.getLogger(name)
        lg.addFilter(f)
        for h in lg.handlers:
            h.addFilter(f)
    # httpx logs full request URLs (which carry access_token for Graph calls) at INFO - keep it quiet
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
