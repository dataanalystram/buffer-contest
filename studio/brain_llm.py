"""The agent brain's model calls: tool use with Claude (the operator's ANTHROPIC_API_KEY or the person's own Anthropic
key) or any OpenAI-compatible key, with budgets, prompt caching, model routing and activity logging.

Model routing: model ids are never guessed. At first use the app lists GET /v1/models and takes the newest Sonnet for
planning/writing and the newest Haiku for fast steps (classification, captions, parsing). ANTHROPIC_MODEL_PLAN and
ANTHROPIC_MODEL_FAST override. The list is cached for six hours.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import threading
import time

import httpx

from . import budget, llm, netguard

ANTHROPIC = "https://api.anthropic.com/v1"
# Client brain (docs/MCP_TOOLS.md): while an MCP tool call runs without server_ai: true, HuhQueue's own key is never used.
_client_brain: contextvars.ContextVar[bool] = contextvars.ContextVar("hq_client_brain", default=False)


def client_brain(on: bool = True) -> contextvars.Token:
    return _client_brain.set(bool(on))


def client_brain_end(tok: contextvars.Token) -> None:
    _client_brain.reset(tok)


def client_brain_active() -> bool:
    return _client_brain.get()
_models: dict = {"at": 0.0, "plan": "", "fast": "", "all": []}
_mlock = threading.Lock()


def operator_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def anthropic_headers(key: str) -> dict:
    """Every Anthropic request; an organisation-scoped key also needs the workspace header."""
    h = {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    ws = os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip()
    if ws and key == operator_key():
        h["anthropic-workspace-id"] = ws
    return h


def resolve_models(key: str, http: httpx.Client | None = None, force: bool = False) -> dict:
    """{'plan': newest sonnet id, 'fast': newest haiku id} from GET /v1/models (overrides win)."""
    with _mlock:
        if not force and _models["at"] > time.time() - 6 * 3600 and _models["plan"]:
            return dict(_models)
        ids = []
        try:
            r = netguard.safe_get(ANTHROPIC + "/models?limit=100", http=http, https_only=True, timeout=12, limit=500_000,
                                  headers=anthropic_headers(key))
            if r.status_code == 200:
                ids = [(m.get("created_at") or "", m["id"]) for m in r.json().get("data") or [] if m.get("id")]
        except (netguard.FetchError, httpx.HTTPError, ValueError):
            ids = []
        ids.sort(reverse=True)

        def newest(word):
            return next((i for _, i in ids if word in i.lower()), "")
        _models.update(at=time.time() if ids else 0.0, plan=os.environ.get("ANTHROPIC_MODEL_PLAN") or newest("sonnet"),
                       fast=os.environ.get("ANTHROPIC_MODEL_FAST") or newest("haiku") or newest("sonnet"), all=[i for _, i in ids][:20])
        return dict(_models)


def _gemini(eid: str) -> dict:
    """Gemini on Oracle Cloud (oci_genai): the model names are what the person sees and what the ledger prices; the call
    goes to the routing profile OCID when OCI_GEMINI_*_MODEL sets one (a region without local Gemini routes to one that has
    it), else to the model name."""
    from . import oci_genai
    flash, pro = "google.gemini-2.5-flash", "google.gemini-2.5-pro"
    targets = {flash: os.environ.get("OCI_GEMINI_FLASH_MODEL", "").strip() or flash,
               pro: os.environ.get("OCI_GEMINI_PRO_MODEL", "").strip() or pro}
    model = pro if eid == "gemini-pro" else flash
    return {"id": eid, "source": "huhqueue gemini", "provider": "oci", "base_url": f"https://{oci_genai.host()}", "api_key": "",
            "model": model, "fast_model": flash, "style": "oci", "tools": True, "budgeted": True, "ledger": "oci",
            "oci_targets": targets}


def engines(store, uid: str, settings, http=None, demo: bool = False) -> list[dict]:
    """Brains in the person's try-order (ai_engines.order): the engine they picked in Settings > AI engine first, then
    the free engines on HuhQueue's keys (Google AI Studio, NVIDIA NIM, Hugging Face; metered in calls per day), Gemini on
    Oracle Cloud (only while its 6-hourly check passes), Claude on the operator key (budgeted), the person's own key, and
    the free starter model (planner mode). None of HuhQueue's keys is used for the free sample or while an MCP client is
    the brain. An engine that is not set up, out of credit or marked down is left out, so the next one takes over."""
    from . import ai_engines, byo_keys, free_engines, oci_genai
    avail: dict[str, dict] = {}
    server_ok = not demo and not client_brain_active()  # an MCP call without server_ai never reaches the operator's keys
    # the person's own key for an engine runs ahead of HuhQueue's key for it (no HuhQueue caps; their provider bills them)
    if not demo:
        for eid in (*free_engines.IDS, "claude"):
            try:
                mine = byo_keys.for_engine(store, uid, eid)
            except Exception:  # an unreadable saved key never stops the agent
                mine = None
            if mine:
                if eid == "claude":
                    m = resolve_models(mine["api_key"], http)
                    mine = {**mine, "model": m["plan"] or mine["model"], "fast_model": m["fast"] or mine["fast_model"]}
                avail[eid] = mine
    if server_ok:
        for eid in free_engines.IDS:
            if eid not in avail and free_engines.configured(eid) and not ai_engines.down(eid):
                try:
                    budget.free_check(store, uid, eid)
                except budget.BudgetExceeded:
                    continue
                except Exception:  # ledger table not there yet: the call itself checks again
                    pass
                avail[eid] = free_engines.engine(eid)
    k = operator_key()
    if k and server_ok and "claude" not in avail and not ai_engines.down("claude"):
        m = resolve_models(k, http)
        if m["plan"]:
            avail["claude"] = {"id": "claude", "source": "huhqueue claude", "provider": "anthropic", "base_url": ANTHROPIC, "api_key": k,
                               "model": m["plan"], "fast_model": m["fast"] or m["plan"], "style": "anthropic", "tools": True,
                               "budgeted": True}
    if server_ok and ai_engines.oracle_enabled() and oci_genai.configured() and ai_engines.oracle_check().get("ok"):
        for eid in ("gemini-flash", "gemini-pro"):
            if not ai_engines.down(eid):
                avail[eid] = _gemini(eid)
    own = llm.resolve(store, uid, settings)
    if own and own["source"] == "your key":
        avail["own"] = {**own, "id": "own", "tools": own.get("provider") in ("anthropic", "openai", "groq", "openrouter", "gemini", "custom"),
                        "fast_model": own["model"], "budgeted": False}
    elif own:
        avail["free"] = {**own, "id": "free", "tools": False, "fast_model": own["model"], "budgeted": False}
    try:
        pref = ai_engines.get_pref(store, uid)
    except Exception:  # the preference table is optional: never let it stop the agent
        pref = "auto"
    return [avail[e] for e in ai_engines.order(pref) if e in avail]


def label(engine: dict) -> str:
    from . import ai_engines
    return ai_engines.SHORT.get(engine.get("id") or "", engine.get("provider") or "The AI model")


def _est_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 3)


def call(engine: dict, store, uid: str, label: str, *, system: str, messages: list, tools: list | None = None,
         max_tokens: int = 1500, fast: bool = False, purpose: str = "agent brain", http=None, timeout: float = 90) -> dict:
    """One model turn. Returns {text, tool_calls:[{id,name,input}], assistant (history entry), usage{in,out,cw,cr,cost}, model,
    stop_reason ("max_tokens" when the answer was cut, for both API styles)}.
    Raises budget.BudgetExceeded (before the call) or llm.LLMError. A failed call is logged too (ok=0, the error in a few
    words), so Settings > AI engine > Logs shows what was tried and why the agent moved on."""
    t0 = time.monotonic()
    try:
        return _call(engine, store, uid, label, system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                     fast=fast, purpose=purpose, http=http, timeout=timeout, t0=t0)
    except llm.LLMError as e:
        model = engine.get("fast_model") if fast else engine.get("model")
        _log(engine, store, uid, label, model, {"in": 0, "out": 0, "cw": 0, "cr": 0, "cost": 0.0}, purpose,
             latency=time.monotonic() - t0, error=str(e))
        raise


def _call(engine: dict, store, uid: str, label: str, *, system: str, messages: list, tools: list | None, max_tokens: int,
          fast: bool, purpose: str, http, timeout: float, t0: float) -> dict:
    model = engine["fast_model"] if fast else engine["model"]
    if engine.get("budgeted") and client_brain_active():  # belt and braces: the client is the brain on this call
        raise budget.BudgetExceeded("This request came from an AI app over MCP: the app's own model is the brain, so "
                                    "HuhQueue's AI isn't used (pass server_ai: true to use it).")
    oci = engine.get("style") == "oci"
    if engine.get("budgeted") and oci:
        budget.oci_check(store, uid, model, _est_tokens([system, messages, tools]), max_tokens)
    elif engine.get("budgeted"):
        budget.check(store, uid, model, _est_tokens([system, messages, tools]), max_tokens)
    elif engine.get("source") == "your key":
        budget.byo_check(store, uid)
    free_eid = engine["id"] if engine.get("free_engine") else ""
    if free_eid:
        budget.free_check(store, uid, free_eid)
    if oci:
        return _call_oci(engine, store, uid, label, model, system=system, messages=messages, tools=tools,
                         max_tokens=max_tokens, purpose=purpose, http=http, timeout=timeout, t0=t0)
    base = llm.check_engine(engine)
    hosts = llm.allowed_hosts(engine)
    try:
        if engine["style"] == "anthropic":
            body = {"model": model, "max_tokens": max_tokens,
                    "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}], "messages": messages}
            if tools:
                tl = [dict(t) for t in tools]
                tl[-1] = {**tl[-1], "cache_control": {"type": "ephemeral"}}  # caches system + every tool definition
                body["tools"] = tl
            r = netguard.safe_post(base + "/messages", http=http, json=body, timeout=timeout, allowed_hosts=hosts,
                                   headers=anthropic_headers(engine["api_key"]))
            if r.status_code >= 400:
                if engine.get("id") == "claude" and (r.status_code in (401, 403) or "credit balance" in r.text):
                    from . import ai_engines
                    ai_engines.mark_down("claude", "Anthropic refused the app's key" if r.status_code != 400 else "the app's Anthropic credit is used up")
                raise llm.LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
            j = r.json()
            u = j.get("usage") or {}
            usage = {"in": int(u.get("input_tokens") or 0), "out": int(u.get("output_tokens") or 0),
                     "cw": int(u.get("cache_creation_input_tokens") or 0), "cr": int(u.get("cache_read_input_tokens") or 0)}
            text = "".join(b.get("text", "") for b in j.get("content") or [] if b.get("type") == "text")
            calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}} for b in j.get("content") or [] if b.get("type") == "tool_use"]
            assistant = {"role": "assistant", "content": j.get("content") or []}
            stop = j.get("stop_reason") or ""
        else:
            headers = {"Authorization": f"Bearer {engine['api_key']}"} if engine.get("api_key") else {}
            msgs = [{"role": "system", "content": system}] + messages
            body = {"model": model, "max_tokens": max_tokens, "messages": msgs}
            if tools:
                body["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                                   "parameters": t["input_schema"]}} for t in tools]
            from . import free_engines
            if engine.get("id") in free_engines.IDS:  # HuhQueue's free engine, or the person's own key for it
                body = free_engines.adapt_request(engine, body)
            r = netguard.safe_post(base + "/chat/completions", http=http, json=body, headers=headers, timeout=timeout, allowed_hosts=hosts,
                                   https_only=(engine.get("provider") != "free") or base.startswith("https://"))
            if r.status_code >= 400:
                if free_eid:
                    from . import ai_engines, free_engines
                    hard = free_engines.failure(free_eid, r.status_code, r.text[:600])
                    if hard:
                        ai_engines.mark_down(free_eid, hard[0], hard[1])
                raise llm.LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {_short_error(r)}")
            j = r.json()
            u = j.get("usage") or {}
            usage = {"in": int(u.get("prompt_tokens") or 0), "out": int(u.get("completion_tokens") or 0), "cw": 0,
                     "cr": int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)}
            choice = (j.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            stop = "max_tokens" if choice.get("finish_reason") == "length" else (choice.get("finish_reason") or "")
            text, calls, tcs = openai_message(msg)
            assistant = {"role": "assistant", "content": text, **({"tool_calls": tcs} if tcs else {})}
    except netguard.FetchError as e:
        raise llm.LLMError(f"The AI endpoint isn't allowed: {e}")
    except httpx.HTTPError as e:
        raise llm.LLMError(f"Couldn't reach the AI model ({e.__class__.__name__}).")
    cost = budget.price(model, usage["in"], usage["out"], usage["cw"], usage["cr"]) if engine["style"] == "anthropic" else None
    if free_eid:
        cost = 0.0  # free tier: metered in calls per day, never in dollars
        budget.free_record(store, uid, free_eid, usage["in"] + usage["out"])
    usage["cost"] = cost
    if engine.get("budgeted") and cost is not None:
        budget.record(store, uid, cost)
    elif engine.get("source") == "your key":
        budget.byo_check(store, uid, usage["in"] + usage["out"])
    _log(engine, store, uid, label, model, usage, purpose, latency=time.monotonic() - t0)
    return {"text": text, "tool_calls": calls, "assistant": assistant, "usage": usage, "model": model, "stop_reason": stop}


def _call_oci(engine, store, uid, label, model, *, system, messages, tools, max_tokens, purpose, http, timeout, t0=None) -> dict:
    """One Gemini turn on Oracle Cloud. History stays in the OpenAI shape (tool results are role=tool), so the agent loop is
    the same as for any OpenAI-compatible key. A hard failure marks the engine down for a few minutes (ai_engines)."""
    from . import ai_engines, oci_genai
    target = (engine.get("oci_targets") or {}).get(model, model)
    try:
        res = oci_genai.chat(system, messages, model=target, tools=tools, max_tokens=max_tokens, http=http, timeout=timeout)
    except oci_genai.OCIError as e:
        msg = str(e)
        if any(f"HTTP {c}" in msg for c in (400, 401, 403, 404, 429)) or "set up" in msg:
            short = ("Oracle's Gemini quota for this account is 0" if "limit" in msg and "set to 0" in msg else msg.split(":")[0])
            ai_engines.mark_down(engine.get("id") or "gemini-flash", short)
            if "publisher model" in msg.lower():  # the region can't serve Gemini: wait for the next 6-hourly check
                ai_engines.oracle_set(False, ai_engines.oracle_reason(msg))
        raise llm.LLMError(msg[:240])
    usage = {"in": res["in"], "out": res["out"], "cw": 0, "cr": 0}
    usage["cost"] = oci_genai.price(model, usage["in"], usage["out"])
    budget.oci_record(store, uid, usage["cost"])
    calls = []
    for tc in res["tool_calls"]:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        calls.append({"id": tc.get("id") or tc["function"]["name"], "name": tc["function"]["name"], "input": args if isinstance(args, dict) else {}})
    assistant = {"role": "assistant", "content": res["text"], **({"tool_calls": res["tool_calls"]} if res["tool_calls"] else {})}
    _log(engine, store, uid, label, model, usage, purpose, latency=(time.monotonic() - t0) if t0 else None)
    stop = "max_tokens" if res["finish"] == "length" else res["finish"]
    return {"text": res["text"], "tool_calls": calls, "assistant": assistant, "usage": usage, "model": model, "stop_reason": stop}


def _log(engine, store, uid, label, model, usage, purpose, latency: float | None = None, error: str = ""):
    """Activity: provider, model, tokens, cost, latency and any fallback reason on an ai_call row (never the key or the
    prompt). A failed call is the same row with ok=0 and the error in a few words."""
    from . import activity
    prov = {"huhqueue claude": "Anthropic (HuhQueue)", "huhqueue gemini": "Gemini (Oracle Cloud)",
            "huhqueue free": {"google-ai-studio": "Google AI Studio (free tier)", "nvidia": "NVIDIA NIM (free credits)",
                              "huggingface": "Hugging Face (free credits)"}.get(engine.get("provider") or "", engine.get("provider")),
            "your key": f"{engine.get('provider')} (your key)", "free starter": "free starter"}.get(engine.get("source"), engine.get("provider"))
    activity.set_usage(prov, model, usage["in"] + usage["cw"] + usage["cr"], usage["out"], engine.get("source", ""))
    store.audit(uid, label, "ai_call", "read", {"engine": "own" if engine.get("source") == "your key" else
                                                ("free" if engine.get("source") == "free starter" else
                                                 engine.get("id") if engine.get("free_engine") else "app"),
                                                "for": purpose, "provider": engine.get("provider"), "model": model,
                                                "source": "oci" if engine.get("style") == "oci" else engine.get("provider"),
                                                "cost_usd": usage.get("cost"), "cache_read": usage["cr"], "cache_write": usage["cw"],
                                                "engine_id": engine.get("id") or "", "byo": engine.get("source") == "your key",
                                                "latency_ms": int(latency * 1000) if latency is not None else None,
                                                **({"fallback": fb} if (fb := activity.ctx().pop("fallback", None)) else {}),
                                                **({"error": activity.clean_text(error.replace(engine.get("api_key") or "\x00", "[key]"), 200)}
                                                   if error else {})},
                ok=not error)


def _short_error(r) -> str:
    """A provider's error in a few words (the JSON message when there is one), never a page of HTML."""
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        msg = (e.get("message") if isinstance(e, dict) else e) or j.get("detail") or j.get("message") or ""
        return str(msg)[:200] or r.text[:200]
    except ValueError:
        return "(not JSON)" if r.text.lstrip().startswith("<") else r.text[:200]


