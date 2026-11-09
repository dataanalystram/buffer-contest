"""Every signed-in workspace starts on the Buffer kit (owner, 9 Nov 2026), without ever switching a brand of its own."""
from __future__ import annotations

from .conftest import connect


def _on(monkeypatch):
    monkeypatch.setenv("HQ_DEFAULT_KIT", "buffer")


def test_a_new_signed_in_workspace_starts_on_the_buffer_kit(client, monkeypatch):
    _on(monkeypatch)
    client.headers["X-HQ-CSRF"] = connect(client)
    lst = client.get("/api/brand-kits").json()
    buf = [k for k in lst["kits"] if k["name"] == "Buffer"]
    assert len(buf) == 1 and buf[0]["active"] and lst["active_id"] == buf[0]["id"]
    kit = client.get("/api/brand-studio").json()["kit"]
    assert kit["name"] == "Buffer" and kit["colors"]["primary"].upper() == "#213130" and kit["logo_url"]


def test_seeded_once_and_a_switch_away_is_respected(client, monkeypatch):
    _on(monkeypatch)
    client.headers["X-HQ-CSRF"] = connect(client)
    mine = client.post("/api/brand-kits", json={"name": "Mine", "activate": True}).json()
    buf = next(k for k in client.get("/api/brand-kits").json()["kits"] if k["name"] == "Buffer")
    client.delete(f"/api/brand-kits/{buf['id']}")
    client.headers["X-HQ-CSRF"] = connect(client)  # signing in again neither re-adds Buffer nor switches back
    lst = client.get("/api/brand-kits").json()
    assert lst["active_id"] == mine["id"] and not [k for k in lst["kits"] if k["name"] == "Buffer"]


def test_a_workspace_with_its_own_brand_is_never_switched(client, monkeypatch):
    client.headers["X-HQ-CSRF"] = connect(client)  # signed in before the default existed, with a brand of its own
    client.post("/api/brand-studio", json={"name": "Acme", "colors": {"primary": "#123456"}})
    _on(monkeypatch)
    client.headers["X-HQ-CSRF"] = connect(client)
    lst = client.get("/api/brand-kits").json()
    buf = [k for k in lst["kits"] if k["name"] == "Buffer"]
    assert len(buf) == 1 and not buf[0]["active"]  # in the library, one click away
    assert client.get("/api/brand-studio").json()["kit"]["name"] == "Acme"


def test_off_switch(client, monkeypatch):
    monkeypatch.setenv("HQ_DEFAULT_KIT", "off")
    client.headers["X-HQ-CSRF"] = connect(client)
    assert not [k for k in client.get("/api/brand-kits").json()["kits"] if k["name"] == "Buffer"]
