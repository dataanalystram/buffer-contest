"""The free demo is view-only (studio/demo_view.py): canned AI runs, zero outbound calls, and every write or destructive
action answers "In the real app this would ... The demo doesn't change anything." without changing data.

Two demo sessions: A tries to delete, wipe, send, schedule, connect, change settings and create tokens; B and the server's
data are unchanged, and nothing reached Buffer, a model, the video worker or any other host."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import demo_view
from studio.app import create_app
from studio.config import load_settings

ENDS = "The demo doesn't change anything."


@pytest.fixture
def world(tmp_path, monkeypatch):
    """The app with every outbound host recorded (Buffer, models, worker dispatch, research, context.dev...)."""
    monkeypatch.delenv("HQ_DEMO_VIEW_ONLY", raising=False)
    for k in ("ANTHROPIC_API_KEY", "CONTEXT_DEV_API_KEY", "FIRECRAWL_API_KEY", "GH_DISPATCH_TOKEN", "INSTABOX_API_KEY"):
        monkeypatch.setenv(k, "test-key-not-real")  # if the demo could spend, these would make it try
    calls = []

    def record(req: httpx.Request):
        calls.append(f"{req.method} {req.url.host}{req.url.path}")
        return httpx.Response(503, json={})
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="v" * 64,
                      db_path=str(tmp_path / "v.db"), secure_cookies=False, ai_base_url="https://models.example/v1",
                      extra={"sync_agent": True})
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(record)))
    before = set(demo_view.refused())
    yield app, calls, before


def _demo(app):
    c = TestClient(app, base_url="https://hq.test")
    assert c.get("/demo", follow_redirects=False).status_code == 302
    s = c.get("/api/session").json()
    assert s["demo"] is True
    c.headers["X-HQ-CSRF"] = s["csrf"]
    return c


def _snapshot(app, uid):
    st = app.state.studio.store
    out = {}
    for t, col in (("agent_tokens", "user_id"), ("approvals", "user_id"), ("brand_kits", "user_id"), ("media", "user_id"),
                   ("feeds", "user_id"), ("style_rules", "user_id"), ("ai_settings", "user_id")):
        try:
            out[t] = st.q(f"SELECT COUNT(*) AS n FROM {t} WHERE {col}=?", (uid,))[0]["n"]
        except Exception:  # noqa: BLE001  (a table this build doesn't have)
            out[t] = None
    out["users"] = st.q("SELECT COUNT(*) AS n FROM users")[0]["n"]
    return out


def _uid(app, c):
    return app.state.studio.store.session(c.cookies.get("hq_sid"))["user_id"]


WRITES = [  # (method, path, body): delete, wipe, send, schedule, connect, settings, tokens, uploads
    ("POST", "/api/agent-tokens", {"name": "x", "policy": "review"}),
    ("DELETE", "/api/agent-tokens/1", None),
    ("POST", "/api/disconnect", {}),
    ("POST", "/api/posts/submit", {"confirm_token": "x"}),
    ("DELETE", "/api/posts/p1", None),
    ("PATCH", "/api/posts/p1", {"text": "x"}),
    ("POST", "/api/posts/p1/reschedule", {"due_at": "2030-01-01T09:00:00Z"}),
    ("POST", "/api/approvals/1/approve", {}),
    ("POST", "/api/approvals/1/reject", {}),
    ("POST", "/api/agent/runs/1/packages/demo_x/schedule", {}),
    ("POST", "/api/agent/runs/1/tools/save_buffer_drafts", {}),
    ("POST", "/api/agent/runs/1/tools/edit_brand", {"instruction": "use #000"}),
    ("POST", "/api/brand-kits", {"name": "x"}),
    ("DELETE", "/api/brand-kits/1", None),
    ("POST", "/api/brand/from-website", {"url": "https://example.com"}),
    ("POST", "/api/ai/keys/anthropic", {"api_key": "sk-ant-x"}),
    ("PUT", "/api/ai/engine", {"engine": "claude"}),
    ("POST", "/api/integrations/producthunt", {"key": "x"}),
    ("POST", "/api/ledger", {"rule": "x"}),
    ("DELETE", "/api/library/1", None),
    ("POST", "/api/media", {"name": "a.jpg", "data": "AAAA"}),
    ("POST", "/api/uploads", {"name": "a.mp4", "size": 10}),
    ("POST", "/api/radar/feeds", {"url": "https://example.com/feed"}),
    ("POST", "/api/safety/kill", {"on": True}),
    ("POST", "/api/device-link", {}),
    ("POST", "/api/ideas", {"title": "x"}),
    ("POST", "/api/takes", {"url": "https://youtu.be/x"}),
    ("POST", "/api/sources/refresh", {}),
    ("POST", "/api/brand-kits", {"sample": "notion"}),
    ("POST", "/api/brand/samples/linear/schedule", {}),
    ("PATCH", "/api/takes/1/clips/c2", {"caption": "x"}),
    ("PUT", "/api/prefs/appearance", {"mode": "dark"}),
    ("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
]


def test_every_write_is_refused_with_the_real_app_line(world):
    app, calls, _ = world
    a = _demo(app)
    for m, path, body in WRITES:
        r = a.request(m, path, json=body) if body is not None else a.request(m, path)
        j = r.json()
        assert r.status_code == 403 and j.get("view_only") is True, (m, path, r.status_code, j)
        assert j["error"].startswith("In the real app this would ") and j["error"].endswith(ENDS), (m, path, j)
    assert calls == []


def test_two_sessions_isolated_and_nothing_changes(world):
    app, calls, before = world
    a, b = _demo(app), _demo(app)
    ua, ub = _uid(app, a), _uid(app, b)
    assert ua != ub and ua.startswith("demo_") and ub.startswith("demo_")
    snap_a, snap_b = _snapshot(app, ua), _snapshot(app, ub)
    queue_b = b.get("/api/queue").json()
    appr_b = b.get("/api/approvals").json()
    # A: delete, wipe, send, schedule
    [appr] = a.get("/api/approvals").json()
    assert a.post(f"/api/approvals/{appr['id']}/approve", json={}).status_code == 403
    for p in a.get("/api/queue").json():
        assert a.delete(f"/api/posts/{p['id']}").status_code == 403
    assert a.post("/api/disconnect", json={}).status_code == 403
    pv = a.post("/api/posts/preview", json={"channel_id": "demo_bsky", "text": "hello", "mode": "draft"})
    assert pv.status_code == 200  # a dry run is fine
    assert a.post("/api/posts/submit", json={"confirm_token": pv.json()["confirm_token"]}).status_code == 403
    # nothing changed: not A's data, not B's, not the server's
    assert _snapshot(app, ua) == snap_a and _snapshot(app, ub) == snap_b
    assert a.get("/api/session").json()["connected"] is True  # the "wipe" didn't sign A out or delete A
    assert b.get("/api/queue").json() == queue_b and b.get("/api/approvals").json() == appr_b
    assert len(a.get("/api/approvals").json()) == 1
    assert calls == [] and set(demo_view.refused()) == before  # zero outbound, not even attempted


def test_ai_runs_are_canned_and_spend_nothing(world):
    app, calls, before = world
    a = _demo(app)
    uid = _uid(app, a)
    left = a.get("/api/session").json()["demo_left"]
    assert left["ai"] == 0 and left["research"] == 0 and left["video"] == 0 and left["import"] == 0
    # Make a post from a prompt: runs to the end on the bundled path
    r = a.post("/api/agent/runs", json={"prompt": "Post about the new open model launch", "channel_ids": ["demo_bsky"]})
    assert r.status_code == 200, r.text
    # One take: the precomputed sample
    t = a.post("/api/takes/sample", json={"channel_ids": ["demo_ig"]})
    assert t.status_code in (200, 400), t.text
    # Write from a link: the sample story, never fetched
    d = a.post("/api/draft", json={"url": "https://news.example.com/x", "channel_ids": ["demo_bsky"]})
    assert d.status_code in (200, 429), d.text
    st = app.state.studio.store
    rows = st.q("SELECT detail FROM audit WHERE user_id=? AND action='ai_call'", (uid,))
    real = [r for r in rows if not json.loads(r["detail"] or "{}").get("sample")]  # the seeded sample Activity rows aside
    assert real == []  # no model call at all
    assert set(demo_view.refused()) == before  # and nothing even tried to call out
    assert calls == []


def test_view_only_can_be_switched_off(world, monkeypatch):
    app, _, _ = world
    monkeypatch.setenv("HQ_DEMO_VIEW_ONLY", "0")
    assert demo_view.blocked("DELETE", "/api/posts/x") is None
    monkeypatch.delenv("HQ_DEMO_VIEW_ONLY")
    assert demo_view.blocked("DELETE", "/api/posts/x").endswith(ENDS)


def test_real_accounts_are_untouched(human, fake):
    """The guard only ever applies to demo users: a connected account still sends, deletes and makes tokens."""
    r = human.post("/api/agent-tokens", json={"name": "Claude", "policy": "review", "daily_cap": 5})
    assert r.status_code == 200 and r.json()["token"]
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hello world", "mode": "draft"}).json()
    assert human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}).status_code == 200
    assert fake.created


def test_outbound_guard_blocks_any_client_while_offline():
    demo_view.install()
    tok = demo_view.OFFLINE.set(True)
    try:
        with pytest.raises(httpx.ConnectError):
            httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))).get("https://api.anthropic.com/v1/x")
    finally:
        demo_view.OFFLINE.reset(tok)
    assert httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))).get("https://x.test/").status_code == 200
    assert "https://api.anthropic.com/v1/x" in demo_view.refused()
    json.dumps(demo_view.refused())


def test_the_demo_still_shows_everything_without_calling_out(world):
    """View-only, not empty: the sample runs, One take's plan, the brand kits and the template previews all still work,
    and none of them reaches Buffer, a model, the worker or any other host."""
    app, calls, _ = world
    a = _demo(app)
    kits = a.get("/api/brand-kits").json()["kits"]
    assert [k["name"] for k in kits] == ["Nothing", "HuhByte AI"]
    assert a.post(f"/api/brand-kits/{kits[1]['id']}/activate", json={}).status_code == 200  # switching sample kits is viewing
    assert a.get("/api/brand-studio").json()["kit"]["name"] == "HuhByte AI"
    assert a.post("/api/brand-studio/examples/buffer").json()["kit"]["name"] == "Buffer"
    assert a.post("/api/brand/extract", json={"url": "https://www.notion.com"}).json()["source"] == "sample"
    live = a.post("/api/brand/extract", json={"url": "https://acme.example"})
    assert live.status_code >= 400 and "no outbound calls" in live.json()["error"]
    pv = a.post("/api/brand-studio/preview", json={"template": "launch", "service": "instagram", "width": 200, "max": 1}).json()
    assert pv["slides"][0].startswith("data:image/")
    reel = a.post("/api/templates/render", json={"template": "weekly_recap", "services": ["instagram"], "video": True}).json()
    assert reel["video"]["example"] is True
    t = a.post("/api/takes/sample", json={}).json()
    assert t["status"] == "ready" and len(t["clips"]) == 5
    plan = a.post(f"/api/takes/{t['id']}/place", json={"dry_run": True}).json()
    assert plan["dry_run"] and len(plan["plan"]["placements"]) == 5
    scan = a.post("/api/sources/scan", json={}).json()
    assert scan["sample"] is True
    assert a.post("/api/sources/draft", json={"run_id": scan["run_id"]}).json()["packages"]
    run = a.post("/api/agent/runs", json={"query": "AI agents", "format": "carousel"}).json()
    assert run["status"] == "ready", run.get("steps")
    assert a.get("/api/calendar").status_code == 200 and a.get("/api/inbox").status_code == 200
    assert calls == []
