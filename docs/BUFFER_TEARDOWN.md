<!-- Copied from the Buffer UX study (22 Oct 2026). Email addresses removed. Read-only observation of Buffer's public app; no Buffer assets are used in HuhQueue. -->

# Buffer UX Study — Notes

Date: 2026-10-22
Observer: read-only; no publishes, no deletes, no setting changes, no trial started.
Account: [email removed] on the Free plan.
Connected channels at time of study: huhbyte (TikTok, 5 scheduled) and huhbyte.ai (Instagram Professional, 5 scheduled). Sidebar shows "2/3 channels connected."

Note on file location: the parent task asked that findings live at
/workspace/projects/78998fff-515c-45f4-88a4-8bbf210164e8/buffer-study/notes.md
but the notes tool only accepts paths relative to its own notes directory and
rejects absolute paths. This file was saved here (the buffer-study notes
directory); to place it at the agreed project path, copy it with the shell:

  cp /workspace/web/notes/78998fff-515c-45f4-88a4-8bbf210164e8/buffer-study-tu_rxgson2onusoycnmf7h7dc6eiw/notes.md \
     /workspace/projects/78998fff-515c-45f4-88a4-8bbf210164e8/buffer-study/notes.md

The 11 screen PDFs and the connect-channels fragment were saved alongside this file
in the same notes directory.

## Screenshots / PDFs captured

PDFs saved to the same directory (paths relative):
- home-screen.pdf — /publish.buffer.com/home
- schedule-list-view.pdf — /schedule (all-channels List view)
- schedule-calendar-week.pdf — /schedule/calendar/week
- schedule-calendar-month.pdf — /schedule/calendar/month
- channel-huhbyte-ai-calendar.pdf — /channels/…/schedule (per-channel List)
- channel-huhbyte-ai-community.pdf — /channels/…/community
- channel-huhbyte-ai-insights.pdf — /insights/<channelId>
- composer-instagram.pdf — New Post opened from huhbyte.ai queue
- connect-channel-dialog.pdf — Connect Channel modal grid
- connect-youtube-google-account-chooser.pdf — Google account chooser (YouTube OAuth consent boundary)
- settings-channels.pdf — Settings > Channels (plan context + connected list)

## 1. Screen-by-screen UX notes

### Layout shell (common to every screen)
- Left sidebar with fixed vertical nav: publish.buffer.com logo, "New" global action button, then nav links Home / Create / Publish / Community / Insights.
- Below nav: the Channels section — each connected channel shown with its name, avatar, count of scheduled posts (e.g. "huhbyte 5 scheduled posts"), plus quick "New post" and a chevron sub-menu per channel (submenu expands Publish / Community / Insights sub-nav).
- Below channels: "Connect more channels" pill row — one-button connectors for Threads, LinkedIn, Bluesky and a "More channels" expander.
- Top-left "My organization" user menu (shows "free plan").
- Top-right "Close sidebar" chevron collapses the left rail.
- Top chrome on content area: Share Feedback, sync status indicator, "About Week Streak / Posting Goals / Comment Score" info tooltips.
- Bottom-left "Help center" FAB.
- Impression: compact, consistent shell; every page keeps the same nav and channel list. Fast and polished.

### Home (/home)
- Greeting: "Good Afternoon, <user>!" + date.
- "Your scorecard": three mini-sparkline charts — Week Streak, Posting Goals, Comment Score (each with its own "About" info).
- "First Steps" onboarding row: 3 cards, each with a "Mark as done" toggle and a primary CTA — 1. Connect a channel → Connect Channel, 2. Create a post → Create Post, 3. Explore Buffer API → Get Started.
- "Up Next": next scheduled posts with channel avatars and Edit buttons (e.g. Tomorrow 07:30 TikTok Spacesheep, Tomorrow 07:30 Instagram Spacesheep, Tomorrow 13:00 TikTok Flint).
- "Comments": 2 unanswered comments/mentions, each row links to reply.
- "Templates": 4 post-template cards.
- "From the Blog": 4 recent Buffer blog articles.
- Quick actions visible from Home: New global button, First Steps CTAs, Edit post, Open comment.
- Polished: clear visual hierarchy, onboarding is contextual not intrusive.
- Friction: none on Home itself.

### New (global) button menu
Items: Post (publish content to a channel), Idea (capture a content idea), Connect a New Channel, Create a Start Page, Invite a Team Member.
- "Invite a Team Member" is the upsell cue (only Team plan has teammates).

### Publish queue — List view (/schedule)
- Header row: "Save current view", Share Feedback, List/Calendar view toggle, New Post button.
- Status tabs: Queue (10 posts) | Drafts (0) | Approvals ("Upgrade") | Sent (6). **Approvals is locked/upsold** on Free.
- Filter controls: Filter by Channels, Filter by Tags, Timezone (currently Dublin).
- Queue body: chronological cards grouped by day. Each card: channel avatar, schedule time, "Custom" badge, post text with hashtags as links, inline action row: Add tags, Publish Now, Edit post, More actions.
- "see more" expander on long captions.
- Polished: clear chronological list, inline edit, inline Publish Now.
- Friction: "Approvals Upgrade" tab is a persistent upsell.

### Publish queue — Calendar view, Week (/schedule/calendar/week)
- Same header; "View" dropdown toggles Week/Month.
- Week calendar: Sun–Sat columns with hour rows. Posts render as small buttons (e.g. "tiktok post scheduled for 23 October Friday 2026, 13:00"). Clicking an empty cell schedules a post for that slot.
- Nav: Previous Week / Next Week / Today.
- Filters: Channels, Status, Tags, "Show No Date drafts", Timezone (Dublin), "Posting times options".
- Polished: click-to-schedule on empty cells is a strong pattern.

