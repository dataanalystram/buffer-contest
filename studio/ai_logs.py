"""AI engine logs: the person's recent model calls, read from the ai_call rows the audit log already keeps.

One row per call: time, engine, model, task, latency, tokens, cost, the fallback reason (why the agent moved to this
engine) and ok/error. Filterable by engine, status (ok | error), task and free text; paged (page, limit). The same data
backs Settings > AI engine > Logs, GET /api/ai/logs and the MCP tool get_ai_logs. Never a prompt, a reply or a key:
the audit row only ever holds scrubbed metadata.
"""
from __future__ import annotations

import json

WINDOW = 2000  # newest ai_call rows looked at per request (filters run over this window)
LIMITS = (10, 25, 50, 100)


def _engine(d: dict, provider: str) -> str:
    """The engine id of a row: the id the row recorded, else inferred from older rows' provider/engine fields."""
    if d.get("engine_id"):
        return str(d["engine_id"])
    e, src = d.get("engine") or "", (d.get("source") or d.get("provider") or provider or "").lower()
    if e in ("own", "free", "client"):
        return e
    if e in ("gemini-studio", "nvidia", "hf"):
        return e
    if src == "oci":
        return "gemini-flash"
    if "anthropic" in src:
        return "claude"
    return {"google-ai-studio": "gemini-studio", "nvidia": "nvidia", "huggingface": "hf"}.get(src, e or "app")


def label(eid: str) -> str:
    from . import ai_engines
    return {"client": "Your AI app", "app": "HuhQueue", "built-in": "Built-in writer"}.get(eid) or ai_engines.SHORT.get(eid, eid)


def _row(r: dict) -> dict:
    try:
        d = json.loads(r.get("detail") or "{}")
    except ValueError:
        d = {}
    eid = _engine(d, r.get("provider") or "")
    return {"id": r["id"], "ts": r["ts"], "engine": eid, "engine_label": label(eid) + (" (your key)" if d.get("byo") else ""),
            "provider": r.get("provider") or d.get("provider") or "", "model": r.get("model") or d.get("model") or "",
            "task": str(d.get("for") or d.get("task") or "")[:80], "latency_ms": d.get("latency_ms"),
            "tokens_in": r.get("tokens_in"), "tokens_out": r.get("tokens_out"),
            "cost_usd": r.get("cost_usd") if r.get("cost_usd") is not None else d.get("cost_usd"),
            "fallback": d.get("fallback") or "", "ok": bool(r.get("ok")), "error": d.get("error") or "",
            "byo": bool(d.get("byo")), "run": d.get("run")}


def logs(store, uid: str, engine: str = "", status: str = "", task: str = "", q: str = "", page: int = 1,
         limit: int = 25) -> dict:
    limit = limit if limit in LIMITS else 25
    page = max(1, int(page or 1))
    rows = [_row(dict(r)) for r in store.q(
        "SELECT id, ts, detail, ok, provider, model, tokens_in, tokens_out, cost_usd FROM audit "
        "WHERE user_id=? AND action='ai_call' ORDER BY id DESC LIMIT ?", (uid, WINDOW))]
    engines = sorted({(r["engine"], r["engine_label"].replace(" (your key)", "")) for r in rows})
    tasks = sorted({r["task"] for r in rows if r["task"]})
    st = (status or "").lower()
    ql = (q or "").strip().lower()[:100]
    sel = [r for r in rows
           if (not engine or r["engine"] == engine)
           and (st not in ("ok", "error") or r["ok"] == (st == "ok"))
           and (not task or r["task"] == task)
           and (not ql or ql in " ".join(str(r[k]) for k in ("engine_label", "model", "task", "error", "fallback", "provider")).lower())]
    total = len(sel)
    pages = max(1, -(-total // limit))
    page = min(page, pages)
    items = sel[(page - 1) * limit: page * limit]
    lat = [r["latency_ms"] for r in sel if isinstance(r["latency_ms"], (int, float))]
    return {"items": items, "page": page, "pages": pages, "limit": limit, "total": total,
            "filters": {"engine": engine, "status": st, "task": task, "q": ql},
            "facets": {"engines": [{"id": e, "label": n} for e, n in engines], "tasks": tasks},
            "summary": {"calls": total, "errors": sum(1 for r in sel if not r["ok"]),
                        "cost_usd": round(sum(r["cost_usd"] or 0 for r in sel), 4),
                        "avg_latency_ms": int(sum(lat) / len(lat)) if lat else None}}
