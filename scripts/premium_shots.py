"""Screenshots of the premium UX in the real app (sample workspace, never a real Buffer account).

    python scripts/premium_shots.py OUT_DIR [set ...]        sets: landing, app, planner, docs, legal, all (default all)
    BASE=https://huhqueue-studio.onrender.com python scripts/premium_shots.py OUT   (shoot a live server instead)

Starts the app in-process on a free port unless BASE is set. Light and dark, 1440 and 390 px. Also used for the docs
screenshots (python scripts/premium_shots.py studio/static/docs-shots docs)."""
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


def want(name: str) -> bool:
    return "all" in SETS or name in SETS


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
                    for tab in ("inbox", "agent", "take"):
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
                ctx.close()
        b.close()
    print("errors:", errors or "none")
    print("wrote", len(list(OUT.glob("*.png"))), "png in", OUT)


if __name__ == "__main__":
    main()
