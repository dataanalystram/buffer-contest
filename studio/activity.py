"""Activity log: every action on an account, categorised, attributable, paginated and exportable.

One table (``audit``) holds every row. Each row gets, at write time:

* a **category**: ``signin`` (human sign-ins, sign-outs, OAuth connects and FAILED sign-ins, with IP and user agent),
  ``agent`` (anything an AI agent did, plus every AI model call), ``human`` (a person's own actions) or ``system``
  (Buffer API calls, worker and housekeeping events);
* **attribution** from the request context: surface (ui / rest / mcp), agent token id and label, the OAuth client id,
  the agent app (Claude, ChatGPT, Grok, Cursor, Codex, Claude Code, the HuhQueue Sources agent or another MCP client),
  and for AI calls the provider, model, tokens and an estimated cost.

Nothing secret is ever stored: ``scrub`` drops secret-looking keys and redacts token-shaped values before the INSERT.
Rows older than ``AUDIT_RETENTION_DAYS`` (default 90) are deleted; sign-in IPs and user agents after ``SIGNIN_IP_DAYS``.
"""
from __future__ import annotations

import base64
import contextvars
import csv
import io
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

CATEGORIES = ("signin", "agent", "human", "system")
TABS = {"all": None, "signin": ("signin",), "agent": ("agent",), "system": ("system",), "human": ("human",)}
PAGE_SIZES = (25, 50, 100)
RETENTION_DAYS = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
SIGNIN_IP_DAYS = int(os.environ.get("SIGNIN_IP_DAYS", "30"))
EXPORT_MAX = 10000

SIGNIN_ACTIONS = {"connect_buffer", "device_signed_in", "device_link", "logout", "disconnect", "signin_failed",
                  "oauth_consent", "oauth_token_issued", "oauth_token_refreshed", "connect_assistant"}
SYSTEM_ACTIONS = {"buffer_api", "buffer_token_refreshed", "worker_claim", "worker_done", "retention_purge", "demo_cap"}

APPS = {  # id -> label
    "claude": "Claude", "chatgpt": "ChatGPT", "grok": "Grok", "cursor": "Cursor", "codex": "Codex",
    "claude-code": "Claude Code", "huhqueue": "HuhQueue Sources agent", "mcp": "Other MCP client", "api": "Agent token (REST)",
}

# USD per million tokens (input, output); estimates from the providers' public price pages. Unknown models get no cost.
PRICES = {
    "gpt-4.1-mini": (0.40, 1.60), "gpt-4.1": (2.00, 8.00), "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00),
    "claude-sonnet-4-5": (3.00, 15.00), "claude-haiku-4-5": (1.00, 5.00), "claude-opus-4-1": (15.00, 75.00),
    "gemini-2.5-flash": (0.30, 2.50), "gemini-2.5-pro": (1.25, 10.00), "llama-3.3-70b-versatile": (0.59, 0.79),
}

# ------------------------------------------------------------------ request context (set by the app, read by Store.audit)
_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("hq_activity_ctx", default=None)
_local = threading.local()


def begin(**fields) -> contextvars.Token:
    return _ctx.set(dict(fields))


def end(tok: contextvars.Token) -> None:
    _ctx.reset(tok)


def ctx() -> dict:
    c = _ctx.get()
    if c is None:
        c = {}
        _ctx.set(c)
    return c


def note(**fields) -> None:
    """Add attribution to the current request (agent row, MCP client info, model hint...)."""
    ctx().update({k: v for k, v in fields.items() if v is not None})


def set_usage(provider: str, model: str, tokens_in: int | None, tokens_out: int | None, source: str = "") -> None:
    """The last AI call's usage on this thread; the next ai_call row picks it up."""
    _local.usage = {"provider": provider, "model": model, "tokens_in": tokens_in, "tokens_out": tokens_out, "source": source}


def take_usage() -> dict | None:
    u = getattr(_local, "usage", None)
    _local.usage = None
    return u


def cost(model: str, source: str, tokens_in: int | None, tokens_out: int | None) -> float | None:
    if source == "free starter":
        return 0.0
    p = PRICES.get((model or "").lower())
    if not p or tokens_in is None or tokens_out is None:
        return None
    return round((tokens_in * p[0] + tokens_out * p[1]) / 1_000_000, 6)


