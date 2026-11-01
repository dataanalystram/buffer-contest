"""Regression tests for the 31 Oct 2026 hardening pass (docs/SECURITY.md, "Fixed in the hardening pass").

Nothing here touches the network: DNS is faked by monkeypatching, HTTP goes to recording or mock transports, and FFmpeg
only reads files made in a temp dir.
"""
import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from studio import llm, mediasafe, netguard
from studio.app import create_app
from studio.config import load_settings
from studio.store import Store

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ 1. HSTS
def test_hsts_on_https_only(app):
    r = TestClient(app, base_url="https://hq.test").get("/healthz")
    hsts = r.headers["strict-transport-security"]
    assert int(re.search(r"max-age=(\d+)", hsts).group(1)) >= 31536000 and "includeSubDomains" in hsts
    assert "strict-transport-security" not in TestClient(app, base_url="http://hq.test").get("/healthz").headers


# ------------------------------------------------------------------ 2. DNS rebinding: the connection goes to the vetted IP
class Recorder(httpx.HTTPTransport):
    """A real network transport class (so netguard pins) that records instead of connecting."""

    def __init__(self, answers=None):
        super().__init__()
        self.seen, self.answers = [], answers or {}

    def handle_request(self, request):
        self.seen.append((str(request.url), request.headers.get("host"), dict(request.extensions).get("sni_hostname")))
        status, headers = self.answers.get(request.headers.get("host"), (200, {}))
        return httpx.Response(status, headers=headers, content=b"ok", request=request)


def _dns(monkeypatch, table):
    """Fake resolver: each lookup pops the next answer for the host (rebinding = a different answer the second time)."""
    calls = []

    def fake(host, *a, **k):
        calls.append(host)
        seq = table[host]
        ip = seq.pop(0) if len(seq) > 1 else seq[0]
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(netguard, "_getaddrinfo", fake)
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return calls


def test_rebinding_host_is_pinned_to_the_vetted_ip(monkeypatch):
    # first lookup public, second lookup loopback: the request must go to the public IP it vetted, never 127.0.0.1
    _dns(monkeypatch, {"rebind.test": ["93.184.216.34", "127.0.0.1"]})
    t = Recorder()
    c = httpx.Client(transport=t, follow_redirects=False)
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)  # the pre-check passes; the pin is what is under test
    r = netguard.safe_get("https://rebind.test/a?b=1", http=c)
    assert r.status_code == 200 and str(r.url) == "https://rebind.test/a?b=1"
    url, host, sni = t.seen[0]
    assert url == "https://93.184.216.34/a?b=1" and host == "rebind.test" and sni == "rebind.test"


def test_a_private_answer_at_connect_time_is_refused(monkeypatch):
    _dns(monkeypatch, {"flip.test": ["10.0.0.5"]})
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)  # pretend the earlier check saw a public answer
    t = Recorder()
    with pytest.raises(netguard.FetchError):
        netguard.safe_get("http://flip.test/", http=httpx.Client(transport=t))
    assert t.seen == []


def test_every_redirect_hop_is_pinned(monkeypatch):
    _dns(monkeypatch, {"a.test": ["93.184.216.34"], "b.test": ["93.184.216.35", "169.254.169.254"]})
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)
    t = Recorder({"a.test": (302, {"location": "https://b.test/final"})})
    r = netguard.safe_get("https://a.test/start", http=httpx.Client(transport=t))
    assert [s[0] for s in t.seen] == ["https://93.184.216.34/start", "https://93.184.216.35/final"]
    assert str(r.url) == "https://b.test/final"


def test_port_and_ipv6_are_kept_when_pinning(monkeypatch):
    _dns(monkeypatch, {"v6.test": ["2606:4700:4700::1111"]})
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)
    t = Recorder()
    netguard.safe_get("http://v6.test:8080/x", http=httpx.Client(transport=t))
    assert t.seen[0][0] == "http://[2606:4700:4700::1111]:8080/x" and t.seen[0][1] == "v6.test:8080"


