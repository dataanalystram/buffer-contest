"""Run discipline for AI apps over MCP (Phase 1 of docs/plans/AGENT_RUNS_EDITING_COMMERCE.md, owner decisions 4 Nov 2026).

One prompt in Claude ("find the best headphones under EUR 150, make a carousel, edit the images nicely") left several
Make a post threads and many Library items: every ``create_package`` inserted a new run, every ``render_carousel`` added
a Library item and nothing told the app to reuse what it had. The rules now, enforced here on the server:

* **One user request = one run.** ``start_post`` opens a run (``status='draft'``) and returns ``run_id``; every render,
  upload and ``create_package`` takes that ``run_id``. A creator called WITHOUT ``run_id`` while this token has an open
  run (``active_run``: newest run the token made, still ``draft``/``ready``, nothing sent to Buffer or waiting for
  approval, touched in the last ``HQ_MCP_ACTIVE_RUN_S``, default 2 h) works on that run and answers ``reused: true``.
* **One request = one run = one thread (owner, 4 Nov 2026 16:14).** Follow-ups about the SAME content (switch carousel to
  reel, edit slide 3, two more reels of the same story) stay in that run. A NEW request with new content, even in the same
  chat, is a new run and thread (``start_post(new_request: true)``), not one giant thread. When it is unclear, the app asks
  the person; ``continue_run(run_id)`` joins an existing run. Every answer that names a run carries ``thread_url``.
* **A new run only for a new request (or a new chat).** ``start_post`` (always with ``user_asked: true``) and no ``run_id``
  is the person's new request: it opens a new run and is never refused for how many runs an app made (owner, 4 Nov 2026
  15:07: people open new chats in their AI app, and per-hour / per-day caps blocked them). The same request again within
  ``DEDUPE_S`` (10 min, same title) answers the run it already opened (``deduped: true``). On a creator, ``new_post: true``
  must come with ``user_asked: true``. The only guard is a lenient burst limit against a runaway loop
  (``HQ_MCP_NEW_RUNS_PER_MINUTE``, default 30 new runs a minute per agent token); there is no hourly or daily limit.
* **One run, many outputs.** Follow-ups stay in the run, even a change of format ("not a carousel, make it a reel":
  ``start_post(run_id, format='reel')`` then render with the same ``run_id``). Several outputs for one request ("make 3
  reels") are ONE run: each render takes ``output: 1..10`` and the run lists them in ``result.outputs`` (studio/mcp_client_brain.py).
* **Same call twice = same result.** ``idempotency_key`` (argument or ``_meta["huhqueue/idempotency_key"]``): the same key
  and arguments within 24 h replay the stored result, the same key with other arguments is an error. Without a key,
  the run/render tools in ``DERIVED`` get a key derived from (token, tool, canonical arguments) for 120 s, which catches
  the usual double call on a retry.
* **Run lookups are per person.** A ``run_id`` of another account, or one that was tidied away, answers 404.

Only agent tokens are disciplined: a signed-in person in the app (and REST for a human) is never limited here.
Rows are per person and token; ``delete_user`` removes them (``store.OWNED_ELSEWHERE``).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

from fastapi import Body, Request

from .actions import ActionError, Actor

ACTIVE_S = int(os.environ.get("HQ_MCP_ACTIVE_RUN_S", str(2 * 3600)))
# burst guard only (a runaway loop), never an hourly or daily limit on new runs (owner, 4 Nov 2026 15:07)
PER_MINUTE = int(os.environ.get("HQ_MCP_NEW_RUNS_PER_MINUTE", "30"))
DEDUPE_S = 10 * 60
IDEM_TTL = 24 * 3600
DERIVED_TTL = 120

# tools that open (or reuse) a post run when called without run_id
REUSERS = {"start_post", "create_package", "make_post"}
# tools that always make a new run / take of their own (burst guard only; a run_id they get is checked for ownership)
ALWAYS_NEW = {"draft_from_library", "run_agent", "edit_media", "clips_from_video", "split_video"}
# tools that take an optional run_id for media they render or store (checked for ownership)
RUN_MEDIA = {"render_carousel", "hero_edit", "edit_video", "render_reel", "split_video", "library_upload_url", "create_upload",
             "import_from_link", "create_package", "start_post", "continue_run",
             # image tools (studio/image_edit.py, Phase 2): the picture they fetch, cut out or edit is stored as run media
             "fetch_image", "cutout_product", "edit_image", "compose_product_card"}
# a new template or brand kit, or switching the active kit, only when the person asked (user_asked: true)
NEEDS_USER_ASKED = {"create_template", "create_brand_kit", "set_active_brand_kit"}
# tools whose identical repeat within DERIVED_TTL is the same call (a retry), never a second render or run
DERIVED = {"start_post", "continue_run", "create_package", "render_carousel", "hero_edit", "edit_video", "render_reel", "split_video",
           "library_upload_url", "import_from_link", "schedule_package", "make_post", "draft_from_library", "clips_from_video",
           # same picture + same ops within DERIVED_TTL is a retry: no second fetch, cut-out job or stored edit
           "fetch_image", "cutout_product", "edit_image", "compose_product_card"}
# never stored: answers that carry a single-use secret (a confirm token, an upload URL, a send code) or are part of a
# chunked upload
NO_IDEM = {"create_send_code", "create_upload", "upload_media_chunk", "finish_upload", "place_clips_in_week", "cleanup_agent_drafts",
           "submit_post", "preview_post"}
# arguments that steer this layer and never reach a tool's own logic or its idempotency hash
CONTROL = ("idempotency_key", "user_asked", "new_post", "new_request", "reason", "approval_id")

SCHEMA = ("CREATE TABLE IF NOT EXISTS mcp_idem (user_id TEXT, agent_id INTEGER, ikey TEXT, tool TEXT, args_hash TEXT, result TEXT, "
          "at REAL, PRIMARY KEY (user_id, agent_id, ikey))")
_lock = threading.Lock()
_FLAG = "_mcp_idem_ready"  # on the Store, never id(store): a reused id must not skip CREATE TABLE on a new database


def ensure(store) -> None:
    if getattr(store, _FLAG, False):
        return
    with _lock:
        if getattr(store, _FLAG, False):
            return
        store.x(SCHEMA.replace(" REAL", " DOUBLE PRECISION") if store.pg else SCHEMA)
        if store.pg:
            store.x("ALTER TABLE mcp_idem ENABLE ROW LEVEL SECURITY")
        setattr(store, _FLAG, True)


def is_agent(a: Actor) -> bool:
    return getattr(a, "kind", "") == "agent" and bool(a.agent)


def _rid(v: Any) -> int | None:
    if v in (None, "", 0, "0") and not isinstance(v, bool):
        return None
    try:
        if isinstance(v, (bool, float)):
            raise ValueError
        n = int(v)
    except (TypeError, ValueError):
        raise ActionError("run_id is the number start_post or create_package returned.") from None
    if n > 2**53:  # past any real id (and past what the database takes): a plain 400, never a 500
        raise ActionError("run_id is the number start_post or create_package returned.")
    return n if n > 0 else None


# ------------------------------------------------------------------------------------------------ runs
def own_run(store, a: Actor, rid: int) -> dict:
    """The person's own run, or 404 (another account's run, a run that doesn't exist, or one tidied into the archive)."""
    rows = store.q("SELECT id, user_id, kind, status, input, result, created, updated, agent_id FROM agent_runs WHERE id=? AND user_id=?",
                   (int(rid), a.uid))
    if not rows or rows[0]["status"] == "archived":
        raise ActionError("No such post run.", 404)
    return dict(rows[0])


