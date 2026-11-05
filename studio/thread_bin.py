"""Threads: the conversations in the Make a post / One take sidebar (owner asks, 4 Nov 2026). UI = REST = MCP.

**Public ids.** Every thread has a stable, unguessable id, ``thr_`` + 128 random bits in lowercase base32 (26 chars),
kept in ``thread_ids`` next to the internal ``agent_runs`` id. Tools and URLs take the public id; a sequential int is
never a thread id in the public API. A thread today holds one run (a Make a post run, or a One take: a take IS its run).

**Reading earlier work** (so Claude can pick up a previous thread): ``list_threads(query?, limit, cursor)`` -> id, title,
updated_at, run_ids, a short summary; ``get_thread(thread_id, cursor)`` -> the messages (paginated, oldest first), the
runs / takes inside and their outputs. Read-only.

**Deleting a thread is like deleting chat history** (owner, 4 Nov 2026 23:45): it removes the conversation only and
NEVER deletes media. Takes, clips, uploads, posts and Library files all stay where they are (One take, the Library,
Buffer). Every thread can be deleted: Make a post and One take, ready, running, failed or errored. The Bin model is the
Library's: 7 days, restore, purge.

* ``delete_thread``: the thread leaves the sidebar, list_threads and get_thread (a row in ``thread_deleted``; the run itself
  is untouched, so nothing a worker or a running agent writes can bring it back or lose it).
* ``restore_thread``: back within 7 days.
* ``purge_thread_bin`` (or 7 days): the conversation goes for good (its messages, summary, custom title and the MCP steps
  shown on it). The run's outputs stay. The row stays as a tombstone (``purged_at``), so the thread id answers 404 for
  good and the thread never comes back.

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

MAX_IDS = 200
ID_RE = re.compile(r"^thr_[a-z2-7]{26}$")
SCHEMA = ("CREATE TABLE IF NOT EXISTS thread_ids (public_id TEXT PRIMARY KEY, user_id TEXT, run_id INTEGER, created REAL)",
          "CREATE UNIQUE INDEX IF NOT EXISTS thread_ids_run ON thread_ids(run_id)",
          # a deleted thread (the Bin) and, once purged, its tombstone; the run in agent_runs is never changed by a delete
          "CREATE TABLE IF NOT EXISTS thread_deleted (run_id INTEGER PRIMARY KEY, user_id TEXT, deleted_at REAL, purged_at REAL)",
          "CREATE INDEX IF NOT EXISTS thread_deleted_user ON thread_deleted(user_id)")
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
     "inside it with their outputs (post captions per channel and media, an AI app's finished renders, or a take's clips). Then continue_run(run_id) to keep "
     "working on it.",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {"thread_id": _th["schema"], "cursor": {"type": "string"}}}},
    {"name": "delete_thread", "description": "Delete a thread (Make a post or One take, in any state, failed ones too) from the "
     "conversation sidebar into the Bin, like deleting chat history: only the conversation goes. Media is NEVER deleted: "
     "takes, clips, uploads, posts and Library files stay. Restorable for 7 days with restore_thread, then the conversation "
     "is removed for good. To delete a take itself, use delete_take.",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {"thread_id": _th["schema"]}}},
    {"name": "restore_thread", "description": "Restore a deleted thread from the Bin (within 7 days).",
     "inputSchema": {"type": "object", "required": ["thread_id"], "properties": {"thread_id": _th["schema"]}}},
    {"name": "list_thread_bin", "description": "Threads deleted in the last 7 days (id, title, kind, days_left). Their media was never deleted.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "purge_thread_bin", "description": "Remove threads in the Bin for good: thread_ids, or all=true. Only the conversation goes "
     "(messages, summary, custom title, MCP steps); media stays. Can't be undone.",
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
        store.x("ALTER TABLE thread_deleted ENABLE ROW LEVEL SECURITY")
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


def deleted_ids(store, uid: str) -> set[int]:
    """This person's runs whose thread is deleted (in the Bin, or purged: a tombstone)."""
    _ensure(store)
    return {int(r["run_id"]) for r in store.q("SELECT run_id FROM thread_deleted WHERE user_id=?", (uid,))}


