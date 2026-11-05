"""The app makes the worker's free-tier calls with its own keys (studio/free_proxy.py): no GROQ / NVIDIA / OPENROUTER key
ever reaches a worker (InstaBox box or GitHub Actions run). Job-token checked, typed, capped, under the free ledger."""
import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio import free_proxy, free_quota, instabox_worker as ib
from studio.media import asr
from studio.media.reframe import vision

from .test_free_quota import _set_spec
from .test_worker_failure_report import _take_job

GROQ_KEY = "gsk_dummy_server_only"
NV_KEY = "nvapi-dummy-server-only"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    free_quota.REMOTE = free_quota.PROXY = None
    free_proxy.HTTP = None
    free_proxy._block["groq"] = 0.0
    for k in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
              "https_proxy", "http_proxy", "all_proxy", "HQ_LAYOUT_VISION"):
        monkeypatch.delenv(k, raising=False)
    yield
    free_quota.REMOTE = free_quota.PROXY = None
    free_proxy.HTTP = None
    free_proxy._block["groq"] = 0.0


def flac(seconds: float, rate: int = 16000) -> bytes:
    """A FLAC header (STREAMINFO with total samples) plus padding: enough for the app's checks."""
    total = int(seconds * rate)
    bits = (rate << 44) | (0 << 41) | (15 << 36) | total
    info = b"\x00\x10\x00\x10" + b"\x00" * 6 + bits.to_bytes(8, "big") + b"\x00" * 16
    return b"fLaC" + bytes([0x80, 0, 0, 34]) + info + b"\x00" * 64


def jpeg(w=320, h=180, exif=True) -> bytes:
    im = Image.new("RGB", (w, h), (120, 80, 200))
    buf = io.BytesIO()
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCam"   # Make
        im.save(buf, "JPEG", exif=ex.tobytes())
    else:
        im.save(buf, "JPEG")
    return buf.getvalue()


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body or {}, headers or {}
        self.content = json.dumps(self._b).encode()

    def json(self):
        return self._b


class FakeHTTP:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def _groq_words():
    return {"language": "english", "words": [{"word": " hi", "start": 0.0, "end": 0.4}, {"word": "there", "start": 0.5, "end": 0.9}]}


# ---------------------------------------------------------------- pure helpers
def test_flac_seconds_reads_streaminfo_and_clean_audio_never_under_reserves():
    assert abs(free_proxy.flac_seconds(flac(42.5)) - 42.5) < 0.01
    assert free_proxy.flac_seconds(b"fLaC" + b"\x00" * 10) is None
    assert free_proxy.clean_audio(flac(300), "30") == pytest.approx(300, abs=0.01)   # the header wins over a low claim
    assert free_proxy.clean_audio(flac(30), "45") == 45.0
    for data, secs in ((b"RIFF....", "30"), (b"", "30"), (flac(30), "abc"), (flac(30), None), (flac(30), "0"),
                       (flac(30), "9999"), (flac(900), "30")):
        with pytest.raises(ValueError):
            free_proxy.clean_audio(data, secs)


def test_clean_frames_reencodes_and_strips_metadata():
    raw = jpeg()
    assert b"SecretCam" in raw
    out = free_proxy.clean_frames([base64.b64encode(raw).decode()])
    assert len(out) == 1 and out[0][:3] == b"\xff\xd8\xff" and b"SecretCam" not in out[0] and b"Exif" not in out[0]


@pytest.mark.parametrize("frames", [None, "x", [], [1], ["not base64!!"], [base64.b64encode(b"\x89PNG....").decode()],
                                    [base64.b64encode(jpeg(1500, 200, False)).decode()],
                                    [base64.b64encode(jpeg(exif=False)).decode()] * 13,
                                    ["A" * 600_000], [base64.b64encode(b"\xff\xd8\xff" + b"\x00" * 50).decode()]])
def test_clean_frames_rejects_bad_input(frames):
    with pytest.raises(ValueError):
        free_proxy.clean_frames(frames)


