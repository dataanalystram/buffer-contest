"""Is a video link really downloadable? Probe it where the download would happen (owner report 6 Nov 2026).

find_video / research_link called a YouTube launch video "found, downloadable" from its metadata alone; the worker then
failed with "This video is not available" and the post silently became slides. Now a candidate is called downloadable only
after a real probe:

* a platform post (YouTube, Vimeo, X, Loom ...): a ``link_probe`` job on the video worker, the same machine and the same
  fetch path renders use (studio.media.download.probe_link: yt-dlp with only that platform's extractor, metadata and the
  first 64 KB of the chosen stream through netguard; the operator's YouTube fallback route when yt-dlp is bot-checked);
* a direct file: the first 64 KB through netguard here (every hop vetted, body capped), and its type checked.

The answer waits up to ``HQ_PROBE_WAIT`` seconds (default 20). A probe still running, a worker that didn't start, or no
worker at all is NOT downloadable: the client gets found_not_downloadable with the reason and the upload steps. Results are
kept per person and link for ``PROBE_TTL`` (6 h), so a second find_video doesn't start a second job. Free: the probe job
runs on the existing free worker and reads at most 64 KB.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

from .actions import Actor

PROBE_TTL = 6 * 3600
WAIT = float(os.environ.get("HQ_PROBE_WAIT", "20"))
FIRST_BYTES = 64 * 1024
SCHEMA = ("CREATE TABLE IF NOT EXISTS link_probes (user_id TEXT, url_hash TEXT, url TEXT, status TEXT, result TEXT, job_id INTEGER, "
          "at REAL, PRIMARY KEY (user_id, url_hash))")
_FLAG = "_link_probes_ready"
_lock = threading.Lock()
VIDEO_MAGIC = (b"ftyp", b"\x1aE\xdf\xa3", b"moov", b"mdat")


def ensure(store) -> None:
    if getattr(store, _FLAG, False):
        return
    with _lock:
        if getattr(store, _FLAG, False):
            return
        store.x(SCHEMA.replace(" REAL", " DOUBLE PRECISION") if store.pg else SCHEMA)
        if store.pg:
            store.x("ALTER TABLE link_probes ENABLE ROW LEVEL SECURITY")
        setattr(store, _FLAG, True)


def _hash(url: str) -> str:
    return hashlib.sha256(str(url).strip().encode()).hexdigest()[:40]


def _save(store, a: Actor, url: str, status: str, result: dict, job_id: int | None = None) -> None:
    h = _hash(url)
    store.x("DELETE FROM link_probes WHERE user_id=? AND url_hash=?", (a.uid, h))
    store.x("INSERT INTO link_probes(user_id,url_hash,url,status,result,job_id,at) VALUES(?,?,?,?,?,?,?)",
            (a.uid, h, str(url)[:2000], status, json.dumps(result, default=str)[:4000], job_id, time.time()))


def direct_probe(url: str, http=None) -> dict:
    """First bytes of a direct video file through netguard (no DNS rebinding, no private hosts, capped)."""
    from . import netguard
    try:
        with netguard.safe_stream(url, http=http, https_only=True, timeout=15, headers={"Range": f"bytes=0-{FIRST_BYTES - 1}"}) as r:
            if r.status_code not in (200, 206):
                return {"ok": False, "reason": f"the file answered {r.status_code}", "via": "direct"}
            ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            got = b""
            for chunk in r.iter_bytes(16 * 1024):
                got += chunk
                if len(got) >= FIRST_BYTES:
                    break
    except Exception as e:  # noqa: BLE001 - FetchError, httpx errors, a closed client: all mean "not downloadable"
        return {"ok": False, "reason": f"couldn't read the file ({e.__class__.__name__})", "via": "direct"}
    head = got[:64]
    if ctype.startswith("video/") or any(m in head for m in VIDEO_MAGIC):
        return {"ok": True, "via": "direct", "bytes_read": len(got), "content_type": ctype}
    return {"ok": False, "reason": f"that link isn't a video file ({ctype or 'unknown type'})", "via": "direct"}


def probe(studio, a: Actor, url: str, wait: float | None = None) -> dict:
    """{ok: True | False | None, status: ok | failed | pending | unchecked, reason, via, job_id}. Only ok True is downloadable."""
    from .media import find
    h = studio.house
    store = studio.store
    ensure(store)
    url = str(url or "").strip()
    if not url.startswith("https://"):
        return {"ok": False, "status": "failed", "reason": "not a public https link"}
    rows = store.q("SELECT status, result, job_id, at FROM link_probes WHERE user_id=? AND url_hash=?", (a.uid, _hash(url)))
    row = dict(rows[0]) if rows else None
    if row and row["at"] > time.time() - PROBE_TTL and row["status"] in ("ok", "failed"):
        return {**json.loads(row["result"] or "{}"), "status": row["status"], "cached": True}
    if not find.platform_of(url):
        out = direct_probe(url, http=h.http)
        _save(store, a, url, "ok" if out["ok"] else "failed", out)
        return {**out, "status": "ok" if out["ok"] else "failed"}
    from . import demo_guard
    if demo_guard.is_demo(a.uid) or h.video_mode() == "none":
        return {"ok": None, "status": "unchecked", "reason": "no video worker here to check it"}
    jid = row["job_id"] if row and row["status"] == "pending" and row["at"] > time.time() - 600 else None
    if not jid:
        from .media import yt_fallback
        spec = {"kind": "link_probe", "url": url, "yt_fallback": yt_fallback.config()}
        jid = store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                      (a.uid, None, json.dumps(spec), "queued", time.time(), time.time()))
        ok, why = h._dispatch_github(jid, ttl=600)
        if not ok:
            store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (str(why)[:300], time.time(), jid))
            return {"ok": None, "status": "unchecked", "reason": f"the video worker didn't start ({str(why)[:120]})", "job_id": jid}
        _save(store, a, url, "pending", {}, jid)
    t0 = time.time()
    limit = WAIT if wait is None else wait
    while True:
        r = store.q("SELECT status, error, result FROM render_jobs WHERE id=? AND user_id=?", (jid, a.uid))
        if r and r[0]["status"] in ("done", "failed"):
            try:
                res = json.loads(r[0]["result"] or "{}") or {}
            except ValueError:
                res = {}
            ok = r[0]["status"] == "done" and res.get("ok") is True
            out = {**res, "ok": ok, "via": res.get("via") or "worker", "job_id": jid}
            if not ok:
                out["reason"] = res.get("reason") or (r[0]["error"] or "the worker couldn't fetch it")[:300]
            _save(store, a, url, "ok" if ok else "failed", out, jid)
            return {**out, "status": "ok" if ok else "failed"}
        if time.time() - t0 >= limit:
            return {"ok": None, "status": "pending", "job_id": jid,
                    "reason": "the worker is still checking it (render_status(job_id) shows the answer)"}
        time.sleep(0.05 if getattr(h, "sync", False) else 1.0)


def finish(store, job: dict, body: dict | None, error: str) -> dict:
    """The worker's answer for a link_probe job (house.finish_video)."""
    pr = (body or {}).get("probe") if isinstance((body or {}).get("probe"), dict) else None
    ok = bool(pr and pr.get("ok") is True and not error)
    keep = {k: pr.get(k) for k in ("ok", "via", "reason", "code", "title", "duration", "bytes_read") if pr and k in pr} if pr else {}
    store.x("UPDATE render_jobs SET status=?, error=?, result=?, updated=? WHERE id=?",
            ("done" if ok else "failed", (error or ("" if ok else str((pr or {}).get("reason") or "probe failed")))[:500],
             json.dumps(keep or {"ok": False}), time.time(), job["id"]))
    return {"ok": ok}


