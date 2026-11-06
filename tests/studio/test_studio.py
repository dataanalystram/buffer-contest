import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from studio import drafter
from studio.actions import build_post_input

from .conftest import CHANNELS, agent_token, connect, mcp_call


def bot(human):
    """A cookie-less client on the same app: how an agent calls the REST API."""
    from fastapi.testclient import TestClient
    return TestClient(human.app, base_url="https://hq.test")


def test_browser_session_wins_over_bearer(human):
    tok = agent_token(human)
    assert human.get("/api/me", headers={"Authorization": f"Bearer {tok}"}).json()["actor"] == "you"
    assert bot(human).get("/api/me", headers={"Authorization": f"Bearer {tok}"}).json()["actor"] == "agent:Claude"
    assert bot(human).get("/api/me").status_code == 401


# ------------------------------------------------------------------ OAuth + token handling
def test_login_uses_pkce_s256_state_and_offline_access(client):
    r = client.get("/login", follow_redirects=False)
    u = urlparse(r.headers["location"])
    q = parse_qs(u.query)
    assert u.netloc == "auth.buffer.com" and u.path == "/auth"
    assert q["code_challenge_method"] == ["S256"] and len(q["code_challenge"][0]) >= 43
    assert "offline_access" in q["scope"][0].split() and "posts:write" in q["scope"][0].split()
    assert q["redirect_uri"] == ["https://hq.test/callback"] and q["state"][0]
    assert "client_secret" not in r.headers["location"]
    # Buffer's documented authorize request (developers.buffer.com/guides/authentication.html, Step 2)
    assert q["prompt"] == ["consent"] and q["response_type"] == ["code"]
    assert q["scope"] == ["account:read posts:read posts:write ideas:read ideas:write offline_access"]


def test_callback_rejects_wrong_state(client):
    client.get("/login", follow_redirects=False)
    r = client.get("/callback?code=abc&state=forged", follow_redirects=False)
    assert "error=" in r.headers["location"]
    assert client.get("/api/session").json()["connected"] is False


def test_tokens_never_reach_browser_and_are_encrypted_at_rest(client, app, fake):
    connect(client)
    s = client.get("/api/session")
    assert s.json()["connected"] is True and s.json()["user"]["email"] == "ram@example.com"
    blob = s.text + str(client.get("/api/me").text) + str(client.cookies)
    assert "at_1" not in blob and "rt_1" not in blob
    st = app.state.store
    raw = str(st.q("SELECT * FROM users")).encode() if st.pg else Path(app.state.settings.db_path).read_bytes()
    assert b"at_1" not in raw and b"rt_1" not in raw
    assert fake.calls[0][2] == "Bearer at_1"


def test_refresh_token_rotates_and_old_one_is_never_reused(human, app, fake):
    store = app.state.store
    tok = store.tokens("acc_1")
    tok["expires_at"] = time.time() - 1
    store.save_tokens("acc_1", tok)
    assert human.get("/api/channels?refresh=1").status_code == 200
    assert store.tokens("acc_1")["refresh_token"] == "rt_2"
    tok = store.tokens("acc_1"); tok["expires_at"] = 0; store.save_tokens("acc_1", tok)
    assert human.get("/api/channels?refresh=1").status_code == 200
    used = [c["refresh_token"][0] for c in fake.token_calls if c["grant_type"] == ["refresh_token"]]
    assert used == ["rt_1", "rt_2"]


def test_session_id_rotates_on_login(client):
    client.get("/api/session")
    before = client.cookies.get("hq_sid")
    connect(client)
    assert client.cookies.get("hq_sid") != before


# ------------------------------------------------------------------ rate limits + caching
def test_channel_list_is_cached(human, fake):
    human.get("/api/channels")
    n = len(fake.calls)
    human.get("/api/channels"); human.get("/api/channels")
    assert len(fake.calls) == n


def test_long_retry_after_is_surfaced_not_hammered(human, fake):
    fake.fail_429 = 1
    r = human.get("/api/channels?refresh=1")
    assert r.status_code == 429 and r.headers["retry-after"] == "120"
    n = len(fake.calls)
    assert human.get("/api/channels?refresh=1").status_code == 429  # local backoff, no extra call
    assert len(fake.calls) == n


def test_ratelimit_headers_are_read(human):
    human.get("/api/channels")
    w = human.get("/api/limits").json()["buffer_api"]["windows"]
    assert w[0]["window"] == "15 min" and w[0]["remaining"] == 97 and w[0]["quota"] == 100


