"""Natural-language command bar: text -> one registry action + input.

Two planners:
  * an LLM through a pluggable provider: any OpenAI-compatible Chat Completions endpoint (OpenAI, Ollama's /v1,
    NVIDIA NIM, Groq, OpenRouter, LM Studio, vLLM) or Anthropic's Messages API. Keys live encrypted in the settings
    table and are managed from Settings › AI only (never through tokens, MCP or the LLM itself).
  * a deterministic rules parser, used when no LLM is configured or the LLM answer is unusable. It covers the common
    commands: schedule / draft / publish, list posts, comments, insights, ideas, pause, bulk delete or requeue, navigation.

The planner only PROPOSES an action. Running it goes through the same registry runner as every other client,
and public or destructive actions always come back as `needs_confirmation` first. Secret and human-only actions are
never in the catalog the LLM sees.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select

from .. import actions as reg
from ..models import KV, Channel, Idea, Media, Post, PostTarget

# Endpoints checked against the official docs (Oct 2026):
#   OpenAI     POST {base}/chat/completions, Authorization: Bearer   https://platform.openai.com/docs/api-reference/chat/create
#   Ollama     OpenAI-compatible at http://localhost:11434/v1          https://github.com/ollama/ollama/blob/main/docs/openai.md
#   NVIDIA NIM OpenAI-compatible at https://integrate.api.nvidia.com/v1 https://docs.api.nvidia.com/nim/reference/llm-apis
#   Gemini     OpenAI-compatible at https://generativelanguage.googleapis.com/v1beta/openai  https://ai.google.dev/gemini-api/docs/openai
#   Groq       OpenAI-compatible at https://api.groq.com/openai/v1    https://console.groq.com/docs/openai
#   OpenRouter OpenAI-compatible at https://openrouter.ai/api/v1      https://openrouter.ai/docs/quickstart
#   Anthropic  POST /v1/messages, x-api-key + anthropic-version: 2023-06-01, top-level `system`
#              https://docs.anthropic.com/en/api/messages
# Every OpenAI-compatible one also answers GET {base}/models, which the Settings model picker uses.
PROVIDERS = {
    "openai": {"label": "OpenAI / any OpenAI-compatible URL", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "key_url": "https://platform.openai.com/api-keys"},
    "nim": {"label": "NVIDIA NIM (free key)", "base_url": "https://integrate.api.nvidia.com/v1", "model": "nvidia/nemotron-3-ultra-550b-a55b",
            "key_url": "https://build.nvidia.com/settings/api-keys"},
    "gemini": {"label": "Google Gemini (free tier)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "model": "gemini-2.5-flash", "key_url": "https://aistudio.google.com/apikey"},
    "groq": {"label": "Groq (free tier)", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
             "key_url": "https://console.groq.com/keys"},
    "openrouter": {"label": "OpenRouter (many open models)", "base_url": "https://openrouter.ai/api/v1",
                   "model": "meta-llama/llama-3.3-70b-instruct:free", "key_url": "https://openrouter.ai/settings/keys"},
    "ollama": {"label": "Ollama (local, free, no key)", "base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b-instruct",
               "key_url": "https://ollama.com/download"},
    "anthropic": {"label": "Anthropic", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5",
                  "key_url": "https://console.anthropic.com/settings/keys"},
}
KEYLESS = {"ollama", "fake"}
# .env / environment fallback for headless installs (used only when nothing is saved in Settings › AI).
ENV = {"provider": "HUHQ_AI_PROVIDER", "base_url": "HUHQ_AI_BASE_URL", "model": "HUHQ_AI_MODEL", "api_key": "HUHQ_AI_API_KEY",
       "max_tokens": "HUHQ_AI_MAX_TOKENS"}
DEFAULT_MAX_TOKENS = 2048


class LLMError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ settings
def _env() -> dict:
    return {k: os.environ.get(v, "").strip() for k, v in ENV.items()}


def ai_settings(core, reveal: bool = False) -> dict:
    with core.db.session() as s:
        row = s.get(KV, "ai")
        v = dict(row.value) if row else {}
    env = _env()
    from_env = not v.get("provider") and bool(env["provider"])
    if from_env:
        v = {"provider": env["provider"], "base_url": env["base_url"], "model": env["model"], "max_tokens": env["max_tokens"]}
    try:
        max_tokens = max(64, min(int(v.get("max_tokens") or DEFAULT_MAX_TOKENS), 32768))
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    has_key = bool(env["api_key"]) if from_env else bool(v.get("key_enc"))
    out = {"provider": v.get("provider", ""), "base_url": v.get("base_url", ""), "model": v.get("model", ""),
           "max_tokens": max_tokens, "has_key": has_key, "from_env": from_env, "providers": PROVIDERS}
    if reveal:
        if from_env:
            out["api_key"] = env["api_key"]
        else:
            out["api_key"] = core.cipher.decrypt(v["key_enc"]) if v.get("key_enc") else ""
    return out


def set_ai_settings(core, payload: dict) -> dict:
    with core.db.session() as s:
        row = s.get(KV, "ai") or KV(key="ai", value={})
        v = dict(row.value or {})
        if "provider" in payload:
            p = payload["provider"] or ""
            if p and p not in PROVIDERS and p != "fake":
                raise ValueError(f"Unknown provider {p}")
            if p != v.get("provider") and "base_url" not in payload:
                v["base_url"] = ""      # a new provider starts from its own preset URL
            v["provider"] = p
        for k in ("base_url", "model"):
            if k in payload:
                val = str(payload[k] or "").strip()[:300]
                if k == "base_url" and val and not val.startswith(("https://", "http://localhost", "http://127.0.0.1", "http://ollama",
                                                                    "http://host.docker.internal")):
                    raise ValueError("Base URL must be https:// (or a local http://localhost URL for Ollama / LM Studio)")
                v[k] = val
        if "max_tokens" in payload:
            try:
                v["max_tokens"] = max(64, min(int(payload["max_tokens"] or DEFAULT_MAX_TOKENS), 32768))
            except (TypeError, ValueError) as e:
                raise ValueError("max_tokens must be a number") from e
        if payload.get("api_key"):
            v["key_enc"] = core.cipher.encrypt(str(payload["api_key"]).strip())
        if payload.get("clear_key"):
            v.pop("key_enc", None)
        row.value = v
        s.merge(row)
    return ai_settings(core)


def configured(core) -> bool:
    st = ai_settings(core)
    return bool(st["provider"]) and (st["has_key"] or st["provider"] in KEYLESS)


def status(core) -> dict:
    """What agents may see: which provider and model, never the key."""
    st = ai_settings(core)
    prov = st["provider"]
    return {"configured": configured(core), "provider": prov, "label": PROVIDERS.get(prov, {}).get("label", prov),
            "model": st["model"] or PROVIDERS.get(prov, {}).get("model", ""), "from_env": st["from_env"],
            "features": ["command bar", "reply suggestions", "caption writer", "post ideas", "alt text", "comment themes"]}


# ------------------------------------------------------------------------------------------------ providers
class FakeLLM:
    """Scripted provider for tests: pops the next canned reply."""
    replies: list[str] = []
    calls: list[dict] = []


THINK = re.compile(r"<think>.*?</think>\s*", re.S)


def _endpoint(st: dict) -> tuple[str, str, str]:
    prov = st["provider"]
    base = (st["base_url"] or PROVIDERS.get(prov, {}).get("base_url", "")).rstrip("/")
    model = st["model"] or PROVIDERS.get(prov, {}).get("model", "")
    return prov, base, model


def chat(core, system: str, user: str, transport=None, image: tuple[str, bytes] | None = None, images: list[str] | None = None,
         temperature: float = 0, max_tokens: int | None = None) -> str:
    """One system + user turn. `image` = (mime, bytes) or `images` = data:/https URLs for vision models (alt text).
    Reasoning output (<think>…</think>) from thinking models such as Nemotron, DeepSeek-R1 or Qwen3 is stripped."""
    images = list(images or []) + ([f"data:{image[0]};base64,{base64.b64encode(image[1]).decode()}"] if image else [])
    st = ai_settings(core, reveal=True)
    prov, base, model = _endpoint(st)
    if not prov:
        raise LLMError("No AI provider set up. Add one in Settings › AI (or HUHQ_AI_* in .env).")
    if prov == "fake":
        FakeLLM.calls.append({"system": system, "user": user, "image": bool(images), "images": images})
        return THINK.sub("", FakeLLM.replies.pop(0) if FakeLLM.replies else "{}")
    key = st.get("api_key", "")
    mt = max_tokens or st["max_tokens"]
    try:
        with httpx.Client(timeout=120, transport=transport) as c:
            if prov == "anthropic":
                content = [{"type": "image", "source": _anthropic_image(u)} for u in images or []] + [{"type": "text", "text": user}]
                r = c.post(f"{base}/messages", headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                           json={"model": model, "max_tokens": mt, "temperature": temperature, "system": system,
                                 "messages": [{"role": "user", "content": content if images else user}]})
                r.raise_for_status()
                return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
            headers = {"Authorization": f"Bearer {key}"} if key else {}
            content = ([{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": u}} for u in images]) if images else user
            r = c.post(f"{base}/chat/completions", headers=headers,
                       json={"model": model, "temperature": temperature, "max_tokens": mt,
                             "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}]})
            if r.status_code >= 400:
                raise LLMError(f"{prov}: HTTP {r.status_code}: {_error_text(r)}")
            return THINK.sub("", r.json()["choices"][0]["message"].get("content") or "").strip()
    except LLMError:
        raise
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMError(f"{prov}: {e.__class__.__name__}: {str(e)[:200]}") from e


def _error_text(r: httpx.Response) -> str:
    try:
        j = r.json()
        e = j.get("error") if isinstance(j, dict) else None
        msg = (e.get("message") if isinstance(e, dict) else e) or j.get("detail") or j.get("message") or r.text
    except ValueError:
        msg = r.text
    return str(msg)[:240]


def _anthropic_image(url: str) -> dict:
    m = re.match(r"data:([\w/+.-]+);base64,(.*)", url, re.S)
    return {"type": "base64", "media_type": m.group(1), "data": m.group(2)} if m else {"type": "url", "url": url}


def test_connection(core, transport=None) -> dict:
    """Send one tiny prompt and report the answer, the model and how long it took."""
    import time
    st = ai_settings(core)
    _, base, model = _endpoint(st)
    t0 = time.monotonic()
    try:
        reply = chat(core, "You are a connectivity check. Answer with the single word OK.", "Say OK.", transport=transport, max_tokens=st["max_tokens"])
    except LLMError as e:
        return {"ok": False, "error": str(e), "model": model, "base_url": base, "ms": int((time.monotonic() - t0) * 1000)}
    return {"ok": True, "reply": reply[:200], "model": model, "base_url": base, "ms": int((time.monotonic() - t0) * 1000)}


def list_models(core, transport=None) -> dict:
    """Model ids from the provider's GET /models (OpenAI-compatible APIs and Anthropic both have it)."""
    st = ai_settings(core, reveal=True)
    prov, base, _ = _endpoint(st)
    if not prov or prov == "fake":
        return {"models": [], "error": "" if prov else "Pick a provider first"}
    key = st.get("api_key", "")
    headers = {"x-api-key": key, "anthropic-version": "2023-06-01"} if prov == "anthropic" else ({"Authorization": f"Bearer {key}"} if key else {})
    try:
        with httpx.Client(timeout=30, transport=transport) as c:
            r = c.get(f"{base}/models", headers=headers)
            if r.status_code >= 400:
                return {"models": [], "error": f"HTTP {r.status_code}: {_error_text(r)}"}
            ids = sorted({str(m.get("id", "")).removeprefix("models/") for m in r.json().get("data", []) if m.get("id")})
    except (httpx.HTTPError, ValueError) as e:
        return {"models": [], "error": f"{e.__class__.__name__}: {str(e)[:200]}"}
    return {"models": ids[:500], "error": ""}


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        raise LLMError("no JSON in the model answer")
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise LLMError(f"bad JSON from the model: {e}") from e


