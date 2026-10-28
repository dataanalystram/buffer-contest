# HuhQueue OSS upgrade plan: learn from open source, don't rebuild it

Written 27 Oct 2026. Research only; no HuhQueue code was changed.
HuhQueue baseline: `dataanalystram/bufferopensource` @ `bf19ec8` (main, with teams merged). It is compared against `docs/FEATURE_MATRIX.md` and the code itself.
Each open-source repo was shallow-cloned and read, not run. Licences come from each repo's LICENSE file.

## 0. What HuhQueue already has (so we don't build it twice)
- **One action registry** (`huhqueue/actions.py`, 108 actions) drives REST, OpenAPI, ⌘K, the CLI, llms.txt and MCP.
  - Every action has a risk class: safe, write, destructive, public, human or secret.
- **The actions runner** (`api/actions_api.py`) already has:
  - `dry_run`, which returns a preview plus a `confirm_token`;
  - idempotency keys;
  - "human" actions (approve/reject) blocked for API tokens.
- **Tokens** (`security.py`) carry scopes, a per-tool allow-list, `require_approval` and `rate_per_min`.
  - There is an `audit_log` table, with a 50-row viewer in Settings.
- **MCP** runs over stdio only (`mcp_server.py`, hand-written JSON-RPC: initialize, ping, tools/list, tools/call).
  - It offers 12 legacy tools plus the registry tools.
- **The worker** (`worker.py`) has:
  - claim/lock and recovery of stale jobs;
  - a fixed backoff of `[60,300,900,3600]` seconds, with `max_attempts=4`;
  - Retryable, Auth and Permanent error classes in `connectors/base.py`.
- **The bio page** (`services/growth.py`) has:
  - five block types (link, posts, socials, text, heading) and three themes;
  - clicks per block via `/l/` short links, top referrers and daily views.
- **CSV import** takes up to 500 rows, shows a preview before importing, and accepts column aliases.
- **Connectors:** Instagram, YouTube, X and TikTok. **There is no LinkedIn connector.**