# ------------------------------------------------------------------ drafting
def test_draft_from_link_fits_every_channel(human):
    r = human.post("/api/draft", json={"url": "https://news.example.com/ai-launch"}).json()
    assert {d["service"] for d in r["drafts"]} == {"twitter", "linkedin", "instagram"}
    for d in r["drafts"]:
        assert 0 < d["length"] <= d["limit"]
        assert drafter.count(d["service"], d["text"]) == d["length"]
    ig = next(d for d in r["drafts"] if d["service"] == "instagram")
    assert "link in bio" in ig["text"] and "https://" not in ig["text"]


def test_counting_rules_match_buffer_docs():
    url = "https://example.com/" + "a" * 80
    assert drafter.count("twitter", "hi " + url) == 3 + 23
    assert drafter.count("linkedin", url) == 24
    assert drafter.count("instagram", "a\nb") == 4
    assert drafter.count("facebook", "🚀") == 2
    assert drafter.count("bluesky", "🚀") == 1


def test_fit_trims_and_keeps_link():
    t = drafter.fit("twitter", "word " * 100 + "https://example.com/x")
    assert drafter.count("twitter", t) <= 280 and t.endswith("https://example.com/x")


def test_fetch_blocks_private_addresses():
    import pytest
    from studio.drafter import FetchError, _public
    assert _public("127.0.0.1") is False
    for bad in ("http://127.0.0.1/admin", "ftp://example.com/x", "not a url"):
        with pytest.raises(FetchError):
            drafter.fetch_article(bad)


# ------------------------------------------------------------------ posting (human)
def test_human_preview_submit_creates_buffer_draft(human, fake):
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "Hello", "mode": "draft"}).json()
    assert pv["confirm_token"].startswith("cf_") and pv["preview"]["limit"] == 280
    r = human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}).json()
    assert r["status"] == "sent_to_buffer" and r["post"]["status"] == "draft"
    assert fake.created[-1]["saveToDraft"] is True and fake.created[-1]["needsApproval"] is False
    again = human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    assert again.status_code == 410


def test_validation_rules(human):
    long = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "x" * 300, "mode": "queue"})
    assert long.status_code == 400 and "300/280" in long.json()["error"]
    ig = human.post("/api/posts/preview", json={"channel_id": "ch_ig", "text": "hi", "mode": "queue"})
    assert ig.status_code == 400 and "image or video" in ig.json()["error"]
    ok = human.post("/api/posts/preview", json={"channel_id": "ch_ig", "text": "hi", "mode": "draft"}).json()
    assert ok["warnings"]
    past = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi", "mode": "schedule", "due_at": "2020-01-01T00:00:00Z"})
    assert past.status_code == 400


def test_post_input_shapes():
    ig = {"id": "ch_ig", "service": "instagram"}
    inp = build_post_input(ig, "hi", "https://cdn/x.mp4", "schedule", "2026-11-02T09:00:00.000Z")
    assert inp["assets"] == [{"video": {"url": "https://cdn/x.mp4"}}] and inp["mode"] == "customScheduled"
    assert inp["metadata"] == {"instagram": {"type": "reel", "shouldShareToFeed": True}}  # a video is a Reel
    yt = build_post_input({"id": "y", "service": "youtube"}, "Title line\nbody", "", "now", None)
    assert yt["metadata"]["youtube"]["title"] == "Title line" and yt["mode"] == "shareNow"


def test_threads_from_link_and_post_input(human, fake):
    r = human.post("/api/draft", json={"url": "https://news.example.com/ai-launch", "thread": True}).json()
    x = next(d for d in r["drafts"] if d["service"] == "twitter")
    parts = drafter.split_thread("twitter", x["text"])
    assert x["parts"] == len(parts) >= 2 and parts[-1].endswith("https://news.example.com/ai-launch")
    assert all(drafter.count("twitter", p) <= 280 for p in parts)
    assert next(d for d in r["drafts"] if d["service"] == "linkedin")["parts"] == 1
    inp = build_post_input({"id": "ch_x", "service": "twitter"}, "one\n---\ntwo", "https://cdn/a.png", "queue", None)
    assert inp["text"] == "one" and inp["metadata"]["twitter"]["thread"] == [
        {"text": "one", "assets": [{"image": {"url": "https://cdn/a.png"}}]}, {"text": "two"}]
    li = build_post_input({"id": "ch_li", "service": "linkedin"}, "one\n---\ntwo", "", "queue", None)
    assert li["text"] == "one\n---\ntwo" and "metadata" not in li
    long = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "ok\n---\n" + "x" * 300, "mode": "queue"})
    assert long.status_code == 400 and "Thread post 2: 300/280" in long.json()["error"]


