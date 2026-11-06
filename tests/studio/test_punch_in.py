"""Punch-in: the gentle auto zoom on One take's face-tracked shots (owner ask, 5 Nov 2026).

1.0 -> at most 1.12, eased over 0.4 s, at scene starts and emphasised words, centred where the tracker keeps the face,
never so far that the face box leaves the frame; ffmpeg only (zoompan); a per-take switch, on by default, that renders
exactly as before when off. The ffmpeg tests use a short synthetic fixture (a white "face" square on grey)."""
import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from studio.media.reframe import punch, render as rr
from studio.media.reframe.decide import OUT_H, OUT_W, UPSCALE_MAX

FF = shutil.which("ffmpeg")
needs_ff = pytest.mark.skipif(not FF, reason="ffmpeg not installed")


def W(w, s, e):
    return {"w": w, "s": s, "e": e}


# ------------------------------------------------------------------ the plan
def test_emphasis_marks_pauses_numbers_and_exclamations():
    words = [W("So", 10.0, 10.2), W("we", 10.25, 10.4), W("shipped", 10.45, 10.8),  # no pause: nothing
             W("forty", 11.5, 11.8),                                               # after a 0.7 s pause
             W("percent", 11.85, 12.2), W("faster!", 12.25, 12.7), W("in", 12.75, 12.8), W("2026", 12.85, 13.2)]
    assert punch.emphasis(words, 10.0) == [1.5, 2.25, 2.85]
    assert punch.emphasis([], 0) == [] and punch.emphasis(None, 0) == []


def test_events_start_the_shot_keep_the_gap_and_fit_the_eases():
    wins = punch.events(5.0, 15.0, [5.5, 6.0, 9.2, 9.4, 13.0, 14.9])
    starts = [a for a, _ in wins]
    assert starts[0] == 0.0  # the scene start always punches
    assert all(b - a >= punch.GAP for a, b in zip(starts, starts[1:]))  # never pumps
    for t0, t1 in wins:
        assert t1 - t0 >= 2 * punch.RAMP and t1 <= 10.0 + 1e-9
        assert t1 - t0 <= 2 * punch.RAMP + punch.HOLD + 1e-9
    assert 0.3 <= punch.RAMP <= 0.5  # the owner's 300-500 ms
    assert punch.events(0.0, 1.0, [0.5]) == []  # a short shot gets none


def test_peak_is_gentle_and_never_crops_the_face():
    assert punch.safe_peak(0.2, 0.15, 0.4) == pytest.approx(punch.ZMAX)  # a normal talking head: the full 1.12
    for fw, fh, cy in [(0.2, 0.15, 0.4), (0.5, 0.3, 0.4), (0.45, 0.6, 0.4), (0.3, 0.2, 0.15)]:
        z = punch.safe_peak(fw, fh, cy)
        assert 1.0 <= z <= punch.ZMAX
        if z > 1.0:  # the face's far edges, wherever the dead zone lets it drift, stay inside the frame at the peak
            assert 0.5 - (punch.DEAD + fw / 2) * z >= punch.MARGIN - 1e-9
            assert cy - (punch.DEAD + fh / 2) * z >= punch.MARGIN - 1e-9
            assert cy + (punch.DEAD + fh / 2) * z <= 1 - punch.MARGIN + 1e-9
    assert punch.safe_peak(0.8, 0.7, 0.4) == 1.0  # a face that fills the frame: no punch at all


def _track(start=0.0, end=6.0, crop_h=1080, crop_w=608):
    return {"layout": "track", "start": start, "end": end, "faces": [1],
            "path": {"crop_w": crop_w, "crop_h": crop_h, "zoom": 1.0, "keys": [(0.0, 600, 0)]}}


