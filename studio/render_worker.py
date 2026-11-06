"""Free video render worker. Runs on GitHub Actions (`.github/workflows/render.yml` for Reels, the weekly recap
(job kind media_recap) and the ML product cut-out (job kind image_matte), `take.yml` for One take),
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
import time
from pathlib import Path

import httpx

from . import render

# The same cap as a multipart One take upload (take_upload.max_bytes, 4096 MB by default; was 1500 MB). A GitHub runner has
# ~14 GB free disk, so a 4 GB source, its 16 kHz WAV for Whisper and the rendered clips fit.
MAX_SOURCE = int(os.environ.get("HQ_MAX_TAKE_BYTES") or int(os.environ.get("HQ_TAKE_UPLOAD_MAX_MB", "4096")) * 1024 * 1024)


def _download(c: httpx.Client, url: str, dest: Path) -> None:
    from . import netguard
    for sig in _SIGS.findall(url):  # a presigned upload URL: the runner masks its signature in the public Actions log
        print(f"::add-mask::{sig}", flush=True)
    with netguard.safe_stream(url, http=c, https_only=url.startswith("https://"), timeout=300) as r:  # every hop checked
        r.raise_for_status()
        n = 0
        with open(dest, "wb") as fh:
            for chunk in r.iter_bytes(1 << 20):
                n += len(chunk)
                if n > MAX_SOURCE:
                    raise RuntimeError(f"The video is over {MAX_SOURCE // (1024 * 1024)} MB.")
                fh.write(chunk)


def store_source(c: httpx.Client, put_url: str, src: Path) -> int:
    """Keep a fetched link in the app's bucket (a presigned PUT the app signed for this take) so the renders don't fetch
    the platform again. Best effort: 0 when the store refused (the renders then fetch the link again)."""
    n = src.stat().st_size
    if not put_url.startswith("https://") or n <= 0 or n > MAX_SOURCE:
        return 0
    try:
        with open(src, "rb") as fh:
            r = c.put(put_url, content=fh, headers={"Content-Length": str(n), "Content-Type": "video/mp4"}, timeout=600)
        print("source stored:", r.status_code)
        return n if r.status_code < 300 else 0
    except (httpx.HTTPError, OSError, RuntimeError) as e:  # RuntimeError: a closed client
        print("source not stored:", e.__class__.__name__)
        return 0


def render_parallel(spec: dict) -> int:
    """How many clips of a shorts-style take render at once: HQ_RENDER_PARALLEL, else one per 2 vCPUs (each encode
    uses HQ_FFMPEG_THREADS=2) while ~1.5 GB of memory stays free per clip, at most 3. One take's own render (OCR,
    Smart layout) keeps one at a time."""
    if spec.get("style") != "opus":
        return 1
    v = os.environ.get("HQ_RENDER_PARALLEL", "")
    if v.isdigit():
        return max(1, min(4, int(v)))
    avail = 0
    try:
        with open("/proc/meminfo") as fh:
            avail = next((int(x.split()[1]) // 1024 for x in fh if x.startswith("MemAvailable:")), 0)
    except (OSError, ValueError, IndexError):
        avail = 0
    by_mem = avail // 1500 if avail else 1
    return max(1, min((os.cpu_count() or 1) // 2, by_mem, 3))


def take_job(c: httpx.Client, base: str, h: dict, jid: int, j: dict) -> int:
    from . import take
    from .media import selfcheck
    spec, urls = j["spec"], j["urls"]
    print("self-check:", selfcheck.check(spec["kind"]))  # fails loudly (a clear job error) before any work, never degrades silently
    if spec["kind"] == "take_render":  # advisory only: OCR off (fail open) when tesseract >= 5.5.4 isn't installed yet
        try:
            from .media import ocr
            st = ocr.status()
            print("ocr:", f"tesseract {st['version']} ok" if st["ok"] else f"off ({st['why']})")
        except Exception as e:  # noqa: BLE001 - OCR is advisory: the render goes on without it
            print("ocr: off (status failed:", e.__class__.__name__ + ")")
    tmp = Path(tempfile.mkdtemp(prefix="hq_worker_"))
    src = tmp / "source.mp4"
    try:
        from .takes import fetch_platform, render_spec_clip, reuse_captions
        fetched = None
        t_dl = time.time()
        if spec.get("platform_url"):  # a YouTube / Vimeo / X post: Make a post's own fetch path (yt-dlp, 4 GB / 3 h caps, vetted)
            fetched = {**fetch_platform(spec["platform_url"], src, MAX_SOURCE), "via": "yt-dlp"}
        else:
            _download(c, urls["source"], src)
            if spec.get("source_url"):
                fetched = {"via": "direct"}
        dl = round(time.time() - t_dl, 1)
        print(f"timing: source download {dl}s ({'yt-dlp' if spec.get('platform_url') else 'stored copy / upload'})", flush=True)
        store = None
        if fetched is not None and spec["kind"] == "take_analyze" and urls.get("source_put"):
            # the fetched link is kept in storage (no second fetch for re-renders) WHILE the analysis reads the same file:
            # the upload used to finish before the analysis started
            import threading
            store = {"t": time.time()}

            def keep() -> None:
                try:
                    store["bytes"] = store_source(c, urls["source_put"], src)
                except BaseException as e:  # noqa: BLE001 - raised on the main thread at the join, as before
                    store["error"] = e
                store["secs"] = round(time.time() - store["t"], 1)
            store["th"] = threading.Thread(target=keep, name="hq_store_source", daemon=True)
            store["th"].start()
        if spec["kind"] == "take_analyze":
            tr = reuse_captions(str(src), spec)  # existing captions first; Whisper only when there are none
            print("captions:", f"reused {tr['source']} ({tr['count']} words)" if tr else "none, transcribing")
            analysis = take.analyze(str(src), spec.get("model", "small"), max_secs=float(os.environ.get("HQ_MAX_TAKE_SECS", "10800")),
                                    transcript=tr, jev=spec.get("jev") is True)
            if fetched is not None:
                analysis["fetch"] = fetched
            analysis.setdefault("timings", {})["download"] = dl
            if store is not None:
                store["th"].join()
                if store.get("error") is not None:
                    raise store["error"]
                fetched["stored_bytes"] = store["bytes"]
                analysis["timings"]["store_source"] = store["secs"]  # alongside the analysis, not before it
                print(f"timing: source kept in storage (R2) {store['secs']}s, alongside the analysis", flush=True)
            print("timing: analysis", analysis.get("timings"), flush=True)
            r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json={"analysis": analysis})
            print("analysis posted:", r.status_code, r.text[:200])
            return 0 if r.status_code == 200 else 1
        logo = c.get(urls["logo"]).content if urls.get("logo") else None
        from urllib.parse import quote

        def one(clip: dict) -> None:
            t = time.time()
            for cid, data, cover, meta in render_spec_clip(str(src), spec, clip, logo):
                if isinstance(meta.get("timings"), dict):
                    meta["timings"]["source_download"] = dl
                tp = time.time()
                r = c.post(f"{base}/api/worker/jobs/{jid}/clips/{quote(cid, safe='')}", headers=h,
                           json={"data": base64.b64encode(data).decode(), "cover": base64.b64encode(cover).decode(), "meta": meta})
                print(f"clip {cid}: {len(data)} bytes, {meta['layout']}: {r.status_code}; timing {meta.get('timings')}, "
                      f"post + storage {time.time() - tp:.1f}s", flush=True)
            print(f"timing: clip {clip['id']} done in {time.time() - t:.1f}s", flush=True)
        k = render_parallel(spec)
        t_r = time.time()
        if k > 1 and len(spec["clips"]) > 1:
            from concurrent.futures import ThreadPoolExecutor
            try:  # OpenCV (YuNet face sampling + render QA) uses every core per call by default: with k clips at once beside
                import cv2  # their FFmpeg encodes they'd fight for the same cores, so each gets its share
                cv2.setNumThreads(max(1, (os.cpu_count() or 2) // k))
            except Exception:  # noqa: BLE001 - no OpenCV here: the face steps fall back on their own and say so
                pass
            with ThreadPoolExecutor(max_workers=k, thread_name_prefix="hq_clip") as ex:
                list(ex.map(one, spec["clips"]))  # the first failure is raised (and reported) once every clip stopped
        else:
            for clip in spec["clips"]:
                one(clip)
        print(f"timing: {len(spec['clips'])} clip(s) rendered in {time.time() - t_r:.1f}s, {k} at a time", flush=True)
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
        try:
            logo = get(urls.get("logo"))
        except httpx.HTTPStatusError:  # the logo is optional: a dead logo link renders without it
            logo = None
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


# ---- failure reporting + heartbeat (2 Nov 2026: job 15 died on a 404 source link and the app kept showing it running)
LOG_TAIL = 4000  # characters of the worker's own output sent back with a failure
_JT = __import__("re").compile(r"jt_[A-Za-z0-9_\-]{8,}")
_SIG = __import__("re").compile(r"(?i)(X-Amz-Signature=)[0-9A-Za-z%]+")  # a presigned URL's signature (log_tail redacts it)
_MASK = __import__("re").compile(r"::add-mask::\S+")
_SIGS = __import__("re").compile(r"(?i)X-Amz-Signature=([0-9A-Za-z%]+)")


class _Tail:
    """Tee for stdout/stderr that keeps the last LOG_TAIL characters, so a failure report carries the log tail on both
    paths (the InstaBox box logs to /data/logs/job-<id>.log, the GitHub runner to its run log)."""

    def __init__(self, inner):
        self.inner, self.buf = inner, ""

    def write(self, s):
        self.buf = (self.buf + s)[-LOG_TAIL * 2:]
        return self.inner.write(s)

    def flush(self):
        self.inner.flush()

    def __getattr__(self, k):
        return getattr(self.inner, k)


_tails: list = []


def _install_tail() -> None:
    if not _tails:
        _tails.extend([_Tail(sys.stdout), _Tail(sys.stderr)])
        sys.stdout, sys.stderr = _tails


def log_tail() -> str:
    """The last lines this worker printed (stdout + stderr), job tokens redacted."""
    out = "".join(t.buf for t in _tails)[-LOG_TAIL:]
    out = _MASK.sub("::add-mask::[redacted]", out)  # a mask command's own value (the runner hides it; this copy is ours)
    return _SIG.sub(r"\1[redacted]", _JT.sub("jt_[redacted]", out))


def friendly(e: BaseException, base: str = "") -> str:
    """A short message a person understands, e.g. "Couldn't download the video: the link returned 404"."""
    from urllib.parse import urlparse
    app_host = urlparse(base).hostname or ""
    if isinstance(e, httpx.HTTPStatusError):
        code, host, path = e.response.status_code, e.request.url.host, e.request.url.path or ""
        if app_host and host == app_host and path.startswith(("/api/worker", "/api/oidc")):
            return f"The video worker couldn't talk to HuhQueue (HTTP {code})."
        if app_host and host == app_host:  # a source link on our own host (a /m/ file, or any page there) answered an error
            return f"Couldn't download the video: the source link returned {code}"
        return f"Couldn't download the video: the link returned {code}"
    if isinstance(e, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "Couldn't download the video: the link didn't answer"
    if isinstance(e, httpx.TimeoutException):
        return "Couldn't download the video: the link timed out"
    try:
        from . import netguard
        if isinstance(e, netguard.FetchError):
            return f"Couldn't download the video: {e}"[:300]
    except Exception:  # noqa: BLE001  a worker venv without netguard's deps still reports
        pass
    if isinstance(e, MemoryError):
        return "The video worker ran out of memory on this video."
    if isinstance(e, (SystemExit, KeyboardInterrupt)):
        return "The video worker was stopped before it finished."
    msg = str(e).strip()
    if e.__class__.__name__ in ("TakeError", "RuntimeError", "CapabilityError", "MediaError", "MatteError") and msg:
        return msg[:300]
    return f"The video worker hit an error ({e.__class__.__name__}{': ' + msg if msg else ''})"[:300]


def report_failure(c: httpx.Client, base: str, h: dict, jid: int, e: BaseException, tries: int = 3) -> bool:
    """POST status=failed with the short message and the log tail on the job's own token. Never raises."""
    import time
    tail = log_tail()  # main() printed the traceback through the tee, so it is in here
    if not tail:  # called outside main(): send the traceback itself
        import traceback
        tail = _JT.sub("jt_[redacted]", "".join(traceback.format_exception(type(e), e, e.__traceback__)))[-LOG_TAIL:]
    body = {"status": "failed", "error": friendly(e, base), "log_tail": tail}
    for i in range(tries):
        try:
            r = c.post(f"{base}/api/worker/jobs/{jid}", headers=h, json=body, timeout=30)
            print(f"failure reported: HTTP {r.status_code} {r.text[:200]}", flush=True)
            if r.status_code < 500:  # 200, or 403 (token spent / job already closed): retrying can't help
                return r.status_code == 200
        except Exception as x:  # noqa: BLE001  the app asleep or unreachable: try again
            print(f"failure report attempt {i + 1} failed: {x.__class__.__name__}", flush=True)
        time.sleep(min(2 ** i, 5))
    return False


