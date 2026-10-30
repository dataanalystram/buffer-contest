"""SQLite schema (SQLAlchemy 2.0)."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # stored as naive UTC


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


class Base(DeclarativeBase):
    pass


class Channel(Base):
    __tablename__ = "channels"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ch"))
    provider: Mapped[str] = mapped_column(String, index=True)
    external_id: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String, default="")
    display_name: Mapped[str] = mapped_column(String, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    access_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    refresh_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    token_obtained_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scopes: Mapped[str] = mapped_column(Text, default="")
    # active | needs_reconnect | disconnected
    status: Mapped[str] = mapped_column(String, default="active")
    status_detail: Mapped[str] = mapped_column(Text, default="")
    timezone: Mapped[str] = mapped_column(String, default="Europe/Dublin")
    daily_limit: Mapped[int] = mapped_column(Integer, default=2)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    # the network rate-limited this account: the worker publishes nothing here before this time (naive UTC)
    rate_limited_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    slots: Mapped[list["Slot"]] = relationship(back_populates="channel", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("provider", "external_id", name="uq_channel_provider_ext"),)


class Slot(Base):
    """A recurring posting time for a channel: weekday 0=Mon..6=Sun, time HH:MM in the channel timezone."""
    __tablename__ = "slots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    time: Mapped[str] = mapped_column(String)  # "HH:MM"
    channel: Mapped[Channel] = relationship(back_populates="slots")


class Media(Base):
    __tablename__ = "media"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("md"))
    filename: Mapped[str] = mapped_column(String)
    stored_name: Mapped[str] = mapped_column(String)        # file on disk inside media dir
    public_token: Mapped[str] = mapped_column(String, default=lambda: secrets.token_urlsafe(24))
    mime: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)               # image | video
    size: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    jpeg_name: Mapped[str | None] = mapped_column(String, nullable=True)  # JPEG copy for Instagram
    alt_text: Mapped[str] = mapped_column(Text, default="")
    endcard_name: Mapped[str | None] = mapped_column(String, nullable=True)  # video + "Follow @" end card
    sha256: Mapped[str] = mapped_column(String, default="", index=True)    # content hash, for dedup
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)   # seconds (videos)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Post(Base):
    """What you write once in the composer. Fans out into one PostTarget per channel."""
    __tablename__ = "posts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("po"))
    caption: Mapped[str] = mapped_column(Text, default="")
    media_ids: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default="")
    # stable key for "the same story" (e.g. "2026-10-22-gpt6-preview"); never posted twice to one channel
    topic_key: Mapped[str] = mapped_column(String, default="", index=True)
    campaign: Mapped[str] = mapped_column(String, default="", index=True)  # free-text campaign label for filters
    source: Mapped[str] = mapped_column(String, default="ui")  # ui | api | cli
    idea_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    targets: Mapped[list["PostTarget"]] = relationship(back_populates="post", cascade="all, delete-orphan")


TARGET_STATUSES = ("draft", "pending_approval", "scheduled", "sending", "processing", "needs_action", "sent", "failed", "cancelled")


class PostTarget(Base):
    __tablename__ = "post_targets"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("pt"))
    post_id: Mapped[str] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    caption_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_override: Mapped[list | None] = mapped_column(JSON, nullable=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    # queue (flows into slots) | custom (pinned time) | now | draft
    schedule_mode: Mapped[str] = mapped_column(String, default="queue")
    prioritized: Mapped[bool] = mapped_column(Boolean, default=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="draft", index=True)
    override_rate_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    provider_state: Mapped[dict] = mapped_column(JSON, default=dict)
    external_id: Mapped[str] = mapped_column(String, default="")
    permalink: Mapped[str] = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    insights: Mapped[dict] = mapped_column(JSON, default=dict)
    insights_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # when an agent token needs human approval, the requested schedule waits here
    approval: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    post: Mapped[Post] = relationship(back_populates="targets")
    channel: Mapped[Channel] = relationship()

    @property
    def caption(self) -> str:
        return self.caption_override if self.caption_override is not None else (self.post.caption if self.post else "")

    @property
    def media_list(self) -> list:
        return self.media_override if self.media_override is not None else (self.post.media_ids if self.post else [])


class PublishAttempt(Base):
    """One call to a network's publish (or the poll that finished it): the post's delivery log."""
    __tablename__ = "publish_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_id: Mapped[str] = mapped_column(ForeignKey("post_targets.id", ondelete="CASCADE"), index=True)
    channel_id: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # started | published | processing | inbox | retry | failed | auth | unconfirmed | interrupted
    outcome: Mapped[str] = mapped_column(String, default="started")
    error_code: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    retry_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Event(Base):
    """Timeline + notifications ("Sending...", "Sent", "Failed: ...")."""
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String, default="info")  # info | success | warning | error
    kind: Mapped[str] = mapped_column(String, default="log")
    message: Mapped[str] = mapped_column(Text)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    channel_id: Mapped[str | None] = mapped_column(String, nullable=True)
    notify: Mapped[bool] = mapped_column(Boolean, default=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False)


class Idea(Base):
    __tablename__ = "ideas"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("id"))
    title: Mapped[str] = mapped_column(String, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    column: Mapped[str] = mapped_column(String, default="unassigned")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    media_ids: Mapped[list] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class OAuthState(Base):
    __tablename__ = "oauth_states"
    state: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String)
    code_verifier: Mapped[str] = mapped_column(String, default="")
    redirect_uri: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class KV(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class FollowerSnapshot(Base):
    """One row per channel per day (UTC date): the follower total that day, for audience-growth charts (Reports)."""
    __tablename__ = "follower_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String, index=True)          # YYYY-MM-DD (UTC)
    followers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    media_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String, default="profile")   # profile | backfill | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("channel_id", "day", name="uq_follower_snapshot_day"),)


class StorySnapshot(Base):
    """A story's metrics saved while it is live. Networks drop story insights after 24 h, so the worker reads every live
    story every 2 h and keeps the last numbers here for good (Insights > Stories)."""
    __tablename__ = "story_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String, index=True)
    media_type: Mapped[str] = mapped_column(String, default="")
    media_url: Mapped[str] = mapped_column(Text, default="")
    thumbnail_url: Mapped[str] = mapped_column(Text, default="")
    permalink: Mapped[str] = mapped_column(Text, default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    reads: Mapped[int] = mapped_column(Integer, default=0)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("channel_id", "external_id", name="uq_story_snapshot"),)


class Comment(Base):
    """Community inbox: comments pulled from the networks' official comment APIs."""
    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cm"))
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String)
    parent_external_id: Mapped[str | None] = mapped_column(String, nullable=True)
    media_external_id: Mapped[str] = mapped_column(String, default="")
    media_permalink: Mapped[str] = mapped_column(Text, default="")
    media_caption: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    is_own: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="open")  # open | replied | resolved
    kind: Mapped[str] = mapped_column(String, default="comment")  # comment | mention (a post that tagged us)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee: Mapped[str] = mapped_column(String, default="")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    __table_args__ = (UniqueConstraint("channel_id", "external_id", name="uq_comment_ext"),)


