# HuhQueue MCP tools

One MCP address for every app: `https://huhqueue-studio.onrender.com/mcp` (Streamable HTTP; OAuth 2.1 with PKCE for Claude and
ChatGPT, or `Authorization: Bearer hq_…` from a token made in **AI apps**). The same tools are plain REST (`/docs/api`), so
UI = REST = MCP.

## Bring your own brain

**Your model does the thinking, HuhQueue does the editing. Free.**

| | In the app | Through Claude / ChatGPT (MCP) |
|---|---|---|
| Brain (planning, reading the research, slide text, captions) | HuhQueue's agent on our Anthropic key | **Your** model |
| AI cost | Counts against your $1 allowance (then the free model) | **$0** to HuhQueue (render compute only) |
| Hands (read links and X posts, find official media, hero edit, render carousels and Reels, cut, caption, split, themes, brand check, Buffer drafts) | HuhQueue | HuhQueue, the same tools |

Rules (owner decision, 1 Nov 2026):

1. **MCP calls are client brain by default.** Over MCP, HuhQueue's Anthropic key is never reached unless the call carries
   `server_ai: true` (`studio/brain_llm.py`: `client_brain_active()` blocks the operator key for the whole call).
2. **Five tools are server AI**: `run_agent`, `make_post`, `reply_post`, `revise`, `edit_media`. Without `server_ai: true`
   they make no model call and answer with `server_ai_required: true` plus the client-brain sequence to do the same thing
   yourself. With `server_ai: true` they still don't run until the person approves (see "Paid and external APIs" below);
   then they run HuhQueue's agent and spend the user's allowance (the kill switch and daily caps still answer first).
3. Tools marked **client brain \*** below can write text (a draft caption, a recommendation). Over MCP they use the user's
   own model key or the free starter model if one is set, else the rule-based writer. Never HuhQueue's Claude.
4. Every client-brain call is written to the budget ledger as `source=mcp_client_brain` at **$0**, so Activity shows it.
5. Nothing reaches Buffer without the user: `submit_post` / `schedule_package` go through preview, the token's policy
   (review tokens wait in Approvals) and the daily cap. `mode=draft` is the default in every recipe below.

Heavy jobs (`edit_video`, `render_reel`, `split_video`, `render_template` video) return an id at once; poll
`render_status` every 10 to 20 seconds until `status` is `done` and read the download URL.

## One request = one run (owner decisions, 4 Nov 2026)

One prompt in Claude ("find the best headphones under EUR 150, make a carousel, edit the images nicely") used to leave
several Make a post threads and a Library item per render. The server now enforces (`studio/run_discipline.py`):

1. **One request = one run = one thread** (owner, 4 Nov 2026 16:14). `start_post(title, brief, user_asked: true, reason)`
   opens ONE post (a run in status `draft`) for the person's request and returns `run_id`. Follow-ups about the SAME
   content (switch carousel to reel, edit slide 3, two more reels of the same story) stay in that run and its thread. A
   NEW request with new content, even in the same chat, is a new run and thread: `start_post(new_request: true)` (it also
   skips the same-title dedupe), never one giant thread to scroll. The answer names the run the app was on
   (`previous_run_id`) in case the person meant that one. **When it's unclear** whether a request is about the same
   content, the app asks the person ("Continue in the current post or start a new one?") and then calls
   `continue_run(run_id, reason)` or `start_post(new_request: true)`. `continue_run` joins an existing run (the person's
   words go in its thread; `format` switches it, as `start_post(run_id, format)` does); a run already sent to Buffer or
   waiting in Approvals answers 409. Every answer from a tool that makes or changes a run (start_post, continue_run, the renders, uploads, create_package,
   the image tools, render_status) carries `run_id` and `thread_url`
   (`<app>/?tab=library&run=<id>`, which opens the thread in Make a post). The same request repeated within 10 minutes (same title, e.g. the app retried)
   returns the run it already opened with `deduped: true` (`new_post: true` forces a separate one).
2. **Pass `run_id` everywhere.** `render_carousel`, `hero_edit`, `edit_video`, `render_reel`, `split_video`,
   `library_upload_url`, `create_upload`, `import_from_link`, `create_package` and the image tools (`fetch_image`,
   `cutout_product`, `edit_image`, `compose_product_card`) take it. Follow-ups update the same run:
   `create_package(run_id)` replaces the post's packages **in place** and keeps the previous version
   (`packages[].undo`, `undo_package_media` goes back, 6 versions). A `run_id` of another account, or an archived one,
   answers 404.
   - **A change of format is the same run.** "I don't want a carousel, make it a reel": `start_post(run_id, format:
     'reel', reason)` switches the run's format (the person's words go in its thread), then `render_reel(run_id)` replaces
     output 1 and `create_package(run_id)` updates the post in place. Same run, same thread, same Library item.
   - **Several outputs for one request are one run.** "Make 3 reels": one `start_post`, then `render_reel(run_id,
     output: 1)`, `output: 2`, `output: 3`. The run lists them in `result.outputs` (`output`, `format`, `media_urls`,
     `library_id`, `versions`), its thread says "Reel 2 ready", and each output keeps ONE Library item.
     `create_package(run_id, output: n)` without `media_urls` stages that output's media. A video render becomes its
     output when it finishes (and `render_status` reports `run_id` and `output`).
3. **The open run is reused by follow-ups that forgot `run_id`.** A creator (`create_package`, external-brain
   `make_post`; not `start_post`) called without `run_id` while this token has an open run (the newest run it made,
   `draft`/`ready`, nothing sent to Buffer or waiting in Approvals, touched in the last `HQ_MCP_ACTIVE_RUN_S` = 2 h) works on
   that run and answers `reused: true`. Another app's token never lands in it.
4. **A new run, template or brand kit only when the person asked.** `new_post: true` needs `user_asked: true`;
   `start_post`, `create_template`, `create_brand_kit` and `set_active_brand_kit` need `user_asked: true` (plus a one-line
   `reason`).
5. **No hourly or daily limit on new runs** (owner, 4 Nov 2026 15:07: people open new chats in their AI app, and caps
   blocked them). Run IDs keep things together instead. The only guard is a lenient burst limit against a runaway loop:
   more than `HQ_MCP_NEW_RUNS_PER_MINUTE` (default 30) new runs in one minute from one agent token answers 429 "This app
   started 30 posts in the last minute, which looks like a loop. Wait a minute and try again." The token's daily action
   cap (writes to Buffer, uploads and the like) never blocks `start_post` either; the kill switch still does. A signed-in
   person in the app is never limited.
6. **Idempotency.** `idempotency_key` on any write (or `_meta["huhqueue/idempotency_key"]`): the same key and arguments
   within 24 h return the first result (`idempotent_replay: true`); the same key with other arguments is a 409. Without a
   key, an identical run/render call within 120 s (a retry) returns the first result too.
7. **One Library item per run output.** `render_carousel(run_id)`, `render_reel` / `edit_video(run_id)` and
   `create_package(run_id)` keep the output's ONE Library item and replace its files when the post changes (a carousel
   switched to a reel replaces it too); a render without `run_id` adds nothing to the Library. Uploads and imports are
   deduplicated by content (SHA-256): the same file twice returns the existing item with `deduped: true`.
8. **One thread.** Every call is a step on the run it names; a call without `run_id` lands on the token's open run, and
   calls made before any run are claimed by the next run the token makes.
9. **Tidy up.** `cleanup_agent_drafts` lists what apps left (runs never sent or approved, Library items no post uses) and
   changes nothing; with the single-use `confirm_token` the person OK'd, runs are archived (hidden from Make a post) and
   Library items go to the Bin (restorable for 7 days). Buffer is never touched.
10. **Your own research.** The agent searches and browses the web with its own tools; HuhQueue's tools read the links it is
    given.

## Paid and external APIs need the person's approval

An MCP agent never spends money or credits on its own (owner, 4 Nov 2026; `studio/consent.py`). A gated call (server AI with
`server_ai: true` today, and any credit-spending external API added later) answers:

