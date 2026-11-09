import json
import os
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import drafter

# The per-IP rate limit for anonymous and sample traffic (SECURITY_RULES 3) is tested on its own in test_security_audit;
# every other test talks to the app from one TestClient "IP", so lift it here.
os.environ.setdefault("HQ_ANON_RPM", "1000000")
os.environ.setdefault("HQ_STARTER_FEEDS", "0")
os.environ.setdefault("HQ_DEFAULT_KIT", "off")  # fresh workspaces keep the plain default kit; test_default_buffer_kit turns it on  # no live feed reads in tests; test_starter_feeds turns it on with fetch mocked
os.environ.setdefault("HQ_AUTH_PER_MIN", "1000000")
os.environ.setdefault("HQ_DEMO_PER_MIN", "1000000")
import tempfile as _tempfile  # noqa: E402
os.environ.setdefault("HUHQ_PREVIEW_WARM", "0")  # no start-up warm-up thread in tests (test_brand_kit_builder warms explicitly)
os.environ.setdefault("HUHQ_PREVIEW_CACHE", _tempfile.mkdtemp(prefix="hq-previews-"))
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
def image_bytes(w: int, h: int, fmt: str = "PNG", color=(240, 90, 30)) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, fmt)
    return buf.getvalue()


RL = '"100-in-15min";r=97;t=880, "500-in-1day";r=490;t=86000'
RLP = '"100-in-15min";q=100;w=900, "500-in-1day";q=500;w=86400'


class FakeBuffer:
    def __init__(self):
        self.calls, self.token_calls, self.created, self.ideas, self.edits, self.deleted = [], [], [], [], [], []
        self.posts = [{"id": "p9", "text": "hi", "status": "scheduled", "dueAt": "2026-11-01T09:00:00.000Z", "channelId": "ch_x"},
                      {"id": "d1", "text": "a draft", "status": "draft", "dueAt": None, "channelId": "ch_li"},
                      {"id": "s1", "text": "gone out", "status": "sent", "dueAt": "2026-10-20T09:00:00.000Z", "channelId": "ch_x"}]
        self.n = 0
        self.refresh_tokens = {"rt_1"}
        self.fail_429 = 0
        self.token_fail = None  # refresh grant: an HTTP status (e.g. 503) or "timeout"; tokens must survive both
        self.gql_error = None   # e.g. {"message": ..., "extensions": {"code": "INSUFFICIENT_SCOPE"}}
        self.daily = None  # e.g. {"limit": 3, "scheduled": 3, "sent": 0, "isAtLimit": True}
        self.plan = None   # e.g. {"scheduledPosts": 10, "channels": 3}
        # media the preflight "downloads" (url -> bytes); anything else counts as unreachable
        self.reject_message, self.rejected = "", []  # Buffer's typed MutationError: HTTP 200 with createPost.message
        self.media = {"https://cdn.example.com/a.png": image_bytes(1200, 627),
                      "https://news.example.com/hero.jpg": image_bytes(1200, 630, "JPEG")}

    def load_media(self, url: str):
        return self.media.get(url)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        h = {"RateLimit": RL, "RateLimit-Policy": RLP}
        if request.url.host == "auth.buffer.com":
            form = parse_qs(request.content.decode())
            self.token_calls.append(form)
            if form["grant_type"] == ["authorization_code"]:
                assert form["code_verifier"][0] and form["client_secret"] == ["sec"]
                return httpx.Response(200, json={"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600,
                                                 "scope": "account:read posts:write offline_access"})
            if self.token_fail == "timeout":
                raise httpx.ConnectTimeout("timed out", request=request)
            if isinstance(self.token_fail, tuple):  # (status, json or raw text): Buffer's answer to the refresh grant
                st, bd = self.token_fail
                return httpx.Response(st, text=bd) if isinstance(bd, str) else httpx.Response(st, json=bd)
            if self.token_fail:
                return httpx.Response(self.token_fail, headers={"Retry-After": "7"}, json={"error": "temporarily_unavailable"})
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
        if self.gql_error:
            return httpx.Response(200, headers=h, json={"errors": [self.gql_error]})
        if self.fail_429:
            self.fail_429 -= 1
            return httpx.Response(429, headers={**h, "Retry-After": "120"}, json={})
        if "dailyPostingLimits" in q:
            if self.daily is None:
                return httpx.Response(200, headers=h, json={"errors": [{"message": "Cannot query field"}]})
            return httpx.Response(200, headers=h, json={"data": {"dailyPostingLimits": [
                dict(self.daily, channelId=c) for c in v["i"]["channelIds"]]}})
        if "limits {" in q:
            if self.plan is None:
                return httpx.Response(200, headers=h, json={"errors": [{"message": "Cannot query field"}]})
            return httpx.Response(200, headers=h, json={"data": {"account": {"organizations": [
                {"id": "org_1", "channelCount": 3, "limits": self.plan}]}}})
        if "account" in q and "organizations" in q:
            return httpx.Response(200, headers=h, json={"data": {"account": {"id": "acc_1", "email": "ram@example.com", "name": "Ram",
                                                                              "organizations": [{"id": "org_1", "name": "HuhByte"}]}}})
        if "postingSchedule" in q:
            return httpx.Response(200, headers=h, json={"data": {"channels": [
                {"id": "ch_x", "timezone": "Europe/Dublin", "postingSchedule": [{"day": "mon", "times": ["09:00", "17:00"], "paused": False}]}]}})
        if "channels(" in q:
            return httpx.Response(200, headers=h, json={"data": {"channels": CHANNELS}})
        if "createPost" in q and self.reject_message:
            self.rejected.append(v["input"])
            return httpx.Response(200, headers=h, json={"data": {"createPost": {"message": self.reject_message}}})
        if "createPost" in q:
            inp = v["input"]
            self.created.append(inp)
            st = "draft" if inp.get("saveToDraft") else "scheduled"
            return httpx.Response(200, headers=h, json={"data": {"createPost": {"post": {
                "id": f"p{len(self.created)}", "text": inp["text"], "status": st, "dueAt": inp.get("dueAt"), "channelId": inp["channelId"]}}}})
        if "createIdea" in q:
            self.ideas.append(v["input"])
            return httpx.Response(200, headers=h, json={"data": {"createIdea": {"id": "idea1", "content": v["input"]["content"]}}})
        if "editPost" in q:
            self.edits.append(v["input"])
            node = next((n for n in self.posts if n["id"] == v["input"]["id"]), None)
            if not node:
                return httpx.Response(200, headers=h, json={"data": {"editPost": {"message": "Post not found"}}})
            node.update({k: v["input"][k] for k in ("text", "dueAt") if k in v["input"]})
            return httpx.Response(200, headers=h, json={"data": {"editPost": {"post": node}}})
        if "deletePost" in q:
            self.deleted.append(v["input"]["id"])
            self.posts = [n for n in self.posts if n["id"] != v["input"]["id"]]
            return httpx.Response(200, headers=h, json={"data": {"deletePost": {"id": v["input"]["id"]}}})
        if "posts(" in q:
            want = set(v["i"]["filter"]["status"])
            return httpx.Response(200, headers=h, json={"data": {"posts": {"edges": [{"node": n} for n in self.posts if n["status"] in want]}}})
        return httpx.Response(200, headers=h, json={"errors": [{"message": "unknown"}]})


