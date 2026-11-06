"""Built-in Style Ledger presets: a starting set of rules for people whose AI doesn't know hooks, retention or CTAs.

Owner, 6 Nov 2026: "users' AIs often don't understand hooks, retention, CTAs". A preset is a named bundle of plain rules a
person picks (and edits) in Brand > Style Ledger, or an AI app applies over MCP (list_style_presets / apply_style_preset).
Applying one writes ordinary ledger rules, so everything the ledger already does keeps working: text rules are applied to
every caption (banned phrases removed, hashtags capped per network), guidance rules ("hook in the first 1-2 seconds") go
into the AI drafter's prompt, and the free built-in drafter (studio/drafter.py, no model, no key) shapes the caption with
the preset's hook / one-idea / payoff / CTA structure.

Each rule paraphrases a public principle; nothing is copied. Where the number comes from a platform (Instagram's
five-hashtag cap, X's "no more than 2", Meta's 125-character truncation, Shorts up to 3 minutes) it is cited in
docs/STYLE_PRESETS.md, not in the UI. Numbers marked "house default" are HuhQueue's own starting points, editable.

Re-applying a preset replaces the rules that preset wrote before (no duplicates). Applying a different preset switches
the earlier preset's rules off (they stay in the ledger, off) and leaves every rule the person wrote alone, except a
single-valued rule of the same type and networks, which the newer one replaces (the ledger's usual "newest wins").
"""
from __future__ import annotations

import json
import re
import time

# networks a preset sets numbers for (the rest keep the ledger's defaults)
NETS = ("instagram", "tiktok", "youtube", "linkedin", "twitter", "threads", "facebook", "bluesky")