# ------------------------------------------------------------------------------------------------ context
def _channels(core) -> list[dict]:
    with core.db.session() as s:
        return [{"id": c.id, "provider": c.provider, "username": c.username, "status": c.status}
                for c in s.scalars(select(Channel).order_by(Channel.created_at))]


def _tz(core) -> str:
    return core.prefs().get("timezone") or "Europe/Dublin"


def llm_plan(core, text: str, context: dict | None = None, transport=None) -> dict:
    cat = [{"id": a["id"], "summary": a["summary"], "risk": a["risk"],
            "params": {k: v.get("type") for k, v in a["input_schema"]["properties"].items()}}
           for a in reg.catalog(agent=True)]
    with core.db.session() as s:
        media = [{"id": m.id, "name": m.filename, "title": getattr(m, "title", "") or "", "kind": m.kind}
                 for m in s.scalars(select(Media).order_by(Media.created_at.desc()).limit(40))]
    now = datetime.now(ZoneInfo(_tz(core)))
    system = ("You turn one instruction for HuhQueue (a social media scheduler) into exactly ONE action call. "
              "Answer with JSON only: {\"action\": \"<id>\", \"input\": {...}, \"reply\": \"<one short sentence>\"}. "
              "Use only these actions and parameters:\n" + json.dumps(cat, separators=(",", ":")) +
              "\nChannels: " + json.dumps(_channels(core)) + "\nRecent media: " + json.dumps(media) +
              f"\nNow: {now.strftime('%A %Y-%m-%d %H:%M')} ({_tz(core)}). Times go in scheduled_at as YYYY-MM-DDTHH:MM local time "
              "with when='custom' and timezone set. Use channel ids and media ids from the lists. If nothing fits, use "
              "{\"action\": \"\", \"reply\": \"<what you need>\"}.")
    data = _json_from(chat(core, system, json.dumps({"text": text, "page": (context or {}).get("route", "")}), transport=transport))
    aid = data.get("action") or ""
    a = reg.get(aid) if aid else None
    if aid and (not a or not a.agent_visible()):
        raise LLMError(f"the model picked an unavailable action '{aid}'")
    inp = data.get("input") or {}
    if a and a.id in ("post.schedule", "post.create_draft"):
        inp["channel_ids"] = _resolve_channel_refs(core, inp.get("channel_ids") or [])
    return {"action": a.id if a else "", "input": inp, "message": data.get("reply", ""), "source": "llm"}


