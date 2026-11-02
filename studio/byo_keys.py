"""Bring your own AI keys: one key per provider per person (Anthropic, OpenAI, Google AI Studio, NVIDIA, Hugging Face,
Groq, OpenRouter, or any OpenAI-compatible endpoint).

Storage reuses the existing sealed per-user AI secret: the ai_settings row (Fernet via Store.seal/unseal, listed in
store.SEALED, so a TOKEN_KEY rotation re-seals it). Its blob now carries {"keys": {provider: {api_key, model, base_url,
saved}}}; the older single-key shape ({provider, base_url, api_key, model} at the top level) is read as that provider's
key and converted on the next save, so nobody loses a key.

Priority: a person's key takes over the engine it belongs to (anthropic -> claude, gemini -> gemini-studio, nvidia ->
nvidia, hf -> hf), ahead of HuhQueue's key for that engine, with no HuhQueue caps (their provider bills them; the daily
token guard in budget.byo_check still protects the key from a runaway loop). The "own" engine uses the first key in
OWN_ORDER, so OpenAI, Groq, OpenRouter and custom endpoints run there.

Every key is tested with a tiny live call before it is saved. A key value is never returned, logged or put in an audit
row: views show the provider, the model and "…abcd". Only a signed-in person (not an agent token) can add or delete one.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from . import llm

PROVIDERS = ("anthropic", "openai", "gemini", "nvidia", "hf", "groq", "openrouter", "custom")
ENGINE_OF = {"anthropic": "claude", "gemini": "gemini-studio", "nvidia": "nvidia", "hf": "hf"}
OWN_ORDER = ("openai", "openrouter", "groq", "custom", "anthropic", "gemini", "nvidia", "hf")
GET_KEY = {"anthropic": "https://console.anthropic.com/settings/keys", "openai": "https://platform.openai.com/api-keys",
           "gemini": "https://aistudio.google.com/apikey", "nvidia": "https://build.nvidia.com/settings/api-keys",
           "hf": "https://huggingface.co/settings/tokens", "groq": "https://console.groq.com/keys",
           "openrouter": "https://openrouter.ai/settings/keys", "custom": ""}
LOGO = {"anthropic": "anthropic", "openai": "openai", "gemini": "gemini", "nvidia": "nvidia", "hf": "huggingface",
        "groq": "groq", "openrouter": "openrouter", "custom": "key"}
ALIASES = {"claude": "anthropic", "google": "gemini", "ai studio": "gemini", "aistudio": "gemini", "gemini-studio": "gemini",
           "google-ai-studio": "gemini", "huggingface": "hf", "hugging face": "hf", "nim": "nvidia"}


class KeyError_(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def norm(provider: str) -> str:
    p = (provider or "").strip().lower()
    return ALIASES.get(p, p)


def _blob(store, uid: str) -> dict:
    rows = store.q("SELECT data FROM ai_settings WHERE user_id=?", (uid,))
    return (store.unseal(rows[0]["data"]) if rows else None) or {}


def keys(store, uid: str) -> dict[str, dict]:
    """{provider: {api_key, model, base_url, saved}} for server-side use only (never for a response)."""
    try:
        b = _blob(store, uid)
    except Exception:  # noqa: BLE001 - an unreadable row means "no keys", never a crash
        return {}
    out = {k: dict(v) for k, v in (b.get("keys") or {}).items() if k in PROVIDERS and (v or {}).get("api_key")}
    legacy = b.get("provider") or ("custom" if b.get("api_key") else "")
    if b.get("api_key") and legacy in PROVIDERS and legacy not in out:  # the older single-key shape
        out[legacy] = {"api_key": b["api_key"], "model": b.get("model") or "", "base_url": b.get("base_url") or "",
                       "saved": b.get("saved") or 0}
    return out


def _write(store, uid: str, ks: dict[str, dict]) -> None:
    if not ks:
        store.x("DELETE FROM ai_settings WHERE user_id=?", (uid,))
        return
    store.x("INSERT INTO ai_settings(user_id,data,updated) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "data=excluded.data, updated=excluded.updated", (uid, store.seal({"keys": ks}), time.time()))


def engine(provider: str, k: dict, eid: str | None = None) -> dict:
    """The engine dict brain_llm.call / llm.chat take for one saved key."""
    pre = llm.PRESETS.get(provider, llm.PRESETS["custom"])
    base = (k.get("base_url") or pre["base_url"]).rstrip("/")
    model = k.get("model") or pre["model"]
    return {"id": eid or ENGINE_OF.get(provider, "own"), "source": "your key", "provider": provider, "base_url": base,
            "api_key": k["api_key"], "model": model, "fast_model": pre.get("fast") if not k.get("model") and pre.get("fast") else model,
            "style": pre["style"], "tools": True, "budgeted": False, "byo": True}


def for_engine(store, uid: str, eid: str) -> dict | None:
    """The person's own key for a hosted engine (claude, gemini-studio, nvidia, hf) as an engine dict, or None."""
    prov = next((p for p, e in ENGINE_OF.items() if e == eid), None)
    k = keys(store, uid).get(prov) if prov else None
    return engine(prov, k, eid) if k else None


