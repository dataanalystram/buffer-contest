# Admin panel (`/admin`)

The operator's view of who uses this HuhQueue and what it spends, and what only the operator can do: block, unblock and
delete an account, cap or revoke one of its tokens, and pause publishing, AI, R2 uploads or video links for everyone. Code: `studio/admin.py` (routes + actions), `studio/admin_ui/` (the page). Tests: `tests/studio/test_admin_storage.py`, `tests/studio/test_admin_controls.py`.

## Who can open it
- Set `HQ_ADMIN_IDS` on the web service: comma-separated **Buffer account ids** and/or **Buffer emails** (case-insensitive),
  e.g. `HQ_ADMIN_IDS=5f1c0a…,ram@example.com`. Empty or unset: nobody. Ids are the stronger choice (an email is whatever
  Buffer reports for the account); the id is shown in the panel and in `GET /api/me`.
- Only a signed-in **browser session** counts. Agent tokens, MCP OAuth tokens and anything else never reach it.
- Everyone else gets the **same 404** as a path that doesn't exist (`{"detail":"Not Found"}`), for the page, its assets and
  every `/api/admin` call, whatever the query or body. Nothing is parsed or validated before the admin check, so a bad
  parameter can't reveal the route either. `/admin` is in `robots.txt` as Disallow.
- There are **no MCP tools** for admin, on purpose.

