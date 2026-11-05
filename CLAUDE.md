# CLAUDE.md: HuhQueue (repo `bufferopensource`)

Free, self-hosted, Buffer-style social media scheduler, built agent-first. The product, Python package and CLI are all
called **HuhQueue** / `huhqueue` (the repo is `dataanalystram/bufferopensource`; this checkout lives in `Openbuffer/`).
Brand assets and defaults reference the owner's account **HuhByte** (`@huhbyte`, IG `huhbyte.ai`). MIT licensed.

Rule of the codebase: **everything the UI can do is also a REST action, an MCP tool, a ⌘K command and a CLI command**,
all generated from one registry (`huhqueue/actions.py`). Keep that parity when adding features (CI tests enforce it).
Exception: the operator admin panel (`/admin`, studio/admin.py, docs/ADMIN.md) is browser-session-only on purpose:
agent tokens must never administer accounts.

## Layout

```
huhqueue/                 Python package (FastAPI + SQLAlchemy 2 + SQLite, httpx for networks)
  cli.py                  `huhqueue` entry point (serve, worker, mcp, token, passwd, actions, run, schedule, list, ...)
  config.py               Settings from env / .env (tiny loader, no python-dotenv). All env vars listed in .env.example
  core.py                 Core container: settings, Database, TokenCipher, connector instances, prefs (KV), app keys
  db.py                   engine (WAL, FKs), runs migrations -> create_all -> add-missing-columns -> after-migrations
  models.py               all tables (Channel, Slot, Media, Post, PostTarget, Event, Idea, Comment, ApiToken, AuditLog,
                          Revision, PostNote, Contact, User, UserSession, Link, Webhook, Automation, Campaign, BioPage...)
  migrations/             forward-only NNNN_name.sql|py, tracked in schema_migrations (0001 baseline, 0010 teams owner)
  actions.py              THE action registry (~127 core entries + feature modules call register())
  security.py             password (scrypt), signed sessions, CSRF, hashed scoped API tokens, rate limiter, log redaction
  teams.py                users/roles (owner/admin/editor/contributor/client/viewer), per-channel access matrix, invites,
                          TOTP 2FA, notifications, SMTP email
  worker.py               publishing worker (thread inside `serve`, or `huhqueue worker`)
  mcp_server.py           dependency-free MCP stdio server (JSON-RPC); proxies to the REST API with a bearer token
  tunnel.py               cloudflared / ngrok helper that exposes only the edge port
  crypto.py               Fernet encryption of OAuth tokens and saved app keys
  connectors/             one file per network + base.py contract + fake.py (demo/tests)
  services/               business logic as plain functions over Core + session (posts, scheduling, media, oauth,
                          community, analytics, reports, pdf, growth, assistant, endcard, events, meta_compliance)
  api/app.py              create_app() (UI + REST, ~80 routes, auth middleware) and create_edge_app()
  api/*.py                feature routers, each `add_*_routes(app, core)` plus its registry entries:
                          actions_api (catalog/runner/command bar/AI settings), agents (/llms.txt, /agents),
                          context (/api/context, /openapi.json), pro_content, reports_api, growth, teams_api, channel_pages
  static/app/             BUILT React bundle (committed so pip users never need Node)
web/                      React 19 + Vite 8 + Tailwind v4 + Motion + lucide; source of static/app
  src/App.jsx             shell, sidebar, header, ⌘K palette/command bar, page switch, agent-context sync
  src/router.js           hash routing (#/queue/ch_123?tab=drafts): every screen/tab/filter is deep-linkable
  src/api.js              fetch wrapper (sends X-CSRF-Token from the huhq_csrf cookie)
  src/store.jsx           global store: channels, providers, settings, library, me/can
  src/pages/*.jsx         Home, Queue, Calendar, Composer, Ideas, Community, Insights, Library, Contacts, Growth,
                          Settings, Channels, Channel{Detail,Tabs,Publish,Community,Insights,Settings}
  src/components/*.jsx    FilterBar, PostCard, Onboarding, Approvals, Team, AppKeys, BulkUpload, Confirm, ...
tests/                    pytest (TestClient + respx mocks); tests/e2e = Playwright against a live uvicorn
scripts/                  dev_setup.sh, demo_seed.py, screenshots.py, deploy_instabox.py
docs/                     plans, research, feature matrix, live-test notes (see below)
```

