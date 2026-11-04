"""Owner bugs, 2 Nov 2026: Buffer couldn't read our media ("Video/Image could not be read from its URL"), the Planner
showed nothing (a client-made package without channel_name made /api/calendar a 500), and MCP clients couldn't upload a
local file (only public links, and a sign-in page was misreported as "Upload a JPG, PNG…")."""
import base64
import json
import threading
import time

import httpx

from studio import mediareach
from studio.actions import Actor

from .conftest import agent_token, image_bytes, mcp_call

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41" + b"\x00" * 4000
HTML = b"<!DOCTYPE html><html><head><title>Sign in</title></head><body>Sign in to continue</body></html>"


def _uid(c):
    return c.app.state.store.q("SELECT user_id FROM sessions WHERE user_id IS NOT NULL")[0]["user_id"]


def _token(c, data: bytes, name: str) -> str:
    return c.app.state.house.put_media(Actor(_uid(c)), data, name, kind="render", meta={"type": "video" if name.endswith(".mp4") else "photo"})


# ---------------------------------------------------------------------------------------------------- 1. media Buffer can read
def test_media_answers_head_with_type_and_length_and_serves_ranges(human):
    tok = _token(human, MP4, "reel.mp4")
    h = human.head(f"/m/{tok}")  # Buffer's fetcher asks with HEAD first; this was a 405
    assert h.status_code == 200, h.text
    assert h.headers["content-type"] == "video/mp4" and int(h.headers["content-length"]) == len(MP4) and h.content == b""
    assert h.headers["accept-ranges"] == "bytes"
    r = human.get(f"/m/{tok}", headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206 and r.headers["content-range"] == f"bytes 0-1023/{len(MP4)}" and r.content == MP4[:1024]
    assert human.get(f"/m/{tok}", headers={"Range": "bytes=99999-"}).status_code == 416
    assert human.head("/m/nope.mp4").status_code == 404
    img = _token(human, image_bytes(64, 64, "JPEG"), "s.jpg")
    assert human.head(f"/m/{img}").headers["content-type"] == "image/jpeg"


def test_library_upload_token_answers_head(human):
    r = human.post("/api/library/upload?name=clip.mp4", content=MP4)
    assert r.status_code == 200, r.text
    tok = r.json()["media_token"]
    h = human.head(f"/m/{tok}")
    assert h.status_code == 200 and int(h.headers["content-length"]) == len(MP4) and h.headers["content-type"] == "video/mp4"


def _transport(routes):
    def handler(req: httpx.Request):
        return routes(req)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reach_check_reads_links_like_buffer(monkeypatch):
    monkeypatch.setattr("studio.drafter._public", lambda h: True)  # hermetic: no DNS for the .example hosts
    mediareach.reset()

    def good(req):
        if req.method == "HEAD":
            return httpx.Response(200, headers={"content-type": "video/mp4", "content-length": str(len(MP4))})
        assert req.headers["range"] == "bytes=0-1023"
        return httpx.Response(206, content=MP4[:1024], headers={"content-type": "video/mp4", "content-range": f"bytes 0-1023/{len(MP4)}"})
    assert mediareach.check_one("https://a.example/v.mp4", _transport(good)) is None

    no_head = _transport(lambda req: httpx.Response(405 if req.method == "HEAD" else 200, content=MP4))
    msg = mediareach.check_one("https://b.example/v.mp4", no_head)
    assert "HEAD" in msg and "405" in msg

    page = _transport(lambda req: httpx.Response(200, content=HTML, headers={"content-type": "text/html; charset=utf-8"}))
    assert "web page" in mediareach.check_one("https://c.example/v.mp4", page)

    signin = _transport(lambda req: httpx.Response(302, headers={"location": "https://login.example/signin"}))
    assert "redirects" in mediareach.check_one("https://d.example/i.jpg", signin)

    def no_range(req):
        return httpx.Response(200, content=MP4, headers={"content-type": "video/mp4", "content-length": str(len(MP4))})
    assert "byte ranges" in mediareach.check_one("https://e.example/v.mp4", _transport(no_range))
    assert "https" in mediareach.check_one("http://f.example/v.mp4", _transport(good))


def test_send_stops_with_a_clear_message_before_buffer_when_a_link_cant_be_read(human, fake, monkeypatch):
    monkeypatch.setattr("studio.drafter._public", lambda h: True)  # hermetic: no DNS for cdn.example.com
    st = human.app.state.studio
    st.media_reach = lambda urls: [mediareach.check_one(u, _transport(lambda req: httpx.Response(405))) for u in urls]
    mediareach.reset()
    p = human.post("/api/posts/preview", json={"channel_id": "ch_li", "text": "hello", "media_url": "https://cdn.example.com/a.png",
                                             "mode": "draft"}).json()
    r = human.post("/api/posts/submit", json={"confirm_token": p["confirm_token"]})
    assert r.status_code == 422 and "refuses HEAD" in r.json()["error"]
    assert not fake.created  # Buffer never saw it
    row = [x for x in human.get("/api/audit").json() if x["action"] == "submit_post"][0]
    assert row["ok"] in (0, False) and row["detail"]["stage"] == "media check"
    st.media_reach = lambda urls: []
    p = human.post("/api/posts/preview", json={"channel_id": "ch_li", "text": "hello", "media_url": "https://cdn.example.com/a.png",
                                             "mode": "draft"}).json()
    assert human.post("/api/posts/submit", json={"confirm_token": p["confirm_token"]}).status_code == 200 and fake.created


def test_buffer_could_not_read_says_what_to_do(human, fake):
    fake.reject_message = "Invalid post: Video could not be read from its URL."
    human.app.state.studio.media_reach = lambda urls: []
    p = human.post("/api/posts/preview", json={"channel_id": "ch_li", "text": "hello", "media_url": "https://cdn.example.com/a.png",
                                             "mode": "draft"}).json()
    r = human.post("/api/posts/submit", json={"confirm_token": p["confirm_token"]})
    assert r.status_code >= 400 and "could not be read" in r.text and "try again" in r.text


def test_mcp_tools_run_off_the_event_loop(human, monkeypatch):
    """A tool that waits on Buffer must not stop the server: Buffer fetches our /m/ media while it decides."""
    from studio import mcp
    seen = {}
    real = mcp.handle

    def spy(studio, a, msg):
        seen["thread"] = threading.current_thread().name
        return real(studio, a, msg)
    monkeypatch.setattr(mcp, "handle", spy)
    tok = agent_token(human, "draft")
    mcp_call(human, tok, "whoami")
    assert "AnyIO worker" in seen["thread"] or seen["thread"] != threading.main_thread().name


# ---------------------------------------------------------------------------------------------------- 2. Planner
def test_planner_survives_a_client_package_without_channel_name(human):
    st = human.app.state.store
    a, b = (_token(human, image_bytes(64, 64, "JPEG"), n) for n in ("a.jpg", "b.jpg"))  # real files: a missing one fails the check
    pk = {"channel_id": "ch_ig", "service": "instagram", "channel": "huhbyte.ai", "text": "Screen Studio 4.0", "status": "ready",
          "media_urls": [f"https://hq.test/m/{a}", f"https://hq.test/m/{b}"], "format": "carousel", "check": {"pass": True}}
    bad = {"channel_id": "ch_li", "status": "ready"}  # an odd old package: no text, no service, no check
    st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
         (_uid(human), "client", json.dumps({"prompt": "x"}), "ready", "[]", json.dumps({"packages": [pk, bad, "junk"]}), time.time(), time.time()))
    r = human.get("/api/calendar")
    assert r.status_code == 200, r.text
    ready = [i for i in r.json()["items"] if i["kind"] == "ready"]
    assert {i["channel_id"] for i in ready} == {"ch_ig", "ch_li"}
    ig = next(i for i in ready if i["channel_id"] == "ch_ig")
    assert ig["channel"] == "huhbyte.ai" and ig["type"] == "carousel" and ig["pass"] is True
    li = next(i for i in ready if i["channel_id"] == "ch_li")
    assert li["service"] == "linkedin" and li["channel"]  # filled from the channel list


