"""Images when a story has no usable media: generated art, or an on-brand typographic card that always works.

Providers, tried in order (each only when configured; nothing here is a hidden paid call):

  pollinations  gen.pollinations.ai/image/{prompt} with the operator's or user's ``sk_`` key (POLLINATIONS_KEY).
                The documented API needs a key (401 without one, checked 1 Nov 2026). The old anonymous
                image.pollinations.ai/prompt endpoint still answered 200 that day but is no longer in the docs, so it
                is opt-in only (HQ_POLLINATIONS_LEGACY=1) and treated as unreliable.
  hf            Hugging Face Inference Providers, FLUX.1-schnell (Apache-2.0 weights) with the user's HF token
                (HF_TOKEN). Free accounts get NO monthly credits (HF pricing page, 1 Nov 2026); PRO gets $2/month.
  openai        any OpenAI-compatible /v1/images/generations endpoint the user configured as their AI key.
  card          offline Pillow card in the brand (gradient + headline + logo). Always available, costs nothing.

Every call goes through netguard (host allowlist, no redirects followed, byte cap). Generated images are labelled
"AI-generated image" in the returned credit so captions can disclose it.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFilter

from .. import netguard
from . import textfit

HOSTS = {"gen.pollinations.ai", "image.pollinations.ai", "router.huggingface.co"}
SIZES = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080), "16:9": (1920, 1080)}


class ImageGenError(RuntimeError):
    pass


def _check_image(data: bytes) -> Image.Image:
    if len(data) < 2000:
        raise ImageGenError("The image service returned no image.")
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.width < 256 or im.height < 256:
        raise ImageGenError("The image service returned a tiny image.")
    return im.convert("RGB")


def pollinations(prompt: str, w: int, h: int, key: str | None = None, seed: int = 0, http=None) -> Image.Image:
    key = key or os.environ.get("POLLINATIONS_KEY")
    if key:
        url = (f"https://gen.pollinations.ai/image/{quote(prompt[:900])}?model=black-forest-labs/flux.1-schnell"
               f"&width={w // 16 * 16}&height={h // 16 * 16}&seed={seed}")
        r = netguard.safe_get(url, http=http, headers={"Authorization": f"Bearer {key}"}, limit=15_000_000, timeout=90,
                              https_only=True)
    elif os.environ.get("HQ_POLLINATIONS_LEGACY") == "1":
        url = f"https://image.pollinations.ai/prompt/{quote(prompt[:900])}?width={w}&height={h}&seed={seed}&nologo=true"
        r = netguard.safe_get(url, http=http, limit=15_000_000, timeout=90, https_only=True)
    else:
        raise ImageGenError("Pollinations needs a key (POLLINATIONS_KEY).")
    if r.status_code != 200:
        raise ImageGenError(f"Pollinations answered {r.status_code}.")
    return _check_image(r.content)


def huggingface(prompt: str, w: int, h: int, token: str | None = None, http=None) -> Image.Image:
    token = token or os.environ.get("HF_TOKEN")
    if not token:
        raise ImageGenError("Hugging Face needs a token (HF_TOKEN).")
    url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
    r = netguard.safe_post(url, http=http, json={"inputs": prompt[:900], "parameters": {"width": w // 16 * 16, "height": h // 16 * 16}},
                           headers={"Authorization": f"Bearer {token}", "Accept": "image/png"}, allowed_hosts=HOSTS,
                           limit=15_000_000, timeout=120)
    if r.status_code != 200:
        raise ImageGenError(f"Hugging Face answered {r.status_code}.")
    return _check_image(r.content)


def brand_card(headline: str, w: int, h: int, brand: dict | None = None, kicker: str = "") -> Image.Image:
    """Offline fallback: a dark gradient with a soft accent glow, the headline auto-fitted, optional logo."""
    b = {"accent": "#C9ACFA", "bg": "#0E0E10", "head_font": "Anton.ttf", "logo": None, **(brand or {})}
    acc = tuple(int(b["accent"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    bg = tuple(int(b["bg"].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    im = Image.new("RGB", (w, h), bg)
    glow = Image.new("RGB", (w, h), bg)
    gd = ImageDraw.Draw(glow)
    gd.ellipse((int(w * 0.15), int(h * 0.05), int(w * 1.1), int(h * 0.65)), fill=tuple(int(c * 0.55) for c in acc))
    im = Image.blend(im, glow.filter(ImageFilter.GaussianBlur(w // 6)), 0.6)
    d = ImageDraw.Draw(im)
    m = int(w * 0.08)
    if kicker:
        r = textfit.fit(kicker.upper(), "Inter-SemiBold.ttf", w - 2 * m, int(h * 0.04), max_size=40, min_size=18, max_lines=1)
        textfit.draw_fit(d, r, (m, int(h * 0.5) - int(h * 0.06), w - m, int(h * 0.5) - int(h * 0.02)), fill=b["accent"], align="left")
    r = textfit.fit(headline, b["head_font"], w - 2 * m, int(h * 0.36), max_size=150, min_size=36, max_lines=5, upper=True)
    textfit.draw_fit(d, r, (m, int(h * 0.5), w - m, int(h * 0.86)), fill="#FFFFFF", align="left", valign="top")
    if b.get("logo") and Path(b["logo"]).is_file():
        lg = Image.open(b["logo"]).convert("RGBA")
        lh = int(h * 0.04)
        lg = lg.resize((max(1, int(lg.width * lh / lg.height)), lh))
        im.paste(lg, (m, m), lg)
    return im


def generate_images(prompt: str, out_dir: str, *, n: int = 3, ratio: str = "9:16", headline: str = "",
                    brand: dict | None = None, providers: list[str] | None = None, http=None) -> list[dict]:
    """n images for the story. Returns [{path, provider, credit}] (never empty: the brand card is the last resort)."""
    w, h = SIZES.get(ratio, SIZES["9:16"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    order = providers or ["pollinations", "hf", "card"]
    res: list[dict] = []
    errors: list[str] = []
    for i in range(n):
        im, used = None, None
        for p in order:
            try:
                if p == "pollinations":
                    im = pollinations(f"{prompt}, cinematic product photo, no text, no watermark", w, h, seed=i, http=http)
                elif p == "hf":
                    im = huggingface(f"{prompt}, cinematic product photo, no text", w, h, http=http)
                elif p == "card":
                    im = brand_card(headline or prompt, w, h, brand, kicker="" if i == 0 else f"{i + 1}/{n}")
                used = p
                break
            except Exception as e:  # noqa: BLE001 - try the next provider
                errors.append(f"{p}: {str(e)[:80]}")
        if im is None:
            continue
        path = out / f"img_{i + 1}.jpg"
        im.save(path, quality=92)
        res.append({"path": str(path), "provider": used,
                    "credit": "Graphic: brand card" if used == "card" else "AI-generated image (FLUX.1-schnell)",
                    "errors": errors[-3:]})
    return res
