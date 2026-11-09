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


def _upload(human, stub, data, name="talk.mp4", drop=(), fp="talk.mp4|1|1730750000000"):
    up = human.post("/api/takes/uploads", json={"name": name, "size": len(data), "content_type": "video/mp4", "fingerprint": fp}).json()
    urls = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": list(range(1, up["parts"] + 1))}).json()["urls"]
    return up, _put_parts(stub, urls, data, up["part_size"], drop)


# ---------------------------------------------------------------- uploads
def test_multipart_upload_resume_verify_and_the_worker_gets_a_presigned_get(human, app, s3, monkeypatch):
    data = MP4_HEAD + b"v" * (11 * 1024 * 1024)              # 3 parts of 5 MiB (the test part size)
    up, etags = _upload(human, s3, data, drop=(2,))          # the network drops during part 2
    assert up["parts"] == 3 and not up["resumed"]
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 409 and "Part 2" in r.json()["error"]
    again = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data), "content_type": "video/mp4",
                                                   "fingerprint": "talk.mp4|1|1730750000000"}).json()
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


def test_bin_constants_match_the_library():
    from studio import library, take_bin
    assert (take_bin.TRASH_DAYS, take_bin.TRASH_TOKEN) == (library.TRASH_DAYS, library.TRASH_TOKEN)


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
                   json.dumps({"clips": [{"id": "c1", "status": "ready", "url": "https://hq.test/m/theirclip.mp4"}]}), time.time(), time.time()))
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


# ---------------------------------------------------------------- nothing fails silently
def test_self_check_fails_loudly_when_a_capability_is_missing(monkeypatch):
    from studio.media import selfcheck
    monkeypatch.setattr(selfcheck, "face_detector", lambda: (False, "OpenCV CascadeClassifier unavailable (AttributeError)"))
    with pytest.raises(selfcheck.CapabilityError) as e:
        selfcheck.check("take_analyze")
    assert "No face detector loads" in str(e.value) and "CascadeClassifier" in str(e.value)
    monkeypatch.setattr(selfcheck, "_ff", lambda args: "")                       # an FFmpeg build without the filters
    monkeypatch.setattr(selfcheck.shutil, "which", lambda b: "/usr/bin/ffmpeg")
    with pytest.raises(selfcheck.CapabilityError) as e:
        selfcheck.check("take_render")
    assert "loudnorm" in str(e.value) and "libx264" in str(e.value)
    from studio import render_worker
    assert render_worker.friendly(selfcheck.CapabilityError("The video worker is missing loudnorm.")) == "The video worker is missing loudnorm."


def test_no_face_found_is_reported_on_the_take(human, app, monkeypatch):
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append((jid, house.issue_job_token(jid, ttl))), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    w = TestClient(app, base_url="https://hq.test")
    w.post(f"/api/worker/jobs/{sent[0][0]}", headers={"x-job-token": sent[0][1]}, json={"analysis": ANALYSIS})
    rjid, rjt = sent[1]
    for cid in ("c1", "c2", "c3"):
        w.post(f"/api/worker/jobs/{rjid}/clips/{cid}", headers={"x-job-token": rjt},
               json={"data": base64.b64encode(MP4_HEAD).decode(), "cover": base64.b64encode(b"\xff\xd8").decode(),
                     "meta": {**_fake_mp4_meta(), "layout": "pad", "face_coverage": None}})
    w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {"clips": 3}})
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["face_fallback"]["note"] == "no face found, centered crop used" and len(t["face_fallback"]["clips"]) == 3
    assert "no face found, centered crop used" in next(s for s in t["steps"] if s["key"] == "reframe")["detail"]


def test_crop_plan_smooths_jitter_and_audio_chain_normalises():
    from studio import take
    rng = [0.5 + (0.03 if i % 2 else -0.03) for i in range(60)]                  # a speaker swaying +-3% around the centre
    faces = [{"t": i / 2, "cx": x, "cy": 0.4, "w": 0.1, "score": 0.9} for i, x in enumerate(rng)]
    plan = take.crop_plan(faces, 0, 29.5, 1920, 1080)
    assert plan["mode"] == "track" and len({x for _, x in plan["keys"]}) == 1      # no pan at all: no jitter
    a = take.audio_chain(30.0)
    assert "loudnorm=I=-14" in a and "afade=t=in" in a and "afade=t=out:st=29.850" in a


