"""One take -> a week: the service (UI = REST = MCP).

A take is an agent run of kind 'take'. The heavy work (transcription, scene and face detection, rendering) runs on the
video worker through render_jobs, exactly like Reels: on GitHub Actions for the hosted app (`take.yml`), or in a
background thread where FFmpeg is installed (self-host, CI). The web process only picks the moments (a small LLM call
through llm.resolve, or the built-in scorer), runs preflight and places drafts.

Placement is always two steps and drafts only: place_clips(dry_run) returns the plan, the preflight per clip and a
single-use confirm token; place_clips(confirm_token) creates Buffer *drafts* at the planned times through the same
preview_post -> submit_post path every post takes, so an agent token's policy (approval inbox) still holds.
Nothing here ever publishes.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import tempfile
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import brand, demo, drafter, llm, take
from .actions import CONFIRM_TTL, ActionError, Actor
from .store import sha

STEPS = [("upload", "Read your video"), ("transcribe", "Transcribe"), ("moments", "Find the best moments"),
         ("reframe", "Reframe to 9:16"), ("captions", "Burn in brand captions"), ("covers", "Branded covers + end card"),
         ("preflight", "Preflight every clip"), ("ready", "Ready to spread")]
VIDEO_FIRST = ("instagram", "tiktok", "youtube")
TAKES_VIDEO = ("instagram", "tiktok", "youtube", "twitter", "linkedin", "threads", "facebook", "bluesky")
THUMB_OFFSET = ("instagram", "tiktok", "pinterest")  # Buffer: metadata.thumbnailOffset is honoured on these only
SOCIAL_PAGES = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "vimeo.com",
                "linkedin.com", "twitch.tv")
SAMPLE_DIR = Path(__file__).parent / "sample_take"
MAX_TAKE_SECS = int(os.environ.get("HQ_MAX_TAKE_SECS", "1800"))


def _now() -> float:
    return time.time()


def sample_meta() -> dict | None:
    p = SAMPLE_DIR / "sample.json"
    return json.loads(p.read_text()) if p.exists() else None


class Takes:
    def __init__(self, house):
        self.h, self.studio, self.store, self.s = house, house.studio, house.store, house.s
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _run(self, a: Actor, rid: int) -> dict:
        r = self.h.run(a, rid)
        if r["kind"] != "take":
            raise ActionError("No such take.", 404)
        return r

    def _save(self, rid, steps, res, status=None):
        self.h._save(rid, steps, res, status)

    def _step(self, steps, key, status, detail="", data=None):
        self.h._step(steps, key, status, detail, data)

    def _channels(self, a: Actor, ids: list[str] | None) -> list[dict]:
        chans = [c for c in self.studio.channels(a) if not c.get("isDisconnected") and c["service"] in TAKES_VIDEO]
        if ids:
            chans = [c for c in chans if c["id"] in ids]
        elif any(c["service"] in VIDEO_FIRST for c in chans):
            chans = [c for c in chans if c["service"] in VIDEO_FIRST]
        return chans

    def _mode(self) -> str:
        m = self.h.worker_mode
        if m in ("local", "github", "none"):
            return m
        if take.have("ffmpeg"):
            return "local"
        return "github" if os.environ.get("GH_DISPATCH_TOKEN") else "none"

    def media_url(self, tok: str) -> str:
        return self.h.media_url(tok)

    def sample_url(self, name: str) -> str:
        return f"{self.s.public_url.rstrip('/')}/sample/{name}"

    # ------------------------------------------------------------------ start
    def start(self, a: Actor, body: dict) -> dict:
        """clips_from_video: one long video you own -> moments -> clips. Returns the take (poll get_take/list_clips)."""
        if not body.get("owns"):
            raise ActionError("Confirm this is your own video (owns: true). One take only clips videos you own or have the "
                              "rights to.")
        token, url = (body.get("media_token") or "").strip(), (body.get("url") or body.get("media_url") or "").strip()
        base = self.s.public_url.rstrip("/") + "/m/"
        if url.startswith(base):
            token, url = url[len(base):], ""
        if not token and not url:
            raise ActionError("Upload a video (media_token) or give a direct link to a video file you own (url).")
        if token:
            meta = self.h.media_meta(a, token)
            if not meta or meta.get("type") != "video":
                raise ActionError("Unknown video. Upload it first (POST /api/media).", 404)
        else:
            p = urlparse(url)
            if p.scheme != "https" or not p.hostname or not drafter._public(p.hostname):
                raise ActionError("The link must be a public https:// address of a video file.")
            host = p.hostname.lower().removeprefix("www.").removeprefix("m.")
            if any(host == d or host.endswith("." + d) for d in SOCIAL_PAGES):
                raise ActionError("Give a direct link to the video file itself (for example …/talk.mp4 from your own storage), "
                                  "not a page on a social network.")
        chans = self._channels(a, body.get("channel_ids"))
        if not chans:
            raise ActionError("Pick at least one connected channel that takes video (Instagram, TikTok, YouTube, X, LinkedIn, Threads).")
        count = body.get("count")
        count = max(1, min(7, int(count))) if count else None
        steps = [{"key": k, "label": l, "status": "pending", "detail": "", "data": None} for k, l in STEPS]
        inp = {"media_token": token, "url": url, "count": count, "owns": True, "name": (body.get("name") or "")[:80],
               "channels": [{"id": c["id"], "service": c["service"], "name": c.get("displayName") or c["name"]} for c in chans]}
        rid = self.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                           (a.uid, "take", json.dumps(inp), "running", json.dumps(steps), json.dumps({}), _now(), _now()))
        self.store.audit(a.uid, a.label, "clips_from_video", "read", {"take": rid, "source": "upload" if token else "link"})
        run = self._run(a, rid)
        self._step(run["steps"], "upload", "done", "Your upload" if token else f"Your link ({urlparse(url).hostname})",
                   {"source": self.media_url(token) if token else url})
        self._save(rid, run["steps"], run["result"])
        self._queue(a, rid, {"kind": "take_analyze", "source": token, "source_url": url, "model": os.environ.get("HQ_STT_MODEL", "small")},
                    "transcribe", "Transcribing on the video worker (faster-whisper, CPU)")
        return self.get(a, rid)

    def start_sample(self, a: Actor, body: dict | None = None) -> dict:
        """The sample take: a 3-minute public-domain talk, analysed and rendered ahead of time by the same pipeline.
        Mining, preflight and placement run live. It needs no worker and no Buffer."""
        sm = sample_meta()
        if not sm:
            raise ActionError("The sample take isn't bundled with this server.", 404)
        chans = self._channels(a, (body or {}).get("channel_ids"))
        if not chans:
            raise ActionError("Pick at least one channel that takes video.")
        steps = [{"key": k, "label": l, "status": "pending", "detail": "", "data": None} for k, l in STEPS]
        inp = {"sample": True, "name": sm["source"]["title"], "owns": True,
               "channels": [{"id": c["id"], "service": c["service"], "name": c.get("displayName") or c["name"]} for c in chans]}
        rid = self.store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                           (a.uid, "take", json.dumps(inp), "running", json.dumps(steps), json.dumps({}), _now(), _now()))
        res = {"sample": True, "source": {**sm["source"], "poster": self.sample_url(sm["source"]["poster"])},
               "analysis": sm["analysis"], "providers": sm["analysis"].get("providers", {}), "timings": sm["analysis"].get("timings", {})}
        self._step(steps, "upload", "done", f"Sample: {sm['source']['title']} ({sm['source']['credit']})", {"source": res["source"]})
        tm = res["timings"]
        self._step(steps, "transcribe", "done", f"{len(sm['analysis']['words'])} words with timestamps · {res['providers'].get('stt')}"
                   f" · {tm.get('transcribe', '?')} s on a 2-core CPU (precomputed for the sample)")
        res["moments"] = sm["moments"]
        res["picked_by"] = sm.get("picked_by", "built-in scorer")
        self._step(steps, "moments", "done", f"{len(sm['moments'])} moments · picked by {res['picked_by']}",
                   {"moments": [{k: m[k] for k in ("id", "start", "end", "hook", "score")} for m in sm["moments"]]})
        clips = []
        for c in sm["clips"]:
            m = next(x for x in sm["moments"] if x["id"] == c["moment_id"])
            clips.append({"id": c["id"], "moment_id": m["id"], "status": "ready", "start": m["start"], "end": m["end"], "hook": m["hook"],
                          "text": m["text"], "score": m.get("score"), "why": m.get("why", ""), "url": self.sample_url(c["file"]),
                          "cover_url": self.sample_url(c["cover"]), "meta": c["meta"], "captions": {}, "placements": [], "sample": True})
        res["clips"] = clips
        layouts = sorted({c["meta"].get("layout") for c in clips})
        self._step(steps, "reframe", "done", "Face-tracked 9:16 crop" if "track" in layouts else "Centre / blurred-pad layout")
        self._step(steps, "captions", "done", "Word-by-word captions in your brand font, clear of the bottom 20%")
        self._step(steps, "covers", "done", "A cover frame at 0.25 s (thumbnailOffset) and an end card on every clip")
        self._save(rid, steps, res)
        self.store.audit(a.uid, a.label, "clips_from_video", "read", {"take": rid, "source": "sample"})
        self._preflight_all(a, rid)
        return self.get(a, rid)

    # ------------------------------------------------------------------ worker jobs
    def _queue(self, a: Actor, rid: int, spec: dict, step: str, detail: str) -> None:
        spec = {**spec, "take": rid}
        jid = self.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                           (a.uid, rid, json.dumps(spec), "queued", _now(), _now()))
        run = self._run(a, rid)
        run["result"].setdefault("jobs", []).append(jid)
        mode = self._mode()
        if mode == "local":
            self._step(run["steps"], step, "running", detail.replace("on the video worker", "on this server"))
            self._save(rid, run["steps"], run["result"])
            self.h._spawn(self._local_job, jid)
            return
        if mode == "github":
            ok, why = self.h._dispatch_github(jid, workflow="take.yml", inputs={"kind": spec["kind"]}, ttl=5400)
            if ok:
                self._step(run["steps"], step, "running", detail + " (a GitHub Actions runner starts in about a minute)")
                self._save(rid, run["steps"], run["result"])
                return
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, _now(), jid))
            self._step(run["steps"], step, "failed", f"Couldn't start the video worker ({why}).")
        else:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", ("no worker", _now(), jid))
            self._step(run["steps"], step, "failed", "This server has no video worker (FFmpeg isn't installed and RENDER_WORKER isn't "
                                                     "set). Try the sample take, or self-host with FFmpeg.")
        self._save(rid, run["steps"], run["result"], "failed")

    def _job(self, jid: int) -> dict:
        rows = self.store.q("SELECT * FROM render_jobs WHERE id=?", (jid,))
        if not rows:
            raise ActionError("No such job.", 404)
        j = dict(rows[0])
        j["spec"] = json.loads(j["spec"])
        return j

    def job_payload(self, jid: int) -> dict:
        """What the worker fetches: the spec plus the URLs it downloads (source, logo)."""
        j = self._job(jid)
        spec = dict(j["spec"])
        spec.pop("_job_token", None)
        base = self.s.public_url.rstrip("/") + "/m/"
        return {"id": jid, "status": j["status"], "spec": spec,
                "urls": {"source": spec.get("source_url") or (base + spec["source"] if spec.get("source") else ""),
                         "logo": base + spec["logo"] if spec.get("logo") else ""}}

    def _local_job(self, jid: int) -> None:
        """Self-host / CI: run the worker's job in-process (FFmpeg on PATH)."""
        j = self._job(jid)
        spec = j["spec"]
        tmp = Path(tempfile.mkdtemp(prefix="hq_takejob_"))
        try:
            src = tmp / "source.mp4"
            if spec.get("source"):
                m = self.h.get_media(spec["source"])
                if not m:
                    raise take.TakeError("The uploaded video is gone.")
                src.write_bytes(m["data"])
            else:
                data = self.h._fetch_bytes(spec["source_url"], int(os.environ.get("HQ_MAX_TAKE_BYTES", str(1500 * 1024 * 1024))))
                if not data:
                    raise take.TakeError("Couldn't download that video link (public https, up to 1.5 GB).")
                src.write_bytes(data)
            if spec["kind"] == "take_analyze":
                analysis = take.analyze(str(src), spec.get("model", "small"), max_secs=MAX_TAKE_SECS)
                self.on_analysis(jid, analysis, "")
            else:
                logo = self.h.get_media(spec["logo"])["data"] if spec.get("logo") and self.h.get_media(spec["logo"]) else None
                for c in spec["clips"]:
                    data, cover, meta = take.render_clip(str(src), c, c["words"], c["faces"], spec.get("scenes") or [], spec["src"],
                                                         spec["kit"], spec["params"], logo=logo)
                    self.on_clip(jid, c["id"], data, cover, meta)
                self.on_render_done(jid, "")
        except Exception as e:  # noqa: BLE001 - every failure is reported on the take, never left spinning
            traceback.print_exc()
            if spec["kind"] == "take_analyze":
                self.on_analysis(jid, None, f"{e}")
            else:
                self.on_render_done(jid, f"{e}")
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    # ------------------------------------------------------------------ callbacks (worker or local)
    def on_analysis(self, jid: int, analysis: dict | None, error: str = "") -> dict:
        j = self._job(jid)
        a, rid = Actor(j["user_id"]), j["run_id"]
        run = self._run(a, rid)
        steps, res = run["steps"], run["result"]
        if error or not analysis:
            self.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", ((error or "no analysis")[:500], _now(), jid))
            self._step(steps, "transcribe", "failed", (error or "The worker returned no transcript.")[:300])
            self._save(rid, steps, res, "failed")
            return {"ok": False}
        self.store.x("UPDATE render_jobs SET status='done', updated=? WHERE id=?", (_now(), jid))
        res["analysis"] = analysis
        res["providers"], res["timings"] = analysis.get("providers", {}), analysis.get("timings", {})
        pr = analysis.get("probe") or {}
        res["source"] = {"duration": pr.get("duration"), "width": pr.get("width"), "height": pr.get("height"),
                         "title": run["input"].get("name") or "Your take"}
        self._step(steps, "transcribe", "done", f"{len(analysis.get('words') or [])} words in {res['timings'].get('transcribe', '?')} s · "
                   f"{res['providers'].get('stt', '')}; {len(analysis.get('scenes') or [])} scene cuts; "
                   f"{len(analysis.get('silences') or [])} pauses")
        self._step(steps, "moments", "running", "Scoring every stretch of the talk")
        self._save(rid, steps, res)
        eng = llm.resolve(self.store, a.uid, self.s)
        try:
            mined = take.mine_moments(analysis, run["input"].get("count"), eng,
                                      lambda e, sy, us: llm.chat_json(e, sy, us, http=self.h.http, max_tokens=1500))
        except take.TakeError as e:
            self._step(steps, "moments", "failed", str(e))
            self._save(rid, steps, res, "failed")
            return {"ok": False}
        if eng:
            self.h._ai_audit(a, eng, "moments")
        res["moments"], res["picked_by"] = mined["moments"], mined["picked_by"]
        self._step(steps, "moments", "done", f"{len(mined['moments'])} moments from {mined['sentences']} sentences · picked by {mined['picked_by']}",
                   {"moments": [{k: m[k] for k in ("id", "start", "end", "hook", "score")} for m in mined["moments"]]})
        res["clips"] = [{"id": f"c{i + 1}", "moment_id": m["id"], "status": "rendering", "start": m["start"], "end": m["end"],
                         "hook": m["hook"], "text": m["text"], "score": m["score"], "why": m.get("why", ""), "captions": {},
                         "placements": []} for i, m in enumerate(mined["moments"])]
        self._save(rid, steps, res)
        self._render(a, rid, [c["id"] for c in res["clips"]])
        return {"ok": True}

    def _render(self, a: Actor, rid: int, clip_ids: list[str]) -> None:
        run = self._run(a, rid)
        res = run["result"]
        an = res["analysis"]
        kit, rules = self.h.kit(a), self.h.rules(a)
        p = brand.visual_params(kit, rules)
        clips = []
        for c in res["clips"]:
            if c["id"] not in clip_ids:
                continue
            c["status"] = "rendering"
            clips.append({"id": c["id"], "start": c["start"], "end": c["end"], "hook": c["hook"],
                          "words": take.words_in(an["words"], c["start"], c["end"]),
                          "faces": [f for f in an.get("faces") or [] if c["start"] - 1 <= f["t"] <= c["end"] + 1]})
        for k in ("reframe", "captions", "covers"):
            self._step(run["steps"], k, "running", f"0 of {len(clips)} clips")
        self._save(rid, run["steps"], res, "rendering")
        spec = {"kind": "take_render", "source": run["input"].get("media_token") or "", "source_url": run["input"].get("url") or "",
                "kit": {k: kit[k] for k in brand.DEFAULT_KIT}, "params": p, "logo": kit.get("logo") or "",
                "scenes": an.get("scenes") or [], "src": an["probe"], "clips": clips}
        self._queue(a, rid, spec, "reframe", f"Rendering {len(clips)} clips on the video worker")

    def on_clip(self, jid: int, clip_id: str, data: bytes, cover: bytes, meta: dict) -> dict:
        with self._lock:
            j = self._job(jid)
            a, rid = Actor(j["user_id"]), j["run_id"]
            run = self._run(a, rid)
            steps, res = run["steps"], run["result"]
            clip = next((c for c in res.get("clips", []) if c["id"] == clip_id), None)
            if not clip:
                raise ActionError("No such clip.", 404)
            tok = self.h.put_media(a, data, f"clip-{clip_id}.mp4", kind="render", meta=meta)
            ctok = self.h.put_media(a, cover, f"cover-{clip_id}.jpg", kind="render", meta={"type": "photo", "cover": True})
            clip.update(status="rendered", token=tok, url=self.media_url(tok), cover_token=ctok, cover_url=self.media_url(ctok),
                        meta=meta, rendered_at=_now())
            done = sum(1 for c in res["clips"] if c["status"] in ("rendered", "ready"))
            todo = len(j["spec"].get("clips") or [])
            got = sum(1 for c in res["clips"] if c["id"] in {x["id"] for x in j["spec"].get("clips") or []} and c["status"] == "rendered")
            for k in ("reframe", "captions", "covers"):
                self._step(steps, k, "running", f"{got} of {todo} clips")
            del done
            self._save(rid, steps, res)
            return {"ok": True, "token": tok}

    def on_render_done(self, jid: int, error: str = "") -> dict:
        j = self._job(jid)
        a, rid = Actor(j["user_id"]), j["run_id"]
        run = self._run(a, rid)
        steps, res = run["steps"], run["result"]
        ids = {x["id"] for x in j["spec"].get("clips") or []}
        failed = [c for c in res.get("clips", []) if c["id"] in ids and c["status"] == "rendering"]
        self.store.x("UPDATE render_jobs SET status=?, error=?, updated=? WHERE id=?",
                     ("failed" if error and failed else "done", (error or "")[:500], _now(), jid))
        for c in failed:
            c["status"], c["error"] = "failed", (error or "The worker didn't return this clip.")[:300]
        ok = [c for c in res.get("clips", []) if c.get("url")]
        if not ok:
            for k in ("reframe", "captions", "covers"):
                self._step(steps, k, "failed", (error or "No clip rendered.")[:300])
            self._save(rid, steps, res, "failed")
            return {"ok": False}
        layouts = [c["meta"].get("layout") for c in ok if c.get("meta")]
        cov = [c["meta"].get("face_coverage") for c in ok if c.get("meta", {}).get("layout") == "track"]
        self._step(steps, "reframe", "done", (f"Face-tracked on {layouts.count('track')} of {len(ok)} clips"
                                              + (f" (face kept in frame {min(cov) * 100:.0f}%+ of the time)" if cov else "")
                                              + (f"; blurred-pad layout on {layouts.count('pad')}" if layouts.count("pad") else "")))
        self._step(steps, "captions", "done", "Word-by-word captions in your brand font, clear of the bottom 20%")
        self._step(steps, "covers", "done", "A branded cover frame (thumbnailOffset) and your end card on every clip"
                   + (f" · {len(failed)} clip(s) failed: {failed[0]['error'][:120]}" if failed else ""))
        self._save(rid, steps, res)
        self._preflight_all(a, rid)
        return {"ok": True}

    # ------------------------------------------------------------------ preflight + captions
    def _clip_options(self, service: str, clip: dict) -> dict:
        o = {}
        if service == "instagram":
            o["ig_type"] = "reel"
        if service == "youtube":
            o["title"] = (clip.get("hook") or "").rstrip("…")[:100]
            o["category_id"] = "22"
        if service in THUMB_OFFSET and clip.get("meta", {}).get("cover_offset_ms") is not None:
            o["thumbnail_offset_ms"] = int(clip["meta"]["cover_offset_ms"])
        return o

    def caption(self, a: Actor, clip: dict, service: str, kit: dict | None = None, rules: list | None = None) -> str:
        if clip.get("captions", {}).get(service):
            return clip["captions"][service]
        if clip.get("captions", {}).get("*"):
            text = clip["captions"]["*"]
        else:
            text = take.caption_for(service, clip, kit or self.h.kit(a))
        return drafter.fit(service, brand.apply_text_rules(text, rules if rules is not None else self.h.rules(a)))

    def _preflight_all(self, a: Actor, rid: int) -> None:
        run = self._run(a, rid)
        steps, res = run["steps"], run["result"]
        self._step(steps, "preflight", "running", "Checking every clip against each channel's rules")
        self._save(rid, steps, res)
        chans = {c["id"]: c for c in self.studio.channels(a)}
        kit, rules = self.h.kit(a), self.h.rules(a)
        worst = {"ok": 0, "warning": 0, "error": 0}
        for clip in res.get("clips", []):
            if not clip.get("url"):
                continue
            clip["preflight"] = {}
            for ch in run["input"]["channels"]:
                c = chans.get(ch["id"])
                if not c:
                    continue
                pf = self.studio._preflight(c, self.caption(a, clip, c["service"], kit, rules), [clip["url"]], "draft",
                                            self._clip_options(c["service"], clip))
                clip["preflight"][c["id"]] = {k: pf[k] for k in ("status", "chip", "findings", "service", "network")}
                worst[pf["status"]] += 1
            clip["status"] = "ready"
        n = len([c for c in res.get("clips", []) if c.get("url")])
        msg = f"{n} clips × {len(run['input']['channels'])} channel(s): {worst['ok']} pass" + \
              (f", {worst['warning']} warning(s)" if worst["warning"] else "") + (f", {worst['error']} need a fix" if worst["error"] else "")
        self._step(steps, "preflight", "warn" if worst["error"] else "done", msg)
        self._step(steps, "ready", "done", "Spread them into the open slots of your week, as Buffer drafts")
        self._save(rid, steps, res, "ready")

    # ------------------------------------------------------------------ reads
    def get(self, a: Actor, rid: int) -> dict:
        run = self._run(a, rid)
        res = run["result"] or {}
        an = res.get("analysis") or {}
        words = an.get("words") or []
        kit = self.h.kit(a)
        rules = self.h.rules(a)
        clips = []
        for c in res.get("clips", []):
            d = {k: c.get(k) for k in ("id", "moment_id", "status", "start", "end", "hook", "text", "score", "why", "url", "cover_url",
                                       "error", "placements", "preflight", "captions")}
            m = c.get("meta") or {}
            d["duration"] = m.get("duration") or round((c["end"] - c["start"]) + take.COVER_SECS + take.END_SECS, 2)
            d["layout"], d["face_coverage"] = m.get("layout"), m.get("face_coverage")
            d["cover_offset_ms"] = m.get("cover_offset_ms")
            d["words"] = [w for w in words if c["start"] - 4 <= w["s"] <= c["end"] + 4]
            d["post_captions"] = {ch["service"]: self.caption(a, c, ch["service"], kit, rules) for ch in run["input"]["channels"]}
            clips.append(d)
        return {"id": run["id"], "kind": "take", "status": run["status"], "steps": run["steps"], "input": run["input"],
                "sample": bool(res.get("sample")), "source": res.get("source") or {}, "providers": res.get("providers") or {},
                "timings": res.get("timings") or {}, "picked_by": res.get("picked_by"), "moments": res.get("moments") or [],
                "clips": clips, "created": run["created"], "updated": run["updated"],
                "can_rerender": not res.get("sample") and self._mode() != "none"}

    def list(self, a: Actor, limit: int = 20) -> list[dict]:
        out = []
        for r in self.store.q("SELECT id,input,status,result,created FROM agent_runs WHERE user_id=? AND kind='take' ORDER BY id DESC LIMIT ?",
                              (a.uid, limit)):
            inp, res = json.loads(r["input"]), json.loads(r["result"] or "{}")
            clips = res.get("clips") or []
            out.append({"id": r["id"], "status": r["status"], "title": inp.get("name") or (res.get("source") or {}).get("title") or "Your take",
                        "sample": bool(inp.get("sample")), "clips": len(clips), "ready": sum(1 for c in clips if c["status"] == "ready"),
                        "placed": sum(1 for c in clips for p in c.get("placements") or [] if p.get("status") in ("draft", "pending_approval")),
                        "cover": next((c.get("cover_url") for c in clips if c.get("cover_url")), ""), "created": r["created"]})
        return out

    # ------------------------------------------------------------------ edit one clip (trim / caption / hook)
    def adjust(self, a: Actor, rid: int, clip_id: str, body: dict) -> dict:
        run = self._run(a, rid)
        if run["status"] in ("running", "rendering"):
            raise ActionError("This take is still being worked on. Try again when it's ready.", 409)
        res = run["result"]
        clip = next((c for c in res.get("clips", []) if c["id"] == clip_id), None)
        if not clip:
            raise ActionError("No such clip in this take.", 404)
        changed, rerender = [], False
        if "caption" in body and body["caption"] is not None:
            cap = str(body["caption"]).strip()[:5000]
            svc = (body.get("service") or "*").strip() or "*"
            clip.setdefault("captions", {})
            if cap:
                clip["captions"][svc] = cap
            else:
                clip["captions"].pop(svc, None)
            changed.append("caption")
        words = (res.get("analysis") or {}).get("words") or []
        dur = float((res.get("analysis") or {}).get("probe", {}).get("duration") or 0)
        if body.get("start_ms") is not None or body.get("end_ms") is not None:
            s = float(body.get("start_ms", clip["start"] * 1000)) / 1000
            e = float(body.get("end_ms", clip["end"] * 1000)) / 1000
            s, e = max(0.0, s), min(dur or e, e)
            if e - s < 3 or e - s > 90:
                raise ActionError("A clip must be 3 to 90 seconds long.")
            if abs(s - clip["start"]) > 0.04 or abs(e - clip["end"]) > 0.04:
                clip["start"], clip["end"] = round(s, 2), round(e, 2)
                clip["text"] = " ".join(w["w"] for w in take.words_in(words, s, e))
                rerender = True
                changed.append("trim")
        if body.get("hook") is not None:
            hook = str(body["hook"]).strip()[:120]
            if not take.verbatim(hook, clip.get("text") or ""):
                raise ActionError("The hook has to be a line said in the clip (extract, don't invent). Pick at least 3 words "
                                  "from its transcript.")
            if hook != clip.get("hook"):
                clip["hook"] = hook
                rerender = True
                changed.append("hook")
        if not changed:
            raise ActionError("Nothing to change: give caption, hook, start_ms or end_ms.")
        if rerender and (res.get("sample") or self._mode() == "none"):
            raise ActionError("Trimming and new hooks re-render the clip on the video worker, which "
                              + ("the sample workspace doesn't use. Caption edits work here." if res.get("sample")
                                 else "this server doesn't have. Caption edits still work."), 409)
        if clip.get("placements"):
            clip["placements_before_edit"] = clip["placements"]
        self._save(rid, run["steps"], res)
        self.store.audit(a.uid, a.label, "adjust_clip", "read", {"take": rid, "clip": clip_id, "changed": changed})
        if rerender:
            clip["status"] = "rendering"
            self._save(rid, run["steps"], res, "rendering")
            self._render(a, rid, [clip_id])
        else:
            self._preflight_all(a, rid)
        return self.get(a, rid)

    # ------------------------------------------------------------------ The Spread: place clips in the week (drafts only)
    def _plan(self, a: Actor, run: dict, channel_ids: list[str] | None, week_of: str | None, clip_ids: list[str] | None) -> dict:
        res = run["result"]
        clips = [c for c in res.get("clips", []) if c.get("url") and c["status"] == "ready" and (not clip_ids or c["id"] in clip_ids)]
        if not clips:
            raise ActionError("No ready clips to place yet.")
        allowed = {c["id"] for c in run["input"]["channels"]}
        ids = [c for c in (channel_ids or list(allowed)) if c in allowed] or list(allowed)
        chans = {c["id"]: c for c in self.studio.channels(a)}
        ids = [i for i in ids if i in chans]
        if not ids:
            raise ActionError("None of this take's channels are available to this caller.", 404)
        u = self.store.user(a.uid)
        sched = self.studio.buffer.posting_schedules(a.uid, u["org_id"])
        tz = next((s.get("timezone") for s in sched if s.get("id") in ids and s.get("timezone")), None) or "UTC"
        monday = take.week_start(week_of, tz)
        busy = []
        for st in ("scheduled", "draft"):
            try:
                busy += self.studio.buffer.posts(a.uid, u["org_id"], [st], first=100)
            except Exception:  # noqa: BLE001
                pass
        slots = take.open_slots(sched, busy, ids, monday)
        # Buffer's daily posting limits for the days we would use (drafts don't count today; we keep room anyway)
        room: dict[str, dict[str, int]] = {}
        days = sorted({s["day"] for v in slots.values() for s in v})
        for d in days:
            try:
                lim = self.studio.buffer.daily_limits(a.uid, ids, f"{d}T12:00:00.000Z")
            except Exception:  # noqa: BLE001
                lim = []
            for st in lim or []:
                if st.get("limit") is not None:
                    room.setdefault(st["channelId"], {})[d] = max(0, int(st["limit"]) - int(st.get("scheduled") or 0) - int(st.get("sent") or 0))
        plan = take.plan_spread([c["id"] for c in clips], slots, room)
        kit, rules = self.h.kit(a), self.h.rules(a)
        byid = {c["id"]: c for c in clips}
        for p in plan["placements"]:
            ch, c = chans[p["channel_id"]], byid[p["clip_id"]]
            p["service"], p["channel_name"] = ch["service"], ch.get("displayName") or ch["name"]
            p["text"] = self.caption(a, c, ch["service"], kit, rules)
            p["media_url"], p["cover_url"], p["hook"] = c["url"], c.get("cover_url"), c["hook"]
            p["options"] = self._clip_options(ch["service"], c)
            p["preflight"] = (c.get("preflight") or {}).get(ch["id"], {}).get("status", "ok")
        return {"week_of": monday.isoformat(), "timezone": tz, "channel_ids": ids, **plan,
                "slots_open": {k: len(v) for k, v in slots.items()}, "suggested_times": any(s["suggested"] for v in slots.values() for s in v)}

    def place(self, a: Actor, rid: int, body: dict) -> dict:
        run = self._run(a, rid)
        if run["status"] != "ready":
            raise ActionError("This take isn't ready yet.", 409)
        if run["result"].get("sample") and not demo.is_demo(a.uid):
            raise ActionError("The sample take can only be spread in the sample workspace (/demo).", 403)
        token = (body.get("confirm_token") or "").strip()
        if not token:
            if body.get("dry_run") is False and a.kind == "agent":
                raise ActionError("Agents place clips in two steps: call with dry_run first, show the plan, then confirm_token.", 400)
            plan = self._plan(a, run, body.get("channel_ids"), body.get("week_of"), body.get("clip_ids"))
            ct = "cf_" + secrets.token_urlsafe(24)
            self.store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires) VALUES(?,?,?,?,?)",
                         (sha(ct), a.uid, a.agent["id"] if a.agent else None, json.dumps({"kind": "spread", "take": rid, "plan": plan}),
                          _now() + CONFIRM_TTL))
            self.store.audit(a.uid, a.label, "place_clips_in_week", "read", {"take": rid, "dry_run": True,
                                                                             "placements": len(plan["placements"])})
            what = "creates Buffer drafts at these times" if a.kind == "human" or a.agent["policy"] != "review" else \
                "waits in the Approvals inbox; once approved each becomes a Buffer draft"
            out = {"dry_run": True, "plan": plan, "confirm_token": ct, "expires_in": CONFIRM_TTL, "on_confirm": what,
                   "buffer_requests_est": len(plan["placements"])}
            if body.get("dry_run") is False:  # a human asked for it in one go
                return self.place(a, rid, {"confirm_token": ct})
            return out
        rows = self.store.q("SELECT * FROM confirms WHERE token_hash=? AND user_id=?", (sha(token), a.uid))
        if not rows:
            raise ActionError("Unknown confirm_token. Run place_clips_in_week with dry_run first.", 404)
        row = rows[0]
        if row["used"] or row["expires"] < _now():
            raise ActionError("This confirm_token is used or expired. Plan again.", 410)
        if (row["agent_id"] or None) != (a.agent["id"] if a.agent else None):
            raise ActionError("This confirm_token belongs to a different caller.", 403)
        payload = json.loads(row["payload"])
        if payload.get("kind") != "spread" or payload.get("take") != rid:
            raise ActionError("That confirm_token is for something else.", 400)
        self.store.x("UPDATE confirms SET used=1 WHERE token_hash=?", (row["token_hash"],))
        plan = payload["plan"]
        results = []
        for p in plan["placements"]:
            item = {k: p[k] for k in ("clip_id", "channel_id", "service", "at", "local", "channel_name")}
            try:
                if datetime.fromisoformat(p["at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc) + timedelta(minutes=2):
                    raise ActionError("That slot has passed. Plan again.")
                prev = self.studio.preview_post(a, {"channel_id": p["channel_id"], "text": p["text"], "media_url": p["media_url"],
                                                    "mode": "draft", "due_at": p["at"], "options": p["options"],
                                                    "override_warnings": bool(body.get("override_warnings"))})
                out = self.studio.submit_post(a, prev["confirm_token"])
                st = out["status"]
                item.update(status="draft" if st == "sent_to_buffer" else st, buffer_post_id=(out.get("post") or {}).get("id"),
                            approval_id=out.get("approval_id"))
            except Exception as e:  # noqa: BLE001 - one bad placement never blocks the others
                item.update(status="failed", error=str(e)[:300])
            results.append(item)
        run = self._run(a, rid)
        for c in run["result"].get("clips", []):
            mine = [r for r in results if r["clip_id"] == c["id"]]
            if mine:
                c["placements"] = [x for x in c.get("placements") or [] if x["channel_id"] not in {m["channel_id"] for m in mine}] + mine
        self._save(rid, run["steps"], run["result"])
        okn = sum(1 for r in results if r["status"] in ("draft", "pending_approval"))
        self.store.audit(a.uid, a.label, "place_clips_in_week", "draft", {"take": rid, "placed": okn, "failed": len(results) - okn})
        return {"dry_run": False, "placed": okn, "failed": len(results) - okn, "results": results, "week_of": plan["week_of"],
                "unplaced": plan.get("unplaced", []),
                "message": f"{okn} clip post(s) saved as Buffer drafts at your posting times. Nothing is published until you "
                           "schedule them." if a.kind == "human" else f"{okn} placement(s) submitted under this token's policy."}


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode()
