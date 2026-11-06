"""The opus hook as a 3 s opener (owner, 6 Nov 2026: "why the credit stays always on the screen and also why it is white
... starting is the hook that should be attractive"). The hook box is the brand's card (not plain white) and, with the
credit chip, pops in over the first compose.HOOK_OPENER_SECS and fades out; the credit is on the end card too. The credit
chip can no longer land on the hook box (it did on take 93's c2), the face check counts both only in their 3 s, and
Layout & captions > Hook picks opener / keep on screen (REST = MCP)."""
import json
import shutil
import subprocess

import pytest

from studio.media import facesafe

from .test_onetake_layouts import take_clip  # noqa: F401  (fixture)

W, H = 1080, 1920
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="needs FFmpeg")


def _fit(text, w, h):
    return h >= 90, 90


def test_the_credit_never_lands_on_the_hook_box():
    """Take 93 c2: the hook went under the captions and the credit's last-resort spot was the bottom of the safe area,
    on the hook's second line. Kept on screen, the fallback now picks a spot clear of the hook box; as an opener, the
    credit goes to the end card only (it is there anyway) instead of crowding the opening."""
    L = {"safe": (60, 230, 930, 1500), "hook": (60, 230, 930, 400), "captions_center": 1152}
    keep = (100, 0, 1000, 1100)  # the face fills the frame down to the captions: no free spot for the chip
    kw = dict(static=keep, segments=[{"start": 0, "end": 9, "keepout": keep}], cap_h=240,
              hook="The third thing is not knowing who you are", hook_fit=_fit, credit_h=46, captions_on=True)
    L2, rec = facesafe.place_opus(dict(L), W, H, **kw)
    assert L2["hook"] and rec["hook"] == "below_captions", rec
    hb = L2["hook"]
    x, y = L2["credit_xy"]
    chip = (x, y, x + int((930 - 60) * 0.75), y + 46)
    assert not facesafe.hits(chip, (hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10)), (rec, hb, chip)
    assert rec["credit"].startswith("lowest_safe")
    hb_old = (hb[0] - 10, hb[1] - 10, hb[2] + 10, hb[3] + 10)  # the old fallback: the bottom of the safe area
    assert facesafe.hits((60, 1500 - 46, 60 + 652, 1500), hb_old)
    L3, rec3 = facesafe.place_opus(dict(L), W, H, **kw, opener=keep, opener_s=3.0)
    assert L3["credit_xy"] is None and L3["credit_end_only"] and rec3["credit"] == "end_card_only"


def test_the_opener_keeps_clear_of_the_first_seconds_faces_only():
    """With the opener, the hook may use a spot the face takes later in the clip but not in the first 3 s."""
    L = {"safe": (60, 230, 930, 1500), "hook": (60, 230, 930, 400), "captions_center": 1152}
    whole = (0, 0, 1080, 1000)    # the speaker leans into the top later on
    early = (300, 700, 800, 1000)  # in the first 3 s the face sits lower
    segs = [{"start": 0, "end": 9, "keepout": whole}]
    L1, r1 = facesafe.place_opus(dict(L), W, H, static=whole, segments=segs, cap_h=200, hook="Hook", hook_fit=_fit,
                                 credit_h=46, captions_on=True)
    L2, r2 = facesafe.place_opus(dict(L), W, H, static=whole, segments=segs, cap_h=200, hook="Hook", hook_fit=_fit,
                                 credit_h=46, captions_on=True, opener=early, opener_s=3.0)
    assert r1["hook"] != "default" and r2["hook"] == "default" and L2["hook"] == L["hook"]
    assert r2["opener_s"] == 3.0 and r2["opener_keepout"] == list(early)


def test_predicted_cover_counts_the_opener_only_in_its_seconds():
    L = {"safe": (60, 230, 930, 1500), "hook": (60, 230, 930, 400), "credit_xy": (60, 420)}
    face = (100, 250, 600, 600)
    pred = [(1.0, face), (5.0, face)]
    assert facesafe.predict_cover(L, pred, W, H, cap_h=200, captions_on=False, credit_h=46) > 0.2
    assert facesafe.predict_cover(L, [(5.0, face)], W, H, cap_h=200, captions_on=False, credit_h=46, static_until=3.0) == 0
    assert facesafe.predict_cover(L, pred, W, H, cap_h=200, captions_on=False, credit_h=46, static_until=3.0) > 0.2


