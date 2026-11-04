"""Insights extras (Buffer Essentials parity, free): audience demographics, story insights saved before they expire, and
plain-language takeaways across every channel.

* Demographics come from the network (Instagram `follower_demographics` / `engaged_audience_demographics`). They are
  cached on the channel (`meta.demographics`) and refreshed at most once a day by the worker, or on demand.
* Stories: Instagram serves story metrics only while the story is live (24 h). The worker lists live stories every 2 h
  and stores the latest numbers in `story_snapshots`, so they stay readable after the story is gone.
* Takeaways compare every channel's last period with the one before, from our own numbers (no LLM, nothing invented),
  and people can mark each one helpful or not.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from ..connectors import ConnectorError
from ..models import KV, Channel, Comment, FollowerSnapshot, StorySnapshot
from . import posts as posts_svc

log = logging.getLogger("huhqueue.audience")

STORIES_EVERY = timedelta(hours=2)
STORY_LIFETIME = timedelta(hours=24)
DEMOGRAPHICS_EVERY = timedelta(hours=24)
ENGAGED_TIMEFRAMES = ("this_week", "this_month", "last_14_days", "last_30_days", "last_90_days", "prev_month")
COUNTRY_NAMES = {"US": "United States", "IN": "India", "GB": "United Kingdom", "DE": "Germany", "BR": "Brazil",
                 "CA": "Canada", "AU": "Australia", "FR": "France", "ES": "Spain", "IT": "Italy", "MX": "Mexico",
                 "ID": "Indonesia", "NG": "Nigeria", "PH": "Philippines", "PK": "Pakistan", "NL": "Netherlands",
                 "JP": "Japan", "TR": "Türkiye", "AE": "United Arab Emirates", "SG": "Singapore"}
GENDER_NAMES = {"F": "Women", "M": "Men", "U": "Not specified"}


def utcnow() -> datetime:
    return posts_svc.utcnow()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _parse_ts(v: str) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=None) - (d.utcoffset() or timedelta()) if d.tzinfo else d


def _supports(core, ch: Channel, cap: str):
    conn = core.connectors.get(ch.provider)
    return conn if conn and conn.capabilities.get(cap) else None


# --------------------------------------------------------------------------------------------- demographics
def _shape(raw: dict) -> dict:
    """Sort each breakdown, add percentages and readable labels."""
    out = {}
    for who in ("followers", "engaged"):
        sec = {}
        for b, vals in (raw.get(who) or {}).items():
            total = sum(vals.values()) or 1
            items = sorted(vals.items(), key=lambda kv: (-kv[1], kv[0])) if b in ("country", "city") else sorted(vals.items())
            sec[b] = [{"key": k, "label": COUNTRY_NAMES.get(k, k) if b == "country" else GENDER_NAMES.get(k, k) if b == "gender" else k,
                       "value": v, "pct": round(100.0 * v / total, 1)} for k, v in items][:10 if b in ("country", "city") else 20]
        out[who] = sec
    return out


def demographics(core, channel_id: str | None = None, refresh: bool = False, timeframe: str = "this_month",
                 now: datetime | None = None) -> dict:
    """Per-channel audience breakdowns (age, gender, country, city) for followers and engaged accounts."""
    now = now or utcnow()
    rows = []
    with core.db.session() as s:
        q = select(Channel).where(Channel.status == "active")
        if channel_id:
            q = q.where(Channel.id.in_(channel_id.split(",")))
        for ch in s.scalars(q.order_by(Channel.created_at)):
            conn = _supports(core, ch, "demographics")
            row = {"channel_id": ch.id, "provider": ch.provider, "username": ch.username, "supported": bool(conn),
                   "followers_count": (ch.meta or {}).get("followers_count")}
            if not conn:
                c = core.connectors.get(ch.provider)
                row["note"] = f"{c.name if c else ch.provider.title()} does not share audience breakdowns through its API."
                rows.append(row)
                continue
            meta = dict(ch.meta or {})
            cached = meta.get("demographics") or {}
            fetched = _parse_ts(cached.get("fetched_at", ""))
            stale = not fetched or now - fetched >= DEMOGRAPHICS_EVERY or cached.get("timeframe") != timeframe
            if refresh or stale:
                try:
                    raw = conn.audience_demographics(core.channel_auth(ch), timeframe=timeframe) or {}
                    cached = {"fetched_at": now.isoformat() + "Z", "timeframe": raw.get("timeframe", timeframe),
                              "data": _shape(raw), "notes": raw.get("notes") or []}
                    meta["demographics"] = cached
                    ch.meta = meta
                except ConnectorError as e:
                    cached = {**cached, "error": e.message}
            row.update({"fetched_at": cached.get("fetched_at"), "timeframe": cached.get("timeframe", timeframe),
                        "followers": (cached.get("data") or {}).get("followers") or {},
                        "engaged": (cached.get("data") or {}).get("engaged") or {},
                        "notes": cached.get("notes") or [], "error": cached.get("error")})
            if not row["followers"] and not row["engaged"] and not row["error"]:
                row["note"] = "No breakdowns yet. Instagram only shares them for accounts with 100 or more followers."
            rows.append(row)
    return {"channels": rows, "timeframe": timeframe}


# --------------------------------------------------------------------------------------------- stories
def sync_stories(core, now: datetime | None = None, channel_id: str | None = None) -> dict:
    """Read every live story and save its metrics. Returns {"channels", "stories", "errors"}."""
    now = now or utcnow()
    n_ch = n_st = 0
    errors = []
    with core.db.session() as s:
        q = select(Channel).where(Channel.status == "active")
        if channel_id:
            q = q.where(Channel.id == channel_id)
        for ch in s.scalars(q):
            conn = _supports(core, ch, "stories")
            if not conn:
                continue
            auth = core.channel_auth(ch)
            try:
                live = conn.list_stories(auth)
            except ConnectorError as e:
                errors.append(f"@{ch.username}: {e.message}")
                continue
            n_ch += 1
            for st in live:
                ext = st.get("external_id")
                if not ext:
                    continue
                row = s.scalars(select(StorySnapshot).where(StorySnapshot.channel_id == ch.id,
                                                            StorySnapshot.external_id == ext)).first()
                if not row:
                    row = StorySnapshot(channel_id=ch.id, external_id=ext)
                    s.add(row)
                for k in ("media_type", "media_url", "thumbnail_url", "permalink", "caption"):
                    if st.get(k):
                        setattr(row, k, st[k])
                row.posted_at = row.posted_at or _parse_ts(st.get("timestamp", "")) or now
                try:
                    m = conn.story_insights(auth, ext) or {}
                except ConnectorError as e:
                    errors.append(f"@{ch.username} story {ext}: {e.message}")
                    continue
                if m:
                    row.metrics = {k: v for k, v in m.items() if v is not None}
                    row.reads = (row.reads or 0) + 1
                    row.last_read_at = now
                    n_st += 1
    return {"channels": n_ch, "stories": n_st, "errors": errors}


def run_due(core, now: datetime | None = None) -> dict:
    """Worker hook: stories every 2 h, demographics once a day (cheap checks in the settings table)."""
    now = now or utcnow()
    out = {}
    with core.db.session() as s:
        row = s.get(KV, "audience_extras_last") or KV(key="audience_extras_last", value={})
        last = dict(row.value or {})
    ls = _parse_ts(last.get("stories", ""))
    if not ls or now - ls >= STORIES_EVERY:
        out["stories"] = sync_stories(core, now)["stories"]
        last["stories"] = now.isoformat() + "Z"
    ld = _parse_ts(last.get("demographics", ""))
    if not ld or now - ld >= DEMOGRAPHICS_EVERY:
        out["demographics"] = len([c for c in demographics(core, now=now)["channels"] if c["supported"]])
        last["demographics"] = now.isoformat() + "Z"
    if out:
        with core.db.session() as s:
            row = s.get(KV, "audience_extras_last") or KV(key="audience_extras_last", value={})
            row.value = last
            s.merge(row)
    return out


def _story_dict(r: StorySnapshot, ch: Channel | None, now: datetime) -> dict:
    m = r.metrics or {}
    reach = m.get("reach") or 0
    fwd, back, exits = m.get("tap_forward") or 0, m.get("tap_back") or 0, m.get("tap_exit") or 0
    live = bool(r.posted_at and now - r.posted_at < STORY_LIFETIME)
    return {"id": r.id, "channel_id": r.channel_id, "username": ch.username if ch else "", "provider": ch.provider if ch else "",
            "external_id": r.external_id, "media_type": r.media_type, "thumbnail_url": r.thumbnail_url or r.media_url,
            "permalink": r.permalink, "caption": r.caption, "posted_at": _iso(r.posted_at),
            "expires_at": _iso(r.posted_at + STORY_LIFETIME) if r.posted_at else None, "live": live,
            "last_read_at": _iso(r.last_read_at), "reads": r.reads, "metrics": m,
            "exit_rate": round(100.0 * exits / reach, 1) if reach else None,
            "completion_hint": round(100.0 * fwd / (fwd + back + exits), 1) if (fwd + back + exits) else None}


def stories(core, channel_id: str | None = None, days: int = 30, now: datetime | None = None) -> dict:
    now = now or utcnow()
    lo = now - timedelta(days=max(1, min(int(days or 30), 3650)))
    with core.db.session() as s:
        cq = select(Channel)
        if channel_id:   # the channel list follows the filter too (a scoped caller never sees other channels, 4 Nov 2026)
            cq = cq.where(Channel.id.in_(channel_id.split(",")))
        chans = {c.id: c for c in s.scalars(cq)}
        q = select(StorySnapshot).where(StorySnapshot.posted_at >= lo)
        if channel_id:
            q = q.where(StorySnapshot.channel_id.in_(channel_id.split(",")))
        items = [_story_dict(r, chans.get(r.channel_id), now) for r in s.scalars(q.order_by(StorySnapshot.posted_at.desc()))]
        supported = [{"channel_id": c.id, "username": c.username, "provider": c.provider} for c in chans.values()
                     if c.status == "active" and _supports(core, c, "stories")]
    tot = {k: sum((x["metrics"].get(k) or 0) for x in items) for k in ("views", "reach", "replies", "shares", "tap_exit")}
    n = len(items)
    summary = {"stories": n, **tot, "avg_reach": round(tot["reach"] / n, 1) if n else None,
               "avg_views": round(tot["views"] / n, 1) if n else None,
               "exit_rate": round(100.0 * tot["tap_exit"] / tot["reach"], 1) if tot["reach"] else None}
    return {"items": items, "summary": summary, "channels": supported,
            "note": None if supported else "Connect an Instagram account to save Story insights before they expire."}


# --------------------------------------------------------------------------------------------- cross-channel takeaways
def _feedback(core) -> dict:
    with core.db.session() as s:
        row = s.get(KV, "takeaway_feedback")
        return dict(row.value or {}) if row else {}


def save_feedback(core, key: str, helpful: bool | None) -> dict:
    key = str(key or "")[:120]
    if not key:
        raise ValueError("key is required")
    with core.db.session() as s:
        row = s.get(KV, "takeaway_feedback") or KV(key="takeaway_feedback", value={})
        fb = dict(row.value or {})
        if helpful is None:
            fb.pop(key, None)
        else:
            fb[key] = {"helpful": bool(helpful), "at": utcnow().isoformat() + "Z"}
        row.value = fb
        s.merge(row)
    return {"key": key, "helpful": helpful}


def takeaways(core, rng: str = "30d", now: datetime | None = None, channel_ids: list[str] | None = None) -> dict:
    """Takeaways across every channel (or only `channel_ids`) for the last period vs the one before (7d / 30d / 90d / mtd)."""
    from ..api import channel_pages as cp
    from ..scope_filter import only
    vis = None if channel_ids is None else set(channel_ids)
    now = now or utcnow()
    per = []
    with core.db.session() as s:
        chans = list(s.scalars(only(select(Channel).where(Channel.status == "active"), Channel.id, vis).order_by(Channel.created_at)))
        for ch in chans:
            d = cp.build_insights(core, s, ch, rng, None, None)
            weeks = max(1.0, _weeks(rng))
            per.append({"ch": ch, "cur": d["totals"], "prev": d["previous_totals"], "goal": d["goal"], "weeks": weeks,
                        "posts": d["posts"]["this"], "period": d["period"], "previous_period": d["previous_period"]})
        open_comments = s.scalar(only(select(func.count()).select_from(Comment).where(Comment.status == "open",
                                                                                       Comment.is_own.is_(False)),
                                      Comment.channel_id, vis)) or 0
        lo = now - timedelta(days=_days(rng))
        snaps: dict[str, list] = {}
        for r in s.scalars(only(select(FollowerSnapshot).where(FollowerSnapshot.day >= (lo - timedelta(days=1)).date().isoformat()),
                                FollowerSnapshot.channel_id, vis).order_by(FollowerSnapshot.day)):
            if r.followers is not None:
                snaps.setdefault(r.channel_id, []).append(r.followers)
        story_rows = list(s.scalars(only(select(StorySnapshot).where(StorySnapshot.posted_at >= lo), StorySnapshot.channel_id, vis)))
    fb = _feedback(core)
    out: list[dict] = []

    def add(key, tone, text, **extra):
        out.append({"key": key, "tone": tone, "text": text, "feedback": (fb.get(key) or {}).get("helpful"), **extra})

    active = [p for p in per if p["cur"]["posts"] or p["prev"]["posts"]]
    tot_cur = {k: sum(p["cur"][k] for p in per) for k in ("posts", "views", "engagements")}
    tot_prev = {k: sum(p["prev"][k] for p in per) for k in ("posts", "views", "engagements")}
    for k in ("views", "engagements"):
        d = cp.delta(tot_cur[k], tot_prev[k])
        if d is not None and tot_prev[k]:
            add(f"all_{k}", "good" if d >= 0 else "warn",
                f"Across all channels, {k} are {'up' if d >= 0 else 'down'} {abs(d):.0f}% on the previous period "
                f"({tot_prev[k]:,} → {tot_cur[k]:,}).")
    movers = [(cp.delta(p["cur"]["views"], p["prev"]["views"]), p) for p in active if p["prev"]["views"]]
    movers = [(d, p) for d, p in movers if d is not None]
    if len(movers) >= 2:
        d, p = max(movers, key=lambda x: x[0])
        if d > 0:
            add("top_grower", "good", f"@{p['ch'].username} ({p['ch'].provider}) grew the most: views up {d:.0f}%.",
                channel_id=p["ch"].id)
        d2, p2 = min(movers, key=lambda x: x[0])
        if d2 < 0 and p2 is not p:
            add("top_decliner", "warn", f"@{p2['ch'].username} ({p2['ch'].provider}) slipped: views down {abs(d2):.0f}%. "
                "Check what changed in its posting.", channel_id=p2["ch"].id)
    rated = [p for p in active if p["cur"]["posts"] >= 1 and p["cur"]["views"]]
    if len(rated) >= 2:
        p = max(rated, key=lambda x: x["cur"]["engagement_rate"])
        add("best_engagement", "info", f"@{p['ch'].username} ({p['ch'].provider}) has the most engaged audience: "
            f"{p['cur']['engagement_rate']}% engagement rate. Reuse what works there on your other channels.", channel_id=p["ch"].id)
    under = [p for p in per if p["goal"] and p["cur"]["posts"] / p["weeks"] < p["goal"]]
    if under:
        names = ", ".join(f"@{p['ch'].username}" for p in under[:3]) + (f" and {len(under) - 3} more" if len(under) > 3 else "")
        add("under_goal", "warn", f"{len(under)} channel{'s are' if len(under) > 1 else ' is'} under the weekly posting goal: {names}. "
            "Fill the empty queue slots to catch up.", channel_ids=[p["ch"].id for p in under])
    elif per and tot_cur["posts"]:
        add("on_goal", "good", "Every channel hit its weekly posting goal. Keep the rhythm.")
    by_fmt: dict[str, list[int]] = {}
    for p in per:
        for r in p["posts"]:
            by_fmt.setdefault(r["format"] or "post", []).append(r["metrics"]["engagements"])
    if len(by_fmt) >= 2:
        f, v = max(by_fmt.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
        add("best_format", "info", f"{f.capitalize()}s earned the most engagement across channels ({sum(v) / len(v):.1f} per post).")
    allp = [(r, p["ch"]) for p in per for r in p["posts"]]
    if allp:
        r, ch = max(allp, key=lambda x: (x[0]["metrics"]["engagements"], x[0]["metrics"]["views"]))
        if r["metrics"]["engagements"] or r["metrics"]["views"]:
            cap = ((r["caption"] or "your post").strip().splitlines() or ["your post"])[0][:70]
            add("top_post", "good", f"Your best post overall was “{cap}” on @{ch.username} with {r['metrics']['engagements']:,} "
                f"engagements and {r['metrics']['views']:,} views.", post_id=r["id"], permalink=r["permalink"])
    growth = []
    for p in per:
        seq = snaps.get(p["ch"].id) or []
        if len(seq) >= 2:
            growth.append((seq[-1] - seq[0], p["ch"]))
    if growth:
        g, ch = max(growth, key=lambda x: x[0])
        if g > 0:
            add("followers", "good", f"@{ch.username} gained the most followers: +{g:,} this period.", channel_id=ch.id)
    if story_rows:
        reach = [r.metrics.get("reach") or 0 for r in story_rows]
        add("stories", "info", f"{len(story_rows)} stor{'ies' if len(story_rows) != 1 else 'y'} reached {sum(reach) / len(reach):.0f} "
            "accounts on average. Their numbers are saved, so you can compare them after they expire.")
    if open_comments:
        add("comments", "warn", f"You have {open_comments} comment{'s' if open_comments != 1 else ''} without a reply. "
            "Answering quickly lifts reach.", count=open_comments)
    period = per[0]["period"] if per else None
    return {"range": rng, "period": period, "takeaways": out[:8], "channels": len(per)}


def _days(rng: str) -> int:
    rng = (rng or "30d").lower().rstrip("d")
    return 31 if rng == "mtd" else int(rng) if rng.isdigit() else 30


def _weeks(rng: str) -> float:
    return _days(rng) / 7
