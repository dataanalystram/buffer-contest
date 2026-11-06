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


# ---------------------------------------------------------------- one render per clip (owner, 6 Nov 2026 18:59: take #91 rendered
# every clip twice with the same cover, 2 x 5 clips x 2 ratios, ~12 min)
def _fake_qa(cover):
    return {"ok": cover <= facesafe.QA_MAX, "max_cover": cover, "threshold": facesafe.QA_MAX, "faces_by": ["YuNet"],
            "frames": [{"t": 1.0, "faces": [[400, 300, 700, 700]], "cover": cover}], "frames_dir": "/nonexistent"}


def _fs_render(monkeypatch, tmp_path, sig_same: bool):
    from studio.media import compose, longform
    encodes = []

    def render_style(source, style, *, out, face_safe=None, skip_sig=None, **kw):
        sig = "same" if sig_same or skip_sig is None else "moved"
        if skip_sig and sig == skip_sig:
            return {"skipped": True, "path": None, "face_safe": {"sig": sig, "level": "compact"}}
        encodes.append(out)
        open(out, "wb").write(b"mp4")
        return {"path": out, "face_safe": {"sig": sig, "level": "full", "predicted_cover": 0.0}, "cover_frame_secs": 0,
                "cover_secs": 0, "timings": {"encode": 1.0}}
    monkeypatch.setattr(compose, "render_style", render_style)
    monkeypatch.setattr(facesafe, "clip_samples", lambda *a, **k: ([{"t": 1.0, "faces": [{"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.35}]}],
                                                                   {"detector": "YuNet", "fallback": None, "cache": "miss"}))
    monkeypatch.setattr(facesafe, "qa_measure", lambda *a, **k: _fake_qa(0.154))
    monkeypatch.setattr(facesafe, "qa_jpegs", lambda qa, **k: [])
    m = {"start": 10.0, "end": 40.0, "hook": "A hook"}
    plan = {"mode": "center", "crop_w": 608, "keys": [(0.0, 0)]}
    rec = longform._face_safe_render("src.mp4", m, plan, (1920, 1080), "9:16", [], [], False,
                                     {"out": str(tmp_path / "c1.mp4"), "ratio": "9:16"})
    return rec, encodes


def test_a_failed_qa_whose_re_place_changes_nothing_encodes_once(monkeypatch, tmp_path):
    rec, encodes = _fs_render(monkeypatch, tmp_path, sig_same=True)
    assert len(encodes) == 1 and rec["renders"] == 1  # main encoded the same placement twice (2 encodes, same 15.4 %)
    assert rec["status"] == "needs_review" and "15.4%" in rec["reason"] and "wasn't rendered again" in rec["reason"]
    assert rec["replace"].startswith("skipped") and rec["timings"]["renders"] == 1
    assert not (tmp_path / "c1.mp4.replace.mp4").exists()


def test_a_re_place_that_moves_the_text_renders_to_a_side_file_and_keeps_the_better_one(monkeypatch, tmp_path):
    covers = iter([0.154, 0.01])
    monkeypatch.setattr(facesafe, "qa_measure", lambda *a, **k: _fake_qa(next(covers)))
    from studio.media import longform
    real = longform._face_safe_render

    def run():
        return real("src.mp4", {"start": 10.0, "end": 40.0, "hook": "A hook"}, {"mode": "center", "crop_w": 608, "keys": [(0.0, 0)]},
                    (1920, 1080), "9:16", [], [], False, {"out": str(tmp_path / "c1.mp4"), "ratio": "9:16"})
    from studio.media import compose
    encodes = []

    def render_style(source, style, *, out, face_safe=None, skip_sig=None, **kw):
        encodes.append((out, face_safe.get("min_level")))
        open(out, "wb").write(out.encode())
        return {"path": out, "face_safe": {"sig": "s%d" % len(encodes), "level": "full" if len(encodes) == 1 else "compact"},
                "cover_frame_secs": 0, "cover_secs": 0}
    monkeypatch.setattr(compose, "render_style", render_style)
    monkeypatch.setattr(facesafe, "clip_samples", lambda *a, **k: ([], {"detector": "YuNet", "fallback": None}))
    monkeypatch.setattr(facesafe, "qa_jpegs", lambda qa, **k: [])
    rec = run()
    assert [e[1] for e in encodes] == [None, 1] and encodes[1][0].endswith(".replace.mp4")  # one level up, a side file
    assert rec["status"] == "ok" and rec["kept"] == "re-placed" and rec["max_cover"] == 0.01 and rec["renders"] == 2
    assert (tmp_path / "c1.mp4").read_bytes().endswith(b".replace.mp4") and not (tmp_path / "c1.mp4.replace.mp4").exists()


def test_a_face_filling_the_4x5_frame_gets_the_compact_caption_level_clear_of_it():
    from studio.media import captions, compose
    W, H = compose.SIZES["4:5"]
    brand = dict(compose.DEFAULT_BRAND)
    L = compose.layout("opus", W, H, 1920, 1080, brand, "Why every model needs a face check", True)
    face = (300, 40, 780, 1060)                            # the speaker fills the 4:5 frame almost to the bottom
    head = (250, 0, 830, 1190)
    zones = {"static": head, "segments": [{"start": 0.0, "end": 8.0, "keepout": head}],
             "pred": [(t / 2, face) for t in range(16)]}
    cfg = {"captions": "opus", "layout": "opus"}
    L2, rec = compose._face_safe_layout(L, W, H, brand, "Why every model needs a face check", "Video: X on YouTube", True, cfg, zones)
    assert rec["levels"]["full"] > facesafe.QA_MAX       # the full two-line block can't fit under the chin
    assert rec["level"] == "compact" and rec["predicted_cover"] <= facesafe.QA_MAX
    st = L2["_cap_style"]
    assert st.max_lines == 1 and abs(st.size_frac - captions.STYLES["opus"].size_frac * facesafe.COMPACT_SIZE) < 1e-9
    assert 60 <= rec["cap_h"] <= 80                        # ~70 px tall at 4:5
    assert all(cy - rec["cap_h"] // 2 >= face[3] for _, _, cy in L2["caption_cy"])  # under the chin


def test_the_yunet_samples_are_sampled_once_per_clip_and_reused_by_the_next_ratio(monkeypatch, tmp_path):
    from studio.media.reframe import faces as fm
    calls = []
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")

    def sample(source, start, end, w, h, fps=2.0):
        calls.append((start, end))
        return {"detector": "YuNet", "samples": [{"t": 0.0, "faces": [{"x": 0.4, "y": 0.2, "w": 0.2, "h": 0.3, "score": 0.9}]}]}
    monkeypatch.setattr(fm, "sample", sample)
    facesafe._SAMPLES.clear()
    a, ra = facesafe.clip_samples(str(src), 1.0, 9.0, 1920, 1080, [{"t": 2.0, "cx": 0.5, "cy": 0.4, "w": 0.2, "src": "jev"}])
    b, rb = facesafe.clip_samples(str(src), 1.0, 9.0, 1920, 1080, None)
    assert len(calls) == 1 and ra["cache"] == "miss" and rb["cache"] == "hit"
    assert len(a[0]["faces"]) == 1 and len(b[0]["faces"]) == 1  # the Jev box added to the 9:16 copy never leaks into the cache
    a[0]["faces"].clear()
    c, _ = facesafe.clip_samples(str(src), 1.0, 9.0, 1920, 1080, None)
    assert len(c[0]["faces"]) == 1 and len(calls) == 1


def test_predict_cover_counts_the_union_of_the_overlays_once():
    L = {"safe": (40, 100, 1040, 1800), "hook": (100, 200, 980, 300), "caption_cy": [(0.0, 5.0, 250)], "credit_xy": None}
    face = (100, 190, 980, 310)  # under the hook box and the caption block at once: covered once, never more than all of it
    assert facesafe.predict_cover(L, [(1.0, face)], 1080, 1920, cap_h=100, captions_on=True, pad=0.0) == 1.0
    L["caption_cy"] = [(0.0, 5.0, 1500)]
    L["hook"] = None
    assert facesafe.predict_cover(L, [(1.0, face)], 1080, 1920, cap_h=100, captions_on=True, pad=0.0) == 0.0
