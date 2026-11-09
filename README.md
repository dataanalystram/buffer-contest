# HuhQueue — built on the Buffer API

**Your brand's editor on top of Buffer. It researches the story, renders it in your brand, learns from every correction, and plans it in Buffer after you approve.**
Give HuhQueue a topic, a link or your own photo or video. An agent finds and verifies the story, renders on-brand carousels,
cards and Reels, writes one caption per channel, and checks all of it against your **Style Ledger**, the rules it has learned from
your corrections. You approve, and the **Planner** shows it in your Buffer queue, ready to drag to a better time. Your own
Claude or ChatGPT can do all of this over MCP, but nothing they write goes out until you approve it.

**Bring your own brain.** In the app the agent starts on Claude with $5 of included credit
(an admin can give a person or a group more), then switches to NVIDIA when it runs out. Add HuhQueue to the Claude or ChatGPT you already
pay for and keep going free: your model does the thinking, HuhQueue does the editing (research, carousels, reels, cuts,
captions, splits) at $0 AI spend. See [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md).

**Live:** https://huhqueue-studio.onrender.com · **Buffer Commit entry:** see [CONTEST.md](CONTEST.md) and [SUBMISSION.md](SUBMISSION.md)

**Build in public:** [X](https://x.com/HUHBYTE/status/2119897935676612635) · [YouTube](https://youtu.be/1DwcEoPNsrI) · [LinkedIn](https://lnkd.in/p/eFecc3xz) · [Instagram](https://www.instagram.com/reel/DfiQgZku-Yp/) · [Facebook](https://www.facebook.com/share/v/19n81wvYeX/)

## Demo (2:00)

[![Watch the 2-minute HuhQueue demo: the landing page, Paste one link. Approve one week.](docs/screenshots/demo-2min-poster.jpg)](https://github.com/dataanalystram/buffer-contest/blob/main/demo/huhqueue-buffer-commit-demo-2min.mp4)

*The full product tour, narrated: Inbox, News, Make a post, brand kits, One take, bring your own AI over MCP, Library and
the Planner. [Watch it](https://github.com/dataanalystram/buffer-contest/blob/main/demo/huhqueue-buffer-commit-demo-2min.mp4)
· [download](https://github.com/dataanalystram/buffer-contest/raw/main/demo/huhqueue-buffer-commit-demo-2min.mp4)
· [transcript](demo/huhqueue-buffer-commit-demo-2min.transcript.md)*

> **The contest entry is Studio (`studio/`).** It signs users in with Buffer OAuth only (Authorization Code + PKCE S256,
> `prompt=consent`, rotating `offline_access` refresh tokens) and never asks for a Buffer API key. The separate core app in
> `huhqueue/` has paste-a-token options; those are for connecting LinkedIn and Instagram directly, not Buffer.
> **Buffer scopes:** enable a new scope on the Buffer app client *before* deploying code that requests it, or every sign-in
> fails with `invalid_scope`. Users who signed in earlier see "Reconnect Buffer to allow …" until they consent again.

> HuhQueue is an independent open-source project built on the [Buffer API](https://developers.buffer.com). It is not made, endorsed or operated by Buffer.
> The Nothing, Linear and Notion kits in the sample workspace are labelled sample brands, not affiliated with those companies (see [NOTICE](NOTICE)).

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/planner-week-dark.png">
  <img src="docs/screenshots/planner-week-light.png" alt="HuhQueue Planner, week view: posts from five Buffer channels on an hourly grid opened at the week's first post, with the Week, Month, Day, List, Board and Grid switch">
</picture>

*The Planner, week view: your Buffer queue and calendar in one place. Drag any post or undated draft to a new time; sent posts stay locked.*

## Screenshots

All captured from the running app (sample workspace at `/demo`, nothing reaches Buffer) with
`scripts/premium_shots.py` (landing, Planner views, ⌘K, Connect AI apps), `scripts/planner_shots.py`, `scripts/readme_shots.py`
and `scripts/inbox_shots.py`. Re-run them whenever the interface changes.

| | |
|---|---|
| ![Planner week view in light mode, hourly grid with posts and Buffer posting times](docs/screenshots/planner-week-light.png) | ![Planner week view in dark mode](docs/screenshots/planner-week-dark.png) |
| **Planner, Week** (light). Click an empty slot to schedule there; dashed chips are Buffer's posting times. | **Planner, Week** (dark). Same view, black-and-white dark theme. |
| ![Planner list view: the Buffer queue grouped by day with thumbnails, channel, time, caption and status, plus edit and reschedule actions](docs/screenshots/planner-list-light.png) | ![Planner month view: a month grid with posts per day](docs/screenshots/planner-month-dark.png) |
| **Planner, List**: the Buffer queue by day, with edit, reschedule, approve and delete-draft actions. | **Planner, Month** (dark): "+N more" opens the whole day. |
| ![Planner day view: one lane per channel with the day's posts, opened at the first post](docs/screenshots/planner-day-light.png) | ![Planner board view: a column per status, Draft, Needs approval, Ready to post, Scheduled in Buffer and Sent](docs/screenshots/planner-board-dark.png) |
| **Planner, Day**: a lane per channel, opened at the first post. | **Planner, Board** (dark): a column per status; drag a card to move it along. |
| ![Planner grid view: an Instagram feed preview with planned tiles dashed and posted tiles solid](docs/screenshots/planner-grid-light.png) | ![The Ctrl/Cmd K command palette over the app: go to any screen, run a command or open a doc](docs/screenshots/palette-light.png) |
| **Planner, Grid**: preview your Instagram feed before it goes out. Keys 1 to 6 switch views. | **⌘K / Ctrl K**: jump to any screen, run a command or open a doc. |
| ![Connect AI apps: one MCP address, then per-app cards for Claude, ChatGPT, Grok, Claude Code, Codex, Cursor and any MCP client, each with steps, a snippet and when it was last used](docs/screenshots/connect-ai-light.png) | ![The landing page for visitors: Paste one link. Approve one week., with a link box, Connect your Buffer account and Try the demo](docs/screenshots/landing-light.png) |
| **Connect AI apps**: Claude, ChatGPT and Grok sign in; Claude Code, Codex, Cursor and scripts get a token per app. | **Landing** at `/` for visitors; the app opens once you sign in, and `/demo` opens the sample with a tour. |
| ![Make a post: the agent edited the user's own photo, with its finished step timeline and the branded image](docs/screenshots/agent-run.png) | ![Ready for Buffer: one package per channel with caption, media, character count and a passing brand check](docs/screenshots/agent-packages.png) |
| **Make a post**: the agent reads your photo (or researches a topic or link), renders it in your brand and shows every step. | **Ready for Buffer**: one caption per channel, counted Buffer's way and checked against your rules. |
| ![Brand kit editor with colours, font and end card, a live preview, and the Style Ledger listing four learned rules](docs/screenshots/brand-ledger.png) | ![Approvals: a post written by the Claude agent waiting for a human, with Reject and Approve and send to Buffer](docs/screenshots/approvals.png) |
| **Brand kit and Style Ledger**: say "logo smaller" once and it becomes a rule every post follows. | **Approvals**: agent posts wait here; nothing reaches Buffer until you approve. |

### Library, chat and Send a file (4 Nov 2026)

Captured from a local, seeded run of `main` (made-up workspace and a fake Buffer, nothing real) with
`scripts/readme_refresh_shots.py`. The send code is shown as `ABCD-EFGH` and its QR code is covered.

| | |
|---|---|
| ![Library at 1280 px: All media 18 with Videos, Carousels, Images and Audio tabs, filters for date, creator, use and generation run, the Library and Bin switch, and a grid of uploaded photos and clips](docs/screenshots/library-grid-1280.png) | ![The Library Bin at 1280 px: two deleted items, each with 7 days left, Restore and Delete permanently, plus Empty bin](docs/screenshots/library-bin-1280.png) |
| **Library**: every upload and every image, carousel and Reel the agent made, with the storage meter. | **Bin**: deleted items stay 7 days, then go for good; **Empty bin** and **Delete permanently** remove them at once. |
| ![Make a post at 1280 px: With AI agent, Quick post and From a link, the prompt box with starter ideas, and Discover with a story to use](docs/screenshots/make-post-1280.png) | ![A Make a post chat thread at 1280 px: the thread list on the left, Worked through 7 steps opened with each step's result, and 3 posts ready to send as Buffer drafts](docs/screenshots/chat-steps-1280.png) |
| **Make a post**: start from an idea, a link or a story from Discover. | **The chat**: each run is a thread; the steps fold into one row you can open. |
| ![The Library at 390 px on a phone: Add media, type tabs, filters, the Library and Bin switch and a two-column grid above the bottom bar](docs/screenshots/library-grid-390.png) | ![Send a file at 1280 px: a code valid 30 minutes for up to 10 files, Copy link, New code and Stop receiving, and a QR code area](docs/screenshots/send-device-1280.png) |
| **Library on a phone** (390 px). | **Send a file**: open `/send` on the device that has the file; the code lasts 30 minutes and takes up to 10 files. |

## Why

Creators who share news (we run a daily AI-news page) do the same chore many times a day: read the story, write a LinkedIn version,
a shorter X version, an Instagram caption with "link in bio", check each network's limit, then queue them. Agents can write those
posts in seconds, but nobody wants an agent posting to their brand unreviewed. HuhQueue is the review desk between the two, on top of
the Buffer account you already use.

## What it does

- **One take → a week** — drop one long video you recorded and get 3 to 7 captioned vertical clips of the best things you already said, each with a branded cover and end card, checked by preflight and spread into the open posting times of your week as Buffer drafts. [More below](#one-take--a-week).
- **The agent ("Make a post")** — start from a topic, a link or your own photo or video. It searches the news, opens the source, checks other outlets carry it and pulls out the facts, with every step on a timeline you can read. Then it picks a format and writes one caption per channel.
- **Renderer** — Pillow renders 4:5 carousels and single cards with your colours, font, logo, handle and end card; FFmpeg renders 9:16 Reels (slideshows or your own clips with a logo bug, hook and end card). Text the brand font can't draw (emoji, stray symbols) is left out rather than shown as empty boxes.
- **Video worker** — on the free hosted plan, Reels render on a GitHub Actions worker (`studio/render_worker.py`, `.github/workflows/render.yml`) that fetches the job with a single-use token and posts the MP4 back; self-hosters render in-process.
- **Brand kit and Style Ledger** — set colours, font, logo, end card and voice, or import them from your website. Say "logo smaller" or "no hype" once and it becomes a written rule; every render and caption follows the ledger, and the brand check names the exact rule a post breaks. Before/after slider on every revision.
- **Planner** — one tab for the Buffer queue and the calendar: List, Week and Month views; filter by channel, post type, status and caption text; any timezone (defaults to your browser's); a **No date** sidebar for undated drafts; Buffer's **posting times** on the grid; click an empty slot to schedule there; drag a post or draft to reschedule (Buffer `editPost`, the same `update_post` action REST and MCP use; sent posts are locked). View and filters live in the URL and are remembered.
- **Never Bounced preflight** — every post is checked for every channel before Buffer sees it: text counted the way Buffer counts it, hashtag and mention limits, required fields (YouTube title and category, Pinterest board), media count, type, aspect, size and video specs. Green, amber or red per channel, the rule and its source on each finding, and one-click **Fix it** (thread or trim the caption, crop and convert the image). [More below](#never-bounced-preflight).
- **Connect Buffer** — OAuth 2.0 Authorization Code + PKCE with a private client. Access and rotating refresh tokens stay encrypted on the server; the browser only holds an opaque session cookie.
- **Link → drafts** — reads the article and writes one caption per channel, counted the way Buffer counts (UTF-16, LinkedIn URLs = 24, Instagram line breaks = 2, X URLs = 23, Bluesky graphemes) and trimmed to fit.
- **Send to Buffer** — as a Buffer draft, add to queue, share next, share now or a custom time; image/video by URL; ideas to your Ideas board. Queue and drafts read back from Buffer.
- **Agents with a leash** — MCP server + REST API per Buffer user. Every write is *preview → single-use confirm token → submit*. Default policy holds agent posts in an **Approvals inbox**; `draft` tokens can only create Buffer drafts; `trusted` tokens can schedule. Daily caps, a one-click **kill switch**, and an **activity log** of every action by you or your agents.
- **Plays nice with Buffer's limits** — reads `RateLimit` headers (shown live in the header), caches channel lists, backs off on `429` + `Retry-After`.

## One take → a week

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/take-clips-dark.png">
  <img src="docs/screenshots/take-clips-light.png" alt="One take: a progress timeline (read, transcribe, find moments, reframe, captions, covers, preflight, ready) next to three 9:16 clips with burned-in captions, each with its hook, where it sits in the talk, its layout and a green Instagram preflight chip, and a Spread into my week button">
</picture>

*The sample take in `/demo`: a 3-minute talk became three clips. Every step is on the timeline, every clip shows where it came from in the talk and passes preflight.*

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/take-spread-dark.png">
  <img src="docs/screenshots/take-spread-light.png" alt="The Planner's week view after The Spread: three planned clips sit in the 11:00 Instagram posting slots on Monday, Wednesday and Friday, and a bar says 3 clip posts planned across 3 days with Discard plan and Save as Buffer drafts buttons">
</picture>

*The Spread: the clip cards fly into the open posting times of next week on the Planner. Nothing exists in Buffer until you press **Save as Buffer drafts** ([after](docs/screenshots/take-saved-light.png), [trim and caption editor](docs/screenshots/take-editor-light.png)).*

The principle is **extract, don't invent**: a clip is a span of your own transcript, the hook on its cover is a line you said, and the post caption quotes you.

1. **Your video** — upload (up to 40 MB on the hosted app) or paste a direct link to a file you host (long talks). You tick "this is my video"; pages on social networks are refused.
2. **Transcribe** — on the video worker, never the web dyno: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) `small` on CPU (int8) with word timestamps and Silero VAD, FFmpeg `silencedetect` for pauses, [PySceneDetect](https://github.com/Breakthrough/PySceneDetect) for cuts, and a face track at 2 fps with [MediaPipe](https://github.com/google-ai-edge/mediapipe)'s Face Detector. Our run on the sample: a 2:59 talk, 411 words in 47 s on a 2-core CPU, 71 s for the whole analysis.
3. **Find the best moments** — whole sentences, 20 to 60 s, scored by your AI engine through the same provider settings the rest of the app uses (your key, the free starter, or none). Every span the model returns is checked against the transcript: out of range, overlapping or too short is dropped, and a hook that isn't a verbatim quote is replaced by the line you actually said. Without a model, or when it fails, a deterministic scorer picks them (strong opening line, clean ending, pace, little filler). Clip edges snap to a pause or a cut.
4. **Reframe to 9:16** — shot by shot: where someone is on camera, the window follows their face with a dead zone and short eases (a jump at scene cuts); where no one is, the whole frame sits over a blurred copy of itself. A test keeps the face inside the window on at least 90% of samples.
5. **Captions** — burned in with libass in your brand font and colours, the word being said in your accent colour, always above the bottom 20% where Instagram and TikTok put their buttons.
6. **Cover and end card** — the sharpest frame of the moment (faces preferred) with the hook in your brand, as the first half second of the clip; `thumbnailOffset` points at it on Instagram, TikTok and Pinterest (the networks Buffer honours it for). Your end card closes the clip.
7. **Preflight** — each clip is checked for each channel with the measurements the worker took (codec, size, fps, duration, bitrate), so the web dyno needs no ffprobe.
8. **The Spread** — reads your Buffer posting times and what's already in the week, then plans one clip per channel per day where it can: never two clips in one slot, never past Buffer's daily limit for a channel, each channel in its own timezone (DST-safe). You see the plan on the Planner first; **Save as Buffer drafts** creates dated drafts (`saveToDraft` + `dueAt`, Instagram `type: reel`). Nothing is ever published. With `prefers-reduced-motion` the plan appears without the flight.

Each clip can be trimmed (click a word to start or end there, or nudge by half a second; it re-renders on the worker), given a new hook (it must be at least three words you said in the clip) or a different caption per network.

API: `POST /api/takes` (upload `media_token` or `url`, `owns: true`), `GET /api/takes/{id}`, `PATCH /api/takes/{id}/clips/{clip_id}`, `POST /api/takes/{id}/place` (dry run by default; then `confirm_token`), `POST /api/takes/sample`. MCP: `clips_from_video`, `list_clips`, `adjust_clip`, `place_clips_in_week` (two steps for agents; a `review` token's drafts wait in Approvals). The worker runs on GitHub Actions (`.github/workflows/take.yml`, `requirements-worker.txt`) with a single-use job token, or in-process wherever FFmpeg is installed. The sample workspace uses a public-domain NASA talk analysed and rendered ahead of time by the same code (`scripts/build_sample_take.py`), and mines, preflights and places it live without Buffer.

## Never Bounced preflight

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/preflight-dark.png">
  <img src="docs/screenshots/preflight-light.png" alt="Composer with a preflight strip: X and Instagram are red (341/280 characters; image 900×2000 outside Instagram's 4:5 to 1.91:1), LinkedIn, Bluesky and Threads are green; each card shows its chip, the finding with a link to the rule, and a Fix it button">
</picture>

*Preflight in the composer, captured from `/demo`: X is 341/280 and the Instagram image is 9:20, so both are red and blocked. One click on **Fix it** threads the X post and crops the image to 4:5 JPEG ([after](docs/screenshots/preflight-fixed-light.png)).*

A post that Buffer or the network would refuse is caught before `createPost` or `editPost`, on every surface:

- **Where it runs** — the composer cards and the Planner's new-post and edit dialog (a chip per channel, plus a summary strip), the REST API and MCP. The same code (`studio/preflight.py`) runs inside `preview_post`, again right before `createPost` (so a post that waited in Approvals is re-checked), and before every `editPost`.
- **Red, amber, green** — errors block with no override; warnings (a PNG for Instagram, an image the server couldn't download, a signed link that may expire) block until you tick **Send anyway** (`override_warnings: true`); notes never block.
- **Counted Buffer's way** — UTF-16 on most networks, Instagram line breaks count 2, LinkedIn URLs 24, Mastodon URLs 23 (and `@user@server` counts `@user`), X's weighted count (URL 23, emoji 2, CJK 2), Bluesky graphemes. Buffer's own examples are unit tests (`𝗛𝗲𝗹𝗹𝗼` = 10 / 5 / 10, family emoji = 8 / 1 / 2). TikTok drops to 2,200 when a video is attached; Mastodon uses the server's limit.
- **Media** — Pillow reads each image (format, bytes, width × height) and ffprobe reads video (codec, fps, duration, bitrate, audio) and checks them against each network's published specs: Instagram 4:5 to 1.91:1, JPEG, 8 MB, Reels 3 s to 15 min at 23 to 60 fps and up to 25 Mbps; X up to 4 photos or one video; LinkedIn multi-image 2 to 20 images only; Threads carousels up to 20; Bluesky 4 images; TikTok photo and video specs; YouTube Shorts length.
- **Fix it** — trims or threads the caption under the real count, keeps the first N media the network takes, crops/converts/recompresses images with Pillow (saved as new media with a stable public URL), sets a YouTube title and category, and flags video that needs re-encoding. **Undo fix** puts the original back.
- **Honest rules** — every limit links the official page it was read on, with the date, in [docs/PREFLIGHT.md](docs/PREFLIGHT.md) (generated from the code; a test fails if they drift). Rules we couldn't verify are listed there and not enforced.
- **Buffer's HTTP 200 errors** — a refused mutation comes back as HTTP 200 with `createPost.message`; HuhQueue treats it as a failure and shows Buffer's message in the UI, the REST API (422) and MCP, and in the activity log.

API: `POST /api/preflight` (dry run), `POST /api/preflight/fix`, `GET /api/preflight/rules`. MCP: `preflight_post`, `autofix_post`, `list_rules`.

## Inbox, phones and the free sample

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/inbox-dark.png">
  <img src="docs/screenshots/inbox-light.png" alt="The Inbox in the sample workspace: Inbox · 5, chips for Approvals waiting, Failed or bounced, Preflight warnings, Drafts with no date, Empty slots this week and Buffer plan, then an agent post from Claude to approve and an Instagram post Buffer couldn't publish with its reason and a Fix and reschedule button">
</picture>

*The Inbox: everything waiting for a person, most urgent first. The same read is `GET /api/inbox` and MCP `get_inbox`.*

| | | |
|---|---|---|
| ![Phone, 390 px: the Inbox with the bottom bar (Inbox with a badge, Make a post, One take, Planner, More)](docs/screenshots/phone-inbox-light.png) | ![Phone, 390 px: the Planner week as an agenda, one day after another with its posts](docs/screenshots/phone-planner-agenda-light.png) | ![Phone, 390 px, dark: step 4 of 7 of the guided tour, the Planner, with Skip tour, Back and Next above the bottom bar](docs/screenshots/tour-phone-dark.png) |
| **Phone**: a bottom bar and a More sheet; every control is 44 px or more. | **Planner on a phone**: the week becomes an agenda ([dark](docs/screenshots/phone-planner-agenda-dark.png)). | **Guided tour** on the first `/demo` visit: 7 steps, keyboard-driven, skippable ([desktop](docs/screenshots/tour-light.png)). |

The sample workspace at `/demo` needs no sign-in and never calls Buffer. It has daily caps per workspace and per visitor
IP (hashed) for live research, the free AI writer, website import, new feeds and Reel renders; past a cap the feature
keeps working on sample data. The sample never starts the video worker, and a sample agent token reaches only the sample
and expires within a day.

## What we built during the Commit window (28 Oct to 13 Nov)

Everything below was built in the Buffer Commit window; the pre-existing self-host scheduler is listed separately in
[CONTEST.md](CONTEST.md). This section is updated in the same commit as each feature.

| Date (2026) | What |
|---|---|
| 28 Oct | Buffer OAuth (PKCE, rotating refresh tokens), GraphQL client with rate-limit handling, link to one caption per channel counted Buffer's way, preview, confirm and send (draft, queue, next, now, custom time), ideas |
| 28 Oct | Agents with a leash: MCP server and REST API per Buffer user, approval inbox by default, drafts-only and trusted policies, daily caps, kill switch, activity log; Playwright e2e and CI on SQLite and Postgres; Render deploy |
| 29 Oct | Threads from one link, sample workspace at `/demo`, post previews, news radar, plan-limit guard, bookmarklet, the home screen for what is waiting (now the Inbox) |
| 30 Oct | Supabase Postgres for the live app, OAuth connector for Claude.ai and ChatGPT, public usage counter |
| 30 Oct | The agent ("Make a post"): research a topic or link or edit your own photo or video, on-brand carousels, cards and Reels, brand kit with website import, the Style Ledger and brand check, AI engine settings (bring your own key or use your own Claude/ChatGPT) |
| 30 Oct | Renamed to HuhQueue. **Planner**: one tab replaces Calendar and Buffer queue (List, Week, Month, filters, timezone, No date drafts, posting times, click-to-schedule, drag to reschedule) on new `update_post`, `reschedule_post` and `delete_draft` actions (Buffer `editPost` / `deletePost`) shared by UI, REST and MCP; light and dark screenshots |
| 30 Oct | Clean renders: no empty-box glyphs for emoji or symbols the brand font lacks, no "My brand" placeholder (the workspace name is used, or no wordmark) |
| 30 Oct | **One take → a week**: one long video you own becomes 3 to 7 captioned 9:16 clips (faster-whisper transcript, verbatim moment mining through the provider abstraction with a deterministic fallback, shot-by-shot MediaPipe reframe, libass brand captions, cover frame + `thumbnailOffset`, end card), preflighted and spread into the week's open Buffer posting slots as dated drafts on the video worker (GitHub Actions `take.yml` or local FFmpeg). The Spread animation on the Planner, trim/hook/caption editor, progress timeline; REST + MCP `clips_from_video`, `list_clips`, `adjust_clip`, `place_clips_in_week`; sample take in `/demo` |
| 30 Oct | **Never Bounced preflight**: per-network rule pack with cited sources (docs/PREFLIGHT.md), Buffer-exact counting, Pillow/ffprobe media checks, Fix it (thread/trim, crop/convert images), runs before every `createPost` and `editPost`; green/amber/red chips in the composer and Planner; `POST /api/preflight` + MCP `preflight_post`, `autofix_post`, `list_rules`; Buffer's HTTP 200 errors surfaced everywhere |
| 31 Oct | One take merged after CI went green (root cause: a skipped browser test left the Playwright driver running). **Inbox**: what needs a person by urgency, `GET /api/inbox` + MCP `get_inbox`, fix and reschedule failed posts. **Responsive**: bottom bar and More sheet on phones, 44 px targets, phone Planner agenda, an audit and a test at 360 to 1440 px in light and dark. **Free sample** with a 7-step guided tour and daily caps with sample fallbacks |
| 1 Nov | **Agent core** (#27): one prompt box, the link is the source, Claude brain with budgets, fact hold, hosted rendering on the worker. **Media pipeline** (#29): finds the real launch video, styled edits (opus / news / minimal / hype), images, music. **UX v3** (#28): nine screens, Inbox dashboard, News, Brand, AI apps, onboarding. **Make a post v2** (#32, #34): every run through the agent, per-channel previews, a media Library, visual Story scan. 21 video themes and HuhByte templates (#35). Long-video splitting, transcript reuse, weekly recap with videos (#36). The agent makes the editorial carousel itself; no video means no Reel (#37). **MCP client brain** and brain swap UI (#38). Settings › Integrations key cards for Product Hunt, X and YouTube (#33). Agent and UI polish from live QA (#26, #30, #31) |
| 2 Nov | **Free AI engines** (#44, #47): Google AI Studio, NVIDIA NIM, Hugging Face, Oracle Gemini; AI model switch with transparent credits and a video style gallery (#40). **InstaBox render worker** (#43) with failure reports, heartbeat and watchdog (#51). Video-first research, branding throughout, branded covers (#46); "No video found, carousel only" on every no-video path (#41). **Sources v2** (#42): free source library by category, shared cache. **Brand kits**: builder and sample brands (#49), v2 with real site styles from a URL and kits in Templates (#54). Instagram music on Reels from Buffer's audio catalog (#69). Landing integrations and uploads from chat apps (Send a file page) (#55). Sign-in survives Buffer rate limits (#72). Security hardening for the public repo (#56). In-app confirm dialog (#52), AI engine cards, logs and keys (#53), editable agent daily limit (#48), Buffer can read our media and MCP clients can upload local files (#45), fixes (#39, #50, #67, #68, #70) |
| 3 Nov | **Make a post as a chat** with a Settings sidebar (#88); template choice, rights asked once, upload in the thread, per-run kit, interrupted runs (#84); Update draft, Undo / Back to the Reel, music control (#90). **Style Ledger v2** and YouTube link reading (#83). **MCP**: prompts and resources, custom templates, one `update_brand_kit`, brand locks (#73); runs show the AI app that made them and its steps (#93). **Library Bin**, bulk delete, Create draft, View run (#91). **View-only demo**: canned AI runs, zero outbound calls (#74, #86). **Security**: prompt-injection defences (#78), XSS escaping with a hostile-content browser test (#80), Privacy / Security / Terms rebuilt from a verified data inventory, `delete_user` covers every table (#82). One take Trim & caption window, For you interests, Product Hunt media (#85). Reel opening and Buffer API audit fixes (#92). Brand kit v2 polish (#71), video agent and cover fixes (#75, #76, #77, #79, #81) |
| 4 Nov | **One request = one run = one thread** over MCP, paid-API approval gate (#104). **Image editing on HuhQueue's side**: cut-out, `fetch_image`, `edit_image`, `compose_product_card` (#103). Library premium: tabs, search, filters, grid / list, bulk selection, server-side pages (#96, #97). Independently collapsible sidebars and live agent steps (#94). Loading states everywhere and an adaptive wide-screen layout (#99). **Security**: SSRF guard on every user-URL fetch (#100), Send from a device audit (#95). Docs refresh (#98), Buffer API gap map (#87), video style skills plan (#89). Fixes and dependency updates (#57 to #60, #102, #105, #106) |
| 5 Nov | **One take**: delete into a Bin with Restore and Undo, 4 GB resumable uploads, cleaner audio and reframe (#107, #114, #115); face-detection CI and a second opinion on faces (#108); per-scene layouts, animated captions, free speech-to-text chain (#113); video-link ingest with the person's rights consent, approvals over MCP (#111, #116). **Admin users panel** and media storage out of Postgres (#110). **Bandwidth and cost**: ranged media reads, ETag / 304, gzip (#118), egress guard with daily budgets (#119), R2 media behind flags (#120). MCP tool registry: `search_tools`, `describe_tool`, `call_tool` (#122). Thread links survive reload with live refresh (#124). Chat model picker (#109), animated logo loader (#117), Library paging fix (#121). |
| 6 Nov | **Agent**: intent first (no tools for "hi"), exact captions byte for byte, nothing reaches Buffer without the button (#137). **MCP reliability**: one recommended path, per-channel media, one rights step, no duplicate drafts (#139); the daily cap counts Buffer writes only (#151). **Contest P0**: Buffer tokens kept on transient refresh failures, Reconnect Buffer on a missing scope (#135). **Admin controls**: kill switches, token caps and revoke, usage meters (#128). **One take**: pause and filler trim with two-pass loudness (#126), Tesseract OCR for caption placement and alt text (#130), gentle punch-in (#131, #141), face-safe overlays and render QA (#142), run view with All takes (#138), one render per clip with Instagram-safe audio (#152), a 3 s branded hook opener with credit on the end card (#153). Style presets and recurring template slots (#134). Story scan opens a prefilled thread (#140). CI runs the SQLite and Postgres suites in parallel (#143, #144, #146). Live fixes: ffprobe in the web image, takes survive the cut-off sweep (#150). Smaller fixes (#123, #127, #129, #132, #133) |
| 7 Nov | **One take Spread**: every clip usable, move or remove planned posts, draft / schedule / queue per post (#154). Pause trim under background music with Silero VAD (#155). Clip placements follow Buffer, so deleted drafts stop showing (#157). **Claude credit** per person and per group: $5 default, admin controls, Claude first then NVIDIA (#158). The 2-minute demo video at the top of the README (#155, #156) |
| 9 Nov | **News is never empty**: starter feeds for new accounts by interest, and for older accounts on their next News open, plus a calmer step-by-step onboarding (#166, #167). **Buffer by default**: every signed-in workspace starts on the Buffer example brand kit (never over a brand of its own). **The agent remembers what you asked**: a running list across all your messages (format per network, your exact hook, credit for the video actually used, channels named in a later message, misspelt network names), one master prompt written for any model, and a "What you asked" card in the run view with a tab for a channel that has no post yet. A new agent face and a fix for the agent label overlapping its text (#168). **Storage**: Library uploads go to Cloudflare R2 only and are refused with a plain message when it is full or down, never kept in the database (#169); a 400 MB Library per person on the hosted app; the live database moved to a fresh Supabase project in Frankfurt. **One take**: a full Library stops new takes before they start, a video's own captions are kept instead of doubled, link takes carry the brand's logo and caption font (#170). **Captions in any language**: the spoken-language caption track is used (not an auto-translation), your own words snap to the speech timing, and Noto fonts draw non-Latin scripts (#172). **Make a post**: a Reel's Instagram music is checked with Buffer before it is sent (best effort: if Buffer can't answer, the post still goes), and **two-way Buffer sync**: a post edited or deleted in Buffer shows that way in HuhQueue, with a Check Buffer button (#171) |

## Connect an agent (MCP)

**Claude.ai or ChatGPT:** add a custom connector with the URL `https://huhqueue-studio.onrender.com/mcp`. It signs you in with
Buffer and asks what the assistant may do (ask first, drafts only, or trusted) and how many posts a day. No token to paste.
The connection is an ordinary agent token underneath, so it shows on the Agents screen and the kill switch stops it.
MCP calls use **your** model by default (client brain): HuhQueue's own AI only runs on a tool call with `server_ai: true`,
and then counts against your allowance. Every tool, which side thinks, and recipes: [docs/MCP_TOOLS.md](docs/MCP_TOOLS.md).

**Claude Code / other clients:** create a token in **Agents**, then:

```bash
claude mcp add --transport http huhqueue https://huhqueue-studio.onrender.com/mcp \
  --header "Authorization: Bearer hq_YOUR_TOKEN"
```

Tools: `get_inbox`, `clips_from_video`, `list_clips`, `adjust_clip`, `place_clips_in_week`, `whoami`, `list_channels`, `draft_from_link`, `preflight_post`, `autofix_post`, `list_rules`, `preview_post`, `submit_post`, `list_approvals`, `list_queue`, `update_post`, `reschedule_post`, `delete_draft`, `list_calendar`, `list_stories`, `create_idea`, `get_limits`, plus the agent tools `run_agent`, `edit_media`, `get_run`, `revise`, `remember_rule`, `brand_check`, `get_brand`, `schedule_package`.
The REST API (reference at `/docs/api`) accepts the same bearer token, so UI = API = MCP (a test asserts all three send Buffer the identical `createPost` input).

## Your data

The full list is in the [privacy policy](https://huhqueue-studio.onrender.com/privacy) (generated by `scripts/build_legal.py`). In short:
the database is Supabase Postgres (Frankfurt) and files live in a private Cloudflare R2 bucket (EU), served only through this app (if R2 refuses a write, a file HuhQueue renders stays in the database; an upload is refused instead);
each person's Library holds up to 400 MB on the hosted app. Uploads have location and camera metadata removed (JPEG EXIF, XMP and IPTC; PNG text and EXIF; GPS in MP4 and MOV blanked;
WebP, GIF and WebM are stored as sent). Media is served at `/m/` links that are random 144-bit tokens, not signed and with no
expiry, and may be cached publicly for 1 day. Deleted Library items stay 7 days in the Bin; **Delete permanently** removes them at
once. Send codes last 30 minutes and 10 files and are stored as hashes. The activity log is kept 90 days (sign-in IP and browser
30 days); AI app calls not tied to a run are kept 24 hours. **Disconnect and delete** removes everything at once.

## Run it yourself

```bash
pip install -r requirements-studio.txt
cp .env.example .env   # BUFFER_CLIENT_ID / BUFFER_CLIENT_SECRET from https://publish.buffer.com/settings/api, redirect https://localhost:8000/callback
export $(grep -v '^#' .env | xargs) && uvicorn studio.app:create_app --factory --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Production: `render.yaml` (free web service) with a free Supabase Postgres, or `Dockerfile.studio` anywhere. Set `DATABASE_URL` to Postgres so rotating refresh tokens survive restarts. Moving databases: see [docs/SUPABASE.md](docs/SUPABASE.md) (use `pg_dump` / `pg_restore`). Media files: set the `R2_*` variables ([docs/STORAGE.md](docs/STORAGE.md)).
Optional `AI_BASE_URL`/`AI_MODEL`/`AI_API_KEY` point the drafter at any OpenAI-compatible model; the hosted demo uses the free Pollinations endpoint and falls back to the built-in writer.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-studio.txt
python -m pytest tests/studio -q            # API, OAuth, rate limits, safety, parity, Planner, preflight, One take + Playwright e2e
TAKE_SHOTS=docs/screenshots TAKE_SCHEME=dark python -m pytest tests/studio/test_take_e2e.py -k drafts   # One take screenshots
python scripts/build_sample_take.py talk.mp4   # rebuild the bundled sample take (needs requirements-worker.txt + ffmpeg)
python scripts/preflight_shots.py http://127.0.0.1:8899 docs/screenshots  # preflight screenshots, light + dark
python scripts/readme_shots.py http://127.0.0.1:8899 docs/screenshots   # README screenshots (app running with /demo)
python scripts/planner_shots.py http://127.0.0.1:8899 docs/screenshots
python scripts/inbox_shots.py http://127.0.0.1:8899 docs/screenshots    # Inbox, phone bar, phone agenda, tour
CHROMIUM_PATH=/usr/bin/chromium python scripts/readme_refresh_shots.py docs/screenshots   # Library, Bin, chat, Send a file (seeded, no server needed)
python scripts/responsive_audit.py http://127.0.0.1:8899   # every screen at 360-1440 px, light + dark: sideways scroll, taps < 44 px
STUDIO_TEST_PG=postgresql://... python -m pytest tests/studio -q   # same suite on Postgres
scripts/secret_scan.sh                      # gitleaks on full history + working tree (run before every push)
```

## Self-hosted mode

The pre-existing HuhQueue scheduler with native connectors (no Buffer needed) is in `huhqueue/` — see [docs/SELF_HOST.md](docs/SELF_HOST.md).
It is not part of the contest edition.

MIT licensed. Built fast with AI coding agents; any agent can read, edit and run this repo.
