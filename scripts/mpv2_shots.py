"""Make a post v2 + Story scan screenshots: every new state at 1440 and 390 wide, light and dark, plus a layout check.

    python scripts/mpv2_shots.py http://127.0.0.1:8899 shots/mpv2
Runs the agent once in the sample workspace (the same session is reused for every size and colour scheme), keeps a
snapshot of the run while it is working so the "running" thread can be shown in every combination, and records, for each
screenshot, any horizontal overflow and any visible control whose text is clipped (layout.json). Exits 1 on a JS error.
"""
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
errors, layout = [], {}
CHECK = """() => {
  const W = document.documentElement.clientWidth, bad = [];
  const over = document.documentElement.scrollWidth - W;
  for (const el of document.querySelectorAll('.mp2 button, .mp2 a, .mp2 .mp-pill, .mp2 .mp-badge, .sa-vis button, .sa-vis h3, .pager button')) {
    const r = el.getBoundingClientRect(); if (!r.width || !r.height || getComputedStyle(el).visibility === 'hidden') continue;
    if (r.right > W + 1 || r.left < -1) bad.push('off-screen: ' + (el.dataset.testid || el.className || el.tagName) + ' ' + (el.textContent || '').trim().slice(0, 30));
    if (el.scrollWidth > el.clientWidth + 2 && getComputedStyle(el).overflow !== 'hidden' && !el.matches('.mp-ds-t, .mp-src, h3')) bad.push('clipped: ' + (el.dataset.testid || el.className) + ' ' + (el.textContent || '').trim().slice(0, 30));
  }
  return { overflow_px: Math.max(0, over), problems: bad.slice(0, 20) };
}"""


def shot(pg, name):
    time.sleep(0.6)
    pg.screenshot(path=str(out / f"{name}.png"), full_page=False)
    layout[name] = pg.evaluate(CHECK)


def watch(pg):
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and not m.text.startswith("Failed to load resource") and errors.append(m.text))


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1440, "height": 1000})
    ctx.add_init_script("localStorage.setItem('hq_tour_done', '1')")
    pg = ctx.new_page(); watch(pg)
    pg.goto(base + "/demo"); pg.wait_for_selector("[data-testid=inbox-title]")
    pg.click("[data-testid=tab-agent]"); pg.wait_for_selector("[data-testid=mp-discover]")
    # one real run in the sample workspace; keep a mid-run snapshot for the "working" thread
    pg.fill("[data-testid=agent-query]", "Find trending AI news and make a carousel for all my channels")
    pg.select_option("[data-testid=agent-format]", "carousel")
    pg.click("[data-testid=agent-go]")
    pg.wait_for_selector("[data-testid=mp-user-msg] .mp-bubble", timeout=30000)
    running = None
    for _ in range(120):
        rid = pg.evaluate("window.H_STATE && window.H_STATE.run && window.H_STATE.run.id")
        if rid:
            j = pg.evaluate(f"fetch('/api/agent/runs/{rid}').then(r => r.json())")
            if j["status"] in ("running", "rendering") and len(j["steps"]) >= 2:
                running = j
                break
            if j["status"] not in ("running", "rendering"):
                break
        time.sleep(0.5)
    pg.wait_for_selector("[data-testid=package]", timeout=180000)
    rid = pg.evaluate("window.H_STATE.run.id")
    state = ctx.storage_state()
    ctx.close()

    for scheme in ("light", "dark"):
        for w, h in ((1440, 1000), (390, 844)):
            tag = f"{w}_{scheme}"
            c = b.new_context(viewport={"width": w, "height": h}, color_scheme=scheme, storage_state=state, device_scale_factor=1)
            c.add_init_script("localStorage.setItem('hq_tour_done', '1')")
            pg = c.new_page(); watch(pg)
            pg.goto(base + "/?tab=agent"); pg.wait_for_selector("[data-testid=mp-discover]"); time.sleep(1.5)
            shot(pg, f"01_compose_{tag}")
            pg.click("[data-testid=mp-chip-more]"); pg.click("[data-testid=mp-example]")
            shot(pg, f"02_chips_example_{tag}")
            pg.locator("[data-testid=mp-discover]").scroll_into_view_if_needed()
            pg.locator("[data-testid=mp-disc-tab]").nth(1).click()
            pg.click("[data-testid=mp-disc-tab-more]")
            shot(pg, f"03_discover_{tag}")
            pg.click("[data-testid=mp-disc-tab-more]")
            pg.locator("[data-testid=mp-recent]").scroll_into_view_if_needed()
            shot(pg, f"04_recent_{tag}")
            pg.locator("[data-testid=mp-filter]").nth(2).click()
            shot(pg, f"05_recent_filter_{tag}")
            # the thread, finished
            pg.locator(f"[data-testid=run-row][data-id='{rid}']").first.click()
            pg.wait_for_selector("[data-testid=package]"); pg.evaluate("window.scrollTo(0, 0)")
            shot(pg, f"06_thread_ready_{tag}")
            pg.locator("[data-testid=packages]").scroll_into_view_if_needed()
            shot(pg, f"07_thread_packages_{tag}")
            # the thread, while the agent works (the mid-run snapshot)
            if running:
                pg.route(f"**/api/agent/runs/{running['id']}", lambda r: r.fulfill(json=running))
                pg.evaluate("j => { window.showRun(j); }", running); pg.evaluate("window.scrollTo(0, 0)")
                shot(pg, f"08_thread_running_{tag}")
                pg.unroute(f"**/api/agent/runs/{running['id']}")
            pg.click("[data-testid=mp-new]")
            pg.click("[data-testid=agent-mode-link]")
            shot(pg, f"09_from_link_{tag}")
            # News: Story scan (visual cards + pages) and the feed
            pg.goto(base + "/?tab=news&sub=scan"); pg.wait_for_selector("[data-testid=sa-scan]")
            pg.click("[data-testid=sa-scan]")
            try:
                pg.wait_for_selector("[data-testid=sa-cluster]", timeout=90000)
            except Exception:  # noqa: BLE001
                errors.append(f"story scan returned no cards ({tag})")
            time.sleep(2.5)
            shot(pg, f"10_story_scan_{tag}")
            if pg.locator("[data-testid=sa-pager]:not([hidden])").count():
                pg.locator("[data-testid=sa-pager]").scroll_into_view_if_needed()
                shot(pg, f"11_story_scan_pager_{tag}")
            pg.goto(base + "/?tab=news"); time.sleep(4)
            shot(pg, f"12_news_feed_{tag}")
            c.close()
    b.close()
(out / "layout.json").write_text(json.dumps({"errors": errors, "layout": layout}, indent=1))
bad = {k: v for k, v in layout.items() if v["overflow_px"] or v["problems"]}
print(json.dumps({"errors": errors, "layout_problems": bad}, indent=1))
sys.exit(1 if errors else 0)