# ------------------------------------------------------------------ classification
def classify_app(*hints: str) -> str:
    """Which agent app this is, from the OAuth client name / redirect host, MCP clientInfo, token name or user agent."""
    h = " ".join(x for x in hints if x).lower()
    if not h:
        return "mcp"
    if "claude code" in h or "claude-code" in h or "claudecode" in h:
        return "claude-code"
    if "codex" in h:
        return "codex"
    if "cursor" in h:
        return "cursor"
    if "grok" in h or "x.ai" in h or "xai" in h:
        return "grok"
    if "chatgpt" in h or "openai" in h:
        return "chatgpt"
    if "claude" in h or "anthropic" in h:
        return "claude"
    return "mcp"


def category_for(action: str, actor: str) -> str:
    if action in SIGNIN_ACTIONS:
        return "signin"
    if action in SYSTEM_ACTIONS or actor == "system":
        return "system"
    if action == "ai_call" or actor.startswith("agent:"):
        return "agent"
    return "human"


# ------------------------------------------------------------------ never log a secret
SECRET_KEYS = re.compile(r"(^|_)(token|secret|password|passwd|api_?key|authorization|auth|cookie|csrf|verifier|code|"
                         r"refresh|access|bearer|signature|private|session|sid|jwt|otp)s?$", re.I)
SAFE_KEYS = {"token_id", "token_label", "id", "tokens_in", "tokens_out", "status_code", "error_code"}
SECRET_VALUE = re.compile(
    r"(hq_[A-Za-z0-9_\-]{16,}|hqr_[A-Za-z0-9_\-]{16,}|jt_[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{12,}|gsk_[A-Za-z0-9]{12,}"
    r"|sk-ant-[A-Za-z0-9_\-]{12,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|gAAAAA[A-Za-z0-9_\-=]{20,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*"
    r"|[Bb]earer\s+[A-Za-z0-9._\-]{8,})")


def scrub(v, key: str = ""):
    if key and key not in SAFE_KEYS and SECRET_KEYS.search(key):
        return "[redacted]"
    if isinstance(v, dict):
        return {k: scrub(x, str(k)) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [scrub(x) for x in v]
    if isinstance(v, str):
        return SECRET_VALUE.sub("[redacted]", v)
    return v


def clean_text(s: str | None, n: int = 200) -> str:
    return SECRET_VALUE.sub("[redacted]", (s or ""))[:n]


# ------------------------------------------------------------------ the row written by Store.audit
def enrich(uid: str, actor: str, action: str, risk: str, detail: dict) -> dict:
    """Columns to store with a row, from the action and the current request context."""
    c = _ctx.get() or {}
    agent = c.get("agent") or {}
    cat = category_for(action, actor)
    row = {"category": cat, "surface": c.get("surface") or ("system" if cat == "system" else ""),
           "app": "", "client_id": agent.get("client_id") or c.get("client_id") or "",
           "token_id": agent.get("id"), "token_label": clean_text(agent.get("name"), 120),
           "provider": "", "model": clean_text(c.get("model_hint"), 80), "tokens_in": None, "tokens_out": None,
           "cost_usd": None, "ip": "", "ua": ""}
    if actor.startswith("agent:") or agent:
        row["app"] = agent.get("app") or classify_app(agent.get("name", ""), agent.get("client_info", ""), c.get("client_info", ""),
                                                      c.get("ua", "") if c.get("surface") == "mcp" else "")
        if not agent.get("client_id") and not agent.get("client_info") and not c.get("client_info") and row["app"] == "mcp" \
                and c.get("surface") == "rest":
            row["app"] = "api"
    if action == "ai_call":
        u = take_usage() or {}
        row.update(provider=clean_text(u.get("provider") or detail.get("provider"), 40),
                   model=clean_text(u.get("model") or detail.get("model"), 80),
                   tokens_in=u.get("tokens_in"), tokens_out=u.get("tokens_out"))
        row["cost_usd"] = cost(row["model"], u.get("source", ""), row["tokens_in"], row["tokens_out"])
        if not row["app"]:
            row["app"] = "huhqueue"  # the app's own Sources/research agent working for the signed-in person
    if cat == "signin":
        row["ip"], row["ua"] = clean_text(c.get("ip"), 64), clean_text(c.get("ua"), 200)
    return row


# ------------------------------------------------------------------ queries
def _cursor_encode(i: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"id": int(i)}).encode()).decode().rstrip("=")


