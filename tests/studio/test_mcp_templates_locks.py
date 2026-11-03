"""MCP prompts/resources, one update_brand_kit, custom templates, the cover option and the brand locks (2 Nov 2026).

B1: two tools were named update_brand_kit (the kit library one needed kit_id; the client brain's patched the house kit) and
dispatch picked one silently. B2-B4: unlocked read-modify-write on the brand extras, a double write in activate(), a
check-then-insert HuhByte seed and unlocked rate-limit dicts. Custom templates: the owner wants the agent to build new ones.
"""
import json
import threading
from collections import Counter

from studio import brand_kits, custom_templates, mcp, userlocks

from .conftest import agent_token, mcp_call


def rpc(client, tok, method, params=None, mid=1):
    r = client.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                    json={"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
    assert r.status_code == 200, r.text
    return r.json()


FUNDING = {"name": "Funding announcement", "about": "A startup's raise: amount, round, investors, what it's for.", "layout": "editorial",
           "fields": [{"key": "company", "label": "Company", "sample": "Acme AI"}, {"key": "amount", "sample": "$20M"},
                      {"key": "round", "sample": "Series A"}, {"key": "investors", "sample": ["Sequoia", "Y Combinator"]},
                      {"key": "use", "sample": "Hiring engineers and opening a Dublin office"}],
           "slides": [{"type": "front", "kicker": "Funding", "title": "{company} raises {amount}", "sub": "{round}"},
                      {"type": "stat", "title": "{amount}", "body": "{round} led by {investors}", "tag": "The round"},
                      {"type": "bullets", "title": "Backed by", "items": ["{investors}"]},
                      {"type": "story", "title": "What it's for", "body": "{use}", "tag": "Next"}]}


def test_tool_names_are_unique():
    c = Counter(t["name"] for t in mcp.TOOLS)
    assert [n for n, k in c.items() if k > 1] == []


def test_update_brand_kit_without_kit_id_changes_the_active_kit(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _, made = mcp_call(human, tok, "create_brand_kit", {"name": "Beta", "colors": {"background": "#FFFFFF", "primary": "#2563EB"},
                                                        "activate": True})
    res, up = mcp_call(human, tok, "update_brand_kit", {"colors": {"primary": "#FF5A1F"}, "end_card": {"cta": "Follow Beta"}})
    assert not res["isError"], up
    assert up["id"] == made["id"] and up["active"]
    assert human.get("/api/brand-studio").json()["kit"]["end_card"]["cta"] == "Follow Beta"
    tools = {t["name"]: t for t in rpc(human, tok, "tools/list")["result"]["tools"]}
    assert "kit_id" not in tools["update_brand_kit"]["inputSchema"].get("required", [])


def test_prompts_and_resources(human):
    tok = agent_token(human)
    init = rpc(human, tok, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    caps = init["result"]["capabilities"]
    assert "prompts" in caps and "resources" in caps
    names = [p["name"] for p in rpc(human, tok, "prompts/list")["result"]["prompts"]]
    assert {"launch_video_in_my_brand", "product_hunt_to_reel_draft", "carousel_from_link", "one_take_clips", "new_template"} <= set(names)
    g = rpc(human, tok, "prompts/get", {"name": "product_hunt_to_reel_draft", "arguments": {"product_hunt_url": "https://www.producthunt.com/products/x"}})
    txt = g["result"]["messages"][0]["content"]["text"]
    assert "find_video" in txt and "mode='draft'" in txt
    assert rpc(human, tok, "prompts/get", {"name": "carousel_from_link", "arguments": {}})["error"]["code"] == -32602
    assert rpc(human, tok, "prompts/get", {"name": "nope"})["error"]["code"] == -32602
    uris = [r["uri"] for r in rpc(human, tok, "resources/list")["result"]["resources"]]
    for u in uris:
        out = rpc(human, tok, "resources/read", {"uri": u})
        assert "result" in out, (u, out)
        assert out["result"]["contents"][0]["text"]
    kit = json.loads(rpc(human, tok, "resources/read", {"uri": "huhqueue://brand-kit/active"})["result"]["contents"][0]["text"])
    assert "kits" in kit
    assert rpc(human, tok, "resources/read", {"uri": "file:///etc/passwd"})["error"]["code"] == -32602
    assert rpc(human, tok, "resources/templates/list")["result"]["resourceTemplates"] == []


def test_custom_template_create_render_ledger_and_delete(human):
    tok = agent_token(human, policy="trusted", cap=50)
    res, t = mcp_call(human, tok, "create_template", FUNDING)
    assert not res["isError"], t
    assert t["id"] == "custom:funding-announcement" and t["custom"]
    lst = human.get("/api/templates").json()["templates"]
    assert any(x["id"] == t["id"] and x["custom"] for x in lst)
    assert any(x["id"] == t["id"] for x in human.get("/api/brand-studio").json()["templates"])  # the Templates tab's list
    _, r = mcp_call(human, tok, "render_template", {"template": t["id"], "services": ["instagram"],
                                                     "data": {"company": "Nova", "amount": "$5M", "round": "Seed", "investors": ["A16z"]}})
    slides = r["channels"][0]["slides"]
    assert len(slides) == 5  # 4 slides + the end card last
    pv = human.post("/api/brand-studio/preview", json={"template": t["id"], "service": "instagram", "max": 1}).json()
    assert pv["slides"][0].startswith("data:image/jpeg")
    rules = mcp_call(human, tok, "get_brand")[1]["rules"]  # the Style Ledger
    assert any(x["kind"] == "template" and "funding-announcement" in x["rule"] for x in rules)
    _, u = mcp_call(human, tok, "update_template", {"template": t["id"], "layout": "bold_news"})
    assert u["layout"] == "bold_news"
    _, d = mcp_call(human, tok, "delete_template", {"template": t["id"]})
    assert d["deleted"] == t["id"]
    assert not any(x["id"] == t["id"] for x in human.get("/api/templates").json()["templates"])


def test_custom_template_is_sanitised_and_capped(human, monkeypatch):
    tok = agent_token(human, policy="trusted", cap=100)
    res, t = mcp_call(human, tok, "create_template", {"name": "<script>Job</script> opening\x00", "slides": [
        {"type": "front", "title": "{role} at {company}", "evil": "x"}, {"type": "exec", "title": "no"}]})
    assert not res["isError"], t
    assert "<" not in t["name"] and "\x00" not in t["name"]
    assert len(t["slides"]) == 1 and "evil" not in t["slides"][0]
    assert {f["key"] for f in t["fields"]} == {"role", "company"}  # placeholders get declared, never printed bare
    res, msg = mcp_call(human, tok, "create_template", {"name": "x", "slides": [{"type": "nope"}]})
    assert res["isError"] and "slide" in msg
    res, msg = mcp_call(human, tok, "create_template", {"name": "x", "layout": "brutalist", "slides": [{"type": "front"}]})
    assert res["isError"] and "layout" in msg
    monkeypatch.setattr(custom_templates, "MAX_CUSTOM", 2)
    mcp_call(human, tok, "create_template", {"name": "two", "slides": [{"type": "front", "title": "a"}]})
    res, msg = mcp_call(human, tok, "create_template", {"name": "three", "slides": [{"type": "front", "title": "a"}]})
    assert res["isError"] and "Up to 2" in msg


def test_render_in_another_kit_does_not_switch_the_active_one(human):
    tok = agent_token(human, policy="trusted", cap=50)
    _, a = mcp_call(human, tok, "create_brand_kit", {"name": "Alpha", "colors": {"background": "#000000", "primary": "#FF0000"}, "activate": True})
    _, b = mcp_call(human, tok, "create_brand_kit", {"name": "Bravo", "colors": {"background": "#FFFFFF", "primary": "#0000FF"}})
    _, r = mcp_call(human, tok, "render_template", {"template": "breaking", "services": ["instagram"], "kit_id": b["id"]})
    assert r["kit"]["id"] == b["id"] and r["kit"]["name"] == "Bravo"
    assert human.get("/api/brand-kits").json()["active_id"] == a["id"]


def test_parallel_kit_writes_lose_nothing_and_seed_once(human, app):
    """Three sessions at once: switching kits, saving brand fields and listing kits (which seeds HuhByte) never lose a write
    and never make a second HuhByte kit."""
    tok = agent_token(human, policy="trusted", cap=500)
    _, k1 = mcp_call(human, tok, "create_brand_kit", {"name": "K1"})
    _, k2 = mcp_call(human, tok, "create_brand_kit", {"name": "K2"})
    errors = []

    def worker(i):
        try:
            for j in range(4):
                mcp_call(human, tok, "set_active_brand_kit", {"kit_id": (k1, k2)[(i + j) % 2]["id"]}, mid=i * 100 + j)
                mcp_call(human, tok, "list_brand_kits", mid=i * 100 + j + 50)
                mcp_call(human, tok, "create_template", {"name": f"T{i}-{j}", "slides": [{"type": "front", "title": "x"}]}, mid=i * 100 + j + 70)
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))
    th = [threading.Thread(target=worker, args=(i,)) for i in range(3)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert not errors, errors
    kits = human.get("/api/brand-kits").json()
    names = [k["name"] for k in kits["kits"]]
    assert names.count("HuhByte AI") == 1 and names.count("K1") == 1 and names.count("K2") == 1
    assert kits["active_id"] in (k1["id"], k2["id"])
    tpl = [t["name"] for t in human.get("/api/templates").json()["templates"] if t.get("custom")]
    assert len(tpl) == 12 and len(set(tpl)) == 12  # no lost and no duplicate template rows


def test_bs_save_extra_lands_in_one_write(human):
    """activate() used to write the extras twice; now active_kit, example and caption style land with the kit fields."""
    house = human.app.state.house if hasattr(human.app.state, "house") else None
    tok = agent_token(human, policy="trusted", cap=50)
    _, k = mcp_call(human, tok, "create_brand_kit", {"name": "Solo", "caption_style": "news"})
    _, act = mcp_call(human, tok, "set_active_brand_kit", {"kit_id": k["id"]})
    assert act["applied"]
    assert human.get("/api/brand-kits").json()["active_id"] == k["id"]
    del house


def test_rate_hit_is_atomic():
    bucket = {}
    ok = []

    def go():
        ok.append(userlocks.rate_hit(bucket, "u1", 1000.0, 10))
    th = [threading.Thread(target=go) for _ in range(40)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert ok.count(True) == 10 and len(bucket["u1"]) == 10


def test_kit_cap_holds_under_parallel_creates(human, monkeypatch):
    monkeypatch.setattr(brand_kits, "MAX_KITS", 4)
    tok = agent_token(human, policy="trusted", cap=100)
    mcp_call(human, tok, "list_brand_kits")  # seeds HuhByte AI (1 of 4)

    def make(i):
        mcp_call(human, tok, "create_brand_kit", {"name": f"P{i}"}, mid=900 + i)
    th = [threading.Thread(target=make, args=(i,)) for i in range(8)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    assert len(human.get("/api/brand-kits").json()["kits"]) == 4


def test_cover_option_on_edit_video_and_create_package(human, monkeypatch):
    from studio import mcp_client_brain as cb
    tok = agent_token(human, policy="trusted", cap=50)
    tools = {t["name"]: t for t in rpc(human, tok, "tools/list")["result"]["tools"]}
    assert "cover" in tools["edit_video"]["inputSchema"]["properties"]
    assert "cover" in tools["create_package"]["inputSchema"]["properties"]
    spec = cb._video_spec.__wrapped__ if hasattr(cb._video_spec, "__wrapped__") else None
    del spec
    chans = human.get("/api/channels").json()
    ig = next((c for c in (chans if isinstance(chans, list) else chans.get("channels", [])) if c.get("service") == "instagram"), None)
    key = ig["id"] if ig else "instagram"
    res, pk = mcp_call(human, tok, "create_package", {"captions": {key: "A reel"}, "media_urls": ["https://hq.test/m/abc.mp4"],
                                                      "format": "reel", "cover": {"url": "https://hq.test/m/cover.jpg", "frame_s": 1.5}})
    assert not res["isError"], pk
    run = mcp_call(human, tok, "get_run", {"run_id": pk["run_id"]})[1]
    p0 = run["result"]["packages"][0]
    assert p0["cover_url"] == "https://hq.test/m/cover.jpg"
    if p0["service"] in ("instagram", "tiktok", "pinterest"):
        assert p0["options"]["thumbnail_offset_ms"] == 1500
    res, msg = mcp_call(human, tok, "create_package", {"captions": {key: "x"}, "cover": {"url": "http://evil/x.jpg"}})
    assert res["isError"] and "https" in msg