def test_plan_shot_only_for_track_shots_and_inside_the_upscale_guard():
    face = {"w": 0.08, "h": 0.15, "cy": 0.4}
    pp = punch.plan_shot(_track(), [2.0], face, (1920, 1080))
    assert pp and pp["windows"][0][0] == 0.0 and 1.0 < pp["peak"] <= punch.ZMAX
    assert OUT_H / (1080 / pp["peak"]) <= UPSCALE_MAX + 1e-9
    for lay in ("fit", "split", "screen"):
        assert punch.plan_shot({**_track(), "layout": lay}, [2.0], face, (1920, 1080)) is None
    # a crop already at the punch's total upscale cap (a small source) can't zoom further
    small = _track(crop_h=int(OUT_H / punch.PUNCH_UPSCALE_MAX), crop_w=360)
    assert punch.plan_shot(small, [2.0], face, (768, 768)) is None


def test_z_expression_starts_at_one_and_is_off_without_windows():
    assert punch.z_expr([], 1.12) == "1" and punch.z_expr([(0.0, 2.0)], 1.0) == "1"
    assert punch.filter_for(None, OUT_W, OUT_H) == ""
    f = punch.filter_for({"windows": [(0.0, 2.0)], "peak": 1.12, "cy": 0.4}, OUT_W, OUT_H)
    assert f.startswith(",zoompan=") and "d=1" in f and f"s={OUT_W}x{OUT_H}" in f and "fps=30" in f


def test_apply_punch_on_and_off():
    plan = {"shots": [{**_track(), "face_med": {"w": 0.08, "h": 0.15, "cy": 0.4}}, {"layout": "fit", "start": 6.0, "end": 9.0}]}
    rr.apply_punch(plan, [W("Big", 3.0, 3.3)], 0.0, {"width": 1920, "height": 1080}, True)
    assert plan["punch"] == {"on": True, "shots": 1} and "punch" in plan["shots"][0] and "punch" not in plan["shots"][1]
    rr.apply_punch(plan, [], 0.0, {"width": 1920, "height": 1080}, False)
    assert plan["punch"] == {"on": False, "shots": 0} and all("punch" not in s for s in plan["shots"])



def test_gap_holds_across_shot_boundaries():
    """CodeRabbit (#131): two face-tracked shots that start 1.5 s apart must not both punch at their start."""
    assert punch.events(1.6, 6.0, [3.5], last=0.0) == [(1.9, 3.9)]  # the shot start is too soon after 0.0; the word at 3.5 isn't
    assert punch.events(1.6, 6.0, [], last=0.0) == []
    assert punch.events(5.0, 15.0, [9.2], last=None) == punch.events(5.0, 15.0, [9.2])  # within one shot: unchanged
    face = {"w": 0.08, "h": 0.15, "cy": 0.4}
    plan = {"shots": [{**_track(0.0, 1.6), "face_med": face}, {**_track(1.6, 6.0), "face_med": face}]}
    rr.apply_punch(plan, [W("so", 1.7, 1.9), W("big!", 3.5, 3.8)], 0.0, {"width": 1920, "height": 1080}, True)
    starts = sorted(s["start"] + t0 for s in plan["shots"] if "punch" in s for t0, _ in s["punch"]["windows"])
    assert starts == [0.0, 3.5], starts
    assert all(b - a >= punch.GAP for a, b in zip(starts, starts[1:]))


# ------------------------------------------------------------------ ffmpeg on a short fixture
def _fixture(tmp: Path) -> str:
    """3 s, 960x540, grey with a white 60x60 'face' centred at (480, 216): where the tracked crop keeps the face."""
    out = tmp / "talk.mp4"
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x404040:s=960x540:r=30:d=3",
                    "-vf", "drawbox=x=450:y=186:w=60:h=60:color=white:t=fill", "-pix_fmt", "yuv420p", str(out)], check=True)
    return str(out)


def _gray(path: str, t: float, w: int = 304, h: int = 540):
    import numpy as np
    raw = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w)


def _box(img):
    import numpy as np
    ys, xs = np.nonzero(img > 200)
    return (xs.min(), xs.max(), ys.min(), ys.max()) if len(xs) else None


def _render(tmp: Path, src: str, shot: dict) -> str:
    out = str(tmp / ("on.mp4" if shot.get("punch") else "off.mp4"))
    args = rr.render_body_cmd(src, 0.0, 3.0, [shot], (960, 540), None, None, out, tmp, "#C9ACFA", "#0B0B12", None)
    rr._ff(args)
    return out