## This deployment (huhqueue-studio.onrender.com)
The only admin is Ram's own studio account: Buffer account **somarajuramprasadie@gmail.com**, studio user id
**`6ad9d04ef3e1acb6edfdc760`** (read from `users` on 4 Nov 2026). Set on the Render service:
`HQ_ADMIN_IDS=6ad9d04ef3e1acb6edfdc760` (the id, not the email: an id can't be claimed by another Buffer account).

## What it shows
Who signs in. Per account: the Buffer identity we already store from the OAuth sign-in (Buffer account id, name,
email, organisation), **first sign-in** (when the account row was created by its first Buffer sign-in), **last sign-in** (time, a coarse network such as
`203.0.113.0/24` or `2001:db8:1::/48`, and the browser family and OS; never the full IP or user agent), **last active**
(`users.last_seen`, written at most every 5 minutes, or an agent token's last use), runs (`agent_runs`), posts sent
through HuhQueue (`submit_post` in the activity log, which keeps 90 days), storage used (media rows + Library files), and
status (`active`, `agents paused`, `disconnected`, `blocked`, `demo`). Sign-in IPs and user agents are themselves only
kept 30 days (`SIGNIN_IP_DAYS`), so older sign-ins show a time and no network.

Above the table: database size (`pg_database_size` on Postgres), media bytes still in the database vs in R2, where new
media goes, and how many R2 objects are waiting to be deleted (docs/STORAGE.md).

## Actions (each one CSRF-checked and written to the admin log)
| Action | What happens |
|---|---|
| **Block** (optional reason) | `users.blocked=1`; every browser session deleted at once; agent and MCP tokens stop resolving (not revoked, so Unblock restores them); MCP authorization codes deleted, token refreshes refused; sign-in answers "This account can't sign in right now"; device links, Send-a-file codes and upload sessions deleted. Reversible. |
| **Unblock** | `blocked=0`: the account's existing agent tokens work again and it can sign in. |
| **Delete user** | Type the account id to confirm. The same erasure as the person's own **Disconnect and delete** (GDPR): every row in every table (`Store.delete_user`), the sealed Buffer OAuth tokens, media rows and Library chunks and their R2 objects (released into `blob_gc`, then deleted by the sweep). Then a **tombstone** in `user_tombstones`: a one-way hash of the id, the time and `via` (`admin` or `self`). |

An admin can't block or delete their own account (400). Deleting doesn't stop someone from signing in again later with
Buffer (that creates a new, empty account); to keep someone out, block instead.

Buffer has no token-revocation call in this codebase: deletion erases the tokens from our database; the person can also
remove HuhQueue in their Buffer account settings (the privacy page says so).

## Agent and MCP tokens of one account
**Tokens** on a user's row opens their agent tokens and MCP apps: name, app, policy, last used, requests this hour,
audited actions in the last 24 h, and two caps (never the token or its hash):
- **Hourly cap** (`agent_tokens.admin_hourly_cap`, 1–100,000, empty = none): every authenticated request on the token
  counts, reads included, on REST and `/mcp`; past it the request answers **429** (`/mcp` with `Retry-After`). A sliding
  one-hour window in memory (one process); a deploy starts it at zero. Use it for a noisy token such as a smoke-test agent.
  **Requests this hour** is only counted while the token has an hourly cap (an uncapped token shows 0 here; use
  **Actions in 24 h** and **Last used** for those), and saving either cap starts the hour again.
- **Daily cap** (1–100): the token's existing daily cap on actions (`Studio._guard_write`).
- Both caps follow an MCP token through its refreshes: the successor takes the parent's caps in force at that moment
  (and its hourly window), a cap saved on a token also lands on every successor it rotated into (`parent_id`), and a
  cap saved while a refresh runs reaches the new token too. A refresh never resets an operator's limit.
- **Revoke** (two clicks): `revoked=1`, for good. Its MCP refresh token is refused too (`mcp_oauth` checks `revoked`).
  To stop a whole account reversibly, Block instead.

## Kill switches
`studio/opsctl.py`, table `ops_switches` (no row = running). Each switch stops **new** work of its kind for everyone,
the admin included, at the one place that work starts; work already running finishes.

| Switch | Stops | Where |
|---|---|---|
| `publishing` | anything sent to Buffer (create and edit post); 503 with a plain message. Delete still works. | `BufferClient.create_post` / `edit_post` |
| `ai` | AI on HuhQueue's own keys: Claude and OCI (`budget.check` / `oci_check`), the free engines (`free_check`), the free starter model, the Groq / NVIDIA / OpenRouter ledger (`free_quota.Ledger.reserve`), the link drafter. The brain falls back to the rule writer, the worker to local Whisper and the layout rules. A person's own key keeps working. | `studio/budget.py`, `studio/free_quota.py`, `House._ai_reserve`, `Studio.draft_from_link` |
| `r2_uploads` | new writes to the bucket: new media stays in the database, One take large uploads answer 507; reads and deletes go on | `r2usage.can_write` |
| `link_ingest` | new video links (One take, `ingest_video_link`, `clips_from_video`, the agent's Opus path), before any consent is recorded; uploads still work | `Takes.start` |

**Fails closed**: if the switch table can't be read, every switch reads as paused until a read succeeds. A process with no
store (the video worker, scripts) has nothing to read and is never paused here; the web app binds its store at start-up.
State is cached 5 s per process; a change in /admin applies at once on this instance. Every Pause / Resume is in the admin log.

## Credits and free tiers
`GET /api/admin/usage`: Claude spend on our key against `AI_BUDGET_TOTAL_USD` (and the testing pool), OCI Gemini against
`AI_OCI_BUDGET_TOTAL_USD`, and the Groq / NVIDIA / OpenRouter
requests in the last 24 h against each free daily limit. The **Media bandwidth** section (`/api/admin/egress`,
studio/egress.py) counts **media only**: media bytes read from the database (the part of Supabase egress that matters) and
media bytes sent to clients (the part of Render bandwidth that matters), with R2 against its caps. Other traffic (pages,
JSON, API calls) is not in these numbers; the provider dashboards have the full totals.

## Admin log
`admin_audit`: time, the admin's account id, action, a hash of the subject's id (`sha256("hq-subject:" + id)`), detail
(the block reason), ok. A deleted account's id and email never appear in it; when an admin's own account is erased,
their id in these rows is replaced by its hash too. Erasure (rows, tombstone and this log entry) is one transaction.

The admin panel is a deliberate exception to the REST/MCP/⌘K/CLI parity rule (CLAUDE.md): Block, Unblock and Delete are
browser-session-only, because agent tokens must never administer accounts (`/api/admin/*` answers 404 to them). The affected account's own activity log
also gets `account_blocked` / `account_unblocked` rows (deleted with the account).

## REST
All under a signed-in admin browser session; writes need the `X-HQ-CSRF` header from `/api/session`.
- `GET /api/admin/users?q=&limit=100&offset=0&demo=0` → `{users, total, limit, offset, overview}`
- `POST /api/admin/users/{id}/block` `{reason?}` · `POST /api/admin/users/{id}/unblock`
- `POST /api/admin/users/{id}/delete` `{confirm: "<id>"}`
- `GET /api/admin/audit?limit=100`
- `GET /api/admin/users/{id}/tokens` · `POST /api/admin/users/{id}/tokens/{tid}/revoke`
- `POST /api/admin/users/{id}/tokens/{tid}/limit` `{hourly_cap?: 1-100000|null, daily_cap?: 1-100}`
- `GET /api/admin/switches` · `POST /api/admin/switches/{name}` `{paused: bool, reason?}`
- `GET /api/admin/usage`

## Requests to delete
Someone who can still sign in uses Settings → Disconnect and delete. Someone who can't (lost Buffer access) asks through
the contact on the privacy page; the operator checks it is them and uses Delete user.
