"""Make a post follows Buffer (owner, 9 Nov 2026): "when I deleted the draft from Buffer, Make a post is not updated with
the edit options again ... make sure it is bidirectional sync".

A Make a post package that went to Buffer (status sent_to_buffer, sent.buffer_post_id), one that changed after it was saved
as a draft (buffer_draft), and one waiting in Approvals are re-checked against the account's real Buffer posts whenever the
run is opened (GET /api/agent/runs/{rid}, MCP get_run). The read is the SAME cached post list the Planner, Inbox and One
take use (buffer_api.POSTS_TTL; our own writes clear it, ?refresh_buffer=1 forces a fresh read), so opening a post costs
no extra Buffer request inside the window.

* deleted in Buffer -> the package is ready again (caption, media, music and Save/Schedule are editable), with a
  ``buffer_sync`` note saying why; a draft that changed afterwards loses its "Update draft" (there is nothing to update)
* draft scheduled / scheduled moved back to draft / sent / failed in Buffer -> ``sent.buffer_status`` (+ time, link, error)
* caption edited in Buffer -> the package takes Buffer's caption (``sent.edited_in_buffer``)
* Approvals: approved -> the Buffer post it became; rejected -> ready again

Absence only counts when it proves something: a post saved in the last 2 minutes is never called deleted, and when the
account's sent list is full (100+), a missing id is looked up once by id before anything changes. Buffer unreachable or
rate limited: nothing changes.
"""
from __future__ import annotations

import copy
import json
import re
import time

GRACE_S = 120  # a post saved this recently is never called deleted just because Buffer's list doesn't show it yet
SYNC_STATUSES = ("draft", "scheduled", "sending", "needs_approval", "error")
MAX_LOOKUPS = 3  # direct by-id reads per open, only when the sent list is full
STATE = {"draft": "draft", "scheduled": "scheduled", "sending": "scheduled", "needs_approval": "needs_approval",
         "sent": "sent", "error": "failed"}


def buffer_index(store, buf, a) -> dict | None:
    """Every post in the account's Buffer, from the cached reads the Planner uses: {"posts": {id: post}, "sent_complete"}.
    None when Buffer can't be read now or a list came back full (absence would prove nothing)."""
    u = store.user(a.uid) or {}
    org = u.get("org_id")
    if not org:
        return None
    from .buffer_api import POSTS_PAGE
    try:
        try:
            buf.prefetch_posts(a.uid, org, SYNC_STATUSES + ("sent",))
        except Exception:  # noqa: BLE001 - the per-status reads below surface any real error
            pass
        posts: dict = {}
        for st in SYNC_STATUSES:
            rows = buf.posts(a.uid, org, [st], first=POSTS_PAGE)
            if len(rows) >= POSTS_PAGE:
                return None
            for p in rows:
                posts[str(p.get("id"))] = {**p, "status": (p.get("status") or st).lower()}
        sent = buf.posts(a.uid, org, ["sent"], first=POSTS_PAGE)
        for p in sent:
            posts[str(p.get("id"))] = {**p, "status": "sent"}
    except Exception:  # noqa: BLE001 - a Buffer hiccup never breaks the page; it just isn't re-checked this time
        return None
    return {"posts": posts, "sent_complete": len(sent) < POSTS_PAGE}


def _norm(t) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip()


def _linked(pk: dict) -> str:
    """The Buffer post a package is tied to ('' when none)."""
    sent = pk.get("sent") if isinstance(pk.get("sent"), dict) else {}
    if pk.get("status") == "sent_to_buffer" and sent.get("buffer_post_id"):
        return str(sent["buffer_post_id"])
    return str((pk.get("buffer_draft") or {}).get("buffer_post_id") or "")


def needs_sync(res: dict) -> bool:
    return any(isinstance(p, dict) and (_linked(p) or (p.get("status") == "pending_approval" and (p.get("sent") or {}).get("approval_id")))
               for p in (res or {}).get("packages") or [])


def _reopen(pk: dict, why: str, pid: str = "") -> None:
    """Back to ready: editable and sendable again, with one line saying what happened in Buffer."""
    was = pk.pop("sent", None) or {}
    pk.pop("buffer_draft", None)
    pk.pop("changed_since_sent", None)
    pk["status"] = "ready"
    pk["buffer_sync"] = {"event": why, "at": time.time(), "buffer_post_id": pid or str(was.get("buffer_post_id") or ""),
                         "mode": was.get("mode") or ""}


