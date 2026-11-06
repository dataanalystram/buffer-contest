# InstaBox render worker (2 Nov 2026)

Status: **owner-approved, merged; the live app runs it** (`HQ_RENDER_BACKEND=instabox`, machine `huhqueue-worker`).
Without that variable nothing changes. When InstaBox fails, times out (`HQ_INSTABOX_WAKE_S`, default 60 s, or the
exec call) or answers non-200, the app starts the GitHub Actions run instead (needs `GH_DISPATCH_TOKEN`); the Actions
claim replaces the job token the box was given, so a late box job can't read or write that job.

## What InstaBox is (docs: https://instabox.sh/llms.txt, /docs/pricing.md, /docs/limits.md, /docs/billing.md)
- A Firecracker microVM per machine: Debian 13, **4 vCPU / 8 GiB** (the only free size), 16 GiB root, **20 GiB
  persistent `/data`**, swap. No GPU. Python 3.13, passwordless sudo/apt (FFmpeg 7.1.5 from apt in ~8 s).
- Persistent: `/data` and the root disk survive pause and stop/start (checked: venv, models and `/usr/bin/ffmpeg`
  still there after stop → start).
- Lifecycle by API (`X-API-Key`, `Idempotency-Key` on lifecycle calls): create, start, stop, suspend, resume,
  delete (`X-Confirm-Delete`), `exec` (10 min per call; long jobs run detached with `nohup`), file PUT/GET.
- Measured: create → ready **5 s**; pause 1 s; **resume 1 s**; stop 3 s; **cold start 3 s**.
- Egress: reached YouTube, X, api.buffer.com, Hugging Face, Render, GitHub and PyPI. **YouTube downloads are
  blocked** from its IPs ("Sign in to confirm you're not a bot"), same class of block as GitHub runners, so the
  existing yt_fallback / upload path still applies. Outbound capped at ~50 Mbit/s per VM (a 50 MB Reel ≈ 8 s).
- Free plan: **a free sandbox is never billed** ($0.00/month); 100 GiB egress/month; idle pause fixed at 30 min;
  max run 480 min then a 5–20 min cooldown; machine limit 1 (early accounts keep 5 until 1 Dec 2026, after which
  only one free sandbox *runs* at a time; the others stay paused). Paid plans: Pro $20/mo incl. $20 credit;
  usage $0.004/vCPU-h + $0.00225/GiB-h → a 4/8 box ≈ **$0.034/hour**.

## Proof run (2 Nov 2026, throwaway machine, deleted afterwards)
| Step | InstaBox 4 vCPU (warm) | GitHub Actions (ubuntu-latest, recent runs) |
|---|---|---|
| Setup per job | 0 s (deps on /data); first-time setup 31 s | ~45–50 s every run (apt ffmpeg 18–25 s, pip 15–24 s) |
| One take analyze, 194 s source (faster-whisper small, VAD, scenes, faces) | **22.6 s** (incl. model download) | n/a (last 2 take runs failed early) |
| One take analyze, 19.4 min source | **106 s** (~11× real time) | — |
| Branded 58 s 9:16 Reel (`render.brand_video`, hook + band + end card) | **21.1 s** | render job step 107 s (run 41441478001, different job) |

Projected: a 60-min video ≈ 5.5 min to transcribe, far inside the 480-min run cap.

## Dispatch flow (studio/instabox_worker.py)
1. App mints the job token (`House.issue_job_token`), same single-job token an OIDC claim gives.
2. Resume/start `HQ_INSTABOX_MACHINE`, wait for ready (~1–3 s).
3. One `exec`: `git fetch` `HQ_INSTABOX_REF` into `/data/hq`, then `HQ_LOCK=/data/hq.lock python -m studio.render_worker`
   detached (the worker takes the one-job lock itself, after its heartbeat starts). The token goes in **stdin**, never
   in the command (exec commands are kept in machine history).