```json
{"needs_user_approval": true, "approval_id": 41, "tool": "run_agent", "spent_usd": 0,
 "estimate": {"provider": "HuhQueue server AI", "usd_max": 0.15, "counts_against": "the person's HuhQueue AI allowance"}}
```

Nothing ran. The request waits in the person's **Inbox → Approvals** (the same queue as agent posts, `kind: paid_api`).
Only a signed-in person can approve it (an agent token, whatever its policy or channels, gets 403). After approval the same
call (same tool, same arguments, same token) runs **once**; a different call asks again. Or the person turns on **Allow paid
APIs** for one app in AI apps (`PATCH /api/agent-tokens/{id} {"allow_paid_apis": true}`, humans only, off by default); then
that app's paid calls run without asking and each one is written to Activity.

## Every tool


### Client brain: the hands (no HuhQueue AI, $0)

| Tool | Brain | What it does |
|---|---|---|
| `start_post` | no model | Open ONE post for the person's request (`user_asked: true`) and get its `run_id`. A new request or a new chat is a new run, never limited by count; the same request within 10 minutes returns it (`deduped: true`). With `run_id` (and `format`) it continues that post, e.g. switching a carousel to a reel. |
| `continue_run` | no model | Join an existing post (run) for a follow-up about the same content: the person's words go in its thread, and `run_id` + `thread_url` come back. Use it after asking the person when a request might belong to the current post; `format` switches the post. A post sent to Buffer or waiting in Approvals answers 409. |
| `cleanup_agent_drafts` | no model | List the runs and Library items AI apps left (dry run, changes nothing, single-use `confirm_token`); with the token the person OK'd: runs archived, items to the Library Bin (7 days). |
| `research_link` | client brain | Read an article, launch page or post URL: title, site, description, the article paragraphs, figures found in the text (each with the source URL), the page's images and videos. |
| `read_x_post` | client brain | The free X reader: an X/Twitter post's author, handle, text, date and its photos/videos (direct MP4 when the post has one), without an X API key. |
| `find_media` | client brain | Find the REAL launch/demo video or official images for a story or link on X, YouTube, Vimeo, Loom, YC launch pages, HN, GitHub and company pages (og:video). |
| `scrape_brand_assets` | client brain | Read a company's website (read-only, nothing is saved): name, logo candidates, palette, theme colour, social handles, og image. |
| `weekly_recap_plan` | client brain | Client brain (no HuhQueue AI): the week's top AI stories from the news sources, each with its official video/image and credit line. |
| `render_carousel` | client brain | Render a carousel YOU wrote in the user's brand kit. |
| `hero_edit` | client brain | The premium cover edit of an official image (no HuhQueue AI): saliency crop to the frame, colour match to the brand ground, vignette and fade; optional product cut-out tilted with a shadow. |
| `edit_video` | client brain | Edit or cut a video on the HuhQueue server (no HuhQueue AI): trim to start_s..end_s or cut_first_s seconds off the start, reframe to 9:16 / 4:5 / 1:1 (speaker-aware), burned-in word captions on or off (transcribed with Whisper on… |
| `render_reel` | client brain | Make a branded 9:16 Reel (no HuhQueue AI): from a video (same options as edit_video) or from slides (image URLs, e.g. render_carousel's media_urls) as a slideshow with the end card. |
| `split_video` | client brain | Split a long video into 3-7 short vertical clips on the video worker (no HuhQueue AI: Whisper transcript, built-in moment scorer, face-tracked reframe, captions, cover and end card). |
| `render_status` | client brain | Status of a video job: job_id (edit_video / render_reel / render_template video) or take_id (split_video). |
| `create_package` | client brain | Stage a post YOU wrote: captions per channel (channel id or service name -> text) and the media URLs (render_carousel / render_status). |
| `update_brand_kit` | client brain | Change the brand kit directly (no HuhQueue AI): name, handle, website, font, voice, colors {primary, accent, text} as hex, end_card {on, line, cta}. |
| `create_upload` | client brain | For a local file when the app can run curl (Claude Code, Codex, Cursor, scripts): a one-time upload URL on HuhQueue's own domain (`PUT /u/<token>`, 15 minutes, one use, up to 100 MB, `max_mb`) plus the exact curl command. The answer carries `if_unreachable`: what to do when the sandbox is network-blocked. |
| `upload_media_chunk` | client brain | Last resort for **small** files (a few MB): base64 chunks of at most 2 MB (decoded), in order, under one `upload_id`. Pass `total_size` on the first chunk: over 4 MB the answer carries `advice` (use the Send a file page or a share link); every error carries the same guidance. |
| `create_send_code` | client brain | **Best way in from a chat app** (Claude.ai, ChatGPT): a short code (`K7PD-3QXA`), a link `/send/<code>` and a QR code. The user opens it on any device and drags the file in (10 files, 30 minutes, Library caps). Only the code's hash is stored; **Stop receiving** or **New code** ends it at once, and a code an app made also stops when that app's token is revoked or expires, or while the kill switch is on. |
| `list_recent_uploads` | client brain | The newest files the user uploaded, sent from a device or imported from a link (`minutes`, `limit`): `library_id`, `media_token`, `name`, `kind`, `bytes`, `from`, `when`. |
| `import_from_link` | client brain | A Google Drive, Dropbox, OneDrive / SharePoint or iCloud Drive share link ("anyone with the link"), or any direct https link, streamed into the Library up to the per-file limit. Drive's large-file "Download anyway" page is followed; a sign-in page says how to share the file publicly; folders are refused. |
| `finish_upload` | client brain | Join the chunks into one Library item: `library_id`, `media_token`, `url`, `kind`, `bytes`. |
| `library_upload_url` | client brain | Only for a file that is already at a public https URL (up to 40 MB). A cloud-drive share link goes through `import_from_link` (up to the per-file limit). A link that answers with a web page (a sign-in or preview page) is refused with "That link returned a web page … Use create_upload instead …". |
| `integrations_status` | client brain | Which integrations are connected for this user (Product Hunt, X reader key, image models...) and whether each key last tested OK. |

### Server AI: HuhQueue's own agent (needs server_ai: true, spends the user's allowance)

| Tool | Brain | What it does |
|---|---|---|
| `run_agent` | **server AI** (`server_ai: true`) | HuhQueue's research agent. |
| `make_post` | **server AI** (`server_ai: true`) | Make on-brand posts from one prompt (prose + links, up to 4,000 characters). |
| `reply_post` | **server AI** (`server_ai: true`) | Refine a post run in plain words, like replying in its thread: 'make the hook stronger', 'redo YouTube at 30s', 'LinkedIn more formal', 'logo smaller', 'it's $1.3M' (answers a fact question). |
| `revise` | **server AI** (`server_ai: true`) | Give a correction in plain words ("logo smaller, top left", "no hype", "use #FF5A1F"). |
| `edit_media` | **server AI** (`server_ai: true`) | Edit the user's own photo or video (public https URL) to their brand kit: crop to the network frame, logo bug, hook headline, end card, captions, brand check. |

### Buffer, posting and the Planner

| Tool | Brain | What it does |
|---|---|---|
| `whoami` | no model | The Buffer account and organization this token acts for, its policy, and whether the kill switch is on. |
| `list_channels` | no model | Buffer channels you may post to, with each network's character limit and media needs. |
| `get_limits` | no model | Buffer API budget left for this app, per-network character limits, and how close the Buffer plan is to its limits (scheduled posts, channels, weekly caps). |
| `preflight_post` | no model | Dry run (read-only): check a post against every chosen channel's real Buffer and network rules before you preview or submit it: text length counted the way Buffer counts it (UTF-16, Instagram line breaks 2, LinkedIn URLs 24, X… |
| `autofix_post` | no model | Apply preflight fixes for one channel (fixes: list of fix ids from preflight_post, or omit for all): trim or thread the caption under the network's count, keep the first N media, crop/convert/recompress images to the network's… |
| `list_rules` | no model | The preflight rule pack: every limit HuhQueue enforces per network, with the official doc URL it was verified against and the date, plus rules that could not be verified (not enforced). |
| `preview_post` | no model | Validate a post for one channel and get a single-use confirm_token. |
| `submit_post` | no model | Submit exactly what preview_post validated. |
| `list_approvals` | no model | Your submissions and their status (pending, approved, rejected). |
| `list_pending_approvals` | no model | The person's video links waiting for the rights statement (any of their AI apps): `approval_id`, url, host, which app asked, when, and the statement. Paid-API and post approvals are not listed here; those are decided in HuhQueue → Inbox. Read-only. |
| `approve_link_ingest` | no model | Approve one waiting video-link request on the person's word. Requires `i_own_or_have_rights: true` (anything else is a 400 and approves nothing); call it only after the person told you they own the video or have permission to use it and are responsible for rights. Owner-scoped: a foreign, missing, already decided or non-link `approval_id` is a 404. The consent record stores which app approved it and when (`link_consents.approved_by_app`, `approved_at`; Activity `approve_link_rights` with `by`). Then call `ingest_video_link` again with the same url and `approval_id`. Never spends money, never posts; every fetch cap still applies; the kill switch stops it. |
| `reject_link_ingest` | no model | Reject one waiting video-link request. Nothing is fetched. Same 404 rule. |
| `schedule_package` | no model | Send one ready package from a run to Buffer (mode draft / queue / next / now / schedule with due_at ISO UTC). |
| `search_instagram_audio` | no model | Instagram licensed music + original sounds for one Instagram channel (blank query = trending), through Buffer's audio catalog. Needs the channel connected through a Facebook Page in Buffer; otherwise `needs_facebook: true` with a help link. Read-only. |
| `set_reel_audio` | no model | Put a track from `search_instagram_audio` on one Instagram Reel package (`music_volume` / `original_volume` 0-100), or remove it with `audio: null`. Sent to Buffer as `metadata.instagram.audio` with the package. Buffer's API has no start-time field, so Instagram picks which part of the track plays (a `start_ms` is kept with the package only). |
| `update_package_draft` | no model | A package changed after it was saved as a Buffer draft (`changed_since_sent` + `buffer_draft` on the package): put its current media and caption on that same draft (editPost). A draft deleted in Buffer is saved again; a scheduled or sent post is never changed. Same as the card's **Update draft**. |
| `undo_package_media` | no model | Go back to a channel's previous media (`channel` = id or network; omit for the latest change on every channel it touched). Every media change keeps the version before it (6 per channel); `packages[].undo.label` says what Undo goes back to ("Back to the Reel"). |
| `list_music` | no model | Each package's Music: `none` (the default: HuhQueue never adds music by itself) or its Instagram track, and `supports` (`instagram` = a Reel, `app_only` = an Instagram carousel or image: added in the Instagram app, `none`). For a Reel, Instagram tracks for `query` (blank = trending). Read-only. |
| `set_post_music` | no model | `music: none` removes it; `music: instagram` with `audio` (a track from `list_music`) or `query` ("lofi": the top track is picked, `alternatives` returned). Neither → `needs_choice` with trending tracks: ask the person. A carousel answers `music: unsupported` with the in-app instruction. Same as replying "add music", "use lofi", "no music". |
| `use_brand_kit` | no model | One run's brand kit by `kit` (name or id from `list_brand_kits`); `default: true` also makes it the account's active kit. No `kit` → `needs_choice` with the available kits: ask, never guess. |
| `get_run` | no model | An agent run: every step with what it did, the rendered media URLs, and one ready package per channel (caption, media, brand check). |
| `list_queue` | no model | Posts in Buffer by status: scheduled (default), draft, sent or error (failed to publish). |
| `update_post` | no model | Edit a post already in Buffer: new caption (text), new time (due_at, ISO UTC), or both. |
| `reschedule_post` | no model | Move a scheduled post or draft in Buffer to a new time (due_at, ISO UTC). |
| `delete_draft` | no model | Delete a Buffer draft (drafts only; scheduled and sent posts are refused). |
| `create_idea` | no model | Save an idea to the Buffer organization's Ideas board (private, not published). |
| `get_inbox` | no model | Your user's Inbox: everything waiting for a person, most urgent first. |
| `list_calendar` | no model | Everything on the user's Planner: scheduled, sent and draft Buffer posts, agent posts waiting for approval, ready packages not yet sent, and each channel's Buffer posting slots. |
| `get_activity` | no model | The account's activity log, newest first, paginated by cursor. |

### Brand kit, Style Ledger and looks

| Tool | Brain | What it does |
|---|---|---|
| `update_brand_kit` | no model | Change kit fields; no `kit_id` = the active kit (one tool: the client-brain duplicate was removed on 2 Nov 2026). |
| `get_brand` | no model | The user's brand kit (name, handle, colors, font, logo, end card, voice) and the Style Ledger rules. |
| `brand_check` | no model | Check a caption (and optionally a HuhQueue media token) against every Style Ledger rule. |
| `remember_rule` | client brain \* | Save a brand rule to the Style Ledger without a run (e.g. from the user's feedback on past posts). |
| `edit_brand` | client brain \* | Change the brand kit in plain words: 'make the logo smaller', 'use #FF5A1F', 'switch font to Poppins'. |
| `list_brand_examples` | no model | The example brands in the sample workspace (HuhByte AI; Buffer, a concept example not affiliated with Buffer) with their palettes, fonts, channels and pre-rendered example gallery. |
| `apply_brand_example` | no model | Switch the workspace's brand kit to an example brand (huhbyte or buffer): logo, colours, fonts, front and end cards, hashtags, channels. |
| `list_themes` | no model | Every video look for Make a post: the four edit styles (opus, huhbyte_news, minimal, hype) and the video themes (caption_news, news_package, newsreel, dark_keynote, glass_studio, swiss, kino, silent_film, risograph, hologram_hud… |
| `set_theme` | no model | Set a post run's video theme and re-render its video channels in it (e.g. 'cinematic', 'news_package'). |
| `replace_channel_media` | no model | Change ONE channel's media in a post run: media_token (a Library item's media_token from list_library / use_library_item, or an upload) uses the person's own file; omit it to regenerate that channel's media. |

### One take (your own long video -> a week of clips)

| Tool | Brain | What it does |
|---|---|---|
| `clips_from_video` | no model | One take -> a week. `jev: true` asks TypeSafe Jev for a second opinion on the speaker's face on a few keyframes (paid, HuhQueue's key): it answers `needs_user_approval` until the person approves (studio/consent.py; then call `clips_from_video` again with `jev: true`). Asked from the server with its own key, capped per take and per account per day (free). Off by default for apps; local face detection always runs. `punch_in` (default true) adds a gentle auto zoom (1.0→1.12, eased 0.4 s) on face-tracked shots at scene starts and emphasised words, never cropping a face; `false` turns it off for the take. A `url` (a YouTube / Vimeo / X post or an MP4 link) goes through the same rights approval as `ingest_video_link`. `trim_pauses` (default true for One take) cuts long pauses and filler words from each clip; each clip reports `trimmed_s`. |
| `ingest_video_link` | no model | One take from a video **link** (YouTube, Vimeo, X, or a direct MP4). Claude calls this instead of asking the person to upload. The first call answers `needs_user_approval` (an item in HuhQueue → Inbox → Approvals with the rights statement *"I own this video or have permission to use it (e.g. my own content, a licence, or Creative Commons); I'm responsible for rights."*). The signed-in person ticks it and approves there; or, once the person tells the app they own the video or have permission, the app relays that with `approve_link_ingest` (`i_own_or_have_rights: true`, recorded with the app's name and the time). An app never confirms the rights on its own. The person's own Settings → **Auto-approve video links** (off by default, only a human can turn it on, with the statement and its time recorded; all apps or picked apps; revocable) answers this at once for the apps it covers, logged in Activity as `auto_approve_link_rights` ("auto-approved"). No token setting skips it, and none of this covers paid APIs or posting. Then call again with the same `url` and `approval_id`: the take starts and the video worker fetches the link (Make a post's own path: yt-dlp with only that platform's extractor, mp4 up to 1080p, no cookies or accounts; 4 GB and 3 h caps; direct links through netguard). A refusal names the reason (age-restricted, private, members-only, unavailable, or a bot check after the public-media fallback) and only then asks for the file. `licence_hint` carries a Creative Commons flag when the platform states one. |
| `list_clips` | no model | Without take_id: the user's takes. |
| `adjust_clip` | no model | Trim & caption one clip of a take (the same edits as the UI's **Trim & caption** window), any of: `start_ms`/`end_ms` (in/out in the source video; the sample take only inside its moment), `transcript` (the clip's corrected words: fixes the burned-in captions, timings follow the voice), `hook` (the cover line: at least 3 words quoted from the clip), `caption` + `service` or `captions {service: text}` (the post caption per network: only the take's channels, each within its network's limit), `music` (an Instagram track from `search_instagram_audio` with `music_volume` / `original_volume` 0-100; `null` removes it). Timing, transcript and hook re-render the clip on the video worker (poll `list_clips` until it is ready: it gets a new url); captions and music save at once. Returns the take with `changed` and `rerendering`. |
| `rewrite_clip_caption` | your AI engine | An AI rewrite of one clip's post caption for one of the take's networks (`service`, optional `text` to improve), from only what was said in the clip (the transcript is data, not instructions). Runs in the person's engine order (AI engine settings); 503 when none is set up. Returns `caption`, `count`, `limit`, `engine`, `saved: false`: nothing is saved until you pass it to `adjust_clip`. REST: `POST /api/takes/{id}/clips/{clip}/rewrite`. |
| `get_clip_layout` | no model | A One take clip's layout plan, scene by scene: `{scene, start, end, layout, rule, pinned}` with `layout` one of `track` (9:16 crop following the face), `fit` (the whole frame over a blurred or brand-colour background), `split` (two people stacked, captions on the seam) or `screen` (a screen recording with the speaker's face, `style` `pip` or `stack`); the clip's per-scene `overrides`, its `caption_preset` and the choices for both. Read-only. Someone else's take or clip and a missing one are both 404. REST: `GET /api/takes/{id}/clips/{clip}/layout`. |
| `set_clip_layout` | no model | Change a One take clip's layout for one scene (`scene`, its index in `get_clip_layout`'s plan) or for every scene (no `scene`): `layout` `track` / `fit` (`bg` `blur` or `brand`) / `split` / `screen` (`style` `pip` or `stack`) or `auto` to go back to the automatic choice; and/or `caption_preset` (`bold_pop` default, `lower_third`, `two_tone`, `typewriter`). A pinned layout always wins over the automatic rules. The clip re-renders on the video worker (poll `list_clips`). Wrong types or values are 400; someone else's take or clip and a missing one are 404; a take still rendering is 409. REST: `POST /api/takes/{id}/clips/{clip}/layout`. |
| `place_clips_in_week` | no model | The Spread: put a take's ready clips into the open Buffer posting slots of a week as DRAFTS (never published). |
| `delete_take` | no model | Delete a One take, or one clip of it (`clip_id`), into the Bin: restorable for 7 days, then removed for good with its files (clips, covers, other-ratio renders, the uploaded source). A job still rendering it is cancelled. Someone else's take and a missing one both answer 404. REST: `DELETE /api/takes/{id}`, `DELETE /api/takes/{id}/clips/{clip_id}`. |
| `list_take_bin` | no model | One take's Bin: `takes` and `clips` deleted in the last 7 days with `days_left`. In the app they are listed in the Library Bin (one Bin, 5 Nov 2026). REST: `GET /api/takes/bin`. |
| `restore_take` | no model | Restore a take (or one clip, `clip_id`) from the Bin with its files; a render cancelled by the delete comes back as failed (trim to render again). REST: `POST /api/takes/{id}/restore`, `POST /api/takes/{id}/clips/{clip_id}/restore`. |
| `purge_take_bin` | no model | Delete Bin takes / clips permanently: `take_ids`, `clips` [{take_id, clip_id}] or `all: true`. Can't be undone; records and files go in one transaction. REST: `POST /api/takes/bin/purge`. |
| `list_threads` | no model | The person's earlier threads (Make a post and One take), newest first: `id` (public `thr_...`, never an int), `title`, `updated_at`, `run_ids`, a short `summary`. `query` matches titles and summaries; `limit` 1-50; pass `next_cursor` back as `cursor`. Own threads only. REST: `GET /api/threads`. |
| `get_thread` | no model | One thread by `thread_id`: its `messages` (oldest first, 50 a page, `cursor`), and the `runs` / takes inside with their `outputs` (`packages`: captions per channel and media; `renders`: an AI app's finished renders before any package; or a take's `clips`). Then `continue_run(run_id)`. A foreign or missing id answers 404; a purged thread also refuses `continue_run`, `start_post(run_id)` and replies (404). REST: `GET /api/threads/{thread_id}`. |
| `delete_thread` | no model | Delete a thread (Make a post or One take, in any state, failed ones too) into the Bin for 7 days, like deleting chat history: only the conversation goes. Media is never deleted (takes, clips, uploads, posts, Library files stay); `delete_take` deletes a take. Someone else's thread and a missing one both answer 404. REST: `DELETE /api/threads/{thread_id}`. |
| `restore_thread` | no model | Restore a deleted thread from the Bin (within 7 days). REST: `POST /api/threads/{thread_id}/restore`. |
| `list_thread_bin` | no model | Threads deleted in the last 7 days (`thread_id`, `title`, `kind`, `days_left`). REST: `GET /api/threads/bin`. |
| `purge_thread_bin` | no model | Remove threads in the Bin for good: `thread_ids` or `all: true`. Only the conversation goes (messages, summary, custom title, MCP steps); media stays. The id stays a 404 for good (a tombstone). REST: `POST /api/threads/bin/purge`. |

### Library

| Tool | Brain | What it does |
|---|---|---|
| `list_library` | no model | The person's Library: every uploaded photo/video and every image, carousel and Reel the agent made, with type, size, date, which posts used each one and `run_id` (the run that made it, for View run; null for uploads). `offset`/`per` page it (`total`); `bin_count` is how many items are in the Bin (Library items plus One take's binned takes and clips: one Bin). `created_by`: `agent`, `you`, `sample` or `one_take` (the clips One take rendered and the videos uploaded to it, `one_take: true`; a clip carries `take_clip {take_id, clip_id}`, so delete it with `delete_take`); any other value answers 400. A take or clip in the Bin is not listed. |
| `use_library_item` | no model | Use a Library item in a new post. |
| `delete_library_item` | no model | Delete one Library item. `mode`: `bin` (default, restorable for 7 days) or `permanent` (the file and its record are removed now; can't be undone). Warns (409) while a post that hasn't gone out or a post scheduled in Buffer uses it (`force: true` goes ahead); a permanent delete of media a scheduled post uses is always refused. REST: `DELETE /api/library/{id}?mode=bin\|permanent&force=0\|1`. |
| `bulk_delete_library` | no model | Delete up to 200 items (`ids`, `mode` bin \| permanent, `force`). Each item is checked like a single delete; returns `deleted` and `refused` (with `error`, `needs_force`). REST: `POST /api/library/bulk-delete`. |
| `list_library_bin` | no model | The Bin: items deleted in the last 7 days with `days_left`, `purge_at`, `used_in`, `scheduled_use` (`offset`, `per` page it). REST: `GET /api/library/bin`. |
| `restore_library_items` | no model | Restore `ids` from the Bin (their files come back for every post that used them): `restored`, `missing`. REST: `POST /api/library/bin/restore`; one item: `POST /api/library/{id}/restore`. |
| `purge_library_bin` | no model | Delete Bin items permanently: `ids`, or `all: true` (Empty bin). Can't be undone; media a scheduled post uses is refused. REST: `POST /api/library/bin/purge`. Items left in the Bin are removed for good after 7 days anyway. |
| `draft_from_library` | no model | Create draft from a Library item: without `channels` it returns the prefill (`media`, per-channel `text` from the item's run, `selected`, `run_id`); with `channels: [{channel_id, text?}]` it saves Buffer DRAFTS (never published) through the same preview -> submit path as every composer (`dry_run: true` previews only). REST: `GET` / `POST /api/library/{id}/draft`. |

### Sources, templates and recurring slots

| Tool | Brain | What it does |
|---|---|---|
| `list_stories` | client brain * | Newest stories from the news feeds your user follows in HuhQueue (news radar). |
| `draft_from_link` | client brain * | Read an article URL and draft one caption per channel, each within that network's Buffer character limit. |
| `sources_scan` | client brain * | The Sources agent: read the user's sources (YC launches, Hacker News front page, RSS/Atom lab blogs, Product Hunt with the user's free developer token, and X only with the user's own X API key), dedupe, cluster the same story… |
| `sources_draft` | client brain * | Turn clusters from a sources_scan run into one package per channel in the brand: the caption written for the channel (length, hashtag count, format), a breaking-news cover rendered in the brand kit at the channel's frame. |
| `sources_settings` | client brain * | Read (no arguments) or change which sources the agent reads: on {yc, hn, rss, ph, x}, rss (feed URLs, up to 8), x_query, hn_query. |
| `list_templates` | client brain * | The brand's post templates (weekly recap, mid-week recap, breaking cover, launch carousel, quote/CEO clip, free-way-to tutorial), the channel frames and slide limits, and the front/end card styles. |
| `render_template` | client brain * | Render a template in the user's brand for each channel (front card first, end card last). |
| `get_template_video` | client brain * | Status of a template Reel the video worker is rendering (render_template's video_job.id), with its URL when done. |
| `schedule_template_slot` | client brain * | Add a recurring Planner slot for a template (e.g. weekly_recap every mon 20:00 Europe/Dublin). |
| `templates_slot_run` | client brain * | Fire a recurring template slot now: render it from this week's posts and put one Buffer DRAFT per channel at the slot's next time. |
| `create_template` | no model | Build a NEW custom template for this workspace (funding announcement, job opening...): name, base layout (kit / bold_news / editorial / minimal / playful), fields, up to 8 slides (front, bullets, story, stat, quote, breaking) with `{field}` placeholders. Renders in the active kit, Follow end card last, shown in the Templates tab as `custom:<slug>`, recorded in the Style Ledger. Max 20 per workspace. |
| `get_template` | no model | One template by id or name: a built-in (`built_in: true`) or a custom one (fields, slides, layout, `made_by`). Read-only. |
| `update_template` / `delete_template` | no model | Change or delete a custom template (`template: custom:<slug>`). An AI app may change or delete only a template **it created**; the person's own and built-in ones are refused with a pointer to `create_template`. |

### Onboarding and the in-app brain

| Tool | Brain | What it does |
|---|---|---|
| `get_onboarding` | client brain * | The person's content types, channels, website, tone and X watchlist, with the options to choose from. |
| `set_onboarding` | client brain * | Save onboarding choices: content_types (ai_news, product_launches, yc_startups, tutorials, dev_tools, memes, india_tech, research, funding, open_source), channel_ids, tone, website (builds the brand kit), x_watchlist (handles or… |
| `get_recommendations` | client brain * | A feed tailored to the person's content types plus up to three ready-to-approve posts in their brand (cover + captions). |
| `get_ai_engine` | client brain * | The in-app agent's AI engine (gemini-studio, nvidia, hf, claude, own, free or auto), which one will actually run, each engine's logo, free/paid tier, usage ("3 of 150 calls today", "$0.34 of $1") and the app-wide pools. A key you saved for an engine runs it. |
| `set_ai_engine` | client brain * | Choose the in-app agent's engine. Out of credit or erroring, it falls back to the next one automatically (a note in the run). |
| `get_ai_logs` | read | Recent AI calls: time, engine, model, task, latency, tokens, cost, fallback reason, ok/error. Filters engine, status, task, q; paged. |
| `list_ai_keys` | read | Your own AI keys per provider (Anthropic, OpenAI, Google AI Studio, NVIDIA, Hugging Face, Groq, OpenRouter, custom), masked. |
| `save_ai_key` | people only | Test and save your key for a provider; agent tokens are refused (use Settings > AI engine). |
| `delete_ai_key` | people only | Delete your key for a provider; agent tokens are refused. |
| `get_brain` | client brain * | Which AI runs the in-app agent for this account (Claude, their own key, the free model or the rule writer) and the included budget left. |

### Run tools (`post_*`): one step of a Make a post run

These act on one post run (`run_id`), the same steps HuhQueue's in-app agent takes. A client can drive a run step by step with them. Over MCP the text-writing steps (`post_extract_facts`, `post_write_captions`, `post_recap_plan`) follow rule 3: your own key or the free model, never HuhQueue's Claude.

| Tool | Brain | What it does |
|---|---|---|
| `post_add_end_card` | client brain \* | Set the end card line for every future video (a Style Ledger rule), e.g. 'Follow @huhbyte'. |
| `post_check` | client brain \* | Check every channel (brand rules, clipped text, about the source, fact conflicts) and build the packages. |
| `post_edit_brand` | client brain \* | Change the brand kit from plain words: 'make the logo smaller', 'use #FF5A1F', 'switch font to Poppins'. |
| `post_edit_channel_media` | client brain \* | Re-render ONLY one channel's media: seconds (Reel length), format (reel/carousel/photo), captions on/off. |
| `post_extract_facts` | client brain \* | Pull the headline, 3 facts, why it matters and a label from the source, and cross-check money figures against other outlets. |
| `post_find_media` | client brain \* | Find the REAL launch / demo video for the story on any platform (X, YouTube, Vimeo, Loom, YC launch pages, HN, GitHub, the company's own site). |
| `post_generate_images` | client brain \* | No usable video or photo: make n images for the story (the configured image model, else on-brand cards), labelled as AI-generated. |
| `post_list_templates` | client brain \* | The brand's post templates (HuhByte formats: editorial cover, text slides, launch Reel, weekly and mid-week recap, GitHub repo pillar, YC launch, viral trend; plus breaking, quote, tutorial). |
| `post_list_themes` | client brain \* | Every video look: the four edit styles and the video themes, with what each looks like, what it is best for and its gallery thumbnail, plus the theme this run uses. |
| `post_read_upload` | client brain \* | Read the person's OWN uploaded photo or video (context.upload). |
| `post_read_url` | client brain \* | Read a link the person gave: an article (direct read, Firecrawl fallback) or an X post (public oEmbed; media only with the person's X API key). |
| `post_recap_plan` | client brain \* | Weekly recap ('make a 7 days what happened in AI recap with videos', 'weekly recap'): the week's top AI stories from the news sources, each with its official video/image and a credit line. |
| `post_remember_rule` | client brain \* | Turn a standing instruction into a Style Ledger rule that applies to every future post ('end every video with Follow @x', 'never say game-changer'). |
| `post_render_carousel` | client brain \* | Render a carousel (4:5): cover, the post card or facts, end card last. |
| `post_render_cover` | client brain \* | Render the cover card (4:5) in the brand for text-first channels. |
| `post_render_recap` | client brain \* | Render the planned weekly recap on the video worker: a 4:5 carousel (cover, one video slide per story with its credit, the Follow card only at the end) plus one 9:16 Reel. |
| `post_render_reel` | client brain \* | Render a 9:16 Reel: the headline on every frame, the source clip if there is one, the end card last. |
| `post_render_template` | client brain \* | Render a post template in the brand for the run's channels (each at its frame) instead of the default carousel, using the story's facts and hero image unless data says otherwise. |
| `post_save_buffer_drafts` | client brain \* | Save the ready packages as Buffer DRAFTS (never published). |
| `post_search_news` | client brain \* | Find recent stories for a topic (Bing News, Hacker News, the person's feeds). |
| `post_set_theme` | client brain \* | Set this run's video theme (the person picked one, or asked 'make it more cinematic' / 'switch to the news theme') and re-render its video channels in it. |
| `post_write_captions` | client brain \* | Write one caption per channel in the brand voice and the ledger's text rules. |
| `post_x_oembed` | client brain \* | Read an X/Twitter post through the public oEmbed endpoint: author, handle, text, date. |

190 tools in total (from `tools/list`, 6 Nov 2026: 187 plus the three registry tools below; +`list_style_presets`, `apply_style_preset` of #134; the three product tools of #112 were removed on 6 Nov; after #94, #96, #97, the One take Bin and thread tools of #107 and #108, `ingest_video_link` of #111, the layout tools of #113 and `list_pending_approvals` / `approve_link_ingest` / `reject_link_ingest`; `test_tools_list_parity_and_page_context` checks the count matches). Tools added since the tables were last filled in carry their full description in `tools/list` itself: `create_brand_kit`, `list_brand_kits`, `set_active_brand_kit`, `preview_brand_kit`, `preview_brand_themes`, `extract_brand`, `list_sample_brands`, `schedule_sample_brand_story`, `update_template`, `delete_template`, `set_cover`, `find_video`, `list_sources`, `refresh_sources`, `set_source_enabled`, and the run steps `post_create_template`, `post_edit_video`, `post_find_brand_assets`, `post_find_video`, `post_hero_edit`, `post_render_editorial`, `post_research_company`, `post_set_cover`, `post_write_editorial`.

## Tool registry: search_tools, describe_tool, call_tool (5 Nov 2026)

With ~190 tools, the full `tools/list` is about 128 KB of JSON (~30,000 tokens, cl100k) before an agent does anything.
An agent can load three tools instead and look the rest up as it needs them. `tools/list` is unchanged by default (the
full list); connect to `/mcp?tools=registry` (or send `tools/list` with `_meta: {"huhqueue/tools": "registry"}`) to get
only these three. Every tool stays callable directly either way.

| Tool | Brain | What it does |
|---|---|---|
| `search_tools` | no model | Keyword search over tool names and descriptions: `query` (≤200 characters), `limit` 1-20 (default 8). Returns `name`, a one-line `summary` and the `required` arguments, best match first. Read-only; reads only the static tool list. |
| `describe_tool` | no model | One tool's full `description`, `inputSchema` and `read_only`, by `name`. Unknown name: 404. |
| `call_tool` | as the named tool | Runs one tool: `{name, arguments}`. It is unwrapped before every gate, so the named tool meets exactly what a direct `tools/call` meets: kill switch, token policy and channel scope, the paid-API / consent gate (`needs_user_approval`, studio/consent.py), the server-AI gate, run discipline (caps, idempotency), the tool's own rate limits, and the Activity record under the named tool. Arguments are checked against that tool's `inputSchema` first (required, type, enum, range, array items; 400 and nothing runs). Unknown tool: 404. It can't call `search_tools`, `describe_tool` or itself (400), and only `name` and `arguments` are accepted. |

A typical task ("post this to Instagram": registry `tools/list` + `search_tools "preview post"` + `describe_tool
preview_post` + `describe_tool submit_post`) is ~3.9 KB / ~900 tokens against ~128 KB / ~30,000 tokens for the full list:
about 97% less context.

## Runs made from an AI app, previews, draft media and page context (3 Nov 2026)

Owner report: a Reel made from Claude over MCP showed "Model: nvidia/nemotron-3-ultra-550b-a55b" (HuhQueue's engine, which
never ran) and "Worked through 0 steps".

* **Who made the run.** A run an AI app made over MCP (`create_package`, `make_post` with `brain: "external"`, or any run its
  tool calls touch) shows **"Claude · via MCP"** (from the MCP `initialize` clientInfo, e.g. `claude-ai 0.1.0`, and the agent
  token's name) with the hint *"The model is chosen in Claude — see that chat for the model used."* HuhQueue never claims a
  model it didn't run; a `_meta.model` sent with a tool call is shown as "Claude reported the model …". `get_run` and
  `GET /api/agent/runs/{id}` carry it as `result.brain` (`mode: "mcp"`, `label`, `hint`, `client`, `client_version`, `token`)
  and `result.mcp`.
* **What it did.** Every MCP tool call an agent token makes is a visible step on the run (`via: "mcp"`, tool, a short
  summary, `done | failed`, duration). Calls made before the run existed (e.g. `research_link`, `render_reel`) are claimed by
  the run the same token creates next (within 3 hours). Polling and lookups (`get_run`, `render_status`, `list_*`, `whoami`…)
  are not steps. Summaries never hold captions, keys or tokens. The steps live in their own table (`mcp_calls`), merged when
  a run is read, so they never race a running agent; the thread never says "Worked through 0 steps" (no steps, no row).
* **The Model switch.** The engine picked in Make a post runs. A pick without tool use (the free starter model) used to be
  skipped for the first tool engine (NVIDIA); it now writes the standard plan. A pick that can't run right now is said once in
  the thread. The chip and footer update at once after a change, and an open thread says `· next reply: <model>`.

| Tool | Brain | What it does |
|---|---|---|
| `get_media_preview` | no model | SEE what you made: frame stills as MCP `image` content (default at 0, 1 and 2 s; `frames_at` up to 6, `width` 160-720) of the person's own video, or a thumbnail of an image, plus `signed.url`, a download link valid for 15 minutes (`/ms/<exp>/<sig>/<token>`, HMAC). `media_token` or `url` (a HuhQueue `/m/<token>` link). Another person's media is "not found". 20 previews per 10 minutes. REST: `GET /api/media/{token}/preview`. |
| `replace_draft_media` | no model | New media on a Buffer **draft**, including drafts HuhQueue didn't make. Caption, channel and settings stay (and an Instagram Reel's music when Buffer reports it); `text` / `options` (e.g. `thumbnail_offset_ms` for the cover frame) change them. `strategy`: `auto` (edit in place; if Buffer refuses, a new draft with the same caption + the new media), `edit` or `recreate`. The old draft is deleted only with `delete_old: true` **and** `confirm: true`, after the new one exists. Scheduled and sent posts are refused. REST: `POST /api/buffer/drafts/{post_id}/media`. |
| `get_page_context` | no model | Each app page (Make a post, Planner, Inbox, Library, Templates, Brand kit, One take, Settings > AI engine, Activity): what it shows and which tools do the same things there, each with its one-line description, plus `tool_count`. REST: `GET /api/agent/pages`. |
| `get_post_context` | no model | A Make a post thread's context as the agent reads it (source, rights, kit, template, summarized turns). |
| `rename_post_thread` | no model | Rename a Make a post thread (blank title = the automatic one). |
| `get_post_templates` | no model | A run's template, the suggested others, and with `all: true` every built-in and custom template (custom ones show "Yours"). |
| `resume_post` | no model | Resume a run the server cut off (status `interrupted`) from its last finished step. |
| `stop_post` | no model | Stop the agent working on a post run after the step it is on; what it made is kept and `resume_post` carries on. Same as the chat's Stop button and `POST /api/agent/runs/{id}/stop`. |

Templates an AI app makes are the person's: `create_template` always makes a NEW template (a name already taken gets a new
id, never an overwrite), it shows in All templates as **Yours** (`yours: true`, `owner: "yours"`), and `made_by` says
"Claude (via MCP)". The app may then change or delete only the ones it made.

Reschedule: `reschedule_post` / `update_post` (`due_at`) use Buffer's `editPost`; review and draft tokens may only move
drafts, trusted tokens may move scheduled posts too. Cover/thumbnail: `set_cover` (a run's 3 cover variants) or
`thumbnail_offset_ms` in `replace_draft_media` / `create_package` `cover.frame_s`. Music: `list_music`, `set_post_music`,
`search_instagram_audio`, `set_reel_audio`. Library: `list_library_bin`, `restore_library_items`, `purge_library_bin`,
`bulk_delete_library`, `draft_from_library`.

## Image editing on HuhQueue's side (4 Nov 2026)

The server fetches and edits pictures, so the AI app never downloads or pastes image bytes. Every tool takes `run_id`
(the post this belongs to) and stores what it makes as **run media** of that post: no Library item is added per edit
(the Library gets one item per finished post). Each edit is a new version; `prev` names the one it was made from.
No HuhQueue AI is used ($0). With a `run_id` the answer also carries `thread_url`; the identical call repeated within
120 s (a retry) answers the first result instead of fetching, cutting out or storing the picture again.

| Tool | What it does |
|---|---|
| `fetch_image` | `url` (public https) -> `media_token`. Every redirect hop vetted (netguard), up to 15 MB, JPG/PNG/WebP/AVIF only, ~40 MP cap, location/camera metadata removed. `ok: false` with `reason`: `blocked_by_site` (401/403: the site blocks servers; `ask` has the words to ask the person to upload the file, then `create_send_code` / `create_upload`), `not_found`, `too_large`, `private`, `not_an_image`, `unreachable`, `bad_url`. |
| `cutout_product` | `image` (media_token or https URL), `engine`: `fast` (Pillow on the server, instant), `auto` (default: fast, then the ML cut-out on the worker when unsure), `ml` (ISNet `isnet-general-use`, Apache-2.0), `hq` (BiRefNet `birefnet-general-lite`, MIT). ml/hq answer `job_id`: poll `render_status(job_id)` for the PNG. A torn cut-out is never returned. |
| `edit_image` | `image`, `ops` (max 12, in order): `crop {box}`, `fit {ratio, mode: cover\|contain, focus}`, `pad {ratio, colour}`, `cutout {}`, `card {ratio, ground, scale, shadow, radius, name, price}`, `round {radius}`, `grade {preset, exposure, contrast, saturation}`, `sharpen {amount}`. |
| `compose_product_card` | `image`, `ratio`, `name`, `price`, `ground`: the product on the brand ground with a soft shadow (cut out when it stands on a plain background, otherwise the whole photo as a rounded card, never zoomed in). |

REST: `POST /api/images/fetch`, `/api/images/cutout`, `/api/images/edit`, `/api/images/card` (same arguments).
The ML cut-out runs only on the render worker (InstaBox when `HQ_RENDER_BACKEND=instabox`, with GitHub Actions
`render.yml` as fallback), never on the web instance. rembg's own default model (BRIA RMBG) is never used.

## Music, Undo, Update draft and brand kits (3 Nov 2026)

- **No music by default.** Renders never add a score: `music: auto` and `none` are both silence for a silent video, and a video's own sound is kept. A score (`cinematic` / `pulse`) is only baked in when it is asked for.
- **Music on a post** is `list_music` → `set_post_music`. Instagram Reels take Instagram music through Buffer (`metadata.instagram.audio`). Buffer's API has no music field for carousels or images (the documented `InstagramPostMetadataInput` has none; checked against developers.buffer.com/reference on 3 Nov 2026), so for a carousel the answer is to add the song in the Instagram app.
- **Template switch on a Reel**: `set_template` (and `post_set_template`) answer `needs_choice` ("Turn the Reel into a carousel? The video becomes slide 2", Keep Reel / Switch); call again with `reel: "keep"` or `reel: "switch"`. Switch keeps the video as slide 2 (Instagram, Threads, Facebook). `undo_package_media` brings the Reel back.
- **Changed since saved to Buffer**: after a re-render or a template change, a package that was in Buffer is `ready` again with `changed_since_sent`; `update_package_draft` updates the draft it made (Save draft does the same instead of making a second draft).
- **Brand kits**: `list_brand_kits` lists the account's available kits only (`include_samples: true` for the sample brands too). To change a run's kit, `use_brand_kit` with the kit the person named; with none named, it returns the choices to ask about.
- **All templates**: `get_post_templates {all: true, previews: true}` lists every built-in and custom template with its first slide drawn from the run in its kit.

## Reel music

Instagram's licensed music and original sounds, through Buffer's audio catalog, on a Reel package (`set_reel_audio`) or a One take clip (`adjust_clip music`):

1. `search_instagram_audio {"channel_id": …, "query": "lofi"}` (blank query = trending). An Instagram channel connected with Instagram Login (not through a Facebook Page) answers `needs_facebook: true` with Buffer's help link: music needs the Page connection.
2. Pass the whole track object as `audio` / `music`, with `music_volume` and `original_volume` (0-100, the track and the video's own sound).
3. Buffer has no start-time field, so Instagram picks which part of the track plays; the UI picker has no start field either.
4. The track goes to Buffer as `metadata.instagram.audio` when the package or clip is saved as a draft. `null` removes it. Nothing is posted by these calls.

## Prompts and resources

`prompts/list` and `prompts/get` give the recipes as ready prompts (the client's model follows them; no HuhQueue AI):
`launch_video_in_my_brand(link, channel?, seconds?)`, `product_hunt_to_reel_draft(product_hunt_url)`,
`carousel_from_link(link, slides?)`, `one_take_clips(video_url, clips?)`, `new_template(purpose)`.

`resources/list` and `resources/read` give read-only context: `huhqueue://brand-kit/active` (JSON),
`huhqueue://brand-kit/active/design.md`, `huhqueue://themes`, `huhqueue://templates` (built-in + custom),
`huhqueue://docs/MCP_TOOLS.md` (this file) and `huhqueue://docs/DESIGN.md`.

## Covers

`edit_video(cover={title, image_url?, index?, opening_frame?})` renders the 3 branded cover variants on the worker; the
chosen one becomes the Reel's opening frames (so Instagram and TikTok show it as the thumbnail). `render_status` returns
`covers[].urls` and `cover_url`. `create_package(cover={url, frame_s})` puts the cover on the package (Planner, previews)
and sends `frame_s` as Buffer's `thumbnailOffset` for Instagram, TikTok and Pinterest Reels.

## Several sessions at once

Brand kit writes (activate, update, save, the HuhByte seed, the kit cap) run under a per-workspace lock, so parallel
sessions never lose a write or make a duplicate kit. The active kit is per workspace: to render in another kit without
switching it under another session, pass `kit_id` to `render_template`.

## Recipes (client brain, $0 HuhQueue AI)

Each recipe is what the client's model does, call by call. The model writes every word; HuhQueue fetches, renders and
stages. Ask the user before the last step that touches Buffer.

### 1. An editorial carousel from a launch page (the ernd example)

ernd (YC F26) is "the credit card for the AI stack". The model:

1. `research_link {"url": "https://www.ycombinator.com/companies/ernd"}` -> title, description, paragraphs, figures with
   their source sentence, image and video candidates. Follow up with `research_link` on the company site (`https://ernd.io/`)
   if the YC page is thin.
2. `find_media {"query": "ernd YC F26 launch"}` -> the official launch
   video or images, each with its credit line.
3. The model writes 5 to 7 slides from those facts only:
   ```json
   {"template": "editorial", "ratio": "4:5", "slides": [
     {"eyebrow": "YC F26 · AI + FINTECH", "headline": "The Credit Card For The AI Stack", "accent_word": "AI",
      "body": "ernd puts every model, API and GPU bill on one card.", "image": "<official image URL from find_media>"},
     {"headline": "What It Does", "body": "…", "footnote": "Source: ycombinator.com/companies/ernd"},
     {"headline": "Who Built It", "body": "Founded in 2026 by Connor Dawson, Nikhil Ruia and Malcolm Wengel."}
   ]}
   ```
   `render_carousel` with it -> `media_urls` (cover first, Follow card last) and a Library item.
4. `brand_check {"text": "<the caption the model wrote>"}` -> fix anything that fails a Style Ledger rule.
5. `create_package {"captions": {"instagram": "…", "linkedin": "…"}, "media_urls": [...], "title": "ernd", "format": "carousel"}`
   -> `run_id`.
6. After the user says yes: `schedule_package {"run_id": …, "channel_id": "<instagram channel id>", "mode": "draft"}`.

### Upload a file the AI app has locally (no public link needed)

Claude, ChatGPT and other clients often have the file itself (an attachment, an artifact, a file in their sandbox) but no
public host: their sandboxes block file hosts and their artifact links answer with a sign-in page. Upload it straight to
HuhQueue instead:

1. `create_upload {"name": "launch.mp4", "size": 26843545}` -> `upload_url` (one use, 15 minutes) and a ready `curl`.
2. Run it from the sandbox: `curl -sS --fail-with-body -X PUT --data-binary @launch.mp4 '<upload_url>'` -> the Library item
   (`library_id`, `media_token`, `url`, `kind`, `bytes`). The body is the raw file: not base64, not a form.
3. If the sandbox can't reach the URL (Claude.ai and ChatGPT block outside hosts, 2 Nov 2026), don't push a video
   through the chat as base64. Either:
   - `create_send_code {}` -> `code`, `url`, `qr`, `say`. Tell the user to open the link on any device (or type the code
     at `/send`) and drag the file in; then `list_recent_uploads {"minutes": 15}` -> the item's `media_token`; or
   - ask for a Google Drive / Dropbox / OneDrive / iCloud share link ("anyone with the link") and call
     `import_from_link {"url": "https://drive.google.com/file/d/…/view"}` -> the Library item.
4. Only for a small file (a few MB): split it into pieces of at most 2 MB, base64 each, and send them in order:
   `upload_media_chunk {"index": 0, "data": "<base64>", "name": "logo.png", "total_size": 812345}` -> `upload_id`, then
   `upload_media_chunk {"upload_id": "up_…", "index": 1, "data": "…"}` and so on, then `finish_upload {"upload_id": "up_…"}`.

Uploads, sent files and link imports count against the Library's caps (100 MB a file, 200 MB a person; `GET
/api/uploads/limits` has the live numbers). The type is read from the bytes, so a web page
saved as `.mp4` is refused with a message that says it is a web page.

### Rights: one parameter

`edit_video`, `render_reel`, `split_video` and `create_package` take `rights`, said once, with no confirmation round-trip:

| `rights` | Meaning | What HuhQueue does |
|---|---|---|
| `own` | The user made it | Nothing extra |
| `permission` | The user has the right to use it | Nothing extra (a `credit` is shown if given) |
| `news_credit` | A news or launch clip used with credit | The credit line (`credit`, or the source's site from `source_url` / `url`) is added to every caption and as a lower-third on the video |

The older `owns` / `rights_confirmed` / `credit` fields still work.

### 2. Cut a video and add captions

1. `list_library {"kind": "video"}` -> pick the clip (or `create_upload` for a local file, or `library_upload_url
   {"url": "https://…/talk.mp4"}` for a file already at a public link, up to 40 MB) -> `media_token`.
2. `edit_video {"media_token": "…", "start_s": 12, "end_s": 41, "ratio": "9:16", "captions": true, "rights": "own",
   "hook": "<a short title the model wrote from what is said>"}` -> `job_id`. For someone else's news clip use
   `"rights": "news_credit", "credit": "Video: …"`.
3. `render_status {"job_id": …}` every 10 to 20 s -> `status: done` and the download URL. Show it to the user.
4. Optional: `create_package` with that URL and a caption per channel, then `schedule_package` as a draft.

### 3. Split a long video into clips

1. `split_video {"media_token": "…", "n": 5, "owns": true}`. For a third-party video use `"rights_confirmed": true` and
   `"credit": "Video: <owner> — <title>"`. `{"sample": true}` runs on the bundled sample (Buffer's Buffer Commit kickoff Q&A,
   5 credited clips) with no worker. -> `take_id`.
2. `render_status {"take_id": …}` until done -> each clip's URL, cover, hook, start/end in the source, transcript.
   The transcript comes from Whisper on the worker and the moments from the built-in scorer: no AI on either side.
3. The model may rewrite a clip's hook from the clip's own words with `adjust_clip {"take_id": …, "clip_id": "c2",
   "hook": "…"}` (hooks must quote what was said) or trim it with `start_ms` / `end_ms`.
4. `place_clips_in_week {"take_id": …}` returns the plan (dry run) and a `confirm_token`; after the user agrees, call it
   again with the token. Clips land as Buffer drafts in open slots, never published.

### 4. A weekly recap

1. `weekly_recap_plan {"n": 5, "days": 7}` -> the week's top AI stories from the user's sources, each with its official
   video or image and its credit line.
2. The model writes one slide per story (headline, one-line body, the credit as footnote) plus a cover ("This week in AI").
3. `render_carousel {"template": "text_slides", "slides": [...]}` (or `render_template` with `weekly_recap`, the recap look) for a carousel, or `render_reel {"images": [...media_urls],
   "hook": "This week in AI"}` for a Reel (`render_status` for the video).
4. `create_package` with the captions the model wrote -> `schedule_package` as a draft after the user says yes.
   For a recap every Monday, `schedule_template_slot {"template": "weekly_recap", "weekday": "mon", "time": "20:00",
   "tz": "Europe/Dublin"}` puts a draft in the Planner each week.

## Checking the $0

`get_activity {"tab": "agent"}` lists every MCP call with the app and the model the client reported. The budget ledger
row `mcp_client_brain` counts the calls at $0; the user's allowance (`get_brain` -> budget) does not move on a client-brain
run. `tests/studio/test_mcp_client_brain.py` asserts an MCP run makes zero Anthropic calls.

## Style Ledger v2 (studio/ledger.py)

| Tool | What it does |
|---|---|
| `list_style_rules` | Every rule by group (voice, words, hashtags, emojis, cta, visual) with origin (taught / learned from N edits / agent / brand kit), scope, on/off, examples, plus open suggestions. `group`, `channel` filter. |
| `add_style_rule` | A rule in plain words, optional `scope` (networks). Only when the person asked. |
| `update_style_rule` / `delete_style_rule` | Two steps: a preview with `confirm_token`, then the same call with it after the person says yes. On/off stays in the app (people only). |
| `list_rule_suggestions` / `decide_rule_suggestion` | Rules suggested from 3 similar caption edits; accept (optionally scoped) or dismiss, with a confirm step. Never applied unaccepted. |
| `check_draft` | Rules that apply on one network, failures, the auto-fixed text, and with `run_id` + `channel_id` the source line behind every fact. |
| `list_style_presets` | Built-in Style Ledger presets (Hook-first short-form, Educator / explainer, Founder build-in-public, News fast and accurate, Podcast clip): hook formulas, first-line rules, length and hashtag cap per network, on-screen text, CTA, banned phrases, and the rules applying one adds (with indexes for edits). Read-only. Sources: docs/STYLE_PRESETS.md. |
| `apply_style_preset` | Add a preset's rules to the person's ledger when they chose it: two steps (preview + `confirm_token`). `edits` drops or rewords rules first. Re-applying replaces that preset's earlier rules; another preset's are switched off. Rules record the calling app (provenance). |
| `create_review_link` / `list_review_links` | A read-only, expiring link (default 72 h, max 14 days) where a reviewer approves or requests changes; the answer lands in the run's thread. |

## What the MCP server keeps about calls

Each tool call is logged so the person can see what their AI apps asked for (`studio/mcp_runs.py`, table `mcp_calls`): the tool
name, a summary of whitelisted arguments, status, duration, client, app, token name, model hint, time and the post run it belongs
to. Calls not attached to a run are swept after 24 hours; calls attached to a run stay with it until the person deletes their
data (Disconnect and delete removes every row). Media the tools hand out is served at `/m/<token>` links: a random 144-bit token,
not signed and with no expiry, publicly cacheable for 1 day, so treat a `/m/` link like the file itself. The 15-minute
`/ms/<exp>/<sig>/<token>` link from `get_media_preview` is the only signed media link.