# ---------------------------------------------------------------- review fixes (PR #107: Greptile / CodeRabbit)
def test_resume_needs_the_same_file_not_just_the_same_name_and_size(human, s3):
    data = MP4_HEAD + b"v" * (6 * 1024 * 1024)
    up, _ = _upload(human, s3, data, drop=(2,), fp="talk.mp4|6291468|111")
    other = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data), "fingerprint": "talk.mp4|6291468|222"}).json()
    assert not other["resumed"] and other["upload_id"] != up["upload_id"]     # another video, same name and size: a new upload
    bare = human.post("/api/takes/uploads", json={"name": "talk.mp4", "size": len(data)}).json()
    assert not bare["resumed"]                                                  # no fingerprint: never resumed


def test_part_urls_sign_the_content_length(human, s3):
    up = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 7 * 1024 * 1024}).json()
    urls = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": [1, 2]}).json()["urls"]
    for u in urls:
        q = parse_qs(urlparse(u["url"]).query)
        assert q["X-Amz-SignedHeaders"] == ["content-length;host"]
    assert [u["size"] for u in urls] == [5 * 1024 * 1024, 2 * 1024 * 1024]
    from datetime import datetime, timezone
    cfg = {"endpoint": "https://acct.r2.cloudflarestorage.com", "key": "AKID", "secret": "SECRET", "bucket": "bkt", "region": "auto"}
    at = datetime(2026, 11, 4, 21, 0, 0, tzinfo=timezone.utc)
    a = take_upload.presign(cfg, "PUT", "k", {"partNumber": "1", "uploadId": "U"}, 3600, at, length=10)
    b = take_upload.presign(cfg, "PUT", "k", {"partNumber": "1", "uploadId": "U"}, 3600, at, length=11)
    assert a.rsplit("=", 1)[1] != b.rsplit("=", 1)[1]                           # a different length is a different signature


def test_a_timeout_after_the_join_recovers_on_finish_again(human, app, s3):
    data = MP4_HEAD + b"v" * (6 * 1024 * 1024)
    up, etags = _upload(human, s3, data)
    real, heads = s3.__call__, []

    def flaky(req):
        if req.method == "HEAD" and not heads:
            heads.append(1)
            raise httpx.ReadTimeout("slow", request=req)
        return real(req)
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(flaky))
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert r.status_code == 502
    assert app.state.store.q("SELECT status FROM take_uploads WHERE id=?", (up["upload_id"],))[0]["status"] == "joined"
    done = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags}).json()
    assert done["status"] == "ready"


def test_a_failed_store_delete_keeps_the_record_for_the_sweep(human, app, s3):
    up = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 100}).json()
    st, tu = app.state.store, app.state.house.take_uploads
    tu.http = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(503) if req.method == "DELETE" else s3(req)))
    human.delete(f"/api/takes/uploads/{up['upload_id']}")
    row = st.q("SELECT status, updated FROM take_uploads WHERE id=?", (up["upload_id"],))
    assert row and row[0]["status"] == "del_open"                               # not forgotten
    assert human.get(f"/api/takes/uploads/{up['upload_id']}").status_code == 404  # but out of every listing
    tu.http = httpx.Client(transport=httpx.MockTransport(s3))
    assert tu.purge_stale() == 1 and not s3.uploads
    assert not st.q("SELECT 1 FROM take_uploads WHERE id=?", (up["upload_id"],))


def test_two_takes_cant_claim_one_upload(human, app, s3, monkeypatch):
    data = MP4_HEAD + b"v" * 1024
    up, etags = _upload(human, s3, data)
    human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    tu, st = app.state.house.take_uploads, app.state.store
    from studio.actions import ActionError, Actor
    uid = st.q("SELECT user_id FROM take_uploads WHERE id=?", (up["upload_id"],))[0]["user_id"]
    tu.claim_for_take(Actor(uid), up["upload_id"], 101)
    st.x("UPDATE take_uploads SET take_id=NULL WHERE id=?", (up["upload_id"],))   # as if a second start passed the check first
    st.x("UPDATE take_uploads SET take_id=? WHERE id=?", (102, up["upload_id"]))
    with pytest.raises(ActionError) as e:
        tu.claim_for_take(Actor(uid), up["upload_id"], 103)
    assert e.value.status == 409


