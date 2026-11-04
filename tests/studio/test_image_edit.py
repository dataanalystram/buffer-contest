"""Phase 2 image editing (docs/plans/AGENT_RUNS_EDITING_COMMERCE.md 2.3-2.8): fetch_image through netguard, the edit
tools over MCP and REST (run media, never extra Library items), the image_matte worker job, its models and licences."""
import base64
import io
import json
import socket
import time
from pathlib import Path

import httpx
import pytest
from PIL import Image, ImageDraw

from studio import image_edit, image_matte, image_ops, instabox_worker, netguard
from studio import house as house_mod

from .conftest import agent_token, mcp_call
from .test_hero_cutout import headphones

ROOT = Path(__file__).resolve().parents[2]


def _png(im) -> bytes:
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def _jpeg_with_gps() -> bytes:
    im = Image.new("RGB", (640, 480), (200, 60, 40))
    exif = Image.Exif()
    exif[0x010F] = "PhoneMaker"                   # Make
    exif[0x8825] = {1: "N", 2: (53.0, 20.0, 0.0)}  # GPSInfo
    b = io.BytesIO()
    im.save(b, "JPEG", exif=exif)
    return b.getvalue()


class Web(httpx.BaseTransport):
    """The internet as the server sees it: answers by Host header (netguard pins the IP and keeps the Host)."""

    def __init__(self, pages):
        self.pages, self.seen = pages, []

    def handle_request(self, request):
        host = request.headers.get("host")
        self.seen.append((host, request.url.path))
        status, headers, body = self.pages.get((host, request.url.path), (404, {}, b"no"))
        return httpx.Response(status, headers=headers, content=body, request=request)


@pytest.fixture
def web(human, monkeypatch):
    table = {"img.test": "93.184.216.34", "shop.test": "93.184.216.35", "evil.test": "127.0.0.1", "meta.test": "169.254.169.254"}

    def fake_dns(host, *a, **k):
        ip = table.get(host)
        if ip is None:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]
    monkeypatch.setattr(netguard, "_getaddrinfo", fake_dns)
    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    w = Web({
        ("img.test", "/hp.png"): (200, {"content-type": "image/png"}, _png(headphones())),
        ("img.test", "/gps.jpg"): (200, {"content-type": "image/jpeg"}, _jpeg_with_gps()),
        ("img.test", "/page.html"): (200, {"content-type": "text/html"}, b"<html><body>sign in</body></html>"),
        ("img.test", "/bomb.png"): (200, {"content-type": "image/png"}, _png(Image.new("1", (12000, 12000)))),
        ("img.test", "/to-private"): (302, {"location": "https://evil.test/x.png"}, b""),
        ("img.test", "/to-meta"): (302, {"location": "https://meta.test/latest/meta-data"}, b""),
        ("img.test", "/to-http"): (302, {"location": "http://img.test/hp.png"}, b""),
        ("shop.test", "/p.jpg"): (403, {}, b"Forbidden"),
    })
    monkeypatch.setattr(human.app.state.house, "http", httpx.Client(transport=w, follow_redirects=False))
    return w


def _counts(store):
    return (store.q("SELECT COUNT(*) n FROM library_items")[0]["n"], store.q("SELECT COUNT(*) n FROM media")[0]["n"])


def _run(store, uid=None):
    uid = uid or store.q("SELECT id FROM users")[0]["id"]
    return store.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                   (uid, "client", "{}", "draft", "[]", "{}", time.time(), time.time()))


# ------------------------------------------------------------------------------------------------ fetch_image
def test_fetch_image_stores_run_media_not_a_library_item(human, web):
    tok = agent_token(human, cap=50)
    st = human.app.state.store
    rid = _run(st)
    lib0, _ = _counts(st)
    res, out = mcp_call(human, tok, "fetch_image", {"url": "https://img.test/hp.png", "run_id": rid})
    assert not res["isError"] and out["ok"] and out["width"] == 1000 and out["run_id"] == rid, out
    assert out["preview"].startswith("data:image/jpeg;base64,")
    meta = json.loads(st.q("SELECT meta FROM media WHERE token=?", (out["media_token"],))[0]["meta"])
    assert meta["kind"] == "source" and meta["run_id"] == rid and meta["source_host"] == "img.test"
    assert _counts(st)[0] == lib0  # no Library item


