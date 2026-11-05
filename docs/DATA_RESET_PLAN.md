# Data reset plan (after the contest) — NOT executed, needs Ram's explicit OK

Status: proposal only. Nothing in this document has been run against production. The contest judges use the live site
until **2026-11-13 23:59 PT**, so every row, file, connection and credential stays as it is until then.

## Why

The hosted database is Supabase's free Postgres: 0.5 GB of database size and 5 GB of egress a month. On 5 Nov 2026 the
dashboard showed **0.376 GB (75%)** of size and **30.07 GB (601%)** of egress, with a grace period until **5 Dec 2026**
after which requests answer 402. Media bytes live in BYTEA columns (`media.data`: 582 files, 268 MB, 30 videos average
7.3 MB; `library_chunks.data`: 5 MB; `upload_parts.data`: empty). The Egress guard (docs/STORAGE.md) stops the
re-reading that produced the egress; this plan is about shrinking what is stored once the demo data is no longer needed.

## Rules (all six, every time)

1. **A backup first**, taken the same day (below).
2. **A proven restore** of that backup into a throwaway Postgres, with row counts compared to production.
3. **Ram's explicit approval** of the exact statements, in writing, after reading 1 and 2.
4. **No secrets in the repo or in logs**: the connection string is read from the environment, never echoed.
5. **R2 objects are covered too**: a row with a `storage_key` keeps its bytes in R2, which the dump does not contain,
   and a purge queues those objects for deletion (`blob_gc` → `blobstore.sweep`). Before any purge, either confirm that
   no `media` / `library_chunks` row has a `storage_key` (`SELECT count(*) ... WHERE storage_key IS NOT NULL` = 0), or
   copy every referenced object to a backup bucket/folder and verify each sha256 against the row.
6. **Writes frozen** from before the backup until the last delete (section 0).

## 0. Freeze writes (from before the backup until the last delete)