### Publish queue — Calendar view, Month (/schedule/calendar/month)
- Standard monthly grid; Previous Month / Next Month / Today nav.
- Days show up to 3 posts inline, then an "N More" expander (e.g. "3 More" on Friday 23, "1 More" on Thursday 22).
- Friction: heavy days collapse behind "N More"; not at-a-glance.

### Channel page — huhbyte.ai (per-channel sub-nav)
- Sidebar channel row for huhbyte.ai expands to a sub-menu: **Publish (5 scheduled posts) | Community (2 unanswered) | Insights** — the channel exposes only these three tabs (the global Publish queue's Queue/Drafts/Approvals/Sent tabs are not a separate per-channel rail set; they live inside the channel's Publish view itself).
- Channel-level Publish (Queue List): /channels/<id>/schedule. Header shows a banner "5 Posts left to schedule on the Free plan." + primary "Upgrade for Unlimited Posts" CTA (**upsell**). "3/5 posts sent this week" goal pill. Instagram-specific "Grid" link (shop-grid / profile grid preview).
- Channel-level status tabs: Queue (5) | Drafts (0) | Approvals ("Upgrade") | Sent (3).
- Filters: Tags, Post Type, timezone link, More actions. Inline empty "New" queue slot (clickable to add a post at a position).
- Drag-to-reschedule handle on each card.
- Channel-level Community: /channels/<id>/community → By-post / List toggle; tabs: Comments (2 unanswered) | Mentions. Per-post reply textarea pre-filled with "@username". Reply controls: emoji selector, Open saved replies, Start a Post, Reply (primary), Resolve this comment, Bulk resolve, Filter and sort.
- Channel-level Insights: /insights/<id>. "Weekly Takeaways" AI summary at top. Date-range radios: 7 Days | 30 Days | Month to Date | Custom ("Upgrade" — **Custom range upsold**).

### Composer — New Post (from Instagram / huhbyte.ai queue)
- Opens as a wide side panel. Top bar: Templates | AI Assistant | Preview (Preview pressed/active by default) | Enter fullscreen | Close Composer.
- Channel selector ("Add channel" combobox): lists connected channels (huhbyte tiktok, huhbyte.ai instagram) plus a "Connect a Channel" entry and "Search channels by name or location". Multi-select capable.
- Post type row (Instagram-specific radio group): **Post | Reel | Story** (Post selected by default).
- Body: rich-text editor with "Browse templates" inline trigger.
- Media: "Select to upload files / Drag & drop" upload button + "More integration options" grouped-integrations dropdown.
- Caption toolbar chips: emoji picker, "Upgrade to use Hashtag Manager" (locked chip — **upsell**), Music, Tag Products (Instagram-specific).
- Posting method dropdown ("Automatic"): Automatic (Buffer posts for you) | Notify Me (mobile notification to post yourself).
- "Your comment" row: **locked** — label "Your comment / Upgrade" (class lockedControl — **First comment scheduling upsold on Free**).
- Collaborator row: "Enter an Instagram username" (IG co-author).
- AI content disclosure toggle switch.
- Bottom bar: Save Draft (disabled until content) | "Next Available" schedule selector (dropdown — scheduling driven by this selector, defaulting to the next available slot; no separate calendar picker visible in this state) | Schedule Post (primary, disabled until content).
- Impression: very feature-dense; preview-pane default is nice. Multiple locked/Upgrade controls in one composer are the clearest friction on Free (Hashtag Manager, Your comment/first comment).

### Create / Ideas (/create/ideas)
- Default view is a **Kanban board**: columns Unassigned / To Do / In Progress / Done. Column titles content-editable ("Rename To Do column").
- Top tab strip: Ideas | Templates | Feeds.
- Header actions: Save current view, Share Feedback, Generate Ideas (AI), New Idea. Filter by Tags. Board/Gallery view toggle. "New Group" to add board groupings.
- 100-ideas limit on Free (see pricing); drafts unlimited.
- Polished: Kanban is clean; rename-in-place is snappy.

### Settings
- /settings/channels is the default landing. Narrow left column with grouped sections, full page on the right.
- Left rail sections:
  - Account: Profile, Preferences, Notifications
  - Organization: General, Role & Permissions
  - **Channels (2)** — active by default, shows connected channels + "Connect Channel" primary button + "Upgrade Plan" CTA + "Get to know your plan" note ("You are on the Free plan and can connect up to 3 channels.")
  - Billing
  - Features: Tags, Channel Groups ("Upgrade" — upsold), Saved Replies
  - Other: API ("New" badge), Apps & Extras, Beta Features, Refer a Friend, Integrations ("New")
- Per connected channel: "Settings for <channel>" link + a "More options" kebab.
- Impression: well-organized, consistent. "Channel Groups Upgrade" and "Upgrade Plan" pill are the upsell surfaces here.

## 2. Connect-channel flow

### Entry point
From Settings > Channels, "Connect Channel" opens a modal grid of provider cards.
Each card = provider logo + name + account-type subtitle. Providers offered:
Instagram (Business, Creator, or Personal), Threads (Profile), LinkedIn (Page or Profile), Facebook (Page or Group), Bluesky (Profile), Substack (New, Profile), YouTube (Channel), TikTok (Business or Personal), Mastodon (Profile), Pinterest, Google Business, Twitter / X, Start Page, plus "Request a Channel".

### Instagram connect flow (Professional path) — stopped at provider login/consent, did NOT connect
1. **Buffer screen — Connect Channel dialog**: click "Connect Instagram".
2. **Buffer screen — "How would you like to connect your Instagram Account?" modal** (Buffer advertises capabilities by account type; it does NOT list OAuth scopes here).
   - Professional (Business & Creator) — "Automatic & Notification-based Posting". Features: Automatic posting (you schedule, we post), Notifications (we notify, you finish in app), Community (reply to comments), Sent Post Metrics (view past post performance). CTA: **Connect to Instagram**. Note: "Instagram will prompt you to easily convert to a professional account if needed."
   - Personal — "Notification-Based Posting Only". Feature: Notifications only. CTA: **Setup a Personal Account**.
   - Tertiary: "Connect Instagram through Facebook" (if IG linked to Facebook).
