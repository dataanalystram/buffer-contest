"""Run discipline for AI apps over MCP (Phase 1 of docs/plans/AGENT_RUNS_EDITING_COMMERCE.md, owner decisions 4 Nov 2026).

One prompt in Claude ("find the best headphones under EUR 150, make a carousel, edit the images nicely") left several
Make a post threads and many Library items: every ``create_package`` inserted a new run, every ``render_carousel`` added
a Library item and nothing told the app to reuse what it had. The rules now, enforced here on the server:

* **One user request = one run.** ``start_post`` opens a run (``status='draft'``) and returns ``run_id``; every render,
  upload and ``create_package`` takes that ``run_id``. A creator called WITHOUT ``run_id`` while this token has an open
  run (``active_run``: newest run the token made, still ``draft``/``ready``, nothing sent to Buffer or waiting for
  approval, touched in the last ``HQ_MCP_ACTIVE_RUN_S``, default 2 h) works on that run and answers ``reused: true``.
* **A new run only when the person asked.** ``new_post: true`` must come with ``user_asked: true``; ``start_post`` always
  needs ``user_asked: true``. Over the caps (``HQ_MCP_NEW_RUNS_PER_HOUR`` = 3, ``HQ_MCP_NEW_RUNS_PER_DAY`` = 20 per agent
  token) a new run is refused with a plain error that names the open run to keep working on.
* **Same call twice = same result.** ``idempotency_key`` (argument or ``_meta["huhqueue/idempotency_key"]``): the same key
  and arguments within 24 h replay the stored result, the same key with other arguments is an error. Without a key,
  the run/render tools in ``DERIVED`` get a key derived from (token, tool, canonical arguments) for 120 s, which catches
  the usual double call on a retry.
* **Run lookups are per person.** A ``run_id`` of another account, or one that was tidied away, answers 404.

Only agent tokens are disciplined: a signed-in person in the app (and REST for a human) is never capped here.
Rows are per person and token; ``delete_user`` removes them (``store.OWNED_ELSEWHERE``).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

from .actions import ActionError, Actor

ACTIVE_S = int(os.environ.get("HQ_MCP_ACTIVE_RUN_S", str(2 * 3600)))
PER_HOUR = int(os.environ.get("HQ_MCP_NEW_RUNS_PER_HOUR", "3"))
PER_DAY = int(os.environ.get("HQ_MCP_NEW_RUNS_PER_DAY", "20"))
IDEM_TTL = 24 * 3600
DERIVED_TTL = 120

# tools that open (or reuse) a post run when called without run_id
REUSERS = {"start_post", "create_package", "make_post"}
# tools that always make a new run / take of their own (capped; a run_id they get is checked for ownership)
ALWAYS_NEW = {"draft_from_library", "run_agent", "edit_media", "clips_from_video", "split_video"}
# tools that take an optional run_id for media they render or store (checked for ownership)
RUN_MEDIA = {"render_carousel", "hero_edit", "edit_video", "render_reel", "split_video", "library_upload_url", "create_upload",
             "import_from_link", "create_package", "start_post"}
# a new template or brand kit, or switching the active kit, only when the person asked (user_asked: true)
NEEDS_USER_ASKED = {"create_template", "create_brand_kit", "set_active_brand_kit"}
# tools whose identical repeat within DERIVED_TTL is the same call (a retry), never a second render or run
DERIVED = {"start_post", "create_package", "render_carousel", "hero_edit", "edit_video", "render_reel", "split_video",
           "library_upload_url", "import_from_link", "schedule_package", "make_post", "draft_from_library", "clips_from_video"}
# never stored: answers that carry a single-use secret (a confirm token, an upload URL, a send code) or are part of a
# chunked upload
NO_IDEM = {"create_send_code", "create_upload", "upload_media_chunk", "finish_upload", "place_clips_in_week", "cleanup_agent_drafts",
           "submit_post", "preview_post"}
# arguments that steer this layer and never reach a tool's own logic or its idempotency hash
CONTROL = ("idempotency_key", "user_asked", "new_post", "reason", "approval_id")

SCHEMA = ("CREATE TABLE IF NOT EXISTS mcp_idem (user_id TEXT, agent_id INTEGER, ikey TEXT, tool TEXT, args_hash TEXT, result TEXT, "
          "at REAL, PRIMARY KEY (user_id, agent_id, ikey))")
_ready: set[int] = set()
_lock = threading.Lock()


def ensure(store) -> None:
    if id(store) in _ready:
        return
    with _lock:
        if id(store) in _ready:
            return
        store.x(SCHEMA.replace(" REAL", " DOUBLE PRECISION") if store.pg else SCHEMA)
        if store.pg:
            store.x("ALTER TABLE mcp_idem ENABLE ROW LEVEL SECURITY")
        _ready.add(id(store))


def is_agent(a: Actor) -> bool:
    return getattr(a, "kind", "") == "agent" and bool(a.agent)


def _rid(v: Any) -> int | None:
    if v in (None, "", 0, "0"):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
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


def active_run(store, a: Actor) -> dict | None:
    """This token's open run: the newest one it made that is draft/ready, not sent or awaiting approval, touched within
    ACTIVE_S. Never another app's run (agent_id) and never another person's (user_id)."""
    if not is_agent(a):
        return None
    rows = store.q("SELECT id, kind, status, result, updated FROM agent_runs WHERE user_id=? AND agent_id=? AND status IN ('draft','ready') "
                   "AND updated>? ORDER BY updated DESC, id DESC LIMIT 5", (a.uid, a.agent["id"], time.time() - ACTIVE_S))
    for r in rows:
        try:
            res = json.loads(r["result"] or "{}")
        except ValueError:
            continue
        if not closed(res if isinstance(res, dict) else {}):
            return dict(r)
    return None


def counts(store, a: Actor) -> tuple[int, int]:
    now = time.time()
    rows = store.q("SELECT SUM(CASE WHEN created>? THEN 1 ELSE 0 END) AS h, COUNT(*) AS d FROM agent_runs WHERE user_id=? AND agent_id=? "
                   "AND created>?", (now - 3600, a.uid, a.agent["id"], now - 86400))
    return int(rows[0]["h"] or 0), int(rows[0]["d"] or 0)


def check_caps(store, a: Actor) -> None:
    """Before a new run: at most PER_HOUR an hour and PER_DAY a day per agent token. The error tells the app what to do."""
    if not is_agent(a):
        return
    hour, day = counts(store, a)
    if hour < PER_HOUR and day < PER_DAY:
        return
    open_ = active_run(store, a)
    keep = f" or keep working on run {open_['id']} (pass run_id: {open_['id']})" if open_ else " or keep working on the post you already made (pass its run_id)"
    if hour >= PER_HOUR:
        msg = f"You've started {hour} posts in the last hour from this app. Ask the person before starting another{keep}."
    else:
        msg = f"You've started {day} posts today from this app (the limit is {PER_DAY}). Ask the person before starting another{keep}."
    try:
        store.audit(a.uid, a.label, "new_run", "blocked", {"reason": "run cap", "hour": hour, "day": day}, ok=False)
    except Exception:  # noqa: BLE001
        pass
    raise ActionError(msg, 429)


def mark_agent(store, a: Actor, rid: int) -> None:
    """Which agent token made the run (caps, reuse, cleanup). Only ever on the person's own run, only once."""
    if is_agent(a) and rid:
        store.x("UPDATE agent_runs SET agent_id=? WHERE id=? AND user_id=? AND agent_id IS NULL", (a.agent["id"], int(rid), a.uid))


