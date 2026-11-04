"""Approval gate for paid or external APIs (owner decision, 4 Nov 2026).

An AI app connected over MCP must NOT spend anything on the person's (or the operator's) behalf until the person says
yes: no HuhQueue server AI (it counts against the person's HuhQueue AI allowance and costs the operator real money) and,
from Phase 4, no Channel3 credits or any other credit-spending external API.

How it works (one generic mechanism; Commerce tools plug in with ``register``):

1. A gated tool called by an agent token answers ``needs_user_approval: true`` with a cost estimate and an
   ``approval_id``. Nothing runs and nothing is spent. The same request asked again while it waits gets the same
   approval item, never a second one.
2. The item sits in the person's Approvals queue in HuhQueue (the existing ``approvals`` table, ``payload.kind =
   "paid_api"``). Only a signed-in human can approve it (``Studio.decide`` refuses every agent token, whatever its policy
   or channels).
3. Once approved, the same call (same tool, same arguments, same token) runs ONCE within ``APPROVAL_TTL``; the approval is
   then used up. A different call needs its own approval.
4. Or the person turns on "Allow paid APIs" for one token in AI apps (off by default; only a human can change it). Then
   that token's gated calls run without asking, each one written to Activity.

The kill switch and the token's daily cap still answer first. Approval rows hold the tool name, a short summary of the
request and the estimate, never keys or captions.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Callable

from .actions import ActionError, Actor

APPROVAL_TTL = 24 * 3600
MAX_PENDING = 10  # paid-API asks one token may have waiting at once (they never crowd out post approvals)
SERVER_AI_EST_USD = float(os.environ.get("HQ_SERVER_AI_EST_USD", "0.15"))  # worst case for one server-AI run
KIND = "paid_api"

# name -> {"provider", "what", "estimate": fn(args) -> dict}. Phase 4 (Channel3) registers its tools here.
PAID: dict[str, dict] = {}
SERVER_AI_LABELS = {"run_agent": "HuhQueue's research agent writes and renders a post", "make_post": "HuhQueue's agent makes a post",
                    "reply_post": "HuhQueue's agent revises a post", "revise": "HuhQueue's agent re-renders a post with a correction",
                    "edit_media": "HuhQueue's agent edits a photo or video"}
_SUMMARY_KEYS = ("query", "url", "media_url", "prompt", "brief", "correction", "format", "run_id")
IGNORED = ("approval_id", "idempotency_key", "server_ai", "user_asked", "reason", "new_post")


def register(name: str, provider: str, what: str, estimate: Callable[[dict], dict]) -> None:
    """A tool that spends credits on an external API (e.g. Channel3 search: 1 credit) goes through the gate."""
    PAID[name] = {"provider": provider, "what": what, "estimate": estimate}


def gated(name: str, args: dict, server_ai_tools: set[str]) -> bool:
    """Does this call spend money or credits? Server AI tools only when they ask for server AI (server_ai: true)."""
    if name in PAID:
        return True
    return name in server_ai_tools and isinstance(args, dict) and args.get("server_ai") is True


def args_hash(name: str, args: dict) -> str:
    clean = {k: v for k, v in (args or {}).items() if k not in IGNORED and not str(k).startswith("_")}
    return hashlib.sha256((name + "|" + json.dumps(clean, sort_keys=True, default=str, separators=(",", ":"))).encode()).hexdigest()


def summary(name: str, args: dict) -> str:
    bits = []
    for k in _SUMMARY_KEYS:
        v = (args or {}).get(k)
        if v in (None, "", [], {}):
            continue
        s = " ".join(str(v).split())
        bits.append(f"{k.replace('_', ' ')}: {s[:117] + '…' if len(s) > 120 else s}")
        if len(bits) >= 2:
            break
    return " · ".join(bits)[:300]


def estimate(studio, a: Actor, name: str, args: dict) -> dict:
    if name in PAID:
        p = PAID[name]
        try:
            e = dict(p["estimate"](args) or {})
        except Exception:  # noqa: BLE001 - an estimate never breaks the gate; unknown means ask
            e = {}
        return {"provider": p["provider"], "what": p["what"], **e}
    out = {"provider": "HuhQueue server AI", "what": SERVER_AI_LABELS.get(name, "HuhQueue's own AI model"),
           "usd_max": SERVER_AI_EST_USD, "counts_against": "the person's HuhQueue AI allowance"}
    try:
        from . import budget
        out["allowance_left_usd"] = budget.remaining(studio.store, a.uid)["left_usd"]
    except Exception:  # noqa: BLE001
        pass
    return out


def _rows(store, a: Actor) -> list[dict]:
    out = []
    for r in store.q("SELECT id, payload, status, result, created, decided FROM approvals WHERE user_id=? AND agent_id=? "
                     "AND status IN ('pending','approved') AND created>? ORDER BY id DESC LIMIT 100",
                     (a.uid, a.agent["id"], time.time() - APPROVAL_TTL)):
        try:
            p = json.loads(r["payload"] or "{}")
        except ValueError:
            continue
        if isinstance(p, dict) and p.get("kind") == KIND:
            out.append({**dict(r), "payload": p})
    return out


def token_allows(a: Actor) -> bool:
    return bool((a.agent or {}).get("allow_paid_apis"))


def gate(studio, a: Actor, name: str, args: dict) -> dict | None:
    """None = go ahead (a human, a token the person allowed, or a matching approval just used up). Otherwise the answer
    to give the app: needs_user_approval with the approval item and the estimate. Nothing has run."""
    if getattr(a, "kind", "") != "agent" or not a.agent:
        return None
    store = studio.store
    est = estimate(studio, a, name, args)
    if token_allows(a):
        store.audit(a.uid, a.label, "paid_api", "spend", {"tool": name, "by": "token setting", "provider": est.get("provider")})
        return None
    h = args_hash(name, args)
    mine = [r for r in _rows(store, a) if r["payload"].get("tool") == name and r["payload"].get("args_hash") == h]
    want = args.get("approval_id")
    if want not in (None, ""):
        try:
            want = int(want)
        except (TypeError, ValueError):
            raise ActionError("approval_id is the number needs_user_approval returned.")
        if not any(r["id"] == want for r in mine):
            raise ActionError("No approval with that id for this exact request. Call without approval_id to ask the person.", 404)
        mine = [r for r in mine if r["id"] == want]
    with store.tx():
        for r in mine:
            if r["status"] == "approved" and not r["result"]:
                used = json.dumps({"used_at": time.time(), "tool": name})
                store.x("UPDATE approvals SET result=? WHERE id=? AND user_id=? AND status='approved' AND result IS NULL", (used, r["id"], a.uid))
                again = store.q("SELECT result FROM approvals WHERE id=?", (r["id"],))
                if again and again[0]["result"] == used:
                    store.audit(a.uid, a.label, "paid_api", "spend", {"tool": name, "approval_id": r["id"], "provider": est.get("provider")})
                    return None
    from .userlocks import held
    with held(a.uid, "paid_api"):  # check-then-insert under the workspace lock: two parallel asks make ONE approval item
        rows = _rows(store, a)
        pending = next((r for r in rows if r["status"] == "pending" and r["payload"].get("tool") == name
                        and r["payload"].get("args_hash") == h), None)
        if pending:
            aid = pending["id"]
        else:
            if sum(1 for r in rows if r["status"] == "pending") >= MAX_PENDING:
                raise ActionError("This app already has too many requests waiting for the person's approval. Ask them to "
                                  "review those in HuhQueue → Inbox → Approvals first.", 429)
            payload = {"kind": KIND, "tool": name, "args_hash": h, "summary": summary(name, args), "estimate": est,
                       "app": (a.agent or {}).get("name") or ""}
            aid = store.x("INSERT INTO approvals(user_id,agent_id,agent_name,payload,status,created) VALUES(?,?,?,?,?,?)",
                          (a.uid, a.agent["id"], a.agent["name"], json.dumps(payload), "pending", time.time()))
            store.audit(a.uid, a.label, "paid_api", "held", {"tool": name, "approval_id": aid, "provider": est.get("provider")})
    return {"needs_user_approval": True, "approval_id": aid, "tool": name, "estimate": est, "spent_usd": 0,
            "message": (f"{name} spends money or credits ({est.get('provider')}), so it waits for the person. Ask them to approve "
                        "it in HuhQueue → Inbox → Approvals (or to turn on 'Allow paid APIs' for this app in AI apps). Then call "
                        f"{name} again with the same arguments (approval_id: {aid}). Nothing ran and nothing was spent. Or do it "
                        "yourself with the client-brain tools for $0."),
            "how": "list_approvals shows when it is approved."}


def decide(store, a: Actor, row: dict, approve: bool) -> dict:
    """Studio.decide for a paid-API approval item (the caller already checked it is a signed-in human and the row is theirs
    and pending). Approving lets the same call run once; nothing runs here."""
    aid = row["id"]
    tool = (json.loads(row["payload"] or "{}") or {}).get("tool", "")
    stamp = time.time()
    with store.tx():  # status='pending' in the UPDATE: a double submit can't re-approve (and un-use) an approval
        store.x("UPDATE approvals SET status=?, result=NULL, decided=? WHERE id=? AND user_id=? AND status='pending'",
                ("approved" if approve else "rejected", stamp, aid, a.uid))
        now = store.q("SELECT status, decided FROM approvals WHERE id=? AND user_id=?", (aid, a.uid))
    if not now or now[0]["status"] != ("approved" if approve else "rejected") or now[0]["decided"] != stamp:
        raise ActionError("No pending approval with that id.", 404)  # someone else decided it first: this call changed nothing
    if not approve:
        store.audit(a.uid, a.label, "reject_paid_api", "safety", {"approval_id": aid, "tool": tool})
        return {"status": "rejected", "approval_id": aid, "kind": KIND}
    store.audit(a.uid, a.label, "approve_paid_api", "safety", {"approval_id": aid, "tool": tool})
    return {"status": "approved", "approval_id": aid, "kind": KIND, "tool": tool,
            "message": f"Approved: the app can run {tool} once for this request."}


def is_paid_row(row: Any) -> bool:
    try:
        p = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(p, dict) and p.get("kind") == KIND