`pg_dump` doesn't block writers, so anything created after the dump would be missing from the backup and could still be
purged (CodeRabbit, PR #119). For the whole window, from before the dump and any R2 copy until every delete and the
final counts are done:

1. Stop background work while the web service still answers: suspend any cron job, cancel `queued` render jobs and let
   `running` ones finish (or cancel them). The worker reports through the web service, so this must happen first. Wait
   until `render_jobs` has no row in `queued`/`running` and `upload_sessions` has none open (read-only counts).
2. Suspend the render worker, then the web service in Render (there is no in-app maintenance switch), so no new media,
   runs or uploads are written. Buffer fetches a scheduled post's `/m/` link when it publishes, so pick a window with no
   scheduled post in it (check Buffer's queue first) and keep it short. Re-run the counts from item 1.
3. Only then take the dump (section 1) and, if R2 is in use, the object copy (rule 5). Lift the freeze only after the
   post-delete check in section 4, step 1 passes.

## 1. Backup

Supabase's free plan has no downloadable backups, so the backup is a `pg_dump` from a machine Ram controls.

* Tool: `pg_dump` **17** (it must be at least the server's major version).
* Connection: the session pooler URL (port 5432), from the Render service's `DATABASE_URL` or the Supabase dashboard,
  put in `PGURL` in the shell, never typed into a command line that is logged.

```sh
# a) everything except the blob columns (~1 MB, a few MB of egress)
pg_dump "$PGURL" -n public --no-owner --no-privileges -Fc \
  --exclude-table-data=public.media --exclude-table-data=public.library_chunks --exclude-table-data=public.upload_parts \
  -f hq-nonblob-$(date +%F).dump
# b) the full backup, blobs included (~0.35 GB: this counts once against Supabase egress)
pg_dump "$PGURL" -n public --no-owner --no-privileges -Fc -f hq-full-$(date +%F).dump
```

Keep the dump files encrypted at rest (e.g. `age` or an encrypted disk) and off the repo: they contain account emails,
session hashes and the sealed (encrypted) Buffer tokens. Delete them when the reset is confirmed and a week has passed.

**Tested 5 Nov 2026:** dump (a) taken read-only (`--exclude-table-data` for the three blob tables), restored into a local
Postgres 17 with `pg_restore --no-owner --no-privileges` (the only message was the harmless "schema public already
exists"), and compared: 56 public tables, `agent_runs` 46, `users` 6, `library_items` 34 — identical to production.
The dump was then shredded in the sandbox. The full dump (b) was **not** taken by the agent: it would add ~0.35 GB to an
egress budget that is already at 601%, and it would put every user's media on a shared sandbox. Ram takes (b) himself
right before the reset.

## 2. Restore proof (before any change)

```sh
createdb restore_check
psql -d restore_check -c "DROP SCHEMA IF EXISTS public CASCADE"   # the dump recreates it; otherwise --exit-on-error stops there
pg_restore --exit-on-error -d restore_check --no-owner --no-privileges hq-full-YYYY-MM-DD.dump || { echo "restore failed: stop"; exit 1; }
for t in users agent_runs media library_items library_media library_chunks render_jobs approvals; do
  psql -d restore_check -tAc "select '$t', count(*) from $t"
done
psql -d restore_check -tAc "select 'media_bytes', coalesce(sum(length(data)),0) from media"
psql -d restore_check -tAc "select 'chunk_bytes', coalesce(sum(length(data)),0) from library_chunks"
```

Run the same counts and byte sums against production (read-only, `SET default_transaction_read_only = on`). **Every**
line must match, and `pg_restore` must exit 0 (with `--exit-on-error`, after the `DROP SCHEMA` above so "schema public
already exists" can't stop it). Any mismatch or restore error stops the reset.

## 3. What to empty vs keep (proposal)

Keep (schema, people, connections, settings): `users`, `sessions`, `agent_tokens`, `oauth_clients`, `oauth_codes`,
`oauth_refresh`, `brand_kits`, `brand_kit_library`, `ai_settings`, `ai_engine_pref`, `style_rules`, `link_auto_approve`,
`link_consents`, `user_profiles`, `source_settings`, `take_prefs`, `demo_owners`, `user_tombstones`, `admin_audit`.

Candidates to empty (content and test artefacts), each listed with its row count at the time for Ram to tick:

| Table | What it holds | Note |
| --- | --- | --- |
| `media` | rendered images / videos (BYTEA) | the bulk of the 0.376 GB |
| `library_items`, `library_media`, `library_chunks` | Library uploads + generated items | chunks are BYTEA |
| `agent_runs`, `render_jobs`, `thread_ids`, `thread_deleted`, `chat_titles` | runs, threads, render queue | drafts already in Buffer stay in Buffer |
| `source_runs`, `source_cache`, `source_refresh` | scan runs | |
| `approvals`, `confirms`, `mcp_calls`, `mcp_idem` | finished approvals, smoke-token MCP artefacts | keep pending approvals |
| `audit` (older than 30 days), `upload_sessions`, `upload_parts`, `send_codes`, `blob_gc` | logs and leftovers | |

**Order matters (scheduled posts).** `Library.purge` protects media a scheduled Buffer post still uses by looking at
the runs that made it, so: (1) list scheduled posts in Buffer that use `/m/` links and keep their media and runs until
each one publishes or is unscheduled; (2) purge Library items and media through the app (`Library.purge`, which checks
scheduled uses) while `agent_runs` still exists; (3) only then delete the run records whose scheduled uses are resolved.

Smoke-test artefacts (the "Smoke test agent" token's runs, e.g. scan run 35 from 4 Nov) can be removed alone, by
`agent_id`, without touching anyone else's rows. Every delete runs inside one transaction per table group and goes
through the app's own purge paths where they exist (`Library.purge`, `record_owner_deletes`), so `/healthz` keeps
counting them as "deleted by owner", not missing.

## 4. After the deletes: check, then reclaim the space

**Step 1, post-delete check (releases the freeze).** It runs entirely in SQL, read-only, so it works while the web
service is suspended. Before the deletes, snapshot every table in the schema (not a hand-picked list, so nothing in
section 3 is missed) and the scheduled-post media:

```sh
psql "$PGURL" -tAc "select table_name from information_schema.tables where table_schema='public' order by 1" |
  while read t; do psql "$PGURL" -tAc "select '$t', count(*) from \"$t\""; done > counts-before.txt
psql "$PGURL" -tAc "select token from media where token in (<the /m/ tokens of scheduled Buffer posts, from section 3>)" | sort > keep-before.txt
```

and write next to each table the rows the approved statements remove (the same `WHERE` clauses as `SELECT count(*)`).
After the deletes, run the same two commands into `counts-after.txt` / `keep-after.txt`. Pass when:

* every table equals *before − planned removals* (tables marked keep: unchanged), except `audit`, which equals
  *before − planned removals + the audit rows the purge wrote* (`select count(*) from audit where ts >= <freeze start>`:
  `Library.purge` records each purge);
* `keep-after.txt` is identical to `keep-before.txt` (no media a scheduled post uses was removed).

Any difference: stop, keep the freeze, and restore from the section 1 dump. Only when everything matches, resume the web
service, then the worker and cron jobs; `/healthz` (its next media audit) is a later confirmation, not the gate.

**Step 2, reclaim the space.**

`DELETE` frees space for reuse but doesn't shrink the files. To shrink: `VACUUM (FULL, ANALYZE) media;` (and
`library_chunks`) — it takes an exclusive lock for the table's duration (seconds at this size) and needs free disk
equal to the table's live size. Run it in a quiet hour, after the deletes are confirmed.

## 5. Option: move media to R2 instead of deleting

`python -m studio.migrate_media --to r2` (docs/STORAGE.md) copies each blob to an R2 bucket and keeps only the key in the row.
It needs Ram's card on file for Cloudflare (R2's free tier: 10 GB, no egress fees) and the `R2_*` settings on Render.
The migration reads every blob once (~0.27 GB of egress) and then the database shrinks after `VACUUM FULL`. The Egress
guard's disk cache sits above either backend, so nothing else changes.

## 6. Confirming egress is back under quota

* `/healthz` → `egress.db_bytes_today` stays under `db_budget` (130 MB/day ≈ 3.9 GB/month); `/admin` → "Media
  bandwidth" lists the last 14 days.
* Supabase dashboard → Usage → Egress: the daily bars after the deploy of the Egress guard should be a small fraction of
  the days before; the monthly total must stay under 5 GB for the cycle after 5 Dec.
* `pg_stat_statements` (read-only): calls of `SELECT ... substr(data` and `FROM library_chunks` should grow by at most
  one file's worth per file per deploy.

## 7. A separate demo dataset (instead of touching production)

`scripts/demo_seed.py` already fills a local data dir with demo data (no network) for the core app's screenshots and
e2e tests; the Studio's free sample uses committed files (`scripts/build_sample_*.py`). Proposal: a `scripts/studio_seed.py`
that seeds a clean database (local Postgres, or a second free Supabase project) with a small fixed set of demo
channels, runs and images (no videos over 2 MB), so demos and screenshots never depend on the production rows above,
and a reset never removes what a demo needs. Not built in this PR.
