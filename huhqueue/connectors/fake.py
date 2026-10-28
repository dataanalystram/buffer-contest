"""A fake network for demos and tests. Never touches the internet.

Enable with HUHQ_ENABLE_FAKE=1. Captions containing these magic words drive
the outcome, so the UI/e2e tests can exercise every state:
  FAIL      -> permanent failure        RETRY  -> transient error (then succeeds)
  AUTHFAIL  -> token revoked            SLOW   -> 'processing' once, then published
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from .base import (AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem, PermanentError,
                   Profile, PublishContext, PublishOutcome, RetryableError, TokenBundle, count_hashtags,
                   thread_parts, utf16_len)


class FakeConnector(Connector):
    id = "fake"
    name = "Demo network"
    color = "#C9ACFA"
    caption_limit = 500
    capabilities = {"oauth": True, "manual_token": True, "insights": True, "first_comment": True,
                    "notify_mode": True, "comments": True, "comments_note": "Demo comments.", "mentions": True, "post_types": ["post"],
                    "threads": True, "thread_limit": 500, "demographics": True, "stories": True}
    replies: list[dict] = []
    published: list[dict] = []  # class-level log so tests can assert

    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        # mimics a network's own consent screen ("Continue as …?") served by the local app
        from urllib.parse import urlencode
        return "/fake-consent?" + urlencode({"redirect_uri": redirect_uri, "state": state})

    def exchange_code(self, code, redirect_uri, code_verifier=""):
        if code != "fakecode":
            raise PermanentError("bad code")
        return self.connect_with_token("fake-token-" + secrets.token_hex(3))

    def connect_with_token(self, access_token, extra=None):
        handle = (extra or {}).get("username") or "demo_" + access_token[-4:]
        return (TokenBundle(access_token=access_token, refresh_token="fake-refresh",
                            expires_at=datetime.utcnow() + timedelta(days=60)),
                Profile(external_id="fake-" + handle, username=handle, display_name=handle.title(),
                        meta={"followers_count": 1234, "media_count": 56, "account_type": "Demo creator"}))

    def fetch_profile(self, auth):
        return Profile(external_id=auth.external_id, username=auth.username, display_name=auth.username.title(),
                       meta={"followers_count": 1240, "media_count": 57, "account_type": "Demo creator"})

    def list_comments(self, auth, limit_media=10):
        now = datetime.utcnow()
        return [
            {"external_id": "c1", "parent_external_id": None, "media_external_id": "m1",
             "media_permalink": "https://example.invalid/p/m1", "media_caption": "Demo post about GPT news",
             "author": "ai_fan", "text": "Which model is this?", "at": now - timedelta(hours=2), "like_count": 3,
             "is_own": False},
            {"external_id": "c2", "parent_external_id": None, "media_external_id": "m1",
             "media_permalink": "https://example.invalid/p/m1", "media_caption": "Demo post about GPT news",
             "author": "dev_daily", "text": "Great breakdown!", "at": now - timedelta(hours=1), "like_count": 1,
             "is_own": False},
        ]

    def list_mentions(self, auth, limit=25):
        now = datetime.utcnow()
        return [{"external_id": "tag-m77", "parent_external_id": None, "media_external_id": "m77",
                 "media_permalink": "https://example.invalid/p/m77", "media_caption": "Loved this breakdown by @huhbyte",
                 "author": "tech_reviewer", "text": "Loved this breakdown by @huhbyte", "at": now - timedelta(hours=3),
                 "like_count": 12, "is_own": False, "kind": "mention"}]

    def reply_comment(self, auth, comment_external_id, text, media_external_id=""):
        FakeConnector.replies.append({"to": comment_external_id, "text": text})
        return "r_" + secrets.token_hex(3)

    def post_comment(self, auth, media_external_id, text):
        return "fc_" + secrets.token_hex(3)

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return bool(auth.token_expires_at and auth.token_expires_at - now < timedelta(days=1))

    def refresh(self, auth: ChannelAuth):
        return TokenBundle(access_token=(auth.access_token or "fake") + "-r", refresh_token="fake-refresh",
                           expires_at=datetime.utcnow() + timedelta(days=60))

    def account_info(self, auth):
        return {"note": "Demo network - nothing leaves your laptop."}

    format_key = "fk_format"

    def formats(self):
        return [["post", "Single post"], ["carousel", "Carousel"], ["reel", "Reel"]]

    def suggest_format(self, caption, media):
        if any(m.kind == "video" for m in media):
            return "reel", "Video → Reel"
        if len(media) > 1:
            return "carousel", f"{len(media)} files → Carousel"
        return "post", "One file → single post"

    def option_schema(self):
        return [
            {"key": "posting_method", "label": "Posting method", "type": "select", "default": "auto",
             "choices": [["auto", "Automatic"], ["notify", "Notify me"]]},
            {"key": "thread", "label": "Thread", "type": "thread", "default": []},
            {"key": "first_comment", "label": "First comment", "type": "textarea", "default": ""},
        ]

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        out = []
        if utf16_len(caption) > self.caption_limit:
            out.append(Issue("error", "caption_too_long", f"Max {self.caption_limit} characters.", "caption"))
        if not caption.strip() and not media:
            out.append(Issue("error", "empty", "Write something or add media.", "caption"))
        if count_hashtags(caption) > rules.hashtag_limit:
            out.append(Issue("warning", "brand_hashtags",
                             f"{count_hashtags(caption)} hashtags - the house style is {rules.hashtag_limit} max.", "caption"))
        for i, p in enumerate(thread_parts(options), 2):
            if utf16_len(p["text"]) > self.caption_limit:
                out.append(Issue("error", "thread_part_too_long", f"Thread post {i}: max {self.caption_limit} characters.", "thread"))
        return out

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        c = ctx.caption or ""
        if "AUTHFAIL" in c:
            raise AuthError("Demo: token revoked")
        if "FAIL" in c and "RETRY" not in c:
            raise PermanentError("Demo: the network rejected this post")
        if "RETRY" in c and not ctx.state.get("retried"):
            ctx.state["retried"] = True
            raise RetryableError("Demo: temporary outage", retry_after=1)
        if "SLOW" in c and not ctx.state.get("slow"):
            ctx.state["slow"] = True
            return PublishOutcome("processing", state=ctx.state, message="Demo: processing…", poll_after_seconds=1)
        return self._done(ctx)

    def poll(self, ctx):
        return self._done(ctx)

    def _done(self, ctx):
        ext = "fk_" + secrets.token_hex(4)
        FakeConnector.published.append({"target": ctx.target_id, "caption": ctx.caption, "media": len(ctx.media),
                                        "thread": [p["text"] for p in thread_parts(ctx.options)],
                                        "thread_media": [len(x) for x in (ctx.attachments or {}).get("thread") or []]})
        return PublishOutcome("published", external_id=ext, permalink=f"https://example.invalid/p/{ext}",
                              state=ctx.state, message="Published to demo network")

    def audience_demographics(self, auth, timeframe="this_month"):
        base = {"age": {"13-17": 20, "18-24": 410, "25-34": 520, "35-44": 190, "45-54": 70, "55-64": 20, "65+": 10},
                "gender": {"F": 540, "M": 660, "U": 40},
                "country": {"US": 480, "IN": 310, "GB": 120, "DE": 80, "BR": 60},
                "city": {"New York, New York": 90, "Bengaluru, Karnataka": 85, "London, England": 70, "Austin, Texas": 40}}
        engaged = {k: {d: max(1, v // 4) for d, v in vals.items()} for k, vals in base.items()}
        return {"followers": base, "engaged": engaged, "notes": [], "timeframe": timeframe}

    stories: list[dict] = []   # tests put live demo stories here

    def list_stories(self, auth):
        return [dict(x) for x in FakeConnector.stories]

    def story_insights(self, auth, external_id):
        n = sum(map(ord, external_id)) % 50
        return {"views": 200 + n * 3, "reach": 150 + n * 2, "replies": n // 5, "shares": n // 7, "total_interactions": n // 3,
                "tap_forward": 40 + n, "tap_back": 5, "tap_exit": 12, "swipe_forward": 3}

    def fetch_insights(self, ctx, external_id):
        n = sum(map(ord, external_id)) % 97
        return {"views": 100 + n * 7, "reach": 80 + n * 5, "likes": n, "comments": n // 4, "shares": n // 9,
                "engagement_rate": round(100 * (n + n // 4) / (80 + n * 5), 2)}
