"""Branded reports: period comparison data, a lavender HuhByte PDF, a weekly schedule, an archive and email delivery.

The numbers come from the same sent-post insights as /api/insights and /api/reports/report.md (pro_content.build_report),
so the PDF, the markdown and the Insights page always agree.

Email uses plain SMTP from the environment (nothing is stored in the database):
  HUHQ_SMTP_HOST, HUHQ_SMTP_PORT (587), HUHQ_SMTP_USER, HUHQ_SMTP_PASSWORD, HUHQ_SMTP_FROM, HUHQ_SMTP_TLS (starttls|ssl|none)
Without SMTP the scheduled report is still generated and kept in the archive (Insights › Report › Archive) for download.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from ..models import KV, Channel, PostTarget
from . import analytics as an
from . import posts as posts_svc
from .pdf import PDF, fit, wrap

log = logging.getLogger("huhqueue.reports")

# HuhByte lavender theme (web/src/index.css --lav-*)
LAV = {"50": "#faf7ff", "100": "#f3ecff", "200": "#e6d9fe", "300": "#d7c3fc", "400": "#c9acfa", "500": "#a985f3",
       "600": "#8a63e6", "700": "#6d46c9", "ink": "#1f1830", "muted": "#6b6280", "green": "#059669", "red": "#e11d48"}
LOGO = Path(__file__).resolve().parent.parent / "static" / "logo.png"
METRIC_LABELS = {"posts": "Posts", "views": "Views", "reach": "Reach", "likes": "Likes", "comments": "Comments",
                 "shares": "Shares", "saved": "Saves", "engagement_rate": "Avg. engagement"}
SCHEDULE_KEY = "report_schedule"
DEFAULT_SCHEDULE = {"enabled": False, "weekday": 0, "time": "09:00", "days": "7", "channel_id": "", "email_to": [],
                    "tag": "", "campaign": "", "format": "", "sections": [],
                    "timezone": "", "last_run_at": None, "next_run_at": None, "last_status": ""}
EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def _parse_iso(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v.rstrip("Z")) if v else None


def _rows(core, lo, hi, channel_id):
    with core.db.session() as s:
        q = select(PostTarget).where(PostTarget.status == "sent", PostTarget.published_at >= lo, PostTarget.published_at < hi)
        if channel_id:
            q = q.where(PostTarget.channel_id.in_(channel_id.split(",")))
        return [posts_svc.serialize_target(core, t) for t in s.scalars(q)]


def _sum(rows, m):
    return sum(int((r["insights"] or {}).get(m) or 0) for r in rows)


def _rate(rows):
    v = [r["insights"]["engagement_rate"] for r in rows if (r["insights"] or {}).get("engagement_rate") is not None]
    return round(sum(v) / len(v), 2) if v else None


def _delta(a, b):
    if a is None or b in (None, 0):
        return None
    return round(100 * (a - b) / b, 1)


def report_data(core, days="30", start=None, end=None, channel_id=None, tag=None, campaign=None, fmt=None,
                sections=None) -> dict:
    """Everything a report shows: KPIs vs previous period, daily series (both periods), per-channel breakdown, top posts,
    plus audience growth, Reels, post types, best times, hashtags and campaigns (services/analytics.py).

    tag / campaign / fmt (post format: post, carousel, reel, story, short, video) narrow the posts; `sections` picks
    what the PDF/CSV include (the JSON always carries everything)."""
    from ..api.pro_content import build_report   # same numbers as the markdown report
    base = build_report(core, days, start, end, channel_id)
    lo, hi = _parse_iso(base["range"][0]), _parse_iso(base["range"][1])
    plo = _parse_iso(base["previous_range"][0])
    cur, prev = _rows(core, lo, hi, channel_id), _rows(core, plo, lo, channel_id)
    filtered = bool(tag or campaign or fmt)
    if filtered:
        cur, prev = an.filter_rows(cur, tag, campaign, fmt), an.filter_rows(prev, tag, campaign, fmt)
        kc, kp = an.kpis(cur), an.kpis(prev)
        base["kpis"] = [{"metric": m, "current": kc[m], "previous": kp[m], "change_pct": _delta(kc[m], kp[m])}
                        for m in ("posts", *an.SUM_METRICS, "engagement_rate")]
        base["top_posts"] = sorted(cur, key=lambda r: (r["insights"] or {}).get("views") or (r["insights"] or {}).get("likes") or 0,
                                   reverse=True)[:10]
    n_days = min(366, max(1, (hi.date() - lo.date()).days + 1))

    def series(rows, start_dt):
        by = {}
        for r in rows:
            by.setdefault((r["published_at"] or "")[:10], []).append(r)
        out = []
        for i in range(n_days):
            d = (start_dt + timedelta(days=i)).date().isoformat()
            day = by.get(d, [])
            out.append({"date": d, "posts": len(day), "views": _sum(day, "views"), "reach": _sum(day, "reach"),
                        "likes": _sum(day, "likes"), "engagement_rate": _rate(day)})
        return out

    with core.db.session() as s:
        q = select(Channel)
        if channel_id:
            q = q.where(Channel.id.in_(channel_id.split(",")))
        chans = [{"id": c.id, "provider": c.provider, "username": c.username, "followers": (c.meta or {}).get("followers_count")}
                 for c in s.scalars(q.order_by(Channel.created_at))]
    per_channel = []
    for c in chans:
        cr = [r for r in cur if r["channel"]["id"] == c["id"]]
        pr = [r for r in prev if r["channel"]["id"] == c["id"]]
        metrics = {}
        for m in ("posts", "views", "reach", "likes", "comments", "shares", "saved"):
            a, b = (len(cr), len(pr)) if m == "posts" else (_sum(cr, m), _sum(pr, m))
            metrics[m] = {"current": a, "previous": b, "change_pct": _delta(a, b)}
        metrics["engagement_rate"] = {"current": _rate(cr), "previous": _rate(pr), "change_pct": _delta(_rate(cr), _rate(pr))}
        per_channel.append({**c, "metrics": metrics, "series": series(cr, lo)})
    prefs = core.prefs()
    brand = prefs.get("report_brand") or (prefs.get("cta_handle") or "").lstrip("@") or "HuhByte"
    posts = sorted(cur, key=lambda r: (r["insights"] or {}).get("views") or (r["insights"] or {}).get("likes") or 0, reverse=True)
    return {**{k: base[k] for k in ("range", "previous_range", "kpis", "top_posts")}, "brand": brand,
            "days": n_days, "series": series(cur, lo), "previous_series": series(prev, plo), "channels": per_channel,
            "audience": an.audience(core, lo, hi, channel_id),
            "posts": [an._slim(r) for r in posts[:500]],
            "reels": an.reels(cur, prev), "types": an.types(cur),
            "best_times": an.best_times(core, channel_id, top=5),   # the full filter: a multi-channel list too
            "hashtags": an.hashtags(core, cur), "campaigns": an.campaigns(cur),
            "filters": {"channel_id": channel_id or "", "tag": tag or "", "campaign": campaign or "", "format": fmt or ""},
            "sections": an.parse_sections(sections),
            "generated_at": posts_svc.iso(datetime.utcnow()), "timezone": prefs.get("timezone")}


# ------------------------------------------------------------------------------------------------- PDF
def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float) and not v.is_integer():
        return f"{v:,.2f}"
    v = int(v)
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:.1f}M"
    if abs(v) >= 10_000:
        return f"{v / 1000:.1f}K"
    return f"{v:,}"


def _chg(p) -> tuple[str, str]:
    if p is None:
        return "no prior data", LAV["muted"]
    return (f"+{p:.1f}%" if p >= 0 else f"{p:.1f}%"), (LAV["green"] if p >= 0 else LAV["red"])


def _bars(pg, x, y, w, h, cur, prev, key, title):
    """Bar chart: this period (deep lavender) with the previous period as light ghost bars behind it."""
    pg.text(x, y - 6, title, 9, True, LAV["ink"])
    pg.rect(x, y, w, h, LAV["50"])
    n = max(1, len(cur))
    top = max([1] + [d[key] for d in cur] + [d[key] for d in prev])
    slot = w / n
    bw = max(1.2, slot * 0.62)
    for i, d in enumerate(cur):
        bx = x + i * slot + (slot - bw) / 2
        if i < len(prev) and prev[i][key]:
            ph = (h - 14) * prev[i][key] / top
            pg.rect(bx - 1, y + h - 12 - ph, bw + 2, ph, LAV["300"])
        if d[key]:
            bh = (h - 14) * d[key] / top
            pg.rect(bx, y + h - 12 - bh, bw, bh, LAV["600"])
    step = max(1, n // 7)
    for i in range(0, n, step):
        pg.text(x + i * slot + slot / 2, y + h - 3, cur[i]["date"][5:], 6, color=LAV["muted"], align="center")
    pg.text(x + w, y - 6, f"max {_fmt(top)}", 6.5, color=LAV["muted"], align="right")


def _logo(pdf, pg, x, y, w):
    if "logo" in pdf.images:
        h = w * pdf.images["logo"]["h"] / pdf.images["logo"]["w"]
        pg.image("logo", x, y, w, h)
        return h
    pg.text(x, y + 20, "HuhByte", 20, True, LAV["700"])
    return 24


def _mix(a: str, b: str, t: float) -> str:
    ra, ga, ba = (int(a[i:i + 2], 16) for i in (1, 3, 5))
    rb, gb, bb = (int(b[i:i + 2], 16) for i in (1, 3, 5))
    t = max(0.0, min(1.0, t))
    return "#%02x%02x%02x" % (round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t))


def _line(pg, x, y, w, h, series, key, title):
    """Line chart (followers over time). Missing days are skipped."""
    pg.text(x, y - 6, title, 9, True, LAV["ink"])
    pg.rect(x, y, w, h, LAV["50"])
    pts = [(i, d[key]) for i, d in enumerate(series) if d.get(key) is not None]
    if len(pts) < 1:
        pg.text(x + w / 2, y + h / 2, "No follower history yet - it is recorded once a day from today.", 8, False, LAV["muted"], "center")
        return
    lo_v, hi_v = min(v for _, v in pts), max(v for _, v in pts)
    if hi_v == lo_v:
        lo_v, hi_v = lo_v - 1, hi_v + 1
    n = max(1, len(series) - 1)

    def px(i, v):
        return x + 6 + (w - 12) * i / n, y + h - 14 - (h - 26) * (v - lo_v) / (hi_v - lo_v)
    for (i1, v1), (i2, v2) in zip(pts, pts[1:]):
        (ax, ay), (bx, by) = px(i1, v1), px(i2, v2)
        pg.line(ax, ay, bx, by, LAV["600"], 1.6)
    for i, v in pts[-1:]:
        cx, cy = px(i, v)
        pg.rect(cx - 2, cy - 2, 4, 4, LAV["700"])
    step = max(1, len(series) // 7)
    for i in range(0, len(series), step):
        pg.text(px(i, lo_v)[0], y + h - 3, series[i]["date"][5:], 6, color=LAV["muted"], align="center")
    pg.text(x + w, y - 6, f"{_fmt(lo_v)} - {_fmt(hi_v)}", 6.5, color=LAV["muted"], align="right")


def _table(pg, y, cols, rows, new_page, M=36, W=595, H=842):
    """cols: [(title, width, align)], rows: list of lists of strings. Flows onto new pages with a repeated header."""
    def head(p, ty):
        p.rect(M, ty, W - 2 * M, 18, LAV["100"])
        tx = M + 6
        for name, w, al in cols:
            p.text(tx + (w - 8 if al == "right" else 0), ty + 12, name, 7.5, True, LAV["700"], al)
            tx += w
        return ty + 18
    y = head(pg, y)
    for n, row in enumerate(rows):
        if y + 18 > H - 40:
            pg, y = new_page()
            y = head(pg, y)
        if n % 2:
            pg.rect(M, y, W - 2 * M, 16, LAV["50"])
        tx = M + 6
        for (_, w, al), v in zip(cols, row):
            pg.text(tx + (w - 8 if al == "right" else 0), y + 11, fit(str(v), 7.5, w - 8), 7.5, False, LAV["ink"], al)
            tx += w
        y += 16
    if not rows:
        pg.text(M + 6, y + 13, "Nothing in this period.", 8.5, False, LAV["muted"])
        y += 18
    return pg, y


def _er(v):
    return f"{v}%" if v is not None else "-"


def _analytics_sections(pdf, pg, y, data, sec, new_page, M=36, W=595, H=842):
    def need(space):
        nonlocal pg, y
        if y + space > H - 50:
            pg, y = new_page()

    if "audience" in sec and data.get("audience"):
        a = data["audience"]
        need(200)
        y += 18
        pg.text(M, y, "Audience growth", 13, True, LAV["ink"])
        net = a.get("net")
        lbl = "no history yet" if net is None else (f"+{net:,}" if net >= 0 else f"{net:,}") + " followers this period"
        pg.text(W - M, y, lbl + ("" if a.get("previous_net") is None else f" (previous: {a['previous_net']:+,})"), 8.5, True,
                LAV["green"] if (net or 0) >= 0 else LAV["red"], "right")
        y += 20
        _line(pg, M, y, W - 2 * M, 110, a["series"], "followers", f"Followers over time - {_fmt(a.get('followers'))} now")
        y += 124
        rows = [[f"@{c['username']}", c["provider"], _fmt(c["start"]), _fmt(c["end"]),
                 "-" if c["net"] is None else f"{c['net']:+,}", "-" if c["growth_pct"] is None else f"{c['growth_pct']:+.2f}%"]
                for c in a["channels"]]
        pg, y = _table(pg, y, [("Channel", 150, "left"), ("Network", 90, "left"), ("Start", 70, "right"), ("End", 70, "right"),
                               ("Net", 70, "right"), ("Growth", 73, "right")], rows, new_page)

    if "reels" in sec and data.get("reels") and data["reels"]["count"]:
        r = data["reels"]
        need(150)
        y += 24
        pg.text(M, y, "Reels & video", 13, True, LAV["ink"])
        y += 12
        tiles = [("Reels", _fmt(r["count"]), r["previous"]["count"]), ("Views", _fmt(r["views"]), r["previous"]["views"]),
                 ("Avg watch time", f"{r['avg_watch_time']}s" if r["avg_watch_time"] is not None else "-", r["previous"]["avg_watch_time"]),
                 ("Total watch time", f"{_fmt(round(r['watch_time'] / 60))} min" if r["watch_time"] else "-", None),
                 ("Skip rate (3s)", _er(r["skip_rate"]), r["previous"]["skip_rate"]), ("Avg engagement", _er(r["engagement_rate"]), None)]
        cw = (W - 2 * M - 5 * 8) / 6
        for i, (label, val, prev) in enumerate(tiles):
            cx = M + i * (cw + 8)
            pg.rect(cx, y, cw, 50, LAV["50"], LAV["200"], 0.6)
            pg.text(cx + 8, y + 14, label.upper(), 6, True, LAV["muted"])
            pg.text(cx + 8, y + 33, val, 12, True, LAV["ink"])
            if prev is not None:
                pg.text(cx + 8, y + 44, f"prev {_fmt(prev) if not isinstance(prev, float) else prev}", 6.5, False, LAV["muted"])
        y += 62
        rows = [[(t["published_at"] or "")[:10], fit(t["caption"] or "(no caption)", 7.5, 220), _fmt(t["insights"].get("views")),
                 f"{t['insights']['avg_watch_time']}s" if t["insights"].get("avg_watch_time") is not None else "-",
                 _er(t["insights"].get("engagement_rate"))] for t in r["top"][:5]]
        pg, y = _table(pg, y, [("Published", 70, "left"), ("Reel", 250, "left"), ("Views", 70, "right"), ("Avg watch", 70, "right"),
                               ("Eng.", 63, "right")], rows, new_page)

    if "types" in sec and data.get("types"):
        need(90)
        y += 24
        pg.text(M, y, "Post types", 13, True, LAV["ink"])
        y += 10
        rows = [[t["format"], t["posts"], _fmt(t["avg_reach"]), _fmt(t["avg_views"]), _fmt(t["avg_likes"]), _er(t["engagement_rate"])]
                for t in data["types"]]
        pg, y = _table(pg, y, [("Format", 130, "left"), ("Posts", 70, "right"), ("Avg reach", 85, "right"), ("Avg views", 85, "right"),
                               ("Avg likes", 80, "right"), ("Eng.", 73, "right")], rows, new_page)

    if "best_times" in sec and data.get("best_times"):
        bt = data["best_times"]
        need(190)
        y += 24
        pg.text(M, y, "Best time to post (your own results)", 13, True, LAV["ink"])
        if bt["top"]:
            pg.text(W - M, y, "Best: " + ", ".join(f"{t['day']} {t['time']}" for t in bt["top"][:3]), 8.5, True, LAV["700"], "right")
        y += 12
        grid = bt["heatmap"]["grid"]
        scores = [c["score"] for row in grid for c in row if c]
        top_s = max(scores) if scores else 1
        lab_w, cell = 26, (W - 2 * M - 26) / 24
        for h in range(0, 24, 3):
            pg.text(M + lab_w + h * cell + cell / 2, y + 8, f"{h:02d}", 6, False, LAV["muted"], "center")
        y += 12
        for d in range(7):
            pg.text(M, y + 10, an.DAYS[d], 7, True, LAV["muted"])
            for h in range(24):
                c = grid[d][h]
                col = _mix(LAV["50"], LAV["700"], (c["score"] / top_s) if c and top_s else 0) if c else "#ffffff"
                pg.rect(M + lab_w + h * cell + 0.5, y + 0.5, cell - 1, 13, col, LAV["100"], 0.3)
            y += 14
        y += 4
        pg.text(M, y + 8, f"From {bt['heatmap']['posts']} sent posts; darker = higher median engagement. Hours are each channel's local time.",
                7, False, LAV["muted"])
        y += 14

    if "hashtags" in sec and data.get("hashtags"):
        hs = data["hashtags"]
        need(90)
        y += 24
        pg.text(M, y, "Hashtag performance", 13, True, LAV["ink"])
        y += 10
        rows = [[h["hashtag"], h["posts"], _fmt(h["avg_reach"]), _fmt(h["avg_views"]), _er(h["engagement_rate"])] for h in hs["hashtags"][:12]]
        pg, y = _table(pg, y, [("Hashtag", 190, "left"), ("Posts", 70, "right"), ("Avg reach", 90, "right"), ("Avg views", 90, "right"),
                               ("Eng.", 83, "right")], rows, new_page)
        if hs["groups"]:
            need(60)
            y += 10
            rows = [[g["name"], g["posts"], _fmt(g["avg_reach"]), _er(g["engagement_rate"]),
                     "-" if g["vs_others_reach_pct"] is None else f"{g['vs_others_reach_pct']:+.1f}%"] for g in hs["groups"]]
            pg, y = _table(pg, y, [("Hashtag group", 190, "left"), ("Posts", 70, "right"), ("Avg reach", 90, "right"),
                                   ("Eng.", 90, "right"), ("Reach vs others", 83, "right")], rows, new_page)

    if "campaigns" in sec and data.get("campaigns") and (data["campaigns"]["campaigns"] or data["campaigns"]["tags"]):
        cp = data["campaigns"]
        need(90)
        y += 24
        pg.text(M, y, "Campaigns & tags", 13, True, LAV["ink"])
        y += 10
        rows = [[c["campaign"], "campaign", c["posts"], _fmt(c["reach"]), _fmt(c["views"]), _er(c["engagement_rate"])] for c in cp["campaigns"]]
        rows += [[t["tag"], "tag", t["posts"], _fmt(t["reach"]), _fmt(t["views"]), _er(t["engagement_rate"])] for t in cp["tags"][:10]]
        pg, y = _table(pg, y, [("Name", 170, "left"), ("Kind", 70, "left"), ("Posts", 60, "right"), ("Reach", 80, "right"),
                               ("Views", 70, "right"), ("Eng.", 73, "right")], rows, new_page)
    return pg, y


def render_pdf(data: dict) -> bytes:
    lo, hi = _parse_iso(data["range"][0]), _parse_iso(data["range"][1])
    plo = _parse_iso(data["previous_range"][0])
    pdf = PDF(title=f"{data['brand']} social report {lo:%d %b} - {hi:%d %b %Y}")
    pdf.add_png("logo", LOGO)
    W, H, M = 595, 842, 36
    pages = []

    def new_page():
        p = pdf.page()
        pages.append(p)
        p.rect(0, 0, W, 60, LAV["100"])
        p.rect(0, 56, W, 4, LAV["400"])
        lh = 92 * (pdf.images["logo"]["h"] / pdf.images["logo"]["w"]) if "logo" in pdf.images else 24
        _logo(pdf, p, M, (56 - lh) / 2, 92)
        p.text(W - M, 34, f"{data['brand']} social report - {lo:%d %b} - {hi:%d %b %Y}", 9, True, LAV["700"], "right")
        return p, 92

    # --- page 1: cover band, KPI grid, daily charts
    pg = pdf.page()
    pages.append(pg)
    pg.rect(0, 0, W, 150, LAV["100"])
    pg.rect(0, 146, W, 4, LAV["400"])
    _logo(pdf, pg, M, 24, 140)
    pg.text(M, 98, f"{data['brand']} social report", 24, True, LAV["ink"])
    pg.text(M, 118, f"{lo:%d %b %Y} - {hi:%d %b %Y}", 11, False, LAV["700"])
    pg.text(M, 134, f"Compared with the previous {data['days']} days ({plo:%d %b} - {lo:%d %b %Y})", 8.5, False, LAV["muted"])
    nch = len(data["channels"])
    pg.text(W - M, 118, f"{nch} channel{'s' if nch != 1 else ''}", 9, True, LAV["700"], "right")

    sec = set(data.get("sections") or an.SECTIONS)
    flt = ", ".join(f"{k.replace('_id', '')}: {v}" for k, v in (data.get("filters") or {}).items() if v and k != "channel_id")
    if flt:
        pg.text(W - M, 134, fit("Filtered - " + flt, 8, 220), 8, False, LAV["muted"], "right")
    y = 176
    if "overview" not in sec:
        y = 164
    else:
        pg.text(M, y, "Performance overview", 13, True, LAV["ink"])
    if "overview" in sec:
        y += 12
        gap = 10
        cw, ch = (W - 2 * M - 3 * gap) / 4, 62
        for i, k in enumerate(data["kpis"]):
            cx, cy = M + (i % 4) * (cw + gap), y + (i // 4) * (ch + gap)
            pg.rect(cx, cy, cw, ch, LAV["50"], LAV["200"], 0.6)
            pg.rect(cx, cy, 3, ch, LAV["500"])
            pg.text(cx + 12, cy + 16, METRIC_LABELS.get(k["metric"], k["metric"]).upper(), 7, True, LAV["muted"])
            er = k["metric"] == "engagement_rate"
            cur, pv = k["current"], k["previous"]
            pg.text(cx + 12, cy + 38, (f"{cur}%" if cur is not None else "-") if er else _fmt(cur), 17, True, LAV["ink"])
            label, color = _chg(k["change_pct"])
            pg.text(cx + 12, cy + 53, label, 7.5, True, color)
            pg.text(cx + cw - 8, cy + 53, "prev " + ((f"{pv}%" if pv is not None else "-") if er else _fmt(pv)), 7, False, LAV["muted"], "right")
        y += 2 * (ch + gap) + 24
        _bars(pg, M, y, W - 2 * M, 120, data["series"], data["previous_series"], "posts",
              "Posts per day (dark = this period, light = previous period)")
        y += 150
        _bars(pg, M, y, W - 2 * M, 120, data["series"], data["previous_series"], "views", "Views per day")
        y += 152

    # --- per-channel cards, two per row
    if "channels" in sec:
        if y > H - 230:
            pg, y = new_page()
        pg.text(M, y, "By channel", 13, True, LAV["ink"])
        y += 12
        card_w, card_h = (W - 2 * M - 12) / 2, 176
        for i, c in enumerate(data["channels"]):
            col = i % 2
            if col == 0 and y + card_h > H - 50:
                pg, y = new_page()
            cx = M + col * (card_w + 12)
            pg.rect(cx, y, card_w, card_h, "#ffffff", LAV["200"], 0.6)
            pg.rect(cx, y, card_w, 26, LAV["100"])
            pg.text(cx + 10, y + 17, fit(f"@{c['username']}", 10, card_w - 130, True), 10, True, LAV["ink"])
            sub = c["provider"] + (f" - {_fmt(c['followers'])} followers" if c["followers"] is not None else "")
            pg.text(cx + card_w - 10, y + 17, sub, 7.5, False, LAV["700"], "right")
            m = c["metrics"]
            for j, key in enumerate(("posts", "views", "likes", "comments")):
                mx = cx + 10 + j * ((card_w - 20) / 4)
                pg.text(mx, y + 42, METRIC_LABELS[key].upper(), 6.5, True, LAV["muted"])
                pg.text(mx, y + 57, _fmt(m[key]["current"]), 12, True, LAV["ink"])
                label, color = _chg(m[key]["change_pct"])
                pg.text(mx, y + 68, label if m[key]["change_pct"] is not None else "-", 6.5, True, color)
            key = "views" if any(d["views"] for d in c["series"]) else "posts"
            _bars(pg, cx + 10, y + 90, card_w - 20, 76, c["series"], [], key, f"{METRIC_LABELS[key]} per day")
            if col == 1 or i == len(data["channels"]) - 1:
                y += card_h + 12
        if not data["channels"]:
            pg.text(M, y + 10, "No channels connected yet.", 9, False, LAV["muted"])
            y += 24

    # --- top posts table
    if "posts" in sec:
        if y > H - 170:
            pg, y = new_page()
        y += 14
        pg.text(M, y, "Top posts", 13, True, LAV["ink"])
        y += 10
        cols = [("Published", 62, "left"), ("Channel", 92, "left"), ("Post", 211, "left"), ("Views", 48, "right"),
                ("Likes", 42, "right"), ("Comm.", 34, "right"), ("Eng.", 34, "right")]

        def table_head(p, ty):
            p.rect(M, ty, W - 2 * M, 18, LAV["100"])
            tx = M + 6
            for name, w, al in cols:
                p.text(tx + (w - 8 if al == "right" else 0), ty + 12, name, 7.5, True, LAV["700"], al)
                tx += w
            return ty + 18

        y = table_head(pg, y)
        for n, r in enumerate(data["top_posts"][:10]):
            if y + 30 > H - 40:
                pg, y = new_page()
                y = table_head(pg, y)
            i = r["insights"] or {}
            if n % 2:
                pg.rect(M, y, W - 2 * M, 28, LAV["50"])
            x = M + 6
            pg.text(x, y + 12, (r["published_at"] or "")[:10], 7.5, color=LAV["ink"])
            x += cols[0][1]
            pg.text(x, y + 12, fit(f"{r['channel']['provider']} @{r['channel']['username']}", 7.5, cols[1][1] - 8), 7.5, color=LAV["ink"])
            x += cols[1][1]
            for li, ln in enumerate(wrap(r["caption"] or "(no caption)", 7.5, cols[2][1] - 8, max_lines=2)):
                pg.text(x, y + 12 + li * 10, ln, 7.5, color=LAV["ink"])
            x += cols[2][1]
            er = i.get("engagement_rate")
            for (_, w, _), v in zip(cols[3:], (_fmt(i.get("views")), _fmt(i.get("likes")), _fmt(i.get("comments")),
                                               f"{er}%" if er is not None else "-")):
                pg.text(x + w - 8, y + 12, v, 7.5, False, LAV["ink"], "right")
                x += w
            y += 28
        if not data["top_posts"]:
            pg.text(M + 6, y + 14, "No sent posts in this period.", 8.5, False, LAV["muted"])

    pg, y = _analytics_sections(pdf, pg, y, data, sec, new_page)

    total = len(pages)
    for n, p in enumerate(pages, 1):
        p.line(M, H - 30, W - M, H - 30, LAV["200"], 0.6)
        p.text(M, H - 18, f"Generated by HuhQueue on {data['generated_at'][:16].replace('T', ' ')} UTC", 7, False, LAV["muted"])
        p.text(W - M, H - 18, f"Page {n} of {total}", 7, False, LAV["muted"], "right")
    return pdf.render()


def filename(data: dict) -> str:
    lo, hi = _parse_iso(data["range"][0]), _parse_iso(data["range"][1])
    slug = re.sub(r"[^a-z0-9]+", "-", data["brand"].lower()).strip("-") or "report"
    return f"{slug}-report-{lo:%Y%m%d}-{hi:%Y%m%d}.pdf"


# ------------------------------------------------------------------------------------------------- archive
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9\-]*\.pdf$")


def archive_dir(core) -> Path:
    d = Path(core.settings.data_dir) / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_to_archive(core, data: dict, pdf: bytes) -> str:
    stamp = datetime.utcnow().strftime("%H%M%S%f")[:9]
    path = archive_dir(core) / filename(data).replace(".pdf", f"-{stamp}.pdf")
    path.write_bytes(pdf)
    return path.name


def list_archive(core) -> list[dict]:
    out = []
    for p in sorted(archive_dir(core).glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True)[:100]:
        st = p.stat()
        out.append({"name": p.name, "size": st.st_size, "created_at": posts_svc.iso(datetime.utcfromtimestamp(st.st_mtime)),
                    "url": f"/api/reports/archive/{p.name}"})
    return out


def archive_path(core, name: str) -> Path | None:
    if not SAFE_NAME.match(name or ""):
        return None
    p = archive_dir(core) / name
    return p if p.is_file() else None


# ------------------------------------------------------------------------------------------------- email
def smtp_config(core) -> dict:
    def g(attr, env, default=""):
        return getattr(core.settings, attr, "") or os.environ.get(env, default)
    return {"host": g("smtp_host", "HUHQ_SMTP_HOST"), "port": int(g("smtp_port", "HUHQ_SMTP_PORT", "587") or 587),
            "user": g("smtp_user", "HUHQ_SMTP_USER"), "password": g("smtp_password", "HUHQ_SMTP_PASSWORD"),
            "from": g("smtp_from", "HUHQ_SMTP_FROM") or g("smtp_user", "HUHQ_SMTP_USER"),
            "tls": (g("smtp_tls", "HUHQ_SMTP_TLS", "starttls") or "starttls").lower()}


def email_configured(core) -> bool:
    c = smtp_config(core)
    return bool(c["host"] and c["from"])


def send_email(core, to: list[str], subject: str, body: str, attachment: bytes, name: str) -> None:
    c = smtp_config(core)
    if not (c["host"] and c["from"]):
        raise RuntimeError("Email is not set up - add HUHQ_SMTP_HOST and HUHQ_SMTP_FROM to .env")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, c["from"], ", ".join(to)
    msg.set_content(body)
    msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=name)
    ctx = ssl.create_default_context()
    server = smtplib.SMTP_SSL(c["host"], c["port"], context=ctx, timeout=30) if c["tls"] == "ssl" \
        else smtplib.SMTP(c["host"], c["port"], timeout=30)
    with server:
        if c["tls"] == "starttls":
            server.starttls(context=ctx)
        if c["user"]:
            server.login(c["user"], c["password"])
        server.send_message(msg)


# ------------------------------------------------------------------------------------------------- weekly schedule
def get_schedule(core) -> dict:
    with core.db.session() as s:
        row = s.get(KV, SCHEDULE_KEY)
        sch = {**DEFAULT_SCHEDULE, **(row.value if row else {})}
    sch["timezone"] = sch.get("timezone") or core.prefs().get("timezone") or "Europe/Dublin"
    sch["email_configured"] = email_configured(core)
    return sch


def next_run(sch: dict, now: datetime) -> datetime:
    """Next weekday/time in the schedule's timezone, as naive UTC, strictly after now (naive UTC)."""
    zone = ZoneInfo(sch.get("timezone") or "Europe/Dublin")
    hh, mm = (int(x) for x in str(sch.get("time") or "09:00").split(":")[:2])
    local_now = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    for add in range(0, 8):
        d = (local_now + timedelta(days=add)).date()
        if d.weekday() != int(sch.get("weekday") or 0):
            continue
        cand = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone)
        if cand > local_now:
            return cand.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    d = (local_now + timedelta(days=7)).date()  # pragma: no cover - the loop always finds one
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def set_schedule(core, changes: dict, now: datetime | None = None) -> dict:
    now = now or datetime.utcnow()
    cur = get_schedule(core)
    sch = {k: cur.get(k) for k in DEFAULT_SCHEDULE}
    if "enabled" in changes:
        sch["enabled"] = bool(changes["enabled"])
    if "weekday" in changes:
        wd = int(changes["weekday"])
        if not 0 <= wd <= 6:
            raise ValueError("weekday is 0 (Monday) to 6 (Sunday)")
        sch["weekday"] = wd
    if "time" in changes:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(changes["time"])):
            raise ValueError("time must be HH:MM (24h)")
        sch["time"] = changes["time"]
    if "days" in changes:
        if str(changes["days"]) not in ("7", "14", "28", "30", "mtd"):
            raise ValueError("days must be 7, 14, 28, 30 or mtd")
        sch["days"] = str(changes["days"])
    if "channel_id" in changes:
        sch["channel_id"] = str(changes["channel_id"] or "")
    for k in ("tag", "campaign", "format"):
        if k in changes:
            sch[k] = str(changes[k] or "")[:200]
    if "sections" in changes:
        sch["sections"] = an.parse_sections(changes["sections"]) if changes["sections"] else []
    if changes.get("timezone"):
        try:
            ZoneInfo(changes["timezone"])
        except Exception as e:
            raise ValueError(f"Unknown timezone {changes['timezone']}") from e
        sch["timezone"] = changes["timezone"]
    if "email_to" in changes:
        raw = changes["email_to"]
        items = raw if isinstance(raw, list) else re.split(r"[,;\s]+", str(raw or ""))
        items = [x.strip() for x in items if x and x.strip()]
        bad = [x for x in items if not EMAIL.match(x)]
        if bad:
            raise ValueError(f"Not an email address: {', '.join(bad)}")
        sch["email_to"] = items[:20]
    sch["next_run_at"] = posts_svc.iso(next_run(sch, now)) if sch["enabled"] else None
    with core.db.session() as s:
        row = s.get(KV, SCHEDULE_KEY)
        if not row:
            row = KV(key=SCHEDULE_KEY, value={})
            s.add(row)
        row.value = sch
    return get_schedule(core)


