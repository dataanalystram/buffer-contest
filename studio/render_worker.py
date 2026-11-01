"""Free video render worker. Runs on GitHub Actions (`.github/workflows/render.yml` for Reels, `take.yml` for One take),
dispatched by the app per job:

    python -m studio.render_worker --job 12 --app https://huhqueue-studio.onrender.com

On GitHub Actions it claims the job once with the run's OIDC identity and keeps the job token in memory only
(SECURITY_RULES 2). JOB_TOKEN=jt_... in the environment is for a self-hosted worker the operator runs by hand.

It reads the job (brand kit, ledger params, media URLs), does the heavy work (FFmpeg; for One take also faster-whisper,
PySceneDetect and MediaPipe) and posts the result back. The web app's free instance never runs FFmpeg itself.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
import tempfile
from pathlib import Path

import httpx

from . import render

MAX_SOURCE = int(os.environ.get("HQ_MAX_TAKE_BYTES", str(1500 * 1024 * 1024)))


def _download(c: httpx.Client, url: str, dest: Path) -> None:
    from . import netguard
    with netguard.safe_stream(url, http=c, https_only=url.startswith("https://"), timeout=300) as r:  # every hop checked
        r.raise_for_status()
        n = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                n += len(chunk)
                if n > MAX_SOURCE:
                    raise RuntimeError(f"The video is over {MAX_SOURCE // (1024 * 1024)} MB.")
                fh.write(chunk)


def take_job(c: httpx.Client, base: str, h: dict, jid: int, j: dict) -> int:
    from . import take
    spec, urls = j["spec"], j["urls"]
    tmp = Path(tempfile.mkdtemp(prefix="hq_worker_"))
    src = tmp / "source.mp4"
    try:
        _download(c, urls["source"], src)
        if spec["kind"] == "take_analyze":
            analysis = take.analyze(str(src), spec.get("model", "small"), max_secs=float(os.environ.get("HQ_MAX_TAKE_SECS", "1800")))
            r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"analysis": analysis})
            print("analysis posted:", r.status_code, r.text[:200])
            return 0 if r.status_code == 200 else 1
        logo = c.get(urls["logo"]).content if urls.get("logo") else None
        for clip in spec["clips"]:
            data, cover, meta = take.render_clip(str(src), clip, clip["words"], clip["faces"], spec.get("scenes") or [], spec["src"],
                                                 spec["kit"], spec["params"], logo=logo)
            r = c.post(f"{base}/api/worker/jobs/{jid}/clips/{clip['id']}", headers=h,
                       json={"data": base64.b64encode(data).decode(), "cover": base64.b64encode(cover).decode(), "meta": meta})
            print(f"clip {clip['id']}: {len(data)} bytes, {meta['layout']}: {r.status_code}")
        r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"meta": {"clips": len(spec["clips"])}})
        print("render done:", r.status_code)
        return 0 if r.status_code == 200 else 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def claim(c: httpx.Client, base: str, jid: int) -> str:
    """Trade this run's GitHub OIDC token (audience: the app URL) for the job's token, once. Never printed."""
    from urllib.parse import quote
    req_url, req_tok = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL"), os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not req_url or not req_tok:
        raise SystemExit("No JOB_TOKEN and no GitHub OIDC (the workflow needs permissions: id-token: write).")
    oidc = c.get(f"{req_url}&audience={quote(base, safe='')}", headers={"Authorization": f"bearer {req_tok}"})
    oidc.raise_for_status()
    r = c.post(f"{base}/api/worker/jobs/{jid}/claim", headers={"Authorization": f"Bearer {oidc.json()['value']}"})
    if r.status_code != 200:
        raise SystemExit(f"claim refused: HTTP {r.status_code} {r.text[:200]}")
    jt = r.json()["job_token"]
    print(f"::add-mask::{jt}", flush=True)  # a workflow command on stdout: the runner hides it and masks the value
    return jt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, type=int)
    ap.add_argument("--app", required=True)
    args = ap.parse_args(argv)
    base = args.app.rstrip("/")
    c = httpx.Client(timeout=300, follow_redirects=False)
    h = {"x-job-token": os.environ.get("JOB_TOKEN") or claim(c, base, args.job)}
    job = c.get(f"{base}/api/worker/jobs/{args.job}", headers=h)
    job.raise_for_status()
    j = job.json()
    spec, urls = j["spec"], j["urls"]

    def get(u):
        if not u:
            return None
        from . import netguard
        return netguard.safe_get(u, http=c, limit=200 * 1024 * 1024, timeout=300).content
    try:
        if str(spec.get("kind", "")).startswith("take_"):
            return take_job(c, base, h, args.job, j)
        logo = get(urls.get("logo"))
        if spec["kind"] == "brand_video":
            data, meta = render.brand_video(get(urls["source"]), spec["kit"], spec["params"], logo=logo, hook=spec.get("hook", ""),
                                            ratio=spec.get("ratio", "9:16"))
        else:
            data, meta = render.slideshow_reel([get(u) for u in urls["slides"]], spec["kit"], spec["params"], logo=logo,
                                               ratio=spec.get("ratio", "9:16"), total=spec.get("total"))
        r = c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"data": base64.b64encode(data).decode(), "meta": meta})
        print("posted", len(data), "bytes:", r.status_code, r.text[:200])
        return 0 if r.status_code == 200 else 1
    except Exception as e:  # report the failure so the run stops spinning
        c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"error": f"{e.__class__.__name__}: {e}"[:500]})
        raise


if __name__ == "__main__":
    sys.exit(main())
