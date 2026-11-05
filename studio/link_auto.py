"""Settings → Privacy & data → "Auto-approve video links" (owner ask, 5 Nov 2026).

Off by default. When the person turns it on they read and tick the rights statement (``link_ingest.ATTESTATION``) once;
that standing attestation is stored with its version and time. While it is on, a video-link request from a covered AI
app (every connected app, or the ones they picked) is approved at once instead of waiting in Inbox → Approvals: the
approval item is written as approved, a consent record names the app and the time, and Activity logs it as
``auto_approve_link_rights`` (shown as "auto-approved").

What it never changes: the fetch itself (netguard, mediasafe, the 4 GB / 3 h caps), free-tier quotas and daily caps, the
kill switch, paid features (studio/consent.py ``gate`` is separate and still asks), and posting: an agent still never
publishes without approval. A newer rights statement (``ATTESTATION_VERSION``) stops it counting until the person ticks
the new text. Revocable any time (``on: false``); revoking keeps the record of when it was given and when it was taken back.
"""
from __future__ import annotations

import json
import time
from typing import Any

from .actions import ActionError, Actor

SCOPES = ("all", "apps")
MAX_APPS = 100

SCHEMA = ("CREATE TABLE IF NOT EXISTS link_auto_approve (user_id TEXT PRIMARY KEY, enabled INTEGER DEFAULT 0, scope TEXT, "
          "apps TEXT, attestation TEXT, attestation_version TEXT, attested_at REAL, revoked_at REAL, updated REAL)")
_FLAG = "_link_auto_ready"


def ensure(store) -> None:
    if getattr(store, _FLAG, False):
        return
    sql = SCHEMA
    if store.pg:
        sql = sql.replace(" REAL", " DOUBLE PRECISION")
    store.x(sql)
    if store.pg:
        store.x("ALTER TABLE link_auto_approve ENABLE ROW LEVEL SECURITY")
    if not getattr(store, "_tx_depth", 0):  # only a committed schema is cached (a rollback can undo the CREATE)
        setattr(store, _FLAG, True)


def _row(store, uid: str) -> dict | None:
    ensure(store)
    rows = store.q("SELECT * FROM link_auto_approve WHERE user_id=?", (uid,))
    return dict(rows[0]) if rows else None


def _apps(raw: Any) -> list[int]:
    try:
        v = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [int(x) for x in v if isinstance(x, int) and not isinstance(x, bool)] if isinstance(v, list) else []


def _tokens(store, uid: str) -> list[dict]:
    return [{"id": int(r["id"]), "name": r["name"]} for r in
            store.q("SELECT id, name FROM agent_tokens WHERE user_id=? AND COALESCE(revoked,0)=0 ORDER BY id", (uid,))]


def get(store, a: Actor) -> dict:
    """The setting as the person sees it. Agents can read whether they are covered, never change it."""
    from . import link_ingest
    r = _row(store, a.uid) or {}
    on = bool(r.get("enabled"))
    current = r.get("attestation_version") == link_ingest.ATTESTATION_VERSION
    out = {"on": on and current, "scope": r.get("scope") if r.get("scope") in SCOPES else "all", "apps": _apps(r.get("apps")),
           "attested_at": r.get("attested_at") if on else None, "revoked_at": r.get("revoked_at"),
           "statement_changed": on and not current,
           "attestation": link_ingest.ATTESTATION, "attestation_version": link_ingest.ATTESTATION_VERSION,
           "covers": "video-link requests from AI apps only. Never paid features, never posting: agents still never publish "
                     "without your approval. Netguard, the size and length caps, free-tier quotas and daily caps still apply."}
    if a.kind == "human":
        out["available_apps"] = _tokens(store, a.uid)
        return out
    # an AI app sees only whether it is covered, never which other apps the person picked
    return {"on": out["on"], "covers_this_app": covers(store, a) is not None, "attestation": out["attestation"],
            "attestation_version": out["attestation_version"], "covers": out["covers"]}