3. **Buffer screen — "First, Check Your Instagram Account"** pre-flight modal. Primary: **Open Instagram**. Secondary: **Skip Check and Connect to Instagram** (relabels to **Connect to Instagram** on return).
4. **Provider: instagram.com login page** (opened by the "Open Instagram" button in a popup). Standard Instagram login — "Log into Instagram", Mobile number/username/email + Password, Log in button (disabled until filled), "Forgot password?", "Log in with Facebook", "Create new account". No Buffer OAuth scope disclosure at this step; login is required first. Closed the popup without entering credentials.
5. **Provider: Instagram third-party OAuth authorize endpoint** (navigated to by the direct "Connect to Instagram" button). Returned HTTP 429 ("This page isn't working") — Meta rate-limited due to rapid successive requests — so the rendered consent screen did not display. The authorize URL itself, however, fully exposes the scopes Buffer requested:

   ```
   https://www.instagram.com/oauth/authorize/third_party/
     ?redirect_uri=https%3A%2F%2Fcallbacks.buffer.com%2Fchannels%2Foauth%2Finstagram_login_api%2Fcallback
     &response_type=code
     &scope=business_basic,business_content_publish,business_manage_comments,instagram_business_manage_insights
     &state=<opaque>
     &enable_fb_login=1
     &client_id=1531617227440535
     &force_reauth=0
   ```

   **Buffer's Instagram scopes requested (4, comma-separated in the URL)**:
   - `business_basic` — basic business/profile info
   - `business_content_publish` — publish posts/content
   - `business_manage_comments` — manage comments (community inbox)
   - `instagram_business_manage_insights` — read post/channel insights

   Callback: `https://callbacks.buffer.com/channels/oauth/instagram_login_api/callback` (returns into Buffer).

6. Backed out to Buffer. No channel connected, no credentials entered.

### YouTube connect flow — stopped at Google account chooser, did NOT connect
1. **Buffer screen — Connect Channel dialog**: click "Connect YouTube" (subtitle "Channel").
2. Buffer performs its own OAuth init and **redirects directly to Google** — there is no intermediate Buffer consent screen for YouTube (contrast with Instagram's two intermediate modals).
3. **Provider: Google account chooser** (`accounts.google.com/v3/signin/accountchooser`).
   Header: "Sign in with Google — Choose an account to continue to Buffer". Lists accounts in this browser (all Signed out at time of study: [email removed], [email removed], [email removed]) and a "Use another account" option. Footer: Buffer Privacy Policy + Terms of Service, language (English US), Google Account Help/Privacy/Terms.
4. **Buffer's YouTube/Google scopes requested** (visible in the OAuth request URL; Google's standard OAuth2 V2 style, prompt=consent, offline access):
   - `https://www.googleapis.com/auth/youtube` — full YouTube Data API access (read/write channel & content)
   - `https://www.googleapis.com/auth/youtube.upload` — upload videos (automatic posting)
   - `https://www.googleapis.com/auth/youtube.force-ssl` — force HTTPS (legacy scope, still requested)
   - `https://www.googleapis.com/auth/yt-analytics.readonly` — read YouTube Analytics
   Other params:
   - `client_id = 1002993234984-37nprn0hicsijr4eqtdr18qi4gu4k33u.apps.googleusercontent.com`
   - `access_type = offline` (Buffer requests a refresh token for background posting)
   - `prompt = consent` (forces the consent screen)
   - `response_type = code` (Authorization Code flow)
   - `redirect_uri = https://callbacks.buffer.com/channels/oauth/youtube/callback`
   - `app_domain = https://callbacks.buffer.com`
5. Selecting an account would then surface the standard Google consent screen listing those four scopes with a Continue to Buffer button. **Stopped here** — no account selected, no sign-in, no consent granted.
6. Return destination (if consent were granted): `https://callbacks.buffer.com/channels/oauth/youtube/callback`, then back into publish.buffer.com.

### Flow comparison (for HuhQueue design reference)
- **Instagram**: Buffer shows two intermediate modals (account-type chooser + sign-in pre-flight check) before leaving to the provider. Scopes are NOT surfaced on a Buffer screen; they live only in Instagram's OAuth authorize URL. Uses Meta's Instagram third-party OAuth; 4 relatively narrow business-scoped permissions.
- **YouTube**: Buffer skips any explanatory modal and redirects straight to Google's account chooser + consent. The 4 scopes are visible in the Google OAuth URL and are re-listed on the Google consent screen. Broadest scope is `youtube` (full YouTube Data API), plus upload, force-ssl, yt-analytics.readonly. Offline access requested.

## 3. Per-channel data for the connected Instagram channel huhbyte.ai

