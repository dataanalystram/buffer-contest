"""Delete a One take, or one of its clips, into the Bin (owner ask, 4 Nov 2026). The same Bin model as the Library:

* Delete moves it to the Bin for TRASH_DAYS (7): it leaves One take at once, its files stop being served (their media rows
  keep their bytes under the ``trash~`` prefix, so ``/m/<token>`` answers 404) and any job still running for it is
  cancelled (the job is marked cancelled and its worker token burned, so the worker's next call is refused and nothing it
  sends lands on the take).
* Restore brings the take (its status, its clips' files) or the clip back.
* Purge (Empty bin, or 7 days later from the media check loop) removes the records and every stored file at once, in one
  transaction: clip and cover media rows (live or binned), variant renders, the uploaded source on disk, the take's
  render jobs and the run. The tokens are noted in ``library_purged`` as deleted by their owner, so /healthz never counts
  them as missing files.

Scope before existence: someone else's take id and a missing one are the same 404, and nothing in an answer names
another person's objects.
"""
from __future__ import annotations

import contextlib
import json
import time

from .actions import ActionError, Actor

# The Library's Bin (library.TRASH_DAYS / TRASH_TOKEN; test_take_delete_upload pins they match). Not imported: the video
# worker imports takes.py, and library.py needs FastAPI, which the worker's venv doesn't have.
TRASH_DAYS = 7
TRASH_TOKEN = "trash~"

TOOLS = [
    {"name": "delete_take", "description": "Delete a One take, or one clip of it (clip_id), into the Bin: it is restorable for 7 days "
     "(restore_take), then removed for good with its files. A job still rendering it is cancelled. Clips already saved as Buffer "
     "drafts stay in Buffer.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"},
                     "clip_id": {"type": "string", "description": "Only this clip (c1, c2, ...); omit to delete the whole take"}}}},
    {"name": "list_take_bin", "description": "One take's Bin: takes and clips deleted in the last 7 days, with days_left before each is removed for good.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "restore_take", "description": "Restore a take (or one clip of it, clip_id) from the Bin, with its files.",
     "inputSchema": {"type": "object", "required": ["take_id"], "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"}}}},
    {"name": "purge_take_bin", "description": "Delete takes / clips in One take's Bin permanently: take_ids, clips [{take_id, clip_id}], "
     "or all=true. Can't be undone; their files are removed.",
     "inputSchema": {"type": "object", "properties": {"take_ids": {"type": "array", "items": {"type": "integer"}},
                     "clips": {"type": "array", "items": {"type": "object", "required": ["take_id", "clip_id"],
                               "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"}}}},
                     "all": {"type": "boolean"}}}},
]
READ_ONLY = {"list_take_bin"}
NAMES = {t["name"] for t in TOOLS}
MAX_IDS = 200


def _now() -> float:
    return time.time()


def _int_id(v) -> int:
    if isinstance(v, bool):
        raise ActionError("No such take.", 404)
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ActionError("No such take.", 404)


def clip_tokens(c: dict) -> list[str]:
    """Every stored file of one clip: the video, its cover and its other-ratio renders."""
    toks = [c.get("token"), c.get("cover_token")] + [(v or {}).get("token") for v in (c.get("variants") or {}).values()]
    return [t.removeprefix(TRASH_TOKEN) for t in toks if t]