def put(store, a: Actor, body: Any) -> dict:
    """Turn it on (needs ``rights_attested: true`` under the current statement version), change its scope, or turn it off.
    Types are checked: anything else answers 400."""
    from . import link_ingest
    if a.kind != "human":
        raise ActionError("Only the signed-in person can change auto-approve. An AI app can't turn it on.", 403)
    if not isinstance(body, dict):
        raise ActionError("Send a JSON object.", 400)
    on = body.get("on")
    if not isinstance(on, bool):
        raise ActionError("on must be true or false.", 400)
    scope = body.get("scope", "all")
    if scope not in SCOPES:
        raise ActionError("scope must be 'all' (every connected app) or 'apps' (the apps you pick).", 400)
    apps = body.get("apps", [])
    if not isinstance(apps, list) or len(apps) > MAX_APPS or any(not isinstance(x, int) or isinstance(x, bool) for x in apps):
        raise ActionError(f"apps must be a list of up to {MAX_APPS} AI app ids (numbers).", 400)
    attested = body.get("rights_attested", False)
    if not isinstance(attested, bool):
        raise ActionError("rights_attested must be true or false.", 400)
    ver = body.get("attestation_version")
    if ver is not None and not isinstance(ver, str):
        raise ActionError("attestation_version is the statement version string the page showed.", 400)
    now = time.time()
    prev = _row(store, a.uid) or {}
    if not on:
        with store.tx():
            store.x("INSERT INTO link_auto_approve(user_id,enabled,scope,apps,attestation,attestation_version,attested_at,revoked_at,updated) "
                    "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=0, revoked_at=excluded.revoked_at, "
                    "updated=excluded.updated",
                    (a.uid, 0, prev.get("scope") or "all", prev.get("apps") or "[]", prev.get("attestation"),
                     prev.get("attestation_version"), prev.get("attested_at"), now, now))
        store.audit(a.uid, a.label, "link_auto_approve_off", "safety", {"was_on": bool(prev.get("enabled"))})
        return get(store, a)
    mine = {t["id"] for t in _tokens(store, a.uid)}
    apps = sorted(set(apps))
    if scope == "apps":
        if not apps:
            raise ActionError("Pick at least one AI app, or choose all connected apps.", 400)
        if not set(apps) <= mine:
            raise ActionError("apps must be AI apps connected to your account.", 400)  # never says whose a foreign id is
    else:
        apps = []
    if ver not in (None, "", link_ingest.ATTESTATION_VERSION):
        raise ActionError("The rights statement changed since this page loaded. Reload, read it and tick it again.", 409)
    fresh = not (prev.get("enabled") and prev.get("attestation_version") == link_ingest.ATTESTATION_VERSION)
    if fresh and attested is not True:
        raise ActionError("Tick “" + link_ingest.ATTESTATION + "” to turn on auto-approve (rights_attested: true).", 403)
    attested_at = now if attested is True or fresh else prev.get("attested_at")
    with store.tx():
        store.x("INSERT INTO link_auto_approve(user_id,enabled,scope,apps,attestation,attestation_version,attested_at,revoked_at,updated) "
                "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET enabled=1, scope=excluded.scope, apps=excluded.apps, "
                "attestation=excluded.attestation, attestation_version=excluded.attestation_version, "
                "attested_at=excluded.attested_at, revoked_at=NULL, updated=excluded.updated",
                (a.uid, 1, scope, json.dumps(apps), link_ingest.ATTESTATION, link_ingest.ATTESTATION_VERSION, attested_at, None, now))
    store.audit(a.uid, a.label, "link_auto_approve_on", "safety",
                {"scope": scope, "apps": apps, "attestation_version": link_ingest.ATTESTATION_VERSION, "attested_at": attested_at})
    return get(store, a)


def covers(store, a: Actor) -> dict | None:
    """The standing attestation when it covers this AI app's link requests now, else None."""
    from . import link_ingest
    if getattr(a, "kind", "") != "agent" or not a.agent:
        return None
    try:
        r = _row(store, a.uid)
    except Exception:  # noqa: BLE001 - an unreadable setting means ask the person (fail closed)
        return None
    if not r or not r.get("enabled") or r.get("attestation_version") != link_ingest.ATTESTATION_VERSION:
        return None
    if r.get("scope") == "apps" and int(a.agent["id"]) not in _apps(r.get("apps")):
        return None
    return r