## Runtime architecture

`huhqueue serve` starts **two** FastAPI apps from one `Core`:
- **UI + REST** on `HUHQ_HOST:HUHQ_PORT` (default `127.0.0.1:8787`). Serves the SPA at `/` (with an embedded
  `<script id="agent-context">`), `/api/*`, Swagger at `/api/docs`, OpenAPI at `/api/openapi.json` and `/openapi.json`,
  `/llms.txt`, `/llms-full.txt`, `/agents`. The publishing worker runs as a daemon thread here (unless `--no-worker`).
- **Edge** on `HUHQ_PUBLIC_PORT` (default 8788): the only thing meant to be public. Media by unguessable token
  (`/m/{token}/{name}`, networks pull media from here), OAuth callbacks (`/oauth/{provider}/callback`), Meta deauthorize /
  data-deletion callbacks, `/privacy`, `/terms`, short links `/l/{code}`, bio pages `/bio/{slug}`. Hosted single-origin
  installs get these edge routes on the UI app too.

Auth (middleware `guard` in `api/app.py`): every `/api/*` except health/auth/invites needs either a session cookie
(`huhq_session` + `X-CSRF-Token` matching `huhq_csrf` on writes) or `Authorization: Bearer hq_...`. Token scope comes from
`security.required_scope(method, path)` (read/write/publish/comments/admin); `X-HUHQ-Tool` enforces the per-token tool
allow-list. Teammates (session or user-bound token) are also checked by `teams.authorize` (role + per-channel access),
and GET lists are filtered to visible channels. Non-GET calls are written to `audit_log`.

Data: SQLite at `$HUHQ_DATA_DIR/huhqueue.db` (default `./data`), media in `data/media/`. Prefs, app keys (encrypted),
library items etc. live in the `settings` KV table. Developer-app keys saved in Settings › App keys win over `.env`.

### Posts and publishing
- A `Post` (caption, media, tags, campaign, `topic_key`) fans out into one `PostTarget` per channel with optional
  caption/media overrides and network `options`. Target statuses: `draft, pending_approval, scheduled, sending,
  processing, needs_action, sent, failed, cancelled`. `when` = `queue | prioritize | now | custom | draft`.
- `services/scheduling.py`: weekly slots per channel in the channel's timezone, queue fills next free slot, daily rate
  guard (default 2/day) with a "major news" `override_rate_limit`.
- Dedup: the same media (SHA-256) or `topic_key` is never posted twice to one channel (409 in API/MCP, re-checked by worker).
- `worker.tick()`: refresh tokens, warn on expiry, recover stale `sending` (20 min), claim + publish due targets,
  retry with backoff `[60, 300, 900, 3600]s` up to `HUHQ_MAX_ATTEMPTS`, poll `processing` (45 min timeout), refresh
  insights (6h, 30-day window), growth automations, comment sync (15 min), follower snapshots, scheduled reports.
- Agent tokens with `require_approval` (and contributor teammates) put posts into `pending_approval` for a human.
- Video end card ("Follow @handle") via ffmpeg when on PATH (`services/endcard.py`); PNG/WebP→JPEG copies for Instagram.

### Connectors (`huhqueue/connectors/`)
Contract in `base.py`: `authorize_url`, `exchange_code`, `validate`, `publish` -> `PublishOutcome(published|processing|inbox)`,
optional `poll`, `refresh`, `revoke`, `fetch_insights`, `list_media`, `account_insights`, `list_comments`, `list_mentions`,
`reply_comment`, `post_comment`, `option_schema()` (the composer renders per-network fields from it), `formats()` /
`suggest_format()`. Errors: `RetryableError` (worker retries), `AuthError` (channel needs reconnect), `PermanentError`,
`NotConfigured`. Built-ins registered in `connectors/__init__.py`; third-party plugins via the `huhqueue.connectors`
entry-point group. `PLANNED` (Facebook Pages, Threads, Bluesky) only show as "coming soon".

