"""Headless / agent-first guarantees: UI <-> API <-> MCP parity, page context, stable control labels, root docs."""
import json
import re
import sys
from pathlib import Path

import pytest

from huhqueue import actions as reg

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web" / "src"
METHOD = {"get": "GET", "post": "POST", "patch": "PATCH", "put": "PUT", "del": "DELETE"}
CALL = re.compile(r"(?<![\w.])(get|post|patch|put|del)\(\s*([`'\"])(/api/[^`'\"]*)\2")
# UI calls that are deliberately not agent tools: session plumbing and the generic runners themselves.
# Registry actions that are API-only on purpose, with the reason. Anything else a person can do is also an MCP tool.
MCP_EXEMPT = {
    "tunnel.start": "starts a process on the host machine", "tunnel.stop": "stops a process on the host machine",
    "channel.connect": "OAuth needs a person in a browser (agents use channel.connect_token or the returned URL)",
    "media.upload_files": "multipart upload; MCP has the upload_media tool (local path / URL)",
    "report.pdf": "binary file download", "report.csv": "file download (report.summary returns the data as JSON)",
    "report.archive_download": "binary file download",
}
NOT_ACTIONS = {("GET", "/api/auth/status"), ("POST", "/api/actions/{}:run"), ("POST", "/api/assistant/command"),
               ("POST", "/api/assistant/confirm")}


def ui_calls():
    seen = set()
    for f in WEB.rglob("*.jsx"):
        for m in CALL.finditer(f.read_text(encoding="utf-8")):
            path = re.sub(r"\$\{[^}]*\}", "{}", m.group(3).split("?")[0])
            seen.add((METHOD[m.group(1)], path, f.name))
    return seen


def _matches(a, method, path):
    if a.method != method or not a.path:
        return False
    pat = re.escape(re.sub(r"{\w+}", "\0", a.path)).replace("\0", "[^/]+")
    ui = re.escape(path).replace(r"\{\}", "[^/]+")
    concrete = path.replace("{}", "x")
    return bool(re.fullmatch(pat, concrete) or re.fullmatch(ui, re.sub(r"{\w+}", "x", a.path)))


def test_every_ui_api_call_is_a_registry_action_with_an_mcp_tool():
    """CI parity: a UI action without an API action (and an MCP tool, unless it is human/secret-only) fails here."""
    import huhqueue.api.app  # noqa: F401  (registers the actions that live next to their routes)
    calls = ui_calls()
    assert len(calls) > 100
    missing, no_mcp = [], []
    for method, path, f in sorted(calls):
        if (method, re.sub(r"{\w*}", "{}", path)) in NOT_ACTIONS:
            continue
        hits = [a for a in reg.ACTIONS if _matches(a, method, path)]
        if not hits:
            missing.append(f"{f}: {method} {path}")
        elif not any((a.mcp and a.agent_visible()) or a.risk in ("human", "secret") or a.id in MCP_EXEMPT for a in hits):
            no_mcp.append(f"{f}: {method} {path}")
    assert not missing, "UI calls without an API action:\n" + "\n".join(missing)
    assert not no_mcp, "UI calls without an MCP tool:\n" + "\n".join(no_mcp)


def test_mcp_tools_cover_agent_visible_actions():
    import huhqueue.api.app  # noqa: F401
    tools = set(reg.mcp_tool_names())
    hidden = [a.id for a in reg.ACTIONS if a.method and a.agent_visible() and a.mcp and a.tool not in tools]
    assert not hidden
    # mcp=False is only for local-machine actions (tunnel); everything else an agent may do is a tool
    off = {a.id for a in reg.ACTIONS if a.method and a.agent_visible() and not a.mcp}
    assert off <= set(MCP_EXEMPT), off - set(MCP_EXEMPT)


