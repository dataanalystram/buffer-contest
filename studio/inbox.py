"""Inbox: everything that is waiting for a person, grouped by how soon it needs them.

One read (GET /api/inbox, MCP `get_inbox`) that the home screen, an agent and the tests share. Groups, most urgent first:

  now   approvals    agent posts held for a human (nothing reaches Buffer until approved)
  now   channels     channels disconnected in Buffer
  now   failed       Buffer posts in status `error`, with Buffer's own reason and a way to fix and reschedule them
  soon  preflight    scheduled posts in the next 7 days that preflight would block or warn about (no media download)
  soon  undated      drafts with no date, which never go out until someone picks a time
  fyi   open_slots   posting times in the next 7 days with nothing in them
  fyi   plan         the Buffer plan's meters at 80% or more, and locked channels

Each group: key, title, urgency, count, summary (one plain sentence), items, actions. `waiting` counts the items in the
`now` and `soon` groups: that is the number on the nav badge.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
HORIZON_DAYS = 7
URGENCY = ("now", "soon", "fyi")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _excerpt(text: str, n: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def open_slots(schedules: list[dict], scheduled: list[dict], now: datetime, days: int = HORIZON_DAYS) -> list[dict]:
    """Upcoming posting times per channel (Buffer posting schedule, in the channel's timezone) with no post within a
    minute of them, over the next `days` days. Sorted by time."""
    taken = {}
    for p in scheduled:
        t = _parse(p.get("dueAt"))
        if t:
            taken.setdefault(p.get("channelId"), []).append(t)
    out = []
    for c in schedules:
        try:
            tz = ZoneInfo(c.get("timezone") or "UTC")
        except Exception:
            tz = timezone.utc
        local_now = now.astimezone(tz)
        for d in range(days + 1):
            day = (local_now + timedelta(days=d)).date()
            sch = next((x for x in c.get("postingSchedule") or [] if x.get("day") == DAYS[day.weekday()]), None)
            if not sch or sch.get("paused"):
                continue
            for tm in sch.get("times") or []:
                try:
                    hh, mm = (int(x) for x in tm.split(":")[:2])
                except ValueError:
                    continue
                at = datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)
                if at <= now or at > now + timedelta(days=days):
                    continue
                if any(abs((at - t).total_seconds()) < 60 for t in taken.get(c["id"], [])):
                    continue
                out.append({"channel_id": c["id"], "when": _iso(at), "local": f"{tm} {c.get('timezone') or 'UTC'}"})
    return sorted(out, key=lambda s: s["when"])


def build(studio, a, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    u = studio.store.user(a.uid)
    org = u["org_id"]
    chans = {c["id"]: c for c in studio.channels(a)}
    name = lambda cid: (chans.get(cid) or {}).get("displayName") or (chans.get(cid) or {}).get("name") or "a channel"
    svc = lambda cid: (chans.get(cid) or {}).get("service", "")

    def posts(status: str, first: int = 60) -> list[dict]:
        try:
            return [p for p in studio.buffer.posts(a.uid, org, [status], first=first) if p.get("channelId") in chans]
        except Exception:
            return []

    try:  # the Inbox reads three statuses: one Buffer request, not three
        studio.buffer.prefetch_posts(a.uid, org, ("error", "scheduled", "draft"))
    except Exception:  # noqa: BLE001
        pass

    groups = []

    # now: approvals (humans only; an agent sees its own submissions through list_approvals)
    appr = studio.approvals(a, "pending") if a.kind == "human" else []
    groups.append({
        "key": "approvals", "urgency": "now", "title": "Approvals waiting", "count": len(appr),
        "summary": (f"{_plural(len(appr), 'post' if all(x.get('kind') != 'paid_api' for x in appr) else 'item')} from "
                    f"{' and '.join(sorted({x['agent_name'] for x in appr}))} waiting for you. Nothing reaches Buffer (and "
                    "nothing paid runs) until you approve it.") if appr else "No agent posts are waiting.",
        "items": [({"id": x["id"], "ref": f"approval:{x['id']}", "kind": "paid_api", "tool": x["payload"].get("tool", ""),
                    "text": _excerpt(f"Use {(x['payload'].get('estimate') or {}).get('provider') or 'a paid API'}: "
                                     f"{x['payload'].get('summary') or x['payload'].get('tool', '')}"),
                    "agent": x["agent_name"], "when": "",
                    "actions": [{"id": "open", "label": "Review", "go": "approvals"}]} if x.get("kind") == "paid_api" else
                   {"id": x["id"], "ref": f"approval:{x['id']}", "channel_id": x["payload"].get("channel_id"),
                    "channel": name(x["payload"].get("channel_id")), "service": svc(x["payload"].get("channel_id")),
                    "text": _excerpt(x["payload"].get("text", "")), "agent": x["agent_name"], "when": x["payload"].get("due_at") or "",
                    "actions": [{"id": "approve", "label": "Approve"}, {"id": "open", "label": "Review", "go": "approvals"}]})
                  for x in appr[:5]],
        "actions": [{"id": "open", "label": "Review all", "go": "approvals"}] if appr else []})

    # now: channels Buffer can't post to until they are reconnected
    off = [c for c in chans.values() if c.get("isDisconnected")]
    groups.append({
        "key": "channels", "urgency": "now", "title": "Disconnected channels", "count": len(off),
        "summary": (f"{_plural(len(off), 'channel')} disconnected in Buffer. Posts to {'it' if len(off) == 1 else 'them'} won't go "
                    "out until you reconnect in Buffer." if off else "Every channel is connected."),
        "items": [{"channel_id": c["id"], "channel": c.get("displayName") or c.get("name"), "service": c.get("service", ""),
                   "actions": [{"id": "reconnect", "label": "Reconnect in Buffer", "href": "https://publish.buffer.com/channels"}]}
                  for c in off[:6]],
        "actions": [{"id": "refresh", "label": "Refresh channels"}] if off else []})

    # now: posts Buffer failed to publish
    failed = posts("error")
    groups.append({
        "key": "failed", "urgency": "now", "title": "Failed or bounced", "count": len(failed),
        "summary": (f"Buffer couldn't publish {_plural(len(failed), 'post')}. Fix it and pick a new time."
                    if failed else "Nothing has bounced."),
        "items": [{"id": p["id"], "ref": f"buffer:{p['id']}", "channel_id": p.get("channelId"), "channel": name(p.get("channelId")),
                   "service": svc(p.get("channelId")), "text": _excerpt(p.get("text", "")), "when": p.get("dueAt") or "",
                   "reason": ((p.get("error") or {}).get("message") or "Buffer didn't say why."),
                   "actions": [{"id": "fix", "label": "Fix and reschedule", "go": f"planner:buffer:{p['id']}"}]}
                  for p in failed[:5]],
        "actions": []})

    # soon: preflight on what is about to go out (text rules only: the web dyno doesn't download media for a read)
    scheduled = posts("scheduled", 100)
    soon_posts = [p for p in scheduled if (t := _parse(p.get("dueAt"))) and t <= now + timedelta(days=HORIZON_DAYS)]
    pf_items = []
    for p in soon_posts[:20]:
        ch = chans.get(p.get("channelId"))
        media = [x.get("source") for x in p.get("assets") or [] if x.get("source")]
        try:
            r = studio._preflight(ch, p.get("text") or "", media, "queue", probe=False, creating=False)
        except Exception:
            continue
        if r["status"] == "ok":
            continue
        worst = next((f for f in r["findings"] if f["level"] == r["status"]), r["findings"][0] if r["findings"] else {})
        pf_items.append({"id": p["id"], "ref": f"buffer:{p['id']}", "channel_id": p.get("channelId"), "channel": name(p.get("channelId")),
                         "service": svc(p.get("channelId")), "text": _excerpt(p.get("text", "")), "when": p.get("dueAt") or "",
                         "status": r["status"], "reason": worst.get("message", ""),
                         "actions": [{"id": "fix", "label": "Fix it", "go": f"planner:buffer:{p['id']}"}]})
    pf_items.sort(key=lambda x: (x["status"] != "error", x["when"]))
    groups.append({
        "key": "preflight", "urgency": "soon", "title": "Preflight warnings", "count": len(pf_items),
        "summary": (f"{_plural(len(pf_items), 'scheduled post')} in the next {HORIZON_DAYS} days "
                    f"{'has' if len(pf_items) == 1 else 'have'} something Buffer or the network may refuse." if pf_items
                    else f"Everything scheduled for the next {HORIZON_DAYS} days passes preflight."),
        "items": pf_items[:5], "actions": []})

    # soon: drafts with no date never go out
    undated = [p for p in posts("draft") if not p.get("dueAt")]
    groups.append({
        "key": "undated", "urgency": "soon", "title": "Drafts with no date", "count": len(undated),
        "summary": (f"{_plural(len(undated), 'draft')} won't go out until you give {'it' if len(undated) == 1 else 'them'} a time."
                    if undated else "Every draft has a time."),
        "items": [{"id": p["id"], "ref": f"buffer:{p['id']}", "channel_id": p.get("channelId"), "channel": name(p.get("channelId")),
                   "service": svc(p.get("channelId")), "text": _excerpt(p.get("text", "")), "when": "",
                   "actions": [{"id": "schedule", "label": "Pick a time", "go": f"planner:buffer:{p['id']}"}]}
                  for p in undated[:5]],
        "actions": [{"id": "open", "label": "Open in Planner", "go": "planner:nodate"}] if undated else []})

    # fyi: open posting times this week
    try:
        schedules = [c for c in studio.buffer.posting_schedules(a.uid, org) if c.get("id") in chans]
    except Exception:
        schedules = []
    slots = open_slots(schedules, scheduled, now)
    per = {}
    for s in slots:
        per[s["channel_id"]] = per.get(s["channel_id"], 0) + 1
    groups.append({
        "key": "open_slots", "urgency": "fyi", "title": "Empty slots this week", "count": len(slots),
        "summary": (f"{_plural(len(slots), 'posting time')} in the next {HORIZON_DAYS} days with nothing in "
                    f"{'it' if len(slots) == 1 else 'them'}." if slots else "Every posting time this week is filled."),
        "items": [{"channel_id": cid, "channel": name(cid), "service": svc(cid), "count": n,
                   "next": next(s["when"] for s in slots if s["channel_id"] == cid)} for cid, n in sorted(per.items(), key=lambda kv: -kv[1])][:6],
        "actions": ([{"id": "fill", "label": "Fill with One take", "go": "take"}, {"id": "open", "label": "Open Planner", "go": "planner:week"}]
                    if slots else [])})

    # fyi (or now when something is locked or full): the Buffer plan
    try:
        plan = studio.plan(a)
    except Exception:
        plan = {"meters": [], "near_limit": [], "locked_channels": [], "upgrade_url": "https://buffer.com/pricing"}
    near, locked = plan.get("near_limit") or [], plan.get("locked_channels") or []
    full = [m for m in near if m["pct"] >= 100]
    groups.append({
        "key": "plan", "urgency": "now" if (full or locked) else "fyi", "title": "Buffer plan", "count": len(near) + len(locked),
        "summary": ("; ".join([f"{m['label']}: {m['used']} of {m['limit']}" for m in near] + [f"{c} is locked on your plan" for c in locked])
                    if near or locked else "Your Buffer plan has room."),
        "meters": plan.get("meters") or [],
        "items": [{"label": m["label"], "used": m["used"], "limit": m["limit"], "pct": m["pct"]} for m in near],
        "actions": [{"id": "upgrade", "label": "See Buffer plans", "href": plan.get("upgrade_url", "https://buffer.com/pricing")}] if near or locked else []})

    groups.sort(key=lambda g: URGENCY.index(g["urgency"]))
    needs = sum(g["count"] for g in groups if g["urgency"] in ("now", "soon") and g["key"] != "plan") + \
        sum(1 for g in groups if g["key"] == "plan" and g["urgency"] == "now")
    return {"waiting": needs, "groups": groups, "horizon_days": HORIZON_DAYS, "as_of": _iso(now),
            "kill_switch": bool(studio.me(a).get("kill_switch"))}
