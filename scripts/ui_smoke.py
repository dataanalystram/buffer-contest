"""Drive the real web app in Chromium against the sample workspace and save screenshots of every HuhQueue screen.

    python scripts/ui_smoke.py http://127.0.0.1:8899 shots/
Runs the agent for real (live news search, render, captions, brand check), revises it, sends a package, opens the calendar.
"""
import os
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    pg = b.new_page(viewport={"width": 1366, "height": 900}, device_scale_factor=1)
    pg.add_init_script("localStorage.setItem('hq_tour_done', '1')")  # the guided tour has its own test
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # a failed request is recorded by its URL (the console only says "Failed to load resource"); no 4xx/5xx is allowed
    pg.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource") and errors.append(m.text))
    pg.on("response", lambda r: r.status >= 400 and r.url.startswith(base) and errors.append(f"{r.status} {r.request.method} {r.url[len(base):]}"))
    pg.goto(base + "/")
    pg.screenshot(path=str(out / "00_landing.png"), full_page=True)
    pg.goto(base + "/demo")  # the sample opens on Brand > Kit, the builder filled with the Nothing sample brand
    pg.wait_for_selector("[data-testid=bk-card-carousel_cover] img", timeout=60000)
    pg.screenshot(path=str(out / "00b_brand_kit.png"))
    pg.click("[data-testid=tab-inbox]")
    pg.wait_for_selector("[data-testid=inbox-title]")
    pg.screenshot(path=str(out / "01_inbox.png"))
    # brand kit first (one Brand screen: Kit, Templates, Style Ledger)
    pg.click("[data-testid=tab-brand]")
    # Brand opens on Kit (the builder); the classic kit form and its template preview live under "More kit settings"
    pg.wait_for_selector("[data-testid=bk-card-carousel_cover] img", timeout=60000)
    if not pg.locator("[data-testid=bk-more]").evaluate("d => d.open"):
        pg.click("[data-testid=bk-more] > summary")
    pg.locator("[data-testid=bs-slide]").first.wait_for(state="visible", timeout=30000)
    pg.fill("[data-testid=bs-name]", "Huhbyte"); pg.fill("[data-testid=bs-handle]", "@huhbyte")
    pg.click("[data-testid=bs-save]"); time.sleep(2)
    pg.click("[data-testid=brand-tab-ledger]")
    pg.fill("[data-testid=rule-input]", "never say game-changer, max 2 hashtags"); pg.click("[data-testid=rule-add]"); time.sleep(2)
    pg.screenshot(path=str(out / "02_brand.png"), full_page=True)
    # agent run
    pg.click("[data-testid=tab-agent]")
    pg.fill("[data-testid=agent-query]", sys.argv[3] if len(sys.argv) > 3 else "OpenAI")
    pg.select_option("[data-testid=agent-format]", "carousel")
    pg.click("[data-testid=agent-go]")
    time.sleep(3)
    pg.screenshot(path=str(out / "03_agent_running.png"), full_page=True)
    pg.wait_for_selector("[data-testid=package]", timeout=90000)
    pg.screenshot(path=str(out / "04_agent_ready.png"), full_page=True)
    # Make a post runs are agent runs: changes are a reply in the run's thread (the old "Fix and remember" form is for
    # media runs). Wait for the agent's answer and the rebuilt packages.
    n = pg.locator("[data-testid=thread-msg]").count()
    pg.fill("[data-testid=reply-input]", "make the logo smaller")
    pg.click("[data-testid=reply-go]")
    end = time.time() + 90  # polled in Python: the app's CSP forbids the string evaluation wait_for_function uses
    while pg.locator("[data-testid=thread-msg]").count() < n + 2 and time.time() < end:
        time.sleep(0.5)
    assert pg.locator("[data-testid=thread-msg]").count() >= n + 2, "the agent didn't answer the reply"
    pg.wait_for_selector("[data-testid=package]", timeout=90000)
    time.sleep(1.5)
    pg.screenshot(path=str(out / "05_revised.png"), full_page=True)
    pg.locator("[data-testid=pkg-send]").first.click()
    pg.wait_for_selector("[data-testid=pkg-sent]", timeout=20000)
    pg.click("[data-testid=tab-planner]")
    pg.click("[data-testid=planner-view-month]")
    pg.wait_for_selector("[data-testid=planner-day]", timeout=20000)
    time.sleep(1)
    pg.screenshot(path=str(out / "06_planner.png"), full_page=True)
    # Never Bounced: a too-long X post is blocked by preflight until Fix it threads it
    pg.click("[data-testid=planner-new]")
    pg.select_option("[data-testid=planner-dialog-channel]", "demo_x")
    pg.fill("[data-testid=planner-dialog-text]", "Agents can draft, humans approve. " * 12)
    pg.wait_for_selector("[data-testid=planner-preflight] [data-testid=preflight-chip][data-status=error]", timeout=20000)
    pg.screenshot(path=str(out / "06b_preflight_blocked.png"))
    pg.click("[data-testid=planner-preflight] [data-testid=preflight-fix]")
    pg.wait_for_selector("[data-testid=planner-preflight] [data-testid=preflight-chip][data-status=ok]", timeout=20000)
    pg.screenshot(path=str(out / "06c_preflight_fixed.png"))
    # three days out: the package above already used the sample X channel's 3-in-24-hours Buffer limit, and the save is
    # (rightly) refused inside that window. The dialog's error is printed if the toast still doesn't come.
    pg.eval_on_selector("#plDlgWhen", """e => { const d = new Date(Date.now() + 3 * 864e5); d.setHours(10, 0, 0, 0);
        const z = (n) => String(n).padStart(2, "0");
        e.value = `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())}T10:00`; e.dispatchEvent(new Event("input", {bubbles: true})); }""")
    pg.click("[data-testid=planner-dialog-save]")
    try:
        pg.wait_for_selector("[data-testid=toast]:has-text('Scheduled in Buffer')", timeout=20000)
    except Exception:
        print("planner save error:", pg.inner_text("#plDlgErr"))
        raise
    pg.click("[data-testid=tab-settings]"); time.sleep(1.5)
    pg.screenshot(path=str(out / "07_ai.png"), full_page=True)
    m = b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    m.add_init_script("localStorage.setItem('hq_tour_done', '1')")
    m.goto(base + "/demo"); m.wait_for_selector("[data-testid=inbox-title]"); time.sleep(1)
    m.screenshot(path=str(out / "08_mobile.png"), full_page=True)
    b.close()
(out / "errors.txt").write_text("\n".join(errors) or "no browser errors")
print("browser errors:", errors or "none")
sys.exit(1 if errors else 0)
