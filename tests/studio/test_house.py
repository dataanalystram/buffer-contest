"""HuhQueue: ledger, brand check, renders, the agent loop end to end (sample workspace, no network)."""
import base64
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import brand, drafter, house, llm, netguard, reader, render, sources
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
    # the agent brain reads links through reader (every Make a post run goes through it now)
    monkeypatch.setattr(reader, "read_url", lambda url, http=None, x_bearer="": {**ARTICLE, "via": "direct", "post": None, "media": [], "media_note": ""})
    monkeypatch.setattr(reader, "read_article", lambda url, http=None: {**ARTICLE, "url": url})
    monkeypatch.setattr(netguard, "_host_ok", lambda host: True)  # hermetic: no DNS for the example hosts
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
def _tools(run):
    return [st.get("tool") for st in run["steps"]]


def test_news_run_end_to_end_with_reply_schedule_and_calendar(client):
    client.post("/api/brand", json={"name": "Huhbyte", "handle": "@huhbyte", "colors": {"accent": "#c9acfa"}})
    r = client.post("/api/agent/runs", json={"query": "travel agents", "format": "carousel"}).json()
    assert r["status"] == "ready", r["steps"]
    assert r["kind"] == "agent"  # every Make a post run goes through the agent brain (plan -> tools -> check)
    tools = _tools(r)
    assert tools[:3] == ["search_news", "read_url", "extract_facts"] and tools[-1] == "check" and "write_captions" in tools
    facts = next(st for st in r["steps"] if st["tool"] == "extract_facts")
    assert facts["detail"].startswith("Acme launches")  # the headline comes from the story, not the request
    res = r["result"]
    ig = next(p for p in res["packages"] if p["service"] == "instagram")
    assert len(ig["media_urls"]) == 3 and ig["check"]["pass"]
    assert ig["media"] and all(m["type"] == "photo" and m["ratio"] == "4:5" for m in ig["media"])  # a real preview per channel
    # media is served publicly for Buffer to fetch
    tok = ig["media_urls"][0].split("/m/")[1]
    got = client.get("/m/" + tok)
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    assert client.get("/m/" + tok, headers={"Range": "bytes=0-9"}).status_code == 206
    # a reply in the thread -> ledger rules -> re-render with the new params (the one edit box for every run)
    v = client.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "logo top left and smaller, no hashtags"}).json()
    r2 = client.get(f"/api/agent/runs/{v['id']}").json()
    assert r2["status"] == "ready", r2["steps"]
    rules = client.get("/api/brand").json()["rules"]
    assert any(x["rule"] == "Logo sits top left" for x in rules)
    ig2 = next(p for p in r2["result"]["packages"] if p["service"] == "instagram")
    uid = client.app.state.store.q("SELECT user_id FROM agent_runs")[0]["user_id"]
    meta = client.app.state.house.media_meta(house.Actor(uid), ig2["media_urls"][0].split("/m/")[1])
    assert meta["params"]["logo_position"] == "tl"
    # hand-off to Buffer (sample workspace stand-in) and the calendar shows it
    out = client.post(f"/api/agent/runs/{r['id']}/packages/{ig['channel_id']}/schedule", json={"mode": "queue"}).json()
    assert out["status"] == "sent_to_buffer"
    again = client.post(f"/api/agent/runs/{r['id']}/packages/{ig['channel_id']}/schedule", json={"mode": "queue"})
    assert again.status_code == 409
    cal = client.get("/api/calendar").json()["items"]
    assert {"buffer", "ready", "approval"} <= {i["kind"] for i in cal}


def test_own_photo_run_goes_through_the_agent(client):
    up = client.post("/api/media", json={"name": "me.jpg", "data": base64.b64encode(jpeg(900, 1200)).decode()}).json()
    r = client.post("/api/agent/runs", json={"prompt": "Our new studio is open", "media_token": up["token"], "channel_ids": ["demo_ig"]}).json()
    assert r["status"] == "ready", r["steps"]
    assert r["kind"] == "agent" and _tools(r)[0] == "read_upload" and "search_news" not in _tools(r)
    assert r["result"]["source"]["via"] == "upload"
    assert r["result"]["packages"][0]["media_urls"]


def test_reel_without_worker_falls_back_to_slides(client, monkeypatch):
    monkeypatch.setattr(house.House, "local_video_ok", staticmethod(lambda: False))
    r = client.post("/api/agent/runs", json={"query": "x", "format": "reel", "channel_ids": ["demo_ig"]}).json()
    assert r["status"] == "ready", r["steps"]
    pk = r["result"]["packages"][0]
    # no source video at all: NO VIDEO -> NO REEL (owner, 1 Nov 2026), the carousel with the note, never a slideshow
    assert pk["format"] == "carousel" and "No video found" in pk["note"]


def test_github_worker_roundtrip(client, monkeypatch):
    """The worker protocol: one claim-issued token reads the spec once, posts the result once, then dies."""
    h = client.app.state.house
    uid = client.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]
    a = house.Actor(uid)
    slides = [h.put_media(a, jpeg(1080, 1920), "s.jpg", kind="render", meta={"type": "photo"}) for _ in range(2)]
    spec = {"kind": "slideshow", "kit": {}, "params": {"logo_position": "br"}, "ratio": "9:16", "hook": "Acme ships", "logo": "",
            "source": "", "slides": slides}
    jid = client.app.state.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                                   (uid, None, json.dumps(spec), "queued", 0, 0))
    t = h.issue_job_token(jid)
    assert client.get(f"/api/worker/jobs/{jid}").status_code == 403
    assert client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": "jt_wrong"}).status_code == 403
    got = client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": t}).json()
    assert "_job_token" not in got["spec"] and got["spec"]["kind"] == "slideshow" and len(got["urls"]["slides"]) == 2
    assert client.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": t}).status_code == 403  # the spec reads once
    meta = {"type": "video", "width": 1080, "height": 1920, "params": spec["params"], "has_logo": True, "has_end_card": True}
    ok = client.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": t},
                     json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42fake").decode(), "meta": meta}).json()
    assert ok["ok"]
    row = client.app.state.store.q("SELECT status, media_token FROM render_jobs WHERE id=?", (jid,))[0]
    assert row["status"] == "done" and row["media_token"].endswith(".mp4")
    # the token dies with the job
    assert client.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": t}, json={"error": "x"}).status_code == 403


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
    run = call("run_agent", {"query": "travel agents", "channel_ids": ["demo_x"], "server_ai": True})  # the person chose server AI
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
