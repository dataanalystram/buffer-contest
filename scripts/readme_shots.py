"""README screenshots of the running app (sample workspace, never a real Buffer account).

    python scripts/readme_shots.py http://127.0.0.1:8899 docs/screenshots flow   # agent run, brand kit + ledger, approvals
    python scripts/planner_shots.py http://127.0.0.1:8899 docs/screenshots       # Planner List/Week/Month, light + dark
The agent run uses a photo you own (offline render) so the shot is repeatable; set QUERY=... to research a topic instead.
"""
import io
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
errors = []
kw = {}
px = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
if px:  # sandboxes: let Chromium load sample images through the proxy, but talk to the app directly
    u = urlparse(px)
    kw["proxy"] = {"server": f"{u.scheme}://{u.hostname}:{u.port}", "bypass": "127.0.0.1,localhost",
                   **({"username": u.username, "password": u.password} if u.username else {})}


def sample_photo(path: Path) -> None:
    """A plain photo-like image to stand in for 'your own photo' (gradient desk scene, no text)."""
    w, h = 1080, 1350
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(28 + 60 * t), int(36 + 70 * t), int(52 + 90 * t)))
    d.rounded_rectangle([140, 520, 940, 1020], 28, fill=(222, 226, 230))
    d.rounded_rectangle([180, 560, 900, 980], 18, fill=(30, 34, 40))
    d.ellipse([760, 220, 980, 440], fill=(255, 196, 92))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=90); path.write_bytes(buf.getvalue())


def shot(pg, name: str, max_h: int = 1700) -> None:
    """Full-page capture, cropped to max_h so a README image stays readable."""
    path = out / name
    pg.evaluate("document.getElementById('toast').hidden = true")
    pg.screenshot(path=str(path), full_page=True)
    im = Image.open(path)
    if im.height > max_h:
        im.crop((0, 0, im.width, max_h)).save(path)


with sync_playwright() as p:
    b = p.chromium.launch(**kw)
    ctx = b.new_context(viewport={"width": 1440, "height": 960}, color_scheme="light", timezone_id="Europe/Dublin", locale="en-IE")
    pg = ctx.new_page()
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(base + "/demo", wait_until="domcontentloaded")
    pg.wait_for_selector("[data-testid=agent-go]")
    # brand kit + a rule in the Style Ledger
    pg.click("[data-testid=tab-brand]")
    pg.wait_for_selector("[data-testid=kit-preview] img", timeout=30000)
    pg.fill("[data-testid=kit-name]", "Bright Desk"); pg.fill("[data-testid=kit-handle]", "@brightdesk")
    pg.click("[data-testid=kit-save]"); time.sleep(1.5)
    for rule in ("never say game-changer, max 2 hashtags", "logo top left and smaller"):
        pg.fill("[data-testid=rule-input]", rule); pg.click("[data-testid=rule-add]"); time.sleep(1.2)
    pg.wait_for_selector("[data-testid=rule-row]")
    pg.evaluate("window.scrollTo(0, 0)"); time.sleep(1)
    shot(pg, "brand-ledger.png", 1500)
    # the agent: research a topic (QUERY) or edit your own photo
    pg.click("[data-testid=tab-agent]")
    q = os.environ.get("QUERY")
    if q:
        pg.fill("[data-testid=agent-query]", q); pg.select_option("[data-testid=agent-format]", "carousel")
    else:
        photo = Path("/tmp/hq-sample-photo.jpg"); sample_photo(photo)
        pg.set_input_files("[data-testid=agent-file]", str(photo))
        pg.fill("[data-testid=agent-query]", "Our new studio desk is ready for the morning show")
        pg.select_option("[data-testid=agent-format]", "photo")
    pg.click("[data-testid=agent-go]")
    pg.wait_for_selector("[data-testid=package]", timeout=100000)
    time.sleep(2)
    pg.evaluate("window.scrollTo(0, 0)")
    shot(pg, "agent-run.png", 1500)
    pg.locator("[data-testid=packages]").screenshot(path=str(out / "agent-packages.png"))
    # approvals: the sample agent post waiting for a human
    pg.click("[data-testid=tab-approvals]")
    pg.wait_for_selector("[data-testid=approval-item]")
    time.sleep(1)
    pg.screenshot(path=str(out / "approvals.png"))
    b.close()
print("errors:", errors or "none")
sys.exit(1 if errors else 0)
