"""Library screenshots + load timing on a seeded workspace (Library premium, 3 Nov 2026).

python scripts/library_shots.py <out_dir> [prefix]   (run from the repo root; CHROMIUM_PATH=/usr/bin/chromium on ARM)
Seeds 30 items (uploads, Reels, carousels, images, some used in a draft / a scheduled post), then shoots the Library at
1280 and 390 in dark and light, and prints the time to the first card and the Library's JS/CSS weight."""
import io
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.getcwd())
import httpx  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from studio import house  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import load_settings  # noqa: E402
from tests.studio import conftest  # noqa: E402
from tests.studio.browsers import free_port, serve  # noqa: E402
from tests.studio.conftest import FakeBuffer  # noqa: E402
from tests.studio.test_agent_core import OWNER_CHANNELS, Router  # noqa: E402

A = house.Actor("acc_1")
PALETTE = [((58, 32, 110), (201, 172, 250)), ((20, 22, 30), (90, 110, 160)), ((120, 60, 140), (240, 200, 230)),
           ((15, 60, 70), (120, 200, 190)), ((90, 30, 40), (240, 150, 120)), ((30, 30, 32), (200, 200, 205))]
TITLES = ["Managed agents API for any model", "Click launches managed agents API", "Screen Studio 4.0: the Mac recorder that edits for you",
          "Vercel ships open-source CLI for agent desktops", "AI agents: what's new and what to watch", "Agent desktop cover",
          "OpenAI ships a smaller reasoning model", "Nvidia earnings beat, again", "Figma adds AI layout", "Apple's on-device model"]


def card(i, w=1080, h=1350, label=""):
    a, b = PALETTE[i % len(PALETTE)]
    im = Image.new("RGB", (w, h), a)
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=tuple(int(a[k] * (1 - t) + b[k] * t) for k in range(3)))
    d.ellipse([w * .45, h * .35, w * 1.3, h * 1.2], fill=tuple(min(255, c + 30) for c in b))
    if label:
        d.text((60, 80), label[:40], fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    return buf.getvalue()


def mp4(i, secs=2):
    a, b = PALETTE[i % len(PALETTE)]
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "v.mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x{a[0]:02x}{a[1]:02x}{a[2]:02x}:s=360x640:d={secs}",
                        "-vf", f"drawbox=x=40:y=200:w=280:h=240:color=0x{b[0]:02x}{b[1]:02x}{b[2]:02x}@0.9:t=fill",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", out], check=True)
        return open(out, "rb").read()


def seed(app):
    st, h = app.state.store, app.state.house
    st.upsert_user("acc_1", "ram@example.com", "Ram", "org_1", "HuhByte",
                   {"access_token": "at_1", "refresh_token": "rt_1", "expires_at": time.time() + 3600})
    st.new_session("sid-ls", "csrf-ls"); st.update_session("sid-ls", user_id="acc_1")
    now = time.time()
    n = 0

    def run(headline, toks, sent=None, created=None):
        pk = {"channel_id": "ch_li", "service": "linkedin", "text": headline, "status": "ready",
              "media": [{"url": h.media_url(t), "type": ty, "token": t} for t, ty in toks]}
        if sent:
            pk["sent"] = {"mode": sent, "buffer_post_id": "p1"}
        return st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
                    ("acc_1", "agent", json.dumps({"prompt": headline}), "ready", "[]",
                     json.dumps({"story": {"headline": headline}, "packages": [pk]}), created, created))
    for i in range(10):  # agent Reels, carousels and images, newest first
        created = now - i * 86400 * 1.5
        title = TITLES[i % len(TITLES)]
        if i % 3 == 0:
            tok = h.put_media(A, mp4(i, 2 + i % 3), "reel.mp4", kind="render", meta={"type": "video", "duration": 48 + i * 11, "width": 1080, "height": 1920})
            rid = run(title, [(tok, "video")], "draft" if i == 0 else None, created)
            h.library.register_generated(A, [{"token": tok, "url": h.media_url(tok), "type": "video"}], "reel", rid, created)
        elif i % 3 == 1:
            toks = [h.put_media(A, card(i + k, label=title if k == 0 else ""), f"s{k}.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350}) for k in range(4 + i % 2)]
            rid = run(title, [(t, "photo") for t in toks], "queue" if i == 1 else None, created)
            h.library.register_generated(A, [{"token": t, "url": h.media_url(t), "type": "photo"} for t in toks], "carousel", rid, created)
        else:
            tok = h.put_media(A, card(i, label=title), "c.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350})
            rid = run(title, [(tok, "photo")], None, created)
            h.library.register_generated(A, [{"token": tok, "url": h.media_url(tok), "type": "photo"}], "photo", rid, created)
        n += 1
    for i in range(16):  # uploads
        if i % 5 == 0:
            h.library.save_bytes(A, mp4(i + 2), f"Clip {i + 1}.mp4")
        else:
            h.library.save_bytes(A, card(i + 3, 1200, 750), f"Photo {i + 1} with a rather long file name.jpg")
        n += 1
    ids = [r["id"] for r in st.q("SELECT id FROM library_items WHERE user_id='acc_1' ORDER BY id DESC LIMIT 2")]
    for iid in ids:
        h.library.delete(A, iid)  # two in the Bin
    return n


def main(out, prefix="after"):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    conftest.CHANNELS = OWNER_CHANNELS
    port = free_port()
    td = tempfile.mkdtemp()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="l" * 64,
                      db_path=os.path.join(td, "ls.db"), secure_cookies=False, ai_base_url="")
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(FakeBuffer()))))
    print("seeded", seed(app))
    server = serve(app, port)
    base = f"http://127.0.0.1:{port}"
    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"])
        times = {}
        for mode in ("dark", "light"):
            for w, hgt in ((1280, 900), (390, 844)):
                ctx = b.new_context(viewport={"width": w, "height": hgt}, color_scheme=mode, device_scale_factor=1)
                ctx.add_init_script(f"localStorage.setItem('hq_tour_done','1');localStorage.setItem('hq_onb_seen','1');localStorage.setItem('hq_mode','{mode}')")
                ctx.add_cookies([{"name": "hq_sid", "value": "sid-ls", "url": base}])
                pg = ctx.new_page()
                weights = {}
                pg.on("response", lambda r: weights.__setitem__(r.url.split("?")[0].replace(base, ""), len(r.body()) if r.ok and r.request.resource_type in ("script", "stylesheet") else 0))
                t0 = time.time()
                pg.goto(base + "/?tab=library", wait_until="domcontentloaded")
                pg.get_by_test_id("library-item").first.wait_for(timeout=30000)
                times.setdefault(f"{w}", []).append(round((time.time() - t0) * 1000))
                pg.wait_for_timeout(900)
                pg.screenshot(path=os.path.join(out, f"{prefix}-{w}-{mode}.png"))
                pg.screenshot(path=os.path.join(out, f"{prefix}-{w}-{mode}-full.png"), full_page=True)
                if mode == "dark" and w == 1280:
                    lib = {k: v for k, v in weights.items() if "library" in k}
                    print("library assets bytes", lib, "all js+css KB", round(sum(weights.values()) / 1024, 1))
                ctx.close()
        print("ms to first card", times)
        b.close()
    server.should_exit = True


if __name__ == "__main__":
    main(sys.argv[1], *(sys.argv[2:3]))
