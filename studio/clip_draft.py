"""Clip Studio drafts: one editing state for a One take clip that the person and the AI director both change (owner brief,
26 Nov 2026: "the agent and the human should use the same underlying editing state and supported operations").

A draft holds the clip's edits before they're rendered: the hook line and its look (headline / words behind you), where
each element sits (take_layout.clean_place), the caption style, the motion graphics picks and the clip's In/Out. Saving a
draft never renders; ``render`` commits it in one re-render on the video worker. Every change is logged (who, what, why)
so the studio can show the history, and the client keeps undo/redo as a stack of drafts it saves back here.

The director (``direct``) turns a request into typed edits on the draft: rules first (no model call), then the person's
AI engine for what the rules don't understand, validated by the same ``clean_draft``. It never invents a quote: a hook
or a graphic's words must be said in the clip. What it can't do yet is said plainly.

Same calls over REST (studio/take_layout_routes.py) and MCP (TOOLS below).
"""
from __future__ import annotations

import json
import re
import time

from .actions import ActionError, Actor
from .take_layout import HOOK_FX, PLACE_Y, _behind_on, _clip, _take_id, clean_place

FIELDS = ("hook", "hook_fx", "hook_words", "place", "caption_preset", "fx", "start", "end", "broll", "punch_in", "trim_pauses", "split")
FX_KINDS = ("stat", "line")
MAX_FX, LOG_MAX = 4, 40

TOOLS = [
    {"name": "get_clip_draft", "description": "One take Clip Studio: a clip's draft (the edits not rendered yet: hook, "
     "hook_fx, hook_words, place, caption_preset, fx, start, end), what's rendered now, and the edit history. The person "
     "in the studio and agents share this one draft.",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id"],
                     "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"}}}},
    {"name": "edit_clip_draft", "description": "One take Clip Studio: change a clip's draft (no render). Give only the "
     "fields to change: hook (a line said in the clip), hook_fx (none | headline | behind), hook_words (1-3 words of the "
     "hook for behind), place (see set_clip_layout), caption_preset, fx (null = automatic motion graphics; [] = none; or "
     "up to 4 picks {kind: stat | line, text: words said in the clip, nth: which time}), start/end (source seconds), "
     "punch_in / trim_pauses / split (true | false | null = the take's own setting). "
     "note: one line on why (shown in the studio's history).",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id", "draft"], "properties": {
         "take_id": {"type": "integer"}, "clip_id": {"type": "string"}, "draft": {"type": "object"},
         "note": {"type": "string", "maxLength": 200}}}},
    {"name": "direct_clip", "description": "One take Clip Studio's AI director: a creative request in plain words (\"make "
     "the number pop without covering my face\", \"put the words behind my head\", \"more cinematic captions\", \"fewer "
     "effects\") becomes typed edits on the clip's draft. selection (optional): {start, end} in source seconds of the "
     "words it's about. Returns the edits made (each with why and where), the new draft, and what it couldn't do. "
     "Nothing renders until render_clip_draft.",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id", "instruction"], "properties": {
         "take_id": {"type": "integer"}, "clip_id": {"type": "string"}, "instruction": {"type": "string", "maxLength": 500},
         "selection": {"type": "object", "properties": {"start": {"type": "number"}, "end": {"type": "number"}}},
         "ai": {"type": "boolean", "description": "false: rules only, no model call"}}}},
    {"name": "render_clip_draft", "description": "One take Clip Studio: render the clip's draft on the video worker (one "
     "re-render; poll list_clips). The draft becomes what's rendered.",
     "inputSchema": {"type": "object", "required": ["take_id", "clip_id"],
                     "properties": {"take_id": {"type": "integer"}, "clip_id": {"type": "string"}}}},
]
READ_ONLY = {"get_clip_draft"}
NAMES = {t["name"] for t in TOOLS}


def committed(clip: dict, run: dict | None = None) -> dict:
    """What the clip is rendered with now, in draft form."""
    from .media import captions_ass
    preset = clip.get("caption_preset") or (clip.get("meta") or {}).get("caption_preset") or \
        ((run or {}).get("input") or {}).get("caption") or captions_ass.DEFAULT_PRESET
    hook_fx = clip.get("hook_fx") or ("headline" if ((run or {}).get("input") or {}).get("headline") else "none")
    return {"hook": clip.get("hook") or "", "hook_fx": hook_fx if hook_fx in HOOK_FX else "none",
            "hook_words": clip.get("hook_words") or "", "place": clip.get("place") or {}, "caption_preset": preset,
            "fx": clip.get("fx") if isinstance(clip.get("fx"), list) else None,
            "broll": clip.get("broll") if isinstance(clip.get("broll"), list) else [],
            "punch_in": clip.get("punch_in") if isinstance(clip.get("punch_in"), bool) else None,
            "trim_pauses": clip.get("trim_pauses") if isinstance(clip.get("trim_pauses"), bool) else None,
            "split": clip.get("split") if isinstance(clip.get("split"), bool) else None,
            "start": round(float(clip.get("start") or 0), 2), "end": round(float(clip.get("end") or 0), 2)}


