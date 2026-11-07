"""Claude allowance per person: the default for everyone, a group's amount, or a personal amount set by the admin.

Which allowance a person gets (first match wins):

1. a **personal** allowance an admin set on that person (``users.claude_allowance_usd``),
2. the allowance of the **group** they are in (``credit_groups`` / ``credit_group_members``; at most one group each),
3. the **default** for everyone: ``AI_BUDGET_PER_USER_USD`` (budget.caps()["user"], $5 unless Render says otherwise).

What the person already spent (``brain_budget`` scope ``user:<id>``, lifetime) always counts against it, so raising an
allowance from $5 to $10 gives $5 more, and lowering it below the spend means "out of credit" at once. Testers listed in
HQ_BRAIN_TESTERS keep drawing from the shared testing pool, and every call stays inside the app-wide cap
(AI_BUDGET_TOTAL_USD), which only Render changes.

The admin's controls (studio/admin.py routes, the /admin page) are browser-session only, CSRF-checked and written to
``admin_audit`` with the old and new values. There are no MCP tools or agent-token routes for any of this, on purpose.
"""
from __future__ import annotations

import json
import math
import re
import time

MAX_USD = 500.0          # the largest personal or group allowance the panel accepts
MAX_STEP_USD = 100.0     # the largest single "+$" top-up
NAME_MAX = 60
MAX_BULK = 500           # users moved into or out of a group in one request


class CreditError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _usd(v, what: str, lo: float = 0.0, hi: float = MAX_USD) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)) or not lo <= float(v) <= hi:
        raise CreditError(f"{what} must be a dollar amount from {lo:g} to {hi:g}.")
    return round(float(v), 2)


def default_usd() -> float:
    from . import budget
    return budget.caps()["user"]


# ------------------------------------------------------------------ which allowance applies
def allowance(store, uid: str) -> dict:
    """{"cap_usd", "source": "personal" | "group" | "default", "group": {"id", "name", "allowance_usd"} | None,
    "personal_usd": float | None, "default_usd"}. Never raises: an unreadable row falls back to the default."""
    d = default_usd()
    personal, group = None, None
    try:
        rows = store.q("SELECT claude_allowance_usd AS p FROM users WHERE id=?", (uid,))
        if rows and rows[0]["p"] is not None:
            personal = round(float(rows[0]["p"]), 2)
        g = store.q("SELECT g.id, g.name, g.allowance_usd FROM credit_group_members m JOIN credit_groups g ON g.id=m.group_id "
                    "WHERE m.user_id=?", (uid,))
        if g:
            group = {"id": int(g[0]["id"]), "name": g[0]["name"] or "", "allowance_usd": round(float(g[0]["allowance_usd"] or 0), 2)}
    except Exception:  # noqa: BLE001  (an older database without the column or tables: everyone on the default)
        pass
    if personal is not None:
        cap, src = personal, "personal"
    elif group is not None:
        cap, src = group["allowance_usd"], "group"
    else:
        cap, src = d, "default"
    return {"cap_usd": cap, "source": src, "group": group, "personal_usd": personal, "default_usd": d}


def cap(store, uid: str) -> float:
    return allowance(store, uid)["cap_usd"]


