"""Video links in One take and over MCP with the person's rights consent (owner ask, 4 Nov 2026). UI = REST = MCP.

Pins: a link is never fetched before a signed-in person confirms the rights statement (403 without it; 409 for an old
statement); each confirmation is a link_consents row (person, link, hash, time, statement version); an AI app gets
needs_user_approval and an Inbox item, can't confirm or approve it itself, and after the person approves, the same call
queues the take with the url once; the worker fetches through Make a post's own path (studio.media.download) with
One take's 4 GB / 3 h caps; a platform refusal names its reason (age-restricted is not a bot block) and asks for the file;
direct links go through netguard. yt-dlp is mocked: no network."""
import sys
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import link_ingest, netguard, takes
from studio.media import download

from .conftest import agent_token, mcp_call
from .test_take_opus import YT, _github, _hosts


def _consents(app):
    st = app.state.store
    link_ingest.ensure(st)
    return [dict(r) for r in st.q("SELECT * FROM link_consents ORDER BY id")]


# ---------------------------------------------------------------- the One take form / REST
def test_a_link_needs_the_rights_statement_before_anything_is_fetched(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    r = human.post("/api/takes", json={"url": YT, "owns": True})
    assert r.status_code == 403 and "I own this video or have permission" in r.json()["error"]
    assert sent == [] and _consents(app) == []                                         # nothing queued, nothing recorded
    r = human.post("/api/takes", json={"url": YT, "owns": True, "rights_attested": True, "attestation_version": "2020-01-01"})
    assert r.status_code == 409 and sent == []                                          # the person saw an old statement
    t = human.post("/api/takes", json={"url": YT, "owns": True, "rights_attested": True,
                                       "attestation_version": link_ingest.ATTESTATION_VERSION}).json()
    assert t["input"]["platform_url"] == YT and t["input"]["consent_id"]
    c = _consents(app)
    assert len(c) == 1 and c[0]["url"] == YT and c[0]["via"] == "app" and c[0]["take_id"] == t["id"]
    assert c[0]["attestation_version"] == link_ingest.ATTESTATION_VERSION and c[0]["attestation"] == link_ingest.ATTESTATION
    assert c[0]["url_hash"] == link_ingest.url_hash("https://youtu.be/QJf0H64BnFo")   # one hash per video
    jid, wf, inputs, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    job = w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()
    assert job["spec"]["platform_url"] == YT and "source_put" not in job["urls"]        # no bucket: streamed on the worker only


def test_bad_links_are_refused_before_any_consent(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    r = human.post("/api/takes", json={"url": "https://127.0.0.1/a.mp4", "owns": True, "rights_attested": True})
    assert r.status_code == 400                                                         # netguard host rule first
    r = human.post("/api/takes", json={"url": "http://cdn.example.com/a.mp4", "owns": True, "rights_attested": True})
    assert r.status_code == 400 and _consents(app) == []


# ---------------------------------------------------------------- MCP: the person approves, the app can't
def test_an_ai_app_waits_for_the_person_and_cannot_approve_itself(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    res, out = mcp_call(human, tok, "ingest_video_link", {"url": YT, "owns": True, "rights_attested": True})
    assert out["needs_user_approval"] is True and out["fetched"] is False and sent == []
    aid = out["approval_id"]
    again = mcp_call(human, tok, "ingest_video_link", {"url": "https://youtu.be/QJf0H64BnFo"}, mid=2)[1]
    assert again["approval_id"] == aid                                                  # the same video: one Inbox item
    items = human.get("/api/approvals").json()
    mine = [x for x in items if x["id"] == aid]
    assert mine and mine[0]["kind"] == "link_rights" and mine[0]["payload"]["attestation"] == link_ingest.ATTESTATION
    inbox = human.get("/api/inbox").json()
    appr = next(g for g in inbox["groups"] if g["key"] == "approvals")
    assert any(i.get("kind") == "link_rights" for i in appr["items"])
    # the app's own token can't approve, and nothing it passes counts as the person's consent
    bot = TestClient(app, base_url="https://hq.test")
    r = bot.post(f"/api/approvals/{aid}/approve", headers={"Authorization": f"Bearer {tok}"}, json={"rights_attested": True})
    assert r.status_code == 403
    assert _consents(app) == [] and sent == []
    # the person must tick the statement to approve
    r = human.post(f"/api/approvals/{aid}/approve", json={})
    assert r.status_code == 403 and _consents(app) == []
    ok = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True}).json()
    assert ok["status"] == "approved" and ok["kind"] == "link_rights"
    c = _consents(app)
    assert len(c) == 1 and c[0]["via"] == "mcp_approval" and c[0]["approval_id"] == aid and c[0]["agent_id"]
    # a wrong approval id for this link is a 404 (no probing other items); REST keeps the id as given
    r = bot.post("/api/takes", headers={"Authorization": f"Bearer {tok}"}, json={"url": YT, "approval_id": aid + 99})
    assert r.status_code == 404 and "No approval with that id" in r.json()["error"] and len(sent) == 0
    t = mcp_call(human, tok, "ingest_video_link", {"url": YT, "approval_id": aid}, mid=4)[1]
    assert t["id"] and t["input"]["platform_url"] == YT and t["input"]["consent_id"] == c[0]["id"] and len(sent) == 1
    # used once: the next take from the same link asks the person again
    nxt = mcp_call(human, tok, "ingest_video_link", {"url": YT}, mid=5)[1]
    assert nxt["needs_user_approval"] is True and nxt["approval_id"] != aid and len(sent) == 1


def test_clips_from_video_with_a_url_goes_through_the_same_gate(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    out = mcp_call(human, tok, "clips_from_video", {"url": YT, "owns": True})[1]
    assert out["needs_user_approval"] is True and out["tool"] == "clips_from_video" and sent == []


def test_tools_list_and_instructions_send_links_to_the_ingest_tool():
    from studio import mcp
    names = {t["name"] for t in mcp.TOOLS}
    assert "ingest_video_link" in names
    tool = next(t for t in mcp.TOOLS if t["name"] == "ingest_video_link")
    assert "don't ask them to upload" in tool["description"] and "Inbox → Approvals" in tool["description"]
    assert "not a social media page" not in next(t for t in mcp.TOOLS if t["name"] == "clips_from_video")["description"]


def test_a_foreign_or_missing_take_is_404(human):
    assert human.get("/api/takes/987654").status_code == 404


# ---------------------------------------------------------------- the worker's fetch (yt-dlp mocked)
def _ytdlp(monkeypatch, *, raise_msg: str = "", info: dict | None = None):
    seen = {}

    class Y:
        def __init__(self, opts):
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=True):
            seen["url"] = url
            if raise_msg:
                raise RuntimeError(raise_msg)
            i = dict(info or {})
            mf = seen["opts"].get("match_filter")
            if mf and mf(i):
                return i                                          # yt-dlp skips the download when the filter says why
            out = Path(seen["opts"]["outtmpl"].replace(".%(ext)s", ".mp4"))
            out.write_bytes(b"x")
            return {**i, "requested_downloads": [{"filepath": str(out)}]}
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=Y))
    monkeypatch.setattr(download.netguard, "check_url", lambda *a, **k: a[0])
    monkeypatch.delenv("HQ_YT_FALLBACK", raising=False)
    return seen


