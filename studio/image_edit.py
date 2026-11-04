"""Image editing on HuhQueue's side (docs/plans/AGENT_RUNS_EDITING_COMMERCE.md Phase 2): MCP tools + the REST routes.

    fetch_image(url, run_id)                       the server fetches the picture (netguard: every hop vetted, https only,
                                                   15 MB, image formats only, ~40 MP cap, EXIF/GPS stripped) and keeps it
                                                   as run media; a site that blocks servers answers blocked_by_site with
                                                   the exact words to ask the person to upload the file instead
    cutout_product(image, engine, run_id)          fast (Pillow, here) | auto (fast, then the ML cut-out when unsure) |
                                                   hq (BiRefNet-lite on the worker); ml = ISNet on the worker
    edit_image(image, ops, run_id)                 crop / fit / pad / cutout / card / round / grade / sharpen (image_ops)
    compose_product_card(image, ratio, name, price, run_id)   the product on the brand ground with a soft shadow

One request = one run (Phase 1): everything here takes run_id and stores what it makes as run media (media.meta.run_id,
kind "source" for fetched originals, "edit" for results). Nothing is added to the Library: the Library gets one item per
finished post, when the run is staged. Each edit is a new media version (meta.prev names the one it was made from), so
the previous version is still there to go back to.

Security (work/plans/SECURITY_RULES.md, plan 2.8): fetches only through House._fetch_bytes_why -> netguard; the worker
only ever gets a media token's /m/ URL, never a user URL; images are parsed with a format allow-list and a pixel cap;
writes need a write token and pass the kill switch and daily cap (Studio._guard_write); a run_id of another account is
"not found"; per-person rate limit on these tools.
"""
from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from typing import Any

from .actions import ActionError, Actor

MAX_FETCH = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
FORMATS = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "AVIF": "avif"}
RATE_PER_10MIN = 60
UPLOAD_ASK = ("This site doesn't let servers download its images. Ask the person: \"I can't download that picture from the "
              "site. Could you send me the image file? Drop it in with create_send_code (any device), or upload it with "
              "create_upload.\" Then pass the uploaded media_token to the edit tools.")
WHY = {
    "blocked_by_site": "The site refused the download (it blocks servers or needs a sign-in).",
    "not_found": "The link answered 404: there is no image there.",
    "too_large": "The image is over 15 MB (or has too many pixels).",
    "private": "That address is private or local; HuhQueue only fetches public https links.",
    "not_an_image": "The link didn't return a JPG, PNG, WebP or AVIF image.",
    "unreachable": "The site didn't answer.",
    "bad_url": "Give a public https:// link to the image.",
}
_hits: dict[str, list[float]] = {}
_hlock = threading.Lock()


