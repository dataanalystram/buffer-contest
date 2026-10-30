"""README screenshots of Never Bounced preflight in the sample workspace (/demo), light and dark.

    python scripts/preflight_shots.py http://127.0.0.1:8899 docs/screenshots [article-url]
Writes preflight-light.png / preflight-dark.png (composer: per-channel chips, findings, Fix it) and
preflight-fixed-light.png (after Fix it). Nothing reaches Buffer: /demo is the sample workspace.
"""
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
article = sys.argv[3] if len(sys.argv) > 3 else "https://en.wikipedia.org/wiki/Large_language_model"
out.mkdir(parents=True, exist_ok=True)
kw = {}
px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
if px:
    u = urlparse(px)
    kw["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}", "bypass": "127.0.0.1,localhost",
                   **({"username": u.username, "password": u.password} if u.username else {})}
LONG = ("Agents can draft your posts. Humans approve them. That is the whole policy, and it is why nothing goes out at "
        "9 am that you did not see first. Here is how the preflight check catches a post Buffer would reject, before "
        "it is scheduled: it counts characters the way Buffer counts them, checks media against each network, and fixes "
        "what it can.")
TALL = "https://picsum.photos/seed/huhqueue-tall/900/2000.jpg"
errors = []


def snap(pg, name: str) -> None:
    path = out / name
    pg.screenshot(path=str(path))
    Image.open(path).crop((0, 0, 1440, 1280)).save(path, optimize=True)


def run(scheme: str, fixed_shot: bool) -> None:
    ctx = b.new_context(viewport={"width": 1440, "height": 1640}, color_scheme=scheme, timezone_id="Europe/Dublin", locale="en-IE")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base + "/demo", wait_until="domcontentloaded")
    pg.wait_for_selector("[data-testid=agent-go]")
    pg.click("[data-testid=tab-studio]")
    pg.wait_for_selector("[data-testid=channel-chip]")
    pg.fill("[data-testid=url-input]", article)
    pg.click("[data-testid=draft-btn]")
    pg.wait_for_selector("[data-testid=draft-card]", timeout=60000)
    x = pg.locator("[data-testid=draft-card][data-service=twitter]")
    x.get_by_test_id("draft-text").fill(LONG)
    ig = pg.locator("[data-testid=draft-card][data-service=instagram]")
    ig.get_by_test_id("draft-media").fill(TALL)
    x.locator("[data-testid=preflight-chip][data-status=error]").wait_for(timeout=30000)
    ig.locator("[data-testid=preflight-chip][data-status=error]").wait_for(timeout=60000)
    time.sleep(1.2)
    pg.evaluate("document.getElementById('toast').hidden = true")
    pg.evaluate("window.scrollTo(0, document.getElementById('pfStrip').getBoundingClientRect().top + window.scrollY - 330)")
    snap(pg, f"preflight-{scheme}.png")
    if fixed_shot:
        x.get_by_test_id("preflight-fix").click()
        x.locator("[data-testid=preflight-chip][data-status=ok]").wait_for(timeout=30000)
        ig.get_by_test_id("preflight-fix").click()
        ig.locator("[data-testid=preflight-chip][data-status=ok]").wait_for(timeout=60000)
        time.sleep(1.2)
        pg.evaluate("document.getElementById('toast').hidden = true")
        pg.evaluate("window.scrollTo(0, document.getElementById('pfStrip').getBoundingClientRect().top + window.scrollY - 330)")
        snap(pg, f"preflight-fixed-{scheme}.png")
    ctx.close()


with sync_playwright() as p:
    b = p.chromium.launch(**kw)
    run("light", True)
    run("dark", False)
    b.close()
print("browser errors:", errors or "none")
sys.exit(1 if errors else 0)
