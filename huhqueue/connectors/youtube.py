"""YouTube connector: Google OAuth 2.0 (with PKCE) + YouTube Data API v3.

  GET  https://accounts.google.com/o/oauth2/v2/auth             consent ("Continue as <channel>")
  POST https://oauth2.googleapis.com/token                       code -> access (1h) + refresh token
  POST https://oauth2.googleapis.com/revoke                      disconnect
  GET  /youtube/v3/channels?part=snippet,statistics&mine=true   profile
  POST /upload/youtube/v3/videos?uploadType=resumable           resumable upload session -> PUT bytes
  GET  /youtube/v3/videos?part=status,processingDetails,statistics
  GET  /youtube/v3/commentThreads?allThreadsRelatedToChannelId=  community inbox
  POST /youtube/v3/comments (reply) / commentThreads (top-level comment)

Quota: 10,000 units/day by default; one upload costs ~1,600 units (about 6 uploads a day).
Uploads from an UNVERIFIED Google API project are locked to private until the project passes
Google's audit - same story as TikTok. See SETUP.md.
"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from .base import (http_errors, ConnectorError, AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem, NotConfigured,
                   PermanentError, Profile, PublishContext, PublishOutcome, RetryableError, TokenBundle,
                   count_hashtags)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
TITLE_LIMIT = 100
DESC_LIMIT = 5000          # snippet.description: max 5000 bytes, no < or >  (developers.google.com/youtube/v3/docs/videos)
TAGS_LIMIT = 500           # snippet.tags[]: 500 characters incl. commas; a tag with a space counts its quotes
THUMB_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/thumbnails/set"   # image/jpeg|png, max 50 MB
CAPTIONS_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/captions"     # part=snippet, multipart
# The assignable YouTube categories (videoCategories.list, regionCode=US)
CATEGORIES = [["1", "Film & Animation"], ["2", "Autos & Vehicles"], ["10", "Music"], ["15", "Pets & Animals"], ["17", "Sports"],
              ["19", "Travel & Events"], ["20", "Gaming"], ["22", "People & Blogs"], ["23", "Comedy"], ["24", "Entertainment"],
              ["25", "News & Politics"], ["26", "Howto & Style"], ["27", "Education"], ["28", "Science & Technology"],
              ["29", "Nonprofits & Activism"]]


def tags_length(tags: list[str]) -> int:
    return sum(len(t) + (2 if " " in t else 0) for t in tags) + max(0, len(tags) - 1)


def split_tags(v) -> list[str]:
    if isinstance(v, list):
        return [str(t).strip() for t in v if str(t).strip()]
    return [t.strip() for t in str(v or "").split(",") if t.strip()]
UPLOAD_CHUNK = 8 * 1024 * 1024  # must be a multiple of 256 KiB


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class YouTubeConnector(Connector):
    id = "youtube"
    name = "YouTube"
    color = "#FF0033"
    caption_limit = DESC_LIMIT
    uses_pkce = True
    capabilities = {
        "oauth": True, "manual_token": False, "insights": True, "first_comment": True, "notify_mode": True,
        "comments": True, "comments_note": "YouTube Data API commentThreads / comments (youtube.force-ssl).",
        "post_types": ["short", "video"],
    }

    @property
    def verified(self) -> bool:
        return bool(getattr(self.settings, "youtube_verified", False))

    @http_errors
    def _handle(self, r: httpx.Response) -> dict:
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {}
        if r.status_code < 400:
            return data
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, str):  # oauth endpoint
            msg, reason = data.get("error_description", err), err
        else:
            err = err or {}
            msg = err.get("message", r.text[:200])
            reason = ((err.get("errors") or [{}])[0]).get("reason", "")
        label = f"YouTube error {r.status_code} {reason}: {msg}".strip()
        if r.status_code == 401 or reason in ("invalid_grant", "authError", "unauthorized_client"):
            raise AuthError(label, code=reason, raw=data)
        if reason in ("quotaExceeded", "dailyLimitExceeded", "uploadLimitExceeded"):
            raise RetryableError(label + " (daily YouTube quota - retrying in a few hours)", code=reason,
                                 retry_after=6 * 3600, raw=data)
        if r.status_code == 429 or r.status_code >= 500 or reason in ("rateLimitExceeded", "backendError"):
            raise RetryableError(label, code=reason, retry_after=120, raw=data)
        raise PermanentError(label, code=reason, raw=data)

    def _req(self, method: str, url: str, token: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {token}", **kw.pop("headers", {})}
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to YouTube: {e}") from e
        return self._handle(r)

    # ---- auth
    def is_configured(self) -> bool:
        return bool(self.settings.youtube_client_id and self.settings.youtube_client_secret)

    def setup_hint(self) -> str:
        return ("Add your Google OAuth client ID and secret in Settings -> App keys (Google Cloud, YouTube Data API v3), "
                "or set YOUTUBE_CLIENT_ID / YOUTUBE_CLIENT_SECRET. See SETUP.md.")

    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        q = {"client_id": self.settings.youtube_client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": self.settings.youtube_scopes, "access_type": "offline", "prompt": "consent",
             "include_granted_scopes": "true", "state": state}
        if code_verifier:
            q.update({"code_challenge": pkce_challenge(code_verifier), "code_challenge_method": "S256"})
        return f"{AUTH_URL}?{urlencode(q)}"

    def _token(self, form: dict) -> TokenBundle:
        try:
            r = self.http.post(TOKEN_URL, data=form)
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r)
        return TokenBundle(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
                           expires_at=datetime.utcnow() + timedelta(seconds=int(d.get("expires_in", 3600))),
                           scopes=d.get("scope", ""))

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        form = {"code": code, "client_id": self.settings.youtube_client_id,
                "client_secret": self.settings.youtube_client_secret, "redirect_uri": redirect_uri,
                "grant_type": "authorization_code"}
        if code_verifier:
            form["code_verifier"] = code_verifier
        bundle = self._token(form)
        return bundle, self._profile(bundle.access_token)

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return bool(auth.refresh_token and (not auth.token_expires_at or auth.token_expires_at - now <= timedelta(minutes=10)))

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        if not auth.refresh_token:
            return None
        b = self._token({"client_id": self.settings.youtube_client_id, "client_secret": self.settings.youtube_client_secret,
                         "refresh_token": auth.refresh_token, "grant_type": "refresh_token"})
        b.refresh_token = b.refresh_token or auth.refresh_token
        return b

    def revoke(self, auth: ChannelAuth) -> None:
        tok = auth.refresh_token or auth.access_token
        if tok:
            try:
                self.http.post(REVOKE_URL, data={"token": tok})
            except httpx.HTTPError:
                pass

    def _profile(self, token: str) -> Profile:
        d = self._req("GET", f"{API}/channels", token, params={"part": "snippet,statistics,contentDetails", "mine": "true"})
        items = d.get("items") or []
        if not items:
            raise PermanentError("This Google account has no YouTube channel yet - create one on youtube.com first.")
        c = items[0]
        sn, stt = c.get("snippet", {}), c.get("statistics", {})
        handle = (sn.get("customUrl") or "").lstrip("@") or sn.get("title", "")
        thumbs = sn.get("thumbnails", {})
        avatar = (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
        return Profile(external_id=c["id"], username=handle, display_name=sn.get("title", ""), avatar_url=avatar,
                       meta={"followers_count": int(stt["subscriberCount"]) if stt.get("subscriberCount") else None,
                             "media_count": int(stt.get("videoCount", 0)), "views_count": int(stt.get("viewCount", 0)),
                             "account_type": "YouTube channel",
                             "uploads_playlist": c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")})

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return self._profile(auth.access_token)

    # ---- format
    format_key = "yt_format"

    def formats(self) -> list[list[str]]:
        return [["short", "Short"], ["video", "Video"]]

    @staticmethod
    def is_short(o: dict) -> bool:
        return (o.get("yt_format") or ("short" if o.get("yt_shorts", True) else "video")) == "short"

    def suggest_format(self, caption, media):
        v = next((m for m in media if m.kind == "video"), None)
        if not v:
            return "short", "YouTube needs a video"
        vertical = bool(v.width and v.height and v.height >= v.width)
        if v.duration and v.duration > 180:
            return "video", f"{int(v.duration)} s is longer than 3 min → regular video"
        if v.width and not vertical:
            return "video", "Landscape video → regular video"
        return "short", "Vertical video up to 3 min → Short"

    # ---- composing
    def option_schema(self) -> list[dict]:
        """Every field maps to a documented YouTube Data API v3 field (videos.insert snippet/status + notifySubscribers,
        thumbnails.set, playlistItems.insert, captions.insert, commentThreads.insert)."""
        return [
            {"key": "yt_format", "label": "Post type", "type": "select", "default": "short", "choices": [["short", "Short"], ["video", "Video"]],
             "help": "Short: vertical, up to 3 minutes. Video: regular (long-form) upload."},
            {"key": "yt_title", "label": "Title", "type": "text", "default": "", "required": True, "max": TITLE_LIMIT,
             "help": "Required. Max 100 characters, no < or >."},
            {"key": "yt_category", "label": "Category", "type": "select", "default": "28", "choices": CATEGORIES},
            {"key": "yt_privacy", "label": "Visibility", "type": "select", "default": "public",
             "choices": [["public", "Public"], ["unlisted", "Unlisted"], ["private", "Private"]],
             "help": "Unverified Google projects can only upload private videos."},
            {"key": "yt_license", "label": "License", "type": "select", "default": "youtube",
             "choices": [["youtube", "Standard YouTube License"], ["creativeCommon", "Creative Commons - Attribution"]]},
            {"key": "yt_notify_subscribers", "label": "Notify subscribers", "type": "toggle", "default": True},
            {"key": "yt_embeddable", "label": "Allow embedding", "type": "toggle", "default": True},
            {"key": "yt_made_for_kids", "label": "Made for kids", "type": "toggle", "default": False},
            {"key": "yt_synthetic_media", "label": "Altered or synthetic content (AI disclosure)", "type": "toggle", "default": False,
             "help": "Sets status.containsSyntheticMedia: realistic content made or changed with AI."},
            {"key": "yt_tags", "label": "Tags / keywords", "type": "text", "default": "", "help": "Comma separated, 500 characters max in total."},
            {"key": "yt_thumbnail_media_id", "label": "Custom thumbnail", "type": "media", "accept": "image", "default": "",
             "help": "JPEG or PNG (thumbnails.set). Custom thumbnails need a verified YouTube account."},
            {"key": "yt_playlist_id", "label": "Add to playlist", "type": "playlist", "default": ""},
            {"key": "yt_publish_at", "label": "Premiere / publish at (YouTube schedules it)", "type": "datetime", "default": "",
             "help": "Uploads now as Private and YouTube makes it Public at this time (status.publishAt)."},
            {"key": "yt_captions_text", "label": "Captions (SRT or WebVTT text)", "type": "textarea", "default": ""},
            {"key": "yt_captions_language", "label": "Captions language", "type": "text", "default": "en"},
            {"key": "first_comment", "label": "First comment", "type": "textarea", "default": "",
             "help": "Posted right after the upload (commentThreads.insert). Pin it from YouTube Studio - the API cannot pin."},
            {"key": "posting_method", "label": "Posting method", "type": "select", "default": "auto",
             "choices": [["auto", "Automatic"], ["notify", "Notify me"]]},
        ]

    def _title(self, caption: str, o: dict) -> str:
        t = (o.get("yt_title") or "").strip() or (caption.strip().splitlines() or ["HuhByte"])[0]
        return t[:TITLE_LIMIT]

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        o = options or {}
        out: list[Issue] = []
        vids = [m for m in media if m.kind == "video"]
        if len(vids) != 1 or len(media) != 1:
            out.append(Issue("error", "one_video", "YouTube needs exactly one video.", "media"))
        if len(caption) > DESC_LIMIT:
            out.append(Issue("error", "caption_too_long", f"YouTube descriptions max {DESC_LIMIT} characters.", "caption"))
        if "<" in caption or ">" in caption:
            out.append(Issue("error", "angle_brackets", "YouTube rejects < and > in descriptions.", "caption"))
        title = (o.get("yt_title") or "").strip()
        if not title:
            out.append(Issue("error", "title_required", "Add a YouTube title.", "options"))
        if len(title) > TITLE_LIMIT:
            out.append(Issue("error", "title_too_long", "YouTube titles max 100 characters.", "options"))
        if "<" in title or ">" in title:
            out.append(Issue("error", "angle_brackets_title", "YouTube rejects < and > in titles.", "options"))
        if len(caption.encode("utf-8")) > DESC_LIMIT >= len(caption):
            out.append(Issue("error", "caption_too_long", f"YouTube descriptions max {DESC_LIMIT} bytes (emoji count as several).", "caption"))
        tags = split_tags(o.get("yt_tags"))
        if tags_length(tags) > TAGS_LIMIT:
            out.append(Issue("error", "tags_too_long", f"YouTube tags max {TAGS_LIMIT} characters in total ({tags_length(tags)}).", "options"))
        if o.get("yt_license") not in (None, "", "youtube", "creativeCommon"):
            out.append(Issue("error", "bad_license", "License must be Standard or Creative Commons.", "options"))
        if o.get("yt_category") and str(o["yt_category"]) not in {c[0] for c in CATEGORIES}:
            out.append(Issue("error", "bad_category", "Pick one of the YouTube categories.", "options"))
        if o.get("yt_publish_at") and o.get("yt_privacy") not in (None, "", "private"):
            out.append(Issue("warning", "publish_at_private", "YouTube schedules only Private uploads: it is uploaded as Private and goes Public at the chosen time.", "options"))
        v = vids[0] if vids else None
        if v and self.is_short(o) and v.duration and v.duration > 180:
            out.append(Issue("error", "short_too_long", f"Shorts are max 3 minutes ({int(v.duration)} s). Pick 'Video' - HuhQueue never trims your video.", "options"))
        if not self.verified and o.get("yt_privacy", "public") != "private":
            out.append(Issue("warning", "unverified", "Google API project not verified yet: YouTube will lock this upload to Private.", "options"))
        if count_hashtags(caption) > rules.hashtag_limit:
            out.append(Issue("warning", "brand_hashtags", f"{count_hashtags(caption)} hashtags - the house style is {rules.hashtag_limit} max.", "caption"))
        return out

    # ---- publishing
    def publish(self, ctx: PublishContext) -> PublishOutcome:
        tok = ctx.channel.access_token
        if not tok:
            raise AuthError("YouTube channel has no access token - reconnect it.")
        st = ctx.state
        if st.get("video_id"):
            return self.poll(ctx)
        o = ctx.options or {}
        m = ctx.media[0]
        desc = ctx.caption
        title = self._title(ctx.caption, o)
        if self.is_short(o) and "#shorts" not in (title + desc).lower():
            desc = (desc + "\n\n#Shorts").strip()
        meta = self.video_resource(title, desc, o)
        size = m.size or m.path.stat().st_size
        if not st.get("upload_url"):
            try:
                r = self.http.post(UPLOAD, params={"uploadType": "resumable", "part": "snippet,status",
                                                   "notifySubscribers": "true" if o.get("yt_notify_subscribers", True) else "false"}, json=meta,
                                   headers={"Authorization": f"Bearer {tok}", "X-Upload-Content-Type": m.mime,
                                            "X-Upload-Content-Length": str(size)})
            except httpx.HTTPError as e:
                raise RetryableError(f"Network error talking to YouTube: {e}") from e
            self._handle(r)
            st["upload_url"] = r.headers.get("location") or r.headers.get("Location")
            if not st["upload_url"]:
                raise RetryableError("YouTube did not return an upload session")
            ctx.checkpoint()
        video = self._upload(st["upload_url"], m, size, tok)
        st["video_id"] = video["id"]
        st.pop("upload_url", None)
        ctx.checkpoint()
        return self.poll(ctx)

    @staticmethod
    def video_resource(title: str, desc: str, o: dict) -> dict:
        status = {"privacyStatus": o.get("yt_privacy") or "public", "selfDeclaredMadeForKids": bool(o.get("yt_made_for_kids")),
                  "embeddable": bool(o.get("yt_embeddable", True)), "license": o.get("yt_license") or "youtube",
                  "containsSyntheticMedia": bool(o.get("yt_synthetic_media"))}
        if o.get("yt_publish_at"):   # status.publishAt can only be set on a private, never-published video
            status["privacyStatus"] = "private"
            status["publishAt"] = str(o["yt_publish_at"])
        return {"snippet": {"title": title, "description": desc, "categoryId": str(o.get("yt_category") or "28"),
                            "tags": split_tags(o.get("yt_tags"))}, "status": status}

    def after_upload(self, ctx: PublishContext, vid: str) -> list[str]:
        """thumbnails.set, playlistItems.insert and captions.insert - each once (state remembers), failures become warnings."""
        o, st, tok, warnings = ctx.options or {}, ctx.state, ctx.channel.access_token, []
        thumb = ctx.attachments.get("yt_thumbnail")
        if thumb and not st.get("thumbnail_set"):
            try:
                with open(thumb.path, "rb") as fh:
                    r = self.http.post(THUMB_UPLOAD, params={"videoId": vid}, content=fh.read(),
                                       headers={"Authorization": f"Bearer {tok}", "Content-Type": thumb.mime})
                self._handle(r)
                st["thumbnail_set"] = True
            except (ConnectorError, httpx.HTTPError, OSError) as e:
                warnings.append(f"Uploaded, but the custom thumbnail failed: {getattr(e, 'message', e)}")
        if o.get("yt_playlist_id") and not st.get("playlist_item_id"):
            try:
                d = self._req("POST", f"{API}/playlistItems", tok, params={"part": "snippet"},
                              json={"snippet": {"playlistId": o["yt_playlist_id"], "resourceId": {"kind": "youtube#video", "videoId": vid}}})
                st["playlist_item_id"] = d.get("id", "added")
            except ConnectorError as e:
                warnings.append(f"Uploaded, but adding it to the playlist failed: {e.message}")
        if (o.get("yt_captions_text") or "").strip() and not st.get("caption_id"):
            try:
                meta = {"snippet": {"videoId": vid, "language": o.get("yt_captions_language") or "en", "name": ""}}
                files = {"metadata": (None, __import__("json").dumps(meta), "application/json; charset=UTF-8"),
                         "media": ("captions.srt", o["yt_captions_text"].encode("utf-8"), "application/octet-stream")}
                r = self.http.post(CAPTIONS_UPLOAD, params={"part": "snippet", "uploadType": "multipart"}, files=files,
                                   headers={"Authorization": f"Bearer {tok}"})
                st["caption_id"] = self._handle(r).get("id", "added")
            except (ConnectorError, httpx.HTTPError) as e:
                warnings.append(f"Uploaded, but the captions failed: {getattr(e, 'message', e)}")
        return warnings

    def list_playlists(self, auth: ChannelAuth) -> list[dict]:
        d = self._req("GET", f"{API}/playlists", auth.access_token, params={"part": "snippet", "mine": "true", "maxResults": 50})
        return [{"id": p["id"], "title": p.get("snippet", {}).get("title", "")} for p in d.get("items", [])]

    def _upload(self, url: str, m: MediaItem, size: int, tok: str) -> dict:
        start = 0
        with open(m.path, "rb") as fh:
            while True:
                fh.seek(start)
                data = fh.read(UPLOAD_CHUNK)
                end = start + len(data) - 1
                try:
                    r = self.http.put(url, content=data, headers={
                        "Authorization": f"Bearer {tok}", "Content-Type": m.mime,
                        "Content-Range": f"bytes {start}-{end}/{size}"})
                except httpx.HTTPError as e:
                    raise RetryableError(f"YouTube upload interrupted: {e}") from e
                if r.status_code == 308:
                    rng = r.headers.get("range", "")
                    start = int(rng.split("-")[-1]) + 1 if rng else end + 1
                    continue
                return self._handle(r)

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        vid = ctx.state["video_id"]
        d = self._req("GET", f"{API}/videos", ctx.channel.access_token, params={"part": "status,processingDetails", "id": vid})
        items = d.get("items") or []
        if not items:
            return PublishOutcome("processing", state=ctx.state, message="YouTube is processing the upload…")
        status = items[0].get("status", {})
        if status.get("uploadStatus") in ("failed", "rejected"):
            raise PermanentError(f"YouTube {status.get('uploadStatus')}: {status.get('failureReason') or status.get('rejectionReason')}")
        warnings = self.after_upload(ctx, vid)
        fc = ((ctx.options or {}).get("first_comment") or "").strip()
        if fc and not ctx.state.get("first_comment_id"):
            try:
                ctx.state["first_comment_id"] = self.post_comment(ctx.channel, vid, fc)
            except (PermanentError, RetryableError, AuthError) as e:
                warnings.append(f"Uploaded, but the first comment failed: {e.message}")
        priv = status.get("privacyStatus", "")
        link = f"https://youtube.com/shorts/{vid}" if self.is_short(ctx.options or {}) else f"https://youtu.be/{vid}"
        return PublishOutcome("published", external_id=vid, permalink=link, state=ctx.state, warnings=warnings,
                              message=f"Uploaded to YouTube ({priv or 'processing'})")

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        d = self._req("GET", f"{API}/videos", ctx.channel.access_token, params={"part": "statistics", "id": external_id})
        items = d.get("items") or []
        if not items:
            return {}
        s = items[0].get("statistics", {})
        out = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
               "comments": int(s.get("commentCount", 0))}
        if out["views"]:
            out["engagement_rate"] = round(100.0 * (out["likes"] + out["comments"]) / out["views"], 2)
        return out

    # ---- community
    def list_comments(self, auth: ChannelAuth, limit_media: int = 10) -> list[dict]:
        d = self._req("GET", f"{API}/commentThreads", auth.access_token, params={
            "part": "snippet,replies", "allThreadsRelatedToChannelId": auth.external_id, "maxResults": 50,
            "order": "time", "textFormat": "plainText"})
        out = []
        for th in d.get("items") or []:
            top = th["snippet"]["topLevelComment"]
            vid = th["snippet"].get("videoId", "")
            out.append(self._c(top, vid, None, auth.external_id))
            for r in (th.get("replies") or {}).get("comments", []):
                out.append(self._c(r, vid, top["id"], auth.external_id))
        return out

    @staticmethod
    def _c(c: dict, vid: str, parent: str | None, me: str) -> dict:
        sn = c["snippet"]
        at = None
        if sn.get("publishedAt"):
            try:
                at = datetime.strptime(sn["publishedAt"][:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                pass
        return {"external_id": c["id"], "parent_external_id": parent, "media_external_id": vid,
                "media_permalink": f"https://youtu.be/{vid}" if vid else "", "media_caption": "",
                "author": sn.get("authorDisplayName", ""), "text": sn.get("textDisplay") or sn.get("textOriginal", ""),
                "at": at, "like_count": sn.get("likeCount", 0),
                "is_own": (sn.get("authorChannelId") or {}).get("value") == me}

    def reply_comment(self, auth: ChannelAuth, comment_external_id: str, text: str, media_external_id: str = "") -> str:
        d = self._req("POST", f"{API}/comments", auth.access_token, params={"part": "snippet"},
                      json={"snippet": {"parentId": comment_external_id, "textOriginal": text}})
        return d.get("id", "")

    def post_comment(self, auth: ChannelAuth, media_external_id: str, text: str) -> str:
        d = self._req("POST", f"{API}/commentThreads", auth.access_token, params={"part": "snippet"},
                      json={"snippet": {"videoId": media_external_id,
                                        "topLevelComment": {"snippet": {"textOriginal": text}}}})
        return d.get("id", "")
