"""Branding THROUGHOUT a video, not only at the end (owner, 2 Nov 2026: "it only added the end card").

``layers`` returns the timed overlay PNGs compose.render_style lays over the main footage when ``brand_pass`` is on:

  hook     0 - 2.2 s   a branded hook title over the first seconds (brand headline font, key word in the accent, a soft
                       dark band behind it), unless the style already opens on a cover card or keeps a hook on screen
  bug      whole clip  a small logo watermark top-right inside the platform-safe area (the @handle in a pill when there
                       is no logo), unless the layout already shows the logo
  lower    2.4 - 7 s   a lower-third with the source credit ("Video: Screen Studio") or the brand handle, above the
                       caption line, unless the layout already shows a credit chip

Captions follow the brand colours (render_style passes brand_colors=True) and the end card stays last. Each element is a
full-frame RGBA PNG plus its time window; nothing here runs FFmpeg.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import textfit

HOOK_SECS = 2.2
LOWER_IN, LOWER_OUT = 2.4, 7.0


def _rgb(h: str, default=(255, 255, 255)) -> tuple[int, int, int]:
    h = (h or "").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return default


def _safe(W: int, H: int) -> tuple[int, int, int, int]:
    k = W / 1080
    if H / W > 1.6:
        return round(60 * k), round(230 * k), W - round(150 * k), H - round(420 * k)
    return round(56 * k), round(56 * k), W - round(56 * k), H - round(56 * k)


def _logo(brand: dict, h: int) -> Image.Image | None:
    p = brand.get("logo")
    if not p or not Path(p).is_file():
        return None
    im = Image.open(p).convert("RGBA")
    return im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)


def _clear_y(x0: int, x1: int, y: int, h: int, avoid: list, H: int) -> int:
    """The first y at or below ``y`` where a box x0..x1 of height h touches none of ``avoid`` (top half only)."""
    for _ in range(12):
        hit = [a for a in avoid if a[0] < x1 and a[2] > x0 and a[1] < y + h and a[3] > y]
        if not hit:
            return y
        y = max(a[3] for a in hit) + max(8, H // 160)
        if y > H * 0.5:
            break
    return y


def bug(W: int, H: int, brand: dict, opacity: float = 0.82, avoid: list | None = None) -> tuple[Image.Image, tuple]:
    """Small logo watermark top-right (inside the safe area), or the @handle in a quiet pill; it steps down past
    anything in ``avoid`` (a hook or credit the layout already put at the top)."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    avoid = [tuple(a) for a in (avoid or []) if isinstance(a, (list, tuple)) and len(a) == 4 and all(isinstance(v, (int, float)) for v in a)]
    l, t, r, b = _safe(W, H)
    lg = _logo(brand, max(28, int(H * (0.03 if H / W > 1.6 else 0.045))))
    if lg:
        a = lg.getchannel("A").point(lambda v: int(v * opacity))
        lg.putalpha(a)
        x, y = r - lg.width, t - int(H * 0.06) if H / W > 1.6 else t
        y = _clear_y(x, x + lg.width, max(int(H * 0.04), y), lg.height, avoid, H)
        ov.alpha_composite(lg, (x, y))
        return ov, (x, y, x + lg.width, y + lg.height)
    handle = brand.get("handle") or brand.get("name") or ""
    if not handle:
        return ov, (0, 0, 0, 0)
    size = max(16, int(H * 0.016))
    f = textfit.font("Inter-SemiBold.ttf", size)
    tw = int(f.getlength(handle))
    pw, ph = tw + size * 2, int(size * 2.1)
    x, y = r - pw, max(int(H * 0.04), t - int(H * 0.06) if H / W > 1.6 else t)
    y = _clear_y(x, x + pw, y, ph, avoid, H)
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((x, y, x + pw, y + ph), ph // 2, fill=(0, 0, 0, 120))
    a_, de = f.getmetrics()
    d.text((x + size, y + (ph - a_ - de) / 2), handle, font=f, fill=_rgb(brand.get("text") or "#FFFFFF") + (int(255 * opacity),))
    return ov, (x, y, x + pw, y + ph)


def hook_title(W: int, H: int, brand: dict, hook: str, accent_words=None) -> tuple[Image.Image, tuple]:
    """The hook over the opening seconds: brand headline font, the key word in the accent, a dark band behind it."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hook = (hook or "").strip()
    if not hook:
        return ov, (0, 0, 0, 0)
    l, t, r, b = _safe(W, H)
    head = brand.get("head_font") or "Anton.ttf"
    upper = head == "Anton.ttf"
    box_h = int((b - t) * (0.30 if H / W > 1.6 else 0.36))
    y0 = t + int(H * 0.01)
    fit = textfit.fit(hook, head, r - l, box_h, max_size=int(W * 0.11), min_size=int(W * 0.045), max_lines=3, upper=upper)
    band = Image.new("L", (W, H), 0)
    bh = fit.height + int(H * 0.10)
    ImageDraw.Draw(band).rectangle((0, max(0, y0 - int(H * 0.05)), W, y0 + bh), fill=170)
    band = band.filter(ImageFilter.GaussianBlur(int(H * 0.03)))
    dark = Image.new("RGBA", (W, H), _rgb(brand.get("bg") or "#0E0E10", (14, 14, 16)) + (255,))
    dark.putalpha(band)
    ov.alpha_composite(dark)
    d = ImageDraw.Draw(ov)
    words = set(w.lower().strip(".,!?:") for w in (accent_words or []))
    if not words:
        import re
        nums = [w for w in hook.split() if re.search(r"[$€£₹]|\d", w)]
        words = {(nums[0] if nums else max(hook.split(), key=len)).lower().strip(".,!?:")}
    ink = textfit.draw_fit(d, fit, (l, y0, r, y0 + box_h), fill=brand.get("text") or "#FFFFFF", align="left", valign="top",
                           accent_words=list(words), accent=brand.get("accent") or "#C9ACFA")
    bar_y = ink[3] + int(H * 0.012)
    d.rectangle((l, bar_y, l + int(W * 0.12), bar_y + max(5, H // 300)), fill=_rgb(brand.get("accent") or "#C9ACFA") + (255,))
    return ov, (l, y0, r, bar_y + max(5, H // 300))


def lower_third(W: int, H: int, brand: dict, text: str, above_y: int | None = None) -> tuple[Image.Image, tuple]:
    """A lower-third strap: an accent bar + the credit (or handle) on a dark plate, above the caption line."""
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    text = (text or "").strip()
    if not text:
        return ov, (0, 0, 0, 0)
    l, t, r, b = _safe(W, H)
    size = max(18, int(H * (0.019 if H / W > 1.6 else 0.026)))
    fit = textfit.fit(text, "Inter-SemiBold.ttf", int((r - l) * 0.8), size * 2, max_size=size, min_size=14, max_lines=1)
    f = textfit.font("Inter-SemiBold.ttf", fit.size)
    tw = int(f.getlength(fit.lines[0]))
    ph = int(fit.size * 2.2)
    bottom = min(b, (above_y - int(H * 0.02)) if above_y else b)
    y = max(t + int(H * 0.25), bottom - ph)
    x = l
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((x, y, x + tw + int(fit.size * 2.2), y + ph), radius=int(ph * 0.22), fill=(0, 0, 0, 170))
    d.rectangle((x, y, x + max(6, W // 160), y + ph), fill=_rgb(brand.get("accent") or "#C9ACFA") + (255,))
    a_, de = f.getmetrics()
    d.text((x + int(fit.size * 1.2), y + (ph - a_ - de) / 2), fit.lines[0], font=f, fill=_rgb(brand.get("text") or "#FFFFFF") + (255,))
    return ov, (x, y, x + tw + int(fit.size * 2.2), y + ph)


def layers(wd: str, W: int, H: int, brand: dict, *, hook: str = "", credit: str = "", D: float = 10.0, boxes: dict | None = None,
           cfg: dict | None = None, has_cover: bool = False, captions_center: int | None = None, accent_words=None,
           want: dict | None = None) -> tuple[list[tuple[str, float, float]], dict]:
    """[(png path, t0, t1)] for the timed overlays and {element: box} for the checks. ``want`` turns elements off
    ({"hook": False, "bug": False, "lower": False})."""
    boxes = boxes or {}
    cfg = cfg or {}
    want = {"hook": True, "bug": True, "lower": True, **(want or {})}
    out: list[tuple[str, float, float]] = []
    got: dict = {}
    if want["bug"] and "logo" not in boxes and cfg.get("chrome") != "bug":
        avoid = []
        for v in boxes.values():
            if isinstance(v, (list, tuple)) and len(v) == 4 and all(isinstance(x, (int, float)) for x in v):
                avoid.append(v)
            elif isinstance(v, dict):
                avoid += [x for x in v.values() if isinstance(x, (list, tuple)) and len(x) == 4]
        im, bx = bug(W, H, brand, avoid=avoid)
        if bx[2] > bx[0]:
            p = str(Path(wd) / "bp_bug.png")
            im.save(p)
            out.append((p, 0.0, D))
            got["bug"] = bx
    hook_on_screen = "hook" in boxes or cfg.get("layout") in ("huhbyte_news", "hype")
    if want["hook"] and hook and not has_cover and not hook_on_screen:
        im, bx = hook_title(W, H, brand, hook, accent_words)
        p = str(Path(wd) / "bp_hook.png")
        im.save(p)
        out.append((p, 0.0, min(D, HOOK_SECS)))
        got["hook"] = bx
    if want["lower"] and "credit" not in boxes and D > LOWER_IN + 1.0:
        text = credit if credit and credit.lower() not in ("own video", "your video") else (brand.get("handle") or "")
        if text:
            above = int(captions_center - H * 0.06) if captions_center else None
            im, bx = lower_third(W, H, brand, text, above)
            p = str(Path(wd) / "bp_lower.png")
            im.save(p)
            out.append((p, LOWER_IN, min(D - 0.3, LOWER_OUT)))
            got["lower"] = bx
    return out, got
