"""Jev (TypeSafe jev-latest) as a second opinion on faces in a take (owner ask, 4 Nov 2026). The API is mocked here:
Noul yes -> the Box goes into the reframe track; no -> ignored; timeout / HTTP error -> local + centred crop, logged, the
take never fails; the per-take cap holds; images are JPEG within 280-1568 px with no EXIF and the Danger-Zone header; the
key is never logged. Consent: a person in the app gets Jev, an MCP app only after studio/consent.py approval.
One optional live call (TYPESAFE_API_KEY set) on the committed public-domain portrait reports the box and latency."""
import base64
import io
import json
import os
import time

import httpx
import pytest
from PIL import Image

from studio.media import facedetect, jev_faces

KEY = "test-dummy-typesafe-key"


def _portrait():
    return Image.open(facedetect.FIXTURE)


def _answer(p_face=0.95, box=None):
    box = box or {"left": 0.5, "right": 0.7, "top": 0.2, "bottom": 0.36}
    return {"model": "jev-1.14.0", "answers": {"face_visible": {"type": "noul", "noul": p_face},
                                               "speaker_face": {"type": "box", "box": box, "confidence": 0.9}},
            "usage": {"input_tokens": 300, "output_tokens": 20}}


class Api:
    def __init__(self, answer=None, fail=None):
        self.answer, self.fail, self.seen = answer or _answer(), fail, []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = req.read()
        self.seen.append((req, body))
        if self.fail == "timeout":
            raise httpx.ReadTimeout("timed out", request=req)
        if self.fail == "500":
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json=self.answer)


def _run(api, faces=None, scenes=(10.0, 20.0), duration=30.0, cap=None, monkeypatch=None, flag=True, logs=None):
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    http = httpx.Client(transport=httpx.MockTransport(api))
    return jev_faces.second_opinion("video.mp4", faces or [], list(scenes), duration, flag, cap=cap, http=http,
                                    grab=lambda p, t: _portrait(), log=(logs.append if logs is not None else lambda m: None))


def test_noul_yes_box_goes_into_the_track_and_the_request_is_right(monkeypatch):
    api, logs = Api(), []
    faces, rec = _run(api, cap=3, monkeypatch=monkeypatch, logs=logs)
    assert rec["enabled"] and rec["calls"] == 3 and rec["used"] == 3 and rec["errors"] == 0 and rec["fallback"] is None
    assert {f["why"] for f in rec["frames"]} == {"scene_start"} and all(f["verdict"] == "face" for f in rec["frames"])
    js = [f for f in faces if f.get("src") == "jev"]
    assert js and all(abs(f["cx"] - 0.6) < 1e-6 and abs(f["cy"] - 0.28) < 1e-6 and f["score"] >= 0.7 for f in js)
    req, body = api.seen[0]
    assert str(req.url) == "https://api.typesafe.ai/v1/systemone" and req.headers["TypeSafe-Danger-Zone-Images"] == "true"
    assert req.headers["Authorization"] == f"Bearer {KEY}" and req.headers["content-type"].startswith("multipart/form-data")
    start = body.index(b'{"model"')
    doc = json.loads(body[start:body.index(b"\r\n", start)])
    ref = doc["state"]["frame"]
    assert doc["model"] == "jev-latest" and doc["assets"] == {ref: {"type": "image", "part": "frame"}}
    assert doc["questions"]["face_visible"]["type"] == "noul" and doc["questions"]["speaker_face"]["type"] == "box"
    jpg = body[body.index(b"\xff\xd8"):]
    im = Image.open(io.BytesIO(jpg[:jpg.rindex(b"\xff\xd9") + 2]))
    assert im.format == "JPEG" and all(280 <= s <= 1568 for s in im.size) and not im.getexif()
    assert not any(KEY in m for m in logs)


def test_noul_no_or_unsettled_is_ignored(monkeypatch):
    local = [{"t": 5.0, "cx": 0.3, "cy": 0.4, "w": 0.1, "score": 0.9}]
    for p, verdict in ((0.05, "no_face"), (0.5, "unsettled")):
        faces, rec = _run(Api(_answer(p)), faces=local, cap=4, monkeypatch=monkeypatch)
        assert faces == local and rec["used"] == 0 and {f["verdict"] for f in rec["frames"]} == {verdict}


def test_a_yes_with_an_impossible_box_is_not_used():
    d = jev_faces.decide(_answer(0.9, {"left": 0.8, "right": 0.2, "top": 0.1, "bottom": 0.3}))
    assert d["verdict"] == "unsettled" and "box" not in d


