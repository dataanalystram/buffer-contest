"""Analytics (Buffer Analyze parity, free): audience growth from daily follower snapshots, per-post and Reels metrics,
post-type / hashtag / hashtag-group / campaign / tag performance, best time to post from our own data, and CSV export.

Only metrics the networks actually return are used. For Instagram (Graph API v26, 2026) that is views, reach, likes,
comments, shares, saved, total_interactions, profile_visits, follows, reposts and, for Reels, ig_reels_avg_watch_time,
ig_reels_video_view_total_time and reels_skip_rate. `impressions` (deprecated for media after 2 Jul 2024) and `plays`
(replaced by `views`) are never requested. Engagement rate = total_interactions / reach (computed in the connector).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import statistics
from datetime import date, datetime, timedelta

from sqlalchemy import select

from ..models import KV, Channel, FollowerSnapshot
from . import posts as posts_svc
from . import scheduling

log = logging.getLogger("huhqueue.analytics")

SUM_METRICS = ("views", "reach", "likes", "comments", "shares", "saved")
POST_COLUMNS = [("views", "Views"), ("reach", "Reach"), ("likes", "Likes"), ("comments", "Comments"), ("shares", "Shares"),
                ("saved", "Saves"), ("engagement_rate", "Eng. %"), ("avg_watch_time", "Avg watch (s)"),
                ("watch_time", "Watch time (s)"), ("profile_visits", "Profile visits"), ("follows", "Follows")]
SECTIONS = ("overview", "audience", "channels", "posts", "reels", "types", "best_times", "hashtags", "campaigns")
HASHTAG = re.compile(r"(?<![\w#])#(\w{2,60})", re.UNICODE)
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def utcnow() -> datetime:
    return posts_svc.utcnow()


def _num(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _avg(vals) -> float | None:
    vals = [float(v) for v in vals if v is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


# --------------------------------------------------------------------------------------------- audience growth
def snapshot_followers(core, now: datetime | None = None, refresh: bool = True) -> int:
    """Store today's follower total for every active channel (once per channel per UTC day).

    With refresh=True the channel profile is re-read from the network first (connector.fetch_profile) so the number
    is current; if that fails the last known `meta.followers_count` is used. Returns how many snapshots were written."""
    now = now or utcnow()
    day = now.date().isoformat()
    n = 0
    with core.db.session() as s:
        have = set(s.scalars(select(FollowerSnapshot.channel_id).where(FollowerSnapshot.day == day)))
        for ch in s.scalars(select(Channel).where(Channel.status == "active")):
            if ch.id in have:
                continue
            meta = dict(ch.meta or {})
            if refresh:
                conn = core.connectors.get(ch.provider)
                try:
                    prof = conn.fetch_profile(core.channel_auth(ch)) if conn else None
                    if prof and prof.meta:
                        for k in ("followers_count", "media_count"):
                            if prof.meta.get(k) is not None:
                                meta[k] = prof.meta[k]
                        ch.meta = meta
                except Exception as e:  # network down, token expired: fall back to the last known number
                    log.info("follower refresh failed for %s: %s", ch.id, e.__class__.__name__)
            if meta.get("followers_count") is None:
                continue
            s.add(FollowerSnapshot(channel_id=ch.id, day=day, followers=int(meta["followers_count"]),
                                   media_count=int(meta["media_count"]) if meta.get("media_count") is not None else None))
            n += 1
    return n


def run_due(core, now: datetime | None = None) -> int:
    """Worker hook: at most one snapshot pass per UTC day (cheap check in the settings table)."""
    now = now or utcnow()
    day = now.date().isoformat()
    with core.db.session() as s:
        row = s.get(KV, "followers_snapshot_day")
        if row and (row.value or {}).get("day") == day:
            return 0
    n = snapshot_followers(core, now)
    with core.db.session() as s:
        row = s.get(KV, "followers_snapshot_day") or KV(key="followers_snapshot_day", value={})
        row.value = {"day": day, "written": n}
        s.merge(row)
    return n


def audience(core, lo: datetime, hi: datetime, channel_id: str | None = None) -> dict:
    """Followers over time per channel and in total, net growth vs the previous period of the same length."""
    span = hi - lo
    plo = lo - span
    ids = channel_id.split(",") if channel_id else None
    with core.db.session() as s:
        q = select(Channel)
        if ids:
            q = q.where(Channel.id.in_(ids))
        chans = list(s.scalars(q.order_by(Channel.created_at)))
        sq = select(FollowerSnapshot).where(FollowerSnapshot.day >= plo.date().isoformat(),
                                            FollowerSnapshot.day <= hi.date().isoformat())
        if ids:
            sq = sq.where(FollowerSnapshot.channel_id.in_(ids))
        snaps: dict[str, dict[str, int]] = {}
        for r in s.scalars(sq.order_by(FollowerSnapshot.day)):
            if r.followers is not None:
                snaps.setdefault(r.channel_id, {})[r.day] = r.followers
        chan_rows = [{"id": c.id, "provider": c.provider, "username": c.username,
                      "current": (c.meta or {}).get("followers_count")} for c in chans]
    days = [(lo.date() + timedelta(days=i)).isoformat() for i in range(max(1, (hi.date() - lo.date()).days + 1))]

    def growth(seq: dict[str, int], a: date, b: date):
        """End minus start, where start is the total on day a (last snapshot on or before it, else the first after)."""
        items = sorted(seq.items())
        upto_b = [(d, v) for d, v in items if d <= b.isoformat()]
        before_a = [(d, v) for d, v in items if d <= a.isoformat()]
        start = before_a[-1] if before_a else next(((d, v) for d, v in items if a.isoformat() <= d <= b.isoformat()), None)
        if not start or not upto_b or upto_b[-1][0] <= start[0]:
            return None
        return upto_b[-1][1] - start[1]

    out_ch, total_series = [], {d: 0 for d in days}
    have_any = {d: False for d in days}
    net_cur = net_prev = 0
    any_cur = any_prev = False
    for c in chan_rows:
        seq = snaps.get(c["id"], {})
        series, last = [], None
        for d in days:                          # carry the last known value forward over missing days
            if d in seq:
                last = seq[d]
            elif last is None:
                prior = [v for k, v in sorted(seq.items()) if k < d]
                last = prior[-1] if prior else None
            series.append({"date": d, "followers": last})
            if last is not None:
                total_series[d] += last
                have_any[d] = True
        g_cur = growth(seq, lo.date(), hi.date())
        g_prev = growth(seq, plo.date(), lo.date())
        if g_cur is not None:
            net_cur += g_cur
            any_cur = True
        if g_prev is not None:
            net_prev += g_prev
            any_prev = True
        first = next((x["followers"] for x in series if x["followers"] is not None), None)
        end = series[-1]["followers"] if series and series[-1]["followers"] is not None else c["current"]
        out_ch.append({**c, "start": first, "end": end, "net": g_cur, "previous_net": g_prev,
                       "growth_pct": round(100 * g_cur / first, 2) if g_cur is not None and first else None,
                       "series": series, "snapshots": len(seq)})
    total = [{"date": d, "followers": total_series[d] if have_any[d] else None} for d in days]
    nc, npv = (net_cur if any_cur else None), (net_prev if any_prev else None)
    return {"series": total, "channels": out_ch, "net": nc, "previous_net": npv,
            "change_pct": round(100 * (nc - npv) / abs(npv), 1) if nc is not None and npv else None,
            "followers": sum(c["end"] or 0 for c in out_ch),
            "note": None if any(c["snapshots"] for c in out_ch) else
            "Follower history starts the day HuhQueue first records it (once a day); the chart fills in from there."}


# --------------------------------------------------------------------------------------------- per-post analytics
def post_type(r: dict) -> str:
    return r.get("format") or "post"


def hashtags_of(caption: str) -> list[str]:
    seen, out = set(), []
    for h in HASHTAG.findall(caption or ""):
        k = h.lower()
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def filter_rows(rows: list[dict], tag: str | None = None, campaign: str | None = None, fmt: str | None = None) -> list[dict]:
    if tag:
        want = {t.strip().lower() for t in tag.split(",") if t.strip()}
        rows = [r for r in rows if want & {str(t).lower() for t in r.get("tags") or []}]
    if campaign:
        want = {c.strip().lower() for c in campaign.split(",") if c.strip()}
        rows = [r for r in rows if (r.get("campaign") or "").lower() in want]
    if fmt:
        want = {f.strip().lower() for f in fmt.split(",") if f.strip()}
        rows = [r for r in rows if post_type(r) in want]
    return rows


def kpis(rows: list[dict]) -> dict:
    k = {"posts": len(rows)}
    for m in SUM_METRICS:
        k[m] = int(sum(_num((r["insights"] or {}).get(m)) for r in rows))
    k["engagement_rate"] = _avg((r["insights"] or {}).get("engagement_rate") for r in rows)
    return k


def group_stats(rows: list[dict]) -> dict:
    ins = [r["insights"] or {} for r in rows]
    return {"posts": len(rows), "views": int(sum(_num(i.get("views")) for i in ins)),
            "reach": int(sum(_num(i.get("reach")) for i in ins)),
            "avg_views": _avg(i.get("views") for i in ins), "avg_reach": _avg(i.get("reach") for i in ins),
            "avg_likes": _avg(i.get("likes") for i in ins),
            "engagement_rate": _avg(i.get("engagement_rate") for i in ins)}


def reels(rows: list[dict], prev_rows: list[dict] | None = None) -> dict:
    def summ(rs):
        rs = [r for r in rs if post_type(r) in ("reel", "short", "video")]
        ins = [r["insights"] or {} for r in rs]
        return {"count": len(rs), "views": int(sum(_num(i.get("views")) for i in ins)),
                "reach": int(sum(_num(i.get("reach")) for i in ins)),
                "avg_watch_time": _avg(i.get("avg_watch_time") for i in ins),
                "watch_time": round(sum(_num(i.get("watch_time")) for i in ins), 1),
                "skip_rate": _avg(i.get("reels_skip_rate") for i in ins),
                "engagement_rate": _avg(i.get("engagement_rate") for i in ins)}
    cur = summ(rows)
    out = {**cur, "previous": summ(prev_rows or [])}
    top = sorted([r for r in rows if post_type(r) in ("reel", "short", "video")],
                 key=lambda r: _num((r["insights"] or {}).get("views")), reverse=True)[:10]
    out["top"] = [_slim(r) for r in top]
    return out


def _slim(r: dict) -> dict:
    return {"id": r["id"], "caption": (r.get("caption") or "")[:200], "published_at": r.get("published_at"),
            "permalink": r.get("permalink"), "format": post_type(r), "campaign": r.get("campaign") or "",
            "tags": r.get("tags") or [], "channel": {k: r["channel"].get(k) for k in ("id", "provider", "username", "avatar_url")},
            "insights": {k: v for k, v in (r.get("insights") or {}).items() if k != "error"}}


def types(rows: list[dict]) -> list[dict]:
    by: dict[str, list] = {}
    for r in rows:
        by.setdefault(post_type(r), []).append(r)
    return sorted(({"format": k, **group_stats(v)} for k, v in by.items()), key=lambda x: -(x["avg_reach"] or x["avg_views"] or 0))


def hashtag_groups(core) -> list[dict]:
    with core.db.session() as s:
        row = s.get(KV, "lib:hashtag_groups")
        if row:
            return list((row.value or {}).get("items") or [])
    from ..api.app import DEFAULT_LIBRARY
    return list(DEFAULT_LIBRARY.get("hashtag_groups") or [])


def hashtags(core, rows: list[dict], limit: int = 25) -> dict:
    """Per-hashtag averages and per-hashtag-group averages (a post 'used' a group when it has at least half its tags)."""
    by: dict[str, list] = {}
    for r in rows:
        for h in hashtags_of(r.get("caption") or ""):
            by.setdefault(h, []).append(r)
    tags = sorted(({"hashtag": "#" + h, **group_stats(v)} for h, v in by.items()),
                  key=lambda x: (-(x["avg_reach"] or 0), -(x["avg_views"] or 0), -x["posts"]))[:limit]
    groups = []
    for g in hashtag_groups(core):
        gt = set(hashtags_of(g.get("text") or ""))
        if not gt:
            continue
        used = [r for r in rows if len(gt & set(hashtags_of(r.get("caption") or ""))) * 2 >= len(gt)]
        others = [r for r in rows if r not in used]
        st = group_stats(used)
        base = group_stats(others)
        groups.append({"id": g.get("id"), "name": g.get("name") or g.get("id"), "hashtags": len(gt), **st,
                       "vs_others_reach_pct": round(100 * (st["avg_reach"] - base["avg_reach"]) / base["avg_reach"], 1)
                       if st["avg_reach"] is not None and base["avg_reach"] else None})
    return {"hashtags": tags, "groups": groups}


def campaigns(rows: list[dict]) -> dict:
    by: dict[str, list] = {}
    tag_by: dict[str, list] = {}
    for r in rows:
        if r.get("campaign"):
            by.setdefault(r["campaign"], []).append(r)
        for t in r.get("tags") or []:
            tag_by.setdefault(str(t), []).append(r)
    rank = lambda x: (-(x["reach"] or 0), -(x["views"] or 0))  # noqa: E731
    return {"campaigns": sorted(({"campaign": k, **group_stats(v)} for k, v in by.items()), key=rank),
            "tags": sorted(({"tag": k, **group_stats(v)} for k, v in tag_by.items()), key=rank)[:25]}


# --------------------------------------------------------------------------------------------- best time to post
def best_times(core, channel_id: str | None = None, days: int = 180, top: int = 5) -> dict:
    from ..api.pro_content import own_heatmap
    with core.db.session() as s:
        heat = own_heatmap(s, channel_id, days)
    cells = [(c["score"], c["posts"], d, h) for d in range(7) for h in range(24) if (c := heat["grid"][d][h])]
    ranked = sorted(cells, key=lambda x: (-x[0], -x[1], x[2], x[3]))
    return {"heatmap": heat, "top": [{"weekday": d, "day": DAYS[d], "hour": h, "time": f"{h:02d}:00", "score": sc, "posts": n}
                                     for sc, n, d, h in ranked[:top]],
            "enough_data": heat["posts"] >= 10,
            "basis": "median engagement rate (or weighted interactions) of your sent posts, by weekday and hour in each channel's timezone"}


def apply_best_slots(core, channel_id: str, count: int = 5, days: int = 180, replace: bool = True) -> dict:
    """'Use as posting slots': write the top N weekday x hour cells of this channel's own data as its schedule."""
    count = max(1, min(int(count or 5), 21))
    bt = best_times(core, channel_id, days, top=count)
    if not bt["top"]:
        raise ValueError("Not enough sent posts with insights on this channel yet to pick times from")
    from ..api.pro_content import _minute
    with core.db.session() as s:
        ch = s.get(Channel, channel_id)
        if not ch:
            raise LookupError("Channel not found")
        new = [{"weekday": t["weekday"], "time": f"{t['hour']:02d}:{_minute(ch.id, t['weekday']):02d}"} for t in bt["top"]]
        keep = [] if replace else [{"weekday": x.weekday, "time": x.time} for x in ch.slots]
        slots = keep + [x for x in new if x not in keep]
        scheduling.set_slots(s, ch, slots)
        per_day = max([sum(1 for x in slots if x["weekday"] == d) for d in range(7)] or [1])
        ch.daily_limit = max(ch.daily_limit or 0, per_day)
        s.flush()
        scheduling.reflow(s, ch, utcnow())
        from .events import record
        record(core, s, f"Posting times set from your best times: {len(new)} a week", channel_id=ch.id)
        return {"ok": True, "slots": [{"weekday": x.weekday, "time": x.time} for x in sorted(ch.slots, key=lambda z: (z.weekday, z.time))],
                "picked": bt["top"]}


