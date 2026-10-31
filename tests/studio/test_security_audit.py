"""Regression tests for the 7 confirmed findings of the 31 Oct 2026 security audit (docs/SECURITY.md). Each test replays
the audit's proof locally (mock transports, a throwaway database, a self-made signing key) and checks it now fails.
Nothing here touches the network or production."""
import base64
import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import demo_guard, drafter, house as house_mod, limits, netguard, radar, sources, take, worker_auth

from .conftest import agent_token, mcp_call

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ("127.0.0.1", "10.0.0.5", "169.254.169.254", "100.100.100.200", "internal.test")


def _pin_dns(monkeypatch):
    """Hermetic DNS: the names below are 'public', the PRIVATE ones are not (same as a real resolver would say)."""
    monkeypatch.setattr(drafter, "_public", lambda h: h not in PRIVATE and netguard.public_host(h) if h[0].isdigit() or ":" in h
                        else h not in PRIVATE)


def _redirecting(internal_hits):
    def handler(req: httpx.Request):
        if req.url.host in PRIVATE:
            internal_hits.append(str(req.url))
            return httpx.Response(200, text="<title>INTERNAL SECRET</title><p>" + "secret " * 20 + "</p>")
        if req.url.path == "/hop":
            return httpx.Response(302, headers={"location": "https://public2.test/final"})
        if req.url.host == "public2.test":
            return httpx.Response(200, text="<title>Fine public page</title>", headers={"content-type": "text/html"})
        return httpx.Response(302, headers={"location": "http://127.0.0.1:9090/internal"})
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)  # even a client that would follow


# ------------------------------------------------------------------ 1. SSRF via redirect (HQ-SSRF-REDIRECT-REVALIDATION)
def test_1_every_fetch_helper_rechecks_each_redirect_hop(monkeypatch, tmp_path):
    _pin_dns(monkeypatch)
    hits = []
    c = _redirecting(hits)
    with pytest.raises(drafter.FetchError):
        drafter.fetch_article("https://public.test/", http=c)
    with pytest.raises(drafter.FetchError):
        radar.fetch("https://public.test/feed.xml", http=c)
    assert sources.resolve_google_link("https://news.google.com/rss/articles/x", http=c) == "https://news.google.com/rss/articles/x"

    class H:  # House._fetch_bytes and Takes._download on the same transport
        http = c
    assert house_mod.House._fetch_bytes(H(), "https://public.test/logo.png", 10_000) is None
    from studio.takes import Takes
    t = Takes.__new__(Takes)
    t.h = H()
    with pytest.raises(take.TakeError):
        t._download("https://public.test/talk.mp4", tmp_path / "v.mp4")
    assert hits == []  # nothing inside was ever asked for
    # a redirect to another public host still works
    r = netguard.safe_get("https://public.test/hop", http=c)
    assert r.status_code == 200 and str(r.url) == "https://public2.test/final"


def test_1_the_blocklist_covers_metadata_cgnat_and_mapped_addresses():
    for h in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "100.100.100.200",
              "0.0.0.0", "::1", "::ffff:127.0.0.1", "::ffff:169.254.169.254", "fd00:ec2::254", "fe80::1", "64:ff9b::a9fe:a9fe",
              "metadata.google.internal", "localhost"):
        assert netguard.public_host(h) is False, h
    assert netguard.public_host("8.8.8.8") and netguard.public_host("2606:4700:4700::1111")
    with pytest.raises(netguard.FetchError):
        netguard.check_url("https://user:pw@example.com/")


# ------------------------------------------------------------------ 2. job token replay (HQ-JOB-TOKEN-REPLAY)
def _job(h, kind="slideshow", clips=None):
    spec = {"kind": kind, "slides": [], "kit": {}, "params": {}, **({"clips": clips} if clips else {})}
    h.store.x("INSERT INTO users(id) VALUES(?) ON CONFLICT DO NOTHING", ("u_sec",))
    return h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                     ("u_sec", 0, json.dumps(spec), "queued", time.time(), time.time()))


