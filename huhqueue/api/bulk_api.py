"""Bulk actions (Buffer parity and beyond) and the settings that go with them.

Every bulk request fans out to the ordinary one-item route for each id, in-process, with the caller's own
credentials (actions_api.dispatch). So a bulk call can never do more than the same calls one by one: the guard
middleware re-checks scopes, the token tool allow-list, rate limits, team roles and per-channel access, agent
tokens with `require_approval` still land in Approvals, and every inner call is audited. One bad id never aborts
the rest; the response lists each id's result.

  POST /api/bulk                    posts: delete, draft, queue, prioritize, reschedule, publish_now, tag, untag,
                                    set_tags, campaign, duplicate, move (to another channel), request_approval
  POST /api/bulk/approve            approve or reject many posts (a person in the app; never an agent token)
  GET  /api/posts/export.csv        CSV of the selected posts, or of any post filter
  POST /api/targets/{id}/move       move one unsent post to another channel (keeps its schedule)
  POST /api/channels/bulk-settings  apply timezone, daily limit, pause, posting goal, notifications, link shortening,
                                    UTM, days off or another channel's posting times to many channels
  POST /api/notifications/test      send a test notification through the configured push webhook
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import Response

from .. import actions as reg
from ..actions import B, I, O, S, SA, Action
from ..core import utcnow
from ..models import PostTarget
from ..services import posts as posts_svc
from ..services.events import NOTIFY_KINDS, webhook
from .actions_api import dispatch

MAX_IDS = 500
POST_ACTIONS = ("delete", "draft", "queue", "prioritize", "reschedule", "publish_now", "tag", "untag", "set_tags",
                "campaign", "duplicate", "move", "request_approval")
# what a contributor may do in bulk on their own posts (everything else goes through an editor)
CONTRIBUTOR_ACTIONS = {"delete", "draft", "tag", "untag", "set_tags", "campaign", "duplicate", "request_approval"}
CHANNEL_FIELDS = ("timezone", "daily_limit", "paused")
CHANNEL_SETTING_FIELDS = ("posting_goal", "notify_default", "link_shortening", "utm_enabled", "utm_source", "utm_medium",
                          "utm_campaign", "disabled_days")
CSV_COLUMNS = ("id", "post_id", "channel", "provider", "status", "schedule_mode", "scheduled_at", "published_at", "format",
               "caption", "tags", "campaign", "media_urls", "permalink", "views", "likes", "comments", "shares", "engagement_rate",
               "last_error", "note")


def _bad(msg: str, code: int = 400):
    raise HTTPException(code, msg)


def _ids(payload: dict) -> list[str]:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not ids:
        _bad("Send the post ids to act on as 'ids': [...]")
    ids = [str(x) for x in dict.fromkeys(ids)]
    if len(ids) > MAX_IDS:
        _bad(f"At most {MAX_IDS} posts per bulk action")
    return ids


def _shifted(core, tid: str, minutes: int) -> str | None:
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        if not t or not t.scheduled_at:
            return None
        return (t.scheduled_at + timedelta(minutes=minutes)).isoformat() + "Z"


def _current_tags(core, tid: str) -> list[str] | None:
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        return list(t.post.tags or []) if t else None


def _plan(core, action: str, tid: str, payload: dict) -> tuple[str, dict] | str:
    """(registry action id, input) for one item, or an error message."""
    if action == "delete":
        return "post.delete", {"id": tid}
    if action in ("draft", "queue", "prioritize"):
        return "post.update", {"id": tid, "when": action}
    if action == "reschedule":
        if payload.get("scheduled_at"):
            return "post.update", {"id": tid, "when": "custom", "scheduled_at": payload["scheduled_at"],
                                   **({"timezone": payload["timezone"]} if payload.get("timezone") else {})}
        at = _shifted(core, tid, int(payload.get("shift_minutes") or 0))
        if at is None:
            return "Not scheduled - give it a date first (scheduled_at)"
        return "post.update", {"id": tid, "when": "custom", "scheduled_at": at, "timezone": "UTC"}
    if action == "publish_now":
        return "post.publish_now", {"id": tid}
    if action in ("tag", "untag", "set_tags"):
        cur = _current_tags(core, tid)
        if cur is None:
            return "Post not found"
        want = [str(x).strip() for x in (payload.get("tags") or ([payload["tag"]] if payload.get("tag") else [])) if str(x).strip()]
        if not want and action != "set_tags":
            return "Say which tag ('tag' or 'tags')"
        tags = sorted(set(cur) | set(want)) if action == "tag" else [x for x in cur if x not in want] if action == "untag" else want
        return "post.update", {"id": tid, "tags": tags}
    if action == "campaign":
        return "post.update", {"id": tid, "campaign": str(payload.get("campaign") or "")}
    if action == "duplicate":
        return "post.duplicate", {"id": tid, **({"channel_ids": payload["channel_ids"]} if payload.get("channel_ids") else {})}
    if action == "move":
        if not payload.get("channel_id"):
            return "Say which channel to move to ('channel_id')"
        return "post.move", {"id": tid, "channel_id": payload["channel_id"]}
    if action == "request_approval":
        return "post.request_approval", {"id": tid, **({"note": payload["note"]} if payload.get("note") else {})}
    return f"Unknown action '{action}'"


def _err_text(data) -> str:
    if isinstance(data, dict):
        d = data.get("detail", data)
        if isinstance(d, dict):
            return str(d.get("message") or d)
        return str(d)
    return str(data)[:300]


async def fan_out(app, request: Request, items: list[tuple[str, str | tuple[str, dict]]]) -> dict:
    results = []
    for tid, plan in items:
        if isinstance(plan, str):
            results.append({"id": tid, "ok": False, "error": plan})
            continue
        aid, inp = plan
        st, data = await dispatch(app, request, reg.get(aid), inp)
        row = {"id": tid, "ok": 200 <= st < 300, "status": st}
        if row["ok"]:
            if isinstance(data, dict):
                if data.get("status") == "pending_approval":
                    row["pending_approval"] = True
                if aid in ("post.duplicate", "post.move") and data.get("id"):
                    row["new_id"] = data["id"]
                elif aid == "post.duplicate" and isinstance(data.get("targets"), list):
                    row["new_ids"] = [t.get("id") for t in data["targets"]]
            elif isinstance(data, list) and aid == "post.duplicate":
                row["new_ids"] = [t.get("id") for t in data if isinstance(t, dict)]
        else:
            row["error"] = _err_text(data)
        results.append(row)
    ok = sum(1 for r in results if r["ok"])
    return {"ok": ok == len(results), "count": ok, "failed": len(results) - ok, "results": results,
            "pending_approval": sum(1 for r in results if r.get("pending_approval"))}


def move_target(core, tid: str, channel_id: str, approval_by: str | None) -> dict:
    """Re-home an unsent post on another channel: same post, caption override, media and options; the schedule is
    re-applied there (queue slot / same time / draft), so the new channel's rules and approvals apply."""
    with core.db.session() as s:
        t = s.get(PostTarget, tid)
        if not t:
            raise posts_svc.PostError(404, "Post not found")
        if t.status in ("sent", "sending", "processing"):
            raise posts_svc.PostError(409, "Sent posts can't move - duplicate it instead")
        ch = posts_svc.resolve_channels(s, [channel_id])[0]
        if ch.id == t.channel_id:
            return posts_svc.serialize_target(core, t)
        if any(x.channel_id == ch.id for x in t.post.targets):
            raise posts_svc.PostError(409, f"This post is already on @{ch.username}")
        when = {"queue": "queue", "custom": "custom", "now": "custom"}.get(t.schedule_mode or "", "draft")
        if t.status == "pending_approval":
            when = (t.approval or {}).get("when") or "queue"
        at = t.scheduled_at.isoformat() + "Z" if when == "custom" and t.scheduled_at else None
        if when == "custom" and not at:
            when = "queue"
        nt = PostTarget(post=t.post, channel=ch, caption_override=t.caption_override,
                        media_override=list(t.media_override) if t.media_override is not None else None,
                        options=dict(t.options or {}), status="draft", schedule_mode="draft")
        s.add(nt)
        s.flush()
        new_id, old_ch = nt.id, t.channel
        was_queue = t.schedule_mode == "queue"
        s.delete(t)
        s.flush()
        if was_queue:
            from ..services import scheduling
            scheduling.reflow(s, old_ch, utcnow())
    if when == "draft":
        with core.db.session() as s:
            return posts_svc.serialize_target(core, s.get(PostTarget, new_id))
    try:
        return posts_svc.update_target(core, new_id, {"when": when, **({"scheduled_at": at, "timezone": "UTC"} if at else {})},
                                       approval_by=approval_by)
    except posts_svc.PostError as e:      # e.g. the new channel's rules: keep it as a draft there, say why
        with core.db.session() as s:
            out = posts_svc.serialize_target(core, s.get(PostTarget, new_id))
        out["notes"] = [f"Moved as a draft: {getattr(e, 'message', str(e))}"]
        return out


