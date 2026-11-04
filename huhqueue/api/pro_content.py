"""Pro features, free for everyone: post-connect onboarding with recommended times, best time to post, human approvals,
version history, internal notes with @mentions, media library (folders, tags, search, usage, dedup), contacts and a
markdown report with period comparison."""
from __future__ import annotations

import hashlib
import re
import statistics
from datetime import datetime, timedelta

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, or_, select

from ..scope_filter import NO_CHANNEL, narrow, only, visible
from .. import actions as reg
from ..actions import B, I, S, SA, WHEN, Action
from ..core import utcnow
from ..models import Channel, Comment, Contact, Event, Idea, Media, Post, PostNote, PostTarget, Revision
from ..services import posts as posts_svc
from ..services import scheduling
from ..services.events import record
from .. import teams
from ..teams import notify_mentions, notify_note

# --------------------------------------------------------------------------------------------- best-time research
# Ranked (weekday 0=Mon, start hour, local time) windows from Buffer's 2026 analysis of 52M+ posts,
# https://buffer.com/resources/best-time-to-post-social-media/ (fetched 22 Oct 2026). Times are local to the channel.
RESEARCH = {
    "instagram": [(3, 9), (2, 9), (2, 18), (1, 18), (3, 18), (0, 18), (1, 9), (6, 18)],   # Thu 9am best; weekdays 9am & 6pm; Fri worst
    "youtube": [(4, 16), (6, 10), (4, 18), (5, 18), (3, 19), (6, 9), (4, 12), (1, 19)],    # Shorts Fri 4/6/7pm; long-form Sun 10am
    "x": [(1, 9), (2, 10), (2, 9), (3, 9), (1, 10), (3, 10), (0, 9), (4, 9)],              # Tue 9am best; weekday mornings
    "tiktok": [(6, 9), (0, 13), (6, 13), (5, 19), (5, 9), (0, 20), (4, 20), (3, 19)],      # Sun 9am, Mon 1pm, Sun 1pm; evenings 6-11pm
    "facebook": [(3, 9), (2, 9), (2, 10), (3, 10), (1, 9), (0, 9), (4, 9)],
    "linkedin": [(2, 16), (4, 16), (4, 15), (3, 16), (5, 16), (6, 16)],
    "threads": [(3, 9), (2, 9), (1, 9), (2, 10), (3, 8), (1, 10)],
}
RESEARCH["fake"] = RESEARCH["instagram"]
RESEARCH_SOURCE = "https://buffer.com/resources/best-time-to-post-social-media/"
GOALS = [{"value": 1, "label": "Keep it steady", "badge": "1x"}, {"value": 3, "label": "Build a presence", "badge": "3x", "default": True},
         {"value": 5, "label": "Reach new heights", "badge": "5x"}]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _minute(channel_id: str, weekday: int) -> int:
    """A stable minute inside the hour window, so channels don't all post at :00 (Buffer does the same)."""
    return int(hashlib.sha256(f"{channel_id}:{weekday}".encode()).hexdigest(), 16) % 12 * 5


def own_heatmap(s, channel_id: str | None, days: int = 180) -> dict:
    """Median engagement by weekday x hour (channel local time) from this channel's own sent posts."""
    qry = select(PostTarget).where(PostTarget.status == "sent", PostTarget.published_at >= utcnow() - timedelta(days=days))
    if channel_id:
        qry = qry.where(PostTarget.channel_id.in_(str(channel_id).split(",")))
    cells: dict[tuple[int, int], list[float]] = {}
    for t in s.scalars(qry):
        ins = t.insights or {}
        score = ins.get("engagement_rate")
        if score is None:
            score = (ins.get("likes") or 0) + 2 * (ins.get("comments") or 0) + 3 * (ins.get("shares") or 0) + 2 * (ins.get("saved") or 0) \
                + (ins.get("views") or 0) / 100
        local = scheduling.to_local(t.published_at, scheduling.tz(t.channel))
        cells.setdefault((local.weekday(), local.hour), []).append(float(score or 0))
    grid = [[None] * 24 for _ in range(7)]
    for (d, h), v in cells.items():
        grid[d][h] = {"score": round(statistics.median(v), 3), "posts": len(v)}
    return {"grid": grid, "posts": sum(len(v) for v in cells.values())}


