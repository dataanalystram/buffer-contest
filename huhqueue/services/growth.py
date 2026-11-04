"""Growth: campaigns, UTM tagging, self-hosted short links, link-in-bio pages, automations (evergreen recycling,
recurring posts, RSS auto-post) and CSV bulk import.

Everything here is plain functions over a Core + SQLAlchemy session; the REST routes and the action-registry
entries live in huhqueue/api/growth.py and the background jobs run from the existing Worker tick.
"""
from __future__ import annotations

import calendar
import csv
import hashlib
import html
import io
import re
import secrets
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from ..models import KV, Automation, Campaign, Channel, Link, LinkClick, Media, Post, PostTarget
from . import posts as posts_svc
from .events import record

utcnow = posts_svc.utcnow
iso = posts_svc.iso


class GrowthError(posts_svc.PostError):
    """Bad input; rendered by the PostError handler as {"detail": {"message", "issues"}}."""

    def __init__(self, message: str, status: int = 400, issues: dict | None = None):
        super().__init__(status, message, issues)


HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CODE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
URL_RE = re.compile(r"https?://[^\s<>\"']+")
FULL_URL = re.compile(r"^https?://[^\s/]+\.[^\s]+")
RESERVED_CODES = {"api", "static", "bio", "files", "m", "oauth"}
PALETTE = ["#7C5CE0", "#C9ACFA", "#F59E0B", "#10B981", "#EC4899", "#3B82F6", "#EF4444", "#14B8A6"]


def base_url(core, request=None) -> str:
    """Base for links people click (short links, bio pages): public URL first, then the UI's own URL."""
    if core.public_base_url():
        return core.public_base_url()
    if core.settings.ui_base_url:
        return core.settings.ui_base_url
    if request is not None:
        return str(request.base_url).rstrip("/")
    return core.settings.local_base_url


def score(ins: dict) -> float:
    """One engagement number for ranking posts (engagement rate when the network gives it)."""
    ins = ins or {}
    if ins.get("engagement_rate") is not None:
        return float(ins["engagement_rate"]) * 100
    return float((ins.get("likes") or 0) + 2 * (ins.get("comments") or 0) + 3 * (ins.get("shares") or 0)
                 + 2 * (ins.get("saved") or 0) + (ins.get("views") or 0) / 100)


# ============================================================================================ campaigns
def _check_day(v, field) -> str:
    v = (v or "").strip()[:10]
    if v:
        if not DAY.match(v):
            raise GrowthError(f"{field} must be YYYY-MM-DD")
        try:
            date.fromisoformat(v)
        except ValueError as e:
            raise GrowthError(f"{field} is not a real date") from e
    return v


def sync_campaigns(s) -> None:
    """Every campaign name used on a post or link gets a Campaign row (older posts only had the free-text label)."""
    have = set(s.scalars(select(Campaign.name)))
    names = set(s.scalars(select(Post.campaign).where(Post.campaign != "").distinct()))
    names |= set(s.scalars(select(Link.campaign).where(Link.campaign != "").distinct()))
    for i, n in enumerate(sorted(names - have)):
        s.add(Campaign(name=n, color=PALETTE[(len(have) + i) % len(PALETTE)]))
    s.flush()


def _visible_links(s, qry, channels: set | None):
    """Links the caller may see: every link for an unrestricted caller (channels None). A channel-limited caller sees a
    link made for a post target / post only when that target / post is on one of its channels, plus links tied to no
    post (manual links), so another channel's UTM links and clicks don't leak into campaign totals (4 Nov 2026)."""
    links = list(qry) if isinstance(qry, list) else list(s.scalars(qry))
    if channels is None:
        return links
    tids = {x for ln in links for x in [ln.target_id] if x}
    pids = {x for ln in links for x in [ln.post_id] if x}
    t_ch = dict(s.execute(select(PostTarget.id, PostTarget.channel_id).where(PostTarget.id.in_(tids))).all()) if tids else {}
    p_ok = set(s.scalars(select(PostTarget.post_id).where(PostTarget.post_id.in_(pids),
                                                          PostTarget.channel_id.in_(sorted(channels))))) if pids else set()
    keep = []
    for ln in links:
        if ln.target_id:
            ok = t_ch.get(ln.target_id) in channels
        elif ln.post_id:
            ok = ln.post_id in p_ok
        else:
            ok = True
        if ok:
            keep.append(ln)
    return keep


def campaign_stats(s, name: str, channels: set | None = None) -> dict:
    """Totals for one campaign, counting only targets on ``channels`` (None = every channel)."""
    from ..scope_filter import only
    rows = s.execute(only(select(PostTarget.status, PostTarget.insights, PostTarget.channel_id, PostTarget.post_id)
                          .join(Post, Post.id == PostTarget.post_id).where(Post.campaign == name),
                          PostTarget.channel_id, channels)).all()
    by_status: dict[str, int] = {}
    totals = {"views": 0, "likes": 0, "comments": 0, "shares": 0, "saved": 0}
    seen_channels: set = set()          # kept apart from `channels`, the caller's filter (None = owner)
    for st, ins, cid, _pid in rows:
        by_status[st] = by_status.get(st, 0) + 1
        seen_channels.add(cid)
        if st == "sent":
            for k in totals:
                totals[k] += int((ins or {}).get(k) or 0)
    if channels is None:
        clicks = s.scalar(select(func.coalesce(func.sum(Link.clicks), 0)).where(Link.campaign == name)) or 0
        posts = s.scalar(select(func.count(Post.id)).where(Post.campaign == name)) or 0
    else:   # a channel-limited caller: posts with a target it can see, clicks on links it can see
        clicks = sum(int(ln.clicks or 0) for ln in _visible_links(s, select(Link).where(Link.campaign == name), channels))
        posts = len({r[3] for r in rows})
    return {"posts": posts, "targets": len(rows), "by_status": by_status, "sent": by_status.get("sent", 0),
            "scheduled": by_status.get("scheduled", 0), "drafts": by_status.get("draft", 0), "channels": len(seen_channels),
            "clicks": int(clicks), **totals,
            "engagements": totals["likes"] + totals["comments"] + totals["shares"] + totals["saved"]}


def campaign_dict(s, c: Campaign, channels: set | None = None) -> dict:
    d = {"id": c.id, "name": c.name, "color": c.color, "description": c.description, "start_date": c.start_date,
         "end_date": c.end_date, "created_at": iso(c.created_at), "stats": campaign_stats(s, c.name, channels)}
    d["posts"] = d["stats"]["posts"]          # back-compat with the old /api/campaigns shape [{name, posts}]
    today = date.today().isoformat()
    d["state"] = ("upcoming" if c.start_date and today < c.start_date else
                  "ended" if c.end_date and today > c.end_date else "active")
    return d


def list_campaigns(core, channels: set | None = None) -> list[dict]:
    """Every campaign, its stats counting only ``channels`` (None = every channel)."""
    with core.db.session() as s:
        sync_campaigns(s)
        return [campaign_dict(s, c, channels) for c in s.scalars(select(Campaign).order_by(Campaign.name))]


def _campaign(s, key: str) -> Campaign:
    c = s.get(Campaign, key) or s.scalar(select(Campaign).where(Campaign.name == key))
    if not c:
        raise GrowthError("Campaign not found", 404)
    return c


def save_campaign(core, payload: dict, cid: str | None = None) -> dict:
    with core.db.session() as s:
        sync_campaigns(s)
        c = _campaign(s, cid) if cid else Campaign()
        if "name" in payload or not cid:
            name = str(payload.get("name") or "").strip()[:80]
            if not name:
                raise GrowthError("Give the campaign a name")
            other = s.scalar(select(Campaign).where(Campaign.name == name))
            if other and other.id != c.id:
                raise GrowthError(f"A campaign called '{name}' already exists", 409)
            if cid and name != c.name:   # rename: move every post and link to the new name
                for p in s.scalars(select(Post).where(Post.campaign == c.name)):
                    p.campaign = name
                for ln in s.scalars(select(Link).where(Link.campaign == c.name)):
                    ln.campaign = name
            c.name = name
        if "color" in payload:
            col = str(payload.get("color") or "").strip()
            if not HEX.match(col):
                raise GrowthError("Colour must look like #7C5CE0")
            c.color = col
        elif not cid:
            c.color = PALETTE[(s.scalar(select(func.count(Campaign.id))) or 0) % len(PALETTE)]
        if "description" in payload:
            c.description = str(payload.get("description") or "")[:2000]
        if "start_date" in payload:
            c.start_date = _check_day(payload.get("start_date"), "start_date")
        if "end_date" in payload:
            c.end_date = _check_day(payload.get("end_date"), "end_date")
        if c.start_date and c.end_date and c.end_date < c.start_date:
            raise GrowthError("The end date is before the start date")
        s.add(c)
        s.flush()
        return campaign_dict(s, c)


