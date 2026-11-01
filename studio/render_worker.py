"""Free video render worker. Runs on GitHub Actions (`.github/workflows/render.yml` for Reels and the weekly recap
(job kind media_recap), `take.yml` for One take),
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
        from .takes import fetch_platform, render_spec_clip, reuse_captions
        if spec.get("platform_url"):  # a YouTube / Vimeo / X post: single-extractor yt-dlp, size-capped, vetted
            fetch_platform(spec["platform_url"], src)
        else:
            _download(c, urls["source"], src)
        if spec["kind"] == "take_analyze":
            tr = reuse_captions(str(src), spec)  # existing captions first; Whisper only when there are none
            print("captions:", f"reused {tr['source']} ({tr['count']} words)" if tr else "none, transcribing")
            analysis = take.analyze(str(src), spec.get("model", "small"), max_secs=float(os.environ.get("HQ_MAX_TAKE_SECS", "1800")),
                                    transcript=tr)
            r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"analysis": analysis})
            print("analysis posted:", r.status_code, r.text[:200])
            return 0 if r.status_code == 200 else 1
        logo = c.get(urls["logo"]).content if urls.get("logo") else None
        from urllib.parse import quote
        for clip in spec["clips"]:
            for cid, data, cover, meta in render_spec_clip(str(src), spec, clip, logo):
                r = c.post(f"{base}/api/worker/jobs/{jid}/clips/{quote(cid, safe='')}", headers=h,
                           json={"data": base64.b64encode(data).decode(), "cover": base64.b64encode(cover).decode(), "meta": meta})
                print(f"clip {cid}: {len(data)} bytes, {meta['layout']}: {r.status_code}")
        r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"meta": {"clips": len(spec["clips"])}})
        print("render done:", r.status_code)
        return 0 if r.status_code == 200 else 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def recap_job(c: httpx.Client, base: str, h: dict, jid: int, j: dict, get) -> int:
    """The weekly recap (studio/media/recap.py): fetch each story's media (netguard / single-extractor yt-dlp, vetted),
    render the 4:5 carousel slides and the 9:16 Reel, and post each file once (the job token allows each file id once)."""
    import shutil
    from urllib.parse import quote

    from .media import tools as media_tools
    from .media.worker import brand_from_kit
    spec, urls = j["spec"], j["urls"]
    tmp = Path(tempfile.mkdtemp(prefix="hq_recap_"))
    try:
        logo = get(urls.get("logo"))
        logo_path = None
        if logo:
            logo_path = str(tmp / "logo.png")
            Path(logo_path).write_bytes(logo)
        # rights were settled in the app before the job was queued (declined stories carry no media)
        out = media_tools.media_recap(spec["plan"], True, brand_from_kit(spec.get("kit"), logo_path), run_dir=str(tmp / "run"), http=c)
        if out.get("needs_confirmation"):
            raise RuntimeError("rights not settled for this recap")
        sent = 0
        for cid, path, meta in media_tools.recap_files(out):
            data = Path(path).read_bytes()
            r = c.post(f"{base}/api/worker/jobs/{jid}/clips/{quote(cid, safe='')}", headers=h,
                       json={"data": base64.b64encode(data).decode(), "meta": meta})
            print(f"recap {cid}: {len(data)} bytes: {r.status_code}")
            sent += r.status_code == 200
        r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"meta": {"files": sent, "slides": len(out.get("slides") or [])}})
        print("recap done:", r.status_code, r.text[:200])
        return 0 if r.status_code == 200 else 1
    finally:
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
        if spec["kind"] == "transcribe":  # the person's own upload -> words, so the post is about what is said in it
            from .media import transcribe as tr
            tmp = Path(tempfile.mkdtemp(prefix="hq_tr_"))
            try:
                _download(c, urls["source"], tmp / "in.mp4")
                out = tr.transcribe(str(tmp / "in.mp4"), model=spec.get("model") or "tiny")
            finally:
                import shutil
                shutil.rmtree(tmp, ignore_errors=True)
            r = c.post(f"{base}/api/worker/jobs/{args.job}", headers=h,
                       json={"transcript": {"text": out.get("text", "")[:20000], "language": out.get("language", "")}})
            print("transcript posted:", len(out.get("text", "")), "chars:", r.status_code)
            return 0 if r.status_code == 200 else 1
        if spec["kind"] == "media_recap":  # the weekly recap: carousel slides + one Reel, several files
            return recap_job(c, base, h, args.job, j, get)
        logo = get(urls.get("logo"))
        if spec["kind"] == "media_render":  # studio.media: find/fetch, transcribe, styled edit (opus/news/minimal/hype)
            from .media.worker import render_job
            data, meta = render_job(spec, get, urls=urls, logo=logo)
        elif spec["kind"] == "template_video":  # a Brand studio template's Reel, in the person's own brand
            from . import templates
            b = templates.Brand(spec["kit"], logo, get(urls.get("front")), get(urls.get("end")))
            data, meta = templates.video(spec["template"], spec.get("data"), b, spec.get("ratio", "9:16")), {"type": "video", "template": spec["template"]}
        elif spec["kind"] == "brand_video":
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