def for_users(store, ids: list[str]) -> dict:
    """The admin users table: {uid: {cap_usd, spent_usd, left_usd, source, group, tester}} for one page of users."""
    from . import budget
    if not ids:
        return {}
    d = default_usd()
    marks = ",".join("?" * len(ids))
    personal, groups, spent = {}, {}, {}
    try:
        personal = {r["id"]: r["p"] for r in store.q(f"SELECT id, claude_allowance_usd AS p FROM users WHERE id IN ({marks})", tuple(ids))}
        groups = {r["user_id"]: {"id": int(r["id"]), "name": r["name"] or "", "allowance_usd": round(float(r["allowance_usd"] or 0), 2)}
                  for r in store.q(f"SELECT m.user_id, g.id, g.name, g.allowance_usd FROM credit_group_members m JOIN credit_groups g "
                                   f"ON g.id=m.group_id WHERE m.user_id IN ({marks})", tuple(ids))}
    except Exception:  # noqa: BLE001
        pass
    try:
        spent = {r["scope"][5:]: float(r["spent"] or 0) for r in store.q(
            f"SELECT scope, spent FROM brain_budget WHERE scope IN ({marks})", tuple("user:" + i for i in ids))}
    except Exception:  # noqa: BLE001  (the budget table is created on the first AI call: nothing spent yet)
        pass
    out = {}
    for uid in ids:
        p, g = personal.get(uid), groups.get(uid)
        if p is not None:
            c, src = round(float(p), 2), "personal"
        elif g is not None:
            c, src = g["allowance_usd"], "group"
        else:
            c, src = d, "default"
        s = round(spent.get(uid, 0.0), 4)
        out[uid] = {"cap_usd": c, "spent_usd": round(s, 2), "left_usd": round(max(0.0, c - s), 2), "source": src, "group": g,
                    "tester": budget.is_tester(store, uid)}
    return out


# ------------------------------------------------------------------ admin: one person
# This module never imports studio/admin.py (FastAPI): budget.py imports it, and the video worker imports budget.py
# (tests/studio/test_owner_bugs_nov2.py keeps the worker's requirements free of the web stack). The two helpers below
# match admin._target and admin.log exactly.
UID_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,100}")


def _target(store, uid):
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
        raise CreditError("No such user.", 404)
    u = store.user(uid)
    if not u:
        raise CreditError("No such user.", 404)
    return u


def _log(store, admin_uid: str, action: str, subject: str, detail: dict) -> None:
    from .store import sha
    store.x("INSERT INTO admin_audit(ts,admin,action,subject_hash,detail,ok) VALUES(?,?,?,?,?,1)",
            (time.time(), admin_uid, action, sha("hq-subject:" + subject) if subject else "", json.dumps(detail or {})[:2000]))


def _spent(store, uid: str) -> float:
    try:
        rows = store.q("SELECT spent FROM brain_budget WHERE scope=?", ("user:" + uid,))
        return round(float(rows[0]["spent"]), 4) if rows else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def view(store, uid: str) -> dict:
    """One person's credit for the admin dialog: the allowance, where it comes from, what is spent and left."""
    from . import budget
    a = allowance(store, uid)
    s = _spent(store, uid)
    return {**a, "spent_usd": round(s, 2), "left_usd": round(max(0.0, a["cap_usd"] - s), 2), "tester": budget.is_tester(store, uid)}


def set_user(store, admin_uid: str, uid, body: dict) -> dict:
    """Body (any one of):
    {"allowance_usd": 12.5}  set a personal allowance (0-500),
    {"allowance_usd": null}  clear it (back to the group's or the default),
    {"add_usd": 5}           raise the person's current allowance by this much (0.01-100) as a personal allowance,
    {"group_id": 3 | null}   put the person in a group, or take them out.
    An optional "note" (<= 200 chars) goes in the audit row."""
    _target(store, uid)
    if not isinstance(body, dict):
        raise CreditError("Send a JSON object.")
    keys = {"allowance_usd", "add_usd", "group_id"} & set(body)
    if len(keys) != 1:
        raise CreditError("Give exactly one of allowance_usd (0-500 or null), add_usd (0.01-100) or group_id (a group or null).")
    note = body.get("note", "")
    note = note.strip()[:200] if isinstance(note, str) else ""
    before = allowance(store, uid)
    k = keys.pop()
    with store.tx():
        if k == "allowance_usd":
            v = body["allowance_usd"]
            new = None if v is None else _usd(v, "allowance_usd")
            store.x("UPDATE users SET claude_allowance_usd=? WHERE id=?", (new, uid))
            action = "credit_set" if new is not None else "credit_clear"
        elif k == "add_usd":
            step = _usd(body["add_usd"], "add_usd", 0.01, MAX_STEP_USD)
            new = round(before["cap_usd"] + step, 2)
            if new > MAX_USD:
                raise CreditError(f"That would make the allowance ${new:g}; the most is ${MAX_USD:g}.")
            store.x("UPDATE users SET claude_allowance_usd=? WHERE id=?", (new, uid))
            action = "credit_add"
        else:
            gid = body["group_id"]
            if gid is None:
                store.x("DELETE FROM credit_group_members WHERE user_id=?", (uid,))
            else:
                g = _group(store, gid)
                _join(store, uid, g["id"])
            action = "credit_group"
        after = allowance(store, uid)
        _log(store, admin_uid, action, uid, {"from": _brief(before), "to": _brief(after),
                                             **({"add_usd": body["add_usd"]} if k == "add_usd" else {}),
                                             **({"note": note} if note else {})})
    return view(store, uid)


