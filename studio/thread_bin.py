"""Threads: the conversations in the Make a post / One take sidebar (owner asks, 4 Nov 2026). UI = REST = MCP.

**Public ids.** Every thread has a stable, unguessable id, ``thr_`` + 128 random bits in lowercase base32 (26 chars),
kept in ``thread_ids`` next to the internal ``agent_runs`` id. Tools and URLs take the public id; a sequential int is
never a thread id in the public API. A thread today holds one run (a Make a post run, or a One take: a take IS its run).

**Reading earlier work** (so Claude can pick up a previous thread): ``list_threads(query?, limit, cursor)`` -> id, title,
updated_at, run_ids, a short summary; ``get_thread(thread_id, cursor)`` -> the messages (paginated, oldest first), the
runs / takes inside and their outputs. Read-only.

**Deleting a One take thread** (the same Bin model as the Library and the take Bin: 7 days, restore, purge):

* ``takes: "keep"`` (delete thread only): the thread leaves the sidebar; the take and its clips stay in One take. Purge
  (or 7 days) removes the conversation for good (its messages, its custom title, the MCP steps shown on it).
* ``takes: "delete"`` (the thread and its take): the take also goes to the take Bin (studio/take_bin.py: files stop
  being served, a running job is cancelled). Restore brings both back. Purge removes the take, its jobs and every stored
  file in one transaction, noting the tokens as deleted by their owner, so /healthz never counts them missing.

The delete state lives on the take's run: ``result.thread_bin``. Make a post threads aren't deletable here (no takes inside).

Scope before existence: someone else's thread id and a missing one are the same 404, and nothing in an answer names
another person's objects.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time

from .actions import ActionError, Actor
from .take_bin import TRASH_DAYS

TAKES_CHOICES = ("keep", "delete")
MAX_IDS = 200
ID_RE = re.compile(r"^thr_[a-z2-7]{26}$")
SCHEMA = ("CREATE TABLE IF NOT EXISTS thread_ids (public_id TEXT PRIMARY KEY, user_id TEXT, run_id INTEGER, created REAL)",
          "CREATE UNIQUE INDEX IF NOT EXISTS thread_ids_run ON thread_ids(run_id)")
LIST_MAX, PAGE_MSGS, SCAN = 50, 50, 500
TEXT_MAX = 2000

_th = {"name": "thread_id", "schema": {"type": "string", "description": "The thread's id (thr_..., from list_threads)"}}
TOOLS = [
    {"name": "list_threads", "description": "The person's previous threads (Make a post and One take conversations), newest first: id, "
     "title, updated_at, run_ids and a short summary. Use it when the person refers to earlier work ('the reel from yesterday', "
     "'that talk I clipped'); query matches titles and summaries. Paginated: pass next_cursor back as cursor.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX},
                                                      "cursor": {"type": "string"}}}},
    {"name": "get_thread", "description": "One previous thread: its messages (oldest first, paginated with cursor), and the runs / takes "
     "inside it with their outputs (post captions per channel and media, or a take's clips). Then continue_run(run_id) to keep "
     "working on it.",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {"thread_id": _th["schema"], "cursor": {"type": "string"}}}},
    {"name": "delete_thread", "description": "Delete a One take thread from the conversation sidebar into the Bin (restorable for 7 days "
     "with restore_thread, then removed for good). takes: 'keep' (default) deletes the thread only, the take and its clips stay "
     "in One take; 'delete' also moves its take to the take Bin (its files stop being served, a running job is cancelled).",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {
         "thread_id": _th["schema"],
         "takes": {"type": "string", "enum": list(TAKES_CHOICES), "description": "keep (thread only) or delete (thread and its take)"}}}},
    {"name": "restore_thread", "description": "Restore a One take thread from the Bin; a take deleted with it comes back too, with its files.",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {"thread_id": _th["schema"]}}},
    {"name": "list_thread_bin", "description": "One take threads deleted in the last 7 days, with what happened to each one's take and days_left.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "purge_thread_bin", "description": "Remove One take threads in the Bin for good: thread_ids, or all=true. A take deleted with "
     "its thread is removed with all its files; a kept take stays. Can't be undone.",
     "inputSchema": {"type": "object", "properties": {"thread_ids": {"type": "array", "items": {"type": "string"}},
                                                      "all": {"type": "boolean"}}}},
]
READ_ONLY = {"list_thread_bin", "list_threads", "get_thread"}
NAMES = {t["name"] for t in TOOLS}


def _now() -> float:
    return time.time()


def new_public_id() -> str:
    return "thr_" + base64.b32encode(os.urandom(16)).decode().lower().rstrip("=")


def _ensure(store) -> None:
    if getattr(store, "_thread_ids_ok", False):
        return
    for s in SCHEMA:
        store.x(s.replace(" REAL", " DOUBLE PRECISION") if store.pg else s)
    if store.pg:
        store.x("ALTER TABLE thread_ids ENABLE ROW LEVEL SECURITY")
    store._thread_ids_ok = True


def public_id(store, uid: str, rid: int) -> str:
    """The thread id of one of this person's runs, made the first time it is asked for (stable after that)."""
    _ensure(store)
    rows = store.q("SELECT public_id, user_id FROM thread_ids WHERE run_id=?", (int(rid),))
    if rows:
        return rows[0]["public_id"]
    pid = new_public_id()
    store.x("INSERT INTO thread_ids(public_id,user_id,run_id,created) VALUES(?,?,?,?) ON CONFLICT DO NOTHING", (pid, uid, int(rid), _now()))
    return store.q("SELECT public_id FROM thread_ids WHERE run_id=?", (int(rid),))[0]["public_id"]


