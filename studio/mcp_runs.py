"""What an external AI app (Claude, ChatGPT, Cursor...) did over MCP, shown on the Make a post thread it made.

Owner, 3 Nov 2026: a Reel made from Claude through HuhQueue's MCP server showed "Model: nvidia/nemotron-3-ultra-550b-a55b"
(the server's engine, which never ran) and "Worked through 0 steps". Now:

* origin(actor): the MCP client from the token's initialize clientInfo (e.g. "claude-ai 0.1.0") and the agent token's
  name, as a brain {"mode": "mcp", "label": "Claude · via MCP", "hint": "The model is chosen in Claude ..."}. HuhQueue never
  sees which model the app used (MCP doesn't say), so it never claims one: a `_meta.model` hint is shown only when sent.
* record(): every MCP tools/call an agent token makes (tool, a short human summary, status, duration) is one row in
  mcp_calls. Calls made before the run exists (research_link, render_reel...) wait unattached and are claimed by the
  run the same token creates next (create_package, make_post brain=external, draft_from_library...) within CLAIM_WINDOW.
* merge(run): the run as read (UI, REST, get_run) gets those rows as visible steps (via "mcp") and, when no HuhQueue
  model wrote it, the MCP brain. Nothing is written into agent_runs, so a running server-AI run never races these
  writes; House._save drops them again before it stores.

Privacy: a summary is built from a few known argument names only (url, query, title, theme, template, due_at...), capped;
captions, keys, tokens, chunks and base64 are never stored. The rows are per person (delete_user clears them).
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

SCHEMA = ("CREATE TABLE IF NOT EXISTS mcp_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, agent_id INTEGER, run_id INTEGER, "
          "tool TEXT, label TEXT, detail TEXT, status TEXT, ms INTEGER, client TEXT, app TEXT, token_name TEXT, model_hint TEXT, at REAL)")
INDEX = "CREATE INDEX IF NOT EXISTS mcp_calls_run ON mcp_calls(user_id, run_id)"
CLAIM_WINDOW = 3 * 3600          # calls this recent (no run yet) belong to the next run the same token makes
MAX_STEPS = 60                   # newest steps shown per run (a long session stays readable)
KEEP_UNCLAIMED = 24 * 3600       # unattached rows older than this are swept
APPS = {"claude": "Claude", "claude-code": "Claude Code", "chatgpt": "ChatGPT", "codex": "Codex", "cursor": "Cursor", "grok": "Grok"}
# tools that make (or return) a post run: the call that creates one claims the token's earlier unattached calls
CREATORS = {"create_package", "make_post", "draft_from_library", "use_library_item", "run_agent", "edit_media", "replace_draft_media",
            "start_post"}
# polling and lookups are not "what the agent did"; everything else is shown
QUIET = {"get_run", "render_status", "list_clips", "whoami", "list_channels", "get_limits", "get_activity", "list_queue", "list_rules",
         "list_approvals", "list_pending_approvals", "list_calendar", "get_inbox", "get_brand", "get_brain", "get_ai_engine", "get_ai_logs", "list_ai_keys",
         "list_library", "list_library_bin", "list_recent_uploads", "integrations_status", "get_post_context", "get_page_context",
         "list_templates", "get_template", "list_themes", "get_post_templates", "list_music", "list_brand_kits", "get_onboarding",
         "upload_media_chunk"}
_SUMMARY_KEYS = ("url", "source_url", "query", "title", "theme", "template", "format", "channel", "service", "due_at", "mode",
                 "post_id", "name", "kit", "engine", "style", "ratio", "seconds", "start_s", "end_s", "cut_first_s", "index")
_lock = threading.Lock()
# Readiness lives on the Store object itself, never in a set of id(store): CPython reuses a freed object's id, so a new
# Store (a fresh database) could inherit an old one's "ready" and skip CREATE TABLE. record() then failed silently and a
# run lost its MCP steps and brain (the flaky test_external_make_post_run_gets_the_mcp_brain in CI, 5 Nov 2026).
_FLAG = "_mcp_calls_ready"


def ensure(store) -> None:
    if getattr(store, _FLAG, False):
        return
    with _lock:
        if getattr(store, _FLAG, False):
            return
        sql = SCHEMA
        if store.pg:
            sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY").replace(" REAL", " DOUBLE PRECISION")
        store.x(sql)
        store.x(INDEX)
        if store.pg:
            store.x("ALTER TABLE mcp_calls ENABLE ROW LEVEL SECURITY")
        setattr(store, _FLAG, True)


def _client(agent: dict) -> tuple[str, str]:
    info = str(agent.get("client_info") or "").strip()
    name, _, ver = info.partition(" ")
    return name[:60], ver[:30]


def app_name(agent: dict) -> str:
    from . import activity
    app = agent.get("app") or activity.classify_app(agent.get("name", ""), agent.get("client_info") or "")
    if app in APPS:
        return APPS[app]
    name, _ = _client(agent)
    return name or str(agent.get("name") or "")[:40] or "Your AI app"


def origin(actor, model_hint: str = "") -> dict | None:
    """The brain line for a run an MCP client made: who drove it, never a model HuhQueue didn't run."""
    if getattr(actor, "kind", "") != "agent" or not actor.agent:
        return None
    ag = actor.agent
    app = app_name(ag)
    name, ver = _client(ag)
    hint = (f"{app} reported the model {model_hint}." if model_hint else
            f"The model is chosen in {app} — see that chat for the model used.")
    return {"mode": "mcp", "via": "mcp", "source": "mcp client", "provider": app, "label": f"{app} · via MCP", "hint": hint,
            "client": name, "client_version": ver, "token": str(ag.get("name") or "")[:60], **({"model": model_hint} if model_hint else {})}