@pytest.mark.parametrize("msg,want", [
    ("ERROR: [youtube] x: Sign in to confirm your age. This video may be inappropriate for some users.", "age-restricted"),
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "private"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot.", "blocked our server"),
    ("ERROR: [youtube] x: Video unavailable. This video has been removed", "unavailable"),
])
def test_a_refusal_names_its_reason_and_asks_for_the_file(monkeypatch, tmp_path, msg, want):
    _ytdlp(monkeypatch, raise_msg=msg)
    with pytest.raises(takes.take.TakeError) as e:
        takes.fetch_platform(YT, tmp_path / "s.mp4")
    assert want in str(e.value) and "upload" in str(e.value).lower()
    if want == "age-restricted":
        assert "blocks servers" not in str(e.value) and "never uses accounts or cookies" in str(e.value)


def test_the_fetch_is_make_a_posts_path_with_one_takes_caps(monkeypatch, tmp_path):
    seen = _ytdlp(monkeypatch, info={"duration": 600, "title": "Talk", "license": "Creative Commons Attribution license (reuse allowed)"})
    monkeypatch.setattr(download.mediasafe, "check_source", lambda p, max_bytes=None: {"format": {"format_name": "mp4"}})
    meta = takes.fetch_platform(YT, tmp_path / "s.mp4")
    o = seen["opts"]
    assert o["allowed_extractors"] == ["Youtube"] and "height<=1080" in o["format"] and o["merge_output_format"] == "mp4"
    assert o["max_filesize"] == 4096 * 1024 * 1024 and "cookiefile" not in o and "cookiesfrombrowser" not in o
    assert meta["licence"].startswith("Creative Commons") and meta["title"] == "Talk"


