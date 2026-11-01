"""The render worker's side of studio.media: one ``media_render`` job in, one MP4 out.

agent-core queues the job (``render_jobs.spec``); the GitHub Actions worker (``render.yml`` -> ``studio.render_worker``)
or, off the hosted app, the web process itself (``house.render_job_local``) calls :func:`render_job`. Spec::

    {"kind": "media_render", "style": "opus|news|minimal|hype", "ratio": "9:16", "hook": "...", "facts": [...],
     "credit": "Video: @handle on X", "names": [...], "music": "auto", "official": false, "rights_confirmed": true,
     "kit": {brand kit}, "logo": token, "source": token | "", "candidate": {a media_find candidate} | null,
     "slides": [image tokens], "image_prompt": "..."}

Where the video comes from, in order: an uploaded / already-fetched clip (``source`` token, read through the app's
media URL), else the found candidate (``media_fetch``: netguard for files, single-extractor yt-dlp for platforms),
else images (``slides``), else generated images. The rights gate in ``tools.media_render`` still applies here.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from . import styles, tools

CAPTION_STYLES = {"opus", "huhbyte_news", "minimal", "hype"}  # every style captions speech when there is a transcript


class JobError(RuntimeError):
    pass


def brand_from_kit(kit: dict | None, logo_path: str | None = None) -> dict:
    """agent-core's brand kit (studio.brand.DEFAULT_KIT shape) -> compose.py's brand dict."""
    kit = kit or {}
    col = kit.get("colors") or {}
    b: dict = {}
    if kit.get("name"):
        b["name"] = kit["name"]
    if kit.get("handle"):
        h = str(kit["handle"]).strip()
        b["handle"] = h if h.startswith("@") else "@" + h
    for src, dst in (("accent", "accent"), ("primary", "bg"), ("text", "text")):
        if col.get(src):
            b[dst] = col[src]
    end = kit.get("end_card") or {}
    if end.get("line"):
        b["end_line"] = end["line"]
    if logo_path:
        b["logo"] = logo_path
    return b


def render_job(spec: dict, get: Callable[[str], bytes | None], urls: dict | None = None, logo: bytes | None = None,
               http=None) -> tuple[bytes, dict]:
    """Run one media_render job. ``get(url)`` fetches an app media URL (the worker passes its netguard getter);
    ``urls`` is the job's URL map from the app (source / slides). Returns (mp4 bytes, meta)."""
    urls = urls or {}
    style = styles.resolve(spec.get("style") or "huhbyte_news")
    if not styles.known(style):
        raise JobError(f"Unknown style {spec.get('style')!r}.")
    ratio = spec.get("ratio") or "9:16"
    tmp = Path(tempfile.mkdtemp(prefix="hq_media_job_"))
    try:
        logo_path = None
        if logo:
            logo_path = str(tmp / "logo.png")
            Path(logo_path).write_bytes(logo)
        brand = brand_from_kit(spec.get("kit"), logo_path)
        source_path, images, official, credit = None, None, bool(spec.get("official")), spec.get("credit") or ""
        cand = spec.get("candidate")
        if urls.get("source"):
            data = get(urls["source"])
            if not data:
                raise JobError("The source video couldn't be read from the app.")
            source_path = str(tmp / "source.mp4")
            Path(source_path).write_bytes(data)
        elif cand:
            got = tools.media_fetch(cand, run_dir=str(tmp), http=http)
            source_path = got.get("path") or str(tmp / "source.mp4")
            official = official or bool(cand.get("official"))
            credit = credit or cand.get("credit") or ""
        elif urls.get("slides"):
            images = []
            for i, u in enumerate(urls["slides"]):
                b = get(u)
                if b:
                    p = tmp / f"slide_{i}.jpg"
                    p.write_bytes(b)
                    images.append(str(p))
        if not source_path and not images:
            gen = tools.media_generate_images(spec.get("image_prompt") or spec.get("hook") or "", headline=spec.get("hook", ""),
                                              n=3, ratio=ratio, brand=brand, run_dir=str(tmp), http=http)
            images = [g["path"] for g in gen["images"]]
            credit = credit or gen["images"][0]["credit"]

        words_path = None
        if source_path and (style in CAPTION_STYLES or style in styles.THEMES):
            try:  # captions and the opus/hype clip picker need the transcript; a failure only turns captions off
                tr = tools.media_transcribe(source_path, names=spec.get("names"), run_dir=str(tmp))
                words_path = tr["words_path"] if tr.get("words") else None
            except Exception as e:  # noqa: BLE001
                print(f"transcribe skipped: {e.__class__.__name__}: {str(e)[:120]}")
        r = tools.media_render(style, spec.get("hook") or "", source_path=source_path, images=images, ratios=[ratio],
                               facts=spec.get("facts"), credit=credit, words_path=words_path, names=spec.get("names"),
                               clip=spec.get("clip"), music=spec.get("music") or "auto", brand=brand, official=official,
                               rights_confirmed=bool(spec.get("rights_confirmed")), run_dir=str(tmp),
                               brand_colors=bool(spec.get("brand_colors")))
        if r.get("needs_confirmation"):
            raise JobError("Rights not confirmed: " + r["question"])
        if r.get("error"):
            raise JobError(r["error"])
        out = r["renders"][0]
        meta = {"type": "video", "renderer": "studio.media", "style": style, "ratio": ratio, "width": out["width"],
                "height": out["height"], "duration": out["duration"], "clip": out["clip"], "captions": out["captions"],
                "music": out["music"], "credit": credit, "caption_problems": out["caption_problems"]}
        return Path(out["path"]).read_bytes(), json.loads(json.dumps(meta))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