def clean_draft(raw, clip: dict, res: dict, base: dict) -> dict:
    """base with the fields of raw applied, each checked. Raises ActionError with the reason."""
    from . import take
    from .media import behind, captions_ass
    from .takes import clip_words
    if not isinstance(raw, dict):
        raise ActionError("draft must be an object.")
    bad = [k for k in raw if k not in FIELDS]
    if bad:
        raise ActionError(f"draft has no field {bad[0][:20]}: use {', '.join(FIELDS)}.")
    d = json.loads(json.dumps(base))
    words = (res.get("analysis") or {}).get("words") or []
    if "start" in raw or "end" in raw:
        from .takes import Takes
        lo, hi = Takes._bounds(res, clip)
        try:
            s = float(raw.get("start", d["start"]))
            e = float(raw.get("end", d["end"]))
        except (TypeError, ValueError):
            raise ActionError("start and end are seconds in the source video.") from None
        s, e = max(lo, s), min(hi or e, e)
        if e - s < 3 or e - s > 90:
            raise ActionError("A clip must be 3 to 90 seconds long.")
        d["start"], d["end"] = round(s, 2), round(e, 2)
    said = " ".join(w["w"] for w in clip_words(words, clip, d["start"], d["end"])) if words else (clip.get("text") or "")
    if "hook" in raw:
        hook = " ".join(str(raw["hook"] or "").split())[:120]
        if hook and not take.verbatim(hook.rstrip("…"), said):
            raise ActionError("The hook has to be a line said in the clip (extract, don't invent).")
        d["hook"] = hook
    if "hook_fx" in raw:
        if raw["hook_fx"] not in HOOK_FX:
            raise ActionError("hook_fx must be none, headline or behind.")
        if raw["hook_fx"] == "behind" and not _behind_on():
            raise ActionError("Words behind you isn't switched on on this server yet.", 409)
        d["hook_fx"] = raw["hook_fx"]
    if "hook_words" in raw:
        hw = " ".join(str(raw["hook_words"] or "").split())[:40]
        if hw:
            try:
                behind.pick_words(d["hook"], hw)
            except behind.BehindError as e:
                raise ActionError(str(e)) from None
        d["hook_words"] = hw
    if "place" in raw:
        d["place"] = clean_place(raw["place"])
    if "caption_preset" in raw:
        p = captions_ass.clean_preset(raw["caption_preset"])
        if not p:
            raise ActionError(f"caption_preset must be one of: {', '.join(captions_ass.PRESETS)}.")
        d["caption_preset"] = p
    if "fx" in raw:
        fx = raw["fx"]
        if fx is None:
            d["fx"] = None
        else:
            if not isinstance(fx, list) or len(fx) > MAX_FX:
                raise ActionError(f"fx must be null (automatic) or a list of at most {MAX_FX} picks.")
            out = []
            low = re.sub(r"[^\w%$€£.' ]", " ", said.lower())
            for f in fx:
                if not isinstance(f, dict) or f.get("kind") not in FX_KINDS:
                    raise ActionError("Each fx pick is {kind: stat | line, text, nth}.")
                text = " ".join(str(f.get("text") or "").split())[:80]
                norm = re.sub(r"[^\w%$€£.' ]", " ", text.lower())
                if not text or " ".join(norm.split()) not in " ".join(low.split()):
                    raise ActionError(f"\"{text[:40]}\" isn't said in this clip: a graphic shows the speaker's own words.")
                nth = f.get("nth", 0)
                if isinstance(nth, bool) or not isinstance(nth, int) or not 0 <= nth < 50:
                    raise ActionError("nth must be 0, 1, 2 ... (which time the words are said).")
                out.append({"kind": f["kind"], "text": text, "nth": nth})
            d["fx"] = out
    for k in ("punch_in", "trim_pauses", "split"):   # None = the take's own setting
        if k in raw:
            if raw[k] is not None and not isinstance(raw[k], bool):
                raise ActionError(f"{k} is true, false or null (the take's setting).")
            d[k] = raw[k]
    if "broll" in raw or "start" in raw or "end" in raw:
        from .media import broll
        try:
            d["broll"] = broll.clean(raw.get("broll", d.get("broll")), d["end"] - d["start"]) if d.get("broll") or "broll" in raw else []
        except ValueError as e:
            if "broll" in raw:
                raise ActionError(str(e)) from None
            d["broll"] = [b for b in d["broll"] if b["e"] <= d["end"] - d["start"] - 0.3]   # trimmed past it: it goes
    return d


def _state(clip: dict, run: dict) -> dict:
    base = committed(clip, run)
    dr = clip.get("draft") if isinstance(clip.get("draft"), dict) else None
    return {"take_id": run["id"], "clip_id": clip["id"], "status": clip.get("status"),
            "draft": {**base, **((dr or {}).get("edits") or {})}, "rendered": base, "dirty": bool(dr and dr.get("edits")),
            "log": (clip.get("draft_log") or [])[-LOG_MAX:], "can_render": True,
            "hook_fx_choices": [x for x in HOOK_FX if x != "behind" or _behind_on()]}