_THINK = re.compile(r"<think>.*?</think>\s*", re.S | re.I)


def openai_message(msg: dict) -> tuple[str, list, list]:
    """An OpenAI-style assistant message from any compatible provider -> (text, calls for the loop, tool_calls for the
    history). Handles the differences seen across Gemini, NVIDIA NIM, Hugging Face and others: content as a list of
    parts or null, <think> blocks in the text, a missing or empty tool-call id (one is made up and used in BOTH places,
    so the tool result matches), arguments as a dict, an empty string or doubly encoded JSON, and extra fields a
    provider needs echoed back (Gemini's thought signatures in extra_content)."""
    c = msg.get("content")
    if isinstance(c, list):
        c = "".join((p.get("text") or "") if isinstance(p, dict) else str(p) for p in c)
    text = _THINK.sub("", c or "").strip() if isinstance(c, str) else ""
    calls, tcs = [], []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        name = fn.get("name") or tc.get("name")
        if not name:
            continue
        raw = fn.get("arguments", tc.get("arguments"))
        args = raw
        for _ in range(2):  # a JSON string, or a JSON string holding a JSON string
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except ValueError:
                    args = {}
        args = args if isinstance(args, dict) else {}
        cid = str(tc.get("id") or "").strip() or f"call_{i}_{name}"
        calls.append({"id": cid, "name": name, "input": args})
        entry = {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
        if tc.get("extra_content"):
            entry["extra_content"] = tc["extra_content"]
        tcs.append(entry)
    return text, calls, tcs


def tool_result(engine: dict, call_id: str, name: str, result) -> dict:
    content = json.dumps(result, default=str)[:6000]
    if engine["style"] == "anthropic":
        return {"type": "tool_result", "tool_use_id": call_id, "content": content}
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
