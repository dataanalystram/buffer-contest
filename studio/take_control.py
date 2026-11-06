"""One take: Stop a running take, Try again on a stopped one, and keep takes alive across server restarts (owner, 6 Nov 2026:
"why is One take failing, it used to work"; "when one take is running give the option to stop it").

Run #80 was marked "Interrupted, Stopped: the server restarted" while its video worker was still working. The cut-off-run
sweep for the chat agent (agent_brain.Brain.interrupt_orphans) read every running run, takes included, and a take whose
heavy step runs on the video worker (GitHub Actions / InstaBox) doesn't touch its run row for minutes, so after 90 s it
looked orphaned. A take never depends on the web process while a worker job is open: the worker calls back, and the job
watchdog (House.sweep_stale_jobs) already fails a worker that goes silent. So takes have their own recovery here:

* an open worker job (remote): left alone; the worker's callbacks and the job watchdog decide.
* an open in-process job (HQ mode local) whose process is gone (a deploy, a crash): run again by this process, at most
  LOCAL_RETRIES times, then the take is interrupted with Try again.
* no open job and silent for IDLE_S: an in-process step (picking moments, preflight) was cut off -> interrupted, Try again.

Stop (stop_take) cancels the take's open jobs (their tokens are burned, so late worker calls are refused and nothing lands)
and keeps what's made. Try again (retry_take) carries on from what's kept: clips still to render are rendered, otherwise the
video is read again. UI = REST = MCP: the run bar's Stop / Try again, POST /api/takes/{id}/stop|retry, stop_take / retry_take.
"""
from __future__ import annotations

import json
import os
import time
import traceback

from .actions import ActionError, Actor
from .take_bin import _int_id

LOCAL_RETRIES = 2
IDLE_S = int(os.environ.get("HQ_TAKE_IDLE_S", str(20 * 60)))
BUSY = ("running", "rendering")
RETRYABLE = ("interrupted", "failed", "stopped")
STOPPED_BY_YOU = "Stopped by you"
CUT_OFF = "Stopped: the server restarted"
LATER = ("transcribe", "moments", "reframe", "captions", "covers", "preflight", "ready")
RENDER_STEPS = ("reframe", "captions", "covers", "preflight", "ready")

TOOLS = [
    {"name": "stop_take", "description": "Stop a One take that is running (reading the video, picking moments or rendering). What it "
     "already made is kept; retry_take carries on. Same as the Stop button on the take and POST /api/takes/{id}/stop.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"}}}},
    {"name": "retry_take", "description": "Try a stopped, interrupted or failed One take again from what it kept: clips not rendered yet "
     "are rendered, otherwise the video is read again. Same as Try again on the take and POST /api/takes/{id}/retry.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"}}}},
]
READ_ONLY: set[str] = set()
NAMES = {t["name"] for t in TOOLS}


def _now() -> float:
    return time.time()


def _spec(raw) -> dict:
    try:
        s = json.loads(raw or "{}")
    except ValueError:
        return {}
    return s if isinstance(s, dict) else {}


