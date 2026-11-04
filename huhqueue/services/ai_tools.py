"""Small AI helpers Buffer gives on its free plan: idea generation, image alt text and comment insights (themes).

Each uses the AI provider from Settings › AI (services/assistant.chat). Ideas and comment insights fall back to plain
rules when no provider is set, so they always answer; alt text needs a vision-capable model.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import timedelta

from sqlalchemy import select

from ..models import Channel, Comment, Idea, Media, utcnow
from . import assistant

ANGLES = ("3 things to know about {t}", "Myth vs fact: {t}", "{t}, explained in 30 seconds", "What most people get wrong about {t}",
          "Before and after: {t}", "Behind the scenes: how we cover {t}", "Hot take: {t}", "Your questions about {t}, answered",
          "A beginner's checklist for {t}", "The one {t} stat that surprised us")
STOP = set("""a an the and or but if of to in on at for with from by is are was were be been it its this that these those
you your yours i me my we our us they them their he she his her not no yes so very just too can will would should could
do does did have has had what when where why how who which all any more most some such than then there here about into
out up down over also as get got like love lol ok okay im dont thats really much one two please thanks thank""".split())


def generate_ideas(core, topic: str, audience: str = "", count: int = 5, transport=None) -> dict:
    topic = (topic or "").strip()[:200]
    if not topic:
        raise ValueError("Say what the ideas should be about ('topic')")
    count = max(1, min(int(count or 5), 10))
    error = ""
    if assistant.configured(core):
        system = ("You suggest social media post ideas. Return JSON only: "
                  '{"ideas": [{"title": "...", "body": "..."}]} with ' f"{count} ideas. Title max 80 characters, body 1-3 "
                  "sentences with the angle and a hook. No hashtags, never invent statistics.")
        user = f"Topic: {topic}\nAudience: {audience or 'general'}"
        try:
            raw = assistant._json_from(assistant.chat(core, system, user, transport=transport)).get("ideas") or []
            ideas = [{"title": str(i.get("title", ""))[:120].strip(), "body": str(i.get("body", ""))[:1000].strip()}
                     for i in raw if isinstance(i, dict) and str(i.get("title", "")).strip()][:count]
            if ideas:
                return {"source": "llm", "ideas": ideas}
        except assistant.LLMError as e:
            error = str(e)
    ideas = [{"title": a.format(t=topic)[:120], "body": f"Angle for {audience or 'your audience'}: {a.format(t=topic)}."}
             for a in ANGLES[:count]]
    return {"source": "rules", "ideas": ideas,
            "note": f"Your AI model failed ({error}); showing idea templates." if error else
                    "" if assistant.configured(core) else "Add an AI provider in Settings › AI model for written ideas."}


def save_ideas(core, ideas: list[dict], column: str = "unassigned", tags: list | None = None) -> list[str]:
    with core.db.session() as s:
        rows = [Idea(title=i["title"], body=i.get("body", ""), column=column, tags=list(tags or [])) for i in ideas]
        s.add_all(rows)
        s.flush()
        return [r.id for r in rows]


def alt_text(core, media_id: str, transport=None) -> str:
    with core.db.session() as s:
        m = s.get(Media, media_id)
        if not m:
            raise KeyError("Media not found")
        if m.kind != "image":
            raise ValueError("Alt text can be generated for images only")
        path = core.settings.media_dir / (m.jpeg_name or m.stored_name)
        mime = "image/jpeg" if m.jpeg_name else m.mime
    if not assistant.configured(core):
        raise ValueError("Add an AI provider with a vision model in Settings › AI to generate alt text")
    system = ("Write alt text for this image for screen-reader users: one or two plain sentences, max 250 characters, "
              "describe what matters, read out any visible text, no 'image of'. Reply with the alt text only.")
    try:
        out = assistant.chat(core, system, "Describe this image.", transport=transport, image=(mime, path.read_bytes()))
    except assistant.LLMError as e:
        raise ValueError(f"The AI provider could not describe the image ({e})") from e
    return out.strip().strip('"')[:400]


def comment_insights(core, days: int = 30, channel_id: str | None = None, transport=None) -> dict:
    """Themes in recent comments: what people ask, praise and complain about."""
    since = utcnow() - timedelta(days=max(1, min(int(days or 30), 365)))
    with core.db.session() as s:
        q = select(Comment).where(Comment.is_own.is_(False), Comment.at >= since)
        if channel_id:
            q = q.where(Comment.channel_id.in_(str(channel_id).split(",")))
        rows = [(c.text or "", c.status) for c in s.scalars(q.order_by(Comment.at.desc()).limit(400))]
    texts = [t for t, _ in rows if t.strip()]
    questions = [t for t in texts if "?" in t]
    stats = {"comments": len(texts), "questions": len(questions),
             "unanswered": sum(1 for _, st in rows if st == "open"), "days": days}
    if not texts:
        return {"source": "rules", **stats, "themes": []}
    if assistant.configured(core):
        system = ('Group these social media comments into 3-6 themes. Return JSON only: {"themes": [{"label": "...", '
                  '"summary": "one sentence", "count": n, "examples": ["short quote", "short quote"], "suggestion": '
                  '"a post idea that answers this theme"}]}. Quote comments exactly, never invent.')
        try:
            raw = assistant._json_from(assistant.chat(core, system, "\n".join(f"- {t[:240]}" for t in texts[:150]),
                                                      transport=transport)).get("themes") or []
            themes = [{"label": str(t.get("label", ""))[:60], "summary": str(t.get("summary", ""))[:240],
                       "count": int(t.get("count") or 0), "examples": [str(x)[:200] for x in (t.get("examples") or [])][:3],
                       "suggestion": str(t.get("suggestion", ""))[:240]} for t in raw if isinstance(t, dict) and t.get("label")]
            if themes:
                return {"source": "llm", **stats, "themes": themes}
        except (assistant.LLMError, ValueError, TypeError):
            pass
    words = Counter(w for t in texts for w in re.findall(r"[a-zA-Z][a-zA-Z']{2,}", t.lower()) if w not in STOP)
    themes = [{"label": w, "summary": f"Mentioned in {sum(1 for t in texts if w in t.lower())} comments", "count": n,
               "examples": [t[:200] for t in texts if w in t.lower()][:2], "suggestion": ""} for w, n in words.most_common(6) if n > 1]
    if questions:
        themes.insert(0, {"label": "Questions", "summary": f"{len(questions)} comments ask something", "count": len(questions),
                          "examples": [q[:200] for q in questions[:3]], "suggestion": "Answer the top question in a post"})
    return {"source": "rules", **stats, "themes": themes}


# ------------------------------------------------------------------------------------------------ caption writer
LIMITS = {"x": 280, "instagram": 2200, "tiktok": 2200, "youtube": 5000, "linkedin": 3000, "facebook": 63206, "threads": 500}
MODES = {
    "write": "Write a new post about the brief.",
    "rephrase": "Rewrite the draft with different wording, same meaning.",
    "shorten": "Make the draft shorter and punchier. Keep the key point.",
    "expand": "Expand the draft with one or two useful details. Do not invent facts, numbers or links.",
    "hook": "Rewrite only the opening line as a stronger hook and keep the rest of the draft.",
    "hashtags": "Keep the draft and add 3 to 6 relevant hashtags at the end.",
    "fix": "Fix spelling, grammar and punctuation only.",
}
TONES = ("friendly", "professional", "playful", "bold", "informative")


def write(core, text: str, mode: str = "write", tone: str = "friendly", providers: list | None = None, instructions: str = "",
          count: int = 3, transport=None) -> dict:
    """Buffer's AI Assistant: options for a caption. Needs a provider; never posts anything."""
    if not assistant.configured(core):
        raise ValueError("Add your AI provider and key in Settings › AI first (NVIDIA, Gemini, Groq, OpenRouter, Ollama, OpenAI…)")
    text = (text or "").strip()[:4000]
    if not text:
        raise ValueError("Type a brief or a draft first")
    mode = mode if mode in MODES else "write"
    tone = tone if tone in TONES else "friendly"
    count = max(1, min(int(count or 3), 5))
    nets = [p for p in (providers or []) if p in LIMITS]
    limit = min((LIMITS[p] for p in nets), default=2200)
    system = ("You write social media posts. " + MODES[mode] + f" Tone: {tone}. Write in the language of the input. "
              f"Each option must fit in {limit} characters" + (f" (networks: {', '.join(nets)})" if nets else "") + ". "
              "Never invent facts, numbers, quotes or links. Return JSON only: "
              '{"options": ["...", "..."]} with ' + f"{count} clearly different options.")
    user = ("Brief: " if mode == "write" else "Draft: ") + text + (f"\nExtra instructions: {instructions[:500]}" if instructions else "")
    raw = assistant.chat(core, system, user, transport=transport, temperature=0.8)
    try:
        out = [str(x).strip()[:limit] for x in (assistant._json_from(raw).get("options") or []) if str(x).strip()][:count]
    except assistant.LLMError:
        out = [raw.strip()[:limit]] if raw.strip() else []   # a model that answered in plain text
    if not out:
        raise assistant.LLMError("The model returned no text")
    return {"mode": mode, "tone": tone, "limit": limit, "options": out}
