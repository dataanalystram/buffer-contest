"""Render a One take clip with per-shot layouts: one ffmpeg pass for cover + body (layouts, captions, logo) + end card.

Filter graphs, all at 1080x1920 (H.264 veryfast, CRF 20, yuv420p, +faststart, AAC after loudness):

  track   sendcmd-driven crop following the face, lanczos up to 1080x1920
  fit     the whole frame, rounded corners, over a darkened, saturated blur of itself (blurred small at 270x480 and
          scaled up, so it's cheap) or over the brand colour, with a vignette on the background
  split   two 9:8 face crops stacked (the left person on top), a brand-accent divider on the seam
  screen  PiP: the screen as a rounded card over the blur, the webcam face as a circle with an accent ring under it;
          stacked: the screen fitted into the top half, the face crop below, accent divider on the seam

Masks (rounded rectangles, circles, rings) are made once per size with ``geq`` and cached on disk.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from . import decide, faces as faces_mod, path as path_mod, screen as screen_mod, shots as shots_mod
from .decide import OUT_H, OUT_W

ACCENT = "#C9ACFA"
GUTTER = 8
RADIUS = 40
VENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30"]
AENC = ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]
MASK_DIR = Path(os.environ.get("HQ_MASK_DIR") or (Path(tempfile.gettempdir()) / "hq_layout_masks"))


def _hex(c: str | None) -> str:
    c = (c or ACCENT).lstrip("#")
    return c.upper() if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c) else ACCENT.lstrip("#")


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _ff(args: list[str], timeout: int = 1800) -> None:
    from ... import mediasafe
    r = mediasafe.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], timeout=timeout, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg failed: " + (r.stderr or "")[-600:])


# ---------------------------------------------------------------------- masks (made once)
def mask(kind: str, w: int, h: int, r: int = RADIUS, color: str | None = None) -> str:
    """A PNG mask: 'round' (rounded rectangle, grey), 'circle' (grey), 'ring' (an RGBA disc in `color`)."""
    MASK_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(f"{kind}:{w}:{h}:{r}:{color}".encode()).hexdigest()[:12]
    out = MASK_DIR / f"{kind}_{w}x{h}_{key}.png"
    if out.exists() and out.stat().st_size > 0:
        return str(out)
    if kind == "round":
        expr = (f"if(gt(abs(X-W/2)\\,W/2-{r})*gt(abs(Y-H/2)\\,H/2-{r})\\,"
                f"if(lte(hypot(abs(X-W/2)-(W/2-{r})\\,abs(Y-H/2)-(H/2-{r}))\\,{r})\\,255\\,0)\\,255)")
        vf = f"format=gray,geq=lum='{expr}'"
    elif kind == "circle":
        vf = f"format=gray,geq=lum='if(lte(hypot(X-{w / 2}\\,Y-{h / 2})\\,{w / 2 - 2})\\,255\\,0)'"
    else:
        c = _hex(color)
        rr, gg, bb = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
        vf = (f"format=rgba,geq=r={rr}:g={gg}:b={bb}:a='if(lte(hypot(X-{w / 2}\\,Y-{h / 2})\\,{w / 2 - 1})\\,255\\,0)'")
    tmp = out.with_suffix(f".{os.getpid()}.png")
    _ff(["-f", "lavfi", "-i", f"color=black:s={w}x{h}", "-frames:v", "1", "-vf", vf, str(tmp)], timeout=120)
    os.replace(tmp, out)
    return str(out)


def _fit(sw: int, sh: int, bw: int, bh: int) -> tuple[int, int]:
    s = min(bw / sw, bh / sh)
    return _even(sw * s), _even(sh * s)


# ---------------------------------------------------------------------- graphs per shot
class Graph:
    def __init__(self):
        self.inputs: list[str] = []   # extra input files (masks), after the cover, the source and the end card
        self.parts: list[str] = []

    def inp(self, path: str, base: int) -> str:
        self.inputs.append(path)
        return f"[{base + len(self.inputs) - 1}:v]"


def blur_bg(src: str, out: str, dark: float = -0.08) -> str:
    return (f"{src}scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=10:2,"
            f"eq=brightness={dark}:saturation=1.2,scale={OUT_W}:{OUT_H},vignette=PI/5,setsar=1{out}")


def seg_graph(g: Graph, k: int, seg: dict, src: tuple[int, int], base: int, tmp: Path, accent: str, brand_bg: str) -> str:
    """Filter chain for shot k from [s{k}] (already trimmed, 30 fps) to [o{k}]. Returns the chain."""
    sw, sh = src
    lay = seg["layout"]
    acc = "0x" + _hex(accent)
    if lay == "track":
        p = seg["path"]
        cmd = tmp / f"track{k}.cmd"
        cmd.write_text(path_mod.sendcmd(p, f"crop@c{k}") or f"0.0 crop@c{k} x {p['keys'][0][1]};\n", encoding="utf-8")
        x0, y0 = p["keys"][0][1], p["keys"][0][2]
        return (f"[s{k}]sendcmd=f='{_esc_path(cmd)}',crop@c{k}=w={p['crop_w']}:h={p['crop_h']}:x={x0}:y={y0},"
                f"scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[o{k}]")
    if lay == "fit":
        fw, fh = _fit(sw, sh, OUT_W - 2 * GUTTER, OUT_H - 2 * GUTTER)
        m = g.inp(mask("round", fw, fh), base)
        if seg.get("bg") == "brand":
            bg = f"color=c=0x{_hex(brand_bg)}:s={OUT_W}x{OUT_H}:r=30,format=yuv420p[bg{k}];"
            pre = f"[s{k}]copy[f{k}];"
            over = f"[bg{k}][fr{k}]overlay=(W-w)/2:(H-h)/2:shortest=1"
        else:
            bg = ""
            pre = f"[s{k}]split[b{k}][f{k}];" + blur_bg(f"[b{k}]", f"[bg{k}]") + ";"
            over = f"[bg{k}][fr{k}]overlay=(W-w)/2:(H-h)/2"
        return (bg + pre + f"[f{k}]scale={fw}:{fh}:flags=lanczos,format=yuva420p[fa{k}];[fa{k}]{m}alphamerge[fr{k}];"
                + over + f",setsar=1[o{k}]")
    if lay == "split":
        (t1, t2) = seg["panels"]
        return (f"[s{k}]split[a{k}][b{k}];[a{k}]crop={t1[2]}:{t1[3]}:{t1[0]}:{t1[1]},scale={OUT_W}:{OUT_H // 2}:flags=lanczos[t{k}];"
                f"[b{k}]crop={t2[2]}:{t2[3]}:{t2[0]}:{t2[1]},scale={OUT_W}:{OUT_H // 2}:flags=lanczos[u{k}];"
                f"[t{k}][u{k}]vstack,drawbox=x=0:y={OUT_H // 2 - 3}:w={OUT_W}:h=6:color={acc}@1:t=fill,setsar=1[o{k}]")
    # screen
    fx, fy, fs = seg["face_box"]
    if seg.get("style") == "stack":
        tw, th = _fit(sw, sh, OUT_W, OUT_H // 2)
        pw, ph = fs, _even(fs * 8 / 9)
        return (f"[s{k}]split=3[a{k}][b{k}][c{k}];[a{k}]scale={OUT_W}:{OUT_H // 2}:force_original_aspect_ratio=increase,"
                f"crop={OUT_W}:{OUT_H // 2},boxblur=12:2,eq=brightness=-0.15[tb{k}];[b{k}]scale={tw}:{th}:flags=lanczos[tf{k}];"
                f"[tb{k}][tf{k}]overlay=(W-w)/2:(H-h)/2[t{k}];"
                f"[c{k}]crop={pw}:{ph}:{fx}:{max(0, min(sh - ph, fy))},scale={OUT_W}:{OUT_H // 2}:flags=lanczos[u{k}];"
                f"[t{k}][u{k}]vstack,drawbox=x=0:y={OUT_H // 2 - 3}:w={OUT_W}:h=6:color={acc}@1:t=fill,setsar=1[o{k}]")
    cw, chh = _fit(sw, sh, OUT_W - 80, 1000)
    rm = g.inp(mask("round", cw, chh), base)
    cm = g.inp(mask("circle", 420, 420), base)
    ring = g.inp(mask("ring", 436, 436, color=accent), base)
    card_y = 300
    return (f"[s{k}]split=3[a{k}][b{k}][c{k}];" + blur_bg(f"[a{k}]", f"[bg{k}]", -0.15) + ";"
            f"[b{k}]scale={cw}:{chh}:flags=lanczos,format=yuva420p[sc{k}];[sc{k}]{rm}alphamerge[sr{k}];"
            f"[c{k}]crop={fs}:{fs}:{fx}:{fy},scale=420:420:flags=lanczos,format=yuva420p[fc{k}];[fc{k}]{cm}alphamerge[fr{k}];"
            f"[bg{k}][sr{k}]overlay=(W-w)/2:{card_y}[l1{k}];[l1{k}]{ring}overlay=(W-w)/2:1198[l2{k}];"
            f"[l2{k}][fr{k}]overlay=(W-w)/2:1206,setsar=1[o{k}]")


def _esc_path(p: Path | str) -> str:
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


# ---------------------------------------------------------------------- the plan
def _speech(words: list[dict], start: float) -> list[tuple[float, float]]:
    return [(float(w["s"]) - start, float(w["e"]) - start) for w in words or []]


def plan_clip(path: str, start: float, end: float, words: list[dict], scenes: list[float] | None, src: dict,
              fallback_faces: list[dict] | None = None, pins: dict[int, dict] | None = None, prefs: dict | None = None,
              detect_shots: bool = False, smart_layout: bool = False) -> dict:
    """{shots: [...with render geometry], sidecar, plan (public), detector, timings} for the clip [start, end]."""
    t0 = time.time()
    sw, sh = int(src.get("width") or 1920), int(src.get("height") or 1080)
    cuts = list(scenes or [])
    if detect_shots and not [c for c in cuts if start < c < end]:
        more = shots_mod.detect(path, start, end)
        cuts += more or []
    cut_spans = [(a - start, b - start) for a, b in shots_mod.shots_in(cuts, start, end)]
    # a layout may change at a cut or at a sentence boundary: long shots are split at the sentence ends inside them
    pieces = shots_mod.split_at_sentences(cut_spans, shots_mod.sentence_bounds(words, start), decide.MIN_HOLD)
    spans, parent = [(a, b) for a, b, _ in pieces], [k for _, _, k in pieces]
    try:
        smp = faces_mod.sample(path, start, end, sw, sh)
        screen_fn = (lambda a, b, boxes: screen_mod.signals(screen_mod.pick_thumbs(smp["thumbs"], a, b), boxes))
    except faces_mod.FaceModelError as e:
        print(f"layouts: YuNet unavailable ({e}); using the analysis' face samples", flush=True)
        smp = faces_mod.from_analysis(fallback_faces, start, end, sw, sh)
        screen_fn = None
    t_faces = time.time() - t0
    tracks = faces_mod.track(smp["samples"])
    sigs = decide.signals_for(spans, smp["samples"], tracks, screen_fn, _speech(words, start), smp["fps"],
                              [cut_spans[k][1] - cut_spans[k][0] for k in parent])
    shots = decide.decide(sigs, (sw, sh), prefs, pins, smp["fps"])
    vis = None
    from . import vision  # optional free vision picker: off by default, shadow logs only, on applies (never over a pin)
    if vision.mode() != "off" and smart_layout:
        vis = vision.pick_for_clip(path, start, end, sw, sh)
        if vis and vision.mode() == "on":
            shots, vis["applied"] = vision.apply(shots, sigs, vis["category"])
    by_id = {t["id"]: t for t in tracks}
    public, side = decide.public(shots), decide.sidecar(shots)
    # render: sentence pieces of one shot that kept the same layout are drawn as one segment (one crop path, no seam)
    rshots, rspans = merge_pieces(shots, parent)
    rsigs = decide.signals_for(rspans, smp["samples"], tracks, None, _speech(words, start), smp["fps"]) if rspans != spans else sigs
    cover = []
    for s, sig in zip(rshots, rsigs):
        geom(s, sig, by_id, (sw, sh))
        if s["layout"] == "track" and s.get("path"):
            pts = [p for fid in s.get("faces") or [] for p in by_id.get(fid, {"points": []})["points"] if s["start"] <= p[0] < s["end"]]
            cover.append((path_mod.coverage(s["path"], pts, s["start"], sw, sh), s["end"] - s["start"]))
    cov = (sum(c * d for c, d in cover) / sum(d for _, d in cover)) if cover else None
    return {"shots": rshots, "sidecar": side, "plan": public, "detector": smp["detector"], "vision": vis,
            "face_coverage": round(cov, 3) if cov is not None else None, "timings": {"faces": round(t_faces, 2)},
            "signals": [{k: v for k, v in s.items() if k != "faces"} | {"faces": [{k: t[k] for k in ("id", "cx", "cy", "h", "presence", "motion")
                                                                                   if k in t} for t in s["faces"]]} for s in sigs]}


def merge_pieces(shots: list[dict], parent: list[int]) -> tuple[list[dict], list[tuple[float, float]]]:
    """Join neighbouring pieces of the same cut-shot that ended up with the same layout and options."""
    out, par = [], []
    for s, k in zip(shots, parent):
        if out and par[-1] == k and _same_look(out[-1], s):
            out[-1] = {**out[-1], "end": s["end"]}
            continue
        out.append(dict(s))
        par.append(k)
    return out, [(s["start"], s["end"]) for s in out]


def _same_look(a: dict, b: dict) -> bool:
    keys = ("layout", "bg", "style", "pinned")
    return all(a.get(k) == b.get(k) for k in keys) and (a["layout"] != "track" or a.get("faces") == b.get("faces"))


def geom(s: dict, sig: dict, by_id: dict, src: tuple[int, int]) -> None:
    """Render geometry for one decided shot (in place). Falls back to fit when a needed face is missing."""
    sw, sh = src
    faces_in = {t["id"]: t for t in sig["faces"]}
    if s["layout"] == "track":
        ids = [i for i in s.get("faces") or [] if i in faces_in] or ([max(sig["faces"], key=lambda t: t["h"] * t["presence"])["id"]]
                                                                     if sig["faces"] else [])
        if not ids:
            s["path"] = path_mod.plan([], 0, s["end"] - s["start"], sw, sh)
            return
        pts = [[p for p in by_id[i]["points"] if s["start"] <= p[0] < s["end"]] for i in ids]
        series = path_mod.centre_series(pts[0], pts if len(pts) == 2 else None)
        s["faces"] = ids
        s["path"] = path_mod.plan(series, s["start"], s["end"], sw, sh)
    elif s["layout"] == "split":
        people = sorted(sig["faces"], key=lambda t: t["cx"])[:2]
        if len(people) < 2:
            s.update(layout="fit", bg="blur")
            return
        s["panels"] = [panel(t, sw, sh) for t in people]  # left person on top, by screen position
    elif s["layout"] == "screen":
        cams = [t for t in sig["faces"] if decide.is_webcam(t)] or sig["faces"]
        if not cams:
            s.update(layout="fit", bg="blur")
            return
        t = max(cams, key=lambda t: t["h"])
        fs = _even(min(sh, sw, max(64, t["h"] * sh * 1.7)))
        fx = int(min(sw - fs, max(0, t["cx"] * sw - fs / 2)))
        fy = int(min(sh - fs, max(0, t["cy"] * sh - fs * 0.45)))
        s["face_box"] = (fx, fy, fs)
        s.setdefault("style", "pip")


def panel(t: dict, sw: int, sh: int) -> tuple[int, int, int, int]:
    """A 9:8 crop (x, y, w, h) around one face for a 1080x960 split panel, the face at 45% of the panel height."""
    ch = min(sh, max(OUT_H / 2 / decide.UPSCALE_MAX, t["h"] * sh / 0.34))
    cw = ch * 9 / 8
    if cw > sw:
        cw, ch = sw, sw * 8 / 9
    cw, ch = _even(cw), _even(ch)
    x = int(min(sw - cw, max(0, t["cx"] * sw - cw / 2)))
    y = int(min(sh - ch, max(0, t["cy"] * sh - ch * 0.45)))
    return x, y, cw, ch


def pins_from(overrides) -> dict[int, dict]:
    """{shot index: pin} from a clip's saved overrides ({"<index>": layout | {layout, bg, style}} or a list)."""
    out = {}
    if isinstance(overrides, dict):
        items = overrides.items()
    elif isinstance(overrides, list):
        items = [(o.get("scene"), o) for o in overrides if isinstance(o, dict)]
    else:
        return out
    for k, v in items:
        try:
            i = int(k)
        except (TypeError, ValueError):
            continue
        pin = decide.clean_pin(v)
        if pin and 0 <= i < 500:
            out[i] = pin
    return out