@needs_ff
def test_render_zooms_in_and_back_out_around_the_face(tmp_path):
    src = _fixture(tmp_path)
    # the tracked crop: 540 high, 304 wide (9:16), the face centred across and 40% down
    shot = {"layout": "track", "start": 0.0, "end": 3.0, "faces": [1],
            "path": {"crop_w": 304, "crop_h": 540, "zoom": 1.0, "keys": [(0.0, 328, 0)]}}
    off = _render(tmp_path, src, dict(shot))
    assert "zoompan" not in (tmp_path / "graph.txt").read_text()  # off: the graph is exactly the old one
    pp = {"windows": [(0.0, 2.0)], "peak": 1.12, "cy": 0.4}
    on = _render(tmp_path, src, {**shot, "punch": pp})
    assert "zoompan" in (tmp_path / "graph.txt").read_text()
    b0_off, b0_on = _box(_gray(off, 0.0)), _box(_gray(on, 0.0))
    peak_off, peak_on = _box(_gray(off, 1.0)), _box(_gray(on, 1.0))
    end_off, end_on = _box(_gray(off, 2.6)), _box(_gray(on, 2.6))
    assert b0_on and peak_on and end_on
    size = lambda b: b[1] - b[0]  # noqa: E731
    centre = lambda b: ((b[0] + b[1]) / 2, (b[2] + b[3]) / 2)  # noqa: E731
    assert abs(size(b0_on) - size(b0_off)) <= 2  # starts at 1.0: no jump
    assert 1.07 <= size(peak_on) / size(peak_off) <= 1.16  # the peak is the gentle ~1.12
    assert abs(size(end_on) - size(end_off)) <= 2  # and comes back out
    for b in (b0_on, peak_on, end_on):  # the face stays put on screen and fully inside the frame
        cx, cy = centre(b)
        assert abs(cx - 152) <= 4 and abs(cy - 216) <= 5
        assert b[0] > 0 and b[1] < 303 and b[2] > 0 and b[3] < 539


# ------------------------------------------------------------------ the per-take switch (UI = REST = MCP)
def test_take_switch_defaults_on_and_rejects_non_bool(human, app, monkeypatch):
    from .conftest import agent_token, mcp_call
    house, sent = app.state.house, []
    monkeypatch.setattr(house, "_dispatch_github", lambda jid, workflow="", inputs=None, ttl=1800: (sent.append(jid), (True, ""))[1])
    monkeypatch.setattr(house.takes, "_mode", lambda: "github")
    up = human.post("/api/media", json={"name": "talk.mp4", "data": base64.b64encode(b"\x00\x00\x00\x18ftypmp42" + b"0" * 64).decode()}).json()
    t = human.post("/api/takes", json={"media_token": up["token"], "owns": True}).json()
    assert t["input"]["punch_in"] is True
    t2 = human.post("/api/takes", json={"media_token": up["token"], "owns": True, "punch_in": False}).json()
    assert t2["input"]["punch_in"] is False
    for bad in ("yes", 1, None):
        assert human.post("/api/takes", json={"media_token": up["token"], "owns": True, "punch_in": bad}).status_code == 400
    tok = agent_token(human, policy="trusted", cap=50)
    _, out = mcp_call(human, tok, "clips_from_video", {"media_url": house.media_url(up["token"]), "owns": True, "punch_in": False})
    assert out["input"]["punch_in"] is False, out


def test_the_switch_is_in_the_take_form_and_follows_reduced_motion():
    root = Path(__file__).resolve().parents[2] / "studio" / "static"
    html, js = (root / "index.html").read_text(), (root / "take.js").read_text()
    assert 'id="tkPunch" data-testid="take-punch-in" checked' in html
    assert 'punch_in: !!$("#tkPunch").checked' in js and "prefers-reduced-motion: reduce" in js
    doc = (Path(__file__).resolve().parents[2] / "docs" / "MCP_TOOLS.md").read_text()
    assert "`punch_in`" in doc
    from studio import mcp
    t = next(x for x in mcp.TOOLS if x["name"] == "clips_from_video")
    assert t["inputSchema"]["properties"]["punch_in"]["type"] == "boolean"
    assert json.dumps(t)  # serialisable
