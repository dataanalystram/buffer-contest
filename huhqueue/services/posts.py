"""Creating, editing and serialising posts (the composer's backend)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from ..connectors import BrandRules
from ..models import Channel, Media, Post, PostTarget
from . import media as media_svc
from . import scheduling
from .endcard import CTA_CHOICES
from .events import record

WHEN = ("queue", "prioritize", "now", "custom", "draft")
# a post in one of these states "occupies" its media + topic on that channel
DEDUP_ACTIVE = ("pending_approval", "scheduled", "sending", "processing", "needs_action", "sent")


def slug_topic(text: str) -> str:
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:80]


def find_duplicates(s: Session, channel: Channel, medias: list, topic_key: str, exclude_post_id: str | None = None,
                    exclude_target_id: str | None = None) -> list[dict]:
    """Same media file (by SHA-256, whatever the format) or same topic key already used on this channel."""
    hashes = {m.sha256 for m in medias if getattr(m, "sha256", "")}
    topic = slug_topic(topic_key)
    if not hashes and not topic:
        return []
    out = []
    rows = s.scalars(select(PostTarget).where(PostTarget.channel_id == channel.id, PostTarget.status.in_(DEDUP_ACTIVE)))
    for t in rows:
        if t.id == exclude_target_id or (exclude_post_id and t.post_id == exclude_post_id):
            continue
        when = t.published_at or t.scheduled_at
        label = f"{t.status} {when.strftime('%d %b %H:%M') if when else ''}".strip()
        if topic and slug_topic(t.post.topic_key) == topic:
            out.append({"level": "error", "code": "duplicate_topic", "field": "topic_key", "target_id": t.id,
                        "message": f"@{channel.username} already has topic '{topic}' ({label}). Each story goes to a channel once."})
            continue
        if hashes:
            theirs = {m.sha256 for m in (s.get(Media, mid) for mid in (t.media_list or [])) if m and m.sha256}
            if hashes & theirs:
                out.append({"level": "error", "code": "duplicate_media", "field": "media", "target_id": t.id,
                            "message": f"@{channel.username} already has this media ({label}) - the same file is never posted to a channel twice."})
    return out


def common_options(prefs: dict) -> list[dict]:
    """Options every connector gets in the composer (rendered before the network's own)."""
    return [{"key": "follow_cta", "label": f"Follow {prefs.get('cta_handle', '@huhbyte')} call-to-action",
             "type": "select", "default": prefs.get("cta_default", "comment"), "choices": CTA_CHOICES,
             "show_if_media": "video",
             "help": "Video posts: add a branded end card, a first comment, or both."}]


class PostError(Exception):
    def __init__(self, status: int, message: str, issues: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.issues = issues or {}

    def detail(self) -> dict:
        return {"message": self.message, "issues": self.issues}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def parse_when(value, zone_name: str) -> datetime:
    """ISO string (with or without offset) -> naive UTC. Without an offset it is read in `zone_name`."""
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
        except ValueError as e:
            raise PostError(400, f"Bad date/time '{value}' - use ISO format like 2026-10-23T09:30") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(zone_name))
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def resolve_channels(session: Session, refs: list[str]) -> list[Channel]:
    chans = list(session.scalars(select(Channel).where(Channel.status != "disconnected")))
    out: list[Channel] = []
    for ref in refs:
        ref = str(ref).strip().lstrip("@")
        hit = [c for c in chans if c.id == ref]
        if not hit and ":" in ref:
            prov, user = ref.split(":", 1)
            hit = [c for c in chans if c.provider == prov and c.username.lower() == user.lstrip("@").lower()]
        if not hit:
            hit = [c for c in chans if c.provider == ref] or [c for c in chans if c.username.lower() == ref.lower()]
        if len(hit) != 1:
            raise PostError(400, f"Channel '{ref}' not found or ambiguous (use its id or provider:username)")
        if hit[0] not in out:
            out.append(hit[0])
    return out


def load_media(session: Session, ids: list[str]) -> list[Media]:
    out = []
    for i in ids or []:
        m = session.get(Media, i)
        if not m:
            raise PostError(400, f"Media '{i}' not found - upload it first")
        out.append(m)
    return out


def brand_rules(core, username: str = "", auth_mode: str = "") -> BrandRules:
    p = core.prefs()
    return BrandRules(hashtag_limit=int(p.get("hashtag_limit", 5)), has_public_url=bool(core.public_base_url()),
                      username=username or "", auth_mode=auth_mode or "")


def validate_one(core, channel: Channel, caption: str, medias: list[Media], options: dict, topic_key: str = "",
                 exclude_post_id: str | None = None, exclude_target_id: str | None = None) -> list[dict]:
    from sqlalchemy.orm import object_session
    conn = core.connector(channel.provider)
    dups = []
    sess = object_session(channel)
    if sess is not None:
        dups = find_duplicates(sess, channel, medias, topic_key, exclude_post_id, exclude_target_id)
    items = [media_svc.to_item(core, m) for m in medias]
    issues = [i.to_dict() for i in conn.validate(caption or "", items, options or {}, brand_rules(core, channel.username, (channel.meta or {}).get("auth_mode", "")))]
    if any(m.kind == "document" for m in medias) and not getattr(conn, "accepts_documents", False):
        issues.append({"level": "error", "code": "document_unsupported", "field": "media",
                       "message": f"{conn.name} does not take PDF documents (LinkedIn does)."})
    if (options or {}).get("follow_cta") in ("card", "both") and any(m.kind == "video" for m in medias):
        from .endcard import ffmpeg_available
        if not ffmpeg_available():
            issues.append({"level": "error", "code": "ffmpeg_missing", "field": "options",
                           "message": "End card needs ffmpeg installed on this computer."})
    issues += dups
    if channel.status != "active":
        issues.append({"level": "warning", "code": "channel_status", "field": "channel",
                       "message": f"{channel.username}: channel needs reconnecting before posts can go out."})
    return issues


def _merge_options(core, channel: Channel, options: dict | None) -> dict:
    defaults = {**{f["key"]: f.get("default") for f in common_options(core.prefs())},
                **core.connector(channel.provider).default_options()}
    return {**defaults, **{k: v for k, v in (options or {}).items()}}


def apply_when(core, session: Session, t: PostTarget, ch: Channel, when: str, at, override: bool,
               zone_name: str | None) -> list[str]:
    notes: list[str] = []
    if when not in WHEN:
        raise PostError(400, f"'when' must be one of {', '.join(WHEN)}")
    t.override_rate_limit = bool(override)
    t.attempts, t.last_error, t.next_attempt_at = 0, "", None
    now = utcnow()
    if when == "draft":
        t.status, t.schedule_mode, t.prioritized = "draft", "draft", False
        t.scheduled_at = parse_when(at, zone_name or ch.timezone) if at else None
    elif when in ("queue", "prioritize"):
        t.status, t.schedule_mode, t.prioritized = "scheduled", "queue", when == "prioritize"
        t.scheduled_at = None
        session.flush()
        missing = scheduling.reflow(session, ch, now)
        if t in missing:
            notes.append(f"{ch.username}: no free posting slot - add posting times or raise the daily limit.")
    else:
        when_dt = now if when == "now" else parse_when(at, zone_name or ch.timezone) if at else None
        if when_dt is None:
            raise PostError(400, "Pick a date and time for a custom schedule")
        if when == "custom" and when_dt < now - timedelta(minutes=1):
            raise PostError(400, "That time is in the past")
        try:
            scheduling.check_rate(session, ch, when_dt, t.id, override)
        except scheduling.RateLimited as e:
            raise PostError(409, e.message) from e
        t.status, t.schedule_mode, t.prioritized, t.scheduled_at = "scheduled", ("now" if when == "now" else "custom"), False, when_dt
    return notes


def hold_for_approval(t: PostTarget, when: str, at, override: bool, zone: str | None, actor: str) -> None:
    t.status, t.schedule_mode, t.prioritized, t.scheduled_at = "pending_approval", "draft", False, None
    t.approval = {"when": when, "scheduled_at": str(at) if at else None, "override_rate_limit": bool(override),
                  "timezone": zone, "requested_by": actor, "requested_at": iso(utcnow())}
    from ..teams import CURRENT_USER, notify_approvers
    if CURRENT_USER.get():   # a teammate asked: the decision notifies them, a reject needs a note, approvers are told
        t.approval["requested_by_user_id"] = CURRENT_USER.get()
        t.approval["kind"] = "human"
        sess = object_session(t)
        if sess is not None:
            notify_approvers(sess, t, actor, CURRENT_USER.get())


def approve(core, target_id: str, actor: str = "ui", override_rate_limit: bool = False) -> dict:
    with core.db.session() as s:
        t = s.get(PostTarget, target_id)
        if not t or t.status != "pending_approval":
            raise PostError(404, "No post waiting for approval with that id")
        a = dict(t.approval or {})
        dups = find_duplicates(s, t.channel, load_media(s, t.media_list), t.post.topic_key, exclude_target_id=t.id)
        if dups:
            raise PostError(409, dups[0]["message"], {t.channel_id: dups})
        notes = apply_when(core, s, t, t.channel, a.get("when") or "queue", a.get("scheduled_at"),
                           a.get("override_rate_limit", False) or override_rate_limit, a.get("timezone"))
        t.approval = {**a, "approved_by": actor, "approved_at": iso(utcnow())}
        record(core, s, f"Approved by {actor} (requested by {a.get('requested_by')})", level="success",
               target_id=t.id, channel_id=t.channel_id)
        s.flush()
        out = serialize_target(core, t)
        out["notes"] = notes
        return out


def reject(core, target_id: str, actor: str = "ui") -> dict:
    with core.db.session() as s:
        t = s.get(PostTarget, target_id)
        if not t or t.status != "pending_approval":
            raise PostError(404, "No post waiting for approval with that id")
        t.status, t.schedule_mode = "draft", "draft"
        t.approval = {**(t.approval or {}), "rejected_by": actor, "rejected_at": iso(utcnow())}
        record(core, s, f"Rejected by {actor} - moved to drafts", level="warning", target_id=t.id, channel_id=t.channel_id)
        s.flush()
        return serialize_target(core, t)


def create_post(core, payload: dict, source: str = "ui", approval_by: str | None = None) -> dict:
    when = payload.get("when") or core.prefs().get("default_when", "queue")
    with core.db.session() as s:
        refs = payload.get("channel_ids") or [c.get("channel_id") for c in payload.get("channels") or []]
        if not refs:
            raise PostError(400, "Pick at least one channel")
        chans = resolve_channels(s, refs)
        per = {c.get("channel_id"): c for c in payload.get("channels") or []}
        base_media = load_media(s, payload.get("media_ids") or [])
        post = Post(caption=payload.get("caption") or "", media_ids=[m.id for m in base_media],
                    tags=payload.get("tags") or [], note=payload.get("note") or "", source=source,
                    idea_id=payload.get("idea_id"), topic_key=slug_topic(payload.get("topic_key") or ""),
                    campaign=(payload.get("campaign") or "").strip()[:80])
        s.add(post)
        s.flush()
        issues: dict = {}
        notes: list[str] = []
        for ch in chans:
            spec = per.get(ch.id) or per.get(f"{ch.provider}:{ch.username}") or {}
            cap_over = spec.get("caption")
            med_over = spec.get("media_ids")
            medias = load_media(s, med_over) if med_over is not None else base_media
            opts = _merge_options(core, ch, {**(payload.get("options") or {}), **(spec.get("options") or {})})
            caption = cap_over if cap_over is not None else post.caption
            found = validate_one(core, ch, caption, medias, opts, post.topic_key, exclude_post_id=post.id)
            if when != "draft" and any(i["level"] == "error" for i in found):
                issues[ch.id] = found
            t = PostTarget(post=post, channel=ch, caption_override=cap_over,
                           media_override=[m.id for m in medias] if med_over is not None else None, options=opts)
            s.add(t)
            s.flush()
            if approval_by and when != "draft":
                hold_for_approval(t, when, payload.get("scheduled_at"), payload.get("override_rate_limit", False),
                                  payload.get("timezone"), approval_by)
                notes.append(f"{ch.username}: waiting for approval in Publish → Approvals.")
                continue
            notes += apply_when(core, s, t, ch, when, payload.get("scheduled_at"), payload.get("override_rate_limit", False),
                                payload.get("timezone"))
        if issues:
            dup = all(i["code"].startswith("duplicate") for lst in issues.values() for i in lst if i["level"] == "error")
            if dup:
                raise PostError(409, "Duplicate: this media / topic is already on that channel - not scheduled.", issues)
            raise PostError(422, "Fix these before scheduling (or save as a draft)", issues)
        for t in post.targets:
            record(core, s, f"Created ({when}) for {t.channel.username}", target_id=t.id, channel_id=t.channel_id)
        s.flush()
        out = serialize_post(core, post)
        out["notes"] = notes
        return out


def update_target(core, target_id: str, payload: dict, approval_by: str | None = None) -> dict:
    with core.db.session() as s:
        t = s.get(PostTarget, target_id)
        if not t:
            raise PostError(404, "Post not found")
        if t.status in ("sending", "processing"):
            raise PostError(409, "This post is being sent right now")
        if t.status == "sent" and payload.get("when") not in (None,):
            raise PostError(409, "Already sent - duplicate it instead")
        ch = t.channel
        if "caption" in payload:
            t.caption_override = payload["caption"]
        if "media_ids" in payload:
            t.media_override = [m.id for m in load_media(s, payload["media_ids"])]
        if "options" in payload:
            t.options = {**(t.options or {}), **(payload["options"] or {})}
        if "tags" in payload:
            t.post.tags = payload["tags"]
        if "topic_key" in payload:
            t.post.topic_key = slug_topic(payload["topic_key"] or "")
        if "campaign" in payload:
            t.post.campaign = (payload["campaign"] or "").strip()[:80]
        if "note" in payload:
            t.post.note = str(payload["note"] or "")[:4000]
        when = payload.get("when")
        notes: list[str] = []
        if when:
            medias = load_media(s, t.media_list)
            found = validate_one(core, ch, t.caption, medias, t.options, t.post.topic_key, exclude_target_id=t.id)
            if when != "draft" and any(i["level"] == "error" for i in found):
                dup = all(i["code"].startswith("duplicate") for i in found if i["level"] == "error")
                raise PostError(409 if dup else 422, "Duplicate - already on this channel" if dup else "Fix these before scheduling", {ch.id: found})
            was_queue = t.schedule_mode == "queue"
            if approval_by and when != "draft":
                hold_for_approval(t, when, payload.get("scheduled_at"), payload.get("override_rate_limit", False),
                                  payload.get("timezone"), approval_by)
                if was_queue:
                    scheduling.reflow(s, ch, utcnow())
                record(core, s, f"{approval_by} asked to {when} - waiting for approval", target_id=t.id, channel_id=ch.id)
                s.flush()
                return {**serialize_target(core, t), "notes": ["Waiting for approval"]}
            notes = apply_when(core, s, t, ch, when, payload.get("scheduled_at"),
                               payload.get("override_rate_limit", t.override_rate_limit), payload.get("timezone"))
            if was_queue and t.schedule_mode != "queue":
                scheduling.reflow(s, ch, utcnow())
            record(core, s, f"Rescheduled ({when})", target_id=t.id, channel_id=ch.id)
        s.flush()
        out = serialize_target(core, t)
        out["notes"] = notes
        return out


def delete_target(core, target_id: str) -> None:
    with core.db.session() as s:
        t = s.get(PostTarget, target_id)
        if not t:
            raise PostError(404, "Post not found")
        if t.status in ("sending", "processing"):
            raise PostError(409, "This post is being sent right now")
        ch, post = t.channel, t.post
        s.delete(t)
        s.flush()
        if not post.targets:
            s.delete(post)
        scheduling.reflow(s, ch, utcnow())


def duplicate_target(core, target_id: str) -> dict:
    with core.db.session() as s:
        t = s.get(PostTarget, target_id)
        if not t:
            raise PostError(404, "Post not found")
        post = Post(caption=t.caption, media_ids=list(t.media_list), tags=list(t.post.tags or []), source="ui")
        s.add(post)
        nt = PostTarget(post=post, channel=t.channel, options=dict(t.options or {}), status="draft", schedule_mode="draft")
        s.add(nt)
        s.flush()
        return serialize_target(core, nt)


FORMATS = ["post", "carousel", "reel", "story", "short", "video"]
_FORMAT_ALIASES = {"feed": "post", "single": "post", "image": "post", "photo": "carousel", "carousel": "carousel",
                   "reel": "reel", "story": "story", "short": "short", "video": "video", "post": "post", "thread": "post"}
_FORMAT_KEYS = {"instagram": "ig_type", "tiktok": "tt_format", "youtube": "yt_format", "x": "x_type", "linkedin": "li_format", "linkedin_page": "li_format", "fake": "fk_format"}


def post_format(provider: str, options: dict, media: list[dict]) -> str:
    """One filterable format per post: post | carousel | reel | story | short | video."""
    v = (options or {}).get(_FORMAT_KEYS.get(provider, ""), "") or ""
    if v in _FORMAT_ALIASES:
        return _FORMAT_ALIASES[v]
    videos = sum(1 for m in media if m.get("kind") == "video")
    if videos and len(media) == 1:
        return {"instagram": "reel", "youtube": "short", "tiktok": "video"}.get(provider, "video")
    if len(media) > 1:
        return "carousel"
    return "post"


def serialize_target(core, t: PostTarget, validate: bool = False) -> dict:
    ch = t.channel
    zone = scheduling.tz(ch)
    medias = []
    with_core_session = None  # media rows are loaded lazily through the same session as t
    from sqlalchemy.orm import object_session
    s = object_session(t)
    for mid in t.media_list or []:
        m = s.get(Media, mid) if s else None
        if m:
            medias.append(media_svc.media_dict(core, m))
    local = scheduling.to_local(t.scheduled_at, zone) if t.scheduled_at else None
    out = {
        "id": t.id, "post_id": t.post_id, "channel_id": ch.id,
        "channel": {"id": ch.id, "provider": ch.provider, "username": ch.username, "avatar_url": ch.avatar_url,
                    "display_name": ch.display_name, "timezone": ch.timezone},
        "caption": t.caption, "caption_override": t.caption_override is not None,
        "media": medias, "options": t.options or {}, "tags": t.post.tags or [],
        "status": t.status, "schedule_mode": t.schedule_mode, "prioritized": t.prioritized,
        "scheduled_at": iso(t.scheduled_at), "scheduled_local": local.strftime("%a %d %b %Y, %H:%M") if local else None,
        "scheduled_local_iso": local.strftime("%Y-%m-%dT%H:%M") if local else None,
        "override_rate_limit": t.override_rate_limit, "attempts": t.attempts, "last_error": t.last_error,
        "next_attempt_at": iso(t.next_attempt_at), "external_id": t.external_id, "permalink": t.permalink,
        "published_at": iso(t.published_at), "insights": t.insights or {}, "insights_at": iso(t.insights_at),
        "created_at": iso(t.created_at), "updated_at": iso(t.updated_at), "source": t.post.source,
        "approval": t.approval or {}, "topic_key": t.post.topic_key, "campaign": t.post.campaign or "",
        "note": t.post.note or "",
        "format": post_format(ch.provider, t.options or {}, medias),
    }
    if validate:
        out["issues"] = validate_one(core, ch, t.caption, [s.get(Media, m) for m in t.media_list if s.get(Media, m)], t.options,
                                     t.post.topic_key, exclude_target_id=t.id)
    return out


def serialize_post(core, post: Post) -> dict:
    return {"id": post.id, "caption": post.caption, "media_ids": post.media_ids, "tags": post.tags,
            "created_at": iso(post.created_at), "targets": [serialize_target(core, t) for t in post.targets]}


def suggest_formats(core, s: Session, channel_ids: list[str], caption: str, media_ids: list[str]) -> dict:
    medias = load_media(s, media_ids)
    items = [media_svc.to_item(core, m) for m in medias]
    out = {}
    for ch in resolve_channels(s, channel_ids):
        conn = core.connector(ch.provider)
        if not conn.formats():
            continue
        val, why = conn.suggest_format(caption or "", items)
        out[ch.id] = {"key": conn.format_key, "format": val, "reason": why, "formats": conn.formats()}
    return out
