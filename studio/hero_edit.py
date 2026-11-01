"""hero_edit: the "premium edit" of an official product / hero image for an editorial cover (owner, 1 Nov 2026).

What Hark does by hand for a HuhByte carousel (the ernd card on its own sky art), as one reusable step the agent calls
(agent tool ``hero_edit``; REST/MCP through the run tools, docs/AGENT_PARITY.md). Pure Pillow, no model download, so it
runs inside the 512 MB web worker:

1. crop      pick the crop by saliency (edge + colour-contrast energy, never the empty sky) or a face box when given
2. grade     colour-match the scene to the brand background (pull toward the brand ground, lower saturation, darken)
3. fade      a soft vignette and a gradient fade into the brand ground where the words sit
4. product   for a product / card shot: cut it out of its plain stage (border-colour key, feathered), round its corners,
             tilt it a few degrees and drop a soft shadow under it
5. founders  circular founder photos with a white ring, overlapping the product's lower-left corner (or in a row)

Every function takes and returns PIL images; ``premium_hero`` runs the whole recipe and ``before_after`` makes the
side-by-side the contact sheets show. Optional: when ``rembg`` (MIT) is installed the cut-out uses it; it isn't by default
(its model is ~170 MB, more than the free worker can hold), and the border-colour key covers product shots on a stage.
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

BLACK = (0, 0, 0)


# ---------------------------------------------------------------------------------------------------- 1. crop
def saliency(im: Image.Image, size: int = 64) -> Image.Image:
    """A small 'where is the interesting stuff' map: edges + distance from the image's mean colour, blurred."""
    small = im.convert("RGB").resize((size, max(1, int(size * im.height / im.width))), Image.BILINEAR)
    edges = small.convert("L").filter(ImageFilter.FIND_EDGES)
    mean = tuple(int(v) for v in ImageStat.Stat(small).mean)
    diff = ImageChops.difference(small, Image.new("RGB", small.size, mean)).convert("L")
    sat = small.convert("HSV").getchannel("S")
    m = ImageChops.add(ImageChops.add(edges, diff, scale=1.3), sat, scale=2.2)
    return m.filter(ImageFilter.GaussianBlur(2))


def best_crop(im: Image.Image, w: int, h: int, face: tuple[int, int, int, int] | None = None,
              bias_y: float = 0.45) -> tuple[int, int, int, int]:
    """The crop box (in im's pixels) of aspect w:h that holds the most saliency; a face box (x0,y0,x1,y1) wins outright."""
    W, H = im.size
    target = w / h
    if W / H > target:
        cw, ch = int(H * target), H
    else:
        cw, ch = W, int(W / target)
    if face:
        cx, cy = (face[0] + face[2]) / 2, face[1] + (face[3] - face[1]) * 0.45
        x0 = int(min(max(0, cx - cw / 2), W - cw))
        y0 = int(min(max(0, cy - ch * 0.42), H - ch))
        return x0, y0, x0 + cw, y0 + ch
    sm = saliency(im)
    sx, sy = sm.width / W, sm.height / H
    px = sm.load()
    best, box = -1.0, (0, 0, cw, ch)
    steps = 12
    for i in range(steps + 1):
        for j in range(steps + 1):
            x0 = int((W - cw) * i / steps)
            y0 = int((H - ch) * j / steps)
            a0, b0 = int(x0 * sx), int(y0 * sy)
            a1, b1 = max(a0 + 1, int((x0 + cw) * sx)), max(b0 + 1, int((y0 + ch) * sy))
            tot = 0
            for yy in range(b0, min(b1, sm.height), 2):
                for xx in range(a0, min(a1, sm.width), 2):
                    tot += px[xx, yy]
            # a gentle preference for the upper-middle (where people and products usually are)
            cyn = (y0 + ch / 2) / H
            score = tot * (1.0 - 0.25 * abs(cyn - bias_y))
            if score > best:
                best, box = score, (x0, y0, x0 + cw, y0 + ch)
    return box


def crop_to(im: Image.Image, w: int, h: int, face=None, bias_y: float = 0.45) -> Image.Image:
    return im.convert("RGB").crop(best_crop(im, w, h, face, bias_y)).resize((w, h), Image.LANCZOS)


