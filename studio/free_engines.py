"""Free AI engines on HuhQueue's own keys: OpenAI-compatible endpoints with free developer tiers.

  gemini-studio  Google AI Studio (free tier), GOOGLE_AI_STUDIO_KEY: Gemini 2.5 Flash plans, Flash-Lite does the quick steps.
  nvidia         NVIDIA NIM (free developer credits), NVIDIA_API_KEY: Nemotron Ultra plans, Nemotron Lightning is the fast model.
  hf             Hugging Face Inference Providers (free monthly credits), HF_TOKEN: Qwen3 235B Instruct.

Keys come only from the environment (never the database, never logs, never the browser). Model ids can be overridden per
engine (GEMINI_STUDIO_MODEL / _FAST, NVIDIA_MODEL / _FAST, HF_MODEL / _FAST). Each default model was checked for tool calling
(a call, then a tool result, then the answer) before it was picked.

They cost HuhQueue nothing, but the free tiers have daily/monthly limits, so each engine has a per-person daily call cap and
an app-wide daily call pool (budget.free_*), shown in Settings > AI engine as a plain credit line.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

AI_STUDIO_KEY_URL = "https://aistudio.google.com/apikey"

ENGINES = {
    "gemini-studio": {
        "provider": "google-ai-studio", "env": "GOOGLE_AI_STUDIO_KEY", "credit": "Google AI Studio (free tier)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": ("GEMINI_STUDIO_MODEL", "gemini-2.5-flash"), "fast": ("GEMINI_STUDIO_MODEL_FAST", "gemini-2.5-flash-lite"),
        "label": "Gemini 2.5 Flash (Google AI Studio)", "short": "Gemini (AI Studio)",
        "about": "Google's fast Gemini model on the AI Studio free tier. Free; daily limits apply.",
        "user_daily": 120, "app_daily": 240},
    "nvidia": {
        "provider": "nvidia", "env": "NVIDIA_API_KEY", "credit": "NVIDIA (free developer credits)",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "model": ("NVIDIA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b"),
        "fast": ("NVIDIA_MODEL_FAST", "nvidia/nemotron-3.5-lightning-30b-a3b"),
        "label": "NVIDIA Nemotron (NVIDIA NIM)", "short": "NVIDIA",
        "about": "Nemotron Ultra plans, Nemotron Lightning does the quick steps. Free on NVIDIA's developer credits.",
        "user_daily": 150, "app_daily": 1500},
    "hf": {
        "provider": "huggingface", "env": "HF_TOKEN", "credit": "Hugging Face (free monthly credits)",
        "base_url": "https://router.huggingface.co/v1",
        "model": ("HF_MODEL", "Qwen/Qwen3-235B-A22B-Instruct-2507"), "fast": ("HF_MODEL_FAST", "Qwen/Qwen3-235B-A22B-Instruct-2507"),
        "label": "Qwen3 235B (Hugging Face)", "short": "Hugging Face",
        "about": "An open model through Hugging Face Inference Providers. Free on the monthly credits, a small allowance.",
        "user_daily": 40, "app_daily": 150},
}
IDS = tuple(ENGINES)
HOSTS = {eid: frozenset({urlparse(c["base_url"]).hostname}) for eid, c in ENGINES.items()}
PROVIDER_HOSTS = {c["provider"]: HOSTS[eid] for eid, c in ENGINES.items()}


def key(eid: str) -> str:
    c = ENGINES.get(eid)
    return (os.environ.get(c["env"]) or "").strip() if c else ""


def configured(eid: str) -> bool:
    return bool(key(eid))


def models(eid: str) -> tuple[str, str]:
    c = ENGINES[eid]
    plan = (os.environ.get(c["model"][0]) or "").strip() or c["model"][1]
    fast = (os.environ.get(c["fast"][0]) or "").strip() or c["fast"][1]
    return plan, fast


def engine(eid: str) -> dict:
    """The engine dict brain_llm.call takes (OpenAI style, tool use, metered by call count, never in dollars)."""
    c = ENGINES[eid]
    plan, fast = models(eid)
    return {"id": eid, "source": "huhqueue free", "provider": c["provider"], "base_url": c["base_url"], "api_key": key(eid),
            "model": plan, "fast_model": fast, "style": "openai", "tools": True, "budgeted": False, "free_engine": True}


def is_free(engine_: dict) -> bool:
    return bool(engine_.get("free_engine")) and engine_.get("id") in ENGINES


# ---------------------------------------------------------------- provider differences, in one place
_GEMINI_DROP = {"additionalProperties", "$schema", "examples", "default", "$id", "title"}


def _gemini_schema(s):
    """Gemini's OpenAI layer accepts an OpenAPI subset of JSON Schema: drop the keywords it rejects, recursively (a
    property that happens to be named like a keyword is kept)."""
    if isinstance(s, dict):
        out = {}
        for k, v in s.items():
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: _gemini_schema(pv) for pk, pv in v.items()}
            elif k not in _GEMINI_DROP:
                out[k] = _gemini_schema(v)
        return out
    if isinstance(s, list):
        return [_gemini_schema(v) for v in s]
    return s


def adapt_request(engine_: dict, body: dict) -> dict:
    """Per-provider tweaks on an OpenAI chat body whose tools are already in function form."""
    if engine_.get("id") == "nvidia" and "nemotron" in str(body.get("model") or "").lower():
        # Nemotron 3 / 3.5 reason before answering by default: an editorial JSON step spent ~2,700 reasoning tokens and 64 s
        # (past the 60 s step timeout) on Lightning. Thinking off: the same step takes ~4 s and tool calling still works.
        body.setdefault("chat_template_kwargs", {"enable_thinking": False})
    if engine_.get("id") == "gemini-studio":
        for t in body.get("tools") or []:
            fn = t.get("function") or {}
            fn["parameters"] = _gemini_schema(fn.get("parameters") or {"type": "object", "properties": {}})
        body.setdefault("reasoning_effort", "low")  # 2.5 Flash thinks by default; keeps the agent's turns quick
        body["messages"] = [{k: v for k, v in m.items() if not (m.get("role") == "tool" and k == "name")}
                            for m in body.get("messages") or []]
    return body


def failure(eid: str, status: int, text: str) -> tuple[str, float] | None:
    """A refusal that won't clear in seconds: (reason shown in the picker, seconds to keep the engine down), else None."""
    t = (text or "").lower()
    if status in (401, 403):
        return ("the server's key was refused", 1800)
    if status == 402 or ("credit" in t and any(w in t for w in ("exceeded", "depleted", "insufficient", "used up"))):
        return (f"{ENGINES[eid]['credit'].split(' (')[0]}'s free credits are used up", 6 * 3600)
    if status in (404, 410):
        return ("the model was retired or moved", 6 * 3600)
    if status == 429:
        daily = any(w in t for w in ("per day", "perday", "quota", "daily"))
        return ("the free tier's daily limit is reached" if daily else "rate limited by the free tier", 3600 if daily else 90)
    return None
