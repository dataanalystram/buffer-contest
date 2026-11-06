"""Intent first (owner bug, 6 Nov 2026): what did the person actually ask the Make a post agent for?

The agent used to treat every message as "make a news post about these words": "test" and "hi" ran a news search for
"test" / "hi" and ended on a raw tool error, and a request to post an exact caption on LinkedIn only searched the news
for the request's own instruction words ("exact only, do not change single character, no hashtags") and wrote an
unrelated caption with hashtags. This module reads the message BEFORE any tool runs:

- ``classify`` -> "chat" (a greeting or a test message: a short reply, no tools), "ask" (no link, no upload and no topic
  left once the instructions are taken out: one specific question, no tools), "verbatim" (quoted text plus exact /
  verbatim / "don't change" wording: that text is the caption, byte for byte, nothing researched or rewritten) or "post"
  (the usual plan). ``needs_search`` says whether a news search is allowed: never with a link or an upload as the
  source, never with only instruction words to search for.
- ``search_query`` is the only query a news search may use: the topic with every instruction and constraint taken
  out, or "" (the server-side guard in agent_tools.t_search_news refuses an empty one).
- ``caption_edit`` reads a reply in a thread: "replace / set / edit the LinkedIn caption to <text>", "use exactly
  ...", "verbatim", quoted text -> the exact new caption.
- ``constraints`` -> no hashtags / no link / nothing extra, honoured by the caption writer (``apply_constraints``).

Pure functions, no network and no web imports (the worker can import it).
"""
from __future__ import annotations

import re

from . import intake

# a whole message that is small talk or a test: answered in one line, never researched
SMALL_TALK = re.compile(
    r"^\s*(?:h+i+|hey+|hello+|hell?o there|yo|hiya|howdy|sup|wassup|good (?:morning|afternoon|evening|night)|"
    r"thanks?(?: you| a lot| so much)?|thank u|thx|ty|cheers|ok(?:ay)?|k|cool|nice|great|awesome|got it|"
    r"test(?:ing)?(?: ?(?:test|one two|1 ?2 ?3|\d+))*|ping|hello world|are you (?:there|working|alive)|you there|"
    r"help|what can you do|who are you|\?+|\.+|!+)\s*[.!?]*\s*(?:hq|huhqueue|agent|bot)?\s*[.!?]*\s*$", re.I)
# the person wants what is happening now: a search is the job
CURRENT = re.compile(
    r"\b(?:today|tonight|yesterday|this (?:week|morning|afternoon|month|year)|latest|newest|most recent|recent(?:ly)?|"
    r"right now|currently|just (?:launched|released|announced|dropped|shipped)|breaking|news|trending|"
    r"launch|launched|release|released|announce|announced|ship|shipped|unveil|unveiled)\b", re.I)
# exact-text wording ("post this exact caption", "verbatim", "do not change a single character")
EXACT = re.compile(
    r"\b(?:exact(?:ly)?|verbatim|word[- ]for[- ]word|as[- ]is|unchanged|character[- ]for[- ]character|byte[- ]for[- ]byte|"
    r"(?:do not|don'?t|dont|never|without) (?:change|changing|edit|editing|alter|altering|modify|modifying|rewrite|"
    r"rewriting|reword|rewording|touch|touching)|(?:this|the following) (?:text|caption) only|only this (?:text|caption))\b", re.I)
NO_HASHTAGS = re.compile(r"\b(?:no|without|zero|skip|drop|remove|(?:do not|don'?t|dont|never) (?:add|use|include|put))\s+"
                         r"(?:any\s+|the\s+)?hash ?tags?\b", re.I)
NO_LINK = re.compile(r"\b(?:no|without|skip|drop|remove|(?:do not|don'?t|dont|never) (?:add|include|put|use))\s+"
                     r"(?:a\s+|any\s+|the\s+)?(?:links?|urls?)\b", re.I)
NO_EXTRA = re.compile(r"\bno (?:extra|other|additional|more) (?:text|words|lines|content)\b|\bnothing else\b|"
                      r"\b(?:do not|don'?t) add (?:anything|any text)\b", re.I)
# instruction and constraint phrases: never a topic, never a search query
CONSTRAINT_WORDS = re.compile(
    r"\b(?:(?:do not|don'?t|dont|never|without|no) (?:change|add|use|include|put|edit|alter|rewrite|touch)"
    r"(?: (?:a|any|the))?(?: single)?(?: [a-z]+)?|no (?:extra|other|additional) [a-z]+|"
    r"exact(?:ly)?|verbatim|word[- ]for[- ]word|as[- ]is|unchanged|only|just|single|character|characters|"
    r"hash ?tags?|links?|urls?|nothing else|text|caption|captions|draft|drafts|please|no)\b", re.I)
QUOTES = (('"', '"'), ("“", "”"), ("„", "“"), ("«", "»"), ("‘", "’"), ("「", "」"))
CAPTION_WORD = re.compile(r"\b(?:captions?|text|copy|wording|words|message)\b", re.I)
EDIT_VERB = re.compile(r"\b(?:replace|set|change|edit|update|make|use|swap|put|switch|rewrite)\b", re.I)
CAPTION_TO = re.compile(
    r"\b(?:replace|set|change|edit|update|make|swap|switch)\b[^\n]{0,80}?\b(?:captions?|text|copy)\b[^\n:]{0,60}?"
    r"(?:\bto\b|\bwith\b|\binto\b|\bas\b|\bsay\b|\bread\b|:)\s*:?\s*(?P<t>\S[\s\S]*)$", re.I)