class TakeBin:
    """Mixed into takes.Takes (self.h = House, self.store, self.studio)."""

    # ------------------------------------------------------------------ helpers
    def _bin_row(self, a: Actor, rid, binned: bool | None = None) -> dict:
        rid = _int_id(rid)
        rows = self.store.q("SELECT * FROM agent_runs WHERE id=? AND user_id=? AND kind='take'", (rid, a.uid))
        if not rows or (binned is True and rows[0]["status"] != "binned") or (binned is False and rows[0]["status"] == "binned"):
            raise ActionError("No such take." if binned is not True else "That take isn't in the Bin.", 404)
        d = dict(rows[0])
        for k in ("input", "steps", "result"):
            d[k] = json.loads(d[k] or "null")
        d["result"] = d["result"] or {}
        return d

    def _move_tokens(self, uid: str, toks: list[str], to_bin: bool) -> None:
        for t in toks:
            old, new = (t, TRASH_TOKEN + t) if to_bin else (TRASH_TOKEN + t, t)
            self.store.x("UPDATE media SET token=? WHERE token=? AND user_id=?", (new, old, uid))

    def _cancel_jobs(self, rid: int, uid: str) -> int:
        jobs = [r["id"] for r in self.store.q("SELECT id FROM render_jobs WHERE run_id=? AND user_id=? AND status='queued'", (rid, uid))]
        for jid in jobs:
            self.store.x("UPDATE render_jobs SET status='cancelled', error=?, updated=? WHERE id=? AND status='queued'",
                         ("cancelled: the take was deleted", _now(), jid))
            try:
                self.h.burn_job_token(jid)
            except Exception:  # noqa: BLE001 - the job is cancelled either way; the callbacks check its status too
                pass
        return len(jobs)

    def job_cancelled(self, jid: int) -> bool:
        """A worker callback for a cancelled job, or for a take in the Bin, is dropped (never lands on the take)."""
        rows = self.store.q("SELECT j.status AS js, r.status AS rs FROM render_jobs j LEFT JOIN agent_runs r ON r.id=j.run_id "
                            "WHERE j.id=?", (jid,))
        return bool(rows) and (rows[0]["js"] == "cancelled" or rows[0]["rs"] == "binned")

    def _save_raw(self, rid: int, res: dict, status: str | None = None) -> None:
        if status:
            self.store.x("UPDATE agent_runs SET result=?, status=?, updated=? WHERE id=?", (json.dumps(res, default=str), status, _now(), rid))
        else:
            self.store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=?", (json.dumps(res, default=str), _now(), rid))

    def _guard(self, a: Actor, action: str) -> None:
        self.h.studio._guard_write(a, action)
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace is view-only. Connect Buffer to manage your own takes.", 403)

    # ------------------------------------------------------------------ delete
    def delete_take(self, a: Actor, rid, clip_id: str | None = None) -> dict:
        with self._take_lock(rid):  # the take's lock (takes._take_lock), the one its worker callbacks take
            return self._delete_take(a, rid, clip_id)

    def _delete_take(self, a: Actor, rid, clip_id: str | None = None) -> dict:
        self._guard(a, "delete_take")
        if clip_id:
            return self._delete_clip(a, rid, str(clip_id))
        with self.store.tx():
            run = self._bin_row(a, rid, binned=False)
            res = run["result"]
            toks = [t for c in res.get("clips") or [] if c.get("status") != "binned" for t in clip_tokens(c)]
            cancelled = self._cancel_jobs(run["id"], a.uid)
            self._move_tokens(a.uid, toks, True)
            res["bin"] = {"at": _now(), "status": run["status"], "tokens": toks}
            self._save_raw(run["id"], res, "binned")
        self.store.audit(a.uid, a.label, "delete_take", "safety", {"take": run["id"], "clips": len(res.get("clips") or []),
                                                                   "cancelled_jobs": cancelled, "trash_days": TRASH_DAYS})
        return {"deleted": True, "take_id": run["id"], "in_bin_days": TRASH_DAYS, "cancelled_jobs": cancelled,
                "restore": f"POST /api/takes/{run['id']}/restore"}

    def _delete_clip(self, a: Actor, rid, clip_id: str) -> dict:
        with self.store.tx():
            run = self._bin_row(a, rid, binned=False)
            res = run["result"]
            clip = next((c for c in res.get("clips") or [] if c.get("id") == clip_id and c.get("status") != "binned"), None)
            if not clip:
                raise ActionError("No such clip.", 404)
            if res.get("sample"):
                raise ActionError("The sample take's clips are bundled with the app; delete the whole take instead.", 409)
            toks = clip_tokens(clip)
            cancelled = 0
            if clip.get("status") == "rendering":  # the clip's own render job is cancelled only when it renders nothing else
                for r in self.store.q("SELECT id, spec FROM render_jobs WHERE run_id=? AND user_id=? AND status='queued'", (run["id"], a.uid)):
                    ids = {x.get("id") for x in (json.loads(r["spec"] or "{}").get("clips") or [])}
                    if ids == {clip_id}:
                        self.store.x("UPDATE render_jobs SET status='cancelled', error=?, updated=? WHERE id=? AND status='queued'",
                                     ("cancelled: the clip was deleted", _now(), r["id"]))
                        self.h.burn_job_token(r["id"])
                        cancelled += 1
            self._move_tokens(a.uid, toks, True)
            clip["bin"] = {"at": _now(), "status": clip.get("status"), "tokens": toks}
            clip["status"] = "binned"
            live = [c for c in res.get("clips") or [] if c.get("status") == "rendering"]
            self._save_raw(run["id"], res, None if live or run["status"] != "rendering" else "ready")
        self.store.audit(a.uid, a.label, "delete_take", "safety", {"take": run["id"], "clip": clip_id, "trash_days": TRASH_DAYS})
        return {"deleted": True, "take_id": run["id"], "clip_id": clip_id, "in_bin_days": TRASH_DAYS, "cancelled_jobs": cancelled,
                "restore": f"POST /api/takes/{run['id']}/clips/{clip_id}/restore"}

    # ------------------------------------------------------------------ the Bin
    def take_bin(self, a: Actor) -> dict:
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            return {"takes": [], "clips": [], "days": TRASH_DAYS}
        now, takes_, clips_ = _now(), [], []

        def left(at):
            sec = max(0.0, (at or 0) + TRASH_DAYS * 86400 - now)
            return int(-(-sec // 86400)) if sec else 0
        # only the rows with something in the Bin (the same filter as the 7-day sweep), however old or many the takes are
        for r in self.store.q("SELECT id,status,input,result,created FROM agent_runs WHERE user_id=? AND kind='take' "
                              "AND (status='binned' OR result LIKE ?) ORDER BY id DESC", (a.uid, '%"binned"%')):
            inp, res = json.loads(r["input"] or "{}"), json.loads(r["result"] or "{}")
            title = inp.get("name") or (res.get("source") or {}).get("title") or "Your take"
            if r["status"] == "binned":
                b = res.get("bin") or {}
                takes_.append({"take_id": r["id"], "title": title, "clips": len(res.get("clips") or []), "deleted_at": b.get("at"),
                               "purge_at": (b.get("at") or 0) + TRASH_DAYS * 86400, "days_left": left(b.get("at"))})
                continue
            for c in res.get("clips") or []:
                if c.get("status") == "binned":
                    b = c.get("bin") or {}
                    clips_.append({"take_id": r["id"], "clip_id": c["id"], "take_title": title, "hook": c.get("hook") or "",
                                   "deleted_at": b.get("at"), "purge_at": (b.get("at") or 0) + TRASH_DAYS * 86400, "days_left": left(b.get("at"))})
        return {"takes": takes_, "clips": clips_, "days": TRASH_DAYS}

    def restore_take(self, a: Actor, rid, clip_id: str | None = None) -> dict:
        with self._take_lock(rid):  # the take's lock (takes._take_lock), the one its worker callbacks take
            return self._restore_take(a, rid, clip_id)

    def _restore_take(self, a: Actor, rid, clip_id: str | None = None) -> dict:
        self._guard(a, "restore_take")
        with self.store.tx():
            if clip_id:
                run = self._bin_row(a, rid, binned=False)
                res = run["result"]
                clip = next((c for c in res.get("clips") or [] if c.get("id") == str(clip_id) and c.get("status") == "binned"), None)
                if not clip:
                    raise ActionError("That clip isn't in the Bin.", 404)
                b = clip.pop("bin", {}) or {}
                self._move_tokens(a.uid, b.get("tokens") or [], False)
                prev = b.get("status") or "rendered"
                clip["status"] = "failed" if prev == "rendering" else prev  # a cancelled render can't resume: re-trim to render again
                if prev == "rendering":
                    clip["error"] = "Its render was cancelled when it was deleted. Trim it to render it again."
                self._save_raw(run["id"], res)
                out = {"restored": True, "take_id": run["id"], "clip_id": clip["id"]}
            else:
                run = self._bin_row(a, rid, binned=True)
                res = run["result"]
                b = res.pop("bin", {}) or {}
                self._move_tokens(a.uid, b.get("tokens") or [], False)
                status = b.get("status") or "ready"
                if status in ("running", "rendering"):  # its jobs were cancelled: the take says so instead of spinning
                    status = "failed"
                    for c in res.get("clips") or []:
                        if c.get("status") == "rendering":
                            c["status"], c["error"] = ("rendered", None) if c.get("url") else ("failed", "Cancelled when the take was deleted.")
                self._save_raw(run["id"], res, status)
                out = {"restored": True, "take_id": run["id"], "status": status}
        self.store.audit(a.uid, a.label, "restore_take", "safety", {"take": out["take_id"], **({"clip": out["clip_id"]} if clip_id else {})})
        return out

    def purge_take_bin(self, a: Actor, take_ids=None, clips=None, everything: bool = False) -> dict:
        return self._purge_take_bin(a, take_ids, clips, everything)

    def _purge_take_bin(self, a: Actor, take_ids=None, clips=None, everything: bool = False) -> dict:
        self._guard(a, "purge_take_bin")
        if not everything:
            if take_ids is not None and not isinstance(take_ids, list):
                raise ActionError("take_ids is a list of take ids.")
            if clips is not None and not isinstance(clips, list):
                raise ActionError("clips is a list of {take_id, clip_id}.")
            take_ids, clips = take_ids or [], clips or []
            if not take_ids and not clips:
                raise ActionError("Pick at least one take or clip (or all: true).")
            if len(take_ids) + len(clips) > MAX_IDS:
                raise ActionError(f"Up to {MAX_IDS} at a time.")
            try:
                take_ids = [int(x) for x in take_ids if not isinstance(x, bool)]
                pairs = [(int(c["take_id"]), str(c["clip_id"])) for c in clips]
            except (TypeError, ValueError, KeyError):
                raise ActionError("clips is a list of {take_id, clip_id}; take_ids a list of numbers.")
        else:
            take_ids = [r["id"] for r in self.store.q("SELECT id FROM agent_runs WHERE user_id=? AND kind='take' AND status='binned'", (a.uid,))]
            pairs = [(x["take_id"], x["clip_id"]) for x in self.take_bin(a)["clips"]]
        done_t, done_c, refused = self._purge(a.uid, take_ids, pairs)
        refused_out = [{"take_id": t, "error": "That take isn't in the Bin."} for t in take_ids if t not in done_t] + \
                      [{"take_id": t, "clip_id": c, "error": "That clip isn't in the Bin."} for t, c in pairs if (t, c) not in done_c]
        if done_t or done_c:
            self.store.audit(a.uid, a.label, "purge_take_bin", "safety", {"takes": len(done_t), "clips": len(done_c), "everything": bool(everything)})
        return {"purged_takes": done_t, "purged_clips": [{"take_id": t, "clip_id": c} for t, c in done_c], "refused": refused_out}

    def _purge(self, uid: str, take_ids: list[int], pairs: list[tuple[int, str]], binned_before: float | None = None):
        """Remove binned takes and clips of this person for good: records and files in one transaction. binned_before (the
        7-day sweep): only what is still in the Bin since before then, re-read under the take's lock, so a take restored
        and deleted again meanwhile keeps its new seven days (Greptile, PR #107)."""
        def expired(b) -> bool:
            return binned_before is None or ((b or {}).get("at") or 0) <= binned_before
        done_t, done_c, files = [], [], []
        st = self.store
        locks = contextlib.ExitStack()  # every take this touches, in id order (no lock-order deadlock), before the transaction
        for rid in sorted({x for x in take_ids if isinstance(x, int)} | {t for t, _ in pairs if isinstance(t, int)}):
            locks.enter_context(self._take_lock(rid))
        with locks, st.tx():
            for rid in dict.fromkeys(take_ids):
                rows = st.q("SELECT id, input, result FROM agent_runs WHERE id=? AND user_id=? AND kind='take' AND status='binned'", (rid, uid))
                if not rows:
                    continue
                res = json.loads(rows[0]["result"] or "{}")
                if not expired(res.get("bin")):
                    continue
                inp = json.loads(rows[0]["input"] or "{}")
                toks = set((res.get("bin") or {}).get("tokens") or [])
                toks |= {t for c in res.get("clips") or [] for t in clip_tokens(c) + ((c.get("bin") or {}).get("tokens") or [])}
                src = inp.get("media_token") or ""
                if src and not st.q("SELECT 1 FROM library_media WHERE token=?", (src,)) and not st.q(
                        "SELECT 1 FROM agent_runs WHERE user_id=? AND id<>? AND input LIKE ?", (uid, rid, f"%{src}%")):
                    toks.add(src)  # the take's own uploaded source (a /api/media row no Library item or other run uses)
                self._drop_tokens(uid, sorted(toks))
                st.x("DELETE FROM render_jobs WHERE run_id=? AND user_id=?", (rid, uid))
                st.x("DELETE FROM agent_runs WHERE id=? AND user_id=?", (rid, uid))
                files.append(rid)
                done_t.append(rid)
            by_take: dict[int, list[str]] = {}
            for t, c in pairs:
                by_take.setdefault(t, []).append(c)
            for rid, cids in by_take.items():
                rows = st.q("SELECT id, result FROM agent_runs WHERE id=? AND user_id=? AND kind='take' AND status<>'binned'", (rid, uid))
                if not rows:
                    continue
                res = json.loads(rows[0]["result"] or "{}")
                keep, toks = [], []
                for c in res.get("clips") or []:
                    if c.get("id") in cids and c.get("status") == "binned" and expired(c.get("bin")):
                        toks += (c.get("bin") or {}).get("tokens") or clip_tokens(c)
                        done_c.append((rid, c["id"]))
                    else:
                        keep.append(c)
                if toks or len(keep) != len(res.get("clips") or []):
                    self._drop_tokens(uid, sorted(set(toks)))
                    res["clips"] = keep
                    self._save_raw(rid, res)
        for rid in files:  # the uploaded source on disk (after the commit: a rollback never leaves a record without its file)
            try:
                self.h.take_uploads.remove_for_take(rid, uid)
            except Exception as e:  # noqa: BLE001
                print(f"take bin: source file of take {rid} not removed: {e.__class__.__name__}", flush=True)
        return done_t, done_c, []

    def _drop_tokens(self, uid: str, toks: list[str]) -> None:
        if not toks:
            return
        st, now = self.store, _now()
        for i in range(0, len(toks), 300):
            part = toks[i:i + 300]
            both = part + [TRASH_TOKEN + t for t in part]
            st.x(f"DELETE FROM media WHERE user_id=? AND token IN ({','.join('?' * len(both))})", (uid, *both))
            st.x("INSERT INTO library_purged(token,user_id,at) VALUES " + ",".join(["(?,?,?)"] * len(part)) +
                 " ON CONFLICT(token) DO NOTHING", tuple(v for t in part for v in (t, uid, now)))

    def purge_expired(self, days: float | None = None) -> int:
        return self._purge_expired(days)

    def _purge_expired(self, days: float | None = None) -> int:
        """Takes and clips in the Bin longer than TRASH_DAYS are removed for good (the media check loop, every 6 hours)."""
        cut = _now() - (TRASH_DAYS if days is None else days) * 86400
        n = 0
        for r in self.store.q("SELECT id, user_id, status, result FROM agent_runs WHERE kind='take' AND (status='binned' OR result LIKE ?)",
                              ('%"binned"%',)):
            res = json.loads(r["result"] or "{}")
            if r["status"] == "binned":
                if ((res.get("bin") or {}).get("at") or 0) <= cut:
                    t, _, _ = self._purge(r["user_id"], [r["id"]], [], binned_before=cut)
                    n += len(t)
                continue
            old = [(r["id"], c["id"]) for c in res.get("clips") or [] if c.get("status") == "binned" and ((c.get("bin") or {}).get("at") or 0) <= cut]
            if old:
                _, c, _ = self._purge(r["user_id"], [], old, binned_before=cut)
                n += len(c)
        return n


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    t = studio.house.takes
    if name == "list_take_bin":
        return t.take_bin(actor)
    if name == "delete_take":
        return t.delete_take(actor, args.get("take_id"), args.get("clip_id") or None)
    if name == "restore_take":
        return t.restore_take(actor, args.get("take_id"), args.get("clip_id") or None)
    return t.purge_take_bin(actor, args.get("take_ids"), args.get("clips"), bool(args.get("all")))
