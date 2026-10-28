"""LinkedIn connector: personal profiles (default) and company pages (optional second app).

Personal profile ("Share on LinkedIn" + "Sign In with LinkedIn using OpenID Connect", no review needed):
  GET  https://www.linkedin.com/oauth/v2/authorization      consent (scopes: openid profile email w_member_social)
  POST https://www.linkedin.com/oauth/v2/accessToken        code -> access token (60 days; refresh tokens only for
                                                            approved partners, so HuhQueue warns 7 days before expiry)
  GET  https://api.linkedin.com/v2/userinfo                 sub -> author urn:li:person:{sub}

Company pages (Community Management API; LinkedIn wants it to be the ONLY product on its app, so it is a separate
app with its own keys, LINKEDIN_PAGES_*, and is off until LINKEDIN_PAGES_ENABLED=1):
  GET  /rest/organizationAcls?q=roleAssignee&state=APPROVED pages the member administers -> one channel per page
  GET  /rest/organizations/{id}                             page name / vanity name

Publishing (REST API, header LinkedIn-Version = settings.linkedin_api_version, X-Restli-Protocol-Version 2.0.0):
  POST /rest/images?action=initializeUpload     -> PUT bytes      image / multi-image (with alt text)
  POST /rest/documents?action=initializeUpload  -> PUT bytes      PDF document carousel
  POST /rest/videos?action=initializeUpload     -> PUT each byte range from uploadInstructions, collect ETags,
  POST /rest/videos?action=finalizeUpload                         then GET /rest/videos/{urn} until AVAILABLE
  POST /rest/posts                               the post (x-restli-id header = urn:li:share:... / urn:li:ugcPost:...)
  POST /rest/socialActions/{urn}/comments        first comment (saved in provider state so a retry never doubles it)
  Link previews: the API does not build them, so we read the page's og:title / og:description / og:image, upload
  the image and send content.article.

Design notes: the two-app split, commentary escaping, byte-range video upload, link-preview approach and error
mapping follow coollabsio/shoutrrr's LinkedInConnector (Apache-2.0); documents and the first comment follow the
behaviour described for Postiz (AGPL - idea only, no code). See NOTICE.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode, urljoin

import httpx

from .base import (http_errors, nothing_sent, Unconfirmed, ConnectorError, AuthError, BrandRules, ChannelAuth, Connector, Issue, MediaItem, NotConfigured,
                   PermanentError, Profile, PublishContext, PublishOutcome, RetryableError, TokenBundle,
                   count_hashtags)

AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
USERINFO_URL = "https://api.linkedin.com/v2/userinfo"
REST = "https://api.linkedin.com/rest"
TEXT_LIMIT = 3000
COMMENT_LIMIT = 1250
MAX_IMAGES = 20
IMAGE_MAX_BYTES = 10 * 1024 * 1024
DOC_MAX_BYTES = 100 * 1024 * 1024
VIDEO_MAX_BYTES = 500 * 1024 * 1024    # API limit for member/organisation videos
VIDEO_MIN_S, VIDEO_MAX_S = 3, 30 * 60
EXPIRY_WARNING_DAYS = 7
URL_RE = re.compile(r"https?://[^\s<>\"']+")
# LinkedIn "little text" reserved characters (escaped with a backslash). Hashtags (#tag) and mentions written as
# @[Name](urn:li:person:xyz) are kept as they are.
_RESERVED = re.compile(r"([\\|{}@\[\]()<>*_~])")
_MENTION = re.compile(r"@\[[^\]]+\]\(urn:li:(?:person|organization):[A-Za-z0-9_-]+\)")

FORMATS = [["auto", "Auto"], ["text", "Text"], ["image", "Image"], ["multi_image", "Multi-image"],
           ["document", "Document (PDF carousel)"], ["video", "Video"], ["article", "Link preview"]]


def escape_commentary(text: str) -> str:
    """Escape LinkedIn's reserved characters but keep @-mentions in little-text form and #hashtags."""
    text = text or ""
    out, last = [], 0
    for m in _MENTION.finditer(text):
        out.append(_RESERVED.sub(r"\\\1", text[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(_RESERVED.sub(r"\\\1", text[last:]))
    return "".join(out)


def first_url(text: str) -> str:
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,);!?") if m else ""


def _utcnow() -> datetime:
    return datetime.utcnow()


class LinkedInConnector(Connector):
    id = "linkedin"
    name = "LinkedIn"
    color = "#0A66C2"
    caption_limit = TEXT_LIMIT
    accepts_documents = True
    expiry_warning_days = EXPIRY_WARNING_DAYS
    author_kind = "person"
    capabilities = {
        "oauth": True, "manual_token": True, "insights": False, "first_comment": True, "notify_mode": False,
        "comments": False, "comments_note": "Reading comments on personal posts needs LinkedIn partner access.",
        "post_types": ["text", "image", "multi_image", "document", "video", "article"],
        "token_days": 60,
    }
    format_key = "li_format"

    # ------------------------------------------------------------------ config
    def _client(self) -> tuple[str, str, str]:
        s = self.settings
        return s.linkedin_client_id, s.linkedin_client_secret, s.linkedin_scopes

    @property
    def api_version(self) -> str:
        return getattr(self.settings, "linkedin_api_version", "") or "202605"

    def is_configured(self) -> bool:
        cid, secret, _ = self._client()
        return bool(cid and secret)

    def setup_hint(self) -> str:
        return ("Create a LinkedIn app (developer.linkedin.com), add the products 'Sign In with LinkedIn using OpenID "
                "Connect' and 'Share on LinkedIn', then paste the client ID and secret in Settings -> App keys or set "
                "LINKEDIN_CLIENT_ID / LINKEDIN_CLIENT_SECRET. See SETUP.md.")

    # ------------------------------------------------------------------ http
    def _headers(self, token: str, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {token}", "LinkedIn-Version": self.api_version,
                "X-Restli-Protocol-Version": "2.0.0", **(extra or {})}

    @http_errors
    def _handle(self, r: httpx.Response) -> httpx.Response:
        if r.status_code < 400:
            return r
        try:
            data = r.json() if r.content else {}
        except ValueError:
            data = {"message": r.text[:300]}
        if not isinstance(data, dict):
            data = {"message": str(data)[:300]}
        msg = data.get("message") or data.get("error_description") or data.get("error") or r.text[:200]
        code = str(data.get("serviceErrorCode") or data.get("code") or data.get("error") or r.status_code)
        label = f"LinkedIn error {r.status_code}: {msg}"
        if r.status_code == 401 or code in ("invalid_token", "REVOKED_ACCESS_TOKEN", "EXPIRED_ACCESS_TOKEN"):
            raise AuthError(label, code=code, raw=data)
        if r.status_code == 429:
            ra = r.headers.get("retry-after", "")
            raise RetryableError(label + " (rate limit)", code="rate_limit",
                                 retry_after=int(ra) if ra.isdigit() else 900, raw=data)
        if r.status_code >= 500:
            ra = r.headers.get("retry-after", "")
            raise RetryableError(label, code=code, retry_after=int(ra) if ra.isdigit() else 120, raw=data)
        if data.get("error") == "invalid_grant":
            raise AuthError(label, code="invalid_grant", raw=data)
        raise PermanentError(label, code=code, raw=data)

    def _req(self, method: str, url: str, token: str, **kw) -> httpx.Response:
        headers = self._headers(token, kw.pop("headers", None))
        try:
            r = self.http.request(method, url, headers=headers, **kw)
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error talking to LinkedIn: {e}") from e
        return self._handle(r)

    def _json(self, method: str, url: str, token: str, **kw) -> dict:
        r = self._req(method, url, token, **kw)
        try:
            return r.json() if r.content else {}
        except ValueError:
            return {}

    # ------------------------------------------------------------------ auth
    def authorize_url(self, state: str, redirect_uri: str, code_verifier: str = "") -> str:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        cid, _, scopes = self._client()
        q = {"response_type": "code", "client_id": cid, "redirect_uri": redirect_uri, "state": state, "scope": scopes}
        return f"{AUTH_URL}?{urlencode(q)}"

    def _token_call(self, form: dict) -> TokenBundle:
        cid, secret, scopes = self._client()
        try:
            r = self.http.post(TOKEN_URL, data={**form, "client_id": cid, "client_secret": secret},
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r).json()
        now = _utcnow()
        return TokenBundle(
            access_token=d["access_token"], refresh_token=d.get("refresh_token"),
            expires_at=now + timedelta(seconds=int(d.get("expires_in", 60 * 86400))),
            refresh_expires_at=(now + timedelta(seconds=int(d["refresh_token_expires_in"]))
                                if d.get("refresh_token_expires_in") else None),
            scopes=(d.get("scope") or scopes).replace(",", " "))

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = "") -> tuple[TokenBundle, Profile]:
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        bundle = self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
        return bundle, self._profile(bundle.access_token)

    def connect_with_token(self, access_token: str, extra: dict | None = None) -> tuple[TokenBundle, Profile]:
        """Paste a token made in LinkedIn's token generator (developer portal); assumed to last 60 days."""
        prof = self._profile(access_token)
        return TokenBundle(access_token=access_token, expires_at=_utcnow() + timedelta(days=60),
                           scopes=self._client()[2]), prof

    def _profile(self, token: str) -> Profile:
        try:
            r = self.http.get(USERINFO_URL, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error: {e}") from e
        d = self._handle(r).json()
        sub = str(d.get("sub") or "")
        if not sub:
            raise PermanentError("LinkedIn did not return your member id (is the 'openid' scope on?)")
        name = d.get("name") or " ".join(x for x in (d.get("given_name"), d.get("family_name")) if x)
        return Profile(external_id=sub, username=(d.get("email") or name or sub), display_name=name or sub,
                       avatar_url=d.get("picture") or "",
                       meta={"author_urn": f"urn:li:person:{sub}", "account_type": "personal",
                             "email": d.get("email")})

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return self._profile(auth.access_token)

    def expand_profiles(self, bundle: TokenBundle, profile: Profile) -> list[Profile]:
        """Every channel this login should create (a personal login is one channel)."""
        return [profile]

    def needs_refresh(self, auth: ChannelAuth, now: datetime) -> bool:
        return bool(auth.refresh_token and auth.token_expires_at
                    and auth.token_expires_at - now <= timedelta(days=EXPIRY_WARNING_DAYS))

    def refresh(self, auth: ChannelAuth) -> TokenBundle | None:
        if not auth.refresh_token:
            return None   # personal tokens: no refresh, the worker warns before expiry
        b = self._token_call({"grant_type": "refresh_token", "refresh_token": auth.refresh_token})
        b.refresh_token = b.refresh_token or auth.refresh_token
        return b

    def author(self, auth: ChannelAuth) -> str:
        return (auth.meta or {}).get("author_urn") or f"urn:li:{self.author_kind}:{auth.external_id}"

    # ------------------------------------------------------------------ composing
    def formats(self) -> list[list[str]]:
        return FORMATS

    def option_schema(self) -> list[dict]:
        return [
            {"key": "li_format", "label": "Post type", "type": "select", "default": "auto", "choices": FORMATS,
             "help": "Auto picks from your media: 1 image, 2-20 images, a PDF, a video, or a link preview."},
            {"key": "visibility", "label": "Who can see it", "type": "select", "default": "PUBLIC",
             "choices": [["PUBLIC", "Anyone"], ["CONNECTIONS", "Connections only"]]},
            {"key": "document_title", "label": "Document title", "type": "text", "default": "",
             "show_if": {"li_format": ["document", "auto"]}, "help": "Shown on the PDF carousel (defaults to the file name)."},
            {"key": "alt_text", "label": "Alt text", "type": "textarea", "default": "",
             "help": "Used for images without their own alt text."},
            {"key": "link", "label": "Link for the preview", "type": "text", "default": "",
             "help": "Defaults to the first link in the text."},
            {"key": "link_preview", "label": "Build a link preview", "type": "toggle", "default": True,
             "help": "Reads the page's title, description and image, as LinkedIn's API does not."},
            {"key": "first_comment", "label": "First comment", "type": "textarea", "default": "",
             "help": "Posted under the post right after it goes out (good for links)."},
        ]

    @staticmethod
    def _split_media(media: list[MediaItem]) -> tuple[list, list, list]:
        imgs = [m for m in media if m.kind == "image"]
        vids = [m for m in media if m.kind == "video"]
        docs = [m for m in media if m.kind == "document" or m.mime == "application/pdf"]
        return imgs, vids, docs

    def resolve_format(self, caption: str, media: list[MediaItem], options: dict) -> str:
        fmt = (options or {}).get("li_format") or "auto"
        if fmt != "auto":
            return fmt
        imgs, vids, docs = self._split_media(media)
        if docs:
            return "document"
        if vids:
            return "video"
        if len(imgs) >= 2:
            return "multi_image"
        if imgs:
            return "image"
        if (options or {}).get("link_preview", True) and ((options or {}).get("link") or first_url(caption)):
            return "article"
        return "text"

    def suggest_format(self, caption: str, media: list[MediaItem]) -> tuple[str, str]:
        f = self.resolve_format(caption, media, {})
        reasons = {"document": "a PDF becomes a swipeable document carousel", "video": "one video",
                   "multi_image": "2+ images post as a multi-image post", "image": "one image",
                   "article": "the text has a link, so it gets a preview card", "text": "text only"}
        return f, reasons.get(f, "")

    def validate(self, caption: str, media: list[MediaItem], options: dict, rules: BrandRules) -> list[Issue]:
        issues: list[Issue] = []
        options = options or {}
        caption = caption or ""
        if len(caption) > TEXT_LIMIT:
            issues.append(Issue("error", "caption_too_long", f"LinkedIn posts max {TEXT_LIMIT} characters ({len(caption)} now).", "caption"))
        imgs, vids, docs = self._split_media(media)
        fmt = self.resolve_format(caption, media, options)
        if not caption.strip() and not media:
            issues.append(Issue("error", "empty", "A LinkedIn post needs text or media.", "caption"))
        if len(vids) > 1:
            issues.append(Issue("error", "li_one_video", "LinkedIn takes one video per post.", "media"))
        if len(docs) > 1:
            issues.append(Issue("error", "li_one_document", "LinkedIn takes one PDF document per post.", "media"))
        if (vids or docs) and (imgs or (vids and docs)):
            issues.append(Issue("error", "li_mix", "A video or PDF can't be mixed with other media on LinkedIn.", "media"))
        if len(imgs) > MAX_IMAGES:
            issues.append(Issue("error", "li_max_images", f"LinkedIn allows at most {MAX_IMAGES} images per post.", "media"))
        need = {"image": (len(imgs) == 1 and not vids and not docs, "An image post needs exactly one image."),
                "multi_image": (2 <= len(imgs) <= MAX_IMAGES and not vids and not docs, "A multi-image post needs 2-20 images and no video."),
                "document": (len(docs) == 1 and not imgs and not vids, "A document post needs exactly one PDF."),
                "video": (len(vids) == 1 and not imgs and not docs, "A video post needs exactly one video."),
                "text": (not media, "A text post can't have media - pick another post type."),
                "article": (not media and bool(options.get("link") or first_url(caption)),
                            "A link preview needs a link (in the text or the Link field) and no media.")}
        ok, msg = need.get(fmt, (True, ""))
        if not ok:
            issues.append(Issue("error", "li_format", msg, "options"))
        for m in imgs:
            if m.size > IMAGE_MAX_BYTES:
                issues.append(Issue("error", "image_too_big", f"{m.filename} is over 10 MB.", "media"))
        for m in docs:
            if m.size > DOC_MAX_BYTES:
                issues.append(Issue("error", "document_too_big", f"{m.filename} is over 100 MB (LinkedIn's document limit).", "media"))
            if not options.get("document_title"):
                issues.append(Issue("info", "document_title", f"Document title will be '{m.filename}'.", "options"))
        for m in vids:
            if m.size > VIDEO_MAX_BYTES:
                issues.append(Issue("error", "video_too_big", f"{m.filename} is over 500 MB (LinkedIn's API limit).", "media"))
            if m.duration and not (VIDEO_MIN_S <= m.duration <= VIDEO_MAX_S):
                issues.append(Issue("error", "video_length", f"LinkedIn videos must be 3 s to 30 min ({m.duration:.0f}s).", "media"))
        if len(options.get("first_comment") or "") > COMMENT_LIMIT:
            issues.append(Issue("error", "first_comment_too_long", f"First comment max {COMMENT_LIMIT} characters.", "options"))
        if (options.get("visibility") or "PUBLIC") not in ("PUBLIC", "CONNECTIONS"):
            issues.append(Issue("error", "li_visibility", "Visibility must be PUBLIC or CONNECTIONS.", "options"))
        if self.author_kind == "organization" and options.get("visibility") == "CONNECTIONS":
            issues.append(Issue("error", "li_visibility", "Company pages can only post publicly.", "options"))
        tags = count_hashtags(caption)
        if tags > rules.hashtag_limit:
            issues.append(Issue("warning", "brand_hashtags", f"{tags} hashtags - 3-5 work best on LinkedIn.", "caption"))
        return issues

    # ------------------------------------------------------------------ uploads
    def _init_upload(self, tok: str, kind: str, owner: str, extra: dict | None = None) -> dict:
        d = self._json("POST", f"{REST}/{kind}?action=initializeUpload", tok,
                       json={"initializeUploadRequest": {"owner": owner, **(extra or {})}})
        return d.get("value") or {}

    def _put(self, url: str, tok: str, data: bytes, mime: str = "application/octet-stream") -> httpx.Response:
        try:
            r = self.http.put(url, content=data, headers={"Authorization": f"Bearer {tok}", "Content-Type": mime})
        except httpx.HTTPError as e:
            raise RetryableError(f"Network error uploading to LinkedIn: {e}") from e
        return self._handle(r)

    def upload_image(self, tok: str, owner: str, data: bytes, mime: str = "image/jpeg") -> str:
        v = self._init_upload(tok, "images", owner)
        if not v.get("uploadUrl") or not v.get("image"):
            raise RetryableError("LinkedIn did not return an image upload URL")
        self._put(v["uploadUrl"], tok, data, mime)
        return v["image"]

    def upload_document(self, tok: str, owner: str, data: bytes) -> str:
        v = self._init_upload(tok, "documents", owner)
        if not v.get("uploadUrl") or not v.get("document"):
            raise RetryableError("LinkedIn did not return a document upload URL")
        self._put(v["uploadUrl"], tok, data, "application/pdf")
        return v["document"]

    def upload_video(self, tok: str, owner: str, data: bytes) -> str:
        v = self._init_upload(tok, "videos", owner, {"fileSizeBytes": len(data), "uploadCaptions": False,
                                                     "uploadThumbnail": False})
        urn, instructions = v.get("video"), v.get("uploadInstructions") or []
        if not urn or not instructions:
            raise RetryableError("LinkedIn did not return video upload instructions")
        etags = []
        for ins in instructions:   # upload exactly the byte ranges LinkedIn asks for
            first, last = int(ins.get("firstByte", 0)), int(ins.get("lastByte", len(data) - 1))
            r = self._put(ins["uploadUrl"], tok, data[first:last + 1])
            etag = r.headers.get("etag", "").strip('"')
            if not etag:
                raise RetryableError("LinkedIn did not return an ETag for a video part")
            etags.append(etag)
        self._req("POST", f"{REST}/videos?action=finalizeUpload", tok,
                  json={"finalizeUploadRequest": {"video": urn, "uploadToken": v.get("uploadToken", ""),
                                                  "uploadedPartIds": etags}})
        return urn

    def video_status(self, tok: str, urn: str) -> str:
        return (self._json("GET", f"{REST}/videos/{quote(urn, safe='')}", tok).get("status") or "PROCESSING").upper()

    def read_preview(self, url: str) -> dict:
        """og:title / og:description / og:image of a page (best effort)."""
        try:
            r = self.http.get(url, headers={"User-Agent": "Mozilla/5.0 (HuhQueue link preview)"}, follow_redirects=True)
            page = r.text[:400_000] if r.status_code < 400 else ""
        except httpx.HTTPError:
            page = ""

        def meta(*names):
            for n in names:
                m = (re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(n)}["\'][^>]*content=["\']([^"\']*)', page, re.I)
                     or re.search(rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{re.escape(n)}["\']', page, re.I))
                if m and m.group(1).strip():
                    return html.unescape(m.group(1).strip())
            return ""
        title = meta("og:title", "twitter:title")
        if not title:
            t = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            title = html.unescape(t.group(1).strip()) if t else ""
        img = meta("og:image", "twitter:image")
        return {"title": title[:200] or url, "description": meta("og:description", "description", "twitter:description")[:300],
                "image": urljoin(url, img) if img else ""}

    # ------------------------------------------------------------------ publishing
    def _post_body(self, ctx: PublishContext, author: str) -> dict:
        opts = ctx.options or {}
        return {"author": author, "commentary": escape_commentary(ctx.caption or ""),
                "visibility": opts.get("visibility") or "PUBLIC",
                "distribution": {"feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": []},
                "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False}

    def publish(self, ctx: PublishContext) -> PublishOutcome:
        tok = ctx.channel.access_token
        if not tok:
            raise AuthError("LinkedIn channel has no access token - reconnect it.")
        st, opts = ctx.state, ctx.options or {}
        if st.get("post_urn") or st.get("uploaded"):
            return self.poll(ctx)   # resume: never upload or post twice
        author = self.author(ctx.channel)
        fmt = self.resolve_format(ctx.caption, ctx.media, opts)
        st["format"], st["warnings"] = fmt, []
        imgs, vids, docs = self._split_media(ctx.media)
        if fmt in ("image", "multi_image"):
            st["images"] = [{"id": self.upload_image(tok, author, m.path.read_bytes(), m.mime),
                             "altText": (m.alt_text or opts.get("alt_text") or "")[:4086]} for m in imgs]
        elif fmt == "document":
            d = docs[0]
            st["document"] = {"id": self.upload_document(tok, author, d.path.read_bytes()),
                              "title": (opts.get("document_title") or d.filename)[:400]}
        elif fmt == "video":
            st["video"] = self.upload_video(tok, author, vids[0].path.read_bytes())
        elif fmt == "article":
            url = opts.get("link") or first_url(ctx.caption)
            pv = self.read_preview(url)
            art = {"source": url, "title": pv["title"]}
            if pv["description"]:
                art["description"] = pv["description"]
            if pv["image"]:
                try:
                    r = self.http.get(pv["image"], follow_redirects=True)
                    if r.status_code < 400 and r.content:
                        art["thumbnail"] = self.upload_image(tok, author, r.content,
                                                             r.headers.get("content-type", "image/jpeg").split(";")[0])
                except (httpx.HTTPError, RetryableError, PermanentError):
                    st["warnings"].append("Link preview image could not be uploaded - posted without it.")
            st["article"] = art
        st["uploaded"] = True
        return self.poll(ctx)

    def poll(self, ctx: PublishContext) -> PublishOutcome:
        tok, st, opts = ctx.channel.access_token, ctx.state, ctx.options or {}
        if not st.get("post_urn"):
            body = self._post_body(ctx, self.author(ctx.channel))
            fmt = st.get("format", "text")
            if fmt == "video":
                status = self.video_status(tok, st["video"])
                if status in ("PROCESSING_FAILED", "FAILED"):
                    raise PermanentError("LinkedIn could not process the video")
                if status != "AVAILABLE":
                    return PublishOutcome("processing", state=st, message="LinkedIn is processing the video…",
                                          poll_after_seconds=15)
                body["content"] = {"media": {"id": st["video"], "title": (opts.get("video_title") or "")[:200]}}
            elif fmt == "image":
                body["content"] = {"media": st["images"][0]}
            elif fmt == "multi_image":
                body["content"] = {"multiImage": {"images": st["images"]}}
            elif fmt == "document":
                body["content"] = {"media": st["document"]}
            elif fmt == "article":
                body["content"] = {"article": st["article"]}
            if st.get("publish_called"):   # an earlier attempt sent the post but never saw the reply
                raise Unconfirmed("LinkedIn may already have this post (the reply to the last attempt was lost) - "
                                  "check the feed and mark it posted or send it again.")
            st["publish_called"] = True
            ctx.checkpoint()
            try:
                r = self._req("POST", f"{REST}/posts", tok, json=body)
            except ConnectorError as e:
                if nothing_sent(e):
                    st.pop("publish_called", None)
                raise
            urn = r.headers.get("x-restli-id") or r.headers.get("x-linkedin-id") or ""
            if not urn:
                try:
                    urn = (r.json() or {}).get("id", "")
                except ValueError:
                    urn = ""
            if not urn:
                raise PermanentError("LinkedIn accepted the post but returned no id")
            st["post_urn"] = urn
        urn = st["post_urn"]
        warnings = list(st.get("warnings") or [])
        fc = (opts.get("first_comment") or "").strip()
        if fc and not st.get("first_comment_id"):
            try:
                d = self._req("POST", f"{REST}/socialActions/{quote(urn, safe='')}/comments", tok,
                              json={"actor": self.author(ctx.channel), "object": urn, "message": {"text": fc}})
                st["first_comment_id"] = d.headers.get("x-restli-id") or "posted"
            except (PermanentError, RetryableError, AuthError) as e:
                warnings.append(f"Posted, but the first comment failed: {e.message}")
        return PublishOutcome("published", external_id=urn, permalink=f"https://www.linkedin.com/feed/update/{urn}/",
                              state=st, warnings=warnings, message="Published to LinkedIn")

    def post_comment(self, auth: ChannelAuth, media_external_id: str, text: str) -> str:
        r = self._req("POST", f"{REST}/socialActions/{quote(media_external_id, safe='')}/comments", auth.access_token,
                      json={"actor": self.author(auth), "object": media_external_id, "message": {"text": text}})
        return r.headers.get("x-restli-id") or ""


class LinkedInPagesConnector(LinkedInConnector):
    """Company pages through a separate Community Management API app (off until LINKEDIN_PAGES_ENABLED=1)."""
    id = "linkedin_page"
    name = "LinkedIn Page"
    author_kind = "organization"
    capabilities = {**LinkedInConnector.capabilities, "insights": True, "manual_token": False,
                    "comments_note": "Page comments come with the Community Management API."}

    def _client(self) -> tuple[str, str, str]:
        s = self.settings
        return s.linkedin_pages_client_id, s.linkedin_pages_client_secret, s.linkedin_pages_scopes

    def is_configured(self) -> bool:
        return bool(getattr(self.settings, "linkedin_pages_enabled", False)) and super().is_configured()

    def setup_hint(self) -> str:
        return ("Company pages need a second LinkedIn app with ONLY the Community Management API product (LinkedIn "
                "approves it for registered companies). Set LINKEDIN_PAGES_ENABLED=1 and paste that app's keys "
                "(LINKEDIN_PAGES_CLIENT_ID / LINKEDIN_PAGES_CLIENT_SECRET). See SETUP.md.")

    def connect_with_token(self, access_token: str, extra: dict | None = None):
        raise PermanentError("Connect LinkedIn pages with the Connect button")

    def list_pages(self, token: str) -> list[Profile]:
        d = self._json("GET", f"{REST}/organizationAcls", token, params={"q": "roleAssignee", "state": "APPROVED"})
        out = []
        for el in d.get("elements") or []:
            if el.get("role") not in ("ADMINISTRATOR", "CONTENT_ADMINISTRATOR"):
                continue
            org = str(el.get("organization") or el.get("organizationTarget") or "")
            oid = org.rsplit(":", 1)[-1]
            if not oid:
                continue
            try:
                o = self._json("GET", f"{REST}/organizations/{oid}", token)
            except PermanentError:
                o = {}
            name = o.get("localizedName") or oid
            out.append(Profile(external_id=oid, username=o.get("vanityName") or name, display_name=name,
                               meta={"author_urn": f"urn:li:organization:{oid}", "account_type": "page",
                                     "role": el.get("role")}))
        return out

    def exchange_code(self, code: str, redirect_uri: str, code_verifier: str = ""):
        if not self.is_configured():
            raise NotConfigured(self.setup_hint())
        bundle = self._token_call({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
        pages = self.list_pages(bundle.access_token)
        if not pages:
            raise PermanentError("This LinkedIn account doesn't administer any company page")
        first = pages[0]
        first.meta = {**first.meta, "_pages": pages}
        return bundle, first

    def expand_profiles(self, bundle, profile):
        pages = (profile.meta or {}).pop("_pages", None) or [profile]
        return pages

    def fetch_profile(self, auth: ChannelAuth) -> Profile | None:
        return None

    def fetch_insights(self, ctx: PublishContext, external_id: str) -> dict:
        org = self.author(ctx.channel)
        key = "ugcPosts" if ":ugcPost:" in external_id else "shares"
        d = self._json("GET", f"{REST}/organizationalEntityShareStatistics", ctx.channel.access_token,
                       params={"q": "organizationalEntity", "organizationalEntity": org, f"{key}[0]": external_id})
        tot = ((d.get("elements") or [{}])[0].get("totalShareStatistics") or {})
        out = {"views": tot.get("impressionCount"), "likes": tot.get("likeCount"), "comments": tot.get("commentCount"),
               "shares": tot.get("shareCount"), "clicks": tot.get("clickCount")}
        if tot.get("engagement") is not None:
            out["engagement_rate"] = round(100.0 * float(tot["engagement"]), 2)
        return out