class ApiToken(Base):
    """Personal API tokens for the CLI / pipeline. Only a SHA-256 hash is stored."""
    __tablename__ = "api_tokens"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("tk"))
    name: Mapped[str] = mapped_column(String, default="")
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    prefix: Mapped[str] = mapped_column(String, default="")
    # read | write | publish | comments | admin   (see huhqueue/security.py SCOPES)
    scopes: Mapped[list] = mapped_column(JSON, default=lambda: ["read", "write"])
    # MCP tool allow-list; empty = every tool the scopes permit
    tools: Mapped[list] = mapped_column(JSON, default=list)
    require_approval: Mapped[bool] = mapped_column(Boolean, default=True)
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditLog(Base):
    """Every request made with an API token (agents, MCP, CLI) and every approval decision."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor: Mapped[str] = mapped_column(String, default="")        # token name / "ui"
    token_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tool: Mapped[str] = mapped_column(String, default="")         # MCP tool name, if any
    method: Mapped[str] = mapped_column(String, default="")
    path: Mapped[str] = mapped_column(String, default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="")


# =====================================================================================  Pro features (all free)
# Media library columns live on Media itself (title, tags, folder, phash, deleted_at) - see below.
Media.title = mapped_column(String, default="")              # type: ignore[attr-defined]
Media.tags = mapped_column(JSON, default=list)               # type: ignore[attr-defined]
Media.folder = mapped_column(String, default="", index=True)  # type: ignore[attr-defined]
Media.phash = mapped_column(String, default="")              # type: ignore[attr-defined]
Media.deleted_at = mapped_column(DateTime, nullable=True)     # type: ignore[attr-defined]
ApiToken.expires_at = mapped_column(DateTime, nullable=True)  # type: ignore[attr-defined]
ApiToken.allowed_origins = mapped_column(JSON, default=list)  # type: ignore[attr-defined]  CORS allow-list (headless)
ApiToken.user_id = mapped_column(String, nullable=True)       # type: ignore[attr-defined]  token capped by this user's role
# Agent safety layer: per-token daily caps, channel scope, MCP auto-confirm; 0 / [] = no limit
ApiToken.max_public_per_day = mapped_column(Integer, default=0)    # type: ignore[attr-defined]
ApiToken.max_actions_per_day = mapped_column(Integer, default=0)   # type: ignore[attr-defined]
ApiToken.allowed_channel_ids = mapped_column(JSON, default=list)   # type: ignore[attr-defined]
ApiToken.auto_confirm = mapped_column(Boolean, default=False)      # type: ignore[attr-defined]  MCP public actions skip preview+confirm
# richer audit trail: which registry action, its risk, a hash of the input, the ids it created and a short result
AuditLog.action = mapped_column(String, default="")       # type: ignore[attr-defined]
AuditLog.risk = mapped_column(String, default="", index=True)   # type: ignore[attr-defined]
AuditLog.input_hash = mapped_column(String, default="")   # type: ignore[attr-defined]
AuditLog.created_ids = mapped_column(JSON, default=list)  # type: ignore[attr-defined]
AuditLog.result = mapped_column(String, default="")       # type: ignore[attr-defined]


class Revision(Base):
    """Version history of a post (caption, media, options, tags) - one row per change."""
    __tablename__ = "revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    post_id: Mapped[str] = mapped_column(String, index=True)
    rev: Mapped[int] = mapped_column(Integer, default=1)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    actor: Mapped[str] = mapped_column(String, default="")
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class PostNote(Base):
    """Internal team notes on a post (never published), with @mentions."""
    __tablename__ = "post_notes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("nt"))
    post_id: Mapped[str] = mapped_column(String, index=True)
    author: Mapped[str] = mapped_column(String, default="")
    author_id: Mapped[str | None] = mapped_column(String, nullable=True)   # the teammate (users.id) who wrote it
    text: Mapped[str] = mapped_column(Text, default="")
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Contact(Base):
    """A person who comments on your posts (built from comments; notes/tags/assignee are yours)."""
    __tablename__ = "contacts"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ct"))
    handle: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    assignee: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    """A person who signs in. The first (setup) password belongs to the Owner."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("us"))
    email: Mapped[str] = mapped_column(String, unique=True)
    name: Mapped[str] = mapped_column(String, default="")
    role: Mapped[str] = mapped_column(String, default="editor")   # owner | admin | editor | contributor | viewer
    password_hash: Mapped[str] = mapped_column(String, default="")
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    recovery_hashes: Mapped[list] = mapped_column(JSON, default=list)
    invite_token_hash: Mapped[str] = mapped_column(String, default="")
    channel_ids: Mapped[list] = mapped_column(JSON, default=list)  # legacy allow-list; empty = every channel
    # per-channel access matrix: {channel_id: "none" | "viewer" | "contributor" | "editor"}; missing = the user's role
    channel_access: Mapped[dict] = mapped_column(JSON, default=dict)
    handle: Mapped[str] = mapped_column(String, default="", index=True)   # @mention handle
    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invited_by: Mapped[str] = mapped_column(String, default="")
    invited_by_id: Mapped[str | None] = mapped_column(String, nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class UserSession(Base):
    """One signed-in browser. The cookie carries `v2.<id>.<secret>`; only a SHA-256 of the secret is stored."""
    __tablename__ = "user_sessions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ss"))
    user_id: Mapped[str] = mapped_column(String, index=True)
    secret_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String, default="")
    user_agent: Mapped[str] = mapped_column(Text, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# notifications addressed to one teammate (@mentions, approval decisions); NULL = everyone
Event.user_id = mapped_column(String, nullable=True, index=True)  # type: ignore[attr-defined]


class Link(Base):
    """Short link (/l/{code}) with UTM parameters and click counts."""
    __tablename__ = "links"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("ln"))
    code: Mapped[str] = mapped_column(String, unique=True)
    url: Mapped[str] = mapped_column(Text)
    campaign: Mapped[str] = mapped_column(String, default="", index=True)
    post_id: Mapped[str] = mapped_column(String, default="")
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String, default="")
    source: Mapped[str] = mapped_column(String, default="manual")      # manual | utm | bio
    target_id: Mapped[str] = mapped_column(String, default="")         # post target a UTM link was made for
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LinkClick(Base):
    __tablename__ = "link_clicks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_id: Mapped[str] = mapped_column(String, index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    referrer: Mapped[str] = mapped_column(String, default="")
    ua_hash: Mapped[str] = mapped_column(String, default="")      # hashed, no cookies
    device: Mapped[str] = mapped_column(String, default="")       # mobile | tablet | desktop (from the user agent, not stored)


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wh"))
    url: Mapped[str] = mapped_column(Text)
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    events: Mapped[list] = mapped_column(JSON, default=list)       # empty = all
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("wd"))
    webhook_id: Mapped[str] = mapped_column(String, index=True)
    event: Mapped[str] = mapped_column(String, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending | delivered | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    response_code: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Automation(Base):
    """Recurring posts, evergreen recycling and RSS auto-post rules."""
    __tablename__ = "automations"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("au"))
    kind: Mapped[str] = mapped_column(String)                      # recurring | evergreen | rss
    name: Mapped[str] = mapped_column(String, default="")
    channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# =====================================================================================  Growth: links & automation