def _resolve_channel_refs(core, refs: list) -> list[str]:
    chans = _channels(core)
    out = []
    for r in refs:
        r = str(r).lower()
        hit = [c["id"] for c in chans if r in (c["id"].lower(), c["provider"], c["username"].lower(), f"@{c['username'].lower()}",
                                               f"{c['provider']}:{c['username'].lower()}")]
        out += hit or ([r] if r == "all" else [])
    return list(dict.fromkeys(out))


# ------------------------------------------------------------------------------------------------ rules parser
NETWORKS = {"instagram": "instagram", "insta": "instagram", "ig": "instagram", "youtube": "youtube", "yt": "youtube",
            "x": "x", "twitter": "x", "tiktok": "tiktok", "tt": "tiktok", "linkedin": "linkedin", "li": "linkedin", "demo": "fake", "fake": "fake"}
FORMAT_WORDS = {"reel": "reel", "reels": "reel", "story": "story", "carousel": "carousel", "short": "short", "video": "video",
                "clip": "video", "image": "post", "photo": "post", "picture": "post"}
FORMAT_KEY = {"instagram": "ig_type", "tiktok": "tt_format", "youtube": "yt_format", "x": "x_type", "linkedin": "li_format", "linkedin_page": "li_format", "fake": "fk_format"}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
PAGES = {"home": "nav.home", "publish": "nav.queue", "queue": "nav.queue", "calendar": "nav.calendar", "ideas": "nav.ideas",
         "community": "nav.community", "comments": "nav.community", "insights": "nav.insights", "analytics": "nav.insights",
         "library": "nav.library", "media": "nav.library", "contacts": "nav.contacts", "settings": "nav.settings",
         "reports": "nav.reports", "campaigns": "nav.campaigns", "team": "nav.team", "bio": "nav.bio", "automation": "nav.automation"}
