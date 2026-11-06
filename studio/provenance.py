"""Who made a template or a Style Ledger rule: built into HuhQueue, created over MCP by an AI app, or by the person.

Owner, 6 Nov 2026: "wherever templates appear, label each as Built-in, or Created via MCP by <client> · <model>, or Created
by you". Stored on the row when it is made (created_by_source, client_name, model):

* created_by_source: ``builtin`` (shipped with HuhQueue: built-in templates, style presets), ``mcp`` (an agent token, i.e.
  an AI app over MCP) or ``user`` (the person, in the app or through REST with their session).
* client_name: the AI app as HuhQueue knows it: the app the token was classified as (Claude, ChatGPT, Cursor...), else the
  MCP initialize clientInfo name, else the token's own name. Never a secret.
* model: only what the client itself reported (``_meta.model`` on the tools/call). MCP doesn't say which model an app used,
  so HuhQueue never guesses one: no report, no model ("model unknown").

Rows made before these columns existed are migrated: an agent maker -> ``mcp`` with the token name it recorded and no model;
everything else -> ``user``.
"""
from __future__ import annotations

import re

SOURCES = ("builtin", "mcp", "user")
APPS = {"claude": "Claude", "claude-code": "Claude Code", "chatgpt": "ChatGPT", "codex": "Codex", "cursor": "Cursor", "grok": "Grok"}
_CLEAN = re.compile(r"[\x00-\x1f<>]")


def _clean(s, n: int = 60) -> str:
    return _CLEAN.sub("", str(s or "")).strip()[:n]


def client_of(agent: dict | None) -> str:
    if not agent:
        return ""
    # the same naming as studio/mcp_runs.app_name, without importing it (mcp_runs reaches agent_tools and FastAPI, and this
    # module is reachable from the video worker through brand_studio)
    try:
        from . import activity
        app = agent.get("app") or activity.classify_app(agent.get("name", ""), agent.get("client_info") or "")
    except Exception:  # noqa: BLE001  (attribution never breaks a write)
        app = ""
    if app in APPS:
        return APPS[app]
    client = str(agent.get("client_info") or "").strip().partition(" ")[0]
    return _clean(client or agent.get("name") or "")


def model_hint() -> str:
    """The model the MCP client reported on this tools/call (``_meta.model``), or ''."""
    try:
        from . import activity
        return _clean(activity.ctx().get("model_hint"), 80)
    except Exception:  # noqa: BLE001
        return ""


def stamp(actor, builtin: bool = False) -> dict:
    """The three provenance columns for a row this actor creates now. builtin: the content itself ships with HuhQueue
    (a style preset); an AI app applying it is still recorded as the client."""
    if getattr(actor, "kind", "") == "agent":
        return {"created_by_source": "builtin" if builtin else "mcp", "client_name": client_of(getattr(actor, "agent", None)),
                "model": model_hint()}
    return {"created_by_source": "builtin" if builtin else "user", "client_name": "", "model": ""}


def label(source: str | None, client_name: str | None = "", model: str | None = "") -> str:
    source = source if source in SOURCES else "user"
    client, model = _clean(client_name), _clean(model, 80)
    if source == "builtin":
        return "Built-in" + (f" · applied via MCP by {client}" if client else "")
    if source == "mcp":
        return f"Created via MCP by {client or 'an AI app'}" + (f" · {model}" if model else " (model unknown)")
    return "Created by you"


def of_row(row: dict) -> dict:
    src = row.get("created_by_source") or "user"
    return {"source": src if src in SOURCES else "user", "client": _clean(row.get("client_name")),
            "model": _clean(row.get("model"), 80), "label": label(src, row.get("client_name"), row.get("model"))}


BUILTIN = {"source": "builtin", "client": "", "model": "", "label": "Built-in"}


def add_columns(store, table: str) -> None:
    """created_by_source / client_name / model on a table (idempotent, SQLite and Postgres)."""
    cols = ("created_by_source", "client_name", "model")
    if store.pg:
        for c in cols:
            store.x(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {c} TEXT")
        return
    have = {r["name"] for r in store.q(f"PRAGMA table_info({table})")}
    for c in cols:
        if c not in have:
            store.x(f"ALTER TABLE {table} ADD COLUMN {c} TEXT")
