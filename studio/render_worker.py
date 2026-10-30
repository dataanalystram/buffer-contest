"""Free video render worker. Runs on GitHub Actions (`.github/workflows/render.yml`), dispatched by the app per job:

    WORKER_SECRET=... python -m studio.render_worker --job 12 --app https://huhqueue-studio.onrender.com

It reads the job (brand kit, ledger params, media URLs), renders with FFmpeg and posts the MP4 back. The web app's free
instance never runs FFmpeg itself.
"""
from __future__ import annotations

import argparse
import base64
import os
import sys

import httpx

from . import render


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, type=int)
    ap.add_argument("--app", required=True)
    args = ap.parse_args(argv)
    base = args.app.rstrip("/")
    h = {"x-worker-secret": os.environ["WORKER_SECRET"]}
    c = httpx.Client(timeout=120, follow_redirects=True)
    job = c.get(f"{base}/api/worker/jobs/{args.job}", headers=h)
    job.raise_for_status()
    j = job.json()
    spec, urls = j["spec"], j["urls"]
    get = lambda u: c.get(u).content if u else None  # noqa: E731
    try:
        logo = get(urls.get("logo"))
        if spec["kind"] == "brand_video":
            data, meta = render.brand_video(get(urls["source"]), spec["kit"], spec["params"], logo=logo, hook=spec.get("hook", ""),
                                            ratio=spec.get("ratio", "9:16"))
        else:
            data, meta = render.slideshow_reel([get(u) for u in urls["slides"]], spec["kit"], spec["params"], logo=logo,
                                               ratio=spec.get("ratio", "9:16"))
        r = c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"data": base64.b64encode(data).decode(), "meta": meta})
        print("posted", len(data), "bytes:", r.status_code, r.text[:200])
        return 0 if r.status_code == 200 else 1
    except Exception as e:  # report the failure so the run stops spinning
        c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"error": f"{e.__class__.__name__}: {e}"[:500]})
        raise


if __name__ == "__main__":
    sys.exit(main())
