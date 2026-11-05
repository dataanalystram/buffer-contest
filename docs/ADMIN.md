# Admin panel (`/admin`)

The operator's view of who uses this HuhQueue, and the three things only the operator can do: block, unblock and delete an
account. Code: `studio/admin.py` (routes + actions), `studio/admin_ui/` (the page). Tests: `tests/studio/test_admin_storage.py`.

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

## Requests to delete
Someone who can still sign in uses Settings → Disconnect and delete. Someone who can't (lost Buffer access) asks through
the contact on the privacy page; the operator checks it is them and uses Delete user.