STATUS_WORDS = {"drafts": "draft", "draft": "draft", "scheduled": "scheduled", "queue": "scheduled", "queued": "scheduled",
                "sent": "sent", "published": "sent", "failed": "failed", "approvals": "pending_approval", "pending": "pending_approval"}


def parse_time(text: str, tzname: str, now: datetime | None = None) -> str | None:
    """'friday 7pm', 'tomorrow at 9', 'in 2 hours', 'at 18:30', 'oct 25 10am', '2026-10-25 10:00' -> 'YYYY-MM-DDTHH:MM' local."""
    zone = ZoneInfo(tzname)
    now = (now or datetime.now(zone)).astimezone(zone)
    t = text.lower()
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})[ t](\d{1,2}):(\d{2})\b", t)
    if m:
        return f"{m.group(1)}T{int(m.group(2)):02d}:{m.group(3)}"
    m = re.search(r"\bin (\d+) (minute|min|hour|hr|day)s?\b", t)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        d = now + {"minute": timedelta(minutes=n), "min": timedelta(minutes=n), "hour": timedelta(hours=n), "hr": timedelta(hours=n),
                   "day": timedelta(days=n)}[unit]
        return d.strftime("%Y-%m-%dT%H:%M")
    hour = minute = None
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    else:
        m = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\b", t) or re.search(r"\b(\d{1,2}):(\d{2})\b", t)
        if m:
            hour, minute = int(m.group(1)), int(m.group(2) or 0)
    for word, (h, mi) in (("noon", (12, 0)), ("midnight", (0, 0)), ("tonight", (20, 0)), ("this evening", (19, 0)), ("morning", (9, 0))):
        if hour is None and word in t:
            hour, minute = h, mi
    day = None
    if "today" in t or "tonight" in t:
        day = now.date()
    elif "tomorrow" in t:
        day = (now + timedelta(days=1)).date()
    else:
        for i, name in enumerate(WEEKDAYS):
            if re.search(rf"\b{name}\b", t) or re.search(rf"\b{name[:3]}\b", t):
                ahead = (i - now.weekday()) % 7
                if ahead == 0 and (hour is None or (hour, minute) <= (now.hour, now.minute)):
                    ahead = 7
                day = (now + timedelta(days=ahead)).date()
                break
        m2 = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", t)
        if day is None and m2:
            mon = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"].index(m2.group(1)) + 1
            cand = now.replace(month=mon, day=int(m2.group(2))).date()
            day = cand if cand >= now.date() else cand.replace(year=cand.year + 1)
    if day is None and hour is None:
        return None
    if day is None:
        day = now.date() if (hour, minute) > (now.hour, now.minute) else (now + timedelta(days=1)).date()
    if hour is None:
        hour, minute = 9, 0
    return f"{day.isoformat()}T{hour:02d}:{minute:02d}"