def test_2_job_token_reads_once_posts_each_clip_once_and_dies_with_the_result(client):
    h = client.app.state.house
    jid = _job(h, "take_render", clips=[{"id": "c1"}, {"id": "c2"}])
    jt = h.issue_job_token(jid)
    assert h.job_token_ok(jid, jt, "read") and not h.job_token_ok(jid, jt, "read")  # the spec, once
    assert h.job_token_ok(jid, jt, "clip", "c1") and not h.job_token_ok(jid, jt, "clip", "c1")  # each clip, once
    assert not h.job_token_ok(jid, jt, "clip", "c9")  # only this job's clips
    h.store.x("UPDATE render_jobs SET status='done' WHERE id=?", (jid,))
    assert not h.job_token_ok(jid, jt, "write") and not h.job_token_ok(jid, jt, "clip", "c2")
    # over HTTP: the audit's replay (GET the spec twice) now fails the second time
    jid2 = _job(h)
    jt2 = h.issue_job_token(jid2)
    assert client.get(f"/api/worker/jobs/{jid2}", headers={"x-job-token": jt2}).status_code == 200
    assert client.get(f"/api/worker/jobs/{jid2}", headers={"x-job-token": jt2}).status_code == 403


def _rsa():
    from cryptography.hazmat.primitives.asymmetric import rsa
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    n = k.public_key().public_numbers()
    b = lambda i: base64.urlsafe_b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()  # noqa: E731
    return k, {"kid1": {"kty": "RSA", "kid": "kid1", "n": b(n.n), "e": b(n.e)}}


def _jwt(k, claims, kid="kid1"):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    e = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    si = f"{e({'alg': 'RS256', 'kid': kid, 'typ': 'JWT'})}.{e(claims)}"
    sig = base64.urlsafe_b64encode(k.sign(si.encode(), padding.PKCS1v15(), hashes.SHA256())).rstrip(b"=").decode()
    return f"{si}.{sig}"


def test_2_worker_claims_its_job_once_with_a_github_oidc_identity(client, monkeypatch):
    monkeypatch.setenv("GH_REPO", "owner/repo")
    monkeypatch.setenv("GH_REF", "main")
    h = client.app.state.house
    k, jwks = _rsa()
    now = time.time()
    good = {"iss": worker_auth.GITHUB_ISS, "aud": h.s.public_url, "exp": now + 300, "iat": now, "repository": "owner/repo",
            "event_name": "workflow_dispatch", "workflow_ref": "owner/repo/.github/workflows/render.yml@refs/heads/main"}
    jid = _job(h)
    h.open_claim(jid, 600, "render.yml")
    assert h.claim_job(jid, "not-a-jwt", jwks)[0] == 401
    for bad in ({"repository": "someone/else"}, {"aud": "https://evil.test"}, {"event_name": "push"},
                {"workflow_ref": "owner/repo/.github/workflows/studio-ci.yml@refs/heads/main"}, {"exp": now - 600}):
        assert h.claim_job(jid, _jwt(k, {**good, **bad}), jwks)[0] == 401, bad
    other, _ = _rsa()
    assert h.claim_job(jid, _jwt(other, good), jwks)[0] == 401  # signed by someone else
    code, body = h.claim_job(jid, _jwt(k, good), jwks)
    assert code == 200 and body["job_token"].startswith("jt_")
    assert h.claim_job(jid, _jwt(k, good), jwks)[0] == 409  # once
    assert h.job_token_ok(jid, body["job_token"], "read")


def test_2_dispatch_sends_no_token(client, monkeypatch):
    h = client.app.state.house
    sent = []

    def gh(req):
        sent.append(req.content.decode())
        return httpx.Response(204)
    monkeypatch.setattr(h, "http", httpx.Client(transport=httpx.MockTransport(gh)))
    monkeypatch.setattr(house_mod.House, "_worker_allowed", lambda self, uid: True)
    jid = _job(h)
    assert h._dispatch_github(jid)[0]
    body = json.loads(sent[0])
    assert set(body["inputs"]) == {"job_id", "app"} and "jt_" not in sent[0]


