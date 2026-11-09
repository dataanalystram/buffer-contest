"""One take: a whole clip with per-shot layouts (worker side).

[cover] + [the moment, laid out shot by shot, captions placed from the layout sidecar, logo] + [end card], in ONE
encode at 1080x1920. Returns (mp4, cover_jpg, meta) like ``take.render_clip``. The moment dict may carry
``layout_overrides`` ({scene index: layout}), ``caption_preset`` and ``smart_layout`` (the owner's opt-in to the vision
picker, studio/media/reframe/vision.py).
"""
from __future__ import annotations

import io
import shutil
import tempfile
from pathlib import Path

from . import path as path_mod, render as rr
from .decide import OUT_H, OUT_W


def crop_at_fn(shots: list[dict] | None):
    """t (clip seconds) -> the tracked crop's (top, height) in source pixels, or None outside a tracked shot."""
    def at(t: float):
        for s in shots or []:
            p = s.get("path") if s.get("layout") == "track" else None
            if p and s["start"] - 1e-6 <= t < s["end"] and p.get("crop_h"):
                _, y = path_mod.at(p, t - s["start"])
                return float(y), float(p["crop_h"])
        return None
    return at


def crop_x_fn(shots: list[dict] | None):
    """t (clip seconds) -> the tracked crop's (left, width) in source pixels, or None outside a tracked shot."""
    def at(t: float):
        for s in shots or []:
            p = s.get("path") if s.get("layout") == "track" else None
            if p and s["start"] - 1e-6 <= t < s["end"] and p.get("crop_w"):
                x, _ = path_mod.at(p, t - s["start"])
                return float(x), float(p["crop_w"])
        return None
    return at


def place_captions(sidecar: list[dict], frames, sw: int, sh: int, shots: list[dict] | None = None) -> int:
    """Move each segment's caption line off on-screen text the OCR found (studio/media/ocr). Fail open: any problem keeps
    the default band. Returns how many segments moved."""
    if not frames or not isinstance(frames, list):
        return 0
    from .. import captions_ass, ocr
    n, crop_at = 0, crop_at_fn(shots)
    for seg in sidecar:
        try:
            y = ocr.free_caption_y(frames, seg, captions_ass.band_y(seg, captions_ass.REF_H), sw, sh, crop_at=crop_at)
        except Exception:  # noqa: BLE001 - placement never fails a render
            y = None
        if y is not None:
            seg["caption_y"] = y
            n += 1
    return n