@pytest.mark.parametrize("fail", ["timeout", "500"])
def test_errors_fall_back_to_local_and_never_fail_the_take(monkeypatch, fail):
    local = [{"t": 1.0, "cx": 0.5, "cy": 0.5, "w": 0.1, "score": 0.9}]
    logs = []
    faces, rec = _run(Api(fail=fail), faces=local, cap=2, monkeypatch=monkeypatch, logs=logs)
    assert faces == local and rec["errors"] == 2 and rec["used"] == 0 and "local faces + centred crop" in rec["fallback"]
    assert all(f["verdict"] == "error" and f["error"] for f in rec["frames"])
    assert any("jev:" in m for m in logs) and not any(KEY in m for m in logs)


def test_the_cap_holds_and_keyframes_follow_scenes_then_gaps(monkeypatch):
    api = Api()
    _, rec = _run(api, scenes=[float(s) for s in range(5, 300, 5)], duration=300.0, cap=12, monkeypatch=monkeypatch)
    assert rec["calls"] == 12 == len(api.seen) and rec["cap"] == 12
    monkeypatch.setenv("HQ_JEV_FACE_CAP", "5")
    assert jev_faces.cap_from_env() == 5
    api2 = Api()
    _, rec = _run(api2, scenes=[float(s) for s in range(5, 300, 5)], duration=300.0, monkeypatch=monkeypatch)
    assert rec["calls"] == 5 == len(api2.seen)
    _, rec = _run(Api(), cap=0, monkeypatch=monkeypatch)
    assert rec["calls"] == 0
    full = [{"t": k / 2, "cx": 0.5, "cy": 0.5, "w": 0.1, "score": 0.95} for k in range(61)]
    assert jev_faces.pick_keyframes(full, [10.0, 20.0], 30.0, cap=12) == []   # confident local faces: no call
    gaps = jev_faces.pick_keyframes(full[:20], [], 30.0, cap=12)
    assert gaps and {k["why"] for k in gaps} == {"no_local_face"} and all(k["t"] >= 9.5 for k in gaps)
    wk = jev_faces.pick_keyframes([dict(f, score=0.5) for f in full], [], 30.0, cap=3)
    assert len(wk) == 3 and wk[0]["why"] == "scene_start" and {k["why"] for k in wk[1:]} == {"low_confidence"}  # t=0 opens a scene


