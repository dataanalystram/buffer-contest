"""Render the example brands' real outputs: covers per channel, a recap carousel, a launch carousel, a tutorial, a Reel
with the front and end card, a captioned quote clip, and per-channel captions.

    python scripts/render_brand_examples.py [--out DIR] [--png-out DIR]

Writes JPEGs + MP4s + index.json + captions.md to studio/static/examples/<brand>/ (the sample workspace's gallery) and,
with --png-out, full-quality PNGs + MP4s to <DIR>/<brand>/. Content is the recorded Sources fixtures
(studio/sample_sources/raw, recorded 31 Oct 2026): real headlines from the labs' own feeds and YC Launches. No network.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from studio import brand_examples, sources_agent, templates  # noqa: E402

NOW = datetime(2026, 10, 31, 20, 0, tzinfo=timezone.utc)
# Real stories from the recorded feeds (31 Oct 2026); each line is faithful to the feed item it cites.
RECAP = [
    {"title": "Gemini 4 Carbon: Google DeepMind's next frontier model", "body": "Announced on the Google DeepMind blog this week.",
     "tag": "Models", "source": "deepmind.google"},
    {"title": "OpenAI will honour a shared robots.txt signal for AI training",
     "body": "One signal for website owners to say how their content may be used for training.", "tag": "Policy", "source": "openai.com"},
    {"title": "Mistral Large 4 lands on Hugging Face", "body": "Hugging Face welcomed it as \"Le Chonk\".", "tag": "Open models",
     "source": "huggingface.co"},
    {"title": "Genie 4: a new generation of world simulation", "body": "A world model built for realism in real-time simulation.",
     "tag": "Research", "source": "deepmind.google"},
    {"title": "Granite Switch 4.1.1: update models like you update software", "body": "A new release, announced on the Hugging Face blog.",
     "tag": "Tools", "source": "huggingface.co"},
    {"title": "Composal: agents that test your product like real users", "body": "A YC Fall 2026 launch: catch bugs before your users do.",
     "tag": "Launch", "source": "ycombinator.com"},
    {"title": "A global map of the world's forest types", "body": "Every 10-metre patch of land on Earth, sorted into forest types.",
     "tag": "Research", "source": "deepmind.google"},
]
BREAKING = {"title": "OpenAI will honour a shared robots.txt signal for AI training", "source": "openai.com", "when": "30 Oct"}
LAUNCH = {"product": "Composal", "company": "YC Fall 2026", "tagline": "a swarm of agents that tests your product like real users",
          "features": ["Agents use your product the way real users do", "Catch bugs before your users do", "Launched on YC Launches, 31 Oct 2026"],
          "price": "", "why": "Testing that scales with the agents, not the team."}
CONTENT = {
    "huhbyte": {
        "quote": {"quote": "Agents should draft. People should decide what goes out.", "speaker": "HuhByte AI", "role": "How we run our feed"},
        "tutorial": {"task": "caption every Reel", "tool": "an open-source speech model", "steps": [
            {"title": "Export your audio", "body": "Any MP3 or the video file itself works."},
            {"title": "Run the speech model", "body": "It runs on a laptop CPU, no paid API."},
            {"title": "Burn the captions in", "body": "Brand font, three words at a time, bottom third clear."}]},
    },
    "buffer": {
        "quote": {"quote": "Default to transparency.", "speaker": "Buffer", "role": "One of Buffer's public company values"},
        "tutorial": {"task": "plan a month of posts in an hour", "tool": "a content calendar", "steps": [
            {"title": "Pick three pillars", "body": "Teach, show the work, share a win. Every post fits one."},
            {"title": "Batch the captions", "body": "Write twelve in one sitting; edit tomorrow with fresh eyes."},
            {"title": "Fill the queue", "body": "Same times each week, so your audience learns when to look."}]},
    },
}


def story_for_captions() -> dict:
    items = sources_agent.ADAPTERS["rss"].sample()
    it = next(i for i in items if "training preferences" in i["title"])
    st = sources_agent.story_from([dict(it, also=[])])
    return sources_agent.finish_story(st, "policy_law")


def save(im: Image.Image, base: Path, png_dir: Path | None, name: str, files: list) -> None:
    im.convert("RGB").save(base / f"{name}.jpg", "JPEG", quality=88, optimize=True, progressive=True)
    if png_dir:
        im.convert("RGB").save(png_dir / f"{name}.png", "PNG", optimize=True)
    files.append({"name": name, "file": f"{name}.jpg", "w": im.width, "h": im.height})


def sheet(ims: list[Image.Image], h: int = 540) -> Image.Image:
    th = [i.resize((int(i.width * h / i.height), h)) for i in ims]
    out = Image.new("RGB", (sum(t.width for t in th) + 16 * (len(th) - 1), h), (24, 24, 24))
    x = 0
    for t in th:
        out.paste(t, (x, 0))
        x += t.width + 16
    return out


def render(example_id: str, out_root: Path, png_root: Path | None, video: bool = True) -> dict:
    ex = brand_examples.EXAMPLES[example_id]
    b = templates.Brand(ex["kit"], brand_examples.logo_bytes(example_id))
    base = out_root / example_id
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    png_dir = None
    if png_root:
        png_dir = png_root / example_id
        if png_dir.exists():
            shutil.rmtree(png_dir)
        png_dir.mkdir(parents=True)
    files: list = []
    covers = []
    for svc in ex["channels"]:
        spec = templates.CHANNELS[svc]
        ratio = spec["video_ratio"] if spec.get("video_only") else spec["ratio"]
        im = templates.breaking_card(b, ratio, BREAKING["title"], BREAKING["source"], BREAKING["when"])
        save(im, base, png_dir, f"cover-{svc}", files)
        covers.append(im)
    recap = templates.slides("weekly_recap", {"stories": RECAP, "range": "25\u201331 Oct"}, b, "4:5", now=NOW)
    for i, im in enumerate(recap):
        save(im, base, png_dir, f"recap-{i + 1:02d}", files)
    recap_x = templates.slides("weekly_recap", {"stories": RECAP, "range": "25\u201331 Oct"}, b, "16:9", now=NOW, max_slides=4)
    for i, im in enumerate(recap_x):
        save(im, base, png_dir, f"recap-x-{i + 1:02d}", files)
    for tpl, data in (("launch", LAUNCH), ("tutorial", CONTENT[example_id]["tutorial"]), ("quote", CONTENT[example_id]["quote"]),
                      ("midweek_recap", {"stories": RECAP[:3], "range": "26\u201328 Oct"})):
        for i, im in enumerate(templates.slides(tpl, data, b, "4:5", now=NOW)):
            save(im, base, png_dir, f"{tpl}-{i + 1:02d}", files)
    sheet(recap).save(base / "overview-recap.jpg", quality=85)
    sheet(covers).save(base / "overview-covers.jpg", quality=85)
    if png_dir:
        sheet(recap).save(png_dir / "overview-recap.png")
        sheet(covers).save(png_dir / "overview-covers.png")
    videos = []
    if video and templates.have_ffmpeg():
        reel_slides = templates.slides("weekly_recap", {"stories": RECAP[:5], "range": "25\u201331 Oct"}, b, "9:16", now=NOW)
        mp4 = templates.reel(reel_slides)
        (base / "reel-weekly_recap.mp4").write_bytes(mp4)
        q = CONTENT[example_id]["quote"]
        mp4q = templates.quote_clip(b, q["quote"], q["speaker"], q["role"], "9:16")
        (base / "reel-quote.mp4").write_bytes(mp4q)
        videos = [{"name": "reel-weekly_recap", "file": "reel-weekly_recap.mp4", "bytes": len(mp4)},
                  {"name": "reel-quote", "file": "reel-quote.mp4", "bytes": len(mp4q)}]
        if png_dir:
            for v in videos:
                shutil.copy(base / v["file"], png_dir / v["file"])
    # per-channel captions: one real story, written for each of the brand's channels by the built-in writer
    st = story_for_captions()
    caps = {}
    for svc in ex["channels"]:
        from studio import drafter
        caps[svc] = drafter.fit(svc, sources_agent.caption(svc, st, {**ex["kit"]}))
    md = [f"# {ex['title']}: per-channel captions", "", f"Story (recorded feed item, 31 Oct 2026): {st['headline']} ({st['url']})", ""]
    for svc, text in caps.items():
        md += [f"## {templates.CHANNELS[svc]['label']} ({drafter.count(svc, text)} of {drafter.limit_for(svc)} characters)", "", "```", text, "```", ""]
    (base / "captions.md").write_text("\n".join(md))
    if png_dir:
        (png_dir / "captions.md").write_text("\n".join(md))
    index = {"brand": example_id, "title": ex["title"], "label": ex["kit"]["label"], "channels": ex["channels"],
             "images": files, "videos": videos, "captions": caps, "rendered": NOW.isoformat()}
    (base / "index.json").write_text(json.dumps(index, indent=1))
    if png_dir:
        (png_dir / "index.json").write_text(json.dumps(index, indent=1))
    return index


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "studio" / "static" / "examples"))
    ap.add_argument("--png-out", default="")
    ap.add_argument("--no-video", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    for ex in (args.only.split(",") if args.only else brand_examples.EXAMPLES):
        idx = render(ex, Path(args.out), Path(args.png_out) if args.png_out else None, video=not args.no_video)
        print(ex, len(idx["images"]), "images,", len(idx["videos"]), "videos")


if __name__ == "__main__":
    main()