def _find_channels(core, t: str) -> list[dict]:
    chans = [c for c in _channels(core) if c["status"] == "active"]
    if re.search(r"\b(everywhere|all channels|every channel|all networks)\b", t):
        return chans
    out = [c for c in chans if f"@{c['username'].lower()}" in t]
    if out:
        return out
    words = set(re.findall(r"[a-z@]+", t))
    nets = {NETWORKS[w] for w in words if w in NETWORKS and not (w == "x" and not re.search(r"\b(on|to|for)\s+x\b", t))}
    return [c for c in chans if c["provider"] in nets]


def _find_media(core, phrase: str, kind_hint: str | None) -> list[dict]:
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in ("the", "a", "an", "my", "new")]
    if not words:
        return []
    with core.db.session() as s:
        hits = []
        for m in s.scalars(select(Media).order_by(Media.created_at.desc()).limit(1000)):
            if getattr(m, "deleted_at", None):
                continue
            hay = f"{m.filename} {getattr(m, 'title', '') or ''} {' '.join(getattr(m, 'tags', None) or [])}".lower()
            if all(w in hay for w in words):
                if kind_hint in ("reel", "video", "short") and m.kind != "video":
                    continue
                hits.append({"id": m.id, "filename": m.filename, "kind": m.kind})
    return hits