def test_fetch_image_strips_exif_and_gps(human, web):
    tok = agent_token(human, cap=50)
    _, out = mcp_call(human, tok, "fetch_image", {"url": "https://img.test/gps.jpg"})
    data = human.app.state.house.get_media(out["media_token"])["data"]
    assert b"PhoneMaker" not in data and b"Exif\x00\x00" not in data
    assert not Image.open(io.BytesIO(data)).getexif().get(0x8825)


@pytest.mark.parametrize("url,reason", [
    ("https://shop.test/p.jpg", "blocked_by_site"),       # 403: the Galaxus case
    ("https://img.test/missing.png", "not_found"),
    ("https://img.test/page.html", "not_an_image"),
    ("https://img.test/bomb.png", "too_large"),           # 144 MP in a few KB: the pixel cap refuses it
    ("https://evil.test/x.png", "private"),
    ("https://img.test/to-private", "private"),          # a redirect to a private address
    ("https://img.test/to-meta", "private"),             # a redirect to the cloud metadata address
    ("http://img.test/hp.png", "bad_url"),               # https only
    ("https://img.test/to-http", "bad_url"),             # https on every redirect hop, not only the first URL
    ("file:///etc/passwd", "bad_url"),
])
def test_fetch_image_failures_say_what_to_do(human, web, url, reason):
    tok = agent_token(human, cap=50)
    res, out = mcp_call(human, tok, "fetch_image", {"url": url})
    assert not res["isError"] and out["ok"] is False and out["reason"] == reason, out
    if reason == "blocked_by_site":
        assert "upload" in out["ask"] and "create_send_code" in out["ask"] and out["next"]
    assert not any(h == "evil.test" for h, _ in web.seen)  # nothing ever connected to the private host


def test_fetch_image_rest_answers_403_with_the_ask(human, web):
    r = human.post("/api/images/fetch", json={"url": "https://shop.test/p.jpg"})
    assert r.status_code == 403 and r.json()["reason"] == "blocked_by_site" and "upload" in r.json()["ask"]


def test_another_accounts_run_is_not_found(human, web):
    st = human.app.state.store
    other = _run(st, uid="someone_else")
    tok = agent_token(human, cap=50)
    res, out = mcp_call(human, tok, "fetch_image", {"url": "https://img.test/hp.png", "run_id": other})
    assert res["isError"] and "No such post run" in out


def test_image_tools_follow_run_discipline_thread_url_and_retry_replay(human, web):
    """Phase 1 x Phase 2: the image tools are run media (thread_url, run ownership) and a retry is not a second edit."""
    from studio import run_discipline
    names = {"fetch_image", "cutout_product", "edit_image", "compose_product_card"}
    assert names <= run_discipline.RUN_MEDIA and names <= run_discipline.DERIVED
    assert names <= run_discipline.THREAD_LINKED and not names & run_discipline.NO_IDEM
    tok = agent_token(human, cap=50)
    st = human.app.state.store
    rid = _run(st)
    _, got = mcp_call(human, tok, "fetch_image", {"url": "https://img.test/hp.png", "run_id": rid})
    assert got["ok"] and got["run_id"] == rid and got["thread_url"].endswith(f"/?tab=library&run={rid}"), got
    args = {"image": got["media_token"], "engine": "fast", "run_id": rid}
    _, cut = mcp_call(human, tok, "cutout_product", args)
    assert cut["ok"] and cut["thread_url"].endswith(f"run={rid}") and not cut.get("idempotent_replay"), cut
    media0 = _counts(st)[1]
    _, again = mcp_call(human, tok, "cutout_product", args)  # the app retried the same call
    assert again["idempotent_replay"] is True and again["media_token"] == cut["media_token"]
    assert _counts(st)[1] == media0  # no second stored cut-out
    _, card = mcp_call(human, tok, "compose_product_card", {"image": cut["media_token"], "ratio": "1:1", "run_id": rid})
    assert card["ok"] and card["run_id"] == rid and card["thread_url"].endswith(f"run={rid}"), card
    _, ed = mcp_call(human, tok, "edit_image", {"image": card["media_token"], "run_id": rid, "ops": [{"op": "round", "radius": 24}]})
    assert ed["ok"] and ed["thread_url"].endswith(f"run={rid}"), ed
    res, out = mcp_call(human, tok, "edit_image", {"image": card["media_token"], "run_id": True, "ops": [{"op": "round"}]})
    assert res["isError"] and "run_id is the number" in out  # a bool is never run 1


