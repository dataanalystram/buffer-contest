"""The free sample's daily caps (studio/demo_guard.py): per workspace and per IP, real accounts never capped, every capped
feature falls back to sample data, and the counters roll over with the UTC day."""
import pytest
from fastapi.testclient import TestClient

from studio import demo, demo_guard
from studio.store import Store

UID, UID2 = demo_guard.PREFIX + "ws1", demo_guard.PREFIX + "ws2"


@pytest.fixture
def store(tmp_path):
    st = Store(str(tmp_path / "g.db"), "k" * 64)
    demo_guard.ensure(st)
    return st


def test_real_accounts_are_never_capped(store):
    for _ in range(50):
        assert demo_guard.take(store, "acc_1", "research") is True
        assert demo_guard.allowed(store, "acc_1", "ai") is True
    demo_guard.spend(store, "acc_1", "ai")  # a no-op for real accounts
    assert store.q("SELECT COUNT(*) AS n FROM demo_usage")[0]["n"] == 0
    assert demo_guard.take(store, None, "video") is True


def test_workspace_cap_stops_at_its_limit_and_audits_the_refusal(store):
    demo_guard.register(store, UID, "1.2.3.4")
    per_ws = demo_guard.CAPS["research"][0]
    assert [demo_guard.take(store, UID, "research") for _ in range(per_ws)] == [True] * per_ws
    assert demo_guard.take(store, UID, "research") is False
    assert demo_guard.allowed(store, UID, "research") is False
    assert any(r["action"] == "demo_cap" for r in store.audit_rows(UID))
    assert demo_guard.left(store, UID)["research"] == 0
    assert demo_guard.take(store, UID, "import") is True  # each kind has its own allowance


def test_ip_cap_is_shared_by_every_workspace_from_that_ip(store):
    per_ws, per_ip = demo_guard.CAPS["video"]
    uids = [f"{demo_guard.PREFIX}v{i}" for i in range(per_ip + 1)]
    for u in uids:
        demo_guard.register(store, u, "9.9.9.9")
    for u in uids[:per_ip]:
        assert [demo_guard.take(store, u, "video") for _ in range(per_ws)] == [True] * per_ws
    assert demo_guard.take(store, uids[-1], "video") is False  # a fresh workspace, but its IP is spent
    demo_guard.register(store, UID2, "8.8.8.8")
    assert demo_guard.take(store, UID2, "video") is True  # another network is not


def test_ip_is_stored_hashed_never_raw(store):
    demo_guard.register(store, UID, "203.0.113.7")
    rows = store.q("SELECT ip_hash FROM demo_owners WHERE user_id=?", (UID,))
    assert rows and "203.0.113.7" not in rows[0]["ip_hash"]


def test_spend_counts_after_allowed_and_left_reports_the_smaller_allowance(store):
    demo_guard.register(store, UID, "1.1.1.1")
    per_ws = demo_guard.CAPS["ai"][0]
    for _ in range(per_ws - 2):
        assert demo_guard.allowed(store, UID, "ai")
        demo_guard.spend(store, UID, "ai")
    left = demo_guard.left(store, UID)
    assert left["ai"] == 2
    assert set(left) == set(demo_guard.CAPS) - {"workspace"}


def test_unregistered_workspace_still_has_its_own_cap(store):
    per_ws = demo_guard.CAPS["feed"][0]
    assert [demo_guard.take(store, UID, "feed") for _ in range(per_ws + 1)] == [True] * per_ws + [False]


def test_counters_roll_over_with_the_utc_day(store, monkeypatch):
    demo_guard.register(store, UID, "1.1.1.1")
    monkeypatch.setattr(demo_guard, "_day", lambda: "2026-10-31")
    for _ in range(demo_guard.CAPS["video"][0]):
        demo_guard.take(store, UID, "video")
    assert demo_guard.take(store, UID, "video") is False
    monkeypatch.setattr(demo_guard, "_day", lambda: "2026-11-01")
    assert demo_guard.take(store, UID, "video") is True


def test_workspace_ok_caps_new_sample_workspaces_per_ip(store):
    cap = demo_guard.CAPS["workspace"][1]
    assert all(demo_guard.workspace_ok(store, "5.5.5.5") for _ in range(cap))
    assert demo_guard.workspace_ok(store, "5.5.5.5") is False
    assert demo_guard.workspace_ok(store, "6.6.6.6") is True


