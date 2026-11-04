"""Channel pages (Buffer parity and beyond, all free): the per-channel Publish header (weekly goal pill, timezone,
empty-slot chips), channel settings (posting goal, generate slots, per-day on/off, notification default, link
shortening and UTM tagging, queue actions), Community grouped by post with bulk resolve and a Mentions support
notice, and Insights with any date range (7d / 30d / month to date / custom), previous-period comparison,
metric categories, a daily comparison series, weekly takeaways, CSV export and "Share as post".

Every route here has a registry action (the "channel pages" block in actions.py), so it also works from
REST, MCP, the CLI and the ⌘K bar."""
from __future__ import annotations

import csv
import io
import random
import re
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import func, select

from ..core import utcnow
from ..models import Channel, Comment, PostTarget
from ..services import posts as posts_svc
from ..services import scheduling

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DEFAULTS = {"posting_goal": None, "notify_default": False, "link_shortening": False, "utm_enabled": False,
            "utm_source": "", "utm_medium": "social", "utm_campaign": "huhqueue"}
QUEUE_ACTIONS = ("shuffle", "clean-failed", "delete-drafts", "empty")
URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


# ------------------------------------------------------------------------------------------------ settings
def channel_settings(ch: Channel) -> dict:
    meta = ch.meta or {}
    st = {**DEFAULTS, **(meta.get("settings") or {})}
    if not st.get("posting_goal"):
        st["posting_goal"] = int((meta.get("onboarding") or {}).get("goal") or 0) or len(ch.slots) or 3
    if not st.get("utm_source"):
        st["utm_source"] = ch.provider
    st["disabled_days"] = sorted({int(d) for d in meta.get("disabled_days") or []})
    return st


def save_settings(ch: Channel, patch: dict) -> dict:
    meta = dict(ch.meta or {})
    cur = dict(meta.get("settings") or {})
    if "posting_goal" in patch:
        try:
            g = int(patch["posting_goal"])
        except (TypeError, ValueError) as e:
            raise ValueError("posting_goal must be a whole number") from e
        if not 1 <= g <= 70:
            raise ValueError("posting_goal must be between 1 and 70 posts a week")
        cur["posting_goal"] = g
    for k in ("notify_default", "link_shortening", "utm_enabled"):
        if k in patch:
            cur[k] = bool(patch[k])
    for k in ("utm_source", "utm_medium", "utm_campaign"):
        if k in patch:
            v = str(patch[k] or "").strip()[:60]
            if v and not re.fullmatch(r"[\w.\-+ ]+", v):
                raise ValueError(f"{k} may only use letters, numbers, spaces and . - _ +")
            cur[k] = v
    if "disabled_days" in patch:
        try:
            days = {int(d) for d in patch["disabled_days"] or []}
        except (TypeError, ValueError) as e:
            raise ValueError("disabled_days are weekdays 0 (Mon) to 6 (Sun)") from e
        if not days <= set(range(7)):
            raise ValueError("disabled_days are weekdays 0 (Mon) to 6 (Sun)")
        meta["disabled_days"] = sorted(days)
    meta["settings"] = cur
    ch.meta = meta
    return channel_settings(ch)


def add_utm(url: str, st: dict) -> str:
    parts = urlsplit(url)
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    if "utm_source" in q:          # never overwrite tags the author set by hand
        return url
    for k in ("utm_source", "utm_medium", "utm_campaign"):
        if st.get(k):
            q[k] = st[k]
    return urlunsplit(parts._replace(query=urlencode(q)))


def rewrite_links(core, s, ch: Channel, caption: str) -> str:
    """Applied by the worker right before publishing: UTM tags (if on) and short links (if on and a links service
    with `shorten()` is installed). Text without links is returned untouched."""
    st = channel_settings(ch)
    if not (st["utm_enabled"] or st["link_shortening"]) or "http" not in (caption or ""):
        return caption

    def one(m):
        url = m.group(0).rstrip(".,!?;:")
        tail = m.group(0)[len(url):]
        if st["utm_enabled"]:
            url = add_utm(url, st)
        if st["link_shortening"]:
            try:
                from ..services import links as links_svc   # provided by the campaigns / links feature
                url = links_svc.shorten(core, s, url, channel_id=ch.id)
            except Exception:  # noqa: BLE001 - no shortener installed: keep the full link
                pass
        return url + tail
    return URL_RE.sub(one, caption)


