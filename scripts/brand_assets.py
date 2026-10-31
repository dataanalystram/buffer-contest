"""Build the HuhByte AI brand assets for HuhQueue from the source logo (dataanalystram/Huhbyte.ai, code/brand/logo.png,
copied to studio/static/brand/src/huhbyte-logo.png). The logo is drawn for dark paper (a white wordmark), so:
- the H mark is set on its own dark rounded tile (crisp on light and dark paper): header, favicon, touch icon;
- the wordmark gets a light-paper variant (white letters -> ink #1a1c1e, purple -> #6c4ac8) and keeps the original for dark.
Run: python scripts/brand_assets.py"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
B = ROOT / "studio" / "static" / "brand"


def tile(mark: Image.Image, s: int, pad: float = 0.14, bg=(18, 19, 20), radius: float = 0.22) -> Image.Image:
    c = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    m = Image.new("L", (s * 4, s * 4), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, s * 4 - 1, s * 4 - 1), radius=int(s * 4 * radius), fill=255)
    t = Image.new("RGBA", (s, s), bg + (255,)); t.putalpha(m.resize((s, s), Image.LANCZOS)); c.alpha_composite(t)
    inner = int(s * (1 - 2 * pad)); r = min(inner / mark.width, inner / mark.height)
    mk = mark.resize((max(1, round(mark.width * r)), max(1, round(mark.height * r))), Image.LANCZOS)
    c.alpha_composite(mk, ((s - mk.width) // 2, (s - mk.height) // 2))
    return c


def main() -> None:
    im = Image.open(B / "src" / "huhbyte-logo.png").convert("RGBA")
    W, H = im.size
    a = im.split()[3]
    gap = next(x for x in range(200, W) if a.crop((x, 0, x + 1, H)).getextrema()[1] < 10)
    mark = im.crop((0, 0, gap, H)); mark = mark.crop(mark.getbbox())
    word = im.crop((gap + 30, 0, W, H)); word = word.crop(word.getbbox())
    for s, name in ((32, "favicon-32"), (64, "hb-mark-64"), (96, "hb-mark-96"), (180, "apple-touch-icon"), (192, "hb-mark-192"), (512, "hb-mark-512")):
        tile(mark, s).save(B / f"{name}.png", optimize=True)
    tile(mark, 64, pad=0.12).save(ROOT / "studio" / "static" / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    light = word.copy(); px = light.load()
    for y in range(light.height):
        for x in range(light.width):
            r, g, b, al = px[x, y]
            if al and max(r, g, b) - min(r, g, b) < 25 and r > 150:
                px[x, y] = (26, 28, 30, al)
            elif al and b > r:
                px[x, y] = (108, 74, 200, al)
    h = 64
    for n, img in (("dark", word), ("light", light)):
        img.resize((round(img.width * h / img.height), h), Image.LANCZOS).save(B / f"hb-wordmark-{n}.png", optimize=True)
    print("brand assets written to", B)


if __name__ == "__main__":
    main()