def test_every_control_has_a_stable_testid_or_aria_label():
    sys.path.insert(0, str(Path(__file__).parent))
    from jsx_controls import controls, labelled
    bad = []
    for f in WEB.rglob("*.jsx"):
        text = f.read_text(encoding="utf-8")
        for tag, start, _a, _e, attrs, _sc in controls(text):
            if not labelled(tag, attrs):
                bad.append(f"{f.name}:{text[:start].count(chr(10)) + 1} <{tag}>")
    assert not bad, "controls without data-testid / aria-label:\n" + "\n".join(bad)


def test_page_context_api(api, channel):
    d = api.get("/api/context", params={"page": "/queue?tab=drafts"}).json()
    assert d["route"] == "queue" and d["known"] and d["query"] == {"tab": "drafts"}
    ids = {a["id"] for a in d["actions"]}
    assert {"post.list", "post.schedule"} <= ids
    sched = next(a for a in d["actions"] if a["id"] == "post.schedule")
    assert sched["method"] and sched["path"] and sched["mcp_tool"] == "post_schedule" and sched["confirm"]
    assert "input_schema" in sched and isinstance(sched["required"], list)
    assert d["ids"]["channels"][0]["id"] == channel["id"]
    ch = api.get("/api/context", params={"page": f"/channel/{channel['id']}?tab=publish"}).json()
    assert ch["ids"]["channel"]["id"] == channel["id"] and "post_targets" in ch["ids"]
    assert any(a["id"] == "import.csv" for a in ch["actions"])       # per-channel bulk upload
    s = api.get("/api/context", params={"page": "/settings"}).json()
    secret = [a for a in s["actions"] if a["risk"] == "secret"]
    assert secret and all(a["mcp_tool"] is None and not a["agent_allowed"] for a in secret)
    assert not api.get("/api/context", params={"page": "/nope"}).json()["known"]


@pytest.mark.parametrize("route", ["home", "queue", "calendar", "ideas", "community", "insights", "library", "contacts",
                                   "growth", "channels", "settings"])
def test_every_page_has_context_and_actions(api, route):
    d = api.get("/api/context", params={"page": f"/{route}"}).json()
    assert d["known"] and d["actions"], route


def test_context_needs_auth(app):
    from fastapi.testclient import TestClient
    assert TestClient(app).get("/api/context?page=/home").status_code == 401


def test_index_embeds_agent_context_catalog(app):
    from fastapi.testclient import TestClient
    html = TestClient(app).get("/").text
    m = re.search(r'<script type="application/json" id="agent-context">(.*?)</script>', html, re.S)
    assert m, "agent-context script missing"
    cat = json.loads(m.group(1))
    assert cat["kind"] == "agent-context" and {p["route"] for p in cat["pages"]} >= {"/home", "/queue", "/channel/{channel_id}"}
    assert "ids" not in cat   # public page: no private data before login


def test_root_openapi_and_llms(app):
    from fastapi.testclient import TestClient
    c = TestClient(app)
    spec = c.get("/openapi.json").json()
    assert spec["openapi"].startswith("3") and "/api/context" in spec["paths"]
    assert spec == c.get("/api/openapi.json").json()
    txt = c.get("/llms.txt").text
    assert "/api/context" in txt and "/openapi.json" in txt and "agent-context" in txt


def test_context_action_runs_through_safeguards(api, app):
    """Tokens reach /api/context through the same scope + audit path as every other call."""
    from fastapi.testclient import TestClient
    tok = api.post("/api/tokens", {"name": "reader", "scopes": ["read"]}).json()["token"]
    c = TestClient(app, headers={"Authorization": f"Bearer {tok}"})
    assert c.get("/api/context?page=/home").status_code == 200
    r = c.post("/api/actions/context.get:run", json={"input": {"page": "/home"}})
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["output"]["route"] == "home"
    audit = api.get("/api/audit").json()
    rows = audit if isinstance(audit, list) else audit.get("items") or audit.get("rows") or []
    assert any("context" in json.dumps(x) for x in rows)
