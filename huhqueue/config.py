"""Runtime configuration, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (no extra dependency). Existing env vars win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _bool(v: str | None, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8787
    public_port: int = 8788
    public_base_url: str = ""
    secret_key: str = ""
    api_token: str = ""
    timezone: str = "Europe/Dublin"
    notify_webhook_url: str = ""
    worker_interval: int = 15
    max_attempts: int = 6
    enable_fake: bool = False
    instagram_app_id: str = ""
    instagram_app_secret: str = ""
    instagram_graph_version: str = "v26.0"
    instagram_scopes: str = (
        "instagram_business_basic,instagram_business_content_publish,"
        "instagram_business_manage_comments,instagram_business_manage_insights"
    )
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_scopes: str = "user.info.basic,user.info.profile,user.info.stats,video.publish,video.upload,video.list"
    tiktok_audited: bool = False
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    youtube_scopes: str = ("https://www.googleapis.com/auth/youtube.upload "
                           "https://www.googleapis.com/auth/youtube.readonly "
                           "https://www.googleapis.com/auth/youtube.force-ssl")
    youtube_verified: bool = False
    x_client_id: str = ""
    x_client_secret: str = ""
    x_scopes: str = "tweet.read tweet.write users.read media.write offline.access"
    unsplash_access_key: str = ""   # Unsplash photo picker (free: unsplash.com/developers)
    linkedin_client_id: str = ""
    linkedin_client_secret: str = ""
    linkedin_scopes: str = "openid profile email w_member_social"
    linkedin_api_version: str = "202605"   # LinkedIn-Version header (YYYYMM); LinkedIn retires versions after ~1 year
    linkedin_pages_enabled: bool = False
    linkedin_pages_client_id: str = ""
    linkedin_pages_client_secret: str = ""
    linkedin_pages_scopes: str = "r_organization_social w_organization_social rw_organization_admin"
    ui_base_url: str = ""  # where the browser UI lives when it is not on localhost (hosted installs)
    admin_password: str = ""
    cookie_secure: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'huhqueue.db').as_posix()}"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def local_base_url(self) -> str:
        return f"http://{'localhost' if self.host in ('127.0.0.1', '0.0.0.0') else self.host}:{self.port}"


def load_settings(env_file: str | os.PathLike | None = None) -> Settings:
    _load_dotenv(Path(env_file) if env_file else Path.cwd() / ".env")
    e = os.environ.get
    data_dir = Path(e("HUHQ_DATA_DIR", "./data")).expanduser().resolve()
    s = Settings(
        data_dir=data_dir,
        host=e("HUHQ_HOST", "127.0.0.1"),
        port=int(e("HUHQ_PORT", "8787")),
        public_port=int(e("HUHQ_PUBLIC_PORT", "8788")),
        public_base_url=(e("HUHQ_PUBLIC_BASE_URL", "") or "").rstrip("/"),
        secret_key=e("HUHQ_SECRET_KEY", ""),
        api_token=e("HUHQ_API_TOKEN", ""),
        timezone=e("HUHQ_TIMEZONE", "Europe/Dublin"),
        notify_webhook_url=e("HUHQ_NOTIFY_WEBHOOK_URL", ""),
        worker_interval=int(e("HUHQ_WORKER_INTERVAL_SECONDS", "15")),
        max_attempts=int(e("HUHQ_MAX_ATTEMPTS", "6")),
        enable_fake=_bool(e("HUHQ_ENABLE_FAKE"), False),
        instagram_app_id=e("INSTAGRAM_APP_ID", ""),
        instagram_app_secret=e("INSTAGRAM_APP_SECRET", ""),
        instagram_graph_version=e("INSTAGRAM_GRAPH_VERSION", "v26.0"),
        instagram_scopes=e("INSTAGRAM_SCOPES", Settings.instagram_scopes),
        tiktok_client_key=e("TIKTOK_CLIENT_KEY", ""),
        tiktok_client_secret=e("TIKTOK_CLIENT_SECRET", ""),
        tiktok_scopes=e("TIKTOK_SCOPES", Settings.tiktok_scopes),
        tiktok_audited=_bool(e("TIKTOK_AUDITED"), False),
        youtube_client_id=e("YOUTUBE_CLIENT_ID", ""),
        youtube_client_secret=e("YOUTUBE_CLIENT_SECRET", ""),
        youtube_scopes=e("YOUTUBE_SCOPES", Settings.youtube_scopes),
        youtube_verified=_bool(e("YOUTUBE_VERIFIED"), False),
        x_client_id=e("X_CLIENT_ID", ""),
        x_client_secret=e("X_CLIENT_SECRET", ""),
        x_scopes=e("X_SCOPES", Settings.x_scopes),
        unsplash_access_key=e("UNSPLASH_ACCESS_KEY", ""),
        linkedin_client_id=e("LINKEDIN_CLIENT_ID", ""),
        linkedin_client_secret=e("LINKEDIN_CLIENT_SECRET", ""),
        linkedin_scopes=e("LINKEDIN_SCOPES", Settings.linkedin_scopes),
        linkedin_api_version=e("LINKEDIN_API_VERSION", Settings.linkedin_api_version),
        linkedin_pages_enabled=_bool(e("LINKEDIN_PAGES_ENABLED"), False),
        linkedin_pages_client_id=e("LINKEDIN_PAGES_CLIENT_ID", ""),
        linkedin_pages_client_secret=e("LINKEDIN_PAGES_CLIENT_SECRET", ""),
        linkedin_pages_scopes=e("LINKEDIN_PAGES_SCOPES", Settings.linkedin_pages_scopes),
        ui_base_url=(e("HUHQ_UI_BASE_URL", "") or "").rstrip("/"),
        admin_password=e("HUHQ_ADMIN_PASSWORD", ""),
        cookie_secure=_bool(e("HUHQ_COOKIE_SECURE"), False),
    )
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.media_dir.mkdir(parents=True, exist_ok=True)
    return s