# ------------------------------------------------------------------ 3. AI model host: re-checked on every call, allowlisted
def _oa(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok(req):
    return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})


def test_a_named_provider_only_talks_to_its_own_host(monkeypatch):
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)
    hits = []
    eng = {"provider": "openai", "base_url": "https://evil.example/v1", "api_key": "k", "model": "m", "style": "openai"}
    with pytest.raises(llm.LLMError, match="isn't allowed"):
        llm.chat(eng, "s", "u", http=_oa(lambda r: hits.append(r) or _ok(r)))
    assert hits == []
    good = dict(eng, base_url="https://api.openai.com/v1")
    assert llm.chat(good, "s", "u", http=_oa(_ok)) == "{}"


def test_custom_endpoint_is_rechecked_each_call(monkeypatch):
    public = {"ok": True}
    monkeypatch.setattr(netguard, "_host_ok", lambda h: public["ok"])
    eng = {"provider": "custom", "base_url": "https://models.example/v1", "api_key": "k", "model": "m", "style": "openai"}
    assert llm.chat(eng, "s", "u", http=_oa(_ok)) == "{}"
    public["ok"] = False  # the name now points inside: the very next call refuses
    with pytest.raises(llm.LLMError):
        llm.chat(eng, "s", "u", http=_oa(_ok))
    with pytest.raises(llm.LLMError):  # never plain http for a user's key
        llm.chat(dict(eng, base_url="http://models.example/v1"), "s", "u", http=_oa(_ok))


def test_operator_can_restrict_custom_hosts_and_redirects_are_not_followed(monkeypatch):
    monkeypatch.setattr(netguard, "_host_ok", lambda h: True)
    monkeypatch.setenv("HQ_AI_CUSTOM_HOSTS", "models.example")
    eng = {"provider": "custom", "base_url": "https://other.example/v1", "api_key": "k", "model": "m", "style": "openai"}
    with pytest.raises(llm.LLMError):
        llm.chat(eng, "s", "u", http=_oa(_ok))
    eng["base_url"] = "https://models.example/v1"
    with pytest.raises(llm.LLMError):
        llm.chat(eng, "s", "u", http=_oa(lambda r: httpx.Response(307, headers={"location": "http://127.0.0.1/"})))


def test_saving_a_preset_with_a_foreign_base_url_is_refused(human, monkeypatch):
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda h: True)
    monkeypatch.setattr(llm, "test_key", lambda eng, http=None: "ok")
    r = human.post("/api/ai", json={"provider": "openai", "api_key": "sk-x", "base_url": "https://evil.example/v1"})
    assert r.status_code == 400 and "provider" in r.json()["error"]


# ------------------------------------------------------------------ 4. FFmpeg: no URLs, no playlists, no local-file reads
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


def test_harden_puts_whitelists_before_every_input():
    cmd = mediasafe.harden(["ffmpeg", "-y", "-i", "/tmp/a.mp4", "-f", "lavfi", "-i", "anullsrc", "-f", "concat", "-safe", "0",
                            "-i", "/tmp/l.txt", "-f", "null", "-"])
    assert cmd[:2] == ["ffmpeg", "-nostdin"]
    idx = [i for i, a in enumerate(cmd) if a == "-i"]
    assert len(idx) == 3
    for i in idx:
        assert cmd[i - 4:i - 2] == ["-protocol_whitelist", "file,pipe"] and cmd[i - 2] == "-format_whitelist"
    assert "lavfi" in cmd[idx[1] - 1] and "concat" in cmd[idx[2] - 1] and "concat" not in cmd[idx[0] - 1]
    p = mediasafe.harden(["ffprobe", "-v", "error", "/tmp/a.mp4"])
    assert p[1:3] == ["-protocol_whitelist", "file,pipe"] and p[3] == "-format_whitelist"
    for bad in ("http://169.254.169.254/x", "file:/etc/passwd", "concat:/a|/b", "subfile,,start,0,end,0,,:/etc/passwd"):
        with pytest.raises(mediasafe.MediaRefused):
            mediasafe.harden(["ffmpeg", "-i", bad, "out.mp4"])