def add_bulk_routes(app: FastAPI, core, approval_actor, post_list, auth_ok_human) -> None:
    @app.post("/api/bulk", tags=["bulk"])
    async def bulk(request: Request, payload: dict = Body(...)):
        """Bulk actions on posts. Each id is run through its own route with your credentials."""
        action = payload.get("action")
        if action not in POST_ACTIONS:
            _bad(f"Unknown action '{action}'. Use one of: {', '.join(POST_ACTIONS)}")
        ids = _ids(payload)
        if action == "reschedule" and not payload.get("scheduled_at") and not payload.get("shift_minutes"):
            _bad("reschedule needs 'scheduled_at' (ISO, all posts at that time) or 'shift_minutes' (e.g. 1440 = one day later)")
        out = await fan_out(app, request, [(tid, _plan(core, action, tid, payload)) for tid in ids])
        out["action"] = action
        return out

    @app.post("/api/bulk/approve", tags=["bulk", "approvals"])
    async def bulk_approve(request: Request, payload: dict = Body(...)):
        """Approve or reject many posts waiting for approval (people only; a rejection needs a note for teammates)."""
        if not auth_ok_human(request):
            _bad("Approvals are made by a human in the app", 403)
        decision = payload.get("decision") or "approve"
        if decision not in ("approve", "reject"):
            _bad("decision must be approve or reject")
        ids = _ids(payload)
        note = str(payload.get("note") or "")[:1000]
        aid = "post.approve" if decision == "approve" else "post.reject"
        out = await fan_out(app, request, [(tid, (aid, {"id": tid, **({"note": note} if note else {})})) for tid in ids])
        out["decision"] = decision
        return out

    @app.post("/api/targets/{tid}/move", tags=["posts"])
    def target_move(tid: str, request: Request, payload: dict = Body(...)):
        if not payload.get("channel_id"):
            _bad("channel_id is required")
        return move_target(core, tid, str(payload["channel_id"]), approval_actor(request))

    @app.get("/api/posts/export.csv", tags=["bulk"])
    def posts_export(request: Request, ids: str = "", status: str | None = None, channel_id: str | None = None, provider: str | None = None,
                     tag: str | None = None, q: str | None = None, start: str | None = None, end: str | None = None,
                     post_type: str | None = None, campaign: str | None = None, limit: int = 2000):
        if ids:
            want = [x for x in ids.split(",") if x][:MAX_IDS * 4]
            with core.db.session() as s:
                rows = [posts_svc.serialize_target(core, t) for t in (s.get(PostTarget, x) for x in want) if t]
        else:
            rows = post_list(status=status, channel_id=channel_id, provider=provider, tag=tag, q=q, start=start, end=end,
                             post_type=post_type, campaign=campaign, limit=max(1, min(limit, 5000)))
        vis = getattr(request.state, "visible_channels", None)
        if vis is not None:                      # a teammate limited to some channels exports only those
            rows = [t for t in rows if t.get("channel_id") in vis]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(CSV_COLUMNS)
        for t in rows:
            ins = t.get("insights") or {}
            ch = t.get("channel") or {}
            w.writerow([t["id"], t["post_id"], "@" + (ch.get("username") or ""), ch.get("provider", ""), t["status"], t["schedule_mode"],
                        t.get("scheduled_at") or "", t.get("published_at") or "", t.get("format") or "", t.get("caption") or "",
                        "|".join(t.get("tags") or []), t.get("campaign") or "", "|".join(m.get("url", "") for m in t.get("media") or []),
                        t.get("permalink") or "", ins.get("views", ""), ins.get("likes", ""), ins.get("comments", ""),
                        ins.get("shares", ""), ins.get("engagement_rate", ""), t.get("last_error") or "", t.get("note") or ""])
        stamp = utcnow().strftime("%Y%m%d-%H%M")
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=huhqueue-posts-{stamp}.csv"})

    @app.post("/api/channels/bulk-settings", tags=["channels", "bulk"])
    async def channels_bulk_settings(request: Request, payload: dict = Body(...)):
        """Apply the same settings to many channels (each channel goes through its own settings routes)."""
        ids = payload.get("channel_ids")
        if not isinstance(ids, list) or not ids:
            _bad("Send the channels as 'channel_ids': [...] (or ['all'])")
        if "all" in ids:
            from ..models import Channel
            from sqlalchemy import select
            with core.db.session() as s:
                ids = [c for (c,) in s.execute(select(Channel.id))]
        ch_patch = {k: payload[k] for k in CHANNEL_FIELDS if k in payload}
        st_patch = {k: v for k, v in (payload.get("settings") or {}).items() if k in CHANNEL_SETTING_FIELDS}
        src = payload.get("copy_slots_from") or ""
        if not (ch_patch or st_patch or src):
            _bad("Nothing to change: give timezone, daily_limit, paused, settings{...} or copy_slots_from")
        results = []
        for cid in dict.fromkeys(str(x) for x in ids):
            row, errs = {"id": cid, "ok": True}, []
            steps = []
            if ch_patch:
                steps.append(("channel.update", {"id": cid, **ch_patch}))
            if st_patch:
                steps.append(("channel.settings_update", {"id": cid, **st_patch}))
            if src and src != cid:
                steps.append(("channel.slots_generate", {"id": cid, "mode": "copy", "source_channel_id": src}))
            for aid, inp in steps:
                st, data = await dispatch(app, request, reg.get(aid), inp)
                if not 200 <= st < 300:
                    errs.append(f"{aid.split('.')[-1]}: {_err_text(data)}")
            if errs:
                row.update(ok=False, error="; ".join(errs))
            results.append(row)
        ok = sum(1 for r in results if r["ok"])
        return {"ok": ok == len(results), "count": ok, "failed": len(results) - ok, "results": results}

    @app.post("/api/notifications/test", tags=["notifications"])
    def notifications_test():
        url = (core.prefs().get("notify_webhook_url") or "").strip()
        if not url:
            _bad("Add a webhook URL first (Settings › Notifications)")
        webhook(core, {"level": "info", "kind": "test", "message": "HuhQueue test notification - push alerts work."})
        return {"ok": True, "sent_to": url.split("?")[0][:80], "kinds": NOTIFY_KINDS}


