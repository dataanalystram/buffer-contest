"""The text product card (Commerce, docs/COMMERCE.md): name, price, CTA and link label on the brand kit, no catalogue
image (Channel3 images aren't licensed for social posts). Drawn onto the One take overlay layer (take.render_clip) on the
video worker, so it needs only Pillow and studio.render.
"""
from __future__ import annotations

from typing import Any

FIELDS = {"name": 80, "price": 24, "cta": 40, "link_label": 40}
POSITIONS = ("bottom", "top")


class CardError(ValueError):
    pass


def clean(card: Any) -> dict | None:
    """Validate a product_card from adjust_clip: None / {} removes it; wrong types raise CardError (answered 400)."""
    if card is None or card == {}:
        return None
    if not isinstance(card, dict):
        raise CardError("product_card is an object {name, price, cta, link_label, position, code} or null.")
    out: dict[str, Any] = {}
    for k, cap in FIELDS.items():
        v = card.get(k, "")
        if v is None:
            v = ""
        if not isinstance(v, str):
            raise CardError(f"product_card.{k} must be text.")
        v = " ".join(v.split())
        if len(v) > cap:
            raise CardError(f"product_card.{k} is too long (at most {cap} characters).")
        out[k] = v
    if not out["name"]:
        raise CardError("product_card.name is required.")
    pos = card.get("position", "bottom")
    if pos not in POSITIONS:
        raise CardError("product_card.position is bottom or top.")
    out["position"] = pos
    code = card.get("code", "")
    if code and (not isinstance(code, str) or not code.replace("-", "").replace("_", "").isalnum() or len(code) > 32):
        raise CardError("product_card.code is the code attach_product_to_post returned.")
    out["code"] = code or ""
    return out


def draw_overlay(ov, card: dict, p: dict, kit: dict, avoid: tuple[int, int] | None = None) -> None:
    """Draw the card on the RGBA overlay ``ov`` (the clip's full frame): a rounded panel in the brand's primary colour
    near the bottom (or top), the name and price in the text colour, the CTA as an accent pill, the link label small.
    A small "Ad" marker sits in the panel's corner when the card carries a shop link. ``avoid`` is the (top, bottom)
    pixel band of the burned-in captions: the "bottom" card sits just above it, so it never hides what was said."""
    from PIL import ImageDraw

    from . import render
    W, H = ov.size
    colors = p.get("colors") or {}
    prim = render.hex_to_rgb(colors.get("primary") or "#18181B")
    acc = render.hex_to_rgb(colors.get("accent") or "#C9ACFA")
    txt = render.hex_to_rgb(colors.get("text") or "#FFFFFF")
    fname = render.FONTS.get(p.get("font"), "Inter-Bold.ttf")
    m = int(p.get("safe_margin", 64))
    pw = W - 2 * m
    f_name, f_price, f_small = render.font(fname, max(22, W // 24)), render.font(fname, max(26, W // 20)), render.font(fname, max(16, W // 40))
    d = ImageDraw.Draw(ov)
    name = render.drawable(card.get("name") or "", f_name)
    while name and d.textlength(name, font=f_name) > pw - 48:
        name = name[:-2].rstrip() + "…" if len(name) > 2 else ""
    price = render.drawable(card.get("price") or "", f_price)
    cta = render.drawable(card.get("cta") or "", f_small)
    label = render.drawable(card.get("link_label") or "", f_small)
    ph = int(H * 0.15)
    if card.get("position") == "top":
        y0 = int(H * 0.08)
    else:
        y0 = (int(avoid[0]) - 24 - ph) if avoid else int(H * 0.54) - ph
        y0 = max(int(H * 0.08) + ph // 2, y0)
    d.rounded_rectangle((m, y0, m + pw, y0 + ph), radius=max(16, W // 40), fill=prim + (232,))
    pad = 24
    d.text((m + pad, y0 + pad), name, font=f_name, fill=txt + (255,))
    ny = y0 + pad + int(f_name.size * 1.35)
    if price:
        d.text((m + pad, ny), price, font=f_price, fill=txt + (255,))
    if cta:
        tw = int(d.textlength(cta, font=f_small)) + 32
        cx1, cy1 = m + pw - pad, y0 + ph - pad
        d.rounded_rectangle((cx1 - tw, cy1 - int(f_small.size * 1.9), cx1, cy1), radius=999, fill=acc + (255,))
        d.text((cx1 - tw + 16, cy1 - int(f_small.size * 1.55)), cta, font=f_small, fill=prim + (255,))
    if label:
        d.text((m + pad, y0 + ph - pad - f_small.size), label, font=f_small, fill=txt + (210,))
    if card.get("code"):
        d.text((m + pw - pad - int(d.textlength("Ad", font=f_small)), y0 + pad // 2), "Ad", font=f_small, fill=txt + (200,))
