"""X (Twitter) connector: OAuth 2.0 Authorization Code with PKCE + X API v2.

  GET  https://x.com/i/oauth2/authorize                 consent (PKCE S256, state)
  POST https://api.x.com/2/oauth2/token                 code -> access (2h) + refresh token (offline.access)
  POST https://api.x.com/2/oauth2/revoke                disconnect
  GET  /2/users/me?user.fields=...                      profile (followers, posts)
  POST /2/media/upload/initialize | /{id}/append | /{id}/finalize, GET /2/media/upload?command=STATUS
                                                        chunked media upload (needs media.write)
  POST /2/tweets                                        the post, each thread part as a reply to the one before
                                                        (reply.in_reply_to_tweet_id), then the optional "first comment"
  GET  /2/users/{id}/tweets                             past posts (history grid)
  GET  /2/tweets/{id}?tweet.fields=public_metrics       insights

Refresh tokens rotate: every refresh returns a new one, and the old one stops working.
A confidential client (an app with a client secret) authenticates the token call with HTTP Basic.
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx

from .base import (THREAD_MAX_PARTS, http_errors, nothing_sent, Unconfirmed, ConnectorError, AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem, NotConfigured,
                   PermanentError, Profile, PublishContext, PublishOutcome, RetryableError, TokenBundle,
                   count_hashtags, thread_parts)

AUTH_URL = "https://x.com/i/oauth2/authorize"
API = "https://api.x.com/2"
TOKEN_URL = f"{API}/oauth2/token"
REVOKE_URL = f"{API}/oauth2/revoke"
TEXT_LIMIT = 280
LONG_TEXT_LIMIT = 25000          # X Premium long posts
CHUNK = 4 * 1024 * 1024          # X recommends <= 5 MB per APPEND segment
IMAGE_MAX = 5 * 1024 * 1024
GIF_MAX = 15 * 1024 * 1024
URL_RE = re.compile(r"https?://\S+")


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def weighted_len(text: str) -> int:
    """X's weighted length: every URL counts 23, most Latin chars 1, CJK/emoji 2."""
    text = text or ""
    n = 0
    for _ in URL_RE.findall(text):
        n += 23
    text = URL_RE.sub("", text)
    for ch in text:
        cp = ord(ch)
        light = (cp <= 0x10FF or 0x2000 <= cp <= 0x200D or 0x2010 <= cp <= 0x201F or 0x2032 <= cp <= 0x2037)
        n += 1 if light else 2
    return n


def _utcnow() -> datetime:
    return datetime.utcnow()