# ---------------------------------------------------------------------------------------------------- 2. grade
def color_match(im: Image.Image, ground=BLACK, strength: float = 0.35, desat: float = 0.85, darken: float = 0.82) -> Image.Image:
    """Pull the scene toward the brand ground so the cover reads as one piece with the slides that follow."""
    im = im.convert("RGB")
    im = ImageEnhance.Color(im).enhance(desat)
    im = ImageEnhance.Brightness(im).enhance(darken)
    return Image.blend(im, Image.new("RGB", im.size, tuple(ground)), max(0.0, min(1.0, strength)))


# ---------------------------------------------------------------------------------------------------- 3. fade
def vignette(im: Image.Image, ground=BLACK, edge: float = 0.55, fade_from: float | None = 0.55) -> Image.Image:
    """A soft radial vignette, then (fade_from) a smoothstep fade into the ground from that height to the bottom."""
    im = im.convert("RGB")
    W, H = im.size
    m = Image.new("L", (W // 4 + 1, H // 4 + 1), 0)
    w4, h4 = m.size
    # radial mask the cheap way: distance from the centre
    px = m.load()
    for y in range(h4):
        for x in range(w4):
            dx, dy = (x / w4 - 0.5) * 2, (y / h4 - 0.45) * 2
            r = min(1.0, math.sqrt(dx * dx * 0.8 + dy * dy))
            px[x, y] = int(255 * edge * max(0.0, (r - 0.45) / 0.55) ** 1.6)
    m = m.resize((W, H), Image.BILINEAR).filter(ImageFilter.GaussianBlur(12))
    im.paste(Image.new("RGB", (W, H), tuple(ground)), (0, 0), m)
    if fade_from is not None:
        g = Image.new("L", (1, H), 0)
        y0 = int(H * fade_from)
        for y in range(H):
            t = 0.0 if y < y0 else min(1.0, (y - y0) / max(1, H - y0))
            g.putpixel((0, y), int(255 * t * t * (3 - 2 * t)))
        im.paste(Image.new("RGB", (W, H), tuple(ground)), (0, 0), g.resize((W, H)))
    return im


# ---------------------------------------------------------------------------------------------------- 4. product
def cutout(im: Image.Image, tol: int = 38, pad: int = 8) -> Image.Image | None:
    """A product shot on a plain stage -> the product as RGBA (border-colour key, feathered). rembg when installed.
    None when the border isn't one colour (a busy photo has no stage to cut)."""
    try:  # optional, MIT; not installed on the free worker
        from rembg import remove  # type: ignore
        out = remove(im.convert("RGBA"))
        box = out.getchannel("A").point(lambda v: 255 if v > 24 else 0).getbbox()
        return out.crop(box) if box else None
    except Exception:  # noqa: BLE001
        pass
    rgb = im.convert("RGB")
    W, H = rgb.size
    border = [rgb.getpixel((x, y)) for x in range(0, W, max(1, W // 40)) for y in (0, H - 1)] + \
             [rgb.getpixel((x, y)) for y in range(0, H, max(1, H // 40)) for x in (0, W - 1)]
    mean = tuple(sum(c[i] for c in border) // len(border) for i in range(3))
    spread = max(max(abs(c[i] - mean[i]) for i in range(3)) for c in border)
    if spread > 90:
        return None
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, mean)).convert("L")
    mask = diff.point(lambda v: 255 if v > tol else 0).filter(ImageFilter.MedianFilter(5))
    box = _main_object(mask)
    if not box or (box[2] - box[0]) * (box[3] - box[1]) < 0.04 * W * H:
        return None
    box = (max(0, box[0] - pad), max(0, box[1] - pad), min(W, box[2] + pad), min(H, box[3] + pad))
    # the object's own rectangle: keep everything inside the box solid, feather only the stage around it
    obj = rgb.crop(box).convert("RGBA")
    # a tighter key inside the box (the soft glow around a product on a white stage is not the product), holes filled
    # (a chip or a white logo on the product is the stage colour but inside the object), then a 1 px choke + feather
    m = diff.crop(box).point(lambda v: 255 if v > max(tol, 70) else 0).filter(ImageFilter.MedianFilter(5))
    m = _fill_holes(m).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.2))
    tight = m.point(lambda v: 255 if v > 128 else 0).getbbox()
    obj.putalpha(m)
    return obj.crop(tight) if tight else obj


def _fill_holes(m: Image.Image) -> Image.Image:
    """Everything not reachable from the border through background becomes object."""
    pad = Image.new("L", (m.width + 2, m.height + 2), 0)
    pad.paste(m, (1, 1))
    ImageDraw.floodfill(pad, (0, 0), 128)
    return pad.crop((1, 1, m.width + 1, m.height + 1)).point(lambda v: 0 if v == 128 else 255)


def _main_object(mask: Image.Image, size: int = 240) -> tuple[int, int, int, int] | None:
    """The bounding box of the biggest solid blob in a stage mask: a product, not the lines of text printed next to it
    (text lines are many thin, wide blobs). Connected components on a small copy, pure Python."""
    W, H = mask.size
    sw = size
    sh = max(1, int(H * size / W))
    sm = mask.resize((sw, sh), Image.BILINEAR).point(lambda v: 255 if v > 100 else 0).filter(ImageFilter.MinFilter(3))
    px = sm.load()
    seen = bytearray(sw * sh)
    best, bbox = 0.0, None
    for y0 in range(sh):
        for x0 in range(sw):
            if not px[x0, y0] or seen[y0 * sw + x0]:
                continue
            stack, n = [(x0, y0)], 0
            seen[y0 * sw + x0] = 1
            a0, b0, a1, b1 = x0, y0, x0, y0
            while stack:
                x, y = stack.pop()
                n += 1
                a0, b0, a1, b1 = min(a0, x), min(b0, y), max(a1, x), max(b1, y)
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and px[nx, ny] and not seen[ny * sw + nx]:
                        seen[ny * sw + nx] = 1
                        stack.append((nx, ny))
            bw, bh = a1 - a0 + 1, b1 - b0 + 1
            fill = n / (bw * bh)
            score = n * fill * (0.3 if bw / bh > 4 else 1.0)  # a solid object beats a long thin line of text
            if score > best:
                best, bbox = score, (a0, b0, a1 + 1, b1 + 1)
    if not bbox:
        return None
    fx, fy = W / sw, H / sh
    return int(bbox[0] * fx), int(bbox[1] * fy), int(bbox[2] * fx), int(bbox[3] * fy)


def rounded(im: Image.Image, r: int) -> Image.Image:
    im = im.convert("RGBA")
    m = Image.new("L", (im.width * 3, im.height * 3), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, m.width - 1, m.height - 1), r * 3, fill=255)
    m = m.resize(im.size, Image.LANCZOS)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), m))
    return im


def product_shot(obj: Image.Image, width: int, tilt: float = -6.0, radius: int = 0) -> tuple[Image.Image, Image.Image]:
    """(product, shadow) as RGBA: scaled to width, corners rounded, tilted; the shadow is a blurred offset of its alpha."""
    obj = obj.convert("RGBA")
    obj = obj.resize((width, max(1, int(obj.height * width / obj.width))), Image.LANCZOS)
    if radius:
        obj = rounded(obj, radius)
    obj = obj.filter(ImageFilter.UnsharpMask(2, 50, 2))
    obj = obj.rotate(tilt, resample=Image.BICUBIC, expand=True)
    pad = 60
    sh = Image.new("RGBA", (obj.width + 2 * pad, obj.height + 2 * pad), (0, 0, 0, 0))
    a = Image.new("L", sh.size, 0)
    a.paste(obj.getchannel("A").point(lambda v: int(v * 0.7)), (pad, pad))
    sh.putalpha(a.filter(ImageFilter.GaussianBlur(26)))
    return obj, sh


# ---------------------------------------------------------------------------------------------------- 5. founders
def avatar(photo: Image.Image, d: int, ring: int = 6, ring_color=(255, 255, 255)) -> Image.Image:
    """A founder photo as a circle with a white ring; the crop keeps the face (saliency, biased to the upper half)."""
    sq = crop_to(photo, d, d, bias_y=0.38)
    m = Image.new("L", (d * 4, d * 4), 0)
    ImageDraw.Draw(m).ellipse((0, 0, d * 4 - 1, d * 4 - 1), fill=255)
    m = m.resize((d, d), Image.LANCZOS)
    D = d + 2 * ring
    out = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    rm = Image.new("L", (D * 4, D * 4), 0)
    ImageDraw.Draw(rm).ellipse((0, 0, D * 4 - 1, D * 4 - 1), fill=255)
    out.paste(Image.new("RGBA", (D, D), tuple(ring_color) + (255,)), (0, 0), rm.resize((D, D), Image.LANCZOS))
    sq = sq.convert("RGBA")
    sq.putalpha(m)
    out.alpha_composite(sq, (ring, ring))
    return out


def avatar_row(photos: list[Image.Image], d: int, overlap: int = 26, ring: int = 5) -> Image.Image | None:
    if not photos:
        return None
    avs = [avatar(p, d, ring) for p in photos]
    step = avs[0].width - overlap
    out = Image.new("RGBA", (step * (len(avs) - 1) + avs[0].width, avs[0].height), (0, 0, 0, 0))
    for i, a in enumerate(avs):
        out.alpha_composite(a, (i * step, 0))
    return out


# ---------------------------------------------------------------------------------------------------- the recipe
def premium_hero(w: int, h: int, *, scene: Image.Image | None = None, product: Image.Image | None = None,
                 founders: list[Image.Image] | None = None, ground=BLACK, kind: str = "auto", tilt: float = -6.0,
                 fade_from: float | None = 0.62, product_y: float = 0.12, product_scale: float = 0.56,
                 face: tuple | None = None) -> tuple[Image.Image, dict]:
    """The whole edit -> (hero image w x h, report of the steps it took). scene: the brand's own art or a photo;
    product: a product / card shot (cut out of its stage when it has one). With only a product image and no scene, the
    product's own picture, blurred and graded, is the scene."""
    steps: list[str] = []
    obj = None
    if product is not None and kind in ("auto", "product", "card"):
        obj = cutout(product)
        steps.append("cut the product out of its stage" if obj is not None else "product photo has no plain stage: used whole")
    base = scene if scene is not None else (product if obj is None else None)
    if base is None and product is not None:  # only a product: its own photo, blurred, is the stage
        base = product.convert("RGB").filter(ImageFilter.GaussianBlur(30))
        steps.append("no scene art: the product photo, blurred, is the backdrop")
    if base is None:
        canvas = Image.new("RGB", (w, h), tuple(ground))
    else:
        canvas = crop_to(base, w, h, face=face, bias_y=0.5 if obj is not None else 0.42)
        steps.append("crop by saliency" + (" (face)" if face else ""))
        canvas = color_match(canvas, ground, strength=0.3 if obj is not None else 0.22)
        steps.append("colour-matched to the brand ground")
    canvas = canvas.convert("RGBA")
    pbox = None
    if obj is not None:
        pw = int(w * product_scale)
        r = int(pw * 0.06) if kind in ("auto", "card") and 1.4 < obj.width / max(1, obj.height) < 1.9 else 0
        p, sh = product_shot(obj, pw, tilt=tilt, radius=r)
        x = (w - p.width) // 2 + int(w * 0.03)
        y = int(h * product_y)
        canvas.alpha_composite(Image.new("RGBA", sh.size, (0, 0, 0, 0)), (0, 0))
        shadow = Image.new("RGBA", sh.size, (0, 0, 0, 255))
        shadow.putalpha(sh.getchannel("A"))
        canvas.alpha_composite(shadow, (x - 60 + 12, y - 60 + 28))
        canvas.alpha_composite(p, (x, y))
        pbox = (x, y, x + p.width, y + p.height)
        steps.append(f"product shot: rounded, tilted {tilt:g} deg, soft drop shadow")
    out = vignette(canvas.convert("RGB"), ground, edge=0.5, fade_from=fade_from)
    steps.append("soft vignette + fade into the ground")
    if founders:
        d = int(w * 0.118)
        row = avatar_row(founders, d)
        if row is not None:
            x = int(w * 0.067) + 10
            y = (pbox[3] - int(d * 0.75)) if pbox else int(h * 0.42)
            o = out.convert("RGBA")
            o.alpha_composite(row, (x, min(max(0, y), h - row.height)))
            out = o.convert("RGB")
            steps.append(f"{len(founders)} founder avatar(s) with a white ring")
    return out, {"steps": steps, "product_box": pbox, "cutout": obj is not None}


def before_after(before: Image.Image, after: Image.Image, h: int = 540) -> Image.Image:
    """The side-by-side the contact sheets show (official image as fetched | after hero_edit)."""
    a = ImageOps.contain(before.convert("RGB"), (int(h * 1.2), h))
    b = ImageOps.contain(after.convert("RGB"), (int(h * 1.2), h))
    out = Image.new("RGB", (a.width + b.width + 30, h + 40), (24, 24, 26))
    out.paste(a, (10, 30))
    out.paste(b, (a.width + 20, 30))
    d = ImageDraw.Draw(out)
    d.text((12, 8), "BEFORE (official image)", fill=(200, 200, 205))
    d.text((a.width + 22, 8), "AFTER hero_edit", fill=(201, 172, 250))
    return out
