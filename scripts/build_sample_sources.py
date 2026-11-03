"""Rebuild the sample take's re-render sources (studio/sample_take/src<N>.mp4) from its own bundled clips.

The 89-minute Buffer Commit source isn't hosted anywhere (PR #68), so a sample clip couldn't be trimmed or re-captioned.
Each bundled clip already holds the source picture for its moment, un-captioned, in the band between the hook header
and the burned-in captions (y 525..1245 of the 1080x1920 frame). This cuts that band for the clip's moment (the clip
minus its 3 s end card), so a trim *inside* the moment, a transcript fix or a new cover hook can re-render the clip on
the same worker as any other take. Run after the sample bundle changes (optionally only some clips: ... 1 3):

    python scripts/build_sample_sources.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "studio" / "sample_take"
BAND = (0, 525, 1080, 720)  # x, y, w, h of the source picture inside a bundled 9:16 clip
OUT_W, OUT_H = 960, 640


def main() -> int:
    only = {int(x) for x in sys.argv[1:]}
    meta = json.loads((DIR / "sample.json").read_text())
    for i, c in enumerate(meta["clips"], 1):
        if only and i not in only:
            continue
        m = next(x for x in meta["moments"] if x["id"] == c["moment_id"])
        length = round(m["end"] - m["start"], 2)
        out = DIR / f"src{i}.mp4"
        x, y, w, h = BAND
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(DIR / c["file"]), "-t", f"{length}",
                        "-vf", f"crop={w}:{h}:{x}:{y},scale={OUT_W}:{OUT_H}", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "30", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out)],
                       check=True)
        c["source_window"] = {"file": out.name, "start": m["start"], "end": m["end"], "width": OUT_W, "height": OUT_H}
        print(out.name, f"{out.stat().st_size / 1e6:.1f} MB", length, "s", flush=True)
        (DIR / "sample.json").write_text(json.dumps(meta, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
