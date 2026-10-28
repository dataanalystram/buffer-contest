"""REST API: auth/CSRF, OAuth state, posts, media, queue, ideas, community, insights, tokens/approvals/audit, edge server."""
import io
import zipfile
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from huhqueue.models import AuditLog
from huhqueue.worker import Worker

from .conftest import PASSWORD, future, make_image, upload


# ------------------------------------------------------------------ auth & security
def test_login_flow_and_csrf(app):
    c = TestClient(app)
    assert c.get("/api/channels").status_code == 401
    assert c.get("/api/auth/status").json() == {"has_password": False, "logged_in": False}
    assert c.post("/api/auth/setup", json={"password": "short"}).status_code == 400
    assert c.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 200
    assert c.post("/api/auth/setup", json={"password": PASSWORD}).status_code == 409
    assert c.get("/api/channels").status_code == 200
    # state-changing request without CSRF header is refused
    assert c.post("/api/ideas", json={"title": "x"}).status_code == 403
    assert c.post("/api/ideas", json={"title": "x"}, headers={"X-CSRF-Token": c.cookies["huhq_csrf"]}).status_code == 200
    c2 = TestClient(app)
    assert c2.post("/api/auth/login", json={"password": "wrong-password"}).status_code == 401
    assert c2.post("/api/auth/login", json={"password": PASSWORD}).status_code == 200


def test_password_change_logs_out_other_sessions(app, api):
    other = TestClient(app)
    other.post("/api/auth/login", json={"password": PASSWORD})
    assert other.get("/api/channels").status_code == 200
    assert api.post("/api/auth/password", {"current": PASSWORD, "new": "another-pass-9"}).status_code == 200
    assert other.get("/api/channels").status_code == 401
    assert api.get("/api/channels").status_code == 200  # the changing session got a fresh cookie


def test_tokens_are_encrypted_at_rest(core, channel):
    import sqlite3
    raw = sqlite3.connect(core.settings.data_dir / "huhqueue.db").execute("select access_token_enc from channels").fetchone()[0]
    assert "tok-1234" not in raw and raw.startswith("gAAAA")


def test_log_redaction():
    from huhqueue.security import redact
    s = redact("GET https://graph.instagram.com/v26.0/me?access_token=IGQVJ123&fields=x Authorization: Bearer abc.def code=xyz")
    assert "IGQVJ123" not in s and "abc.def" not in s and "xyz" not in s


def test_log_redaction_keeps_uvicorn_access_args():
    """uvicorn's AccessFormatter unpacks record.args as a 5-tuple; redaction must not empty it
    (it did on the live Instagram OAuth callback: 'not enough values to unpack (expected 5, got 0)')."""
    import logging
    from uvicorn.logging import AccessFormatter
    from huhqueue.security import RedactFilter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4:0", "GET", "/oauth/instagram/callback?code=SECRETCODE&state=abc", "1.1", 303), None)
    assert RedactFilter().filter(rec)
    assert len(rec.args) == 5
    out = AccessFormatter("%(client_addr)s - \"%(request_line)s\" %(status_code)s", use_colors=False).format(rec)
    assert "SECRETCODE" not in out and "code=***" in out and "303" in out


# ------------------------------------------------------------------ OAuth connect flow
def test_oauth_connect_state_and_callback(api, edge):
    start = api.post("/api/connect/fake").json()
    q = parse_qs(urlparse(start["url"]).query)
    state = q["state"][0]
    assert len(state) > 30 and start["redirect_uri"].endswith("/oauth/fake/callback")
    # the demo consent page mimics Instagram's "Continue as …"
    page = api.get(start["url"])
    assert "Continue as" in page.text
    r = edge.get(f"/oauth/fake/callback?code=fakecode&state={state}", follow_redirects=False)
    assert r.status_code == 303 and "#/channel/ch_" in r.headers["location"] and "connected=1" in r.headers["location"]
    chans = api.get("/api/channels").json()
    assert len(chans) == 1 and chans[0]["followers_count"] == 1234 and len(chans[0]["slots"]) == 14
    # replaying the same state is refused (one-time CSRF state)
    r2 = edge.get(f"/oauth/fake/callback?code=fakecode&state={state}", follow_redirects=False)
    assert "connect_error" in r2.headers["location"]
    # forged state is refused
    r3 = edge.get("/oauth/fake/callback?code=fakecode&state=forged", follow_redirects=False)
    assert "connect_error" in r3.headers["location"]