Where Buffer exposes huhbyte.ai data:
- **Channel list (sidebar + Settings > Channels)**: name "huhbyte.ai", avatar, platform labeled "Instagram Professional Account", scheduled-post count (5). Per-channel "Settings for huhbyte.ai" + kebab "More options".
- **Rail sub-nav**: Publish (5) | Community (2) | Insights — three tabs per channel.
- **Publish/Queue**: 5 scheduled Instagram posts (Queue tab), 0 Drafts, Approvals locked, 3 Sent. Filters include "Filter by Post Type" (Post/Reel/Story-aware). "3/5 posts sent this week" goal pill. Instagram-specific "Grid" link (profile grid preview). Drag-to-reschedule handles.
- **Community (comments)**: 2 unanswered comments currently. By post (left post grid) / List toggle; tabs: Comments | Mentions. Per-comment: reply textarea pre-filled with the mention, emoji picker, saved replies, "Start a Post" (turn comment into post), Reply primary, Resolve, Bulk resolve, Filter and sort. Original post shown with Instagram media; "Open in new tab / Copy link / Open Thread" link back to instagram.com. **Buffer pulls live comments and lets you reply from Buffer for Instagram Professional.**
- **Past posts**: yes — sent posts appear in the channel's Sent tab (3 sent for huhbyte.ai), and Insights > Performance per Post lists past posts with their content, date, and type.
- **Profile stats**: yes — Insights shows a follower count (Total Followers: 12 for huhbyte.ai in the 30-day summary).
- **Analytics/Insights pulled by Buffer for huhbyte.ai** (30-day summary, Sep 23 – Oct 22 2026 vs prior period):
  - Total Followers: 12
  - Posts: 2
  - Reactions: 10
  - Comments: 2
  - Engagement Rate: 8.03%
  - Views: 425
  - Shares: 6
  - Saves: 0
  - Follows gained from posts: 0
  - Reach: 249
  - Watch Time (min): 43.44
  - Avg. Watch Time (sec): 14.9
  - "Performance per Post" table: #1 "Your AI has its own computer now…" — Oct 21, 2026 — **Reel** — 7 reactions, 1 comment, 6.11% Eng. Rate, 322 views. #2 "My keyboard is about to lose its job… Wispr Flow" — Oct 22, 2026 — **Reel** — 3 reactions, 1 comment, 30.00% Eng. Rate, 103 views. (Buffer labels post type.)
  - "Weekly Takeaways" AI summary at top ("Reply to Your Comments — You have 2 comments you haven't replied to.").
  - "Metrics" charts with segment control: Posts | Content Impact | Audience Growth | Visibility | Follower Growth Rate. Content Impact plots Posts and Followers over time with This Period / Comparison / Both toggle.
  - Date-range presets: 7 Days, 30 Days, Month to Date. **Custom range is "Upgrade"** (upsold on Free).
  - Filters: by Tags, by Post Type.
  - "Share as Post for Summary / Metrics" buttons (surface Insights as a post).
  - Export button in the header.

**Bottom line on what Buffer pulls for a connected Instagram Professional channel**: yes to past posts (Sent + Performance-per-Post), yes to profile stats (Total Followers), yes to community/comments (live feed with reply), yes to analytics/insights (reactions, comments, engagement rate, views, shares, saves, follows-from-posts, reach, watch time, avg watch time, plus AI takeaways and multiple chart views).

## 4. Pricing — buffer.com/pricing (Free / Essentials / Team)

Pricing model: **per-channel** (pay only for channels you connect). A channel-count stepper sits at the top; billing toggle Monthly / Yearly (Yearly selected by default, saves 2 months). Default observed at 1 channel:
- **Free**: Free forever — "Let's go" CTA (signs up / logs in; no trial)
- **Essentials**: **$5/month** per channel ($60 billed yearly at 1 channel) — "Recommended", 14-day free trial
- **Team**: **$10/month** per channel ($120 billed yearly at 1 channel) — 14-day free trial

Pricing last updated November 2025. Channels 1–10 priced at the standard rate; additional channels above 10 cost less per channel (total increases, average per channel goes down).

### Top-card "What's included" summary
| Feature | Free | Essentials | Team |
|---|---|---|---|
| Scheduled posts per channel | 10 (refill anytime) | Unlimited | Unlimited |
| Ideas | 100 | Unlimited | Unlimited |
| User accounts | 1 | 1 | Unlimited team members |
| AI Assistant | Included | Included | Included |
| Insights / Analytics | Insights (basic) | Advanced analytics | Advanced analytics |
| API access | 1 API key, 3,000 req/mo | 3 API keys, 7,500 req/mo | 5 API keys, 15,000 req/mo |
| Community inbox | Included | Included | Included |
| Hashtag manager | — (Not included) | Included | Included |
| First comment scheduling | — (Not included) | Included | Included |
| Access levels | — | — | Included |
| Content approval workflows | — | — | Included |
| World-class customer support | Included | Included | Included |

### Detailed feature matrix (Feature comparison table)

**Content creation**
- Ideas: 100 / Unlimited / Unlimited
- Tags: 3 / 250 / 250
- Drafts: Unlimited / Unlimited / Unlimited
- Templates: Included / Included / Included
- AI Assistant: Included (unlimited credits) / Included / Included
- Board view (kanban): Included / Included / Included
- Browser extension: Included / Included / Included

**Publishing**
- Scheduled posts per channel: 10 / Unlimited / Unlimited (paid "unlimited" subject to a 5,000 posts/channel fair-use cap)
- Threaded posts (X, Bluesky, Threads, Mastodon): 1 / Unlimited / Unlimited
- Queue (re-arrange/shuffle): Included / Included / Included
- Visual content calendar (week/month): Included / Included / Included
- Reminder notifications (mobile nudge to post natively; IG, TikTok, YouTube): Included / Included / Included
- Start Page (link-in-bio; counts as a channel): Included / Included / Included
- Duplicate posts: Included / Included / Included
- Link shortening: Included / Included / Included
- First comment scheduling (IG, FB, LinkedIn): **Not included / Included / Included**
- Channel groups (bulk selection): **Not included / Included / Included**
- Hashtag manager: **Not included / Included / Included**
- Custom video covers (FB, IG, TikTok): **Not included / Included / Included**

**Community**
- Reply to comments: Included / Included / Included
- Comment score: Included / Included / Included
- AI replies: 5 suggestions/week / Unlimited / Unlimited
- Saved replies: 1 saved reply / Unlimited / Unlimited
- Comment insights: Included / Included / Included
- Turn comments into posts: Included / Included / Included

