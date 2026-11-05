"""Opus-style takes: "split this into 5 shorts <YouTube link>" from Make a post or One take. Platform links need the
person's rights confirmation and a credit; existing captions are reused instead of Whisper; each clip is rendered in
every ratio its channels need and placement picks the right one; the worker protocol accepts the extra ratios."""
import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import takes
from studio.media import subtitles as S

VTT = (Path(__file__).with_name("fixtures") / "nasa_grc_2016.vtt").read_text()
YT = "https://www.youtube.com/watch?v=QJf0H64BnFo"


def _hosts(monkeypatch):
    from studio import drafter
    monkeypatch.setattr(drafter, "_public", lambda h: h not in ("127.0.0.1", "localhost"))


def _github(app, monkeypatch):
    house = app.state.house
    sent = []

    def dispatch(jid, workflow="render.yml", inputs=None, ttl=1800):
        sent.append((jid, workflow, inputs, house.issue_job_token(jid, ttl)))
        return True, ""
    monkeypatch.setattr(house, "_dispatch_github", dispatch)
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    monkeypatch.setattr("studio.media.find.oembed", lambda url, http=None: {"author": "Buffer", "provider": "YouTube"})
    return sent


def test_a_platform_link_needs_rights_and_gets_a_credit(human, app, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    r = human.post("/api/takes", json={"url": YT})
    assert r.status_code == 400 and "rights_confirmed" in r.json()["error"]
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "split this into 5 shorts"}).json()
    assert t["style"] == "opus" and t["input"]["platform_url"] == YT and t["input"]["url"] == ""
    assert t["input"]["credit"] == "Video: Buffer on YouTube" and t["input"]["count"] == 5
    assert t["input"]["ratios"] == ["9:16"]                                     # ch_ig only takes video here -> Reel
    jid, wf, inputs, jt = sent[0]
    assert wf == "take.yml" and inputs == {"kind": "take_analyze"}
    w = TestClient(app, base_url="https://hq.test")
    spec = w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}).json()
    assert spec["spec"]["platform_url"] == YT and spec["urls"]["source"] == ""   # the worker fetches the post itself
    # a profile page is still refused; a direct file still works for your own video
    r = human.post("/api/takes", json={"url": "https://www.facebook.com/somebrand", "owns": True, "rights_attested": True})
    assert r.status_code == 400 and "video post itself" in r.json()["error"]


