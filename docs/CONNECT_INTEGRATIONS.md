# Connect your integrations

Plain-English steps for connecting every network HuhQueue supports. You do each one once.

How it works for every network:
- HuhQueue posts through **your own developer app** on each network. You create the app, copy its ID and secret, and paste them into HuhQueue.
- Paste keys in **Settings → App keys**, or in the form that opens when you click **Connect** on a network with no keys yet. Keys saved there win over the `.env` file.
- Every network asks for a **redirect URL**. Yours is `<your HuhQueue URL>/oauth/<network>/callback`. HuhQueue shows the exact URL next to each key form, so copy it from there. With no public URL configured it is the URL you opened HuhQueue on (e.g. `http://localhost:8787` locally), so you can add both a localhost and a hosted callback to the same developer app.
- Local install? Networks need a public https URL. Set **Settings → Public URL** (or `HUHQ_PUBLIC_BASE_URL`) first, for example with a Cloudflare tunnel.
- Nothing is posted while you connect. HuhQueue only reads your profile until you schedule a post.

Contents: [LinkedIn profile](#1-linkedin-personal-profile) · [LinkedIn company pages](#2-linkedin-company-pages) · [Instagram](#3-instagram-and-facebook-meta) · [YouTube](#4-youtube) · [X](#5-x-twitter) · [TikTok](#6-tiktok) · [Buffer CSV import](#7-import-posts-from-buffer-csv) · [Agents and the API](#8-agents-and-the-api)

---

## 1. LinkedIn (personal profile)

No LinkedIn review needed. A personal login lasts **60 days**. HuhQueue warns you 7 days before it expires. Reconnecting takes one click.

1. Go to <https://developer.linkedin.com/> → **My apps** → **Create app**.
2. Fill in the app name (e.g. "HuhQueue"). LinkedIn asks for a **company page** for the app itself. Any page you admin is fine, and you can create an empty one. Upload a logo and accept the terms.
3. Open the **Products** tab and request both of these (they're approved instantly):
   - **Sign In with LinkedIn using OpenID Connect**
   - **Share on LinkedIn**
4. Open the **Auth** tab → **Authorized redirect URLs for your app** → add `<your HuhQueue URL>/oauth/linkedin/callback` → **Update**.
5. On the same tab, check that the scopes include `openid`, `profile`, `email` and `w_member_social`.
6. Copy the **Client ID** and **Primary Client Secret**.
7. In HuhQueue, go to **Channels → Connect a channel → LinkedIn** (or **Settings → App keys → LinkedIn**), paste both and click **Save keys**.
   - Or use the `.env` file: `LINKEDIN_CLIENT_ID=...` and `LINKEDIN_CLIENT_SECRET=...`.
8. Click **Continue to LinkedIn** → sign in → **Allow**. Your profile now shows as a channel.
9. Test it safely:
   - Write a post and pick **Save as draft**. Nothing goes out.
   - Use **Preview** to check the post type (Auto picks text, image, multi-image, PDF document, video or link preview).
   - Schedule a real post only when you're happy with it.

Post types: text, one image, 2–20 images, a PDF document (swipeable carousel), one video (3 s – 30 min, up to 500 MB) and a link preview. A **first comment** can go under the post.

API version: LinkedIn retires old API versions about a year after release. HuhQueue sends `LINKEDIN_API_VERSION` (default `202605`). If LinkedIn emails you about a version sunset, change that one value in `.env`.

## 2. LinkedIn company pages

LinkedIn only gives page access to **registered companies**, through the *Community Management API*. That product must be the **only** product on its app, so pages use a second app.

1. Create a **second** app at <https://developer.linkedin.com/>, linked to your company page.
2. On **Products**, request **Community Management API** and fill in LinkedIn's form about your company. Approval can take days or weeks.
3. Once approved, go to the **Auth** tab and add the redirect URL `<your HuhQueue URL>/oauth/linkedin_page/callback`.
4. Check that the scopes include `r_organization_social`, `w_organization_social` and `rw_organization_admin`.
5. Turn pages on in `.env` and add that app's keys:
   ```
   LINKEDIN_PAGES_ENABLED=1
   LINKEDIN_PAGES_CLIENT_ID=...
   LINKEDIN_PAGES_CLIENT_SECRET=...
   ```
   You can also paste the keys in **Settings → App keys → LinkedIn Page**. Restart HuhQueue after changing `.env`.
6. Go to **Channels → Connect a channel → LinkedIn Page → Continue**. Every page where you are an **Administrator** or **Content administrator** becomes its own channel.
7. Test it with a draft first, as in step 9 of section 1. Page posts are always public.

## 3. Instagram and Facebook (Meta)

You need an Instagram **Business or Creator** account. A personal account can switch for free in the Instagram app: *Settings → Account type and tools*. HuhQueue uses **Instagram Login**, so no Facebook Page is needed.

1. Go to <https://developers.facebook.com/apps/> → **Create app**.
2. Use case: **Manage messaging & content on Instagram**. If asked for a type, pick **Business**.
3. In the app, go to **Instagram → API setup with Instagram login**.
4. Click **Add account** and log in with your Instagram account. While the app is in development mode, only accounts added here can connect. Testers can also be added under **App roles → Instagram testers**, and each tester accepts the invite in the Instagram app.
5. Go to **Business login settings → OAuth redirect URIs** → add `<your HuhQueue URL>/oauth/instagram/callback` → **Save**.
6. Copy the **Instagram app ID** and **Instagram app secret** from the top of that page. These are *not* the Facebook App ID on Basic settings.
7. In HuhQueue, go to **Channels → Instagram**, paste both and click **Save keys** → **Continue to Instagram** → **Allow**.
8. In **App settings → Basic**, add your privacy policy URL: `<your HuhQueue URL>/privacy`.
9. In **Instagram → App settings**, add the *Deauthorize* and *Data deletion* URLs shown in **Settings → App keys → Instagram**.
10. **Review:** you don't need App Review for your own account. You only need it if *other people's* accounts will connect to your app.

Permissions requested: `instagram_business_basic`, `instagram_business_content_publish`, `instagram_business_manage_comments` and `instagram_business_manage_insights`.

Facebook: Facebook Pages are not a HuhQueue channel yet. If you link a Page to the Instagram account in Meta, Instagram music and collaborators work; the plan for this is in [FBLOGIN_PLAN.md](FBLOGIN_PLAN.md).

## 4. YouTube

1. Go to <https://console.cloud.google.com/> and create a project (e.g. "HuhQueue").
2. Go to **APIs & Services → Library** and enable **YouTube Data API v3**.
3. Set up the **OAuth consent screen** (Google Auth Platform):
   - User type: **External**. Add the app name and your email.
   - Scopes: `youtube.upload`, `youtube.readonly` and `youtube.force-ssl`.
   - Under **Audience → Test users**, add the Google account that owns the channel.
4. Go to **Credentials → Create credentials → OAuth client ID → Web application**:
   - Under **Authorized redirect URIs**, add `<your HuhQueue URL>/oauth/youtube/callback`.
5. Copy the **Client ID** and **Client secret** → HuhQueue **Settings → App keys → YouTube** → **Save**.
6. Go to **Channels → YouTube → Continue to YouTube**, pick the channel and click **Allow**.

Until Google verifies the project, uploads stay **private**, and test-mode logins expire after 7 days. Set the consent screen to *In production*, or ask Google for verification.

## 5. X (Twitter)

1. Go to <https://developer.x.com/en/portal/dashboard> and create a Project and an App. The Free tier allows a small number of posts a month.
2. Go to **User authentication settings → Set up**:
   - Permissions: **Read and write**.
   - Type of app: **Web App, Automated App or Bot**.
   - Callback URI: `<your HuhQueue URL>/oauth/x/callback`. Website: your HuhQueue URL.
3. Go to **Keys and tokens** and copy the **OAuth 2.0 Client ID** and **Client Secret**. The secret is shown only once.
4. In HuhQueue, go to **Settings → App keys → X**, paste both and click **Save** → **Channels → X → Authorize app**.

Scopes: `tweet.read`, `tweet.write`, `users.read`, `media.write` and `offline.access`. HuhQueue refreshes the login automatically.

## 6. TikTok

1. Go to <https://developers.tiktok.com/> → **Manage apps → Connect an app**.
2. Add the products **Login Kit** and **Content Posting API**.
3. Add the redirect URI `<your HuhQueue URL>/oauth/tiktok/callback`. It must be https.
4. Add your Terms and Privacy URLs: `<your HuhQueue URL>/terms` and `<your HuhQueue URL>/privacy`.
5. Scopes: `user.info.basic`, `user.info.profile`, `user.info.stats`, `video.upload`, `video.publish` and `video.list`.
6. Copy the **Client key** and **Client secret** → HuhQueue **Settings → App keys → TikTok** → **Save** → **Channels → TikTok → Continue**.

Until TikTok audits your app, direct posts can only be **private**. The free route is **"Send to TikTok inbox"**: HuhQueue uploads a draft, and you tap Post in the TikTok app. TikTok usually refuses audits for private tools.

## 7. Import posts from Buffer (CSV)

Buffer's bulk-upload CSV imports unchanged.

1. Open a channel → **Publish** → **Bulk upload**. To import into several channels at once, use **Growth → Import** instead.
2. Click **Buffer template** to download it, or use a CSV exported from Buffer. The columns are `Text`, `Image URL`, `Tags` and `Posting Time` (`YYYY-MM-DD HH:mm`; leave it blank to use the next free queue slot).
3. LinkedIn channels have a **LinkedIn template** with extra columns: `Format` (text, image, multi_image, document, video, article), `Document URL`, `Document Title`, `Alt Text`, `Link`, `First Comment` and `Privacy` (public or connections).
4. Choose **Queue** or **Drafts**, then pick your file. Every row is checked before anything is created.
5. Rows with problems are listed with the reason. Rows over the 500-row limit are **flagged, not silently dropped** (Buffer drops them silently).
6. Click **Add N to Queue** (or **Drafts**).

Agents can do the same with `POST /api/import/csv/preview` and `POST /api/import/csv` (body: `csv`, `channel_ids`, `when=queue|draft`).

## 8. Agents and the API

- Create a token in **Settings → API & MCP agents**. Pick its scopes, allowed tools, rate limit, and whether its posts need your approval.
- Docs live at `/llms.txt`, `/openapi.json` and `/agents`. `GET /api/context?page=/queue` describes any screen, with its actions, IDs and required fields.
- MCP: run `huhqueue mcp` with `HUHQ_URL` and `HUHQ_API_TOKEN` set.
