# Media storage: database or R2

## Why
The hosted app runs on Render's free web service (512 MB RAM, no disk) with Supabase's **free Postgres: 500 MB for
everything**. Until this change every image and video was a `bytea` value inside it: generated media in `media.data`,
Library uploads in 1 MB rows of `library_chunks.data` (not Supabase Storage buckets). Media was most of the database.
Cloudflare **R2's free tier holds 10 GB with no egress fees**, so files belong there and Postgres keeps only metadata.

## Backends (`studio/blobstore.py`)
- **`db`**: bytes stay in the row, as before. The default, and the automatic fallback whenever R2 isn't configured.
- **`r2`**: on when all four `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` are set.
  HuhQueue's bucket (`huhqueue-media`, private) is in the **EU jurisdiction**, so also set `R2_JURISDICTION=eu`: the S3
  host is then `https://<R2_ACCOUNT_ID>.eu.r2.cloudflarestorage.com` (the default non-EU host does not serve an EU
  bucket). `R2_ENDPOINT` overrides the host entirely (https only); an unknown jurisdiction or a plain-http endpoint
  turns R2 off rather than guessing. The **same variables and the same bucket client as One take's uploads**
  (`studio/r2.py`, docs/ONE_TAKE_UPLOADS.md). `HQ_MEDIA_STORAGE=db` keeps *new* media in the database even then; files
  already in R2 are still read from it while the `R2_*` variables are set (so never remove those while R2 holds files).

With R2 on, a new media row or Library chunk keeps `data` NULL and gets `storage_key` (+ `sha256`):
`media/<owner>/<random>` or `lib/<owner>/<random>`, where `<owner>` is a hash of the account id (never the id) and
`<random>` is 32 random URL-safe characters. The bucket stays **private**: files are still served by this app at
`/m/<token>`, `/ms/<exp>/<sig>/<token>` (signed, 15 min) and `/api/library/...`, with every ownership check as before;
the app reads the object (whole, or the 1 MB chunks a Range needs). `studio/r2.py` can also issue short-lived presigned GETs.

If the bucket refuses or is down when a file is written, that file stays in the database (logged), so uploads never fail
because R2 did. A read of an R2 object that fails answers 503.

## Deleting
A delete never removes an object inline. `blobstore.release()` copies the rows' keys into `blob_gc` and deletes the rows in
the same transaction; `blobstore.sweep()` deletes the objects after the commit (right after a purge, on a background
thread, and every minute from the job watchdog), and a key leaves `blob_gc` only when the bucket confirms. A rolled-back
purge therefore can't lose bytes, and a failed delete is retried. Account deletion (self-service or admin) does the same
for every file the account had (docs/ADMIN.md).