def test_csp_allows_only_the_configured_bucket_origin(human, s3, monkeypatch):
    csp = human.get("/api/session").headers["content-security-policy"]
    assert "connect-src 'self' https://acct.r2.cloudflarestorage.com;" in csp
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    assert "connect-src 'self';" in human.get("/api/session").headers["content-security-policy"]


def test_deleting_the_account_removes_its_uploads_from_the_bucket(human, app, s3):
    data = MP4_HEAD + b"v" * 1024
    up, etags = _upload(human, s3, data)
    human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    assert s3.objects
    assert human.post("/api/disconnect").json()["deleted"]
    assert not s3.objects
    assert not app.state.store.q("SELECT 1 FROM take_uploads WHERE id=?", (up["upload_id"],))


def test_a_deleted_clip_stays_in_the_bin_when_another_clip_finishes(human, app, monkeypatch):
    rid, w, (rjid, rjt), _ = _rendered_take(human, app, monkeypatch)
    assert human.delete(f"/api/takes/{rid}/clips/c1").json()["deleted"]
    w.post(f"/api/worker/jobs/{rjid}/clips/c3", headers={"x-job-token": rjt},
           json={"data": base64.b64encode(MP4_HEAD + b"c3").decode(), "cover": base64.b64encode(b"\xff\xd8\xffcov").decode(),
                 "meta": _fake_mp4_meta()})
    w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {"clips": 3}})  # render done -> preflight all
    assert "c1" not in [c["id"] for c in human.get(f"/api/takes/{rid}").json()["clips"]]
    assert [(c["take_id"], c["clip_id"]) for c in human.get("/api/takes/bin").json()["clips"]] == [(rid, "c1")]


def test_analysis_workers_arent_held_to_render_filters(monkeypatch):
    from studio.media import selfcheck
    monkeypatch.setattr(selfcheck, "face_detector", lambda: (True, "test"))
    monkeypatch.setattr(selfcheck, "_ff", lambda args: "")
    monkeypatch.setattr(selfcheck.shutil, "which", lambda b: "/usr/bin/" + b)
    import sys
    import types
    monkeypatch.setitem(sys.modules, "faster_whisper", types.ModuleType("faster_whisper"))
    assert selfcheck.check("take_analyze")                                      # no ass / libx264 needed to analyse
    monkeypatch.setattr(selfcheck.shutil, "which", lambda b: None if b == "ffprobe" else "/usr/bin/" + b)
    with pytest.raises(selfcheck.CapabilityError) as e:
        selfcheck.check("take_analyze")
    assert "ffprobe" in str(e.value)


def test_crop_pans_keep_their_keys_in_order():
    from studio import take
    xs = [0.2] * 8 + [0.8] * 4 + [0.3] * 28                                    # the speaker moves twice in quick succession
    faces = [{"t": i / 4, "cx": x, "cy": 0.5, "w": 0.1, "score": 0.9} for i, x in enumerate(xs)]
    keys = take.crop_plan(faces, 0, 10, 1920, 1080, [])["keys"]
    ts = [t for t, _ in keys]
    assert ts == sorted(ts) and len(set(keys)) == len(keys)


def test_a_mixed_clip_with_a_padded_shot_reports_the_fallback_and_a_later_render_clears_it(human, app, monkeypatch):
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append((jid, house.issue_job_token(jid, ttl))), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    w = TestClient(app, base_url="https://hq.test")
    w.post(f"/api/worker/jobs/{sent[0][0]}", headers={"x-job-token": sent[0][1]}, json={"analysis": ANALYSIS})
    rjid, rjt = sent[1]
    for cid in ("c1", "c2", "c3"):
        meta = {**_fake_mp4_meta(), "layout": "mixed", "face_fallback": "no face found, centered crop used"} if cid == "c1" else \
               {**_fake_mp4_meta(), "layout": "track"}
        w.post(f"/api/worker/jobs/{rjid}/clips/{cid}", headers={"x-job-token": rjt},
               json={"data": base64.b64encode(MP4_HEAD).decode(), "cover": base64.b64encode(b"\xff\xd8").decode(), "meta": meta})
    w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {"clips": 3}})
    assert human.get(f"/api/takes/{t['id']}").json()["face_fallback"]["clips"] == ["c1"]