def get_campaign(core, cid: str, request=None, channels: set | None = None) -> dict:
    """One campaign with its posts, per-channel rows and links, limited to ``channels`` (None = every channel)."""
    from ..scope_filter import only
    with core.db.session() as s:
        sync_campaigns(s)
        c = _campaign(s, cid)
        out = campaign_dict(s, c, channels)
        qry = (only(select(PostTarget).join(Post, Post.id == PostTarget.post_id).where(Post.campaign == c.name),
                    PostTarget.channel_id, channels)
               .order_by(func.coalesce(PostTarget.published_at, PostTarget.scheduled_at, PostTarget.created_at)))
        out["targets"] = [posts_svc.serialize_target(core, t) for t in s.scalars(qry)]
        by_ch: dict[str, dict] = {}
        for t in out["targets"]:
            row = by_ch.setdefault(t["channel_id"], {"channel": t["channel"], "posts": 0, "sent": 0, "views": 0, "likes": 0})
            row["posts"] += 1
            if t["status"] == "sent":
                row["sent"] += 1
                row["views"] += int(t["insights"].get("views") or 0)
                row["likes"] += int(t["insights"].get("likes") or 0)
        out["channels"] = list(by_ch.values())
        out["links"] = [link_dict(core, ln, request) for ln in
                        _visible_links(s, select(Link).where(Link.campaign == c.name).order_by(Link.created_at.desc()), channels)]
        return out


def delete_campaign(core, cid: str) -> dict:
    with core.db.session() as s:
        c = _campaign(s, cid)
        n = 0
        for p in s.scalars(select(Post).where(Post.campaign == c.name)):
            p.campaign = ""
            n += 1
        for ln in s.scalars(select(Link).where(Link.campaign == c.name)):
            ln.campaign = ""
        s.delete(c)
        return {"deleted": c.id, "posts_untagged": n}


# ============================================================================================ UTM
UTM_KEYS = ("source", "medium", "campaign", "term", "content")
DEFAULT_UTM = {"source": "{provider}", "medium": "social", "campaign": "{campaign}", "term": "", "content": ""}


def utm_defaults(core) -> dict:
    with core.db.session() as s:
        row = s.get(KV, "utm_defaults")
        stored = dict(row.value) if row else {}
    return {"*": {**DEFAULT_UTM, "auto": False, "shorten": False, **(stored.get("*") or {})},
            **{k: v for k, v in stored.items() if k != "*"}}


def set_utm_defaults(core, channel_id: str, values: dict) -> dict:
    """Save UTM defaults for one channel ('*' = every channel). On '*', `auto` tags every post's links at publish
    time without opting in per post, and `shorten` also swaps them for tracked /l/ short links."""
    clean = {k: str(values.get(k) or "").strip()[:120] for k in UTM_KEYS if k in values}
    if channel_id == "*":
        for k in ("auto", "shorten"):
            if k in values:
                clean[k] = bool(values[k])
    with core.db.session() as s:
        if channel_id != "*" and not s.get(Channel, channel_id):
            raise GrowthError("Channel not found", 404)
        row = s.get(KV, "utm_defaults") or KV(key="utm_defaults", value={})
        val = dict(row.value or {})
        if values.get("reset"):
            val.pop(channel_id, None)
        else:
            val[channel_id] = {**(val.get(channel_id) or {}), **clean}
        row.value = val
        s.merge(row)
    return utm_defaults(core)


def utm_for(core, ch: Channel, campaign: str = "", override: dict | None = None, defaults: dict | None = None) -> dict:
    """The utm_* values for one channel: global defaults < channel defaults < this post's overrides."""
    d = defaults or utm_defaults(core)
    merged = {**d["*"], **{k: v for k, v in (d.get(ch.id) or {}).items() if v != ""},
              **{k: v for k, v in (override or {}).items() if k in UTM_KEYS and v not in (None, "")}}
    ctx = {"provider": ch.provider, "username": ch.username, "channel": ch.display_name or ch.username, "campaign": campaign or ""}
    out = {}
    for k in UTM_KEYS:
        v = str(merged.get(k) or "")
        for ph, rep in ctx.items():
            v = v.replace("{" + ph + "}", rep)
        v = v.strip()
        if k != "content":
            v = re.sub(r"\s+", "-", v).lower()
        if v:
            out[k] = v
    return out


def tag_url(url: str, utm: dict) -> str:
    """Add utm_* parameters (never overwrite ones the URL already has); keeps the fragment."""
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    have = {k for k, _ in q}
    q += [(f"utm_{k}", v) for k, v in utm.items() if f"utm_{k}" not in have and v]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def find_urls(text: str) -> list[str]:
    out = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?)]}'\"")
        if u not in out:
            out.append(u)
    return out


def _is_own_short(core, url: str) -> bool:
    u = urlsplit(url)
    return u.path.startswith(("/l/", "/bio/")) and u.netloc == urlsplit(base_url(core)).netloc


def tag_text(core, s, ch: Channel, text: str, utm_opt: dict, campaign: str, target_id: str = "", post_id: str = "",
             defaults: dict | None = None) -> tuple[str, list[dict]]:
    """Tag every link in `text` for this channel; optionally swap it for a tracked short link."""
    if not text or not (utm_opt or {}).get("enabled"):
        return text, []
    params = utm_for(core, ch, campaign or utm_opt.get("campaign") or "", utm_opt, defaults)
    can_short = bool(core.public_base_url() or core.settings.ui_base_url)
    changes = []
    for u in find_urls(text):
        if _is_own_short(core, u):
            continue
        tagged = tag_url(u, params)
        final = tagged
        if utm_opt.get("shorten") and s is not None and can_short:
            ln = s.scalar(select(Link).where(Link.url == tagged, Link.target_id == target_id)) if target_id else None
            if not ln:
                ln = Link(code=new_code(s), url=tagged, campaign=campaign or "", post_id=post_id or "", title=u[:200],
                          source="utm", target_id=target_id or "")
                s.add(ln)
                s.flush()
            final = f"{base_url(core)}/l/{ln.code}"
        text = text.replace(u, final)
        changes.append({"url": u, "tagged": tagged, "final": final})
    return text, changes


def effective_utm(opts: dict, defaults: dict) -> dict:
    """The post's own utm option wins (enabled true/false); otherwise the global 'auto' setting decides."""
    u = (opts or {}).get("utm")
    if isinstance(u, dict) and "enabled" in u:
        return u
    g = defaults.get("*") or {}
    if g.get("auto"):
        return {**(u if isinstance(u, dict) else {}), "enabled": True, "shorten": bool(g.get("shorten"))}
    return {}


def tag_for_publish(core, s, t: PostTarget, caption: str, opts: dict) -> tuple[str, dict]:
    """Worker hook, right before publishing: first-comment automations, then per-channel UTM tags (and short
    links) on the caption + first comment. Works on copies; the stored post is never changed."""
    opts = apply_first_comment_rules(core, s, t, dict(opts or {}))
    d = utm_defaults(core)
    u = effective_utm(opts, d)
    if not u.get("enabled"):
        return caption, opts
    campaign = t.post.campaign if t.post else ""
    caption, _ = tag_text(core, s, t.channel, caption, u, campaign, t.id, t.post_id, d)
    if opts.get("first_comment"):
        opts = {**opts}
        opts["first_comment"], _ = tag_text(core, s, t.channel, opts["first_comment"], u, campaign, t.id, t.post_id, d)
    return caption, opts


def utm_preview(core, payload: dict) -> dict:
    url = (payload.get("url") or "").strip()
    if url and not re.match(r"^https?://", url):
        raise GrowthError("The link must start with http:// or https://")
    urls = [url] if url else find_urls(payload.get("caption") or "")
    utm = payload.get("utm") if isinstance(payload.get("utm"), dict) else {}
    out = {}
    with core.db.session() as s:
        d = utm_defaults(core)
        for ch in posts_svc.resolve_channels(s, payload.get("channel_ids") or []):
            params = utm_for(core, ch, payload.get("campaign") or "", utm, d)
            out[ch.id] = {"provider": ch.provider, "username": ch.username, "params": params,
                          "urls": [{"url": u, "tagged": tag_url(u, params)} for u in urls]}
    return {"channels": out, "urls": urls, "can_shorten": bool(core.public_base_url() or core.settings.ui_base_url)}


# ============================================================================================ short links
ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_code(s, n: int = 6) -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(n))
        if not s.scalar(select(Link.id).where(Link.code == code)):
            return code


def link_dict(core, ln: Link, request=None) -> dict:
    return {"id": ln.id, "code": ln.code, "url": ln.url, "title": ln.title or "", "campaign": ln.campaign or "",
            "post_id": ln.post_id or "", "target_id": ln.target_id or "", "source": ln.source or "manual",
            "clicks": ln.clicks or 0, "short_url": f"{base_url(core, request)}/l/{ln.code}", "created_at": iso(ln.created_at)}


