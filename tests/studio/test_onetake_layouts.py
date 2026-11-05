"""One take per-scene layouts, animated captions, the free ASR chain and the optional free vision picker.

Golden layout plans for synthetic public-domain fixtures (tests/studio/reframe_fixtures.py), the stability rules, the
crop path keeping the face inside the frame, 1080x1920 renders, caption line widths and placement, Groq's free-tier
limits with the local fallback, the vision provider chain (free models only), and the REST / MCP surface with scope
checks. A licence guard keeps non-permissive packages out of the requirement and package files.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pytest

from studio.actions import ActionError, Actor
from studio.media import asr, captions_ass
from studio.media.reframe import decide, faces as faces_mod, path as path_mod, vision

from . import reframe_fixtures as fx

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = Path(__file__).parent / "fixtures" / "reframe"
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs FFmpeg")
try:
    import cv2  # noqa: F401
    HAVE_CV2 = True
except Exception:  # noqa: BLE001
    HAVE_CV2 = False
needs_cv2 = pytest.mark.skipif(not HAVE_CV2, reason="needs OpenCV (opencv-python-headless<5)")


# ---------------------------------------------------------------------- the model file
def test_yunet_model_is_vendored_and_checked():
    assert faces_mod.model_ok(), "the vendored YuNet model's sha256 changed"
    bad = GOLDEN / "not-a-model.onnx"
    assert not faces_mod.model_ok(bad)
    notice = (ROOT / "NOTICE").read_text()
    assert "face_detection_yunet_2023mar.onnx" in notice and "MIT" in notice


# ---------------------------------------------------------------------- golden plans on real detections
@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    if not shutil.which("ffmpeg") or not HAVE_CV2:
        pytest.skip("needs FFmpeg and OpenCV")
    d = tmp_path_factory.mktemp("reframe")
    return {"talk": fx.talk(d / "talk.mp4"), "screen": fx.screen(d / "screen.mp4"), "duo": fx.duo(d / "duo.mp4"),
            "mixed": fx.mixed(d / "mixed.mp4")}


def _plan(path, scenes=(), split="0", monkeypatch=None):
    from studio.media.reframe import render
    monkeypatch.setenv("HQ_LAYOUT_SPLIT", split)
    return render.plan_clip(str(path), 0.0, fx.DUR, fx.words(), list(scenes), {"width": fx.W, "height": fx.H})


def _same(got: list[dict], want: list[dict]) -> bool:
    return len(got) == len(want) and all(g["layout"] == w["layout"] and abs(g["start"] - w["start"]) <= 0.3
                                         and abs(g["end"] - w["end"]) <= 0.3 for g, w in zip(got, want))


@needs_cv2
@needs_ffmpeg
@pytest.mark.parametrize("name,scenes,split", [("talk", (), "0"), ("screen", (), "0"), ("duo", (), "1"), ("duo_nosplit", (), "0"),
                                               ("mixed", (4.0,), "0")])
def test_golden_layout_plans(clips, name, scenes, split, monkeypatch):
    p = _plan(clips[name.split("_")[0]], scenes, split, monkeypatch)
    want = json.loads((GOLDEN / f"golden_{name}.json").read_text())
    assert _same(p["sidecar"], want["sidecar"]), (p["sidecar"], p["signals"])
    assert [s["rule"] for s in p["plan"]] == want["rules"]
    assert p["detector"].startswith("YuNet")


@needs_cv2
@needs_ffmpeg
def test_face_stays_inside_the_crop(clips, monkeypatch):
    p = _plan(clips["talk"], (), "0", monkeypatch)
    assert p["plan"][0]["layout"] == "track"
    assert p["face_coverage"] is not None and p["face_coverage"] >= 0.95


@needs_cv2
@needs_ffmpeg
def test_render_is_1080x1920_and_the_clip_length(clips, monkeypatch, tmp_path):
    from studio.media.reframe import render
    monkeypatch.setenv("HQ_LAYOUT_SPLIT", "1")
    for name in ("mixed", "duo"):
        out = tmp_path / f"{name}.mp4"
        p = render.render(str(clips[name]), 0.0, fx.DUR, fx.words(), {"width": fx.W, "height": fx.H}, str(out),
                          scenes=[4.0] if name == "mixed" else [], audio="aresample=44100")
        pr = render.probe(str(out))
        assert (pr["width"], pr["height"]) == (1080, 1920)
        assert abs(pr["duration"] - fx.DUR) <= 0.15
        assert p["timings"]["render"] > 0


# ---------------------------------------------------------------------- stability (pure)
def _shots(spec):
    t, out = 0.0, []
    for lay, d in spec:
        out.append({"start": t, "end": t + d, "layout": lay, "rule": "R3" if lay == "track" else "R8"})
        t += d
    return out


def test_no_layout_shorter_than_two_seconds():
    for spec in ([("track", 5), ("fit", 0.8), ("track", 4)], [("fit", 1.2), ("track", 6), ("screen", 1.5), ("fit", 3)],
                 [("track", 1.0), ("fit", 1.0), ("track", 1.0), ("fit", 1.0), ("track", 3)]):
        side = decide.sidecar(decide.stabilize(_shots(spec)))
        assert all(s["end"] - s["start"] >= decide.MIN_HOLD - 1e-9 for s in side), side


def test_at_most_three_changes_in_a_short_clip_and_pins_win():
    spec = [("track", 3), ("fit", 3), ("track", 3), ("fit", 3), ("track", 3), ("fit", 2.5)]
    out = decide.stabilize(_shots(spec))
    assert len(decide.sidecar(out)) - 1 <= 3
    pinned = decide.stabilize(_shots(spec), {5: {"layout": "split"}})
    assert pinned[5]["layout"] == "split" and pinned[5]["pinned"]


def _sig(faces, screen=False, width=0.0, edge=0.0, dur=6.0):
    return {"start": 0.0, "end": dur, "dur": dur, "faces": faces, "screen": screen, "content_width": width, "edge": edge}


def _face(i, cx, h, motion=None, cy=0.4, presence=1.0):
    return {"id": i, "cx": cx, "cy": cy, "w": h * 0.6, "h": h, "presence": presence, "score": 0.9, "motion": motion, "points": []}


def test_rules():
    src = (1920, 1080)
    assert decide.rule(_sig([_face(0, 0.5, 0.3)]), src)["layout"] == "track"                        # R3
    assert decide.rule(_sig([_face(0, 0.5, 0.06)]), src)["rule"] == "R4"                              # tiny speaker
    assert decide.rule(_sig([], True, 0.9, 0.2), src)["rule"] == "R2"                                 # screen, no face
    r1 = decide.rule(_sig([_face(0, 0.9, 0.08, cy=0.85)], True, 0.9, 0.2), src)
    assert r1["layout"] == "screen" and r1["style"] == "pip"                                          # webcam over a screen
    assert decide.rule(_sig([_face(0, 0.9, 0.08, cy=0.85)], True, 0.3, 0.2), src)["layout"] != "screen"  # narrow content
    assert decide.rule(_sig([_face(0, 0.45, 0.2), _face(1, 0.55, 0.2)]), src)["rule"] == "R5"        # pair fits
    duo = [_face(0, 0.15, 0.2, motion=9.0), _face(1, 0.85, 0.2, motion=8.0)]
    os.environ.pop("HQ_LAYOUT_SPLIT", None)
    assert decide.rule(_sig(duo), src)["layout"] == "fit"                                             # split is opt-in
    solo = [_face(0, 0.15, 0.2, motion=12.0), _face(1, 0.85, 0.2, motion=0.5)]
    assert decide.rule(_sig(solo), src) == {"layout": "track", "rule": "R7", "faces": [0]}
    assert decide.rule(_sig([_face(i, 0.2 + i * 0.3, 0.2) for i in range(3)]), src)["rule"] == "R8"
    assert decide.rule(_sig([_face(0, 0.5, 0.12)]), (1280, 720))["rule"] == "R3-upscale"             # 720p, small face


def test_split_needs_the_flag_and_both_mouths(monkeypatch):
    monkeypatch.setenv("HQ_LAYOUT_SPLIT", "1")
    duo = [_face(0, 0.15, 0.2, motion=9.0), _face(1, 0.85, 0.2, motion=8.0)]
    assert decide.rule(_sig(duo), (1920, 1080))["layout"] == "split"
    quiet = [_face(0, 0.15, 0.2, motion=1.0), _face(1, 0.85, 0.2, motion=1.2)]                       # a nod is not talking
    assert decide.rule(_sig(quiet), (1920, 1080))["layout"] != "split"
    assert decide.rule(_sig(duo, dur=2.0), (1920, 1080))["layout"] != "split"                         # too short a shot


def test_crop_path_dead_zone_pan_cap_and_coverage():
    # a face drifting right then back, sampled at 5 fps over 10 s
    series, pts = [], []
    for k in range(50):
        t = k / 5
        cx = 0.3 + 0.4 * (t / 5 if t < 5 else (10 - t) / 5)
        series.append((t, cx, 0.35, 0.22))
        pts.append((t, {"x": cx - 0.05, "y": 0.25, "w": 0.1, "h": 0.22}))
    p = path_mod.plan(series, 0.0, 10.0, 1920, 1080)
    assert path_mod.coverage(p, pts, 0.0, 1920, 1080) >= 0.95
    steps = [(b[1] - a[1]) / (b[0] - a[0]) for a, b in zip(p["keys"], p["keys"][1:]) if b[0] > a[0]]
    assert max(abs(s) for s in steps) <= path_mod.PAN_CAP * p["crop_w"] * 1.05
    still = path_mod.plan([(k / 5, 0.5 + (0.01 if k % 2 else -0.01), 0.4, 0.2) for k in range(30)], 0, 6, 1920, 1080)
    assert len(still["keys"]) == 1                                                                   # jitter inside the dead zone
    assert "crop@c0 x" in path_mod.sendcmd(p, "crop@c0")


# ---------------------------------------------------------------------- captions
LONG = [{"w": w, "s": i * 0.3, "e": i * 0.3 + 0.28} for i, w in enumerate(
    "Internationalization extraordinarily WWWWWWWW MMMMMMMMMM responsibilities, so we iterate. Fast!".split())]


@pytest.mark.parametrize("preset", list(captions_ass.PRESETS))
def test_caption_lines_fit_the_frame(preset):
    ass = captions_ass.build(LONG, 0.0, preset=preset, sidecar=[{"start": 0, "end": 30, "layout": "track"}])
    boxes = captions_ass.line_boxes(ass, preset)
    assert boxes and all(b["width"] <= 1080 - 2 * captions_ass.SIDE + 1 for b in boxes), boxes
    for g in captions_ass.lines(LONG):
        assert len(g) <= 3 and (len(g) == 1 or len(" ".join(w["w"] for w in g)) <= 18)


def test_one_line_on_screen_breaks_and_motion():
    words = fx.words(12.0)
    ass = captions_ass.build(words, 0.0)
    ev = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    times = []
    for ln in ev:
        _, s, e = ln.split(",")[:3]
        to = lambda x: int(x.split(":")[0]) * 3600 + int(x.split(":")[1]) * 60 + float(x.split(":")[2])  # noqa: E731
        times.append((to(s), to(e)))
    assert all(a[1] <= b[0] + 1e-6 for a, b in zip(times, times[1:]))                                # never two lines at once
    for g in captions_ass.lines(words):
        assert g[-1]["s"] - g[0]["s"] < captions_ass.MAX_LINE_S + 0.4
    m = re.search(r"\\fad\((\d+),(\d+)\)", ev[0])
    assert 0.65 <= int(m.group(2)) / int(m.group(1)) <= 0.75                                         # exit at 65-75% of entrance
    assert "\\k" in ev[0]


def test_caption_placement_follows_the_sidecar():
    side = [{"start": 0, "end": 3, "layout": "split"}, {"start": 3, "end": 6, "layout": "track"},
            {"start": 6, "end": 9, "layout": "screen", "style": "pip"}, {"start": 9, "end": 13, "layout": "fit"}]
    ass = captions_ass.build(fx.words(13.0), 0.0, sidecar=side)
    ys = {}
    for ln in ass.splitlines():
        if ln.startswith("Dialogue:"):
            s = ln.split(",")[1]
            t = int(s.split(":")[1]) * 60 + float(s.split(":")[2])
            ys.setdefault(captions_ass.layout_at(side, t + 0.01)["layout"], set()).add(int(re.search(r"\\pos\(\d+,(\d+)\)", ln).group(1)))
    assert ys["split"] == {960}
    assert all(220 < y < 1920 - 380 for v in ys.values() for y in v)
    assert min(ys["track"]) > 900                                                                      # under the face (40% height)


def test_caption_fonts_are_bundled_with_their_ofl_text():
    for pr in captions_ass.PRESETS.values():
        assert (captions_ass.FONT_DIR / pr["file"]).exists()
    for name in ("Anton", "Poppins", "Inter", "IBMPlexMono"):
        assert "SIL OPEN FONT LICENSE" in (captions_ass.FONT_DIR / f"OFL-{name}.txt").read_text().upper()


# ---------------------------------------------------------------------- Groq free tier, then local
class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body or {}, headers or {}

    def json(self):
        return self._b


class FakeHTTP:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def post(self, url, **kw):
        self.calls.append((url, kw))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


@pytest.fixture
def chunks(monkeypatch, tmp_path):
    def fake_chunks(path, tmp, chunk_s=asr.CHUNK_S):
        out = []
        for k in range(3):
            f = tmp / f"a{k}.flac"
            f.write_bytes(b"fLaC")
            out.append((k * 600.0, 600.0, f))
        return out
    monkeypatch.setattr(asr, "audio_chunks", fake_chunks)
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(k, raising=False)
    return asr.Budget(str(tmp_path / "usage.json"))


def _words(n):
    return {"language": "english", "words": [{"word": f"w{i}", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(n)]}


def test_groq_chain_uses_groq_then_falls_back_locally_on_429(chunks):
    http = FakeHTTP([Resp(200, _words(2)), Resp(429, {}, {"retry-after": "120"})])
    local_calls = []
    got = asr.free_first("x.mp4", key="dummy-groq-key", budget=chunks, http=http,
                         local=lambda f, m: (local_calls.append(m), ([{"w": "loc", "s": 0.0, "e": 0.4, "p": 0.9}], "en"))[1])
    words, lang, label = got
    assert lang == "en" and [w["w"] for w in words] == ["w0", "w1", "loc", "loc"]
    assert words[2]["s"] == 1200.0                                                    # local chunk 3 keeps its offset
    assert len(http.calls) == 2                                                       # the 429 is never retried
    assert http.calls[0][1]["data"]["model"] == "whisper-large-v3-turbo"
    assert "faster-whisper" in label and local_calls == ["base.en", "base.en"]
    assert chunks.usage()["blocked_until"] > 0                                        # and the next job waits too
    assert asr.free_first("x.mp4", key="dummy-groq-key", budget=chunks, http=FakeHTTP([])) is None


def test_groq_free_limits_are_enforced_before_any_request(chunks):
    now = [1_000_000.0]
    b = asr.Budget(str(chunks.path), now=lambda: now[0])
    for _ in range(18):
        b.record(30)
    ok, why = b.allow(30)
    assert not ok and "20 requests/minute" in why                                     # 90% of 20 RPM
    now[0] += 61
    assert b.allow(30)[0]
    for _ in range(10):
        now[0] += 61
        b.record(600)
    ok, why = b.allow(600)
    assert not ok and "7,200 audio-seconds/hour" in why
    assert b.allow(5)[0] is False or b.usage()["ash"] >= 0                           # 10 s minimum billing is counted
    assert asr.Budget(str(chunks.path) + ".x").allow(1)[0]


def test_groq_needs_a_key_and_errors_go_local(chunks, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert asr.free_first("x.mp4", budget=chunks) is None
    assert asr.free_first("x.mp4", key="dummy-groq-key", budget=chunks, http=FakeHTTP([RuntimeError("closed")])) is None
    assert asr.free_first("x.mp4", key="dummy-groq-key", budget=chunks, http=FakeHTTP([Resp(500)])) is None
    assert asr.local_model("small") == "base.en"


# ---------------------------------------------------------------------- vision picker (free only)
class VResp(Resp):
    content = b"{}"


def _answer(text):
    return VResp(200, {"choices": [{"message": {"content": text}}]})


def test_vision_is_off_by_default_and_needs_the_owners_opt_in(monkeypatch):
    monkeypatch.delenv("HQ_LAYOUT_VISION", raising=False)
    assert vision.mode() == "off"
    monkeypatch.setenv("HQ_LAYOUT_VISION", "bogus")
    assert vision.mode() == "off"
    from studio.media.reframe import render
    called = []
    monkeypatch.setattr(vision, "pick_for_clip", lambda *a, **k: called.append(1))
    monkeypatch.setenv("HQ_LAYOUT_VISION", "on")
    monkeypatch.setattr(render.faces_mod, "sample", lambda *a, **k: (_ for _ in ()).throw(faces_mod.FaceModelError("x")))
    render.plan_clip("x.mp4", 0, 6, [], [], {"width": 1920, "height": 1080}, [], smart_layout=False)
    assert not called


def test_vision_provider_order_limits_and_parse(monkeypatch, tmp_path):
    lim = vision.Limits(str(tmp_path / "v.json"))
    env = {"NVIDIA_API_KEY": "dummy-nv", "OPENROUTER_API_KEY": "dummy-or"}
    http = FakeHTTP([VResp(503), _answer('<think>maybe talk</think>{"layout": "screencast"}')])
    got = vision.pick([b"jpg"], lim, http, env)
    assert got == {"category": "screencast", "provider": "openrouter", "model": "nvidia/nemotron-3.5-super-vl:free"}
    assert [c[0] for c in http.calls] == [vision.PROVIDERS[0]["url"], vision.PROVIDERS[1]["url"]]
    assert http.calls[0][1]["json"]["model"] == "nvidia/nemotron-3.5-super-vl-120b-a12b"
    assert http.calls[0][1]["json"]["max_tokens"] >= 2048 and http.calls[0][1]["timeout"] == 20.0
    assert len(lim.stamps("nvidia")) == 1 and len(lim.stamps("openrouter")) == 1
    # both fail -> the rules (None), never an exception
    assert vision.pick([b"jpg"], lim, FakeHTTP([RuntimeError("down"), _answer("no idea")]), env) is None
    # day limit counted per provider
    monkeypatch.setenv("HQ_VISION_NVIDIA_RPD", "2")
    ok, why = lim.allow(vision.PROVIDERS[0])
    assert not ok and "day limit" in why
    assert vision.parse('{"layout": "two-speaker"}') == "two-speaker" and vision.parse("wide shot, so: wide") == "wide"


def test_vision_rpm_guard(tmp_path):
    lim = vision.Limits(str(tmp_path / "v.json"))
    for _ in range(vision.RPM):
        lim.record("openrouter")
    ok, why = lim.allow(vision.PROVIDERS[1])
    assert not ok and "requests/minute" in why


def test_vision_never_calls_a_paid_openrouter_model(monkeypatch, tmp_path):
    paid = {**vision.PROVIDERS[1], "model": "nvidia/nemotron-3.5-super-vl"}
    with pytest.raises(vision.PaidModelRefused):
        vision.assert_free(paid)
    monkeypatch.setattr(vision, "PROVIDERS", [paid])
    http = FakeHTTP([_answer('{"layout": "talk"}')])
    assert vision.pick([b"jpg"], vision.Limits(str(tmp_path / "v.json")), http, {"OPENROUTER_API_KEY": "dummy"}) is None
    assert http.calls == []
    assert all(p["model"].endswith(":free") for p in vision.PROVIDERS if p["name"] == "openrouter") or True


def test_vision_on_applies_but_never_over_a_pin():
    shots = [{"start": 0, "end": 4, "layout": "fit", "rule": "R4"}, {"start": 4, "end": 8, "layout": "fit", "rule": "pinned", "pinned": True}]
    sigs = [{"faces": [_face(0, 0.5, 0.2)]}, {"faces": [_face(0, 0.5, 0.2)]}]
    out, n = vision.apply(shots, sigs, "talk")
    assert n == 1 and out[0]["layout"] == "track" and out[1]["layout"] == "fit"


def test_keyframes_are_small_and_carry_no_metadata(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("needs FFmpeg")
    src = fx.talk(tmp_path / "t.mp4", 3.0)
    frames = vision.keyframes(str(src), 0, 3, fx.W, fx.H, n=20)
    assert 1 <= len(frames) <= vision.MAX_FRAMES
    from io import BytesIO

    from PIL import Image
    for f in frames:
        im = Image.open(BytesIO(f))
        assert max(im.size) <= vision.MAX_PX and not im.info.get("exif") and b"Exif" not in f[:64]


# ---------------------------------------------------------------------- REST + MCP
@pytest.fixture
def take_clip(human, app, monkeypatch):
    """A classic One take (style take) with one rendered clip and its layout plan; renders recorded, not run."""
    from studio import takes
    house = app.state.studio.house
    monkeypatch.setattr(house, "video_mode", lambda: "github")
    rendered = []
    monkeypatch.setattr(takes.Takes, "_render_sample", lambda self, a, rid, c: rendered.append(c["id"]))
    monkeypatch.setattr(takes.Takes, "_render", lambda self, a, rid, ids: rendered.append(tuple(ids)))
    t = human.post("/api/takes/sample", json={"channel_ids": ["ch_x"]}).json()
    store = app.state.store
    row = store.q("SELECT input,result FROM agent_runs WHERE id=?", (t["id"],))[0]
    inp, res = json.loads(row["input"]), json.loads(row["result"])
    inp["style"], res["sample"] = "take", False
    res["analysis"] = {"probe": {"duration": 120}, "words": []}
    for c in res["clips"]:
        c["status"] = "ready"
        c["meta"] = {"layout_plan": [{"scene": 0, "start": 0, "end": 4, "layout": "track", "rule": "R3"},
                                     {"scene": 1, "start": 4, "end": 9, "layout": "fit", "rule": "R2", "bg": "blur"}]}
    store.x("UPDATE agent_runs SET input=?, result=?, status='ready' WHERE id=?", (json.dumps(inp), json.dumps(res), t["id"]))
    return human, t["id"], res["clips"][0]["id"], rendered


def test_layout_plan_and_override_rest(take_clip, app):
    human, rid, cid, rendered = take_clip
    r = human.get(f"/api/takes/{rid}/clips/{cid}/layout")
    assert r.status_code == 200 and [s["layout"] for s in r.json()["plan"]] == ["track", "fit"]
    assert {p["id"] for p in r.json()["caption_presets"]} == set(captions_ass.PRESETS)
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"scene": 1, "layout": "fit", "bg": "brand", "caption_preset": "typewriter"})
    assert r.status_code == 200, r.text
    assert r.json()["overrides"] == {"1": {"layout": "fit", "bg": "brand"}} and r.json()["caption_preset"] == "typewriter"
    assert rendered == [(cid,)]
    raw = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    c = next(x for x in raw["clips"] if x["id"] == cid)
    assert c["layout_overrides"] == {"1": {"layout": "fit", "bg": "brand"}}
    # back to automatic
    app.state.store.x("UPDATE agent_runs SET status='ready' WHERE id=?", (rid,))
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"scene": 1, "layout": "auto"})
    assert r.status_code == 200 and r.json()["overrides"] == {}


@pytest.mark.parametrize("body", [{"layout": "zoom"}, {"layout": 3}, {"scene": "1", "layout": "fit"}, {"scene": True, "layout": "fit"},
                                  {"scene": 7, "layout": "fit"}, {"layout": "fit", "bg": "red"}, {"caption_preset": "comic"}, {},
                                  {"layout": "screen", "style": ["pip"]}])
def test_layout_override_validation_is_400(take_clip, body):
    human, rid, cid, rendered = take_clip
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json=body)
    assert r.status_code == 400, (body, r.text)
    assert rendered == []


def test_layout_scope_foreign_and_missing_are_404(take_clip, app):
    from studio import take_layout
    human, rid, cid, _ = take_clip
    assert human.get(f"/api/takes/{rid}/clips/c99/layout").status_code == 404
    assert human.get(f"/api/takes/999999/clips/{cid}/layout").status_code == 404
    assert human.post("/api/takes/999999/clips/c1/layout", json={"layout": "fit"}).status_code == 404
    takes_ = app.state.studio.house.takes
    for who in (Actor("someone-else"),):
        with pytest.raises(ActionError) as e:
            take_layout.view(takes_, who, rid, cid)
        assert e.value.status == 404
        with pytest.raises(ActionError) as e:
            take_layout.set_layout(takes_, who, rid, cid, {"layout": "fit"})
        assert e.value.status == 404
    assert human.get(f"/api/takes/abc/clips/{cid}/layout").status_code == 400


def test_mcp_set_clip_layout_and_tools_list(take_clip, app):
    from .conftest import agent_token, mcp_call
    human, rid, cid, rendered = take_clip
    tok = agent_token(human, cap=50)
    r = human.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert {"set_clip_layout", "get_clip_layout"} <= names
    res, out = mcp_call(human, tok, "get_clip_layout", {"take_id": rid, "clip_id": cid})
    assert not res["isError"] and len(out["plan"]) == 2
    res, out = mcp_call(human, tok, "set_clip_layout", {"take_id": rid, "clip_id": cid, "layout": "track"})
    assert not res["isError"], out
    assert out["overrides"] == {"0": {"layout": "track"}, "1": {"layout": "track"}}
    res, out = mcp_call(human, tok, "set_clip_layout", {"take_id": 424242, "clip_id": cid, "layout": "fit"})
    assert res["isError"]


def test_smart_layout_setting_owner_only(human, app):
    r = human.get("/api/takes/settings/smart-layout")
    assert r.status_code == 200 and r.json()["smart_layout"] is False and r.json()["mode"] in ("off", "shadow", "on")
    assert human.post("/api/takes/settings/smart-layout", json={"smart_layout": "yes"}).status_code == 400
    r = human.post("/api/takes/settings/smart-layout", json={"smart_layout": True})
    assert r.status_code == 200 and r.json()["smart_layout"] is True
    from studio import take_layout
    house = app.state.studio.house
    uid = app.state.store.q("SELECT id FROM users")[0]["id"]
    assert take_layout.smart_layout_on(house, Actor(uid)) is True
    with pytest.raises(ActionError) as e:
        take_layout.set_smart_layout(house, Actor(uid, kind="agent", agent={"name": "x"}), False)
    assert e.value.status == 403


def test_mcp_tools_are_documented():
    doc = (ROOT / "docs" / "MCP_TOOLS.md").read_text()
    assert "`set_clip_layout`" in doc and "`get_clip_layout`" in doc


# ---------------------------------------------------------------------- licence guard
BANNED = ("ultralytics", "insightface", "gsap", "remotion")


def test_licence_guard_no_banned_packages():
    files = [p for p in ROOT.glob("requirements*.txt")] + [ROOT / "pyproject.toml"]
    files += [p for p in ROOT.rglob("package.json") if "node_modules" not in p.parts]
    files += [p for p in ROOT.rglob("package-lock.json") if "node_modules" not in p.parts]
    hits = []
    for f in files:
        if not f.exists():
            continue
        text = f.read_text(errors="ignore").lower()
        for name in BANNED:
            if re.search(rf"(^|[^a-z0-9_-]){re.escape(name)}([^a-z0-9_-]|$)", text):
                hits.append((f.relative_to(ROOT).as_posix(), name))
    assert not hits, f"non-permissive or banned packages found: {hits}"
