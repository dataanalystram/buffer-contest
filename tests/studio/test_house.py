"""HuhQueue: ledger, brand check, renders, the agent loop end to end (sample workspace, no network)."""
import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import brand, drafter, house, llm, render, sources
from studio.app import create_app
from studio.config import load_settings

ARTICLE = {"url": "https://news.example.com/agents", "title": "Acme launches an agent that books travel with one approval",
           "description": "The agent plans trips end to end and asks once before paying.",
           "image": "", "site": "Example News",
           "paragraphs": ["Acme said the agent works in 12 countries at launch and is free for Plus users this month.",
                          "It books flights and hotels, then waits for a single approval before it pays with a saved card."]}


def jpeg(w=1200, h=800, color=(30, 90, 160)):
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    s = load_settings(db_path=str(tmp_path / "h.db"), public_url="https://hs.test", secure_cookies=False)
    s.extra.update(sync_agent=True, render_worker="none")
    monkeypatch.setattr(sources, "search", lambda q, feeds, http=None, limit=12: [
        {"title": ARTICLE["title"], "url": ARTICLE["url"], "source": "news.example.com", "published": 0, "via": "bing_news"}])
    monkeypatch.setattr(sources, "bing_news", lambda q, http=None, limit=10: [
        {"title": "Acme agent books travel with one approval", "url": "https://other.example.org/a", "source": "other.example.org",
         "published": 0, "via": "bing_news"}])
    monkeypatch.setattr(sources, "google_news", lambda q, http=None, limit=10: [])
    monkeypatch.setattr(drafter, "fetch_article", lambda url, http=None, check_host=True: dict(ARTICLE))
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)
    c = TestClient(create_app(s))
    c.get("/demo", follow_redirects=False)
    c.headers["x-hq-csrf"] = c.get("/api/session").json()["csrf"]
    return c


# ---------------------------------------------------------------- ledger
@pytest.mark.parametrize("text,ty", [
    ("never say game-changer", "banned_words"), ("no emojis please", "no_emoji"), ("max 2 hashtags", "max_hashtags"),
    ("logo top left", "logo_position"), ("the logo is too big", "logo_scale"), ("drop the end card", "end_card"),
    ("headlines in sentence case", "headline_case"), ("use #FF5A1F as the accent color", "accent_color"),
    ("always end with — Ram", "ends_with"), ("less hype", "no_exclamation"), ("text is hard to read", "overlay"),
])
def test_corrections_become_rules(text, ty):
    assert ty in {r["check"]["type"] for r in brand.parse_correction(text)}


def test_unparseable_correction_adds_nothing():
    assert brand.parse_correction("hmm, not sure about this one") == []


def test_brand_check_cites_the_rule_and_fixer_repairs_it():
    rules = [dict(r, id=i, active=True) for i, r in enumerate(brand.parse_correction("never say game-changer. max 1 hashtags. no emojis"))]
    bad = "This is a game-changer 🚀 #ai #agents"
    res = brand.brand_check(bad, None, rules, brand.DEFAULT_KIT)
    assert not res["pass"] and {f["rule"] for f in res["failed"]} == {r["rule"] for r in rules}
    fixed = brand.apply_text_rules(bad, rules)
    assert brand.brand_check(fixed, None, rules, brand.DEFAULT_KIT)["pass"], fixed


def test_contrast_and_frame_builtins():
    p = brand.visual_params(brand.DEFAULT_KIT, [])
    _, meta = render.news_card("Hello world", brand.DEFAULT_KIT, p, image=jpeg(), ratio="4:5", credit="x.com")
    res = brand.brand_check("ok", meta, [], brand.DEFAULT_KIT)
    assert res["pass"] and meta["width"] == 1080 and meta["height"] == 1350


def test_visual_rules_drive_the_render():
    rules = [dict(r, id=i, active=True) for i, r in enumerate(brand.parse_correction("logo smaller, bottom left. headlines in sentence case"))]
    p = brand.visual_params(brand.DEFAULT_KIT, rules)
    _, meta = render.news_card("OPENAI SHIPS A THING", brand.DEFAULT_KIT, p, image=jpeg(), ratio="9:16")
    assert meta["params"]["logo_position"] == "bl" and meta["params"]["logo_scale"] == 0.075 and meta["height"] == 1920
    assert brand.brand_check("ok", meta, rules, brand.DEFAULT_KIT)["pass"]