def test_uploaded_captions_are_checked_before_parsing(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    r = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4", "owns": True, "rights_attested": True, "captions": "not captions"})
    assert r.status_code == 400 and "SRT or WebVTT" in r.json()["error"]
    r = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4", "owns": True, "rights_attested": True, "captions": "x" * (takes.MAX_CAPTION_TEXT + 1)})
    assert r.status_code == 413
    t = human.post("/api/takes", json={"url": "https://cdn.example.com/talk.mp4", "owns": True, "rights_attested": True, "captions": VTT, "style": "opus"}).json()
    assert t["input"]["captions_given"] is True and t["style"] == "opus"


def test_reuse_captions_skips_whisper_and_falls_back_when_there_are_none(monkeypatch):
    tr = takes.reuse_captions("missing.mp4", {"captions": VTT})
    assert tr["source"] == "uploaded" and tr["count"] > 300
    monkeypatch.setattr(S, "embedded_captions", lambda p: None)
    assert takes.reuse_captions("missing.mp4", {"captions": ""}) is None


def test_take_analyze_uses_given_captions_instead_of_whisper(monkeypatch):
    from studio import take
    monkeypatch.setattr(take, "probe", lambda p: {"duration": 185.0, "width": 1280, "height": 720, "audio": True, "codec": "h264"})
    monkeypatch.setattr(take, "detect_silences", lambda p: [])
    monkeypatch.setattr(take, "detect_scenes", lambda p: ([], "test"))
    monkeypatch.setattr(take, "face_track", lambda p, d: ([], "none (test)"))
    monkeypatch.setattr(take, "transcribe", lambda *a, **k: pytest.fail("Whisper must not run when captions exist"))
    monkeypatch.setattr("studio.mediasafe.check_source", lambda p, **k: {})
    monkeypatch.setattr("studio.media.compose.detect_burned_subs", lambda p, d: True)
    an = take.analyze("x.mp4", transcript={"words": S.parse_captions(VTT), "source": "uploaded", "detail": "your caption file"},
                      log=lambda *a: None)
    assert an["transcript_source"] == "uploaded" and "no Whisper run" in an["providers"]["stt"]
    assert an["burned_subs"] is True and an["face_detector"] is False


def _analysis():
    words = S.parse_captions(VTT)
    return {"probe": {"duration": 185.1, "width": 1280, "height": 720, "audio": True, "codec": "h264"}, "words": words,
            "language": "en", "silences": [], "scenes": [], "faces": [], "transcript_source": "uploaded", "burned_subs": False,
            "face_detector": True, "providers": {"stt": "reused captions"}, "timings": {"transcribe": 0}}


def test_opus_take_renders_every_ratio_and_placement_uses_the_channels_ratio(human, app, fake, monkeypatch):
    _hosts(monkeypatch)
    sent = _github(app, monkeypatch)
    house = app.state.house
    monkeypatch.setattr(house.takes, "_channels",
                        lambda a, ids: [c for c in house.studio.channels(a) if c["id"] in ("ch_ig", "ch_li")])
    t = human.post("/api/takes", json={"url": YT, "rights_confirmed": True, "rights_attested": True, "prompt": "find the best moments"}).json()
    assert t["input"]["ratios"] == ["9:16", "4:5"]
    jid, _, _, jt = sent[0]
    w = TestClient(app, base_url="https://hq.test")
    w.get(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt})
    assert w.post(f"/api/worker/jobs/{jid}", headers={"x-job-token": jt}, json={"analysis": _analysis()}).json()["ok"]
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["transcript_source"] == "uploaded" and t["picked_by"] == "built-in scorer"
    assert 3 <= len(t["clips"]) <= 4 and all(c["why"] and c["rank"] for c in t["clips"])
    rjid, _, inputs, rjt = sent[1]
    rspec = w.get(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}).json()["spec"]
    assert rspec["style"] == "opus" and rspec["ratios"] == ["9:16", "4:5"] and rspec["credit"] == "Video: Buffer on YouTube"
    assert rspec["clips"][0]["variant_ids"] == ["c1@4:5"]
    meta = {"type": "video", "width": 1080, "height": 1920, "duration": 40.0, "cover_offset_ms": 400, "layout": "track",
            "face_coverage": 0.98}
    for c in rspec["clips"]:
        for cid in [c["id"], *c["variant_ids"]]:
            r = w.post(f"/api/worker/jobs/{rjid}/clips/{cid.replace(':', '%3A')}", headers={"x-job-token": rjt},
                       json={"data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + cid.encode()).decode(),
                             "cover": base64.b64encode(b"\xff\xd8\xff c").decode(), "meta": meta})
            assert r.json()["ok"], r.text
        # each id is accepted once (the job token records it)
        again = w.post(f"/api/worker/jobs/{rjid}/clips/{c['id']}", headers={"x-job-token": rjt},
                       json={"data": "AA==", "cover": "AA==", "meta": meta})
        assert again.status_code == 403
    assert w.post(f"/api/worker/jobs/{rjid}", headers={"x-job-token": rjt}, json={"meta": {}}).json()["ok"]
    t = human.get(f"/api/takes/{t['id']}").json()
    assert t["status"] == "ready" and all(set(c["variants"]) == {"4:5"} for c in t["clips"])
    dry = human.post(f"/api/takes/{t['id']}/place", json={"dry_run": True, "week_of": "next"}).json()
    by = {(p["clip_id"], p["service"]): p["media_url"] for p in dry["plan"]["placements"]}
    for (cid, svc), url in by.items():
        clip = next(c for c in t["clips"] if c["id"] == cid)
        assert url == (clip["variants"]["4:5"] if svc == "linkedin" else clip["url"])


def test_make_a_post_split_request_becomes_a_take(human, app, monkeypatch):
    _hosts(monkeypatch)
    _github(app, monkeypatch)
    r = human.post("/api/agent/runs", json={"prompt": f"split this into 5 shorts {YT}", "channel_ids": ["ch_ig"]})
    assert r.status_code == 409 and r.json()["error"].startswith("Rights:") and "Video: Buffer on YouTube" in r.json()["error"]
    t = human.post("/api/agent/runs", json={"prompt": f"split this into 5 shorts {YT}", "channel_ids": ["ch_ig"],
                                            "rights_confirmed": True, "rights_attested": True}).json()
    assert t["kind"] == "take" and t["style"] == "opus" and t["input"]["count"] == 5
    # an ordinary post about a link is still a Make a post run
    run = human.post("/api/agent/runs", json={"prompt": "post about this https://example.com/launch", "channel_ids": ["ch_ig"]}).json()
    assert run["kind"] == "agent"


def test_ui_shows_why_each_moment_was_picked_and_asks_for_rights_inline():
    js = (Path(takes.__file__).parent / "static" / "take.js").read_text()
    assert 'data-testid="clip-why"' in js and 'data-testid="clip-variants"' in js and "take-captions-reused" in js
    hj = (Path(takes.__file__).parent / "static" / "house.js").read_text()
    assert "agent-rights-yes" in hj and "rights_confirmed: true" in hj and "window.openTake" in hj
    assert "window.confirm" not in hj