def test_oauth_paste_url_fallback(api):
    start = api.post("/api/connect/fake").json()
    state = parse_qs(urlparse(start["url"]).query)["state"][0]
    r = api.post("/api/oauth/complete", {"url": f"https://whatever/oauth/fake/callback?code=fakecode&state={state}"})
    assert r.status_code == 200 and r.json()["provider"] == "fake"


def test_instagram_connect_not_configured_message(api, core):
    core.settings.instagram_app_id = ""
    r = api.post("/api/connect/instagram")
    assert r.status_code == 400 and "INSTAGRAM_APP_ID" in r.json()["detail"]["message"]


def test_channel_profile_refresh_settings_disconnect(api, channel):
    cid = channel["id"]
    assert api.post(f"/api/channels/{cid}/refresh-profile").json()["followers_count"] == 1240
    r = api.patch(f"/api/channels/{cid}", {"timezone": "Asia/Kolkata", "daily_limit": 3, "paused": True}).json()
    assert r["timezone"] == "Asia/Kolkata" and r["daily_limit"] == 3 and r["paused"]
    assert api.patch(f"/api/channels/{cid}", {"timezone": "Mars/Base"}).status_code == 400
    assert api.post(f"/api/channels/{cid}/slots/preset", {"preset": "weekdays-3"}).json()["slots"][0] == {"weekday": 0, "time": "08:30"}
    assert len(api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": 2, "time": "7:05"}]}).json()["slots"]) == 1
    assert api.put(f"/api/channels/{cid}/slots", {"slots": [{"weekday": 9, "time": "25:00"}]}).status_code == 400
    assert api.post(f"/api/channels/{cid}/refresh-token").status_code == 200
    assert api.delete(f"/api/channels/{cid}").status_code == 200
    assert api.get("/api/channels").json() == []


# ------------------------------------------------------------------ media
def test_media_upload_jpeg_copy_and_edge_serving(api, edge, core, image_file):
    m = upload(api, image_file)[0]
    assert m["kind"] == "image" and m["width"] == 1080
    assert api.get(m["url"]).status_code == 200
    assert m["public_url"] is None  # no tunnel yet
    api.put("/api/settings", {"public_base_url": "https://edge.example"})
    m2 = api.get("/api/media").json()[0]
    assert m2["jpeg_public_url"].endswith(".jpg") and m2["public_url"].startswith("https://edge.example/m/")
    path = urlparse(m2["jpeg_public_url"]).path
    r = edge.get(path)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert edge.head(path).status_code == 200
    token = path.split("/")[2]
    assert edge.get(f"/m/{token}/other-file.png").status_code == 404     # only this media's own files
    assert edge.get(f"/m/wrongtoken/{path.split('/')[3]}").status_code == 404
    assert edge.get("/api/channels").status_code == 404                  # the dashboard is NOT on the edge
    assert api.put("/api/settings", {"public_base_url": "http://insecure"}).status_code == 400


def test_bulk_zip_upload_and_bad_files(api, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(3):
            p = make_image(tmp_path / f"s{i}.png")
            z.write(p, f"folder/{i:02d}.png")
        z.writestr("__MACOSX/._junk", "x")
        z.writestr("notes.txt", "ignore me")
    r = api.post("/api/media", files={"files": ("batch.zip", buf.getvalue())})
    assert [m["filename"] for m in r.json()["media"]] == ["00.png", "01.png", "02.png"]
    r = api.post("/api/media", files={"files": ("evil.exe", b"MZ")})
    assert r.json()["media"] == [] and "Unsupported" in r.json()["errors"][0]


# ------------------------------------------------------------------ posts
def test_create_validate_schedule_and_queue_views(api, channel, image_file):
    cid = channel["id"]
    m = upload(api, image_file)[0]
    v = api.post("/api/validate", {"caption": "x" * 600, "channel_ids": [cid]}).json()
    assert v["issues"][cid][0]["code"] == "caption_too_long"
    bad = api.post("/api/posts", {"caption": "x" * 600, "channel_ids": [cid], "when": "queue"})
    assert bad.status_code == 422 and bad.json()["detail"]["issues"][cid]
    assert api.post("/api/posts", {"caption": "x" * 600, "channel_ids": [cid], "when": "draft"}).status_code == 200  # drafts may be incomplete
    p = api.post("/api/posts", {"caption": "Hello #AI", "media_ids": [m["id"]], "channel_ids": [cid], "when": "queue", "tags": ["news"]}).json()
    t = p["targets"][0]
    assert t["status"] == "scheduled" and t["scheduled_local"] and t["tags"] == ["news"]
    assert api.get("/api/posts?status=scheduled&tag=news").json()[0]["id"] == t["id"]
    assert api.get("/api/posts?status=draft").json()[0]["status"] == "draft"
    got = api.get(f"/api/targets/{t['id']}").json()
    assert got["issues"] == [] and got["media"][0]["id"] == m["id"]
    # edit caption + reschedule to a custom time
    r = api.patch(f"/api/targets/{t['id']}", {"caption": "Edited", "when": "custom", "scheduled_at": future(120)}).json()
    assert r["caption"] == "Edited" and r["schedule_mode"] == "custom"
    # publish now through the worker
    r = api.post(f"/api/targets/{t['id']}/publish-now").json()
    assert r["status"] == "sent" and r["permalink"]
    assert api.patch(f"/api/targets/{t['id']}", {"when": "queue"}).status_code == 409  # already sent
    ev = api.get(f"/api/targets/{t['id']}/events").json()
    assert any("Sending" in e["message"] for e in ev)


def test_multi_channel_per_network_overrides(api):
    a = api.post("/api/connect/fake/token", {"access_token": "t-aaaa", "username": "one"}).json()
    b = api.post("/api/connect/fake/token", {"access_token": "t-bbbb", "username": "two"}).json()
    p = api.post("/api/posts", {"caption": "shared", "channel_ids": [a["id"], b["id"]], "when": "draft",
                                "channels": [{"channel_id": b["id"], "caption": "custom for two", "options": {"first_comment": "hi"}}]}).json()
    caps = {t["channel"]["username"]: t["caption"] for t in p["targets"]}
    assert caps == {"one": "shared", "two": "custom for two"}
    assert [t for t in p["targets"] if t["channel"]["username"] == "two"][0]["options"]["first_comment"] == "hi"
    # channel references by provider:username also work (CLI / agents)
    p2 = api.post("/api/posts", {"caption": "x", "channel_ids": ["fake:one"], "when": "draft"}).json()
    assert p2["targets"][0]["channel"]["username"] == "one"
    assert api.post("/api/posts", {"caption": "x", "channel_ids": ["fake"], "when": "draft"}).status_code == 400  # ambiguous


def test_retry_duplicate_mark_posted_bulk_and_reorder(api, channel):
    cid = channel["id"]
    f = api.post("/api/posts", {"caption": "FAIL", "channel_ids": [cid], "when": "now"}).json()["targets"][0]
    api.post("/api/worker/tick")
    assert api.get(f"/api/targets/{f['id']}").json()["status"] == "failed"
    assert api.post(f"/api/targets/{f['id']}/retry").json()["status"] == "failed"  # still fails, but retried
    other = api.post("/api/connect/fake/token", {"access_token": "t-zzzz", "username": "other"}).json()
    d = api.post(f"/api/targets/{f['id']}/duplicate", {"channel_ids": [other["id"]]}).json()
    assert d["targets"][0]["channel"]["username"] == "other" and d["targets"][0]["status"] == "draft"
    n = api.post("/api/posts", {"caption": "manual", "channel_ids": [cid], "when": "now", "options": {"posting_method": "notify"}}).json()["targets"][0]
    api.post("/api/worker/tick")
    assert api.post(f"/api/targets/{n['id']}/mark-posted", {"permalink": "https://x"}).json()["status"] == "sent"
    ids = [api.post("/api/posts", {"caption": f"q{i}", "channel_ids": [cid], "when": "queue"}).json()["targets"][0]["id"] for i in range(3)]
    before = {t["id"]: t["scheduled_at"] for t in api.get(f"/api/posts?channel_id={cid}&status=scheduled").json()}
    after = api.post(f"/api/queue/{cid}/order", {"ids": list(reversed(ids))}).json()
    assert [t["id"] for t in after] == list(reversed(ids))
    assert sorted(t["scheduled_at"] for t in after) == sorted(before.values())  # same slots, new order
    assert api.post(f"/api/queue/{cid}/order", {"ids": ids[:1]}).status_code == 400
    assert api.post("/api/bulk", {"action": "tag", "ids": ids, "tag": "yc"}).json()["count"] == 3
    assert api.post("/api/bulk", {"action": "draft", "ids": ids[:2]}).json()["count"] == 2
    assert api.post("/api/bulk", {"action": "delete", "ids": ids}).json()["count"] == 3


# ------------------------------------------------------------------ ideas, community, insights, settings
def test_ideas_board(api):
    i = api.post("/api/ideas", {"title": "MoE explainer", "column": "todo"}).json()
    assert api.patch(f"/api/ideas/{i['id']}", {"column": "done"}).json()["column"] == "done"
    cols = api.put("/api/idea-columns", {"columns": [{"id": "todo", "label": "Backlog"}, {"label": "Review"}]})
    assert [c["id"] for c in cols.json()] == ["todo", "review"]
    assert api.delete(f"/api/ideas/{i['id']}").status_code == 200 and api.get("/api/ideas").json()["ideas"] == []


def test_community_sync_reply_resolve(api, channel):
    assert api.post("/api/comments/sync").json()["new"] == 3   # 2 demo comments + 1 demo mention
    c = api.get("/api/comments?status=open").json()
    assert {x["author"] for x in c} == {"ai_fan", "dev_daily"}
    r = api.post(f"/api/comments/{c[0]['id']}/reply", {"text": "Thanks!"}).json()
    assert r["status"] == "replied"
    assert api.get("/api/comments?status=replied").json()[0]["my_replies"][0]["text"] == "Thanks!"
    assert api.post(f"/api/comments/{c[1]['id']}/resolve").json()["status"] == "resolved"
    assert api.post(f"/api/comments/{c[1]['id']}/reopen").json()["status"] == "open"
    assert api.post(f"/api/comments/{c[1]['id']}/reply", {"text": " "}).status_code == 400


def test_insights_home_notifications_csv(api, channel):
    api.post("/api/posts", {"caption": "x", "channel_ids": [channel["id"]], "when": "now"})
    api.post("/api/worker/tick")
    t = api.get("/api/posts?status=sent").json()[0]
    api.post(f"/api/targets/{t['id']}/refresh-insights")
    d = api.get("/api/insights?days=7").json()
    assert d["kpis"]["posts"] == 1 and d["kpis"]["views"] > 0 and d["top_posts"][0]["id"] == t["id"]
    assert api.get("/api/insights?days=mtd").status_code == 200
    csv = api.get("/api/insights/export.csv?days=7")
    assert csv.headers["content-type"].startswith("text/csv") and "engagement_rate" in csv.text
    h = api.get("/api/home").json()
    assert h["sent_this_week"] == 1 and h["weekly_goal"] == 14
    n = api.get("/api/notifications").json()
    assert any(x["kind"] == "sent" for x in n)
    api.post("/api/notifications/read")
    assert api.get("/api/notifications?unread=true").json() == []


def test_settings_and_library(api):
    s = api.put("/api/settings", {"hashtag_limit": 99, "cta_handle": "@huhbyte", "evil_key": 1}).json()
    assert s["hashtag_limit"] == 30 and "evil_key" not in s
    assert api.get("/api/library/templates").json()[0]["name"] == "Launch"
    items = api.put("/api/library/hashtag_groups", {"items": [{"name": "YC", "text": "#YC"}]}).json()
    assert items[0]["id"] and api.get("/api/library/hashtag_groups").json()[0]["text"] == "#YC"
    assert api.get("/api/library/nope").status_code == 404


# ------------------------------------------------------------------ agent tokens, approvals, audit
def test_scoped_tokens_approval_rate_limit_and_audit(app, api, channel, core):
    read = api.post("/api/tokens", {"name": "reader", "scopes": ["read"]}).json()["token"]
    agent = api.post("/api/tokens", {"name": "agent", "scopes": ["read", "write", "publish"], "require_approval": True}).json()["token"]
    direct = api.post("/api/tokens", {"name": "pipeline", "scopes": ["read", "write", "publish"], "require_approval": False, "rate_per_min": 3}).json()["token"]
    c = TestClient(app)
    H = lambda t: {"Authorization": f"Bearer {t}"}
    assert c.get("/api/channels", headers=H("hq_bogus")).status_code == 401
    assert c.get("/api/channels", headers=H(read)).status_code == 200
    assert c.post("/api/posts", json={"caption": "x", "channel_ids": [channel["id"]], "when": "draft"}, headers=H(read)).status_code == 403
    assert c.post("/api/tokens", json={}, headers=H(agent)).status_code == 403            # admin only
    # approval-required token: scheduling becomes pending_approval
    r = c.post("/api/posts", json={"caption": "agent post", "channel_ids": [channel["id"]], "when": "now"}, headers=H(agent)).json()
    tid = r["targets"][0]["id"]
    assert r["targets"][0]["status"] == "pending_approval" and r["targets"][0]["approval"]["requested_by"] == "agent"
    assert c.post(f"/api/targets/{tid}/approve", headers=H(agent)).status_code == 403    # agents can't approve
    Worker(core).tick()
    assert api.get(f"/api/targets/{tid}").json()["status"] == "pending_approval"           # nothing went out
    assert api.post(f"/api/targets/{tid}/approve").json()["status"] == "scheduled"
    Worker(core).tick()
    assert api.get(f"/api/targets/{tid}").json()["status"] == "sent"
    r2 = c.post("/api/posts", json={"caption": "nope", "channel_ids": [channel["id"]], "when": "queue"}, headers=H(agent)).json()
    assert api.post(f"/api/targets/{r2['targets'][0]['id']}/reject").json()["status"] == "draft"
    # direct token schedules straight away, and is rate limited
    r3 = c.post("/api/posts", json={"caption": "pipeline", "channel_ids": [channel["id"]], "when": "queue"}, headers=H(direct))
    assert r3.json()["targets"][0]["status"] == "scheduled" and r3.json()["targets"][0]["source"] == "api"
    codes = [c.get("/api/channels", headers=H(direct)).status_code for _ in range(4)]
    assert 429 in codes
    # tool allow-list
    tool_tok = api.post("/api/tokens", {"name": "tools", "scopes": ["read"], "tools": ["list_channels"]}).json()["token"]
    assert c.get("/api/channels", headers={**H(tool_tok), "X-HuhQ-Tool": "list_channels"}).status_code == 200
    assert c.get("/api/posts", headers={**H(tool_tok), "X-HuhQ-Tool": "list_queue"}).status_code == 403
    audit = api.get("/api/audit").json()
    assert any(a["actor"] == "agent" and a["path"] == "/api/posts" for a in audit)
    assert any(a["detail"] == "approved" for a in audit)
    toks = api.get("/api/tokens").json()["tokens"]
    assert all("token_hash" not in t for t in toks) and {t["name"] for t in toks} >= {"reader", "agent", "pipeline"}
    with core.db.session() as s:
        assert s.query(AuditLog).count() >= 5


def test_env_token_has_full_access(app, core, channel):
    core.settings.api_token = "env-secret-token"
    c = TestClient(app)
    r = c.post("/api/posts", json={"caption": "x", "channel_ids": [channel["id"]], "when": "now"}, headers={"Authorization": "Bearer env-secret-token"})
    assert r.json()["targets"][0]["status"] == "scheduled"


def test_openapi_documents_the_api(api):
    spec = api.get("/api/openapi.json").json()
    for p in ("/api/posts", "/api/channels", "/api/media", "/api/comments", "/api/insights", "/api/tokens"):
        assert p in spec["paths"]
