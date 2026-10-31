"""Screenshots of the premium UX in the real app (sample workspace, never a real Buffer account).

    python scripts/premium_shots.py OUT_DIR [set ...]        sets: landing, app, planner, docs, legal, all (default all); land (the landing's Planner images, 1440 only)
    BASE=https://huhqueue-studio.onrender.com python scripts/premium_shots.py OUT   (shoot a live server instead)

Starts the app in-process on a free port unless BASE is set. Light and dark, 1440 and 390 px. Also used for the docs
screenshots (python scripts/premium_shots.py studio/static/docs-shots docs).
VIA_HTTPX=1 fetches every non-local request with httpx instead of the browser's network (for a sandbox whose proxy only
Python can use); the pages, cookies and images are the same."""
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "shots")
SETS = set(sys.argv[2:]) or {"all"}
OUT.mkdir(parents=True, exist_ok=True)
WIDTHS = [int(w) for w in os.environ.get("WIDTHS", "1440,390").split(",")]
SCHEMES = os.environ.get("SCHEMES", "light,dark").split(",")
VIEWS = ["week", "month", "day", "list", "board", "grid"]


def serve() -> str:
    base = os.environ.get("BASE")
    if base:
        return base.rstrip("/")
    os.environ.setdefault("STUDIO_INSECURE_COOKIES", "1")
    os.environ.setdefault("STUDIO_DB", tempfile.mktemp(suffix=".db"))
    os.environ.setdefault("HQ_ANON_RPM", "100000")
    os.environ.setdefault("HQ_DEMO_PER_MIN", "100000")
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    os.environ["PUBLIC_URL"] = f"http://127.0.0.1:{port}"
    import httpx
    import uvicorn

    from studio.app import create_app
    server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(120):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=2).status_code == 200:
                break
        except Exception:
            time.sleep(0.25)
    return f"http://127.0.0.1:{port}"


DOCSHOTS = [("composer", "agent"), ("brand", "brand"), ("radar", "radar"), ("inbox", "inbox"), ("planner-week", "planner"), ("take", "take"),
            ("connect", "connect"), ("activity", "activity")]


def docshots(pg, base: str, scheme: str, dev: str) -> None:
    from PIL import Image
    d = ROOT / "studio" / "static" / "docs-shots"; d.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())

    def save(key):
        pg.evaluate("document.getElementById('toast') && (document.getElementById('toast').hidden = true)")
        pg.screenshot(path=str(tmp / "s.png"))
        im = Image.open(tmp / "s.png").convert("RGB")
        if dev == "desktop":
            im = im.resize((1200, 750), Image.LANCZOS)
        im.save(d / f"{key}-{scheme}-{dev}.webp", "WEBP", quality=78, method=6)
    pg.goto(base + "/", wait_until="networkidle"); pg.wait_for_timeout(1500); save("landing")
    pg.goto(base + "/demo", wait_until="networkidle"); pg.wait_for_selector("#app:not([hidden])")
    pg.evaluate("localStorage.setItem('hq_tour_done','1'); window.tourEnd && document.querySelector('.tour') && tourEnd()")
    for key, tab in DOCSHOTS:
        if tab == "planner":
            pg.evaluate("plSetView('week')")
        else:
            pg.evaluate(f"showTab('{tab}')")
        pg.wait_for_timeout(1000); save(key)


def via_httpx(ctx) -> None:
    """Route non-local requests through httpx (VIA_HTTPX=1)."""
    import httpx
    client = httpx.Client(follow_redirects=False, timeout=30)

    def handle(route, request):
        if request.url.startswith(("http://127.0.0.1", "http://localhost", "data:", "blob:")):
            return route.continue_()
        try:
            h = {k: v for k, v in request.all_headers().items() if not k.startswith(":") and k.lower() not in ("host", "content-length", "accept-encoding")}
            r = client.request(request.method, request.url, headers=h, content=request.post_data_buffer,
                               follow_redirects=request.resource_type != "document")
            hdrs = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
            route.fulfill(status=r.status_code, headers=dict(hdrs), body=r.content)
            for k, v in hdrs:
                if k.lower() == "set-cookie":
                    ctx.add_cookies([_cookie(v, request.url)])
        except Exception:
            route.abort()
    ctx.route("**/*", handle)


def _cookie(header: str, url: str) -> dict:
    name, _, rest = header.partition("=")
    value = rest.split(";", 1)[0]
    return {"name": name.strip(), "value": value, "url": url.split("/", 3)[0] + "//" + url.split("/", 3)[2]}


def land_shots(pg, base: str, scheme: str) -> None:
    """studio/static/land/planner-<view>-<scheme>.webp: the Planner panel of the real sample, 1300x757."""
    from PIL import Image
    d = ROOT / "studio" / "static" / "land"
    tmp = Path(tempfile.mkdtemp())
    pg.goto(base + "/demo", wait_until="networkidle"); pg.wait_for_selector("#app:not([hidden])")
    pg.evaluate("localStorage.setItem('hq_tour_done','1'); document.querySelector('.tour') && window.tourEnd && tourEnd()")
    pg.evaluate("showTab('planner')")
    for v in ("week", "month", "list", "board", "grid"):
        pg.evaluate(f"plSetView('{v}')"); pg.wait_for_timeout(300)
        if v == "month":  # the sample's posts sit at the end of this month and through the next
            pg.evaluate("plShift(1)")
        pg.wait_for_timeout(1200)
        pg.evaluate("document.getElementById('toast') && (document.getElementById('toast').hidden = true)")
        box = pg.locator("#tab-planner").bounding_box()
        pad = 32
        w = box["width"] + 2 * pad
        pg.screenshot(path=str(tmp / "s.png"), clip={"x": box["x"] - pad, "y": box["y"] - pad, "width": w, "height": round(w * 757 / 1300)})
        if v == "month":
            pg.evaluate("plShift(-1)")
        Image.open(tmp / "s.png").convert("RGB").resize((1300, 757), Image.LANCZOS).save(
            d / f"planner-{v}-{scheme}.webp", "WEBP", quality=80, method=6)