def render_take_clip(path: str, m: dict, words: list[dict], faces: list[dict], scenes: list[float], src: dict, kit: dict,
                     p: dict, logo: bytes | None = None) -> tuple[bytes, bytes, dict]:
    from PIL import Image

    from ... import render as stills, take
    from .. import captions_ass
    W, H = OUT_W, OUT_H
    tmp = Path(tempfile.mkdtemp(prefix="hq_take_layout_"))
    try:
        start, end = float(m["start"]), float(m["end"])
        dur = end - start
        sw = int(src.get("width") or 1920)
        pins = rr.pins_from(m.get("layout_overrides"))
        plan = rr.plan_clip(path, start, end, words, scenes, src, faces, pins, m.get("layout_prefs") or {},
                            smart_layout=bool(m.get("smart_layout")))
        sh = int(src.get("height") or 1080)
        # captions clear of the face AFTER the punch-in (the same punch plan rr.render applies), and of on-screen text
        from .. import facesafe
        rr.apply_punch(plan, words, start, src, m.get("punch_in", True) is not False, m.get("_punch_emph"))
        fs_place = facesafe.place_track_captions(plan["sidecar"], plan["shots"], sw, sh, m.get("_ocr_frames"),
                                                 crop_at=crop_at_fn(plan["shots"]), crop_x=crop_x_fn(plan["shots"]))
        moved = fs_place["moved_for_text"]
        # cover: the sharpest early frame, framed like the shot it falls in
        ct = take.pick_cover_time(path, m, faces, tmp)
        shot = next((s for s in plan["shots"] if s["start"] <= ct - start < s["end"]), plan["shots"][0])
        cplan = {"mode": "pad", "crop_w": sw, "keys": [(0.0, 0)]}
        if shot["layout"] == "track" and shot.get("path"):
            x, _ = path_mod.at(shot["path"], ct - start - shot["start"])
            cplan = {"mode": "track", "crop_w": int(shot["path"]["crop_w"]), "keys": [(0.0, int(x))]}
        take.frame_at(path, ct, str(tmp / "cover_src.jpg"))
        cover = take.cover_image((tmp / "cover_src.jpg").read_bytes(), cplan, sw, m.get("hook") or "", kit, p, logo, 0.0, W, H)
        (tmp / "cover.jpg").write_bytes(cover)
        has_end = bool(p.get("end_card", True))
        end_jpg = None
        if has_end:
            ec, _ = stills.end_card(kit, p, logo=logo, ratio="9:16")
            Image.open(io.BytesIO(ec)).convert("RGB").resize((W, H), Image.LANCZOS).save(tmp / "end.jpg", quality=92)
            end_jpg = str(tmp / "end.jpg")
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        stills.place_logo(ov, stills.load_logo(logo), p, kit)
        ov.save(tmp / "logo.png")
        preset = captions_ass.clean_preset(m.get("caption_preset")) or captions_ass.DEFAULT_PRESET
        out = tmp / "clip.mp4"
        audio = take.audio_chain(dur, m.get("_loud")) if src.get("audio", True) else None
        plan = rr.render(path, start, end, words, src, str(out), kit=kit, p=p, preset=preset, logo_png=str(tmp / "logo.png"),
                         plan=plan, audio=audio, cover=str(tmp / "cover.jpg"), cover_s=take.COVER_SECS, end_card=end_jpg,
                         end_s=take.END_SECS if has_end else 0.0, punch_in=m.get("punch_in", True) is not False,
                         punch_emph=m.get("_punch_emph"), captions=not m.get("_source_captions"))
        # no captions of ours (the source keeps its own): nothing of ours can cover the face, so the QA has no line to check
        fs_qa = facesafe.main_qa(str(out), plan["sidecar"] if plan.get("ass") else [],
                                 fs_place if plan.get("ass") else {**fs_place, "blocked": []}, offset=take.COVER_SECS)
        fs_qa.update(placement={k: fs_place[k] for k in ("moved_for_text", "moved_for_face")}, blocked=fs_place["blocked"][:10])
        if fs_qa["status"] != "ok":
            print(f"face-safe: clip {m.get('id', '')}: {fs_qa.get('reason')}", flush=True)
        data = out.read_bytes()
        info = take.probe(str(out))
        from ...preflight import parse_ffprobe
        pf = parse_ffprobe(info["raw"])
        pf.update(kind="video", bytes=len(data))
        modes = [s["layout"] for s in plan["shots"]]
        ys = [captions_ass.band_y(s, H) for s in plan["sidecar"]] if plan.get("ass") else []
        meta = {"type": "video", "width": W, "height": H, "duration": round(info["duration"], 2), "params": stills._pub(p),
                "has_logo": True, "has_end_card": has_end, "headline": m.get("hook", ""), "credit": "own video",
                "cover_offset_ms": int(take.COVER_SECS * 1000 / 2),
                "layout": "track" if set(modes) == {"track"} else ("mixed" if len(set(modes)) > 1 else modes[0]),
                "shots": [{"start": round(start + s["start"], 3), "end": round(start + s["end"], 3), "mode": s["layout"]} for s in plan["shots"]],
                "layout_plan": [{"scene": i, **s} for i, s in enumerate(plan["plan"])], "layout_sidecar": plan["sidecar"],
                "layout_detector": plan["detector"], "layout_vision": plan.get("vision"), "caption_preset": preset,
                "face_coverage": plan["face_coverage"], "moment": {k: m.get(k) for k in ("start", "end")},
                "caption_band": [min(ys) - 60, max(ys) + 60] if ys else None, "probe": pf, "timings": plan["timings"],
                "captions_moved_for_text": moved, "face_safe": fs_qa,
                "audio": {"loudnorm": take.LOUDNESS, "fade_in": take.FADE_IN, "fade_out": take.FADE_OUT} if audio else None,
                "punch_in": plan.get("punch") or {"on": False, "shots": 0},
                "face_fallback": "no face found, full frame used" if any(s.get("rule") == "R8-noface" for s in plan["shots"]) else None}
        if meta["face_fallback"]:
            print(f"render: clip {m.get('id', '')} {meta['face_fallback']}", flush=True)
        return data, cover, meta
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