def create_link(core, payload: dict, request=None) -> dict:
    url = (payload.get("url") or "").strip()
    if not FULL_URL.match(url):
        raise GrowthError("Enter a full link that starts with https://")
    if isinstance(payload.get("utm"), dict) and payload["utm"]:
        url = tag_url(url, {k: v for k, v in payload["utm"].items() if k in UTM_KEYS and v})
    with core.db.session() as s:
        code = (payload.get("code") or "").strip()
        if code:
            if not CODE.match(code) or code.lower() in RESERVED_CODES:
                raise GrowthError("Short code: 3-32 letters, numbers, - or _")
            if s.scalar(select(Link.id).where(Link.code == code)):
                raise GrowthError(f"/l/{code} is taken - pick another code", 409)
        ln = Link(code=code or new_code(s), url=url, campaign=(payload.get("campaign") or "").strip()[:80],
                  title=(payload.get("title") or "").strip()[:200], post_id=payload.get("post_id") or "", source="manual")
        s.add(ln)
        s.flush()
        if ln.campaign:
            sync_campaigns(s)
        return link_dict(core, ln, request)


def _link_or_404(s, ln, channels: set | None):
    """The link, or 404 when it doesn't exist OR sits on a channel the caller can't see (same answer: no id probing)."""
    if not ln or (channels is not None and not _visible_links(s, [ln], channels)):
        raise GrowthError("Link not found", 404)
    return ln


LINKS_LIMIT = 1000


def list_links(core, q: str = "", campaign: str = "", source: str = "", request=None, channels: set | None = None) -> list[dict]:
    with core.db.session() as s:
        qry = select(Link).order_by(Link.created_at.desc())
        if campaign:
            qry = qry.where(Link.campaign == campaign)
        if source:
            qry = qry.where(Link.source.in_(source.split(",")))
        if q:
            like = f"%{q}%"
            qry = qry.where(Link.url.ilike(like) | Link.code.ilike(like) | Link.title.ilike(like))
        if channels is not None:  # visibility in SQL, before ORDER BY + LIMIT (Greptile perf on #102): one indexed query
            qry = qry.where(_visible_sql(channels))
        return [link_dict(core, ln, request) for ln in s.scalars(qry.limit(LINKS_LIMIT))]


def _visible_sql(channels: set):
    """The SQL form of _visible_links for a channel-limited caller: a link made for a post target is visible when that
    target is on one of ``channels``; one made for a post (no target) when any of the post's targets is; a link tied to
    neither (a manual link) always. Empty and NULL ids both mean "not tied"."""
    from sqlalchemy import and_, or_
    ch = sorted(channels)
    tid, pid = func.coalesce(Link.target_id, ""), func.coalesce(Link.post_id, "")
    return or_(
        and_(tid != "", Link.target_id.in_(select(PostTarget.id).where(PostTarget.channel_id.in_(ch)))),
        and_(tid == "", pid != "", Link.post_id.in_(select(PostTarget.post_id).where(PostTarget.channel_id.in_(ch)))),
        and_(tid == "", pid == ""),
    )


def get_link(core, lid: str, request=None, days: int = 30, channels: set | None = None) -> dict:
    with core.db.session() as s:
        ln = _link_or_404(s, s.get(Link, lid) or s.scalar(select(Link).where(Link.code == lid)), channels)
        out = link_dict(core, ln, request)
        since = utcnow() - timedelta(days=days)
        daily: dict[str, int] = {}
        refs: dict[str, int] = {}
        for at, ref in s.execute(select(LinkClick.at, LinkClick.referrer).where(LinkClick.link_id == ln.id, LinkClick.at >= since)):
            daily[at.date().isoformat()] = daily.get(at.date().isoformat(), 0) + 1
            host = (urlsplit(ref).netloc if ref else "") or "direct"
            refs[host] = refs.get(host, 0) + 1
        start = since.date()
        out["daily"] = [{"day": (start + timedelta(days=i)).isoformat(), "clicks": daily.get((start + timedelta(days=i)).isoformat(), 0)}
                        for i in range(days + 1)]
        out["referrers"] = sorted(({"referrer": k, "clicks": v} for k, v in refs.items()), key=lambda r: -r["clicks"])[:10]
        return out


def update_link(core, lid: str, payload: dict, request=None, channels: set | None = None) -> dict:
    with core.db.session() as s:
        ln = _link_or_404(s, s.get(Link, lid), channels)
        if "url" in payload:
            url = (payload.get("url") or "").strip()
            if not FULL_URL.match(url):
                raise GrowthError("Enter a full link that starts with https://")
            ln.url = url
        if "title" in payload:
            ln.title = (payload.get("title") or "").strip()[:200]
        if "campaign" in payload:
            ln.campaign = (payload.get("campaign") or "").strip()[:80]
            sync_campaigns(s)
        s.flush()
        return link_dict(core, ln, request)


def delete_link(core, lid: str, channels: set | None = None) -> dict:
    with core.db.session() as s:
        ln = _link_or_404(s, s.get(Link, lid), channels)
        s.delete(ln)
        s.query(LinkClick).filter(LinkClick.link_id == lid).delete()
        return {"deleted": lid}


BOT_UA = re.compile(r"bot|crawl|spider|preview|facebookexternalhit|slurp|embedly", re.I)
TABLET_UA = re.compile(r"ipad|tablet|kindle|silk|playbook|(android(?!.*mobile))", re.I)
MOBILE_UA = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini|iemobile|windows phone", re.I)


def device_of(ua: str) -> str:
    """mobile | tablet | desktop | unknown from a user agent (only this label is kept, never the UA itself)."""
    if not ua:
        return "unknown"
    if TABLET_UA.search(ua):
        return "tablet"
    if MOBILE_UA.search(ua):
        return "mobile"
    return "desktop"


def resolve_click(core, code: str, referrer: str = "", ua: str = "", count: bool = True) -> str | None:
    """Target URL for /l/{code}; counts the click (link-preview bots and HEAD requests are not counted)."""
    with core.db.session() as s:
        ln = s.scalar(select(Link).where(Link.code == code))
        if not ln:
            return None
        if count and not BOT_UA.search(ua or ""):
            ln.clicks = (ln.clicks or 0) + 1
            s.add(LinkClick(link_id=ln.id, referrer=(referrer or "")[:300], device=device_of(ua),
                            ua_hash=hashlib.sha256((ua or "").encode()).hexdigest()[:16]))
        return ln.url


# ============================================================================================ link in bio
# Bio pages live in services/bio.py (v2: brand buttons, embeds, email signup, contact cards, QR, custom domain).


# ============================================================================================ automations
KINDS = ("recurring", "evergreen", "rss", "first_comment")
FREQS = ("daily", "weekly", "monthly")
ACTIVE_TARGET = ("draft", "scheduled", "pending_approval", "sending", "processing")


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception as e:
        raise GrowthError(f"Unknown timezone '{name}'") from e


def next_occurrence(cfg: dict, after_utc: datetime) -> datetime | None:
    """Next recurring time strictly after `after_utc` (naive UTC), or None when the series has ended."""
    zone = _zone(cfg.get("timezone") or "Europe/Dublin")
    hh, mm = (int(x) for x in (cfg.get("time") or "09:00").split(":"))
    start = date.fromisoformat(cfg["start_date"]) if cfg.get("start_date") else None
    end = date.fromisoformat(cfg["end_date"]) if cfg.get("end_date") else None
    freq, every = cfg.get("frequency", "weekly"), max(1, int(cfg.get("interval") or 1))
    days = [int(d) for d in (cfg.get("weekdays") or [])]
    anchor = start or date.fromisoformat(cfg.get("anchor_date") or utcnow().date().isoformat())
    local_after = after_utc.replace(tzinfo=ZoneInfo("UTC")).astimezone(zone)
    d = max(local_after.date(), start) if start else local_after.date()
    for _ in range(800):
        if end and d > end:
            return None
        if freq == "daily":
            ok = (d - anchor).days % every == 0
        elif freq == "weekly":
            wk = ((d - timedelta(days=d.weekday())) - (anchor - timedelta(days=anchor.weekday()))).days // 7
            ok = d.weekday() in (days or [anchor.weekday()]) and wk % every == 0
        else:
            dom = min(int(cfg.get("day_of_month") or anchor.day), calendar.monthrange(d.year, d.month)[1])
            ok = d.day == dom and ((d.year - anchor.year) * 12 + d.month - anchor.month) % every == 0
        if ok:
            utc = datetime(d.year, d.month, d.day, hh, mm, tzinfo=zone).astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
            if utc > after_utc:
                return utc
        d += timedelta(days=1)
    return None