def recommend(s, ch: Channel, goal: int) -> dict:
    goal = max(1, min(int(goal or 3), 21))
    heat = own_heatmap(s, ch.id)
    ranked, source = [], "research"
    if heat["posts"] >= 10:
        cells = [(c["score"], d, h) for d in range(7) for h in range(24) if (c := heat["grid"][d][h]) and c["posts"] >= 2]
        if len(cells) >= min(goal, 3):
            ranked, source = [(d, h) for _, d, h in sorted(cells, reverse=True)], "own_data"
    research = RESEARCH.get(ch.provider, RESEARCH["instagram"])
    ranked += [w for w in research if w not in ranked]
    picks, used = [], set()
    for d, h in ranked:                         # one per weekday first (spread the week, like Buffer)
        if len(picks) < goal and d not in used:
            picks.append((d, h))
            used.add(d)
    for d, h in ranked:                         # then allow a second slot on a day
        if len(picks) < goal and (d, h) not in picks:
            picks.append((d, h))
    while len(picks) < goal:                    # custom goals bigger than the research list: spread evenly
        d, h = len(picks) % 7, 9 + (len(picks) // 7) * 4
        if (d, h) not in picks:
            picks.append((d, h))
    picks.sort()
    times = [{"weekday": d, "day": DAYS[d], "start": f"{h:02d}:00", "end": f"{(h + 1) % 24:02d}:00",
              "time": f"{h:02d}:{_minute(ch.id, d):02d}"} for d, h in picks]
    return {"goal": goal, "times": times, "source": source, "own_posts": heat["posts"], "timezone": ch.timezone,
            "basis": "your own posts' engagement" if source == "own_data" else f"Buffer's 2026 analysis of 52M+ posts ({ch.provider})",
            "source_url": None if source == "own_data" else RESEARCH_SOURCE}


# --------------------------------------------------------------------------------------------- revisions / notes
def snapshot(core, s, post: Post, actor: str = "ui") -> None:
    last = s.scalar(select(func.max(Revision.rev)).where(Revision.post_id == post.id)) or 0
    s.add(Revision(post_id=post.id, rev=last + 1, actor=actor,
                   snapshot={"caption": post.caption, "media_ids": list(post.media_ids or []), "tags": list(post.tags or []),
                             "note": post.note, "campaign": post.campaign,
                             "targets": {t.id: {"caption_override": t.caption_override, "options": t.options or {}} for t in post.targets}}))


MENTION = re.compile(r"@([A-Za-z0-9_.\-]{2,40})")


def _media_dict(core, m: Media) -> dict:
    from ..services import media as media_svc
    d = media_svc.media_dict(core, m)
    d.update({"title": m.title or "", "tags": m.tags or [], "folder": m.folder or "", "phash": m.phash or ""})
    return d


def ahash(path) -> str:
    """64-bit average hash (near-duplicate detection survives resizing / recompression)."""
    from PIL import Image
    with Image.open(path) as im:
        px = list(im.convert("L").resize((8, 8)).getdata())
    avg = sum(px) / 64
    return f"{int(''.join('1' if p >= avg else '0' for p in px), 2):016x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1") if a and b else 64


def media_usage(s, mid: str) -> dict:
    like = f'%"{mid}"%'
    posts = [p for p in s.scalars(select(Post).where(Post.media_ids.like(like)))]
    over = [t for t in s.scalars(select(PostTarget).where(or_(PostTarget.media_override.like(like),
                                                              PostTarget.options.like(like))))]   # thread parts, thumbnails
    ideas = [i for i in s.scalars(select(Idea).where(Idea.media_ids.like(like)))]
    targets = {t.id: t for p in posts for t in p.targets if t.media_override is None} | {t.id: t for t in over}
    return {"uses": len(targets) + len(ideas), "posts": [{"id": t.id, "status": t.status, "channel_id": t.channel_id,
                                                         "caption": (t.caption or "")[:80]} for t in targets.values()],
            "ideas": [{"id": i.id, "title": i.title} for i in ideas]}


def filter_usage(usage: dict, visible: set | None, recount: bool = True) -> dict:
    """A copy of ``usage`` listing only posts on ``visible`` channels (None = no channel limit). ``recount`` sets "uses"
    to what is listed; without it the full count stays (media_delete blocks on the full usage, CodeRabbit 4 Nov 2026)."""
    if visible is None:
        return usage
    posts = [p for p in usage["posts"] if p["channel_id"] in visible]
    return {"uses": len(posts) + len(usage["ideas"]) if recount else usage["uses"], "posts": posts, "ideas": usage["ideas"]}


