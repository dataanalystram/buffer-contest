"""Face-safe overlays (owner bug, 6 Nov 2026: on take 77 the hook box covered the speaker's eyes and the captions sat on
his chin, because the Opus layout used fixed positions and never looked at the faces).

Covers the placement geometry (after the reframe crop and the 1.12 punch-in zoom), the main path's tracked captions,
OCR boxes limited to the visible crop with noise dropped (a shirt pattern can't push captions onto the face), the
server's final look (Jev first, SubQ tie-break, daily allowance, frame-hash cache, YuNet fallback) and the clip landing
(needs_review, never flipped back to ready by preflight)."""
import base64
import io

import pytest

from studio import face_qa
from studio.media import captions_ass, facesafe, jev_faces, ocr
from studio.media.reframe import take_clip

W, H = 1080, 1920


# ---------------------------------------------------------------- geometry: reframe + punch-in
def test_head_box_grows_the_face_to_the_head():
    x0, y0, x1, y1 = facesafe.head_box({"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.3})
    assert x0 < 0.4 and x1 > 0.6 and y0 == pytest.approx(0.2 - 0.3 * facesafe.HEAD_UP) and y1 > 0.5


def test_view_at_maps_a_source_point_through_the_tracked_crop():
    plan = {"mode": "track", "crop_w": 540, "keys": [(0.0, 690)]}
    f = facesafe.view_at(plan, 0.0, 1920, 960, W, H)
    assert f(690, 0) == (0.0, 0.0) and f(690 + 540, 960) == (W, H)


def test_the_punch_in_zoom_pushes_a_point_below_its_centre_further_down():
    plan = {"mode": "track", "crop_w": 540, "keys": [(0.0, 690)]}
    flat = facesafe.view_at(plan, 0.0, 1920, 960, W, H)
    zoom = facesafe.view_at(plan, 0.0, 1920, 960, W, H, zoom=(1.12, 0.4))
    cx, cy = 690 + 270, 0.4 * 960
    assert zoom(cx, cy)[1] == pytest.approx(flat(cx, cy)[1])            # the zoom centre stays put
    assert zoom(cx, 0.8 * 960)[1] > flat(cx, 0.8 * 960)[1] + 50           # the chin moves down at 1.12


def test_out_boxes_puts_a_tracked_face_in_output_pixels():
    plan = {"mode": "track", "crop_w": 540, "keys": [(0.0, 690)]}
    s = [{"t": 1.0, "faces": [{"x": 0.42, "y": 0.15, "w": 0.15, "h": 0.4}]}]
    (_, (x0, y0, x1, y1)), = facesafe.out_boxes(s, plan, 1920, 960, W, H)
    assert 0 <= x0 < x1 <= W and 0 <= y0 < 40 and 1000 < y1 < 1250


def test_a_single_stray_detection_does_not_empty_the_frame():
    boxes = [(300, 200, 800, 900)] * 19 + [(0, 1400, 200, 1700)]
    z = facesafe.zone(boxes, W, H, occ=0.10)
    assert z[3] < 1000 and z[1] < 200


# ---------------------------------------------------------------- Opus layout
def _hook_fit(text, w, h):
    return h >= 90, 90


def test_take_77_shape_hook_and_captions_keep_off_the_face():
    """Talking head filling the top two thirds (take 77): the hook must not sit on the eyes, captions not on the chin."""
    L = {"safe": (60, 230, 930, 1500), "hook": (60, 230, 930, 518), "captions_center": 1152}
    keep = (0, 0, 1065, 1228)
    L2, rec = facesafe.place_opus(dict(L), W, H, static=keep, segments=[{"start": 0, "end": 4, "keepout": keep}], cap_h=240,
                                  hook="I want to share with you the top mistakes", hook_fit=_hook_fit, credit_h=46,
                                  captions_on=True)
    if L2["hook"]:
        assert not facesafe.hits((L2["hook"][0] - 10, L2["hook"][1] - 10, L2["hook"][2] + 10, L2["hook"][3] + 10), keep)
    else:
        assert rec["hook"] == "dropped" and any("hook" in n for n in rec["notes"])
    cy = L2["caption_cy"][0][2]
    assert cy - 120 >= keep[3]                                            # under the chin
    # the fixed layout (before the fix) put both on the face
    assert facesafe.hits(L["hook"], keep) and facesafe.hits((60, 1152 - 120, 930, 1152 + 120), keep)


def test_a_face_low_in_frame_keeps_the_hook_at_the_top():
    L = {"safe": (60, 230, 930, 1500), "hook": (60, 230, 930, 518), "captions_center": 1152}
    keep = (300, 700, 800, 1100)
    L2, rec = facesafe.place_opus(dict(L), W, H, static=keep, segments=[{"start": 0, "end": 4, "keepout": keep}], cap_h=240,
                                  hook="Hook", hook_fit=_hook_fit, credit_h=46, captions_on=True)
    assert rec["hook"] == "default" and L2["hook"] == L["hook"]
    assert not facesafe.hits((60, L2["caption_cy"][0][2] - 120, 930, L2["caption_cy"][0][2] + 120), keep)


def test_captions_go_above_the_head_when_the_chin_leaves_no_room():
    cy, where = facesafe.caption_center((0, 800, W, 1500), (60, 230, 930, 1500), 240, 1152)
    assert where == "above_face" and cy + 120 <= 800


def test_the_face_qa_mask_measures_what_we_drew():
    np = pytest.importorskip("numpy")
    m = np.zeros((H, W), np.uint8)
    m[200:400, 0:W] = 1                                                   # a hook box band over the top of a face
    assert facesafe.face_cover(m, (300, 300, 700, 800), pad=0.0) == pytest.approx(0.2)
    assert facesafe.face_cover(m, (300, 900, 700, 1300)) == 0.0


# ---------------------------------------------------------------- main take path: tracked captions after punch-in
def _shot(cy, h, punch=None, crop_h=1080):
    s = {"start": 0.0, "end": 5.0, "layout": "track", "path": {"crop_w": 608, "crop_h": crop_h, "keys": [(0.0, 600, 0)]},
         "face_med": {"w": 0.2, "h": h, "cy": cy}}
    if punch:
        s["punch"] = {"windows": [(0.0, 2.0)], "peak": punch, "cy": cy}
    return s


def test_tracked_captions_move_under_the_chin():
    side = [{"start": 0.0, "end": 5.0, "layout": "track"}]
    rec = facesafe.place_track_captions(side, [_shot(0.5, 0.25)], 1920, 1080)
    (a, b), = facesafe.track_face_spans(side[0], [_shot(0.5, 0.25)], 1920, 1080)
    y = side[0]["caption_y"]
    assert rec["moved_for_face"] == 1 and not rec["blocked"] and y - 85 >= b
    assert captions_ass.band_y(side[0]) == y


def test_the_punch_in_zoom_counts_for_the_tracked_face():
    flat = facesafe.track_face_spans({"start": 0, "end": 5}, [_shot(0.5, 0.25)], 1920, 1080)[0]
    zoomed = facesafe.track_face_spans({"start": 0, "end": 5}, [_shot(0.5, 0.25, punch=1.12)], 1920, 1080)[0]
    assert zoomed[1] > flat[1] + 30 and zoomed[0] < flat[0]
    side = [{"start": 0.0, "end": 5.0, "layout": "track"}]
    rec = facesafe.place_track_captions(side, [_shot(0.5, 0.25, punch=1.12)], 1920, 1080)
    y = side[0].get("caption_y", captions_ass.band_y(side[0]))
    assert rec["blocked"] or (y - 85 >= zoomed[1] or y + 85 <= zoomed[0])  # never on the zoomed face


def test_a_face_filling_the_frame_is_flagged_not_silently_covered():
    side = [{"start": 0.0, "end": 5.0, "layout": "track"}]
    rec = facesafe.place_track_captions(side, [_shot(0.55, 0.5)], 1920, 1080)
    assert len(rec["blocked"]) == 1
    qa = facesafe.main_qa("/nonexistent.mp4", side, rec)
    assert qa["status"] == "needs_review" and "face" in qa["reason"] and qa["faces_by"][0].startswith("none")


def test_main_qa_never_reports_an_unmeasured_cover_as_clear_and_samples_the_whole_clip(monkeypatch):
    side = [{"start": float(i), "end": float(i + 1), "layout": "track"} for i in range(30)]
    qa = facesafe.main_qa("/nonexistent.mp4", side, {"blocked": []})
    assert qa["max_cover"] is None and qa["status"] == "needs_review" and "couldn't check" in qa["reason"]
    assert face_qa.fallback({"max_cover": qa["max_cover"]})["verdict"] == "unsettled"   # not "clear"
    from PIL import Image
    seen = []

    def frame(video, t, width=768):
        seen.append(t)
        if t > 25:
            raise RuntimeError("late frame failed")
        return Image.new("RGB", (W, H))
    monkeypatch.setattr(jev_faces, "frame_image", frame)
    monkeypatch.setattr(facesafe, "detect_out", lambda img: [(300, 1200, 700, 1700)])   # a face low in the frame
    qa = facesafe.main_qa("x.mp4", side, {"blocked": []})
    assert max(seen) > 25 and len(seen) <= facesafe.MAIN_QA_FRAMES + 2                  # the late segments are looked at
    assert qa["max_cover"] > facesafe.QA_MAX and qa["status"] == "needs_review"        # the partial measure is kept


def test_other_layouts_keep_their_caption_line():
    side = [{"start": 0.0, "end": 5.0, "layout": "split"}]
    rec = facesafe.place_track_captions(side, [_shot(0.5, 0.25)], 1920, 1080)
    assert "caption_y" not in side[0] and not rec["blocked"]


# ---------------------------------------------------------------- OCR boxes (bug 3): visible crop, no noise
def _band_box(**kw):
    default = captions_ass.band_y({"layout": "track"})
    return {"x": 0.4, "y": (default - 40) / 1920, "w": 0.1, "h": 80 / 1920, "text": "SALE", "conf": 90, **kw}


def test_a_shirt_pattern_read_as_text_does_not_move_the_captions():
    seg = {"start": 0, "end": 5, "layout": "track"}
    default = captions_ass.band_y(seg)
    for noise in ({"text": "lll", "conf": 62}, {"text": "~|/", "conf": 91}, {"text": "MW", "conf": 64}, {"h": 0.4}):
        assert ocr.free_caption_y([{"t": 1.0, "boxes": [_band_box(**noise)]}], seg, default, 1920, 1080) is None, noise
    assert ocr.free_caption_y([{"t": 1.0, "boxes": [_band_box()]}], seg, default, 1920, 1080) is not None  # real text


def test_text_outside_the_visible_crop_is_ignored():
    shots = [{"start": 0, "end": 5, "layout": "track", "path": {"crop_w": 608, "crop_h": 1080, "keys": [(0.0, 600, 0)]}}]
    seg = {"start": 0, "end": 5, "layout": "track"}
    default = captions_ass.band_y(seg)
    off = [{"t": 1.0, "boxes": [_band_box(x=0.05)]}]                     # x 96-288 px: left of the 600-1208 crop
    on = [{"t": 1.0, "boxes": [_band_box(x=0.4)]}]                       # x 768-960 px: inside
    kw = {"crop_at": take_clip.crop_at_fn(shots), "crop_x": take_clip.crop_x_fn(shots)}
    assert take_clip.crop_x_fn(shots)(1.0) == (600.0, 608.0)
    assert ocr.free_caption_y(off, seg, default, 1920, 1080, **kw) is None
    assert ocr.free_caption_y(on, seg, default, 1920, 1080, **kw) is not None


def test_text_and_face_together_never_put_the_captions_on_the_face():
    seg = {"start": 0, "end": 5, "layout": "track"}
    default = captions_ass.band_y(seg)
    face = [(300, 1100)]
    box = _band_box()
    y = ocr.free_caption_y([{"t": 1.0, "boxes": [box]}], seg, default, 1920, 1080, avoid=face)
    text = (box["y"] * 1920 - 12, (box["y"] + box["h"]) * 1920 + 12)
    assert y is not None and y - 85 >= 1100 and (y + 85 <= text[0] or y - 85 >= text[1])  # under the chin, off the text


# ---------------------------------------------------------------- Jev boxes keep their height
def test_jev_merge_keeps_the_box_height():
    res = [{"t": 1.0, "verdict": "face", "face_visible": 0.9, "box": {"left": 0.4, "right": 0.6, "top": 0.1, "bottom": 0.55}}]
    got = jev_faces.merge([], res)
    assert got and all(f["h"] == pytest.approx(0.45) for f in got)
    box = facesafe.from_center(got[0], 1920, 1080)
    assert box["h"] == pytest.approx(0.45)                                # not re-estimated from the width


# ---------------------------------------------------------------- the server's final look
def _jpeg(color=(200, 50, 50), size=(540, 960), exif=False):
    from PIL import Image
    b = io.BytesIO()
    im = Image.new("RGB", size, color)
    if exif:
        ex = Image.Exif()
        ex[0x010F] = "SecretCam"
        im.save(b, "JPEG", exif=ex)
    else:
        im.save(b, "JPEG")
    return base64.b64encode(b.getvalue()).decode()


def test_clean_keeps_real_jpegs_rebuilt_without_metadata_and_drops_the_rest():
    frames = [{"t": 1.0, "jpeg": _jpeg(exif=True)}, {"t": "x", "jpeg": _jpeg()}, {"t": 2.0, "jpeg": "bm90IGEganBlZw=="},
              {"t": True, "jpeg": _jpeg()}, "junk"]
    got = face_qa.clean(frames, cap=5)
    assert [f["t"] for f in got] == [1.0] and b"SecretCam" not in got[0]["jpeg"]
    assert face_qa.clean("junk") == [] and len(face_qa.clean([{"t": 1, "jpeg": _jpeg()}] * 5, cap=5)) == face_qa.MAX_FRAMES


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-test-key")
    monkeypatch.setenv("SUBQ_API_KEY", "dummy-test-key")
    monkeypatch.setattr(face_qa, "_CACHE", {})
    calls = []

    def fake(answers):
        def ask(provider, model, jpeg, http=None):
            calls.append((provider[0], model))
            v = answers[provider[0]]
            if isinstance(v, Exception):
                raise v
            return v
        monkeypatch.setattr(face_qa, "_ask", ask)
    return calls, fake


def _frames(n=1, c=(10, 20, 30)):
    return face_qa.clean([{"t": float(i), "jpeg": _jpeg((c[0] + i * 40, c[1], c[2]))} for i in range(n)])


def test_jev_answers_first_and_subq_is_not_asked_when_jev_is_sure(keys):
    calls, fake = keys
    fake({"jev": 0.05, "subq": 0.99})
    r = face_qa.judge(_frames(), 4, log=lambda m: None)
    assert r["verdict"] == "clear" and r["by"] == ["jev"] and calls == [("jev", "jev-latest")] and r["calls"] == 1


def test_subq_breaks_the_tie_when_jev_is_unsettled(keys):
    calls, fake = keys
    fake({"jev": 0.5, "subq": 0.9})
    r = face_qa.judge(_frames(), 4, log=lambda m: None)
    assert r["verdict"] == "covered" and r["by"] == ["jev", "subq"] and calls[1] == ("subq", "subq-snap-1.1")
    assert face_qa.needs_review({"status": "ok"}, r) == "Jev/SubQ saw text covering the speaker's face"


def test_the_daily_allowance_caps_the_calls_and_the_cache_makes_a_recheck_free(keys):
    calls, fake = keys
    fake({"jev": 0.5, "subq": 0.1})
    r = face_qa.judge(_frames(2), 1, log=lambda m: None)
    assert r["calls"] == 1 and len(calls) == 1                             # one call allowed, one spent
    calls.clear()
    fake({"jev": 0.05, "subq": 0.05})
    first = face_qa.judge(_frames(1, (99, 1, 1)), 4, log=lambda m: None)
    again = face_qa.judge(_frames(1, (99, 1, 1)), 4, log=lambda m: None)
    assert first["calls"] == 1 and again["calls"] == 0 and again["frames"][0]["cached"] and again["verdict"] == "clear"


def test_no_model_falls_back_to_the_worker_yunet_measure_and_says_so(keys, monkeypatch):
    calls, fake = keys
    fake({"jev": RuntimeError("HTTP 500"), "subq": RuntimeError("HTTP 503")})
    logged = []
    r = face_qa.judge(_frames(), 4, worker={"max_cover": 0.31}, log=logged.append)
    assert not r["ran"] and r["fallback"]["by"] == "YuNet (worker)" and r["verdict"] == "covered" and logged
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.delenv("SUBQ_API_KEY")
    r = face_qa.judge(_frames(), 4, worker={"max_cover": 0.0}, log=logged.append)
    assert "neither" in r["why"] and r["verdict"] == "clear" and r["calls"] == 0
    r = face_qa.judge(_frames(), 0, worker={"max_cover": "x"}, log=logged.append, off="off for this take")
    assert r["why"] == "off for this take" and r["verdict"] == "unsettled"


def test_jev_model_pins_only_known_versions(monkeypatch):
    monkeypatch.setenv("HQ_FACE_QA_MODEL", "jev-1.14.2")
    assert face_qa.jev_model() == "jev-1.14.2"
    for bad in ("jev-1.9", "gpt-4o", "jev-latest; rm -rf"):
        monkeypatch.setenv("HQ_FACE_QA_MODEL", bad)
        assert face_qa.jev_model() == "jev-latest"


def test_ask_never_leaks_the_key_in_an_error(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy-secret-123")
    from studio import netguard

    def boom(*a, **k):
        raise netguard.FetchError("dummy-secret-123 refused")
    monkeypatch.setattr(netguard, "safe_post", boom)
    with pytest.raises(RuntimeError) as e:
        face_qa._ask(face_qa.JEV, "jev-latest", b"\xff\xd8x")
    assert "dummy-secret-123" not in str(e.value) and str(e.value) == "FetchError"


# ---------------------------------------------------------------- the clip landing
def test_a_clip_with_text_on_the_face_lands_as_needs_review_and_preflight_keeps_it(human, app, fake, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.studio.test_take_opus import YT, _analysis, _github, _hosts
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    house = app.state.house
    monkeypatch.setattr(house.takes, "_channels", lambda a, ids: [c for c in house.studio.channels(a) if c["id"] == "ch_ig"])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    stored = []
    put = house.takes.h.put_media

    def spy(a, data, name, kind="render", meta=None):
        stored.append(meta or {})
        return put(a, data, name, kind=kind, meta=meta)
    monkeypatch.setattr(house.takes.h, "put_media", spy)
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "best moments"}).json()
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt})
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": _analysis()}).json()["ok"]
    rjid, _, _, rjt = sent[1]
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    base = {"type": "video", "width": 1080, "height": 1920, "duration": 40.0, "cover_offset_ms": 400, "layout": "track",
            "face_coverage": 0.98}
    for k, c in enumerate(rspec["clips"]):
        fs = {"status": "ok", "max_cover": 0.31 if k == 0 else 0.0, "qa_jpegs": [{"t": 1.0, "jpeg": _jpeg()}]}
        for cid in [c["id"], *c["variant_ids"]]:
            r = w.post(f"/api/worker/jobs/{rjid}/clips/{cid.replace(':', '%3A')}", headers={"x-job-token": rjt},
                       json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + cid.encode()).decode(),
                             "cover": base64.b64encode(b"\xff\xd8\xff c").decode(), "meta": {**base, "face_safe": dict(fs)}})
            assert r.json()["ok"], r.text
    assert w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {}}).json()["ok"]
    t = human.get(f"/api/takes/{t['id']}").json()
    first, rest = t["clips"][0], t["clips"][1:]
    assert first["status"] == "needs_review" and "face" in first["review_reason"]
    assert rest and all(c["status"] == "ready" for c in rest)
    look = first["face_safe"]["final_look"]
    assert look["fallback"]["by"] == "YuNet (worker)" and not look["ran"] and first["face_safe"]["max_cover"] == 0.31
    assert stored and all("qa_jpegs" not in (m.get("face_safe") or {}) for m in stored)  # the frames are never stored
    dry = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True, "week_of": "next"}).json()
    assert first["id"] not in {p["clip_id"] for p in dry["plan"]["placements"]}


def test_the_take_page_labels_a_clip_that_needs_review():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "studio" / "static" / "take.js").read_text()
    assert 'c.status === "needs_review"' in js and "Needs review" in js and "clip-review-reason" in js
