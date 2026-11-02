"""Build the "Split a long video" demo from Buffer's own Buffer Commit kickoff Q&A (89 min, 720p).

Runs on the GitHub video worker (.github/workflows/demo-clips.yml), never on the web dyno, and spends nothing on AI:
transcripts come from faster-whisper on the worker, moments are scored by the built-in scorer (studio.media.longform).

    python scripts/demo_buffer_split.py --source <url or path> --out demo_out   # --source is required

For each moment the owner picked (seconds in the source) it cuts a window with a little room either side, transcribes
just that window, snaps the moment to whole sentences, re-scores it with longform.score/reasons/hook_title, tracks faces
in the window and renders the opus-style 9:16 clip with longform.render_moment (hook, word captions, credit chip,
Follow end card). Writes clipN.mp4, coverN.jpg, clipN_sheet.png and sample.json (the bundle studio/takes.start_sample
reads) to --out.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from studio import take  # noqa: E402
from studio.media import ff, longform  # noqa: E402

YOUTUBE = "https://youtu.be/QJf0H64BnFo"
TITLE = "Everything You Need to Know Before Buffer Commit Starts"
CREDIT = "Video: Buffer — Everything You Need to Know Before Buffer Commit Starts"
MOMENTS = [(2043.9, 2085.5), (2132.7, 2173.8), (2827.9, 2869.8), (4528.4, 4559.4), (5209.5, 5237.4)]
PAD = 25.0
# The speakers' names as the slides spell them (Whisper hears "Justina" as "Yestina"): fixed in the words before scoring,
# so the hook title and the captions both carry the right name.
NAMES = {"yestina": "Justina", "justina": "Justina", "sabrina": "Sabreen", "sabreen": "Sabreen"}


def fix_words(words: list[dict]) -> list[dict]:
    out = []
    for x in words:
        core = x["w"].strip(".,!?;:\"'…").lower()
        if core in NAMES:
            x = {**x, "w": x["w"].replace(x["w"].strip(".,!?;:\"'…"), NAMES[core])}
        out.append(x)
    return out


def sh(cmd: list[str], timeout: int = 900) -> None:
    subprocess.run(cmd, check=True, timeout=timeout)


def fetch(src: str, dest: Path) -> Path:
    if not src.startswith("http"):
        return Path(src)
    if not dest.exists():
        import httpx
        with httpx.stream("GET", src, follow_redirects=True, timeout=600) as r:
            r.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
    return dest


def snap(sents: list[dict], s: float, e: float) -> tuple[int, int]:
    """The run of whole sentences that best covers [s, e] (window-local seconds)."""
    a = min(range(len(sents)), key=lambda i: abs(sents[i]["s"] - s))
    b = min(range(a, len(sents)), key=lambda i: abs(sents[i]["e"] - e))
    return a, b


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True,
                    help="URL or path of the source video (you supply it; nothing is hosted for you). The bundle credits the "
                         "YouTube original and never records where the file came from.")
    ap.add_argument("--out", default="demo_out")
    ap.add_argument("--model", default="small")
    ap.add_argument("--only", type=int, default=0, help="render just moment N (1-based) for a quick check")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    src = fetch(args.source, out / "source.mp4")
    pr = ff.probe(str(src))
    print("source:", pr)
    moments, clips, all_words, timings = [], [], [], {"transcribe": 0.0, "faces": 0.0, "render": 0.0}
    for k, (ms, me) in enumerate(MOMENTS, 1):
        if args.only and k != args.only:
            continue
        w0 = max(0.0, ms - PAD)
        w1 = min(pr["duration"], me + PAD)
        win = out / f"win{k}.mp4"
        sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{w0:.2f}", "-i", str(src), "-t", f"{w1 - w0:.2f}", "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "128k", str(win)])
        t = time.time()
        words, lang, how = take.transcribe(str(win), args.model)
        words = fix_words(words)
        if lang != "en":  # the source must carry the speakers' own (English) audio, never an auto-dub track
            raise SystemExit(f"moment {k}: transcript language is {lang!r}, expected 'en' (wrong audio track in the source?)")
        timings["transcribe"] += time.time() - t
        sents = longform.sentences(words)
        a, b = snap(sents, ms - w0, me - w0)
        sc = longform.score(sents, a, b)
        rs = longform.reasons(sents, a, b)
        hook = longform.hook_title(sents, a, b)
        f = take.finalize({"a": a, "b": b, "s": sents[a]["s"], "e": sents[b]["e"], "score": sc, "hook": "", "why": ""}, sents, [], [],
                          w1 - w0)
        m = {"id": f"m{k}", "a": a, "b": b, "start": f["start"], "end": f["end"], "duration": f["duration"], "score": sc,
             "hook": hook, "text": f["text"], "reasons": rs, "why": "; ".join(rs[:3]).capitalize() + " (built-in scorer)."}
        t = time.time()
        faces, detector, fhow = longform.analyze_faces(str(win), w1 - w0)
        timings["faces"] += time.time() - t
        t = time.time()
        clip = out / f"clip{k}.mp4"
        r = longform.render_moment(str(win), m, words, ratio="9:16", credit=CREDIT, out=str(clip), faces=faces, detector=detector,
                                   music="none")
        timings["render"] += time.time() - t
        cover = out / f"cover{k}.jpg"
        sh(["ffmpeg", "-y", "-v", "error", "-ss", "0.25", "-i", str(clip), "-frames:v", "1", "-q:v", "3", str(cover)])
        longform.contact_sheet(str(clip), str(out / f"clip{k}_sheet.png"))
        cp = ff.probe(str(clip))
        print(f"m{k}: {w0 + m['start']:.1f}-{w0 + m['end']:.1f}s score {sc} hook {hook!r} reframe {r['reframe']['mode']} faces {fhow}")
        gm = {**m, "start": round(w0 + m["start"], 2), "end": round(w0 + m["end"], 2), "s": round(w0 + sents[a]["s"], 2),
              "e": round(w0 + sents[b]["e"], 2)}
        gm.pop("reasons", None)
        moments.append(gm)
        all_words += [{**x, "s": round(x["s"] + w0, 2), "e": round(x["e"] + w0, 2)} for x in words
                      if m["start"] - 0.5 <= x["s"] <= m["end"] + 0.5]
        clips.append({"id": f"c{k}", "moment_id": f"m{k}", "file": clip.name, "cover": cover.name, "sheet": f"clip{k}_sheet.png",
                      "meta": {"type": "video", "width": cp["width"], "height": cp["height"], "duration": round(cp["duration"], 2),
                               "headline": r.get("hook") or hook, "credit": CREDIT, "cover_offset_ms": 250,
                               "layout": r["reframe"]["mode"], "face_coverage": r["reframe"].get("face_coverage"),
                               "has_end_card": True, "captions": r.get("captions"),
                               "probe": {"probed": True, "codec": "h264", "width": cp["width"], "height": cp["height"],
                                         "duration": cp["duration"], "kind": "video", "bytes": clip.stat().st_size}}})
        win.unlink(missing_ok=True)
    sh(["ffmpeg", "-y", "-v", "error", "-ss", str(MOMENTS[0][0]), "-i", str(src), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4",
        str(out / "poster.jpg")])
    bundle = {"source": {"title": TITLE, "speaker": "Buffer team (Buffer Commit kickoff Q&A)", "credit": CREDIT, "url": YOUTUBE,
                         "note": "Buffer's own 89-minute Q&A for the Buffer Commit contest, used as the demo "
                         "example with credit. Buffer does not endorse HuhQueue.", "poster": "poster.jpg",
                         "duration": round(pr["duration"], 2)},
              "analysis": {"probe": {"duration": pr["duration"], "width": pr["width"], "height": pr["height"], "audio": True,
                                     "codec": "h264"},
                           "words": all_words, "language": "en", "silences": [], "scenes": [], "faces": [],
                           "providers": {"stt": f"faster-whisper {args.model} (CPU int8) on the GitHub worker, per moment window",
                                         "faces": "MediaPipe Face Detector (BlazeFace)"},
                           "timings": {k: round(v, 1) for k, v in timings.items()}},
              "moments": moments, "picked_by": "the owner's moments, re-scored by the built-in scorer", "clips": clips}
    (out / "sample.json").write_text(json.dumps(bundle, indent=1))
    (out / "source.mp4").unlink(missing_ok=True) if args.source.startswith("http") else None
    print("done:", [c["file"] for c in clips])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
