"""Two example brands for the sample workspace, switchable from the Brand studio screen (UI = REST = MCP).

* HuhByte AI: the owner's AI-news brand. Logo from github.com/dataanalystram/Huhbyte.ai (code/brand/logo.png), black
  ground, lavender #C9ACFA accent, Anton headlines, Poppins body. Channels: Instagram, YouTube Shorts, X, LinkedIn.
* Buffer (concept example, not affiliated): Buffer's public brand as buffer.com serves it today. The layered "stack"
  mark is buffer.com/img/logos/buffer-logo.png; the palette is the site's own CSS tokens (--color-brand-dark
  hsl(176 20% 16%) = #213130, --color-brand-primary hsl(105 68% 77%) = #B0EC9C, coral hsl(7 100% 83%) = #FFB2A8,
  neutral-050 = #FEFDFB); the type is Figtree, which buffer.com loads (SIL OFL 1.1). Every Buffer render carries the
  line "Concept example, not affiliated with Buffer". Trademarks belong to their owners.
"""
from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DIR = Path(__file__).parent / "brand_examples"
FONT_DIR = Path(__file__).parent / "fonts"

EXAMPLES = {
    "huhbyte": {
        "id": "huhbyte", "title": "HuhByte AI", "about": "AI news, explained simply. Dark, loud, lavender.",
        "kit": {"name": "HuhByte AI", "handle": "huhbyte.ai", "website": "https://www.instagram.com/huhbyte.ai/",
                "colors": {"primary": "#000000", "accent": "#C9ACFA", "text": "#FFFFFF", "secondary": "#6B4FD8"},
                "font": "anton", "body_font": "poppins", "headline_case": "upper",
                "front_card": {"style": "glow", "kicker": "AI news"},
                "end_card": {"on": True, "style": "glow", "cta": "Follow for daily AI", "line": "AI news, explained simply."},
                "voice": "Plain, punchy, no hype. Short sentences. Explain why it matters to a normal person.",
                "hashtags": ["#AI", "#AInews", "#HuhByte"], "label": ""},
        "channels": ["instagram", "youtube", "twitter", "linkedin"],
        "logo": "huhbyte/logo.png",
        "source": "https://github.com/dataanalystram/Huhbyte.ai/blob/main/code/brand/logo.png",
    },
    "buffer": {
        "id": "buffer", "title": "Buffer (concept example)", "about": "Buffer's public brand, made consistent on every channel. Not affiliated.",
        "kit": {"name": "Buffer", "handle": "buffer", "website": "https://buffer.com",
                "colors": {"primary": "#213130", "accent": "#B0EC9C", "text": "#FEFDFB", "secondary": "#FFB2A8"},
                "font": "figtree", "body_font": "figtree", "headline_case": "title",
                "front_card": {"style": "minimal", "kicker": "This week"},
                "end_card": {"on": True, "style": "bold", "cta": "Build your audience", "line": "Plan, publish and grow with Buffer."},
                "voice": "Warm, helpful, plain words. Teach one thing. No jargon, no hype.",
                "hashtags": ["#SocialMedia", "#CreatorTips"], "label": "Concept example, not affiliated with Buffer"},
        "channels": ["instagram", "linkedin", "twitter", "threads", "bluesky", "facebook", "tiktok", "youtube"],
        "logo": "buffer/mark.png",
        "source": "https://buffer.com/img/logos/buffer-logo.png (mark) + buffer.com CSS tokens (palette)",
    },
}


@lru_cache(maxsize=4)
def logo_bytes(example_id: str) -> bytes:
    ex = EXAMPLES[example_id]
    raw = (DIR / ex["logo"]).read_bytes()
    if example_id != "buffer":
        return raw
    # Buffer: the stack mark + the wordmark set in Figtree, in Buffer's dark green (templates recolour it on dark cards)
    mark = Image.open(io.BytesIO(raw)).convert("RGBA")
    mark = mark.crop(mark.getchannel("A").getbbox())
    h = 300
    mark = mark.resize((int(mark.width * h / mark.height), h), Image.LANCZOS)
    f = ImageFont.truetype(str(FONT_DIR / "Figtree-ExtraBold.ttf"), int(h * 0.92))
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(d0.textlength("Buffer", font=f))
    bb = d0.textbbox((0, 0), "Buffer", font=f)
    out = Image.new("RGBA", (mark.width + int(h * 0.28) + tw, h), (0, 0, 0, 0))
    out.paste(mark, (0, 0), mark)
    d = ImageDraw.Draw(out)
    d.text((mark.width + int(h * 0.28), (h - (bb[3] - bb[1])) / 2 - bb[1]), "Buffer", font=f, fill=(33, 49, 48, 255))
    buf = io.BytesIO()
    out.save(buf, "PNG")
    return buf.getvalue()


def listing() -> list[dict]:
    return [{"id": e["id"], "title": e["title"], "about": e["about"], "channels": e["channels"], "colors": e["kit"]["colors"],
             "font": e["kit"]["font"], "body_font": e["kit"]["body_font"], "label": e["kit"]["label"], "source": e["source"],
             "gallery": f"/static/examples/{e['id']}/index.json"} for e in EXAMPLES.values()]
