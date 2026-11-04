"""One take: delete into the Bin (takes and clips) and multipart uploads straight to an S3-compatible bucket (owner ask,
4 Nov 2026). UI = REST = MCP.

Pins: the owner deletes a take or a clip into the Bin (7 days), its files stop being served, a running job is cancelled
and the worker's late callbacks never land; someone else's take and a missing one are the same 404 and nothing changes;
restore brings the take and its files back; purge removes the run, its jobs and every stored file and notes the tokens as
deleted by their owner (so /healthz never counts them missing). Uploads: SigV4 presigned parts to a stub S3, resume from the
store's own part list, ETag / size / container checks on complete, the 4 GB cap, ownership, abort cleanup, the worker's
presigned GET, and the Library fallback (100 MB) when no bucket is configured."""
import base64
import hashlib
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import take_upload
from studio.library import TRASH_TOKEN

from .conftest import agent_token, mcp_call
from .test_take import ANALYSIS, _fake_mp4_meta

MP4_HEAD = b"\x00\x00\x00\x18ftypmp42"


# ---------------------------------------------------------------- a tiny S3 (just what multipart needs)
class StubS3:
    def __init__(self):
        self.uploads, self.objects, self.calls = {}, {}, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlparse(str(req.url)).query, keep_blank_values=True).items()}
        assert q.get("X-Amz-Algorithm") == "AWS4-HMAC-SHA256" and re.fullmatch(r"[0-9a-f]{64}", q.get("X-Amz-Signature", ""))
        key = urlparse(str(req.url)).path.split("/", 2)[2]
        self.calls.append((req.method, key, {k: v for k, v in q.items() if not k.startswith("X-Amz")}))
        ns = 'xmlns="http://s3.amazonaws.com/doc/2006-03-01/"'
        if req.method == "POST" and "uploads" in q:
            uid = f"U{len(self.uploads) + 1}"
            self.uploads[uid] = {"key": key, "parts": {}}
            return httpx.Response(200, content=f"<InitiateMultipartUploadResult {ns}><UploadId>{uid}</UploadId></InitiateMultipartUploadResult>".encode())
        if req.method == "PUT" and "partNumber" in q:
            body = req.read()
            self.uploads[q["uploadId"]]["parts"][int(q["partNumber"])] = body
            return httpx.Response(200, headers={"ETag": f'"{hashlib.md5(body).hexdigest()}"'})  # noqa: S324 - S3's ETag is MD5
        if req.method == "GET" and "uploadId" in q:
            up = self.uploads.get(q["uploadId"])
            if not up:
                return httpx.Response(404)
            parts = "".join(f"<Part><PartNumber>{n}</PartNumber><ETag>\"{hashlib.md5(b).hexdigest()}\"</ETag><Size>{len(b)}</Size></Part>"  # noqa: S324
                            for n, b in sorted(up["parts"].items()))
            return httpx.Response(200, content=f"<ListPartsResult {ns}><IsTruncated>false</IsTruncated>{parts}</ListPartsResult>".encode())
        if req.method == "POST" and "uploadId" in q:
            up = self.uploads.pop(q["uploadId"])
            self.objects[key] = b"".join(b for _, b in sorted(up["parts"].items()))
            return httpx.Response(200, content=f"<CompleteMultipartUploadResult {ns}><Key>{key}</Key></CompleteMultipartUploadResult>".encode())
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-length": str(len(self.objects[key]))}) if key in self.objects else httpx.Response(404)
        if req.method == "GET":
            data = self.objects.get(key)
            if data is None:
                return httpx.Response(404)
            m = re.match(r"bytes=(\d+)-(\d+)", req.headers.get("range", ""))
            return httpx.Response(206 if m else 200, content=data[int(m[1]):int(m[2]) + 1] if m else data)
        if req.method == "DELETE":
            if "uploadId" in q:
                self.uploads.pop(q["uploadId"], None)
            else:
                self.objects.pop(key, None)
            return httpx.Response(204)
        return httpx.Response(400)


@pytest.fixture
def s3(app, monkeypatch):
    for k, v in {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "AKIDTEST", "R2_SECRET_ACCESS_KEY": "dummy-secret-for-tests",
                 "R2_BUCKET": "takes-test"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(take_upload, "PART", 5 * 1024 * 1024)
    stub = StubS3()
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(stub))
    return stub


