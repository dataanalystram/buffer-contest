"""Words behind you (studio/media/behind.py): a hook word drawn behind the speaker for the first seconds of a One take clip.

Pure parts (word choice, placement, fades, compositing under the mask) run everywhere. The real render needs FFmpeg,
MediaPipe and its selfie model (downloaded once) and runs on the committed public-domain portrait. The REST / MCP surface:
off unless HQ_HOOK_BEHIND=1, words must be said in the hook, only for One take clips from an upload, and the worker fails
open (the clip as rendered, the reason in its meta).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio.media import behind

ROOT = Path(__file__).resolve().parents[2]
FACE = ROOT / "tests" / "studio" / "fixtures" / "media" / "face_armstrong_pd.jpg"


# ---------------------------------------------------------------------- pure
def test_pick_words_extracts_never_invents():
    assert behind.pick_words("We shipped it in two days") == "SHIPPED"
    assert behind.pick_words("We shipped it in two days", "two days") == "TWO DAYS"
    assert behind.pick_words("Why nobody reads the docs", "nobody") == "NOBODY"
    for bad in ("launch", "one two three four", "supercalifragilistic expialidocious"):
        with pytest.raises(behind.BehindError):
            behind.pick_words("We shipped it in two days", bad)
    with pytest.raises(behind.BehindError):
        behind.pick_words("it is a the")


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("HQ_HOOK_BEHIND", raising=False)
    assert not behind.enabled()
    monkeypatch.setenv("HQ_HOOK_BEHIND", "1")
    assert behind.enabled()


def test_place_overlaps_the_head_and_respects_the_top_ui():
    W, H = 720, 1280
    x, y = behind.place(W, H, top=300, text_w=400, text_h=200, cx=500)
    assert y < 300 < y + 200                    # the head's top is inside the word's box: it overlaps the lower part
    assert x == 300 and x + 400 <= W            # centred on the head, kept inside the frame
    assert behind.place(W, H, top=40, text_w=400, text_h=200)[1] >= int(H * behind.TOP_SAFE)
    assert behind.place(W, H, top=None, text_w=400, text_h=200) == (160, int(H * 0.18))


def test_alpha_fades_in_and_out():
    assert behind.alpha_at(0.1, 0.2, 2.6) == 0 and behind.alpha_at(3.0, 0.2, 2.6) == 0
    assert behind.alpha_at(1.5, 0.2, 2.6) == pytest.approx(1.0)
    assert 0 < behind.alpha_at(0.25, 0.2, 2.6) < 1 and 0 < behind.alpha_at(2.75, 0.2, 2.6) < 1


def test_composite_puts_the_word_under_the_person():
    frame = np.full((100, 100, 3), 40, np.uint8)
    rgb = np.full((20, 60, 3), 255.0, np.float32)
    a = np.ones((20, 60), np.float32)
    person = np.zeros((100, 100), np.float32)
    person[:, 50:] = 1.0                        # the right half is the person
    out = behind.composite(frame, rgb, a, 20, 10, person, 1.0)
    assert out[15, 30].tolist() == [255, 255, 255]   # the word shows on the background
    assert out[15, 60].tolist() == [40, 40, 40]      # and is hidden behind the person
    assert out[50, 30].tolist() == [40, 40, 40]      # nothing outside the word's box changes
    assert behind.composite(frame, rgb, a, 20, 10, person, 0.0) is frame


def test_head_top_and_centre_from_the_mask():
    m = np.zeros((200, 100), np.float32)
    m[60:, 30:70] = 1
    top = behind.head_top(m)
    assert top == 60 and behind.head_x(m, top) == 49
    assert behind.head_top(np.zeros((10, 10), np.float32)) is None


def test_on_clip_fails_open():
    data, meta = behind.on_clip(b"not a video", "it is a the", None, {}, 0.2)
    assert data == b"not a video" and meta["on"] is False and meta["why"]


# ---------------------------------------------------------------------- the real render
def _seg_or_skip():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("needs FFmpeg")
    try:
        return behind.segmenter()
    except behind.BehindError as e:
        pytest.skip(str(e))


def _audio_md5(path: Path) -> str:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _frame(path: Path, t: float) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                        "-pix_fmt", "rgb24", "-"], capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(1280, 720, 3).astype(np.int16)


def test_real_render_on_a_portrait(tmp_path):
    seg = _seg_or_skip()
    src, out = tmp_path / "in.mp4", tmp_path / "out.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(FACE), "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=44100", "-t", "4", "-vf",
                    "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p", "-r", "30",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", str(src)], check=True)
    try:
        got = behind.apply(str(src), str(out), "MOON", start=0.2, kit_font="anton", seg=seg)
    finally:
        seg.close()
    assert got["on"] and got["frames"] == 78 and got["head_top"] is not None
    info_in, info_out = behind._probe(str(src)), behind._probe(str(out))
    assert (info_out["w"], info_out["h"]) == (720, 1280) and abs(info_out["duration"] - info_in["duration"]) < 0.05
    assert _audio_md5(src) == _audio_md5(out)          # the sound is copied byte for byte
    x, y = got["xy"]
    mid = _frame(out, 1.4)[y:y + 120, :] - _frame(src, 1.4)[y:y + 120, :]
    assert np.abs(mid).mean() > 8                      # the word is there in the window
    after = np.abs(_frame(out, 3.6) - _frame(src, 3.6)).mean()
    assert after < 3                                   # and gone after it (only re-encoding noise)


# ---------------------------------------------------------------------- REST + MCP
@pytest.fixture
def take_clip(human, app, monkeypatch):
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
        c["status"], c["hook"] = "ready", "We shipped it in two days"
        c["meta"] = {"layout_plan": [{"scene": 0, "start": 0, "end": 4, "layout": "track", "rule": "R3"}]}
    store.x("UPDATE agent_runs SET input=?, result=?, status='ready' WHERE id=?", (json.dumps(inp), json.dumps(res), t["id"]))
    return human, t["id"], res["clips"][0]["id"], rendered


def test_rest_needs_the_switch(take_clip, monkeypatch):
    human, rid, cid, rendered = take_clip
    monkeypatch.delenv("HQ_HOOK_BEHIND", raising=False)
    v = human.get(f"/api/takes/{rid}/clips/{cid}/layout").json()
    assert "hook_fx_choices" not in v                  # the UI shows no Hook row
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_fx": "behind"})
    assert r.status_code == 409 and rendered == []


def test_rest_sets_words_behind_and_rerenders(take_clip, app, monkeypatch):
    human, rid, cid, rendered = take_clip
    monkeypatch.setenv("HQ_HOOK_BEHIND", "1")
    v = human.get(f"/api/takes/{rid}/clips/{cid}/layout").json()
    assert v["hook_fx"] == "none" and v["hook_fx_choices"] == ["none", "behind"] and v["hook"]
    assert human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_fx": "glow"}).status_code == 400
    assert human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_fx": "behind", "hook_words": "launch"}).status_code == 400
    assert rendered == []
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_fx": "behind", "hook_words": "two days"})
    assert r.status_code == 200, r.text
    assert r.json()["hook_fx"] == "behind" and r.json()["hook_words"] == "two days" and rendered == [(cid,)]
    raw = json.loads(app.state.store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    c = next(x for x in raw["clips"] if x["id"] == cid)
    assert c["hook_fx"] == "behind" and c["hook_words"] == "two days"


def test_mcp_set_clip_layout_hook_fx(take_clip, monkeypatch):
    from .conftest import agent_token, mcp_call
    human, rid, cid, rendered = take_clip
    monkeypatch.setenv("HQ_HOOK_BEHIND", "1")
    tok = agent_token(human, cap=50)
    res, out = mcp_call(human, tok, "set_clip_layout", {"take_id": rid, "clip_id": cid, "hook_fx": "behind"})
    assert not res["isError"], out
    assert out["hook_fx"] == "behind" and rendered == [(cid,)]


def test_render_spec_applies_it_and_fails_open(monkeypatch):
    from studio import take, takes
    monkeypatch.setattr(takes, "ocr_clip", lambda src, spec, c: {})
    monkeypatch.setattr(takes, "source_captions", lambda src, c: None)
    monkeypatch.setattr(take, "render_clip", lambda *a, **k: (b"MP4", b"JPG", {"layout": "track"}))
    calls = []
    monkeypatch.setattr(behind, "on_clip", lambda data, hook, words, kit, start: (calls.append((hook, words, start)) or
                                                                                  (b"MP4+", {"on": True, "text": "X"})))
    spec = {"style": "take", "scenes": [], "src": {}, "kit": {"font": "anton"}, "params": {}}
    c = {"id": "c1", "words": [], "faces": [], "hook": "We shipped it", "hook_fx": "behind", "hook_words": "shipped"}
    monkeypatch.setenv("HQ_HOOK_BEHIND", "1")
    (cid, data, cover, meta), = list(takes.render_spec_clip("src.mp4", spec, c, None))
    assert data == b"MP4+" and meta["hook_fx"]["on"] and calls == [("We shipped it", "shipped", take.COVER_SECS)]
    monkeypatch.delenv("HQ_HOOK_BEHIND")
    (cid, data, cover, meta), = list(takes.render_spec_clip("src.mp4", spec, c, None))
    assert data == b"MP4" and meta["hook_fx"]["on"] is False
    (cid, data, cover, meta), = list(takes.render_spec_clip("src.mp4", spec, {**c, "hook_fx": "none"}, None))
    assert data == b"MP4" and "hook_fx" not in meta
