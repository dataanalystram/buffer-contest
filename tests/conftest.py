import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from huhqueue.api.app import create_app, create_edge_app
from huhqueue.config import load_settings
from huhqueue.connectors.fake import FakeConnector
from huhqueue.core import Core

PASSWORD = "correct-horse-1"


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for k in list(os.environ):
        if k.startswith(("HUHQ_", "INSTAGRAM_", "TIKTOK_", "YOUTUBE_", "X_", "LINKEDIN_")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HUHQ_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HUHQ_ENABLE_FAKE", "1")
    monkeypatch.chdir(tmp_path)
    s = load_settings(env_file=tmp_path / "none.env")
    s.instagram_app_id, s.instagram_app_secret = "ig-app", "ig-secret"
    s.tiktok_client_key, s.tiktok_client_secret = "tt-key", "tt-secret"
    s.youtube_client_id, s.youtube_client_secret = "yt-id", "yt-secret"
    s.x_client_id, s.x_client_secret = "x-id", "x-secret"
    s.linkedin_client_id, s.linkedin_client_secret = "li-id", "li-secret"
    return s


@pytest.fixture
def core(settings):
    FakeConnector.published.clear()
    FakeConnector.replies.clear()
    return Core(settings)


@pytest.fixture
def app(core):
    return create_app(core, start_worker=False)


@pytest.fixture
def edge(core):
    return TestClient(create_edge_app(core))


class Api:
    """Logged-in test client that sends the CSRF header automatically."""

    def __init__(self, client: TestClient):
        self.c = client

    def _h(self):
        return {"X-CSRF-Token": self.c.cookies.get("huhq_csrf", "")}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post(url, json=json if json is not None else ({} if "files" not in kw else None), headers=self._h(), **kw)

    def patch(self, url, json=None):
        return self.c.patch(url, json=json or {}, headers=self._h())

    def put(self, url, json=None):
        return self.c.put(url, json=json or {}, headers=self._h())

    def delete(self, url):
        return self.c.delete(url, headers=self._h())


@pytest.fixture
def api(app):
    c = TestClient(app)
    r = c.post("/api/auth/setup", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    return Api(c)


@pytest.fixture
def channel(api):
    r = api.post("/api/connect/fake/token", {"access_token": "tok-1234", "username": "huhbyte_demo"})
    assert r.status_code == 200, r.text
    return r.json()


def make_image(path: Path, size=(1080, 1080), fmt="PNG", color=(201, 172, 250)) -> Path:
    Image.new("RGB", size, color).save(path, fmt)
    return path


@pytest.fixture
def image_file(tmp_path):
    return make_image(tmp_path / "card.png")


def upload(api, path: Path, name: str | None = None):
    with open(path, "rb") as fh:
        r = api.post("/api/media", files={"files": (name or path.name, fh)})
    assert r.status_code == 200, r.text
    return r.json()["media"]


def future(minutes=60) -> str:
    return (datetime.utcnow() + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M") + "+00:00"
