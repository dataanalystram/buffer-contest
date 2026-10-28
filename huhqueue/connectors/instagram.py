"""Instagram connector: official "Instagram API with Instagram Login" (graph.instagram.com).

Flow (exactly what Meta documents for Professional accounts):
  1. OAuth: https://www.instagram.com/oauth/authorize -> ?code
  2. POST https://api.instagram.com/oauth/access_token          -> short-lived token (1h) + user_id
  3. GET  https://graph.instagram.com/access_token (ig_exchange_token) -> long-lived token (60 days)
  4. GET  https://graph.instagram.com/refresh_access_token (ig_refresh_token) before it expires
  5. POST /{ig-user-id}/media          -> container (IMAGE / REELS / STORIES / carousel items / CAROUSEL)
  6. GET  /{container-id}?fields=status_code until FINISHED
  7. POST /{ig-user-id}/media_publish  -> media id
  8. GET  /{media-id}?fields=permalink, optional POST /{media-id}/comments (first comment)
  9. GET  /{media-id}/insights          -> views, reach, likes, comments, shares, saved ...

Media must be reachable at a public https URL when Meta fetches it (see the
edge server + tunnel in huhqueue/edge.py). JPEG is the only image format the API
accepts, so HuhQueue keeps a JPEG copy of every uploaded image.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from .base import (http_errors, AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem,
                   NotConfigured, PermanentError, Profile, PublishContext, PublishOutcome,
                   RetryableError, TokenBundle, count_hashtags, count_mentions, text_heavy, utf16_len)

AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH = "https://graph.instagram.com"

CAPTION_LIMIT = 2200
HASHTAG_HARD_LIMIT = 30
MENTION_HARD_LIMIT = 20
CAROUSEL_MAX = 20
IMAGE_MAX_BYTES = 8 * 1024 * 1024
VIDEO_MIMES = {"video/mp4", "video/quicktime"}

# Graph error codes that mean "try again later"
TRANSIENT_CODES = {1, 2, 4, 17, 32, 341, 368, 613, 80002}
MEDIA_FETCH_HINT = ("Instagram could not download the media. Make sure the public media URL / tunnel is "
                    "running (Settings -> Public media URL) and the file is a JPEG image or MP4/MOV video.")


def _utcnow() -> datetime:
    return datetime.utcnow()


class InstagramConnector(Connector):
    id = "instagram"
    name = "Instagram"
    color = "#E1306C"
    caption_limit = CAPTION_LIMIT
    capabilities = {
        "oauth": True,
        "manual_token": True,
        "insights": True,
        "first_comment": True,
        "notify_mode": True,
        "comments": True,
        "comments_note": "Instagram Graph API comments (instagram_business_manage_comments).",
        "mentions": True,
        "mentions_note": "Posts you are tagged in, from the Graph API /{ig-user-id}/tags edge.",
        "collaborators": True,
        "post_types": ["auto", "feed", "reel", "story", "carousel"],
        "demographics": True,
        "demographics_note": "follower_demographics and engaged_audience_demographics; Meta needs 100+ followers.",
        "stories": True,
        "stories_note": "Story metrics can only be read while the story is live (24 h). HuhQueue saves them every 2 h.",
    }

    # ------------------------------------------------------------------ helpers
    COLLAB_MAX = 3
    USERNAME_RE = re.compile(r"^(?!.*\.\.)(?!\.)[A-Za-z0-9._]{1,30}(?<!\.)$")

    @staticmethod
    def parse_collaborators(value) -> list[str]:
        """'@a, b' or ['a', '@b'] -> ['a', 'b'] (lowercased, de-duplicated, order kept)."""
        if isinstance(value, str):
            parts = re.split(r"[,\s]+", value)
        else:
            parts = list(value or [])
        out: list[str] = []
        for x in parts:
            u = str(x or "").strip().lstrip("@").lower()
            if u and u not in out:
                out.append(u)
        return out

    @property
    def version(self) -> str:
        return self.settings.instagram_graph_version or "v26.0"

    def _url(self, path: str) -> str:
        return f"{GRAPH}/{self.version}/{path.lstrip('/')}"

    @http_errors
    def _handle(self, resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}
        if resp.status_code < 400 and not (isinstance(data, dict) and "error" in data):
            return data
        err = data.get("error", {}) if isinstance(data, dict) else {}
        if isinstance(err, str):
            err = {"message": err}
        msg = (err.get("error_user_msg") or err.get("message") or data.get("error_message")
               or resp.text[:300])
        code = err.get("code") or data.get("code")
        sub = err.get("error_subcode")
        label = f"Instagram error {code}{'/' + str(sub) if sub else ''}: {msg}"
        if code == 190 or (err.get("type") == "OAuthException" and code in (102, 463, 467)):
            raise AuthError(label, code=str(code), raw=data)
        if sub == 2207042:
            raise RetryableError(label + " (daily publishing limit reached)", code="publish_cap", retry_after=3600, raw=data)
        if resp.status_code == 429 or code in TRANSIENT_CODES or err.get("is_transient") or resp.status_code >= 500:
            raise RetryableError(label, code=str(code), retry_after=120, raw=data)
        if code == 9004 or sub in (2207003, 2207020, 2207026, 2207052):
            raise PermanentError(f"{label}. {MEDIA_FETCH_HINT}", code=str(code), raw=data)
        if resp.status_code == 401:
            raise AuthError(label, raw=data)
        raise PermanentError(label, code=str(code) if code else None, raw=data)

    def _get(self, path: str, token: str, **params) -> dict:
        try:
            r = self.http.get(self._url(path), params={**params, "access_token": token})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to Instagram: {e}") from e
        return self._handle(r)

    def _post(self, path: str, token: str, **data) -> dict:
        payload = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in data.items() if v is not None}
        try:
            r = self.http.post(self._url(path), data={**payload, "access_token": token})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to Instagram: {e}") from e
        return self._handle(r)

    # ------------------------------------------------------------------ config/auth
    def is_configured(self) -> bool:
        return bool(self.settings.instagram_app_id and self.settings.instagram_app_secret)

    def setup_hint(self) -> str:
        return ("Add your Meta app's Instagram app ID and secret in Settings -> App keys (Meta app -> Instagram -> "
                "API setup with Instagram login), or set INSTAGRAM_APP_ID / INSTAGRAM_APP_SECRET. See SETUP.md.")

    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        q = {
            "client_id": self.settings.instagram_app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": self.settings.instagram_scopes,
            "state": state,
            "enable_fb_login": "0",
            "force_reauth": "true",
        }
        return f"{AUTH_URL}?{urlencode(q)}"

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        code = code.split("#_")[0]  # Instagram appends "#_" to the code
        try:
            r = self.http.post(TOKEN_URL, data={
                "client_id": self.settings.instagram_app_id,
                "client_secret": self.settings.instagram_app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
                "code": code,
            })
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        data = self._handle(r)
        if isinstance(data.get("data"), list) and data["data"]:
            data = data["data"][0]
        short = data["access_token"]
        perms = data.get("permissions") or ""
        if isinstance(perms, list):
            perms = ",".join(perms)
        bundle = self._long_lived(short)
        bundle.scopes = perms or self.settings.instagram_scopes
        return bundle, self._profile(bundle.access_token)

    def _long_lived(self, short_token: str) -> TokenBundle:
        try:
            r = self.http.get(f"{GRAPH}/access_token", params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.settings.instagram_app_secret,
                "access_token": short_token,
            })
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r)
        return TokenBundle(access_token=d["access_token"],
                           expires_at=_utcnow() + timedelta(seconds=int(d.get("expires_in", 5184000))))

    def _profile(self, token: str) -> Profile:
        d = self._get("me", token,
                      fields="user_id,username,name,profile_picture_url,account_type,followers_count,media_count")
        ext = str(d.get("user_id") or d.get("id"))
        return Profile(external_id=ext, username=d.get("username", ""),
                       display_name=d.get("name", "") or d.get("username", ""),
                       avatar_url=d.get("profile_picture_url", ""),
                       meta={"account_type": d.get("account_type"), "followers_count": d.get("followers_count"),
                             "media_count": d.get("media_count"), "app_scoped_id": d.get("id")})

    def connect_with_token(self, access_token: str, extra: dict | None = None) -> tuple[TokenBundle, Profile]:
        """Paste a token from Meta dashboard -> Instagram -> 'Generate token' (long-lived, ~60 days)."""
        profile = self._profile(access_token)
        secs = int((extra or {}).get("expires_in") or 60 * 86400)
        return TokenBundle(access_token=access_token, expires_at=_utcnow() + timedelta(seconds=secs),
                           scopes=self.settings.instagram_scopes), profile

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        if not auth.token_expires_at:
            return False
        # long-lived tokens can be refreshed once they are >= 24h old; refresh within 7 days of expiry
        old_enough = not auth.token_obtained_at or (now - auth.token_obtained_at) >= timedelta(hours=24)
        return old_enough and auth.token_expires_at - now <= timedelta(days=7)

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        try:
            r = self.http.get(f"{GRAPH}/refresh_access_token", params={
                "grant_type": "ig_refresh_token", "access_token": auth.access_token})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r)
        return TokenBundle(access_token=d["access_token"], scopes=auth.scopes,
                           expires_at=_utcnow() + timedelta(seconds=int(d.get("expires_in", 5184000))))

    def account_info(self, auth: ChannelAuth) -> dict:
        d = self._get(f"{auth.external_id}/content_publishing_limit", auth.access_token, fields="quota_usage,config")
        row = (d.get("data") or [{}])[0]
        cfg = row.get("config") or {}
        return {"quota_usage": row.get("quota_usage", 0) or 0, "quota_total": cfg.get("quota_total", 100) or 100,
                "quota_duration": cfg.get("quota_duration", 86400)}

    # ------------------------------------------------------------------ format
    format_key = "ig_type"

    def formats(self) -> list[list[str]]:
        return [["feed", "Single image"], ["carousel", "Carousel"], ["reel", "Reel"], ["story", "Story"]]

    def suggest_format(self, caption, media):
        vids = [m for m in media if m.kind == "video"]
        if len(media) == 1 and vids:
            return "reel", "Video → Reel (Reels get the most reach)"
        if len(media) > 1:
            return "carousel", f"{len(media)} files → Carousel"
        if text_heavy(caption):
            return "carousel", "Text-heavy → split it into carousel slides"
        return "feed", "One image → single post"

    # ------------------------------------------------------------------ composing
    def option_schema(self) -> list[dict]:
        return [
            {"key": "ig_type", "label": "Post type", "type": "select", "default": "auto",
             "choices": [["auto", "Auto (from media)"], ["feed", "Post"], ["reel", "Reel"],
                         ["story", "Story"], ["carousel", "Carousel"]]},
            {"key": "posting_method", "label": "Posting method", "type": "select", "default": "auto",
             "choices": [["auto", "Automatic - we publish it"], ["notify", "Notify me - I finish in the app"]]},
            {"key": "first_comment", "label": "First comment", "type": "textarea", "default": "",
             "help": "Posted right after publishing (needs instagram_business_manage_comments)."},
            {"key": "collaborators", "label": "Collaborators", "type": "text", "default": "",
             "help": "Up to 3 public Instagram usernames, comma separated (feed posts, Reels, carousels - not Stories). Each gets an invite to co-author the post."},
            {"key": "share_to_feed", "label": "Also show reel in feed", "type": "toggle", "default": True,
             "show_if": {"ig_type": ["reel", "auto"]}},
            {"key": "cover_url", "label": "Reel cover image URL", "type": "text", "default": "",
             "show_if": {"ig_type": ["reel", "auto"]}},
            {"key": "thumb_offset", "label": "Reel cover frame (ms)", "type": "number", "default": None,
             "show_if": {"ig_type": ["reel", "auto"]}},
            {"key": "location_id", "label": "Location page ID", "type": "text", "default": ""},
            {"key": "is_ai_generated", "label": "AI-generated content label", "type": "toggle", "default": False},
        ]

    @staticmethod
    def resolve_type(media: list[MediaItem], options: dict) -> str:
        t = (options or {}).get("ig_type") or "auto"
        if t != "auto":
            return t
        if len(media) > 1:
            return "carousel"
        if media and media[0].kind == "video":
            return "reel"
        return "feed"

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        issues: list[Issue] = []
        options = options or {}
        t = self.resolve_type(media, options)
        n = utf16_len(caption)
        if n > CAPTION_LIMIT:
            issues.append(Issue("error", "caption_too_long",
                                f"Instagram captions max {CAPTION_LIMIT} characters ({n} now).", "caption"))
        tags = count_hashtags(caption)
        if tags > HASHTAG_HARD_LIMIT:
            issues.append(Issue("error", "too_many_hashtags",
                                f"Instagram allows max {HASHTAG_HARD_LIMIT} hashtags ({tags} now).", "caption"))
        elif tags > rules.hashtag_limit:
            issues.append(Issue("warning", "brand_hashtags",
                                f"{tags} hashtags - the house style is {rules.hashtag_limit} max.", "caption"))
        if count_mentions(caption) > MENTION_HARD_LIMIT:
            issues.append(Issue("error", "too_many_mentions", f"Instagram allows max {MENTION_HARD_LIMIT} @mentions.", "caption"))
        if not media:
            issues.append(Issue("error", "media_required", "Instagram needs at least one image or video.", "media"))
            return issues
        if t == "carousel":
            if len(media) < 2:
                issues.append(Issue("error", "carousel_min", "A carousel needs 2-20 items.", "media"))
            if len(media) > CAROUSEL_MAX:
                issues.append(Issue("error", "carousel_max", f"Carousels allow max {CAROUSEL_MAX} items.", "media"))
        elif len(media) > 1:
            issues.append(Issue("error", "single_media",
                                f"A {t} takes exactly one media file (use Carousel for more).", "media"))
        if t == "reel" and media[0].kind != "video":
            issues.append(Issue("error", "reel_needs_video", "Reels need a video (MP4/MOV).", "media"))
        if t == "feed" and media[0].kind != "image":
            issues.append(Issue("error", "feed_needs_image", "Video posts go out as Reels - choose Reel or Auto.", "media"))
        if t == "feed" and len(media) == 1 and text_heavy(caption):
            issues.append(Issue("info", "suggest_carousel", "Text-heavy caption - a carousel usually performs better.", "options"))
        if t == "story" and caption.strip():
            issues.append(Issue("warning", "story_caption", "Stories have no caption; the text will be ignored.", "caption"))
        for m in media:
            if m.kind == "image":
                if m.mime != "image/jpeg":
                    issues.append(Issue("info", "jpeg_convert", f"{m.filename} will be sent as a JPEG copy.", "media"))
                if m.size > IMAGE_MAX_BYTES:
                    issues.append(Issue("error", "image_too_big", f"{m.filename} is over 8 MB.", "media"))
                if m.width and m.height and t in ("feed", "carousel"):
                    ratio = m.width / m.height
                    if ratio < 0.79 or ratio > 1.92:
                        issues.append(Issue("warning", "aspect_ratio",
                                            f"{m.filename} is {ratio:.2f}:1; feed images must be between 4:5 and 1.91:1.",
                                            "media"))
            elif m.mime not in VIDEO_MIMES:
                issues.append(Issue("error", "video_format", f"{m.filename}: Instagram needs MP4 or MOV video.", "media"))
        collabs = self.parse_collaborators(options.get("collaborators"))
        if not self.page_linked(getattr(rules, "auth_mode", "")):
            for key, label in (("collaborators", "Collaborators"), ("location_id", "Location")):
                if (collabs if key == "collaborators" else options.get(key)):
                    issues.append(Issue("warning", f"{key}_needs_fb_login",
                                        f"{label} only work for Instagram accounts connected through Facebook Login (a linked "
                                        "Facebook Page). This channel uses Instagram Login, so it won't be sent.", "options"))
        if collabs:
            if t == "story":
                issues.append(Issue("error", "collaborators_story",
                                    "Stories can't have collaborators - use a feed post, Reel or carousel.", "options"))
            if len(collabs) > self.COLLAB_MAX:
                issues.append(Issue("error", "collaborators",
                                    f"Max {self.COLLAB_MAX} collaborators ({len(collabs)} now).", "options"))
            bad = [c for c in collabs if not self.USERNAME_RE.match(c)]
            if bad:
                issues.append(Issue("error", "collaborators_invalid",
                                    f"Not a valid Instagram username: {', '.join('@' + b for b in bad)}.", "options"))
            me = (getattr(rules, "username", "") or "").lower()
            if me and me in collabs:
                issues.append(Issue("error", "collaborators_self", "You can't invite your own account as a collaborator.", "options"))
        if not rules.has_public_url and options.get("posting_method", "auto") != "notify":
            issues.append(Issue("warning", "no_public_url",
                                "No public media URL yet - start the tunnel in Settings before this post goes out.", "media"))
        return issues

    # ------------------------------------------------------------------ publishing
    @staticmethod
    def _image_url(m: MediaItem) -> str:
        url = m.jpeg_public_url or m.public_url
        if not url:
            raise RetryableError("No public media URL - start the tunnel (Settings -> Public media URL).", retry_after=300)
        return url

    @staticmethod
    def _video_url(m: MediaItem) -> str:
        if not m.public_url:
            raise RetryableError("No public media URL - start the tunnel (Settings -> Public media URL).", retry_after=300)
        return m.public_url

    @staticmethod
    def page_linked(auth_mode: str) -> bool:
        """Collaborators and location tagging only work through the Instagram API with Facebook Login (Page-linked).
        Instagram Login channels (graph.instagram.com, the default) must not send them."""
        return auth_mode == "fb_login"

    @staticmethod
    def _common(caption: str, options: dict, auth_mode: str = "") -> dict:
        out: dict = {"caption": caption or None}
        if InstagramConnector.page_linked(auth_mode):
            collabs = InstagramConnector.parse_collaborators(options.get("collaborators"))[:InstagramConnector.COLLAB_MAX]
            if collabs:
                out["collaborators"] = collabs   # JSON array of up to 3 usernames (feed, Reels, carousel parent)
            if options.get("location_id"):
                out["location_id"] = options["location_id"]
        if options.get("is_ai_generated"):
            out["is_ai_generated"] = "true"
        return out

    PUBLISH_CAP = 50  # Instagram API: at most 50 API-published posts per account per rolling 24 hours

    def check_publish_cap(self, info: dict | None, local_count: int | None) -> None:
        """Refuse to publish over the 24h cap. Meta's content_publishing_limit is the source of truth when
        available; HuhQueue's own count of posts sent in the last 24h is the fallback (and a floor)."""
        cap = self.PUBLISH_CAP
        used = local_count or 0
        if info:
            cap = min(cap, int(info.get("quota_total") or cap))
            used = max(used, int(info.get("quota_usage") or 0))
        if used >= cap:
            src = "Meta's content_publishing_limit" if info else "posts sent in the last 24h"
            raise RetryableError(f"Instagram publish cap reached: {used}/{cap} posts in the last 24 hours ({src}). "
                                 "It will retry when the rolling window frees up.", code="publish_cap", retry_after=3600)

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        a, opts, media = ctx.channel, ctx.options or {}, ctx.media
        uid, tok = a.external_id, a.access_token
        if not tok:
            raise AuthError("Instagram channel has no access token - reconnect it.")
        if ctx.state.get("parent") or ctx.state.get("children"):
            return self.poll(ctx)  # resume a half-finished attempt instead of creating new containers
        t = self.resolve_type(media, opts)
        # Respect Meta's moving 24h quota before creating anything
        try:
            info = self.account_info(a)
        except PermanentError:
            info = None  # quota endpoint unavailable - media_publish enforces it anyway
        self.check_publish_cap(info, ctx.recent_publishes)

        ctx.state.update({"type": t, "stage": "containers"})
        if t == "carousel":
            children = []
            for m in media:
                if m.kind == "image":
                    body = {"image_url": self._image_url(m), "is_carousel_item": "true"}
                    if m.alt_text:
                        body["alt_text"] = m.alt_text
                else:
                    body = {"video_url": self._video_url(m), "media_type": "VIDEO", "is_carousel_item": "true"}
                children.append(self._post(f"{uid}/media", tok, **body)["id"])
                ctx.state["children"] = list(children)
                ctx.checkpoint()
            ctx.state["parent"] = None
        else:
            m = media[0]
            if t == "story":
                body = {"media_type": "STORIES"}
                if m.kind == "image":
                    body["image_url"] = self._image_url(m)
                else:
                    body["video_url"] = self._video_url(m)
            elif t == "reel":
                body = {"media_type": "REELS", "video_url": self._video_url(m), **self._common(ctx.caption, opts, (a.meta or {}).get('auth_mode', '')),
                        "share_to_feed": "true" if opts.get("share_to_feed", True) else "false"}
                if opts.get("cover_url"):
                    body["cover_url"] = opts["cover_url"]
                if opts.get("thumb_offset") not in (None, ""):
                    body["thumb_offset"] = int(opts["thumb_offset"])
            else:
                body = {"image_url": self._image_url(m), **self._common(ctx.caption, opts, (a.meta or {}).get('auth_mode', ''))}
                if m.alt_text:
                    body["alt_text"] = m.alt_text
            ctx.state["parent"] = self._post(f"{uid}/media", tok, **body)["id"]
            ctx.checkpoint()   # a resumed attempt polls this container (PUBLISHED = already live) instead of re-posting
        return self.poll(ctx)

    def _status(self, container_id: str, tok: str) -> str:
        d = self._get(container_id, tok, fields="status_code,status")
        code = d.get("status_code", "")
        if code == "ERROR":
            raise PermanentError(f"Instagram could not process the media: {d.get('status') or 'ERROR'}. {MEDIA_FETCH_HINT}")
        if code == "EXPIRED":
            raise PermanentError("Instagram container expired (not published within 24h).")
        return code

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        a, st, opts = ctx.channel, ctx.state, ctx.options or {}
        uid, tok = a.external_id, a.access_token
        if st.get("type") == "carousel" and not st.get("parent"):
            pending = [c for c in st["children"] if self._status(c, tok) not in ("FINISHED", "PUBLISHED")]
            if pending:
                return PublishOutcome("processing", state=st,
                                      message=f"Instagram is processing {len(pending)} carousel item(s)…")
            st["parent"] = self._post(f"{uid}/media", tok, media_type="CAROUSEL", children=",".join(st["children"]),
                                      **self._common(ctx.caption, opts, (a.meta or {}).get('auth_mode', '')))["id"]
            ctx.checkpoint()
        if not st.get("media_id"):
            status = self._status(st["parent"], tok)
            if status == "PUBLISHED":
                # media_publish went through but we never saw the reply (e.g. network drop):
                # never publish twice - pick up the newest media instead.
                recent = self._get(f"{uid}/media", tok, fields="id,permalink,timestamp", limit=1).get("data") or []
                if recent:
                    st["media_id"] = recent[0]["id"]
            elif status != "FINISHED":
                return PublishOutcome("processing", state=st, message="Instagram is processing the media…",
                                      poll_after_seconds=30)
        if not st.get("media_id"):
            st["publish_called"] = True
            st["media_id"] = self._post(f"{uid}/media_publish", tok, creation_id=st["parent"])["id"]
        media_id = st["media_id"]
        warnings: list[str] = []
        permalink = ""
        try:
            permalink = self._get(media_id, tok, fields="id,permalink,media_product_type,timestamp").get("permalink", "")
        except (PermanentError, RetryableError) as e:
            warnings.append(f"Could not read permalink: {e.message}")
        fc = (opts.get("first_comment") or "").strip()
        if fc and st.get("type") != "story" and not st.get("first_comment_id"):
            try:
                st["first_comment_id"] = self._post(f"{media_id}/comments", tok, message=fc).get("id")
            except (PermanentError, RetryableError, AuthError) as e:
                warnings.append(f"Posted, but the first comment failed: {e.message}")
        return PublishOutcome("published", external_id=media_id, permalink=permalink, state=st, warnings=warnings,
                              message="Published to Instagram")

    # Media insights metrics Meta still serves in 2026 (v26 docs, /{media-id}/insights). `impressions` is deprecated for
    # media created after 2 Jul 2024 and `plays` was replaced by `views`, so neither is requested. Reels get watch time.
    # Metrics are listed per media product type because asking for one a type does not support fails the whole call.
    BASE_METRICS = ("views", "reach", "likes", "comments", "shares", "saved", "total_interactions")
    TYPE_METRICS = {
        "reel": ("ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate", "reposts"),
        "feed": ("profile_visits", "follows", "reposts"),
        "carousel": ("profile_visits", "follows", "reposts"),
    }
    STORY_METRICS = ("views", "reach", "replies", "shares", "total_interactions", "follows", "profile_visits",
                     "navigation", "link_clicks")

    def _read_insights(self, external_id: str, tok: str, metrics) -> dict:
        out: dict = {}
        d = self._get(f"{external_id}/insights", tok, metric=",".join(metrics))
        for row in d.get("data", []):
            val = None
            if row.get("values"):
                val = row["values"][0].get("value")
            elif row.get("total_value"):
                val = row["total_value"].get("value")
            out[row.get("name")] = val
        return out

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        tok = ctx.channel.access_token
        t = (ctx.state or {}).get("type", "feed")
        if t == "story":
            attempts = [self.STORY_METRICS, ("views", "reach", "replies", "shares", "total_interactions")]
        else:
            attempts = [self.BASE_METRICS + self.TYPE_METRICS.get(t, ()), self.BASE_METRICS]
        out: dict | None = None
        for metrics in attempts:
            try:
                out = self._read_insights(external_id, tok, metrics)
                break
            except PermanentError:
                continue
        if out is None:
            d = self._get(external_id, tok, fields="like_count,comments_count")
            out = {"likes": d.get("like_count"), "comments": d.get("comments_count")}
        # friendlier names next to Meta's: seconds instead of milliseconds
        if out.get("ig_reels_avg_watch_time") is not None:
            out["avg_watch_time"] = round(float(out["ig_reels_avg_watch_time"]) / 1000.0, 2)
        if out.get("ig_reels_video_view_total_time") is not None:
            out["watch_time"] = round(float(out["ig_reels_video_view_total_time"]) / 1000.0, 1)
        likes, comments = out.get("likes") or 0, out.get("comments") or 0
        reach = out.get("reach") or 0
        if reach:
            inter = out.get("total_interactions") or (likes + comments + (out.get("shares") or 0) + (out.get("saved") or 0))
            out["engagement_rate"] = round(100.0 * inter / reach, 2)
        return out

    # ------------------------------------------------------------------ profile + community
    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return self._profile(auth.access_token)

    MEDIA_FIELDS = ("id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,"
                    "like_count,comments_count")

    def list_media(self, auth: ChannelAuth, limit: int = 24, after: str = "") -> dict:
        """GET /{ig-user-id}/media: every post on the account (not only HuhQueue's), newest first."""
        params = {"fields": self.MEDIA_FIELDS, "limit": max(1, min(int(limit or 24), 100))}
        if after:
            params["after"] = after
        d = self._get(f"{auth.external_id}/media", auth.access_token, **params)
        items = []
        for m in d.get("data") or []:
            mt = m.get("media_type", "")
            items.append({
                "external_id": m.get("id", ""), "caption": m.get("caption", "") or "", "media_type": mt,
                "product_type": m.get("media_product_type", ""),
                "media_url": m.get("media_url", "") or "",
                # videos/reels: thumbnail_url is the still; images: media_url is the image itself
                "thumbnail_url": m.get("thumbnail_url") or (m.get("media_url") if mt != "VIDEO" else "") or "",
                "permalink": m.get("permalink", ""), "timestamp": m.get("timestamp", ""),
                "like_count": m.get("like_count"), "comments_count": m.get("comments_count"),
            })
        paging = d.get("paging") or {}
        nxt = (paging.get("cursors") or {}).get("after", "") if paging.get("next") else ""
        return {"items": items, "next": nxt}

    ACCOUNT_METRICS = ["reach", "views", "accounts_engaged", "total_interactions", "likes", "comments", "shares",
                       "saves", "replies", "profile_links_taps"]

    def account_insights(self, auth: ChannelAuth, days: int = 28) -> dict:
        """GET /{ig-user-id}/insights (instagram_business_manage_insights). Meta allows at most 30 days per call."""
        days = max(1, min(int(days or 28), 30))
        until = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        since = until - timedelta(days=days)
        rng = {"since": int((since - datetime(1970, 1, 1)).total_seconds()),
               "until": int((until - datetime(1970, 1, 1)).total_seconds())}
        totals: dict = {}

        def read(rows):
            for row in rows or []:
                tv = row.get("total_value") or {}
                if "value" in tv:
                    totals[row.get("name")] = tv.get("value")

        uid, tok = auth.external_id, auth.access_token
        try:
            read(self._get(f"{uid}/insights", tok, metric=",".join(self.ACCOUNT_METRICS), period="day",
                           metric_type="total_value", **rng).get("data"))
        except PermanentError:
            # one unsupported metric fails the whole call: fall back to one metric at a time
            for m in self.ACCOUNT_METRICS:
                try:
                    read(self._get(f"{uid}/insights", tok, metric=m, period="day", metric_type="total_value",
                                   **rng).get("data"))
                except PermanentError:
                    continue
        series: dict = {}
        try:
            fc_since = max(since, until - timedelta(days=30))
            d = self._get(f"{uid}/insights", tok, metric="follower_count", period="day",
                          since=int((fc_since - datetime(1970, 1, 1)).total_seconds()), until=rng["until"])
            for row in d.get("data") or []:
                series[row.get("name")] = [[(v.get("end_time") or "")[:10], v.get("value")] for v in row.get("values") or []]
        except PermanentError:
            pass  # follower_count needs 100+ followers
        return {"since": since.date().isoformat(), "until": until.date().isoformat(), "days": days,
                "totals": totals, "series": series}

    DEMO_BREAKDOWNS = ("age", "gender", "country", "city")
    ENGAGED_TIMEFRAMES = ("this_week", "this_month", "last_14_days", "last_30_days", "last_90_days", "prev_month")

    def audience_demographics(self, auth: ChannelAuth, timeframe: str = "this_month") -> dict | None:
        """GET /{ig-user-id}/insights?metric=follower_demographics|engaged_audience_demographics&period=lifetime
        &metric_type=total_value&breakdown=<age|gender|country|city> (one breakdown per call). Accounts under 100
        followers get an error from Meta; that is returned as a note, never raised."""
        uid, tok = auth.external_id, auth.access_token
        tf = timeframe if timeframe in self.ENGAGED_TIMEFRAMES else "this_month"
        out: dict = {"followers": {}, "engaged": {}, "notes": [], "timeframe": tf}
        for key, metric, extra in (("followers", "follower_demographics", {}),
                                   ("engaged", "engaged_audience_demographics", {"timeframe": tf})):
            for b in self.DEMO_BREAKDOWNS:
                try:
                    d = self._get(f"{uid}/insights", tok, metric=metric, period="lifetime", metric_type="total_value",
                                  breakdown=b, **extra)
                except PermanentError as e:
                    note = f"{metric} by {b}: {e.message}"
                    if not any(n.split(":")[0] == note.split(":")[0] for n in out["notes"]):
                        out["notes"].append(note)
                    continue
                vals: dict = {}
                for row in d.get("data") or []:
                    for br in (row.get("total_value") or {}).get("breakdowns") or []:
                        for r in br.get("results") or []:
                            dim = ", ".join(str(x) for x in r.get("dimension_values") or []) or "unknown"
                            vals[dim] = vals.get(dim, 0) + int(r.get("value") or 0)
                if vals:
                    out[key][b] = vals
        return out

    def list_stories(self, auth: ChannelAuth) -> list[dict]:
        """GET /{ig-user-id}/stories: the account's live stories (only the last 24 h)."""
        d = self._get(f"{auth.external_id}/stories", auth.access_token,
                      fields="id,media_type,media_url,thumbnail_url,permalink,caption,timestamp")
        return [{"external_id": m.get("id"), "media_type": m.get("media_type", ""), "media_url": m.get("media_url", ""),
                 "thumbnail_url": m.get("thumbnail_url", ""), "permalink": m.get("permalink", ""),
                 "caption": m.get("caption", ""), "timestamp": m.get("timestamp", "")} for m in d.get("data") or [] if m.get("id")]

    def story_insights(self, auth: ChannelAuth, external_id: str) -> dict:
        """GET /{story-id}/insights. `navigation` (taps forward/back, exits, swipes) needs a breakdown, so it is read
        separately; metrics Meta refuses are skipped."""
        tok = auth.access_token
        out: dict = {}
        for metrics in ([m for m in self.STORY_METRICS if m != "navigation"], ("views", "reach", "replies", "shares", "total_interactions")):
            try:
                out = self._read_insights(external_id, tok, metrics)
                break
            except PermanentError:
                continue
        try:
            d = self._get(f"{external_id}/insights", tok, metric="navigation", breakdown="story_navigation_action_type")
            for row in d.get("data") or []:
                for br in (row.get("total_value") or {}).get("breakdowns") or []:
                    for r in br.get("results") or []:
                        k = "_".join(r.get("dimension_values") or []).lower()
                        if k:
                            out[k] = int(r.get("value") or 0)
        except PermanentError:
            pass
        return out

    def list_comments(self, auth: ChannelAuth, limit_media: int = 10) -> list[dict]:
        tok = auth.access_token
        media = self._get(f"{auth.external_id}/media", tok,
                          fields="id,caption,permalink,timestamp,comments_count", limit=limit_media).get("data") or []
        out: list[dict] = []
        expected = 0
        for m in media:
            if not m.get("comments_count"):
                continue
            expected += int(m.get("comments_count") or 0)
            rows = self._get(f"{m['id']}/comments", tok, limit=50,
                             fields="id,text,username,timestamp,like_count,replies{id,text,username,timestamp,like_count}"
                             ).get("data") or []
            for c in rows:
                out.append(self._comment(c, m, None, auth.username))
                for r in (c.get("replies") or {}).get("data", []):
                    out.append(self._comment(r, m, c["id"], auth.username))
        # Meta counts comments it will not hand over: while the Meta app is in Development mode (or the comments
        # permission lacks Advanced Access) /comments returns [] for anyone without a role on the app.
        self.last_comments_note = ""
        if expected and not out:
            self.last_comments_note = (
                f"Instagram reports {expected} comment(s) on your recent posts but returned none. This happens while "
                "your Meta app is in Development mode, or instagram_business_manage_comments has only Standard Access: "
                "Meta then hides comments from people who have no role on the app. Switch the app to Live (App Review "
                "for instagram_business_manage_comments) to see them here.")
        return out

    @staticmethod
    def _comment(c: dict, m: dict, parent: str | None, me: str) -> dict:
        ts = c.get("timestamp")
        at = None
        if ts:
            try:
                at = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                at = None
        return {"external_id": c["id"], "parent_external_id": parent, "media_external_id": m["id"],
                "media_permalink": m.get("permalink", ""), "media_caption": (m.get("caption") or "")[:200],
                "author": c.get("username", ""), "text": c.get("text", ""), "at": at,
                "like_count": c.get("like_count", 0) or 0, "is_own": bool(me) and c.get("username") == me}

    def list_mentions(self, auth: ChannelAuth, limit: int = 25) -> list[dict]:
        """GET /{ig-user-id}/tags: media other accounts tagged you in. Returned in the comment shape with kind='mention'
        (the media's caption is the text, its owner the author), so they share the Community inbox."""
        d = self._get(f"{auth.external_id}/tags", auth.access_token, limit=max(1, min(int(limit or 25), 50)),
                      fields="id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,username,like_count,comments_count")
        out = []
        for m in d.get("data") or []:
            row = self._comment({"id": f"tag-{m['id']}", "text": m.get("caption") or "", "username": m.get("username", ""),
                                 "timestamp": m.get("timestamp"), "like_count": m.get("like_count")}, m, None, "")
            row["kind"] = "mention"
            row["media_caption"] = (m.get("caption") or "")[:200]
            out.append(row)
        return out

    def reply_comment(self, auth: ChannelAuth, comment_external_id: str, text: str, media_external_id: str = "") -> str:
        if comment_external_id.startswith("tag-"):   # a mention: reply with a comment on the media that tagged us
            return self._post(f"{comment_external_id[4:]}/comments", auth.access_token, message=text).get("id", "")
        return self._post(f"{comment_external_id}/replies", auth.access_token, message=text).get("id", "")

    def post_comment(self, auth: ChannelAuth, media_external_id: str, text: str) -> str:
        return self._post(f"{media_external_id}/comments", auth.access_token, message=text).get("id", "")
