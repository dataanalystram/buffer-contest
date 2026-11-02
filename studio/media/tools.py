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
    {"name": "media_styles", "worker": False, "description": "List every look: the four edit styles (opus, news = huhbyte_news, minimal, hype) and the video themes (caption_news, news_package, newsreel, dark_keynote, cinematic, crt, ...) with what each looks like and is best for.",
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
         "style": {"type": "string", "enum": ["news"] + _styles.ALL_IDS, "description": "A base style or a video theme"},
         "brand_colors": {"type": "boolean", "description": "Keep the brand's accent inside a theme"},
         "ratios": {"type": "array", "items": {"type": "string", "enum": ["9:16", "4:5", "1:1"]}},
         "hook": {"type": "string", "description": "Max ~12 words, only facts from the source"},
         "facts": {"type": "array", "items": S}, "credit": S, "words_path": S, "names": {"type": "array", "items": S},
         "clip": {"type": "array", "items": {"type": "number"}, "description": "[start, end] seconds; omit for the style default"},
         "music": {"type": "string", "enum": ["auto", "cinematic", "pulse", "none"]}, "brand": BRAND,
         "official": {"type": "boolean"}, "rights_confirmed": {"type": "boolean"}, "run_dir": S}}},
    {"name": "media_moments", "worker": False,
     "description": "Long video -> the best moments to clip (Opus-style): N non-overlapping spans of whole sentences with a score, the reason each was picked and a hook title. Needs the transcript (words_path from media_transcribe, or caption text from an .srt/.vtt the source already has).",
     "input_schema": {"type": "object", "properties": {"words_path": S, "captions": {"type": "string", "description": "SRT or WebVTT text"},
                                                        "prompt": S, "n": {"type": "integer"}, "duration": {"type": "number"}}}},
    {"name": "media_split", "worker": True,
     "description": "Split a long video into N short clips ('split this into 5 shorts', 'find the best moments'): picks the moments, reuses the source's captions when it has them (else Whisper), reframes each clip around the speaker per ratio, hook title, word captions (skipped over burned-in subtitles), credit and Follow end card. Third-party video needs rights_confirmed.",
     "input_schema": {"type": "object", "required": ["source_path"], "properties": {
         "source_path": S, "url": {"type": "string", "description": "the post the video came from (its captions are reused)"},
         "captions": {"type": "string", "description": "SRT or WebVTT text the person uploaded"}, "prompt": S,
         "n": {"type": "integer"}, "ratios": {"type": "array", "items": {"type": "string", "enum": ["9:16", "4:5", "1:1"]}},
         "credit": S, "official": {"type": "boolean"}, "rights_confirmed": {"type": "boolean"}, "brand": BRAND,
         "names": {"type": "array", "items": S}, "run_dir": S}}},
    {"name": "media_recap_plan", "worker": False,
     "description": "Weekly recap ('make a 7 days what happened in AI recap with videos'): the week's top AI stories from the news sources, each with its official video/image (X posts through the free X reader), credit lines, and the one rights question for media that isn't the company's own.",
     "input_schema": {"type": "object", "properties": {"prompt": S, "n": {"type": "integer"}, "days": {"type": "integer"}}}},
    {"name": "media_recap", "worker": True,
     "description": "Render a recap plan (after media_recap_plan and the person's answer): HuhByte weekly-recap format, cover 'DO YOU REALIZE WHAT JUST HAPPENED IN AI IN THE LAST 7 DAYS?', one branded segment per story with its credit, Follow card only at the end; a 4:5 carousel of video slides plus one 9:16 Reel.",
     "input_schema": {"type": "object", "required": ["plan"], "properties": {"plan": {"type": "object"},
                                                                            "rights_confirmed": {"description": "true, false, or the story numbers OK'd"},
                                                                            "brand": BRAND, "run_dir": S}}},
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


