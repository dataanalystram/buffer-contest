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

def _captions_args(spec: dict) -> dict:
    """spec["captions"] -> render_style's captions mode. edit_video (via mcp_client_brain) sets captions_mode itself:
    "auto" (default: caption it unless it already has captions burned in), "replace" (crop the old ones off and caption
    again), "on" (caption it anyway), "off". Older specs carry only captions: true/false."""
    mode = spec.get("captions_mode")
    if mode in ("auto", "replace", "on", "off"):
        return {"captions_mode": mode, "detect_own_captions": mode == "auto"}
    return {}


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
    fonts = {"anton": ("Anton.ttf", "Poppins-Bold.ttf"), "poppins": ("Poppins-Bold.ttf", "Poppins-Bold.ttf"),
             "inter": ("Inter-Bold.ttf", "Inter-Bold.ttf"), "figtree": ("Figtree-ExtraBold.ttf", "Figtree-ExtraBold.ttf")}
    fk = str(kit.get("font") or "").lower()
    if fk in fonts:  # the brand kit's headline font on every title, hook, cover and end card
        b["head_font"], b["bold_font"] = fonts[fk]
    end = kit.get("end_card") or {}
    if end.get("line"):
        b["end_line"] = end["line"]
    if logo_path:
        b["logo"] = logo_path
    return b


COVER_RATIO = {"9:16": "9:16", "4:5": "4:5"}


def _covers(spec: dict, source_path: str, brand: dict, style: str, ratio: str, tmp: Path, get, urls: dict) -> tuple[list, str | None, int]:
    """Three branded cover variants (studio/media/covers.py) from the source video (and the product's hero image when the
    app sent one), as base64 JPEGs per size; the chosen one also becomes the Reel's opening frame (not for style none,
    which posts the video as it is). A failure only means no covers."""
    import base64
    try:
        from . import covers as cov
        hero = get(urls["cover_image"]) if urls.get("cover_image") else None
        names = spec.get("names") or []
        cv = cov.make_covers(source_path, image=hero, hook=spec.get("cover_hook") or spec.get("hook") or "", brand=brand,
                             name=names[0] if names else "", clip=tuple(spec["clip"]) if spec.get("clip") else None)
    except Exception as e:  # noqa: BLE001
        print(f"covers skipped: {e.__class__.__name__}: {str(e)[:120]}")
        return [], None, 0
    out = []
    for i, v in enumerate(cv["variants"]):
        out.append({"variant": i, "layout": v["layout"], "score": v["score"], "frame_t": v["frame_t"], "source": v["source"],
                    "files": {r: base64.b64encode(cov.jpeg(im)).decode() for r, im in v["images"].items()}})
    if not out:
        return [], None, 0
    idx = min(max(0, int(spec.get("cover_index") or 0)), len(out) - 1)
    png = None
    if style != "none" and spec.get("cover_frame", True) is not False and ratio in COVER_RATIO:
        png = str(tmp / "cover.png")
        cv["variants"][idx]["images"][COVER_RATIO[ratio]].save(png)
    return out, png, idx


def cover_swap(video: bytes, cover: bytes, secs: float) -> tuple[bytes, dict]:
    """Replace the Reel's opening cover frames (the first ``secs``) with another cover variant; the sound and every
    other frame stay as rendered."""
    from . import ff
    tmp = Path(tempfile.mkdtemp(prefix="hq_cswap_"))
    try:
        (tmp / "in.mp4").write_bytes(video)
        (tmp / "cover.jpg").write_bytes(cover)
        pr = ff.probe(str(tmp / "in.mp4"))
        W, H = pr["width"], pr["height"]
        secs = max(0.3, float(secs or 0.6))
        fc = (f"[0:v]scale={W}:{H},setsar=1,fps=30,format=yuv420p[c];[1:v]trim=start={secs:.3f},setpts=PTS-STARTPTS,fps=30,"
              f"format=yuv420p,setsar=1[m];[c][m]concat=n=2:v=1:a=0[v]")
        amap = ["-map", "1:a", "-c:a", "copy"] if pr.get("has_audio") else []
        ff.run(["-y", "-v", "error", "-loop", "1", "-framerate", "30", "-t", f"{secs:.3f}", "-i", str(tmp / "cover.jpg"), "-i",
                str(tmp / "in.mp4"), "-filter_complex", fc, "-map", "[v]", *amap, "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(tmp / "out.mp4")], timeout=900)
        got = ff.probe(str(tmp / "out.mp4"))
        return (tmp / "out.mp4").read_bytes(), {"type": "video", "width": got["width"], "height": got["height"],
                                                "duration": round(got["duration"], 2), "cover_frame_secs": secs,
                                                "thumbnail_offset_ms": int(secs * 500)}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def render_job(spec: dict, get: Callable[[str], bytes | None], urls: dict | None = None, logo: bytes | None = None,
               http=None) -> tuple[bytes, dict]:
    """Run one media_render job. ``get(url)`` fetches an app media URL (the worker passes its netguard getter);
    ``urls`` is the job's URL map from the app (source / slides). Returns (mp4 bytes, meta)."""
    from ..headline import spec_hook
    spec = spec_hook(dict(spec))  # render time: a URL or the person's request is never burned in as the hook
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

        covers_out, cover_png, cover_idx = [], None, 0
        if source_path and spec.get("covers") is not False:
            covers_out, cover_png, cover_idx = _covers(spec, source_path, brand, style, ratio, tmp, get, urls)
        words_path = None
        if source_path and spec.get("captions") is not False and (style in CAPTION_STYLES or style in styles.THEMES):
            try:  # captions and the opus/hype clip picker need the transcript; a failure only turns captions off
                tr = tools.media_transcribe(source_path, names=spec.get("names"), run_dir=str(tmp))
                words_path = tr["words_path"] if tr.get("words") else None
            except Exception as e:  # noqa: BLE001
                print(f"transcribe skipped: {e.__class__.__name__}: {str(e)[:120]}")
        r = tools.media_render(style, spec.get("hook") or "", source_path=source_path, images=images, ratios=[ratio],
                               facts=spec.get("facts"), credit=credit, words_path=words_path, names=spec.get("names"),
                               clip=spec.get("clip"), music=spec.get("music") or "auto", brand=brand, official=official,
                               rights_confirmed=bool(spec.get("rights_confirmed")), run_dir=str(tmp),
                               brand_colors=bool(spec.get("brand_colors")), brand_pass=spec.get("brand_pass") or False,
                               cover_png=cover_png, logo=spec.get("logo", True) is not False, **_captions_args(spec))
        if r.get("needs_confirmation"):
            raise JobError("Rights not confirmed: " + r["question"])
        if r.get("error"):
            raise JobError(r["error"])
        out = r["renders"][0]
        meta = {"type": "video", "renderer": "studio.media", "style": style, "ratio": ratio, "width": out["width"],
                "height": out["height"], "duration": out["duration"], "clip": out["clip"], "captions": out["captions"],
                "music": out["music"], "credit": credit, "caption_problems": out["caption_problems"],
                "captions_note": out.get("captions_note") or "", "source_captions": out.get("source_captions"),
                "brand_pass": out.get("brand_pass") or [], "thumbnail_offset_ms": out.get("thumbnail_offset_ms"),
                "cover_frame_secs": out.get("cover_frame_secs") or 0, "fit": out.get("fit"), "logo": out.get("logo")}
        meta = json.loads(json.dumps(meta))
        if covers_out:
            meta["covers_b64"], meta["cover_index"] = covers_out, cover_idx  # finish_video stores them as media, then drops these
        return Path(out["path"]).read_bytes(), meta
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
