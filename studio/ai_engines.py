"""The AI engine switch: which model the in-app agent uses first, what each one has left, and the automatic fallback.

Engines (ids are stable; REST, MCP and the UI use them):
  gemini-studio Gemini 2.5 Flash on Google AI Studio's free tier (GOOGLE_AI_STUDIO_KEY), free_engines.
  nvidia        NVIDIA NIM on its free developer credits (NVIDIA_API_KEY): Nemotron Ultra plans, Nemotron Lightning is fast.
  hf            Hugging Face Inference Providers on the free monthly credits (HF_TOKEN): Qwen3 235B.
  claude        Claude (Anthropic) on HuhQueue's key: $1 allowance per signed-in user (budget.caps), app-wide pool.
  gemini-flash  Gemini 2.5 Flash via Oracle Cloud (OCI Generative AI), metered per user and app-wide on the Oracle ledger.
  gemini-pro    Gemini 2.5 Pro via Oracle Cloud: same Oracle credit, slower and stronger (only when chosen).
  own           Your own API key (Settings > AI engine > Bring your own key), when one is saved.
  free          The free starter model (no tool use: the agent follows the standard plan).

Automatic order: the person's choice first, then the free engines (AI Studio, NVIDIA, Hugging Face, Oracle when it can
serve), then Claude, the person's own key and the free starter model. An engine that is out of credit or erroring hands
over to the next one with a short note in the run (agent_brain._go). An engine that failed for a reason that will not
clear in seconds (a 4xx, a zero quota, spent free credits) is marked down for a while so runs don't keep paying the
timeout; the picker shows that as "Unavailable right now".

Oracle is checked on its own clock: one tiny probe call at most every ORACLE_RECHECK seconds (6 h), never once per run.
While the last probe failed, both Oracle engines stay listed but unavailable with the probe's reason.
"""
from __future__ import annotations

import threading
import time

from . import free_engines as _fe

IDS = ("gemini-studio", "nvidia", "hf", "claude", "gemini-flash", "gemini-pro", "own", "free")
DEFAULT_ORDER = ("gemini-studio", "nvidia", "hf", "gemini-flash", "claude", "own", "free")
FREE_IDS = _fe.IDS
ORACLE_IDS = ("gemini-flash", "gemini-pro")
LABELS = {**{e: c["label"] for e, c in _fe.ENGINES.items()}, "claude": "Claude (Anthropic)",
          "gemini-flash": "Gemini 2.5 Flash (Oracle Cloud)", "gemini-pro": "Gemini 2.5 Pro (Oracle Cloud)",
          "own": "Your own key", "free": "Free starter model"}
SHORT = {**{e: c["short"] for e, c in _fe.ENGINES.items()}, "claude": "Claude", "gemini-flash": "Gemini Flash (Oracle)",
         "gemini-pro": "Gemini Pro (Oracle)", "own": "Your key", "free": "Free model"}
ABOUT = {**{e: c["about"] for e, c in _fe.ENGINES.items()},
         "claude": "Best writing and tool use. Included allowance per account.",
         "gemini-flash": "Fast and capable, on Oracle Cloud. Free during the contest.",
         "gemini-pro": "Gemini's strongest model, on Oracle Cloud. Slower; free during the contest.",
         "own": "Your provider bills you directly; HuhQueue caps tokens per day to protect your key.",
         "free": "Always available. Follows the standard plan (no tool use)."}
ORACLE_RECHECK = 6 * 3600
ORACLE_PARIS = "Unavailable: Oracle's Paris region can't serve Gemini yet"
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


# ---------------------------------------------------------------- Oracle availability, re-checked every 6 h
_oracle = {"at": 0.0, "ok": None, "reason": "", "running": False}


