# HuhQueue feature matrix: Buffer Free / Essentials / Team vs competitors vs HuhQueue

Written 22 Oct 2026 on branch `plan/feature-parity`, based on `main` at `f5a144f`. This is a docs-only audit. No app code was changed.

> **See also:** [BUFFER_PRO_FEATURES.md](BUFFER_PRO_FEATURES.md) — every Buffer Essentials/Team feature seen in Ram's Team trial (26 Oct 2026), API feasibility, and the Instagram collaborators + music answers (both need the Facebook Login connection).

## How to read this

- **Buffer columns** come from https://buffer.com/pricing (feature comparison table), https://buffer.com/pricing.md (the machine-readable copy) and Ram's own Buffer Free account, which was walked through read-only today (`buffer-study/notes.md` in the project workspace). "In-app" means the item was seen in the live Buffer UI and is not on the pricing page.
  - ✅ = included, — = not included, a number = a limit.
- **Best competitor** names the strongest paid tier that has the feature, with its source:
  - Hootsuite: https://www.hootsuite.com/plans
  - Later: https://later.com/pricing/
  - Sprout Social: https://sproutsocial.com/pricing/ and https://sproutsocial.com/features
  - Publer: https://publer.com/pricing
  - Metricool: https://metricool.com/pricing/
  - SocialBee: https://socialbee.com/pricing/
  - Postiz: https://postiz.com/pricing
  - Mixpost Pro: https://mixpost.app/pricing
  - Sendible: https://www.sendible.com/pricing
  
  All of these were fetched 22 Oct 2026.
- **HuhQueue status** was checked by reading the code at `f5a144f`, not guessed. Each status names the file it rests on.
  - **Done**: it works end to end (API + UI, or API where only an API makes sense).
  - **Partial**: some of it exists. The cell says what is missing.
  - **Missing**: nothing in the code.
- File shorthand:
  - `app.py` = `huhqueue/api/app.py`
  - `posts.py` = `huhqueue/services/posts.py`
  - `ig.py` = `huhqueue/connectors/instagram.py`
  - `security.py` = `huhqueue/security.py`
  - `mcp` = `huhqueue/mcp_server.py`
  - UI pages live in `web/src/pages/*.jsx`

### Plans at a glance (prices as listed on 22 Oct 2026)

| Product | Tiers looked at | Price shape |
|---|---|---|
| Buffer | Free / Essentials / Team | Free (3 channels). Essentials $5/channel/mo billed yearly ($6 monthly per pricing.md). Team $10/channel/mo billed yearly |
| Hootsuite | Standard / Professional / Advanced / Enterprise | Per user: from $99 / $199 / $399 per month. Enterprise is custom |
| Later | Starter / Growth / Scale | $18.75 / $37.50 / $82.50 per month billed yearly |
| Sprout Social | Essentials / Standard / Professional / Advanced | $79 / $199 / $299 / $399 per seat per month |
| Publer | Free / Professional / Business / Enterprise | Per account + per member (slider) |
| Metricool | Free / Starter / Advanced / Custom | From $20–25/mo up to $159–210/mo for 50 brands |
| SocialBee | Bootstrap / Accelerate / Pro tiers | $29 / $49 / $99 per month (monthly billing) |
| Postiz | 4 cloud plans + self-host (AGPL) | $29 / $39 / $49 / $99 per month cloud |
| Mixpost | Lite (free OSS) / Pro / Enterprise | One-time $299 (Pro) / $1,199 (Enterprise) self-hosted |
| Sendible | Creator → White Label | Per plan (the page shows a USD switcher). White Label is a separate plan |

---

## 1. Core feature matrix

