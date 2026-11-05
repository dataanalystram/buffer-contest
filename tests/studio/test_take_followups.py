"""Follow-ups to #107 (owner ask, 5 Nov 2026), each pinned by a test that fails on b9efca7:
1. take.js: an ETag the browser can't read is a hard error with a clear message, never retried as a dropped connection.
2. The take Bin finds a binned take however many newer takes there are (it scanned only the newest 500).
3. _check_object drops a joined upload only on a definitive 404; 401 / 403 / 408 / 429 keep it for a later finish.
4. The same file again after its parts were joined resumes that upload (the checks only), never a fresh upload, and the
   browser goes straight to finishing it.
5. purge_stale and _finish can't undo each other: a row the sweep marked for deletion is never reset to 'ready'."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from studio import take_upload

from .test_take_delete_upload import MP4_HEAD, StubS3, _upload, s3  # noqa: F401  (s3 is a fixture)

TAKE_JS = Path(__file__).parents[2] / "studio" / "static" / "take.js"
FP = "talk.mp4|1|1730750000000"


def _status(app, uid):
    rows = app.state.store.q("SELECT status FROM take_uploads WHERE id=?", (uid,))
    return rows[0]["status"] if rows else None


def _answer(app, stub, method, code):
    """The store answers `method` with `code` ("GET" means the first-bytes read, not ListParts); everything else goes
    to the stub S3."""
    def hit(req):
        return req.method == method and (method != "GET" or "range" in req.headers)
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(code) if hit(req) else stub(req)))


# ---------------------------------------------------------------- 1 + 4 (browser side): tkUploadMultipart in Node
NODE_HARNESS = r"""
const calls = { put: 0, parts: 0, complete: 0 };
const UP = __UP__;
const api = async (p, o) => {
  if (p === "/api/takes/uploads") return UP;
  if (p.endsWith("/parts")) { calls.parts++; return { urls: [{ part_number: 1, size: 5, url: "https://bucket.test/x" }] }; }
  if (p.endsWith("/complete")) { calls.complete++; return { status: "ready" }; }
  throw new Error("unexpected " + p);
};
const tkXhr = async () => { calls.put++; return { getResponseHeader: () => null }; };  // CORS hides the ETag
const tkProg = () => {}; const tkSleep = async () => {}; const tkOnline = async () => {};
const navigator = { onLine: true };
__FN__
tkUploadMultipart({ name: "a.mp4", size: 5, type: "video/mp4", lastModified: 1, slice: () => "bytes" })
  .then((id) => console.log(JSON.stringify({ ok: true, id, ...calls })), (e) => console.log(JSON.stringify({ ok: false, msg: e.message, ...calls })));