# ---------------------------------------------------------------- agent loop
def test_news_run_end_to_end_with_revision_schedule_and_calendar(client):
    client.post("/api/brand", json={"name": "Huhbyte", "handle": "@huhbyte", "colors": {"accent": "#c9acfa"}})
    r = client.post("/api/agent/runs", json={"query": "travel agents", "format": "carousel"}).json()
    assert r["status"] == "ready", r["steps"]
    keys = [s["key"] for s in r["steps"]]
    assert keys == ["find", "verify", "structure", "format", "render", "caption", "check", "ready"]
    assert "also reported by other.example.org" in r["steps"][1]["detail"]
    res = r["result"]
    assert len(res["media"]) == 3 and all(m["url"].startswith("https://hs.test/m/") for m in res["media"])
    ig = next(p for p in res["packages"] if p["service"] == "instagram")
    assert len(ig["media_urls"]) == 3 and ig["check"]["pass"]
    # media is served publicly for Buffer to fetch
    tok = res["media"][0]["url"].split("/m/")[1]
    got = client.get("/m/" + tok)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert client.get("/m/" + tok, headers={"Range": "bytes=0-9"}).status_code == 206
    # correction -> ledger rules -> re-render with the new params
    v = client.post(f"/api/agent/runs/{r['id']}/revise", json={"correction": "logo top left and smaller, no hashtags"}).json()
    assert {x["check"]["type"] for x in v["learned"]} >= {"logo_position", "logo_scale", "max_hashtags"}
    r2 = client.get(f"/api/agent/runs/{r['id']}").json()
    assert r2["status"] == "ready" and r2["result"]["revisions"][0]["after"] != r2["result"]["revisions"][0]["before"]
    meta = client.app.state.house.media_meta(house.Actor(client.app.state.store.q("SELECT user_id FROM agent_runs")[0]["user_id"]),
                                             r2["result"]["media"][0]["token"])
    assert meta["params"]["logo_position"] == "tl"
    assert all("#" not in p["text"].replace("#c9", "") or not brand.HASHTAG_RE.findall(p["text"]) for p in r2["result"]["packages"])
    rules = client.get("/api/brand").json()["rules"]
    assert any(x["rule"] == "Logo sits top left" for x in rules)
    # hand-off to Buffer (sample workspace stand-in) and the calendar shows it
    out = client.post(f"/api/agent/runs/{r['id']}/packages/{ig['channel_id']}/schedule", json={"mode": "queue"}).json()
    assert out["status"] == "sent_to_buffer"
    again = client.post(f"/api/agent/runs/{r['id']}/packages/{ig['channel_id']}/schedule", json={"mode": "queue"})
    assert again.status_code == 409
    cal = client.get("/api/calendar").json()["items"]
    assert {"buffer", "ready", "approval"} <= {i["kind"] for i in cal}


def test_own_photo_run(client):
    up = client.post("/api/media", json={"name": "me.jpg", "data": base64.b64encode(jpeg(900, 1200)).decode()}).json()
    r = client.post("/api/agent/runs", json={"media_token": up["token"], "brief": "Our new studio is open", "channel_ids": ["demo_ig"]}).json()
    assert r["status"] == "ready", r["steps"]
    assert r["result"]["format"]["format"] == "photo" and r["result"]["packages"][0]["media_urls"]


def test_reel_without_worker_falls_back_to_carousel(client):
    r = client.post("/api/agent/runs", json={"query": "x", "format": "reel", "channel_ids": ["demo_ig"]}).json()
    assert r["status"] == "ready" and r["result"]["format"]["format"] == "carousel"
    assert "worker isn't available" in r["result"]["format"]["why"]


def test_github_worker_roundtrip(client, monkeypatch):
    h = client.app.state.house
    h.worker_mode = "github"
    issued = {}
    monkeypatch.setattr(house.House, "_dispatch_github", lambda self, jid: (issued.setdefault("t", self.issue_job_token(jid)) and True, "204"))
    r = client.post("/api/agent/runs", json={"query": "x", "format": "reel", "channel_ids": ["demo_ig"]}).json()
    assert r["status"] == "rendering"
    jid = r["result"]["render_job"]["id"]
    assert client.get(f"/api/worker/jobs/{jid}").status_code == 403
    assert client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": "jt_wrong"}).status_code == 403
    spec = client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": issued["t"]}).json()
    assert "_job_token" not in spec["spec"]
    assert spec["spec"]["kind"] == "slideshow" and len(spec["urls"]["slides"]) == 2
    meta = {"type": "video", "width": 1080, "height": 1920, "params": spec["spec"]["params"], "has_logo": True, "has_end_card": True}
    ok = client.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": issued["t"]},
                     json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42fake").decode(), "meta": meta}).json()
    assert ok["ok"]
    r2 = client.get(f"/api/agent/runs/{r['id']}").json()
    assert r2["status"] == "ready" and r2["result"]["packages"][0]["media_urls"][0].endswith(".mp4")
    # the token dies with the job
    assert client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": issued["t"]}).status_code == 403


def test_failed_step_is_reported_not_spinning(client, monkeypatch):
    monkeypatch.setattr(sources, "search", lambda *a, **k: [])
    r = client.post("/api/agent/runs", json={"query": "nothing"}).json()
    assert r["status"] == "failed" and r["steps"][0]["status"] == "failed"