def test_client_ip_prefers_the_first_forwarded_hop():
    class R:
        def __init__(self, h, host="10.0.0.1"):
            self.headers, self.client = h, type("C", (), {"host": host})()
    assert demo_guard.client_ip(R({"x-forwarded-for": "198.51.100.4, 10.1.1.1"})) == "198.51.100.4"
    assert demo_guard.client_ip(R({})) == "10.0.0.1"
    r = R({}); r.client = None
    assert demo_guard.client_ip(r) == "unknown"
    # behind Cloudflare (Render) the edge's header wins: a typed X-Forwarded-For can't pick the bucket
    assert demo_guard.client_ip(R({"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "1.2.3.4, 203.0.113.9"})) == "203.0.113.9"
    assert demo_guard.client_ip(R({"true-client-ip": "203.0.113.10", "x-forwarded-for": "1.2.3.4"})) == "203.0.113.10"


def test_every_cap_has_a_plain_reason():
    assert set(demo_guard.WHY) == set(demo_guard.CAPS)
    assert all(w.endswith(".") and len(w) < 140 for w in demo_guard.WHY.values())


# ---------------------------------------------------------------- the fallbacks, through the app

def _demo(app):
    c = TestClient(app, base_url="https://hq.test")
    assert c.get("/demo", follow_redirects=False).headers["location"] == "/?demo=1"
    s = c.get("/api/session").json()
    c.headers["X-HQ-CSRF"] = s["csrf"]
    return c, s


def test_session_reports_what_the_sample_has_left(app):
    _, s = _demo(app)
    assert s["demo"] is True
    assert s["demo_left"]["research"] == demo_guard.CAPS["research"][0]


def test_too_many_sample_workspaces_from_one_ip_redirects_with_the_reason(app, monkeypatch):
    monkeypatch.setitem(demo_guard.CAPS, "workspace", (None, 1))
    _demo(app)
    r = TestClient(app, base_url="https://hq.test").get("/demo", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/?error=")


def test_feed_cap_answers_429_with_the_reason(app, monkeypatch):
    monkeypatch.setitem(demo_guard.CAPS, "feed", (0, 0))
    c, _ = _demo(app)
    r = c.post("/api/radar/feeds", json={"url": "https://news.example.com/feed.xml"})
    assert r.status_code == 429, r.text
    assert "free sample" in r.text


def test_research_cap_falls_back_to_the_sample_story(app, fake, monkeypatch):
    monkeypatch.setitem(demo_guard.CAPS, "research", (0, 0))
    c, _ = _demo(app)
    r = c.post("/api/draft", json={"url": "https://news.example.com/ai-launch"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["article"]["title"] == demo.SAMPLE_ARTICLE["title"]
    assert "sample" in body
    assert fake.calls == []  # nothing reaches Buffer from the sample


def test_sample_never_starts_the_github_worker(app, monkeypatch):
    """SECURITY_RULES 3: /demo and anonymous traffic never run the worker, whatever the server's worker mode."""
    monkeypatch.setenv("GH_DISPATCH_TOKEN", "x")
    _demo(app)
    h = app.state.house
    st = app.state.store
    uid = st.q("SELECT id FROM users WHERE id LIKE ?", (demo_guard.PREFIX + "%",))[0]["id"]
    dispatched = []

    class NoHttp:
        def post(self, *a, **k):
            dispatched.append(a); raise AssertionError("the sample reached GitHub")
    monkeypatch.setattr(h, "http", NoHttp(), raising=False)
    jid = st.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
               (uid, 0, "{}", "queued", 0, 0))
    ok, why = h._dispatch_github(jid)
    assert ok is False and "sample" in why and dispatched == []
    from studio.actions import Actor
    h.worker_mode = "github"
    res = {"media": [], "story": {"headline": "x"}}
    from studio import brand
    out = h._queue_video(Actor(uid), 0, {k: "" for k in brand.DEFAULT_KIT}, {}, res, {})
    assert out["status"] == "done" and res["sample"] == demo_guard.WHY["video"] and dispatched == []
    assert demo_guard.left(st, uid)["video"] == demo_guard.CAPS["video"][0]  # a refused worker run doesn't spend the cap


def test_sample_x_limit_counts_only_the_24_hours_around_the_post():
    """The sample X channel allows 3 posts in 24 hours around the intended time, like Buffer; a post days out is fine."""
    from datetime import datetime, timedelta, timezone
    b = demo.DemoBuffer()
    uid = "demo_limit"
    now = datetime.now(timezone.utc)
    for k in range(3):
        b._seed(uid).append({"id": f"lim{k}", "text": "x", "status": "scheduled", "channelId": "demo_x",
                             "dueAt": (now + timedelta(hours=1 + k)).isoformat(), "assets": []})
    assert b.daily_limits(uid, ["demo_x"])[0]["isAtLimit"]
    later = (now + timedelta(days=4)).isoformat()
    st = b.daily_limits(uid, ["demo_x"], later)[0]
    assert not st["isAtLimit"] and st["scheduled"] < 3