def touch(store, a: Actor, rid: int) -> None:
    store.x("UPDATE agent_runs SET updated=? WHERE id=? AND user_id=?", (time.time(), int(rid), a.uid))


REUSED_NOTE = "Updated your open post (run {rid}). Pass new_post: true with user_asked: true only when the person asks for another post."


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
        return args, None  # server AI make_post: its own path (consent gate) plus the cap below
    new_post = args.get("new_post") is True
    if new_post and args.get("user_asked") is not True:
        raise ActionError("Starting another post needs the person's say-so: ask them, then call again with new_post: true and "
                          "user_asked: true (and a one-line reason). To change the post you're on, pass its run_id instead.", 403)
    if name == "start_post" and not rid and not new_post and args.get("user_asked") is not True:
        raise ActionError("start_post opens a post for the person's request: call it once per request with user_asked: true "
                          "and a one-line reason (the request in their words).", 403)
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
    check_caps(store, a)
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
    if args.get("_reused") and rid:
        result = {**result, "reused": True, "note": REUSED_NOTE.format(rid=rid)}
    return result


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


def strip_control(args: dict) -> dict:
    """The arguments a tool sees: run_id stays, the layer's own fields go."""
    return {k: v for k, v in (args or {}).items() if k not in CONTROL or k == "run_id"}


