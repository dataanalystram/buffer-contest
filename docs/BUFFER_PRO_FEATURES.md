# Buffer paid (Essentials / Team) features, and what HuhQueue needs to match them

Written 26 Oct 2026 on branch `docs/buffer-pro`. Docs only, no app code changed.

## Sources

- **Live Buffer account.** Ram's Buffer org on the Team plan trial (plan "Team / TRIAL", trial ends 9 Nov 2026). Walked through read-only on 26 Oct 2026. Nothing was published, deleted, invited, connected or changed in billing. Connected channels: Instagram `huhbyte.ai` (Professional, **connected with Instagram login, not through a Facebook Page**), TikTok `huhbyte`, YouTube `Huhbyte` (Shorts). X and LinkedIn are not connected, so their composers could not be checked.
- **Buffer pricing:** https://buffer.com/pricing (feature comparison table, last pricing change Nov 2025).
- **Buffer help centre:**
  - https://support.buffer.com/en-us/articles/connecting-your-instagram-account-to-buffer-n9Ad6veXsu
  - https://support.buffer.com/en-us/articles/adding-music-stickers-and-other-effects-to-instagram-and-tiktok-posts-G5QrWfQL4k
  - https://support.buffer.com/articles/using-instagram-with-buffer-YSjg2dXFV8
  - https://support.buffer.com/en-us/articles/refreshing-your-instagram-connection-through-facebook-acaC8V633l
- **Buffer changelog** (music + collaborators launch): https://buffer.com/changelog/music-collaborators-and-more-for-instagram
- **Buffer public API:** https://developers.buffer.com/reference.md (has `searchInstagramAudioCatalog`, `trendingInstagramAudio`, `InstagramAudio`).
- **Meta docs (Graph API v26.0):**
  - Content publishing: https://developers.facebook.com/docs/instagram-platform/content-publishing/
  - IG User Media (container params): https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
  - **Instagram Audio API:** https://developers.facebook.com/docs/instagram-platform/content-publishing/audio-api
  - Changelog (Audio API entry): https://developers.facebook.com/documentation/instagram-platform/changelog
  - IG Media collaborators: https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-media/collaborators.md
  - Collaboration invites: https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/collaboration.md
  - Media insights: https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/insights
  - Account insights: https://developers.facebook.com/documentation/instagram-platform/api-reference/instagram-user/insights.md
  - Mentions (Instagram Login): https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/mentions.md
  - Comment moderation: https://developers.facebook.com/documentation/instagram-platform/comment-moderation.md
- **HuhQueue code** checked at: `main` `b8ee675`, `feat/growth` `b9bf370`, `feat/teams` `62d0426`, `feat/channel-pages` `b04c000` (WIP), `feat/reports` `0f8466a` (already merged into main as the Reports work).
- **Screenshots** (from the live trial): `docs/screenshots/buffer-pro/`
  - `community-ai-reply-upgrade.png`: Community inbox with the AI reply upgrade dialog
  - `team-members.png`: Settings › Team Members
  - `calendar-week.png`: Publish › Calendar week view
  - `posting-schedule-ig.png`: Instagram channel › Posting Schedule

## Plans in one line

| Plan | Price | What it adds |
|---|---|---|
| Free | $0, up to 3 channels | 10 scheduled posts per channel, 100 ideas, 3 tags, 1 user, 30-day Insights history, 5 AI reply suggestions per week, 1 saved reply, API 1 key / 3,000 requests a month |
| Essentials | $5 per channel per month, yearly | Unlimited posts and ideas, 250 tags, first comment, channel groups, hashtag manager, custom video covers, unlimited AI replies and saved replies, full Insights history, performance overview, custom analytics (filter by tag/channel), reports (CSV/PDF/Markdown), custom UTM, notes. API 3 keys / 7,500 a month |
| Team | $10 per channel per month, yearly | Everything in Essentials plus unlimited users, **approval workflows**, **custom access and permissions** (per channel), **branded reports** (logo + cover page). API 5 keys / 15,000 a month |

Volume discount applies above 10 channels. "Unlimited" posts are capped by fair use at 5,000 per channel.

---

## 1. Feature-by-feature table

