"""The media agent's tool interface: what hq-agent-core's brain (and REST / MCP, via its registry) calls.

Every tool takes plain JSON args and returns a small JSON dict. Light tools (find, styles, plan) run anywhere,
including the free web dyno. Heavy tools (fetch, transcribe, render, generate_images, music) need FFmpeg and run on
the video worker (GitHub Actions ``render.yml`` today, or a paid Render instance, see docs/MEDIA_PIPELINE.md); their
spec carries ``"worker": true`` so the brain queues them as a render job instead of running them in the request.

Rights: rendering a third-party video (anything ``find`` did not mark ``official``, or any video at all unless the
person confirms) needs ``rights_confirmed: true``. Without it the tool returns ``needs_confirmation`` with the
question to ask, so the agent asks the person once ("I'll use Aarush Yadav's launch video from X with credit. OK?").

    from studio.media import tools
    tools.call("media_plan", {"prompt": "post about this in all my channels <url> make it a short punchy explainer",
                              "channels": ["instagram", "tiktok", "linkedin", "x"]})
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from . import styles as _styles
from .find import find_launch_video, urls_in

S = {"type": "string"}
BRAND = {"type": "object", "description": "Brand kit: name, handle, accent, bg, text, logo (local path), head_font, body_font, end_line"}
SPECS = [
    {"name": "media_plan", "worker": False,
     "description": "Read the person's request and plan the media: which edit style fits their words, which aspect ratio each channel needs, and the best real launch video for the link (or that images must be generated). Run this first.",
     "input_schema": {"type": "object", "required": ["prompt"], "properties": {
         "prompt": S, "channels": {"type": "array", "items": S, "description": "Channel services, e.g. instagram, tiktok, linkedin, x"}}}},
    {"name": "media_find", "worker": False,
     "description": "Find the REAL launch/demo video for a link or story on any platform (X, YouTube, Vimeo, Loom, YC launch pages, HN, GitHub, company pages via og:video). Returns ranked candidates with credit lines; best_downloadable is the one the worker can fetch.",
     "input_schema": {"type": "object", "required": ["query"], "properties": {"query": S, "max_results": {"type": "integer"}}}},
    {"name": "media_styles", "worker": False, "description": "List the edit styles (opus, news = huhbyte_news, minimal, hype) with what each looks like and is best for.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "media_fetch", "worker": True,
     "description": "Download one candidate from media_find to the run folder (size-capped, vetted as plain video).",
     "input_schema": {"type": "object", "required": ["candidate"], "properties": {"candidate": {"type": "object"}, "run_dir": S}}},
    {"name": "media_transcribe", "worker": True,
     "description": "Word-level transcript of a fetched video for captions and auto-clipping. names: product/company names to spell right.",
     "input_schema": {"type": "object", "required": ["path"], "properties": {"path": S, "names": {"type": "array", "items": S}, "model": S}}},
    {"name": "media_render", "worker": True,
     "description": "Edit the video (or images) into branded shorts in one style for every ratio needed (9:16, 4:5, 1:1): hook in the first second, captions, credit, Follow end card, music under silent video. Third-party video needs rights_confirmed.",
     "input_schema": {"type": "object", "required": ["style", "hook"], "properties": {
         "source_path": S, "images": {"type": "array", "items": S},
         "style": {"type": "string", "enum": ["opus", "news", "huhbyte_news", "minimal", "hype"]},
         "ratios": {"type": "array", "items": {"type": "string", "enum": ["9:16", "4:5", "1:1"]}},
         "hook": {"type": "string", "description": "Max ~12 words, only facts from the source"},
         "facts": {"type": "array", "items": S}, "credit": S, "words_path": S, "names": {"type": "array", "items": S},
         "clip": {"type": "array", "items": {"type": "number"}, "description": "[start, end] seconds; omit for the style default"},
         "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]}, "brand": BRAND,
         "official": {"type": "boolean"}, "rights_confirmed": {"type": "boolean"}, "run_dir": S}}},
    {"name": "media_generate_images", "worker": True,
     "description": "No usable video or photo: generate n images for the story (configured image model, else on-brand cards). Labelled as AI-generated.",
     "input_schema": {"type": "object", "required": ["prompt"], "properties": {"prompt": S, "headline": S, "n": {"type": "integer"},
                                                                              "ratio": S, "brand": BRAND, "run_dir": S}}},
    {"name": "media_music", "worker": True, "description": "Original royalty-free music (cinematic or pulse) of a given length, as a WAV.",
     "input_schema": {"type": "object", "required": ["seconds"], "properties": {"seconds": {"type": "number"}, "mood": S, "run_dir": S}}},
]
NAMES = [s["name"] for s in SPECS]


def _run_dir(args: dict) -> Path:
    p = Path(args.get("run_dir") or os.environ.get("HQ_MEDIA_RUN_DIR") or "/tmp/hq-media-run")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _brief(c: dict) -> dict:
    keep = ("url", "platform", "kind", "how", "confidence", "title", "uploader", "duration", "official", "downloadable",
            "credit", "source_page", "thumbnail", "note", "blocked", "company")
    return {k: c[k] for k in keep if c.get(k) not in (None, "")}


def media_find(query: str, max_results: int = 5, http=None) -> dict:
    cands = find_launch_video(query, http=http, max_results=max(max_results, 6))
    vids = [c for c in cands if c["kind"] == "video"]
    best = vids[0] if vids else None
    best_dl = next((c for c in vids if c.get("downloadable")), None)
    imgs = [c for c in cands if c["kind"] == "image"]
    note = ""
    if best and best_dl and best is not best_dl:
        note = (f"The launch video is {best['url']} but this server can't fetch it ({best.get('blocked') or 'not downloadable'}); "
                f"the best fetchable video is {best_dl['url']}. Link the launch video in the caption.")
    elif not vids:
        note = "No video found: use the images, or generate some (media_generate_images)."
    return {"best_video": _brief(best) if best else None, "best_downloadable": _brief(best_dl) if best_dl else None,
            "candidates": [_brief(c) for c in cands[:max_results]], "images": [_brief(c) for c in imgs[:3]], "note": note}


def ratios_for(channels: list[str] | None) -> dict:
    out: dict[str, list[str]] = {}
    for ch in channels or ["instagram_reel", "tiktok", "linkedin", "x"]:
        r = _styles.CHANNEL_RATIO.get(ch.lower(), "4:5")
        out.setdefault(r, []).append(ch)
    return out


def media_plan(prompt: str, channels: list[str] | None = None, http=None, find: bool = True) -> dict:
    style = _styles.pick(prompt)
    m = re.search(r"(\d{2,3})\s*(?:s\b|sec|seconds)", prompt or "")
    plan = {"style": style, "style_about": _styles.STYLE_PRESETS[style]["about"], "ratios": ratios_for(channels),
            "target_seconds": int(m.group(1)) if m else None, "links": urls_in(prompt)}
    if find and plan["links"]:
        plan["media"] = media_find(prompt, http=http)
        bv = plan["media"]["best_downloadable"]
        plan["needs_rights_confirmation"] = bool(bv and not bv.get("official"))
        plan["next"] = ("media_fetch -> media_transcribe -> media_render" if bv else "media_generate_images -> media_render")
    return plan


def media_fetch(candidate: dict, run_dir: str | None = None, http=None) -> dict:
    from .download import download_video
    d = _run_dir({"run_dir": run_dir})
    return download_video(candidate, d / "source.mp4", http=http)


def media_transcribe(path: str, names: list[str] | None = None, model: str | None = None, run_dir: str | None = None) -> dict:
    from .transcribe import transcribe
    r = transcribe(path, model=model, names=names)
    wp = _run_dir({"run_dir": run_dir}) / "words.json"
    wp.write_text(json.dumps(r))
    return {"words_path": str(wp), "words": len(r["words"]), "language": r["language"], "model": r["model"], "text": r["text"][:1200]}


def media_render(style: str, hook: str, source_path: str | None = None, images: list[str] | None = None,
                 ratios: list[str] | None = None, facts: list[str] | None = None, credit: str = "", words_path: str | None = None,
                 names: list[str] | None = None, clip: list[float] | None = None, music: str = "auto", brand: dict | None = None,
                 official: bool = False, rights_confirmed: bool = False, run_dir: str | None = None) -> dict:
    from .compose import render_style
    style = _styles.resolve(style)
    if style not in _styles.STYLE_PRESETS:
        return {"error": f"Unknown style {style!r}. Pick one of: opus, news, minimal, hype."}
    if source_path and not (rights_confirmed or official):
        return {"needs_confirmation": True,
                "question": f"This uses someone else's video{(' (' + credit + ')') if credit else ''}. I'll credit it on screen "
                            "and in the caption. Do you have the right to repost it (or is it fair commentary)?"}
    if source_path and not credit:
        return {"error": "A credit line is required for any video you didn't make (e.g. 'Video: @handle on X')."}
    words = json.loads(Path(words_path).read_text())["words"] if words_path else []
    d = _run_dir({"run_dir": run_dir})
    outs = []
    for r in ratios or ["9:16"]:
        out = d / f"{style}_{r.replace(':', 'x')}.mp4"
        m = render_style(source_path, style, out=str(out), ratio=r, hook=hook, facts=facts, brand=brand, words=words,
                         credit=credit, clip=tuple(clip) if clip else None, images=images, music_mood=music, names=names)
        outs.append({k: m[k] for k in ("path", "ratio", "width", "height", "duration", "clip", "captions", "burned_subs",
                                       "music", "caption_problems")})
    return {"renders": outs}


def media_generate_images(prompt: str, headline: str = "", n: int = 3, ratio: str = "9:16", brand: dict | None = None,
                          run_dir: str | None = None, http=None) -> dict:
    from .imagegen import generate_images
    d = _run_dir({"run_dir": run_dir}) / "images"
    return {"images": generate_images(prompt, str(d), n=n, ratio=ratio, headline=headline, brand=brand, http=http)}


def media_music(seconds: float, mood: str = "cinematic", run_dir: str | None = None) -> dict:
    from . import music
    p = _run_dir({"run_dir": run_dir}) / f"music_{mood}.wav"
    music.make(str(p), float(seconds), mood)
    return {"path": str(p), "mood": mood, "seconds": seconds, "licence": "original, royalty-free (synthesised in code)"}


def call(name: str, args: dict | None = None, http=None) -> dict:
    """Dispatch a tool by name with JSON args (what the brain / REST / MCP layer calls)."""
    a = dict(args or {})
    if name == "media_styles":
        return {"styles": _styles.describe()}
    if name == "media_plan":
        return media_plan(a.get("prompt", ""), a.get("channels"), http=http)
    if name == "media_find":
        return media_find(a["query"], int(a.get("max_results") or 5), http=http)
    if name == "media_fetch":
        return media_fetch(a["candidate"], a.get("run_dir"), http=http)
    if name == "media_transcribe":
        return media_transcribe(a["path"], a.get("names"), a.get("model"), a.get("run_dir"))
    if name == "media_render":
        return media_render(**{k: v for k, v in a.items() if k in media_render.__code__.co_varnames})
    if name == "media_generate_images":
        return media_generate_images(a["prompt"], a.get("headline", ""), int(a.get("n") or 3), a.get("ratio", "9:16"),
                                     a.get("brand"), a.get("run_dir"), http=http)
    if name == "media_music":
        return media_music(a["seconds"], a.get("mood", "cinematic"), a.get("run_dir"))
    raise KeyError(f"Unknown media tool {name!r}. Tools: {', '.join(NAMES)}")
