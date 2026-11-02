"""The AI engine switch: which model the in-app agent uses first, what each one has left, and the automatic fallback.

Engines (ids are stable; REST, MCP and the UI use them):
  claude        Claude (Anthropic) on HuhQueue's key: $1 allowance per signed-in user (budget.caps), app-wide pool.
  gemini-flash  Gemini 2.5 Flash via Oracle Cloud (OCI Generative AI): free during the contest, metered per user and app-wide
  gemini-pro    Gemini 2.5 Pro via Oracle Cloud: same Oracle credit, slower and stronger.
  own           Your own API key (Settings > AI engine > Bring your own key), when one is saved.
  free          The free starter model (no tool use: the agent follows the standard plan).

The person picks one; brain_llm.engines() puts it first and keeps the rest in DEFAULT_ORDER behind it, so a model that is
out of credit or erroring hands over to the next one with a short note in the run (agent_brain._go). An engine that
failed for a reason that will not clear in seconds (a 4xx from Oracle, a zero quota) is marked down for a few minutes so
runs don't keep paying the timeout; the picker shows that as "Unavailable right now".
"""
from __future__ import annotations

import threading
import time

IDS = ("claude", "gemini-flash", "gemini-pro", "own", "free")
DEFAULT_ORDER = ("claude", "gemini-flash", "own", "free")
LABELS = {"claude": "Claude (Anthropic)", "gemini-flash": "Gemini 2.5 Flash (Oracle Cloud)",
          "gemini-pro": "Gemini 2.5 Pro (Oracle Cloud)", "own": "Your own key", "free": "Free starter model"}
SHORT = {"claude": "Claude", "gemini-flash": "Gemini Flash", "gemini-pro": "Gemini Pro", "own": "Your key", "free": "Free model"}
ABOUT = {"claude": "Best writing and tool use. Included allowance per account.",
         "gemini-flash": "Fast and capable, on Oracle Cloud. Free during the contest.",
         "gemini-pro": "Gemini's strongest model, on Oracle Cloud. Slower; free during the contest.",
         "own": "Your provider bills you directly; HuhQueue caps tokens per day to protect your key.",
         "free": "Always available. Follows the standard plan (no tool use)."}
DOWN_TTL = 300  # seconds an engine stays "unavailable right now" after a hard failure

_down: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()


class EngineError(Exception):
    pass


def mark_down(eid: str, reason: str, ttl: float = DOWN_TTL) -> None:
    with _lock:
        _down[eid] = (time.time() + ttl, reason[:200])


def down(eid: str) -> str | None:
    """The reason this engine is down, or None when it can be tried."""
    with _lock:
        v = _down.get(eid)
        if v and v[0] > time.time():
            return v[1]
        _down.pop(eid, None)
        return None


def clear_down() -> None:
    with _lock:
        _down.clear()


def get_pref(store, uid: str) -> str:
    rows = store.q("SELECT engine FROM ai_engine_pref WHERE user_id=?", (uid,))
    e = rows[0]["engine"] if rows else ""
    return e if e in IDS else "auto"


def set_pref(store, uid: str, engine: str) -> str:
    e = (engine or "").strip().lower()
    e = {"anthropic": "claude", "gemini": "gemini-flash", "oracle": "gemini-flash", "oci": "gemini-flash", "byo": "own",
         "your key": "own", "starter": "free"}.get(e, e)
    if e not in IDS and e != "auto":
        raise EngineError(f"Unknown AI engine {engine!r}. Pick one of: auto, {', '.join(IDS)}.")
    store.x("INSERT INTO ai_engine_pref(user_id,engine,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "engine=excluded.engine, updated=excluded.updated", (uid, e, time.time()))
    return e


def order(pref: str) -> list[str]:
    """The try-order: the chosen engine first, then the default order (Gemini Pro only when chosen)."""
    rest = [e for e in DEFAULT_ORDER if e != pref]
    return ([pref] if pref in IDS else []) + rest


def status(store, uid: str, settings, http=None, demo: bool = False) -> dict:
    """Everything the picker shows: each engine with availability and its credit in plain words, the choice, and which
    engine will actually run next (the first available one in the try-order)."""
    from . import brain_llm, budget, llm, oci_genai
    pref = get_pref(store, uid)
    own = None
    try:
        own = llm.resolve(store, uid, settings)
    except Exception:  # a broken saved key never breaks the picker
        own = None
    has_own = bool(own and own.get("source") == "your key")
    free_ok = bool(getattr(settings, "ai_base_url", ""))
    client = brain_llm.client_brain_active()
    opts = []
    for eid in IDS:
        o = {"id": eid, "label": LABELS[eid], "short": SHORT[eid], "about": ABOUT[eid], "available": True, "reason": "",
             "credit": None, "selected": pref == eid}
        if eid == "claude":
            o["model"] = "Claude Sonnet / Haiku"
            if not brain_llm.operator_key():
                o["available"], o["reason"] = False, "Not set up on this server."
            else:
                cr = budget.claude_credit(store, uid)
                o["credit"] = {**cr, "label": f"Claude: {cr['label']}", "kind": "allowance"}
                if cr["out"]:
                    o["available"], o["reason"] = False, "Out of credit."
        elif eid in ("gemini-flash", "gemini-pro"):
            o["model"] = "google.gemini-2.5-flash" if eid == "gemini-flash" else "google.gemini-2.5-pro"
            if not oci_genai.configured():
                o["available"], o["reason"] = False, "Not set up on this server."
            else:
                cr = budget.oci_credit(store, uid)
                o["credit"] = {**cr, "label": f"Gemini (Oracle): free during the contest, {cr['label']}", "kind": "oracle"}
                if cr["out"]:
                    o["available"], o["reason"] = False, "Out of credit."
        elif eid == "own":
            o["model"] = (own or {}).get("model", "") if has_own else ""
            if not has_own:
                o["available"], o["reason"] = False, "Add a key below to use it."
            else:
                o["credit"] = {"label": "Your key: billed by your provider", "kind": "own", "level": "ok", "used_pct": 0}
        elif eid == "free":
            o["model"] = getattr(settings, "ai_model", "") or "starter"
            o["credit"] = {"label": "Free: no charge", "kind": "free", "level": "ok", "used_pct": 0}
            if not free_ok:
                o["available"], o["reason"] = False, "Not set up on this server; the built-in writer runs instead."
        if o["available"] and eid != "own":
            why = down(eid)
            if why:
                o["available"], o["reason"] = False, f"Unavailable right now: {why}"
        if demo and eid in ("claude", "gemini-flash", "gemini-pro"):
            o["available"], o["reason"] = False, "Not in the free sample. Sign in with Buffer to use it."
        if client and eid in ("claude", "gemini-flash", "gemini-pro"):
            o["available"], o["reason"] = False, "Your AI app is the brain on this call."
        opts.append(o)
    by = {o["id"]: o for o in opts}
    seq = order(pref if pref != "auto" else "")
    runs = next((e for e in seq if by[e]["available"]), None)
    note = ""
    if pref not in ("auto", runs or "") and pref in by:
        note = (f"{by[pref]['short']} isn't available ({by[pref]['reason'].rstrip('.').lower() or 'unavailable'}), so "
                f"{by[runs]['short'] if runs else 'the built-in writer'} runs instead.")
    return {"selected": pref, "runs": runs or "built-in", "runs_label": by[runs]["label"] if runs else "Built-in writer",
            "order": seq, "note": note, "engines": opts,
            "claude_pool": opts[0]["credit"]["pool"] if opts[0].get("credit") else None,
            "oracle_pool": (by["gemini-flash"].get("credit") or {}).get("pool")}