def _find_idea(core, phrase: str) -> dict | None:
    words = [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in ("the", "a", "an", "my")]
    if not words:
        return None
    with core.db.session() as s:
        for i in s.scalars(select(Idea).order_by(Idea.created_at.desc())):
            if all(w in f"{i.title} {i.body}".lower() for w in words):
                return {"id": i.id, "title": i.title, "body": i.body}
    return None


def _quoted(text: str) -> str:
    m = re.search(r"[\"“]([^\"”]{2,})[\"”]", text) or re.search(r"(?<![a-z])'([^']{2,})'", text, re.I)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:caption|saying)[:\s]+(.+)$", text, re.I)
    return m.group(1).strip() if m else ""


def _target_ids(core, status: str, tag: str | None, channel_ids: list[str]) -> list[str]:
    with core.db.session() as s:
        q = select(PostTarget.id, Post.tags).join(Post, Post.id == PostTarget.post_id).where(PostTarget.status == status)
        if channel_ids:
            q = q.where(PostTarget.channel_id.in_(channel_ids))
        return [tid for tid, tags in s.execute(q) if not tag or tag in (tags or [])]


def rules_plan(core, text: str, context: dict | None = None) -> dict:
    raw = text.strip()
    t = " " + raw.lower() + " "
    tzname = _tz(core)

    def out(a, inp, m):
        return {"action": a, "input": inp, "message": m, "source": "rules"}

    if re.search(r"\b(dark|light) (mode|theme)\b|\btoggle (the )?theme\b", t):
        return out("ui.theme", {}, "Toggling the theme.")
    m = re.search(r"^(?:go to|open|show me the|take me to)\s+(?:the\s+)?(\w+)(?: page)?$", t.strip())
    if m and m.group(1) in PAGES and reg.get(PAGES[m.group(1)]):
        return out(PAGES[m.group(1)], {}, f"Opening {m.group(1)}.")
    if re.search(r"\bsync\b.*\bcomments?\b|\bpull (new )?comments\b", t):
        return out("comment.sync", {}, "Pulling new comments.")
    if re.search(r"\b(unanswered|open|new)\s+comments\b", t):
        return out("comment.list", {"status": "open"}, "Here are the unanswered comments.")
    if re.search(r"\bbest time", t) and reg.get("channel.best_times"):
        return out("channel.best_times", {}, "Best posting times from your own results.")
    m = re.search(r"\b(?:insights|analytics|stats|report|performance)\b.*?\b(?:last|past)\s+(\d+)\s+days?\b", t)
    if m or re.search(r"\b(insights|analytics|stats)\b", t):
        days = m.group(1) if m else ("mtd" if "month" in t else "30")
        return out("insights.get", {"days": days}, f"Insights for {'this month' if days == 'mtd' else 'the last ' + days + ' days'}.")
    m = re.search(r"\b(?:new|add|save)(?: an?)? idea[:\s]+(.+)$", raw, re.I)
    if m:
        return out("idea.create", {"title": m.group(1).strip()[:200]}, "Saved to Ideas.")
    m = re.search(r"\b(pause|resume|unpause)\b", t)
    if m:
        chans = _find_channels(core, t)
        if len(chans) == 1:
            return out("channel.update", {"id": chans[0]["id"], "paused": m.group(1) == "pause"},
                       f"{m.group(1).capitalize()} @{chans[0]['username']}.")
    m = re.search(r"\b(delete|remove|clear)\b.*?\b(failed|drafts?|scheduled)\b", t)
    m2 = re.search(r"\b(move|send|put)\b.*?\bdrafts?\b.*?\b(?:to|into)\s+(?:the\s+)?queue\b", t)
    if m or m2:
        status = "draft" if m2 or m.group(2).startswith("draft") else m.group(2)
        tag = re.search(r"\btagged\s+#?([\w-]+)", t)
        ids = _target_ids(core, status, tag.group(1) if tag else None, [c["id"] for c in _find_channels(core, t)])
        act = "queue" if m2 else "delete"
        return out("post.bulk", {"action": act, "ids": ids},
                   f"{'Move' if act == 'queue' else 'Delete'} {len(ids)} {status} post(s){' tagged ' + tag.group(1) if tag else ''}.")
    m = re.search(r"\b(?:show|list|what are|what's in|see)\b.*?\b(drafts?|scheduled|queued?|sent|published|failed|approvals|pending)\b", t)
    if m:
        chans = _find_channels(core, t)
        inp = {"status": STATUS_WORDS[m.group(1)], **({"channel_id": ",".join(c["id"] for c in chans)} if chans else {})}
        return out("post.list", inp, f"Showing {m.group(1)} posts.")
    m = re.search(r"\b(?:publish|post|share)\s+(pt_[0-9a-f]+)\b", t)
    if m:
        return out("post.publish_now", {"id": m.group(1)}, "Publish this post now.")

    m = re.search(r"\b(schedule|post|publish|share|queue|draft)\b", t)
    if m:
        verb = m.group(1)
        chans = _find_channels(core, t)
        if not chans:
            return out("", {}, "Which channel? Say e.g. “on Instagram” or “everywhere”.")
        media, caption, idea, fmt = [], _quoted(raw), None, None
        mm = re.search(r"\bthe\s+([\w\- ]+?)\s+(reels?|video|clip|image|photo|picture|carousel|story|short)\b", t)
        if mm:
            fmt = FORMAT_WORDS.get(mm.group(2))
            media = _find_media(core, mm.group(1), fmt)
            idea = _find_idea(core, mm.group(1))
            if not media:
                return out("", {}, f"I couldn't find media matching “{mm.group(1).strip()}”. Upload it to the library first.")
        else:
            fmt = next((FORMAT_WORDS[w] for w in re.findall(r"[a-z]+", t) if w in FORMAT_WORDS), None)
        when, at = "queue", None
        if verb == "draft":
            when = "draft"
        elif re.search(r"\b(right now|now|immediately)\b", t):
            when = "now"
        elif re.search(r"\b(top of (the )?queue|prioriti[sz]e|next up)\b", t):
            when = "prioritize"
        else:
            at = parse_time(t, tzname)
            if at:
                when = "custom"
        if not caption and idea:
            caption = idea["body"] or idea["title"]
        inp = {"channel_ids": [c["id"] for c in chans], "caption": caption, "media_ids": [x["id"] for x in media[:10]]}
        if fmt and fmt != "post":
            opts = {}
            for c in chans:
                key = FORMAT_KEY.get(c["provider"])
                if key:
                    opts[key] = "reel" if (fmt == "video" and c["provider"] == "instagram") else fmt
            if opts:
                inp["options"] = opts
        if when == "draft":
            return out("post.create_draft", inp, "Save as a draft.")
        inp["when"] = when
        if at:
            inp["scheduled_at"], inp["timezone"] = at, tzname
        where = ", ".join(f"@{c['username']} ({c['provider']})" for c in chans)
        return out("post.schedule", inp, f"{'Publish now' if when == 'now' else 'Schedule'} on {where}"
                   + (f" at {at.replace('T', ' ')} {tzname}" if at else "") + ".")
    return out("", {}, "I didn't catch that. Try “schedule the launch reel for Friday 7pm on Instagram”, “show drafts” or “sync comments”.")


def plan(core, text: str, context: dict | None = None, transport=None) -> dict:
    if configured(core):
        try:
            p = llm_plan(core, text, context, transport=transport)
            if p["action"]:
                return p
            rules = rules_plan(core, text, context)
            return rules if rules["action"] else p
        except LLMError as e:
            res = rules_plan(core, text, context)
            res["llm_error"] = str(e)
            return res
    return rules_plan(core, text, context)