def _chunk(typ: bytes, data: bytes) -> bytes:
    import struct
    import zlib
    return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)


def test_png_metadata_after_the_image_data_is_dropped_too():
    """Greptile P1 (4 Nov 2026): tEXt / iTXt (XMP GPS) / eXIf chunks after IDAT used to pass through png_head."""
    from studio import metastrip
    raw = _png(Image.new("RGB", (16, 16), (10, 120, 200)))
    ex = Image.Exif()
    ex[0x010F] = "GPSDATA-Maker"
    ex[0x8825] = {1: "N", 2: (53.0, 20.0, 59.0)}
    iend = raw.rindex(b"IEND") - 4
    xmp = b"XML:com.adobe.xmp\x00\x00\x00\x00\x00<x:xmpmeta><exif:GPSLatitude>53,20.9N</exif:GPSLatitude></x:xmpmeta>"
    late = _chunk(b"iTXt", xmp) + _chunk(b"tEXt", b"GPS\x0053.3498,-6.2603") + _chunk(b"eXIf", ex.tobytes()[6:])
    dirty = raw[:iend] + late + raw[iend:] + b"trailing-53.3498"
    Image.open(io.BytesIO(dirty)).load()  # still a valid PNG
    out = metastrip.strip_bytes("png", dirty)
    for leak in (b"53.3498", b"GPSLatitude", b"GPSDATA", b"iTXt", b"tEXt", b"eXIf"):
        assert leak not in out, leak
    assert out.endswith(b"IEND\xaeB`\x82")
    assert Image.open(io.BytesIO(out)).size == (16, 16)
    im, ext = image_edit.open_image(dirty)
    stored, ext2 = image_edit.strip_meta(dirty, ext, im)  # the fetch_image path
    assert ext2 == "png" and b"53.3498" not in stored and b"GPSLatitude" not in stored
    assert metastrip.strip_bytes("png", raw[:-20]) == metastrip.png_head(raw[:-20])  # truncated: the header-only clean


# ------------------------------------------------------------------------------------------------ cut-out + edits
def test_cutout_fast_keeps_the_earcups_and_adds_no_library_item(human, web):
    tok = agent_token(human, cap=50)
    st = human.app.state.store
    rid = _run(st)
    lib0, _ = _counts(st)
    _, out = mcp_call(human, tok, "cutout_product", {"image": "https://img.test/hp.png", "engine": "fast", "run_id": rid})
    assert out["ok"] and out["engine"] == "fast" and out["transparent"] and out["confidence"] >= 0.9, out
    im = Image.open(io.BytesIO(human.app.state.house.get_media(out["media_token"])["data"]))
    assert im.mode == "RGBA" and im.width > 700 and im.height > 800
    meta = json.loads(st.q("SELECT meta FROM media WHERE token=?", (out["media_token"],))[0]["meta"])
    assert meta["kind"] == "edit" and meta["run_id"] == rid and meta["prev"]
    assert _counts(st)[0] == lib0


def test_cutout_never_returns_a_torn_cut(human, web, monkeypatch):
    import random
    random.seed(5)
    busy = Image.effect_noise((500, 400), 90).convert("RGB")
    up = human.post("/api/media", json={"name": "busy.png", "data": base64.b64encode(_png(busy)).decode()}).json()
    tok = agent_token(human, cap=50)
    _, out = mcp_call(human, tok, "cutout_product", {"image": up["token"], "engine": "fast"})
    assert out["ok"] is False and "media_token" not in out and "torn" in out["message"]


