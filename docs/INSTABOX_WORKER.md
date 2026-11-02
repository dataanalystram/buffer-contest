# InstaBox render worker (evaluation + opt-in sketch, 2 Nov 2026)

Status: **sketch, not merged, off by default.** `HQ_RENDER_BACKEND=instabox` routes a render/take job to a warm
InstaBox VM instead of a GitHub Actions run. Without it nothing changes.

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
3. One `exec`: `git fetch` `HQ_INSTABOX_REF` into `/data/hq`, then `flock /data/hq.lock python -m studio.render_worker`
   detached. The token goes in **stdin**, never in the command (exec commands are kept in machine history).
4. Worker posts results back to the app as today (the app's media store); the box pauses itself after 30 idle min.

## One-time box setup
```
sudo apt-get update && sudo apt-get install -y ffmpeg
git clone <repo> /data/hq            # read access via an InstaBox Org Secret for github.com (no token on disk)
python3 -m venv /data/venv && /data/venv/bin/pip install -r /data/hq/requirements-worker.txt -r /data/hq/requirements-media.txt
```
App env on Render: `HQ_RENDER_BACKEND=instabox`, `INSTABOX_API_KEY` (secret), `HQ_INSTABOX_MACHINE`, `HQ_INSTABOX_REF`.

## Cost per video
Free plan: **$0**. On Pro (if ever needed): ~1–2 min of a 4/8 box per Reel ≈ $0.001; a 60-min One take
(~6 min analyze + renders) ≈ $0.005–0.01, plus idle time until auto-pause (≤30 min ≈ $0.017).

## Open items before turning it on
- Which machine: free plan allows one *running* sandbox after 1 Dec; repurpose an existing paused machine or
  delete unused ones (owner decision).
- Does `exec` stdin show up in `instabox history`? Not verified; command string confirmed secret-free.
- Fallback: keep GitHub Actions dispatch when InstaBox answers non-200 (one-line change in `_dispatch_github`).