def test_client_package_carries_channel_name_and_the_planner_lists_it(human):
    tok = agent_token(human, "draft")
    res, out = mcp_call(human, tok, "create_package", {"captions": {"instagram": "Hello"}, "media_urls": ["https://cdn.example.com/a.png"]})
    assert not res["isError"], out
    run = json.loads(human.app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (out["run_id"],))[0]["result"])
    assert run["packages"][0]["channel_name"] == "huhbyte.ai"
    assert human.get("/api/calendar").status_code == 200


# ---------------------------------------------------------------------------------------------------- 3. uploads for MCP clients
def test_create_upload_put_lands_in_the_library_once(human):
    tok = agent_token(human, "draft")
    res, out = mcp_call(human, tok, "create_upload", {"name": "launch.mp4", "size": len(MP4)})
    assert not res["isError"], out
    url = out["upload_url"]
    assert url.startswith("https://hq.test/u/ut_") and out["expires_in"] == 900 and "--data-binary" in out["curl"] and url in out["curl"]
    path = url.split("hq.test", 1)[1]
    r = human.put(path, content=MP4)
    assert r.status_code == 200, r.text
    item = r.json()
    assert item["kind"] == "video" and item["bytes"] == len(MP4) and item["media_token"].endswith(".mp4")
    assert item["url"] == f"https://hq.test/m/{item['media_token']}"
    assert human.put(path, content=MP4).status_code == 410  # one use
    lib = human.get("/api/library").json()["items"]
    assert any(i["id"] == item["library_id"] for i in lib)


def test_create_upload_refuses_a_web_page_and_expires(human):
    tok = agent_token(human, "draft")
    _, out = mcp_call(human, tok, "create_upload", {"name": "x.mp4"})
    r = human.put(out["upload_url"].split("hq.test", 1)[1], content=HTML)
    assert r.status_code == 415 and "web page" in r.json()["error"]
    _, out = mcp_call(human, tok, "create_upload", {"name": "x.mp4"})
    human.app.state.store.x("UPDATE upload_sessions SET expires=? WHERE id=?", (time.time() - 1, out["upload_id"]))
    assert human.put(out["upload_url"].split("hq.test", 1)[1], content=MP4).status_code == 410
    _, msg = mcp_call(human, tok, "create_upload", {"name": "big.mp4", "size": 500 * 1024 * 1024})
    assert "MB" in msg


