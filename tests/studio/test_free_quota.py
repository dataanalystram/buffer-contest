"""Free tiers hold across worker runs (studio/free_quota.py): each GitHub Actions run is a fresh machine whose own
counter starts at zero, so every Groq / vision request is first reserved on the app's ledger. Fails closed."""
import json

import pytest
from fastapi.testclient import TestClient

from studio import free_quota

from .test_worker_failure_report import _take_job


@pytest.fixture(autouse=True)
def _no_remote():
    free_quota.REMOTE = None
    yield
    free_quota.REMOTE = None


def test_check_enforces_every_groq_limit_with_the_margin():
    now = 100000.0
    assert free_quota.check([], "groq", 30, now) == (True, "")
    rpm = [(now - 1, 10.0)] * 18                                       # 19th request: over 90% of 20/min
    assert not free_quota.check(rpm, "groq", 10, now)[0]
    hour = [(now - 120 - i, 600.0) for i in range(10)]                 # 6,000 s in the hour + 600 > 6,480
    assert not free_quota.check(hour, "groq", 600, now)[0]
    assert free_quota.check(hour, "groq", 400, now)[0]
    day = [(now - 4000 - i * 60, 600.0) for i in range(43)]            # 25,800 s today + 600 > 25,920
    assert not free_quota.check(day, "groq", 600, now)[0]
    assert free_quota.check([(now - 90000, 9e9)], "groq", 10, now)[0]  # older than a day: gone


def test_check_vision_day_and_minute():
    now = 5e5
    assert not free_quota.check([(now - 3600 - i, 1.0) for i in range(45)], "openrouter", 1, now)[0]
    assert not free_quota.check([(now - 5, 1.0)] * 20, "nvidia", 1, now)[0]
    assert free_quota.check([(now - 61, 1.0)] * 20, "nvidia", 1, now)[0]


@pytest.mark.parametrize("provider,units", [("paid", 1), (None, 1), (["groq"], 1), ("groq", "10"), ("groq", True),
                                            ("groq", 0), ("groq", -5), ("groq", 1e9)])
def test_clean_rejects_bad_types(provider, units):
    with pytest.raises(ValueError):
        free_quota.clean(provider, units)


def test_remote_fails_closed():
    free_quota.REMOTE = lambda p, u: (_ for _ in ()).throw(RuntimeError("closed client"))
    ok, why = free_quota.remote_ok("groq", 10)
    assert not ok and "didn't answer" in why
    free_quota.REMOTE = lambda p, u: (False, "nope")
    assert free_quota.remote_ok("nvidia", 1) == (False, "nope")


def test_groq_chunk_never_calls_groq_when_the_ledger_says_no(tmp_path):
    from studio.media import asr
    free_quota.REMOTE = lambda p, u: (False, "groq: near 20 requests/minute")
    f = tmp_path / "a.flac"
    f.write_bytes(b"x")

    class Boom:
        def post(self, *a, **k):
            raise AssertionError("Groq was called")
    with pytest.raises(asr.GroqUnavailable):
        asr.groq_chunk(f, 30.0, "gsk_dummy", asr.Budget(str(tmp_path / "u.json")), http=Boom())


def test_vision_skips_providers_the_ledger_refuses(tmp_path):
    from studio.media.reframe import vision
    free_quota.REMOTE = lambda p, u: (False, "day limit")

    class Boom:
        def post(self, *a, **k):
            raise AssertionError("a provider was called")
    env = {"NVIDIA_API_KEY": "nvapi-dummy", "OPENROUTER_API_KEY": "sk-or-dummy"}
    assert vision.pick([b"\xff\xd8"], vision.Limits(str(tmp_path / "v.json")), http=Boom(), env=env) is None


# ---------------------------------------------------------------- the route
def _set_spec(app, jid, **kw):
    st = app.state.house.store
    spec = json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (jid,))[0]["spec"])
    spec.update(kw)
    st.x("UPDATE render_jobs SET spec=? WHERE id=?", (json.dumps(spec), jid))


def test_quota_route_token_types_and_scope(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    w = TestClient(app, base_url="https://hq.test")
    url = f"/api/worker/jobs/{jid}/quota"
    assert w.post(url, json={"provider": "groq", "units": 30}).status_code == 403
    assert w.post(url, headers={"x-job-token": "jt_wrong"}, json={"provider": "groq", "units": 30}).status_code == 403
    for bad in ({"provider": "groq", "units": "30"}, {"provider": "openai", "units": 1}, {"provider": ["groq"]}, {}):
        assert w.post(url, headers={"x-job-token": jt}, json=bad).status_code == 400
    h = {"x-job-token": jt}
    assert w.post(url, headers=h, json={"provider": "groq", "units": 30}).json() == {"ok": True, "why": ""}  # analyze: groq ok
    r = w.post(url, headers=h, json={"provider": "nvidia", "units": 1}).json()
    assert r["ok"] is False and "Smart layout" in r["why"]                                     # analyze: no vision
    _set_spec(app, jid, kind="take_render", clips=[{"id": "c1", "smart_layout": False}])
    assert w.post(url, headers=h, json={"provider": "nvidia", "units": 1}).json()["ok"] is False  # owner didn't opt in
    assert w.post(url, headers=h, json={"provider": "groq", "units": 30}).json()["ok"] is False   # render: no ASR
    _set_spec(app, jid, clips=[{"id": "c1", "smart_layout": True}])
    assert w.post(url, headers=h, json={"provider": "nvidia", "units": 1}).json()["ok"] is True


def test_quota_ledger_holds_across_runs(human, app, monkeypatch):
    _, jid, jt = _take_job(human, app, monkeypatch)
    w = TestClient(app, base_url="https://hq.test")
    _set_spec(app, jid, kind="take_render", clips=[{"id": "c1", "smart_layout": True}])
    oks = [w.post(f"/api/worker/jobs/{jid}/quota", headers={"x-job-token": jt}, json={"provider": "nvidia", "units": 1}).json()["ok"]
           for _ in range(22)]
    assert oks.count(True) == 20 and oks[-1] is False                                          # 20 a minute, shared
    rows = app.state.house.store.q("SELECT COUNT(*) AS n FROM free_usage WHERE provider='nvidia'")
    assert rows[0]["n"] == 20


def test_worker_remote_reads_only_a_clean_yes():
    import httpx

    def handler(req):
        body = json.loads(req.content)
        assert req.headers["x-job-token"] == "jt_t" and body == {"provider": "groq", "units": 12.0}
        return httpx.Response(200, json={"ok": "yes"})
    c = httpx.Client(transport=httpx.MockTransport(handler))
    ask = free_quota.worker_remote(c, "https://hq.test/", {"x-job-token": "jt_t"}, 9)
    assert ask("groq", 12)[0] is False                                                         # "yes" is not True
    c2 = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert free_quota.worker_remote(c2, "https://hq.test", {}, 9)("groq", 12)[0] is False
