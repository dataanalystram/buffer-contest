# HuhQueue: next milestones (local hand-off, 27 Oct 2026)

`main` @ `0ec9a29` is live on InstaBox, with 302 unit and 71 e2e tests passing. Work through the milestones below in order. Each has a branch, the task, the files involved, and how to know it's done.

## Local setup
```bash
git clone https://github.com/dataanalystram/bufferopensource && cd bufferopensource
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt segno pytest playwright
playwright install chromium        # or use the chromium from your OS package manager
cp .env.example .env               # fill in keys only for the networks you test live
python3 -m pytest tests -q --ignore=tests/e2e   # unit tests, about 300
python3 -m pytest tests/e2e -q                  # Playwright e2e tests, about 70
```

Rules for every milestone:
- Branch from the latest `main`.
- Give every new UI control a `data-testid`.
- Every UI call must map to an action in `huhqueue/actions.py`, which also exposes it as an MCP tool. `tests/test_headless.py` enforces this.
- Merge only when the full suite is green. Never force-push `main`.
- Credit any copied MIT or Apache code in `NOTICE`. AGPL and GPL projects (Postiz, LinkStack, Socioboard) are for ideas only; don't copy their code.

---

## M1: Finish and merge `feat/bio-bulk`
**State:** Bio v2 is complete and tested: `services/bio.py`, `api/bio_api.py`, `web/src/pages/Bio.jsx`, `tests/test_bio_v2.py` (22 tests) and `tests/e2e/test_ui_bio_v2.py` (5 tests). The bulk and settings backend is in `api/bulk_api.py`; it's wired up but only partly tested.

**To do**
1. `git checkout feat/bio-bulk && git rebase origin/main`. Expected conflicts:
   - `Growth.jsx`: take the bio branch's version, because the bio section moved to `Bio.jsx`.
   - `NOTICE`: keep the entries from both sides.
   - `app.py` imports: merge both sets.
   - `Queue.jsx`: merge.
   - Rebuilt bundle under `huhqueue/static/app/`: don't merge it. Rebuild it (`cd web && npm ci && npm run build`).
2. Write `tests/test_bulk.py`. Cover:
   - each `POST /api/bulk` action: delete, draft, queue, prioritize, reschedule, publish_now, tag, untag, set_tags, campaign, duplicate, move, request_approval;
   - the per-item results list;
   - posts queued by an agent token are held for approval;
   - contributors are refused queue, prioritize, reschedule, publish_now and move;
   - `POST /api/bulk/approve` refuses agent tokens with 403;
   - `GET /api/posts/export.csv` only includes channels the user can see;
   - `POST /api/channels/bulk-settings`, including that an invalid timezone gets 422;
   - `notify_events` and `notify_paused`;
   - `POST /api/notifications/test`.
3. Bulk UI in `web/src/pages/Queue.jsx`:
   - checkbox per post and select-all;
   - a bulk bar with Reschedule (date/time dialog), Tag/Untag, Campaign, Duplicate, Move to channel, Request approval, Delete and Export CSV;
   - Approve/Reject on the Approvals tab.
   - Show each item's result as a toast with the failed count.
4. Settings UI in `web/src/pages/Settings.jsx`:
   - Channels › Bulk edit (timezone, daily limit, pause, days off, UTM, copy posting times from another channel);
   - Notifications › one switch per kind, a pause switch and a "Send test" button.
5. Add e2e tests `tests/e2e/test_ui_bulk.py` and `tests/e2e/test_ui_settings_bulk.py`.
6. Docs: in `docs/FEATURE_MATRIX.md`, update row 11 (link in bio) and the bulk and settings rows.

**Done when:** the full suite is green, including `test_headless.py`, and the branch is merged.

## M2: Finish and merge `feat/agent-safety` (plan item 2)
**State:** commit `c327937` has the backend and tests:
- kill switch (API, CLI and a worker hold);
- per-token daily caps, channel scope, expiry and auto-confirm;
- MCP preview with a `confirm_token` for public actions;
- a richer audit log with filters, facets and CSV export.

**To do**
1. Rebase onto `main`, after M1.
2. UI in Settings › API & MCP agents:
   - a big Kill switch toggle with a red banner across the whole app while it's on;
   - token create/edit with daily cap, channels, expiry and auto-confirm.
3. Audit log page: filters (actor, action, risk, date), facets and an Export CSV button.
4. Put the kill-switch state in `/api/health` and `/api/context`, so agents can see it.
5. Write e2e tests:
   - while the kill switch is on, publishing is blocked and the worker holds posts;
   - a token over its cap gets 429;
   - an MCP public action without `confirm_token` returns only a preview.

**Done when:** the suite is green and merged, and a live check on InstaBox with the kill switch on shows nothing publishing.

