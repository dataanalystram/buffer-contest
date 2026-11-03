"""Which post template a run uses, and why (owner, 3 Nov 2026: "how does the agent pick among the templates?").

One deterministic rule, the same for every brain (the tool-use model, the free model, no AI, an MCP client):

  story type            template                        why (shown on the run as "Template: <name> · <why>")
  a GitHub repo link    github_repo  (GitHub repo pillar)  "a GitHub repo link"
  a funding round       funding      (Funding round)       "a funding story"
  a YC startup          yc_launch    (YC launch)           "a Y Combinator startup"
  a launch with video   launch_reel  (Launch video Reel)   "a launch with its video: a Reel"
  a viral clip          viral_trend                        "someone else's viral clip, credited"
  everything else       news_carousel (News carousel)      "a news story"

The template is always drawn in the ACTIVE brand kit (house.kit), so a template change never switches the kit or its
layout. The person can name another one in words ("use the funding template", "editorial style", "keep slide 1, restyle
slide 2"); a slide they name is the only slide that changes.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

NEWS = "news_carousel"
NEWS_INFO = {"name": "News carousel", "kind": "carousel", "video": False,
             "about": "Your kit's news cover, the key facts, then your Follow card (the default for a news story)."}
# what the agent never suggests on its own (they need their own data: the week's stories, the editorial research chain)
NOT_SUGGESTED = {"weekly_recap", "midweek_recap", "editorial_story", "quote", "launch_reel", "viral_trend"}
WHY = {"github_repo": "a GitHub repo link", "funding": "a funding story", "yc_launch": "a Y Combinator startup",
       "launch_reel": "a launch with its video: a Reel", "viral_trend": "someone else's viral clip, credited",
       "editorial_story": "a post about one company", NEWS: "a news story", "x_post": "an X post: its card is slide 2"}
# the order of the other suggestions per story type (the picked one is always first)
NEXT = {"github_repo": ["text_slides", NEWS, "editorial_cover"], "funding": ["yc_launch", NEWS, "text_slides"],
        "yc_launch": ["funding", "text_slides", NEWS], "launch_reel": ["launch", "text_slides", NEWS],
        "editorial_story": ["text_slides", NEWS, "editorial_cover"], "x_post": ["text_slides", "funding", "editorial_cover"], NEWS: ["text_slides", "editorial_cover", "breaking"]}
# words -> template id ("use the funding template", "editorial style", "text slides")
ALIASES = [(r"\b(?:github|repo(?:sitory)?)\b", "github_repo"), (r"\bfund(?:ing|raise|raising)?\b|\braise[ds]?\b|\bseries [a-d]\b", "funding"),
           (r"\byc\b|\by ?combinator\b|\bstartup launch\b", "yc_launch"), (r"\btext[- ]?(?:slides?|only slides?)\b|\bplain(?:[- ]words)? slides?\b", "text_slides"),
           (r"\beditorial\b", "editorial_cover"), (r"\bbreaking\b", "breaking"), (r"\bnews(?: carousel)?\b|\bdefault\b|\boriginal\b", NEWS),
           (r"\bproduct launch\b|\blaunch carousel\b|\blaunch\b", "launch"), (r"\bquote\b", "quote"), (r"\btutorial\b|\bhow[- ]to\b|\bfree way\b", "tutorial"),
           (r"\bviral\b|\btrend(?:ing)?\b", "viral_trend"), (r"\breel\b", "launch_reel"), (r"\brecap\b", "weekly_recap")]
INTENT = re.compile(r"\btemplates?\b|\bstyle\b|\blayout\b|\bdesign\b|\blook\b|\brestyle\b|\bformat\b", re.I)
LIST_RE = re.compile(r"\b(?:what|which|list|show)\b[^.?!]{0,40}\btemplates?\b|\btemplates?\b[^.?!]{0,30}\b(?:can you|do you|available|have)\b"
                     r"|\bwhat (?:else|other)\b[^.?!]{0,30}\b(?:templates?|styles?|layouts?)\b", re.I)
ORD = {"first": 1, "1st": 1, "one": 1, "second": 2, "2nd": 2, "two": 2, "third": 3, "3rd": 3, "three": 3, "fourth": 4, "4th": 4, "four": 4,
       "fifth": 5, "5th": 5, "five": 5, "sixth": 6, "6th": 6, "six": 6, "last": -1}
_N = r"(first|1st|second|2nd|third|3rd|fourth|4th|fifth|5th|sixth|6th|last|\d{1,2})"


def _n(w: str) -> int:
    w = w.lower()
    return int(w) if w.isdigit() else ORD.get(w, 0)


def github_repo(urls) -> str:
    """'owner/name' for a github.com repo link, else ''."""
    for u in urls or []:
        p = urlparse(u if "://" in (u or "") else "https://" + (u or ""))
        if (p.hostname or "").lower().removeprefix("www.") != "github.com":
            continue
        parts = [x for x in p.path.split("/") if x]
        if len(parts) >= 2 and parts[0].lower() not in ("orgs", "topics", "search", "marketplace", "features", "collections", "trending"):
            name = parts[1][:-4] if parts[1].lower().endswith(".git") else parts[1]
            return f"{parts[0]}/{name}"
    return ""


FUNDING_RE = re.compile(r"\b(?:raise[sd]?|raising|funding|fundrais\w*|seed round|pre-seed|series [a-e]|valuation|backed by|led by)\b", re.I)
VIRAL_HOSTS = ("tiktok.com", "instagram.com")


def story_type(run: dict, has_video: bool = False) -> str:
    """The story type of a run, from its links, its words and the facts it found (no model)."""
    inp, res = run.get("input") or {}, run.get("result") or {}
    it = inp.get("intake") or {}
    urls = list(it.get("urls") or []) + [inp.get("source_url") or "", (res.get("source") or {}).get("url") or ""]
    if github_repo(urls):
        return "github_repo"
    if (res.get("source") or {}).get("post") and not has_video:
        return "x_post"  # the X post itself is slide 2 of the News carousel
    st = res.get("story") or {}
    words = " ".join([inp.get("prompt") or "", st.get("headline") or "", " ".join(st.get("facts") or [])[:600]])
    if res.get("editorial") or res.get("company"):
        return "editorial_story"
    if any("ycombinator.com" in (u or "") for u in urls) or re.search(r"\b(?:YC|Y Combinator)\b(?:\s*[WSFX]\d{2})?", words):
        return "yc_launch"
    if FUNDING_RE.search(words) and re.search(r"[$€£₹]\s?\d|\d+\s?(?:M|B|million|billion)\b", words):
        return "funding"
    if has_video and any(h in (u or "") for u in urls for h in VIRAL_HOSTS):
        return "viral_trend"
    if has_video:
        return "launch_reel"
    return NEWS


def catalog(custom: list[dict] | None = None) -> dict[str, dict]:
    """Every template the agent can draw: the News carousel, the built-ins and the workspace's own (custom:<slug>)."""
    from . import templates
    out = {NEWS: dict(NEWS_INFO)}
    for k, v in templates.TEMPLATES.items():
        out[k] = {x: v.get(x) for x in ("name", "kind", "about", "video")}
    for t in custom or []:
        if t.get("id"):
            out[t["id"]] = {"name": t.get("name") or t["id"], "kind": t.get("kind") or "carousel", "about": t.get("about") or "",
                            "video": bool(t.get("video")), "custom": True}
    return out