def _verdict(studio, a: Actor, vf: dict, run_id: Any = None) -> dict:
    """A video-first answer with its candidate probed: 'found' only when the worker could fetch it."""
    from .mcp_flow import upload_steps
    if not isinstance(vf, dict) or vf.get("status") != "found" or not isinstance(vf.get("video"), dict) or not vf["video"].get("url"):
        return vf
    p = probe(studio, a, vf["video"]["url"])
    slim = {k: p.get(k) for k in ("ok", "status", "reason", "via", "job_id", "cached") if p.get(k) is not None}
    if p.get("ok") is True:
        return {**vf, "video": {**vf["video"], "downloadable": True, "probed": True}, "probe": slim}
    limit = vf.get("upload_limit_mb") or (vf.get("upload") or {}).get("max_mb") or 100
    ask = (f"I found the launch video ({vf['video'].get('url')}), but HuhQueue's video worker can't download it right now "
           f"({p.get('reason') or 'unknown reason'}). Could you send me the file? Open the link I'll give you and drop the "
           f"video (MP4 or MOV, up to {limit} MB).")
    return {**vf, "status": "found_not_downloadable", "video": {**vf["video"], "downloadable": False, "probed": True},
            "probe": slim, "ask": ask, "plan_line": "Launch video found but the worker can't fetch it: asking you to upload it",
            "next_steps": upload_steps(run_id)}


def apply(studio, a: Actor, vf: dict, run_id: Any = None) -> dict:
    return _verdict(studio, a, vf, run_id)


def apply_to_run(studio, a: Actor, rid: int, out: dict) -> dict:
    """post_find_video: the run remembers the probe; a video the worker can't fetch is never the Reel's source."""
    got = _verdict(studio, a, out, rid)
    if got is out or not isinstance(got.get("probe"), dict):
        return got
    h = studio.house
    run = h.run(a, rid)
    res = run["result"]
    vr = res.setdefault("video_research", {})
    vr["probe"] = got["probe"]
    if got["status"] != "found":
        vr["status"] = "found_not_downloadable"
        found = res.get("found_media") if isinstance(res.get("found_media"), dict) else {}
        if (found.get("best_downloadable") or {}).get("url") == (got.get("video") or {}).get("url"):
            found["best_downloadable"] = None
            res["found_media"] = found
        res["video_ask"] = {**(res.get("video_ask") or {}), "text": got["ask"], "status": "found_not_downloadable"}
    h._save(rid, run["steps"], res)
    return got
