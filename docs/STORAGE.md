# Media storage: database or R2

## Why
The hosted app runs on Render's free web service (512 MB RAM, no disk) with Supabase's **free Postgres: 500 MB for
everything**. Until this change every image and video was a `bytea` value inside it: generated media in `media.data`,
Library uploads in 1 MB rows of `library_chunks.data` (not Supabase Storage buckets). Media was most of the database.
Cloudflare **R2's free tier holds 10 GB with no egress fees**, so files belong there and Postgres keeps only metadata.

## Backends (`studio/blobstore.py`)
- **`db`**: bytes stay in the row, as before. The default, and the automatic fallback whenever R2 isn't configured.
- **`r2`**: on when all four `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` are set (`R2_ENDPOINT`
  for another S3-compatible store). The **same variables and the same bucket client as One take's uploads**
  (`studio/r2.py`, docs/ONE_TAKE_UPLOADS.md). `HQ_MEDIA_STORAGE=db` keeps new media in the database even then.

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
python -m studio.migrate_media --to r2             # move everything (needs R2_* and the app's DATABASE_URL / SESSION_SECRET)
python -m studio.migrate_media --to r2 --limit 200 # in steps
```
For each row still holding bytes: sha256, PUT to a deterministic key, **GET it back and compare length and sha256**, and
only then one guarded UPDATE that sets the key and nulls `data`. A copy that fails the check leaves the row as it was. Rows
already moved are skipped, so it's idempotent and an interrupted run is finished by the next one. Exit code 1 if any row
failed, 2 if R2 isn't configured.

**Not run on production by the PR.** Ram runs it when ready (order: set the R2 env vars on Render and deploy, so new files
already go to R2; dry run; run; then `VACUUM FULL media, library_chunks;` in the Supabase SQL editor at a quiet moment,
because Postgres only reuses freed space and the 500 MB quota counts the table files).

## Bucket settings
Same bucket as One take is fine (different prefixes: `takes/`, `media/`, `lib/`). Do **not** add a lifecycle rule that
expires `media/` or `lib/`: those are the person's Library and posts. (The `takes/` 7-day rule stays.) No CORS change is
needed for media: the browser never talks to the bucket for it.