def media_find(query: str, max_results: int = 5, http=None, youtube_api_key: str | None = None) -> dict:
    cands = find_launch_video(query, http=http, max_results=max(max_results, 6), **({"youtube_api_key": youtube_api_key} if youtube_api_key else {}))
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
    from .longform import parse_request
    style, why = _styles.pick_theme(prompt)
    m = re.search(r"(\d{2,3})\s*(?:s\b|sec|seconds)", prompt or "")
    sp = parse_request(prompt)
    plan = {"style": style, "style_about": _styles.config(style)["about"], "theme_why": why, "ratios": ratios_for(channels),
            "target_seconds": int(m.group(1)) if m else None, "links": urls_in(prompt)}
    if sp["split"]:
        plan["style"], plan["style_about"] = "opus", _styles.STYLE_PRESETS["opus"]["about"]
        plan["split"] = {"clips": sp["n"] or "3-5", "seconds": [sp["lo"], sp["hi"]]}
    if find and plan["links"]:
        plan["media"] = media_find(prompt, http=http)
        bv = plan["media"]["best_downloadable"]
        plan["needs_rights_confirmation"] = bool(bv and not bv.get("official"))
        plan["next"] = ("media_fetch -> media_split" if sp["split"] and bv else
                        "media_fetch -> media_transcribe -> media_render" if bv else "media_generate_images -> media_render")
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
                 official: bool = False, rights_confirmed: bool = False, run_dir: str | None = None,
                 brand_colors: bool = False, captions_mode: str = "auto", detect_own_captions: bool = False) -> dict:
    from .compose import render_style
    style = _styles.resolve(style)
    if not _styles.known(style):
        return {"error": f"Unknown style {style!r}. Pick one of: {', '.join(_styles.ALL_IDS)}."}
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
                         credit=credit, clip=tuple(clip) if clip else None, images=images, music_mood=music, names=names,
                         brand_colors=brand_colors, captions_mode=captions_mode, detect_own_captions=detect_own_captions)
        outs.append({k: m.get(k) for k in ("path", "ratio", "width", "height", "duration", "clip", "captions", "burned_subs",
                                           "music", "caption_problems", "captions_note", "source_captions")})
    return {"renders": outs}


def media_moments(words_path: str | None = None, captions: str | None = None, prompt: str = "", n: int | None = None,
                  duration: float | None = None) -> dict:
    from . import longform, subtitles
    if captions:
        words = subtitles.parse_captions(captions)
    elif words_path:
        words = json.loads(Path(words_path).read_text())["words"]
    else:
        return {"error": "Give words_path (media_transcribe) or captions (SRT/WebVTT text)."}
    sp = longform.parse_request(prompt)
    r = longform.pick_moments(words, duration, n or sp["n"], sp["lo"], sp["hi"])
    keep = ("id", "rank", "start", "end", "duration", "score", "reason", "hook")
    return {"moments": [{k: m[k] for k in keep} for m in r["moments"]], "picked_by": r["picked_by"], "sentences": r["sentences"]}


def media_split(source_path: str, url: str | None = None, captions: str | None = None, prompt: str = "", n: int | None = None,
                ratios: list[str] | None = None, credit: str = "", official: bool = False, rights_confirmed: bool = False,
                brand: dict | None = None, names: list[str] | None = None, run_dir: str | None = None) -> dict:
    from . import longform
    d = _run_dir({"run_dir": run_dir}) / "clips"
    man = longform.split_video(source_path, out_dir=str(d), url=url, caption_text=captions, prompt=prompt, n=n, ratios=ratios,
                               brand=brand, credit=credit, official=official, rights_confirmed=rights_confirmed, names=names)
    if man.get("needs_confirmation") or man.get("error"):
        return man
    return {"transcript": man["transcript"], "burned_subs": man["burned_subs"], "picked_by": man["picked_by"],
            "clips": [{k: c[k] for k in ("id", "rank", "start", "end", "score", "reason", "hook")} |
                      {"renders": [{k: r[k] for k in ("path", "ratio", "duration", "captions")} | {"reframe": r["reframe"]["mode"]}
                                   for r in c["renders"]]} for c in man["clips"]]}