4. Worker posts results back to the app as today (the app's media store); the box pauses itself after 30 idle min.

## Failures never hang a job (2 Nov 2026, after job 15)
Job 15 (source `https://example.com/not-a-video`, 404) left a traceback in `/data/logs/job-15.log` and the job could
stay "running". Now, on both the InstaBox and the GitHub Actions path:
- **Any exception** in the worker (`studio/render_worker.py`, `main`) posts `{"status": "failed", "error": <short human
  message>, "log_tail": <last ~4 KB of the worker's output, job tokens redacted>}` to `POST /api/worker/jobs/{id}` with
  the job's own token (3 tries, so a sleeping Render instance still gets it), then exits 1. Messages look like
  "Couldn't download the video: the link returned 404". The app shows the message on the run/take step and keeps the
  log tail on the job (`render_jobs.result.log_tail`, server side) and in the app log.
- **Heartbeat**: the worker pings `POST /api/worker/jobs/{id}/heartbeat` every `HQ_HEARTBEAT_S` (60 s) while it runs.
- **Watchdog** (app): a worker job (one handed a job token) with no heartbeat for `HQ_JOB_HEARTBEAT_MIN` (10) minutes
  is failed ("The video worker stopped responding ..."), its token burned, the run/take marked failed. It runs every
  `HQ_WATCHDOG_S` (60 s; 0 = off) in the app and, throttled, whenever a run or take is read. This covers what the worker
  can't report itself: OOM kill, box paused, runner cancelled, a claim that never happened.

## The box: `huhqueue-worker` (created 2 Nov 2026, template agent-browser, free 4 vCPU / 8 GiB)
A dedicated machine; the old `huhqueue` machine (the self-hosted HuhQueue) is not used or touched.
The repo is public (since 2 Nov 2026), so the box clones and fetches over plain HTTPS with no credential at all;
the read-only deploy key it used while the repo was private was deleted the same day. No GitHub token or key is stored on the box.
```
sudo apt-get update && sudo apt-get install -y ffmpeg libegl1 libgles2 git
git clone --depth 1 https://github.com/dataanalystram/buffer-contest.git /data/hq
python3 -m venv /data/venv && /data/venv/bin/pip install -r /data/hq/requirements-worker.txt -r /data/hq/requirements-media.txt
/data/venv/bin/python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
```
Jobs log to `/data/logs/job-<id>.log` on the box.

**Tesseract OCR (5 Nov 2026).** Debian 13's `tesseract-ocr` is 5.5.0, below the 5.5.4 HuhQueue requires, so the box
builds the pinned upstream tag once: every job's exec command checks `/data/tesseract/bin/tesseract --version` and, when
it isn't `tesseract 5.5.4`, starts `scripts/worker_tesseract.sh` in the background (copied to `/data` first, `flock -n`,
`nice -n 19`, log `/data/logs/tesseract-setup.log`). The script apt-installs the build tools and `libleptonica-dev`,
clones tag 5.5.4 and refuses to build unless it is commit `7e548e6e…`, builds a static binary into `/data/tesseract`
(persistent disk), and fetches `eng.traineddata` from tessdata_fast with a SHA-256 check. Jobs keep running meanwhile;
OCR is off (fail open) until the binary is there. Read-only check: `/data/venv/bin/python -m studio.media.ocr` prints
`{"ok": true, "version": "5.5.4", ...}`; each take_render job log has an `ocr:` line.
App env on Render: `HQ_RENDER_BACKEND=instabox`, `INSTABOX_API_KEY` (secret), `HQ_INSTABOX_MACHINE`, `HQ_INSTABOX_REF`.
No provider key is ever put on the box: Groq ASR and the Smart layout vision picker are called by the app with its own
`GROQ_API_KEY` / `NVIDIA_API_KEY` / `OPENROUTER_API_KEY`, on the job's token, under the free-tier ledger
(`POST /api/worker/jobs/{id}/asr` and `/vision`, studio/free_proxy.py). The exec command passes `HQ_LAYOUT_VISION`
(the app's value, default `shadow`; anything else but off/shadow/on becomes off).

## Cost per video
Free plan: **$0**. On Pro (if ever needed): ~1–2 min of a 4/8 box per Reel ≈ $0.001; a 60-min One take
(~6 min analyze + renders) ≈ $0.005–0.01, plus idle time until auto-pause (≤30 min ≈ $0.017).

## Notes
- Free plan after 1 Dec 2026: only one free machine *runs* at a time (paused ones are kept). The worker box
  resumes per job and pauses after 30 idle minutes; if another free box is running then, a resume can fail and the
  job falls back to GitHub Actions.
- Checked 2 Nov 2026: `GET /v1/machines/{id}/history` (`instabox history`) keeps the command string and byte counts,
  **not the exec stdin**, so the job token (stdin) is not in the machine's history; the command string has no secret.
- Fallback to GitHub Actions: done (`House._dispatch_github`, tests in tests/studio/test_instabox_worker.py).
