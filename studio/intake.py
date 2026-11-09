"""Intake: one prompt box -> what the person actually asked for.

The Make a post box takes prose and links together (up to MAX_PROMPT characters). Before anything is searched, the
prompt is read for: every URL in it (a pasted URL is THE source; the agent never swaps it for a search), the channels
("all my channels" = every connected channel; "just LinkedIn and Instagram" = those), the format ("a reel", "a
carousel", "a single image"), a target length ("30s"), the tone ("punchy", "formal", "explainer"), standing rules the
person states in passing ("end every video with Follow @x" -> a Style Ledger rule) and the topic left once all that is
removed. Pure functions, no network, so the demo and the tests are deterministic.
"""
from __future__ import annotations

import difflib
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


# "with my HuhByte AI brand kit", "in my theme", "using the Aviern kit": how to style the post, never what it is about
# (owner bug, 3 Nov 2026: "can you get this video with huhbyte ai brand kit" became a carousel about our own brand kit).
STYLE_RE = re.compile(r"\b(?:with|using|in|on|use)\s+(?:my|our|the|your)?\s*((?:[\w.@&'-]+\s+){0,4}?)"
                      r"(brand\s*kit|kit|theme|branding|brand style|style|template|look and feel|look)\b", re.I)


def style_hint(text: str) -> dict:
    """{"text", "kit", "what"} for a styling instruction in the prompt, else {}."""
    m = STYLE_RE.search(URL_RE.sub(" ", text or ""))
    if not m:
        return {}
    return {"text": m.group(0).strip(), "kit": m.group(1).strip()[:60], "what": re.sub(r"\s+", " ", m.group(2).lower())}


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


# misspelt network names still count (owner bug, 9 Nov 2026: "a reel on sintagram, youtube and facebook" dropped Instagram
# because only exact spellings matched). Twitter is left out: "twister" is a word, and X has its own pattern.
_FUZZY_SERVICES = ("instagram", "linkedin", "facebook", "youtube", "tiktok", "pinterest", "bluesky", "mastodon")
_NOT_TYPOS = {"linked", "instant", "faceboo", "interests", "pinterests"}


def _typo_services(t: str) -> set[str]:
    out = set()
    for w in set(re.findall(r"[a-z]{6,12}", t)):
        if w in _NOT_TYPOS or w in _FUZZY_SERVICES:
            continue
        for svc in _FUZZY_SERVICES:
            if svc in w or w in svc:
                continue  # a real word inside or around the name ("interest", "youtuber"), not a typo of it
            if difflib.SequenceMatcher(None, w, svc).ratio() >= 0.85:
                out.add(svc)
    return out


def _carousel_typo(t: str) -> bool:
    """"caraosul", "carasoul", "carousal": the person asked for a carousel."""
    return any(difflib.SequenceMatcher(None, w, "carousel").ratio() >= 0.7 for w in re.findall(r"\bcar[a-z]{4,6}\b", t))


def _services_in(t: str) -> set[str]:
    return {svc for svc, pat in SERVICE_WORDS.items() if re.search(pat, t)} | _typo_services(t)


def channel_formats(text: str) -> dict[str, str]:
    """{service: "carousel" | "reel"} when the person tied a format to some networks ("a reel on Instagram, YouTube and
    Facebook; next, on LinkedIn I need a carousel"). Read clause by clause; a clause that names networks and one format
    sets it for those networks. Later clauses win. Nothing tied -> {} (owner bug, 9 Nov 2026: LinkedIn was asked for a
    carousel and got the 4:5 video)."""
    t = URL_RE.sub(" ", text or "").lower()
    out: dict[str, str] = {}
    def fmt(c: str) -> tuple[bool, bool]:
        return (bool(re.search(r"\bcarousel|\bslides\b|\bswipe", c) or _carousel_typo(c)),
                bool(re.search(r"\b(?:reels?|shorts?)\b", c)))

    for clause in re.split(r"[.;!?\n]+|\b(?:next|then|also|plus|but|while|whereas)\b", t):
        car, reel = fmt(clause)
        # both formats in one sentence ("a reel on instagram, and for linkedin a carousel"): read its comma / "and for" parts
        parts = re.split(r",|\band\s+(?=(?:for|on|in|to|an?)\b)", clause) if car and reel else [clause]
        for part in parts:
            svcs = _services_in(part)
            if not svcs:
                continue
            car, reel = fmt(part)
            if car != reel:
                for svc in svcs:
                    out[svc] = "carousel" if car else "reel"
    return out