# --------------------------------------------------------------------------------------------- contacts
def contact_rows(s, q: str = "", channels: set | None = None) -> list[dict]:
    """People who commented, counted from their comments on `channels` only (None = every channel)."""
    rows = s.execute(only(select(Comment.author, func.count(), func.min(Comment.at), func.max(Comment.at),
                                 func.sum(func.iif(Comment.status == "open", 1, 0)), func.sum(func.iif(Comment.flagged, 1, 0)),
                                 func.sum(func.iif(Comment.text.like("%?%"), 1, 0)), func.group_concat(Comment.channel_id.distinct()))
                          .where(Comment.is_own.is_(False), Comment.author != ""), Comment.channel_id, channels)
                     .group_by(Comment.author)).all()
    extra = {c.handle: c for c in s.scalars(select(Contact))}
    out = []
    for author, n, first, last, open_, flagged, questions, chans in rows:
        c = extra.get(author)
        if q and q.lower() not in (author + " " + (c.notes if c else "") + " " + " ".join(c.tags if c else [])).lower():
            continue
        out.append({"id": c.id if c else "", "handle": author, "name": (c.name if c else "") or author, "comments": n,
                    "first_seen": posts_svc.iso(first), "last_seen": posts_svc.iso(last), "open": open_ or 0, "flagged": flagged or 0,
                    "questions": questions or 0, "channel_ids": (chans or "").split(","), "notes": c.notes if c else "",
                    "tags": c.tags if c else [], "assignee": c.assignee if c else ""})
    return sorted(out, key=lambda x: x["last_seen"] or "", reverse=True)


SEGMENTS = {
    "superfans": ("Superfans", "3+ comments", lambda c: c["comments"] >= 3),
    "new": ("New", "first comment in the last 7 days", lambda c: (c["first_seen"] or "") >= posts_svc.iso(utcnow() - timedelta(days=7))),
    "questions": ("Asked a question", "a comment with a '?'", lambda c: c["questions"] > 0),
    "unanswered": ("Waiting for you", "has an unanswered comment", lambda c: c["open"] > 0),
    "flagged": ("Flagged", "a flagged comment", lambda c: c["flagged"] > 0),
    "assigned": ("Assigned", "assigned to someone", lambda c: bool(c["assignee"])),
}


def _ensure_contact(s, handle: str) -> Contact:
    c = s.scalar(select(Contact).where(Contact.handle == handle))
    if not c:
        if not s.scalar(select(func.count()).select_from(Comment).where(Comment.author == handle)):
            raise HTTPException(404, "Contact not found")
        c = Contact(handle=handle, name=handle)
        s.add(c)
        s.flush()
    return c