| id | Network / API | Notes |
|---|---|---|
| `instagram` | Instagram API with Instagram Login (graph.instagram.com, `INSTAGRAM_GRAPH_VERSION` v26.0) | image/carousel/reel/story, collaborators, first comment, insights, comments, mentions, 50/24h publish cap, Meta compliance callbacks. Facebook-Login variant is planned (docs/FBLOGIN_PLAN.md) |
| `youtube` | Google OAuth + PKCE, YouTube Data API v3 | resumable upload, Short/Video, all snippet/status fields, thumbnails, playlists, captions, comments; private until `YOUTUBE_VERIFIED=1` |
| `x` | OAuth 2.0 PKCE, X API v2 | chunked media upload, posts, history, insights |
| `tiktok` | Login Kit + Content Posting API | direct post or inbox upload, photo carousel; `SELF_ONLY` until `TIKTOK_AUDITED=1`; no comment API |
| `linkedin` / `linkedin_page` | OIDC + Share on LinkedIn; pages need a 2nd app (Community Management API) behind `LINKEDIN_PAGES_ENABLED` | text/image/multi-image/PDF/video/link/first comment; 60-day tokens, warns 7 days before expiry; `LINKEDIN_API_VERSION` header |
| `fake` | Demo network (`HUHQ_ENABLE_FAKE=1`) | caption words drive outcomes: `FAIL`, `RETRY`, `AUTHFAIL`, `SLOW` |

All network HTTP goes through the shared `core.http` httpx client, so tests mock it with `respx`.

### Agent / headless layer
- **Registry** (`actions.py`): each `Action` has id, REST method/path, JSON-schema params, scope, risk
  (`safe, write, destructive, public, human, secret`). `human` and `secret` actions are never exposed to MCP, the palette
  forms or the LLM. Feature modules add entries with `register(A(...))`. `LEGACY_TOOLS` keeps old MCP tool names.
- Generated from it: `GET /api/actions`, `POST /api/actions/{id}:run` (dry-run, confirm tokens, idempotency), OpenAPI
  `x-huhq-*` extensions, MCP `tools/list` (tool name = id with `.`→`_`), ⌘K palette, `huhqueue actions` / `huhqueue run`,
  `/llms.txt`, `/llms-full.txt`, `/agents`.
- `GET /api/context?page=<route>`: what a page shows, its actions and current IDs; also embedded in the HTML.
- **Command bar** (`services/assistant.py`): natural language → one action. Pluggable LLM (any OpenAI-compatible
  endpoint, Ollama, NIM, Anthropic Messages); keys stored encrypted, set only in Settings › AI. Deterministic rules
  parser when no LLM is configured. Public/destructive actions always need confirmation.
- **MCP**: `huhqueue mcp` with `HUHQ_URL` + `HUHQ_API_TOKEN` (stdio, protocol `2025-06-18`, no SDK dependency).
- Every UI control has a stable `data-testid` and accessible label (enforced by tests using `tests/jsx_controls.py`).