def test_edit_image_crop_fit_card_over_mcp_and_rest(human, web):
    tok = agent_token(human, cap=50)
    st = human.app.state.store
    rid = _run(st)
    lib0, media0 = _counts(st)
    _, out = mcp_call(human, tok, "edit_image", {"image": "https://img.test/hp.png", "run_id": rid, "ops": [
        {"op": "crop", "box": [0.05, 0.0, 0.95, 1.0]}, {"op": "card", "ratio": "4:5", "name": "Buds Pro", "price": "$79"}]})
    assert out["ok"] and (out["width"], out["height"]) == (1080, 1350), out
    assert out["report"][0].startswith("cropped") and "card" in out["report"][1]
    r = human.post("/api/images/edit", json={"image": out["media_token"], "run_id": rid, "ops": [{"op": "fit", "ratio": "1:1"}]})
    assert r.status_code == 200 and (r.json()["width"], r.json()["height"]) == (1080, 1080) and r.json()["prev"] == out["media_token"]
    lib1, media1 = _counts(st)
    assert lib1 == lib0 and media1 == media0 + 3  # source + 2 versions, all run media


def test_compose_product_card_places_the_whole_product(human, web):
    tok = agent_token(human, cap=50)
    _, out = mcp_call(human, tok, "compose_product_card", {"image": "https://img.test/hp.png", "ratio": "1:1", "ground": "#101012"})
    assert out["ok"] and (out["width"], out["height"]) == (1080, 1080)
    im = Image.open(io.BytesIO(human.app.state.house.get_media(out["media_token"])["data"])).convert("RGB")
    assert im.getpixel((5, 5)) == (16, 16, 18)                    # the brand ground
    from PIL import ImageChops
    box = ImageChops.difference(im, Image.new("RGB", im.size, (16, 16, 18))).convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()
    assert box and box[2] - box[0] > 600 and box[3] - box[1] > 600  # the whole product, centred, never zoomed in
    assert abs((box[0] + box[2]) / 2 - 540) < 40 and box[0] > 60 and box[2] < 1020


@pytest.mark.parametrize("ops,msg", [
    ([], "list of edits"),
    ([{"op": "blur"}], "unknown op"),
    ([{"op": "sharpen"}] * 13, "Up to 12 ops"),
    ([{"op": "fit", "ratio": "7:3"}], "fit.ratio"),
    ([{"op": "crop", "box": [10, 10, 5, 50]}], "x1 > x0"),
    ([{"op": "grade", "exposure": 3}], "between -1 and 1"),
    ([{"op": "card", "scale": 2}], "card.scale"),
    ([{"op": "pad", "ratio": "1:1", "colour": "red"}], None),
    ([{"op": "round", "radius": True}], "must be a number"),
])
def test_edit_ops_are_validated(ops, msg):
    if msg is None:  # validate passes, apply refuses the colour
        image_ops.validate(ops)
        with pytest.raises(image_ops.OpError, match="colour"):
            image_ops.apply(Image.new("RGB", (100, 100)), ops)
        return
    with pytest.raises(image_ops.OpError, match=msg):
        image_ops.validate(ops)


def test_edit_image_bad_ops_are_a_plain_error(human, web):
    tok = agent_token(human, cap=50)
    res, out = mcp_call(human, tok, "edit_image", {"image": "https://img.test/hp.png", "ops": [{"op": "explode"}]})
    assert res["isError"] and "unknown op" in out


# ------------------------------------------------------------------------------------------------ the ML cut-out job
def _matte_ready(human, monkeypatch):
    h = human.app.state.house
    sent = []
    monkeypatch.setattr(h, "worker_mode", "github")
    monkeypatch.setattr(house_mod.House, "_dispatch_github", lambda self, jid, workflow="render.yml", inputs=None, ttl=1800:
                        (sent.append((jid, inputs)) or True, "GitHub answered 204"))
    return h, sent