# --------------------------------------------------------------------------------------------- routes
def add_content_routes(app: FastAPI, core) -> None:
    def channel(s, cid) -> Channel:
        ch = s.get(Channel, cid)
        if not ch:
            raise HTTPException(404, "Channel not found")
        return ch

    def actor(request: Request) -> str:
        tok = getattr(request.state, "token", None)
        return tok.name if tok else (getattr(request.state, "user_name", "") or "ui")

    # ---- onboarding (every network): confirm account -> posting goal -> recommended times -> saved as posting slots
    @app.get("/api/channels/{cid}/onboarding", tags=["onboarding"])
    def onboarding_get(cid: str):
        with core.db.session() as s:
            ch = channel(s, cid)
            ob = (ch.meta or {}).get("onboarding") or {}
            conn = core.connectors.get(ch.provider)
            return {"channel": {"id": ch.id, "provider": ch.provider, "name": ch.connector_name if hasattr(ch, "connector_name") else ch.provider,
                                "username": ch.username, "display_name": ch.display_name or ch.username, "avatar_url": ch.avatar_url,
                                "type": {"youtube": "Channel", "instagram": (ch.meta or {}).get("account_type") or "Business account",
                                         "x": "Profile", "tiktok": "Account", "linkedin": "Profile", "linkedin_page": "Page"}.get(ch.provider, "Account"),
                                "followers_count": (ch.meta or {}).get("followers_count"), "status": ch.status,
                                "network": conn.describe().get("name") if conn else ch.provider},
                    "done": bool(ob.get("done")), "goal": ob.get("goal"), "goals": GOALS, "default_goal": 3,
                    "has_slots": bool(ch.slots), "recommended": recommend(s, ch, ob.get("goal") or 3)}

    @app.post("/api/channels/{cid}/onboarding/recommend", tags=["onboarding"])
    def onboarding_recommend(cid: str, payload: dict = Body(default={})):
        with core.db.session() as s:
            return recommend(s, channel(s, cid), payload.get("goal") or 3)

    @app.post("/api/channels/{cid}/onboarding/finish", tags=["onboarding"])
    def onboarding_finish(cid: str, request: Request, payload: dict = Body(default={})):
        """Save the chosen times as the channel's posting schedule (they show as slots in the queue and calendar)."""
        with core.db.session() as s:
            ch = channel(s, cid)
            goal = int(payload.get("goal") or 3)
            times = payload.get("times")
            if times is None:
                times = recommend(s, ch, goal)["times"]
            slots = [{"weekday": int(t["weekday"]), "time": t["time"]} for t in times if t.get("enabled", True)]
            if payload.get("skip"):
                slots = [{"weekday": x.weekday, "time": x.time} for x in ch.slots]
            scheduling.set_slots(s, ch, slots)
            per_day = max([sum(1 for x in slots if x["weekday"] == d) for d in range(7)] or [1])
            ch.daily_limit = max(ch.daily_limit or 0, per_day)
            ch.meta = {**(ch.meta or {}), "onboarding": {"done": True, "goal": goal, "at": posts_svc.iso(utcnow()), "by": actor(request)}}
            s.flush()
            scheduling.reflow(s, ch, utcnow())
            record(core, s, f"Posting schedule set: {len(slots)} times a week", channel_id=ch.id)
            return {"ok": True, "goal": goal, "slots": [{"weekday": x.weekday, "time": x.time} for x in sorted(ch.slots, key=lambda z: (z.weekday, z.time))]}

    @app.get("/api/channels/{cid}/playlists", tags=["channels"])
    def channel_playlists(cid: str):
        """The channel's own playlists (YouTube playlists.list mine=true) for the composer's 'Add to playlist'."""
        with core.db.session() as s:
            ch = channel(s, cid)
            conn = core.connectors.get(ch.provider)
            if not conn or not hasattr(conn, "list_playlists"):
                return []
            try:
                return conn.list_playlists(core.channel_auth(ch))
            except Exception as e:   # noqa: BLE001 - surface as an empty list with a reason
                raise HTTPException(502, f"Could not load playlists: {getattr(e, 'message', e)}") from e

    @app.get("/api/insights/best-times", tags=["insights"])
    def best_times(request: Request, channel_id: str | None = None, days: int = 180):
        channel_id = narrow(request, channel_id)   # a scoped token / restricted teammate: only its channels (4 Nov 2026)
        with core.db.session() as s:
            if channel_id == NO_CHANNEL:
                chans = []
            else:
                chans = [channel(s, c) for c in channel_id.split(",")] if channel_id else list(s.scalars(select(Channel)))
            heat = own_heatmap(s, channel_id, days)
            return {"heatmap": heat, "channels": [{"id": c.id, "provider": c.provider, "username": c.username,
                                                    **recommend(s, c, max(3, len(c.slots) or 3))} for c in chans],
                    "research_source": RESEARCH_SOURCE}

    # ---- human approvals
    @app.post("/api/targets/{tid}/request-approval", tags=["approvals"])
    def request_approval(tid: str, request: Request, payload: dict = Body(default={})):
        with core.db.session() as s:
            t = s.get(PostTarget, tid)
            if not t:
                raise HTTPException(404, "Post not found")
            if t.status in ("sent", "sending", "processing"):
                raise HTTPException(409, "Already sent")
            who = actor(request)
            posts_svc.hold_for_approval(t, payload.get("when") or "queue", payload.get("scheduled_at"), False, payload.get("timezone"), who)
            t.approval = {**t.approval, "kind": "human", "note": str(payload.get("note") or "")[:1000]}
            # a teammate's request already notified each approver (teams.notify_approvers); an agent's goes to everyone
            record(core, s, f"{who} asked for approval" + (f": {t.approval['note']}" if t.approval["note"] else ""),
                   target_id=t.id, channel_id=t.channel_id, notify=not t.approval.get("requested_by_user_id"), kind="approval")
            s.flush()
            return posts_svc.serialize_target(core, t)

    # ---- revisions
    @app.get("/api/posts/{post_id}/revisions", tags=["revisions"])
    def revisions(post_id: str):
        with core.db.session() as s:
            return [{"rev": r.rev, "at": posts_svc.iso(r.at), "actor": r.actor, "snapshot": r.snapshot}
                    for r in s.scalars(select(Revision).where(Revision.post_id == post_id).order_by(Revision.rev.desc()))]

    @app.post("/api/posts/{post_id}/revisions/{rev}/restore", tags=["revisions"])
    def revision_restore(post_id: str, rev: int, request: Request):
        with core.db.session() as s:
            r = s.scalar(select(Revision).where(Revision.post_id == post_id, Revision.rev == rev))
            p = s.get(Post, post_id)
            if not r or not p:
                raise HTTPException(404, "Revision not found")
            snapshot(core, s, p, actor(request))
            snap = r.snapshot
            p.caption, p.media_ids, p.tags = snap.get("caption", ""), snap.get("media_ids", []), snap.get("tags", [])
            p.note, p.campaign = snap.get("note", p.note), snap.get("campaign", p.campaign)
            for t in p.targets:
                ts = (snap.get("targets") or {}).get(t.id)
                if ts:
                    t.caption_override, t.options = ts.get("caption_override"), ts.get("options") or {}
            s.flush()
            return {"ok": True, "restored": rev, "targets": [posts_svc.serialize_target(core, t) for t in p.targets]}

    # ---- internal notes with @mentions
    def note_dict(n: PostNote) -> dict:
        return {"id": n.id, "author": n.author, "author_id": n.author_id, "text": n.text, "mentions": n.mentions, "at": posts_svc.iso(n.at)}

    @app.get("/api/posts/{post_id}/notes", tags=["notes"])
    def notes(post_id: str):
        with core.db.session() as s:
            return [note_dict(n) for n in s.scalars(select(PostNote).where(PostNote.post_id == post_id).order_by(PostNote.at))]

    @app.delete("/api/posts/{post_id}/notes/{note_id}", tags=["notes"])
    def note_delete(post_id: str, note_id: str, request: Request):
        me = getattr(request.state, "user", None)
        with core.db.session() as s:
            n = s.get(PostNote, note_id)
            if not n or n.post_id != post_id:
                raise HTTPException(404, "Note not found")
            if me and not me.manager and n.author_id != me.id:
                raise HTTPException(403, "You can only delete your own comments")
            s.delete(n)
        return {"ok": True}

    @app.post("/api/posts/{post_id}/notes", tags=["notes"])
    def note_add(post_id: str, request: Request, payload: dict = Body(...)):
        text = str(payload.get("text") or "").strip()[:4000]
        if not text:
            raise HTTPException(400, "Write a note")
        with core.db.session() as s:
            if not s.get(Post, post_id):
                raise HTTPException(404, "Post not found")
            who = actor(request)
            mentions = sorted(set(MENTION.findall(text)))
            me = getattr(request.state, "user", None)
            n = PostNote(post_id=post_id, author=who, author_id=me.id if me else None, text=text, mentions=mentions)
            s.add(n)
            p = s.get(Post, post_id)
            # @mentions notify the matching teammates only; everyone else in the conversation gets a "comment" notification
            hit = notify_mentions(s, text, who, next((t.id for t in p.targets), None), me.id if me else None)
            notify_note(s, p, text, who, me.id if me else None, hit)
            s.flush()
            return note_dict(n)

    # ---- media library
    @app.get("/api/media-folders", tags=["media"])
    def media_folders():
        with core.db.session() as s:
            rows = s.execute(select(Media.folder, func.count()).where(Media.deleted_at.is_(None)).group_by(Media.folder)).all()
            tags: dict[str, int] = {}
            for (tg,) in s.execute(select(Media.tags).where(Media.deleted_at.is_(None))):
                for x in tg or []:
                    tags[x] = tags.get(x, 0) + 1
        return {"folders": [{"name": f or "", "count": n} for f, n in sorted(rows, key=lambda r: r[0] or "")],
                "tags": [{"name": k, "count": v} for k, v in sorted(tags.items())]}

    @app.get("/api/media/{mid}/usage", tags=["media"])
    def media_usage_route(mid: str, request: Request):
        with core.db.session() as s:
            if not s.get(Media, mid):
                raise HTTPException(404, "Media not found")
            out = media_usage(s, mid)
        return filter_usage(out, visible(request))   # channel-limited token / teammate

    @app.post("/api/media/{mid}/edit", tags=["media"])
    def media_edit(mid: str, payload: dict = Body(...)):
        """Image editor: rotate, flip, crop / aspect, resize, colours, text. Saves a new copy (or previews with preview=true)."""
        from ..services import image_edit
        with core.db.session() as s:
            m = s.get(Media, mid)
            if not m or m.deleted_at:
                raise HTTPException(404, "Media not found")
            s.expunge(m)
        if payload.get("preview"):
            return image_edit.preview(core, m, payload)
        return {"media": _media_dict(core, image_edit.edit(core, m, payload)), "source_id": mid}

    @app.get("/api/media/unsplash/search", tags=["media"])
    def unsplash_search(q: str = "", page: int = 1, orientation: str = ""):
        from ..services import unsplash
        return unsplash.search(core, q, page, orientation=orientation)

    @app.post("/api/media/unsplash/import", tags=["media"])
    def unsplash_import(payload: dict = Body(...)):
        from ..services import unsplash
        r = unsplash.import_photo(core, str(payload.get("id") or ""))
        return {**r, "media": _media_dict(core, r["media"])}

    app.state.media_dict = lambda m: _media_dict(core, m)

    def list_media(q="", folder=None, tag="", kind="", unused=False, duplicates=False, limit=500):
        with core.db.session() as s:
            qry = select(Media).where(Media.deleted_at.is_(None))
            if q:
                qry = qry.where(or_(Media.filename.ilike(f"%{q}%"), Media.title.ilike(f"%{q}%"), Media.alt_text.ilike(f"%{q}%"),
                                    Media.tags.like(f"%{q}%")))
            if folder is not None and folder != "*":
                qry = qry.where(Media.folder == folder)
            if tag:
                qry = qry.where(Media.tags.like(f'%"{tag}"%'))
            if kind:
                qry = qry.where(Media.kind == kind)
            rows = list(s.scalars(qry.order_by(Media.created_at.desc()).limit(min(limit, 2000))))
            for m in rows:   # lazily fill the near-duplicate hash for images
                if m.kind == "image" and not m.phash:
                    try:
                        m.phash = ahash(core.settings.media_dir / m.stored_name)
                    except Exception:
                        m.phash = "-"
            out = []
            for m in rows:
                d = _media_dict(core, m)
                d["uses"] = media_usage(s, m.id)["uses"]
                out.append(d)
        if unused:
            out = [d for d in out if not d["uses"]]
        if duplicates:
            groups: dict[str, list] = {}
            for d in out:
                key = d["sha256"] or d["id"]
                near = next((g for g, items in groups.items() if d["phash"] not in ("", "-") and
                             any(hamming(d["phash"], x["phash"]) <= 5 for x in items if x["phash"] not in ("", "-"))), None)
                groups.setdefault(near or key, []).append(d)
            out = [dict(d, duplicate_group=g) for g, items in groups.items() if len(items) > 1 for d in items]
        return out

    app.state.list_media = list_media

    # ---- contacts
    @app.get("/api/contacts", tags=["contacts"])
    def contacts(request: Request, q: str = "", segment: str = "", tag: str = "", assignee: str | None = None):
        with core.db.session() as s:
            rows = contact_rows(s, q, visible(request))
        if segment in SEGMENTS:
            rows = [c for c in rows if SEGMENTS[segment][2](c)]
        if tag:
            rows = [c for c in rows if tag in c["tags"]]
        if assignee is not None:
            rows = [c for c in rows if c["assignee"] == assignee]
        return rows

    @app.get("/api/contact-segments", tags=["contacts"])
    def contact_segments(request: Request):
        with core.db.session() as s:
            rows = contact_rows(s, "", visible(request))
        return [{"id": k, "label": v[0], "rule": v[1], "count": sum(1 for c in rows if v[2](c))} for k, v in SEGMENTS.items()]

    @app.get("/api/contacts/{key}", tags=["contacts"])
    def contact_get(key: str, request: Request):
        vis = visible(request)          # someone seen only on a hidden channel answers 404, like a missing contact
        with core.db.session() as s:
            c = s.get(Contact, key)
            handle = c.handle if c else key
            row = next((r for r in contact_rows(s, "", vis) if r["handle"] == handle), None)
            if not row:
                raise HTTPException(404, "Contact not found")
            hist = [{"id": x.id, "text": x.text, "at": posts_svc.iso(x.at), "status": x.status, "channel_id": x.channel_id,
                     "provider": ch.provider, "media_caption": (x.media_caption or "")[:100], "media_permalink": x.media_permalink, "flagged": x.flagged}
                    for x, ch in s.execute(only(select(Comment, Channel).join(Channel, Channel.id == Comment.channel_id)
                                                .where(Comment.author == handle), Comment.channel_id, vis)
                                           .order_by(Comment.at.desc()))]
            return {**row, "history": hist}

    @app.patch("/api/contacts/{key}", tags=["contacts"])
    def contact_patch(key: str, request: Request, payload: dict = Body(...)):
        vis = visible(request)
        who = getattr(request.state, "user", None)
        limited = who is not None and not who.manager
        with core.db.session() as s:
            if vis is not None or limited:
                c0 = s.get(Contact, key)
                handle = c0.handle if c0 else key
                row = next((r for r in contact_rows(s, "", vis) if r["handle"] == handle), None)
                if not row:         # a channel-limited caller edits only people who commented on a channel it can see
                    raise HTTPException(404, "Contact not found")
                # the guard can't map a contact to channels, so the per-channel role is checked here: a teammate who is
                # only a viewer on every channel this person commented on can read the contact but not edit it
                # (CodeRabbit on #102, CWE-862)
                if limited and not any(teams.RANK.get(who.channel_role(c), 0) >= teams.RANK["contributor"]
                                       for c in row["channel_ids"] if c):
                    raise HTTPException(403, "On this contact's channels you have read-only access")
            c = s.get(Contact, key) or _ensure_contact(s, key)
            for k in ("notes", "name", "assignee"):
                if k in payload:
                    setattr(c, k, str(payload[k] or "")[:4000])
            if "tags" in payload:
                c.tags = sorted({str(x).strip()[:40] for x in payload["tags"] or [] if str(x).strip()})
            s.flush()
            return {"id": c.id, "handle": c.handle, "name": c.name, "notes": c.notes, "tags": c.tags, "assignee": c.assignee}

    # ---- markdown report with period comparison
    @app.get("/api/reports/report.md", tags=["reports"], response_class=PlainTextResponse)
    def report_md(request: Request, days: str | None = "30", start: str | None = None, end: str | None = None,
                  channel_id: str | None = None):
        try:
            md = build_report(core, days, start, end, narrow(request, channel_id))["markdown"]
        except ValueError as e:              # days=abc, or an end before the start: the caller's mistake, not a 500
            raise HTTPException(400, str(e)) from e
        return PlainTextResponse(md, media_type="text/markdown; charset=utf-8")

    app.state.build_report = lambda *a: build_report(core, *a)


