# Database on Supabase

Render's free Postgres is deleted 30 days after it is created, which would land in the middle of Buffer Commit judging
(16 Nov to 7 Dec 2026). The live app keeps its data in a free Supabase Postgres instead.

## Connection string

Use the **Session pooler** string from Supabase › Connect (port 5432). Render has no IPv6, and the direct
`db.<ref>.supabase.co` host is IPv6 only. URL-encode special characters in the password (`@` becomes `%40`).

```
postgresql://postgres.<project-ref>:<password>@aws-<n>-<region>.pooler.supabase.com:5432/postgres
```

The app only needs `DATABASE_URL`. It does not use the Supabase API keys. On start it creates its tables in `public` and
turns on row-level security for each of them, so the Supabase Data API (publishable key) cannot read them. The app
connects as the table owner, which RLS does not restrict.

## Keeping the free tiers awake

Supabase pauses a free project after 7 days without activity, and Render's free web service sleeps after 15 idle
minutes. `/healthz` runs a query, so anything that pings it every 10 minutes keeps both awake. The repo is private, so
the `keepalive` GitHub Actions cron costs Actions minutes; Supabase's own scheduler can do the same for free. Run once in
the Supabase SQL editor (Database › Extensions: enable `pg_cron` and `pg_net` first):

```sql
select cron.schedule('ping-huhqueue', '*/10 * * * *',
  $$ select net.http_get('https://huhqueue-studio.onrender.com/healthz') $$);
```

## Moving to another Supabase project

On 9 Nov 2026 the live database moved to a new free project (Frankfurt, `eu-central-1`, session pooler) after the old
project hit its free egress cap, which old in-database media reads had used up before media moved to R2
([STORAGE.md](STORAGE.md)). The move was a plain `pg_dump` / `pg_restore` of the whole database, then a row count of
every table on both sides, then a new `DATABASE_URL` on Render and the `ping-huhqueue` cron job above on the new project.
The old project stays frozen as a rollback copy.

Use `pg_dump` / `pg_restore` for any move, with writes stopped first: stop new video jobs from starting (pause the AI and Video link
ingest kill switches in /admin, and start no takes or renders), wait for running jobs to report back, then suspend the Render web service and dump, and switch `DATABASE_URL` before you resume. The dump carries the paused switches over, so after the new database is live set the AI and Video link ingest switches in /admin back to how they were before the move. A
dump is a snapshot, so anything written after it starts is not copied, and matching row counts do not prove that
updated rows match. The old project is a rollback copy only: it misses everything written after the cutover, so
switching back loses those writes. To go back without losing them, dump the new project the same way and restore it into a fresh, empty project (not over the old copy, which already has the tables and rows, so a plain `pg_restore` would hit existing objects and duplicate keys); switch `DATABASE_URL` only after that restore finishes with no errors. A free Supabase organization can have only two
active free projects (paused ones don't count), so pause one you don't need first if there is no free slot. Once the
move is confirmed, delete the frozen old project (the privacy policy says so), never the one `DATABASE_URL` points at. `MIGRATE_FROM_URL` (`Store.copy_from`) copies only the core tables in
`store.TABLES`, not the tables other modules create (Library, takes, threads, templates and more), so it is not a
full copy.