def _save_state(core, **changes):
    with core.db.session() as s:
        row = s.get(KV, SCHEDULE_KEY)
        if not row:
            row = KV(key=SCHEDULE_KEY, value=dict(DEFAULT_SCHEDULE))
            s.add(row)
        row.value = {**(row.value or {}), **changes}


def generate(core, days="7", start=None, end=None, channel_id=None, email_to: list | None = None, tag=None, campaign=None,
             fmt=None, sections=None) -> dict:
    """Build the PDF, keep it in the archive, and email it when recipients are given and SMTP is set up."""
    data = report_data(core, days, start, end, channel_id or None, tag or None, campaign or None, fmt or None, sections or None)
    pdf = render_pdf(data)
    name = save_to_archive(core, data, pdf)
    out = {"file": name, "url": f"/api/reports/archive/{name}", "size": len(pdf), "range": data["range"], "emailed": [], "email_error": ""}
    if email_to:
        try:
            lo, hi = _parse_iso(data["range"][0]), _parse_iso(data["range"][1])
            send_email(core, email_to, f"{data['brand']} social report {lo:%d %b} - {hi:%d %b %Y}",
                       f"Your {data['brand']} report for {lo:%d %b} - {hi:%d %b %Y} is attached.\n\n- HuhQueue", pdf, filename(data))
            out["emailed"] = list(email_to)
        except Exception as e:     # keep the archived copy even if mail fails
            out["email_error"] = str(e)[:300]
            log.warning("report email failed: %s", e.__class__.__name__)
    return out