def _short(v: Any, n: int = 80) -> str:
    s = re.sub(r"\s+", " ", str(v)).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def summary(tool: str, args: dict, result: Any, ok: bool, error: str = "") -> tuple[str, str]:
    """(label, detail) in plain words: 'Render reel', 'theme cinematic · job j_12'. Never captions or secrets."""
    from .agent_tools import LABELS
    label = LABELS.get(tool) or tool.replace("_", " ").capitalize()
    bits = []
    for k in _SUMMARY_KEYS:
        v = (args or {}).get(k)
        if v in (None, "", [], {}):
            continue
        if any(s in k for s in ("key", "token", "secret")):
            continue
        bits.append(_short(v, 90) if k in ("url", "source_url") else f"{k.replace('_', ' ')} {_short(v, 40)}")
        if len(bits) >= 3:
            break
    if isinstance((args or {}).get("captions"), dict):
        bits.append(f"captions for {len(args['captions'])} channel{'s' if len(args['captions']) != 1 else ''}")
    if isinstance((args or {}).get("media_urls"), list) and args["media_urls"]:
        bits.append(f"{len(args['media_urls'])} media")
    if not ok:
        bits.append(_short(error or "failed", 140))
    elif isinstance(result, dict):
        for k in ("job_id", "take_id", "status"):
            if result.get(k) not in (None, ""):
                bits.append(f"{k.replace('_', ' ')} {_short(result[k], 30)}")
                break
    return label[:80], " · ".join(bits)[:300]


def _rid(args: dict, result: Any) -> int | None:
    for src in (args or {}, result if isinstance(result, dict) else {}):
        v = src.get("run_id")
        if v is None and src is not args and isinstance(src.get("run"), dict):
            v = src["run"].get("id")
        try:
            if v is not None and int(v) > 0:
                return int(v)
        except (TypeError, ValueError):
            continue
    if isinstance(result, dict) and result.get("kind") in ("agent", "client") and isinstance(result.get("id"), int):
        return result["id"]  # make_post returns the run itself
    return None


