"""AI ideas, image alt text, comment insights (LLM and rules fallback) and channel groups."""
import json
from datetime import datetime

from huhqueue.models import Comment
from huhqueue.services import assistant

from .conftest import upload


def _comments(core, cid, texts):
    with core.db.session() as s:
        for i, t in enumerate(texts):
            s.add(Comment(channel_id=cid, external_id=f"c{i}", media_external_id="m1", author=f"user{i}", text=t,
                          at=datetime.utcnow(), status="open"))


def test_ideas_rules_fallback_and_save(api):
    r = api.post("/api/ideas/generate", {"topic": "AI agents", "count": 3})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["source"] == "rules" and len(out["ideas"]) == 3 and "AI agents" in out["ideas"][0]["title"]
    assert "Settings › AI" in out["note"]
    assert api.post("/api/ideas/generate", {"topic": ""}).status_code == 400
    out = api.post("/api/ideas/generate", {"topic": "AI agents", "count": 2, "save": True, "column": "todo"}).json()
    ideas = {i["id"]: i for i in api.get("/api/ideas").json()["ideas"]}
    assert all(ideas[i]["column"] == "todo" for i in out["saved_ids"]) and len(out["saved_ids"]) == 2


def test_ideas_with_llm(api):
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"ideas": [{"title": "Agents that click for you", "body": "Hook: ..."},
                                                          {"title": "", "body": "dropped"}]})]
    out = api.post("/api/ideas/generate", {"topic": "computer-use agents", "audience": "founders"}).json()
    assert out["source"] == "llm" and [i["title"] for i in out["ideas"]] == ["Agents that click for you"]
    assert "founders" in assistant.FakeLLM.calls[-1]["user"]


def test_alt_text(api, image_file):
    m = upload(api, image_file)[0]
    r = api.post(f"/api/media/{m['id']}/alt-text", {})
    assert r.status_code == 400 and "AI provider" in r.text          # no provider: clear message, nothing invented
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = ['"A lavender square with the HuhByte logo."']
    out = api.post(f"/api/media/{m['id']}/alt-text", {"save": True}).json()
    assert out["alt_text"] == "A lavender square with the HuhByte logo."
    assert assistant.FakeLLM.calls[-1]["image"] is True
    lib = api.get("/api/media").json()
    items = lib["items"] if isinstance(lib, dict) else lib
    assert next(x for x in items if x["id"] == m["id"])["alt_text"] == out["alt_text"]
    assert api.post("/api/media/md_nope/alt-text", {}).status_code == 404


def test_comment_insights_rules_and_llm(api, core, channel):
    assert api.get("/api/comments/insights").json()["themes"] == []
    _comments(core, channel["id"], ["How do I get access to the model?", "Pricing please? how much", "love the pricing breakdown",
                                    "pricing seems high", "great video 🔥"])
    out = api.get("/api/comments/insights", params={"days": 7}).json()
    assert out["source"] == "rules" and out["comments"] == 5 and out["questions"] == 2 and out["unanswered"] == 5
    labels = [t["label"] for t in out["themes"]]
    assert labels[0] == "Questions" and "pricing" in labels
    api.put("/api/ai/settings", {"provider": "fake"})
    assistant.FakeLLM.replies[:] = [json.dumps({"themes": [{"label": "Pricing", "summary": "People ask about price", "count": 3,
                                                            "examples": ["pricing seems high"], "suggestion": "Post a pricing explainer"}]})]
    out = api.get("/api/comments/insights", params={"channel_id": channel["id"]}).json()
    assert out["source"] == "llm" and out["themes"][0]["suggestion"] == "Post a pricing explainer"


def test_channel_groups(api, channel):
    two = api.post("/api/connect/fake/token", {"access_token": "tok-5678", "username": "huhbyte_two"}).json()
    assert api.post("/api/channel-groups", {"name": " "}).status_code == 400
    g = api.post("/api/channel-groups", {"name": "Video", "channel_ids": [channel["id"], two["id"], "ch_gone"]}).json()
    assert g["channel_ids"] == [channel["id"], two["id"]]                     # unknown ids are dropped
    g = api.patch(f"/api/channel-groups/{g['id']}", {"name": "Shorts", "channel_ids": [two["id"]]}).json()
    assert g["name"] == "Shorts" and g["channel_ids"] == [two["id"]]
    assert [x["name"] for x in api.get("/api/channel-groups").json()] == ["Shorts"]
    api.delete(f"/api/channel-groups/{g['id']}")
    assert api.get("/api/channel-groups").json() == []
    assert api.patch("/api/channel-groups/cg_nope", {"name": "x"}).status_code == 404