| # | Feature | Buffer Free | Buffer Essentials | Buffer Team | Best competitor that has it | HuhQueue status (verified) |
|---|---|---|---|---|---|---|
| 1 | Collaborators / team members | 1 user | 1 user | Unlimited | Mixpost Pro: unlimited members, one-time license. Postiz: unlimited team members | **Done (teams branch, 26 Oct 2026).** Unlimited people, each with their own email + password and server-side sessions (`teams.py`, `api/teams_api.py`, `security.py` `Auth.session`). Settings › Team: invite by link (email optional; emailed when `HUHQ_SMTP_*` is set, 7-day one-use token), resend/new link, disable, remove, reset 2FA. The old app password became the bootstrap Owner (migration `0010_teams_bootstrap_owner.py`) |
| 2 | Roles & permissions | — | — | ✅ "Custom access and permissions" (per-channel) | Later Growth: custom roles and permissions. Publer: roles and access levels per account | **Done.** Roles Owner / Admin / Editor / Contributor / Client (view + comment) / Viewer, plus a per-channel cell (`User.channel_access`: none / viewer / contributor / editor) that can only lower a role. The guard enforces it for sessions and user-bound tokens (`teams.authorize`), list responses are filtered to visible channels, and a teammate's API token is capped by their current role. UI: role picker, per-person Channels dialog and a who-can-do-what matrix (`components/Team.jsx`) |
| 3 | Approval workflows | — | — | ✅ | Hootsuite Advanced. Later Growth (internal + external approvals). Publer (internal + client) | **Done.** Contributors (by role or channel cell) can only submit: scheduling, queueing or publish-now is held as `pending_approval`; every approver on that channel gets a bell (and email) notification. Editors+ Approve (optional note) or Reject (note required for a teammate's post) from Publish › Approvals or any post card (`components/Approvals.jsx`); the requester is notified with the note, the approved post is scheduled as requested, the rejected one returns to Drafts showing "Changes requested". Agent-token approvals unchanged. Not built: multi-step chains, external no-login client approval links |
| 4 | Client / brand workspaces | — (per-channel billing only) | — | — | Publer: unlimited workspaces. SocialBee: 5–30 workspaces. Metricool: up to 50 brands. Sendible: client workspaces | **Missing.** No workspace or brand entity. Every channel is global |
| 5 | First comment | — | ✅ (IG, FB, LinkedIn) | ✅ | Later Starter: IG first comment. Publer: scheduled comments and threads | **Done.** IG (`ig.py` posts `/{media}/comments` after publish), X (reply under the post, `x.py`) and YouTube (`youtube.py` `post_comment`). The composer fans it out to every channel that supports it (`Composer.jsx` ~L403). The worker appends the CTA text (`worker.py` L171). **Auto first comment** rules (Growth › Automations, `automation.create kind=first_comment`): per channel, optional tag/campaign/video-only filter, keep-or-append, applied at publish (`services/growth.py` `apply_first_comment_rules`) and "Run now" fills queued posts. TikTok: not supported by the API (`tiktok.py` `first_comment: False`) |
| 6 | Hashtag manager | — | ✅ | ✅ | Publer Business: hashtag suggestions and analytics. SocialBee: hashtag collections + AI generation. Metricool: hashtag tracker | **Done (analytics).** Saved hashtag groups (Library) + composer insert, plus per-hashtag and per-group performance (avg reach/views/ER, group reach vs posts without it) in Insights › Hashtags & campaigns and in the PDF/CSV (`services/analytics.py` `hashtags`). **Growth › Hashtags** (`/api/hashtags/*`): groups CRUD (same store the composer reads, limit-checked), per-hashtag uses / sent / avg engagement over 90 days, group performance, and caption suggestions from your own best performers. Missing: AI-generated hashtags |
| 7 | Channel groups | — | ✅ | ✅ | Postiz: "customer groups" + preset channel groups. Buffer Essentials | **Missing.** Only "New post to all channels" (⌘K) and manual multi-select chips. No saved groups |
| 8 | Bulk CSV upload | — | — | — | Publer: CSV up to 500 posts. SocialBee: CSV uploads. Sendible: bulk scheduler + CSV | **Done.** Growth › CSV import (`/api/import/csv/preview` dry run + `/api/import/csv`): up to 500 rows, `,` `;` or tab, column aliases, channels by provider or @handle, dates in a chosen timezone, media URLs downloaded to the library, per-network checks (YouTube video + title, IG/TikTok media, caption limits), topic-key duplicate guard, template download, import valid rows only |
| 9 | RSS auto-post | — | — | — | Publer: unlimited RSS. Postiz: RSS auto-post with AI descriptions. Sendible: RSS auto-poster | **Done.** RSS/Atom automation: drafts or queue, caption template, backfill, dedupe by GUID + topic key, optional image (Growth › Automations). Missing: AI captions for feed items |
| 10 | Evergreen recycling / recurring | — | — | — | Publer Business: recycling + recurring + spintax. Postiz: evergreen repost. Sendible: repeating posts | **Done.** Evergreen recycling (min age, max recycles per post, per-channel per run, tag/campaign/score filter, never while a copy is pending) and recurring posts (daily/weekly/monthly, interval, weekdays, until date, max occurrences, DST-safe) run from the worker; preview + run now in Growth › Automations. Missing: spintax variants |
| 11 | Link-in-bio page | ✅ Start Page (counts as a channel) | ✅ | ✅ | Later: Link in Bio with linked posts, analytics, email capture. Metricool: multiple link-in-bio | **Done.** Growth › Link in bio: public `/bio/{slug}` (server-rendered, no JS, served by the UI and edge apps, no login), blocks (links with highlight, heading, text, latest-posts grid, social icons auto from channels), hide/reorder, lavender/night/lilac themes + accent + button shape, draft/publish with owner preview, multiple pages. Every link goes through a tracked `/l/` short link; analytics: views and clicks per day, CTR, clicks per link, top referrers. Missing: email capture |
| 12 | UTM builder / link shortener | Shortening ✅, UTM — | Shortening ✅, UTM ✅ | ✅ / ✅ | Sendible: custom UTM + Bitly custom domain. Publer: 7 shorteners | **Done.** Self-hosted shortener `/l/{code}` (custom codes, 302, bots and HEAD not counted, UA hashed, no cookies), per-link daily clicks + referrers. UTM builder with live preview; per-channel defaults with `{provider}` `{username}` `{campaign}`; tags every link in caption + first comment at publish (per post `options.utm` or the global "tag every post" switch), optionally swapped for tracked short links; clicks roll into campaign stats (Growth › Short links & UTM) |
| 13 | AI assistant (captions, repurpose, hooks) | ✅ (unlimited credits) | ✅ | ✅ | Publer Business: unlimited AI prompts (text + images). Postiz: AI copilot + agents. Later: Caption Writer | **Done, free, bring your own model.** Composer › AI writer (`POST /api/ai/write`, `ai.write`): write, rephrase, shorten, expand, better hook, hashtags, fix grammar, 5 tones, length capped for the selected networks. Runs on the model from Settings › AI model (NVIDIA NIM, Gemini, Groq, OpenRouter, Ollama, OpenAI, Anthropic or any OpenAI-compatible URL); no credits |
| 14 | Media library / asset manager (folders, tags, search) | Not on pricing page | — | — | Publer: media library with labels, notes, used/unused search. Mixpost: media library | **Partial.** `media` table with SHA-256 dedup hash, alt text, JPEG copy for IG, end-card render, duration and size. API: list (latest 200), upload (multi + `.zip`), from-URL, patch alt text, delete (`app.py` `/api/media*`). Missing: library UI page (upload only happens inside the composer), folders, tags, search, usage tracker, pagination |
| 15 | Canva / Unsplash / Drive integrations | ✅ all | ✅ | ✅ | Publer: Canva, Drive, Dropbox, OneDrive, Unsplash. SocialBee: Canva, Unsplash, GIPHY | **Partial.** Import from any public https URL (`/api/media/from-url`, MCP `upload_media` accepts a URL). No native pickers |
| 16 | Unified inbox (comments, DMs, mentions) | Comments ✅ | ✅ | ✅ | Sprout Standard: Smart Inbox (comments, DMs, mentions). Later Growth: IG DMs, comments, mentions | **Partial → mostly done** (`feat/channel-pages`). Comments from Instagram and YouTube (sync, reply, resolve/reopen, status tabs, by-post view) plus a **Mentions** tab: IG posts that tag you via the Graph `/{ig-user-id}/tags` edge, stored as `comments.kind='mention'` (`connectors/instagram.py` `list_mentions`, `services/community.py`, `ChannelCommunity.jsx`). Bulk resolve/reopen/reply (`POST /api/comments/bulk`). Missing: DMs, X/TikTok comments, @mentions in other people's comments (needs webhooks) |
| 17 | Saved replies | 1 | Unlimited | Unlimited | Sprout and Hootsuite (saved replies / templates) | **Done** (`feat/channel-pages`). Library `saved_replies` with `{author}` fill-in, picker in Community and **bulk reply with a saved reply** on the channel-page Community tab (`ChannelCommunity.jsx` BulkBar, `community.bulk`). Unlimited |
| 18 | Sentiment | — | — | — | Sprout Advanced: sentiment in Smart Inbox and Reviews. Later Scale: Brand Health | **Missing** |
| 19 | Analytics with custom ranges | 7/30/MTD, Custom = Upgrade | ✅ custom | ✅ | Later Scale: custom analytics, 2-year history | **Done.** 7/30/90/MTD/Custom + channel, format, campaign and tag filters, period-over-period deltas on every KPI and a this-vs-previous views chart (`Insights.jsx`, `/api/reports/summary`). Audience growth from daily follower snapshots (`follower_snapshots`, worker once a day, Insights › Audience). Known: the legacy Export CSV button still sends `days=30` for custom ranges; the report CSV (`/api/reports/report.csv`) honours the range |
| 20 | Competitor benchmarking | — | — | — | Hootsuite Standard (5) and Professional (20). Later Scale (20). Metricool (up to 100) | **Missing** |
| 21 | Best time to post | — (blog only) | — | — | Later Starter: IG/FB/TikTok best time. Publer Business. Metricool | **Done (own data).** Weekday×hour heat-map of median engagement from your sent posts (channel local time), top slots, and "Use as posting slots" (`POST /api/insights/best-times/apply`, action `channel.best_times_apply`). Research fallback in onboarding. IG `online_followers` not used yet |
| 22 | Branded PDF / CSV reports | — | Reports CSV/PDF/markdown ✅ | + Branded reports ✅ | Metricool: PDF/PPT + custom templates. Sendible: branded + auto-emailed | **Done.** Custom report builder (Insights › Report): pick sections (overview, audience, channels, top posts, Reels, post types, best times, hashtags, campaigns) + filters → branded lavender PDF (logo cover, page numbers, period comparison) or CSV; markdown via `/api/reports/report.md`. Weekly schedule (day/time/tz/range, keeps the builder's sections and filters) emailed over your own SMTP and kept in an archive |
| 23 | White-label | — | — | — | Mixpost: White Label Basic/Branding. Sendible: White Label plan. Metricool: White Label | **Missing.** The logo and brand are hard-coded HuhByte assets (`huhqueue/static/logo.png`) |
| 24 | Notes / internal comments on posts | — | ✅ Notes | ✅ | Later Growth: post comments + activity log. Hootsuite Advanced: internal comments | **Done.** Comment thread on every post card (`Comments` in `components/Approvals.jsx`, `GET/POST/DELETE /api/posts/{id}/notes`): @mention autocomplete from the team directory, mentions notify that teammate, other participants (requester, earlier commenters) get a "comment" notification. Clients can comment; viewers read only. Authors delete their own comments, owners/admins any |
| 25 | Version history | — | — | — | Mixpost Pro: "Post versions" | **Missing.** There is a per-post event timeline (`/api/targets/{id}/events`), not content revisions |
| 26 | Audit log | — | — | — | Sprout: message audit trail | **Partial.** `audit_log` table. Every token request except GET is logged, plus token mint, app-key change, approve/reject (`app.py` `_audit`). It is viewable in Settings (`/api/audit`). Missing: UI-session edits (caption changes, deletes, settings) are not audited, and there is no filter or export |
| 27 | Content calendar with campaigns | Calendar ✅ | ✅ | ✅ | Sendible: campaigns. Metricool: campaign dashboards. Later: calendar notes | **Partial.** Week/month calendar, drag to reschedule, click an empty cell to schedule, "N more" (`Calendar.jsx`). Campaigns entity (Growth › Campaigns): colour, dates, active/upcoming/ended, per-campaign report (posts, scheduled, sent, views, engagements, link clicks, per channel), rename moves posts and links. Missing: campaign colour on calendar cells, calendar notes |
| 28 | Labels / tags | 3 tags | 250 | 250 | Mixpost: post labels | **Done.** Tag library with colours (`/api/library/tags`), tags on posts, tag filter in the list, bulk add tag. Unlimited. Missing: tag filter in Calendar and Insights (see §3) |
| 29 | Browser extension | ✅ | ✅ | ✅ | Publer / Later (Chrome) | **Missing** |
| 30 | Mobile app / PWA | iOS/Android apps | ✅ | ✅ | All majors have mobile apps | **Missing.** No `manifest.webmanifest` or service worker in `web/` |
| 31 | Webhooks | — | — | — | Postiz: webhooks. Mixpost Pro: webhooks | **Done.** Settings › Webhooks: any number of endpoints, per-event subscriptions (post.sent, post.failed, post.notify, approval.updated, comment.received, mention.received, channel.*, report.sent, automation.ran…), HMAC-SHA256 signature over `timestamp.body`, queued with the event and retried by the worker (1 m, 5 m, 30 m, 2 h, 12 h), auto-off after 15 failures, test ping, delivery log with retry (`services/webhooks.py`). The old single ntfy/Slack notification URL still works |
| 32 | Zapier / n8n / Make | ✅ all three | ✅ | ✅ | Postiz: n8n custom node + Make. SocialBee: Zapier/Pabbly/Make | **Done (generic).** Triggers: webhooks above work with Zapier "Catch Hook", Make "Custom webhook" and n8n's Webhook node. Actions: the REST API with a scoped token. No listed Zapier/Make app or n8n community node yet |
| 33 | Public API | 1 key, 3,000 req/mo | 3 keys, 7,500 | 5 keys, 15,000 | Publer Business. Sprout Advanced | **Done.** Full REST API, OpenAPI at `/api/openapi.json`, docs at `/api/docs`, unlimited scoped tokens with per-token rate limits (`security.py` `RateLimiter`). Missing (headless): CORS, token expiry |
| 34 | MCP | ✅ (Claude, ChatGPT, Cursor) | ✅ | ✅ | Publer Business. Postiz (every plan). Mixpost. Metricool | **Done.** stdio MCP server with 12 tools and a per-token tool allowlist, honours approval (`mcp_server.py`, `huhqueue mcp`). Covers about 30% of UI actions (see AI_NATIVE_PLAN.md). No HTTP/SSE transport |
| 35 | SSO / 2FA | 2FA ✅ | 2FA ✅ | 2FA ✅ | Later: 2FA + SSO. Hootsuite Enterprise: SSO | **Done (2FA).** TOTP (pyotp) with QR enrolment, 10 one-time recovery codes, replay protection, re-auth to disable or regenerate, owner/admin reset for a lost phone, per-session list with sign-out (Settings › Security). No SSO/OIDC |
| 36 | Image editor | ✅ (crop, rotate, flip, zoom, alt text; changelog 15 Sep 2026) | ✅ | ✅ | Later, Publer, Canva-style editors in Hootsuite | **Done.** Library › Edit image and the pencil on each composer image: crop to Original / 1:1 / 4:5 / 9:16 / 16:9 / 1.91:1, zoom, rotate, straighten, flip, brightness / contrast / saturation, up to 5 text overlays (position, size, colour, box), resize, alt text, server-rendered preview. Saves a new copy (`<name>-edited`) so posts using the original are untouched. Cropper is react-easy-crop (MIT); pixels are made by Pillow in `services/image_edit.py`, so agents get the same editor (`media.edit`, MCP `media_edit`) |

### 1b. Other Buffer plan-table rows (so nothing on buffer.com/pricing is skipped)

| Feature | Buffer Free | Essentials | Team | Best competitor | HuhQueue status (verified) |
|---|---|---|---|---|---|
| Ideas | 100 | Unlimited | Unlimited | Publer: Ideas | **Done.** Unlimited Kanban board with renamable columns (`Ideas.jsx`, `/api/ideas`, `/api/idea-columns`) |
| Drafts | Unlimited | Unlimited | Unlimited | — | **Done** (`when=draft`) |
| Templates | ✅ | ✅ | ✅ | Mixpost: post templates + variables | **Partial.** Library templates with `{placeholders}`, inserted from the composer. No variable fill-in form |
| Board view | ✅ | ✅ | ✅ | — | **Done** (Ideas board). No Gallery view (Buffer has Board/Gallery) |
| Scheduled posts per channel | 10 | Unlimited (5,000 fair use) | Unlimited | — | **Done.** Unlimited, with a per-channel daily limit (`channels.daily_limit`) |
| Threaded posts (X/Bluesky/Threads/Mastodon) | 1 | Unlimited | Unlimited | Publer: threads composer | **Done (X).** Composer › Post options › Thread: posts 2..n, each with its own text and up to 4 images or 1 video, live X-weighted counters, "Split into thread" (paragraph / sentence / word breaks, optional 1/n numbering, `POST /api/threads/split` = MCP `thread_split`) and a thread preview. Stored as `options.thread`; X posts each part as a reply to the one before, resumes after a rate limit without double-posting, puts the first comment under the last part (`connectors/x.py` `_post_thread`). Any connector with `capabilities.threads` gets the editor (Bluesky / Threads when they land) |
| Queue (re-arrange / shuffle) | ✅ | ✅ | ✅ | Publer: auto-schedule | **Done** (`feat/channel-pages`). Slot-based queue, drag reorder (`/api/queue/{cid}/order`, also from the planned grid), prioritize, **shuffle**, clean failed, delete drafts, empty queue (channel Settings → Queue actions, in-app confirm) |
| Visual calendar | ✅ | ✅ | ✅ | — | **Done** (see #27) |
| Reminder notifications (notify me) | ✅ | ✅ | ✅ | Later: notification publishing | **Partial.** `posting_method=notify` on IG, `notify_mode` on X/YouTube, TikTok inbox mode, "Your turn" tab, Mark as posted, and ntfy push through the webhook. No native mobile push |
| Start Page | ✅ | ✅ | ✅ | Later Link in Bio | **Done** (#11) |
| Duplicate posts | ✅ | ✅ | ✅ | — | **Done.** Duplicate, and duplicate-to-other-channels (`/api/targets/{id}/duplicate`) |
| Link shortening | ✅ | ✅ | ✅ | Publer | **Done** (#12) |
| Custom video covers | — | ✅ | ✅ | SocialBee: custom video thumbnails | **Partial.** IG Reel `cover_url` + `thumb_offset` (`ig.py` option_schema). Missing for TikTok and YouTube custom thumbnail upload |
| Reply to comments | ✅ | ✅ | ✅ | — | **Partial** (IG + YouTube only, #16) |
| Comment score | ✅ | ✅ | ✅ | — | **Done.** % of comments replied or resolved in 30 days on Home (`app.py` `/api/home`) |
| AI replies | 5/week | Unlimited | Unlimited | Sprout Advanced: Enhance Reply | **Done** (`feat/channel-pages`). `POST /api/comments/suggest-reply`: 3 suggestions from the pluggable AI provider (Settings → AI; OpenAI-compatible, Anthropic, Ollama) with tone, or rule-based quick replies when no provider is set. Never auto-sends (`community.suggest_reply`, "AI reply" button) |
| Comment insights (themes) | ✅ | ✅ | ✅ | — | **Missing** |
| Turn comments into posts | ✅ | ✅ | ✅ | — | **Done** (`feat/channel-pages`). "Start a post" on any comment or mention opens the composer with the comment quoted and the author credited; `POST /api/comments/start-post` (optionally saves a draft) |
| Insights history | 30 days | Unlimited | Unlimited | Later Scale: 2 yrs | **Done.** Kept forever on our own DB rows (`post_targets.insights`). Refreshed every 6h for 30 days after publish (`worker.refresh_insights`) |
| Performance overview (period-over-period) | — | ✅ | ✅ | — | **Missing** |
| Custom analytics (tag/channel filter) | — | ✅ | ✅ | Later Scale | **Done.** Channel, tag, campaign and post-format filters on Insights and reports |
| Takeaways (AI) | ✅ | ✅ | ✅ | Hootsuite Wisdom | **Missing** |
| Custom UTM | — | ✅ | ✅ | Sendible | **Done** (#12) |
| Two-factor auth | ✅ | ✅ | ✅ | — | **Missing** |
| Integrations: Bitly, Canva, ChatGPT, Claude, Cursor, Dropbox, Drive/Photos, IFTTT, Make, n8n, OneDrive, Perplexity, Raycast, Unsplash, WordPress, Zapier | ✅ | ✅ | ✅ | Publer, SocialBee | **Partial.** ChatGPT/Claude/Cursor are reachable through MCP (Claude Desktop/Cursor run `huhqueue mcp`). Make / n8n / Zapier / IFTTT: outgoing webhooks + REST API (rows 31-32). **Unsplash**: picker in Library and composer with your free key (Settings › App keys), imports keep the photographer credit as title and trigger Unsplash's download tracking (`services/unsplash.py`, `media.unsplash_search` / `media.unsplash_import`). Missing: Canva, Dropbox, Google Drive/Photos, OneDrive, Bitly, WordPress (generic URL import only) |
| API keys / app clients / rate limits | 1/1, 3,000 per 30d | 3/3, 7,500 | 5/5, 15,000 | — | **Done.** Unlimited tokens, rate per minute per token. No OAuth "app clients" |

### 1c. Things HuhQueue already has that Buffer does not (keep them)

- Duplicate guard: the same media (SHA-256) or the same topic key on a channel is blocked (`posts.find_duplicates`).
- Auto "Follow @handle" end card on videos (`services/endcard.py`).
- Format suggestion per channel (`/api/suggest-format`).
- Agent approval policy per token.
- Meta deauthorize and data-deletion callbacks (`services/meta_compliance.py`).
- IG 50-per-24h publish cap check.
- PNG/WebP→JPEG conversion for IG.
- Built-in tunnel (`tunnel.py`).
- Per-channel daily limit and pause.

---

## 2. Instagram-specific parity (Ram's first channel)

| Feature | Buffer | HuhQueue status (verified in `ig.py`) |
|---|---|---|
| Post / Reel / Story / Carousel | ✅ Post/Reel/Story radio | **Done.** `ig_type` auto/feed/reel/story/carousel (up to 20 items) |
| AI-content disclosure | ✅ | **Done.** `is_ai_generated` |
| Product tags / music | ✅ Tag Products, Music (auto music on Reels needs FB Page connection) | **Missing.** Music on Reels **is** possible since Meta's Instagram Audio API (`/ig_audio` + `audio_configuration`), Facebook Login only. Product tags also need the FB-login route. See [BUFFER_PRO_FEATURES.md §3](BUFFER_PRO_FEATURES.md#3-instagram-music) |
| Collaborators (co-author) | ✅ | **Partial** (`feat/channel-pages`). `collaborators` option validated (≤3, valid usernames, not on Stories, not your own account) and sent on feed/Reel/carousel-parent containers — **only for Facebook Login (Page-linked) channels** (`channel.meta.auth_mode == 'fb_login'`). Instagram Login channels (today's default) get a composer warning and the field is not sent, per Meta docs (see BUFFER_PRO_FEATURES.md §2). Needs the Facebook Login auth mode (bq-fblogin) |
| Location | — on composer | **Partial.** Raw `location_id` text field. No location search. (`feat/channel-pages`): like collaborators, only sent for Facebook Login channels; Instagram Login channels get a warning |
| User tags on image | — | **Missing** |
| Profile grid preview | ✅ "Grid" link | **Done** (`feat/channel-pages`). Publish › **Grid**: queued posts (newest-first, stories skipped) above the live profile grid, drag or arrows to reorder the queue (`GET /api/channels/{id}/grid`, `ChannelPublish.jsx` PlannedGrid) |
| Notify-me posting | ✅ | **Done.** `posting_method=notify` |
| Comments: pull + reply + resolve | ✅ | **Done** |
| Mentions / tags tab | ✅ Mentions tab | **Done** (`feat/channel-pages`). Community › Mentions (Graph `tags` edge), reply = comment on the tagging media; `GET/POST /api/channels/{id}/mentions[/sync]` |
| DMs | Not in Buffer | **Missing** (Later Growth has IG DMs) |
| Account insights (followers, reach, views) | ✅ | **Done.** `account_insights` + follower chart (follower_count needs 100+ followers) |
| Per-post insights | ✅ incl. watch time | **Partial.** views/reach/likes/comments/shares/saved/engagement_rate. Watch time and avg watch time are not stored |

---

## 3. Filter, view and sort controls (Calendar, Publish list, Sent, Community, Insights)

The Buffer column lists every control seen in Ram's account today (`buffer-study/notes.md` §1). Each HuhQueue status points to the line that implements it. "Server" means the API filters the rows. "Client" means the UI filters them after loading.

### 3.1 Publish: list view (all channels, `/schedule` in Buffer; `Queue.jsx` in HuhQueue)

| Buffer control | Buffer behaviour | HuhQueue status (verified) |
|---|---|---|
| List / Calendar view toggle | Header segmented control | **Done.** `Queue.jsx` L67–70 ("View" group → `/calendar`) |
| Status tabs: Queue / Drafts / Approvals / Sent (+counts) | Approvals = Upgrade on Free | **Done, and more.** Queue, Drafts, Approvals, **Your turn** (needs_action), Sent, **Failed** with counts (`Queue.jsx` `TABS` L10–16) |
| Filter by Channels | Multi-select | **Partial.** Single-channel pill row (All + one channel, `Queue.jsx` L83–89). The API accepts comma lists (`/api/posts?channel_id=a,b`), but the UI can't pick several |
| Filter by Tags | Multi-select | **Partial.** Single tag select (`Queue.jsx` L96). The API filters one tag, client-side, after a 300-row limit (`app.py` `post_list`). That can miss rows |
| Filter by Post Type (channel page) | Post/Reel/Story-aware | **Partial.** "All / Images / Videos" (`Queue.jsx` L97). The server only knows video vs not (`post_type == "video"`). No Reel/Story/Carousel/Text |
| Timezone selector ("Dublin") | Changes displayed times | **Missing** as a control. The channel header shows the channel timezone (`Queue.jsx` L64). Times use the browser timezone |
| Save current view | Saves the filter set | **Missing.** Filters live in the URL hash, so a bookmark works, but there are no named saved views |
| Search | Not seen in Buffer list | **Done (beyond Buffer).** Caption search (`queue-search`, client-side) |
| Campaign filter | Not in Buffer (tags = campaigns) | **Missing** (no campaign entity) |
| Bulk select + actions | Not in Buffer list | **Done (beyond Buffer).** Bulk delete / to drafts / requeue / add tag (`/api/bulk`, `bulk-bar`) |
| Card actions: Add tags, Publish Now, Edit, More (duplicate, delete…) | Inline row | **Done.** `PostCard.jsx`: publish-now, edit, duplicate, delete, approve/reject, retry, mark posted, refresh insights, event timeline. **Missing:** an inline "Add tags" picker (tags only via the composer or bulk) |
| "see more" on long captions | ✅ | **Partial.** Captions are clamped to 4 lines (`PostCard.jsx` L82 `line-clamp-4`), but there is no "see more" expander |
| Day group headers | ✅ | **Partial.** Shown on the Queue tab only (`Queue.jsx` ~L137 `tab === 'queue'`), not on Sent, Drafts or Failed |
| Drag to reorder (channel queue) | Drag handle | **Done.** Only when a channel is chosen and no filter is active (`Queue.jsx` `canDrag` L56) |
| Empty queue slot "New" (add at position) | ✅ | **Missing** (there is a New post button but no slot placeholders) |
| Posts-left banner / weekly goal pill "3/5 sent" | ✅ | **Partial.** The weekly goal is on Home (`/api/home` `weekly_goal`), not in the queue header |
| Sort order | Chronological | **Done.** Upcoming ascending. Sent newest first (`app.py` `post_list` sort). No sort control |

### 3.2 Publish: calendar (week / month)

| Buffer control | HuhQueue status (verified, `Calendar.jsx`) |
|---|---|
| View dropdown Week / Month | **Done.** Tabs L92 |
| Previous / Next / Today | **Done.** L85–87 |
| Filter: Channels | **Partial.** Single channel select L91. No multi-select |
| Filter: Status | **Missing.** The calendar always loads scheduled+sending+processing+sent+failed+needs_action+pending_approval (L36). You can't hide Sent or show only Approvals |
| Filter: Tags | **Missing** |
| "Show No Date drafts" | **Partial.** A "Drafts" switch adds drafts (L90), but only drafts *with* a date can appear. Undated drafts have no tray |
| Timezone selector | **Missing.** Hard-wired to the browser timezone (L95 note; drag writes `Intl…timeZone`, L54) |
| "Posting times options" (show slots) | **Missing.** Queue slots are not drawn on the grid |
| Click empty cell to schedule | **Done.** `calendar-cell` / `calendar-add` |
| Drag to reschedule | **Done.** L54 |
| Month "N more" expander | **Done.** `calendar-more` |
| Save current view | **Missing** (URL only) |
| Post type filter | **Missing** on the calendar |
| Campaign colour / filter | **Missing** |

### 3.3 Sent (global Sent tab + channel Sent/History)

| Control | Buffer | HuhQueue status (verified) |
|---|---|---|
| Sent tab with count | ✅ | **Done.** `Queue.jsx` TABS "sent" |
| Filter by channel / tag / post type / search | Same bar as Queue | **Done to the same degree as §3.1** (single channel, single tag, image/video, caption search) |
| Date range on Sent | Not seen | **Partial.** The API supports `start`/`end` (`/api/posts`). No UI control |
| Per-post metrics on sent cards | ✅ (Insights) | **Done.** Insights shown on card + refresh-insights |
| Channel History grid (posts made outside the tool) | Buffer Sent shows only Buffer posts. Insights lists all | **Done (beyond Buffer).** `ChannelTabs.jsx` HistoryTab: network grid, load more, refresh |
| Channel page Publish tabs (Queue/Drafts/Approvals/Sent) | ✅ | **Partial.** Tabs exist (`ChannelTabs.jsx` `PUB_TABS`) but have **no filters at all** there (no tag/type/search). Use `/queue/<id>` for filters |
| "Share as post" / reuse a sent post | ✅ (Insights) | **Partial.** Duplicate works on a sent post. No "share metrics as post" |

### 3.4 Community

| Buffer control | HuhQueue status (verified, `Community.jsx`, `ChannelTabs.jsx` CommunityTab) |
|---|---|
| By-post / List toggle | **Done.** `comments-view` L65 (global). The channel tab has no toggle |
| Tabs: Comments \| Mentions | **Partial.** Comments only. No Mentions |
| Status: unanswered (count) | **Done, and more.** Unanswered / Replied / Resolved / All tabs (L64; channel tab L81) |
| Filter and sort menu | **Partial.** Channel select (L68) + text search across comment and author (L67). Missing: sort (newest/oldest/most liked), filter by post, date, has-question, network |
| Reply box pre-filled with @username | **Partial.** Saved-reply insert pre-fills `@author` (L45). The plain reply box is empty |
| Emoji picker | **Missing** |
| Open saved replies | **Done** in global Community. **Missing** on the channel tab |
| Start a Post (comment → post) | **Missing** |
| Reply (primary) | **Done** |
| Resolve / reopen | **Done** (global). The channel tab has Reply only |
| Bulk resolve | **Missing** |
| Open in new tab / copy link / open thread | **Partial.** "Open post" link (`comment-open-post`). No copy link |
| AI reply suggestion | **Missing** |
| Sync / pull now | **Done (explicit button)**: `comments-sync`. The worker also syncs on a timer |
| Save current view | **Missing** |

### 3.5 Insights

| Buffer control | HuhQueue status (verified, `Insights.jsx`, `ChannelTabs.jsx` InsightsTab) |
|---|---|
| Date presets 7 days / 30 days / Month to date | **Done, and more.** 7/30/90/MTD (L35). The channel tab has 7/14/28/30 |
| Custom range (Upgrade on Free) | **Done.** Start/end date inputs (L39). Not on the channel tab |
| Filter by Tags | **Missing** |
| Filter by Post Type | **Missing** |
| Filter by Channel | **Done.** L34 |
| Metric segments: Posts / Content Impact / Audience Growth / Visibility / Follower Growth Rate | **Done.** Insights tabs: Overview (KPIs with deltas), Audience (followers over time, net growth, growth %), Posts (views, reach, likes, comments, shares, saves, ER, avg watch, profile visits, follows), Reels (views, avg/total watch time, 3s skip rate), Best times, Hashtags & campaigns, Report |
| This period / Comparison / Both | **Missing** |
| Performance-per-post table (with post type) | **Partial.** Top posts table (`insights-table`, top 20). No post-type column and no sorting |
| Weekly Takeaways (AI) | **Missing** |
| Export | **Partial.** CSV with two bugs (top-20 only, custom range ignored) |
| Share as Post | **Missing** |

### 3.6 Cross-cutting view controls to add (plan in ROADMAP M3)

1. Multi-select channel, tag, status and post-type filters, done on the server.
   - Move tag, search and post_type into SQL. Today they run in Python after `LIMIT`, which silently drops rows.
2. A display-timezone control: user pref, overridable per view.
3. Named **saved views**: a `saved_views` table holding the filter JSON per page, shown as chips.
4. Campaign filter, once campaigns exist.
5. Sort controls on lists, Community and the Insights table.
6. Bring the same filter bar to the channel-page tabs.

---

## 4. Contact management (commenters as contacts)

Buffer Community today (Ram's account, notes §1) has no contact or profile view. You work per comment: reply, resolve, saved replies, AI replies, comment score. Competitor inboxes go further.

| Capability | Buffer Community | Best competitor | HuhQueue status (verified) |
|---|---|---|---|
| Commenter profile card (avatar, handle, followers, bio) | Not observed | Sprout: "Contact Views… shared customer records" (all plans, https://sproutsocial.com/features) | **Missing.** `comments.author` is a plain string. No contact table |
| Conversation history per person (all their comments/replies across posts and channels) | Not observed | Sprout: "Conversation History… across profiles and networks" (all plans, same URL) | **Missing.** You can search by author text, but there is no per-person view |
| Internal notes on a contact | Not observed | Sprout Contact Profile Views: add custom contact info and notes (support article 115005889823, search excerpt; the page blocks fetches) | **Missing** |
| Custom contact fields / tags | Not observed | Sprout: customizable contact fields (Sprout pricing PDF, search excerpt). Hootsuite Professional: "build more complete customer contact profiles" (https://www.hootsuite.com/plans FAQ) | **Missing** |
| Segments / contact lists (VIPs, superfans, leads) | Not observed | Sprout People View: Contact Lists, filter by follower count (support 4853927757709, search excerpt) | **Missing** |
| Assignment (assign a comment or conversation to a teammate) | Not in Buffer (single user on Free) | Hootsuite Advanced: assign + skill-based routing (plans FAQ). Sprout: tasks/cases from messages (features page "Create customer care cases") | **Missing** (no users) |
| Collision detection | — | Sprout (all plans, features page) | **Missing** |
| Message tagging | Tags on posts only | Sprout Professional+: Message Tagging (features page) | **Missing** for comments |
| Saved replies | 1 (Free) / unlimited | Sprout, Hootsuite | **Done** (unlimited library, see #17) |
| AI reply suggestions in your voice | 5/wk Free, unlimited paid | Sprout Advanced: Enhance Reply. Hootsuite Professional: smart replies | **Missing** |
| Sentiment per message / contact | — | Sprout Advanced. Later Scale: Brand Health | **Missing** |
| Priority inbox / automated rules | — | Sendible: Priority Inbox. Hootsuite Professional: automated responses | **Missing** |
| DMs in the same thread as comments | — | Sprout Smart Inbox, Later Growth (IG DMs) | **Missing** (IG DMs need `instagram_business_manage_messages`) |
| CRM / helpdesk export | — | Sprout Advanced: Helpdesk integrations | **Missing.** Can be done through webhooks later |

The plan for this is in `CMS_PLAN.md` §4.9 ("Contacts"): data model, endpoints and screens. It is roadmap milestone M8.

---

## 5. What costs money to run (for the free self-host vs cheap hosted tier)

| Feature | Running cost | Notes |
|---|---|---|
| Instagram / Facebook / Threads / YouTube / TikTok inbox / Bluesky / Mastodon / LinkedIn publishing | Free (bring your own developer app) | From `docs/CONNECTORS_RESEARCH.md` |
| X publishing | **Paid**: $0.015 per post, $0.20 per post with a URL, owned reads $0.001 (pay-per-use since 6 Feb 2026) | Same doc §2. Needs a spending cap + "strip links" option |
| AI assistant / command bar / AI replies / takeaways / sentiment | **Paid per call** on a hosted LLM, or free with a local Ollama | Make the provider pluggable. Hosted tier = metered credits |
| Competitor benchmarking | Mostly API-restricted. IG Business Discovery is free but limited to business accounts | Scraping is out of scope |
| Link shortener | Free if self-hosted (own `/l/<code>` redirect). Bitly is paid above its free tier | Build our own |
| Media storage / bandwidth | Free on own disk. Hosted tier pays for storage + egress | Hosted tier: cap GB |
| Email (reports, invites, 2FA by email) | Free with own SMTP. Hosted tier pays per email | TOTP 2FA needs no email |
| Browser extension, PWA, webhooks, RSS, CSV, link-in-bio, UTM, roles, workspaces | Free (pure code) | All free-for-all |

## 6. Top 15 missing features (by impact for Ram's Instagram-first use and for "Pro features free")

1. AI assistant in the composer (captions, hooks, repurpose, hashtags) with a pluggable LLM. **Missing**
2. Natural-language command bar + one action registry (REST/OpenAPI/MCP/⌘K from one source). **Missing.** MCP covers about 30% of actions
3. Media library page with folders, tags, search, usage tracker (dedup hash already exists). **Partial**
4. Multi-user: users, roles, invites, per-channel access, 2FA (TOTP). **Done** (teams, 26 Oct 2026)
5. Human approval workflow (submit/approve/reject with comments, notes, @mentions). **Done** (teams, 26 Oct 2026; no multi-step or external links yet)
6. Filter parity + saved views + timezone control (§3). **Partial**
7. IG Mentions tab + DMs + bulk resolve + comment→post. **Missing**
8. Contacts: commenter profiles, history, notes, segments, assignment. **Missing**
9. Link-in-bio (Start Page) with click tracking + built-in shortener + UTM builder. **Done** (Growth)
10. Campaigns (calendar colour, filter, campaign report). **Partial** (entity + report done; calendar colour pending)
11. Evergreen recycling / recurring posts + RSS auto-post. **Done** (Growth › Automations)
12. Bulk CSV import of posts. **Done** (Growth › CSV import)
13. Reports: period-over-period, tag/type filters, PDF/markdown + branded cover, fix CSV bugs. **Done**
14. Best-time-to-post from the account's own data, feeding queue slots. **Missing**
15. Real webhooks (subscriptions, HMAC, retries) + headless mode (CORS, token expiry) + `/llms.txt`. **Partial** (`/llms.txt`, `/agents`, `/openapi.json`, `/api/context` done 27 Oct 2026; webhooks, CORS, token expiry still missing)

Also worth knowing:

- Channel groups are missing (in progress on another branch). Threads are done for X; Bluesky/Threads need their connectors first.
- Brand workspaces are missing.
- `/agents` and `/llms.txt` now exist (`huhqueue/api/agents.py`, 27 Oct 2026), so the ⌘K "For agents" link works.


## Analyze parity (Reports, Oct 2026)

Metrics are only those the networks return in 2026. Instagram media insights (Graph API v26, `/{media-id}/insights`): `views`, `reach`, `likes`, `comments`, `shares`, `saved`, `total_interactions`, `profile_visits`, `follows`, `reposts`; Reels add `ig_reels_avg_watch_time`, `ig_reels_video_view_total_time`, `reels_skip_rate`. `impressions` (deprecated for media after 2 Jul 2024) and `plays` (replaced by `views`) are not requested. Source: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights

| Buffer Analyze | HuhQueue |
|---|---|
| Audience growth (followers over time) | **Done.** Daily `follower_snapshots` (worker + "Record today"), net growth vs previous period, per channel |
| Post analytics (reach, views, likes, comments, saves, shares, ER) | **Done.** Posts tab + CSV; ER = total_interactions ÷ reach |
| Reels metrics (plays → views, avg watch time) | **Done.** Reels tab and PDF section |
| Best time to post + use as slots | **Done** |
| Period-over-period comparison | **Done** on every KPI, chart and section |
| Custom report builder → branded PDF/CSV | **Done** |
| Scheduled weekly reports | **Done** (own SMTP; archive without SMTP) |
| Hashtag / campaign performance | **Done** |
| Not yet | Follower history backfill from IG `follower_count` (new-followers-per-day, 30 days), `online_followers` in best times, monthly cadence, PowerPoint export |

---

## Channel pages + publishing pro (branch `feat/channel-pages`, 26 Oct 2026)

Per-channel page tabs, all free:
- **Publish:** Queue / Drafts / Approvals / Sent, posts-sent-this-week vs goal pill, timezone pill, "Add a post at <time>" chips for empty slots, List / Calendar / **planned Grid** (IG, TikTok, demo) with drag or arrow reorder, tag filter, Sent cards with numbers.
- **Community:** Comments · **Mentions**, Unanswered / Replied / Resolved / All, select many → **bulk resolve / reopen / reply (text or saved reply)**, reply box pre-filled with `@author`, emoji picker, **AI reply** suggestions, **Start a post** from a comment.
- **Insights:** 7d / 30d / month to date / **custom range** (free), this vs previous period per metric, daily comparison chart, takeaways, posts table, posts/daily CSV, **Share as post**.
- **Posting schedule:** custom times per weekday, presets, **posting goal**, **days off** (times kept, `disabled_days`), generate times (meet goal / recommended / copy from another channel), daily limit, timezone.
- **Settings:** pause, notification-publishing default, link shortening, UTM tagging (source/medium/campaign), queue actions (shuffle, clean failed, delete drafts, empty) behind an in-app confirm, token refresh, disconnect.
- **First comment:** Done (unchanged, composer fan-out).
- **Instagram collaborators:** validated and passed to the Graph API for Facebook Login channels only (see the §2 row).

Tests: `tests/test_channel_pages.py`, `tests/test_channel_pages_pro.py` (mocked Graph API for collaborators, `tags` mentions), `tests/e2e/test_ui_channel_pages.py` (Playwright, all tabs).

---

## LinkedIn + Buffer CSV + headless (branch `feat/linkedin-headless`, merged 27 Oct 2026)

| Area | Buffer | HuhQueue |
|---|---|---|
| LinkedIn personal profile | ✅ | **Done.** `connectors/linkedin.py` (OpenID Connect + `w_member_social`), text, image, multi-image, PDF document, video, link preview, first comment, `LinkedIn-Version` header from config, 7-day expiry warning |
| LinkedIn company pages | ✅ | **Done, behind config** (`LINKEDIN_PAGES_ENABLED` + a second app with Community Management API access, `r/w_organization_social`). Needs LinkedIn's approval for that product |
| Buffer CSV bulk upload | ✅ (Essentials+) | **Done, free.** Buffer's columns (Text, Image URL, Tags, Posting Time, Queue), Posting Time wins over Queue, LinkedIn columns (document, link, alt text, first comment, privacy), PDF media kind, over-limit rows flagged not dropped, Buffer + LinkedIn templates, **per-channel Bulk upload** into Queue or Drafts |
| Agent / headless access | API only | **Done.** `GET /api/context?page=` (page state, actions with REST route + MCP tool + risk), embedded `agent-context` script kept current by the SPA, `/openapi.json` at root, `/llms.txt`, `/agents`, stable `data-testid`/`aria-label` on every control, CI parity tests (UI call → API action → MCP tool; control labels) |
| Connect guide | Help centre | **Done.** `docs/CONNECT_INTEGRATIONS.md`: numbered steps for LinkedIn, Instagram/Meta, YouTube, X, TikTok, Buffer CSV and agents, linked from every connect screen |

Tests: `tests/test_linkedin.py`, `tests/test_headless.py`, `tests/e2e/test_ui_linkedin_headless.py`.
