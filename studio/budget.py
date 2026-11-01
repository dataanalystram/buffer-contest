"""Hard budgets for the app's own model keys (the operator's ANTHROPIC_API_KEY), persisted in the database.

- Global lifetime cap AI_BUDGET_TOTAL_USD (default $60) across every account.
- Testing pool AI_BUDGET_TEST_USD (default $30) for the accounts in HQ_BRAIN_TESTERS (comma-separated Buffer account
  emails or user ids); testers draw from this pool instead of the per-user cap.
- Per signed-in user AI_BUDGET_PER_USER_USD (default $1) lifetime.
- The free sample never uses the operator key.

Every call is priced from its real token usage (input, output, cache writes, cache reads) with PRICES; before each call
the worst case (estimated input + max output) is checked against every cap that applies. Over a cap -> BudgetExceeded,
and the brain falls back to the free model with a note the UI shows. The person's own key (BYO) is theirs to spend and is
only capped per day in tokens (HQ_BRAIN_BYO_TOKENS) so a runaway loop can't drain it.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone

# USD per million tokens: input, output, cache write (5 min), cache read. Anthropic's public price page (checked 1 Nov
# 2026 for the families; a model id is priced by its family). Unknown families are priced as the most expensive known
# one, so the cap can never be undercounted.
PRICES = {
    "opus": (15.00, 75.00, 18.75, 1.50),
    "sonnet": (3.00, 15.00, 3.75, 0.30),
    "haiku": (1.00, 5.00, 1.25, 0.10),
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_budget (scope TEXT PRIMARY KEY, spent REAL, calls INTEGER, updated REAL);
CREATE TABLE IF NOT EXISTS brain_tokens (day TEXT, user_id TEXT, tokens INTEGER, PRIMARY KEY (day, user_id));
"""


class BudgetExceeded(Exception):
    pass


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION")
        store.x(stmt)
    if store.pg:
        for t in ("brain_budget", "brain_tokens"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def caps() -> dict:
    e = os.environ.get
    return {"global": float(e("AI_BUDGET_TOTAL_USD") or e("HQ_BRAIN_GLOBAL_USD") or "60"),
            "testing": float(e("AI_BUDGET_TEST_USD") or e("HQ_BRAIN_TEST_USD") or "30"),
            "user": float(e("AI_BUDGET_PER_USER_USD") or e("HQ_BRAIN_USER_USD") or "1"), "byo_tokens": int(os.environ.get("HQ_BRAIN_BYO_TOKENS", "400000"))}


def family(model: str) -> str:
    m = (model or "").lower()
    return next((f for f in PRICES if f in m), "opus")


def price(model: str, tin: int = 0, tout: int = 0, cache_write: int = 0, cache_read: int = 0) -> float:
    p = PRICES[family(model)]
    return round((tin * p[0] + tout * p[1] + cache_write * p[2] + cache_read * p[3]) / 1_000_000, 6)


def is_tester(store, uid: str) -> bool:
    ids = {x.strip().lower() for x in os.environ.get("HQ_BRAIN_TESTERS", "").split(",") if x.strip()}
    if not ids:
        return False
    if uid.lower() in ids:
        return True
    u = store.user(uid) or {}
    return (u.get("email") or "").lower() in ids


def _spent(store, scope: str) -> float:
    rows = store.q("SELECT spent FROM brain_budget WHERE scope=?", (scope,))
    return float(rows[0]["spent"]) if rows else 0.0


def scopes(store, uid: str) -> list[str]:
    return ["global", "testing" if is_tester(store, uid) else f"user:{uid}"]


def remaining(store, uid: str) -> dict:
    c = caps()
    g = c["global"] - _spent(store, "global")
    if is_tester(store, uid):
        mine, label, cap = c["testing"] - _spent(store, "testing"), "testing pool", c["testing"]
    else:
        mine, label, cap = c["user"] - _spent(store, f"user:{uid}"), "your allowance", c["user"]
    return {"global_left_usd": round(max(0.0, g), 4), "left_usd": round(max(0.0, min(g, mine)), 4), "pool": label, "pool_cap_usd": cap,
            "global_cap_usd": c["global"], "spent_usd": round(cap - max(0.0, mine), 4)}


def check(store, uid: str, model: str, est_in: int, max_out: int) -> None:
    """Refuse BEFORE the call when the worst case would cross any cap."""
    worst = price(model, est_in, max_out)
    c = caps()
    g = _spent(store, "global")
    if g + worst > c["global"]:
        raise BudgetExceeded("HuhQueue's shared AI budget is used up, so I'm using the free model.")
    if is_tester(store, uid):
        if _spent(store, "testing") + worst > c["testing"]:
            raise BudgetExceeded("The testing budget is used up, so I'm using the free model.")
    elif _spent(store, f"user:{uid}") + worst > c["user"]:
        raise BudgetExceeded(f"Your ${c['user']:.2f} of included Claude usage is used up, so I'm using the free model. "
                             "Add your own key in AI engine to keep the full agent.")


def record(store, uid: str, cost: float) -> None:
    now = time.time()
    for scope in scopes(store, uid):
        store.x("INSERT INTO brain_budget(scope,spent,calls,updated) VALUES(?,?,1,?) ON CONFLICT(scope) DO UPDATE SET "
                "spent=brain_budget.spent+excluded.spent, calls=brain_budget.calls+1, updated=excluded.updated", (scope, cost, now))


def byo_check(store, uid: str, add: int = 0) -> None:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if add:
        store.x("INSERT INTO brain_tokens(day,user_id,tokens) VALUES(?,?,?) ON CONFLICT(day,user_id) DO UPDATE SET "
                "tokens=brain_tokens.tokens+excluded.tokens", (day, uid, add))
        return
    rows = store.q("SELECT tokens FROM brain_tokens WHERE day=? AND user_id=?", (day, uid))
    if rows and int(rows[0]["tokens"]) >= caps()["byo_tokens"]:
        raise BudgetExceeded("Today's agent token cap for your own key is reached (it protects your key from a runaway loop).")