def test_picking_moments_holds_no_lock_and_a_take_deleted_meanwhile_gets_nothing(human, app, monkeypatch):
    """Greptile / CodeRabbit, PR #107: the AI moment pick (up to minutes) ran under a lock every take shared."""
    import threading
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append((jid, house.issue_job_token(jid, ttl))), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    real, seen = house.takes._mine, {}

    def slow_mine(run, analysis, eng, chat):
        out = {}
        th = threading.Thread(target=lambda: out.update(r=human.delete(f"/api/takes/{t['id']}").json()))
        th.start()
        th.join(10)                                       # would hang if the take's lock were held while picking
        seen["deleted"] = out.get("r", {}).get("deleted")
        return real(run, analysis, eng, chat)
    monkeypatch.setattr(house.takes, "_mine", slow_mine)
    w = TestClient(app, base_url="https://hq.test")
    r = w.post(f"/api/worker/jobs/{sent[0][0]}", headers={"x-job-token": sent[0][1]}, json={"analysis": ANALYSIS}).json()
    assert seen["deleted"] is True and r.get("cancelled")
    row = app.state.store.q("SELECT status, result FROM agent_runs WHERE id=?", (t["id"],))[0]
    assert row["status"] == "binned" and not json.loads(row["result"]).get("clips") and len(sent) == 1   # no render queued


def test_a_take_deleted_and_restored_while_moments_are_picked_doesnt_start_rendering(human, app, monkeypatch):
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append((jid, house.issue_job_token(jid, ttl))), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(MP4_HEAD + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    real = house.takes._mine

    def mine_then_bin_and_back(run, analysis, eng, chat):
        from studio.actions import Actor
        uid = app.state.store.q("SELECT user_id FROM agent_runs WHERE id=?", (t["id"],))[0]["user_id"]
        house.takes.delete_take(Actor(uid), t["id"])
        house.takes.restore_take(Actor(uid), t["id"])
        return real(run, analysis, eng, chat)
    monkeypatch.setattr(house.takes, "_mine", mine_then_bin_and_back)
    w = TestClient(app, base_url="https://hq.test")
    r = w.post(f"/api/worker/jobs/{sent[0][0]}", headers={"x-job-token": sent[0][1]}, json={"analysis": ANALYSIS}).json()
    assert r.get("cancelled") and len(sent) == 1                                 # no render queued
    assert human.get(f"/api/takes/{t['id']}").json()["status"] == "failed"


def test_the_7_day_sweep_spares_a_take_restored_and_deleted_again(human, app, monkeypatch):
    rid, _, _, _ = _rendered_take(human, app, monkeypatch)
    st, tk = app.state.store, app.state.house.takes
    human.delete(f"/api/takes/{rid}")
    res = json.loads(st.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    res["bin"]["at"] = time.time()                                               # deleted again just now
    st.x("UPDATE agent_runs SET result=? WHERE id=?", (json.dumps(res), rid))
    uid = st.q("SELECT user_id FROM agent_runs WHERE id=?", (rid,))[0]["user_id"]
    assert tk._purge(uid, [rid], [], binned_before=time.time() - 7 * 86400)[0] == []
    assert st.q("SELECT status FROM agent_runs WHERE id=?", (rid,))[0]["status"] == "binned"


def test_the_watchdog_sweep_never_runs_inside_a_take_lock(app):
    """CodeRabbit, PR #107: a callback holding take B's lock swept a stale job of take A (A's lock) while a purge held A
    and waited for B: a deadlock. The sweep is skipped on a thread that holds a take lock."""
    house, ran = app.state.house, []
    house.sweep_stale_jobs = lambda *a, **k: ran.append(1) or []
    with house.takes._take_lock(1):
        house._sweep_quietly()
    assert ran == []
    house._sweep_quietly()
    assert ran == [1]


def test_cleanup_never_deletes_an_upload_a_take_just_claimed(human, app, s3):
    data = MP4_HEAD + b"v" * 1024
    up, etags = _upload(human, s3, data)
    human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={"parts": etags})
    st, tu = app.state.store, app.state.house.take_uploads
    st.x("UPDATE take_uploads SET updated=0 WHERE id=?", (up["upload_id"],))
    real_q = st.q

    def q(sql, args=()):
        out = real_q(sql, args)
        if sql.startswith("SELECT id FROM take_uploads WHERE take_id IS NULL"):  # claimed right after the sweep listed it
            st.x("UPDATE take_uploads SET take_id=7 WHERE id=?", (up["upload_id"],))
        return out
    st.q = q
    try:
        assert tu.purge_stale() == 0
    finally:
        st.q = real_q
    assert s3.objects and st.q("SELECT take_id FROM take_uploads WHERE id=?", (up["upload_id"],))[0]["take_id"] == 7


def test_a_malformed_create_answer_from_the_store_is_a_clean_502(human, app, s3):
    """CodeRabbit, PR #107: CreateMultipartUpload's answer was parsed unguarded, so a non-XML body was a 500."""
    app.state.house.take_uploads.http = httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"<not xml") if req.method == "POST" else s3(req)))
    r = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 100, "fingerprint": "a.mp4|100|1"})
    assert r.status_code == 502
    assert not app.state.store.q("SELECT 1 FROM take_uploads")