# ---------------------------------------------------------------------- render
def render_body_cmd(path: str, start: float, dur: float, shots: list[dict], src: tuple[int, int], ass_path: str | None,
                    logo_png: str | None, out: str, tmp: Path, accent: str, brand_bg: str, audio: str | None,
                    cover: str | None = None, cover_s: float = 0.0, end: str | None = None, end_s: float = 0.0) -> list[str]:
    """The ffmpeg arguments for [cover] + body + [end card] in one encode."""
    ins = []
    idx = 0
    if cover:
        ins += ["-loop", "1", "-framerate", "30", "-t", f"{cover_s:.3f}", "-i", cover]
        ci = idx
        idx += 1
    ins += ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path]
    si = idx
    idx += 1
    if end:
        ins += ["-loop", "1", "-framerate", "30", "-t", f"{end_s:.3f}", "-i", end]
        ei = idx
        idx += 1
    if logo_png:
        ins += ["-i", logo_png]
        li = idx
        idx += 1
    g = Graph()
    n = len(shots)
    parts = [f"[{si}:v]fps=30,setpts=PTS-STARTPTS,split={n}" + "".join(f"[r{k}]" for k in range(n)) if n > 1 else
             f"[{si}:v]fps=30,setpts=PTS-STARTPTS[r0]"]
    for k, s in enumerate(shots):
        parts.append(f"[r{k}]trim=start={s['start']:.3f}:end={s['end']:.3f},setpts=PTS-STARTPTS[s{k}]")
        parts.append(seg_graph(g, k, s, src, idx, tmp, accent, brand_bg))
    parts.append("".join(f"[o{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p[body0]")
    cur = "[body0]"
    if ass_path:
        fonts = _esc_path(Path(__file__).resolve().parents[2] / "fonts")
        parts.append(f"{cur}ass=filename='{_esc_path(ass_path)}':fontsdir='{fonts}'[body1]")
        cur = "[body1]"
    if logo_png:
        parts.append(f"{cur}[{li}:v]overlay=0:0,format=yuv420p[body2]")
        cur = "[body2]"
    parts.append(f"{cur}setsar=1[bodyv]")
    parts.append(f"[{si}:a]{audio}[bodya]" if audio else f"anullsrc=r=44100:cl=stereo,atrim=0:{dur:.3f}[bodya]")
    seq = []
    if cover:
        parts.append(f"[{ci}:v]scale={OUT_W}:{OUT_H},setsar=1,fps=30,format=yuv420p[cv]")
        parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{cover_s:.3f}[ca]")
        seq.append("[cv][ca]")
    seq.append("[bodyv][bodya]")
    if end:
        parts.append(f"[{ei}:v]scale={OUT_W}:{OUT_H},setsar=1,fps=30,format=yuv420p[ev]")
        parts.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{end_s:.3f}[ea]")
        seq.append("[ev][ea]")
    parts.append("".join(seq) + f"concat=n={len(seq)}:v=1:a=1[v][a]")
    graph = ";".join(parts)
    gfile = tmp / "graph.txt"
    gfile.write_text(graph, encoding="utf-8")
    extra = []
    for m in g.inputs:
        extra += ["-i", m]
    return [*ins, *extra, "-filter_complex_script", str(gfile), "-map", "[v]", "-map", "[a]", *VENC, *AENC,
            "-movflags", "+faststart", out]


def render(path: str, start: float, end: float, words: list[dict], src: dict, out: str, *, kit: dict | None = None,
           p: dict | None = None, preset: str | None = None, logo_png: str | None = None, plan: dict | None = None,
           scenes: list[float] | None = None, fallback_faces: list[dict] | None = None, pins=None, prefs: dict | None = None,
           audio: str | None = None, cover: str | None = None, cover_s: float = 0.0, end_card: str | None = None,
           end_s: float = 0.0) -> dict:
    """Render the clip [start, end] to `out` (1080x1920). Returns {plan, sidecar, timings, ass}."""
    from .. import captions_ass
    t0 = time.time()
    plan = plan or plan_clip(path, start, end, words, scenes, src, fallback_faces, pins_from(pins), prefs)
    tmp = Path(tempfile.mkdtemp(prefix="hq_layout_"))
    try:
        colors = (p or {}).get("colors") or (kit or {}).get("colors") or {}
        accent = colors.get("accent") or ACCENT
        brand_bg = colors.get("primary") or "#0B0B12"
        ass = captions_ass.build(words, start, kit, p, preset, plan["sidecar"]) if words else ""
        ass_path = None
        if ass:
            ass_path = str(tmp / "cap.ass")
            Path(ass_path).write_text(ass, encoding="utf-8")
        t1 = time.time()
        _ff(render_body_cmd(path, start, end - start, plan["shots"], (int(src.get("width") or 1920), int(src.get("height") or 1080)),
                            ass_path, logo_png, out, tmp, accent, brand_bg, audio, cover, cover_s, end_card, end_s))
        plan["timings"] = {**plan.get("timings", {}), "render": round(time.time() - t1, 2), "total": round(time.time() - t0, 2)}
        plan["ass"] = ass
        return plan
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probe(out: str) -> dict:
    """{width, height, duration} of a rendered file (ffprobe)."""
    import json
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height:format=duration",
                        "-of", "json", out], capture_output=True, text=True, timeout=60)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    return {"width": st.get("width"), "height": st.get("height"), "duration": float((d.get("format") or {}).get("duration") or 0)}