def want(name: str) -> bool:
    return ("all" in SETS and name not in ("docshots", "og")) or name in SETS


def main() -> None:
    from playwright.sync_api import sync_playwright
    base = serve()
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        for scheme in SCHEMES:
            for w in WIDTHS:
                h = 900 if w > 600 else 844
                ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, timezone_id="Europe/Dublin", locale="en-IE",
                                    device_scale_factor=1, reduced_motion="no-preference")
                if os.environ.get("VIA_HTTPX"):
                    via_httpx(ctx)
                pg = ctx.new_page()
                pg.on("pageerror", lambda e: errors.append(f"{scheme} {w}: {e}"))
                tag = f"{w}-{scheme}"
                if want("landing"):
                    pg.goto(base + "/", wait_until="networkidle"); pg.wait_for_timeout(1600)
                    pg.screenshot(path=str(OUT / f"landing-{tag}.png"))
                    if os.environ.get("SCROLLS"):
                        total = pg.evaluate("document.documentElement.scrollHeight - innerHeight")
                        top = pg.evaluate("document.getElementById('chapter').offsetTop")
                        chh = pg.evaluate("document.getElementById('chapter').offsetHeight - innerHeight")
                        for pct in (25, 50, 75, 100):
                            pg.evaluate(f"scrollTo(0, {top} + {chh} * {pct} / 100)"); pg.wait_for_timeout(250)
                            pg.screenshot(path=str(OUT / f"landing-{tag}-chapter{pct}.png"))
                    pg.evaluate("scrollTo(0,0)"); pg.wait_for_timeout(150)
                    pg.screenshot(path=str(OUT / f"landing-{tag}-full.png"), full_page=True)
                if want("legal"):
                    for page in ("privacy", "terms", "security"):
                        pg.goto(base + "/" + page, wait_until="networkidle")
                        pg.screenshot(path=str(OUT / f"{page}-{tag}.png"), full_page=w > 600)
                if want("docs"):
                    pg.goto(base + "/docs", wait_until="networkidle")
                    pg.screenshot(path=str(OUT / f"docs-{tag}.png"))
                    pg.goto(base + "/docs/planner", wait_until="networkidle")
                    pg.screenshot(path=str(OUT / f"docs-planner-{tag}.png"))
                if want("app") or want("planner"):
                    pg.goto(base + "/demo", wait_until="networkidle")
                    pg.wait_for_selector("#app:not([hidden])")
                    pg.evaluate("localStorage.setItem('hq_tour_done','1'); document.querySelector('.tour') && window.tourEnd && tourEnd()")
                if want("app"):
                    for tab in ("inbox", "agent", "take", "connect"):
                        pg.evaluate(f"showTab('{tab}')"); pg.wait_for_timeout(900)
                        pg.evaluate("document.getElementById('toast').hidden = true")
                        pg.screenshot(path=str(OUT / f"{ {'agent': 'composer'}.get(tab, tab)}-{tag}.png"))
                    if os.environ.get("TAKE_RUN"):
                        pg.evaluate("showTab('take')"); pg.click("[data-testid=take-sample]")
                        pg.wait_for_selector("[data-testid=clip-grid] video, [data-testid=clip-grid] img", timeout=60000)
                        pg.wait_for_timeout(800); pg.screenshot(path=str(OUT / f"take-run-{tag}.png"))
                    pg.evaluate("openPalette()"); pg.wait_for_timeout(300)
                    pg.screenshot(path=str(OUT / f"palette-{tag}.png")); pg.keyboard.press("Escape")
                if want("planner"):
                    for v in VIEWS:
                        pg.evaluate(f"plSetView('{v}')"); pg.wait_for_timeout(900)
                        pg.evaluate("document.getElementById('toast').hidden = true")
                        pg.screenshot(path=str(OUT / f"planner-{v}-{tag}.png"))
                if "land" in SETS and w == 1440:
                    land_shots(pg, base, scheme)
                if want("docshots") and w in (1440, 390):
                    docshots(pg, base, scheme, "desktop" if w > 600 else "phone")
                ctx.close()
        if want("og"):
            ctx = b.new_context(viewport={"width": 1200, "height": 630}, color_scheme="light", reduced_motion="reduce")
            pg = ctx.new_page(); pg.goto(base + "/", wait_until="networkidle")
            pg.add_style_tag(content=".top nav,.aux,.connect-row,#configNote,.stats,.lede,.bead.end{display:none!important}.hero{min-height:630px;padding:56px 72px}.hero h1{font-size:112px}.wrap{padding:0 72px}")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(ROOT / "studio" / "static" / "brand" / "og.png")); ctx.close()
        b.close()
    print("errors:", errors or "none")
    print("wrote", len(list(OUT.glob("*.png"))), "png in", OUT)


if __name__ == "__main__":
    main()