def oracle_reason(msg: str) -> str:
    """The picker's words for a failed Oracle call."""
    m = (msg or "").lower()
    if "publisher model" in m or ("was not found" in m and "gemini" in m):
        return ORACLE_PARIS  # Oracle's backend in the region can't reach Gemini: an Oracle-side gap, not ours
    if "limit" in m and "set to 0" in m:
        return "Unavailable: Oracle's Gemini quota for this account is 0"
    return "Unavailable: Oracle Cloud refused the call (" + (msg.split(":")[0][:80] or "error") + ")"


def oracle_set(ok: bool, reason: str = "", at: float | None = None) -> None:
    with _lock:
        _oracle.update(ok=bool(ok), reason="" if ok else (reason or ORACLE_PARIS), at=time.time() if at is None else at)


def oracle_state() -> dict:
    with _lock:
        return {k: _oracle[k] for k in ("ok", "reason", "at")}


def oracle_reset() -> None:
    with _lock:
        _oracle.update(at=0.0, ok=None, reason="", running=False)


def _oracle_probe() -> None:
    from . import oci_genai
    import os
    target = os.environ.get("OCI_GEMINI_FLASH_MODEL", "").strip() or "gemini-2.5-flash"
    try:
        oci_genai.chat("Reply with OK.", [{"role": "user", "content": "ping"}], model=target, max_tokens=8, timeout=25)
        oracle_set(True)
    except oci_genai.OCIError as e:
        oracle_set(False, oracle_reason(str(e)))
    except Exception as e:  # a probe never takes the app down
        oracle_set(False, f"Unavailable: the Oracle check failed ({e.__class__.__name__})")
    finally:
        with _lock:
            _oracle["running"] = False


def oracle_check(background: bool = True) -> dict:
    """Start the Oracle probe when the last one is older than ORACLE_RECHECK (or never ran); returns the current state.
    Never more than one probe at a time and never on every run: the result is kept for 6 h."""
    from . import oci_genai
    if not oci_genai.configured():
        return oracle_state()
    with _lock:
        due = not _oracle["running"] and time.time() - _oracle["at"] >= ORACLE_RECHECK
        if due:
            _oracle["running"] = True
    if due:
        if background:
            threading.Thread(target=_oracle_probe, name="oracle-probe", daemon=True).start()
        else:
            _oracle_probe()
    return oracle_state()


def get_pref(store, uid: str) -> str:
    rows = store.q("SELECT engine FROM ai_engine_pref WHERE user_id=?", (uid,))
    e = rows[0]["engine"] if rows else ""
    return e if e in IDS else "auto"


