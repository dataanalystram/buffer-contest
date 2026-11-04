"""Synthetic clips for the layout tests, built with FFmpeg from two public-domain paintings (tests/studio/fixtures/reframe):

* mona.jpg  - Leonardo da Vinci, "Mona Lisa" (c. 1503-1519), public domain (Wikimedia Commons, "Mona Lisa, by Leonardo
              da Vinci, from C2RMF retouched.jpg", scaled to 320 px)
* pearl.jpg - Johannes Vermeer, "Girl with a Pearl Earring" (c. 1665), public domain (Wikimedia Commons,
              "1665 Girl with a Pearl Earring.jpg", scaled to 320 px)

    talk     one face, moving slowly over a noisy background (a person on camera)          -> track
    screen   a static UI screenshot full of text, a small webcam face in the bottom-right  -> screen (PiP)
    duo      two faces far apart, both mouths moving                                       -> split (HQ_LAYOUT_SPLIT=1), else fit
    mixed    talk for 4 s, then a hard cut to the screen for 4 s                           -> track, then screen
"""
from __future__ import annotations

import subprocess
from pathlib import Path

HERE = Path(__file__).parent / "fixtures" / "reframe"
MONA, PEARL = HERE / "mona.jpg", HERE / "pearl.jpg"
W, H, DUR = 1280, 720, 8.0


def _ff(args: list[str]) -> None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args], capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])


def _enc(out: Path) -> list[str]:
    return ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p", "-r", "30",
            "-c:a", "aac", "-b:a", "96k", "-shortest", str(out)]


def screenshot(out: Path) -> Path:
    """A UI-like still: white page, a dark sidebar, rows of text across the full width."""
    from PIL import Image, ImageDraw, ImageFont
    fonts = Path(__file__).resolve().parents[2] / "studio" / "fonts"
    im = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 210, H), fill=(32, 33, 40))
    f = ImageFont.truetype(str(fonts / "Inter-Regular.ttf"), 22)
    fb = ImageFont.truetype(str(fonts / "Inter-Bold.ttf"), 34)
    d.text((250, 30), "Quarterly report: pipeline and revenue by region", font=fb, fill=(20, 20, 24))
    words = "revenue pipeline region forecast target margin churn growth quarter accounts".split()
    for i in range(18):
        y = 90 + i * 33
        line = " ".join(words[(i + j) % len(words)] for j in range(12))
        d.text((250, y), line, font=f, fill=(40, 40, 48))
        d.text((20, 90 + i * 30), f"Menu item {i}", font=f, fill=(210, 210, 220))
    im.save(out)
    return out


def talk(out: Path, dur: float = DUR) -> Path:
    _ff(["-f", "lavfi", "-i", f"color=c=0x404850:s={W}x{H}:r=30:d={dur}", "-loop", "1", "-i", str(MONA),
         "-f", "lavfi", "-i", f"sine=frequency=220:duration={dur}",
         "-filter_complex", "[0:v]noise=alls=18:allf=t[bg];[1:v]scale=440:-2[f];"
         "[bg][f]overlay=x='420+110*sin(t/1.5)':y=30:shortest=1,format=yuv420p[v]",
         "-map", "[v]", "-map", "2:a", "-t", f"{dur}", *_enc(out)])
    return out


def screen(out: Path, dur: float = DUR) -> Path:
    shot = screenshot(out.with_suffix(".png"))
    _ff(["-loop", "1", "-i", str(shot), "-loop", "1", "-i", str(MONA), "-f", "lavfi", "-i", f"sine=frequency=330:duration={dur}",
         "-filter_complex", "[1:v]crop=200:200:70:60,scale=110:110[cam];[0:v][cam]overlay=x=1150:y=590,format=yuv420p[v]",
         "-map", "[v]", "-map", "2:a", "-t", f"{dur}", *_enc(out)])
    return out


def duo(out: Path, dur: float = DUR) -> Path:
    # both mouths "move": a skin-toned box flickers over each mouth (YuNet still sees the faces)
    _ff(["-f", "lavfi", "-i", f"color=c=0x30343a:s={W}x{H}:r=30:d={dur}", "-loop", "1", "-i", str(MONA), "-loop", "1", "-i", str(PEARL),
         "-f", "lavfi", "-i", f"sine=frequency=260:duration={dur}",
         "-filter_complex", "[0:v]noise=alls=22:allf=t[bg];[1:v]scale=300:-2,"
         "drawbox=x=118:y=130:w=36:h=12:color=0x1a0c08:t=fill:enable='lt(mod(n,4),2)'[a];"
         "[2:v]scale=320:-2,drawbox=x=116:y=188:w=32:h=12:color=0x1a0c08:t=fill:enable='gte(mod(n,4),2)'[b];"
         "[bg][a]overlay=x=40:y=150:shortest=1[t];[t][b]overlay=x=940:y=170,format=yuv420p[v]",
         "-map", "[v]", "-map", "3:a", "-t", f"{dur}", *_enc(out)])
    return out


def mixed(out: Path) -> Path:
    a, b = talk(out.with_name("mix_a.mp4"), 4.0), screen(out.with_name("mix_b.mp4"), 4.0)
    _ff(["-i", str(a), "-i", str(b), "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[v][au]",
         "-map", "[v]", "-map", "[au]", *_enc(out)])
    return out


def words(dur: float = DUR, step: float = 0.38) -> list[dict]:
    """A steady stream of timed words over the clip (the speech spans the mouth-motion gate reads)."""
    text = ("so here is the thing about building in public, you learn faster than you think. every week we ship, "
            "we measure and we fix what broke. that is the whole method, honestly.").split()
    out, t, i = [], 0.2, 0
    while t + step < dur:
        w = text[i % len(text)]
        out.append({"w": w, "s": round(t, 2), "e": round(t + step - 0.05, 2), "p": 0.9})
        t += step
        i += 1
    return out
