"""Owner, 6 Nov 2026 18:59 (take #91): a pasted link must be quick again, Instagram failed preflight on every clip, and
the clip cards lost "Layout & captions". Each test fails on main."""
import base64

from tests.studio.test_take_opus import YT, _analysis, _github, _hosts


def _landed(human, app, fake, monkeypatch, services=("ch_ig",)):
    from fastapi.testclient import TestClient
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    house = app.state.house
    monkeypatch.setattr(house.takes, "_channels", lambda a, ids: [c for c in house.studio.channels(a) if c["id"] in services])
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.delenv("SUBQ_API_KEY", raising=False)
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "best moments"}).json()
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt})
    an = _analysis()
    an["timings"] = {**(an.get("timings") or {}), "download": 12.5, "faces": 30.0}
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": an}).json()["ok"]
    rjid, _, _, rjt = sent[1]
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    base = {"type": "video", "width": 1080, "height": 1920, "duration": 40.0, "cover_offset_ms": 400, "layout": "track",
            "face_coverage": 0.98, "timings": {"face_sampling": 3.0, "encode": 20.0, "qa": 4.0, "renders": 1, "bad": "x"}}
    for c in rspec["clips"]:
        for cid in [c["id"], *c["variant_ids"]]:
            r = w.post(f"/api/worker/jobs/{rjid}/clips/{cid.replace(':', '%3A')}", headers={"x-job-token": rjt},
                       json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + cid.encode()).decode(),
                             "cover": base64.b64encode(b"\xff\xd8\xff c").decode(),
                             "meta": {**base, "face_safe": {"status": "ok", "max_cover": 0.0}}})
            assert r.json()["ok"], r.text
    assert w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {}}).json()["ok"]
    return t, sent, rspec


def test_a_link_take_shows_its_layout_dialog_and_save_re_renders_with_the_choice(human, app, fake, monkeypatch):
    t, sent, _ = _landed(human, app, fake, monkeypatch)
    tid = t["id"]
    clip = human.get(f"/api/takes/{tid}").json()["clips"][0]["id"]
    v = human.get(f"/api/takes/{tid}/clips/{clip}/layout").json()
    assert v["style"] == "opus" and v["layouts"] == ["auto", "track", "fit"] and v["caption_presets"]
    bad = human.post(f"/api/takes/{tid}/clips/{clip}/layout", json={"scenes": {"0": "split"}})
    assert bad.status_code == 400 and "Follow face" in bad.json()["error"]
    r = human.post(f"/api/takes/{tid}/clips/{clip}/layout", json={"scenes": {"0": "fit"}, "caption_preset": "lower_third"})
    assert r.status_code == 200, r.text  # main: 409 "Per-scene layouts apply to One take clips"
    assert r.json()["rerendering"] is True
    spec = sent[-1][1] if isinstance(sent[-1][1], dict) and sent[-1][1].get("clips") else None
    from studio.takes import opus_choices
    jid, _, _, jt = sent[-1]
    from fastapi.testclient import TestClient
    w = TestClient(app, base_url="https://hq.test")
    spec = w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()["spec"]
    c = next(x for x in spec["clips"] if x["id"] == clip)
    assert opus_choices(c) == ("fit", "minimal")


def test_the_shorts_render_uses_the_dialog_choices(monkeypatch, tmp_path):
    from studio import take, takes
    from studio.media import longform
    seen = {}

    def render_moment(src, c, words, **kw):
        seen.update(kw)
        open(kw["out"], "wb").write(b"mp4")
        return {"duration": 30.0, "width": 1080, "height": 1920, "captions": True, "burned_subs": False, "caption_problems": [],
                "face_safe": {"status": "ok"}, "reframe": {"mode": "pad"}, "timings": {"encode": 2.0, "renders": 1}}
    monkeypatch.setattr(longform, "render_moment", render_moment)
    monkeypatch.setattr(take, "frame_at", lambda v, t, out: open(out, "wb").write(b"jpg"))
    spec = {"style": "opus", "ratios": ["9:16"], "src": {"width": 1920, "height": 1080}, "kit": {}, "credit": "Video: X"}
    c = {"id": "c1", "start": 1.0, "end": 31.0, "hook": "h", "words": [], "faces": [],
         "layout_overrides": {"0": {"layout": "fit", "at": 15.0}}, "caption_preset": "typewriter"}
    out = list(takes.render_spec_clip("src.mp4", spec, c, None))
    assert seen["framing"] == "fit" and seen["captions_style"] == "mono"
    meta = out[0][3]
    assert meta["framing"] == "fit" and meta["timings"]["encode"] == 2.0 and "clip_total" in meta["timings"]