def test_cutout_hq_queues_birefnet_lite_on_the_worker_and_stores_its_png(human, web, monkeypatch):
    h, sent = _matte_ready(human, monkeypatch)
    st = h.store
    rid = _run(st)
    tok = agent_token(human, cap=50)
    lib0, _ = _counts(st)
    _, out = mcp_call(human, tok, "cutout_product", {"image": "https://img.test/hp.png", "engine": "hq", "run_id": rid})
    assert out["status"] == "queued" and out["model"] == "birefnet-general-lite" and out["model_licence"] == "MIT", out
    jid = out["job_id"]
    assert sent == [(jid, {"kind": "image_matte"})]
    job = st.q("SELECT run_id, spec FROM render_jobs WHERE id=?", (jid,))[0]
    spec = json.loads(job["spec"])
    assert job["run_id"] == rid and spec["kind"] == "image_matte" and spec["model"] == "birefnet-general-lite"
    assert not spec["source"].startswith("http")  # the worker gets a media token, never the user's URL
    # the worker reads the job (a /m/ URL on the app) and posts one PNG back
    jt = h.issue_job_token(jid)
    j = human.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()
    assert j["urls"]["source"] == "https://hq.test/m/" + spec["source"]
    cut = Image.new("RGBA", (300, 200), (0, 0, 0, 0))
    ImageDraw.Draw(cut).ellipse((20, 20, 280, 180), fill=(236, 234, 230, 255))
    r = human.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt},
                   json={"data": base64.b64encode(_png(cut)).decode(), "meta": {"engine": "hq", "model": "birefnet-general-lite",
                                                                                  "model_licence": "MIT", "evil": "x"}})
    assert r.status_code == 200 and r.json()["ok"]
    _, stat = mcp_call(human, tok, "render_status", {"job_id": jid})
    assert stat["status"] == "done" and stat["download_url"].endswith(".png") and stat["meta"]["model"] == "birefnet-general-lite"
    meta = json.loads(st.q("SELECT meta FROM media WHERE token=?", (stat["media_token"],))[0]["meta"])
    assert meta["kind"] == "edit" and meta["run_id"] == rid and "evil" not in meta
    assert _counts(st)[0] == lib0


def test_matte_job_refuses_a_non_png_result(human, monkeypatch):
    h, _ = _matte_ready(human, monkeypatch)
    up = human.post("/api/media", json={"name": "hp.png", "data": base64.b64encode(_png(headphones())).decode()}).json()
    out = image_edit.queue_matte(human.app.state.studio, h.store and __import__("studio.actions", fromlist=["Actor"]).Actor(
        h.store.q("SELECT id FROM users")[0]["id"]), up["token"], "isnet-general-use", None)
    jid = out["job_id"]
    jt = h.issue_job_token(jid)
    r = human.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt},
                   json={"data": base64.b64encode(b"<html>not a png</html>").decode(), "meta": {}})
    assert r.status_code == 415
    assert h.store.q("SELECT status FROM render_jobs WHERE id=?", (jid,))[0]["status"] == "failed"


def test_auto_without_a_worker_says_so_instead_of_a_torn_cut(human, web, monkeypatch):
    import random
    random.seed(6)
    busy = Image.effect_noise((500, 400), 90).convert("RGB")
    up = human.post("/api/media", json={"name": "busy.png", "data": base64.b64encode(_png(busy)).decode()}).json()
    monkeypatch.setattr(human.app.state.house, "worker_mode", "")
    monkeypatch.setattr(house_mod.House, "local_video_ok", staticmethod(lambda: False))
    monkeypatch.delenv("GH_DISPATCH_TOKEN", raising=False)
    tok = agent_token(human, cap=50)
    _, out = mcp_call(human, tok, "cutout_product", {"image": up["token"]})
    assert out["ok"] is False and "worker" in out["message"] and "compose_product_card" in out["message"]


# ------------------------------------------------------------------------------------------------ image_matte (worker side)
def test_models_are_always_named_and_bria_is_refused():
    assert image_matte.model_for("hq") == "birefnet-general-lite" and image_matte.model_for(None) == "isnet-general-use"
    assert image_matte.MODELS["isnet-general-use"]["licence"] == "Apache-2.0"
    assert image_matte.MODELS["birefnet-general-lite"]["licence"] == "MIT"
    for bad in ("bria-rmbg", "BRIA-RMBG-2.0", "rmbg-1.4", "u2net-something"):
        with pytest.raises(image_matte.MatteError):
            image_matte.check_model(bad)
    src = (ROOT / "studio" / "image_matte.py").read_text()
    assert "new_session(model)" in src and "new_session()" not in src  # never rembg's default (bria-rmbg)