# ---------------------------------------------------------------- /asr
def test_asr_route_token_types_and_no_key_on_the_app(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    w = TestClient(app, base_url="https://hq.test")
    url = f"/api/worker/jobs/{jid}/asr"
    h = {"x-job-token": jt, "content-type": "audio/flac", "x-audio-seconds": "30"}
    assert w.post(url, content=flac(30), headers={**h, "x-job-token": ""}).status_code == 403
    assert w.post(url, content=flac(30), headers={**h, "x-job-token": "jt_wrong"}).status_code == 403
    assert w.post(url, content=b"not flac", headers=h).status_code == 400
    assert w.post(url, content=flac(30), headers={**h, "x-audio-seconds": "lots"}).status_code == 400
    r = w.post(url, content=flac(30), headers=h).json()
    assert r == {"ok": False, "why": "groq: no key on the app"}                    # local fallback on the worker


def test_asr_route_calls_groq_with_the_apps_key_under_the_ledger(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    fake = FakeHTTP([Resp(200, _groq_words())])
    free_proxy.HTTP = fake
    w = TestClient(app, base_url="https://hq.test")
    r = w.post(f"/api/worker/jobs/{jid}/asr", content=flac(120),
               headers={"x-job-token": jt, "content-type": "audio/flac", "x-audio-seconds": "120"})
    assert r.status_code == 200 and GROQ_KEY not in r.text
    d = r.json()
    assert d["ok"] is True and d["language"] == "en" and [x["w"] for x in d["words"]] == ["hi", "there"]
    assert fake.calls[0][0] == asr.GROQ_URL and fake.calls[0][1]["headers"]["Authorization"] == f"Bearer {GROQ_KEY}"
    rows = app.state.house.store.q("SELECT units FROM free_usage WHERE provider='groq'")
    assert [round(float(x["units"])) for x in rows] == [120]                      # reserved on the shared ledger


def test_asr_route_refuses_jobs_that_dont_transcribe_and_pauses_on_429(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", GROQ_KEY)
    w = TestClient(app, base_url="https://hq.test")
    h = {"x-job-token": jt, "content-type": "audio/flac", "x-audio-seconds": "30"}
    fake = FakeHTTP([Resp(429, {}, {"retry-after": "300"})])
    free_proxy.HTTP = fake
    r = w.post(f"/api/worker/jobs/{jid}/asr", content=flac(30), headers=h).json()
    assert r["ok"] is False and "429" in r["why"]
    r = w.post(f"/api/worker/jobs/{jid}/asr", content=flac(30), headers=h).json()
    assert r["ok"] is False and "wait" in r["why"] and len(fake.calls) == 1       # never retried into Groq
    free_proxy._block["groq"] = 0.0
    _set_spec(app, jid, kind="take_render", clips=[{"id": "c1", "smart_layout": True}])
    free_proxy.HTTP = FakeHTTP([])                                                # would raise if called
    r = w.post(f"/api/worker/jobs/{jid}/asr", content=flac(30), headers=h).json()
    assert r["ok"] is False and "doesn't transcribe" in r["why"]


# ---------------------------------------------------------------- /vision
def _answer(text):
    return Resp(200, {"choices": [{"message": {"content": text}}]})


def test_vision_route_needs_the_owners_opt_in_before_anything(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    monkeypatch.setenv("NVIDIA_API_KEY", NV_KEY)
    free_proxy.HTTP = FakeHTTP([])
    w = TestClient(app, base_url="https://hq.test")
    url = f"/api/worker/jobs/{jid}/vision"
    body = {"frames": [base64.b64encode(jpeg()).decode()]}
    assert w.post(url, json=body).status_code == 403
    r = w.post(url, headers={"x-job-token": jt}, json=body).json()                # an analyze job: no vision
    assert r["ok"] is False and "Smart layout" in r["why"]
    _set_spec(app, jid, kind="take_render", clips=[{"id": "c1", "smart_layout": False}])
    assert w.post(url, headers={"x-job-token": jt}, json={"frames": "junk"}).json()["ok"] is False  # opt-in checked first
    _set_spec(app, jid, clips=[{"id": "c1", "smart_layout": True}])
    for bad in ({"frames": "junk"}, {"frames": []}, {"frames": [7]}, {}):
        assert w.post(url, headers={"x-job-token": jt}, json=bad).status_code == 400


def test_vision_route_asks_with_the_apps_key_and_sends_clean_frames(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    _set_spec(app, jid, kind="take_render", clips=[{"id": "c1", "smart_layout": True}])
    monkeypatch.setenv("NVIDIA_API_KEY", NV_KEY)
    fake = FakeHTTP([_answer('{"layout": "screencast"}')])
    free_proxy.HTTP = fake
    w = TestClient(app, base_url="https://hq.test")
    r = w.post(f"/api/worker/jobs/{jid}/vision", headers={"x-job-token": jt}, json={"frames": [base64.b64encode(jpeg()).decode()]})
    assert r.status_code == 200 and NV_KEY not in r.text
    assert r.json() == {"ok": True, "category": "screencast", "provider": "nvidia", "model": vision.PROVIDERS[0]["model"]}
    url, kw = fake.calls[0]
    assert url == vision.PROVIDERS[0]["url"] and kw["headers"]["Authorization"] == f"Bearer {NV_KEY}"
    sent = base64.b64decode(kw["json"]["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1])
    assert b"SecretCam" not in sent                                               # re-encoded on the app
    rows = app.state.house.store.q("SELECT COUNT(*) AS n FROM free_usage WHERE provider='nvidia'")
    assert rows[0]["n"] == 1


# ---------------------------------------------------------------- the worker side
def test_worker_proxy_never_raises_and_sends_only_the_job_token():
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.path.endswith("/asr"):
            return httpx.Response(200, json={"ok": True, "words": [], "language": "en"})
        return httpx.Response(503)
    p = free_quota.WorkerProxy(httpx.Client(transport=httpx.MockTransport(handler)), "https://hq.test/", {"x-job-token": "jt_t"}, 7)
    assert p.asr(b"fLaC", 30)["ok"] is True
    assert seen[0].url.path == "/api/worker/jobs/7/asr" and seen[0].headers["x-audio-seconds"] == "30.000"
    assert seen[0].headers["x-job-token"] == "jt_t" and "authorization" not in seen[0].headers
    assert p.vision([b"\xff\xd8"]) == {"ok": False, "why": "vision: the app answered 503"}
    assert json.loads(seen[1].content) == {"frames": [base64.b64encode(b"\xff\xd8").decode()]}
    boom = free_quota.WorkerProxy(httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("x")))),
                                  "https://hq.test", {}, 7)
    assert boom.asr(b"fLaC", 30)["ok"] is False and boom.vision([b"x"])["ok"] is False
    odd = free_quota.WorkerProxy(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1]))), "https://hq.test", {}, 7)
    assert odd.asr(b"fLaC", 30)["ok"] is False