def pick(run: dict, has_video: bool = False, custom: list[dict] | None = None) -> dict:
    """{id, name, why, type, suggestions[ids]}: the template for this run and 3-4 others worth a look."""
    cat = catalog(custom)
    kind = story_type(run, has_video)
    tid = kind if kind in cat else NEWS
    if kind == "funding" and "funding" not in cat:  # pragma: no cover - the built-in exists; a custom one wins below
        tid = NEWS
    own = next((k for k, v in cat.items() if v.get("custom") and kind != NEWS and kind.split("_")[0] in (v["name"] or "").lower()), "")
    if own:  # the workspace's own template for this story type ("Funding announcement") wins over the built-in
        tid = own
    sugg = [tid] + [x for x in NEXT.get(kind, NEXT[NEWS]) if x in cat and x != tid]
    sugg += [k for k, v in cat.items() if v.get("custom") and k not in sugg][:1]
    return {"id": tid, "name": cat[tid]["name"], "why": WHY.get(kind, "a news story") + (" (your own template)" if own else ""),
            "type": kind, "suggestions": sugg[:4]}


def wants_list(text: str) -> bool:
    """'what templates can you use', 'what else brand templates you can use', 'list the templates'."""
    return bool(LIST_RE.search(text or ""))


def named(text: str, custom: list[dict] | None = None) -> str:
    """The template the person named in words, or ''. A custom template's own name wins over the aliases."""
    t = (text or "").lower()
    for c in sorted(custom or [], key=lambda x: -len(x.get("name") or "")):
        nm = (c.get("name") or "").lower().strip()
        if nm and len(nm) >= 3 and nm in t:
            return c["id"]
    from . import templates
    for k, v in templates.TEMPLATES.items():  # its full name ("GitHub repo pillar", "Text slides")
        if (v.get("name") or "").lower() in t:
            return k
    for rx, tid in ALIASES:
        if re.search(rx, t):
            return tid
    return ""


