"""Optional render backend: run a job on an InstaBox machine instead of a GitHub Actions run (sketch, opt-in).

Why: the GitHub runner reinstalls FFmpeg + pip on every run (~45 s), is a 2-vCPU box, and uses private-repo Actions
minutes. An InstaBox machine (Firecracker VM, 4 vCPU / 8 GiB, persistent /data with the venv and the Whisper models
already in place) resumes in ~1 s, so a job starts at once. Free plan: the machine is never billed; it pauses itself
after 30 idle minutes. Proof (2 Nov 2026): 194 s source -> take.analyze (faster-whisper small) 22.6 s; 19.4 min
source -> 106 s; 58 s branded Reel (render.brand_video) 21.1 s. docs/INSTABOX_WORKER.md has the details.

Flow, per job:
  1. the app mints the job's token itself (House.issue_job_token, the same token an OIDC claim hands out),
  2. resumes / starts the machine HQ_INSTABOX_MACHINE (POST .../resume or .../start, Idempotency-Key),
  3. one exec: update the checkout to HQ_INSTABOX_REF, then start `python -m studio.render_worker` in the background;
     the worker takes the box lock (HQ_LOCK, one job at a time) after its heartbeat starts.
     The token travels in the exec's stdin, never in the command
     string (exec commands are kept in the machine's history) and never in a dispatch input,
  4. the worker posts its result back to the app exactly as on GitHub Actions; the box then idles and pauses itself.

Env (all on the web app):
  HQ_RENDER_BACKEND=instabox     opt in (default: GitHub Actions, unchanged)
  INSTABOX_API_KEY               an ibk_ key with machines:write (Render secret; never logged)
  HQ_INSTABOX_MACHINE            the machine id (m_...)
  HQ_INSTABOX_REF                git ref the box runs (default main)
  HQ_INSTABOX_WAKE_S             seconds to wait for the box to be ready (default 60)
Fallback: when InstaBox fails, times out or answers non-200, House._dispatch_github starts the GitHub Actions run
instead (if GH_DISPATCH_TOKEN is set); its open_claim replaces the box's job token, so a late box job can't act.
One-time box setup (docs/INSTABOX_WORKER.md): /data/hq checkout (read access through an InstaBox Org Secret for
github.com, so no GitHub token is stored on the box), apt ffmpeg, /data/venv with requirements-worker*.txt.
"""
from __future__ import annotations

import os
import shlex
import uuid

import httpx

API = "https://api.instabox.sh/v1"


def enabled() -> bool:
    return os.environ.get("HQ_RENDER_BACKEND", "").lower() == "instabox" and bool(os.environ.get("INSTABOX_API_KEY"))


def _h(lifecycle: bool = False) -> dict:
    h = {"X-API-Key": os.environ["INSTABOX_API_KEY"], "Content-Type": "application/json"}
    if lifecycle:
        h["Idempotency-Key"] = str(uuid.uuid4())
    return h