# ------------------------------------------------------------------ 3. token in the Actions log (HQ-JOB-TOKEN-IN-ACTIONS-LOG)
@pytest.mark.parametrize("wf", ["render.yml", "take.yml"])
def test_3_worker_workflows_carry_no_token_and_expand_no_input_in_a_command(wf):
    text = (ROOT / ".github" / "workflows" / wf).read_text()
    assert "job_token" not in text and "JOB_TOKEN" not in text and "add-mask" not in text
    assert "id-token: write" in text and "persist-credentials: false" in text
    for line in text.splitlines():
        if line.strip().startswith(("run:", "- run:")):
            assert "${{" not in line.split("run:", 1)[1], line  # inputs reach commands only through env


# ------------------------------------------------------------------ 4. OAuth name spoofing (HQ-OAUTH-CLIENT-NAME-SPOOF)
def _consent(client, cb, name="Claude"):
    import hashlib
    from urllib.parse import parse_qs, urlparse
    reg = client.post("/oauth/register", json={"client_name": name, "redirect_uris": [cb]}).json()
    verifier = "v" * 64
    ch = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": cb, "state": "s", "code_challenge": ch,
         "code_challenge_method": "S256"}
    page = client.get("/oauth/authorize", params=q).text
    form = {**{k: v for k, v in q.items() if k != "response_type"}, "csrf": client.get("/api/session").json()["csrf"],
            "policy": "trusted", "daily_cap": "5", "decision": "allow"}
    r = client.post("/oauth/authorize", data=form, follow_redirects=False)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                            "redirect_uri": cb, "code_verifier": verifier}).json()
    return page, tok


def test_4_consent_shows_the_redirect_host_and_an_unverified_claude_gets_no_trusted_token(human):
    page, tok = _consent(human, "https://attacker.example/cb")
    assert "attacker.example" in page and 'data-testid="oauth-unverified"' in page
    assert 'data-testid="oauth-policy-trusted"' not in page
    toks = human.get("/api/agent-tokens").json()
    mine = [t for t in toks if "attacker.example" in t["name"]]
    assert mine and mine[0]["policy"] == "review" and "unverified" in mine[0]["name"]  # the form's "trusted" was refused
    page2, _ = _consent(human, "https://claude.ai/api/mcp/auth_callback")
    assert 'data-testid="oauth-verified"' in page2 and 'data-testid="oauth-policy-trusted"' in page2 and "claude.ai" in page2


def test_4_registration_is_rate_limited(client):
    for _ in range(20):
        assert client.post("/oauth/register", json={"redirect_uris": ["https://a.example/cb"]}).status_code == 201
    assert client.post("/oauth/register", json={"redirect_uris": ["https://a.example/cb"]}).status_code == 429


# ------------------------------------------------------------------ 5. kill switch / cap gaps (HQ-AGENT-GUARD-GAPS)
def test_5_kill_switch_stops_every_agent_action_and_rule_toggles_are_human_only(human):
    from .test_studio import bot
    tok = agent_token(human, "review", cap=50)
    rid = human.post("/api/ledger", json={"correction": "never say game-changer"}).json()["added"][0]["id"]
    agent = bot(human)
    hdr = {"Authorization": f"Bearer {tok}"}
    assert agent.post(f"/api/ledger/{rid}", headers=hdr, json={"active": False}).status_code == 403  # human-only
    human.post("/api/safety/kill", json={"on": True})
    for name, args in (("clips_from_video", {"sample": True}), ("run_agent", {"query": "x"}), ("remember_rule", {"correction": "no emojis"}),
                       ("revise", {"run_id": 1, "correction": "x"}), ("adjust_clip", {"take_id": 1, "clip_id": "c1"}),
                       ("draft_from_link", {"url": "https://news.example.com/a"}), ("create_idea", {"title": "t"})):
        res, msg = mcp_call(human, tok, name, args)
        assert res["isError"] and "kill switch" in msg, (name, msg)
    assert agent.post("/api/agent/runs", headers=hdr, json={"query": "x"}).status_code == 423  # REST too
    human.post("/api/safety/kill", json={"on": False})
    assert any(r["id"] == rid and r["active"] for r in human.get("/api/brand").json()["rules"])


