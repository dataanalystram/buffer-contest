"""The Style Ledger, v2: rules a brand has taught or learned, grouped, scoped, explained and proven.

What it adds on top of the rule writer in ``brand.py`` (parse_correction / apply_text_rules / brand_check):

* **Groups.** Every rule sits in one group a person can scan: voice, words to avoid or use, hashtags, emojis, calls to
  action, visual and brand. A rule can be for every channel or scoped to some (``scope``: ["instagram", ...]); text rules
  are applied per channel (``brand.for_channel``), so a LinkedIn-only rule never touches the Instagram caption.
* **Source.** ``origin`` says where a rule came from: ``taught`` (the person typed it, in the Ledger or in a run's
  thread), ``learned`` (accepted from a suggestion; ``evidence`` keeps the edit ids it was learned from), ``agent`` (an
  AI app added it through MCP), and the read-only rules that come from the brand kit itself (voice, hashtags, end card).
* **Examples.** Each time a rule changes a caption (``apply_text_rules(trace=...)``), the before -> after snippet is kept
  on the rule (newest three), so the Ledger shows what the rule really does.
* **Learning from edits.** When a person edits a caption the agent wrote, the edit is diffed into signals (a phrase
  removed, a word swapped, emojis or exclamation marks dropped, fewer hashtags, a sign-off added, a much shorter
  caption). One package counts once, however many times it is saved. When the same signal shows up in
  ``SUGGEST_AFTER`` (3) different edits, a suggestion appears ("Suggested: never use 'game-changer' -- from 3 edits")
  with Accept / Dismiss. A suggestion never applies until a person accepts it; dismissed stays dismissed.
* **Nothing from outside content.** A rule the agent proposes while working on a run is saved only when the person's
  own words in that run (the request or a reply) carry it (``from_person``); a sentence on a web page or in a
  transcript that says "never say X" can't add a rule (the hold in agent_brain covers the model side, PR #78).
* **Review links.** A shareable, expiring, read-only page for one run (or one channel of it) where a reviewer approves
  or asks for changes. The token is shown once; only its hash is stored. Nothing is sent to Buffer from the page.

UI = REST = MCP: the Brand > Style Ledger tab, ``/api/ledger*`` and ``/api/agent/runs/{rid}/review-links``, and the MCP
tools in ``TOOLS`` (list/add/update/delete rules, list/decide suggestions, check_draft, create/list review links).
"""
from __future__ import annotations

import difflib
import html
import json
import os
import re
import secrets
import time
from urllib.parse import parse_qs

from fastapi import Body, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import brand
from .actions import ActionError, Actor

SUGGEST_AFTER = max(2, int(os.environ.get("HQ_LEDGER_SUGGEST_AFTER", "3") or 3))
REVIEW_HOURS = 72
REVIEW_MAX_HOURS = 24 * 14
EXAMPLES_KEPT = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS style_edits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, run_id INTEGER, channel_id TEXT,
  service TEXT, before TEXT, after TEXT, signals TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS style_suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, signature TEXT, kind TEXT,
  spec TEXT, rule TEXT, scope TEXT, edits TEXT, status TEXT, rule_id INTEGER, created REAL, decided REAL);
CREATE TABLE IF NOT EXISTS review_links (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, token_hash TEXT, run_id INTEGER,
  channel_id TEXT, expires REAL, status TEXT, reviewer TEXT, note TEXT, created REAL, decided REAL, revoked INTEGER DEFAULT 0);
