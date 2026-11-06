"""Internal media readers use the instance's local copy (studio/mediacache.py, docs/STORAGE.md#egress): preflight before a
Buffer push probes our own /m/ file from disk (never loads it into memory), and whole-file readers (renders, image edits)
read the copy in one buffer. With the cache off everything falls back to the bytes, as before.
"""
from __future__ import annotations

from studio import mediacache, preflight
from studio.actions import Actor

from .conftest import image_bytes


def _url(app, tok: str) -> str:
    return app.state.settings.public_url.rstrip("/") + "/m/" + tok


def test_preflight_probes_our_own_media_from_the_local_copy_without_loading_it(app, human, monkeypatch):
    house, studio = app.state.house, app.state.studio
    data = image_bytes(64, 48)
    tok = house.put_media(Actor("acc_1"), data, "card.png")
    p = house.media_path(tok)
    assert p is not None and p.read_bytes() == data                 # filled once from the backend
    preflight._CACHE.clear()

    def boom(*_a, **_k):
        raise AssertionError("the file was loaded into memory")
    monkeypatch.setattr(house, "get_media", boom)
    monkeypatch.setattr(studio, "_load_media", boom)
    res = preflight.probe_url(_url(app, tok), studio._load_media, studio._media_path)
    assert res["kind"] == "image" and res["width"] == 64 and res["height"] == 48 and res["bytes"] == len(data)


def test_preflight_falls_back_to_bytes_when_the_cache_is_off(app, human, monkeypatch):
    monkeypatch.setenv("HQ_MEDIA_CACHE_MB", "0")
    house, studio = app.state.house, app.state.studio
    store = house.store
    store.__dict__.pop("_media_cache", None)                        # a fresh cache object that reads the setting
    data = image_bytes(40, 30)
    tok = house.put_media(Actor("acc_1"), data, "a.png")
    assert house.media_path(tok) is None
    preflight._CACHE.clear()
    res = preflight.probe_url(_url(app, tok), studio._load_media, studio._media_path)
    assert res["kind"] == "image" and res["width"] == 40


def test_media_path_refuses_binned_missing_and_foreign_hosts(app, human):
    house, studio = app.state.house, app.state.studio
    assert house.media_path("") is None and house.media_path("nope") is None
    from studio.library import TRASH_TOKEN
    assert house.media_path(TRASH_TOKEN + "abc") is None
    assert studio._media_path("https://elsewhere.example/m/abc") is None


def test_whole_reads_the_local_copy_in_one_buffer(app, human):
    house = app.state.house
    data = image_bytes(50, 50)
    tok = house.put_media(Actor("acc_1"), data, "w.png")
    assert house.get_media(tok)["data"] == data
    f = __import__("studio.library", fromlist=["media_file"]).media_file(house.store, tok)
    p = f["path"]()
    assert p is not None
    p.unlink()                                                     # evicted between lookup and read: the backend, once
    assert mediacache.whole({**f, "path": lambda: p}) == data


def test_a_video_evicted_while_ffprobe_runs_falls_back_to_the_bytes(tmp_path, monkeypatch):
    p = tmp_path / "v.mp4"
    p.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)

    def gone(path):
        p.unlink()
        return {"probed": False, "why": "ffprobe failed"}
    monkeypatch.setattr(preflight, "ffprobe_path", gone)
    assert preflight.probe_path(p, "/m/x.mp4") is None
    preflight._CACHE.clear()
    res = preflight.probe_url("https://hq.test/m/x", lambda u: b"loaded", lambda u: (p.write_bytes(b"\x00\x00\x00\x18ftyp" + b"\x00" * 8), p)[1])
    assert res.get("bytes") == len(b"loaded")                       # the bytes loader ran instead