def test_duration_and_size_caps_refuse_before_downloading(monkeypatch, tmp_path):
    _ytdlp(monkeypatch, info={"duration": 4 * 3600})
    with pytest.raises(takes.take.TakeError, match="up to 3 h"):
        takes.fetch_platform(YT, tmp_path / "s.mp4")
    assert not (tmp_path / "s.mp4").exists()
    _ytdlp(monkeypatch, info={"duration": 60, "filesize": 5 * 1024 ** 3})
    with pytest.raises(takes.take.TakeError, match="over 4096 MB"):
        takes.fetch_platform(YT, tmp_path / "s.mp4")


def test_direct_links_go_through_netguard(tmp_path):
    for bad in ("https://169.254.169.254/latest/video.mp4", "https://127.0.0.1/a.mp4", "https://[fd00:ec2::254]/a.mp4"):
        with pytest.raises(netguard.FetchError):
            download.download_video(bad, tmp_path / "d.mp4")


def test_a_blocked_fetch_shows_on_the_take(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    t = human.post("/api/takes", json={"url": YT, "owns": True, "rights_attested": True}).json()
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    msg = "YouTube blocked our server (a bot check), so it couldn't fetch this video. Download it yourself and upload the file."
    w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"error": f"TakeError: {msg}"})
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "failed" and "Download it yourself and upload the file" in str(t["steps"])


def test_attestation_text_matches_the_ui():
    static = Path(takes.__file__).parent / "static"
    html, js = (static / "index.html").read_text(), (static / "take.js").read_text()
    assert link_ingest.ATTESTATION in html.replace("&#39;", "'")
    assert f'TK_ATTEST_V = "{link_ingest.ATTESTATION_VERSION}"' in js
    priv = (static / "privacy.html").read_text()
    assert "7 days" in priv and "Video link consents" in priv and "yt-dlp" in priv


def test_an_approval_covers_only_that_link_on_its_own_host():
    h = link_ingest.url_hash
    assert h("https://vimeo.com/123") != h("https://attacker.example/big.mp4?x=vimeo.com/123")
    assert h("https://x.com/a/status/123") != h("https://evil.example/v.mp4#x.com/a/status/123")
    assert h("https://notx.com/a/status/123") != h("https://x.com/a/status/123")
    assert h("https://youtu.be/QJf0H64BnFo") == h("https://www.youtube.com/watch?v=QJf0H64BnFo")


def test_the_fallbacks_keep_the_duration_cap(monkeypatch, tmp_path):
    from studio.media import yt_fallback
    _ytdlp(monkeypatch, raise_msg="ERROR: [youtube] x: Sign in to confirm you're not a bot.")
    monkeypatch.setenv("HQ_YT_FALLBACK", "invidious")
    monkeypatch.setenv("HQ_INVIDIOUS_INSTANCES", "https://inv.example")
    monkeypatch.setattr(yt_fallback, "_get_json", lambda url, http=None: {"lengthSeconds": 5 * 3600, "adaptiveFormats": []})
    pulled = []
    monkeypatch.setattr(yt_fallback, "_download", lambda *a, **k: pulled.append(a))
    with pytest.raises(takes.take.TakeError, match="up to 3 h"):
        takes.fetch_platform(YT, tmp_path / "s.mp4")
    assert pulled == []


def test_postgres_returns_the_consent_id():
    from studio import store
    assert "link_consents" in store.ID_TABLES


def test_make_a_post_split_asks_for_the_statement(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    body = {"prompt": f"split this into 5 shorts {YT}", "channel_ids": ["ch_ig"], "rights_confirmed": True}
    r = human.post("/api/agent/runs", json=body)
    assert r.status_code == 409 and link_ingest.ATTESTATION in r.json()["error"] and _consents(app) == []
    t = human.post("/api/agent/runs", json={**body, "rights_attested": True}).json()
    assert t["kind"] == "take" and _consents(app)[0]["take_id"] == t["id"]


def test_an_approval_asked_under_an_older_statement_is_refused(human, app, monkeypatch):
    """The statement version is part of the consent: an item the app asked under an older statement can't be approved
    (409, closed, nothing recorded), and the app's next call makes a fresh item with the new text."""
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    old = mcp_call(human, tok, "ingest_video_link", {"url": YT})[1]
    assert old["needs_user_approval"] is True
    monkeypatch.setattr(link_ingest, "ATTESTATION_VERSION", "2099-01-01.1")       # the statement changed after the ask
    r = human.post(f"/api/approvals/{old['approval_id']}/approve", json={"rights_attested": True})
    assert r.status_code == 409 and "statement changed" in r.json()["error"]
    assert _consents(app) == [] and sent == []
    assert not [x for x in human.get("/api/approvals").json() if x["id"] == old["approval_id"]]  # closed, not left waiting
    new = mcp_call(human, tok, "ingest_video_link", {"url": YT}, mid=2)[1]
    assert new["needs_user_approval"] is True and new["approval_id"] != old["approval_id"]
    aid = new["approval_id"]
    # a page that still shows the older statement is refused too, and the item stays pending
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True, "attestation_version": "2026-11-04.1"})
    assert r.status_code == 409 and _consents(app) == []
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True, "attestation_version": ["x"]})
    assert r.status_code == 400
    ok = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True, "attestation_version": "2099-01-01.1"}).json()
    assert ok["status"] == "approved"
    c = _consents(app)
    assert len(c) == 1 and c[0]["attestation_version"] == "2099-01-01.1"