# ------------------------------------------------------------------------------------------------ publish header
def week_bounds(ch: Channel, now: datetime) -> tuple[date, datetime, datetime]:
    zone = scheduling.tz(ch)
    today = scheduling.local_day(now, zone)
    monday = today - timedelta(days=today.weekday())
    lo = scheduling.to_utc_naive(datetime.combine(monday, time(0, 0)), zone)
    hi = scheduling.to_utc_naive(datetime.combine(monday + timedelta(days=7), time(0, 0)), zone)
    return monday, lo, hi


def open_slots(s, ch: Channel, now: datetime, days: int = 7, limit: int = 12) -> list[dict]:
    zone = scheduling.tz(ch)
    taken = {t.scheduled_at.replace(second=0, microsecond=0) for t in s.scalars(select(PostTarget).where(
        PostTarget.channel_id == ch.id, PostTarget.status.in_(("scheduled", "processing", "sending", "pending_approval")),
        PostTarget.scheduled_at.is_not(None)))}
    out = []
    for utc in scheduling.slot_times(ch, now, days=days):
        if utc.replace(second=0, microsecond=0) in taken:
            continue
        local = scheduling.to_local(utc, zone)
        out.append({"at": posts_svc.iso(utc), "local": local.strftime("%Y-%m-%dT%H:%M"),
                    "day": local.strftime("%a %d %b"), "time": local.strftime("%H:%M")})
        if len(out) >= limit:
            break
    return out


def tz_label(name: str) -> str:
    return name.split("/")[-1].replace("_", " ")


# ------------------------------------------------------------------------------------------------ insights
METRICS = [("posts", "Posts"), ("views", "Views"), ("reactions", "Reactions"), ("comments", "Comments"), ("shares", "Shares"),
           ("saves", "Saves"), ("engagements", "Engagements"), ("engagement_rate", "Eng. rate")]
CATEGORIES = [
    {"id": "content_impact", "label": "Content impact", "metrics": ["posts", "views"],
     "description": "Posts and views. Shows whether posting more is getting you seen more."},
    {"id": "engagement", "label": "Engagement", "metrics": ["reactions", "comments", "shares"],
     "description": "Reactions, comments and shares per day. Shows how people respond."},
    {"id": "visibility", "label": "Visibility", "metrics": ["views"],
     "description": "Views per day. Shows how far your posts travel."},
    {"id": "engagement_rate", "label": "Engagement rate", "metrics": ["engagement_rate"],
     "description": "Engagements divided by views. Shows how well your content lands, independent of reach."},
]
RANGES = ("7d", "30d", "mtd", "custom")


def ins_range(ch: Channel, rng: str, start: str | None, end: str | None, now: datetime):
    """(lo, hi, prev_lo, prev_hi) as naive UTC. Custom ranges are free and read in the channel's timezone."""
    rng = (rng or "30d").lower()
    zone = scheduling.tz(ch)
    if rng == "custom" or start:
        if not start:
            raise ValueError("A custom range needs a start date")
        lo = posts_svc.parse_when(start if "T" in start else start + "T00:00", ch.timezone)
        hi = posts_svc.parse_when(end if "T" in end else end + "T23:59", ch.timezone) if end else now
        if hi <= lo:
            raise ValueError("The end date must be after the start date")
        if hi - lo > timedelta(days=731):
            raise ValueError("Custom ranges can be up to two years long")
        return lo, hi, lo - (hi - lo), lo
    if rng == "mtd":
        today = scheduling.local_day(now, zone)
        first = today.replace(day=1)
        lo = scheduling.to_utc_naive(datetime.combine(first, time(0, 0)), zone)
        prev_first = (first - timedelta(days=1)).replace(day=1)
        prev_lo = scheduling.to_utc_naive(datetime.combine(prev_first, time(0, 0)), zone)
        return lo, now, prev_lo, prev_lo + (now - lo)
    m = re.fullmatch(r"(\d{1,3})d?", rng)
    if not m:
        raise ValueError(f"Unknown range {rng}: use 7d, 30d, mtd or custom")
    span = timedelta(days=int(m.group(1)))
    return now - span, now, now - 2 * span, now - span


def post_metrics(ins: dict) -> dict:
    views = int(ins.get("views") or ins.get("impressions") or ins.get("reach") or 0)
    reactions = int(ins.get("likes") or 0)
    comments = int(ins.get("comments") or 0)
    shares = int(ins.get("shares") or 0)
    saves = int(ins.get("saved") or ins.get("saves") or 0)
    eng = reactions + comments + shares + saves
    rate = ins.get("engagement_rate")
    if rate is None and views:
        rate = round(100.0 * eng / views, 2)
    return {"views": views, "reactions": reactions, "comments": comments, "shares": shares, "saves": saves,
            "engagements": eng, "engagement_rate": rate}