class Heartbeat:
    """Pings the app every HQ_HEARTBEAT_S seconds while the job runs; the app fails a job that goes silent
    (House.sweep_stale_jobs), so a worker killed outright (OOM, box paused, runner cancelled) can't leave it running."""

    def __init__(self, base: str, h: dict, jid: int, every: float | None = None):
        import threading
        self.url, self.h = f"{base}/api/worker/jobs/{int(jid)}/heartbeat", dict(h)
        self.every = float(os.environ.get("HQ_HEARTBEAT_S", "60")) if every is None else every
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._loop, name="hq-heartbeat", daemon=True)

    def beat(self) -> int:
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as hc:
                return hc.post(self.url, headers=self.h).status_code
        except Exception:  # noqa: BLE001  a missed beat is fine; the app allows several
            return 0

    def _loop(self):
        while not self.stop.is_set():
            self.beat()
            self.stop.wait(self.every)

    def __enter__(self):
        if self.every > 0:
            self.t.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        return False


def _lock():
    """HQ_LOCK=/data/hq.lock (InstaBox): one job at a time on the box, taken after the heartbeat starts so a job waiting
    its turn still looks alive to the app."""
    path = os.environ.get("HQ_LOCK")
    if not path:
        return None
    import fcntl
    fh = open(path, "a")
    fcntl.flock(fh, fcntl.LOCK_EX)
    return fh


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True, type=int)
    ap.add_argument("--app", required=True)
    args = ap.parse_args(argv)
    base = args.app.rstrip("/")
    saved = sys.stdout, sys.stderr
    _install_tail()
    try:
        return _main(args, base)
    finally:
        sys.stdout, sys.stderr = saved
        _tails.clear()