@needs_ffmpeg
def test_crafted_playlists_cannot_read_local_files_or_urls(tmp_path):
    ok = tmp_path / "ok.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=64x64", "-f", "lavfi", "-i", "sine=d=1",
                    "-shortest", str(ok)], check=True, timeout=60)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-LINE\n")
    hls = tmp_path / "evil.m3u8"
    hls.write_text(f"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nfile://{secret}\n#EXTINF:1,\n"
                   "http://169.254.169.254/latest/meta-data/\n#EXT-X-ENDLIST\n")
    ffc = tmp_path / "evil.mp4"
    ffc.write_text(f"ffconcat version 1.0\nfile '{secret}'\n")
    for f in (hls, ffc):
        r = mediasafe.run(["ffmpeg", "-v", "error", "-i", str(f), "-f", "null", "-"], text=True, timeout=60)
        assert r.returncode != 0 and "not on whitelist" in r.stderr.lower()
        with pytest.raises(mediasafe.MediaRefused):
            mediasafe.check_source(str(f))
    assert "mp4" in mediasafe.check_source(str(ok))["format"]["format_name"]


def test_take_analyze_refuses_a_non_video_before_any_decoder(tmp_path):
    from studio import take
    f = tmp_path / "talk.mp4"
    f.write_text("#EXTM3U\nhttp://169.254.169.254/\n")
    if not shutil.which("ffprobe"):
        pytest.skip("ffprobe not installed")
    with pytest.raises(take.TakeError):
        take.analyze(str(f))


def test_no_raw_subprocess_ffmpeg_calls_outside_mediasafe():
    for p in (ROOT / "studio").glob("*.py"):
        if p.name == "mediasafe.py":
            continue
        assert not re.search(r"subprocess\.(run|Popen|call|check_output)\(", p.read_text()), p.name


# ------------------------------------------------------------------ 5. TOKEN_KEY: its own keyring, rotation, migration
def test_token_key_is_separate_from_session_secret_and_migrates(tmp_path):
    db = str(tmp_path / "k.db")
    old = Store(db, "s" * 64)  # before TOKEN_KEY existed: sealed with the SESSION_SECRET-derived key
    old.upsert_user("u1", "", "", "", "", {"access_token": "at_secret"})
    old.x("INSERT INTO ai_settings(user_id,data,updated) VALUES(?,?,?)", ("u1", old.seal({"api_key": "sk_secret"}), 0))
    k1 = Fernet.generate_key().decode()
    new = Store(db, "s" * 64, token_key=k1)
    assert new.tokens("u1")["access_token"] == "at_secret"  # still readable via the legacy key
    assert new.rotate_keys() == {"resealed": 2, "current": 0, "unreadable": 0}
    assert new.rotate_keys()["resealed"] == 0  # idempotent
    # after migration SESSION_SECRET no longer opens anything, and changing it doesn't lose tokens
    assert Store(db, "s" * 64).tokens("u1") is None
    assert Store(db, "another-session-secret", token_key=k1).tokens("u1")["access_token"] == "at_secret"


def test_rotation_reads_the_previous_key_and_writes_the_new_one(tmp_path):
    db = str(tmp_path / "r.db")
    k1, k2 = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    a = Store(db, "x" * 64, token_key=k1)
    a.upsert_user("u1", "", "", "", "", {"access_token": "at_1"})
    b = Store(db, "x" * 64, token_key=k2, token_keys_previous=[k1])
    assert b.tokens("u1")["access_token"] == "at_1"
    assert b.rotate_keys()["resealed"] == 1
    assert Store(db, "x" * 64, token_key=k2).tokens("u1")["access_token"] == "at_1"  # k1 can now be dropped
    assert Store(db, "x" * 64, token_key=k1).tokens("u1") is None
    # a row no key opens is left untouched, never overwritten
    b.x("UPDATE users SET tokens=? WHERE id=?", ("not-a-fernet-token", "u1"))
    assert b.rotate_keys()["unreadable"] == 1
    assert b.q("SELECT tokens FROM users WHERE id='u1'")[0]["tokens"] == "not-a-fernet-token"


