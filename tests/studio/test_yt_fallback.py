"""The opt-in YouTube route for link -> split (studio/media/yt_fallback.py) and its wiring in download_platform."""
import sys
import types

import pytest

from studio.media import download, yt_fallback

INFO = {"adaptiveFormats": [
    {"itag": "140", "url": "https://inv.example/videoplayback?itag=140", "clen": "100"},
    {"itag": "140", "url": "https://inv.example/videoplayback?itag=140&lang=2", "clen": "101"},
    {"itag": "137", "url": "https://inv.example/videoplayback?itag=137", "clen": "900"},
    {"itag": "136", "url": "/videoplayback?itag=136", "clen": "500"},
    {"itag": "135", "url": "https://inv.example/videoplayback?itag=135", "clen": "300"},
]}


def test_pick_formats_caps_height_and_keeps_first_audio():
    v, a = yt_fallback.pick_formats(INFO, 720)
    assert v["itag"] == "136" and a["clen"] == "100"
    v, _ = yt_fallback.pick_formats(INFO, 480)
    assert v["itag"] == "135"
    assert yt_fallback.pick_formats({"adaptiveFormats": []}) == (None, None)


def test_original_audio_beats_auto_dubs():
    info = {"adaptiveFormats": [
        {"itag": "140", "url": "https://i.example/vp?xtags=acont%3Ddubbed-auto%3Alang%3Dar", "clen": "2"},
        {"itag": "140", "url": "https://i.example/vp?xtags=acont%3Doriginal%3Alang%3Den-US", "clen": "1"},
        {"itag": "136", "url": "https://i.example/vp?itag=136", "clen": "5"}]}
    _, a = yt_fallback.pick_formats(info)
    assert "original" in a["url"]


def test_flag_off_by_default_and_config_travels(monkeypatch):
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    monkeypatch.delenv("HQ_INVIDIOUS_INSTANCES", raising=False)
    assert not yt_fallback.enabled() and yt_fallback.config() is None
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    monkeypatch.setenv("HQ_INVIDIOUS_INSTANCES", "https://a.example, http://plain.example ,https://b.example/")
    assert yt_fallback.instances() == ["https://a.example", "https://b.example"]
    assert yt_fallback.config() == {"mode": "invidious", "instances": ["https://a.example", "https://b.example"]}


def test_apply_on_worker_only_https(monkeypatch):
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    monkeypatch.delenv("HQ_INVIDIOUS_INSTANCES", raising=False)
    yt_fallback.apply(None)
    assert not yt_fallback.enabled()
    yt_fallback.apply({"mode": "invidious", "instances": ["http://evil.example", "https://ok.example"]})
    assert yt_fallback.instances() == ["https://ok.example"]


def test_fetch_refuses_bad_id_and_when_off(monkeypatch, tmp_path):
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    with pytest.raises(yt_fallback.FallbackError):
        yt_fallback.fetch("../../etc", tmp_path / "x.mp4")
    with pytest.raises(yt_fallback.FallbackError, match="off"):
        yt_fallback.fetch("QJf0H64BnFo", tmp_path / "x.mp4")


def _bot_blocked_ytdlp(monkeypatch):
    class Y:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, *a, **k):
            raise RuntimeError("ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Y))
    monkeypatch.setattr(download.netguard, "check_url", lambda *a, **k: a[0])


def test_bot_block_without_flag_says_upload_instead(monkeypatch, tmp_path):
    _bot_blocked_ytdlp(monkeypatch)
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    with pytest.raises(download.DownloadError, match="blocked our server.*upload the file"):
        download.download_platform("https://youtu.be/QJf0H64BnFo", tmp_path / "v.mp4")


def test_bot_block_with_flag_uses_fallback(monkeypatch, tmp_path):
    _bot_blocked_ytdlp(monkeypatch)
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    monkeypatch.setenv("HQ_INVIDIOUS_INSTANCES", "https://inv.example")
    seen = {}

    def fake_fetch(vid, dest, **k):
        seen["vid"] = vid
        open(dest, "wb").write(b"x")
        return {"path": str(dest)}
    monkeypatch.setattr(yt_fallback, "fetch", fake_fetch)
    out = download.download_platform("https://www.youtube.com/watch?v=QJf0H64BnFo", tmp_path / "v.mp4")
    assert seen["vid"] == "QJf0H64BnFo" and out.read_bytes() == b"x"


def test_fallback_failure_message(monkeypatch, tmp_path):
    _bot_blocked_ytdlp(monkeypatch)
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    monkeypatch.setenv("HQ_INVIDIOUS_INSTANCES", "https://inv.example")

    def boom(*a, **k):
        raise yt_fallback.FallbackError("https://inv.example: answered 403")
    monkeypatch.setattr(yt_fallback, "fetch", boom)
    with pytest.raises(download.DownloadError, match="blocked our server.*upload the file"):
        download.download_platform("https://youtu.be/QJf0H64BnFo", tmp_path / "v.mp4")


def test_probe_stops_at_64kb_when_range_is_ignored(monkeypatch):
    """A server that ignores Range and answers 200 with the whole body: the probe reads 64 KB and stops."""
    import contextlib
    monkeypatch.setenv("HQ_INVIDIOUS_INSTANCES", "https://inv.example")
    monkeypatch.setattr(yt_fallback, "_get_json", lambda url, http=None: dict(INFO, title="t", lengthSeconds=10))
    pulled = {"n": 0}

    class R:
        status_code = 200

        def iter_bytes(self, size):
            for _ in range(1000):  # 1000 x 64 KB: far more than the probe may read
                pulled["n"] += 1
                yield b"x" * 65536

    @contextlib.contextmanager
    def stream(url, **k):
        yield R()
    monkeypatch.setattr(yt_fallback.netguard, "safe_stream", stream)
    out = yt_fallback.probe("https://www.youtube.com/watch?v=QJf0H64BnFo")
    assert out["ok"] and out["bytes_read"] == 65536 and pulled["n"] == 1