def record(store, actor, tool: str, args: dict, result: Any, ok: bool, ms: int, error: str = "", model_hint: str = "") -> None:
    """One row per MCP tools/call by an agent token. Never raises: a log row must not break the tool."""
    if getattr(actor, "kind", "") != "agent" or not actor.agent or tool in QUIET:
        return
    try:
        ensure(store)
        ag = actor.agent
        rid = _rid(args, result)  # 1) the run the call names (or made)
        if rid is not None and not store.q("SELECT id FROM agent_runs WHERE id=? AND user_id=?", (rid, actor.uid)):
            rid = None  # never attach to another person's run
        if rid is None and ok:  # 2) this app's open run (one request = one thread, studio/run_discipline.py)
            try:
                from . import run_discipline
                open_ = run_discipline.active_run(store, actor)
                rid = open_["id"] if open_ else None
            except Exception:  # noqa: BLE001
                rid = None
        # 3) otherwise the call waits unattached and the next run this token creates claims it (CREATORS, CLAIM_WINDOW)
        label, detail = summary(tool, args, result, ok, error)
        name, ver = _client(ag)
        now = time.time()
        if rid is not None and tool in CREATORS:  # this call made the run: the token's earlier calls were for it
            store.x("UPDATE mcp_calls SET run_id=? WHERE user_id=? AND agent_id=? AND run_id IS NULL AND at>?",
                    (rid, actor.uid, ag["id"], now - CLAIM_WINDOW))
        store.x("INSERT INTO mcp_calls(user_id,agent_id,run_id,tool,label,detail,status,ms,client,app,token_name,model_hint,at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (actor.uid, ag["id"], rid, tool[:60], label, detail, "done" if ok else "failed", int(ms), f"{name} {ver}".strip(),
                 app_name(ag), str(ag.get("name") or "")[:60], (model_hint or "")[:80], now))
        if int(now) % 50 == 0:
            store.x("DELETE FROM mcp_calls WHERE run_id IS NULL AND at<?", (now - KEEP_UNCLAIMED,))
    except Exception:  # noqa: BLE001
        pass


def steps_for(store, uid: str, rid: int) -> list[dict]:
    try:
        ensure(store)
        rows = store.q("SELECT * FROM mcp_calls WHERE user_id=? AND run_id=? ORDER BY id DESC LIMIT ?", (uid, rid, MAX_STEPS))
    except Exception:  # noqa: BLE001
        return []
    return [dict(r) for r in reversed(rows)]


def merge(store, run: dict) -> dict:
    """The run as shown: MCP calls as steps (via mcp) and, when no HuhQueue model wrote it, the MCP client as its brain."""
    rows = steps_for(store, run.get("user_id") or "", int(run.get("id") or 0))
    if not rows:
        return run
    steps = [s for s in (run.get("steps") or []) if not (isinstance(s, dict) and s.get("via") == "mcp")]
    for r in rows:
        secs = (r["ms"] or 0) / 1000
        steps.append({"key": f"m{r['id']}", "tool": r["tool"], "label": r["label"], "status": r["status"],
                      "detail": " · ".join(x for x in (r["detail"], f"{secs:.1f}s" if secs >= 0.1 else "") if x),
                      "data": None, "at": r["at"], "via": "mcp", "ms": r["ms"], "app": r["app"]})
    run["steps"] = steps
    res = run.get("result")
    if isinstance(res, dict):
        last = rows[-1]
        hint = next((r["model_hint"] for r in reversed(rows) if r["model_hint"]), "")
        app = last["app"] or "Your AI app"
        mcp = {"mode": "mcp", "via": "mcp", "source": "mcp client", "provider": app, "label": f"{app} · via MCP",
               "hint": (f"{app} reported the model {hint}." if hint else f"The model is chosen in {app} — see that chat for the model used."),
               "client": (last["client"] or "").split(" ")[0], "client_version": " ".join((last["client"] or "").split(" ")[1:]),
               "token": last["token_name"] or "", "steps": len(rows), **({"model": hint} if hint else {})}
        res["mcp"] = mcp
        br = res.get("brain")
        if not isinstance(br, dict) or not br.get("mode") or br.get("mode") == "mcp":  # "client", {} or ours: the app drove it
            res["brain"] = mcp
    return run


def strip(steps: list, result: Any) -> tuple[list, Any]:
    """Before House._save stores a run: drop what merge() added, so it is never persisted twice."""
    steps = [s for s in (steps or []) if not (isinstance(s, dict) and s.get("via") == "mcp")]
    if isinstance(result, dict) and ("mcp" in result or (isinstance(result.get("brain"), dict) and result["brain"].get("mode") == "mcp")):
        result = {k: v for k, v in result.items() if k != "mcp"}
        if isinstance(result.get("brain"), dict) and result["brain"].get("mode") == "mcp":
            result["brain"] = {}
    return steps, result

