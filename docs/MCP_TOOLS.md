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
   yourself. With `server_ai: true` they run HuhQueue's agent and spend the user's allowance (the kill switch and daily caps
   still answer first).
3. Tools marked **client brain \*** below can write text (a draft caption, a recommendation). Over MCP they use the user's
   own model key or the free starter model if one is set, else the rule-based writer. Never HuhQueue's Claude.
4. Every client-brain call is written to the budget ledger as `source=mcp_client_brain` at **$0**, so Activity shows it.
5. Nothing reaches Buffer without the user: `submit_post` / `schedule_package` go through preview, the token's policy
   (review tokens wait in Approvals) and the daily cap. `mode=draft` is the default in every recipe below.

Heavy jobs (`edit_video`, `render_reel`, `split_video`, `render_template` video) return an id at once; poll
`render_status` every 10 to 20 seconds until `status` is `done` and read the download URL.

## Every tool


### Client brain: the hands (no HuhQueue AI, $0)

| Tool | Brain | What it does |
|---|---|---|
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
| `library_upload_url` | client brain | Add a photo or video the user owns to their Library from a public https URL (up to 40 MB). |
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
| `schedule_package` | no model | Send one ready package from a run to Buffer (mode draft / queue / next / now / schedule with due_at ISO UTC). |
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
| `clips_from_video` | no model | One take -> a week. |
| `list_clips` | no model | Without take_id: the user's takes. |
| `adjust_clip` | no model | Edit one clip of a take: trim (start_ms/end_ms in the source video; re-renders on the worker), a new hook for the cover (must be at least 3 words quoted from the clip), or the post caption (caption, optionally for one service). |
| `place_clips_in_week` | no model | The Spread: put a take's ready clips into the open Buffer posting slots of a week as DRAFTS (never published). |

### Library

| Tool | Brain | What it does |
|---|---|---|
| `list_library` | no model | The person's Library: every uploaded photo/video and every image, carousel and Reel the agent made, with type, size, date and which posts used each one. |
| `use_library_item` | no model | Use a Library item in a new post. |
| `delete_library_item` | no model | Delete one Library item (the file and its record). |

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

### Onboarding and the in-app brain

| Tool | Brain | What it does |
|---|---|---|
| `get_onboarding` | client brain * | The person's content types, channels, website, tone and X watchlist, with the options to choose from. |
| `set_onboarding` | client brain * | Save onboarding choices: content_types (ai_news, product_launches, yc_startups, tutorials, dev_tools, memes, india_tech, research, funding, open_source), channel_ids, tone, website (builds the brand kit), x_watchlist (handles or… |
| `get_recommendations` | client brain * | A feed tailored to the person's content types plus up to three ready-to-approve posts in their brand (cover + captions). |
| `get_ai_engine` | client brain * | The in-app agent's AI engine (claude, gemini-flash, gemini-pro, own, free or auto), which one will actually run, each engine's credit in plain words ("Claude: $0.34 of your $1 used", "Gemini (Oracle): free during the contest, $0.00 of your $3 used") and the app-wide pools. |
| `set_ai_engine` | client brain * | Choose the in-app agent's engine. Out of credit or erroring, it falls back to the next one automatically (a note in the run). |
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

92 tools in total (from `tools/list`, 92 as of this branch).

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

### 2. Cut a video and add captions

1. `list_library {"kind": "video"}` -> pick the clip (or `library_upload_url {"url": "https://…/talk.mp4"}` for a file the
   user owns, up to 40 MB) -> `media_token`.
2. `edit_video {"media_token": "…", "start_s": 12, "end_s": 41, "ratio": "9:16", "captions": true,
   "hook": "<a short title the model wrote from what is said>"}` -> `job_id`. For someone else's video add
   `"credit": "Video: …"` and `"rights_confirmed": true`.
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