# ---------------------------------------------------------------- MCP parity
def test_mcp_agent_tools(client):
    tok = client.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    def call(name, args=None):
        out = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                   "params": {"name": name, "arguments": args or {}}}).json()["result"]
        assert not out["isError"], out["content"][0]["text"]
        return out["structuredContent"]

    names = {t["name"] for t in client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"run_agent", "edit_media", "get_run", "revise", "remember_rule", "brand_check", "get_brand", "schedule_package",
            "list_calendar"} <= names
    run = call("run_agent", {"query": "travel agents", "channel_ids": ["demo_x"]})
    assert run["status"] == "ready"
    call("remember_rule", {"correction": "never say travel"})
    chk = call("brand_check", {"text": "Travel is great"})
    assert not chk["pass"] and chk["failed"][0]["rule"] == "Never use: travel"
    sent = call("schedule_package", {"run_id": run["id"], "channel_id": "demo_x", "mode": "queue"})
    assert sent["status"] == "pending_approval"  # agent tokens still wait for a human


def test_agents_cannot_delete_rules_or_change_ai(client):
    tok = client.post("/api/agent-tokens", json={"name": "bot"}).json()["token"]
    rid = client.post("/api/ledger", json={"correction": "no emojis"}).json()["added"][0]["id"]
    bare = TestClient(client.app)
    assert bare.delete(f"/api/ledger/{rid}", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert bare.post("/api/ai", headers={"Authorization": f"Bearer {tok}"}, json={"provider": "openai", "api_key": "k"}).status_code == 403


# ---------------------------------------------------------------- bring your own key
def test_llm_openai_and_anthropic_shapes():
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.url.path, dict(req.headers)))
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"content": [{"type": "text", "text": '{"ok":true}'}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    oa = {"provider": "openai", "base_url": "https://api.openai.com/v1", "api_key": "k1", "model": "m", "style": "openai"}
    an = {"provider": "anthropic", "base_url": "https://api.anthropic.com/v1", "api_key": "k2", "model": "m", "style": "anthropic"}
    assert llm.chat_json(oa, "s", "u", http=http) == {"ok": True}
    assert llm.chat_json(an, "s", "u", http=http) == {"ok": True}
    assert seen[0][1]["authorization"] == "Bearer k1" and seen[1][1]["x-api-key"] == "k2"


def test_byo_key_is_encrypted_and_never_returned(client, monkeypatch):
    monkeypatch.setattr(llm, "test_key", lambda eng, http=None: "ok")
    monkeypatch.setattr(drafter, "_public", lambda host: True)
    v = client.post("/api/ai", json={"provider": "groq", "api_key": "gsk_secret_1234"}).json()
    assert v["engine"] == "your key" and v["own"]["key_hint"] == "…1234" and "gsk_secret" not in json.dumps(v)
    raw = client.app.state.store.q("SELECT data FROM ai_settings")[0]["data"]
    assert "gsk_secret" not in raw
    assert client.post("/api/ai", json={"remove": True}).json()["own"] is None


def test_kit_from_html():
    html = ('<html><head><title>Acme | Home</title><meta property="og:site_name" content="Acme">'
            '<meta name="theme-color" content="#FF5A1F"><meta name="twitter:site" content="@acme">'
            '<link rel="apple-touch-icon" sizes="180x180" href="/apple.png"></head></html>')
    k = brand.kit_from_html("https://acme.test/", html)
    assert k["name"] == "Acme" and k["handle"] == "acme" and k["theme_color"] == "#FF5A1F"
    assert k["logo_candidates"][0] == "https://acme.test/apple.png"
    assert brand.pick_colors(k["theme_color"], [])["accent"] == "#FF5A1F"


def test_rendered_text_drops_glyphs_the_font_lacks():
    from studio import render
    f = render.font("Anton.ttf", 40)
    assert render.drawable("AI agents 🚀 ship → faster ✨", f) == "AI agents ship -> faster "
    assert render.drawable("Café naïve", render.font("Inter-Regular.ttf", 20)) == "Café naïve"
    # a headline full of emoji renders without tofu and without crashing
    kit = dict(brand.DEFAULT_KIT, name="Bright Desk")
    p = brand.visual_params(kit, [])
    data, meta = render.news_card("🔥🔥 Big news 🧠 today", kit, p)
    assert data[:2] == b"\xff\xd8"


def test_no_my_brand_placeholder(client):
    kit = client.get("/api/brand").json()["kit"]
    assert kit["name"] == "Bright Desk"  # the sample workspace's own name, not "My brand"
    from studio import render
    p = brand.visual_params(dict(brand.DEFAULT_KIT), [])
    _, meta = render.news_card("Headline", dict(brand.DEFAULT_KIT, name=""), p)
    assert meta["has_logo"] is False  # no logo and no name: no wordmark at all
