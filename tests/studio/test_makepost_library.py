"""Make a post with the person's own upload, and the Library (owner bugs, 1 Nov 2026).

Pins: an upload never runs the old pipeline (every run is the agent: plan -> tools -> check, reply thread); the prompt
is never burned into the video as the headline and URLs never are; instructions ("search it", "post on all channels",
"make a reel") are not the topic; each channel gets its own media with a playable preview (9:16 for Instagram/YouTube,
4:5 for LinkedIn/Facebook); the Library keeps uploads and generated media in the database with caps, range requests,
delete-with-warning and use-in-a-post, over REST and MCP; the sample workspace sees sample items read-only.
"""
import base64
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from studio import agent_tools, house, intake, library, reader, sources

from . import conftest
from .conftest import FakeBuffer, connect
from .test_agent_core import OWNER_CHANNELS, TWEET, Router, jpeg

# the owner's live prompt shape: instructions + a link, with his own video attached, for all channels
OWNER_PROMPT = (f"you can do that online search it is {TWEET} research and have a instgram reel post on all channels")
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64 + b"moov" + bytes(range(256)) * 40  # sniffed as MP4 (bytes never decoded here)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    monkeypatch.setattr(conftest, "CHANNELS", OWNER_CHANNELS)
    fake = FakeBuffer()
    router = Router(fake)
    from studio.app import create_app
    from studio.config import load_settings
    s = load_settings(client_id="cid", client_secret="sec", public_url="https://hq.test", session_secret="s" * 64,
                      db_path=str(tmp_path / "a.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True)
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(router)))
    app.state.studio.media_loader = lambda url: jpeg()
    h = app.state.house
    h.sync, h.worker_mode = True, "local"
    monkeypatch.setattr(house.House, "local_video_ok", staticmethod(lambda: True))
    specs = []

    def fake_render(self, spec):  # the render itself is tested elsewhere; here: what was asked for, per channel
        specs.append(spec)
        w, hh = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}[spec.get("ratio", "9:16")]
        return MP4, {"type": "video", "width": w, "height": hh, "duration": 14.5, "params": spec.get("params") or {}, "has_logo": True,
                     "has_end_card": True, "headline": spec.get("hook", "")}
    monkeypatch.setattr(house.House, "render_job_local", fake_render)
    monkeypatch.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)
    monkeypatch.setattr(house.House, "_corroborate", lambda self, article: [])
    monkeypatch.setattr(reader, "read_article", lambda url, http=None: {"url": url, "title": "", "description": "", "paragraphs": []})
    searched = []
    monkeypatch.setattr(sources, "search", lambda *a, **k: searched.append(a) or [])
    c = TestClient(app, base_url="https://hq.test")
    c.headers["X-HQ-CSRF"] = connect(c)
    c.searched, c.fake, c.router, c.specs = searched, fake, router, specs
    return c