def public_ids(store, uid: str, rids: list[int]) -> dict[int, str]:
    _ensure(store)
    have = {}
    for i in range(0, len(rids), 300):
        part = [int(r) for r in rids[i:i + 300]]
        if part:
            have.update({int(r["run_id"]): r["public_id"] for r in store.q(
                f"SELECT run_id, public_id FROM thread_ids WHERE user_id=? AND run_id IN ({','.join('?' * len(part))})", (uid, *part))})
    for r in rids:
        if int(r) not in have:
            have[int(r)] = public_id(store, uid, int(r))
    return have


def resolve(store, uid: str, tid) -> int:
    """The run behind a public thread id of THIS person; anything else (malformed, missing, someone else's) is a 404."""
    if not isinstance(tid, str) or not ID_RE.match(tid.strip().lower()):
        raise ActionError("No such thread.", 404)
    _ensure(store)
    rows = store.q("SELECT t.run_id FROM thread_ids t JOIN agent_runs r ON r.id=t.run_id WHERE t.public_id=? AND t.user_id=? AND r.user_id=?",
                   (tid.strip().lower(), uid, uid))
    if not rows:
        raise ActionError("No such thread.", 404)
    return int(rows[0]["run_id"])


def hidden(status: str, res: dict) -> bool:
    """Is this take's thread out of the sidebar? (in the thread Bin, or purged). A take deleted with its thread but
    restored from the take Bin brings its thread back."""
    tb = (res or {}).get("thread_bin")
    if not isinstance(tb, dict):
        return False
    if tb.get("purged"):
        return True
    if tb.get("takes") == "delete" and status == "binned" and ((res.get("bin") or {}).get("at") or 0) > (tb.get("at") or 0):
        return False  # the take was restored from the take Bin and deleted again on its own: this marker is stale, and the
        # take's own Bin clock (take_bin.py) decides when it goes, never the older thread deletion time
    return not (tb.get("takes") == "delete" and status != "binned")


