"""Settings -> AI apps: a human can change a token's daily limit (PATCH /api/agent-tokens/{id}); an agent never can."""
from .conftest import agent_token, mcp_call


def _tid(human):
    return [t for t in human.get("/api/safety").json()["tokens"] if not t["revoked"]][0]["id"]


def test_human_raises_cap_and_agent_gets_more_actions(client, human):
    tok = agent_token(human, cap=1)
    tid = _tid(human)
    r = human.patch(f"/api/agent-tokens/{tid}", json={"daily_cap": 60})
    assert r.status_code == 200 and r.json() == {"id": tid, "daily_cap": 60}
    assert [t for t in human.get("/api/safety").json()["tokens"] if t["id"] == tid][0]["daily_cap"] == 60
    acts = human.get("/api/audit").json()
    rows = acts if isinstance(acts, list) else acts.get("items", acts.get("rows", []))
    assert any(r.get("action") == "set_agent_cap" for r in rows)


def test_cap_bounds_and_unknown_token(client, human):
    agent_token(human)
    tid = _tid(human)
    for bad in (0, 101, "x", None):
        assert human.patch(f"/api/agent-tokens/{tid}", json={"daily_cap": bad}).status_code == 400
    assert human.patch("/api/agent-tokens/99999", json={"daily_cap": 20}).status_code == 404


def test_agent_cannot_change_its_own_cap(client, human):
    tok = agent_token(human, cap=3)
    tid = _tid(human)
    r = client.patch(f"/api/agent-tokens/{tid}", json={"daily_cap": 100}, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code in (401, 403)
    assert [t for t in human.get("/api/safety").json()["tokens"] if t["id"] == tid][0]["daily_cap"] == 3


def test_revoked_token_cap_cannot_change(client, human):
    agent_token(human)
    tid = _tid(human)
    human.delete(f"/api/agent-tokens/{tid}")
    assert human.patch(f"/api/agent-tokens/{tid}", json={"daily_cap": 20}).status_code == 404