def test_5_daily_cap_counts_runs_takes_and_rules(human):
    tok = agent_token(human, "review", cap=1)
    res, msg = mcp_call(human, tok, "remember_rule", {"correction": "no emojis"})
    assert not res["isError"], msg
    res, msg = mcp_call(human, tok, "remember_rule", {"correction": "max 2 hashtags"})
    assert res["isError"] and "daily cap" in msg
    res, msg = mcp_call(human, tok, "clips_from_video", {"sample": True})
    assert res["isError"] and "daily cap" in msg


# ------------------------------------------------------------------ 6. anonymous /demo cost (HQ-DEMO-UNAUTH-COST)
def test_6_anonymous_and_sample_requests_are_rate_limited_per_ip(client, human):
    rate = client.app.state.rate
    rate.per_min = 5
    anon = TestClient(client.app, base_url="https://hq.test")
    codes = [anon.get("/api/session").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5] == 429
    assert all(human.get("/api/session").status_code == 200 for _ in range(8))  # a real account is never slowed


def test_6_the_sample_ai_cap_is_atomic_under_parallel_calls(app):
    store = app.state.store
    uid = demo_guard.PREFIX + "race"
    demo_guard.register(store, uid, "203.0.113.50")
    got = []
    barrier = threading.Barrier(24)

    def go():
        barrier.wait()
        got.append(demo_guard.reserve(store, uid, "ai"))
    ts = [threading.Thread(target=go) for _ in range(24)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(got) == demo_guard.CAPS["ai"][0]  # exactly the cap, never more


def test_6_sample_uploads_are_capped_per_file_and_per_day(client):
    client.get("/demo")
    csrf = client.get("/api/session").json()["csrf"]
    client.headers["X-HQ-CSRF"] = csrf
    big = base64.b64encode(b"\0" * (demo_guard.DEMO_FILE_MAX + 1024)).decode()
    assert client.post("/api/media", json={"name": "a.jpg", "data": big}).status_code == 413
    a = client.app.state.house
    uid = client.get("/api/session").json().get("user", {}).get("id") or a.store.q("SELECT user_id FROM demo_owners")[0]["user_id"]
    cap_kb = demo_guard.CAPS["upload_kb"][0]
    assert demo_guard.reserve(a.store, uid, "upload_kb", cap_kb)  # use up today's allowance
    small = base64.b64encode(b"\xff\xd8\xff" + b"\0" * 4000).decode()
    assert client.post("/api/media", json={"name": "b.jpg", "data": small}).status_code == 429


# ------------------------------------------------------------------ 7. unbounded upload body (HQ-UPLOAD-UNBOUNDED-BODY)
def test_7_upload_authenticates_first_and_caps_the_body_before_decoding(client, monkeypatch):
    anon = TestClient(client.app, base_url="https://hq.test")
    assert anon.post("/api/media", json={"name": "a.jpg", "data": "AAAA"}).status_code == 401  # no body work for strangers
    monkeypatch.setattr(limits, "MEDIA_MAX", 2000)
    monkeypatch.setattr(limits, "DEMO_MEDIA_MAX", 2000)
    assert anon.post("/api/media", content=b"x" * 5000, headers={"content-type": "application/json"}).status_code == 413
    client.get("/demo")
    client.headers["X-HQ-CSRF"] = client.get("/api/session").json()["csrf"]

    def chunks():  # no Content-Length: counted as it streams
        for _ in range(10):
            yield b" " * 500
    r = client.post("/api/media", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    monkeypatch.setattr(limits, "DEFAULT_MAX", 1000)
    assert anon.post("/api/ideas", content=b"{" + b" " * 3000 + b"}", headers={"content-type": "application/json"}).status_code == 413