def _has_table(store, name: str) -> bool:
    if store.pg:
        return bool(store.q("SELECT 1 FROM information_schema.tables WHERE table_name=? AND table_schema = current_schema()", (name,)))
    return bool(store.q("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def _cursor_in(cursor) -> int:
    if cursor in (None, ""):
        return 0
    try:
        n = int(base64.urlsafe_b64decode(str(cursor) + "==").decode().removeprefix("o:"))
    except (ValueError, UnicodeDecodeError, TypeError):
        raise ActionError("cursor is the next_cursor a previous page returned.")
    if n < 0 or n > 100000:
        raise ActionError("cursor is the next_cursor a previous page returned.")
    return n


def _cursor_out(n: int) -> str:
    return base64.urlsafe_b64encode(f"o:{n}".encode()).decode().rstrip("=")


def _clip(s, n: int = TEXT_MAX) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[:n - 1] + "…"


class ThreadBin:
    def __init__(self, house):
        self.h, self.store = house, house.store

    # ------------------------------------------------------------------ reading
    def _title(self, kind: str, inp: dict, res: dict, named: dict, rid: int) -> str:
        if named.get(rid):
            return named[rid]
        if kind == "take":
            return "One take: " + (inp.get("name") or "your video")
        from .headline import title as _t
        req = " ".join(str(inp.get(k) or "") for k in ("prompt", "query", "url", "brief") if inp.get(k)).strip()
        return _t((res.get("story") or {}).get("headline") or "", req, "Your media" if kind == "media" else "Your post")

    def _summary(self, kind: str, status: str, inp: dict, res: dict) -> str:
        if kind == "take":
            clips = [c for c in res.get("clips") or [] if c.get("status") != "binned"]
            ready = sum(1 for c in clips if c.get("url"))
            return _clip(f"One take, {status}: {ready} of {len(clips)} clips ready" + (f"; first hook: {clips[0].get('hook')}" if clips and clips[0].get("hook") else ""), 300)
        s = (res.get("thread_summary") or {}).get("summary") or ""
        if not s:
            first = next((m.get("text") for m in res.get("thread") or [] if m.get("role") == "user" and m.get("text")), "") or inp.get("prompt") or ""
            pk = res.get("packages") or []
            sent = sum(1 for p in pk if p.get("sent"))
            s = f"{status}; {len(pk)} channel post{'s' if len(pk) != 1 else ''}{f', {sent} in Buffer' if sent else ''}. Asked: {first}"
        return _clip(s, 300)

    def list_threads(self, a: Actor, query=None, limit=20, cursor=None) -> dict:
        if query is not None and not isinstance(query, str):
            raise ActionError("query is text.")
        if isinstance(limit, bool):
            raise ActionError("limit is a number from 1 to 50.")
        try:
            limit = max(1, min(LIST_MAX, int(limit or 20)))
        except (TypeError, ValueError):
            raise ActionError("limit is a number from 1 to 50.")
        off = _cursor_in(cursor)
        q = (query or "").strip().lower()[:200]
        from .chat_context import titles as _titles
        named = _titles(self.store, a.uid)
        rows = self.store.q("SELECT id,kind,input,status,result,created,updated FROM agent_runs WHERE user_id=? AND status NOT IN ('archived','binned') "
                            "ORDER BY updated DESC, id DESC LIMIT ?", (a.uid, SCAN))
        items = []
        for r in rows:
            inp, res = json.loads(r["input"] or "{}") or {}, json.loads(r["result"] or "{}") or {}
            if r["kind"] == "take" and hidden(r["status"], res):
                continue
            title, summ = self._title(r["kind"], inp, res, named, r["id"]), self._summary(r["kind"], r["status"], inp, res)
            if q and q not in title.lower() and q not in summ.lower():
                continue
            items.append({"rid": r["id"], "title": title, "summary": summ, "kind": "take" if r["kind"] == "take" else "post",
                          "status": r["status"], "updated_at": r["updated"], "created_at": r["created"]})
        page = items[off:off + limit]
        ids = public_ids(self.store, a.uid, [x["rid"] for x in page])
        # run_ids: a thread holds one run today (the ids continue_run / get_run / list_clips take)
        out = [{"id": ids[x["rid"]], "run_ids": [x["rid"]], **{k: v for k, v in x.items() if k != "rid"}} for x in page]
        more = off + limit < len(items)
        return {"threads": out, "next_cursor": _cursor_out(off + limit) if more else None}

    def get_thread(self, a: Actor, tid, cursor=None) -> dict:
        rid = resolve(self.store, a.uid, tid)
        run = self.h.run(a, rid)  # merges the MCP steps; 404 for anyone else's
        res, inp = run.get("result") or {}, run.get("input") or {}
        if run["status"] in ("archived", "binned") or (run["kind"] == "take" and hidden(run["status"], res)):
            raise ActionError("No such thread.", 404)  # what list_threads hides, get_thread hides (tidied or in a Bin)
        off = _cursor_in(cursor)
        msgs = [m for m in res.get("thread") or [] if isinstance(m, dict)]
        page = [{"role": m.get("role"), "text": _clip(m.get("text")), "at": m.get("at")} for m in msgs[off:off + PAGE_MSGS]]
        from .chat_context import titles as _titles
        title = self._title(run["kind"], inp, res, _titles(self.store, a.uid), rid)
        if run["kind"] == "take":
            outputs = {"clips": [{k: c.get(k) for k in ("id", "status", "hook", "start", "end", "url", "cover_url")}
                                 for c in res.get("clips") or [] if c.get("status") != "binned"]}
        else:
            outputs = {"packages": [{"channel_id": p.get("channel_id"), "service": p.get("service"), "format": p.get("format"),
                                     "text": _clip(p.get("text")), "media": [m.get("url") for m in p.get("media") or [] if isinstance(m, dict)][:10],
                                     "sent": bool(p.get("sent"))} for p in res.get("packages") or [] if isinstance(p, dict)]}
        return {"id": public_id(self.store, a.uid, rid), "title": title, "updated_at": run.get("updated"),
                "summary": self._summary(run["kind"], run["status"], inp, res),
                "runs": [{"run_id": rid, "kind": "take" if run["kind"] == "take" else "post", "status": run["status"],
                          "request": _clip(inp.get("prompt") or inp.get("name") or "", 500), "outputs": outputs}],
                "messages": page, "messages_total": len(msgs),
                "next_cursor": _cursor_out(off + PAGE_MSGS) if off + PAGE_MSGS < len(msgs) else None,
                "next": f"continue_run(run_id={rid}) to keep working on this thread" if run["kind"] != "take"
                else f"list_clips(take_id={rid}) for the clips' full detail"}

    # ------------------------------------------------------------------ delete / restore (One take threads)
    def _row(self, a: Actor, tid) -> dict:
        rid = resolve(self.store, a.uid, tid)
        rows = self.store.q("SELECT id,status,input,result FROM agent_runs WHERE id=? AND user_id=? AND kind='take'", (rid, a.uid))
        if not rows:
            raise ActionError("No such thread.", 404)
        d = dict(rows[0])
        d["input"], d["result"] = json.loads(d["input"] or "{}"), json.loads(d["result"] or "{}") or {}
        d["tid"] = tid.strip().lower()
        return d

    def _save(self, rid: int, res: dict) -> None:
        self.store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=?", (json.dumps(res, default=str), _now(), rid))

    def _guard(self, a: Actor, action: str) -> None:
        self.h.studio._guard_write(a, action)
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace is view-only. Connect Buffer to manage your own threads.", 403)

    def delete(self, a: Actor, tid, takes="keep") -> dict:
        self._guard(a, "delete_thread")
        takes = "keep" if takes in (None, "") else takes
        if takes not in TAKES_CHOICES:
            raise ActionError("takes is 'keep' (delete the thread only) or 'delete' (the thread and its take).")
        # the take's lock first, then the store's (the order worker callbacks and the take Bin use: no lock-order deadlock)
        with self.h.takes._take_lock(resolve(self.store, a.uid, tid)), self.store.tx():
            run = self._row(a, tid)
            if hidden(run["status"], run["result"]):
                raise ActionError("No such thread.", 404)
            take_out = None
            if takes == "delete" and run["status"] != "binned":
                take_out = self.h.takes.delete_take(a, run["id"])
                run = self._row(a, tid)
            res = run["result"]
            res["thread_bin"] = {"at": _now(), "takes": takes}
            self._save(run["id"], res)
        self.store.audit(a.uid, a.label, "delete_thread", "safety", {"thread": run["tid"], "takes": takes, "trash_days": TRASH_DAYS})
        return {"deleted": True, "thread_id": run["tid"], "takes": takes, "in_bin_days": TRASH_DAYS,
                "take": ("moved to the take Bin" if takes == "delete" else "kept in One take"),
                "cancelled_jobs": (take_out or {}).get("cancelled_jobs", 0), "restore": f"POST /api/threads/{run['tid']}/restore"}

    def restore(self, a: Actor, tid) -> dict:
        self._guard(a, "restore_thread")
        with self.h.takes._take_lock(resolve(self.store, a.uid, tid)), self.store.tx():  # take lock, then store (as delete)
            run = self._row(a, tid)
            tb = run["result"].get("thread_bin")
            if not isinstance(tb, dict) or tb.get("purged") or not hidden(run["status"], run["result"]):
                raise ActionError("That thread isn't in the Bin.", 404)
            take_out = None
            if tb.get("takes") == "delete" and run["status"] == "binned":
                take_out = self.h.takes.restore_take(a, run["id"])
                run = self._row(a, tid)
            res = run["result"]
            res.pop("thread_bin", None)
            self._save(run["id"], res)
        self.store.audit(a.uid, a.label, "restore_thread", "safety", {"thread": run["tid"]})
        return {"restored": True, "thread_id": run["tid"], "take_restored": bool(take_out),
                **({"take_status": take_out["status"]} if take_out else {})}

    # ------------------------------------------------------------------ the Bin
    def list(self, a: Actor) -> dict:
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            return {"threads": [], "days": TRASH_DAYS}
        now, out = _now(), []
        rows = self.store.q("SELECT id,status,input,result FROM agent_runs WHERE user_id=? AND kind='take' AND result LIKE ? "
                            "ORDER BY id DESC LIMIT 500", (a.uid, '%"thread_bin"%'))
        keep = []
        for r in rows:
            res = json.loads(r["result"] or "{}") or {}
            tb = res.get("thread_bin")
            if isinstance(tb, dict) and not tb.get("purged") and hidden(r["status"], res):
                keep.append((r, res, tb))
        ids = public_ids(self.store, a.uid, [r["id"] for r, _, _ in keep])
        for r, res, tb in keep:
            inp = json.loads(r["input"] or "{}") or {}
            at = tb.get("at") or 0
            sec = max(0.0, at + TRASH_DAYS * 86400 - now)
            out.append({"thread_id": ids[r["id"]], "title": "One take: " + (inp.get("name") or "your video"), "takes": tb.get("takes"),
                        "deleted_at": at, "purge_at": at + TRASH_DAYS * 86400, "days_left": int(-(-sec // 86400)) if sec else 0})
        return {"threads": out, "days": TRASH_DAYS}

    def purge(self, a: Actor, thread_ids=None, everything: bool = False) -> dict:
        self._guard(a, "purge_thread_bin")
        if everything:
            ids = [t["thread_id"] for t in self.list(a)["threads"]]
        else:
            if not isinstance(thread_ids, list) or not thread_ids:
                raise ActionError("thread_ids is a list of thread ids (or all: true).")
            if len(thread_ids) > MAX_IDS:
                raise ActionError(f"Up to {MAX_IDS} at a time.")
            if any(not isinstance(x, str) for x in thread_ids):
                raise ActionError("thread_ids is a list of thread ids (thr_...).")
            ids = [x.strip().lower() for x in thread_ids]
        done, refused = [], []
        for tid in dict.fromkeys(ids):
            try:
                rid = resolve(self.store, a.uid, tid)
            except ActionError:
                rid = None
            if rid and self._purge_one(a.uid, rid):
                done.append(tid)
            else:
                refused.append({"thread_id": tid, "error": "That thread isn't in the Bin."})
        if done:
            self.store.audit(a.uid, a.label, "purge_thread_bin", "safety", {"threads": len(done), "everything": bool(everything)})
        return {"purged_threads": done, "refused": refused}

    def _purge_one(self, uid: str, rid: int, cut: float | None = None) -> bool:
        with self.h.takes._take_lock(rid):  # take lock, then store (as delete); takes._purge below re-enters it (RLock)
            return self._purge_one_locked(uid, rid, cut)

    def _purge_one_locked(self, uid: str, rid: int, cut: float | None = None) -> bool:
        st = self.store
        with st.tx():
            rows = st.q("SELECT id,status,result FROM agent_runs WHERE id=? AND user_id=? AND kind='take'", (rid, uid))
            if not rows:
                return False
            res = json.loads(rows[0]["result"] or "{}") or {}
            tb = res.get("thread_bin")
            if not isinstance(tb, dict) or tb.get("purged") or not hidden(rows[0]["status"], res):
                return False
            if cut is not None and (tb.get("at") or 0) > cut:  # the 7-day sweep: restored and deleted again since it looked
                return False
            self._drop_conversation(uid, rid)
            if not (tb.get("takes") == "delete" and rows[0]["status"] == "binned"):
                res["thread"] = []
                res.pop("thread_summary", None)
                res["thread_bin"] = {"purged": True, "at": _now(), "takes": "keep"}
                self._save(rid, res)
                return True
        # the take goes with its thread: its run, jobs and every stored file in one transaction (+ library_purged notes,
        # so /healthz never counts them missing), then its uploaded source on disk (studio/take_bin.py _purge)
        done_t, _, _ = self.h.takes._purge(uid, [rid], [])
        if done_t:
            try:
                st.x("DELETE FROM thread_ids WHERE run_id=? AND user_id=?", (rid, uid))
            except Exception:  # noqa: BLE001
                pass
        return bool(done_t)

    def _drop_conversation(self, uid: str, rid: int) -> None:
        # the tables are made on first use (chat_titles on the first rename): look before deleting, never catch a failed
        # statement inside the transaction (on Postgres that aborts it, and the save after it would fail too)
        for table in ("chat_titles", "mcp_calls"):
            if _has_table(self.store, table):
                self.store.x(f"DELETE FROM {table} WHERE user_id=? AND run_id=?", (uid, rid))  # nosec B608 - fixed names

    def purge_expired(self, days: float | None = None) -> int:
        """Threads in the Bin longer than TRASH_DAYS are removed for good (the media check loop, every 6 hours)."""
        cut = _now() - (TRASH_DAYS if days is None else days) * 86400
        n = 0
        for r in self.store.q("SELECT id,user_id,status,result FROM agent_runs WHERE kind='take' AND result LIKE ?", ('%"thread_bin"%',)):
            res = json.loads(r["result"] or "{}") or {}
            tb = res.get("thread_bin")
            if isinstance(tb, dict) and not tb.get("purged") and hidden(r["status"], res) and (tb.get("at") or 0) <= cut:
                n += self._purge_one(r["user_id"], r["id"], cut)  # the deletion time is checked again under the take's lock
        return n


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    tb = studio.house.threads
    args = args or {}
    if name == "list_threads":
        return tb.list_threads(actor, args.get("query"), args.get("limit") or 20, args.get("cursor"))
    if name == "get_thread":
        return tb.get_thread(actor, args.get("thread_id"), args.get("cursor"))
    if name == "list_thread_bin":
        return tb.list(actor)
    if name == "delete_thread":
        return tb.delete(actor, args.get("thread_id"), args.get("takes") or "keep")
    if name == "restore_thread":
        return tb.restore(actor, args.get("thread_id"))
    return tb.purge(actor, args.get("thread_ids"), args.get("all") is True)


def with_thread_id(store, a: Actor, out):
    """start_post / continue_run answers carry the thread's public id next to run_id."""
    if isinstance(out, dict) and isinstance(out.get("run_id"), int) and "thread_id" not in out:
        try:
            if store.q("SELECT 1 FROM agent_runs WHERE id=? AND user_id=?", (out["run_id"], a.uid)):
                out["thread_id"] = public_id(store, a.uid, out["run_id"])
        except Exception:  # noqa: BLE001 - never break the answer over its thread id
            pass
    return out
