"""Screenshots of the Planner (List, Week, Month) in light and dark mode from the sample workspace.

    python scripts/planner_shots.py http://127.0.0.1:8899 docs/screenshots
Uses $HTTPS_PROXY for Chromium if set (so sample images load in sandboxes); never touches a real Buffer account.
"""
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
prefix = sys.argv[3] if len(sys.argv) > 3 else "planner"
out.mkdir(parents=True, exist_ok=True)
errors = []
kw = {}
px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
if px and "127.0.0.1" not in base and "localhost" not in base:
    u = urlparse(px)
    kw["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}", **({"username": u.username, "password": u.password} if u.username else {})}
elif px:
    u = urlparse(px)
    kw["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}", "bypass": "127.0.0.1,localhost",
                   **({"username": u.username, "password": u.password} if u.username else {})}
with sync_playwright() as p:
    b = p.chromium.launch(**kw)
    for scheme in ("light", "dark"):
        ctx = b.new_context(viewport={"width": 1440, "height": 960}, device_scale_factor=1, color_scheme=scheme,
                            timezone_id="Europe/Dublin", locale="en-IE")
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "/demo", wait_until="domcontentloaded")
        pg.wait_for_selector("[data-testid=tab-planner]")
        pg.click("[data-testid=tab-planner]")
        pg.wait_for_selector("[data-testid=planner-main] [data-testid=planner-week], [data-testid=planner-main] [data-testid=planner-list], [data-testid=planner-main] [data-testid=planner-month]")
        if not pg.get_by_test_id("planner-nodate-toggle").is_checked():
            pg.get_by_test_id("planner-nodate-toggle").check(force=True)
        for view in ("list", "week", "month"):
            pg.click(f"[data-testid=planner-view-{view}]")
            pg.wait_for_selector(f"[data-testid=planner-{view}], [data-testid=planner-empty]")
            time.sleep(1.2)
            pg.evaluate("document.getElementById('toast').hidden = true")
            pg.screenshot(path=str(out / f"{prefix}-{view}-{scheme}.png"))
        ctx.close()
    b.close()
print("errors:", errors or "none")
sys.exit(1 if errors else 0)
