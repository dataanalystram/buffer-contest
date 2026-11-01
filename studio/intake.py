"""Intake: one prompt box -> what the person actually asked for.

The Make a post box takes prose and links together (up to MAX_PROMPT characters). Before anything is searched, the
prompt is read for: every URL in it (a pasted URL is THE source; the agent never swaps it for a search), the channels
("all my channels" = every connected channel; "just LinkedIn and Instagram" = those), the format ("a reel", "a
carousel", "a single image"), a target length ("30s"), the tone ("punchy", "formal", "explainer"), standing rules the
person states in passing ("end every video with Follow @x" -> a Style Ledger rule) and the topic left once all that is
removed. Pure functions, no network, so the demo and the tests are deterministic.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

MAX_PROMPT = 4000

URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.I)
BARE_RE = re.compile(r"(?<![@\w/.])((?:www\.)?(?:x|twitter|youtube|youtu|linkedin|github|news\.ycombinator|ycombinator)\.(?:com|be)/[^\s<>\"']+)", re.I)

SERVICE_WORDS = {
    "instagram": r"instagram|\binsta\b|\big\b",
    "linkedin": r"linked\s?in",
    "facebook": r"facebook|\bfb\b",
    "youtube": r"youtube|\byt\b|\bshorts?\b",
    "twitter": r"twitter|\bx\b(?!\.com)|\btweet",
    "threads": r"\bthreads\b",
    "bluesky": r"bluesky|bsky",
    "tiktok": r"tik ?tok",
    "mastodon": r"mastodon",
    "pinterest": r"pinterest",
}
TONES = {"formal": r"\bformal|professional|corporate\b", "punchy": r"\bpunchy|snappy|short and sharp|hype\b",
         "casual": r"\bcasual|friendly|\bfun\b", "explainer": r"\bexplain(?:er)?\b|\bsimple\b|\beli5\b|beginner"}


def clean_url(u: str) -> str:
    u = (u or "").strip().rstrip(".,;:!?)…")
    if not u.lower().startswith(("http://", "https://")):
        u = "https://" + u
    return u


def urls(text: str) -> list[str]:
    seen, out = set(), []
    found = [m.group(0) for m in URL_RE.finditer(text or "")]
    stripped = URL_RE.sub(" ", text or "")
    found += [m.group(1) for m in BARE_RE.finditer(stripped)]
    for raw in found:
        u = clean_url(raw)
        key = u.lower().split("?")[0].rstrip("/")
        if key not in seen and urlparse(u).hostname:
            seen.add(key)
            out.append(u)
    return out


def _channels(t: str, connected: list[dict]) -> tuple[list[str], str]:
    """(channel ids, why). Default and "all my channels" -> every connected channel."""
    ids_all = [c["id"] for c in connected]
    if re.search(r"\ball (?:of )?(?:my |the )?(?:channels|networks|platforms|socials|accounts)\b|\beverywhere\b|\bevery (?:channel|network)\b", t):
        return ids_all, "all connected channels (you said all)"
    named = [svc for svc, pat in SERVICE_WORDS.items() if re.search(pat, t)]
    if named and re.search(r"\b(?:on|to|for|in|post|share|only|just)\b", t):
        ids = [c["id"] for c in connected if c["service"] in named]
        if ids:
            return ids, "the channels you named"
    return ids_all, "all connected channels"


def parse(prompt: str, connected: list[dict] | None = None) -> dict:
    """Everything the agent needs to plan, from the prompt alone."""
    raw = (prompt or "").strip()[:MAX_PROMPT]
    found = urls(raw)
    rules = standing_rules(raw)
    t = URL_RE.sub(" ", raw)  # words in a link ("x.com", "/status/") are not instructions
    for r in rules:  # nor are the words of a standing rule ("end every VIDEO with ..." doesn't ask for a video)
        t = t.replace(r, " ")
    t = t.lower()
    connected = connected or []
    chan_ids, chan_why = _channels(t, connected)
    fmt = "auto"
    if re.search(r"\b(?:reel|video|tiktok)s?\b", t) and not re.search(r"\bno (?:reel|video)", t):
        fmt = "reel"
    if re.search(r"\bcarousel|\bslides\b|\bswipe", t):
        fmt = "carousel"
    if re.search(r"\b(?:single|one) (?:image|card|photo)\b|\bjust an image\b", t):
        fmt = "photo"
    secs = None
    m = re.search(r"\b(\d{1,3})\s*(?:s|sec|secs|seconds)\b", t)
    if m:
        secs = max(5, min(180, int(m.group(1))))
    tone = next((k for k, pat in TONES.items() if re.search(pat, t)), "")
    topic = topic_of(raw, rules)
    return {"prompt": raw, "truncated": len((prompt or "").strip()) > MAX_PROMPT, "urls": found,
            "source_url": found[0] if found else "", "extra_urls": found[1:], "channel_ids": chan_ids, "channels_why": chan_why,
            "format": fmt, "seconds": secs, "tone": tone, "rules": rules, "topic": topic[:200], "research": asks_research(raw),
            "kind": "link" if found else ("topic" if topic else "empty")}


# Words that tell the agent WHAT TO DO, never what the post is about ("search it online", "post on all my channels",
# "make a reel", "you can do that"). Owner bug, 1 Nov 2026: a prompt like "you can do that online search it is <link>
# research and have a instgram reel post on all channels" became the headline and the topic of the captions.
INSTRUCTION_RE = re.compile(
    r"\b(?:i want to|i'd like to|i would like to|please|pls|can you|could you|would you|you can|you could|you should|"
    r"do that|do it|do this|go ahead|make|create|write|post(?:ed|ing|s)?|share|publish|upload(?:ed)?|schedule|send|put|"
    r"search(?:ed|ing)?|research(?:ed|ing)?|look(?:ed)? (?:it )?up|google|find|online|on the (?:web|internet)|web|internet|"
    r"have|get|give|use|add|turn|into|out of|from|as|with|of|"
    r"about|this|that|these|those|it|its|it's|is|are|was|be|in|on|to|at|all|my|our|your|me|us|for|and|or|so|then|also|here|there|"
    r"channels?|networks?|platforms?|socials?|accounts?|everywhere|"
    r"a|an|the|short|punchy|snappy|explainer|simple|formal|casual|"
    r"reels?|carousels?|videos?|clips?|shorts?|story|stories|posts?|captions?|images?|photos?|pictures?|slides?|thumbnails?|"
    r"insta(?:gram)?|inst?g?ram|ig|linked ?in|facebook|fb|youtube|yt|tik ?tok|twitter|x|threads|bluesky|mastodon|"
    r"link|url|below|above|attached|uploaded|my file|the file)\b", re.I)


def topic_of(prompt: str, rules: list[str] | None = None) -> str:
    """What the post is about once links, standing rules and instructions are taken out. "" when the prompt only says
    what to do ("search it and post a reel on all channels https://x.com/..."): the link or the upload is then the
    topic, never the prompt's own words."""
    t = URL_RE.sub(" ", prompt or "")
    t = BARE_RE.sub(" ", t)
    for r in rules or []:
        t = t.replace(r, " ")
    t = INSTRUCTION_RE.sub(" ", t)
    t = re.sub(r"\b\d{1,3}\s*(?:s|sec|secs|seconds)\b", " ", t, flags=re.I)
    t = re.sub(r"[^\w$€£₹%.,'&+#@ -]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" .,;:-'")
    return t if re.search(r"[A-Za-z0-9$€£₹]{2,}", t) else ""


def describe(prompt: str, rules: list[str] | None = None) -> str:
    """The person's own words about their content, with the instructions taken out but their grammar kept: clauses that
    only say what to do ("make a reel", "post on all my channels", "search it") are dropped whole, links removed.
    "Our studio opens Monday. Make a reel and post it everywhere" -> "Our studio opens Monday." Used as the person's line
    about an upload (context for the captions), never as a headline."""
    t = URL_RE.sub(" ", prompt or "")
    t = BARE_RE.sub(" ", t)
    for r in rules or standing_rules(prompt or ""):
        t = t.replace(r, " ")
    keep = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", t):
        parts = [p for p in re.split(r",\s*|\s+(?:and|then|also|so)\s+", sent) if p and p.strip()]
        kept = [p.strip() for p in parts if topic_of(p)]
        if kept:
            s = ", ".join(kept)
            keep.append(s if re.search(r"[.!?]$", s) else s + ".")
    return re.sub(r"\s+", " ", " ".join(keep)).strip()[:600]


def asks_research(prompt: str) -> bool:
    """Did the person ask the agent to look things up ("search it", "research", "find out", "look up", "online")?"""
    return bool(re.search(r"\b(?:search|research|look (?:it )?up|google|find out|dig into|online)\b", prompt or "", re.I))


def standing_rules(text: str) -> list[str]:
    """Sentences that set a rule for every post ("end every video with Follow @x", "always use #FF5A1F", "never say
    game-changer"). They go to the Style Ledger, so they apply to this post and to all future renders."""
    out = []
    for s in re.split(r"(?<=[.!?\n])\s+|\s+(?:and|also)\s+(?=(?:always|never|end every|from now on)\b)", text or ""):
        s2 = s.strip()
        if re.search(r"\b(?:always|never|from now on|end (?:each|every|all))\b", s2, re.I):
            out.append(s2[:300])
    return out[:5]


# ---------------------------------------------------------------- refine messages (the chat thread under a run)
def refine(text: str, services: list[str]) -> dict:
    """A reply in the run's thread -> what to change. Scope: one channel when one is named, else every channel."""
    t = (text or "").lower()
    svc = [s for s, pat in SERVICE_WORDS.items() if re.search(pat, t) and s in services]
    out: dict = {"text": (text or "")[:1000], "services": svc, "actions": []}
    m = re.search(r"\b(\d{1,3})\s*(?:s|sec|secs|seconds)\b", t)
    if m:
        out["actions"].append({"do": "duration", "seconds": max(5, min(180, int(m.group(1))))})
    if re.search(r"\bhook\b|headline|\btitle\b", t) and re.search(r"strong|punch|better|bold|catch|short|sharp", t):
        out["actions"].append({"do": "hook", "how": "stronger"})
    tone = next((k for k, pat in TONES.items() if re.search(pat, t)), "")
    if tone and re.search(r"\bmore\b|\bmake\b|\bsound\b|\btone\b|\bless\b|\bbe\b", t):
        out["actions"].append({"do": "tone", "tone": tone})
    if re.search(r"\b(?:add|burn|with|turn on) (?:the )?(?:captions|subtitles)\b", t):
        out["actions"].append({"do": "captions", "on": True})
    fm = re.search(r"\b(reel|carousel|single image)\b", t)
    if fm and re.search(r"\bredo|make it|turn|as a|into", t) and not m:
        f = fm.group(1)
        out["actions"].append({"do": "format", "format": "photo" if f == "single image" else f})
    m2 = re.search(r"\b(?:it'?s|it is|use|the (?:right|correct) (?:number|figure|amount) is)\s+([$€£₹]?\d[\d.,]*\s*(?:k|m|bn|b|million|billion|thousand)?)", t)
    if m2:
        out["actions"].append({"do": "resolve_number", "value": m2.group(1).strip()})
    if re.search(r"\bredo|re-?render|again\b", t) and not out["actions"]:
        out["actions"].append({"do": "rerender"})
    return out
