# HuhQueue

**A free, self-hosted social media scheduler (Buffer-style) for Instagram, YouTube, X and TikTok, built AI-native.**
Write once, schedule everywhere, answer comments, and read your insights from one small Python app with a React UI.
Everything the UI does is also available to agents through a REST API (OpenAPI), an MCP server, a CLI and `/llms.txt`.

No paid tiers, no feature gates, no limits beyond the networks' own. MIT licensed.

## Status (27 Oct 2026)

**Done (on `main`)**
- Core queue, composer, calendar, library, ideas, approvals, notifications, local AI assistant/actions
- Connectors: Instagram (Instagram Login), X, TikTok, YouTube
- Reports & Insights (audience, posts, Reels, best times, hashtags, report builder + PDF), Growth, Teams (roles, invites, approvals)
- Channel pages: Publish (list/calendar, Instagram planned grid), Community (comments + mentions, bulk resolve/reply, saved and AI replies), Insights (ranges, previous period, CSV, share), Posting schedule, per-channel Settings
- Live-test fixes: in-app confirm dialogs, Instagram comment notes for Development mode, Composer carousel and alt text. Notes in [docs/LIVE_TEST_2026-10-26.md](docs/LIVE_TEST_2026-10-26.md)
- LinkedIn connector: personal profiles + company pages (pages behind `LINKEDIN_PAGES_ENABLED`), text / image / multi-image / PDF document / video / link preview / first comment, 7-day token-expiry warning. Setup: [docs/CONNECT_INTEGRATIONS.md](docs/CONNECT_INTEGRATIONS.md)
- Buffer CSV parity: Buffer's own CSV columns (Posting Time wins over Queue), LinkedIn columns, PDF media, over-limit rows flagged (not dropped), per-channel Bulk upload to Queue or Drafts
- Headless / agent layer: `GET /api/context?page=` (also embedded in every page as `<script id="agent-context">`), `/openapi.json`, `/llms.txt`, `/agents`, a stable `data-testid` on every control, and CI parity tests (every UI call maps to an API action and an MCP tool)
- Tests: 302 unit + 71 Playwright e2e passing (`python3 -m pytest`; e2e needs Chromium)
- Live: first real post through HuhQueue (Instagram Reel, human approval on), 27 Oct 2026: https://www.instagram.com/reel/DfACLYnMj22/

**Next**
- Branch `feat/bio-bulk`: bio page v2 is tested; the bulk/settings backend is partly tested and still needs `tests/test_bulk.py`, the Queue/Settings UI and a rebase onto `main`
- Plan item 2, agent safety layer (kill switch, per-token daily caps/expiry/channel scope, MCP preview + confirm, audit filters/export): backend + tests on branch `feat/agent-safety`, UI not built, not merged
- Plan item 3, reliable publishing (retryable vs permanent errors, attempt log, jittered backoff, per-channel rate memory, hourly requeue of missed posts, worker heartbeat in `/api/health`): not started
- Live LinkedIn validation: needs Ram's own LinkedIn developer app (and Community Management API approval for pages), then a real connect and test post. Steps: [docs/CONNECT_INTEGRATIONS.md](docs/CONNECT_INTEGRATIONS.md)
- YouTube: the connected channel is Ram's personal channel, not @huhbyte; reconnect and pick the huh byte channel ([docs/CONNECT_INTEGRATIONS.md § 4](docs/CONNECT_INTEGRATIONS.md#4-youtube))
- Instagram via Facebook Login (collaborators, location, product tags) and the Reels music picker: [docs/FBLOGIN_PLAN.md](docs/FBLOGIN_PLAN.md)
- Meta app review, so Instagram comments come through outside Development mode
- Hosting: the InstaBox dev box sleeps after 30 minutes idle, so scheduled posts can be missed; move to an always-on host

**Docs**
- [docs/MILESTONES.md](docs/MILESTONES.md): **next milestones M1–M6 with local setup, tasks, files and done-criteria (start here)**
- [docs/CONNECT_INTEGRATIONS.md](docs/CONNECT_INTEGRATIONS.md): connect every network, numbered steps
- [docs/FBLOGIN_PLAN.md](docs/FBLOGIN_PLAN.md): Instagram via Facebook Login plan
- [docs/LIVE_TEST_2026-10-26.md](docs/LIVE_TEST_2026-10-26.md): live-test notes
- [NOTICE](NOTICE): third-party code credits and licences

## Quick start

```bash
git clone https://github.com/dataanalystram/bufferopensource && cd bufferopensource
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # requirements-dev.txt for tests
python -m huhqueue serve                  # UI + API on http://localhost:8787
```