def slide_refs(text: str) -> tuple[list[int], list[int], bool]:
    """(slides to change, slides to keep, 'the rest') from 'keep slide 1, restyle slide 2', 'the second slide', 'first one
    is nice'. Numbers are 1-based; -1 is the last slide."""
    t = (text or "").lower()
    keep: set[int] = set()
    for m in re.finditer(rf"\b(?:keep|leave|don'?t (?:touch|change))\s+(?:the\s+)?(?:slides?\s+)?{_N}(?:\s+(?:one|slide))?"
                         rf"(?:\s*(?:,|and|&)\s*(?:slides?\s+)?{_N})?", t):
        keep.update(_n(g) for g in m.groups() if g)
    for m in re.finditer(rf"\b(?:the\s+)?{_N}\s+(?:one|slide)\s+(?:is|looks|was)\s+(?:nice|good|great|fine|ok|okay|perfect)", t):
        keep.add(_n(m.group(1)))
    change: set[int] = set()
    for m in re.finditer(rf"\b{_N}\s+slides?\b|\bslides?\s+(?:no\.?\s*|number\s+|#)?{_N}(?:\s*(?:,|and|&)\s*{_N})*", t):
        for g in m.groups():
            if g:
                change.add(_n(g))
    for m in re.finditer(rf"\bslides?\s+{_N}((?:\s*(?:,|and|&)\s*{_N})+)", t):
        change.update(_n(x) for x in re.findall(_N, m.group(2) or ""))
    rest = bool(re.search(r"\b(?:the )?(?:rest|others|other slides)\b", t))
    change -= keep
    change.discard(0)
    keep.discard(0)
    return sorted(change), sorted(keep), rest


def template_reply(text: str, custom: list[dict] | None = None) -> dict | None:
    """A reply about the template: {'list': True} | {'template': id or '', 'slides': [...], 'keep': [...], 'rest': bool}.
    None when the reply isn't about templates or slides."""
    if wants_list(text):
        return {"list": True}
    change, keep, rest = slide_refs(text)
    tid = named(text, custom) if INTENT.search(text or "") or re.search(r"\buse (?:the|a|my)\b", text or "", re.I) else ""
    restyle = bool(re.search(r"\b(?:restyle|change|switch|swap|different|another|other|new)\b[^.?!]{0,30}\b(?:template|style|layout|design|look)\b"
                             r"|\b(?:template|style|layout|design|look)\b[^.?!]{0,20}\b(?:change|switch|swap|different)\b|\brestyle\b", text or "", re.I))
    if not (tid or restyle or (change and INTENT.search(text or ""))):
        return None
    return {"template": tid, "slides": change, "keep": keep, "rest": rest}


