"""Which AI writes for this user, and one call shape for all of them.

Order: the user's own key (bring your own: OpenAI, Anthropic, Gemini, Groq, OpenRouter or any OpenAI-compatible
endpoint, encrypted at rest) -> the app's free starter model (AI_BASE_URL, e.g. Pollinations) -> no AI (the built-in
rule writer). People who use HuhQueue from Claude or ChatGPT via MCP need none of this: their own assistant thinks,
HuhQueue only supplies the tools.
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlparse

import httpx

PRESETS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "style": "openai"},
    "anthropic": {"label": "Anthropic (Claude)", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5",
                  "fast": "claude-haiku-4-5", "style": "anthropic"},
    "gemini": {"label": "Google AI Studio (Gemini)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-flash-latest", "fast": "gemini-flash-lite-latest", "style": "openai"},
    "nvidia": {"label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1",
               "model": "nvidia/nemotron-3-ultra-550b-a55b", "fast": "nvidia/nemotron-3.5-lightning-30b-a3b", "style": "openai"},
    "hf": {"label": "Hugging Face", "base_url": "https://router.huggingface.co/v1", "model": "Qwen/Qwen3-235B-A22B-Instruct-2507",
           "style": "openai"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "style": "openai"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto", "style": "openai"},
    "custom": {"label": "Any OpenAI-compatible endpoint", "base_url": "", "model": "", "style": "openai"},
}
FREE_DAILY = 40  # free starter calls per user per day

# Each named provider talks only to its own host. A stored engine whose base_url was edited to point anywhere else is
# refused at call time, not just when the key was saved.
PROVIDER_HOSTS = {k: frozenset({urlparse(v["base_url"]).hostname}) for k, v in PRESETS.items() if v["base_url"]}


def allowed_hosts(engine: dict) -> frozenset[str] | None:
    """The hosts this engine may call, or None for "any public https host" (a custom endpoint with no operator list).
    free -> the operator's AI_BASE_URL host only; openai/anthropic/gemini/groq/openrouter -> that provider's API host;
    custom -> HQ_AI_CUSTOM_HOSTS (comma list) when the operator sets one."""
    prov = engine.get("provider") or "custom"
    if engine.get("free_engine"):  # HuhQueue's free engines (free_engines): each talks only to its own provider's host
        from . import free_engines
        return free_engines.PROVIDER_HOSTS.get(prov, frozenset())
    if prov == "free":
        base = os.environ.get("AI_BASE_URL") or engine.get("base_url") or ""
        return frozenset({(urlparse(base).hostname or "").lower()})
    if prov in PROVIDER_HOSTS:
        return PROVIDER_HOSTS[prov]
    extra = {h.strip().lower() for h in os.environ.get("HQ_AI_CUSTOM_HOSTS", "").split(",") if h.strip()}
    return frozenset(extra) if extra else None


def check_engine(engine: dict) -> str:
    """Re-check the model endpoint on EVERY call: https, the provider's own host, and a public address (the request is
    then pinned to the vetted IP by netguard). Returns the base URL; LLMError when it isn't allowed."""
    from . import netguard
    base = (engine.get("base_url") or "").rstrip("/")
    https_only = (engine.get("provider") != "free") or base.startswith("https://")
    try:
        netguard.check_url(base + "/", https_only=https_only)
    except netguard.FetchError as e:
        raise LLMError(f"The AI endpoint isn't allowed: {e}")
    hosts = allowed_hosts(engine)
    if hosts is not None and (urlparse(base).hostname or "").lower() not in hosts:
        raise LLMError("The AI endpoint isn't allowed: that host doesn't belong to the chosen provider.")
    return base


class LLMError(Exception):
    pass


def resolve(store, uid: str, settings) -> dict | None:
    """The engine this user's agent uses right now, or None for the built-in writer."""
    from . import byo_keys
    own = byo_keys.own(store, uid)  # the person's saved keys (byo_keys.OWN_ORDER), sealed in ai_settings
    if own:
        return own
    if settings.ai_base_url:
        from . import demo_guard
        if not demo_guard.allowed(store, uid, "ai"):  # free sample: daily AI allowance per workspace and per IP
            return None
        day = time.time() - 86400
        used = store.q("SELECT COUNT(*) AS n FROM audit WHERE user_id=? AND action='ai_call' AND ok=1 AND detail LIKE ? AND ts>?",
                       (uid, '%"free"%', day))[0]["n"]
        if used >= FREE_DAILY:
            return None
        return {"source": "free starter", "provider": "free", "base_url": settings.ai_base_url, "api_key": settings.ai_api_key,
                "model": settings.ai_model or "openai", "style": "openai"}
    return None


