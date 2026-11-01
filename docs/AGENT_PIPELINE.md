# The agent pipeline: diagnosis and architecture

How **Make a post** turns one prompt into per-channel posts for a person's Buffer channels, why it was rebuilt, and where
each piece lives. Pinned by `tests/studio/test_agent_core.py` (the owner's exact prompt on recorded fixtures),
`tests/studio/test_hosted_render.py` and `tests/studio/test_onboarding.py`.

## 1. Diagnosis: what went wrong on the owner's real prompt

The owner typed, on production, signed in with his own Buffer (LinkedIn, Facebook, YouTube, Instagram):

> i want to post about this in all my channels https://x.com/AarushYadav47/status/2116198463847408066 make it a short punchy explainer

The old pipeline (`house.start_run`, one fixed news flow behind a one-line "Topic" field) failed it in seven ways:

| # | Symptom | Cause | Fix (file) |
|---|---|---|---|
| 1 | The prompt was cut to 120 characters | The Topic field was an `<input maxlength="120">` and the headline was sliced `[:120]` | One textarea, 4,000 characters with a counter; `intake.parse` reads all of it: URLs, channels, format, length, tone, standing rules, topic (`studio/intake.py`, `static/index.html`) |
| 2 | The pasted tweet was ignored and a news search ran instead | The flow always called `sources.search(query)`; a URL inside the text was just words | A pasted link is THE source: `read_url` reads it; X links through X's public oEmbed (no scraping, no login); articles directly with a Firecrawl fallback. `search_news` only runs when there is no link (`studio/reader.py`, `agent_tools.py`) |
| 3 | A canned sample Reel appeared as if it were the post | Without a worker, the run fell back to the bundled sample clip | Never substitute a clip: Reels render locally (self-host) or on the GitHub render worker (hosted); with neither, the channel gets the slides and a note says why. Brand studio template Reels follow the same rule; the sample's pre-rendered Reel is labelled `example: true`, "EXAMPLE REEL, not your content" (`agent_tools._reel`, `brand_studio.render`) |
| 4 | Text clipped off covers, end cards and Reel frames | Fixed font sizes on fixed boxes | `hbstyle.fit()` shrinks each block until it fits width and height, hard-wraps over-long words, records every box so tests and `check` prove nothing leaves the canvas (`studio/hbstyle.py`, ported from the owner's `hb.py`, see NOTICE) |
| 5 | The run hung after the video step | The run had no bounded wait on its render job and no stale-run check | The brain waits on its own render job with a bounded wait (`HQ_VIDEO_WAIT`), keeps `updated` fresh, and a stale run (`HQ_RUN_STALE`) is reported as failed, never left spinning |
| 6 | Saving twice made duplicate Buffer drafts | No idempotency on create | `save_buffer_drafts` is idempotent per run and channel; drafts only, never publish/schedule/queue |
| 7 | A money figure was picked between two sources without asking | No cross-source check | `factcheck` compares money figures with other outlets on the same story; a conflict ("$1.3M" vs "$13 million") **holds** the post, asks the person, and drafts are refused until they answer ("it's $1.3M") |

## 2. Architecture

```
prompt (<= 4,000 chars) ─► intake.parse ─► Brain.start (agent_runs row, thread) ─► brain loop ─► check ─► packages
                                                              ▲                                     │
                                         reply in the thread ─┘            save_buffer_drafts ◄────┘ (only when asked)
```

### Intake (`studio/intake.py`)
Pure functions, no network. Every URL; channels ("all my channels" = every connected channel, named networks = those);
format (reel / carousel / single image); length ("30s"); tone (punchy, formal, explainer, casual); standing rules said
in passing ("end every video with Follow @x" becomes a Style Ledger rule; words inside a rule never set the format); the
topic left over. The prompt's channels win over the channel chips.

### The brain (`studio/agent_brain.py`, `studio/brain_llm.py`, `studio/budget.py`)
Engines in order (`brain_llm.engines`):
1. The operator's Claude key: Sonnet plans and drives the tools (tool use, prompt caching), Haiku writes facts and
   captions as JSON. Organisation keys send `anthropic-workspace-id` (`ANTHROPIC_WORKSPACE_ID`).
2. The person's own key (tool use where the provider supports it).
3. The free starter model: a fixed plan; the model only writes facts and captions.
4. No AI: the same fixed plan with the rule writers.

Budgets are hard and persisted: `AI_BUDGET_TOTAL_USD` (lifetime, all accounts), `AI_BUDGET_PER_USER_USD` (per signed-in
user), a testers' pool; every call is priced from real token usage and checked against the worst case before it runs.
Over a cap, the run falls back to the next engine with a note. The sample workspace never uses the operator key.

Whatever the engine, the steps are the same tools in a deterministic order: rules first, then the source, facts, renders
per channel, captions, `check`, and drafts only when asked. Each step is stored and shown live; the plan is shown
first. A reply in the run's thread ("make the hook stronger", "redo YouTube at 30s", "LinkedIn more formal", "it's
$1.3M") continues the same run and touches only what it names.

### Tools (`studio/agent_tools.py`): one implementation for the brain, REST and MCP
`read_url`, `x_oembed`, `search_news`, `extract_facts`, `render_cover`, `render_carousel`, `render_reel`,
`add_end_card`, `edit_brand`, `edit_channel_media`, `write_captions`, `remember_rule`, `find_media`, `generate_images`,
`check`, `save_buffer_drafts`, `finish`. REST: `POST /api/agent/runs`, `/api/agent/runs/{id}/messages`,
`/api/agent/runs/{id}/tools/{name}`. MCP: `make_post` (with `brain=external` a person's own ChatGPT/Claude drives the
`post_*` tools step by step), `reply_post`, `edit_brand`, `get_brain` (`studio/agent_routes.py`).

### Media pipeline (`studio.media`, docs/MEDIA_PIPELINE.md)
`find_media` runs `studio.media.tools.media_find` (the real launch video for the person's link: X, YouTube, YC, HN,
GitHub, the company site). `render_reel` with `style` (`opus` | `news` | `minimal` | `hype`) queues a `media_render`
job: the GitHub render worker (or local FFmpeg off the hosted app) fetches the clip, transcribes it, and edits it with
`studio.media.worker.render_job` (captions, credit, music, end card). Someone else's video is not rendered until the
person confirms the rights: the tool returns `needs_confirmation` and the question, and the next call passes
`rights_confirmed: true`. `generate_images` covers stories with no media. The fixed (no-AI) planner uses a style only
when the person names one ("opus clip", "hype style", "minimal look", "news layout").

### Formats per channel
Video-only channels (YouTube, TikTok) get a 9:16 Reel; Instagram, LinkedIn and Facebook a 4:5 carousel unless the person
asked for a Reel or the source has a video; X / Threads / Bluesky a cover. The headline is on every Reel frame; the end
card is last and follows the Style Ledger.

### Rendering and where video runs
Images (covers, carousels, post cards, fact slides) render in-process with Pillow through `hbstyle`. Video never
renders in-process on the hosted app: Render's free instance has 512 MB and an FFmpeg Reel peaked at 1.5 GB.
`House.video_mode()` decides:

| Where | Mode | Video |
|---|---|---|
| Self-host with FFmpeg | `local` | in this process |
| Hosted (`RENDER` set by Render, or `HQ_NO_LOCAL_VIDEO=1`) with `GH_DISPATCH_TOKEN` | `github` | `.github/workflows/render.yml` per job |
| Neither | `none` | slides instead, with a note |

The worker claims its job once with its GitHub OIDC identity (no token in the dispatch inputs), reads the spec once,
posts the MP4 back, and the job token dies with the result (`studio/worker_auth.py`, `studio/render_worker.py`). Job
kinds: `slideshow` and `brand_video` (Make a post Reels), `template_video` (Brand studio template Reels, in the person's
brand, at most `HQ_TEMPLATE_VIDEO_DAILY` (default 6) a day per account; `GET /api/templates/video/{id}` / MCP
`get_template_video` while it renders), `take_*` (One take). One take,
Make a post and the Brand studio all use the same `video_mode()`.

### Check and fact hold (`studio/factcheck.py`)
`check` runs last for every channel: brand rules, clipped text (from the recorded boxes), "about the source", open fact
conflicts. It builds one package per channel (media URLs, caption). A conflict sets a hold: the result view shows the
question with both figures, drafts are refused, and the person's answer resolves it and re-runs captions.

### Buffer
Reads (organisations, channels) go through a per-user cache (`CHANNEL_TTL`) so polling the UI costs no extra Buffer
requests. Writes are Buffer **drafts** only (`saveToDraft`), idempotent per run and channel.

### Onboarding and recommendations (`studio/onboarding.py`)
Content types, channels, tone, website (brand kit from the context.dev Brand API, the site reader as fallback) and an X
watchlist, then a feed filtered to those choices with up to three ready-to-approve posts in the brand that hand off to
Make a post. Cached for an hour per account.

## 3. Providers and keys (server env)

| Provider | Env | Used for |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` | the brain (Sonnet + Haiku) |
| TypeSafe / SubQ System One | `TYPESAFE_API_KEY`, `SUBQ_API_KEY` | vision: is this image on-topic and good enough |
| Firecrawl | `FIRECRAWL_API_KEY` | reading pages that block bots |
| context.dev | `CONTEXT_DEV_API_KEY` | brand kit from a website |
| GitHub | `GH_DISPATCH_TOKEN`, `GH_REPO` | starting the render worker |

`python scripts/provider_check.py` makes one tiny call to each and prints only provider, HTTP status and ok.
