"""Profile the Brand > Kit landing on /demo: a local server + Chromium, time to the first preview on screen, time to every
theme card, the requests and bytes the builder fetched. Two visits per run (a fresh sample workspace each time).

    CHROMIUM_PATH=/usr/bin/chromium python scripts/profile_brand_kit.py [label] [--wait-warm]

--wait-warm waits for the start-up warm-up of the sample kits first (a warm instance). Prints one JSON line per visit.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "studio"))
os.chdir(ROOT)
os.environ.setdefault("HUHQ_PREVIEW_CACHE", tempfile.mkdtemp(prefix="hq-prof-"))

import httpx  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from browsers import free_port, serve  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import load_settings  # noqa: E402

LABEL = next((a for a in sys.argv[1:] if not a.startswith("--")), "run")
FIRST = ("() => { const i = document.querySelector('[data-testid=bk-card-carousel_cover] img');"
         " return !!(i && i.complete && i.naturalWidth > 0); }")
ALL = ("() => document.querySelectorAll('[data-testid=bk-themes] [data-card] img').length >= 21 &&"
       " [...document.querySelectorAll('[data-testid=bk-themes] img')].every(i => i.complete && i.naturalWidth > 0)")
TIMELINE = ("() => performance.getEntriesByType('resource').filter(e => e.name.includes('/api/brand')).map(e =>"
            " [e.name.replace(location.origin, ''), Math.round(e.startTime), Math.round(e.responseEnd), e.encodedBodySize])")


def until(page, fn: str, timeout: float = 120.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if page.evaluate(fn):
            return
        time.sleep(0.02)
    raise TimeoutError(fn)


def main() -> None:
    td, port = tempfile.mkdtemp(), free_port()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="s" * 64,
                      db_path=f"{td}/d.db", secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
    srv = serve(app, port)
    base = f"http://127.0.0.1:{port}"
    if "--wait-warm" in sys.argv:
        for t in threading.enumerate():
            if t.name == "brand-kit-warm":
                t.join()
    p = sync_playwright().start()
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    try:
        for n in (1, 2):
            ctx = b.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_init_script("localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')")
            page = ctx.new_page()
            t0 = time.time()
            page.goto(base + "/demo")
            until(page, FIRST)
            first = (time.time() - t0) * 1000
            until(page, ALL)
            every = (time.time() - t0) * 1000
            tl = page.evaluate(TIMELINE)
            prev = [r for r in tl if r[0].startswith("/api/brand-kits/preview") and not r[0].startswith("/api/brand-kits/preview-img")]
            imgs = [r for r in tl if r[0].startswith("/api/brand-kits/preview-img")]
            print(json.dumps({"label": LABEL, "visit": n, "first_preview_ms": round(first), "all_themes_ms": round(every),
                              "preview_calls": len(prev), "preview_json_kb": round(sum(r[3] for r in prev) / 1024, 1),
                              "image_requests": len(imgs), "image_kb": round(sum(r[3] for r in imgs) / 1024, 1),
                              "preview_call_ms": [r[2] - r[1] for r in prev]}))
            ctx.close()
    finally:
        b.close()
        p.stop()
        srv.should_exit = True


if __name__ == "__main__":
    main()
