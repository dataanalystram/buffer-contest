"""Loading states + adaptive layout (owner ask, 4 Nov 2026): real screenshots of a local, seeded run. Never a real Buffer.

    CHROMIUM_PATH=/usr/bin/chromium python scripts/loading_layout_shots.py <out-dir> [prefix] [only]   (from the repo root)

Seeds the made-up workspace from scripts/library_shots.py (acc_1, ram@example.com, a fake Buffer behind a mock transport),
adds one multi-channel run (Instagram, Facebook, LinkedIn, YouTube) and Buffer posts for the Planner (scheduled this week,
drafts with no date), then captures every page at the widths the ask names. Every shot is checked for sideways overflow
and squeezed under 300 KB. Also prints time-to-skeleton / time-to-content for a Library filter change and the Bin switch.
`only` limits the run to shot names containing that text (e.g. "planner", "1440")."""
import datetime as dt
import io
import json
import os
import re
import sys
import tempfile
import time

sys.path.insert(0, os.getcwd())
import httpx  # noqa: E402
from _pytest.monkeypatch import MonkeyPatch  # noqa: E402
from PIL import Image  # noqa: E402

from studio import house  # noqa: E402
from studio.app import create_app  # noqa: E402
from studio.config import load_settings  # noqa: E402
from tests.studio import conftest  # noqa: E402
from tests.studio.browsers import free_port, serve  # noqa: E402
from tests.studio.conftest import FakeBuffer  # noqa: E402
from tests.studio.test_agent_core import OWNER_CHANNELS, Router  # noqa: E402

sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))
from library_shots import card, mp4, seed  # noqa: E402
from readme_refresh_shots import STORY, stub_outside  # noqa: E402

MAX_BYTES = 300 * 1024
A = house.Actor("acc_1")
X_CHANNEL = {"id": "ch_x", "name": "huhbyte", "displayName": "huhbyte", "service": "twitter", "avatar": "", "isDisconnected": False, "isQueuePaused": False}


