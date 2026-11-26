"""The editor's fx notes store (studio/fxstore.py): the app's database or Cloudflare D1, same SQL, easy to move."""
import json

import httpx

from studio import fxstore


def _motion():
    return {"on": True, "fx": [{"kind": "stat", "text": "300%", "why": "a number", "template": {"template": "info-count", "ms": 900}}],
            "skipped": ["Line \"x\" left as a caption: too early."]}


def test_store_backend_records_reads_exports_and_imports(app):
    be = fxstore.StoreBackend(app.state.store)
    rows = fxstore.rows_from_motion("u1", "t1", "c1", _motion())
    assert fxstore.record(be, rows) == 2
    got = fxstore.for_clip(be, "t1", "c1", "u1")
    assert [r["decision"] for r in got] == ["chose", "skipped"] and got[0]["template"] == "info-count"
    assert fxstore.for_clip(be, "t1", "c1", "someone-else") == []
    dump = fxstore.export_rows(be, batch=1)
    assert len(dump) == 2 and json.dumps(dump)
    other = fxstore.StoreBackend(app.state.store)
    assert fxstore.import_rows(other, dump) == 2                    # same ids: nothing doubled
    assert len(fxstore.export_rows(other)) == 2
    fxstore.forget_user(be, "u1")
    assert fxstore.for_clip(be, "t1", "c1", "u1") == []


def test_d1_backend_speaks_the_rest_api_and_never_fails_a_take():
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append((req.headers["authorization"], body["sql"].split()[0], body["params"]))
        return httpx.Response(200, json={"success": True, "result": [{"results": [{"kind": "stat"}] if body["sql"].startswith("SELECT") else []}]})
    be = fxstore.D1Backend("acct", "db", "tok", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert fxstore.record(be, fxstore.rows_from_motion("u1", "t1", "c1", _motion())) == 2
    assert fxstore.for_clip(be, "t1", "c1", "u1") == [{"kind": "stat"}]
    assert all(a == "Bearer tok" for a, _, _ in seen) and [v for _, v, _ in seen][:3] == ["CREATE", "CREATE", "CREATE"]
    bad = fxstore.D1Backend("acct", "db", "tok", client=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"success": False, "errors": [{"message": "no"}]}))))
    assert fxstore.record(bad, fxstore.rows_from_motion("u1", "t1", "c1", _motion())) == 0


def test_backend_choice(monkeypatch, app):
    monkeypatch.setenv("HQ_FX_STORE", "d1")
    for k in ("HQ_D1_ACCOUNT", "HQ_D1_DATABASE", "HQ_D1_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert fxstore.backend(app.state.store).name == "store"        # incomplete settings: the app's database
    for k in ("HQ_D1_ACCOUNT", "HQ_D1_DATABASE", "HQ_D1_TOKEN"):
        monkeypatch.setenv(k, "x")
    assert fxstore.backend(app.state.store).name == "d1"
    monkeypatch.delenv("HQ_FX_STORE")
    assert fxstore.backend(app.state.store).name == "store"