def closed(res: dict) -> bool:
    """A run whose post went to Buffer (a draft or scheduled) or waits in Approvals is done: follow-ups start fresh."""
    for p in (res or {}).get("packages") or []:
        if p.get("sent") or p.get("status") in ("sent_to_buffer", "pending_approval") or p.get("approval_id"):
            return True
    return False


def _deleted(store, uid: str) -> set[int]:
    from .thread_bin import deleted_ids
    return deleted_ids(store, uid)


# deleted threads are left out in SQL, before the LIMIT, so they never hide an older open run (Greptile, 5 Nov 2026)
_NOT_DELETED = "AND id NOT IN (SELECT run_id FROM thread_deleted WHERE user_id=?) "


def _ensure_threads(store) -> None:
    from .thread_bin import _ensure
    _ensure(store)


def active_run(store, a: Actor) -> dict | None:
    """This token's open run: the newest one it made that is draft/ready, not sent or awaiting approval, touched within
    ACTIVE_S. Never another app's run (agent_id) and never another person's (user_id)."""
    if not is_agent(a):
        return None
    _ensure_threads(store)  # a thread the person deleted (in the Bin or purged) is never reused
    rows = store.q("SELECT id, kind, status, result, updated FROM agent_runs WHERE user_id=? AND agent_id=? AND status IN ('draft','ready') "
                   "AND updated>? " + _NOT_DELETED + "ORDER BY updated DESC, id DESC LIMIT 5",
                   (a.uid, a.agent["id"], time.time() - ACTIVE_S, a.uid))
    for r in rows:
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            continue
        if not closed(res if isinstance(res, dict) else {}):
            return dict(r)
    return None