## 1. Licences (this decides whether we copy code or rewrite it)
| Project | Licence (from LICENSE) | Stack | What HuhQueue (MIT) may take |
|---|---|---|---|
| Postiz `gitroomhq/postiz-app` | **AGPL-3.0** | NestJS, Prisma, Next.js, Temporal workers, Mastra agent, `@mastra/mcp` | Ideas, UX and data models only. **No code.** |
| Mixpost Lite `inovector/mixpost` | **MIT** | Laravel package, Vue/Inertia, Horizon queues | **Code can be copied** with credit |
| Shoutrrr `coollabsio/shoutrrr` (the Buffer/Typefully alternative, not containrrr's notification library) | **Apache-2.0** | Laravel 12, React/Inertia, `laravel/mcp`, SQLite-first single container | **Code can be copied** with credit. Keep their NOTICE and mark changed files |
| Socioboard `socioboard/Socioboard-5.0` | **GPL-3.0** | Node microservices plus a PHP web app, Mongo and MySQL | Ideas only |
| LinkStack `linkstackorg/linkstack` | **AGPL-3.0** (themes GPLv3) | Laravel, Livewire, block plugins in `blocks/*` | Ideas only |
| LittleLink `sethcottle/littlelink` | **MIT** | Static HTML/CSS: 91 brand buttons and 123 SVG icons | **Code can be copied** (the CSS and icons) |
| BioDrop `EddieHubCommunity/BioDrop` (archived) | **MIT** | Next.js, Mongo, `qrcode.react` | Code can be copied, but the project is archived, so treat it as a reference |

Add a `NOTICE` file at the repo root. List each file we copy with its source repo, commit, licence and "modified".

## 2. What each project does that HuhQueue doesn't (3–6 features each, and how they build them)

### 2.1 Postiz (AGPL, so ideas only)
1. **Remote MCP over Streamable HTTP, with OAuth.** In `libraries/nestjs-libraries/src/chat/start.mcp.ts`:
   - `MCPServer` is mounted on the API itself.
   - It accepts OAuth tokens (`pos_` prefix, scopes `mcp:read`/`mcp:write`, plus `openid email`) or an API key.
   - It can relay to self-hosted instances (`mcp.relay.service.ts`).
   - HuhQueue's MCP is stdio-only, so Claude.ai, ChatGPT connectors and phones can't reach it.
2. **MCP tool annotations and a per-network schema tool.**
   - Each tool in `chat/tools/*.ts` declares `readOnlyHint`, `destructiveHint`, `idempotentHint` and `openWorldHint`.
   - `integration.validation.tool.ts` (`integrationSchema`) returns a network's rules, `maxLength` and settings schema, so the agent knows the limits before it writes.
3. **Durable publish workflows** (Temporal, in `apps/orchestrator/src/workflows/post-workflows/*`):
   - activities retry 3 times;
   - when a step fails because the token expired, the workflow refreshes the token and repeats the step;
   - thread and comment parts are sent with a delay between them;
   - `missing.post.workflow.ts` checks every hour for posts that should have gone out in the last 3 hours.
4. **Public preview page `/p/[id]` with comments on parts of the text.**
   - The `Comments` model stores `anchorStart/End/Quote`, `parentId`, `resolvedAt` and a guest `displayName`.
   - Clients can comment without an account.
5. **"Plugs": automations per channel.**
   - A `@Plug({identifier, runEveryMilliseconds, totalRuns, fields})` decorator, e.g. `x.provider.ts:302` reposts a post once it reaches N likes.
   - LinkedIn Pages have plugs too (`linkedin.page.provider.ts:572/643`).
   - Stored in a `Plugs` table.
6. **Smaller features:** Signatures (text added to every post automatically, `autoAdd`), Sets (saved post presets), and an `Errors` table that stores the request body of every failed post.

### 2.2 Mixpost Lite (MIT, code can be copied)
1. **Per-network limits declared as data.** `src/Support/SocialProviderPostConfigs.php` sets, per post type:
   - minimum and maximum characters, photos, videos and GIFs;
   - `allowMixingMediaTypes` and `mediaTextRequirementOperator`.
   - HuhQueue instead writes a `validate()` function by hand for each connector.
2. **Rate limits remembered at two levels.** In `src/Concerns/Job/HasSocialProviderJobRateLimit.php`:
   - the cache is keyed per account and per network app (e.g. `mixpost-meta-api-limit`);
   - a rate-limited job is put back with `release($retryAfter)` instead of failing, and stops trying after 24 hours;
   - it also notes when a limit is about to be hit.
3. **A version of each post per account.** The `PostVersion` model (keyed by `account_id`) holds the original plus per-account overrides.
4. **Media conversions and stock media.**
   - Image resizing and video thumbnails (`MediaConversions/*`).
   - Unsplash and Tenor stock images and GIFs (`Services/UnsplashService.php`, `TenorService.php`).
5. **System status and log pages** (`SystemStatusController`, `SystemLogsController`, `Support/HorizonStatus.php`).
6. **No LinkedIn code to copy.** Mixpost's LinkedIn, Instagram, YouTube and TikTok support is in its paid Pro version, not in the MIT repo. `src/SocialProviders` has only Meta, Twitter and Mastodon.

### 2.3 Shoutrrr (Apache-2.0, code can be copied)
1. **Errors sorted by whether a retry can help.**
   - `app/Enums/ErrorKind.php` has `isRetryable()`.
   - Rate limits, network errors, server errors and media processing are retried. Auth, validation, duplicate and unsupported errors are not.
   - Every attempt is stored as a `PostTargetAttempt`.
   - `PublishPostTarget` runs with `tries=1` and handles its own backoff, so queue-level retries never pile up.
2. **Backoff with jitter.** `Services/Publishing/BackoffSchedule.php`: 60 s × 2ⁿ, capped at 1 hour, plus 0–10 % random jitter (25 lines).
3. **MCP that requires an explicit confirm.**
   - `app/Mcp/Servers/ShoutrrrServer.php` has 22 tools.
   - Its instructions say outward-facing actions that can't be undone (`publish_post_now`, `retry_post_target`, `delete_post`) need `confirm=true`, and only after a human approves.
   - `routes/ai.php`: OAuth routes throttled to 20 requests a minute, `auth:api`, `throttle:mcp`, and `RecordApiUsage` (usage events per key).
   - The `McpGrantWorkspace` model ties each grant to one workspace.
4. **Share links for review.**
   - The `PostShare` model stores `token_hash`, `expires_at` and `revoked_at`.
   - `CreateShareLinkTool` lets an agent create one.
5. **Media fixes before upload.** `ImageCompressor::compressToFit()`, `GifToMp4Converter` and `ImageToJpegConverter`.
6. **Planning features:**
   - account sets (`AccountSet`);
   - automatic threading (`Services/Posts/PostSplitter.php`);
   - `PublishPrecheck` and `NextSlotResolver`.
   - One container runs the web app, worker and scheduler on SQLite.

### 2.4 Socioboard (GPL, so ideas only)
1. **Keyword and hashtag "boards" plus trends**, i.e. social listening (`socioboard-api/Feeds/core/boards`, `trends`).
2. **Team tasks:** assign a post or comment to someone (`Publish/core/task`).
3. **Day-by-day schedule timers** (`Publish/core/schedule/schedulebase.js`) and report emails. HuhQueue already has scheduled reports.

### 2.5 LinkStack (AGPL, so ideas only)
1. **Blocks as plugins.** Each block is a folder `blocks/<type>/{config.yml, form.blade.php, display.blade.php, handler.php}`. Built-in blocks: email, heading, link, spacer, telephone, text, vcard.
2. **A vCard block:** a downloadable contact card with about 25 fields (`blocks/vcard/handler.php`).
3. **Buttons and themes.**
   - A large set of brand buttons (`config/button-names.php`).
   - A button editor with custom colour and CSS per button.
   - Theme packs uploaded as zip files.
4. **Analytics and page tools.**
   - Visit counters per day, week, month and year (`awssat/laravel-visits`).
   - JSON import and export of links.
   - An abuse-report form, a verified badge and a share button.
5. **A custom home domain** (`HOME_URL`, `SUPPORTED_DOMAINS`).

### 2.6 LittleLink (MIT, code can be copied)
1. **Ready-made buttons and icons.** `css/brands.css` has 91 brand buttons built from CSS variables, and `images/icons/` has 123 SVG icons. The logos stay trademarks of their brands.
2. **Accessible themes.**
   - Auto, light and dark themes, following the device's `prefers-color-scheme`.
   - Buttons with too little contrast get an inverse outline.
   - Lighthouse scores 100/100.

### 2.7 BioDrop (MIT, archived)
1. **A downloadable QR code** of the profile URL (`components/user/UserProfile.js`).
2. **A custom domain per profile** (`models/Profile.js` `settings.domain`), looked up in `pages/api/search/[domain].js`.
3. **Analytics and extra blocks.**
   - Daily stats by referrer and country (`ProfileStats`) and per link (`LinkStats`).
   - An `isStatsPublic` setting.
   - Event and milestone blocks.

### 2.8 How Postiz and Shoutrrr build LinkedIn (personal profiles and company pages)
Mixpost Lite has no LinkedIn code (see 2.2.6). The two real references are Postiz (AGPL, ideas only) and Shoutrrr (Apache, code can be copied).

| Topic | Postiz (`integrations/social/linkedin.provider.ts`, `linkedin.page.provider.ts`) | Shoutrrr (`Services/Publishing/Connectors/LinkedInConnector.php`, `LinkedInOrgResolver.php`, `config/services.php`) |
|---|---|---|
| OAuth scopes | **One LinkedIn app** asks for `openid profile w_member_social r_basicprofile rw_organization_admin w_organization_social r_organization_social`. The organization scopes need LinkedIn's approval for the Community Management API, even for personal posting. It has `refreshToken()`, but LinkedIn only gives refresh tokens to approved partners | **Two apps.** `linkedin-openid` covers personal profiles and needs no approval (`openid profile email w_member_social`). `linkedin-pages` is a separate app, because LinkedIn requires the Community Management API to be the only product on its app. **This is the right model for Ram** |
| Choosing a company page | Reads `v2/organizationalEntityAcls?q=roleAssignee&state=APPROVED` and keeps pages where the user is ADMINISTRATOR or CONTENT_ADMINISTRATOR. The author is `urn:li:organization:{id}` | `linkedInAuthorUrn()`. A resolver checks for `rw_organization_admin` |
| Text posts | Posts to `rest/posts` with headers `LinkedIn-Version: 202601` and `X-Restli-Protocol-Version: 2.0.0`, setting `commentary`, `visibility PUBLIC`, `feedDistribution MAIN_FEED` and `lifecycleState PUBLISHED`. Limit 3,000 characters. `fixText()` escapes LinkedIn's reserved characters but keeps @-mentions | Same endpoint, API version set in config (default `202605`). `escapeCommentary()` keeps mentions and `#hashtags` and escapes `\|{}@[]()<>*_~` |
| One or several images | `rest/images?action=initializeUpload`, then PUT the file, then `content.media.id` (one image) or `content.multiImage.images[]` (several). Several images need at least 2 and no video | Same, plus **alt text** for each image |
| Document (PDF carousel) | `rest/documents?action=initializeUpload`, then `content.media {id, title}` | Not supported |
| Video | `rest/videos?action=initializeUpload` with `fileSizeBytes`, upload in parts, then `finalizeUpload` with the parts' etags. Checks the video status until it is AVAILABLE | Same, but uploads exactly the byte ranges LinkedIn returns in `uploadInstructions`. `ensureVideoReady()` waits for processing |
| First comment | Posts it to `rest/socialActions/{urn}/comments` as `{actor, object, message.text}` | Not supported |
| Link previews | None | `articleFromText()` reads the first link's page title, description and image, uploads the image and sets `content.article`. The API does not build link previews by itself |
| Analytics and limits | Page share stats from `organizationalEntityShareStatistics`, plus plugs. At most 8 jobs at once (`maxConcurrentJob = 8`) | Status codes mapped to error kinds, and `Retry-After` honoured |

Limits to plan around:
- Personal LinkedIn tokens last 60 days, and LinkedIn gives refresh tokens only to approved partners. HuhQueue should warn 7 days before expiry and then mark the channel `needs_reconnect`.
- Posting to company pages needs a Community Management API app, and LinkedIn grants that only to a registered legal entity. So ship personal profiles first, and have the pages code ready behind config.

### 2.9 Buffer CSV bulk upload (what parity means)
Sources: https://support.buffer.com/articles/how-to-upload-posts-in-bulk-to-buffer-cTIhl4mv6H and https://buffer.com/resources/introducing-bulk-upload/
- **Buffer's columns** (headers are case-sensitive):
  - `Text`;
  - `Image URL`;
  - `Tags`;
  - `Posting Time`, as `YYYY-MM-DD HH:mm`; blank means the next queue slot;
  - `Board Name`, for Pinterest only.
- **Limits:** one CSV per channel; 100 posts per upload on paid plans and 10 on Free. Text and a single image only.
- **At the end** the user chooses Queue or Drafts. Rows over the limit are dropped without any warning.
- **HuhQueue's gap** (`growth.py` COLUMNS, line 1416):
  - `Posting Time` is not recognised, so the column is ignored;
  - there are no LinkedIn formats;
  - there's no upload button on each channel's page;
  - there's no Queue/Drafts choice;
  - there's no Buffer-style template to download.

## 3. Build list, in priority order (12 items)
Effort: S = up to 1 day, M = 2–3 days, L = 4 days or more.

| # | Item | Effort | Copy or rewrite | HuhQueue files to touch |
|---|---|---|---|---|
| 1 | **LinkedIn connector (personal profiles and company pages) + CSV bulk upload parity with Buffer** | L | Copy from Shoutrrr: escaping, image and video upload, alt text, link previews, error mapping. Rewrite from Postiz ideas: documents, first comment, page picker, page stats | new `connectors/linkedin.py`, `connectors/__init__.py`, `services/oauth.py`, `config.py`, `.env.example` (`LINKEDIN_*`, `LINKEDIN_PAGES_*`, `LINKEDIN_API_VERSION`), `worker.py` (expiry warning), `services/growth.py` (CSV), `Channels.jsx` (connect button and page picker), `Composer.jsx`, `services/analytics.py`, tests |
| 2 | **Agent safety layer:** kill switch, daily caps, token expiry, fuller audit log | M | Rewrite | `security.py`, `api/actions_api.py`, `actions.py`, `models.py` and a migration, `api/app.py`, `worker.py`, `Settings.jsx` |
| 3 | **Reliable publishing:** errors sorted by retryability, a record of every attempt, backoff with jitter, a rate-limit cache, a sweep for missed posts, a worker heartbeat | M | Copy from Shoutrrr and Mixpost, rewrite the Postiz sweep idea | `connectors/base.py`, `worker.py`, `models.py` (`post_target_attempts`), `api/app.py` (`/api/health`), `scripts/deploy_instabox.py` (systemd watchdog) |
| 4 | **Remote MCP:** Streamable HTTP with OAuth 2.1, every registry action available, tool annotations, a schema tool | L | Rewrite | `mcp_server.py`, new `api/mcp_http.py`, `security.py` (OAuth clients and grants, dynamic client registration, PKCE), `actions.py`, `api/agents.py` |
| 5 | **Bio page v2: blocks and themes.** Email signup, vCard, phone, spacer, image, YouTube/Spotify embed, featured post, 90+ brand buttons, auto/light/dark themes, button styles, JSON import and export | M | Copy LittleLink's CSS and icons, rewrite LinkStack's block idea | `services/growth.py`, new `static/bio/brands.css` and `icons/`, `models.py` (`bio_subscribers`), `Growth.jsx`, NOTICE |
| 6 | **Bio analytics, QR code and SEO.** Daily totals by referrer, country and device; click-through per block; UTM tags; downloadable QR; share image; verified badge; share button | S–M | Rewrite. Use the `segno` library (BSD licence) for QR codes | `services/growth.py`, `models.py` (`bio_daily_stats`), `api/growth.py` (`/api/bio/{id}/qr.svg` and `.png`), `Growth.jsx`, `requirements.txt` |
| 7 | **Media checks and automatic fixes:** limits declared per network, compress to fit, GIF to MP4, check videos before upload | M | Copy (Mixpost limit configs, Shoutrrr compressor logic) | `connectors/base.py`, each connector's `validate()`, `services/media.py`, `services/endcard.py` |
| 8 | **Custom domain for the bio page and short links** | M | Rewrite | `models.py` (`custom_domains`), domain routing in `api/app.py`, `api/growth.py`, `Settings.jsx`, docs for Caddy or Cloudflare |
| 9 | **Share links and guest comments**, so a client can approve without an account | M | Copy Shoutrrr's `PostShare`, rewrite Postiz's text-anchored comments | `teams.py`, `api/teams_api.py`, `models.py` (`post_shares`), new public page `/p/{token}`, `PostCard.jsx`, `Approvals.jsx` |
| 10 | **Channel groups, per-channel post versions and signatures** | M | Copy (Shoutrrr `AccountSet`, Mixpost `PostVersion`), rewrite Postiz Signatures | `models.py`, `services/posts.py`, `actions.py`, `Composer.jsx`, `Library.jsx` |
| 11 | **Always-on packaging:** one Docker container running web, worker and scheduler; docker-compose; a status page | S | Rewrite | new `Dockerfile` and `docker-compose.yml`, `cli.py` (`serve --with-worker`), `Settings.jsx`, `SETUP.md` |
| 12 | **Automations and threading:** repost or auto-comment when a post passes N likes; split long text into X or Threads threads | M | Rewrite Postiz plugs, copy Shoutrrr's `PostSplitter` | `services/growth.py`, `worker.py`, `connectors/x.py`, `Composer.jsx` |

### Details for items 1–4

**1. LinkedIn and CSV parity**
- **Personal profiles:**
  - Sign in with the scopes `openid profile email w_member_social`.
  - The post author is `urn:li:person:{sub}`, read from `/v2/userinfo`.
  - Store `token_expires_at`. Warn 7 days before expiry, then mark the channel `needs_reconnect`.
- **Company pages** use a second, optional LinkedIn app (`LINKEDIN_PAGES_*`):
  - scopes `w_organization_social r_organization_social rw_organization_admin`;
  - after sign-in, list the pages the user can post to (`organizationalEntityAcls`);
  - each page Ram picks becomes its own channel.
- **Composer options** (`option_schema`):
  - `li_format`: auto, text, image, multi-image, document, video or article;
  - `document_title`, `alt_text`, and visibility (public or connections only);
  - `first_comment`, posted through `socialActions/{urn}/comments` after the post goes out, with saved progress so a retry doesn't post the comment twice.
- **Checks before posting:**
  - at most 3,000 characters;
  - multi-image posts need 2–20 images and no video;
  - a post can have one video or one PDF, never mixed with images.
- **Video:**
  - Upload using the byte ranges LinkedIn returns, then finalize.
  - Wait for processing in the worker's existing `poll_processing` loop.
- **Errors:**
  - 401 means the login expired;
  - 429 and 5xx are retried, honouring `Retry-After`;
  - 422 is final.
- **CSV:**
  - Recognise these column names: `posting_time`, `alt_text`, `link`, `document_url`, `document_title`, `first_comment`.
  - Accept Buffer's date format, and add LinkedIn post formats.
  - Add a "Bulk upload" button on each channel's page, with a Queue or Drafts choice.
  - Offer a template that matches Buffer's, so a Buffer CSV imports unchanged.
  - Keep the 500-row limit, but **tell the user about extra rows instead of dropping them silently.**

**2. Agent safety layer**
- **A global kill switch:**
  - stored as a key/value row `agents.paused`;
  - toggled from a header button or `huhqueue agents pause`;
  - while paused, every token call gets HTTP 423 and the worker holds posts that agents created.
- **New limits on each token:** `max_public_per_day`, `max_actions_per_day`, `expires_at`, `allowed_channel_ids`.
- **Public or destructive actions through MCP** first return a preview and a `confirm_token`, unless the token has `auto_confirm`.
- **The audit log should record:**
  - a hash of the input, the action, the result, and the ids of anything created.
- **The audit viewer should get:**
  - filters, CSV export, and "undo" links where a reverse action exists.
- **An alert** (ntfy or email) on each token's first public action of the day.

**3. Reliable publishing**
- Sort errors by kind and store every attempt in `post_target_attempts`.
- Retry after 60·2ⁿ seconds, capped at 1 hour, plus up to 10 % jitter.
- Remember rate limits per channel and per network app.
- Run an hourly sweep that re-queues posts more than 10 minutes overdue and sends an alert.
- `/api/health` reports the worker's last run, overdue posts and failures in the last 24 hours.
- Run under systemd with automatic restart and a watchdog, plus an outside uptime ping.

**4. Remote MCP**
- Serve MCP at `/mcp` over Streamable HTTP.
- Act as an OAuth 2.1 server: dynamic client registration, PKCE and a consent screen.
  - Map `mcp:read` and `mcp:write` onto HuhQueue's scopes and tool lists.
  - Existing API tokens keep working.
- Offer every registry action an agent may use.
- Drop the 12 hand-written legacy tools, keeping their old names as aliases.
- Derive tool annotations from each action's risk class.
- Add a `channel.schema` tool, and MCP resources for queue and calendar snapshots.
- Throttle the OAuth endpoints to 20 requests a minute.

## 4. Where this beats Buffer once built
- **Agent-first, with real safeguards:** a kill switch, caps, previews, human approval, and an audit log with undo.
- **LinkedIn:** document carousels, first comment, and link previews with the right image.
- **CSV:** takes Buffer's template plus channels, several media files, first comment and post formats, up to 500 rows, and never drops rows silently.
- **Bio page:** your own domain, QR code, email signup and full analytics, without paying per channel.
- **Review and hosting:** guest review links, and always-on self-hosting with health checks.

## 5. Notes and judgment calls
- **Postiz** is the richest reference, but it is AGPL. Every Postiz item is a rewrite in Python from observed behaviour; don't open the Postiz file while writing its HuhQueue equivalent.
- **Shoutrrr** is the best legal source for LinkedIn code and for reliability patterns. Anything copied needs the Apache licence text and a "changed" note in NOTICE.
- **LittleLink:** the button CSS and SVG icons are MIT, but the logos remain their brands' trademarks.
- **Not checked live:** how long LinkedIn takes to approve the Community Management API, and whether API version `202605` is still current at build time. Keep `LINKEDIN_API_VERSION` in config.

## North star (Ram, 27 Oct 2026)
- Goal: the world's best content management/publishing tool, beyond Buffer, with best-in-class UI/UX.
- Reuse aggressively: copy permissively licensed open-source code (MIT/Apache/BSD) where it is the best implementation, credit it in NOTICE, and save time and tokens. AGPL/GPL projects are ideas only.
- Fully headless and agent-first, with no bottlenecks:
  - Every UI action has an equivalent API endpoint and MCP tool (parity test in CI).
  - Every page exposes machine-readable context (`GET /api/context?page=<route>` plus a `<script type="application/json" id="agent-context">` block): what the page shows, the actions available, the IDs, and the required fields.
  - Stable `data-testid`/ARIA labels on every control, so browser agents can drive the UI with simple clicks.
  - An `llms.txt` and OpenAPI spec at the root, plus remote MCP (item 4).
  - Safeguards stay on every path: approvals, rate limits, the kill switch and the audit log.