def purged(store, uid: str, rid) -> bool:
    """The thread was removed for good (a tombstone): nothing may write to its conversation again (reply, continue_run,
    start_post with its run_id). Its outputs stay readable (record_output and lookups use own_run, not this)."""
    _ensure(store)
    try:
        rid = int(rid)
    except (TypeError, ValueError):
        return False
    return bool(store.q("SELECT 1 FROM thread_deleted WHERE run_id=? AND user_id=? AND purged_at IS NOT NULL", (rid, uid)))


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
        gone = deleted_ids(self.store, a.uid)
        from . import run_text  # the text only when it changed (Supabase egress, 5 Nov 2026)
        rows = run_text.attach(self.store, self.store.q(
            f"SELECT id,kind,{run_text.cols(self.store)},status,created,updated FROM agent_runs WHERE user_id=? AND status NOT IN ('archived','binned') "
            "AND id NOT IN (SELECT run_id FROM thread_deleted WHERE user_id=?) "
            "ORDER BY updated DESC, id DESC LIMIT ?", (a.uid, a.uid, SCAN)))
        items = []
        for r in rows:
            inp, res = json.loads(r["input"] or "{}") or {}, json.loads(r["result"] or "{}") or {}
            if r["id"] in gone:
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
        if run["status"] in ("archived", "binned") or rid in deleted_ids(self.store, a.uid):
            raise ActionError("No such thread.", 404)  # what list_threads hides, get_thread hides (tidied, in a Bin, or deleted)
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
                                     "sent": bool(p.get("sent"))} for p in res.get("packages") or [] if isinstance(p, dict)],
                       # an AI app's finished renders (record_output) before any package exists (Greptile, 5 Nov 2026)
                       "renders": [{"output": o.get("output"), "format": o.get("format"),
                                    "media": [u for u in o.get("media_urls") or [] if isinstance(u, str)][:10],
                                    "library_id": o.get("library_id"), "versions": o.get("versions")}
                                   for o in (res.get("outputs") or [])[:50] if isinstance(o, dict)]}
        return {"id": public_id(self.store, a.uid, rid), "title": title, "updated_at": run.get("updated"),
                "summary": self._summary(run["kind"], run["status"], inp, res),
                "runs": [{"run_id": rid, "kind": "take" if run["kind"] == "take" else "post", "status": run["status"],
                          "request": _clip(inp.get("prompt") or inp.get("name") or "", 500), "outputs": outputs}],
                "messages": page, "messages_total": len(msgs),
                "next_cursor": _cursor_out(off + PAGE_MSGS) if off + PAGE_MSGS < len(msgs) else None,
                "next": f"continue_run(run_id={rid}) to keep working on this thread" if run["kind"] != "take"
                else f"list_clips(take_id={rid}) for the clips' full detail"}

    # ------------------------------------------------------------------ delete / restore (every thread; never media)
    def _guard(self, a: Actor, action: str) -> None:
        self.h.studio._guard_write(a, action)
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            raise ActionError("The sample workspace is view-only. Connect Buffer to manage your own threads.", 403)

    def _run_row(self, a: Actor, tid) -> dict:
        rid = resolve(self.store, a.uid, tid)  # malformed, missing or someone else's: 404
        rows = self.store.q("SELECT id,kind,status FROM agent_runs WHERE id=? AND user_id=?", (rid, a.uid))
        if not rows:
            raise ActionError("No such thread.", 404)
        return {"id": int(rows[0]["id"]), "kind": rows[0]["kind"], "status": rows[0]["status"], "tid": tid.strip().lower()}

    def delete(self, a: Actor, tid, takes=None) -> dict:
        self._guard(a, "delete_thread")
        if takes not in (None, "", "keep"):
            raise ActionError("Deleting a thread never deletes media: the take, its clips and uploads stay. "
                              "To delete a take, use delete_take in One take.")
        run = self._run_row(a, tid)
        with self.store.tx():
            if self.store.q("SELECT 1 FROM thread_deleted WHERE run_id=?", (run["id"],)):
                raise ActionError("No such thread.", 404)  # already in the Bin, or purged (a tombstone)
            self.store.x("INSERT INTO thread_deleted(run_id,user_id,deleted_at,purged_at) VALUES(?,?,?,NULL)", (run["id"], a.uid, _now()))
        self.store.audit(a.uid, a.label, "delete_thread", "safety", {"thread": run["tid"], "kind": run["kind"], "trash_days": TRASH_DAYS})
        return {"deleted": True, "thread_id": run["tid"], "in_bin_days": TRASH_DAYS, "media": "kept",
                "note": "Only the conversation is deleted. Takes, clips, uploads, posts and Library files stay.",
                "restore": f"POST /api/threads/{run['tid']}/restore"}

    def restore(self, a: Actor, tid) -> dict:
        self._guard(a, "restore_thread")
        run = self._run_row(a, tid)
        with self.store.tx():
            rows = self.store.q("SELECT purged_at FROM thread_deleted WHERE run_id=? AND user_id=?", (run["id"], a.uid))
            if not rows or rows[0]["purged_at"] is not None:
                raise ActionError("That thread isn't in the Bin.", 404)
            self.store.x("DELETE FROM thread_deleted WHERE run_id=? AND user_id=? AND purged_at IS NULL", (run["id"], a.uid))
        self.store.audit(a.uid, a.label, "restore_thread", "safety", {"thread": run["tid"]})
        return {"restored": True, "thread_id": run["tid"]}

    # ------------------------------------------------------------------ the Bin
    def list(self, a: Actor) -> dict:
        from . import demo_guard
        if demo_guard.is_demo(a.uid):
            return {"threads": [], "days": TRASH_DAYS}
        _ensure(self.store)
        now, out = _now(), []
        rows = self.store.q("SELECT d.run_id, d.deleted_at, r.kind, r.status, r.input, r.result FROM thread_deleted d "
                            "JOIN agent_runs r ON r.id=d.run_id AND r.user_id=d.user_id WHERE d.user_id=? AND d.purged_at IS NULL "
                            "ORDER BY d.deleted_at DESC", (a.uid,))
        from .chat_context import titles as _titles
        named = _titles(self.store, a.uid)
        ids = public_ids(self.store, a.uid, [int(r["run_id"]) for r in rows])
        for r in rows:
            inp, res = json.loads(r["input"] or "{}") or {}, json.loads(r["result"] or "{}") or {}
            at = r["deleted_at"] or 0
            sec = max(0.0, at + TRASH_DAYS * 86400 - now)
            out.append({"thread_id": ids[int(r["run_id"])], "title": self._title(r["kind"], inp, res, named, int(r["run_id"])),
                        "kind": "take" if r["kind"] == "take" else "post", "status": r["status"],
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
        """The conversation goes for good; the run's outputs (posts, media, a take and its clips) stay. A take's result is
        rewritten under its lock (the one worker callbacks take), so a callback never loses its write or brings messages back."""
        import contextlib
        kind = self.store.q("SELECT kind FROM agent_runs WHERE id=? AND user_id=?", (rid, uid))
        lock = self.h.takes._take_lock(rid) if kind and kind[0]["kind"] == "take" else contextlib.nullcontext()
        st = self.store
        with lock, st.tx():
            d = st.q("SELECT deleted_at, purged_at FROM thread_deleted WHERE run_id=? AND user_id=?", (rid, uid))
            if not d or d[0]["purged_at"] is not None:
                return False
            if cut is not None and (d[0]["deleted_at"] or 0) > cut:  # the 7-day sweep: restored and deleted again since it looked
                return False
            rows = st.q("SELECT status, result FROM agent_runs WHERE id=? AND user_id=?", (rid, uid))
            if rows:
                if rows[0]["status"] in ("running", "queued") and (not kind or kind[0]["kind"] != "take"):
                    return False  # a Make a post run still writing its conversation: the sweep purges it once it ends
                res = json.loads(rows[0]["result"] or "{}") or {}
                if res.get("thread") or res.get("thread_summary"):
                    res["thread"] = []
                    res.pop("thread_summary", None)
                    st.x("UPDATE agent_runs SET result=? WHERE id=? AND user_id=?", (json.dumps(res, default=str), rid, uid))
            self._drop_conversation(uid, rid)
            st.x("UPDATE thread_deleted SET purged_at=? WHERE run_id=? AND user_id=?", (_now(), rid, uid))  # the tombstone
        return True

    def _drop_conversation(self, uid: str, rid: int) -> None:
        # the tables are made on first use (chat_titles on the first rename): look before deleting, never catch a failed
        # statement inside the transaction (on Postgres that aborts it, and the save after it would fail too)
        for table in ("chat_titles", "mcp_calls"):
            if _has_table(self.store, table):
                self.store.x(f"DELETE FROM {table} WHERE user_id=? AND run_id=?", (uid, rid))  # nosec B608 - fixed names

    def purge_expired(self, days: float | None = None) -> int:
        """Threads in the Bin longer than TRASH_DAYS lose their conversation for good (the media check loop, every 6 hours)."""
        _ensure(self.store)
        cut = _now() - (TRASH_DAYS if days is None else days) * 86400
        n = 0
        for r in self.store.q("SELECT run_id, user_id FROM thread_deleted WHERE purged_at IS NULL AND deleted_at <= ?", (cut,)):
            n += self._purge_one(r["user_id"], int(r["run_id"]), cut)  # the deletion time is checked again under the lock
        self._reclear()
        return n

    def _reclear(self) -> int:
        """A late write (a client render's record_output read the result before the purge and saved it after) can bring
        a purged conversation back into the run's result: the sweep clears it again. Tombstoned threads stay hidden anyway."""
        import contextlib
        n = 0
        for r in self.store.q("SELECT d.run_id, d.user_id, a.kind FROM thread_deleted d JOIN agent_runs a ON a.id=d.run_id "
                              "WHERE d.purged_at IS NOT NULL AND (a.result LIKE ? OR a.result LIKE ?)", ('%"thread": [{%', '%"thread_summary"%')):
            rid, uid = int(r["run_id"]), r["user_id"]
            with (self.h.takes._take_lock(rid) if r["kind"] == "take" else contextlib.nullcontext()), self.store.tx():
                rows = self.store.q("SELECT status, result FROM agent_runs WHERE id=? AND user_id=?", (rid, uid))
                if not rows or (rows[0]["status"] in ("running", "queued") and r["kind"] != "take"):
                    continue
                res = json.loads(rows[0]["result"] or "{}") or {}
                if res.get("thread") or res.get("thread_summary"):
                    res["thread"] = []
                    res.pop("thread_summary", None)
                    self.store.x("UPDATE agent_runs SET result=? WHERE id=? AND user_id=?", (json.dumps(res, default=str), rid, uid))
                    n += 1
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
        return tb.delete(actor, args.get("thread_id"), args.get("takes"))
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
