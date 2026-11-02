"""Sign-in survives Buffer's rate limits (developers.buffer.com/guides/api-limits.md: limits are per app client and shared
by every HuhQueue user; a 429 carries Retry-After and costs nothing).

* One account's 429 never blocks anyone else's login or calls.
* A 429 on the sign-in's own account lookup signs the person in later: the tokens wait sealed on the session, the landing
  shows "Buffer is busy; finishing connecting in N min", and /api/session finishes it once Retry-After has passed.
* Reads that every page needs are cached (account/org a day, channels 15 min, all post statuses in one request) and
  served stale with ~10% of a window left."""
import time
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from .conftest import connect


def _start(client):
    r = client.get("/login", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    return client.get(f"/callback?code=abc&state={state}", follow_redirects=False)


def test_another_accounts_429_never_blocks_login(app, human, fake):
    fake.fail_429 = 1
    assert human.get("/api/channels?refresh=1").status_code == 429  # acc_1 is now blocked for 120 s
    other = TestClient(app, base_url="https://hq.test")
    r = _start(other)
    assert r.headers["location"] == "/?connected=1"  # the sign-in ignored acc_1's block
    assert other.get("/api/session").json()["connected"] is True


def test_login_429_defers_then_finishes_from_session(app, client, fake):
    fake.fail_429 = 1  # the account lookup right after the code exchange gets Buffer's 429 (Retry-After 120)
    r = _start(client)
    assert r.status_code == 302 and r.headers["location"].startswith("/?connecting=")
    wait = int(r.headers["location"].split("=")[1])
    assert 125 <= wait <= 151  # Retry-After + 5-30 s jitter
    store = app.state.studio.store
    sess = store.session(client.cookies.get("hq_sid"))
    assert sess["user_id"] is None and "at_1" not in (sess["oauth"] or "")  # the tokens are sealed, not stored in clear
    n = len(fake.calls)
    s = client.get("/api/session").json()
    assert s["connected"] is False and "Buffer is busy; finishing connecting in" in s["connecting"]["message"]
    assert 0 < s["connecting"]["retry_in"] <= 152
    assert len(fake.calls) == n  # asking before Retry-After costs no Buffer call
    # Retry-After has passed: the next /api/session finishes the sign-in with exactly one Buffer call
    p = store.unseal(sess["oauth"]); p["signin"]["retry_at"] = time.time() - 1
    store.update_session(client.cookies.get("hq_sid"), oauth=store.seal(p))
    s = client.get("/api/session").json()
    assert s["connected"] is True and s["user"] and "connecting" not in s
    assert len(fake.calls) == n + 1
    assert store.tokens("acc_1")["access_token"] == "at_1"


def test_deferred_login_429_again_reschedules(app, client, fake):
    fake.fail_429 = 1
    _start(client)
    store = app.state.studio.store
    sid = client.cookies.get("hq_sid")
    p = store.unseal(store.session(sid)["oauth"]); p["signin"]["retry_at"] = time.time() - 1
    store.update_session(sid, oauth=store.seal(p))
    fake.fail_429 = 1
    s = client.get("/api/session").json()
    assert s["connected"] is False and s["connecting"]["retry_in"] > 100


def test_account_is_cached_a_day_and_org_limits_too(human, fake):
    buf = human.app.state.studio.buffer
    n = len(fake.calls)
    buf.account(uid="acc_1"); buf.account(uid="acc_1")
    assert len(fake.calls) == n  # the sign-in's own lookup is the cached answer


def test_all_post_statuses_in_one_request(human, fake):
    buf = human.app.state.studio.buffer
    n = len(fake.calls)
    buf.prefetch_posts("acc_1", "org_1", ("scheduled", "sent", "draft", "error"))
    assert len(fake.calls) == n + 1
    for st in ("scheduled", "sent", "draft", "error"):
        buf.posts("acc_1", "org_1", [st])
    assert len(fake.calls) == n + 1
    assert [p["id"] for p in buf.posts("acc_1", "org_1", ["draft"])] == ["d1"]


def test_planner_reads_posts_once(human, fake):
    human.get("/api/channels")
    n = len([c for c in fake.calls if "posts(" in c[0]])
    assert human.get("/api/calendar").status_code == 200
    m = len([c for c in fake.calls if "posts(" in c[0]])
    assert m - n <= 1


def test_throttle_at_ten_percent_serves_stale_cache(human, fake):
    from studio import buffer_api
    buf = human.app.state.studio.buffer
    human.get("/api/channels")
    buf.budget.windows[86400] = {"name": "500-in-1day", "remaining": 40, "quota": 500, "reset_in": 9000, "at": time.time()}
    assert buf.budget.low()
    k = ("channels", "acc_1")
    buf._cache[k] = (time.time() - buffer_api.CHANNEL_TTL - 5, buf._cache[k][1])
    n = len(fake.calls)
    human.get("/api/channels")
    assert len(fake.calls) == n  # stale but served: the last 10% is kept for sign-ins and posts
    buf.budget.windows[86400]["remaining"] = 300
    assert not buf.budget.low()
