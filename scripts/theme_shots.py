"""Render every video theme (and base style) on a real clip and build contact sheets + gallery thumbnails.

    python scripts/theme_shots.py SOURCE.mp4 --words words.json --out work/shots [--ratios 9:16,4:5,1:1] [--only crt,swiss]
    python scripts/theme_shots.py SOURCE.mp4 --gallery studio/static/themes      # one still + one 2.5 s loop per theme

Each render runs through compose.render_style exactly as the worker does. Peak memory of the whole process tree is
sampled every 0.1 s (what a container limit sees) and written to <out>/report.json with layout / caption problems.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from studio.media import compose, ff, styles  # noqa: E402

HOOK = "Glasses that let you talk without making a sound"
FACTS = ["Reads silent mouthing with radar built into the frames", "Turns it into text at up to 200 words a minute",
         "Made by a student founder, Aarush Yadav"]


def _tree_rss(pid: int) -> int:
    tot = 0
    try:
        out = subprocess.run(["ps", "-o", "pid=,ppid=,rss=", "-A"], capture_output=True, text=True).stdout
    except Exception:  # noqa: BLE001
        return 0
    kids: dict[int, list[tuple[int, int]]] = {}
    for ln in out.splitlines():
        p = ln.split()
        if len(p) == 3:
            kids.setdefault(int(p[1]), []).append((int(p[0]), int(p[2])))
    stack, seen = [pid], set()
    rss_of = {int(ln.split()[0]): int(ln.split()[2]) for ln in out.splitlines() if len(ln.split()) == 3}
    while stack:
        q = stack.pop()
        if q in seen:
            continue
        seen.add(q)
        tot += rss_of.get(q, 0)
        stack += [c for c, _ in kids.get(q, [])]
    return tot * 1024


class Peak:
    def __enter__(self):
        self.peak, self.run = 0, True
        self.t = threading.Thread(target=self._loop, daemon=True)
        self.t.start()
        return self

    def _loop(self):
        while self.run:
            self.peak = max(self.peak, _tree_rss(os.getpid()))
            time.sleep(0.1)

    def __exit__(self, *a):
        self.run = False
        self.t.join()


def frames(path: str, times: list[float], out_png: str, w: int = 270) -> None:
    from PIL import Image
    ims = []
    for i, t in enumerate(times):
        p = f"{out_png}.{i}.png"
        ff.run(["-y", "-v", "error", "-ss", f"{max(0, t):.2f}", "-i", path, "-frames:v", "1", "-update", "1", p], timeout=120)
        im = Image.open(p).convert("RGB")
        ims.append(im.resize((w, int(im.height * w / im.width))))
        os.remove(p)
    sheet = Image.new("RGB", (w * len(ims) + 8 * (len(ims) - 1), ims[0].height), (40, 40, 44))
    for i, im in enumerate(ims):
        sheet.paste(im, (i * (w + 8), 0))
    sheet.save(out_png)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--words")
    ap.add_argument("--out", default="theme_shots")
    ap.add_argument("--ratios", default="9:16")
    ap.add_argument("--only", default="")
    ap.add_argument("--clip", default="28.2,36.2")
    ap.add_argument("--preset", default="veryfast")
    ap.add_argument("--gallery", default="")
    a = ap.parse_args()
    words = []
    if a.words:
        d = json.loads(Path(a.words).read_text())
        words = d["words"] if isinstance(d, dict) else d
    ids = [i for i in (a.only.split(",") if a.only else styles.ALL_IDS) if i]
    c0, c1 = (float(x) for x in a.clip.split(","))
    out = Path(a.gallery or a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = []
    for sid in ids:
        for ratio in (["9:16"] if a.gallery else a.ratios.split(",")):
            dst = out / f"{sid}_{ratio.replace(':', 'x')}.mp4"
            t0 = time.time()
            with Peak() as pk:
                m = compose.render_style(a.source, sid, out=str(dst), ratio=ratio, hook=HOOK, facts=FACTS, words=words,
                                         credit="Video: @AarushYadav47 on X", clip=(c0, c1), preset=a.preset, crf=23,
                                         burned_subs=False, names=["Subvocal", "Aarush"])
            row = {"id": sid, "ratio": ratio, "secs": round(time.time() - t0, 1), "peak_mb": round(pk.peak / 2**20),
                   "duration": m["duration"], "captions": m["captions"], "caption_problems": m["caption_problems"],
                   "boxes": {k: v for k, v in m["boxes"].items() if isinstance(v, tuple)}, "safe": m["safe_area"]}
            report.append(row)
            print(json.dumps({k: row[k] for k in ("id", "ratio", "secs", "peak_mb", "duration", "caption_problems")}), flush=True)
            cov, D = m["cover_secs"], c1 - c0
            if a.gallery:
                from PIL import Image
                still = out / f"{sid}.jpg"
                ff.run(["-y", "-v", "error", "-ss", f"{cov + D * 0.45:.2f}", "-i", str(dst), "-frames:v", "1", "-update", "1",
                        "-vf", "scale=360:-2", str(still)], timeout=120)
                Image.open(still).save(still, quality=80)
                ff.run(["-y", "-v", "error", "-ss", f"{max(0.0, cov - 0.6):.2f}", "-t", "2.6", "-i", str(dst), "-an", "-vf",
                        "scale=270:-2,fps=20", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out / f"{sid}.mp4")], timeout=300)
                dst.unlink()
            else:
                times = [min(0.5, cov / 2) if cov else 0.3, cov + 0.6, cov + D * 0.5, cov + D - 0.4,
                         m["duration"] - compose.CARD_SECS - (m["facts_secs"] / 2 if m["facts_secs"] else 0.3), m["duration"] - 0.5]
                frames(str(dst), times, str(out / f"sheet_{sid}_{ratio.replace(':', 'x')}.png"))
    (out / "report.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
