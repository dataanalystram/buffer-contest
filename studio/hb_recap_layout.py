"""The HuhByte ``weekly_recap`` template as a recap look (studio/media/recap.py RecapLayout).

The video recap (recap.compose) asks a layout for its cover, story overlays, title plates and closing card. This one
draws the cover and the closing card with the weekly_recap template from studio/hb_templates.py ("Do you realize what
just happened in AI in the last 7 days?" editorial cover, the SAVE THIS card), so the carousel and the Reel match the
still template in Brand -> Templates. Story overlays and plates keep the base layout (media panel, counter, credit,
the 4:5 summary + "Reported by" footer). Registered as ``huhbyte_weekly_recap``, which recap.layout_for prefers.
"""
from __future__ import annotations

from PIL import Image

from .media import recap as _recap

NAME = "huhbyte_weekly_recap"


def _brand(brand: dict):
    """recap's brand dict (compose.DEFAULT_BRAND keys) -> the templates' Brand."""
    from . import templates as T
    logo = brand.get("logo")
    kit = {"name": brand.get("name") or "", "handle": brand.get("handle") or "",
           "colors": {"primary": brand.get("bg"), "accent": brand.get("accent"), "text": brand.get("text")},
           "font": "anton", "end_card": {"line": brand.get("end_line") or ""}}
    return T.Brand(kit, logo if isinstance(logo, (bytes, bytearray)) else None)


def _ratio(W: int, H: int) -> str:
    return "9:16" if H / W > 1.6 else "1:1" if abs(H - W) < 4 else "4:5"


def _save(im: Image.Image, path: str, W: int, H: int) -> None:
    if im.size != (W, H):
        im = im.resize((W, H), Image.LANCZOS)
    im.convert("RGB").save(path)


class HuhByteWeeklyRecap(_recap.RecapLayout):
    name = NAME

    def cover(self, path: str, W: int, H: int, brand: dict, text: str, still) -> dict:
        from datetime import datetime, timezone

        from . import templates  # noqa: F401  (hb_templates must load after templates: it builds on it)
        from . import hb_templates as hb
        b = _brand(brand)
        title = (text or "").strip() or hb.RECAP_HEADLINE
        sub = hb._range({}, datetime.now(timezone.utc), 7)
        im = hb.editorial_cover(b, _ratio(W, H), title, hero=still, accent=hb.RECAP_ACCENT, sub=sub, label="WEEKLY RECAP")
        _save(im, path, W, H)
        return {"cover": (0, 0, W, H), "template": "weekly_recap"}

    def follow(self, path: str, W: int, H: int, brand: dict) -> dict:
        from . import templates  # noqa: F401  (hb_templates must load after templates: it builds on it)
        from . import hb_templates as hb
        _save(hb.save_card(_brand(brand), _ratio(W, H)), path, W, H)
        return {"follow": (0, 0, W, H), "template": "weekly_recap"}


def register() -> None:
    if hasattr(_recap, "register_layout"):
        _recap.register_layout(NAME, HuhByteWeeklyRecap())
