"""Screenshots of every HuhQueue screen for the UX v3 self-review: the sample workspace's nine screens and their
sub-tabs, the onboarding sheet and the landing, at the given widths and colour schemes.

    python scripts/ux_v3_shots.py http://127.0.0.1:8899 out_dir [1440,390] [light,dark] [screen,screen...]

Hermetic (requests to other hosts are aborted), lazy images are scrolled into view first, and page errors are printed.
Exits 1 if any page throws.
"""
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SCREENS = ["inbox", "agent", "agent:quick", "take", "planner", "news", "news:scan", "news:sources", "brand", "brand:templates",
           "brand:ledger", "apps", "activity", "settings", "onboarding", "landing"]
SCROLL = ("async () => { for (let y = 0; y < document.body.scrollHeight; y += 600) { scrollTo(0, y);"
          " await new Promise((r) => setTimeout(r, 100)); } scrollTo(0, 0); }")


def main(base, out, widths="1440,390", schemes="light,dark", screens=",".join(SCREENS)):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        exe = os.environ.get("CHROMIUM_PATH") or ("/usr/bin/chromium" if Path("/usr/bin/chromium").exists() else None)
        b = p.chromium.launch(executable_path=exe, args=["--disable-dev-shm-usage", "--disable-gpu", "--renderer-process-limit=2"])
        for w in [int(x) for x in widths.split(",")]:
            for scheme in schemes.split(","):
                ctx = b.new_context(viewport={"width": w, "height": 900}, color_scheme=scheme)
                ctx.add_init_script("localStorage.setItem('hq_tour_done','1')")
                ctx.route(lambda u: not u.startswith(base), lambda r: r.abort())
                pg = ctx.new_page()
                pg.on("pageerror", lambda e, w=w, s=scheme: errors.append(f"{w}/{s}: {e}"))
                for sc in screens.split(","):
                    name = sc.replace(":", "-")
                    if sc == "landing":
                        lp = ctx.new_page(); lp.goto(base + "/"); lp.wait_for_timeout(1200); lp.evaluate(SCROLL); lp.wait_for_timeout(1200)
                        lp.screenshot(path=str(out / f"landing-{w}-{scheme}.png"), full_page=True); lp.close(); continue
                    if not pg.url.startswith(base + "/"):
                        pg.goto(base + "/demo"); pg.wait_for_timeout(2500)
                    if sc == "onboarding":
                        pg.evaluate("onbOpen(0)"); pg.wait_for_timeout(800)
                        pg.screenshot(path=str(out / f"onboarding-1-{w}-{scheme}.png"))
                        for step in (2, 3, 4):
                            pg.click("[data-testid=onboarding-next]"); pg.wait_for_timeout(1500)
                            pg.screenshot(path=str(out / f"onboarding-{step}-{w}-{scheme}.png"))
                        pg.keyboard.press("Escape"); pg.wait_for_timeout(300); continue
                    t, _, sub = sc.partition(":")
                    pg.evaluate("([t, s]) => showTab(t, s)", [t, sub]); pg.wait_for_timeout(2200); pg.evaluate(SCROLL); pg.wait_for_timeout(500)
                    pg.screenshot(path=str(out / f"{name}-{w}-{scheme}.png"), full_page=True)
                ctx.close()
        b.close()
    print("\n".join(errors) or "no page errors")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