def _put_parts(stub, urls, data, part, skip=()):
    c = httpx.Client(transport=httpx.MockTransport(stub))
    out = []
    for u in urls:
        n = u["part_number"]
        if n in skip:
            continue
        r = c.put(u["url"], content=data[(n - 1) * part:n * part])
        out.append({"part_number": n, "etag": r.headers["etag"]})
    return out


def _upload(human, stub, data, name="talk.mp4", drop=()):
    up = human.post("/api/takes/uploads", json={"name": name, "size": len(data), "content_type": "video/mp4"}).json()
    urls = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": list(range(1, up["parts"] + 1))}).json()["urls"]
    return up, _put_parts(stub, urls, data, up["part_size"], drop)


# ---------------------------------------------------------------- uploads
def test_multipart_upload_resume_verify_and_the_worker_gets_a_presigned_get(human, app, s3, monkeypatch):
    data = MP4_HEAD + b"v" * (11 * 1024 * 1024)              # 3 parts of 5 MiB (the test part size)
    up, etags = _upload(human, s3, data, drop=(2,))          # the network drops during part 2
    assert up["parts"] == 3 and not up["resumed"]
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 409 and "Part 2" in r.json()["error"]
    again = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data), "content_type": "video/mp4"}).json()
    assert again["resumed"] and again["upload_id"] == up["upload_id"] and [p["part_number"] for p in again["received"]] == [1, 3]
    urls = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": [2]}).json()["urls"]
    etags += _put_parts(s3, urls, data, up["part_size"])
    bad = [dict(p, etag='"' + "0" * 32 + '"') if p["part_number"] == 3 else p for p in etags]
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": bad})
    assert r.status_code == 422 and "ETag" in r.json()["error"]                       # hash mismatch is refused
    done = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags}).json()
    assert done["status"] == "ready" and s3.objects[next(iter(s3.objects))] == data
    # the take uses it; the worker's source is a presigned GET on the bucket, never the web instance
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append((jid, house.issue_job_token(jid, ttl))), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    t = human.post("/api/takes", json={"upload_id": up["upload_id"], "owns": True, "name": "Long talk"}).json()
    assert t.get("status") == "running", t
    jid, jt = sent[0]
    spec = TestClient(app, base_url="https://hq.test").get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()
    src = spec["urls"]["source"]
    assert src.startswith("https://acct.r2.cloudflarestorage.com/takes-test/takes/") and "X-Amz-Signature=" in src
    assert spec["spec"]["source_upload"] == up["upload_id"]
    # the same upload can't feed a second take
    r = human.post("/api/takes", json={"upload_id": up["upload_id"], "owns": True})
    assert r.status_code == 409


def test_upload_ownership_cap_type_abort_and_non_video(human, app, s3):
    st = app.state.store
    st.x("INSERT INTO take_uploads(id,user_id,name,size,part_size,parts,status,object_key,s3_upload,take_id,created,updated) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", ("tu_someoneelse12345", "acc_other", "x.mp4", 10, 10, 1, "open", "takes/o/x.mp4", "U9", None, time.time(), time.time()))
    for path in ("tu_someoneelse12345", "tu_doesnotexist12345", "nope"):
        assert human.get(f"/api/takes/uploads/{path}").status_code == 404
        assert human.post(f"/api/takes/uploads/{path}/complete", json={}).status_code == 404
        assert human.delete(f"/api/takes/uploads/{path}").status_code == 404
    assert st.q("SELECT COUNT(*) n FROM take_uploads WHERE id='tu_someoneelse12345'")[0]["n"] == 1
    big = human.post("/api/takes/uploads", json={"name": "t.mp4", "size": 4097 * 1024 * 1024})
    assert big.status_code == 413 and "4,096 MB" in big.json()["error"]
    assert human.post("/api/takes/uploads", json={"name": "t.exe", "size": 100}).status_code == 415
    assert human.post("/api/takes/uploads", json={"name": "t.mp4", "size": "12"}).status_code == 400
    # abort: the multipart upload is aborted in the store and the record is gone
    up = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 100}).json()
    assert human.delete(f"/api/takes/uploads/{up['upload_id']}").json()["aborted"]
    assert any(m == "DELETE" and "uploadId" in q for m, _, q in s3.calls) and not s3.uploads
    assert human.get(f"/api/takes/uploads/{up['upload_id']}").status_code == 404
    # bytes that aren't a video are refused on complete and removed from the bucket
    junk = b"MZ" + b"x" * 200
    up, etags = _upload(human, s3, junk, name="junk.mp4")
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 415 and not s3.objects