# --------------------------------------------------------------------------------------------- CSV
def to_csv(data: dict, sections: list[str] | None = None) -> str:
    """One CSV with a block per section (blank line between blocks), matching the PDF's sections."""
    want = set(sections or SECTIONS)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"{data.get('brand', 'HuhQueue')} social report", data["range"][0][:10], data["range"][1][:10]])
    w.writerow([])
    if "overview" in want:
        w.writerow(["metric", "this_period", "previous_period", "change_pct"])
        for k in data["kpis"]:
            w.writerow([k["metric"], k["current"], k["previous"], k["change_pct"]])
        w.writerow([])
    if "audience" in want and data.get("audience"):
        a = data["audience"]
        w.writerow(["date", "followers_total"] + [f"@{c['username']} ({c['provider']})" for c in a["channels"]])
        for i, d in enumerate(a["series"]):
            w.writerow([d["date"], d["followers"]] + [c["series"][i]["followers"] for c in a["channels"]])
        w.writerow([])
    if "posts" in want:
        w.writerow(["published_at", "provider", "username", "format", "campaign", "caption", "permalink"] + [k for k, _ in POST_COLUMNS])
        for r in data.get("posts") or data["top_posts"]:
            i = r.get("insights") or {}
            w.writerow([r.get("published_at"), r["channel"]["provider"], r["channel"]["username"], r.get("format", ""),
                        r.get("campaign", ""), (r.get("caption") or "").replace("\n", " ")[:300], r.get("permalink") or ""]
                       + [i.get(k) for k, _ in POST_COLUMNS])
        w.writerow([])
    if "reels" in want and data.get("reels"):
        rl = data["reels"]
        w.writerow(["reels_metric", "this_period", "previous_period"])
        for k in ("count", "views", "reach", "avg_watch_time", "watch_time", "skip_rate", "engagement_rate"):
            w.writerow([k, rl.get(k), rl["previous"].get(k)])
        w.writerow([])
    if "types" in want and data.get("types") is not None:
        w.writerow(["format", "posts", "avg_reach", "avg_views", "engagement_rate"])
        for t in data["types"]:
            w.writerow([t["format"], t["posts"], t["avg_reach"], t["avg_views"], t["engagement_rate"]])
        w.writerow([])
    if "best_times" in want and data.get("best_times"):
        w.writerow(["best_day", "hour", "score", "posts"])
        for t in data["best_times"]["top"]:
            w.writerow([t["day"], t["time"], t["score"], t["posts"]])
        w.writerow([])
    if "hashtags" in want and data.get("hashtags"):
        w.writerow(["hashtag", "posts", "avg_reach", "avg_views", "engagement_rate"])
        for h in data["hashtags"]["hashtags"]:
            w.writerow([h["hashtag"], h["posts"], h["avg_reach"], h["avg_views"], h["engagement_rate"]])
        w.writerow([])
        w.writerow(["hashtag_group", "posts", "avg_reach", "engagement_rate", "vs_others_reach_pct"])
        for g in data["hashtags"]["groups"]:
            w.writerow([g["name"], g["posts"], g["avg_reach"], g["engagement_rate"], g["vs_others_reach_pct"]])
        w.writerow([])
    if "campaigns" in want and data.get("campaigns"):
        w.writerow(["campaign", "posts", "reach", "views", "avg_reach", "engagement_rate"])
        for c in data["campaigns"]["campaigns"]:
            w.writerow([c["campaign"], c["posts"], c["reach"], c["views"], c["avg_reach"], c["engagement_rate"]])
        w.writerow([])
    return buf.getvalue()


def parse_sections(raw) -> list[str]:
    if not raw:
        return list(SECTIONS)
    items = raw if isinstance(raw, list) else re.split(r"[,\s]+", str(raw))
    bad = [x for x in items if x and x not in SECTIONS]
    if bad:
        raise ValueError(f"Unknown report section(s): {', '.join(bad)}. Use: {', '.join(SECTIONS)}")
    return [x for x in SECTIONS if x in items]


