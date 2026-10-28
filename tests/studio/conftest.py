import json
import os
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import drafter
from studio.app import create_app
from studio.config import load_settings

CHANNELS = [
    {"id": "ch_x", "name": "huhbyte", "displayName": "HuhByte X", "service": "twitter", "avatar": "https://img/x.png",
     "isDisconnected": False, "isQueuePaused": False},
    {"id": "ch_li", "name": "Ram", "displayName": "Ram on LinkedIn", "service": "linkedin", "avatar": "",
     "isDisconnected": False, "isQueuePaused": False},
    {"id": "ch_ig", "name": "huhbyte.ai", "displayName": "huhbyte.ai", "service": "instagram", "avatar": "",
     "isDisconnected": False, "isQueuePaused": False},
]
ARTICLE = {"url": "https://news.example.com/ai-launch", "title": "OpenAI launches a faster reasoning model for developers",
           "description": "The new model halves latency and costs less, with the same accuracy on coding benchmarks.",
           "image": "https://news.example.com/hero.jpg", "site": "Example News",
           "paragraphs": ["Developers can switch today in the API, and the company says pricing drops by forty percent for most workloads."]}
RL = '"100-in-15min";r=97;t=880, "500-in-1day";r=490;t=86000'
RLP = '"100-in-15min";q=100;w=900, "500-in-1day";q=500;w=86400'


class FakeBuffer:
    def __init__(self):
        self.calls, self.token_calls, self.created, self.ideas = [], [], [], []
        self.n = 0
        self.refresh_tokens = {"rt_1"}
        self.fail_429 = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        h = {"RateLimit": RL, "RateLimit-Policy": RLP}
        if request.url.host == "auth.buffer.com":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0] and form["client_secret"] == ["sec"]
                return httpx.Response(200, json={"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600,
                                                 "scope": "account:read posts:write offline_access"})
            rt = form["refresh_token"][0]
            if rt not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh_tokens.discard(rt)
            self.n += 1
            new = f"rt_{self.n + 1}"
            self.refresh_tokens.add(new)
            return httpx.Response(200, json={"access_token": f"at_{self.n + 1}", "refresh_token": new, "expires_in": 3600})
        body = json.loads(request.content)
        q, v = body["query"], body.get("variables") or {}
        self.calls.append((q, v, request.headers.get("authorization")))
        if self.fail_429:
            self.fail_429 -= 1
            return httpx.Response(429, headers={**h, "Retry-After": "120"}, json={})
        if "account" in q and "organizations" in q:
            return httpx.Response(200, headers=h, json={"data": {"account": {"id": "acc_1", "email": "ram@example.com", "name": "Ram",
                                                                              "organizations": [{"id": "org_1", "name": "HuhByte"}]}}})
        if "channels(" in q:
            return httpx.Response(200, headers=h, json={"data": {"channels": CHANNELS}})
        if "createPost" in q:
            inp = v["input"]
            self.created.append(inp)
            st = "draft" if inp.get("saveToDraft") else "scheduled"
            return httpx.Response(200, headers=h, json={"data": {"createPost": {"post": {
                "id": f"p{len(self.created)}", "text": inp["text"], "status": st, "dueAt": inp.get("dueAt"), "channelId": inp["channelId"]}}}})
        if "createIdea" in q:
            self.ideas.append(v["input"])
            return httpx.Response(200, headers=h, json={"data": {"createIdea": {"id": "idea1", "content": v["input"]["content"]}}})
        if "posts(" in q:
            return httpx.Response(200, headers=h, json={"data": {"posts": {"edges": [{"node": {"id": "p9", "text": "hi", "status": "scheduled",
                                                                                               "dueAt": "2026-11-01T09:00:00Z", "channelId": "ch_x"}}]}}})
        return httpx.Response(200, headers=h, json={"errors": [{"message": "unknown"}]})


@pytest.fixture
def fake():
    return FakeBuffer()


@pytest.fixture
def app(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(drafter, "fetch_article", lambda url, *a, **k: dict(ARTICLE, url=url))
    db = os.environ.get("STUDIO_TEST_PG") or str(tmp_path / "t.db")
    if db.startswith("postgres"):
        import psycopg
        with psycopg.connect(db, autocommit=True) as c:
            c.execute("DROP TABLE IF EXISTS users, sessions, agent_tokens, confirms, approvals, audit")
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="s" * 64,
                      db_path=db, secure_cookies=False, ai_base_url="")
    return create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))


@pytest.fixture
def client(app):
    return TestClient(app, base_url="https://hq.test")


def connect(client) -> str:
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 302
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"] == "/?connected=1"
    return client.get("/api/session").json()["csrf"]


@pytest.fixture
def human(client):
    csrf = connect(client)
    client.headers["X-HQ-CSRF"] = csrf
    return client


def agent_token(human, policy="review", cap=10):
    return human.post("/api/agent-tokens", json={"name": "Claude", "policy": policy, "daily_cap": cap}).json()["token"]


def mcp_call(client, token, name, args=None, mid=1):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {token}"},
                    json={"jsonrpc": "2.0", "id": mid, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return res, (json.loads(res["content"][0]["text"]) if not res["isError"] else res["content"][0]["text"])
