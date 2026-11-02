"""Build the demo's sample brands: extract each from its public homepage with the app's own extractor (studio/brand_extract.py,
no AI) and cache the kit, the logo and the renders, so the sample workspace's "Try it" flow is instant and offline.

    python scripts/build_sample_brands.py            # fetch the sites, extract, render
    python scripts/build_sample_brands.py --offline  # re-render from the cached kits and logos (no network)

Writes studio/sample_brands/<id>.json and studio/static/brands/<id>/{logo,mark}.png + the stills and the story carousel.
Every render carries "Sample brand, not affiliated". The logos are the companies' public marks as their own sites serve
them, used only to show the extractor working on a real site.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from studio import brand_extract, brand_onboard, netguard, templates  # noqa: E402

SITES = {
    "linear": {"url": "https://linear.app/", "hosts": ["linear.app"], "hashtags": ["#ProductUpdate", "#Linear"],
               "about": "Dark, precise, indigo. Read from linear.app."},
    "notion": {"url": "https://www.notion.com/", "hosts": ["notion.com", "notion.so"], "hashtags": ["#ProductUpdate", "#Notion"],
               "about": "Light, calm, ink and blue. Read from notion.com."},
}
UA = brand_onboard.UA


def fetch(url: str, limit: int) -> bytes | None:
    try:
        with netguard.safe_stream(url, headers=UA, timeout=15) as r:
            if r.status_code >= 400:
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    return None
            return bytes(buf)
    except Exception:
        return None


def extract_live(bid: str) -> dict:
    site = SITES[bid]
    r = netguard.safe_get(site["url"], headers=UA, limit=brand_extract.MAX_HTML_BYTES, timeout=20)
    final, html = str(r.url), r.text
    sheets = brand_extract.stylesheet_urls(final, html)
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        css = [d.decode("utf-8", "replace") for d in ex.map(lambda u: fetch(u, brand_extract.MAX_CSS_BYTES), sheets) if d]
    got = brand_extract.extract(final, html, css, fetch=fetch)
    got["kit"]["website"] = final
    got["stylesheets_read"] = len(css)
    return got


def render(bid: str, rec: dict) -> dict:
    out_dir = ROOT / "studio" / "static" / "brands" / bid
    logo = (out_dir / "logo.png").read_bytes() if (out_dir / "logo.png").is_file() else None
    kit = {**rec["kit"], "label": brand_onboard.SAMPLE_LABEL}
    b = brand_onboard.brand_for(kit, logo)
    names = {}
    for (ratio, label, im), key in zip(brand_onboard.theme_stills(b), ("reel_cover", "cover", "square")):
        fn = f"still-{ratio.replace(':', 'x')}.jpg"
        (out_dir / fn).write_bytes(templates.jpeg(im, 86))
        names[key] = fn
    car = []
    for i, im in enumerate(brand_onboard.story_carousel(b), 1):
        fn = f"carousel-{i}.jpg"
        (out_dir / fn).write_bytes(templates.jpeg(im, 86))
        car.append(fn)
    return {"renders": names, "carousel": car}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    for bid in [b for b in SITES if not args.only or b == args.only]:
        out_dir = ROOT / "studio" / "static" / "brands" / bid
        out_dir.mkdir(parents=True, exist_ok=True)
        jp = ROOT / "studio" / "sample_brands" / f"{bid}.json"
        jp.parent.mkdir(parents=True, exist_ok=True)
        if args.offline:
            rec = json.loads(jp.read_text())
        else:
            got = extract_live(bid)
            if got.get("logo_png"):
                (out_dir / "logo.png").write_bytes(got["logo_png"])
            if got.get("mark_png"):
                (out_dir / "mark.png").write_bytes(got["mark_png"])
            kit = {**got["kit"], "hashtags": SITES[bid]["hashtags"], "label": brand_onboard.SAMPLE_LABEL}
            rec = {"id": bid, "title": kit["name"], "about": SITES[bid]["about"], "hosts": SITES[bid]["hosts"],
                   "source_url": SITES[bid]["url"], "kit": kit, "provenance": got["provenance"], "fonts": got["fonts"],
                   "found": {**got["found"], "stylesheets": got["stylesheets_read"]}, "label": brand_onboard.SAMPLE_LABEL,
                   "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                   "extracted_by": "studio/brand_extract.py (no AI), scripts/build_sample_brands.py"}
        rec.update(render(bid, rec))
        jp.write_text(json.dumps(rec, indent=1, ensure_ascii=False) + "\n")
        print(bid, rec["kit"]["colors"], rec["kit"]["font"], rec["found"])


if __name__ == "__main__":
    main()
