"""One take layouts: wall-clock realtime factor of plan + render for 1080p sources (target <= 1.2x on 2 vCPU).

    taskset -c 0,1 python scripts/bench_layouts.py [seconds]   # CI pins it to two cores (studio-ci, informative)

Sources are the synthetic test clips (tests/studio/reframe_fixtures.py) scaled to 1920x1080 and re-encoded like a
typical upload (H.264, 8 Mbit/s, B-frames). Prints one JSON line per layout and a summary line.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio.media.reframe import render  # noqa: E402
from tests.studio import reframe_fixtures as fx  # noqa: E402


def main(dur: float = 30.0) -> int:
    d = Path(tempfile.mkdtemp(prefix="hq_bench_"))
    rows = []
    for kind, split in (("talk", "0"), ("screen", "0"), ("duo", "1")):
        os.environ["HQ_LAYOUT_SPLIT"] = split
        raw = getattr(fx, kind)(d / f"{kind}_raw.mp4", dur)
        src = d / f"{kind}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-vf", "scale=1920:1080", "-c:v", "libx264", "-preset",
                        "veryfast", "-b:v", "8M", "-maxrate", "8M", "-bufsize", "16M", "-c:a", "copy", str(src)], check=True)
        t0 = time.time()
        p = render.render(str(src), 0.0, dur, fx.words(dur), {"width": 1920, "height": 1080}, str(d / f"{kind}_out.mp4"),
                          audio="aresample=44100")
        el = time.time() - t0
        rows.append({"layout": "/".join(sorted({s["layout"] for s in p["shots"]})), "seconds": dur, "wall_s": round(el, 2),
                     "rtf": round(el / dur, 3), "faces_s": p["timings"].get("faces"), "render_s": p["timings"].get("render"),
                     "cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()})
        print(json.dumps(rows[-1]), flush=True)
    worst = max(r["rtf"] for r in rows)
    print(json.dumps({"worst_rtf": worst, "target": 1.2, "ok": worst <= 1.2}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 30.0))