def _cursor_decode(c: str | None) -> int | None:
    if not c:
        return None
    try:
        return int(json.loads(base64.urlsafe_b64decode(c + "=" * (-len(c) % 4)))["id"])
    except (ValueError, KeyError, TypeError):
        raise ValueError("Bad cursor.")


def _day(s: str | None, end_of_day: bool = False) -> float | None:
    if not s:
        return None
    try:
        if re.fullmatch(r"\d+(\.\d+)?", str(s)):
            return float(s)
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        if end_of_day and len(str(s)) <= 10:
            return d.timestamp() + 86400
        return d.timestamp()
    except ValueError:
        raise ValueError("Dates are YYYY-MM-DD (or ISO 8601).")


def where(uid: str, f: dict, *, with_tab: bool = True) -> tuple[str, list]:
    sql, args = ["user_id=?"], [uid]
    tab = f.get("tab") or "all"
    if tab not in TABS:
        raise ValueError(f"tab must be one of {', '.join(TABS)}")
    if with_tab and TABS[tab]:
        sql.append("category IN (" + ",".join("?" * len(TABS[tab])) + ")")
        args += list(TABS[tab])
    lo, hi = _day(f.get("from")), _day(f.get("to"), end_of_day=True)
    if lo is not None:
        sql.append("ts>=?")
        args.append(lo)
    if hi is not None:
        sql.append("ts<?")
        args.append(hi)
    for col in ("app", "model", "provider", "client_id", "surface"):
        if f.get(col):
            sql.append(f"{col}=?")
            args.append(str(f[col]))
    if f.get("token_id"):
        sql.append("token_id=?")
        args.append(int(f["token_id"]))
    if str(f.get("failed") or "").lower() in ("1", "true", "yes"):
        sql.append("ok=0")
    q = (f.get("q") or "").strip()[:100]
    if q:
        like = "%" + q.lower().replace("\\", "").replace("%", "").replace("_", "") + "%"
        sql.append("(LOWER(action) LIKE ? OR LOWER(actor) LIKE ? OR LOWER(COALESCE(detail,'')) LIKE ? OR LOWER(COALESCE(app,'')) LIKE ?"
                   " OR LOWER(COALESCE(model,'')) LIKE ? OR LOWER(COALESCE(token_label,'')) LIKE ?)")
        args += [like] * 6
    return " AND ".join(sql), args


def _row(r, show_ip: bool) -> dict:
    d = dict(r)
    d["detail"] = json.loads(d.get("detail") or "{}")
    d["ok"] = bool(d["ok"])
    d["app_label"] = APPS.get(d.get("app") or "", "")
    if not show_ip:
        d.pop("ip", None)
        d.pop("ua", None)
    d.pop("user_id", None)
    return d


def page(store, uid: str, f: dict, *, show_ip: bool = True) -> dict:
    try:
        size = int(f.get("limit") or 25)
    except ValueError:
        size = 25
    if size not in PAGE_SIZES:
        raise ValueError("limit must be 25, 50 or 100")
    w, args = where(uid, f)
    before = _cursor_decode(f.get("cursor"))
    if before is not None:
        w += " AND id<?"
        args.append(before)
    rows = store.q(f"SELECT * FROM audit WHERE {w} ORDER BY id DESC LIMIT ?", (*args, size + 1))
    more = len(rows) > size
    rows = rows[:size]
    items = [_row(r, show_ip) for r in rows]
    return {"items": items, "limit": size, "next_cursor": _cursor_encode(rows[-1]["id"]) if more and rows else None,
            "counts": counts(store, uid, f), "retention_days": RETENTION_DAYS}


def counts(store, uid: str, f: dict) -> dict:
    w, args = where(uid, f, with_tab=False)
    out = {c: 0 for c in CATEGORIES}
    for r in store.q(f"SELECT category, COUNT(*) AS n FROM audit WHERE {w} GROUP BY category", tuple(args)):
        if r["category"] in out:
            out[r["category"]] = r["n"]
    out["all"] = sum(out.values())
    return out


