# HuhQueue connectors: free-tier research (as of 22 Oct 2026)

Research only. Nothing here was committed. Sources were fetched on 22 Oct 2026, and URLs are listed inline and at the end.
**Priority (Ram):** get Instagram working end to end first, then X. Other networks are covered briefly.

---

## 0. TL;DR

| Network | Developer account / app | Cost | Review needed for own account? | Scopes (publish / read / comments / insights) | Notes |
|---|---|---|---|---|---|
| **Instagram (Instagram Login)** | Meta developer app (Business type), Instagram product, "API setup with Instagram login" | Free | **No.** Standard Access covers accounts you own and have added to the app. App Review + Business Verification are only for Advanced Access | `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_insights` | No FB Page needed. 60-day token, refreshable. 100 API posts/24h. Media must be on a public URL |
| Instagram (Facebook Login, Page-linked) | Same Meta app, "API setup with Facebook login" + Facebook Login for Business | Free | No (Standard Access) | `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`, `pages_show_list`, `pages_read_engagement` (+`business_management` optional) | Needs IG linked to a FB Page. Long-lived **Page token never expires**. Adds hashtag search, product tags, `total_*` metrics, story_insights webhook, and resumable upload |
| Facebook Pages | Same Meta app | Free | Not for own Page, but Dev-mode posts are visible only to app roles (see §3) | `pages_show_list`, `pages_manage_posts`, `pages_read_engagement`, `pages_manage_engagement`, `read_insights` (+`pages_read_user_content`, `pages_manage_metadata` for comments/webhooks) | To make posts public, the app has to be "Live"/Published. Meta says publishing needs a Business-Verified business connected (verify this) |
| Threads | Meta app with the "Access the Threads API" use case (own Threads App ID/secret) | Free | No. Add yourself as a Threads Tester | `threads_basic`, `threads_content_publish`, `threads_read_replies`, `threads_manage_replies`, `threads_manage_insights` | 250 API posts/24h |
| YouTube | Google Cloud project, YouTube Data API v3, OAuth client (Web) | Free | No, but in "Testing" status, refresh tokens expire after **7 days** | `youtube.upload`, `youtube` / `youtube.readonly`, `youtube.force-ssl` (comments), plus YouTube Analytics API `yt-analytics.readonly` | Default quota: 100 `videos.insert`/day + 10,000 units/day for everything else |
| **X** | X developer account on console.x.com, **Pay-per-use** | **Paid.** No free tier for new developers since 6 Feb 2026 | No | OAuth 2.0: `tweet.write`, `tweet.read`, `users.read`, `offline.access` (+`media.write`), or OAuth 1.0a Read+Write | $0.015 per post, **$0.20 per post with a URL**, owned reads $0.001. One-time $20 credit after first purchase + up to $50 auto-recharge match (expire after 3 months) |
| TikTok | TikTok for Developers app, Login Kit + Content Posting API | Free | **Audit needed for public Direct Post.** Unaudited apps can only post SELF_ONLY (private), from accounts set to private. TikTok refuses audits for private/internal tools | `user.info.basic`, `video.upload` (inbox/draft), `video.publish` (direct), `video.list`, `user.info.profile`, `user.info.stats` | Free and practical route: **upload to inbox (draft)**, then Ram taps Post in the app |
| LinkedIn (personal) | LinkedIn developer app (needs a LinkedIn Company Page), products "Share on LinkedIn" + "Sign In with OpenID Connect" | Free | No (self-serve) | `w_member_social`, `openid`, `profile` | 60-day tokens. Refresh tokens are for approved MDP partners only, so re-auth every 60 days. Org pages need Community Management API (registered legal entity) |
| Bluesky | None. App password (or atproto OAuth) | Free | No | n/a | 5,000 write points/hr (CREATE = 3). createSession is limited to 30 per 5 min and 300/day, so cache sessions |
| Mastodon | Register an app per instance via `POST /api/v1/apps` (no portal) | Free | No | `write:statuses write:media profile` (+`read:statuses`, `read:notifications` for history/replies) | One app per instance |

