"""Outgoing webhooks for Zapier / Make / n8n (services/webhooks.py). Adding or re-pointing a webhook sends your data to
another server, so those two actions are `human` (UI only, never an agent tool); listing, testing and reading
deliveries are open to agents like everything else."""
from __future__ import annotations

from fastapi import Body, FastAPI, HTTPException, Request

from .. import actions as reg
from ..actions import B, S, SA, Action
from ..models import Webhook, WebhookDelivery
from ..services import webhooks as wh


def add_webhook_routes(app: FastAPI, core) -> None:
    def get(s, wid) -> Webhook:
        w = s.get(Webhook, wid)
        if not w:
            raise HTTPException(404, "Webhook not found")
        return w

    def bad(e):
        raise HTTPException(400, str(e)) from e

    def person_only(request: Request):
        if getattr(request.state, "via", None) == "token":
            raise HTTPException(403, "Webhooks send your data to another server, so a person adds or edits them in the app")

    @app.get("/api/webhooks", tags=["webhooks"])
    def list_hooks():
        with core.db.session() as s:
            return {"webhooks": [wh.to_dict(core, w) for w in s.query(Webhook).order_by(Webhook.created_at)], "events": wh.EVENTS}

    @app.post("/api/webhooks", tags=["webhooks"])
    def create(request: Request, payload: dict = Body(...)):
        person_only(request)
        try:
            with core.db.session() as s:
                return wh.create(core, s, payload.get("url", ""), payload.get("events"), payload.get("active", True))
        except wh.WebhookError as e:
            bad(e)

    @app.patch("/api/webhooks/{wid}", tags=["webhooks"])
    def update(wid: str, request: Request, payload: dict = Body(...)):
        person_only(request)
        try:
            with core.db.session() as s:
                return wh.update(core, s, get(s, wid), payload)
        except wh.WebhookError as e:
            bad(e)

    @app.delete("/api/webhooks/{wid}", tags=["webhooks"])
    def delete(wid: str):
        with core.db.session() as s:
            s.delete(get(s, wid))
            s.query(WebhookDelivery).filter(WebhookDelivery.webhook_id == wid).delete()
        return {"ok": True}

    @app.post("/api/webhooks/{wid}/test", tags=["webhooks"])
    def test(wid: str):
        with core.db.session() as s:
            return wh.test(core, s, get(s, wid))

    @app.get("/api/webhooks/{wid}/deliveries", tags=["webhooks"])
    def deliveries(wid: str, limit: int = 50):
        with core.db.session() as s:
            get(s, wid)
            return {"deliveries": wh.deliveries(s, wid, limit)}

    @app.post("/api/webhooks/deliveries/{did}/redeliver", tags=["webhooks"])
    def redeliver(did: str):
        with core.db.session() as s:
            d = s.get(WebhookDelivery, did)
            if not d:
                raise HTTPException(404, "Delivery not found")
            return wh.redeliver(core, s, d)


EV = {**SA, "description": "event names (empty or ['*'] = all): " + ", ".join(wh.EVENTS)}
for _a in (
    Action("webhook.list", "List outgoing webhooks (Zapier, Make, n8n)", "GET", "/api/webhooks", scope="admin",
           ui="Settings › Webhooks"),
    Action("webhook.create", "Add an outgoing webhook", "POST", "/api/webhooks", {"url": S, "events": EV, "active": B}, ("url",),
           "admin", "human", ui="Settings › Webhooks › Add"),
    Action("webhook.update", "Edit a webhook (URL, events, on/off, new secret)", "PATCH", "/api/webhooks/{id}",
           {"id": S, "url": S, "events": EV, "active": B, "rotate_secret": B}, ("id",), "admin", "human", ui="Settings › Webhooks"),
    Action("webhook.delete", "Delete a webhook", "DELETE", "/api/webhooks/{id}", {"id": S}, ("id",), "admin", "destructive",
           ui="Settings › Webhooks"),
    Action("webhook.test", "Send a test ping to a webhook", "POST", "/api/webhooks/{id}/test", {"id": S}, ("id",), "admin", "write",
           ui="Settings › Webhooks › Test"),
    Action("webhook.deliveries", "Recent deliveries of a webhook", "GET", "/api/webhooks/{id}/deliveries", {"id": S, "limit": {"type": "integer"}},
           ("id",), "admin", ui="Settings › Webhooks › Deliveries"),
    Action("webhook.redeliver", "Send a failed delivery again", "POST", "/api/webhooks/deliveries/{id}/redeliver", {"id": S}, ("id",),
           "admin", "write", ui="Settings › Webhooks › Deliveries"),
):
    reg.register(_a)
