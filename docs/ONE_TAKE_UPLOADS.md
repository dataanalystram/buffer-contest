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


## Faces: local detection, checked in CI, and Jev as a second opinion (4 Nov 2026)

**Local first, on every sampled frame.** The worker samples the take at 2 fps (320 px wide) and runs MediaPipe's Face
Detector (BlazeFace short range); when MediaPipe can't load (no libEGL / libGLES) it uses OpenCV's Haar frontal-face
cascade. Both live in `studio/media/facedetect.py`; the take's `providers.faces` says which one ran.

**No silent failures.** The worker self-check (`python -m studio.media.selfcheck --kind take_analyze`, before every
analyze job) only counts a detector that loads AND finds the face in a committed public-domain portrait
(`tests/studio/fixtures/media/face_armstrong_pd.jpg`, credited in NOTICE). CI's worker-imports `take_analyze` job runs
`python -m studio.media.facedetect --check` in the worker's own venv: both detectors must find a sane face box in the
portrait, none in a blank frame, and agree on where the face is; it prints the median ms per frame. An OpenCV or
MediaPipe upgrade that breaks detection fails CI. (OpenCV stays `<5` on all three opencv-* packages: mediapipe pulls in
`opencv-contrib-python` 5 otherwise, and 5 drops `CascadeClassifier`.)

**Jev (TypeSafe `jev-1.14.0`) as a second opinion, on a few keyframes only** (`studio/media/jev_faces.py`):

* Which frames: the first frame after each scene cut that the local detector didn't cover confidently (`scene_start`),
  then stretches with no local face (`no_local_face`) or only a weak one (`low_confidence`), spread over the take. At
  most `HQ_JEV_FACE_CAP` per take (default 12). Calls run 4 at a time with a 20 s timeout.
* What it asks, per frame, in one request: a Noul "Is a person's face visible in `frame`?" and a Box "the main speaker's
  face in `frame`". A Box always returns a box, so code only uses it when the Noul is a clear yes (>= 0.7) and the box is
  sane; <= 0.3 is "no face"; in between is unsettled and ignored.
* The model judges, the code decides: a Jev face replaces a missing or weak local sample at its keyframe and holds for
  1 s over local-empty samples (never over a confident local face); `crop_plan`'s median + EMA smoothing then applies.
* Images: the frame re-encoded from raw pixels as baseline JPEG (no EXIF / metadata), 280-1568 px per side, sent as
  multipart with `TypeSafe-Danger-Zone-Images: true`.
* Never fails a take: any error, timeout or odd answer is recorded on that frame and the take keeps the local faces
  (centred crop where there are none), logged in the worker output.
* Recorded on the take: `face_jev` in `GET /api/takes/{id}` / `list_clips`: `enabled`, `why`, `calls`, `used`, `errors`,
  `fallback`, and per frame `t`, `why`, `verdict` (face / no_face / unsettled / error), `face_visible`, `box`, `used`, `ms`.
* Model: `jev-1.14.0`, pinned (docs.typesafe.ai/models, 4 Nov 2026: the newest release; `jev-latest` and `jev-preview`
  both point to it, and Box needs Jev 1.14+). `HQ_JEV_MODEL` may name an alias or a newer version; older ones fall back.
* Where it runs in production: `TYPESAFE_API_KEY` is set on the HuhQueue server (Render), not on the video workers. A
  worker (InstaBox, GitHub Actions, or the local one) picks the keyframes, re-encodes them and
  returns them with the analysis; the server checks each one (JPEG, size, re-encoded again), asks Jev, merges the boxes and
  drops the frames before the take is saved. The key never leaves the server and is never logged (errors are scrubbed).
  Every worker does this, even one with the key (and the in-process local worker on the server): only the server
  counts the per-account daily allowance, so only the server asks Jev.
* Free, capped: at most `HQ_JEV_FACE_CAP` keyframes per take (default 12) and `HQ_JEV_DAILY_CAP` keyframes per account
  per day on the server's key (default 60, counted in `jev_usage`; 0 turns it off). Past either cap the take keeps the
  local faces. SubQ is not used for faces (not confirmed free for image questions).
* Who may use it: HuhQueue's key, so it runs for takes a person starts in the app (send `jev: false` to skip it). An MCP
  app gets it only with `jev: true`, which answers `needs_user_approval` (studio/consent.py; retry `clips_from_video` with
  `jev: true` once approved) until the person approves, or when the person turned on "Allow paid APIs" for that app.
* Measured on the portrait (480x600), 4 Nov 2026: one live Jev call returned the face box in ~1.1 s for ~1,190 input
  tokens; local OpenCV Haar ~45 ms (320 px) to ~110 ms (480 px) per frame on the build sandbox (MediaPipe's is in the CI log).

## Threads: reading earlier ones, and deleting a thread (4 Nov 2026)

Every thread has a public id (`thr_` + 128 random bits); tools and URLs use it, never the internal run number.
`list_threads` / `GET /api/threads` and `get_thread` / `GET /api/threads/{thread_id}` let a connected AI app read the
person's earlier threads (own threads only; a foreign id is a 404). `start_post` and `continue_run` answers carry
`thread_id`.

Deleting a thread is like deleting chat history (owner, 4 Nov 23:45): it removes the conversation only and NEVER deletes
media. Takes, clips, uploads, posts and Library files stay. Every thread in the sidebar has a delete button: Make a post
and One take, ready, running, failed or errored. The delete is a row in `thread_deleted`; the run itself is not changed.
Deleted threads stay in the Bin for 7 days (restore brings them back), then the conversation goes for good (messages,
summary, custom title, MCP steps) and the row stays as a tombstone, so the id answers 404 for good. A foreign or missing
id is the same 404. REST: `DELETE /api/threads/{thread_id}` (`?takes=delete` is refused: use `delete_take` for a take),
`POST /api/threads/{thread_id}/restore`, `GET /api/threads/bin`, `POST /api/threads/bin/purge`. MCP: `delete_thread`,
`restore_thread`, `list_thread_bin`, `purge_thread_bin`.