**Recommended order (zero cost, single user):** 1) Instagram via Instagram Login (already coded) → 2) X (costs cents; only if no-link posts are acceptable) → 3) YouTube → 4) Facebook Page (same Meta app) → 5) TikTok inbox/draft → 6) Bluesky / Mastodon (easy wins) → 7) Threads → 8) LinkedIn.

---

## 1. Instagram: deep dive

### 1.1 Two free routes compared (official: Instagram Platform overview)

| | **Instagram API with Instagram Login** ("Business Login for Instagram") | **Instagram API with Facebook Login** (Facebook Login for Business, Page-linked) |
|---|---|---|
| User logs in with | Instagram credentials | Facebook credentials |
| FB Page required | **No** | **Yes.** IG pro account must be linked to a Page, and the user needs admin-equivalent tasks on it |
| Host | `graph.instagram.com` | `graph.facebook.com` |
| Token | Instagram User token: short-lived 1h → long-lived **60 days**, refresh via `ig_refresh_token` once the token is ≥24h old (each refresh gives another 60 days) | FB User token: short → long-lived (~60 days). From that, a **long-lived Page token with no expiry date** (only invalidated under certain conditions) |
| App ID used | **Instagram App ID / secret** (shown under Instagram > API setup with Instagram login) | Meta App ID / secret (top of dashboard) |
| Permissions | `instagram_business_basic`, `_content_publish`, `_manage_comments`, `_manage_insights`, `_manage_messages` | `instagram_basic`, `instagram_content_publish`, `instagram_manage_comments`, `instagram_manage_insights`, `instagram_manage_messages`, `pages_show_list`, `pages_read_engagement` (+`ads_management`/`ads_read` if the Page role came via Business Manager) |
| Publish image / carousel (≤20) / video / **Reels** / **Stories** | Yes (all) | Yes (all) |
| Resumable video upload (`rupload.facebook.com`) | Content-publishing doc says resumable sessions are "Only for apps that have implemented Facebook Login for Business". Use `video_url` on a public URL | Yes |
| Read past media (`/{ig-id}/media`) | Yes | Yes |
| Comments: list / reply / hide / delete / disable | Yes | Yes (+`/ordered_comments` for ads) |
| Insights (media + account) | **Yes** (now supported for Instagram Login) | Yes, plus `total_likes/total_comments/total_views` (include ads) and the `story_insights` webhook |
| Hashtag search, product tagging, partnership ads, collaborators data | No | Yes |
| Messaging | Yes | Via Messenger Platform |

**Verdict for HuhQueue / huhbyte.ai:** stay on **Instagram Login**. It needs no Page or Business portfolio, and it is the route HuhQueue already implements (`huhqueue/connectors/instagram.py`). It covers publishing (feed, carousel, reels, stories), past media, comments and insights. The only operational cost is the 60-day token, which HuhQueue already refreshes within 7 days of expiry. Add the Facebook Login route later only if Ram wants a never-expiring token, hashtag search, or resumable uploads.

### 1.2 What works in Development mode / Standard Access (own account)

Official Instagram Platform overview: *"Standard Access is the default access level for all apps … intended for apps that will only be used by people who have roles on them … **If your app only serves your Instagram professional account or an account you manage, Standard Access is all your app needs.**"*
*"Advanced Access … requires App Review and Business Verification"*. It is only needed for accounts you don't own or manage, used by people without a role on the app.