def _validate(kind: str, cfg: dict, s) -> dict:
    cfg = dict(cfg or {})
    if kind == "recurring":
        if not (cfg.get("caption") or "").strip() and not cfg.get("media_ids"):
            raise GrowthError("A recurring post needs a caption or media")
        if cfg.get("frequency", "weekly") not in FREQS:
            raise GrowthError("frequency must be daily, weekly or monthly")
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", cfg.get("time") or "09:00"):
            raise GrowthError("time must be HH:MM (24h)")
        try:
            wds = [int(d) for d in cfg.get("weekdays") or []]
        except (TypeError, ValueError) as e:
            raise GrowthError("weekdays are 0 (Mon) to 6 (Sun)") from e
        if any(d not in range(7) for d in wds):
            raise GrowthError("weekdays are 0 (Mon) to 6 (Sun)")
        cfg["weekdays"] = sorted(set(wds))
        for k in ("start_date", "end_date"):
            cfg[k] = _check_day(cfg.get(k), k)
        if cfg["start_date"] and cfg["end_date"] and cfg["end_date"] < cfg["start_date"]:
            raise GrowthError("The end date is before the start date")
        _zone(cfg.get("timezone") or "Europe/Dublin")
        posts_svc.load_media(s, cfg.get("media_ids") or [])
        cfg.setdefault("frequency", "weekly")
        cfg.setdefault("time", "09:00")
        cfg["interval"] = max(1, min(int(cfg.get("interval") or 1), 52))
        cfg.setdefault("timezone", "Europe/Dublin")
        cfg.setdefault("anchor_date", utcnow().date().isoformat())
        cfg["lead_hours"] = max(0, min(int(cfg.get("lead_hours", 24)), 24 * 14))
        cfg["max_occurrences"] = max(0, int(cfg.get("max_occurrences") or 0))
    elif kind == "evergreen":
        cfg["min_age_days"] = max(1, int(cfg.get("min_age_days") or 30))
        cfg["max_recycles"] = max(1, min(int(cfg.get("max_recycles") or 2), 50))
        cfg["per_run"] = max(1, min(int(cfg.get("per_run") or 1), 10))
        cfg["every_days"] = max(1, min(int(cfg.get("every_days") or 7), 90))
        cfg["min_score"] = float(cfg.get("min_score") or 0)
        cfg["when"] = cfg.get("when") if cfg.get("when") in ("queue", "draft") else "queue"
    elif kind == "rss":
        url = (cfg.get("url") or "").strip()
        if not re.match(r"^https?://", url):
            raise GrowthError("Feed URL must start with http:// or https://")
        cfg["url"] = url
        cfg["mode"] = cfg.get("mode") if cfg.get("mode") in ("draft", "queue") else "draft"
        cfg["template"] = cfg.get("template") or "{title}\n\n{link}"
        cfg["max_per_run"] = max(1, min(int(cfg.get("max_per_run") or 3), 20))
        cfg["every_minutes"] = max(15, min(int(cfg.get("every_minutes") or 60), 24 * 60))
        cfg["backfill"] = max(0, min(int(cfg.get("backfill") or 0), 20))
        cfg["include_image"] = bool(cfg.get("include_image", False))
    elif kind == "first_comment":
        text = (cfg.get("text") or "").strip()
        if not text:
            raise GrowthError("Write the first comment")
        if len(text) > 2200:
            raise GrowthError("A first comment is 2200 characters max")
        cfg["text"] = text
        cfg["mode"] = cfg.get("mode") if cfg.get("mode") in ("if_empty", "append") else "if_empty"
        cfg["tag"] = (cfg.get("tag") or "").strip()
        cfg["campaign"] = (cfg.get("campaign") or "").strip()
        cfg["video_only"] = bool(cfg.get("video_only", False))
    else:
        raise GrowthError(f"kind must be one of {', '.join(KINDS)}")
    return cfg


def _first_run(kind: str, cfg: dict, st: dict, now: datetime) -> datetime | None:
    if kind == "first_comment":     # applied at publish time, never scheduled
        return None
    if kind == "recurring":
        after = datetime.fromisoformat(st["last_occurrence"]) if st.get("last_occurrence") else now - timedelta(minutes=1)
        occ = next_occurrence(cfg, max(after, now - timedelta(minutes=1)))
        return occ - timedelta(hours=cfg.get("lead_hours", 24)) if occ else None
    return now


def automation_dict(a: Automation) -> dict:
    st = dict(a.state or {})
    st["seen_count"] = len(st.pop("seen", None) or [])
    st.pop("last", None)
    st["recycled_posts"] = len(st.pop("recycled", None) or {})
    st.pop("targets", None)
    return {"id": a.id, "kind": a.kind, "name": a.name, "channel_ids": a.channel_ids or [], "config": a.config or {},
            "state": st, "active": a.active, "next_run_at": iso(a.next_run_at), "created_at": iso(a.created_at)}


def list_automations(core, kind: str = "") -> list[dict]:
    with core.db.session() as s:
        qry = select(Automation).order_by(Automation.created_at)
        if kind:
            qry = qry.where(Automation.kind.in_(kind.split(",")))
        return [automation_dict(a) for a in s.scalars(qry)]


def save_automation(core, payload: dict, aid: str | None = None) -> dict:
    with core.db.session() as s:
        a = s.get(Automation, aid) if aid else None
        if aid and not a:
            raise GrowthError("Automation not found", 404)
        kind = a.kind if a else (payload.get("kind") or "")
        if kind not in KINDS:
            raise GrowthError(f"kind must be one of {', '.join(KINDS)}")
        a = a or Automation(kind=kind, state={}, active=True)
        refs = payload.get("channel_ids", a.channel_ids or [])
        if not refs:
            raise GrowthError("Pick at least one channel")
        chans = posts_svc.resolve_channels(s, refs)
        cfg = {**(a.config or {}), **(payload.get("config") or {})} if aid else (payload.get("config") or {})
        cfg = _validate(kind, cfg, s)
        a.kind, a.channel_ids, a.config = kind, [c.id for c in chans], cfg
        a.name = (payload.get("name") or a.name or {"recurring": "Recurring post", "evergreen": "Evergreen recycling",
                                                     "rss": "RSS feed", "first_comment": "Auto first comment"}[kind]).strip()[:80]
        if "active" in payload:
            a.active = bool(payload["active"])
        if not aid or "config" in payload or "active" in payload:
            a.next_run_at = _first_run(kind, cfg, a.state or {}, utcnow()) if a.active else None
        s.add(a)
        s.flush()
        return automation_dict(a)


def _upcoming(cfg: dict, after: datetime, n: int = 5) -> list[str]:
    out = []
    for _ in range(n):
        nxt = next_occurrence(cfg, after)
        if not nxt:
            break
        out.append(iso(nxt))
        after = nxt
    return out


def get_automation(core, aid: str) -> dict:
    with core.db.session() as s:
        a = s.get(Automation, aid)
        if not a:
            raise GrowthError("Automation not found", 404)
        out = automation_dict(a)
        made = s.scalars(select(PostTarget).join(Post, Post.id == PostTarget.post_id)
                         .where(Post.note.like(f"%[auto:{a.id}]%")).order_by(PostTarget.created_at.desc()).limit(50))
        out["posts"] = [posts_svc.serialize_target(core, t) for t in made]
        if a.kind == "recurring":
            out["upcoming"] = _upcoming(a.config, utcnow())
        return out


def delete_automation(core, aid: str) -> dict:
    with core.db.session() as s:
        a = s.get(Automation, aid)
        if not a:
            raise GrowthError("Automation not found", 404)
        s.delete(a)
        return {"deleted": aid}


def make_post(core, s, chans: list[Channel], caption: str, media_ids: list, options: dict, *, tags=None, campaign="",
              note="", when="queue", at=None, source="automation", topic_key="") -> Post:
    """Create a post directly. Automations repeat content on purpose (recurring, evergreen), so the
    duplicate-media guard of the composer is skipped here; RSS uses its own topic-key dedupe."""
    post = Post(caption=caption or "", media_ids=list(media_ids or []), tags=list(tags or []), note=note, source=source,
                campaign=(campaign or "")[:80], topic_key=posts_svc.slug_topic(topic_key))
    s.add(post)
    s.flush()
    for ch in chans:
        t = PostTarget(post=post, channel=ch, options=posts_svc._merge_options(core, ch, options or {}))
        s.add(t)
        s.flush()
        w = when
        try:
            posts_svc.apply_when(core, s, t, ch, w, at, False, None)
        except posts_svc.PostError:       # daily limit / time already passed: take the next free slot instead
            w = "queue"
            posts_svc.apply_when(core, s, t, ch, w, None, False, None)
        record(core, s, f"Created by automation ({w}) for {ch.username}", target_id=t.id, channel_id=ch.id)
    if campaign:
        sync_campaigns(s)
    return post


