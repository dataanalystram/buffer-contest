"""README screenshots of the Inbox, the phone layout and the guided tour, from the sample workspace at /demo (never a real
Buffer account). Each shot opens its own fresh sample workspace.

    python scripts/inbox_shots.py http://127.0.0.1:8899 docs/screenshots

Writes inbox-light.png, inbox-dark.png (desktop), phone-inbox-light.png (bottom bar), phone-planner-agenda-light.png and
-dark.png (the Planner week as an agenda on a phone), tour-light.png (step 1, desktop) and tour-phone-dark.png (step 4).
"""
import os
import sys
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

base, out = sys.argv[1].rstrip("/"), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
DESK, PHONE = {"width": 1280, "height": 860}, {"width": 390, "height": 844}


def _photo(route):
    try:
        r = httpx.get(route.request.url, follow_redirects=True, timeout=15)
        route.fulfill(status=r.status_code, body=r.content, content_type=r.headers.get("content-type", "image/jpeg"))
    except httpx.HTTPError:
        route.abort()


def shot(browser, name, viewport, scheme, tab=None, tour_step=None, init=""):
    ctx = browser.new_context(viewport=viewport, color_scheme=scheme, device_scale_factor=2, timezone_id="Europe/Dublin",
                              has_touch=viewport["width"] < 1024)
    if tour_step is None:
        ctx.add_init_script("localStorage.setItem('hq_tour_done', '1');" + init)
    # sample data only: nothing from outside except the sample posts' stock photos (picsum.photos), fetched here and handed
    # to the page so thumbnails aren't broken on machines where the browser itself has no internet
    ctx.route(lambda u: not (u.startswith(base) or u.startswith("https://picsum.photos/")), lambda r: r.abort())
    ctx.route(lambda u: u.startswith("https://picsum.photos/"), _photo)
    try:
        page = ctx.new_page()
        page.goto(base + "/demo")
        page.locator("#app:not([hidden])").wait_for()
        if tour_step is not None:
            page.locator("[data-testid=tour][data-step='1']").wait_for()
            for n in range(2, tour_step + 1):
                page.get_by_test_id("tour-next").click()
                page.locator(f"[data-testid=tour][data-step='{n}']").wait_for()
            page.wait_for_timeout(500)
        elif tab:
            page.evaluate("(t) => showTab(t)", tab)
            page.wait_for_timeout(900)
            if tab == "planner" and viewport["width"] < 600:  # the agenda, from the first day with posts on it
                page.locator("[data-testid=planner-agenda]").wait_for()
                page.evaluate("""() => { const d = [...document.querySelectorAll('[data-testid=planner-list-day]')]
                  .find((x) => !x.querySelector('.pl-agenda-empty')) || document.querySelector('[data-testid=planner-agenda]');
                  const head = d.previousElementSibling || d;
                  window.scrollTo(0, head.getBoundingClientRect().top + scrollY - 76); }""")
                page.wait_for_function("[...document.images].every((i) => i.complete)", timeout=15000)
                page.wait_for_timeout(300)
        page.screenshot(path=str(out / name))
        print("wrote", out / name)
    finally:
        ctx.close()


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    week = "localStorage.setItem('hq_planner', JSON.stringify({view: 'week'}));"
    shot(browser, "inbox-light.png", DESK, "light", "inbox")
    shot(browser, "inbox-dark.png", DESK, "dark", "inbox")
    shot(browser, "phone-inbox-light.png", PHONE, "light", "inbox")
    shot(browser, "phone-planner-agenda-light.png", PHONE, "light", "planner", init=week)
    shot(browser, "phone-planner-agenda-dark.png", PHONE, "dark", "planner", init=week)
    shot(browser, "tour-light.png", DESK, "light", tour_step=1)
    shot(browser, "tour-phone-dark.png", PHONE, "dark", tour_step=4)
    browser.close()