def test_matte_with_a_fake_session_crops_and_names_the_model():
    seen = []

    def fake_remove(img):
        seen.append(img.size)
        out = img.convert("RGBA")
        a = Image.new("L", img.size, 0)
        ImageDraw.Draw(a).rectangle((100, 50, 899, 909), fill=255)
        out.putalpha(a)
        return out
    png, meta = image_matte.matte(_png(headphones()), "isnet-general-use", remove_fn=fake_remove)
    im = Image.open(io.BytesIO(png))
    assert seen == [(1000, 1000)] and im.mode == "RGBA" and im.size == (816, 876)
    assert meta["model"] == "isnet-general-use" and meta["model_licence"] == "Apache-2.0" and meta["engine"] == "ml"


def test_matte_worker_job_reads_the_token_url_through_get():
    got = []

    def get(u):
        got.append(u)
        return _png(headphones())
    import studio.image_matte as im_mod
    orig = im_mod.matte
    im_mod.matte = lambda data, model: (b"\x89PNG\r\n\x1a\nfake", {"model": model, "width": 1, "height": 1})
    try:
        png, meta = image_matte.worker_job({"kind": "image_matte", "source": "tok.png", "model": "birefnet-general-lite"},
                                           {"source": "https://hq.test/m/tok.png"}, get)
    finally:
        im_mod.matte = orig
    assert got == ["https://hq.test/m/tok.png"] and meta["model"] == "birefnet-general-lite"
    with pytest.raises(image_matte.MatteError):
        image_matte.worker_job({"kind": "image_matte", "model": "bria-rmbg"}, {"source": "https://hq.test/m/t"}, get)


def test_matte_load_refuses_bombs_and_non_images():
    with pytest.raises(image_matte.MatteError):
        image_matte.load(b"<html></html>")
    with pytest.raises(image_matte.MatteError):
        image_matte.load(_png(Image.new("1", (12000, 12000))))


# ------------------------------------------------------------------------------------------------ worker plumbing
def test_instabox_matte_runs_in_its_own_venv_with_no_token_in_the_command():
    cmd = instabox_worker.command(77, "https://hq.example", "image_matte", "main")
    assert "jt_" not in cmd and "read -r JOB_TOKEN" in cmd and "--job 77" in cmd
    assert "/data/venv-matte/bin/python" in cmd and "requirements-matte.txt" in cmd and "U2NET_HOME=/data/u2net" in cmd
    assert "flock /data/pip-matte.lock" in cmd and "HQ_LOCK=/data/hq.lock" in cmd
    assert "/data/venv/bin/python" not in cmd  # the video venv (MediaPipe) is never upgraded by rembg's numpy
    assert "venv-matte" not in instabox_worker.command(78, "https://hq.example", "take_render", "main")


def test_render_workflow_installs_the_matte_runner_only_for_matte_jobs():
    wf = (ROOT / ".github" / "workflows" / "render.yml").read_text()
    assert "kind: {description:" in wf and "-r requirements-matte.txt" in wf and "path: ~/.u2net" in wf
    assert "inputs.kind == 'image_matte'" in wf and "inputs.kind != 'image_matte'" in wf
    reqs = (ROOT / "requirements-matte.txt").read_text()
    assert "rembg[cpu]==2.0.86" in reqs
    web = (ROOT / "requirements-studio.txt").read_text()
    assert "rembg" not in web and "onnxruntime" not in web  # never on the web dyno


def test_notice_credits_the_models_and_runner():
    notice = (ROOT / "NOTICE").read_text()
    for s in ("danielgatis/rembg", "xuebinqin/DIS", "ZhengPeng7/BiRefNet", "isnet-general-use", "birefnet-general-lite",
              "Apache License 2.0", "BRIA"):
        assert s in notice, s


def test_docs_list_the_image_tools():
    doc = (ROOT / "docs" / "MCP_TOOLS.md").read_text()
    for n in ("fetch_image", "cutout_product", "edit_image", "compose_product_card"):
        assert f"`{n}`" in doc, n