class _Proxy:
    def __init__(self, answers):
        self.answers, self.asked = list(answers), []

    def asr(self, data, secs):
        self.asked.append(secs)
        return self.answers.pop(0)

    def vision(self, frames):
        self.asked.append(len(frames))
        return self.answers.pop(0)


@pytest.fixture
def chunks(monkeypatch, tmp_path):
    def fake_chunks(path, tmp, chunk_s=asr.CHUNK_S):
        out = []
        for k in range(2):
            f = Path(tmp) / f"a{k}.flac"
            f.write_bytes(b"fLaC")
            out.append((k * 600.0, 600.0, f))
        return out
    monkeypatch.setattr(asr, "audio_chunks", fake_chunks)
    return asr.Budget(str(tmp_path / "u.json"))


def test_free_first_on_the_worker_goes_through_the_app_even_with_a_local_key(chunks, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_not_be_used")
    free_quota.PROXY = _Proxy([{"ok": True, "language": "en", "words": [{"w": "hi", "s": 0.1, "e": 0.3, "p": 1}]},
                               {"ok": False, "why": "groq: near 20 requests/minute"}])
    local = lambda f, m: ([{"w": "loc", "s": 0.0, "e": 0.2, "p": 0.9}], "en")  # noqa: E731
    words, lang, label = asr.free_first("x.mp4", budget=chunks, http=FakeHTTP([]), local=local)
    assert [x["w"] for x in words] == ["hi", "loc"] and words[1]["s"] == 600.0 and lang == "en"
    assert "via the app" in label and "faster-whisper" in label
    free_quota.PROXY = _Proxy([{"ok": False, "why": "groq: no key on the app"}])
    assert asr.free_first("x.mp4", budget=chunks, http=FakeHTTP([])) is None      # the caller's local path runs
    free_quota.PROXY = _Proxy([{"ok": True, "words": "garbage"}])
    assert asr.free_first("x.mp4", budget=chunks, http=FakeHTTP([])) is None


def test_vision_on_the_worker_goes_through_the_app(monkeypatch):
    monkeypatch.setattr(vision, "keyframes", lambda *a, **k: [b"\xff\xd8"])
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi_should_not_be_used")
    monkeypatch.setattr(vision, "pick", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called a provider locally")))
    free_quota.PROXY = _Proxy([{"ok": True, "category": "talk", "provider": "nvidia"}, {"ok": True, "category": "bogus"},
                               {"ok": False, "why": "Smart layout (beta) is off for this take"}])
    got = vision.pick_for_clip("x.mp4", 0, 5, 1920, 1080)
    assert got["category"] == "talk" and got["via"] == "app" and got["model"] == vision.PROVIDERS[0]["model"]
    assert vision.pick_for_clip("x.mp4", 0, 5, 1920, 1080) is None
    assert vision.pick_for_clip("x.mp4", 0, 5, 1920, 1080) is None


def test_no_provider_key_travels_to_a_worker(monkeypatch):
    wf = (Path(__file__).resolve().parents[2] / ".github/workflows/take.yml").read_text()
    for k in ("GROQ_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        assert f"secrets.{k}" not in wf
    cmd = ib.command(5, "https://hq.test", "take_render", "main")
    assert "API_KEY" not in cmd and "HQ_LAYOUT_VISION=shadow" in cmd               # same default as take.yml
    monkeypatch.setenv("HQ_LAYOUT_VISION", "on; rm -rf /")
    assert "HQ_LAYOUT_VISION=off" in ib.command(5, "https://hq.test", "take_render", "main")