def burst_guard(store, a: Actor) -> None:
    """Before a new run: only a runaway loop is stopped (more than PER_MINUTE new runs in 60 s from one agent token).
    There is no hourly or daily limit: a person opening new chats in their AI app is never blocked (owner, 4 Nov 2026)."""
    if not is_agent(a) or PER_MINUTE <= 0:
        return
    n = int(store.q("SELECT COUNT(*) AS n FROM agent_runs WHERE user_id=? AND agent_id=? AND created>?",
                    (a.uid, a.agent["id"], time.time() - 60))[0]["n"] or 0)
    if n < PER_MINUTE:
        return
    open_ = active_run(store, a)
    keep = f" To keep working on run {open_['id']}, pass run_id: {open_['id']}." if open_ else ""
    try:
        store.audit(a.uid, a.label, "new_run", "blocked", {"reason": "burst", "minute": n}, ok=False)
    except Exception:  # noqa: BLE001
        pass
    raise ActionError(f"This app started {n} posts in the last minute, which looks like a loop. Wait a minute and try "
                      f"again.{keep}", 429)


def _norm(t: Any) -> str:
    return " ".join(str(t or "").lower().split())[:200]


def same_request(store, a: Actor, title: str) -> dict | None:
    """The run this token opened for the SAME request (same title) in the last DEDUPE_S: a start_post retried or repeated
    by the app answers it instead of a second run. A different title is a new request and gets its own run."""
    if not is_agent(a) or not _norm(title):
        return None
    _ensure_threads(store)  # a deleted thread is not "the same request": asking again opens a new post (Greptile, 5 Nov)
    rows = store.q("SELECT id, input, result FROM agent_runs WHERE user_id=? AND agent_id=? AND status IN ('draft','ready') "
                   "AND created>? " + _NOT_DELETED + "ORDER BY id DESC LIMIT 10", (a.uid, a.agent["id"], time.time() - DEDUPE_S, a.uid))
    for r in rows:
        try:
            inp, res = json.loads(r["input"] or "{}"), json.loads(r["result"] or "{}")
        except ValueError:
            continue
        if inp.get("started_by") == "start_post" and _norm(inp.get("prompt")) == _norm(title) and not closed(res):
            return dict(r)
    return None


def mark_agent(store, a: Actor, rid: int) -> None:
    """Which agent token made the run (reuse, burst guard, cleanup). Only ever on the person's own run, only once."""
    if is_agent(a) and rid:
        store.x("UPDATE agent_runs SET agent_id=? WHERE id=? AND user_id=? AND agent_id IS NULL", (a.agent["id"], int(rid), a.uid))


def touch(store, a: Actor, rid: int) -> None:
    store.x("UPDATE agent_runs SET updated=? WHERE id=? AND user_id=?", (time.time(), int(rid), a.uid))


REUSED_NOTE = ("Updated your open post (run {rid}). Keep passing run_id: {rid}. For the person's NEW request (or a new chat), "
               "call start_post with user_asked: true instead.")
DEDUPED_NOTE = "Same request as run {rid} a moment ago: continuing that post (no second run). Keep passing run_id: {rid}."


def before(studio, a: Actor, name: str, args: dict) -> tuple[dict, dict | None]:
    """Run the rules before a tool: (arguments for the tool, an early answer or None). Agent tokens only."""
    if not is_agent(a):
        return args, None
    store = studio.store
    if name in NEEDS_USER_ASKED and args.get("user_asked") is not True:
        what = {"create_template": "a new template", "create_brand_kit": "a new brand kit",
                "set_active_brand_kit": "a different active brand kit"}[name]
        raise ActionError(f"Only make {what} when the person asked for it: ask them first, then call again with user_asked: true "
                          "and a one-line reason.", 403)
    rid = _rid(args.get("run_id")) if (name in RUN_MEDIA or name in REUSERS or name in ALWAYS_NEW) else None
    if rid:
        own_run(store, a, rid)  # 404 for someone else's run (and an archived one)
    if name not in REUSERS and name not in ALWAYS_NEW:
        return args, None
    if name == "make_post" and args.get("brain") != "external":
        return args, None  # server AI make_post: its own path (the paid-API consent gate)
    if name == "continue_run":
        if not rid:
            raise ActionError("continue_run needs the run_id of the post to continue (start_post returned it).")
        return args, None
    if name == "start_post" and args.get("new_request") is True and not rid:
        # "a new request with new content": the person asked for it, so it is a new run even if the title matches
        args = {**args, "new_post": True, "user_asked": True}
    new_post = args.get("new_post") is True
    if new_post and args.get("user_asked") is not True:
        raise ActionError("Starting another post needs the person's say-so: ask them, then call again with new_post: true and "
                          "user_asked: true (and a one-line reason). To change the post you're on, pass its run_id instead.", 403)
    if name == "start_post" and not rid and not new_post and args.get("user_asked") is not True:
        raise ActionError("start_post opens a post for the person's request: call it once per request with user_asked: true "
                          "and a one-line reason (the request in their words).", 403)
    if name == "start_post" and not rid:
        # the person's request: a NEW run (a new chat, a new ask), never the previous chat's run. Only the same request
        # repeated within DEDUPE_S answers the run it already opened.
        dup = None if new_post else same_request(store, a, args.get("title"))
        if dup:
            return {**args, "run_id": dup["id"], "_deduped": True}, None
        burst_guard(store, a)
        return args, None
    if name in REUSERS and not rid and not new_post:
        open_ = active_run(store, a)
        if open_:
            args = {**args, "run_id": open_["id"], "_reused": True}
            if name == "make_post":
                touch(store, a, open_["id"])
                run = studio.house.run(a, open_["id"])
                return args, {**run, "run_id": open_["id"], "reused": True, "note": REUSED_NOTE.format(rid=open_["id"])}
            return args, None
    if name in REUSERS and rid:
        return args, None
    if name in ALWAYS_NEW and rid:
        return args, None
    burst_guard(store, a)
    return args, None