## M3: Reliable publishing (plan item 3, built on branch `m3/reliable-publishing`)

**As built** (differences from the plan below): errors are classified once per network by `@http_errors` in
`connectors/base.py` (408/429/5xx retryable, other 4xx permanent, Retry-After honoured, HTTP status kept).
`PublishAttempt` rows (`GET /api/targets/{id}/attempts`, action `post.attempts`, shown under the post timeline) record
outcome `published|processing|inbox|retry|failed|auth|unconfirmed|interrupted`. Backoff is equal jitter over
60 s * 2^n capped at 1 h, 6 tries (`HUHQ_MAX_ATTEMPTS`). A rate-limited account sets `channels.rate_limited_until`
and its waits don't use up tries. Missed posts are checked every tick (not hourly, so a reconnected or unpaused
channel can't fire stale posts first): up to **6 h** late they go out, later a queued post moves to the next free
slot and a custom-time post becomes "needs action" with a notification. `/api/health` returns
`worker: {thread_alive, last_tick, age_seconds, stale, interval, due_now}`. Connectors checkpoint `provider_state`
before irreversible calls; a lost reply to a create call is confirmed via the account's recent posts (X) or handed to
a person as "needs action" (LinkedIn, TikTok pull-from-URL), never re-sent.

**Files:** `huhqueue/worker.py`, `huhqueue/services/scheduling.py`, `huhqueue/connectors/base.py`, `huhqueue/models.py`.

1. **Error classes.** In `connectors/base.py`, add `RetryableError` (429, 5xx, timeouts, network) and `PermanentError` (400 validation, 403 permission, media rejected). Each connector maps its API errors to one of the two.
2. **Attempt log.** Add a new model `PublishAttempt` with these fields:
   - id, target_id, attempt_no, started_at, finished_at;
   - status (`ok`, `retryable`, `permanent`), http_status, error_code, error_message, remote_id.
   - Show it in the post's detail drawer.
3. **Backoff.** On a retryable error: `delay = min(base * 2**n, 1h) * uniform(0.8, 1.2)`, with base 60 s and at most 6 attempts. Honour `Retry-After` when present. After the last attempt, set status `failed` and notify.
4. **Per-channel rate memory.** Store `rate_reset_at` on the channel. The worker skips that channel until the reset time passes.
5. **Missed-post requeue.** Every hour, find posts that were due more than 10 minutes ago and are still `scheduled`, and requeue them. The InstaBox sleep is the reason. Each requeue writes an audit event.
6. **Heartbeat.** The worker writes `last_tick_at` every loop. `/api/health` returns `worker: {last_tick_at, stale: bool}`, stale after 5 minutes.
7. **Idempotency.** Before retrying a publish, check whether `remote_id` was already set, so the same post is never published twice. Instagram containers need an extra check: look up the container status before creating a new one.
8. **Tests:** fake connectors that raise each error type, plus a freezegun-style clock to test backoff, the requeue and the heartbeat.

**Done when:** `tests/test_reliable_publishing.py` is green and a forced-429 run on InstaBox retries and then succeeds.

## M4: Live connector validation (needs your accounts)
- **YouTube:** reconnect and pick the **@huhbyte** channel (it's currently connected to your personal channel). See `docs/CONNECT_INTEGRATIONS.md` § 4. Then post the ready Short.
- **LinkedIn:**
  - create your own LinkedIn developer app with products "Sign In with LinkedIn (OIDC)" and "Share on LinkedIn";
  - set `LINKEDIN_CLIENT_ID` and `LINKEDIN_CLIENT_SECRET`;
  - connect and send one test post;
  - confirm that `LINKEDIN_API_VERSION` (default `202605`) is accepted.
  - Company pages also need Community Management API approval.
- **Meta app review:** needed so Instagram comments arrive outside Development mode.

## M5: Instagram via Facebook Login and the music picker
The spec is `docs/FBLOGIN_PLAN.md`. It covers a second Instagram connector over the Graph API (Facebook Login) for collaborators, location and product tags. Note that the Reels music picker is not available in the public API; the plan explains the workaround.

## M6: Always-on hosting
The InstaBox dev box sleeps after 30 minutes idle, so scheduled posts can be missed. M3 step 5 softens this, but doesn't fix it.
- Add a `Dockerfile` and `docker-compose.yml` (app, worker, Postgres or SQLite volume).
- Deploy to an always-on host, such as a small VPS or Fly.io.

## Later (from `docs/OSS_UPGRADE_PLAN.md`)
- Remote MCP over HTTP (SSE/streamable) with token auth.
- Media checks before scheduling (aspect ratio, duration, size per network).
- Client review links.
- Channel groups.
- Automations (RSS → draft).
- Analytics for links and QR codes.