Plan column: **F** = Free, **E** = Essentials, **T** = Team only. "API?" means whether the official platform APIs allow it (mainly Meta for Instagram).

HuhQueue status codes: **Done**, **Partial**, **Missing**. Branch tags: `[main]`, `[growth]`, `[teams]`, `[pages]` = where the code lives today.

### Analytics (Insights)

| Buffer feature | Plan | How it works in Buffer (seen in the trial) | API? | HuhQueue status | What to build |
|---|---|---|---|---|---|
| Insights overview | F (30 days) / E+ unlimited | `/insights`. Range 7d / 30d / Month to date / Last month / Custom (natural-language input "last 7 days, Q1 2026"). Filter by channel and tag. Summary cards: Posts, Followers, Reactions, Comments, Eng. Rate, Views, auto-compared to the previous period. Top 5 posts (sort reactions/comments, Duplicate post). Performance table per channel (choose columns). Followers chart Bar/Line/Growth. Posts chart Bar/Stacked. "Show data table" and "Share as Post" on every chart | Yes: IG account + media insights | **Done** `[main]` Insights tabs, period comparison (`services/analytics.py`, `Insights.jsx`) | Natural-language date input; "Share as post" (turn a chart into an image post) |
| Per-channel insights | E+ | `/insights/<channel>`. Compare: previous period / week / month / year / custom. Filter by post type (Post/Reel/Story) and tag. Extra IG metrics: Shares, Saves, Follows from posts, Reach, Watch time (min), Avg watch time (s). Per-post table with paging. Metric presets: Content Impact, Audience Growth, Visibility, Follower Growth Rate vs "Buffer Median" | Yes. `shares`, `saved`, `follows`, `reach`, `ig_reels_video_view_total_time`, `ig_reels_avg_watch_time`, `views` on media insights | **Done** `[main]` Reels watch time, types, follower snapshots. `[pages]` channel insights 7d/30d/MTD/custom, takeaways, CSV | Benchmark line ("median of similar accounts") needs outside data, skip or use own history |
| Audience / demographics | E+ (marketing) | **Not shown** as its own section in the trial UI | Yes: `follower_demographics`, `engaged_audience_demographics` (age, city, country, gender), needs 100+ followers | **Done** Insights › Audience › Audience demographics: `follower_demographics` + `engaged_audience_demographics` by age, gender, country, city with timeframe, cached daily (`services/audience.py`, action `insights.demographics`); Meta's under-100-followers error is shown as a note | — |
| Reels / video metrics | E+ | Watch time + avg watch time, Reel badge, Post type filter | Yes (see above), plus `reels_skip_rate`, `crossposted_views`, `facebook_views` | **Done** `[main]` Reels tab | Add `reels_skip_rate` and `reposts` |
| Stories metrics | E+ | Post-type filter includes Story. No separate Story report seen | Yes but only for 24 h (or via `story_insights` webhook, **Facebook Login only**) | **Done** Insights › Stories: the worker lists live stories (`/{ig-user-id}/stories`) every 2 h and saves views, reach, replies, shares, navigation taps and exits in `story_snapshots`, also for stories not posted with HuhQueue (actions `insights.stories`, `insights.stories_sync`) | `story_insights` webhook needs Facebook Login (M5) |
| Best time to post | E+ | **No chart in the UI.** Posting Schedule › "Generate New Posting Slots" › "Update Slots to Match Buffer's Recommendations" and "Meet My Posting Goal" (disabled in trial) | Yes: `online_followers` (last 30 days, 100+ followers) or own post history | **Done** `[main]` best-times heatmap + "use as slots" | Add `online_followers` as a second signal; "meet my posting goal" slot generator `[pages]` |
| Weekly Takeaways (AI) | F | AI cards ("You have 2 comments without a reply", "Your content impact grew this period"), helpful/not helpful | n/a (own data + LLM) | **Done** `[pages]` channel takeaways + Insights › Overview "Takeaways across your channels" (growth leader, decliner, most engaged channel, posting goal, best format, best post, follower gain, stories, unanswered comments) with helpful / not helpful (`insights.takeaways`, `insights.takeaway_feedback`) | — |
| Reports / export | E+ | Export menu: CSV, Markdown, PDF, Download snapshots. "Save current view" saves page + filters to the sidebar. **No report builder, schedule or email UI was visible**, only notification toggles "Daily Post Recap", "Weekly Report Card" | n/a | **Done** `[main]` report builder, schedule, archive, branded PDF, CSV (more than Buffer shows) | Markdown export; saved views |
| Branded reports | **T** | Logo + cover page (pricing page; not visible in trial UI) | n/a | **Done** `[main]` branded PDF | — |
| Comment score | F | Animated score (52–70) in Community header: consistency, speed, habits | n/a | **Partial** `[main]` `response_time` stats | Score widget with explanation |