class ImageFetchError(ActionError):
    """fetch_image failed for a reason the agent can act on (code in WHY)."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        msg = WHY.get(code, WHY["unreachable"]) + (f" ({detail})" if detail else "")
        super().__init__(msg, 403 if code in ("blocked_by_site", "private") else 422)

    def payload(self) -> dict:
        out = {"ok": False, "reason": self.code, "message": str(self), "spent_usd": 0}
        if self.code in ("blocked_by_site", "unreachable", "not_an_image"):
            out["ask"] = UPLOAD_ASK
            out["next"] = "create_send_code or create_upload, then pass media_token"
        return out


def _limit(uid: str) -> None:
    now = time.time()
    with _hlock:
        h = [t for t in _hits.get(uid, []) if t > now - 600]
        if len(h) >= RATE_PER_10MIN:
            _hits[uid] = h
            raise ActionError(f"Too many image edits: up to {RATE_PER_10MIN} every 10 minutes. Wait a few minutes.", 429)
        h.append(now)
        _hits[uid] = h


def own_run(h, a: Actor, run_id: Any) -> int | None:
    """run_id -> the person's own run id, or None when not given. Another account's run (or an archived one) is 404."""
    if run_id in (None, "", 0, "0"):
        return None
    try:
        rid = int(run_id)
    except (TypeError, ValueError):
        raise ActionError("run_id is the number start_post or create_package returned.")
    rows = h.store.q("SELECT id, status FROM agent_runs WHERE id=? AND user_id=?", (rid, a.uid))
    if not rows or rows[0]["status"] == "archived":
        raise ActionError("No such post run.", 404)
    return rid


# ------------------------------------------------------------------------------------------------ parsing
def open_image(data: bytes, work_side: int = 0) -> tuple[Any, str]:
    """(PIL image, ext) for bytes that really are an allowed image; ImageFetchError otherwise (format allow-list, pixel
    cap / decompression-bomb guard, truncated files). work_side > 0 (the edit tools' working copy on the 512 MB web
    dyno): JPEG decodes straight at a reduced scale (draft, never the full 40 MP raster) and any other format is reduced
    to work_side right after decoding, so one edit holds at most one full decode and then a bounded copy."""
    from PIL import Image
    if not data:
        raise ImageFetchError("not_an_image")
    if len(data) > MAX_FETCH:
        raise ImageFetchError("too_large")
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS  # Pillow refuses (DecompressionBombError) at 2x this, warns above it
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            w, h = probe.size
            probe.verify()
        if fmt not in FORMATS:
            raise ImageFetchError("not_an_image", fmt or "unknown format")
        if w * h > MAX_PIXELS:
            raise ImageFetchError("too_large", f"{w}x{h} pixels")
        im = Image.open(io.BytesIO(data))
        if work_side and fmt in ("JPEG", "MPO") and max(w, h) > work_side:
            im.draft("RGB", (work_side, work_side))  # DCT scaling at decode: 1/2, 1/4 or 1/8, never below work_side
        im.load()
        if work_side and max(im.size) > work_side:
            from PIL import ImageOps as _IO
            im = _IO.exif_transpose(im)  # orientation before the copy that drops the EXIF block
            im.thumbnail((work_side, work_side), Image.LANCZOS)
    except ImageFetchError:
        raise
    except Image.DecompressionBombError:
        raise ImageFetchError("too_large", "too many pixels")
    except Exception:  # noqa: BLE001  not an image, truncated, a web page
        raise ImageFetchError("not_an_image")
    return im, FORMATS[fmt]


def strip_meta(data: bytes, ext: str, im) -> tuple[bytes, str]:
    """EXIF / GPS / XMP out before the file is stored and served at /m/: lossless for JPEG and PNG (studio/metastrip),
    a clean re-encode to PNG for WebP / AVIF (no metadata is carried over)."""
    from PIL import ImageOps
    if ext == "png" and (im.getexif().get(0x0112) or 1) != 1:
        ext = "png-rotate"  # a PNG eXIf Orientation: rotate the pixels (re-encode below); stripping alone would leave it sideways
    if ext in ("jpg", "png"):
        from . import metastrip
        return metastrip.strip_bytes(ext, data), ext
    from PIL import Image
    from . import metastrip
    up = ImageOps.exif_transpose(im)
    up = up.convert("RGBA" if up.mode in ("RGBA", "LA", "P") else "RGB")
    clean = Image.frombytes(up.mode, up.size, up.tobytes())  # pixels only: no info / EXIF / XMP / ICC carried over
    buf = io.BytesIO()
    clean.save(buf, "PNG", optimize=True)
    return metastrip.strip_bytes("png", buf.getvalue()), "png"  # belt and braces: no eXIf / text chunk survives


# ------------------------------------------------------------------------------------------------ storage
def _audit(h, a: Actor, action: str, detail: dict) -> None:
    """One activity row per successful image call; the action names are in Studio.COUNTED, so an agent token's
    daily cap counts them (REST and MCP alike)."""
    h.store.audit(a.uid, a.label, action, "write", detail)


def _store(h, a: Actor, im, name: str, kind: str, rid: int | None, meta: dict | None = None) -> dict:
    """An edited image as run media (never a Library item): PNG when it has transparency, else JPEG."""
    has_alpha = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 255
    buf = io.BytesIO()
    if has_alpha:
        im.save(buf, "PNG", optimize=True)
        name = name.rsplit(".", 1)[0] + ".png"
    else:
        im.convert("RGB").save(buf, "JPEG", quality=92, optimize=True)
        name = name.rsplit(".", 1)[0] + ".jpg"
    m = {"type": "photo", "width": im.width, "height": im.height, "run_id": rid, **(meta or {})}
    tok = h.put_media(a, buf.getvalue(), name, kind=kind, meta=m)
    return {"media_token": tok, "url": h.media_url(tok), "width": im.width, "height": im.height, "transparent": has_alpha}


def _preview(im, side: int = 320) -> str:
    """A small inline preview (data URL, checkerboard under transparency) so the agent can see the result."""
    from PIL import Image, ImageDraw, ImageOps
    p = ImageOps.contain(im.convert("RGBA"), (side, side))
    bg = Image.new("RGBA", p.size, (255, 255, 255, 255))
    d = ImageDraw.Draw(bg)
    for y in range(0, p.height, 16):
        for x in range((y // 16 % 2) * 16, p.width, 32):
            d.rectangle((x, y, x + 15, y + 15), fill=(222, 222, 222, 255))
    bg.alpha_composite(p)
    buf = io.BytesIO()
    bg.convert("RGB").save(buf, "JPEG", quality=70)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


# ------------------------------------------------------------------------------------------------ fetch
def fetch_image(studio, a: Actor, args: dict) -> dict:
    h = studio.house
    studio._guard_write(a, "fetch_image")
    rid = own_run(h, a, args.get("run_id"))
    _limit(a.uid)
    url = str(args.get("url") or args.get("image_url") or "").strip()
    tok, meta = _own_token(h, a, url)
    if tok:  # one of the person's own /m/ files: nothing to fetch
        m = h.get_media(tok)
        im, _ = open_image(m["data"])
        return {"ok": True, "media_token": tok, "url": h.media_url(tok), "width": im.width, "height": im.height,
                "already_yours": True, "run_id": rid, "spent_usd": 0}
    if not url.startswith("https://"):
        raise ImageFetchError("bad_url")
    data, why = h._fetch_bytes_why(url, MAX_FETCH, https_only=True)  # every redirect hop stays https
    if data is None:
        code = {"forbidden": "blocked_by_site", "not_found": "not_found", "too_large": "too_large", "private": "private",
                "bad_url": "bad_url"}.get(why)
        if code is None and why.startswith("http_"):
            code = "blocked_by_site" if why in ("http_429", "http_451", "http_406") else "unreachable"
        raise ImageFetchError(code or "unreachable", why if why.startswith("http_") else "")
    im, ext = open_image(data)
    clean, ext = strip_meta(data, ext, im)
    from PIL import ImageOps
    im = ImageOps.exif_transpose(im)  # the size and preview of what is stored (a rotated PNG is stored upright)
    from urllib.parse import urlparse
    tok = h.put_media(a, clean, f"source.{ext}", kind="source",
                      meta={"type": "photo", "width": im.width, "height": im.height, "run_id": rid,
                            "source_host": (urlparse(url).hostname or "")[:120]})
    h.store.audit(a.uid, a.label, "fetch_image", "read", {"host": urlparse(url).hostname, "bytes": len(clean), "run_id": rid})
    return {"ok": True, "media_token": tok, "url": h.media_url(tok), "width": im.width, "height": im.height,
            "bytes": len(clean), "run_id": rid, "preview": _preview(im), "spent_usd": 0,
            "next": "cutout_product / edit_image / compose_product_card with image=media_token and the same run_id"}


def _own_token(h, a: Actor, ref: str) -> tuple[str, dict | None]:
    """A media token (or this server's /m/<token> URL) the person owns -> (token, meta); ("", None) otherwise."""
    ref = (ref or "").strip()
    base = h.s.public_url.rstrip("/") + "/m/"
    if ref.startswith(base):
        ref = ref[len(base):].split("?", 1)[0]
    elif ref.startswith(("http://", "https://")) or not ref:
        return "", None
    meta = h.media_meta(a, ref)
    return (ref, meta) if meta is not None else ("", None)


def load_source(studio, a: Actor, args: dict, rid: int | None):
    """(PIL image, media token, meta) for image / media_token / image_url. A URL is fetched first (fetch_image)."""
    h = studio.house
    ref = str(args.get("media_token") or args.get("image") or args.get("image_url") or "").strip()
    if not ref:
        raise ActionError("Give the picture as image (a media_token, or a public https URL).")
    tok, meta = _own_token(h, a, ref)
    if not tok:
        if not ref.startswith(("http://", "https://")):
            raise ActionError("No such media for this account.", 404)
        got = fetch_image(studio, a, {"url": ref, "run_id": rid})
        tok, meta = got["media_token"], h.media_meta(a, got["media_token"]) or {}
    m = h.get_media(tok)
    if not m:
        raise ActionError("No such media for this account.", 404)
    from PIL import Image, ImageOps
    from . import image_ops
    im, _ = open_image(m["data"], work_side=image_ops.MAX_SIDE)  # guarded: non-images / bombs answer ImageFetchError
    with Image.open(io.BytesIO(m["data"])) as probe:  # header only (already validated): the size crop pixels refer to
        sw, sh = probe.size
        src_size = (sh, sw) if (probe.getexif().get(0x0112) or 1) in (5, 6, 7, 8) else (sw, sh)
    im = ImageOps.exif_transpose(im)
    return im, tok, {**(meta or {}), "src_size": src_size}


def _brand(h, a: Actor) -> tuple[int, int, int]:
    from . import image_ops
    kit = h.kit(a) or {}
    c = (kit.get("colors") or {}).get("primary") or "#000000"
    try:
        return image_ops._colour(c, (0, 0, 0), "brand")
    except image_ops.OpError:
        return (0, 0, 0)


# ------------------------------------------------------------------------------------------------ cut-out
def queue_matte(studio, a: Actor, src: str, model: str, rid: int | None) -> dict:
    """The ML cut-out as a worker job (studio/image_matte.py): InstaBox when that backend is on (GitHub Actions as its
    fallback), else GitHub Actions render.yml with kind=image_matte. Never on the web dyno."""
    from . import demo_guard, image_matte
    h = studio.house
    model = image_matte.check_model(model)
    if demo_guard.is_demo(a.uid):
        raise ActionError("The sample workspace doesn't run the ML cut-out. Connect Buffer to use it.", 403)
    spec = {"kind": "image_matte", "source": src, "model": model}
    from . import instabox_worker
    # the cut-out never runs on the web dyno: it needs a remote worker (InstaBox or GitHub Actions), whatever video_mode
    # says about where video renders (a server with local FFmpeg still sends cut-outs to the worker)
    if not (instabox_worker.enabled() or os.environ.get("GH_DISPATCH_TOKEN") or getattr(h, "worker_mode", "") == "github"):
        raise ActionError("The ML cut-out runs on the free render worker, which isn't set up on this server "
                          "(use engine=fast, or the card op to place the whole photo).", 503)
    jid = h.store.x("INSERT INTO render_jobs(user_id,run_id,spec,status,created,updated) VALUES(?,?,?,?,?,?)",
                    (a.uid, rid, json.dumps(spec), "queued", time.time(), time.time()))
    ok, why = h._dispatch_github(jid, inputs={"kind": "image_matte"})
    if not ok:
        h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", (why, time.time(), jid))
        raise ActionError(f"The cut-out worker didn't start ({why}).", 502)
    info = image_matte.MODELS[model]
    return {"job_id": jid, "status": "queued", "engine": "hq" if model == image_matte.HQ_MODEL else "ml", "model": model,
            "model_licence": info["licence"], "rendered_by": h.backend_label(why).replace("video worker", "cut-out worker"),
            "run_id": rid, "poll": f"render_status(job_id={jid}) every 10-20 s; when done, download_url is the PNG cut-out",
            "spent_usd": 0}


def finish_matte(h, job: dict, data: bytes | None, meta: dict | None, error: str) -> dict:
    """The worker's PNG for an image_matte job: checked (PNG signature, decodes, pixel cap) and stored as run media."""
    from .actions import ActionError as _AE
    jid = job["id"]
    if error or not data:
        h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", ((error or "no output")[:500], time.time(), jid))
        return {"ok": False}
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", ("the worker didn't return a PNG", time.time(), jid))
        raise _AE("image_matte: not a PNG file.", 415)
    try:
        im, _ = open_image(data)
    except ImageFetchError:
        h.store.x("UPDATE render_jobs SET status='failed', error=?, updated=? WHERE id=?", ("unreadable cut-out", time.time(), jid))
        raise _AE("image_matte: unreadable PNG.", 415)
    spec = json.loads(job["spec"])
    m = {k: v for k, v in (meta or {}).items() if k in ("engine", "model", "model_licence", "coverage") and isinstance(v, (int, float, str))}
    tok = h.put_media(Actor(job["user_id"]), data, "cutout.png", kind="edit",
                      meta={"type": "photo", "width": im.width, "height": im.height, "run_id": job.get("run_id"),
                            "prev": spec.get("source"), "op": "cutout", **m})
    h.store.x("UPDATE render_jobs SET status='done', media_token=?, updated=? WHERE id=?", (tok, time.time(), jid))
    return {"ok": True, "token": tok}


def cutout_product(studio, a: Actor, args: dict) -> dict:
    from . import hero_edit, image_matte
    h = studio.house
    studio._guard_write(a, "cutout_product")
    rid = own_run(h, a, args.get("run_id"))
    _limit(a.uid)
    engine = str(args.get("engine") or "auto").lower()
    if engine not in ("auto", "fast", "hq", "ml"):
        raise ActionError("engine is auto, fast, ml or hq.")
    im, src, _ = load_source(studio, a, args, rid)
    if engine in ("hq", "ml"):
        q = queue_matte(studio, a, src, image_matte.model_for(engine), rid)
        _audit(h, a, "cutout_product", {"engine": engine, "job_id": q["job_id"], "run_id": rid})
        return q
    cut = hero_edit.cutout_detailed(im)
    if cut["ok"]:
        _audit(h, a, "cutout_product", {"engine": "fast", "run_id": rid})
        out = _store(h, a, cut["image"], "cutout.png", "edit", rid, {"prev": src, "op": "cutout", "engine": cut["engine"],
                                                                      "confidence": cut["confidence"]})
        return {**out, "ok": True, "engine": cut["engine"], "confidence": cut["confidence"], "pieces": cut["components"],
                "report": cut["reason"], "run_id": rid, "preview": _preview(cut["image"]), "spent_usd": 0}
    if engine == "auto":
        try:
            q = queue_matte(studio, a, src, image_matte.DEFAULT_MODEL, rid)
            _audit(h, a, "cutout_product", {"engine": "auto", "job_id": q["job_id"], "run_id": rid})
            return {**q, "ok": True, "report": f"The fast cut-out wasn't confident ({cut['reason']}); the ML cut-out "
                    f"({image_matte.DEFAULT_MODEL}) is running on the worker."}
        except ActionError as e:
            why = str(e)
    else:
        why = ""
    return {"ok": False, "engine": "fast", "confidence": cut["confidence"], "reason": cut["reason"], "run_id": rid,
            "message": "No cut-out: the fast cut-out isn't confident and a torn cut-out is never returned. " +
                       (f"{why} " if why else "Use engine=hq for the ML cut-out on the worker, ") +
                       "or compose_product_card to place the whole photo on a card.", "spent_usd": 0}


# ------------------------------------------------------------------------------------------------ edits
def edit_image(studio, a: Actor, args: dict) -> dict:
    from . import image_ops
    h = studio.house
    studio._guard_write(a, "edit_image")
    rid = own_run(h, a, args.get("run_id"))
    _limit(a.uid)
    try:
        ops = image_ops.validate(args.get("ops"))
    except image_ops.OpError as e:
        raise ActionError(str(e))
    im, src, smeta = load_source(studio, a, args, rid)
    try:
        out, report = image_ops.apply(im, ops, _brand(h, a), source_size=smeta.get("src_size"))
    except image_ops.OpError as e:
        raise ActionError(str(e))
    res = _store(h, a, out, "edit.png", "edit", rid, {"prev": src, "op": "edit", "ops": [o["op"] for o in ops]})
    _audit(h, a, "edit_image", {"ops": [o["op"] for o in ops], "run_id": rid})
    return {**res, "ok": True, "report": report, "run_id": rid, "prev": src, "preview": _preview(out), "spent_usd": 0}


def compose_product_card(studio, a: Actor, args: dict) -> dict:
    op = {"op": "card", "ratio": args.get("ratio") or "4:5"}
    for k in ("ground", "scale", "shadow", "name", "price"):
        if args.get(k) is not None:
            op[k] = args[k]
    out = edit_image(studio, a, {**{k: args.get(k) for k in ("image", "image_url", "media_token", "run_id")}, "ops": [op]})
    return out


# ------------------------------------------------------------------------------------------------ MCP tools
S = {"type": "string"}
RUN = {"type": "integer", "description": "The post this edit belongs to (start_post / create_package returned it). The result is "
       "run media of that post: no Library item is added per edit."}
IMG = {"type": "string", "description": "The picture: a media_token (fetch_image, create_upload, list_library) or a public https "
       "URL (fetched by the server for you)."}
TOOLS: list[dict[str, Any]] = [
    {"name": "fetch_image", "description": "Fetch a picture from a public https URL on the HuhQueue server (no download or "
     "paste on your side): every redirect checked, up to 15 MB, JPG/PNG/WebP/AVIF only, location/camera metadata removed. "
     "Returns media_token for the edit tools. ok false with reason blocked_by_site means the site blocks servers: ask the "
     "person to upload the file (the answer's ask has the words), then use that media_token.",
     "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": S, "run_id": RUN}}},
    {"name": "cutout_product", "description": "Cut a product out of its background (no HuhQueue AI). engine: fast (instant, "
     "on the server, for a product on a plain background), auto (default: fast, then the ML cut-out on the worker when the "
     "fast one isn't confident), ml (ISNet isnet-general-use, Apache-2.0, on the worker), hq (BiRefNet-lite, MIT, on the "
     "worker; best edges). fast answers the PNG cut-out at once with its confidence; ml/hq (and auto when unsure) answer "
     "job_id: poll render_status(job_id). A torn cut-out is never returned.",
     "inputSchema": {"type": "object", "properties": {"image": IMG, "engine": {"type": "string", "enum": ["auto", "fast", "ml", "hq"]},
                                                      "run_id": RUN}}},
    {"name": "edit_image", "description": "Edit a picture with an ordered list of ops (no HuhQueue AI, max 12): "
     "crop {box: [x0,y0,x1,y1] px or 0-1}, fit {ratio, mode: cover|contain, focus: auto|[x,y]}, pad {ratio, colour: #hex|brand}, "
     "cutout {} (fast cut-out), card {ratio, ground: #hex|brand, scale 0.3-0.95, shadow 0-1, radius, name, price} (place on "
     "a card), round {radius}, grade {preset: neutral|warm|cool|brand, exposure/contrast/saturation -1..1}, sharpen {amount 0-3}. "
     "Returns the new version's url and media_token (the previous version stays; prev names it).",
     "inputSchema": {"type": "object", "required": ["ops"], "properties": {
         "image": IMG, "run_id": RUN,
         "ops": {"type": "array", "minItems": 1, "maxItems": 12, "items": {"type": "object", "required": ["op"], "properties": {
             "op": {"type": "string", "enum": ["crop", "fit", "pad", "cutout", "card", "round", "grade", "sharpen"]}}}}}}},
    {"name": "compose_product_card", "description": "A product on the brand ground (no HuhQueue AI): cut out when it stands "
     "on a plain background, otherwise the whole photo as a rounded card, never zoomed in; soft shadow and a quiet name / "
     "price line. For product slides.",
     "inputSchema": {"type": "object", "properties": {"image": IMG, "run_id": RUN, "name": S, "price": S,
                     "ratio": {"type": "string", "enum": ["4:5", "1:1", "9:16", "16:9"]}, "ground": S}}},
]
NAMES = {t["name"] for t in TOOLS}
READ_ONLY: set[str] = set()
FNS = {"fetch_image": fetch_image, "cutout_product": cutout_product, "edit_image": edit_image,
       "compose_product_card": compose_product_card}


def call_tool(studio, actor: Actor, name: str, args: dict):
    if name not in NAMES:
        return NotImplemented
    try:
        out = FNS[name](studio, actor, args or {})
    except ImageFetchError as e:
        out = e.payload()
    from . import mcp_client_brain
    mcp_client_brain.record(studio, actor, name)
    return out