# ------------------------------------------------------------------------------------------------ tools
CLEANUP_TTL = 10 * 60
S = {"type": "string"}
TOOLS: list[dict[str, Any]] = [
    {"name": "start_post", "description": "Open ONE post for the person's request and get its run_id (no HuhQueue AI, nothing is "
     "rendered or posted). Call it once per request, with user_asked: true and the request as reason, then pass run_id to every "
     "render_carousel, hero_edit, edit_video, render_reel, split_video, upload and create_package call: follow-ups ('brighter', "
     "'swap slide 3', 'shorter caption') update this same post and its thread. If this app already has an open post (made in the "
     "last 2 hours, not sent to Buffer), that post is returned with reused: true: keep working on it. To start a different "
     "post only when the person asks for one, pass new_post: true with user_asked: true. At most 3 new posts an hour and 20 a "
     "day per app.",
     "inputSchema": {"type": "object", "required": ["title", "user_asked"], "properties": {
         "title": {"type": "string", "description": "Short title of the post, from the person's request"},
         "brief": {"type": "string", "description": "One line: what the person asked for"},
         "channel_ids": {"type": "array", "items": S, "description": "Channels the person named (optional)"},
         "format": {"type": "string", "enum": ["carousel", "reel", "photo", "text"]},
         "user_asked": {"type": "boolean", "description": "true: the person asked for this post"},
         "reason": {"type": "string", "description": "The request in a few words"},
         "new_post": {"type": "boolean", "description": "true starts another post next to the open one (needs user_asked: true)"},
         "run_id": {"type": "integer", "description": "Continue this post instead"},
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
        touch(store, a, rid)
        title = ""
        try:
            title = ((json.loads(run["result"] or "{}").get("story") or {}).get("headline") or
                     json.loads(run["input"] or "{}").get("prompt") or "")
        except (ValueError, AttributeError):
            pass
        return {"run_id": rid, "status": run["status"], "title": str(title)[:120], "reused": True, "spent_usd": 0,
                "next": "Pass this run_id to every render, upload and create_package call for this request."}
    studio._guard_write(a, "start_post")
    title = " ".join(str(args.get("title") or "").split())[:200]
    if not title:
        raise ActionError("Give the post a short title (from the person's request).")
    chans = [str(c) for c in args.get("channel_ids") or [] if c][:20]
    inp = {"prompt": title, "brief": str(args.get("brief") or "")[:500], "via": ROLE_SOURCE, "started_by": "start_post",
           "channel_ids": chans, "format": args.get("format") if args.get("format") in ("carousel", "reel", "photo", "text") else ""}
    res = {"packages": [], "media": [], "story": {"headline": title[:120]}, "brain": "client"}
    now = time.time()
    rid = store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated,agent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                  (a.uid, "client", json.dumps(inp), "draft", json.dumps([]), json.dumps(res), now, now,
                   a.agent["id"] if is_agent(a) else None))
    store.audit(a.uid, a.label, "start_post", "read", {"run": rid, "reason": str(args.get("reason") or "")[:120]})
    return {"run_id": rid, "status": "draft", "title": title[:120], "reused": False, "spent_usd": 0,
            "next": "Keep this run_id. Pass it to render_carousel / hero_edit / edit_video / uploads and to create_package; "
                    "follow-ups update this same post."}


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
    if name == "start_post":
        return start_post(studio, actor, args or {})
    return cleanup_agent_drafts(studio, actor, args or {})


# ------------------------------------------------------------------------------------------------ REST (UI = REST = MCP)
def add_routes(app, house, actor) -> None:
    from fastapi import Body, Request

    @app.post("/api/agent/start-post")
    def rest_start_post(request: Request, body: dict = Body(default={})):
        """start_post: open one post for a request ({title, brief, channel_ids, format}); returns run_id."""
        a = actor(request, write=True)
        b = dict(body or {})
        args, early = before(house.studio, a, "start_post", b)
        return after(house.studio, a, "start_post", args, early if early is not None else start_post(house.studio, a, strip_control(args)))

    @app.post("/api/agent/cleanup")
    def rest_cleanup(request: Request, body: dict = Body(default={})):
        """cleanup_agent_drafts: {} lists what AI apps left (nothing changes, single-use confirm_token);
        {"confirm_token": ...} archives those runs and moves those Library items to the Bin."""
        return cleanup_agent_drafts(house.studio, actor(request, write=True), dict(body or {}))