### Community / engagement

| Buffer feature | Plan | How it works in Buffer | API? | HuhQueue status | What to build |
|---|---|---|---|---|---|
| Comment inbox + reply | F | `/community`. "By post" or "List" view. Tabs **Comments** and **Mentions**. Filter by channel, status (All / Unanswered / Replied / Resolved), "Filter and sort comments" (disabled in trial), Bulk resolve. Reply box 9000 chars prefilled with `@user`, emoji, saved replies, "Start a Post" (turn comment into a post), Resolve | Yes: `GET /{media}/comments`, `POST /{comment}/replies`, hide/unhide, delete (IG Login and FB Login) | **Done** `[main]` pull/reply/resolve (`services/community.py`). `[pages]` by-post view, bulk resolve | Comment → post; "sort by newest unanswered" |
| AI reply suggestions | F 5/week, E+ unlimited | "Unlock Reply Suggestions" button. Upgrade modal: "You've reached the free plan limit of 5 reply suggestions this week" (screenshot) | n/a (LLM) | **Missing** (no reply-suggest endpoint; `assistant.py` drafts posts, not replies) | `POST /api/comments/{id}/suggest`: 2–3 drafts in the account's voice from past replies; no limit |
| Saved replies | F 1, E+ unlimited | Settings › Saved Replies (name ≤100, content ≤1000) + picker in the reply box | n/a | **Done** `[main]` saved replies | — |
| Mentions | F | Mentions tab (11 unanswered on huhbyte.ai). Liking a mention needs the **Facebook Page** connection (help centre) | Yes. IG Login: `GET /{ig_id}/tags` (media you're tagged in) + `POST /{ig_id}/mentions` reply; caption/comment @mentions arrive by webhook. FB Login: also `mentioned_media`, `mentioned_comment` lookups | **Missing** `[main]`; `[pages]` only shows a "mentions" notice | Pull `/tags`, subscribe to `mentions` webhook, Mentions tab with reply |
| Comment insights | F | Themes pulled from replies (pricing page) | n/a | **Missing** | LLM clustering of comment themes |
| Daily community recap email | F | Notification setting | n/a | **Partial** (in-app bell) | Daily digest email |

### Team, roles, approvals

| Buffer feature | Plan | How it works in Buffer | API? | HuhQueue status | What to build |
|---|---|---|---|---|---|
| Team members | **T** | Settings › Team Members. Invite: email + name, one **Admin** switch ("Can manage team members and team settings"). Owner badge. Require 2FA toggle (disabled with one member) | n/a | **Done** `[teams]` users, sessions, invites (link-only), 2FA TOTP + recovery codes | Merge `feat/teams` |
| Access levels per channel | **T** | Invite › Channel Permissions › Customize: per channel **Publish** = Full Access / Needs Approval / No Access, **Community** = Full Access / View Only / No Access | n/a | **Done** `[teams]` roles owner/admin/editor/contributor/client/viewer + per-channel matrix | Map Buffer's 2-column model onto ours in the invite UI (Publish + Community per channel) |
| Approval workflow | **T** | No approver role: a member set to **Needs Approval** on a channel submits; posts land in Publish › **Approvals** tab and calendar status "Pending Approval". "Collaboration" notifications cover approval requests | n/a | **Done** `[teams]` approve/reject with notes, notifications. `[main]` approvals tab for agent tokens | Request-changes state (Buffer marketing mentions it; not seen live) |
| Notes on posts | E+ | "Add Note" on every queued post and draft; threads, @mentions, follow thread; email settings "Mentions of you", "Thread updates", "Notes on your posts and drafts" | n/a | **Done** `[main]` notes + @mentions (`pro_content.py`); `[teams]` threads, delete | Follow/unfollow thread |
| Clients | — | Buffer has no client role; agencies use per-channel access | n/a | **Done** `[teams]` client role (view + comment) | — |

### Create & publish

| Buffer feature | Plan | How it works in Buffer | API? | HuhQueue status | What to build |
|---|---|---|---|---|---|
| Drafts | F | Publish › Drafts tab; calendar "Show No Date drafts" | n/a | **Done** | — |
| Ideas board | F 100 / E+ unlimited | Create › Ideas. Kanban Unassigned / To Do / In Progress / Done, rename/delete columns, New Group, Board/Gallery, filter by tag, **Generate Ideas** (AI: business + audience) | n/a | **Done** `[main]` ideas | AI "Generate ideas" from business/audience |
| Templates | F | Create › Templates: Discover (160) / Team / Personal, search, filter | n/a | **Partial** `[main]` templates | Curated starter library |
| RSS feeds | F | Create › Feeds: RSS URL + curated feed library by category | n/a | **Done** `[growth]` RSS automations | Curated list |
| Tags / campaigns | F 3 / E+ 250 | Settings › Tags (name + 19 colours, org-wide). Filter calendar, queue, ideas and Insights by tag | n/a | **Done** `[main]` tags; `[growth]` campaigns with stats | — |
| Channel groups | E+ | Settings › Channel Groups (name + channels, private to the user) | n/a | **Missing** (no model in any branch) | `ChannelGroup` model + picker chip in composer |
| Hashtag manager | E+ | Composer button. Group name ≤200, content ≤2000, "# Remaining: 5" counter (Buffer nudges ≤5 hashtags) | Meta allows 30 hashtags | **Done** `[main]` groups + stats; `[growth]` suggest | Live hashtag counter in composer |
| AI Assistant | F | Composer side panel: one prompt "What do you want to write about?" + Generate. (Rephrase/shorten etc. not visible before generating) | n/a | **Done** `[main]` `assistant.py` | — |
| First comment | E+ | IG Post/Reel "First Comment" field; IG, FB, LinkedIn | Yes: `POST /{media}/comments` after publish | **Done** `[main]` IG/X/YouTube; `[growth]` auto first-comment rules | — |
| Custom video covers | E+ | IG/FB/TikTok frame picker | Yes: `cover_url`, `thumb_offset` | **Done** `[main]` | — |
| Posting schedule | F | Channel › Settings › Posting Schedule: timezone (≈290 cities), Posting Goal (posts/week), per-day slots with hour/min, disable a day, Add slot for "Every Day" or a day, Clear All, Generate slots: Meet my goal / Buffer recommendations / Copy from channel. General tab: Pause Queue, Shuffle Queue (first 200), Clean Queue, Delete all drafts, Empty Queue, Bulk CSV upload | n/a | **Done** `[main]` slots, pause. `[pages]` goal, days off, generate slots, shuffle/clean/delete drafts/empty | Merge `feat/channel-pages`; "copy slots from channel" |
| Bulk CSV upload | F (beta) | Columns Text / Image URL / Tags / Posting Time (YYYY-MM-DD HH:MM, blank = queue), 100 per file | n/a | **Done** `[growth]` CSV import | — |
| Notify Me (reminder publishing) | F | Per post "Automatic" vs "Notify Me" (push to Buffer mobile app). Default per channel in General tab | n/a | **Done** `[main]` `posting_method=notify` | Real push (PWA/web push or Telegram) |
| Link shortening / UTM | F / UTM E+ | Toggle in TikTok/YouTube composers; disabled on IG ("Instagram does not allow posting of shortened links"). Bitly integration | n/a | **Done** `[growth]` short links + UTM builder + defaults | — |
| Start Page (link in bio) | F (counts as a channel) | Connectable "channel". Builder not inspected (connecting it would add a channel) | n/a | **Done** `[growth]` bio page editor, preview, analytics | — |
| Shop Grid / grid preview | F | IG Post field "Shop Grid Link" (URL per post) + per-channel "Grid" page `/channels/<id>/shop-grid` | n/a (own hosted page) | **Partial** sent-grid only; no per-post link page | Per-post link field + public clickable grid page (can live in the bio page) |
| Product tags | — | "Tag Products" chip on Post/Reel (as a reminder for Notify Me) | Yes, **Facebook Login only**, needs IG Shop + `instagram_shopping_tag_products`, `catalog_management` | **Missing** | Low priority |
| Threads (X/Bluesky/Threads/Mastodon) | F 1 / E+ unlimited | Beta flag "Threaded Posts" (off) | Yes | **Done** `[main]` thread support | — |
| Recurring posts | beta | Beta flag "Recurring Posts" (off) | n/a | **Done** `[growth]` recurring + evergreen automations | — |
| YouTube options | F | Title, Category (15), Visibility, License, Notify subscribers, Allow embedding, Made for kids, AI-generated. **Shorts only** | Yes | **Done** `[main]` youtube connector | — |
| Integrations | F | Zapier, Claude, ChatGPT, Notion, n8n, Cursor, Raycast, Perplexity, MCP via API key; media from Canva, Dropbox, Google Drive/Photos, OneDrive, Unsplash | — | **Partial** `[main]` MCP + API tokens; no Canva/Drive pickers | Google Drive / Dropbox pickers |
| Public API | F 1 key / E 3 / T 5 | Settings › API: personal keys, app clients, rate tiers 100/15 min, 500/24 h, 15,000/30 days (Team), 11 permissions | — | **Done** `[main]` scoped tokens, REST + MCP | — |

### Instagram composer fields (Buffer, live)

| Field | Post | Reel | Story | Note |
|---|---|---|---|---|
| Caption 2200, hashtag counter | ✅ | ✅ | — | |
| First comment | ✅ | ✅ | — | |
| Location | ✅ | ✅ | — | Help centre: needs Facebook Page connection to actually tag |
| Collaborators | ✅ "Add up to 5 Instagram usernames." | not shown | — | See section 2 |
| Music | text box "Add placeholder text for the music you'd like to include" | same | same | On this IG-login channel it is only a reminder note. See section 3 |
| Tag Products, Shop Grid Link, AI-Generated | ✅ | Tag Products | — | |
| Posting method default | Automatic | Notify Me | Notify Me | |

---

## 2. Instagram collaborators

**What Meta allows**

- `collaborators` is a parameter on `POST /{ig-user-id}/media`: "For Feed image, Reels and Carousels only. A list of up to 3 instagram usernames as collaborators on an ig media. **Not supported for Stories.**" (IG User Media reference).
- The read endpoint `GET /{ig-media-id}/collaborators` says "**Available for the Instagram API with Facebook Login**", needs `instagram_basic` + `pages_read_engagement`, and lists up to 5 collaborators with invite status. Only users who allow collaborator tagging are returned.
- Accepting invites (`GET/POST /{ig-user-id}/collaboration_invites`) is Facebook Login / Unified Login only; 300 calls per user per day.
- Usernames must be real, public accounts that allow collaboration. Buffer's help: "If Instagram rejects a collaborator, the post will fail to publish. You can't change collaborators after the post is published."

**Limit discrepancy.** The create parameter says **3**; the read endpoint says up to **5**. Buffer's help centre says **3**, but Buffer's live composer says "Add up to 5 Instagram usernames." Keep HuhQueue's current cap of 3 (`instagram.py` "Max 3 collaborators.") because that is what the publish call documents.

**What Buffer does.** Collaborators work only when the Instagram channel is "connected through a linked Facebook Page". With Instagram login, Buffer's connection table lists "❌ Collaborators" (help centre, Connecting your Instagram account).

**HuhQueue today.** `huhqueue/connectors/instagram.py` uses **Instagram API with Instagram Login** (`GRAPH = "https://graph.instagram.com"`) and still sends `collaborators` and `location_id` in the container. Meta documents collaborators (and location tagging, which needs the Pages Search API) for the Facebook Login path, and Buffer confirms neither works over Instagram login. Expect them to be ignored or rejected. **This is a live bug risk: the composer offers a field that may silently do nothing.**

**What to build**

1. Add a second Instagram connection: **Instagram API with Facebook Login** (`graph.facebook.com`, Page access token). Permissions: `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` (+ `instagram_manage_comments`, `instagram_manage_insights`; `business_management` / `ads_read` if the Page is reached through Business Manager). Store `auth_mode = "ig_login" | "fb_login"` on the channel.
2. Show Collaborators (and Location search, music) only when `auth_mode == "fb_login"`; otherwise show a "Reconnect through Facebook to use this" prompt, like Buffer's "Save & Link".
3. Validate: ≤3 usernames, not on Stories, strip `@`; after publish read `/{media}/collaborators` to show invite status.

## 3. Instagram music

**Short answer:** Yes, licensed music can now be attached to Reels through the official API. It needs the **Facebook Login** route (a Facebook Page linked to a Business or Creator account). It is not available with Instagram Login, and it is **Reels only**. `audio_name` only renames original audio and does not add music.

**Facts, with sources**

- `audio_name`: "**For Reels only.** Name of the audio of your Reels media. You can only rename once." Reels limitations: "**Music tagging is only available for original audio.**" (IG User Media reference). So `audio_name` cannot attach a licensed track.
- **Instagram Audio API** (Meta changelog, dated 1 June 2026): "lets your app search for and retrieve audio — original sounds from Reels and royalty-free music from Meta's Sound Collection — and attach it to Reels at creation time." Endpoints:
  - `GET /ig_audio?audio_type=music|original_sound&user_id={ig-user-id}&search_query=...` (no query = trending)
  - `GET /{ig-audio-id}?user_id=...` (metadata: title, display_artist, duration_in_ms, cover art, temporary `download_url` preview ~1.5 days, `is_ads_eligible`)
  - Attach on the Reel container: `audio_configuration={"audio_id":"…","audio_volume":1-100,"video_volume":1-100}`, then the normal `media_publish`.
- **Requirements** (Audio API page, "Before You Start"): "An Instagram Business or Instagram Creator account", "**A Facebook Page connected to that account**", a Facebook app with `instagram_basic` and `instagram_content_publish` granted via Facebook Login, a user access token.
- **Platform support:** "available on the Instagram API with Facebook Login and the Instagram API with Unified Login. **It is not supported on the Instagram API with Instagram Login.**"
- **Catalogue limits:** "returns audio that has been authorized for third party use. The available selection may vary from what appears in the native app." Previewing a Reel with the audio attached is not supported. Meta forum reports that search results can miss songs that exist in the app.
- Reading back: `media_url` is omitted for videos with licensed audio; `media_audio_type` returns `MUSIC` or `ORIGINAL_SOUND`.
- Business vs Creator: **both work**; Personal accounts cannot publish via API at all.

**What Buffer does**

- "Instagram Reels: you can add music in Buffer and still publish automatically, if your channel is connected through Facebook." Search or browse suggested tracks, preview, set music and video volume 0–100. Picker note: "Results are limited and may differ from Instagram." "Music starts at the beginning of the track" (no clip start). If a track becomes unavailable before publish, the Reel fails. Switching to Notify Me clears the track.
- Posts and Stories with music, TikTok trending audio, stickers: only via Notify Me (finish in the app).
- With Instagram login: "you'll be prompted to reconnect when you select **Music**" (Save & Link). That is why Ram's composer shows only the placeholder text box: `huhbyte.ai` is connected with Instagram login.
- Buffer's own API wraps the same Meta calls: `searchInstagramAudioCatalog`, `trendingInstagramAudio`, `instagramAudio` ("Requires Facebook Login. Instagram Login channels return ChannelRefreshRequired").

**What HuhQueue needs**

1. The Facebook Login connection from section 2 (same token, same permissions; no extra permission is listed for music).
2. `GET /api/channels/{id}/ig-audio?type=music|original_sound&q=` proxy to `/ig_audio`, returning id, title, artist, duration, cover, preview URL (cache briefly; previews expire).
3. Composer: "Add music" on **Reel only** when `auth_mode == fb_login`; preview player; two volume sliders; store `options.audio = {audio_id, title, artist, audio_volume, video_volume}`.
4. Connector: add `audio_configuration` to the REELS container. Before publishing, re-check `GET /{audio_id}` and fail with a clear message if gone (Buffer fails the post rather than publishing silently).
5. Keep the current reminder text box for Posts/Stories and for Instagram-login channels (Notify Me).
6. Meta App Review: the app already needs Advanced Access for `instagram_content_publish` to serve accounts it does not own. For Ram's own account, Standard Access with the account added in the App Dashboard is enough.

## 4. What Ram must do (to get collaborators, location and music)

1. Make sure `@huhbyte.ai` is a **Business or Creator** account (it is "Professional" already).
2. Create a Facebook Page for huhbyte (if none) and **link the Instagram account to it**: Instagram app › Settings › Accounts Center, or Facebook Page › Settings › Linked accounts › Instagram. Each IG account needs its own Page.
3. Make sure his Facebook profile has **Full control** of the Page (not just Business Manager partner access). If the Page enforces 2FA, turn on 2FA on his Facebook profile. Complete Page Publishing Authorization if Facebook asks.
4. **In Buffer:** Channels › huhbyte.ai › refresh / reconnect **"Connect Instagram through Facebook"**, log in with that Facebook profile and **leave every permission ticked for every Page and IG account**. Drafts and scheduled posts stay (use the refresh, not remove + re-add).
5. **In HuhQueue:** after the Facebook Login connector is built, reconnect the channel the same way. Our Meta app will need Facebook Login for Business added and the permissions above.

---

## 5. Gaps HuhQueue still has, ranked

Ranked by value for Ram's Instagram-first channel, then by effort. "Branch done" items only need merging.

| # | Gap | Why it matters | Effort |
|---|---|---|---|
| 1 | **Instagram via Facebook Login** connector (`graph.facebook.com`, Page token) | Unlocks music on Reels, real collaborators, location tagging, liking mentions, `story_insights` webhook, `total_*` metrics, resumable upload. Today's collaborators/location fields are likely no-ops on Instagram login | M |
| 2 | **Music on Reels** (Audio API: search, trending, preview, volumes, `audio_configuration`) | Buffer's newest headline IG feature (changelog 7 Oct 2026); trending audio lifts Reels reach | M (after #1) |
| 3 | **Merge `feat/teams`, `feat/growth`, `feat/channel-pages`** | Team users, per-channel access, approvals with notes, 2FA, link in bio, short links/UTM, campaigns, automations, posting goal, queue actions all exist only on branches | S (merge + test) |
| 4 | **AI reply suggestions** in Community | Buffer charges for unlimited; we can give it free. Needs the LLM key we already use | S |
| 5 | **Mentions tab** (IG `/tags` + `mentions` webhook + reply) | Buffer has it on Free; we have nothing on main | S–M |
| 6 | **Collaborator UX fix** now: hide or warn on Instagram-login channels, read invite status after publish | Avoid silent failures | S |
| 7 | **Audience demographics** (`follower_demographics`, `engaged_audience_demographics`) | Buffer's marketing promises "audience" analytics; Meta API supports it | S |
| 8 | **Story insights** before expiry | Story metrics vanish after 24 h | S |
| 9 | **Channel groups** | Essentials feature, nothing in any branch | S |
| 10 | Shop Grid (per-post link + public grid page) | Link-in-bio traffic from posts; fits into the bio page | S–M |
| 11 | AI "Generate ideas", comment insights, comment→post | Nice-to-haves Buffer gives on Free | S each |
| 12 | Media pickers (Google Drive, Dropbox, Canva) | Convenience | M |
| 13 | Product tags | Needs Instagram Shop + Facebook Login; not relevant to huhbyte now | M |

## 6. Not verified in this pass

- Start Page builder (blocks, themes, custom domain): not opened, connecting it would add a channel.
- Approve / Reject / Request changes buttons on a real pending post: none existed (0 approvals).
- Post-generate AI Assistant actions (rephrase, shorten…): not triggered to avoid using AI on the account.
- X and LinkedIn composers: those channels are not connected.
- Buffer's live music picker: hidden because `huhbyte.ai` is connected with Instagram login (that is itself the answer to the Facebook Page question).