def run_due(core, now: datetime | None = None) -> int:
    """Called by the worker every tick: generate the weekly report when it is due."""
    now = now or datetime.utcnow()
    sch = get_schedule(core)
    if not sch.get("enabled"):
        return 0
    due = _parse_iso(sch.get("next_run_at")) or next_run(sch, now - timedelta(seconds=1))
    if due > now:
        return 0
    try:
        res = generate(core, sch.get("days") or "7", channel_id=sch.get("channel_id") or None, email_to=sch.get("email_to") or [],
                       tag=sch.get("tag"), campaign=sch.get("campaign"), fmt=sch.get("format"), sections=sch.get("sections"))
        status = "emailed" if res["emailed"] else (f"saved (email failed: {res['email_error']})" if res["email_error"] else "saved")
        _save_state(core, last_run_at=posts_svc.iso(now), last_status=f"{status}: {res['file']}",
                    next_run_at=posts_svc.iso(next_run(sch, now)))
        from .events import record
        with core.db.session() as s:
            record(core, s, f"Weekly report ready: {res['file']}", level="success", kind="report", notify=True)
        return 1
    except Exception as e:
        log.exception("scheduled report failed")
        _save_state(core, last_run_at=posts_svc.iso(now), last_status=f"failed: {e}"[:300], next_run_at=posts_svc.iso(next_run(sch, now)))
        return 0
