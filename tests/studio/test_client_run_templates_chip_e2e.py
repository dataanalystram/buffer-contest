"""6 Nov 2026 (owner): on a run made by an AI app over MCP, the reply chip "What templates can you use?" posted a run
message, and the page's showRun + watch redrew and restarted the preview. On a non-agent run the chip now answers on the
page (the template list), with no message, no watch and no redraw."""
import time

from studio import house

from .test_makepost_nov3_fixes_e2e import _ctx, _open, live  # noqa: F401  (the live fixture)

A = house.Actor("acc_1")
TPL_Q = "What templates can you use?"


def _client_run(base, app):
    tok = app.state.studio.create_agent_token(A, "Claude", policy="review", daily_cap=50)["token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "create_package", "arguments": {"title": "Click: managed agents API",
                                                              "captions": {"instagram": "Click ships a managed agents API."}}}}
    from fastapi.testclient import TestClient
    r = TestClient(app, base_url="https://hq.test").post("/mcp", headers=hdr, json=rpc)
    assert r.status_code == 200, r.text
    import json
    out = json.loads(r.json()["result"]["content"][0]["text"])
    rid = out["run_id"]
    assert app.state.house.run(A, rid)["kind"] == "client"
    return rid


def test_templates_chip_on_an_ai_app_run_answers_locally(live, chromium, close_contexts):  # noqa: F811
    rid = _client_run(live["base"], live["app"])
    ctx, page, errors = _ctx(chromium, live["base"], 1280)
    posts = []
    page.on("request", lambda q: posts.append(q.url) if q.method == "POST" and "/api/agent/runs/" in q.url else None)
    _open(page, live["base"], rid)
    chip = page.locator(f"[data-testid=reply-chip][data-say='{TPL_Q}']")
    chip.wait_for()
    page.evaluate("""() => { document.querySelector('[data-testid=package]').dataset.mark = 'kept';
      window.__watched = 0; const w = window.watch; window.watch = (...a) => { window.__watched++; return w && w(...a); }; }""")
    msgs = page.get_by_test_id("thread-msg").count()
    chip.click()
    dlg = page.get_by_test_id("tpl-list-dialog")
    dlg.wait_for()
    page.get_by_test_id("tpl-list-item").first.wait_for()
    assert page.get_by_test_id("tpl-list-item").count() >= 5
    time.sleep(1.5)  # anything a posted message would start has had time to happen
    assert posts == [], posts                                                     # no run message, no template switch
    assert page.evaluate("window.__watched") == 0                                 # no watch
    assert page.locator("[data-testid=package][data-mark=kept]").count() == 1    # the preview was not redrawn
    assert page.get_by_test_id("thread-msg").count() == msgs
    page.get_by_test_id("tpl-list-close").click()
    assert page.get_by_test_id("tpl-list-dialog").count() == 0
    assert errors == [], errors
