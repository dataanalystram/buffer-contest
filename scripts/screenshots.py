"""Render the UI with demo data and save screenshots.

    HUHQ_DATA_DIR=/tmp/demo HUHQ_ENABLE_FAKE=1 python scripts/demo_seed.py
    HUHQ_DATA_DIR=/tmp/demo HUHQ_ENABLE_FAKE=1 THEME=dark python scripts/screenshots.py OUT_DIR [pages]

THEME=light|dark (default light) - files are saved as <page>-<theme>.png.
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from huhqueue.api.app import create_app, create_edge_app  # noqa: E402
from huhqueue.core import Core  # noqa: E402

PASSWORD = "demo-password-123"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def serve(core, port, edge_port):
    core.settings.port, core.settings.public_port = port, edge_port
    app, edge = create_app(core, start_worker=False), create_edge_app(core)
    for a, p in ((app, port), (edge, edge_port)):
        srv = uvicorn.Server(uvicorn.Config(a, host="127.0.0.1", port=p, log_level="warning"))
        threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            return
        except OSError:
            time.sleep(0.1)


def main(out: str, pages: list[str] | None = None):
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    core = Core()
    from huhqueue.security import Auth
    a = Auth(core)
    if not a.has_password():
        a.set_password(PASSWORD)
    port, edge = free_port(), free_port()
    serve(core, port, edge)
    base = f"http://localhost:{port}"
    shots = pages or ["home", "composer", "connect"]
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
        pg = b.new_page(viewport={"width": 1500, "height": 960}, device_scale_factor=1)
        theme = os.environ.get("THEME", "light")
        pg.goto(base + f"/?theme={theme}")
        pg.get_by_test_id("login-password").fill(PASSWORD)
        pg.get_by_test_id("login-submit").click()
        pg.get_by_test_id("page-home").wait_for()
        plan = {
            "home": ("#/home", None),
            "composer": ("#/home?compose=new&channels=all", "composer"),
            "connect": ("#/channels", "page-channels"),
            "connect-instagram": ("#/channels?network=instagram", "connect-explainer"),
            "queue": ("#/queue", "page-queue"),
            "calendar": ("#/calendar?view=week", "page-calendar"),
            "ideas": ("#/ideas", "page-ideas"),
            "community": ("#/community", "page-community"),
            "insights": ("#/insights", "page-insights"),
            "settings": ("#/settings", "page-settings"),
            "channel": (None, "page-channeldetail"),
        }
        for name in shots:
            url, tid = plan[name]
            if name == "channel":
                cid = pg.evaluate("fetch('/api/channels').then(r=>r.json()).then(x=>x[0].id)")
                url = f"#/channel/{cid}"
            pg.goto(base + f"/?theme={theme}" + url)
            if tid:
                pg.get_by_test_id(tid).wait_for()
            if name == "composer":
                pg.get_by_test_id("composer-caption").fill("🚀 GPT-6 preview just dropped - here's what changed for builders.\n\n#AI #OpenAI #TechNews #HuhByte")
                pg.evaluate("""async () => { const m = await (await fetch('/api/media')).json();
                    window.__m = m.filter(x => x.kind==='image' && !x.filename.startsWith('avatar')).slice(0,1) }""")
            pg.wait_for_timeout(900)
            pg.screenshot(path=str(out_dir / f"{name}-{theme}.png"), full_page=False)
            print("saved", out_dir / f"{name}-{theme}.png")
        b.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "screenshots", sys.argv[2].split(",") if len(sys.argv) > 2 else None)
