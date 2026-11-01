"""Live check of the long-video splitter on a real video (run by hand or on a GitHub runner):

    python scripts/longform_live.py --video talk.mp4 [--captions talk.vtt | --url https://youtu.be/...] \\
        --prompt "split this into 5 shorts" --ratios 9:16,4:5 --credit "Video: NASA" --official --out shots/

Writes every clip, a contact sheet per clip and manifest.json (clip list with timestamps, scores, reasons, which
transcript source was used, reframe mode, face coverage) to --out. With --url and no --video it first tries to fetch
the post (studio.media.download) and reports whether the platform let this machine in.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video")
    ap.add_argument("--url", default="")
    ap.add_argument("--captions")
    ap.add_argument("--prompt", default="find the best moments of this video")
    ap.add_argument("--ratios", default="9:16")
    ap.add_argument("--credit", default="")
    ap.add_argument("--official", action="store_true")
    ap.add_argument("--no-faces", action="store_true")
    ap.add_argument("--max-clips", type=int, default=0)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from studio.media import longform, subtitles
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report: dict = {"url": a.url}
    video = a.video
    if not video and a.url:
        from studio.media.download import DownloadError, download_video
        try:
            got = download_video(a.url, out / "source.mp4")
            video, report["fetch"] = got["path"], "ok"
        except DownloadError as e:
            report["fetch"] = f"blocked: {e}"
        cap = subtitles.platform_captions(a.url)
        report["platform_captions"] = {"words": len(cap["words"]), "kind": cap["kind"]} if cap else None
    if not video:
        (out / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 2
    t0 = time.time()
    tr = subtitles.resolve_transcript(video, url=a.url or None,
                                      caption_text=Path(a.captions).read_text("utf-8", "replace") if a.captions else None,
                                      caption_name=Path(a.captions).name if a.captions else "")
    report["transcript_secs"] = round(time.time() - t0, 1)
    man = longform.split_video(video, out_dir=str(out), transcript=tr, url=a.url or None, prompt=a.prompt,
                               n=a.max_clips or None, ratios=a.ratios.split(","), credit=a.credit, official=a.official,
                               rights_confirmed=True, find_faces=not a.no_faces)
    man["report"] = report
    man["total_secs"] = round(time.time() - t0, 1)
    (out / "manifest.json").write_text(json.dumps(man, indent=2, default=str))
    for c in man.get("clips", []):
        print(f"{c['id']} #{c['rank']} {c['start']:.1f}-{c['end']:.1f}s score {c['score']} | {c['hook']} | {c['reason']}")
        for r in c["renders"]:
            print(f"   {r['ratio']} {r['duration']}s reframe={r['reframe']['mode']} coverage={r['reframe']['face_coverage']} "
                  f"captions={r['captions']} problems={len(r['caption_problems'])}")
    print("transcript:", man["transcript"], "detector:", man["reframe_detector"], "secs:", man["total_secs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