def after(studio, a: Actor, name: str, args: dict, result: Any) -> Any:
    """After a creator: remember which token made the run; say when an open run was reused."""
    if not is_agent(a) or not isinstance(result, dict):
        return result
    rid = result.get("run_id") or (result.get("id") if result.get("kind") in ("agent", "client") else None)
    if name in REUSERS or name in ALWAYS_NEW:
        try:
            if rid:
                mark_agent(studio.store, a, int(rid))
        except (TypeError, ValueError):
            pass
    if args.get("_deduped") and rid:
        result = {**result, "reused": True, "deduped": True, "note": DEDUPED_NOTE.format(rid=rid)}
    elif args.get("_reused") and rid:
        result = {**result, "reused": True, "note": REUSED_NOTE.format(rid=rid)}
    if name in THREAD_LINKED and result.get("run_id") and "thread_url" not in result:
        url = thread_url(studio, result["run_id"])
        if url:
            result = {**result, "thread_url": url}
    return result


# tools that make or change a run (or report a render that lands on one): their answers carry the thread link
THREAD_LINKED = REUSERS | RUN_MEDIA | ALWAYS_NEW | {"continue_run", "render_status"}


def thread_url(studio, rid: Any) -> str:
    """The run's thread in HuhQueue (the Library's "View run" deep link opens it in Make a post)."""
    try:
        base = str(getattr(getattr(studio, "s", None), "public_url", "") or "").rstrip("/")
        return f"{base}/?tab=library&run={int(rid)}" if base else ""
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------------------------------------ idempotency
def _canon(args: dict) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in CONTROL and not k.startswith("_") and k != "server_ai"}
    return hashlib.sha256(json.dumps(clean, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def idem_key(name: str, args: dict, meta: dict | None) -> tuple[str, bool] | None:
    """(key, explicit?) for this call, or None when it is never replayed."""
    if name in NO_IDEM:
        return None
    k = args.get("idempotency_key")
    if k in (None, "") and isinstance(meta, dict):
        k = meta.get("huhqueue/idempotency_key") or meta.get("idempotency_key")
    if k not in (None, ""):
        k = str(k).strip()[:120]
        if k:
            return "k:" + k, True
    if name in DERIVED:
        if args.get("new_request") is True or args.get("new_post") is True:
            return None  # a deliberate new post is never answered from an earlier call's replay (an explicit key still is)
        if name == "create_package" and not args.get("media_urls"):
            return None  # stages the output's CURRENT media: a re-render in between must not be answered by a replay
        if name in ("start_post", "continue_run") and args.get("format"):
            return None  # a format switch reads the run's current format (carousel -> reel -> carousel within 120 s)
        return "d:" + name + ":" + _canon(args), False
    return None


def replay(store, a: Actor, name: str, args: dict, key: tuple[str, bool]) -> Any:
    """The stored result for the same key and arguments, None for a first call; a key reused with other arguments is an error."""
    if not is_agent(a):
        return None
    ensure(store)
    k, explicit = key
    ttl = IDEM_TTL if explicit else DERIVED_TTL
    rows = store.q("SELECT tool, args_hash, result, at FROM mcp_idem WHERE user_id=? AND agent_id=? AND ikey=?", (a.uid, a.agent["id"], k))
    if not rows or rows[0]["at"] < time.time() - ttl:
        return None
    r = rows[0]
    if r["tool"] != name or r["args_hash"] != _canon(args):
        raise ActionError("That idempotency_key was already used for a different call. Use a new key for a new action.", 409)
    try:
        out = json.loads(r["result"])
    except ValueError:
        return None
    if name in ("start_post", "continue_run") and isinstance(out, dict) and isinstance(out.get("run_id"), int) \
            and out["run_id"] in _deleted(store, a.uid):
        return None  # it would open a deleted thread: ask again (renders and other finished work still replay)
    return {**out, "idempotent_replay": True} if isinstance(out, dict) else out


def remember(store, a: Actor, name: str, args: dict, key: tuple[str, bool], result: Any) -> None:
    if not is_agent(a):
        return
    try:
        ensure(store)
        blob = json.dumps(result, default=str)
        if len(blob) > 200_000:
            return  # a huge answer (preview stills) is not kept; the call stays safe to retry by its own rules
        now = time.time()
        store.x("DELETE FROM mcp_idem WHERE user_id=? AND agent_id=? AND ikey=?", (a.uid, a.agent["id"], key[0]))
        store.x("INSERT INTO mcp_idem(user_id,agent_id,ikey,tool,args_hash,result,at) VALUES(?,?,?,?,?,?,?)",
                (a.uid, a.agent["id"], key[0], name[:60], _canon(args), blob, now))
        if int(now) % 20 == 0:
            store.x("DELETE FROM mcp_idem WHERE at<?", (now - IDEM_TTL,))
    except Exception:  # noqa: BLE001 - a bookkeeping row never breaks the tool
        pass


KEEP_REASON = {"start_post", "continue_run"}  # start_post keeps the person's words (reason): its thread and audit line quote them


def strip_control(args: dict, name: str = "") -> dict:
    """The arguments a tool sees: run_id stays (and reason for start_post), the layer's own fields go."""
    return {k: v for k, v in (args or {}).items() if k not in CONTROL or k == "run_id" or (k == "reason" and name in KEEP_REASON)}


# ------------------------------------------------------------------------------------------------ tools
CLEANUP_TTL = 10 * 60
S = {"type": "string"}
TOOLS: list[dict[str, Any]] = [
    {"name": "start_post", "description": "Open ONE post for the person's request and get its run_id (no HuhQueue AI, nothing is "
     "rendered or posted). Call it once per request, with user_asked: true and the request as reason, then pass run_id to every "
     "render_carousel, hero_edit, edit_video, render_reel, split_video, upload and create_package call: follow-ups ('brighter', "
     "'swap slide 3', 'shorter caption') update this same post and its thread. A change of format ('not a carousel, make it a "
     "reel') is the SAME post: call start_post(run_id, format='reel') and render with the same run_id. Several outputs for one "
     "request ('make 3 reels') are ONE post: render each with output: 1, 2, 3. A new request from the person, or a new chat, "
     "is a new start_post (never limited by how many posts the app made): one request = one run = one thread, so a NEW "
     "request with new content in the same chat is a new start_post(new_request: true), not more of the old thread. When "
     "you can't tell whether it's about the same content, ask the person ('continue in the current post or start a new "
     "one?') and use continue_run(run_id) or start_post(new_request: true). The same request again within 10 minutes "
     "returns the post it opened (deduped: true). Every answer carries run_id and thread_url.",
     "inputSchema": {"type": "object", "required": ["title", "user_asked"], "properties": {
         "title": {"type": "string", "description": "Short title of the post, from the person's request"},
         "brief": {"type": "string", "description": "One line: what the person asked for"},
         "channel_ids": {"type": "array", "items": S, "description": "Channels the person named (optional)"},
         "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"],
                    "description": "With run_id: the person changed the format; the same post and thread switch to it"},
         "user_asked": {"type": "boolean", "description": "true: the person asked for this post"},
         "reason": {"type": "string", "description": "The request in a few words"},
         "new_post": {"type": "boolean", "description": "true: a separate post even if the title matches one opened a moment ago"},
         "new_request": {"type": "boolean", "description": "true: the person made a NEW request with new content (a new run and "
                         "thread even in the same chat, even if the title matches)"},
         "run_id": {"type": "integer", "description": "Continue this post instead (a follow-up or a change of format)"},
         "idempotency_key": S}}},
    {"name": "continue_run", "description": "Join an existing post (run) for a follow-up about the SAME content: the person's "
     "words go in its thread and you get its run_id and thread_url back; then pass run_id to every render and create_package "
     "call. Use it when the person says to keep working on an earlier post, or answered 'continue' when you asked whether a "
     "request belongs to the current post. format switches the post (e.g. carousel to reel), as start_post(run_id, format) "
     "does. A post already sent to Buffer or waiting in Approvals can't be continued: start a new one.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {
         "run_id": {"type": "integer", "description": "The post to continue (start_post returned it)"},
         "reason": {"type": "string", "description": "The person's follow-up in a few words (added to the thread)"},
         "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"],
                    "description": "Optional: the person changed the format"},
         "idempotency_key": S}}},
    {"name": "cleanup_agent_drafts", "description": "Tidy up what AI apps left behind: post runs an app made that never went to "
     "Buffer or Approvals, and Library items an app uploaded or rendered that no post uses. Step 1 (default dry_run: true) "
     "LISTS them (title, when, app) with a single-use confirm_token and changes nothing: show the list to the person. Step 2, "
     "only after they say yes: call again with confirm_token. Runs are archived (hidden from Make a post) and Library items go "
     "to the Library Bin (restorable for 7 days); nothing is deleted for good and nothing in Buffer is touched. "
     "older_than_h (default 1) skips anything newer; app limits it to one app's name.",
     "inputSchema": {"type": "object", "properties": {"dry_run": {"type": "boolean", "default": True}, "confirm_token": S,
                                                      "older_than_h": {"type": "number", "minimum": 0, "maximum": 720}, "app": S}}},
]
NAMES = {t["name"] for t in TOOLS}
ROLE_SOURCE = "mcp_client_brain"