def test_the_cleanup_sweep_lets_go_of_the_claim_lock_before_the_bucket_delete(human, app, s3):
    """Greptile, PR #107: purge_stale held the one claim lock every user's take start needs while a bucket DELETE ran.
    The row is marked for deletion under the lock (so no take can claim it), and the DELETE runs outside it."""
    up = human.post("/api/takes/uploads", json={"name": "a.mp4", "size": 100}).json()
    st, tu = app.state.store, app.state.house.take_uploads
    st.x("UPDATE take_uploads SET updated=0 WHERE id=?", (up["upload_id"],))
    held = []

    def stub(req):
        if req.method == "DELETE":
            held.append(take_upload._CLAIM.locked())
            held.append(st.q("SELECT status FROM take_uploads WHERE id=?", (up["upload_id"],))[0]["status"])
        return s3(req)
    tu.http = httpx.Client(transport=httpx.MockTransport(stub))
    assert tu.purge_stale() == 1
    assert held == [False, "del_open"]
    assert not st.q("SELECT 1 FROM take_uploads WHERE id=?", (up["upload_id"],)) and not s3.uploads


def test_a_mediapipe_model_that_fails_to_load_falls_back_to_haar(monkeypatch, tmp_path):
    """CodeRabbit, PR #107: face_track caught only OSError around the MediaPipe detector, so any other load failure
    escaped instead of falling back to the Haar cascade the self-check vouches for."""
    import sys
    import types

    from studio import take
    model = tmp_path / "m.tflite"
    model.write_bytes(b"x")

    class Det:
        @staticmethod
        def create_from_options(_o):
            raise RuntimeError("Unable to open the model")
    vision = types.SimpleNamespace(FaceDetector=Det, FaceDetectorOptions=lambda **k: k)
    tasks_py = types.ModuleType("mediapipe.tasks.python")
    tasks_py.BaseOptions, tasks_py.vision = (lambda **k: k), vision
    for name, mod in {"mediapipe": types.ModuleType("mediapipe"), "mediapipe.tasks": types.ModuleType("mediapipe.tasks"),
                      "mediapipe.tasks.python": tasks_py}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    try:
        import numpy  # noqa: F401
    except ImportError:
        monkeypatch.setitem(sys.modules, "numpy", types.ModuleType("numpy"))
    seen = []
    monkeypatch.setattr(take, "_face_track_cv2", lambda path, fps, why: seen.append(why) or ([], why))
    faces, why = take.face_track("clip.mp4", 3.0, model_path=str(model))
    assert faces == [] and seen and "couldn't load" in why


