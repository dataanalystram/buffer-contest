# One take: long video uploads (4 Nov 2026)

## Why One take stopped at ~1500 MB (and really at 40 MB for files)
- **File uploads:** the One take screen refused files over 40 MB in the browser (`take.js`), then sent the file as ONE
  base64 JSON body to `/api/media` (56 MB body cap in `limits.py`, `MAX_UPLOAD` 40 MB in `house.py`), decoded in memory and
  stored as a database row. The hosted app is a free Render web service (512 MB RAM, **no disk**: Render API shows
  `plan: free, disk: null`) on Supabase's free Postgres (500 MB in all), so files can't simply be made bigger there.
- **Links:** a pasted link was streamed by the worker but refused past `HQ_MAX_TAKE_BYTES`, 1500 MB by default
  (`takes.py`, `render_worker.py`): the "~1500 MB" limit.

## What it does now
- **With a bucket (recommended): up to 4 GB** (`HQ_TAKE_UPLOAD_MAX_MB`, default 4096). The browser uploads 16 MiB parts
  straight to Cloudflare R2 with presigned URLs (S3 multipart, SigV4); the web instance only signs URLs and, on complete,
  checks every part's size and ETag against the bucket's own part list, the object's length and that it starts like a video
  (MP4/MOV/WebM). A dropped connection resumes: opening the same file again returns the parts already stored. The worker
  downloads the source from a presigned GET (6 h), streamed and size-capped. Why 4 GB: R2's free tier holds 10 GB, and a
  GitHub Actions runner has ~14 GB of disk for the source, its 16 kHz WAV for Whisper and the renders.
- **Without a bucket:** One take's file uploads go through the raw Library upload (`/api/library/upload`, no base64):
  **up to 100 MB**, and the screen says so. Links go up to the same 4 GB cap on the worker.

## Setting up R2 (Ram, once; HuhQueue never creates buckets or keys)
1. Cloudflare dashboard -> R2 -> Create bucket (e.g. `huhqueue-takes`).
2. R2 -> Manage API tokens -> Create token with **Object Read & Write** on that bucket only. Note the Access Key ID and Secret.
3. Bucket -> Settings -> **CORS policy** (the browser PUTs parts directly and must read the ETag):
   ```json
   [{"AllowedOrigins": ["https://huhqueue-studio.onrender.com"], "AllowedMethods": ["PUT", "GET", "HEAD"],
     "AllowedHeaders": ["*"], "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
   ```
4. Bucket -> Settings -> **Object lifecycle rules**: delete objects with prefix `takes/` after **7 days**, and abort
   incomplete multipart uploads after **1 day**. (The app also deletes a take's source when the take is purged from the Bin,
   and unfinished / unused uploads after a day; the lifecycle rule is the backstop.)
5. Render -> huhqueue-studio -> Environment: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
   (`R2_ENDPOINT` points at another S3-compatible store instead.)

Note: re-rendering a clip (trim, transcript, hook) downloads the source again, so after 7 days a take's clips can't be
re-rendered any more; its rendered clips stay.

## REST
`GET /api/takes/uploads/config` · `POST /api/takes/uploads` {name, size, content_type} · `POST /api/takes/uploads/{id}/parts`
{parts:[1,2,...]} · `GET /api/takes/uploads/{id}` · `POST /api/takes/uploads/{id}/complete` {parts:[{part_number, etag}]} ·
`DELETE /api/takes/uploads/{id}` · then `POST /api/takes` {upload_id, owns: true}. Someone else's upload and a missing one
are the same 404.

## Shared with media storage
The same `R2_*` variables and the same signer (`studio/r2.py`) also move new images and videos out of the database once
set (docs/STORAGE.md). Keep the `takes/` 7-day lifecycle rule scoped to that prefix: `media/` and `lib/` must never expire.