COLON_TAIL = re.compile(r"\b(?:text|caption|copy|verbatim|exactly|only|this)\b[^\n:]{0,40}:[ \t]*(?P<t>\S[\s\S]*)$", re.I)
LEAD_IN = re.compile(r"^(?:(?:exactly|precisely)\s+)?(?:this|the following)?\s*(?:exact\s+)?(?:text|caption|copy)?\s*(?:only)?"
                     r"\s*(?:,?\s*verbatim)?\s*[:,]?\s*", re.I)

CHAT_REPLY = ("Hi! I'm here and working. I make posts for your channels: paste a link, upload a photo or video, or tell me "
              "the topic (for example “what OpenAI launched today”), and I'll draft it for you to review. Nothing goes to "
              "Buffer until you press the button on a post.")
ASK_REPLY = ("What should the post be about? Paste a link, upload a photo or video, or give me the topic in a few words. "
             "For an exact caption, put it in quotes, for example: post this exact caption on LinkedIn: “Your text”.")


def small_talk(text: str) -> bool:
    return bool(SMALL_TALK.match(text or "")) and not intake.urls(text or "")


def quoted(text: str) -> str:
    """The text between the first matching pair of quote marks, byte for byte ("" when there is none). Straight double
    quotes, curly quotes, guillemets; single straight quotes are left alone (they are apostrophes far more often)."""
    t = text or ""
    best = None
    for o, c in QUOTES:
        i = t.find(o)
        while i != -1:
            j = t.find(c, i + 1)
            if j == -1:
                break
            inner = t[i + 1:j]
            if inner.strip() and (o != "‘" or len(inner) > 12):  # ’ is also an apostrophe: only a long ‘...’ span counts
                if best is None or i < best[0]:
                    best = (i, inner)
                break
            i = t.find(o, j + 1)
    return best[1] if best else ""


def constraints(text: str) -> dict:
    t = text or ""
    return {"no_hashtags": bool(NO_HASHTAGS.search(t)), "no_link": bool(NO_LINK.search(t)), "no_extra": bool(NO_EXTRA.search(t))}


def verbatim(text: str) -> str:
    """The exact caption the person gave ("" when they didn't): quoted text plus exact wording, or quoted text with
    "post / use this caption". Returned byte for byte, quotes taken off."""
    q = quoted(text)
    if not q:
        return ""
    outside = (text or "").replace(q, " ")
    if EXACT.search(outside) or (re.search(r"\bcaptions?\b", outside, re.I) and re.search(r"\b(?:post|use|publish|share|put|set)\b", outside, re.I)):
        return q
    return ""


def caption_edit(text: str) -> str:
    """A reply that sets a caption to exact text -> that text ("" when it isn't one). Quoted text wins; else what follows
    "caption to / with / :" ("Edit LinkedIn caption to this exact text only: Hello"). Leading/trailing whitespace is
    the only thing trimmed from an unquoted tail."""
    t = text or ""
    if not (EDIT_VERB.search(t) or EXACT.search(t)) or not (CAPTION_WORD.search(t) or EXACT.search(t)):
        return ""
    q = quoted(t)
    if q:
        return q
    m = COLON_TAIL.search(t)
    if m and (EXACT.search(t[:m.start("t")]) or CAPTION_TO.search(t)):
        return m.group("t").strip()
    m = CAPTION_TO.search(t)
    if m:
        tail = m.group("t")
        if re.match(r"(?:exactly|precisely|this|the following|exact)\b", tail, re.I):
            tail = LEAD_IN.sub("", tail, count=1)
        return tail.strip()
    return ""


def search_query(text: str) -> str:
    """The words a news search may use: the topic with links, quotes, instructions and constraints taken out."""
    q = quoted(text)
    t = (text or "").replace(q, " ") if q else (text or "")
    t = intake.topic_of(t)
    t = CONSTRAINT_WORDS.sub(" ", t)
    t = re.sub(r"\bwhat (?:did|has|have|is|are)\b|\bwhat'?s\b|\btell me\b|\bany\b", " ", t, flags=re.I)
    t = re.sub(r"[\s,.;:!?-]+", " ", t).strip(" ,.;:-")
    return t if re.search(r"[A-Za-z0-9$€£₹]{2,}", t) and not small_talk(t) else ""


def classify(prompt: str, *, has_url: bool = False, has_upload: bool = False) -> dict:
    """{"kind": chat | ask | verbatim | post, "text": the exact caption for verbatim, "query": the allowed search query,
    "needs_search": bool, "constraints": {...}, "reply": what to say for chat / ask}."""
    p = (prompt or "").strip()
    cons = constraints(p)
    v = verbatim(p)
    if v:
        return {"kind": "verbatim", "text": v, "query": "", "needs_search": False, "constraints": cons, "reply": ""}
    if not has_url and not has_upload and small_talk(p):
        return {"kind": "chat", "text": "", "query": "", "needs_search": False, "constraints": cons, "reply": CHAT_REPLY}
    query = "" if (has_url or has_upload) else search_query(p)
    if not has_url and not has_upload and not query:
        return {"kind": "ask", "text": "", "query": "", "needs_search": False, "constraints": cons, "reply": ASK_REPLY}
    return {"kind": "post", "text": "", "query": query, "needs_search": bool(query), "current": bool(CURRENT.search(p)),
            "constraints": cons, "reply": ""}


def apply_constraints(caption: str, cons: dict) -> str:
    """No hashtags / no link, as asked: taken out of a written caption (never out of an exact caption)."""
    t = caption or ""
    if cons.get("no_hashtags"):
        t = re.sub(r"(?<![\w&])#[\w-]+", "", t)
    if cons.get("no_link"):
        t = intake.URL_RE.sub("", t)
        t = re.sub(r"(?im)^\s*(?:read more|link|source|more|full story)\s*(?:in bio|below|here)?\s*[:→-]*\s*$", "", t)
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return re.sub(r"[ \t]{2,}", " ", t).strip()
