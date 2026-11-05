"""Pause & filler trim and two-pass loudness for One take clips (worker side: stdlib + the ffmpeg program only).

  plan_cuts(words, start, end, silences)  pure: which stretches of the span to keep. Long pauses between words shrink to
                                          PAD either side of speech; filler words (um / uh / erm / hmm, and "like" only
                                          when it stands between pauses or commas) go. A cut never starts or ends
                                          inside a kept word, and keeps PAD (80 ms) next to it.
  remap / remap_words / remap_points      original time -> trimmed time, so captions, face samples and scene cuts stay
                                          in sync with the trimmed video.
  cut(path, keep, out)                    ffmpeg trim/atrim + concat (10 ms fades at every join: no clicks).
  loudnorm_filter(path, start, dur)       two-pass EBU R128: measure the span, then the linear loudnorm filter string
                                          for -14 LUFS integrated / -1 dBTP. None when the measurement fails (the
                                          caller keeps the one-pass filter).
  prepare(...)                            the whole step for take.render_clip; None means "render the span as it is"
                                          (fail open: a trim never fails a clip).

Conservative on purpose: only the word timestamps the ASR already produced are used (no second model), a pause cut
needs the silence detector to agree when its output is available, and a plan that would remove half the span or more
is dropped as suspicious (music, a demo with long silent screen work).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0
LOUDNESS_1PASS = f"loudnorm=I={TARGET_I:g}:TP={TARGET_TP:g}:LRA={TARGET_LRA:g}"
PAD = 0.08            # speech kept either side of every cut
MIN_PAUSE = 0.6       # a gap between two words longer than this is a pause worth trimming
MIN_CUT = 0.12        # shorter removals aren't worth a join
LIKE_GAP = 0.15       # "like" counts as filler only between pauses this long (or commas)
MAX_REMOVED = 0.5     # a plan removing this share of the span or more is dropped (fail open)
MAX_SEGMENTS = 120    # joins per clip (filter graph size); the smallest cuts are dropped past this
JOIN_FADE = 0.01
FILLERS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "er", "hmm", "hm", "mm"})
_NOT_FILLER_AFTER_LIKE = frozenset({"a", "an", "the", "this", "that", "these", "those", "to", "it", "him", "her", "them",
                                    "me", "you", "us", "my", "your", "his", "our", "their"})


def _tok(w: dict) -> str:
    return re.sub(r"[^a-z']+", "", str(w.get("w", "")).lower())


def _raw(w: dict) -> str:
    return str(w.get("w", "")).strip()


def _is_filler(ws: list[dict], i: int, start: float, end: float) -> bool:
    t = _tok(ws[i])
    if t in FILLERS:
        return True
    if t != "like":
        return False
    prev_e = float(ws[i - 1]["e"]) if i > 0 else start
    next_s = float(ws[i + 1]["s"]) if i + 1 < len(ws) else end
    before = (i > 0 and _raw(ws[i - 1]).endswith((",", ".", "?", "!", ";", ":"))) or float(ws[i]["s"]) - prev_e >= LIKE_GAP \
        or (i > 0 and _tok(ws[i - 1]) in FILLERS)
    after = _raw(ws[i]).endswith((",", "...", "…")) or next_s - float(ws[i]["e"]) >= LIKE_GAP
    if i + 1 < len(ws) and _tok(ws[i + 1]) in _NOT_FILLER_AFTER_LIKE and not _raw(ws[i]).endswith(","):
        return False  # "like a dream", "like this": a real use
    return before and after


def _merge(iv: list[list[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _intersect(a: float, b: float, sil: list[list[float]]) -> list[list[float]]:
    return [[max(a, s), min(b, e)] for s, e in sil if min(b, e) - max(a, s) > 0]


def plan_cuts(words: list[dict], start: float, end: float, silences: list | None = None) -> dict:
    """Keep segments (absolute seconds) for the span [start, end]. silences: [[s, e], ...] from ffmpeg silencedetect, or
    None when there is no detector output (then the word gaps alone decide). Returns {keep, removed, pauses, fillers,
    skipped}; keep == [[start, end]] when nothing is cut."""
    start, end = float(start), float(end)
    span = end - start
    base = {"keep": [[round(start, 3), round(end, 3)]], "removed": 0.0, "pauses": 0, "fillers": 0, "skipped": ""}
    ws = []
    for w in words or []:
        try:
            s, e = float(w["s"]), float(w["e"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(s) and math.isfinite(e)) or e < s or s < start - 0.05 or e > end + 0.05:
            continue
        ws.append({**w, "s": max(s, start), "e": min(e, end)})
    ws.sort(key=lambda w: (w["s"], w["e"]))
    if span <= 0 or not ws:
        return {**base, "skipped": "no words"}
    sil = None
    if silences is not None:
        sil = []
        for x in silences:
            if isinstance(x, (list, tuple)) and len(x) == 2:
                try:
                    a, b = float(x[0]), float(x[1])
                except (TypeError, ValueError):
                    continue
                if math.isfinite(a) and math.isfinite(b) and b > a:
                    sil.append([a, b])
    cuts: list[list[float]] = []
    pauses = fillers = 0
    filler_idx = {i for i in range(len(ws)) if _is_filler(ws, i, start, end)}
    kept = [w for i, w in enumerate(ws) if i not in filler_idx]
    # fillers: the word and the pause around it go; PAD stays next to the neighbouring kept words
    for i in sorted(filler_idx):
        prev_e = max([w["e"] for w in kept if w["e"] <= ws[i]["s"] + 1e-6] or [start])
        next_s = min([w["s"] for w in kept if w["s"] >= ws[i]["e"] - 1e-6] or [end])
        a = max(prev_e, min(prev_e + PAD, ws[i]["s"])) if prev_e > start else max(start, ws[i]["s"] - PAD)
        b = min(next_s, max(next_s - PAD, ws[i]["e"])) if next_s < end else min(end, ws[i]["e"] + PAD)
        if b - a >= MIN_CUT:
            cuts.append([a, b])
            fillers += 1
    # pauses between kept words (and before the first / after the last word)
    edges = [start] + [x for w in kept for x in (w["s"], w["e"])] + [end]
    for k in range(0, len(edges) - 1, 2):
        g0, g1 = edges[k], edges[k + 1]
        if g1 - g0 <= MIN_PAUSE:
            continue
        a = g0 if k == 0 else g0 + PAD
        b = g1 if k + 1 == len(edges) - 1 else g1 - PAD
        pieces = [[a, b]] if sil is None else _intersect(a, b, sil)
        got = False
        for pa, pb in pieces:
            if pb - pa >= MIN_CUT:
                cuts.append([pa, pb])
                got = True
        pauses += got
    cuts = [c for c in _merge(cuts) if c[1] - c[0] >= MIN_CUT]
    if not cuts:
        return {**base, "skipped": "nothing to trim"}
    if len(cuts) >= MAX_SEGMENTS:  # keep the biggest cuts only
        cuts = sorted(sorted(cuts, key=lambda c: c[1] - c[0], reverse=True)[:MAX_SEGMENTS - 1])
    removed = sum(b - a for a, b in cuts)
    if removed >= MAX_REMOVED * span:
        return {**base, "skipped": f"would remove {removed:.1f} of {span:.1f} s"}
    keep, cur = [], start
    for a, b in cuts:
        if a > cur + 1e-6:
            keep.append([round(cur, 3), round(a, 3)])
        cur = max(cur, b)
    if end > cur + 1e-6:
        keep.append([round(cur, 3), round(end, 3)])
    keep = [k for k in keep if k[1] - k[0] > 0.02]
    return {"keep": keep, "removed": round(span - sum(b - a for a, b in keep), 2), "pauses": pauses, "fillers": fillers,
            "skipped": ""}


def remap(t: float, keep: list[list[float]]) -> float | None:
    """Original time -> trimmed time (0 = the first kept instant). None when t falls in a removed stretch."""
    acc = 0.0
    for a, b in keep:
        if t < a - 1e-6:
            return None
        if t <= b + 1e-6:
            return round(acc + min(max(t - a, 0.0), b - a), 3)
        acc += b - a
    return None


def remap_words(words: list[dict], keep: list[list[float]]) -> list[dict]:
    """Timed words on the trimmed timeline; a word that was cut (a filler) is dropped."""
    out = []
    for w in words or []:
        try:
            s, e = float(w["s"]), float(w["e"])
        except (KeyError, TypeError, ValueError):
            continue
        mid = (s + e) / 2
        seg = next((k for k in keep if k[0] - 1e-6 <= mid <= k[1] + 1e-6), None)
        if seg is None:
            continue
        ns, ne = remap(min(max(s, seg[0]), seg[1]), keep), remap(min(max(e, seg[0]), seg[1]), keep)
        if ns is None or ne is None:
            continue
        out.append({**w, "s": ns, "e": max(ne, ns)})
    return out


def remap_points(items: list, keep: list[list[float]], key: str | None = None) -> list:
    """Scene cut times (key None) or face samples ({t: ...}, key 't') on the trimmed timeline."""
    out = []
    for x in items or []:
        try:
            t = float(x[key] if key else x)
        except (KeyError, TypeError, ValueError):
            continue
        nt = remap(t, keep)
        if nt is not None:
            out.append({**x, key: nt} if key else nt)
    return out


def _filter_graph(keep: list[list[float]], offset: float, audio: bool) -> str:
    parts, labels = [], []
    for i, (a, b) in enumerate(keep):
        a, b = a - offset, b - offset
        parts.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        lab = f"[v{i}]"
        if audio:
            d = b - a
            fo = max(0.0, d - JOIN_FADE)
            parts.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,"
                         f"afade=t=in:st=0:d={JOIN_FADE},afade=t=out:st={fo:.3f}:d={JOIN_FADE}[a{i}]")
            lab += f"[a{i}]"
        labels.append(lab)
    parts.append("".join(labels) + f"concat=n={len(keep)}:v=1:a={1 if audio else 0}[v]" + ("[a]" if audio else ""))
    return ";".join(parts)


def cut(path: str, keep: list[list[float]], out: str, audio: bool = True, timeout: int = 900) -> None:
    """Write only the kept stretches of `path` to `out` (re-encoded near-lossless; the clip render encodes once more)."""
    from .. import take
    lo, hi = keep[0][0], keep[-1][1]
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}", "-i", path,
           "-filter_complex", _filter_graph(keep, lo, audio), "-map", "[v]"]
    if audio:
        cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    take.run(cmd, timeout=timeout)


def _measured(stderr: str) -> dict | None:
    m = None
    for m in re.finditer(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr or ""):
        pass
    if not m:
        return None
    try:
        j = json.loads(m.group(0))
        vals = {k: float(j[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in vals.values()) or vals["input_i"] < -70:
        return None  # silence or a broken stream: the one-pass filter is the safe choice
    return vals


def measure(path: str, start: float, dur: float, timeout: int = 300) -> dict | None:
    """Pass one of EBU R128 loudnorm over the span: its measured loudness, or None."""
    from .. import mediasafe
    try:
        r = mediasafe.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{float(start):.3f}", "-t", f"{float(dur):.3f}", "-i", path,
                           "-vn", "-af", f"{LOUDNESS_1PASS}:print_format=json", "-f", "null", "-"], timeout=timeout, text=True)
    except Exception:  # noqa: BLE001 - a failed measurement falls back to the one-pass filter
        return None
    if r.returncode != 0:
        return None
    return _measured(r.stderr)


def loudnorm_filter(path: str, start: float, dur: float) -> str:
    """The loudness filter for the span: two-pass linear loudnorm when it can be measured, else the one-pass filter."""
    v = measure(path, start, dur)
    if not v:
        return LOUDNESS_1PASS
    return (f"{LOUDNESS_1PASS}:measured_I={v['input_i']:.2f}:measured_TP={v['input_tp']:.2f}:measured_LRA={v['input_lra']:.2f}"
            f":measured_thresh={v['input_thresh']:.2f}:offset={v['target_offset']:.2f}:linear=true")


def prepare(path: str, m: dict, words: list[dict], faces: list[dict], scenes: list[float], src: dict, workdir: str) -> dict:
    """The trim step for one clip. Returns {"stats": {...}} always, plus path/m/words/faces/scenes/src for the trimmed
    file when something was cut. Any failure keeps the span as it is (stats.skipped says why)."""
    start, end = float(m["start"]), float(m["end"])
    stats = {"on": True, "trimmed_s": 0.0, "pauses": 0, "fillers": 0, "skipped": ""}
    try:
        plan = plan_cuts(words, start, end, m.get("silences") if isinstance(m.get("silences"), list) else None)
    except Exception as e:  # noqa: BLE001
        return {"stats": {**stats, "skipped": f"plan failed: {e.__class__.__name__}"}}
    if plan["skipped"]:
        return {"stats": {**stats, "skipped": plan["skipped"]}}
    keep = plan["keep"]
    out = str(Path(workdir) / "trimmed.mp4")
    try:
        cut(path, keep, out, audio=bool(src.get("audio", True)))
    except Exception as e:  # noqa: BLE001 - fail open: the untrimmed span renders
        return {"stats": {**stats, "skipped": f"cut failed: {e.__class__.__name__}"}}
    new_dur = round(sum(b - a for a, b in keep), 3)
    m2 = {k: v for k, v in m.items() if k != "silences"}
    m2.update(start=0.0, end=new_dur)
    return {"path": out, "m": m2, "words": remap_words(words, keep), "faces": remap_points(faces, keep, "t"),
            "scenes": remap_points(scenes, keep), "src": {**src, "duration": new_dur}, "keep": keep,
            "stats": {**stats, "trimmed_s": plan["removed"], "pauses": plan["pauses"], "fillers": plan["fillers"]}}
