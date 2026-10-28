"""Fill a data dir with realistic demo data (no network calls) for screenshots, e2e tests and trying the UI.

    HUHQ_DATA_DIR=./demo-data HUHQ_ENABLE_FAKE=1 python scripts/demo_seed.py
    HUHQ_DATA_DIR=./demo-data HUHQ_ENABLE_FAKE=1 HUHQ_ADMIN_PASSWORD=demo-password huhqueue serve --no-worker
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from huhqueue.core import Core  # noqa: E402
from huhqueue.models import Channel, Comment, Idea, Post, PostTarget  # noqa: E402
from huhqueue.services import media as media_svc  # noqa: E402
from huhqueue.services import posts as posts_svc  # noqa: E402
from huhqueue.services import scheduling  # noqa: E402
from huhqueue.connectors import TokenBundle  # noqa: E402

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def card(core, name, title, colors, size=(1080, 1350)):
    img = Image.new("RGB", size, colors[0])
    d = ImageDraw.Draw(img)
    for y in range(size[1]):
        t = y / size[1]
        d.line([(0, y), (size[0], y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(colors[0], colors[1])))
    try:
        f = ImageFont.truetype(FONT, 86)
        f2 = ImageFont.truetype(FONT, 40)
    except OSError:
        f = f2 = ImageFont.load_default()
    d.text((80, 120), "HUHBYTE · AI NEWS", font=f2, fill=(255, 255, 255))
    y = size[1] // 2 - 150
    for line in title.split("\n"):
        d.text((80, y), line, font=f, fill=(255, 255, 255))
        y += 110
    tmp = core.settings.media_dir / f".seed-{name}"
    img.save(tmp, "PNG")
    return media_svc.save_file(core, tmp, name + ".png")


def video_from(core, media, name):
    import subprocess
    src = core.settings.media_dir / media.stored_name
    out = core.settings.media_dir / f".seed-{name}.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(src), "-t", "3", "-vf",
                    "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,format=yuv420p",
                    "-c:v", "libx264", "-r", "30", str(out)], check=True)
    return media_svc.save_file(core, out, name + ".mp4")


def avatar(core, letter, color):
    img = Image.new("RGB", (200, 200), color)
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(FONT, 110)
    except OSError:
        f = ImageFont.load_default()
    w = d.textlength(letter, font=f)
    d.text(((200 - w) / 2, 30), letter, font=f, fill="white")
    m = media_svc.save_file(core, _save(core, img, f"avatar-{letter}"), f"avatar-{letter}.png")
    return f"/files/{m.id}"


def _save(core, img, name):
    p = core.settings.media_dir / f".seed-{name}"
    img.save(p, "PNG")
    return p


def main():
    core = Core()
    now = datetime.utcnow()
    with core.db.session() as s:
        if s.query(Channel).count():
            print("already seeded")
            return
    specs = [("instagram", "huhbyte.ai", "HuhByte AI", 12840, 214, "BUSINESS", "#8A63E6"),
             ("tiktok", "huhbyte", "HuhByte", 30410, 166, "TikTok creator", "#141020"),
             ("youtube", "huhbyte", "HuhByte AI", 5120, 88, "YouTube channel", "#E11D48")]
    ids = {}
    for prov, user, name, fol, mc, kind, color in specs:
        av = avatar(core, user[0].upper() if prov != "youtube" else "Y", color)
        with core.db.session() as s:
            ch = Channel(provider=prov, external_id=f"demo-{prov}", username=user, display_name=name, avatar_url=av,
                         timezone="Europe/Dublin", daily_limit=2,
                         meta={"followers_count": fol, "media_count": mc, "account_type": kind,
                               "profile_synced_at": now.isoformat()})
            core.store_tokens(ch, TokenBundle(access_token="demo-token", expires_at=now + timedelta(days=55)))
            s.add(ch)
            s.flush()
            scheduling.set_slots(s, ch, scheduling.preset_slots("huhbyte"))
            ids[prov] = ch.id
    pal = [((124, 92, 224), (201, 172, 250)), ((20, 16, 32), (109, 70, 201)), ((236, 72, 153), (139, 92, 246)),
           ((14, 165, 233), (124, 92, 224)), ((16, 185, 129), (109, 70, 201))]
    titles = ["GPT-6 preview:\nwhat changed", "YC W27: 5 AI\nstartups to watch", "Gemini runs\non-device now",
              "AI fact: tokens\nare not words", "Open-source model\nbeats GPT-5"]
    medias = [card(core, f"post{i}", t, pal[i]) for i, t in enumerate(titles)]
    queued = [
        ("🚀 GPT-6 preview just dropped - here's what actually changed for builders.\n\n#AI #OpenAI #TechNews", [0], ["instagram", "tiktok"]),
        ("🟧 YC W27: five AI startups worth watching this batch. Swipe →\n\n#YC #AIstartups", [1, 3], ["instagram"]),
        ("Gemini now runs fully on-device. Why that matters for privacy 🔐 #AI #Google", [2], ["instagram", "youtube"]),
        ("🤯 AI fact: a token is not a word. Here's the 15-second explanation.", [3], ["tiktok"]),
    ]
    videos = {i: video_from(core, medias[i], f"clip{i}") for i in (0, 2, 3)}
    for cap, mi, provs in queued:
        chans = []
        for p in provs:
            spec = {"channel_id": ids[p]}
            if p != "instagram":
                spec["media_ids"] = [videos[mi[0]].id]
                if p == "tiktok":
                    spec["options"] = {"tt_mode": "inbox"}
            chans.append(spec)
        posts_svc.create_post(core, {"caption": cap, "media_ids": [medias[i].id for i in mi], "channels": chans,
                                     "when": "queue", "tags": ["news"]})
    posts_svc.create_post(core, {"caption": "Draft: weekly roundup - top 7 AI launches", "media_ids": [medias[4].id],
                                 "channel_ids": [ids["instagram"]], "when": "draft"})
    with core.db.session() as s:
        for i, (prov, days, ins) in enumerate([("instagram", 1, {"views": 18200, "reach": 12100, "likes": 940, "comments": 61, "shares": 120, "saved": 210}),
                                              ("tiktok", 2, {"views": 41200, "likes": 3100, "comments": 140, "shares": 380}),
                                              ("instagram", 3, {"views": 9100, "reach": 7300, "likes": 512, "comments": 22, "shares": 40, "saved": 95}),
                                              ("youtube", 4, {"views": 6400, "likes": 410, "comments": 33})]):
            p = Post(caption=["Claude 5 benchmarks, explained in 30s #AI", "This AI makes 3D from one photo 🤯", "OpenAI DevDay in 60 seconds", "Why small models are winning"][i],
                     media_ids=[medias[i].id], tags=["news"])
            s.add(p)
            base = ins.get("reach") or ins["views"]
            ins["engagement_rate"] = round(100 * (ins["likes"] + ins["comments"] + ins.get("shares", 0)) / base, 2)
            s.add(PostTarget(post=p, channel_id=ids[prov], status="sent", schedule_mode="queue",
                             scheduled_at=now - timedelta(days=days), published_at=now - timedelta(days=days),
                             external_id=f"demo{i}", permalink="https://example.invalid/demo", insights=ins, insights_at=now))
        p = Post(caption="FAIL demo: media URL unreachable", media_ids=[medias[4].id])
        s.add(p)
        s.add(PostTarget(post=p, channel_id=ids["instagram"], status="failed", schedule_mode="custom", scheduled_at=now - timedelta(hours=3),
                         attempts=4, last_error="Instagram could not download the media. Make sure the public media URL / tunnel is running."))
        for j, (a, t) in enumerate([("ai_fan", "Which model is the fastest right now?"), ("dev.daily", "Saved! Great breakdown 🙌"), ("mlnerd", "Source for the benchmark?")]):
            s.add(Comment(channel_id=ids["instagram"], external_id=f"dc{j}", media_external_id="demo0", media_permalink="https://example.invalid/demo",
                          media_caption="Claude 5 benchmarks, explained in 30s", author=a, text=t, at=now - timedelta(hours=j + 1), like_count=j))
        s.add(Comment(channel_id=ids["youtube"], external_id="dy1", media_external_id="demo3", media_caption="Why small models are winning",
                      author="Priya K", text="More videos like this please!", at=now - timedelta(hours=5)))
        for t, col in [("Explain Mixture-of-Experts in 30s", "todo"), ("YC Demo Day recap carousel", "in_progress"), ("AI fact series #12", "unassigned"), ("Cursor vs Windsurf", "done")]:
            s.add(Idea(title=t, body="", column=col, tags=["fact"] if "fact" in t else []))
    print("seeded", ids)


if __name__ == "__main__":
    main()
