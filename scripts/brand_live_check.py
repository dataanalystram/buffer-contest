"""Live check of the brand kit builder on the deployed app (/demo Brand > Kit), run from a GitHub runner.
  wait: block until a new instance answers /healthz (media.checked_at changes after a deploy), up to WAIT_MIN minutes.
  run:  time the first preview cold (first visit after the deploy) and warm, then screenshots at 1440 light and 390 dark
        (Nothing sample, HuhByte switch, Start your own) with console-error and overflow checks. Writes OUT/report.json."""
import json, os, statistics, sys, time, urllib.request
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "https://huhqueue-studio.onrender.com")
OUT = os.environ.get("OUT", "out")
NO_TOUR = "localStorage.setItem('hq_tour_done','1'); localStorage.setItem('hq_onb_seen','1')"
os.makedirs(OUT, exist_ok=True)


def health():
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)}


def wait():
    s, h = health()
    first = (h.get("media") or {}).get("checked_at", "x")
    print("READY waiting for a new instance; checked_at now", first, flush=True)
    end = time.time() + float(os.environ.get("WAIT_MIN", "15")) * 60
    while time.time() < end:
        time.sleep(1)
        s, h = health()
        c = (h.get("media") or {}).get("checked_at", "x") if s == 200 else "x"
        if s == 200 and c != first:
            print("NEW INSTANCE at", time.strftime("%H:%M:%S", time.gmtime()), "checked_at", c, flush=True)
            json.dump({"new_instance_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "checked_at": c},
                      open(os.path.join(OUT, "deploy.json"), "w"))
            return
    print("no new instance seen; measuring anyway", flush=True)


IMG_OK = "(sel) => { const i = document.querySelector(sel); return !!(i && i.complete && i.naturalWidth > 0); }"


def first_preview(b, label, width=1440, scheme="light"):
    ctx = b.new_context(viewport={"width": width, "height": 900 if width > 500 else 844}, color_scheme=scheme, has_touch=width < 1024)
    ctx.add_init_script(NO_TOUR)
    page = ctx.new_page()
    resps, errors = [], []
    page.on("response", lambda r: "/api/brand-kits" in r.url and resps.append(r))
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    t0 = time.time()
    page.goto(BASE + "/demo", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_selector("#bkBuilder", state="visible", timeout=90000)
    tb = time.time()
    page.wait_for_function(IMG_OK, arg="[data-testid=bk-card-carousel_cover] img", timeout=90000)
    tk = time.time()
    page.wait_for_function(IMG_OK, arg="[data-testid=bk-themes] [data-card=opus] img", timeout=90000)
    tt = time.time()
    api = []
    for r in resps:
        it = {"url": r.url.replace(BASE, ""), "method": r.request.method, "status": r.status}
        if "/preview" in r.url and r.request.method == "POST":
            try:
                j = r.json(); it.update({"server_ms": j.get("ms"), "cached": j.get("cached"), "part": j.get("part")})
            except Exception:  # noqa: BLE001
                pass
        if r.url.endswith("/api/brand-kits") or "/api/brand-kits?" in r.url:
            try:
                j = r.json(); it["list_preview_cards"] = len((j.get("preview") or {}).get("cards") or [])
            except Exception:  # noqa: BLE001
                pass
        api.append(it)
    res = {"label": label, "page_to_builder_s": round(tb - t0, 2), "first_kit_card_after_builder_s": round(tk - tb, 2),
           "first_kit_card_from_navigation_s": round(tk - t0, 2), "first_theme_card_after_builder_s": round(tt - tb, 2),
           "api": api[:12], "console_errors": errors}
    print(json.dumps(res), flush=True)
    return res, ctx, page, errors


def overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def until(fn, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.2)
    return False


def shots(b, width, scheme):
    tag = f"{width}-{scheme}"
    res, ctx, page, errors = first_preview(b, "shots-" + tag, width, scheme)
    out = {"size": tag, "states": []}
    def snap(name):
        page.locator("#bkBuilder").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(500)
        p = os.path.join(OUT, f"brand-{tag}-{name}.png")
        page.screenshot(path=p)
        out["states"].append({"state": name, "file": os.path.basename(p), "overflow_px": overflow(page),
                              "name_field": page.input_value("[data-testid=bk-name]")})
    until(lambda: page.locator("[data-testid=bk-themes] [data-card] img").count() >= 20)
    snap("nothing")
    before = page.get_attribute("[data-testid=bk-themes] [data-card=opus] img", "src")
    page.click("[data-testid=bk-kit-huhbyte-ai]")
    ok = until(lambda: page.input_value("[data-testid=bk-name]") == "HuhByte AI") and \
        until(lambda: page.get_attribute("[data-testid=bk-themes] [data-card=opus] img", "src") != before) and \
        until(lambda: page.evaluate(IMG_OK, "[data-testid=bk-themes] [data-card=opus] img"))
    out["huhbyte_switch_ok"] = bool(ok)
    snap("huhbyte")
    page.click("[data-testid=bk-start-own]")
    out["start_own_ok"] = until(lambda: page.input_value("[data-testid=bk-name]") == "")
    snap("start-own")
    out["console_errors"] = list(errors)
    out["api_brand_kits_status"] = page.request.get(BASE + "/api/brand-kits").status
    ctx.close()
    print(json.dumps(out), flush=True)
    return out


def run():
    report = {"base": BASE, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    try:
        report["deploy"] = json.load(open(os.path.join(OUT, "deploy.json")))
    except OSError:
        report["deploy"] = None
    with sync_playwright() as p:
        b = p.chromium.launch()
        cold, ctx, page, _ = first_preview(b, "cold")
        report["cold"] = cold
        report["api_brand_kits_status"] = page.request.get(BASE + "/api/brand-kits").status
        ctx.close()
        warm = []
        for i in range(3):
            r, ctx, page, _ = first_preview(b, f"warm-{i + 1}")
            warm.append(r); ctx.close()
        report["warm"] = warm
        report["warm_median_first_kit_card_after_builder_s"] = statistics.median(w["first_kit_card_after_builder_s"] for w in warm)
        report["shots"] = [shots(b, 1440, "light"), shots(b, 390, "dark")]
        b.close()
    report["healthz"] = health()[0]
    json.dump(report, open(os.path.join(OUT, "report.json"), "w"), indent=2)
    print("REPORT", json.dumps({k: report[k] for k in ("cold", "warm_median_first_kit_card_after_builder_s", "healthz", "api_brand_kits_status")}))


if __name__ == "__main__":
    {"wait": wait, "run": run}[sys.argv[1]]()