def test_app_boot_migrates_and_logs_counts_only(tmp_path, capsys):
    db = str(tmp_path / "boot.db")
    Store(db, "b" * 64).upsert_user("u1", "", "", "", "", {"access_token": "at_boot_secret"})
    k = Fernet.generate_key().decode()
    create_app(load_settings(db_path=db, session_secret="b" * 64, token_key=k, secure_cookies=False, ai_base_url=""))
    out = capsys.readouterr().out
    assert "dedicated TOKEN_KEY" in out and "'resealed': 1" in out
    assert "at_boot_secret" not in out and k not in out


# ------------------------------------------------------------------ sweep: every route is authenticated or listed public
PUBLIC = {  # path -> why it is open to anyone
    "/openapi.json": "API description (open source)", "/api/docs": "redirects to /docs/api", "/docs/api": "API reference page",
    "/": "landing / app shell", "/privacy": "privacy notice", "/static/{name}": "static assets", "/api/stats": "public counters",
    "/static/examples/{brand}/{name}": "pre-rendered example brand gallery (committed files)",
    "/static/{name:path}": "static assets (nested: fonts, brand, docs shots)", "/app": "app shell; redirects to / when signed out",
    "/terms": "terms of use", "/security": "security page", "/favicon.ico": "favicon", "/og.png": "social preview image",
    "/robots.txt": "crawl rules", "/sitemap.xml": "sitemap", "/docs": "documentation", "/docs/{slug}": "documentation page",
    "/docs/search.json": "documentation search index",
    "/security.txt": "security contact", "/.well-known/security.txt": "security contact", "/healthz": "uptime check",
    "/login": "starts Buffer OAuth", "/callback": "Buffer OAuth return (state-checked)", "/demo": "rate-capped sample",
    "/device/{tok}": "single-use device link (the token is the credential)", "/api/logout": "ends the caller's own session",
    "/api/session": "anonymous session + CSRF token", "/m/{token}": "media by unguessable token (Buffer fetches it)",
    "/sample/{name}": "sample take files", "/.well-known/oauth-protected-resource": "MCP OAuth discovery",
    "/.well-known/oauth-protected-resource/mcp": "MCP OAuth discovery", "/.well-known/oauth-authorization-server": "MCP OAuth discovery",
    "/.well-known/oauth-authorization-server/mcp": "MCP OAuth discovery", "/oauth/register": "dynamic client registration (rate-limited)",
    "/oauth/authorize": "consent; needs a signed-in session to grant", "/oauth/token": "code/refresh exchange (PKCE)",
    "/mcp:GET": "405 only",
    "/integrations/producthunt/callback": "Product Hunt Redirect URI: a friendly page; a code is exchanged only with our own single-use state",
}


def test_every_route_requires_auth_or_is_explicitly_public(app):
    anon = TestClient(app, base_url="https://hq.test", raise_server_exceptions=False)
    unlisted = []
    for r in app.routes:
        methods = sorted(getattr(r, "methods", None) or [])
        for m in [x for x in methods if x != "HEAD"]:
            if r.path in PUBLIC or f"{r.path}:{m}" in PUBLIC:
                continue
            url = re.sub(r"\{[^}]+\}", "1", r.path)
            res = anon.request(m, url, json={} if m in ("POST", "PATCH", "PUT") else None, follow_redirects=False)
            if res.status_code not in (401, 403):
                unlisted.append(f"{m} {r.path} -> {res.status_code}")
    assert unlisted == [], "routes neither authenticated nor in PUBLIC: " + ", ".join(unlisted)
    assert all(isinstance(r, APIRoute) or r.path in PUBLIC for r in app.routes if hasattr(r, "path"))


