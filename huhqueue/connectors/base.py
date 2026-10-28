"""The connector plugin contract.

A connector teaches HuhQueue how to talk to one network. To add a network
(Facebook, YouTube, X, LinkedIn, ...) subclass :class:`Connector`, implement the
methods below and register it, either in ``BUILTIN_CONNECTORS`` or from your
own package through the ``huhqueue.connectors`` entry-point group (see
docs/CONNECTORS.md). The UI renders per-channel options from
``option_schema()``, so a new connector gets a composer section for free.
"""
from __future__ import annotations

import functools
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import httpx


# --------------------------------------------------------------------------- errors
class ConnectorError(Exception):
    retryable = False

    def __init__(self, message: str, *, code: str | None = None, retry_after: int | None = None,
                 raw: Any = None, status: int | None = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.retry_after = retry_after
        self.raw = raw
        self.status = status  # HTTP status, when the error came from a response

    @property
    def rate_limited(self) -> bool:
        """The network said "slow down" for the whole account: the worker holds the channel until retry_after."""
        return self.retryable and (self.status == 429 or self.code in RATE_LIMIT_CODES)


class RetryableError(ConnectorError):
    """Transient: network blip, 5xx, rate limit. The worker retries with backoff."""
    retryable = True


class AuthError(ConnectorError):
    """Token revoked/expired beyond refresh. Channel is flagged 'needs reconnect'."""


class PermanentError(ConnectorError):
    """The network rejected the post for a reason a retry will not fix."""


class NotConfigured(ConnectorError):
    """App keys missing in .env."""


class Unconfirmed(ConnectorError):
    """A create call may have reached the network but its reply was lost, and the post can't be found.
    The worker hands it to a person ("needs action") instead of risking a second post."""


# per-account rate limits / publish caps (HTTP 429 always counts)
RATE_LIMIT_CODES = {"rate_limit", "publish_cap", "rateLimitExceeded", "quotaExceeded", "dailyLimitExceeded",
                    "uploadLimitExceeded", "rate_limit_exceeded", "spam_risk_too_many_posts", "reached_active_user_cap"}


def retry_after_header(resp: httpx.Response) -> int | None:
    """Retry-After in seconds (delta-seconds or an HTTP date), or None."""
    v = (resp.headers.get("retry-after") or "").strip()
    if v.isdigit():
        return int(v)
    try:
        return max(0, int((parsedate_to_datetime(v) - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return None


def http_errors(handle):
    """Wrap a connector's response handler so every network classifies errors the same way: the HTTP status is
    kept on the error, 408/429/5xx are retryable even if the handler said permanent (other 4xx stay permanent),
    and a Retry-After header wins when it asks for a longer wait."""
    @functools.wraps(handle)
    def wrapper(self, resp: httpx.Response):
        try:
            return handle(self, resp)
        except ConnectorError as e:
            code = resp.status_code
            err = e
            if type(e) is PermanentError and (code in (408, 429) or code >= 500):
                err = RetryableError(e.message, code=e.code, retry_after=e.retry_after, raw=e.raw)
            err.status = err.status or code
            ra = retry_after_header(resp)
            if err.retryable and ra and ra > (err.retry_after or 0):
                err.retry_after = ra
            if err is e:
                raise
            raise err from e
    return wrapper


def nothing_sent(e: ConnectorError) -> bool:
    """True when the network clearly did not act on a request: it answered 4xx, or we never connected."""
    return bool(e.status and e.status < 500) or isinstance(e.__cause__, (httpx.ConnectError, httpx.ConnectTimeout))


# --------------------------------------------------------------------------- value objects
@dataclass
class MediaItem:
    id: str
    kind: str  # image | video
    mime: str
    path: Path
    filename: str
    size: int = 0
    width: int | None = None
    height: int | None = None
    alt_text: str = ""
    duration: float | None = None
    sha256: str = ""
    public_url: str | None = None        # original file on the public edge server
    jpeg_public_url: str | None = None   # JPEG copy (Instagram accepts only JPEG images)
    jpeg_path: Path | None = None


@dataclass
class ChannelAuth:
    id: str
    external_id: str
    username: str
    access_token: str | None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    token_obtained_at: datetime | None = None
    scopes: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class PublishContext:
    target_id: str
    channel: ChannelAuth
    caption: str
    media: list[MediaItem]
    options: dict
    state: dict  # provider_state, persisted between publish() and poll() (and across retries)
    recent_publishes: int | None = None  # posts HuhQueue published on this channel in the last 24h (rolling)
    attachments: dict = field(default_factory=dict)  # extra files named by options "<key>_media_id" (e.g. YouTube thumbnail)
    on_checkpoint: Any = None  # set by the worker: saves `state` to the database right away

    def checkpoint(self) -> None:
        """Persist state now, before an irreversible call, so a crash or lost reply resumes instead of re-posting."""
        if self.on_checkpoint:
            self.on_checkpoint(self.state)


@dataclass
class PublishOutcome:
    # published: live on the network | processing: poll again later | inbox: user must finish in the app
    status: str
    external_id: str = ""
    permalink: str = ""
    state: dict = field(default_factory=dict)
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    poll_after_seconds: int = 20


@dataclass
class TokenBundle:
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    scopes: str = ""


@dataclass
class Profile:
    external_id: str
    username: str
    display_name: str = ""
    avatar_url: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class Issue:
    level: str  # error | warning | info
    code: str
    message: str
    field: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "field": self.field}


HASHTAG_RE = re.compile(r"(?<![\w&])#(\w+)", re.UNICODE)
MENTION_RE = re.compile(r"(?<![\w])@([A-Za-z0-9._]{1,30})")


def _fingerprint(text: str | None) -> str:
    """Caption without links (networks shorten them) or whitespace, for matching a post we may have sent."""
    return re.sub(r"\s+", "", re.sub(r"https?://\S+", "", text or "")).lower()[:120]


def count_hashtags(text: str) -> int:
    return len(HASHTAG_RE.findall(text or ""))


def count_mentions(text: str) -> int:
    return len(MENTION_RE.findall(text or ""))


def text_heavy(caption: str) -> bool:
    """Long or list-like captions read better as carousel slides."""
    c = caption or ""
    lines = [x for x in c.splitlines() if x.strip()]
    listy = sum(1 for x in lines if re.match(r"^\s*(\d+[.)]|[-•*]|[0-9]\ufe0f?\u20e3)", x))
    return len(c) > 600 or len(lines) >= 8 or listy >= 3


def utf16_len(text: str) -> int:
    """Length as counted by Meta/TikTok (UTF-16 code units, most emoji = 2)."""
    return len((text or "").encode("utf-16-le")) // 2


# --------------------------------------------------------------------------- threads (X today; Bluesky/Threads later)
THREAD_MAX_PARTS = 25


def thread_parts(options: dict | None) -> list[dict]:
    """The posts that follow the main one in a thread (options["thread"]), as [{"text", "media_ids"}].

    Each part may be a plain string (agents: ["part 2", "part 3"]) or {"text": ..., "media_ids": [...]}.
    Blank parts with no media are dropped, so an empty box in the composer never posts.
    """
    out = []
    for p in (options or {}).get("thread") or []:
        if isinstance(p, str):
            p = {"text": p}
        if not isinstance(p, dict):
            continue
        text = str(p.get("text") or "").strip()
        mids = [str(m) for m in (p.get("media_ids") or []) if m]
        if text or mids:
            out.append({"text": text, "media_ids": mids})
    return out


def split_thread(text: str, limit: int = 280, length=len, numbering: bool = False) -> list[str]:
    """Split a long text into thread parts of at most `limit` (by `length`), breaking at paragraphs, then sentences,
    then words. With numbering, each part ends with " i/n" (room for it is kept inside the limit)."""
    text = (text or "").strip()
    if not text:
        return []
    room = limit - (len(" 99/99") if numbering else 0)

    def pieces(t: str) -> list[str]:
        if length(t) <= room:
            return [t]
        for sep, joiner in ((r"\n\s*\n", "\n\n"), (r"(?<=[.!?…])\s+", " "), (r"\s+", " ")):
            units = [u.strip() for u in re.split(sep, t) if u.strip()]
            if len(units) < 2:
                continue
            out, cur = [], ""
            for u in units:
                for p in pieces(u):
                    cand = f"{cur}{joiner}{p}" if cur else p
                    if length(cand) <= room:
                        cur = cand
                    else:
                        if cur:
                            out.append(cur)
                        cur = p
            if cur:
                out.append(cur)
            return out
        out, cur = [], ""          # one unbreakable token (a very long URL or word)
        for ch in t:
            if length(cur + ch) > room:
                out.append(cur)
                cur = ""
            cur += ch
        return out + ([cur] if cur else [])

    parts = pieces(text)
    if numbering and len(parts) > 1:
        parts = [f"{p} {i}/{len(parts)}" for i, p in enumerate(parts, 1)]
    return parts


@dataclass
class BrandRules:
    hashtag_limit: int = 5          # HuhByte house rule (warning, not an API limit)
    has_public_url: bool = True
    username: str = ""              # the channel's own handle (e.g. so it is not invited as its own collaborator)
    auth_mode: str = ""             # channel.meta["auth_mode"], e.g. Instagram "ig_login" (default) | "fb_login"


# --------------------------------------------------------------------------- the contract
class Connector(ABC):
    id: str = ""
    name: str = ""
    color: str = "#C9ACFA"
    caption_limit: int = 2200
    uses_pkce: bool = False   # oauth.start() makes a PKCE verifier and passes it to authorize_url/exchange_code
    # what the UI may offer for this network
    capabilities: dict = {
        "oauth": True,
        "manual_token": True,
        "insights": False,
        "first_comment": False,
        "notify_mode": True,
        "comments": False,
        "comments_note": "",
        "post_types": [],
    }

    def __init__(self, settings, http: httpx.Client | None = None):
        self.settings = settings
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))

    # ---- configuration / auth
    def is_configured(self) -> bool:
        return True

    def setup_hint(self) -> str:
        return ""

    @abstractmethod
    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str: ...

    @abstractmethod
    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]: ...

    def connect_with_token(self, access_token: str, extra: dict | None = None) -> tuple[TokenBundle, Profile]:
        raise PermanentError(f"{self.name} does not support pasting a token")

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return False

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        return None

    def revoke(self, auth: ChannelAuth) -> None:
        return None

    def account_info(self, auth: ChannelAuth) -> dict:
        """Live info shown in the composer (e.g. TikTok creator_info, IG publishing quota)."""
        return {}

    # ---- post format (first-class choice in the composer)
    format_key: str = ""                 # which option holds the format, e.g. "ig_type"

    def formats(self) -> list[list[str]]:
        """[[value, label], ...] the composer shows as the format picker for this network."""
        return []

    def suggest_format(self, caption: str, media: list["MediaItem"]) -> tuple[str, str]:
        """(format value, human reason). Default: first format."""
        f = self.formats()
        return (f[0][0], "default") if f else ("", "")

    # ---- composing
    def option_schema(self) -> list[dict]:
        """Per-channel composer fields:
        {key, label, type(select|toggle|text|textarea|number), choices, default, help, show_if}"""
        return []

    def default_options(self) -> dict:
        return {f["key"]: f.get("default") for f in self.option_schema() if "default" in f}

    @abstractmethod
    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]: ...

    # ---- publishing
    @abstractmethod
    def publish(self, ctx: PublishContext) -> PublishOutcome: ...

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        raise PermanentError("poll() not implemented for this connector")

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        return {}

    def find_published(self, ctx: PublishContext) -> dict | None:
        """After a lost reply to a create call: look for the post among the account's newest posts (same caption,
        posted since the call) so it is marked sent instead of posted twice. None = can't confirm."""
        want = _fingerprint(ctx.caption)
        since = ctx.state.get("publish_called_at") or ""
        if not want:
            return None
        try:
            items = self.list_media(ctx.channel, limit=10).get("items") or []
        except ConnectorError:
            return None
        for it in items:
            if _fingerprint(it.get("caption")) != want:
                continue
            try:
                ts = datetime.fromisoformat(str(it.get("timestamp")).replace("Z", "+00:00"))
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts
                if since and ts < datetime.fromisoformat(since) - timedelta(minutes=5):
                    continue  # an older post with the same text (e.g. a recurring post)
            except ValueError:
                pass
            return it
        return None

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        """Re-read username/avatar/followers etc. for the channel dashboard."""
        return None

    def list_media(self, auth: ChannelAuth, limit: int = 24, after: str = "") -> dict:
        """Past posts already on the network (also ones not made with HuhQueue), newest first:
        {"items": [{external_id, caption, media_type, product_type, media_url, thumbnail_url, permalink,
                    timestamp (ISO str), like_count, comments_count}], "next": cursor or ""}"""
        return {"items": [], "next": ""}

    def account_insights(self, auth: ChannelAuth, days: int = 28) -> dict:
        """Account-level insights: {"since", "until", "totals": {metric: n}, "series": {metric: [[date, n]]}}."""
        return {}

    def audience_demographics(self, auth: ChannelAuth, timeframe: str = "this_month") -> dict | None:
        """Who follows / engages: {"followers": {"age": {"18-24": n}, "gender": {"F": n}, "country": {"US": n},
        "city": {"Austin, Texas": n}}, "engaged": {...same...}, "notes": [str]}. None = the network's API has no
        audience breakdowns (only networks that set capabilities['demographics'] implement it)."""
        return None

    def list_stories(self, auth: ChannelAuth) -> list[dict]:
        """Stories live right now (they vanish after 24 h): [{external_id, media_type, media_url, thumbnail_url,
        permalink, caption, timestamp (ISO str)}]. Only networks that set capabilities['stories'] implement it."""
        return []

    def story_insights(self, auth: ChannelAuth, external_id: str) -> dict:
        """Metrics of one live story ({views, reach, replies, shares, ...}); readable only while it is live."""
        return {}

    # ---- community (comments inbox)
    def list_comments(self, auth: ChannelAuth, limit_media: int = 10) -> list[dict]:
        """Return recent comments as dicts:
        {external_id, parent_external_id, media_external_id, media_permalink, media_caption,
         author, text, at (datetime, naive UTC), like_count, is_own}"""
        return []

    def list_mentions(self, auth: ChannelAuth, limit: int = 25) -> list[dict]:
        """Posts/comments that mention or tag the account, in the list_comments shape plus kind='mention'.
        Only networks whose official API exposes them set capabilities['mentions']."""
        return []

    def reply_comment(self, auth: ChannelAuth, comment_external_id: str, text: str,
                      media_external_id: str = "") -> str:
        raise PermanentError(f"{self.name} has no public API for replying to comments")

    def post_comment(self, auth: ChannelAuth, media_external_id: str, text: str) -> str:
        raise PermanentError(f"{self.name} has no public API for posting comments")

    def describe(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "caption_limit": self.caption_limit,
            "configured": self.is_configured(),
            "setup_hint": self.setup_hint(),
            "capabilities": self.capabilities,
            "option_schema": self.option_schema(),
            "format_key": self.format_key,
            "formats": self.formats(),
            "history": type(self).list_media is not Connector.list_media,
            "account_insights": type(self).account_insights is not Connector.account_insights,
        }
