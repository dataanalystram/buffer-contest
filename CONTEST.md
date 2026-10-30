# Buffer Commit — what was built, and when

HuhQueue is our entry to **Buffer Commit** (build window 28 Oct – 13 Nov 2026, 23:59 PT).
Judges: please only judge the work listed under "Built during Buffer Commit".

| | |
|---|---|
| Live app | https://huhqueue-studio.onrender.com |
| Buffer app client ID | `BFQSCDAPoD-hJ0cmZtncSW-kLqnDwPfQ511K35AcVmj` (private client) |
| Scopes | `account:read posts:read posts:write ideas:read ideas:write` + `offline_access` at authorize time |
| Redirect URI | `https://huhqueue-studio.onrender.com/callback` |
| Privacy policy | https://huhqueue-studio.onrender.com/privacy |

## Pre-existing (NOT contest work)

Commit `81b568e` — *"Import pre-existing HuhQueue base"* (imported 28 Oct 2026) — is a snapshot of
[dataanalystram/bufferopensource@243ef81](https://github.com/dataanalystram/bufferopensource): the self-hosted
HuhQueue scheduler with its own native Instagram/YouTube/X/TikTok/LinkedIn connectors (`huhqueue/`, `web/`,
`tests/*.py` outside `tests/studio/`, `docs/` except files named below). It is kept as an optional self-host mode and is
**not** part of the hosted contest app. Its README now lives at [docs/SELF_HOST.md](docs/SELF_HOST.md).

## Built during Buffer Commit (every commit after `81b568e`)

All of `studio/`, `tests/studio/`, `Dockerfile.studio`, `render.yaml`, `requirements-studio.txt`,
`.github/workflows/`, `scripts/secret_scan.sh`, `demo/`, `README.md`, `CONTEST.md`, `SUBMISSION.md`.

| Date (2026) | What |
|---|---|
| 28 Oct | Buffer OAuth 2.0 Authorization Code + PKCE (S256, state, private client, `client_secret_post`), server-side encrypted token store, rotating refresh tokens under a per-user lock, session rotation at login |
| 28 Oct | Buffer GraphQL client: `account`, `channels`, `posts`, `createPost` (draft / queue / share next / share now / custom schedule, image+video assets, per-network metadata), `createIdea`; reads the `RateLimit` headers, caches channel lists, waits out a short `Retry-After` once and surfaces longer ones |
| 28 Oct | Link → drafts: safe article fetch (public hosts only), one caption per channel measured with Buffer's counting rules (UTF-16, LinkedIn URLs = 24, Instagram line breaks = 2, X URLs = 23, Bluesky graphemes) and trimmed to fit; optional AI writer with built-in fallback |
| 28 Oct | Agent layer: MCP server (`/mcp`, Streamable HTTP) + the same REST API the UI uses, per-user agent tokens (hashed), preview → single-use `confirm_token` → submit, policies `review` (default: human approval inbox) / `draft` / `trusted`, daily caps, kill switch, audit log — ported from HuhQueue's unmerged `feat/agent-safety` design and rebuilt per Buffer user |
| 28 Oct | Web UI (Studio, Approvals, Queue, Agents, Activity), privacy page, one-time device links; `data-testid` on every control |
| 28 Oct | Tests: 29 API/unit tests incl. UI = REST = MCP parity (identical `createPost` input from all three) + Playwright e2e; CI runs them on SQLite and Postgres |
| 28 Oct | Deploy: Render free web service + Render free Postgres (tokens survive restarts), GitHub Actions keep-alive ping every 10 min, gitleaks secret scan before every push |

| 29 Oct | Threads from one link: X, Bluesky, Threads and Mastodon posts written as threads and sent through `metadata.<service>.thread`, each part checked against its limit |
| 29 Oct | Try-it sample workspace (`/demo`): no sign-in, sample channels and an agent post waiting for approval; runs the real drafting, validation and approval code against an in-memory stand-in for Buffer |
| 29 Oct | Post previews on home and queue: image/video thumbnails from `assets`, links to the live post (`externalLink`) or the channel in Buffer; public counter of connected Buffer accounts and posts sent |
| 29 Oct | News radar: follow RSS/Atom feeds, newest stories on the home screen with one-click "Write posts"; `list_stories` MCP tool so agents can work from the same news |
| 29 Oct | Plan-limit guard: reads the organization's plan limits, each channel's weekly cap and lock state, and `dailyPostingLimits` before every send; warns on the last allowed post, stops a post Buffer would refuse, shows usage meters and a banner with an upgrade link at 80% |
| 29 Oct | One-click capture: a bookmarklet that opens any page you're reading in HuhQueue and writes the posts |
| 29 Oct | New interface: a "Needs you" home, drafts previewed the way each network shows them, thread previews, mobile layout |
| 30 Oct | Live app moved to Supabase Postgres (pg_cron keep-alive); MCP OAuth sign-in so Claude.ai and ChatGPT can add HuhQueue as a connector; usage counter on the landing page |
| 30 Oct | The agent: research a topic or link, or edit your own photo or video; on-brand carousels, cards and Reels; brand kit (with website import); the Style Ledger (every correction becomes a rule) and a brand check that cites the rule; AI engine settings. Details in [docs/HUHQUEUE.md](docs/HUHQUEUE.md) |
| 30 Oct | Renamed the product to **HuhQueue** everywhere (was "House Style"; Style Ledger stays a feature name) |
| 30 Oct | **Planner**: one tab replaces Calendar and Buffer queue. List / Week / Month, filters (channels, post type, status, caption search), timezone selector, "No date" drafts sidebar, Buffer posting times (`channels.postingSchedule`), click an empty slot to schedule, drag to reschedule, edit, approve and delete-draft actions, light and dark themes, view and filters kept in the URL. New Buffer calls `editPost` and `deletePost` behind one `update_post` action (also `reschedule_post`, `delete_draft`) shared by UI, REST (`PATCH /api/posts/{id}`, `POST /api/posts/{id}/reschedule`, `DELETE /api/posts/{id}`) and MCP; approvals can carry a time. Tests: `tests/studio/test_planner.py` and a Playwright run that switches views, filters by channel and drags a draft. Screenshots in `docs/screenshots/planner-*.png` |

Not used: Buffer analytics/metrics APIs (not available to third-party apps).
