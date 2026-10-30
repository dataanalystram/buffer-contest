# HuhQueue — built on the Buffer API

**Paste a link. Get a post for every channel. Approve it, and it's in your Buffer queue.**
An open-source, agent-first content studio. Your AI agents (Claude, ChatGPT, Cursor) can draft and schedule through it too —
but nothing they write goes out until you approve it.

**Live:** https://huhqueue-studio.onrender.com · **Buffer Commit entry:** see [CONTEST.md](CONTEST.md) and [SUBMISSION.md](SUBMISSION.md)

> HuhQueue is an independent open-source project built on the [Buffer API](https://developers.buffer.com). It is not made, endorsed or operated by Buffer.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/screenshots/planner-week-dark.png">
  <img src="docs/screenshots/planner-week-light.png" alt="HuhQueue Planner, week view: posts from five Buffer channels on an hourly grid, dashed Buffer posting times, and a No date sidebar with drafts and an agent post waiting for approval">
</picture>

*The Planner, week view: your Buffer queue and calendar in one place. Drag any post or undated draft to a new time; sent posts stay locked.*

## Screenshots

All captured from the running app (sample workspace at `/demo`, nothing reaches Buffer) with
`scripts/planner_shots.py` and `scripts/readme_shots.py`. Re-run them whenever the interface changes.

| | |
|---|---|
| ![Planner week view in light mode, hourly grid with posts and Buffer posting times](docs/screenshots/planner-week-light.png) | ![Planner week view in dark mode](docs/screenshots/planner-week-dark.png) |
| **Planner, Week** (light). Click an empty slot to schedule there; dashed chips are Buffer's posting times. | **Planner, Week** (dark). Same view, black-and-white dark theme. |
| ![Planner list view: the Buffer queue grouped by day with thumbnails, channel, time, caption and status, plus edit and reschedule actions](docs/screenshots/planner-list-light.png) | ![Planner month view: a month grid with posts per day and a No date drafts sidebar](docs/screenshots/planner-month-dark.png) |
| **Planner, List**: the Buffer queue by day, with edit, reschedule, approve and delete-draft actions. | **Planner, Month** (dark): "+N more" opens the whole day. |
| ![Make a post: the agent edited the user's own photo, with its finished step timeline and the branded image](docs/screenshots/agent-run.png) | ![Ready for Buffer: one package per channel with caption, media, character count and a passing brand check](docs/screenshots/agent-packages.png) |
| **Make a post**: the agent reads your photo (or researches a topic or link), renders it in your brand and shows every step. | **Ready for Buffer**: one caption per channel, counted Buffer's way and checked against your rules. |
| ![Brand kit editor with colours, font and end card, a live preview, and the Style Ledger listing four learned rules](docs/screenshots/brand-ledger.png) | ![Approvals: a post written by the Claude agent waiting for a human, with Reject and Approve and send to Buffer](docs/screenshots/approvals.png) |
| **Brand kit and Style Ledger**: say "logo smaller" once and it becomes a rule every post follows. | **Approvals**: agent posts wait here; nothing reaches Buffer until you approve. |

## Why

Creators who share news (we run a daily AI-news page) do the same chore many times a day: read the story, write a LinkedIn version,
a shorter X version, an Instagram caption with "link in bio", check each network's limit, then queue them. Agents can write those
posts in seconds, but nobody wants an agent posting to their brand unreviewed. HuhQueue is the review desk between the two, on top of
the Buffer account you already use.

## What it does

- **Planner** — one tab for the Buffer queue and the calendar: List, Week and Month views; filter by channel, post type, status and caption text; any timezone (defaults to your browser's); a **No date** sidebar for undated drafts; Buffer's **posting times** on the grid; click an empty slot to schedule there; drag a post or draft to reschedule (Buffer `editPost`, the same `update_post` action REST and MCP use; sent posts are locked). View and filters live in the URL and are remembered.
- **Connect Buffer** — OAuth 2.0 Authorization Code + PKCE with a private client. Access and rotating refresh tokens stay encrypted on the server; the browser only holds an opaque session cookie.
- **Link → drafts** — reads the article and writes one caption per channel, counted the way Buffer counts (UTF-16, LinkedIn URLs = 24, Instagram line breaks = 2, X URLs = 23, Bluesky graphemes) and trimmed to fit.
- **Send to Buffer** — as a Buffer draft, add to queue, share next, share now or a custom time; image/video by URL; ideas to your Ideas board. Queue and drafts read back from Buffer.
- **Agents with a leash** — MCP server + REST API per Buffer user. Every write is *preview → single-use confirm token → submit*. Default policy holds agent posts in an **Approvals inbox**; `draft` tokens can only create Buffer drafts; `trusted` tokens can schedule. Daily caps, a one-click **kill switch**, and an **activity log** of every action by you or your agents.
- **Plays nice with Buffer's limits** — reads `RateLimit` headers (shown live in the header), caches channel lists, backs off on `429` + `Retry-After`.

## What we built during the Commit window (28 Oct to 13 Nov)

Everything below was built in the Buffer Commit window; the pre-existing self-host scheduler is listed separately in
[CONTEST.md](CONTEST.md). This section is updated in the same commit as each feature.

| Date (2026) | What |
|---|---|
| 28 Oct | Buffer OAuth (PKCE, rotating refresh tokens), GraphQL client with rate-limit handling, link to one caption per channel counted Buffer's way, preview, confirm and send (draft, queue, next, now, custom time), ideas |
| 28 Oct | Agents with a leash: MCP server and REST API per Buffer user, approval inbox by default, drafts-only and trusted policies, daily caps, kill switch, activity log; Playwright e2e and CI on SQLite and Postgres; Render deploy |
| 29 Oct | Threads from one link, sample workspace at `/demo`, post previews, news radar, plan-limit guard, bookmarklet, the "Needs you" home |
| 30 Oct | Supabase Postgres for the live app, OAuth connector for Claude.ai and ChatGPT, public usage counter |
| 30 Oct | The agent ("Make a post"): research a topic or link or edit your own photo or video, on-brand carousels, cards and Reels, brand kit with website import, the Style Ledger and brand check, AI engine settings (bring your own key or use your own Claude/ChatGPT) |
| 30 Oct | Renamed to HuhQueue. **Planner**: one tab replaces Calendar and Buffer queue (List, Week, Month, filters, timezone, No date drafts, posting times, click-to-schedule, drag to reschedule) on new `update_post`, `reschedule_post` and `delete_draft` actions (Buffer `editPost` / `deletePost`) shared by UI, REST and MCP; light and dark screenshots |

## Connect an agent (MCP)

**Claude.ai or ChatGPT:** add a custom connector with the URL `https://huhqueue-studio.onrender.com/mcp`. It signs you in with
Buffer and asks what the assistant may do (ask first, drafts only, or trusted) and how many posts a day. No token to paste.
The connection is an ordinary agent token underneath, so it shows on the Agents screen and the kill switch stops it.

**Claude Code / other clients:** create a token in **Agents**, then:

```bash
claude mcp add --transport http huhqueue https://huhqueue-studio.onrender.com/mcp \
  --header "Authorization: Bearer hq_YOUR_TOKEN"
```

Tools: `whoami`, `list_channels`, `draft_from_link`, `preview_post`, `submit_post`, `list_approvals`, `list_queue`, `update_post`, `reschedule_post`, `delete_draft`, `list_calendar`, `list_stories`, `create_idea`, `get_limits`, plus the agent tools `run_agent`, `edit_media`, `get_run`, `revise`, `remember_rule`, `brand_check`, `get_brand`, `schedule_package`.
The REST API (`/api/docs`) accepts the same bearer token, so UI = API = MCP (a test asserts all three send Buffer the identical `createPost` input).

## Run it yourself

```bash
pip install -r requirements-studio.txt
cp .env.example .env   # BUFFER_CLIENT_ID / BUFFER_CLIENT_SECRET from https://publish.buffer.com/settings/api, redirect https://localhost:8000/callback
export $(grep -v '^#' .env | xargs) && uvicorn studio.app:create_app --factory --port 8000 --ssl-keyfile key.pem --ssl-certfile cert.pem
```

Production: `render.yaml` (free web service) with a free Supabase Postgres, or `Dockerfile.studio` anywhere. Set `DATABASE_URL` to Postgres so rotating refresh tokens survive restarts. Moving databases: see [docs/SUPABASE.md](docs/SUPABASE.md).
Optional `AI_BASE_URL`/`AI_MODEL`/`AI_API_KEY` point the drafter at any OpenAI-compatible model; the hosted demo uses the free Pollinations endpoint and falls back to the built-in writer.

## Tests

```bash
pip install -r requirements.txt -r requirements-dev.txt -r requirements-studio.txt
python -m pytest tests/studio -q            # API, OAuth, rate limits, safety, parity, Planner + Playwright e2e
python scripts/readme_shots.py http://127.0.0.1:8899 docs/screenshots   # README screenshots (app running with /demo)
python scripts/planner_shots.py http://127.0.0.1:8899 docs/screenshots
STUDIO_TEST_PG=postgresql://... python -m pytest tests/studio -q   # same suite on Postgres
scripts/secret_scan.sh                      # gitleaks on full history + working tree (run before every push)
```

## Self-hosted mode

The pre-existing HuhQueue scheduler with native connectors (no Buffer needed) is in `huhqueue/` — see [docs/SELF_HOST.md](docs/SELF_HOST.md).
It is not part of the contest edition.

MIT licensed. Built fast with AI coding agents; any agent can read, edit and run this repo.
