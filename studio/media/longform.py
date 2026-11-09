"""Long video -> several short clips, Opus-Clip style ("split this into 5 shorts", "find the best moments").

    plan = longform.parse_request("split this into 5 shorts https://youtu.be/...")
    moments = longform.pick_moments(words, duration, n=plan["n"])          # pure: no FFmpeg, no model
    out = longform.split_video("talk.mp4", words=words, n=5, ratios=["9:16"], brand=..., credit=..., official=True,
                               out_dir="clips/")                            # worker: FFmpeg

Each clip is a run of whole sentences (20-60 s by default) that stands on its own, with:

* a **score** (0-100) and a plain **reason** for why that moment was picked (strong opening line, a question or a
  number up front, clean ending, steady pace, little filler), so the person can pick the clips they want;
* a **hook title** on screen from frame 0: the strongest line the speaker actually said in the first sentences of the
  moment, cut to a few words (never invented);
* **word-level captions** (studio.media.captions, opus style), unless the source already has burned-in subtitles, in
  which case the subtitle band is cropped off before reframing (so captions never stack);
* **subject-aware reframing**: a face track (MediaPipe, on the worker) drives ``take.crop_plan`` per clip and per
  ratio; with no face on screen the whole frame is shown over a blurred copy of itself (screen recordings, slides);
* the brand's credit chip, Follow end card and per-channel aspect ratio (``styles.CHANNEL_RATIO``).

The transcript comes from ``subtitles.resolve_transcript`` first (an uploaded .srt/.vtt, the platform's captions,
a caption track in the file) and only falls back to Whisper when none exists.

Moment scoring reuses One take's deterministic scorer (studio/take.py) so both features agree on what a good moment
is; an optional model (``chat_json``) can rank candidates, and every span it returns is validated against the
transcript, exactly like One take.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from .. import take

MIN_S, MAX_S = 20.0, 60.0
MAX_CLIPS = 10
NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "a couple of": 2, "a few": 3, "several": 4, "a handful of": 4}
_N = r"(\d{1,2}|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + ")"
_UNIT = r"(?:shorts?|clips?|reels?|moments?|highlights?|tiktoks?|parts?|videos?|cuts?|pieces?)"
SPLIT_RX = [
    re.compile(rf"\b(?:split|cut|chop|break|slice|turn|make|clip|edit)\b[^.?!]{{0,60}}?\binto\s+{_N}\s+(?:\w+\s+)?{_UNIT}", re.I),
    re.compile(rf"\b(?:top|best|give me|make|create|pull|find|get)\s+(?:me\s+)?(?:the\s+)?{_N}\s+(?:\w+\s+)?{_UNIT}", re.I),
    re.compile(rf"\b{_N}\s+(?:\w+\s+)?{_UNIT}\s+(?:from|out of|of)\b", re.I),
]
INTENT_RX = re.compile(
    r"\b(?:best|top|key|viral|strongest|most interesting)\s+(?:\w+\s+)?(?:moments?|parts?|bits?|highlights?|clips?|quotes?)\b|"
    r"\bsplit\b.{0,40}\b(?:into|shorts?|clips?|reels?)\b|\bclip\s+(?:this|it|the)\b|\bhighlights?\b.{0,30}\b(?:of|from)\b|"
    # "opus clip this keynote" / "opus clips from this talk" split; "as an opus clip" / "opus style" name an edit style
    r"\bopus[\s-]?clip\s+(?:this|it|that|the|my)\b|\bopus[\s-]?(?:style\s+)?clips\b|"
    r"\binto\s+(?:\w+\s+)?shorts\b|\brepurpose\b.{0,40}\b(?:shorts?|clips?|reels?)\b", re.I)
LEN_RX = re.compile(r"\b(\d{2,3})\s*(?:-|–|to)\s*(\d{2,3})\s*(?:s|sec|secs|seconds)\b", re.I)
UNDER_RX = re.compile(r"\b(?:under|max(?:imum)?|at most|no longer than|up to)\s+(\d{2,3})\s*(?:s|sec|secs|seconds)\b", re.I)
EACH_RX = re.compile(r"\b(\d{2,3})\s*(?:s|sec|secs|seconds)\s+(?:each|clips?|shorts?|long)\b", re.I)


# ====================================================================== the request
def _num(tok: str) -> int | None:
    tok = tok.lower().strip()
    if tok.isdigit():
        return int(tok)
    return NUM_WORDS.get(tok)


def parse_request(prompt: str) -> dict:
    """What a split request asks for. Pure. {"split", "n", "lo", "hi", "urls"}; n None = pick 3-5 by length."""
    p = prompt or ""
    n = None
    for rx in SPLIT_RX:
        m = rx.search(p)
        if m:
            n = _num(m.group(1))
            break
    split = bool(n) or bool(INTENT_RX.search(p))
    lo, hi = MIN_S, MAX_S
    m = LEN_RX.search(p)
    if m:
        a, b = sorted((int(m.group(1)), int(m.group(2))))
        lo, hi = float(max(8, a)), float(min(180, b))
    else:
        m = UNDER_RX.search(p) or EACH_RX.search(p)
        if m:
            v = float(int(m.group(1)))
            hi = min(180.0, v)
            lo = min(lo, max(8.0, hi * 0.5))
            if m.re is EACH_RX:  # "30 s each": aim at that length
                lo = max(8.0, v - 10)
                hi = min(180.0, v + 10)
    if n is not None:
        n = max(1, min(MAX_CLIPS, n))
    from .find import urls_in
    return {"split": split, "n": n, "lo": lo, "hi": hi, "urls": urls_in(p)}


def default_count(duration: float) -> int:
    """3-5 clips by default: 3 for a short video, up to 5 for a long one."""
    if duration <= 0:
        return 3
    return max(3, min(5, round(duration / 240) + 3))


# ====================================================================== transcript -> sentences
def sentences(words: list[dict], pause: float = 0.6, max_words: int = 34) -> list[dict]:
    """take.sentences when the transcript has punctuation (Whisper, human captions); otherwise (auto-captions) cut
    on pauses, so a "sentence" is still something said in one breath."""
    from .subtitles import has_punctuation
    if has_punctuation(words):
        return take.sentences(words)
    out, cur = [], []
    for i, w in enumerate(words):
        if not str(w.get("w", "")).strip():
            continue
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["s"] - w["e"]) if nxt else 99
        if gap >= pause or len(cur) >= max_words or (len(cur) >= 18 and gap >= pause / 2):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"i": i, "s": float(ws[0]["s"]), "e": float(ws[-1]["e"]), "text": " ".join(str(w["w"]).strip() for w in ws), "words": ws}
            for i, ws in enumerate(out)]


# ====================================================================== scoring + reasons
FILLER_RX = re.compile(r"\b(?:um+|uh+|erm|uhm|hmm|you know|i mean|sort of|kind of)\b[,.]?\s*", re.I)
LEAD_IN_RX = re.compile(  # throat-clearing before the point: dropped from the on-screen hook (the words stay in the clip)
    r"^(?:(?:so|and|but|well|okay|ok|now|yeah|right|basically|first of all|first|second(?:ly)?|also|anyway|"
    r"i think|i mean|you know|i(?:'d| would) like to (?:mention|say|point out|talk about|share)|i want to (?:mention|say|point out)|"
    r"let me (?:tell you|say)|(?:our |the |my )?(?:next |first |last )?question is|it's worth (?:mentioning|noting)|the fact)(?:\s+that)?[,]?\s+)+", re.I)
REPEAT_RX = re.compile(r"\b(\w+(?:\s+\w+){0,4})(?:\s+\1\b)+", re.I)


def stutters(text: str) -> int:
    """Restarts like "how do you want to how do you want to": they read badly as a clip."""
    t = take.norm(text)
    return len(REPEAT_RX.findall(t))


def score(sents: list[dict], a: int, b: int, scenes: list[float] | None = None) -> float:
    """One take's deterministic score, plus what matters for clips cut from a long talk: fewer restarts, names and
    numbers (concrete, quotable)."""
    base = take.heuristic_score(sents, a, b, scenes)
    text = take.span_text(sents, a, b)
    toks = text.split()
    caps = sum(1 for i, w in enumerate(toks) if i and w[:1].isupper() and not toks[i - 1].endswith((".", "?", "!")))
    adj = -4 * min(4, stutters(text)) + min(6, caps * 0.6) + (3 if re.search(r"[$%]|\b\d", text) else 0)
    return round(max(0.0, min(100.0, base + adj)), 1)


def reasons(sents: list[dict], a: int, b: int) -> list[str]:
    """Why this moment, in plain words, from the same features the scorer weighs."""
    first = sents[a]["text"].strip()
    fw = take.norm(first).split()
    span = take.norm(take.span_text(sents, a, b)).split()
    dur = sents[b]["e"] - sents[a]["s"]
    out = []
    if first.endswith("?"):
        out.append("opens with a question")
    hooks = [w for w in fw[:14] if w in take.HOOK_WORDS]
    if hooks:
        q = [w for w in fw if w not in ("um", "uh", "erm", "uhm", "hmm")][:6]
        out.append(f"strong opening line (\u201c{' '.join(q)}\u2026\u201d)")
    if any(ch.isdigit() for ch in first):
        out.append("a number up front")
    if fw and fw[0] in take.WEAK_START:
        out.append("starts mid-thought (lower score)")
    if sents[b]["text"].strip().endswith((".", "!", "?")):
        out.append("ends on a full sentence")
    if stutters(take.span_text(sents, a, b)) >= 2:
        out.append("a few restarts (lower score)")
    if span:
        filler = sum(1 for w in span if w in take.FILLER) / len(span)
        wps = len(span) / max(dur, 1)
        if filler < 0.02:
            out.append("almost no filler words")
        if wps >= 2.4:
            out.append(f"fast, dense delivery ({wps:.1f} words/s)")
        elif wps < 1.6:
            out.append("slow delivery (lower score)")
    out.append(f"{dur:.0f} s, a self-contained stretch")
    return out


def hook_title(sents: list[dict], a: int, b: int, max_words: int = 9) -> str:
    """The on-screen hook: the most hook-like line among the moment's first three sentences, filler removed, cut on a
    word boundary. Only words the speaker said, in order."""
    best, best_sc = sents[a]["text"], -1e9
    for i in range(a, min(b, a + 2) + 1):
        t = sents[i]["text"].strip()
        w = take.norm(t).split()
        if len(w) < 3:
            continue
        sc = 3 * sum(1 for x in w[:12] if x in take.HOOK_WORDS) + (4 if t.endswith("?") else 0) + \
            (2 if any(c.isdigit() for c in t) else 0) - (5 if w[0] in take.WEAK_START else 0) - (i - a) * 1.5 - abs(len(w) - 10) * 0.2
        if sc > best_sc:
            best, best_sc = t, sc
    t = FILLER_RX.sub("", best).strip(" ,;:-")
    t = REPEAT_RX.sub(r"\1", t)
    t = LEAD_IN_RX.sub("", t).strip(" ,;:-")
    t = re.sub(r"(\w)-{1,2}(this|that|which|and|but|so|it|we|they)\b", r"\1 — \2", t)  # "NASA--this" -> a clause break
    words = t.split()
    if len(words) > max_words + 2:  # a whole line that is just a little long reads better than a cut one
        # cut at the first clause break that leaves a real line (4+ words); else on a word boundary with an ellipsis
        cut = next((i + 1 for i, w in enumerate(words[:max_words]) if i >= 3 and (w.endswith((",", ";", ":")) or
                                                                                 (i + 1 < len(words) and words[i + 1] in ("—", "-")))), None)
        t = " ".join(words[:cut]).rstrip(",;:") if cut else " ".join(words[:max_words]).rstrip(",;:-") + "…"
    return (t[:1].upper() + t[1:]) if t else take.best_line(sents, a, b, 60)


def miner_prompt(sents: list[dict], n: int, lo: float, hi: float) -> str:
    lines = [f"[{s['i']}] ({s['s']:.1f}-{s['e']:.1f}s) {s['text']}" for s in sents]
    return (f"Pick the {n} best moments for {int(lo)}-{int(hi)} second vertical clips. A moment is a run of whole sentences "
            "[start..end] that makes sense on its own, opens with a strong line and ends cleanly. Moments must not overlap. "
            "Return JSON {\"moments\": [{\"start\": <sentence index>, \"end\": <sentence index>, \"hook\": <an exact quote of "
            "3-12 words from those sentences>, \"why\": <one short reason>, \"score\": <0-100>}]}.\nTRANSCRIPT:\n" + "\n".join(lines))


def pick_moments(words: list[dict], duration: float | None = None, n: int | None = None, lo: float = MIN_S, hi: float = MAX_S,
                 scenes: list[float] | None = None, silences: list[list[float]] | None = None, engine: dict | None = None,
                 chat_json=None, max_sentences: int = 1400) -> dict:
    """The N best non-overlapping moments. Returns {"moments": [...], "picked_by", "sentences", "target"}; each moment
    {id, rank, start, end, duration, score, reason, reasons, hook, text}. Deterministic without a model."""
    sents = sentences(words)
    if not sents:
        raise take.TakeError("No speech found in this video, so there is nothing to clip.")
    duration = float(duration or sents[-1]["e"])
    auto = not n
    n = max(1, min(MAX_CLIPS, int(n))) if n else default_count(duration)
    picks, how = [], "built-in scorer"
    if engine and chat_json and len(sents) <= max_sentences:
        try:
            data = chat_json(engine, take.MINER_SYSTEM, miner_prompt(sents, n, lo, hi))
            picks = [m for m in take.validate_llm(data, sents, n) if lo - 5 <= m["e"] - m["s"] <= hi + 5]
            how = engine.get("source", "AI model")
        except Exception as e:  # noqa: BLE001 - a model failure falls back to the deterministic scorer
            print(f"longform moments: model failed, built-in scorer used: {str(e)[:200]}", flush=True)  # server log only
            picks, how = [], take.SCORER_FALLBACK  # never the engine's raw error on screen
    if len(picks) < n:
        cands = []
        for a, b in take.candidates(sents, lo, hi):
            cands.append({"a": a, "b": b, "s": sents[a]["s"], "e": sents[b]["e"], "score": score(sents, a, b, scenes)})
        extra = [x for x in cands if all(x["e"] <= p["s"] or x["s"] >= p["e"] for p in picks)]
        picks = take._non_overlapping(picks + extra, n) if picks else take._non_overlapping(extra, n)
        if picks and how not in ("built-in scorer",) and not how.startswith("built-in"):
            how += " + built-in scorer"
    if not picks:  # shorter than the minimum: the whole thing, up to hi
        b = 0
        while b + 1 < len(sents) and sents[b + 1]["e"] - sents[0]["s"] <= hi:
            b += 1
        picks = [{"a": 0, "b": b, "s": sents[0]["s"], "e": sents[b]["e"], "score": score(sents, 0, b, scenes)}]
    if auto and len(picks) > 3:  # no count asked: leave out weak moments instead of padding to the target
        top = max(p["score"] for p in picks)
        keep = [p for p in picks if p["score"] >= max(40.0, top - 25)]
        picks = keep if len(keep) >= 3 else sorted(picks, key=lambda m: -m["score"])[:3]
    ranked = sorted(picks, key=lambda m: -m["score"])
    moments = []
    for m in picks:
        f = take.finalize({**m, "hook": "", "why": ""}, sents, silences or [], scenes or [], duration)
        rs = reasons(sents, m["a"], m["b"])
        why = m.get("why") or ""
        moments.append({"start": f["start"], "end": f["end"], "duration": f["duration"], "score": round(float(m["score"]), 1),
                        "rank": ranked.index(m) + 1, "reason": why or "; ".join(rs[:3]).capitalize(), "reasons": rs,
                        "hook": hook_title(sents, m["a"], m["b"]) if not (m.get("hook") and take.verbatim(m["hook"], f["text"]))
                        else m["hook"], "text": f["text"], "a": m["a"], "b": m["b"]})
    moments.sort(key=lambda m: m["start"])
    for i, m in enumerate(moments):
        m["id"] = f"m{i + 1}"
    return {"moments": moments, "picked_by": how, "sentences": len(sents), "target": n}


# ====================================================================== reframe
def reframe_plan(faces: list[dict] | None, start: float, end: float, src_w: int, src_h: int, ratio: str = "9:16",
                 scenes: list[float] | None = None, burned_subs: bool = False, detector: bool = True) -> dict:
    """Where the crop window sits for one clip and one ratio. Reuses take.crop_plan (dead-zone, smoothing, hard cut on
    scene changes). ``detector`` False (no face model on this box) keeps the centre crop instead of guessing.
    With burned-in subtitles the bottom 16% is cropped first, so the plan is computed on that shorter frame."""
    from .compose import SIZES
    W, H = SIZES[ratio]
    sh = int(src_h * 0.84) // 2 * 2 if burned_subs else src_h
    if not detector:
        return {"mode": "center", "crop_w": min(src_w, int(round(sh * W / H / 2)) * 2), "keys": [(0.0, 0)]}
    plan = take.crop_plan(faces or [], start, end, src_w, sh, scenes, out_ratio=W / H)
    plan["src_h"] = sh
    return plan


# ====================================================================== render (worker)
BRAND_BUG = {"hook": False, "lower": False, "bug": True}  # the opus layout has its own hook box and credit chip


def caption_family(brand: dict | None) -> str | None:
    """The captions.FONTS family of the kit's headline font (Anton, Poppins, Inter, Figtree), or None (a font word
    captions don't carry, such as a serif or the dot font: the opus style's own font stays)."""
    from .captions import FONTS
    f = str((brand or {}).get("head_font") or "")
    return next((fam for fam, file in FONTS.items() if file == f), None)
def render_moment(source: str, m: dict, words: list[dict], *, ratio: str = "9:16", brand: dict | None = None, credit: str = "",
                  out: str, faces: list[dict] | None = None, scenes: list[float] | None = None, detector: bool = True,
                  burned_subs: bool | None = None, names: list[str] | None = None, music: str = "auto",
                  src_size: tuple[int, int] | None = None, face_safe: bool = True, qa_keep: str | None = None,
                  framing: str | None = None, captions_style: str | None = None, hook_mode: str = "opener",
                  source_captions: dict | None = None) -> dict:
    """One finished clip in one ratio: opus style (hook, word captions, credit, end card), reframed.

    face_safe (default on): the hook box, captions and credit are placed clear of the faces (studio/media/facesafe:
    YuNet on the clip span + the take's Jev-verified boxes, mapped through the reframe), then the render QA measures
    the finished frames; over facesafe.QA_MAX it re-places with the faces the QA saw and renders ONCE more; still over,
    r["face_safe"]["status"] is "needs_review" with the reason (the take does not ship it). qa_keep: a path prefix to
    keep the QA's rendered frames (review only).
    framing: the clip's Layout & captions choice: "fit" shows the whole frame over a blurred copy, "track" follows the
    face, None is automatic. captions_style: a captions.STYLES id (the Caption look). hook_mode: "opener" (the hook box
    and credit for the first 3 s, the credit on the end card too) or "keep" (both on screen all clip long).
    source_captions: captions already burned into the source over this span (compose.detect_burned_captions): the clip
    keeps them, adds none of its own and never crops the bottom of the frame (that would cut the source's captions off).
    Every clip carries the brand (owner bug 9 Nov 2026, take 108 "didn't apply my branding"): the logo bug all clip long
    (compose brand_pass, bug only) and word captions in the kit's headline font (brand["caption_family"])."""
    from ..headline import render_hook
    from . import facesafe, ff
    from .compose import SIZES, _keep, detect_burned_subs, render_style
    m = {**m, "hook": render_hook(m.get("hook") or "")}  # the headline guard on every render path (owner P0)
    fam = caption_family(brand)
    if fam:
        brand = {**(brand or {}), "caption_family": fam}
    pr = None
    if src_size is None:
        pr = ff.probe(source)
        src_size = (pr["width"] or 1920, pr["height"] or 1080)
        if burned_subs is None:
            burned_subs = detect_burned_subs(source, pr["duration"])
    if source_captions:
        burned_subs = False
    plan = reframe_plan(faces, m["start"], m["end"], src_size[0], src_size[1], ratio, scenes, bool(burned_subs),
                        detector or framing == "track")
    if framing == "fit":  # Layout & captions > Full frame: the whole picture over a blurred copy of itself
        plan = {"mode": "pad", "crop_w": int(src_size[0]), "keys": [(0.0, 0)], "framing": "fit"}
    kw = dict(out=out, ratio=ratio, hook=m["hook"], brand=brand, words=words, credit=credit, clip=(m["start"], m["end"]),
              music_mood=music, names=names, burned_subs=bool(burned_subs), reframe=plan, captions_style=captions_style,
              hook_mode=hook_mode if hook_mode in ("opener", "keep") else "opener",
              captions_mode="off" if source_captions else "auto", brand_pass=dict(BRAND_BUG))
    fs = None
    if face_safe:
        try:
            fs = _face_safe_render(source, m, plan, src_size, ratio, faces, scenes, bool(burned_subs), kw, qa_keep)
            r = fs.pop("_result")
        except Exception as e:  # noqa: BLE001 - placement never fails the clip: the fixed layout, flagged for review
            print(f"face-safe: {e.__class__.__name__}: {str(e)[:120]}; fixed layout, flagged", flush=True)
            r = render_style(source, "opus", **kw)
            fs = {"status": "needs_review", "reason": f"face-safe placement didn't run ({e.__class__.__name__}): "
                  "the overlays may cover the face", "placed": False}
    else:
        r = render_style(source, "opus", **kw)
    r["face_safe"] = fs
    r["timings"] = {**(r.get("timings") or {}), **((fs or {}).get("timings") or {})}
    cov = take.face_coverage(plan, faces or [], m["start"], m["end"], src_size[0]) if plan["mode"] == "track" else None
    r["reframe"] = {"mode": plan["mode"], "crop_w": plan.get("crop_w"), "keys": len(plan.get("keys") or []),
                    "face_coverage": round(cov, 3) if cov is not None else None, "face_ratio": plan.get("face_ratio")}
    return r


def _face_safe_render(source, m, plan, src_size, ratio, faces, scenes, burned, kw, qa_keep=None) -> dict:
    """Place, render ONCE, QA. The caption level is picked before rendering from the face track (compose._face_safe_layout).
    A failed QA re-places once with the faces the QA saw, one level up, into a side file, and keeps whichever covers less;
    a re-place that would land on the same spots is skipped (no second encode) and the clip waits for a person, saying
    why. Returns the face_safe record (renders, timings) with the kept render under _result."""
    import os
    import shutil
    import tempfile
    import time

    from . import facesafe
    from .compose import HOOK_OPENER_SECS, SIZES, _keep, render_style
    t_all = time.time()
    op_s = HOOK_OPENER_SECS if kw.get("hook_mode", "opener") != "keep" else None
    W, H = SIZES[ratio]
    sw, sh = src_size
    c0, c1 = float(m["start"]), float(m["end"])
    D = max(0.5, c1 - c0)
    keep = _keep(True) if burned else 1.0
    t = time.time()
    samples, det = facesafe.clip_samples(source, c0, c1, sw, sh, faces)
    tm = {"face_sampling": round(time.time() - t, 2), "render": 0.0, "qa": 0.0}
    cuts = [float(x) - c0 for x in scenes or [] if c0 < float(x) < c1]
    rec = {"detector": det, "passes": [], "status": "ok", "placed": True, "renders": 0}
    if det.get("fallback"):
        print(f"face-safe: {det['fallback']}", flush=True)
    pred = facesafe.out_boxes(samples, plan, sw, sh, W, H, keep, head=False)
    td = tempfile.mkdtemp(prefix="hq_fs_")

    def predicted(t, pb=pred):
        return [b for tt, b in pb if abs(tt - t) <= 0.5]

    def encode(z, **extra):
        t = time.time()
        r = render_style(source, "opus", face_safe=z, **{**kw, **extra})
        tm["render"] = round(tm["render"] + time.time() - t, 2)
        if not r.get("skipped"):
            rec["renders"] += 1
        return r

    def measure(r, attempt):
        t = time.time()
        try:
            return facesafe.qa_measure(r["path"], source, c0, D, plan, W, H, keep, float(r.get("cover_frame_secs") or 0)
                                       + float(r.get("cover_secs") or 0), workdir=f"{td}/p{attempt}", predicted=predicted,
                                       keep_frames=f"{qa_keep}_p{attempt}_" if qa_keep else None)
        except Exception as e:  # noqa: BLE001 - a QA that couldn't run is said, and the clip waits for a person
            rec["passes"].append({"attempt": attempt, "placement": r.get("face_safe"), "qa": None,
                                  "qa_error": f"{e.__class__.__name__}: {str(e)[:120]}"})
            return None
        finally:
            tm["qa"] = round(tm["qa"] + time.time() - t, 2)

    def note(r, qa, attempt):
        rec["passes"].append({"attempt": attempt, "placement": r.get("face_safe"), "hook_shown": r.get("hook_shown"),
                              "qa": {k: qa[k] for k in ("ok", "max_cover", "threshold", "faces_by")},
                              "qa_frames": [{k: f.get(k) for k in ("t", "cover")} for f in qa["frames"]]})
    try:
        z = facesafe.zones_for(samples, plan, sw, sh, W, H, D, keep, cuts, opener_s=op_s)
        z["pred"] = pred
        r = encode(z)
        qa = measure(r, 1)
        best = qa
        if qa is None:
            rec.update(status="needs_review", reason="the render QA couldn't check the frames for text on the face")
        else:
            note(r, qa, 1)
            if not qa["ok"]:
                print(f"face-safe: pass 1: text covers {qa['max_cover'] * 100:.1f}% of a face (max {facesafe.QA_MAX * 100:.0f}%)",
                      flush=True)
                fs1 = r.get("face_safe") or {}
                lv = facesafe.LEVELS.index(fs1["level"]) if fs1.get("level") in facesafe.LEVELS else 0
                z2 = facesafe.zones_for(samples, plan, sw, sh, W, H, D, keep, cuts, facesafe.grow_qa_boxes(qa), opener_s=op_s)
                z2["pred"] = pred + facesafe.qa_boxes(qa)
                z2["min_level"] = min(lv + 1, len(facesafe.LEVELS) - 1)
                side = f"{kw['out']}.replace.mp4"
                r2 = encode(z2, out=side, skip_sig=fs1.get("sig"))
                if r2.get("skipped"):  # the faces the QA saw change nothing: a second encode would be the same video
                    rec["replace"] = "skipped: the re-place lands where the first render already was"
                    rec["passes"].append({"attempt": 2, "placement": r2.get("face_safe"), "skipped": True})
                else:
                    qa2 = measure(r2, 2)
                    if qa2 is not None:
                        note(r2, qa2, 2)
                        if qa2["max_cover"] < qa["max_cover"]:
                            shutil.move(side, kw["out"])
                            r2["path"] = kw["out"]
                            r, best = r2, qa2
                            rec["kept"] = "re-placed"
                        else:
                            rec["kept"] = "first"
                if os.path.exists(side):
                    os.remove(side)
                if not best["ok"]:
                    how = ("no other placement clears it (the re-place lands on the same spots, so it wasn't rendered again)"
                           if r2.get("skipped") else "after one re-place")
                    rec.update(status="needs_review", reason=f"text still covers {best['max_cover'] * 100:.1f}% of the speaker's "
                               f"face {how} (limit {facesafe.QA_MAX * 100:.0f}%)")
            rec["qa_jpegs"] = facesafe.qa_jpegs(best)
        fsr = r.get("face_safe") or {}
        rec["level"], rec["predicted_cover"] = fsr.get("level"), fsr.get("predicted_cover")
        rec["max_cover"] = best["max_cover"] if best else None
        rec["first_cover"] = qa["max_cover"] if qa else None
        tm["total"] = round(time.time() - t_all, 2)
        tm["renders"] = rec["renders"]
        rec["timings"] = tm
        measured = "n/a" if rec["max_cover"] is None else f"{rec['max_cover'] * 100:.1f}%"
        print(f"face-safe {ratio}: {rec['renders']} render(s) in {tm['total']:.1f}s (face sampling {tm['face_sampling']:.1f}s"
              f"{' cached' if det.get('cache') == 'hit' else ''}, encode {tm['render']:.1f}s, QA {tm['qa']:.1f}s); level "
              f"{rec['level']}, predicted {(rec['predicted_cover'] or 0) * 100:.1f}%, measured {measured}; {rec['status']}",
              flush=True)
        rec["_result"] = r
        return rec
    finally:
        shutil.rmtree(td, ignore_errors=True)


def contact_sheet(video: str, out: str, cols: int = 5, rows: int = 2, width: int = 216) -> str:
    """A grid of evenly spaced frames (first frame and the end card included) to inspect a clip at a glance."""
    from . import ff
    d = ff.probe(video)["duration"]
    n = cols * rows
    ts = [min(d - 0.05, 0.05 + i * (d - 0.2) / (n - 1)) for i in range(n)]
    with tempfile.TemporaryDirectory() as td:
        frames = []
        for i, t in enumerate(ts):
            p = Path(td) / f"f{i:02d}.png"
            ff.run(["-y", "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale={width}:-2",
                    "-update", "1", str(p)], timeout=60)
            frames.append(p)
        from PIL import Image, ImageDraw
        ims = [Image.open(p).convert("RGB") for p in frames]
        w, h = ims[0].size
        sheet = Image.new("RGB", (cols * w + (cols + 1) * 6, rows * (h + 22) + (rows + 1) * 6), (24, 24, 28))
        dr = ImageDraw.Draw(sheet)
        for i, (im, t) in enumerate(zip(ims, ts)):
            x, y = 6 + (i % cols) * (w + 6), 6 + (i // cols) * (h + 28)
            sheet.paste(im, (x, y))
            dr.text((x + 4, y + h + 4), f"{t:5.1f}s", fill=(220, 220, 225))
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
    return out


def analyze_faces(source: str, duration: float) -> tuple[list[dict], bool, str]:
    """Face samples for reframing (MediaPipe on the worker). Returns (faces, detector_available, how)."""
    try:
        faces, how = take.face_track(source, duration)
    except Exception as e:  # noqa: BLE001
        return [], False, f"none ({e.__class__.__name__})"
    return faces, not how.startswith("none"), how


def split_video(source: str, *, out_dir: str, words: list[dict] | None = None, transcript: dict | None = None,
                url: str | None = None, caption_text: str | None = None, prompt: str = "", n: int | None = None,
                lo: float | None = None, hi: float | None = None, ratios: list[str] | None = None, brand: dict | None = None,
                credit: str = "", official: bool = False, rights_confirmed: bool = False, names: list[str] | None = None,
                faces: list[dict] | None = None, find_faces: bool = True, scenes: list[float] | None = None,
                engine: dict | None = None, chat_json=None, render: bool = True, sheets: bool = True, transcribe=None,
                music: str = "auto", log=print) -> dict:
    """The whole split, on the worker. Returns a manifest (also written to out_dir/manifest.json):
    {source, transcript: {source, detail, words}, burned_subs, reframe_detector, picked_by, clips: [{id, rank, start,
    end, score, reason, hook, renders: [{ratio, path, duration, ...}], sheet}]}."""
    from . import ff
    from .compose import detect_burned_subs
    if not (official or rights_confirmed):
        return {"needs_confirmation": True,
                "question": f"This splits someone else's video{(' (' + credit + ')') if credit else ''} into clips. I'll credit it "
                            "on screen and in each caption. Do you have the right to repost it (or is it fair commentary)?"}
    if not credit and not official:
        return {"error": "A credit line is required for any video you didn't make (e.g. 'Video: @handle on YouTube')."}
    req = parse_request(prompt) if prompt else {"n": None, "lo": MIN_S, "hi": MAX_S}
    n, lo, hi = n or req["n"], float(lo or req["lo"]), float(hi or req["hi"])
    od = Path(out_dir)
    od.mkdir(parents=True, exist_ok=True)
    pr = ff.probe(source)
    dur, sw, sh = pr["duration"], pr["width"] or 1920, pr["height"] or 1080
    if transcript is None:
        if words is not None:
            transcript = {"words": words, "source": "given", "detail": "words passed in"}
        else:
            from .subtitles import resolve_transcript
            transcript = resolve_transcript(source, url=url, caption_text=caption_text, names=names, transcribe=transcribe)
    words = transcript["words"]
    log(f"transcript: {len(words)} words from {transcript.get('source')} ({transcript.get('detail')})")
    burned = detect_burned_subs(source, dur)
    if scenes is None:
        try:
            scenes, _ = take.detect_scenes(source)
        except Exception:  # noqa: BLE001
            scenes = []
    silences = []
    try:
        silences = take.detect_silences(source)
    except Exception:  # noqa: BLE001
        pass
    picked = pick_moments(words, dur, n, lo, hi, scenes=scenes, silences=silences, engine=engine, chat_json=chat_json)
    log(f"moments: {len(picked['moments'])} by {picked['picked_by']}")
    detector = True
    how = "given"
    if faces is None:
        if find_faces:
            faces, detector, how = analyze_faces(source, dur)
        else:
            faces, detector, how = [], False, "off"
    clips = []
    for m in picked["moments"]:
        c = {k: m[k] for k in ("id", "rank", "start", "end", "duration", "score", "reason", "reasons", "hook", "text")}
        c["renders"] = []
        if render:
            for r in ratios or ["9:16"]:
                outp = od / f"{m['id']}_{r.replace(':', 'x')}.mp4"
                got = render_moment(source, m, words, ratio=r, brand=brand, credit=credit, out=str(outp), faces=faces,
                                    scenes=scenes, detector=detector, burned_subs=burned, music=music,
                                    names=names if transcript.get("source") == "whisper" else None,  # typed / human captions stay as written
                                    src_size=(sw, sh))
                item = {k: got[k] for k in ("path", "ratio", "width", "height", "duration", "clip", "captions", "burned_subs",
                                            "caption_problems", "music")}
                item["reframe"] = got["reframe"]
                item["boxes"] = {k: v for k, v in got["boxes"].items() if isinstance(v, (list, tuple))}
                if sheets:
                    item["sheet"] = contact_sheet(str(outp), str(od / f"{m['id']}_{r.replace(':', 'x')}_sheet.png"))
                c["renders"].append(item)
                log(f"{m['id']} {r}: {item['duration']} s, reframe {item['reframe']['mode']}, captions {item['captions']}")
        clips.append(c)
    man = {"source": {"path": source, "duration": dur, "width": sw, "height": sh, "url": url or ""},
           "transcript": {k: transcript.get(k) for k in ("source", "detail", "kind", "language", "punctuated")} | {"words": len(words)},
           "burned_subs": burned, "reframe_detector": how, "picked_by": picked["picked_by"], "sentences": picked["sentences"],
           "target": picked["target"], "range": [lo, hi], "credit": credit, "clips": clips}
    (od / "manifest.json").write_text(json.dumps(man, indent=2, default=str))
    return man

