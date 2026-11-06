"""Contest P0: a Buffer token-endpoint blip never signs anyone out; a missing scope asks to reconnect."""
import time

import pytest


def _expire(app):
    st = app.state.store
    tok = st.tokens("acc_1")
    tok["expires_at"] = time.time() - 1
    st.save_tokens("acc_1", tok)


@pytest.mark.parametrize("fail", [500, 502, 503, 429, "timeout"])
def test_transient_refresh_failure_keeps_tokens_and_is_retryable(human, app, fake, fail):
    _expire(app)
    fake.token_fail = fail
    r = human.get("/api/channels?refresh=1")
    assert r.status_code == 503
    j = r.json()
    assert j["code"] == "buffer_busy" and j["retryable"] is True and "busy" in j["error"].lower()
    assert int(r.headers["Retry-After"]) >= 1
    tok = app.state.store.tokens("acc_1")
    assert tok and tok["refresh_token"] == "rt_1" and tok["access_token"] == "at_1"  # nothing wiped or spent
    assert human.get("/api/session").json()["connected"] is True
    # a second call inside the backoff doesn't spend another token request
    n = len(fake.token_calls)
    assert human.get("/api/channels?refresh=1").status_code == 503 and len(fake.token_calls) == n
    # Buffer recovers: the same refresh token works
    fake.token_fail = None
    app.state.buffer._busy.clear()
    assert human.get("/api/channels?refresh=1").status_code == 200
    assert app.state.store.tokens("acc_1")["refresh_token"] == "rt_2"


@pytest.mark.parametrize("status,body", [(400, {"error": "invalid_grant"}), (401, {"error": "invalid_client"}),
                                         (200, {"error": "invalid_grant"})])
def test_rejected_refresh_clears_tokens(human, app, fake, status, body):
    _expire(app)
    fake.token_fail = (status, body)
    r = human.get("/api/channels?refresh=1")
    assert r.status_code == 401 and "connect again" in r.json()["error"].lower()
    assert app.state.store.tokens("acc_1") is None


@pytest.mark.parametrize("status,body", [(400, "<html>Bad Request</html>"), (401, ""), (400, {"message": "no code"}),
                                         (503, {"error": "invalid_grant"}), (429, {"error": "invalid_grant"})])
def test_garbled_400_401_keeps_tokens(human, app, fake, status, body):
    _expire(app)
    fake.token_fail = (status, body)
    r = human.get("/api/channels?refresh=1")
    assert r.status_code == 503 and r.json()["code"] == "buffer_busy"
    assert app.state.store.tokens("acc_1")["refresh_token"] == "rt_1"


def test_insufficient_scope_asks_to_reconnect(human, fake):
    fake.gql_error = {"message": "Missing required scope: posts:read", "extensions": {"code": "INSUFFICIENT_SCOPE"}}
    r = human.get("/api/channels?refresh=1")
    assert r.status_code == 403
    j = r.json()
    assert j["code"] == "buffer_scope" and j["reconnect"] == "/login"
    assert j["error"] == "Reconnect Buffer to allow reading your posts and queue."


def test_unknown_scope_name_is_never_echoed(human, fake):
    fake.gql_error = {"message": "insufficient_scope <script>x</script> evil:write", "extensions": {"code": "FORBIDDEN"}}
    j = human.get("/api/channels?refresh=1").json()
    assert j["code"] == "buffer_scope" and j["error"] == "Reconnect Buffer to allow the access this needs."


def test_invalid_scope_on_callback_is_friendly(client):
    client.get("/login", follow_redirects=False)
    r = client.get("/callback?error=invalid_scope&error_description=bad", follow_redirects=False)
    loc = r.headers["location"]
    assert loc.startswith("/?error=") and "Reconnect%20Buffer" in loc
    assert client.get("/api/session").json()["connected"] is False
