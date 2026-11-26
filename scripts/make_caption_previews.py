"""Animated caption-style previews for the One take setup (studio/static/captions/<id>.webp).

Each preview is the real caption renderer (studio/media/captions_ass + libass via ffmpeg) on a plain dark card, so the
gallery shows exactly what a clip gets, not a CSS imitation. Run after changing a preset:
    python scripts/make_caption_previews.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from studio.media import captions_ass  # noqa: E402

W, H = 540, 960
CROP_W, CROP_H = 440, 200
LINE = "Only the words you actually said".split()


def words():
    out, t = [], 0.15
    for w in LINE:
        d = 0.22 + 0.03 * len(w)
        out.append({"w": w, "s": round(t, 2), "e": round(t + d - 0.03, 2)})
        t += d
    return out, t + 0.5


def main():
    dest = ROOT / "studio" / "static" / "captions"
    dest.mkdir(parents=True, exist_ok=True)
    ws, dur = words()
    kit = {"colors": {"text": "#FFFFFF", "accent": "#FFD84D"}}
    for pid in captions_ass.PRESETS:
        ass = captions_ass.build(ws, 0.0, kit, {}, pid, [{"start": 0, "end": 99, "layout": "track"}], W=W, H=H, bg_luma=0.15)
        y = captions_ass.band_y({"layout": "track"}, H)
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "c.ass"
            a.write_text(ass)
            vf = (f"ass=filename={a}:fontsdir={ROOT / 'studio' / 'fonts'},crop={CROP_W}:{CROP_H}:{(W - CROP_W) // 2}:{max(0, y - CROP_H // 2)},"
                  "scale=360:-2,fps=15,trim=start=0.45,setpts=PTS-STARTPTS")  # first frame shows words (a still on reduced motion)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x26282b:s={W}x{H}:d={dur:.2f}",
                            "-vf", vf, "-loop", "0", "-quality", "70", "-compression_level", "6",
                            str(dest / f"{pid}.webp")], check=True)
        print(pid, (dest / f"{pid}.webp").stat().st_size)


if __name__ == "__main__":
    main()