def start_post(studio, a: Actor, args: dict) -> dict:
    store = studio.store
    rid = _rid(args.get("run_id"))
    if rid:
        run = own_run(store, a, rid)
        from .thread_bin import purged
        if purged(store, a.uid, rid):  # a thread deleted for good takes no more writes (outputs stay readable)
            raise ActionError("No such post run.", 404)
        touch(store, a, rid)
        try:
            res = json.loads(run["result"] or "{}") or {}
            inp = json.loads(run["input"] or "{}") or {}
        except ValueError:
            res, inp = {}, {}
        title = (res.get("story") or {}).get("headline") or inp.get("prompt") or ""
        fmt = args.get("format") if args.get("format") in ("carousel", "reel", "photo", "text") else ""
        out = {"run_id": rid, "status": run["status"], "title": str(title)[:120], "reused": True, "spent_usd": 0,
               "format": inp.get("format") or "", "outputs": len(res.get("outputs") or []),
               "next": "Pass this run_id to every render, upload and create_package call for this request."}
        if fmt and fmt != inp.get("format") and is_agent(a) and run["kind"] == "client" and not closed(res):
            # "I don't want a carousel, make it a reel": the same run and thread switch format (owner, 4 Nov 2026)
            was = inp.get("format") or ""
            inp["format"], res["format"] = fmt, fmt
            said = " ".join(str(args.get("reason") or "").split())[:300] or f"Make it a {fmt} instead"
            res.setdefault("thread", []).append({"role": "user", "text": said, "at": time.time()})
            store.x("UPDATE agent_runs SET input=?, result=?, updated=? WHERE id=? AND user_id=?",
                    (json.dumps(inp), json.dumps(res, default=str), time.time(), rid, a.uid))
            out.update(format=fmt, format_was=was, next=f"Same post, now a {fmt}: render it with run_id {rid} (output 1 is "
                       "replaced in place) and call create_package(run_id) again.")
        return out
    # opening a post spends nothing and reaches nobody: the kill switch answers, but not the token's daily action cap
    # (no per-day limit on new runs, owner 4 Nov 2026 15:07; the burst guard in before() stops a runaway loop)
    studio.kill_switch_check(a, "start_post")
    title = " ".join(str(args.get("title") or "").split())[:200]
    if not title:
        raise ActionError("Give the post a short title (from the person's request).")
    prev_run = active_run(store, a) if is_agent(a) else None
    prev = prev_run["id"] if prev_run else None
    chans = [str(c) for c in args.get("channel_ids") or [] if c][:20]
    inp = {"prompt": title, "brief": str(args.get("brief") or "")[:500], "via": ROLE_SOURCE, "started_by": "start_post",
           "channel_ids": chans, "format": args.get("format") if args.get("format") in ("carousel", "reel", "photo", "text") else ""}
    now = time.time()
    said = " ".join(str(args.get("brief") or args.get("reason") or "").split())[:500] or title
    res = {"packages": [], "media": [], "story": {"headline": title[:120]}, "brain": "client", "outputs": [],
           "format": inp["format"], "thread": [{"role": "user", "text": said, "at": now}]}
    rid = store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated,agent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (a.uid, "client", json.dumps(inp), "draft", json.dumps([]), json.dumps(res), now, now,
                   a.agent["id"] if is_agent(a) else None))
    store.audit(a.uid, a.label, "start_post", "read", {"run": rid, "reason": str(args.get("reason") or "")[:120]})
    out = {"run_id": rid, "status": "draft", "title": title[:120], "reused": False, "spent_usd": 0,
           "next": "Keep this run_id for this request. Pass it to render_carousel / hero_edit / edit_video / render_reel / "
                   "uploads and to create_package; follow-ups (and a change of format) update this same post. Several "
                   "outputs: pass output: 1, 2, 3 to each render."}
    if prev:
        out.update(previous_run_id=prev, note=f"New post (run {rid}) for a new request. If the person meant to change run "
                   f"{prev} instead, ask them and use continue_run(run_id={prev}).")
    return out


