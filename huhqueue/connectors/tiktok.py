"""TikTok connector: Login Kit (OAuth v2) + Content Posting API.

Endpoints used (open.tiktokapis.com):
  POST /v2/oauth/token/                        code -> access (24h) + refresh (365d) token; refresh
  POST /v2/oauth/revoke/                       disconnect
  GET  /v2/user/info/                          profile
  POST /v2/post/publish/creator_info/query/    privacy options, duet/stitch/comment flags, max duration
  POST /v2/post/publish/video/init/            DIRECT POST video (FILE_UPLOAD or PULL_FROM_URL)
  POST /v2/post/publish/inbox/video/init/      UPLOAD video to the creator's TikTok inbox (they finish in app)
  POST /v2/post/publish/content/init/          photo posts (PULL_FROM_URL only; DIRECT_POST or MEDIA_UPLOAD)
  PUT  {upload_url}                            chunked upload with Content-Range
  POST /v2/post/publish/status/fetch/          poll
  POST /v2/video/query/                        view/like/comment/share counts (video.list scope)

IMPORTANT (TikTok policy): until TikTok audits your app, every direct post is
forced to SELF_ONLY (private) and the target account itself must be private
("unaudited_client_can_only_post_to_private_accounts"). Use "Send to inbox"
mode to finish the post publicly inside the TikTok app instead.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from .base import (http_errors, nothing_sent, Unconfirmed, ConnectorError, AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem,
                   NotConfigured, PermanentError, Profile, PublishContext, PublishOutcome,
                   RetryableError, TokenBundle, count_hashtags, utf16_len)

AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"

VIDEO_CAPTION_LIMIT = 2200
PHOTO_TITLE_LIMIT = 90
PHOTO_DESC_LIMIT = 4000
PHOTO_MAX = 35
MIN_CHUNK = 5 * 1024 * 1024
DEFAULT_CHUNK = 10 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
VIDEO_MIMES = {"video/mp4", "video/quicktime", "video/webm"}
PRIVACY_LABELS = {
    "PUBLIC_TO_EVERYONE": "Everyone",
    "MUTUAL_FOLLOW_FRIENDS": "Friends",
    "FOLLOWER_OF_CREATOR": "Followers",
    "SELF_ONLY": "Only me",
}

AUTH_CODES = {"access_token_invalid", "scope_not_authorized", "invalid_grant", "token_expired"}
RETRY_CODES = {"rate_limit_exceeded", "internal_error", "spam_risk_too_many_posts", "reached_active_user_cap"}
FRIENDLY = {
    "unaudited_client_can_only_post_to_private_accounts":
        "TikTok: your app is not audited yet, so it can only DIRECT-post to a PRIVATE TikTok account. "
        "Set the TikTok account to private, or switch this post to 'Send to TikTok inbox'.",
    "url_ownership_unverified":
        "TikTok: PULL_FROM_URL needs a verified domain/URL prefix in your TikTok app. Videos use FILE_UPLOAD "
        "automatically; photo posts need a verified domain (see SETUP.md).",
    "privacy_level_option_mismatch": "TikTok: pick one of the privacy options TikTok offers for this account.",
    "spam_risk_too_many_posts": "TikTok: daily API post cap reached for this account - will retry later.",
    "spam_risk_user_banned_from_posting": "TikTok: this account is currently banned from posting.",
}


class TikTokConnector(Connector):
    id = "tiktok"
    name = "TikTok"
    color = "#25F4EE"
    caption_limit = VIDEO_CAPTION_LIMIT
    capabilities = {
        "oauth": True,
        "manual_token": True,
        "insights": True,
        "first_comment": False,
        "notify_mode": True,
        "comments": False,
        "comments_note": ("TikTok's public developer APIs (Login Kit / Content Posting / Display) do not expose "
                          "reading or replying to comments for regular apps, so the TikTok inbox opens TikTok instead."),
        "post_types": ["video", "photo"],
    }

    # ------------------------------------------------------------------ helpers
    @property
    def audited(self) -> bool:
        return bool(getattr(self.settings, "tiktok_audited", False))

    @http_errors
    def _handle(self, resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        err = data.get("error") if isinstance(data, dict) else None
        # oauth endpoints use {"error": "invalid_grant", "error_description": ...}
        if isinstance(err, str):
            code, msg = err, data.get("error_description", err)
        elif isinstance(err, dict):
            code, msg = err.get("code", "ok"), err.get("message", "")
        else:
            code, msg = ("ok" if resp.status_code < 400 else str(resp.status_code)), resp.text[:200]
        if code == "ok" and resp.status_code < 400:
            return data.get("data", data) if isinstance(data, dict) else {}
        label = FRIENDLY.get(code) or f"TikTok error {code}: {msg}"
        if code in AUTH_CODES or resp.status_code == 401:
            raise AuthError(label, code=code, raw=data)
        if code in RETRY_CODES or resp.status_code == 429 or resp.status_code >= 500:
            delay = 6 * 3600 if code.startswith("spam_risk") or code == "reached_active_user_cap" else 120
            raise RetryableError(label, code=code, retry_after=delay, raw=data)
        raise PermanentError(label, code=code, raw=data)

    def _post(self, path: str, token: str, body: dict | None = None, params: dict | None = None) -> dict:
        try:
            r = self.http.post(f"{API}{path}", params=params, json=body or {},
                               headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to TikTok: {e}") from e
        return self._handle(r)

    def _token_call(self, form: dict) -> tuple[TokenBundle, dict]:
        try:
            r = self.http.post(f"{API}/v2/oauth/token/", data=form,
                               headers={"Content-Type": "application/x-www-form-urlencoded", "Cache-Control": "no-cache"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r)
        now = datetime.utcnow()
        return TokenBundle(
            access_token=d["access_token"], refresh_token=d.get("refresh_token"),
            expires_at=now + timedelta(seconds=int(d.get("expires_in", 86400))),
            refresh_expires_at=now + timedelta(seconds=int(d.get("refresh_expires_in", 31536000))),
            scopes=d.get("scope", ""),
        ), d

    # ------------------------------------------------------------------ config/auth
    def is_configured(self) -> bool:
        return bool(self.settings.tiktok_client_key and self.settings.tiktok_client_secret)

    def setup_hint(self) -> str:
        return "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env (TikTok for Developers app). See SETUP.md."

    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        q = {"client_key": self.settings.tiktok_client_key, "scope": self.settings.tiktok_scopes,
             "response_type": "code", "redirect_uri": redirect_uri, "state": state}
        return f"{AUTH_URL}?{urlencode(q)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        bundle, raw = self._token_call({
            "client_key": self.settings.tiktok_client_key, "client_secret": self.settings.tiktok_client_secret,
            "code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})
        return bundle, self._profile(bundle.access_token, raw.get("open_id", ""))

    def connect_with_token(self, access_token: str, extra: dict | None = None) -> tuple[TokenBundle, Profile]:
        extra = extra or {}
        bundle = TokenBundle(access_token=access_token, refresh_token=extra.get("refresh_token") or None,
                             expires_at=datetime.utcnow() + timedelta(seconds=int(extra.get("expires_in") or 86400)),
                             refresh_expires_at=datetime.utcnow() + timedelta(days=365) if extra.get("refresh_token") else None,
                             scopes=self.settings.tiktok_scopes)
        return bundle, self._profile(access_token, "")

    def _profile(self, token: str, open_id: str) -> Profile:
        try:
            fields = "open_id,union_id,avatar_url,display_name"
            scopes = self.settings.tiktok_scopes
            if "user.info.profile" in scopes:
                fields += ",username,profile_deep_link,is_verified"
            if "user.info.stats" in scopes:
                fields += ",follower_count,following_count,likes_count,video_count"
            r = self.http.get(f"{API}/v2/user/info/", params={"fields": fields},
                              headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        user = self._handle(r).get("user", {})
        username = ""
        try:  # creator_info carries the @handle (needs video.publish)
            username = self._post("/v2/post/publish/creator_info/query/", token).get("creator_username", "")
        except (PermanentError, AuthError, RetryableError):
            pass
        return Profile(external_id=user.get("open_id") or open_id,
                       username=user.get("username") or username or user.get("display_name", ""),
                       display_name=user.get("display_name", ""), avatar_url=user.get("avatar_url", ""),
                       meta={"union_id": user.get("union_id"), "followers_count": user.get("follower_count"),
                             "following_count": user.get("following_count"), "likes_count": user.get("likes_count"),
                             "media_count": user.get("video_count"), "account_type": "TikTok creator",
                             "profile_url": user.get("profile_deep_link")})

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return self._profile(auth.access_token, auth.external_id)

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return bool(auth.refresh_token and auth.token_expires_at and auth.token_expires_at - now <= timedelta(hours=2))

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        if not auth.refresh_token:
            return None
        bundle, _ = self._token_call({
            "client_key": self.settings.tiktok_client_key, "client_secret": self.settings.tiktok_client_secret,
            "grant_type": "refresh_token", "refresh_token": auth.refresh_token})
        return bundle

    def revoke(self, auth: ChannelAuth) -> None:
        if not (auth.access_token and self.is_configured()):
            return
        try:
            self.http.post(f"{API}/v2/oauth/revoke/", data={
                "client_key": self.settings.tiktok_client_key, "client_secret": self.settings.tiktok_client_secret,
                "token": auth.access_token}, headers={"Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError:
            pass

    def account_info(self, auth: ChannelAuth) -> dict:
        d = self._post("/v2/post/publish/creator_info/query/", auth.access_token)
        opts = d.get("privacy_level_options") or []
        d["privacy_choices"] = [[o, PRIVACY_LABELS.get(o, o)] for o in opts]
        d["audited"] = self.audited
        return d

    # ------------------------------------------------------------------ format
    format_key = "tt_format"

    def formats(self) -> list[list[str]]:
        return [["video", "Video"], ["photo", "Photo carousel"]]

    def suggest_format(self, caption, media):
        if any(m.kind == "video" for m in media):
            return "video", "Video → TikTok video"
        if media:
            return "photo", f"{len(media)} image(s) → photo carousel"
        return "video", "TikTok needs a video or photos"

    # ------------------------------------------------------------------ composing
    def option_schema(self) -> list[dict]:
        return [
            {"key": "tt_mode", "label": "How to post", "type": "select", "default": "direct",
             "choices": [["direct", "Direct post (we publish it)"], ["inbox", "Send to TikTok inbox / drafts (finish in app)"]]},
            {"key": "privacy_level", "label": "Who can view", "type": "select", "default": "",
             "choices": [["", "Choose…"]] + [[k, v] for k, v in PRIVACY_LABELS.items()],
             "dynamic": "privacy_choices", "show_if": {"tt_mode": ["direct"]},
             "help": "Required for direct posts. Unaudited apps are always 'Only me'."},
            {"key": "title", "label": "Photo title", "type": "text", "default": "",
             "help": "Photo posts only, max 90 characters. The caption becomes the description."},
            {"key": "disable_comment", "label": "Turn off comments", "type": "toggle", "default": False},
            {"key": "disable_duet", "label": "Turn off Duet", "type": "toggle", "default": False},
            {"key": "disable_stitch", "label": "Turn off Stitch", "type": "toggle", "default": False},
            {"key": "video_cover_timestamp_ms", "label": "Cover frame (ms)", "type": "number", "default": None},
            {"key": "brand_organic_toggle", "label": "Promotes my own business", "type": "toggle", "default": False},
            {"key": "brand_content_toggle", "label": "Paid partnership", "type": "toggle", "default": False},
            {"key": "is_aigc", "label": "AI-generated content label", "type": "toggle", "default": False},
            {"key": "auto_add_music", "label": "Auto-add music (photos)", "type": "toggle", "default": True},
            {"key": "video_source", "label": "Video transfer", "type": "select", "default": "upload",
             "choices": [["upload", "Upload file (no domain needed)"], ["url", "Pull from public URL (verified domain)"]]},
        ]

    @staticmethod
    def kind_of(media: list[MediaItem]) -> str:
        if media and all(m.kind == "image" for m in media):
            return "photo"
        return "video"

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        issues: list[Issue] = []
        options = options or {}
        if not media:
            return [Issue("error", "media_required", "TikTok needs a video or 1-35 photos.", "media")]
        fmt = options.get("tt_format") or ""
        if fmt == "photo" and any(m.kind == "video" for m in media):
            issues.append(Issue("error", "format_mismatch", "Photo carousel takes images only.", "options"))
        if fmt == "video" and not any(m.kind == "video" for m in media):
            issues.append(Issue("error", "format_mismatch", "Video format needs a video - or pick Photo carousel.", "options"))
        kinds = {m.kind for m in media}
        if len(kinds) > 1:
            issues.append(Issue("error", "mixed_media", "TikTok posts are either one video or photos - not both.", "media"))
        kind = self.kind_of(media)
        n = utf16_len(caption)
        if kind == "video":
            if len(media) > 1:
                issues.append(Issue("error", "one_video", "TikTok takes one video per post.", "media"))
            if n > VIDEO_CAPTION_LIMIT:
                issues.append(Issue("error", "caption_too_long", f"TikTok video captions max {VIDEO_CAPTION_LIMIT} characters ({n} now).", "caption"))
            for m in media:
                if m.kind == "video" and m.mime not in VIDEO_MIMES:
                    issues.append(Issue("error", "video_format", f"{m.filename}: TikTok takes MP4, MOV or WebM.", "media"))
            if options.get("video_source") == "url" and not rules.has_public_url:
                issues.append(Issue("error", "no_public_url", "Pull-from-URL needs a public media URL.", "media"))
        else:
            if len(media) > PHOTO_MAX:
                issues.append(Issue("error", "photo_max", f"TikTok photo posts take max {PHOTO_MAX} images.", "media"))
            if n > PHOTO_DESC_LIMIT:
                issues.append(Issue("error", "caption_too_long", f"TikTok photo descriptions max {PHOTO_DESC_LIMIT} characters.", "caption"))
            if utf16_len(options.get("title") or "") > PHOTO_TITLE_LIMIT:
                issues.append(Issue("error", "title_too_long", "TikTok photo titles max 90 characters.", "options"))
            issues.append(Issue("warning", "photo_needs_domain", "TikTok photo posts are pulled from a URL on a domain you verified in the TikTok app (a random tunnel URL will be rejected).", "media"))
            if not rules.has_public_url:
                issues.append(Issue("error", "no_public_url", "Photo posts need a public media URL.", "media"))
        mode = options.get("tt_mode", "direct")
        if mode == "direct":
            if not self.audited:
                issues.append(Issue("warning", "unaudited", "TikTok app not audited yet: the post will be PRIVATE (Only me) and your TikTok account must be private. Use 'Send to TikTok inbox' to post publicly from the app.", "options"))
            elif not options.get("privacy_level"):
                issues.append(Issue("error", "privacy_required", "Choose who can view this TikTok.", "options"))
        if options.get("brand_content_toggle") and options.get("privacy_level") == "SELF_ONLY":
            issues.append(Issue("error", "branded_private", "Paid-partnership posts can't be 'Only me'.", "options"))
        if count_hashtags(caption) > rules.hashtag_limit:
            issues.append(Issue("warning", "brand_hashtags", f"{count_hashtags(caption)} hashtags - the house style is {rules.hashtag_limit} max.", "caption"))
        return issues

    # ------------------------------------------------------------------ publishing
    def _post_info(self, ctx: PublishContext, creator: dict) -> dict:
        o = ctx.options or {}
        privacy = o.get("privacy_level") or ""
        allowed = creator.get("privacy_level_options") or []
        if not self.audited:
            privacy = "SELF_ONLY"
        if privacy not in allowed:
            if "SELF_ONLY" in allowed and not self.audited:
                privacy = "SELF_ONLY"
            else:
                raise PermanentError(f"Privacy '{privacy or 'not set'}' isn't offered for this TikTok account (allowed: {', '.join(allowed) or 'none'}).")
        info = {
            "privacy_level": privacy,
            "disable_comment": bool(o.get("disable_comment")) or bool(creator.get("comment_disabled")),
            "disable_duet": bool(o.get("disable_duet")) or bool(creator.get("duet_disabled")),
            "disable_stitch": bool(o.get("disable_stitch")) or bool(creator.get("stitch_disabled")),
            "brand_content_toggle": bool(o.get("brand_content_toggle")),
            "brand_organic_toggle": bool(o.get("brand_organic_toggle")),
            "is_aigc": bool(o.get("is_aigc")),
        }
        if o.get("video_cover_timestamp_ms") not in (None, ""):
            info["video_cover_timestamp_ms"] = int(o["video_cover_timestamp_ms"])
        return info

    @staticmethod
    def chunk_plan(size: int, chunk: int = DEFAULT_CHUNK) -> tuple[int, int]:
        """Return (chunk_size, total_chunk_count) per TikTok's media transfer rules."""
        if size <= MIN_CHUNK:
            return size, 1
        chunk = max(MIN_CHUNK, min(chunk, MAX_CHUNK))
        return chunk, max(1, math.floor(size / chunk))

    def _upload(self, upload_url: str, m: MediaItem, chunk_size: int, count: int) -> None:
        size = m.size or m.path.stat().st_size
        with open(m.path, "rb") as fh:
            for i in range(count):
                start = i * chunk_size
                end = size - 1 if i == count - 1 else start + chunk_size - 1
                fh.seek(start)
                data = fh.read(end - start + 1)
                try:
                    r = self.http.put(upload_url, content=data, headers={
                        "Content-Type": m.mime if m.mime in VIDEO_MIMES else "video/mp4",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                except httpx.HTTPError as e:
                    raise RetryableError(f"TikTok upload interrupted: {e}") from e
                if r.status_code >= 500 or r.status_code == 429:
                    raise RetryableError(f"TikTok upload failed (HTTP {r.status_code})")
                if r.status_code >= 400:
                    raise PermanentError(f"TikTok rejected the upload (HTTP {r.status_code}): {r.text[:200]}")

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        tok = ctx.channel.access_token
        if not tok:
            raise AuthError("TikTok channel has no access token - reconnect it.")
        if ctx.state.get("publish_id") and ctx.state.get("uploaded", True):
            return self.poll(ctx)  # resume, never post twice
        if ctx.state.get("publish_called") and not ctx.state.get("publish_id"):
            raise Unconfirmed("TikTok may already have this post (the reply to the last attempt was lost) - "
                              "check the account and mark it posted or send it again.")

        def init(path: str, body: dict, pull: bool) -> dict:
            if pull:   # TikTok fetches and posts by itself: a lost reply must not lead to a second post
                ctx.state["publish_called"] = True
                ctx.checkpoint()
            try:
                return self._post(path, tok, body)
            except ConnectorError as e:
                if nothing_sent(e):
                    ctx.state.pop("publish_called", None)
                raise
        o = ctx.options or {}
        mode = o.get("tt_mode", "direct")
        kind = self.kind_of(ctx.media)
        if kind == "photo":
            urls = [m.jpeg_public_url or m.public_url for m in ctx.media]
            if not all(urls):
                raise RetryableError("No public media URL - start the tunnel.", retry_after=300)
            body: dict = {
                "post_info": {"title": (o.get("title") or "")[:PHOTO_TITLE_LIMIT], "description": ctx.caption,
                              "auto_add_music": bool(o.get("auto_add_music", True))},
                "source_info": {"source": "PULL_FROM_URL", "photo_cover_index": 0, "photo_images": urls},
                "post_mode": "DIRECT_POST" if mode == "direct" else "MEDIA_UPLOAD",
                "media_type": "PHOTO",
            }
            if mode == "direct":
                creator = self._post("/v2/post/publish/creator_info/query/", tok)
                pi = self._post_info(ctx, creator)
                pi.pop("disable_duet", None); pi.pop("disable_stitch", None); pi.pop("video_cover_timestamp_ms", None)
                body["post_info"].update(pi)
            d = init("/v2/post/publish/content/init/", body, True)
        else:
            m = ctx.media[0]
            if o.get("video_source") == "url":
                if not m.public_url:
                    raise RetryableError("No public media URL - start the tunnel.", retry_after=300)
                source = {"source": "PULL_FROM_URL", "video_url": m.public_url}
                chunk = None
            else:
                size = m.size or m.path.stat().st_size
                cs, cnt = self.chunk_plan(size)
                source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": cs, "total_chunk_count": cnt}
                chunk = (cs, cnt)
            if mode == "direct":
                creator = self._post("/v2/post/publish/creator_info/query/", tok)
                pi = self._post_info(ctx, creator)
                pi["title"] = ctx.caption
                d = init("/v2/post/publish/video/init/", {"post_info": pi, "source_info": source}, not chunk)
            else:
                d = init("/v2/post/publish/inbox/video/init/", {"source_info": source}, not chunk)
            if chunk:
                ctx.state.update({"publish_id": d["publish_id"], "uploaded": False})
                self._upload(d["upload_url"], m, *chunk)
                ctx.state["uploaded"] = True
        ctx.state.update({"publish_id": d["publish_id"], "mode": mode, "kind": kind})
        ctx.checkpoint()
        return self.poll(ctx)

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        st = ctx.state
        d = self._post("/v2/post/publish/status/fetch/", ctx.channel.access_token, {"publish_id": st["publish_id"]})
        status = d.get("status", "")
        if status == "FAILED":
            raise PermanentError(f"TikTok could not publish: {d.get('fail_reason') or 'unknown reason'}")
        if status == "PUBLISH_COMPLETE":
            ids = d.get("publicaly_available_post_id") or d.get("publicly_available_post_id") or []
            vid = str(ids[0]) if ids else ""
            user = ctx.channel.username
            link = f"https://www.tiktok.com/@{user}/video/{vid}" if vid and user else (f"https://www.tiktok.com/@{user}" if user else "")
            msg = "Published to TikTok" + ("" if vid else " (private - visible only to you until the app is audited)")
            return PublishOutcome("published", external_id=vid, permalink=link, state=st, message=msg)
        if status == "SEND_TO_USER_INBOX":
            return PublishOutcome("inbox", state=st, message="Sent to your TikTok inbox - open TikTok to finish posting.")
        return PublishOutcome("processing", state=st, message=f"TikTok is processing ({status or 'pending'})…")

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        if not external_id:
            return {}
        d = self._post("/v2/video/query/", ctx.channel.access_token, {"filters": {"video_ids": [external_id]}},
                       params={"fields": "id,view_count,like_count,comment_count,share_count"})
        vids = d.get("videos") or []
        if not vids:
            return {}
        v = vids[0]
        out = {"views": v.get("view_count"), "likes": v.get("like_count"), "comments": v.get("comment_count"),
               "shares": v.get("share_count")}
        if out["views"]:
            out["engagement_rate"] = round(100.0 * ((out["likes"] or 0) + (out["comments"] or 0) + (out["shares"] or 0)) / out["views"], 2)
        return out