def reconcile(store, a, res: dict, index: dict | None, lookup=None) -> bool:
    """Bring each package in line with Buffer (and Approvals). lookup(pid) -> post dict | None (Buffer says no such post)
    | raises (unknown). True when anything changed."""
    changed = False
    lookups = 0
    for pk in (res or {}).get("packages") or []:
        if not isinstance(pk, dict):
            continue
        sent = pk.get("sent") if isinstance(pk.get("sent"), dict) else {}
        if pk.get("status") == "pending_approval" and sent.get("approval_id"):
            rows = store.q("SELECT status,result FROM approvals WHERE id=? AND user_id=?", (sent["approval_id"], a.uid))
            ap = dict(rows[0]) if rows else None
            if ap and ap["status"] == "rejected":
                _reopen(pk, "rejected")
                changed = True
            elif ap and ap["status"] == "approved":
                try:
                    out = json.loads(ap["result"] or "{}") or {}
                except ValueError:
                    out = {}
                pid = (out.get("post") or {}).get("id")
                if pid:
                    pk["status"] = "sent_to_buffer"
                    pk["sent"] = {**sent, "buffer_post_id": pid, "mode": out.get("mode") or sent.get("mode"), "at": time.time()}
                    changed = True
            continue
        pid = _linked(pk)
        if not pid or index is None:
            continue
        post = index["posts"].get(pid)
        if post is None:
            if time.time() - float(sent.get("at") or (pk.get("buffer_draft") or {}).get("at") or 0) < GRACE_S:
                continue  # just saved: Buffer may not list it yet
            gone = bool(index["sent_complete"])
            if not gone and lookup and lookups < MAX_LOOKUPS:
                lookups += 1
                try:
                    found = lookup(pid)
                except Exception:  # noqa: BLE001 - unknown: leave it, and say the check didn't finish
                    index["incomplete"] = True
                    continue
                if found:
                    post = {**found, "status": str(found.get("status") or "").lower()}
                    index["posts"][pid] = post
                else:
                    gone = True
            if post is None:
                if not gone:
                    continue
                if pk.get("status") == "sent_to_buffer":
                    _reopen(pk, "deleted", pid)
                else:  # a draft that changed here after it was saved, and is now gone from Buffer: nothing to update
                    pk.pop("buffer_draft", None)
                    pk.pop("changed_since_sent", None)
                    pk["buffer_sync"] = {"event": "deleted", "at": time.time(), "buffer_post_id": pid, "mode": "draft"}
                changed = True
                continue
        if pk.get("status") != "sent_to_buffer":
            continue  # a changed package's draft that is still in Buffer: "Update draft" stays as it is
        bst = STATE.get(str(post.get("status") or "").lower())
        if not bst:
            continue
        upd = {"buffer_status": bst}
        if bst == "sent":
            upd["sent_at"] = post.get("sentAt") or post.get("dueAt") or ""
            if post.get("externalLink"):
                upd["link"] = post["externalLink"]
        elif post.get("dueAt") and bst != "draft":
            upd["due_at"] = post["dueAt"]
        if bst == "failed":
            upd["error"] = "Buffer couldn't publish it: " + (((post.get("error") or {}).get("message")) or "see Buffer.")
        bt = _norm(post.get("text"))
        mine = _norm(pk.get("text"))
        edited = bool(bt and bt != mine and not mine.startswith(bt))  # a thread's first part is a prefix: not an edit
        if edited:  # edited in Buffer
            pk["text"] = post["text"]
            upd["edited_in_buffer"] = time.time()
            changed = True
        if edited or any(sent.get(k) != v for k, v in upd.items() if k != "edited_in_buffer"):
            pk["sent"] = {**sent, **upd}
            pk.pop("buffer_sync", None)
            changed = True
    return changed


def sync_run(house, a, run: dict, refresh: bool = False) -> bool:
    """Re-check one Make a post run's packages against Buffer and save what changed into ``run`` (the dict the caller is
    about to return). Quiet: never raises. Runs that are working, takes and runs with nothing in Buffer are left alone."""
    try:
        res = run.get("result") if isinstance(run, dict) else None
        if not isinstance(res, dict) or run.get("kind") == "take" or run.get("status") in ("running", "rendering"):
            return False
        if not needs_sync(res):
            return False
        buf = house.studio.buffer
        if refresh:
            buf.refresh(a.uid)
        linked = any(_linked(p) for p in res.get("packages") or [] if isinstance(p, dict))
        index = buffer_index(house.store, buf, a) if linked else None
        lookup = getattr(buf, "post_status", None)
        fn = (lambda pid: lookup(a.uid, pid)) if lookup else None
        work = copy.deepcopy(res)  # reconciled on a copy: the answer shows it only once it is saved
        moved = reconcile(house.store, a, work, index, fn)
        if linked:  # the card's "Check Buffer" says whether Buffer really answered for every post this time
            run["buffer_check"] = "ok" if index is not None and not index.get("incomplete") else "unavailable"
        if not moved:
            return False
        fresh = house.store.q("SELECT status, updated FROM agent_runs WHERE id=? AND user_id=?", (run["id"], a.uid))
        if not fresh or fresh[0]["status"] in ("running", "rendering") or fresh[0]["updated"] != run.get("updated"):
            run.pop("buffer_check", None)  # the agent or an edit saved it meanwhile: that save wins, the next open re-checks
            return False
        house._save(run["id"], run["steps"], work)
        run["result"] = work
        return True
    except Exception:  # noqa: BLE001 - the run always opens
        return False