def summarize(rows: list[dict]) -> dict:
    out = {"posts": len(rows)}
    for k in ("views", "reactions", "comments", "shares", "saves", "engagements"):
        out[k] = sum(r["metrics"][k] for r in rows)
    out["engagement_rate"] = round(100.0 * out["engagements"] / out["views"], 2) if out["views"] else (
        round(sum(r["metrics"]["engagement_rate"] or 0 for r in rows) / len(rows), 2) if rows else 0)
    return out


def delta(cur, prev):
    if cur is None or prev is None:
        return None
    if not prev:
        return None if not cur else 100.0
    return round(100.0 * (cur - prev) / prev, 1)


def series(rows: list[dict], lo: datetime, hi: datetime, zone) -> list[dict]:
    first, last = scheduling.local_day(lo, zone), scheduling.local_day(hi, zone)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)][:732]
    by: dict[date, list] = {d: [] for d in days}
    for r in rows:
        d = scheduling.local_day(r["_published"], zone)
        if d in by:
            by[d].append(r)
    return [{"day": d.isoformat(), "index": i, **summarize(by[d])} for i, d in enumerate(days)]


def takeaways(cur: dict, prev: dict, rows: list[dict], goal: int, weeks: float, zone) -> list[dict]:
    """Plain-language weekly takeaways from the channel's own numbers (no LLM needed, nothing invented)."""
    out: list[dict] = []
    if not rows:
        return out
    per_week = cur["posts"] / max(weeks, 1.0)
    if per_week >= goal:
        out.append({"kind": "goal", "tone": "good", "text": f"You posted about {per_week:.1f} times a week - at or above your goal of {goal}. Keep the rhythm."})
    else:
        out.append({"kind": "goal", "tone": "warn", "text": f"You posted about {per_week:.1f} times a week, under your goal of {goal}. Fill the empty slots in your queue to catch up."})
    for key in ("views", "engagements"):
        d = delta(cur[key], prev[key])
        if d is not None and prev[key]:
            out.append({"kind": key, "tone": "good" if d >= 0 else "warn",
                        "text": f"{key.capitalize()} are {'up' if d >= 0 else 'down'} {abs(d):.0f}% on the previous period ({prev[key]:,} → {cur[key]:,})."})
    best = max(rows, key=lambda r: (r["metrics"]["engagements"], r["metrics"]["views"]))
    if best["metrics"]["engagements"] or best["metrics"]["views"]:
        cap = ((best["caption"] or "your post").strip().splitlines() or ["your post"])[0][:70]
        out.append({"kind": "top_post", "tone": "good", "post_id": best["id"], "permalink": best["permalink"],
                    "text": f"Your best post was “{cap}” with {best['metrics']['engagements']:,} engagements and {best['metrics']['views']:,} views."})
    by_day: dict[int, list[int]] = {}
    for r in rows:
        by_day.setdefault(scheduling.to_local(r["_published"], zone).weekday(), []).append(r["metrics"]["engagements"])
    if len(by_day) >= 2:
        d, v = max(by_day.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        out.append({"kind": "best_day", "tone": "info", "weekday": d,
                    "text": f"{DAYS[d]} posts earned the most engagement on average ({sum(v) / len(v):.1f} per post). Consider a slot on {DAYS[d]}."})
    by_fmt: dict[str, list[int]] = {}
    for r in rows:
        by_fmt.setdefault(r["format"] or "post", []).append(r["metrics"]["engagements"])
    if len(by_fmt) >= 2:
        f, v = max(by_fmt.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        out.append({"kind": "best_format", "tone": "info", "text": f"{f.capitalize()}s did best ({sum(v) / len(v):.1f} engagements per post)."})
    return out[:5]


def build_insights(core, s, ch: Channel, rng: str, start: str | None, end: str | None) -> dict:
    now = utcnow()
    lo, hi, plo, phi = ins_range(ch, rng, start, end, now)
    zone = scheduling.tz(ch)

    def load(a, b):
        out = []
        for t in s.scalars(select(PostTarget).where(PostTarget.channel_id == ch.id, PostTarget.status == "sent",
                                                    PostTarget.published_at >= a, PostTarget.published_at <= b)
                           .order_by(PostTarget.published_at.desc())):
            d = posts_svc.serialize_target(core, t)
            m0 = d["media"][0] if d["media"] else {}
            out.append({"id": t.id, "caption": d["caption"], "permalink": d["permalink"], "format": d["format"],
                        "published_at": d["published_at"], "tags": d["tags"], "thumbnail": m0.get("thumb_url") or m0.get("url"),
                        "metrics": post_metrics(t.insights or {}), "_published": t.published_at})
        return out
    cur_rows, prev_rows = load(lo, hi), load(plo, phi)
    cur, prev = summarize(cur_rows), summarize(prev_rows)
    st = channel_settings(ch)
    followers = (ch.meta or {}).get("followers_count")
    summary = [{"key": "followers", "label": "Followers", "value": followers, "previous": None, "delta": None}]
    summary += [{"key": k, "label": lbl, "value": cur[k], "previous": prev[k], "delta": delta(cur[k], prev[k])} for k, lbl in METRICS]
    s_cur, s_prev = series(cur_rows, lo, hi, zone), series(prev_rows, plo, phi, zone)
    comparison = [{"index": i, "day": c["day"], "previous_day": s_prev[i]["day"] if i < len(s_prev) else None,
                   **{k: c[k] for k, _ in METRICS},
                   **{f"previous_{k}": (s_prev[i][k] if i < len(s_prev) else None) for k, _ in METRICS}} for i, c in enumerate(s_cur)]
    weeks = (hi - lo).total_seconds() / (7 * 86400)

    def strip(rows):
        return [{k: v for k, v in r.items() if k != "_published"} for r in rows]
    fmt = lambda a, b: f"{scheduling.to_local(a, zone):%d %b} – {scheduling.to_local(b, zone):%d %b %Y}"  # noqa: E731
    return {"channel_id": ch.id, "provider": ch.provider, "timezone": ch.timezone, "range": "custom" if start else (rng or "30d"),
            "period": {"start": posts_svc.iso(lo), "end": posts_svc.iso(hi), "label": fmt(lo, hi)},
            "previous_period": {"start": posts_svc.iso(plo), "end": posts_svc.iso(phi), "label": fmt(plo, phi)},
            "summary": summary, "totals": cur, "previous_totals": prev, "categories": CATEGORIES,
            "metrics": [{"key": k, "label": lbl} for k, lbl in METRICS], "comparison": comparison,
            "posts": {"this": strip(cur_rows), "previous": strip(prev_rows)},
            "takeaways": takeaways(cur, prev, cur_rows, st["posting_goal"], weeks, zone), "goal": st["posting_goal"]}


def share_caption(ch: Channel, d: dict) -> str:
    t = d["totals"]
    lines = [f"📊 @{ch.username} · {d['period']['label']}", ""]
    for k, lbl in (("posts", "Posts"), ("views", "Views"), ("reactions", "Reactions"), ("comments", "Comments"), ("engagement_rate", "Engagement rate")):
        v = t[k]
        dl = next((x["delta"] for x in d["summary"] if x["key"] == k), None)
        val = f"{v}%" if k == "engagement_rate" else f"{v:,}"
        lines.append(f"• {lbl}: {val}" + (f" ({'+' if dl >= 0 else ''}{dl:.0f}% vs previous)" if dl is not None else ""))
    if d["takeaways"]:
        lines += ["", d["takeaways"][0]["text"]]
    lines += ["", "Thanks for watching, reading and replying 💜"]
    return "\n".join(lines)


def id_list(payload: dict, key: str = "ids", what: str = "comment") -> list[str]:
    """The ids a bulk route acts on, checked before use: a list of non-empty strings (or integers). Anything else, such as
    {"ids": 5} or {"ids": "abc"}, is a 400 with a clear message instead of a TypeError (a 500) or a string read as a list
    of characters (4 Nov 2026)."""
    raw = payload.get(key) if isinstance(payload, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, f"'{key}' must be a list of {what} ids, like [\"id1\", \"id2\"]")
    out = []
    for x in raw:
        if isinstance(x, bool) or not isinstance(x, (str, int)) or not str(x).strip():
            raise HTTPException(400, f"Every entry in '{key}' must be a {what} id (a non-empty string)")
        out.append(str(x).strip())
    return out


# ------------------------------------------------------------------------------------------------ routes
def add_channel_page_routes(app: FastAPI, core) -> None:
    def channel(s, cid) -> Channel:
        ch = s.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return ch

    def bad(e: Exception):
        raise HTTPException(400, str(e)) from e

    # ---- settings
    @app.get("/api/channels/{cid}/settings", tags=["channels"])
    def settings_get(cid: str):
        with core.db.session() as s:
            ch = channel(s, cid)
            return {**channel_settings(ch), "timezone": ch.timezone, "paused": ch.paused}

    @app.patch("/api/channels/{cid}/settings", tags=["channels"])
    def settings_patch(cid: str, payload: dict = Body(...)):
        with core.db.session() as s:
            ch = channel(s, cid)
            try:
                out = save_settings(ch, payload)
            except ValueError as e:
                bad(e)
            s.flush()
            if "disabled_days" in payload:
                scheduling.reflow(s, ch, utcnow())
            return {**out, "timezone": ch.timezone, "paused": ch.paused}

    # ---- publish header: goal pill, timezone pill, counts, empty-slot chips, tags
    @app.get("/api/channels/{cid}/publish-summary", tags=["channels"])
    def publish_summary(cid: str, days: int = 7):
        now = utcnow()
        with core.db.session() as s:
            ch = channel(s, cid)
            st = channel_settings(ch)
            monday, lo, hi = week_bounds(ch, now)
            rows = list(s.scalars(select(PostTarget).where(PostTarget.channel_id == cid)))
            sent_week = sum(1 for t in rows if t.status == "sent" and t.published_at and lo <= t.published_at < hi)
            counts = {"queue": sum(t.status in ("scheduled", "sending", "processing", "needs_action") for t in rows),
                      "drafts": sum(t.status == "draft" for t in rows),
                      "approvals": sum(t.status == "pending_approval" for t in rows),
                      "sent": sum(t.status == "sent" for t in rows), "failed": sum(t.status == "failed" for t in rows)}
            tags = sorted({tg for t in rows for tg in (t.post.tags or [])})
            return {"goal": st["posting_goal"], "sent_this_week": sent_week, "week_start": monday.isoformat(),
                    "timezone": ch.timezone, "timezone_label": tz_label(ch.timezone), "paused": ch.paused,
                    "counts": counts, "open_slots": open_slots(s, ch, now, days=max(1, min(days, 31))), "tags": tags,
                    "slots": len(ch.slots), "disabled_days": st["disabled_days"]}

    # ---- generate posting slots
    @app.post("/api/channels/{cid}/slots/generate", tags=["channels"])
    def slots_generate(cid: str, payload: dict = Body(default={})):
        from .pro_content import recommend
        mode = payload.get("mode") or "goal"
        with core.db.session() as s:
            ch = channel(s, cid)
            st = channel_settings(ch)
            goal = int(payload.get("goal") or st["posting_goal"])
            have = [{"weekday": x.weekday, "time": x.time} for x in ch.slots]
            if mode == "goal":                    # keep existing times, add recommended ones until the goal is met
                need = goal - len(have)
                if need <= 0:
                    return {"added": 0, "mode": mode, "slots": sorted(have, key=lambda z: (z["weekday"], z["time"])),
                            "message": f"You already have {len(have)} posting times for a goal of {goal}."}
                rec = recommend(s, ch, min(21, goal + len(have)))["times"]
                busy = {(x["weekday"], x["time"][:2]) for x in have}
                new = [{"weekday": r["weekday"], "time": r["time"]} for r in rec if (r["weekday"], r["time"][:2]) not in busy][:need]
                slots = have + new
            elif mode == "recommended":           # replace with the recommended times for the goal
                slots = [{"weekday": r["weekday"], "time": r["time"]} for r in recommend(s, ch, goal)["times"]]
                new = slots
            elif mode == "copy":
                src = channel(s, payload.get("source_channel_id") or "")
                if src.id == ch.id:
                    bad(ValueError("Pick a different channel to copy from"))
                slots = [{"weekday": x.weekday, "time": x.time} for x in src.slots]
                if not slots:
                    bad(ValueError(f"@{src.username} has no posting times to copy"))
                new = slots
            else:
                bad(ValueError("mode must be goal, recommended or copy"))
            scheduling.set_slots(s, ch, slots)
            s.flush()
            scheduling.reflow(s, ch, utcnow())
            return {"added": len(new), "mode": mode,
                    "slots": [{"weekday": x.weekday, "time": x.time} for x in sorted(ch.slots, key=lambda z: (z.weekday, z.time))]}

    # ---- queue actions
    for _act in QUEUE_ACTIONS:              # one literal route per action, so each maps to its own registry action
        app.add_api_route(f"/api/channels/{{cid}}/queue/{_act}", (lambda a: lambda cid: queue_action(cid, a))(_act),
                          methods=["POST"], tags=["channels"], name=f"queue_{_act.replace('-', '_')}")

    def queue_action(cid: str, action: str):
        with core.db.session() as s:
            ch = channel(s, cid)
            if action == "shuffle":
                rows = list(s.scalars(select(PostTarget).where(PostTarget.channel_id == cid, PostTarget.status == "scheduled",
                                                               PostTarget.schedule_mode == "queue")
                                      .order_by(PostTarget.scheduled_at)))[:200]
                times = sorted(t.scheduled_at for t in rows if t.scheduled_at)
                random.shuffle(rows)
                for i, t in enumerate(rows):
                    t.prioritized = False
                    t.scheduled_at = times[i] if i < len(times) else None
                s.flush()
                scheduling.reflow(s, ch, utcnow())
                return {"ok": True, "action": action, "count": len(rows)}
            status = {"clean-failed": ("failed",), "delete-drafts": ("draft",), "empty": ("scheduled", "needs_action")}[action]
            ids = [t.id for t in s.scalars(select(PostTarget).where(PostTarget.channel_id == cid, PostTarget.status.in_(status)))]
        for tid in ids:
            posts_svc.delete_target(core, tid)
        return {"ok": True, "action": action, "count": len(ids)}

    # ---- community, by post or as a list
    @app.get("/api/channels/{cid}/community", tags=["community"])
    def community_view(cid: str, view: str = "posts", sort: str = "newest", status: str = "all", show_all: bool = False, q: str = "",
                       kind: str = "comment"):
        from ..services import community as community_svc
        with core.db.session() as s:
            ch = channel(s, cid)
            caps = core.connectors[ch.provider].capabilities if ch.provider in core.connectors else {}
            qry = select(Comment).where(Comment.channel_id == cid, Comment.is_own.is_(False))
            if kind != "all":
                qry = qry.where(func.coalesce(Comment.kind, "comment") == kind)
            if status and status != "all":
                sts = set(status.split(","))
                if "answered" in sts:
                    sts |= {"replied", "resolved"}
                qry = qry.where(Comment.status.in_(sts))
            if q:
                qry = qry.where(Comment.text.ilike(f"%{q}%") | Comment.author.ilike(f"%{q}%"))
            rows = list(s.scalars(qry.order_by(Comment.at.desc()).limit(1000)))
            replies: dict[str, list] = {}
            for c in s.scalars(select(Comment).where(Comment.channel_id == cid, Comment.is_own.is_(True))):
                replies.setdefault(c.parent_external_id, []).append({"text": c.text, "at": posts_svc.iso(c.at)})
            comments = [{**community_svc.serialize(c, ch), "my_replies": replies.get(c.external_id, [])} for c in rows]

            def unanswered(c):
                return c["status"] == "open"
            if sort == "unanswered":          # unanswered first; rows are already newest first inside each group
                comments.sort(key=lambda c: not unanswered(c))
            totals = {"all": len(comments), "open": sum(map(unanswered, comments))}
            base = {"channel_id": cid, "provider": ch.provider, "comments_supported": bool(caps.get("comments")),
                    "comments_note": caps.get("comments_note", ""), "view": view, "sort": sort, "status": status, "totals": totals}
            if view == "list":
                return {**base, "comments": comments}
            posts: dict[str, dict] = {}
            for c in comments:
                p = posts.setdefault(c["media_external_id"] or "-", {"media_external_id": c["media_external_id"], "caption": c["media_caption"],
                                                                     "permalink": c["media_permalink"], "comments": [], "unanswered": 0,
                                                                     "last_at": None, "last_unanswered_at": None})
                p["comments"].append(c)
                p["last_at"] = max(p["last_at"] or "", c["at"] or "") or None
                if unanswered(c):
                    p["unanswered"] += 1
                    p["last_unanswered_at"] = max(p["last_unanswered_at"] or "", c["at"] or "") or None
            if show_all:
                for t in s.scalars(select(PostTarget).where(PostTarget.channel_id == cid, PostTarget.status == "sent")
                                   .order_by(PostTarget.published_at.desc()).limit(200)):
                    if t.external_id and t.external_id not in posts:
                        posts[t.external_id] = {"media_external_id": t.external_id, "caption": t.caption, "permalink": t.permalink,
                                                "comments": [], "unanswered": 0, "last_at": posts_svc.iso(t.published_at),
                                                "last_unanswered_at": None}
            out = list(posts.values())
            for p in out:
                p["count"] = len(p["comments"])
            if sort == "unanswered":          # posts with the newest unanswered comments on top
                out.sort(key=lambda p: p["last_unanswered_at"] or p["last_at"] or "", reverse=True)
                out.sort(key=lambda p: p["unanswered"] == 0)
            else:
                out.sort(key=lambda p: p["last_at"] or "", reverse=True)
            return {**base, "posts": out}

    @app.post("/api/channels/{cid}/comments/bulk-resolve", tags=["community"])
    def comments_bulk_resolve(cid: str, payload: dict = Body(default={})):
        """Resolve many comments: by ids, by post (media_external_id), or every comment on the channel (all: true)."""
        with core.db.session() as s:
            channel(s, cid)
            qry = select(Comment).where(Comment.channel_id == cid, Comment.is_own.is_(False), Comment.status != "resolved")
            ids = id_list(payload)
            if ids:
                qry = qry.where(Comment.id.in_(ids))
            elif payload.get("media_external_id"):
                qry = qry.where(Comment.media_external_id == payload["media_external_id"])
            elif not payload.get("all"):
                bad(ValueError("Send ids, media_external_id or all: true"))
            if payload.get("only_status"):
                qry = qry.where(Comment.status == payload["only_status"])
            n = 0
            for c in s.scalars(qry):
                c.status = "resolved"
                n += 1
            return {"ok": True, "resolved": n}

    @app.get("/api/channels/{cid}/mentions", tags=["community"])
    def mentions(cid: str, status: str = "all"):
        from ..services import community as community_svc
        with core.db.session() as s:
            ch = channel(s, cid)
            supported = [p for p, c in core.connectors.items() if c.capabilities.get("mentions")]
            names = {p: getattr(c, "name", p) for p, c in core.connectors.items()}
            ok = ch.provider in supported
            qry = select(Comment).where(Comment.channel_id == cid, Comment.kind == "mention")
            if status and status != "all":
                sts = set(status.split(","))
                if "answered" in sts:
                    sts |= {"replied", "resolved"}
                qry = qry.where(Comment.status.in_(sts))
            items = [community_svc.serialize(c, ch) for c in s.scalars(qry.order_by(Comment.at.desc()).limit(500))]
            return {"supported": ok, "provider": ch.provider, "network": names.get(ch.provider, ch.provider),
                    "supported_networks": [{"provider": p, "name": names[p]} for p in supported], "items": items,
                    "open": sum(i["status"] == "open" for i in items),
                    "message": "" if ok else f"Mentions aren't available for {names.get(ch.provider, ch.provider)} yet - HuhQueue only "
                    "shows mentions where the network's official API exposes them. Comments on your posts still arrive in the Comments tab."}

    @app.post("/api/channels/{cid}/mentions/sync", tags=["community"])
    def mentions_sync(cid: str):
        from ..connectors import ConnectorError
        from ..services import community as community_svc
        with core.db.session() as s:
            channel(s, cid)
        try:
            return {"new": community_svc.sync_mentions(core, cid)}
        except ConnectorError as e:
            raise HTTPException(502, e.message) from e

    # ---- comments: bulk resolve / reopen / reply (saved reply), AI suggestions, turn into a post
    @app.post("/api/comments/bulk", tags=["community"])
    def comments_bulk(payload: dict = Body(...)):
        from ..services import community as community_svc
        ids = id_list(payload)
        for k in ("action", "text", "saved_reply_id"):
            if payload.get(k) is not None and not isinstance(payload[k], str):
                raise HTTPException(400, f"'{k}' must be a string")
        try:
            return community_svc.bulk(core, ids, payload.get("action") or "",
                                      payload.get("text") or "", payload.get("saved_reply_id") or "")
        except ValueError as e:
            bad(e)
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e

    @app.post("/api/comments/suggest-reply", tags=["community"])
    def comments_suggest_reply(payload: dict = Body(...)):
        from ..services import community as community_svc
        try:
            return community_svc.suggest_reply(core, payload.get("comment_id") or "", payload.get("tone") or "friendly")
        except KeyError as e:
            raise HTTPException(404, str(e).strip("'")) from e

    @app.post("/api/comments/start-post", tags=["community"])
    def comments_start_post(request: Request, payload: dict = Body(...)):
        """Turn a comment (or a mention) into a post: a caption quoting it and crediting the author. With create_draft
        it is saved as a draft on channel_ids (default: the comment's channel)."""
        from ..services import community as community_svc
        with core.db.session() as s:
            c = s.get(Comment, payload.get("comment_id") or "")
            if not c:
                raise HTTPException(404, "Comment not found")
            caption = community_svc.comment_post_caption(c)
            out = {"caption": caption, "channel_id": c.channel_id, "author": c.author, "comment_id": c.id}
        if payload.get("create_draft"):
            out["draft"] = posts_svc.create_post(core, {"caption": caption, "channel_ids": payload.get("channel_ids") or [out["channel_id"]],
                                                        "when": "draft", "tags": ["from-comment"]},
                                                 source="ui" if getattr(request.state, "via", None) == "session" else "api")
        return out

    # ---- Instagram planned grid: queued posts (what the profile will look like) above the live grid
    @app.get("/api/channels/{cid}/grid", tags=["channels"])
    def planned_grid(cid: str, live: int = 12):
        from ..connectors import ConnectorError
        with core.db.session() as s:
            ch = channel(s, cid)
            rows = list(s.scalars(select(PostTarget).where(
                PostTarget.channel_id == cid, PostTarget.status.in_(("scheduled", "pending_approval", "processing", "sending")))
                .order_by(PostTarget.scheduled_at)))
            planned, queue_ids = [], []
            for t in rows:
                d = posts_svc.serialize_target(core, t)
                if t.status == "scheduled" and t.schedule_mode == "queue":
                    queue_ids.append(t.id)        # the whole queue in time order (what /api/queue/{cid}/order takes)
                if d["format"] == "story":       # stories never land on the grid
                    continue
                m = (d["media"] or [{}])[0]
                planned.append({"id": t.id, "caption": t.caption, "format": d["format"], "status": t.status,
                                "schedule_mode": t.schedule_mode, "scheduled_at": d["scheduled_at"],
                                "scheduled_local": d["scheduled_local"], "scheduled_local_iso": d["scheduled_local_iso"],
                                "thumb_url": m.get("thumb_url") or m.get("url") or "", "kind": m.get("kind") or "",
                                "media_count": len(d["media"]),
                                "collaborators": (t.options or {}).get("collaborators") or "",
                                "in_queue": t.status == "scheduled" and t.schedule_mode == "queue"})
            planned.reverse()                    # newest planned first: the top-left tile is the last post to go out
            auth_, provider = core.channel_auth(ch), ch.provider
        conn = core.connector(provider)
        out = {"provider": provider, "planned": planned, "queue_ids": queue_ids, "live": [], "live_supported": conn.describe()["history"],
               "live_error": ""}
        if out["live_supported"] and live > 0:
            try:
                items = conn.list_media(auth_, limit=max(1, min(live, 30))).get("items", [])
                out["live"] = [m for m in items if (m.get("product_type") or "") != "STORY"]
            except ConnectorError as e:
                out["live_error"] = e.message
        return out

    # ---- insights
    @app.get("/api/channels/{cid}/insights/summary", tags=["insights"])
    def insights_summary(cid: str, range: str = "30d", start: str | None = None, end: str | None = None):  # noqa: A002
        with core.db.session() as s:
            ch = channel(s, cid)
            try:
                return build_insights(core, s, ch, range, start, end)
            except ValueError as e:
                bad(e)

    @app.get("/api/channels/{cid}/insights/export.csv", tags=["insights"])
    def insights_export(cid: str, range: str = "30d", start: str | None = None, end: str | None = None, kind: str = "posts"):  # noqa: A002
        with core.db.session() as s:
            ch = channel(s, cid)
            try:
                d = build_insights(core, s, ch, range, start, end)
            except ValueError as e:
                bad(e)
            name = ch.username
        buf = io.StringIO()
        w = csv.writer(buf)
        keys = [k for k, _ in METRICS if k != "posts"]
        if kind == "daily":
            w.writerow(["day", "previous_day"] + [k for k, _ in METRICS] + [f"previous_{k}" for k, _ in METRICS])
            for r in d["comparison"]:
                w.writerow([r["day"], r["previous_day"]] + [r[k] for k, _ in METRICS] + [r[f"previous_{k}"] for k, _ in METRICS])
        else:
            w.writerow(["period", "published_at", "format", "caption"] + keys + ["tags", "permalink"])
            for period in ("this", "previous"):
                for p in d["posts"][period]:
                    w.writerow([period, p["published_at"], p["format"], (p["caption"] or "")[:200]] + [p["metrics"][k] for k in keys]
                               + [" ".join(p["tags"] or []), p["permalink"]])
        fn = f"huhqueue-{re.sub(r'[^A-Za-z0-9_.-]', '', name) or 'channel'}-insights-{kind}.csv"
        return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={fn}"})

    @app.post("/api/channels/{cid}/insights/share", tags=["insights"])
    def insights_share(cid: str, request: Request, payload: dict = Body(default={})):
        """Turn the summary into a post caption; with create_draft=true it is also saved as a draft on this channel."""
        with core.db.session() as s:
            ch = channel(s, cid)
            try:
                d = build_insights(core, s, ch, payload.get("range") or "30d", payload.get("start"), payload.get("end"))
            except ValueError as e:
                bad(e)
            caption = share_caption(ch, d)
        out = {"caption": caption, "period": d["period"]}
        if payload.get("create_draft"):
            out["draft"] = posts_svc.create_post(core, {"caption": caption, "channel_ids": [cid], "when": "draft", "tags": ["insights"]},
                                                 source="ui" if getattr(request.state, "via", None) == "session" else "api")
        return out