METRICS = ("posts", "views", "reach", "likes", "comments", "shares", "saved")


def in_window(column, lo, hi, *, end_inclusive: bool):
    """lo <= column <= hi for the current period (a post published at exactly "now" counts); lo <= column < hi for the
    previous period, so a post on the boundary between the two periods is counted once, in the current one."""
    return (column >= lo, column <= hi) if end_inclusive else (column >= lo, column < hi)


def report_window(core, days="30", start=None, end=None, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The report's (lo, hi) as naive UTC datetimes at full precision, the same type published_at is stored as.

    Callers must compare against these values, never a re-parse of the iso() strings in the response: iso() keeps whole
    seconds, so a re-parsed hi lands up to 999 ms before now and drops a post published in that same second (the flaky
    tests/e2e/test_ui_reports.py::test_01_views, 4 Nov 2026). A date-only `end` (the Custom range's date picker) keeps
    its existing meaning (midnight at the start of that day), the same bound /api/insights uses."""
    now = now or utcnow()
    tzname = core.prefs()["timezone"]
    if start:
        lo = posts_svc.parse_when(start, tzname)
        hi = posts_svc.parse_when(end, tzname) if end else now
    elif days == "mtd":
        lo, hi = datetime(now.year, now.month, 1), now
    else:
        lo, hi = now - timedelta(days=int(days or 30)), now
    if hi < lo:
        raise ValueError("The report's end is before its start")
    return lo, hi


def _period(core, lo, hi, channel_id, end_inclusive: bool = True):
    with core.db.session() as s:
        qry = select(PostTarget).where(PostTarget.status == "sent", *in_window(PostTarget.published_at, lo, hi, end_inclusive=end_inclusive))
        if channel_id:
            qry = qry.where(PostTarget.channel_id.in_(channel_id.split(",")))
        rows = [posts_svc.serialize_target(core, t) for t in s.scalars(qry)]
    k = {"posts": len(rows)}
    for m in METRICS[1:]:
        k[m] = sum(int((r["insights"] or {}).get(m) or 0) for r in rows)
    rates = [r["insights"]["engagement_rate"] for r in rows if (r["insights"] or {}).get("engagement_rate") is not None]
    k["engagement_rate"] = round(sum(rates) / len(rates), 2) if rates else None
    return k, rows


def build_report(core, days="30", start=None, end=None, channel_id=None, now: datetime | None = None) -> dict:
    now = now or utcnow()
    lo, hi = report_window(core, days, start, end, now)
    span = hi - lo
    cur, rows = _period(core, lo, hi, channel_id)
    prev, _ = _period(core, lo - span, lo, channel_id, end_inclusive=False)

    def delta(a, b):
        if a is None or b in (None, 0):
            return None
        return round(100 * (a - b) / b, 1)
    kpis = [{"metric": m, "current": cur[m], "previous": prev[m], "change_pct": delta(cur[m], prev[m])} for m in (*METRICS, "engagement_rate")]
    brand = core.prefs().get("cta_handle") or "HuhQueue"
    top = sorted(rows, key=lambda r: (r["insights"] or {}).get("views") or (r["insights"] or {}).get("likes") or 0, reverse=True)[:10]
    md = [f"# {brand} social report", "", f"**Period:** {lo:%d %b %Y} – {hi:%d %b %Y} (compared with the previous {span.days or 1} days)", "",
          "| Metric | This period | Previous | Change |", "|---|---:|---:|---:|"]
    for k in kpis:
        ch = "–" if k["change_pct"] is None else f"{k['change_pct']:+.1f}%"
        md.append(f"| {k['metric'].replace('_', ' ').capitalize()} | {k['current'] if k['current'] is not None else '–'} | "
                  f"{k['previous'] if k['previous'] is not None else '–'} | {ch} |")
    md += ["", "## Top posts", "", "| Published | Channel | Post | Views | Likes |", "|---|---|---|---:|---:|"]
    for r in top:
        i = r["insights"] or {}
        cap = (r["caption"] or "").replace("|", "/").replace("\n", " ")[:70]
        md.append(f"| {(r['published_at'] or '')[:10]} | {r['channel']['provider']} @{r['channel']['username']} | {cap} | {i.get('views') or 0} | {i.get('likes') or 0} |")
    md += ["", f"_Generated by HuhQueue on {now:%d %b %Y %H:%M} UTC._"]
    return {"range": [posts_svc.iso(lo), posts_svc.iso(hi)], "previous_range": [posts_svc.iso(lo - span), posts_svc.iso(lo)], "kpis": kpis,
            "top_posts": top, "markdown": "\n".join(md), "brand": brand,
            "window": (lo, hi, lo - span)}   # full-precision bounds for report_data; not JSON, never returned as-is


# --------------------------------------------------------------------------------------------- registry entries
for _a in (
    Action("onboarding.get", "Channel onboarding: confirm account, goals, recommended times", "GET", "/api/channels/{id}/onboarding",
           {"id": S}, ("id",), ui="After connecting a channel"),
    Action("onboarding.recommend", "Recommended posting times for a weekly goal", "POST", "/api/channels/{id}/onboarding/recommend",
           {"id": S, "goal": {**I, "description": "posts per week: 1, 3, 5 or any number"}}, ("id",),
           examples=("recommend 5 posting times a week for youtube",), ui="Onboarding › posting goal"),
    Action("onboarding.finish", "Save posting times as the channel's schedule", "POST", "/api/channels/{id}/onboarding/finish",
           {"id": S, "goal": I, "times": {"type": "array", "items": {"type": "object", "properties": {"weekday": I, "time": S, "enabled": B}}},
            "skip": B}, ("id",), "admin", "write", ui="Onboarding › Got it"),
    Action("channel.playlists", "List a YouTube channel's playlists", "GET", "/api/channels/{id}/playlists", {"id": S}, ("id",), ui="Composer › YouTube › Playlist"),
    Action("channel.playlists", "List a YouTube channel's playlists", "GET", "/api/channels/{id}/playlists", {"id": S}, ("id",), ui="Composer › YouTube › Playlist"),
    Action("channel.best_times", "Best time to post from your own data (and research)", "GET", "/api/insights/best-times",
           {"channel_id": S, "days": I}, examples=("when should I post?",), ui="Insights › Best times / Onboarding"),
    Action("post.request_approval", "Submit a post for approval", "POST", "/api/targets/{id}/request-approval",
           {"id": S, "when": WHEN, "scheduled_at": S, "note": S}, ("id",), "write", "write", ui="Composer › Request approval"),
    Action("post.revisions", "Version history of a post", "GET", "/api/posts/{post_id}/revisions", {"post_id": S}, ("post_id",), ui="Composer › History"),
    Action("post.revision_restore", "Restore an earlier version", "POST", "/api/posts/{post_id}/revisions/{rev}/restore",
           {"post_id": S, "rev": I}, ("post_id", "rev"), "write", "write", ui="Composer › History"),
    Action("post.notes", "Internal notes on a post", "GET", "/api/posts/{post_id}/notes", {"post_id": S}, ("post_id",), ui="PostCard › Comments"),
    Action("post.note_delete", "Delete a comment on a post (your own; owners/admins any)", "DELETE", "/api/posts/{post_id}/notes/{note_id}",
           {"post_id": S, "note_id": S}, ("post_id", "note_id"), "write", "destructive", ui="PostCard › Comments"),
    Action("post.note_add", "Add an internal note (@mention teammates)", "POST", "/api/posts/{post_id}/notes", {"post_id": S, "text": S},
           ("post_id", "text"), "write", "write", ui="Composer › Notes"),
    Action("media.usage", "Where a media item is used", "GET", "/api/media/{id}/usage", {"id": S}, ("id",), ui="Library › asset drawer"),
    Action("media.folders", "Media folders and tags", "GET", "/api/media-folders", ui="Library"),
    Action("contact.list", "List contacts (people who comment)", "GET", "/api/contacts", {"q": S, "segment": S, "tag": S, "assignee": S},
           examples=("show my superfans",), ui="Contacts"),
    Action("contact.get", "Contact profile with comment history", "GET", "/api/contacts/{id}", {"id": S}, ("id",), ui="Contacts › profile"),
    Action("contact.update", "Edit a contact (notes, tags, assignee)", "PATCH", "/api/contacts/{id}",
           {"id": S, "notes": S, "tags": SA, "assignee": S, "name": S}, ("id",), "comments", "write", ui="Contacts › profile"),
    Action("contact.segments", "Contact segments with counts", "GET", "/api/contact-segments", ui="Contacts"),
    Action("report.markdown", "Report as markdown with period comparison", "GET", "/api/reports/report.md",
           {"days": S, "start": S, "end": S, "channel_id": S}, ui="Reports › Markdown"),
    Action("nav.library", "Go to Media library", nav="#/library", mcp=False, ui="⌘K"),
    Action("nav.contacts", "Go to Contacts", nav="#/contacts", mcp=False, ui="⌘K"),
):
    reg.register(_a)