def test_the_take_reports_its_stage_timings(human, app, fake, monkeypatch):
    t, _, _ = _landed(human, app, fake, monkeypatch)
    tm = human.get(f"/api/takes/{t['id']}").json()["timings"]
    assert tm["download"] == 12.5 and tm["faces"] == 30.0 and "moments" in tm and "preflight" in tm and "render_job" in tm
    row = tm["clips"]["c1"]
    assert row["encode"] == 20.0 and row["renders"] == 1 and "upload_storage" in row and "bad" not in row
    assert "end_to_end" in tm


def test_a_flagged_4x5_version_flags_the_clip(human, app, fake, monkeypatch):
    from fastapi.testclient import TestClient
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    house = app.state.house
    monkeypatch.setattr(house.takes, "_channels", lambda a, ids: [c for c in house.studio.channels(a) if c["id"] in ("ch_ig", "ch_li")])
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "best moments"}).json()
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt})
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": _analysis()}).json()["ok"]
    rjid, _, _, rjt = sent[1]
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    c = rspec["clips"][0]
    if not c["variant_ids"]:
        import pytest
        pytest.skip("no 4:5 channel in the fake account")
    base = {"type": "video", "width": 1080, "height": 1920, "duration": 40.0, "cover_offset_ms": 400, "layout": "track"}
    for cid, fs in ((c["id"], {"status": "ok", "max_cover": 0.0}),
                    (c["variant_ids"][0], {"status": "needs_review", "max_cover": 0.48, "reason": "text still covers 48.0% of the speaker's face"})):
        w.post(f"/api/worker/jobs/{rjid}/clips/{cid.replace(':', '%3A')}", headers={"x-job-token": rjt},
               json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42").decode(), "cover": base64.b64encode(b"\xff\xd8\xff").decode(),
                     "meta": {**base, "face_safe": fs}})
    got = next(x for x in human.get(f"/api/takes/{t['id']}").json()["clips"] if x["id"] == c["id"])
    assert got["status"] == "needs_review" and got["review_reason"].startswith("4:5 version")


def test_instagram_audio_is_encoded_at_its_128_kbps_spec():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "studio" / "media" / "compose.py").read_text()
    assert '"-b:a", "128k"' in src and '"-b:a", "160k"' not in src  # 160k measured 142-153 kbps: Instagram warned on every clip


def test_the_clip_card_offers_layout_on_link_takes_and_the_sheet_never_shows_a_bare_error():
    from pathlib import Path
    st = Path(__file__).resolve().parents[2] / "studio" / "static"
    js = (st / "take.js").read_text()
    assert 'T.take.style === "take" && !T.take.sample ? `<button type="button" class="btn2 sm" data-act="layout"' not in js
    assert 'T.take && !T.take.sample ? `<button type="button" class="btn2 sm" data-act="layout"' in js
    tl = (st / "takelayout.js").read_text()
    assert 'tlErr").textContent' not in tl and "Saving the layout" in tl and 'id="tlErr" role="alert" data-testid="layout-error" hidden' in tl
    assert ".st.err:empty{display:none}" in (st / "style.css").read_text()


def test_link_takes_render_clips_in_parallel_where_the_box_allows(monkeypatch):
    from studio import render_worker
    monkeypatch.delenv("HQ_RENDER_PARALLEL", raising=False)
    assert render_worker.render_parallel({"style": "take"}) == 1
    monkeypatch.setattr(render_worker.os, "cpu_count", lambda: 4)
    assert 1 <= render_worker.render_parallel({"style": "opus"}) <= 2
    monkeypatch.setenv("HQ_RENDER_PARALLEL", "3")
    assert render_worker.render_parallel({"style": "opus"}) == 3
