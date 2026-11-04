"""image_matte: the ML product cut-out, run on the render worker (job kind ``image_matte``), never on the web dyno.

docs/plans/AGENT_RUNS_EDITING_COMMERCE.md 2.3. The fast cut-out (studio/hero_edit.py, no model) runs on the web
instance; when it isn't confident, or the person asks for high quality, the app queues this job on the free worker
(the InstaBox box when HQ_RENDER_BACKEND=instabox, GitHub Actions render.yml otherwise or as its fallback).

Runner: rembg (MIT, danielgatis/rembg), pinned in requirements-matte.txt. Models (weights carry their own licence,
recorded in NOTICE):

    isnet-general-use       ISNet / DIS (xuebinqin/DIS), Apache-2.0   default (engine=ml / auto)
    birefnet-general-lite   BiRefNet lite (ZhengPeng7/BiRefNet), MIT  high quality (engine=hq)

rembg's own default model is ``bria-rmbg`` (BRIA RMBG-2.0), whose licence needs a paid agreement for commercial use,
so the model name is ALWAYS passed and anything BRIA is refused here. The weights are downloaded once by rembg (pooch,
checked against the md5 rembg pins) into U2NET_HOME: ~/.u2net on Actions (actions/cache), /data/u2net on InstaBox.

The worker gets a media token's /m/ URL from the app (never a user URL: fetch_image already vetted that on the web side),
reads it through netguard, and posts back one PNG.
"""
from __future__ import annotations

import io

from PIL import Image, ImageOps

MODELS = {
    "isnet-general-use": {"licence": "Apache-2.0", "source": "https://github.com/xuebinqin/DIS", "name": "ISNet (DIS) general use"},
    "birefnet-general-lite": {"licence": "MIT", "source": "https://github.com/ZhengPeng7/BiRefNet", "name": "BiRefNet lite"},
}
DEFAULT_MODEL = "isnet-general-use"
HQ_MODEL = "birefnet-general-lite"
MAX_SIDE = 2048            # longest side the model sees and the PNG keeps
MAX_PIXELS = 40_000_000    # decompression-bomb guard (~40 MP), same as fetch_image
MAX_BYTES = 15 * 1024 * 1024
_SESSIONS: dict = {}


class MatteError(RuntimeError):
    """A plain message for the person (render_worker.friendly passes RuntimeError messages through)."""


def model_for(engine: str | None) -> str:
    """engine -> the model name, always explicit: hq = BiRefNet-lite, anything else = ISNet."""
    return HQ_MODEL if str(engine or "").lower() == "hq" else DEFAULT_MODEL


def check_model(model: str) -> str:
    m = str(model or "").strip().lower()
    if "bria" in m or "rmbg" in m:
        raise MatteError("BRIA RMBG models are not used (non-commercial licence); use isnet-general-use or birefnet-general-lite.")
    if m not in MODELS:
        raise MatteError(f"Unknown cut-out model {model!r}; allowed: {', '.join(MODELS)}.")
    return m


def load(data: bytes) -> Image.Image:
    """Bytes -> an RGB image, with the same guards as fetch_image: size cap, format allow-list, pixel cap, EXIF turned."""
    if not data or len(data) > MAX_BYTES:
        raise MatteError("The image is empty or over 15 MB.")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
        if fmt not in ("JPEG", "PNG", "WEBP", "AVIF", "MPO"):
            raise MatteError(f"Not a supported image ({fmt or 'unknown format'}).")
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > MAX_PIXELS:
            raise MatteError("The image has too many pixels.")
        im = ImageOps.exif_transpose(im)
        im.load()
    except Image.DecompressionBombError as e:
        raise MatteError("The image has too many pixels.") from e
    except MatteError:
        raise
    except Exception as e:  # noqa: BLE001  truncated / not an image
        raise MatteError("Couldn't read that image.") from e
    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
    if max(im.size) > MAX_SIDE:
        im = ImageOps.contain(im, (MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return im


def _session(model: str):
    try:
        from rembg import new_session  # type: ignore  # requirements-matte.txt; the worker's matte venv only
    except ImportError as e:
        raise MatteError("The ML cut-out isn't installed on this worker (requirements-matte.txt).") from e
    if model not in _SESSIONS:
        _SESSIONS[model] = new_session(model)  # always the explicit name: rembg's default is BRIA RMBG
    return _SESSIONS[model]


def matte(data: bytes, model: str = DEFAULT_MODEL, pad: int = 8, remove_fn=None) -> tuple[bytes, dict]:
    """(PNG bytes of the cut-out, cropped to the product, meta). remove_fn: tests pass a fake for rembg.remove."""
    model = check_model(model)
    im = load(data)
    if remove_fn is None:
        from rembg import remove  # type: ignore
        sess = _session(model)

        def remove_fn(img):
            return remove(img, session=sess, post_process_mask=True)
    out = remove_fn(im.convert("RGB"))
    if not isinstance(out, Image.Image):
        out = Image.open(io.BytesIO(out))
    out = out.convert("RGBA")
    a = out.getchannel("A")
    box = a.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box:
        raise MatteError("The cut-out found no product in the image.")
    cover = sum(i * c for i, c in enumerate(a.histogram())) / (255.0 * out.width * out.height)
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(out.width, box[2] + pad), min(out.height, box[3] + pad))
    out = out.crop(box)
    buf = io.BytesIO()
    out.save(buf, "PNG", optimize=True)
    info = MODELS[model]
    return buf.getvalue(), {"type": "photo", "width": out.width, "height": out.height, "engine": "hq" if model == HQ_MODEL else "ml",
                            "model": model, "model_licence": info["licence"], "coverage": round(cover, 3)}


def worker_job(spec: dict, urls: dict, get) -> tuple[bytes, dict]:
    """render_worker: job kind image_matte. spec: {kind, source (media token), model}; urls["source"]: that token's /m/
    URL on the app; get: the worker's netguard-vetted fetch."""
    model = check_model(spec.get("model") or DEFAULT_MODEL)
    if not urls.get("source"):
        raise MatteError("The cut-out job has no source image.")
    data = get(urls["source"])
    png, meta = matte(data, model)
    print(f"image_matte: {model} -> {meta['width']}x{meta['height']} PNG, {len(png)} bytes", flush=True)
    return png, meta
