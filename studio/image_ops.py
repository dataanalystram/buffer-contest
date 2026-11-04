"""image_ops: the ordered, validated edit list behind ``edit_image`` (MCP + REST), pure Pillow, web-safe.

docs/plans/AGENT_RUNS_EDITING_COMMERCE.md 2.5. Every op is checked before any pixel moves (unknown op, too many ops,
out-of-range values answer a plain error), then applied in order to one image. No model, no network: ``cutout`` here is
the fast cut-out (studio/hero_edit.py); the ML cut-out is a worker job (studio/image_matte.py, cutout_product engine=hq).

    crop    {box: [x0, y0, x1, y1]}            pixels, or fractions 0-1 of the image
    fit     {ratio: "4:5", mode: cover|contain, focus: auto|[x, y]}   cover crops (saliency for auto), contain letterboxes
    pad     {ratio: "1:1", colour: "#rrggbb"|"brand"}                 grow the canvas to the ratio, image centred
    cutout  {}                                 fast cut-out of a product on a plain background (RGBA); refuses when unsure
    card    {ratio, ground: "#rrggbb"|"brand", scale 0.3-0.95, shadow 0-1, radius 0-120, name, price}
                                               the product (cut out first when it has a plain stage, else the whole photo
                                               as a rounded card) centred on the brand ground with a soft shadow
    round   {radius: 0-400}
    grade   {preset: neutral|warm|cool|brand, exposure -1..1, contrast -1..1, saturation -1..1}
    sharpen {amount: 0-3}
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

MAX_OPS = 12
MAX_SIDE = 4096
RATIOS = {"1:1": (1080, 1080), "4:5": (1080, 1350), "9:16": (1080, 1920), "16:9": (1920, 1080), "3:4": (1080, 1440),
          "2:3": (1080, 1620), "1.91:1": (1200, 628)}
OPS = ("crop", "fit", "pad", "cutout", "card", "round", "grade", "sharpen")
_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")
FONT = Path(__file__).parent / "fonts" / "Figtree-ExtraBold.ttf"


class OpError(ValueError):
    """A plain message for the agent / person: what was wrong with the op list."""


def _num(op: dict, k: str, lo: float, hi: float, default: float | None = None) -> float | None:
    v = op.get(k, default)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise OpError(f"{op['op']}.{k} must be a number between {lo:g} and {hi:g}.")
    if not lo <= float(v) <= hi:
        raise OpError(f"{op['op']}.{k} is {v}; it must be between {lo:g} and {hi:g}.")
    return float(v)


def _ratio(op: dict, required: bool = True) -> str | None:
    r = op.get("ratio")
    if r is None and not required:
        return None
    if r not in RATIOS:
        raise OpError(f"{op['op']}.ratio must be one of {', '.join(RATIOS)}.")
    return r


def _colour(v, brand: tuple[int, int, int], what: str) -> tuple[int, int, int]:
    if v in (None, "", "brand"):
        return brand
    if isinstance(v, str) and _HEX.match(v):
        v = v.lstrip("#")
        return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)
    raise OpError(f"{what} must be a colour like #101010, or \"brand\".")


def validate(ops) -> list[dict]:
    """The op list, checked and normalised (each item {op: name, ...}). Raises OpError with what to fix."""
    if not isinstance(ops, list) or not ops:
        raise OpError("ops is a list of edits, e.g. [{\"op\": \"fit\", \"ratio\": \"4:5\"}].")
    if len(ops) > MAX_OPS:
        raise OpError(f"Up to {MAX_OPS} ops per call ({len(ops)} given).")
    out = []
    for i, raw in enumerate(ops):
        if not isinstance(raw, dict) or not isinstance(raw.get("op"), str):
            raise OpError(f"ops[{i}] needs an \"op\" name ({', '.join(OPS)}).")
        op = dict(raw)
        name = op["op"]
        if name not in OPS:
            raise OpError(f"ops[{i}]: unknown op {name!r}; use one of {', '.join(OPS)}.")
        if name == "crop":
            box = op.get("box")
            if not (isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                             for v in box)):
                raise OpError("crop.box is [x0, y0, x1, y1] in pixels or as fractions 0-1.")
            if box[2] <= box[0] or box[3] <= box[1] or min(box) < 0:
                raise OpError("crop.box must have x1 > x0, y1 > y0 and no negative values.")
        elif name == "fit":
            _ratio(op)
            if op.get("mode", "cover") not in ("cover", "contain"):
                raise OpError("fit.mode is cover or contain.")
            f = op.get("focus", "auto")
            if f != "auto" and not (isinstance(f, (list, tuple)) and len(f) == 2 and all(isinstance(v, (int, float)) and 0 <= v <= 1
                                                                                          for v in f)):
                raise OpError("fit.focus is \"auto\" or [x, y] as fractions 0-1.")
        elif name == "pad":
            _ratio(op)
        elif name == "card":
            _ratio(op, required=False)
            _num(op, "scale", 0.3, 0.95)
            _num(op, "shadow", 0, 1)
            _num(op, "radius", 0, 120)
            for k in ("name", "price"):
                if op.get(k) is not None and (not isinstance(op[k], str) or len(op[k]) > 80):
                    raise OpError(f"card.{k} is text up to 80 characters.")
        elif name == "round":
            op["radius"] = _num(op, "radius", 0, 400, 40) if op.get("radius") is not None else 40.0
        elif name == "grade":
            if op.get("preset", "neutral") not in ("neutral", "warm", "cool", "brand"):
                raise OpError("grade.preset is neutral, warm, cool or brand.")
            for k in ("exposure", "contrast", "saturation"):
                _num(op, k, -1, 1)
        elif name == "sharpen":
            op["amount"] = _num(op, "amount", 0, 3, 1) if op.get("amount") is not None else 1.0
        out.append(op)
    return out


def _font(size: int):
    try:
        return ImageFont.truetype(str(FONT), size)
    except OSError:
        return ImageFont.load_default()


def _fit(im: Image.Image, ratio: str, mode: str, focus) -> Image.Image:
    from . import hero_edit
    W, H = RATIOS[ratio]
    if mode == "contain":
        # the whole picture, never cropped: the long side fills the frame, the rest is transparent (pad colours it)
        im = ImageOps.contain(im.convert("RGBA"), (W, H), Image.LANCZOS)
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out.alpha_composite(im, ((W - im.width) // 2, (H - im.height) // 2))
        return out
    if focus == "auto":
        x0, y0, x1, y1 = hero_edit.best_crop(im.convert("RGB"), W, H)
        return im.crop((x0, y0, x1, y1)).resize((W, H), Image.LANCZOS)
    return ImageOps.fit(im, (W, H), Image.LANCZOS, centering=(float(focus[0]), float(focus[1])))


def _pad(im: Image.Image, ratio: str, colour: tuple[int, int, int]) -> Image.Image:
    rw, rh = RATIOS[ratio]
    W, H = im.size
    if W / H > rw / rh:
        cw, ch = W, round(W * rh / rw)
    else:
        cw, ch = round(H * rw / rh), H
    if max(cw, ch) > MAX_SIDE:  # alternating pads (16:9 then 9:16 ...) would grow the canvas without end: cap it
        k = MAX_SIDE / max(cw, ch)
        im = im.resize((max(1, round(W * k)), max(1, round(H * k))), Image.LANCZOS)
        W, H = im.size
        cw, ch = min(MAX_SIDE, max(W, round(cw * k))), min(MAX_SIDE, max(H, round(ch * k)))
    out = Image.new("RGBA", (cw, ch), colour + (255,))
    out.alpha_composite(im.convert("RGBA"), ((cw - W) // 2, (ch - H) // 2))
    return out


def card(im: Image.Image, ratio: str = "4:5", ground=(0, 0, 0), scale: float = 0.72, shadow: float = 0.55, radius: int = 36,
         name: str = "", price: str = "", text_colour=None) -> tuple[Image.Image, dict]:
    """The product placed on a card (compose_product_card): cut out when it stands on a plain background (fast cut-out),
    otherwise the whole photo with rounded corners, fitted inside scale x the frame (never zoomed into), a soft shadow,
    and a quiet name / price line. (image, report)."""
    from . import hero_edit
    W, H = RATIOS[ratio]
    rep: dict = {}
    has_alpha = im.mode == "RGBA" and im.getchannel("A").getextrema()[0] < 16
    if has_alpha:
        obj, rep["placed"] = im, "cut-out"
    else:
        cut = hero_edit.cutout_detailed(im)
        rep["cutout_confidence"] = cut["confidence"]
        if cut["ok"]:
            obj, rep["placed"] = cut["image"], "cut-out"
        else:
            obj = ImageOps.contain(im.convert("RGB"), (2000, 2000), Image.LANCZOS)
            obj = hero_edit.rounded(obj, int(radius)) if int(radius) > 0 else obj.convert("RGBA")  # radius 0: square corners
            rep["placed"], rep["why"] = "whole photo", cut["reason"]
    text_h = int(H * 0.12) if (name or price) else 0
    box_w, box_h = int(W * scale), int((H - text_h) * scale)
    k = min(box_w / obj.width, box_h / obj.height)
    obj = obj.convert("RGBA").resize((max(1, int(obj.width * k)), max(1, int(obj.height * k))), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), tuple(ground) + (255,))
    x, y = (W - obj.width) // 2, (H - text_h - obj.height) // 2
    if shadow > 0:
        pad = 80
        a = Image.new("L", (obj.width + 2 * pad, obj.height + 2 * pad), 0)
        a.paste(obj.getchannel("A").point(lambda v: int(v * shadow)), (pad, pad))
        sh = Image.new("RGBA", a.size, (0, 0, 0, 255))
        sh.putalpha(a.filter(ImageFilter.GaussianBlur(28)))
        out.alpha_composite(sh, (x - pad + 10, y - pad + 24))
    out.alpha_composite(obj, (x, y))
    if text_h:
        lum = 0.2126 * ground[0] + 0.7152 * ground[1] + 0.0722 * ground[2]
        fg = text_colour or ((245, 245, 245) if lum < 140 else (20, 20, 22))
        d = ImageDraw.Draw(out)
        line = "  ·  ".join(t for t in (name.strip(), price.strip()) if t)
        size = int(H * 0.034)
        f = _font(size)
        while d.textlength(line, font=f) > W * 0.86 and size > 14:
            size -= 2
            f = _font(size)
        tw = d.textlength(line, font=f)
        d.text(((W - tw) / 2, H - text_h + (text_h - size) / 2 - size * 0.2), line, font=f, fill=tuple(fg))
    rep["box"] = (x, y, x + obj.width, y + obj.height)
    return out, rep


def _grade(im: Image.Image, op: dict, brand: tuple[int, int, int]) -> Image.Image:
    a = im.getchannel("A") if im.mode == "RGBA" else None
    rgb = im.convert("RGB")
    preset = op.get("preset", "neutral")
    if preset in ("warm", "cool"):
        r, g, b = rgb.split()
        k = 1.06 if preset == "warm" else 0.94
        r = r.point(lambda v: min(255, int(v * k)))
        b = b.point(lambda v: min(255, int(v * (2 - k))))
        rgb = Image.merge("RGB", (r, g, b))
    elif preset == "brand":
        rgb = Image.blend(ImageEnhance.Color(rgb).enhance(0.9), Image.new("RGB", rgb.size, brand), 0.12)
    if op.get("exposure"):
        rgb = ImageEnhance.Brightness(rgb).enhance(1 + float(op["exposure"]) * 0.5)
    if op.get("contrast"):
        rgb = ImageEnhance.Contrast(rgb).enhance(1 + float(op["contrast"]) * 0.5)
    if op.get("saturation"):
        rgb = ImageEnhance.Color(rgb).enhance(1 + float(op["saturation"]))
    if a is not None:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(a)
    return rgb


def apply(im: Image.Image, ops: list[dict], brand=(0, 0, 0), source_size: tuple[int, int] | None = None
          ) -> tuple[Image.Image, list[str]]:
    """Run the (validated) ops in order -> (image, report lines). Raises OpError for an op that can't apply here.
    source_size: the original picture's (width, height) when ``im`` is a reduced working copy (or larger than MAX_SIDE
    and shrunk here); pixel crop boxes given before any size-changing op refer to the original and are scaled."""
    from . import hero_edit
    ops = validate(ops)
    brand = tuple(brand)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    src_w = (source_size or im.size)[0] or im.width
    if max(im.size) > MAX_SIDE:
        im = ImageOps.contain(im, (MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    k_src = im.width / src_w  # 1.0 unless the picture was reduced; pixel boxes in source space are scaled by it
    report = []
    for op in ops:
        name = op["op"]
        if name == "crop":
            x0, y0, x1, y1 = (float(v) for v in op["box"])
            if max(x0, y0, x1, y1) <= 1:
                x0, x1, y0, y1 = x0 * im.width, x1 * im.width, y0 * im.height, y1 * im.height
            elif k_src != 1.0:
                x0, y0, x1, y1 = x0 * k_src, y0 * k_src, x1 * k_src, y1 * k_src
            box = (int(x0), int(y0), min(im.width, int(x1)), min(im.height, int(y1)))
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                raise OpError("crop.box is outside the image or smaller than 8 px.")
            im = im.crop(box)
            report.append(f"cropped to {box[2] - box[0]}x{box[3] - box[1]}")
        elif name == "fit":
            im = _fit(im, op["ratio"], op.get("mode", "cover"), op.get("focus", "auto"))
            report.append(f"fit to {op['ratio']} ({op.get('mode', 'cover')})")
        elif name == "pad":
            im = _pad(im, op["ratio"], _colour(op.get("colour"), brand, "pad.colour"))
            report.append(f"padded to {op['ratio']}")
        elif name == "cutout":
            cut = hero_edit.cutout_detailed(im)
            if not cut["ok"]:
                raise OpError(f"The fast cut-out isn't confident here ({cut['reason']}). Use cutout_product with engine=hq "
                              "(the ML cut-out on the worker) or place the whole photo with the card op.")
            im = cut["image"]
            report.append(f"fast cut-out (confidence {cut['confidence']:.2f}, {cut['components']} piece(s))")
        elif name == "card":
            ratio = op.get("ratio") or "4:5"
            im, rep = card(im, ratio, _colour(op.get("ground"), brand, "card.ground"), float(op.get("scale") or 0.72),
                           0.55 if op.get("shadow") is None else float(op["shadow"]),
                           36 if op.get("radius") is None else int(op["radius"]),
                           str(op.get("name") or ""), str(op.get("price") or ""))
            report.append(f"placed on a {ratio} card ({rep['placed']})")
        elif name == "round":
            im = hero_edit.rounded(im, int(op["radius"]))
            report.append(f"rounded corners {int(op['radius'])} px")
        elif name == "grade":
            im = _grade(im, op, brand)
            report.append(f"graded ({op.get('preset', 'neutral')})")
        elif name == "sharpen":
            amt = float(op["amount"])
            if amt:
                im = im.filter(ImageFilter.UnsharpMask(2, int(60 * amt), 2))
            report.append("sharpened")
        if name in ("crop", "fit", "pad", "cutout", "card"):
            k_src = 1.0  # later pixel boxes refer to this new picture
        if max(im.size) > MAX_SIDE:  # no op may leave a picture bigger than the cap (memory on the web dyno)
            im = ImageOps.contain(im, (MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return im, report