def get(takes, a: Actor, rid, clip_id) -> dict:
    run, res, clip = _clip(takes, a, rid, clip_id)
    out = _state(clip, run)
    out["can_render"] = not res.get("sample") and takes._can_rerender(a, res)
    return out


def _diff(d: dict, base: dict) -> dict:
    return {k: d[k] for k in FIELDS if d.get(k) != base.get(k)}


def _log(clip: dict, a: Actor, by: str, what: str, ops: list | None = None, reply: str = "") -> dict:
    e = {"reply": reply[:500], "at": round(time.time(), 1), "by": by, "who": a.label[:60], "what": what[:300], "ops": (ops or [])[:8]}
    clip["draft_log"] = ((clip.get("draft_log") or []) + [e])[-LOG_MAX:]
    return e


def _write(takes, a: Actor, rid: int, clip_id: str, change, by: str, what: str, reply: str = ""):
    takes.h.studio._guard_write(a, "adjust_clip")
    with takes._take_lock(rid):
        run, res, clip = _clip(takes, a, rid, clip_id)
        if run["status"] in ("running", "rendering") and clip.get("status") == "rendering":
            raise ActionError("This clip is rendering. Try again when it's ready.", 409)
        base = committed(clip, run)
        cur = {**base, **(((clip.get("draft") or {}).get("edits")) or {})}
        new, ops = change(cur, clip, res, run)
        edits = _diff(new, base)
        clip["draft"] = {"edits": edits, "at": round(time.time(), 1)} if edits else None
        if not clip["draft"]:
            clip.pop("draft", None)
        entry = _log(clip, a, by, what, ops, reply)
        takes._save(rid, run["steps"], res)
        out = _state(clip, run)
        out["can_render"] = not res.get("sample") and takes._can_rerender(a, res)
        out["entry"] = entry
        return out