def wake(http: httpx.Client, mid: str) -> tuple[bool, str]:
    """Running stays running; paused resumes (~1 s); stopped starts (~3 s). Waits until the guest is ready."""
    import time
    m = http.get(f"{API}/machines/{mid}", headers=_h())
    if m.status_code != 200:
        return False, f"InstaBox answered {m.status_code}"
    state = m.json().get("state")
    if state in ("suspended", "suspending"):
        http.post(f"{API}/machines/{mid}/resume", headers=_h(True), json={})
    elif state in ("stopped", "stopping"):
        http.post(f"{API}/machines/{mid}/start", headers=_h(True), json={})
    wait = int(os.environ.get("HQ_INSTABOX_WAKE_S", "60"))  # past this the app falls back to GitHub Actions
    for _ in range(max(1, wait // 2)):
        j = http.get(f"{API}/machines/{mid}", headers=_h()).json()
        if j.get("state") == "running" and (j.get("runtime") or {}).get("ready"):
            return True, "ready"
        if j.get("state") == "error":
            return False, "machine in error"
        time.sleep(2)
    return False, f"machine not ready in {wait} s"


TESSERACT_TAG = "5.5.4"               # scripts/worker_tesseract.sh builds this tag (kept equal by a test)
TESSERACT_PREFIX = "/data/tesseract"


def layout_vision() -> str:
    """The Smart layout mode the box runs (the app's HQ_LAYOUT_VISION, default shadow like take.yml). Frames only ever
    leave when the take's owner turned Smart layout on, and then through the app (studio/free_proxy.py)."""
    m = os.environ.get("HQ_LAYOUT_VISION", "shadow").strip().lower()
    return m if m in ("off", "shadow", "on") else "off"


def command(jid: int, app_url: str, kind: str = "", ref: str = "main") -> str:
    """The exec command. Reads the job token from stdin; nothing secret is in this string."""
    # HQ_LOCK: the worker takes the box's one-job-at-a-time lock itself, after its heartbeat starts, so a job waiting its
    # turn behind a long One take still looks alive to the app's watchdog (House.sweep_stale_jobs)
    env = f"HQ_JOB_ID={int(jid)} HQ_APP={shlex.quote(app_url)} HQ_LOCK=/data/hq.lock HQ_LAYOUT_VISION={layout_vision()}"
    if kind:
        env += f" HQ_KIND={shlex.quote(kind)}"
    head = ("set -e; IFS= read -r JOB_TOKEN; export JOB_TOKEN; cd /data/hq; "
            f"git fetch -q --depth 1 origin {shlex.quote(ref)} && git checkout -q -f FETCH_HEAD; "
            "mkdir -p /data/logs; "
            # Tesseract OCR >= 5.5.4 (One take on-screen text): built once from the pinned tag in the background, at nice 19,
            # under a non-blocking lock; OCR stays off (fail open) until it's there (scripts/worker_tesseract.sh)
            f"{{ {TESSERACT_PREFIX}/bin/tesseract --version 2>&1 | head -1 | grep -qx 'tesseract {TESSERACT_TAG}' && "
            f"[ -s {TESSERACT_PREFIX}/share/tessdata/eng.traineddata ]; }} || "
            "(cp scripts/worker_tesseract.sh /data/tesseract-setup.sh && nohup flock -n /data/tesseract.lock nice -n 19 "
            "sh /data/tesseract-setup.sh > /data/logs/tesseract-setup.log 2>&1 &); ")
    log = f"/data/logs/job-{int(jid)}.log"
    worker = f"-m studio.render_worker --job {int(jid)} --app {shlex.quote(app_url)}"
    if kind == "image_matte":
        # the ML cut-out runs in its own venv (rembg needs numpy>=2.3; the video venv's MediaPipe must not be upgraded),
        # created and kept in step with requirements-matte.txt in the background (pip is a no-op once installed), under
        # a lock so two cut-outs never install at once; model weights live on the box's disk (U2NET_HOME)
        inner = ("[ -x /data/venv-matte/bin/python ] || python3 -m venv /data/venv-matte; "
                 "flock /data/pip-matte.lock /data/venv-matte/bin/pip install -q -r requirements-worker-core.txt "
                 "-r requirements-matte.txt && "
                 f"exec env {env} U2NET_HOME=/data/u2net /data/venv-matte/bin/python {worker}")
        return head + f"(nohup sh -c {shlex.quote(inner)} > {log} 2>&1 &); echo started"
    return head + f"(nohup env {env} /data/venv/bin/python {worker} > {log} 2>&1 &); echo started"


def dispatch(house, jid: int, workflow: str = "render.yml", inputs: dict | None = None, ttl: int = 1800) -> tuple[bool, str]:
    """Same contract as House._dispatch_github: (ok, why)."""
    mid = os.environ.get("HQ_INSTABOX_MACHINE", "")
    if not mid:
        return False, "HQ_INSTABOX_MACHINE is not set"
    http = getattr(house, "http", None) or httpx.Client(timeout=30)
    try:
        ok, why = wake(http, mid)
        if not ok:
            return False, why
        jt = house.issue_job_token(jid, ttl)  # same one-job token the OIDC claim would hand out
        cmd = command(jid, house.s.public_url, (inputs or {}).get("kind", ""), os.environ.get("HQ_INSTABOX_REF", "main"))
        r = http.post(f"{API}/machines/{mid}/exec", headers=_h(), json={"cmd": cmd, "stdin": jt + "\n", "timeoutSeconds": 120},
                      timeout=150)
        out = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        ok = r.status_code == 200 and out.get("exitCode", 1) == 0
        return ok, f"InstaBox exec {r.status_code}" + ("" if ok else f" exit {out.get('exitCode')}")
    except (httpx.HTTPError, ValueError) as e:  # network error, timeout or a non-JSON answer: the caller falls back
        return False, e.__class__.__name__
