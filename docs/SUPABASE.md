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

## Moving the data from Render (one deploy)

1. In the Render service's Environment, add `MIGRATE_FROM_URL` = the current `DATABASE_URL` value (the Render database).
2. Set `DATABASE_URL` = the Supabase Session pooler URL. Save, which redeploys.
3. The deploy log shows `migrate: copied {...}` with row counts. Users stay signed in to Buffer and agent tokens keep
   working, because `SESSION_SECRET` is unchanged and the encrypted tokens move as they are.
4. Remove `MIGRATE_FROM_URL`. The copy only runs into an empty database, so leaving it set is harmless, but the Render
   database is going away.

## Keeping the free tiers awake

Supabase pauses a free project after 7 days without activity, and Render's free web service sleeps after 15 idle
minutes. `/healthz` runs a query, so anything that pings it every 10 minutes keeps both awake. The repo is private, so
the `keepalive` GitHub Actions cron costs Actions minutes; Supabase's own scheduler can do the same for free. Run once in
the Supabase SQL editor (Database › Extensions: enable `pg_cron` and `pg_net` first):

```sql
select cron.schedule('ping-huhqueue', '*/10 * * * *',
  $$ select net.http_get('https://huhqueue-studio.onrender.com/healthz') $$);
```
