"""HuhQueue Studio configuration (Buffer Commit edition).

Everything comes from the environment so the same image runs locally, in tests and on the host.
Secrets (BUFFER_CLIENT_SECRET, SESSION_SECRET) are host secrets and never reach the browser.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field

AUTH_URL = "https://auth.buffer.com/auth"
TOKEN_URL = "https://auth.buffer.com/token"
API_URL = "https://api.buffer.com"
# offline_access gives a rotating refresh token, so users are not sent back to consent every hour.
SCOPES = "account:read posts:read posts:write ideas:read ideas:write offline_access"


@dataclass
class Settings:
    client_id: str = ""
    client_secret: str = ""
    public_url: str = "https://localhost:8000"
    session_secret: str = ""
    db_path: str = "studio.db"
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    secure_cookies: bool = True
    repo_url: str = "https://github.com/dataanalystram/buffer-contest"
    extra: dict = field(default_factory=dict)

    @property
    def redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/callback"

    @property
    def configured(self) -> bool:
        return bool(self.client_id)


def load_settings(**overrides) -> Settings:
    env = os.environ.get
    s = Settings(
        client_id=(env("BUFFER_CLIENT_ID") or "").strip(),
        client_secret=(env("BUFFER_CLIENT_SECRET") or "").strip(),
        public_url=(env("PUBLIC_URL") or env("RENDER_EXTERNAL_URL") or "https://localhost:8000").strip().rstrip("/"),
        session_secret=(env("SESSION_SECRET") or "").strip(),
        db_path=(env("DATABASE_URL") or env("STUDIO_DB") or "studio.db").strip(),
        # Optional OpenAI-compatible endpoint for caption drafting. Without it the built-in drafter is used.
        ai_base_url=(env("AI_BASE_URL") or "").strip().rstrip("/"),
        ai_api_key=(env("AI_API_KEY") or "").strip(),
        ai_model=(env("AI_MODEL") or "").strip(),
        secure_cookies=env("STUDIO_INSECURE_COOKIES", "") != "1",
        # Set REPO_URL="" while the repository is private so the footer never links to a 404.
        repo_url=env("REPO_URL", Settings.repo_url).strip(),
    )
    for k, v in overrides.items():
        setattr(s, k, v)
    if not s.session_secret:
        # Dev/test fallback only; production sets SESSION_SECRET so sessions survive restarts.
        s.session_secret = secrets.token_hex(32)
    return s