# ------------------------------------------------------------------------------------------------ review fixes (PR #103)
def test_alternating_pads_never_grow_past_the_cap():
    ops = [{"op": "pad", "ratio": r} for r in ("16:9", "9:16") * 6]  # 12 pads: unbounded this would be gigapixels
    out, _ = image_ops.apply(Image.new("RGB", (3000, 3000), (200, 30, 30)), ops)
    assert max(out.size) <= image_ops.MAX_SIDE


def test_card_radius_zero_is_square_and_null_values_take_defaults():
    import random
    rng = random.Random(3)  # Python noise, not Image.effect_noise: that one shares C rand() with test_hero_cutout's photo
    busy = Image.frombytes("RGB", (600, 600), rng.randbytes(600 * 600 * 3))  # no plain stage: the whole photo is a card
    out, _ = image_ops.apply(busy, [{"op": "card", "ratio": "1:1", "radius": 0, "shadow": 0, "scale": 0.9}])
    # the photo's own top-left corner pixel is opaque photo, not the ground: square corners
    W = out.width
    box_side = int(W * 0.9)
    corner = out.getpixel(((W - box_side) // 2 + 1, (W - box_side) // 2 + 1))
    assert corner[:3] != (0, 0, 0)
    ops = image_ops.validate([{"op": "round", "radius": None}, {"op": "sharpen", "amount": None}])
    assert ops[0]["radius"] == 40 and ops[1]["amount"] == 1
    image_ops.apply(Image.new("RGB", (64, 64), (90, 90, 90)), ops)  # no TypeError


def test_pixel_crop_refers_to_the_source_when_the_working_copy_is_smaller():
    src = Image.new("RGB", (6000, 4000), (0, 0, 255))
    src.paste((255, 0, 0), (4000, 2000, 6000, 4000))  # red bottom-right block in source pixels
    work = src.copy()
    work.thumbnail((image_ops.MAX_SIDE, image_ops.MAX_SIDE))
    out, _ = image_ops.apply(work, [{"op": "crop", "box": [4100, 2100, 5900, 3900]}], source_size=src.size)
    assert out.convert("RGB").getpixel((out.width // 2, out.height // 2)) == (255, 0, 0)
    assert abs(out.width - 1800 * work.width / 6000) <= 2


def test_png_exif_orientation_is_applied_before_metadata_is_stripped():
    im = Image.new("RGB", (200, 100), (10, 200, 10))
    ex = Image.Exif()
    ex[0x0112] = 6  # rotate 90 CW on display
    buf = io.BytesIO()
    im.save(buf, "PNG", exif=ex.tobytes())
    data = buf.getvalue()
    opened, ext = image_edit.open_image(data)
    clean, ext = image_edit.strip_meta(data, ext, opened)
    back = Image.open(io.BytesIO(clean))
    assert ext == "png" and back.size == (100, 200) and not back.getexif().get(0x0112)


def test_image_calls_count_against_the_agent_tokens_daily_cap(human, web):
    up = human.post("/api/media", json={"name": "hp.png", "data": base64.b64encode(_png(headphones())).decode()}).json()
    tok = agent_token(human, cap=2)
    for _ in range(2):
        res, out = mcp_call(human, tok, "edit_image", {"image": up["token"], "ops": [{"op": "fit", "ratio": "1:1"}]})
        assert not res["isError"] and out["ok"], out
    res, out = mcp_call(human, tok, "edit_image", {"image": up["token"], "ops": [{"op": "fit", "ratio": "1:1"}]})
    assert res["isError"] and "daily cap" in str(out)


def test_reencoded_webp_carries_no_location_data():
    im = Image.new("RGB", (120, 80), (40, 90, 200))
    ex = Image.Exif()
    ex[0x8825] = {2: (53.0, 20.0, 0.0)}  # GPSInfo
    ex[0x010F] = "PhoneMaker"  # camera make
    buf = io.BytesIO()
    im.save(buf, "WEBP", exif=ex.tobytes())
    data = buf.getvalue()
    opened, ext = image_edit.open_image(data)
    clean, ext = image_edit.strip_meta(data, ext, opened)
    back = Image.open(io.BytesIO(clean))
    assert ext == "png" and not back.getexif() and b"PhoneMaker" not in clean and "exif" not in back.info