def _brief(a: dict) -> dict:
    return {"cap_usd": a["cap_usd"], "source": a["source"], **({"group": a["group"]["name"]} if a["group"] else {})}


def _join(store, uid: str, gid: int) -> None:
    store.x("INSERT INTO credit_group_members(user_id,group_id,added) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
            "group_id=excluded.group_id, added=excluded.added", (uid, gid, time.time()))


# ------------------------------------------------------------------ admin: groups
def _name(v) -> str:
    if not isinstance(v, str) or not v.strip():
        raise CreditError("Give the group a name.")
    n = " ".join(v.split())
    if len(n) > NAME_MAX:
        raise CreditError(f"Group names are at most {NAME_MAX} characters.")
    return n


def _gid(v) -> int:
    if isinstance(v, bool):
        raise CreditError("No such group.", 404)
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise CreditError("No such group.", 404) from None
    if not 0 < i < 2**31:
        raise CreditError("No such group.", 404)
    return i


def _group(store, gid) -> dict:
    rows = store.q("SELECT id, name, allowance_usd FROM credit_groups WHERE id=?", (_gid(gid),))
    if not rows:
        raise CreditError("No such group.", 404)
    r = rows[0]
    return {"id": int(r["id"]), "name": r["name"] or "", "allowance_usd": round(float(r["allowance_usd"] or 0), 2)}


def _unique(store, name: str, skip: int | None = None) -> None:
    for r in store.q("SELECT id FROM credit_groups WHERE LOWER(name)=?", (name.lower(),)):
        if int(r["id"]) != skip:
            raise CreditError(f"There is already a group called {name!r}.")


def groups(store) -> list[dict]:
    counts = {}
    try:
        counts = {int(r["group_id"]): int(r["n"]) for r in store.q("SELECT group_id, COUNT(*) AS n FROM credit_group_members GROUP BY group_id")}
    except Exception:  # noqa: BLE001
        pass
    return [{"id": int(r["id"]), "name": r["name"] or "", "allowance_usd": round(float(r["allowance_usd"] or 0), 2),
             "members": counts.get(int(r["id"]), 0), "created": r["created"], "updated": r["updated"]}
            for r in store.q("SELECT id, name, allowance_usd, created, updated FROM credit_groups ORDER BY LOWER(name)")]


def create_group(store, admin_uid: str, body: dict) -> dict:
    if not isinstance(body, dict):
        raise CreditError("Send a JSON object.")
    name = _name(body.get("name"))
    usd = _usd(body.get("allowance_usd"), "allowance_usd")
    now = time.time()
    with store.tx():
        _unique(store, name)
        store.x("INSERT INTO credit_groups(name,allowance_usd,created,updated) VALUES(?,?,?,?)", (name, usd, now, now))
        gid = int(store.q("SELECT id FROM credit_groups WHERE LOWER(name)=?", (name.lower(),))[0]["id"])
        _log(store, admin_uid, "group_create", "", {"group": gid, "name": name, "allowance_usd": usd})
    return {**_group(store, gid), "members": 0}