"""


def _run_upload(up: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node isn't installed")
    js = TAKE_JS.read_text()
    fn = js[js.index("async function tkUploadMultipart(f) {"):js.index("async function tkUploadLibrary(f) {")]
    out = subprocess.run([node, "-e", NODE_HARNESS.replace("__UP__", json.dumps(up)).replace("__FN__", fn)],  # noqa: S603
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_a_hidden_etag_is_a_hard_error_not_six_retries():
    r = _run_upload({"upload_id": "tu_x", "parts": 1, "part_size": 5, "received": [], "status": "open", "resumed": False})
    assert not r["ok"] and r["put"] == 1, r                       # one try, not retried like a dropped connection
    assert "ETag" in r["msg"] and "CORS" in r["msg"] and "Connection" not in r["msg"]


def test_the_browser_finishes_a_joined_upload_without_sending_parts():
    r = _run_upload({"upload_id": "tu_x", "parts": 1, "part_size": 5, "received": [], "status": "joined", "resumed": True})
    assert r["ok"] and r["id"] == "tu_x" and r["put"] == 0 and r["parts"] == 0 and r["complete"] == 1, r


# ---------------------------------------------------------------- 2: the Bin isn't limited to the newest 500 takes
def test_the_bin_finds_a_binned_take_behind_500_newer_ones(human, app):
    st, now = app.state.store, time.time()

    def run(status, res):
        return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                    ("acc_1", "take", json.dumps({"name": "t", "channels": []}), status, "[]", json.dumps(res), now, now))
    old = run("binned", {"clips": [], "bin": {"at": now - 86400, "status": "ready", "tokens": []}})
    for _ in range(501):
        run("ready", {"clips": [{"id": "c1", "status": "ready"}]})
    b = human.get("/api/takes/bin").json()
    assert [x["take_id"] for x in b["takes"]] == [old]
    assert human.post("/api/takes/bin/purge", json={"all": True}).json()["purged_takes"] == [old]


# ---------------------------------------------------------------- 3: only a 404 drops a joined upload
@pytest.mark.parametrize("code", [401, 403, 408, 429])
def test_a_non_definitive_check_answer_keeps_the_joined_upload(human, app, s3, code):  # noqa: F811
    data = MP4_HEAD + b"v" * 1000
    up, etags = _upload(human, s3, data)
    _answer(app, s3, "HEAD", code)
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 502 and "kept" in r.json()["error"], r.text
    assert _status(app, up["upload_id"]) == "joined" and s3.objects               # nothing deleted
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(s3))
    assert human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={}).json()["status"] == "ready"


def test_a_404_on_the_check_drops_the_upload(human, app, s3):  # noqa: F811
    up, etags = _upload(human, s3, MP4_HEAD + b"v" * 1000)
    _answer(app, s3, "HEAD", 404)
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 422 and _status(app, up["upload_id"]) is None


@pytest.mark.parametrize("code", [403, 429])
def test_a_non_definitive_answer_to_the_first_bytes_keeps_it_too(human, app, s3, code):  # noqa: F811
    up, etags = _upload(human, s3, MP4_HEAD + b"v" * 1000)
    _answer(app, s3, "GET", code)
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 502 and _status(app, up["upload_id"]) == "joined" and s3.objects


# ---------------------------------------------------------------- 4: a joined upload resumes, never restarts
def test_the_same_file_after_the_join_resumes_that_upload(human, app, s3):  # noqa: F811
    data = MP4_HEAD + b"v" * 1000
    up, etags = _upload(human, s3, data, fp=FP)
    _answer(app, s3, "HEAD", 503)
    assert human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags}).status_code == 502
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(s3))
    n_uploads = len([c for c in s3.calls if c[0] == "POST" and "uploads" in c[2]])
    again = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data), "content_type": "video/mp4", "fingerprint": FP}).json()
    assert again["upload_id"] == up["upload_id"] and again["status"] == "joined" and again["resumed"], again
    assert len([c for c in s3.calls if c[0] == "POST" and "uploads" in c[2]]) == n_uploads  # no new multipart upload
    assert human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={}).json()["status"] == "ready"
    same = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data), "content_type": "video/mp4", "fingerprint": FP}).json()
    assert same["upload_id"] == up["upload_id"] and same["status"] == "ready"   # ready and unclaimed: a no-op


# ---------------------------------------------------------------- 5: the stale sweep vs a finish in progress
def test_a_sweep_during_the_checks_is_never_undone_by_the_finish(human, app, s3, monkeypatch):  # noqa: F811
    up, etags = _upload(human, s3, MP4_HEAD + b"v" * 1000)
    tu = app.state.house.take_uploads
    real = tu._check_object

    def check_while_swept(d):
        # the sweep runs while the checks are in flight, and the store doesn't confirm its DELETE (row kept for later)
        _answer(app, s3, "DELETE", 503)
        tu.purge_stale(now=time.time() + take_upload.TTL_OPEN + 60)
        tu.http = httpx.Client(transport=httpx.MockTransport(s3))
        real(d)
    monkeypatch.setattr(tu, "_check_object", check_while_swept)
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 410, r.text
    assert _status(app, up["upload_id"]) == "del_ready"                             # still marked for the sweep
    assert tu.signed_url(up["upload_id"]) == ""                                    # no take or worker can use it


def test_the_sweep_leaves_a_row_whose_status_changed_since_it_read_it(app, s3, human):  # noqa: F811
    up = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 100, "fingerprint": "x"}).json()
    st, tu = app.state.store, app.state.house.take_uploads
    st.x("UPDATE take_uploads SET updated=0 WHERE id=?", (up["upload_id"],))
    real_q = st.q

    def racing_q(sql, args=()):
        out = real_q(sql, args)
        if sql.startswith("SELECT * FROM take_uploads WHERE id=? AND take_id IS NULL AND updated<?"):
            st.x("UPDATE take_uploads SET status='joined', updated=? WHERE id=?", (time.time(), up["upload_id"]))  # a finish moved it on
        return out
    st.q = racing_q
    try:
        assert tu.purge_stale() == 0
    finally:
        st.q = real_q
    assert _status(app, up["upload_id"]) == "joined"