"""
TABLES = ("style_edits", "style_suggestions", "review_links")

GROUPS = [
    {"id": "voice", "label": "Voice and tone", "hint": "How captions sound: punctuation, case, length."},
    {"id": "words", "label": "Words to avoid or use", "hint": "Phrases that never appear, and the words you prefer."},
    {"id": "hashtags", "label": "Hashtags", "hint": "How many, and which ones always go in."},
    {"id": "emojis", "label": "Emojis", "hint": "Whether captions use them."},
    {"id": "cta", "label": "Calls to action", "hint": "Sign-offs, handles and end cards."},
    {"id": "visual", "label": "Visual and brand", "hint": "Logo, colours, fonts, overlays and templates."},
]
GROUP_IDS = [g["id"] for g in GROUPS]
TYPE_GROUP = {"banned_words": "words", "prefer_words": "words", "no_emoji": "emojis", "max_hashtags": "hashtags",
              "no_exclamation": "voice", "caption_case": "voice", "max_length": "voice", "ends_with": "cta",
              "end_card": "cta"}
SERVICES = ("instagram", "linkedin", "twitter", "facebook", "threads", "bluesky", "tiktok", "youtube", "mastodon", "pinterest",
            "googlebusiness")
STOP = set("a an the and or but of to in on at for with by from is are was were be been it its this that these those as "
           "we our you your i my they their he she his her so very just really also then than there here not no".split())
EMOJI = brand.EMOJI_RE
HASH = brand.HASHTAG_RE


def _now() -> float:
    return time.time()


def ensure(store) -> None:
    for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION").replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        store.x(stmt)
    if store.pg:
        for t in TABLES:
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
    store.x("CREATE INDEX IF NOT EXISTS style_edits_user ON style_edits(user_id, id)")
    store.x("CREATE INDEX IF NOT EXISTS review_links_hash ON review_links(token_hash)")


def group_of(r: dict) -> str:
    if r.get("kind") in ("visual", "template"):
        return "cta" if (r.get("check") or {}).get("type") == "end_card" else "visual"
    c = r.get("check") or {}
    ty = c.get("type", "")
    if ty == "require_text":
        return "hashtags" if str(c.get("text", "")).startswith("#") else "cta"
    return TYPE_GROUP.get(ty, "voice")


def norm_scope(scope) -> list[str]:
    if isinstance(scope, str):
        scope = [x for x in re.split(r"[\s,]+", scope) if x]
    out: list[str] = []
    for s in scope or []:
        s = str(s).strip().lower()
        s = {"x": "twitter", "ig": "instagram", "yt": "youtube", "fb": "facebook"}.get(s, s)
        if s in ("all", "*", "every"):
            return []
        if s in SERVICES and s not in out:
            out.append(s)
    return out


def _snip(before: str, after: str, pad: int = 6) -> dict:
    """The part of a caption that changed, with a few words either side (an example a person can read in one line)."""
    a, b = (before or "").split(), (after or "").split()
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    ops = [o for o in sm.get_opcodes() if o[0] != "equal"]
    if not ops:
        return {"before": (before or "")[:160], "after": (after or "")[:160]}
    i1, i2 = max(0, ops[0][1] - pad), min(len(a), ops[-1][2] + pad)
    j1, j2 = max(0, ops[0][3] - pad), min(len(b), ops[-1][4] + pad)

    def cut(ws, x, y, n):
        return ("… " if x > 0 else "") + " ".join(ws[x:y]) + (" …" if y < n else "")
    return {"before": cut(a, i1, i2, len(a))[:220], "after": cut(b, j1, j2, len(b))[:220]}


def _tok(w: str) -> str:
    return re.sub(r"^[^\w#@$]+|[^\w%]+$", "", w).lower()


# ------------------------------------------------------------------------------------------------ edit -> signals
def signals(before: str, after: str, service: str = "") -> list[dict]:
    """What kind of edit this was, as stable signatures. Rewrites (long replaced chunks) say nothing on their own."""
    out: dict[str, dict] = {}
    if not before or not after or before.strip() == after.strip():
        return []
    a, b = [_tok(w) for w in before.split()], [_tok(w) for w in after.split()]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        gone = [w for w in a[i1:i2] if w]
        new = [w for w in b[j1:j2] if w]
        if op == "delete" and 1 <= len(gone) <= 4:
            ph = " ".join(gone)
            if _phrase_ok(gone):
                out[f"avoid:{ph}"] = {"sig": f"avoid:{ph}", "type": "banned_words", "phrase": ph}
        elif op == "replace" and 1 <= len(gone) <= 3 and 1 <= len(new) <= 3:
            ph, to = " ".join(gone), " ".join(new)
            if _phrase_ok(gone) and ph != to and not any(w.startswith(("#", "http")) for w in new):
                out[f"prefer:{ph}=>{to}"] = {"sig": f"prefer:{ph}=>{to}", "type": "prefer_words", "from": ph, "to": to}
    if EMOJI.search(before) and not EMOJI.search(after):
        out["no_emoji"] = {"sig": "no_emoji", "type": "no_emoji"}
    nb, na = len(HASH.findall(before)), len(HASH.findall(after))
    if nb > na and nb >= 2:
        out["fewer_hashtags"] = {"sig": "fewer_hashtags", "type": "max_hashtags", "n": na}
    if "!" in before and "!" not in after:
        out["no_exclamation"] = {"sig": "no_exclamation", "type": "no_exclamation"}
    letters = re.sub(r"https?://\S+|@\w+|#\w+", "", after)
    if letters == letters.lower() and before != before.lower() and re.search(r"[a-z]{3}", letters):
        out["lowercase"] = {"sig": "lowercase", "type": "caption_case"}
    lines = after.rstrip().splitlines()
    last = lines[-1].strip() if lines else ""
    if last and last not in before and 2 <= len(last) <= 80 and len(lines) > 1:
        out[f"ends_with:{last.lower()}"] = {"sig": f"ends_with:{last.lower()}", "type": "ends_with", "text": last}
    if service and len(after) < 0.7 * len(before) and len(before) > 120:
        out[f"shorter:{service}"] = {"sig": f"shorter:{service}", "type": "max_length", "n": len(after), "service": service}
    return list(out.values())


def _phrase_ok(words: list[str]) -> bool:
    """A removed phrase worth learning: not just stopwords, numbers, links, handles or tags."""
    if not words or all(w in STOP for w in words):
        return False
    if any(w.startswith(("http", "@", "#")) or re.fullmatch(r"[\d.,%$€£]+", w) for w in words):
        return False
    return len("".join(words)) >= 4


def suggestion_rule(sig: dict, hits: list[dict]) -> tuple[str, dict, str]:
    """(kind, check, human rule) for a signal seen in enough edits."""
    ty = sig["type"]
    if ty == "banned_words":
        return "text", {"type": "banned_words", "words": [sig["phrase"]]}, f"Never use “{sig['phrase']}”"
    if ty == "prefer_words":
        return "text", {"type": "prefer_words", "from": [sig["from"]], "to": sig["to"]}, f"Say “{sig['to']}”, not “{sig['from']}”"
    if ty == "no_emoji":
        return "text", {"type": "no_emoji"}, "No emojis in captions"
    if ty == "max_hashtags":
        n = max(int(h.get("n", 0)) for h in hits) if hits else int(sig.get("n", 0))
        return "text", {"type": "max_hashtags", "n": n}, ("No hashtags" if n == 0 else f"At most {n} hashtag{'s' if n != 1 else ''}")
    if ty == "no_exclamation":
        return "text", {"type": "no_exclamation"}, "No exclamation marks"
    if ty == "caption_case":
        return "text", {"type": "caption_case", "value": "lower"}, "Captions in lowercase"
    if ty == "ends_with":
        return "text", {"type": "ends_with", "text": sig["text"]}, f"Captions end with “{sig['text']}”"
    if ty == "max_length":
        n = max(int(h.get("n", 0)) for h in hits) if hits else int(sig.get("n", 0))
        n = int((n + 49) // 50 * 50)
        return "text", {"type": "max_length", "n": n}, f"Captions under {n} characters"
    raise ValueError(ty)


def covered(sig: dict, rules: list[dict]) -> bool:
    """An active rule already does what this signal would suggest."""
    for r in rules:
        if not r.get("active", True):
            continue
        c = r.get("check") or {}
        if sig["type"] == "banned_words" and c.get("type") == "banned_words" and sig["phrase"] in [w.lower() for w in c.get("words", [])]:
            return True
        if sig["type"] == "prefer_words" and c.get("type") == "prefer_words" and sig["from"] in [w.lower() for w in c.get("from", [])]:
            return True
        if sig["type"] in ("no_emoji", "no_exclamation", "caption_case") and c.get("type") == sig["type"]:
            return True
        if sig["type"] == "max_hashtags" and c.get("type") == "max_hashtags" and int(c.get("n", 99)) <= int(sig.get("n", 0)):
            return True
        if sig["type"] == "ends_with" and c.get("type") == "ends_with" and c.get("text", "").lower() == sig["text"].lower():
            return True
        if sig["type"] == "max_length" and c.get("type") == "max_length" and (not r.get("scope") or sig.get("service") in r["scope"]):
            return True
    return False


# ------------------------------------------------------------------------------------------------ source evidence
def evidence(story: dict | None, source: dict | None) -> list[dict]:
    """Each fact on a draft with the line of the source it came from (word overlap), or marked as not found there."""
    story, source = story or {}, source or {}
    url = source.get("url") or story.get("url") or ""
    site = source.get("site") or story.get("source") or ""
    paras = [p for p in (source.get("paragraphs") or []) if isinstance(p, str) and p.strip()]
    if source.get("title"):
        paras = [source["title"]] + paras
    if source.get("description"):
        paras.append(source["description"])
    out = []
    for f in (story.get("facts") or [])[:6]:
        fw = {w for w in (_tok(x) for x in str(f).split()) if w and w not in STOP and len(w) > 2}
        best, score = "", 0.0
        for p in paras:
            pw = {w for w in (_tok(x) for x in p.split()) if w}
            if fw:
                s = len(fw & pw) / len(fw)
                if s > score:
                    best, score = p, s
        nums = re.findall(r"\d[\d.,]*\d|\d", str(f))
        nums_ok = all(any(n in p for p in paras) for n in nums) if nums else True
        out.append({"claim": str(f)[:200], "quote": best[:240] if score >= 0.34 else "", "url": url, "site": site,
                    "found": bool(score >= 0.34 and nums_ok), "match": round(score, 2)})
    return out


def from_person(correction: str, own_words: list[str]) -> bool:
    """Did the person's own words in this run carry this rule? (A page that says "never say X" can't add a rule.)"""
    c = re.sub(r"\s+", " ", (correction or "").lower()).strip(" .!")
    own = [re.sub(r"\s+", " ", (w or "").lower()) for w in own_words if w]
    if not c:
        return False
    if any(c in w for w in own):
        return True
    keys: list[str] = []
    for r in brand.parse_correction(correction):
        ch = r["check"]
        keys += [str(x).lower() for x in (ch.get("words") or []) + (ch.get("from") or [])]
        keys += [str(ch[k]).lower() for k in ("to", "text", "hex", "name", "cta") if ch.get(k)]
        word = {"no_emoji": "emoji", "no_exclamation": "exclamation", "max_hashtags": "hashtag", "caption_case": "lower",
                "logo_position": "logo", "logo_scale": "logo", "end_card": "end", "headline_case": "case", "overlay": "overlay",
                "source_credit": "credit", "max_length": "char"}.get(ch.get("type"))
        if word:
            keys.append(word)
    keys = [k.lstrip("#") for k in keys if k]
    return bool(keys) and all(any(k in w for w in own) for k in keys)


# ------------------------------------------------------------------------------------------------ the service
class Ledger:
    def __init__(self, house):
        self.h, self.store = house, house.store
        ensure(self.store)

    # ---- rules
    @staticmethod
    def rule_out(r: dict) -> dict:
        r = dict(r)
        r["group"] = group_of(r)
        r["scope"] = r.get("scope") or []
        r["origin"] = r.get("origin") or "taught"
        r["examples"] = r.get("examples") or []
        r["evidence"] = r.get("evidence") or []
        r["learned_from"] = len(r["evidence"])
        return r

    def kit_rules(self, a: Actor) -> list[dict]:
        """Read-only rules that come from the brand kit itself: voice, hashtags, end card. Edited in Brand > Kit."""
        kit = self.h.kit(a)
        out = []
        if (kit.get("voice") or "").strip():
            out.append({"id": "kit:voice", "group": "voice", "rule": f"Voice: {kit['voice'].strip()[:200]}", "origin": "kit",
                        "kind": "text", "active": True, "scope": [], "readonly": True})
        tags = []
        try:
            bs = getattr(self.h, "brand_studio", None)
            tags = (bs.extras(a).get("hashtags") or []) if bs else []
        except Exception:  # noqa: BLE001
            tags = []
        if tags:
            out.append({"id": "kit:hashtags", "group": "hashtags", "rule": "Brand hashtags: " + " ".join(str(t) for t in tags[:8]),
                        "origin": "kit", "kind": "text", "active": True, "scope": [], "readonly": True})
        ec = kit.get("end_card") or {}
        if ec.get("on", True) and (ec.get("line") or kit.get("handle")):
            line = ec.get("line") or ("@" + kit.get("handle", ""))
            out.append({"id": "kit:end_card", "group": "cta", "rule": f"Videos end on the brand end card: “{(ec.get('cta') or 'Follow')} {line}”",
                        "origin": "kit", "kind": "visual", "active": True, "scope": [], "readonly": True})
        return out

    def overview(self, a: Actor) -> dict:
        rules = [self.rule_out(r) for r in self.h.rules(a, include_inactive=True) if r.get("kind") != "template" or r.get("active")]
        sugg = self.suggestions(a)
        kit = self.kit_rules(a)
        counts = {g: 0 for g in GROUP_IDS}
        for r in rules + kit:
            counts[r["group"]] = counts.get(r["group"], 0) + 1
        return {"groups": [{**g, "count": counts.get(g["id"], 0)} for g in GROUPS], "rules": rules, "kit_rules": kit,
                "suggestions": sugg, "suggest_after": SUGGEST_AFTER, "services": list(SERVICES),
                "summary": {"active": sum(1 for r in rules if r["active"]), "learned": sum(1 for r in rules if r["origin"] == "learned"),
                            "suggested": len(sugg), "scoped": sum(1 for r in rules if r["scope"])}}

    def get_rule(self, a: Actor, rule_id: int) -> dict:
        r = next((x for x in self.h.rules(a, include_inactive=True) if x["id"] == int(rule_id)), None)
        if not r:
            raise ActionError("No such rule.", 404)
        out = self.rule_out(r)
        out["edits"] = self.edits_for(a, out["evidence"])
        return out

    def stamp(self, a: Actor, added: list[dict], scope=None, origin: str | None = None) -> None:
        sc = norm_scope(scope)
        org = origin or ("taught" if a.kind == "human" else "agent")
        for r in added or []:
            s = sc if r.get("kind") == "text" else []
            self.store.x("UPDATE style_rules SET scope=?, origin=?, updated=? WHERE id=? AND user_id=?",
                         (json.dumps(s), org, _now(), r["id"], a.uid))
            r.update(scope=s, origin=org)

    def add(self, a: Actor, correction: str, scope=None, origin: str | None = None) -> dict:
        res = self.h.remember(a, correction, scope=scope, origin=origin)
        res["ledger"] = self.h.rules(a)
        return res

    def _confirm(self, a: Actor, op: str, payload: dict, confirm_token: str | None) -> dict | None:
        """Agents change or remove a rule in two steps (preview -> the person says yes -> confirm_token). People: one step."""
        if a.kind == "human":
            return None
        from .store import sha
        key = json.dumps({"op": op, **payload}, sort_keys=True, default=str)
        if confirm_token:
            rows = self.store.q("SELECT * FROM confirms WHERE token_hash=? AND user_id=? AND used=0 AND expires>?",
                                (sha(confirm_token), a.uid, _now()))
            if not rows or rows[0]["payload"] != key:
                raise ActionError("That confirm_token is wrong, used or expired. Ask again without it for a new one.", 409)
            self.store.x("UPDATE confirms SET used=1 WHERE token_hash=?", (sha(confirm_token),))
            return None
        tok = "lc_" + secrets.token_urlsafe(18)
        self.store.x("INSERT INTO confirms(token_hash,user_id,agent_id,payload,expires,used) VALUES(?,?,?,?,?,0)",
                     (sha(tok), a.uid, (a.agent or {}).get("id"), key, _now() + 900))
        return {"needs_confirmation": True, "confirm_token": tok, "preview": payload,
                "next": "Show the person this change. When they say yes, call again with the same arguments and confirm_token "
                        "(valid 15 minutes, one use)."}

    def update(self, a: Actor, rule_id: int, body: dict) -> dict:
        self.h.studio._guard_write(a, "update_style_rule")
        r = self.get_rule(a, rule_id)
        changes: dict = {}
        if body.get("active") is not None:
            if a.kind != "human":
                raise ActionError("Only a human can switch ledger rules on or off; agents can add them.", 403)
            changes["active"] = bool(body["active"])
        if body.get("scope") is not None:
            sc = norm_scope(body["scope"])
            if r["kind"] != "text" and sc:
                raise ActionError("Visual rules apply to every channel (the render is shared); scope a text rule instead.")
            changes["scope"] = sc
        text = str(body.get("rule") or body.get("correction") or "").strip()[:500]
        if text:
            found = brand.parse_correction(text)
            if len(found) != 1:
                raise ActionError("Say the rule in one plain sentence, e.g. “never say game-changer or synergy”, “max 2 hashtags”.")
            changes.update(check=found[0]["check"], rule=found[0]["rule"], kind=found[0]["kind"], source=text)
        if not changes:
            raise ActionError("Nothing to change: give rule (plain words), scope or active.")
        held = self._confirm(a, "update", {"rule_id": int(rule_id), **{k: v for k, v in changes.items() if k != "check"}},
                             body.get("confirm_token"))
        if held:
            return held
        sets, args = [], []
        if "active" in changes:
            sets.append("active=?")
            args.append(1 if changes["active"] else 0)
        if "scope" in changes:
            sets.append("scope=?")
            args.append(json.dumps(changes["scope"]))
        if "check" in changes:
            sets += ["spec=?", "rule=?", "kind=?", "source=?"]
            args += [json.dumps(changes["check"]), changes["rule"], changes["kind"], changes["source"]]
        sets.append("updated=?")
        args.append(_now())
        self.store.x(f"UPDATE style_rules SET {', '.join(sets)} WHERE id=? AND user_id=?", (*args, int(rule_id), a.uid))
        self.store.audit(a.uid, a.label, "update_style_rule", "write", {"rule": int(rule_id), "fields": sorted(changes)})
        return self.get_rule(a, rule_id)

    def delete(self, a: Actor, rule_id: int, confirm_token: str | None = None) -> dict:
        r = self.get_rule(a, rule_id)
        held = self._confirm(a, "delete", {"rule_id": int(rule_id), "rule": r["rule"]}, confirm_token)
        if held:
            return held
        self.store.x("DELETE FROM style_rules WHERE id=? AND user_id=?", (int(rule_id), a.uid))
        self.store.audit(a.uid, a.label, "delete_style_rule", "write", {"rule": r["rule"][:120]})
        return {"deleted": int(rule_id)}

    def record_examples(self, uid: str, trace: list[dict]) -> None:
        """Keep the newest before -> after for each rule that changed a caption."""
        by: dict[int, list] = {}
        for t in trace or []:
            by.setdefault(int(t["rule_id"]), []).append(_snip(t["before"], t["after"]))
        for rid, ex in by.items():
            rows = self.store.q("SELECT examples FROM style_rules WHERE id=? AND user_id=?", (rid, uid))
            if not rows:
                continue
            old = json.loads(rows[0]["examples"]) if rows[0]["examples"] else []
            new = [e for e in ex if e not in old] + old
            self.store.x("UPDATE style_rules SET examples=? WHERE id=?", (json.dumps(new[:EXAMPLES_KEPT]), rid))

    # ---- edits -> suggestions
    def record_edit(self, a: Actor, rid: int, channel_id: str, service: str, before: str, after: str) -> list[dict]:
        """A person changed a caption the agent wrote. One row per package (the first text the agent wrote stays the
        'before'), re-diffed on every save, then suggestions are refreshed. Returns suggestions that are new now."""
        if a.kind != "human" or before is None or after is None:
            return []
        rows = self.store.q("SELECT * FROM style_edits WHERE user_id=? AND run_id=? AND channel_id=?", (a.uid, int(rid), channel_id))
        orig = rows[0]["before"] if rows else before
        sig = signals(orig, after, service)
        if rows:
            self.store.x("UPDATE style_edits SET after=?, signals=?, updated=? WHERE id=?", (after, json.dumps(sig), _now(), rows[0]["id"]))
        else:
            if orig.strip() == after.strip():
                return []
            self.store.x("INSERT INTO style_edits(user_id,run_id,channel_id,service,before,after,signals,created,updated) "
                         "VALUES(?,?,?,?,?,?,?,?,?)", (a.uid, int(rid), channel_id, service, orig, after, json.dumps(sig), _now(), _now()))
        return self.refresh_suggestions(a, [s["sig"] for s in sig])

    def _edits(self, a: Actor, limit: int = 400) -> list[dict]:
        out = []
        for r in self.store.q("SELECT * FROM style_edits WHERE user_id=? ORDER BY id DESC LIMIT ?", (a.uid, limit)):
            d = dict(r)
            d["signals"] = json.loads(d["signals"] or "[]")
            out.append(d)
        return out

    def edits_for(self, a: Actor, ids: list) -> list[dict]:
        ids = [int(i) for i in ids or [] if str(i).isdigit()][:20]
        if not ids:
            return []
        out = []
        for r in self.store.q(f"SELECT * FROM style_edits WHERE user_id=? AND id IN ({','.join('?' * len(ids))}) ORDER BY id DESC",
                              (a.uid, *ids)):
            out.append({"id": r["id"], "run_id": r["run_id"], "service": r["service"], "at": r["updated"] or r["created"],
                        **_snip(r["before"], r["after"])})
        return out

    def refresh_suggestions(self, a: Actor, sigs: list[str] | None = None) -> list[dict]:
        edits = self._edits(a)
        rules = self.h.rules(a)
        by: dict[str, list] = {}
        for e in edits:
            for s in e["signals"]:
                by.setdefault(s["sig"], []).append((e, s))
        new = []
        for sig, hits in by.items():
            if sigs is not None and sig not in sigs:
                continue
            row = self.store.q("SELECT * FROM style_suggestions WHERE user_id=? AND signature=?", (a.uid, sig))
            if row and row[0]["status"] in ("dismissed", "accepted"):
                continue
            s0 = hits[0][1]
            if covered(s0, rules):
                if row:
                    self.store.x("UPDATE style_suggestions SET status='covered', decided=? WHERE id=?", (_now(), row[0]["id"]))
                continue
            if len(hits) < SUGGEST_AFTER:
                continue
            kind, check, text = suggestion_rule(s0, [s for _, s in hits])
            svcs = sorted({e["service"] for e, _ in hits if e["service"]})
            scope = [svcs[0]] if (len(svcs) == 1 and s0["type"] == "max_length") else []
            ids = [e["id"] for e, _ in hits]
            if row:
                self.store.x("UPDATE style_suggestions SET edits=?, spec=?, rule=?, scope=?, status='open' WHERE id=?",
                             (json.dumps(ids), json.dumps(check), text, json.dumps(scope), row[0]["id"]))
            else:
                sid = self.store.x("INSERT INTO style_suggestions(user_id,signature,kind,spec,rule,scope,edits,status,created) "
                                   "VALUES(?,?,?,?,?,?,?,'open',?)", (a.uid, sig, kind, json.dumps(check), text, json.dumps(scope),
                                                                     json.dumps(ids), _now()))
                self.store.audit(a.uid, "system", "rule_suggested", "read", {"rule": text, "edits": len(ids)})
                new.append(sid)
        return [s for s in self.suggestions(a) if s["id"] in new]

    def suggestions(self, a: Actor, status: str = "open") -> list[dict]:
        out = []
        for r in self.store.q("SELECT * FROM style_suggestions WHERE user_id=? AND status=? ORDER BY id DESC", (a.uid, status)):
            ids = json.loads(r["edits"] or "[]")
            edits = self.edits_for(a, ids)
            check = json.loads(r["spec"] or "{}")
            out.append({"id": r["id"], "rule": r["rule"], "kind": r["kind"], "check": check, "group": group_of({"kind": r["kind"], "check": check}),
                        "scope": json.loads(r["scope"] or "[]"), "edit_count": len(ids),
                        "services": sorted({e["service"] for e in edits if e.get("service")}), "edits": edits[:5],
                        "label": f"Suggested: {r['rule']}", "why": f"from {len(ids)} of your edits", "status": r["status"],
                        "created": r["created"]})
        return out

    def decide(self, a: Actor, sid: int, decision: str, scope=None, confirm_token: str | None = None) -> dict:
        rows = self.store.q("SELECT * FROM style_suggestions WHERE id=? AND user_id=?", (int(sid), a.uid))
        if not rows:
            raise ActionError("No such suggestion.", 404)
        r = rows[0]
        if r["status"] != "open":
            raise ActionError(f"That suggestion was already {r['status']}.", 409)
        decision = (decision or "").lower()
        if decision not in ("accept", "dismiss"):
            raise ActionError("decision is accept or dismiss.")
        held = self._confirm(a, decision + "_suggestion", {"suggestion_id": int(sid), "rule": r["rule"]}, confirm_token)
        if held:
            return held
        if decision == "dismiss":
            self.store.x("UPDATE style_suggestions SET status='dismissed', decided=? WHERE id=?", (_now(), r["id"]))
            self.store.audit(a.uid, a.label, "rule_suggestion_dismissed", "write", {"rule": r["rule"]})
            return {"dismissed": r["id"], "suggestions": self.suggestions(a)}
        check = json.loads(r["spec"])
        sc = norm_scope(scope) if scope is not None else json.loads(r["scope"] or "[]")
        ids = json.loads(r["edits"] or "[]")
        examples = [{k: e[k] for k in ("before", "after")} for e in self.edits_for(a, ids)[:EXAMPLES_KEPT]]
        if check["type"] not in ("banned_words", "require_text", "prefer_words"):
            for old in self.h.rules(a):
                if old["check"]["type"] == check["type"] and (old.get("scope") or []) == sc:
                    self.store.x("UPDATE style_rules SET active=0 WHERE id=?", (old["id"],))
        rid = self.store.x("INSERT INTO style_rules(user_id,kind,spec,rule,source,active,created,scope,origin,examples,evidence,updated) "
                           "VALUES(?,?,?,?,?,1,?,?,?,?,?,?)",
                           (a.uid, r["kind"], json.dumps(check), r["rule"], f"Learned from {len(ids)} of your edits", _now(), json.dumps(sc),
                            "learned", json.dumps(examples), json.dumps(ids), _now()))
        self.store.x("UPDATE style_suggestions SET status='accepted', decided=?, rule_id=? WHERE id=?", (_now(), rid, r["id"]))
        self.store.audit(a.uid, a.label, "rule_suggestion_accepted", "write", {"rule": r["rule"], "edits": len(ids)})
        return {"accepted": r["id"], "rule": self.get_rule(a, rid), "suggestions": self.suggestions(a)}

    # ---- a draft against the ledger
    @staticmethod
    def applied(rules: list[dict], service: str) -> list[dict]:
        """The rules a package for this network followed (text rules in scope, and every visual rule)."""
        out = []
        for r in brand.for_channel(rules, service):
            if not r.get("active", True) or r.get("kind") == "template":
                continue
            out.append({"id": r.get("id"), "rule": r["rule"], "group": group_of(r), "origin": r.get("origin") or "taught",
                        "scope": r.get("scope") or []})
        return out

    def check_draft(self, a: Actor, text: str = "", service: str = "", media_token: str = "", run_id=None,
                    channel_id: str = "") -> dict:
        rules = self.h.rules(a)
        kit = self.h.kit(a)
        ev: list = []
        if run_id:
            run = self.h.run(a, int(run_id))
            res = run["result"] or {}
            pk = next((p for p in res.get("packages") or [] if p["channel_id"] == channel_id or p["service"] == channel_id), None)
            if pk:
                text = text or pk["text"]
                service = service or pk["service"]
            ev = evidence(res.get("story"), res.get("source"))
        service = (norm_scope([service]) or [service or ""])[0]
        scoped = brand.for_channel(rules, service)
        meta = self.h.media_meta(a, media_token) if media_token else None
        res = brand.brand_check(text, meta, scoped, kit)
        trace: list = []
        fixed = brand.apply_text_rules(text, scoped, trace)
        return {**res, "service": service or "all", "applied_rules": self.applied(rules, service),
                "fixed_text": fixed if fixed != text else "",
                "fixes": [{"rule_id": t["rule_id"], **_snip(t["before"], t["after"])} for t in trace],
                "evidence": ev, "unsupported_claims": [e["claim"] for e in ev if not e["found"]]}

    # ---- review links
    def create_review(self, a: Actor, rid: int, channel_id: str = "", hours=REVIEW_HOURS) -> dict:
        self.h.studio._guard_write(a, "create_review_link")
        run = self.h.run(a, int(rid))
        pkgs = (run["result"] or {}).get("packages") or []
        if not pkgs:
            raise ActionError("This run has no drafts to review yet.")
        if channel_id and not any(p["channel_id"] == channel_id for p in pkgs):
            raise ActionError("No draft for that channel in this run.", 404)
        hours = max(1.0, min(float(hours or REVIEW_HOURS), float(REVIEW_MAX_HOURS)))
        from .store import sha
        tok = secrets.token_urlsafe(24)
        lid = self.store.x("INSERT INTO review_links(user_id,token_hash,run_id,channel_id,expires,status,created,revoked) "
                           "VALUES(?,?,?,?,?,'open',?,0)", (a.uid, sha("review:" + tok), int(rid), channel_id or "", _now() + hours * 3600, _now()))
        self.store.audit(a.uid, a.label, "create_review_link", "write", {"run": int(rid), "channel": channel_id or "all", "hours": hours})
        base = (getattr(self.h.s, "public_url", "") or "").rstrip("/")
        return {"id": lid, "url": f"{base}/review/{tok}", "expires": _now() + hours * 3600, "hours": hours, "channel_id": channel_id or "",
                "note": "Anyone with this link can read these drafts and approve or ask for changes until it expires. It can't post, "
                        "edit or see anything else."}

    def reviews(self, a: Actor, rid: int) -> list[dict]:
        self.h.run(a, int(rid))
        out = []
        for r in self.store.q("SELECT * FROM review_links WHERE user_id=? AND run_id=? ORDER BY id DESC", (a.uid, int(rid))):
            st = "revoked" if r["revoked"] else ("expired" if r["expires"] < _now() and r["status"] == "open" else r["status"])
            out.append({"id": r["id"], "channel_id": r["channel_id"], "status": st, "reviewer": r["reviewer"] or "", "note": r["note"] or "",
                        "expires": r["expires"], "created": r["created"], "decided": r["decided"]})
        return out

    def revoke_review(self, a: Actor, lid: int) -> dict:
        self.store.x("UPDATE review_links SET revoked=1 WHERE id=? AND user_id=?", (int(lid), a.uid))
        return {"revoked": int(lid)}

    def _link(self, tok: str) -> dict | None:
        from .store import sha
        if not tok or len(tok) > 80:
            return None
        rows = self.store.q("SELECT * FROM review_links WHERE token_hash=?", (sha("review:" + tok),))
        if not rows or rows[0]["revoked"] or rows[0]["expires"] < _now():
            return None
        return dict(rows[0])

    def review_view(self, tok: str) -> dict | None:
        link = self._link(tok)
        if not link:
            return None
        a = Actor(link["user_id"])
        try:
            run = self.h.run(a, int(link["run_id"]))
        except ActionError:
            return None
        res = run["result"] or {}
        pkgs = [p for p in res.get("packages") or [] if not link["channel_id"] or p["channel_id"] == link["channel_id"]]
        rules = self.h.rules(a)
        kit = self.h.kit(a)
        return {"link": link, "tok": tok, "brand": kit.get("name") or "", "title": (res.get("story") or {}).get("headline") or "",
                "evidence": evidence(res.get("story"), res.get("source")),
                "drafts": [{"service": p["service"], "channel": p.get("channel_name") or p["service"], "text": p["text"],
                            "media": list(p.get("media_urls") or [])[:10], "format": p.get("format", ""),
                            "check": (p.get("check") or {}).get("summary", ""), "applied": self.applied(rules, p["service"])} for p in pkgs]}

    def review_decide(self, tok: str, decision: str, note: str = "", reviewer: str = "") -> dict:
        link = self._link(tok)
        if not link:
            raise ActionError("This review link has expired or was turned off.", 410)
        decision = {"approve": "approved", "approved": "approved", "changes": "changes_requested",
                    "request_changes": "changes_requested"}.get((decision or "").lower(), "")
        if not decision:
            raise ActionError("Choose approve or request changes.")
        note = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", note or "")[:1000]
        reviewer = re.sub(r"[^\w .'@-]", "", reviewer or "")[:60]
        if decision == "changes_requested" and not note.strip():
            raise ActionError("Say what to change.")
        self.store.x("UPDATE review_links SET status=?, note=?, reviewer=?, decided=? WHERE id=?", (decision, note, reviewer, _now(), link["id"]))
        a = Actor(link["user_id"])
        try:  # the run's thread shows the review to its owner; a reviewer's note is quoted, never treated as an instruction
            run = self.h.run(a, int(link["run_id"]))
            who = reviewer or "A reviewer"
            what = "approved the drafts" if decision == "approved" else "asked for changes"
            line = (f"{who} {what} through a review link" + (f" ({link['channel_id']})" if link["channel_id"] else "")
                    + (f": “{note.strip()[:300]}”" if note.strip() else "."))
            res = run["result"]
            res.setdefault("reviews", []).append({"link": link["id"], "decision": decision, "note": note, "reviewer": reviewer, "at": _now()})
            res.setdefault("thread", []).append({"role": "agent", "text": line, "at": _now(), "kind": "review"})
            self.h._save(int(link["run_id"]), run["steps"], res)
        except Exception:  # noqa: BLE001
            pass
        self.store.audit(link["user_id"], "reviewer", "review_decision", "write", {"run": link["run_id"], "decision": decision})
        return {"ok": True, "decision": decision}


# ------------------------------------------------------------------------------------------------ the public review page
def _is_video(u: str) -> bool:
    return u.lower().split("?")[0].endswith((".mp4", ".mov", ".webm", ".m4v"))


def review_page(view: dict | None, done: str = "", error: str = "") -> str:
    e = html.escape
    head = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="robots" content="noindex"><title>Review drafts · HuhQueue</title><link rel="stylesheet" href="/static/fonts.css">'
            '<link rel="stylesheet" href="/static/ledger.css"></head><body class="rv">')
    if not view:
        return head + ('<main class="rv-main"><h1>This review link has expired</h1><p class="muted">Ask the person who sent it for '
                       'a new one.</p></main></body></html>')
    ln = view["link"]
    status = ln.get("status") or "open"
    cards = []
    for d in view["drafts"]:
        media = "".join(f'<video src="{e(u)}" controls playsinline preload="metadata"></video>' if _is_video(u)
                        else f'<img src="{e(u)}" alt="{e(d["service"])} image {i + 1}" loading="lazy">' for i, u in enumerate(d["media"][:4]))
        rules = ""
        if d["applied"]:
            rules = (f'<details><summary>{len(d["applied"])} brand rule{"s" if len(d["applied"]) != 1 else ""} applied</summary><ul>'
                     + "".join(f'<li>{e(r["rule"])}</li>' for r in d["applied"]) + "</ul></details>")
        cards.append(f'<article class="rv-draft" data-testid="review-draft"><header><b>{e(d["channel"])}</b><small>{e(d["service"].title())}'
                     f'{" · " + e(d["format"]) if d["format"] else ""}</small></header><div class="rv-media">{media}</div>'
                     f'<p class="rv-text">{e(d["text"])}</p>{rules}</article>')
    ev = "".join(f'<li class="{"ok" if x["found"] else "warn"}"><b>{e(x["claim"])}</b>'
                 + (f'<q>{e(x["quote"])}</q>' if x["quote"] else '<span>Not found in the source text</span>')
                 + (f' <a href="{e(x["url"])}" rel="noopener noreferrer nofollow" target="_blank">{e(x["site"] or "Source")}</a>' if x["url"] else "")
                 + "</li>" for x in view["evidence"])
    if done or status in ("approved", "changes_requested"):
        msg = done or ("Approved." if status == "approved" else "Changes requested.")
        form = f'<div class="rv-done" role="status" data-testid="review-done">{e(msg)} The owner sees it in their thread.</div>'
    else:
        form = (f'<form class="rv-form" method="post" action="/review/{e(view["tok"])}/decision" data-testid="review-form">'
                + (f'<p class="rv-err" role="alert">{e(error)}</p>' if error else "")
                + '<label>Your name (optional)<input name="reviewer" maxlength="60" autocomplete="name"></label>'
                  '<label>Note<textarea name="note" rows="3" maxlength="1000" placeholder="What to change, or a note for the team"></textarea></label>'
                  '<div class="rv-acts"><button class="rv-btn primary" name="decision" value="approve" data-testid="review-approve">Approve</button>'
                  '<button class="rv-btn" name="decision" value="changes" data-testid="review-changes">Request changes</button></div></form>')
    exp = time.strftime("%d %b %Y, %H:%M UTC", time.gmtime(ln["expires"]))
    return head + (f'<main class="rv-main"><p class="rv-kicker">{e(view["brand"] or "HuhQueue")} · drafts for review</p>'
                   f'<h1>{e(view["title"] or "Drafts")}</h1><p class="muted">Read-only. This link expires {e(exp)}. Nothing here posts anything.</p>'
                   f'<div class="rv-grid">{"".join(cards)}</div>'
                   + (f'<section class="rv-ev"><h2>Where the facts come from</h2><ul>{ev}</ul></section>' if ev else "")
                   + form + "</main></body></html>")


# ------------------------------------------------------------------------------------------------ MCP + REST
TOOLS = [
    {"name": "list_style_rules", "description": "The Style Ledger: every brand rule, grouped (voice, words, hashtags, emojis, cta, visual), with where it came from (taught / learned from N edits / agent / brand kit), its scope (all channels or some), on/off, before -> after examples, plus open rule suggestions learned from the person's edits.",
     "inputSchema": {"type": "object", "properties": {"group": {"type": "string", "enum": GROUP_IDS}, "channel": {"type": "string", "description": "Only rules that apply on this network"}}}},
    {"name": "add_style_rule", "description": "Teach a brand rule in plain words ('never say game-changer', 'max 2 hashtags', 'say launch not drop'). It applies to every later caption and render. Use only when the person asked for it.",
     "inputSchema": {"type": "object", "required": ["rule"], "properties": {"rule": {"type": "string"}, "scope": {"type": "array", "items": {"type": "string"}, "description": "Networks this text rule is for (e.g. ['linkedin']); omit for every channel"}}}},
    {"name": "update_style_rule", "description": "Change a rule's wording or its scope. Two steps: the first call returns a preview and confirm_token; show it to the person, then call again with confirm_token. Switching rules on or off is the person's, in the app.",
     "inputSchema": {"type": "object", "required": ["rule_id"], "properties": {"rule_id": {"type": "integer"}, "rule": {"type": "string"},
                     "scope": {"type": "array", "items": {"type": "string"}}, "confirm_token": {"type": "string"}}}},
    {"name": "delete_style_rule", "description": "Delete a Style Ledger rule. Two steps: preview + confirm_token, then call again with it once the person says yes.",
     "inputSchema": {"type": "object", "required": ["rule_id"], "properties": {"rule_id": {"type": "integer"}, "confirm_token": {"type": "string"}}}},
    {"name": "list_rule_suggestions", "description": "Rules HuhQueue suggests because the person made the same kind of caption edit several times (with the edits as before -> after). They never apply until accepted.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "decide_rule_suggestion", "description": "Accept or dismiss a rule suggestion for the person (two steps: preview + confirm_token). scope narrows an accepted text rule to some networks.",
     "inputSchema": {"type": "object", "required": ["suggestion_id", "decision"], "properties": {"suggestion_id": {"type": "integer"},
                     "decision": {"type": "string", "enum": ["accept", "dismiss"]}, "scope": {"type": "array", "items": {"type": "string"}},
                     "confirm_token": {"type": "string"}}}},
    {"name": "check_draft", "description": "Check a draft against the Style Ledger for one network: which rules apply, which fail (each names the rule), the auto-fixed text, and with run_id + channel_id the source evidence for every fact (the source line it came from, or 'not found').",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "service": {"type": "string"}, "media_token": {"type": "string"},
                     "run_id": {"type": "integer"}, "channel_id": {"type": "string"}}}},
    {"name": "create_review_link", "description": "A shareable read-only link to a run's drafts (or one channel's) for someone to approve or request changes. Expires (default 72 hours, max 14 days); nothing can be posted from it. The decision shows in the run's thread.",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}, "channel_id": {"type": "string"},
                     "hours": {"type": "number"}}}},
    {"name": "list_review_links", "description": "Review links made for a run and what each reviewer decided (approved / changes_requested with their note).",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}}}},
]
NAMES = {t["name"] for t in TOOLS}
READ_ONLY = {"list_style_rules", "list_rule_suggestions", "check_draft", "list_review_links"}


def call_tool(studio, actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    L = studio.house.ledger
    args = args or {}
    if name == "list_style_rules":
        ov = L.overview(actor)
        if args.get("group"):
            ov["rules"] = [r for r in ov["rules"] if r["group"] == args["group"]]
        if args.get("channel"):
            svc = norm_scope([args["channel"]])
            ov["rules"] = brand.for_channel(ov["rules"], svc[0] if svc else str(args["channel"]))
        return ov
    if name == "add_style_rule":
        return L.add(actor, str(args.get("rule") or ""), args.get("scope"))
    if name == "update_style_rule":
        return L.update(actor, int(args.get("rule_id") or 0), {k: args.get(k) for k in ("rule", "scope", "confirm_token") if k in args})
    if name == "delete_style_rule":
        return L.delete(actor, int(args.get("rule_id") or 0), args.get("confirm_token"))
    if name == "list_rule_suggestions":
        return {"suggestions": L.suggestions(actor), "suggest_after": SUGGEST_AFTER}
    if name == "decide_rule_suggestion":
        return L.decide(actor, int(args.get("suggestion_id") or 0), str(args.get("decision") or ""), args.get("scope"), args.get("confirm_token"))
    if name == "check_draft":
        return L.check_draft(actor, str(args.get("text") or ""), str(args.get("service") or ""), str(args.get("media_token") or ""),
                             args.get("run_id"), str(args.get("channel_id") or ""))
    if name == "create_review_link":
        return L.create_review(actor, int(args.get("run_id") or 0), str(args.get("channel_id") or ""), args.get("hours") or REVIEW_HOURS)
    if name == "list_review_links":
        return {"reviews": L.reviews(actor, int(args.get("run_id") or 0))}
    return NotImplemented


def add_routes(app, house, actor) -> None:
    L = house.ledger

    @app.get("/api/ledger")
    def ledger_overview(request: Request):
        return L.overview(actor(request))

    @app.get("/api/ledger/rules/{rule_id}")
    def ledger_rule(rule_id: int, request: Request):
        return L.get_rule(actor(request), rule_id)

    @app.patch("/api/ledger/rules/{rule_id}")
    def ledger_rule_update(rule_id: int, request: Request, body: dict = Body(default={})):
        return L.update(actor(request, write=True), rule_id, body or {})

    @app.get("/api/ledger/suggestions")
    def ledger_suggestions(request: Request):
        return {"suggestions": L.suggestions(actor(request)), "suggest_after": SUGGEST_AFTER}

    @app.post("/api/ledger/suggestions/{sid}")
    def ledger_suggestion_decide(sid: int, request: Request, body: dict = Body(default={})):
        b = body or {}
        return L.decide(actor(request, write=True), sid, str(b.get("decision") or ""), b.get("scope"), b.get("confirm_token"))

    @app.post("/api/ledger/check")
    def ledger_check(request: Request, body: dict = Body(default={})):
        b = body or {}
        return L.check_draft(actor(request), str(b.get("text") or ""), str(b.get("service") or ""), str(b.get("media_token") or ""),
                             b.get("run_id"), str(b.get("channel_id") or ""))

    @app.post("/api/agent/runs/{rid}/review-links")
    def review_create(rid: int, request: Request, body: dict = Body(default={})):
        b = body or {}
        return L.create_review(actor(request, write=True), rid, str(b.get("channel_id") or ""), b.get("hours") or REVIEW_HOURS)

    @app.get("/api/agent/runs/{rid}/review-links")
    def review_list(rid: int, request: Request):
        return {"reviews": L.reviews(actor(request), rid)}

    @app.delete("/api/review-links/{lid}")
    def review_revoke(lid: int, request: Request):
        return L.revoke_review(actor(request, write=True), lid)

    # The public, read-only review page: no session and no JS; it can only record a decision on its own link.
    def _page(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex",
                                                               "Referrer-Policy": "no-referrer"})

    @app.get("/review/{tok}", include_in_schema=False)
    def review_get(tok: str):
        v = L.review_view(tok)
        return _page(review_page(v), 200 if v else 410)

    @app.post("/review/{tok}/decision", include_in_schema=False)
    async def review_post(tok: str, request: Request):
        raw = (await request.body())[:8000]
        js = "json" in request.headers.get("content-type", "")
        if js:
            try:
                form = json.loads(raw or b"{}")
            except ValueError:
                form = {}
        else:
            form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
        try:
            out = L.review_decide(tok, str(form.get("decision") or ""), str(form.get("note") or ""), str(form.get("reviewer") or ""))
        except ActionError as e:
            if js:
                return JSONResponse({"error": str(e)}, status_code=e.status)
            v = L.review_view(tok)
            return _page(review_page(v, error=str(e)), 400 if v else 410)
        if js:
            return out
        v = L.review_view(tok)
        return _page(review_page(v, done="Approved. Thank you." if out["decision"] == "approved" else "Changes requested. Thank you."))