def _run_recurring(core, s, a: Automation, now: datetime) -> dict:
    cfg, st = a.config, dict(a.state or {})
    after = datetime.fromisoformat(st["last_occurrence"]) if st.get("last_occurrence") else now - timedelta(minutes=1)
    occ = next_occurrence(cfg, after)
    lead = timedelta(hours=cfg.get("lead_hours", 24))
    created = 0
    while occ and occ - lead <= now:
        if cfg.get("max_occurrences") and st.get("occurrences", 0) >= cfg["max_occurrences"]:
            occ = None
            break
        if occ >= now - timedelta(hours=1):     # an occurrence missed by more than an hour (server off) is skipped
            future = occ > now + timedelta(minutes=1)
            make_post(core, s, posts_svc.resolve_channels(s, a.channel_ids), cfg.get("caption") or "", cfg.get("media_ids") or [],
                      cfg.get("options") or {}, tags=cfg.get("tags"), campaign=cfg.get("campaign") or "",
                      note=f"Recurring: {a.name} [auto:{a.id}]", when="custom" if future else "now", at=iso(occ) if future else None)
            created += 1
            st["occurrences"] = st.get("occurrences", 0) + 1
        st["last_occurrence"] = occ.isoformat()
        occ = next_occurrence(cfg, occ)
    if cfg.get("max_occurrences") and st.get("occurrences", 0) >= cfg["max_occurrences"]:
        occ = None
    st["last_run"] = now.isoformat()
    a.state = st
    a.next_run_at = occ - lead if occ else None
    if not occ:
        a.active = False          # the series is over
    return {"created": created}


def evergreen_candidates(core, s, a: Automation, now: datetime) -> list[dict]:
    """Sent posts old enough to recycle, best first; one entry per original story."""
    cfg, st = a.config, dict(a.state or {})
    counts, last = st.get("recycled", {}), st.get("last", {})
    age = timedelta(days=cfg["min_age_days"])
    pending = {(t.options or {}).get("recycled_from") for t in
               s.scalars(select(PostTarget).where(PostTarget.status.in_(ACTIVE_TARGET)))} - {None}
    rows = s.scalars(select(PostTarget).where(PostTarget.status == "sent", PostTarget.channel_id.in_(a.channel_ids or []),
                                              PostTarget.published_at <= now - age))
    out = []
    for t in rows:
        origin = (t.options or {}).get("recycled_from") or t.id
        if origin in pending or counts.get(origin, 0) >= cfg["max_recycles"]:
            continue
        if last.get(origin) and datetime.fromisoformat(last[origin]) > now - age:
            continue
        if cfg.get("tag") and cfg["tag"] not in (t.post.tags or []):
            continue
        if cfg.get("campaign") and t.post.campaign != cfg["campaign"]:
            continue
        sc = score(t.insights)
        if sc < cfg.get("min_score", 0):
            continue
        out.append({"target": t, "origin": origin, "score": round(sc, 2), "recycled": counts.get(origin, 0)})
    out.sort(key=lambda r: -r["score"])
    best: dict[str, dict] = {}
    for r in out:
        best.setdefault(r["origin"], r)
    return list(best.values())


def _run_evergreen(core, s, a: Automation, now: datetime) -> dict:
    cfg, st = a.config, dict(a.state or {})
    recycled, last = dict(st.get("recycled") or {}), dict(st.get("last") or {})
    created, per_channel = 0, {}
    for r in evergreen_candidates(core, s, a, now):
        t = r["target"]
        if per_channel.get(t.channel_id, 0) >= cfg["per_run"]:
            continue
        opts = dict(t.options or {})
        opts["recycled_from"] = r["origin"]
        make_post(core, s, [t.channel], t.caption, t.media_list, opts, tags=t.post.tags, campaign=t.post.campaign,
                  note=f"Evergreen: recycled from {r['origin']} (score {r['score']}) [auto:{a.id}]", when=cfg["when"])
        recycled[r["origin"]] = recycled.get(r["origin"], 0) + 1
        last[r["origin"]] = now.isoformat()
        per_channel[t.channel_id] = per_channel.get(t.channel_id, 0) + 1
        created += 1
    st.update({"recycled": recycled, "last": last, "last_run": now.isoformat(), "total": st.get("total", 0) + created})
    a.state = st
    a.next_run_at = now + timedelta(days=cfg["every_days"])
    return {"created": created}