def agents(store, uid: str, f: dict) -> list[dict]:
    """AI agent activity grouped by app, with each model/provider used, tokens and estimated cost."""
    w, args = where(uid, dict(f, tab="agent"))
    groups: dict[str, dict] = {}
    for r in store.q(f"SELECT COALESCE(app,'') AS app, COALESCE(provider,'') AS provider, COALESCE(model,'') AS model, "
                     f"COUNT(*) AS n, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) AS failed, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout, "
                     f"SUM(cost_usd) AS cost, MAX(ts) AS last FROM audit WHERE {w} GROUP BY app, provider, model", tuple(args)):
        app = r["app"] or "mcp"
        g = groups.setdefault(app, {"app": app, "label": APPS.get(app, app), "actions": 0, "failed": 0, "tokens_in": 0,
                                    "tokens_out": 0, "cost_usd": None, "last": 0, "models": [], "agents": []})
        g["actions"] += r["n"]
        g["failed"] += r["failed"] or 0
        g["tokens_in"] += r["tin"] or 0
        g["tokens_out"] += r["tout"] or 0
        if r["cost"] is not None:
            g["cost_usd"] = round((g["cost_usd"] or 0) + r["cost"], 6)
        g["last"] = max(g["last"], r["last"] or 0)
        if r["model"] or r["provider"]:
            g["models"].append({"provider": r["provider"], "model": r["model"], "calls": r["n"]})
    for r in store.q(f"SELECT COALESCE(app,'') AS app, token_id, MAX(token_label) AS label, MAX(client_id) AS client_id, COUNT(*) AS n "
                     f"FROM audit WHERE {w} AND token_id IS NOT NULL GROUP BY app, token_id", tuple(args)):
        g = groups.get(r["app"] or "mcp")
        if g:
            g["agents"].append({"token_id": r["token_id"], "label": r["label"], "client_id": r["client_id"], "actions": r["n"]})
    return sorted(groups.values(), key=lambda g: -g["last"])


def buffer_usage(store, uid: str, budget: dict | None) -> dict:
    """Buffer API calls this account made (15 min / 24 h) next to the app client's live limits from Buffer's headers."""
    now = time.time()
    n15 = store.q("SELECT COUNT(*) AS n FROM audit WHERE user_id=? AND action='buffer_api' AND ts>?", (uid, now - 900))[0]["n"]
    nday = store.q("SELECT COUNT(*) AS n FROM audit WHERE user_id=? AND action='buffer_api' AND ts>?", (uid, now - 86400))[0]["n"]
    wins = {w["window"]: w for w in (budget or {}).get("windows", [])}
    w15, wday = wins.get("15 min") or {}, wins.get("24 h") or {}
    return {"you_15min": n15, "you_24h": nday,
            "limit_15min": w15.get("quota") or 100, "remaining_15min": w15.get("remaining"),
            "limit_24h": wday.get("quota") or 500, "remaining_24h": wday.get("remaining"),
            "note": "Buffer's limits are per app client and shared by every HuhQueue user; remaining is Buffer's own count."}


CSV_COLS = ("id", "time_utc", "category", "actor", "action", "ok", "risk", "surface", "app", "token_id", "token_label", "client_id",
            "provider", "model", "tokens_in", "tokens_out", "cost_usd", "ip", "user_agent", "detail")


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # no formula injection in spreadsheets


def export_csv(store, uid: str, f: dict, *, show_ip: bool = True) -> str:
    w, args = where(uid, f)
    rows = store.q(f"SELECT * FROM audit WHERE {w} ORDER BY id DESC LIMIT ?", (*args, EXPORT_MAX))
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(CSV_COLS)
    for r in rows:
        d = dict(r)
        out.writerow([_cell(x) for x in (
            d["id"], datetime.fromtimestamp(d["ts"], timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), d.get("category"), d["actor"],
            d["action"], "yes" if d["ok"] else "no", d["risk"], d.get("surface"), APPS.get(d.get("app") or "", d.get("app") or ""),
            d.get("token_id"), d.get("token_label"), d.get("client_id"), d.get("provider"), d.get("model"), d.get("tokens_in"),
            d.get("tokens_out"), d.get("cost_usd"), d.get("ip") if show_ip else "", d.get("ua") if show_ip else "", d.get("detail"))])
    return buf.getvalue()


def purge(store) -> dict:
    now = time.time()
    old = store.q("SELECT COUNT(*) AS n FROM audit WHERE ts<?", (now - RETENTION_DAYS * 86400,))[0]["n"]
    if old:
        store.x("DELETE FROM audit WHERE ts<?", (now - RETENTION_DAYS * 86400,))
    store.x("UPDATE audit SET ip='', ua='' WHERE category='signin' AND ts<? AND (ip<>'' OR ua<>'')", (now - SIGNIN_IP_DAYS * 86400,))
    return {"deleted": old}