def test_without_a_bucket_uploads_fall_back_to_the_library_limit(human, monkeypatch):
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    cfg = human.get("/api/takes/uploads/config").json()
    assert cfg["multipart"] is False and cfg["max_bytes"] == 100 * 1024 * 1024 and cfg["fallback"]["path"].startswith("/api/library/upload")
    r = human.post("/api/takes/uploads", json={"name": "t.mp4", "size": 10})
    assert r.status_code == 503 and "100 MB" in r.json()["error"]


def test_presign_matches_aws_sigv4():
    """A known-answer vector, cross-checked with botocore's presigner when this was written (4 Nov 2026)."""
    from datetime import datetime, timezone
    cfg = {"endpoint": "https://acct.r2.cloudflarestorage.com", "key": "AKID", "secret": "SECRET", "bucket": "bkt", "region": "auto"}
    u = take_upload.presign(cfg, "PUT", "takes/x/tu_a.mp4", {"partNumber": "3", "uploadId": "U1"}, 3600,
                            datetime(2026, 11, 4, 21, 0, 0, tzinfo=timezone.utc))
    assert u.endswith("X-Amz-Signature=c7d8b0ba12facffdc444ffd361d656e6cf29bcd92274be2312ad5aebf50ed186")


# ---------------------------------------------------------------- delete into the Bin
def _rendered_take(human, app, monkeypatch):
    house, sent = app.state.house, []

    def dispatch(jid, workflow="", inputs=None, ttl=1800):
        sent.append((jid, house.issue_job_token(jid, ttl)))
        return True, ""
    monkeypatch.setattr(house, "_dispatch_github", dispatch)
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "name": "My talk"}).json()
    w = TestClient(app, base_url="https://hq.test")
    jid, jt = sent[0]
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": ANALYSIS}).json()["ok"]
    rjid, rjt = sent[1]
    for cid in ("c1", "c2"):
        w.post(f"/api/worker/jobs/{rjid}/clips/{cid}", headers={"x-job-token": rjt},
               json={"data": base64.b64encode(MP4_HEAD + cid.encode()).decode(), "cover": base64.b64encode(b"\xff\xd8\xffcov").decode(),
                     "meta": _fake_mp4_meta()})
    return t["id"], w, (rjid, rjt), up["token"]


def test_owner_deletes_a_take_into_the_bin_job_cancelled_restore_then_purge_removes_files(human, app, monkeypatch):
    rid, w, (rjid, rjt), src = _rendered_take(human, app, monkeypatch)
    st = app.state.store
    t = human.get(f"/api/takes/{rid}").json()
    url = next(c["url"] for c in t["clips"] if c.get("url"))
    tok = url.rsplit("/m/", 1)[1]
    assert w.get(f"/m/{tok}").status_code == 200
    # c3 is still rendering: deleting the take cancels its job, and the worker's late clip never lands
    out = human.delete(f"/api/takes/{rid}").json()
    assert out["deleted"] and out["in_bin_days"] == 7 and out["cancelled_jobs"] == 1
    assert st.q("SELECT status FROM render_jobs WHERE id=?", (rjid,))[0]["status"] == "cancelled"
    late = w.post(f"/api/worker/jobs/{rjid}/clips/c3", headers={"x-job-token": rjt},
                  json={"data": base64.b64encode(MP4_HEAD).decode(), "cover": base64.b64encode(b"\xff\xd8").decode(), "meta": _fake_mp4_meta()})
    assert late.status_code == 403
    assert human.get(f"/api/takes/{rid}").status_code == 404
    assert rid not in [x["id"] for x in human.get("/api/takes").json()]
    assert w.get(f"/m/{tok}").status_code == 404                                       # files stop being served
    b = human.get("/api/takes/bin").json()
    assert [x["take_id"] for x in b["takes"]] == [rid] and b["takes"][0]["days_left"] == 7
    # restore: the take and its files come back; the cancelled render is said, not left spinning
    r = human.post(f"/api/takes/{rid}/restore").json()
    assert r["restored"] and r["status"] == "failed"
    assert w.get(f"/m/{tok}").status_code == 200
    t = human.get(f"/api/takes/{rid}").json()
    assert {c["id"]: c["status"] for c in t["clips"]}["c3"] == "failed"
    # delete again and purge: run, jobs, clip / cover / source rows gone, tokens noted as deleted by their owner
    human.delete(f"/api/takes/{rid}")
    p = human.post("/api/takes/bin/purge", json={"take_ids": [rid]}).json()
    assert p["purged_takes"] == [rid]
    assert st.q("SELECT COUNT(*) n FROM agent_runs WHERE id=?", (rid,))[0]["n"] == 0
    assert st.q("SELECT COUNT(*) n FROM render_jobs WHERE run_id=?", (rid,))[0]["n"] == 0
    for x in (tok, TRASH_TOKEN + tok, src):
        assert st.q("SELECT COUNT(*) n FROM media WHERE token=?", (x,))[0]["n"] == 0
    assert st.q("SELECT COUNT(*) n FROM library_purged WHERE token=?", (tok,))[0]["n"] == 1
    assert human.post(f"/api/takes/{rid}/restore").status_code == 404
    assert app.state.house.media_audit()["unique_files_missing"] == 0


