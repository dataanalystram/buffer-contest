"""hero_edit: the "premium edit" of an official product / hero image for an editorial cover (owner, 1 Nov 2026).

What Hark does by hand for a HuhByte carousel (the ernd card on its own sky art), as one reusable step the agent calls
(agent tool ``hero_edit``; REST/MCP through the run tools, docs/AGENT_PARITY.md). Pure Pillow, no model download, so it
runs inside the 512 MB web worker:

1. crop      pick the crop by saliency (edge + colour-contrast energy, never the empty sky) or a face box when given
2. grade     colour-match the scene to the brand background (pull toward the brand ground, lower saturation, darken)
3. fade      a soft vignette and a gradient fade into the brand ground where the words sit
4. product   for a product / card shot: cut it out of its plain stage (border flood, every piece kept), round its corners,
             tilt it a few degrees and drop a soft shadow under it
5. founders  circular founder photos with a white ring, overlapping the product's lower-left corner (or in a row)

Every function takes and returns PIL images; ``premium_hero`` runs the whole recipe and ``before_after`` makes the
side-by-side the contact sheets show. The cut-out here is the fast one (no model). The ML cut-out (rembg with ISNet
isnet-general-use, Apache-2.0, or BiRefNet-lite, MIT; never BRIA RMBG) is studio/image_matte.py on the render worker.
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
# The fast cut-out (no model, web-safe: Pillow + plain Python on a small working copy). Rewritten 4 Nov 2026
# (docs/plans/AGENT_RUNS_EDITING.md 2.1-2.2): the old near-white key (inner threshold 70) erased off-white
# earcups, kept one blob and zoomed into it. Now the background is only what is connected to the border AND looks like
# the stage (close in Lab, or a little further but flat), so a light part of the product survives when its outline
# stops the flood; every significant piece is kept; holes inside the object are filled; low confidence says so instead
# of handing back a torn cut-out. The ML cut-out (studio/image_matte.py, ISNet / BiRefNet-lite) runs on the worker.
WORK = 400            # working size (longest side) for the flood and the components
MAX_IN = 2048         # bigger inputs are scaled down first (a 1080 render never needs more)
MIN_PART = 0.003      # a piece of the object smaller than this share of the image is a speck
MIN_CONFIDENCE = 0.5  # below this cutout() answers None and callers frame the whole photo instead
_LAB = None


def _to_lab(rgb: Image.Image) -> Image.Image:
    global _LAB
    from PIL import ImageCms
    if _LAB is None:
        _LAB = ImageCms.buildTransformFromOpenProfiles(ImageCms.createProfile("sRGB"), ImageCms.createProfile("LAB"), "RGB", "LAB")
    return ImageCms.applyTransform(rgb, _LAB)


def _delta(lab: Image.Image, ref: tuple[int, int, int]) -> Image.Image:
    """|dL| + |da| + |db| against one Lab colour (8-bit Lab units, clipped at 255): a cheap Delta E."""
    out = None
    for band, v in zip(lab.split(), ref):
        d = ImageChops.difference(band, Image.new("L", lab.size, int(v)))
        out = d if out is None else ImageChops.add(out, d)
    return out


def _grad(lab: Image.Image) -> Image.Image:
    """Morphological gradient (3x3 max - min) summed over the Lab bands: high on any edge, ~0 on a flat stage."""
    out = None
    for band in lab.split():
        g = ImageChops.subtract(band.filter(ImageFilter.MaxFilter(3)), band.filter(ImageFilter.MinFilter(3)))
        out = g if out is None else ImageChops.add(out, g)
    return out


def _pct(vals: list[int], q: float) -> int:
    v = sorted(vals)
    return v[min(len(v) - 1, int(q * (len(v) - 1)))] if v else 0


def _border_idx(w: int, h: int) -> list[int]:
    idx = list(range(w)) + [(h - 1) * w + x for x in range(w)]
    idx += [y * w for y in range(1, h - 1)] + [y * w + w - 1 for y in range(1, h - 1)]
    return idx


def _flood(cand: bytearray, w: int, h: int, seeds: list[int]) -> bytearray:
    """4-connected flood over cand (1 = may be background) from seeds; returns 1 where reached."""
    seen = bytearray(w * h)
    stack = [i for i in seeds if cand[i]]
    for i in stack:
        seen[i] = 1
    while stack:
        i = stack.pop()
        x = i % w
        for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x < w - 1 else -1):
            if 0 <= j < w * h and cand[j] and not seen[j]:
                seen[j] = 1
                stack.append(j)
    return seen


def _components(mask: bytearray, w: int, h: int) -> list[dict]:
    """4-connected components of mask (1 = set): [{n, box (x0,y0,x1,y1), px}] biggest first."""
    seen = bytearray(w * h)
    comps = []
    for s0 in range(w * h):
        if not mask[s0] or seen[s0]:
            continue
        seen[s0] = 1
        stack, px = [s0], []
        x0 = x1 = s0 % w
        y0 = y1 = s0 // w
        while stack:
            i = stack.pop()
            px.append(i)
            x, y = i % w, i // w
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for j in (i - w, i + w, i - 1 if x else -1, i + 1 if x < w - 1 else -1):
                if 0 <= j < w * h and mask[j] and not seen[j]:
                    seen[j] = 1
                    stack.append(j)
        comps.append({"n": len(px), "box": (x0, y0, x1 + 1, y1 + 1), "px": px})
    comps.sort(key=lambda c: -c["n"])
    return comps


def _alpha_cut(im: Image.Image) -> dict | None:
    """An image that already has a transparent background (a PNG cut-out): use its own alpha."""
    if im.mode not in ("RGBA", "LA") and not (im.mode == "P" and "transparency" in im.info):
        return None
    rgba = im.convert("RGBA")
    a = rgba.getchannel("A")
    if a.getextrema()[0] > 16:  # nothing transparent: an ordinary photo saved with an alpha channel
        return None
    box = a.point(lambda v: 255 if v > 24 else 0).getbbox()
    if not box:
        return None
    return {"image": rgba.crop(box), "ok": True, "confidence": 1.0, "reason": "the image already had a transparent background",
            "components": 1, "box": box, "engine": "alpha"}


def cutout_detailed(im: Image.Image, pad: int = 8) -> dict:
    """The fast cut-out with its evidence: {image (RGBA, cropped to the object, or None), ok, confidence 0-1, reason,
    components, box (x0,y0,x1,y1 in the (capped) input's pixels), engine: fast | alpha}. ok False means: don't composite
    it; frame the whole photo (premium_hero's clean card) or queue the ML cut-out (image_matte on the worker)."""
    own = _alpha_cut(im)
    if own:
        return own
    rgb = im.convert("RGB")
    if max(rgb.size) > MAX_IN:
        rgb = ImageOps.contain(rgb, (MAX_IN, MAX_IN), Image.LANCZOS)
    W, H = rgb.size
    fail = {"image": None, "ok": False, "confidence": 0.0, "components": 0, "box": None, "engine": "fast"}
    if W < 16 or H < 16:
        return {**fail, "reason": "image too small to cut"}
    s = min(1.0, WORK / max(W, H))
    sw, sh = max(8, round(W * s)), max(8, round(H * s))
    small = rgb.resize((sw, sh), Image.BOX) if s < 1 else rgb
    lab = _to_lab(small.filter(ImageFilter.GaussianBlur(0.8)))  # sensor / JPEG noise off, edges stay
    border = _border_idx(sw, sh)
    lab_px = [lab.getpixel((i % sw, i // sw)) for i in border]
    stage = tuple(_pct([p[k] for p in lab_px], 0.5) for k in range(3))
    dist = _delta(lab, stage)
    grad = _grad(lab)
    d = dist.tobytes()
    g = grad.tobytes()
    bd = [d[i] for i in border]
    spread = _pct(bd, 0.75)
    if spread > 40:  # the border is not one colour: a busy photo has no stage to cut
        return {**fail, "reason": "the photo has no plain background around the product"}
    # noise = how much the stage itself varies pixel to pixel; a gentle light fall-off across the stage is not noise
    # (it is what the "flat" rule below lets the flood follow)
    noise = _pct([g[i] for i in border], 0.9)
    tol_near = max(5, min(16, 3 + noise))
    tol_far = max(30, min(60, 3 * tol_near + 12))
    g_thr = max(4, min(24, noise + 2))
    n = sw * sh
    # walls: every edge, thickened to 5 px so noise can't open a gap in a faint outline (an off-white earcup on white)
    wall = grad.point(lambda v: 255 if v > g_thr else 0).filter(ImageFilter.MaxFilter(5)).tobytes()
    cand = bytearray(1 if (not wall[i] and d[i] <= tol_far) or d[i] <= 2 else 0 for i in range(n))
    bg = _flood(cand, sw, sh, border)
    # enclosed stage-like regions: a big flat hole that matches the stage (a mug handle, the gap in a ring) is background;
    # anything else enclosed (a white logo, a light earcup inside its outline) is the object
    holes = bytearray(1 if cand[i] and not bg[i] else 0 for i in range(n))
    for c in _components(holes, sw, sh):
        if c["n"] < 0.01 * n:
            break
        if sum(d[i] for i in c["px"]) / c["n"] <= tol_near:
            for i in c["px"]:
                bg[i] = 1
    obj = bytearray(0 if bg[i] else 1 for i in range(n))
    comps = _components(obj, sw, sh)
    keep, dropped = [], 0
    big = comps[0] if comps else None
    near = 0.05 * (sw * sw + sh * sh) ** 0.5
    for c in comps:
        bw, bh = c["box"][2] - c["box"][0], c["box"][3] - c["box"][1]
        thin = bw / max(1, bh) > 5 and c["n"] < 0.25 * big["n"]  # a line of text beside the product
        # every significant piece is kept: a big one anywhere, a smaller one only next to the main piece (a second
        # earbud, a cable plug), so the lines of a spec sheet printed beside the product stay out
        gap = max(big["box"][0] - c["box"][2], c["box"][0] - big["box"][2], big["box"][1] - c["box"][3], c["box"][1] - big["box"][3], 0)
        significant = c["n"] >= 0.15 * big["n"] or gap <= near
        if c["n"] >= MIN_PART * n and not thin and significant:
            keep.append(c)
        else:
            dropped += 1
            for i in c["px"]:
                obj[i] = 0
    if not keep:
        return {**fail, "reason": "no product found on the background"}
    x0 = min(c["box"][0] for c in keep)
    y0 = min(c["box"][1] for c in keep)
    x1 = max(c["box"][2] for c in keep)
    y1 = max(c["box"][3] for c in keep)
    area = sum(c["n"] for c in keep)
    frac, solid = area / n, area / max(1, (x1 - x0) * (y1 - y0))
    conf = 1.0
    reasons = []
    if frac < 0.02:
        conf *= frac / 0.02
        reasons.append("the product is tiny in the frame")
    if frac > 0.85:
        conf *= max(0.0, (1.0 - frac) / 0.15)
        reasons.append("almost nothing around the product is background")
    if len(keep) > 3:
        conf *= 0.8 if len(keep) <= 6 else 0.4
        reasons.append(f"the product came out in {len(keep)} pieces")
    if solid < 0.25:
        conf *= max(0.2, solid / 0.25)
        reasons.append("the cut-out is mostly empty inside its box")
    conf *= max(0.3, 1.0 - max(0, spread - 12) / 40)
    # off-stage colour the flood still took as background, inside the product's own box: a light part may have been lost
    lost = sum(1 for y in range(y0, y1) for i in range(y * sw + x0, y * sw + x1) if bg[i] and d[i] > tol_near + 2)
    lost_frac = lost / max(1, (x1 - x0) * (y1 - y0))
    if lost_frac > 0.03:
        # measured on the regression headphones over seeded sensor noise: every cut with this sign had a torn earcup
        # (rim alpha 85-135 of 255) at confidence 0.51-0.65, so it is never confident enough to composite
        conf = min(conf * max(0.1, 1.0 - (lost_frac - 0.03) * 8), MIN_CONFIDENCE * 0.9)
        reasons.append("parts close to the background colour may be missing")
    conf = round(max(0.0, min(1.0, conf)), 2)
    # full-resolution mask: the small mask scaled up, then the edge band re-decided per pixel from the colour distance
    m_small = Image.frombytes("L", (sw, sh), bytes(255 if v else 0 for v in obj))
    m = m_small.resize((W, H), Image.BILINEAR).point(lambda v: 255 if v >= 128 else 0)
    if s < 1:
        k = max(3, int(6 / s) | 1)  # the band covers the 5 px wall the flood stopped at
        band = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(k)), m.filter(ImageFilter.MinFilter(k)))
        dfull = _delta(_to_lab(rgb.filter(ImageFilter.GaussianBlur(1.2))), stage)
        ramp = max(4, tol_near)
        soft = dfull.point(lambda v: max(0, min(255, (v - tol_near) * 255 // ramp)))
        m = Image.composite(soft, m, band)
    m = m.filter(ImageFilter.GaussianBlur(0.8))  # 1 px feather only: no choke, thin headbands and cables stay
    fx, fy = W / sw, H / sh
    box = (max(0, int(x0 * fx) - pad), max(0, int(y0 * fy) - pad),
           min(W, int(x1 * fx + 0.999) + pad), min(H, int(y1 * fy + 0.999) + pad))
    out = rgb.convert("RGBA")
    out.putalpha(m)
    out = out.crop(box)
    why = "; ".join(reasons) if reasons else "clean background, product outline found"
    return {"image": out, "ok": conf >= MIN_CONFIDENCE, "confidence": conf, "reason": why, "components": len(keep),
            "dropped": dropped, "box": box, "engine": "fast", "lost": round(lost_frac, 3)}


def cutout(im: Image.Image, pad: int = 8) -> Image.Image | None:
    """A product shot on a plain stage -> the product as RGBA, cropped to it (the fast cut-out above). None when the
    photo has no plain stage or the cut isn't confident: never a torn cut-out. No model is loaded here (rembg's default
    model is BRIA RMBG, non-commercial; the worker's studio/image_matte.py always names ISNet or BiRefNet-lite)."""
    r = cutout_detailed(im, pad)
    return r["image"] if r["ok"] else None


def rounded(im: Image.Image, r: int) -> Image.Image:
    im = im.convert("RGBA")
    s = 3 if max(im.size) <= 2048 else 1  # 3x supersampled edge; at 4096 px a 3x mask alone would be ~150 MB
    m = Image.new("L", (im.width * s, im.height * s), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, m.width - 1, m.height - 1), r * s, fill=255)
    m = m.resize(im.size, Image.LANCZOS)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), m))
    return im


def product_shot(obj: Image.Image, width: int, tilt: float = -6.0, radius: int = 0,
                 max_h: int | None = None) -> tuple[Image.Image, Image.Image]:
    """(product, shadow) as RGBA: fitted inside width x max_h (never width-only: a tall product would overflow the
    canvas), corners rounded, tilted; the shadow is a blurred offset of its alpha."""
    obj = obj.convert("RGBA")
    k = width / obj.width
    if max_h:
        k = min(k, max_h / obj.height)
    obj = obj.resize((max(1, int(obj.width * k)), max(1, int(obj.height * k))), Image.LANCZOS)
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
    cut: dict = {}
    card = False
    if product is not None and kind in ("auto", "product", "card"):
        cut = cutout_detailed(product)
        if cut["ok"]:
            obj = cut["image"]
            steps.append(f"cut the product out of its stage (fast cut-out, confidence {cut['confidence']:.2f}, "
                         f"{cut['components']} piece{'s' if cut['components'] != 1 else ''})")
        else:
            # never a torn cut-out, never a zoomed crop of the product: the whole photo on a clean card (plan 2.2)
            obj, card = rounded(ImageOps.contain(product.convert("RGB"), (1600, 1600), Image.LANCZOS), 28), True
            steps.append(f"cut-out not confident ({cut['reason']}): the whole product photo framed on a clean card; "
                         "the high-quality cut-out (cutout_product engine=hq) runs on the worker")
    base = scene
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
        r = int(pw * 0.06) if not card and kind in ("auto", "card") and 1.4 < obj.width / max(1, obj.height) < 1.9 else 0
        p, sh = product_shot(obj, pw, tilt=0.0 if card else tilt, radius=r, max_h=int(h * 0.5))
        x = (w - p.width) // 2 + int(w * 0.03)
        y = int(h * product_y)
        canvas.alpha_composite(Image.new("RGBA", sh.size, (0, 0, 0, 0)), (0, 0))
        shadow = Image.new("RGBA", sh.size, (0, 0, 0, 255))
        shadow.putalpha(sh.getchannel("A"))
        canvas.alpha_composite(shadow, (x - 60 + 12, y - 60 + 28))
        canvas.alpha_composite(p, (x, y))
        pbox = (x, y, x + p.width, y + p.height)
        steps.append("product photo on a clean card, soft drop shadow" if card else
                     f"product shot: rounded, tilted {tilt:g} deg, soft drop shadow")
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
    return out, {"steps": steps, "product_box": pbox, "cutout": obj is not None and not card, "clean_card": card,
                 "cutout_confidence": cut.get("confidence"), "cutout_engine": cut.get("engine")}


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