def fill(tid: str, run: dict, extra: dict | None = None) -> dict:
    """The template's fields from the run's own facts. Every field the template's SAMPLE has is set (blank when unknown),
    so a render never shows the preview's sample company or figures."""
    from . import hb_templates, templates
    res = run.get("result") or {}
    st = res.get("story") or {}
    facts = [f for f in st.get("facts") or [] if f][:4]
    head = st.get("headline") or ""
    src = st.get("source") or ""
    sample = (hb_templates.SAMPLE.get(tid) or templates.SAMPLE.get(tid) or {}) if not tid.startswith("custom:") else {}
    d: dict = {k: ([] if isinstance(v, list) else "") for k, v in sample.items()}
    d.update(title=head, hook=head, credit=f"Source: {src}" if src else "", facts=facts, features=facts, what=facts, points=facts)
    comp = st.get("company") or (res.get("company") or {}).get("name") or ""
    if tid == "github_repo":
        repo = github_repo([(res.get("source") or {}).get("url") or "", (run.get("input") or {}).get("source_url") or "",
                            *(((run.get("input") or {}).get("intake") or {}).get("urls") or [])])
        info = res.get("repo_info") or {}
        d.update(repo=repo, stars=info.get("stars") or "", license=info.get("license") or "", language=info.get("language") or "",
                 hook=head or (f"This free GitHub repo: {repo}" if repo else ""))
    elif tid == "funding":
        txt = " ".join([head, *facts])
        m = re.search(r"[$€£₹]\s?\d[\d.,]*\s?(?:[MBK]|million|billion)?", txt)
        rnd = re.search(r"\b(?:pre-seed|seed|series [a-e])\b", txt, re.I)
        d.update(company=comp, amount=m.group(0).strip() if m else "", round=rnd.group(0).title() if rnd else "",
                 round_facts=[f for f in facts if FUNDING_RE.search(f) or re.search(r"[$€£₹]\d", f)][:3] or facts[:2],
                 what=[f for f in facts if not FUNDING_RE.search(f)][:3])
    elif tid == "yc_launch":
        b = re.search(r"\b([WSFX]\d{2})\b", " ".join([head, *facts, (run.get("input") or {}).get("prompt") or ""]))
        d.update(company=comp or head.split(" ")[0], batch=b.group(1) if b else "", founders="", funding="")
    elif tid == "text_slides":
        d.update(slides=[{"text": f} for f in facts[:5]], sub="")
    elif tid == "launch":
        d.update(product=comp or head, company=comp, tagline=st.get("why") or (facts[0] if facts else ""), price="", availability="",
                 why=st.get("why") or "")
    elif tid == "breaking":
        d.update(source=src, when="Just now")
    elif tid == "tutorial":
        d.update(task=head, tool="", steps=[{"title": f, "body": ""} for f in facts[:5]])
    elif tid == "quote":
        d.update(quote=facts[0] if facts else head, speaker=src, role="")
    elif tid == "viral_trend":
        d.update(creator=(res.get("video_credit") or "").replace("Video: ", ""), platform="")
    elif tid == "editorial_cover":
        d.update(sub=src)
    d.update(extra or {})
    return d


def repo_info(repo: str, http) -> dict:
    """Stars, licence and language from GitHub's public API (no key; never fatal, never invented)."""
    if not repo or http is None:
        return {}
    try:
        r = http.get(f"https://api.github.com/repos/{repo}", headers={"Accept": "application/vnd.github+json"}, timeout=8)
        if r.status_code != 200:
            return {}
        j = r.json()
    except Exception:  # noqa: BLE001
        return {}
    n = j.get("stargazers_count")
    stars = "" if not isinstance(n, int) else (f"{n / 1000:.1f}k".replace(".0k", "k") if n >= 1000 else str(n))
    return {"stars": stars, "license": ((j.get("license") or {}).get("spdx_id") or "").replace("NOASSERTION", ""),
            "language": j.get("language") or "", "description": (j.get("description") or "")[:200]}
