"""Local end-to-end One take harness (no Render, Supabase, R2 or Groq).

  python harness.py analyze <name|all>   take.analyze -> runs/<name>/analysis.json
  python harness.py render  <name|all>   mine moments, pick one, takes.render_spec_clip (OCR -> trim -> reframe -> punch ->
                                         captions -> loudness), preflight watermark finding + alt text -> runs/<name>/
  python harness.py measure <name|all>   loudness, faces in output, zoom curve, transcript diff, contact sheet
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

REVIEW = Path(__file__).resolve().parent
REPO = Path(os.environ.get("HQ_REPO") or REVIEW.parent.parent)
sys.path.insert(0, str(REPO))
os.environ.pop("GROQ_API_KEY", None)
os.environ.setdefault("HQ_TESSERACT", str(REVIEW.parent / "tools/tess554/bin/tesseract"))
os.environ.setdefault("HQ_FACE_MODEL", str(REVIEW / "blaze_face_short_range.tflite"))
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)

VIDEOS = {
    "v1_talkinghead_kende": "v1_talkinghead_kende.mp4",
    "v2_interview_cloke": "v2_interview_cloke.mp4",
    "v3_screen_redirects": "v3_screen_redirects.mp4",
    "v4_watermark_css": "v4_watermark_css.mp4",
    "v5_pauses_caruso": "v5_pauses_caruso.mp4",
}


def rundir(name: str) -> Path:
    d = REVIEW / "runs" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def analyze(name: str) -> None:
    from studio import take
    an = take.analyze(str(REVIEW / VIDEOS[name]), model="base.en")
    an.pop("jev_frames", None)
    (rundir(name) / "analysis.json").write_text(json.dumps(an, indent=1))
    print("analysis", name, an["providers"], an["timings"], len(an["words"]), "words", len(an["faces"]), "faces", flush=True)


def pick_moment(an: dict) -> dict:
    """The mined moment (of the top 4) with the most pause/filler to trim, so the trim is exercised."""
    from studio import take
    from studio.media import cleancut
    mined = take.mine_moments(an)
    best, best_cut = None, -1.0
    for m in mined["moments"][:4]:
        ws = [w for w in an["words"] if m["start"] - 0.05 <= w["s"] and w["e"] <= m["end"] + 0.05]
        sil = [x for x in an["silences"] if x[1] > m["start"] and x[0] < m["end"]]
        pl = cleancut.plan_cuts(ws, m["start"], m["end"], sil)
        if pl["removed"] > best_cut:
            best, best_cut = m, pl["removed"]
    return {**best, "picked_by": mined["picked_by"], "n_moments": len(mined["moments"])}


def render(name: str) -> None:
    from studio import brand, takes
    from studio.media.reframe import render as rr
    d = rundir(name)
    an = json.loads((d / "analysis.json").read_text())
    m = pick_moment(an)
    src = str(REVIEW / VIDEOS[name])
    captured = {}
    orig = rr.render

    def spy(*a, **kw):
        out = orig(*a, **kw)
        captured["shots"] = [{k: s.get(k) for k in ("start", "end", "layout", "rule", "punch", "face_med")}
                             for s in out["plan"]["shots"]]
        captured["sidecar"] = out["plan"].get("sidecar")
        return out
    rr.render = spy
    run = {"input": {"style": "take", "trim_pauses": True}}
    c = {"id": m["id"], "start": m["start"], "end": m["end"], "hook": m.get("hook") or m.get("text", "")[:90],
         "words": takes.clip_words(an["words"], m, m["start"], m["end"]),
         "faces": [f for f in an.get("faces") or [] if m["start"] - 1 <= f["t"] <= m["end"] + 1],
         "layout_overrides": {}, "caption_preset": None, "smart_layout": False, "punch_in": True,
         **takes.Takes._trim_spec(run, an, m)}
    kit = dict(brand.DEFAULT_KIT)
    kit["handle"] = "huhqueue_test"
    spec = {"kind": "take_render", "kit": kit, "params": brand.visual_params(kit, []), "scenes": an.get("scenes") or [],
            "src": an["probe"], "style": "take"}
    out = list(takes.render_spec_clip(src, spec, c, None))
    cid, data, cover, meta = out[0]
    (d / "clip.mp4").write_bytes(data)
    (d / "cover.jpg").write_bytes(cover)
    meta["preflight_watermark"] = takes.watermark_finding(meta)
    meta["alt_text"] = takes._alt_text(c["hook"], str((meta.get("ocr") or {}).get("text") or "")[:500])
    meta["_moment"] = {k: m.get(k) for k in ("id", "start", "end", "hook", "text", "picked_by", "n_moments")}
    meta["_shots_plan"] = captured.get("shots")
    meta["_sidecar"] = captured.get("sidecar")
    (d / "meta.json").write_text(json.dumps(meta, indent=1, default=str))
    print("rendered", name, len(data), "bytes", meta.get("duration"), "s", "trim", meta.get("trim"), "punch", meta.get("punch_in"),
          "ocr", {k: (meta.get("ocr") or {}).get(k) for k in ("ok", "frames", "watermarks", "skipped")},
          "moved", meta.get("captions_moved_for_text"), flush=True)


# ---------------------------------------------------------------------------------------------- measure
def ebur128(path: str) -> dict:
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    tail = r.stderr[r.stderr.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    tp = re.search(r"Peak:\s+(-?[\d.]+) dBFS", tail)
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", tail)
    return {"I": float(i.group(1)) if i else None, "TP": float(tp.group(1)) if tp else None,
            "LRA": float(lra.group(1)) if lra else None}


def z_at(windows, peak, t, ramp=0.4):
    z = 0.0
    for t0, t1 in windows:
        u = min(max(min((t - t0) / ramp, 1), 0), max(min((t1 - t) / ramp, 1), 0))
        z += (1 - math.cos(math.pi * u)) / 2
    return 1 + (peak - 1) * z


def zoom_curve(shots: list[dict]) -> dict:
    pts, starts, peaks = [], [], []
    for s in shots or []:
        pp = s.get("punch")
        if not pp:
            continue
        peaks.append(pp["peak"])
        for t0, t1 in pp["windows"]:
            starts.append(round(s["start"] + t0, 3))
        k = 0.0
        while k <= s["end"] - s["start"]:
            pts.append((round(s["start"] + k, 2), round(z_at(pp["windows"], pp["peak"], k), 4)))
            k += 0.05
    gaps = [round(b - a, 3) for a, b in zip(starts, starts[1:])]
    return {"punches": len(starts), "starts_clip_s": starts, "min_gap_s": min(gaps) if gaps else None,
            "peak_max": max((z for _, z in pts), default=1.0), "plan_peaks": peaks, "curve": pts}


def face_check(path: str, fps: float = 2.0, skip_head: float = 0.5, skip_tail: float = 1.5) -> dict:
    """YuNet on the rendered 1080x1920 frames (cover and end card skipped): is every detected face fully in frame?"""
    import numpy as np
    from studio.media.reframe import faces as fm
    w, h = 270, 480
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                               capture_output=True, text=True).stdout.strip() or 0)
    det = fm.detector(w, h)
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(skip_head), "-t", str(max(0.1, dur - skip_head - skip_tail)), "-i", path,
                        "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True)
    n = len(r.stdout) // (w * h * 3)
    with_face, clipped, minmargin, xs = 0, 0, 1.0, []
    for i in range(n):
        img = np.frombuffer(r.stdout[i * w * h * 3:(i + 1) * w * h * 3], dtype=np.uint8).reshape(h, w, 3)
        _, rows = det.detect(img)
        rows = [x for x in (rows if rows is not None else []) if x[-1] >= 0.6]
        if not rows:
            continue
        with_face += 1
        x, y, bw, bh = [float(v) for v in rows[0][:4]]
        xs.append((x + bw / 2) / w)
        m = min(x / w, y / h, (w - x - bw) / w, (h - y - bh) / h)
        minmargin = min(minmargin, m)
        if m < 0:
            clipped += 1
    return {"frames": n, "with_face": with_face, "face_clipped": clipped, "min_edge_margin": round(minmargin, 3),
            "face_cx_range": [round(min(xs), 2), round(max(xs), 2)] if xs else None}


def src_face_frames(an: dict, s: float, e: float) -> dict:
    fs = [f for f in an.get("faces") or [] if s <= f["t"] <= e]
    return {"samples_with_face": len(fs), "span_samples": int((e - s) * 2)}


def norm_words(ws):
    out = []
    for w in ws:
        t = re.sub(r"[^a-z0-9']", "", str(w["w"]).lower())
        if t:
            out.append(t)
    return out


def _tok(w):
    return re.sub(r"[^a-z']+", "", str(w.get("w", "")).lower())


def transcript_diff(name: str, an: dict, meta: dict) -> dict:
    import difflib
    from studio import take
    from studio.media import cleancut
    m = meta.get("moment") or meta["_moment"]
    s, e = float(m["start"]), float(m["end"])
    src_ws = [w for w in an["words"] if s - 0.05 <= w["s"] and w["e"] <= e + 0.05]
    out_words, _, _ = take.transcribe(str(rundir(name) / "clip.mp4"), "base.en")
    a = [x for x in norm_words(src_ws) if x not in cleancut.FILLERS]
    b = [x for x in norm_words(out_words) if x not in cleancut.FILLERS]
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    lost = [" ".join(a[i1:i2]) + " -> " + " ".join(b[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op in ("delete", "replace")]
    added = [" ".join(b[j1:j2]) for op, i1, i2, j1, j2 in sm.get_opcodes() if op == "insert"]
    pl = cleancut.plan_cuts(src_ws, s, e, [x for x in an["silences"] if x[1] > s and x[0] < e])
    keep = pl["keep"]
    # timestamp-exact: every non-filler source word must sit wholly inside a kept segment
    cut_words = [f"{w['w']}@{w['s']}" for w in src_ws if _tok(w) not in cleancut.FILLERS and
                 not any(k0 - 1e-3 <= w["s"] and w["e"] <= k1 + 1e-3 for k0, k1 in keep)]
    fillers_src = [w["w"] for w in src_ws if _tok(w) in cleancut.FILLERS]
    return {"src_words": len(a), "out_words": len(b), "similarity": round(sm.ratio(), 3), "diff_runs": lost[:25],
            "inserted_runs": added[:15], "plan_words_outside_keep": cut_words, "fillers_in_asr": fillers_src,
            "keep_segments": len(keep), "plan": {k: pl.get(k) for k in ("removed", "pauses", "fillers", "skipped")},
            "src_text": " ".join(w["w"] for w in src_ws)[:2500], "out_text": " ".join(w["w"] for w in out_words)[:2500]}


def keep_map(an: dict, mom: dict):
    from studio.media import cleancut
    s0, e0 = float(mom["start"]), float(mom["end"])
    ws = [w for w in an["words"] if s0 - 0.05 <= w["s"] and w["e"] <= e0 + 0.05]
    return cleancut.plan_cuts(ws, s0, e0, [x for x in an["silences"] if x[1] > s0 and x[0] < e0])["keep"]


def sheet(name: str, an: dict, meta: dict, ms: dict) -> str:
    """Contact sheet: rows at times across the clip; source 16:9 frame (before) left, rendered 9:16 frame (after) right."""
    from PIL import Image, ImageDraw, ImageFont
    d = rundir(name)
    cover = 0.5
    mom = meta.get("moment") or meta["_moment"]
    s0 = float(mom["start"])
    clipdur = float(meta["duration"]) - cover - (1.5 if meta.get("has_end_card") else 0)
    times, labels = [], []
    for st in ms["zoom"]["starts_clip_s"][:2]:
        times.append(st + 0.6)
        labels.append("punch-in peak")
    for f in (0.1, 0.4, 0.7, 0.95):
        if len(times) >= 5:
            break
        times.append(clipdur * f)
        labels.append("")
    keep = keep_map(an, mom) if (meta.get("trim") or {}).get("trimmed_s") else None
    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    rows = []
    for t, lab in sorted(zip(times, labels)):
        src_t = s0 + t
        if keep:
            acc = 0.0
            for k0, k1 in keep:
                if t <= acc + (k1 - k0):
                    src_t = k0 + (t - acc)
                    break
                acc += k1 - k0
        a, b = d / f"_src_{t:.2f}.png", d / f"_out_{t:.2f}.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{src_t:.3f}", "-i", str(REVIEW / VIDEOS[name]), "-frames:v", "1",
                        "-vf", "scale=854:-2", str(a)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t + cover:.3f}", "-i", str(d / "clip.mp4"), "-frames:v", "1",
                        "-vf", "scale=270:480", str(b)], check=True)
        A, B = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
        A = A.resize((854, int(A.height * 854 / A.width)))
        row = Image.new("RGB", (854 + 20 + 270, max(A.height, 480) + 34), (24, 24, 28))
        row.paste(A, (0, 34 + max(0, (480 - A.height) // 2)))
        row.paste(B, (874, 34))
        ImageDraw.Draw(row).text((6, 4), f"before: source {src_t:.1f}s  |  after: clip {t:.1f}s  {lab}", fill=(201, 172, 250), font=font)
        rows.append(row)
        a.unlink()
        b.unlink()
    W = max(r.width for r in rows)
    out = Image.new("RGB", (W, sum(r.height for r in rows) + 50), (12, 12, 14))
    ImageDraw.Draw(out).text((8, 12), f"{name}: source (before, left) vs One take 9:16 (after, right)", fill=(255, 255, 255), font=font)
    y = 50
    for r in rows:
        out.paste(r, (0, y))
        y += r.height
    p = REVIEW / f"contact_{name}.png"
    out.save(p)
    return str(p)


def measure(name: str) -> None:
    d = rundir(name)
    an = json.loads((d / "analysis.json").read_text())
    meta = json.loads((d / "meta.json").read_text())
    res = {"name": name}
    res["loudness_out"] = ebur128(str(d / "clip.mp4"))
    mom = meta.get("moment") or meta["_moment"]
    s, e = float(mom["start"]), float(mom["end"])
    tmp = d / "_span.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(s), "-t", str(e - s), "-i", str(REVIEW / VIDEOS[name]), "-vn", str(tmp)], check=True)
    res["loudness_src"] = ebur128(str(tmp))
    tmp.unlink()
    res["faces_src"] = src_face_frames(an, s, e)
    res["faces_out"] = face_check(str(d / "clip.mp4"))
    res["zoom"] = zoom_curve(meta.get("_shots_plan"))
    (d / "zoom_curve.json").write_text(json.dumps(res["zoom"].pop("curve")))
    res["transcript"] = transcript_diff(name, an, meta)
    res["sheet"] = sheet(name, an, meta, res)
    (d / "measure.json").write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "transcript"}, indent=1))
    print("transcript", json.dumps({k: v for k, v in res["transcript"].items() if k not in ("out_text", "src_text")}), flush=True)


if __name__ == "__main__":
    cmd, name = sys.argv[1], sys.argv[2]
    names = list(VIDEOS) if name == "all" else name.split(",")
    for n in names:
        try:
            {"analyze": analyze, "render": render, "measure": measure}[cmd](n)
        except Exception as ex:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print("FAILED", cmd, n, ex, flush=True)