## Moving existing files (`python -m studio.migrate_media`)
```bash
python -m studio.migrate_media --to r2 --dry-run   # rows and bytes that would move; touches nothing
python -m studio.migrate_media --to r2 --report --real-email you@example.com  # row by row: size, owner hash, demo/real/other
python -m studio.migrate_media --to r2             # COPY everything: rows take the key, keep their BYTEA (needs R2_*)
python -m studio.migrate_media --to r2 --limit 200 # in steps
python -m studio.migrate_media --to r2 --cleanup --i-have-a-verified-backup   # later, with Ram's OK: drop DB copies
```
For each row still holding bytes: sha256, PUT to a fresh random key, **GET it back and compare length and sha256**, and
only then one guarded UPDATE that sets the key (and, only with `--drop-db-copy --i-have-a-verified-backup`, nulls
`data`). By default the copy is **copy-only**: the BYTEA stays as a second copy, reads switch to R2 (so the database copy
stops leaving Postgres), and with R2 switched off the database copy serves again. `--cleanup` re-downloads each object
and drops a row's database copy only when its sha256 still matches; both ways of dropping refuse to run without
`--i-have-a-verified-backup` (exit 2). A copy that fails the check leaves the row as it was (that
attempt's object is queued for deletion; the next attempt uses a new key). Rows already moved are skipped, so it's
idempotent and an interrupted run is finished by the next one. Chunks of an upload still arriving are skipped. Exit code 1
if any row failed, 2 if R2 isn't configured. The sweep never deletes a key that any row still references.

**Not run on production by the PR.** Ram runs it when ready (order: set the R2 env vars on Render and deploy, so new files
already go to R2; dry run; run; then `VACUUM FULL media, library_chunks;` in the Supabase SQL editor at a quiet moment,
because Postgres only reuses freed space and the 500 MB quota counts the table files).

## Bucket settings
Same bucket as One take is fine (different prefixes: `takes/`, `media/`, `lib/`). Do **not** add a lifecycle rule that
expires `media/` or `lib/`: those are the person's Library and posts. `scripts/r2_bucket_setup.py` prints the CORS
(our `PUBLIC_URL` origin only, no wildcard) and lifecycle (abort incomplete multipart after 1 day, `tmp/` after 7 days)
documents and sends nothing unless `--apply` is given.

## Browser redirect (`HQ_MEDIA_REDIRECT=1`, off by default)
With R2 on and this flag set, a browser's own fetch of `/m/<token>` (it sends `Sec-Fetch-*`) for a file in R2 gets a
302 to a presigned GET that lives **at most 15 minutes**, with `Cache-Control: no-store` and `Referrer-Policy:
no-referrer`. The token is resolved first (missing, binned or foreign ids answer 404 and nothing is signed), an ETag
match still answers 304, and the repeat-download limit applies to redirects too. Buffer, Instagram and the render
worker (no `Sec-Fetch-*`) and every HEAD always get the bytes from `/m/` itself: a scheduled post's link must work
whenever Buffer fetches it, and a presigned URL expires.

## R2 free-tier caps (`studio/r2usage.py`)
R2 must never cost money. The free tier is 10 GB-month stored, 1M Class A (writes) and 10M Class B (reads) operations a
month; the code stops 20% short of each:

| Setting | Default | Past it |
|---|---|---|
| `HQ_R2_MAX_BYTES` | 8 GB stored (rows with a `storage_key`, plus open/ready One take uploads) | new media stays in the database; migration stops; large uploads answer 507 |
| `HQ_R2_MAX_CLASS_A` | 800,000 a month (UTC) | no new R2 writes (database fallback); a rewrite of an R2 object (GPS blanking) fails closed with 503 |
| `HQ_R2_MAX_CLASS_B` | 8,000,000 a month (UTC) | reads use the row's database copy when it has one, else a clear 503 until the month turns; no redirects |

Every SigV4 signature counts as one operation of its class (deletes are free). Counts sit in the `r2_usage` table (one
row per month and class, three months kept, row-level security on) and are written in batches, never inside another
transaction. `/admin` shows stored bytes and both operation counts against their caps, with a warning once a cap is hit.

## Failure behaviour (PR #110 review)
- Every bucket call has a 5 s connect and 20 s overall timeout. Streamed Library uploads write chunks from the thread
  pool, never on the event loop, so a slow bucket can't stall other requests or `/healthz`.
- The video GPS check fails closed: if storage can't be read (or rewritten) while a video's location is blanked, the
  upload is refused with a 503 and nothing is kept or published.
- A whole-file Library download reads its first chunk before the headers go out, so a storage outage is a clean 503.
- Deletes (Library purge, account deletion, One take Bin purge) note keys in `blob_gc`; the background sweep is scheduled
  for after the transaction commits. A new object whose row's transaction rolls back is discarded.

## Serving media without the whole file in memory (5 Nov 2026)

Render killed the free 512 MB instance twice on 5 Nov 2026 (08:10 and 11:31 GMT) and then suspended the workspace for
"Monthly Quota Exceeded": the Hobby plan's 5 GB of outbound bandwidth (5.44 GB of HTTP answers, 0.61 GB
service-initiated, 1-5 Nov). The serving path now:

- `/m/<token>`, `/ms/...` and a rendered Library file (`/api/library/{id}/file` for a post's Reel or carousel) use
  `library.media_file`: a Range reads only those bytes (`substr(data, …)` for rows kept in the database, a ranged GET for
  R2 objects), a range answer is capped at 8 MB, and a whole answer streams in 1 MB pieces. HEAD takes the length from the
  row or SQL `length(data)`. No request loads a video whole.
- Every media answer carries a strong `ETag` (the row's sha256, else its token and length); `If-None-Match` answers 304
  with no body, so a browser or Buffer that already has the file downloads nothing.
- Text answers (HTML, JS, CSS, JSON, SVG) of 1 KB-4 MB with a declared length are gzipped (`studio/gzipmw.py`); media,
  Range answers, streams and HEAD pass through byte for byte. A gzipped answer's ETag is weakened (`W/`).
- The run and take polls (`house.js watch`, `take.js watchTake`) wait at least 30 s while the tab is hidden and catch up
  when it is shown again (`HQLoad.pollMs` / `HQLoad.wake`).

## Egress guard: each file leaves the database once (5 Nov 2026)

**Why.** Supabase's free plan allows 5 GB of egress a month; the dashboard showed 30.07 GB (601%) while Render sent only
~6 GB to clients. The gap was amplification between this server and Postgres: before #118 every Range answer read the
whole `media.data` BYTEA (a video player sends 10–20 Range requests per view, so a 7–26 MB video left the database
10–20 times to send a few MB), and `House.get_media` read whole files again for every render, Buffer push, edit and
logo (2,771 whole-file reads in 6 days, from `pg_stat_statements`). The database is also at 75% of its 0.5 GB, so
nothing here adds rows or blobs to it.

**Layers** (the interface every reader goes through):

| Layer | Module | Holds |
| --- | --- | --- |
| Reference + metadata | `media`, `library_items`, `library_chunks` rows | token, owner, MIME, size, sha256, storage_key |
| Bytes backend | `studio/blobstore.py` | `db` (BYTEA, today) or `r2` (S3-compatible, off until R2 is set up) |
| Read-through disk cache | `studio/mediacache.py` | one copy per file on the instance's disk |

`library.media_file()` and `Library._file_of()` return a ranged reader (`mime`, `size`, `etag`, `read(s, e)`,
`iter()`); both are wrapped by `mediacache.wrap()`. The first read of any range copies the whole file from the
backend once (1 MB pieces, never whole in memory), atomically (temp file + rename, one fill per file at a time);
every later read, ranged or whole, is a local file read. `House.get_media` (renders, Buffer pushes, image edits, logos)
and `Library.blob` read through the same cache, so internal callers no longer re-download either. The render worker
(GitHub Actions / InstaBox) has no database access: it fetches `/m/` over HTTPS, which is served from this cache.

* Key: the token plus the ETag (sha256 when the row has one), so a changed file never gets the old copy.
* Ownership first: routes resolve the row and refuse foreign or missing ids (404) before asking for bytes; the cache
  is never a way in. A permanent delete (`record_owner_deletes`) removes the copy at once.
* `HQ_MEDIA_CACHE_MB` (default 1024; 0 = off), `HQ_MEDIA_CACHE_DIR` (default the temp dir). LRU eviction; a file over a
  quarter of the cap is served without a copy. The cache lives on the Store object; Render's disk is ephemeral, so a
  deploy starts empty (each file is read once again per deploy).
* Switching on R2 later needs no code change here: `blobstore` reads the object by range and the cache sits above it.

**HTTP.** `/m/<token>`: `Cache-Control: private, max-age=31536000, immutable` (a token's bytes never change) with a
strong ETag; `If-None-Match` → 304; `Range` → 206 with `Content-Range` (max 8 MB per answer); `If-Range` applies the
range only for the current ETag (otherwise the whole file, 200). Library files by id: `private, no-cache` + ETag (an id
can point at new files after a re-render, so the browser revalidates; the 304 costs no bytes).

**Meter and budget** (`studio/egress.py`). Per UTC day: media bytes read from the backend (`db`) and media bytes sent
(`served`). Budgets `HQ_EGRESS_DB_DAILY_MB` (130) and `HQ_EGRESS_SERVE_DAILY_MB` (150) keep a month under ~4 GB and
~4.5 GB. At 80% one warning line is logged; at 100% a *browser* fetch (Sec-Fetch headers) of media that isn't the
signed-in viewer's own answers 429 + Retry-After until midnight UTC. Never refused: the owner, Buffer / Instagram /
the render worker (no Sec-Fetch headers), HEAD and 304. One browser client that was already sent more than 3x a file's
size of the same file within `HQ_EGRESS_REPEAT_S` (600 s) gets 429 for that file (owner exempt). The day's totals and 14
daily summaries are kept in a small JSON file on the instance disk (`HQ_EGRESS_STATE`); at each UTC midnight a
`egress daily …` summary line is logged. Numbers only: `/healthz` → `egress`, admin → `GET /api/admin/egress` and the
"Media bandwidth" section of `/admin`.

**Not covered by the cache** (text, not BYTEA): list and poll queries that read `agent_runs.result` (~15 KB a row), e.g.
the Library list's runs read and the threads list, measured at ~0.5 GB in 6 days. Next step: select only the fields the
lists show (a follow-up). `/healthz` now pings the database at most every 15 s (`HQ_HEALTH_PING_S`; 124k pings in
6 days before).