def test_chunked_upload_joins_into_one_library_item(human):
    tok = agent_token(human, "draft")
    data = MP4 + bytes(range(256)) * 12000  # ~3 MB: two chunks
    parts = [data[:2 * 1024 * 1024], data[2 * 1024 * 1024:]]
    res, a = mcp_call(human, tok, "upload_media_chunk", {"index": 0, "data": base64.b64encode(parts[0]).decode(), "name": "talk.mp4"})
    assert not res["isError"], a
    uid = a["upload_id"]
    _, again = mcp_call(human, tok, "upload_media_chunk", {"upload_id": uid, "index": 3, "data": base64.b64encode(b"x").decode()})
    assert "next one is index 1" in again
    _, b = mcp_call(human, tok, "upload_media_chunk", {"upload_id": uid, "index": 1, "data": base64.b64encode(parts[1]).decode()})
    assert b["bytes_so_far"] == len(data) and b["next_index"] == 2
    res, done = mcp_call(human, tok, "finish_upload", {"upload_id": uid})
    assert not res["isError"], done
    assert done["kind"] == "video" and done["bytes"] == len(data)
    assert human.get(f"/m/{done['media_token']}").content == data
    assert human.app.state.store.q("SELECT COUNT(*) AS n FROM upload_parts WHERE upload_id=?", (uid,))[0]["n"] == 0
    _, gone = mcp_call(human, tok, "finish_upload", {"upload_id": uid})
    assert "already used" in gone


def test_chunk_size_and_base64_are_checked(human):
    tok = agent_token(human, "draft")
    _, msg = mcp_call(human, tok, "upload_media_chunk", {"index": 0, "data": "not base64!!"})
    assert "base64" in msg
    _, msg = mcp_call(human, tok, "upload_media_chunk", {"index": 0, "data": base64.b64encode(b"\0" * (2 * 1024 * 1024 + 1)).decode()})
    assert "2 MB" in msg


def test_tool_descriptions_point_local_files_at_create_upload(human):
    tok = agent_token(human, "draft")
    tools = {t["name"]: t for t in human.post("/mcp", headers={"Authorization": f"Bearer {tok}"},
                                              json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]}
    assert {"create_upload", "upload_media_chunk", "finish_upload"} <= set(tools)
    assert "USE THIS FIRST" in tools["create_upload"]["description"]
    assert "create_upload" in tools["library_upload_url"]["description"]
    assert tools["edit_video"]["inputSchema"]["properties"]["rights"]["enum"] == ["own", "permission", "news_credit"]


def test_import_of_a_sign_in_page_says_so(human, monkeypatch):
    house = human.app.state.house
    monkeypatch.setattr(house, "_fetch_bytes", lambda url, cap: HTML)
    tok = agent_token(human, "draft")
    _, msg = mcp_call(human, tok, "library_upload_url", {"url": "https://claude.ai/artifacts/abc/launch.mp4"})
    assert msg.startswith("That link returned a web page (probably a sign-in page), not a video.") and "create_upload" in msg


# ---------------------------------------------------------------------------------------------------- rights in one parameter
def test_rights_news_credit_goes_in_every_caption_once(human):
    tok = agent_token(human, "draft")
    res, out = mcp_call(human, tok, "create_package", {"captions": {"instagram": "Big launch", "linkedin": "Big launch. Video: OpenAI"},
                                                       "media_urls": ["https://cdn.example.com/a.png"], "rights": "news_credit",
                                                       "credit": "Video: OpenAI"})
    assert not res["isError"], out
    pk = json.loads(human.app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (out["run_id"],))[0]["result"])["packages"]
    texts = {p["service"]: p["text"] for p in pk}
    assert texts["instagram"].endswith("\n\nVideo: OpenAI") and texts["linkedin"].count("Video: OpenAI") == 1
    _, msg = mcp_call(human, tok, "create_package", {"captions": {"instagram": "x"}, "rights": "news_credit"})
    assert "credit" in msg
    _, msg = mcp_call(human, tok, "create_package", {"captions": {"instagram": "x"}, "rights": "maybe"})
    assert "own | permission | news_credit" in msg


def test_rights_map_to_the_render_spec():
    from studio.mcp_client_brain import _rights, credit_line
    assert _rights({"rights": "own"})["owns"] is True
    assert _rights({"rights": "permission"}) == {"rights": "permission", "owns": False, "rights_confirmed": True, "credit": ""}
    nc = _rights({"rights": "news_credit"}, "https://www.youtube.com/watch?v=x")
    assert nc["credit"] == "Video: youtube.com" and nc["rights_confirmed"] is True
    assert _rights({})["rights_confirmed"] is True  # the older default (the user's own upload)
    assert credit_line("OpenAI") == "Credit: OpenAI" and credit_line("Video: OpenAI") == "Video: OpenAI"
