"""This app's own Buffer drafts: no duplicates across runs, and a scoped delete (owner report 6 Nov 2026).

Run 73 left four Buffer drafts and run 74, about the same story, made four more. Now:

* ``guard_duplicate`` (schedule_package mode=draft from an AI app): when an earlier run of THIS app already saved a Buffer
  draft of the same story (same title, or same source link) on the same channel, nothing is created: the app gets
  ``duplicate_draft`` with the earlier draft and the choice. ``on_duplicate: 'update'`` edits that draft (editPost:
  Studio.replace_draft, drafts only) with this package and moves it to this run; ``'new'`` makes a second draft (only when
  the person asked).
* ``delete_drafts``: dry run lists the Buffer drafts this app created (from its own runs' packages) with a single-use
  confirm_token; the confirm deletes only those that are STILL drafts in Buffer (Studio.delete_draft refuses scheduled and
  sent posts). A draft another app or the person made is never listed.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from typing import Any

from .actions import ActionError, Actor

LOOKBACK_S = 14 * 86400
CONFIRM_TTL = 10 * 60
S = {"type": "string"}


def _norm(t: Any) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", str(t or "").lower()) if len(w) > 2}


def same_story(a: dict, b: dict) -> bool:
    """Two runs about the same story: the same source link, or titles that share most of their words."""
    la, lb = str(a.get("link") or ""), str(b.get("link") or "")
    if la and la == lb:
        return True
    wa, wb = _norm(a.get("title")), _norm(b.get("title"))
    if not wa or not wb:
        return False
    return len(wa & wb) / max(1, len(wa | wb)) >= 0.6


def _story(run_row: dict) -> dict:
    try:
        inp, res = json.loads(run_row.get("input") or "{}") or {}, json.loads(run_row.get("result") or "{}") or {}
    except ValueError:
        return {}
    urls = (inp.get("intake") or {}).get("urls") or []
    return {"title": (res.get("story") or {}).get("headline") or inp.get("prompt") or "",
            "link": inp.get("source_url") or (urls[0] if urls else "") or (res.get("source") or {}).get("url") or ""}


def _draft_id(pk: dict) -> str:
    sent = pk.get("sent") if isinstance(pk.get("sent"), dict) else {}
    if pk.get("status") == "sent_to_buffer" and sent.get("mode") == "draft" and sent.get("buffer_post_id"):
        return str(sent["buffer_post_id"])
    return str((pk.get("buffer_draft") or {}).get("buffer_post_id") or "")


def app_drafts(store, a: Actor, exclude_run: int | None = None) -> list[dict]:
    """Buffer drafts THIS app saved from its own runs (agent_id = this token), newest first."""
    if a.kind != "agent" or not a.agent:
        return []
    out = []
    for r in store.q("SELECT id, input, result, updated FROM agent_runs WHERE user_id=? AND agent_id=? AND updated>? "
                     "AND status<>'archived' ORDER BY id DESC LIMIT 200", (a.uid, a.agent["id"], time.time() - LOOKBACK_S)):
        if exclude_run and r["id"] == exclude_run:
            continue
        try:
            res = json.loads(r["result"] or "{}") or {}
        except ValueError:
            continue
        st = _story(dict(r))
        for pk in res.get("packages") or []:
            pid = _draft_id(pk)
            if pid and not pk.get("draft_deleted") and not pk.get("draft_moved_to"):
                out.append({"run_id": r["id"], "channel_id": pk.get("channel_id"), "service": pk.get("service"),
                            "buffer_post_id": pid, "title": str(st.get("title") or "")[:120], "link": st.get("link") or ""})
    return out


def guard_duplicate(house, a: Actor, rid: int, channel_id: str, pk: dict, on_duplicate: str | None) -> dict | None:
    """None = no earlier draft of this story on this channel (or on_duplicate='new'): go on and save a new draft."""
    from .mcp_flow import StepError
    if on_duplicate == "new":
        return None
    row = house.store.q("SELECT id, input, result FROM agent_runs WHERE id=? AND user_id=?", (rid, a.uid))
    if not row:
        return None
    me = _story(dict(row[0]))
    dup = next((d for d in app_drafts(house.store, a, exclude_run=rid) if d["channel_id"] == channel_id and same_story(me, d)), None)
    if not dup:
        return None
    if on_duplicate != "update":
        raise StepError(f"Run {dup['run_id']} of this app already saved a Buffer draft of this story on this channel "
                        f"({dup['buffer_post_id']}). Nothing was created. Ask the person: update that draft with this post, or "
                        "make a second one?", 409, code="duplicate_draft", duplicate_of=dup, choices=["update", "new"],
                        next_steps=[f"schedule_package(run_id={rid}, channel_id='{channel_id}', mode='draft', on_duplicate='update') "
                                    "edits the earlier draft (editPost), never a second one",
                                    "on_duplicate='new' only if the person wants two drafts"])
    body = {"channel_id": channel_id, "text": pk["text"], "media_urls": pk.get("media_urls") or [], "options": pk.get("options") or {}}
    out = house.studio.replace_draft(a, dup["buffer_post_id"], body)  # drafts only: a scheduled / sent post is refused (409)
    house.mark_package(a, rid, channel_id, "sent_to_buffer", {"mode": "draft", "buffer_post_id": dup["buffer_post_id"],
                                                              "updated_from_run": dup["run_id"]})
    _mark_old(house.store, a, dup["run_id"], channel_id, {"draft_moved_to": rid})
    return {**out, "status": "updated", "buffer_post_id": dup["buffer_post_id"], "replaced_run_id": dup["run_id"],
            "note": f"Updated the earlier draft from run {dup['run_id']} (editPost); no second draft was made."}


def _mark_old(store, a: Actor, rid: int, channel_id: str, flag: dict) -> None:
    rows = store.q("SELECT result FROM agent_runs WHERE id=? AND user_id=?", (rid, a.uid))
    if not rows:
        return
    res = json.loads(rows[0]["result"] or "{}") or {}
    for p in res.get("packages") or []:
        if p.get("channel_id") == channel_id:
            p.update(flag)
    store.x("UPDATE agent_runs SET result=?, updated=? WHERE id=? AND user_id=?", (json.dumps(res, default=str), time.time(), rid, a.uid))


TOOLS: list[dict[str, Any]] = [
    {"name": "delete_drafts", "description": "Delete Buffer DRAFTS that THIS app created (from its own post runs), e.g. "
     "duplicates an earlier run left. Step 1 (default dry_run: true) lists them (run_id, channel, buffer_post_id, title) with a "
     "single-use confirm_token and changes nothing: show the list to the person. Step 2, only after their yes: call again with "
     "confirm_token (optionally buffer_post_ids to delete only some of the listed ones). Drafts the person or another app made "
     "are never listed, and a post that was scheduled or sent meanwhile is never touched (Buffer is re-checked per post).",
     "inputSchema": {"type": "object", "properties": {"dry_run": {"type": "boolean", "default": True}, "confirm_token": S,
                                                      "run_id": {"type": "integer", "description": "Only this run's drafts"},
                                                      "buffer_post_ids": {"type": "array", "items": S, "maxItems": 100}}}},
]


def delete_drafts(studio, a: Actor, args: dict) -> dict:
    from .store import sha
    store = studio.store
    if a.kind != "agent" or not a.agent:
        raise ActionError("delete_drafts is for AI apps; in HuhQueue delete drafts on the Planner.", 400)
    only = args.get("buffer_post_ids")
    if only is not None and (not isinstance(only, list) or len(only) > 100 or not all(isinstance(x, str) for x in only)):
        raise ActionError("buffer_post_ids is a list of up to 100 ids from the dry run.")
    rid = args.get("run_id")
    if rid is not None and (isinstance(rid, bool) or not isinstance(rid, int)):
        raise ActionError("run_id is the number start_post returned.")
    if args.get("confirm_token"):
        studio._guard_write(a, "delete_drafts")
        tok = str(args["confirm_token"])
        rows = store.q("SELECT * FROM confirms WHERE token_hash=? AND user_id=?", (sha(tok), a.uid))
        if not rows:
            raise ActionError("Unknown confirm_token. Run delete_drafts with dry_run first.", 404)
        row = rows[0]
        if row["used"] or row["expires"] < time.time():
            raise ActionError("This confirm_token is used or expired. Run the dry run again.", 410)
        if (row["agent_id"] or None) != a.agent["id"]:
            raise ActionError("This confirm_token belongs to a different caller.", 403)
        plan = json.loads(row["payload"] or "{}")
        if plan.get("kind") != "delete_drafts":
            raise ActionError("That confirm_token is for something else.", 409)
        # claim the token atomically: two calls racing past the check above can't both spend it
        if store.xn("UPDATE confirms SET used=1 WHERE token_hash=? AND user_id=? AND used=0",
                    (row["token_hash"], a.uid)) != 1:
            raise ActionError("This confirm_token is used or expired. Run the dry run again.", 410)
        listed = {d["buffer_post_id"]: d for d in plan.get("drafts") or []}
        mine_now = {d["buffer_post_id"]: d for d in app_drafts(store, a)}  # still this app's, right now
        want = [p for p in (only if only is not None else list(listed)) if p in listed and p in mine_now]
        from .buffer_api import BufferError
        deleted, refused, unknown = [], [], []
        for pid in want:
            try:
                studio.delete_draft(a, pid)  # re-reads the post in Buffer: only a DRAFT is deleted (409 for scheduled / sent)
            except ActionError as e:
                refused.append({"buffer_post_id": pid, "why": str(e)[:200], "status": e.status})
                continue
            except BufferError as e:
                # Buffer failed mid-call: the delete may or may not have landed, so say so and carry on with the rest
                unknown.append({"buffer_post_id": pid, "outcome": "unknown", "status": getattr(e, "status", 502),
                                "why": "Buffer did not answer cleanly; check this draft in Buffer before retrying."})
                continue
            d = mine_now[pid]
            _mark_old(store, a, d["run_id"], d["channel_id"], {"draft_deleted": True})
            deleted.append(pid)
        out = {"deleted": deleted, "refused": refused, "spent_usd": 0}
        if unknown:
            out["unknown"] = unknown
        return out
    drafts = [d for d in app_drafts(store, a) if rid is None or d["run_id"] == rid]
    out = {"dry_run": True, "drafts": drafts, "count": len(drafts), "spent_usd": 0}
    if drafts:
        token = "cf_" + secrets.token_urlsafe(24)
        store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires) VALUES(?,?,?,?,?)",
                (sha(token), a.uid, a.agent["id"], json.dumps({"kind": "delete_drafts", "drafts": drafts}), time.time() + CONFIRM_TTL))
        out.update(confirm_token=token, expires_in=CONFIRM_TTL,
                   next="Show this list to the person. Only if they say yes: delete_drafts(confirm_token=...).")
    else:
        out["next"] = "This app has no Buffer drafts to delete."
    return out


def call_tool(studio, actor, name: str, args: dict):
    if name != "delete_drafts":
        return NotImplemented
    return delete_drafts(studio, actor, args or {})
