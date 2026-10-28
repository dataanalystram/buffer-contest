"""Community inbox: pull comments through each network's official comment API, reply, resolve."""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from ..connectors import ConnectorError
from ..models import Channel, Comment
from .events import record

log = logging.getLogger("huhqueue.community")


def sync_channel(core, channel_id: str) -> int:
    with core.db.session() as s:
        ch = s.get(Channel, channel_id)
        conn = core.connectors.get(ch.provider) if ch else None
        if not ch or not conn or not conn.capabilities.get("comments") or ch.status != "active":
            return 0
        try:
            rows = conn.list_comments(core.channel_auth(ch))
        except ConnectorError as e:
            record(core, s, f"Comment sync for {ch.username} failed: {e.message}", level="warning", channel_id=ch.id)
            return 0
        if conn.capabilities.get("mentions"):
            try:
                rows += [{**r, "kind": "mention"} for r in conn.list_mentions(core.channel_auth(ch))]
            except ConnectorError as e:   # mentions are a bonus: never fail the comment sync over them
                record(core, s, f"Mentions sync for {ch.username} failed: {e.message}", level="warning", channel_id=ch.id)
        note = getattr(conn, "last_comments_note", "") or ""
        if note != (ch.meta or {}).get("comments_note", ""):
            ch.meta = {**(ch.meta or {}), "comments_note": note}
            if note:
                record(core, s, f"Comments for {ch.username}: {note}", level="warning", channel_id=ch.id)
        existing = {c.external_id: c for c in s.scalars(select(Comment).where(Comment.channel_id == ch.id))}
        replied_parents = {r["parent_external_id"] for r in rows if r.get("is_own") and r.get("parent_external_id")}
        n = 0
        for r in rows:
            c = existing.get(r["external_id"])
            if not c:
                c = Comment(channel_id=ch.id, external_id=r["external_id"])
                s.add(c)
                n += 1
            for k in ("parent_external_id", "media_external_id", "media_permalink", "media_caption", "author", "text",
                      "at", "like_count", "is_own"):
                setattr(c, k, r.get(k) if r.get(k) is not None else getattr(c, k))
            c.kind = r.get("kind") or c.kind or "comment"
            c.synced_at = datetime.utcnow()
            if c.external_id in replied_parents and c.status == "open":
                c.status = "replied"
        return n


def sync_all(core) -> int:
    with core.db.session() as s:
        ids = [c.id for c in s.scalars(select(Channel).where(Channel.status == "active"))]
    return sum(sync_channel(core, i) for i in ids)


def reply(core, comment_id: str, text: str) -> dict:
    if not (text or "").strip():
        raise ValueError("Write a reply first")
    with core.db.session() as s:
        c = s.get(Comment, comment_id)
        if not c:
            raise KeyError("Comment not found")
        ch = s.get(Channel, c.channel_id)
        conn = core.connector(ch.provider)
        target = c.parent_external_id or c.external_id  # IG/YT reply to the thread's top comment
        ext = conn.reply_comment(core.channel_auth(ch), target, text.strip(), c.media_external_id)
        mine = Comment(channel_id=ch.id, external_id=ext or f"local-{datetime.utcnow().timestamp()}",
                       parent_external_id=target, media_external_id=c.media_external_id,
                       media_permalink=c.media_permalink, media_caption=c.media_caption, author=ch.username,
                       text=text.strip(), at=datetime.utcnow(), is_own=True, status="replied")
        s.add(mine)
        c.status = "replied"
        record(core, s, f"Replied to @{c.author} on {ch.provider}", channel_id=ch.id)
        return serialize(c, ch)


def serialize(c: Comment, ch: Channel) -> dict:
    return {"id": c.id, "channel_id": c.channel_id, "provider": ch.provider, "channel_username": ch.username,
            "external_id": c.external_id, "parent_external_id": c.parent_external_id,
            "media_external_id": c.media_external_id, "media_permalink": c.media_permalink,
            "media_caption": c.media_caption, "author": c.author, "text": c.text,
            "at": c.at.isoformat() + "Z" if c.at else None, "like_count": c.like_count, "is_own": c.is_own,
            "status": c.status, "flagged": bool(c.flagged), "assignee": c.assignee or "", "kind": c.kind or "comment"}


def sync_mentions(core, channel_id: str) -> int:
    """Pull only mentions (the Mentions tab's own refresh button)."""
    with core.db.session() as s:
        ch = s.get(Channel, channel_id)
        conn = core.connectors.get(ch.provider) if ch else None
        if not ch or not conn or not conn.capabilities.get("mentions") or ch.status != "active":
            return 0
        try:
            rows = conn.list_mentions(core.channel_auth(ch))
        except ConnectorError as e:
            record(core, s, f"Mentions sync for {ch.username} failed: {e.message}", level="warning", channel_id=ch.id)
            raise
        existing = {c.external_id: c for c in s.scalars(select(Comment).where(Comment.channel_id == ch.id))}
        n = 0
        for r in rows:
            c = existing.get(r["external_id"])
            if not c:
                c = Comment(channel_id=ch.id, external_id=r["external_id"], kind="mention")
                s.add(c)
                n += 1
            for k in ("media_external_id", "media_permalink", "media_caption", "author", "text", "at", "like_count"):
                if r.get(k) is not None:
                    setattr(c, k, r[k])
            c.kind, c.is_own, c.synced_at = "mention", False, datetime.utcnow()
        return n


# ------------------------------------------------------------------------------------------------ bulk + AI + to-post
def saved_reply_text(core, s, saved_reply_id: str) -> str:
    from ..models import KV
    row = s.get(KV, "lib:saved_replies")
    items = row.value.get("items", []) if row else []
    if not row:
        from ..api.app import DEFAULT_LIBRARY
        items = DEFAULT_LIBRARY["saved_replies"]
    hit = next((i for i in items if i.get("id") == saved_reply_id), None)
    if not hit:
        raise KeyError("Saved reply not found")
    return hit.get("text", "")