Or with Docker: `docker compose up -d` (always-on hosting notes in [SETUP.md](SETUP.md#docker-any-always-on-host)).

Open http://localhost:8787, set a password, then **Settings → App keys** to paste your own Meta / Google / X / TikTok
developer-app keys and click **Connect** on a channel. Step-by-step network setup: [docs/CONNECT_INTEGRATIONS.md](docs/CONNECT_INTEGRATIONS.md) (every network, numbered steps) and [SETUP.md](SETUP.md) (hosting, compliance callbacks).

- Demo data: `HUHQ_ENABLE_FAKE=1 python scripts/demo_seed.py` (a fake "Demo network" connector, no keys needed).
- UI development: `cd web && npm install && npm run build` (the built bundle is committed in `huhqueue/static/app`, so
  running the app never needs Node).
- Tests: `pytest tests --ignore=tests/e2e` (backend) and `CHROMIUM_PATH=/usr/bin/chromium pytest tests/e2e/test_ui.py`
  (browser tests; run each e2e file on its own).
- Agents: `huhqueue mcp` (stdio MCP server, env `HUHQ_URL` + `HUHQ_API_TOKEN`) or `huhqueue run <action>`; the guide is
  served at `/agents` and `/llms.txt`.

## Features

**Publishing**
- Composer with preview-first side panel, multi-channel chips, per-channel captions, Instagram Post / Reel / Story /
  carousel, collaborators, first comment, AI-disclosure, templates, hashtag groups, alt text, format suggestions.
- Queue with posting slots, prioritise, drag-to-reorder, per-channel daily limits with a major-news override,
  "same story never twice on one channel" guard, retries, approvals for agent tokens.
- Calendar (list / week / month), undated-drafts tray, timezone-aware drag, shared filter bar (channels, type, status,
  tags, campaign, search, timezone) with saved views.
- Video end card ("Follow @handle") added by ffmpeg, PNG/WebP→JPEG for Instagram, 50/24h IG publish cap respected.

**Engagement and insights**
- Community inbox: Unanswered / Answered / Flagged / Assigned, commenter profiles with history across posts, saved
  replies, assign, flag, done.
- Insights: KPIs, posts by day, top posts, CSV export of every post in the range; per-channel account insights and
  history grid.

**Agents and automation**
- One action registry that generates the REST actions endpoint, MCP tools, ⌘K entries, the CLI and `/llms.txt`.
- Bring your own AI model (Settings › AI model): NVIDIA NIM, Google Gemini, Groq, OpenRouter, Ollama, OpenAI, Anthropic
  or any OpenAI-compatible URL (LM Studio, vLLM). Key stored encrypted, test connection, model picker, thinking models
  supported. It powers the composer AI writer, post ideas, alt text, comment themes, reply suggestions and the command bar.
  Headless setup with `HUHQ_AI_*` in `.env` or `huhqueue ai set`; agents get `ai_status` / `ai_write` but never the key.
- Natural-language command bar ("schedule the launch reel for Friday 7pm on Instagram") with a confirm step
  for public or destructive actions, and a deterministic fallback parser when no AI model is set.
- Scoped API tokens (scopes, tool allow-list, rate limit, require-approval), audit log of every token and UI action.

See [docs/ROADMAP.md](docs/ROADMAP.md) for what comes next.

## Docs

- [docs/CONNECT_INTEGRATIONS.md](docs/CONNECT_INTEGRATIONS.md): connect LinkedIn (profile + pages), Instagram, YouTube, X, TikTok and import a Buffer CSV, step by step.
- [SETUP.md](SETUP.md): developer apps, redirect URIs, Meta compliance callbacks, hosting.
- [docs/CONNECTORS_RESEARCH.md](docs/CONNECTORS_RESEARCH.md): every network's free tier, scopes and limits.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones with acceptance tests.
- [docs/CMS_PLAN.md](docs/CMS_PLAN.md) and [docs/AI_NATIVE_PLAN.md](docs/AI_NATIVE_PLAN.md): designs behind the roadmap.

## Open-source credits

HuhQueue's UI is built on these projects. Thank you to their authors.

| Project | Used for | Licence |
|---|---|---|
| [React](https://react.dev) | UI library | MIT |
| [Vite](https://vite.dev) | Build tool | MIT |
| [Tailwind CSS](https://tailwindcss.com) | Styling | MIT |
| [Motion](https://motion.dev) (formerly Framer Motion) | Animations | MIT |
| [lucide](https://lucide.dev) (`lucide-react`) | Icons | ISC |
| [clsx](https://github.com/lukeed/clsx) | Class names | MIT |
| [tailwind-merge](https://github.com/dcastil/tailwind-merge) | Class merging | MIT |
| [shadcn/ui](https://ui.shadcn.com) conventions | Theme tokens and component patterns (no code copied as a dependency) | MIT |
| [React Bits](https://reactbits.dev)-inspired components | e.g. `CircularText`; written as our own code, inspired by React Bits' ideas | own code (React Bits is MIT + Commons Clause) |

Backend: [FastAPI](https://fastapi.tiangolo.com) (MIT), [SQLAlchemy](https://www.sqlalchemy.org) (MIT),
[httpx](https://www.python-httpx.org) (BSD-3), [Pillow](https://python-pillow.org) (MIT-CMU),
[cryptography](https://cryptography.io) (Apache-2.0 / BSD), [Uvicorn](https://www.uvicorn.org) (BSD-3),
[Playwright](https://playwright.dev) for tests (Apache-2.0).

## Licence

MIT, see [LICENSE](LICENSE).