def test_off_without_a_key_or_without_consent(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    _, rec = jev_faces.second_opinion("v.mp4", [], [1.0], 10.0, True)
    assert not rec["enabled"] and "TYPESAFE_API_KEY" in rec["why"] and rec["calls"] == 0
    monkeypatch.setenv("TYPESAFE_API_KEY", KEY)
    for flag in (None, False, "true"):
        _, rec = jev_faces.second_opinion("v.mp4", [], [1.0], 10.0, flag)
        assert not rec["enabled"] and rec["calls"] == 0


def test_prepare_sizes_and_strips_metadata():
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    buf = io.BytesIO()
    Image.new("RGB", (200, 120), "white").save(buf, "JPEG", exif=exif)
    jpg, size = jev_faces.prepare(Image.open(io.BytesIO(buf.getvalue())))
    assert min(size) >= 280 and max(size) <= 1568 and b"SecretCam" not in jpg and b"Exif" not in jpg
    _, size = jev_faces.prepare(Image.new("RGB", (4000, 3000)))
    assert max(size) == 1568 and min(size) >= 280


def test_merge_keeps_confident_local_faces_and_fills_gaps():
    local = [{"t": 4.0, "cx": 0.2, "cy": 0.5, "w": 0.1, "score": 0.9}, {"t": 5.0, "cx": 0.2, "cy": 0.5, "w": 0.1, "score": 0.4}]
    res = [{"t": 5.0, "verdict": "face", "face_visible": 0.9, "box": {"left": 0.6, "right": 0.8, "top": 0.2, "bottom": 0.4}}]
    by_t = {f["t"]: f for f in jev_faces.merge(local, res)}
    assert by_t[4.0]["cx"] == 0.2                                  # confident local sample stays
    assert by_t[5.0]["src"] == "jev" and by_t[5.0]["cx"] == 0.7      # the weak one is replaced
    assert by_t[5.5]["src"] == "jev" and by_t[6.0]["src"] == "jev"   # held over local-empty samples (HOLD_S)
    assert res[0]["used"] is True


def test_analyze_records_jev_and_never_fails_on_it(monkeypatch):
    from studio import take
    monkeypatch.setattr(take, "probe", lambda p: {"duration": 10.0, "width": 1280, "height": 720, "audio": True, "codec": "h264"})
    monkeypatch.setattr(take, "detect_silences", lambda p: [])
    monkeypatch.setattr(take, "detect_scenes", lambda p: ([5.0], "test"))
    monkeypatch.setattr(take, "face_track", lambda p, d: ([], "OpenCV Haar cascade (test)"))
    import studio.mediasafe as ms
    monkeypatch.setattr(ms, "check_source", lambda p, *a, **k: {})

    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(jev_faces, "second_opinion", boom)
    an = take.analyze("v.mp4", transcript={"words": [{"w": "hi", "s": 0, "e": 1}], "source": "srt"}, jev=True, log=lambda m: None)
    assert an["jev"]["enabled"] is False and "error" in an["jev"]["why"] and an["faces"] == []


def test_people_in_the_app_get_jev_and_mcp_apps_wait_for_approval(human, app, monkeypatch):
    from .conftest import agent_token, mcp_call
    house, sent = app.state.house, []

    def dispatch(jid, workflow="", inputs=None, ttl=1800):
        sent.append(jid)
        return True, ""
    monkeypatch.setattr(house, "_dispatch_github", dispatch)
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    st = app.state.store

    def spec():
        return json.loads(st.q("SELECT spec FROM render_jobs WHERE id=?", (sent[-1],))[0]["spec"])
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    assert spec()["jev"] is True and t["input"]["jev"] is True
    human.post("/api/takes", json={"media_token": up["token"], "owns": True, "jev": False})
    assert spec()["jev"] is False
    assert human.post("/api/takes", json={"media_token": up["token"], "owns": True, "jev": "yes"}).status_code == 400
    tok = agent_token(human, policy="trusted", cap=50)
    url = house.media_url(up["token"])
    n = st.q("SELECT COUNT(*) n FROM agent_runs WHERE kind='take'")[0]["n"]
    _, out = mcp_call(human, tok, "clips_from_video", {"media_url": url, "owns": True, "jev": True})
    assert out["needs_user_approval"] is True and out["tool"] == "clips_from_video_jev" and out["spent_usd"] == 0
    assert st.q("SELECT COUNT(*) n FROM agent_runs WHERE kind='take'")[0]["n"] == n            # nothing was created
    _, out = mcp_call(human, tok, "clips_from_video", {"media_url": url, "owns": True})
    assert out.get("id") and spec()["jev"] is False                                              # apps: off unless approved


def test_the_take_shows_which_frames_used_jev():
    from studio.takes import _jev_view
    rec = {"enabled": True, "why": "on", "model": "jev-latest", "cap": 12, "calls": 2, "used": 1, "errors": 1,
           "fallback": "1 of 2 Jev calls failed", "frames": [
               {"t": 0.4, "why": "scene_start", "verdict": "face", "face_visible": 0.97, "box": {"left": 0.5}, "used": True, "ms": 900,
                "input_tokens": 300},
               {"t": 12.0, "why": "no_local_face", "verdict": "error", "error": "ReadTimeout", "used": False, "ms": 20000}]}
    v = _jev_view(rec)
    assert v["used"] == 1 and [f["why"] for f in v["frames"]] == ["scene_start", "no_local_face"]
    assert "input_tokens" not in v["frames"][0] and _jev_view(None) is None


@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="live: set TYPESAFE_API_KEY to call Jev once")
def test_live_one_jev_call_on_the_portrait():
    jpg, _ = jev_faces.prepare(_portrait())
    t0 = time.perf_counter()
    with httpx.Client(timeout=30) as c:  # honours the environment's proxy (a sandbox may inject the key on the wire)
        data = jev_faces.ask(jpg, os.environ["TYPESAFE_API_KEY"], http=c, timeout=30)
    ms = (time.perf_counter() - t0) * 1000
    d = jev_faces.decide(data)
    print(f"live Jev ({data.get('model')}): {d} in {ms:.0f} ms, {data.get('usage')}")
    assert d["verdict"] == "face"
    b = d["box"]
    # the face in the portrait is around x 245-340, y 118-213 of 480x600
    assert 0.4 < (b["left"] + b["right"]) / 2 < 0.8 and 0.12 < (b["top"] + b["bottom"]) / 2 < 0.45
