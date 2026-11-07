"""Hard budgets for the app's own model keys (the operator's ANTHROPIC_API_KEY), persisted in the database.

- Global lifetime cap AI_BUDGET_TOTAL_USD (default $60) across every account.
- Testing pool AI_BUDGET_TEST_USD (default $30) for the accounts in HQ_BRAIN_TESTERS (comma-separated Buffer account
  emails or user ids); testers draw from this pool instead of the per-user cap.
- Per signed-in user AI_BUDGET_PER_USER_USD (default $5) lifetime, unless the admin gave that person a personal
  allowance or put them in a group with its own (studio/credits.py: personal, then group, then this default).
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
CREATE TABLE IF NOT EXISTS ai_engine_pref (user_id TEXT PRIMARY KEY, engine TEXT, updated REAL);
"""


class BudgetExceeded(Exception):
    pass


def ensure(store) -> None:
    for stmt in [x.strip() for x in SCHEMA.split(";") if x.strip()]:
        if store.pg:
            stmt = stmt.replace(" REAL", " DOUBLE PRECISION")
        store.x(stmt)
    if store.pg:
        for t in ("brain_budget", "brain_tokens", "ai_engine_pref"):
            store.x(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")


def caps() -> dict:
    e = os.environ.get
    return {"global": float(e("AI_BUDGET_TOTAL_USD") or e("HQ_BRAIN_GLOBAL_USD") or "60"),
            "testing": float(e("AI_BUDGET_TEST_USD") or e("HQ_BRAIN_TEST_USD") or "30"),
            "user": float(e("AI_BUDGET_PER_USER_USD") or e("HQ_BRAIN_USER_USD") or "5"), "byo_tokens": int(os.environ.get("HQ_BRAIN_BYO_TOKENS", "400000"))}


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


def user_cap(store, uid: str) -> float:
    """This person's own Claude allowance in USD: personal, group or default (studio/credits.py)."""
    from . import credits
    return credits.cap(store, uid)


def scopes(store, uid: str) -> list[str]:
    return ["global", "testing" if is_tester(store, uid) else f"user:{uid}"]


def remaining(store, uid: str) -> dict:
    c = caps()
    g = c["global"] - _spent(store, "global")
    if is_tester(store, uid):
        mine, label, cap = c["testing"] - _spent(store, "testing"), "testing pool", c["testing"]
    else:
        cap = user_cap(store, uid)
        mine, label = cap - _spent(store, f"user:{uid}"), "your allowance"
    return {"global_left_usd": round(max(0.0, g), 4), "left_usd": round(max(0.0, min(g, mine)), 4), "pool": label, "pool_cap_usd": cap,
            "global_cap_usd": c["global"], "spent_usd": round(cap - max(0.0, mine), 4)}


NUDGE_AT = 0.70  # share of the allowance used before the meter offers the free route (HuhQueue in Claude / ChatGPT)
NUDGE = "Keep going free in Claude: connect HuhQueue"


def meter(store, uid: str) -> dict:
    """The allowance meter the app shows: "$0.62 of $1 left", and from 70% used (and at 100%) the brain-swap prompt:
    the person's own Claude / ChatGPT does the thinking over MCP, HuhQueue does the editing, at $0 AI spend to us."""
    r = remaining(store, uid)
    cap = float(r["pool_cap_usd"] or 0)
    left = float(r["left_usd"])
    used = 1.0 if cap <= 0 else min(1.0, max(0.0, 1 - left / cap))
    out = used >= 0.999 or left <= 0.005
    return {"left_usd": round(left, 2), "cap_usd": cap, "used_pct": int(round(used * 100)), "pool": r["pool"],
            "label": f"${left:.2f} of ${cap:g} left", "out": out, "nudge": out or used >= NUDGE_AT,
            "message": ("Out of credit? " if out else "") + NUDGE + "."}


def check(store, uid: str, model: str, est_in: int, max_out: int) -> None:
    """Refuse BEFORE the call when the worst case would cross any cap."""
    ai_open(store)
    worst = price(model, est_in, max_out)
    c = caps()
    g = _spent(store, "global")
    if g + worst > c["global"]:
        raise BudgetExceeded("HuhQueue's shared AI budget is used up, so I'm using the free model.")
    if is_tester(store, uid):
        if _spent(store, "testing") + worst > c["testing"]:
            raise BudgetExceeded("The testing budget is used up, so I'm using the free model.")
    elif _spent(store, f"user:{uid}") + worst > (cap := user_cap(store, uid)):
        raise BudgetExceeded(f"Your ${cap:.2f} of included Claude usage is used up, so I'm using the free model. "
                             "Add your own key in AI engine to keep the full agent.")


def record(store, uid: str, cost: float) -> None:
    now = time.time()
    for scope in scopes(store, uid):
        store.x("INSERT INTO brain_budget(scope,spent,calls,updated) VALUES(?,?,1,?) ON CONFLICT(scope) DO UPDATE SET "
                "spent=brain_budget.spent+excluded.spent, calls=brain_budget.calls+1, updated=excluded.updated", (scope, cost, now))


def charge_user(store, uid: str, cost: float) -> None:
    """A non-model cost (a scan + renders) charged to the person's own allowance only, not the shared AI pool."""
    now = time.time()
    for scope in scopes(store, uid)[1:]:
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


# ------------------------------------------------------------------ Gemini on Oracle Cloud (source=oci)
# A separate ledger: Oracle trial credit, metered at Google's public Gemini list price as an upper bound (oci_genai.PRICES).
# App-wide pool AI_OCI_BUDGET_TOTAL_USD (default $200) and per signed-in user AI_OCI_BUDGET_PER_USER_USD (default $3).
OCI_GLOBAL, OCI_USER = "oci:global", "oci:user:"


def oci_caps() -> dict:
    e = os.environ.get
    return {"global": float(e("AI_OCI_BUDGET_TOTAL_USD") or "200"), "user": float(e("AI_OCI_BUDGET_PER_USER_USD") or "3")}


def ai_open(store) -> None:
    """The operator's AI kill switch (studio/opsctl.py): no call on HuhQueue's keys while it is on."""
    from . import opsctl
    if opsctl.paused("ai", store):
        raise BudgetExceeded(opsctl.message("ai"))


def oci_check(store, uid: str, model: str, est_in: int, max_out: int) -> None:
    from . import oci_genai
    ai_open(store)
    worst = oci_genai.price(model, est_in, max_out)
    c = oci_caps()
    if _spent(store, OCI_GLOBAL) + worst > c["global"]:
        raise BudgetExceeded("HuhQueue's shared Gemini credit is used up.")
    if _spent(store, OCI_USER + uid) + worst > c["user"]:
        raise BudgetExceeded(f"Your ${c['user']:g} of included Gemini usage is used up.")


def oci_record(store, uid: str, cost: float) -> None:
    now = time.time()
    for scope in (OCI_GLOBAL, OCI_USER + uid):
        store.x("INSERT INTO brain_budget(scope,spent,calls,updated) VALUES(?,?,1,?) ON CONFLICT(scope) DO UPDATE SET "
                "spent=brain_budget.spent+excluded.spent, calls=brain_budget.calls+1, updated=excluded.updated", (scope, cost, now))


def _credit(spent: float, cap: float, pool_spent: float, pool_cap: float, what: str) -> dict:
    """One credit line for the engine picker: "$0.34 of your $1 used", the pool, and the 70% / 100% warning level."""
    mine_left = max(0.0, cap - spent)
    pool_left = max(0.0, pool_cap - pool_spent)
    used = 1.0 if cap <= 0 else min(1.0, max(0.0, spent / cap))
    out = mine_left <= 0.005 or pool_left <= 0.005
    level = "out" if out else "low" if used >= NUDGE_AT else "ok"
    return {"used_usd": round(min(spent, cap), 2), "cap_usd": cap, "left_usd": round(min(mine_left, pool_left), 2),
            "used_pct": 100 if out else int(round(used * 100)), "level": level, "out": out,
            "label": f"${min(spent, cap):.2f} of your ${cap:g} used",
            "pool": {"left_usd": round(pool_left, 2), "cap_usd": pool_cap,
                     "label": f"App-wide {what} pool: ${pool_left:,.2f} of ${pool_cap:,.0f} left"}}


def claude_credit(store, uid: str) -> dict:
    c = caps()
    if is_tester(store, uid):
        spent, cap = _spent(store, "testing"), c["testing"]
    else:
        spent, cap = _spent(store, f"user:{uid}"), user_cap(store, uid)
    return _credit(spent, cap, _spent(store, "global"), c["global"], "Claude")


def oci_credit(store, uid: str) -> dict:
    c = oci_caps()
    return _credit(_spent(store, OCI_USER + uid), c["user"], _spent(store, OCI_GLOBAL), c["global"], "Gemini")


# ------------------------------------------------------------------ free engines on HuhQueue's keys (free_engines)
# Google AI Studio, NVIDIA NIM and Hugging Face cost HuhQueue nothing but have free-tier limits, so they are metered in
# calls per UTC day: a per-person cap and an app-wide pool per engine (FREE_<ENGINE>_USER_DAILY / FREE_<ENGINE>_APP_DAILY
# override the defaults in free_engines.ENGINES). Rows live in brain_budget (calls column) under day-stamped scopes.
def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _calls(store, scope: str) -> int:
    rows = store.q("SELECT calls FROM brain_budget WHERE scope=?", (scope,))
    return int(rows[0]["calls"] or 0) if rows else 0


def free_caps(eid: str) -> dict:
    from . import free_engines
    c, env = free_engines.ENGINES[eid], "FREE_" + eid.upper().replace("-", "_")
    return {"user": int(os.environ.get(env + "_USER_DAILY") or c["user_daily"]),
            "app": int(os.environ.get(env + "_APP_DAILY") or c["app_daily"])}


def _free_scopes(eid: str, uid: str) -> tuple[str, str]:
    d = _day()
    return f"free:{eid}:{d}:app", f"free:{eid}:{d}:user:{uid}"


def free_check(store, uid: str, eid: str) -> None:
    from . import free_engines
    ai_open(store)
    app_s, user_s = _free_scopes(eid, uid)
    c, name = free_caps(eid), free_engines.ENGINES[eid]["short"]
    if _calls(store, app_s) >= c["app"]:
        raise BudgetExceeded(f"HuhQueue's shared {name} allowance for today is used up.")
    if _calls(store, user_s) >= c["user"]:
        raise BudgetExceeded(f"Your {c['user']} free {name} calls for today are used up.")


def free_record(store, uid: str, eid: str, tokens: int = 0) -> None:
    now = time.time()
    for scope in _free_scopes(eid, uid):
        store.x("INSERT INTO brain_budget(scope,spent,calls,updated) VALUES(?,?,1,?) ON CONFLICT(scope) DO UPDATE SET "
                "spent=brain_budget.spent+excluded.spent, calls=brain_budget.calls+1, updated=excluded.updated", (scope, float(tokens), now))


def free_credit(store, uid: str, eid: str) -> dict:
    """"NVIDIA (free developer credits): 12 of your 150 calls today", the app-wide pool, and the 70% / 100% level."""
    from . import free_engines
    app_s, user_s = _free_scopes(eid, uid)
    c, credit = free_caps(eid), free_engines.ENGINES[eid]["credit"]
    mine, pool = _calls(store, user_s), _calls(store, app_s)
    used = 1.0 if c["user"] <= 0 else min(1.0, mine / c["user"])
    out = mine >= c["user"] or pool >= c["app"]
    level = "out" if out else "low" if used >= NUDGE_AT else "ok"
    pool_left = max(0, c["app"] - pool)
    return {"kind": "free-credit", "calls": mine, "cap_calls": c["user"], "left_calls": max(0, min(c["user"] - mine, pool_left)),
            "used_pct": 100 if out else int(round(used * 100)), "level": level, "out": out,
            "label": f"{credit}: {min(mine, c['user'])} of your {c['user']} calls today",
            "pool": {"left_calls": pool_left, "cap_calls": c["app"],
                     "label": f"{free_engines.ENGINES[eid]['short']} today: {pool_left:,} of {c['app']:,} app-wide calls left"}}