def put(takes, a: Actor, rid, clip_id, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.")
    rid = _take_id(rid)
    raw = body.get("draft")
    note = " ".join(str(body.get("note") or "").split())[:200] or "Edited by hand"
    if raw is None and body.get("discard") is True:
        def change(cur, clip, res, run):
            return committed(clip, run), [{"op": "discard"}]
        return _write(takes, a, rid, clip_id, change, "person" if a.kind == "human" else "agent", "Back to what's rendered")

    def change(cur, clip, res, run):
        # undo/redo send the whole draft; only fields that differ from the committed render are kept
        return clean_draft(raw, clip, res, cur if body.get("merge", True) else committed(clip, run)), \
            [{"op": "set", "fields": sorted(raw.keys())[:8]}]
    return _write(takes, a, rid, clip_id, change, "person" if a.kind == "human" else "agent", note)


# ------------------------------------------------------------------------------------------------ the director
CAPTION_WORDS = [("cinematic", "cinematic"), ("film", "cinematic"), ("documentary", "documentary"), ("editorial", "documentary"),
                 ("minimal", "minimal"), ("clean", "clean"), ("lower third", "lower_third"), ("typewriter", "typewriter"),
                 ("two tone", "two_tone"), ("two-tone", "two_tone"), ("bold", "bold_pop"), ("creator", "bold_pop")]
NOT_YET = [("music", "Music"), ("sound effect", "Sound effects"),
           ("sfx", "Sound effects"), ("whoosh", "Sound effects"), ("transition", "Transitions"), ("voiceover", "Voiceover"),
           ("three different openings", "Several openings at once"), ("3 different openings", "Several openings at once")]


BROLL_WORDS = ("b-roll", "broll", "b roll", "cutaway", "stock footage", "stock video")


def broll_rule(instruction: str, d: dict, ws: list[dict], sel) -> tuple[dict, list, list]:
    """"add b-roll of a busy kitchen" / "remove the b-roll": (changes, ops, not_yet). One Pexels cutaway of about 3 seconds
    in the middle of the clip (or over the selected words), the speaker's audio playing on. Searches here, on the web
    server: the worker only downloads the file chosen."""
    from .media import broll
    t = " " + re.sub(r"\s+", " ", instruction.lower()) + " "
    if not any(w in t for w in BROLL_WORDS):
        return {}, [], []
    if re.search(r"\b(remove|delete|no|without|drop|clear|take out|get rid of)\b", t):
        if d.get("broll"):
            return {"broll": []}, [{"what": "Removed the B-roll", "why": "You asked for it gone.", "focus": {"track": "broll"}}], []
        return {}, [], ["There's no B-roll on this clip to remove"]
    m = re.search(r"(?:b-?\s?roll|cutaways?|stock (?:footage|video))\s+(?:of|showing|with|about|for|on)\s+(.+?)(?:[.!?]|$| and (?:make|add|put|move|also)\b)", instruction, re.I)
    query = " ".join((m.group(1) if m else "").split()).strip(" ,.;")[:60]
    if not query:   # no subject given: the most visual words said in the clip
        stop = set("the a an and or but so to of in on at for with is are was were be been it this that these those you your we our i my "
                   "they them he she his her not no yes just very really like then than there here what when where which who how all "
                   "can will would could should about from into over out up down have has had do does did get got make made".split())
        words = [re.sub(r"[^a-z]", "", str(w["w"]).lower()) for w in ws]
        freq: dict[str, int] = {}
        for w in words:
            if len(w) > 4 and w not in stop:
                freq[w] = freq.get(w, 0) + 1
        top = sorted(freq, key=lambda k: (-freq[k], -len(k)))[:2]
        query = " ".join(top)
    if not query:
        return {}, [], ["B-roll needs a subject: say what it should show, e.g. \"b-roll of a busy kitchen\""]
    dur = d["end"] - d["start"]
    taken = [(b["s"], b["e"]) for b in d.get("broll") or []]
    picked = _sel(ws, sel)
    if picked:   # over the selected words
        s0 = float(ws[picked[0]]["s"]) - d["start"]
        e0 = float(ws[picked[0] + picked[1] - 1]["e"]) - d["start"]
        win = (round(max(broll.LEAD_IN, s0), 2), round(min(dur - 0.3, max(e0, s0 + broll.MIN_LEN)), 2))
        if win[1] - win[0] > broll.MAX_LEN:
            win = (win[0], round(win[0] + broll.MAX_LEN, 2))
    else:
        win = broll.slots(ws, d["start"], d["end"], taken)
    if not win or win[1] - win[0] < broll.MIN_LEN - 0.01:
        return {}, [], ["This clip is too short for a B-roll cutaway"]
    if len(taken) >= broll.MAX_ITEMS:
        return {}, [], [f"A clip can carry {broll.MAX_ITEMS} cutaways: remove one first"]
    try:
        found = broll.search(query)
    except broll.BrollError as e:
        return {}, [], [str(e)]
    f = found[0]
    item = {"s": win[0], "e": win[1], "url": f["url"], "thumb": f["thumb"], "credit": f["credit"], "query": query}
    return ({"broll": [*(d.get("broll") or []), item]},
            [{"what": f"Added B-roll of {query}", "why": f"{win[1] - win[0]:.1f}s cutaway; your voice plays over it. {f['credit']}.",
              "focus": {"s": d["start"] + win[0], "e": d["start"] + win[1], "track": "broll"}}], [])


def _clip_words(res, clip, d):
    from .takes import clip_words
    return clip_words((res.get("analysis") or {}).get("words") or [], clip, d["start"], d["end"])


def _nth(ws: list[dict], i: int, n: int) -> int:
    """How many times the phrase at ws[i:i+n] was said before i."""
    from .media import motion
    toks = [motion._norm(w.get("w")) for w in ws]
    want = toks[i:i + n]
    return sum(1 for j in range(i) if toks[j:j + n] == want)


def _sel(ws: list[dict], sel) -> tuple[int, int] | None:
    """(first, count) of the selected words, or None."""
    if not isinstance(sel, dict):
        return None
    try:
        s, e = float(sel.get("start")), float(sel.get("end"))
    except (TypeError, ValueError):
        return None
    idx = [i for i, w in enumerate(ws) if float(w["e"]) > s + 0.01 and float(w["s"]) < e - 0.01]
    return (idx[0], len(idx)) if idx else None


def rules(instruction: str, d: dict, ws: list[dict], sel, behind_ok: bool) -> tuple[dict, list, list, str]:
    """(changes, ops, not_yet, reply) from plain words, no model. ops: [{what, why, focus}] in the order they happen;
    focus is where the studio points: {"element": ...} on the frame or {"s", "e"} on the timeline (source seconds)."""
    from . import take
    from .media import motion
    t = " " + re.sub(r"\s+", " ", instruction.lower()) + " "
    ch, ops, nope = {}, [], []
    picked = _sel(ws, sel)
    seltext = " ".join(str(w["w"]).strip() for w in ws[picked[0]:picked[0] + picked[1]]) if picked else ""
    place = json.loads(json.dumps(d.get("place") or {}))
    for k, label in NOT_YET:
        if k in t and label not in nope:
            nope.append(label)
    # make something pop / land / stand out: a graphic on the selected words, or on the clip's first number
    # captions bigger / smaller: the nearest real edit is the caption style (sizes come with the style)
    if re.search(r"\bcaptions?\b.{0,20}\b(bigger|larger|huge|bolder|louder|more readable|easier to read)\b|\b(bigger|larger|bolder)\b.{0,12}\b(captions?|subtitles?|text)\b", t) and "caption_preset" not in ch:
        ch["caption_preset"] = "bold_pop"
        ops.append({"what": "Captions: bold creator", "why": "Caption size comes with the style: this one is the biggest and boldest.", "focus": {"element": "captions"}})
    elif re.search(r"\b(captions?|subtitles?|text)\b.{0,20}\b(smaller|subtle|quieter|less loud)\b|\b(smaller|quieter)\b.{0,12}\b(captions?|subtitles?)\b", t):
        ch["caption_preset"] = "minimal"
        ops.append({"what": "Captions: minimal", "why": "The smallest, quietest caption style.", "focus": {"element": "captions"}})
    # the pipeline's own switches, per clip: zoom, pauses, split, length
    if re.search(r"\b(no|stop|less|without|don'?t|do not|turn off|disable)\b.{0,14}\b(zoom\w*|punch[- ]?ins?)\b|\bkeep (it|the (screen|demo|product)) (readable|still|steady)\b", t):
        ch["punch_in"] = False
        ops.append({"what": "Zoom off", "why": "The picture stays steady, no zooms on strong words.", "focus": {"track": "layout"}})
    elif re.search(r"\b(zoom\w*|punch[- ]?ins?|push[- ]?in|dynamic)\b", t) and not re.search(r"\b(product|screen)\b.{0,20}\bzoom|zoom\b.{0,20}\b(product|screen)", t):
        ch["punch_in"] = True
        ops.append({"what": "Gentle zooms on", "why": "The picture eases in on strong words, then back.", "focus": {"track": "layout"}})
    if re.search(r"\b(cut|trim|remove|tighten|tighter|no)\b.{0,14}\b(pauses?|silences?|filler|dead air|ums?)\b|\bfast[- ]?paced\b|\btighter\b", t):
        ch["trim_pauses"] = True
        ops.append({"what": "Pauses and filler cut", "why": "Silences the detector found are taken out when it renders.", "focus": {"track": "words"}})
    elif re.search(r"\b(keep|leave)\b.{0,12}\b(pauses?|silences?)\b|\b(natural|relaxed) pace\b|\buncut\b", t):
        ch["trim_pauses"] = False
        ops.append({"what": "Pauses kept", "why": "The natural pace stays.", "focus": {"track": "words"}})
    if re.search(r"\bno split\b|\b(without|turn off|stop)\b.{0,10}\bsplit", t):
        ch["split"] = False
        ops.append({"what": "Split screen off", "why": "One frame follows the speaker.", "focus": {"track": "layout"}})
    elif re.search(r"\bsplit[- ]?screen\b|\bsplit (the )?(screen|frame)\b|\btwo speakers?\b", t):
        ch["split"] = True
        ops.append({"what": "Split screen for two speakers", "why": "Each speaker gets half the frame when two are on screen.", "focus": {"track": "layout"}})
    m = re.search(r"\b(\d{1,2})\s*(?:-|\s)?(?:s|sec|secs|seconds?)\b", t)
    if m and re.search(r"\b(make|shorten|trim|cut|length|long|shorter|down to|about|around|under|exactly)\b", t) and 3 <= int(m.group(1)) <= 90:
        want = int(m.group(1))
        ch["end"] = round(float(d["start"]) + want, 2)
        ops.append({"what": f"Clip length about {want} s", "why": "The end moves to keep that length; it stays on the nearest word when it renders.",
                    "focus": {"track": "words"}})
    if re.search(r"\b(pop|stand out|emphasi[sz]e|highlight|land|bigger)\b", t) and not re.search(r"\bcaption", t):
        i, n = (picked if picked else (None, 0))
        if i is None:
            for j in range(len(ws)):
                st = motion._stat_at(ws, j)
                if st:
                    i, n = j, st["n"]
                    break
        if i is not None:
            phrase = " ".join(str(w["w"]).strip() for w in ws[i:i + n]).strip(" ,.;:!?")
            st = motion._stat_at(ws, i)
            kind = "stat" if st else "line"
            fx = [f for f in (d.get("fx") or []) if f.get("text") != phrase][:MAX_FX - 1]
            fx.append({"kind": kind, "text": phrase, "nth": _nth(ws, i, n)})
            ch["fx"] = fx
            face = " face" in t or "cover me" in t or "my head" in t
            ops.append({"what": f"{'Counter' if kind == 'stat' else 'Kinetic line'} on \"{phrase}\"",
                        "why": ("A number counted up lands harder than a caption." if kind == "stat" else
                                "The line slams in word by word.") + (" It sits in the caption band, which the face check "
                                                                       "keeps clear of your face." if face else ""),
                        "focus": {"s": float(ws[i]["s"]), "e": float(ws[i + n - 1]["e"]), "track": "graphics"}})
        else:
            nope.append("Pop: select the words to make pop (no number in this clip)")
    if re.search(r"\b(fewer|less|no|remove the|without) (effects?|graphics|motion)\b|\bsimpler\b|\bcalmer\b", t):
        ch["fx"] = []
        ops.append({"what": "No motion graphics", "why": "Fewer effects: the speaker carries the clip.", "focus": {"track": "graphics"}})
        if re.search(r"\bno (effects?|graphics)\b", t) and d.get("hook_fx") != "none":
            ch["hook_fx"] = "none"
            ops.append({"what": "No hook graphic", "why": "No effects at all.", "focus": {"element": "headline"}})
    # behind the head / 3D
    if re.search(r"behind (my|the|his|her|their) head|behind me|behind (him|her|them)|text behind|words behind", t):
        if not behind_ok:
            nope.append("Words behind you (not switched on on this server)")
        else:
            ch["hook_fx"] = "behind"
            hw = seltext if seltext and take.verbatim(seltext, d.get("hook") or "") and len(seltext.split()) <= 3 else ""
            if hw:
                ch["hook_words"] = hw
            ops.append({"what": f"Words behind you{': ' + hw.upper() if hw else ''}", "why": "The word sits in the scene: "
                        "your head passes in front of it.", "focus": {"element": "behind"}})
    if re.search(r"\b(ring|orbit|3d|three d)\b", t):
        if behind_ok:
            ch["hook_fx"] = "behind"
            place["behind"] = {**place.get("behind", {}), "ring": True}
            ch["place"] = place
            ops.append({"what": "Orbit ring around you", "why": "Its back half passes behind you and its front half in "
                        "front, so you stand out in depth.", "focus": {"element": "behind"}})
        else:
            nope.append("Orbit ring (needs Words behind you on this server)")
    # a stronger opening: the clip's strongest line from its first seconds, as a headline
    if re.search(r"\b(opening|hook|intro|first (second|seconds|line)|start) (stronger|better|punchier|hit)|stronger (opening|hook|start)|better hook", t):
        sents = take.sentences(ws)
        line = take.best_line(sents, 0, len(sents) - 1) if sents else ""
        if seltext and len(seltext.split()) >= 3:
            line = seltext[:120]
        if line and line != d.get("hook"):
            ch["hook"] = line
        if d.get("hook_fx") == "none" and "hook_fx" not in ch:
            ch["hook_fx"] = "headline"
        if not ch:
            nope.append("The opening already uses the strongest line from the first seconds. Say which words to open on, or ask for words behind your head")
            line = ""
        short = (line or d.get("hook") or "")
        short = short if len(short) <= 60 else short[:60].rsplit(" ", 1)[0] + "…"
        if line:
          ops.append({"what": f"Hook: \"{short}\"", "why": "The strongest line said in the first "
                    "seconds, boxed over the opening so the viewer knows why to stay.", "focus": {"element": "headline"}})
    if re.search(r"\bheadline\b", t) and "hook_fx" not in ch and not re.search(r"\bno headline|remove (the )?headline", t):
        ch["hook_fx"] = "headline"
        ops.append({"what": "Headline over the opening", "why": "The hook line in a box for the first seconds.",
                    "focus": {"element": "headline"}})
    if re.search(r"\bno headline|remove (the )?headline|without (a |the )?headline", t):
        ch["hook_fx"] = "none"
        ops.append({"what": "No headline", "why": "As asked.", "focus": {"element": "headline"}})
    # caption style
    if re.search(r"caption|subtitle|style|look|energetic|energy|font|cinematic", t):
        for k, pid in CAPTION_WORDS:
            if re.search(r"\b" + re.escape(k), t):
                if pid != d.get("caption_preset"):
                    ch["caption_preset"] = pid
                    ops.append({"what": f"Captions: {pid.replace('_', ' ')}", "why": "The caption style you asked for.",
                                "focus": {"element": "captions"}})
                break
        if re.search(r"\b(energetic|energy|punchier|more exciting)\b", t) and "caption_preset" not in ch:
            ch["caption_preset"] = "bold_pop"
            ops.append({"what": "Captions: bold pop", "why": "Bigger words that pop in on the beat read as energy.",
                        "focus": {"element": "captions"}})
    # move things
    def move(el: str, dy: float = 0.0, x: float | None = None, y: float | None = None, why: str = ""):
        cur = {**place.get(el, {})}
        if y is not None:
            cur["y"] = y
        elif dy:
            base = cur.get("y", {"captions": 0.693, "headline": 0.13, "logo": 0.033, "tag": 0.86, "behind": 0.18}[el])
            cur["y"] = round(min(PLACE_Y[1], max(PLACE_Y[0], base + dy)), 4)
        if x is not None:
            cur["x"] = x
        place[el] = cur
        ch["place"] = place
        ops.append({"what": f"Moved the {el}", "why": why or "Where you asked.", "focus": {"element": el}})
    for el, names in (("captions", r"captions?|subtitles?"), ("headline", r"headline|title"), ("logo", r"logo"),
                      ("tag", r"handle|brand (words|name|tag)|tagline")):
        m = re.search(rf"(?:{names})\b[^.]*?\b(higher|up|lower|down|top|bottom)\b(?:[^.]*?\b(left|right|centre|center|middle)\b)?", t)
        if m:
            v, hside = m.group(1), m.group(2)
            if v in ("top", "bottom") and el in ("logo", "tag"):
                move(el, y=0.04 if v == "top" else 0.86, x={"left": 0.12, "right": 0.88}.get(hside or "", 0.5 if el == "tag" else None))
            else:
                move(el, dy=-0.08 if v in ("higher", "up", "top") else 0.08)
    if re.search(r"(hide|remove|no) (the )?logo", t):
        place["logo"] = {**place.get("logo", {}), "on": False}
        ch["place"] = place
        ops.append({"what": "Logo hidden", "why": "As asked.", "focus": {"element": "logo"}})
    q = re.search(r"(?:handle|tag|tagline|brand words?)[^\"“']*[\"“']([^\"”']{1,40})[\"”']", instruction, re.I)
    if q:
        place["tag"] = {**place.get("tag", {}), "text": q.group(1).strip(), "on": True}
        ch["place"] = place
        ops.append({"what": f"Brand words: {q.group(1).strip()}", "why": "Your own words, placed bottom centre.",
                    "focus": {"element": "tag"}})
    # cut the selected words when they're at an edge of the clip (a cut in the middle needs pauses trimmed at setup)
    if picked and re.search(r"\b(cut|remove|delete|drop|trim)\b", t) and not ops:
        i, n = picked
        if i == 0 and i + n < len(ws):
            ch["start"] = round(float(ws[i + n]["s"]) - 0.05, 2)
            ops.append({"what": f"Cut \"{seltext[:40]}\" from the start", "why": "The clip now starts on the next word.",
                        "focus": {"s": float(ws[i]["s"]), "e": float(ws[i + n - 1]["e"]), "track": "words"}})
        elif i + n >= len(ws) and i > 0:
            ch["end"] = round(float(ws[i - 1]["e"]) + 0.15, 2)
            ops.append({"what": f"Cut \"{seltext[:40]}\" from the end", "why": "The clip now ends on the word before.",
                        "focus": {"s": float(ws[i]["s"]), "e": float(ws[i + n - 1]["e"]), "track": "words"}})
        else:
            nope.append("Cutting words from the middle of a clip (only from its start or end for now)")
    reply = "; ".join(o["what"] for o in ops)
    return ch, ops, nope, reply


AI_SYSTEM = ("You are the creative director in a video clip editor. Turn the person's request into edits on the clip's "
             "draft. Answer JSON: {\"reply\": one or two plain sentences to the person (answer questions about the clip; say what you did or why you did nothing), \"draft\": {only fields to change}, \"ops\": [{\"what\": short, \"why\": one sentence, "
             "\"focus\": {\"element\": headline|behind|captions|logo|tag} or {\"s\": sec, \"e\": sec}}], \"not_yet\": [things "
             "you can't do]}. Fields: hook (MUST be words said in the clip, copied exactly), hook_fx (none|headline|behind), "
             "hook_words (1-3 words of the hook), caption_preset (clean|minimal|bold_pop|cinematic|documentary|lower_third|"
             "two_tone|typewriter), fx (list of {kind: stat|line, text: words said in the clip, nth: 0}; [] = none), "
             "place ({headline|behind|logo|tag: {x, y, scale}, captions: {y}}; shares of a 9:16 frame, y 0.04-0.92), "
             "start/end (source seconds), punch_in / trim_pauses / split (true, false or null = the take's setting). Never invent words. Doing nothing is a valid answer. B-roll is added by the server, not by you. You can't add music, "
             "sound effects or transitions yet.")


def _ai(takes, a: Actor, instruction: str, d: dict, ws: list[dict], seltext: str):
    eng, chat, used = takes._moment_chat(a)
    if not chat:
        return None
    said = " ".join(f"[{float(w['s']):.1f}]{w['w']}" if i % 8 == 0 else str(w["w"]) for i, w in enumerate(ws))[:4000]
    user = json.dumps({"request": instruction[:500], "selected_words": seltext[:300], "draft": d, "clip_words": said})
    try:
        out = chat(eng, AI_SYSTEM, user)
    except Exception as e:  # noqa: BLE001 - the rules' answer stands
        print(f"director: AI failed: {str(e)[:160]}", flush=True)
        return None
    return out if isinstance(out, dict) else None


def direct(takes, a: Actor, rid, clip_id, body: dict) -> dict:
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.")
    instruction = " ".join(str(body.get("instruction") or "").split())[:500]
    if not instruction:
        raise ActionError("Tell the director what to change.")
    rid = _take_id(rid)
    sel = body.get("selection")
    result: dict = {}
    # read first (outside the lock: a model call can take a while), then apply under the lock
    run, res, clip = _clip(takes, a, rid, clip_id)
    cur = {**committed(clip, run), **(((clip.get("draft") or {}).get("edits")) or {})}
    ws = _clip_words(res, clip, cur)
    ch, ops, nope, reply = rules(instruction, cur, ws, sel, _behind_on())
    read_by = "rules"
    ai_reply = ""
    bch, bops, bnope = broll_rule(instruction, cur, ws, sel)
    if bops or bnope:
        ch = {**ch, **bch}
        ops += bops
        nope += bnope
    if not ops and body.get("ai") is not False:
        picked = _sel(ws, sel)
        seltext = " ".join(str(w["w"]) for w in ws[picked[0]:picked[0] + picked[1]]) if picked else ""
        got = _ai(takes, a, instruction, cur, ws, seltext)
        if got:
            ai_reply = " ".join(str(got.get("reply") or "").split())[:500]
            try:
                raw = {k: v for k, v in (got.get("draft") or {}).items() if k in FIELDS}
                clean_draft(raw, clip, res, cur)   # checked here; applied below
                ch = raw
                ops = [{"what": str(o.get("what") or "")[:80], "why": str(o.get("why") or "")[:200],
                        "focus": o.get("focus") if isinstance(o.get("focus"), dict) else {}}
                       for o in (got.get("ops") or []) if isinstance(o, dict)][:6] if raw else []
                nope += [str(x)[:80] for x in (got.get("not_yet") or [])][:4]
                read_by = "AI"
            except ActionError as e:
                nope.append(f"The AI's idea didn't pass the checks ({e})")
    final = (reply or "; ".join(o["what"] for o in ops) or ai_reply or "")
    if ch:
        def change(cur2, clip2, res2, run2):
            return clean_draft(ch, clip2, res2, cur2), ops
        result = _write(takes, a, rid, clip_id, change, "agent", instruction, ai_reply or final)
    else:
        result = get(takes, a, rid, clip_id)
    result.update(ops=ops, not_yet=nope, read_by=read_by,
                  reply=(reply or "; ".join(o["what"] for o in ops) or ai_reply or
                         ("Nothing changed: " + "; ".join(nope) if nope else
                          "I can change this clip's opening, put words behind your head, make a number pop, switch caption "
                          "styles, zoom, cut pauses, set the length, move things, or add B-roll. Say which.")))
    return result


def render(takes, a: Actor, rid, clip_id) -> dict:
    """Commit the draft: the clip takes its edits and re-renders once on the video worker."""
    from .takes import clip_words
    rid = _take_id(rid)
    takes.h.studio._guard_write(a, "adjust_clip")
    with takes._take_lock(rid):
        run, res, clip = _clip(takes, a, rid, clip_id)
        if run["status"] in ("running", "rendering") and clip.get("status") == "rendering":
            raise ActionError("This clip is already rendering.", 409)
        if res.get("sample"):
            raise ActionError("The sample take can't be re-rendered: start a take from your own video to keep edits.", 409)
        if not takes._can_rerender(a, res):
            raise ActionError("Rendering needs the video worker, which this server doesn't have.", 409)
        edits = ((clip.get("draft") or {}).get("edits")) or {}
        if not edits:
            raise ActionError("Nothing to render: the draft is the same as the clip.")
        d = clean_draft(edits, clip, res, committed(clip, run))
        words = (res.get("analysis") or {}).get("words") or []
        if (d["start"], d["end"]) != (clip.get("start"), clip.get("end")):
            clip["start"], clip["end"] = d["start"], d["end"]
            clip["text"] = " ".join(w["w"] for w in clip_words(words, clip, d["start"], d["end"]))
        for k in ("hook", "hook_fx", "hook_words", "caption_preset"):
            clip[k] = d[k]
        if d["place"]:
            clip["place"] = d["place"]
        else:
            clip.pop("place", None)
        for k in ("punch_in", "trim_pauses", "split"):
            if d.get(k) is None:
                clip.pop(k, None)
            else:
                clip[k] = d[k]
        if d.get("broll"):
            clip["broll"] = d["broll"]
        else:
            clip.pop("broll", None)
        if d["fx"] is None:
            clip.pop("fx", None)
        else:
            clip["fx"] = d["fx"]
        clip.pop("draft", None)
        _log(clip, a, "person" if a.kind == "human" else "agent", "Rendered the draft", [{"op": "render", "fields": sorted(edits)}])
        clip["status"] = "rendering"
        takes._save(rid, run["steps"], res, "rendering")
        takes.store.audit(a.uid, a.label, "render_clip_draft", "write", {"take": rid, "clip": clip_id, "fields": sorted(edits)})
    takes._render(a, rid, [clip_id])
    out = get(takes, a, rid, clip_id)
    out["rerendering"] = True
    return out


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    t = studio.house.takes
    rid, cid = args.get("take_id"), args.get("clip_id")
    if name == "get_clip_draft":
        return get(t, actor, rid, cid)
    if name == "edit_clip_draft":
        return put(t, actor, rid, cid, {"draft": args.get("draft"), "note": args.get("note")})
    if name == "direct_clip":
        return direct(t, actor, rid, cid, {k: args[k] for k in ("instruction", "selection", "ai") if k in args})
    return render(t, actor, rid, cid)