def set_pref(store, uid: str, engine: str) -> str:
    e = (engine or "").strip().lower()
    e = {"anthropic": "claude", "gemini": "gemini-studio", "ai studio": "gemini-studio", "aistudio": "gemini-studio",
         "google": "gemini-studio", "oracle": "gemini-flash", "oci": "gemini-flash", "nim": "nvidia", "nemotron": "nvidia",
         "huggingface": "hf", "hugging face": "hf", "byo": "own", "your key": "own", "starter": "free"}.get(e, e)
    if e not in IDS and e != "auto":
        raise EngineError(f"Unknown AI engine {engine!r}. Pick one of: auto, {', '.join(IDS)}.")
    store.x("INSERT INTO ai_engine_pref(user_id,engine,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "engine=excluded.engine, updated=excluded.updated", (uid, e, time.time()))
    return e


def order(pref: str) -> list[str]:
    """The try-order: the chosen engine first, then the default order (Gemini Pro on Oracle only when chosen)."""
    rest = [e for e in DEFAULT_ORDER if e != pref]
    return ([pref] if pref in IDS else []) + rest


def status(store, uid: str, settings, http=None, demo: bool = False) -> dict:
    """Everything the picker shows: each engine with availability and its credit in plain words, the choice, and which
    engine will actually run next (the first available one in the try-order)."""
    from . import brain_llm, budget, free_engines, llm, oci_genai
    pref = get_pref(store, uid)
    own = None
    try:
        own = llm.resolve(store, uid, settings)
    except Exception:  # a broken saved key never breaks the picker
        own = None
    has_own = bool(own and own.get("source") == "your key")
    free_ok = bool(getattr(settings, "ai_base_url", ""))
    client = brain_llm.client_brain_active()
    oracle = oracle_check() if oci_genai.configured() else oracle_state()
    hosted = set(FREE_IDS) | {"claude", *ORACLE_IDS}  # engines on HuhQueue's own keys
    opts = []
    for eid in IDS:
        o = {"id": eid, "label": LABELS[eid], "short": SHORT[eid], "about": ABOUT[eid], "available": True, "reason": "",
             "credit": None, "selected": pref == eid}
        if eid in FREE_IDS:
            o["model"], o["fast_model"] = free_engines.models(eid)
            if not free_engines.configured(eid):
                o["available"] = False
                if eid == "gemini-studio":
                    o["reason"] = "Add a free Google AI Studio key"
                    o["link"] = {"url": free_engines.AI_STUDIO_KEY_URL, "label": "Get a free key"}
                else:
                    o["reason"] = "Not set up on this server."
            else:
                cr = budget.free_credit(store, uid, eid)
                o["credit"] = cr
                if cr["out"]:
                    o["available"], o["reason"] = False, "Today's free calls are used up."
        elif eid == "claude":
            o["model"] = "Claude Sonnet / Haiku"
            if not brain_llm.operator_key():
                o["available"], o["reason"] = False, "Not set up on this server."
            else:
                cr = budget.claude_credit(store, uid)
                o["credit"] = {**cr, "label": f"Claude: {cr['label']}", "kind": "allowance"}
                if cr["out"]:
                    o["available"], o["reason"] = False, "Out of credit."
        elif eid in ORACLE_IDS:
            o["model"] = "google.gemini-2.5-flash" if eid == "gemini-flash" else "google.gemini-2.5-pro"
            if not oci_genai.configured():
                o["available"], o["reason"] = False, "Not set up on this server."
            else:
                cr = budget.oci_credit(store, uid)
                o["credit"] = {**cr, "label": f"Gemini (Oracle): free during the contest, {cr['label']}", "kind": "oracle"}
                if oracle["ok"] is False:
                    o["available"], o["reason"] = False, oracle["reason"]
                    o["checked_at"] = oracle["at"]
                elif oracle["ok"] is None:
                    o["available"], o["reason"] = False, "Checking Oracle Cloud…"
                elif cr["out"]:
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
        if demo and eid in hosted:
            o["available"], o["reason"] = False, "Not in the free sample. Sign in with Buffer to use it."
        if client and eid in hosted:
            o["available"], o["reason"] = False, "Your AI app is the brain on this call."
        opts.append(o)
    by = {o["id"]: o for o in opts}
    seq = order(pref if pref != "auto" else "")
    runs = next((e for e in seq if by[e]["available"]), None)
    note = ""
    if pref not in ("auto", runs or "") and pref in by:
        note = (f"{by[pref]['short']} isn't available ({by[pref]['reason'].rstrip('.').lower() or 'unavailable'}), so "
                f"{by[runs]['short'] if runs else 'the built-in writer'} runs instead.")
    pools = [(by[e].get("credit") or {}).get("pool") for e in FREE_IDS]
    return {"selected": pref, "runs": runs or "built-in", "runs_label": by[runs]["label"] if runs else "Built-in writer",
            "order": seq, "note": note, "engines": opts,
            "claude_pool": (by["claude"].get("credit") or {}).get("pool"),
            "oracle_pool": (by["gemini-flash"].get("credit") or {}).get("pool"),
            "free_pools": [p for p in pools if p],
            "oracle": {"ok": oracle["ok"], "reason": oracle["reason"], "checked_at": oracle["at"] or None,
                       "recheck_hours": ORACLE_RECHECK // 3600}}