# --------------------------------------------------------------------------------------------- registry entries
BULK_IDS = {**SA, "description": f"post (target) ids, at most {MAX_IDS}"}
for _a in (
    Action("post.move", "Move an unsent post to another channel (keeps its schedule)", "POST", "/api/targets/{id}/move",
           {"id": S, "channel_id": S}, ("id", "channel_id"), "write", "write", group="post", ui="PostCard › Move; Publish list › select › Move"),
    Action("post.bulk_approve", "Approve or reject many posts waiting for approval", "POST", "/api/bulk/approve",
           {"ids": BULK_IDS, "decision": {"type": "string", "enum": ["approve", "reject"]}, "note": S}, ("ids",), "admin", "human",
           group="post", ui="Publish › Approvals › select › Approve / Reject"),
    Action("post.export_csv", "Export posts as CSV (selected ids, or any post filter)", "GET", "/api/posts/export.csv",
           {"ids": {**S, "description": "comma-separated post ids; empty = use the filters"}, "status": S, "channel_id": S, "provider": S,
            "tag": S, "q": S, "start": S, "end": S, "post_type": S, "campaign": S, "limit": I},
           examples=("export my sent posts from October as CSV",), group="post", ui="Publish list › select › Export CSV"),
    Action("channel.bulk_settings", "Apply settings to many channels (timezone, daily limit, pause, posting goal, notifications, "
           "link shortening, UTM, days off, copy posting times)", "POST", "/api/channels/bulk-settings",
           {"channel_ids": {**SA, "description": "channel ids or ['all']"}, "timezone": S, "daily_limit": I, "paused": B,
            "settings": {**O, "description": "{posting_goal, notify_default, link_shortening, utm_enabled, utm_source, utm_medium, "
                         "utm_campaign, disabled_days[0-6]}"}, "copy_slots_from": {**S, "description": "channel id whose posting times to copy"}},
           ("channel_ids",), "admin", "write", examples=("turn on link shortening for all channels",), group="channel",
           ui="Settings › Channels › Bulk edit"),
    Action("notifications.test", "Send a test push notification", "POST", "/api/notifications/test", scope="admin", risk="write",
           group="notifications", ui="Settings › Notifications › Send test"),
):
    reg.register(_a)

_bulk = reg.BY_ID.get("post.bulk")
if _bulk:
    _bulk.title = "Bulk actions on posts: delete, drafts, queue, prioritize, reschedule, publish now, tags, campaign, duplicate, move, request approval"
    _bulk.params = {"action": {"type": "string", "enum": list(POST_ACTIONS)}, "ids": BULK_IDS,
                    "tag": S, "tags": SA, "campaign": {**S, "description": "'' removes the campaign"},
                    "scheduled_at": {**S, "description": "reschedule: ISO time for every post"}, "timezone": S,
                    "shift_minutes": {**I, "description": "reschedule: move each post by N minutes (negative = earlier)"},
                    "channel_id": {**S, "description": "move: the channel to move to"},
                    "channel_ids": {**SA, "description": "duplicate: copy to these channels instead of the same one"},
                    "note": {**S, "description": "request_approval: note for the approver"}}
    _bulk.examples = ("delete all failed posts", "move these 5 drafts to my linkedin channel", "push every post in the DevDay campaign back one day")
    _bulk.ui = "Publish list › select (bulk bar)"