def continue_run(studio, a: Actor, args: dict) -> dict:
    """Join an existing run for a follow-up about the same content (the person said so, or answered 'continue')."""
    store = studio.store
    rid = _rid(args.get("run_id"))
    if not rid:
        raise ActionError("continue_run needs the run_id of the post to continue (start_post returned it).")
    studio.kill_switch_check(a, "continue_run")
    run = own_run(store, a, rid)
    from .thread_bin import purged
    if purged(store, a.uid, rid):  # before the message is appended: a purged conversation stays gone
        raise ActionError("No such post run.", 404)
    try:
        res = json.loads(run["result"] or "{}") or {}
    except ValueError:
        res = {}
    if closed(res):
        raise ActionError(f"Run {rid} already went to Buffer or waits in Approvals, so it can't take more changes. Start a new "
                          "post with start_post(new_request: true).", 409)
    said = " ".join(str(args.get("reason") or "").split())[:300]
    fmt = args.get("format") if args.get("format") in ("carousel", "reel", "photo", "text") else ""
    if said and not fmt:  # with a format switch, start_post(run_id, format) writes the person's words into the thread
        res.setdefault("thread", []).append({"role": "user", "text": said, "at": time.time()})
        store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=? AND user_id=?",
                (json.dumps(res, default=str), time.time(), rid, a.uid))
    out = start_post(studio, a, {"run_id": rid, "format": fmt, "reason": said})
    store.audit(a.uid, a.label, "continue_run", "read", {"run": rid, "reason": said[:120]})
    return {**out, "joined": True, "next": f"Continuing run {rid}: pass run_id: {rid} to every render and create_package "
            "call. " + str(out.get("next") or "")}