def test_oauth_authorize_grant_needs_a_signed_in_human(app):
    anon = TestClient(app, base_url="https://hq.test")
    r = anon.post("/oauth/authorize", data={"client_id": "x", "redirect_uri": "https://claude.ai/cb", "decision": "allow"},
                  follow_redirects=False)
    assert r.status_code in (302, 400, 401, 403) and "code=" not in r.headers.get("location", "")


# ------------------------------------------------------------------ sweep: errors, redirects, rate limits, cookies, CSP
def test_a_crash_never_leaks_a_trace(app):
    @app.get("/api/__boom")
    def boom():
        raise RuntimeError("secret-internal-detail /srv/app/studio/x.py line 9")
    r = TestClient(app, base_url="https://hq.test", raise_server_exceptions=False).get("/api/__boom")
    assert r.status_code == 500 and "secret-internal-detail" not in r.text and "Traceback" not in r.text
    assert r.json()["error"]


def test_login_next_cannot_redirect_off_site(app):
    st = app.state.store
    for evil in ("https://evil.example/", "//evil.example/", "/\\evil.example", "javascript:alert(1)"):
        c = TestClient(app, base_url="https://hq.test")
        r = c.get("/login", params={"next": evil}, follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"].startswith("https://auth.buffer.com/")
        sess = st.session(c.cookies.get("hq_sid"))
        assert st.unseal(sess["oauth"])["next"] == ""


def test_sign_in_endpoints_are_rate_limited_per_ip(app):
    app.state.rate.auth_per_min = 3
    c = TestClient(app, base_url="https://hq.test")
    codes = [c.get("/device/nope", follow_redirects=False).status_code for _ in range(5)]
    assert codes[:3] == [302] * 3 and codes[3] == 429


def test_session_cookie_flags(tmp_path):
    prod = create_app(load_settings(db_path=str(tmp_path / "c.db"), secure_cookies=True, ai_base_url=""))
    r = TestClient(prod, base_url="https://hq.test").get("/api/session")
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "secure" in sc and "samesite=lax" in sc and "path=/" in sc
    assert r.headers["cache-control"] == "no-store"


def test_csp_and_isolation_headers(app):
    h = TestClient(app, base_url="https://hq.test").get("/").headers
    csp = h["content-security-policy"]
    for d in ("default-src 'self'", "script-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"):
        assert d in csp
    assert "unsafe-eval" not in csp and "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert h["cross-origin-opener-policy"] == "same-origin" and "camera=()" in h["permissions-policy"]


def test_security_txt(app):
    r = TestClient(app, base_url="https://hq.test").get("/.well-known/security.txt")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    fields = dict(line.split(": ", 1) for line in r.text.strip().splitlines())
    assert fields["Contact"].startswith("https://") and fields["Expires"] > "2026-10-31"
    assert fields["Canonical"].endswith("/.well-known/security.txt")


# ------------------------------------------------------------------ sweep: dependency floors past known advisories
FLOORS = {"fastapi": (0, 115, 0), "starlette": (0, 49, 1), "cryptography": (44, 0, 1), "pillow": (11, 3, 0),
          "httpx": (0, 27, 0), "uvicorn": (0, 30, 0), "psycopg": (3, 2, 0)}


def test_studio_requirements_pin_floors_past_known_advisories():
    reqs = {}
    for line in (ROOT / "requirements-studio.txt").read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?>=([\d.]+)", line.strip())
        if m:
            reqs[m.group(1).lower()] = tuple(int(x) for x in m.group(2).split("."))
    for pkg, floor in FLOORS.items():
        assert pkg in reqs, f"{pkg} has no >= floor in requirements-studio.txt"
        assert reqs[pkg] + (0,) * (3 - len(reqs[pkg])) >= floor, f"{pkg} floor {reqs[pkg]} < {floor}"