def seed_more(app, fake):
    """A multi-channel run (the newest thread) and Buffer posts for the Planner."""
    st, h = app.state.store, app.state.house
    reel = h.put_media(A, mp4(3, 3), "reel.mp4", kind="render", meta={"type": "video", "duration": 42, "width": 1080, "height": 1920})
    pic = h.put_media(A, card(7, label="Agent Desktop ships"), "c.jpg", kind="render", meta={"type": "photo", "width": 1080, "height": 1350})
    caps = {"instagram": "Agent Desktop ships today: one approval, then it books the whole trip. Here is what changed and why it matters. #ai #agents",
            "facebook": "Agent Desktop ships today. The agent plans trips end to end and asks you once before paying.",
            "linkedin": "Agent Desktop ships: what shipped, why it matters for teams, and the one approval step that keeps a human in charge.",
            "youtube": "Agent Desktop in 40 seconds: the one-approval travel agent, explained."}
    pks = []
    for ch, svc in (("ch_ig", "instagram"), ("ch_fb", "facebook"), ("ch_li", "linkedin"), ("ch_yt", "youtube")):
        video = svc in ("instagram", "youtube")
        tok = reel if video else pic
        pks.append({"channel_id": ch, "service": svc, "text": caps[svc], "status": "ready",
                    "media": [{"url": h.media_url(tok), "type": "video" if video else "photo", "token": tok}]})
    now = time.time()
    st.x("INSERT INTO agent_runs(user_id,kind,input,status,steps,result,created,updated) VALUES(?,?,?,?,?,?,?,?)",
         ("acc_1", "agent", json.dumps({"prompt": "Agent Desktop ships"}), "ready", "[]",
          json.dumps({"story": {"headline": "Agent Desktop ships"}, "packages": pks}), now + 5, now + 5))
    today = dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    posts = []
    for k, (d, hh, ch, text) in enumerate([(1, 9, "ch_li", "Why one-approval agents beat autopilot"), (2, 13, "ch_ig", "Reel: Agent Desktop in 40s"),
                                            (3, 17, "ch_fb", "Weekly AI recap is live"), (4, 10, "ch_yt", "Short: GPU prices explained"),
                                            (2, 9, "ch_x", "Thread: five launches this week"), (5, 12, "ch_ig", "Carousel: the agent stack")]):
        day = monday + dt.timedelta(days=d)
        posts.append({"id": f"w{k}", "text": text, "status": "scheduled", "channelId": ch,
                      "dueAt": dt.datetime(day.year, day.month, day.day, hh, 0, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")})
    for k, (ch, text) in enumerate([("ch_li", "Draft: a founder's take on agent pricing"), ("ch_fb", "Draft: behind the scenes of the recap"),
                                    ("ch_ig", "Draft: carousel on prompt caching")]):
        posts.append({"id": f"nd{k}", "text": text, "status": "draft", "dueAt": None, "channelId": ch})
    fake.posts = posts + fake.posts


def save(page, out, name, w, prefix):
    sw = page.evaluate("() => document.documentElement.scrollWidth")
    over = sw > w + 1
    page.wait_for_timeout(350)
    raw = page.screenshot()
    im = Image.open(io.BytesIO(raw))
    buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
    for colors in (256, 192, 128, 96, 64):
        if buf.tell() <= MAX_BYTES:
            break
        buf = io.BytesIO(); im.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
    open(os.path.join(out, f"{prefix}-{name}.png"), "wb").write(buf.getvalue())
    print(f"{prefix}-{name}.png {im.width}x{im.height} {round(buf.tell() / 1024)} KB{'  OVERFLOW ' + str(sw) if over else ''}", flush=True)
    return not over


def main(out, prefix="after", only=""):
    from playwright.sync_api import sync_playwright
    os.makedirs(out, exist_ok=True)
    mp = MonkeyPatch()
    stub_outside(mp)
    conftest.CHANNELS = OWNER_CHANNELS + [X_CHANNEL]
    port = free_port()
    td = tempfile.mkdtemp()
    s = load_settings(client_id="cid", client_secret="sec", public_url=f"http://127.0.0.1:{port}", session_secret="r" * 64,
                      db_path=os.path.join(td, "ll.db"), secure_cookies=False, ai_base_url="")
    s.extra.update(sync_agent=True, render_worker="none")
    fake = FakeBuffer()
    app = create_app(s, http=httpx.Client(transport=httpx.MockTransport(Router(fake))))
    print("seeded", seed(app), flush=True)
    seed_more(app, fake)
    server = serve(app, port)
    base = f"http://127.0.0.1:{port}"
    bad = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None, args=["--no-sandbox"])

            def page_at(w, h=900):
                ctx = b.new_context(viewport={"width": w, "height": h}, color_scheme="dark", device_scale_factor=1,
                                    timezone_id="Europe/Dublin", locale="en-IE")
                ctx.add_init_script("localStorage.setItem('hq_tour_done','1');localStorage.setItem('hq_onb_seen','1');localStorage.setItem('hq_mode','dark')")
                ctx.add_cookies([{"name": "hq_sid", "value": "sid-ls", "url": base}])
                ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
                ctx.route(re.compile(r".*/api/radar.*"), lambda r: r.fulfill(json={"stories": [STORY]}))
                pg = ctx.new_page()
                pg.set_default_timeout(30000)
                return ctx, pg

            def want(name):
                return not only or only in name

            for w in (390, 768, 1280, 1440, 1920, 2560):
                h = 844 if w == 390 else (1024 if w == 768 else (1440 if w == 2560 else 900))
                names = [f"thread-{w}"] + [f"{t}-{w}" for t in ("inbox", "library", "brand", "apps", "activity", "settings")] \
                    + [f"planner-{v}-{w}" for v in ("week", "month", "list", "board", "grid")]
                if not any(want(n) for n in names):
                    continue
                ctx, pg = page_at(w, h)
                if want(f"thread-{w}"):
                    pg.goto(base + "/?tab=agent", wait_until="domcontentloaded")
                    pg.wait_for_timeout(1500)
                    try:
                        pg.evaluate("async () => { const r = await api('/api/agent/runs?limit=1'); const x = await api('/api/agent/runs/' + r[0].id); showRun(x); }")
                        pg.get_by_test_id("package").first.wait_for(timeout=20000)
                    except Exception as e:  # noqa: BLE001
                        print("thread open failed", w, str(e)[:120])
                    pg.wait_for_timeout(900)
                    save(pg, out, f"thread-{w}", w, prefix) or bad.append(f"thread-{w}")
                    pg.evaluate("() => { const h = document.getElementById('pkgHead'); if (h) h.scrollIntoView({ block: 'start' }); }")
                    pg.wait_for_timeout(500)
                    save(pg, out, f"review-{w}", w, prefix) or bad.append(f"review-{w}")
                    if w >= 1440:
                        pg.get_by_test_id("chat-sidebar-open").click()
                        pg.wait_for_timeout(600)
                        pg.evaluate("() => { const h = document.getElementById('pkgHead'); if (h) h.scrollIntoView({ block: 'start' }); }")
                        pg.wait_for_timeout(400)
                        save(pg, out, f"review-threads-hidden-{w}", w, prefix) or bad.append(f"review-threads-hidden-{w}")
                        pg.get_by_test_id("chat-sidebar-open").click()
                for tab in ("inbox", "library", "brand", "apps", "activity", "settings"):
                    if not want(f"{tab}-{w}"):
                        continue
                    pg.goto(base + f"/?tab={tab}", wait_until="domcontentloaded")
                    pg.wait_for_timeout(2200)
                    save(pg, out, f"{tab}-{w}", w, prefix) or bad.append(f"{tab}-{w}")
                for view in ("week", "month", "list", "board", "grid"):
                    if not want(f"planner-{view}-{w}"):
                        continue
                    pg.goto(base + "/?tab=planner", wait_until="domcontentloaded")
                    pg.wait_for_selector("[data-testid=planner-main]")
                    pg.evaluate(f"() => {{ const s = JSON.parse(localStorage.getItem('hq_planner') || '{{}}'); s.view = '{view}'; s.nodate = '1'; localStorage.setItem('hq_planner', JSON.stringify(s)); }}")
                    pg.goto(base + "/?tab=planner", wait_until="domcontentloaded")
                    pg.wait_for_timeout(2000)
                    pg.evaluate("() => { const t = document.getElementById('toast'); if (t) t.hidden = true; }")
                    save(pg, out, f"planner-{view}-{w}", w, prefix) or bad.append(f"planner-{view}-{w}")
                ctx.close()
            if want("timing"):
                ctx, pg = page_at(1440)
                pg.goto(base + "/?tab=library", wait_until="domcontentloaded")
                pg.get_by_test_id("library-item").first.wait_for()
                pg.wait_for_timeout(600)
                t = pg.evaluate("""async () => {
                  const g = document.getElementById('libGrid'), out = {};
                  const watch = (testSk, testDone) => new Promise((res) => { const t0 = performance.now(); let sk = null;
                    const tick = () => { if (sk === null && testSk()) sk = performance.now() - t0;
                      if (sk !== null && testDone()) return res({skeleton_ms: +sk.toFixed(1), content_ms: +(performance.now() - t0).toFixed(1)});
                      if (performance.now() - t0 > 8000) return res({skeleton_ms: sk, content_ms: null}); setTimeout(tick, 0); };
                    tick(); });
                  let p = watch(() => g.querySelector('.lp-skel'), () => !g.querySelector('.lp-skel') && g.getAttribute('aria-busy') === 'false');
                  document.querySelector('#libTabs [data-type=video]').click(); out.filter = await p;
                  p = watch(() => g.querySelector('.lp-skel'), () => g.querySelector('[data-testid=bin-item],[data-testid=bin-empty]'));
                  document.querySelector('[data-testid=library-view-bin]').click(); out.bin = await p;
                  return out; }""")
                print("timing", json.dumps(t), flush=True)
                ctx.close()
            b.close()
    finally:
        server.should_exit = True
        mp.undo()
    print("overflow:", bad or "none")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots/pr-loading", *(sys.argv[2:4]))