class TakeControl:
    """Mixed into takes.Takes (self.h, self.store, _run, _save, _step, _queue, _render, _local_job, _take_lock)."""

    # ------------------------------------------------------------------ stop
    def stop_take(self, a: Actor, rid) -> dict:
        self._guard(a, "stop_take")
        rid = _int_id(rid)
        with self._take_lock(rid):
            run = self._run(a, rid)
            if run["status"] not in BUSY:
                raise ActionError("This take isn't running.", 409)
            n = self._cancel_open_jobs(run["id"], a.uid, "stopped by the owner")
            steps, res = run["steps"], run["result"]
            for st in steps:
                if st.get("status") in ("running", "pending"):
                    st["status"], st["detail"] = "failed", STOPPED_BY_YOU
            for c in res.get("clips") or []:
                if c.get("status") == "rendering":
                    if c.get("url"):
                        c["status"] = "rendered"
                    else:
                        c["status"], c["error"] = "failed", STOPPED_BY_YOU
            res["stopped"] = {"at": _now()}
            self._save(run["id"], steps, res, "stopped")
        self.store.audit(a.uid, a.label, "stop_take", "write", {"take": run["id"], "cancelled_jobs": n})
        return {**self.get(a, run["id"]), "cancelled_jobs": n}

    def _cancel_open_jobs(self, rid: int, uid: str, why: str) -> int:
        jobs = [r["id"] for r in self.store.q("SELECT id FROM render_jobs WHERE run_id=? AND user_id=? AND status='queued'", (rid, uid))]
        for jid in jobs:
            self.store.x("UPDATE render_jobs SET status='cancelled', error=?, updated=? WHERE id=? AND status='queued'",
                         (f"cancelled: {why}"[:500], _now(), jid))
            try:
                self.h.burn_job_token(jid)
            except Exception:  # noqa: BLE001 - cancelled either way; the callbacks check the job's status too
                pass
        return len(jobs)

    # ------------------------------------------------------------------ try again
    def retry_take(self, a: Actor, rid) -> dict:
        self._guard(a, "retry_take")
        rid = _int_id(rid)
        with self._take_lock(rid):
            run = self._run(a, rid)
            if run["status"] not in RETRYABLE:
                raise ActionError("Only a stopped, interrupted or failed take can be tried again.", 409)
            res = run["result"]
            if res.get("sample"):
                raise ActionError("The sample take can't be run again. Start it fresh from One take.", 409)
            if self._mode() == "none":
                raise ActionError("This server has no video worker, so the take can't run again here.", 409)
            for k in ("stopped", "interrupted", "error"):
                res.pop(k, None)
            steps = run["steps"]
            clips = [c for c in res.get("clips") or [] if c.get("status") != "binned"]
            todo = [c["id"] for c in clips if not c.get("url")]
            if res.get("analysis") and clips:
                if not todo:  # everything rendered: only the last steps were cut off
                    for st in steps:
                        if st.get("status") in ("failed", "running", "pending") and st.get("key") in RENDER_STEPS:
                            st["status"], st["detail"] = "done", ""
                    self._save(run["id"], steps, res, "running")
                    how = "finish"
                else:
                    for c in clips:
                        if c["id"] in todo:
                            c["status"] = "rendering"
                            c.pop("error", None)
                    for st in steps:
                        if st.get("key") in RENDER_STEPS:
                            st["status"], st["detail"] = "pending", ""
                    self._save(run["id"], steps, res, "running")
                    how = "render"
            else:
                spec = self._first_analyze_spec(run["id"])
                if spec is None:
                    raise ActionError("This take's video can't be read again. Start a new take with the same video.", 409)
                for k in ("analysis", "moments", "clips", "picked_by", "providers", "timings"):
                    res.pop(k, None)
                for st in steps:
                    if st.get("key") in LATER:
                        st["status"], st["detail"] = "pending", ""
                self._save(run["id"], steps, res, "running")
                how = "analyze"
            if how == "render":
                self._render(a, run["id"], todo)
            elif how == "finish":
                self._preflight_all(a, run["id"])
            else:
                self._queue(a, run["id"], spec, "transcribe", "Reading the video again on the video worker")
        self.store.audit(a.uid, a.label, "retry_take", "write", {"take": run["id"], "how": how})
        return {**self.get(a, run["id"]), "retried": how}

    def _first_analyze_spec(self, rid: int) -> dict | None:
        for r in self.store.q("SELECT spec FROM render_jobs WHERE run_id=? ORDER BY id", (rid,)):
            s = _spec(r["spec"])
            if s.get("kind") == "take_analyze":
                return {k: v for k, v in s.items() if not k.startswith("_") and k != "take"}
        return None

    # ------------------------------------------------------------------ restarts
    def recover_orphans(self, live: set[str], boot: str, now: float | None = None) -> dict:
        """Called by the agent heartbeat loop with the process ids that are still beating. Returns what it did."""
        now = now or _now()
        out = {"rerun": [], "interrupted": []}
        for r in self.store.q("SELECT id, user_id, updated FROM agent_runs WHERE kind='take' AND status IN ('running','rendering')"):
            rid = r["id"]
            try:
                jobs = self.store.q("SELECT id, spec, updated FROM render_jobs WHERE run_id=? AND status='queued' ORDER BY id", (rid,))
                if jobs:
                    for j in jobs:
                        s = _spec(j["spec"])
                        jb = s.get("_boot")
                        if not jb or jb in live:  # a worker's job (it calls back; the job watchdog covers it) or ours
                            continue
                        tries = int(s.get("_retries") or 0)
                        if tries >= LOCAL_RETRIES:
                            self._cut_off(rid, r["user_id"], now)
                            out["interrupted"].append(rid)
                            break
                        s["_boot"], s["_retries"] = boot, tries + 1
                        if not self.store.q("UPDATE render_jobs SET spec=?, updated=? WHERE id=? AND status='queued' AND spec=? RETURNING id",
                                            (json.dumps(s), now, j["id"], j["spec"])):
                            continue  # another process took it first
                        self.h._spawn(self._local_job, j["id"])
                        out["rerun"].append(j["id"])
                    continue
                if (r["updated"] or 0) < now - IDLE_S:
                    self._cut_off(rid, r["user_id"], now)
                    out["interrupted"].append(rid)
            except Exception:  # noqa: BLE001 - one bad take never stops the others' recovery
                traceback.print_exc()
        if out["rerun"] or out["interrupted"]:
            print(f"takes: after a restart re-ran local jobs {out['rerun']}, interrupted takes {out['interrupted']}", flush=True)
        return out

    def _cut_off(self, rid: int, uid: str, now: float) -> None:
        with self._take_lock(rid):
            rows = self.store.q("SELECT status, steps, result FROM agent_runs WHERE id=? AND user_id=?", (rid, uid))
            if not rows or rows[0]["status"] not in BUSY:
                return
            run = {"steps": json.loads(rows[0]["steps"] or "[]") or [], "result": json.loads(rows[0]["result"] or "{}") or {}}
            self._cancel_open_jobs(rid, uid, "the server restarted")
            steps, res = run["steps"], run["result"]
            for st in steps:
                if st.get("status") in ("running", "pending"):
                    st["status"], st["detail"] = "failed", CUT_OFF
            for c in res.get("clips") or []:
                if c.get("status") == "rendering":
                    c["status"] = "rendered" if c.get("url") else "failed"
                    if not c.get("url"):
                        c["error"] = CUT_OFF
            res["interrupted"] = {"at": now}
            self._save(rid, steps, res, "interrupted")


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    t = studio.house.takes
    if name == "stop_take":
        return t.stop_take(actor, args.get("take_id"))
    return t.retry_take(actor, args.get("take_id"))