def fill_reply(text: str, c: Comment) -> str:
    """Saved replies may use {author} / {name}; a reply that doesn't start with the handle gets '@author ' in front."""
    out = (text or "").replace("{author}", c.author or "").replace("{name}", c.author or "").strip()
    if c.author and not out.lower().startswith(f"@{c.author.lower()}"):
        out = f"@{c.author} {out}"
    return out


def bulk(core, ids: list[str], action: str, text: str = "", saved_reply_id: str = "") -> dict:
    """resolve | reopen | reply (with text or a saved reply) for many comments. Replies go one by one; a failure on one
    comment is reported and the rest carry on."""
    ids = [i for i in dict.fromkeys(ids or []) if i][:200]
    if not ids:
        raise ValueError("Pick at least one comment")
    if action not in ("resolve", "reopen", "reply"):
        raise ValueError("action must be resolve, reopen or reply")
    results: list[dict] = []
    if action in ("resolve", "reopen"):
        with core.db.session() as s:
            for c in s.scalars(select(Comment).where(Comment.id.in_(ids), Comment.is_own.is_(False))):
                c.status = "resolved" if action == "resolve" else "open"
                results.append({"id": c.id, "ok": True, "status": c.status})
        return {"action": action, "ok": len(results), "failed": 0, "results": results}
    with core.db.session() as s:
        base = saved_reply_text(core, s, saved_reply_id) if saved_reply_id else text
        if not (base or "").strip():
            raise ValueError("Write a reply or pick a saved reply")
        rows = {c.id: fill_reply(base, c) for c in s.scalars(select(Comment).where(Comment.id.in_(ids), Comment.is_own.is_(False)))}
    for cid in ids:
        if cid not in rows:
            results.append({"id": cid, "ok": False, "error": "Comment not found"})
            continue
        try:
            reply(core, cid, rows[cid])
            results.append({"id": cid, "ok": True, "status": "replied"})
        except (ConnectorError, ValueError, KeyError) as e:
            results.append({"id": cid, "ok": False, "error": getattr(e, "message", None) or str(e)})
    ok = sum(r["ok"] for r in results)
    return {"action": action, "ok": ok, "failed": len(results) - ok, "results": results}


TONES = ("friendly", "professional", "playful", "short")


def rule_suggestions(c: Comment, brand: str) -> list[str]:
    t = (c.text or "").strip()
    at = f"@{c.author} " if c.author else ""
    low = t.lower()
    if "?" in t:
        return [f"{at}Great question! Short answer: … - we'll cover it in more detail soon, so stay tuned 👀",
                f"{at}Good one - check the caption/first comment for the source. Anything else you want us to cover?",
                f"{at}Thanks for asking! DM us and we'll send you the details 🙌"]
    if any(w in low for w in ("wrong", "bad", "fake", "hate", "worst", "misleading", "clickbait")):
        return [f"{at}Thanks for the honest feedback - what did we get wrong? We'll fix it.",
                f"{at}Fair point, appreciate you calling it out. We'll double-check the sources.",
                f"{at}Sorry it missed the mark! Tell us what you'd like to see instead."]
    return [f"{at}Thank you! 🙌 Glad you liked it.",
            f"{at}Appreciate it! Follow @{brand} for more like this 💜" if brand else f"{at}Appreciate it! More coming soon 💜",
            f"{at}Thanks for watching! What should we cover next?"]


def suggest_reply(core, comment_id: str, tone: str = "friendly", transport=None) -> dict:
    """AI reply suggestions: the configured LLM provider (Settings -> AI) when there is one, otherwise rule-based
    suggestions. Never posts anything - the user picks, edits and sends."""
    from . import assistant
    tone = tone if tone in TONES else "friendly"
    with core.db.session() as s:
        c = s.get(Comment, comment_id)
        if not c:
            raise KeyError("Comment not found")
        ch = s.get(Channel, c.channel_id)
        brand, author, text, caption, kind = ch.username, c.author, c.text, c.media_caption, c.kind or "comment"
        fallback = rule_suggestions(c, brand)
    if assistant.configured(core):
        system = ("You write replies to social media comments for the account @" + brand + ". Reply in the comment's language, "
                  f"tone: {tone}. Max 220 characters each, no hashtags, at most one emoji, never invent facts or links. "
                  'Return JSON only: {"replies": ["...", "...", "..."]} with 3 different options.')
        user = (f"{'Post that tagged us' if kind == 'mention' else 'Our post'}: {caption[:400]}\n"
                f"@{author} wrote: {text[:600]}")
        try:
            raw = assistant.chat(core, system, user, transport=transport)
        except assistant.LLMError as e:
            return {"source": "rules", "tone": tone, "suggestions": fallback, "note": f"AI provider failed ({e}); showing quick replies."}
        try:
            out = [str(x).strip()[:300] for x in (assistant._json_from(raw).get("replies") or []) if str(x).strip()][:3]
        except assistant.LLMError:
            out = [raw.strip()[:300]] if raw.strip() else []   # a model that answered in plain text
        if out:
            return {"source": "llm", "tone": tone, "suggestions": out}
    return {"source": "rules", "tone": tone, "suggestions": fallback,
            "note": "" if assistant.configured(core) else "Add an AI provider in Settings -> AI for written suggestions."}


def comment_post_caption(c: Comment) -> str:
    """'Turn comment into post': quote the comment and credit the author."""
    quote = " ".join((c.text or "").split())
    return f"💬 “{quote}”\n- @{c.author}\n\n" if c.author else f"💬 “{quote}”\n\n"