**Analytics**
- Insights history: 30-day / Unlimited / Unlimited
- Performance overview (period-over-period summary): **Not included / Included / Included**
- Custom analytics (filter by tag/channel): **Not included / Included / Included**
- Takeaways (AI plain-language observations): Included / Included / Included
- Reports export (CSV, PDF, markdown): **Not included / Included / Included**
- Custom UTM parameters: **Not included / Included / Included**
- Branded reports (logo + cover page): **Not included / Not included / Included**

**Collaboration**
- Included users: 1 / 1 / Unlimited
- Two-factor authentication: Included / Included / Included
- Notes (internal comments on content): **Not included / Included / Included**
- Approval workflows: **Not included / Not included / Included**
- Custom access and permissions: **Not included / Not included / Included**

**Integrations** — identical across all three plans (all Included): Bitly, Canva, ChatGPT, Claude, Cursor, Dropbox, Google Drive and Photos, IFTTT, Make, n8n, One Drive, Perplexity Web, Raycast, Unsplash, WordPress, Zapier.

**API**
- API keys: 1 / 3 / 5
- App clients (OAuth): 1 / 3 / 5
- 15-min limit: 100 / 100 / 100
- 24-hr limit: 250 / 250 / 500
- 30-day limit: 3,000 / 7,500 / 15,000

**Support** — Self-service knowledge base and Email support: Included on all three plans.

### Live-app upsells / locks observed (and which plan unlocks them)
- "Approvals" tab labeled **Upgrade** → Approval workflows (Team only).
- "Your comment" composer field labeled **Upgrade** → First comment scheduling (Essentials+).
- "Upgrade to use Hashtag Manager" composer chip → Hashtag manager (Essentials+).
- "Custom" date-range radio in Insights labeled **Upgrade** → Custom analytics range (Essentials+).
- "Channel Groups" in Settings labeled **Upgrade** → Channel groups (Essentials+).
- "Invite a Team Member" in New menu + "Upgrade Plan" CTA in Settings → Team plan.
- Per-channel queue banner "X Posts left to schedule on the Free plan. Upgrade for Unlimited Posts." → Free's 10-scheduled-post/channel cap.
- Free plan channel cap: "You are on the Free plan and can connect up to 3 channels" (2/3 shown in rail).
- "Start 14-day free trial" CTAs on Essentials and Team cards were observed but not clicked.

---

# Part 2 — YouTube channel study (Ram, 22 Oct 2026)

## 3. YouTube channel – Community tab
URL: /channels/6ada1715778b240e2f8c7800/community/posts (default when clicking Community in channel submenu; the nav goes to /community/posts).

Chrome: Save current view, Score (Comment Score 0 bar), Share Feedback, "About sync status" tooltip, plus a right-side radiogroup By post | List (posts view default; "Switch to comments view" link → /community which is the comments-view URL).

Tablist: Comments | Mentions

**Comments tab** (default selected)
- When the "By post" radiogroup = Posts view (URL ends /community/posts):
  - Toolbar: "Bring posts with the newest unanswered comments to the top." (reorder), "Show all posts, including those with no comments", "Collapse posts panel".
  - Header row: status filter "All", "Bulk resolve" menu.
  - Empty state: heading "No posts found", body "When your posts receive comments, they'll appear here. Want to see all your posts?", button "Show All Posts".
  - Side panel empty state: heading "We're listening for new comments", body "We'll let you know when someone comments on your posts. In the meantime, keep exploring:", with two action cards — "Post something new / Use templates to create content that starts conversations. ✨ Pro tip: Ask a question" and "Connect more channels / Manage conversations from all your channels from a single, distraction-free inbox."
- When the "By post" radiogroup = List / comments view (URL /community):
  - Same Comments/Mentions tablist. The Comments tab's menu_options (hover) now lists fewer helpers: only the two action cards ("Post something new", "Connect more channels").
  - Same two action cards render; no comments exist for this channel yet.
  - Right-side buttons: Filter by status, Filter and sort comments, Bulk resolve (all present but operate on the empty set).