def test_an_ai_apps_direct_file_link_reaches_the_person(human, app, monkeypatch):
    """A direct MP4 link from an AI app, with no owns or credit, asks the person (credited to its host), never errors."""
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    out = mcp_call(human, tok, "ingest_video_link", {"url": "https://cdn.example.com/talk.mp4"})[1]
    assert out.get("needs_user_approval") is True and out["fetched"] is False and sent == [] and _consents(app) == []


def test_a_failed_consent_write_leaves_the_item_pending(human, app, monkeypatch):
    """Approve, the consent record and its id on the item commit together: if the record fails nothing is approved."""
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = mcp_call(human, tok, "ingest_video_link", {"url": YT})[1]["approval_id"]

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(link_ingest, "record", boom)
    with pytest.raises(RuntimeError):
        human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True})
    st = app.state.store.q("SELECT status, payload FROM approvals WHERE id=?", (aid,))[0]
    assert st["status"] == "pending" and "consent_id" not in st["payload"]


def test_the_upload_store_makes_its_own_client_when_none_is_shared(app, monkeypatch):
    from studio import take_upload
    tu = app.state.house.take_uploads
    monkeypatch.setattr(tu, "http", None)
    monkeypatch.setattr(app.state.house, "http", None)
    monkeypatch.setattr(tu, "_cfg", lambda: {"endpoint": "https://bucket.example.com", "bucket": "b", "key": "k", "secret": "s", "region": "auto"})
    monkeypatch.setattr(take_upload, "presign", lambda *a, **k: "https://bucket.example.com/x")
    seen = []

    class FakeClient:
        def __init__(self, **kw):
            seen.append(kw)

        def request(self, *a, **k):
            return "ok"
    monkeypatch.setattr(take_upload.httpx, "Client", FakeClient)
    assert tu._s3("GET", "x") == "ok" and seen and seen[0]["trust_env"] is False


def test_an_approval_older_than_a_day_is_closed_not_approved(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    tok = agent_token(human, policy="trusted", cap=50)
    aid = mcp_call(human, tok, "ingest_video_link", {"url": YT})[1]["approval_id"]
    st = app.state.store
    st.x("UPDATE approvals SET created=? WHERE id=?", (1.0, aid))
    r = human.post(f"/api/approvals/{aid}/approve", json={"rights_attested": True})
    assert r.status_code == 410 and _consents(app) == []
    assert st.q("SELECT status FROM approvals WHERE id=?", (aid,))[0]["status"] == "rejected"


def test_account_deletion_keeps_an_unconfirmed_bucket_delete_for_the_sweep(app):
    from studio import take_upload
    st = app.state.store
    take_upload.ensure(st)
    for i, status in enumerate(("ready", "del_ready", "del_open")):
        st.x("INSERT INTO take_uploads(id,user_id,name,size,part_size,parts,status,object_key,s3_upload,take_id,created,updated,"
             "fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (f"u{i}", "gone-user", "my talk.mp4", 10, 10, 1, status, f"k{i}", "up" if status == "del_open" else "", None, 1.0, 5.0, "fp"))
    st.delete_user("gone-user")
    left = {r["id"]: dict(r) for r in st.q("SELECT * FROM take_uploads WHERE id IN ('u0','u1','u2')")}
    assert set(left) == {"u1", "u2"}                                         # the sweep still deletes these objects
    assert all(r["user_id"] == "" and r["name"] == "" and r["fingerprint"] is None for r in left.values())


def test_the_consent_schema_is_only_cached_once_committed(tmp_path):
    """A rollback can undo a table created inside a transaction: ensure() caches readiness only outside one."""
    from studio.store import Store
    st = Store(str(tmp_path / "s.db"), "x" * 32)
    with pytest.raises(RuntimeError):
        with st.tx():
            link_ingest.ensure(st)
            raise RuntimeError("rolled back")
    assert not getattr(st, "_link_consents_ready", False)
    link_ingest.ensure(st)
    assert st._link_consents_ready and st.q("SELECT COUNT(*) AS n FROM link_consents")[0]["n"] == 0