def upload(c, data=MP4, name="my clip.mp4"):
    r = c.post(f"/api/library/upload?name={name}", content=data, headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def uid_of(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


# ---------------------------------------------------------------- intake + headline guard (pure)
def test_instructions_are_not_the_topic():
    p = intake.parse(OWNER_PROMPT, [])
    assert p["topic"] == "" and p["source_url"] == TWEET and p["format"] == "reel" and p["research"]
    assert intake.topic_of("make a reel about Nvidia earnings beat, post on LinkedIn") == "Nvidia earnings beat"
    assert intake.topic_of("make a reel of this and post it everywhere") == ""


def test_headline_is_never_the_prompt_or_a_url():
    burned = "YOU CAN DO THAT ONLINE SEARCH IT IS HTTPS://X.COM/AARUSHYADAV47/STATUS/2116198463847408066 RESEARCH AND HAVE A INSTGRAM"
    assert agent_tools.safe_headline(burned, OWNER_PROMPT, ["Subvocal raises $1.3M for silent-speech glasses"]) == \
        "Subvocal raises $1.3M for silent-speech glasses"
    assert "http" not in agent_tools.safe_headline("Read this https://example.com/a now please", "", ["x"]).lower()
    assert agent_tools.safe_headline("Google brings online search to glasses") == "Google brings online search to glasses"


# ---------------------------------------------------------------- the owner's bug, end to end
def test_upload_with_prompt_runs_the_agent_not_the_old_pipeline(owner):
    up = upload(owner)
    assert up["kind"] == "video" and up["source"] == "upload" and up["name"] == "my clip.mp4"
    r = owner.post("/api/agent/runs", json={"prompt": OWNER_PROMPT, "media_token": up["media_token"], "channel_ids": ["ch_ig"]}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["kind"] == "agent" and run["status"] == "ready", run["steps"]
    tools = [s.get("tool") for s in run["steps"]]
    old = {"Pick the format", "Render on-brand media", "Ready to schedule"}
    assert not old & {s["label"] for s in run["steps"]}  # the old fixed pipeline's steps are gone
    assert tools[0] == "read_upload" and "read_url" in tools and tools[-1] == "check"
    assert owner.searched == []  # the link is the source: "search it" is an instruction, not a search
    res = run["result"]
    # 2) the headline comes from the story; the prompt and URLs never reach the video
    head = res["story"]["headline"]
    assert "http" not in head.lower() and "x.com" not in head.lower() and "search" not in head.lower() and "instgram" not in head.lower()
    assert any(w in head.upper() for w in ("SUBVOCAL", "1.3M", "RAISED")), head
    assert owner.specs and all(sp["hook"] == head and "http" not in sp["hook"].lower() for sp in owner.specs)
    assert all(sp["source"] == up["media_token"] for sp in owner.specs)  # their own video is the clip (an input tool)
    # 3) captions are about the story, not about "finding a ready-made Reel"
    for p in res["packages"]:
        t = p["text"].lower()
        assert "ready-made" not in t and "search online" not in t and "instgram" not in t, p["text"]
    # 4) every channel has its own media: a real video preview with its aspect and length
    want = {"instagram": "9:16", "youtube": "9:16", "linkedin": "4:5", "facebook": "4:5"}
    assert {p["service"] for p in res["packages"]} == set(want)  # "all channels" beats the one chip
    for p in res["packages"]:
        assert p["media"] and p["media"][0]["type"] == "video", p
        assert p["media"][0]["ratio"] == want[p["service"]] and p["media"][0]["duration"] == 14.5
    assert {sp["ratio"] for sp in owner.specs} == {"9:16", "4:5"}
    # the upload's own data came along: the read_upload step says what it found (no transcriber in tests)
    assert res["upload"]["read"] and res["upload"]["transcript_tried"]


def test_upload_without_a_link_uses_the_transcript_and_never_the_prompt(owner, monkeypatch):
    monkeypatch.setattr(house.House, "transcribe", lambda self, a, rid, tok: (
        "Today we are opening our second studio in Dublin. It has room for forty makers. Doors open on Monday.", "Transcribed (test)."))
    up = upload(owner)
    r = owner.post("/api/agent/runs", json={"prompt": "make a reel of this and post it on all my channels", "media_token": up["media_token"]}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["status"] == "ready", run["steps"]
    res = run["result"]
    assert res["source"]["via"] == "upload" and owner.searched == []
    head = res["story"]["headline"].lower()
    assert "make a reel" not in head and "channels" not in head and ("studio" in head or "dublin" in head), head


def test_upload_with_nothing_to_go_on_asks_instead_of_guessing(owner):
    up = upload(owner, jpeg(800, 1000), "photo.jpg")
    r = owner.post("/api/agent/runs", json={"prompt": "post this everywhere", "media_token": up["media_token"]}).json()
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    assert run["status"] == "failed" and "what your photo is about" in run["result"]["thread"][-1]["text"]


def test_replace_and_regenerate_one_channel(owner):
    up = upload(owner)
    r = owner.post("/api/agent/runs", json={"prompt": f"post this on all channels {TWEET}", "media_token": up["media_token"]}).json()
    rid = r["id"]
    pic = upload(owner, jpeg(1080, 1350), "cover.jpg")
    out = owner.post(f"/api/agent/runs/{rid}/packages/ch_li/media", json={"token": pic["media_token"]}).json()
    li = next(p for p in out["result"]["packages"] if p["service"] == "linkedin")
    assert li["media"][0]["type"] == "photo" and li["format"] == "photo"
    bad = owner.post(f"/api/agent/runs/{rid}/packages/ch_yt/media", json={"token": pic["media_token"]})
    assert bad.status_code == 400 and "needs a video" in bad.json()["error"]
    n = len(owner.specs)
    out = owner.post(f"/api/agent/runs/{rid}/packages/ch_yt/media", json={}).json()  # regenerate
    assert len(owner.specs) == n + 1 and out["status"] == "ready"
    # the same through MCP
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    res = owner.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": "replace_channel_media", "arguments": {"run_id": rid, "channel": "facebook", "media_token": pic["media_token"]}}}).json()
    assert not res["result"]["isError"], res


def test_one_reply_box_edits_any_channel(owner):
    r = owner.post("/api/agent/runs", json={"prompt": f"post this on all channels {TWEET}"}).json()
    n = len(owner.specs)
    owner.post(f"/api/agent/runs/{r['id']}/messages", json={"text": "redo YouTube at 30s"})
    run = owner.get(f"/api/agent/runs/{r['id']}").json()
    # the post has no video: NO VIDEO -> NO REEL (owner, 1 Nov 2026), so no 30 s slideshow is rendered; YouTube says why
    assert run["status"] == "ready" and not owner.specs[n:]
    assert run["result"]["channel_media"]["ch_yt"]["format"] == "skipped"


# ---------------------------------------------------------------- the Library
def test_library_upload_sniffs_caps_and_serves_ranges(owner, monkeypatch):
    monkeypatch.setattr(library, "CHUNK", 1000)  # several chunks, so ranges cross chunk boundaries
    it = upload(owner, MP4, "../../etc/pass wd.mov")
    assert it["mime"] == "video/mp4" and it["name"] == "pass wd.mp4" and it["size"] == len(MP4)  # sniffed type, safe name
    full = owner.get(it["url"])
    assert full.status_code == 200 and full.content == MP4
    part = owner.get(it["url"], headers={"Range": "bytes=990-2010"})
    assert part.status_code == 206 and part.content == MP4[990:2011] and part.headers["content-range"] == f"bytes 990-2010/{len(MP4)}"
    tail = owner.get(it["url"], headers={"Range": "bytes=-10"})
    assert tail.content == MP4[-10:]
    assert owner.get(it["url"], headers={"Range": f"bytes={len(MP4) + 5}-"}).status_code == 416
    # the same file again is not stored twice
    assert upload(owner, MP4, "again.mp4")["id"] == it["id"]
    # the public media link (Buffer and the render worker) streams the same bytes
    assert owner.get("/m/" + it["media_token"], headers={"Range": "bytes=0-9"}).content == MP4[:10]
    # not a picture or a video: refused, nothing kept
    r = owner.post("/api/library/upload?name=x.mp4", content=b"#!/bin/sh\necho hi\n" * 10)
    assert r.status_code == 415
    # caps checked before reading: per file, then the quota
    monkeypatch.setattr(library, "FILE_MAX", 100)
    assert owner.post("/api/library/upload?name=big.mp4", content=MP4).status_code == 413
    monkeypatch.setattr(library, "FILE_MAX", 10 * 1024 * 1024)
    monkeypatch.setattr(library, "QUOTA", len(MP4) + 50)
    assert owner.post("/api/library/upload?name=other.jpg", content=jpeg()).status_code == 413
    lst = owner.get("/api/library").json()
    assert [i["id"] for i in lst["items"]] == [it["id"]] and lst["used"] == len(MP4)
    # signed out: nothing
    anon = TestClient(owner.app, base_url="https://hq.test")
    assert anon.get(it["url"]).status_code == 401 and anon.get("/api/library").status_code == 401


def test_library_lists_uploads_and_generated_media_with_posts(owner):
    up = upload(owner)
    r = owner.post("/api/agent/runs", json={"prompt": f"post this on all channels {TWEET}", "media_token": up["media_token"]}).json()
    lst = owner.get("/api/library").json()["items"]
    mine = next(i for i in lst if i["id"] == up["id"])
    assert mine["used_in"] and mine["used_in"][0]["run_id"] == r["id"]
    gen = [i for i in lst if i["source"] == "generated"]
    assert gen and {i["kind"] for i in gen} == {"video"} and all(i["used_in"] for i in gen)
    assert owner.get("/api/library?kind=upload").json()["items"][0]["id"] == up["id"]
    assert owner.get("/api/library?q=nothing-matches").json()["items"] == []
    # /api/media (Make a post, One take) saves into the Library too
    b64 = base64.b64encode(jpeg(600, 600)).decode()
    m = owner.post("/api/media", json={"name": "take.jpg", "data": b64, "source": "take"}).json()
    item = owner.get(f"/api/library/{m['library_id']}").json()
    assert item["source"] == "take" and item["kind"] == "image"


def test_library_delete_warns_when_a_post_uses_it_and_use_in_a_post(owner):
    up = upload(owner)
    owner.post("/api/agent/runs", json={"prompt": f"post this on all channels {TWEET}", "media_token": up["media_token"]})
    warn = owner.delete(f"/api/library/{up['id']}")
    assert warn.status_code == 409 and "hasn't gone out" in warn.json()["error"]
    pic = upload(owner, jpeg(900, 900), "free.jpg")
    use = owner.post(f"/api/library/{pic['id']}/use", json={}).json()
    assert use["media_token"] == pic["media_token"] and use["type"] == "photo"
    gone = owner.delete(f"/api/library/{up['id']}?force=1").json()
    assert gone["deleted"] and owner.get(up["url"]).status_code == 404
    assert owner.get("/m/" + up["media_token"]).status_code == 404
    assert owner.app.state.store.q("SELECT COUNT(*) n FROM library_chunks WHERE item_id=?", (up["id"],))[0]["n"] == 0


def test_library_mcp_tools(owner):
    tok = owner.post("/api/agent-tokens", json={"name": "Claude", "policy": "review"}).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}

    def call(name, args=None):
        out = owner.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}).json()
        return out["result"]

    names = {t["name"] for t in owner.post("/mcp", headers=h, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"list_library", "delete_library_item", "use_library_item", "replace_channel_media"} <= names
    pic = upload(owner, jpeg(900, 900), "a.jpg")
    lst = call("list_library")
    assert not lst["isError"] and lst["structuredContent"]["items"][0]["id"] == pic["id"]
    run = call("use_library_item", {"item_id": pic["id"], "prompt": f"post this on LinkedIn {TWEET}"})
    assert not run["isError"], run["content"][0]["text"]
    assert run["structuredContent"]["run"]["kind"] == "agent"
    owner.post("/api/safety/kill", json={"on": True})
    assert call("delete_library_item", {"item_id": pic["id"], "force": True})["isError"]  # the kill switch stops agent writes
    owner.post("/api/safety/kill", json={"on": False})
    assert not call("delete_library_item", {"item_id": pic["id"], "force": True})["isError"]
    assert pic["id"] not in [i["id"] for i in call("list_library")["structuredContent"]["items"]]


def test_sample_workspace_library_is_read_only(tmp_path):
    from studio.app import create_app
    from studio.config import load_settings
    s = load_settings(db_path=str(tmp_path / "d.db"), public_url="https://hs.test", secure_cookies=False)
    c = TestClient(create_app(s))
    c.get("/demo", follow_redirects=False)
    c.headers["x-hq-csrf"] = c.get("/api/session").json()["csrf"]
    lst = c.get("/api/library").json()
    assert lst["read_only"] and lst["items"] and all(i["read_only"] for i in lst["items"])
    assert {"video", "carousel"} <= {i["kind"] for i in lst["items"]}
    assert c.get(lst["items"][0]["url"]).status_code == 200  # the sample files are served
    # the 96 MiB source the clips were cut from is no longer hosted: the Library shows the 5 credited clips, never a remote file
    assert not any(i["id"] == "sample-source" for i in lst["items"])
    assert not any("huggingface.co" in (i.get("url") or "") for i in lst["items"])
    assert len([i for i in lst["items"] if i["id"].startswith("sample-") and i["kind"] == "video"]) == 5
    assert c.post("/api/library/upload?name=a.mp4", content=MP4).status_code == 403
    assert c.delete("/api/library/1").status_code == 403
    # the view-only demo takes no uploads at all (Make a post runs on the sample media)
    up = c.post("/api/media", json={"name": "me.jpg", "data": base64.b64encode(jpeg(600, 800)).decode()})
    assert up.status_code == 403 and up.json()["view_only"] and "upload your file" in up.json()["error"]


def test_transcribe_job_round_trip(owner, monkeypatch):
    """The worker's transcribe job: dispatched, posted back once, read by the agent."""
    h = owner.app.state.house
    h.worker_mode = "github"
    monkeypatch.setattr(house.House, "video_mode", lambda self: "github")

    def dispatch(self, jid, workflow="render.yml", inputs=None, ttl=1800):
        t = self.issue_job_token(jid)
        spec = owner.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": t}).json()
        assert spec["spec"]["kind"] == "transcribe" and spec["urls"]["source"].endswith(spec["spec"]["source"])
        self.finish_video(jid, None, None, body={"transcript": {"text": "We shipped the new editor today.", "language": "en"}})
        return True, "204"
    monkeypatch.setattr(house.House, "_dispatch_github", dispatch)
    up = upload(owner)
    text, note = h.transcribe(house.Actor(uid_of(owner)), 0, up["media_token"])
    assert text == "We shipped the new editor today." and "worker" in note
    row = owner.app.state.store.q("SELECT status, result FROM render_jobs ORDER BY id DESC LIMIT 1")[0]
    assert row["status"] == "done" and json.loads(row["result"])["language"] == "en"