**Mentions tab**
- Empty state locked for YouTube: heading "Mentions are available only for selected channels", body "We're actively working on expanding this list. Connect one of the supported channels to see mentions."
- Three "Connect <platform>" buttons shown: Connect X (Twitter), Connect Instagram, Connect LinkedIn Profiles.
- Mentions menu_options lists: "Connect X (Twitter) | Connect Instagram | Connect LinkedIn Profiles".
- YouTube is NOT supported for Mentions (Buffer's limitation, not a paid-plan gate).

## 4. YouTube channel – Insights
URL: /insights/6ada1715778b240e2f8c7800

Chrome: Save current view, Channel settings (link), Share Feedback, Sync status, Export (menu), plus a top banner card.

**Top banner ("A Path to Growth with Weekly Takeaways")**
- Heading: "A Path to Growth with Weekly Takeaways"
- Body: "The more you post, the better your takeaways. Share a few posts and we'll start showing you weekly insights to help you grow."
- CTA: primary button "Create Post".
- (This is a free-plan educational banner; the weekly takeaways panel below is empty because no posts were sent in the period — not paywalled.)

**Date-range radiogroup** (segmented control, 5 options):
- 7 Days (radio)
- 30 Days (radio, selected by default)
- Month to Date (radio)
- Custom / Upgrade — locked: the radio label is "Custom" with a subtitle "Upgrade"; clicking opens an upgrade dialog. Custom date ranges are behind a paid plan.

Other filters: "Filter by Tags" combobox (standard Buffer tag filter, not YouTube-specific).

**Summary panel**
- Subtitle: "Sep 23 - Oct 22, 2026 · Compared to Aug 24 - Sep 22, 2026"
- Two ghost buttons row: "Share as Post for Summary", "View All Sent Posts" (links to Sent tab).
- Metric chips (each has an "About <metric>" info tooltip):
  - Total Followers → value 0
  - Posts → 0
  - Reactions → 0
  - Comments → 0
  - Eng. Rate → 0%
  - Video Views → 0  (YouTube-specific metric, surfaced on the channel's Insights)

**Performance per Post**
- Subtitle "Sep 23 - Oct 22, 2026", period toggle This Period | Previous Period.
- Empty: "No posts found in the last 30 days. Try another date range."

**Metrics panel**
- Subtitle "Sep 23 - Oct 22, 2026 · Compared to Aug 24 - Sep 22, 2026"
- Ghost button: "Share as Post for Metrics"
- Left axis selector: a button "Posts" next to "Change metric, currently Posts" combobox (lets you pick the secondary series).
- Metric tabs (segmented/tabbar, role=radiogroup):
  - Content Impact — "Posts and Followers. Shows whether your posting activity is growing your audience." (selected)
  - Audience Growth — "Followers Over Time. Shows whether your audience is growing."
  - Visibility — "Profile Views and Followers. Shows how many people who discover your profile actually follow you."
  - Follower Growth Rate — "Growth Rate and Buffer Median. Shows where you stand compared to similar accounts."
- Empty-state line in the chart area: "You haven't been active during this period. Review your [takeaways] to get started!" plus "Posts 0 / Followers 0".
- Chart view period radiogroup: This period | Comparison period | Both periods.
- Chart shows "Content impact: posts and followers over time" (recharts SVG, empty). "Show data table" toggle present.
- (No Insights feature here is paywalled on the YouTube channel beyond the Custom date range; the "Weekly Takeaways" panel itself is just empty due to no data.)

## 5. Create / New post — YouTube only (Post Composer)
Opened via the YouTube channel's "New Post" button on /channels/…/schedule (the channel-specific composer; no other channel is selected — the composer opened with Ram prasad Somaraju pre-selected).

**Composer chrome (top)**
- Left: "Post Composer" card with body "Use the composer to draft, schedule or publish a post." / "Create Post" / "Create and edit posts".
- Side panel toggles (segmented): Tags (button), Templates (toggle button), AI Assistant (toggle button), Preview (toggle button, pressed=true on open — preview shown by default).
- Right: Enter fullscreen (icon), Close Composer (×).

**Channel selector row**
- "Select channels" label.
- "Add channel" button (combobox) — secondary, large, to add another destination. Ram prasad Somaraju YouTube avatar is the only selected destination.

**Post type** (YouTube-specific)
- A single radio button labelled "Short" (name=youtubepostType, value=short, checked=true).
- Only one radio is rendered — there is NO "Video" toggle alongside it on this composer for this channel. The YouTube post type is surfaced as a single "Short" radio, pre-checked. (Buffer currently surfaces Shorts scheduling; a long-form "Video" radio is not present in this composer.)

**Body / description**
- Rich-text composer (slate-editor, role=textbox, testid=composer-text-area).
- Placeholder text: "Start writing or get inspired with Templates".
- Inline "Browse templates" ghost button inside the empty editor.

**Media row**
- "Select to upload files" button (upload trigger) with helper "Drag & drop or select a file".
- "More integration options" button (dropdown, testid=grouped-integrations-button): options are Dropbox, Google Drive, Google Photos, OneDrive. (These appeared; when no media was attached, the button became non-clickable / pointer-events:none for some integrations in later turns — the menu contents are as listed.)
- "Select emoji" button.
- "Upgrade to use Hashtag Manager" button — this is the hashtags control, locked behind a paid plan.
- "Link shortening" button (link-shortener toggle, present in the toolbar).
- Character counter at right edge of the toolbar showing "5000" (the YouTube description limit; editor is empty, so 5000 represents the remaining/limit).

**Validation / required-media behaviour**
- No inline validation messages (no "file required" error text) are rendered while the editor is empty.
- "Save Draft" button is DISABLED while empty.
- "Schedule Post" button is DISABLED while empty.
- Implied requirement: a video file must be added (and presumably title) for the post to become actionable — both action buttons are disabled and the YouTube Preview panel shows only its placeholder.

**Right-hand "YouTube Preview" panel**
- Heading "YouTube Preview" with info icon.
- Empty state: grey placeholder card and the line "See your post's preview here".
- (The preview panel is on by default because the Preview toggle is pressed=true.)

**Posting method** (YouTube-specific scheduling behaviour)
- Button labelled "Automatic" (posting-method-trigger) at the top of the YouTube-specific block.
- Dropdown (role=menu) with radio items:
  - "Automatic" — "Buffer will post automatically for you." (selected by default)
  - "Notify Me" — "You'll receive a mobile notification to post yourself."
- (This is the YouTube-publishing flow: Automatic pushes the upload via Buffer's YouTube API; Notify Me hands off to the mobile app so the user publishes manually from the phone. No third "Manual" option.)

**YouTube metadata fields** (below the posting method, in a two-column grid, then a checkbox row, then AI row, then footer)

- **Title** — text input, placeholder "Enter a title for your video". Empty by default. (Buffer's only title control for YouTube.)
- **Category** — combobox (role=combobox), default "Autos & Vehicles". Full option list (15 values):
  Autos & Vehicles (selected), Comedy, Education, Entertainment, Gaming, Howto & Style, Film & Animation, Music, News & Politics, Nonprofits & Activism, People & Blogs, Pets & Animals, Science & Technology, Sports, Travel & Events.
  (These match YouTube's standard upload categories. Notably absent: a "Tags" keyword field — Buffer routes tags through the composer's top "Tags" side-panel / "Hashtag Manager" (paid) rather than a YouTube keyphrase box.)
- **Visibility** (Privacy) — combobox, default "Public". Options: Public, Unlisted, Private.
- **License** — combobox, default "Standard YouTube License". Options: Standard YouTube License, Creative Commons - Attribution.
- **Checkbox row** (three checkboxes, role=checkbox):
  - Notify Subscribers — checked by default
  - Allow Embedding — checked by default
  - Made for Kids — unchecked by default
- **AI-Generated content disclosure** (YouTube's AI disclosure)
  - "About AI content disclosure" info link with helper text: "Tell viewers your content was generated or edited with AI."
  - Inline switch (role=switch), default OFF.
- **Footer row**
  - "Create Another" checkbox (role=checkbox), unchecked by default — lets you chain posts.
  - "Save Draft" button — large/tertiary, disabled while empty.
  - "Next Available" button (schedule-selector-trigger, secondary) — opens the schedule picker; default selects the next open queue slot.
  - "Schedule Post" button — large/primary, disabled while empty.

**YouTube-specific fields NOT present in Buffer's composer**
The following well-known YouTube Studio upload fields do NOT appear in Buffer's composer for this channel:
- Dedicated "Tags / Keywords" input (only the composer's top "Tags" tag-chips UI and the paid Hashtag Manager exist).
- "Thumbnail / Custom thumbnail" upload (no thumbnail control is rendered; the media uploader is the video file itself).
- "Playlist / Add to playlist" selector.
- "First comment / pinned comment" field.
- Subtitles/CC upload, category sub-settings, audience restriction beyond Made for Kids, end screens / cards.
These are handled by YouTube directly or are not supported by Buffer's YouTube integration.

**Other composer side-panels observed (not YouTube-specific but present)**
- Tags side-panel button (opens per-post tag chips).
- Templates side-panel (browse templates).
- AI Assistant side-panel.
- The "Browse templates" button inside the editor.

Closed the composer with the Close Composer × button without saving. Nothing was scheduled, saved, or published.

## 6. YouTube channel — Settings
Reached via the per-channel "Channel settings" link (URL /channels/6ada1715778b240e2f8c7800/settings) and also from the "Dublin" timezone pill on the Publish page.
Page title: "Buffer - Channel Settings - Posting Schedule".
Breadcrumbs: Back → Ram prasad Somaraju → Channel Settings.
Tablist (role=tablist) at top: Posting Schedule | General.

### 6a. Posting Schedule tab (default)
Section text: "Posting Schedule / Time Zone / Time zone used for scheduling queue for this channel / Timezone Dublin / Posting Goal / Choose how often you aim to post on this channel each week. / Posting Slots / Your posting times tells Buffer when to send out posts in your Queue. For example, the next 5 posts you add to your Queue will be sent in the next 5 upcoming time slots you choose below."

Controls:
- **Timezone** — combobox button showing current value, default "Dublin". Sets the time zone used for the channel's queue. (This is the same setting the Publish page's "Dublin" pill links to.)
- **Posting Goal** — numeric spinbutton (input type=text with inputmode=numeric, pattern="[0-9]*", role=spinbutton) flanked by Decrease/Increase buttons. Current value 3. Means "aim to post 3 times per week on this channel". Drives the "0/3 posts sent this week" chip on the Publish page and the Meet My Posting Goal option.
- **Generate New Posting Slots** — secondary button that opens a 3-item menu:
  - "Meet My Posting Goal" — menuitemradio, DISABLED in this session (greyed) because no goal is configured beyond the spinbutton value, or Buffer has no data to generate from.
  - "Update Slots to Match Buffer's Recommendations" — enabled menuitem.
  - "Copy from Channel" — menuitem with a sub-trigger (haspopup=menu) — copy the schedule from another connected channel.
  - An in-product tooltip appears anchored to this button on first open: "New — Match Posting Slots With Your Goal: We can add recommended times based on Buffer data to match your goal. Existing posting times and scheduled posts won't be affected." with Got It (primary) and × Dismiss.
- **Per-day posting-slot cards** (one per day of week; Sunday–Saturday headers exist). Configured days show an On/Off switch + hour:minute selectors + a Remove affordance:
  - Sunday: no slot configured
  - Monday: no slot configured
  - Tuesday: no slot configured
  - Wednesday: no slot configured
  - Thursday: switch "Disable Thursday posting schedule" checked=ON (schedule enabled), time selectors "20" hours and "02" minutes → 20:02. A "Remove Thursday 20:02" action exists in the tab's menu_options.
  - Friday: switch ON, time 18:35, with Remove action.
  - Saturday: switch ON, time 19:15, with Remove action.
  (Default YouTube queue for this channel = 3 weekly slots: Thu 20:02, Fri 18:35, Sat 19:15, in Europe/Dublin — matching the Posting Goal of 3.)
- **Add a new posting time** row:
  - Day selector: button "Every Day" (postingtime-form-days-selector, dropdown) — chooses which day(s) the new slot applies to.
  - Hour selector: button "15" (postingtime-form-hours-selector).
  - Minute selector: button "07" (postingtime-form-minutes-selector).
  - "Add Posting Slot" primary button (postingtime-form-submit-button).
  - (Default new-slot values: Every Day at 15:07.)
- **Clear All** — large/critical-subtle button with haspopup=dialog (confirmation dialog before clearing all posting slots).

No Posting Schedule option here is labelled as paid-plan-gated.

### 6b. General tab
Reached by tab click; URL gains ?tab=general. Page title becomes "Buffer - Channel Settings - General".

Sections top-to-bottom:

1. **Enable Notifications by default**
   - "You can set up default notifications from the Buffer mobile app to notify you when to publish your posts for this channel. Learn more." (Learn more. → support.buffer.com article on notification publishing.)
   - Status label "Disabled".
   - Inline switch (role=switch, id=instagram-reminders-switch) — checked=false and disabled=true (greyed out). Cannot be toggled on until notifications are configured.
   - Primary button "Set Up Notifications" — opens the mobile notification setup flow.
   - (This is the Buffer "Notify Me" publishing path complement; enabling it here is what makes "Notify Me" meaningful.)

2. **Link Shortening**
   - Helper: "Are your links feeling a little long? Well worry no longer, choose one of our link shorteners or connect your own bit.ly account and Buffer will make sure that your links are shortened whenever you post."
   - Dropdown button default "No Shortening".
   - Dropdown options: No Shortening, buff.ly, Connect Bit.ly.
   - (Note: link shortening is not useful for YouTube video posts, but the control is present for the channel.)

3. **Google Analytics Campaign Tracking**
   - "With campaign tracking enabled in Buffer, you will be able to see how much traffic you receive from social media posts directly inside your Google Analytics dashboard. You can disable this below if you'd like."
   - Inline helper: "Please choose a link shortener above if you'd like to enable campaign tracking."
   - (Greyed / not enable-able until a shortener is picked. Irrelevant to YouTube, but rendered.)

4. **Bulk Post Upload**
   - Info-only message: "This feature currently only supports images, so YouTube is not supported at the moment."
   - No controls. Explicitly calls out YouTube as unsupported for bulk upload.

5. **Queue Actions** group (label inferred from the cluster):
   - **Pause Queue** — "Stop all posts from being sent on this channel? Learn more" (Learn more → support article on pausing your queue). Inline switch testid=pause-queue-toggle, default Off (checked=false).
   - **Shuffle Queue** — "Your first 200 queued updates will be shuffled into a completely random order." Button "Shuffle Queue" (secondary).
   - **Clean Queue** — "Cleaning your queue will remove all failed updates within your Ram prasad Somaraju queue." Button "Clean Queue" (secondary).
   - **Delete All Drafts** — "Remove all drafts from your Ram prasad Somaraju channel. Posts pending approval will not be affected." Button "Delete Drafts" (critical).
   - **Empty Queue** — "Want to fresh start? Emptying your queue will remove all updates within your Ram prasad Somaraju queue." Button "Empty Queue" (critical).

None of the General options shown for this channel are paywalled individually; however, the per-organization "Upgrade for More" card persists in the sidebar throughout and the composer's Hashtag Manager + Insights Custom date range + Publish "Approvals" tab are the paid-plan gates observed.

## 7. Paid-plan / locked-features summary (YouTube channel, free plan)

Observed on-free-plan gates that affect or are surfaced to the YouTube channel:
- **Publish → Approvals tab**: label "Approvals / Upgrade". Clicking opens paywall: "Collaboration made easy" (choose who can post, review posts, collaborate, automated reports) with CTA "Start a Free Trial". Locked.
- **Insights → Custom date range**: radiogroup item "Custom / Upgrade" — opens an upgrade dialog. Locked. (7 Days / 30 Days / Month to Date are free.)
- **Composer → Hashtags**: "Upgrade to use Hashtag Manager" button in the media toolbar — paid. (YouTube has no native tags keyphrase box in Buffer anyway; the composer's generic "Tags" chip UI is free.)
- **Composer → "Generate New Posting Slots / Meet My Posting Goal"**: menu item rendered disabled in this session (not necessarily paywalled, but unavailable — possibly requires goal + historical data).
- **Home dashboard → "3/3 channels connected" card** with "Upgrade for More" — the free plan cap for connected channels (Ram already has 3/3: YouTube, TikTok, Instagram). Connecting more requires upgrade.
- **Sidebar → "Connect more channels" / "Upgrade for More"**, **My organization → free plan** badge, persist.

NOT locked on free for the YouTube channel:
- Queue, Drafts, Sent tabs and per-slot "Add a post to YouTube" composer triggers.
- Community tab Comments view (Mentions is unsupported for YouTube regardless of plan).
- Insights Summary / Performance / Metrics (except Custom range).
- All three composer YouTube metadata controls (Category, Visibility, License, Notify Subscribers, Allow Embedding, Made for Kids, AI-Generated switch, Posting Method Automatic vs Notify Me, Title, 5000-char description).
- Channel Settings Posting Schedule (timezone, posting goal spinbutton, per-day slot switches, Add Slot, Clear All, Generate New Posting Slots) and General (Notifications setup, Link Shortening, GA Campaign Tracking note, Bulk Post Upload notice, Pause Queue switch, Shuffle/Clean/Delete Drafts/Empty Queue).

## 8. What was NOT changed
- No post was published, scheduled, saved, or drafted.
- No setting, posting slot, timezone, goal, switch, or dropdown was changed.
- No channel was connected or disconnected.
- The composer was closed via Close Composer (×) without saving; both Save Draft and Schedule Post were left disabled.
- The only modal-like element interacted with was the in-product "Match Posting Slots With Your Goal" tooltip on the Generate New Posting Slots button, which was dismissed via Escape (no setting applied).
- No upgrade/free-trial flow was entered.
---

# Part 3 — Connect-channels notes

Connect Channel dialog (opened from Settings > Channels "Connect Channel" primary button)
URL: https://publish.buffer.com/settings/channels
Dialog is a modal grid of provider cards, each card shows provider name + account type subtitle:
- Instagram — Business, Creator, or Personal
- Threads — Profile
- LinkedIn — Page or Profile
- Facebook — Page or Group
- Bluesky — Profile
- Substack — New Profile
- YouTube — Channel
- TikTok — Business or Personal
- Mastodon — Profile
- Pinterest
- Google Business
- Twitter / X
- Start Page
- Request a Channel