def _channels(t: str, connected: list[dict]) -> tuple[list[str], str]:
    """(channel ids, why). Default and "all my channels" -> every connected channel."""
    ids_all = [c["id"] for c in connected]
    if re.search(r"\ball (?:of )?(?:my |the )?(?:channels|networks|platforms|socials|accounts)\b|\beverywhere\b|\bevery (?:channel|network)\b", t):
        return ids_all, "all connected channels (you said all)"
    named = [svc for svc, pat in SERVICE_WORDS.items() if re.search(pat, t)]
    named += sorted(_typo_services(t) - set(named))
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
    if re.search(r"\bcarousel|\bslides\b|\bswipe", t) or _carousel_typo(t):
        fmt = "carousel"
    if re.search(r"\b(?:single|one) (?:image|card|photo)\b|\bjust an image\b", t):
        fmt = "photo"
    secs = None
    m = re.search(r"\b(\d{1,3})\s*(?:s|sec|secs|seconds)\b", t)
    if m:
        secs = max(5, min(180, int(m.group(1))))
    tone = next((k for k, pat in TONES.items() if re.search(pat, t)), "")
    style = style_hint(raw)
    topic = topic_of(raw.replace(style["text"], " ") if style else raw, rules)
    return {"style": style,"prompt": raw, "truncated": len((prompt or "").strip()) > MAX_PROMPT, "urls": found,
            "source_url": found[0] if found else "", "extra_urls": found[1:], "channel_ids": chan_ids, "channels_why": chan_why,
            "format": fmt, "formats": channel_formats(raw), "seconds": secs, "tone": tone, "rules": rules, "topic": topic[:200], "research": asks_research(raw),
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


# a clause that tells the app what to do (it starts with an order): never the person's words about their content
IMPERATIVE_RE = re.compile(
    r"^(?:please\s+|pls\s+|can you\s+|could you\s+)?(?:"
    r"(?:use|post|put|attach|upload|publish|schedule|send|share|make|create|write|add)\s+(?:it|this|that|them|these|the|my|our|a|an)\b|"
    r"(?:do not|don'?t)\s+(?:change|edit|add|alter|rewrite|include|use)\b|"
    r"with\s+(?:a|an|the)\s+(?:short\s+|long\s+|simple\s+)?(?:caption|headline|title|text)\b)", re.I)


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
        kept = []
        for p in parts:
            p = p.strip()
            if IMPERATIVE_RE.match(p):  # "use the attached image as the media", "post it on Instagram only ... about X"
                m = re.search(r"\babout\s+(.+)$", p, re.I)
                p = m.group(1).strip() if m else ""
            if p and topic_of(p):
                kept.append(p)
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


_NUM = r"(\d{1,3}(?:\.\d+)?)"
_SECS = r"\s*(?:s|sec|secs|second|seconds)\b"
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def video_edits(text: str) -> dict:
    """Plain-word video edits in a thread reply -> edit_video arguments (live pass 2 Nov 2026: "trim the first 3 seconds and
    add captions", "switch to the Nothing theme", "make a 4:5 version" did nothing to the video). {} when none."""
    t = " " + re.sub(r"\s+", " ", (text or "").lower()) + " "
    for w, n in _WORDNUM.items():
        t = re.sub(rf"\b{w}\b(?=\s*(?:s|sec|secs|second|seconds)\b)", str(n), t)
    out: dict = {}
    m = re.search(r"\b(?:trim|cut|remove|drop|skip|chop|lose)\b(?: off)?(?: the)? (?:first|opening|initial) " + _NUM + _SECS, t) or \
        re.search(r"\b(?:trim|cut|remove|drop|skip|chop)\b " + _NUM + _SECS + r"(?: off)? (?:from |of |at )?(?:the )?(?:start|beginning|front|top)", t) or \
        re.search(r"\b(?:start|begin)(?: it| the video)? (?:at|from) " + _NUM + _SECS, t)
    if m:
        out["cut_first_s"] = float(m.group(1))
    if re.search(r"\b(?:no|remove|drop|without|turn off|hide)(?: the)? (?:captions|subtitles|subs)\b|\b(?:captions|subtitles) off\b", t):
        out["captions"] = False
    elif re.search(r"\b(?:captions|subtitles|subs)\b", t) and re.search(
            r"\b(?:add|burn|with|turn on|put|include|show|caption it|captions on|subtitles on)\b", t):
        out["captions"] = True
    m = re.search(r"\b(4|1|9|16)\s*(?::|x|by|/)\s*(5|1|16|9)\b", t)
    if m and f"{m.group(1)}:{m.group(2)}" in ("4:5", "1:1", "9:16"):
        out["ratio"] = f"{m.group(1)}:{m.group(2)}"
    elif re.search(r"\bsquare (?:version|video|one|cut)\b|\bmake it square\b", t):
        out["ratio"] = "1:1"
    if re.search(r"\b(?:auto|automatic) theme\b|\btheme:? auto\b|\b(?:you|agent) (?:pick|choose) the theme\b", t):
        out["theme"] = "auto"
    else:
        m = re.search(r"\b(?:switch|change|move|go|set)(?: it| the video| the theme)? (?:to|back to) (?:the |my |a )?([a-z0-9][\w .&'-]{1,30}?) (?:theme|kit|brand kit|brand|look|style)\b", t) or \
            re.search(r"\b(?:use|in|with|apply|try) (?:the |my |a )?([a-z0-9][\w .&'-]{1,30}?) (?:theme|kit|brand kit|look|style)\b", t)
        if m:
            name = m.group(1).strip()
            if name not in ("same", "other", "another", "new", "different", "video", "this", "that"):
                out["theme"] = name
    return out