def _app_of(store, uid: str) -> dict[int, str]:
    from . import mcp_runs
    out = {}
    for r in store.q("SELECT id, name, client_info, app FROM agent_tokens WHERE user_id=?", (uid,)):
        out[r["id"]] = mcp_runs.app_name(dict(r))
    return out


def _leftovers(studio, a: Actor, older_than_h: float, app: str) -> tuple[list[dict], list[dict]]:
    """(runs, library items) an AI app left that no post needs. Only this person's rows."""
    store, lib = studio.store, studio.house.library
    cutoff = time.time() - max(0.0, float(older_than_h)) * 3600
    apps = _app_of(store, a.uid)
    want = (app or "").strip().casefold()
    runs = []
    for r in store.q("SELECT id, kind, input, result, status, created, updated, agent_id FROM agent_runs WHERE user_id=? AND agent_id IS NOT NULL "
                     "AND status IN ('draft','ready','failed') AND updated<? ORDER BY id DESC LIMIT 200", (a.uid, cutoff)):
        try:
            res, inp = json.loads(r["result"] or "{}"), json.loads(r["input"] or "{}")
        except ValueError:
            continue
        if not isinstance(res, dict) or closed(res):
            continue
        name = apps.get(r["agent_id"], "Your AI app")
        if want and want not in name.casefold():
            continue
        title = (res.get("story") or {}).get("headline") or inp.get("prompt") or "A post"
        thumb = next((m.get("url") for p in res.get("packages") or [] for m in p.get("media") or [] if m.get("url")), "")
        runs.append({"run_id": r["id"], "title": str(title)[:90], "created": r["created"], "app": name, "status": r["status"],
                     "thumb": thumb})
    run_ids = {r["run_id"] for r in runs}
    from .library import ensure as lib_ensure
    lib_ensure(store)
    rows = store.q("SELECT * FROM library_items WHERE user_id=? AND agent_id IS NOT NULL AND storage NOT IN ('pending','trash') "
                   "AND created<? ORDER BY id DESC LIMIT 400", (a.uid, cutoff))
    all_runs = [r for r in lib._runs(a) if r["run_id"] not in run_ids and r["status"] != "archived"]  # posts that stay
    items = []
    for r in rows:
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        if d.get("run_id") and d["run_id"] not in run_ids and any(x["run_id"] == d["run_id"] for x in all_runs):
            continue  # it belongs to a post that stays
        used, _live, _sched = lib._uses(d, all_runs)
        if used:
            continue
        name = apps.get(d.get("agent_id"), "Your AI app")
        if want and want not in name.casefold():
            continue
        items.append({"library_id": d["id"], "name": d["name"], "kind": d["kind"], "created": d["created"], "app": name,
                      "run_id": d.get("run_id"), "thumb": f"/api/library/{d['id']}/file" if d["kind"] != "video" else ""})
    return runs, items