def test_someone_elses_take_and_a_missing_one_are_the_same_404(human, app, monkeypatch):
    st = app.state.store
    theirs = st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                  ("acc_other", "take", json.dumps({"name": "theirs", "channels": []}), "ready", "[]",
                   json.dumps({"clips": [{"id": "c1", "status": "ready", "token": "tok_theirs.mp4"}]}), time.time(), time.time()))
    for rid in (theirs, 999999, "abc"):
        for r in (human.delete(f"/api/takes/{rid}"), human.delete(f"/api/takes/{rid}/clips/c1"),
                  human.post(f"/api/takes/{rid}/restore"), human.post(f"/api/takes/{rid}/clips/c1/restore")):
            assert r.status_code == 404, (rid, r.text)
            assert "theirs" not in r.text
    p = human.post("/api/takes/bin/purge", json={"take_ids": [theirs]}).json()
    assert p["purged_takes"] == [] and p["refused"]
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (theirs,))[0]["status"] == "ready"
    assert human.post("/api/takes/bin/purge", json={"take_ids": "1"}).status_code == 400


def test_delete_and_restore_one_clip_and_the_bin_purge_after_7_days(human, app, monkeypatch):
    rid, w, _, _ = _rendered_take(human, app, monkeypatch)
    st, house = app.state.store, app.state.house
    t = human.get(f"/api/takes/{rid}").json()
    tok = next(c["url"] for c in t["clips"] if c["id"] == "c1").rsplit("/m/", 1)[1]
    assert human.delete(f"/api/takes/{rid}/clips/c1").json()["deleted"]
    assert "c1" not in [c["id"] for c in human.get(f"/api/takes/{rid}").json()["clips"]]
    assert w.get(f"/m/{tok}").status_code == 404
    assert human.delete(f"/api/takes/{rid}/clips/c1").status_code == 404                # already in the Bin
    assert [(c["take_id"], c["clip_id"]) for c in human.get("/api/takes/bin").json()["clips"]] == [(rid, "c1")]
    assert human.post(f"/api/takes/{rid}/clips/c1/restore").json()["restored"]
    assert w.get(f"/m/{tok}").status_code == 200
    human.delete(f"/api/takes/{rid}/clips/c1")
    assert house.takes.purge_expired(days=0) == 1                                       # the loop's 7-day purge
    assert st.q("SELECT COUNT(*) n FROM media WHERE token IN (?,?)", (tok, TRASH_TOKEN + tok))[0]["n"] == 0
    assert "c1" not in [c["id"] for c in human.get(f"/api/takes/{rid}").json()["clips"]]


def test_mcp_delete_take_and_bin_tools(human, app, monkeypatch):
    rid, _, _, _ = _rendered_take(human, app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"delete_take", "list_take_bin", "restore_take", "purge_take_bin"} <= names
    _, out = mcp_call(human, tok, "delete_take", {"take_id": rid})
    assert out["deleted"]
    _, b = mcp_call(human, tok, "list_take_bin")
    assert [x["take_id"] for x in b["takes"]] == [rid]
    _, out = mcp_call(human, tok, "restore_take", {"take_id": rid})
    assert out["restored"]
    res, msg = mcp_call(human, tok, "delete_take", {"take_id": 424242})
    assert res["isError"] and "No such take" in msg
    doc = open("docs/MCP_TOOLS.md").read()
    assert all(f"`{n}`" in doc for n in ("delete_take", "list_take_bin", "restore_take", "purge_take_bin"))