def test_zones_for_adds_the_opener_keep_out():
    samples = [{"t": t, "faces": [{"x": 0.4, "y": 0.5 if t < 3 else 0.1, "w": 0.2, "h": 0.2}]} for t in (0.5, 1, 1.5, 2, 5, 6, 7, 8)]
    z = facesafe.zones_for(samples, None, 1920, 1080, W, H, 9.0, opener_s=3.0)
    assert z["opener"] and z["static"] and z["opener"][1] > z["static"][1]
    assert "opener" not in facesafe.zones_for(samples, None, 1920, 1080, W, H, 9.0)


def _src(tmp_path):
    p = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=6", "-f", "lavfi", "-i",
                    "sine=d=6", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(p)], check=True)
    return str(p)


@needs_ffmpeg
@pytest.mark.parametrize("mode", ["opener", "keep"])
def test_render_the_hook_as_an_opener_or_kept(tmp_path, mode):
    from PIL import Image

    from studio.media.compose import render_style
    words = [{"w": w, "s": i * 0.4, "e": i * 0.4 + 0.35} for i, w in enumerate("one two three four five six seven".split())]
    z = {"static": (380, 300, 700, 900), "segments": [], "opener": (380, 300, 700, 900), "pred": []}
    r = render_style(_src(tmp_path), "opus", out=str(tmp_path / "o.mp4"), hook="The third thing is not knowing who you are",
                     words=words, credit="Credit: @someone", clip=(0, 5), face_safe=z, workdir=str(tmp_path / "wd"),
                     hook_mode=mode, crf=30, preset="ultrafast")
    assert r["hook_mode"] == mode and r["boxes"].get("hook") and r["boxes"].get("credit")
    ov = Image.open(tmp_path / "wd" / "overlay.png")
    if mode == "opener":
        assert r["hook_secs"] == 3.0 and (tmp_path / "wd" / "opener.png").is_file()
        assert ov.getbbox() is None                       # nothing stays on screen after the opener
        assert r["boxes"]["end_card"].get("credit")       # the rights line ends the video
        hb = r["boxes"]["hook"]
        px = Image.open(tmp_path / "wd" / "opener.png").convert("RGBA").getpixel((hb[0] + 40, hb[1] + 4))
        assert px[:3] != (255, 255, 255)                  # the brand's card, not the old white box
    else:
        assert r["hook_secs"] is None and ov.getbbox() is not None and not r["boxes"]["end_card"].get("credit")


def test_layout_and_captions_hook_choice_rest(take_clip, app):  # noqa: F811
    human, rid, cid, rendered = take_clip
    store = app.state.store
    assert "hook_modes" not in human.get(f"/api/takes/{rid}/clips/{cid}/layout").json()  # a One take clip has no hook box
    assert human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_mode": "keep"}).status_code == 409
    row = store.q("SELECT input FROM agent_runs WHERE id=?", (rid,))[0]
    inp = json.loads(row["input"])
    inp["style"] = "opus"
    store.x("UPDATE agent_runs SET input=? WHERE id=?", (json.dumps(inp), rid))
    v = human.get(f"/api/takes/{rid}/clips/{cid}/layout").json()
    assert v["hook_mode"] == "opener" and v["hook_modes"] == ["opener", "keep"]
    assert human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_mode": "always"}).status_code == 400
    r = human.post(f"/api/takes/{rid}/clips/{cid}/layout", json={"hook_mode": "keep"})
    assert r.status_code == 200, r.text
    assert r.json()["hook_mode"] == "keep" and rendered == [(cid,)]
    from studio.takes import opus_hook_mode
    raw = json.loads(store.q("SELECT result FROM agent_runs WHERE id=?", (rid,))[0]["result"])
    assert opus_hook_mode(next(c for c in raw["clips"] if c["id"] == cid)) == "keep"
    assert opus_hook_mode({}) == "opener"


def test_mcp_schema_offers_the_hook_choice():
    from studio import take_layout
    t = next(x for x in take_layout.TOOLS if x["name"] == "set_clip_layout")
    assert t["inputSchema"]["properties"]["hook_mode"]["enum"] == ["opener", "keep"] and "opener" in t["description"]


def test_the_layout_sheet_has_the_hook_chips():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "studio" / "static" / "takelayout.js").read_text()
    assert 'data-testid="layout-hook-${esc(k)}"' in js and "hook_mode: my.hook" in js