def cleanup_agent_drafts(studio, a: Actor, args: dict) -> dict:
    """Dry run lists and mints a single-use confirm_token; the confirm archives runs and moves Library items to the Bin."""
    from .store import sha
    store = studio.store
    older = args.get("older_than_h") if args.get("older_than_h") is not None else 1
    try:
        older = max(0.0, min(720.0, float(older)))
    except (TypeError, ValueError):
        raise ActionError("older_than_h is a number of hours.")
    if args.get("confirm_token"):
        studio._guard_write(a, "cleanup_agent_drafts")
        tok = str(args["confirm_token"])
        rows = store.q("SELECT * FROM confirms WHERE token_hash=? AND user_id=?", (sha(tok), a.uid))
        if not rows:
            raise ActionError("Unknown confirm_token. Run cleanup_agent_drafts with dry_run first.", 404)
        row = rows[0]
        if row["used"] or row["expires"] < time.time():
            raise ActionError("This confirm_token is used or expired. Run the dry run again.", 410)
        if (row["agent_id"] or None) != (a.agent["id"] if is_agent(a) else None):
            raise ActionError("This confirm_token belongs to a different caller.", 403)
        plan = json.loads(row["payload"] or "{}")
        if plan.get("kind") != "cleanup":
            raise ActionError("That confirm_token is for something else.", 409)
        store.x("UPDATE confirms SET used=1 WHERE token_hash=?", (row["token_hash"],))
        runs_now, _ = _leftovers(studio, a, plan.get("older_than_h", 1), plan.get("app", ""))
        listed_runs = set(plan.get("runs") or [])
        archived = []
        for r in runs_now:  # only what the person saw in the list AND is still a leftover right now
            if r["run_id"] not in listed_runs:
                continue
            store.x("UPDATE agent_runs SET status='archived', updated=? WHERE id=? AND user_id=? AND status IN ('draft','ready','failed')",
                    (time.time(), r["run_id"], a.uid))
            store.audit(a.uid, a.label, "archive_run", "safety", {"run": r["run_id"], "by": "cleanup_agent_drafts"})
            archived.append(r["run_id"])
        _, items_now = _leftovers(studio, a, plan.get("older_than_h", 1), plan.get("app", ""))
        listed_items = set(plan.get("items") or [])
        ids = [i["library_id"] for i in items_now if i["library_id"] in listed_items]
        binned, refused = [], []
        if ids:
            out = studio.house.library.bulk_delete(a, ids, mode="bin")
            binned, refused = out["deleted"], out["refused"]
        return {"archived_runs": archived, "binned_items": binned, "refused": refused, "bin_days": 7, "spent_usd": 0,
                "note": "Archived runs are hidden from Make a post; Library items are in the Library Bin (restore_library_items)."}
    runs, items = _leftovers(studio, a, older, str(args.get("app") or ""))
    out = {"dry_run": True, "runs": runs, "library_items": items, "count": len(runs) + len(items), "spent_usd": 0}
    if runs or items:
        import secrets
        token = "cf_" + secrets.token_urlsafe(24)
        payload = {"kind": "cleanup", "runs": [r["run_id"] for r in runs], "items": [i["library_id"] for i in items],
                   "older_than_h": older, "app": str(args.get("app") or "")[:60]}
        store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires) VALUES(?,?,?,?,?)",
                (sha(token), a.uid, a.agent["id"] if is_agent(a) else None, json.dumps(payload), time.time() + CLEANUP_TTL))
        out.update(confirm_token=token, expires_in=CLEANUP_TTL,
                   next="Show this list to the person. Only if they say yes: cleanup_agent_drafts(confirm_token=...).")
    else:
        out["next"] = "Nothing to tidy up."
    return out


def call_tool(studio, actor: Actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    from .thread_bin import with_thread_id
    if name == "start_post":
        return with_thread_id(studio.store, actor, start_post(studio, actor, args or {}))
    if name == "continue_run":
        return with_thread_id(studio.store, actor, continue_run(studio, actor, args or {}))
    return cleanup_agent_drafts(studio, actor, args or {})


# ------------------------------------------------------------------------------------------------ REST (UI = REST = MCP)
def add_routes(app, house, actor) -> None:
    @app.post("/api/agent/start-post")
    def rest_start_post(request: Request, body: dict = Body(default={})):
        """start_post: open one post for a request ({title, brief, channel_ids, format}); returns run_id."""
        a = actor(request, write=True)
        b = dict(body or {})
        args, early = before(house.studio, a, "start_post", b)
        from .thread_bin import with_thread_id
        return with_thread_id(house.store, a, after(house.studio, a, "start_post", args, early if early is not None else start_post(house.studio, a, strip_control(args, "start_post"))))

    @app.post("/api/agent/continue-run")
    def rest_continue_run(request: Request, body: dict = Body(default={})):
        """continue_run: join an existing run for a follow-up about the same content ({run_id, reason, format})."""
        a = actor(request, write=True)
        b = dict(body or {})
        args, early = before(house.studio, a, "continue_run", b)
        from .thread_bin import with_thread_id
        return with_thread_id(house.store, a, after(house.studio, a, "continue_run", args, early if early is not None else continue_run(house.studio, a, strip_control(args, "continue_run"))))

    @app.post("/api/agent/cleanup")
    def rest_cleanup(request: Request, body: dict = Body(default={})):
        """cleanup_agent_drafts: {} lists what AI apps left (nothing changes, single-use confirm_token);
        {"confirm_token": ...} archives those runs and moves those Library items to the Bin."""
        return cleanup_agent_drafts(house.studio, actor(request, write=True), dict(body or {}))
