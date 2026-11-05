"""Shot cuts inside one clip span. Every layout decision is made per shot and never crosses a cut."""
from __future__ import annotations

MIN_SHOT = 0.5  # cuts closer than this to each other or to the clip edges are dropped (a flash, a fade)


def shots_in(scenes: list[float] | None, start: float, end: float, min_len: float = MIN_SHOT) -> list[tuple[float, float]]:
    """[(a, b)] in SOURCE seconds covering [start, end], split at the scene cuts that fall inside it."""
    start, end = float(start), float(end)
    bounds = [start]
    for c in sorted(float(x) for x in scenes or []):
        if start + min_len <= c <= end - min_len and c - bounds[-1] >= min_len:
            bounds.append(c)
    bounds.append(end)
    return [(round(a, 3), round(b, 3)) for a, b in zip(bounds, bounds[1:]) if b > a]


def detect(path: str, start: float, end: float, threshold: float = 27.0) -> list[float] | None:
    """Cuts between start and end with PySceneDetect's ContentDetector (BSD-3), in source seconds. None when
    PySceneDetect isn't installed here (the caller then uses the analysis' cuts)."""
    try:
        from scenedetect import ContentDetector, SceneManager, open_video  # BSD-3
    except Exception:  # noqa: BLE001 - optional on the slim render worker
        return None
    try:
        video = open_video(path)
        sm = SceneManager()
        sm.add_detector(ContentDetector(threshold=threshold))
        fps = video.frame_rate or 30.0
        video.seek(max(0.0, float(start)))
        sm.detect_scenes(video, end_time=int(round(float(end) * fps)), show_progress=False)
        return [round(s.get_seconds(), 3) for s, _ in sm.get_scene_list()[1:]]
    except Exception:  # noqa: BLE001 - a decoder hiccup: no extra cuts, never a failed render
        return None


SENTENCE_END = (".", "?", "!")


def sentence_bounds(words: list[dict] | None, start: float) -> list[float]:
    """Clip-time points where a sentence ends: halfway between a word ending in . ? ! and the next word's start."""
    ws = [w for w in words or [] if str(w.get("w", "")).strip()]
    out = []
    for i, w in enumerate(ws):
        if not str(w["w"]).strip().rstrip("\"')]").endswith(SENTENCE_END):
            continue
        e = float(w["e"]) - start
        nxt = float(ws[i + 1]["s"]) - start if i + 1 < len(ws) else e
        out.append(round((e + max(e, nxt)) / 2, 3))
    return out


def split_at_sentences(spans: list[tuple[float, float]], bounds: list[float], min_len: float) -> list[tuple[float, float, int]]:
    """[(a, b, shot index)]: each shot split at the sentence ends inside it, every piece at least ``min_len`` long, so a
    layout can change at a cut OR at a sentence boundary and nowhere else."""
    out = []
    for k, (a, b) in enumerate(spans):
        last = a
        for c in sorted(bounds):
            if c - last >= min_len and b - c >= min_len:
                out.append((round(last, 3), round(c, 3), k))
                last = c
        out.append((round(last, 3), round(b, 3), k))
    return out
