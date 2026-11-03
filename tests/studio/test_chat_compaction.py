"""Make a post as a chat (owner, 3 Nov 2026): thread compaction keeps every decision, the context drawer, thread titles."""
import json

from studio import agent_brain, chat_context

from .test_house import client  # noqa: F401  (the sample-workspace app fixture)


def _thread(n):
    out = [{"role": "user", "text": "Post this on all my channels: https://news.example.com/agents", "at": 1}]
    out.append({"role": "user", "text": "Yes, I have the rights to repost it with credit", "at": 2})
    out.append({"role": "user", "text": "Use the Funding template", "at": 3})
    for i in range(n - len(out)):
        out.append({"role": "agent" if i % 2 else "user", "text": f"turn {i} " + "word " * 60, "at": 10 + i})
    return out


def _run(n=30):
    return {"id": 7, "kind": "agent",
            "input": {"prompt": "Post this", "intake": {"format": "reel"}, "theme": "cinematic",
                      "channels": [{"id": "ch_ig", "service": "instagram", "name": "huhbyte.ai"},
                                   {"id": "ch_li", "service": "linkedin", "name": "Ram"}]},
            "result": {"thread": _thread(n),
                       "source": {"url": "https://news.example.com/agents", "site": "Example News", "title": "Acme agent", "via": "your_link"},
                       "rights": {"mode": "news_credit", "credit": "Acme"},
                       "template": {"id": "funding", "name": "Funding", "why": "you picked it"},
                       "kit_override": {"name": "Acme Rockets"},
                       "packages": [{"channel_id": "ch_ig", "applied_rules": [{"rule": "Never use: game-changer"}]},
                                    {"channel_id": "ch_li", "applied_rules": [{"rule": "Never use: game-changer"}, {"rule": "Max 2 hashtags"}]}]}}


def test_short_thread_is_not_compacted():
    c = chat_context.compact(_thread(8))
    assert c["summarized"] == 0 and len(c["kept"]) == 8 and c["summary"] == ""
    assert chat_context.summarize({"result": {"thread": _thread(8)}}) is None


def test_long_thread_keeps_the_last_turns_verbatim_and_summarizes_the_rest():
    th = _thread(30)
    c = chat_context.compact(th)
    assert c["summarized"] == 30 - chat_context.KEEP_LAST and c["total"] == 30
    assert c["kept"] == th[-chat_context.KEEP_LAST:]  # word for word
    assert c["summary"].startswith("Earlier in this thread (24 messages")
    assert len(c["summary"]) <= chat_context.SUMMARY_CHARS + 120
    assert all(len(ln) <= chat_context.TURN_CHARS + 12 for ln in c["summary"].splitlines()[1:])


def test_compacted_thread_keeps_rights_kit_and_template():
    run = _run(40)
    c = chat_context.compact(run["result"]["thread"])
    # the turns that made the decisions were folded away...
    assert all("rights" not in t["text"] and "Funding" not in t["text"] for t in c["kept"])
    block = chat_context.prompt_block(run, {"name": "HuhByte", "handle": "@huhbyte.ai"})
    facts = json.loads(block.split("Pinned facts (always true for this post): ", 1)[1].split("\n", 1)[0])
    # ...and the decisions are still there, whole
    assert facts["rights"] == "news_credit"
    assert facts["kit"] == {"name": "Acme Rockets", "handle": "@huhbyte.ai"}  # the run's own kit wins over the active one
    assert facts["template"] == {"id": "funding", "name": "Funding", "why": "you picked it"}
    assert facts["source"]["url"] == "https://news.example.com/agents"
    assert [c["service"] for c in facts["channels"]] == ["instagram", "linkedin"]
    assert facts["format"] == "reel" and facts["theme"] == "cinematic"
    assert facts["rules"] == ["Never use: game-changer", "Max 2 hashtags"]
    assert "Earlier in this thread" in block and "Latest messages:" in block


def test_the_agent_reads_the_compacted_conversation_on_a_reply():
    class T:
        def _kit(self):
            return {"name": "HuhByte", "handle": "@huhbyte.ai"}
    out = agent_brain._conversation(_run(40), T())
    assert "news_credit" in out and "Funding" in out and "Acme Rockets" in out and "Earlier in this thread" in out
    assert len(out) <= 6000


def test_pinned_facts_without_a_rights_answer_say_it_was_asked():
    run = _run(4)
    run["result"].pop("rights")
    run["result"]["rights_question"] = "Is this your video?"
    assert chat_context.pinned(run)["rights"] == "asked"


def _make(client):  # noqa: F811
    r = client.post("/api/agent/runs", json={"query": "travel agents", "channel_ids": ["demo_x"], "server_ai": True}).json()
    assert r["status"] == "ready", r.get("steps")
    return r["id"]


def test_context_endpoint_and_reply_store_the_run_summary(client):  # noqa: F811
    rid = _make(client)
    st = client.app.state.store
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    res["thread"] = _thread(20)
    res["rights"] = {"mode": "slides"}
    res["template"] = {"id": "news", "name": "News", "why": "auto"}
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), rid))
    ctx = client.get(f"/api/agent/runs/{rid}/context").json()
    assert "compaction" in ctx, ctx
    assert ctx["compaction"]["summarized"] == 20 - chat_context.KEEP_LAST and ctx["compaction"]["kept"] == chat_context.KEEP_LAST
    assert ctx["facts"]["rights"] == "slides" and ctx["facts"]["template"]["id"] == "news" and ctx["facts"]["kit"]["name"]
    client.post(f"/api/agent/runs/{rid}/messages", json={"text": "make the hook stronger"})
    after = client.get(f"/api/agent/runs/{rid}").json()["result"]
    assert after["thread_summary"]["summarized"] >= 15 and "Earlier in this thread" in after["thread_summary"]["summary"]
    assert after["rights"]["mode"] == "slides"  # compaction never touches the decisions themselves


def test_rename_thread_and_the_sidebar_title(client):  # noqa: F811
    rid = _make(client)
    auto = next(x for x in client.get("/api/agent/runs").json() if x["id"] == rid)["title"]
    r = client.post(f"/api/agent/runs/{rid}/title", json={"title": "  Launch <b>week</b>  "}).json()
    assert r["title"] == "Launch bweek/b" and r["custom"]
    row = next(x for x in client.get("/api/agent/runs").json() if x["id"] == rid)
    assert row["title"] == "Launch bweek/b" and row["auto_title"] == auto
    assert client.post(f"/api/agent/runs/{rid}/title", json={"title": ""}).json()["custom"] is False
    assert next(x for x in client.get("/api/agent/runs").json() if x["id"] == rid)["title"] == auto
    assert client.post("/api/agent/runs/999999/title", json={"title": "x"}).status_code == 404


def test_mcp_parity_for_context_and_rename(client):  # noqa: F811
    rid = _make(client)
    tok = client.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    names = {t["name"] for t in client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"get_post_context", "rename_post_thread"} <= names
    out = client.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                               "params": {"name": "get_post_context", "arguments": {"run_id": rid}}}).json()["result"]
    assert not out["isError"] and out["structuredContent"]["run_id"] == rid