def test_csrf_required_for_browser_writes(client):
    connect(client)
    r = client.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi"})
    assert r.status_code == 403


def test_idea_goes_to_buffer(human, fake):
    r = human.post("/api/ideas", json={"title": "T", "text": "Body"}).json()
    assert r["status"] == "saved_to_buffer_ideas" and fake.ideas[0]["organizationId"] == "org_1"


# ------------------------------------------------------------------ agents + safety
def test_review_policy_holds_agent_posts_until_human_approves(human, fake):
    tok = agent_token(human)
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "From Claude", "mode": "queue"})
    assert "Approvals inbox" in pv["on_submit"]
    _, res = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["status"] == "pending_approval" and fake.created == []
    # an agent can't approve its own post through REST
    r = bot(human).post(f"/api/approvals/{res['approval_id']}/approve", headers={"Authorization": f"Bearer {tok}"}, json={})
    assert r.status_code == 403 and fake.created == []
    pend = human.get("/api/approvals").json()
    assert pend[0]["agent_name"] == "Claude"
    ok = human.post(f"/api/approvals/{res['approval_id']}/approve", json={"text": "From Claude, edited"}).json()
    assert ok["status"] == "sent_to_buffer" and fake.created[-1]["text"] == "From Claude, edited"
    assert human.get("/api/approvals").json() == []


def test_draft_policy_never_publishes(human, fake):
    tok = agent_token(human, "draft")
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "x", "mode": "now"})
    _, res = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["mode"] == "draft" and fake.created[-1]["saveToDraft"] is True and "shareNow" not in str(fake.created[-1])


def test_kill_switch_and_daily_cap(human, fake):
    tok = agent_token(human, "trusted", cap=1)
    human.post("/api/safety/kill", json={"on": True})
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "x", "mode": "queue"})
    res, msg = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["isError"] and "kill switch" in msg and fake.created == []
    # agents can't flip it back
    r = bot(human).post("/api/safety/kill", headers={"Authorization": f"Bearer {tok}"}, json={"on": False})
    assert r.status_code in (403, 423)  # human-only (403), or refused while the switch is on (423)
    human.post("/api/safety/kill", json={"on": False})
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "x", "mode": "queue"})
    _, ok = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert ok["status"] == "sent_to_buffer"
    _, pv = mcp_call(human, tok, "preview_post", {"channel_id": "ch_x", "text": "y", "mode": "queue"})
    res, msg = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["isError"] and "daily cap" in msg
    audit = human.get("/api/audit").json()
    assert any(a["risk"] == "blocked" for a in audit) and any(a["action"] == "kill_switch" for a in audit)


def test_confirm_token_is_bound_to_its_caller(human):
    tok = agent_token(human, "trusted")
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "mine", "mode": "draft"}).json()
    res, msg = mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert res["isError"] and "different caller" in msg


def test_revoked_token_is_rejected(human):
    tok = agent_token(human)
    tid = human.get("/api/safety").json()["tokens"][0]["id"]
    human.delete(f"/api/agent-tokens/{tid}")
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


# ------------------------------------------------------------------ parity: UI = REST API = MCP
def test_ui_rest_and_mcp_send_identical_buffer_calls(human, fake):
    post = {"channel_id": "ch_li", "text": "Same post https://example.com", "mode": "queue",
            "media_url": "https://cdn.example.com/a.png"}
    # 1) UI path (session + CSRF)
    pv = human.post("/api/posts/preview", json=post).json()
    human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    # 2) REST path with an agent bearer token (trusted)
    tok = agent_token(human, "trusted")
    h = {"Authorization": f"Bearer {tok}"}
    agent_rest = bot(human)
    pv = agent_rest.post("/api/posts/preview", json=post, headers=h).json()
    assert agent_rest.get("/api/me", headers=h).json()["actor"] == "agent:Claude"
    agent_rest.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]}, headers=h)
    # 3) MCP path, same token
    _, pv = mcp_call(human, tok, "preview_post", post)
    mcp_call(human, tok, "submit_post", {"confirm_token": pv["confirm_token"]})
    assert len(fake.created) == 3 and fake.created[0] == fake.created[1] == fake.created[2]


