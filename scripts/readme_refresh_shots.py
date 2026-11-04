"""README Screenshots section (4 Nov 2026): real shots of a local, seeded run of main. Never a real Buffer account.

    CHROMIUM_PATH=/usr/bin/chromium python scripts/readme_refresh_shots.py docs/screenshots   (from the repo root)

Seeds the made-up workspace from scripts/library_shots.py (acc_1, ram@example.com, a fake Buffer behind a mock
transport), stubs every outside fetch (news search, article reads, images), then captures:
library-grid-1280, library-bin-1280, library-grid-390, make-post-1280, chat-steps-1280, send-device-1280.
Each PNG is checked for sideways overflow and squeezed under 300 KB (palette PNG) if it comes out bigger. The send
code on /send is shown as ABCD-EFGH and its QR is drawn over, so no live code or link is ever in a picture."""
import io
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.getcwd())
import httpx  # noqa: E402
from _pytest.monkeypatch import MonkeyPatch  # noqa: E402
from PIL import Image  # noqa: E402

from studio import drafter, house, netguard, reader, sources  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import load_settings  # noqa: E402
from tests.studio import conftest  # noqa: E402
from tests.studio.browsers import free_port, serve  # noqa: E402
from tests.studio.conftest import FakeBuffer  # noqa: E402
from tests.studio.test_agent_core import OWNER_CHANNELS, Router  # noqa: E402
from tests.studio.test_house import ARTICLE, jpeg  # noqa: E402

sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from library_shots import seed  # noqa: E402

MAX_BYTES = 300 * 1024
STORY = {"title": "Acme launches an agent that books travel with one approval", "url": ARTICLE["url"], "source": "news.example.com",
         "summary": "The agent plans trips end to end and asks once before paying. An AI launch.", "published": int(time.time()) - 3600, "image": ""}


def stub_outside(mp):
    """No network: research, article reads and image fetches answer from fixtures (as tests/studio/test_chat_ui_e2e.py)."""
    mp.setattr(sources, "search", lambda q, feeds, http=None, limit=12, **kw: [
        {"title": ARTICLE["title"], "url": ARTICLE["url"], "source": "news.example.com", "published": 0, "via": "bing_news"}])
    mp.setattr(sources, "bing_news", lambda q, http=None, limit=10: [])
    mp.setattr(sources, "google_news", lambda q, http=None, limit=10: [])
    mp.setattr(drafter, "fetch_article", lambda url, http=None, check_host=True: dict(ARTICLE))
    mp.setattr(reader, "read_url", lambda url, http=None, x_bearer="": {**ARTICLE, "via": "direct", "post": None, "media": [], "media_note": ""})
    mp.setattr(reader, "read_article", lambda url, http=None: {**ARTICLE, "url": url})
    mp.setattr(netguard, "_host_ok", lambda host: True)
    mp.setattr(house.House, "_fetch_bytes", lambda self, url, limit: jpeg() if url else None)


def save(page, out, name, w):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    assert sw <= w + 1, f"{name}: horizontal overflow at {w}px ({sw})"
    page.wait_for_timeout(400)
    raw = page.screenshot()
    path = os.path.join(out, f"{name}.png")
    im = Image.open(io.BytesIO(raw))
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    for colors in (256, 192, 128, 96):
        if buf.tell() <= MAX_BYTES:
            break
        buf = io.BytesIO(); im.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
    assert buf.tell() <= MAX_BYTES, f"{name}: {buf.tell()} bytes"
    open(path, "wb").write(buf.getvalue())
    print(f"{name}.png {im.width}x{im.height} {round(buf.tell() / 1024)} KB")


