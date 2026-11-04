"""Outgoing webhooks (Zapier / Make / n8n): CRUD, signed deliveries queued from events, retries, test ping, agent limits."""
import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from huhqueue import actions as reg
from huhqueue.models import WebhookDelivery
from huhqueue.services import webhooks as wh


@pytest.fixture(autouse=True)
def _unpinned_outbound(monkeypatch):
    """These tests mock outbound calls by host name (respx). netguard pins each vetted request to its resolved IP, which
    respx would see instead, so here DNS answers one public address and requests keep their host name; pinning itself is
    covered in tests/test_outbound_ssrf.py."""
    from huhqueue import netguard
    monkeypatch.setattr(netguard, "_getaddrinfo", lambda h, *a, **k: [(2, 1, 6, "", ("93.184.216.34", 0))])
    monkeypatch.setattr(netguard, "_pinnable", lambda c, url: False)

HOOK = "https://hooks.zapier.com/hooks/catch/1/abc"


def test_create_validates_and_returns_secret_once(api):
    assert api.post("/api/webhooks", {"url": "ftp://x"}).status_code == 400
    assert api.post("/api/webhooks", {"url": HOOK, "events": ["post.nope"]}).status_code == 400
    assert api.post("/api/webhooks", {"url": "http://169.254.169.254/latest"}).status_code == 400
    w = api.post("/api/webhooks", {"url": HOOK, "events": ["post.sent", "post.failed"]}).json()
    assert w["secret"].startswith("whsec_") and w["events"] == ["post.failed", "post.sent"]
    listed = api.get("/api/webhooks").json()
    assert "secret" not in listed["webhooks"][0] and "post.sent" in listed["events"]
    rot = api.patch(f"/api/webhooks/{w['id']}", {"rotate_secret": True}).json()
    assert rot["secret"] != w["secret"]
    assert api.patch(f"/api/webhooks/{w['id']}", {"active": False}).json()["active"] is False
    assert api.delete(f"/api/webhooks/{w['id']}").json()["ok"] and api.get("/api/webhooks").json()["webhooks"] == []


def test_sent_post_is_delivered_signed(api, core, channel):
    w = api.post("/api/webhooks", {"url": HOOK, "events": ["post.sent"]}).json()
    api.post("/api/posts", {"caption": "Hello webhooks", "channel_ids": [channel["id"]], "when": "now", "override_rate_limit": True})
    with respx.mock:
        route = respx.post(HOOK).mock(return_value=httpx.Response(200, json={"status": "success"}))
        api.post("/api/worker/tick")
        api.post("/api/worker/tick")
        assert route.call_count == 1
        req = route.calls[0].request
    body = json.loads(req.content)
    assert req.headers["X-HuhQueue-Event"] == "post.sent" and body["event"] == "post.sent"
    assert body["post"]["caption"] == "Hello webhooks" and body["post"]["permalink"] and body["channel"]["username"] == "huhbyte_demo"
    ts = req.headers["X-HuhQueue-Timestamp"]
    want = "sha256=" + hmac.new(w["secret"].encode(), f"{ts}.".encode() + req.content, hashlib.sha256).hexdigest()
    assert req.headers["X-HuhQueue-Signature"] == want
    d = api.get(f"/api/webhooks/{w['id']}/deliveries").json()["deliveries"]
    assert d[0]["status"] == "delivered" and d[0]["response_code"] == 200


def test_unsubscribed_events_are_not_queued(api, core, channel):
    api.post("/api/webhooks", {"url": HOOK, "events": ["post.failed"]})
    api.post("/api/posts", {"caption": "fine", "channel_ids": [channel["id"]], "when": "now", "override_rate_limit": True})
    with respx.mock(assert_all_called=False) as m:
        m.post(HOOK).mock(return_value=httpx.Response(200))
        api.post("/api/worker/tick")
    with core.db.session() as s:
        assert s.query(WebhookDelivery).count() == 0


def test_retries_with_backoff_then_fails_and_switches_off(api, core, channel):
    w = api.post("/api/webhooks", {"url": HOOK}).json()                     # all events
    api.post("/api/posts", {"caption": "FAIL please", "channel_ids": [channel["id"]], "when": "now", "override_rate_limit": True})
    now = wh.utcnow()
    with respx.mock:
        route = respx.post(HOOK).mock(return_value=httpx.Response(500))
        api.post("/api/worker/tick")
        with core.db.session() as s:
            d = s.query(WebhookDelivery).filter_by(event="post.failed").one()
            assert d.status == "pending" and d.attempts == 1 and d.next_attempt_at > now
        t = now
        for secs in wh.BACKOFF:
            t += timedelta(seconds=secs + 1)
            wh.deliver_due(core, t)
        assert route.call_count >= wh.MAX_ATTEMPTS
    with core.db.session() as s:
        d = s.query(WebhookDelivery).filter_by(event="post.failed").one()
        assert d.status == "failed" and d.attempts == wh.MAX_ATTEMPTS
    rid = api.post(f"/api/webhooks/deliveries/{d.id}/redeliver").json()
    assert rid["status"] == "pending"
    with core.db.session() as s:
        from huhqueue.models import Webhook
        hook = s.get(Webhook, w["id"])
        hook.failures = wh.DISABLE_AFTER - 1
    with respx.mock:
        respx.post(HOOK).mock(return_value=httpx.Response(500))
        for i in range(wh.MAX_ATTEMPTS):
            wh.deliver_due(core, wh.utcnow() + timedelta(days=1 + i))
    assert api.get("/api/webhooks").json()["webhooks"][0]["active"] is False


def test_ping(api):
    w = api.post("/api/webhooks", {"url": HOOK}).json()
    with respx.mock:
        respx.post(HOOK).mock(return_value=httpx.Response(200, text="ok"))
        r = api.post(f"/api/webhooks/{w['id']}/test").json()
    assert r["ok"] and r["status_code"] == 200
    with respx.mock:
        respx.post(HOOK).mock(side_effect=httpx.ConnectError("down"))
        r = api.post(f"/api/webhooks/{w['id']}/test").json()
    assert not r["ok"] and r["status_code"] == 0


def test_agents_cannot_add_or_repoint_webhooks(app, api):
    tok = api.post("/api/tokens", {"name": "admin-agent", "scopes": ["admin"]}).json()["token"]
    writer = api.post("/api/tokens", {"name": "writer", "scopes": ["read", "write"]}).json()["token"]
    c = TestClient(app)
    H = lambda t: {"Authorization": f"Bearer {t}"}  # noqa: E731
    assert c.post("/api/webhooks", json={"url": HOOK}, headers=H(tok)).status_code == 403
    assert c.get("/api/webhooks", headers=H(writer)).status_code == 403
    assert c.get("/api/webhooks", headers=H(tok)).status_code == 200
    assert not reg.BY_ID["webhook.create"].agent_visible() and reg.BY_ID["webhook.test"].tool in reg.mcp_tool_names()
