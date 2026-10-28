"""The application container: settings, database, token cipher and connector instances."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .config import Settings, load_settings
from .connectors import Connector, discover
from .crypto import TokenCipher
from .db import Database
from .models import KV, Channel

log = logging.getLogger("huhqueue")

DEFAULT_PREFS = {
    "timezone": "Europe/Dublin",
    "hashtag_limit": 5,
    "default_when": "queue",
    "default_daily_limit": 2,
    "notify_webhook_url": "",
    "public_base_url": "",          # set by the tunnel helper or by hand in Settings
    "idea_columns": [
        {"id": "unassigned", "label": "Unassigned"},
        {"id": "todo", "label": "To do"},
        {"id": "in_progress", "label": "In progress"},
        {"id": "done", "label": "Done"},
    ],
}


# Developer-app keys that can be entered in Settings -> App keys (stored encrypted in the DB).
# provider -> [(settings attribute, label, is_secret)]
APP_KEY_FIELDS: dict[str, list[tuple[str, str, bool]]] = {
    "instagram": [("instagram_app_id", "Instagram app ID", False), ("instagram_app_secret", "Instagram app secret", True)],
    "youtube": [("youtube_client_id", "Google OAuth client ID", False), ("youtube_client_secret", "Google OAuth client secret", True)],
    "x": [("x_client_id", "X OAuth 2.0 client ID", False), ("x_client_secret", "X OAuth 2.0 client secret", True)],
    "linkedin": [("linkedin_client_id", "LinkedIn client ID", False), ("linkedin_client_secret", "LinkedIn client secret", True)],
    "linkedin_page": [("linkedin_pages_client_id", "LinkedIn Pages app client ID", False),
                      ("linkedin_pages_client_secret", "LinkedIn Pages app client secret", True)],
    "tiktok": [("tiktok_client_key", "TikTok client key", False), ("tiktok_client_secret", "TikTok client secret", True)],
    "unsplash": [("unsplash_access_key", "Unsplash access key", True)],
}
# Keys for integrations that are not networks (no OAuth redirect): provider -> display name
INTEGRATIONS = {"unsplash": "Unsplash"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Core:
    def __init__(self, settings: Settings | None = None, http: httpx.Client | None = None,
                 connectors: dict[str, type[Connector]] | None = None):
        self.settings = settings or load_settings()
        self.db = Database(self.settings.db_url)
        self.cipher = TokenCipher(self.settings.secret_key, self.settings.data_dir)
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0), follow_redirects=True)
        classes = connectors or discover(self.settings.enable_fake)
        self.connectors: dict[str, Connector] = {k: cls(self.settings, self.http) for k, cls in classes.items()}
        DEFAULT_PREFS["timezone"] = self.settings.timezone or DEFAULT_PREFS["timezone"]
        self._env_keys = {f: getattr(self.settings, f, "") for fields in APP_KEY_FIELDS.values() for f, _, _ in fields}
        self.apply_app_keys()
        self.tunnel = None  # set by api.app when the tunnel helper is used
        self._detected = ""  # last https origin seen from a signed-in browser (see note_origin)

    # ---- developer-app keys (Settings -> App keys); a key saved in the UI wins over .env
    def _stored_app_keys(self) -> dict:
        with self.db.session() as s:
            row = s.get(KV, "app_keys")
            return dict(row.value) if row else {}

    def apply_app_keys(self) -> None:
        stored = self._stored_app_keys()
        for field, env_value in self._env_keys.items():
            enc = stored.get(field)
            val = self.cipher.decrypt(enc) if enc else None
            setattr(self.settings, field, val if val else env_value)

    def app_keys(self, origin: str = "") -> dict:
        stored = self._stored_app_keys()
        out = {}
        for provider, fields in APP_KEY_FIELDS.items():
            if provider not in self.connectors and provider not in INTEGRATIONS:
                continue
            rows = []
            for field, label, secret in fields:
                value = getattr(self.settings, field, "") or ""
                source = "ui" if stored.get(field) else ("env" if self._env_keys.get(field) else "")
                row = {"key": field, "label": label, "secret": secret, "set": bool(value), "source": source}
                if secret:
                    row["hint"] = f"••••{value[-4:]}" if len(value) > 8 else ("••••" if value else "")
                else:
                    row["value"] = value
                rows.append(row)
            if provider in INTEGRATIONS:
                out[provider] = {"name": INTEGRATIONS[provider], "configured": all(r["set"] for r in rows), "fields": rows,
                                 "redirect_uri": None, "integration": True}
                continue
            conn = self.connectors[provider]
            out[provider] = {"name": conn.name, "configured": conn.is_configured(), "fields": rows,
                             "redirect_uri": self.redirect_uri(provider, origin)}
            if provider == "instagram":  # Meta app dashboard asks for these two compliance callbacks
                base = self.redirect_uri(provider, origin).rsplit("/oauth/", 1)[0]
                out[provider]["deauthorize_url"] = f"{base}/oauth/instagram/deauthorize"
                out[provider]["data_deletion_url"] = f"{base}/oauth/instagram/data-deletion"
        return out

    def set_app_keys(self, provider: str, values: dict, origin: str = "") -> dict:
        """values: {field: str}. Empty string clears the UI value (falls back to .env). Missing = unchanged."""
        if provider not in APP_KEY_FIELDS:
            raise KeyError(f"No app keys for '{provider}'")
        allowed = {f for f, _, _ in APP_KEY_FIELDS[provider]}
        with self.db.session() as s:
            row = s.get(KV, "app_keys")
            if not row:
                row = KV(key="app_keys", value={})
                s.add(row)
            cur = dict(row.value or {})
            for k, v in (values or {}).items():
                if k not in allowed or v is None:
                    continue
                v = str(v).strip()
                if v:
                    cur[k] = self.cipher.encrypt(v)
                else:
                    cur.pop(k, None)
            row.value = cur
        self.apply_app_keys()
        return self.app_keys(origin).get(provider, {})

    # ---- connectors
    def connector(self, provider: str) -> Connector:
        if provider not in self.connectors:
            raise KeyError(f"Unknown network '{provider}'")
        return self.connectors[provider]

    # ---- preferences stored in the DB (editable from the Settings screen)
    def prefs(self) -> dict:
        with self.db.session() as s:
            row = s.get(KV, "prefs")
            stored = dict(row.value) if row else {}
        out = {**DEFAULT_PREFS, **stored}
        if self.settings.notify_webhook_url and not out.get("notify_webhook_url"):
            out["notify_webhook_url"] = self.settings.notify_webhook_url
        return out

    def update_prefs(self, changes: dict) -> dict:
        with self.db.session() as s:
            row = s.get(KV, "prefs")
            if not row:
                row = KV(key="prefs", value={})
                s.add(row)
            merged = {**(row.value or {}), **changes}
            row.value = merged
        return self.prefs()

    def public_base_url(self) -> str:
        """Public https base that serves media + OAuth callbacks: env, then Settings/tunnel, then the https origin
        a signed-in browser last used (so a hosted install needs no configuration and follows a changed URL)."""
        p = self.prefs()
        return (self.settings.public_base_url or p.get("public_base_url") or p.get("detected_base_url") or "").rstrip("/")

    def note_origin(self, origin: str) -> None:
        """Remember the https origin the UI is served on (called for signed-in requests only)."""
        origin = origin.rstrip("/")
        if not origin.startswith("https://") or origin == self._detected:
            return
        self._detected = origin
        if self.prefs().get("detected_base_url") != origin:
            self.update_prefs({"detected_base_url": origin})

    def redirect_uri(self, provider: str, origin: str = "") -> str:
        """origin = the browser's own origin (scheme://host), used when no public URL is configured, e.g.
        http://localhost:8787 locally. The UI app answers /oauth/<network>/callback too."""
        base = self.public_base_url() or origin.rstrip("/") or f"http://localhost:{self.settings.public_port}"
        return f"{base}/oauth/{provider}/callback"

    # ---- tokens
    def channel_auth(self, ch: Channel):
        from .connectors import ChannelAuth
        return ChannelAuth(
            id=ch.id, external_id=ch.external_id, username=ch.username,
            access_token=self.cipher.decrypt(ch.access_token_enc),
            refresh_token=self.cipher.decrypt(ch.refresh_token_enc),
            token_expires_at=ch.token_expires_at, refresh_expires_at=ch.refresh_expires_at,
            token_obtained_at=ch.token_obtained_at, scopes=ch.scopes or "", meta=dict(ch.meta or {}),
        )

    def store_tokens(self, ch: Channel, bundle) -> None:
        ch.access_token_enc = self.cipher.encrypt(bundle.access_token)
        if bundle.refresh_token:
            ch.refresh_token_enc = self.cipher.encrypt(bundle.refresh_token)
        ch.token_expires_at = bundle.expires_at
        if bundle.refresh_expires_at:
            ch.refresh_expires_at = bundle.refresh_expires_at
        ch.token_obtained_at = utcnow()
        if bundle.scopes:
            ch.scopes = bundle.scopes
