"""Which AI writes for this user, and one call shape for all of them.

Order: the user's own key (bring your own: OpenAI, Anthropic, Gemini, Groq, OpenRouter or any OpenAI-compatible
endpoint, encrypted at rest) -> the app's free starter model (AI_BASE_URL, e.g. Pollinations) -> no AI (the built-in
rule writer). People who use HuhQueue from Claude or ChatGPT via MCP need none of this: their own assistant thinks,
HuhQueue only supplies the tools.
"""
from __future__ import annotations

import json
import re
import time

import httpx

PRESETS = {
    "openai": {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "style": "openai"},
    "anthropic": {"label": "Anthropic (Claude API key)", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5",
                  "style": "anthropic"},
    "gemini": {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.5-flash", "style": "openai"},
    "groq": {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile", "style": "openai"},
    "openrouter": {"label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1", "model": "openrouter/auto", "style": "openai"},
    "custom": {"label": "Any OpenAI-compatible endpoint", "base_url": "", "model": "", "style": "openai"},
}
FREE_DAILY = 40  # free starter calls per user per day


class LLMError(Exception):
    pass


def resolve(store, uid: str, settings) -> dict | None:
    """The engine this user's agent uses right now, or None for the built-in writer."""
    rows = store.q("SELECT data FROM ai_settings WHERE user_id=?", (uid,))
    own = store.unseal(rows[0]["data"]) if rows else None
    if own and own.get("api_key") and own.get("base_url"):
        pre = PRESETS.get(own.get("provider") or "custom", PRESETS["custom"])
        return {"source": "your key", "provider": own.get("provider", "custom"), "base_url": own["base_url"].rstrip("/"),
                "api_key": own["api_key"], "model": own.get("model") or pre["model"], "style": pre["style"]}
    if settings.ai_base_url:
        day = time.time() - 86400
        used = store.q("SELECT COUNT(*) AS n FROM audit WHERE user_id=? AND action='ai_call' AND detail LIKE ? AND ts>?",
                       (uid, '%"free"%', day))[0]["n"]
        if used >= FREE_DAILY:
            return None
        return {"source": "free starter", "provider": "free", "base_url": settings.ai_base_url, "api_key": settings.ai_api_key,
                "model": settings.ai_model or "openai", "style": "openai"}
    return None


def public_view(store, uid: str, settings) -> dict:
    rows = store.q("SELECT data, updated FROM ai_settings WHERE user_id=?", (uid,))
    own = store.unseal(rows[0]["data"]) if rows else None
    eng = resolve(store, uid, settings)
    return {"engine": eng["source"] if eng else "built-in writer",
            "own": ({"provider": own.get("provider"), "model": own.get("model"), "base_url": own.get("base_url"),
                     "key_hint": "…" + own["api_key"][-4:] if own.get("api_key") else ""} if own else None),
            "free_starter": bool(settings.ai_base_url), "free_daily": FREE_DAILY,
            "presets": {k: {kk: v[kk] for kk in ("label", "base_url", "model")} for k, v in PRESETS.items()}}


def chat(engine: dict, system: str, user: str, *, http: httpx.Client | None = None, max_tokens: int = 1800,
         want_json: bool = True, timeout: float = 60) -> str:
    client = http or httpx.Client(timeout=timeout)
    try:
        if engine.get("style") == "anthropic":
            r = client.post(engine["base_url"] + "/messages", headers={
                "x-api-key": engine["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": engine["model"], "max_tokens": max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}]})
            if r.status_code >= 400:
                raise LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        headers = {"Authorization": f"Bearer {engine['api_key']}"} if engine.get("api_key") else {}
        body = {"model": engine["model"], "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        r = client.post(engine["base_url"] + "/chat/completions", headers=headers, json=body)
        if r.status_code >= 400:
            raise LLMError(f"{engine['provider']} answered HTTP {r.status_code}: {r.text[:200]}")
        return r.json()["choices"][0]["message"]["content"] or ""
    except httpx.HTTPError as e:
        raise LLMError(f"Couldn't reach the AI model ({e.__class__.__name__}).")


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