def test_mcp_lists_tools_matching_rest_surface(human):
    tok = agent_token(human)
    h = {"Authorization": f"Bearer {tok}"}
    init = human.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}).json()
    assert init["result"]["serverInfo"]["name"] == "huhqueue-studio"
    assert human.post("/mcp", headers=h, json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    names = {t["name"] for t in human.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"list_channels", "draft_from_link", "preview_post", "submit_post", "list_queue", "create_idea", "list_approvals"} <= names
    _, chans = mcp_call(human, tok, "list_channels")
    assert [c["id"] for c in chans] == [c["id"] for c in human.get("/api/channels").json()]
    _, d = mcp_call(human, tok, "draft_from_link", {"url": "https://news.example.com/x", "channel_ids": ["ch_x"]})
    assert d["drafts"][0]["service"] == "twitter"
    assert human.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).status_code == 401


def test_disconnect_deletes_everything(human, app):
    agent_token(human)
    assert human.post("/api/disconnect").json()["deleted"] is True
    assert app.state.store.user("acc_1") is None
    assert app.state.store.q("SELECT COUNT(*) n FROM agent_tokens")[0]["n"] == 0


# ------------------------------------------------------------------ UI contract
def test_every_control_has_a_data_testid():
    html = (Path(__file__).parents[2] / "studio/static/index.html").read_text()
    for tag in re.findall(r"<(?:button|input|select|textarea)\b[^>]*>", html):
        assert "data-testid" in tag, tag
    for name in ("app.js", "planner.js", "connect.js", "shell.js", "oauth-open.js"):
        js = (Path(__file__).parents[2] / "studio/static" / name).read_text()
        for tag in re.findall(r"<(?:button|input|select|textarea)\b[^>]*>", js):
            assert "data-testid" in tag, (name, tag)


def test_pages_and_branding(client):
    home = client.get("/").text
    assert "built on the Buffer API" in home and "Not affiliated" in home
    assert client.get("/privacy").status_code == 200 and "Privacy policy" in client.get("/privacy").text
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/static/../app.py").status_code == 404


def test_clean_title_drops_site_suffix():
    assert drafter.clean_title("Large language model - Wikipedia", "Wikipedia") == "Large language model"
    assert drafter.clean_title("OpenAI ships GPT-6 | The Verge", "The Verge") == "OpenAI ships GPT-6"
    assert drafter.clean_title("No suffix here at all", "x") == "No suffix here at all"


def test_device_link_is_single_use(human, app):
    from fastapi.testclient import TestClient
    url = human.post("/api/device-link").json()["url"]
    path = url.replace("https://hq.test", "")
    other = TestClient(app, base_url="https://hq.test")
    r = other.get(path, follow_redirects=False)
    assert r.headers["location"] == "/?connected=1"
    assert other.get("/api/session").json()["connected"] is True
    third = TestClient(app, base_url="https://hq.test")
    assert "error=" in third.get(path, follow_redirects=False).headers["location"]
    assert third.get("/api/session").json()["connected"] is False


def test_demo_workspace_never_calls_buffer(client, fake):
    r = client.get("/demo", follow_redirects=False)
    assert r.headers["location"] == "/?demo=1"
    s = client.get("/api/session").json()
    assert s["connected"] is True and s["demo"] is True
    client.headers["X-HQ-CSRF"] = s["csrf"]
    assert {c["service"] for c in client.get("/api/channels").json()} >= {"twitter", "linkedin", "bluesky"}
    [appr] = client.get("/api/approvals").json()
    r = client.post(f"/api/approvals/{appr['id']}/approve", json={})  # view-only: the real-app line, nothing changes
    assert r.status_code == 403 and r.json()["error"].startswith("In the real app this would approve")
    assert len(client.get("/api/approvals").json()) == 1
    pv = client.post("/api/posts/preview", json={"channel_id": "demo_bsky", "text": "hello", "mode": "draft"}).json()
    r = client.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    assert r.status_code == 403 and "The demo doesn't change anything." in r.json()["error"]
    assert fake.calls == [] and fake.token_calls == []