def main(out):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    mp = MonkeyPatch()
    stub_outside(mp)
    conftest.CHANNELS = OWNER_CHANNELS
    port = free_port()
    td = tempfile.mkdtemp()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="r" * 64,
                      db_path=os.path.join(td, "rr.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True, render_worker="none")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(FakeBuffer()))))
    print("seeded", seed(app))  # user acc_1 + session sid-ls
    server = serve(app, port)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"])

            def page_at(w, h):
                ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme="light", device_scale_factor=1,
                                    timezone_id="Europe/Dublin", locale="en-IE")
                ctx.add_init_script("localStorage.setItem('hq_tour_done','1');localStorage.setItem('hq_onb_seen','1');localStorage.setItem('hq_mode','light')")
                ctx.add_cookies([{"name": "hq_sid", "value": "sid-ls", "url": base}])
                ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
                ctx.route(re.compile(r".*/api/radar.*"), lambda r: r.fulfill(json={"stories": [STORY]}))
                pg = ctx.new_page()
                pg.set_default_timeout(30000)
                return ctx, pg

            # Library grid + Bin at 1280, grid at 390
            ctx, pg = page_at(1280, 900)
            pg.goto(base + "/?tab=library", wait_until="domcontentloaded")
            pg.get_by_test_id("library-item").first.wait_for()
            pg.wait_for_timeout(900)
            save(pg, out, "library-grid-1280", 1280)
            pg.get_by_test_id("library-view-bin").click()
            pg.get_by_test_id("bin-item").first.wait_for()
            pg.wait_for_timeout(700)
            save(pg, out, "library-bin-1280", 1280)
            ctx.close()
            ctx, pg = page_at(390, 844)
            pg.goto(base + "/?tab=library", wait_until="domcontentloaded")
            pg.get_by_test_id("library-item").first.wait_for()
            pg.wait_for_timeout(900)
            save(pg, out, "library-grid-390", 390)
            ctx.close()

            # Make a post: Discover, then a chat thread with its steps open
            ctx, pg = page_at(1280, 900)
            pg.goto(base + "/?tab=agent", wait_until="domcontentloaded")
            pg.get_by_test_id("mp-discover").wait_for()
            pg.get_by_test_id("mp-disc-use").first.wait_for()
            pg.wait_for_timeout(600)
            save(pg, out, "make-post-1280", 1280)
            pg.get_by_test_id("mp-disc-use").first.click()
            q = pg.get_by_test_id("agent-query")
            if not q.input_value().strip():
                q.fill("Acme launches an agent that books travel with one approval")
            q.press("Enter")
            pg.get_by_test_id("package").first.wait_for(timeout=90000)
            pg.wait_for_function("() => !document.getElementById('runView').classList.contains('hc-busy')", timeout=90000)
            pg.wait_for_timeout(1200)
            if not pg.get_by_test_id("mp-trace").evaluate("d => d.open"):
                pg.get_by_test_id("mp-trace-toggle").click()
            pg.get_by_test_id("mp-trace").scroll_into_view_if_needed()
            pg.wait_for_timeout(600)
            save(pg, out, "chat-steps-1280", 1280)
            ctx.close()

            # Send from a device: the code page (code masked, QR covered)
            ctx, pg = page_at(1280, 900)
            pg.goto(base + "/send", wait_until="domcontentloaded")
            pg.get_by_test_id("send-qr").wait_for(state="visible")
            pg.evaluate("""() => { const c = document.querySelector('[data-testid=send-code]'); if (c) c.textContent = 'ABCD-EFGH';
                const q = document.querySelector('[data-testid=send-qr]'); if (q) { const r = q.getBoundingClientRect();
                const d = document.createElement('div'); Object.assign(d.style, {position: 'fixed', left: r.left + 'px', top: r.top + 'px',
                width: r.width + 'px', height: r.height + 'px', background: 'repeating-linear-gradient(45deg,#ddd 0 8px,#f4f4f4 8px 16px)',
                borderRadius: '8px', zIndex: 9999}); d.textContent = ''; document.body.appendChild(d); }
                document.querySelectorAll('a,span,p,code,input').forEach(e => { for (const n of e.childNodes) if (n.nodeType === 3)
                  n.textContent = n.textContent.replace(/\\/send\\/[A-Za-z0-9-]{6,}/g, '/send/ABCD-EFGH'); }); }""")
            save(pg, out, "send-device-1280", 1280)
            ctx.close()
            b.close()
    finally:
        server.should_exit = True
        mp.undo()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