# ---- RSS
NS = {"atom": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
TAG_RE = re.compile(r"<[^>]+>")


def _text(el, path) -> str:
    x = el.find(path, NS) if el is not None else None
    return (x.text or "").strip() if x is not None and x.text else ""


def parse_feed(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text.encode() if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as e:
        raise GrowthError(f"That is not a valid RSS or Atom feed ({e})") from e
    items, title = [], ""
    if root.tag == "rss" or root.find("channel") is not None:
        ch = root.find("channel")
        title = _text(ch, "title")
        for it in (ch.findall("item") if ch is not None else []):
            img = ""
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image"):
                img = enc.get("url") or ""
            for p in ("media:content", "media:thumbnail"):
                m = it.find(p, NS)
                if not img and m is not None and m.get("url"):
                    img = m.get("url")
            items.append({"title": _text(it, "title"), "link": _text(it, "link"), "guid": _text(it, "guid"),
                          "summary": _text(it, "description"), "published": _text(it, "pubDate"), "image": img})
    elif root.tag.endswith("feed"):
        title = _text(root, "atom:title")
        for it in root.findall("atom:entry", NS):
            link = next((ln.get("href") or "" for ln in it.findall("atom:link", NS) if ln.get("rel", "alternate") == "alternate"), "")
            m = it.find("media:thumbnail", NS)
            items.append({"title": _text(it, "atom:title"), "link": link, "guid": _text(it, "atom:id"),
                          "summary": _text(it, "atom:summary") or _text(it, "atom:content"),
                          "published": _text(it, "atom:published") or _text(it, "atom:updated"),
                          "image": (m.get("url") or "") if m is not None else ""})
    else:
        raise GrowthError("That is not a valid RSS or Atom feed")
    for it in items:
        it["summary"] = re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", it["summary"] or ""))).strip()[:280]
        it["title"] = html.unescape(it["title"])
        it["key"] = hashlib.sha1((it["guid"] or it["link"] or it["title"]).encode(), usedforsecurity=False).hexdigest()[:16]
    return {"title": html.unescape(title), "items": items}


def fetch_feed(core, url: str) -> dict:
    import httpx

    from .. import netguard   # SECURITY_RULES 1: a feed URL is user-supplied
    try:
        r = netguard.request(core.http, "GET", url, policy=netguard.fetch_policy(core), limit=5_000_000,
                             headers={"User-Agent": "HuhQueue RSS (+https://github.com/dataanalystram/bufferopensource)"})
    except netguard.FetchError as e:
        raise GrowthError(f"Could not fetch the feed: {e}") from e
    except httpx.HTTPError as e:
        raise GrowthError(f"Could not fetch the feed: {e}") from e
    if r.status_code >= 400:
        raise GrowthError(f"Could not fetch the feed: HTTP {r.status_code}")
    return parse_feed(r.text)


def render_template(tpl: str, item: dict, feed_title: str) -> str:
    out = tpl
    for k in ("title", "link", "summary"):
        out = out.replace("{" + k + "}", item.get(k) or "")
    return out.replace("{feed}", feed_title or "").strip()


def _run_rss(core, s, a: Automation, now: datetime) -> dict:
    cfg, st = a.config, dict(a.state or {})
    seen = list(st.get("seen") or [])
    a.next_run_at = now + timedelta(minutes=cfg["every_minutes"])
    try:
        feed = fetch_feed(core, cfg["url"])
    except GrowthError as e:
        st.update({"last_error": e.message, "last_checked": now.isoformat()})
        a.state = st
        record(core, s, f"RSS '{a.name}': {e.message}", level="warning", kind="automation")
        return {"created": 0, "error": e.message}
    items = feed["items"]
    if not st.get("initialized"):
        # first look at a feed: only the newest `backfill` items are posted; the back catalogue is marked as seen
        keep = {it["key"] for it in items[:cfg.get("backfill", 0)]}
        seen += [it["key"] for it in items if it["key"] not in keep]
        st["initialized"] = True
    created, skipped = 0, 0
    chans = posts_svc.resolve_channels(s, a.channel_ids)
    for it in reversed(items):          # oldest new item first, so the queue keeps the feed's order
        if it["key"] in seen:
            continue
        if created >= cfg["max_per_run"]:
            break
        seen.append(it["key"])
        topic = f"rss-{it['key']}"
        if s.scalar(select(func.count(Post.id)).where(Post.topic_key == topic)):   # also across rules / a re-created rule
            skipped += 1
            continue
        media_ids = []
        if cfg.get("include_image") and it.get("image"):
            from . import media as media_svc
            try:
                media_ids = [media_svc.download(core, it["image"]).id]
            except Exception:  # an image that will not download never blocks the post
                media_ids = []
        make_post(core, s, chans, render_template(cfg["template"], it, feed["title"]), media_ids, cfg.get("options") or {},
                  tags=cfg.get("tags"), campaign=cfg.get("campaign") or "", note=f"RSS: {a.name} · {it['link']} [auto:{a.id}]",
                  when=cfg["mode"], topic_key=topic)
        created += 1
    st.update({"seen": seen[-1000:], "last_checked": now.isoformat(), "last_error": "", "feed_title": feed["title"],
               "posted": st.get("posted", 0) + created})
    a.state = st
    return {"created": created, "skipped_duplicates": skipped}


def first_comment_text(cfg: dict, t: PostTarget, current: str) -> str | None:
    """The first comment a rule gives this target, or None when the rule does not apply."""
    post = t.post
    if cfg.get("tag") and cfg["tag"] not in ((post.tags if post else None) or []):
        return None
    if cfg.get("campaign") and (post.campaign if post else "") != cfg["campaign"]:
        return None
    text = cfg["text"].replace("{campaign}", (post.campaign if post else "") or "").strip()
    current = (current or "").strip()
    if text in current:
        return None
    if current and cfg.get("mode") != "append":
        return None
    return (current + "\n\n" + text).strip() if current else text


def apply_first_comment_rules(core, s, t: PostTarget, opts: dict) -> dict:
    """Publish-time hook: active first_comment rules for this channel fill (or extend) the first comment."""
    if s is None or not core.connector(t.channel.provider).capabilities.get("first_comment"):
        return opts
    rules = [a for a in s.scalars(select(Automation).where(Automation.kind == "first_comment", Automation.active.is_(True))
                                  .order_by(Automation.created_at)) if t.channel_id in (a.channel_ids or [])]
    if rules and any(a.config.get("video_only") for a in rules):
        kinds = {m.kind for m in (s.get(Media, mid) for mid in t.media_list) if m}
    for a in rules:
        if a.config.get("video_only") and "video" not in kinds:
            continue
        new = first_comment_text(a.config, t, opts.get("first_comment") or "")
        if new is not None:
            opts["first_comment"] = new
            st = dict(a.state or {})
            done = list(st.get("targets") or [])
            if t.id not in done:
                st["targets"] = (done + [t.id])[-500:]
                st["applied"] = st.get("applied", 0) + 1
                a.state = st
    return opts


def _run_first_comment(core, s, a: Automation, now: datetime) -> dict:
    """'Run now' for a first-comment rule: fill the comment on matching scheduled posts (no comment yet)."""
    n = 0
    for t in s.scalars(select(PostTarget).where(PostTarget.status.in_(("scheduled", "draft", "pending_approval")),
                                                PostTarget.channel_id.in_(a.channel_ids or []))):
        if not core.connector(t.channel.provider).capabilities.get("first_comment"):
            continue
        new = first_comment_text(a.config, t, (t.options or {}).get("first_comment") or "")
        if new is not None:
            t.options = {**(t.options or {}), "first_comment": new}
            n += 1
    a.state = {**(a.state or {}), "last_run": now.isoformat()}
    return {"created": 0, "updated": n}


RUNNERS = {"recurring": _run_recurring, "evergreen": _run_evergreen, "rss": _run_rss, "first_comment": _run_first_comment}


def run_automation(core, aid: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    with core.db.session() as s:
        a = s.get(Automation, aid)
        if not a:
            raise GrowthError("Automation not found", 404)
        res = RUNNERS[a.kind](core, s, a, now)
        if res.get("created"):
            record(core, s, f"Automation '{a.name}' created {res['created']} post(s)", level="success", kind="automation")
        s.flush()
        return {**res, "automation": automation_dict(a)}


def preview_automation(core, aid: str, now: datetime | None = None) -> dict:
    """What a run would do now, without creating anything."""
    now = now or utcnow()
    with core.db.session() as s:
        a = s.get(Automation, aid)
        if not a:
            raise GrowthError("Automation not found", 404)
        if a.kind == "evergreen":
            return {"items": [{"target_id": r["target"].id, "origin": r["origin"], "score": r["score"], "recycled": r["recycled"],
                               "channel_id": r["target"].channel_id, "caption": (r["target"].caption or "")[:120]}
                              for r in evergreen_candidates(core, s, a, now)[:20]]}
        if a.kind == "first_comment":
            rows = []
            for t in s.scalars(select(PostTarget).where(PostTarget.status.in_(("scheduled", "draft", "pending_approval")),
                                                        PostTarget.channel_id.in_(a.channel_ids or [])).limit(200)):
                new = first_comment_text(a.config, t, (t.options or {}).get("first_comment") or "")
                if new is not None:
                    rows.append({"target_id": t.id, "caption": (t.caption or "")[:120], "first_comment": new})
            return {"items": rows[:20]}
        if a.kind == "rss":
            feed = fetch_feed(core, a.config["url"])
            seen = set((a.state or {}).get("seen") or [])
            return {"feed_title": feed["title"],
                    "items": [{"title": it["title"], "link": it["link"], "new": it["key"] not in seen,
                               "caption": render_template(a.config["template"], it, feed["title"])} for it in feed["items"][:20]]}
        return {"items": [{"at": x} for x in _upcoming(a.config, now)]}


def run_due_automations(core, now: datetime | None = None) -> int:
    """Worker hook: run every active automation whose next_run_at has come."""
    now = now or utcnow()
    with core.db.session() as s:
        ids = list(s.scalars(select(Automation.id).where(Automation.active.is_(True), Automation.next_run_at.is_not(None),
                                                          Automation.next_run_at <= now)))
    n = 0
    for aid in ids:
        try:
            n += run_automation(core, aid, now).get("created", 0)
        except Exception as e:  # one broken rule never stops the others or the publisher
            with core.db.session() as s:
                a = s.get(Automation, aid)
                if a:
                    a.state = {**(a.state or {}), "last_error": str(e)[:300]}
                    a.next_run_at = now + timedelta(hours=1)
                    record(core, s, f"Automation '{a.name}' failed: {e}", level="error", kind="automation")
    return n


# ============================================================================================ hashtags
HASHTAG = re.compile(r"(?<![\w&])#([A-Za-z0-9_\u00C0-\uFFFF]{1,100})")


def parse_hashtags(text: str) -> list[str]:
    out = []
    for m in HASHTAG.finditer(text or ""):
        h = "#" + m.group(1)
        if h.lower() not in (x.lower() for x in out):
            out.append(h)
    return out


def _groups_raw(s) -> list[dict]:
    row = s.get(KV, "lib:hashtag_groups")
    if row:
        return list((row.value or {}).get("items") or [])
    from ..api.app import DEFAULT_LIBRARY      # same defaults as GET /api/library/hashtag_groups
    return [dict(g) for g in DEFAULT_LIBRARY.get("hashtag_groups", [])]


def _store_groups(s, items: list[dict]) -> None:
    row = s.get(KV, "lib:hashtag_groups") or KV(key="lib:hashtag_groups", value={})
    row.value = {"items": items}
    s.merge(row)


def hashtag_stats(core, days: int = 90, s=None, channels: set | None = None) -> list[dict]:
    """Every hashtag used in the last `days` on `channels` (None = all): how often, on which networks, how the sent posts did."""
    from ..scope_filter import only
    def run(s):
        since = utcnow() - timedelta(days=days)
        rows = s.execute(only(select(func.coalesce(PostTarget.caption_override, Post.caption), PostTarget.options, PostTarget.status, PostTarget.insights, PostTarget.published_at,
                                PostTarget.scheduled_at, PostTarget.created_at, Channel.provider)
                         .join(Channel, Channel.id == PostTarget.channel_id).join(Post, Post.id == PostTarget.post_id)
                         .where(func.coalesce(PostTarget.published_at, PostTarget.scheduled_at, PostTarget.created_at) >= since),
                              PostTarget.channel_id, channels))
        stats: dict[str, dict] = {}
        for cap, opts, st, ins, pub, sch, cr, prov in rows:
            tags = parse_hashtags((cap or "") + "\n" + ((opts or {}).get("first_comment") or ""))
            for h in tags:
                k = h.lower()
                r = stats.setdefault(k, {"tag": h, "uses": 0, "sent": 0, "scheduled": 0, "networks": set(), "score": 0.0,
                                         "views": 0, "engagements": 0, "last_used": None})
                r["uses"] += 1
                r["networks"].add(prov)
                when = pub or sch or cr
                if when and (r["last_used"] is None or when > r["last_used"]):
                    r["last_used"] = when
                if st == "sent":
                    ins = ins or {}
                    r["sent"] += 1
                    r["score"] += score(ins)
                    r["views"] += int(ins.get("views") or 0)
                    r["engagements"] += sum(int(ins.get(x) or 0) for x in ("likes", "comments", "shares", "saved"))
                elif st == "scheduled":
                    r["scheduled"] += 1
        out = []
        for r in stats.values():
            n = r["sent"] or 1
            out.append({"tag": r["tag"], "uses": r["uses"], "sent": r["sent"], "scheduled": r["scheduled"],
                        "networks": sorted(r["networks"]), "avg_score": round(r["score"] / n, 2) if r["sent"] else None,
                        "avg_views": round(r["views"] / n) if r["sent"] else None,
                        "avg_engagements": round(r["engagements"] / n, 1) if r["sent"] else None, "last_used": iso(r["last_used"])})
        out.sort(key=lambda r: (-(r["avg_score"] or -1), -r["uses"]))
        return out
    if s is not None:
        return run(s)
    with core.db.session() as s2:
        return run(s2)


def list_hashtag_groups(core) -> list[dict]:
    with core.db.session() as s:
        by_tag = {r["tag"].lower(): r for r in hashtag_stats(core, 90, s)}
        out = []
        for g in _groups_raw(s):
            tags = parse_hashtags(g.get("text") or "")
            perf = [by_tag[t.lower()]["avg_score"] for t in tags if t.lower() in by_tag and by_tag[t.lower()]["avg_score"] is not None]
            out.append({**g, "tags": tags, "count": len(tags), "uses": sum(by_tag[t.lower()]["uses"] for t in tags if t.lower() in by_tag),
                        "avg_score": round(sum(perf) / len(perf), 2) if perf else None})
        return out


def save_hashtag_group(core, payload: dict, gid: str | None = None) -> dict:
    name = str(payload.get("name") or "").strip()[:60]
    raw = str(payload.get("text") or "")
    if "tags" in payload and isinstance(payload["tags"], list):
        raw = " ".join(payload["tags"])
    tags = parse_hashtags(raw if "#" in raw else " ".join("#" + w.strip().lstrip("#") for w in re.split(r"[\s,]+", raw) if w.strip()))
    limit = int(core.prefs().get("hashtag_limit") or 30) or 30
    with core.db.session() as s:
        items = _groups_raw(s)
        cur = next((g for g in items if g.get("id") == gid), None) if gid else None
        if gid and not cur:
            raise GrowthError("Hashtag group not found", 404)
        if not name and not cur:
            raise GrowthError("Give the group a name")
        if ("text" in payload or "tags" in payload or not cur) and not tags:
            raise GrowthError("Add at least one hashtag")
        if len(tags) > limit:
            raise GrowthError(f"{len(tags)} hashtags - your limit is {limit} (Settings › Hashtag limit)")
        if any(g.get("name", "").lower() == name.lower() and g is not cur for g in items if name):
            raise GrowthError(f"A group called '{name}' already exists", 409)
        g = cur or {"id": secrets.token_hex(4)}
        if name:
            g["name"] = name
        if tags and ("text" in payload or "tags" in payload or not cur):
            g["text"] = " ".join(tags)
        if not cur:
            items.append(g)
        _store_groups(s, items)
    return next(x for x in list_hashtag_groups(core) if x["id"] == g["id"])


def delete_hashtag_group(core, gid: str) -> dict:
    with core.db.session() as s:
        items = _groups_raw(s)
        keep = [g for g in items if g.get("id") != gid]
        if len(keep) == len(items):
            raise GrowthError("Hashtag group not found", 404)
        _store_groups(s, keep)
    return {"deleted": gid}


def suggest_hashtags(core, caption: str = "", limit: int = 10, channels: set | None = None) -> dict:
    """Best-performing hashtags you have used that are not in the caption yet, plus groups that share its tags."""
    have = {t.lower() for t in parse_hashtags(caption)}
    words = {w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", caption or "")}
    stats = [r for r in hashtag_stats(core, 180, channels=channels) if r["tag"].lower() not in have]
    def rank(r):
        rel = 1 if any(w in r["tag"].lower() for w in words) else 0
        return (-rel, -(r["avg_score"] or 0), -r["uses"])
    groups = [g for g in list_hashtag_groups(core) if have & {t.lower() for t in g["tags"]} or
              any(w in g.get("name", "").lower() for w in words)]
    return {"tags": [{"tag": r["tag"], "avg_score": r["avg_score"], "uses": r["uses"]} for r in sorted(stats, key=rank)[:limit]],
            "groups": [{"id": g["id"], "name": g.get("name"), "text": g.get("text")} for g in groups[:5]]}


# ============================================================================================ CSV import
COLUMNS = {
    "caption": ("caption", "text", "message", "content", "description", "body"),
    "channels": ("channels", "channel", "profiles", "profile", "accounts", "networks"),
    "scheduled_at": ("scheduled_at", "posting_time", "date", "datetime", "schedule", "publish_at", "time", "scheduled"),
    "when": ("when", "mode"),
    "media_urls": ("media_urls", "media_url", "media", "image_url", "video_url", "image", "video", "images", "videos"),
    "tags": ("tags", "tag", "labels"),
    "campaign": ("campaign",),
    "topic_key": ("topic_key", "topic"),
    "title": ("title", "video_title", "yt_title"),
    "first_comment": ("first_comment", "comment"),
    "alt_text": ("alt_text", "alt", "image_alt_text"),
    "link": ("link", "url", "link_url"),
    "document_url": ("document_url", "pdf_url", "document", "pdf"),
    "document_title": ("document_title", "pdf_title"),
    "format": ("format", "post_type", "type"),
    "privacy": ("privacy", "visibility", "privacy_level"),
    "timezone": ("timezone", "tz"),
    "note": ("note", "notes"),
}
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
FORMAT_MAP = {
    "instagram": {"key": "ig_type", "reel": "reel", "story": "story", "post": "feed", "carousel": "carousel", "video": "reel"},
    "youtube": {"key": "yt_format", "short": "short", "video": "video", "reel": "short"},
    "tiktok": {"key": "tt_format", "video": "video", "photo": "photo", "carousel": "photo", "post": "photo"},
    "linkedin": {"key": "li_format", "auto": "auto", "text": "text", "post": "auto", "image": "image", "photo": "image",
                 "multi_image": "multi_image", "multi-image": "multi_image", "images": "multi_image",
                 "document": "document", "pdf": "document", "carousel": "document", "video": "video",
                 "article": "article", "link": "article"},
}
FORMAT_MAP["linkedin_page"] = FORMAT_MAP["linkedin"]
MAX_ROWS = 500


def _kind_of(url: str) -> str:
    path = urlsplit(url).path.lower()
    return "video" if path.endswith(VIDEO_EXT) else "image" if path.endswith(IMAGE_EXT) else "unknown"


def _read_csv(text: str) -> tuple[list[dict], list[str], list[str]]:
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        raise GrowthError("The CSV is empty")
    first = text.splitlines()[0]
    delim = max((",", ";", "\t"), key=first.count)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    header = [h.strip() for h in (reader.fieldnames or [])]
    lookup, unknown = {}, []
    for h in header:
        k = h.lower().replace(" ", "_")
        canon = next((c for c, al in COLUMNS.items() if k in al), None)
        if canon and canon not in lookup.values():
            lookup[h] = canon
        else:
            unknown.append(h)
    if "caption" not in lookup.values() and "media_urls" not in lookup.values():
        raise GrowthError("The CSV needs a 'caption' column (and optionally channels, scheduled_at, media_urls, tags, campaign, title)")
    rows = []
    for raw in reader:
        row = {}
        for h, v in raw.items():
            canon = lookup.get((h or "").strip())
            if canon and isinstance(v, str):
                row[canon] = v.strip()
        if any(row.values()):
            if len(rows) >= MAX_ROWS:
                row["_over_limit"] = True     # flagged in the preview, never silently dropped (Buffer drops them)
            rows.append(row)
    if not rows:
        raise GrowthError("The CSV has a header but no rows")
    return rows, list(lookup.values()), unknown


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[|;\n]+|,(?!\S*=)|\s+(?=https?://)", v or "") if x.strip()]


def check_rows(core, s, text: str, defaults: dict | None = None) -> dict:
    defaults = defaults or {}
    rows, cols, unknown = _read_csv(text)
    tz_default = defaults.get("timezone") or core.prefs()["timezone"]
    out, topics = [], {}
    now = utcnow()
    for i, r in enumerate(rows, start=2):           # row 1 is the header
        errs, warns = [], []
        if r.get("_over_limit"):
            out.append({"row": i, "caption": r.get("caption", ""), "channels": [], "when": "", "scheduled_at": None,
                        "media": [], "tags": [], "campaign": "", "topic_key": "", "note": "", "options": {},
                        "errors": [f"Over the {MAX_ROWS}-row limit - not imported. Put it in another file."],
                        "warnings": [], "ok": False, "over_limit": True})
            continue
        refs = _split(r.get("channels", "")) or list(defaults.get("channel_ids") or [])
        chans = []
        for ref in refs:
            try:
                chans += [c for c in posts_svc.resolve_channels(s, [ref]) if c not in chans]
            except posts_svc.PostError as e:
                errs.append(e.message)
        if not refs:
            errs.append("No channel - add a 'channels' column (e.g. instagram|youtube) or pick default channels")
        caption = r.get("caption", "")
        media = [{"url": u, "kind": _kind_of(u)} for u in _split(r.get("media_urls", ""))]
        for u in _split(r.get("document_url", "")):
            media.append({"url": u, "kind": "document"})
        for m in media:
            if not re.match(r"^https?://", m["url"]):
                errs.append(f"Media '{m['url'][:60]}' must be an http(s) URL")
            elif m["kind"] == "unknown":
                warns.append(f"Can't tell if {m['url'][:60]} is an image or a video from its name - checked on import")
        if not caption and not media:
            errs.append("Empty row: needs a caption or media")
        when = (r.get("when") or "").lower() or (defaults.get("when") or "")
        at, sched = r.get("scheduled_at") or "", None
        if when and when not in posts_svc.WHEN:
            errs.append(f"'when' must be one of {', '.join(posts_svc.WHEN)}")
        if at and when != "draft":
            try:
                zone = r.get("timezone") or tz_default
                ZoneInfo(zone)
            except Exception:
                errs.append(f"Unknown timezone '{r.get('timezone')}'")
                zone = None
            if zone:
                try:
                    sched = posts_svc.parse_when(at.replace(" ", "T", 1) if re.match(r"^\d{4}-\d{2}-\d{2} \d", at) else at, zone)
                    # Buffer: a Posting Time wins over the upload's Queue choice; blank = next queue slot
                    when = "custom" if not r.get("when") and when in ("", "queue") else (when or "custom")
                    if when == "custom" and sched < now - timedelta(minutes=1):
                        errs.append(f"{at} is in the past")
                except posts_svc.PostError:
                    errs.append(f"Bad date '{at}' - use 2026-11-03 09:30 or ISO 2026-11-03T09:30")
        when = when or "queue"
        if when == "custom" and not at:
            errs.append("'custom' needs a scheduled_at")
        videos = [m for m in media if m["kind"] == "video"]
        unknown_media = any(m["kind"] == "unknown" for m in media)
        fmt = (r.get("format") or "").lower()
        per_opts = {}
        for ch in chans:
            o = {}
            fm = FORMAT_MAP.get(ch.provider)
            if fm and fmt:
                if fmt in fm and fmt != "key":
                    o[fm["key"]] = fm[fmt]
                else:
                    warns.append(f"{ch.provider}: format '{fmt}' is not used there - picked automatically")
            if ch.provider == "youtube":
                if not videos and not unknown_media:
                    errs.append(f"YouTube @{ch.username} needs a video (a media URL ending .mp4/.mov…)")
                title = r.get("title") or caption.split("\n")[0].strip()
                if not title:
                    errs.append(f"YouTube @{ch.username} needs a title")
                elif len(title) > 100:
                    errs.append(f"YouTube titles are 100 characters max ({len(title)})")
                elif not r.get("title"):
                    warns.append("YouTube title taken from the caption's first line")
                o["yt_title"] = title[:100]
                if r.get("privacy"):
                    if r["privacy"].lower() not in ("public", "unlisted", "private"):
                        errs.append("YouTube privacy must be public, unlisted or private")
                    o["yt_privacy"] = r["privacy"].lower()
            elif ch.provider == "tiktok":
                if not media:
                    errs.append(f"TikTok @{ch.username} needs a video or photos")
                if r.get("privacy"):
                    o["privacy_level"] = r["privacy"].upper()
                if r.get("title") and not videos:
                    o["title"] = r["title"][:90]
            elif ch.provider == "instagram" and not media:
                errs.append(f"Instagram @{ch.username} needs an image or a video")
            elif ch.provider in ("linkedin", "linkedin_page"):
                for k in ("alt_text", "link", "document_title"):
                    if r.get(k):
                        o[k] = r[k]
                if r.get("privacy"):
                    vis = {"public": "PUBLIC", "connections": "CONNECTIONS"}.get(r["privacy"].lower())
                    if not vis:
                        errs.append("LinkedIn privacy must be public or connections")
                    o["visibility"] = vis or "PUBLIC"
            if any(m["kind"] == "document" for m in media) and ch.provider not in ("linkedin", "linkedin_page"):
                errs.append(f"{ch.provider}: PDF documents only post to LinkedIn")
            if r.get("first_comment"):
                o["first_comment"] = r["first_comment"]
            lim = core.connector(ch.provider).caption_limit
            if len(caption) > lim:
                errs.append(f"{ch.provider}: caption is {len(caption)} characters (max {lim})")
            per_opts[ch.id] = o
        topic = posts_svc.slug_topic(r.get("topic_key", ""))
        if topic:
            if topic in topics:
                errs.append(f"Same topic as row {topics[topic]} - each story is posted once")
            topics.setdefault(topic, i)
            for ch in chans:
                errs += [d["message"] for d in posts_svc.find_duplicates(s, ch, [], topic)]
        out.append({"row": i, "caption": caption, "channels": [{"id": c.id, "provider": c.provider, "username": c.username} for c in chans],
                    "when": when, "scheduled_at": iso(sched), "media": media, "tags": _split(r.get("tags", "")),
                    "campaign": r.get("campaign", ""), "topic_key": topic, "note": r.get("note", ""), "options": per_opts,
                    "errors": errs, "warnings": warns, "ok": not errs})
    return {"rows": out, "total": len(out), "valid": sum(1 for r in out if r["ok"]), "invalid": sum(1 for r in out if not r["ok"]),
            "over_limit": sum(1 for r in out if r.get("over_limit")), "max_rows": MAX_ROWS,
            "columns": cols, "unknown_columns": unknown}


def preview_csv(core, text: str, defaults: dict | None = None) -> dict:
    with core.db.session() as s:
        return check_rows(core, s, text, defaults)


def import_csv(core, text: str, defaults: dict | None = None, approval_by: str | None = None) -> dict:
    """Create every valid row. Media URLs are downloaded into the library first (videos for YouTube / TikTok)."""
    from . import media as media_svc
    defaults = defaults or {}
    pre = preview_csv(core, text, defaults)
    if pre["invalid"] and defaults.get("skip_invalid") is False:
        raise GrowthError(f"{pre['invalid']} row(s) have errors - fix them or import only the valid rows", 422)
    results = []
    for r in pre["rows"]:
        if not r["ok"]:
            results.append({"row": r["row"], "ok": False, "error": "; ".join(r["errors"])})
            continue
        try:
            media_ids = []
            for m in r["media"]:
                md = media_svc.download(core, m["url"])
                media_ids.append(md.id)
                m["kind"] = md.kind
            if any(c["provider"] == "youtube" for c in r["channels"]) and not any(m["kind"] == "video" for m in r["media"]):
                raise GrowthError("YouTube needs a video - the downloaded media is not one")
            payload = {"caption": r["caption"], "media_ids": media_ids, "channel_ids": [c["id"] for c in r["channels"]],
                       "tags": r["tags"], "campaign": r["campaign"], "topic_key": r["topic_key"], "note": r["note"],
                       "when": r["when"], "scheduled_at": r["scheduled_at"] if r["when"] == "custom" else None,
                       "channels": [{"channel_id": cid, "options": o} for cid, o in r["options"].items()]}
            out = posts_svc.create_post(core, payload, source="csv", approval_by=approval_by)
            results.append({"row": r["row"], "ok": True, "post_id": out["id"], "targets": [t["id"] for t in out["targets"]],
                            "notes": out.get("notes", [])})
        except (posts_svc.PostError, media_svc.MediaError) as e:
            msg = getattr(e, "message", None) or str(e)
            issues = getattr(e, "issues", None) or {}
            extra = [i["message"] for lst in issues.values() for i in lst if i.get("level") == "error"]
            results.append({"row": r["row"], "ok": False, "error": msg + (": " + "; ".join(extra) if extra else "")})
    with core.db.session() as s:
        sync_campaigns(s)
    ok = sum(1 for x in results if x["ok"])
    return {"imported": ok, "failed": len(results) - ok, "results": results}


# Same headers as Buffer's bulk-upload template, so a Buffer CSV imports unchanged (Posting Time blank = next slot).
CSV_TEMPLATE_BUFFER = ("Text,Image URL,Tags,Posting Time\n"
                       "\"GPT-6 is here: 3 things to know\",https://example.com/gpt6.jpg,news,2026-11-03 09:30\n"
                       "\"Our weekly AI recap\",,recap,\n")
CSV_TEMPLATE_LINKEDIN = ("Text,Format,Image URL,Document URL,Document Title,Alt Text,Link,First Comment,Tags,Posting Time\n"
                         "\"5 AI launches this week (swipe)\",document,,https://example.com/deck.pdf,AI launches this week,,,"
                         "\"Sources in the comments\",recap,2026-11-03 09:30\n"
                         "\"Two screenshots from the demo\",multi_image,https://example.com/a.jpg|https://example.com/b.jpg,,,"
                         "Demo screenshot,,,demo,\n"
                         "\"Read the launch post\",article,,,,,https://example.com/launch,,news,\n")

CSV_TEMPLATE = ("caption,channels,scheduled_at,media_urls,title,format,tags,campaign,topic_key\n"
                "\"GPT-6 is here: 3 things to know\",instagram|x,2026-11-03 09:30,https://example.com/gpt6.jpg,,post,news|launch,DevDay week,gpt6-launch\n"
                "\"Watch: the new agent demo\",youtube|tiktok,2026-11-03 18:00,https://example.com/demo.mp4,The new agent demo in 45 seconds,short,demo,DevDay week,agent-demo\n")

CSV_TEMPLATES = {"huhqueue": CSV_TEMPLATE, "buffer": CSV_TEMPLATE_BUFFER, "linkedin": CSV_TEMPLATE_LINKEDIN}