def test_public_stats_count_real_accounts_only(human, client):
    pv = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "Hello", "mode": "queue"}).json()
    human.post("/api/posts/submit", json={"confirm_token": pv["confirm_token"]})
    from fastapi.testclient import TestClient
    TestClient(human.app, base_url="https://hq.test").get("/demo", follow_redirects=False)
    st = client.get("/api/stats").json()
    assert st == {"buffer_accounts": 1, "posts_sent_to_buffer": 1, "sample_workspaces_open": 1}


RSS = b"""<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Example News</title>
<item><title>Old story</title><link>https://news.example.com/old</link><pubDate>Mon, 26 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>New &amp; shiny</title><link>https://news.example.com/new</link><description>&lt;p&gt;Big &lt;b&gt;news&lt;/b&gt;&lt;/p&gt;</description>
<pubDate>Wed, 28 Oct 2026 09:00:00 GMT</pubDate><media:content url="https://news.example.com/a.jpg" medium="image"/></item></channel></rss>"""
ATOM = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Blog</title><entry><title>Atom post</title>
<link rel="alternate" href="https://blog.example.com/p1"/><updated>2026-10-27T10:00:00Z</updated><summary>Hi</summary></entry></feed>"""


def test_radar_parses_rss_and_atom():
    from studio import radar
    rss = radar.parse(RSS, "https://news.example.com/feed")
    assert rss["title"] == "Example News" and len(rss["stories"]) == 2
    new = next(s for s in rss["stories"] if s["url"].endswith("/new"))
    assert new["title"] == "New & shiny" and new["summary"] == "Big news" and new["image"] == "https://news.example.com/a.jpg"
    atom = radar.parse(ATOM, "https://blog.example.com/atom")
    assert atom["stories"][0]["url"] == "https://blog.example.com/p1" and atom["stories"][0]["published"] > 0
    import pytest
    with pytest.raises(drafter.FetchError):
        radar.parse(b"<html>not a feed", "https://x.example.com")


def test_radar_follow_list_and_agent_tool(human, monkeypatch):
    from studio import radar
    monkeypatch.setattr(radar, "fetch", lambda url, *a, **k: radar.parse(RSS if "news" in url else ATOM, url))
    assert human.post("/api/radar/feeds", json={"url": "https://news.example.com/feed"}).json()["stories"] == 2
    human.post("/api/radar/feeds", json={"url": "https://blog.example.com/atom"})
    assert human.post("/api/radar/feeds", json={"url": "https://blog.example.com/atom"}).status_code == 400
    r = human.get("/api/radar").json()
    assert [s["title"] for s in r["stories"]] == ["New & shiny", "Atom post", "Old story"]
    tok = agent_token(human)
    _, stories = mcp_call(human, tok, "list_stories")
    assert stories["stories"][0]["url"] == "https://news.example.com/new"
    assert bot(human).post("/api/radar/feeds", json={"url": "https://x.example.com/f"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    human.delete(f"/api/radar/feeds/{r['feeds'][0]['id']}")
    assert len(human.get("/api/radar").json()["feeds"]) == 1


def test_plan_limits_warn_and_block_before_buffer_does(human, fake, app):
    assert human.get("/api/plan").json()["meters"] == []  # Buffer shares nothing: no warnings, nothing breaks
    fake.plan = {"scheduledPosts": 1, "channels": 3}
    app.state.buffer._cache.clear()
    p = human.get("/api/plan").json()
    assert {m["key"]: m["pct"] for m in p["meters"]} == {"scheduled_posts": 100, "channels": 100}
    assert len(p["near_limit"]) == 2 and p["upgrade_url"].startswith("https://buffer.com/")
    fake.daily = {"limit": 3, "scheduled": 2, "sent": 0, "isAtLimit": False}
    ok = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi", "mode": "queue"}).json()
    assert any("last post" in w for w in ok["warnings"])
    fake.daily = {"limit": 3, "scheduled": 3, "sent": 0, "isAtLimit": True}
    r = human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi", "mode": "queue"})
    assert r.status_code == 400 and "3 of 3" in r.json()["error"]
    assert human.post("/api/posts/preview", json={"channel_id": "ch_x", "text": "hi", "mode": "draft"}).status_code == 200


def test_migrate_from_copies_everything_once(human, app, tmp_path, fake):
    """Moving databases (Render Postgres -> Supabase): MIGRATE_FROM_URL copies every row into the empty new store,
    the user stays signed in to Buffer and agent tokens keep working; a second boot copies nothing."""
    import httpx
    from fastapi.testclient import TestClient
    from studio.app import create_app
    from studio.config import load_settings
    token = agent_token(human)
    uid = app.state.store.q("SELECT id FROM users")[0]["id"]
    app.state.store.x("INSERT INTO feeds(user_id,url,created) VALUES(?,?,?)", (uid, "https://openai.com/news/rss.xml", 1.0))
    old = app.state.settings
    new_db = str(tmp_path / "new.db")
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret=old.session_secret,
                      db_path=new_db, migrate_from=old.db_path, secure_cookies=False, ai_base_url="")
    moved = TestClient(create_app(s, http=httpx.Client(transport=httpx.MockTransport(fake))), base_url="https://hq.test")
    moved.cookies.update(human.cookies)
    assert moved.get("/api/me").status_code == 200
    r = moved.get("/api/channels", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    store = moved.app.state.store
    n_audit = store.q("SELECT COUNT(*) AS n FROM audit")[0]["n"]
    assert n_audit > 0 and store.q("SELECT COUNT(*) AS n FROM feeds")[0]["n"] == 1
    assert store.copy_from(app.state.store) == {}  # second boot: target already has data
    assert moved.get("/healthz").json()["db"] is True


def test_mcp_oauth_connect_from_claude_or_chatgpt(client, fake):
    """Claude.ai / ChatGPT flow: 401 points at metadata -> register -> sign in with Buffer -> consent -> PKCE token
    exchange -> the access token is an ordinary agent token with the chosen policy -> refresh rotates it."""
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlparse
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401 and 'resource_metadata="https://hq.test/.well-known/oauth-protected-resource/mcp"' in r.headers["www-authenticate"]
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert prm["resource"] == "https://hq.test/mcp" and prm["authorization_servers"] == ["https://hq.test"]
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert asm["code_challenge_methods_supported"] == ["S256"]
    assert client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]}).status_code == 400
    cb = "https://claude.ai/api/mcp/auth_callback"
    reg = client.post("/oauth/register", json={"client_name": "Claude", "redirect_uris": [cb]}).json()
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    q = {"response_type": "code", "client_id": reg["client_id"], "redirect_uri": cb, "state": "st8",
         "code_challenge": challenge, "code_challenge_method": "S256"}
    # not signed in: off to Buffer, and back to the consent page afterwards
    r = client.get("/oauth/authorize", params=q, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=%2Foauth%2Fauthorize%3F")
    r = client.get(r.headers["location"], follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    r = client.get(f"/callback?code=abc&state={state}", follow_redirects=False)
    assert r.headers["location"].startswith("/oauth/authorize?")
    page = client.get(r.headers["location"]).text
    assert "Claude" in page and 'data-testid="oauth-allow"' in page
    csrf = client.get("/api/session").json()["csrf"]
    form = {**{k: v for k, v in q.items() if k != "response_type"}, "csrf": csrf, "policy": "draft", "daily_cap": "5",
            "decision": "allow"}
    assert client.post("/oauth/authorize", data={**form, "csrf": "wrong"}, follow_redirects=False).status_code == 403
    r = client.post("/oauth/authorize", data=form, follow_redirects=False)
    back = urlparse(r.headers["location"])
    assert r.status_code == 302 and back.netloc == "claude.ai" and parse_qs(back.query)["state"] == ["st8"]
    code = parse_qs(back.query)["code"][0]
    bad = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                            "redirect_uri": cb, "code_verifier": "wrong" * 10})
    assert bad.status_code == 400  # and the code is now burned
    r = client.post("/oauth/authorize", data=form, follow_redirects=False)
    code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
    tok = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code, "client_id": reg["client_id"],
                                            "redirect_uri": cb, "code_verifier": verifier}).json()
    assert tok["access_token"].startswith("hq_") and tok["refresh_token"].startswith("hqr_")
    again = client.post("/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                              "client_id": reg["client_id"], "code_verifier": verifier})
    assert again.status_code == 400  # single use
    client.cookies.clear()
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "whoami"}},
                    headers={"Authorization": "Bearer " + tok["access_token"]})
    assert r.status_code == 200 and '"draft"' in r.json()["result"]["content"][0]["text"]
    new = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                            "client_id": reg["client_id"]}).json()
    assert new["access_token"] != tok["access_token"]
    old = client.post("/mcp", json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
                      headers={"Authorization": "Bearer " + tok["access_token"]})
    assert old.status_code == 401  # rotated out
    assert client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                             "client_id": reg["client_id"]}).status_code == 400