def update_group(store, admin_uid: str, gid, body: dict) -> dict:
    """{"name": "...", "allowance_usd": 15} (either or both): a new amount applies to every member at once."""
    g = _group(store, gid)
    if not isinstance(body, dict) or not ({"name", "allowance_usd"} & set(body)):
        raise CreditError("Give name and/or allowance_usd.")
    name = _name(body["name"]) if "name" in body else g["name"]
    usd = _usd(body["allowance_usd"], "allowance_usd") if "allowance_usd" in body else g["allowance_usd"]
    with store.tx():
        if name.lower() != g["name"].lower():
            _unique(store, name, skip=g["id"])
        store.x("UPDATE credit_groups SET name=?, allowance_usd=?, updated=? WHERE id=?", (name, usd, time.time(), g["id"]))
        _log(store, admin_uid, "group_update", "", {"group": g["id"], "name": {"from": g["name"], "to": name},
                                                     "allowance_usd": {"from": g["allowance_usd"], "to": usd}})
    return next(x for x in groups(store) if x["id"] == g["id"])


def delete_group(store, admin_uid: str, gid, body: dict) -> dict:
    """Members go back to their personal allowance or the default. Confirm with {"confirm": "<group name>"}."""
    g = _group(store, gid)
    if not isinstance(body, dict) or body.get("confirm") != g["name"]:
        raise CreditError("Type the group's name exactly to confirm.")
    with store.tx():
        n = int(store.q("SELECT COUNT(*) AS n FROM credit_group_members WHERE group_id=?", (g["id"],))[0]["n"] or 0)
        store.x("DELETE FROM credit_group_members WHERE group_id=?", (g["id"],))
        store.x("DELETE FROM credit_groups WHERE id=?", (g["id"],))
        _log(store, admin_uid, "group_delete", "", {"group": g["id"], "name": g["name"], "allowance_usd": g["allowance_usd"],
                                                     "members": n})
    return {"deleted": True, "id": g["id"], "members_released": n}


def group_members(store, gid) -> list[dict]:
    g = _group(store, gid)
    return [{"id": r["id"], "name": r["name"] or "", "email": r["email"] or ""} for r in store.q(
        "SELECT u.id, u.name, u.email FROM credit_group_members m JOIN users u ON u.id=m.user_id WHERE m.group_id=? "
        "ORDER BY LOWER(COALESCE(u.name, u.email, u.id))", (g["id"],))]


def set_members(store, admin_uid: str, gid, body: dict) -> dict:
    """{"add": [user ids], "remove": [user ids]}: adding moves a person out of any other group (one group each)."""
    g = _group(store, gid)
    if not isinstance(body, dict):
        raise CreditError("Send a JSON object.")
    add, rem = body.get("add") or [], body.get("remove") or []
    if not isinstance(add, list) or not isinstance(rem, list) or not (add or rem):
        raise CreditError("Give add and/or remove as lists of user ids.")
    if len(add) + len(rem) > MAX_BULK:
        raise CreditError(f"At most {MAX_BULK} users at a time.")
    for uid in add + rem:
        _target(store, uid)
    moved = 0
    with store.tx():
        for uid in dict.fromkeys(add):
            _join(store, uid, g["id"])
            moved += 1
        removed = 0
        for uid in dict.fromkeys(rem):
            if store.q("SELECT 1 AS n FROM credit_group_members WHERE user_id=? AND group_id=?", (uid, g["id"])):
                store.x("DELETE FROM credit_group_members WHERE user_id=? AND group_id=?", (uid, g["id"]))
                removed += 1
        _log(store, admin_uid, "group_members", "", {"group": g["id"], "name": g["name"], "added": moved, "removed": removed})
    return {"id": g["id"], "added": moved, "removed": removed,
            "members": next(x for x in groups(store) if x["id"] == g["id"])["members"]}
