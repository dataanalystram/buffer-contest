"""Build the bundled sample take (studio/sample_take/) with the real One take pipeline.

    pip install -r requirements-worker.txt          # faster-whisper, scenedetect, mediapipe (+ ffmpeg on PATH)
    python scripts/build_sample_take.py SOURCE.mp4 [--analysis analysis.json]

The sample is a public-domain NASA talk (Dr. Fran Chiaramonte on the Light Microscopy Module, NASA Glenn,
images.nasa.gov id GRC-2016-CM-0129.11), trimmed to start after the opening logo. NASA does not endorse HuhQueue.
Analysis (transcript, silences, scenes, faces) and the clips are produced by studio/take.py exactly as the worker does;
the sample workspace then mines, preflights and places them live.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio import brand, take  # noqa: E402

OUT = ROOT / "studio" / "sample_take"
SOURCE = {"title": "The Light Microscopy Module on the ISS", "speaker": "Dr. Fran Chiaramonte, NASA",
          "credit": "NASA Glenn Research Center, public domain", "url": "https://images.nasa.gov/details/GRC-2016-CM-0129.11",
          "note": "NASA material is not protected by copyright in the US. NASA does not endorse HuhQueue."}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--analysis", help="reuse an analysis JSON from take.analyze()")
    ap.add_argument("--crf", type=int, default=28)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    analysis = json.loads(Path(a.analysis).read_text()) if a.analysis else take.analyze(a.source)
    mined = take.mine_moments(analysis)
    kit = {**brand.DEFAULT_KIT, "name": "Bright Desk", "handle": "brightdesk"}
    p = brand.visual_params(kit, [])
    clips = []
    for i, m in enumerate(mined["moments"], 1):
        data, cover, meta = take.render_clip(a.source, m, take.words_in(analysis["words"], m["start"], m["end"]), analysis["faces"],
                                             analysis["scenes"], analysis["probe"], kit, p, crf=a.crf)
        (OUT / f"clip{i}.mp4").write_bytes(data)
        (OUT / f"cover{i}.jpg").write_bytes(cover)
        clips.append({"id": f"c{i}", "moment_id": m["id"], "file": f"clip{i}.mp4", "cover": f"cover{i}.jpg", "meta": meta})
        print(m["id"], meta["layout"], meta["duration"], len(data), flush=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "80", "-i", a.source, "-frames:v", "1", "-vf", "scale=640:-2",
                    str(OUT / "poster.jpg")], check=True)
    keep = {k: analysis[k] for k in ("probe", "words", "language", "silences", "scenes", "providers", "timings")}
    keep["faces"] = []  # not needed once rendered; keeps the bundle small
    (OUT / "sample.json").write_text(json.dumps({"source": {**SOURCE, "poster": "poster.jpg",
                                                            "duration": analysis["probe"]["duration"]},
                                                 "analysis": keep, "moments": mined["moments"], "picked_by": mined["picked_by"],
                                                 "clips": clips}, indent=1))
    print("wrote", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