def public_view(store, uid: str, settings) -> dict:
    from . import byo_keys
    own = byo_keys.own(store, uid)
    eng = resolve(store, uid, settings)
    return {"engine": eng["source"] if eng else "built-in writer",
            "own": ({"provider": own.get("provider"), "model": own.get("model"), "base_url": own.get("base_url"),
                     "key_hint": byo_keys.hint(own.get("api_key") or "")} if own else None),
            "keys": byo_keys.view(store, uid)["keys"],
            "free_starter": bool(settings.ai_base_url), "free_daily": FREE_DAILY,
            "presets": {k: {kk: v[kk] for kk in ("label", "base_url", "model")} for k, v in PRESETS.items()}}


def chat(engine: dict, system: str, user: str, *, http: httpx.Client | None = None, max_tokens: int = 1800,
         want_json: bool = True, timeout: float = 60) -> str:
    from . import netguard, untrusted
    system, user = untrusted.scrub(untrusted.with_notice(system)), untrusted.scrub(user)  # outside text is data; no secrets
    base = check_engine(engine)  # every call, not only when the key was saved
    hosts = allowed_hosts(engine)
    https_only = (engine.get("provider") != "free") or base.startswith("https://")

    def post(url, headers, body):
        return netguard.safe_post(url, http=http, json=body, headers=headers, timeout=timeout, https_only=https_only,
                                  allowed_hosts=hosts)
    try:
        if engine.get("style") == "anthropic":
            r = post(base + "/messages", {
                "x-api-key": engine["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                {"model": engine["model"], "max_tokens": max_tokens, "system": system,
                 "messages": [{"role": "user", "content": user}]})
            if r.status_code >= 400:
                raise LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
            j = r.json()
            u = j.get("usage") or {}
            _usage(engine, j.get("model"), u.get("input_tokens"), u.get("output_tokens"))
            return "".join(b.get("text", "") for b in j.get("content", []))
        headers = {"Authorization": f"Bearer {engine['api_key']}"} if engine.get("api_key") else {}
        body = {"model": engine["model"], "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = post(base + "/chat/completions", headers, body)
        if r.status_code >= 400:
            raise LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
        j = r.json()
        u = j.get("usage") or {}
        _usage(engine, j.get("model"), u.get("prompt_tokens"), u.get("completion_tokens"))
        return j["choices"][0]["message"]["content"] or ""
    except netguard.FetchError as e:
        raise LLMError(f"The AI endpoint isn't allowed: {e}")
    except httpx.HTTPError as e:
        raise LLMError(f"Couldn't reach the AI model ({e.__class__.__name__}).")


def _usage(engine: dict, answered_model, tin, tout) -> None:
    """Hand this call's provider, model and token counts to the activity log (the next ai_call row on this thread)."""
    from . import activity
    prov = engine.get("provider") or "custom"
    label = {"free": "free starter"}.get(prov, PRESETS.get(prov, {}).get("label", prov))
    if engine.get("source") == "your key":
        label += " (your key)"
    activity.set_usage(label, str(engine.get("model") or answered_model or ""), tin if isinstance(tin, int) else None,
                       tout if isinstance(tout, int) else None, engine.get("source", ""))


def parse_json(text: str):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    m = re.search(r"[\{\[].*[\}\]]", t, re.S)
    if not m:
        raise LLMError("The AI model didn't return JSON.")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"The AI model returned broken JSON ({e.msg}).")


def chat_json(engine: dict, system: str, user: str, **kw):
    return parse_json(chat(engine, system + " Reply with JSON only.", user, **kw))


def test_key(engine: dict, http: httpx.Client | None = None) -> str:
    out = chat(engine, "You are a connectivity check.", 'Reply with {"ok":true}', http=http, max_tokens=20, timeout=25)
    return out[:80]