def _main(args, base: str) -> int:
    c = httpx.Client(timeout=300, follow_redirects=False)
    h = {"x-job-token": os.environ.get("JOB_TOKEN") or claim(c, base, args.job)}  # no token: nothing can be reported;
    # the app's watchdog fails the job when no heartbeat arrives
    from . import free_quota  # every free Groq / vision request is reserved on the app's ledger first
    free_quota.REMOTE = free_quota.worker_remote(c, base, h, args.job)
    free_quota.PROXY = free_quota.WorkerProxy(c, base, h, args.job)  # Groq / vision calls are made by the app, with its keys
    with Heartbeat(base, h, args.job):
        lock = _lock()
        try:
            return _run(c, base, h, args)
        except BaseException as e:  # ANY failure (bad link, crash, Ctrl-C, SystemExit) is reported, then the run fails
            import traceback
            traceback.print_exc()
            report_failure(c, base, h, args.job, e)
            return 1
        finally:
            free_quota.REMOTE = None
            free_quota.PROXY = None
            if lock:
                lock.close()


def _run(c: httpx.Client, base: str, h: dict, args) -> int:
    job = c.get(f"{base}/api/worker/jobs/{args.job}", headers=h)
    job.raise_for_status()
    j = job.json()
    spec, urls = j["spec"], j["urls"]
    from .headline import spec_hook
    spec_hook(spec)  # the worker cleans the hook itself too: the app's guard is not the only one (owner P0, 1 Nov 2026)
    from .media import yt_fallback
    yt_fallback.apply(spec.get("yt_fallback"))  # the app's opt-in YouTube route (HQ_YT_FALLBACK), for this job only

    def get(u, optional: bool = False):
        if not u:
            return None
        from . import netguard
        r = netguard.safe_get(u, http=c, limit=200 * 1024 * 1024, timeout=300)
        if r.status_code >= 400:  # a 404 page is not a video: fail with "the link returned 404", not an FFmpeg error
            if optional:
                return None
            r.raise_for_status()
        return r.content
    if spec.get("kind") == "link_probe":  # can this worker download the link? (studio/link_probe.py, 6 Nov 2026)
        from .media.download import probe_link
        out = probe_link(str(spec.get("url") or ""))
        r = c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"probe": out})
        print("probe posted:", out.get("ok"), str(out.get("reason") or "")[:120], r.status_code)
        return 0 if r.status_code == 200 else 1
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
    if spec["kind"] == "image_matte":  # the ML product cut-out (studio/image_matte.py): one PNG back, no logo, no FFmpeg
        from .image_matte import worker_job as matte_job
        data, meta = matte_job(spec, urls, get)
        r = c.post(f"{base}/api/worker/jobs/{args.job}", headers=h, json={"data": base64.b64encode(data).decode(), "meta": meta})
        print("cut-out posted", len(data), "bytes:", r.status_code, r.text[:200])
        return 0 if r.status_code == 200 else 1
    logo = get(urls.get("logo"), optional=True)
    if spec["kind"] == "cover_swap":  # another cover variant as the Reel's opening frames (studio.media.worker.cover_swap)
        from .media.worker import cover_swap
        data, meta = cover_swap(get(urls["source"]), get(urls["cover"]), spec.get("secs") or None, spec.get("replace"))
    elif spec["kind"] == "audio_fix":  # only the sound re-encoded to Instagram's spec; the picture is copied (preflight audio fix)
        from .media.worker import fix_audio
        data, meta = fix_audio(get(urls["source"]))
    elif spec["kind"] == "media_render":  # studio.media: find/fetch, transcribe, styled edit (opus/news/minimal/hype)
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


if __name__ == "__main__":
    sys.exit(main())