@pytest.fixture(autouse=True)
def _no_free_engine_keys(monkeypatch):
    """The free AI engines (studio/free_engines.py) read their keys from the environment: tests start with none set, no
    engine marked down and no Oracle check result, so nothing ever reaches a real model."""
    from studio import ai_engines
    for k in ("GOOGLE_AI_STUDIO_KEY", "NVIDIA_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    ai_engines.clear_down(); ai_engines.oracle_reset()
    yield
    ai_engines.clear_down(); ai_engines.oracle_reset()


@pytest.fixture(autouse=True)
def _fresh_app_keys():
    """App-level research keys cache tokens and results globally (studio/appkeys.py): start every test clean."""
    from studio import appkeys, xreader
    appkeys.reset(); xreader.reset()
    yield
    appkeys.reset(); xreader.reset()


@pytest.fixture(autouse=True)
def _fresh_egress(tmp_path, monkeypatch):
    """The egress meter (studio/egress.py) counts per process and the media disk cache (studio/mediacache.py) writes
    files: every test gets its own meter state file and cache directory, and starts at zero. The R2 free-tier counter
    (studio/r2usage.py) starts at zero too."""
    from studio import egress, r2usage
    monkeypatch.setenv("HQ_EGRESS_STATE", str(tmp_path / "egress.json"))
    monkeypatch.setenv("HQ_MEDIA_CACHE_DIR", str(tmp_path / "media-cache"))
    egress.reset()
    r2usage.reset()
    yield
    egress.reset()
    r2usage.reset()


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
            # every table the app creates (all of them, so a new table like brain_budget or a cache never leaks between tests)
            c.execute("DROP SCHEMA IF EXISTS public CASCADE")
            c.execute("CREATE SCHEMA public")
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="s" * 64,
                      db_path=db, secure_cookies=False, ai_base_url="")
    application = create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake)))
    application.state.studio.media_loader = fake.load_media
    from studio import preflight
    preflight._CACHE.clear()
    return application


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


@pytest.fixture(scope="session")
def chromium():
    """A Chromium shared by every Studio browser test (skips cleanly when none is installed)."""
    pw = pytest.importorskip("playwright.sync_api")
    from .browsers import launch_or_skip
    p, browser = launch_or_skip(pw.sync_playwright)
    yield browser
    browser.close()
    p.stop()


@pytest.fixture
def close_contexts(chromium):
    yield
    for c in list(chromium.contexts):
        c.close()


@pytest.fixture
def demo_live(tmp_path):
    """The app on a real port with no Buffer behind it (every Buffer call answers 503 and is recorded), for browser tests
    of the sample workspace at /demo. Yields (base_url, buffer_calls)."""
    from .browsers import free_port, serve
    calls = []

    def nobuffer(r):
        calls.append(str(r.url)); return httpx.Response(503)
    port = free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="d" * 64,
                      db_path=str(tmp_path / "demo.db"), secure_cookies=False, ai_base_url="")
    server = serve(create_app(s, http=httpx.Client(transport=httpx.MockTransport(nobuffer))), port)
    yield f"http://127.0.0.1:{port}", calls
    server.should_exit = True