## Run, build, test

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt                 # or: scripts/dev_setup.sh (also npm installs web/)
python -m huhqueue serve                            # http://localhost:8787 (edge :8788)
HUHQ_ENABLE_FAKE=1 python scripts/demo_seed.py      # demo data with the fake connector
huhqueue token --name agent --scopes read,write     # mint a scoped token (shown once)
```

- Python ≥ 3.10. Config: copy `.env.example` to `.env`; never commit `.env` or `data/`.
- **UI changes**: edit `web/src`, then `cd web && npm install && npm run build`. The build overwrites
  `huhqueue/static/app` (hashed asset names) and **the built bundle must be committed** with the source change. `npm run dev`
  proxies `/api` to :8787.
- **Backend tests**: `python -m pytest tests --ignore=tests/e2e` (fixtures in `tests/conftest.py`: temp data dir, fake
  connector enabled, dummy app keys, `api` = logged-in client that sends CSRF, `make_image`).
- **E2E**: Playwright, Chromium needed; each module starts its own uvicorn pair. Run files one at a time:
  `CHROMIUM_PATH=/path/to/chromium python -m pytest tests/e2e/test_ui.py`. `test_deployed.py` hits the hosted instance.
- Parity/consistency tests to keep green when adding routes or UI: `tests/test_m1_registry.py` (registry ↔ routes,
  MCP, CLI, OpenAPI), `tests/test_headless.py` (every UI API call is a registry action with an MCP tool; control labels).
- Watch for date-dependent tests; several past commits fixed tests that rotted with the calendar.

## Conventions seen in the history
- New feature = service functions in `services/`, routes in an `api/*.py` `add_*_routes` module wired from `create_app`,
  registry entries for every route, backend tests, then UI + `data-testid`s + an e2e test, then rebuild the bundle.
- New columns: add to `models.py` (auto-added on startup); data moves or non-trivial schema changes get a numbered
  migration in `huhqueue/migrations/`.
- Destructive UI actions use the in-app `Confirm` dialog, never `window.confirm`/`prompt`.
- Copy third-party code only with credit in `NOTICE`.
- Commit messages are descriptive, feature-prefixed sentences ("Channel pages: ...", "E2E: ...", "Rebuild web bundle").

## Status (28 Oct 2026)
Everything below is on `main` and deployed to the InstaBox machine `huhqueue`
(https://8787-agqwhbc22xbwwr7lmn2ja5jobq.instabox.tech) with `scripts/deploy_instabox.py --machine huhqueue`.
Owner's rule: commit to `main` and test on that live URL (real channels connected there), not only localhost.

On `main`: queue, composer, calendar, ideas, library, contacts, approvals, notifications, command bar; Instagram,
YouTube, X, TikTok, LinkedIn connectors; reports/insights; growth; teams; channel pages; Buffer CSV import; headless
layer; plus (27-28 Oct) dynamic OAuth callback URL (follows the URL you sign in on), bulk actions + bulk UI,
Link-in-bio block editor (`web/src/pages/bio/`, live draft preview, autosave, design drafts), agent safety (kill switch,
token caps, audit log), AI ideas / alt text / comment insights / channel groups, bring-your-own AI model, reliable
publishing (`PublishAttempt`, jittered backoff, rate-limit holds, missed-post requeue, heartbeat in `/api/health`),
Docker (`Dockerfile`, `docker-compose.yml`). ~402 unit tests; e2e green except `test_ui_reports::test_01_views` and
`test_ui_settings::test_12` (tunnel step; environment-specific, fail on older main too).

Gap list vs Buffer: `docs/BUFFER_GAPS.md`. Still open: X threads, image editor, insights extras (separate threads),
Instagram via Facebook Login (M5, `docs/FBLOGIN_PLAN.md`), M4 owner tasks (YouTube @huhbyte reconnect, LinkedIn app,
Meta app review).

Build notes: on this Mac `npm run build` needs `npm install --no-save @rolldown/binding-darwin-arm64@<ver>
lightningcss-darwin-arm64@<ver> @tailwindcss/oxide-darwin-arm64@<ver>` (lockfile was made on Linux); e2e runs with
`CHROMIUM_PATH` pointing at an installed Playwright Chromium.

## UI design
The hosted contest app's UI is `studio/static/` (plain HTML/CSS/JS, no build step). Follow `DESIGN.md` for every UI
change there: Taste tokens, one accent that means "needs a person", flat hairline cards, motion only in response to an
action, every control with a `data-testid`.

## Docs
`README.md` (status, quick start), `SETUP.md` (developer apps, redirect URIs, compliance callbacks, hosting),
`docs/CONNECT_INTEGRATIONS.md` (numbered connect steps per network), `docs/CONNECTORS_RESEARCH.md` (tiers, scopes,
limits), `docs/ROADMAP.md` (milestones + acceptance tests),
`docs/CMS_PLAN.md`, `docs/AI_NATIVE_PLAN.md` (registry/risk design), `docs/FBLOGIN_PLAN.md`,
`docs/BUFFER_TEARDOWN.md`, `docs/BUFFER_PRO_FEATURES.md`, `docs/LIVE_TEST_2026-10-26.md`.
Note: `connectors/base.py` mentions `docs/CONNECTORS.md`, which does not exist.