So with huhbyte.ai added as an **Instagram Tester** (or with Ram's FB account as admin/developer on the app):
- **Publishing:** image (JPEG only), video, `REELS`, `STORIES`, `CAROUSEL` (up to 20 children; carousel counts as one post). Trial reels via `trial_params`. AI label via `is_ai_generated=true`. Limit: **100 API-published posts per rolling 24h** (`GET /{ig-id}/content_publishing_limit`). The carousel section of the same doc still says "50 published posts", which is a doc inconsistency. Enforce ≤50/day in-app to be safe; Ram posts 1–2/day anyway.
- **Media must be publicly reachable:** "We cURL media used in publishing attempts, so the media must be hosted on a publicly accessible server." HuhQueue's public edge app (`/m/...` on `HUHQ_PUBLIC_BASE_URL`) covers this, so the InstaBox URL must be public HTTPS.
- **Container flow:** `POST /{ig-id}/media` → poll `GET /{container}?fields=status_code` (FINISHED / IN_PROGRESS / ERROR / EXPIRED after 24h; Meta suggests polling once per minute for up to 5 min) → `POST /{ig-id}/media_publish`.
- **Reading past media:** `GET /{ig-id}/media` with fields `id,caption,media_type,media_product_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count` (paging cursors). Use `media_product_type` to tell REELS/STORY apart (`media_type` returns VIDEO/IMAGE).
- **Comments:** `GET /{media}/comments`, `GET|POST /{comment}/replies`, hide/unhide `POST /{comment}`, `DELETE /{comment}`, enable/disable on media. Webhooks `comments`, `live_comments`.
- **Insights:** `GET /{media}/insights` and `GET /{ig-id}/insights`. Use `views`, `reach`, `likes`, `comments`, `shares`, `saved`, `total_interactions` (stories: `replies`, `navigation` etc.). **`impressions` is deprecated for media created after 2 July 2024.** HuhQueue already requests `views`, which is correct. Some account metrics are unavailable under 100 followers. Data can lag up to 48h.
- **Token lifetime:** dashboard-generated tokens are long-lived (60 days). OAuth gives 1h, which is exchanged for 60 days (`grant_type=ig_exchange_token`), then refreshed (`grant_type=ig_refresh_token`, token must be ≥24h old).
- **Business Verification / App Review:** **not required** for own-account use. Meta's create-app guide says a Business-Verified business must be connected "before you can publish your app" (switch it Live). For Instagram Login on your own account, you don't need to go Live: Standard Access works in Development mode.
- *Unverified:* whether posts published in Development mode are visible to the public on Instagram. Facebook Page posts from Dev-mode apps are role-only. I found no equivalent statement for Instagram. Confirm in the first live test by opening the permalink in a logged-out browser.
- The create-app guide's "Generate access tokens" step says the account added there "must be public". Make sure huhbyte.ai is not set to private.

### 1.3 Meta dashboard click path (Instagram Login route)

Ram already has a Meta developer account.

1. **developers.facebook.com/apps → Create app.** (If reusing an existing app, it must be a **Business**-type app. The Instagram product can't be added to Consumer apps. If the existing app isn't Business type, create a new one.)
2. **Connect a business:** optional at this stage. Choose "I don't want to connect a business portfolio yet" if offered. It is only needed to publish/go Live.
3. **Use case:**
   - Meta's Instagram guide (classic flow): choose **Other → Next → app type Business → Next**.
   - Newer use-case flow : filter **Content management** and select **"Manage messaging & content on Instagram"** (add **"Manage everything on your Page"** too if Facebook Pages will live in the same app).
4. **App details:** name (e.g. "HuhQueue"), contact email → Create app.
5. **Add product: Instagram → Set up.** "API setup with Instagram login" is added automatically. (Newer flow: Use cases → *Manage messaging & content on Instagram* → **Customize** → "Add all required permissions", then add `instagram_business_content_publish`, `instagram_business_manage_comments`, `instagram_business_manage_insights`.)
6. **Instagram > API setup with Instagram login:** copy the **Instagram App ID** and **Instagram App Secret** into HuhQueue as `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`. These are *not* the Meta App ID/secret at the top of the dashboard.
7. **Add the tester:** **App roles → Roles → Add people → "Instagram Tester"** → type `huhbyte.ai` → Add.
   **Accept the invite:** log in as huhbyte.ai at instagram.com → **Settings → Apps and websites → Tester invites** (direct link: https://www.instagram.com/accounts/manage_access/) → Accept. If the invite is missing, the "Insufficient developer role" error appears at login.
   (Alternative: in step 2 "Generate access tokens", click **Add account** and log in as huhbyte.ai, which also gives a 60-day token you can paste via HuhQueue's connect-with-token path.)
8. **"3. Set up Instagram business login" → Set up:** add the **Redirect URL** and Save. Then **Business login settings**:
   - **OAuth redirect URIs:** `https://<PUBLIC_BASE_URL>/oauth/instagram/callback`. This is HuhQueue's format (`core.redirect_uri()` = `{HUHQ_PUBLIC_BASE_URL}/oauth/{provider}/callback`). It must match exactly, and the dashboard may append a trailing slash, so check.
   - **Deauthorize callback URL:** e.g. `https://<PUBLIC_BASE_URL>/oauth/instagram/deauthorize`
   - **Data deletion request URL:** e.g. `https://<PUBLIC_BASE_URL>/oauth/instagram/data-deletion`. Meta posts a `signed_request`. The endpoint must start deletion and return JSON `{ "url": "<status url>", "confirmation_code": "<code>" }`. **HuhQueue does not implement these two endpoints yet.** Add small handlers, or point both at a page that exists. Meta's forms often refuse to save empty fields.
   - Save.
9. **App settings → Basic:** set App Domains (the InstaBox host) and a Privacy Policy URL (any public page works for Dev mode; it is required later for Live/App Review).
10. **Webhooks (optional, for real-time comments):** Instagram > API setup > Configure webhooks: callback URL + verify token. Only needed if HuhQueue moves from polling comments to webhooks.
11. **Do not submit App Review** for own-account use. Leave the app in Development mode.
12. In HuhQueue, click Connect Instagram → the Instagram consent screen (`https://www.instagram.com/oauth/authorize?client_id=…&redirect_uri=…&response_type=code&scope=instagram_business_basic,instagram_business_content_publish,instagram_business_manage_comments,instagram_business_manage_insights`) → callback → token exchange → long-lived token.

**First end-to-end test checklist:** connect → `GET /me?fields=user_id,username` → publish one JPEG (public URL) → check `content_publishing_limit` → list `/media` → read `/insights` on the new post → reply to a comment → confirm the post is publicly visible logged-out → after 24h, call `refresh_access_token` once to prove refresh.


**HuhQueue status (local clone, read-only check):** `huhqueue/connectors/instagram.py` uses Instagram Login, the same 4 scopes (`config.instagram_scopes`), Graph `v26.0`, long-lived exchange plus refresh, IMAGE/REELS/STORIES/CAROUSEL (max 20), container polling, `views`-based insights, `list_media`. Gaps: deauthorize and data-deletion endpoints; an in-app daily publish cap; JPEG conversion for non-JPEG images (Instagram only accepts JPEG).

---

## 2. X (next priority)

- **No free tier for new developers.** X launched Pay-Per-Use on **6 Feb 2026**. The old Free tier closed to new sign-ups ("Recently active Legacy Free tier users receive a one-time $10 voucher"). X docs now describe the API as pay-per-usage with no subscriptions. Public Utility Apps still get free access, case by case.
- **Write prices (docs.x.com pricing, fetched 22 Oct 2026):** Post create **$0.015**, Post create *with URL* **$0.200**, summoned reply $0.010, delete $0.010, media metadata $0.005.
  **Reads:** Posts $0.005 each, users $0.010. **Owned Reads** (your own app reading your own `/users/{id}/tweets`, mentions, followers…) cost **$0.001/resource**. 24h UTC dedup. Cap of 3M post reads/month.
- **Free credits:** one-time **$20** after your first eligible card *and first credit purchase* (adding a card alone stopped qualifying on 13 Oct 2026), plus a one-time match of the first auto-recharge **up to $50** (minimum auto-recharge $5). These credits **expire 3 months after grant** and are rolling out. Spending limits can be set per billing cycle.
- **Cost for Ram:** 2 text-only posts/day ≈ 60 × $0.015 = **$0.90/month**. 2 posts/day with links ≈ $12/month. Reading his own timeline for history at $0.001 each is negligible. So the realistic "free" plan is: buy the minimum credit, collect the $20 (and optionally the auto-recharge match), **strip links**, and set a spending limit. Going truly $0 is not possible.
- **Setup:** console.x.com → sign up for Pay-per-use → create App (Production) → User authentication settings: **Read and write**, type **Web App, Automated App or Bot** → Callback URL `https://<PUBLIC_BASE_URL>/oauth/x/callback` + website URL. OAuth 2.0 (PKCE) scopes: `tweet.read tweet.write users.read offline.access` (+`media.write` for v2 media upload).0a consumer keys (API Key/Secret) for media upload compatibility.
- No app review is needed.
- Sources: https://docs.x.com/x-api/getting-started/pricing , https://docs.x.com/x-api/getting-started/free-credits , https://docs.x.com/changelog

## 3. Other networks (brief)

- **Facebook Pages:** same Meta app as Instagram. Add the Page use case / Facebook Login for Business. Redirect `…/oauth/facebook/callback`. Scopes: `pages_show_list, pages_manage_posts, pages_read_engagement, pages_manage_engagement, read_insights, business_management`. Optional: `pages_read_user_content`, plus `pages_manage_metadata` for comment webhooks. For personal use, advanced permissions aren't required, **but in Development mode posts (and their images) are visible only to app roles. Set the app Live.** Meta's create-app guide says going Live requires a Business-Verified business connected to the app. This is the one real friction point for FB: test whether Ram's app can be switched Live without verification. Page token: long-lived, no expiry.
- **Threads:** Meta app with **"Access the Threads API"** use case. It has its own **Threads App ID/secret**. An existing app can be reused, or a dedicated one created. Use a dedicated app if the dashboard won't add the use case to the Business app. Redirect `…/oauth/threads/callback` (you must *click the dropdown suggestion* or the form won't save). Uninstall/Delete callback URLs are required fields. Scopes `threads_basic, threads_content_publish, threads_read_replies, threads_manage_replies, threads_manage_insights`. **Threads Tester** role → accept at threads.com → Settings → Website permissions → Invites. Then click **"Finish customization"**. Auth: `https://threads.com/oauth/authorize`, token at `graph.threads.com/oauth/access_token`. 250 API posts/24h. Free; no review for testers.
- **YouTube:** Google Cloud project → enable **YouTube Data API v3** (+ YouTube Analytics API for insights) → OAuth consent screen (External, add yourself as test user) → OAuth client "Web application" → redirect `…/oauth/youtube/callback`. Default quota: **100 `videos.insert` calls/day + 10,000 units/day** for other endpoints (resets at midnight PT). **Testing-status apps get refresh tokens that expire after 7 days** (Google OAuth docs). The usual fix for a single user is to switch the consent screen to "In production" without verification and click through the "unverified app" warning; check current Google rules for sensitive scopes before relying on this. The `videos.insert` doc currently says "Videos uploaded from unverified API projects are not restricted to private viewing mode." Brand-account channels may need the app trusted in Workspace admin.
- **TikTok:** developers.tiktok.com → app → Login Kit + Content Posting API → redirect must be **HTTPS** (`…/oauth/tiktok/callback`) → ToS + privacy URLs on a verified domain. Photo posts use PULL_FROM_URL, which needs a verified URL prefix. Unaudited Direct Post: **SELF_ONLY visibility only, max 5 users/24h, and the posting account must be private.** Audit guidelines reject "a utility tool to help upload contents to the account(s) you or your team manages", so Ram can't pass audit as a private tool. Sandbox: up to 10 test accounts, no Direct Post. **Zero-cost path: `video.upload` "upload to inbox" (draft). Ram gets a notification and publishes from the TikTok app within 24h.**
- **LinkedIn:** create an app (needs a LinkedIn Company Page to associate) → products **Share on LinkedIn** (`w_member_social`) + **Sign In with LinkedIn using OpenID Connect** (`openid profile email`). Redirect `…/oauth/linkedin/callback`. Access tokens last 60 days. Programmatic refresh tokens (365d) are for approved MDP partners only, so expect a manual reconnect every ~60 days. Reading your own posts/comments needs restricted scopes (`r_member_social`). Company-page posting needs the Community Management API (legal entity only).
- **Bluesky:** no developer app. Use service URL + handle + **app password** (Settings → Privacy and security → App passwords) and calls `createSession`. atproto OAuth is the alternative; it needs a generated private key and a public domain. Limits: 5,000 points/hr and 35,000/day (create = 3 points), so up to 1,666 creates/hr. `createSession` is limited to 30 per 5 min and 300/day per account, so **store and refresh the session JWTs**; don't log in per post. Blobs up to 50 MB. Free.
- **Mastodon:** per instance, `curl -X POST https://<instance>/api/v1/apps -F client_name=HuhQueue -F redirect_uris=https://<PUBLIC_BASE_URL>/oauth/mastodon/callback -F "scopes=write:statuses write:media profile"`. This returns client_id/secret. Free, no review.

## 4. Recommended zero-cost order for Ram

1. **Instagram (huhbyte.ai):** one Meta Business app, Instagram product, Instagram Login, Instagram Tester accepted, redirect set, no review. Prove the full loop (publish, list, insights, comment reply, refresh).
2. **X:** costs cents, not free. Buy the minimum credit to unlock the $20 bonus, set a spending limit, and post text-only (links cost $0.20 each).
3. **YouTube:** free. Watch the 7-day refresh-token expiry while in Testing.
4. **Facebook Page:** add to the same Meta app. Check whether the app can be switched Live, otherwise posts stay role-only.
5. **TikTok (huhbyte):** inbox/draft upload, with Ram publishing from the app.
6. **Bluesky / Mastodon:** trivial and free if Ram wants them.
7. **Threads:** free with the Threads Tester role.
8. **LinkedIn:** free, but needs a reconnect every 60 days.

**Same Meta app:** Instagram (both routes) + Facebook Pages in one Business-type app. Threads has its own App ID/secret under its own use case. Reusing an existing app is possible; if the dashboard blocks it, create a second app.

## 5. Open items to verify during testing
- Are IG posts published from a Development-mode app visible to the public? (Expected yes; not found stated in docs.)
- Can the Meta app be switched Live without Business Verification? (Matters for FB Page visibility only.)
- The publish limit text conflicts (100 vs 50 per 24h), so cap low in the app.
- Resumable IG uploads on the Instagram Login route (docs conflict). Use public `video_url`.

## Sources (fetched 22 Oct 2026)
- Instagram Platform overview: https://developers.facebook.com/docs/instagram-platform/overview
- Create a Meta app with Instagram: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/create-a-meta-app-with-instagram
- Business Login for Instagram: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- Get started (Instagram Login): https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/get-started
- Content publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing
- Insights: https://developers.facebook.com/docs/instagram-platform/insights ; media metrics: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
- Comment moderation: https://developers.facebook.com/docs/instagram-platform/comment-moderation
- Tokens: https://developers.facebook.com/docs/instagram-platform/reference/access_token , https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token , https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived
- Data deletion callback: https://developers.facebook.com/docs/development/create-an-app/app-dashboard/data-deletion-callback
- Threads: https://developers.facebook.com/docs/threads/get-started , https://developers.facebook.com/docs/threads/get-started/get-access-tokens-and-permissions , https://developers.facebook.com/docs/threads/overview
- X: https://docs.x.com/x-api/getting-started/pricing , https://docs.x.com/x-api/getting-started/free-credits , https://docs.x.com/changelog
- YouTube: https://developers.google.com/youtube/v3/determine_quota_cost , https://developers.google.com/youtube/v3/docs/videos/insert , https://developers.google.com/identity/protocols/oauth2
- TikTok: https://developers.tiktok.com/doc/content-sharing-guidelines
- LinkedIn: https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin , https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens
- Bluesky: https://docs.bsky.app/docs/advanced-guides/rate-limits
- Mastodon: https://docs.joinmastodon.org/methods/apps/