PRESETS: list[dict] = [
    {
        "id": "hook_first",
        "name": "Hook-first short-form",
        "for": "Reels, TikTok and Shorts: one idea, fast",
        "summary": "Hook in the first 1-2 seconds, a pattern interrupt every few seconds, one idea, a clear payoff, one CTA.",
        "hook_formulas": [
            "Lead with the most surprising number: “{number}: {what it means}”",
            "Show the end result first, then how you got there",
            "Say the common belief, then the twist: “Most people think {x}. It's {y}.”",
            "Ask the one question the video answers",
            "Name what the viewer loses by scrolling past",
        ],
        "first_line": [
            "The first line states the subject; a stranger can tell what the post is about from it alone",
            "First line under 125 characters (it's all most people see before “more”)",
            "No greeting and no “in this video”: start on the claim",
            "The hook promises only what the video delivers",
        ],
        "structure": [
            "Hook in the first 1-2 seconds: motion or a bold line on screen from frame one",
            "A pattern interrupt every 2-4 seconds: a cut, zoom or new on-screen line",
            "One idea per video; cut anything that is not that idea",
            "Payoff before the end, then end straight after it (no long outro)",
        ],
        "on_screen": [
            "Burned-in captions on every spoken line (most people watch without sound)",
            "At most 6 words on screen at a time, inside the safe zone",
        ],
        "cta": {"line": "Follow for the next one.", "rules": ["One call to action only, right after the payoff"]},
        "length": {"instagram": 300, "tiktok": 300, "youtube": 300, "facebook": 300, "linkedin": 900, "twitter": 260, "threads": 400},
        "hashtags": {"instagram": 5, "tiktok": 4, "youtube": 3, "facebook": 3, "linkedin": 3, "twitter": 2, "threads": 1, "bluesky": 2},
        "banned": ["in this video", "hey guys", "game-changer", "game changer", "you won't believe", "smash that like button"],
        "video": "15-45 s (house default; Shorts and Reels can run to 3 minutes)",
        "drafter": {"hook": "number", "shape": "hook_idea_payoff"},
    },
    {
        "id": "educator",
        "name": "Plain explainer",
        "for": "Teaching one thing clearly: tutorials, how-tos, breakdowns",
        "summary": "Name the problem, teach it in numbered steps, recap, ask people to save it.",
        "hook_formulas": [
            "The mistake most {people} make with {topic}",
            "{topic}, explained in {n} steps",
            "Why {thing} happens (and what to do about it)",
            "Before you {action}, know this",
        ],
        "first_line": [
            "The first line names the problem or the result the reader gets",
            "First line under 125 characters",
            "Say the number of steps up front so people know what they get",
        ],
        "structure": [
            "Problem, then numbered steps (3-5), then a one-line recap",
            "One term per step; define jargon the first time it appears",
            "Each step is an action the viewer can take",
        ],
        "on_screen": [
            "Show the step number and title on screen for every step",
            "Captions on every spoken line; key term highlighted",
        ],
        "cta": {"line": "Save this for later.", "rules": ["Ask for one thing: save it (or follow for part 2), not both"]},
        "length": {"instagram": 700, "tiktok": 500, "youtube": 700, "facebook": 600, "linkedin": 1300, "twitter": 260, "threads": 450},
        "hashtags": {"instagram": 5, "tiktok": 4, "youtube": 3, "facebook": 3, "linkedin": 3, "twitter": 2, "threads": 1, "bluesky": 2},
        "banned": ["obviously", "everyone knows", "it's simple", "just simply", "in today's fast-paced world"],
        "video": "30-90 s (house default)",
        "drafter": {"hook": "steps", "shape": "steps"},
    },
    {
        "id": "build_in_public",
        "name": "Founder story",
        "for": "Founders sharing what they ship, the numbers and the lessons",
        "summary": "A real number or date up front, what happened, the honest lesson, a question that invites replies.",
        "hook_formulas": [
            "Day {n} of building {product}: {what happened}",
            "We shipped {thing}. Here's what broke.",
            "{metric} this week: the honest numbers",
            "The decision I'd make differently",
        ],
        "first_line": [
            "The first line carries a real number, date or result",
            "Talk about what you did (“how I”), not what others should do (“how to”)",
            "First line under 125 characters",
        ],
        "structure": [
            "What we did, what happened (with numbers), the lesson, the question",
            "Include one thing that went wrong",
            "First person, specific, no announcement voice",
        ],
        "on_screen": ["Show the real screen, dashboard or product, not stock footage", "Captions on every spoken line"],
        "cta": {"line": "What would you have done?", "rules": ["End on one specific question people can answer in a reply"]},
        "length": {"instagram": 600, "tiktok": 400, "youtube": 600, "facebook": 600, "linkedin": 1300, "twitter": 260, "threads": 450},
        "hashtags": {"instagram": 3, "tiktok": 3, "youtube": 2, "facebook": 2, "linkedin": 3, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["humbled to announce", "thrilled to announce", "excited to announce", "game-changer", "revolutionary"],
        "video": "20-60 s (house default)",
        "drafter": {"hook": "build", "shape": "build"},
    },
    {
        "id": "news_fast",
        "name": "Punchy news hook",
        "for": "Breaking and daily news: speed without losing accuracy",
        "summary": "Who did what, attributed, in the first line; why it matters; what's next; the source. No hype.",
        "hook_formulas": [
            "{who} {did what}: the one-line version",
            "{number}: the figure that matters in {story}",
            "What changes for you after {event}",
        ],
        "first_line": [
            "The first line is the fact, attributed (“{source} says…”), not a tease",
            "The headline never promises more than the story says",
            "First line under 125 characters",
        ],
        "structure": [
            "What happened, why it matters, what's next, the source",
            "Every number and name comes from the source; nothing invented",
            "Label opinion as opinion",
        ],
        "on_screen": ["Source name and date visible on the image", "No figure on screen that isn't in the source"],
        "cta": {"line": "Follow for the day's AI news.", "rules": ["Point to the source (link, or “link in bio” on Instagram)"]},
        "length": {"instagram": 600, "tiktok": 400, "youtube": 600, "facebook": 500, "linkedin": 900, "twitter": 260, "threads": 450},
        "hashtags": {"instagram": 3, "tiktok": 3, "youtube": 2, "facebook": 2, "linkedin": 2, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["shocking", "you won't believe", "mind-blowing", "game-changer", "rumor has it"],
        "extra_rules": ["no exclamation marks", "always credit the source"],
        "video": "15-40 s (house default)",
        "drafter": {"hook": "news", "shape": "news"},
    },
    {
        "id": "podcast_clip",
        "name": "Podcast clip",
        "for": "Short clips cut from long episodes and interviews",
        "summary": "Open cold on the strongest line, one moment per clip, name the speaker, point to the full episode.",
        "hook_formulas": [
            "Open on the strongest sentence, mid-thought, no intro",
            "{guest}: “{the claim}”",
            "The question, then the answer",
        ],
        "first_line": [
            "The first line is the quote or the claim, in quotation marks",
            "Name the speaker in the first two lines",
            "First line under 125 characters",
        ],
        "structure": [
            "One moment per clip: a single question and answer, or one story",
            "Start at the strongest line, not where the topic starts",
            "Cut before the conversation moves on",
        ],
        "on_screen": ["Speaker name as a lower third", "Word-by-word captions with the key word highlighted"],
        "cta": {"line": "Full episode: link in bio.", "rules": ["One CTA: the full episode"]},
        "length": {"instagram": 400, "tiktok": 300, "youtube": 400, "facebook": 400, "linkedin": 900, "twitter": 260, "threads": 400},
        "hashtags": {"instagram": 4, "tiktok": 3, "youtube": 3, "facebook": 2, "linkedin": 2, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["in this episode we talk about", "hey guys", "make sure to subscribe"],
        "video": "20-60 s (house default)",
        "drafter": {"hook": "quote", "shape": "quote"},
    },
    {
        "id": "data_led",
        "name": "Data-led",
        "for": "Stories with a figure at their heart: benchmarks, prices, growth, surveys",
        "summary": "Lead with one sourced number, give it a comparison so it means something, say what it changes, name the source.",
        "hook_formulas": [
            "{number} {unit}: {what it measures}",
            "{thing} went from {before} to {after}",
            "{number} times {faster/cheaper/bigger} than {the usual comparison}",
            "The one figure in {story} worth remembering",
        ],
        "first_line": [
            "The first line carries the number and what it measures",
            "Round only where the source rounds; never make a number bigger than the source",
            "First line under 125 characters",
        ],
        "structure": [
            "The number, a comparison that gives it scale, what it changes, the source",
            "One number per post as the star; any second number is the comparison",
            "Say what was measured and by whom before saying what it means",
        ],
        "on_screen": ["The number large on screen, the unit and source small beneath it", "No chart without labelled axes"],
        "cta": {"line": "Save this for the numbers.", "rules": ["One CTA: save it, or check the source"]},
        "length": {"instagram": 500, "tiktok": 350, "youtube": 500, "facebook": 500, "linkedin": 1100, "twitter": 260, "threads": 450},
        "hashtags": {"instagram": 3, "tiktok": 3, "youtube": 2, "facebook": 2, "linkedin": 3, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["studies show", "skyrocketed", "literally", "mind-blowing", "game-changer"],
        "extra_rules": ["always credit the source"],
        "video": "15-40 s (house default)",
        "drafter": {"hook": "number", "shape": "data"},
    },
    {
        "id": "linkedin_thread",
        "name": "Thread-style LinkedIn",
        "for": "LinkedIn posts that read like a short thread: one line per thought, easy to skim",
        "summary": "A one-line hook, short one-sentence lines with space between them, a takeaway, then one real question.",
        "hook_formulas": [
            "{thing} just changed {what}. Here's what it means:",
            "{n} things {story} tells us about {topic}:",
            "Everyone is talking about {thing}. Few are talking about {the overlooked part}.",
            "I read {source} so you don't have to. The short version:",
        ],
        "first_line": [
            "The first line works on its own before \u201csee more\u201d: a claim or a promise of a list",
            "First line under 125 characters",
            "No \u201cI'm excited to share\u201d openers",
        ],
        "structure": [
            "One sentence per line, a blank line between them",
            "3-5 points, each starting with \u2192 or a number",
            "End with a one-line takeaway, then one question",
            "Write for a smart reader outside your field: no unexplained acronyms",
        ],
        "on_screen": ["A document or carousel: one point per slide, the hook on slide one"],
        "cta": {"line": "What's your take?", "rules": ["End on one specific question, not \u201cThoughts?\u201d or \u201cAgree?\u201d"]},
        "length": {"instagram": 600, "tiktok": 400, "youtube": 600, "facebook": 700, "linkedin": 1500, "twitter": 260, "threads": 450},
        "hashtags": {"instagram": 3, "tiktok": 3, "youtube": 2, "facebook": 2, "linkedin": 3, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["thrilled to share", "excited to share", "let that sink in", "agree?", "thoughts?", "game-changer"],
        "video": "30-60 s (house default)",
        "drafter": {"hook": "list", "shape": "thread"},
    },
    {
        "id": "genz_casual",
        "name": "Gen Z casual",
        "for": "Relaxed, conversational posts for a young audience on TikTok, Reels and Threads",
        "summary": "Talk like a friend explaining it in a group chat: short lines, plain words, a bit of humour, no corporate voice.",
        "hook_formulas": [
            "ok so {thing} just happened",
            "nobody is talking about {the part that matters}",
            "{thing}, but explained like you have 10 seconds",
            "pov: you just found out {fact}",
        ],
        "first_line": [
            "The first line sounds like a person talking, not a press release",
            "First line under 125 characters",
            "Still say what happened: casual, never vague",
        ],
        "structure": [
            "Short lines; one thought each",
            "Plain words: if a friend would ask \u201cmeaning what?\u201d, say it simpler",
            "A little humour is fine; facts stay exact",
            "At most one emoji per line, and none needed",
        ],
        "on_screen": ["Big casual text, a few words at a time", "Captions on every spoken line"],
        "cta": {"line": "follow for more like this", "rules": ["One CTA, said casually"]},
        "length": {"instagram": 300, "tiktok": 250, "youtube": 300, "facebook": 300, "linkedin": 700, "twitter": 260, "threads": 350},
        "hashtags": {"instagram": 3, "tiktok": 4, "youtube": 2, "facebook": 2, "linkedin": 2, "twitter": 1, "threads": 1, "bluesky": 1},
        "banned": ["synergy", "leverage", "dear followers", "in today's fast-paced world", "game-changer", "hello fellow"],
        "video": "10-30 s (house default)",
        "drafter": {"hook": "casual", "shape": "casual"},
    },
]
BY_ID = {p["id"]: p for p in PRESETS}
SOURCE_PREFIX = "Built-in preset: "


def listing() -> list[dict]:
    """Every preset, with the rules it would add (what the UI shows before applying)."""
    return [{**{k: p[k] for k in ("id", "name", "for", "summary", "hook_formulas", "first_line", "structure", "on_screen",
                                  "cta", "length", "hashtags", "banned", "video")}, "rules": rules_for(p)} for p in PRESETS]


def get(pid: str) -> dict:
    p = BY_ID.get(str(pid or ""))
    if not p:
        raise KeyError(pid)
    return p


def _g(area: str, text: str, pid: str, **extra) -> dict:
    return {"kind": "text", "check": {"type": "guideline", "area": area, "text": text, "preset": pid, **extra}, "rule": text,
            "scope": []}


def rules_for(p: dict) -> list[dict]:
    """The ledger rules a preset writes: {kind, check, rule, scope}. Guidance rules (check type 'guideline') go to the
    drafter; text rules (banned phrases, hashtag caps, lengths) are applied and checked on every caption."""
    pid = p["id"]
    out: list[dict] = []
    for f in p["hook_formulas"]:
        out.append(_g("hook", "Hook formula: " + f, pid))
    for t in p["first_line"]:
        out.append(_g("first_line", t, pid))
    for t in p["structure"]:
        out.append(_g("structure", t, pid))
    for t in p["on_screen"]:
        out.append(_g("on_screen", t, pid))
    for t in p["cta"]["rules"]:
        out.append(_g("cta", t, pid))
    out.append(_g("cta", f"Sign off with: {p['cta']['line']}", pid, line=p["cta"]["line"]))
    out.append(_g("style", f"Caption shape: {p['name']} (used by the free built-in drafter)", pid, drafter=p["drafter"]))
    if p.get("video"):
        out.append(_g("structure", f"Video length: {p['video']}", pid))
    if p["banned"]:
        out.append({"kind": "text", "check": {"type": "banned_words", "words": list(p["banned"]), "preset": pid},
                    "rule": "Never use: " + ", ".join(p["banned"]), "scope": []})
    for net, n in p["hashtags"].items():
        out.append({"kind": "text", "check": {"type": "max_hashtags", "n": int(n), "preset": pid},
                    "rule": f"At most {n} hashtag{'s' if n != 1 else ''} on {net.title()}", "scope": [net]})
    for net, n in p["length"].items():
        out.append({"kind": "text", "check": {"type": "max_length", "n": int(n), "preset": pid},
                    "rule": f"{net.title()} captions under {n} characters", "scope": [net]})
    from . import brand
    for sentence in p.get("extra_rules") or []:
        for r in brand.parse_correction(sentence):
            out.append({"kind": r["kind"], "check": {**r["check"], "preset": pid}, "rule": r["rule"], "scope": []})
    return out


# ------------------------------------------------------------------------------------------------ applying
SINGLE_VALUED = {"no_emoji", "max_hashtags", "no_exclamation", "ends_with", "caption_case", "max_length", "logo_position",
                 "logo_scale", "end_card", "headline_case", "headline_max_words", "accent_color", "font", "overlay",
                 "source_credit"}


def _clean_edit(r: dict, edit) -> dict | None:
    """A person's edit to one preset rule before applying: drop it (False/None), or new wording / number."""
    if edit is False or edit is None:
        return None
    if not isinstance(edit, dict):
        return r
    if edit.get("drop") is True:
        return None
    r = json.loads(json.dumps(r))
    c = r["check"]
    if c["type"] == "guideline" and isinstance(edit.get("text"), str):
        t = re.sub(r"[\x00-\x1f<>]", "", edit["text"]).strip()[:300]
        if not t:
            return None
        c["text"], r["rule"] = t, t
        if c.get("area") == "cta" and "line" in c:
            c["line"] = t.removeprefix("Sign off with:").strip()[:120] or c["line"]
    elif c["type"] in ("max_hashtags", "max_length") and edit.get("n") is not None:
        try:
            n = int(edit["n"])
        except (TypeError, ValueError):
            raise ValueError("n is a whole number")
        n = max(0, min(n, 30 if c["type"] == "max_hashtags" else 5000))
        if c["type"] == "max_hashtags" and r["scope"] == ["instagram"]:
            n = min(n, 5)  # Instagram's own cap (Dec 2025)
        c["n"] = n
        net = r["scope"][0].title() if r["scope"] else "all networks"
        r["rule"] = (f"At most {n} hashtag{'s' if n != 1 else ''} on {net}" if c["type"] == "max_hashtags"
                     else f"{net} captions under {n} characters")
    elif c["type"] == "banned_words" and isinstance(edit.get("words"), list):
        words = [re.sub(r"[\x00-\x1f<>]", "", str(w)).strip()[:60] for w in edit["words"][:40]]
        words = [w for w in words if w]
        if not words:
            return None
        c["words"], r["rule"] = words, "Never use: " + ", ".join(words)
    return r


def plan(p: dict, edits: dict | None = None) -> list[dict]:
    """The rules to write, after the person's edits: edits maps a rule's index in rules_for(p) to {drop|text|n|words}."""
    if edits is not None and not isinstance(edits, dict):
        raise ValueError("edits is an object: {\"<rule index>\": {\"drop\": true} | {\"text\": ...} | {\"n\": 3} | {\"words\": [...]}}")
    base = rules_for(p)
    if edits and len(edits) > len(base):
        raise ValueError("Too many edits.")
    out = []
    for i, r in enumerate(base):
        e = (edits or {}).get(str(i), (edits or {}).get(i, r)) if edits else r
        if e is r:
            out.append(r)
            continue
        c = _clean_edit(r, e)
        if c is not None:
            out.append(c)
    return out


def preset_of(row: dict) -> str:
    c = row.get("check") or {}
    return str(c.get("preset") or "")


def apply(house, a, pid: str, edits: dict | None = None) -> dict:
    """Write the preset's rules into the person's ledger (see the module doc for what is replaced or switched off)."""
    from .actions import ActionError
    try:
        p = get(pid)
    except KeyError:
        raise ActionError(f"No preset called {pid!r}. list_style_presets lists them: {', '.join(BY_ID)}.", 404)
    try:
        rules = plan(p, edits)
    except ValueError as e:
        raise ActionError(str(e), 400)
    house.studio._guard_write(a, "apply_style_preset")
    store = house.store
    from . import provenance
    prov = provenance.stamp(a, builtin=True)
    now = time.time()
    replaced, switched_off = 0, []
    from .userlocks import held
    with held(a.uid, "ledger"), store.tx():  # the deletes, switch-offs and inserts commit together or not at all
        existing = house.rules(a, include_inactive=True)
        for r in existing:  # the same preset applied again: its old rules go (no duplicates)
            if preset_of(r) == p["id"]:
                store.x("DELETE FROM style_rules WHERE id=? AND user_id=?", (r["id"], a.uid))
                replaced += 1
        for r in existing:  # another preset: its rules switch off, kept in the ledger
            if preset_of(r) and preset_of(r) != p["id"] and r["active"]:
                store.x("UPDATE style_rules SET active=0, updated=? WHERE id=? AND user_id=?", (now, r["id"], a.uid))
                switched_off.append({"id": r["id"], "rule": r["rule"], "why": f"from the {BY_ID.get(preset_of(r), {}).get('name', 'other')} preset"})
        mine = [r for r in house.rules(a) if not preset_of(r)]
        for nr in rules:  # newest wins for a single-valued rule of the same type on the same networks
            ty = nr["check"]["type"]
            if ty not in SINGLE_VALUED:
                continue
            for old in mine:
                if old["check"]["type"] == ty and sorted(old.get("scope") or []) == sorted(nr["scope"]) and old["active"]:
                    store.x("UPDATE style_rules SET active=0, updated=? WHERE id=? AND user_id=?", (now, old["id"], a.uid))
                    old["active"] = False
                    switched_off.append({"id": old["id"], "rule": old["rule"], "why": "replaced by the preset's rule"})
        added = []
        for nr in rules:
            rid = store.x("INSERT INTO style_rules(user_id,kind,spec,rule,source,active,created,scope,origin,examples,evidence,updated,"
                          "created_by_source,client_name,model) VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?,?)",
                          (a.uid, nr["kind"], json.dumps(nr["check"]), nr["rule"][:300], SOURCE_PREFIX + p["name"], now,
                           json.dumps(nr["scope"]), "preset", "[]", "[]", now, prov["created_by_source"], prov["client_name"],
                           prov["model"]))
            added.append({**nr, "id": rid})
    store.audit(a.uid, a.label, "apply_style_preset", "write", {"preset": p["id"], "rules": len(added), "replaced": replaced,
                                                                "switched_off": len(switched_off)})
    return {"preset": {"id": p["id"], "name": p["name"]}, "added": len(added), "replaced": replaced,
            "switched_off": switched_off, "rules": added,
            "note": "These are ordinary ledger rules: edit, scope or switch any of them off in Brand > Style Ledger."}


# ------------------------------------------------------------------------------------------------ the free drafter
def style_from_rules(rules: list[dict]) -> dict | None:
    """What the built-in drafter needs from the active preset rules: its shape, the sign-off line, per-network limits."""
    shape = next((r for r in rules if r.get("active", True) and (r.get("check") or {}).get("type") == "guideline"
                  and r["check"].get("area") == "style" and r["check"].get("drafter")), None)
    if not shape:
        return None
    pid = shape["check"].get("preset", "")
    cta = next((r["check"].get("line") for r in rules if r.get("active", True) and (r.get("check") or {}).get("type") == "guideline"
                and r["check"].get("area") == "cta" and r["check"].get("line")), "")
    caps: dict[str, int] = {}
    lengths: dict[str, int] = {}
    for r in rules:
        c = r.get("check") or {}
        if not r.get("active", True) or not r.get("scope"):
            continue
        for net in r["scope"]:
            if c.get("type") == "max_hashtags":
                caps[net] = min(caps.get(net, 99), int(c.get("n", 3)))
            elif c.get("type") == "max_length":
                lengths[net] = min(lengths.get(net, 99999), int(c.get("n", 2000)))
    return {"preset": pid, **dict(shape["check"]["drafter"]), "cta": cta, "hashtags": caps, "length": lengths}