def own(store, uid: str) -> dict | None:
    """The "own" engine: the first saved key in OWN_ORDER (llm.resolve's shape)."""
    ks = keys(store, uid)
    prov = next((p for p in OWN_ORDER if p in ks), None)
    if not prov:
        return None
    e = engine(prov, ks[prov], "own")
    return {k: e[k] for k in ("source", "provider", "base_url", "api_key", "model", "style")}


def hint(value: str) -> str:
    return "…" + value[-4:] if value and len(value) >= 8 else "…"


def view(store, uid: str) -> dict:
    """What Settings > AI engine > Your keys shows: every provider, saved or not, never a key value."""
    ks = keys(store, uid)
    rows = []
    for p in PROVIDERS:
        pre = llm.PRESETS.get(p, llm.PRESETS["custom"])
        k = ks.get(p)
        rows.append({"provider": p, "label": pre["label"], "logo": LOGO[p], "engine": ENGINE_OF.get(p, "own"),
                     "default_model": pre["model"], "get_key_url": GET_KEY[p], "needs_base_url": p == "custom",
                     "saved": bool(k), "hint": hint(k["api_key"]) if k else "", "model": (k or {}).get("model") or "",
                     "base_url": ((k or {}).get("base_url") or "") if p == "custom" else "",
                     "saved_at": (k or {}).get("saved") or None})
    return {"keys": rows, "saved": [r["provider"] for r in rows if r["saved"]],
            "priority": "Your key runs first for its engine, ahead of HuhQueue's key; your provider bills you."}


def save(store, uid: str, provider: str, api_key: str, model: str = "", base_url: str = "", http=None) -> dict:
    """Validate with a live test call, then seal. Raises KeyError_ with a plain reason (never echoing the key)."""
    from . import drafter
    p = norm(provider)
    if p not in PROVIDERS:
        raise KeyError_(f"Unknown provider {provider!r}. Pick one of: {', '.join(PROVIDERS)}.")
    pre = llm.PRESETS[p]
    if p != "custom" and (base_url or "").strip() and \
            (urlparse(base_url.strip()).hostname or "").lower() != urlparse(pre["base_url"]).hostname:
        raise KeyError_("The AI endpoint isn't allowed: that host doesn't belong to the chosen provider.")
    pre = llm.PRESETS[p]
    if p != "custom" and (base_url or "").strip() and \
            (urlparse(base_url.strip()).hostname or "").lower() != urlparse(pre["base_url"]).hostname:
        raise KeyError_("The AI endpoint isn't allowed: that host doesn't belong to the chosen provider.")
    key = (api_key or "").strip()
    if not key or len(key) < 8 or len(key) > 400 or any(c.isspace() for c in key):
        raise KeyError_("Paste the whole API key (no spaces).")
    base = (base_url or pre["base_url"]).strip().rstrip("/") if p == "custom" else pre["base_url"]
    if not base.startswith("https://"):
        raise KeyError_("A custom endpoint needs an https:// base URL.")
    if not drafter._public(urlparse(base).hostname or ""):
        raise KeyError_("That endpoint isn't a public address.")
    m = (model or "").strip()[:120]
    eng = engine(p, {"api_key": key, "model": m, "base_url": base})
    try:
        llm.check_engine(eng)
        llm.test_key(eng, http=http)
    except llm.LLMError as e:
        raise KeyError_(f"The key didn't work: {str(e).replace(key, '[key]')[:200]}")
    ks = keys(store, uid)
    ks[p] = {"api_key": key, "model": m, "base_url": base if p == "custom" else "", "saved": time.time()}
    _write(store, uid, ks)
    return view(store, uid)


def delete(store, uid: str, provider: str) -> dict:
    p = norm(provider)
    ks = keys(store, uid)
    if p not in ks:
        raise KeyError_("No saved key for that provider.", 404)
    ks.pop(p)
    _write(store, uid, ks)
    return view(store, uid)