def media_recap_plan(prompt: str = "", n: int | None = None, days: int | None = None, http=None) -> dict:
    from . import recap
    want = recap.wants_recap(prompt)
    stories = recap.gather_week(http=http, days=days or want["days"], n=n or want["n"] or 5)
    return recap.plan(stories, find=lambda u: media_find(u, http=http), video=True, http=http)


def media_recap(plan: dict, rights_confirmed=None, brand: dict | None = None, run_dir: str | None = None, http=None) -> dict:
    from . import recap
    from .download import DownloadError, download_video
    if plan.get("needs_confirmation") and rights_confirmed is None:
        return {"needs_confirmation": True, "question": plan.get("question")}
    p = recap.apply_rights(dict(plan), rights_confirmed if rights_confirmed is not None else True)
    d = _run_dir({"run_dir": run_dir}) / "recap"
    d.mkdir(parents=True, exist_ok=True)
    media: dict[int, str | None] = {}
    for s in p["segments"]:
        m = s.get("media")
        media[s["i"]] = None
        if not m:
            continue
        ext = ".mp4" if m["kind"] == "video" else ".jpg"
        try:  # videos: direct files through netguard, platform posts through single-extractor yt-dlp, then vetted;
            # images: netguard (every hop checked, 15 MB cap), then recap.usable() checks they really are images
            if m["kind"] == "video":
                media[s["i"]] = download_video(m.get("candidate") or m["url"], d / f"src_{s['i']}{ext}", http=http)["path"]
            else:
                from .. import netguard
                f = d / f"src_{s['i']}{ext}"
                f.write_bytes(netguard.safe_get(m["url"], http=http, limit=15_000_000, timeout=30).content)
                media[s["i"]] = str(f)
        except (DownloadError, Exception):  # noqa: BLE001 - that story gets its title plate
            media[s["i"]] = None
    out = recap.compose(p, media, str(d), brand=brand)
    return {k: v for k, v in out.items() if k != "boxes"}


def recap_files(out: dict) -> list[tuple[str, str, dict]]:
    """studio.media.tools.media_recap's result -> [(file id, path, meta)] in the order the worker posts them."""
    files = [("cover", out["cover"], {"ratio": "4:5"})]
    files += [(f"s{sl['i']}", sl["path"], {"duration": sl.get("duration"), "ratio": "4:5", "credit": sl.get("credit") or ""})
              for sl in out.get("slides") or []]
    files.append(("follow", out["follow"], {"ratio": "4:5"}))
    if out.get("reel"):
        files.append(("reel", out["reel"]["path"], {"duration": out["reel"].get("duration"), "ratio": "9:16"}))
    return files


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
        return {"styles": _styles.describe(), "themes": _styles.describe_themes()}
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
    if name == "media_moments":
        return media_moments(a.get("words_path"), a.get("captions"), a.get("prompt", ""), a.get("n"), a.get("duration"))
    if name == "media_split":
        return media_split(**{k: v for k, v in a.items() if k in media_split.__code__.co_varnames})
    if name == "media_recap_plan":
        return media_recap_plan(a.get("prompt", ""), a.get("n"), a.get("days"), http=http)
    if name == "media_recap":
        return media_recap(a["plan"], a.get("rights_confirmed"), a.get("brand"), a.get("run_dir"), http=http)
    if name == "media_generate_images":
        return media_generate_images(a["prompt"], a.get("headline", ""), int(a.get("n") or 3), a.get("ratio", "9:16"),
                                     a.get("brand"), a.get("run_dir"), http=http)
    if name == "media_music":
        return media_music(a["seconds"], a.get("mood", "cinematic"), a.get("run_dir"))
    raise KeyError(f"Unknown media tool {name!r}. Tools: {', '.join(NAMES)}")
