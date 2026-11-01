"""The agent brain's model calls: tool use with Claude (the operator's ANTHROPIC_API_KEY or the person's own Anthropic
key) or any OpenAI-compatible key, with budgets, prompt caching, model routing and activity logging.

Model routing: model ids are never guessed. At first use the app lists GET /v1/models and takes the newest Sonnet for
planning/writing and the newest Haiku for fast steps (classification, captions, parsing). ANTHROPIC_MODEL_PLAN and
ANTHROPIC_MODEL_FAST override. The list is cached for six hours.
"""
from __future__ import annotations

import json
import os
import threading
import time

import httpx

from . import budget, llm, netguard

ANTHROPIC = "https://api.anthropic.com/v1"
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


def engines(store, uid: str, settings, http=None, demo: bool = False) -> list[dict]:
    """Brains in order: the operator's Claude key (budgeted; never for the free sample), the person's own key, the free
    starter model (planner mode, simpler plan), then none (rules)."""
    out = []
    k = operator_key()
    if k and not demo:
        m = resolve_models(k, http)
        if m["plan"]:
            out.append({"source": "huhqueue claude", "provider": "anthropic", "base_url": ANTHROPIC, "api_key": k, "model": m["plan"],
                        "fast_model": m["fast"] or m["plan"], "style": "anthropic", "tools": True, "budgeted": True})
    own = llm.resolve(store, uid, settings)
    if own and own["source"] == "your key":
        out.append({**own, "tools": own.get("provider") in ("anthropic", "openai", "groq", "openrouter", "gemini", "custom"),
                    "fast_model": own["model"], "budgeted": False})
    elif own:
        out.append({**own, "tools": False, "fast_model": own["model"], "budgeted": False})
    return out


def _est_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 3)


def call(engine: dict, store, uid: str, label: str, *, system: str, messages: list, tools: list | None = None,
         max_tokens: int = 1500, fast: bool = False, purpose: str = "agent brain", http=None, timeout: float = 90) -> dict:
    """One model turn. Returns {text, tool_calls:[{id,name,input}], assistant (history entry), usage{in,out,cw,cr,cost}, model,
    stop_reason ("max_tokens" when the answer was cut, for both API styles)}.
    Raises budget.BudgetExceeded (before the call) or llm.LLMError."""
    model = engine["fast_model"] if fast else engine["model"]
    if engine.get("budgeted"):
        budget.check(store, uid, model, _est_tokens([system, messages, tools]), max_tokens)
    elif engine.get("source") == "your key":
        budget.byo_check(store, uid)
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
            r = netguard.safe_post(base + "/chat/completions", http=http, json=body, headers=headers, timeout=timeout, allowed_hosts=hosts,
                                   https_only=(engine.get("provider") != "free") or base.startswith("https://"))
            if r.status_code >= 400:
                raise llm.LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
            j = r.json()
            u = j.get("usage") or {}
            usage = {"in": int(u.get("prompt_tokens") or 0), "out": int(u.get("completion_tokens") or 0), "cw": 0,
                     "cr": int(((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)}
            choice = (j.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            stop = "max_tokens" if choice.get("finish_reason") == "length" else (choice.get("finish_reason") or "")
            text = msg.get("content") or ""
            calls = []
            for tc in msg.get("tool_calls") or []:
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except (ValueError, KeyError):
                    args = {}
                calls.append({"id": tc.get("id") or tc["function"]["name"], "name": tc["function"]["name"], "input": args})
            assistant = {"role": "assistant", "content": text, **({"tool_calls": msg["tool_calls"]} if msg.get("tool_calls") else {})}
    except netguard.FetchError as e:
        raise llm.LLMError(f"The AI endpoint isn't allowed: {e}")
    except httpx.HTTPError as e:
        raise llm.LLMError(f"Couldn't reach the AI model ({e.__class__.__name__}).")
    cost = budget.price(model, usage["in"], usage["out"], usage["cw"], usage["cr"]) if engine["style"] == "anthropic" else None
    usage["cost"] = cost
    if engine.get("budgeted") and cost is not None:
        budget.record(store, uid, cost)
    elif engine.get("source") == "your key":
        budget.byo_check(store, uid, usage["in"] + usage["out"])
    _log(engine, store, uid, label, model, usage, purpose)
    return {"text": text, "tool_calls": calls, "assistant": assistant, "usage": usage, "model": model, "stop_reason": stop}


def _log(engine, store, uid, label, model, usage, purpose):
    """Activity: provider, model, tokens and cost on an ai_call row (never the key or the prompt)."""
    from . import activity
    prov = {"huhqueue claude": "Anthropic (HuhQueue)", "your key": f"{engine.get('provider')} (your key)",
            "free starter": "free starter"}.get(engine.get("source"), engine.get("provider"))
    activity.set_usage(prov, model, usage["in"] + usage["cw"] + usage["cr"], usage["out"], engine.get("source", ""))
    store.audit(uid, label, "ai_call", "read", {"engine": "own" if engine.get("source") == "your key" else
                                                ("free" if engine.get("source") == "free starter" else "app"),
                                                "for": purpose, "provider": engine.get("provider"), "model": model,
                                                "cost_usd": usage.get("cost"), "cache_read": usage["cr"], "cache_write": usage["cw"]})


def tool_result(engine: dict, call_id: str, name: str, result) -> dict:
    content = json.dumps(result, default=str)[:6000]
    if engine["style"] == "anthropic":
        return {"type": "tool_result", "tool_use_id": call_id, "content": content}
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
