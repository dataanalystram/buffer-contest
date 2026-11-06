"""One take on-screen text: Tesseract OCR (studio/media/ocr.py) for caption placement, the watermark warning, alt text
and the Library search index. Fail open everywhere; tesseract >= 5.5.4 only; fixed args, no shell, caps enforced."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studio import instabox_worker, takes
from studio.media import captions_ass, ocr
from studio.media.reframe import take_clip

ROOT = Path(__file__).resolve().parents[2]


def _ok_status():
    st = ocr.status()
    return st if st["ok"] else None


LANGS_SH = ("#!/bin/sh\ncase \"$*\" in *--list-langs*) echo 'List of available languages in \"/x/\" (1):'; "
            "for l in {langs}; do echo $l; done;; *) echo 'tesseract 5.5.4';; esac\n")

needs_tess = pytest.mark.skipif(not (shutil.which("ffmpeg") and _ok_status()), reason="tesseract >= 5.5.4 + ffmpeg not installed")


# ---------------------------------------------------------------- version gate, binary call
def test_versions_below_5_5_4_turn_ocr_off(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract"
    fake.write_text("#!/bin/sh\necho 'tesseract 5.5.0'\necho ' leptonica-1.84.1'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HQ_TESSERACT", str(fake))
    st = ocr.status()
    assert st["ok"] is False and st["version"] == "5.5.0" and "older than 5.5.4" in st["why"]
    res = ocr.scan("x.mp4", 0, 10, ocr.Budget(), st)
    assert res["ok"] is False and res["frames"] == [] and "older" in res["skipped"]
    fake.write_text(LANGS_SH.format(langs="eng"))
    assert ocr.status()["ok"] is True and ocr.status()["version"] == "5.5.4"
    assert ocr.parse_version("tesseract v5.10.0") == (5, 10, 0) and ocr.parse_version("nope") is None


def test_missing_binary_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("HQ_TESSERACT", "")
    monkeypatch.setattr(ocr, "PREFIXES", ())
    monkeypatch.setattr(ocr.shutil, "which", lambda n: None)
    assert ocr.status() == {"ok": False, "version": None, "path": None, "why": "tesseract not installed"}


def test_tesseract_is_called_with_fixed_args_a_timeout_and_no_shell(monkeypatch, tmp_path):
    from PIL import Image
    png = tmp_path / "f.png"
    Image.new("RGB", (100, 50)).save(png)
    seen = {}

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return subprocess.CompletedProcess(cmd, 0, "level\tp\tb\tpar\tl\tw\tleft\ttop\twidth\theight\tconf\ttext\n"
                                                   "5\t1\t1\t1\t1\t1\t10\t5\t40\t10\t91.0\t@someone\n", "")
    monkeypatch.setattr(ocr.subprocess, "run", run)
    boxes, size = ocr.read_frame("/usr/bin/tesseract", str(png), "/data/tesseract/share/tessdata", timeout=3)
    assert seen["cmd"] == ["/usr/bin/tesseract", str(png), "stdout", "-l", "eng", "--psm", "11", "--oem", "1",
                           "--tessdata-dir", "/data/tesseract/share/tessdata", "tsv"]
    assert seen["timeout"] == 3 and "shell" not in seen and seen["env"]["OMP_THREAD_LIMIT"] == "1"
    assert boxes == [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.2, "text": "@someone", "conf": 91.0}] and size == (100, 50)
    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("t", 3)))
    assert ocr.read_frame("/usr/bin/tesseract", str(png), None)[0] == []                     # a timeout is an empty frame


def test_tsv_parsing_drops_low_confidence_and_junk():
    tsv = ("h\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t30.0\tblur\n5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t95\t|\n"
           "5\t1\t1\t1\t1\t3\t0\t0\t0\t10\t95\tzero\n4\t1\t1\t1\t1\t0\t0\t0\t10\t10\t95\tline\nbad\n"
           "5\t1\t1\t1\t1\t4\t50\t25\t50\t25\t88.5\tSALE\n")
    assert [b["text"] for b in ocr.parse_tsv(tsv, 100, 50)] == ["SALE"]


# ---------------------------------------------------------------- caps
def test_frames_are_at_most_one_per_two_seconds_and_sixty_per_take():
    t = ocr.sample_times(10, 30)
    assert len(t) == 10 and all(b - a >= 2.0 - 1e-6 for a, b in zip(t, t[1:])) and 10 < t[0] < t[-1] < 30
    assert len(ocr.sample_times(0, 3600)) == 60 and ocr.sample_times(0, 1.0) == [0.5] and ocr.sample_times(5, 5) == []
    assert len(ocr.sample_times(0, 60, step=0.5)) == 30                                       # never denser than 1 / 2 s


def test_the_budget_is_shared_by_every_clip_of_a_take(monkeypatch):
    st = {"ok": True, "version": "5.5.4", "path": "/t", "tessdata": None}
    calls = []
    monkeypatch.setattr(ocr, "_grab", lambda path, t, out, timeout=30: calls.append(t) or True)
    monkeypatch.setattr(ocr, "read_frame", lambda *a, **k: ([{"x": 0, "y": 0, "w": 0.1, "h": 0.1, "text": "Hi", "conf": 90}], (1, 1)))
    b = ocr.Budget(seconds=30, frames=60)
    for _ in range(5):                                                       # five 60 s clips: 30 frames asked each
        ocr.scan("x.mp4", 0, 60, b, st)
    assert len(calls) == 60 and b.used_frames == 60
    assert ocr.scan("x.mp4", 0, 60, b, st)["skipped"] == "take OCR budget used"
    slow = ocr.Budget(seconds=30, frames=60)
    clock = iter(range(0, 10_000, 7))                                        # each frame "takes" 7 s
    monkeypatch.setattr(ocr.time, "monotonic", lambda: next(clock))
    ocr.scan("x.mp4", 0, 120, slow, st)
    assert slow.used_frames <= 5 and slow.used_s >= 30                       # stops once 30 s of OCR are spent


# ---------------------------------------------------------------- watermark, alt text
def test_watermarks_find_platforms_and_handles_but_not_the_brands_own():
    assert ocr.watermarks("TikTok @Cool.Creator_1 hello", own_handle="brightdesk") == ["tiktok", "@cool.creator_1"]
    assert ocr.watermarks("follow @brightdesk", own_handle="@BrightDesk") == []
    assert ocr.watermarks("email me at a@b.com or ticktock") == []
    assert ocr.watermarks("Made with CapCut") == ["capcut"]


def test_watermark_is_a_preflight_warning_never_an_error():
    f = takes.watermark_finding({"ocr": {"watermarks": ["tiktok", "@someone"]}})
    assert f["level"] == "warning" and f["rule"] == "media.watermark" and "tiktok, @someone" in f["message"]
    assert takes.watermark_finding({"ocr": {"watermarks": []}}) is None and takes.watermark_finding({}) is None


def test_alt_text_says_the_hook_and_the_screen_text():
    assert ocr.alt_text("We shipped it…", "Launch  day\nv2") == "Video clip: “We shipped it”. On-screen text: Launch day v2"
    assert ocr.alt_text("", "") == "Video clip" and len(ocr.alt_text("x", "y" * 2000)) == 420


# ---------------------------------------------------------------- caption placement
def test_captions_move_off_text_in_the_default_band_and_keep_it_otherwise():
    default = captions_ass.band_y({"layout": "track"})
    box = {"x": 0.1, "y": (default - 40) / 1920, "w": 0.5, "h": 80 / 1920, "text": "SALE", "conf": 90}
    frames = [{"t": 1.0, "boxes": [box]}]
    y = ocr.free_caption_y(frames, {"start": 0, "end": 5, "layout": "track"}, default, 1920, 1080)
    assert y is not None and (y - 85 >= default + 52 or y + 85 <= default - 52)          # clear of the text (+12 px margin)
    assert 290 <= y <= 1470
    assert ocr.free_caption_y(frames, {"start": 6, "end": 9, "layout": "track"}, default, 1920, 1080) is None  # no text then
    far = [{"t": 1.0, "boxes": [{**box, "y": 0.05}]}]
    assert ocr.free_caption_y(far, {"start": 0, "end": 5, "layout": "track"}, default, 1920, 1080) is None    # band is clear
    assert ocr.free_caption_y(frames, {"start": 0, "end": 5, "layout": "split"}, default, 1920, 1080) is None  # unmapped: default
    side = [{"start": 0, "end": 5, "layout": "track"}, {"start": 5, "end": 9, "layout": "track"}]
    assert take_clip.place_captions(side, frames, 1920, 1080) == 1 and side[0]["caption_y"] == y and "caption_y" not in side[1]
    assert captions_ass.band_y(side[0]) == y
    assert take_clip.place_captions(side, "junk", 1920, 1080) == 0


def test_band_y_ignores_a_bad_caption_y_and_stays_in_the_safe_area():
    assert captions_ass.band_y({"layout": "track", "caption_y": True}) == captions_ass.band_y({"layout": "track"})
    assert captions_ass.band_y({"layout": "track", "caption_y": "10"}) == captions_ass.band_y({"layout": "track"})
    assert captions_ass.band_y({"layout": "track", "caption_y": 5}) == captions_ass.SAFE_TOP + 70


def test_spec_cannot_inject_ocr_frames_and_ocr_failure_never_fails_the_render(monkeypatch):
    seen = {}
    monkeypatch.setattr(takes.take, "render_clip", lambda src, c, *a, **k: (seen.update(c=c), (b"v", b"c", {"layout": "track"}))[1])
    monkeypatch.setattr(ocr, "status", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    spec = {"style": "take", "scenes": [], "src": {}, "kit": {"handle": "me"}, "params": {}}
    clip = {"id": "c1", "start": 0, "end": 5, "words": [], "faces": [], "_ocr_frames": [{"t": 0, "boxes": []}], "_loud": "x"}
    out = list(takes.render_spec_clip("x.mp4", spec, clip, None))
    assert out[0][0] == "c1" and "_ocr_frames" not in seen["c"] and "_loud" not in seen["c"]
    assert out[0][3]["ocr"]["ok"] is False and out[0][3]["ocr"]["skipped"].startswith("ocr failed")


# ---------------------------------------------------------------- the worker install
def test_worker_builds_the_pinned_tag_in_the_background_with_no_secret():
    sh = (ROOT / "scripts" / "worker_tesseract.sh").read_text()
    assert f"TAG={instabox_worker.TESSERACT_TAG}" in sh and ocr.MIN_VERSION == tuple(map(int, instabox_worker.TESSERACT_TAG.split(".")))
    assert re.search(r"COMMIT=[0-9a-f]{40}", sh) and re.search(r"DATA_SHA256=[0-9a-f]{64}", sh) and "sha256sum -c" in sh
    assert "refusing to build" in sh and "curl | sh" not in sh
    cmd = instabox_worker.command(7, "https://hq.example", "take_render", "main")
    assert "tesseract --version" in cmd and "flock -n /data/tesseract.lock" in cmd and "nice -n 19" in cmd
    assert cmd.index("worker_tesseract.sh") < cmd.index("studio.render_worker")
    assert "JOB_TOKEN" not in cmd.split("export JOB_TOKEN;")[1]


# ---------------------------------------------------------------- the real binary (CI image / worker)
def _frame(tmp_path):
    font = next((p for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", str(ROOT / "studio/fonts/Inter-Bold.ttf")]
                 if Path(p).is_file()))
    src = tmp_path / "wm.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=4:r=25", "-vf",
                    f"drawtext=fontfile={font}:text='TikTok':fontcolor=white:fontsize=40:x=460:y=20,"
                    f"drawtext=fontfile={font}:text='@coolcreator':fontcolor=white:fontsize=36:x=20:y=300",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)], check=True)
    return src


@needs_tess
def test_real_tesseract_reads_a_watermark_within_the_caps(tmp_path):
    src = _frame(tmp_path)
    b = ocr.Budget()
    res = ocr.scan(str(src), 0, 4, b)
    assert res["ok"] and len(res["frames"]) == 2 and b.used_frames == 2 and b.used_s < 30
    assert ocr.watermarks(res["text"]) == ["tiktok", "@coolcreator"]
    box = next(x for f in res["frames"] for x in f["boxes"] if x["text"] == "@coolcreator")
    assert 0.78 < box["y"] < 0.9


# ---------------------------------------------------------------- review fixes (CodeRabbit, #130)
def test_a_zoomed_track_crop_maps_boxes_through_its_own_window():
    # text at 40-45% of a 1080-tall source; the crop is 540 px tall starting at y=270 -> 20-30% of the clip...
    box = {"x": 0.1, "y": 0.40, "w": 0.3, "h": 0.05, "text": "SALE", "conf": 90}
    full = ocr._to_out_y(box, "track", 1920, 1080, 1920, 1080)
    zoom = ocr._to_out_y(box, "track", 1920, 1080, 1920, 1080, crop=(270.0, 540.0))
    assert round(full[0]) == 768 and round(zoom[0]) == 576 and round(zoom[1]) == 768
    shots = [{"start": 0, "end": 5, "layout": "track", "path": {"crop_w": 304, "crop_h": 540, "keys": [(0.0, 10, 270)]}}]
    assert take_clip.crop_at_fn(shots)(1.0) == (270.0, 540.0) and take_clip.crop_at_fn(shots)(6.0) is None
    default = captions_ass.band_y({"layout": "track"})                       # 1330: text at 1280-1380 if the crop is used
    hit = {**box, "y": (270 + default / 1920 * 540 - 20) / 1080, "h": 40 / 1080}
    frames = [{"t": 1.0, "boxes": [hit]}]
    seg = {"start": 0, "end": 5, "layout": "track"}
    assert ocr.free_caption_y(frames, seg, default, 1920, 1080) is None       # full-frame mapping: misses it
    assert ocr.free_caption_y(frames, seg, default, 1920, 1080, crop_at=take_clip.crop_at_fn(shots)) is not None


def test_frame_extraction_counts_against_the_budget(monkeypatch):
    st = {"ok": True, "version": "5.5.4", "path": "/t", "tessdata": None}
    seen = {}
    clock = iter([0, 0, 29.9, 29.9, 30, 30, 30, 30])

    def grab(path, t, out, timeout=30):
        seen.setdefault("grab_timeouts", []).append(timeout)
        return True
    monkeypatch.setattr(ocr, "_grab", grab)
    monkeypatch.setattr(ocr.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(ocr, "read_frame", lambda *a, **k: seen.setdefault("read", True) and ([], (1, 1)))
    b = ocr.Budget(seconds=30)
    ocr.scan("x.mp4", 0, 4, b, st)
    assert seen["grab_timeouts"][0] == 30 and seen["grab_timeouts"][1] < 0.2      # the 2nd grab only gets what's left
    assert "read" not in seen and b.used_s >= 29.9                              # no time left: no recognition


def test_status_survives_undecodable_version_output(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract"
    fake.write_bytes(b"#!/bin/sh\nprintf 'tesseract 5.5.4\\n\\377\\376 junk\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("HQ_TESSERACT", str(fake))
    assert ocr.status()["version"] == "5.5.4"


def test_worker_retries_setup_when_the_language_data_is_missing():
    cmd = instabox_worker.command(7, "https://hq.example", "take_render", "main")
    assert "[ -s /data/tesseract/share/tessdata/eng.traineddata ]; } ||" in cmd


def test_ocr_is_off_without_english_data(monkeypatch, tmp_path):
    fake = tmp_path / "tesseract"
    fake.write_text(LANGS_SH.format(langs="osd"))
    fake.chmod(0o755)
    monkeypatch.setenv("HQ_TESSERACT", str(fake))
    st = ocr.status()
    assert st["ok"] is False and st["version"] == "5.5.4" and "eng.traineddata" in st["why"]
    assert ocr.scan("x.mp4", 0, 10, ocr.Budget(), st)["frames"] == []
    monkeypatch.setattr(ocr.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("t", 10)))
    assert ocr.has_eng(str(fake), None) is False


def test_frame_extraction_never_gets_more_than_the_budget_left(monkeypatch):
    seen = []
    monkeypatch.setattr(ocr, "_grab", lambda path, t, out, timeout=30: seen.append(timeout) or False)
    b = ocr.Budget(seconds=30.0)
    b.used_s = 29.9
    ocr.scan("x.mp4", 0, 20, b, {"ok": True, "path": "/bin/true", "version": "5.5.4"})
    assert seen and all(0 < t <= 0.1 + 1e-9 for t in seen)
    seen.clear()
    b.used_s = 30.0
    res = ocr.scan("x.mp4", 0, 20, b, {"ok": True, "path": "/bin/true", "version": "5.5.4"})
    assert seen == [] and res["skipped"] == "take OCR budget used"


def test_ocr_frame_times_follow_the_pause_trim():
    from studio.media import cleancut
    keep = [[10.0, 12.0], [15.0, 20.0]]           # 3 s cut between 12 and 15 (source seconds)
    frames = [{"t": 1.0, "boxes": [1]}, {"t": 3.0, "boxes": [2]}, {"t": 6.0, "boxes": [3]}, {"t": "x"}, None]
    got = cleancut.remap_frames(frames, keep, 10.0)   # t is clip seconds from the span's start (10 s)
    assert got == [{"t": 1.0, "boxes": [1]}, {"t": 3.0, "boxes": [3]}]   # 13 s was cut; 16 s -> 2 + 1


def test_prepare_carries_remapped_ocr_frames(monkeypatch, tmp_path):
    from studio.media import cleancut
    monkeypatch.setattr(cleancut, "plan_cuts", lambda *a, **k: {"keep": [[10.0, 12.0], [15.0, 20.0]], "removed": 3.0,
                                                                "pauses": 1, "fillers": 0, "skipped": ""})
    monkeypatch.setattr(cleancut, "cut", lambda *a, **k: None)
    m = {"id": "c1", "start": 10.0, "end": 20.0, "_ocr_frames": [{"t": 1.0, "boxes": []}, {"t": 3.0, "boxes": []},
                                                                    {"t": 6.0, "boxes": []}]}
    got = cleancut.prepare("src.mp4", m, [], [], [], {"audio": False}, str(tmp_path))
    assert got["m"]["start"] == 0.0 and got["m"]["_ocr_frames"] == [{"t": 1.0, "boxes": []}, {"t": 3.0, "boxes": []}]
    assert m["_ocr_frames"][1]["t"] == 3.0   # the caller's dict is left alone