class Campaign(Base):
    """A named group of posts across channels (Post.campaign holds the name), with a colour and a date range."""
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cp"))
    name: Mapped[str] = mapped_column(String, unique=True)
    color: Mapped[str] = mapped_column(String, default="#7C5CE0")
    description: Mapped[str] = mapped_column(Text, default="")
    start_date: Mapped[str] = mapped_column(String, default="")   # YYYY-MM-DD (inclusive)
    end_date: Mapped[str] = mapped_column(String, default="")     # YYYY-MM-DD (inclusive)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ChannelGroup(Base):
    """A named set of channels picked together in the composer (Buffer's 'channel groups')."""
    __tablename__ = "channel_groups"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("cg"))
    name: Mapped[str] = mapped_column(String)
    channel_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BioPage(Base):
    """Public link-in-bio page served at /bio/{slug}."""
    __tablename__ = "bio_pages"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("bp"))
    slug: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String, default="")
    bio: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    theme: Mapped[dict] = mapped_column(JSON, default=dict)
    blocks: Mapped[list] = mapped_column(JSON, default=list)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    daily_views: Mapped[dict] = mapped_column(JSON, default=dict)     # {"YYYY-MM-DD": n}, last 400 days
    domain: Mapped[str] = mapped_column(String, default="")           # optional custom domain (links.example.com) serving this page at /
    settings: Mapped[dict] = mapped_column(JSON, default=dict)        # seo title/description, og image, share button, stats_public
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class BioSubscriber(Base):
    """Email signups from a bio page's email block (double opt-in is left to the mail tool the list is exported to)."""
    __tablename__ = "bio_subscribers"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: new_id("bs"))
    page_id: Mapped[str] = mapped_column(String, index=True)
    block_id: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String)
    name: Mapped[str] = mapped_column(String, default="")
    referrer: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