def test_past_an_r2_cap_no_new_put_capability_is_issued(human, app, s3, monkeypatch):
    """Part URLs, the completion POST and the worker's link PUT all check the free-tier caps first (CodeRabbit, PR #120)."""
    up = human.post("/api/takes/uploads", json={"name": "c.mp4", "size": 7 * 1024 * 1024}).json()
    monkeypatch.setenv("HQ_R2_MAX_CLASS_A", "0")
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": [1]})
    assert r.status_code == 507 and "paused" in r.json()["error"]
    tu = app.state.house.take_uploads
    from studio import r2usage
    monkeypatch.setattr(tu, "_list_parts", lambda d: {1: ("e1", 5 * 1024 * 1024), 2: ("e2", 2 * 1024 * 1024)})
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={})
    assert r.status_code == 507 and r2usage.can_write(0)
    assert tu.link_target("acc_1", 4242) == ""                           # the worker streams the link instead


def test_part_urls_never_cross_the_class_a_cap_and_reads_stop_at_the_class_b_cap(human, app, s3, monkeypatch):
    from studio import r2usage
    up = human.post("/api/takes/uploads", json={"name": "d.mp4", "size": 7 * 1024 * 1024}).json()
    used = r2usage.ops()["A"]
    monkeypatch.setenv("HQ_R2_MAX_CLASS_A", str(used + 1))               # room for one more signature, not two
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": [1, 2]})
    assert r.status_code == 507
    monkeypatch.delenv("HQ_R2_MAX_CLASS_A")
    monkeypatch.setenv("HQ_R2_MAX_CLASS_B", "0")
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/complete", json={})
    assert r.status_code == 507                                          # the part-list GET isn't signed


def test_link_puts_count_at_their_cap_until_stored_and_an_open_upload_isnt_counted_twice(human, app, s3, monkeypatch):
    """Concurrent link uploads can't together pass HQ_R2_MAX_BYTES; a fresh upload's part URLs aren't refused because its
    size was counted both as a reservation and as its row (CodeRabbit, PR #120)."""
    from studio import r2usage
    tu = app.state.house.take_uploads
    monkeypatch.setenv("HQ_R2_MAX_BYTES", str(take_upload.max_bytes() + 10 * 1024 * 1024))
    assert tu.link_target("acc_1", 501)                                  # one in-flight link: counted at max_bytes()
    assert tu.link_target("acc_1", 502) == ""                            # a second would pass the cap: streamed instead
    assert r2usage.stored_bytes() >= take_upload.max_bytes()
    up = human.post("/api/takes/uploads", json={"name": "e.mp4", "size": 7 * 1024 * 1024}).json()
    r = human.post(f"/api/takes/uploads/{up['upload_id']}/parts", json={"parts": [1, 2]})
    assert r.status_code == 200 and len(r.json()["urls"]) == 2
    assert r2usage._reserved() == 0                                      # settled: the rows carry the bytes


def test_a_full_library_refuses_new_one_take_videos_on_every_path(human, app, s3, monkeypatch):
    """Owner bug 9 Nov 2026: "when the 200mb limit is reached still the user is able to upload". One take's large
    uploads go to the bucket, not through the Library upload's quota check, and a link take never uploads at all; both
    still saved their clips into a full Library. Now the bucket upload, the take start (upload, upload_id or link) and
    the form's config all say the Library is full, before a byte moves."""
    from studio import library
    lib = app.state.house.library
    up = human.post("/api/takes/uploads", json={"name": "ok.mp4", "size": 100}).json()   # room left: uploads work
    assert up["upload_id"]
    cfg = human.get("/api/takes/uploads/config").json()
    assert cfg["library"]["quota"] == library.QUOTA and cfg["library"]["used"] < library.QUOTA
    monkeypatch.setattr(type(lib), "used", lambda self, uid: library.QUOTA)
    assert human.get("/api/takes/uploads/config").json()["library"]["used"] == library.QUOTA
    r = human.post("/api/takes/uploads", json={"name": "t.mp4", "size": 100})
    assert r.status_code == 413 and "Library is full" in r.json()["error"] and "One take" in r.json()["error"]
    n = len(s3.uploads)
    for body in ({"upload_id": up["upload_id"], "owns": True}, {"url": "https://example.com/talk.mp4", "owns": True},
                 {"media_token": "nope.mp4", "owns": True}):
        r = human.post("/api/takes", json=body)
        assert r.status_code == 413 and "Library is full" in r.json()["error"], (body, r.text)
    assert len(s3.uploads) == n
    assert not app.state.store.q("SELECT id FROM agent_runs WHERE kind='take'")