class XConnector(Connector):
    id = "x"
    name = "X"
    color = "#000000"
    caption_limit = TEXT_LIMIT
    uses_pkce = True
    capabilities = {
        "oauth": True, "manual_token": False, "insights": True, "first_comment": True, "notify_mode": True,
        "comments": False, "comments_note": "Reading replies needs a paid X API search tier, so the inbox skips X.",
        "post_types": ["post"], "threads": True, "thread_limit": TEXT_LIMIT,
    }
    format_key = "x_type"

    # ------------------------------------------------------------------ http
    @http_errors
    def _handle(self, r: httpx.Response) -> dict:
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"raw": r.text[:300]}
        if r.status_code < 400 and not (isinstance(data, dict) and data.get("errors") and not data.get("data")):
            return data
        if isinstance(data, dict) and isinstance(data.get("error"), str):  # oauth endpoint
            msg, code = data.get("error_description") or data["error"], data["error"]
        else:
            errs = (data.get("errors") or [{}]) if isinstance(data, dict) else [{}]
            msg = (data.get("detail") if isinstance(data, dict) else "") or errs[0].get("message") or errs[0].get("detail") or r.text[:200]
            code = (data.get("title") if isinstance(data, dict) else "") or errs[0].get("title") or ""
        label = f"X error {r.status_code}{' ' + code if code else ''}: {msg}"
        if r.status_code == 401 or code in ("invalid_grant", "invalid_token", "Unauthorized"):
            raise AuthError(label, code=code or str(r.status_code), raw=data)
        if r.status_code == 429:
            reset = r.headers.get("x-rate-limit-reset")
            wait = 900
            if reset and reset.isdigit():
                wait = max(60, int(reset) - int((_utcnow() - datetime(1970, 1, 1)).total_seconds()))
            raise RetryableError(label + " (rate limit)", code="rate_limit", retry_after=wait, raw=data)
        if r.status_code >= 500:
            raise RetryableError(label, code=str(r.status_code), retry_after=120, raw=data)
        raise PermanentError(label, code=code or str(r.status_code), raw=data)

    def _req(self, method: str, url: str, token: str, **kw) -> dict:
        headers = {"Authorization": f"Bearer {token}", **kw.pop("headers", {})}
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to X: {e}") from e
        return self._handle(r)

    # ------------------------------------------------------------------ auth
    def is_configured(self) -> bool:
        return bool(self.settings.x_client_id)

    def setup_hint(self) -> str:
        return ("Add your X OAuth 2.0 client ID (and secret) in Settings -> App keys, or set X_CLIENT_ID / "
                "X_CLIENT_SECRET. See SETUP.md.")

    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        if not code_verifier:
            raise PermanentError("X needs PKCE: start Connect again")
        q = {"response_type": "code", "client_id": self.settings.x_client_id, "redirect_uri": redirect_uri,
             "scope": self.settings.x_scopes, "state": state, "code_challenge": pkce_challenge(code_verifier),
             "code_challenge_method": "S256"}
        return f"{AUTH_URL}?{urlencode(q)}"

    def _token_call(self, form: dict) -> TokenBundle:
        form = {**form, "client_id": self.settings.x_client_id}
        kw = {}
        if self.settings.x_client_secret:  # confidential client
            kw["auth"] = (self.settings.x_client_id, self.settings.x_client_secret)
        try:
            r = self.http.post(TOKEN_URL, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, **kw)
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r)
        return TokenBundle(access_token=d["access_token"], refresh_token=d.get("refresh_token"),
                           expires_at=_utcnow() + timedelta(seconds=int(d.get("expires_in", 7200))),
                           scopes=d.get("scope", self.settings.x_scopes))

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        bundle = self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
                                   "code_verifier": code_verifier})
        return bundle, self._profile(bundle.access_token)

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return bool(auth.refresh_token and (not auth.token_expires_at or auth.token_expires_at - now <= timedelta(minutes=10)))

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        if not auth.refresh_token:
            return None
        b = self._token_call({"grant_type": "refresh_token", "refresh_token": auth.refresh_token})
        b.refresh_token = b.refresh_token or auth.refresh_token
        return b

    def revoke(self, auth: ChannelAuth) -> None:
        tok = auth.refresh_token or auth.access_token
        if not tok:
            return
        kw = {"auth": (self.settings.x_client_id, self.settings.x_client_secret)} if self.settings.x_client_secret else {}
        try:
            self.http.post(REVOKE_URL, data={"token": tok, "client_id": self.settings.x_client_id,
                                             "token_type_hint": "refresh_token" if auth.refresh_token else "access_token"}, **kw)
        except httpx.HTTPError:
            pass

    def _profile(self, token: str) -> Profile:
        d = self._req("GET", f"{API}/users/me", token,
                      params={"user.fields": "id,name,username,profile_image_url,public_metrics,verified"}).get("data") or {}
        pm = d.get("public_metrics") or {}
        return Profile(external_id=str(d.get("id", "")), username=d.get("username", ""),
                       display_name=d.get("name", "") or d.get("username", ""),
                       avatar_url=(d.get("profile_image_url") or "").replace("_normal", "_400x400"),
                       meta={"account_type": "verified" if d.get("verified") else "account",
                             "followers_count": pm.get("followers_count"), "media_count": pm.get("tweet_count"),
                             "following_count": pm.get("following_count")})

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return self._profile(auth.access_token)

    # ------------------------------------------------------------------ composing
    def formats(self) -> list[list[str]]:
        return [["post", "Post"]]

    def option_schema(self) -> list[dict]:
        return [
            {"key": "x_type", "label": "Post type", "type": "select", "default": "post", "choices": [["post", "Post"]]},
            {"key": "posting_method", "label": "Posting method", "type": "select", "default": "auto",
             "choices": [["auto", "Automatic - we publish it"], ["notify", "Notify me - I finish in the app"]]},
            {"key": "thread", "label": "Thread", "type": "thread", "default": [],
             "help": "More posts, each published as a reply to the one before. A list of texts, or {text, media_ids} "
                     "for a part with up to 4 images or 1 video."},
            {"key": "first_comment", "label": "Reply under the post", "type": "textarea", "default": "",
             "help": "Posted as a reply right after publishing (under the last part of a thread)."},
            {"key": "long_post", "label": "Long post (X Premium)", "type": "toggle", "default": False,
             "help": "Allows up to 25,000 characters - only for Premium accounts."},
            {"key": "reply_settings", "label": "Who can reply", "type": "select", "default": "",
             "choices": [["", "Everyone"], ["following", "Accounts you follow"], ["mentionedUsers", "Only people you mention"]]},
        ]

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        issues: list[Issue] = []
        options = options or {}
        n = weighted_len(caption)
        limit = LONG_TEXT_LIMIT if options.get("long_post") else TEXT_LIMIT
        if n > limit:
            issues.append(Issue("error", "caption_too_long",
                                f"X posts max {limit} characters ({n} now, links count 23)."
                                + ("" if options.get("long_post") else " Turn on 'Long post' if you have Premium."), "caption"))
        if not (caption or "").strip() and not media:
            issues.append(Issue("error", "empty", "An X post needs text or media.", "caption"))
        tags = count_hashtags(caption)
        if tags > rules.hashtag_limit:
            issues.append(Issue("warning", "brand_hashtags", f"{tags} hashtags - 1-2 work best on X.", "caption"))
        vids = [m for m in media if m.kind == "video" or m.mime == "image/gif"]
        if vids and len(media) > 1:
            issues.append(Issue("error", "x_mix", "X takes up to 4 images, or exactly 1 video/GIF.", "media"))
        if len(media) > 4:
            issues.append(Issue("error", "x_max_media", "X allows at most 4 images per post.", "media"))
        for m in media:
            if m.kind == "image" and m.mime != "image/gif" and m.size > IMAGE_MAX:
                issues.append(Issue("error", "image_too_big", f"{m.filename} is over 5 MB (X's image limit).", "media"))
            if m.mime == "image/gif" and m.size > GIF_MAX:
                issues.append(Issue("error", "gif_too_big", f"{m.filename} is over 15 MB (X's GIF limit).", "media"))
            if m.kind == "video" and m.duration and m.duration > 140:
                issues.append(Issue("warning", "x_video_long",
                                    f"{m.filename} is {m.duration:.0f}s - longer than 2:20 needs X Premium.", "media"))
        parts = thread_parts(options)
        if len(parts) + 1 > THREAD_MAX_PARTS:
            issues.append(Issue("error", "thread_too_long", f"A thread can have at most {THREAD_MAX_PARTS} posts "
                                                            f"({len(parts) + 1} now).", "thread"))
        for i, p in enumerate(parts, 2):
            pn = weighted_len(p["text"])
            if pn > limit:
                issues.append(Issue("error", "thread_part_too_long",
                                    f"Thread post {i} is {pn} characters (max {limit}, links count 23).", "thread"))
            if len(p["media_ids"]) > 4:
                issues.append(Issue("error", "x_max_media", f"Thread post {i}: X allows at most 4 images per post.", "thread"))
        return issues

    # ------------------------------------------------------------------ publishing
    @staticmethod
    def _category(m: MediaItem) -> str:
        if m.mime == "image/gif":
            return "tweet_gif"
        return "tweet_video" if m.kind == "video" else "tweet_image"

    def _upload(self, tok: str, m: MediaItem) -> tuple[str, dict | None]:
        path = m.path
        data = path.read_bytes()
        init = self._req("POST", f"{API}/media/upload/initialize", tok,
                         json={"media_type": m.mime, "total_bytes": len(data), "media_category": self._category(m)})
        mid = str((init.get("data") or {}).get("id") or "")
        if not mid:
            raise RetryableError("X did not return a media id")
        for i in range(0, max(1, len(data)), CHUNK):
            self._req("POST", f"{API}/media/upload/{mid}/append", tok,
                      data={"segment_index": str(i // CHUNK)}, files={"media": ("chunk", data[i:i + CHUNK], "application/octet-stream")})
        fin = self._req("POST", f"{API}/media/upload/{mid}/finalize", tok)
        info = (fin.get("data") or {}).get("processing_info")
        if m.alt_text and m.kind == "image":
            try:
                self._req("POST", f"{API}/media/metadata", tok,
                          json={"id": mid, "metadata": {"alt_text": {"text": m.alt_text[:1000]}}})
            except (PermanentError, RetryableError):
                pass
        return mid, info

    def _media_state(self, tok: str, mid: str) -> dict:
        d = self._req("GET", f"{API}/media/upload", tok, params={"command": "STATUS", "media_id": mid})
        return (d.get("data") or {}).get("processing_info") or {"state": "succeeded"}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        tok = ctx.channel.access_token
        if not tok:
            raise AuthError("X channel has no access token - reconnect it.")
        st = ctx.state
        if st.get("tweet_id") or st.get("uploaded"):
            return self.poll(ctx)  # resume: never upload or post twice
        st["media_ids"], st["pending"], st["thread_media"] = [], [], []
        for m in ctx.media:
            mid, info = self._upload(tok, m)
            st["media_ids"].append(mid)
            if info and info.get("state") not in (None, "succeeded"):
                st["pending"].append(mid)
        for items in (ctx.attachments or {}).get("thread") or []:   # media of each thread part, in order
            ids = []
            for m in items:
                mid, info = self._upload(tok, m)
                ids.append(mid)
                if info and info.get("state") not in (None, "succeeded"):
                    st["pending"].append(mid)
            st["thread_media"].append(ids)
        st["uploaded"] = True
        return self.poll(ctx)

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        tok, st, opts = ctx.channel.access_token, ctx.state, ctx.options or {}
        if not st.get("tweet_id"):
            still = []
            for mid in st.get("pending") or []:
                info = self._media_state(tok, mid)
                if info.get("state") == "failed":
                    err = (info.get("error") or {}).get("message", "processing failed")
                    raise PermanentError(f"X could not process the media: {err}")
                if info.get("state") != "succeeded":
                    still.append(mid)
            st["pending"] = still
            if still:
                return PublishOutcome("processing", state=st, message="X is processing the video…", poll_after_seconds=10)
            body: dict = {"text": ctx.caption or ""}
            if st.get("media_ids"):
                body["media"] = {"media_ids": st["media_ids"]}
            if opts.get("reply_settings"):
                body["reply_settings"] = opts["reply_settings"]
            if st.get("publish_called"):   # an earlier attempt sent the post but never saw the reply
                found = self.find_published(ctx)
                if not found:
                    raise Unconfirmed("X may already have this post (the reply to the last attempt was lost) - "
                                      "check your profile and mark it posted or send it again.")
                st["tweet_id"] = str(found["external_id"])
            else:
                st["publish_called"], st["publish_called_at"] = True, _utcnow().isoformat()
                ctx.checkpoint()
                try:
                    d = self._req("POST", f"{API}/tweets", tok, json=body).get("data") or {}
                except ConnectorError as e:
                    if nothing_sent(e):
                        st.pop("publish_called", None)
                    raise
                st["tweet_id"] = str(d.get("id", ""))
        tid = st["tweet_id"]
        warnings = []
        last = self._post_thread(tok, ctx, warnings)
        fc = (opts.get("first_comment") or "").strip()
        if fc and not st.get("first_comment_id"):
            try:
                r = self._req("POST", f"{API}/tweets", tok, json={"text": fc, "reply": {"in_reply_to_tweet_id": last}})
                st["first_comment_id"] = (r.get("data") or {}).get("id")
            except (PermanentError, RetryableError, AuthError) as e:
                warnings.append(f"Posted, but the reply failed: {e.message}")
        user = ctx.channel.username or "i"
        return PublishOutcome("published", external_id=tid, permalink=f"https://x.com/{user}/status/{tid}", state=st,
                              warnings=warnings, message="Published to X")

    def _post_thread(self, tok: str, ctx: PublishContext, warnings: list[str]) -> str:
        """Post each thread part as a reply to the one before; returns the id of the last post.

        Ids are kept in state["thread_ids"], so a retry after a rate limit carries on where it stopped and never
        posts a part twice. Like the main post, each part is checkpointed before it is sent: if the reply to that
        call was lost, the retry stops as Unconfirmed instead of maybe posting the part again. A part X refuses
        for good stops the thread with a warning: the posts before it are live.
        """
        st, parts = ctx.state, thread_parts(ctx.options)
        done = st.setdefault("thread_ids", [])
        media = st.get("thread_media") or []
        prev = done[-1] if done else st["tweet_id"]
        if st.get("thread_sending") is not None and st["thread_sending"] >= len(done) and len(done) < len(parts):
            raise Unconfirmed(f"X may already have thread post {len(done) + 2} (the reply to the last attempt was lost) - "
                              f"check the thread on your profile and mark it posted.")
        for i in range(len(done), len(parts)):
            body: dict = {"text": parts[i]["text"], "reply": {"in_reply_to_tweet_id": prev}}
            if i < len(media) and media[i]:
                body["media"] = {"media_ids": media[i]}
            st["thread_sending"] = i
            ctx.checkpoint()
            try:
                d = self._req("POST", f"{API}/tweets", tok, json=body).get("data") or {}
            except ConnectorError as e:
                if not nothing_sent(e):
                    raise
                st.pop("thread_sending", None)
                if isinstance(e, PermanentError):
                    warnings.append(f"Posted {len(done) + 1} of {len(parts) + 1} thread posts; X refused post {i + 2}: {e.message}")
                    break
                raise
            prev = str(d.get("id", ""))
            done.append(prev)
            st.pop("thread_sending", None)
        return prev

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        d = self._req("GET", f"{API}/tweets/{external_id}", ctx.channel.access_token,
                      params={"tweet.fields": "public_metrics"}).get("data") or {}
        pm = d.get("public_metrics") or {}
        out = {"views": pm.get("impression_count"), "likes": pm.get("like_count"), "comments": pm.get("reply_count"),
               "shares": (pm.get("retweet_count") or 0) + (pm.get("quote_count") or 0), "saved": pm.get("bookmark_count")}
        views = out["views"] or 0
        if views:
            inter = (out["likes"] or 0) + (out["comments"] or 0) + out["shares"] + (out["saved"] or 0)
            out["engagement_rate"] = round(100.0 * inter / views, 2)
        return out

    def list_media(self, auth: ChannelAuth, limit: int = 24, after: str = "") -> dict:
        params = {"max_results": max(5, min(int(limit or 24), 100)),
                  "tweet.fields": "created_at,public_metrics,attachments", "expansions": "attachments.media_keys",
                  "media.fields": "url,preview_image_url,type", "exclude": "replies"}
        if after:
            params["pagination_token"] = after
        d = self._req("GET", f"{API}/users/{auth.external_id}/tweets", auth.access_token, params=params)
        media = {m.get("media_key"): m for m in ((d.get("includes") or {}).get("media") or [])}
        items = []
        for t in d.get("data") or []:
            keys = (t.get("attachments") or {}).get("media_keys") or []
            first = media.get(keys[0]) if keys else None
            pm = t.get("public_metrics") or {}
            items.append({
                "external_id": t.get("id", ""), "caption": t.get("text", ""),
                "media_type": (first or {}).get("type", "text").upper(), "product_type": "POST",
                "media_url": (first or {}).get("url", "") or "",
                "thumbnail_url": (first or {}).get("url") or (first or {}).get("preview_image_url") or "",
                "permalink": f"https://x.com/{auth.username or 'i'}/status/{t.get('id', '')}",
                "timestamp": t.get("created_at", ""), "like_count": pm.get("like_count"),
                "comments_count": pm.get("reply_count"),
            })
        return {"items": items, "next": (d.get("meta") or {}).get("next_token", "") or ""}
