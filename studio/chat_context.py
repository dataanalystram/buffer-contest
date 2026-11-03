"""Make a post as a chat (owner, 3 Nov 2026): what the agent knows for a thread, thread compaction, and thread titles.

UI = REST = MCP:
  GET  /api/agent/runs/{rid}/context   MCP get_post_context   the context drawer: source, brand kit, template, channels,
                                                              rights, rules applied, and the compacted conversation
  POST /api/agent/runs/{rid}/title     MCP rename_post_thread rename a thread in the sidebar ({"title": ""} resets it)

Compaction. A long thread is folded server-side: every turn older than the last KEEP_LAST is condensed into one run
summary (a few words per turn, deterministic, no model call, so it costs nothing and is the same on every read), the
last KEEP_LAST turns stay verbatim, and the PINNED facts (source, rights decision, brand kit, template, channels,
format/theme, rules applied) are always carried whole, whatever was folded. The agent reads ``prompt_block`` on every
reply; the UI shows "Earlier messages summarized" from ``result.thread_summary``.
Nothing here decides what the agent does: it only shapes what the agent is told about the conversation so far.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from fastapi import Body, Request

KEEP_LAST = 6        # turns kept word for word
THRESHOLD = 10       # compaction starts once the thread is longer than this
TURN_CHARS = 140     # each folded turn keeps at most this many characters
SUMMARY_CHARS = 2400  # the run summary never grows past this (oldest folded lines go first)
TITLE_MAX = 80

TITLES_SCHEMA = ("CREATE TABLE IF NOT EXISTS chat_titles (user_id TEXT, run_id INTEGER, title TEXT, updated REAL)",
                 "CREATE UNIQUE INDEX IF NOT EXISTS chat_titles_user_run ON chat_titles(user_id, run_id)")


def _clip(t: str, n: int) -> str:
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def compact(thread: list[dict], keep: int = KEEP_LAST, threshold: int = THRESHOLD) -> dict:
    """Fold a thread: {"summarized": n, "summary": str, "kept": [last turns verbatim], "total": len}."""
    thread = [t for t in (thread or []) if isinstance(t, dict)]
    if len(thread) <= max(threshold, keep):
        return {"summarized": 0, "summary": "", "kept": thread, "total": len(thread)}
    old, kept = thread[:-keep], thread[-keep:]
    lines = [f"- {'Person' if t.get('role') == 'user' else 'Agent'}: {_clip(t.get('text'), TURN_CHARS)}" for t in old]
    while len("\n".join(lines)) > SUMMARY_CHARS and len(lines) > 1:
        lines.pop(0)  # the pinned facts carry the decisions, so the oldest small talk can go first
    dropped = len(old) - len(lines)
    head = f"Earlier in this thread ({len(old)} messages{', the oldest ' + str(dropped) + ' left out' if dropped else ''}):"
    return {"summarized": len(old), "summary": head + "\n" + "\n".join(lines), "kept": kept, "total": len(thread)}


def pinned(run: dict, kit: dict | None = None) -> dict:
    """The facts every turn keeps, compacted or not: they decide what the post is and what it may use."""
    res, inp = run.get("result") or {}, run.get("input") or {}
    src = res.get("source") or {}
    tpl = res.get("template") or {}
    rights = res.get("rights") or {}
    ko = res.get("kit_override") or {}
    k = kit or {}
    rules: list[str] = []
    for p in res.get("packages") or []:
        for r in (p.get("applied_rules") or []) if isinstance(p, dict) else []:
            txt = r.get("rule") if isinstance(r, dict) else str(r)
            if txt and txt not in rules:
                rules.append(txt)
    intake = inp.get("intake") or {}
    return {
        "source": {k2: src.get(k2) for k2 in ("url", "site", "title", "via") if src.get(k2)},
        "rights": rights.get("mode") or ("asked" if res.get("rights_question") else ""),
        "kit": {"name": ko.get("name") or k.get("name") or "", "handle": k.get("handle") or ""},
        "template": {k2: tpl.get(k2) for k2 in ("id", "name", "why") if tpl.get(k2)},
        "channels": [{"id": c.get("id"), "service": c.get("service"), "name": c.get("name")} for c in inp.get("channels") or []],
        "format": intake.get("format") or "auto",
        "theme": inp.get("theme") or "auto",
        "rules": rules[:20],
    }


def summarize(run: dict) -> dict | None:
    """The stored run summary (result.thread_summary) for this thread, or None while it is short."""
    c = compact((run.get("result") or {}).get("thread") or [])
    if not c["summarized"]:
        return None
    return {"summarized": c["summarized"], "kept": len(c["kept"]), "summary": c["summary"], "at": time.time()}


def prompt_block(run: dict, kit: dict | None = None) -> str:
    """What the agent reads about the conversation on a reply: pinned facts, the run summary, the last turns."""
    c = compact((run.get("result") or {}).get("thread") or [])
    turns = "\n".join(f"{'Person' if t.get('role') == 'user' else 'Agent'}: {_clip(t.get('text'), 600)}" for t in c["kept"])
    out = f"Pinned facts (always true for this post): {json.dumps(pinned(run, kit), default=str)[:3000]}\n"
    if c["summarized"]:
        out += c["summary"] + "\n"
    return out + "Latest messages:\n" + turns


def context(house, a, rid: int) -> dict:
    """The context drawer and MCP get_post_context."""
    run = house.run(a, rid)
    try:
        kit = house.kit(a)
    except Exception:  # noqa: BLE001  a missing kit never breaks the drawer
        kit = {}
    c = compact((run.get("result") or {}).get("thread") or [])
    return {"run_id": rid, "facts": pinned(run, kit),
            "compaction": {"summarized": c["summarized"], "kept": len(c["kept"]), "total": c["total"], "summary": c["summary"],
                           "keep_last": KEEP_LAST, "threshold": THRESHOLD}}


# ---------------------------------------------------------------- thread titles
def _ensure(store) -> None:
    for s in TITLES_SCHEMA:
        store.x(s.replace(" REAL", " DOUBLE PRECISION") if store.pg else s)
    if store.pg:
        store.x("ALTER TABLE chat_titles ENABLE ROW LEVEL SECURITY")


def titles(store, uid: str) -> dict[int, str]:
    try:
        return {int(r["run_id"]): r["title"] for r in store.q("SELECT run_id,title FROM chat_titles WHERE user_id=?", (uid,))
                if r["run_id"] is not None and r["title"]}
    except Exception:  # noqa: BLE001  (the table is made on the first rename)
        return {}


def rename(house, a, rid: int, title: Any) -> dict:
    from .actions import ActionError
    house.run(a, rid)  # 404 for someone else's run
    t = re.sub(r"[\x00-\x1f<>]", "", _clip(str(title or ""), TITLE_MAX)).strip()
    if not t and str(title or "").strip():
        raise ActionError("Give the thread a name.")
    _ensure(house.store)
    house.store.x("DELETE FROM chat_titles WHERE user_id=? AND run_id=?", (a.uid, rid))
    if t:
        house.store.x("INSERT INTO chat_titles(user_id,run_id,title,updated) VALUES(?,?,?,?)", (a.uid, rid, t, time.time()))
    house.store.audit(a.uid, a.label, "rename_post_thread", "write", {"run_id": rid})
    return {"run_id": rid, "title": t, "custom": bool(t)}


# ---------------------------------------------------------------- REST + MCP
TOOLS = [
    {"name": "get_post_context", "description": "What the agent knows for one post thread: its source, brand kit, template, channels, "
     "the rights decision, the rules applied, and how the conversation is compacted (older turns summarized, the last turns kept).",
     "inputSchema": {"type": "object", "required": ["run_id"], "properties": {"run_id": {"type": "integer"}}}},
    {"name": "rename_post_thread", "description": "Rename a post thread in the Make a post sidebar. An empty title resets it to the story headline.",
     "inputSchema": {"type": "object", "required": ["run_id", "title"], "properties": {"run_id": {"type": "integer"}, "title": {"type": "string"}}}},
]
READ_ONLY = {"get_post_context"}


def call_tool(studio, actor, name: str, args: dict):
    if name == "get_post_context":
        return context(studio.house, actor, int(args.get("run_id") or 0))
    if name == "rename_post_thread":
        return rename(studio.house, actor, int(args.get("run_id") or 0), args.get("title", ""))
    return NotImplemented


def add_routes(app, house, actor) -> None:
    @app.get("/api/agent/runs/{rid}/context")
    def run_context(rid: int, request: Request):
        """What the agent knows for this thread, and the compacted conversation."""
        return context(house, actor(request), rid)

    @app.post("/api/agent/runs/{rid}/title")
    def run_title(rid: int, request: Request, body: dict = Body(...)):
        """Rename a thread ({"title": "..."}; "" resets it)."""
        return rename(house, actor(request, write=True), rid, (body or {}).get("title", ""))
